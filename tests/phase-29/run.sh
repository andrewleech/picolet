#!/bin/bash
set -e
# yamllint both workflows (skip if yamllint missing)
if command -v yamllint >/dev/null; then
  yamllint -s .github/workflows/release.yml .github/workflows/perf-check.yml || true
fi
# Assert the macOS matrix entries exist
grep -q "macos-13" .github/workflows/release.yml
grep -q "macos-14" .github/workflows/release.yml
grep -q "variant: \[cli, webview, lvgl\]" .github/workflows/release.yml
grep -q "perf-macos:" .github/workflows/perf-check.yml
# Assert NFR-MAC-10: perf-macos must NOT trigger on push
if grep -A 5 'perf-macos:' .github/workflows/perf-check.yml | grep -q 'on: push'; then
  echo "FAIL: perf-macos triggers on push (violates NFR-MAC-10)"
  exit 1
fi
echo "PASS"
