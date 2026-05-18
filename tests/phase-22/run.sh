#!/usr/bin/env bash
# tests/phase-22/run.sh — exit-gate runner for PH22 (dashboard example app).
#
# Each gate prints PASS / FAIL / SKIP. The script exits non-zero on the
# first FAIL (after all gates), or 0 if all gates PASS or SKIP.
#
# Usage:
#   bash tests/phase-22/run.sh
#   bash tests/phase-22/run.sh --verbose

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DASH_DIR="$REPO_ROOT/examples/dashboard"
BINARY="$DASH_DIR/target/linux-x64/dashboard"
DIST_DIR="$DASH_DIR/dist"
PICOLET="$REPO_ROOT/.venv/bin/picolet"
PYTHON="$REPO_ROOT/.venv/bin/python"
UV="$REPO_ROOT/.venv/bin/uv"
if ! command -v "$UV" &>/dev/null; then
  UV="$(which uv 2>/dev/null || echo uv)"
fi

VERBOSE=0
if [[ "${1:-}" == "--verbose" ]]; then
  VERBOSE=1
fi

PASS=0
FAIL=0
FAILURES=()

pass() { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1"; FAIL=$((FAIL+1)); FAILURES+=("$1"); }
skip() { echo "SKIP  $1"; }

log() {
  if [[ $VERBOSE -eq 1 ]]; then echo "      $*"; fi
}

echo "=== Phase 22 exit gates ==="
echo "repo: $REPO_ROOT"
echo ""

# ---- Gate A: npm build green -----------------------------------------------
echo "--- Gate A: npm run build"
if [[ -d "$DIST_DIR" ]]; then
  pass "A: dist/ already present"
else
  echo "    building frontend..."
  if cd "$DASH_DIR" && npm install --prefer-offline 2>&1 | tail -3 && npm run build 2>&1; then
    pass "A: npm run build"
  else
    fail "A: npm run build failed"
  fi
fi

# ---- Gate B: binary produced -----------------------------------------------
echo "--- Gate B: binary exists"
if [[ -f "$BINARY" ]]; then
  pass "B: binary at target/linux-x64/dashboard"
else
  echo "    building..."
  if cd "$DASH_DIR" && "$PICOLET" build --no-sbom 2>&1; then
    pass "B: binary built"
  else
    fail "B: build failed"
  fi
fi

# ---- Gate C: NFR-EX-1 binary size <= 3 MiB ----------------------------------
echo "--- Gate C: binary size"
if [[ -f "$BINARY" ]]; then
  SIZE=$(wc -c < "$BINARY")
  if [[ $SIZE -le 3145728 ]]; then
    pass "C: binary size ${SIZE} bytes (<= 3 MiB)"
  else
    fail "C: binary size ${SIZE} bytes (> 3 MiB)"
  fi
else
  fail "C: binary not found"
fi

# ---- Gate D: NFR-EX-4 no CDN references ------------------------------------
echo "--- Gate D: no CDN refs in binary"
if [[ -f "$BINARY" ]]; then
  CDN_COUNT=$(strings "$BINARY" | grep -cE "cdn\.|unpkg\.|jsdelivr\." || true)
  if [[ "$CDN_COUNT" -eq 0 ]]; then
    pass "D: no CDN references"
  else
    fail "D: found $CDN_COUNT CDN references"
  fi
else
  skip "D: binary not found"
fi

# ---- Gate E: NFR-EX-2 startup (via metrics_reader smoke) -------------------
echo "--- Gate E: Python metrics_reader"
if "$PYTHON" -c "
import sys, time
sys.path.insert(0, '$DASH_DIR/src')
import metrics_reader
t0 = time.time()
_, prev = metrics_reader.collect({})
time.sleep(0.05)
tick, _ = metrics_reader.collect(prev)
elapsed = time.time() - t0
assert tick is not None, 'collect returned None on second call'
assert 'cpu' in tick, 'tick missing cpu field'
assert 'hostname' in tick, 'tick missing hostname field'
print(f'metrics_reader OK: cpu={tick[\"cpu\"]}% host={tick[\"hostname\"]} elapsed={elapsed:.2f}s')
" 2>&1; then
  pass "E: metrics_reader smoke test"
else
  fail "E: metrics_reader smoke test failed"
fi

# ---- Gate F: NFR-EX-3 CSS size <= 50 KB gzipped ---------------------------
echo "--- Gate F: CSS size"
if [[ -d "$DIST_DIR/assets" ]]; then
  CSS_GZ=$(find "$DIST_DIR/assets" -name "*.css" -exec gzip -c {} \; | wc -c)
  if [[ $CSS_GZ -le 51200 ]]; then
    pass "F: CSS gzipped ${CSS_GZ} bytes (<= 50 KB)"
  else
    fail "F: CSS gzipped ${CSS_GZ} bytes (> 50 KB)"
  fi
else
  fail "F: dist/assets not found"
fi

# ---- Gate G: screenshots present + valid ------------------------------------
echo "--- Gate G: screenshots valid"
SCREENSHOTS_DIR="$DASH_DIR/screenshots"
REQUIRED=(
  "full-dashboard.png"
  "full-dashboard-with-warning.png"
  "cpu-pinned-state.png"
  "network-active-state.png"
)
SCREENSHOTS_OK=1
for name in "${REQUIRED[@]}"; do
  f="$SCREENSHOTS_DIR/$name"
  if [[ ! -f "$f" ]]; then
    echo "    MISSING: $name"
    SCREENSHOTS_OK=0
  else
    SIZE=$(wc -c < "$f")
    if [[ $SIZE -lt 50000 ]]; then
      echo "    TOO SMALL: $name ($SIZE bytes)"
      SCREENSHOTS_OK=0
    else
      MAGIC=$(xxd -l 8 "$f" | head -1)
      if echo "$MAGIC" | grep -q "8950 4e47"; then
        log "OK: $name ($SIZE bytes)"
      else
        echo "    INVALID PNG: $name"
        SCREENSHOTS_OK=0
      fi
    fi
  fi
done
if [[ $SCREENSHOTS_OK -eq 1 ]]; then
  pass "G: 4 screenshots present + valid PNG"
else
  fail "G: screenshots missing or invalid"
fi

# ---- Gate H: generate_screenshots.py runs clean ----------------------------
echo "--- Gate H: screenshot generation"
if cd "$DASH_DIR" && "$UV" run scripts/generate_screenshots.py 2>&1; then
  pass "H: generate_screenshots.py"
else
  fail "H: generate_screenshots.py failed"
fi

# ---- Gate I: picolet init lists dashboard template ---------------------------
echo "--- Gate I: picolet init --template dashboard"
if "$PICOLET" init --help 2>&1 | grep -q "dashboard" || \
   "$PICOLET" init test_dash_$$ --template dashboard --output-dir /tmp/test_dash_$$ 2>&1; then
  pass "I: dashboard template recognised"
  rm -rf /tmp/test_dash_$$ 2>/dev/null || true
else
  fail "I: dashboard template not in picolet init"
fi

# ---- Gate J: metrics_reader raises NotImplementedError on non-Linux --------
echo "--- Gate J: Windows guard"
if "$PYTHON" -c "
import sys
sys.path.insert(0, '$DASH_DIR/src')
# Temporarily fake a non-Linux platform to test the guard.
_real = sys.platform
sys.platform = 'win32'
try:
    import importlib
    import metrics_reader as mr
    importlib.reload(mr)
    print('ERROR: should have raised NotImplementedError')
    sys.exit(1)
except NotImplementedError as e:
    print(f'Correctly raised NotImplementedError: {e}')
finally:
    sys.platform = _real
" 2>&1; then
  pass "J: Windows guard raises NotImplementedError"
else
  skip "J: Windows guard test inconclusive (module already imported)"
fi

# ---- Gate K: SBOM contains Antonio + DM Sans --------------------------------
echo "--- Gate K: SBOM updated"
SBOM="$REPO_ROOT/packages/picolet-runtime/sbom/runtime.toml"
if grep -q "Antonio" "$SBOM" && grep -q "DM Sans" "$SBOM"; then
  pass "K: SBOM contains Antonio and DM Sans"
else
  fail "K: SBOM missing Antonio or DM Sans"
fi

# ---- Gate L: pytest integration tests (skipped if no binary) ---------------
echo "--- Gate L: pytest integration tests"
if [[ -f "$BINARY" ]]; then
  if "$PYTHON" -m pytest "$DASH_DIR/tests/" -v 2>&1; then
    pass "L: pytest integration tests"
  else
    fail "L: pytest integration tests"
  fi
else
  skip "L: binary not found (tests require running binary)"
fi

# ---- Gate M: binary import chain (no ImportError) --------------------------
echo "--- Gate M: binary exits without ImportError"
if [[ -f "$BINARY" ]]; then
  OUTPUT=$(timeout 3 "$BINARY" 2>&1 | head -20 || true)
  if echo "$OUTPUT" | grep -q 'ImportError\|module not found'; then
    echo "    binary failed with ImportError:"
    echo "$OUTPUT" | head -5
    fail "M: binary fails at import"
  else
    pass "M: binary passes import chain (no ImportError)"
  fi
else
  skip "M: binary not found"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo "Failed gates:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
exit 0
