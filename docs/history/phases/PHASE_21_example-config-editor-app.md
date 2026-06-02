# PH21 — config-editor example app

## Plan

### Goal

Build `examples/config-editor/` — a schema-driven config file editor that
demonstrates Picolet for a technical, utilitarian tool. The app loads TOML,
YAML, and JSON files from the host filesystem, validates them against
jsonschema-style schemas stored at `~/.config/config-editor/schemas/`, and
provides a load → edit → validate → save → diff confirmation flow. Aesthetic
is **brutalist terminal**: phosphor green on near-black, monospace throughout,
deliberately ASCII — box-drawing section dividers, inline cursor-block inputs,
`+`/`-` prefixed unified diff, magenta only for validation errors.

PH21 builds on PH18's Vue 3 + Vite + TypeScript toolchain and follows the
structural pattern established by PH19 and PH20 without modifying the
framework. The structural unknowns resolved by PH19 (screenshot approach,
mock injection, AppHarness test fixtures, init_cmd wiring) are inherited
directly.

The two genuinely novel concerns for PH21 are:

1. **Library selection for TOML/YAML/JSON parsing in MicroPython** — the
   Python side must handle structured serialisation formats; not all have
   stdlib support in the frozen MicroPython environment.
2. **Vendor strategy for jsonschema validation** — no standard jsonschema
   library ships with MicroPython; a subset must be vendored or written inline.

Both are resolved in the Key Research Findings section below.

---

### Spec coverage

| Spec ID | Requirement | Where in this phase |
|---|---|---|
| FR-EX-3 | `picolet init <name> --template config-editor` scaffolds a schema-driven config editor with TOML/YAML/JSON read+write, validation, and load→edit→validate→save→diff flow | Chunks 1–5 (app scaffold + Python backend + Vue UI + integration) + Chunk 7 (init_cmd wiring) |
| FR-EX-5 | Each example ships `tests/` with Playwright integration tests | Chunk 6 (load TOML → modify → validate → save → diff confirmation flow) |
| FR-EX-6 | Each example ships `screenshots/` with auto-generated PNGs covering major UI states | Chunk 8 (generate_screenshots.py — five states) |
| NFR-EX-1 | Binary size ≤ 3 MiB on linux-x64-webview | Chunk 9 (Gate C) |
| NFR-EX-2 | Start-up ≤ 1500 ms first interactive frame | Chunk 9 (Gate E, AppHarness time_to_ready) |
| NFR-EX-3 | CSS does not pull a runtime CSS framework heavier than 50 KB gzipped | Chunk 3 (hand-crafted CSS; no component library; TOML/YAML/JSON parsers are JS-side only for display formatting, not CSS) |
| NFR-EX-4 | No external CDN at runtime; all assets in romfs | Chunk 3 (JetBrains Mono woff2 copied from pydfu, no CDN) |
| NFR-EX-5 | Deterministic screenshots; same inputs → byte-identical PNG | Chunk 8 (deterministic fixture data; animations disabled) |
| NFR-EX-6 | Screenshot gallery regenerated on every CI build; drift is CI failure | Chunk 8 + Chunk 9 (Gate H verifies PNGs present and valid) |
| NFR-EX-AESTHETIC | Must pass "show me the screenshot — is it memorable?" test | Chunk 3 (all aesthetic decisions spec-exact) |

---

### Dependencies

#### From v1 (already landed)

- `picolet.command` / `picolet.emit` / `picolet.run` at
  `packages/picolet-runtime/python/picolet/__init__.py`.
- `picolet._dispatcher.Dispatcher` wire format (newline-delimited JSON) at
  `packages/picolet-runtime/python/picolet/_dispatcher.py`.
- MicroPython stdlib available in frozen environment: `os`, `pathlib`,
  `sys`, `json`, `time`, `re`. Also `tomllib` (read-only TOML, Python 3.11+
  stdlib — available in MicroPython via the `tomllib` module shipped in the
  MicroPython standard library tree; verified present in the picolet runtime
  build).

#### From PH17 (already landed)

- `picolet.testing.AppHarness` at `packages/picolet/picolet/testing/_harness.py`.
- `picolet test --screenshot` CLI at `packages/picolet/picolet/test_cmd.py`.
- `window.picolet.__ready__ === true` contract waited on by AppHarness.

#### From PH18 (already landed)

- `[ui.frontend]` table parser + `npm run build` hook in `build_cmd.py`.
- `createWebHashHistory()` as the required Vue Router mode under `picolet://`.

#### From PH19 (already landed)

- `examples/pydfu/scripts/generate_screenshots.py` — Playwright headless
  Chromium + local HTTP server screenshot pattern. PH21 replicates this.
- `examples/pydfu/tests/conftest.py` — AppHarness pytest fixture pattern.
- Font: `examples/pydfu/ui/public/fonts/JetBrainsMono-Regular.woff2` — the
  spec names JetBrains Mono (OFL) as the ship-safe font for config-editor.
  PH21 **copies this file verbatim** from `examples/pydfu/`; it does not
  re-download or re-add to `runtime.toml`. See F9 below.
- `vite.config.ts` pattern: `base: './'`, `root: 'ui'`, `build.outDir: '../dist'`.
- `picolet.toml` structure.

#### From PH20 (already landed)

- `init_cmd._KNOWN_TEMPLATES` already includes `"notes"`; PH21 adds
  `"config-editor"` using the identical mechanism.
- `examples/notes/ui/src/router/index.ts` — `createWebHashHistory()` router
  with three routes; PH21 follows the same shape.
- Screenshot `generate_screenshots.py` structure with `_build_mock_picolet_js`,
  `_start_file_server`, pixel verification pattern.

#### What PH23 needs from PH21

- `examples/config-editor/` present and buildable. PH23's mirror script copies
  it to `packages/picolet/picolet/templates/config-editor/`.
- `examples/config-editor/screenshots/` non-empty. PH23's CI screenshot job
  validates.

---

### Key research findings

**F1 — TOML: MicroPython `tomllib` is read-only; TOML write requires a
minimal serialiser.**

Python 3.11 added `tomllib` (read-only) to the stdlib. The MicroPython
standard library includes `tomllib` in its CPython-compatibility tree
(`micropython-lib/python-stdlib/tomllib/`). The picolet runtime build includes
this module. Parsing TOML input therefore needs no vendored code:

```python
import tomllib
with open(path, "rb") as f:
    doc = tomllib.load(f)
```

Writing TOML back out requires serialisation. MicroPython does not include
`tomli_w` or any equivalent. The config-editor's Python backend implements a
minimal inline TOML serialiser sufficient for the value types that appear in
typical config files (string, int, float, bool, list of scalars, inline table).
Nested tables are written as `[section]` headers. This is approximately 60
lines of Python. It does not need to handle the full TOML spec — it only needs
to round-trip what `tomllib.load()` can parse from a typical config file.

The serialiser is in `src/config_store.py` as `_toml_dumps(doc)`. It is not a
general-purpose library; its contract is: produce valid TOML that
`tomllib.load()` can read back to produce the same Python dict.

**F2 — YAML: no MicroPython stdlib module; vendor `micro-yaml`.**

MicroPython has no built-in YAML parser. The options are:

1. Vendor `micro-yaml` — a 200-line pure-Python subset YAML parser
   (https://github.com/nickovs/micropython-yaml / micropython-lib). Handles
   simple scalars, mappings, sequences — the common config-file subset. Does
   not handle anchors, tags, or multi-document streams. This is the right fit
   for config files.
2. Implement a minimal inline parser (~100 lines) that covers `key: value`,
   `- item`, nested indentation. More predictable scope but more maintenance.

Decision: vendor `micro-yaml` (option 1). It is MIT licensed. The vendored
file goes in `src/micro_yaml.py`. Its scope (simple config YAML) matches the
use case. If a config YAML file uses anchors or other advanced features, the
backend returns an error with a clear message rather than silently misparting.

YAML write: `micro-yaml` is read-only. The same approach as TOML applies: a
minimal inline `_yaml_dumps(doc)` function (~50 lines) in `config_store.py`
that serialises Python dicts/lists to YAML text. Round-trip fidelity for
simple config files; no support for anchors or block scalars.

**F3 — JSON: MicroPython `json` stdlib, read+write.**

`json` is available in the MicroPython stdlib. `json.loads()` and
`json.dumps(doc, indent=2)` handle the full load/save cycle. No vendoring
needed.

**F4 — jsonschema validation: vendor a minimal subset.**

The spec requires "jsonschema-style validation". Full `jsonschema` (the PyPI
package) is CPython-only and has heavy dependencies. For MicroPython the
approach is:

Vendor a minimal inline validator in `src/config_validator.py` (~150 lines)
that implements the subset of JSON Schema Draft-07 relevant to config files:

- `type` (string, number, integer, boolean, null, array, object)
- `required` (list of required keys)
- `properties` (per-key sub-schemas)
- `additionalProperties` (boolean or schema)
- `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`
- `minLength`, `maxLength`
- `pattern` (basic regex via `re`)
- `enum`
- `items` (for arrays)

This covers ~90% of real-world config schemas. The validator returns a list of
error objects `{"path": "field.subfield", "message": "..."}` which the Vue
frontend renders as inline magenta `!! ` prefixed lines.

Schema files live at `~/.config/config-editor/schemas/<name>.json`. The
Python backend loads them with `json.loads()`. Schemas themselves are JSON,
not YAML, which avoids a chicken-and-egg dependency.

**F5 — Unified diff: pure Python `difflib`, not a vendored library.**

Python's `difflib` stdlib module (available in MicroPython's CPython compat
tree, confirmed in the picolet runtime build) provides `difflib.unified_diff()`.
The `save()` command uses this to produce the diff between the original
on-disk content and the new serialised content before writing:

```python
import difflib
diff = list(difflib.unified_diff(
    original_lines,
    new_lines,
    fromfile="original",
    tofile="new",
    lineterm="",
))
```

The diff is returned to the JS side as a list of strings. The Vue diff view
renders them verbatim in a `<pre>` with phosphor-green colouring for context
lines, brighter green for `+` lines, and `--fg-dim` for `-` lines. No
syntax highlighting. This is the "literal `+`/`-` prefixes, no syntax
highlighting" requirement from the spec.

The JS side does not implement diff itself — the Python backend owns diff
generation. This is the load-bearing architectural decision: the diff lives
Python-side so the JS view is a pure renderer of pre-computed text lines.

**F6 — File picker: typed path input with filesystem autocomplete.**

The spec requires a file picker on the `/` route. The choice between `<input
type="file">` (native OS dialog, as used by pydfu implicitly) and a typed path
input with autocomplete is resolved in favour of **typed path with
autocomplete**.

Rationale: the brutalist aesthetic is predicated on a keyboard-first terminal
feel. An OS file picker dialog is visually incompatible — it breaks out of the
brutalist frame into the host OS chrome. A typed path input stays in the same
aesthetic register as the rest of the UI (`key = ▌ /path █`).

Implementation:

- `<input>` bound to a `path` ref. On each `input` event, if the current
  value ends with `/`, the frontend calls `list_dir({path: value})` to get
  directory entries. The results appear as a monospace dropdown list below the
  input (no popover component — a plain `<ul>` with green hover highlight).
- `list_dir(path)` is an additional Python IPC command that calls `os.listdir`
  on the given path and returns an array of `{name, is_dir}` entries. It is
  used only for autocomplete; the load is still triggered explicitly by Enter
  or a button.
- The autocomplete list uses `Tab` to complete. Escape dismisses.
- On screens where the path is already known (e.g. from a test), autocomplete
  is never triggered — the test calls `load(path)` directly.

This approach satisfies NFR-EX-4 (no CDN), NFR-EX-AESTHETIC (brutalist), and
the spec's "file picker (host FS browser)" intent without an OS dialog.

**F7 — Schema picker: typed name input, not a file browser.**

The schema is identified by name (e.g. `myapp`), and the backend looks for
`~/.config/config-editor/schemas/<name>.json`. The `/` route shows a second
typed input for the schema name below the file path input. The backend also
provides a `list_schemas()` command that returns the names available in the
schemas directory. If the schemas directory is empty or absent, the editor
works in schema-free mode (no validation).

**F8 — 80-column max width and retro layout.**

The spec mandates "80-column max width centred on wide screens (deliberately
retro)". Implementation:

```css
.terminal-frame {
  max-width: 80ch;
  margin: 0 auto;
  padding: 0 1ch;
}
```

`80ch` is computed relative to the current font's `ch` unit (width of `0`
glyph in the monospace font). At JetBrains Mono 14px, `80ch` is approximately
616px. On a 1200px viewport, the terminal frame is centred with ~290px of
empty phosphor-green-on-black on each side. The emptiness is intentional —
it is part of the brutalist point.

**F9 — JetBrains Mono: copy from pydfu, do not re-add to runtime.toml.**

`examples/pydfu/ui/public/fonts/JetBrainsMono-Regular.woff2` is the font file
that PH19 shipped. PH21 copies it to
`examples/config-editor/ui/public/fonts/JetBrainsMono-Regular.woff2` during
development. The `@font-face` declaration in `fonts.css` is identical to
pydfu's. No entry is added to `packages/picolet-runtime/sbom/runtime.toml` —
the font is already declared there from PH19 (it is a romfs asset, not a
compiled dependency). If the SBOM file does not yet have it, PH21 adds it
following the same format as PH19 did.

The spec says "JetBrains Mono — already in repo from PH19!" — this is the
confirmed pattern.

**F10 — Cursor block blink: pure CSS `@keyframes`, input `::after` pseudo.**

The spec mandates a blinking cursor block on focused inputs. The aesthetic
is `key = ▌ value █` — the `▌` is a left half-block before the value (static
label decoration) and the `█` represents the cursor block after the current
value. Implementation:

The `▌` is rendered as a CSS `::before` pseudo on the `.field-label` span.
The blinking `█` is rendered as a CSS `::after` pseudo on the `.field-input`
wrapper, visible only when the `<input>` inside is `:focus-within`:

```css
@keyframes cursor-blink {
  0%, 49% { opacity: 1; }
  50%, 100% { opacity: 0; }
}

.field-input::after {
  content: '█';
  color: var(--cursor);
  display: none;
}

.field-input:focus-within::after {
  display: inline;
  animation: cursor-blink 1s step-start infinite;
}
```

`1s` is exactly 1 Hz, matching the spec. `step-start` produces an abrupt
binary toggle (no fade) appropriate for a terminal cursor. This is analogous
to PH19's `animation: pulse 0.5s ease-in-out infinite` for LED dots — same
primitive, different timing function.

Magenta (`--error: #ff5cd1`) is used exclusively for validation error display.
It does not appear in any other element class, hover state, or decoration.

**F11 — Diff view: pure JS renderer, ~30 lines.**

The Python backend returns the diff as a list of strings (one per line of
unified diff output). The Vue `DiffView.vue` component renders these in a
`<pre>` element with per-line colouring:

```typescript
function lineClass(line: string): string {
  if (line.startsWith('+') && !line.startsWith('+++')) return 'diff-add'
  if (line.startsWith('-') && !line.startsWith('---')) return 'diff-del'
  if (line.startsWith('@@')) return 'diff-hunk'
  return 'diff-ctx'
}
```

```css
.diff-add  { color: var(--fg); }        /* #a3ff7c — bright phosphor */
.diff-del  { color: var(--fg-dim); }    /* #4a8a4a — dim phosphor */
.diff-hunk { color: var(--fg-dim); font-style: italic; }
.diff-ctx  { color: var(--fg-dim); }
```

No JS diff library. Total diff view component: ~50 lines of template + script
+ style. This satisfies the "pure JS, ~50 lines" constraint from the spec.

---

### Aesthetic spec

All values are mandatory. The developer must not deviate without recording a
decision commit.

#### CSS custom properties

```css
:root {
  --bg:         #0d1b0d;   /* body background — phosphor black-green */
  --fg:         #a3ff7c;   /* primary text — phosphor green */
  --fg-dim:     #4a8a4a;   /* secondary text — dim phosphor */
  --error:      #ff5cd1;   /* magenta — validation errors ONLY */
  --cursor:     #a3ff7c;   /* cursor block colour */
  --rule:       #1a3a1a;   /* section dividers */
  --font-mono:  'JetBrains Mono', 'Courier New', monospace;
}
```

`--error` (`#ff5cd1`) appears only on:
- `.field-error` — the `!! ` prefix + error message text
- `.field-error`'s `text-decoration: underline` on the associated field label
- Nowhere else in the codebase. No hover states, no borders, no backgrounds.

#### Typography

Everything is monospace. There are no non-monospace fonts in this app.

```css
body {
  background: var(--bg);
  color: var(--fg);
  font-family: var(--font-mono);
  font-size: 14px;
  line-height: 1.5;
  margin: 0;
}
```

Section dividers use the `═` box-drawing character repeated to fill the width:

```
══════════════════════════════════════════════════════════════════════════════
[section name]
══════════════════════════════════════════════════════════════════════════════
```

Rendered as a `<div class="section-rule">` containing a `<span>` of `═`
characters followed by the section name:

```css
.section-rule {
  color: var(--fg-dim);
  white-space: pre;
  overflow: hidden;
  font-size: 14px;
  margin: 1em 0 0.5em;
}

.section-title {
  color: var(--fg);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.05em;
  margin-bottom: 0.25em;
}
```

The `═` count is computed to fill the `80ch` column width minus the section
name length. Vue `computed` ref:

```typescript
const rulerLine = computed(() =>
  '═'.repeat(Math.max(0, 80 - sectionName.length - 3)) + ' ' + sectionName + ' ═'
)
```

#### Field layout

Each editable field is rendered as:

```
key-name = ▌ current-value █
```

Where `▌` is a static label separator and `█` is the animated cursor block
(visible when focused). The full label is a flexbox row:

```
[.field-label "key-name = ▌"][.field-input <input>][.field-cursor "█"]
```

