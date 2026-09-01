"""Constrain the dedicated PostgreSQL role before application SQL can run."""

from sqlalchemy.engine import make_url


class PostgresRoleError(RuntimeError):
    pass


def _native_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        return ""
    if (
        url.username != "open_node"
        or url.database != "open_node"
        or url.password is None
        or not url.host
        or set(url.query) - {"sslmode"}
    ):
        raise PostgresRoleError()
    return url.set(
        drivername="postgresql", database="postgres"
    ).render_as_string(hide_password=False)


def restrict_postgres_application_role(database_url: str) -> None:
    """Self-demote the official init role, then verify its least-privilege contract."""
    try:
        native = _native_url(database_url)
        if not native:
            return
        import psycopg
        from psycopg import sql

        query = (
            "SELECT r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole, "
            "r.rolreplication, r.rolbypassrls, r.rolcanlogin, "
            "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid) "
            "FROM pg_roles r WHERE r.rolname=current_user"
        )
        with psycopg.connect(native, connect_timeout=10) as connection:
            row = connection.execute(query).fetchone()
            if row is None or row[0] != "open_node":
                raise PostgresRoleError()
            if row[1]:
                connection.execute(
                    sql.SQL(
                        "ALTER ROLE {} NOSUPERUSER CREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier("open_node"))
                )
                connection.commit()
        # Reconnect so authorization cannot inherit a pre-demotion backend state.
        with psycopg.connect(native, connect_timeout=10) as connection:
            row = connection.execute(query).fetchone()
        if row != ("open_node", False, True, False, False, False, True, False):
            raise PostgresRoleError()
    except PostgresRoleError:
        raise
    except Exception:
        raise PostgresRoleError() from None
