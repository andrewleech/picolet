#!/usr/bin/env bash
# tests/phase-09/run.sh — PH09 exit gate verification harness.
#
# Covers: FR-CLI-2, FR-WV-{2,3,4,5}, FR-IPC-{2,3}, FR-BP-{1,3,4,5}.
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-09/run.sh [--skip-integration] [--skip-rebuild]
#                               [--skip-regression] [--verbose]
#
# Prerequisites:
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-webview
#   - packages/picolet-bridge-js/dist/picolet-bridge.js built
#   - xvfb-run on PATH (for integration gates)
#   - uv on PATH
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
WEBVIEW_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-webview"
E2E_FIXTURE="$SCRIPT_DIR/fixtures/hello-webview-e2e"

SKIP_INTEGRATION=0
SKIP_REBUILD=0
SKIP_REGRESSION=0
VERBOSE=0

for arg in "$@"; do
    case "$arg" in
        --skip-integration) SKIP_INTEGRATION=1 ;;
        --skip-rebuild)     SKIP_REBUILD=1 ;;
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

WORKDIR="/tmp/picolet-ph09-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH09 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    runtime: $WEBVIEW_RUNTIME"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Group A: picolet init scaffold (gates 1–3 + tester-added A4–A6)
# ---------------------------------------------------------------------------

echo "--- Group A: scaffold (picolet init) ---"

SCAFFOLD_DIR="$WORKDIR/test-app"

NAME="A1 scaffold-tree (gate 1)"
if ! command -v uv >/dev/null 2>&1; then
    skip "$NAME" "uv not on PATH"
    SCAFFOLD_OK=0
else
    (
        cd "$REPO_ROOT" && \
        uv run python -m picolet init test-app \
            --template hello-webview \
            --output-dir "$SCAFFOLD_DIR" \
            > "$WORKDIR/a1.log" 2>&1
    ) || true

    if [[ -f "$SCAFFOLD_DIR/picolet.toml" && \
          -f "$SCAFFOLD_DIR/src/main.py" && \
          -f "$SCAFFOLD_DIR/ui/index.html" && \
          -f "$SCAFFOLD_DIR/ui/style.css" && \
          -f "$SCAFFOLD_DIR/ui/app.js" ]]; then
        pass "$NAME"
        SCAFFOLD_OK=1
    else
        fail "$NAME" "missing files in scaffold; see $WORKDIR/a1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/a1.log"; fi
        SCAFFOLD_OK=0
    fi
fi

NAME="A2 toml-validates (gate 2)"
if [[ "${SCAFFOLD_OK:-0}" -eq 0 ]]; then
    skip "$NAME" "A1 did not produce scaffold"
else
    # picolet init runs validate_toml internally; check that no error: line appeared.
    if grep -q "^error:" "$WORKDIR/a1.log" 2>/dev/null; then
        fail "$NAME" "validate error detected; see $WORKDIR/a1.log"
    else
        pass "$NAME"
    fi
fi

NAME="A3 name-substituted (gate 3)"
if [[ "${SCAFFOLD_OK:-0}" -eq 0 ]]; then
    skip "$NAME" "A1 did not produce scaffold"
else
    TOML_OK=0
    HTML_OK=0
    if grep -q 'name = "test-app"' "$SCAFFOLD_DIR/picolet.toml"; then
        TOML_OK=1
    fi
    if grep -q '<title>test-app</title>' "$SCAFFOLD_DIR/ui/index.html"; then
        HTML_OK=1
    fi
    if [[ "$TOML_OK" -eq 1 && "$HTML_OK" -eq 1 ]]; then
        pass "$NAME"
    else
        fail "$NAME" "{{name}} substitution incomplete (toml=$TOML_OK html=$HTML_OK)"
    fi
fi

# A4: verify {{name}} substituted in all files that carry it (h1, main.py comment, app.js comment).
# Ensures _copy_template applied substitution across all template files, not just toml/title.
NAME="A4 name-substituted-all-files (tester gate)"
if [[ "${SCAFFOLD_OK:-0}" -eq 0 ]]; then
    skip "$NAME" "A1 did not produce scaffold"
else
    H1_OK=0
    MAIN_OK=0
    APPJS_OK=0
    if grep -q '<h1>test-app</h1>' "$SCAFFOLD_DIR/ui/index.html"; then H1_OK=1; fi
    if grep -q 'test-app' "$SCAFFOLD_DIR/src/main.py"; then MAIN_OK=1; fi
    if grep -q 'test-app' "$SCAFFOLD_DIR/ui/app.js"; then APPJS_OK=1; fi
    if [[ "$H1_OK" -eq 1 && "$MAIN_OK" -eq 1 && "$APPJS_OK" -eq 1 ]]; then
        pass "$NAME"
    else
        fail "$NAME" "{{name}} missing in some files (h1=$H1_OK main.py=$MAIN_OK app.js=$APPJS_OK)"
    fi