But implemented as two elements with CSS `::before` / `::after` pseudo-elements
to avoid extra DOM nodes. See F10 above for the `::after` blink pattern.

The `<input>` itself:
```css
.field-input input {
  background: transparent;
  border: none;
  color: var(--fg);
  font-family: var(--font-mono);
  font-size: 14px;
  outline: none;
  caret-color: transparent;   /* hide native caret; cursor-block pseudo handles it */
  width: 100%;
}
```

`caret-color: transparent` hides the browser's native text cursor so only the
`█` pseudo appears.

#### 80-column frame

```css
.terminal-frame {
  max-width: 80ch;
  margin: 0 auto;
  padding: 2ch 1ch;
}
```

On wide viewports, the sides remain solid `--bg` (`#0d1b0d`) — no side panels,
no decorations. The emptiness is the design.

#### Motion

Only two animations are permitted:
- `.field-input::after` blink: `cursor-blink 1s step-start infinite`, active
  only while the input is focused via `:focus-within`.
- The `.no-animation` override class (set in screenshot mode) kills both via
  `animation: none !important`.

No page transitions. No hover transitions. Route changes are instant.

#### Validation error display

```
!! field-name must be a string of at least 3 characters
```

```css
.field-error {
  color: var(--error);   /* #ff5cd1 */
  display: block;
  margin-top: 2px;
}

.field-label.has-error {
  text-decoration: underline;
  text-decoration-color: var(--error);
}
```

The `!! ` prefix is hardcoded in the Vue template — it is not a CSS
pseudo-element — so it appears in any text copy/paste of the error output
(deliberate: the errors should be copyable).

---

### Implementation breakdown

Seven chunks ordered by dependency. Each chunk is independently testable.

---

#### Chunk 1 — `examples/config-editor/` scaffold (structure, picolet.toml, Vite config)

**Goal**: Lay down directory structure and configuration so `picolet build` runs
cleanly before any real Python or Vue logic is present.

**Pattern reference:**
- `examples/pydfu/picolet.toml` — identical structure.
- `examples/pydfu/vite.config.ts` — copy with `name: 'config-editor'`.
- `examples/pydfu/package.json` — same Vue + Vite + TypeScript deps; no
  additional npm deps needed (no `marked` equivalent; TOML/YAML/JSON parsing
  is Python-side only).

**Files to create:**

- `examples/config-editor/picolet.toml`:
  ```toml
  [app]
  name = "config-editor"
  version = "0.1.0"
  entry = "src/main.py"

  [ui]
  renderer = "webview"
  root = "ui"
  index = "index.html"

  [ui.frontend]
  framework = "vue"
  build_cmd = "npm run build"
  dist_dir = "dist"
  dev_url = "http://localhost:5173/"

  [window]
  title = "Config Editor"
  size = [1200, 800]
  resizable = true
  ```

- `examples/config-editor/package.json` — same as pydfu's but `name =
  "config-editor"`. No additional runtime npm dependencies: TOML/YAML/JSON
  parsing happens Python-side. The Vue frontend receives plain JSON over IPC
  and renders the resulting dict. The diff view renders pre-computed strings
  from Python's `difflib`.

- `examples/config-editor/vite.config.ts` — identical to pydfu's.

- `examples/config-editor/tsconfig.json` / `tsconfig.node.json` — copy from
  pydfu.

- `examples/config-editor/ui/index.html` — Vite entry, `<title>Config
  Editor</title>`.

- `examples/config-editor/ui/src/main.ts` — `createApp(App).use(router).mount('#app')`.
  Imports `./assets/fonts.css` and `./assets/main.css`.

- `examples/config-editor/ui/src/env.d.ts` — triple-slash reference to
  `picolet.d.ts`.

- `examples/config-editor/ui/src/picolet.d.ts` — copy from pydfu.

- `examples/config-editor/ui/src/App.vue` — stub: `<RouterView />`.

- `examples/config-editor/ui/public/fonts/JetBrainsMono-Regular.woff2` —
  copy from `examples/pydfu/ui/public/fonts/JetBrainsMono-Regular.woff2`.
  Do not re-download; byte-copy only.

- `examples/config-editor/src/main.py` — stub that boots `ui.Application()`.

**Exercise:**
```bash
cd /home/anl/picolet/examples/config-editor
cp /home/anl/picolet/examples/pydfu/ui/public/fonts/JetBrainsMono-Regular.woff2 \
   ui/public/fonts/
