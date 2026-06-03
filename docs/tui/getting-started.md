# Getting started with picolet-tui

This tutorial walks from the smallest possible TUI app to a multi-screen
program with reactive state, custom widgets, key bindings, and tests.
Every section is a runnable snippet.

picolet-tui ships as a separate runtime variant
(`picolet-runtime-{linux-x64,windows-x64}-tui`, ~1.17 MiB). The Python
framework — `picolet_tui` — is frozen into the binary. Build once,
ship a single file, run anywhere with a VT100+ terminal.

## Prerequisites

- picolet CLI installed (`uv tool install picolet` or `pipx install picolet`).
- A terminal that speaks ANSI VT100+. Anything modern works:
  - **Linux**: any terminal emulator (xterm, alacritty, kitty, GNOME
    Terminal, Konsole, tmux, screen). Explicitly verified in CI.
  - **Windows**: Windows Terminal on Windows 10 22H2 or Windows 11.
    Explicitly verified in CI. The pre-1809 conhost is rejected with a
    diagnostic per FR-TUI-10 — upgrade or use Windows Terminal.
  - **macOS**: deferred to v0.2. Building works; the runtime is not
    gated on macOS.
- stdin and stdout must be a tty. Piping output (`./app | tee log`)
  is refused with a one-line stderr diagnostic per FR-TUI-10.

> **Accessibility note.** picolet-tui owns stdout and emits raw ANSI
> while running. Most screen readers do not interpret these streams.
> If you need a screen-reader-friendly UI today, use the webview
> renderer instead. See `NFR-TUI-28`; full SR integration is on the
> v0.2 roadmap.

> **Recovery after a crash.** If a TUI app dies before it can restore
> the terminal (SIGKILL, segfault, host shutdown), your shell may
> appear broken — no echo, wrong cursor, alt-screen artefacts. Run
> `reset` to recover. `stty sane` is a partial fallback that fixes
> input modes but not alt-screen. This is the documented `NFR-TUI-30`
> recovery procedure; v0.1 ships no watchdog.

---

## 1. Hello, TUI

The smallest possible picolet-tui app:

```bash
picolet init my-tui-app --template hello-tui
cd my-tui-app
```

The scaffolded `src/main.py`:

```python
from picolet_tui import App, widget
from picolet_tui.widgets import Static


@widget
class HelloApp(App):
    TITLE = "Hello"

    def compose(self):
        yield Static("hello tui")


HelloApp().run()
```

Build it:

```bash
picolet build
# produces target/linux-x64/my-tui-app (~1.2 MiB)
```

Run it:

```bash
./target/linux-x64/my-tui-app
```

You see `hello tui` in the top-left of the alt-screen. Press
**`Ctrl+Q`** to quit — that binding is shipped on `App` itself
(FR-TUI-4) and survives subclassing unless you explicitly override it.

**What just happened.** `App.run()` calls `tuiterm.enable()` (raw mode,
alt-screen, hide cursor, mouse SGR, bracketed paste), schedules three
asyncio tasks (`_pump_input`, `_pump_resize`, `_render`) via
`asyncio.gather`, mounts your root widget, and blocks until
`Ctrl+Q` (`App.quit()` → `App.exit(None)`). On the way out
`tuiterm.disable()` runs exactly once and restores the terminal.

**Why `@widget`.** Every class that participates in the framework
(reactives, message handlers, BINDINGS, computed properties) must
carry the `@widget` decorator. MicroPython does not call
`__set_name__` and does not run a metaclass, so the decorator is the
single class-time registration hook (FR-TUI-57, synthesis D1).
Forgetting it raises `MissingWidgetDecoratorError` on first
instantiation.

To target Windows from Linux:

```bash
picolet build --target windows-x64
# produces target/windows-x64/my-tui-app.exe
```

---

## 2. Reactive properties + watchers

A `Reactive` is a class-level descriptor that fires a watcher on every
assignment. The watcher is the canonical place to update derived
state — re-render, post a `Message`, etc.

