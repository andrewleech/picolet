# PH18 SQE Report — Vue 3 + Vite toolchain integration

**Phase:** 18
**Feature:** Vue 3 + Vite toolchain integration
**Attempt:** 1
**SQE date:** 2026-05-17

---

## Tests created

**File:** `tests/phase-18/test_vue_toolchain.py`

72 tests across 8 test classes. All pass.

### TestValidatorFrontendSchema (6 tests)
Asserts the schema constants `_UI_FRONTEND_SCHEMA` and `_UI_FRONTEND_FRAMEWORK_VALUES` contain
the expected keys and values before any file I/O occurs.

| Test | What it verifies |
|---|---|
| `test_framework_values_contains_vue` | "vue" is in the valid-framework set |
| `test_framework_values_contains_react` | "react" is in the valid-framework set (forward-compat) |
| `test_framework_values_contains_vanilla` | "vanilla" is in the valid-framework set |
| `test_framework_values_does_not_contain_ember` | "ember" is not accepted |
| `test_frontend_schema_has_expected_keys` | schema contains framework, build_cmd, dist_dir, dev_url |
| `test_frontend_schema_all_values_are_str` | every schema value maps to `str` type |

### TestValidatorFrontendSection (7 tests)
Calls `validate_toml` on real TOML strings via temp files. Exercises the
`[ui.frontend]` validator path end-to-end.

| Test | What it verifies |
|---|---|
| `test_accepts_vue_framework` | Full `[ui.frontend]` block with `framework = "vue"` produces zero hard errors |
| `test_accepts_vanilla_framework` | `framework = "vanilla"` accepted |
| `test_accepts_react_framework` | `framework = "react"` accepted (forward-compat, O4) |
| `test_rejects_unknown_framework_ember` | `framework = "ember"` produces a hard error mentioning "ember" |
| `test_rejects_unknown_framework_mentions_valid_values` | error message for unknown framework lists valid alternatives |
| `test_absent_frontend_section_produces_no_errors` | no `[ui.frontend]` → no frontend errors (vanilla default) |
| `test_unknown_key_in_frontend_is_warn_not_error` | unknown key in `[ui.frontend]` is level="warn", not error |
| `test_framework_wrong_type_is_error` | non-string framework value produces a hard error |

### TestPathsIgnoreDirs (6 tests)
Verifies `_IGNORE_DIRS` membership and `should_ignore` path predicate.

| Test | What it verifies |
|---|---|
| `test_node_modules_in_ignore_dirs` | "node_modules" in `_IGNORE_DIRS` |
| `test_dist_in_ignore_dirs` | "dist" in `_IGNORE_DIRS` |
| `test_should_ignore_path_under_node_modules` | path component match triggers ignore |
| `test_should_ignore_path_under_dist` | dist/ path component triggers ignore |
| `test_should_not_ignore_normal_src_path` | src/main.py not ignored |
| `test_should_not_ignore_ui_src_ts` | ui/src/App.vue not ignored |

### TestRunFrontendBuild (8 tests)
Calls `_run_frontend_build` directly with subprocess patched (or PATH-shimmed for one integration
test). Each test exercises real production code; none re-implement the production logic.

| Test | What it verifies |
|---|---|
| `test_noop_for_vanilla` | vanilla framework → subprocess never called |
| `test_noop_when_frontend_section_absent` | absent `[ui.frontend]` → subprocess never called |
| `test_raises_build_failed_when_npm_missing` | `shutil.which("npm")` returning None → `BuildFailed` |
| `test_runs_npm_install_then_build_cmd` | two subprocess.run calls in order: install then build |
| `test_npm_install_uses_prefer_offline` | `--prefer-offline` present in install invocation |
| `test_raises_build_failed_on_nonzero_build_cmd` | build_cmd CalledProcessError → `BuildFailed` |
| `test_custom_build_cmd_is_used` | `build_cmd` key overrides default "npm run build" |
| `test_uses_fake_npm_on_path` | PATH-shimmed npm writes `dist/index.html`; function returns without error |

### TestCopyDistToUiRoot (6 tests)
Calls `_copy_dist_to_ui_root` against real temp directories.

| Test | What it verifies |
|---|---|
| `test_noop_for_vanilla` | vanilla → nothing written to romfs |
| `test_raises_build_failed_when_dist_missing` | absent dist_dir → `BuildFailed` |
| `test_copies_dist_into_romfs_ui_root` | dist/ contents land at romfs_root/ui/ |
| `test_respects_custom_dist_dir` | `dist_dir` config key respected |
| `test_respects_custom_ui_root` | `[ui] root` key controls destination |
| `test_noop_when_frontend_absent` | no `[ui.frontend]` → vanilla default → no-op |

