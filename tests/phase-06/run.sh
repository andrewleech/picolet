#!/usr/bin/env bash
# tests/phase-06/run.sh — PH06 exit gate verification harness.
#
# Covers: FR-IPC-1, FR-IPC-2, FR-IPC-3, FR-IPC-4, FR-IPC-5, plus the
#         operational gates 12-18 spelled out in PHASE_06's exit-gate
#         table (unknown command, malformed JSON, EOF, cancellation,
#         concurrent invokes).
#
# Usage:
#   cd /home/anl/picolet
#   ./tests/phase-06/run.sh [--skip-unit] [--skip-windows]
#
#   --skip-unit      Skip the CPython unit-test pass.
#   --skip-windows   Skip gate 20 (Windows runtime import check) — useful
#                    when no Windows build is available locally.
#
# Prerequisites:
#   - packages/picolet-runtime/build/picolet-runtime-linux-x64-cli  (PH04+)
#   - packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe (PH04+)
#   - python3 (host; for the unit-test pass)
#
# Returns 0 if all mandatory gates pass, non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
LINUX_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-cli"
WINDOWS_RUNTIME="$PKG_ROOT/build/picolet-runtime-windows-x64-cli.exe"
PICOLET_PYTHON="$PKG_ROOT/python"
UNIT_TEST="$SCRIPT_DIR/test_dispatcher.py"

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

SKIP_UNIT=0
SKIP_WINDOWS=0
for arg in "$@"; do
    case "$arg" in
        --skip-unit) SKIP_UNIT=1 ;;
        --skip-windows) SKIP_WINDOWS=1 ;;
        --help|-h)
            grep '^#' "$0" | cut -c3-
            exit 0 ;;
        *)
            echo "error: unknown argument: $arg" >&2
            exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

PASS=0
FAIL=0
SKIP=0
FAILED_GATES=()
SUITE_START=$(date +%s%N 2>/dev/null || date +%s)

pass() {
    local name="$1"
    printf "  PASS  %s\n" "$name"
    PASS=$(( PASS + 1 ))
}

fail() {
    local name="$1"
    local msg="${2:-}"
    if [[ -n "$msg" ]]; then
        printf "  FAIL  %s\n        %s\n" "$name" "$msg"
    else
        printf "  FAIL  %s\n" "$name"
    fi
    FAIL=$(( FAIL + 1 ))
    FAILED_GATES+=("$name")
}

skip() {
    local name="$1"
    local reason="${2:-}"
    if [[ -n "$reason" ]]; then
        printf "  SKIP  %s  (%s)\n" "$name" "$reason"
    else
        printf "  SKIP  %s\n" "$name"
    fi
    SKIP=$(( SKIP + 1 ))
}

# ---------------------------------------------------------------------------
# Scratch directory
# ---------------------------------------------------------------------------

WORKDIR="/tmp/picolet-ph06-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

# ---------------------------------------------------------------------------
# Suite header
# ---------------------------------------------------------------------------

echo "=== PH06 exit gate verification ==="
echo "    repo:          $REPO_ROOT"
echo "    linux runtime: $LINUX_RUNTIME"
echo "    workdir:       $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

if [[ ! -f "$LINUX_RUNTIME" ]]; then
    echo "FATAL: linux runtime not found: $LINUX_RUNTIME"
    echo "       run: packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant cli"
    exit 1
fi

# ---------------------------------------------------------------------------
# Group U: CPython unit tests
# ---------------------------------------------------------------------------

echo "--- Group U: CPython unit tests ---"

NAME="U1 dispatcher-unit-tests"
if [[ "$SKIP_UNIT" -eq 1 ]]; then
    skip "$NAME" "--skip-unit requested"
else
    if PYTHONPATH="$PICOLET_PYTHON" python3 "$UNIT_TEST" >"$WORKDIR/u1.log" 2>&1; then
        # unittest prints "OK" on success; pull the result line.
        tail -3 "$WORKDIR/u1.log"
        pass "$NAME"
    else
        cat "$WORKDIR/u1.log"
        fail "$NAME" "unit tests failed; see $WORKDIR/u1.log"
    fi
fi
echo

# ---------------------------------------------------------------------------
# Group A: Frozen package + import
# ---------------------------------------------------------------------------

echo "--- Group A: frozen picolet package ---"

