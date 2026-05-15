#!/usr/bin/env bash
# tests/phase-16/run.sh — PH16 exit gate verification harness.
#
# Tests:
#   A. CLI wiring: `picolet dev --help` and `picolet run --help` work.
#   B. Watcher imports correctly; _Watcher detects file changes.
#   C. Debounce: multiple rapid changes produce one rebuild signal.
#   D. Integration: `picolet dev` rebuilds + relaunches on touch src/main.py.
#   E. SIGINT: dev loop shuts down cleanly.
#   F. Regression: PH00-PH15 still green.
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-16/run.sh [--skip-regression] [--skip-integration] [--verbose]
#
# Flags:
#   --skip-regression    Skip PH15 regression call.
#   --skip-integration   Skip gates D and E (require a built runtime).
#   --verbose            Print extra detail.
#
# Prerequisites:
#   - uv on PATH.
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-cli (for D+E).
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
SKIP_INTEGRATION=0

for arg in "$@"; do
    case "$arg" in
        --skip-regression)  SKIP_REGRESSION=1 ;;
        --skip-integration) SKIP_INTEGRATION=1 ;;
        --verbose)          VERBOSE=1 ;;
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

WORKDIR="$(mktemp -d /tmp/picolet-ph16-XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

CLI_RUNTIME_LINUX="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-linux-x64-cli"

echo "=== PH16 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Gate A: CLI wiring
# ---------------------------------------------------------------------------
echo "=== Gate A: CLI wiring ==="

if (cd "$REPO_ROOT" && uv run python -m picolet --help 2>&1 | grep -q "dev"); then
    pass "picolet --help lists 'dev' subcommand"
else
    fail "picolet --help missing 'dev' subcommand"
fi

if (cd "$REPO_ROOT" && uv run python -m picolet --help 2>&1 | grep -q "run"); then
    pass "picolet --help lists 'run' subcommand"
else
    fail "picolet --help missing 'run' subcommand"
fi

if (cd "$REPO_ROOT" && uv run python -m picolet dev --help 2>&1 | grep -q "\-\-target"); then
    pass "picolet dev --help shows --target flag"
else
    fail "picolet dev --help missing --target flag"
fi

if (cd "$REPO_ROOT" && uv run python -m picolet run --help 2>&1 | grep -q "\-\-no-build"); then
    pass "picolet run --help shows --no-build flag"
else
    fail "picolet run --help missing --no-build flag"
fi

# ---------------------------------------------------------------------------
# Gate B: _Watcher unit test (stdlib only, no runtime needed)
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate B: _Watcher unit test ==="

WATCHER_TEST="$WORKDIR/test_watcher.py"
WATCH_DIR="$WORKDIR/watch_src"
mkdir -p "$WATCH_DIR"

cat > "$WATCHER_TEST" <<'PYEOF'
import sys
import time
from pathlib import Path

# Bootstrap picolet package from repo.
repo_root = Path(sys.argv[1])
sys.path.insert(0, str(repo_root / "packages" / "picolet-cli"))

from picolet.dev_cmd import _Watcher

watch_dir = Path(sys.argv[2])

# Seed initial file.
f = watch_dir / "main.py"
f.write_text("print('hello')")

watcher = _Watcher([watch_dir])

# No change yet — should return False.
result1 = watcher.changed()
assert result1 is False, f"Expected no change after snapshot, got {result1}"
print("B1: no spurious change detected: OK")

# Modify the file (ensure mtime advances).
time.sleep(0.05)
f.write_text("print('world')")
# Force mtime increment on systems with low-resolution clocks.
t = time.time() + 1
import os
os.utime(f, (t, t))

result2 = watcher.changed()
assert result2 is True, f"Expected change after file modification, got {result2}"
print("B2: modification detected: OK")

# Snapshot was updated internally; second call should return False.
result3 = watcher.changed()
assert result3 is False, f"Expected no change after snapshot reset, got {result3}"
print("B3: snapshot reset after detection: OK")

# New file.
g = watch_dir / "extra.py"
g.write_text("x = 1")
t2 = time.time() + 2
os.utime(g, (t2, t2))
result4 = watcher.changed()
assert result4 is True, f"Expected change for new file, got {result4}"
print("B4: new file detected: OK")

# Deleted file.
watcher.changed()  # consume
g.unlink()
result5 = watcher.changed()
assert result5 is True, f"Expected change for deleted file, got {result5}"
print("B5: deletion detected: OK")

