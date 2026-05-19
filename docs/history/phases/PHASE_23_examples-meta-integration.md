# PH23 — examples-meta-integration

## Plan

### Goal

Close the v1.1 examples epic with six pieces of glue work: a mirror script
that makes `picolet_templates/` a derivative of `examples/`; `--list-templates`
support in `picolet init`; a cross-example `examples/README.md`; a narrative
`docs/examples.md` tour; a screenshot-regeneration CI job; and a root
`README.md` update with a 2×2 thumbnail grid. Nothing here is novel runtime
work — it is all tooling, documentation, and CI wiring on top of PH19–PH22.

---

### Spec coverage

| Spec ID | Requirement | Chunk |
|---|---|---|
| FR-EX-1 | `picolet init <name> --template pydfu` scaffolds the pydfu flasher | Chunk 2 — `--list-templates` output + template wiring verification |
| FR-EX-2 | `picolet init <name> --template notes` scaffolds the notes app | Chunk 2 |
| FR-EX-3 | `picolet init <name> --template config-editor` scaffolds the config editor | Chunk 2 |
| FR-EX-4 | `picolet init <name> --template dashboard` scaffolds the dashboard | Chunk 2 |
| FR-EX-6 | Each example ships `screenshots/` with auto-generated PNGs | Chunk 5 — CI job regenerates them on every build |
| NFR-EX-5 | Deterministic screenshots; same inputs → byte-identical PNG | Chunk 5 — existing `generate_screenshots.py` pattern unchanged; CI verifies |
| NFR-EX-6 | Screenshot gallery regenerated on every CI build; drift is CI failure | Chunk 1 (mirror drift check) + Chunk 5 (screenshot drift check) |

All FR-EX-{1,2,3,4} were substantively implemented in PH19–PH22. PH23's
contribution is confirming end-to-end discoverability (`--list-templates`),
keeping the templates in sync with the examples via the mirror script, and
enforcing that gap in CI.

---

### Dependencies

- PH19–PH22 all passed: `examples/{pydfu,notes,config-editor,dashboard}/`
  are complete, built, and have committed screenshots.
- `packages/picolet-templates/picolet_templates/{pydfu,notes,config-editor,dashboard}/`
  already exist and are mostly in sync with examples (drift catalogued below).
- `packages/picolet-cli/picolet_cli/init_cmd.py` already lists all four templates
  in `_KNOWN_TEMPLATES` and the `--help` string. The `--list-templates` flag
  and clean machine-readable listing do not yet exist.
- `.github/workflows/release.yml` exists; only `release.yml` is present in
  `.github/workflows/` — no screenshot workflow yet.

---

### Key research findings

#### Current template set vs examples: drift inventory

Running `diff -rq` (excluding `node_modules/`, `dist/`, `target/`,
`screenshots/`, `scripts/`, `tests/`, `package-lock.json`) between each
`examples/<name>/` and `packages/picolet-templates/picolet_templates/<name>/`
reveals the following:

**pydfu — 4 files differ:**

| File | Difference |
|---|---|
| `package.json` | `"name": "pydfu"` vs `"name": "{{name}}"` |
| `picolet.toml` | `name = "pydfu"`, `title = "PyDFU"` vs `name = "{{name}}"`, `title = "{{name}}"` |
| `src/main.py` | Comment line 1 (`# pydfu — DFU...` vs `# {{name}} — DFU...`); error-sentinel docstring present in example but stripped in template (the sentinel code itself is identical — only the docstring differs) |
| `ui/index.html` | `<title>PyDFU</title>` vs `<title>{{name}}</title>` |

**notes — 5 items differ:**

| File / Dir | Difference |
|---|---|
| `package.json` | `"name": "notes"` vs `"name": "{{name}}"` |
| `picolet.toml` | `name = "notes"`, `title = "Notes"` vs `name = "{{name}}"`, `title = "{{name}}"` |
| `src/main.py` | Comment line 1 |
| `src/notes_store.py` | Template uses `_APP_NAME = "{{name}}"` as an indirection; example has the literal path `"notes"` hardcoded in three places. Template also has minor comment refinements. |
| `ui/index.html` | `<title>Notes</title>` vs `<title>{{name}}</title>` |
| `ui/src/views/AboutView.vue` | Hard-coded `"Notes"` and `~/.config/notes/` vs `{{name}}` tokens |
| `ui/src/components/` | Empty directory present in example, absent from template |

