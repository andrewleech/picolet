#!/usr/bin/env bash
# tests/phase-01/run.sh
#
# Behaviour test suite for picolet-runtime-linux-x64-cli (PH01).
#
# Covers: FR-RT-1, FR-RT-3, FR-RT-4, FR-RT-5, FR-RT-6, FR-RT-7, FR-RT-8, NFR-1.
#
# Usage:
#   ./tests/phase-01/run.sh [--build] [--skip-romfs-rebuild]
#
#   --build              Run build-runtime.sh before testing (full build from
#                        a warm or cold tree). Without this flag the script
#                        requires the binary to already exist.
#   --skip-romfs-rebuild Skip the subtest that rebuilds the binary with the
#                        no_frozen romfs. Useful in environments where the full
#                        build toolchain is not available.
#
# Returns 0 if all enabled subtests pass, non-zero otherwise.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
BIN="$PKG_ROOT/build/picolet-runtime-linux-x64-cli"
BUILD_SCRIPT="$PKG_ROOT/scripts/build-runtime.sh"

# ---------------------------------------------------------------------------
# Parse options
# ---------------------------------------------------------------------------

DO_BUILD=0
SKIP_ROMFS_REBUILD=0

for arg in "$@"; do
    case "$arg" in
        --build)              DO_BUILD=1 ;;
        --skip-romfs-rebuild) SKIP_ROMFS_REBUILD=1 ;;
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
SUITE_START=$(date +%s%N)

pass() {
    local name="$1"
    printf "  PASS  %s\n" "$name"
    PASS=$(( PASS + 1 ))
}

fail() {
    local name="$1"
    local msg="$2"
    printf "  FAIL  %s\n        %s\n" "$name" "$msg"
    FAIL=$(( FAIL + 1 ))
}

skip() {
    local name="$1"
    local reason="$2"
    printf "  SKIP  %s  (%s)\n" "$name" "$reason"
    SKIP=$(( SKIP + 1 ))
}

# assert_output <subtest-name> <expected> <actual>
assert_output() {
    local name="$1"
    local expected="$2"
    local actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        pass "$name"
    else
        fail "$name" "expected=$(printf '%q' "$expected") actual=$(printf '%q' "$actual")"
    fi
}

# assert_exit0 <subtest-name> — run remaining args, assert exit 0.
assert_exit0() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        pass "$name"
    else
        fail "$name" "command exited non-zero: $*"
    fi
}

# ---------------------------------------------------------------------------
# Pre-flight: optionally build first
# ---------------------------------------------------------------------------

echo "=== PH01 behaviour tests ==="
echo "    binary: $BIN"
echo

if [[ "$DO_BUILD" -eq 1 ]]; then
    echo "[pre-flight] Running build-runtime.sh --target linux-x64 --variant cli"
    "$BUILD_SCRIPT" --target linux-x64 --variant cli
    echo
fi

if [[ ! -f "$BIN" ]]; then
    echo "error: binary not found: $BIN" >&2
    echo "       Run with --build to build it first, or run build-runtime.sh manually." >&2
    exit 1
fi

# Group C4 depends on the binary having the test_romfs fixture embedded.
# Subsequent phases rebuild this same path with a different (empty) romfs,
# so re-link the primary binary with test_romfs to guarantee C4 sees its
# expected state.  Idempotent and fast on warm cache (~3 s).
echo "[pre-flight] Re-linking $BIN with test_romfs embedded"
"$BUILD_SCRIPT" --target linux-x64 --variant cli --test-romfs test_romfs \
    >/dev/null 2>&1 || {
    echo "warning: failed to re-link with test_romfs; C4 may fail" >&2
}
echo

# ---------------------------------------------------------------------------
# Group A: Build-time checks (binary properties)
# ---------------------------------------------------------------------------

echo "--- Group A: build-time checks ---"

# A1 — Binary exists and is executable (FR-RT-1)
NAME="A1 binary-exists-and-executable"
if [[ -x "$BIN" ]]; then
    pass "$NAME"
else
    fail "$NAME" "file not executable or not found: $BIN"
fi

# A2 — Binary is a 64-bit ELF (FR-RT-1 — single self-contained Linux executable)
NAME="A2 binary-is-elf64"
actual=$(file "$BIN")
if echo "$actual" | grep -q "ELF 64-bit"; then
    pass "$NAME"
else
    fail "$NAME" "file(1) output does not contain 'ELF 64-bit': $actual"
fi

# A3 — Size ≤ 1 MiB (NFR-1)
NAME="A3 binary-size-le-1mib"
SIZE=$(wc -c < "$BIN")
CEILING=1048576
if [[ "$SIZE" -le "$CEILING" ]]; then
    pass "$NAME"
    echo "       size: $SIZE bytes ($(( SIZE * 100 / CEILING ))% of NFR-1 ceiling)"
