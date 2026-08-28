"""Host-opted-in, expiring HTTP-01 responses. No ACME account keys reach this module."""

import asyncio
import json
import logging
import os
import re
import stat
from time import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_node_agent.certificates import hostname
from open_node_agent.host_files import guarded_path, remove_file
from open_node_agent.runtime import RuntimeFailure, atomic_write

log = logging.getLogger("open-node-agent")
ENDPOINT = "/api/child/cert/http01"
TOKEN = r"[a-zA-Z0-9_-]{22,128}"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Challenge(Input):
    domain: str = Field(max_length=253)
    token: str = Field(pattern="^" + TOKEN + "$")
    key_authorization: str = Field(max_length=172)

    @field_validator("domain")
    @classmethod
    def domain_name(cls, value):
        return hostname(value)

    @model_validator(mode="after")
    def response(self):
        if not re.fullmatch(re.escape(self.token) + r"\.[a-zA-Z0-9_-]{43}", self.key_authorization):
            raise ValueError("Invalid HTTP-01 key authorization")
        return self


class LeaseRelease(Input):
    lease_id: UUID
    expires_at: float = Field(ge=0, allow_inf_nan=False)


class Presentation(LeaseRelease):
    mode: Literal["standalone", "webroot"]
    webroot_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    challenges: list[Challenge] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def distinct(self):
        if (self.mode == "webroot") != bool(self.webroot_id):
            raise ValueError("Only webroot challenges require a webroot ID")
        if len({item.token for item in self.challenges}) != len(self.challenges):
            raise ValueError("Challenge tokens must be distinct")
        return self


