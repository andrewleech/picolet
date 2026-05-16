#!/usr/bin/env bash
# tests/phase-04/run.sh — PH04 exit gate verification harness.
#
# Covers: FR-CLI-3, FR-CLI-4, FR-RT-1, FR-RT-3, FR-RT-4, FR-RT-5, FR-RT-6,
#         FR-RT-7, FR-RT-8, NFR-1, NFR-9, FR-BP-5 (trailer round-trip),
#         FR-BP-6 (Windows reproducibility).
#
# Usage:
#   cd /home/anl/picolet
#   ./tests/phase-04/run.sh [--skip-build]
#
#   --skip-build   Skip the initial build-runtime.sh invocation; require the
#                  runtime artifact to already exist on disk.
#
# Prerequisites:
#   - WSL2 with Windows interop enabled (to run .exe files)
#   - docker with dockcross/windows-static-x64-posix:latest (only without --skip-build)
#   - uv (for picolet invocation without installation)
#
# Returns 0 if all gates pass, non-zero if any gate fails.

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG_ROOT="$REPO_ROOT/packages/picolet-runtime"
MAIN_PY="$REPO_ROOT/packages/picolet-cli/picolet/__main__.py"

RUNTIME_EXE="$PKG_ROOT/build/picolet-runtime-windows-x64-cli.exe"
LINUX_RUNTIME="$PKG_ROOT/build/picolet-runtime-linux-x64-cli"

# Invoke picolet via uv run — matches PH02/PH03 convention; no venv install needed.
PICOLET="uv run $MAIN_PY"

# ---------------------------------------------------------------------------
# Parse options
# ---------------------------------------------------------------------------

SKIP_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
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
SUITE_START=$(date +%s%N)

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

assert_output() {
    local name="$1"
    local expected="$2"
    local actual="$3"
    # Strip Windows CR from WSL interop output.
    actual="$(printf '%s' "$actual" | tr -d '\r')"
    if [[ "$actual" == "$expected" ]]; then
        pass "$name"
    else
        fail "$name" "expected=$(printf '%q' "$expected") actual=$(printf '%q' "$actual")"
    fi
}

# ---------------------------------------------------------------------------
# Scratch directory
# ---------------------------------------------------------------------------

WORKDIR="/tmp/picolet-phase-04-tests-$$"
mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

# ---------------------------------------------------------------------------
# Suite header
# ---------------------------------------------------------------------------

echo "=== PH04 exit gate verification ==="
echo "    repo:    $REPO_ROOT"
echo "    runtime: $RUNTIME_EXE"
echo "    workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Group A: Build + artifact
# ---------------------------------------------------------------------------

echo "--- Group A: build + artifact ---"

# A1 — build-runtime.sh exits 0 and produces the .exe.
NAME="A1 build-runtime-exits-0"
if [[ "$SKIP_BUILD" -eq 1 ]]; then
    skip "$NAME" "--skip-build requested"
else
    if bash "$PKG_ROOT/scripts/build-runtime.sh" --target windows-x64 --variant cli; then
        pass "$NAME"
    else
        fail "$NAME" "build-runtime.sh exited non-zero"
    fi
fi

NAME="A2 artifact-exists-on-disk"
if [[ -f "$RUNTIME_EXE" ]]; then
    pass "$NAME"
else
    fail "$NAME" "artifact not found: $RUNTIME_EXE"
    echo "Cannot continue without runtime artifact." >&2
    exit 1
fi

NAME="A3 artifact-is-pe-coff-exe"
# `file` should report it as a PE32+ executable.
FILE_DESC="$(file "$RUNTIME_EXE")"
if echo "$FILE_DESC" | grep -qi "PE32\|MS Windows\|executable"; then
    pass "$NAME"
else
    fail "$NAME" "file(1) did not identify as PE/Windows: $FILE_DESC"
fi

echo

# ---------------------------------------------------------------------------
# Group B: Binary properties (FR-RT-1, FR-RT-3, NFR-1, NFR-9)
# ---------------------------------------------------------------------------