```python
from picolet_tui import App, Reactive, widget
from picolet_tui.widgets import Label


@widget
class Counter(Label):
    count = Reactive(0)

    def watch_count(self, old, new):
        # Watcher runs on every assignment (FR-TUI-20).  Two-arg
        # form (new) and three-arg form (old, new) are both
        # supported; the decorator records the arity once at
        # decoration time so the call site is just a tuple unpack.
        self.update("count: {}".format(new))


@widget
class CounterApp(App):
    def compose(self):
        self.counter = Counter()
        yield self.counter

    async def on_mount(self):
        # Mutate the reactive from any async context.  The watcher
        # fires before the next render frame.
        for n in range(1, 6):
            self.counter.count = n
            await __import__("asyncio").sleep(0.5)


CounterApp().run()
```

You see the label tick from `count: 1` to `count: 5`. Each assignment
runs `watch_count`, which calls `Label.update(...)`, which flags the
widget dirty; the next render frame paints the new text.

**Reactive flags.**

```python
# Schedule a layout pass on assignment (not just a repaint).
visible = Reactive(True, layout=True)

# Fire the watcher even when the new value equals the old.
selection = Reactive(None, always_update=True)
```

**Computed reactives.** Declare `compute_<name>(self)` instead of a
`Reactive(...)` descriptor; reads call the method, writes raise
`ReactiveError`:

```python
@widget
class Total(Label):
    qty = Reactive(0)
    price = Reactive(0.0)

    def compute_total(self):
        return self.qty * self.price

    def watch_qty(self, new):
        self.update("total: {:.2f}".format(self.total))
```

You cannot define both `compute_<name>` and `Reactive(<name>)` on the
same class — the decorator raises `TooManyComputesError` at
decoration time (FR-TUI-21).

---

## 3. Layout: Container, Vertical, Horizontal, Stack

The four layout widgets compose all v0.1 layouts.

### Container — non-directional grouping

```python
from picolet_tui.widgets import Container, Static

yield Container(Static("a"), Static("b"))
```

No implicit direction; children stack in mount order. Use when you
want a styled group without committing to a row/column axis.

### Vertical — top-to-bottom

```python
from picolet_tui.widgets import Vertical, Label

yield Vertical(Label("first"), Label("second"), Label("third"))
```

Each child receives a horizontal strip. `1fr` heights split the
remainder evenly (FR-TUI-44).

### Horizontal — left-to-right

```python
from picolet_tui.widgets import Horizontal, Button

yield Horizontal(Button("OK", id="ok"), Button("Cancel", id="cancel"))
```

Children get vertical strips. `1fr` widths split the remainder
(FR-TUI-45).

### Stack — one visible child at a time

```python
from picolet_tui.widgets import Stack, Static

stack = Stack(Static("page 1"), Static("page 2"), Static("page 3"))
stack.active = 1   # show page 2
```

`Stack` is the v0.1 "card pile" widget. Children live in the DOM
together; only the one at index `active` is rendered. Use it for
tabbed UIs and step wizards where mounting/unmounting per step would
discard state. For modal dialogs that own focus, use
`App.push_screen()` instead (§5).

**Nesting is the usual pattern:**

```python
@widget
class TwoPaneApp(App):
    def compose(self):
        yield Horizontal(
            Vertical(Label("sidebar A"), Label("sidebar B")),
            Vertical(Label("content"), Label("status bar")),
        )
```

---

## 4. Events: `@on` and Bindings

Events bubble up the DOM from the originating widget toward the root
(FR-TUI-12). A handler stops bubbling with `message.stop()`.

### `@on` decorator

```python
from picolet_tui import App, on, widget
from picolet_tui.widgets import Button, Label, Vertical


@widget
class ClickApp(App):
    def compose(self):
        self.status = Label("idle")
        yield Vertical(
            Button("Click me", id="go"),
            self.status,
        )

    @on(Button.Pressed)
    def handle_press(self, event):
        # event.button is the Button instance that fired.
        self.status.update("clicked: {}".format(event.button.label))


ClickApp().run()
```

`@on(MessageType)` collects the handler into
`cls._tui_widget_meta["handlers"]` at class-decoration time. There is
no per-message decorator lookup at runtime — the cost is one dict
lookup per ancestor walked.

