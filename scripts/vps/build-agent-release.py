#!/usr/bin/env python3
"""Build and verify four Agent release assets from one exact committed revision.

Run only on the isolated Linux VPS, using an existing Python 3.11+ environment
with pip and the project's hatchling build backend already installed. This
does not install build dependencies, create a tag/release, or upload anything.

Example::

    /path/to/build-venv/bin/python build-agent-release.py \
        --repository /opt/open-node/mmwx-parity-candidate \
        --revision FULL_40_CHARACTER_COMMIT --version 0.3.0a0 \
        --output /tmp/private-release-directory/assets

Only committed Git objects are exported; the checkout and its uncommitted
changes are never used as build input. The output must be a new or empty
absolute directory outside the repository, with an existing parent directory.
"""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import email.parser
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

BOOTSTRAP_FILES = (
    "service.py",
    "lifecycle_protocol.py",
    "lifecycle_host.py",
    "lifecycle_report.py",
    "LICENSE",
)
VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?"
MAX_WHEEL_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024


class BuildFailure(RuntimeError):
    """A bounded failure whose message is safe to show without build logs."""


def require(condition: bool, message: str):
    if not condition:
        raise BuildFailure(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=60,
    )
    require(result.returncode == 0, "Cannot read the requested committed Git source")
    return result.stdout


def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    require(
        bool(name)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and str(path) == name.rstrip("/"),
        "An archive contains an unsafe or non-canonical member path",
    )
    return path


def export_source(repository: Path, revision: str, directory: Path):
    require(
        git(repository, "cat-file", "-t", revision).strip() == b"commit",
        "The revision must identify a commit, not a tag or other Git object",
    )
    resolved = git(repository, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    require(resolved == revision, "The resolved commit differs from the requested revision")
    raw = git(repository, "archive", "--format=tar", revision, "agent", "LICENSE")
    require(len(raw) <= MAX_SOURCE_BYTES, "Committed Agent source exceeds the export limit")
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            name = safe_member(member.name)
            require(member.name not in seen, "The source archive contains duplicate members")
            seen.add(member.name)
            require(
                name.parts[0] == "agent" or str(name) == "LICENSE",
                "The source export contains a file outside the Agent and license",
            )
            target = directory.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            require(member.isfile(), "The source export cannot contain links or special files")
            require(member.size <= MAX_SOURCE_BYTES, "A source file exceeds the export limit")
            source = archive.extractfile(member)
            require(source is not None, "Cannot read a committed source file")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("xb") as stream:
                stream.write(source.read())
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def source_version(source: Path, expected: str) -> dict:
    project = tomllib.loads((source / "agent/pyproject.toml").read_text(encoding="utf8"))
    require(project["project"]["name"] == "open-node-agent", "Unexpected project name")
    require(
        project["project"].get("version") == expected,
        "pyproject version does not match --version",
    )
    require(
        project["build-system"]["build-backend"] == "hatchling.build",
        "This builder supports the Agent's existing hatchling backend only",
    )
    tree = ast.parse((source / "agent/app/open_node_agent/__init__.py").read_bytes())
    versions = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            versions.append(ast.literal_eval(node.value))
    require(versions == [expected], "Runtime __version__ does not match the project version")
    return project


def stop_build(process: subprocess.Popen):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def build_wheel(source: Path, work: Path, epoch: str, version: str) -> tuple[Path, dict]:
    tools = {}
    for package in ("pip", "hatchling"):
        try:
            tools[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            raise BuildFailure(
                f"Use an existing build environment with {package} installed"
            ) from None
    built = work / "wheel"
    built.mkdir()
    # An allowlisted environment, --no-index and --no-build-isolation prevent
    # inherited index credentials, downloads, or checkout-specific build tools.
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "SOURCE_DATE_EPOCH": epoch,
        "TMPDIR": str(work),
    }
    with (work / "pip-build.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                "--no-index", "--no-cache-dir", "--disable-pip-version-check",
                "--wheel-dir", str(built), str(source / "agent"),
            ],
            cwd=work,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=180)
            require(
                code == 0,
                f"Offline pip wheel build failed (exit {code}); no build logs published",
            )
        except subprocess.TimeoutExpired:
            raise BuildFailure("Offline pip wheel build exceeded 180 seconds") from None
        finally:
            stop_build(process)
    expected = built / f"open_node_agent-{version}-py3-none-any.whl"
    require(
        list(built.iterdir()) == [expected],
        "Build did not produce exactly the expected pure-Python wheel",
    )
    return expected, tools