npm install --prefer-offline
picolet build --no-sbom
# binary exists at target/linux-x64/config-editor
```

---

#### Chunk 2 — Python backend: `config_store.py` + `main.py` IPC commands

**Goal**: Implement the five IPC commands plus the TOML/YAML/JSON
parse/serialise pipeline, the jsonschema-subset validator, and the unified
diff generation. Test isolation via `PICOLET_CONFIG_DIR` env var override for
the schemas directory.

**Files to create:**

- `examples/config-editor/src/micro_yaml.py` — vendored micro-yaml. Source:
  `micropython-lib/python-stdlib/yaml` or
  https://github.com/nickovs/micropython-yaml. MIT licensed. Drop in verbatim.
  If the file exceeds 300 lines it may be trimmed to the subset of features
  needed (load simple mappings and sequences; raise `YAMLError` for anything
  beyond scope). Add a module docstring explaining the provenance and licence.

- `examples/config-editor/src/config_validator.py` — inline jsonschema subset
  validator:

  ```python
  """config_validator.py — minimal JSON Schema Draft-07 subset validator.

  Implements: type, required, properties, additionalProperties,
  minimum, maximum, exclusiveMinimum, exclusiveMaximum, minLength,
  maxLength, pattern, enum, items.

  Returns a list of error dicts: [{"path": "a.b", "message": "..."}]
  An empty list means no errors.
  """
  import re as _re

  def validate(document: dict, schema: dict) -> list:
      errors = []
      _validate_node(document, schema, "", errors)
      return errors

  def _validate_node(value, schema, path, errors):
      if not isinstance(schema, dict):
          return
      t = schema.get("type")
      if t is not None:
          if not _check_type(value, t):
              errors.append({"path": path, "message": f"expected type {t!r}, got {type(value).__name__}"})
              return  # further checks not meaningful if type wrong
      if "enum" in schema and value not in schema["enum"]:
          errors.append({"path": path, "message": f"value not in enum {schema['enum']}"})
      if isinstance(value, dict):
          _validate_object(value, schema, path, errors)
      elif isinstance(value, list):
          _validate_array(value, schema, path, errors)
      elif isinstance(value, str):
          _validate_string(value, schema, path, errors)
      elif isinstance(value, (int, float)):
          _validate_number(value, schema, path, errors)

  # ... (full implementation ~120 lines total)
  ```

  The developer expands this to the full implementation. Key contract: inputs
  are Python dicts/lists (already parsed from TOML/YAML/JSON); output is a
  list of error dicts with `path` (dotted key path) and `message` fields.

- `examples/config-editor/src/config_store.py`:

  ```python
  """config_store.py — load, validate, save TOML/YAML/JSON config files.

  Supported formats (detected by file extension):
    .toml  — parsed via tomllib (stdlib); serialised via _toml_dumps()
    .yaml, .yml — parsed via micro_yaml; serialised via _yaml_dumps()
    .json  — parsed via json (stdlib); serialised via json.dumps(indent=2)

  Test isolation: PICOLET_CONFIG_DIR overrides the schemas base directory.
  Default schemas dir: ~/.config/config-editor/schemas/
  """
  import difflib
  import json
  import os
  import sys
  from pathlib import Path

  import tomllib
  import micro_yaml
  import config_validator


  def _schemas_dir() -> Path:
      override = os.environ.get("PICOLET_CONFIG_DIR")
      if override:
          p = Path(override) / "schemas"
      elif sys.platform == "win32":
          base = os.environ.get("APPDATA")
          if not base:
              raise RuntimeError("APPDATA not set on Windows")
          p = Path(base) / "config-editor" / "schemas"
      else:
          xdg = os.environ.get("XDG_CONFIG_HOME")
          base = Path(xdg) if xdg else Path.home() / ".config"
          p = base / "config-editor" / "schemas"
      p.mkdir(parents=True, exist_ok=True)
      return p


  def _detect_format(path: str) -> str:
      ext = Path(path).suffix.lower()
      if ext == ".toml":
          return "toml"
      if ext in (".yaml", ".yml"):
          return "yaml"
      if ext == ".json":
          return "json"
      raise ValueError(f"unsupported file extension: {ext!r}")


  def _parse(path: str, fmt: str) -> dict:
      if fmt == "toml":
          with open(path, "rb") as f:
              return tomllib.load(f)
      if fmt == "yaml":
          with open(path, "r", encoding="utf-8") as f:
              return micro_yaml.load(f.read())
      if fmt == "json":
          with open(path, "r", encoding="utf-8") as f:
              return json.loads(f.read())
      raise ValueError(f"unknown format: {fmt!r}")


  def _serialise(doc: dict, fmt: str) -> str:
      if fmt == "toml":
          return _toml_dumps(doc)
      if fmt == "yaml":
          return _yaml_dumps(doc)
      if fmt == "json":
          return json.dumps(doc, indent=2)
      raise ValueError(f"unknown format: {fmt!r}")


  def _toml_dumps(obj, _indent=0) -> str:
      """Minimal TOML serialiser. Handles: str, int, float, bool, list of scalars,
      dict (as [section] or inline). Does not handle datetime, multi-line strings."""
      lines = []
      tables = {}
      for k, v in obj.items():
          if isinstance(v, dict):
              tables[k] = v
          else:
              lines.append(f"{k} = {_toml_value(v)}")
      if tables:
          for k, v in tables.items():
              lines.append(f"\n[{k}]")
              for sk, sv in v.items():
                  lines.append(f"{sk} = {_toml_value(sv)}")
      return "\n".join(lines) + "\n"


  def _toml_value(v) -> str:
      if isinstance(v, bool):
          return "true" if v else "false"
      if isinstance(v, int):
          return str(v)
      if isinstance(v, float):
          return repr(v)
      if isinstance(v, str):
          escaped = v.replace("\\", "\\\\").replace('"', '\\"')
          return f'"{escaped}"'
      if isinstance(v, list):
          items = ", ".join(_toml_value(i) for i in v)
          return f"[{items}]"
      raise TypeError(f"_toml_value: unsupported type {type(v)}")


  def _yaml_dumps(obj, indent=0) -> str:
      """Minimal YAML serialiser. Handles: str, int, float, bool, None,
      list, dict. Produces block-style YAML."""
      pad = "  " * indent
      if isinstance(obj, dict):
          if not obj:
              return "{}"
          lines = []
          for k, v in obj.items():
              if isinstance(v, (dict, list)):
                  lines.append(f"{pad}{k}:")
                  lines.append(_yaml_dumps(v, indent + 1))
              else:
                  lines.append(f"{pad}{k}: {_yaml_scalar(v)}")
          return "\n".join(lines)
      if isinstance(obj, list):
          if not obj:
              return f"{pad}[]"
          lines = []
          for item in obj:
              if isinstance(item, (dict, list)):
                  lines.append(f"{pad}-")
                  lines.append(_yaml_dumps(item, indent + 1))
              else:
                  lines.append(f"{pad}- {_yaml_scalar(item)}")
          return "\n".join(lines)
      return f"{pad}{_yaml_scalar(obj)}"


  def _yaml_scalar(v) -> str:
      if v is None:
          return "null"
      if isinstance(v, bool):
          return "true" if v else "false"
      if isinstance(v, (int, float)):
          return str(v)
      if isinstance(v, str):
          # Quote strings that could be misread as scalars.
          if any(c in v for c in (':', '#', '[', ']', '{', '}', ',', '&', '*',
                                   '?', '|', '-', '<', '>', '=', '!', '%',
                                   '@', '`', '"', "'")):
              return f'"{v.replace(chr(34), chr(92) + chr(34))}"'
          if v.lower() in ("true", "false", "null", "yes", "no", "on", "off"):
              return f'"{v}"'
          return v
      raise TypeError(f"_yaml_scalar: unsupported type {type(v)}")


  def list_dir(path: str) -> list:
      """List directory entries for autocomplete."""
      p = Path(path)
      if not p.is_dir():
          return []
      entries = []
      try:
          for name in sorted(os.listdir(str(p))):
              full = p / name
              entries.append({"name": name, "is_dir": full.is_dir()})
      except PermissionError:
          pass
      return entries


  def list_schemas() -> list:
      """Return schema names available in the schemas directory."""
      d = _schemas_dir()
      return [f.stem for f in sorted(d.glob("*.json"))]


  def load(path: str) -> dict:
      """Parse a config file. Returns {format, document, schema_hint}."""
      fmt = _detect_format(path)
      doc = _parse(path, fmt)
      # schema_hint: if a schema with the same stem exists, return its name.
      stem = Path(path).stem
      schema_hint = stem if (_schemas_dir() / f"{stem}.json").exists() else None
      return {"format": fmt, "document": doc, "schema_hint": schema_hint}


  def validate(fmt: str, document: dict, schema_name: str) -> list:
      """Validate document against named schema. Returns list of error dicts."""
      schema_path = _schemas_dir() / f"{schema_name}.json"
      if not schema_path.exists():
          return [{"path": "", "message": f"schema not found: {schema_name!r}"}]
      schema = json.loads(schema_path.read_text(encoding="utf-8"))
      return config_validator.validate(document, schema)


  def save(path: str, fmt: str, document: dict) -> dict:
      """Serialise document and write to path. Returns unified diff lines."""
      p = Path(path)
      original_text = p.read_text(encoding="utf-8") if p.exists() else ""
      new_text = _serialise(document, fmt)
      original_lines = original_text.splitlines(keepends=True)
      new_lines = new_text.splitlines(keepends=True)
      diff = list(difflib.unified_diff(
          original_lines,
          new_lines,
          fromfile="original",
          tofile="new",
          lineterm="",
      ))
      p.write_text(new_text, encoding="utf-8")
      return {"diff": diff, "ok": True}
  ```

- `examples/config-editor/src/main.py`:

  ```python
  """config-editor — schema-driven config file editor (picolet example).

  IPC commands:
    list_dir(path)               -> [{name, is_dir}]
    list_schemas()               -> [schema_name, ...]
    load(path)                   -> {format, document, schema_hint}
    validate(format, document, schema_name) -> [{path, message}]
    save(path, format, document) -> {diff: [...], ok: True}
  """
  import picolet
  import picolet_ui as ui
  import config_store as store


  @picolet.command
  async def list_dir(args):
      path = args.get("path", "") if isinstance(args, dict) else str(args)
      try:
          return store.list_dir(path)
      except Exception as e:
          return {"ok": False, "error": str(e)}


  @picolet.command
  async def list_schemas(args):
      return store.list_schemas()


  @picolet.command
  async def load(args):
      path = args.get("path") if isinstance(args, dict) else str(args)
      try:
          return store.load(path)
      except Exception as e:
          return {"ok": False, "error": str(e)}


  @picolet.command
  async def validate(args):
      if not isinstance(args, dict):
          return {"ok": False, "error": "args must be a dict"}
      fmt = args.get("format", "")
      document = args.get("document", {})
      schema_name = args.get("schema_name", "")
      try:
          errors = store.validate(fmt, document, schema_name)
          return {"errors": errors, "ok": True}
      except Exception as e:
          return {"ok": False, "error": str(e)}


  @picolet.command
  async def save(args):
      if not isinstance(args, dict):
          return {"ok": False, "error": "args must be a dict"}
      path = args.get("path")
      fmt = args.get("format")
      document = args.get("document", {})
      try:
          return store.save(path, fmt, document)
      except Exception as e:
          return {"ok": False, "error": str(e)}


  def main():
      app = ui.Application()
      app.run()


  main()
  ```

**Python-only smoke test:**
```bash
cd /home/anl/picolet/examples/config-editor
PICOLET_CONFIG_DIR=/tmp/ce-test python3 -c "
import sys; sys.path.insert(0, 'src')
import config_store as s
# Write a test TOML file
with open('/tmp/test.toml', 'w') as f:
    f.write('[server]\nhost = \"localhost\"\nport = 8080\n')
