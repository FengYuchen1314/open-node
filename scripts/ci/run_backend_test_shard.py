#!/usr/bin/env python3
"""Run one deterministic, file-level shard of the backend pytest suite."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SHARD_COUNT = 12


@dataclass(frozen=True, slots=True)
class TestFile:
    path: Path
    weight: int


@dataclass(slots=True)
class Shard:
    index: int
    weight: int = 0
    files: list[TestFile] = field(default_factory=list)


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def discover_test_files(root: Path) -> tuple[TestFile, ...]:
    tests_root = root / "backend/tests"
    paths = sorted(tests_root.rglob("test_*.py"), key=lambda path: path.as_posix())
    if not paths:
        raise RuntimeError(f"no backend test files found under {tests_root}")

    discovered = []
    for path in paths:
        with path.open("rb") as stream:
            line_count = sum(1 for _ in stream)
        discovered.append(
            TestFile(path=path.relative_to(root), weight=max(1, line_count))
        )
    return tuple(discovered)


def build_shards(files: tuple[TestFile, ...], shard_count: int) -> tuple[Shard, ...]:
    if shard_count < 1:
        raise ValueError("shard count must be positive")
    if shard_count > len(files):
        raise ValueError("shard count must not exceed the number of test files")

    shards = [Shard(index=index) for index in range(shard_count)]
    weighted_files = sorted(
        files,
        key=lambda item: (-item.weight, item.path.as_posix()),
    )
    for test_file in weighted_files:
        target = min(shards, key=lambda shard: (shard.weight, shard.index))
        target.files.append(test_file)
        target.weight += test_file.weight

    expected = {test_file.path for test_file in files}
    assigned = [test_file.path for shard in shards for test_file in shard.files]
    if len(assigned) != len(set(assigned)):
        raise RuntimeError("a backend test file was assigned to more than one shard")
    if set(assigned) != expected:
        raise RuntimeError("backend test shard assignment is incomplete")
    if any(not shard.files for shard in shards):
        raise RuntimeError("backend test shard assignment produced an empty shard")

    for shard in shards:
        shard.files.sort(key=lambda item: item.path.as_posix())
    return tuple(shards)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard-count",
        type=int,
        default=DEFAULT_SHARD_COUNT,
        help=f"number of shards (default: {DEFAULT_SHARD_COUNT})",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        help="zero-based shard index to execute",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate and print all assignments without running pytest",
    )
    return parser.parse_args()


def print_summary(files: tuple[TestFile, ...], shards: tuple[Shard, ...]) -> None:
    print(f"backend test shards: {len(files)} files across {len(shards)} shards")
    for shard in shards:
        preview = ", ".join(test_file.path.name for test_file in shard.files[:3])
        if len(shard.files) > 3:
            preview += ", ..."
        print(
            f"shard {shard.index}: {len(shard.files)} files, "
            f"{shard.weight} lines ({preview})"
        )


def main() -> int:
    args = parse_args()
    root = repository_root()
    try:
        files = discover_test_files(root)
        shards = build_shards(files, args.shard_count)
    except (RuntimeError, ValueError) as error:
        print(f"backend test sharding failed: {error}", file=sys.stderr)
        return 2

    if args.check_only:
        print_summary(files, shards)
        return 0
    if args.shard_index is None:
        print("--shard-index is required unless --check-only is used", file=sys.stderr)
        return 2
    if not 0 <= args.shard_index < len(shards):
        print(
            f"--shard-index must be between 0 and {len(shards) - 1}",
            file=sys.stderr,
        )
        return 2

    shard = shards[args.shard_index]
    print(
        f"running backend shard {shard.index}/{len(shards) - 1}: "
        f"{len(shard.files)} files, {shard.weight} weighted lines",
        flush=True,
    )
    for test_file in shard.files:
        print(f"  {test_file.path.as_posix()}", flush=True)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *(test_file.path.as_posix() for test_file in shard.files),
    ]
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
