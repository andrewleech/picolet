# PH18 — Vue 3 + Vite toolchain integration

## Plan

### Goal

Make Vue 3 + Vite + TypeScript a first-class frontend toolchain inside
`picolet build` and `picolet dev`. Deliver `examples/with-vue/` as the
canonical baseline that PH19–PH22 each copy and extend.

This phase is entirely additive. The vanilla template path (`framework =
"vanilla"` default, or absent `[ui.frontend]`) is unchanged — no
existing app breaks.

---

### Spec coverage

| Spec id | Requirement | Where in this phase |
|---|---|---|
| FR-VUE-1 | `picolet init <name> --template hello-vue` produces a working Vue 3 + Vite + TypeScript skeleton that builds via `picolet build` | Chunk 4 (template), Chunk 5 (init_cmd wiring) |
| FR-VUE-2 | `picolet dev` against a Vue app runs the Vite dev server alongside the watcher; webview loads from `http://localhost:5173/` during dev, from `/rom/ui/` after build | Chunk 3 (dev_cmd extension) |
| FR-VUE-3 | `picolet-bridge-js` ships a TypeScript declaration file (`picolet.d.ts`) so Vue/TS apps get typed `window.picolet` | Chunk 1 (picolet.d.ts) |
| FR-VUE-4 | `picolet build` detects a Vue project, runs `npm run build`, packs `dist/` into romfs — no extra subcommand | Chunk 2 (build_cmd extension) |
| FR-VUE-5 | `picolet.toml` gains `[ui.frontend]` table: `framework`, `build_cmd`, `dist_dir`, `dev_url`; default `framework = "vanilla"` | Chunk 2 (build_cmd reads it), Chunk 3 (dev_cmd reads it), Chunk 5 (validator), plus every template that ships it |

---

### Dependencies

#### From v1 and PH17 (already landed)

- `build_cmd._do_build` pipeline at
  `packages/picolet/picolet/build_cmd.py:175` — steps 1–10 are
  the existing pipeline; PH18 inserts a new step after step 4 and
  before step 5 to run the npm frontend build when `[ui.frontend]`
  specifies a non-vanilla framework.
- `build_cmd._copy_includes` at `build_cmd.py:684` — handles
  `[romfs] include` copying. Vue's `dist/` is packed differently:
  PH18 adds `_copy_dist_to_ui_root` which mirrors `dist/` contents
  into the `romfs_root / ui_root` path (where `ui_root` is
  `data["ui"]["root"]`, defaulting to `"ui"`). This replaces the
  manual `[romfs] include = ["ui"]` that vanilla apps use.
- `build_cmd._emit_webview_toml` at `build_cmd.py:569` — already
  emits `[ui]` including `root` and `index` into the romfs
  `picolet.toml`. No change needed; the emitted values correctly point
  the runtime at the packed Vue assets.
- `dev_cmd.run` at `packages/picolet/picolet/dev_cmd.py:65` —
  the main loop. PH18 spawns a Vite child process alongside the
  existing watcher loop and cleans it up on exit via the same
  `_kill_child` pattern (extended to a process group to kill Vite's
  child processes too).
- `dev_cmd._Watcher` at `dev_cmd.py:157` — polls `src/`, `picolet.toml`,
  and `ui_root/`. For Vue apps the `ui_root` watch is harmless (the
  watcher ignores `node_modules` via `_IGNORE_DIRS`, which PH18
  extends) and Vue source lives under `ui/src/` which is watched.
  Python-side changes still trigger the full rebuild; JS-side changes
  are handled by Vite HMR without a rebuild.
- `_paths.py:_IGNORE_DIRS` at `packages/picolet/picolet/_paths.py:21`
  — currently `{"target", ".picolet-cache", "__pycache__"}`. PH18 adds
  `"node_modules"` and `"dist"` so the watcher never crawls those trees.
- `validator.py:_ALLOWED_SECTIONS` and `_UI_SCHEMA` at
  `packages/picolet/picolet/validator.py:27, 41` — PH18 adds the
  `[ui.frontend]` sub-table schema. Validator currently processes
  `[ui]` as a flat table; `[ui.frontend]` is a TOML dotted sub-table
  (`[ui.frontend]` → `data["ui"]["frontend"]`), so the validator
  needs a new schema dict and check block inside the `[ui]` handler.
- `init_cmd._KNOWN_TEMPLATES` at
  `packages/picolet/picolet/init_cmd.py:26` — PH18 adds
  `"hello-vue"`.
- `init_cmd._TEXT_EXTENSIONS` at `init_cmd.py:154` — already includes
  `.ts`, `.toml`, `.json`, `.html`. `.vue` files need to be added for
  `{{name}}` substitution in Vue SFCs.
- `packages/picolet-bridge-js/src/index.ts` — already ships the runtime
  bridge. PH18 adds a sibling `picolet.d.ts` that type-declares the same
  surface for TS consumers; the build process must include `picolet.d.ts`
  in the package's output (added to `tsconfig.json`'s `declaration`
  setting, or shipped as a hand-authored `.d.ts`). Hand-authored is
  correct here since the bridge is an IIFE injected at document-start,
  not a module — see Research F3.
