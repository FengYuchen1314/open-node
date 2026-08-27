"""Explicit, non-overwriting management of the legacy Agent signing identity."""

import argparse
import json
from pathlib import Path

from open_node.services.secure_channel import AgentIdentity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "show"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if not args.path.is_absolute():
        parser.exit(1, "Identity path must be absolute\n")
    try:
        identity = (
            AgentIdentity.create(args.path)
            if args.action == "create"
            else AgentIdentity.load(args.path)
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Identity unchanged: {exc}\n")
    print(json.dumps(identity.public_metadata()))


if __name__ == "__main__":
    main()
