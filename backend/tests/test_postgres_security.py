import pytest
from open_node.services.postgres_security import (
    PostgresRoleError,
    _native_url,
    restrict_postgres_application_role,
)


def test_sqlite_role_restriction_is_a_noop():
    assert restrict_postgres_application_role("sqlite:///:memory:") is None


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://postgres:secret@postgres/open_node",
        "postgresql+psycopg://open_node:secret@postgres/other",
        "postgresql+psycopg://open_node:secret@postgres/open_node?options=unsafe",
    ],
)
def test_role_restriction_requires_the_dedicated_official_identity(database_url):
    with pytest.raises(PostgresRoleError):
        _native_url(database_url)


def test_role_restriction_native_url_drops_sqlalchemy_driver_name():
    rendered = _native_url(
        "postgresql+psycopg://open_node:secret@postgres:5432/open_node?sslmode=require"
    )

    assert rendered == (
        "postgresql://open_node:secret@postgres:5432/postgres?sslmode=require"
    )