- `packages/picolet/` and `AppHarness` from PH17 — used by
  `tests/phase-18/run.sh` to drive the Playwright invoke round-trip.
- `window.picolet.__ready__` flag in `index.ts:200` (PH17) — already
  landed; the PH18 test harness waits on it.

#### What PH19–PH22 consume from PH18

- `examples/with-vue/` — each example starts from this tree. PH19
  copies it and replaces `src/`, `ui/src/`, aesthetic CSS, and Python.
- `picolet-bridge-js/src/picolet.d.ts` — all four examples import it via
  the TS path mapping.
- `[ui.frontend]` validator — each example's `picolet.toml` includes the
  table; the validator must accept it before those phases can pass
  `picolet build`.
- `build_cmd` npm-install + npm-run-build hook — every example relies
  on it.
- `dev_cmd` Vite spawn — every example's dev workflow relies on it.

---

### Key research findings

**F1 — `build_cmd` has no pre-build hook today.** Steps 1–4 are
runtime/toolchain resolution; step 5 is mpy-cross compilation. There is
no existing hook point for a host-side build tool. PH18 inserts a new
step between step 4 and step 5: `_run_frontend_build(data, app_root,
args.verbose)`. This function is a no-op when `[ui.frontend].framework`
is absent or `"vanilla"`.

**F2 — romfs `[ui] root` is the mount point.** The runtime reads
`picolet.toml`'s `[ui] root` and serves that directory. For vanilla apps
the user manually writes `[romfs] include = ["ui"]`. For Vue apps the
built `dist/` must be placed at `romfs_root / ui_root` so the runtime's
existing `_webview.py` path `load_uri("picolet:///ui/index.html")` (or
equivalent) continues to work. PH18's `_copy_dist_to_ui_root` handles
this copy; the user does NOT add a `[romfs] include` entry for Vue apps
— the build pipeline handles it automatically.

**F3 — `picolet.d.ts` is hand-authored, not generated.** `picolet-bridge-js`
builds as an IIFE (`format: "iife"`) — there is no ES module export,
so `tsc --declaration` produces nothing meaningful. The correct approach
is a hand-authored `.d.ts` that augments `Window` with the `picolet`
property, placed at `packages/picolet-bridge-js/src/picolet.d.ts`. Vue apps
reference it via a TS path mapping in their `tsconfig.json`. The
declaration does not need to be built — it is shipped as source and
referenced directly.

**F4 — `dev_cmd` child process management.** The existing `_kill_child`
uses `child.send_signal(signal.SIGTERM)`. Vite spawns ESBuild and other
child processes; SIGTERM to the `npm run dev` shell process does not
propagate to Vite's grandchildren on Linux. The fix is to use
`subprocess.Popen(..., start_new_session=True)` (which creates a new
process group) and then `os.killpg(os.getpgid(vite_proc.pid), signal.SIGTERM)`
on cleanup. The existing app child does not need this change — it is a
single binary.

**F5 — `dev_cmd` webview URL at dev time.** During `picolet dev` the
binary is re-built and re-launched on each Python change. When
`[ui.frontend].framework = "vue"`, the webview's `load_uri` call must
point at `dev_url` (`http://localhost:5173/`) rather than the romfs
`picolet://` scheme. The runtime reads its URL from the romfs `picolet.toml`
— so the dev-time picolet.toml must be patched to include a `[ui] dev_url`
key, and `_webview.py` must be taught to use it when present. This is
the most invasive touch in the phase — it requires a 2-line change in
`picolet_ui._webview.py` (frozen Python, not the CLI). Alternative: pass
the dev URL via an environment variable read by the runtime. The env-var
approach is cleaner because it avoids modifying the frozen runtime's
romfs picolet.toml format and is orthogonal to the schema. Decision: use
`PICOLET_DEV_URL` env var. The dev_cmd sets it in the child's environment;
`_webview.py` reads `os.getenv("PICOLET_DEV_URL")` and calls `load_uri`
with that value instead of the romfs path. If `PICOLET_DEV_URL` is not
set, behaviour is unchanged. This is a minimal runtime touch; recorded
as Decision D1 below.

**F6 — `node_modules` + `dist` must not be watched.** `_paths.py`
`_IGNORE_DIRS` is checked per path component. `node_modules` and `dist`
must be added or the watcher crawls the entire npm dependency tree and
the `dist/` output on every poll tick, which is unacceptably slow.

**F7 — `_ALLOWED_SECTIONS` in validator.** `[ui.frontend]` is not a
new top-level section; it is a sub-table of `[ui]`. TOML represents
`[ui.frontend]` as `data["ui"]["frontend"]` — a dict inside the `"ui"`
dict. The validator's `_ALLOWED_SECTIONS` check operates on top-level
keys only; no change needed there. The `[ui]` handler in `validate_toml`
needs to check for a `"frontend"` sub-key and validate it against a new
`_UI_FRONTEND_SCHEMA`. The `_UI_SCHEMA` must also accept `"frontend"` as
a known key of dict type (currently any unknown `[ui]` key raises a
warn; `"frontend"` with a dict value would currently produce a type-error
because `_UI_SCHEMA["frontend"]` is absent from the schema dict and the
type-check path emits a warn for unknown keys, not an error — this is
safe but noisy; the fix is to add `"frontend": dict` to `_UI_SCHEMA`).

