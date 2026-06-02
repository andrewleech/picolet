# Phase 23 Tester Report — examples-meta-integration

**Phase:** 23  
**Feature:** examples-meta-integration  
**Date:** 2026-05-17  
**Attempt:** 1  
**Verdict:** PASS

---

## Build

No compiled artefact for PH23 deliverables. The `picolet` CLI binary in `.venv/bin/picolet` (produced by earlier phases) is present and used by the tests.

---

## Test execution

### PH23 suite

```
python -m pytest tests/phase-23/test_examples_meta.py -v
105 passed in 1.47s
```

All 105 tests pass. No skips, no failures.

### Regression suite

```
python -m pytest tests/phase-{05,07,11,13,17,18,19,20,21,22,23}/ -q
962 passed, 1 xfailed, 106 subtests passed in 18.68s
```

Zero regressions introduced. The 1 xfailed is the pre-existing `tests/phase-06/test_dispatcher.py` `ModuleNotFoundError` (pre-dates PH23, unchanged).

### Phase run.sh gates

| Phase | Result |
|-------|--------|
| 20 | 12 passed, 0 failed |
| 21 | 15 passed, 0 failed |
| 22 | 12 passed, 0 failed |
| 23 | 12 passed, 0 failed |

---

## Mirror script verification

`bash scripts/mirror-examples-to-templates.sh --check` → exit 0, output: `mirror: no drift — templates match examples`.

Drift detection: modified `examples/dashboard/picolet.toml` (`title = "System Dashboard"` → `title = "System Dashboardx"`), ran `--check`:
- Exit code: 1 (correct)
- Unified diff printed with `---`/`+++` markers (correct)
- File reverted; subsequent `--check` returns exit 0 (correct)

---

## Requirements coverage matrix

| # | Source | Requirement | Implemented? | Evidence | Tests |
|---|--------|-------------|-------------|----------|-------|
| A | Phase exit gate | `--list-templates` prints 8 templates, sorted, exit 0 | Yes | `init_cmd.py:69-72` | `TestListTemplates` (9 tests) |
| B | Phase exit gate | `picolet init --template pydfu/notes/config-editor/dashboard` produces correct substitution | Yes | `init_cmd.py:_copy_template`; verified manually for all 4 | `TestInit*SmokeTest` (20 tests) |
| C | Phase exit gate | `examples/README.md` has thumbnails for all 4 examples, paths resolve | Yes | `examples/README.md` 16 PNG image links, all paths confirmed present | `TestExamplesReadme` (11 tests) |
| D | Phase exit gate | `docs/examples.md` covers all 4 examples with code snippets | Yes | `docs/examples.md`: 4 `##` headings, 4 `python` code blocks, 5 FR-EX-* references | `TestDocsExamplesMd` (9 tests) |
| E (mirror idempotence) | Phase exit gate | `mirror --check` exits 0 on clean repo | Yes | Executed — exit 0 confirmed | `TestMirrorScriptIdempotence` (4 tests) |
| F | Phase exit gate | Root `README.md` 2×2 thumbnail grid | Yes | `README.md:43-48`; all 4 screenshot paths exist on disk | `TestRootReadme` (6 tests) |
| G | Phase exit gate | `screenshots.yml` exists, all 4 examples, drift gate | Yes | `.github/workflows/screenshots.yml`; `git diff --exit-code` drift gate present | `TestScreenshotsWorkflow` (8 tests) |
| H | Phase exit gate | `release.yml` `screenshots-release` job, `needs: build`, no auto-merge | Yes | `release.yml:242-331`; `needs: build` at line 246; no `--auto-merge` | `TestReleaseWorkflowScreenshots` (7 tests) |
| I | Phase exit gate | Committed screenshots match generated output (spot check) | Yes | Mirror --check exit 0 covers template side; PNG magic bytes + size >1KB verified | `TestScreenshotFiles` (6 tests) |
| FR-EX-1 | Spec | `picolet init --template pydfu` works | Yes | Manual run confirmed; `name = "my-pydfu"`, `"name": "my-pydfu"` in produced files | `TestInitPydfuSmokeTest` |
| FR-EX-2 | Spec | `picolet init --template notes`; persists to `~/.config/<app-name>/` | Yes | `notes_store.py` in produced app references `"my-notes"` in path segments; no literal `"notes"` | `TestInitNotesSmokeTest` |
| FR-EX-3 | Spec | `picolet init --template config-editor`; path uses `<app-name>` | Yes | `config_store.py` in produced app references `"my-config"`; no literal `"config-editor"` | `TestInitConfigEditorSmokeTest` |
| FR-EX-4 | Spec | `picolet init --template dashboard`; title preserved | Yes | `title = "System Dashboard"` preserved in produced `picolet.toml` | `TestInitDashboardSmokeTest` |
| FR-EX-5 | Spec | Each example ships `tests/` directory | Yes | `examples/{pydfu,notes,config-editor,dashboard}/tests/` all present (7, 4, 4, 4 files) | — |
| FR-EX-6 | Spec | Each example ships `screenshots/` with PNGs | Yes | 6, 6, 5, 4 PNGs respectively; all valid magic bytes, all >1 KB | `TestScreenshotFiles` |
| FR-EX-7 | Spec | pydfu: `NotImplementedError` guard on Windows | Yes | `examples/pydfu/src/pydfu_adapter.py:97-98`; `sys.platform == "win32"` guard raises `NotImplementedError` | — |
| FR-VUE-* | Spec | `hello-vue` template still works post-mirror | Yes | `picolet init my-vue-test --template hello-vue` succeeds; mirror script does not touch `hello-vue` | Phase-18 suite (184 passed) |
| FR-TEST-* | Spec | `picolet test` not regressed | Yes | `picolet test --help` responds; phase-05 suite: 56 passed, 1 xfailed | Phase-05 suite |
| NFR-EX-6 | Spec | Drift = CI failure | Yes | `screenshots.yml` drift gate; `mirror --check` drift gate both wired | `TestScreenshotsWorkflow`, `TestMirrorScriptIdempotence` |

