# PH20 — notes example app

## Plan

### Goal

Build `examples/notes/` — a markdown notes app that demonstrates Picolet for a
content-first creative tool. The app creates, edits, and persists markdown
notes to the host filesystem. Aesthetic is the **editorial / refined** direction:
warm off-white paper, italic serif headings, generous page margins, a single
sharp red accent that appears only in the unsaved state. No save button.

PH20 builds on PH18's Vue 3 + Vite + TypeScript toolchain without modifying it.
The pattern established by PH19 (structure, IPC commands, mock injection,
screenshot capture via Playwright headless Chromium, AppHarness tests) is
replicated directly. Notes differ from pydfu in one important way: storage is
the central concern. The Python side owns a writable directory on the host
filesystem; no USB or ffi is involved.

---

### Spec coverage

| Spec ID | Requirement | Where in this phase |
|---|---|---|
| FR-EX-2 | `picolet init <name> --template notes` scaffolds a markdown-backed notes app persisting to `~/.config/<app-name>/notes/` on Linux, `%APPDATA%\<app-name>\notes\` on Windows | Chunks 1–4 (app + Python storage + Vue UI) + Chunk 6 (init_cmd wiring) |
| FR-EX-5 | Each example ships `tests/` with Playwright integration tests | Chunk 5 (create → edit → save → reopen → delete flow with host FS assertions) |
| FR-EX-6 | Each example ships `screenshots/` with auto-generated PNGs covering major UI states | Chunk 7 (generate_screenshots.py using Playwright headless Chromium + mock window.picolet) |
| NFR-EX-1 | Binary size ≤ 3 MiB on linux-x64-webview | Chunk 8 (Gate C) |
| NFR-EX-2 | Start-up ≤ 1500 ms first interactive frame | Chunk 8 (Gate E, AppHarness time_to_ready) |
| NFR-EX-3 | CSS does not pull a runtime CSS framework heavier than 50 KB gzipped | Chunk 3 (hand-crafted CSS + `marked` ~30 KB gzipped; no component library) |
| NFR-EX-4 | No external CDN at runtime; all assets in romfs | Chunk 3 (fonts bundled in romfs as woff2; `marked` bundled by Vite into JS asset) |
| NFR-EX-5 | Deterministic screenshots; same inputs → byte-identical PNG | Chunk 7 (deterministic mock fixture; animations disabled via init script) |
| NFR-EX-6 | Screenshot gallery regenerated on every CI build; drift is CI failure | Chunk 7 + Chunk 8 (Gate H verifies PNGs present and valid) |
| NFR-EX-AESTHETIC | Must pass "show me the screenshot — is it memorable?" test | Chunk 3 (all aesthetic decisions spec-exact) |

---

### Dependencies

#### From v1 (already landed)

- `picolet.command` / `picolet.emit` / `picolet.run` at
  `packages/picolet-runtime/python/picolet/__init__.py`.
- `picolet._dispatcher.Dispatcher` wire format (newline-delimited JSON) at
  `packages/picolet-runtime/python/picolet/_dispatcher.py`.
- `pathlib`, `os`, `json`, `time` — available in MicroPython frozen environment.

#### From PH17 (already landed)

- `picolet.testing.AppHarness` at `packages/picolet/picolet/testing/_harness.py`.
- `picolet test --screenshot` CLI at `packages/picolet/picolet/test_cmd.py`.
- `window.picolet.__ready__ === true` contract waited on by AppHarness.

#### From PH18 (already landed)

- `[ui.frontend]` table parser + `npm run build` hook in `build_cmd.py`.
- `examples/with-vue/` and `examples/pydfu/` structural baseline.
- `createWebHashHistory()` as the required Vue Router mode under `picolet://`.

#### From PH19 (already landed)

- `examples/pydfu/scripts/generate_screenshots.py` — the Playwright headless
  Chromium + local HTTP server screenshot pattern. PH20 replicates this approach
  directly.
- `examples/pydfu/tests/conftest.py` — the AppHarness pytest fixture pattern.
- Font bundling pattern: woff2 files under `ui/public/fonts/`, CSS `@font-face`
  in `ui/src/assets/fonts.css`, imported once in `main.ts`.
- `vite.config.ts` pattern: `base: './'`, `root: 'ui'`, `build.outDir: '../dist'`.
- `picolet.toml` structure.
- `init_cmd._KNOWN_TEMPLATES` — PH20 adds `"notes"` to this list.

#### What PH23 needs from PH20

- `examples/notes/` present and buildable. PH23's mirror script copies it to
  `packages/picolet/picolet/templates/notes/`.
- `examples/notes/screenshots/` non-empty. PH23's CI screenshot job validates.

---

### Key research findings

**F1 — Host filesystem path resolution per platform.**

The Python side must resolve the notes storage directory without any platform
abstraction library — MicroPython frozen code has `os`, `pathlib.Path`, and
`sys`.

```python
import os, sys
from pathlib import Path

def _notes_dir(app_name: str = "notes") -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if not base:
            raise RuntimeError("APPDATA not set")
        p = Path(base) / app_name / "notes"
    else:
        # Linux and any other POSIX
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        p = base / app_name / "notes"
    p.mkdir(parents=True, exist_ok=True)
    return p
```

Test isolation is achieved by setting `PICOLET_NOTES_DIR` to override this
calculation:

```python
def _notes_dir(app_name: str = "notes") -> Path:
    override = os.environ.get("PICOLET_NOTES_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    ...
```