else
    fail "$NAME" "size $SIZE bytes exceeds 1 MiB ceiling ($CEILING bytes)"
fi

# A4 — Only permitted shared-library deps (NFR-4, NFR-8: libc, libm, ld-linux; no libffi, no SDL2, etc.)
NAME="A4 shared-lib-deps-permitted-only"
BAD_DEPS=$(ldd "$BIN" | grep -vE 'linux-vdso|libc\.so|libm\.so|libpthread\.so|libdl\.so|libgcc_s\.so|ld-linux' || true)
if [[ -z "$BAD_DEPS" ]]; then
    pass "$NAME"
else
    fail "$NAME" "unexpected dynamic dependencies:\n$BAD_DEPS"
fi

# A5 — Binary runs inside ubuntu:22.04 (NFR-8: glibc 2.35 baseline)
#
# The ldd name-check in A4 does not catch versioned symbol requirements.
# A binary built on Ubuntu 24.04 may silently pick up GLIBC_2.38 symbols
# (__isoc23_sscanf, fmod) that break at runtime on 22.04 even if ldd
# shows only libc.so / libm.so.  This subtest is the authoritative gate.
#
# Skips cleanly if Docker is unavailable rather than failing, so CI
# environments without Docker do not hard-block the suite.
NAME="A5 ubuntu-2204-runtime (NFR-8)"
if ! command -v docker >/dev/null 2>&1; then
    skip "$NAME" "docker not available"
elif ! docker image inspect ubuntu:22.04 >/dev/null 2>&1; then
    skip "$NAME" "ubuntu:22.04 image not pulled; run: docker pull ubuntu:22.04"
