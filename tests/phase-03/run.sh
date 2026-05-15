#!/usr/bin/env bash
# tests/phase-03/run.sh — PH03 exit gate verification harness.
#
# Usage:
#   cd /home/anl/picolet
#   ./tests/phase-03/run.sh
#
# Exits 0 if all gates pass, 1 if any gate fails.
# Each gate is numbered per the phase file's exit gate table.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PICOLET="uv run $REPO_ROOT/packages/picolet-cli/picolet/__main__.py"
RUNTIME="$REPO_ROOT/packages/picolet-runtime/build/picolet-runtime-linux-x64-cli"
WORKDIR="/tmp/picolet-ph03-test-$$"
PASS=0
FAIL=0
FAILED_GATES=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pass() { echo "  [PASS] Gate $1: $2"; ((PASS++)) || true; }
fail() { echo "  [FAIL] Gate $1: $2"; ((FAIL++)) || true; FAILED_GATES+=("$1"); }

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=== PH03 exit gate verification ==="
echo "  repo: $REPO_ROOT"
echo "  workdir: $WORKDIR"
echo

# ---------------------------------------------------------------------------
# Gate 1 — rebuild-integration.sh completes 0.
# ---------------------------------------------------------------------------
echo "[Gate 1] rebuild-integration.sh exit code"
if "$REPO_ROOT/packages/picolet-runtime/scripts/rebuild-integration.sh" >/dev/null 2>&1; then
    pass 1 "rebuild-integration.sh exits 0"
else
    fail 1 "rebuild-integration.sh failed"
fi

# ---------------------------------------------------------------------------
# Gate 2 — stock runtime still works; NFR-1 holds.
# ---------------------------------------------------------------------------
echo "[Gate 2] Stock runtime smoke test + NFR-1"
if [[ ! -f "$RUNTIME" ]]; then
    fail 2 "runtime artifact missing: $RUNTIME"
else
    RT_SIZE=$(wc -c < "$RUNTIME")
    if [[ $RT_SIZE -gt 1048576 ]]; then
        fail 2 "NFR-1 violated: runtime is $RT_SIZE bytes"
    else
        result=$("$RUNTIME" -c 'print("rt-ok")' 2>&1)
        if [[ "$result" == "rt-ok" ]]; then
            pass 2 "runtime works, size=$RT_SIZE bytes (NFR-1 OK)"
        else
            fail 2 "runtime smoke test failed: got '$result'"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Gate 3 — stock runtime mounts empty romfs, no trailer noise.
# ---------------------------------------------------------------------------
echo "[Gate 3] Stock runtime: empty romfs, no stderr trailer noise"
stdout=$("$RUNTIME" -c 'import os; print(sorted(os.listdir("/rom")))' 2>/tmp/ph03_stderr.txt || true)
stderr=$(cat /tmp/ph03_stderr.txt)
if [[ "$stdout" == "[]" && -z "$stderr" ]]; then
    pass 3 "empty romfs, silent"
else
    fail 3 "expected '[]' on stdout and no stderr; got stdout='$stdout' stderr='$stderr'"
fi

# ---------------------------------------------------------------------------
# Gate 4 — picolet init + picolet build succeeds.
# ---------------------------------------------------------------------------
echo "[Gate 4] picolet init + picolet build end-to-end"
APP4="$WORKDIR/hello-cli-test"
if (cd "$WORKDIR" && $PICOLET init hello-cli-test --template hello-cli >/dev/null 2>&1) && \
   (cd "$APP4" && $PICOLET build >/dev/null 2>&1) && \
   [[ -f "$APP4/target/linux-x64/hello-cli-test" ]]; then
    pass 4 "init + build succeeded, artifact present"
else
    fail 4 "init or build failed"
fi

# ---------------------------------------------------------------------------
# Gate 5 — produced binary is executable and prints expected output.
# ---------------------------------------------------------------------------
echo "[Gate 5] Binary runs and prints expected output"
if [[ -x "$APP4/target/linux-x64/hello-cli-test" ]]; then
    out=$("$APP4/target/linux-x64/hello-cli-test" 2>&1)
    if [[ "$out" == "Hello from hello-cli-test" ]]; then
        pass 5 "output matches: '$out'"
    else
        fail 5 "expected 'Hello from hello-cli-test', got '$out'"
    fi