**F8 — `[ui.frontend]` is not emitted into the romfs picolet.toml.**
`_emit_webview_toml` currently emits `[ui]` with `renderer`, `root`,
`index`. The `frontend` sub-table is host-only build config; it must not
be emitted into the romfs because the frozen runtime does not parse it.
The existing `_emit_webview_toml` only emits explicitly listed keys —
no change needed; `frontend` is simply not in the emit list.

**F9 — `init_cmd._TEXT_EXTENSIONS`** does not include `.vue`. Vue SFCs
(`.vue` files) that contain `{{name}}` in their template must be
substituted. Add `.vue` to `_TEXT_EXTENSIONS`. Note: Vue template syntax
uses `{{ expr }}` (double-brace) which conflicts with `{{name}}`
substitution only when both appear. The `with-vue` template's SFCs
avoid raw `{{name}}` strings in Vue template context; any occurrence of
`{{name}}` in `.vue` files is the Picolet substitution marker. Safe to add.

**F10 — `vite.config.ts` base must be `'./'`.** The picolet webview
runtime serves content via the `picolet://` custom scheme. Vite's default
`base: '/'` produces absolute paths (`/assets/main.js`) which the
scheme handler resolves correctly on Linux (WebKitGTK) but incorrectly
on Windows (WebView2 maps `picolet:///assets/` not `picolet://assets/`).
Setting `base: './'` produces relative paths (`./assets/main.js`)
which work on both.

**F11 — Node version constraint.** Vite 5.x requires Node ≥ 18.0.0.
`vue-tsc` 2.x requires Node ≥ 18.0.0. Document as Node ≥ 18 LTS
(currently Node 20 LTS or 22 LTS). Do not bundle Node; it is a host
build-time dependency only, not shipped in the app binary.

**F12 — `picolet dev` webview can open HTTP URLs.** The `_webview.py`
`load_uri` already accepts arbitrary URIs — it calls
`webkit_web_view_load_uri(view, uri)` which handles `http://` correctly.
WebView2 likewise accepts HTTP URIs in `NavigateToString` / `Navigate`.
No protocol-handler change is needed. The only requirement is that the
dev_cmd sets `PICOLET_DEV_URL=http://localhost:5173/` in the child env
and the runtime reads it.

**F13 — `examples/with-vue/` is the authoritative source.** Per
v1.1-plan.md conventions, `examples/<name>/` is the checked-in source
tree; it is mirrored into `picolet.templates/` by a script in PH23. For
PH18, the `hello-vue` template at
`packages/picolet/picolet/templates/hello-vue/` is maintained
manually in sync with `examples/with-vue/`. PH23 automates this mirror.

---

### Architectural decisions

#### D1 — Dev-time URL via `PICOLET_DEV_URL` env var, not romfs picolet.toml

Two options for telling the runtime to load from Vite's dev server:

| Option | Description | Verdict |
|---|---|---|
| **A: Patch romfs picolet.toml at dev-time** | `dev_cmd` writes a temporary `picolet.toml` with `[ui] dev_url = "http://localhost:5173/"` and the runtime reads it. | **Rejected.** Requires a new romfs picolet.toml key that the runtime must understand, extending the schema the frozen runtime parses. Cross-cuts PH18 into the runtime build. |
| **B: `PICOLET_DEV_URL` env var (chosen)** | `dev_cmd` sets `PICOLET_DEV_URL=http://localhost:5173/` in the child's env. `_webview.py` reads it at startup and redirects `load_uri` accordingly. | **Selected.** Two-line runtime change (one `os.getenv`, one conditional `load_uri`). The env var is never set in production builds. Same pattern as `PICOLET_TEST_MODE`. |

The runtime touch for D1 is small: in
`packages/picolet-runtime/python/picolet_ui/_webview.py`, inside
`Webview.__init__`, after the view is created and before the first
`load_uri`, read `os.getenv("PICOLET_DEV_URL")`. If set, call
`load_uri(dev_url)` instead of the normal romfs path. This affects both
Linux and Windows branches.

#### D2 — npm install is idempotent and always run before build

FR-VUE-4 says "runs `npm install` (idempotent), then `build_cmd`". The
build pipeline runs `npm install --prefer-offline` (not `ci`) before
`npm run build` on every `picolet build`. This is safe: `npm install` is
fast when `node_modules/` already exists and `package-lock.json` is
unchanged. The alternative (`npm ci`) deletes `node_modules/` first,
which is slower. `--prefer-offline` reduces network calls in
development. Both commands are run with `check=True`; failure aborts
the build with a `BuildFailed`.

#### D3 — Vite child in dev_cmd uses process group for clean teardown

`subprocess.Popen(["npm", "run", "dev"], ..., start_new_session=True)`
creates a new session/process group. Teardown: `os.killpg(vite_pgid,
signal.SIGTERM)`, then wait. The existing app-binary child is unchanged
(single process, no grandchildren). The `atexit` handler is extended to
also kill the Vite process group.

