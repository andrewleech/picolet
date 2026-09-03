"""Git-derived versioning for [app] version = "git".

Not a byte-for-bit port of setuptools-scm's scheme -- same spirit (infer a
version from the nearest tag, commit distance, and working-tree dirtiness)
without depending on setuptools-scm as a runtime dependency:

- Exactly on a clean, semver-shaped tag ("v1.2.3" or "1.2.3"): that tag's
  numbers, as-is.
- N commits past a semver tag: "{major}.{minor}.{patch+1}.devN+g{sha}"
  (guess-next-dev, same idea as setuptools-scm's default scheme).
- No semver tag reachable at all: "0.0.0.dev{commit_count}+g{sha}".
- Dirty working tree: ".dirty" appended in either case.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_DESCRIBE_RE = re.compile(r"^(.*)-(\d+)-g([0-9a-f]+)$")


def _run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def resolve_git_version(repo_dir: Path) -> str:
    """Derive a version string from `git describe` in `repo_dir`.

    Raises RuntimeError if `repo_dir` isn't inside a git repository.
    """
    try:
        describe = _run_git(["describe", "--tags", "--long", "--always", "--dirty"], repo_dir)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(
            f'[app] version = "git" requires a git repository at {repo_dir}: {exc}'
        ) from exc

    dirty = describe.endswith("-dirty")
    if dirty:
        describe = describe[: -len("-dirty")]

    # "<tag>-<distance>-g<sha>" when a tag is reachable; with --always, git
    # falls back to just the abbreviated commit hash when there's no tag.
    m = _DESCRIBE_RE.match(describe)
    if m:
        tag, distance, sha = m.group(1), int(m.group(2)), m.group(3)
    else:
        tag, distance, sha = None, None, describe

    tag_match = _TAG_RE.match(tag) if tag else None
    if tag_match and distance == 0:
        version = ".".join(tag_match.groups())
    elif tag_match:
        major, minor, patch = (int(g) for g in tag_match.groups())
        version = f"{major}.{minor}.{patch + 1}.dev{distance}+g{sha}"
    else:
        # No semver-shaped tag reachable: fall back to total commit count as
        # the dev distance, matching setuptools-scm's no-tag behaviour.
        count = _run_git(["rev-list", "--count", "HEAD"], repo_dir)
        version = f"0.0.0.dev{count}+g{sha}"

    if dirty:
        version += ".dirty"
    return version