result = s.load('/tmp/test.toml')
print('load:', result)
# Modify and save
doc = result['document']
doc['server']['port'] = 9090
save_result = s.save('/tmp/test.toml', 'toml', doc)
print('diff lines:', save_result['diff'])
"
```

---

#### Chunk 3 — Vue frontend: aesthetic, fonts, components, three routes

**Goal**: Build the complete Vue 3 frontend with the brutalist terminal
aesthetic — font loading, CSS custom properties, three routes (`/`, `/edit`,
`/diff`), cursor-block blink, field form rendering, diff view.

**Pattern reference:**
- `examples/pydfu/ui/src/assets/fonts.css` — `@font-face` + `font-display:
  block` pattern.
- `examples/pydfu/ui/src/assets/main.css` — CSS custom properties block,
  global resets, no-animation override.
- `examples/pydfu/ui/src/router/index.ts` — `createWebHashHistory()` router.

**Files to create / modify:**

- `examples/config-editor/ui/src/assets/fonts.css`:
  ```css
  @font-face {
    font-family: 'JetBrains Mono';
    src: url('/fonts/JetBrainsMono-Regular.woff2') format('woff2');
    font-weight: 400 700;
    font-display: block;
  }
  ```
  This is identical to pydfu's `fonts.css`. Only one `@font-face` block —
  config-editor uses a single weight (no bold variant needed; weight
  differentiation is achieved through `--fg` vs `--fg-dim` colour alone).

- `examples/config-editor/ui/src/assets/main.css` — complete global
  stylesheet:
  - CSS custom properties block (all seven custom properties from the Aesthetic
    spec).
  - `body` rule: `background: var(--bg)`, `color: var(--fg)`, 14px / 1.5,
    `font-family: var(--font-mono)`.
  - `* { box-sizing: border-box; }`.
  - `.terminal-frame`: `max-width: 80ch; margin: 0 auto; padding: 2ch 1ch;`.
  - `.section-rule`: box-drawing rule line styles.
  - `.section-title`: phosphor green, bold, 14px, 0.05em letter spacing.
  - `.field-row`: display flex, align-items baseline, gap 0.
  - `.field-label`: `color: var(--fg-dim)` base; `color: var(--fg)` when not
    `.has-error`; `.has-error` adds `text-decoration: underline
    text-decoration-color: var(--error)`.
  - `.field-input`: `flex: 1`. Inner `input` rules as specified in Aesthetic
    spec (transparent bg, no border, `caret-color: transparent`).
  - `.field-input::after` + `@keyframes cursor-blink` as specified in F10.
  - `.field-error`: `color: var(--error)`, block display.
  - `.diff-add`, `.diff-del`, `.diff-hunk`, `.diff-ctx` colouring as
    specified in F11.
  - `.no-animation * { animation: none !important; transition: none !important; }`
    (screenshot mode override, same as pydfu and notes).
  - `a { color: var(--fg); }` — links use the same phosphor green.

- `examples/config-editor/ui/src/router/index.ts`:
  ```typescript
  import { createRouter, createWebHashHistory } from 'vue-router'
  import PickerView from '../views/PickerView.vue'
  import EditView from '../views/EditView.vue'
  import DiffView from '../views/DiffView.vue'

  export default createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/',     component: PickerView },
      { path: '/edit', component: EditView },
      { path: '/diff', component: DiffView },
    ],
  })
  ```
  State is passed between routes via a Vuex-free shared reactive store (see
  below). Route params are not used because the document data is too large for
  URL encoding.

- `examples/config-editor/ui/src/store.ts` — thin reactive shared state
  (replaces Vuex, which is too heavy; follows the Vue 3 Composition API
  pattern of a module-level `reactive` object shared across views):

  ```typescript
  import { reactive } from 'vue'

  export interface ConfigState {
    filePath: string
    format: string
    document: Record<string, unknown>
    schemaName: string
    errors: Array<{ path: string; message: string }>
    diff: string[]
    pendingDocument: Record<string, unknown> | null
  }

  export const state = reactive<ConfigState>({
    filePath: '',
    format: '',
    document: {},
    schemaName: '',
    errors: [],
    diff: [],
    pendingDocument: null,
  })
  ```

  All three views import `state` from `./store`. This avoids prop-drilling
  across route transitions.

- `examples/config-editor/ui/src/App.vue`:
  ```vue
  <script setup lang="ts">
  import { onMounted } from 'vue'
  onMounted(() => {
    // Screenshot mode: disable animations if flag set.
    if ((window as Record<string, unknown>).__PICOLET_SCREENSHOT_MODE__) {
      document.documentElement.classList.add('no-animation')
    }
  })
  </script>
  <template>
    <RouterView />
  </template>
  ```

- `examples/config-editor/ui/src/views/PickerView.vue` — route `/`:
  - `filePath` ref bound to a text input. Label: `file = ▌`. On `input`
    event: if value ends with `/`, calls `list_dir({path: value})` and
    populates `suggestions` ref (debounced 150 ms). Tab key selects first
    suggestion.
  - `schemaName` ref bound to a second text input. Label: `schema = ▌`. On
    focus: calls `list_schemas()` and shows schema list as suggestions.
  - `[LOAD]` button (styled as a monospace text link, not a box button):
    calls `load({path: filePath.value})`. On success: sets `state.filePath`,
    `state.format`, `state.document`, `state.schemaName` (from
    `schema_hint` if present, else from `schemaName` input). Navigates to
    `/edit`.
  - Error state: if `load` returns `{ok: false}`, shows the error message in
    `--error` magenta with `!! ` prefix.
  - Suggestions dropdown: `<ul class="suggestions">` absolutely positioned
    below the input. Items are `<li>` elements showing `name + (/ if is_dir)`.
    Clicking appends to the path input. Escape hides.
  - Section layout:
    ```
    ══════════════════════ CONFIG EDITOR ══════════════════════

    file   = ▌ /etc/myapp.toml █
    schema = ▌ myapp █

    [LOAD]
    ```

- `examples/config-editor/ui/src/views/EditView.vue` — route `/edit`:
  - On mount: reads `state.document` and `state.format`. If empty, navigates
    back to `/`.
  - Renders each top-level key in the document as a `.field-row`. Nested
    dicts become sub-sections with their own `═════` rule.
  - Field type detection from the loaded value: `string` → text input;
    `number` (int/float) → number input; `boolean` → checkbox styled as
    `[x]` / `[ ]` (monospace characters, not a native checkbox). Array of
    scalars → comma-separated text input. Nested object → sub-section (not
    an inline field; rendered as a new section block).
  - Editing updates `state.document` directly (two-way binding via `v-model`
    on the input refs; each field has a setter that updates the appropriate
    nested key in `state.document`).
  - `[VALIDATE]` button: calls `validate({format: state.format,
    document: state.document, schema_name: state.schemaName})`. On success:
    sets `state.errors`. Fields with errors have `.has-error` class on their
    label and an inline `.field-error` div below them.
  - `[SAVE]` button: calls `save({path: state.filePath, format: state.format,
    document: state.document})`. On success: sets `state.diff` from the
    returned diff array. Navigates to `/diff`.
  - `[← BACK]` link: navigates to `/`.
  - Section layout:
    ```
    ══════ /etc/myapp.toml (toml) ══════════════════════════

    [server]
    ══════ server ══════════════════════════════════════════
    host = ▌ localhost █
    port = ▌ 8080 █

    [database]
    ══════ database ══════════════════════════════════════════
    url  = ▌ postgresql://... █
    !! url must match pattern ^postgresql://
    ```

- `examples/config-editor/ui/src/views/DiffView.vue` — route `/diff`:
  - On mount: reads `state.diff`. If empty, navigates back to `/edit`.
  - Renders diff lines in a `<pre class="diff-output">` element. Each line
    is a `<span>` with the appropriate diff class (add/del/hunk/ctx) from
    the `lineClass()` function in F11.
  - `[← BACK TO EDITOR]` link: navigates to `/edit`.
  - `[LOAD ANOTHER FILE]` link: resets `state` and navigates to `/`.
  - Section layout:
    ```
    ══════ DIFF: /etc/myapp.toml ════════════════════════════

    --- original
    +++ new
    @@ -3,7 +3,7 @@
     host = "localhost"
    -port = 8080
    +port = 9090
     debug = false
    ```

- `examples/config-editor/ui/src/main.ts`:
  ```typescript
  import { createApp } from 'vue'
  import App from './App.vue'
  import router from './router'
  import './assets/fonts.css'
  import './assets/main.css'

  createApp(App).use(router).mount('#app')
  ```

**CSS budget check:**
- `main.css` hand-crafted: estimated ~4–6 KB minified.
- No JS markdown library (unlike notes).
- No CSS framework. Total CSS well under 50 KB gzipped. NFR-EX-3 met.

**Typecheck exercise:**
```bash
cd /home/anl/picolet/examples/config-editor
npm run typecheck   # vue-tsc --noEmit must exit 0
npm run build
# dist/ must contain index.html, assets/*.js, assets/*.css, fonts/JetBrainsMono*.woff2
```

---

#### Chunk 4 — Integration: build, run, manual smoke

**Goal**: Produce a working binary. Manual visual check confirms the brutalist
aesthetic is correct and the load → edit → validate → save → diff flow works
end-to-end.

**Steps:**

1. Create a test TOML file:
   ```bash
   mkdir -p /tmp/ce-smoke/schemas
   cat > /tmp/ce-smoke/test.toml <<'EOF'
   [server]
   host = "localhost"
   port = 8080
   debug = false
   EOF
   ```

2. Create a matching schema for `test.toml` (optional but verifies validation):
   ```bash
   cat > /tmp/ce-smoke/schemas/test.json <<'EOF'
   {
     "type": "object",
     "properties": {
       "server": {
         "type": "object",
         "properties": {
           "host": {"type": "string"},
           "port": {"type": "integer", "minimum": 1, "maximum": 65535},
           "debug": {"type": "boolean"}
         },
         "required": ["host", "port"]
       }
     }
   }
   EOF
   ```

3. Run:
   ```bash
   cd /home/anl/picolet/examples/config-editor
   picolet build --no-sbom
   PICOLET_CONFIG_DIR=/tmp/ce-smoke ./target/linux-x64/config-editor
   ```

4. In the UI:
   - Type `/tmp/ce-smoke/test.toml` in the file input. Press `[LOAD]`.
   - Verify the edit view shows the server section with fields.
   - Change `port` to `9090`. Press `[VALIDATE]` — no errors (9090 is in
     range). Press `[SAVE]`.
   - Verify the diff view shows `-port = 8080` and `+port = 9090`.
   - Verify `/tmp/ce-smoke/test.toml` now contains `port = 9090`.

**Manual aesthetic checklist** (before proceeding to Chunk 5):
- [ ] Background is solid `#0d1b0d` — no gradients, no textures.
- [ ] All text is JetBrains Mono at 14px.
- [ ] Terminal frame is max 80ch, centred on the 1200px window.
- [ ] Section dividers use `═` box-drawing characters.
- [ ] Focused field shows blinking `█` cursor block at 1 Hz.
- [ ] Validation errors are magenta `#ff5cd1` with `!! ` prefix.
- [ ] No magenta anywhere other than validation errors.
- [ ] Diff view shows `+`/`-` prefixes in phosphor green / dim green.
- [ ] No syntax highlighting in diff view.

