#!/usr/bin/env bash
# tests/phase-14/run.sh — PH14 exit gate verification harness.
#
# Covers: FR-CLI-2 for all three templates (hello-cli, hello-webview, hello-lvgl).
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-14/run.sh [--skip-integration] [--skip-windows]
#                               [--skip-regression] [--verbose]
#
# Flags:
#   --skip-integration   Skip xvfb (webview + lvgl headed tests).
#   --skip-windows       Skip windows-x64 build + run tests.
#   --skip-regression    Skip PH13 regression check.
#   --verbose            Print extra output.
#
# Prerequisites:
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-{cli,webview,lvgl}
#   - packages/picolet-runtime/build/picolet-runtime-windows-x64-{cli,webview,lvgl}.exe
#   - xvfb-run on PATH (for integration gates)
#   - uv on PATH
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
BUILD="$PKG_ROOT/build"

CLI_RUNTIME_LINUX="$BUILD/picolet-runtime-linux-x64-cli"
WEBVIEW_RUNTIME_LINUX="$BUILD/picolet-runtime-linux-x64-webview"
LVGL_RUNTIME_LINUX="$BUILD/picolet-runtime-linux-x64-lvgl"
CLI_RUNTIME_WIN="$BUILD/picolet-runtime-windows-x64-cli.exe"
WEBVIEW_RUNTIME_WIN="$BUILD/picolet-runtime-windows-x64-webview.exe"
LVGL_RUNTIME_WIN="$BUILD/picolet-runtime-windows-x64-lvgl.exe"

SKIP_INTEGRATION=0
SKIP_WINDOWS=0
SKIP_REGRESSION=0
VERBOSE=0

for arg in "$@"; do
    case "$arg" in
        --skip-integration) SKIP_INTEGRATION=1 ;;
        --skip-windows)     SKIP_WINDOWS=1 ;;
        --skip-regression)  SKIP_REGRESSION=1 ;;
        --verbose)          VERBOSE=1 ;;
        --help|-h)
            grep '^#' "$0" | cut -c3-
            exit 0 ;;
        *)
            echo "error: unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

PASS=0
FAIL=0
SKIP=0
FAILED_GATES=()

pass()  { printf "  PASS  %s\n" "$1"; PASS=$((PASS + 1)); }
fail()  {
    printf "  FAIL  %s\n" "$1"
    if [[ -n "${2:-}" ]]; then printf "        %s\n" "$2"; fi
    FAIL=$((FAIL + 1))
    FAILED_GATES+=("$1")
}
skip()  {
    printf "  SKIP  %s" "$1"
    if [[ -n "${2:-}" ]]; then printf "  (%s)" "$2"; fi
    printf "\n"
    SKIP=$((SKIP + 1))
}

WORKDIR="/tmp/picolet-ph14-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH14 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Helper: run picolet init
# ---------------------------------------------------------------------------
_picolet_init() {
    local name="$1"
    local template="$2"
    local outdir="$3"
    (
        cd "$REPO_ROOT" && \
        uv run python -m picolet init "$name" \
            --template "$template" \
            --output-dir "$outdir" \
            > "$WORKDIR/init-${template}.log" 2>&1
    )
}

# ---------------------------------------------------------------------------
# Helper: run picolet build (linux)
# ---------------------------------------------------------------------------
_picolet_build_linux() {
    local appdir="$1"
    local runtime="$2"
    local logfile="$3"
    (
        cd "$appdir" && \
        uv run --project "$REPO_ROOT" python -m picolet build \
            --target linux-x64 \
            --runtime "$runtime" \
            --no-sbom \
            > "$logfile" 2>&1
    )
}

# ---------------------------------------------------------------------------
# Helper: run picolet build (windows)
# ---------------------------------------------------------------------------
_picolet_build_win() {
    local appdir="$1"
    local runtime="$2"
    local logfile="$3"
    (
        cd "$appdir" && \
        uv run --project "$REPO_ROOT" python -m picolet build \
            --target windows-x64 \
            --runtime "$runtime" \
            --no-sbom \
            > "$logfile" 2>&1
    )
}

