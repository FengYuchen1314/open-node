import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/vps/sync-and-test.py"
SPEC = importlib.util.spec_from_file_location("vps_sync", SCRIPT)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def git(path, *args):
    return sync.git(*args, cwd=path)


def commit(path, value):
    (path / "source.txt").write_text(value)
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        value,
    )
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path):
    origin, author, target = [tmp_path / name for name in ("origin.git", "author", "target")]
    sync.git("init", "--bare", "--initial-branch=main", str(origin))
    sync.git("clone", str(origin), str(author))
    (author / ".gitignore").write_text("private.env\n")
    old = commit(author, "original")
    git(author, "push", "origin", "main")
    sync.git("clone", str(origin), str(target))
    new = commit(author, "updated")
    git(author, "push", "origin", "main")
    return origin, author, target, old, new


def test_sync_fast_forwards_and_preserves_ignored_files(checkout):
    origin, _, target, _, new = checkout
    (target / "private.env").write_text("keep secret")
    sync.synchronize(target, str(origin), "main", new)
    assert git(target, "rev-parse", "HEAD") == new
    assert (target / "source.txt").read_text() == "updated"
    assert (target / "private.env").read_text() == "keep secret"


@pytest.mark.parametrize("existing_empty", [False, True])
def test_sync_clones_into_missing_or_empty_directory(checkout, tmp_path, existing_empty):
    origin, _, _, _, new = checkout
    target = tmp_path / "new target"
    if existing_empty:
        target.mkdir()
    sync.synchronize(target, str(origin), "main", new)
    assert git(target, "rev-parse", "HEAD") == new


@pytest.mark.parametrize("name", ["source.txt", "untracked.txt"])
def test_sync_refuses_local_edits_without_changing_files(checkout, name):
    origin, _, target, old, new = checkout
    (target / name).write_text("operator edit")
    with pytest.raises(ValueError, match="local changes"):
        sync.synchronize(target, str(origin), "main", new)
    assert git(target, "rev-parse", "HEAD") == old
    assert (target / name).read_text() == "operator edit"


def test_sync_refuses_divergence_without_resetting(checkout):
    origin, _, target, _, new = checkout
    local = commit(target, "local commit")
    with pytest.raises(subprocess.CalledProcessError):
        sync.synchronize(target, str(origin), "main", new)
    assert git(target, "rev-parse", "HEAD") == local
    assert (target / "source.txt").read_text() == "local commit"


def test_sync_refuses_a_moved_remote_branch(checkout):
    origin, _, target, old, _ = checkout
    with pytest.raises(ValueError, match="branch moved"):
        sync.synchronize(target, str(origin), "main", old)
    assert git(target, "rev-parse", "HEAD") == old


def test_sync_refuses_other_origin(checkout):
    _, _, target, old, new = checkout
    with pytest.raises(ValueError, match="origin does not match"):
        sync.synchronize(target, "https://example.invalid/other.git", "main", new)
    assert git(target, "rev-parse", "HEAD") == old


def test_sync_refuses_other_branch(checkout):
    origin, _, target, old, new = checkout
    git(target, "switch", "-c", "operator")
    with pytest.raises(ValueError, match="different branch"):
        sync.synchronize(target, str(origin), "main", new)
    assert git(target, "branch", "--show-current") == "operator"
    assert git(target, "rev-parse", "HEAD") == old


def test_sync_does_not_remove_non_repository_directory(checkout, tmp_path):
    origin, _, _, _, new = checkout
    target = tmp_path / "existing"
    target.mkdir()
    (target / "important").write_text("preserve")
    with pytest.raises(ValueError, match="nothing was removed"):
        sync.synchronize(target, str(origin), "main", new)
    assert (target / "important").read_text() == "preserve"


@pytest.mark.parametrize("parent", [False, True])
def test_sync_refuses_symlinked_target_or_parent(checkout, tmp_path, parent):
    origin, _, target, old, new = checkout
    link = tmp_path / "link"
    link.symlink_to(tmp_path if parent else target, target_is_directory=True)
    with pytest.raises(ValueError, match="real directories"):
        sync.synchronize(link / "target" if parent else link, str(origin), "main", new)
    assert git(target, "rev-parse", "HEAD") == old


def test_sync_preserves_ignored_files_even_if_new_revision_tracks_them(checkout):
    origin, author, target, old, _ = checkout
    (target / "private.env").write_text("operator secret")
    (author / "private.env").write_text("incoming default")
    git(author, "add", "-f", "private.env")
    new = commit(author, "tracks previously ignored file")
    git(author, "push", "origin", "main")
    with pytest.raises(ValueError, match="conflicts"):
        sync.synchronize(target, str(origin), "main", new)
    assert git(target, "rev-parse", "HEAD") == old
    assert (target / "private.env").read_text() == "operator secret"
