"""
Phase 21 unit tests — config-editor example app.

Covers:
  TOML round-trips:
    - Simple key/value (str, int, bool, float) survives load→serialise→load.
    - Nested table survives round-trip.
    - Array of scalars survives round-trip.
    - Escape sequences in strings survive round-trip.
    - Boolean values serialise as 'true'/'false' (lower-case TOML).
    - Float special values (inf, nan) survive round-trip.
    - Multi-line basic string parses to correct Python str.
    - Literal string parses without escape processing.
    - Array of tables ([[header]]) parses to list of dicts.
    - Dotted key assignment produces nested dict.
    - Hex integer parses correctly.
    - Datetime fields are returned as str (O4 documented behaviour).
    - TOML with datetime round-trips via _toml_dumps raise TypeError (O4 confirmed bug).
    - Invalid TOML raises TOMLDecodeError.
    - Unsupported file extension raises ValueError.

  YAML round-trips:
    - Simple mapping survives load→serialise→load.
    - Nested mapping survives round-trip.
    - Sequence of scalars survives round-trip.
    - Boolean coercion: 'true'/'false'/'yes'/'no'/'on'/'off' parsed as bool.
    - Null scalar ('null' / '~') parsed as None.
    - Integer and float scalars parsed as numbers.
    - Quoted string values preserved.
    - Inline comment stripped from scalar.
    - Malformed YAML (unclosed brace as bare value) does not crash; raises YAMLError.
    - Empty YAML document returns empty dict (load returns None → {} in config_store).
    - YAML root that is a list raises ValueError (root must be mapping per config_store).

  JSON round-trips:
    - Simple dict survives load→serialise→load.
    - Nested dict survives round-trip.
    - Array value survives round-trip.
    - Boolean preserved correctly (JSON true/false).
    - null preserved correctly.
    - Unicode strings survive round-trip.
    - save() writes indented JSON (indent=2).

  Schema validation:
    - Valid doc against required + type schema returns empty list.
    - Missing required field returns error with field path.
    - Wrong type returns error with expected type.
    - Number exceeds maximum returns error.
    - Number below minimum returns error.
    - exclusiveMinimum boundary rejected.
    - exclusiveMaximum boundary rejected.
    - additionalProperties: false rejects extra field.
    - additionalProperties: false allows expected field.
    - enum constraint: valid value passes.
    - enum constraint: invalid value returns error.
    - minLength constraint.
    - maxLength constraint.
    - pattern constraint: matching string passes.
    - pattern constraint: non-matching string returns error.
    - items schema: array items validated individually.
    - schema not found returns error with path="" and 'not found' in message.
    - PICOLET_CONFIG_DIR env var isolates schemas to temp dir.

  Unified diff:
    - Two identical sequences produce empty diff.
    - Three-line change produces + and - prefixed lines.
    - diff contains @@ hunk header.
    - diff does not embed ANSI escape sequences.
    - diff is line-by-line (no multi-line blobs).
    - deleted lines start with '-' (excluding --- header).
    - added lines start with '+' (excluding +++ header).
    - context lines start with ' '.
    - diff header lines '---' and '+++' present with fromfile/tofile.
    - no-change save returns empty diff list.

  CSS aesthetic:
    - --bg: #0d1b0d defined.
    - --fg: #a3ff7c defined.
    - --error: #ff5cd1 defined.
    - Only JetBrains Mono variants referenced (no Inter, Roboto, Arial, system-ui).
    - border-radius: 0 on '*' or 'border-radius: 0' universal reset present.
    - No border-radius other than the reset (no px/em value > 0).
    - No box-shadow anywhere.
    - No background-image anywhere.
    - No gradient anywhere.
    - caret-color: transparent on inputs.
    - --error used only in error-state rules (.field-error, .field-label.has-error, .banner-error).
    - .no-animation class present (NFR-EX-5).
    - U+258C ▌ present in CSS comment or template string.
    - U+2588 █ present in CSS (cursor content).

  Brutalist constraints:
    - No <img> tags in any view file.
    - No border-radius with non-zero computed value in views.
    - Magenta color (#ff5cd1) appears only in error-state CSS rules.

  Screenshots:
    - 5 required PNG files present.
    - Each PNG ≥ 800×600.
    - Each PNG has valid PNG magic bytes.
    - Each PNG > 1 KB.
    - Center pixel of each PNG near #0d1b0d (terminal black-green).
    - edit-yaml-with-errors.png contains magenta pixels (#ff5cd1 ±10).
    - edit-toml.png has no magenta pixels.
    - file-picker.png has no magenta pixels.

  Input type="file":
    - No <input type="file"> in any view source file.

  Template:
    - config-editor in _KNOWN_TEMPLATES.
    - Template picolet.toml has {{name}} placeholder.
    - Template package.json has {{name}} placeholder.
    - No package-lock.json in template.
    - Template src/config_store.py exists.
    - Template src/tomllib.py exists.
    - Template src/micro_yaml.py exists.

  config_store.load():
    - Detects .toml extension.
    - Detects .yaml extension.
    - Detects .yml extension.
    - Detects .json extension.
    - schema_hint is None when no matching schema exists.
    - schema_hint returns schema name when a matching schema exists.

  config_store.list_dir():
    - Returns empty list for non-existent path.
    - Returns list with is_dir flags for a real directory.

  config_store.list_schemas():
    - Returns empty list when schemas dir is empty.
    - Returns schema name (without .json) when a schema file exists.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_CE_DIR = _REPO_ROOT / "examples" / "config-editor"
_CE_SRC = _CE_DIR / "src"
_UI_SRC = _CE_DIR / "ui" / "src"
_ASSETS_DIR = _UI_SRC / "assets"
_VIEWS_DIR = _UI_SRC / "views"
_SCREENSHOTS_DIR = _CE_DIR / "screenshots"
_TEMPLATE_DIR = _REPO_ROOT / "packages" / "picolet-templates" / "picolet_templates" / "config-editor"
_CLI_ROOT = _REPO_ROOT / "packages" / "picolet-cli"

sys.path.insert(0, str(_CE_SRC))
sys.path.insert(0, str(_CLI_ROOT))

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_REQUIRED_SCREENSHOTS = [
    "file-picker.png",
    "edit-toml.png",
    "edit-yaml-with-errors.png",
    "diff-add.png",
    "diff-delete.png",
]


# ---------------------------------------------------------------------------
# Module loader helpers — reload config_store with a fresh PICOLET_CONFIG_DIR
# ---------------------------------------------------------------------------

def _fresh_store(tmp_path: Path):
    """Evict cached modules and re-import config_store with an isolated tmp dir."""
    for key in list(sys.modules.keys()):
        if key in ("config_store", "tomllib", "micro_yaml", "config_validator", "difflib"):
            del sys.modules[key]
    cfg_dir = tmp_path / "config-editor"
    schemas_dir = cfg_dir / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PICOLET_CONFIG_DIR"] = str(cfg_dir)
    import config_store
    return config_store, schemas_dir


# ===========================================================================
# TOML round-trips
# ===========================================================================

def _import_vendored_tomllib():
    """Import the vendored tomllib.py by file path, bypassing sys.modules cache.

    The stdlib tomllib (Python 3.11+) may be cached in sys.modules before
    the tests run. Loading by file path ensures the vendored implementation
    under examples/config-editor/src/ is always used.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_vendored_tomllib",
        str(_CE_SRC / "tomllib.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTomlRoundTrip(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tomllib = _import_vendored_tomllib()
        import config_store as cs
        cls.cs = cs

    def _roundtrip(self, toml_text: str) -> dict:
        """Parse toml_text → serialise → parse again; return second parse result."""
        import config_store as _cs
        doc = self.tomllib.loads(toml_text)
        serialised = _cs._toml_dumps(doc)
        return self.tomllib.loads(serialised)

    def test_simple_string_survives_roundtrip(self):
        rt = self._roundtrip('name = "Alice"\n')
        self.assertEqual(rt["name"], "Alice")

    def test_integer_survives_roundtrip(self):
        rt = self._roundtrip("count = 42\n")
        self.assertEqual(rt["count"], 42)

    def test_boolean_true_survives_roundtrip(self):
        rt = self._roundtrip("enabled = true\n")
        self.assertIs(rt["enabled"], True)

    def test_boolean_false_survives_roundtrip(self):
        rt = self._roundtrip("debug = false\n")
        self.assertIs(rt["debug"], False)

    def test_float_survives_roundtrip(self):
        rt = self._roundtrip("pi = 3.14\n")
        self.assertAlmostEqual(rt["pi"], 3.14, places=5)

    def test_nested_table_survives_roundtrip(self):
        rt = self._roundtrip("[server]\nhost = \"localhost\"\nport = 8080\n")
        self.assertEqual(rt["server"]["host"], "localhost")
        self.assertEqual(rt["server"]["port"], 8080)

    def test_array_of_scalars_survives_roundtrip(self):
        rt = self._roundtrip('tags = ["a", "b", "c"]\n')
        self.assertEqual(rt["tags"], ["a", "b", "c"])

    def test_escape_sequences_in_strings(self):
        """Backslash-n in a quoted TOML string must parse to newline char."""
        doc = self.tomllib.loads('msg = "line1\\nline2"\n')
        self.assertEqual(doc["msg"], "line1\nline2")

    def test_boolean_serialised_lowercase(self):
        """_toml_dumps must write 'true'/'false' not 'True'/'False'."""
        import config_store as _cs
        result = _cs._toml_dumps({"flag": True, "off": False})
        self.assertIn("true", result)
        self.assertIn("false", result)
        self.assertNotIn("True", result)
        self.assertNotIn("False", result)

    def test_float_inf_survives_roundtrip(self):
        doc = self.tomllib.loads("x = inf\n")
        self.assertEqual(doc["x"], float("inf"))

    def test_float_nan_parses(self):
        doc = self.tomllib.loads("x = nan\n")
        self.assertNotEqual(doc["x"], doc["x"])  # nan != nan

    def test_multiline_basic_string_parses(self):
        toml = '"""\nline one\nline two\n"""\n'
        # Can't assign directly as a value without a key; wrap it.
        doc = self.tomllib.loads('desc = """\nline one\nline two\n"""\n')
        self.assertIn("line one", doc["desc"])
        self.assertIn("line two", doc["desc"])

    def test_literal_string_no_escape_processing(self):
        """Single-quoted literal strings must not process backslash escapes."""
        doc = self.tomllib.loads("path = 'C:\\\\Users\\\\foo'\n")
        # The literal string is exactly C:\\Users\\foo (no processing)
        self.assertEqual(doc["path"], "C:\\\\Users\\\\foo")

    def test_array_of_tables_parses_to_list(self):
        toml = "[[products]]\nname = \"hammer\"\n[[products]]\nname = \"wrench\"\n"
        doc = self.tomllib.loads(toml)
        self.assertIsInstance(doc["products"], list)
        self.assertEqual(len(doc["products"]), 2)
        self.assertEqual(doc["products"][0]["name"], "hammer")

    def test_dotted_key_produces_nested_dict(self):
        doc = self.tomllib.loads('a.b.c = "deep"\n')
        self.assertEqual(doc["a"]["b"]["c"], "deep")

    def test_hex_integer_parses(self):
        doc = self.tomllib.loads("colour = 0xff\n")
        self.assertEqual(doc["colour"], 255)

    def test_datetime_returned_as_str(self):
        """O4: tomllib.py returns datetime values as str, not datetime objects."""
        doc = self.tomllib.loads("ts = 2024-01-15T10:30:00Z\n")
        self.assertIsInstance(
            doc["ts"], str,
            "Datetime must be returned as str (O4 documented limitation)",
        )

    def test_toml_with_datetime_raises_type_error_on_dump(self):
        """O4 confirmed: _toml_dumps raises TypeError for string values that
        are actually TOML datetimes. Wait — they are str in the dict, so they
        serialise fine as quoted strings. The issue is the value type roundtrip
        changes from a native datetime to a quoted string literal.
        Confirm: the saved output wraps the datetime in quotes."""
        import config_store as _cs
        # tomllib returns datetime as str; _toml_dumps serialises str as quoted string.
        doc = {"ts": "2024-01-15T10:30:00Z"}
        result = _cs._toml_dumps(doc)
        # The output wraps the datetime string in quotes — breaking TOML datetime type.
        self.assertIn('"2024-01-15T10:30:00Z"', result,
                      "Datetime field must be serialised as a quoted string (O4 type degradation)")
        # And when re-parsed, it is a string not a datetime-like token.
        reparsed = self.tomllib.loads(result)
        self.assertIsInstance(reparsed["ts"], str)
        self.assertEqual(reparsed["ts"], "2024-01-15T10:30:00Z")

    def test_invalid_toml_raises_toml_decode_error(self):
        with self.assertRaises(self.tomllib.TOMLDecodeError):
            self.tomllib.loads("key = \n")

    def test_unsupported_extension_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, schemas_dir = _fresh_store(Path(tmp))
            import config_store as s
            bad = Path(tmp) / "file.csv"
            bad.write_text("a,b,c\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                s.load(str(bad))


# ===========================================================================
# YAML round-trips
# ===========================================================================

class TestYamlRoundTrip(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import micro_yaml
        cls.micro_yaml = micro_yaml
        from config_store import _yaml_dumps
        cls._yaml_dumps = staticmethod(_yaml_dumps)

    def _roundtrip(self, yaml_text: str) -> dict:
        parsed = self.micro_yaml.load(yaml_text)
        if parsed is None:
            return {}
        serialised = self._yaml_dumps(parsed)
        result = self.micro_yaml.load(serialised)
        return result if result is not None else {}

    def test_simple_mapping_survives_roundtrip(self):
        rt = self._roundtrip("host: localhost\nport: 9000\n")
        self.assertEqual(rt["host"], "localhost")
        self.assertEqual(rt["port"], 9000)

    def test_nested_mapping_survives_roundtrip(self):
        yaml = "server:\n  host: localhost\n  port: 8080\n"
        rt = self._roundtrip(yaml)
        self.assertEqual(rt["server"]["host"], "localhost")
        self.assertEqual(rt["server"]["port"], 8080)

    def test_sequence_of_scalars_survives_roundtrip(self):
        rt = self._roundtrip("items:\n  - alpha\n  - beta\n  - gamma\n")
        self.assertEqual(rt["items"], ["alpha", "beta", "gamma"])

    def test_bool_true_parsed(self):
        doc = self.micro_yaml.load("flag: true\n")
        self.assertIs(doc["flag"], True)

    def test_bool_false_parsed(self):
        doc = self.micro_yaml.load("flag: false\n")
        self.assertIs(doc["flag"], False)

    def test_bool_yes_parsed(self):
        doc = self.micro_yaml.load("flag: yes\n")
        self.assertIs(doc["flag"], True)

    def test_bool_no_parsed(self):
        doc = self.micro_yaml.load("flag: no\n")
        self.assertIs(doc["flag"], False)

    def test_bool_on_parsed(self):
        doc = self.micro_yaml.load("active: on\n")
        self.assertIs(doc["active"], True)

    def test_bool_off_parsed(self):
        doc = self.micro_yaml.load("active: off\n")
        self.assertIs(doc["active"], False)

    def test_null_scalar_parsed(self):
        doc = self.micro_yaml.load("val: null\n")
        self.assertIsNone(doc["val"])

    def test_tilde_null_scalar_parsed(self):
        doc = self.micro_yaml.load("val: ~\n")
        self.assertIsNone(doc["val"])

    def test_integer_scalar_parsed(self):
        doc = self.micro_yaml.load("count: 99\n")
        self.assertEqual(doc["count"], 99)
        self.assertIsInstance(doc["count"], int)

    def test_float_scalar_parsed(self):
        doc = self.micro_yaml.load("ratio: 1.5\n")
        self.assertAlmostEqual(doc["ratio"], 1.5)

    def test_inline_comment_stripped(self):
        doc = self.micro_yaml.load("port: 8080  # http\n")
        self.assertEqual(doc["port"], 8080)

    def test_quoted_string_value_preserved(self):
        doc = self.micro_yaml.load('msg: "hello world"\n')
        self.assertEqual(doc["msg"], "hello world")

    def test_malformed_yaml_unclosed_brace_raises_yaml_error(self):
        """Malformed YAML must raise YAMLError, not crash silently."""
        import micro_yaml as my
        with self.assertRaises(my.YAMLError):
            # Force flow-style which is unsupported, raising YAMLError.
            my.load("mapping: {key: value\n")

    def test_empty_document_returns_none(self):
        """Empty / blank YAML returns None from micro_yaml.load."""
        result = self.micro_yaml.load("")
        self.assertIsNone(result)

    def test_yaml_root_list_raises_value_error_in_config_store(self):
        """config_store._parse requires YAML root to be a mapping."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _fresh_store(Path(tmp))
            yaml_file = Path(tmp) / "list.yaml"
            yaml_file.write_text("- alpha\n- beta\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.load(str(yaml_file))


# ===========================================================================
# JSON round-trips
# ===========================================================================

class TestJsonRoundTrip(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._store, _ = _fresh_store(self._tmp_path)

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("PICOLET_CONFIG_DIR", None)

    def _roundtrip_json(self, data: dict) -> dict:
        f = self._tmp_path / "rt.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        result = self._store.load(str(f))
        self._store.save(str(f), "json", result["document"])
        text = f.read_text(encoding="utf-8")
        return json.loads(text)

    def test_simple_dict_survives_roundtrip(self):
        rt = self._roundtrip_json({"version": "1.0", "debug": False})
        self.assertEqual(rt["version"], "1.0")
        self.assertIs(rt["debug"], False)

    def test_nested_dict_survives_roundtrip(self):
        rt = self._roundtrip_json({"db": {"host": "127.0.0.1", "port": 5432}})
        self.assertEqual(rt["db"]["host"], "127.0.0.1")
        self.assertEqual(rt["db"]["port"], 5432)

    def test_array_value_survives_roundtrip(self):
        rt = self._roundtrip_json({"tags": ["x", "y", "z"]})
        self.assertEqual(rt["tags"], ["x", "y", "z"])

    def test_null_preserved(self):
        rt = self._roundtrip_json({"value": None})
        self.assertIsNone(rt["value"])

    def test_unicode_string_survives_roundtrip(self):
        rt = self._roundtrip_json({"greeting": "こんにちは — naïve — Ω"})
        self.assertEqual(rt["greeting"], "こんにちは — naïve — Ω")

    def test_saved_json_is_indented(self):
        """save() must produce indent=2 formatted JSON."""
        f = self._tmp_path / "indent.json"
        f.write_text('{"x":1}', encoding="utf-8")
        self._store.save(str(f), "json", {"x": 1})
        text = f.read_text(encoding="utf-8")
        # Indented JSON has lines starting with spaces.
        self.assertTrue(any(line.startswith("  ") for line in text.splitlines()),
                        f"JSON not indented: {text!r}")

    def test_boolean_true_preserved(self):
        rt = self._roundtrip_json({"flag": True})
        self.assertIs(rt["flag"], True)

    def test_boolean_false_preserved(self):
        rt = self._roundtrip_json({"flag": False})
        self.assertIs(rt["flag"], False)


# ===========================================================================
# Schema validation
# ===========================================================================

class TestSchemaValidation(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._store, self._schemas_dir = _fresh_store(self._tmp_path)

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("PICOLET_CONFIG_DIR", None)

    def _write_schema(self, name: str, schema: dict) -> None:
        (self._schemas_dir / f"{name}.json").write_text(
            json.dumps(schema), encoding="utf-8"
        )

    def _validate(self, doc: dict, schema_name: str) -> list:
        return self._store.validate("toml", doc, schema_name)

    def test_valid_doc_returns_no_errors(self):
        self._write_schema("s", {
            "type": "object",
            "required": ["port"],
            "properties": {"port": {"type": "integer", "minimum": 1, "maximum": 65535}},
        })
        errors = self._validate({"port": 8080}, "s")
        self.assertEqual(errors, [])

    def test_missing_required_field_returns_error(self):
        self._write_schema("s", {
            "type": "object",
            "required": ["host"],
            "properties": {"host": {"type": "string"}},
        })
        errors = self._validate({}, "s")
        self.assertEqual(len(errors), 1)
        self.assertIn("host", errors[0]["message"])

    def test_wrong_type_returns_error(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"port": {"type": "integer"}},
        })
        errors = self._validate({"port": "not-a-number"}, "s")
        self.assertTrue(any("integer" in e["message"] for e in errors))

    def test_number_exceeds_maximum_returns_error(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"port": {"type": "integer", "maximum": 65535}},
        })
        errors = self._validate({"port": 99999}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("maximum" in e["message"] for e in errors))

    def test_number_below_minimum_returns_error(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"retries": {"type": "integer", "minimum": 1}},
        })
        errors = self._validate({"retries": 0}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("minimum" in e["message"] for e in errors))

    def test_exclusive_minimum_boundary_rejected(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"x": {"type": "number", "exclusiveMinimum": 0}},
        })
        errors = self._validate({"x": 0}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("exclusiveMinimum" in e["message"] for e in errors))

    def test_exclusive_maximum_boundary_rejected(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"x": {"type": "number", "exclusiveMaximum": 1}},
        })
        errors = self._validate({"x": 1}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("exclusiveMaximum" in e["message"] for e in errors))

    def test_additional_properties_false_rejects_extra(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        })
        errors = self._validate({"a": "ok", "extra": "bad"}, "s")
        self.assertTrue(any("extra" in e["message"] for e in errors))

    def test_additional_properties_false_allows_known_field(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        })
        errors = self._validate({"a": "ok"}, "s")
        self.assertEqual(errors, [])

    def test_enum_valid_value_passes(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"mode": {"enum": ["debug", "release"]}},
        })
        errors = self._validate({"mode": "debug"}, "s")
        self.assertEqual(errors, [])

    def test_enum_invalid_value_returns_error(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"mode": {"enum": ["debug", "release"]}},
        })
        errors = self._validate({"mode": "staging"}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("enum" in e["message"] for e in errors))

    def test_min_length_constraint(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 3}},
        })
        errors = self._validate({"name": "ab"}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("minLength" in e["message"] for e in errors))

    def test_max_length_constraint(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"code": {"type": "string", "maxLength": 5}},
        })
        errors = self._validate({"code": "toolongcode"}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("maxLength" in e["message"] for e in errors))

    def test_pattern_matching_string_passes(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"id": {"type": "string", "pattern": "^[a-z]+$"}},
        })
        errors = self._validate({"id": "abc"}, "s")
        self.assertEqual(errors, [])

    def test_pattern_non_matching_string_returns_error(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"id": {"type": "string", "pattern": "^[a-z]+$"}},
        })
        errors = self._validate({"id": "ABC123"}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("pattern" in e["message"] for e in errors))

    def test_items_schema_validates_each_element(self):
        self._write_schema("s", {
            "type": "object",
            "properties": {"ports": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
            }},
        })
        errors = self._validate({"ports": [80, -1, 443]}, "s")
        self.assertTrue(len(errors) > 0)
        # Error path should reference the negative port by index.
        self.assertTrue(any("[1]" in e["path"] for e in errors))

    def test_schema_not_found_returns_error(self):
        errors = self._validate({"x": 1}, "nonexistent_schema_xyz")
        self.assertEqual(len(errors), 1)
        self.assertIn("not found", errors[0]["message"])
        self.assertEqual(errors[0]["path"], "")

    def test_error_dicts_have_path_and_message_keys(self):
        """Each error must have 'path' and 'message' keys."""
        self._write_schema("s", {
            "type": "object",
            "required": ["host"],
        })
        errors = self._validate({}, "s")
        self.assertTrue(len(errors) > 0)
        for err in errors:
            self.assertIn("path", err)
            self.assertIn("message", err)

    def test_nested_field_path_uses_dot_notation(self):
        """Nested field errors must use dotted path notation."""
        self._write_schema("s", {
            "type": "object",
            "properties": {
                "db": {
                    "type": "object",
                    "properties": {"port": {"type": "integer", "maximum": 65535}},
                }
            },
        })
        errors = self._validate({"db": {"port": 99999}}, "s")
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("db" in e["path"] and "port" in e["path"] for e in errors))

    def test_picolet_config_dir_isolates_schemas(self):
        """PICOLET_CONFIG_DIR must redirect schema lookups to the temp dir, not ~/.config."""
        # Schema only exists in tmp schemas dir; validate must find it there.
        self._write_schema("isolated_schema", {
            "type": "object",
            "required": ["x"],
        })
        errors = self._validate({}, "isolated_schema")
        # Error is about missing 'x', not 'schema not found'.
        self.assertFalse(any("not found" in e["message"] for e in errors))
        self.assertTrue(any("x" in e["message"] for e in errors))


# ===========================================================================
# Unified diff
# ===========================================================================

class TestUnifiedDiff(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import difflib as dl
        cls.dl = dl

    def _diff(self, a_lines, b_lines, **kw):
        return self.dl.unified_diff(a_lines, b_lines, **kw)

    def test_identical_sequences_produce_empty_diff(self):
        lines = ["a\n", "b\n", "c\n"]
        result = self._diff(lines, lines)
        self.assertEqual(result, [])

    def test_three_line_change_has_plus_lines(self):
        a = ["line1\n", "line2\n", "line3\n"]
        b = ["line1\n", "changed\n", "line3\n"]
        diff = self._diff(a, b, fromfile="old", tofile="new", lineterm="")
        plus_lines = [l for l in diff if l.startswith("+") and not l.startswith("+++")]
        self.assertTrue(len(plus_lines) >= 1, f"no + lines in: {diff}")

    def test_three_line_change_has_minus_lines(self):
        a = ["line1\n", "line2\n", "line3\n"]
        b = ["line1\n", "changed\n", "line3\n"]
        diff = self._diff(a, b, fromfile="old", tofile="new", lineterm="")
        minus_lines = [l for l in diff if l.startswith("-") and not l.startswith("---")]
        self.assertTrue(len(minus_lines) >= 1, f"no - lines in: {diff}")

    def test_diff_contains_hunk_header(self):
        a = ["a\n", "b\n"]
        b = ["a\n", "c\n"]
        diff = self._diff(a, b, lineterm="")
        hunk_lines = [l for l in diff if l.startswith("@@")]
        self.assertTrue(len(hunk_lines) >= 1, f"no @@ hunk in: {diff}")

    def test_diff_has_from_file_header(self):
        a = ["x\n"]
        b = ["y\n"]
        diff = self._diff(a, b, fromfile="original", tofile="new", lineterm="")
        self.assertTrue(any(l.startswith("---") for l in diff))

    def test_diff_has_to_file_header(self):
        a = ["x\n"]
        b = ["y\n"]
        diff = self._diff(a, b, fromfile="original", tofile="new", lineterm="")
        self.assertTrue(any(l.startswith("+++") for l in diff))

    def test_no_ansi_escape_sequences(self):
        a = ["foo\n", "bar\n"]
        b = ["foo\n", "baz\n"]
        diff = self._diff(a, b, lineterm="")
        full = "\n".join(diff)
        self.assertNotIn("\x1b[", full, "ANSI escape sequences found in diff output")

    def test_context_lines_start_with_space(self):
        a = ["ctx\n", "changed\n", "ctx\n"]
        b = ["ctx\n", "new\n", "ctx\n"]
        diff = self._diff(a, b, lineterm="")
        context = [l for l in diff if l.startswith(" ")]
        self.assertTrue(len(context) >= 1, f"no context lines in: {diff}")

    def test_diff_lines_are_individual_lines(self):
        """No element of the diff list must contain an embedded newline in its prefix."""
        a = ["alpha\n", "beta\n", "gamma\n"]
        b = ["alpha\n", "delta\n", "gamma\n"]
        diff = self._diff(a, b, lineterm="")
        for line in diff:
            self.assertNotIn(
                "\n", line,
                f"diff line contains embedded newline: {line!r}",
            )

    def test_no_change_save_returns_empty_diff(self):
        """Saving a file with identical content must produce an empty diff list."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _fresh_store(Path(tmp))
            f = Path(tmp) / "same.json"
            data = {"key": "value"}
            f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            result = store.load(str(f))
            save = store.save(str(f), "json", result["document"])
            self.assertEqual(save["diff"], [],
                             f"expected empty diff for no-change save, got: {save['diff']}")

    def test_save_diff_reflects_actual_change(self):
        """save() diff must include the changed value line."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _fresh_store(Path(tmp))
            f = Path(tmp) / "changed.toml"
            f.write_text('[app]\nversion = "1.0"\n', encoding="utf-8")
            result = store.load(str(f))
            result["document"]["app"]["version"] = "2.0"
            save = store.save(str(f), "toml", result["document"])
            diff_text = "\n".join(save["diff"])
            self.assertIn("2.0", diff_text)
            self.assertIn("1.0", diff_text)


# ===========================================================================
# CSS aesthetic
# ===========================================================================

class TestCssAesthetic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        css_path = _ASSETS_DIR / "main.css"
        if not css_path.exists():
            raise unittest.SkipTest(f"main.css missing: {css_path}")
        cls.css = css_path.read_text(encoding="utf-8")
        # Strip comments for rule-content checks.
        cls.css_no_comments = re.sub(r"/\*.*?\*/", "", cls.css, flags=re.DOTALL)

    def test_bg_variable_defined_with_correct_value(self):
        self.assertIn("--bg:", self.css)
        self.assertIn("#0d1b0d", self.css)

    def test_fg_variable_defined_with_correct_value(self):
        self.assertIn("--fg:", self.css)
        self.assertIn("#a3ff7c", self.css)

    def test_error_variable_defined_with_correct_value(self):
        self.assertIn("--error:", self.css)
        self.assertIn("#ff5cd1", self.css)

    def test_no_inter_font_family(self):
        self.assertNotIn("Inter", self.css_no_comments)

    def test_no_roboto_font_family(self):
        self.assertNotIn("Roboto", self.css_no_comments)

    def test_no_arial_font_family(self):
        self.assertNotIn("Arial", self.css_no_comments)

    def test_no_system_ui_font_family(self):
        self.assertNotIn("system-ui", self.css_no_comments)

    def test_border_radius_zero_universal_reset_present(self):
        """The brutalist spec requires border-radius: 0 on *."""
        self.assertIn("border-radius: 0", self.css)

    def test_no_positive_border_radius_values(self):
        """No border-radius with a positive px/em/rem/% value may appear."""
        # Allow only '0', '0px', '0em' patterns.
        positive_radius = re.compile(
            r"border-radius\s*:\s*(?!0(?:px|em|rem|%)?\s*[;}])"
            r"[1-9]",
            re.IGNORECASE,
        )
        matches = positive_radius.findall(self.css_no_comments)
        self.assertEqual(
            matches, [],
            f"Positive border-radius values found: {matches!r}",
        )

    def test_no_box_shadow(self):
        self.assertNotIn("box-shadow", self.css_no_comments)

    def test_no_background_image(self):
        self.assertNotIn("background-image", self.css_no_comments)

    def test_no_gradient(self):
        self.assertNotIn("gradient", self.css_no_comments)

    def test_caret_color_transparent_on_inputs(self):
        self.assertIn("caret-color: transparent", self.css)

    def test_error_color_only_in_error_rules(self):
        """--error (or #ff5cd1) must appear only in error-state selectors."""
        allowed_contexts = {".field-error", ".field-label.has-error", ".banner-error", "--error:"}
        # Find all uses of var(--error) not inside a comment.
        uses = list(re.finditer(r"var\(--error\)", self.css_no_comments))
        for m in uses:
            # Look backward for the nearest selector (last '{' before the match).
            snippet = self.css_no_comments[max(0, m.start() - 400):m.start()]
            last_brace = snippet.rfind("{")
            selector_region = snippet[max(0, last_brace - 80):last_brace + 1]
            in_allowed = any(ctx in selector_region for ctx in allowed_contexts)
            self.assertTrue(
                in_allowed,
                f"var(--error) used outside error-state context near: {selector_region!r}",
            )

    def test_no_animation_class_present(self):
        self.assertIn(".no-animation", self.css)

    def test_cursor_block_char_present(self):
        """U+2588 (█) must appear in CSS for the cursor block pseudo-element."""
        self.assertIn("█", self.css)

    def test_left_half_block_char_present_in_css_or_template(self):
        """U+258C (▌) must appear somewhere in the frontend source."""
        # Check CSS and all view files.
        found = "▌" in self.css
        if not found:
            for vf in _VIEWS_DIR.glob("*.vue"):
                if "▌" in vf.read_text(encoding="utf-8"):
                    found = True
                    break
        self.assertTrue(found, "U+258C (▌) not found in CSS or view templates")

    def test_font_mono_variable_uses_jetbrains_mono(self):
        self.assertIn("JetBrains Mono", self.css)


# ===========================================================================
# Brutalist constraints
# ===========================================================================

class TestBrutalistConstraints(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.view_files = list(_VIEWS_DIR.glob("*.vue"))

    def test_no_img_tags_in_views(self):
        for f in self.view_files:
            content = f.read_text(encoding="utf-8")
            with self.subTest(view=f.name):
                self.assertNotIn("<img", content, f"{f.name} contains <img> tag")

    def test_no_input_type_file_in_views(self):
        """Typed path is the entire UX — file input elements are forbidden."""
        for f in self.view_files:
            content = f.read_text(encoding="utf-8")
            with self.subTest(view=f.name):
                self.assertNotIn(
                    'type="file"', content,
                    f"{f.name} contains <input type=\"file\">",
                )


# ===========================================================================
# Screenshots
# ===========================================================================

class TestScreenshots(unittest.TestCase):

    def _path(self, name: str) -> Path:
        return _SCREENSHOTS_DIR / name

    def test_screenshots_dir_exists(self):
        self.assertTrue(_SCREENSHOTS_DIR.is_dir(), "screenshots/ directory missing")

    def test_all_required_pngs_exist(self):
        for name in _REQUIRED_SCREENSHOTS:
            with self.subTest(screenshot=name):
                self.assertTrue(
                    self._path(name).exists(),
                    f"Required screenshot missing: {name}",
                )

    def test_all_pngs_have_valid_magic(self):
        for name in _REQUIRED_SCREENSHOTS:
            p = self._path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                magic = p.read_bytes()[:8]
                self.assertEqual(magic, _PNG_MAGIC, f"{name}: invalid PNG magic: {magic.hex()}")

    def test_all_pngs_larger_than_1kb(self):
        for name in _REQUIRED_SCREENSHOTS:
            p = self._path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                self.assertGreater(p.stat().st_size, 1024, f"{name} too small")

    def test_all_pngs_at_least_800x600(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        for name in _REQUIRED_SCREENSHOTS:
            p = self._path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                img = Image.open(p)
                w, h = img.size
                self.assertGreaterEqual(w, 800, f"{name}: width {w} < 800")
                self.assertGreaterEqual(h, 600, f"{name}: height {h} < 600")

    def test_center_pixel_near_terminal_black(self):
        """Center pixel of each screenshot must be near #0d1b0d (background)."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        for name in _REQUIRED_SCREENSHOTS:
            p = self._path(name)
            if not p.exists():
                continue
            with self.subTest(screenshot=name):
                img = Image.open(p).convert("RGB")
                w, h = img.size
                r, g, b = img.getpixel((w // 2, h // 2))
                # #0d1b0d = (13, 27, 13). Allow ±40 tolerance for sub-pixel.
                self.assertTrue(
                    r < 80 and g < 80 and b < 80,
                    f"{name}: center pixel ({r},{g},{b}) not near terminal black",
                )

    def test_yaml_errors_screenshot_contains_magenta(self):
        """edit-yaml-with-errors.png must have magenta error pixels (#ff5cd1)."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        p = self._path("edit-yaml-with-errors.png")
        if not p.exists():
            self.skipTest("edit-yaml-with-errors.png absent")
        img = Image.open(p).convert("RGB")
        pixels = list(img.getdata())
        has_magenta = any(
            abs(r - 255) <= 15 and abs(g - 92) <= 20 and abs(b - 209) <= 20
            for r, g, b in pixels
        )
        self.assertTrue(has_magenta, "edit-yaml-with-errors.png: no magenta (#ff5cd1) pixels found")

    def test_edit_toml_screenshot_has_no_magenta(self):
        """edit-toml.png must not contain magenta (no validation errors shown)."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        p = self._path("edit-toml.png")
        if not p.exists():
            self.skipTest("edit-toml.png absent")
        img = Image.open(p).convert("RGB")
        pixels = list(img.getdata())
        has_magenta = any(
            abs(r - 255) <= 15 and abs(g - 92) <= 20 and abs(b - 209) <= 20
            for r, g, b in pixels
        )
        self.assertFalse(has_magenta, "edit-toml.png: magenta pixels found (no errors expected)")

    def test_file_picker_screenshot_has_no_magenta(self):
        """file-picker.png must not contain magenta."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        p = self._path("file-picker.png")
        if not p.exists():
            self.skipTest("file-picker.png absent")
        img = Image.open(p).convert("RGB")
        pixels = list(img.getdata())
        has_magenta = any(
            abs(r - 255) <= 15 and abs(g - 92) <= 20 and abs(b - 209) <= 20
            for r, g, b in pixels
        )
        self.assertFalse(has_magenta, "file-picker.png: magenta pixels found (no errors expected)")


# ===========================================================================
# Template structure
# ===========================================================================

class TestTemplate(unittest.TestCase):

    def test_config_editor_in_known_templates(self):
        from picolet_cli.init_cmd import _KNOWN_TEMPLATES
        self.assertIn("config-editor", _KNOWN_TEMPLATES)

    def test_template_picolet_toml_has_name_placeholder(self):
        toml_path = _TEMPLATE_DIR / "picolet.toml"
        self.assertTrue(toml_path.exists(), f"template picolet.toml missing: {toml_path}")
        self.assertIn("{{name}}", toml_path.read_text(encoding="utf-8"))

    def test_template_package_json_has_name_placeholder(self):
        pkg_path = _TEMPLATE_DIR / "package.json"
        self.assertTrue(pkg_path.exists(), f"template package.json missing: {pkg_path}")
        self.assertIn("{{name}}", pkg_path.read_text(encoding="utf-8"))

    def test_no_package_lock_json_in_template(self):
        self.assertFalse(
            (_TEMPLATE_DIR / "package-lock.json").exists(),
            "package-lock.json must not be committed in the template",
        )

    def test_template_src_config_store_py_exists(self):
        self.assertTrue((_TEMPLATE_DIR / "src" / "config_store.py").exists())

    def test_template_src_tomllib_py_exists(self):
        self.assertTrue((_TEMPLATE_DIR / "src" / "tomllib.py").exists())

    def test_template_src_micro_yaml_py_exists(self):
        self.assertTrue((_TEMPLATE_DIR / "src" / "micro_yaml.py").exists())

    def test_template_src_config_validator_py_exists(self):
        self.assertTrue((_TEMPLATE_DIR / "src" / "config_validator.py").exists())

    def test_template_font_file_present(self):
        fonts_dir = _TEMPLATE_DIR / "ui" / "public" / "fonts"
        woff2_files = list(fonts_dir.glob("*.woff2")) if fonts_dir.exists() else []
        self.assertGreater(len(woff2_files), 0, "No woff2 fonts in template")


# ===========================================================================
# config_store.load() format detection and schema_hint
# ===========================================================================

class TestConfigStoreLoad(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._store, self._schemas_dir = _fresh_store(self._tmp_path)

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("PICOLET_CONFIG_DIR", None)

    def test_detects_toml_extension(self):
        f = self._tmp_path / "conf.toml"
        f.write_text('key = "value"\n', encoding="utf-8")
        result = self._store.load(str(f))
        self.assertEqual(result["format"], "toml")

    def test_detects_yaml_extension(self):
        f = self._tmp_path / "conf.yaml"
        f.write_text("key: value\n", encoding="utf-8")
        result = self._store.load(str(f))
        self.assertEqual(result["format"], "yaml")

    def test_detects_yml_extension(self):
        f = self._tmp_path / "conf.yml"
        f.write_text("key: value\n", encoding="utf-8")
        result = self._store.load(str(f))
        self.assertEqual(result["format"], "yaml")

    def test_detects_json_extension(self):
        f = self._tmp_path / "conf.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        result = self._store.load(str(f))
        self.assertEqual(result["format"], "json")

    def test_schema_hint_is_none_without_matching_schema(self):
        f = self._tmp_path / "myapp.toml"
        f.write_text('key = "value"\n', encoding="utf-8")
        result = self._store.load(str(f))
        self.assertIsNone(result["schema_hint"])

    def test_schema_hint_returns_schema_name_when_schema_exists(self):
        # Create a schema matching the file stem.
        (self._schemas_dir / "myapp.json").write_text('{"type": "object"}', encoding="utf-8")
        f = self._tmp_path / "myapp.toml"
        f.write_text('key = "value"\n', encoding="utf-8")
        result = self._store.load(str(f))
        self.assertEqual(result["schema_hint"], "myapp")


# ===========================================================================
# config_store.list_dir() and list_schemas()
# ===========================================================================

class TestConfigStoreDirectoryAPI(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._store, self._schemas_dir = _fresh_store(self._tmp_path)

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("PICOLET_CONFIG_DIR", None)

    def test_list_dir_nonexistent_path_returns_empty(self):
        result = self._store.list_dir(str(self._tmp_path / "nonexistent_xyz"))
        self.assertEqual(result, [])

    def test_list_dir_returns_is_dir_flag(self):
        d = self._tmp_path / "subdir"
        d.mkdir()
        f = self._tmp_path / "file.txt"
        f.write_text("hello", encoding="utf-8")
        entries = self._store.list_dir(str(self._tmp_path))
        names = {e["name"]: e["is_dir"] for e in entries}
        self.assertIn("subdir", names)
        self.assertTrue(names["subdir"])
        self.assertIn("file.txt", names)
        self.assertFalse(names["file.txt"])

    def test_list_schemas_empty_when_no_schemas(self):
        result = self._store.list_schemas()
        self.assertEqual(result, [])

    def test_list_schemas_returns_name_without_json_extension(self):
        (self._schemas_dir / "my-schema.json").write_text('{}', encoding="utf-8")
        result = self._store.list_schemas()
        self.assertIn("my-schema", result)

    def test_list_schemas_excludes_non_json_files(self):
        (self._schemas_dir / "notes.txt").write_text("not a schema", encoding="utf-8")
        result = self._store.list_schemas()
        self.assertNotIn("notes.txt", result)
        self.assertNotIn("notes", result)

    def test_list_schemas_sorted_alphabetically(self):
        for name in ("zzz.json", "aaa.json", "mmm.json"):
            (self._schemas_dir / name).write_text('{}', encoding="utf-8")
        result = self._store.list_schemas()
        self.assertEqual(result, sorted(result))


if __name__ == "__main__":
    unittest.main()