# ---------------------------------------------------------------------------
# Check prerequisites
# ---------------------------------------------------------------------------
HAVE_UV=0
command -v uv >/dev/null 2>&1 && HAVE_UV=1

HAVE_XVFB=0
command -v xvfb-run >/dev/null 2>&1 && HAVE_XVFB=1

# ---------------------------------------------------------------------------
# Group A: scaffold — all three templates
# ---------------------------------------------------------------------------
echo "--- Group A: scaffold ---"

for TEMPLATE in hello-cli hello-webview hello-lvgl; do
    OUTDIR="$WORKDIR/scaffold-$TEMPLATE"

    NAME="A-scaffold-$TEMPLATE"
    if [[ "$HAVE_UV" -eq 0 ]]; then
        skip "$NAME" "uv not on PATH"
        continue
    fi

    if _picolet_init "test-app" "$TEMPLATE" "$OUTDIR" 2>&1; then
        # Verify picolet.toml and src/main.py exist
        if [[ -f "$OUTDIR/picolet.toml" && -f "$OUTDIR/src/main.py" ]]; then
            pass "$NAME"
        else
            fail "$NAME" "missing expected files; see $WORKDIR/init-${TEMPLATE}.log"
            if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/init-${TEMPLATE}.log"; fi
        fi
    else
        fail "$NAME" "picolet init exited non-zero; see $WORKDIR/init-${TEMPLATE}.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/init-${TEMPLATE}.log"; fi
    fi

    NAME="A-name-substituted-$TEMPLATE"
    if [[ ! -f "$OUTDIR/picolet.toml" ]]; then
        skip "$NAME" "scaffold failed"
    elif grep -q 'name = "test-app"' "$OUTDIR/picolet.toml" && ! grep -q '{{name}}' "$OUTDIR/picolet.toml"; then
        pass "$NAME"
    else
        fail "$NAME" "{{name}} not substituted in picolet.toml"
    fi

    NAME="A-no-literal-placeholder-$TEMPLATE"
    if [[ ! -d "$OUTDIR" ]]; then
        skip "$NAME" "scaffold failed"
    else
        if grep -r '{{name}}' "$OUTDIR" --include="*.py" --include="*.toml" --include="*.html" --include="*.js" --include="*.css" 2>/dev/null | grep -q '{{name}}'; then
            fail "$NAME" "literal {{name}} found in scaffolded files"
        else
            pass "$NAME"
        fi
    fi
done

# hello-webview specific: check ui/ files exist
NAME="A-webview-ui-files"
if [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ -d "$WORKDIR/scaffold-hello-webview" ]]; then
    WVDIR="$WORKDIR/scaffold-hello-webview"
    if [[ -f "$WVDIR/ui/index.html" && -f "$WVDIR/ui/app.js" && -f "$WVDIR/ui/style.css" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "hello-webview scaffold missing ui/ files"
    fi
else
    skip "$NAME" "hello-webview scaffold not present"
fi

# hello-lvgl specific: check [ui] renderer = "lvgl" in produced picolet.toml
NAME="A-lvgl-renderer-key"
if [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ -f "$WORKDIR/scaffold-hello-lvgl/picolet.toml" ]]; then
    if grep -q 'renderer = "lvgl"' "$WORKDIR/scaffold-hello-lvgl/picolet.toml"; then
        pass "$NAME"
    else
        fail "$NAME" 'renderer = "lvgl" not found in hello-lvgl picolet.toml'
    fi
else
    skip "$NAME" "hello-lvgl scaffold not present"
fi

# Reject unknown template
NAME="A-reject-unknown-template"
if [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
else
    OUT="$(
        cd "$REPO_ROOT" && \
        uv run python -m picolet init neg --template no-such-template \
            --output-dir "$WORKDIR/neg-template" 2>&1
    )" || true
    if echo "$OUT" | grep -q "unknown template"; then
        pass "$NAME"
    else
        fail "$NAME" "expected 'unknown template' error; got: $OUT"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group B: hello-cli — linux build + run
# ---------------------------------------------------------------------------
echo "--- Group B: hello-cli build + run ---"

CLI_APPDIR="$WORKDIR/scaffold-hello-cli"
CLI_BINARY="$CLI_APPDIR/target/linux-x64/test-app"

NAME="B-cli-linux-build"
if [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ ! -f "$CLI_RUNTIME_LINUX" ]]; then
    skip "$NAME" "linux-x64 cli runtime not found"
