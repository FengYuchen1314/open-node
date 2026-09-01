"""Container-start entry point for a prepared browser restore."""

from open_node.core.config import Settings
from open_node.domain.restore import BrowserRestoreError
from open_node.services.browser_restore import activate_pending_restore


def main() -> int:
    try:
        root = activate_pending_restore(Settings(_env_file=None))
    except BrowserRestoreError as exc:
        # PostgreSQL and nonstandard/manual layouts simply do not expose this
        # SQLite-only facility. A malformed pending marker still fails closed.
        if exc.code != "restore_upload_unavailable":
            raise
    else:
        print(root, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