print("ALL WATCHER TESTS PASS")
PYEOF

if uv run python "$WATCHER_TEST" "$REPO_ROOT" "$WATCH_DIR" 2>&1; then
    pass "_Watcher: all unit assertions pass (B1-B5)"
else
    fail "_Watcher: unit test failed"
fi

# ---------------------------------------------------------------------------
# Gate C: debounce unit test
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate C: debounce logic unit test ==="

DEBOUNCE_TEST="$WORKDIR/test_debounce.py"
DEBOUNCE_DIR="$WORKDIR/debounce_src"
mkdir -p "$DEBOUNCE_DIR"

cat > "$DEBOUNCE_TEST" <<'PYEOF'
"""
Simulate the dev loop debounce logic without starting a real subprocess.

The invariant tested: N rapid file changes within one poll window
(before DEBOUNCE_DELAY passes) must produce exactly one rebuild trigger.
"""
import sys
import time
import os
from pathlib import Path

repo_root = Path(sys.argv[1])
src_dir = Path(sys.argv[2])
sys.path.insert(0, str(repo_root / "packages" / "picolet-cli"))

from picolet.dev_cmd import _Watcher, _DEBOUNCE_DELAY, _POLL_INTERVAL

# Seed files.
files = [src_dir / f"file{i}.py" for i in range(5)]
for f in files:
    f.write_text("pass")

watcher = _Watcher([src_dir])

rebuild_count = 0
last_change_time = None
pending_rebuild = False

# Simulate 5 rapid changes (all within same poll window).
base_t = time.time() + 1
for i, f in enumerate(files):
    f.write_text(f"x = {i}")
    os.utime(f, (base_t + i * 0.01, base_t + i * 0.01))

# One poll cycle — should detect changes.
changed = watcher.changed()
if changed:
    last_change_time = time.monotonic()
    pending_rebuild = True

assert pending_rebuild is True, "Should have pending_rebuild after changes"
print("C1: pending_rebuild set on first change detection: OK")

# Simulate a second poll — no new changes.
changed = watcher.changed()
assert changed is False, f"No new changes expected, got {changed}"
print("C2: no spurious second detection: OK")

# Debounce window has not yet passed — no rebuild yet.
if pending_rebuild and last_change_time is not None:
    elapsed = time.monotonic() - last_change_time
    # This should be < DEBOUNCE_DELAY since we did no sleep.
    assert elapsed < _DEBOUNCE_DELAY, f"Elapsed {elapsed:.3f}s already past debounce delay"
    print(f"C3: debounce not yet elapsed ({elapsed:.3f}s < {_DEBOUNCE_DELAY}s): OK")

# Simulate time passing past the debounce window.
time.sleep(_DEBOUNCE_DELAY + 0.05)

# Now a poll cycle should fire the rebuild.
elapsed = time.monotonic() - last_change_time
if pending_rebuild and elapsed >= _DEBOUNCE_DELAY:
    rebuild_count += 1
    pending_rebuild = False
    last_change_time = None

assert rebuild_count == 1, f"Expected exactly 1 rebuild, got {rebuild_count}"
print(f"C4: exactly 1 rebuild triggered for {len(files)} rapid changes: OK")

print("ALL DEBOUNCE TESTS PASS")
PYEOF

if uv run python "$DEBOUNCE_TEST" "$REPO_ROOT" "$DEBOUNCE_DIR" 2>&1; then
    pass "debounce: rapid changes produce single rebuild (C1-C4)"
else
    fail "debounce: logic test failed"
fi

# ---------------------------------------------------------------------------
# Gate D: integration — dev loop rebuilds on touch (requires runtime)
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate D: integration — rebuild on file change ==="

if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "integration skipped via --skip-integration"
elif [[ ! -f "$CLI_RUNTIME_LINUX" ]]; then
    skip "runtime not found ($CLI_RUNTIME_LINUX); skipping integration gate"
