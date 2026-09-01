"""A restored instance stays offline until reviewed and explicitly restarted."""

import json
import os
import secrets
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import make_url
from starlette.responses import JSONResponse, RedirectResponse

from open_node.domain.restore import RestoreRecord, RestoreStatus

RESTORE_MARKER = ".open-node-restore.json"
MAX_RECORD_BYTES = 16_384


class RestoreStateError(ValueError):
    def __init__(self):
        super().__init__("恢复记录不可用，请在停止服务后检查恢复目录。")


def _read(path: Path) -> RestoreRecord | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not 1 <= info.st_size <= MAX_RECORD_BYTES
        ):
            raise RestoreStateError()
        data = source.read(MAX_RECORD_BYTES + 1)
        after = os.fstat(source.fileno())
        if any(getattr(after, key) != getattr(info, key) for key in (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink",
        )) or len(data) != info.st_size:
            raise RestoreStateError()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RestoreStateError()
            result[key] = value
        return result
    return RestoreRecord.model_validate(json.loads(data, object_pairs_hook=unique))


class RestoreState:
    def __init__(self, database_url: str, state_root: Path | None = None):
        self.path = None
        self.blocked = False
        self._lock = threading.Lock()
        try:
            url = make_url(database_url)
            if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
                self.path = Path(url.database).absolute().parent / RESTORE_MARKER
            elif url.get_backend_name() == "postgresql" and state_root is not None:
                self.path = state_root.absolute() / RESTORE_MARKER
            record = _read(self.path) if self.path is not None else None
            self.blocked = record is not None and record.status == "review_required"
        except Exception:
            raise RestoreStateError() from None

    def read(self) -> RestoreStatus:
        try:
            record = _read(self.path) if self.path else None
            if self.blocked and record is None:
                raise RestoreStateError()
            return RestoreStatus(
                blocked=self.blocked, record=record,
                restart_required=(
                    self.blocked and record is not None and record.status == "reviewed"
                ),
            )
        except Exception:
            raise RestoreStateError() from None

    def review(self, identifier: UUID) -> RestoreStatus:
        with self._lock:
            return self._review(identifier)

    def _review(self, identifier: UUID) -> RestoreStatus:
        # The HTTP caller serializes proof and this write under its submission
        # lock. Reviewing never unblocks this running process: restart is explicit.
        try:
            current = self.read().record
            if not self.blocked or current is None or current.id != identifier or not self.path:
                raise RestoreStateError()
            if current.status == "reviewed":
                return self.read()
            record = current.model_copy(update={
                "status": "reviewed", "reviewed_at": datetime.now(UTC),
            })
            temporary = self.path.parent / (".restore-review-" + secrets.token_hex(16))
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as target:
                    target.write(record.model_dump_json().encode())
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
            return self.read()
        except Exception:
            raise RestoreStateError() from None


class RestoreIsolationMiddleware:
    def __init__(self, app, state: RestoreState, api_prefix: str):
        self.app, self.state = app, state
        self.auth_prefix = api_prefix + "/auth/"
        self.allowed_api = {
            api_prefix + "/branding", api_prefix + "/backups",
            api_prefix + "/backups/restore-review",
        }

    async def __call__(self, scope, receive, send):
        if not self.state.blocked or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return
        path = scope.get("path", "").rstrip("/") or "/"
        method = scope.get("method", "")
        if path == "/" and method == "GET":
            await RedirectResponse("/backups", status_code=307,
                                   headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        if (
            path.startswith(self.auth_prefix)
            or (method in {"GET", "HEAD"} and (path in {"/backups", "/healthz", "/favicon.svg"}
                                     or path.startswith("/assets/") or path in self.allowed_api))
            or (method == "OPTIONS" and path in self.allowed_api)
            or (method == "POST" and path.endswith("/backups/restore-review")
                and path in self.allowed_api)
        ):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(status_code=503, content={
            "code": "restore_review_required",
            "detail": "恢复后的实例尚未启用，请登录管理员账户，在“备份与恢复”中完成复核后重启。",
            "license_required": False,
        }, headers={"Cache-Control": "no-store", "Retry-After": "60"})
        await response(scope, receive, send)
