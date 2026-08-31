"""Check or encrypt explicitly selected local packages without loading the application."""

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, NoReturn

from open_node.services.backup_validation import (
    MAX_ARCHIVE_BYTES,
    BackupArchiveReport,
    validate_backup_archive,
)

COPY_CHUNK_BYTES = 64 * 1024
MAX_COPY_READS = 65536
COPY_SECONDS = 30.0
ERROR_MESSAGE = "备份包检查失败：输入无效、不可读取或超出支持范围。未执行恢复。"
ENCRYPT_ERROR_MESSAGE = (
    "备份包加密失败：输入无效、输出不可用或超出支持范围。未覆盖已有文件，未执行恢复。"
)
_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_REPORT_FIELDS = (
    "archive_size", "payload_size", "file_count", "checked_archive_sha256", "manifest_sha256",
    "structure_verified", "content_hashes_verified", "source_authentication",
    "database_validation", "key_validation", "snapshot_validation", "restore_validation",
    "restoration_ready",
)


class _CLIError(ValueError):
    def __init__(self) -> None:
        super().__init__(ERROR_MESSAGE)


class _HelpFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix="用法：")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's usual diagnostic includes caller-supplied arguments.
        raise _CLIError() from None


class _UniqueValue(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            raise _CLIError()
        setattr(namespace, self.dest, values)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="open-node-backup",
        usage=(
            "%(prog)s validate PATH [--identity KEYFILE] [--json]\n"
            "      %(prog)s encrypt PATH --recipient AGEKEY --output FILE [--json]"
        ),
        add_help=False,
        allow_abbrev=False,
        formatter_class=_HelpFormatter,
        description=(
            "检查 v1 备份包结构和内容摘要，或加密已有 v1 包；"
            "不解压、不恢复、不创建应用快照，也不加载应用配置。"
        ),
        epilog=(
            "输入仅限明确指定的本地普通文件，不接受标准输入或 URL。会先只读复制到匿名私有"
            "暂存文件，结束后关闭清理；不向源文件写入。复制和校验分别使用 30 秒操作间软期限，"
            "无法中断阻塞 I/O。数据库可用性、密钥配对、来源真实性、一致快照及实际恢复均未"
            "检查，不能据此认定恢复就绪；副本检查也不能排除来源被不合作写入者并发修改。"
            "加密仅支持单个原生 age X25519 接收者，使用镜像内固定官方 age。"
            "解密认证须完整通过才检查 ZIP；不向用户目录导出明文。age 子进程另有 30 秒硬期限。"
            "临时空间最多需约两倍包大小；默认容器 /tmp 只有 64 MiB。"
        ),
    )
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument(
        "action", choices=("validate", "encrypt"), metavar="{validate,encrypt}",
        help="validate 只读检查；encrypt 加密已有包，不创建应用快照",
    )
    parser.add_argument("path", metavar="PATH", help="要只读检查的本地文件路径")
    parser.add_argument(
        "--identity", metavar="KEYFILE", action=_UniqueValue,
        help="仅用于 validate：当前用户私有的原生 age 私钥文件，不接受口令或插件",
    )
    parser.add_argument(
        "--recipient", metavar="AGEKEY", action=_UniqueValue,
        help="仅用于 encrypt：一个原生 age X25519 公钥",
    )
    parser.add_argument(
        "--output", metavar="FILE", action=_UniqueValue,
        help="仅用于 encrypt：创建私有密文文件，已有路径一律拒绝",
    )
    parser.add_argument("--json", action="store_true", help="只输出安全 JSON 汇总，不包含清单声明")
    parser.add_argument("-h", "--help", action="help", help="显示此帮助后退出")
    return parser


def _source_flags() -> int:
    if os.name != "posix":
        raise _CLIError()
    for name in ("O_NOFOLLOW", "O_NONBLOCK"):
        value = getattr(os, name, None)
        if type(value) is not int or value <= 0:
            raise _CLIError()
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise _CLIError()


def _source_signature(info: os.stat_result) -> tuple[int, ...]:
    # Reading may legitimately update atime. No timestamp is written back.
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
    )


