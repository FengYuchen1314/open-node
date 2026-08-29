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
            "source_package_id": None,
            "package_started_at": None,
            "package_expires_at": None,
            "is_reset": False,
            "reset_day": 0,
            "created_at": "2026-01-02T03:04:05Z",
        }
    ]
    assert bundle["packages"] == []
    assert bundle["subscription_profiles"] == []
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


def test_exporter_includes_packages_subscription_files_and_assignments(tmp_path):
    database = tmp_path / "mmwx.db"
    output = tmp_path / "identities.json"
    legacy_database(database)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        ALTER TABLE users ADD COLUMN package_id INTEGER;
        ALTER TABLE users ADD COLUMN package_start_date TEXT;
        ALTER TABLE users ADD COLUMN package_end_date TEXT;
        ALTER TABLE users ADD COLUMN is_reset INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE users ADD COLUMN reset_day INTEGER NOT NULL DEFAULT 0;
        CREATE TABLE packages (id INTEGER PRIMARY KEY, name TEXT NOT NULL, short_code TEXT);
        CREATE TABLE subscribe_files (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, type TEXT NOT NULL,
            filename TEXT, template_filename TEXT, file_short_code TEXT NOT NULL,
            custom_short_code TEXT, selected_tags TEXT, selected_node_ids TEXT,
            selected_custom_rule_ids TEXT, selected_override_script_ids TEXT,
            raw_output INTEGER, sort_order INTEGER, expire_at TEXT, created_by TEXT NOT NULL,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE user_subscriptions (username TEXT, subscription_id INTEGER);
        INSERT INTO packages VALUES (7, 'Legacy Premium', 'pkg');
        INSERT INTO subscribe_files VALUES (
            11, 'Mobile', 'Phone profile', 'create', 'mobile.yaml', 'mobile-template.yaml',
            'mob', 'phone', '["mobile"]', '[101]', '[]', '[9]', 0, 3, NULL,
            'alice', '2026-01-03T00:00:00Z', '2026-01-04T00:00:00Z'
        );
        INSERT INTO user_subscriptions VALUES ('alice', 11);
        UPDATE users SET package_id=7, package_start_date='2026-01-01T00:00:00Z',
            package_end_date='2027-01-01T00:00:00Z', is_reset=1, reset_day=5
            WHERE username='alice';
        """
    )
    connection.commit()
    connection.close()

    result = run(database, output)
    assert result.returncode == 0, result.stderr
    bundle = json.loads(output.read_text())
    assert bundle["users"][0]["source_package_id"] == 7
    assert bundle["users"][0]["package_expires_at"] == "2027-01-01T00:00:00Z"
    assert bundle["packages"] == [{"source_id": 7, "name": "Legacy Premium", "short_code": "pkg"}]
    profile = bundle["subscription_profiles"][0]
    assert profile["source_id"] == 11
    assert profile["file_short_code"] == "mob"
    assert profile["selected_node_ids"] == [101]
    assert profile["selected_override_script_ids"] == [9]
    assert profile["assigned_usernames"] == ["alice"]
