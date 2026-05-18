#!/usr/bin/env bash
# tests/phase-24/run.sh
#
# PH24 runner-info verification script.
#
# Usage:
#   bash tests/phase-24/run.sh [--artifact-dir <dir>]
#
# If macos-runner-info-*.txt artifacts are present (downloaded from the
# GitHub Actions CI run), this script parses them and prints the OS version
# and architecture for each runner.
#
# If no artifacts are found, the script exits 0 with an explanatory message —
# the artifacts only exist after a CI workflow run.
#
# To obtain the artifacts:
#   1. Push to dev and trigger the release workflow manually (or push a
#      runtime-v* tag).
#   2. In the Actions run, find the "build-macos" jobs.
#   3. Download the "macos-runner-info-macos-13" and
#      "macos-runner-info-macos-14" artifacts.
#   4. Place the extracted .txt files in a directory.
#   5. Run: bash tests/phase-24/run.sh --artifact-dir <dir>

set -euo pipefail

ARTIFACT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --artifact-dir)
            ARTIFACT_DIR="$2"; shift 2 ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "usage: $0 [--artifact-dir <dir>]" >&2
            exit 1 ;;
    esac
done

# Default search: look in the current directory and the script's own directory.
if [[ -z "$ARTIFACT_DIR" ]]; then
    ARTIFACT_DIR="$(dirname "${BASH_SOURCE[0]}")"
fi

mapfile -t ARTIFACTS < <(find "$ARTIFACT_DIR" -name "macos-runner-info*.txt" 2>/dev/null | sort)

if [[ "${#ARTIFACTS[@]}" -eq 0 ]]; then
    echo "SKIP: no macos-runner-info-*.txt artifacts found in $ARTIFACT_DIR"
    echo ""
    echo "To verify PH24:"
    echo "  1. Push to dev and trigger the release workflow manually:"
    echo "       gh workflow run release.yml --ref dev"
    echo "     Or push a runtime-v* tag."
    echo "  2. Download the macos-runner-info-macos-13 and"
    echo "     macos-runner-info-macos-14 artifacts from the Actions run."
    echo "  3. Re-run: bash tests/phase-24/run.sh --artifact-dir <dir>"
    exit 0
fi

echo "=== PH24 macOS runner info ==="
PASS=0
FAIL=0

for artifact in "${ARTIFACTS[@]}"; do
    echo ""
    echo "--- $artifact ---"
    cat "$artifact"

    # Extract OS version from sw_vers output.
    os_version="$(grep -A1 'sw_vers' "$artifact" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true)"
    if [[ -z "$os_version" ]]; then
        # Try ProductVersion line directly.
        os_version="$(grep 'ProductVersion' "$artifact" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true)"
    fi

    arch="$(grep -A1 'uname -m' "$artifact" | tail -1 | tr -d '[:space:]' || true)"
    clang_path="$(grep -A1 'which clang' "$artifact" | tail -1 | tr -d '[:space:]' || true)"

    if [[ -n "$os_version" ]]; then
        echo ""
        echo "  OS version:  $os_version"
        echo "  arch:        $arch"
        echo "  clang path:  $clang_path"

        # Assert macOS >= 11.0 (NFR-MAC-9).
        major="$(echo "$os_version" | cut -d. -f1)"
        if [[ "$major" -ge 11 ]]; then
            echo "  NFR-MAC-9:   OK (macOS $os_version >= 11.0)"
            PASS=$((PASS + 1))
        else
            echo "  NFR-MAC-9:   FAIL (macOS $os_version < 11.0 — below deployment target)" >&2
            FAIL=$((FAIL + 1))
        fi
    else
        echo "  WARNING: could not parse OS version from $artifact"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