elif [[ ! -d "$CLI_APPDIR" ]]; then
    skip "$NAME" "hello-cli scaffold not present (A-scaffold-hello-cli failed)"
else
    if _picolet_build_linux "$CLI_APPDIR" "$CLI_RUNTIME_LINUX" "$WORKDIR/b-cli-linux-build.log"; then
        if [[ -f "$CLI_BINARY" ]]; then
            pass "$NAME"
        else
            fail "$NAME" "build succeeded but binary not found at $CLI_BINARY"
        fi
    else
        fail "$NAME" "picolet build exited non-zero; see $WORKDIR/b-cli-linux-build.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/b-cli-linux-build.log"; fi
    fi
fi

NAME="B-cli-linux-run"
if [[ ! -f "$CLI_BINARY" ]]; then
    skip "$NAME" "cli linux binary not present (B-cli-linux-build failed)"
else
    RUN_OUT="$("$CLI_BINARY" 2>&1 || true)"
    if echo "$RUN_OUT" | grep -q "Hello from test-app"; then
        pass "$NAME"
    else
        fail "$NAME" "binary did not print 'Hello from test-app'; got: $RUN_OUT"
    fi
fi

# Windows cli build + run
NAME="B-cli-windows-build"
if [[ "$SKIP_WINDOWS" -eq 1 ]]; then
    skip "$NAME" "--skip-windows"
elif [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ ! -f "$CLI_RUNTIME_WIN" ]]; then
    skip "$NAME" "windows-x64 cli runtime not found"
elif [[ ! -d "$CLI_APPDIR" ]]; then
    skip "$NAME" "hello-cli scaffold not present"
else
    WIN_CLI_BINARY="$CLI_APPDIR/target/windows-x64/test-app.exe"
    if _picolet_build_win "$CLI_APPDIR" "$CLI_RUNTIME_WIN" "$WORKDIR/b-cli-win-build.log"; then
        if [[ -f "$WIN_CLI_BINARY" ]]; then
            pass "$NAME"
        else
            fail "$NAME" "build succeeded but binary not found at $WIN_CLI_BINARY"
        fi
    else
        fail "$NAME" "windows build exited non-zero; see $WORKDIR/b-cli-win-build.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/b-cli-win-build.log"; fi
    fi
fi

NAME="B-cli-windows-run"
WIN_CLI_BINARY="$CLI_APPDIR/target/windows-x64/test-app.exe"
if [[ "$SKIP_WINDOWS" -eq 1 ]]; then
    skip "$NAME" "--skip-windows"
elif [[ ! -f "$WIN_CLI_BINARY" ]]; then
    skip "$NAME" "windows cli binary not present (B-cli-windows-build failed)"
else
    WIN_OUT="$("$WIN_CLI_BINARY" 2>&1 || true)"
    if echo "$WIN_OUT" | grep -q "Hello from test-app"; then
        pass "$NAME"
    else
        fail "$NAME" "windows binary did not print 'Hello from test-app'; got: $WIN_OUT"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group C: hello-webview — linux build + romfs check + xvfb run
# ---------------------------------------------------------------------------
echo "--- Group C: hello-webview build + run ---"

WV_APPDIR="$WORKDIR/scaffold-hello-webview"
WV_BINARY="$WV_APPDIR/target/linux-x64/test-app"

NAME="C-webview-linux-build"
if [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ ! -f "$WEBVIEW_RUNTIME_LINUX" ]]; then
    skip "$NAME" "linux-x64 webview runtime not found"
elif [[ ! -d "$WV_APPDIR" ]]; then
    skip "$NAME" "hello-webview scaffold not present"
