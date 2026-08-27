"""Read-only preflight, fast-forward checkout update, then VPS-only tests."""

import base64
import binascii
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return result.stdout.rstrip("\r\n")


def require_directories(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and not part.is_dir()):
            raise ValueError("Checkout and parent paths must be real directories")


def require_clean(path: Path, branch: str) -> None:
    if Path(git("rev-parse", "--show-toplevel", cwd=path)).resolve() != path.resolve():
        raise ValueError("The target must be the repository root")
    if git("symbolic-ref", "--short", "HEAD", cwd=path) != branch:
        raise ValueError(
            "Remote checkout is on a different branch; no branch switch attempted"
        )
    if git("status", "--porcelain", "--untracked-files=all", cwd=path):
        raise ValueError(
            "Remote checkout has local changes; preserve them before updating"
        )


def synchronize(path: Path, repo: str, branch: str, revision: str) -> None:
    if not path.is_absolute():
        raise ValueError("Checkout path must be absolute")
    if not repo or repo.startswith("-") or any(ord(char) < 32 for char in repo):
        raise ValueError("Invalid repository URL")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise ValueError("Expected revision must be a full Git commit ID")
    git("check-ref-format", f"refs/heads/{branch}")
    require_directories(path)
    if not path.exists() or not any(path.iterdir()):
        tip = git("ls-remote", "--exit-code", "--heads", repo, f"refs/heads/{branch}")
        if tip.split()[0] != revision:
            raise ValueError(
                "Remote branch moved; refusing to test an unexpected revision"
            )
        git("clone", "--no-local", "--branch", branch, "--", repo, str(path))
    if not (path / ".git").is_dir() or (path / ".git").is_symlink():
        raise ValueError(
            "Existing target is not an ordinary Git checkout; nothing was removed"
        )
    require_clean(path, branch)
    if git("remote", "get-url", "--all", "origin", cwd=path) != repo:
        raise ValueError(
            "Remote checkout origin does not match the requested repository"
        )
    git("fetch", "--no-tags", "origin", f"refs/heads/{branch}", cwd=path)
    if git("rev-parse", "FETCH_HEAD^{commit}", cwd=path) != revision:
        raise ValueError("Remote branch moved; refusing to test an unexpected revision")
    git("merge-base", "--is-ancestor", "HEAD", revision, cwd=path)
    # Git can overwrite ignored files when an incoming commit starts tracking them.
    added = git(
        "diff", "--name-only", "--diff-filter=A", "-z", "HEAD", revision, cwd=path
    )
    for name in filter(None, added.split("\0")):
        target = path / name
        if target.exists() or target.is_symlink():
            raise ValueError(
                "An incoming tracked file conflicts with an existing local file"
            )
        require_directories(target.parent)
    require_clean(path, branch)
    git("merge", "--ff-only", "--no-edit", "--no-autostash", revision, cwd=path)
    if git("rev-parse", "HEAD", cwd=path) != revision:
        raise ValueError("Checkout changed during synchronization")
    require_clean(path, branch)
    print(f"PASS clean checkout at {revision}", flush=True)


def main() -> int:
    try:
        options = json.loads(base64.b64decode(sys.argv[1], validate=True))
        path = options["remote_dir"]
        if not isinstance(path, str) or not re.fullmatch(
            r"/opt/open-node(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?", path
        ):
            raise ValueError(
                "Target must be /opt/open-node or a direct non-hidden child"
            )
        if not isinstance(options["skip_bootstrap"], bool):
            raise TypeError("skip_bootstrap must be boolean")
        root = Path(path)
        synchronize(root, options["repo_url"], options["branch"], options["revision"])
        if not options["skip_bootstrap"]:
            subprocess.run(
                ["bash", str(root / "scripts/vps/bootstrap-debian.sh")], check=True
            )
        subprocess.run(["bash", str(root / "scripts/vps/run-tests.sh")], check=True)
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        binascii.Error,
    ) as exc:
        print(f"VPS sync refused: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"VPS command failed (exit {exc.returncode}); no reset or cleanup attempted",
            file=sys.stderr,
        )
        if exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