---

#### Chunk 5 — Playwright integration tests

**Goal**: `examples/config-editor/tests/` with a Playwright test that
exercises the full load → modify → validate → save → diff confirmation flow
with host FS assertions.

**Pattern reference:**
- `examples/notes/tests/conftest.py` — AppHarness + env-var isolation pattern.
- `examples/notes/tests/test_notes_flow.py` — wait_for_selector + assert flow.

**Files to create:**

- `examples/config-editor/tests/conftest.py`:
  ```python
  """conftest.py — pytest fixtures for config-editor integration tests.

  PICOLET_CONFIG_DIR is set to a temp directory containing a pre-written TOML
  fixture and a matching schema. The real config_store.py runs against this
  directory so host FS state can be asserted directly.
  """
  import json
  import pytest
  from pathlib import Path
  from picolet.testing import AppHarness

  BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "config-editor"


  @pytest.fixture
  async def config_dir(tmp_path):
      d = tmp_path / "config-editor"
      schemas_d = d / "schemas"
      schemas_d.mkdir(parents=True)
      # Write a minimal TOML fixture.
      toml_file = tmp_path / "test.toml"
      toml_file.write_text('[server]\nhost = "localhost"\nport = 8080\n',
                            encoding="utf-8")
      # Write a matching schema.
      schema = {
          "type": "object",
          "properties": {
              "server": {
                  "type": "object",
                  "properties": {
                      "host": {"type": "string"},
                      "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                  },
                  "required": ["host", "port"],
              }
          },
      }
      (schemas_d / "test.json").write_text(json.dumps(schema), encoding="utf-8")
      return d, toml_file


  @pytest.fixture
  async def harness(config_dir):
      cfg_base, _ = config_dir
      h = AppHarness(
          str(BINARY),
          env={"PICOLET_CONFIG_DIR": str(cfg_base)},
      )
      await h.start()
      yield h
      await h.stop()
  ```

- `examples/config-editor/tests/test_config_flow.py`:
  ```python
  """Integration test: load TOML → modify → validate → save → diff confirmation."""
  import asyncio
  import pytest
  from pathlib import Path

  pytestmark = pytest.mark.asyncio


  async def test_load_and_edit_toml(harness, config_dir):
      cfg_base, toml_file = config_dir
      page = harness.page

      # Picker view — fill in path and load.
      await page.wait_for_selector(".picker-view", timeout=5000)
      await page.fill("input.file-path-input", str(toml_file))
      await page.click("button.btn-load")

      # Edit view — server.port field should be visible.
      await page.wait_for_selector(".edit-view", timeout=5000)
      port_input = page.locator("input[data-key='server.port']")
      await port_input.wait_for(state="visible", timeout=3000)
      old_val = await port_input.input_value()
      assert old_val == "8080"

      # Modify port.
      await port_input.fill("9090")


  async def test_validate_passes(harness, config_dir):
      cfg_base, toml_file = config_dir
      page = harness.page

      await page.wait_for_selector(".picker-view", timeout=5000)
      await page.fill("input.file-path-input", str(toml_file))
      await page.fill("input.schema-name-input", "test")
      await page.click("button.btn-load")
      await page.wait_for_selector(".edit-view", timeout=5000)
      await page.fill("input[data-key='server.port']", "9090")

      # Validate — no errors expected.
      await page.click("button.btn-validate")
      await asyncio.sleep(0.3)
      errors = page.locator(".field-error")
      count = await errors.count()
      assert count == 0, f"expected no validation errors, got {count}"


  async def test_save_and_diff(harness, config_dir):
      cfg_base, toml_file = config_dir
      page = harness.page

      await page.wait_for_selector(".picker-view", timeout=5000)
      await page.fill("input.file-path-input", str(toml_file))
      await page.click("button.btn-load")
      await page.wait_for_selector(".edit-view", timeout=5000)
      await page.fill("input[data-key='server.port']", "9090")
      await page.click("button.btn-save")

      # Diff view — should show the port change.
      await page.wait_for_selector(".diff-view", timeout=5000)
      diff_text = await page.locator("pre.diff-output").inner_text()
      assert "-port" in diff_text or "port" in diff_text
      assert "9090" in diff_text

      # FS: file now contains 9090.
      await asyncio.sleep(0.3)
      content = toml_file.read_text(encoding="utf-8")
      assert "9090" in content
      assert "8080" not in content


  async def test_validate_fails_with_magenta_error(harness, config_dir):
      cfg_base, toml_file = config_dir
      page = harness.page

      await page.wait_for_selector(".picker-view", timeout=5000)
      await page.fill("input.file-path-input", str(toml_file))
      await page.fill("input.schema-name-input", "test")
      await page.click("button.btn-load")
      await page.wait_for_selector(".edit-view", timeout=5000)

      # Set port to an invalid value (> 65535).
      await page.fill("input[data-key='server.port']", "99999")
      await page.click("button.btn-validate")
      await asyncio.sleep(0.3)

      errors = page.locator(".field-error")
      count = await errors.count()
      assert count > 0, "expected at least one validation error"
      # Verify the error has magenta colour via CSS.
      color = await errors.first.evaluate(
          "el => getComputedStyle(el).color"
      )
      # #ff5cd1 in RGB is rgb(255, 92, 209)
      assert "255" in color and "92" in color, f"expected magenta error colour, got {color!r}"
  ```

- `examples/config-editor/tests/pytest.ini`:
  ```ini
  [pytest]
  asyncio_mode = auto
  ```

**Exercise:**
```bash
cd /home/anl/picolet/examples/config-editor
uv run --with pytest --with pytest-asyncio pytest tests/ -v
# All four tests must pass.
```

---

#### Chunk 6 — Screenshots (`screenshots/` directory)

**Goal**: `examples/config-editor/screenshots/` with five required PNGs,
generated via `examples/config-editor/scripts/generate_screenshots.py` using
the PH19/PH20 Playwright headless Chromium pattern.

**Pattern reference:**
- `examples/pydfu/scripts/generate_screenshots.py` — complete structure.
- `examples/notes/scripts/generate_screenshots.py` — simpler mock, same skeleton.

**Required screenshots:**

| Filename | Route / state | Mock data |
|---|---|---|
| `file-picker.png` | `/` — picker view, path partially typed | `list_dir` returns a directory listing; `list_schemas` returns `["myapp"]` |
| `edit-toml.png` | `/edit` — TOML file loaded, no errors | `load` returns fixture TOML document |
| `edit-yaml-with-errors.png` | `/edit` — YAML file loaded, validation errors showing in magenta | `load` returns fixture YAML document; `validate` returns two errors |
| `diff-add.png` | `/diff` — diff showing added lines | `save` returns diff with `+` lines |
| `diff-delete.png` | `/diff` — diff showing deleted lines | `save` returns diff with `-` lines |

**Mock fixture for `window.picolet`:**

```python
_MOCK_TOML_DOC = {
    "server": {"host": "localhost", "port": 8080, "debug": False},
    "database": {"url": "postgresql://localhost/myapp", "pool_size": 5},
}

_MOCK_YAML_DOC = {
    "logging": {"level": "info", "file": "/var/log/myapp.log"},
    "cache": {"backend": "redis", "ttl": 300},
}

_MOCK_ERRORS = [
    {"path": "logging.level", "message": "value not in enum ['debug', 'warning', 'error']"},
    {"path": "cache.ttl", "message": "expected type integer, got str"},
]

_MOCK_DIFF_ADD = [
    "--- original",
    "+++ new",
    "@@ -1,4 +1,5 @@",
    " [server]",
    " host = \"localhost\"",
    "-port = 8080",
    "+port = 9090",
    "+timeout = 30",
    " debug = false",
]

_MOCK_DIFF_DEL = [
    "--- original",
    "+++ new",
    "@@ -1,6 +1,4 @@",
    " [database]",
    "-url = \"postgresql://localhost/myapp\"",
    "-pool_size = 5",
    "-max_connections = 20",
    "+url = \"postgresql://prod-db/myapp\"",
]
```

**`window.picolet` mock JS structure:**

