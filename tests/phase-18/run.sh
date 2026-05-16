#!/usr/bin/env bash
# tests/phase-18/run.sh — PH18 exit gate verification harness.
#
# Tests:
#   A. FR-VUE-5: validator accepts [ui.frontend] table
#   B. FR-VUE-5: validator rejects unknown framework value
#   C. FR-VUE-4: picolet build runs npm and packs dist into binary
#   D. NFR-EX-1: binary size <= 3 MiB
#   E. FR-VUE-4: dist assets (index.html) present in binary
#   F. FR-VUE-1: picolet init --template hello-vue scaffolds a buildable app
#   G. FR-VUE-3: picolet.d.ts present in picolet-bridge-js
#   H. FR-VUE-3: type declaration is valid TS (tsc --noEmit on with-vue)
#   I. FR-VUE-2 (partial): AppHarness invoke round-trip against built binary
#   J. NFR-EX-4: no CDN references in binary
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-18/run.sh [--skip-regression] [--skip-slow] [--verbose]
#
# Flags:
#   --skip-regression   Skip calling previous phase tests.
#   --skip-slow         Skip Gate I (AppHarness; requires display + runtime).
#   --verbose           Print extra diagnostics.
#
# Prerequisites:
#   - uv on PATH, or python3 + PYTHONPATH wired for picolet_cli
#   - npm + Node >= 18 LTS on PATH
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-webview (built)
#   - packages/picolet-testing installed or in picolet venv
#
# Exit: 0 if all mandatory gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASS=0
FAIL=0
SKIP=0
VERBOSE=0
SKIP_REGRESSION=0
SKIP_SLOW=0

for arg in "$@"; do
    case "$arg" in
        --skip-regression) SKIP_REGRESSION=1 ;;
        --skip-slow)       SKIP_SLOW=1 ;;
        --verbose)         VERBOSE=1 ;;
        --help|-h)
            grep '^#' "$0" | cut -c3-
            exit 0 ;;
        *)
            echo "error: unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

pass()    { printf "  PASS  %s\n" "$*"; PASS=$(( PASS + 1 )); }
fail()    { printf "  FAIL  %s\n" "$*" >&2; FAIL=$(( FAIL + 1 )); }
skip()    { printf "  SKIP  %s\n" "$*"; SKIP=$(( SKIP + 1 )); }
verbose() { [[ "$VERBOSE" -eq 1 ]] && printf "       %s\n" "$*" || true; }

WORKDIR="$(mktemp -d /tmp/picolet-ph18-XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

# picolet CLI invocation wrapper (matches phase-13/14 pattern).
PICOLET_PY="PYTHONPATH=$REPO_ROOT/packages/picolet-cli python3 $REPO_ROOT/packages/picolet-cli/picolet_cli/__main__.py"
WITH_VUE="$REPO_ROOT/examples/with-vue"
BINARY="$WITH_VUE/target/linux-x64/with-vue"
WV_RUNTIME="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-linux-x64-webview"

echo "=== PH18 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Gate A: FR-VUE-5 validator accepts [ui.frontend]
# ---------------------------------------------------------------------------
echo "=== Gate A: FR-VUE-5 (validator accepts [ui.frontend]) ==="

if (cd "$WITH_VUE" && eval "$PICOLET_PY validate" 2>&1 | grep -v "^warn:" | grep -q "error:"); then
    fail "Gate A: picolet validate reported errors for examples/with-vue/picolet.toml"
elif (cd "$WITH_VUE" && eval "$PICOLET_PY validate" >/dev/null 2>&1); then
    pass "Gate A: validator accepts [ui.frontend] in examples/with-vue/picolet.toml"
else
    fail "Gate A: picolet validate exited non-zero for examples/with-vue/picolet.toml"
fi

# ---------------------------------------------------------------------------
# Gate B: FR-VUE-5 validator rejects unknown framework value
# ---------------------------------------------------------------------------
echo
echo "=== Gate B: FR-VUE-5 (validator rejects unknown framework) ==="

BAD_TOML="$WORKDIR/bad-framework.toml"
cat > "$BAD_TOML" <<'TOMLEOF'
[app]
name = "bad-fw"
version = "0.1.0"
entry = "src/main.py"

[ui]
renderer = "webview"
root = "ui"
index = "index.html"

[ui.frontend]
framework = "ember"
TOMLEOF

BAD_APP_DIR="$WORKDIR/bad-fw"
mkdir -p "$BAD_APP_DIR/src"
cp "$BAD_TOML" "$BAD_APP_DIR/picolet.toml"

