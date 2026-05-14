# Picolet v1 Implementation Plan

This plan turns [v1-spec.md](v1-spec.md) into a phased delivery
schedule that an agent loop can drive autonomously. Each phase is a
unit of work with a goal, deliverables, and an exit gate that
references FR / NFR ids from the spec.

## Loop shape

The scrum-style flow per phase:

```
scrum-planner ─▶ scrum-developer ─▶ scrum-sqe ─▶ scrum-tester
                       ↑                              │
                       └────── fix loop on FAIL ──────┘
```

After every phase has passed its tester gate, `scrum-po` performs the
final spec-acceptance audit against [v1-spec.md](v1-spec.md).

## Conventions

- Branch and commit conventions: see [CLAUDE.md](../CLAUDE.md) at the
  repo root.
- Phase artefact lives at `docs/phases/PHASE_NN_<slug>.md` — produced
  by `scrum-planner` and updated by subsequent roles.
- Each phase's exit gate must be verified by `scrum-tester` with both
  a Linux build and a Windows build green (where the phase touches
  buildable code).

## Model tier defaults

Each phase declares the recommended model for each role. The
orchestrator may override. Defaults:

- `opus` — planning, acceptance, complex C, FFI bindings, anything
  cross-cutting.
- `sonnet` — bulk implementation, tests, validation.
- `haiku` — lookups, formatting, trivial scaffolding.

## Critical path

```
PH00 ─▶ PH01 ─▶ PH02 ─▶ PH03 ─▶ PH04 ─┬─▶ PH05  (parallel: artifact dist)
                                      ├─▶ PH13  (parallel: SBOM)
                                      ├─▶ PH06 ─▶ PH07 ─▶ PH08 ─▶ PH09 ─▶ PH10
                                      └─▶ PH11 ─▶ PH12        (LVGL branch)

PH14 ─▶ PH15 ─▶ PH16   (templates, CI release, picolet dev — after the
                        renderer branches are green)
```

## Phases

### PH00 — Verify mbm integration baseline

**Goal**: Confirm the seven inherited PRs rebase cleanly onto current
upstream MicroPython master in WSL, and that the resulting integration
branch still builds the stock unix and windows ports.

**Deliverables**:
- A green run of `scripts/rebuild-integration.sh`.
- A stock `micropython` binary built from the integration branch (unix
  port) executes `print("ok")` correctly.
- A stock `micropython.exe` built via dockcross runs the same under
  WSL interop.

**Exit gate**: both binaries print `ok`; no overlay code added.
**Model tiers**: planner `haiku`, developer `haiku`, sqe `haiku`,
tester `sonnet`.

### PH01 — picolet-runtime-linux-x64-cli

**Goal**: Produce the minimal `cli` runtime variant for Linux.

**Deliverables**:
- `overlay/ports/unix/variants/picolet-cli/{mpconfigvariant.h,.mk}` that
  strips the unix port to the same lean profile as `pydfu`.
- `manifests/manifest_cli.py` baseline frozen manifest (asyncio,
  os-path, json).
- `scripts/build-runtime.sh` builds the variant.
- Output `picolet-runtime-linux-x64-cli` runs a frozen `main.py` from a
  test romfs and exits with the right status.

**Exit gate**: FR-RT-{1,3,4,5,6,7,8}; NFR-1 (≤ 1 MB) holds.
**Model tiers**: planner `opus`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH02 — picolet-cli skeleton

**Goal**: A working `picolet` command with `init`, `--version`, and
`picolet.toml` validation.

**Deliverables**:
- `packages/picolet-cli/picolet/__main__.py` as a PEP 723 uv-runnable
  script.
- TOML schema validation with structured error reporting.
- `picolet init <name> --template hello-cli` scaffolds a working
  directory tree.

**Exit gate**: FR-CLI-{1,2,8}.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH03 — End-to-end build for cli variant on Linux