else
    fail 5 "binary not executable"
fi

# ---------------------------------------------------------------------------
# Gate 6 — FR-BP-1: [ui] absent → cli; webview → NotImplementedError.
# ---------------------------------------------------------------------------
echo "[Gate 6] FR-BP-1: variant detection"
vout=$(cd "$APP4" && $PICOLET build -v 2>&1 | grep "runtime variant:" || true)
if echo "$vout" | grep -q "runtime variant: cli"; then
    pass 6a "cli variant inferred from absent [ui]"
else
    fail 6a "expected 'runtime variant: cli' in verbose output, got '$vout'"
fi
# Create webview app and verify NotImplementedError.
WV_APP="$WORKDIR/test-webview"
mkdir -p "$WV_APP/src"
cat > "$WV_APP/picolet.toml" << 'TOML'
[app]
name = "test-webview"
version = "0.1.0"
entry = "src/main.py"
[ui]
renderer = "webview"
TOML
echo 'print("x")' > "$WV_APP/src/main.py"
wverr=$(cd "$WV_APP" && $PICOLET build 2>&1 || true)
if echo "$wverr" | grep -q "not implemented"; then
    pass 6b "webview variant raises NotImplementedError"
else
    fail 6b "expected not-implemented error for webview, got '$wverr'"
fi

# ---------------------------------------------------------------------------
# Gate 7 — FR-BP-3: .mpy files present in romfs; no .py present.
# ---------------------------------------------------------------------------
echo "[Gate 7] FR-BP-3: .mpy in romfs, no .py"
APP7="$WORKDIR/hello-cli-gate7"
if (cd "$WORKDIR" && $PICOLET init hello-cli-gate7 --template hello-cli >/dev/null 2>&1) && \
   (cd "$APP7" && $PICOLET build --keep-staging >/dev/null 2>&1); then
    staging="$APP7/target/linux-x64/.picolet-build/romfs"
    if find "$staging" -name "*.mpy" | grep -q . && ! find "$staging" -name "*.py" | grep -q .; then
        pass 7 ".mpy present, no .py in romfs staging"
    else
        fail 7 "staging dir mpy/py check failed; staging=$staging"
    fi
else
    fail 7 "build failed for gate 7"
fi

# ---------------------------------------------------------------------------
# Gate 8 — FR-BP-4: [romfs] include directories bundled.
# ---------------------------------------------------------------------------
echo "[Gate 8] FR-BP-4: [romfs] include directories"
ASSETS_APP="$REPO_ROOT/tests/phase-03/fixtures/hello-cli-with-assets"
if (cd "$ASSETS_APP" && $PICOLET build >/dev/null 2>&1); then
    out=$("$ASSETS_APP/target/linux-x64/hello-cli-with-assets" 2>&1)
    if echo "$out" | grep -q "hello asset"; then
        pass 8 "asset file accessible at runtime"
    else
        fail 8 "expected 'hello asset' in output, got '$out'"
    fi
    # Clean up fixture target dir.
    rm -rf "$ASSETS_APP/target"
else
    fail 8 "build of hello-cli-with-assets failed"
fi

# ---------------------------------------------------------------------------
# Gate 9 — FR-BP-5: trailer path exercised (runtime has no frozen main.py).
# ---------------------------------------------------------------------------
echo "[Gate 9] FR-BP-5: trailer detection (romfs path)"
# Already verified by gate 5: if output is correct, romfs via trailer was used.
out=$("$APP4/target/linux-x64/hello-cli-test" 2>&1)
if [[ "$out" == "Hello from hello-cli-test" ]]; then
    pass 9 "trailer path confirmed (output proves user romfs loaded)"
else
    fail 9 "expected hello output, got '$out'"
fi