def package_sources(source: Path) -> dict[str, bytes]:
    package = source / "agent/app"
    return {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in (package / "open_node_agent").rglob("*") if path.is_file()
    }


def verify_wheel(wheel: Path, expected: dict[str, bytes], version: str, project: dict) -> dict:
    raw = wheel.read_bytes()
    require(0 < len(raw) <= MAX_WHEEL_BYTES, "Wheel exceeds the host helper's 32 MiB limit")
    info_directory = f"open_node_agent-{version}.dist-info"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Wheel contains duplicate members")
        for member in archive.infolist():
            safe_member(member.filename)
            require(not member.is_dir(), "Wheel contains unexpected directory members")
            mode = member.external_attr >> 16
            require(not stat.S_ISLNK(mode), "Wheel contains a symbolic link")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(
            metadata_names == [info_directory + "/METADATA"],
            "Unexpected wheel metadata identity",
        )
        metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_names[0]))
        normalized_name = re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower()
        require(normalized_name == "open-node-agent", "Wheel distribution name does not match")
        require(metadata.get_all("Version") == [version], "Wheel metadata version does not match")
        require(
            metadata.get("Requires-Python") == project["project"]["requires-python"],
            "Wheel Python requirement does not match committed source",
        )
        wheel_metadata = email.parser.BytesParser().parsebytes(
            archive.read(info_directory + "/WHEEL")
        )
        require(wheel_metadata.get_all("Tag") == ["py3-none-any"], "Unexpected wheel platform tag")
        require(wheel_metadata.get("Root-Is-Purelib") == "true", "Wheel is not pure Python")
        record_name = info_directory + "/RECORD"
        rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf8"))))
        require(all(len(row) == 3 for row in rows), "Malformed wheel RECORD")
        records = {row[0]: row[1:] for row in rows}
        require(
            len(records) == len(rows) and set(records) == set(names),
            "Wheel RECORD membership differs",
        )
        for name, (checksum, size) in records.items():
            if name == record_name:
                require(checksum == size == "", "RECORD must leave its own digest and size empty")
                continue
            content = archive.read(name)
            encoded = (
                base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
            )
            require(checksum == "sha256=" + encoded, "Wheel RECORD digest mismatch")
            require(size == str(len(content)), "Wheel RECORD size mismatch")
        actual = {name for name in names if name.startswith("open_node_agent/")}
        require(
            actual == set(expected),
            "Wheel package files differ from the committed source export",
        )
        for name, content in expected.items():
            require(
                archive.read(name) == content,
                "Wheel package bytes differ from committed source",
            )
    checksum = digest(raw)
    return {
        "wheel_info": {"version": version, "sha256": checksum, "id": version + "-" + checksum[:16]},
        "source_files": len(expected),
        "record_entries": len(records),
        "bytes": len(raw),
    }


def bootstrap_sources(source: Path) -> dict[str, bytes]:
    return {
        name: (source / "LICENSE" if name == "LICENSE"
               else source / "agent/app/open_node_agent" / name).read_bytes()
        for name in BOOTSTRAP_FILES
    }


def build_bootstrap(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, mtime=0, compresslevel=9,
    ) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o644
                member.uid = member.gid = member.mtime = 0
                member.uname = member.gname = ""
                archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def verify_bootstrap(raw: bytes, files: dict[str, bytes]):
    require(raw[:3] == b"\x1f\x8b\x08" and raw[4:8] == b"\x00" * 4, "Unexpected gzip timestamp")
    require(not raw[3] & 0x08, "Gzip header must not embed an output filename")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        members = archive.getmembers()
        require(
            [member.name for member in members] == list(BOOTSTRAP_FILES),
            "Unexpected bootstrap members",
        )
        for member in members:
            require(
                member.isfile() and member.mode == 0o644
                and member.uid == member.gid == member.mtime == 0
                and member.uname == member.gname == "" and not member.pax_headers,
                "Bootstrap member ownership, mode, timestamp or type differs",
            )
            source = archive.extractfile(member)
            require(source is not None, "Cannot read bootstrap member")
            with source:
                require(source.read() == files[member.name], "Bootstrap source bytes differ")
    require(build_bootstrap(files) == raw, "Bootstrap generation is not reproducible")


def prepare_output(path: Path, repository: Path) -> Path:
    require(
        path.is_absolute() and ".." not in path.parts,
        "--output must be an explicit absolute path",
    )
    require(len(path.parts) >= 3, "Use a dedicated output directory, not a broad filesystem root")
    for component in (path, *path.parents):
        require(not component.is_symlink(), "The output path cannot contain symbolic links")
    require(
        not path.resolve().is_relative_to(repository),
        "Release output must be outside the repository",
    )
    require(path.parent.is_dir(), "The output parent directory must already exist")
    path.mkdir(mode=0o700, exist_ok=True)
    require(
        path.is_dir() and not any(path.iterdir()),
        "Output must be new or empty; existing artifacts are preserved",
    )
    return path