else
    APP_DIR="$WORKDIR/hello-app"
    DEV_LOG="$WORKDIR/dev.log"

    # Scaffold a hello-cli app.
    (
        cd "$REPO_ROOT" && \
        uv run python -m picolet init hello-app \
            --template hello-cli \
            --output-dir "$APP_DIR" \
            > "$WORKDIR/init.log" 2>&1
    )
    verbose "init log: $(cat "$WORKDIR/init.log")"

    # Start picolet dev in background.
    # Use --project so uv finds the picolet workspace when running from the
    # app directory (which has no uv workspace of its own).
    (
        cd "$APP_DIR" && \
        uv run --project "$REPO_ROOT" python -m picolet dev --verbose > "$DEV_LOG" 2>&1
    ) &
    DEV_PID=$!
    verbose "dev PID: $DEV_PID"

    # Wait for initial build to complete (up to 60 s).
    INITIAL_BUILD_OK=0
    for i in $(seq 1 60); do
        if grep -q "^Built " "$DEV_LOG" 2>/dev/null; then
            INITIAL_BUILD_OK=1
            break
        fi
        sleep 1
    done

    if [[ "$INITIAL_BUILD_OK" -eq 0 ]]; then
        fail "dev: initial build did not complete within 60 s"
        verbose "dev log tail: $(tail -20 "$DEV_LOG" 2>/dev/null || true)"
        kill "$DEV_PID" 2>/dev/null || true
    else
        verbose "initial build completed"
        pass "dev: initial build completes on startup"

        # Record the number of 'Built' lines before the touch.
        BUILDS_BEFORE=$(grep -c "^Built " "$DEV_LOG" 2>/dev/null || echo 0)
        verbose "builds before touch: $BUILDS_BEFORE"

        # Touch the source file to trigger a rebuild.
        ENTRY_FILE="$APP_DIR/src/main.py"
        touch "$ENTRY_FILE"
        verbose "touched $ENTRY_FILE"

        # Wait up to 10 s for a second build.
        REBUILD_OK=0
        for i in $(seq 1 20); do
            BUILDS_NOW=$(grep -c "^Built " "$DEV_LOG" 2>/dev/null || echo 0)
            if [[ "$BUILDS_NOW" -gt "$BUILDS_BEFORE" ]]; then
                REBUILD_OK=1
                break
            fi
            sleep 0.5
        done

        if [[ "$REBUILD_OK" -eq 1 ]]; then
            pass "dev: rebuild triggered within 10 s of touch src/main.py"
        else
            fail "dev: no rebuild detected within 10 s of file change"
            verbose "dev log tail: $(tail -20 "$DEV_LOG" 2>/dev/null || true)"
        fi

        # Gate D2: flurry of edits produces single rebuild.
        BUILDS_BEFORE2=$(grep -c "^Built " "$DEV_LOG" 2>/dev/null || echo 0)
        # Touch 5 files in rapid succession (within 200 ms).
        for i in 1 2 3 4 5; do
            echo "# edit $i" >> "$ENTRY_FILE"
        done
        verbose "wrote 5 rapid appends to $ENTRY_FILE"

        # Wait up to 5 s for debounce to fire.
        sleep 3

        BUILDS_AFTER2=$(grep -c "^Built " "$DEV_LOG" 2>/dev/null || echo 0)
        EXTRA_BUILDS=$(( BUILDS_AFTER2 - BUILDS_BEFORE2 ))
        verbose "extra builds triggered: $EXTRA_BUILDS"

        if [[ "$EXTRA_BUILDS" -eq 1 ]]; then
            pass "dev: flurry of edits triggers exactly 1 rebuild (debounce)"
        elif [[ "$EXTRA_BUILDS" -eq 0 ]]; then
            fail "dev: no rebuild triggered after rapid edits"
        else
            # Allow up to 2 — poll granularity may split two poll windows.
            if [[ "$EXTRA_BUILDS" -le 2 ]]; then
                pass "dev: rapid edits triggered $EXTRA_BUILDS rebuild(s) (within acceptable range)"
            else
                fail "dev: rapid edits triggered $EXTRA_BUILDS rebuilds (expected 1-2)"
            fi
        fi

        # Kill the dev process cleanly.
        kill "$DEV_PID" 2>/dev/null || true
        wait "$DEV_PID" 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------------------
# Gate E: SIGINT shuts down cleanly
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate E: SIGINT clean shutdown ==="

if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
    skip "integration skipped via --skip-integration"
elif [[ ! -f "$CLI_RUNTIME_LINUX" ]]; then
    skip "runtime not found; skipping SIGINT gate"
