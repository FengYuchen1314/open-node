"""File handoff to the root-owned, fixed-function application update helper."""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from open_node.domain.application_updates import (
    ApplicationUpdateAccepted,
    ApplicationUpdateError,
    ApplicationUpdateState,
)

STATE_FILE = "state.json"
REQUEST_FILE = "request.json"
PENDING = {"checking", "updating"}
EXPECTED_STATE_KEYS = {
    "schema_version", "managed", "status", "request_id", "current_revision",
    "latest_revision", "has_update", "checked_at", "started_at", "completed_at",
    "message", "release_url", "license_required",
}


def _read_bounded(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(16_384, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short file write")
        view = view[written:]


def _unavailable(revision: str) -> ApplicationUpdateState:
    current = revision if len(revision) == 40 else "unknown"
    return ApplicationUpdateState(
        managed=False,
        status="unavailable",
        request_id=None,
        current_revision=current,
        latest_revision=None,
        has_update=None,
        checked_at=None,
        started_at=None,
        completed_at=None,
        message="当前部署没有可用的宿主机更新助手，请使用安装脚本更新。",
        release_url=None,
    )


class ApplicationUpdateStore:
    def __init__(
        self, root: Path | None, revision: str, state_owner_uid: int, state_group_gid: int
    ):
        self.root = root
        self.revision = revision
        self.state_owner_uid = state_owner_uid
        self.state_group_gid = state_group_gid

    def _directory_fd(self) -> int:
        if self.root is None:
            raise ApplicationUpdateError("application_update_unavailable", 503)
        try:
            info = self.root.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != self.state_owner_uid
                or info.st_gid != self.state_group_gid
                or stat.S_IMODE(info.st_mode) != 0o1770
            ):
                raise OSError("unsafe update directory")
            return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            raise ApplicationUpdateError("application_update_unavailable", 503) from None

    def status(self) -> ApplicationUpdateState:
        if self.root is None:
            return _unavailable(self.revision)
        try:
            directory = self._directory_fd()
            try:
                descriptor = os.open(STATE_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    info = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or info.st_uid != self.state_owner_uid
                        or info.st_gid != self.state_group_gid
                        or stat.S_IMODE(info.st_mode) != 0o640
                        or info.st_size < 2
                        or info.st_size > 16_384
                    ):
                        raise OSError("unsafe update state")
                    content = _read_bounded(descriptor, 16_384)
                finally:
                    os.close(descriptor)
            finally:
                os.close(directory)

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate state field")
                    result[key] = value
                return result

            raw = json.loads(content.decode("utf-8"), object_pairs_hook=unique,
                             parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(raw, dict) or set(raw) != EXPECTED_STATE_KEYS:
                raise ValueError("invalid state fields")
            # Strict Pydantic models still accept RFC 3339 timestamps through
            # their JSON path. Duplicate and unknown keys were checked above.
            state = ApplicationUpdateState.model_validate_json(content)
            if state.current_revision != "unknown" and len(state.current_revision) != 40:
                raise ValueError("invalid current revision")
            if state.latest_revision is not None and len(state.latest_revision) != 40:
                raise ValueError("invalid latest revision")
            expected_url = (
                None if state.latest_revision is None else
                f"https://github.com/FengYuchen1314/open-node/commit/{state.latest_revision}"
            )
            if state.release_url != expected_url:
                raise ValueError("invalid release URL")
            return state
        except (ApplicationUpdateError, OSError, UnicodeError, ValueError, TypeError,
                RecursionError, ValidationError):
            return _unavailable(self.revision)

    def _queue(self, action: str, expected_revision: str | None) -> ApplicationUpdateAccepted:
        state = self.status()
        if not state.managed:
            raise ApplicationUpdateError("application_update_unavailable", 503)
        if state.status in PENDING:
            raise ApplicationUpdateError("application_update_busy", 409)
        if action == "apply" and (
            not state.has_update or state.latest_revision != expected_revision
        ):
            raise ApplicationUpdateError("application_update_target_changed", 409)
        request_id = uuid4()
        payload = json.dumps({
            "schema_version": 1,
            "request_id": str(request_id),
            "action": action,
            "expected_revision": expected_revision,
            "requested_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
        directory = self._directory_fd()
        try:
            try:
                descriptor = os.open(
                    REQUEST_FILE,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
            except FileExistsError:
                raise ApplicationUpdateError("application_update_busy", 409) from None
            try:
                try:
                    _write_all(descriptor, payload)
                    os.fsync(descriptor)
                except OSError:
                    # A partially written request must not be left for the
                    # root-owned path helper to interpret. If the helper won
                    # the race and already consumed it, the outcome is still
                    # deliberately reported as unknown rather than retried.
                    try:
                        os.unlink(REQUEST_FILE, dir_fd=directory)
                    except OSError:
                        pass
                    raise ApplicationUpdateError(
                        "application_update_state_unavailable", 503
                    ) from None
            finally:
                os.close(descriptor)
            try:
                os.fsync(directory)
            except OSError:
                # The complete request may already be visible to systemd.
                # Keep it in place and make callers reconcile through GET;
                # deleting or automatically replaying it would be unsafe.
                raise ApplicationUpdateError(
                    "application_update_state_unavailable", 503
                ) from None
        finally:
            os.close(directory)
        return ApplicationUpdateAccepted(request_id=request_id, action=action)

    def check(self) -> ApplicationUpdateAccepted:
        return self._queue("check", None)

    def apply(self, expected_revision: str) -> ApplicationUpdateAccepted:
        return self._queue("apply", expected_revision)