def build(repository: Path, revision: str, version: str, output: Path) -> dict:
    require(sys.platform == "linux", "Run this builder only on the isolated Linux VPS")
    require(sys.version_info >= (3, 11), "Python 3.11 or newer is required")
    require(
        re.fullmatch(r"[0-9a-f]{40}", revision) is not None,
        "Use an explicit full lowercase 40-character Git commit",
    )
    require(
        len(version) <= 64 and re.fullmatch(VERSION_PATTERN, version) is not None,
        "Use an explicit supported Agent version",
    )
    repository = repository.resolve(strict=True)
    require(repository.is_dir(), "The repository path must be a Git checkout")
    output = prepare_output(output, repository)
    epoch = git(repository, "show", "-s", "--format=%ct", revision).decode().strip()
    require(
        epoch.isdigit() and int(epoch) >= 315532800,
        "Commit timestamp cannot be used for a reproducible wheel",
    )
    with tempfile.TemporaryDirectory(prefix="open-node-agent-release-source-") as temporary:
        work = Path(temporary)
        source = work / "source"
        source.mkdir()
        export_source(repository, revision, source)
        project = source_version(source, version)
        # Snapshot before invoking the build backend, not after it has had an
        # opportunity to modify the exported working files.
        expected_sources = package_sources(source)
        sources = bootstrap_sources(source)
        wheel, tools = build_wheel(source, work, epoch, version)
        verification = verify_wheel(wheel, expected_sources, version, project)
        bootstrap_name = f"open-node-agent-bootstrap-{version}.tar.gz"
        bootstrap = build_bootstrap(sources)
        verify_bootstrap(bootstrap, sources)
        assets = {wheel.name: wheel.read_bytes(), bootstrap_name: bootstrap}
        executable_hashes = {name: digest(assets[name]) for name in sorted(assets)}
        manifest = {
            "source_commit": revision,
            "version": version,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "artifacts": executable_hashes,
        }
        assets["BUILD.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
        checksums = {name: digest(content) for name, content in assets.items()}
        assets["SHA256SUMS"] = "".join(
            checksum + "  " + name + "\n" for name, checksum in checksums.items()
        ).encode()
        parsed = json.loads(assets["BUILD.json"])
        require(parsed == manifest, "BUILD.json round-trip verification failed")
        require(
            parsed["artifacts"] == {name: digest(assets[name]) for name in executable_hashes},
            "BUILD.json artifact hashes differ from the final bytes",
        )
        require(
            len(assets["SHA256SUMS"].splitlines()) == 3,
            "SHA256SUMS must contain exactly three entries",
        )
        require(
            not any(output.iterdir()),
            "Output changed during the build; refusing to overwrite it",
        )
        for name, content in assets.items():
            with (output / name).open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        require(
            {path.name for path in output.iterdir()} == set(assets),
            "Unexpected output artifact set",
        )
        final_hashes = {name: digest((output / name).read_bytes()) for name in assets}
        require(
            final_hashes == {name: digest(content) for name, content in assets.items()},
            "Written output bytes differ",
        )
        for line in (output / "SHA256SUMS").read_text().splitlines():
            checksum, name = line.split("  ")
            require(re.fullmatch(r"[0-9a-f]{64}", checksum) is not None, "Invalid checksum format")
            require(checksum == final_hashes[name], "Written SHA256SUMS verification failed")
        return {
            "status": "verified",
            "output": str(output),
            "source_commit": revision,
            "version": version,
            "source_date_epoch": epoch,
            "build_tools": tools,
            "artifacts": final_hashes,
            **verification,
            "bootstrap_members": list(BOOTSTRAP_FILES),
            "checks": [
                "metadata", "RECORD", "committed_source_bytes",
                "bootstrap", "BUILD.json", "SHA256SUMS",
            ],
            "published": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = build(
            arguments.repository, arguments.revision, arguments.version, arguments.output,
        )
    except (
        BuildFailure, OSError, ValueError, KeyError, SyntaxError,
        subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile,
    ) as error:
        # No pip output, inherited environment, or source contents are echoed.
        message = str(error) if isinstance(error, BuildFailure) else type(error).__name__
        print("Agent release build failed: " + message, file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
