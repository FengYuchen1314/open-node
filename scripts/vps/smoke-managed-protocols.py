"""Exercise all five managed Mihomo profiles with authenticated real traffic.

This smoke is deliberately host-local.  It creates one private directory under
``/tmp``, chooses currently unused high ports, and starts only owned processes.
It never changes systemd, firewall, nginx, or a system Mihomo installation.

The success path is:

    curl -> private Mihomo SOCKS port -> managed listener -> local HTTP health

For every profile the same route is attempted once more with an invalid
credential and must fail.  VLESS Reality and AnyTLS ShadowTLS use real public
TLS handshake targets and never disable certificate verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class SmokeError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def private_json(path: Path, value: object, *, owner: tuple[int, int]) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=False).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            path.unlink()
        raise
    os.chown(path, *owner)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    require(port >= 1024, "Kernel returned a privileged fixture port")
    return port


def wait_port(port: int, *, process: subprocess.Popen, timeout: float = 12) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(process.poll() is None, f"Owned Mihomo exited before port {port} became ready")
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise SmokeError(f"Timed out waiting for owned port {port}")


def wait_closed(ports: set[int], timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        open_ports = set()
        for port in ports:
            with socket.socket() as probe:
                probe.settimeout(0.1)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    open_ports.add(port)
        if not open_ports:
            return
        time.sleep(0.1)
    raise SmokeError(f"Owned listeners survived process cleanup: {sorted(open_ports)}")


def stop_owned(process: subprocess.Popen) -> None:
    if process.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def process_uid(process: subprocess.Popen) -> int:
    content = Path(f"/proc/{process.pid}/status").read_text()
    match = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", content, re.MULTILINE)
    require(match is not None, "Could not inspect owned Mihomo UID")
    values = {int(value) for value in match.groups()}
    require(len(values) == 1, "Owned Mihomo changed process credentials")
    return values.pop()


def command_text(arguments: list[str | Path]) -> str:
    return shlex.join([str(value) for value in arguments])


class HealthHandler(BaseHTTPRequestHandler):
    body_token = ""

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        profile = parse_qs(parsed.query).get("profile", [""])[0]
        if parsed.path != "/health" or not re.fullmatch(r"[a-z0-9_-]{1,40}", profile):
            self.send_error(404)
            return
        body = f"{self.body_token}:{profile}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_arguments: object) -> None:
        return


def validate_config(
    binary: Path,
    config: Path,
    data: Path,
    *,
    prefix: list[str],
    environment: dict[str, str],
    commands: list[dict],
) -> None:
    arguments = [*prefix, binary, "-t", "-f", config, "-d", data]
    result = subprocess.run(
        list(map(str, arguments)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
        timeout=30,
        check=False,
    )
    commands.append(
        {
            "command": command_text(arguments),
            "exit_code": result.returncode,
            "output_tail": result.stdout.decode(errors="replace")[-1000:],
        }
    )
    require(result.returncode == 0, f"Mihomo rejected {config.name}")


def start_mihomo(
    binary: Path,
    config: Path,
    data: Path,
    log: Path,
    *,
    prefix: list[str],
    environment: dict[str, str],
    commands: list[dict],
) -> subprocess.Popen:
    arguments = [*prefix, binary, "-f", config, "-d", data]
    stream = log.open("xb")
    log.chmod(0o600)
    process = subprocess.Popen(
        list(map(str, arguments)),
        stdin=subprocess.DEVNULL,
        stdout=stream,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    stream.close()
    commands.append({"command": command_text(arguments), "pid": process.pid, "log": str(log)})
    return process


def curl_health(
    proxy_port: int,
    echo_port: int,
    profile: str,
    *,
    token: str,
    commands: list[dict],
    expect_success: bool,
) -> bool:
    arguments = [
        "/usr/bin/curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "12" if expect_success else "5",
        "--noproxy",
        "",
        "--proxy",
        f"socks5h://127.0.0.1:{proxy_port}",
        f"http://127.0.0.1:{echo_port}/health?profile={profile}",
    ]
    require("-k" not in arguments and "--insecure" not in arguments, "Insecure curl is forbidden")
    environment = {**os.environ, "NO_PROXY": "", "no_proxy": ""}
    result = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=environment,
        timeout=18,
        check=False,
    )
    expected = f"{token}:{profile}\n".encode()
    succeeded = result.returncode == 0 and result.stdout == expected
    commands.append(
        {
            "command": command_text(arguments),
            "exit_code": result.returncode,
            "response_sha256": hashlib.sha256(result.stdout).hexdigest(),
            "response_bytes": len(result.stdout),
            "stderr_tail": result.stderr.decode(errors="replace")[-500:],
            "expected_success": expect_success,
        }
    )
    return succeeded


def reality_keys(
    binary: Path, *, prefix: list[str], environment: dict[str, str]
) -> tuple[str, str]:
    result = subprocess.run(
        [*map(str, prefix), str(binary), "generate", "reality-keypair"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=environment,
        timeout=20,
        check=False,
    )
    output = result.stdout.decode(errors="replace")
    private = re.search(r"^PrivateKey:\s*([A-Za-z0-9_-]{43})$", output, re.MULTILINE)
    public = re.search(r"^PublicKey:\s*([A-Za-z0-9_-]{43})$", output, re.MULTILINE)
    require(result.returncode == 0 and private and public, "Could not generate Reality keypair")
    return private.group(1), public.group(1)


def client_proxy(
    profile: str,
    *,
    server_port: int,
    private: dict[str, str],
    valid: bool,
) -> dict:
    suffix = "valid" if valid else "invalid"
    common = {
        "name": f"{profile}-{suffix}",
        "server": "127.0.0.1",
        "port": server_port,
        "udp": True,
    }
    if profile == "vless_reality_vision":
        return {
            **common,
            "type": "vless",
            "uuid": private["vision_uuid"] if valid else private["wrong_uuid"],
            "flow": "xtls-rprx-vision",
            "encryption": "",
            "tls": True,
            "servername": private["vision_sni"],
            "client-fingerprint": "chrome",
            "reality-opts": {
                "public-key": private["reality_public"],
                "short-id": private["vision_short_id"],
            },
        }
    if profile == "vless_xhttp_reality_xmux":
        return {
            **common,
            "type": "vless",
            "uuid": private["xhttp_uuid"] if valid else private["wrong_uuid"],
            "encryption": "",
            "tls": True,
            "servername": private["xhttp_sni"],
            "client-fingerprint": "chrome",
            "network": "xhttp",
            "alpn": ["h2"],
            "reality-opts": {
                "public-key": private["reality_public"],
                "short-id": private["xhttp_short_id"],
            },
            "xhttp-opts": {"path": private["xhttp_path"], "host": private["xhttp_sni"]},
        }
    if profile == "anytls_shadowtls":
        password = private["anytls_password"] if valid else "wrong-anytls-password"
        return {
            **common,
            "type": "anytls",
            "password": password,
            "tls": True,
            "sni": private["anytls_sni"],
            "client-fingerprint": "chrome",
            "shadow-tls-opts": {"version": 3, "password": password},
        }
    if profile == "mieru":
        return {
            **common,
            "type": "mieru",
            "transport": "TCP",
            "username": "managed-mieru",
            "password": private["mieru_password"] if valid else "wrong-mieru-password",
        }
    return {
        **common,
        "type": "socks5",
        "username": "managed-socks",
        "password": private["socks_password"] if valid else "wrong-socks-password",
    }


def client_config(proxy: dict, port: int) -> dict:
    return {
        "mixed-port": port,
        "bind-address": "127.0.0.1",
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": [proxy],
        "rules": [f"MATCH,{proxy['name']}"],
    }


def exercise(binary: Path, output: Path | None) -> int:
    binary = binary.resolve(strict=True)
    require(binary.is_file() and not binary.is_symlink(), "Mihomo must be one regular file")
    mode = stat.S_IMODE(binary.stat().st_mode)
    require(mode & stat.S_IXUSR, "Mihomo is not executable")
    require(shutil.which("curl") == "/usr/bin/curl", "Expected the audited /usr/bin/curl")
    require(shutil.which("setpriv") is not None or os.geteuid() != 0, "setpriv is required as root")

    work = Path(tempfile.mkdtemp(prefix="open-node-managed-smoke-", dir="/tmp"))
    runtime_user = pwd.getpwnam("nobody") if os.geteuid() == 0 else pwd.getpwuid(os.geteuid())
    runtime_owner = (runtime_user.pw_uid, runtime_user.pw_gid)
    os.chown(work, *runtime_owner)
    work.chmod(0o700)
    prefix = (
        [
            shutil.which("setpriv"),
            f"--reuid={runtime_user.pw_uid}",
            f"--regid={runtime_user.pw_gid}",
            "--clear-groups",
            "--",
        ]
        if os.geteuid() == 0
        else []
    )
    prefix = [str(item) for item in prefix]
    environment = {
        **os.environ,
        "HOME": str(work),
        "TMPDIR": str(work),
        "NO_PROXY": "",
        "no_proxy": "",
    }
    commands: list[dict] = []
    processes: list[subprocess.Popen] = []
    owned_ports: set[int] = set()
    report = {
        "schema_version": 1,
        "status": "failed",
        "work_directory": str(work),
        "mihomo_binary": str(binary),
        "mihomo_sha256": sha256(binary),
        "runtime_uid": runtime_user.pw_uid,
        "runtime_user": runtime_user.pw_name,
        "curl_certificate_verification_disabled": False,
        "shared_443_or_system_nginx_modified": False,
        "profiles": {},
        "commands": commands,
    }
    server: subprocess.Popen | None = None
    httpd: ThreadingHTTPServer | None = None
    http_thread: threading.Thread | None = None
    failure: BaseException | None = None
    try:
        version = subprocess.run(
            [*prefix, str(binary), "-v"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            timeout=10,
            check=False,
        )
        report["mihomo_version"] = version.stdout.decode(errors="replace").splitlines()[0]
        require(
            version.returncode == 0 and "v1.19.30" in report["mihomo_version"],
            "Unexpected Mihomo version",
        )
        reality_private, reality_public = reality_keys(
            binary, prefix=prefix, environment=environment
        )
        private = {
            "reality_private": reality_private,
            "reality_public": reality_public,
            "vision_uuid": "9d0cb9d0-964f-4ef6-897d-6c6b3ccf9e68",
            "xhttp_uuid": "64c9a30b-e08b-4be4-951a-a91c20c7bcb9",
            "wrong_uuid": "9cb5ea7c-cc1a-4bf0-8dc6-558394a65c86",
            "vision_short_id": "0123456789abcdef",
            "xhttp_short_id": "1123456789abcdef",
            "vision_sni": "www.cloudflare.com",
            "xhttp_sni": "www.microsoft.com",
            "anytls_sni": "www.apple.com",
            "xhttp_path": "/open-node-managed-smoke",
            "anytls_password": "open-node-anytls-smoke-6c911",
            "mieru_password": "open-node-mieru-smoke-52b28",
            "socks_password": "open-node-socks-smoke-c9f31",
        }
        server_ports = {
            profile: free_port()
            for profile in (
                "vless_reality_vision",
                "vless_xhttp_reality_xmux",
                "anytls_shadowtls",
                "mieru",
                "socks5",
            )
        }
        require(len(set(server_ports.values())) == len(server_ports), "Fixture port collision")
        owned_ports.update(server_ports.values())
        listeners = [
            {
                "name": "vless_reality_vision",
                "type": "vless",
                "listen": "127.0.0.1",
                "port": server_ports["vless_reality_vision"],
                "users": [
                    {
                        "username": "managed-vision",
                        "uuid": private["vision_uuid"],
                        "flow": "xtls-rprx-vision",
                    }
                ],
                "reality-config": {
                    "dest": private["vision_sni"] + ":443",
                    "private-key": private["reality_private"],
                    "short-id": [private["vision_short_id"]],
                    "server-names": [private["vision_sni"]],
                },
            },
            {
                "name": "vless_xhttp_reality_xmux",
                "type": "vless",
                "listen": "127.0.0.1",
                "port": server_ports["vless_xhttp_reality_xmux"],
                "users": [{"username": "managed-xhttp", "uuid": private["xhttp_uuid"]}],
                "reality-config": {
                    "dest": private["xhttp_sni"] + ":443",
                    "private-key": private["reality_private"],
                    "short-id": [private["xhttp_short_id"]],
                    "server-names": [private["xhttp_sni"]],
                },
                "xhttp-config": {"path": private["xhttp_path"], "host": private["xhttp_sni"]},
            },
            {
                "name": "anytls_shadowtls",
                "type": "anytls",
                "listen": "127.0.0.1",
                "port": server_ports["anytls_shadowtls"],
                "users": {"managed-anytls": private["anytls_password"]},
                "shadow-tls": {
                    "enable": True,
                    "version": 3,
                    "users": [{"name": "managed-anytls", "password": private["anytls_password"]}],
                    "handshake": {"dest": private["anytls_sni"] + ":443"},
                },
            },
            {
                "name": "mieru",
                "type": "mieru",
                "listen": "127.0.0.1",
                "port": server_ports["mieru"],
                "transport": "TCP",
                "users": {"managed-mieru": private["mieru_password"]},
            },
            {
                "name": "socks5",
                "type": "socks",
                "listen": "127.0.0.1",
                "port": server_ports["socks5"],
                "udp": True,
                "users": [{"username": "managed-socks", "password": private["socks_password"]}],
            },
        ]
        server_config = {
            "mode": "rule",
            "log-level": "info",
            "allow-lan": False,
            "listeners": listeners,
            "rules": ["MATCH,DIRECT"],
        }
        server_path = work / "server.json"
        server_data = work / "server-data"
        server_data.mkdir(mode=0o700)
        os.chown(server_data, *runtime_owner)
        private_json(server_path, server_config, owner=runtime_owner)
        validate_config(
            binary,
            server_path,
            server_data,
            prefix=prefix,
            environment=environment,
            commands=commands,
        )

        HealthHandler.body_token = hashlib.sha256(os.urandom(32)).hexdigest()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        echo_port = httpd.server_port
        owned_ports.add(echo_port)
        http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        http_thread.start()
        server = start_mihomo(
            binary,
            server_path,
            server_data,
            work / "server.log",
            prefix=prefix,
            environment=environment,
            commands=commands,
        )
        processes.append(server)
        for port in server_ports.values():
            wait_port(port, process=server)
        require(process_uid(server) == runtime_user.pw_uid, "Mihomo server did not drop root")

        for profile, server_port in server_ports.items():
            result = {
                "server_port": server_port,
                "correct_credential_http_health": False,
                "incorrect_credential_rejected": False,
            }
            report["profiles"][profile] = result
            for valid in (True, False):
                client_port = free_port()
                owned_ports.add(client_port)
                label = profile + ("-valid" if valid else "-invalid")
                config_path = work / f"{label}.json"
                data_path = work / f"{label}-data"
                data_path.mkdir(mode=0o700)
                os.chown(data_path, *runtime_owner)
                proxy = client_proxy(profile, server_port=server_port, private=private, valid=valid)
                private_json(config_path, client_config(proxy, client_port), owner=runtime_owner)
                validate_config(
                    binary,
                    config_path,
                    data_path,
                    prefix=prefix,
                    environment=environment,
                    commands=commands,
                )
                client = start_mihomo(
                    binary,
                    config_path,
                    data_path,
                    work / f"{label}.log",
                    prefix=prefix,
                    environment=environment,
                    commands=commands,
                )
                processes.append(client)
                wait_port(client_port, process=client)
                require(
                    process_uid(client) == runtime_user.pw_uid,
                    "Mihomo client did not drop root",
                )
                succeeded = curl_health(
                    client_port,
                    echo_port,
                    profile,
                    token=HealthHandler.body_token,
                    commands=commands,
                    expect_success=valid,
                )
                if valid:
                    require(succeeded, f"Authenticated {profile} HTTP health failed")
                    result["correct_credential_http_health"] = True
                    result["client_port"] = client_port
                else:
                    require(not succeeded, f"Invalid {profile} credential reached HTTP health")
                    result["incorrect_credential_rejected"] = True
                stop_owned(client)
                processes.remove(client)
        report["status"] = "passed"
    except Exception as error:  # noqa: BLE001 - failure is captured in an audit report
        failure = error
        report["error"] = str(error) if isinstance(error, SmokeError) else type(error).__name__
    finally:
        for process in reversed(processes):
            stop_owned(process)
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if http_thread is not None:
            http_thread.join(timeout=5)
        try:
            wait_closed(owned_ports)
            report["owned_listeners_cleaned"] = True
        except SmokeError as cleanup_error:
            report["owned_listeners_cleaned"] = False
            report["cleanup_error"] = str(cleanup_error)
            report["status"] = "failed"
            failure = failure or cleanup_error
        report_path = work / "report.json"
        private_json(report_path, report, owner=(os.geteuid(), os.getegid()))
        if output is not None:
            output = output.resolve()
            require(output.parent.exists(), "Output parent does not exist")
            require(not output.exists(), "Refusing to overwrite an existing report")
            shutil.copyfile(report_path, output)
            output.chmod(0o600)
        print(f"Managed protocol smoke {report['status']}; report: {report_path}", flush=True)
        for profile, result in report["profiles"].items():
            print(
                f"{profile}: authenticated_health={result['correct_credential_http_health']} "
                f"invalid_rejected={result['incorrect_credential_rejected']}",
                flush=True,
            )
    return 0 if failure is None and report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mihomo", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    raise SystemExit(exercise(arguments.mihomo, arguments.output))