def _check_staging(staging: BinaryIO) -> None:
    info = os.fstat(staging.fileno())
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 0
    ):
        raise _CLIError()


def _copy_source(
    descriptor: int, initial: os.stat_result, staging: BinaryIO, deadline: float,
) -> str:
    digest = hashlib.sha256()
    count = reads = 0
    while True:
        _check_deadline(deadline)
        reads += 1
        if reads > MAX_COPY_READS:
            raise _CLIError()
        # The one-byte EOF probe detects growth without accepting or copying it.
        limit = min(COPY_CHUNK_BYTES, initial.st_size - count) if count < initial.st_size else 1
        block = os.read(descriptor, limit)
        _check_deadline(deadline)
        if type(block) is not bytes or len(block) > limit:
            raise _CLIError()
        if not block:
            if count != initial.st_size:
                raise _CLIError()
            break
        count += len(block)
        if count > initial.st_size or count > MAX_ARCHIVE_BYTES:
            raise _CLIError()
        written = staging.write(block)
        _check_deadline(deadline)
        if type(written) is not int or written != len(block):
            raise _CLIError()
        digest.update(block)
    final = os.fstat(descriptor)
    _check_deadline(deadline)
    if _source_signature(initial) != _source_signature(final):
        raise _CLIError()
    staging.flush()
    _check_deadline(deadline)
    _check_staging(staging)
    if os.fstat(staging.fileno()).st_size != count:
        raise _CLIError()
    staging.seek(0)
    _check_deadline(deadline)
    return digest.hexdigest()


def _validate_path(path: str) -> BackupArchiveReport:
    if not path or path == "-" or _URI.match(path):
        raise _CLIError()
    flags = _source_flags()
    deadline = time.monotonic() + COPY_SECONDS
    descriptor = os.open(path, flags)
    try:
        _check_deadline(deadline)
        initial = os.fstat(descriptor)
        _check_deadline(deadline)
        # Reject special files and oversized inputs before any source read.
        if not stat.S_ISREG(initial.st_mode) or not 22 <= initial.st_size <= MAX_ARCHIVE_BYTES:
            raise _CLIError()
        # Explicit /tmp avoids application paths and TMPDIR-selected destinations.
        # TemporaryFile uses O_TMPFILE or unlinks its private 0600 fallback before returning.
        with tempfile.TemporaryFile(
            mode="w+b", dir="/tmp", prefix=f"open-node-backup-validate-{os.getpid()}-",
        ) as staging:
            _check_deadline(deadline)
            _check_staging(staging)
            copied_digest = _copy_source(descriptor, initial, staging, deadline)
            report = validate_backup_archive(staging)
            if (
                report.archive_size != initial.st_size
                or report.checked_archive_sha256 != copied_digest
            ):
                raise _CLIError()
            return report
    finally:
        os.close(descriptor)


@contextmanager
def _held_input(path: str, minimum: int, maximum: int, *, private: bool = False):
    if not path or path == "-" or _URI.match(path):
        raise _CLIError()
    descriptor = os.open(path, _source_flags())
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or not minimum <= initial.st_size <= maximum:
            raise _CLIError()
        if private and (
            initial.st_uid != os.geteuid()
            or stat.S_IMODE(initial.st_mode) not in (0o400, 0o600)
            or initial.st_nlink != 1
        ):
            raise _CLIError()
        # A FileIO owns no independent buffer. The encryption service makes its
        # own anonymous copy and never passes this external descriptor to age.
        with os.fdopen(descriptor, "rb", buffering=0, closefd=False) as source:
            yield source, initial
    finally:
        os.close(descriptor)


def _unchanged(source: BinaryIO, initial: os.stat_result) -> None:
    if _source_signature(os.fstat(source.fileno())) != _source_signature(initial):
        raise _CLIError()


def _identity_bytes(path: str) -> bytes:
    deadline = time.monotonic() + COPY_SECONDS
    with _held_input(path, 1, 4096, private=True) as (source, initial):
        result = bytearray()
        for _ in range(4097):
            _check_deadline(deadline)
            block = source.read(min(COPY_CHUNK_BYTES, initial.st_size - len(result) + 1))
            _check_deadline(deadline)
            if type(block) is not bytes or len(result) + len(block) > initial.st_size:
                raise _CLIError()
            if not block:
                if len(result) != initial.st_size:
                    raise _CLIError()
                _unchanged(source, initial)
                return bytes(result)
            result.extend(block)
        raise _CLIError()