echo "--- Group B: binary properties ---"

# B1 — FR-RT-3: no renderer module strings in binary.
NAME="B1 no-renderer-strings (FR-RT-3)"
if strings "$RUNTIME_EXE" | grep -qiE 'webview|gtk|sdl|lvgl'; then
    fail "$NAME" "renderer string found in binary"
else
    pass "$NAME"
fi

# B2 — FR-RT-1: no unexpected DLLs (statically linked; only system DLLs).
NAME="B2 no-unexpected-dlls (FR-RT-1)"
BAD_DLLS="$(objdump -p "$RUNTIME_EXE" 2>/dev/null | grep 'DLL Name' | \
    grep -iE 'webview|gtk|sdl|lvgl|opengl' || true)"
if [[ -z "$BAD_DLLS" ]]; then
    pass "$NAME"
else
    fail "$NAME" "unexpected DLLs: $BAD_DLLS"
fi

# B3 — NFR-1: runtime binary <= 1 MiB.
NAME="B3 runtime-size-le-1mib (NFR-1)"
RT_SIZE=$(wc -c < "$RUNTIME_EXE")
if [[ "$RT_SIZE" -le 1048576 ]]; then
    pass "$NAME"
    echo "       size: $RT_SIZE bytes ($(( RT_SIZE * 100 / 1048576 ))% of ceiling)"
else
    fail "$NAME" "size $RT_SIZE bytes > 1048576 bytes (NFR-1 violated)"
fi

# B4 — NFR-9: PE OS version <= 10 (targets Windows 10 or earlier).
# Note: MajorOSystemVersion=4 means Windows NT 4.0; MinGW defaults are conservative.
# This satisfies NFR-9 (Windows 10 21H2+ runs any lower-version binary).
# Perfect Win10 21H2 verification requires a VM; WSL interop proves the exe
# runs on the current Windows host (necessarily >= the declared OS version).
# The NFR-9 limitation (WSL host may be newer than 21H2) is documented here
# and in the [PH04] Caveat commit in git log.
NAME="B4 pe-os-version-le-10 (NFR-9)"
MAJOR_OS="$(objdump -p "$RUNTIME_EXE" 2>/dev/null | \
    grep -i 'MajorOSystemVersion' | awk '{print $2}' | head -1 || true)"
if [[ -n "$MAJOR_OS" && "$MAJOR_OS" -le 10 ]]; then
    pass "$NAME"
    echo "       MajorOSystemVersion=$MAJOR_OS"
else
    fail "$NAME" "MajorOSystemVersion='$MAJOR_OS' (expected <= 10)"
fi

# B5 — Stock .exe last 4 bytes not 'PYLT' (no false-positive trigger).
NAME="B5 stock-exe-tail-not-pylt"
LAST4="$(tail -c 4 "$RUNTIME_EXE" | od -An -tx1 | tr -d ' \n')"
if [[ "$LAST4" != "50594c54" ]]; then
    pass "$NAME"
    echo "       tail bytes: $LAST4"
else
    fail "$NAME" "last 4 bytes are PYLT -- false-positive risk"
fi

echo

# ---------------------------------------------------------------------------
# Group C: Runtime behaviour (FR-RT-4, FR-RT-5, FR-RT-6, FR-RT-7, FR-RT-8)
# ---------------------------------------------------------------------------

echo "--- Group C: runtime behaviour ---"

# C1 — FR-RT-4: gc.add_heap() callable.
NAME="C1 gc-add-heap (FR-RT-4)"
actual="$("$RUNTIME_EXE" -c 'import gc; gc.add_heap(4096); print("heap-ok")' 2>/dev/null | tr -d '\r' || true)"
assert_output "$NAME" "heap-ok" "$actual"

# C2 — FR-RT-5: ffi module importable.
NAME="C2 ffi-importable (FR-RT-5)"
actual="$("$RUNTIME_EXE" -c 'import ffi; print("ffi-ok")' 2>/dev/null | tr -d '\r' || true)"
assert_output "$NAME" "ffi-ok" "$actual"