#### D4 — `picolet.d.ts` augments `Window` via module augmentation

The declaration file is hand-authored and uses ambient module
augmentation so it works without an explicit import in user `.vue` files:

```typescript
// packages/picolet-bridge-js/src/picolet.d.ts
export {};
declare global {
  interface Window {
    picolet: PicoletBridge;
  }
  interface PicoletBridge {
    invoke(cmd: string, args?: unknown, opts?: { timeout?: number }): Promise<unknown>;
    on(event: string, handler: (data: unknown) => void): () => void;
    emit(topic: string, data?: unknown): void;
    _drainPending(reason: string): void;
    readonly __ready__: boolean;
  }
}
```

Vue apps reference it via `tsconfig.json` `types` or `typeRoots`, or
via a path mapping in `vite.config.ts`. The template ships a
`tsconfig.json` that includes it via `types: ["picolet-bridge-js"]` —
which resolves because the template's `package.json` declares
`picolet-bridge-js` as a `devDependency` referencing the workspace
package path.

---

### Implementation breakdown

Eight chunks, ordered by topological dependency. Each chunk targets one
or two commits. Total estimated developer effort: 4–6 hours.

---

#### Chunk 1 — `picolet.d.ts` TypeScript declaration (FR-VUE-3)

**Goal**: Ship the typed declaration for `window.picolet` in
`picolet-bridge-js` so Vue/TS apps can use typed `window.picolet` without
any extra import.

**Files to create:**

- `packages/picolet-bridge-js/src/picolet.d.ts` — ambient `Window`
  augmentation (see D4 above). Hand-authored; not generated by tsc.

**Files to modify:**

- `packages/picolet-bridge-js/tsconfig.json` — add
  `"include": ["src/**/*.ts", "src/**/*.d.ts"]` so tsc sees the new
  file during any typecheck pass. The existing include is
  `"src/**/*.ts"` which already matches; no change strictly necessary
  but making it explicit avoids ambiguity. Also set
  `"declaration": true` and `"declarationDir": "dist"` so that if
  a future consumer runs `tsc` on this package, it picks up the `.d.ts`.
  For PH18, the `.d.ts` is referenced directly from the template's
  `tsconfig.json` via a path mapping rather than from the `dist/`; both
  paths are covered.

**Exercise:** `cd packages/picolet-bridge-js && npx tsc --noEmit` exits 0
(no type errors). A Vue test file that writes `window.picolet.invoke("x")`
and `window.picolet.on("y", h)` type-checks without error.

---

#### Chunk 2 — `build_cmd` Vue frontend build hook (FR-VUE-4, FR-VUE-5)

**Goal**: `picolet build` runs `npm install --prefer-offline` then
`npm run build` (or the configured `build_cmd`) when
`[ui.frontend].framework` is `"vue"` (or any non-vanilla value).
Packs `dist/` into romfs at `[ui] root`.

**Files to modify:**

- `packages/picolet/picolet/build_cmd.py`:
  - Add `_run_frontend_build(data, app_root, verbose)` helper. Logic:
    - Read `frontend = data.get("ui", {}).get("frontend", {})`.
    - `framework = frontend.get("framework", "vanilla")`.
    - If `framework == "vanilla"` (or absent): return early (no-op).
    - Verify `node_modules/` exists or run `npm install
      --prefer-offline` first in `app_root`. If `npm` is not on PATH,
      raise `BuildFailed` with a clear message: "npm not found; Node ≥
      18 LTS is required for Vue projects (see docs/architecture.md)".
    - Run `npm install --prefer-offline` in `app_root` (idempotent).
    - Run `build_cmd_str` (`frontend.get("build_cmd", "npm run build")`)
      via `subprocess.run(shlex.split(build_cmd_str), cwd=app_root,
      check=True, capture_output=not verbose)`.
    - If the build command fails, raise `BuildFailed`.
  - Add `_copy_dist_to_ui_root(data, app_root, romfs_root, verbose)`
    helper:
    - `dist_dir = frontend.get("dist_dir", "dist")`.
    - `ui_root = data.get("ui", {}).get("root", "ui")`.
    - `src = app_root / dist_dir`. If not a dir, raise `BuildFailed`.
    - `dst = romfs_root / ui_root`. `shutil.copytree(src, dst,
      dirs_exist_ok=True)`.
  - In `_do_build`, call `_run_frontend_build(data, app_root,
    args.verbose)` after step 4 (runtime resolution) and before step 5
    (mpy-cross compilation). Then, inside the staging block, call
    `_copy_dist_to_ui_root(data, app_root, romfs_root, args.verbose)`
    after step 5 (mpy-cross) and before step 6 (copy includes). Note:
    for Vue apps, `[romfs] include` should NOT include `"ui"` (that
    would double-copy the vanilla static files); the template's
    `picolet.toml` omits `[romfs] include` for Vue apps.

