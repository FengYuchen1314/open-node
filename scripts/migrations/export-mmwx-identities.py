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


def optional_columns(connection, table):
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
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


def json_list(raw, label):
    if raw in (None, ""):
        return []
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid JSON list for {label}") from exc
    if not isinstance(values, list):
        raise ExportError(f"invalid JSON list for {label}")
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
            "package_id": "NULL",
            "package_start_date": "NULL",
            "package_end_date": "NULL",
            "is_reset": "0",
            "reset_day": "0",
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
                    "source_package_id": int(row["package_id"] or 0) or None,
                    "package_started_at": optional_text(row["package_start_date"]),
                    "package_expires_at": optional_text(row["package_end_date"]),
                    "is_reset": bool(row["is_reset"]),
                    "reset_day": int(row["reset_day"] or 0),
                    "created_at": optional_text(row["created_at"]),
                }
            )
        if not users:
            raise ExportError("MMWX database contains no users")

        packages = []
        package_columns = optional_columns(connection, "packages")
        if package_columns:
            require_columns("packages", package_columns, {"id", "name"})
            package_sql = ", ".join(
                [
                    selected("id", package_columns),
                    selected("name", package_columns),
                    selected("short_code", package_columns),
                ]
            )
            for row in connection.execute(
                f"SELECT {package_sql} FROM packages ORDER BY id"
            ):
                packages.append(
                    {
                        "source_id": int(row["id"]),
                        "name": str(row["name"]),
                        "short_code": optional_text(row["short_code"]),
                    }
                )

        assignments = {}
        assignment_columns = optional_columns(connection, "user_subscriptions")
        if assignment_columns:
            require_columns(
                "user_subscriptions",
                assignment_columns,
                {"username", "subscription_id"},
            )
            for row in connection.execute(
                "SELECT username, subscription_id FROM user_subscriptions "
                "ORDER BY subscription_id, username"
            ):
                assignments.setdefault(int(row["subscription_id"]), []).append(
                    str(row["username"])
                )

        profiles = []
        profile_columns = optional_columns(connection, "subscribe_files")
        if profile_columns:
            require_columns(
                "subscribe_files",
                profile_columns,
                {"id", "name", "type", "file_short_code", "created_by"},
            )
            fields = {
                "id": "NULL",
                "name": "NULL",
                "description": "''",
                "type": "'create'",
                "filename": "''",
                "template_filename": "''",
                "file_short_code": "NULL",
                "custom_short_code": "NULL",
                "selected_tags": "'[]'",
                "selected_node_ids": "'[]'",
                "selected_custom_rule_ids": "'[]'",
                "selected_override_script_ids": "'[]'",
                "raw_output": "0",
                "sort_order": "0",
                "expire_at": "NULL",
                "created_by": "NULL",
                "created_at": "NULL",
                "updated_at": "NULL",
            }
            profile_sql = ", ".join(
                selected(name, profile_columns, default)
                for name, default in fields.items()
            )
            for row in connection.execute(
                f"SELECT {profile_sql} FROM subscribe_files ORDER BY sort_order, id"
            ):
                identifier = int(row["id"])
                profiles.append(
                    {
                        "source_id": identifier,
                        "owner_username": str(row["created_by"]),
                        "name": str(row["name"]),
                        "description": optional_text(row["description"]) or "",
                        "source_type": str(row["type"]),
                        "filename": optional_text(row["filename"]) or "",
                        "template_filename": optional_text(row["template_filename"])
                        or "",
                        "file_short_code": str(row["file_short_code"]),
                        "custom_short_code": optional_text(row["custom_short_code"]),
                        "selected_tags": json_list(
                            row["selected_tags"],
                            f"subscription {identifier} selected_tags",
                        ),
                        "selected_node_ids": json_list(
                            row["selected_node_ids"],
                            f"subscription {identifier} selected_node_ids",
                        ),
                        "selected_custom_rule_ids": json_list(
                            row["selected_custom_rule_ids"],
                            f"subscription {identifier} selected_custom_rule_ids",
                        ),
                        "selected_override_script_ids": json_list(
                            row["selected_override_script_ids"],
                            f"subscription {identifier} selected_override_script_ids",
                        ),
                        "raw_output": bool(row["raw_output"]),
                        "sort_order": int(row["sort_order"] or 0),
                        "expires_at": optional_text(row["expire_at"]),
                        "assigned_usernames": assignments.get(identifier, []),
                        "created_at": optional_text(row["created_at"]),
                        "updated_at": optional_text(row["updated_at"]),
                    }
                )

        bundle = {
            "version": 1,
            "source_revision": None,
            "users": users,
            "packages": packages,
            "subscription_profiles": profiles,
        }
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