fi

# A5: picolet init rejects unknown template names (error path).
NAME="A5 reject-unknown-template (tester gate)"
if ! command -v uv >/dev/null 2>&1; then
    skip "$NAME" "uv not on PATH"
else
    NEG_OUT="$(
        cd "$REPO_ROOT" && \
        uv run python -m picolet init negtest --template nosuchtemplate \
            --output-dir "$WORKDIR/neg-test-a5" 2>&1
    )" || true
    if echo "$NEG_OUT" | grep -q "unknown template"; then
        pass "$NAME"
    else
        fail "$NAME" "expected 'unknown template' error; got: $NEG_OUT"
    fi
fi

# A6: picolet init rejects a non-empty existing target directory (error path).
NAME="A6 reject-existing-nonempty-dir (tester gate)"
if ! command -v uv >/dev/null 2>&1; then
    skip "$NAME" "uv not on PATH"
else
    mkdir -p "$WORKDIR/existing-dir"
    touch "$WORKDIR/existing-dir/sentinel"
    NEG_OUT="$(
        cd "$REPO_ROOT" && \
        uv run python -m picolet init existingdir \
            --template hello-webview \
            --output-dir "$WORKDIR/existing-dir" 2>&1
    )" || true
    if echo "$NEG_OUT" | grep -q "already exists"; then
        pass "$NAME"
    else
        fail "$NAME" "expected 'already exists' error; got: $NEG_OUT"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group B: picolet build (gates 4–5 + tester-added B3)
# ---------------------------------------------------------------------------

echo "--- Group B: picolet build ---"

BUILT_TEMPLATE=""

NAME="B1 build-succeeds (gate 4 / FR-BP-1)"
if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "$NAME" "--skip-integration"
elif ! command -v uv >/dev/null 2>&1; then
    skip "$NAME" "uv not on PATH"
elif [[ ! -f "$WEBVIEW_RUNTIME" ]]; then
    skip "$NAME" "webview runtime not found"
elif [[ "${SCAFFOLD_OK:-0}" -eq 0 ]]; then
    skip "$NAME" "scaffold missing (A1 failed)"
else
    (
        cd "$SCAFFOLD_DIR" && \
        uv run --project "$REPO_ROOT" python -m picolet build \
            --target linux-x64 \
            --runtime "$WEBVIEW_RUNTIME" \
            > "$WORKDIR/b1.log" 2>&1
    ) || true

    BUILT_TEMPLATE="$SCAFFOLD_DIR/target/linux-x64/test-app"
    if [[ -f "$BUILT_TEMPLATE" ]]; then
        BSIZE=$(wc -c < "$BUILT_TEMPLATE")
        pass "$NAME"
        echo "       binary: $BUILT_TEMPLATE ($BSIZE bytes)"
    else
        fail "$NAME" "picolet build failed; see $WORKDIR/b1.log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$WORKDIR/b1.log"; fi
        BUILT_TEMPLATE=""
    fi
fi

NAME="B2 romfs-embedded (gate 5 / FR-BP-4,5)"
if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "$NAME" "--skip-integration"
elif [[ -z "$BUILT_TEMPLATE" ]]; then
    skip "$NAME" "B1 did not produce binary"
else
    TOML_OUT="$("$BUILT_TEMPLATE" -c 'print(open("/rom/picolet.toml").read())' 2>&1)"
    if echo "$TOML_OUT" | grep -q '\[window\]'; then
        pass "$NAME"
        if [[ "$VERBOSE" -eq 1 ]]; then echo "       picolet.toml from romfs: $TOML_OUT"; fi
    else
        fail "$NAME" "picolet.toml not in romfs; got: $TOML_OUT"
    fi
fi

# B3: verify [romfs] include = ["ui"] is honoured — ui/index.html accessible at /rom/ui/index.html.
# FR-WV-2 requires the webview to load root doc from /rom/<ui.root>/<index>; this confirms packing.
NAME="B3 ui-packed-in-romfs (tester gate / FR-WV-2)"
if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "$NAME" "--skip-integration"
elif [[ -z "$BUILT_TEMPLATE" ]]; then
    skip "$NAME" "B1 did not produce binary"
