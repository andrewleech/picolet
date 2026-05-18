#!/usr/bin/env bash
# tests/phase-21/run.sh — exit-gate runner for PH21 (config-editor example app).
#
# Each gate prints PASS / FAIL / SKIP. The script exits non-zero on the
# first FAIL, or 0 if all gates PASS or SKIP.
#
# Usage:
#   bash tests/phase-21/run.sh
#   bash tests/phase-21/run.sh --verbose

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CE_DIR="$REPO_ROOT/examples/config-editor"
BINARY="$CE_DIR/target/linux-x64/config-editor"
DIST_DIR="$CE_DIR/dist"
PICOLET="$REPO_ROOT/.venv/bin/picolet"
PYTHON="$REPO_ROOT/.venv/bin/python"
PHASE_DIR="$REPO_ROOT/tests/phase-21"

VERBOSE=0
if [[ "${1:-}" == "--verbose" ]]; then
  VERBOSE=1
fi

PASS=0
FAIL=0

pass() { echo "PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL  $1"; FAIL=$((FAIL+1)); }
skip() { echo "SKIP  $1"; }

log() {
  if [[ $VERBOSE -eq 1 ]]; then echo "      $*"; fi
}

echo "=== Phase 21 exit gates ==="
echo "repo: $REPO_ROOT"
echo ""

# ---- Gate A: picolet validate exits 0 ----------------------------------------
echo "--- Gate A: picolet validate"
if cd "$CE_DIR" && "$PICOLET" validate 2>&1 | ([ $VERBOSE -eq 1 ] && cat || cat > /dev/null); then
  pass "A: picolet validate"
else
  fail "A: picolet validate"
fi

# ---- Gate B: binary produced -----------------------------------------------
echo "--- Gate B: binary exists"
if [[ -f "$BINARY" ]]; then
  pass "B: binary at target/linux-x64/config-editor"
else
  echo "    building..."
  if cd "$CE_DIR" && "$PICOLET" build --no-sbom 2>&1; then
    pass "B: binary built"
  else
    fail "B: build failed"
  fi
fi

# ---- Gate C: NFR-EX-1 binary size ≤ 3 MiB ----------------------------------
echo "--- Gate C: binary size"
if [[ -f "$BINARY" ]]; then
  SIZE=$(wc -c < "$BINARY")
  if [[ $SIZE -le 3145728 ]]; then
    pass "C: binary size ${SIZE} bytes (≤ 3 MiB)"
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

# ---- Gate E: NFR-EX-2 startup ≤ 1500 ms (via smoke_toml) -------------------
echo "--- Gate E: startup time (smoke_toml)"
if [[ -f "$BINARY" ]]; then
  if "$PYTHON" "$PHASE_DIR/smoke_toml.py" 2>&1; then
    pass "E: startup + IPC round-trip OK"
  else
    fail "E: smoke_toml failed"
  fi
else
  skip "E: binary not found"
fi

# ---- Gate F: TOML round-trip -----------------------------------------------
echo "--- Gate F: TOML load+save round-trip"
if "$PYTHON" "$PHASE_DIR/smoke_toml.py" 2>&1; then
  pass "F: TOML round-trip"
else
  fail "F: TOML round-trip"
fi

# ---- Gate G: YAML round-trip -----------------------------------------------
echo "--- Gate G: YAML load+save round-trip"
if "$PYTHON" "$PHASE_DIR/smoke_yaml.py" 2>&1; then
  pass "G: YAML round-trip"
else
  fail "G: YAML round-trip"
fi

# ---- Gate H: JSON round-trip -----------------------------------------------
echo "--- Gate H: JSON load+save round-trip"
if "$PYTHON" "$PHASE_DIR/smoke_json.py" 2>&1; then
  pass "H: JSON round-trip"
else
  fail "H: JSON round-trip"
fi

# ---- Gate I: validate returns errors for invalid doc -----------------------
echo "--- Gate I: validation errors"
if "$PYTHON" "$PHASE_DIR/smoke_validate.py" 2>&1; then
  pass "I: validate errors for invalid doc"
else
  fail "I: validate errors for invalid doc"
fi

# ---- Gate J: diff returned on save -----------------------------------------
echo "--- Gate J: unified diff on save"
if "$PYTHON" "$PHASE_DIR/smoke_diff.py" 2>&1; then
  pass "J: diff returned on save"