A method named `on_button_pressed(self, event)` is dispatched as a
fallback (FR-TUI-14). The `@on` form is preferred because it tolerates
class renames.

### BINDINGS

```python
from picolet_tui import App, Binding, widget
from picolet_tui.widgets import Label


@widget
class BindingsApp(App):
    BINDINGS = [
        Binding("d", "toggle_dark", "Toggle theme"),
        Binding("r", "refresh", "Refresh"),
        # The 2-tuple shorthand is accepted too.
        ("ctrl+l", "clear_log"),
    ]

    def compose(self):
        yield Label("press d, r, or ctrl+l")

    def action_toggle_dark(self):
        # Bindings resolve action="toggle_dark" -> self.action_toggle_dark()
        # The dispatcher prepends "action_" per design §6.3.
        self.log("toggle dark")

    def action_refresh(self):
        self.log("refresh")

    def action_clear_log(self):
        self.log("clear")


BindingsApp().run()
```

`BINDINGS` lives on `App`, `Screen`, or any `Widget` subclass. The
`@widget` decorator merges parent BINDINGS along the MRO with
subclass-wins precedence (so removing the `ctrl+q` quit binding takes
an explicit re-bind, not just omission).

The shorthand forms accepted are `Binding(key, action, description)`,
`(key, action, description)`, and `(key, action)`.

---

## 5. Adding a Screen

A `Screen` is a full-display `Widget` hosted by `App._screen_stack`.
Multiple screens stack; the top is visible and receives input. Use a
screen for any UI that should fully replace the previous view — modal
dialogs, confirmation prompts, settings panels.

### Push and pop

```python
from picolet_tui import App, Screen, on, widget
from picolet_tui.widgets import Button, Label, Vertical


@widget
class HelpScreen(Screen):
    def compose(self):
        yield Vertical(
            Label("Help: press q to go back"),
            Button("Close", id="close"),
        )

    @on(Button.Pressed)
    async def handle_close(self, event):
        # dismiss() returns this screen's result to the caller of
        # push_screen() and pops the screen off the stack.
        await self.dismiss(result="closed")


@widget
class MainApp(App):
    BINDINGS = [("h", "help", "Help")]

    def compose(self):
        yield Label("press h for help")

    async def action_help(self):
        # push_screen mounts and focuses the new screen.
        # The Event+slot dismiss pattern is exposed via the
        # screen's wait_for_dismiss() helper.
        screen = HelpScreen()
        await self.push_screen(screen)
        result = await screen.wait_for_dismiss()
        self.log("help dismissed:", result)


MainApp().run()
```

### Sync `pop_screen()`

`App.pop_screen()` is sync but returns a coroutine the caller must
await:

```python
await self.pop_screen()
```

This mirrors upstream Textual's surface (sync function, coroutine
return) so existing recipes port unchanged.

### Why not `Stack` for screens?

`Stack` is a layout widget — its children are siblings in the DOM
and all stay mounted. A `Screen` push fully covers prior screens,
moves focus to the new top, and fires visibility hooks
(`_on_hidden` / `_on_visible`). Use `Stack` for tab-like UIs that
should keep all panes alive; use screens for modals.

---

## 6. Testing your app

`TuiHarness` drives a built picolet-tui binary attached to a real pty.
No mocks, no parallel parser, no wall-clock sleeps — synchronisation
is via `wait_idle()`, which detects byte-stream quiescence (a DSR-6
round-trip once the Phase-4 compositor lands; FR-TUI-63). Source at
`packages/picolet/picolet/testing/_tui.py`.

```python
# tests/test_hello.py
import pytest
from picolet.testing import TuiHarness


@pytest.mark.asyncio
async def test_hello_renders():
    async with TuiHarness("target/linux-x64/my-tui-app") as h:
        await h.wait_idle()
        assert h.cells_at(0, 0, 9) == "hello tui"
        await h.exit_app()
```

The harness API in one block:

```python
async with TuiHarness(binary_path, cols=80, rows=24) as h:
    await h.send("hello")          # type characters
    await h.press("enter")         # symbolic key (FR-TUI-15 vocab)
    await h.press("ctrl+q")        # modifier-prefixed form
    await h.wait_idle()            # block until output settles
    h.cells_at(row, col, length)   # read a string from the screen
    h.style_at(row, col)           # read the Style at a cell
    h.frame()                      # full Cell grid snapshot
    await h.exit_app()             # send ctrl+q and wait for exit
```

The harness rejects mintty / Cygwin pty emulation at construction
(FR-TUI-61) — on Windows it allocates ConPTY directly (v0.2).

**No `sleep` in tests.** A grep gate (`tests/check-no-sleep.py`)
fails CI if a widget or integration test calls `asyncio.sleep` /
`time.sleep` with a non-zero argument. Use `wait_idle()`.

**Unknown ANSI fails loudly.** The harness's virtual screen raises on
any input it cannot parse rather than silently dropping bytes
(NFR-TUI-21). When a test fails with `unknown ANSI: ...`, the
SUT emitted something the parser does not know about — likely a
bug in a custom render path, not the harness.

---

## 7. Differences from CPython Textual

picolet-tui is **inspired by** Textual but is a separate framework. If
you are porting an upstream app, expect the following deltas:

- **`@widget` decorator required on every class** with reactives,
  `@on`-decorated handlers, `BINDINGS`, or `compute_<name>` methods.
  MicroPython does not call `__set_name__` and we do not use a
  metaclass. Forgetting raises `MissingWidgetDecoratorError`.
- **No CSS / TCSS.** v0.1 styles are Python — use the `Style(...)`
  DSL or hard-code in `render()`. TCSS is a v0.2 candidate. See
  `Style` keyword surface at FR-TUI-32..37.
- **No DataTable, Tree, TextArea, RichLog, MarkdownViewer, Tabs,
  OptionList, Switch, RadioSet, Sparkline.** v0.1 ships nine widgets:
  Static, Label, Container, Vertical, Horizontal, Button, Input,
  Stack, ProgressBar. The rest are v0.2.
- **No animation / transitions.** No `App.animate()`, no easing
  curves, no fade-in. Layout changes are immediate (FR-TUI-31).
- **No worker threads.** Single-threaded asyncio only — no `_thread`,
  no `loop.run_in_executor`, no thread-keyed structures. The build
  flag `MICROPY_PY_THREAD` is **off** in the TUI variant.
- **asyncio subset only.** Uses `Task`, `Event`, `Queue`, and `gather`.
  No `TaskGroup` (D6 pins pre-0.50 Textual semantics). No
  `asyncio.timeout()`, no `run_coroutine_threadsafe`.
- **No `__init_subclass__` hooks.** All class-time work happens in the
  `@widget` decorator's `vars(cls)` walk. Subclassing semantics are
  identical from a user's point of view, but the framework's internal
  registration is explicit.
- **No screen-reader integration.** Stdout is raw ANSI (NFR-TUI-28).
- **Configuration via env vars only in v0.1.** `PICOLET_TUI_COLOR`,
  `PICOLET_TUI_BORDER`, `PICOLET_TUI_DEBUG`. Config-file integration
  is deferred. See NFR-TUI-27.

A complete porting cheat-sheet lives at
[docs/tui/migration-from-textual.md](migration-from-textual.md).

---

## Where to go next

- **Phase 0 research** — the four investigations that pinned the v0.1
  shape: dependency analysis, Rich subset, MicroPython stdlib coverage,
  terminal handling. `docs/tui/research/{00-synthesis,01..04}.md`.
- **Spec** — every FR-TUI-* and NFR-TUI-* referenced in this doc.
  `docs/tui/tui-v0.1-spec.md`.
- **Design** — class-decoration algorithm, asyncio task topology,
  message bubbling, Style DSL surface.
  `docs/tui/textual-core-design.md`.
- **Authoring widgets** — when the nine built-ins are not enough:
  `docs/tui/authoring-widgets.md`.
- **Examples** — a real picolet-tui app: `examples/tui-pydfu/` ships
  a DFU flash utility with a progress bar, a device picker (Stack),
  and a quit confirmation (Screen).