**config-editor — 4 files differ:**

| File | Difference |
|---|---|
| `package.json` | `"name": "config-editor"` vs `"name": "{{name}}"` |
| `picolet.toml` | `name = "config-editor"`, `title = "Config Editor"` vs `{{name}}` |
| `src/config_store.py` | Literal `"config-editor"` path components vs `"{{name}}"` |
| `src/main.py` | Comment line 1 |
| `ui/index.html` | `<title>Config Editor</title>` vs `<title>{{name}}</title>` |

**dashboard — 0 meaningful drift:**

Both `examples/dashboard/` and the template use the concrete string `"dashboard"`
throughout (no `{{name}}` substitution applied to this template in PH22). The
only differences are the example-only directories (`screenshots/`, `tests/`,
`scripts/`) which are not part of the template set.

This is the load-bearing inconsistency PH23 must resolve: dashboard's
`picolet.toml`, `package.json`, and `index.html` all contain concrete `"dashboard"`
/ `"System Dashboard"` rather than `{{name}}`. The other three templates use
`{{name}}` throughout. The window title for pydfu uses `"PyDFU"` in the example
and `"{{name}}"` in the template — a meaningful degradation for `picolet init`
users who get `title = "<their-app-name>"` rather than `"PyDFU"`.

**Decision the developer must make (flagged as open question below):**

The mirror script is defined as: examples are authoritative, templates are
derived. Applying that rule means the mirror must transform concrete names
into `{{name}}` tokens. For pydfu, the transformation is
`"PyDFU"` → `"{{name}}"` (acceptable — user picks their own title).
For dashboard, `"System Dashboard"` → `"{{name}}"` produces an ugly default.
Options:

A. Keep `dashboard` template with `title = "System Dashboard"` and
   `name = "{{name}}"` — the title stays descriptive regardless of user's app
   name. The mirror script special-cases `dashboard`: it substitutes
   `"dashboard"` → `"{{name}}"` in `package.json` and `picolet.toml [app] name`
   only, leaving `title = "System Dashboard"` unchanged.

B. Let the mirror script substitute `"dashboard"` → `"{{name}}"` everywhere,
   including the window title. Consistent but produces `title = "my-app"` for
   someone who ran `picolet init my-app --template dashboard`.

Option A is the right outcome; the mirror script must be per-field-aware for
`picolet.toml` substitution, not a raw text replace.

#### init_cmd.py: `--list-templates` does not yet exist

`packages/picolet-cli/picolet_cli/init_cmd.py` has no `--list-templates` flag.
`_KNOWN_TEMPLATES` is a `frozenset` that already includes all eight templates
(including the four real ones). The `--help` string on `--template` lists them
inline. Adding `--list-templates` means: if that flag is present, print each
template name (one per line, sorted), skip all other logic, exit 0.

This is a one-argument addition to `add_parser` plus a short-circuit at the
top of `run()`.

#### CI workflow: release.yml vs a separate workflow

`release.yml` is the only workflow file. It triggers on `push` of tags matching
`runtime-v*`. The screenshot job as described in the plan commits PNGs to a
`screenshots-vX.Y.Z` branch and opens a PR for human review — this is
release-time behaviour.

NFR-EX-6 requires "regenerated on every CI build" with drift as a CI failure.
These are two different concerns:

1. **Drift gate** (every push to `dev`): run `generate_screenshots.py` for all
   four examples, compare against committed PNGs, fail if any differ.
2. **Release gallery regen** (on release tag): regenerate, commit to a sidecar
   branch, open a human-review PR.

The drift gate belongs in a separate workflow (`screenshots.yml`) triggered on
`push` to `dev` and `pull_request`. The release gallery regen goes in
`release.yml` as an additional job after `build`.

Both use `generate_screenshots.py` (Playwright headless Chromium against
`dist/`). No `picolet` binary and no Xvfb required — that was the PH19 finding
that the WebKit-inspector path is unreliable in CI.

#### generate_screenshots.py pattern

