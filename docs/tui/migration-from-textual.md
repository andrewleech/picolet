# Migrating from Textual to picolet-tui

picolet-tui is a Textual-compatible subset that runs on MicroPython. The
core mental model is identical; the divergences below are forced by the
constraints of the runtime (no metaclasses, no threads, no CSS engine).

## Same

The following concepts and APIs behave as they do in Textual:

- `App`, `Widget`, `Screen`, `MessagePump` core hierarchy and lifecycle.
- `Reactive(default)` descriptor pattern, including watchers.
- `@on(MessageType)` decorator for message handlers.
- `on_<event>` name-based handlers (e.g. `on_button_pressed`).
- `BINDINGS` class attribute with `Binding(key, action, description)` tuples.
- Layout containers: `Container`, `Vertical`, `Horizontal`, `Stack`.
- Widgets: `Static`, `Label`, `Button`, `Input`, `ProgressBar`.
- `compose()` returning an iterable of children.
- `query_one(...)` / `query(...)` lookups by type or id.
- `push_screen`, `pop_screen`, `switch_screen` on the screen stack.

If your Textual app only uses the above, the port is mechanical.

## Different — MicroPython divergences

- **`@widget` decorator MUST be applied** to every `Widget`, `Screen`,
  or `App` subclass — and to anything else that declares `Reactive`,
  `@on`, `BINDINGS`, or `on_<event>` handlers. Textual wires these via
  metaclass `__init_subclass__` hooks; MicroPython does not honour
  metaclasses, so the decorator is the only path that registers
  descriptors and handler tables. Forgetting `@widget` produces a class
  that looks correct but never reacts to anything.

- **No CSS / TCSS** in v0.1. There is no stylesheet loader, no
  `DEFAULT_CSS`, no `CSS_PATH`. Set visual properties through the
  `Style(...)` constructor passed to widgets, or hard-code styles inside
  the strings/segments returned from `render()`.

- **No worker threads** — the runtime is a single asyncio loop. The
  `@work` decorator and `Worker` class do not exist. Long-running work
  must be expressed as `async def` coroutines scheduled with
  `asyncio.create_task(...)`.

- **`asyncio.run` and `asyncio.gather` only.** MicroPython's asyncio
  lacks `TaskGroup`, `loop.add_signal_handler`, and a real `Future`
  result type. Patterns that rely on `asyncio.TaskGroup()` or
  `await future` (where `future` is a standalone `Future`) need to be
  rewritten using `gather` or an `Event`.

- **No `weakref`.** Holding a `Widget` reference in user code is a
  strong reference and will keep the widget alive past unmount. Use
  `widget.remove()` / `widget.unmount()` explicitly for lifecycle, and
  avoid stashing widgets in module-level caches.

- **Two-argument watchers.** `watch_<name>(self, old, new)` always
  receives both the previous and new value. Textual permits a
  single-argument form (`watch_<name>(self, value)`); picolet-tui does
  not.

- **Reactive is capitalised.** `Reactive(0)`, not `reactive(0)`. The
  lowercase alias is not exported.

## Out of scope for v0.1 (planned for v0.2)

Widgets and features that exist in Textual but are not in this release:

- `DataTable`, `Tree`, `TextArea`, `MarkdownViewer`, `RichLog`.
- `Tabs`, `OptionList`, `Switch`, `RadioSet`, `Sparkline`.
- `Header`, `Footer` (the framed app chrome).
- CSS / TCSS stylesheet support.
- Animations and the easing system.
- macOS native runtime binary.
- Windows ConPTY support inside `TuiHarness` (Linux PTY only for now).

File feature-gap requests against the v0.2 milestone (see end of doc).

## Worked example: porting a Textual Counter app

Before (Textual):

```python
from textual.app import App
from textual.reactive import reactive
from textual.widgets import Label, Button

class Counter(App):
    count = reactive(0)

    def compose(self):
        yield Label("0")
        yield Button("+1")

    def on_button_pressed(self):
        self.count += 1

    def watch_count(self, count):
        self.query_one(Label).update(str(count))
```

After (picolet-tui):

```python
from picolet_tui._textual._widget_decorator import widget
from picolet_tui._textual.app import App
from picolet_tui._textual.reactive import Reactive
from picolet_tui.widgets import Label, Button

@widget                                    # required, no metaclass
class Counter(App):
    count = Reactive(0)                    # capital R

    def compose(self):
        yield Label("0")
        yield Button("+1")

    def on_button_pressed(self):
        self.count += 1

    def watch_count(self, old, new):       # two-argument watcher
        self.query_one(Label).update(str(new))

Counter().run()
```

The four edits that always apply when porting:

1. Add `@widget` above every class that subclasses `App`, `Screen`, or
   `Widget`.
2. Rename `reactive(...)` to `Reactive(...)`.
3. Convert single-argument watchers to `(self, old, new)`.
4. Replace `@work` / `Worker` usage with `asyncio.create_task` on an
   `async def` method.

## Where to file feature-gap requests

Open issues at <https://github.com/andrewleech/picolet>. Tag with
`tui-v0.2` if the missing piece is on the planned list above, or
`tui-gap` if it is a divergence not documented here — divergences we
have not yet catalogued are bugs in this document.