# ---------------------------------------------------------------------------
# Gate 10 — FR-BP-6: two builds produce identical output.
# ---------------------------------------------------------------------------
echo "[Gate 10] FR-BP-6: reproducibility"
APP10="$WORKDIR/hello-cli-repro"
(cd "$WORKDIR" && $PICOLET init hello-cli-repro --template hello-cli >/dev/null 2>&1)
(cd "$APP10" && $PICOLET build >/dev/null 2>&1)
cp "$APP10/target/linux-x64/hello-cli-repro" "$WORKDIR/repro1"
rm -rf "$APP10/target"
(cd "$APP10" && $PICOLET build >/dev/null 2>&1)
if cmp "$WORKDIR/repro1" "$APP10/target/linux-x64/hello-cli-repro"; then
    pass 10 "two builds are byte-identical"
else
    fail 10 "builds differ"
fi

# ---------------------------------------------------------------------------
# Gate 11 — Trailer stripped → fallback to empty romfs.
# ---------------------------------------------------------------------------
echo "[Gate 11] Trailer stripped → empty romfs fallback"
no_trailer="$WORKDIR/no-trailer"
cp "$APP4/target/linux-x64/hello-cli-test" "$no_trailer"
truncate -s -24 "$no_trailer"
chmod +x "$no_trailer"
out11=$("$no_trailer" 2>&1 || true)
if [[ -z "$out11" ]]; then
    pass 11 "stripped binary exits 0 with no output (empty romfs)"
else
    fail 11 "expected no output, got '$out11'"
fi

# ---------------------------------------------------------------------------
# Gate 12 — CRC mismatch → loud warning.
# ---------------------------------------------------------------------------
echo "[Gate 12] CRC mismatch warning"
broken="$WORKDIR/broken-crc"
cp "$APP4/target/linux-x64/hello-cli-test" "$broken"
sz=$(stat -c%s "$broken")
printf '\xFF' | dd of="$broken" conv=notrunc bs=1 seek=$((sz - 8)) count=1 2>/dev/null
chmod +x "$broken"
out12=$("$broken" 2>&1 || true)
if echo "$out12" | grep -q "trailer crc mismatch"; then
    pass 12 "CRC mismatch warning emitted"
else
    fail 12 "expected 'trailer crc mismatch' in stderr, got '$out12'"
fi

# ---------------------------------------------------------------------------
# Gate 13a — NFR-1 still holds after amendment.
# ---------------------------------------------------------------------------
echo "[Gate 13a] NFR-1 size gate on amended runtime"
RT_SIZE=$(wc -c < "$RUNTIME")
if [[ $RT_SIZE -le 1048576 ]]; then
    pass 13a "runtime size $RT_SIZE bytes (NFR-1 OK)"
else
    fail 13a "NFR-1 violated: $RT_SIZE bytes"
fi

# ---------------------------------------------------------------------------
# Gate 13b — NFR-8: app binary runs on Ubuntu 22.04.
# ---------------------------------------------------------------------------
echo "[Gate 13b] NFR-8: app runs on Ubuntu 22.04"
APP13="$WORKDIR/hello-cli-test"  # reuse gate-4 app
if docker run --rm \
       -v "$WORKDIR:$WORKDIR" -w "$WORKDIR" \
       ubuntu:22.04 \
       "$APP13/target/linux-x64/hello-cli-test" 2>&1 | grep -q "Hello from hello-cli-test"; then
    pass 13b "Ubuntu 22.04 run succeeded"
else
    fail 13b "Ubuntu 22.04 run failed or wrong output"
fi

# ---------------------------------------------------------------------------
# Gate 14 — Stock runtime tail != "PYLT" magic.
# ---------------------------------------------------------------------------
echo "[Gate 14] Stock runtime tail != trailer magic"
LAST4=$(tail -c 4 "$RUNTIME" | od -An -tx1 | tr -d ' \n')
if [[ "$LAST4" != "50594c54" ]]; then
    pass 14 "stock runtime tail=$LAST4 (not PYLT)"
else
    fail 14 "stock runtime last 4 bytes are PYLT — false-positive risk"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "=== PH03 gate results: $PASS passed, $FAIL failed ==="
if [[ $FAIL -gt 0 ]]; then
    echo "Failed gates: ${FAILED_GATES[*]}"
    exit 1
fi
echo "All gates PASS."
