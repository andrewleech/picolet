#!/usr/bin/env bash
# MicroPython test gate for picolet_tui.
#
# Runs the portable subset of tests/system/tui under the unix-port
# picolet-tui MicroPython binary, importing picolet_tui from the
# source tree (not the frozen copy), so a source edit is testable
# without rebuilding the runtime.
#
# The harness tests (test_harness_*.py) stay CPython-only: they drive
# the *built binary* from outside via a PTY and depend on host-side
# picolet.testing.
#
# The frozen .mpy compile step only proves the source parses; this
# gate is what actually executes framework code on MicroPython.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MP="$REPO/packages/picolet-runtime/micropython/ports/unix/build-picolet-tui/micropython"

if [[ ! -x "$MP" ]]; then
    echo "MicroPython tui binary missing — building it first..."
    bash "$REPO/packages/picolet-runtime/scripts/build-runtime.sh" --target linux-x64 --variant tui
fi

# Source tree before .frozen so edits to picolet_tui are picked up
# without a runtime rebuild.  .frozen still needed for asyncio,
# functools, __future__ etc.
export MICROPYPATH="$REPO/packages/picolet-runtime/python:$REPO/tests/mp:.frozen"

# Portable test files: everything in tests/system/tui except the
# CPython-only PTY/harness drivers.
mapfile -t TEST_FILES < <(ls "$REPO"/tests/system/tui/test_*.py | grep -v '/test_harness_')

exec "$MP" -m pytest "${TEST_FILES[@]}"