**Exercise:** `cd examples/with-vue && picolet build` produces a binary
with `dist/` assets packed into romfs under `ui/`. `strings <binary> |
grep -c "index.html"` ≥ 1 (the asset is present).

---

#### Chunk 3 — `dev_cmd` Vite dev-server integration (FR-VUE-2)

**Goal**: `picolet dev` against a Vue app spawns Vite alongside the Python
rebuild loop; the webview loads from `http://localhost:5173/` during dev.

**Files to modify:**

- `packages/picolet/picolet/dev_cmd.py`:
  - In `run(args)`, after resolving `toml_path`/`data`, read
    `frontend = data.get("ui", {}).get("frontend", {})`.
    `framework = frontend.get("framework", "vanilla")`.
  - If `framework != "vanilla"`: spawn Vite. The Vite spawn:
    ```python
    dev_url = frontend.get("dev_url", "http://localhost:5173/")
    vite_env = {**os.environ, "FORCE_COLOR": "1"}
    vite_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(app_root),
        env=vite_env,
        start_new_session=True,   # D3: own process group
    )
    ```
    Print `dev: Vite dev server spawned (PID {vite_proc.pid}), loading
    from {dev_url}` to stderr.
  - Set `child_env = {**os.environ, "PICOLET_DEV_URL": dev_url}` and
    pass it to `subprocess.Popen([str(binary_path)], ..., env=child_env)`.
    For vanilla apps `child_env` is not set (or equals `os.environ`).
  - Extend `_kill_child` (or add a parallel `_kill_vite`) to also
    terminate the Vite process group via `os.killpg` + wait. Register
    via `atexit`. The `_Watcher` snapshot logic and rebuild loop are
    unchanged.
  - Add `"node_modules"` and `"dist"` to `_paths.py:_IGNORE_DIRS`
    (one-line change; this file is shared by dev_cmd and build_cmd
    indirectly via watch path collection).

- `packages/picolet/picolet/_paths.py`:
  - `_IGNORE_DIRS = frozenset({"target", ".picolet-cache", "__pycache__",
    "node_modules", "dist"})`.

- `packages/picolet-runtime/python/picolet_ui/_webview.py`:
  - In `Webview.__init__` (both Linux and Windows branches), before
    the `load_uri` call, check `os.getenv("PICOLET_DEV_URL")`. If set,
    use it as the URI. The exact patch point on Linux is after
    `webkit_web_view_new()` and the PICOLET_TEST_MODE block; on Windows
    after `_ensure_environment()`. Both branches share the env-var read
    pattern.

**Exercise:**
1. `cd examples/with-vue && picolet dev` starts both the binary and the
   Vite server. A browser (or `curl`) reaching `http://localhost:5173/`
   returns the Vue app HTML.
2. CTRL-C exits cleanly; `ps aux | grep vite` shows no orphan processes.

---

#### Chunk 4 — `examples/with-vue/` baseline app (FR-VUE-1 partial, FR-VUE-4)

**Goal**: A working Vue 3 + Vite + TypeScript example demonstrating
`picolet.invoke` and `picolet.on` from a Composition-API component.

**Files to create** (all under `examples/with-vue/`):

- `picolet.toml`:
  ```toml
  [app]
  name = "with-vue"
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
  title = "with-vue"
  size = [800, 600]
  resizable = true
  ```

- `src/main.py` — minimal Python side: one `@picolet.command async def
  ping(args)` returning `{"pong": args.get("ts")}`, one
  `@picolet.command async def get_info(args)` returning platform/version
  dict, and a background asyncio task that emits a `ticker:tick` event
  every second with a Unix timestamp. Demonstrates both invoke and
  push-event patterns.

- `ui/` — Vite project root:
  - `index.html` — Vite entry, references `src/main.ts`.
  - `src/main.ts` — Vue `createApp` mounting `App.vue`.
  - `src/App.vue` — Composition-API component with:
    - A button that calls `window.picolet.invoke("ping", { ts: Date.now() })`
      and displays the round-trip result.
    - A button that calls `window.picolet.invoke("get_info")` and displays
      Python platform info.
    - A live counter updated by `window.picolet.on("ticker:tick", ...)`.
    - A clean unsubscribe on `onUnmounted`.
  - `src/env.d.ts` — `/// <reference types="picolet-bridge-js" />` so
    the compiler finds `picolet.d.ts`.
  - `vite.config.ts` — base `'./'`, `@vitejs/plugin-vue`, `resolve.alias`
    mapping `picolet-bridge-js` to the workspace package's `src/picolet.d.ts`.
  - `tsconfig.json` — strict, `"types": []` (resolved via `env.d.ts`
    triple-slash reference), `"paths"` for `picolet-bridge-js`.
  - `package.json` — `name: "with-vue"`, deps: `vue@^3`, devDeps:
    `vite@^5`, `@vitejs/plugin-vue@^5`, `vue-tsc@^2`,
    `typescript@^5`. No CDN dependencies (NFR-EX-4). No component
    library.

- `package-lock.json` — committed (reproducible builds, offline-capable
  with `npm install --prefer-offline`).

