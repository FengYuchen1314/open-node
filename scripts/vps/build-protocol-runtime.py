"""Build the pinned, license-server-free Xray compatibility runtime on the VPS."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

REPOSITORY = "https://github.com/FengYuchen1314/Xray-core-mmwx.git"
REVISION = "d3fdae5833a92070414db588ee9893264147b789"
ROOT = Path(__file__).resolve().parents[2]
PATCHES = [
    ROOT / "runtime/xray/empty-users.patch",
    ROOT / "runtime/xray/anytls-udp-address.patch",
]


def command(*args, cwd, env=None, capture=False):
    return subprocess.run(
        list(map(str, args)),
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        timeout=1200,
    ).stdout


def build(work: Path, go: Path, jobs: int, reference: bool):
    if not 1 <= jobs <= 16 or not work.is_absolute() or work.exists():
        raise ValueError("Use an unused absolute work directory and 1-16 build jobs")
    work.mkdir(parents=True)
    source = work / "source"
    command("git", "init", source, cwd=work)
    command(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "fetch",
        "--depth=1",
        REPOSITORY,
        REVISION,
        cwd=source,
    )
    command(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "checkout",
        "--detach",
        REVISION,
        cwd=source,
    )
    assert (
        command("git", "rev-parse", "HEAD", cwd=source, capture=True).strip()
        == REVISION
    )
    env = {
        **os.environ,
        "CGO_ENABLED": "0",
        "GOTOOLCHAIN": "local",
        "GOMAXPROCS": str(jobs),
    }
    flags = [
        "build",
        "-mod=readonly",
        "-trimpath",
        "-buildvcs=false",
        f"-p={jobs}",
        "-ldflags=-s -w",
        "-o",
    ]
    if reference:
        command(go, *flags, work / "xray-reference", "./main", cwd=source, env=env)
    for patch in PATCHES:
        command("git", "apply", "--check", patch, cwd=source)
        command("git", "apply", patch, cwd=source)
    command(
        go,
        "test",
        "-mod=readonly",
        f"-p={jobs}",
        "./proxy/anytls",
        "./proxy/snell",
        "./proxy/mieru",
        cwd=source,
        env=env,
    )
    binary = work / "xray"
    command(go, *flags, binary, "./main", cwd=source, env=env)
    command(go, "mod", "verify", cwd=source, env=env)
    shutil.copyfile(source / "LICENSE", work / "LICENSE-Xray-MPL-2.0")
    for patch in PATCHES:
        shutil.copyfile(patch, work / patch.name)
    names = command("git", "ls-files", "-z", cwd=source, capture=True).split("\0")
    with tarfile.open(work / "matching-source.tar.gz", "w:gz") as archive:
        for name in filter(None, names):
            archive.add(source / name, arcname="xray-source/" + name, recursive=False)
    manifest = {
        "repository": REPOSITORY,
        "revision": REVISION,
        "patches": {
            patch.name: hashlib.sha256(patch.read_bytes()).hexdigest()
            for patch in PATCHES
        },
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(
            (work / "matching-source.tar.gz").read_bytes()
        ).hexdigest(),
        "go_version": command(go, "version", cwd=source, capture=True).strip(),
        "goos": command(go, "env", "GOOS", cwd=source, env=env, capture=True).strip(),
        "goarch": command(
            go, "env", "GOARCH", cwd=source, env=env, capture=True
        ).strip(),
        "license": "MPL-2.0",
        "activation_required": False,
    }
    (work / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--reference-binary", action="store_true")
    args = parser.parse_args()
    build(args.work_dir, args.go.resolve(strict=True), args.jobs, args.reference_binary)
