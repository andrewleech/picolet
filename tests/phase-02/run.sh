#!/usr/bin/env bash
# tests/phase-02/run.sh
#
# Behaviour test suite for picolet-cli PH02.
#
# Covers: FR-CLI-1, FR-CLI-2, FR-CLI-8.
# Exit gates: PH02 gates 1-11.
#
# Usage:
#   ./tests/phase-02/run.sh [--installed]
#
#   --installed   Use the `picolet` entry-point (requires `uv pip install -e
#                 packages/picolet-cli` or equivalent). Default: uv run path.
#
# The script detects Python 3.11+ and either uv or python3 before proceeding.
# Returns 0 if all enabled subtests pass, non-zero otherwise.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MAIN_PY="$REPO_ROOT/packages/picolet-cli/picolet/__main__.py"
FIXTURES="$SCRIPT_DIR/fixtures"

# ---------------------------------------------------------------------------
# Parse options
# ---------------------------------------------------------------------------

USE_INSTALLED=0
for arg in "$@"; do
    case "$arg" in
        --installed) USE_INSTALLED=1 ;;
        --help|-h)
            grep '^#' "$0" | cut -c3-
            exit 0 ;;
        *)
            echo "error: unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Detect invocation path
# ---------------------------------------------------------------------------

if [[ "$USE_INSTALLED" -eq 1 ]]; then
    if ! command -v picolet >/dev/null 2>&1; then
        echo "error: --installed requested but 'picolet' not on PATH" >&2
        echo "       Run: uv pip install -e packages/picolet-cli" >&2
        exit 1
    fi
    PICOLET() { picolet "$@"; }
else
    # Prefer uv run; fall back to python3 if uv is absent.
    if command -v uv >/dev/null 2>&1; then
        PICOLET() { uv run "$MAIN_PY" "$@"; }
    else
        # Verify Python >= 3.11 for tomllib.
        PY_VER=$(python3 -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo "(0, 0)")
        if [[ "$PY_VER" < "(3, 11)" ]]; then
            echo "error: Python 3.11+ required (got $PY_VER); tomllib not available" >&2
            exit 1
        fi
        PICOLET() { python3 "$MAIN_PY" "$@"; }
    fi
fi

# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

PASS=0
FAIL=0
SUITE_START=$(date +%s%N)

pass() {
    printf "  PASS  %s\n" "$1"
    PASS=$(( PASS + 1 ))
}

fail() {
    printf "  FAIL  %s\n        %s\n" "$1" "$2"
    FAIL=$(( FAIL + 1 ))
}

# assert_exit0 <name> <cmd...>  — assert command exits 0.
assert_exit0() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        pass "$name"
    else
        fail "$name" "command exited non-zero: $*"
    fi
}

# assert_exit_nonzero <name> <cmd...>  — assert command exits non-zero.
assert_exit_nonzero() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        fail "$name" "expected non-zero exit but got 0: $*"
    else
        pass "$name"
    fi
}

# assert_stdout_contains <name> <pattern> <cmd...>  — assert stdout contains pattern.
assert_stdout_contains() {
    local name="$1"
    local pattern="$2"
    shift 2
    local out
    out=$("$@" 2>/dev/null || true)
    if echo "$out" | grep -qF "$pattern"; then
        pass "$name"
    else
        fail "$name" "stdout did not contain $(printf '%q' "$pattern"); got: $(printf '%q' "$out")"
    fi
}

# assert_stderr_contains <name> <pattern> <cmd...>  — assert stderr contains pattern.
assert_stderr_contains() {
    local name="$1"
    local pattern="$2"
    shift 2
    local err
    err=$("$@" 2>&1 >/dev/null || true)
    if echo "$err" | grep -qF "$pattern"; then
        pass "$name"
    else
        fail "$name" "stderr did not contain $(printf '%q' "$pattern"); got: $(printf '%q' "$err")"
    fi
}

# ---------------------------------------------------------------------------
# Temporary working area
# ---------------------------------------------------------------------------

WORKDIR=$(mktemp -d)
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

echo "=== PH02 behaviour tests ==="
echo "    main:    $MAIN_PY"
echo "    workdir: $WORKDIR"
echo

# --- Gate 1: help lists init ---

echo "--- Gate 1: help ---"

NAME="G1 help-exits-0"
assert_exit0 "$NAME" PICOLET --help

NAME="G1 help-mentions-init"
assert_stdout_contains "$NAME" "init" PICOLET --help

echo

# --- Gate 2: version ---

echo "--- Gate 2: version ---"

NAME="G2 version-exits-0"
assert_exit0 "$NAME" PICOLET --version

NAME="G2 version-stdout-nonempty"
VER_OUT=$(PICOLET --version 2>&1 || true)
if [[ -n "$VER_OUT" ]]; then
    pass "$NAME"
else
    fail "$NAME" "version output was empty"
fi

echo

# --- Gate 3: scaffold creates expected files ---

echo "--- Gate 3: scaffold ---"

