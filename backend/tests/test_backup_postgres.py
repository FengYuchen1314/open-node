import sys
from types import SimpleNamespace

import pytest

if sys.platform == "win32":
    pytest.skip(
        "PostgreSQL backup helpers use Linux file-descriptor semantics",
        allow_module_level=True,
    )

from open_node.services import (  # noqa: E402, I001
    backup_dependencies,
    backup_postgres,
    backup_postgres_restore,
)


DATABASE_URL = (
    "postgresql+psycopg://open_node:"
    "0123456789abcdef0123456789abcdef@postgres:5432/open_node?sslmode=require"
)


def test_pg_cli_connection_uses_password_environment_without_uri(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "ambient-secret")
    arguments, environment = backup_postgres._connection(DATABASE_URL)

    assert arguments == [
        "--host", "postgres", "--port", "5432", "--username", "open_node",
        "--dbname", "open_node",
    ]
    assert environment["PGPASSWORD"] == "0123456789abcdef0123456789abcdef"
    assert environment["PGSSLMODE"] == "require"
    assert all("0123456789abcdef" not in value for value in arguments)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://open_node:secret@postgres/open_node",
        "postgresql+psycopg://open_node@postgres/open_node",
        "postgresql+psycopg:///open_node",
        "postgresql+psycopg://open_node:secret@postgres/open_node?target_session_attrs=read-write",
    ],
)
def test_pg_cli_connection_rejects_unsupported_urls(database_url):
    with pytest.raises(backup_postgres.BackupPostgresError):
        backup_postgres._connection(database_url)


def test_restore_converts_sqlalchemy_url_to_native_psycopg_uri():
    parsed = backup_postgres_restore.postgres_url(DATABASE_URL)
    rendered = backup_postgres_restore._url_text(parsed)

    assert rendered.startswith("postgresql://open_node:")
    assert "+psycopg" not in rendered
    assert rendered.endswith("@postgres:5432/open_node?sslmode=require")


def test_dependency_capture_converts_sqlalchemy_url_before_psycopg(monkeypatch):
    captured = []

    def connect(database_url, **options):
        captured.append((database_url, options))
        raise RuntimeError("controlled connection stop")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    with pytest.raises(backup_dependencies.BackupDependencyError):
        backup_dependencies.capture_postgres_dependency_snapshot(DATABASE_URL)

    assert captured == [(
        DATABASE_URL.replace("postgresql+psycopg://", "postgresql://"),
        {"connect_timeout": 10},
    )]


def test_staging_dependency_check_keeps_sqlalchemy_driver(monkeypatch, tmp_path):
    dump = tmp_path / "postgres.dump"
    dump.write_bytes(b"PGDMP")
    captured_dependencies = []
    captured_restore_urls = []
    stage_events = []
    monkeypatch.setattr(
        backup_postgres_restore,
        "_database_command",
        lambda *_args: stage_events.append("create"),
    )
    monkeypatch.setattr(
        backup_postgres_restore,
        "_restore_dump",
        lambda _dump, database_url: captured_restore_urls.append(database_url),
    )
    monkeypatch.setattr(
        backup_postgres_restore,
        "capture_postgres_dependency_snapshot",
        lambda database_url: captured_dependencies.append(database_url) or object(),
    )
    monkeypatch.setattr(
        backup_postgres_restore,
        "check_postgres_backup_dependencies",
        lambda *_args, **_kwargs: SimpleNamespace(totp_status="verified"),
    )
    monkeypatch.setattr(backup_postgres_restore, "_quiesce", lambda _url: {})

    stage, _counts = backup_postgres_restore.restore_postgres_to_staging(
        dump,
        DATABASE_URL,
        {},
        totp_key=None,
        stage_journal=lambda _stage: stage_events.append("journal"),
    )

    expected = DATABASE_URL.replace("/open_node?", f"/{stage}?")
    assert captured_restore_urls == [expected]
    assert captured_dependencies == [expected]
    assert captured_dependencies[0].startswith("postgresql+psycopg://")
    assert stage_events == ["journal", "create"]


def test_staging_restore_rejects_postgres_maintenance_database(tmp_path):
    dump = tmp_path / "postgres.dump"
    dump.write_bytes(b"PGDMP")
    maintenance_url = DATABASE_URL.replace("/open_node?", "/postgres?")

    with pytest.raises(backup_postgres_restore.PostgresRestoreError):
        backup_postgres_restore.restore_postgres_to_staging(
            dump,
            maintenance_url,
            {},
            totp_key=None,
            stage_journal=lambda stage: None,
        )


@pytest.mark.parametrize("name", ["open-node", "UPPER", "", "a" * 64])
def test_restore_rejects_database_names_that_cannot_be_quoted_safely(name):
    with pytest.raises(backup_postgres_restore.PostgresRestoreError):
        backup_postgres_restore.postgres_url(DATABASE_URL, name)
