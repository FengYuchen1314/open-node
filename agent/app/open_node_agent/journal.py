import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from time import time


class CommandJournal:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError("Agent state directory must be private (0700) and not a symlink")
        self.lock = (directory / "agent.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("Another agent owns this state directory") from None
        self.db = sqlite3.connect(directory / "commands.sqlite")
        os.chmod(directory / "commands.sqlite", 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS commands (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                result TEXT, acknowledged INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def begin(self, command: dict) -> dict | None:
        request_id = command["request_id"]
        payload = {key: command.get(key) for key in ("method", "path", "query", "body", "stream")}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        row = self.db.execute(
            "SELECT fingerprint, result FROM commands WHERE request_id=?", (request_id,)
        ).fetchone()
        if row:
            if row[0] != fingerprint:
                return {
                    "request_id": request_id,
                    "status": 409,
                    "error": "Request ID reused with different content",
                }
            if row[1] is not None:
                return json.loads(row[1])
            return {
                "request_id": request_id,
                "status": 409,
                "error": (
                    "Previous execution was interrupted; inspect runtime "
                    "before issuing a new command"
                ),
            }
        with self.db:
            self.db.execute(
                "INSERT INTO commands(request_id, fingerprint, created_at) VALUES (?, ?, ?)",
                (request_id, fingerprint, time()),
            )
        return None

    def finish(self, result: dict) -> None:
        with self.db:
            self.db.execute(
                "UPDATE commands SET result=? WHERE request_id=? AND result IS NULL",
                (json.dumps(result), result["request_id"]),
            )

    def acknowledge(self, request_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE commands SET acknowledged=1 WHERE request_id=?", (request_id,))

    def pending_results(self) -> list[dict]:
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT result FROM commands WHERE result IS NOT NULL AND acknowledged=0 "
                "ORDER BY created_at"
            )
        ]

    def desired_running(self, default: bool) -> bool:
        row = self.db.execute("SELECT value FROM settings WHERE key='runtime_running'").fetchone()
        return row[0] == "true" if row else default

    def set_desired_running(self, running: bool) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('runtime_running', ?)",
                ("true" if running else "false",),
            )

    def close(self) -> None:
        self.db.close()
        self.lock.close()