Integration tests set `PICOLET_NOTES_DIR` to a temp path keyed by pid:
`/tmp/notes-test-{pid}/`. This is the only test isolation mechanism needed —
no mock object, no interface swap (unlike pydfu's USB mock). The Python
storage commands work identically against the temp dir.

**F2 — Front matter: simple split, no library.**

MicroPython does not have `python-frontmatter` or `yaml`. The notes format
is deliberately constrained to a three-field YAML-like header:

```
---
title: My Note
created: 1747123456
updated: 1747127890
---

Body markdown content here.
```

Parsing is a plain string split — no YAML parser required:

```python
def _parse_note(text: str) -> dict:
    """Return {"title": str, "created": int, "updated": int, "body": str}."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            header = text[4:end]
            body = text[end + 5:]
            meta = {}
            for line in header.splitlines():
                if ": " in line:
                    k, _, v = line.partition(": ")
                    meta[k.strip()] = v.strip()
            return {
                "title": meta.get("title", "Untitled"),
                "created": int(meta.get("created", 0)),
                "updated": int(meta.get("updated", 0)),
                "body": body,
            }
    return {"title": "Untitled", "created": 0, "updated": 0, "body": text}

def _render_note(title: str, created: int, updated: int, body: str) -> str:
    return f"---\ntitle: {title}\ncreated: {created}\nupdated: {updated}\n---\n\n{body}"
```

This is the entire front matter strategy. No external dependency; no YAML
edge cases beyond the three known fields.

**F3 — Slug and filename scheme.**

Each note is stored as `<slug>-<unix-ts>.md` where `slug` is a URL-safe
version of the title (lowercase, spaces → hyphens, non-alphanumeric stripped,
max 40 chars) and `unix-ts` is the integer seconds timestamp at creation.
Example: `meeting-notes-1747123456.md`.

The slug is derived once at creation and does not change if the title is
subsequently edited (the filename stays stable; only the front matter `title`
field changes). This avoids rename complexity.

```python
import re, time

def _make_slug(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r" +", "-", s)
    s = s[:40].rstrip("-")
    return s or "note"

def _make_filename(slug: str) -> str:
    return f"{slug}-{int(time.time())}.md"
```

The IPC layer exposes the full filename (without `.md` extension) as the
`slug` field in list responses. Load/save/delete operations accept this full
slug (e.g. `"meeting-notes-1747123456"`). The Vue side treats it as an opaque
identifier.

**F4 — Markdown rendering: `marked` bundled by Vite.**

`marked` is a pure-JS markdown parser. At version 14.x, the ESM bundle is
approximately 25–30 KB gzipped. Vite tree-shakes and bundles it into the JS
asset — no CDN, satisfying NFR-EX-4. The Python side does no markdown
processing.

```bash
npm install marked@^14
```

In the Vue component:
```typescript
import { marked } from 'marked'
const rendered = computed(() => marked.parse(body.value))
```

The `marked` default output is safe for rendering inside a scoped `<div
v-html="rendered">` in the edit view's preview pane. No additional sanitiser
is needed for a single-user local notes app.

Size check: `marked@14` minified + gzipped is approximately 26 KB. Combined
with Vue 3 core (~40 KB gzipped) + hand-crafted CSS (~4 KB) + fonts (~200 KB
uncompressed in romfs), total romfs JS is under 200 KB. Binary budget:
runtime ~750 KB + romfs ~500 KB = ~1.25 MB, well under 3 MiB (NFR-EX-1).

**F5 — Vue Router dynamic slug route under `picolet://`.**

The edit route is `/edit/:slug`. In hash-mode Vue Router
(`createWebHashHistory()`), this renders as `picolet:///ui/index.html#/edit/meeting-notes-1747123456`.
The custom scheme handler serves the static `index.html` for any path; the
fragment is handled entirely client-side by Vue Router. Dynamic params work
exactly as they do in a normal SPA:

```typescript
// router/index.ts
import { createRouter, createWebHashHistory } from 'vue-router'
import ListView from '../views/ListView.vue'
import EditView from '../views/EditView.vue'
import AboutView from '../views/AboutView.vue'

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/',           component: ListView },
    { path: '/edit/:slug', component: EditView },
    { path: '/about',      component: AboutView },
  ],
})
```

In `EditView.vue`, the slug is read from `useRoute().params.slug`. No special
handling is needed for the `picolet://` scheme — hash routing bypasses the
scheme handler entirely for navigation.

**F6 — Unsaved dot: no save button; Cmd/Ctrl-S saves.**

The spec mandates no save button. The only feedback affordance for unsaved
state is a 2×2 px red dot at the top-left of the editor pane. This dot
appears when the in-memory body diverges from the last-saved body.

The dot is a `<span>` with:
```css
.unsaved-dot {
  position: absolute;
  top: 12px;
  left: 12px;
  width: 8px;
  height: 8px;
  background: var(--mark);   /* #c4392b */
  border-radius: 50%;
  pointer-events: none;
}
```

`v-if="isUnsaved"` controls visibility.

Keyboard shortcut: `keydown` listener on `document` in `EditView.vue`:

```typescript
function onKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') {
    e.preventDefault()
    saveNote()
  }
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
```

WebKitGTK on Linux passes `ctrlKey` for Ctrl+S. WebView2 on Windows passes
`ctrlKey` for Ctrl+S and `metaKey` for Cmd+S (on keyboards that have a Meta
key). Testing with `ctrlKey || metaKey` covers both platforms without
conditional logic.

**F7 — Search: client-side filter, no Python involvement.**

The list view has an inline search input. Filtering is entirely client-side:
the full note list is loaded once on mount (via `list_notes()`), then filtered
by a computed ref:

```typescript
const query = ref('')
const filtered = computed(() =>
  query.value.trim()
    ? notes.value.filter(n =>
        n.title.toLowerCase().includes(query.value.toLowerCase()))
    : notes.value
)
```

The `search-active` screenshot state is produced by pre-populating `query`
in the mock before screenshotting. No IPC command for search.

**F8 — Screenshot approach: Playwright headless Chromium + local HTTP server.**

PH19 proved this approach in `examples/pydfu/scripts/generate_screenshots.py`.
PH20 replicates the same structure:

1. `npm run build` produces `dist/` with `index.html` + assets + fonts.
2. Python HTTP server serves `dist/` on a random localhost port.
3. Playwright headless Chromium navigates to `http://127.0.0.1:{port}/#/`.
4. `ctx.add_init_script(mock_js)` installs `window.picolet` before Vue boots.
5. `ctx.add_init_script(disable_animations_js)` kills CSS transitions/animations.
6. Navigation + wait + screenshot per state.

The mock `window.picolet` for notes returns a deterministic fixture of three
notes (title, slug, created, updated timestamps are hardcoded). Body content
for the edit views is a fixed markdown string.

Pixel verification for notes checks:
- PNG magic bytes present.
- Dimensions ≥ 1000×700.
- At least one pixel is warm off-white (~`#f7f3ed`, tolerance ±15).
- At least one pixel is near-ink dark (~`#1a1715`, tolerance ±15).
- The `edit-unsaved` and `edit-typing-mid` screenshots additionally assert at
  least one pixel is mark-red (~`#c4392b`, tolerance ±30).

**F9 — Test isolation: temp dir keyed by pid.**

```python
# tests/conftest.py
import os, pytest, tempfile
from pathlib import Path
from picolet.testing import AppHarness

BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "notes"

@pytest.fixture
async def notes_dir(tmp_path):
    """Isolated per-test notes directory under tmp_path."""
    d = tmp_path / "notes"
    d.mkdir()
    return d

@pytest.fixture
async def harness(notes_dir):
    h = AppHarness(
        str(BINARY),
        env={"PICOLET_NOTES_DIR": str(notes_dir)},
    )
    await h.start()
    yield h
    await h.stop()
```

pytest's `tmp_path` fixture provides a unique temp directory per test
function. `PICOLET_NOTES_DIR` override in the Python backend routes all storage
to this temp directory. After each test, `tmp_path` is cleaned up automatically
by pytest. No shared state between tests; no pollution of `~/.config/notes/`.

The test suite exercises host FS at each step (create → read-back → edit →
save → reopen → delete → assert gone) so the FS interaction is fully verified
without mocking the storage layer.

**F10 — Two-column layout and narrow fallback.**

The two-column layout (list 320px fixed, editor flex-grow) uses CSS grid:

```css
.app-columns {
  display: grid;
  grid-template-columns: 320px 1fr;
  height: 100%;
  overflow: hidden;
}
@media (max-width: 1000px) {
  .app-columns {
    grid-template-columns: 1fr;
  }
  /* On narrow: list takes full width; editor is navigated to via route change */
}
```

The narrow layout does not implement a drawer (the spec says "single-column
with a drawer on narrow"). A route-based approach is simpler: on narrow
screens, navigating to `/edit/:slug` hides the list column. The spec's
"drawer" language is interpreted as route navigation on narrow — this keeps
the implementation simple and avoids a custom drawer component. If a drawer
is required, note as open question O2.

**F11 — Background paper-grain.**

The spec requires "a faint vertical paper-grain (CSS repeating linear
gradient at 0.5% opacity)". Implementation:

```css
body::after {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image: repeating-linear-gradient(
    to bottom,
    rgba(26, 23, 21, 0.5) 0px,
    transparent 1px,
    transparent 4px
  );
  opacity: 0.005;   /* 0.5% */
}
```

At 0.5% opacity the grain is barely perceptible — a subliminal texture that
reads as paper under close inspection. It is rendered via CSS (no PNG asset),
so it costs nothing in the romfs budget.

---

### Aesthetic spec

All values are mandatory. The developer must not deviate without recording a
decision commit.

#### CSS custom properties

```css
:root {
  --paper:       #f7f3ed;   /* body background — warm off-white */
  --ink:         #1a1715;   /* primary text — near-black */
  --ink-soft:    #5b524c;   /* secondary text — warm mid-grey */
  --mark:        #c4392b;   /* single accent — unsaved dot only */
  --rule:        #e3dccf;   /* dividers, borders */
  --surface:     #f0ebe3;   /* slightly warmer than paper, for list hover */
  --font-serif:  'Source Serif 4', Georgia, serif;
  --font-sans:   'Source Sans 3', system-ui, sans-serif;
}
```

#### Font choices

Both fonts are SIL Open Font Licence 1.1 — ship-safe, no commercial
restriction.

- **Source Serif 4** (display / headings): The spec names GT Sectra as the
  ideal; Source Serif 4 is the approved OFL alternative. Used for `h1` at
  42px italic, and for the note title in the editor header. Variable font
  woff2 (covers 100–900 weight range) from the Google Fonts GitHub releases:
  `SourceSerif4[opsz,wght].woff2`. Approximately 120 KB.
- **Source Sans 3** (body text, UI labels): The spec names Söhne / Tiempos
  Text; Source Sans 3 is the OFL alternative. Used for body text at 18px /
  1.6 line-height, list items, and all UI chrome labels. Variable font woff2:
  `SourceSans3[wght].woff2`. Approximately 85 KB.

Both variable woff2 files go under `ui/public/fonts/`. Total font weight:
~205 KB.

Download sources:
- `https://github.com/adobe-fonts/source-serif/releases` (SourceSerif4 variable)
- `https://github.com/adobe-fonts/source-sans/releases` (SourceSans3 variable)

#### Font-face declarations

```css
/* ui/src/assets/fonts.css */
@font-face {
  font-family: 'Source Serif 4';
  src: url('/fonts/SourceSerif4[opsz,wght].woff2') format('woff2');
  font-weight: 200 900;
  font-style: normal italic;
  font-display: block;
}

@font-face {
  font-family: 'Source Sans 3';
  src: url('/fonts/SourceSans3[wght].woff2') format('woff2');
  font-weight: 200 900;
  font-style: normal;
  font-display: block;
}
```

#### Typography rules

```css
body {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: 18px;
  line-height: 1.6;
  margin: 0;
}

h1 {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: 42px;
  font-weight: 400;
  letter-spacing: -0.01em;   /* tracking -1% */
  line-height: 1.1;
  margin: 0 0 0.5em;
  color: var(--ink);
}

h2 { font-family: var(--font-serif); font-size: 26px; font-weight: 400; }
h3 { font-family: var(--font-serif); font-size: 20px; font-weight: 400; }
```

#### Layout grid

Two-column shell on >1000px viewport:

```
┌──────────────────────────────────────────────────────────┐
│  note list (320px fixed)  │  editor pane (flex-grow 1)  │
│                           │                              │
│  [Search input]           │  [h1 — italic serif title]  │
│  ──────────────────────   │                              │
│  Note title               │  [textarea / preview]        │
│  Note title               │                              │
│  Note title               │  96px top padding            │
│  ...                      │  64px left/right padding     │
│                           │                              │
└──────────────────────────────────────────────────────────┘
   border-right: 1px solid var(--rule)
```

Editor pane padding (gives "page" feel):
```css
.editor-pane {
  padding: 96px 64px 64px;
  overflow-y: auto;
  position: relative;   /* anchor for .unsaved-dot */
}
```

#### Unsaved dot

```css
.unsaved-dot {
  position: absolute;
  top: 12px;
  left: 12px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--mark);   /* #c4392b */
  pointer-events: none;
  transition: opacity 120ms ease;
}
```

The dot is the only place `--mark` (`#c4392b`) appears in the entire app.
No save button. No other red anywhere.

#### Note list item

```css
.note-item {
  padding: 12px 16px;
  cursor: pointer;
  border-bottom: 1px solid var(--rule);
  transition: background 80ms;
}
.note-item:hover { background: var(--surface); }
.note-item.active { background: var(--surface); }

.note-item-title {
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 400;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.note-item-date {
  font-size: 12px;
  color: var(--ink-soft);
  margin-top: 2px;
}
```

#### Motion

Minimal: only two transitions are permitted:
- `.note-item` background on hover: `80ms linear`.
- `.unsaved-dot` opacity: `120ms ease`.

All other transitions/animations: none. No page-transition animations;
route changes are instant. This reinforces the "printed medium" feel of
the editorial aesthetic — the app does not animate, it reveals.

---

### Implementation breakdown

Six chunks ordered by dependency. Each chunk is independently testable.

---

#### Chunk 1 — `examples/notes/` scaffold (structure, picolet.toml, Vite config)

**Goal**: Lay down directory structure and configuration so `picolet build`
runs cleanly before any real Python or Vue logic is present.

**Pattern reference:**
- `examples/pydfu/picolet.toml` — exact same structure.
- `examples/pydfu/vite.config.ts` — copy with `name: 'notes'`.
- `examples/pydfu/package.json` — same Vue + Vite + TypeScript deps.

**Files to create:**

- `examples/notes/picolet.toml`:
  ```toml
  [app]
  name = "notes"
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
  title = "Notes"
  size = [1200, 800]
  resizable = true
  ```

- `examples/notes/package.json` — same as pydfu but `name = "notes"` and
  adds `"marked": "^14"` to `dependencies`.

- `examples/notes/vite.config.ts` — identical to pydfu's. `base: './'`,
  `root: 'ui'`, `build.outDir: '../dist'`.

- `examples/notes/tsconfig.json` / `tsconfig.node.json` — copy from pydfu,
  name substituted.

- `examples/notes/ui/index.html` — Vite entry, title `Notes`.

- `examples/notes/ui/src/main.ts` — `createApp(App).use(router).mount('#app')`.
  Imports `./assets/main.css` and `./assets/fonts.css`.

- `examples/notes/ui/src/env.d.ts` — triple-slash reference to `picolet.d.ts`.

- `examples/notes/ui/src/picolet.d.ts` — copy from pydfu (or reference the
  package-level type; pydfu ships its own copy for self-containedness).

- `examples/notes/ui/src/App.vue` — stub: `<RouterView />` for this chunk.

- `examples/notes/ui/public/fonts/` — directory (woff2 files added in Chunk 3).

- `examples/notes/src/main.py` — stub that boots a `ui.Application()`.

**Exercise:**
```bash
cd /home/anl/picolet/examples/notes
npm install --prefer-offline
picolet build --no-sbom
# binary exists at target/linux-x64/notes
```

---

#### Chunk 2 — Python backend: `notes_store.py` + `main.py` IPC commands

**Goal**: Implement the five IPC commands using host-filesystem storage.
Test isolation via `PICOLET_NOTES_DIR` env var override.

**Pattern reference:**
- `examples/pydfu/src/main.py` — `@picolet.command` decorator pattern.
- `examples/pydfu/src/pydfu_adapter.py` — module-level state and env-var
  override pattern.

**Files to create:**

- `examples/notes/src/notes_store.py`:

  ```python
  """notes_store.py — host-filesystem note storage.

  Storage path (in priority order):
    1. PICOLET_NOTES_DIR env var (test isolation)
    2. Linux: $XDG_CONFIG_HOME/notes/ or ~/.config/notes/
    3. Windows: %APPDATA%\notes\

  Note file format:
    Filename: <slug>-<unix-ts>.md
    Content:  YAML-lite front matter (title/created/updated) + blank line + body
  """
  import os, sys, re, time
  from pathlib import Path


  def _notes_dir() -> Path:
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
      s = title.lower().strip()
      s = re.sub(r"[^a-z0-9 ]", "", s)
      s = re.sub(r" +", "-", s)
      s = s[:40].rstrip("-")
      return s or "note"


  def _parse_note(text: str) -> dict:
      if text.startswith("---\n"):
          end = text.find("\n---\n", 4)
          if end != -1:
              header = text[4:end]
              body = text[end + 5:]
              meta = {}
              for line in header.splitlines():
                  if ": " in line:
                      k, _, v = line.partition(": ")
                      meta[k.strip()] = v.strip()
              return {
                  "title": meta.get("title", "Untitled"),
                  "created": int(meta.get("created", 0)),
                  "updated": int(meta.get("updated", 0)),
                  "body": body,
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
              slug = f.stem   # filename without .md
              notes.append({
                  "slug": slug,
                  "title": m["title"],
                  "created": m["created"],
                  "updated": m["updated"],
              })
          except Exception:
              pass   # skip malformed files silently
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
      return {"slug": slug, "title": old["title"], "created": old["created"],
              "updated": now}


  def create_note(title: str) -> dict:
      """Create a new note; return its metadata."""
      d = _notes_dir()
      now = int(time.time())
      slug = f"{_make_slug(title)}-{now}"
      f = d / f"{slug}.md"
      # Handle slug collision (same title, same second)
      counter = 1
      while f.exists():
          f = d / f"{slug}-{counter}.md"
          slug = f"{_make_slug(title)}-{now}-{counter}"
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
  ```

- `examples/notes/src/main.py`:

  ```python
  """notes — markdown notes app (picolet example).

  IPC commands:
    list_notes()            -> list of {slug, title, created, updated}
    load_note(slug)         -> {slug, title, created, updated, body}
    save_note(slug, body)   -> {slug, title, created, updated}
    create_note(title)      -> {slug, title, created, updated}
    delete_note(slug)       -> {"ok": True}
  """
  import picolet
  import picolet_ui as ui
  import notes_store as store


  @picolet.command
  async def list_notes(args):
      return store.list_notes()


  @picolet.command
  async def load_note(args):
      slug = args.get("slug") if isinstance(args, dict) else args
      try:
          return store.load_note(slug)
      except FileNotFoundError as e:
          return {"ok": False, "error": str(e)}


  @picolet.command
  async def save_note(args):
      slug = args.get("slug") if isinstance(args, dict) else None
      body = args.get("body", "") if isinstance(args, dict) else ""
      try:
          return store.save_note(slug, body)
      except FileNotFoundError as e:
          return {"ok": False, "error": str(e)}


  @picolet.command
  async def create_note(args):
      title = args.get("title", "Untitled") if isinstance(args, dict) else str(args)
      return store.create_note(title)


  @picolet.command
  async def delete_note(args):
      slug = args.get("slug") if isinstance(args, dict) else args
      try:
          store.delete_note(slug)
          return {"ok": True}
      except FileNotFoundError as e:
          return {"ok": False, "error": str(e)}


  def main():
      app = ui.Application()
      app.run()


  main()
  ```

**Exercise:**
```bash
cd /home/anl/picolet/examples/notes
PICOLET_NOTES_DIR=/tmp/notes-test python3 -c "
import sys; sys.path.insert(0, 'src')
import notes_store as s
n = s.create_note('Hello World')
print('created:', n)
notes = s.list_notes()
print('list:', notes)
loaded = s.load_note(n['slug'])
print('loaded body:', repr(loaded['body']))
s.save_note(n['slug'], '# Hello\n\nSome content.')
loaded2 = s.load_note(n['slug'])
print('after save:', repr(loaded2['body']))
s.delete_note(n['slug'])
print('deleted; list now:', s.list_notes())
"
```

---

#### Chunk 3 — Vue frontend: aesthetic, fonts, components, three routes

**Goal**: Build the complete Vue 3 frontend with the editorial aesthetic —
font loading, CSS custom properties, paper-grain background, three routes
(`/`, `/edit/:slug`, `/about`), markdown rendering via `marked`, unsaved dot.

**Pattern reference:**
- `examples/pydfu/ui/src/assets/fonts.css` — `@font-face` + `font-display:
  block` pattern.
- `examples/pydfu/ui/src/assets/main.css` — CSS custom properties block,
  global resets.
- `examples/pydfu/ui/src/router/index.ts` — `createWebHashHistory()` router.
- `examples/pydfu/ui/src/App.vue` — `provide`/`inject` pattern for shared
  state across routes.

**Files to create / modify:**

- `examples/notes/ui/public/fonts/SourceSerif4[opsz,wght].woff2` — variable
  font woff2 from Adobe Fonts / google-webfonts-helper.
- `examples/notes/ui/public/fonts/SourceSans3[wght].woff2` — variable font
  woff2.

- `examples/notes/ui/src/assets/fonts.css` — two `@font-face` blocks as
  specified in the Aesthetic spec above.

- `examples/notes/ui/src/assets/main.css` — complete global stylesheet:
  - CSS custom properties block (`--paper`, `--ink`, `--ink-soft`, `--mark`,
    `--rule`, `--surface`, `--font-serif`, `--font-sans`).
  - `body` rule: `background: var(--paper)`, `color: var(--ink)`, 18px / 1.6.
  - `h1` rule: Source Serif 4, italic, 42px, letter-spacing -1%.
  - `h2`, `h3` rules: Source Serif 4, non-italic, smaller sizes.
  - `* { box-sizing: border-box; }`.
  - Paper-grain `body::after` pseudo-element.
  - `.note-item`, `.note-item-title`, `.note-item-date` styles.
  - `.unsaved-dot` style.
  - `.search-input` style: 1px `--rule` border, no border-radius, full-width,
    sans-serif 15px, paper background.
  - `a` style: `color: var(--ink)`, underline on hover only.

- `examples/notes/ui/src/router/index.ts` — three routes as shown in F5.

- `examples/notes/ui/src/App.vue` — thin shell. Provides no global shared
  state (unlike pydfu; notes has no cross-route shared device). Just renders
  `<RouterView />` in a full-height flex column. Screenshot mode detection
  (sets `.no-animation` on `documentElement` if `window.__PICOLET_SCREENSHOT_MODE__`
  is set) follows the pydfu pattern exactly.

- `examples/notes/ui/src/views/ListView.vue` — route `/`:
  - On mount: calls `window.picolet.invoke('list_notes')`, populates `notes` ref.
  - Inline search input bound to `query` ref; filtered computed ref.
  - Note list: `.note-item` divs, each showing title + relative date.
  - Click on a note navigates to `/edit/:slug`.
  - `[+ New Note]` button (text-only, no border box): calls
    `create_note({title: 'Untitled'})`, then navigates to the new note's edit
    route.
  - Empty state: a centred message in `--ink-soft` ("No notes yet. Press + to
    create one.").
  - Search active state: when `query` is non-empty and `filtered` is empty,
    show "No notes match your search."

- `examples/notes/ui/src/views/EditView.vue` — route `/edit/:slug`:
  - Slug from `useRoute().params.slug`.
  - On mount: calls `load_note({slug})`, populates `title` ref, `body` ref,
    `savedBody` ref (the last-saved value).
  - `isUnsaved` computed: `body.value !== savedBody.value`.
  - `.unsaved-dot` rendered with `v-if="isUnsaved"`.
  - Keyboard shortcut: `onKeydown` listener as shown in F6.
  - `saveNote()` function: calls `save_note({slug, body: body.value})`, then
    sets `savedBody.value = body.value`.
  - Layout: `position: relative` wrapper (for unsaved-dot anchor) → editor
    content with 96px top / 64px horizontal padding.
  - Title display: `<h1>{{ title }}</h1>` in italic serif at top of editor.
  - Editor: `<textarea>` bound to `body` with `v-model`. Full-width,
    transparent background, no border, no resize handle, `font-family:
    var(--font-sans)`, 18px / 1.6, matching body styles. Height: `100%` of
    available space.
  - Preview toggle: a small link "preview" above the textarea; clicking shows
    `<div v-html="renderedBody" class="preview">` instead. Back to edit on
    another click. `renderedBody` is a computed ref using `marked.parse(body.value)`.
  - Back navigation: a `<a href="#/">← Notes</a>` link at the top of the
    editor pane.
  - Delete: a `delete` text link (small, `--ink-soft` colour). On click: calls
    `delete_note({slug})`, then navigates to `/`.

- `examples/notes/ui/src/views/AboutView.vue` — route `/about`:
  - Minimal layout: centred content with 120px top padding.
  - `<h1>Notes</h1>` in serif italic.
  - Two short paragraphs of help text: keyboard shortcuts (Ctrl/Cmd+S to save),
    file storage location note.
  - `<a href="#/">← Back</a>` link.

- `examples/notes/ui/src/main.ts` (updated):
  ```typescript
  import { createApp } from 'vue'
  import App from './App.vue'
  import router from './router'
  import './assets/fonts.css'
  import './assets/main.css'

  createApp(App).use(router).mount('#app')
  ```

**CSS budget check:**
- `main.css` hand-crafted: estimated ~3–5 KB minified.
- `marked` ~26 KB gzipped (bundled into JS asset by Vite, not CSS budget).
- No CSS framework. Total CSS well under 50 KB gzipped. NFR-EX-3 met.

**Exercise:**
```bash
cd /home/anl/picolet/examples/notes
npm install --prefer-offline
npm run typecheck    # vue-tsc --noEmit must exit 0
npm run build
# Verify dist/ structure: index.html, assets/*.js, assets/*.css, fonts/*.woff2
ls dist/fonts/   # SourceSerif4*.woff2  SourceSans3*.woff2
```

---

#### Chunk 4 — Integration: build, run, manual smoke

**Goal**: Produce a working binary. Manual visual check confirms the
editorial aesthetic is correct.

**Steps:**

1. `cd /home/anl/picolet/examples/notes && picolet build --no-sbom`.
2. Binary size check: `wc -c target/linux-x64/notes` ≤ 3145728.
3. Run: `PICOLET_NOTES_DIR=/tmp/notes-smoke ./target/linux-x64/notes`. Window
   must open within 1500 ms showing the empty note list with "No notes yet."
4. Create a note via the UI. Verify the `.md` file appears in
   `/tmp/notes-smoke/`.
5. Edit the note — red dot appears; Ctrl+S saves — dot disappears.
6. Reopen the note (navigate away and back) — saved body is shown.
7. Delete the note — navigated back to list; file gone from `/tmp/notes-smoke/`.

**Manual aesthetic checklist** (before proceeding to Chunk 5):
- [ ] Background is warm off-white `#f7f3ed`.
- [ ] Body text is Source Sans 3 at 18px.
- [ ] Note titles in list are Source Serif 4.
- [ ] h1 in editor is italic serif at 42px.
- [ ] Unsaved dot is 8px red (`#c4392b`), top-left corner.
- [ ] No other red anywhere in the UI.
- [ ] Paper-grain texture is visible on close inspection (0.5% opacity).
- [ ] No rounded corners on list items or inputs.

---

#### Chunk 5 — Playwright integration tests (host FS assertions)

**Goal**: `examples/notes/tests/` with a Playwright test that exercises the
full CRUD flow and asserts host filesystem state at each step.

**Pattern reference:**
- `examples/pydfu/tests/conftest.py` — AppHarness fixture pattern.
- `examples/pydfu/tests/test_flash_flow.py` — wait_for_selector + interact pattern.

**Files to create:**

- `examples/notes/tests/conftest.py`:

  ```python
  """conftest.py — pytest fixtures for notes integration tests.

  Uses PICOLET_NOTES_DIR override to isolate each test in a temp directory.
  No mock is needed for the storage layer — the real notes_store.py runs
  against the temp directory so host FS state can be asserted directly.
  """
  import pytest
  from pathlib import Path
  from picolet.testing import AppHarness

  BINARY = Path(__file__).parent.parent / "target" / "linux-x64" / "notes"


  @pytest.fixture
  async def notes_dir(tmp_path):
      d = tmp_path / "notes"
      d.mkdir()
      return d


  @pytest.fixture
  async def harness(notes_dir):
      h = AppHarness(
          str(BINARY),
          env={"PICOLET_NOTES_DIR": str(notes_dir)},
      )
      await h.start()
      yield h
      await h.stop()
  ```

- `examples/notes/tests/test_notes_flow.py`:

  ```python
  """Integration test: create → edit → save → reopen → delete, with FS assertions."""
  import asyncio, pytest
  from pathlib import Path
  pytestmark = pytest.mark.asyncio


  async def test_create_note_appears_on_fs(harness, notes_dir):
      page = harness.page
      # List view — empty
      await page.wait_for_selector(".note-list-empty", timeout=5000)
      # Create a note
      await page.click(".btn-new-note")
      # Navigated to edit route; title is "Untitled"
      await page.wait_for_selector(".editor-pane", timeout=5000)
      # One .md file should exist in notes_dir
      await asyncio.sleep(0.3)   # allow async command to complete
      md_files = list(notes_dir.glob("*.md"))
      assert len(md_files) == 1, f"expected 1 .md file, got {md_files}"


  async def test_edit_and_save(harness, notes_dir):
      page = harness.page
      # Create
      await page.wait_for_selector(".note-list-empty", timeout=5000)
      await page.click(".btn-new-note")
      await page.wait_for_selector(".editor-pane", timeout=5000)
      # Type content — unsaved dot should appear
      textarea = page.locator("textarea.note-body")
      await textarea.fill("# My test note\n\nSome content.")
      dot = page.locator(".unsaved-dot")
      await dot.wait_for(state="visible", timeout=2000)
      # Ctrl+S to save — dot should disappear
      await page.keyboard.press("Control+s")
      await dot.wait_for(state="hidden", timeout=2000)
      # FS: file contains the typed body
      await asyncio.sleep(0.2)
      md = list(notes_dir.glob("*.md"))[0]
      content = md.read_text(encoding="utf-8")
      assert "My test note" in content
      assert "Some content." in content


  async def test_reopen_shows_saved_body(harness, notes_dir):
      page = harness.page
      # Create + save
      await page.wait_for_selector(".note-list-empty", timeout=5000)
      await page.click(".btn-new-note")
      await page.wait_for_selector(".editor-pane", timeout=5000)
      await page.locator("textarea.note-body").fill("Persisted body text.")
      await page.keyboard.press("Control+s")
      # Navigate to list
      await page.click("a.back-to-list")
      await page.wait_for_selector(".note-item", timeout=5000)
      # Click the note to reopen
      await page.click(".note-item")
      await page.wait_for_selector(".editor-pane", timeout=5000)
      body_val = await page.locator("textarea.note-body").input_value()
      assert "Persisted body text." in body_val


  async def test_delete_note(harness, notes_dir):
      page = harness.page
      # Create
      await page.wait_for_selector(".note-list-empty", timeout=5000)
      await page.click(".btn-new-note")
      await page.wait_for_selector(".editor-pane", timeout=5000)
      await asyncio.sleep(0.3)
      md_files = list(notes_dir.glob("*.md"))
      assert len(md_files) == 1
      slug_file = md_files[0]
      # Delete
      await page.click(".btn-delete-note")
      # Navigated back to list; file gone
      await page.wait_for_selector(".note-list-empty", timeout=5000)
      await asyncio.sleep(0.2)
      assert not slug_file.exists(), f"expected {slug_file} to be deleted"
  ```

- `examples/notes/tests/pytest.ini`:
  ```ini
  [pytest]
  asyncio_mode = auto
  ```

**Exercise:**
```bash
cd /home/anl/picolet/examples/notes
# binary must exist (Chunk 4)
uv run --with pytest --with pytest-asyncio pytest tests/ -v
# All four tests must pass
```

---

#### Chunk 6 — `init_cmd` template wiring (`--template notes`)

**Goal**: `picolet init <name> --template notes` scaffolds a buildable copy
of the notes app with `{{name}}` substituted.

**Pattern reference:**
- `packages/picolet/picolet/init_cmd.py` — `_KNOWN_TEMPLATES` list,
  `_copy_template` function, `{{name}}` substitution rules.
- PH19 added `"pydfu"` to `_KNOWN_TEMPLATES` using the same mechanism.

**Files to create:**

- `packages/picolet/picolet/templates/notes/` — structurally identical
  to `examples/notes/` with `{{name}}` substitutions:
  - `picolet.toml`: `name = "{{name}}"`, window `title = "{{name}}"`.
  - `package.json`: `"name": "{{name}}"`.
  - `ui/index.html`: `<title>{{name}}</title>`.
  - `src/main.py`: header comment `# {{name}} — markdown notes app`.
  - `src/notes_store.py`: the `_notes_dir()` function uses `"notes"` as the
    subdirectory name by default. For templated copies, the `app_name` arg
    should default to `"{{name}}"` so the scaffolded app stores under its own
    name. Specifically: the template version passes the `picolet.toml [app] name`
    to `_notes_dir()`. In the fixed `examples/notes/` copy, `"notes"` is
    hardcoded; in the template copy, use `"{{name}}"`.
  - `.vue` files: any visible app-name text (e.g. heading in `AboutView.vue`).
  - Font woff2 files: byte-copied verbatim (`.woff2` not in `_TEXT_EXTENSIONS`).

**Files to modify:**

- `packages/picolet/picolet/init_cmd.py`:
  - Add `"notes"` to `_KNOWN_TEMPLATES`.
  - Update help string to include `"notes"` in the listed templates.

**Exercise:**
```bash
cd /tmp
picolet init my-notes-app --template notes
cd my-notes-app
picolet validate                       # must exit 0
npm install --prefer-offline
picolet build --no-sbom                # must produce target/linux-x64/my-notes-app
```

---

#### Chunk 7 — Screenshots (`screenshots/` directory)

**Goal**: `examples/notes/screenshots/` with all six required PNGs, generated
via `examples/notes/scripts/generate_screenshots.py` using the PH19 Playwright
headless Chromium pattern.

**Pattern reference:**
- `examples/pydfu/scripts/generate_screenshots.py` — the complete approach:
  local HTTP server for `dist/`, Playwright headless Chromium, mock
  `window.picolet` injected as init script, animation disable init script,
  pixel verification.

**Required screenshots:**

| Filename | Route / state | Mock data |
|---|---|---|
| `list-empty.png` | `/` — no notes | `list_notes` returns `[]` |
| `list-populated.png` | `/` — three notes visible | `list_notes` returns 3-note fixture |
| `edit-pristine.png` | `/edit/:slug` — note loaded, no changes | `load_note` returns fixture body; `isUnsaved` false |
| `edit-unsaved.png` | `/edit/:slug` — body modified, dot visible | After `textarea.fill(...)`, before save |
| `edit-typing-mid.png` | `/edit/:slug` — mid-typing state, cursor visible | Same state as `edit-unsaved` but different body content |
| `search-active.png` | `/` — search input has text, filtered results | `list_notes` returns 3-note fixture; query typed |

**Mock fixture (deterministic):**

```python
_MOCK_NOTES = [
    {"slug": "meeting-notes-1747000000", "title": "Meeting Notes",
     "created": 1747000000, "updated": 1747086400},
    {"slug": "weekend-reading-1746913600", "title": "Weekend Reading",
     "created": 1746913600, "updated": 1746999000},
    {"slug": "project-ideas-1746827200", "title": "Project Ideas",
     "created": 1746827200, "updated": 1746827200},
]

_MOCK_BODY = """# Meeting Notes

Discussion points from the Tuesday sync.

## Decisions

- Proceed with the new storage layout.
- Review scheduled for end of week.

## Action items

1. Draft the updated spec section.
2. Run the benchmark suite.
3. Send the summary to the team.
"""
```

**`window.picolet` mock JS for notes:**

```javascript
(function() {
  const _notes = <NOTES_JSON>;
  const _body = <BODY_JSON>;
  const _handlers = {};

  window.picolet = {
    __ready__: true,
    invoke: async function(cmd, args) {
      if (cmd === 'list_notes') return _notes;
      if (cmd === 'load_note') return {
        slug: (args && args.slug) || _notes[0].slug,
        title: _notes[0].title,
        created: _notes[0].created,
        updated: _notes[0].updated,
        body: _body,
      };
      if (cmd === 'save_note') return { ok: true,
        slug: args.slug, title: _notes[0].title,
        created: _notes[0].created, updated: Date.now() / 1000 | 0 };
      if (cmd === 'create_note') return {
        slug: 'new-note-1747100000', title: args.title || 'Untitled',
        created: 1747100000, updated: 1747100000 };
      if (cmd === 'delete_note') return { ok: true };
      throw new Error('unknown command: ' + cmd);
    },
    on: function(event, handler) {
      if (!_handlers[event]) _handlers[event] = [];
      _handlers[event].push(handler);
      return function() {
        _handlers[event] = (_handlers[event] || []).filter(h => h !== handler);
      };
    },
    emit: function(t, d) {
      (_handlers[t] || []).forEach(h => { try { h(d); } catch(e) {} });
    },
    _drainPending: function() {},
  };
})();
```

**Pixel verification for notes:**

```python
def _check_screenshot_notes(path: Path, expect_red: bool = False) -> None:
    from PIL import Image
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC
    img = Image.open(path).convert("RGB")
    w, h = img.size
    assert w >= 1000 and h >= 700, f"{path.name}: {w}x{h} below 1000x700"
    pixels = list(img.getdata())
    # Warm off-white paper: #f7f3ed = (247, 243, 237)
    has_paper = any(
        abs(r-247)<=15 and abs(g-243)<=15 and abs(b-237)<=15
        for r,g,b in pixels)
    assert has_paper, f"{path.name}: no paper-colour pixels"
    # Near-ink: #1a1715 = (26, 23, 21)
    has_ink = any(r<60 and g<60 and b<60 for r,g,b in pixels)
    assert has_ink, f"{path.name}: no ink-dark pixels"
    if expect_red:
        # Mark red: #c4392b = (196, 57, 43)
        has_red = any(
            abs(r-196)<=30 and abs(g-57)<=30 and abs(b-43)<=30
            for r,g,b in pixels)
        assert has_red, f"{path.name}: no mark-red pixels (unsaved dot expected)"
```

`edit-unsaved` and `edit-typing-mid` are verified with `expect_red=True`.

**Files to create:**

- `examples/notes/scripts/generate_screenshots.py` — full script following the
  PH19 structure. Six screenshot capture blocks, one per state, each in a fresh
  browser context. Pixel verification called for each PNG after capture.
- `examples/notes/screenshots/` — directory; populated by running the script.

**Exercise:**
```bash
cd /home/anl/picolet/examples/notes
uv run scripts/generate_screenshots.py
# Six PNGs must appear in screenshots/
# Verify: python3 -c "from PIL import Image; [Image.open(f).verify() for f in __import__('glob').glob('examples/notes/screenshots/*.png')]"
```

---

#### Chunk 8 — Phase tests and exit gate

**Goal**: `tests/phase-20/run.sh` exercises all FR-EX and NFR-EX gates.

**Files to create:**

- `tests/phase-20/run.sh`:

| Gate | Proves | Command |
|---|---|---|
| A | FR-EX-2 scaffold: `picolet validate` exits 0 | `cd examples/notes && picolet validate` |
| B | FR-EX-2 build: binary produced | `picolet build --no-sbom` → `target/linux-x64/notes` exists |
| C | NFR-EX-1 size: binary ≤ 3 MiB | `wc -c target/linux-x64/notes` ≤ 3145728 |
| D | NFR-EX-4 no CDN: no external URL refs in binary | `strings notes \| grep -cE "cdn\.|unpkg\.|jsdelivr\."` = 0 |
| E | NFR-EX-2 startup ≤ 1500 ms | AppHarness `time_to_ready` assertion |
| F | FR-EX-2 list_notes IPC: round-trip returns list | `smoke_list_notes.py` exits 0 |
| G | FR-EX-2 create+save+load cycle: FS write verified | `smoke_crud.py` exits 0 |
| H | FR-EX-6 screenshots: six PNGs present + valid | PIL verify loop |
| I | FR-EX-5 tests: Playwright suite passes | `pytest examples/notes/tests/ -v` exits 0 |
| J | NFR-EX-3 CSS ≤ 50 KB gzip | `gzip -c dist/assets/*.css | wc -c` ≤ 51200 |
| K | NFR-EX-AESTHETIC fonts: Source Serif 4 referenced | `strings notes \| grep -q "Source Serif"` |
| L | FR-EX-2 template: `picolet init --template notes` scaffolds + builds | `picolet init` in tempdir + build |

- `tests/phase-20/smoke_list_notes.py`:

  ```python
  # /// script
  # requires-python = ">=3.11"
  # dependencies = ["picolet-cli"]
  # ///
  """Smoke: list_notes IPC round-trip returns a list."""
  import asyncio, os, tempfile
  from pathlib import Path
  from picolet.testing import AppHarness

  BINARY = Path(__file__).parent.parent.parent / "examples/notes/target/linux-x64/notes"

  async def main():
      with tempfile.TemporaryDirectory() as tmp:
          async with AppHarness(str(BINARY), env={"PICOLET_NOTES_DIR": tmp}) as h:
              result = await h.page.evaluate("window.picolet.invoke('list_notes')")
              assert isinstance(result, list), f"expected list, got {type(result)}"
              print(f"list_notes: OK ({len(result)} notes)")

  asyncio.run(main())
  ```

- `tests/phase-20/smoke_crud.py`:

  ```python
  # /// script
  # requires-python = ">=3.11"
  # dependencies = ["picolet-cli"]
  # ///
  """Smoke: create → save → load → delete cycle with FS verification."""
  import asyncio, tempfile
  from pathlib import Path
  from picolet.testing import AppHarness

  BINARY = Path(__file__).parent.parent.parent / "examples/notes/target/linux-x64/notes"

  async def main():
      with tempfile.TemporaryDirectory() as tmp:
          tmp_path = Path(tmp)
          async with AppHarness(str(BINARY), env={"PICOLET_NOTES_DIR": tmp}) as h:
              note = await h.page.evaluate(
                  "window.picolet.invoke('create_note', {title: 'Smoke Test'})"
              )
              slug = note["slug"]
              assert slug, "create_note returned no slug"
              # FS check
              assert (tmp_path / f"{slug}.md").exists()
              # Save
              await h.page.evaluate(
                  f"window.picolet.invoke('save_note', {{slug: '{slug}', body: '# Test'}})"
              )
              content = (tmp_path / f"{slug}.md").read_text()
              assert "# Test" in content
              # Delete
              await h.page.evaluate(
                  f"window.picolet.invoke('delete_note', {{slug: '{slug}'}})"
              )
              assert not (tmp_path / f"{slug}.md").exists()
              print("CRUD cycle: OK")

  asyncio.run(main())
  ```

**Exercise:**
```bash
cd /home/anl/picolet
bash tests/phase-20/run.sh --verbose
# All gates PASS or SKIP (no FAIL)
```

---

### Open questions

**O1 — Narrow-screen "drawer" vs. route navigation.**
The spec says "single-column with a drawer on narrow". The plan uses route
navigation (navigating to `/edit/:slug` hides the list on narrow). A drawer
component would require additional state management and a slide animation that
conflicts with the "no motion" aesthetic principle. The route-navigation
approach is cleaner and consistent with the editorial feel. If a slide-out
drawer is required, flag at implementation time; it adds ~30 min of work.

**O2 — Title editing in the editor.**
The spec does not explicitly define how the note title is edited. The plan
renders the title as a read-only `<h1>` in the editor. An alternative is to
make the `<h1>` a `contenteditable` element; a third option is a separate
`<input>` above the textarea. Decision needed before implementation of
`EditView.vue`. The simplest approach (contenteditable `<h1>`) is recommended
— it preserves the editorial aesthetic without breaking the visual hierarchy
with an extra input field.

**O3 — `re` module availability in MicroPython.**
`notes_store.py` uses `re.sub(...)` for slug generation. MicroPython includes
a `re` module but with a reduced feature set (`ure` / `re` depending on build
config). The patterns used (`[^a-z0-9 ]` and ` +`) are basic enough to work
with MicroPython `re`. Verify in the runtime with `import re; re.sub("[^a-z0-9 ]",
"", "test-123!")`. If unavailable, replace with a pure-Python character-loop
fallback (straightforward, ~10 lines).

**O4 — Variable font woff2 filenames with brackets.**
The canonical filenames for variable fonts include axis tags in brackets:
`SourceSerif4[opsz,wght].woff2`. Filesystems and shell scripts handle this
without issue, but some Vite versions may have trouble with `[` and `]` in
asset filenames inside `public/`. If Vite's asset handling breaks, rename the
files to `SourceSerif4-Variable.woff2` and update the `@font-face` `src` URL
accordingly. Monitor during Chunk 3 build.

---

### Exit gate

A successful PH20 has all of the following true, verified by
`bash tests/phase-20/run.sh` exiting 0:

| Gate | Proves |
|---|---|
| A | FR-EX-2 scaffold: `picolet validate` exits 0 |
| B | FR-EX-2 build: binary at `target/linux-x64/notes` |
| C | NFR-EX-1: binary ≤ 3 MiB |
| D | NFR-EX-4: no CDN references in binary |
| E | NFR-EX-2: startup ≤ 1500 ms |
| F | FR-EX-2 list_notes IPC: exits 0 |
| G | FR-EX-2 CRUD cycle: FS write + delete verified |
| H | FR-EX-6: six screenshots present + valid PNG, each > 1 KB |
| I | FR-EX-5: `pytest examples/notes/tests/` exits 0 |
| J | NFR-EX-3: CSS asset ≤ 50 KB gzipped |
| K | NFR-EX-AESTHETIC: Source Serif 4 in binary strings |
| L | FR-EX-2 template: `picolet init --template notes` scaffolds + builds |

NFR-EX-AESTHETIC is additionally human-judged. Gate K (font presence) is the
automated proxy; a human reviewer must confirm the screenshot is editorially
distinctive before the tester marks PH20 PASS.

---

### Risks / footguns

**R1 — `re` module in MicroPython (see O3).**
Low risk given the simple patterns used, but verify early in Chunk 2.

**R2 — Variable font woff2 filenames with bracket characters (see O4).**
Medium risk. Monitor during `npm run build` in Chunk 3. Easy mitigation:
rename files.

**R3 — Ctrl+S swallowed by WebKitGTK before Vue sees it.**
WebKitGTK may intercept Ctrl+S at the browser level (save-as in non-headless
mode). Test during Chunk 4 smoke. If intercepted, override via WebKit
Inspector Protocol or use a different key combination. The WebView2 path is
unlikely to have this issue. Mitigation: listen on the `window` level and
call `e.preventDefault()` as the first action in the handler (already in the
plan); WebKitGTK should defer to the page handler if `preventDefault` is
called synchronously.

**R4 — `marked` version API drift.**
`marked@14` has a different API surface than `marked@4` (the function is still
`marked.parse(str)` in v14, but options are passed differently). Pin to
`marked@^14` in `package.json` and verify that `import { marked } from
'marked'; marked.parse("# test")` returns a string in the build.

**R5 — Slug collision on rapid creation.**
The slug includes the Unix timestamp in seconds. Creating two notes in the
same second with the same title produces a collision. The `create_note` function
handles this with a counter suffix. This is a very edge case in a single-user
app but must be tested in unit-level Python tests (not Playwright — too fast
to hit in normal use).

**R6 — `font-display: block` and empty-state first render.**
Same footgun as PH19 R5: if WebKit introduces async latency in serving fonts
from `picolet://`, the first rendered frame may show invisible text. Mitigation
is identical: if observed, switch to `font-display: swap` and add a 200 ms
settle delay in screenshot scripts after `wait_for_selector`.

**R7 — Screenshot determinism with timestamp display.**
If the note list shows relative timestamps ("2 hours ago") based on the
current wall-clock time, the pixel output changes every run. Mitigation: the
mock fixture uses fixed Unix timestamps, and the Vue component formats them
as absolute dates (ISO or locale date only, no relative "N minutes ago").
Relative timestamps would require mocking `Date.now()` in the screenshot
scripts to keep determinism.

---

### Model tier recommendations

| Role | v1.1-plan default | Recommended | Rationale |
|---|---|---|---|
| planner | opus | **sonnet** (this artefact) | App-building work on a well-established baseline. PH19 resolved the structural unknowns; PH20 introduces no novel framework issues. |
| developer | sonnet | **sonnet** | The storage layer is pure Python pathlib — no ffi, no USB. The Vue frontend is straightforward Composition API. The one risk area is the `re` module in MicroPython (O3); if that requires a fallback implementation, it remains within sonnet capability. |
| sqe | sonnet | **sonnet** | Test patterns are directly inherited from PH19. |
| tester | sonnet | **opus** | The NFR-EX-AESTHETIC gate requires a human design judgement about whether the editorial aesthetic is genuinely distinctive and memorable. The typographic precision (42px italic serif h1, warm off-white palette, no red anywhere except the unsaved dot) is subtle enough to require a careful eye. Keep at opus for the final visual sign-off. |
