#!/usr/bin/env bash
# tests/phase-23/run.sh — exit-gate runner for PH23 (examples meta + CI).
#
# Each gate prints PASS / FAIL / SKIP. The script exits non-zero on the
# first FAIL (after printing all results), or 0 if all gates PASS or SKIP.
#
# Usage:
#   bash tests/phase-23/run.sh
#   bash tests/phase-23/run.sh --verbose

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PICOLET="$REPO_ROOT/.venv/bin/picolet"
UV="$(which uv 2>/dev/null || echo uv)"

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

echo "=== Phase 23 exit gates ==="
echo "repo: $REPO_ROOT"
echo ""

# ---- Gate A: mirror script exits 0 (no drift) ---------------------------------
echo "--- Gate A: mirror script --check exits 0"
if bash "$REPO_ROOT/scripts/mirror-examples-to-templates.sh" --check 2>&1; then
  pass "A: mirror --check exits 0 (no drift)"
else
  fail "A: mirror --check reports drift (re-run without --check to reconcile)"
fi

# ---- Gate B: picolet init --list-templates output --------------------------------
echo ""
echo "--- Gate B: picolet init --list-templates"
if [[ ! -x "$PICOLET" ]]; then
  skip "B: picolet not found at $PICOLET"
else
  TEMPLATES_OUT=$("$PICOLET" init --list-templates 2>/dev/null)
  EXPECTED="config-editor
dashboard
hello-cli
hello-lvgl
hello-vue
hello-webview
notes
pydfu"
  if [[ "$TEMPLATES_OUT" == "$EXPECTED" ]]; then
    pass "B: --list-templates prints 8 templates alphabetically"
  else
    fail "B: --list-templates output mismatch"
    log "got:"
    log "$TEMPLATES_OUT"
    log "expected:"
    log "$EXPECTED"
  fi
fi

# ---- Gate B2: picolet init scaffolds each real template --------------------------
echo ""
echo "--- Gate B2: picolet init with each real template"
TMPDIR_INIT="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_INIT"' EXIT

for TMPL in pydfu notes config-editor dashboard; do
  APP_NAME="test-${TMPL//-/_}"
  OUT_DIR="$TMPDIR_INIT/$APP_NAME"
  if [[ ! -x "$PICOLET" ]]; then
    skip "B2/$TMPL: picolet not installed"
    continue
  fi
  if "$PICOLET" init "$APP_NAME" --template "$TMPL" --output-dir "$OUT_DIR" 2>/dev/null; then
    # Check that picolet.toml has the correct app name.
    TOML_NAME=$(grep '^name' "$OUT_DIR/picolet.toml" | head -1 | sed 's/name = "\(.*\)"/\1/')
    PKG_NAME=$(python3 -c "import json; d=json.load(open('$OUT_DIR/package.json')); print(d['name'])" 2>/dev/null || echo "")
    if [[ "$TOML_NAME" == "$APP_NAME" && "$PKG_NAME" == "$APP_NAME" ]]; then
      pass "B2/$TMPL: name substituted correctly in picolet.toml and package.json"
    else
      fail "B2/$TMPL: name mismatch — toml=$TOML_NAME pkg=$PKG_NAME expected=$APP_NAME"
    fi
    # Special check for dashboard: title should be preserved.
    if [[ "$TMPL" == "dashboard" ]]; then
      TITLE=$(grep '^title' "$OUT_DIR/picolet.toml" | head -1 | sed 's/title = "\(.*\)"/\1/')
      if [[ "$TITLE" == "System Dashboard" ]]; then
        pass "B2/dashboard: title preserved as 'System Dashboard'"
      else
        fail "B2/dashboard: title='$TITLE', expected 'System Dashboard'"
      fi
    fi
  else
    fail "B2/$TMPL: picolet init failed"
  fi
done

# ---- Gate C: examples/README.md exists and references all examples ------------
echo ""
echo "--- Gate C: examples/README.md"
EXAMPLES_README="$REPO_ROOT/examples/README.md"
if [[ ! -f "$EXAMPLES_README" ]]; then
  fail "C: examples/README.md does not exist"
