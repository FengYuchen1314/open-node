import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import bcrypt

SCRIPT = Path(__file__).parents[2] / "scripts" / "migrations" / "export-mmwx-identities.py"


def legacy_database(path, *, tokens=True):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            email TEXT,
            nickname TEXT,
            role TEXT,
            is_active INTEGER,
            totp_secret TEXT,
            totp_enabled INTEGER,
            recovery_codes TEXT,
            created_at TEXT
        );
        """
    )
    if tokens:
        connection.execute(
            "CREATE TABLE user_tokens (username TEXT PRIMARY KEY, token TEXT NOT NULL, "
            "user_short_code TEXT, custom_user_short_code TEXT)"
        )
    password = bcrypt.hashpw(b"legacy-secret", bcrypt.gensalt(rounds=4)).decode()
    recovery = "a" * 64
    connection.execute(
        "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "alice",
            password,
            "alice@example.com",
            "Alice",
            "admin",
            1,
            "JBSWY3DPEHPK3PXP",
            1,
            json.dumps([recovery]),
            "2026-01-02T03:04:05Z",
        ),
    )
    if tokens:
        connection.execute(
            "INSERT INTO user_tokens VALUES (?, ?, ?, ?)",
            ("alice", "legacy-token-alice", "abc", "alice_link"),
        )
    connection.commit()
    connection.close()
    return password


def run(database, output, *arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(database), str(output), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_exporter_preserves_identity_fields_without_printing_secrets(tmp_path):
    database = tmp_path / "mmwx.db"
    output = tmp_path / "identities.json"
    password = legacy_database(database)
    result = run(database, output)
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout and "legacy-token-alice" not in result.stdout
    assert os.stat(output).st_mode & 0o777 == 0o600
    bundle = json.loads(output.read_text())
    assert bundle["version"] == 1 and len(bundle["source_revision"]) == 64
    assert bundle["users"] == [
        {
            "username": "alice",
            "password_hash": password,
            "email": "alice@example.com",
            "display_name": "Alice",
            "source_role": "admin",
            "is_active": True,
            "totp_enabled": True,
            "totp_secret": "JBSWY3DPEHPK3PXP",
            "recovery_code_hashes": ["a" * 64],
            "token": "legacy-token-alice",
            "generated_short_code": "abc",
            "custom_short_code": "alice_link",
            "created_at": "2026-01-02T03:04:05Z",
        }
    ]
    refused = run(database, output)
    assert refused.returncode == 1 and "--force" in refused.stderr
    assert run(database, output, "--force").returncode == 0


def test_exporter_rejects_missing_required_schema(tmp_path):
    database = tmp_path / "broken.db"
    output = tmp_path / "identities.json"
    legacy_database(database, tokens=False)
    result = run(database, output)
    assert result.returncode == 1
    assert "required table is missing: user_tokens" in result.stderr
    assert not output.exists()
