# PH09 — End-to-end webview app on Linux

## Plan

### Goal

Deliver a `hello-webview` starter template that a user gets from
`picolet init <name> --template hello-webview`. The template must build
with `picolet build --target linux-x64`, produce a working binary, and
round-trip an IPC `greet` call end-to-end through the live WebKitGTK
webview under xvfb.

This phase is the integration capstone for the Linux webview pipeline
established by PH06 (IPC dispatcher), PH07 (WebKitGTK renderer), and
PH08 (JS bridge). It does not add new runtime machinery; it wires the
existing pieces into a canonical, user-facing template and verifies them
as a unit.

Spec requirements closed by this phase:

| Spec id | Requirement |
|---|---|
| FR-CLI-2 | `picolet init <name> --template hello-webview` scaffolds a working directory tree. |
| FR-WV-2 | Webview loads its root document from `/rom/<ui.root>/<index>`. |
| FR-WV-3 | Window title and size come from `[window]` in `picolet.toml`. |
| FR-WV-4 | `picolet-bridge-js` script is injected before any user frontend JS runs. |
| FR-WV-5 | Bridge exposes `window.picolet.invoke(cmd, args) → Promise<result>` and `window.picolet.on(event, handler) → unsubscribe`. |
| FR-IPC-2 | `invoke` from a peer returns the command's return value, or raises with the originating exception type and message preserved. |
| FR-IPC-3 | `picolet.emit(topic, data)` from Python pushes an event reachable by `picolet.on(topic, handler)`. |
| FR-BP-1 | `picolet build` resolves the webview variant from `[ui] renderer`. |
| FR-BP-3 | User `.py` sources are compiled to `.mpy` via `mpy-cross`. |
| FR-BP-4 | Romfs image is built from `[romfs] include` dirs plus `.mpy` set plus the bridge-js bundle. |
| FR-BP-5 | Final binary is the runtime with the romfs appended. |

FR-IPC-2 across the wire is the primary exit gate (per v1-plan.md §PH09).

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-IPC-2, FR-WV-{2,3,4,5}, FR-CLI-2, FR-BP-{1,3,4,5}. |
| `/home/anl/picolet/docs/v1-plan.md` §PH09 | Phase scope, deliverables, exit gate. |
| `/home/anl/picolet/CLAUDE.md` | Branch / commit / signing conventions, phase file conventions. |
| `/home/anl/picolet/packages/picolet-templates/picolet_templates/hello-cli/` | Existing template structure and `{{name}}` substitution contract. |
| `/home/anl/picolet/packages/picolet-cli/picolet/init_cmd.py` | Template resolution order, `_KNOWN_TEMPLATES` guard, `_copy_template` substitution mechanics. |
| `/home/anl/picolet/packages/picolet-templates/pyproject.toml` | Package structure; templates live under `picolet_templates/`. |
| `/home/anl/picolet/tests/phase-07/fixtures/hello-webview-min/` | Minimal webview fixture pattern (picolet.toml shape, `picolet_ui.Application` usage). |
| `/home/anl/picolet/tests/phase-08/fixtures/invoke-roundtrip/` | `@picolet.command` + JS `invoke` + postMessage echo pattern (invoke and error paths). |
| `/home/anl/picolet/tests/phase-08/fixtures/event-push/` | `picolet.emit` from Python + JS `on()` + echo-back pattern (event path). |
| `/home/anl/picolet/tests/phase-07/run.sh` | Harness structure: groups A–F, `pass/fail/skip` helpers, `_run_fixture` helper. |
| `/home/anl/picolet/tests/phase-08/run.sh` | Integration fixture runner, `--skip-integration` / `--skip-rebuild` flags, regression group. |
| `/home/anl/picolet/packages/picolet-cli/picolet/build_cmd.py` | Webview variant already fully wired (lines 132–220); `picolet build` works for webview apps today. |
| `/home/anl/picolet/packages/picolet-cli/picolet/validator.py` | `renderer = "webview"` accepted; `[ui]`, `[window]`, `[romfs]` all validated. |

### Codebase state entering PH09

`picolet build --target linux-x64` for a webview-variant app is fully
operational as of PH08. The build pipeline:

1. Reads `[ui] renderer = "webview"` → selects webview variant.
2. Cross-compiles `.py` sources via `mpy-cross`.
3. Copies `[romfs] include` dirs into staging.
4. Copies `picolet-bridge.js` into `staging/picolet/picolet-bridge.js`.
5. Embeds a sanitised `picolet.toml` in the romfs.
6. Appends the romfs to the webview runtime binary.

`picolet init` already works for `hello-cli`. `init_cmd.py`'s
`_KNOWN_TEMPLATES` set (`frozenset({"hello-cli"})`) is the only guard
that must be extended. Template files live under
`packages/picolet-templates/picolet_templates/<template-name>/`.

The IPC round-trip (invoke, error propagation, Python emit → JS on) has
already been verified by PH08's fixtures. PH09 does not re-prove the
plumbing; it verifies that the canonical template uses it correctly.

### Template design

#### Location

```
packages/picolet-templates/picolet_templates/hello-webview/
    picolet.toml
    src/
        main.py
    ui/
        index.html
        style.css
        app.js
```

The template lives under `picolet_templates/` (not `hello-cli/` — same
parent directory). `init_cmd.py`'s `_resolve_template` finds it via
`importlib.resources` or `__file__`-relative fallback; no change to the
resolver logic is needed.

#### `picolet.toml` skeleton

```toml
[app]
name = "{{name}}"
version = "0.1.0"
entry = "src/main.py"

[ui]
renderer = "webview"
root = "ui"
index = "index.html"

[window]
title = "{{name}}"
size = [800, 600]
resizable = true

[romfs]
include = ["ui"]
```

`{{name}}` is substituted by `_copy_template` for both `[app] name` and
`[window] title`. The validator accepts this shape as written (all
required fields present for a webview variant).

#### `src/main.py` skeleton

```python
# {{name}} — a minimal picolet webview app.
import picolet
import picolet_ui


@picolet.command
async def greet(args):
    name = args.get("name", "World")
    return "Hello, " + name


@picolet.command
async def fail_example(args):
    raise ValueError("this is an example error")


def main():
    app = picolet_ui.Application()
    return app.run()


main()
```

Key choices:
- `@picolet.command` registers both a success handler and an error-example
  handler. This makes the template self-documenting for both paths.
- `app.run()` with no `main=` kwarg uses the dispatcher loop as the main
  coroutine (the default `picolet_ui.Application.run` behaviour). This is
  the simplest correct form for a user-facing template.
- No `asyncio.wait_for` watcher, no `sys.exit` — the app stays open and
  is driven by user interaction. This distinguishes the template from the
  test fixtures which self-terminate.

#### `ui/index.html` skeleton

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{name}}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>{{name}}</h1>
  <p>
    <button id="btn-greet">Say Hello</button>
    <button id="btn-fail">Trigger Error</button>
  </p>
  <p id="result"></p>
  <script src="app.js"></script>
