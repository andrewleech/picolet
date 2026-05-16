"""
Resolve picolet runtime artifacts for a given (target, variant) tuple.

Decision-tree (evaluated in order; first match wins):

  1. explicit_path     Use the provided path as-is; no integrity check.
  2. from_source       Invoke build-runtime.sh via Docker.
  3. Cache lookup      SHA256-verified hit in the per-user cache.
  4. Network download  Fetch artifact + .sha256 + .cdx.json; verify; cache.
  5. In-tree fallback  packages/picolet-runtime/build/<artifact> if present.
  6. Hard error        Structured three-option message.

Steps 3-5 are skipped when no_cache=True; control goes from step 2 (or
start) straight to download, then hard-errors if the download fails.

Configuration knobs (highest to lowest precedence):

  source:  PICOLET_RUNTIME_SOURCE env > [runtime] source in picolet.toml >
           _DEFAULT_BASE_URL constant.
  tag:     PICOLET_RUNTIME_TAG env > [runtime] tag in picolet.toml >
           packages/picolet-runtime/RUNTIME_TAG file content.
  cache:   PICOLET_CACHE_DIR env > XDG_CACHE_HOME/picolet (Linux) >
           ~/.cache/picolet (Linux fallback) > %LOCALAPPDATA%/picolet/cache (Windows).

GitHub Releases CDN asset downloads (releases/download/) are not subject
to the 60 req/hr API rate limit. No auth token is needed for public releases;
do not add auth logic without confirming rate-limit evidence first.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

_DEFAULT_BASE_URL = "https://github.com/andrewleech/picolet/releases/download"

# Timeout in seconds for urllib.request.urlopen.
# urllib has no default timeout; without one, a stalled connection hangs
# forever.  30 s is generous for a local file:// URL and reasonable for a
# CDN serving a sub-1 MiB binary.
_URLOPEN_TIMEOUT = 30

# Streaming chunk size for SHA256 computation and download writes.
_CHUNK_SIZE = 65536

# URL schemes accepted by default.  Anything else (file://, ftp://, data://,
# gopher://, ...) is rejected to avoid local-file or non-HTTP exfiltration via
# a hostile picolet.toml.  Tests and air-gapped workflows can opt in to file://
# by setting PICOLET_ALLOW_FILE_URLS=1.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Environment variables.
_ENV_ALLOW_FILE_URLS = "PICOLET_ALLOW_FILE_URLS"
_ENV_ALLOW_UNVERIFIED = "PICOLET_ALLOW_UNVERIFIED_CACHE"


class ResolvedRuntime(NamedTuple):
    binary: Path
    sbom: "Path | None"   # None when no .cdx.json was available


class RuntimeNotFound(FileNotFoundError):
    """Raised when the requested runtime artifact cannot be located."""


class RuntimeDownloadError(RuntimeError):
    """Raised when a download fails and no fallback is available."""


class RuntimeIntegrityError(RuntimeError):
    """Raised when a cached artifact fails SHA256 verification and cannot be repaired."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class _Config:
    tag: str
    base_url: str
    cache_root: Path