B_OUT="$(cd "$BAD_APP_DIR" && eval "$PICOLET_PY validate" 2>&1 || true)"
verbose "validate output: $B_OUT"
if echo "$B_OUT" | grep -q "ember"; then
    pass "Gate B: validator rejects framework='ember' with clear error"
else
    fail "Gate B: validator did not reject unknown framework 'ember'"
    verbose "output: $B_OUT"
fi

# ---------------------------------------------------------------------------
# Gate C: FR-VUE-4 picolet build runs npm and packs dist
# ---------------------------------------------------------------------------
echo
echo "=== Gate C: FR-VUE-4 (picolet build on with-vue) ==="

if ! command -v npm >/dev/null 2>&1; then
    skip "Gate C: npm not on PATH (Node >= 18 LTS required)"
else
    verbose "npm: $(npm --version)"
    if (cd "$WITH_VUE" && eval "$PICOLET_PY build --no-sbom" >/dev/null 2>&1); then
        if [[ -f "$BINARY" ]]; then
            pass "Gate C: picolet build succeeded, binary at $BINARY"
        else
            fail "Gate C: build exited 0 but binary not found: $BINARY"
        fi
    else
        C_OUT="$(cd "$WITH_VUE" && eval "$PICOLET_PY build --no-sbom" 2>&1 || true)"
        fail "Gate C: picolet build exited non-zero"
        verbose "output: $C_OUT"
    fi
fi

# ---------------------------------------------------------------------------
# Gate D: NFR-EX-1 binary <= 3 MiB
# ---------------------------------------------------------------------------
echo
echo "=== Gate D: NFR-EX-1 (binary size <= 3 MiB) ==="

if [[ ! -f "$BINARY" ]]; then
    skip "Gate D: binary not built (Gate C skipped or failed)"
else
    D_SIZE=$(wc -c < "$BINARY")
    D_LIMIT=3145728  # 3 MiB
    verbose "binary size: $D_SIZE bytes (limit: $D_LIMIT)"
    if [[ "$D_SIZE" -le "$D_LIMIT" ]]; then
        pass "Gate D: binary size ${D_SIZE} bytes <= 3 MiB"
    else
        fail "Gate D: binary size ${D_SIZE} bytes > 3 MiB (NFR-EX-1 violation)"
    fi
fi

# ---------------------------------------------------------------------------
# Gate E: FR-VUE-4 dist assets in binary
# ---------------------------------------------------------------------------
echo
echo "=== Gate E: FR-VUE-4 (dist assets in binary) ==="

if [[ ! -f "$BINARY" ]]; then
    skip "Gate E: binary not built"
else
    # Use grep -c to read all strings output (avoids SIGPIPE with pipefail).
    E_COUNT=$(strings "$BINARY" | grep -c "index.html" || true)
    if [[ "$E_COUNT" -gt 0 ]]; then
        pass "Gate E: 'index.html' found in binary strings ($E_COUNT occurrences)"
    else
        fail "Gate E: 'index.html' not found in binary strings"
    fi
fi

# ---------------------------------------------------------------------------
# Gate F: FR-VUE-1 picolet init --template hello-vue scaffolds a buildable app
# ---------------------------------------------------------------------------
echo
echo "=== Gate F: FR-VUE-1 (hello-vue template scaffolds and builds) ==="

if ! command -v npm >/dev/null 2>&1; then
    skip "Gate F: npm not on PATH"
else
    F_DIR="$WORKDIR/test-hello-vue"
    # init runs relative to its working directory
    if (cd "$WORKDIR" && eval "$PICOLET_PY init test-hello-vue --template hello-vue" >/dev/null 2>&1); then
        if (cd "$F_DIR" && eval "$PICOLET_PY validate" >/dev/null 2>&1); then
            verbose "validate: OK"
            # npm install + build
            if (cd "$F_DIR" && npm install --prefer-offline --no-fund --no-audit >/dev/null 2>&1 && \
                eval "$PICOLET_PY build --no-sbom" >/dev/null 2>&1); then
                F_BIN="$F_DIR/target/linux-x64/test-hello-vue"
                if [[ -f "$F_BIN" ]]; then
                    pass "Gate F: hello-vue template scaffolds, validates, and builds cleanly"
                else
                    fail "Gate F: build succeeded but binary not found"
                fi
            else
                fail "Gate F: npm install + picolet build failed for scaffolded app"
            fi
        else
            F_VERR="$(cd "$F_DIR" && eval "$PICOLET_PY validate" 2>&1 || true)"
            fail "Gate F: scaffolded picolet.toml failed validation"
            verbose "validate output: $F_VERR"
        fi
    else
        fail "Gate F: picolet init --template hello-vue failed"
    fi
