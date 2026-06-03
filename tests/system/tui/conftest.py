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

# Append the runtime path AFTER the host so `import picolet_tui` (top-level
# module living only under the runtime tree) resolves, but `import picolet`
# still hits the host CLI's package first.  Append, not insert: we want host
# `picolet` to win, runtime `picolet_tui` (a distinct top-level name) to be
# found by the path fall-through.
if _RUNTIME_PATH not in sys.path:
    sys.path.append(_RUNTIME_PATH)

# Evict any cached `picolet` modules that came from the runtime tree so
# the very next `import picolet.testing` resolves against the host CLI.
for _mod_name in list(sys.modules):
    if _mod_name == "picolet" or _mod_name.startswith("picolet."):
        if not _mod_name.startswith(("picolet_cli", "picolet_ui", "picolet_tui")):
            del sys.modules[_mod_name]


# pytest-asyncio mode: the test files in this directory mix sync and async
# tests.  Mark `asyncio_mode = "auto"` here would force every test into the
# event loop; instead we leave it strict (the default) and each async test
# carries its own `@pytest.mark.asyncio` decorator.  The conftest documents
# the install step in case pytest-asyncio is missing from the environment:
#
#     uv sync         # picks up pytest-asyncio>=1.3.0 from pyproject.toml's
#                     # [dependency-groups].dev table; or
#     pip install 'pytest-asyncio>=1.3.0'
#
# The harness PTY test (test_harness_pty.py) skips automatically when the
# picolet-tui binary is not available; the rest of the tests are pure-Python
# and run on any host.


def pytest_configure(config):
    """Register custom markers used by tests in this directory.

    ``requires_binary`` flags tests that need the compiled picolet-tui
    runtime artefact.  Registering the marker here silences pytest's
    PytestUnknownMarkWarning when the test file declares it via
    ``pytestmark = pytest.mark.requires_binary``.  The actual skip-on-
    absence logic lives in test_harness_pty.py itself; this hook only
    teaches pytest the marker name exists.
    """
    config.addinivalue_line(
        "markers",
        "requires_binary: test requires the compiled picolet-tui binary",
    )