# ---------------------------------------------------------------------------
# Repo-root resolution
#
# TODO(A6): the repo-walk pattern here is a development convenience.  When
# the CLI ships as an installable wheel the resolver must locate mpy-cross,
# build-runtime.sh, the in-tree build/ directory, and the RUNTIME_TAG sidecar
# via importlib.resources.files("picolet_runtime") (or similar) instead of
# walking three parents up from __file__.  That is a packaging refactor —
# layout for the wheel, a picolet_runtime data package, sdist/MANIFEST changes —
# and is deliberately left for a follow-up phase.  The single _repo_root()
# helper below is the chokepoint to update when that work lands.
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Walk up from this file to find the repo root.

    This file lives at packages/picolet-cli/picolet/runtime_resolver.py.
    The repo root is three levels up.  See the TODO(A6) note above for the
    packaging story.
    """
    return Path(__file__).parent.parent.parent.parent


# Back-compat alias.  Existing tests (and external callers) patch
# ``rr._find_repo_root`` via mock.patch.object; keep the name so they
# continue to work while internal code uses _repo_root() directly.
def _find_repo_root() -> Path:
    """Deprecated alias for :func:`_repo_root`.  See A6 TODO."""
    return _repo_root()


def _validate_url_scheme(url: str) -> None:
    """Reject URLs whose scheme is not in :data:`_ALLOWED_URL_SCHEMES`.

    ``file://`` is accepted only when :envvar:`PICOLET_ALLOW_FILE_URLS` is set
    to ``1`` — this is the documented test / air-gapped escape hatch.  All
    other schemes (ftp, data, gopher, jar, ...) hard-error so that a hostile
    ``[runtime] source`` cannot smuggle local-file reads or non-HTTP
    protocols past the resolver.

    Raises
    ------
    RuntimeNotFound
        When the scheme is rejected.  The message explicitly names the env
        var that would re-enable file:// so users can self-serve.
    """
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in _ALLOWED_URL_SCHEMES:
        return
    if scheme == "file" and os.environ.get(_ENV_ALLOW_FILE_URLS) == "1":
        return
    if scheme == "file":
        raise RuntimeNotFound(
            f"runtime source URL uses file:// scheme: {url}\n"
            f"  file:// is rejected by default to prevent a hostile "
            f"picolet.toml from reading arbitrary local files.\n"
            f"  Set {_ENV_ALLOW_FILE_URLS}=1 to allow file:// "
            f"(intended for tests and air-gapped mirrors only)."
        )
    raise RuntimeNotFound(
        f"runtime source URL uses unsupported scheme {scheme!r}: {url}\n"
        f"  Allowed schemes: {', '.join(sorted(_ALLOWED_URL_SCHEMES))}.\n"
        f"  Update PICOLET_RUNTIME_SOURCE or [runtime] source in picolet.toml."
    )


def _read_runtime_tag_sidecar() -> str:
    """Read packages/picolet-runtime/RUNTIME_TAG; return stripped content."""
    tag_file = _repo_root() / "packages" / "picolet-runtime" / "RUNTIME_TAG"
    if tag_file.is_file():
        return tag_file.read_text().strip()
    return "runtime-v0.1.0"   # last-resort default if sidecar absent


def _cache_root() -> Path:
    """Compute the per-user cache root per OS and env configuration.

    Priority:
      1. PICOLET_CACHE_DIR env var.
      2. Linux/macOS: ${XDG_CACHE_HOME:-$HOME/.cache}/picolet.
      3. Windows (native CPython, sys.platform == "win32"):
             %LOCALAPPDATA%/picolet/cache.

    WSL2 reports sys.platform == "linux" and must use the XDG path; do not
    attempt to detect WSL and redirect to the Windows %LOCALAPPDATA%.
    """
    override = os.environ.get("PICOLET_CACHE_DIR")
    if override:
        return Path(override)

    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local_appdata) / "picolet" / "cache"

    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "picolet"
    return Path.home() / ".cache" / "picolet"


def _load_config(config: "dict | None" = None) -> _Config:
    """Resolve tag, base_url, and cache_root from env, picolet.toml, and sidecar.

    Parameters
    ----------
    config:
        Pre-parsed picolet.toml data dict (or None).  Only the [runtime]
        subtable is consulted.
    """
    runtime_section: dict = {}
    if config and isinstance(config.get("runtime"), dict):
        runtime_section = config["runtime"]

    # Tag resolution.
    tag = (
        os.environ.get("PICOLET_RUNTIME_TAG")
        or runtime_section.get("tag")
        or _read_runtime_tag_sidecar()
    )

    # Source / base URL resolution.
    base_url = (
        os.environ.get("PICOLET_RUNTIME_SOURCE")
        or runtime_section.get("source")
        or _DEFAULT_BASE_URL
    )

    _validate_url_scheme(base_url)

    return _Config(tag=tag, base_url=base_url.rstrip("/"), cache_root=_cache_root())


# ---------------------------------------------------------------------------
# Artifact naming
# ---------------------------------------------------------------------------

def _artifact_name(target: str, variant: str) -> str:
    """Return the artifact filename for (target, variant).

    e.g. linux-x64 / cli → picolet-runtime-linux-x64-cli
         windows-x64 / cli → picolet-runtime-windows-x64-cli.exe
    """
    from picolet._targets import target_exe_suffix
    return f"picolet-runtime-{target}-{variant}{target_exe_suffix(target)}"


# ---------------------------------------------------------------------------
# SHA256 helpers
# ---------------------------------------------------------------------------

def _compute_sha256(path: Path) -> str:
    """Return the lowercase hex SHA256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_sha256(artifact: Path, sha256_path: Path) -> bool:
    """Return True iff artifact's SHA256 matches the digest in sha256_path.

    The .sha256 sidecar format is: <hex-digest> [optional whitespace and filename]
    Only the first 64 hex characters are read.
    """
    sidecar_text = sha256_path.read_text().strip()
    expected = sidecar_text[:64].lower()
    if len(expected) != 64:
        # Malformed sidecar — treat as mismatch.
        return False
    actual = _compute_sha256(artifact)
    return actual == expected


