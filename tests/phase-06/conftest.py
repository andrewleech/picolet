"""pytest conftest for phase-06 tests.

test_dispatcher.py imports ``picolet`` directly; on the host that module lives
in ``packages/picolet-runtime/python/`` (it is frozen into the runtime binary,
not installed as a host package).  This conftest adds that path to sys.path
so that ``pytest tests/phase-06/`` works without a manual PYTHONPATH export.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "picolet-runtime" / "python"))
