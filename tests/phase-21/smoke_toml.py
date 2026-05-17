#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Smoke: TOML load → modify → save round-trip via config_store directly."""
import os
import sys
import tempfile
from pathlib import Path

# Add config-editor src to path for direct testing without running the binary.
_CE_SRC = Path(__file__).parent.parent.parent / "examples" / "config-editor" / "src"
sys.path.insert(0, str(_CE_SRC))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = tmp_path / "config-editor"
        cfg_dir.mkdir()
        os.environ["PICOLET_CONFIG_DIR"] = str(cfg_dir)

        import config_store as s

        toml_file = tmp_path / "smoke.toml"
        toml_file.write_text('[app]\nversion = "1.0"\n', encoding="utf-8")

        result = s.load(str(toml_file))
        assert result["format"] == "toml", f"unexpected format: {result}"
        doc = result["document"]
        assert doc == {"app": {"version": "1.0"}}, f"unexpected doc: {doc}"

        doc["app"]["version"] = "2.0"
        save_result = s.save(str(toml_file), "toml", doc)
        assert save_result["ok"], f"save failed: {save_result}"

        content = toml_file.read_text(encoding="utf-8")
        assert "2.0" in content, f"expected 2.0 in {content!r}"
        assert "1.0" not in content, f"1.0 still in {content!r}"

        # Verify diff contains the version change.
        diff = save_result["diff"]
        assert any("2.0" in line for line in diff), f"no 2.0 in diff: {diff}"
        assert any(line.startswith("+") for line in diff), f"no + lines in diff"

        print("TOML round-trip: OK")


if __name__ == "__main__":
    main()