**Goal**: `picolet build` produces a working binary from a hello-cli
app on Linux, using the locally-built runtime from PH01.

**Deliverables**:
- mpy-cross invocation pipeline in `picolet-cli`.
- romfs image construction.
- Binary concatenation step matching the runtime's expected layout.
- `target/linux-x64/hello-cli` runs and prints the expected output.

**Exit gate**: FR-CLI-3, FR-BP-{1,3,4,5,6}.
**Model tiers**: planner `opus`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH04 — picolet-runtime-windows-x64-cli + Windows build

**Goal**: Mirror PH01–PH03 for Windows. Build the cli runtime via
dockcross MinGW and verify `picolet build --target windows-x64` produces
a working `.exe`.

**Deliverables**:
- `overlay/ports/windows/variants/picolet-cli/{mpconfigvariant.h,.mk}`.
- `scripts/build-runtime.sh` learns the windows target.
- `target/windows-x64/hello-cli.exe` runs under WSL interop.

**Exit gate**: FR-CLI-{3,4}, FR-RT-* on Windows, NFR-1 windows binary.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH05 — Runtime artifact distribution

**Goal**: Define and implement runtime artifact distribution: where
pre-built artifacts live, how `picolet build` resolves and caches them.

**Deliverables**:
- Cache layout under `.picolet-cache/runtime/<tag>/<artifact>`.
- Resolver in `picolet-cli` reading a configured release URL.
- Falls back cleanly when the cache is empty and the network is
  reachable.
- `picolet build --from-source` bypasses the resolver.

**Exit gate**: FR-CLI-5, FR-BP-2.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH06 — picolet_ipc C module + asyncio dispatcher

**Goal**: A C module + Python facade that registers commands and
routes JSON messages to/from a configurable transport.

**Deliverables**:
- `overlay/modules/picolet_ipc/` C source with JSON parser glue.
- `picolet` Python package (frozen) exposing `@picolet.command`,
  `picolet.invoke`, `picolet.emit`, `picolet.on`.
- Stdin/stdout transport for headless tests.
- Round-trip tests with mock messages.

**Exit gate**: FR-IPC-{1,2,3,4,5}.
**Model tiers**: planner `opus`, developer `opus`, sqe `sonnet`,
tester `opus`.

### PH07 — Webview renderer on Linux

**Goal**: `picolet_window` + `picolet_webview` C modules using WebKitGTK
4.1 via libffi. Open a window, load HTML from `/rom/ui/index.html`.

**Deliverables**:
- `overlay/modules/picolet_window/` and `picolet_webview/`.
- `overlay/ports/unix/variants/picolet-webview/`.
- Bridge between webview's `postMessage` and `picolet_ipc`'s transport.
- A no-IPC sanity test: window opens, document loads, page renders.

**Exit gate**: FR-WV-{1,2,3}, FR-RT-2 (webview variant builds), NFR-2.
**Model tiers**: planner `opus`, developer `opus`, sqe `sonnet`,
tester `opus`.

### PH08 — picolet-bridge-js

**Goal**: The JS shim that exposes `window.picolet`. Built as an ES
module + UMD bundle, packed into the webview runtime's romfs at
build time.

**Deliverables**:
- `packages/picolet-bridge-js/src/index.ts` + build (esbuild or vite).
- Bundle automatically included in webview-variant romfs.
- Inject before user JS via the runtime's webview hook.

**Exit gate**: FR-WV-{4,5}.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH09 — End-to-end webview app on Linux

**Goal**: `hello-webview` template builds, runs, and round-trips IPC.

**Deliverables**:
- `packages/picolet-templates/hello-webview/` with a button that calls
  `picolet.invoke('greet', { name: 'World' })`.
- Test harness that drives the webview headlessly (or asserts via
  screenshot / DOM inspection).
- `picolet build --target linux-x64` produces a working binary.