All four examples have `examples/<name>/scripts/generate_screenshots.py` using
the same shape: PEP 723 inline deps (`playwright>=1.40`, `pillow>=10.0`),
`uv run` entry, headless Chromium against `dist/`, mock `window.picolet` injected
via `add_init_script`. The CI steps are:

```
npm --prefix examples/<name> run build
uv run examples/<name>/scripts/generate_screenshots.py
```

No `DISPLAY`, no Xvfb, no picolet binary.

#### Files to exclude from mirror

The mirror script copies template content only; it must exclude:
`screenshots/`, `scripts/` (screenshot generators), `tests/`, `dist/`,
`node_modules/`, `target/`, `package-lock.json`. These are
example-development artefacts, not scaffolding content.

#### `with-vue` example

`examples/with-vue/` has no `picolet-templates` counterpart except
`picolet_templates/hello-vue/` which was written in PH18. These are
**different** templates. `with-vue` is the baseline Vue integration example;
`hello-vue` is the starter template. The mirror script should not touch
`with-vue` — only `{pydfu,notes,config-editor,dashboard}`.

---

### Open questions

1. **Dashboard `title` field**: Should the mirror script preserve
   `title = "System Dashboard"` in the dashboard template (Option A) or
   substitute it to `"{{name}}"` (Option B)? This requires an explicit
   decision before the mirror script is written. Recommendation: Option A.
   The developer should confirm with the user before implementing.

2. **pydfu `title = "PyDFU"` vs `"{{name}}"`**: The current template has
   `title = "{{name}}"`. The example has `title = "PyDFU"`. Post-mirror, a
   user doing `picolet init my-flasher --template pydfu` would get
   `title = "my-flasher"` — not `"PyDFU"`. Is that intended? It is consistent
   with how `notes` and `config-editor` work. No action needed unless the
   user wants per-template default titles preserved (which would require a
   `{{title}}` token distinct from `{{name}}`). Document the decision in a
   `[PH23] Decision:` commit.

3. **Empty `notes/ui/src/components/` directory**: The example has an empty
   `components/` directory; the template does not. The mirror script will copy
   it as an empty directory. `git` does not track empty directories. Decision:
   add a `.gitkeep` placeholder when copying empty directories, or simply omit
   empty directories from the mirror output. The latter is cleaner.

---

### Implementation breakdown

#### Chunk 1 — Mirror script (`scripts/mirror-examples-to-templates.sh`)

**What it does**: For each of `{pydfu, notes, config-editor, dashboard}`:
1. Delete the corresponding `packages/picolet-templates/picolet_templates/<name>/`
   content (or rsync with `--delete`).
2. Copy the source files from `examples/<name>/` to the template directory,
   excluding: `node_modules/`, `dist/`, `target/`, `screenshots/`, `scripts/`,
   `tests/`, `package-lock.json`, `.pytest_cache/`, `__pycache__/`.
3. In all text files (`.py`, `.toml`, `.html`, `.vue`, `.ts`, `.json`, `.md`,
   `.css`, `.yaml`, `.yml`, `.txt`) perform name substitution.
4. After copying all four, run `git diff --name-only` on the template
   directories. If any changes are detected, print the full `git diff` output
   and exit 1 (drift detected). If no changes, exit 0.

**Name substitution table** (per example):

| Example | Concrete token → `{{name}}` replacement rules |
|---|---|
| `pydfu` | `"pydfu"` → `"{{name}}"` in `package.json` `.name` field only; `name = "pydfu"` → `name = "{{name}}"` in `picolet.toml`; comment line 1 pattern; `<title>PyDFU</title>` → `<title>{{name}}</title>`; `# pydfu —` → `# {{name}} —` in `.py` comments |
| `notes` | Same pattern; `"notes"` and `"Notes"` → `"{{name}}"` in respective files; `notes_store.py` paths; `AboutView.vue` display strings |
| `config-editor` | `"config-editor"` and `"Config Editor"` in respective files |
| `dashboard` | `"dashboard"` in `package.json [name]` and `picolet.toml [app] name` ONLY. `title = "System Dashboard"` is preserved as-is. No other substitutions. |

**Idempotency**: The script checks git state after copying. If it is run
against an already-up-to-date template directory, `git diff` produces no
output and it exits 0.

**Implementation note**: Rather than shell-level `sed`, the substitution is
cleaner as a small embedded Python snippet (heredoc inside the bash script).
This avoids `sed` portability issues with multiline replacements and the
`config-editor` hyphen.

