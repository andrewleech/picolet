# Picolet v1.1 examples — Phase 21 SQE Report

**Feature:** Picolet v1.1 examples epic
**Phase:** 21 — config-editor example app
**Date:** 2026-05-17
**Attempt:** 1
**Phase File:** docs/phases/PHASE_21_example-config-editor-app.md
**Dev Report:** docs/phases/PHASE_21_DEV_REPORT.md

---

## Testing Summary

Tests target the vendored Python backend (`config_store.py`, `tomllib.py`,
`micro_yaml.py`, `config_validator.py`, `difflib.py`) directly via
`sys.path` injection, matching the smoke-test pattern established by PH20.
The CSS, screenshots, brutalist constraints, template structure, and
`_KNOWN_TEMPLATES` wiring are covered by file-content and pixel-level
assertions, also matching PH20 patterns.

A notable complication arose with `tomllib` import isolation: Python's
stdlib `tomllib` (3.11+) is cached in `sys.modules` by pytest before the
test module's `sys.path.insert` fires. Tests that import `tomllib` by name
therefore receive the stdlib version, which returns `datetime.datetime`
objects instead of strings. The O4 datetime test was fixed to load the
vendored `tomllib.py` by file path via `importlib.util.spec_from_file_location`,
bypassing the module cache.

The `package-lock.json` found in the template (`packages/picolet/
picolet.templates/config-editor/package-lock.json`) is a confirmed implementation
bug. The file should not be committed to the template; users who run
`picolet init --template config-editor` will receive a locked dependency tree
from the dev's environment, defeating the portability of the scaffold.

---

## Tests Created

**File:** `tests/phase-21/test_config_editor.py`