# ---------------------------------------------------------------------------
# Cache lookup
# ---------------------------------------------------------------------------

def _unverified_allowed(allow_unverified: bool) -> bool:
    """Return True iff the user opted into running without a sha256 sidecar.

    Two channels are accepted: the ``--allow-unverified-runtime`` CLI flag
    (which surfaces here as the function arg) and the
    :envvar:`PICOLET_ALLOW_UNVERIFIED_CACHE` env var.  Both are escape hatches
    for development and air-gapped mirrors; the default is to refuse.
    """
    if allow_unverified:
        return True
    return os.environ.get(_ENV_ALLOW_UNVERIFIED) == "1"


def _unverified_refusal(artifact: str, where: str) -> RuntimeIntegrityError:
    """Build the canonical refusal error for a missing sha256 sidecar."""
    return RuntimeIntegrityError(
        f"{artifact} {where} has no .sha256 sidecar — "
        f"binary not verified — refusing to execute. "
        f"Set {_ENV_ALLOW_UNVERIFIED}=1 to override."
    )


def _check_cache(
    cfg: _Config,
    artifact: str,
    verbose: bool,
    allow_unverified: bool = False,
) -> "ResolvedRuntime | None":
    """Return a ResolvedRuntime if the artifact is in the cache and valid.

    Verifies SHA256 on cache hit.  A missing sidecar is a hard error unless
    the caller has opted into unverified runs (see :func:`_unverified_allowed`).

    Returns None on cache miss or SHA256 mismatch (so the caller can attempt
    a re-download).  Raises :class:`RuntimeIntegrityError` only when the cache
    entry exists but cannot be verified and the user has not opted in.
    """
    tag_dir = cfg.cache_root / "runtime" / cfg.tag
    binary = tag_dir / artifact
    sha256_file = tag_dir / f"{artifact}.sha256"
    sbom_file = tag_dir / f"{artifact}.cdx.json"

    if not binary.is_file():
        return None

    if not sha256_file.is_file():
        if not _unverified_allowed(allow_unverified):
            raise _unverified_refusal(artifact, "in cache")
        # Opted in: proceed without verification, but make the bypass visible.
        print(
            f"warning: no .sha256 sidecar in cache for {artifact}; "
            f"proceeding without integrity check ({_ENV_ALLOW_UNVERIFIED}=1 set)",
            file=sys.stderr,
        )
        sbom = sbom_file if sbom_file.is_file() else None
        if verbose:
            print(f"Using cached runtime (no integrity check): {binary}", file=sys.stderr)
        return ResolvedRuntime(binary=binary, sbom=sbom)

    if not _verify_sha256(binary, sha256_file):
        print(
            f"warning: SHA256 mismatch for cached {artifact}; will re-download",
            file=sys.stderr,
        )
        return None

    sbom = sbom_file if sbom_file.is_file() else None
    if verbose:
        print(f"Using cached runtime: {binary}", file=sys.stderr)
    return ResolvedRuntime(binary=binary, sbom=sbom)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _fetch_url(url: str, dest: Path) -> None:
    """Download url into dest using urllib. Raises urllib.error.URLError on failure."""
    with urllib.request.urlopen(url, timeout=_URLOPEN_TIMEOUT) as resp:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(_CHUNK_SIZE)
                if not chunk:
                    break
                fh.write(chunk)