**Size constraint check (NFR-EX-1):** Vanilla app binary is ~750 KB.
Vue 3 + Vite + minimal CSS target ≤ ~300 KB gzipped for the JS/CSS
assets. Committed uncompressed into romfs adds ~800 KB. Total binary
≤ 3 MiB. Vite tree-shakes aggressively; the template avoids any runtime
CSS framework (NFR-EX-3, 50 KB gzip limit). Confirm with `wc -c
target/linux-x64/with-vue` after first build; document in the commit.

**Exercise:** `cd examples/with-vue && npm install && picolet build &&
./target/linux-x64/with-vue` — window opens, shows the Vue app.
Clicking "Ping Python" shows a round-trip timestamp; the ticker counter
increments.

---

#### Chunk 5 — `hello-vue` template + `init_cmd` wiring (FR-VUE-1)

**Goal**: `picolet init <name> --template hello-vue` scaffolds a
ready-to-build Vue 3 + Vite skeleton.

**Files to create** (under
`packages/picolet/picolet/templates/hello-vue/`):

- Structurally identical to `examples/with-vue/` with `{{name}}`
  substituted for `"with-vue"` in:
  - `picolet.toml`: `name = "{{name}}"`, `title = "{{name}}"`.
  - `package.json`: `"name": "{{name}}"`.
  - `ui/index.html`: `<title>{{name}}</title>` and `<h1>{{name}}</h1>`.
  - `src/main.py`: comment line `# {{name}} — a minimal Vue picolet app.`
  - `src/App.vue`: heading text `{{name}} demo`.
- `package-lock.json` is NOT included in the template — the user runs
  `npm install` after `picolet init`. (Including it would pin the user to
  exact versions at template-creation time, which may be stale.)

**Files to modify:**

- `packages/picolet/picolet/init_cmd.py`:
  - `_KNOWN_TEMPLATES`: add `"hello-vue"`.
  - `_TEXT_EXTENSIONS`: add `".vue"`.
  - `add_parser`: update the `--template` help string to include
    `"hello-vue"`.

- `packages/picolet/picolet/validator.py`:
  - `_UI_SCHEMA`: add `"frontend": dict` (maps the sub-table key to its
    Python type; the existing `_check_section` will type-check it as a
    dict and not warn "unknown key").
  - Add `_UI_FRONTEND_SCHEMA`:
    ```python
    _UI_FRONTEND_SCHEMA: dict[str, type | tuple[type, ...]] = {
        "framework": str,
        "build_cmd": str,
        "dist_dir":  str,
        "dev_url":   str,
    }
    _UI_FRONTEND_FRAMEWORK_VALUES: frozenset[str] = frozenset(
        {"vanilla", "vue", "react"}
    )
    ```
  - In the `[ui]` handler block of `validate_toml`, after the existing
    `renderer` check, add:
    ```python
    if "frontend" in ui:
        fe = ui["frontend"]
        if not isinstance(fe, dict):
            errors.append(PicoletTomlError(..., reason='"[ui.frontend]" must be a table'))
        else:
            errors.extend(_check_section(file_str, "ui.frontend", fe, _UI_FRONTEND_SCHEMA))
            fw = fe.get("framework")
            if fw is not None and fw not in _UI_FRONTEND_FRAMEWORK_VALUES:
                errors.append(PicoletTomlError(..., reason=f'unknown framework "{fw}"'))
    ```

**Exercise:**
1. `picolet init my-app --template hello-vue` creates `my-app/` with the
   scaffolded structure.
2. `cd my-app && picolet validate` exits 0 with no errors.
3. `cd my-app && npm install && picolet build` succeeds.
4. The produced binary launches and shows the Vue demo UI.

---

#### Chunk 6 — `vite.config.ts` canonical form + `vue-tsc` typecheck (FR-VUE-1)

**Goal**: Document and enforce the canonical `vite.config.ts` shape
required by picolet Vue apps. Add a `vue-tsc --noEmit` typecheck invocation
to the `npm run build` script in both `examples/with-vue/` and the template.

This chunk is partly in the `package.json` scripts and partly in the
`vite.config.ts`. Both are created in Chunk 4; this chunk refines them:

**Files to modify:**

- `examples/with-vue/package.json` and
  `packages/picolet/picolet/templates/hello-vue/package.json`:
  - `"build": "vue-tsc --noEmit && vite build"` — typechecks before
    bundling. Failures abort the picolet build cleanly (non-zero exit
    from `npm run build` → `BuildFailed`).
  - `"dev": "vite"` — unchanged.
  - `"typecheck": "vue-tsc --noEmit"` — available standalone.

- `examples/with-vue/vite.config.ts` (refinements):
  - Confirm `base: './'` is set.
  - `build.outDir: 'dist'` explicit (matches `picolet.toml` `dist_dir`).
  - `build.emptyOutDir: true` — clean on every build (reproducibility).
  - `resolve.alias` pointing `picolet-bridge-js` at the workspace source.

**Exercise:** `cd examples/with-vue && npm run build` runs `vue-tsc
--noEmit` first; any type error in `.vue` SFCs fails the build before
esbuild/Rollup runs.

---

#### Chunk 7 — `docs/architecture.md` frontend toolchains section

