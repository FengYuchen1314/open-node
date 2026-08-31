"""Validate a closed private copy of one explicitly selected local backup package."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
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


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="open-node-backup",
        usage="%(prog)s validate PATH [--json]",
        add_help=False,
        allow_abbrev=False,
        formatter_class=_HelpFormatter,
        description="只检查 v1 备份包结构和内容摘要，不解压、不恢复，也不加载应用配置。",
        epilog=(
            "输入仅限明确指定的本地普通文件，不接受标准输入或 URL。会先只读复制到匿名私有"
            "暂存文件，结束后关闭清理；不向源文件写入。复制和校验分别使用 30 秒操作间软期限，"
            "无法中断阻塞 I/O。数据库可用性、密钥配对、来源真实性、一致快照及实际恢复均未"
            "检查，不能据此认定恢复就绪；副本检查也不能排除来源被不合作写入者并发修改。"
        ),
    )
    parser._positionals.title = "位置参数"
    parser._optionals.title = "选项"
    parser.add_argument("action", choices=("validate",), metavar="validate", help="检查备份包")
    parser.add_argument("path", metavar="PATH", help="要只读检查的本地文件路径")
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


def _summary(report: BackupArchiveReport) -> dict[str, object]:
    # Never serialize report.manifest: its paths and source claims are untrusted input.
    return {name: getattr(report, name) for name in _REPORT_FIELDS}


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
    try:
        args = _parser().parse_args(argv)
        report = _validate_path(args.path)
        output = (
            json.dumps(_summary(report), ensure_ascii=False, separators=(",", ":")) + "\n"
            if args.json else _human_report(report)
        )
        sys.stdout.write(output)
        sys.stdout.flush()
        return 0
    except (Exception, KeyboardInterrupt):
        # Never render decoder, filesystem, argument, or resource exception details.
        try:
            sys.stderr.write(ERROR_MESSAGE + "\n")
            sys.stderr.flush()
        except (OSError, UnicodeError, ValueError):
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