else
    if _picolet_build_linux "$WV_APPDIR" "$WEBVIEW_RUNTIME_LINUX" "$WORKDIR/c-wv-build.log"; then
        if [[ -f "$WV_BINARY" ]]; then
            pass "$NAME"
        else
            fail "$NAME" "build succeeded but binary not found at $WV_BINARY"
        fi
    else
        fail "$NAME" "picolet build exited non-zero; see $WORKDIR/c-wv-build.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/c-wv-build.log"; fi
    fi
fi

NAME="C-webview-romfs-picolet-toml"
if [[ ! -f "$WV_BINARY" ]]; then
    skip "$NAME" "webview binary not present"
else
    TOML_OUT="$("$WV_BINARY" -c 'print(open("/rom/picolet.toml").read())' 2>&1 || true)"
    if echo "$TOML_OUT" | grep -q '\[window\]'; then
        pass "$NAME"
    else
        fail "$NAME" "picolet.toml not in romfs; got: $TOML_OUT"
    fi
fi

NAME="C-webview-romfs-index-html"
if [[ ! -f "$WV_BINARY" ]]; then
    skip "$NAME" "webview binary not present"
else
    HTML_OUT="$("$WV_BINARY" -c 'print(open("/rom/ui/index.html").read())' 2>&1 || true)"
    if echo "$HTML_OUT" | grep -qi 'btn-greet\|test-app'; then
        pass "$NAME"
    else
        fail "$NAME" "ui/index.html not in romfs; got: $HTML_OUT"
    fi
fi

NAME="C-webview-linux-xvfb-run"
if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "$NAME" "--skip-integration"
elif [[ "$HAVE_XVFB" -eq 0 ]]; then
    skip "$NAME" "xvfb-run not on PATH"
elif [[ ! -f "$WV_BINARY" ]]; then
    skip "$NAME" "webview binary not present"
else
    # The webview app does not print a sentinel; just verify it starts and
    # exits within 5 seconds without a crash (exit code from timeout is 124
    # when it kills the process, which is fine — the app is a GUI loop).
    xvfb-run -a -s '-screen 0 800x600x24' \
        timeout 5 "$WV_BINARY" \
        > "$WORKDIR/c-wv-run.log" 2>&1 || XVFB_RC=$?
    XVFB_RC="${XVFB_RC:-0}"
    # exit 124 = timeout killed (expected for a GUI app)
    # exit 0   = app exited cleanly (also fine)
    if [[ "$XVFB_RC" -eq 0 || "$XVFB_RC" -eq 124 ]]; then
        pass "$NAME"
    else
        fail "$NAME" "webview exited with unexpected code $XVFB_RC; see $WORKDIR/c-wv-run.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/c-wv-run.log"; fi
    fi
fi

# Windows webview build
NAME="C-webview-windows-build"
if [[ "$SKIP_WINDOWS" -eq 1 ]]; then
    skip "$NAME" "--skip-windows"
elif [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ ! -f "$WEBVIEW_RUNTIME_WIN" ]]; then
    skip "$NAME" "windows-x64 webview runtime not found"
elif [[ ! -d "$WV_APPDIR" ]]; then
    skip "$NAME" "hello-webview scaffold not present"
else
    WIN_WV_BINARY="$WV_APPDIR/target/windows-x64/test-app.exe"
    if _picolet_build_win "$WV_APPDIR" "$WEBVIEW_RUNTIME_WIN" "$WORKDIR/c-wv-win-build.log"; then
        if [[ -f "$WIN_WV_BINARY" ]]; then
            pass "$NAME"
        else
            fail "$NAME" "build succeeded but windows binary not found"
        fi
    else
        fail "$NAME" "windows webview build exited non-zero; see $WORKDIR/c-wv-win-build.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/c-wv-win-build.log"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group D: hello-lvgl — linux build + xvfb run; windows build-only
# ---------------------------------------------------------------------------
echo "--- Group D: hello-lvgl build + run ---"

LV_APPDIR="$WORKDIR/scaffold-hello-lvgl"
LV_BINARY="$LV_APPDIR/target/linux-x64/test-app"

NAME="D-lvgl-linux-build"
if [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ ! -f "$LVGL_RUNTIME_LINUX" ]]; then
    skip "$NAME" "linux-x64 lvgl runtime not found"
