#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Smoke: save returns non-empty diff with + and - prefixed lines."""
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

        toml_file = tmp_path / "diff_test.toml"
        toml_file.write_text('[server]\nhost = "localhost"\nport = 8080\n', encoding="utf-8")

        result = s.load(str(toml_file))
        doc = result["document"]
        doc["server"]["port"] = 9090

        save_result = s.save(str(toml_file), "toml", doc)
        diff = save_result["diff"]

        assert len(diff) > 0, "expected non-empty diff"
        assert any(line.startswith("+") and "+++" not in line for line in diff), (
            f"no + diff lines found in: {diff}"
        )
        assert any(line.startswith("-") and "---" not in line for line in diff), (
            f"no - diff lines found in: {diff}"
        )
        assert any("9090" in line for line in diff), f"no 9090 in diff: {diff}"
        assert any("8080" in line for line in diff), f"no 8080 in diff: {diff}"

        print("Diff smoke test: OK")
        print("Diff lines:")
        for line in diff:
            print(f"  {line!r}")


if __name__ == "__main__":
    main()