**File**: `scripts/mirror-examples-to-templates.sh`

**Pattern reference**: `examples/pydfu/screenshots/capture_screenshots.sh` for
bash script structure; the substitution logic should mirror the approach in
`packages/picolet-cli/picolet_cli/init_cmd.py:_copy_template()` (which does the
reverse direction: `{{name}}` → concrete name in text files).

---

#### Chunk 2 — `--list-templates` in `init_cmd.py`

**File**: `packages/picolet-cli/picolet_cli/init_cmd.py`

Add `--list-templates` flag to `add_parser()`:

```python
p.add_argument(
    "--list-templates",
    action="store_true",
    default=False,
    help="print available template names (one per line) and exit",
)
```

Add short-circuit at the top of `run()`:

```python
if getattr(args, "list_templates", False):
    for t in sorted(_KNOWN_TEMPLATES):
        print(t)
    return
```

The output must include all eight templates currently in `_KNOWN_TEMPLATES`:
`config-editor`, `dashboard`, `hello-cli`, `hello-lvgl`, `hello-vue`,
`hello-webview`, `notes`, `pydfu`.

The exit gate requires the four real templates to be discoverable. The three
`hello-*` templates stay in the set; they are valid scaffolds. No change to
`_KNOWN_TEMPLATES` content needed.

**Verify**: `picolet init --list-templates` prints eight lines, one per template,
sorted alphabetically, with no additional output.

---

#### Chunk 3 — `examples/README.md`

Replace the current placeholder content (which still references only
`hello-cli`, `hello-webview`, `hello-lvgl`) with a cross-example narrative.

**Structure**:

```markdown
# Picolet examples

Four worked applications demonstrating the Picolet framework across distinct
use cases and aesthetic directions.

| Example | What it demonstrates | Aesthetic |
|---|---|---|
| [pydfu](pydfu/) | Host FS + USB + long-running tasks + progress events | Industrial control panel |
| [notes](notes/) | Host FS persistence + multi-route Vue + markdown rendering | Editorial / refined |
| [config-editor](config-editor/) | Structured-data manipulation + validation pipeline | Brutalist terminal |
| [dashboard](dashboard/) | 1 Hz event push + history buffer + custom SVG dataviz | Sophisticated data-dense |

## Screenshots

### pydfu

| device-list-empty | device-list-populated | flash-mid-progress | flash-complete |
|---|---|---|---|
| ![](pydfu/screenshots/device-list-empty.png) | ![](pydfu/screenshots/device-list-populated.png) | ![](pydfu/screenshots/flash-mid-progress.png) | ![](pydfu/screenshots/flash-complete.png) |

... (same pattern for notes, config-editor, dashboard)

## Using these as templates

Each example is available as a `picolet init` template:

    picolet init my-app --template pydfu
    picolet init my-notes --template notes
    picolet init my-config --template config-editor
    picolet init my-dash --template dashboard

Run `picolet init --list-templates` to see all available templates.
```

Thumbnail images use relative paths (`pydfu/screenshots/device-list-empty.png`
etc.) — these resolve correctly in GitHub's Markdown renderer from
`examples/README.md`.

**Note**: do not use HTML `<img width="...">` tags. Standard Markdown image
syntax is sufficient; GitHub scales them in the table layout.

---

#### Chunk 4 — `docs/examples.md`

Narrative tour document. Four sections, one per example. Each section:
- One paragraph on the use case and what it demonstrates about Picolet.
- The relevant spec IDs it covers.
- A 2-column screenshot grid (two images wide).
- A code snippet showing the key Python-side pattern (the `@picolet.command`
  or `picolet.emit` call that is the architectural centrepiece).
- A "try it" block: `picolet init <name> --template <template>`.

Emphasis per example (from the plan):
- **pydfu**: host FS access + USB + long-running background task +
  per-block progress events via `picolet.emit`.
- **notes**: host FS persistence in the platform config directory + multi-route
  Vue Router + markdown rendering delegated to JS (`marked` in the Vite bundle).
- **config-editor**: structured-data read/parse/validate/serialise pipeline;
  TOML/YAML/JSON support; diff confirmation before save.