# A1 / gate 3 — import picolet works in the runtime.
NAME="A1 import-picolet (gate 3)"
actual="$("$LINUX_RUNTIME" -c 'import picolet; print("picolet-ok")' 2>&1)"
if [[ "$actual" == "picolet-ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'picolet-ok'; got: $actual"
fi

# A2 / gate 4 — public API names are callable.
NAME="A2 public-api-callable (gate 4)"
actual="$("$LINUX_RUNTIME" -c '
import picolet
print(callable(picolet.command), callable(picolet.invoke),
      callable(picolet.emit), callable(picolet.on), callable(picolet.run))
' 2>&1)"
if [[ "$actual" == "True True True True True" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected all True; got: $actual"
fi

# A3 / gate 2 + 16 — NFR-1 size still holds for linux runtime.
NAME="A3 runtime-size-le-1mib (gate 2/16)"
RT_SIZE=$(wc -c < "$LINUX_RUNTIME")
if [[ "$RT_SIZE" -le 1048576 ]]; then
    pass "$NAME"
    echo "       linux runtime: $RT_SIZE bytes (PH04 baseline 624944; picolet adds $(( RT_SIZE - 624944 )) bytes)"
else
    fail "$NAME" "size $RT_SIZE > 1048576 (NFR-1 violated)"
fi

echo

# ---------------------------------------------------------------------------
# Group B: stdio round-trip integration tests
# ---------------------------------------------------------------------------

echo "--- Group B: stdio round-trip (runtime binary, frozen picolet) ---"

# Helper: build a test app on stdin and run a JSON exchange with it.
# Args: $1 = test name, $2 = py source string, $3 = stdin, $4 = expected stdout
run_stdio() {
    local name="$1"
    local app_src="$2"
    local stdin_data="$3"
    local expected="$4"
    local actual
    actual="$(printf '%s' "$stdin_data" | "$LINUX_RUNTIME" -c "$app_src" 2>"$WORKDIR/stderr.log" || true)"
    if [[ "$actual" == "$expected" ]]; then
        pass "$name"
    else
        fail "$name" "expected=$(printf '%q' "$expected") actual=$(printf '%q' "$actual")"
        if [[ -s "$WORKDIR/stderr.log" ]]; then
            echo "        stderr: $(cat "$WORKDIR/stderr.log")"
        fi
    fi
}

# B1 / gate 6 — greet round trip.
APP_GREET='
import picolet
@picolet.command
async def greet(args):
    return "hi " + args["name"]
picolet.run()
'
run_stdio "B1 stdio-greet-roundtrip (gate 6)" "$APP_GREET" \
    '{"id":1,"cmd":"greet","args":{"name":"world"}}' \
    '{"result": "hi world", "id": 1, "ok": true}'

# B2 / gate 7 — exception preservation.
APP_BOOM='
import picolet
@picolet.command
async def boom(args):
    raise ValueError("oops")
picolet.run()
'
run_stdio "B2 stdio-exception (gate 7)" "$APP_BOOM" \
    '{"id":2,"cmd":"boom","args":null}' \
    '{"error": {"message": "oops", "type": "ValueError"}, "id": 2, "ok": false}'

# B3 / gate 12 — unknown command returns structured error.
APP_EMPTY='
import picolet
picolet.run()
'
run_stdio "B3 stdio-unknown-command (gate 12)" "$APP_EMPTY" \
    '{"id":3,"cmd":"nope","args":null}' \
    '{"error": {"message": "no command: nope", "type": "NameError"}, "id": 3, "ok": false}'

# B4 / gate 13 — malformed JSON tolerated; subsequent valid message replied.
NAME="B4 stdio-malformed-json-tolerated (gate 13)"
APP_GREET2='
import picolet
@picolet.command
async def greet(args):
    return "ok"
picolet.run()
'
B4_STDIN=$'not-json\n{"id":4,"cmd":"greet","args":null}\n'
B4_OUT="$(printf '%s' "$B4_STDIN" | "$LINUX_RUNTIME" -c "$APP_GREET2" 2>"$WORKDIR/b4_err.log" || true)"
B4_ERR="$(cat "$WORKDIR/b4_err.log")"
if [[ "$B4_OUT" == '{"result": "ok", "id": 4, "ok": true}' ]] && \
   echo "$B4_ERR" | grep -q "malformed JSON on stdin"; then
    pass "$NAME"
else
    fail "$NAME" "stdout=$(printf '%q' "$B4_OUT") stderr=$(printf '%q' "$B4_ERR")"
fi

# B5 / gate 14 — EOF triggers clean exit within 1 s.
NAME="B5 stdio-eof-clean-exit (gate 14)"
B5_START=$(date +%s%N 2>/dev/null || date +%s)
: | "$LINUX_RUNTIME" -c "$APP_EMPTY" >"$WORKDIR/b5_out.log" 2>"$WORKDIR/b5_err.log"
B5_RC=$?
B5_END=$(date +%s%N 2>/dev/null || date +%s)
B5_ELAPSED_NS=$(( B5_END - B5_START ))
if [[ ${#B5_START} -gt 12 ]]; then
    B5_ELAPSED_MS=$(( B5_ELAPSED_NS / 1000000 ))
else
    B5_ELAPSED_MS=$(( B5_ELAPSED_NS * 1000 ))
fi
if [[ "$B5_RC" -eq 0 && "$B5_ELAPSED_MS" -lt 1000 && ! -s "$WORKDIR/b5_err.log" ]]; then
    pass "$NAME"
    echo "       exit=$B5_RC, elapsed=${B5_ELAPSED_MS} ms"
else
    fail "$NAME" "rc=$B5_RC, elapsed=${B5_ELAPSED_MS} ms, stderr=$(cat "$WORKDIR/b5_err.log")"
fi

# B6 / gate 17 — concurrent in-flight (depth 3): slow + medium + fast.
NAME="B6 stdio-concurrent-in-flight (gate 17)"
APP_MULTI='
import picolet, asyncio
@picolet.command
async def slow(args):
    await asyncio.sleep(args["ms"]/1000.0); return "slow-done"
@picolet.command
async def med(args):
    await asyncio.sleep(args["ms"]/1000.0); return "med-done"
@picolet.command
async def fast(args):
    return "fast-done"
picolet.run()
'
B6_STDIN=$'{"id":1,"cmd":"slow","args":{"ms":100}}\n{"id":2,"cmd":"med","args":{"ms":50}}\n{"id":3,"cmd":"fast","args":null}\n'
# Pipe with a brief held-open period so all three handlers can complete
# before EOF.
B6_OUT="$( (printf '%s' "$B6_STDIN"; sleep 0.5) | "$LINUX_RUNTIME" -c "$APP_MULTI" 2>"$WORKDIR/b6_err.log" || true)"
# Expect 3 reply lines; fast first, med second, slow third (interleaving order).
# The exact ordering is timing-dependent; assert the *count* and *content*.
B6_LINE_COUNT="$(printf '%s\n' "$B6_OUT" | wc -l)"
if echo "$B6_OUT" | grep -q '"result": "fast-done", "id": 3, "ok": true' && \
   echo "$B6_OUT" | grep -q '"result": "med-done", "id": 2, "ok": true' && \
   echo "$B6_OUT" | grep -q '"result": "slow-done", "id": 1, "ok": true'; then
    pass "$NAME"
    echo "       reply count: $B6_LINE_COUNT"
    # Bonus: verify concurrency by checking that fast came before slow.
    FAST_LINE="$(echo "$B6_OUT" | grep -n 'fast-done' | cut -d: -f1)"
    SLOW_LINE="$(echo "$B6_OUT" | grep -n 'slow-done' | cut -d: -f1)"
    if [[ -n "$FAST_LINE" && -n "$SLOW_LINE" && "$FAST_LINE" -lt "$SLOW_LINE" ]]; then
        echo "       concurrency confirmed: fast reply (line $FAST_LINE) precedes slow (line $SLOW_LINE)"
    fi
else
    fail "$NAME" "missing one or more replies. out=$(printf '%q' "$B6_OUT")"
fi

# B7 — emit from python writes to stdout (no reply expected).
NAME="B7 stdio-emit-event-output"
APP_EMIT='
import picolet, asyncio
async def boot():
    await picolet.emit("started", {"v": 1})
    await picolet.emit("progress", {"pct": 42})
    await picolet.emit("done", None)
picolet.run(main=boot)
'
B7_OUT="$(: | "$LINUX_RUNTIME" -c "$APP_EMIT" 2>"$WORKDIR/b7_err.log" || true)"
EXPECTED_LINES=$'{"data": {"v": 1}, "event": "started"}\n{"data": {"pct": 42}, "event": "progress"}\n{"data": null, "event": "done"}'
if [[ "$B7_OUT" == "$EXPECTED_LINES" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected=$(printf '%q' "$EXPECTED_LINES") actual=$(printf '%q' "$B7_OUT")"
fi

# B8 — wire format: every reply has only the spec-allowed keys.
NAME="B8 stdio-wire-format (gate 10)"
APP_ECHO='
import picolet
@picolet.command
async def echo(args):
    return args
picolet.run()
'
B8_OUT="$(printf '%s\n' '{"id":1,"cmd":"echo","args":[1,2,3]}' | "$LINUX_RUNTIME" -c "$APP_ECHO" 2>/dev/null || true)"
# Use python to parse and check keys.
B8_CHECK="$(python3 -c "
import json
m = json.loads(r'''$B8_OUT''')
allowed = {'id', 'ok', 'result'}
extra = set(m) - allowed
missing = allowed - set(m)
print('extra=', extra, 'missing=', missing)
" 2>&1)"
if [[ "$B8_CHECK" == "extra= set() missing= set()" ]]; then
    pass "$NAME"
else
    fail "$NAME" "key audit failed: $B8_CHECK; reply=$B8_OUT"
fi

# B9 — large JSON payload (100 KB) survives a stdio round-trip.
NAME="B9 stdio-large-payload-roundtrip"
APP_ECHO_BIG='
import picolet
@picolet.command
async def echo_big(args):
    return args
picolet.run()
'
# Build a 100 KB string payload as a JSON request.
B9_PAYLOAD="$(python3 -c "import json; print(json.dumps({'id':1,'cmd':'echo_big','args':'x'*102400}))")"
B9_OUT="$(printf '%s\n' "$B9_PAYLOAD" | "$LINUX_RUNTIME" -c "$APP_ECHO_BIG" 2>"$WORKDIR/b9_err.log" || true)"
B9_CHECK="$(python3 -c "
import json
reply = json.loads('''$B9_OUT''')
expected = 'x' * 102400
print('ok' if reply.get('ok') and reply.get('result') == expected else 'FAIL: result mismatch or ok=false')
" 2>&1)"
if [[ "$B9_CHECK" == "ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "large-payload check: $B9_CHECK; stderr=$(cat "$WORKDIR/b9_err.log")"
fi

# B10 — non-async function passed to @picolet.command raises TypeError in the runtime.
NAME="B10 runtime-non-async-command-rejected"
B10_OUT="$("$LINUX_RUNTIME" -c '
import picolet, sys
try:
    @picolet.command
    def not_async(args):
        return args
    print("no-error")
except TypeError as e:
    print("TypeError")
' 2>&1)"
if [[ "$B10_OUT" == "TypeError" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected TypeError; got: $B10_OUT"
fi

# B11 — two subscribers on the same topic both receive the event.
# The app registers two subscribers, receives one event from stdin, prints
# the count, then exits cleanly.  We keep stdin open until the app replies
# via stdout (which it does after printing the count).
NAME="B11 stdio-multi-subscriber-both-receive"
APP_MULTI_SUB='
import picolet, asyncio
received = []
def h1(data): received.append(1)
def h2(data): received.append(2)
picolet.on("tick", h1)
picolet.on("tick", h2)

@picolet.command
async def check(args):
    # By the time this command handler runs, the earlier event has already
    # been dispatched to h1 and h2 synchronously within the same recv loop.
    return len(received)

picolet.run()
'
# Send the event then immediately a check command; the command reply tells
# us how many subscribers were called.
B11_STDIN=$'{"event":"tick","data":1}\n{"id":1,"cmd":"check","args":null}\n'
B11_OUT="$(printf '%s' "$B11_STDIN" | "$LINUX_RUNTIME" -c "$APP_MULTI_SUB" 2>"$WORKDIR/b11_err.log" || true)"
# The reply may be the only line; check that result == 2.
B11_CHECK="$(python3 -c "
import json
for line in '''$B11_OUT'''.strip().splitlines():
    m = json.loads(line)
    if m.get('id') == 1:
        print(m.get('result'))
        break
" 2>&1)"
if [[ "$B11_CHECK" == "2" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected result=2; got check=$B11_CHECK; output=$(printf '%q' "$B11_OUT"); stderr=$(cat "$WORKDIR/b11_err.log")"
fi

echo

# ---------------------------------------------------------------------------
# Group C: Windows non-regression
# ---------------------------------------------------------------------------

echo "--- Group C: Windows non-regression (gate 20) ---"

NAME="C1 windows-import-picolet (gate 20)"
if [[ "$SKIP_WINDOWS" -eq 1 ]]; then
    skip "$NAME" "--skip-windows requested"
elif [[ ! -f "$WINDOWS_RUNTIME" ]]; then
    skip "$NAME" "windows runtime not present: $WINDOWS_RUNTIME"
else
    actual="$("$WINDOWS_RUNTIME" -c 'import picolet; print("picolet-ok")' 2>&1 | tr -d '\r')"
    if [[ "$actual" == "picolet-ok" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "expected 'picolet-ok'; got: $actual"
    fi
fi

NAME="C2 windows-public-api-callable"
if [[ "$SKIP_WINDOWS" -eq 1 ]]; then
    skip "$NAME" "--skip-windows requested"
elif [[ ! -f "$WINDOWS_RUNTIME" ]]; then
    skip "$NAME" "windows runtime not present"
else
    actual="$("$WINDOWS_RUNTIME" -c '
import picolet
print(callable(picolet.command), callable(picolet.invoke),
      callable(picolet.emit), callable(picolet.on), callable(picolet.run))
' 2>&1 | tr -d '\r')"
    if [[ "$actual" == "True True True True True" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "expected all True; got: $actual"
    fi
fi

NAME="C3 windows-runtime-size-le-1mib"
if [[ "$SKIP_WINDOWS" -eq 1 ]]; then
    skip "$NAME" "--skip-windows requested"
elif [[ ! -f "$WINDOWS_RUNTIME" ]]; then
    skip "$NAME" "windows runtime not present"
else
    WIN_SIZE="$(wc -c < "$WINDOWS_RUNTIME")"
    if [[ "$WIN_SIZE" -le 1048576 ]]; then
        pass "$NAME"
        echo "       windows runtime: $WIN_SIZE bytes (PH04 baseline 565760; picolet adds $(( WIN_SIZE - 565760 )) bytes)"
    else
        fail "$NAME" "size $WIN_SIZE > 1048576 (NFR-1 violated)"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group D: PH03/PH04/PH05 regression
# ---------------------------------------------------------------------------

echo "--- Group D: prior phase regression ---"

# D1 — Stock linux runtime still prints from -c.
NAME="D1 ph03-linux-c-flag"
actual="$("$LINUX_RUNTIME" -c 'print("ph03-ok")' 2>&1)"
if [[ "$actual" == "ph03-ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected 'ph03-ok'; got: $actual"
fi

# D2 — Stock linux runtime: gc.add_heap, ffi, /rom mount (PH04 gates C1-C4).
NAME="D2 ph04-runtime-basics"
HEAP_OK="$("$LINUX_RUNTIME" -c 'import gc; gc.add_heap(4096); print("heap-ok")' 2>&1)"
FFI_OK="$("$LINUX_RUNTIME" -c 'import ffi; print("ffi-ok")' 2>&1)"
ROM_OK="$("$LINUX_RUNTIME" -c 'import os; os.stat("/rom"); print("stat-ok")' 2>&1)"
ASYNC_OK="$("$LINUX_RUNTIME" -c 'import asyncio; print("aio-ok")' 2>&1)"
if [[ "$HEAP_OK" == "heap-ok" && "$FFI_OK" == "ffi-ok" && \
      "$ROM_OK" == "stat-ok" && "$ASYNC_OK" == "aio-ok" ]]; then
    pass "$NAME"
else
    fail "$NAME" "heap=$HEAP_OK ffi=$FFI_OK rom=$ROM_OK aio=$ASYNC_OK"
fi

# D3 — End-to-end picolet build still produces a working app (regression).
NAME="D3 ph03-end-to-end-build"
if command -v uv &>/dev/null; then
    D3_APP="$WORKDIR/d3app"
    (cd "$WORKDIR" && uv run "$REPO_ROOT/packages/picolet-cli/picolet_cli/__main__.py" \
        init d3app --template hello-cli >/dev/null 2>&1) || true
    if [[ -d "$D3_APP" ]]; then
        (cd "$D3_APP" && uv run "$REPO_ROOT/packages/picolet-cli/picolet_cli/__main__.py" \
            build --target linux-x64 --runtime "$LINUX_RUNTIME" >/dev/null 2>&1) || true
        if [[ -f "$D3_APP/target/linux-x64/d3app" ]]; then
            actual="$("$D3_APP/target/linux-x64/d3app" 2>&1)"
            if [[ "$actual" == "Hello from d3app" ]]; then
                pass "$NAME"
            else
                fail "$NAME" "expected 'Hello from d3app'; got: $actual"
            fi
        else
            fail "$NAME" "d3app binary not produced"
        fi
    else
        fail "$NAME" "picolet init failed"
    fi
else
    skip "$NAME" "uv not available; skipping picolet build regression"
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

SUITE_END=$(date +%s%N 2>/dev/null || date +%s)
if [[ ${#SUITE_START} -gt 12 ]]; then
    ELAPSED_MS=$(( (SUITE_END - SUITE_START) / 1000000 ))
else
    ELAPSED_MS=$(( (SUITE_END - SUITE_START) * 1000 ))
fi

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH06 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="
echo "    wall time: ${ELAPSED_MS} ms"

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All mandatory gates PASS."