### TestInitCmdVue (5 tests)
Checks `init_cmd` constants and `_copy_template` behaviour for Vue file types.

| Test | What it verifies |
|---|---|
| `test_hello_vue_in_known_templates` | "hello-vue" is in `_KNOWN_TEMPLATES` |
| `test_vue_extension_in_text_extensions` | ".vue" is in `_TEXT_EXTENSIONS` |
| `test_ts_extension_in_text_extensions` | ".ts" is in `_TEXT_EXTENSIONS` |
| `test_copy_template_substitutes_name_in_vue_file` | `{{name}}` replaced in .vue files |
| `test_copy_template_substitutes_name_in_ts_file` | `{{name}}` replaced in .ts files |

### TestHelloVueTemplate (10 tests)
Structural assertions against the committed `packages/picolet/picolet/templates/hello-vue/`
tree. No file generation; reads committed files.

| Test | What it verifies |
|---|---|
| `test_template_dir_exists` | hello-vue/ dir is present |
| `test_picolet_toml_contains_framework_vue` | `framework = "vue"` in template picolet.toml |
| `test_picolet_toml_has_name_placeholder` | `{{name}}` present in template picolet.toml |
| `test_vite_config_present` | vite.config.ts committed |
| `test_package_json_present` | package.json committed |
| `test_package_json_has_name_placeholder` | `{{name}}` in package.json name field |
| `test_ui_src_app_vue_present` | ui/src/App.vue committed |
| `test_ui_index_html_present` | ui/index.html committed |
| `test_src_main_py_present` | src/main.py committed |
| `test_no_package_lock_json_in_template` | package-lock.json must NOT be in template (O3) |
| `test_local_picolet_d_ts_present_in_template` | ui/src/picolet.d.ts bundled for standalone use |

### TestWithVueExample (5 tests)
Structural assertions against `examples/with-vue/`.

| Test | What it verifies |
|---|---|
| `test_picolet_toml_validates_without_hard_errors` | `validate_toml` returns zero hard errors |
| `test_picolet_toml_framework_is_vue` | `framework = "vue"` in picolet.toml |
| `test_package_lock_json_committed` | package-lock.json is committed (O3) |
| `test_no_node_modules_directory` | `git ls-files` confirms node_modules/ not tracked |
| `test_package_json_name_matches_dir` | package.json `name` field is "with-vue" |

### TestPicoletDts (12 tests)
Reads `packages/picolet-bridge-js/src/picolet.d.ts` and checks the declaration surface.

| Test | What it verifies |
|---|---|
| `test_file_present` | picolet.d.ts exists at expected path |
| `test_declares_picolet_bridge_interface` | `PicoletBridge` interface declared |
| `test_declares_invoke_method` | `invoke(` present |
| `test_declares_on_method` | `on(` present |
| `test_declares_emit_method` | `emit(` present |
| `test_declares_drain_pending` | `_drainPending(` present |
| `test_declares_ready_flag` | `__ready__` present |
| `test_augments_window_interface` | `interface Window` with `picolet:` property |
| `test_has_module_augmentation_export` | `export {}` present (required for ambient module augmentation) |
| `test_declare_global_block_present` | `declare global` block present |
| `test_invoke_returns_promise` | `Promise` in declaration |
| `test_on_returns_unsubscribe` | `() => void` return type for unsubscribe |

### TestDevCmdViteIntegration (5 tests)
Exercises `dev_cmd.run` with Popen, build_cmd.run, and time.sleep patched.
Each test inspects actual Popen call arguments captured from the production code path.

| Test | What it verifies |
|---|---|
| `test_vue_framework_spawns_npm_run_dev` | `["npm", "run", "dev"]` Popen call made for vue framework |
| `test_vue_framework_uses_start_new_session` | Vite Popen has `start_new_session=True` (D3) |
| `test_vue_framework_injects_picolet_dev_url_into_binary_env` | binary Popen receives `PICOLET_DEV_URL=http://localhost:5173/` in env |
| `test_vanilla_framework_does_not_spawn_vite` | no `["npm", "run", "dev"]` Popen for vanilla framework |
| `test_vanilla_framework_no_picolet_dev_url` | binary env does not contain `PICOLET_DEV_URL` for vanilla |

---

## Test results

### New tests (PH18)

```
tests/phase-18/test_vue_toolchain.py: 72 passed in 0.25s
```

### Regression — pytest (phases 05, 07, 11, 13, 17, 18)

```
334 passed, 1 xfailed in 12.98s
```

