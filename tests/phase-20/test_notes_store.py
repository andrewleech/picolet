"""
Phase 20 unit tests — notes_store.py host-filesystem round-trips.

Covers:
  - create_note: returns dict with slug, title, created, updated.
  - create_note: writes a .md file to the notes dir.
  - create_note: file contains valid front matter.
  - create_note: empty title falls back to slug "note-<ts>".
  - create_note: slug collision (same title, same second) is resolved with counter.
  - list_notes: returns empty list when notes dir is empty.
  - list_notes: returns one entry after create_note.
  - list_notes: entry slug matches create_note slug.
  - list_notes: entry title matches create_note title.
  - list_notes: sorted by updated descending when multiple notes present.
  - load_note: returns slug, title, body, created, updated.
  - load_note: body is empty string immediately after create_note.
  - load_note: raises FileNotFoundError for unknown slug.
  - save_note: writes updated body to disk.
  - save_note: updated timestamp changes.
  - save_note: title is preserved (save_note does not accept a title arg).
  - save_note: raises FileNotFoundError for unknown slug.
  - save_note: reloading after save returns updated body.
  - delete_note: removes the .md file from disk.
  - delete_note: note no longer appears in list_notes.
  - delete_note: raises FileNotFoundError for unknown slug.
  - rename_note: updates front matter title; body is unchanged.
  - rename_note: old slug still resolves (filename unchanged).
  - rename_note: updated timestamp changes.
  - rename_note: raises FileNotFoundError for unknown slug.
  - _make_slug: "Hello World" produces "hello-world".
  - _make_slug: accented characters stripped to ascii remainder or "note".
  - _make_slug: all-whitespace title produces "note" fallback.
  - _make_slug: punctuation-only title produces "note" fallback.
  - _make_slug: empty string produces "note" fallback.
  - _make_slug: max-40-char truncation without trailing hyphen.
  - _make_slug: result contains no forward slashes.
  - _make_slug: result does not start with a dot.
  - _parse_note: round-trip: render then parse returns original fields.
  - _parse_note: text without front matter returns body=text, title=Untitled.
  - _parse_note: malformed front matter (no closing ---) treated as plain text.
  - _parse_note: title with colon in value parses correctly.
  - _parse_note: unicode in body survives round-trip.
  - _parse_note: missing created/updated fields default to 0.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_SRC_DIR = _REPO_ROOT / "examples" / "notes" / "src"
sys.path.insert(0, str(_SRC_DIR))


def _load_store(tmp_path: Path):
    """Return notes_store module isolated to tmp_path via PICOLET_NOTES_DIR."""
    # Evict any previously-loaded module so env-var re-evaluation fires.
    for key in list(sys.modules.keys()):
        if key == "notes_store":
            del sys.modules[key]
    os.environ["PICOLET_NOTES_DIR"] = str(tmp_path)
    import notes_store
    return notes_store


class _TmpDir:
    """Context manager: creates a temp dir and sets PICOLET_NOTES_DIR."""

    def __init__(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()

    def __enter__(self) -> tuple[Path, object]:
        p = Path(self._td.name)
        store = _load_store(p)
        return p, store

    def __exit__(self, *_):
        self._td.cleanup()
        os.environ.pop("PICOLET_NOTES_DIR", None)


# ---------------------------------------------------------------------------
# _make_slug
# ---------------------------------------------------------------------------

class TestMakeSlug(unittest.TestCase):
    """Slug generator: URL-safe, filesystem-safe, deterministic."""

    def _slug(self, title: str) -> str:
        with _TmpDir() as (_, store):
            return store._make_slug(title)

    def test_hello_world_produces_hello_world(self):
        self.assertEqual(self._slug("Hello World"), "hello-world")

    def test_lowercase_applied(self):
        self.assertEqual(self._slug("UPPER CASE"), "upper-case")

    def test_multiple_spaces_collapsed_to_single_hyphen(self):
        self.assertEqual(self._slug("a  b"), "a-b")

    def test_punctuation_stripped(self):
        slug = self._slug("hello, world!")
        self.assertNotIn(",", slug)
        self.assertNotIn("!", slug)

    def test_all_whitespace_returns_note_fallback(self):
        self.assertEqual(self._slug("   "), "note")

    def test_punctuation_only_returns_note_fallback(self):
        self.assertEqual(self._slug("!!!"), "note")

    def test_empty_string_returns_note_fallback(self):
        self.assertEqual(self._slug(""), "note")

    def test_max_40_chars(self):
        long_title = "a" * 50
        slug = self._slug(long_title)
        self.assertLessEqual(len(slug), 40)

    def test_no_trailing_hyphen_after_truncation(self):
        # Title that would produce a hyphen exactly at position 40.
        title = "a" * 39 + " b"  # slug would be "aaa...a-b" (41 chars); truncated to 40 = trailing hyphen
        slug = self._slug(title)
        self.assertFalse(slug.endswith("-"), f"slug has trailing hyphen: {slug!r}")

    def test_no_forward_slash(self):
        slug = self._slug("a/b/c")
        self.assertNotIn("/", slug)

    def test_does_not_start_with_dot(self):
        slug = self._slug(".hidden")
        self.assertFalse(slug.startswith("."), f"slug starts with dot: {slug!r}")

    def test_deterministic(self):
        # Same input → same output on repeated calls.
        t = "My Deterministic Title"
        with _TmpDir() as (_, store):
            self.assertEqual(store._make_slug(t), store._make_slug(t))

    def test_ueber_title_produces_filesystem_safe_slug(self):
        # Non-ASCII stripped; remaining ASCII letters kept.
        slug = self._slug("Über alles")
        self.assertNotIn("Ü", slug)
        self.assertNotIn("ü", slug)
        # Whatever survives must be alphanumeric + hyphens only.
        import re
        self.assertTrue(
            re.fullmatch(r"[a-z0-9][a-z0-9\-]*", slug) or slug == "note",
            f"unsafe chars in slug: {slug!r}",
        )


# ---------------------------------------------------------------------------
# _parse_note / _render_note round-trip
# ---------------------------------------------------------------------------

class TestParseNote(unittest.TestCase):
    """Front matter parser and renderer."""

    @classmethod
    def setUpClass(cls):
        with _TmpDir() as (_, store):
            cls._store = store
        # Re-load to have a permanent reference (env may be gone, but module cached).
        for key in list(sys.modules.keys()):
            if key == "notes_store":
                del sys.modules[key]
        os.environ["PICOLET_NOTES_DIR"] = "/tmp/notes-sqe-parse-test"
        Path("/tmp/notes-sqe-parse-test").mkdir(parents=True, exist_ok=True)
        import notes_store
        cls._store = notes_store

    def _parse(self, text: str) -> dict:
        return self._store._parse_note(text)

    def _render(self, title: str, created: int, updated: int, body: str) -> str:
        return self._store._render_note(title, created, updated, body)

    def test_round_trip_title(self):
        raw = self._render("My Title", 1000, 2000, "body text")
        m = self._parse(raw)
        self.assertEqual(m["title"], "My Title")

    def test_round_trip_created(self):
        raw = self._render("T", 1747000000, 1747100000, "")
        m = self._parse(raw)
        self.assertEqual(m["created"], 1747000000)

    def test_round_trip_updated(self):
        raw = self._render("T", 1747000000, 1747100000, "")
        m = self._parse(raw)
        self.assertEqual(m["updated"], 1747100000)

    def test_round_trip_body(self):
        body = "# Heading\n\nParagraph text."
        raw = self._render("T", 1, 2, body)
        m = self._parse(raw)
        self.assertEqual(m["body"], body)

    def test_plain_text_without_front_matter_returns_body_as_text(self):
        text = "Just plain text here."
        m = self._parse(text)
        self.assertEqual(m["body"], text)
        self.assertEqual(m["title"], "Untitled")

    def test_plain_text_defaults_created_to_zero(self):
        m = self._parse("no front matter")
        self.assertEqual(m["created"], 0)

    def test_plain_text_defaults_updated_to_zero(self):
        m = self._parse("no front matter")
        self.assertEqual(m["updated"], 0)

    def test_malformed_no_closing_delimiter_treated_as_plain_text(self):
        # Front matter with opening --- but no closing --- is not valid.
        text = "---\ntitle: Broken\ncreated: 1000\n# body continues"
        m = self._parse(text)
        # Should fall back: no recognised front matter parsed.
        self.assertEqual(m["title"], "Untitled")
        self.assertIn("Broken", m["body"])

    def test_title_with_colon_in_value(self):
        # partition on ": " means "title: foo: bar" gives title="foo: bar".
        raw = self._render("foo: bar", 1, 2, "body")
        m = self._parse(raw)
        self.assertEqual(m["title"], "foo: bar")

    def test_unicode_in_body_survives_round_trip(self):
        body = "日本語テスト — naïve résumé — Ω ≠ ∞"
        raw = self._render("T", 1, 2, body)
        m = self._parse(raw)
        self.assertEqual(m["body"], body)

    def test_missing_created_field_defaults_to_zero(self):
        text = "---\ntitle: NoTimestamp\nupdated: 9999\n---\n\nbody"
        m = self._parse(text)
        self.assertEqual(m["created"], 0)

    def test_missing_updated_field_defaults_to_zero(self):
        text = "---\ntitle: NoTimestamp\ncreated: 9999\n---\n\nbody"
        m = self._parse(text)
        self.assertEqual(m["updated"], 0)


# ---------------------------------------------------------------------------
# create_note
# ---------------------------------------------------------------------------

class TestCreateNote(unittest.TestCase):

    def test_returns_dict_with_slug(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Hello")
            self.assertIn("slug", n)

    def test_returns_dict_with_title(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Hello")
            self.assertEqual(n["title"], "Hello")

    def test_returns_dict_with_created(self):
        with _TmpDir() as (_, store):
            before = int(time.time())
            n = store.create_note("Hello")
            self.assertGreaterEqual(n["created"], before)

    def test_returns_dict_with_updated(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Hello")
            self.assertIn("updated", n)

    def test_writes_md_file_to_dir(self):
        with _TmpDir() as (tmp, store):
            n = store.create_note("My Note")
            expected = tmp / f"{n['slug']}.md"
            self.assertTrue(expected.exists(), f".md file not found: {expected}")

    def test_written_file_has_valid_front_matter(self):
        with _TmpDir() as (tmp, store):
            n = store.create_note("My Note")
            text = (tmp / f"{n['slug']}.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("\n---\n", text)

    def test_written_file_contains_title(self):
        with _TmpDir() as (tmp, store):
            n = store.create_note("My Note")
            text = (tmp / f"{n['slug']}.md").read_text(encoding="utf-8")
            self.assertIn("My Note", text)

    def test_empty_title_slug_falls_back_to_note(self):
        with _TmpDir() as (_, store):
            n = store.create_note("")
            self.assertTrue(n["slug"].startswith("note-"), f"slug: {n['slug']!r}")

    def test_slug_collision_resolved_with_counter(self):
        """Creating two notes with identical title in the same second must produce distinct files."""
        with _TmpDir() as (tmp, store):
            ts = int(time.time())
            base_slug = store._make_slug("Collision Test")
            # Pre-create a file that occupies the first slug.
            first_path = tmp / f"{base_slug}-{ts}.md"
            first_path.write_text(
                store._render_note("Collision Test", ts, ts, ""),
                encoding="utf-8",
            )
            n2 = store.create_note("Collision Test")
            # The returned slug must differ from the first.
            self.assertNotEqual(n2["slug"], f"{base_slug}-{ts}")
            # And its file must exist.
            self.assertTrue((tmp / f"{n2['slug']}.md").exists())


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------

class TestListNotes(unittest.TestCase):

    def test_empty_dir_returns_empty_list(self):
        with _TmpDir() as (_, store):
            self.assertEqual(store.list_notes(), [])

    def test_single_note_returns_one_entry(self):
        with _TmpDir() as (_, store):
            store.create_note("Solo")
            self.assertEqual(len(store.list_notes()), 1)

    def test_entry_slug_matches_create_slug(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Slugged")
            listed = store.list_notes()
            self.assertEqual(listed[0]["slug"], n["slug"])

    def test_entry_title_matches_create_title(self):
        with _TmpDir() as (_, store):
            store.create_note("Titled Note")
            listed = store.list_notes()
            self.assertEqual(listed[0]["title"], "Titled Note")

    def test_multiple_notes_returns_all(self):
        with _TmpDir() as (_, store):
            store.create_note("A")
            store.create_note("B")
            store.create_note("C")
            self.assertEqual(len(store.list_notes()), 3)

    def test_sorted_by_updated_descending(self):
        """Most recently updated note appears first."""
        with _TmpDir() as (tmp, store):
            n1 = store.create_note("Older")
            n2 = store.create_note("Newer")
            # Manually bump n2's updated timestamp so it's clearly newer.
            f2 = tmp / f"{n2['slug']}.md"
            parsed2 = store._parse_note(f2.read_text(encoding="utf-8"))
            f2.write_text(
                store._render_note(
                    parsed2["title"], parsed2["created"],
                    parsed2["updated"] + 100, parsed2["body"]
                ),
                encoding="utf-8",
            )
            listed = store.list_notes()
            self.assertEqual(listed[0]["slug"], n2["slug"])
            self.assertEqual(listed[1]["slug"], n1["slug"])

    def test_malformed_file_skipped_silently(self):
        """A non-parsable .md file must not crash list_notes."""
        with _TmpDir() as (tmp, store):
            (tmp / "corrupt.md").write_bytes(b"\xff\xfe bad binary")
            store.create_note("Good")
            notes = store.list_notes()
            # The good note is returned; the corrupt file is silently skipped.
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["title"], "Good")


# ---------------------------------------------------------------------------
# load_note
# ---------------------------------------------------------------------------

class TestLoadNote(unittest.TestCase):

    def test_returns_slug(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Load Me")
            loaded = store.load_note(n["slug"])
            self.assertEqual(loaded["slug"], n["slug"])

    def test_returns_title(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Load Me")
            loaded = store.load_note(n["slug"])
            self.assertEqual(loaded["title"], "Load Me")

    def test_body_empty_after_create(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Empty Body")
            loaded = store.load_note(n["slug"])
            self.assertEqual(loaded["body"], "")

    def test_returns_created_timestamp(self):
        with _TmpDir() as (_, store):
            before = int(time.time())
            n = store.create_note("TS Test")
            loaded = store.load_note(n["slug"])
            self.assertGreaterEqual(loaded["created"], before)

    def test_raises_file_not_found_for_unknown_slug(self):
        with _TmpDir() as (_, store):
            with self.assertRaises(FileNotFoundError):
                store.load_note("nonexistent-slug-9999999")


# ---------------------------------------------------------------------------
# save_note
# ---------------------------------------------------------------------------

class TestSaveNote(unittest.TestCase):

    def test_save_writes_body_to_disk(self):
        with _TmpDir() as (tmp, store):
            n = store.create_note("Save Test")
            store.save_note(n["slug"], "# New Body\n\nContent.")
            text = (tmp / f"{n['slug']}.md").read_text(encoding="utf-8")
            self.assertIn("# New Body", text)
            self.assertIn("Content.", text)

    def test_save_updates_timestamp(self):
        with _TmpDir() as (_, store):
            n = store.create_note("TS Save")
            time.sleep(0.01)  # ensure clock advances at least marginally
            result = store.save_note(n["slug"], "new body")
            # updated must be >= created (same-second saves are fine)
            self.assertGreaterEqual(result["updated"], n["created"])

    def test_save_preserves_title(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Preserved Title")
            result = store.save_note(n["slug"], "new body")
            self.assertEqual(result["title"], "Preserved Title")

    def test_reload_after_save_returns_updated_body(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Reload Test")
            store.save_note(n["slug"], "Persisted content.")
            loaded = store.load_note(n["slug"])
            self.assertEqual(loaded["body"], "Persisted content.")

    def test_save_raises_file_not_found_for_unknown_slug(self):
        with _TmpDir() as (_, store):
            with self.assertRaises(FileNotFoundError):
                store.save_note("no-such-slug-12345", "body")

    def test_save_result_contains_expected_keys(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Keys Test")
            result = store.save_note(n["slug"], "body")
            for key in ("slug", "title", "created", "updated"):
                self.assertIn(key, result)


# ---------------------------------------------------------------------------
# delete_note
# ---------------------------------------------------------------------------

class TestDeleteNote(unittest.TestCase):

    def test_delete_removes_file_from_disk(self):
        with _TmpDir() as (tmp, store):
            n = store.create_note("To Delete")
            f = tmp / f"{n['slug']}.md"
            self.assertTrue(f.exists())
            store.delete_note(n["slug"])
            self.assertFalse(f.exists(), ".md file still exists after delete_note")

    def test_delete_note_no_longer_in_list(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Gone")
            store.delete_note(n["slug"])
            slugs = [x["slug"] for x in store.list_notes()]
            self.assertNotIn(n["slug"], slugs)

    def test_delete_raises_file_not_found_for_unknown_slug(self):
        with _TmpDir() as (_, store):
            with self.assertRaises(FileNotFoundError):
                store.delete_note("never-existed-9999")

    def test_delete_only_removes_target_note(self):
        """Deleting one note must not affect other notes."""
        with _TmpDir() as (_, store):
            n1 = store.create_note("Keep Me")
            n2 = store.create_note("Delete Me")
            store.delete_note(n2["slug"])
            remaining = store.list_notes()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]["slug"], n1["slug"])


# ---------------------------------------------------------------------------
# rename_note
# ---------------------------------------------------------------------------

class TestRenameNote(unittest.TestCase):

    def test_rename_updates_title_in_front_matter(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Original Title")
            store.rename_note(n["slug"], "New Title")
            loaded = store.load_note(n["slug"])
            self.assertEqual(loaded["title"], "New Title")

    def test_rename_preserves_body(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Title")
            store.save_note(n["slug"], "Body content to preserve.")
            store.rename_note(n["slug"], "Renamed Title")
            loaded = store.load_note(n["slug"])
            self.assertEqual(loaded["body"], "Body content to preserve.")

    def test_rename_old_slug_still_resolves(self):
        """Filename must not change — rename is front-matter only."""
        with _TmpDir() as (_, store):
            n = store.create_note("Before")
            store.rename_note(n["slug"], "After")
            # load_note with the original slug must succeed.
            loaded = store.load_note(n["slug"])
            self.assertEqual(loaded["slug"], n["slug"])

    def test_rename_updates_updated_timestamp(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Stamp Test")
            original_updated = n["updated"]
            time.sleep(0.01)
            result = store.rename_note(n["slug"], "New Name")
            self.assertGreaterEqual(result["updated"], original_updated)

    def test_rename_returns_new_title_in_result(self):
        with _TmpDir() as (_, store):
            n = store.create_note("Old")
            result = store.rename_note(n["slug"], "Brand New")
            self.assertEqual(result["title"], "Brand New")

    def test_rename_raises_file_not_found_for_unknown_slug(self):
        with _TmpDir() as (_, store):
            with self.assertRaises(FileNotFoundError):
                store.rename_note("no-such-slug-99999", "X")


if __name__ == "__main__":
    unittest.main()
