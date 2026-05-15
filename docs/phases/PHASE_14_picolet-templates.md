# PH14 — picolet-templates

## Plan

### Goal (restated)

Wire all three starter templates into `picolet init`:
- `hello-cli` — minimal print-based IPC app (already exists; polished).
- `hello-webview` — WebKitGTK/WebView2 app (already exists; verified).
- `hello-lvgl` — LVGL SDL2 app (new; mirrors PH11 fixture shape).

`picolet init <name> --template <t>` copies the named template into a new
directory and substitutes `{{name}}` throughout all files.

### Exit gate

FR-CLI-2: all three templates scaffold cleanly via `picolet init`, each
produced app builds with `picolet build`, and the resulting binary runs.

### What each template ships

#### hello-cli

Files:
- `picolet.toml` — `[app]` only; no `[ui]`, no `[window]`.
- `src/main.py` — prints a greeting, registers a minimal IPC handler
  to demonstrate the `@picolet.command` pattern, then exits.

The IPC handler is illustrative (it will only be exercised if the user
later wires a transport peer). On the cli runtime the dispatcher is not
started automatically, so the `@picolet.command` decorator is effectively
a no-op until the user adds transport wiring. The main script does a
straight `print` so the binary is immediately runnable without any
GUI or IPC setup.

#### hello-webview

Already polished (PH09). Keep two buttons (greet + fail_example) as the
IPC round-trip demo is the point of a webview template. No simplification;
two-button shape remains. Verify `{{name}}` substitution covers all files.

#### hello-lvgl

Files:
- `picolet.toml` — `[app]`, `[ui] renderer="lvgl"`, `[window]`, `[romfs]`.
- `src/main.py` — platform-adaptive import (`import lvgl as lv`), creates
  a screen with a "Hello, {{name}}" label and an increment-counter button,
  then calls `_lvgl_run(main=main)` to enter the pump loop.

The template mirrors the PH11 `hello-lvgl-min-e2e` fixture but with
`{{name}}` substituted in the label text and window title, and with a
counter button to demonstrate LVGL widget interaction.

### Changes needed

| File | Change |
|------|--------|
| `packages/picolet-templates/picolet_templates/hello-cli/picolet.toml` | Add `description` key (nice to have; not schema-required) — actually skip this, validator doesn't know description key. Keep as-is. |
| `packages/picolet-templates/picolet_templates/hello-cli/src/main.py` | Add `@picolet.command` pattern example. |
| `packages/picolet-templates/picolet_templates/hello-lvgl/` | Create directory + `picolet.toml` + `src/main.py`. |
| `packages/picolet-cli/picolet/init_cmd.py` | Add `"hello-lvgl"` to `_KNOWN_TEMPLATES`. |
| `tests/phase-14/run.sh` | New test harness: scaffold + build + run for all three templates. |

### Test harness design

`tests/phase-14/run.sh` covers:

- **Group A (scaffold)**: `picolet init test-N --template <t>` for each template.
  Assert the expected files exist and `{{name}}` is substituted.
- **Group B (cli build + run)**: `picolet build --target linux-x64` for hello-cli.
  Assert the binary runs and prints a greeting. Windows: run `.exe` via WSL interop.
- **Group C (webview build)**: `picolet build --target linux-x64` for hello-webview.
  Assert binary exists and contains romfs with picolet.toml and ui/index.html.
  `xvfb-run` integration gate for linux only.
- **Group D (lvgl build + run)**: `picolet build --target linux-x64` for hello-lvgl.
  `xvfb-run` with `timeout 5` for linux. Windows: build-only (`--target windows-x64`).
- **Group E (regression)**: invoke `tests/phase-13/run.sh --skip-build`.

### Notes

- The `hello-cli` template's `@picolet.command` decorator import from `picolet`
  works because `picolet` is frozen into every variant. On the cli runtime there
  is no dispatcher started automatically, so the decorated function will never
  be called unless the user adds a transport. The import itself is always safe.
- `hello-lvgl`'s `src/main.py` uses `from picolet_ui._lvgl import run as _lvgl_run`
  — same pattern as the PH11 fixture. This is intentional: the public
  `picolet_ui.run()` facade dispatches on `[ui] renderer`, but the explicit
  `_lvgl.run` call is clearer for a starter template.
- The `{{name}}` marker in `hello-lvgl/src/main.py` appears in the label text
  (`"Hello, {{name}}"`) so the produced app greets the project name.