@contextmanager
def _output_parent(path: str):
    if (
        not path or path == "-" or _URI.match(path)
        or path.rsplit("/", 1)[-1] in ("", ".", "..")
    ):
        raise _CLIError()
    target = Path(path)
    name = target.name
    if not name or name in (".", "..") or "\x00" in name:
        raise _CLIError()
    descriptor = os.open(target.parent, _source_flags() | os.O_DIRECTORY)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise _CLIError()
        try:
            os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _CLIError()
        # Both staging and publication stay relative to this held directory,
        # even if the operator renames an ancestor while encryption is running.
        yield descriptor, name
    finally:
        os.close(descriptor)


def _same_file(info: os.stat_result, expected: os.stat_result) -> bool:
    return (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)


def _remove_owned(parent: int, name: str, expected: os.stat_result) -> None:
    # Best-effort cleanup in the caller's trusted private directory, not an
    # atomic compare-and-unlink against a hostile process with the same UID.
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode) and _same_file(info, expected):
            os.unlink(name, dir_fd=parent)
    except OSError:
        # Do not hide the original error. A fatal filesystem failure can leave
        # private ciphertext. The public output name is never passed here.
        pass


def _write_encrypted_file(
    source: BinaryIO, descriptor: int, expected_size: int, expected_hash: str,
) -> None:
    deadline = time.monotonic() + COPY_SECONDS
    if source.seek(0) != 0 or source.tell() != 0:
        raise _CLIError()
    _check_staging(source)
    digest = hashlib.sha256()
    total = operations = 0
    while True:
        _check_deadline(deadline)
        operations += 1
        if operations > MAX_COPY_READS:
            raise _CLIError()
        limit = min(COPY_CHUNK_BYTES, expected_size - total) if total < expected_size else 1
        block = source.read(limit)
        _check_deadline(deadline)
        if type(block) is not bytes or len(block) > limit:
            raise _CLIError()
        if not block:
            break
        total += len(block)
        if total > expected_size:
            raise _CLIError()
        offset = 0
        while offset < len(block):
            operations += 1
            if operations > MAX_COPY_READS:
                raise _CLIError()
            _check_deadline(deadline)
            written = os.write(descriptor, block[offset:])
            _check_deadline(deadline)
            if type(written) is not int or not 0 < written <= len(block) - offset:
                raise _CLIError()
            offset += written
        digest.update(block)
    if (
        total != expected_size or digest.hexdigest() != expected_hash
        or os.fstat(source.fileno()).st_size != total
        or os.fstat(descriptor).st_size != total
    ):
        raise _CLIError()
    os.fsync(descriptor)
    _check_deadline(deadline)