**Exit gate**: FR-IPC-2 across the wire, full webview pipeline.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH10 — Webview renderer on Windows

**Goal**: Mirror PH07 + PH09 on Windows using WebView2.

**Deliverables**:
- WebView2 loader binding via libffi (COM interop layer).
- `overlay/ports/windows/variants/picolet-webview/`.
- Bridge round-trips on Windows.
- `target/windows-x64/hello-webview.exe` runs under WSL interop and
  passes the same end-to-end test.

**Exit gate**: FR-WV-1 (Windows), FR-WV-{2-5} on Windows, NFR-9.
**Model tiers**: planner `opus`, developer `opus`, sqe `sonnet`,
tester `opus`.

### PH11 — LVGL renderer on Linux

**Goal**: LVGL via `lv_binding_micropython` with SDL2 desktop backend.
`hello-lvgl` template runs.

**Deliverables**:
- `lv_binding_micropython` integration (mbm.toml addition or overlay).
- `overlay/ports/unix/variants/picolet-lvgl/`.
- `manifests/manifest_lvgl.py`.
- Test asserts an LVGL window opens and renders a label.

**Exit gate**: FR-LV-{1,2,3,4}, FR-RT-2 (lvgl variant builds), NFR-3.
**Model tiers**: planner `opus`, developer `opus`, sqe `sonnet`,
tester `sonnet`.

### PH12 — LVGL renderer on Windows

**Goal**: Mirror PH11 on Windows. SDL2 Windows backend.

**Deliverables**:
- `overlay/ports/windows/variants/picolet-lvgl/`.
- `hello-lvgl.exe` builds and runs under WSL interop.

**Exit gate**: FR-LV-1 (Windows), FR-RT-2 on Windows, NFR-3.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH13 — SBOM emission

**Goal**: Emit a CycloneDX 1.5 sibling document for every runtime
artifact and every `picolet build` output.

**Deliverables**:
- `packages/picolet-runtime/sbom/runtime.toml` hand-curated source.
- SBOM generator inside `picolet-cli` that walks runtime.toml + mbm.toml
  + app `[dependencies]`.
- Allowlist enforcement per `[sbom]` config; warn / fail behaviour.

**Exit gate**: FR-SBOM-{1,2,3}.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH14 — picolet-templates

**Goal**: Wire all three templates into `picolet init`.

**Deliverables**:
- `packages/picolet-templates/{hello-webview,hello-lvgl,hello-cli}/`.
- `picolet init <name> --template <t>` copies the named template and
  templates `[app] name`.

**Exit gate**: FR-CLI-2 for all three templates.
**Model tiers**: planner `haiku`, developer `sonnet`, sqe `sonnet`,
tester `haiku`.

### PH15 — CI release pipeline

**Goal**: GitHub Actions workflow that on tag pushes builds the 3 × 2
runtime matrix, generates SBOMs, and uploads to GitHub Releases.

**Deliverables**:
- `.github/workflows/release.yml`.
- Build matrix: `{linux-x64, windows-x64} × {webview, lvgl, cli}`.
- Each artifact uploaded with its sibling `.cdx.json`.

**Exit gate**: NFR-7.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

### PH16 — picolet dev

**Goal**: File watcher that triggers `build` + `run` on UI-asset or
Python-source change.

**Deliverables**:
- `picolet dev` subcommand with a watch loop (watchdog).
- Debounce / batching so a flurry of file events triggers one rebuild.
- Existing process is killed cleanly on rebuild.

**Exit gate**: FR-CLI-7.
**Model tiers**: planner `sonnet`, developer `sonnet`, sqe `sonnet`,
tester `sonnet`.

## Acceptance

After PH16 completes its tester gate, `scrum-po` runs the final
spec audit: every FR and NFR in [v1-spec.md](v1-spec.md) gets a
Yes / No verdict with file:line evidence. A No on any requirement
sends control back to the planner for a fix-up phase.
