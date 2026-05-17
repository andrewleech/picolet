#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Smoke: validate returns errors for invalid document."""
import json
import os
import sys
import tempfile
from pathlib import Path

_CE_SRC = Path(__file__).parent.parent.parent / "examples" / "config-editor" / "src"
sys.path.insert(0, str(_CE_SRC))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = tmp_path / "config-editor"
        schemas_dir = cfg_dir / "schemas"
        schemas_dir.mkdir(parents=True)
        os.environ["PICOLET_CONFIG_DIR"] = str(cfg_dir)

        schema = {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "minimum": 1, "maximum": 65535}
            },
            "required": ["port"],
        }
        (schemas_dir / "test.json").write_text(json.dumps(schema), encoding="utf-8")

        import config_store as s

        # Valid document.
        errors = s.validate("toml", {"port": 8080}, "test")
        assert errors == [], f"expected no errors, got {errors}"

        # Invalid: port out of range.
        errors = s.validate("toml", {"port": 99999}, "test")
        assert len(errors) > 0, "expected validation errors for port 99999"
        assert any("99999" in e["message"] or "maximum" in e["message"] for e in errors), (
            f"expected 'maximum' in error message, got {errors}"
        )

        # Invalid: missing required key.
        errors = s.validate("toml", {"other_key": "value"}, "test")
        assert len(errors) > 0, "expected validation errors for missing port"
        assert any("port" in e["message"] for e in errors), (
            f"expected 'port' in error message, got {errors}"
        )

        print("Validation smoke test: OK")


if __name__ == "__main__":
    main()