**Goal**: Document the Vue toolchain integration for future maintainers
and the PH23 acceptance audit.

**Files to modify:**

- `docs/architecture.md` — append a "Frontend toolchains" section:
  - The `[ui.frontend]` table schema.
  - How `picolet build` detects and invokes the frontend build.
  - The `PICOLET_DEV_URL` env-var contract.
  - Node ≥ 18 LTS as a host build-time dependency (not shipped).
  - The `base: './'` requirement and its rationale (picolet:// scheme
    on both WebKitGTK and WebView2).
  - How `picolet.d.ts` is referenced from a Vue project.
  - The process-group teardown contract for `picolet dev`.

**Exercise:** `grep -c "frontend toolchain" docs/architecture.md` ≥ 1.

---

#### Chunk 8 — Phase tests (FR-VUE-1..5, NFR-EX-1..4)

**Goal**: `tests/phase-18/run.sh` verifies the complete PH18 surface.

**Files to create:**

- `tests/phase-18/run.sh` — modelled on `tests/phase-17/run.sh` (same
  `pass`/`fail`/`skip` helpers):

  | Test | What it proves | Command |
  |---|---|---|
  | A | FR-VUE-5: validator accepts `[ui.frontend]` | `picolet validate` in `examples/with-vue/` exits 0 |
  | B | FR-VUE-5: validator rejects unknown `framework` | inject `framework = "ember"` into a temp toml; `picolet validate` exits non-zero |
  | C | FR-VUE-4: `picolet build` runs npm and packs dist | `cd examples/with-vue && picolet build` exits 0; binary exists |
  | D | NFR-EX-1: binary ≤ 3 MiB | `wc -c target/linux-x64/with-vue` ≤ 3145728 |
  | E | FR-VUE-4: dist assets in binary | `strings target/linux-x64/with-vue | grep -q "index.html"` |
  | F | FR-VUE-1: `picolet init` scaffolds correctly | `picolet init test-vue-app --template hello-vue`; `cd test-vue-app && picolet validate` exits 0; `npm install && picolet build` exits 0 |
  | G | FR-VUE-3: `picolet.d.ts` present | `test -f packages/picolet-bridge-js/src/picolet.d.ts` |
  | H | FR-VUE-3: type declaration is valid TS | `cd packages/picolet-bridge-js && npx tsc --noEmit` exits 0 |
  | I | FR-VUE-2: AppHarness + Playwright invoke round-trip | `picolet test --run tests/phase-18/invoke_roundtrip.py target/linux-x64/with-vue` exits 0 |
  | J | NFR-EX-4: no external CDN references | `strings target/linux-x64/with-vue | grep -qE "cdn\.|unpkg\.|jsdelivr\."` exits non-zero |

- `tests/phase-18/invoke_roundtrip.py` — AppHarness script:
  ```python
  # Run via: picolet test --run <this-file> <binary>
  import asyncio
  from picolet.testing import AppHarness

  async def main():
      async with AppHarness(binary) as h:
          result = await h.page.evaluate(
              "window.picolet.invoke('ping', { ts: 12345 })"
          )
          assert result["pong"] == 12345, f"expected 12345, got {result}"
          print("invoke round-trip: OK")

  asyncio.run(main())
  ```
  (`binary` is injected into the script's globals by `test_cmd` per
  the PH17 `--run` contract.)

**Exercise:** `bash tests/phase-18/run.sh` exits 0 with all gates
passing.

---

### Open questions

**O1 — `PICOLET_DEV_URL` in the webview runtime requires a runtime build.**
The dev_cmd env-var approach (D1) requires touching
`packages/picolet-runtime/python/picolet_ui/_webview.py` — frozen Python
that is compiled into the runtime binary. This means PH18 needs at
least one Linux runtime build to exercise `picolet dev` end-to-end. The
exit gate `tests/phase-18/run.sh` tests `picolet build` and the
Playwright invoke round-trip; if the runtime build is gated behind
`--from-source`, CI may not have Docker. **Confirm with user:** is a
runtime build required in PH18's exit gate, or is the `picolet dev`
E2E test (Vite spawn + webview pointing at `localhost:5173`) optional
for PH18 and deferred to each example phase's own gate?

**O2 — `vue-tsc` in `npm run build` adds ~5–10 s to the build.**
Acceptable for production builds. For tight CI environments, the
`--no-typecheck` escape hatch (via `vite build` directly, bypassing
`vue-tsc`) should be documented. Not a blocker, but worth noting for
PH19–PH22 CI configuration.

**O3 — `package-lock.json` committed in `examples/with-vue/` but not in
the `hello-vue` template.** This means `examples/with-vue/` gets a
pinned, reproducible install, but `picolet init` users get the latest
compatible versions at init time. PH23's mirror script must not copy
`package-lock.json` from examples into templates. Confirm this
convention is acceptable before PH23 planning.

