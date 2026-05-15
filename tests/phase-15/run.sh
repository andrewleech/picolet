#!/usr/bin/env bash
# tests/phase-15/run.sh — PH15 exit gate verification harness.
#
# Tests:
#   A. YAML lint of .github/workflows/release.yml.
#   B. Matrix shape: assert exactly 6 jobs (3 variants × 2 targets).
#   C. Structural assertions: trigger pattern, permissions, required steps.
#   D. Regression: PH00-PH14 still green (invokes phase-14/run.sh).
#
# Usage:
#   cd /home/anl/picolet
#   bash tests/phase-15/run.sh [--skip-regression] [--verbose]
#
# Flags:
#   --skip-regression   Skip the PH14 regression call.
#   --verbose           Print extra detail.
#
# Prerequisites:
#   - python3 with PyYAML  (pip install pyyaml  or  uv add pyyaml)
#   - python3 with tomllib (stdlib ≥ 3.11; or pip install tomli for 3.10)
#
# Exit: 0 if all gates pass; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKFLOW="$REPO_ROOT/.github/workflows/release.yml"

PASS=0
FAIL=0
SKIP=0
VERBOSE=0
SKIP_REGRESSION=0

for arg in "$@"; do
    case "$arg" in
        --skip-regression) SKIP_REGRESSION=1 ;;
        --verbose)         VERBOSE=1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pass() { echo "  PASS: $*"; PASS=$(( PASS + 1 )); }
fail() { echo "  FAIL: $*" >&2; FAIL=$(( FAIL + 1 )); }
skip() { echo "  SKIP: $*"; SKIP=$(( SKIP + 1 )); }
verbose() { [[ "$VERBOSE" -eq 1 ]] && echo "       $*" || true; }

# ---------------------------------------------------------------------------
# Gate A: YAML lint
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate A: YAML lint ==="

if [[ ! -f "$WORKFLOW" ]]; then
    fail "release.yml not found: $WORKFLOW"
else
    verbose "workflow path: $WORKFLOW"

    # Use python3 + yaml to parse the file; any syntax error exits non-zero.
    if python3 -c "
import sys
try:
    import yaml
except ImportError:
    print('PyYAML not installed; install with: pip install pyyaml', file=sys.stderr)
    sys.exit(2)
with open('$WORKFLOW') as f:
    doc = yaml.safe_load(f)
if not isinstance(doc, dict):
    print('error: YAML root is not a mapping', file=sys.stderr)
    sys.exit(1)
print('YAML parsed OK; root keys:', list(doc.keys()))
" 2>&1; then
        pass "release.yml is valid YAML"
    else
        fail "release.yml failed YAML parse"
    fi
fi

# ---------------------------------------------------------------------------
# Gate B: matrix shape (3 variants × 2 targets = 6 jobs)
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate B: matrix shape ==="

python3 - "$WORKFLOW" <<'PYEOF'
import sys
import yaml

path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f)

jobs = doc.get("jobs", {})
build_job = jobs.get("build")
if build_job is None:
    print("FAIL: no 'build' job found in workflow", file=sys.stderr)
    sys.exit(1)

strategy = build_job.get("strategy", {})
matrix = strategy.get("matrix", {})
targets = matrix.get("target", [])
variants = matrix.get("variant", [])

expected_targets = sorted(["linux-x64", "windows-x64"])
expected_variants = sorted(["cli", "webview", "lvgl"])

errors = []
if sorted(targets) != expected_targets:
    errors.append(f"targets mismatch: got {sorted(targets)}, want {expected_targets}")
if sorted(variants) != expected_variants:
    errors.append(f"variants mismatch: got {sorted(variants)}, want {expected_variants}")

n_jobs = len(targets) * len(variants)
if n_jobs != 6:
    errors.append(f"matrix produces {n_jobs} jobs, want 6")

if errors:
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)

print(f"  matrix: {len(targets)} targets × {len(variants)} variants = {n_jobs} jobs")
print(f"  targets: {targets}")
print(f"  variants: {variants}")
PYEOF

if [[ $? -eq 0 ]]; then
    pass "matrix shape: 3 variants × 2 targets = 6 jobs"
else
    fail "matrix shape incorrect"
fi

# ---------------------------------------------------------------------------
# Gate C: structural assertions
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate C: structural assertions ==="

python3 - "$WORKFLOW" <<'PYEOF'
import sys
import yaml

path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f)

errors = []
warnings = []

# C1: trigger — tag push on runtime-v*
on_block = doc.get("on", {})
if not on_block:
    on_block = doc.get(True, {})  # yaml parses bare 'on' as True in some versions
push = on_block.get("push", {}) if isinstance(on_block, dict) else {}
tags = push.get("tags", [])
if not any("runtime-v*" in str(t) for t in tags):
    errors.append("trigger: no 'runtime-v*' tag pattern in on.push.tags")
else:
    print("  C1: trigger pattern 'runtime-v*' present")

# C2: permissions — contents: write
perms = doc.get("permissions", {})
if perms.get("contents") != "write":
    errors.append("permissions.contents is not 'write'")
else:
    print("  C2: permissions.contents: write")

# C3: build job exists with matrix
jobs = doc.get("jobs", {})
build_job = jobs.get("build", {})
if not build_job:
    errors.append("no 'build' job")
else:
    strat = build_job.get("strategy", {})
    if not strat.get("matrix"):
        errors.append("build job has no strategy.matrix")
    elif strat.get("fail-fast") is not False:
        warnings.append("build job strategy.fail-fast is not false (partial failures won't be visible)")
    print("  C3: build job with matrix present")