else
  fail "J: diff returned on save"
fi

# ---- Gate K: screenshots present + valid ------------------------------------
echo "--- Gate K: screenshots valid"
SCREENSHOTS_DIR="$CE_DIR/screenshots"
REQUIRED=("file-picker.png" "edit-toml.png" "edit-yaml-with-errors.png" "diff-add.png" "diff-delete.png")
SCREENSHOTS_OK=1
for name in "${REQUIRED[@]}"; do
  f="$SCREENSHOTS_DIR/$name"
  if [[ ! -f "$f" ]]; then
    echo "    MISSING: $name"
    SCREENSHOTS_OK=0
  else
    SIZE=$(wc -c < "$f")
    if [[ $SIZE -lt 1024 ]]; then
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
  pass "K: 5 screenshots present + valid PNG"
else
  fail "K: screenshots missing or invalid"
fi

# ---- Gate L: pytest integration tests (skipped if no running binary) --------
echo "--- Gate L: pytest integration tests"
if [[ -f "$BINARY" ]]; then
  if "$PYTHON" -m pytest "$CE_DIR/tests/" -v 2>&1; then
    pass "L: pytest integration tests"
  else
    fail "L: pytest integration tests"
  fi
else
  skip "L: binary not found (tests require running binary)"
fi

# ---- Gate M: NFR-EX-3 CSS ≤ 50 KB gzipped ----------------------------------
echo "--- Gate M: CSS size"
if [[ -d "$DIST_DIR/assets" ]]; then
  CSS_GZ=$(find "$DIST_DIR/assets" -name "*.css" -exec gzip -c {} \; | wc -c)
  if [[ $CSS_GZ -le 51200 ]]; then
    pass "M: CSS gzipped ${CSS_GZ} bytes (≤ 50 KB)"
  else
    fail "M: CSS gzipped ${CSS_GZ} bytes (> 50 KB)"
  fi
else
  if cd "$CE_DIR" && npm run build 2>&1 > /dev/null; then
    CSS_GZ=$(find "$DIST_DIR/assets" -name "*.css" -exec gzip -c {} \; | wc -c)
    if [[ $CSS_GZ -le 51200 ]]; then
      pass "M: CSS gzipped ${CSS_GZ} bytes (≤ 50 KB)"
    else
      fail "M: CSS gzipped ${CSS_GZ} bytes (> 50 KB)"
    fi
  else
    fail "M: could not build dist/"
  fi
fi

# ---- Gate N: JetBrains Mono in binary strings --------------------------------
echo "--- Gate N: JetBrains Mono in binary"
if [[ -f "$BINARY" ]]; then
  JB_COUNT=$(strings "$BINARY" | grep -c "JetBrains" || true)
  if [[ "$JB_COUNT" -gt 0 ]]; then
    pass "N: 'JetBrains' found in binary ($JB_COUNT occurrences)"
  else
    fail "N: 'JetBrains' not found in binary"
  fi
else
  skip "N: binary not found"
fi

# ---- Gate O: template init + build -----------------------------------------
echo "--- Gate O: template init + build"
TMPDIR_O=$(mktemp -d)
trap "rm -rf $TMPDIR_O" EXIT
APP_NAME="test-ce-$(date +%s)"
if (
  cd "$TMPDIR_O"
  "$PICOLET" init "$APP_NAME" --template config-editor 2>&1
  cd "$APP_NAME"
  npm install --prefer-offline 2>&1 | tail -3
  "$PICOLET" validate 2>&1
  "$PICOLET" build --no-sbom 2>&1
  test -f "target/linux-x64/$APP_NAME"
); then
  pass "O: template init + build"
else
  fail "O: template init + build"
fi

# ---- Gate P: binary import chain (no ImportError) --------------------------
echo "--- Gate P: binary exits without ImportError"
if [[ -f "$BINARY" ]]; then
  OUTPUT=$(timeout 3 "$BINARY" 2>&1 | head -20 || true)
  if echo "$OUTPUT" | grep -q 'ImportError\|module not found'; then
    echo "    binary failed with ImportError:"
    echo "$OUTPUT" | head -5
    fail "P: binary fails at import"
  else
    pass "P: binary passes import chain (no ImportError)"
  fi
else
  skip "P: binary not found"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
