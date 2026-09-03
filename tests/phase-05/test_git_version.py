"""git_version unit tests -- run against a throwaway git repo built in tmp_path."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_PKG_PARENT = _REPO_ROOT / "packages" / "picolet"
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

from picolet.git_version import resolve_git_version  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "test")
    (r / "f.txt").write_text("1")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-q", "-m", "initial")
    return r


def test_no_tags_falls_back_to_commit_count(repo):
    version = resolve_git_version(repo)
    assert version.startswith("0.0.0.dev1+g")
    assert not version.endswith(".dirty")


def test_clean_tag_used_as_is(repo):
    _git(repo, "tag", "v1.2.3")
    assert resolve_git_version(repo) == "1.2.3"


def test_tag_without_v_prefix(repo):
    _git(repo, "tag", "1.2.3")
    assert resolve_git_version(repo) == "1.2.3"


def test_commits_past_tag_bump_and_dev_distance(repo):
    _git(repo, "tag", "v1.2.3")
    (repo / "f.txt").write_text("2")
    _git(repo, "commit", "-q", "-am", "second")
    version = resolve_git_version(repo)
    assert version.startswith("1.2.4.dev1+g")


def test_dirty_tree_appends_suffix(repo):
    _git(repo, "tag", "v1.2.3")
    (repo / "f.txt").write_text("uncommitted")
    version = resolve_git_version(repo)
    assert version == "1.2.3.dirty"


def test_non_repo_raises(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    with pytest.raises(RuntimeError):
        resolve_git_version(not_a_repo)
