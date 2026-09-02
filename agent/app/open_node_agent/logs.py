import logging
import os
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from open_node_agent.config import AgentConfig
from open_node_agent.runtime import RuntimeFailure

LOG_NAMES = tuple(
    service + ".log" + suffix
    for service in ("agent", "xray", "nginx", "mihomo")
    for suffix in ("", ".1", ".2")
)


class PrivateLogFormatter(logging.Formatter):
    def __init__(self, token):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.token = token

    def format(self, record):
        return super().format(record).replace(self.token, "[redacted]")


class OwnedLogs:
    def __init__(self, config: AgentConfig):
        self.config = config

    @contextmanager
    def directory(self):
        fd = os.open(self.config.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise RuntimeFailure("Log directory must be private and owned by the Agent")
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def file(self, directory: int, name: str, *, writable=False):
        if name not in LOG_NAMES:
            raise RuntimeFailure(
                "Only owned Agent, Xray, Nginx, and Mihomo log files may be accessed"
            )
        fd = os.open(
            name,
            (os.O_WRONLY if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                raise RuntimeFailure("Log file must be a regular, singly linked, Agent-owned file")
            yield fd, info
        finally:
            os.close(fd)

    def tail(self, query: dict) -> dict:
        service = query.get("service", ["agent"])[0]
        if service not in {"agent", "xray", "nginx", "mihomo"}:
            raise RuntimeFailure("Unknown log service")
        lines = int(query.get("lines", ["200"])[0])
        if not 1 <= lines <= 2000:
            raise RuntimeFailure("Log lines must be between 1 and 2000")
        with self.directory() as directory:
            try:
                with self.file(directory, service + ".log") as (fd, info):
                    start = max(0, info.st_size - 128_000)
                    os.lseek(fd, start, os.SEEK_SET)
                    content = os.read(fd, 128_000).decode(errors="replace")
                    if start:
                        content = content.partition("\n")[2]
            except FileNotFoundError:
                content = ""
        content = "\n".join(content.splitlines()[-lines:])
        return {
            "success": True,
            "service": service,
            "logs": content.replace(self.config.token.get_secret_value(), "[redacted]"),
        }

    def list(self) -> dict:
        files = []
        with self.directory() as directory:
            for name in LOG_NAMES:
                try:
                    with self.file(directory, name) as (_, info):
                        files.append(
                            {
                                "name": name,
                                "size": info.st_size,
                                "active": name.endswith(".log"),
                                "modified": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
                            }
                        )
                except (OSError, RuntimeFailure):
                    continue
        files.sort(key=lambda item: (not item["active"], item["name"]))
        return {
            "success": True,
            "files": files,
            "total_size": sum(item["size"] for item in files),
            "dir": str(self.config.state_dir),
        }

    def delete(self, query: dict) -> dict:
        all_files = query.get("all", ["0"])[0] == "1"
        name = query.get("name", [""])[0]
        if not all_files and name not in LOG_NAMES:
            raise RuntimeFailure("A listed owned log file name is required")
        if all_files and name:
            raise RuntimeFailure("Select either one log file or all logs")
        removed, freed, errors = 0, 0, []
        names = LOG_NAMES if all_files else (name,)
        with self.directory() as directory:
            for name in names:
                try:
                    with self.file(directory, name, writable=True) as (fd, info):
                        if name.endswith(".log"):
                            # Keep the live writer's inode; unlinking it hides future log entries.
                            os.ftruncate(fd, 0)
                        else:
                            os.unlink(name, dir_fd=directory)
                        removed += 1
                        freed += info.st_size
                except FileNotFoundError:
                    continue
                except (OSError, RuntimeFailure) as error:
                    errors.append({"name": name, "error": str(error)[:512]})
        return {"success": not errors, "removed": removed, "freed": freed, "errors": errors}


def configure_agent_log(config: AgentConfig) -> RotatingFileHandler:
    config.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    logs = OwnedLogs(config)
    with logs.directory() as directory:
        fd = os.open(
            "agent.log",
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        os.close(fd)
        with logs.file(directory, "agent.log", writable=True) as (fd, _):
            os.fchmod(fd, 0o600)
    handler = RotatingFileHandler(
        config.state_dir / "agent.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(PrivateLogFormatter(config.token.get_secret_value()))
    logging.getLogger("open-node-agent").addHandler(handler)
    return handler