else
    A5_OUT=$(docker run --rm \
        -v "$REPO_ROOT:$REPO_ROOT" \
        -w "$REPO_ROOT" \
        ubuntu:22.04 \
        packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'print("ok")' 2>&1)
    A5_RC=$?
    if [[ "$A5_RC" -eq 0 && "$A5_OUT" == "ok" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "exit=$A5_RC output=$(printf '%q' "$A5_OUT")"
    fi
fi

echo

# ---------------------------------------------------------------------------
# Group B: Runtime behaviour checks
# ---------------------------------------------------------------------------

echo "--- Group B: runtime behaviour ---"

# B1 — Basic print via -c (FR-RT-1 smoke, FR-RT-8 implicit)
NAME="B1 print-via-dash-c"
actual=$("$BIN" -c 'print("ok")' 2>&1)
assert_output "$NAME" "ok" "$actual"

# B2 — sys.argv shape for -c invocation (FR-RT-8)
# unix port main.c: when -c is used, sys.argv[0] is '-c'; subsequent args follow.
NAME="B2 sys-argv-dash-c-shape"
actual=$("$BIN" -c 'import sys; print(sys.argv)' arg1 arg2 2>&1)
assert_output "$NAME" "['-c', 'arg1', 'arg2']" "$actual"

# B3 — sys.argv shape when a positional script path is passed (FR-RT-8)
#
# Behaviour note: when the binary has an embedded romfs main (test_romfs fixture),
# a positional script-path argument does NOT bypass run_main — only -c and -m do.
# The romfs main runs first; if it calls sys.exit() the script is never reached.
# Testing posional-script argv therefore requires a binary whose romfs main does
# NOT call sys.exit() so control falls through.  The test_romfs_no_frozen fixture
# also calls sys.exit(0), so there is no fixture that leaves control to the caller.
#
# Verified with -c: sys.argv[0] == '-c' and positional args follow (tested in B2).
# For a script invocation the unix main.c sets sys.argv[0] to the script path,
# which is confirmed by reading main.c lines 875-876.  We cannot exercise this path
# from the pre-built binary; it is documented as a gap and tested via -c in B2.
#
# This subtest is a no-op marker so the skip is visible in the report.
NAME="B3 sys-argv-script-shape-gap"
skip "$NAME" "romfs main bypasses script args; tested via -c in B2 (see Note commit)"

# B4 — gc.add_heap is callable with an integer argument (FR-RT-4)
NAME="B4 gc-add-heap-callable"
actual=$("$BIN" -c 'import gc; gc.add_heap(4096); print("heap-ok")' 2>&1)
assert_output "$NAME" "heap-ok" "$actual"

# B5 — gc.add_heap returns an int (FR-RT-4)
NAME="B5 gc-add-heap-returns-int"
actual=$("$BIN" -c 'import gc; r = gc.add_heap(4096); print(type(r).__name__)' 2>&1)
assert_output "$NAME" "int" "$actual"

# B6 — gc.add_heap raises ValueError for N < 4096 (FR-RT-4)
NAME="B6 gc-add-heap-value-error-small-n"
actual=$("$BIN" -c '
import gc
try:
    gc.add_heap(1)
    print("no-error")
except ValueError:
    print("ValueError")
except Exception as e:
    print("unexpected:", type(e).__name__)
' 2>&1)
assert_output "$NAME" "ValueError" "$actual"

# B7 — ffi module is importable and ffi.open exists (FR-RT-5)
NAME="B7 ffi-open-exists"
actual=$("$BIN" -c 'import ffi; print(hasattr(ffi, "open"))' 2>&1)
assert_output "$NAME" "True" "$actual"

# B8 — asyncio.run executes a coroutine end-to-end (frozen manifest)
NAME="B8 asyncio-run-coro"
actual=$("$BIN" -c '
import asyncio
async def coro():
    return "aio-ok"
print(asyncio.run(coro()))
' 2>&1)
assert_output "$NAME" "aio-ok" "$actual"

# B9 — json is available as a built-in (gate 8 — C extmod)
NAME="B9 json-builtin-available"
actual=$("$BIN" -c 'import json; print(json.dumps({"a":1}))' 2>&1)
assert_output "$NAME" '{"a": 1}' "$actual"

# B10 — os.path is available from frozen manifest (gate 9)
NAME="B10 os-path-join-works"
actual=$("$BIN" -c 'import os.path; print(os.path.join("a","b"))' 2>&1)
assert_output "$NAME" "a/b" "$actual"

# B11 — MICROPY_ENABLE_COMPILER=1: compiler is present (variant correctness check)
# The cli variant keeps the compiler ON for -c / eval() / PH16 REPL.
NAME="B11 compiler-enabled"
actual=$("$BIN" -c 'import builtins; print(hasattr(builtins, "compile"))' 2>&1)
assert_output "$NAME" "True" "$actual"

echo

# ---------------------------------------------------------------------------
# Group C: Romfs / startup checks
# ---------------------------------------------------------------------------

echo "--- Group C: romfs and startup ---"

# C1 — Romfs auto-mounted: os.stat("/rom") succeeds (FR-RT-6)
NAME="C1 rom-mounted-stat"
actual=$("$BIN" -c 'import os; os.stat("/rom"); print("stat-ok")' 2>&1)
assert_output "$NAME" "stat-ok" "$actual"

# C2 — /rom in sys.path (FR-RT-6)
NAME="C2 rom-in-sys-path"
actual=$("$BIN" -c 'import sys; print("/rom" in sys.path)' 2>&1)
assert_output "$NAME" "True" "$actual"

# C3 — /rom/lib in sys.path (FR-RT-6 — both /rom and /rom/lib are prepended)
NAME="C3 rom-lib-in-sys-path"
actual=$("$BIN" -c 'import sys; print("/rom/lib" in sys.path)' 2>&1)
assert_output "$NAME" "True" "$actual"

# C4 — Binary with embedded test_romfs runs /rom/main.mpy at startup (FR-RT-7)
# The manifest has no main.py; /rom/main.mpy runs instead.
# The embedded romfs is test_romfs which prints "ok".
NAME="C4 romfs-main-runs-at-startup"
actual=$("$BIN" 2>&1)
assert_output "$NAME" "ok" "$actual"

# C5 — Binary exits 0 when /rom/main.mpy calls sys.exit(0) (FR-RT-7)
NAME="C5 romfs-main-exit-code"
"$BIN" >/dev/null 2>&1
rc=$?
if [[ "$rc" -eq 0 ]]; then
    pass "$NAME"
else
    fail "$NAME" "expected exit code 0, got $rc"
fi

echo

# ---------------------------------------------------------------------------
# Group D: Romfs fallback with a rebuilt binary
# ---------------------------------------------------------------------------
# This group rebuilds the binary with test_romfs_no_frozen (prints "ok-from-rom")
# to verify FR-RT-7 (both romfs fixtures work) and confirm the objcopy relative-
# path staging workaround is applied correctly by build-runtime.sh.
#
# Rebuilds run inside the picolet-linux-x64-build:22.04 container, matching the
# build-runtime.sh convention so the produced binary has the same glibc baseline.

LINUX_BUILD_IMAGE="picolet-linux-x64-build:22.04"
UNIX_PORT="$PKG_ROOT/micropython/ports/unix"

# Helper: run make inside the build container, working dir = UNIX_PORT.
docker_make() {
    docker run --rm \
        -v "$REPO_ROOT:$REPO_ROOT" \
        -w "$UNIX_PORT" \
        --user "$(id -u):$(id -g)" \
        "$LINUX_BUILD_IMAGE" \
        make "$@"
}

echo "--- Group D: romfs_no_frozen binary ---"

if [[ "$SKIP_ROMFS_REBUILD" -eq 1 ]]; then
    skip "D1 no-frozen-romfs-main-runs" "--skip-romfs-rebuild requested"
    skip "D2 no-frozen-romfs-exit-code"  "--skip-romfs-rebuild requested"
elif ! command -v docker >/dev/null 2>&1; then
    skip "D1 no-frozen-romfs-main-runs" "docker not available; cannot run containerised rebuild"
    skip "D2 no-frozen-romfs-exit-code"  "docker not available"
elif ! docker image inspect "$LINUX_BUILD_IMAGE" >/dev/null 2>&1; then
    skip "D1 no-frozen-romfs-main-runs" "$LINUX_BUILD_IMAGE not built; run build-runtime.sh first"
    skip "D2 no-frozen-romfs-exit-code"  "$LINUX_BUILD_IMAGE not built"
else
    # Warm rebuild with test_romfs_no_frozen takes ~3 s (only link step changes).
    NOFROZEN_BIN="/tmp/picolet-runtime-linux-x64-cli-nofrozen"

    echo "    rebuilding with test_romfs_no_frozen (warm link ~3 s) ..."
    ROMFS_STAGING="$PKG_ROOT/build/romfs_staging"

    if [[ ! -f "$ROMFS_STAGING/test_romfs_no_frozen.romfs" ]]; then
        echo "    romfs staging file missing; building it"
        python3 -m mpremote romfs \
            --output "$ROMFS_STAGING/test_romfs_no_frozen.romfs" \
            build "$PKG_ROOT/tests/phase-01/test_romfs_no_frozen"
    fi

    # Stage at hyphen-free path within the bind-mounted tree (same convention as
    # build-runtime.sh).  The relative path from $UNIX_PORT traverses no hyphens.
    ROMFS_IMG_NOFROZEN="$ROMFS_STAGING/picolet_romfs_test_romfs_no_frozen.romfs"
    cp "$ROMFS_STAGING/test_romfs_no_frozen.romfs" "$ROMFS_IMG_NOFROZEN"
    ROMFS_IMG_NOFROZEN_REL="$(realpath --relative-to="$UNIX_PORT" "$ROMFS_IMG_NOFROZEN")"

    docker_make \
        -j \
        VARIANT=picolet-cli \
        ROMFS_IMG="$ROMFS_IMG_NOFROZEN_REL" \
        PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
        >/dev/null 2>&1

    BUILT="$UNIX_PORT/build-picolet-cli/micropython"
    cp "$BUILT" "$NOFROZEN_BIN"
    strip --strip-unneeded "$NOFROZEN_BIN"

    # D1 — no_frozen binary prints "ok-from-rom" at startup (FR-RT-7)
    NAME="D1 no-frozen-romfs-main-runs"
    actual=$("$NOFROZEN_BIN" 2>&1)
    assert_output "$NAME" "ok-from-rom" "$actual"

    # D2 — no_frozen binary exits 0
    NAME="D2 no-frozen-romfs-exit-code"
    "$NOFROZEN_BIN" >/dev/null 2>&1
    rc=$?
    if [[ "$rc" -eq 0 ]]; then
        pass "$NAME"
    else
        fail "$NAME" "expected exit code 0, got $rc"
    fi

    # Restore the primary binary's romfs (re-link with test_romfs).
    echo "    restoring primary binary romfs (test_romfs) ..."
    ROMFS_IMG_PRIMARY="$ROMFS_STAGING/picolet_romfs_test_romfs.romfs"
    cp "$ROMFS_STAGING/test_romfs.romfs" "$ROMFS_IMG_PRIMARY"
    ROMFS_IMG_PRIMARY_REL="$(realpath --relative-to="$UNIX_PORT" "$ROMFS_IMG_PRIMARY")"

    docker_make \
        -j \
        VARIANT=picolet-cli \
        ROMFS_IMG="$ROMFS_IMG_PRIMARY_REL" \
        PICOLET_RUNTIME_ROOT="$(realpath "$PKG_ROOT")" \
        >/dev/null 2>&1

    cp "$BUILT" "$BIN"
    strip --strip-unneeded "$BIN"
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

SUITE_END=$(date +%s%N)
ELAPSED_MS=$(( (SUITE_END - SUITE_START) / 1000000 ))

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== Results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="
echo "    wall time: ${ELAPSED_MS} ms"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
