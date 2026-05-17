"""notes_store.py — host-filesystem note storage.

Storage path (in priority order):
  1. PICOLET_NOTES_DIR env var (test isolation)
  2. Linux: $XDG_CONFIG_HOME/notes/ or ~/.config/notes/
  3. Windows: %APPDATA%\\notes\\

Note file format:
  Filename: <slug>-<unix-ts>.md
  Content:  YAML-lite front matter (title/created/updated) + blank line + body
"""
import os
import sys
import re
import time
from pathlib import Path


def _notes_dir() -> Path:
    """Resolve the notes storage directory.

    Test isolation: set PICOLET_NOTES_DIR env var to override.
    """
    override = os.environ.get("PICOLET_NOTES_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if not base:
            raise RuntimeError("APPDATA not set on Windows")
        p = Path(base) / "notes"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        p = base / "notes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _make_slug(title: str) -> str:
    """Derive a URL-safe slug from a title."""
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r" +", "-", s)
    s = s[:40].rstrip("-")
    return s or "note"


def _parse_note(text: str) -> dict:
    """Return {"title": str, "created": int, "updated": int, "body": str}."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            header = text[4:end]
            body = text[end + 5:]
            meta: dict = {}
            for line in header.splitlines():
                if ": " in line:
                    k, _, v = line.partition(": ")
                    meta[k.strip()] = v.strip()
            return {
                "title": meta.get("title", "Untitled"),
                "created": int(meta.get("created", 0)),
                "updated": int(meta.get("updated", 0)),
                "body": body.lstrip("\n"),
            }
    return {"title": "Untitled", "created": 0, "updated": 0, "body": text}


def _render_note(title: str, created: int, updated: int, body: str) -> str:
    return f"---\ntitle: {title}\ncreated: {created}\nupdated: {updated}\n---\n\n{body}"


def list_notes() -> list:
    """Return list of note metadata dicts sorted by updated desc."""
    d = _notes_dir()
    notes = []
    for f in d.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            m = _parse_note(text)
            slug = f.stem  # filename without .md
            notes.append({
                "slug": slug,
                "title": m["title"],
                "created": m["created"],
                "updated": m["updated"],
            })
        except Exception:
            pass  # skip malformed files silently
    notes.sort(key=lambda n: n["updated"], reverse=True)
    return notes


def load_note(slug: str) -> dict:
    """Return full note dict (slug, title, created, updated, body)."""
    d = _notes_dir()
    f = d / f"{slug}.md"
    if not f.exists():
        raise FileNotFoundError(f"note not found: {slug}")
    text = f.read_text(encoding="utf-8")
    m = _parse_note(text)
    m["slug"] = slug
    return m


def save_note(slug: str, body: str) -> dict:
    """Overwrite body; update `updated` timestamp. Returns updated metadata."""
    d = _notes_dir()
    f = d / f"{slug}.md"
    if not f.exists():
        raise FileNotFoundError(f"note not found: {slug}")
    old = _parse_note(f.read_text(encoding="utf-8"))
    now = int(time.time())
    f.write_text(
        _render_note(old["title"], old["created"], now, body),
        encoding="utf-8",
    )
    return {
        "slug": slug,
        "title": old["title"],
        "created": old["created"],
        "updated": now,
    }


def rename_note(slug: str, title: str) -> dict:
    """Update only the title field in front matter; body unchanged."""
    d = _notes_dir()
    f = d / f"{slug}.md"
    if not f.exists():
        raise FileNotFoundError(f"note not found: {slug}")
    old = _parse_note(f.read_text(encoding="utf-8"))
    now = int(time.time())
    f.write_text(
        _render_note(title, old["created"], now, old["body"]),
        encoding="utf-8",
    )
    return {
        "slug": slug,
        "title": title,
        "created": old["created"],
        "updated": now,
    }


def create_note(title: str) -> dict:
    """Create a new note; return its metadata."""
    d = _notes_dir()
    now = int(time.time())
    base_slug = _make_slug(title)
    slug = f"{base_slug}-{now}"
    f = d / f"{slug}.md"
    # Handle slug collision (same title, same second).
    counter = 1
    while f.exists():
        slug = f"{base_slug}-{now}-{counter}"
        f = d / f"{slug}.md"
        counter += 1
    f.write_text(
        _render_note(title, now, now, ""),
        encoding="utf-8",
    )
    return {"slug": slug, "title": title, "created": now, "updated": now}


def delete_note(slug: str) -> None:
    """Delete a note file. Raises FileNotFoundError if not found."""
    d = _notes_dir()
    f = d / f"{slug}.md"
    if not f.exists():
        raise FileNotFoundError(f"note not found: {slug}")
    f.unlink()