**O4 — `react` as a valid `framework` value.** The spec says
`framework = "vue" | "react" | "vanilla"`. React is out of scope for
v1.1 (spec §Out of scope). The validator should accept `"react"` as a
valid string (no error) to be forward-compatible, but `build_cmd`
should only special-case `"vue"` (and any other non-vanilla) by running
the `build_cmd` string. The pipeline is already framework-agnostic once
npm is invoked; `"react"` would work if the user provides a `build_cmd`
and `dist_dir`. Document this in `architecture.md`.

---

### Exit gate

A successful PH18 has all of the following true, verified by
`bash tests/phase-18/run.sh` exiting 0:

| Check | What it proves |
|---|---|
| Test A | FR-VUE-5 validator accepts `[ui.frontend]` |
| Test B | FR-VUE-5 validator rejects invalid framework value |
| Test C | FR-VUE-4 build pipeline runs npm and packs dist |
| Test D | NFR-EX-1 binary ≤ 3 MiB |
| Test E | FR-VUE-4 dist assets present in binary |
| Test F | FR-VUE-1 `picolet init --template hello-vue` scaffolds a buildable app |
| Test G | FR-VUE-3 `picolet.d.ts` present in picolet-bridge-js |
| Test H | FR-VUE-3 type declaration is valid TS |
| Test I | FR-VUE-2 (partial) + FR-VUE-4: AppHarness `invoke` round-trip against built binary |
| Test J | NFR-EX-4 no CDN references in binary |

Plus: one successful Linux build of `examples/with-vue/` and confirmation
that the `hello-vue` template scaffolds and builds cleanly.

`picolet dev` end-to-end (Vite spawn + webview at localhost) is verified
manually or deferred to PH19's exit gate pending resolution of O1.

---

### Risks / footguns

**R1 — `npm` not on PATH.** The build pipeline must fail clearly when
`npm` is absent. `shutil.which("npm")` check before any `subprocess.run`;
error message includes the Node ≥ 18 LTS requirement and a pointer to
`docs/architecture.md`.

**R2 — `dist/` inside `[romfs] include`.** If a Vue app's `picolet.toml`
accidentally includes `"ui"` in `[romfs] include` AND has the frontend
build hook active, the vanilla HTML (not the Vite output) would be
double-included. The `with-vue` template omits `[romfs] include`
entirely. The validator could detect and warn on this combination; PH18
does not add that check (it is a footgun documentation item in
`architecture.md`).

**R3 — Vite process group on Windows.** `start_new_session=True` /
`os.killpg` are POSIX-only. On Windows `picolet dev` runs the binary
directly (WSL interop or native); Vue dev mode on Windows would need
`CREATE_NEW_PROCESS_GROUP` + `GenerateConsoleCtrlEvent`. Since `picolet
dev` for Vue apps is primarily a Linux developer workflow (and Windows
builds are cross-compiled via dockcross for release), document this
limitation and use a best-effort `vite_proc.terminate()` on Windows
(reached via `sys.platform == "win32"` guard).

**R4 — `base: './'` and Vite's asset hashing.** Vite adds content-hash
suffixes to asset filenames (`assets/main-Cn3VHXS6.js`). The romfs
`index.html` references these hashed filenames. This is correct and
intentional — the full `dist/` is packed, including the hashed assets.
No manifest file is needed on the Python side.

**R5 — npm lockfile reproducibility.** `package-lock.json` is committed
in `examples/with-vue/`. CI must run `npm install --prefer-offline`
(which respects the lockfile) rather than `npm ci` (which is correct
but deletes `node_modules/` first, adding several seconds). If the CI
host is air-gapped, `--prefer-offline` will fail if the cache is cold.
Document in `architecture.md`; the escape hatch is `PICOLET_NPM_ARGS`
env override (not implemented in PH18 — just documented as a future
knob).

**R6 — `vue-tsc` slow in CI.** `vue-tsc --noEmit` type-checks the full
Vue project including all imported types. On a cold run this takes 5–10
s. If PH18 CI shows this as a bottleneck, the `--build` mode of
`vue-tsc` (incremental, uses `tsconfig.tsbuildinfo`) can be used in the
template's `package.json`. Not addressed in PH18; noted for the
developer.

**R7 — `_webview.py` runtime touch requires a runtime build.** See O1.
If the developer cannot rebuild the runtime, the `PICOLET_DEV_URL`
feature cannot be tested end-to-end. The `picolet build` + Playwright
round-trip (Tests C–J) do not require `PICOLET_DEV_URL` — those use the
built binary loading from romfs. Only the interactive `picolet dev`
session requires the env-var read in the runtime.

---

### Model tier recommendations

| Role | v1.1-plan default | Recommended | Rationale |
|---|---|---|---|
| planner | opus | **sonnet** (this artefact) | Primarily toolchain wiring; no novel C/FFI design. The load-bearing decisions are architectural (D1 env-var, D3 process group) but tractable at sonnet tier. |
| developer | sonnet | **sonnet** | CLI extension, template scaffolding, and minimal runtime touch. The runtime touch (two lines in `_webview.py`) is the riskiest item but is syntactically trivial. |
| sqe | sonnet | **sonnet** | Test script authoring against established PH17 patterns. |
| tester | sonnet | **sonnet** | Verification is checklist-driven against the run.sh gates; no novel protocol or timing assertions. |
