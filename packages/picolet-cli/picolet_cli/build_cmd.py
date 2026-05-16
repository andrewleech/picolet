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
 10. Emit SBOM sibling .cdx.json (FR-SBOM-1, FR-SBOM-2, FR-SBOM-3).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from picolet_cli._paths import find_picolet_toml as _find_picolet_toml
from picolet_cli._targets import (
    SUPPORTED_RENDERERS,
    SUPPORTED_TARGETS,
    TARGET_WINDOWS_X64,
    VARIANT_CLI,
    VARIANT_LVGL,
    VARIANT_WEBVIEW,
    host_target,
    target_exe_suffix,
    variant_for_renderer,
)
from picolet_cli._trailer import pack_trailer
from picolet_cli.runtime_resolver import (
    locate_mpy_cross,
    resolve_runtime,
    ResolvedRuntime,
    RuntimeIntegrityError,
    RuntimeNotFound,
)
from picolet_cli.sbom_gen import emit_app_sbom, SbomViolation
from picolet_cli.validator import validate_toml


class BuildFailed(Exception):
    """Raised by build helpers to abort the build with a structured error.

    The error message (if any) is expected to have been printed to stderr
    before raising. ``run()`` catches this and converts it into an exit
    code; callers (``dev_cmd``, ``run_cmd``) can also catch it to keep a
    long-lived process alive across a failed build.
    """


