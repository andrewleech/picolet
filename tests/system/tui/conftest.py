"""pytest conftest for tests/system/tui/.

Unlike its sibling tests/system/conftest.py, this directory drives a host-
side harness (`picolet.testing.TuiHarness`) that lives in the CLI host
package at packages/picolet/picolet/, NOT the runtime frozen sources at
packages/picolet-runtime/python/picolet/.  The parent system/conftest.py
swaps `picolet` over to the runtime path on collection — which has no
`.testing` submodule, breaking the import.

We undo that swap here at this directory's collection time: drop the
runtime path back out of sys.path and re-prime the host-CLI `picolet`
module from packages/picolet/.  Pytest then proceeds to collect the
test files against the right namespace.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_RUNTIME_PATH = str(_REPO_ROOT / "packages" / "picolet-runtime" / "python")
_HOST_PATH = str(_REPO_ROOT / "packages" / "picolet")

# Remove the runtime path so it does not shadow the host CLI.
while _RUNTIME_PATH in sys.path:
    sys.path.remove(_RUNTIME_PATH)

# Ensure the host CLI package is on the path.
if _HOST_PATH not in sys.path:
    sys.path.insert(0, _HOST_PATH)

# Evict any cached `picolet` modules that came from the runtime tree so
# the very next `import picolet.testing` resolves against the host CLI.
for _mod_name in list(sys.modules):
    if _mod_name == "picolet" or _mod_name.startswith("picolet."):
        if not _mod_name.startswith(("picolet_cli", "picolet_ui", "picolet_tui")):
            del sys.modules[_mod_name]