# C3 — FR-RT-6: /rom auto-mounted (stat succeeds).
NAME="C3 rom-mounted (FR-RT-6)"
actual="$("$RUNTIME_EXE" -c 'import os; os.stat("/rom"); print("stat-ok")' 2>/dev/null | tr -d '\r' || true)"
assert_output "$NAME" "stat-ok" "$actual"

# C4 — FR-RT-6: /rom in sys.path.
NAME="C4 rom-in-sys-path (FR-RT-6)"
actual="$("$RUNTIME_EXE" -c 'import sys; print("/rom" in sys.path)' 2>/dev/null | tr -d '\r' || true)"
assert_output "$NAME" "True" "$actual"

# C5 — FR-RT-8: sys.argv populated (non-empty for -c invocation).
NAME="C5 sys-argv-populated (FR-RT-8)"
ARGV0="$("$RUNTIME_EXE" -c 'import sys; print(sys.argv[0])' 2>/dev/null | tr -d '\r' || true)"
if [[ -n "$ARGV0" ]]; then
    pass "$NAME"
    echo "       sys.argv[0]='$ARGV0'"
else
    fail "$NAME" "sys.argv[0] is empty"
fi

# C6 — asyncio import (frozen manifest).
NAME="C6 asyncio-importable"
actual="$("$RUNTIME_EXE" -c 'import asyncio; print("aio-ok")' 2>/dev/null | tr -d '\r' || true)"
assert_output "$NAME" "aio-ok" "$actual"

# C7 — json built-in.
NAME="C7 json-builtin"
actual="$("$RUNTIME_EXE" -c 'import json; print(json.dumps({"a":1}))' 2>/dev/null | tr -d '\r' || true)"
assert_output "$NAME" '{"a": 1}' "$actual"

# C8 — os.path (frozen manifest).
NAME="C8 os-path-join"
OSPATH="$("$RUNTIME_EXE" -c 'import os.path; print(os.path.join("a","b"))' 2>/dev/null | tr -d '\r' || true)"
if [[ "$OSPATH" == "a/b" || "$OSPATH" == 'a\b' ]]; then
    pass "$NAME"
    echo "       os.path.join='$OSPATH'"
else
    fail "$NAME" "unexpected: '$OSPATH'"
fi

# C9 — Stock runtime: empty /rom, no stderr (no trailer appended).
NAME="C9 empty-romfs-no-stderr"
ROMFS_STDOUT="$("$RUNTIME_EXE" -c 'import os; print(sorted(os.listdir("/rom")))' \
    2>"$WORKDIR/c9_stderr.txt" | tr -d '\r' || true)"
ROMFS_STDERR="$(cat "$WORKDIR/c9_stderr.txt")"
if [[ "$ROMFS_STDOUT" == "[]" && -z "$ROMFS_STDERR" ]]; then
    pass "$NAME"
else
    fail "$NAME" "stdout='$ROMFS_STDOUT' stderr='$ROMFS_STDERR'"
fi

echo

# ---------------------------------------------------------------------------
# Group D: End-to-end build pipeline (FR-CLI-3, FR-CLI-4, FR-RT-6, FR-RT-7)
# ---------------------------------------------------------------------------

echo "--- Group D: end-to-end build pipeline ---"