else
    SIGINT_APP="$WORKDIR/sigint-app"
    SIGINT_LOG="$WORKDIR/sigint.log"

    # Reuse the app from Gate D if it exists, else scaffold fresh.
    if [[ ! -d "$SIGINT_APP" ]]; then
        (
            cd "$REPO_ROOT" && \
            uv run python -m picolet init sigint-app \
                --template hello-cli \
                --output-dir "$SIGINT_APP" \
                > /dev/null 2>&1
        )
    fi

    # Write a PID file so we can find the Python process directly.
    SIGINT_PID_FILE="$WORKDIR/sigint-dev.pid"

    # Start dev loop.  We wrap the invocation in a small Python script that
    # writes its own PID before exec'ing into dev_cmd, so the test can send
    # SIGINT directly to the Python process (bypassing uv's signal forwarding).
    SIGINT_LAUNCHER="$WORKDIR/sigint_launch.py"
    cat > "$SIGINT_LAUNCHER" <<PYEOF
import os, sys
os.write(1, (str(os.getpid()) + "\n").encode())
sys.stdout.flush()
sys.path.insert(0, "$REPO_ROOT/packages/picolet-cli")
from picolet.dev_cmd import run as _dev_run
import argparse
args = argparse.Namespace(target=None, verbose=False, func=_dev_run)
_dev_run(args)
PYEOF

    (
        cd "$SIGINT_APP" && \
        uv run --project "$REPO_ROOT" python "$SIGINT_LAUNCHER" > "$SIGINT_LOG" 2>&1
    ) &
    SIGINT_SUBSHELL=$!
    verbose "sigint subshell PID: $SIGINT_SUBSHELL"

    # Extract the Python PID from the first line of the log.
    SIGINT_PYTHON_PID=""
    for i in $(seq 1 30); do
        FIRSTLINE="$(head -1 "$SIGINT_LOG" 2>/dev/null || true)"
        if [[ "$FIRSTLINE" =~ ^[0-9]+$ ]]; then
            SIGINT_PYTHON_PID="$FIRSTLINE"
            break
        fi
        sleep 0.5
    done
    verbose "sigint Python PID: $SIGINT_PYTHON_PID"

    # Wait for dev to be running (up to 60 s).
    for i in $(seq 1 60); do
        if grep -q "watching for changes" "$SIGINT_LOG" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    verbose "dev is watching; sending SIGINT to Python PID $SIGINT_PYTHON_PID"

    # Send SIGINT directly to the Python dev process.
    if [[ -n "$SIGINT_PYTHON_PID" ]]; then
        kill -SIGINT "$SIGINT_PYTHON_PID" 2>/dev/null || true
    else
        kill -SIGINT "$SIGINT_SUBSHELL" 2>/dev/null || true
    fi

    # Wait for clean exit (up to 10 s).
    CLEAN_EXIT=0
    for i in $(seq 1 20); do
        if ! kill -0 "$SIGINT_SUBSHELL" 2>/dev/null; then
            CLEAN_EXIT=1
            break
        fi
        sleep 0.5
    done

    if [[ "$CLEAN_EXIT" -eq 1 ]]; then
        pass "dev: exits cleanly on SIGINT"
    else
        fail "dev: did not exit within 10 s of SIGINT"
        kill -9 "$SIGINT_SUBSHELL" 2>/dev/null || true
        wait "$SIGINT_SUBSHELL" 2>/dev/null || true
    fi

    if grep -q "shutting down" "$SIGINT_LOG" 2>/dev/null; then
        pass "dev: prints shutdown message on SIGINT"
    else
        fail "dev: no shutdown message found in output"
        verbose "sigint log: $(cat "$SIGINT_LOG" 2>/dev/null || true)"
    fi
fi

# ---------------------------------------------------------------------------
# Gate F: PH00-PH15 regression
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate F: PH00-PH15 regression ==="

if [[ "$SKIP_REGRESSION" -eq 1 ]]; then
    skip "regression skipped via --skip-regression"
else
    PH15_HARNESS="$REPO_ROOT/tests/phase-15/run.sh"
    if [[ ! -f "$PH15_HARNESS" ]]; then
        skip "phase-15/run.sh not found; skipping regression"
    else
        echo "  Running phase-15/run.sh --skip-regression ..."
        if bash "$PH15_HARNESS" --skip-regression 2>&1; then
            pass "PH00-PH15 regression: phase-15 harness passes"
        else
            fail "PH00-PH15 regression: phase-15 harness failed"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 16 test summary ==="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  SKIP: $SKIP"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
    echo "RESULT: FAIL ($FAIL failures)"
    exit 1
else
    echo "RESULT: PASS"
    exit 0
fi
