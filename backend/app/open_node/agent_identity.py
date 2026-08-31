"""Explicit, non-overwriting management of the legacy Agent signing identity."""

import argparse
import json
from pathlib import Path

from open_node.core.config import get_settings
from open_node.services.backup_coordination import BackupCoordinationError
from open_node.services.backup_runtime import backup_operation, configured_backup_barrier
from open_node.services.secure_channel import AgentIdentity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "show"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if not args.path.is_absolute():
        parser.exit(1, "Identity path must be absolute\n")
    try:
        if args.action == "create":
            backup_writes = configured_backup_barrier(get_settings().database_url)
            try:
                with backup_operation(backup_writes):
                    identity = AgentIdentity.create(args.path)
            finally:
                backup_writes.close()
        else:
            # Showing public metadata is read-only and needs no application
            # configuration, database initialization or write admission.
            identity = AgentIdentity.load(args.path)
    except BackupCoordinationError:
        parser.exit(1, "备份停写协调暂不可用，请稍后重试。\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Identity unchanged: {exc}\n")
    print(json.dumps(identity.public_metadata()))


if __name__ == "__main__":
    main()
