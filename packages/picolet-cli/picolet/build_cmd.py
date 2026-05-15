"""
picolet build — compile a picolet app into a single self-contained binary.

Usage:
    picolet build [--target {linux-x64,windows-x64}] [--verbose]
                [--keep-staging] [--runtime PATH]

Pipeline (FR-BP-1 through FR-BP-6):

  1. Read + validate picolet.toml (FR-CLI-8 pre-flight).
  2. Resolve runtime variant from [ui] (absent → cli) (FR-BP-1).
  3. Resolve target from --target or host auto-detection (FR-BP-1).
  4. Locate runtime artifact + mpy-cross, verify version match.
  5. Compile user .py sources → .mpy via mpy-cross (FR-BP-3).
  6. Copy [romfs] include dirs into staging (FR-BP-4).
  7. Zero mtimes for reproducibility (FR-BP-6).
  8. Build romfs image with mpremote (FR-BP-4).
  9. Append romfs + 24-byte trailer to runtime binary (FR-BP-5).
 10. Write executable to target/<target>/<app.name>[.exe] (FR-CLI-3).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from picolet.validator import validate_toml
from picolet._trailer import pack_trailer
from picolet.runtime_resolver import (
    locate_mpy_cross,
    resolve_runtime,
    ResolvedRuntime,
    RuntimeNotFound,
)


def add_parser(subparsers) -> None:
    """Register the build subcommand with the given subparsers object."""
    p = subparsers.add_parser(
        "build",
        help="build a picolet app into a single executable",
        description=(
            "Compile the current app's Python sources, build a romfs image, "
            "and append it to the pre-built runtime to produce a single binary."
        ),
    )
    p.add_argument(
        "--target",
        default=None,
        metavar="TARGET",
        help=(
            "build target (default: host; "
            "supported: linux-x64, windows-x64)"
        ),
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="print build steps to stderr",
    )
    p.add_argument(
        "--keep-staging",
        action="store_true",
        default=False,
        help="keep the staging directory after a successful build (for debugging)",
    )
    # Undocumented escape hatch — used by SQE to test alternate runtimes.
    p.add_argument(
        "--runtime",
        default=None,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--from-source",
        action="store_true",
        default=False,
        dest="from_source",
        help="build the runtime locally using build-runtime.sh (requires Docker)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        dest="no_cache",
        help="skip the runtime artifact cache; always download fresh",
    )
    p.set_defaults(func=run)


def run(args) -> None:
    """Entry point for `picolet build`."""
    # -------------------------------------------------------------------------
    # Step 1 – Find and validate picolet.toml.
    # -------------------------------------------------------------------------
    toml_path = _find_picolet_toml(Path.cwd())
    if toml_path is None:
        print(
            "error: picolet.toml not found in current directory or any ancestor",
            file=sys.stderr,
        )
        sys.exit(1)

    errors = validate_toml(toml_path)
    if errors:
        for e in errors:
            print(str(e), file=sys.stderr)
        sys.exit(1)

    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)

    app_name: str = data["app"]["name"]
    entry: str = data["app"]["entry"]            # e.g. "src/main.py"
    romfs_includes: list[str] = data.get("romfs", {}).get("include", [])
    app_root: Path = toml_path.parent

    # -------------------------------------------------------------------------
    # Step 2 – Resolve runtime variant (FR-BP-1).
    # -------------------------------------------------------------------------
    if "ui" not in data:
        variant = "cli"
    else:
        renderer = data["ui"].get("renderer", "")
        if renderer == "webview":
            # PH07: linux-x64 webview variant.  Windows webview is PH10.
            variant = "webview"
        elif renderer == "lvgl":
            raise NotImplementedError(
                "lvgl variant builds land in PH11; "
                "remove [ui] from picolet.toml to build a cli app"
            )
        else:
            # Validator already rejected invalid renderer values; this branch
            # is a belt-and-suspenders guard.
            raise NotImplementedError(
                f"unknown ui renderer {renderer!r}; "
                "valid values are 'webview' and 'lvgl' (PH09/PH11)"
            )

    # -------------------------------------------------------------------------
    # Step 3 – Resolve target (FR-BP-1).
    # -------------------------------------------------------------------------
    target = args.target if args.target else _host_target()

    SUPPORTED_TARGETS = {"linux-x64", "windows-x64"}
    if target not in SUPPORTED_TARGETS:
        raise NotImplementedError(
            f"--target {target!r} not implemented; "
            f"supported targets: {', '.join(sorted(SUPPORTED_TARGETS))}. "
            "webview targets land in PH09/PH10; lvgl in PH11/PH12."
        )

    if args.verbose:
        print(f"runtime variant: {variant}", file=sys.stderr)
        print(f"target: {target}", file=sys.stderr)

    # -------------------------------------------------------------------------
    # Step 4 – Locate runtime artifact and mpy-cross; verify version match.
    # -------------------------------------------------------------------------
    try:
        resolved: ResolvedRuntime = resolve_runtime(
            target,
            variant,
            explicit_path=Path(args.runtime) if args.runtime else None,
            from_source=args.from_source,
            no_cache=args.no_cache,
            config=data,
            verbose=args.verbose,
        )
    except RuntimeNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    runtime_path = resolved.binary
    # resolved.sbom is preserved for PH13's SBOM emitter; unused here.

    try:
        mpy_cross = locate_mpy_cross()
    except RuntimeNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    _verify_mpy_cross_version(runtime_path, mpy_cross, args.verbose)

    # -------------------------------------------------------------------------
    # Steps 5–9 in a temp staging area.
    # -------------------------------------------------------------------------
    staging = app_root / "target" / target / ".picolet-build"
    staging.mkdir(parents=True, exist_ok=True)

    if args.verbose:
        print(f"staging: {staging}", file=sys.stderr)

    try:
        # Step 5 – Compile .py → .mpy (FR-BP-3).
        romfs_root = staging / "romfs"
        _compile_mpy(app_root, entry, romfs_root, mpy_cross, args.verbose)

        # Step 6 – Copy [romfs] include dirs (FR-BP-4).
        _copy_includes(app_root, romfs_includes, romfs_root, args.verbose)

        # Step 6b – Webview variant: drop a sanitised picolet.toml at the
        # romfs root so the runtime can read [window] and [ui] at
        # startup (FR-WV-3).  The user does not need to add picolet.toml
        # to [romfs] include manually.
        if variant == "webview":
            _emit_webview_toml(data, romfs_root, args.verbose)
            # Step 6c – Copy the picolet-bridge-js bundle into the romfs
            # at picolet/picolet-bridge.js (FR-BP-4, FR-WV-4).  The runtime
            # reads it from /rom/picolet/picolet-bridge.js and injects it
            # at DOCUMENT_START so window.picolet is available to user JS.
            _copy_bridge_js(romfs_root, args.verbose)

        # Step 7 – Zero mtimes for reproducibility (FR-BP-6).
        _zero_mtimes(romfs_root)

        # Step 8 – Build romfs image with mpremote.
        romfs_img = staging / f"{app_name}.romfs"
        _build_romfs(romfs_root, romfs_img, args.verbose)

        # Step 9 – Append + trailer → final binary.
        output_dir = app_root / "target" / target
        output_path = output_dir / app_name
        if target == "windows-x64":
            output_path = output_path.with_suffix(".exe")
        _append_with_trailer(runtime_path, romfs_img, output_path, args.verbose)
        output_path.chmod(0o755)

    finally:
        if not args.keep_staging and staging.exists():
            shutil.rmtree(staging)

    print(f"Built {output_path}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_picolet_toml(start: Path) -> Path | None:
    """Walk up from start looking for picolet.toml; return its path or None."""
    candidate = start / "picolet.toml"
    if candidate.is_file():
        return candidate
    parent = start.parent
    if parent == start:
        return None
    return _find_picolet_toml(parent)


def _host_target() -> str:
    """Return the target string for the current host.

    WSL2 reports sys.platform == 'linux', so the host default on WSL is
    'linux-x64'.  Cross-compilation for Windows from WSL requires an explicit
    --target windows-x64.  The 'windows-x64' path here only triggers when
    running natively on Win32 CPython.
    """
    machine = platform.machine().lower()
    system = sys.platform
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "linux-x64"
    if system == "win32" and machine in ("x86_64", "amd64"):
        return "windows-x64"
    raise NotImplementedError(
        f"host auto-detection: unsupported platform {sys.platform}/{platform.machine()}; "
        "use --target to specify explicitly. "
        "Supported targets: linux-x64, windows-x64."
    )


def _verify_mpy_cross_version(
    runtime_path: Path, mpy_cross: Path, verbose: bool
) -> None:
    """Compare the .version sidecar against mpy-cross --version output.

    Exits with an error if they differ.  The sidecar is written by
    build-runtime.sh step [7b] and encodes the mpy bytecode format version
    (e.g. 'mpy v6.3').  Using a mismatched mpy-cross silently produces
    bytecode the runtime cannot load.
    """
    version_file = runtime_path.parent / f"{runtime_path.name}.version"
    if not version_file.is_file():
        # Sidecar absent (pre-PH03 runtime or --runtime override).
        # Warn and continue rather than hard-fail — the runtime may still work.
        if verbose:
            print(
                f"warning: no .version sidecar at {version_file}; "
                "skipping version check",
                file=sys.stderr,
            )
        return

    runtime_ver = version_file.read_text().strip()

    try:
        ver_output = subprocess.check_output(
            [str(mpy_cross), "--version"], text=True, stderr=subprocess.STDOUT
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(
            f"error: could not run mpy-cross at {mpy_cross}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    # mpy-cross --version outputs something like:
    #   "MicroPython v1.24.0 on 2025-01-01; mpy-cross emitting mpy v6.3"
    # Extract the "mpy v6.3" token to compare against the sidecar.
    m = re.search(r"mpy v[\d.]+", ver_output)
    mpy_ver = m.group(0) if m else ver_output

    if mpy_ver != runtime_ver:
        print(
            f"error: mpy-cross version mismatch\n"
            f"  mpy-cross reports: {mpy_ver}\n"
            f"  runtime expects:   {runtime_ver}\n"
            f"  Use the mpy-cross built alongside this runtime, or rebuild\n"
            f"  the runtime with build-runtime.sh --target {_guess_target(runtime_path)} "
            f"--variant {_guess_variant(runtime_path)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    if verbose:
        print(f"mpy-cross version: {mpy_ver} (matches runtime)", file=sys.stderr)


def _guess_target(runtime_path: Path) -> str:
    """Extract target from runtime artifact name."""
    name = runtime_path.stem  # e.g. picolet-runtime-linux-x64-cli
    parts = name.split("-")
    # picolet-runtime-linux-x64-cli → linux-x64
    if len(parts) >= 5:
        return f"{parts[2]}-{parts[3]}"
    return "linux-x64"


def _guess_variant(runtime_path: Path) -> str:
    """Extract variant from runtime artifact name."""
    name = runtime_path.stem
    parts = name.split("-")
    if len(parts) >= 5:
        return parts[4]
    return "cli"


def _copy_bridge_js(romfs_root: Path, verbose: bool) -> None:
    """Copy picolet-bridge.js into the romfs at picolet/picolet-bridge.js.

    The bundle is located relative to this module's package root so no
    Python package installation is needed for development (AD4).  The
    canonical source is packages/picolet-bridge-js/dist/picolet-bridge.js.

    Inside the frozen runtime the file is accessible at
    /rom/picolet/picolet-bridge.js.  _webview.py reads it at Webview
    construction time and injects it via webkit_user_script_new at
    DOCUMENT_START.
    """
    # Resolve relative to this file: build_cmd.py is in
    # packages/picolet-cli/picolet/; the bridge dist is at
    # packages/picolet-bridge-js/dist/picolet-bridge.js.
    here = Path(__file__).parent            # packages/picolet-cli/picolet/
    bridge_src = (
        here.parent.parent                  # packages/
        / "picolet-bridge-js"
        / "dist"
        / "picolet-bridge.js"
    )
    if not bridge_src.is_file():
        print(
            f"error: picolet-bridge.js not found at {bridge_src}; "
            "run: cd packages/picolet-bridge-js && node build.mjs",
            file=sys.stderr,
        )
        sys.exit(1)
    dest = romfs_root / "picolet" / "picolet-bridge.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bridge_src, dest)
    if verbose:
        print(
            f"  bridge: {bridge_src.name} → romfs/picolet/picolet-bridge.js "
            f"({bridge_src.stat().st_size} bytes)",
            file=sys.stderr,
        )


def _emit_webview_toml(
    data: dict, romfs_root: Path, verbose: bool
) -> None:
    """Write a sanitised picolet.toml into the romfs root for the runtime.

    The webview runtime reads /rom/picolet.toml at startup to apply
    [window] (title, size, resizable) and [ui] (root, index) — FR-WV-3
    and FR-WV-2.  Users do not need to add picolet.toml to [romfs] include
    themselves; we emit a minimal subset automatically.

    Only [window] and [ui] are emitted — host-only sections like [app],
    [build], [runtime] are deliberately dropped.  The runtime's
    picolet_ui._toml is a small subset reader; it tolerates extra keys
    but the surface area is minimal by design.
    """
    out_path = romfs_root / "picolet.toml"
    lines = []
    window = data.get("window") or {}
    if window:
        lines.append("[window]")
        if "title" in window:
            lines.append('title = "{}"'.format(_escape_toml_string(window["title"])))
        if "size" in window and isinstance(window["size"], list):
            sz = window["size"]
            if len(sz) == 2:
                lines.append("size = [{}, {}]".format(int(sz[0]), int(sz[1])))
        if "resizable" in window:
            lines.append("resizable = {}".format("true" if window["resizable"] else "false"))
        lines.append("")
    ui = data.get("ui") or {}
    if ui:
        lines.append("[ui]")
        if "renderer" in ui:
            lines.append('renderer = "{}"'.format(_escape_toml_string(ui["renderer"])))
        if "root" in ui:
            lines.append('root = "{}"'.format(_escape_toml_string(ui["root"])))
        if "index" in ui:
            lines.append('index = "{}"'.format(_escape_toml_string(ui["index"])))
        lines.append("")
    romfs_root.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    if verbose:
        print(
            f"  emitted webview picolet.toml at {out_path}",
            file=sys.stderr,
        )


def _escape_toml_string(s: str) -> str:
    """Minimal TOML string escape: backslash and double-quote."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _compile_mpy(
    app_root: Path,
    entry_str: str,
    romfs_root: Path,
    mpy_cross: Path,
    verbose: bool,
) -> None:
    """Compile all .py files under dirname(entry) → .mpy in romfs_root.

    Files are processed in sorted order for reproducibility (FR-BP-6).
    Output paths mirror the input tree relative to app_root.

    The entry point file is additionally compiled to romfs_root/main.mpy so
    the runtime's auto-run path (/rom/main.mpy) executes the app entry.

    e.g. entry = "src/main.py"  →  src_dir = app_root / "src"
         src/main.py            →  romfs_root/src/main.mpy
         src/main.py (entry)    →  romfs_root/main.mpy   (auto-run by runtime)
    """
    entry = Path(entry_str)
    entry_abs = app_root / entry
    src_dir = app_root / entry.parent  # e.g. app_root/"src"

    if not src_dir.is_dir():
        print(
            f"error: entry directory not found: {src_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    py_files = sorted(src_dir.rglob("*.py"))
    if not py_files:
        print(
            f"warning: no .py files found under {src_dir}",
            file=sys.stderr,
        )

    for py in py_files:
        rel = py.relative_to(app_root)          # e.g. src/main.py
        out_mpy = romfs_root / rel.with_suffix(".mpy")
        out_mpy.parent.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(f"  mpy-cross: {rel} → romfs/{rel.with_suffix('.mpy')}", file=sys.stderr)
        subprocess.run(
            [str(mpy_cross), "-o", str(out_mpy), str(py)],
            check=True,
            capture_output=not verbose,
        )

    # Compile the entry point to /rom/main.mpy (the runtime's auto-run location).
    # The runtime checks /rom/main.mpy on startup and executes it automatically.
    romfs_root.mkdir(parents=True, exist_ok=True)
    entry_main_mpy = romfs_root / "main.mpy"
    if verbose:
        print(f"  mpy-cross: {entry} → romfs/main.mpy (entry point)", file=sys.stderr)
    subprocess.run(
        [str(mpy_cross), "-o", str(entry_main_mpy), str(entry_abs)],
        check=True,
        capture_output=not verbose,
    )


def _copy_includes(
    app_root: Path,
    includes: list[str],
    romfs_root: Path,
    verbose: bool,
) -> None:
    """Copy [romfs] include directories into romfs_root (FR-BP-4).

    Files are copied preserving their relative path within each include dir.
    Destination paths mirror the source tree rooted at romfs_root.
    """
    for inc in includes:
        src = app_root / inc
        if not src.is_dir():
            print(
                f"error: [romfs] include directory not found: {src}",
                file=sys.stderr,
            )
            sys.exit(1)
        for f in sorted(src.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(app_root)
            dst = romfs_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if verbose:
                print(f"  include: {rel} → romfs/{rel}", file=sys.stderr)
            shutil.copy2(f, dst)


def _zero_mtimes(root: Path) -> None:
    """Recursively set all file mtimes to epoch 0 for reproducibility (FR-BP-6).

    mpremote romfs build embeds file mtimes in the romfs directory entries.
    Setting all mtimes to 0 ensures byte-identical romfs images for identical
    inputs regardless of when the build is run.
    """
    for item in root.rglob("*"):
        if item.is_file() or item.is_dir():
            os.utime(item, (0, 0))


def _build_romfs(romfs_root: Path, output: Path, verbose: bool) -> None:
    """Invoke mpremote to build a romfs image from romfs_root.

    mpremote romfs --output <output> build <dir>
    """
    if verbose:
        print(f"  mpremote romfs build → {output}", file=sys.stderr)
    subprocess.run(
        [
            sys.executable,
            "-m", "mpremote",
            "romfs",
            "--output", str(output),
            "build", str(romfs_root),
        ],
        check=True,
        capture_output=not verbose,
    )


def _append_with_trailer(
    runtime_path: Path,
    romfs_path: Path,
    out_path: Path,
    verbose: bool,
) -> None:
    """Concatenate runtime + romfs payload + 24-byte trailer → out_path.

    Writes to a temporary path first, then renames atomically (or falls back
    to shutil.move on cross-filesystem writes) to avoid partial outputs.

    Layout (FR-BP-5):
        [ELF runtime bytes][romfs payload N bytes][trailer 24 bytes]
    """
    runtime = runtime_path.read_bytes()
    payload = romfs_path.read_bytes()
    trailer = pack_trailer(payload)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.parent / f".{out_path.name}.tmp"

    with open(tmp_path, "wb") as f:
        f.write(runtime)
        f.write(payload)
        f.write(trailer)

    # Atomic rename; falls back to copy+unlink on cross-filesystem writes.
    try:
        tmp_path.rename(out_path)
    except OSError:
        shutil.move(str(tmp_path), out_path)

    if verbose:
        total = len(runtime) + len(payload) + len(trailer)
        print(
            f"  binary: {out_path}  "
            f"({len(runtime)} + {len(payload)} + {len(trailer)} = {total} bytes)",
            file=sys.stderr,
        )


