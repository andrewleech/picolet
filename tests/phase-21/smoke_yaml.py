#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Smoke: YAML load → modify → save round-trip via config_store directly."""
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
        cfg_dir.mkdir()
        os.environ["PICOLET_CONFIG_DIR"] = str(cfg_dir)

        import config_store as s

        yaml_file = tmp_path / "smoke.yaml"
        yaml_file.write_text(
            "server:\n  host: localhost\n  port: 8080\n",
            encoding="utf-8",
        )

        result = s.load(str(yaml_file))
        assert result["format"] == "yaml", f"unexpected format: {result}"
        doc = result["document"]
        assert doc["server"]["port"] == 8080, f"unexpected port: {doc}"

        doc["server"]["port"] = 9090
        save_result = s.save(str(yaml_file), "yaml", doc)
        assert save_result["ok"], f"save failed: {save_result}"

        content = yaml_file.read_text(encoding="utf-8")
        assert "9090" in content, f"expected 9090 in {content!r}"
        assert "8080" not in content, f"8080 still in {content!r}"

        diff = save_result["diff"]
        assert any("9090" in line for line in diff), f"no 9090 in diff: {diff}"

        print("YAML round-trip: OK")


if __name__ == "__main__":
    main()
