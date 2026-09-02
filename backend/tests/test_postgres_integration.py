"""Opt-in real PostgreSQL application, custom-dump, and staging-restore gate."""

import os
import shutil
from pathlib import Path

import pytest
from conftest import authenticated_client

DATABASE_URL = os.environ.get("OPEN_NODE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="real PostgreSQL URL not configured")


def test_fresh_application_custom_dump_and_staging_restore(tmp_path: Path):
    from open_node.core.config import Settings
    from open_node.main import create_app
    from open_node.services.backup_postgres import postgres_backup_snapshot
    from open_node.services.backup_postgres_restore import (
        _url_text,
        drop_postgres_database,
        postgres_url,
        restore_postgres_to_staging,
    )
    from test_subscriptions import create_plan_node_fixture

    state = tmp_path / "state"
    scratch = tmp_path / "scratch"
    state.mkdir(mode=0o700)
    scratch.mkdir(mode=0o700)
    settings = Settings(
        database_url=DATABASE_URL,
        control_state_dir=state,
        certificate_state_dir=state / "certificates",
        external_subscriptions_state_dir=state / "external-subscriptions",
        federation_state_dir=state / "federation",
        notifications_state_dir=state / "notifications",
        speedtest_state_dir=state / "speedtests",
        backup_temporary_directory=scratch,
        _env_file=None,
    )
    app = create_app(settings)
    dump = tmp_path / "postgres.dump"
    stage = None
    try:
        import psycopg

        with psycopg.connect(
            DATABASE_URL.replace("postgresql+psycopg://", "postgresql://"),
            connect_timeout=10,
        ) as connection:
            assert connection.execute(
                "SELECT r.rolname,r.rolsuper,r.rolcreatedb,r.rolcreaterole,"
                "r.rolreplication,r.rolbypassrls,"
                "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) "
                "FROM pg_roles r WHERE r.rolname=current_user"
            ).fetchone() == ("open_node", False, True, False, False, False, False)
        with authenticated_client(app) as client:
            created = client.post("/api/v1/servers", json={"name": "postgres-node"})
            assert created.status_code == 201, created.text
            user = client.post("/api/v1/users", json={"username": "postgres-user"})
            assert user.status_code == 201, user.text
            node_id = create_plan_node_fixture(client, namespace="postgres")
            plan = client.post(
                "/api/v1/plans",
                json={
                    "name": "PostgreSQL 套餐",
                    "cycle_days": 30,
                    "traffic_limit_gb": 10,
                    "node_ids": [node_id],
                },
            )
            assert plan.status_code == 201, plan.text
            assert client.put(
                "/api/v1/system-settings/branding",
                json={
                    "expected_revision": 0,
                    "site_title": "PostgreSQL 站点",
                    "brand_title": "PG",
                },
            ).status_code == 200
            assert client.post(
                "/api/v1/announcements",
                json={
                    "type": "general",
                    "title": "PostgreSQL 公告",
                    "body": "真实数据库联调",
                    "expires_minutes": 60,
                },
            ).status_code == 201
        with postgres_backup_snapshot(DATABASE_URL, staging_directory=scratch) as snapshot:
            assert snapshot.engine == "postgresql"
            assert snapshot.size >= 5 and len(snapshot.sha256) == 64
            assert len(snapshot.schema_fingerprint) == 64
            with dump.open("wb") as output:
                shutil.copyfileobj(snapshot.stream, output)
        dump.chmod(0o600)

        stage, counts = restore_postgres_to_staging(
            dump,
            DATABASE_URL,
            {},
            totp_key=None,
            stage_journal=lambda prepared: None,
        )
        assert stage.startswith("open_node_restore_")
        assert counts["invalidated_sessions"] >= 1
        assert counts["cancelled_agent_commands"] == 0
        assert counts["cancelled_certificate_jobs"] == 0

        stage_url = _url_text(postgres_url(DATABASE_URL, stage))
        with psycopg.connect(stage_url, connect_timeout=10) as connection:
            assert connection.execute(
                "SELECT username FROM administrator WHERE id=1"
            ).fetchone() == ("admin",)
    finally:
        for store in (app.state.auth, app.state.inventory, app.state.certificates):
            for name in ("engine", "_engine"):
                engine = getattr(store, name, None)
                if engine is not None:
                    engine.dispose()
        app.state.backup_writes.close()
        if stage is not None:
            drop_postgres_database(DATABASE_URL, stage)
