"""Export login identities from the active MMWX SQLite schema."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


class ExportError(ValueError):
    pass


def columns(connection, table):
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not rows:
        raise ExportError(f"required table is missing: {table}")
    return {row[1] for row in rows}


def require_columns(table, available, required):
    missing = sorted(set(required) - available)
    if missing:
        raise ExportError(f"{table} is missing required columns: {', '.join(missing)}")


def selected(column, available, default="NULL"):
    return f'"{column}"' if column in available else f'{default} AS "{column}"'


def optional_text(value):
    value = str(value).strip() if value is not None else ""
    return value or None


def recovery_hashes(raw, username):
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid recovery-code JSON for user {username!r}") from exc
    if not isinstance(values, list) or any(
        not isinstance(value, str) for value in values
    ):
        raise ExportError(f"invalid recovery-code list for user {username!r}")
    return values


def fallback_short_code(token):
    return "mmw_" + hashlib.sha256(token.encode()).hexdigest()[:12]


def export_bundle(database):
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        user_columns = columns(connection, "users")
        token_columns = columns(connection, "user_tokens")
        require_columns("users", user_columns, {"username", "password_hash"})
        require_columns("user_tokens", token_columns, {"username", "token"})

        user_fields = {
            "username": "NULL",
            "password_hash": "NULL",
            "email": "NULL",
            "nickname": "NULL",
            "role": "'user'",
            "is_active": "1",
            "totp_secret": "NULL",
            "totp_enabled": "0",
            "recovery_codes": "'[]'",
            "created_at": "NULL",
        }
        token_fields = {
            "username": "NULL",
            "token": "NULL",
            "user_short_code": "NULL",
            "custom_user_short_code": "NULL",
        }
        user_sql = ", ".join(
            selected(name, user_columns, default)
            for name, default in user_fields.items()
        )
        token_sql = ", ".join(
            selected(name, token_columns, default)
            for name, default in token_fields.items()
        )
        tokens = {
            row["username"]: row
            for row in connection.execute(
                f"SELECT {token_sql} FROM user_tokens ORDER BY username"
            )
        }
        users = []
        for row in connection.execute(
            f"SELECT {user_sql} FROM users ORDER BY username"
        ):
            username = str(row["username"])
            enabled = bool(row["totp_enabled"])
            secret = optional_text(row["totp_secret"]) if enabled else None
            recovery = (
                recovery_hashes(row["recovery_codes"], username) if enabled else []
            )
            token_row = tokens.get(row["username"])
            token = optional_text(token_row["token"]) if token_row else None
            generated = (
                optional_text(token_row["user_short_code"]) if token_row else None
            )
            custom = (
                optional_text(token_row["custom_user_short_code"])
                if token_row
                else None
            )
            if not token:
                generated = custom = None
            elif not generated:
                generated = fallback_short_code(token)
            users.append(
                {
                    "username": username,
                    "password_hash": str(row["password_hash"]),
                    "email": optional_text(row["email"]),
                    "display_name": optional_text(row["nickname"]),
                    "source_role": "admin" if row["role"] == "admin" else "user",
                    "is_active": bool(row["is_active"]),
                    "totp_enabled": enabled,
                    "totp_secret": secret,
                    "recovery_code_hashes": recovery,
                    "token": token,
                    "generated_short_code": generated,
                    "custom_short_code": custom,
                    "created_at": optional_text(row["created_at"]),
                }
            )
        if not users:
            raise ExportError("MMWX database contains no users")
        bundle = {"version": 1, "source_revision": None, "users": users}
        fingerprint = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
        bundle["source_revision"] = hashlib.sha256(fingerprint).hexdigest()
        return bundle
    finally:
        connection.close()


def atomic_write(output, bundle, force):
    output = output.resolve()
    if output.exists() and not force:
        raise ExportError("output already exists; pass --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{output.name}.", dir=output.parent
        )
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(bundle, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description="Export MMWX identities for Open Node")
    parser.add_argument("database", type=Path, help="MMWX SQLite database")
    parser.add_argument("output", type=Path, help="destination JSON file")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing output"
    )
    args = parser.parse_args()
    try:
        bundle = export_bundle(args.database)
        atomic_write(args.output, bundle, args.force)
    except (ExportError, OSError, sqlite3.Error) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(f"Exported {len(bundle['users'])} identities to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