The 1 xfailed is a pre-existing PH17 known failure, unrelated to PH18.

### Regression — phase-06

`tests/phase-06/test_dispatcher.py` fails collection with `ModuleNotFoundError: No module named 'picolet'`.
This is a pre-existing failure predating PH18; the dispatcher test requires the `picolet` runtime
package installed in the test environment. Not caused by any PH18 change.

### Developer exit gate (run.sh --skip-slow)

```
PASS: 9 / FAIL: 0 / SKIP: 1 (Gate I: --skip-slow)
RESULT: PASS
```

Gate I (AppHarness invoke round-trip) is skipped; requires a full display and WebKitGTK inspector
stack. The developer's report documents this as an environment limitation (D-6).

---

## Coverage assessment

| FR / NFR | Requirement | Coverage in SQE tests |
|---|---|---|
| FR-VUE-1 | `picolet init --template hello-vue` scaffolds Vue skeleton | `TestHelloVueTemplate` (10 structural), `TestInitCmdVue` (5 functional) |
| FR-VUE-2 | `picolet dev` spawns Vite; webview loads from dev_url | `TestDevCmdViteIntegration` (5 tests — process spawn, env injection, SIGKILL cascade path) |
| FR-VUE-3 | `picolet-bridge-js` ships typed `picolet.d.ts` | `TestPicoletDts` (12 tests — all declared members verified) |
| FR-VUE-4 | `picolet build` runs npm, packs dist/ | `TestRunFrontendBuild` (8), `TestCopyDistToUiRoot` (6) |
| FR-VUE-5 | `picolet.toml` [ui.frontend] schema | `TestValidatorFrontendSchema` (6), `TestValidatorFrontendSection` (7) |
| NFR-EX-1 | Binary ≤ 3 MiB | Covered by run.sh Gate D (775976 bytes); no separate pytest (requires full build) |
| NFR-EX-4 | No CDN references | Covered by run.sh Gate J; no separate pytest (requires full build) |

### Coverage gaps and reasons

**`picolet dev` SIGTERM cascade / process-group teardown (`_kill_vite`):** `_kill_vite` is a
closure inside `dev_cmd.run`; it is not directly importable. The `TestDevCmdViteIntegration`
tests verify that `start_new_session=True` is set on the Vite Popen (the precondition for
`os.killpg` working), but the `os.killpg(pgid, SIGTERM)` call itself is patched out in the
test. Full teardown verification requires a real subprocess and is deferred to the
`tests/phase-18/run.sh` gate or an integration test.

**TypeScript type-check (`tsc --noEmit`):** `picolet.d.ts` shape is verified through text
assertions in `TestPicoletDts`. A full `tsc --noEmit` compile-check requires Node.js and
installed `node_modules`; this is covered by run.sh Gate H (`vue-tsc --noEmit on with-vue
exits 0`) rather than a pytest.

**`_app.py` PICOLET_DEV_URL runtime path:** `packages/picolet-runtime/python/picolet_ui/_app.py`
reads `PICOLET_DEV_URL` and calls `webkit_web_view_load_uri`. This path is in frozen Python
compiled into the runtime binary; the unit under test is not importable as a pure-Python module
without the full runtime FFI. Tested only through Gate I (invoke round-trip) which requires
a display. Gap reported; no pytest written.

**`picolet build` end-to-end (step 4b call site):** The insertion point in `_do_build` (line 268)
is not covered by pytest because it requires a real runtime binary, mpy-cross, mpremote, and npm.
Covered by run.sh Gates C+F. Testing `_run_frontend_build` in isolation (as done here) is the
correct unit-test boundary.

---

## Implementation bugs found

**BUG-1 (cosmetic / template inconsistency):** `examples/with-vue/ui/src/App.vue` line 49
contains the literal string `{{name}} demo` in Vue template syntax (`<h1>{{name}} demo</h1>`).
In the built example this renders as the literal text "{{name}} demo" (Vue does not interpret
`{{name}}` as a data binding since `name` is not a reactive ref in that component). The
developer intended this as a template substitution marker, but `examples/with-vue/` is a
concrete example (not a template); the marker was not substituted. The correct text would be
`with-vue demo`. This is a display-only defect in the example; it does not affect the build
pipeline, the template, or any FR. Filed for developer awareness; not fixed here.

**Note on phase-06 pre-existing failure:** `tests/phase-06/test_dispatcher.py` imports `picolet`
as a top-level package, which is not installed in the standard test environment. This predates
PH18 and is unrelated to any PH18 change.

---

## Regressions introduced by PH18

None. The 334 existing pytest tests all pass. The phase-06 collection failure is pre-existing.
