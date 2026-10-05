#!/usr/bin/env python3
"""Validate and normalise the build recorder's JSON-lines output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def normalise(repo_root: Path, source: Path) -> list[dict[str, Any]]:
    entries = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid command record at {source}:{line_number}: {exc}") from exc
        if not isinstance(entry.get("arguments"), list) or not entry["arguments"]:
            raise ValueError(f"missing compiler arguments at {source}:{line_number}")
        file_path = Path(entry["file"]).resolve()
        try:
            entry["file"] = file_path.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"compiler input is outside the repository: {file_path}") from exc
        entry["directory"] = str(Path(entry["directory"]).resolve())
        entries.append(entry)
    if not entries:
        raise ValueError(f"compiler capture is empty: {source}")
    return sorted(entries, key=lambda entry: (entry["file"], entry["arguments"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        entries = normalise(args.repo_root.resolve(), args.input)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(entries)} compiler commands across {len({entry['file'] for entry in entries})} source files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