</body>
</html>
```

#### `ui/style.css` skeleton

```css
body {
  font-family: sans-serif;
  max-width: 600px;
  margin: 4rem auto;
  background: #f5f5f5;
  color: #222;
}
#result {
  min-height: 1.5em;
  padding: 0.5em;
  background: #fff;
  border: 1px solid #ccc;
  border-radius: 4px;
}
.error { color: #c00; }
```

#### `ui/app.js` skeleton

```js
// {{name}} — wires UI buttons to picolet IPC commands.
const resultEl = document.getElementById('result');

document.getElementById('btn-greet').addEventListener('click', async () => {
  resultEl.className = '';
  try {
    const msg = await window.picolet.invoke('greet', { name: 'World' });
    resultEl.textContent = msg;
  } catch (err) {
    resultEl.className = 'error';
    resultEl.textContent = err.name + ': ' + err.message;
  }
});

document.getElementById('btn-fail').addEventListener('click', async () => {
  resultEl.className = '';
  try {
    await window.picolet.invoke('fail_example');
    resultEl.textContent = 'no error (unexpected)';
  } catch (err) {
    resultEl.className = 'error';
    resultEl.textContent = err.name + ': ' + err.message;
  }
});
```

`{{name}}` substitution in `app.js` is purely cosmetic (the comment).
No `{{name}}` token appears in executable JS — `_copy_template` runs
the substitution over all files, which is harmless here.

### `init_cmd.py` change

A single-line edit: add `"hello-webview"` to `_KNOWN_TEMPLATES`.

```python
_KNOWN_TEMPLATES: frozenset[str] = frozenset({"hello-cli", "hello-webview"})
```

The `--template` help text on the `init` parser may also be updated to
mention `hello-webview` in the description.

### Test harness design

#### Location and shape

```
tests/phase-09/
    run.sh          — tester harness (mirrors PH07/PH08 structure)
```

No pytest files. The harness pattern for webview integration tests in
this repo is a bash script that drives `picolet build` + `xvfb-run`. JS
unit tests are not needed here — the bridge JS was fully unit-tested in
PH08; PH09 tests the template at the application level.

#### Self-terminating test app

The `hello-webview` template is an interactive app; it does not
self-terminate. The test harness cannot use the template binary directly
as a test driver because the app waits indefinitely for user button
clicks.

The solution used across PH07 and PH08: drive the test via JS that runs
automatically on page load and posts results back to Python via
`window.webkit.messageHandlers.picolet.postMessage`. Python receives the
results via `picolet.on()` and calls `sys.exit(0)` on success.

However, PH09's harness should avoid modifying the template source to
add test scaffolding. Instead the harness:

1. Uses `picolet init` to scaffold the template into a temp directory.
2. Verifies the scaffolded file tree is correct (gate A).
3. Runs `picolet build` and verifies the binary is produced (gate B).
4. Builds a **separate** test fixture under `tests/phase-09/fixtures/`
   that implements the same `greet` / `fail_example` / `emit` handlers
   as the template but adds JS-side self-test logic and Python-side
   assertion + `sys.exit`. This is the integration test binary.
5. Runs the fixture binary under `xvfb-run` and checks tokens in stdout
   (gates C–E).

This separation keeps the template clean and production-like while
providing a deterministic, automatable integration gate.

#### Test fixture

```
tests/phase-09/fixtures/hello-webview-e2e/
    picolet.toml          — same shape as the template's picolet.toml
    src/main.py         — greet + fail_example commands + event emit
                          + watcher coroutine that asserts and exits
    ui/index.html       — page that auto-invokes greet, fail_example,
                          subscribes on("server-push"), posts results back
```

The fixture `src/main.py` is similar to PH08's `invoke-roundtrip` +
`event-push` fixtures but exercises all three paths in one shot:
- invoke success path (`greet`)
- invoke error path (`fail_example`)
- Python emit → JS on path (`picolet.emit("server-push", ...)`)

It prints three sentinel tokens:
- `PICOLET_PH09_INVOKE_OK` — greet round-trip succeeded.
- `PICOLET_PH09_ERROR_OK` — error propagation confirmed.
- `PICOLET_PH09_EVENT_OK` — Python emit reached JS on() handler.

Then it calls `sys.exit(0)`.

#### Harness gate groups

```
Group A: picolet init scaffold (gates 1–3)
  A1  picolet init produces the expected directory tree (picolet.toml, src/main.py,
      ui/index.html, ui/style.css, ui/app.js present)
  A2  Scaffolded picolet.toml validates cleanly (picolet validate or init self-check)
  A3  {{name}} substitution applied in picolet.toml and ui/index.html

Group B: picolet build (gates 4–5)
  B1  picolet build --target linux-x64 exits 0 and produces the binary
  B2  Binary contains picolet.toml in romfs (/rom/picolet.toml readable)

Group C: invoke round-trip (gate 6 / FR-IPC-2, FR-WV-5)
  C1  greet("World") returns "Hello, World" end-to-end
      Token: PICOLET_PH09_INVOKE_OK

Group D: error propagation (gate 7 / FR-IPC-2)
  D1  fail_example() rejects with ValueError / "this is an example error"
      Token: PICOLET_PH09_ERROR_OK

Group E: Python emit → JS on (gate 8 / FR-IPC-3, FR-WV-5)
  E1  picolet.emit("server-push", {}) reaches JS window.picolet.on("server-push")
      Token: PICOLET_PH09_EVENT_OK

Group F: regression (gate 9)
  F1  PH08 gates still pass (bash tests/phase-08/run.sh --skip-rebuild)
```

#### xvfb invocation pattern (mirror PH08)

```bash
xvfb-run -a -s '-screen 0 800x600x24' \
    timeout 20 "$built" \
    > "$WORKDIR/$gate-run.log" 2>&1 || true
```

Timeout is 20 s (slightly longer than PH08's 15 s because PH09's
fixture exercises three gates sequentially in one run).

### Exit gate table

| # | Gate | Condition | Verification |
|---|---|---|---|
| 1 | A1 — scaffold tree | `picolet init test-app --template hello-webview` produces `test-app/picolet.toml`, `src/main.py`, `ui/index.html`, `ui/style.css`, `ui/app.js` | `ls test-app/` + `test -f test-app/ui/app.js` |
| 2 | A2 — toml valid | Scaffolded `picolet.toml` passes validation | `picolet init` self-check already calls `validate_toml`; gate confirms no `error:` in output |
| 3 | A3 — substitution | `name = "test-app"` appears in `picolet.toml`; `<title>test-app</title>` in `index.html` | `grep 'name = "test-app"' test-app/picolet.toml` |
| 4 | B1 — build succeeds | `cd test-app && picolet build --target linux-x64` exits 0 | Binary at `test-app/target/linux-x64/test-app` |
| 5 | B2 — romfs embedded | Binary can read `/rom/picolet.toml` | `"$built" -c 'print(open("/rom/picolet.toml").read())' 2>&1 \| grep '\[window\]'` |
| 6 | C1 — invoke round-trip | `greet({name:"World"})` returns `"Hello, World"` end-to-end | `PICOLET_PH09_INVOKE_OK` in fixture stdout |
| 7 | D1 — error path | `fail_example()` rejects with `ValueError / "this is an example error"` | `PICOLET_PH09_ERROR_OK` in fixture stdout |
| 8 | E1 — Python emit | `picolet.emit("server-push", {})` received by JS `on("server-push")` handler | `PICOLET_PH09_EVENT_OK` in fixture stdout |
| 9 | F1 — regression | PH08 mandatory gates still pass | `bash tests/phase-08/run.sh --skip-rebuild` → `All mandatory gates PASS` |

Gates 6–8 require the webview runtime and `xvfb-run`. The harness
guards each group with the same `command -v xvfb-run`, `command -v uv`,
`-f "$WEBVIEW_RUNTIME"` checks used in PH07/PH08 `run.sh`.

### Deliverables

1. `packages/picolet-templates/picolet_templates/hello-webview/picolet.toml` —
   template manifest with `{{name}}` substitution.
2. `packages/picolet-templates/picolet_templates/hello-webview/src/main.py` —
   two `@picolet.command` handlers plus `picolet_ui.Application().run()`.
3. `packages/picolet-templates/picolet_templates/hello-webview/ui/index.html` —
   two buttons wired to `invoke`, `<title>{{name}}</title>`.
4. `packages/picolet-templates/picolet_templates/hello-webview/ui/style.css` —
   minimal stylesheet.
5. `packages/picolet-templates/picolet_templates/hello-webview/ui/app.js` —
   button event listeners calling `window.picolet.invoke`.
6. `packages/picolet-cli/picolet/init_cmd.py` — `"hello-webview"` added to
   `_KNOWN_TEMPLATES`; `--template` help text updated.
7. `tests/phase-09/fixtures/hello-webview-e2e/picolet.toml` — fixture
   manifest.
8. `tests/phase-09/fixtures/hello-webview-e2e/src/main.py` — greet +
   fail_example + emit watcher + `sys.exit`.
9. `tests/phase-09/fixtures/hello-webview-e2e/ui/index.html` — auto-runs
   all three test paths on page load, posts results back.
10. `tests/phase-09/run.sh` — tester harness covering gates A–F.

### Sequence

All work is on `dev`. Multiple small commits are preferred over one
end-of-phase commit (per CLAUDE.md).

**1. Template files (deliverables 1–5).**

Write the five template files. Verify locally:
```bash
cd /home/anl/picolet
uv run python -m picolet init /tmp/ph09-test --template hello-webview
ls /tmp/ph09-test
grep 'name = "ph09-test"' /tmp/ph09-test/picolet.toml
```

Commit:
```
[PH09] Add hello-webview template.

Adds packages/picolet-templates/picolet_templates/hello-webview/ with
picolet.toml, src/main.py, ui/{index.html,style.css,app.js}. Registers
a greet command, a fail_example command, and two buttons. Template uses
{{name}} substitution for [app] name and [window] title.

Closes: FR-CLI-2 (hello-webview), FR-WV-{2,3,5}.
```

**2. `init_cmd.py` update (deliverable 6).**

Add `"hello-webview"` to `_KNOWN_TEMPLATES`. Update the `--template`
argument help string. Commit:
```
[PH09] Register hello-webview in init_cmd._KNOWN_TEMPLATES.

picolet init <name> --template hello-webview now resolves correctly.
```

**3. Test fixture (deliverables 7–9).**

Write the `hello-webview-e2e` fixture. This is the test driver, not the
template. Its `src/main.py` must self-terminate after all three tokens
are printed. Model it on `tests/phase-08/fixtures/invoke-roundtrip/` +
`event-push/` combined. Commit:
```
[PH09] Add hello-webview-e2e test fixture.

tests/phase-09/fixtures/hello-webview-e2e/ drives invoke, error-
propagation, and Python-emit gates in a single xvfb run.
```

**4. Harness (deliverable 10).**

Write `tests/phase-09/run.sh`. Follow the exact structure of
`tests/phase-08/run.sh`: PASS/FAIL/SKIP counters, `_run_fixture`
helper, `--skip-integration` / `--skip-rebuild` / `--verbose` flags,
final summary line `"All mandatory gates PASS."`. Commit:
```
[PH09] Add tests/phase-09/run.sh harness.
```

**5. Local verification.**

```bash
# Build the e2e fixture binary once.
cd tests/phase-09/fixtures/hello-webview-e2e
uv run python -m picolet build \
    --target linux-x64 \
    --runtime /home/anl/picolet/packages/picolet-runtime/build/picolet-runtime-linux-x64-webview

# Run the full gate suite.
cd /home/anl/picolet
bash tests/phase-09/run.sh --verbose
```

All 9 gates must be green before the tester step.

### Implementation guidance

#### Fixture `src/main.py` pattern

The fixture must coordinate three async events. Model it as a single
watcher coroutine passed to `app.run(main=watcher)`, using
`asyncio.Event` + `picolet.on` for each token:

```python
import sys, asyncio
import picolet, picolet_ui

@picolet.command
async def greet(args):
    name = args.get("name", "World")
    return "Hello, " + name

@picolet.command
async def fail_example(args):
    raise ValueError("this is an example error")

async def watcher():
    invoke_evt = asyncio.Event()
    error_evt  = asyncio.Event()
    event_evt  = asyncio.Event()

    def on_invoke_result(data):
        if data.get("value") == "Hello, World":
            print("PICOLET_PH09_INVOKE_OK")
            invoke_evt.set()
        else:
            sys.stderr.write("unexpected invoke result: {}\n".format(data))

    def on_error_result(data):
        if data.get("name") == "ValueError" and \
           "this is an example error" in data.get("message", ""):
            print("PICOLET_PH09_ERROR_OK")
            error_evt.set()
        else:
            sys.stderr.write("unexpected error result: {}\n".format(data))

    def on_event_echo(data):
        print("PICOLET_PH09_EVENT_OK")
        event_evt.set()

    picolet.on("invoke-result", on_invoke_result)
    picolet.on("error-result", on_error_result)
    picolet.on("event-echo", on_event_echo)

    for evt, label in [
        (invoke_evt, "invoke-result"),
        (error_evt,  "error-result"),
        (event_evt,  "event-echo"),
    ]:
        try:
            await asyncio.wait_for(evt.wait(), 20.0)
        except asyncio.TimeoutError:
            sys.stderr.write("hello-webview-e2e: timed out on {}\n".format(label))
            sys.exit(1)

    sys.exit(0)

def main():
    app = picolet_ui.Application()
    return app.run(main=watcher)

main()
```

#### Fixture `ui/index.html` pattern

The page must wait for the bridge to be available (it is, because
`DOCUMENT_START` injects the bridge before this script runs), then
auto-run all three test paths sequentially:

```html
<!doctype html>
<html>
<head><meta charset="utf-8"><title>PH09 E2E</title></head>
<body>
<script>
(async function() {
  // Gate C: invoke round-trip.
  try {
    var val = await window.picolet.invoke("greet", { name: "World" });
    window.webkit.messageHandlers.picolet.postMessage(
      JSON.stringify({ event: "invoke-result", data: { value: val } })
    );
  } catch (e) {
    window.webkit.messageHandlers.picolet.postMessage(
      JSON.stringify({ event: "invoke-result", data: { error: e.message } })
    );
  }

  // Gate D: error propagation.
  try {
    await window.picolet.invoke("fail_example");
    window.webkit.messageHandlers.picolet.postMessage(
      JSON.stringify({ event: "error-result", data: { unexpected: "no error" } })
    );
  } catch (e) {
    window.webkit.messageHandlers.picolet.postMessage(
      JSON.stringify({ event: "error-result",
                       data: { name: e.name, message: e.message } })
    );
  }

  // Gate E: Python emit → JS on.
  window.picolet.on("server-push", function(data) {
    window.webkit.messageHandlers.picolet.postMessage(
      JSON.stringify({ event: "event-echo", data: data })
    );
  });
  // Signal readiness so Python knows to emit.
  window.webkit.messageHandlers.picolet.postMessage(
    JSON.stringify({ event: "page-ready", data: {} })
  );
})();
</script>
</body>
</html>
```

The `watcher` coroutine in `main.py` must also register `picolet.on("page-ready",...)`
and call `await picolet.emit("server-push", {})` after receiving it,
before waiting for `event-echo`. This exactly mirrors the PH08
`event-push` fixture pattern.

Updated watcher skeleton including the emit step:

```python
async def watcher():
    invoke_evt = asyncio.Event()
    error_evt  = asyncio.Event()
    event_evt  = asyncio.Event()
    ready_evt  = asyncio.Event()

    picolet.on("invoke-result", lambda d: (
        d.get("value") == "Hello, World" and (
            print("PICOLET_PH09_INVOKE_OK") or invoke_evt.set()
        ) or sys.stderr.write("unexpected invoke: {}\n".format(d))
    ))
    picolet.on("error-result", lambda d: (
        (d.get("name") == "ValueError" and "this is an example error" in d.get("message","")) and (
            print("PICOLET_PH09_ERROR_OK") or error_evt.set()
        ) or sys.stderr.write("unexpected error: {}\n".format(d))
    ))
    picolet.on("event-echo", lambda d: (print("PICOLET_PH09_EVENT_OK") or event_evt.set()))
    picolet.on("page-ready", lambda d: ready_evt.set())

    for evt, label in [
        (invoke_evt, "invoke-result"),
        (error_evt,  "error-result"),
    ]:
        try:
            await asyncio.wait_for(evt.wait(), 20.0)
        except asyncio.TimeoutError:
            sys.stderr.write("timed out: {}\n".format(label))
            sys.exit(1)

    try:
        await asyncio.wait_for(ready_evt.wait(), 10.0)
    except asyncio.TimeoutError:
        sys.stderr.write("timed out: page-ready\n")
        sys.exit(1)

    await picolet.emit("server-push", {})

    try:
        await asyncio.wait_for(event_evt.wait(), 10.0)
    except asyncio.TimeoutError:
        sys.stderr.write("timed out: event-echo\n")
        sys.exit(1)

    sys.exit(0)
```

Note: lambda-based handlers are compact but can obscure logic. Using
named inner functions (as in the PH08 fixtures) is equally correct and
easier to read. The developer should choose based on readability.

#### `run.sh` structure notes

- Use absolute paths computed from `SCRIPT_DIR` and `REPO_ROOT`, as in
  PH07/PH08.
- The scaffold gates (group A) run `picolet init` into a temp directory
  under `$WORKDIR`. Do not hardcode a path the user may not have.
- The build gate (group B) builds the template binary from the temp
  scaffold dir. This is the only gate that touches the template itself;
  the integration gates use the separate fixture.
- The integration gates (C–E) build and run
  `tests/phase-09/fixtures/hello-webview-e2e/` via `_run_fixture`,
  exactly as PH08 does for its fixtures.
- The regression gate (F) calls `bash tests/phase-08/run.sh --skip-rebuild`.
- The harness must have a `--skip-integration` flag that skips groups
  B–F (or at minimum C–E) when xvfb is not available.

### Foreseeable risks

**Risk 1: xvfb timing — all three gates in one fixture run.**

The PH09 fixture exercises three sequential gates in a single xvfb run.
If any early step times out (e.g. the invoke round-trip takes longer
than expected under load), all subsequent gates also fail. PH08's
longest fixture ran in under 15 s; PH09 uses 20 s for the combined run.

Mitigation: set `await asyncio.wait_for(...)` timeouts conservatively
(20 s for the early gates, 10 s for the later ones). If the combined
run still flakes, split into separate `_run_fixture` calls per token,
each as a standalone fixture. The cost is three xvfb launches instead
of one; the benefit is independent failure isolation.

**Risk 2: page-ready / emit ordering race.**

The JS page posts `invoke` and `error` before registering the
`on("server-push")` handler, then posts `page-ready`. If Python emits
`server-push` before JS has registered the handler, the event is lost.

The fixture HTML above sequences this correctly: `on("server-push")`
is registered before the `page-ready` postMessage fires. Python waits
for `page-ready` before emitting. This matches the verified PH08
`event-push` pattern. The developer must not reorder these steps.

**Risk 3: `{{name}}` in `app.js` causing unexpected substitution.**

`_copy_template` replaces `{{name}}` in every file including `app.js`.
If the template's `app.js` contains any JS string literals that
coincidentally include `{{name}}`, they will be substituted. The
skeleton above uses `{{name}}` only in a comment. The developer must
avoid placing `{{name}}` in any executable JS expression.

**Risk 4: `picolet init` validation failure if `[window]` or `[romfs]`
have wrong types.**

`validate_toml` is called by `init_cmd.run` on the scaffolded output.
If the template's `picolet.toml` skeleton has any type mismatch (e.g.
`size = [800, 600]` stored as strings instead of integers, or missing
required keys), `init` will roll back and fail.

Mitigation: test `picolet init` locally before committing the template.
The validator's `[window] size` type is `list[int]`; TOML integer
literals `[800, 600]` are correct.

**Risk 5: `hello-webview` template binary differs from fixture binary.**

Gates B1/B2 build the scaffolded template binary; gates C–E run the
separate fixture binary. If the two binaries diverge (e.g. the template
`main.py` has a different command name), the test remains valid but the
template is untested end-to-end.

Mitigation: the template's `greet` command must use exactly the same
signature (`args.get("name", "World")`) as the fixture's `greet`
command. Keep the two in sync. The phase is complete only when both the
template builds successfully (gate B) and the fixture round-trips (gates
C–E).

### Out of scope

- Windows webview (PH10) — deferred.
- LVGL template (PH11/12) — deferred.
- `hello-lvgl` template (PH14) — deferred.
- `picolet dev` live-reload (PH16) — deferred.
- TypeScript types for registered commands — out of scope for v1.
- Full template polish / multi-file templates / TypeScript frontend
  scaffolding — PH14 scope.
- macOS — out of scope for v1.

### Spec traceability

| Spec id | Requirement | Gate(s) |
|---|---|---|
| FR-CLI-2 | `picolet init <name> --template hello-webview` scaffolds a working directory tree. | A1, A2, A3 |
| FR-WV-2 | Webview loads root document from `/rom/<ui.root>/<index>`. | B2 (romfs embedded), C1 (page loads and JS runs) |
| FR-WV-3 | Window title and size come from `[window]` in `picolet.toml`. | A3 (title substituted), B2 (toml in romfs) |
| FR-WV-4 | `picolet-bridge-js` injected before user frontend JS runs. | C1 (invoke succeeds, proving bridge was present when `app.js` ran) |
| FR-WV-5 | Bridge exposes `invoke` → Promise and `on` → unsubscribe. | C1, D1, E1 |
| FR-IPC-2 | `invoke` returns return value or raises with originating exception type + message. | C1 (success path), D1 (error path) |
| FR-IPC-3 | `picolet.emit(topic, data)` from Python reachable by `picolet.on`. | E1 |
| FR-BP-1 | `picolet build` resolves webview variant from `[ui] renderer`. | B1 |
| FR-BP-3 | User `.py` sources compiled to `.mpy` via `mpy-cross`. | B1 (build pipeline exercises mpy-cross) |
| FR-BP-4 | Romfs includes `[romfs] include` dirs + `.mpy` + bridge-js bundle. | B1, B2 |
| FR-BP-5 | Final binary is runtime with romfs appended. | B1, B2 |

## Verification

**Verdict: PASS**

Performed 2026-05-15 on `dev` by scrum-tester (sonnet-4.6).

### Method

Independent verification — all gates run from scratch without relying on developer artefacts,
except the pre-built webview runtime and bridge-js (phase prerequisites).

### Gate results (enhanced harness — 13 gates)

Original 9 developer gates plus 4 tester-added gap-coverage gates.

| Gate | Label | Result |
|---|---|---|
| A1 | scaffold-tree | PASS |
| A2 | toml-validates | PASS |
| A3 | name-substituted (title + picolet.toml) | PASS |
| A4 | name-substituted-all-files (h1, main.py, app.js) | PASS — tester-added |
| A5 | reject-unknown-template | PASS — tester-added |
| A6 | reject-existing-nonempty-dir | PASS — tester-added |
| B1 | build-succeeds | PASS |
| B2 | romfs-embedded (picolet.toml) | PASS |
| B3 | ui-packed-in-romfs (/rom/ui/index.html readable) | PASS — tester-added |
| C1 | invoke-roundtrip (PICOLET_PH09_INVOKE_OK) | PASS |
| D1 | error-propagation (PICOLET_PH09_ERROR_OK) | PASS |
| E1 | python-emit (PICOLET_PH09_EVENT_OK) | PASS |
| F1 | ph08-gates-still-pass | PASS |

**13 passed, 0 failed, 0 skipped.**

### Independent integration probe

Beyond the harness, a standalone probe fixture was built from scratch and run under xvfb:
`window.picolet.invoke("greet", {name: "Tester"})` → `"Hello, Tester"` confirmed end-to-end.

### PH03–PH08 regression

| Phase | Result |
|---|---|
| PH03 | PASS (21/21 gates) |
| PH04 | PASS |
| PH05 | PASS |
| PH06 | PASS |
| PH07 | PASS |
| PH08 | PASS |

### Coverage gaps closed

- **A4**: A3 only verified `<title>` and `name =` in toml. A4 adds `<h1>`, `src/main.py` comment,
  and `ui/app.js` comment — all substituted correctly.
- **A5/A6**: Negative paths (wrong template name, non-empty existing dir) were untested. Both
  produce correct error messages and exit non-zero.
- **B3**: B2 verified `picolet.toml` in romfs but not the `ui/` tree. B3 confirms
  `/rom/ui/index.html` is readable from the binary, directly proving FR-WV-2's romfs packing.

### Spec requirement coverage

All FR-CLI-2, FR-WV-{2,3,4,5}, FR-IPC-{2,3}, FR-BP-{1,3,4,5} verified end-to-end. Primary exit
gate FR-IPC-2 confirmed via both success path (C1) and error path (D1).

### Notes for scrum-po

- A2 (toml-validates) is structurally implied by A1 (files present implies no rollback implies no
  validation error). It provides documentation value but no independent signal. Low priority to fix.
- The hello-webview template binary is interactive (no self-termination). Gate B1/B2 exercise the
  template build; gates C–E use a separate self-terminating fixture. This separation is correct by
  design and noted in the phase plan.
- Windows webview is deferred to PH10 as planned.
