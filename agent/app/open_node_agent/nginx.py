"""An isolated Nginx master, with owned configuration and certificate transactions."""

import asyncio
import contextlib
import copy
import fnmatch
import hashlib
import json
import logging
import os
import re
import signal
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

import crossplane
import httpx
import psutil
from pydantic import BaseModel, ConfigDict, Field

from open_node_agent.certificates import hostname, validate_pair
from open_node_agent.host_files import FileTransaction, guarded_path, read_private
from open_node_agent.runtime import (
    MAX_CONFIG_BYTES,
    RuntimeFailure,
    atomic_write,
    decode_config,
    run_command,
)


def directive(name: str, *args: str, block: list | None = None) -> dict:
    value = {"directive": name, "args": list(args)}
    if block is not None:
        value["block"] = block
    return value


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("block", []))


def render(nodes: list) -> bytes:
    return (crossplane.build(nodes) + "\n").encode()


class TunnelDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    domain: str = Field(min_length=1, max_length=255)
    cert_name: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_.-]+$")
    nginx_http: list[dict] = Field(min_length=1, max_length=8)
    domain_config: str = Field(min_length=1, max_length=MAX_CONFIG_BYTES)
    xray_config: dict
    expected_xray_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    clear_stream_port: bool = True
    listen_port: int = Field(default=443, ge=1, le=65535)
    restart_xray: bool = True