def build_args_namespace(target, verbose, **overrides) -> argparse.Namespace:
    """Return a :class:`argparse.Namespace` suitable for :func:`run`.

    Provides the minimal set of attributes that :func:`run` / ``_do_build``
    read.  ``target`` and ``verbose`` are the two most commonly varied
    fields; all others default to False/None but may be overridden via
    keyword arguments.

    Used by ``run_cmd`` and ``dev_cmd`` to synthesise build arguments from
    their own parsed args without duplicating the field list in each caller.
    """
    defaults = dict(
        target=target,
        verbose=verbose,
        keep_staging=False,
        runtime=None,
        from_source=False,
        no_cache=False,
        no_sbom=False,
        allow_unverified_runtime=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


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
    p.add_argument(
        "--allow-unverified-runtime",
        action="store_true",
        default=False,
        dest="allow_unverified_runtime",
        help=(
            "run with a runtime binary that has no .sha256 sidecar "
            "(escape hatch for air-gapped mirrors; equivalent to "
            "PICOLET_ALLOW_UNVERIFIED_CACHE=1)"
        ),
    )
    p.add_argument(
        "--no-sbom",
        action="store_true",
        default=False,
        dest="no_sbom",
        help="skip SBOM emission (for tests that do not need the .cdx.json side-effect)",
    )
    p.set_defaults(func=run)


def run(args) -> int:
    """Entry point for `picolet build`. Returns the exit code (0 on success)."""
    try:
        return _do_build(args)
    except BuildFailed as exc:
        if str(exc):
            print(f"error: {exc}", file=sys.stderr)
        return 1


def _do_build(args) -> int:
    # -------------------------------------------------------------------------
    # Step 1 – Find and validate picolet.toml.
    # -------------------------------------------------------------------------
    toml_path = _find_picolet_toml(Path.cwd())
    if toml_path is None:
        print(
            "error: picolet.toml not found in current directory or any ancestor",
            file=sys.stderr,
        )
        return 1

    _all_validation = validate_toml(toml_path)
    _hard_errors = [e for e in _all_validation if e.level != "warn"]
    for e in _all_validation:
        if e.level == "warn":
            print(str(e), file=sys.stderr)
    if _hard_errors:
        for e in _hard_errors:
            print(str(e), file=sys.stderr)
        return 1

    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)

    app_name: str = data["app"]["name"]
    entry: str = data["app"]["entry"]            # e.g. "src/main.py"
    romfs_includes: list[str] = data.get("romfs", {}).get("include", [])
    app_root: Path = toml_path.parent

    # -------------------------------------------------------------------------
    # Step 2 – Resolve runtime variant (FR-BP-1).
    # -------------------------------------------------------------------------
    renderer = data.get("ui", {}).get("renderer") if "ui" in data else None
    try:
        variant = variant_for_renderer(renderer)
    except ValueError:
        # Validator already rejected invalid renderer values; this is a
        # belt-and-suspenders guard.
        raise NotImplementedError(
            f"unknown ui renderer {renderer!r}; "
            f"valid values are {sorted(SUPPORTED_RENDERERS)}"
        )

    # -------------------------------------------------------------------------
    # Step 3 – Resolve target (FR-BP-1).
    # -------------------------------------------------------------------------
    target = args.target if args.target else host_target()

    if target not in SUPPORTED_TARGETS:
        raise NotImplementedError(
            f"--target {target!r} not implemented; "
            f"supported targets: {', '.join(sorted(SUPPORTED_TARGETS))}."
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
            allow_unverified=getattr(args, "allow_unverified_runtime", False),
            config=data,
            verbose=args.verbose,
        )
    except (RuntimeNotFound, RuntimeIntegrityError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    runtime_path = resolved.binary
    # resolved.sbom is preserved for PH13's SBOM emitter; unused here.

    try:
        mpy_cross = locate_mpy_cross()
    except RuntimeNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _verify_mpy_cross_version(runtime_path, mpy_cross, args.verbose)

    # -------------------------------------------------------------------------
    # Step 4b – Frontend build (FR-VUE-4, FR-VUE-5): run npm install + build
    # command when [ui.frontend].framework is non-vanilla.  No-op for vanilla.
    # -------------------------------------------------------------------------
    _run_frontend_build(data, app_root, args.verbose)

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

        # Step 6a – For non-vanilla frontend frameworks, copy the built
        # dist/ into romfs at the [ui] root.  Vanilla apps include their
        # static files via [romfs] include = ["ui"] — Vue apps omit that
        # entry and rely on this step instead (FR-VUE-4).
        _copy_dist_to_ui_root(data, app_root, romfs_root, args.verbose)

        # Step 6b – UI variants: drop a sanitised picolet.toml at the
        # romfs root so the runtime can read [window] and [ui] at
        # startup (FR-WV-3 webview, FR-LV-2 lvgl).  The user does not
        # need to add picolet.toml to [romfs] include manually.
        if variant in (VARIANT_WEBVIEW, VARIANT_LVGL):
            _emit_webview_toml(data, romfs_root, args.verbose)
        if variant == VARIANT_WEBVIEW:
            # Step 6c – Copy the picolet-bridge-js bundle into the romfs
            # at picolet/picolet-bridge.js (FR-BP-4, FR-WV-4).  The runtime
            # reads it from /rom/picolet/picolet-bridge.js and injects it
            # at DOCUMENT_START so window.picolet is available to user JS.
            _copy_bridge_js(romfs_root, args.verbose)
            # Step 6d – Windows-x64: copy WebView2Loader.dll into the
            # romfs at picolet/WebView2Loader.dll (PH10).  The runtime
            # extracts it to %LOCALAPPDATA%\picolet\<pid>\ at first use
            # and LoadLibraryW's it from there (the loader DLL is not
            # in System32, so the search-path-based default load is
            # unreliable).
            if target == TARGET_WINDOWS_X64:
                _copy_webview2_loader(romfs_root, args.verbose)

        # Step 7 – Zero mtimes for reproducibility (FR-BP-6).
        _zero_mtimes(romfs_root)

        # Step 8 – Build romfs image with mpremote.
        romfs_img = staging / f"{app_name}.romfs"
        _build_romfs(romfs_root, romfs_img, args.verbose)

        # Step 9 – Append + trailer → final binary.
        output_dir = app_root / "target" / target
        output_path = output_dir / (app_name + target_exe_suffix(target))
        _append_with_trailer(runtime_path, romfs_img, output_path, args.verbose)
        output_path.chmod(0o755)

    finally:
        if not args.keep_staging and staging.exists():
            shutil.rmtree(staging)

    # Step 10 – Emit SBOM (FR-SBOM-1, FR-SBOM-2, FR-SBOM-3).
    if not args.no_sbom:
        sbom_path = output_path.parent / f"{output_path.name}.cdx.json"
        if args.verbose:
            print(f"  sbom: emitting {sbom_path}", file=sys.stderr)
        violations = emit_app_sbom(
            output_path=sbom_path,
            runtime_sbom_path=resolved.sbom,
            app_data=data,
            target=target,
            variant=variant,
            repo_root=_find_repo_root(),
        )
        _handle_sbom_violations(violations, data, args.verbose)
        if args.verbose:
            print(f"  sbom: written {sbom_path}", file=sys.stderr)

    # flush=True so callers consuming stdout in real time (notably
    # `picolet dev`, which now invokes build in-process) see this without
    # waiting for the process to exit.
    print(f"Built {output_path}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    """Return the repository root (three levels up from this file).

    This file lives at packages/picolet-cli/picolet_cli/build_cmd.py.
    """
    here = Path(__file__).parent      # packages/picolet-cli/picolet_cli/
    return here.parent.parent.parent  # repo root


def _handle_sbom_violations(
    violations: list[SbomViolation],
    app_data: dict,
    verbose: bool,
) -> None:
    """Print warnings and exit 1 on policy failures.

    The SBOM file is always written before this is called, so downstream
    tooling can inspect the document even when the build fails.
    """
    if not violations:
        return

    has_fail = any(v.severity == "fail" for v in violations)

    for v in violations:
        if v.severity == "fail":
            print(
                f"error: sbom policy violation in {v.component!r}: {v.reason}",
                file=sys.stderr,
            )
        else:
            print(
                f"warn: sbom policy: {v.component!r}: {v.reason}",
                file=sys.stderr,
            )

    if has_fail:
        print(
            "error: sbom policy — build failed due to licence policy violations; "
            "see [sbom] allow_licences / allow_dynamic / fail_unknown in picolet.toml",
            file=sys.stderr,
        )
        raise BuildFailed()


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
        raise BuildFailed()

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
        raise BuildFailed()

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


def _run_frontend_build(data: dict, app_root: Path, verbose: bool) -> None:
    """Run npm install + the configured build command for non-vanilla frontends.

    Called after runtime resolution (step 4) and before mpy-cross compilation
    (step 5) so the dist/ output is available when _copy_dist_to_ui_root runs.

    No-op when [ui.frontend].framework is absent or "vanilla".

    Raises BuildFailed when:
      - npm is not on PATH (Node ≥ 18 LTS required for Vue projects).
      - npm install exits non-zero.
      - The build command exits non-zero.
    """
    frontend = data.get("ui", {}).get("frontend", {})
    framework = frontend.get("framework", "vanilla")
    if framework == "vanilla":
        return

    if shutil.which("npm") is None:
        print(
            "error: npm not found on PATH; Node ≥ 18 LTS is required for Vue projects "
            "(see docs/architecture.md §Frontend toolchains)",
            file=sys.stderr,
        )
        raise BuildFailed()

    if verbose:
        print(f"  frontend: framework={framework!r}; running npm install …", file=sys.stderr)

    # npm install --prefer-offline: respects package-lock.json when present;
    # fast when node_modules/ already exists (D2).
    subprocess.run(
        ["npm", "install", "--prefer-offline", "--no-fund", "--no-audit"],
        cwd=str(app_root),
        check=True,
        capture_output=not verbose,
    )

    build_cmd_str = frontend.get("build_cmd", "npm run build")
    if verbose:
        print(f"  frontend: running {build_cmd_str!r} in {app_root}", file=sys.stderr)

    try:
        subprocess.run(
            shlex.split(build_cmd_str),
            cwd=str(app_root),
            check=True,
            capture_output=not verbose,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"error: frontend build command {build_cmd_str!r} failed (rc={exc.returncode})",
            file=sys.stderr,
        )
        raise BuildFailed()


def _copy_dist_to_ui_root(
    data: dict, app_root: Path, romfs_root: Path, verbose: bool
) -> None:
    """Copy the frontend build dist/ into romfs at [ui] root.

    No-op when [ui.frontend].framework is absent or "vanilla".

    The dist/ contents are merged into romfs_root/<ui_root>/ using
    shutil.copytree with dirs_exist_ok=True so any existing files from a
    prior step are not clobbered.

    Raises BuildFailed when dist_dir does not exist (frontend build must
    have run first via _run_frontend_build).
    """
    frontend = data.get("ui", {}).get("frontend", {})
    framework = frontend.get("framework", "vanilla")
    if framework == "vanilla":
        return

    dist_dir = frontend.get("dist_dir", "dist")
    ui_root = data.get("ui", {}).get("root", "ui")

    src = app_root / dist_dir
    if not src.is_dir():
        print(
            f"error: frontend dist directory not found: {src}; "
            f"the build command did not produce expected output",
            file=sys.stderr,
        )
        raise BuildFailed()

    dst = romfs_root / ui_root
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    if verbose:
        count = sum(1 for _ in dst.rglob("*") if _.is_file())
        print(
            f"  dist: copied {count} files from {src} → romfs/{ui_root}/",
            file=sys.stderr,
        )


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
    # packages/picolet-cli/picolet_cli/; the bridge dist is at
    # packages/picolet-bridge-js/dist/picolet-bridge.js.
    here = Path(__file__).parent            # packages/picolet-cli/picolet_cli/
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
        raise BuildFailed()
    dest = romfs_root / "picolet" / "picolet-bridge.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bridge_src, dest)
    if verbose:
        print(
            f"  bridge: {bridge_src.name} → romfs/picolet/picolet-bridge.js "
            f"({bridge_src.stat().st_size} bytes)",
            file=sys.stderr,
        )


def _copy_webview2_loader(romfs_root: Path, verbose: bool) -> None:
    """Copy WebView2Loader.dll into the romfs at picolet/WebView2Loader.dll.

    PH10.  The runtime needs the loader DLL to LoadLibraryW it at
    startup; bundling inside the romfs (not the runtime's empty-default
    romfs) is AD1's load-deterministic distribution.

    Resolution order:
      1. Environment variable PICOLET_WEBVIEW2_LOADER_DLL (escape hatch
         for CI / hosts with a system-installed loader).
      2. packages/picolet-runtime/overlay/ports/windows/modules/picolet_webview2/
         redist/WebView2Loader.x64.dll  (vendored, dev path).

    Errors with a clear message + fetch instructions when neither
    source is present.
    """
    import os

    dest = romfs_root / "picolet" / "WebView2Loader.dll"
    dest.parent.mkdir(parents=True, exist_ok=True)

    env_path = os.environ.get("PICOLET_WEBVIEW2_LOADER_DLL")
    sources = []
    if env_path:
        sources.append(Path(env_path))

    # Repo-relative dev path: ../picolet-runtime/overlay/ports/windows/
    # variants/picolet-webview/redist/WebView2Loader.x64.dll
    here = Path(__file__).parent
    repo_dev = (
        here.parent.parent
        / "picolet-runtime" / "overlay" / "ports" / "windows"
        / "variants" / "picolet-webview" / "redist"
        / "WebView2Loader.x64.dll"
    )
    sources.append(repo_dev)

    for src in sources:
        if src.is_file():
            shutil.copy2(src, dest)
            if verbose:
                print(
                    f"  loader: {src.name} -> romfs/picolet/WebView2Loader.dll "
                    f"({src.stat().st_size} bytes)",
                    file=sys.stderr,
                )
            return

    print(
        "error: WebView2Loader.dll not found in any of:\n"
        + "\n".join(f"  {s}" for s in sources)
        + "\n\n"
        "Obtain the loader DLL from the Microsoft Edge WebView2 SDK:\n"
        "  nuget install Microsoft.Web.WebView2 -Version 1.0.2210.55\n"
        "and place build/native/x64/WebView2Loader.dll at:\n"
        f"  {repo_dev}\n"
        "Or point PICOLET_WEBVIEW2_LOADER_DLL at a copy on disk.",
        file=sys.stderr,
    )
    raise BuildFailed()


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
        raise BuildFailed()

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
            raise BuildFailed()
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