else
  MISSING=()
  for EXAMPLE in pydfu notes config-editor dashboard; do
    if ! grep -q "$EXAMPLE" "$EXAMPLES_README"; then
      MISSING+=("$EXAMPLE")
    fi
  done
  # Check for screenshot image links.
  if ! grep -q 'screenshots/' "$EXAMPLES_README"; then
    MISSING+=("screenshot links")
  fi
  if [[ ${#MISSING[@]} -eq 0 ]]; then
    pass "C: examples/README.md references all examples and screenshots"
  else
    fail "C: examples/README.md missing references: ${MISSING[*]}"
  fi
fi

# ---- Gate D: docs/examples.md exists and covers all examples ------------------
echo ""
echo "--- Gate D: docs/examples.md"
DOCS_EXAMPLES="$REPO_ROOT/docs/examples.md"
if [[ ! -f "$DOCS_EXAMPLES" ]]; then
  fail "D: docs/examples.md does not exist"
else
  MISSING=()
  for EXAMPLE in pydfu notes config-editor dashboard; do
    if ! grep -q "$EXAMPLE" "$DOCS_EXAMPLES"; then
      MISSING+=("$EXAMPLE")
    fi
  done
  # Check for at least one code snippet per example (```python blocks).
  CODE_BLOCKS=$(grep -c '```python' "$DOCS_EXAMPLES" 2>/dev/null || echo 0)
  if [[ "$CODE_BLOCKS" -lt 4 ]]; then
    MISSING+=("code snippets (found $CODE_BLOCKS, need >= 4)")
  fi
  if [[ ${#MISSING[@]} -eq 0 ]]; then
    pass "D: docs/examples.md covers all examples with code snippets"
  else
    fail "D: docs/examples.md missing: ${MISSING[*]}"
  fi
fi

# ---- Gate E: screenshots.yml parses via yamllint ------------------------------
echo ""
echo "--- Gate E: screenshots.yml yamllint"
SCREENSHOTS_YML="$REPO_ROOT/.github/workflows/screenshots.yml"
if [[ ! -f "$SCREENSHOTS_YML" ]]; then
  fail "E: .github/workflows/screenshots.yml does not exist"
elif ! command -v yamllint &>/dev/null; then
  skip "E: yamllint not installed (pip install yamllint)"
else
  if yamllint -d relaxed "$SCREENSHOTS_YML" 2>&1; then
    pass "E: screenshots.yml passes yamllint"
  else
    fail "E: screenshots.yml fails yamllint"
  fi
fi

# ---- Gate E2: release.yml has screenshots-release job -------------------------
echo ""
echo "--- Gate E2: release.yml screenshots-release job"
RELEASE_YML="$REPO_ROOT/.github/workflows/release.yml"
if [[ ! -f "$RELEASE_YML" ]]; then
  fail "E2: .github/workflows/release.yml does not exist"
else
  if grep -q "screenshots-release" "$RELEASE_YML" && grep -q "needs: build" "$RELEASE_YML"; then
    pass "E2: release.yml has screenshots-release job with needs: build"
  else
    fail "E2: release.yml missing screenshots-release job or needs: build"
  fi
  # Verify the PR opened for screenshots does NOT have enable-auto-merge flag.
  if grep -q "enable-auto-merge\|--auto-merge" "$RELEASE_YML"; then
    fail "E2: release.yml uses --auto-merge or enable-auto-merge (must remain OFF)"
  fi
fi

# ---- Gate F: root README.md links resolve -------------------------------------
echo ""
echo "--- Gate F: root README.md contains 2x2 thumbnail grid"
ROOT_README="$REPO_ROOT/README.md"
if [[ ! -f "$ROOT_README" ]]; then
  fail "F: README.md does not exist"
else
  MISSING=()
  for EXAMPLE in pydfu notes config-editor dashboard; do
    if ! grep -q "examples/$EXAMPLE/screenshots/" "$ROOT_README"; then
      MISSING+=("$EXAMPLE screenshot link")
    fi
  done
  if ! grep -q "examples/README.md\|examples/" "$ROOT_README"; then
    MISSING+=("link to examples/")
  fi
  if [[ ${#MISSING[@]} -eq 0 ]]; then
    pass "F: root README.md has 2x2 thumbnail grid referencing all examples"
  else
    fail "F: root README.md missing: ${MISSING[*]}"
  fi
fi

# ---- Gate F2: screenshot image files exist ------------------------------------
echo ""
echo "--- Gate F2: screenshot PNG files present"
MISSING_PNGS=()
for EXAMPLE in pydfu notes config-editor dashboard; do
  SS_DIR="$REPO_ROOT/examples/$EXAMPLE/screenshots"
  COUNT=$(find "$SS_DIR" -name "*.png" 2>/dev/null | wc -l)
  if [[ "$COUNT" -eq 0 ]]; then
    MISSING_PNGS+=("$EXAMPLE")
  else
    log "$EXAMPLE: $COUNT PNG(s) in screenshots/"
  fi
done
if [[ ${#MISSING_PNGS[@]} -eq 0 ]]; then
  pass "F2: all examples have screenshots"
else
  fail "F2: no screenshots found in: ${MISSING_PNGS[*]}"
fi

# ---- Summary ------------------------------------------------------------------
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [[ $FAIL -gt 0 ]]; then
  echo "Failed gates:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
exit 0
