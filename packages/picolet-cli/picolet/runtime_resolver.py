"""
Resolve picolet runtime artifacts for a given (target, variant) tuple.

PH03 implementation: hardcoded path to the in-tree build output.
TODO(PH05): replace with a cache + download path that fetches pre-built
            runtimes from the picolet release server into ~/.picolet-cache/.
"""

from __future__ import annotations

from pathlib import Path


class RuntimeNotFound(FileNotFoundError):
    """Raised when the requested runtime artifact cannot be located."""


def resolve_runtime(target: str, variant: str) -> Path:
    """Return the absolute path to the runtime artifact.

    Raises RuntimeNotFound if the artifact does not exist on disk.

    Parameters
    ----------
    target:
        Target triple, e.g. ``"linux-x64"`` or ``"windows-x64"``.
    variant:
        Runtime variant, e.g. ``"cli"``.

    Returns
    -------
    Path
        Absolute path to the pre-built runtime binary.
    """
    # TODO(PH05): consult ~/.picolet-cache/<tag>/ first; download if absent.
    artifact_name = f"picolet-runtime-{target}-{variant}"
    if target == "windows-x64":
        artifact_name += ".exe"

    # Walk up from this file to find the repo root, then locate the build dir.
    # This file lives at packages/picolet-cli/picolet/runtime_resolver.py.
    # The runtime builds land at packages/picolet-runtime/build/.
    here = Path(__file__).parent          # packages/picolet-cli/picolet/
    repo_root = here.parent.parent.parent  # repo root
    artifact = repo_root / "packages" / "picolet-runtime" / "build" / artifact_name

    if not artifact.is_file():
        raise RuntimeNotFound(
            f"runtime artifact not found: {artifact}\n"
            f"  Build it with: ./packages/picolet-runtime/scripts/build-runtime.sh "
            f"--target {target} --variant {variant}\n"
            f"  TODO(PH05): automatic download will land in PH05."
        )

    return artifact.resolve()


def locate_mpy_cross() -> Path:
    """Return the absolute path to the in-tree mpy-cross binary.

    The binary is built by build-runtime.sh step [3/8] inside the
    ubuntu:22.04 container and runs on the host because the host glibc
    is newer.

    TODO(PH05): fall back to a cached / downloaded mpy-cross when the
                in-tree binary is absent.
    """
    here = Path(__file__).parent
    repo_root = here.parent.parent.parent
    mpy_cross = (
        repo_root
        / "packages"
        / "picolet-runtime"
        / "micropython"
        / "mpy-cross"
        / "build"
        / "mpy-cross"
    )
    if not mpy_cross.is_file():
        raise RuntimeNotFound(
            f"mpy-cross not found: {mpy_cross}\n"
            f"  Build it with: ./packages/picolet-runtime/scripts/build-runtime.sh "
            f"--target linux-x64 --variant cli\n"
            f"  TODO(PH05): automatic download will land in PH05."
        )
    return mpy_cross.resolve()