def _commit_temp(tmp: Path, final: Path) -> None:
    """Atomically move ``tmp`` to ``final``.

    Prefers :meth:`Path.rename` (atomic on a single filesystem); falls back to
    :func:`shutil.move` for cross-filesystem writes.  The ``finally`` unlink
    of ``tmp`` is a belt-and-suspenders cleanup — both ``rename`` and
    ``shutil.move`` are expected to remove the source on success; the unlink
    catches the corner case where ``shutil.move`` raises mid-copy.
    """
    try:
        tmp.rename(final)
    except OSError:
        shutil.move(str(tmp), final)
    finally:
        tmp.unlink(missing_ok=True)


def _download(
    cfg: _Config,
    artifact: str,
    verbose: bool,
    allow_unverified: bool = False,
) -> "ResolvedRuntime":
    """Fetch artifact + .sha256 + .cdx.json into the cache.

    Uses atomic write: download to ``<artifact>.tmp``, verify SHA256, rename.
    The ``.sha256`` and ``.cdx.json`` sidecars are written only after the
    binary rename succeeds, so a partial state has a re-downloadable missing
    sidecar rather than a corrupted binary.

    A missing ``.sha256`` sidecar at the source is a hard error unless the
    caller opts into unverified runs via :envvar:`PICOLET_ALLOW_UNVERIFIED_CACHE`
    or the ``--allow-unverified-runtime`` CLI flag.

    Raises
    ------
    RuntimeDownloadError
        On network failure (binary fetch) or SHA256 mismatch.
    RuntimeIntegrityError
        When the sidecar is unavailable and the user has not opted in.
    """
    tag_dir = cfg.cache_root / "runtime" / cfg.tag
    tag_dir.mkdir(parents=True, exist_ok=True)

    binary_url = f"{cfg.base_url}/{cfg.tag}/{artifact}"
    sha256_url = f"{cfg.base_url}/{cfg.tag}/{artifact}.sha256"
    sbom_url = f"{cfg.base_url}/{cfg.tag}/{artifact}.cdx.json"

    binary_path = tag_dir / artifact
    sha256_path = tag_dir / f"{artifact}.sha256"
    sbom_path = tag_dir / f"{artifact}.cdx.json"

    tmp_binary = tag_dir / f".{artifact}.tmp"
    tmp_sha256 = tag_dir / f".{artifact}.sha256.tmp"
    tmp_sbom = tag_dir / f".{artifact}.cdx.json.tmp"

    if verbose:
        print(f"Downloading runtime {cfg.tag}/{artifact}", file=sys.stderr)

    # Download binary to .tmp; clean up on any failure.
    try:
        _fetch_url(binary_url, tmp_binary)
    except (urllib.error.URLError, OSError) as exc:
        tmp_binary.unlink(missing_ok=True)
        raise RuntimeDownloadError(str(exc)) from exc

    # Download .sha256 sidecar.
    sha256_available = False
    try:
        _fetch_url(sha256_url, tmp_sha256)
        sha256_available = True
    except (urllib.error.URLError, OSError):
        tmp_sha256.unlink(missing_ok=True)

    if not sha256_available:
        if not _unverified_allowed(allow_unverified):
            tmp_binary.unlink(missing_ok=True)
            raise _unverified_refusal(artifact, "at the download source")
        print(
            f"warning: no .sha256 sidecar available for {artifact}; "
            f"proceeding without integrity check ({_ENV_ALLOW_UNVERIFIED}=1 set)",
            file=sys.stderr,
        )

    # Verify SHA256 before committing to cache.
    if sha256_available and not _verify_sha256(tmp_binary, tmp_sha256):
        tmp_binary.unlink(missing_ok=True)
        tmp_sha256.unlink(missing_ok=True)
        raise RuntimeDownloadError(
            f"SHA256 mismatch for downloaded {artifact}; "
            "the release artifact may be corrupted"
        )

    # Commit binary, then sidecar (sidecar only after binary lands).
    _commit_temp(tmp_binary, binary_path)
    if sha256_available:
        _commit_temp(tmp_sha256, sha256_path)

    # Fetch .cdx.json (best-effort; 404 is not a failure).
    sbom: "Path | None" = None
    try:
        _fetch_url(sbom_url, tmp_sbom)
        _commit_temp(tmp_sbom, sbom_path)
        sbom = sbom_path
    except (urllib.error.URLError, OSError) as exc:
        tmp_sbom.unlink(missing_ok=True)
        # 404 is expected during early development before PH15 publishes sidecars.
        print(
            f"debug: .cdx.json not available for {artifact}: {exc}",
            file=sys.stderr,
        )

    if verbose:
        print(f"Cached runtime at: {binary_path}", file=sys.stderr)

    return ResolvedRuntime(binary=binary_path, sbom=sbom)