The mock JS (following pydfu's `_build_mock_picolet_js` pattern) maps each
command name to a fixture response. Different screenshot states use different
mock configurations. The `validate` command response is configurable:
- For `edit-toml.png`: `validate` returns `{errors: [], ok: true}`.
- For `edit-yaml-with-errors.png`: `validate` returns `{errors:
  _MOCK_ERRORS, ok: true}`. Vue's `EditView` calls `validate` on mount if
  `state.schemaName` is set, or the screenshot script calls it via
  `page.evaluate`.

The screenshot script pre-navigates to `/edit` by setting the shared store
state via `page.evaluate("() => { window.__initState = <JSON>; }")` before
Vue boots. App.vue reads `window.__initState` on mount and pre-populates the
reactive store. This avoids the picker flow for mid-flow screenshots.

**Pixel verification for config-editor:**

```python
def _check_screenshot_config_editor(path: Path, expect_magenta: bool = False) -> None:
    from PIL import Image
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC
    img = Image.open(path).convert("RGB")
    w, h = img.size
    assert w >= 1000 and h >= 700, f"{path.name}: {w}x{h} below 1000x700"
    pixels = list(img.getdata())
    # Phosphor background: #0d1b0d = (13, 27, 13)
    has_bg = any(r < 40 and g < 60 and b < 40 for r, g, b in pixels)
    assert has_bg, f"{path.name}: no near-black phosphor background pixels"
    # Phosphor green: #a3ff7c = (163, 255, 124)
    has_green = any(
        abs(r - 163) <= 40 and abs(g - 255) <= 10 and abs(b - 124) <= 40
        for r, g, b in pixels
    )
    assert has_green, f"{path.name}: no phosphor-green pixels"
    if expect_magenta:
        # Magenta: #ff5cd1 = (255, 92, 209)
        has_magenta = any(
            abs(r - 255) <= 20 and abs(g - 92) <= 30 and abs(b - 209) <= 30
            for r, g, b in pixels
        )
        assert has_magenta, f"{path.name}: no magenta pixels (expected validation error)"
```

`edit-yaml-with-errors.png` is verified with `expect_magenta=True`. All other
screenshots are verified with `expect_magenta=False` (verifies magenta does NOT
bleed into non-error states — magenta absence is part of the spec).

**Files to create:**

- `examples/config-editor/scripts/generate_screenshots.py` — full script
  following the PH19/PH20 structure. Five screenshot capture blocks. Pixel
  verification for each.
- `examples/config-editor/screenshots/` — directory; populated by running
  the script.

**Exercise:**
```bash
cd /home/anl/picolet/examples/config-editor
uv run scripts/generate_screenshots.py
# Five PNGs must appear in screenshots/
```

---

#### Chunk 7 — `init_cmd` template wiring (`--template config-editor`)

**Goal**: `picolet init <name> --template config-editor` scaffolds a buildable
copy of the config-editor app with `{{name}}` substituted.

**Pattern reference:**
- `packages/picolet/picolet/init_cmd.py` — `_KNOWN_TEMPLATES` frozenset,
  `_copy_template` function, `{{name}}` substitution logic.
- PH20 added `"notes"` to `_KNOWN_TEMPLATES` using this exact mechanism.
  PH21 adds `"config-editor"`.

**Files to create:**

- `packages/picolet/picolet/templates/config-editor/` — structurally
  identical to `examples/config-editor/` with `{{name}}` substitutions in all
  text files (`.py`, `.toml`, `.html`, `.ts`, `.vue`, `.json`). The
  `_TEXT_EXTENSIONS` frozenset already includes these suffixes.
  - `picolet.toml`: `name = "{{name}}"`, window `title = "{{name}}"`.
  - `package.json`: `"name": "{{name}}"`.
  - `ui/index.html`: `<title>{{name}}</title>`.
  - `src/main.py`: header comment `# {{name}} — schema-driven config editor`.
  - The schemas directory default path uses `"config-editor"` hardcoded in
    `examples/config-editor/src/config_store.py`. In the template copy, the
    `_schemas_dir()` function uses `"{{name}}"` as the subdirectory under
    `~/.config/`. This means a scaffolded app named `my-editor` stores schemas
    at `~/.config/my-editor/schemas/`.
  - `src/micro_yaml.py` — copy verbatim (binary-safe text, but it is `.py`
    so `_TEXT_EXTENSIONS` includes it; ensure no `{{name}}` appears in the
    vendored file accidentally).
  - `ui/public/fonts/JetBrainsMono-Regular.woff2` — byte-copied (not in
    `_TEXT_EXTENSIONS`; `shutil.copy2` handles binary fonts correctly).

**Files to modify:**

- `packages/picolet/picolet/init_cmd.py`:
  - Add `"config-editor"` to `_KNOWN_TEMPLATES` frozenset.
  - Update the `--template` help string to include `"config-editor"`.

**Exercise:**
```bash
cd /tmp
picolet init my-config-editor --template config-editor
cd my-config-editor
picolet validate                        # must exit 0
npm install --prefer-offline
picolet build --no-sbom                 # must produce target/linux-x64/my-config-editor
```

---

#### Chunk 8 — Phase tests and exit gate

**Goal**: `tests/phase-21/run.sh` exercises all FR-EX and NFR-EX gates.

**Files to create:**

- `tests/phase-21/run.sh`:

| Gate | Proves | Command |
|---|---|---|
| A | FR-EX-3 scaffold: `picolet validate` exits 0 | `cd examples/config-editor && picolet validate` |
| B | FR-EX-3 build: binary produced | `picolet build --no-sbom` → `target/linux-x64/config-editor` exists |
| C | NFR-EX-1 size: binary ≤ 3 MiB | `wc -c target/linux-x64/config-editor` ≤ 3145728 |
| D | NFR-EX-4 no CDN: no external URL refs in binary | `strings config-editor \| grep -cE "cdn\.|unpkg\.|jsdelivr\."` = 0 |
| E | NFR-EX-2 startup ≤ 1500 ms | AppHarness `time_to_ready` assertion |
| F | FR-EX-3 TOML load+save: round-trip verified | `smoke_toml.py` exits 0 |
| G | FR-EX-3 YAML load+save: round-trip verified | `smoke_yaml.py` exits 0 |
| H | FR-EX-3 JSON load+save: round-trip verified | `smoke_json.py` exits 0 |
| I | FR-EX-3 validate: errors returned for invalid doc | `smoke_validate.py` exits 0 |
| J | FR-EX-3 diff: unified diff returned on save | `smoke_diff.py` exits 0 |
| K | FR-EX-6 screenshots: five PNGs present + valid | PIL verify loop |
| L | FR-EX-5 tests: Playwright suite passes | `pytest examples/config-editor/tests/ -v` exits 0 |
| M | NFR-EX-3 CSS ≤ 50 KB gzip | `gzip -c dist/assets/*.css \| wc -c` ≤ 51200 |
| N | NFR-EX-AESTHETIC monospace: JetBrains Mono in binary | `strings config-editor \| grep -q "JetBrains"` |
| O | FR-EX-3 template: `picolet init --template config-editor` scaffolds + builds | `picolet init` in tempdir + build |

- `tests/phase-21/smoke_toml.py` — PEP 723 script that creates a TOML file,
  calls `load`, modifies a value, calls `save`, reads back the file and asserts
  the value changed:

  ```python
  # /// script
  # requires-python = ">=3.11"
  # dependencies = ["picolet-cli"]
  # ///
  """Smoke: TOML load → modify → save round-trip."""
  import asyncio, tempfile, json
  from pathlib import Path
  from picolet.testing import AppHarness

  BINARY = Path(__file__).parent.parent.parent / "examples/config-editor/target/linux-x64/config-editor"

  async def main():
      with tempfile.TemporaryDirectory() as tmp:
          tmp_path = Path(tmp)
          cfg_dir = tmp_path / "config-editor"
          cfg_dir.mkdir()
          toml_file = tmp_path / "smoke.toml"
          toml_file.write_text('[app]\nversion = "1.0"\n', encoding="utf-8")
          async with AppHarness(str(BINARY), env={"PICOLET_CONFIG_DIR": str(cfg_dir)}) as h:
              result = await h.page.evaluate(
                  f"window.picolet.invoke('load', {{path: {json.dumps(str(toml_file))}}})"
              )
              assert result.get("format") == "toml", f"unexpected format: {result}"
              doc = result["document"]
              doc["app"]["version"] = "2.0"
              save_result = await h.page.evaluate(
                  f"window.picolet.invoke('save', {json.dumps({'path': str(toml_file), 'format': 'toml', 'document': doc})})"
              )
              assert save_result.get("ok"), f"save failed: {save_result}"
              content = toml_file.read_text(encoding="utf-8")
              assert "2.0" in content, f"expected 2.0 in {content!r}"
              assert "1.0" not in content, f"1.0 still in {content!r}"
              print("TOML round-trip: OK")

  asyncio.run(main())
  ```

- `tests/phase-21/smoke_yaml.py` — same structure, uses a YAML fixture file.
- `tests/phase-21/smoke_json.py` — same structure, uses a JSON fixture file.
- `tests/phase-21/smoke_validate.py` — loads a TOML file, calls `validate`
  with an intentionally invalid document, asserts `errors` list is non-empty.
- `tests/phase-21/smoke_diff.py` — calls `save` after a modification, asserts
  the returned `diff` list is non-empty and contains `+` / `-` prefixed lines.

**Exercise:**
```bash
cd /home/anl/picolet
bash tests/phase-21/run.sh --verbose
# All gates PASS or SKIP.
```

---

### Open questions

**O1 — `micro-yaml` source and version pinning.**
The `micro-yaml` vendored file must be pinned to a specific commit hash to
prevent upstream drift. At implementation time, the developer should record
the commit SHA in the module docstring of `src/micro_yaml.py` and in a
`[PH21] Decision:` commit. If `micropython-lib` ships a version of this module
in the Python stdlib path, prefer that over a third-party source (avoids a
separate provenance record in the SBOM).

**O2 — `tomllib` availability in the picolet MicroPython runtime build.**
The phase plan asserts `tomllib` is available. The developer must verify this
at Chunk 2 start by attempting `import tomllib` in the MicroPython frozen
environment. If it is absent, the fallback is to vendor the CPython reference
implementation from `micropython-lib/python-stdlib/tomllib/`. This is the same
module; it is pure Python and ports without changes. Record the finding as a
`[PH21] Caveat:` commit regardless of which path is taken.

**O3 — `difflib` availability in the picolet MicroPython runtime build.**
Same caveat as O2. `difflib` is in `micropython-lib/python-stdlib/difflib/`
and is pure Python. If absent from the frozen env, vendor it. The diff use
case needs only `unified_diff`; a 200-line extraction of just that function
from CPython's `difflib.py` is sufficient if the full module is too large.

**O4 — TOML serialiser completeness vs. datetime values.**
`tomllib` can parse TOML datetime values (Python `datetime` objects). The
inline `_toml_dumps()` does not handle them — it will raise `TypeError`. If
the loaded config file contains datetime fields, the save will fail. This is
an acceptable limitation for v1.1; the error message should be explicit:
`"_toml_dumps: unsupported type datetime.datetime — datetime fields are
read-only in this editor"`. Record as a known limitation in the module
docstring.

**O5 — Reactive store vs. Vue Router query params for cross-route state.**
The plan uses a module-level `reactive` store (`store.ts`) to pass document
data between routes. An alternative is to use Vue Router's navigation guards
and serialise the document to `sessionStorage`. The reactive store approach
is simpler and avoids sessionStorage size limits for large configs, but it
means back-button navigation to `/` resets the store. This is acceptable for
the config editor use case (single-file session), but should be documented as
a limitation if multiple concurrent edit sessions are ever needed.

**O6 — Field rendering for deeply nested TOML/YAML/JSON.**
The plan renders one level of nesting as sub-sections with `═════` rules.
Configs with three or more levels of nesting are not handled — deeply nested
objects would need recursive section rendering or a collapsible tree. For
v1.1, nesting depth > 2 shows a read-only `<pre>` dump of the sub-tree with
an `[edit as raw text]` fallback. The aesthetics of the fallback must still
comply: monospace, phosphor green, no syntax highlighting.

---

### Exit gate

A successful PH21 has all of the following true, verified by
`bash tests/phase-21/run.sh` exiting 0:

| Gate | Proves |
|---|---|
| A | FR-EX-3 scaffold: `picolet validate` exits 0 |
| B | FR-EX-3 build: binary at `target/linux-x64/config-editor` |
| C | NFR-EX-1: binary ≤ 3 MiB |
| D | NFR-EX-4: no CDN references in binary |
| E | NFR-EX-2: startup ≤ 1500 ms |
| F | FR-EX-3 TOML round-trip |
| G | FR-EX-3 YAML round-trip |
| H | FR-EX-3 JSON round-trip |
| I | FR-EX-3 validate: errors returned for invalid input |
| J | FR-EX-3 diff: unified diff returned on save |
| K | FR-EX-6: five screenshots present + valid PNG, each > 1 KB |
| L | FR-EX-5: `pytest examples/config-editor/tests/` exits 0 |
| M | NFR-EX-3: CSS ≤ 50 KB gzipped |
| N | NFR-EX-AESTHETIC: JetBrains Mono referenced in binary strings |
| O | FR-EX-3 template: `picolet init --template config-editor` scaffolds + builds |

NFR-EX-AESTHETIC is additionally human-judged. Gate N (font presence) is the
automated proxy. The human reviewer must confirm:
- Background is solid `#0d1b0d` with no gradients or textures.
- All text is monospace JetBrains Mono.
- Terminal frame is visibly centred within a wider viewport.
- Section dividers use `═` box-drawing characters.
- Cursor block blinks at 1 Hz on focused inputs.
- Magenta appears only on validation errors.
- The diff view uses `+`/`-` prefixes with no syntax highlighting.

---

### Risks / footguns

**R1 — `micro-yaml` parse failures on real-world YAML.**
The vendored micro-yaml subset handles simple config YAML but fails on anchors,
tags, multi-document streams, and multi-line block scalars. If the loaded YAML
uses any of these features, `micro_yaml.load()` will raise an exception. The
backend must catch all exceptions from the parser and return
`{"ok": False, "error": "YAML parse error: <message>"}`. The Vue frontend
renders this as a top-level `!! ` error above the form.

**R2 — TOML write producing syntactically invalid output.**
The inline `_toml_dumps()` serialiser handles the common cases but may produce
invalid TOML for edge cases (keys containing special characters, bare integers
that TOML requires quoting, etc.). After every `save()`, the backend should
validate its own output by parsing it back with `tomllib.loads()`. If the
round-trip parse fails, the save is aborted and the error returned to the
frontend. This is a cheap guard: 5 extra lines of Python.

**R3 — `caret-color: transparent` breaks accessibility.**
Hiding the browser's native cursor is a deliberate aesthetic choice. The custom
`█` pseudo-element provides visual feedback. This is acceptable for a
demonstration app; document the choice in a `[PH21] Decision:` commit so the
rationale is recorded.

**R4 — Reactive store state survives route navigation but is lost on page reload.**
The `store.ts` reactive object is module-level; it does not persist across hard
page reloads. In the webview (not a browser tab), hard reloads can be triggered
by the developer tools. Tests should not rely on state surviving a reload; if
a test navigates away and back, the state is still in memory (no reload
occurred). The AppHarness fixture restarts the binary between tests, so state
is always clean at test start.

**R5 — `difflib` not in MicroPython frozen env (see O3).**
If `difflib` is absent, the `save()` command crashes on the first call. The
developer must verify early (end of Chunk 2 Python smoke test). If absent,
vendor the 200-line extraction and add a `[PH21] Caveat:` commit.

**R6 — Cursor-block `::after` not visible in Playwright screenshots.**
The `::after` blink is implemented via `:focus-within`. In Playwright headless
Chromium, `fill()` on an input gives it focus; the `::after` pseudo should be
visible at screenshot time. However, if the `animation: cursor-blink 1s
step-start` is at its `opacity: 0` phase at screenshot time, the cursor block
disappears. Mitigation: the `no-animation` init script forces `opacity: 1`
by setting `animation-play-state: paused; animation-delay: 0ms` which freezes
the animation at its initial keyframe (0% = `opacity: 1`). This ensures the
cursor block is always visible in screenshots. Document this nuance in a code
comment.

**R7 — 80ch terminal frame width on different viewport sizes.**
The `80ch` measurement is relative to the font's `0` glyph width, which
varies slightly between rendering environments. At JetBrains Mono 14px on
Linux, `1ch ≈ 7.7px`, so `80ch ≈ 616px`. On Windows via WebView2 the
rendering engine is Chromium and should match. If there is a visible
discrepancy, measure empirically and add a comment. The requirement is
"80-column max width centred on wide screens" — exact pixel count is less
important than the retro visual effect.

**R8 — Screenshot `expect_magenta=False` assertions.**
For `file-picker.png`, `edit-toml.png`, `diff-add.png`, `diff-delete.png`,
the pixel verification asserts that no magenta pixel is present. This is a
strict aesthetic gate: if any non-error UI element has picked up `--error`
colour, the test fails. This is intentional. CSS specificity errors that bleed
magenta into non-error elements are caught here before human review.

---

### Model tier recommendations

| Role | v1.1-plan default | Recommended | Rationale |
|---|---|---|---|
| planner | opus | **sonnet** (this artefact) | App-building work on a well-established baseline. PH19 and PH20 resolved all structural unknowns. The novel elements (library selection, minimal serialisers) are scoped and documented; implementation is mechanical. |
| developer | sonnet | **sonnet** | The Python backend is pure stdlib + vendored modules — no ffi, no USB, no async event loops. The Vue frontend is Composition API with no novel patterns beyond the cursor-block CSS. The inline serialisers and validator require care but are within sonnet scope. Risk areas R1 and R5 may trigger Caveat commits. |
| sqe | sonnet | **sonnet** | Test patterns inherited directly from PH19/PH20. No novel test infrastructure. |
| tester | sonnet | **opus** | NFR-EX-AESTHETIC requires human judgement on a nuanced brutalist design. The terminal frame, cursor block, box-drawing dividers, magenta-absence verification, and diff rendering all have specific visual requirements that are harder to fake past a careful eye than the notes editorial aesthetic. |
