"""conftest.py — pytest fixtures for config-editor integration tests.

PICOLET_CONFIG_DIR is set to a temp directory containing a pre-written TOML
fixture and a matching schema. The real config_store.py runs against this
directory so host FS state can be asserted directly.
"""
import json
import pytest
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "config-editor"


@pytest.fixture
async def config_dir(tmp_path):
    d = tmp_path / "config-editor"
    schemas_d = d / "schemas"
    schemas_d.mkdir(parents=True)
    # Write a minimal TOML fixture.
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(
        '[server]\nhost = "localhost"\nport = 8080\n',
        encoding="utf-8",
    )
    # Write a matching schema.
    schema = {
        "type": "object",
        "properties": {
            "server": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                },
                "required": ["host", "port"],
            }
        },
    }
    (schemas_d / "test.json").write_text(json.dumps(schema), encoding="utf-8")
    return d, toml_file


@pytest.fixture
async def harness(config_dir):
    cfg_base, _ = config_dir
    h = AppHarness(
        str(BINARY),
        env={"PICOLET_CONFIG_DIR": str(cfg_base)},
    )
    await h.start()
    yield h
    await h.stop()