# ---------------------------------------------------------------------------
# --from-source build
# ---------------------------------------------------------------------------

def _check_docker() -> bool:
    """Return True if Docker is available and responsive."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _build_from_source(target: str, variant: str, verbose: bool) -> Path:
    """Invoke build-runtime.sh for (target, variant); return path to the built artifact.

    Preconditions:
      - Docker is available.
      - packages/picolet-runtime/scripts/build-runtime.sh exists and is executable.

    Raises RuntimeNotFound if preconditions fail.
    Does not write to the cache (deliberate: --from-source is for source
    modification workflows; caching a custom build would be surprising).
    """
    if not _check_docker():
        raise RuntimeNotFound(
            "docker is required for --from-source builds; install Docker and try again"
        )

    repo_root = _find_repo_root()
    build_script = repo_root / "packages" / "picolet-runtime" / "scripts" / "build-runtime.sh"

    if not build_script.is_file():
        raise RuntimeNotFound(
            f"build-runtime.sh not found: {build_script}\n"
            "  Is packages/picolet-runtime present in the repo?"
        )
    if not os.access(build_script, os.X_OK):
        raise RuntimeNotFound(
            f"build-runtime.sh is not executable: {build_script}"
        )

    if verbose:
        print(
            f"Invoking build-runtime.sh --target {target} --variant {variant}",
            file=sys.stderr,
        )

    subprocess.run(
        [str(build_script), "--target", target, "--variant", variant],
        check=True,
        cwd=str(repo_root),
    )

    artifact = _artifact_name(target, variant)
    built = repo_root / "packages" / "picolet-runtime" / "build" / artifact
    if not built.is_file():
        raise RuntimeNotFound(
            f"build-runtime.sh succeeded but output not found: {built}"
        )

    return built.resolve()


# ---------------------------------------------------------------------------
# In-tree fallback
# ---------------------------------------------------------------------------

def _intree_fallback(target: str, variant: str) -> "Path | None":
    """Return the in-tree build path if the artifact exists, else None."""
    artifact = _artifact_name(target, variant)
    repo_root = _find_repo_root()
    candidate = repo_root / "packages" / "picolet-runtime" / "build" / artifact
    if candidate.is_file():
        return candidate.resolve()
    return None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def resolve_runtime(
    target: str,
    variant: str,
    *,
    explicit_path: "Path | None" = None,
    from_source: bool = False,
    no_cache: bool = False,
    allow_unverified: bool = False,
    config: "dict | None" = None,
    verbose: bool = False,
) -> ResolvedRuntime:
    """Resolve the runtime artifact for (target, variant) and return its path.

    Parameters
    ----------
    target:
        Target triple, e.g. ``"linux-x64"`` or ``"windows-x64"``.
    variant:
        Runtime variant, e.g. ``"cli"``.
    explicit_path:
        If provided, use this path directly without any resolution, integrity
        check, or caching.  Raises RuntimeNotFound if the path does not exist.
    from_source:
        Invoke build-runtime.sh instead of downloading.  Requires Docker.
    no_cache:
        Skip cache read and write; always download fresh.  If the download
        fails, hard-errors immediately (no in-tree fallback).
    allow_unverified:
        Permit running with a runtime binary that has no ``.sha256`` sidecar.
        The default refuses such binaries to avoid running unverified code.
        :envvar:`PICOLET_ALLOW_UNVERIFIED_CACHE` ``=1`` has the same effect.
    config:
        Pre-parsed picolet.toml data (or None).  Used to read [runtime] section.
    verbose:
        Emit progress messages to stderr.

    Returns
    -------
    ResolvedRuntime
        NamedTuple with binary (Path) and sbom (Path | None).

    Raises
    ------
    RuntimeNotFound
        When no artifact can be found through any path.
    RuntimeIntegrityError
        When a cached artifact fails integrity and cannot be repaired.
    """
    artifact = _artifact_name(target, variant)

    # -------------------------------------------------------------------------
    # Step 1 — explicit --runtime override.
    # -------------------------------------------------------------------------
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise RuntimeNotFound(
                f"--runtime path not found: {explicit_path}"
            )
        return ResolvedRuntime(binary=explicit_path.resolve(), sbom=None)

    # -------------------------------------------------------------------------
    # Step 2 — --from-source: invoke build-runtime.sh.
    # -------------------------------------------------------------------------
    if from_source:
        built = _build_from_source(target, variant, verbose)
        return ResolvedRuntime(binary=built, sbom=None)

    # -------------------------------------------------------------------------
    # Steps 3–5 (skipped when no_cache=True).
    # -------------------------------------------------------------------------
    cfg = _load_config(config)

    cache_path_str = str(cfg.cache_root / "runtime" / cfg.tag / artifact)
    download_url = f"{cfg.base_url}/{cfg.tag}/{artifact}"
    intree_path = _find_repo_root() / "packages" / "picolet-runtime" / "build" / artifact

    download_exc_str: str = ""

    if not no_cache:
        # Step 3 — cache lookup (SHA256-verified).
        cached = _check_cache(cfg, artifact, verbose, allow_unverified=allow_unverified)
        if cached is not None:
            return cached

        # Step 4 — download.
        try:
            return _download(cfg, artifact, verbose, allow_unverified=allow_unverified)
        except RuntimeDownloadError as exc:
            download_exc_str = str(exc)

        # Step 5 — in-tree build-output fallback.
        fallback = _intree_fallback(target, variant)
        if fallback is not None:
            print(
                f"warning: using in-tree build fallback (cache bypassed): {fallback}",
                file=sys.stderr,
            )
            return ResolvedRuntime(binary=fallback, sbom=None)

    else:
        # no_cache mode: skip cache and in-tree fallback; download only.
        try:
            return _download(cfg, artifact, verbose, allow_unverified=allow_unverified)
        except RuntimeDownloadError as exc:
            download_exc_str = str(exc)

    # -------------------------------------------------------------------------
    # Step 6 — hard error with structured three-option message.
    # -------------------------------------------------------------------------
    if no_cache:
        fallback_line = f"    fallback: skipped (--no-cache)"
    else:
        fallback_status = "(not found)" if not intree_path.is_file() else "(found but download failed)"
        fallback_line = f"    fallback: {intree_path} {fallback_status}"

    cache_status = "(not found)" if not (cfg.cache_root / "runtime" / cfg.tag / artifact).is_file() else "(integrity failure)"

    raise RuntimeNotFound(
        f"runtime artifact not available: {artifact}\n"
        f"\n"
        f"  Tried:\n"
        f"    cache:    {cache_path_str} {cache_status}\n"
        f"    download: {download_url}\n"
        f"              (connection failed: {download_exc_str})\n"
        f"{fallback_line}\n"
        f"\n"
        f"  To resolve:\n"
        f"    1. Connect to the network and re-run `picolet build`.\n"
        f"    2. Run `picolet build --from-source` to build the runtime locally (requires Docker).\n"
        f"    3. Run `picolet build --runtime /path/to/runtime` to use a specific binary."
    )


def locate_mpy_cross() -> Path:
    """Return the absolute path to the in-tree mpy-cross binary.

    The binary is built by build-runtime.sh step [3/8] inside the
    ubuntu:22.04 container and runs on the host because the host glibc
    is newer.

    PH05 does not distribute mpy-cross as a cached artifact. If the
    in-tree binary is absent and --from-source was not used, the build
    will error here. Use `picolet build --from-source` to build mpy-cross
    alongside the runtime (future phase: mpy-cross distribution).
    """
    mpy_cross = (
        _repo_root()
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
            f"  Build it with: picolet build --from-source\n"
            f"  Or directly:   ./packages/picolet-runtime/scripts/build-runtime.sh "
            f"--target linux-x64 --variant cli"
        )
    return mpy_cross.resolve()
