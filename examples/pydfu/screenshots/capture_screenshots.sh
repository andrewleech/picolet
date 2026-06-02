#!/usr/bin/env bash
# Captures all six pydfu screenshots via AppHarness Python scripts.
# Requires: binary built, Xvfb available (or DISPLAY set).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SHOTS="$REPO_ROOT/examples/pydfu/screenshots"
BINARY="$REPO_ROOT/examples/pydfu/target/linux-x64/pydfu"

if [ ! -f "$BINARY" ]; then
    echo "ERROR: binary not found at $BINARY; run 'picolet build --no-sbom' first."
    exit 1
fi

PICOLET_PYDFU_MOCK=1 uv run --project "$REPO_ROOT/packages/picolet" \
    python3 "$SHOTS/scripts/device_list_empty.py"

PICOLET_PYDFU_MOCK=1 uv run --project "$REPO_ROOT/packages/picolet" \
    python3 "$SHOTS/scripts/device_list_populated.py"

PICOLET_PYDFU_MOCK=1 uv run --project "$REPO_ROOT/packages/picolet" \
    python3 "$SHOTS/scripts/flash_start.py"

PICOLET_PYDFU_MOCK=1 uv run --project "$REPO_ROOT/packages/picolet" \
    python3 "$SHOTS/scripts/flash_mid_progress.py"

PICOLET_PYDFU_MOCK=1 uv run --project "$REPO_ROOT/packages/picolet" \
    python3 "$SHOTS/scripts/flash_complete.py"

PICOLET_PYDFU_MOCK=1 uv run --project "$REPO_ROOT/packages/picolet" \
    python3 "$SHOTS/scripts/flash_error.py"

echo "All screenshots captured in $SHOTS/"
ls -lh "$SHOTS"/*.png 2>/dev/null || echo "(no PNG files present yet)"
