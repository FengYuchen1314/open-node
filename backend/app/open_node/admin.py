import argparse
import json
import sys
from getpass import getpass

from pydantic import ValidationError

from open_node.core.config import get_settings
from open_node.domain.auth import AdministratorCredentials
from open_node.domain.initial_setup import InitialSetupError
from open_node.services.auth import AuthStore
from open_node.services.backup_coordination import BackupCoordinationError
from open_node.services.backup_runtime import backup_operation, configured_backup_barrier
from open_node.services.initial_setup import InitialSetupStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the local Open Node administrator")
    parser.add_argument("action", choices=["create", "reset-password", "prepare-setup"])
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument(
        "--json", action="store_true", help="Print the local setup credential as JSON",
    )
    args = parser.parse_args()
    if args.action == "prepare-setup":
        if args.password_stdin:
            parser.error("prepare-setup does not accept a password")
        prepare_setup(parser, json_output=args.json)
        return
    if args.json:
        parser.error("--json is only supported by prepare-setup")
    password = sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass("Password: ")
    if not args.password_stdin and password != getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match\n")
    try:
        credentials = AdministratorCredentials(username=args.username, password=password)
    except ValidationError:
        parser.exit(1, "Use a 1-64 character username and a 12-1024 character password\n")
    try:
        settings = get_settings()
        backup_writes = configured_backup_barrier(settings.database_url)
        try:
            with backup_operation(backup_writes):
                AuthStore(
                    settings.database_url, settings.subscriber_totp_key, settings.app_name
                ).set_administrator(
                    credentials.username,
                    credentials.password.get_secret_value(),
                    reset=args.action == "reset-password",
                )
        finally:
            backup_writes.close()
    except BackupCoordinationError:
        parser.exit(1, "备份停写协调暂不可用，请稍后重试。\n")
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    if args.action == "reset-password":
        print("Administrator password reset. All sessions and two-factor settings were cleared.")
    else:
        print("Administrator created.")


def prepare_setup(parser, *, json_output=False):
    settings = get_settings()
    barrier = configured_backup_barrier(settings.database_url)
    try:
        with backup_operation(barrier):
            auth = AuthStore(settings.database_url, settings.subscriber_totp_key, settings.app_name)
            try:
                token, expires = InitialSetupStore(auth).issue()
            finally:
                auth.engine.dispose()
    except InitialSetupError as exc:
        message = (
            "此实例已初始化。请登录；忘记密码时使用 reset-password。"
            if exc.code == "setup_already_completed" else "初始化凭证暂不可用，请稍后重试。"
        )
        parser.exit(1, message + "\n")
    except BackupCoordinationError:
        parser.exit(1, "备份停写协调暂不可用，请稍后重试。\n")
    finally:
        barrier.close()
    if json_output:
        print(json.dumps({"setup_token": token, "expires_at": expires.isoformat()}))
    else:
        print("在浏览器打开面板，输入以下一次性凭证创建管理员：")
        print(token)
        print(f"凭证有效期 30 分钟，截止 {expires.isoformat()}。请勿分享终端输出。")
        print("再次执行 prepare-setup 会使旧凭证失效；初始化后不能再次签发。")


if __name__ == "__main__":
    main()