else
    HTML_OUT="$("$BUILT_TEMPLATE" -c 'print(open("/rom/ui/index.html").read())' 2>&1)"
    if echo "$HTML_OUT" | grep -q 'btn-greet\|btn-fail\|app\.js'; then
        pass "$NAME"
        if [[ "$VERBOSE" -eq 1 ]]; then echo "       /rom/ui/index.html first line: $(echo "$HTML_OUT" | head -1)"; fi
    else
        fail "$NAME" "ui/index.html not in romfs at /rom/ui/index.html; got: $HTML_OUT"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group C–E: integration tests under xvfb (gates 6–8)
# All three tokens come from a single fixture run.
# ---------------------------------------------------------------------------

echo "--- Groups C–E: integration tests (xvfb) ---"

_run_e2e_fixture() {
    local built="$E2E_FIXTURE/target/linux-x64/hello-webview-e2e"
    local build_log="$WORKDIR/e2e-build.log"
    local run_log="$WORKDIR/e2e-run.log"

    if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
        skip "C1/D1/E1" "--skip-integration"
        return
    fi
    if ! command -v xvfb-run >/dev/null 2>&1; then
        skip "C1/D1/E1" "xvfb-run not on PATH"
        return
    fi
    if ! command -v uv >/dev/null 2>&1; then
        skip "C1/D1/E1" "uv not on PATH"
        return
    fi
    if [[ ! -f "$WEBVIEW_RUNTIME" ]]; then
        skip "C1/D1/E1" "webview runtime not found"
        return
    fi

    # Build the e2e fixture if not already built or if rebuild requested.
    if [[ "$SKIP_REBUILD" -eq 0 || ! -f "$built" ]]; then
        (
            cd "$E2E_FIXTURE" && \
            uv run python -m picolet build \
                --target linux-x64 \
                --runtime "$WEBVIEW_RUNTIME" \
                > "$build_log" 2>&1
        ) || true
    fi

    if [[ ! -f "$built" ]]; then
        fail "C1 invoke-roundtrip (gate 6)" "e2e fixture build failed; see $build_log"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$build_log"; fi
        fail "D1 error-propagation (gate 7)" "e2e fixture build failed"
        fail "E1 python-emit (gate 8)" "e2e fixture build failed"
        return
    fi

    xvfb-run -a -s '-screen 0 800x600x24' \
        timeout 20 "$built" \
        > "$run_log" 2>&1 || true

    NAME="C1 invoke-roundtrip (gate 6 / FR-IPC-2, FR-WV-5)"
    if grep -q "PICOLET_PH09_INVOKE_OK" "$run_log"; then
        pass "$NAME"
    else
        fail "$NAME" "PICOLET_PH09_INVOKE_OK not found in output"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$run_log"; fi
    fi

    NAME="D1 error-propagation (gate 7 / FR-IPC-2)"
    if grep -q "PICOLET_PH09_ERROR_OK" "$run_log"; then
        pass "$NAME"
    else
        fail "$NAME" "PICOLET_PH09_ERROR_OK not found in output"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$run_log"; fi
    fi

    NAME="E1 python-emit (gate 8 / FR-IPC-3, FR-WV-5)"
    if grep -q "PICOLET_PH09_EVENT_OK" "$run_log"; then
        pass "$NAME"
    else
        fail "$NAME" "PICOLET_PH09_EVENT_OK not found in output"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$run_log"; fi
    fi
}

_run_e2e_fixture

echo

# ---------------------------------------------------------------------------
# Group F: regression — PH08 gates still pass (gate 9)
# ---------------------------------------------------------------------------

echo "--- Group F: regression (PH08) ---"

NAME="F1 ph08-gates-still-pass (gate 9)"
if [[ "$SKIP_REGRESSION" -eq 1 ]]; then
    skip "$NAME" "--skip-regression"
elif [[ ! -f "$REPO_ROOT/tests/phase-08/run.sh" ]]; then
    skip "$NAME" "PH08 run.sh not found"
else
    PH08_OUT="$WORKDIR/ph08.log"
    bash "$REPO_ROOT/tests/phase-08/run.sh" \
        --skip-rebuild \
        > "$PH08_OUT" 2>&1 || true
    if grep -q "All mandatory gates PASS" "$PH08_OUT"; then
        pass "$NAME"
    else
        fail "$NAME" "PH08 regression detected; see $PH08_OUT"
        if [[ "$VERBOSE" -eq 1 ]]; then cat "$PH08_OUT"; fi
    fi
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH09 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All mandatory gates PASS."