- **dashboard**: 1 Hz `asyncio` push loop; 60-sample ring buffer maintained
  Python-side; `get_history()` bootstrap command; pure-SVG chart paths computed
  in Vue and animated with CSS `transition: d`.

The document should be factual and specific, not promotional. Link to the
example directory and to the relevant spec IDs. Length: ~400–600 words; four
section headings, one per example.

---

#### Chunk 5 — Screenshot drift CI (`.github/workflows/screenshots.yml`)

A new workflow file `screenshots.yml` separate from `release.yml`.

**Trigger**: `push` to `dev`, `pull_request` targeting `dev` or `main`.

**Job structure**:

```yaml
name: Regenerate and verify screenshots

on:
  push:
    branches: [dev, main]
  pull_request:
    branches: [dev, main]

jobs:
  screenshots:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install Playwright Chromium
        run: uv run --with playwright playwright install chromium --with-deps

      - name: Build and regenerate screenshots (pydfu)
        run: |
          npm --prefix examples/pydfu ci
          npm --prefix examples/pydfu run build
          uv run examples/pydfu/scripts/generate_screenshots.py

      - name: Build and regenerate screenshots (notes)
        run: |
          npm --prefix examples/notes ci
          npm --prefix examples/notes run build
          uv run examples/notes/scripts/generate_screenshots.py

      - name: Build and regenerate screenshots (config-editor)
        run: |
          npm --prefix examples/config-editor ci
          npm --prefix examples/config-editor run build
          uv run examples/config-editor/scripts/generate_screenshots.py

      - name: Build and regenerate screenshots (dashboard)
        run: |
          npm --prefix examples/dashboard ci
          npm --prefix examples/dashboard run build
          uv run examples/dashboard/scripts/generate_screenshots.py

      - name: Assert no screenshot drift
        run: |
          git diff --exit-code examples/*/screenshots/*.png \
            || (echo "Screenshot drift detected — run generate_screenshots.py locally and commit the result." && exit 1)
```

**Note on `git diff` for PNG**: Git tracks PNGs as binary. `git diff --exit-code`
returns non-zero if any tracked file has changed, regardless of type. This is
the correct drift gate.

**Note on `npm ci` vs `npm install`**: Use `npm ci` (uses `package-lock.json`)
for reproducibility in CI. `node_modules/` is not committed; `package-lock.json`
is.

**No Xvfb required**: `generate_screenshots.py` uses Playwright's headless
Chromium directly, which works without a display server. Confirmed for all
four examples (all four scripts use `headless: True` and serve `dist/` over a
local HTTP server).

**Release-time screenshot job (in `release.yml`)**: After the six `build` jobs
complete, add a `screenshots-release` job that:
1. Runs the same four build+regen steps.
2. Creates a branch `screenshots-${GITHUB_REF_NAME}` off the current commit.
3. Commits any changed PNGs with `git commit --allow-empty -m "[release] Regenerate screenshots for ${TAG}"`.
4. Opens a PR via `gh pr create --title "Screenshots for ${TAG}" --body "Auto-generated. Review visual changes before merging." --base dev --head screenshots-${TAG}`.
5. Does NOT set auto-merge.

The release job needs `pull-requests: write` permission in addition to the
existing `contents: write`.

---

#### Chunk 6 — Root `README.md` update

The current root `README.md` has no link to `examples/` and no thumbnail grid.

Add after the existing "Repository layout" section:

```markdown
## Examples

Four worked applications. See [`examples/`](examples/) for the full source.

| pydfu | notes |
|---|---|
| ![pydfu — industrial control panel](examples/pydfu/screenshots/device-list-populated.png) | ![notes — editorial refined](examples/notes/screenshots/list-populated.png) |

| config-editor | dashboard |
|---|---|
| ![config-editor — brutalist terminal](examples/config-editor/screenshots/edit-toml.png) | ![dashboard — data-dense](examples/dashboard/screenshots/full-dashboard.png) |
```

The 2×2 layout using two separate tables (each 1×2) renders correctly on
GitHub. A single 2×2 Markdown table with images is not standard Markdown and
has inconsistent renderer support.

Screenshot paths are relative from repo root. They resolve correctly in
GitHub's Markdown renderer.