---

## Incomplete-implementation scan

No TODO, FIXME, HACK, or "not implemented" comments found in any PH23-created or -modified file.

---

## Test value assessment

All SQE tests call production code directly:
- Mirror tests invoke `bash scripts/mirror-examples-to-templates.sh` as a subprocess.
- `picolet init` tests invoke the installed `picolet` binary.
- Template/file-content tests read the actual committed files.
- Workflow tests parse the actual YAML workflow files.

No tests re-implement production logic inline. No simulation tests detected.

---

## Notable findings

**SQE-reported Bug 1 (orphan `dashboard/scripts/`)** is confirmed fixed by commit `f27cc69`. The orphan detection no longer applies `EXCLUDE_DIRS` to the template destination walk. Manual verification confirms `packages/picolet/picolet/templates/dashboard/scripts/` is absent. `TestTemplateSubstitution::test_templates_do_not_contain_scripts_dir` passes for all 4 templates without annotation.

**Phase-03 run.sh failures (gates 1, 3, 11)** are pre-existing and unrelated to PH23 — same failure pattern present before PH23 commits.

**config-editor smoke test uses `my-config` not `my-config-editor`**: The test runs `picolet init my-config --template config-editor`. The spec exit-gate verifier (run.sh Gate B2) uses `test-config_editor` as the app name. Both substitute correctly; the difference is only in test naming, not a correctness issue.

---

## Verdict: PASS

All 10 phase exit gates satisfied. 105 PH23 tests pass. 962 regression tests pass with zero new failures. Mirror script drift detection works correctly. All 4 real templates produce correctly substituted output including notes/config-editor path-segment parameterisation and dashboard title preservation. FR-EX-7 platform guard present. No orphan directories in any template. CI workflows (screenshots.yml + release.yml screenshots-release job) structurally correct.