class HttpChallenges:
    def __init__(self, config, journal):
        self.config, self.db = config, journal.db
        self.html = config.state_dir / "nginx/html"
        self.lock = asyncio.Lock()
        self.runner = None
        self.last_error = None
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS http01_leases (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS http01_roots (
                site TEXT PRIMARY KEY, device INTEGER NOT NULL, inode INTEGER NOT NULL
            );
        """)

    def snapshot(self):
        return {
            "version": 1,
            "standalone": bool(self.config.certificate_http_address),
            "webroots": self.config.certificate_webroots,
            "cleanup_error": self.last_error,
        }

    def _leases(self, condition="1=1", parameters=()):
        return [
            {"id": row[0], "payload": json.loads(row[1]), "status": row[2], "expires_at": row[3]}
            for row in self.db.execute(
                "SELECT id,payload,status,expires_at FROM http01_leases WHERE " + condition,
                parameters,
            )
        ]

    def _root(self, site, *, create=False):
        if not isinstance(site, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", site):
            raise RuntimeFailure("Invalid owned HTTP webroot ID")
        directory = self.html / site / ".well-known/acme-challenge"
        for path in reversed([directory, *directory.parents]):
            if path.is_symlink():
                raise RuntimeFailure("HTTP webroot paths cannot contain symlinks")
            if not path.is_relative_to(self.config.state_dir):
                continue
            if create and not path.exists():
                path.mkdir(mode=0o755)
                path.chmod(0o755)
            info = path.stat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
            ):
                raise RuntimeFailure(
                    "HTTP webroot directories must be owned and not publicly writable"
                )
        info = directory.stat()
        record = self.db.execute(
            "SELECT device,inode FROM http01_roots WHERE site=?", (site,)
        ).fetchone()
        if record is None:
            if not create or any(directory.iterdir()):
                raise RuntimeFailure("An unowned HTTP challenge directory must be empty")
            with self.db:
                self.db.execute(
                    "INSERT INTO http01_roots VALUES (?,?,?)", (site, info.st_dev, info.st_ino)
                )
        elif record != (info.st_dev, info.st_ino):
            raise RuntimeFailure("HTTP challenge directory identity changed")
        return directory

    def _file(self, directory, challenge):
        path = guarded_path(directory, challenge["token"])
        if not path.exists():
            return path
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o7022
                or stream.read(257) != challenge["key_authorization"].encode()
            ):
                raise RuntimeFailure("HTTP challenge file was changed outside its lease")
        return path

    def _release(self, lease_id, expires_at):
        rows = self._leases("id=?", (lease_id,))
        if not rows:
            with self.db:
                self.db.execute(
                    "INSERT INTO http01_leases VALUES (?,?,'released',?)",
                    (lease_id, "{}", expires_at),
                )
            return
        lease = rows[0]
        if lease["status"] == "released":
            return
        with self.db:
            self.db.execute("UPDATE http01_leases SET status='releasing' WHERE id=?", (lease_id,))
        payload = lease["payload"]
        if payload["mode"] == "webroot":
            directory = self._root(payload["webroot_id"])
            paths = [self._file(directory, item) for item in payload["challenges"]]
            for path in paths:
                remove_file(path)
        with self.db:
            self.db.execute("UPDATE http01_leases SET status='released' WHERE id=?", (lease_id,))

    def _expire(self):
        failure = None
        for lease in self._leases("status!='released' AND expires_at<=?", (time(),)):
            try:
                self._release(lease["id"], lease["expires_at"])
            except (OSError, RuntimeFailure) as exc:
                failure = failure or exc
        with self.db:
            self.db.execute(
                "DELETE FROM http01_leases WHERE status='released' AND expires_at<?",
                (time() - 86400,),
            )
        if failure:
            raise failure

    async def _respond(self, request):
        token = request.match_info.get("token", "")
        try:
            hosts = request.headers.getall("Host", [])
            address = urlsplit("http://" + hosts[0]) if len(hosts) == 1 else None
            if (
                not address
                or not address.hostname
                or address.username
                or address.password
                or address.path
                or address.query
                or address.fragment
                or (address.port is not None and not 1 <= address.port <= 65535)
            ):
                raise ValueError("Invalid Host")
            domain = hostname(address.hostname)
        except (ValueError, RuntimeFailure):
            raise web.HTTPNotFound() from None
        if (
            not re.fullmatch(TOKEN, token)
            or request.raw_path != "/.well-known/acme-challenge/" + token
            or request.can_read_body
        ):
            raise web.HTTPNotFound()
        for lease in self._leases("status='ready' AND expires_at>?", (time(),)):
            if lease["payload"]["mode"] != "standalone":
                continue
            for item in lease["payload"]["challenges"]:
                if item["token"] == token and item["domain"] == domain:
                    return web.Response(
                        text=item["key_authorization"], headers={"Cache-Control": "no-store"}
                    )
        raise web.HTTPNotFound()

    async def _listener(self):
        needed = self.config.certificate_http_address and any(
            lease["payload"]["mode"] == "standalone"
            for lease in self._leases("status IN ('preparing','ready') AND expires_at>?", (time(),))
        )
        if not needed:
            await self.close()
        elif self.runner is None:
            address = urlsplit("http://" + self.config.certificate_http_address)
            application = web.Application(client_max_size=1024)
            application.router.add_get("/.well-known/acme-challenge/{token}", self._respond)
            runner = web.AppRunner(
                application,
                access_log=None,
                shutdown_timeout=1,
                keepalive_timeout=2,
                max_line_size=2048,
                max_field_size=2048,
            )
            try:
                await runner.setup()
                await web.TCPSite(runner, address.hostname, address.port).start()
            except BaseException:
                await runner.cleanup()
                raise
            self.runner = runner

    async def present(self, body):
        request = Presentation.model_validate(body)
        payload = request.model_dump(mode="json")
        now = time()
        if not now < request.expires_at <= now + 600:
            raise RuntimeFailure("HTTP challenge lease must expire within ten minutes")
        if (
            request.mode == "standalone"
            and not self.config.certificate_http_address
            or request.mode == "webroot"
            and request.webroot_id not in self.config.certificate_webroots
        ):
            raise RuntimeFailure("HTTP challenge mode is not enabled by the host owner")
        lease_id = str(request.lease_id)
        async with self.lock:
            self._expire()
            previous = self._leases("id=?", (lease_id,))
            if previous and (
                previous[0]["status"] in {"released", "releasing"}
                or previous[0]["payload"] != payload
            ):
                raise RuntimeFailure(
                    "HTTP challenge lease was released or reused with different content"
                )
            others = self._leases("id!=? AND status!='released'", (lease_id,))
            if len(others) >= 16:
                raise RuntimeFailure("Too many active HTTP challenge leases")
            tokens = {item.token for item in request.challenges}
            if any(
                tokens & {item["token"] for item in lease["payload"]["challenges"]}
                for lease in others
            ):
                raise RuntimeFailure("HTTP challenge token is already owned by another lease")
            directory = (
                self._root(request.webroot_id, create=True) if request.mode == "webroot" else None
            )
            if directory and not previous:
                for item in payload["challenges"]:
                    path = guarded_path(directory, item["token"])
                    if path.exists():
                        raise RuntimeFailure("HTTP challenge filename is already occupied")
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO http01_leases VALUES (?,?,'preparing',?)",
                    (lease_id, json.dumps(payload), request.expires_at),
                )
            try:
                if directory:
                    for item in payload["challenges"]:
                        path = self._file(directory, item)
                        atomic_write(path, item["key_authorization"].encode())
                        path.chmod(0o644)
                await self._listener()
                with self.db:
                    self.db.execute(
                        "UPDATE http01_leases SET status='ready' WHERE id=?", (lease_id,)
                    )
            except BaseException:
                self._release(lease_id, request.expires_at)
                await self._listener()
                raise
        return {"success": True, "lease_id": lease_id, "expires_at": request.expires_at}

    async def release(self, body):
        request = LeaseRelease.model_validate(body)
        if request.expires_at > time() + 600:
            raise RuntimeFailure("Invalid HTTP challenge lease deadline")
        async with self.lock:
            self._release(str(request.lease_id), request.expires_at)
            await self._listener()
        return {"success": True, "lease_id": str(request.lease_id)}

    async def run(self):
        while True:
            try:
                async with self.lock:
                    self._expire()
                    await self._listener()
                self.last_error = None
            except (OSError, ValueError) as error:
                message = "HTTP challenge cleanup or listener needs host attention"
                if not self.last_error:
                    log.warning("%s (%s)", message, type(error).__name__)
                self.last_error = message
            await asyncio.sleep(1)

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