Also update the existing "Status" section from "Pre-alpha scaffolding. No
releases." to reflect the current state: v1.1 examples complete, see
`examples/` and `docs/examples.md`.

---

### Integration points

| Surface | Where it connects |
|---|---|
| `scripts/mirror-examples-to-templates.sh` | Reads `examples/{pydfu,notes,config-editor,dashboard}/`; writes `packages/picolet-templates/picolet_templates/{pydfu,notes,config-editor,dashboard}/` |
| `init_cmd.py --list-templates` | Reads `_KNOWN_TEMPLATES` frozenset; no file I/O |
| `screenshots.yml` | Reads `examples/*/scripts/generate_screenshots.py`; reads `examples/*/screenshots/*.png` via `git diff` |
| `release.yml` screenshots job | Same as above; additionally uses `gh pr create` (needs `pull-requests: write`) |
| `examples/README.md` | References `examples/*/screenshots/*.png` as relative paths |
| `docs/examples.md` | References same screenshots; references `examples/*/src/main.py` for code snippets |
| `README.md` | References `examples/*/screenshots/*.png` as relative paths from repo root |

---

### Exit gate

The phase passes when all of the following are true:

- [ ] A. `picolet init --list-templates` prints all eight templates on stdout, one
       per line, sorted, and exits 0.
- [ ] B. `picolet init my-pydfu --template pydfu` completes without error; the
       produced `my-pydfu/picolet.toml` contains `name = "my-pydfu"`;
       `package.json` contains `"name": "my-pydfu"`.
- [ ] C. Same as B for `notes`, `config-editor`, `dashboard`.
- [ ] D. `bash scripts/mirror-examples-to-templates.sh` exits 0 against the
       current state of the repo (no drift after PH23 lands).
- [ ] E. `examples/README.md` contains thumbnail links to all four examples'
       screenshot directories.
- [ ] F. `docs/examples.md` exists and covers all four examples with at least
       one code snippet each.
- [ ] G. `README.md` at repo root contains a 2×2 thumbnail grid referencing
       `examples/*/screenshots/*.png` (relative paths resolve from root).
- [ ] H. `.github/workflows/screenshots.yml` exists; job steps cover all four
       examples; `git diff --exit-code examples/*/screenshots/*.png` is the
       drift gate.
- [ ] I. `release.yml` has a `screenshots-release` job with `needs: build`;
       it commits regenerated PNGs to a `screenshots-${TAG}` branch and opens
       a human-review PR with auto-merge off.
- [ ] J. `git diff --exit-code examples/*/screenshots/*.png` exits 0 on the
       current repo (committed screenshots match what `generate_screenshots.py`
       would produce — spot-checked for at least two examples).

---

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Mirror script's text substitution corrupts a binary asset (font `.woff2`, etc.) | Low | The mirror script must restrict substitution to text extensions only (same list as `init_cmd._TEXT_EXTENSIONS`); binary files are copied verbatim |
| Dashboard `title = "System Dashboard"` choice creates a permanent two-tier inconsistency | Medium | Document the decision in a `[PH23] Decision:` commit; consider whether a `{{title}}` token distinct from `{{name}}` is warranted (backlog item) |
| `npm ci` in CI fails due to `package-lock.json` being out of sync with `package.json` (dependency added in PH19–PH22 without updating the lock file) | Medium | Run `npm install` locally first to regenerate lock files if CI fails; check lock file is committed for all four examples |
| GitHub `git diff --exit-code` on PNGs passes even when images differ because PNG headers differ (e.g. timestamp metadata) | Low | `generate_screenshots.py` for all four examples disables CSS animations and uses deterministic mock data; Playwright headless Chromium produces stable output. If non-determinism surfaces, the `--no-verify` flag in the script can be used to identify the source |
| `gh pr create` in the release job fails if the `screenshots-vX.Y.Z` branch already exists (re-run on same tag) | Low | Add `git push --force` or `gh pr edit` as fallback; or use `git push origin HEAD:screenshots-${TAG} --force` before `gh pr create` |
| `release.yml` `pull-requests: write` permission not currently set; adding it may violate a repo policy | Low | The permission is only needed for the new `screenshots-release` job; it can be scoped to that job's `permissions:` block to minimise blast radius |

---

### Model tiers

Per v1.1-plan.md PH23 guidance: all roles `sonnet`.