| Class | Method | What It Verifies | Spec Ref |
|-------|--------|-----------------|----------|
| TestTomlRoundTrip | test_simple_string_survives_roundtrip | str key survives parse→serialise→parse | FR-EX-3 |
| TestTomlRoundTrip | test_integer_survives_roundtrip | integer survives round-trip | FR-EX-3 |
| TestTomlRoundTrip | test_boolean_true_survives_roundtrip | bool true round-trips | FR-EX-3 |
| TestTomlRoundTrip | test_boolean_false_survives_roundtrip | bool false round-trips | FR-EX-3 |
| TestTomlRoundTrip | test_float_survives_roundtrip | float round-trips within tolerance | FR-EX-3 |
| TestTomlRoundTrip | test_nested_table_survives_roundtrip | [section] tables survive round-trip | FR-EX-3 |
| TestTomlRoundTrip | test_array_of_scalars_survives_roundtrip | inline array round-trips | FR-EX-3 |
| TestTomlRoundTrip | test_escape_sequences_in_strings | \\n in basic strings parses to newline | FR-EX-3 |
| TestTomlRoundTrip | test_boolean_serialised_lowercase | _toml_dumps writes true/false not True/False | FR-EX-3 |
| TestTomlRoundTrip | test_float_inf_survives_roundtrip | inf parses to float('inf') | FR-EX-3 |
| TestTomlRoundTrip | test_float_nan_parses | nan parses and is NaN | FR-EX-3 |
| TestTomlRoundTrip | test_multiline_basic_string_parses | """ strings parse correctly | FR-EX-3 |
| TestTomlRoundTrip | test_literal_string_no_escape_processing | single-quoted strings are literal | FR-EX-3 |
| TestTomlRoundTrip | test_array_of_tables_parses_to_list | [[header]] produces list of dicts | FR-EX-3 |
| TestTomlRoundTrip | test_dotted_key_produces_nested_dict | a.b.c = "x" creates nested dict | FR-EX-3 |
| TestTomlRoundTrip | test_hex_integer_parses | 0xff parses to 255 | FR-EX-3 |
| TestTomlRoundTrip | test_datetime_returned_as_str | vendored tomllib returns datetime as str (O4) | FR-EX-3, D1 |
| TestTomlRoundTrip | test_toml_with_datetime_raises_type_error_on_dump | O4 type degradation: datetime saved as quoted string | FR-EX-3, D1 |
| TestTomlRoundTrip | test_invalid_toml_raises_toml_decode_error | malformed TOML raises TOMLDecodeError | FR-EX-3 |
| TestTomlRoundTrip | test_unsupported_extension_raises_value_error | .csv raises ValueError | FR-EX-3 |
| TestYamlRoundTrip | test_simple_mapping_survives_roundtrip | key: value survives round-trip | FR-EX-3 |
| TestYamlRoundTrip | test_nested_mapping_survives_roundtrip | nested indented mapping round-trips | FR-EX-3 |
| TestYamlRoundTrip | test_sequence_of_scalars_survives_roundtrip | - item list round-trips | FR-EX-3 |
| TestYamlRoundTrip | test_bool_true_parsed | 'true' parsed as Python True | FR-EX-3 |
| TestYamlRoundTrip | test_bool_false_parsed | 'false' parsed as Python False | FR-EX-3 |
| TestYamlRoundTrip | test_bool_yes_parsed | 'yes' parsed as True | FR-EX-3 |
| TestYamlRoundTrip | test_bool_no_parsed | 'no' parsed as False | FR-EX-3 |
| TestYamlRoundTrip | test_bool_on_parsed | 'on' parsed as True | FR-EX-3 |
| TestYamlRoundTrip | test_bool_off_parsed | 'off' parsed as False | FR-EX-3 |
| TestYamlRoundTrip | test_null_scalar_parsed | 'null' parsed as None | FR-EX-3 |
| TestYamlRoundTrip | test_tilde_null_scalar_parsed | '~' parsed as None | FR-EX-3 |
| TestYamlRoundTrip | test_integer_scalar_parsed | integer scalar parsed as int | FR-EX-3 |
| TestYamlRoundTrip | test_float_scalar_parsed | float scalar parsed as float | FR-EX-3 |
| TestYamlRoundTrip | test_inline_comment_stripped | # comment after value is stripped | FR-EX-3 |
| TestYamlRoundTrip | test_quoted_string_value_preserved | double-quoted string value preserved | FR-EX-3 |
| TestYamlRoundTrip | test_malformed_yaml_unclosed_brace_raises_yaml_error | flow-style triggers YAMLError | FR-EX-3 |
| TestYamlRoundTrip | test_empty_document_returns_none | blank YAML returns None | FR-EX-3 |
| TestYamlRoundTrip | test_yaml_root_list_raises_value_error_in_config_store | list root rejected by config_store | FR-EX-3 |
| TestJsonRoundTrip | test_simple_dict_survives_roundtrip | basic dict round-trips through JSON pipe | FR-EX-3 |
| TestJsonRoundTrip | test_nested_dict_survives_roundtrip | nested dict round-trips | FR-EX-3 |
| TestJsonRoundTrip | test_array_value_survives_roundtrip | JSON array round-trips | FR-EX-3 |
| TestJsonRoundTrip | test_null_preserved | null preserved through pipe | FR-EX-3 |
| TestJsonRoundTrip | test_unicode_string_survives_roundtrip | unicode survives JSON round-trip | FR-EX-3 |
| TestJsonRoundTrip | test_saved_json_is_indented | save() writes indent=2 JSON | FR-EX-3 |
| TestJsonRoundTrip | test_boolean_true_preserved | JSON true preserved | FR-EX-3 |
| TestJsonRoundTrip | test_boolean_false_preserved | JSON false preserved | FR-EX-3 |
| TestSchemaValidation | test_valid_doc_returns_no_errors | valid doc returns [] | FR-EX-3 |
| TestSchemaValidation | test_missing_required_field_returns_error | required key missing → error with field name | FR-EX-3 |
| TestSchemaValidation | test_wrong_type_returns_error | type mismatch → error with expected type | FR-EX-3 |
| TestSchemaValidation | test_number_exceeds_maximum_returns_error | maximum exceeded → error | FR-EX-3 |
| TestSchemaValidation | test_number_below_minimum_returns_error | below minimum → error | FR-EX-3 |
| TestSchemaValidation | test_exclusive_minimum_boundary_rejected | value == exclusiveMinimum → error | FR-EX-3 |
| TestSchemaValidation | test_exclusive_maximum_boundary_rejected | value == exclusiveMaximum → error | FR-EX-3 |
| TestSchemaValidation | test_additional_properties_false_rejects_extra | extra field rejected | FR-EX-3 |
| TestSchemaValidation | test_additional_properties_false_allows_known_field | known field passes | FR-EX-3 |
| TestSchemaValidation | test_enum_valid_value_passes | enum match passes | FR-EX-3 |
| TestSchemaValidation | test_enum_invalid_value_returns_error | enum mismatch → error | FR-EX-3 |
| TestSchemaValidation | test_min_length_constraint | minLength violated → error | FR-EX-3 |
| TestSchemaValidation | test_max_length_constraint | maxLength violated → error | FR-EX-3 |
| TestSchemaValidation | test_pattern_matching_string_passes | pattern match passes | FR-EX-3 |
| TestSchemaValidation | test_pattern_non_matching_string_returns_error | pattern fail → error | FR-EX-3 |
| TestSchemaValidation | test_items_schema_validates_each_element | array items validated; path includes [index] | FR-EX-3 |
| TestSchemaValidation | test_schema_not_found_returns_error | missing schema → error at path="" | FR-EX-3 |
| TestSchemaValidation | test_error_dicts_have_path_and_message_keys | error shape has 'path' and 'message' | FR-EX-3 |
| TestSchemaValidation | test_nested_field_path_uses_dot_notation | nested error path is dotted | FR-EX-3 |
| TestSchemaValidation | test_picolet_config_dir_isolates_schemas | PICOLET_CONFIG_DIR redirects schema lookups | FR-EX-3 |
| TestUnifiedDiff | test_identical_sequences_produce_empty_diff | no-change diff is empty | FR-EX-3 |
| TestUnifiedDiff | test_three_line_change_has_plus_lines | changed lines produce + lines | FR-EX-3 |
| TestUnifiedDiff | test_three_line_change_has_minus_lines | changed lines produce - lines | FR-EX-3 |
| TestUnifiedDiff | test_diff_contains_hunk_header | @@ header present | FR-EX-3 |
| TestUnifiedDiff | test_diff_has_from_file_header | --- header present | FR-EX-3 |
| TestUnifiedDiff | test_diff_has_to_file_header | +++ header present | FR-EX-3 |
| TestUnifiedDiff | test_no_ansi_escape_sequences | diff contains no ANSI codes | NFR-EX-AESTHETIC |
| TestUnifiedDiff | test_context_lines_start_with_space | context lines prefixed with space | FR-EX-3 |
| TestUnifiedDiff | test_diff_lines_are_individual_lines | each list element is a single line | FR-EX-3 |
| TestUnifiedDiff | test_no_change_save_returns_empty_diff | save with identical content → [] diff | FR-EX-3 |
| TestUnifiedDiff | test_save_diff_reflects_actual_change | save diff contains before/after values | FR-EX-3 |
| TestCssAesthetic | test_bg_variable_defined_with_correct_value | --bg: #0d1b0d present | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_fg_variable_defined_with_correct_value | --fg: #a3ff7c present | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_error_variable_defined_with_correct_value | --error: #ff5cd1 present | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_inter_font_family | Inter not referenced | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_roboto_font_family | Roboto not referenced | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_arial_font_family | Arial not referenced | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_system_ui_font_family | system-ui not referenced | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_border_radius_zero_universal_reset_present | border-radius: 0 on * | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_positive_border_radius_values | no non-zero border-radius anywhere | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_box_shadow | no box-shadow | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_background_image | no background-image | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_gradient | no gradient | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_caret_color_transparent_on_inputs | caret-color: transparent set | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_error_color_only_in_error_rules | var(--error) only in error-state selectors | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_no_animation_class_present | .no-animation class exists (NFR-EX-5) | NFR-EX-5 |
| TestCssAesthetic | test_cursor_block_char_present | U+2588 █ in CSS | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_left_half_block_char_present_in_css_or_template | U+258C ▌ in CSS or views | NFR-EX-AESTHETIC |
| TestCssAesthetic | test_font_mono_variable_uses_jetbrains_mono | JetBrains Mono referenced | NFR-EX-AESTHETIC, NFR-EX-4 |
| TestBrutalistConstraints | test_no_img_tags_in_views | no <img> in any view | NFR-EX-AESTHETIC |
| TestBrutalistConstraints | test_no_input_type_file_in_views | no <input type="file"> in views | NFR-EX-AESTHETIC |
| TestScreenshots | test_screenshots_dir_exists | screenshots/ directory present | NFR-EX-6 |
| TestScreenshots | test_all_required_pngs_exist | all 5 PNGs present | NFR-EX-6 |
| TestScreenshots | test_all_pngs_have_valid_magic | PNG magic bytes correct | NFR-EX-6 |
| TestScreenshots | test_all_pngs_larger_than_1kb | PNGs not stubs | NFR-EX-6 |
| TestScreenshots | test_all_pngs_at_least_800x600 | dimensions ≥ 800×600 | NFR-EX-6 |
| TestScreenshots | test_center_pixel_near_terminal_black | background is near-black (#0d1b0d) | NFR-EX-AESTHETIC |
| TestScreenshots | test_yaml_errors_screenshot_contains_magenta | edit-yaml-with-errors.png has #ff5cd1 pixels | NFR-EX-AESTHETIC |
| TestScreenshots | test_edit_toml_screenshot_has_no_magenta | edit-toml.png has no magenta | NFR-EX-AESTHETIC |
| TestScreenshots | test_file_picker_screenshot_has_no_magenta | file-picker.png has no magenta | NFR-EX-AESTHETIC |
| TestTemplate | test_config_editor_in_known_templates | "config-editor" in _KNOWN_TEMPLATES | FR-EX-3 |
| TestTemplate | test_template_picolet_toml_has_name_placeholder | picolet.toml has {{name}} | FR-EX-3 |
| TestTemplate | test_template_package_json_has_name_placeholder | package.json has {{name}} | FR-EX-3 |
| TestTemplate | test_no_package_lock_json_in_template | package-lock.json absent from template | FR-EX-3 |
| TestTemplate | test_template_src_config_store_py_exists | config_store.py in template | FR-EX-3 |
| TestTemplate | test_template_src_tomllib_py_exists | tomllib.py in template | FR-EX-3 |
| TestTemplate | test_template_src_micro_yaml_py_exists | micro_yaml.py in template | FR-EX-3 |
| TestTemplate | test_template_src_config_validator_py_exists | config_validator.py in template | FR-EX-3 |
| TestTemplate | test_template_font_file_present | woff2 font in template | NFR-EX-4 |
| TestConfigStoreLoad | test_detects_toml_extension | .toml → format "toml" | FR-EX-3 |
| TestConfigStoreLoad | test_detects_yaml_extension | .yaml → format "yaml" | FR-EX-3 |
| TestConfigStoreLoad | test_detects_yml_extension | .yml → format "yaml" | FR-EX-3 |
| TestConfigStoreLoad | test_detects_json_extension | .json → format "json" | FR-EX-3 |
| TestConfigStoreLoad | test_schema_hint_is_none_without_matching_schema | schema_hint None when no schema | FR-EX-3 |
| TestConfigStoreLoad | test_schema_hint_returns_schema_name_when_schema_exists | schema_hint returns stem when schema exists | FR-EX-3 |
| TestConfigStoreDirectoryAPI | test_list_dir_nonexistent_path_returns_empty | list_dir on missing path returns [] | FR-EX-3 |
| TestConfigStoreDirectoryAPI | test_list_dir_returns_is_dir_flag | list_dir entries have correct is_dir flags | FR-EX-3 |
| TestConfigStoreDirectoryAPI | test_list_schemas_empty_when_no_schemas | empty schemas dir → [] | FR-EX-3 |
| TestConfigStoreDirectoryAPI | test_list_schemas_returns_name_without_json_extension | schema name stripped of .json | FR-EX-3 |
| TestConfigStoreDirectoryAPI | test_list_schemas_excludes_non_json_files | non-.json files ignored | FR-EX-3 |
| TestConfigStoreDirectoryAPI | test_list_schemas_sorted_alphabetically | schemas sorted | FR-EX-3 |

---

## Test Results

| Test | Result |
|------|--------|
| All TestTomlRoundTrip (20 tests) | 20 PASS |
| All TestYamlRoundTrip (18 tests) | 18 PASS |
| All TestJsonRoundTrip (8 tests) | 8 PASS |
| All TestSchemaValidation (18 tests) | 18 PASS |
| All TestUnifiedDiff (11 tests) | 11 PASS |
| All TestCssAesthetic (16 tests) | 16 PASS |
| All TestBrutalistConstraints (2 tests, 6 subtests) | 2 PASS |
| All TestScreenshots (9 tests) | 9 PASS |
| TestTemplate (9 tests) | 8 PASS, 1 FAIL |
| All TestConfigStoreLoad (6 tests) | 6 PASS |
| All TestConfigStoreDirectoryAPI (6 tests) | 6 PASS |

**FAIL:** `TestTemplate::test_no_package_lock_json_in_template`

```
AssertionError: True is not false : package-lock.json must not be committed in the template
```

`packages/picolet/picolet/templates/config-editor/package-lock.json` exists.
Per the PH20 `test_notes_app_structure.py` precedent (same test, same rule), this
file should not be committed to the template directory. Users who scaffold a new
app from this template will receive the dev's locked dependency tree.

---

## Test Execution

- **Command:** `python -m pytest tests/phase-21/test_config_editor.py -v`
- **Total tests:** 127 (including 31 subtests)
- **Passed:** 126
- **Failed:** 1 (implementation bug — see Bugs Found)
- **Skipped:** 0

**Phase-21 exit gate runner:**
`bash tests/phase-21/run.sh` → `15 passed, 0 failed`

**Regression (pytest across phases 05, 07, 11, 13, 17, 18, 19, 20, 21):**
`python -m pytest tests/phase-{05,07,11,13,17,18,19,20,21}/` →
`739 passed, 1 failed (the package-lock.json bug above), 1 xfailed`
No regressions introduced by PH21 in any prior phase.

Note: `tests/phase-06/test_dispatcher.py` has a pre-existing
`ModuleNotFoundError: No module named 'picolet'` that prevents collection.
This is unrelated to PH21.

---

## Coverage Assessment

| Phase Requirement | Covered? | Tests |
|-------------------|----------|-------|
| FR-EX-3: TOML load/save | Yes | TestTomlRoundTrip (20 tests) |
| FR-EX-3: YAML load/save | Yes | TestYamlRoundTrip (18 tests) |
| FR-EX-3: JSON load/save | Yes | TestJsonRoundTrip (8 tests) |
| FR-EX-3: Schema validation (type, required, min/max, enum, pattern, items) | Yes | TestSchemaValidation (18 tests) |
| FR-EX-3: Unified diff on save | Yes | TestUnifiedDiff (11 tests) |
| FR-EX-3: PICOLET_CONFIG_DIR isolation | Yes | test_picolet_config_dir_isolates_schemas |
| FR-EX-3: Template scaffolding | Partial | TestTemplate — passes except package-lock.json bug |
| FR-EX-5: Integration tests present | Not retested | Dev's Playwright tests at examples/config-editor/tests/ skip without CDP |
| FR-EX-6: Screenshots present and valid | Yes | TestScreenshots (9 tests + pixel checks) |
| NFR-EX-1: Binary ≤ 3 MiB | Via run.sh | Gate C: 928 246 bytes |
| NFR-EX-2: Startup ≤ 1500 ms | Via run.sh | Gate E: PASS |
| NFR-EX-3: CSS ≤ 50 KB gzipped | Via run.sh | Gate M: 1272 bytes |
| NFR-EX-4: No CDN references | Via run.sh | Gate D: PASS |
| NFR-EX-5: Deterministic screenshots | Partial | .no-animation class confirmed present; pixel content verified |
| NFR-EX-AESTHETIC: Brutalist CSS | Yes | TestCssAesthetic (16 tests), TestBrutalistConstraints (2 tests) |

---

## Bugs Found

### BUG-1: `package-lock.json` committed in config-editor template (SEVERITY: MEDIUM)

**File:** `packages/picolet/picolet/templates/config-editor/package-lock.json`

**Symptom:** `picolet init <name> --template config-editor` copies
`package-lock.json` into the new project. Users get a lockfile pinned to
the dev's exact environment, which breaks `npm install` on different npm
versions or platforms and defeats template portability.

**Precedent:** The PH20 notes template does not include a `package-lock.json`
(verified — `examples/notes/` has no lockfile). The fix is to delete
`packages/picolet/picolet/templates/config-editor/package-lock.json`
and add it to the template's `.gitignore` or the top-level `.gitignore`.

**Test detecting this:** `TestTemplate::test_no_package_lock_json_in_template`

### BUG-2: O4 datetime round-trip type degradation (SEVERITY: LOW, DOCUMENTED)

**File:** `examples/config-editor/src/tomllib.py` + `src/config_store.py`

**Symptom:** TOML files containing datetime literals (`ts = 2024-01-15T10:30:00Z`)
round-trip with type degradation: `tomllib.py` returns datetimes as strings
(documented), and `_toml_dumps()` then serialises those strings as TOML
quoted strings (`ts = "2024-01-15T10:30:00Z"`), not as bare TOML datetime
literals. The round-tripped file is syntactically valid TOML but the field
type changes from datetime to string.

**Impact:** A config file that a TOML-aware tool writes with datetime fields
will load into config-editor correctly but save back with those fields
converted to quoted strings, breaking downstream parsers that expect the
bare datetime syntax.

**Test confirming this:** `TestTomlRoundTrip::test_toml_with_datetime_raises_type_error_on_dump`

This bug is explicitly documented as deviation D1 / limitation O4 in the
dev report. No fix is expected for v1.1.

---

## Edge Cases Tested

- `tomllib`: hex integer (0xff), float infinity, NaN, array-of-tables ([[header]]),
  dotted keys (a.b.c = "x"), multi-line basic strings, literal strings (no escape
  processing), datetime-as-string O4 behaviour.
- `micro_yaml`: all six boolean aliases (true/false/yes/no/on/off), null variants
  (null/~), inline comment stripping, flow-style rejection (YAMLError), empty document,
  list-root rejection in config_store.
- `config_validator`: exclusiveMinimum/Maximum boundary values (value == boundary
  must be rejected), additionalProperties: false allowing vs rejecting, items
  schema with indexed error paths, schema-not-found sentinel error.
- `difflib`: identical sequences → empty result, ANSI-clean output, embedded
  newline absence in diff elements, no-change save → empty diff list.
- CSS: --error colour scoped exclusively to error-state selectors (checked via
  regex over comment-stripped source); U+258C ▌ and U+2588 █ character presence.
- Screenshots: center pixel darkness (background colour), magenta presence only
  in the error-state screenshot, magenta absence in the no-error screenshots.

---

## Notes for Tester

1. BUG-1 (package-lock.json in template) requires a one-file delete from
   the template directory; the fix is trivial and should be done before
   this phase is closed.

2. The vendored `tomllib.py` must be loaded by file path in tests that
   need to exercise its datetime behaviour, because the stdlib `tomllib`
   (Python 3.11+) is cached in `sys.modules` by pytest before the test
   module initialises. The test file uses `importlib.util.spec_from_file_location`
   to work around this. Any future tests that extend `TestTomlRoundTrip`
   must use `self.tomllib` (the vendored instance) rather than a bare
   `import tomllib`.

3. The Playwright integration tests in `examples/config-editor/tests/` skip
   when no running binary + CDP-capable browser is available (D3 deviation).
   They are not retested here as PH21 SQE; they were verified as
   unconditionally skipping via Gate L.

4. NFR-EX-5 (deterministic screenshots) is partially verified: the
   `.no-animation` CSS class exists and is confirmed applied by `App.vue`.
   The actual byte-identity between runs is not asserted here (it is an
   NFR for CI, not a unit test concern), but the pixel content of all five
   screenshots is verified structurally (black background, magenta presence/
   absence in the correct files).