class NginxRuntime:
    def __init__(self, xray, journal):
        self.xray, self.journal, self.config = xray, journal, xray.config
        self.root = self.config.xray_config.parent / "nginx"
        self.certs = self.config.xray_config.parent / "certificates"
        self.state = self.config.state_dir / "nginx"
        self.main = self.root / "nginx.conf"
        self.effective = self.state / "effective.conf"
        self.html = self.state / "html"
        self.process = None
        self.process_started = None
        self.log_task = None
        self.log_handler = None
        self.log = logging.Logger("open-node-nginx")
        self.transaction = FileTransaction(
            self.config.state_dir / "host-transaction.json",
            self.transaction_path,
            self.restore_intents,
        )
        self.transaction.recover()

    def restore_intents(self, intents):
        for service, desired in intents.items():
            self.journal.set_desired_running(desired, service)

    def config_path(self, value: str | Path) -> Path:
        path = guarded_path(self.root, value)
        if path.suffix != ".conf" and path.name != "mime.types":
            raise RuntimeFailure("Only Nginx .conf and mime.types files may be accessed")
        return path

    def cert_path(self, value: str | Path) -> Path:
        if "$" in str(value):
            raise RuntimeFailure("Dynamic certificate paths are not allowed")
        path = guarded_path(self.certs, value)
        if path.suffix not in {".pem", ".key", ".crt", ".cer"}:
            raise RuntimeFailure("Certificate paths must end in .pem, .key, .crt or .cer")
        return path

    def transaction_path(self, path: Path) -> Path:
        if path == self.config.xray_config:
            return guarded_path(path.parent, path)
        if path.is_relative_to(self.root):
            return self.config_path(path)
        if path.is_relative_to(self.certs):
            return self.cert_path(path)
        if path in {self.effective, self.html / "index.html"}:
            return guarded_path(self.state, path)
        raise RuntimeFailure("Path is not owned by the host configuration manager")

    def require_binary(self):
        if self.config.nginx_binary is None:
            raise RuntimeFailure(
                "Nginx is not configured; set nginx_binary during host installation"
            )

    def prepare(self):
        self.require_binary()
        for directory in (self.root, self.state, self.html):
            guarded_path(directory.parent, directory / ".check")
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.log_handler is None:
            path = guarded_path(self.config.state_dir, "nginx.log")
            self.log_handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=2)
            path.chmod(0o600)
            self.log.addHandler(self.log_handler)

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        files = []
        for directory, dirs, names in os.walk(self.root, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise RuntimeFailure("Symlink config directories are not allowed")
            for name in names:
                if name.endswith(".conf") or name == "mime.types":
                    files.append(self.config_path(Path(directory) / name))
                    if len(files) > 128:
                        raise RuntimeFailure("Too many Nginx configuration files")
        return sorted(files)

    def parse(self, path: Path) -> list:
        self.config_path(path)
        read_private(path)
        parsed = crossplane.parse(str(path), single=True, check_ctx=False, check_args=False)
        if parsed["status"] != "ok":
            raise RuntimeFailure("Invalid Nginx syntax in " + str(path.relative_to(self.root)))
        return parsed["config"][0]["parsed"]

    def site_path(self, value: str) -> Path:
        if "$" in value:
            raise RuntimeFailure("Dynamic static content paths are not allowed")
        path = Path(value)
        for root in (self.html, *self.config.nginx_site_roots):
            if path == root or path.is_relative_to(root):
                # Root directories may not exist yet; validate every component.
                guarded_path(root.parent, path / ".check")
                return path
        raise RuntimeFailure(
            "Static content must stay inside a configured nginx_site_roots directory"
        )

    def compile(self) -> bytes:
        available = self.files()
        parsed = {path: self.parse(path) for path in available}
        budget = 0

        def expand(path, stack=()):
            nonlocal budget
            if path in stack or len(stack) >= 32:
                raise RuntimeFailure("Recursive Nginx includes are not allowed")
            budget += len(read_private(path))
            if budget > MAX_CONFIG_BYTES:
                raise RuntimeFailure("Expanded Nginx configuration exceeds 2 MiB")

            def nodes(values):
                result = []
                for original in values:
                    item = copy.deepcopy(original)
                    name, args = item["directive"], item["args"]
                    if name == "include":
                        if len(args) != 1:
                            raise RuntimeFailure("An include requires exactly one path")
                        pattern = self.config_path(args[0])
                        matches = [
                            p for p in available if fnmatch.fnmatchcase(str(p), str(pattern))
                        ]
                        if not matches and not any(c in str(pattern) for c in "*?["):
                            raise RuntimeFailure("Included configuration does not exist")
                        for match in matches:
                            result.extend(expand(match, (*stack, path)))
                        continue
                    if name in {"daemon", "master_process", "pid", "user", "load_module"}:
                        raise RuntimeFailure(f"{name} is controlled by the Agent")
                    if name == "error_log":
                        item["args"] = ["stderr", "notice"]
                    elif name == "access_log" and args and args[0] != "off":
                        item["args"] = ["/dev/stdout", *args[1:]]
                    elif name in {"ssl_certificate", "ssl_certificate_key"} and args:
                        item["args"][0] = str(self.cert_path(args[0]))
                    elif name in {"root", "alias"} and args:
                        item["args"][0] = str(self.site_path(args[0]))
                    elif name.endswith("_temp_path") and args:
                        item["args"][0] = str(self.state / name)
                    if "block" in item:
                        item["block"] = nodes(item["block"])
                    result.append(item)
                return result

            return nodes(parsed[path])

        if self.main not in parsed:
            raise RuntimeFailure("Nginx has not been installed into its owned directory")
        result = expand(self.main)
        if not any(n["directive"] == "error_log" for n in result):
            result.insert(0, directive("error_log", "stderr", "notice"))
        if not any(n["directive"] == "worker_processes" for n in result):
            result.insert(0, directive("worker_processes", "1"))
        result.insert(0, directive("pid", str(self.state / "nginx.pid")))
        for module in reversed(self.config.nginx_modules):
            result.insert(0, directive("load_module", str(module)))
        for item in result:
            if item["directive"] == "http":
                for name in (
                    "client_body_temp_path",
                    "proxy_temp_path",
                    "fastcgi_temp_path",
                    "uwsgi_temp_path",
                    "scgi_temp_path",
                ):
                    if not any(n["directive"] == name for n in item["block"]):
                        item["block"].append(directive(name, str(self.state / name)))
        content = render(result)
        if len(content) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Expanded Nginx configuration exceeds 2 MiB")
        return content

    def args(self):
        return [
            str(self.config.nginx_binary),
            "-p",
            str(self.state) + "/",
            "-c",
            str(self.effective),
            "-e",
            "stderr",
            "-g",
            "daemon off; master_process on;",
        ]

    async def validate(self):
        code, _ = await run_command(*self.args(), "-t")
        if code:
            # Config test output can include directive values or private file paths.
            raise RuntimeFailure("Nginx configuration test failed; changes were not activated")

    async def running(self):
        return self.process is not None and self.process.returncode is None

    async def version(self):
        if self.config.nginx_binary is None:
            return None
        try:
            code, output = await run_command(
                str(self.config.nginx_binary), "-v", timeout=5
            )
        except (OSError, RuntimeFailure, TimeoutError):
            return None
        if code:
            return None
        match = re.search(
            r"nginx version:\s*nginx/[0-9][0-9A-Za-z.+_-]{0,79}",
            output,
            re.IGNORECASE,
        )
        return match.group(0) if match is not None else None

    def workers(self):
        if not self.process or self.process.returncode is not None:
            return set()
        try:
            return {
                (p.pid, p.create_time())
                for p in psutil.Process(self.process.pid).children()
                if p.is_running()
                and p.status() != psutil.STATUS_ZOMBIE
                and "nginx: worker process" in " ".join(p.cmdline())
            }
        except psutil.Error:
            return set()

    async def wait_workers(self, previous=frozenset()):
        async with asyncio.timeout(9):
            while await self.running():
                fresh = self.workers() - previous
                if fresh:
                    await asyncio.sleep(0.2)
                    if fresh & self.workers():
                        return
                await asyncio.sleep(0.1)
        raise RuntimeFailure("Nginx exited before starting a worker")

    async def capture_logs(self, process):
        while block := await process.stdout.read(4096):
            self.log.info("%s", block.decode(errors="replace").rstrip())

    async def start(self):
        if await self.running():
            return
        await self.stop()
        if os.geteuid() == 0:
            raise RuntimeFailure("Run Nginx through a dedicated non-root Agent service account")
        self.prepare()
        atomic_write(self.effective, self.compile())
        await self.validate()
        self.process = await asyncio.create_subprocess_exec(
            *self.args(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self.log_task = asyncio.create_task(self.capture_logs(self.process))
        try:
            self.process_started = psutil.Process(self.process.pid).create_time()
        except psutil.NoSuchProcess:
            self.process_started = None
        try:
            await self.wait_workers()
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.send_signal(signal.SIGQUIT)
            try:
                await asyncio.wait_for(asyncio.shield(self.process.wait()), 5)
            except TimeoutError:
                self.kill_owned_group()
                await self.process.wait()
        # A dead master can leave workers holding both listeners and the log pipe.
        self.kill_owned_group()
        if self.log_task:
            await self.log_task
            self.log_task = None
        self.process = None
        self.process_started = None

    def kill_owned_group(self):
        if self.process is None:
            return
        try:
            if psutil.Process(self.process.pid).create_time() != self.process_started:
                return
        except psutil.NoSuchProcess:
            pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)

    async def reload(self):
        if not await self.running():
            return
        await self.validate()
        previous = self.workers()
        self.process.send_signal(signal.SIGHUP)
        try:
            await self.wait_workers(previous)
        except TimeoutError:
            raise RuntimeFailure(
                "Nginx did not activate new workers; configuration rolled back"
            ) from None

    async def apply(self, changes, *, activate=False, reload_xray=False, start_services=False):
        nginx_running, xray_running = await self.running(), await self.xray.running()
        changes = dict(changes)
        coupled = self.config.xray_config in changes
        intents = (
            {
                "xray": self.journal.desired_running(self.config.auto_start),
                "nginx": self.journal.desired_running(False, "nginx"),
            }
            if coupled
            else None
        )
        if self.main.exists() or self.main in changes:
            self.prepare()
            changes[self.effective] = (
                read_private(self.effective) if self.effective.exists() else None
            )
        self.transaction.begin(changes, intents=intents)
        touched_nginx = touched_xray = False
        try:
            if self.main.exists():
                atomic_write(self.effective, self.compile())
                await self.validate()
            if reload_xray or coupled:
                valid, _ = await self.xray.validate(self.xray.read())
                if not valid:
                    raise RuntimeFailure("Xray rejected the candidate host configuration")
            if activate and (nginx_running or start_services):
                touched_nginx = True
                await (self.reload() if nginx_running else self.start())
            if reload_xray and (xray_running or start_services):
                touched_xray = True
                await self.xray.restart()
            if coupled:
                if touched_nginx:
                    self.journal.set_desired_running(True, "nginx")
                if touched_xray:
                    self.journal.set_desired_running(True)
            self.transaction.commit()
        except BaseException:
            self.transaction.recover()

            async def restore_services():
                if coupled:
                    # Release new listeners before restoring the previous port ownership.
                    if touched_xray:
                        await self.xray.stop()
                    if touched_nginx:
                        await self.stop()
                    if touched_nginx and nginx_running:
                        await self.start()
                    if touched_xray and xray_running:
                        await self.xray.start()
                    return
                if touched_nginx:
                    await self.reload()
                if touched_xray:
                    await self.xray.restart()

            # Finish rollback even when the caller's command deadline has expired.
            restore = asyncio.create_task(restore_services())
            try:
                await asyncio.shield(restore)
            except asyncio.CancelledError:
                await restore
            raise
        return {
            "success": True,
            "restart_required": (nginx_running and not activate) or (coupled and not reload_xray),
        }

    async def deploy_tunnel(self, body):
        self.require_binary()
        if self.config.runtime_mode != "managed":
            raise RuntimeFailure("Atomic tunnel deployment requires the managed Xray runtime")
        body = TunnelDeployment.model_validate(body).model_dump()
        domain = hostname(body.get("domain"))
        current = self.xray.read()
        expected = body.get("expected_xray_sha256")
        actual = hashlib.sha256(
            json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if not isinstance(expected, str) or expected != actual:
            raise RuntimeFailure(
                "Xray configuration changed; refresh its snapshot before deployment"
            )
        cert_name = body.get("cert_name", domain)
        cert, key = self.cert_path(cert_name + ".pem"), self.cert_path(cert_name + ".key")
        validate_pair(domain, read_private(cert).decode(), read_private(key).decode())
        candidate = decode_config(body.get("xray_config"))
        if (
            self.config.stats_address
            and candidate.get("api", {}).get("listen") != self.config.stats_address
        ):
            raise RuntimeFailure("Tunnel API listener must match the operator's stats_address")
        main = (
            self.parse(self.main)
            if self.main.exists()
            else [
                directive("events", block=[directive("worker_connections", "1024")]),
                directive("http", block=[directive("access_log", "/dev/stdout")]),
            ]
        )
        http = [item for item in main if item["directive"] == "http" and "block" in item]
        if len(http) != 1:
            raise RuntimeFailure("Tunnel deployment requires exactly one main http block")
        additions = body.get("nginx_http")
        if not isinstance(additions, list) or len(additions) > 8:
            raise RuntimeFailure("Invalid tunnel HTTP configuration")
        for item in additions:
            if not isinstance(item, dict) or item.get("directive") not in {"map", "include"}:
                raise RuntimeFailure("Tunnel HTTP additions must be maps or includes")
            if item["directive"] == "include":
                existing = any(
                    n["directive"] == "include"
                    and n["args"]
                    in [
                        ["servers/*.conf"],
                        [str(self.root / "servers/*.conf")],
                    ]
                    for n in http[0]["block"]
                )
                if existing:
                    continue
            else:
                names = [
                    n
                    for n in http[0]["block"]
                    if n["directive"] == "map" and n["args"][-1:] == item.get("args", [])[-1:]
                ]
                if names:

                    def shape(node):
                        return {
                            k: [shape(n) for n in v] if k == "block" else v
                            for k, v in node.items()
                            if k in {"directive", "args", "block"}
                        }

                    if len(names) != 1 or shape(names[0]) != shape(item):
                        raise RuntimeFailure(
                            "An existing Nginx map conflicts with tunnel deployment"
                        )
                    continue
            http[0]["block"].append(item)
        changes = {
            self.main: render(main),
            self.config_path("servers/" + domain + ".conf"): body["domain_config"].encode(),
            self.config.xray_config: json.dumps(candidate, indent=2).encode() + b"\n",
        }
        index = self.html / "index.html"
        if not index.exists():
            changes[index] = b"Open Node\n"
        if body.get("clear_stream_port", True):
            stream_changes, _ = self.stream_changes(body.get("listen_port", 443))
            changes.update(stream_changes)
        result = await self.apply(
            changes, activate=True, reload_xray=body.get("restart_xray", True), start_services=True
        )
        return {
            **result,
            "domain": domain,
            "nginx": await self.status(),
            "xray_running": await self.xray.running(),
        }

    def stream_changes(self, port):
        if type(port) is not int or not 1 <= port <= 65535:
            raise RuntimeFailure("Invalid stream port")
        changes, removed = {}, 0
        for file in self.files():
            if file.parent != self.root / "stream_servers":
                continue
            kept, file_removed = [], 0
            for item in self.parse(file):
                matches = item["directive"] == "server" and any(
                    n["directive"] == "listen"
                    and n["args"]
                    and n["args"][0].rsplit(":", 1)[-1] == str(port)
                    for n in item.get("block", [])
                )
                if matches:
                    removed += 1
                    file_removed += 1
                else:
                    kept.append(item)
            if file_removed:
                changes[file] = render(kept)
        return changes, removed

    def default_main(self):
        return render(
            [
                directive("events", block=[directive("worker_connections", "1024")]),
                directive(
                    "http",
                    block=[
                        directive("access_log", "/dev/stdout"),
                        directive("include", "servers/*.conf"),
                    ],
                ),
            ]
        )

    def default_site(self, domain, *, tls=False):
        address = self.config.nginx_listen_address
        if ":" in address:
            address = "[" + address + "]"
        port = self.config.nginx_https_port if tls else self.config.nginx_http_port
        block = [
            directive(
                "listen",
                f"{address}:{port}",
                *(["ssl"] if tls else []),
            ),
            directive("server_name", domain),
        ]
        if tls:
            block.extend(
                [
                    directive("ssl_certificate", str(self.certs / (domain + ".pem"))),
                    directive("ssl_certificate_key", str(self.certs / (domain + ".key"))),
                    directive("ssl_protocols", "TLSv1.2", "TLSv1.3"),
                ]
            )
        block.append(
            directive(
                "location",
                "/",
                block=[directive("root", str(self.html)), directive("index", "index.html")],
            )
        )
        return render([directive("server", block=block)])

    async def status(self):
        return {
            "running": await self.running(),
            "installed": self.main.exists(),
            "available": self.config.nginx_binary is not None,
            "version": await self.version(),
            "tunnel_deploy": int(self.config.runtime_mode == "managed"),
            "mode": "managed",
            "config_path": str(self.main),
            "certificate_dir": str(self.certs),
            "html_path": str(self.html),
        }

    def websites(self):
        result = []
        for path in self.files():
            if path.parent != self.root / "servers":
                continue
            items = list(walk(self.parse(path)))
            names = [arg for n in items if n["directive"] == "server_name" for arg in n["args"]]
            proxy = [n["args"][0] for n in items if n["directive"] == "proxy_pass" and n["args"]]
            roots = [n["args"][0] for n in items if n["directive"] == "root" and n["args"]]
            result.append(
                {
                    "domain": path.stem,
                    "server_names": names,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "managed": True,
                    "protected": False,
                    "type": "proxy" if proxy else "static",
                    "legacy": False,
                    "value": next(iter(proxy or roots), ""),
                    "ssl": any(n["directive"] == "ssl_certificate" for n in items),
                    "mod_time": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                }
            )
        return result

    async def handle(self, method, path, body, query):
        if path == "/api/child/cert/deploy" and method == "POST":
            metadata = validate_pair(body.get("domain"), body.get("cert_pem"), body.get("key_pem"))
            cert, key = self.cert_path(body["cert_path"]), self.cert_path(body["key_path"])
            target = body.get("reload", "none")
            if cert == key or target not in {"nginx", "xray", "both", "none"}:
                raise RuntimeFailure("Invalid certificate paths or reload target")
            if target in {"nginx", "both"}:
                self.require_binary()
            result = await self.apply(
                {cert: body["cert_pem"].encode(), key: body["key_pem"].encode()},
                activate=target in {"nginx", "both"},
                reload_xray=target in {"xray", "both"},
            )
            return {**result, **metadata, "cert_path": str(cert), "key_path": str(key)}
        if path == "/api/child/validate-site" and method == "POST":
            if body.get("site_type") == "static":
                directory = self.site_path(body["site_value"])
                index = guarded_path(directory, "index.html")
                return {
                    "success": index.is_file(),
                    "message": "index.html exists" if index.is_file() else "index.html not found",
                }
            if body.get("site_type") != "proxy":
                raise RuntimeFailure("site_type must be static or proxy")
            value = body["site_value"]
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
            ):
                raise RuntimeFailure("Proxy target must be an HTTP(S) URL without credentials")
            try:
                async with httpx.AsyncClient(
                    timeout=5, trust_env=False, follow_redirects=False
                ) as client:
                    async with client.stream("GET", value) as response:
                        return {"success": True, "message": f"HTTP {response.status_code}"}
            except httpx.HTTPError:
                return {"success": False, "message": "Proxy target connection failed"}
        prefix = "/api/child/nginx/"
        if not path.startswith(prefix):
            raise NotImplementedError(f"Operation not implemented: {method} {path}")
        operation = path[len(prefix) :]
        if operation in {"install", "install-stream"} and method in {"POST", "GET"}:
            self.prepare()
            domain = hostname(body.get("domain") or query.get("domain", ["localhost"])[0])
            changes = {}
            if not self.main.exists():
                changes[self.main] = self.default_main()
                changes[self.root / "servers" / (domain + ".conf")] = self.default_site(domain)
            if not (self.html / "index.html").exists():
                changes[self.html / "index.html"] = b"Open Node\n"
            await self.apply(changes)
            await self.start()
            self.journal.set_desired_running(True, "nginx")
            return {"success": True, **await self.status()}
        if operation in {"remove", "remove-stream"} and method in {"POST", "GET"}:
            await self.stop()
            self.journal.set_desired_running(False, "nginx")
            return {"success": True, "data_preserved": True, "running": False}
        if operation == "config" and method == "GET":
            return {
                "success": True,
                "path": str(self.main),
                "config": read_private(self.config_path(self.main)).decode(),
            }
        if operation == "config" and method == "POST":
            path = self.config_path(body.get("path") or self.main)
            if path != self.main:
                raise RuntimeFailure("Use config-files for non-primary configuration")
            return await self.apply({path: body["config"].encode()})
        if operation == "config-files" and method == "GET":
            if query.get("file"):
                path = self.config_path(query["file"][0])
                return {"success": True, "path": str(path), "content": read_private(path).decode()}
            files = {}
            for file in self.files():
                group = str(file.parent.relative_to(self.root))
                files.setdefault("main" if group == "." else group, []).append(
                    {
                        "name": file.name,
                        "path": str(file),
                        "size": file.stat().st_size,
                        "mod_time": datetime.fromtimestamp(file.stat().st_mtime, UTC).isoformat(),
                    }
                )
            return {"success": True, "files": files}
        if operation == "config-files" and method == "POST":
            return await self.apply({self.config_path(body["path"]): body["content"].encode()})
        if operation == "setup-ssl" and method == "POST":
            domain = hostname(body.get("domain"))
            changes = {
                self.config_path("servers/" + domain + ".conf"): body["domain_config"].encode()
                if body.get("domain_config")
                else self.default_site(domain, tls=True)
            }
            if body.get("nginx_config"):
                changes[self.main] = body["nginx_config"].encode()
            elif not self.main.exists():
                changes[self.main] = self.default_main()
            return await self.apply(changes, activate=True)
        if operation in {"websites", "servers-list"} and method == "GET":
            return {
                "success": True,
                "nginx": await self.status(),
                "websites" if operation == "websites" else "domains": self.websites(),
            }
        if operation == "websites" and method == "DELETE":
            domain = hostname(body.get("domain"))
            file = self.config_path("servers/" + domain + ".conf")
            if not file.exists():
                raise RuntimeFailure("Website not found")
            return await self.apply({file: None}, activate=True)
        if operation == "clear-stream-port" and method == "POST":
            changes, removed = self.stream_changes(body.get("port"))
            await self.apply(changes, activate=True)
            return {"success": True, "removed": removed}
        raise NotImplementedError(f"Operation not implemented: {method} {path}")

    async def close(self):
        await self.stop()
        if self.log_handler:
            self.log_handler.close()
