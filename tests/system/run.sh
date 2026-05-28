#!/usr/bin/env bash
# tests/system/run.sh — picolet.system unit test runner.
#
# Runs the CPython-host unit tests for picolet.system + picolet._system_win
# (the WinBackend → picolet.system event-mapping layer).  The actual C-side
# picolet_winevents.c is exercised only when a real Windows binary runs
# against it — that is the CI compilation gate on the windows variants.

set -euo pipefail

cd "$(dirname "$0")/../.."

python3 -m pytest tests/system/ "$@"
