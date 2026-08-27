import argparse
import sys
from getpass import getpass

from pydantic import ValidationError

from open_node.core.config import get_settings
from open_node.domain.auth import AdministratorCredentials
from open_node.services.auth import AuthStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the local Open Node administrator")
    parser.add_argument("action", choices=["create", "reset-password"])
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()
    password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass("Password: ")
    if not args.password_stdin and password != getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match\n")
    try:
        credentials = AdministratorCredentials(username=args.username, password=password)
    except ValidationError:
        parser.exit(1, "Use a 1-64 character username and a 12-1024 character password\n")
    try:
        AuthStore(get_settings().database_url).set_administrator(
            credentials.username,
            credentials.password.get_secret_value(),
            reset=args.action == "reset-password",
        )
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print("Administrator saved. Previous sessions were revoked.")


if __name__ == "__main__":
    main()
