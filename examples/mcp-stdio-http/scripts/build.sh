#!/usr/bin/env bash
# Build with a locally built Windows x64 CLI runtime.
# Usage: build.sh /path/to/runtime.exe [EXPECTED_SHA256]
# The Picolet CLI and mpy-cross must match the runtime's MicroPython build.
set -euo pipefail
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 /path/to/runtime.exe [EXPECTED_SHA256]" >&2
  exit 2
fi
runtime=$1
# Override only when developing the runtime; normal builds use the verified pin.
expected_sha256=${2:-c2843411abe30b678c2a29dab1383827e35395b9ad9b4dec3e92b78ac785871e}
if [ ! -s "$runtime" ]; then
  echo "error: runtime missing or empty: $runtime" >&2
  exit 1
fi
if [[ ! "$expected_sha256" =~ ^[[:xdigit:]]{64}$ ]]; then
  echo "error: expected runtime SHA-256 must be 64 hexadecimal characters" >&2
  exit 2
fi
runtime="$(cd "$(dirname "$runtime")" && pwd -P)/$(basename "$runtime")"
actual_sha256=$(sha256sum "$runtime")
actual_sha256=${actual_sha256%% *}
if [ "${actual_sha256,,}" != "${expected_sha256,,}" ]; then
  echo "error: runtime SHA-256 mismatch: $runtime" >&2
  echo "expected: $expected_sha256; actual: $actual_sha256" >&2
  exit 1
fi
cd "$(dirname "$0")/.."
picolet build --target windows-x64 --runtime "$runtime"
exe=target/windows-x64/mcp-stdio-http.exe
test -s "$exe"
sha256sum "$exe"
ls -l "$exe"