elif [[ ! -d "$LV_APPDIR" ]]; then
    skip "$NAME" "hello-lvgl scaffold not present"
else
    if _picolet_build_linux "$LV_APPDIR" "$LVGL_RUNTIME_LINUX" "$WORKDIR/d-lvgl-build.log"; then
        if [[ -f "$LV_BINARY" ]]; then
            pass "$NAME"
        else
            fail "$NAME" "build succeeded but binary not found at $LV_BINARY"
        fi
    else
        fail "$NAME" "picolet build exited non-zero; see $WORKDIR/d-lvgl-build.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/d-lvgl-build.log"; fi
    fi
fi

NAME="D-lvgl-linux-xvfb-run"
if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "$NAME" "--skip-integration"
elif [[ "$HAVE_XVFB" -eq 0 ]]; then
    skip "$NAME" "xvfb-run not on PATH"
elif [[ ! -f "$LV_BINARY" ]]; then
    skip "$NAME" "lvgl binary not present (D-lvgl-linux-build failed)"
else
    # The template runs an infinite loop (while True: await asyncio.sleep(0.1)).
    # timeout exits with 124 after 5 seconds; we accept that as success.
    # A crash (segfault = 139, Python error = 1, etc.) is a failure.
    xvfb-run -a -s '-screen 0 800x600x24' \
        timeout 5 "$LV_BINARY" \
        > "$WORKDIR/d-lvgl-run.log" 2>&1 || LVGL_RC=$?
    LVGL_RC="${LVGL_RC:-0}"
    if [[ "$LVGL_RC" -eq 0 || "$LVGL_RC" -eq 124 ]]; then
        pass "$NAME"
    else
        fail "$NAME" "lvgl exited with unexpected code $LVGL_RC; see $WORKDIR/d-lvgl-run.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/d-lvgl-run.log"; fi
    fi
fi

# Windows lvgl — build only (no headed run)
NAME="D-lvgl-windows-build"
if [[ "$SKIP_WINDOWS" -eq 1 ]]; then
    skip "$NAME" "--skip-windows"
elif [[ "$HAVE_UV" -eq 0 ]]; then
    skip "$NAME" "uv not on PATH"
elif [[ ! -f "$LVGL_RUNTIME_WIN" ]]; then
    skip "$NAME" "windows-x64 lvgl runtime not found"
elif [[ ! -d "$LV_APPDIR" ]]; then
    skip "$NAME" "hello-lvgl scaffold not present"
else
    WIN_LV_BINARY="$LV_APPDIR/target/windows-x64/test-app.exe"
    if _picolet_build_win "$LV_APPDIR" "$LVGL_RUNTIME_WIN" "$WORKDIR/d-lvgl-win-build.log"; then
        if [[ -f "$WIN_LV_BINARY" ]]; then
            pass "$NAME"
        else
            fail "$NAME" "build succeeded but windows lvgl binary not found"
        fi
    else
        fail "$NAME" "windows lvgl build exited non-zero; see $WORKDIR/d-lvgl-win-build.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/d-lvgl-win-build.log"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group E: regression — PH13 gates still pass
# ---------------------------------------------------------------------------
echo "--- Group E: regression (PH13) ---"

NAME="E-ph13-regression"
if [[ "$SKIP_REGRESSION" -eq 1 ]]; then
    skip "$NAME" "--skip-regression"
elif [[ ! -f "$REPO_ROOT/tests/phase-13/run.sh" ]]; then
    skip "$NAME" "PH13 run.sh not found"
else
    PH13_LOG="$WORKDIR/e-ph13.log"
    bash "$REPO_ROOT/tests/phase-13/run.sh" \
        --skip-build \
        --skip-non-regression \
        > "$PH13_LOG" 2>&1 || true
    # PH13 run.sh outputs "=== ALL PH13 GATES PASSED ===" on success.
    if grep -q "ALL PH13 GATES PASSED" "$PH13_LOG"; then
        pass "$NAME"
    else
        fail "$NAME" "PH13 regression detected; see $PH13_LOG"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$PH13_LOG"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH14 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All mandatory gates PASS."