# C4: release job exists and needs build
release_job = jobs.get("release", {})
if not release_job:
    errors.append("no 'release' job")
else:
    needs = release_job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    if "build" not in needs:
        errors.append("release job does not 'needs: build'")
    else:
        print("  C4: release job 'needs: build'")

# C5: build job has checkout step
build_steps = build_job.get("steps", [])
has_checkout = any(
    isinstance(s.get("uses", ""), str) and "actions/checkout" in s.get("uses", "")
    for s in build_steps
)
if not has_checkout:
    errors.append("build job has no actions/checkout step")
else:
    print("  C5: build job has checkout step")

# C6: build job has build-runtime.sh invocation
has_build_script = any(
    "build-runtime.sh" in str(s.get("run", ""))
    for s in build_steps
)
if not has_build_script:
    errors.append("build job has no build-runtime.sh invocation")
else:
    print("  C6: build job invokes build-runtime.sh")

# C7: build job has sha256 computation
has_sha256 = any(
    "sha256sum" in str(s.get("run", ""))
    for s in build_steps
)
if not has_sha256:
    errors.append("build job has no sha256sum step")
else:
    print("  C7: build job computes SHA256 sidecar")

# C8: build job uploads artifacts
has_upload = any(
    isinstance(s.get("uses", ""), str) and "upload-artifact" in s.get("uses", "")
    for s in build_steps
)
if not has_upload:
    errors.append("build job has no actions/upload-artifact step")
else:
    print("  C8: build job uploads artifacts")

# C9: release job uploads to GitHub Release
release_steps = release_job.get("steps", [])
has_gh_upload = any(
    "gh release upload" in str(s.get("run", ""))
    for s in release_steps
)
if not has_gh_upload:
    errors.append("release job has no 'gh release upload' step")
else:
    print("  C9: release job calls 'gh release upload'")

# C10: matrix axis names are exactly 'target' and 'variant'
matrix = build_job.get("strategy", {}).get("matrix", {})
if set(matrix.keys()) != {"target", "variant"}:
    errors.append(f"matrix axes are {set(matrix.keys())}, expected {{'target', 'variant'}}")
else:
    print("  C10: matrix axes are 'target' and 'variant'")

for w in warnings:
    print(f"  WARN: {w}")

if errors:
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

if [[ $? -eq 0 ]]; then
    pass "structural assertions: all 10 checks pass"
else
    fail "structural assertions failed"
fi

# ---------------------------------------------------------------------------
# Gate D: RUNTIME_TAG format
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate D: RUNTIME_TAG ==="

RUNTIME_TAG_FILE="$REPO_ROOT/packages/picolet-runtime/RUNTIME_TAG"
if [[ ! -f "$RUNTIME_TAG_FILE" ]]; then
    fail "RUNTIME_TAG not found: $RUNTIME_TAG_FILE"
else
    TAG_VAL="$(cat "$RUNTIME_TAG_FILE" | tr -d '[:space:]')"
    verbose "RUNTIME_TAG: $TAG_VAL"
    if echo "$TAG_VAL" | grep -qE '^runtime-v[0-9]+\.[0-9]+\.[0-9]+'; then
        pass "RUNTIME_TAG '$TAG_VAL' matches runtime-v* pattern"
    else
        fail "RUNTIME_TAG '$TAG_VAL' does not match runtime-v<semver> pattern"
    fi
fi

# ---------------------------------------------------------------------------
# Gate E: RELEASING.md exists
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate E: RELEASING.md ==="

RELEASING_MD="$REPO_ROOT/RELEASING.md"
if [[ ! -f "$RELEASING_MD" ]]; then
    fail "RELEASING.md not found"
else
    RELEASING_LINES="$(wc -l < "$RELEASING_MD")"
    verbose "RELEASING.md: $RELEASING_LINES lines"
    if [[ "$RELEASING_LINES" -lt 20 ]]; then
        fail "RELEASING.md is suspiciously short ($RELEASING_LINES lines)"
    else
        pass "RELEASING.md exists ($RELEASING_LINES lines)"
    fi
fi

# ---------------------------------------------------------------------------
# Gate F: dry-run act check (if act is available)
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate F: act dry-run (if available) ==="

if command -v act > /dev/null 2>&1; then
    verbose "act found; running dry-run list"
    if act --list -W "$WORKFLOW" 2>/dev/null | grep -q "build"; then
        pass "act --list shows build job in workflow"
    else
        fail "act --list did not show expected jobs"
    fi
else
    skip "act not installed; skipping dry-run (install from https://github.com/nektos/act)"
fi

# ---------------------------------------------------------------------------
# Gate G: PH00-PH14 regression
# ---------------------------------------------------------------------------
echo ""
echo "=== Gate G: PH00-PH14 regression ==="

if [[ "$SKIP_REGRESSION" -eq 1 ]]; then
    skip "regression skipped via --skip-regression"
else
    PH14_HARNESS="$REPO_ROOT/tests/phase-14/run.sh"
    if [[ ! -f "$PH14_HARNESS" ]]; then
        skip "phase-14/run.sh not found; skipping regression"
    else
        echo "  Running phase-14/run.sh --skip-integration --skip-windows ..."
        if bash "$PH14_HARNESS" --skip-integration --skip-windows 2>&1; then
            pass "PH00-PH14 regression: phase-14 harness passes"
        else
            fail "PH00-PH14 regression: phase-14 harness failed"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 15 test summary ==="
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
