"""Container-start entry point for a prepared browser restore."""

from open_node.core.config import Settings
from open_node.domain.restore import BrowserRestoreError
from open_node.services.browser_restore import activate_pending_restore
from open_node.services.postgres_security import restrict_postgres_application_role


def main() -> int:
    try:
        settings = Settings(_env_file=None)
        restrict_postgres_application_role(settings.database_url)
        root = activate_pending_restore(settings)
    except BrowserRestoreError as exc:
        # Nonstandard/manual layouts do not expose browser restoration. A
        # malformed SQLite or PostgreSQL pending marker still fails closed.
        if exc.code != "restore_upload_unavailable":
            raise
    else:
        print(root, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