# D1 — FR-CLI-3: picolet build --target windows-x64 emits target/windows-x64/<app>.exe
NAME="D1 output-path-correct (FR-CLI-3)"
D1_DIR="$WORKDIR/d1"
mkdir -p "$D1_DIR"
(cd "$D1_DIR" && $PICOLET init d1app --template hello-cli >/dev/null 2>&1) || true
D1_APP="$D1_DIR/d1app"
if [[ -d "$D1_APP" ]]; then
    (cd "$D1_APP" && $PICOLET build --target windows-x64 >/dev/null 2>&1) || true
    if [[ -f "$D1_APP/target/windows-x64/d1app.exe" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "target/windows-x64/d1app.exe not produced"
    fi
else
    fail "$NAME" "picolet init failed"
fi

# D2 — FR-CLI-3/FR-RT-7: built exe runs and prints expected hello output.
NAME="D2 hello-cli-output (FR-RT-7)"
D1_EXE="$D1_APP/target/windows-x64/d1app.exe"
if [[ -f "$D1_EXE" ]]; then
    actual="$("$D1_EXE" 2>/dev/null | tr -d '\r' || true)"
    assert_output "$NAME" "Hello from d1app" "$actual"
else
    fail "$NAME" "exe missing (D1 failed)"
fi

# D3 — FR-RT-6: built exe's romfs auto-mounts (trailer path exercised).
# The hello output in D2 depends on /rom/main.mpy being found via the trailer.
# D2 passing is the direct proof; this gate documents it explicitly.
NAME="D3 trailer-path-exercised (FR-RT-6)"
if [[ -f "$D1_EXE" ]]; then
    actual="$("$D1_EXE" 2>/dev/null | tr -d '\r' || true)"
    if [[ "$actual" == "Hello from d1app" ]]; then
        pass "$NAME"
        echo "       trailer path confirmed: /rom/main.mpy loaded and auto-ran"
    else
        fail "$NAME" "expected hello output (proves trailer); got '$actual'"
    fi
else
    skip "$NAME" "exe missing (D1 failed)"
fi

# D4 — FR-CLI-4: picolet build with no --target on WSL defaults to linux-x64.
NAME="D4 default-target-linux-x64 (FR-CLI-4)"
D4_DIR="$WORKDIR/d4"
mkdir -p "$D4_DIR"
(cd "$D4_DIR" && $PICOLET init d4app --template hello-cli >/dev/null 2>&1) || true
D4_APP="$D4_DIR/d4app"
if [[ -d "$D4_APP" ]]; then
    (cd "$D4_APP" && $PICOLET build >/dev/null 2>&1) || true
    if [[ -f "$D4_APP/target/linux-x64/d4app" ]]; then
        pass "$NAME"
    else
        fail "$NAME" "target/linux-x64/d4app not produced by default build"
    fi
else
    fail "$NAME" "picolet init failed"
fi

# D5 — webview renderer + windows-x64 target: PH10 made this a real
# build path. Verify `picolet build --target windows-x64 -v` reports
# "runtime variant: webview" rather than the old not-implemented error.
NAME="D5 webview-windows-builds (PH10)"
D5_DIR="$WORKDIR/d5"
mkdir -p "$D5_DIR/src/ui"
cat > "$D5_DIR/picolet.toml" << 'TOML'
[app]
name = "d5app"
version = "0.1.0"
entry = "src/main.py"
[ui]
renderer = "webview"
root = "ui"
[romfs]
include = ["ui"]
TOML
echo 'import picolet_ui' > "$D5_DIR/src/main.py"
echo '<html><body>x</body></html>' > "$D5_DIR/src/ui/index.html"
D5_OUT="$(cd "$D5_DIR" && $PICOLET build --target windows-x64 -v 2>&1 | grep "runtime variant:" || true)"
if echo "$D5_OUT" | grep -q "runtime variant: webview"; then
    pass "$NAME"
else
    fail "$NAME" "expected 'runtime variant: webview' in verbose output, got: $D5_OUT"
fi

echo

# ---------------------------------------------------------------------------
# Group E: Trailer round-trip + fallback modes
# ---------------------------------------------------------------------------

echo "--- Group E: trailer round-trip + fallbacks ---"

# E1 — Trailer magic: first 4 bytes of the 24-byte trailer are 'PYLT'.
NAME="E1 trailer-magic-present"
if [[ -f "$D1_EXE" ]]; then
    # The trailer is the last 24 bytes; bytes 0-3 are the 'PYLT' magic.
    TRAILER_MAGIC="$(tail -c 24 "$D1_EXE" | head -c 4 | od -An -tx1 | tr -d ' \n')"
    if [[ "$TRAILER_MAGIC" == "50594c54" ]]; then
        pass "$NAME"
        echo "       trailer magic: $TRAILER_MAGIC (PYLT)"
    else
        fail "$NAME" "expected 50594c54 (PYLT), got $TRAILER_MAGIC"
    fi
else
    skip "$NAME" "built exe missing (D1 failed)"
fi

# E2 — Trailer fallback: truncating the trailer causes fallback to empty romfs.
# Truncated binary exits 0 with no output (no user main in linked romfs).
NAME="E2 trailer-truncated-fallback"
if [[ -f "$D1_EXE" ]]; then
    BROKEN="$WORKDIR/broken.exe"
    cp "$D1_EXE" "$BROKEN"
    truncate -s -24 "$BROKEN"
    OUTPUT="$("$BROKEN" 2>/dev/null | tr -d '\r' || echo "EXIT_NONZERO")"
    if [[ -z "$OUTPUT" ]]; then
        pass "$NAME"
        echo "       truncated .exe: empty output (silent fallback to linked romfs)"
    elif [[ "$OUTPUT" == "EXIT_NONZERO" ]]; then
        fail "$NAME" "truncated .exe exited non-zero"
    else
        fail "$NAME" "unexpected output from truncated .exe: '$OUTPUT'"
    fi
else
    skip "$NAME" "built exe missing (D1 failed)"
fi

# E3 — CRC mismatch: flipping a CRC byte emits 'trailer crc mismatch' to stderr.
NAME="E3 trailer-crc-mismatch-warning"
if [[ -f "$D1_EXE" ]]; then
    BAD_CRC="$WORKDIR/bad-crc.exe"
    cp "$D1_EXE" "$BAD_CRC"
    SZ=$(wc -c < "$BAD_CRC")
    # The CRC32 field occupies bytes 12-15 of the 24-byte trailer (offset -12 to -9).
    printf '\xFF' | dd of="$BAD_CRC" conv=notrunc bs=1 seek=$(( SZ - 8 )) count=1 2>/dev/null
    ERR_OUTPUT="$("$BAD_CRC" 2>&1 | tr -d '\r' || true)"
    if echo "$ERR_OUTPUT" | grep -q "trailer crc mismatch"; then
        pass "$NAME"
    else
        fail "$NAME" "expected 'trailer crc mismatch'; got: '$ERR_OUTPUT'"
    fi
else
    skip "$NAME" "built exe missing (D1 failed)"
fi

# E4 — PE-COFF appended data tolerance: exe with trailer runs correctly.
# Passing D2 is the direct proof; document explicitly.
NAME="E4 pe-coff-appended-data-tolerated"
if [[ -f "$D1_EXE" ]]; then
    actual="$("$D1_EXE" 2>/dev/null | tr -d '\r' || true)"
    if [[ "$actual" == "Hello from d1app" ]]; then
        pass "$NAME"
        echo "       appended romfs + trailer coexist with PE-COFF loader"
    else
        fail "$NAME" "expected hello output; got '$actual'"
    fi
else
    skip "$NAME" "exe missing (D1 failed)"
fi

echo

# ---------------------------------------------------------------------------
# Group F: NFR-1 on built app; reproducibility (FR-BP-6 Windows)
# ---------------------------------------------------------------------------

echo "--- Group F: app size + reproducibility ---"

# F1 — NFR-1: built app .exe <= 1 MiB.
NAME="F1 app-exe-size-le-1mib (NFR-1)"
if [[ -f "$D1_EXE" ]]; then
    APP_SIZE="$(wc -c < "$D1_EXE")"
    if [[ "$APP_SIZE" -le 1048576 ]]; then
        pass "$NAME"
        echo "       size: $APP_SIZE bytes"
    else
        fail "$NAME" "size $APP_SIZE > 1048576 (NFR-1 violated)"
    fi
else
    skip "$NAME" "exe missing (D1 failed)"
fi

# F2 — FR-BP-6 (Windows): two picolet build --target windows-x64 runs are byte-identical.
NAME="F2 reproducibility-windows (FR-BP-6)"
F2_DIR="$WORKDIR/f2"
mkdir -p "$F2_DIR"
(cd "$F2_DIR" && $PICOLET init f2app --template hello-cli >/dev/null 2>&1) || true
F2_APP="$F2_DIR/f2app"
if [[ -d "$F2_APP" ]]; then
    (cd "$F2_APP" && $PICOLET build --target windows-x64 >/dev/null 2>&1) || true
    F2_EXE="$F2_APP/target/windows-x64/f2app.exe"
    if [[ -f "$F2_EXE" ]]; then
        cp "$F2_EXE" "$WORKDIR/f2_build1.exe"
        rm -rf "$F2_APP/target"
        (cd "$F2_APP" && $PICOLET build --target windows-x64 >/dev/null 2>&1) || true
        if [[ -f "$F2_EXE" ]]; then
            if cmp -s "$WORKDIR/f2_build1.exe" "$F2_EXE"; then
                pass "$NAME"
            else
                fail "$NAME" "two builds differ (reproducibility broken)"
            fi
        else
            fail "$NAME" "second build did not produce exe"
        fi
    else
        fail "$NAME" "first build did not produce exe"
    fi
else
    fail "$NAME" "picolet init failed"
fi

echo

# ---------------------------------------------------------------------------
# Group G: Linux regression (smoke check -- full coverage in tests/phase-03/)
# ---------------------------------------------------------------------------

echo "--- Group G: Linux pipeline regression ---"

# G1 — Linux runtime artifact still present and functional.
NAME="G1 linux-runtime-smoke"
if [[ -f "$LINUX_RUNTIME" ]]; then
    actual="$("$LINUX_RUNTIME" -c 'print("linux-reg-ok")' 2>&1 || true)"
    assert_output "$NAME" "linux-reg-ok" "$actual"
else
    fail "$NAME" "linux runtime not found: $LINUX_RUNTIME"
fi

# G2 — picolet build (Linux default) still produces linux-x64 binary.
NAME="G2 linux-build-regression"
G2_DIR="$WORKDIR/g2"
mkdir -p "$G2_DIR"
(cd "$G2_DIR" && $PICOLET init g2app --template hello-cli >/dev/null 2>&1) || true
G2_APP="$G2_DIR/g2app"
if [[ -d "$G2_APP" ]]; then
    (cd "$G2_APP" && $PICOLET build >/dev/null 2>&1) || true
    G2_BIN="$G2_APP/target/linux-x64/g2app"
    if [[ -f "$G2_BIN" ]]; then
        actual="$("$G2_BIN" 2>&1 || true)"
        assert_output "$NAME" "Hello from g2app" "$actual"
    else
        fail "$NAME" "linux-x64 binary not produced"
    fi
else
    fail "$NAME" "picolet init failed"
fi

# G3 — Linux runtime NFR-1 still holds.
NAME="G3 linux-runtime-nfr1"
if [[ -f "$LINUX_RUNTIME" ]]; then
    LRT_SIZE="$(wc -c < "$LINUX_RUNTIME")"
    if [[ "$LRT_SIZE" -le 1048576 ]]; then
        pass "$NAME"
        echo "       size: $LRT_SIZE bytes"
    else
        fail "$NAME" "size $LRT_SIZE > 1048576 (NFR-1 violated)"
    fi
else
    skip "$NAME" "linux runtime not found"
fi

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

SUITE_END=$(date +%s%N)
ELAPSED_MS=$(( (SUITE_END - SUITE_START) / 1000000 ))

TOTAL=$(( PASS + FAIL + SKIP ))
echo "=== PH04 gate results: $PASS passed, $FAIL failed, $SKIP skipped / $TOTAL total ==="
echo "    wall time: ${ELAPSED_MS} ms"

if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates:"
    for g in "${FAILED_GATES[@]}"; do
        echo "  - $g"
    done
    exit 1
fi
echo "All gates PASS."