def _publish_encrypted(staged, parent: int, name: str) -> None:
    from open_node.services.backup_encryption import MAX_ENCRYPTED_ARCHIVE_BYTES

    report = staged.report
    if (
        type(report.encrypted_size) is not int
        or not 1 <= report.encrypted_size <= MAX_ENCRYPTED_ARCHIVE_BYTES
        or type(report.encrypted_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", report.encrypted_sha256) is None
    ):
        raise _CLIError()
    temporary = f".open-node-encrypted-{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
    initial = None
    try:
        initial = os.fstat(descriptor)
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid() or info.st_nlink != 1
        ):
            raise _CLIError()
        _write_encrypted_file(
            staged.stream, descriptor, report.encrypted_size, report.encrypted_sha256,
        )
        if not _same_file(os.stat(temporary, dir_fd=parent, follow_symlinks=False), initial):
            raise _CLIError()
        # linkat is an atomic no-replace publication; rename would overwrite a
        # file created after the preflight. Unsupported filesystems fail closed.
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        if not _same_file(os.stat(name, dir_fd=parent, follow_symlinks=False), initial):
            raise _CLIError()
        os.fsync(parent)
        os.unlink(temporary, dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.close(descriptor)
        finally:
            if initial is not None:
                _remove_owned(parent, temporary, initial)
            # After linkat, the destination is already a complete checked
            # ciphertext. Never roll back that public name: another process
            # could replace it between a stat and unlink. A later fsync/output
            # failure can therefore leave a complete new file, but no old file
            # is overwritten or removed and no partial bytes are published.


def _encrypt_path(path: str, recipient: str, output: str):
    from open_node.services.backup_encryption import encrypted_backup_archive

    with _output_parent(output) as (parent, name):
        with _held_input(path, 22, MAX_ARCHIVE_BYTES) as (source, initial):
            with encrypted_backup_archive(source, recipient) as staged:
                _unchanged(source, initial)
                _publish_encrypted(staged, parent, name)
                return staged.report


def _validate_encrypted_path(path: str, identity_path: str):
    from open_node.services.backup_encryption import (
        MAX_ENCRYPTED_ARCHIVE_BYTES,
        decrypted_backup_archive,
    )

    identity = _identity_bytes(identity_path)
    with _held_input(path, 1, MAX_ENCRYPTED_ARCHIVE_BYTES) as (source, initial):
        with decrypted_backup_archive(source, identity) as staged:
            _unchanged(source, initial)
            return staged.report


def _summary(report: BackupArchiveReport) -> dict[str, object]:
    # Never serialize report.manifest: its paths and source claims are untrusted input.
    return {name: getattr(report, name) for name in _REPORT_FIELDS}


def _encryption_summary(report) -> dict[str, object]:
    return {
        **_summary(report.archive_report),
        "encryption": report.encryption,
        "encrypted_size": report.encrypted_size,
        "encrypted_sha256": report.encrypted_sha256,
        "authenticated_decryption": report.authenticated_decryption,
    }


def _encrypted_human_report(report) -> str:
    outcome = (
        "加密包完整解密认证通过；未发布明文文件。\n"
        if report.authenticated_decryption
        else "已创建私有加密文件，未覆盖已有文件；未执行私钥解密验证。\n"
    )
    return (
        outcome + f"密文大小：{report.encrypted_size} 字节。\n"
        f"密文 SHA-256：{report.encrypted_sha256}\n"
        "加密类型：age v1，单个 X25519 接收者；不证明发送者身份。\n"
        + _human_report(report.archive_report)
    )


def _human_report(report: BackupArchiveReport) -> str:
    return (
        "v1 备份包结构与内容摘要检查通过。\n"
        f"归档大小：{report.archive_size} 字节；文件内容合计：{report.payload_size} 字节；"
        f"文件数：{report.file_count}。\n"
        f"归档 SHA-256：{report.checked_archive_sha256}\n"
        f"清单 SHA-256：{report.manifest_sha256}\n"
        "未检查：数据库可用性、密钥配对、来源真实性、一致快照和实际恢复。\n"
        "恢复就绪：否。\n"
        "仅检查匿名私有暂存副本；未写入源文件，不执行解压或恢复。\n"
    )


def main(argv: list[str] | None = None) -> int:
    error = ERROR_MESSAGE
    try:
        args = _parser().parse_args(argv)
        if args.action == "encrypt":
            error = ENCRYPT_ERROR_MESSAGE
            if not args.recipient or not args.output or args.identity is not None:
                raise _CLIError()
            report = _encrypt_path(args.path, args.recipient, args.output)
            summary, human = _encryption_summary(report), _encrypted_human_report(report)
        else:
            if args.recipient is not None or args.output is not None:
                raise _CLIError()
            if args.identity is not None:
                report = _validate_encrypted_path(args.path, args.identity)
                summary, human = _encryption_summary(report), _encrypted_human_report(report)
            else:
                report = _validate_path(args.path)
                summary, human = _summary(report), _human_report(report)
        output = (
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n"
            if args.json else human
        )
        sys.stdout.write(output)
        sys.stdout.flush()
        return 0
    except (Exception, KeyboardInterrupt):
        # Never render decoder, filesystem, argument, or resource exception details.
        try:
            sys.stderr.write(error + "\n")
            sys.stderr.flush()
        except (OSError, UnicodeError, ValueError):
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