APP_DIR="$WORKDIR/test-ph02-app"

NAME="G3 init-exits-0"
assert_exit0 "$NAME" PICOLET init test-ph02-app --output-dir "$APP_DIR"

NAME="G3 picolet-toml-exists"
if [[ -f "$APP_DIR/picolet.toml" ]]; then
    pass "$NAME"
else
    fail "$NAME" "picolet.toml not found in $APP_DIR"
fi

NAME="G3 src-main-py-exists"
if [[ -f "$APP_DIR/src/main.py" ]]; then
    pass "$NAME"
else
    fail "$NAME" "src/main.py not found in $APP_DIR"
fi

echo

# --- Gate 4: name substitution ---

echo "--- Gate 4: name substitution ---"

NAME="G4 name-substituted-in-toml"
if grep -qF 'name = "test-ph02-app"' "$APP_DIR/picolet.toml"; then
    pass "$NAME"
else
    fail "$NAME" "name substitution missing in $APP_DIR/picolet.toml"
fi

echo

# --- Gate 5: refuse non-empty directory ---

echo "--- Gate 5: refuse non-empty dir ---"

EXIST_DIR="$WORKDIR/existing"
mkdir -p "$EXIST_DIR"
touch "$EXIST_DIR/canary"

NAME="G5 init-nonempty-dir-exits-nonzero"
assert_exit_nonzero "$NAME" PICOLET init existing --output-dir "$EXIST_DIR"

NAME="G5 init-nonempty-dir-error-message"
assert_stderr_contains "$NAME" "non-empty" PICOLET init existing --output-dir "$EXIST_DIR"

echo

# --- Gate 6: valid toml passes validation ---

echo "--- Gate 6: valid toml passes ---"

NAME="G6 validate-valid-exits-0"
assert_exit0 "$NAME" PICOLET validate "$APP_DIR/picolet.toml"

NAME="G6 validate-fixture-valid-exits-0"
assert_exit0 "$NAME" PICOLET validate "$FIXTURES/valid.toml"

echo

# --- Gate 7: unknown top-level section rejected ---

echo "--- Gate 7: unknown section rejected ---"

NAME="G7 validate-unknown-section-exits-nonzero"
assert_exit_nonzero "$NAME" PICOLET validate "$FIXTURES/unknown-key.toml"

NAME="G7 validate-unknown-section-mentions-foo"
assert_stderr_contains "$NAME" "foo" PICOLET validate "$FIXTURES/unknown-key.toml"

NAME="G7 error-contains-file-path"
assert_stderr_contains "$NAME" "unknown-key.toml" PICOLET validate "$FIXTURES/unknown-key.toml"

echo

# --- Gate 8: wrong type rejected ---

echo "--- Gate 8: wrong type rejected ---"

NAME="G8 validate-invalid-type-exits-nonzero"
assert_exit_nonzero "$NAME" PICOLET validate "$FIXTURES/invalid-type.toml"

NAME="G8 error-contains-file-path"
assert_stderr_contains "$NAME" "invalid-type.toml" PICOLET validate "$FIXTURES/invalid-type.toml"

echo

# --- Gate 9: unknown renderer rejected ---

echo "--- Gate 9: unknown renderer rejected ---"

NAME="G9 validate-invalid-renderer-exits-nonzero"
assert_exit_nonzero "$NAME" PICOLET validate "$FIXTURES/invalid-renderer.toml"

NAME="G9 error-mentions-renderer-value"
assert_stderr_contains "$NAME" "qt" PICOLET validate "$FIXTURES/invalid-renderer.toml"

NAME="G9 error-contains-file-path"
assert_stderr_contains "$NAME" "invalid-renderer.toml" PICOLET validate "$FIXTURES/invalid-renderer.toml"

echo

# --- Gate 10: missing [app] section rejected ---

echo "--- Gate 10: missing [app] ---"

NAME="G10 validate-missing-app-exits-nonzero"
assert_exit_nonzero "$NAME" PICOLET validate "$FIXTURES/missing-app.toml"

NAME="G10 error-contains-file-path"
assert_stderr_contains "$NAME" "missing-app.toml" PICOLET validate "$FIXTURES/missing-app.toml"

echo

# --- Gate 11: unknown template rejected ---

echo "--- Gate 11: unknown template rejected ---"

NAME="G11 init-unknown-template-exits-nonzero"
assert_exit_nonzero "$NAME" PICOLET init x --template hello-lvgl --output-dir "$WORKDIR/x"

NAME="G11 error-mentions-template-name"
assert_stderr_contains "$NAME" "hello-lvgl" PICOLET init x --template hello-lvgl --output-dir "$WORKDIR/x"

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

SUITE_END=$(date +%s%N)
ELAPSED_MS=$(( (SUITE_END - SUITE_START) / 1000000 ))

TOTAL=$(( PASS + FAIL ))
echo "=== Results: $PASS passed, $FAIL failed / $TOTAL total ==="
echo "    wall time: ${ELAPSED_MS} ms"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