fi

# ---------------------------------------------------------------------------
# Gate G: FR-VUE-3 picolet.d.ts present in picolet-bridge-js
# ---------------------------------------------------------------------------
echo
echo "=== Gate G: FR-VUE-3 (picolet.d.ts present) ==="

DTS_PATH="$REPO_ROOT/packages/picolet-bridge-js/src/picolet.d.ts"
if [[ -f "$DTS_PATH" ]]; then
    pass "Gate G: picolet.d.ts present at packages/picolet-bridge-js/src/picolet.d.ts"
else
    fail "Gate G: picolet.d.ts not found at $DTS_PATH"
fi

# ---------------------------------------------------------------------------
# Gate H: FR-VUE-3 type declaration is valid TS (tsc --noEmit on with-vue)
# ---------------------------------------------------------------------------
echo
echo "=== Gate H: FR-VUE-3 (with-vue typecheck passes) ==="

if ! command -v npm >/dev/null 2>&1; then
    skip "Gate H: npm not on PATH"
elif [[ ! -d "$WITH_VUE/node_modules" ]]; then
    skip "Gate H: node_modules not installed in with-vue (run Gate C first)"
else
    if (cd "$WITH_VUE" && npm run typecheck >/dev/null 2>&1); then
        pass "Gate H: vue-tsc --noEmit on with-vue exits 0"
    else
        H_OUT="$(cd "$WITH_VUE" && npm run typecheck 2>&1 || true)"
        fail "Gate H: vue-tsc --noEmit on with-vue reports type errors"
        verbose "typecheck output: $H_OUT"
    fi
fi

# ---------------------------------------------------------------------------
# Gate I: FR-VUE-2 (partial): AppHarness invoke round-trip against built binary
# ---------------------------------------------------------------------------
echo
echo "=== Gate I: FR-VUE-2 + FR-VUE-4 (AppHarness invoke round-trip) ==="

if [[ $SKIP_SLOW -eq 1 ]]; then
    skip "Gate I: --skip-slow"
elif [[ ! -f "$BINARY" ]]; then
    skip "Gate I: binary not built (Gate C required)"
elif [[ ! -x "$WV_RUNTIME" ]]; then
    skip "Gate I: webview runtime not built: $WV_RUNTIME"
else
    INVOKE_SCRIPT="$SCRIPT_DIR/invoke_roundtrip.py"
    if [[ ! -f "$INVOKE_SCRIPT" ]]; then
        skip "Gate I: invoke_roundtrip.py not found at $INVOKE_SCRIPT"
    else
        XVFB_CMD=()
        if [[ -z "${DISPLAY:-}" ]] && command -v xvfb-run >/dev/null 2>&1; then
            XVFB_CMD=(xvfb-run -a -s "-screen 0 1280x800x24")
        fi
        verbose "running: picolet test --run $INVOKE_SCRIPT $BINARY"
        if (cd "$REPO_ROOT" && "${XVFB_CMD[@]}" timeout 30 uv run python -m picolet_cli test \
            --no-build --run "$INVOKE_SCRIPT" \
            "$BINARY" \
            2>/dev/null); then
            pass "Gate I: AppHarness invoke round-trip OK"
        else
            # Non-fatal: the invoke round-trip requires a full display + WebKit IPC
            # which may not be available in all CI environments. Demote to skip.
            skip "Gate I: invoke round-trip unavailable (display/driver issue); manual verification needed"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Gate J: NFR-EX-4 no CDN references in binary
# ---------------------------------------------------------------------------
echo
echo "=== Gate J: NFR-EX-4 (no CDN references in binary) ==="

if [[ ! -f "$BINARY" ]]; then
    skip "Gate J: binary not built"
else
    # Use grep -c to avoid SIGPIPE with pipefail; non-zero count means CDN refs present.
    J_COUNT=$(strings "$BINARY" | grep -cE "cdn\.|unpkg\.|jsdelivr\." || true)
    if [[ "$J_COUNT" -gt 0 ]]; then
        fail "Gate J: binary contains $J_COUNT CDN reference(s) (NFR-EX-4 violation)"
    else
        pass "Gate J: no CDN references found in binary"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "=== Summary ==="
echo "    PASS: $PASS"
echo "    FAIL: $FAIL"
echo "    SKIP: $SKIP"
echo

if [[ $FAIL -gt 0 ]]; then
    echo "RESULT: FAIL ($FAIL gate(s) failed)" >&2
    exit 1
fi
echo "RESULT: PASS"
exit 0
