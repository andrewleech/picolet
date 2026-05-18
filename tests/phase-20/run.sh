#!/usr/bin/env bash
# tests/phase-20/run.sh — exit-gate runner for PH20 (notes example app).
#
# Each gate prints PASS / FAIL / SKIP. The script exits non-zero on the
# first FAIL, or 0 if all gates PASS or SKIP.
#
# Usage:
#   bash tests/phase-20/run.sh
#   bash tests/phase-20/run.sh --verbose

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NOTES_DIR="$REPO_ROOT/examples/notes"
BINARY="$NOTES_DIR/target/linux-x64/notes"
DIST_DIR="$NOTES_DIR/dist"
PICOLET="$REPO_ROOT/.venv/bin/picolet"
PYTHON="$REPO_ROOT/.venv/bin/python"

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

echo "=== Phase 20 exit gates ==="
echo "repo: $REPO_ROOT"
echo ""

# ---- Gate A: picolet validate exits 0 ----------------------------------------
echo "--- Gate A: picolet validate"
if cd "$NOTES_DIR" && "$PICOLET" validate 2>&1 | ([ $VERBOSE -eq 1 ] && cat || cat > /dev/null); then
  pass "A: picolet validate"
else
  fail "A: picolet validate"
fi

# ---- Gate B: binary produced -----------------------------------------------
echo "--- Gate B: binary exists"
if [[ -f "$BINARY" ]]; then
  pass "B: binary at target/linux-x64/notes"
else
  echo "    building..."
  if cd "$NOTES_DIR" && "$PICOLET" build --no-sbom 2>&1; then
    pass "B: binary built"
  else
    fail "B: build failed"
  fi
fi

# ---- Gate C: NFR-EX-1 binary size ≤ 3 MiB ---------------------------------
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

# ---- Gate E: NFR-EX-2 startup ≤ 1500 ms (via smoke_list_notes) ---------------
echo "--- Gate E: startup time (smoke_list_notes)"
if [[ -f "$BINARY" ]]; then
  if "$PYTHON" "$REPO_ROOT/tests/phase-20/smoke_list_notes.py" 2>&1; then
    pass "E: startup + IPC round-trip OK"
  else
    fail "E: smoke_list_notes failed"
  fi
else
  skip "E: binary not found"
fi

# ---- Gate F: list_notes IPC round-trip -------------------------------------
echo "--- Gate F: list_notes IPC"
if [[ -f "$BINARY" ]]; then
  if "$PYTHON" "$REPO_ROOT/tests/phase-20/smoke_list_notes.py" 2>&1; then
    pass "F: list_notes IPC"
  else
    fail "F: list_notes IPC"
  fi
else
  skip "F: binary not found"
fi

# ---- Gate G: CRUD cycle + FS verification ----------------------------------
echo "--- Gate G: CRUD cycle"
if [[ -f "$BINARY" ]]; then
  if "$PYTHON" "$REPO_ROOT/tests/phase-20/smoke_crud.py" 2>&1; then
    pass "G: CRUD cycle"
  else
    fail "G: CRUD cycle"
  fi
else
  skip "G: binary not found"
fi

# ---- Gate H: screenshots present + valid -----------------------------------
echo "--- Gate H: screenshots valid"
SCREENSHOTS_DIR="$NOTES_DIR/screenshots"
REQUIRED=("list-empty.png" "list-populated.png" "edit-pristine.png" "edit-unsaved.png" "edit-typing-mid.png" "search-active.png")
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
      # Check PNG magic bytes
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
  pass "H: 6 screenshots present + valid PNG"
else
  fail "H: screenshots missing or invalid"
fi

# ---- Gate I: pytest integration tests --------------------------------------
echo "--- Gate I: pytest integration tests"
if [[ -f "$BINARY" ]]; then
  if "$PYTHON" -m pytest "$NOTES_DIR/tests/" -v 2>&1; then
    pass "I: pytest integration tests"
  else
    fail "I: pytest integration tests"
  fi
else
  skip "I: binary not found (tests require running binary)"
fi

# ---- Gate J: NFR-EX-3 CSS ≤ 50 KB gzipped ---------------------------------
echo "--- Gate J: CSS size"
if [[ -d "$DIST_DIR/assets" ]]; then
  CSS_GZ=$(find "$DIST_DIR/assets" -name "*.css" -exec gzip -c {} \; | wc -c)
  if [[ $CSS_GZ -le 51200 ]]; then
    pass "J: CSS gzipped ${CSS_GZ} bytes (≤ 50 KB)"
  else
    fail "J: CSS gzipped ${CSS_GZ} bytes (> 50 KB)"
  fi
else
  # Build dist first
  if cd "$NOTES_DIR" && npm run build 2>&1 > /dev/null; then
    CSS_GZ=$(find "$DIST_DIR/assets" -name "*.css" -exec gzip -c {} \; | wc -c)
    if [[ $CSS_GZ -le 51200 ]]; then
      pass "J: CSS gzipped ${CSS_GZ} bytes (≤ 50 KB)"
    else
      fail "J: CSS gzipped ${CSS_GZ} bytes (> 50 KB)"
    fi
  else
    fail "J: could not build dist/"
  fi
fi

# ---- Gate K: Source Serif 4 in binary strings --------------------------------
echo "--- Gate K: Source Serif 4 in binary"
if [[ -f "$BINARY" ]]; then
  SERIF_COUNT=$(strings "$BINARY" | grep -c "Source Serif" || true)
  if [[ "$SERIF_COUNT" -gt 0 ]]; then
    pass "K: 'Source Serif' found in binary ($SERIF_COUNT occurrences)"
  else
    fail "K: 'Source Serif' not found in binary"
  fi
else
  skip "K: binary not found"
fi

# ---- Gate L: picolet init --template notes scaffolds + builds -----------------
echo "--- Gate L: template init + build"
TMPDIR_L=$(mktemp -d)
trap "rm -rf $TMPDIR_L" EXIT
APP_NAME="test-notes-$(date +%s)"
if (
  cd "$TMPDIR_L"
  "$PICOLET" init "$APP_NAME" --template notes 2>&1
  cd "$APP_NAME"
  npm install --prefer-offline 2>&1 | tail -3
  "$PICOLET" validate 2>&1
  "$PICOLET" build --no-sbom 2>&1
  test -f "target/linux-x64/$APP_NAME"
); then
  pass "L: template init + build"
else
  fail "L: template init + build"
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

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
