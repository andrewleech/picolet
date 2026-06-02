# Textual Porting Feasibility Assessment for MicroPython

Status: research note for picolet TUI investigation
Date: 2026-06-03
Subject: Textualize/textual @ main (Python 3.9+ required)

## Overview

Textual is a CPython-only TUI framework. It is buildable on top of `rich`,
`markdown-it-py`, `pygments`, `platformdirs`, and `typing-extensions`, and
sits on a heavy runtime introspection / metaclass / descriptor stack. The
core class machinery (DOMNode, Widget, MessagePump, Reactive, Styles, CSS
parser) leans on `cls.__mro__` walks inside `__init_subclass__`, a
`_MessagePumpMeta` metaclass, descriptor-protocol heavy CSS properties,
`contextvars.ContextVar`, `weakref` (Timer, MessagePump parent ref, App
registries), and `concurrent.futures.Future`. The Linux driver is built
on `termios`, `tty`, `selectors`, `signal`, and threads; the Windows
driver mirrors with `ctypes`/win32 API. The CSS subsystem is a ~20-file
tokenizer + recursive-descent parser driven by `re`.

A straight port is not realistic. The bigger blockers are not the asyncio
gap or even rich-segment rendering but the **class-construction-time
machinery**: `__init_subclass__`, `metaclass=_MessagePumpMeta`, the
descriptor protocol (`__set_name__`, `__get__`, `__set__`) used by both
`Reactive` and the ~24 CSS style-property descriptors, plus
`dir(cls)`/`cls.__dict__` introspection. MicroPython lacks
`__init_subclass__` and reliable metaclass support, which means the
existing source cannot be loaded as-is even before runtime semantics are
considered.

The right framing is "Textual-inspired", not "Textual port": take the
DOM + Widget + reactive + CSS-like styling concepts, reimplement them
with MicroPython-idiomatic class registration, and reuse only the small
leaves (geometry, color, easing, key-name tables) verbatim.

## Dependency tree

### Direct runtime deps (per textual `pyproject.toml`)

| Dep              | Version    | MicroPython portability                                    |
| ---------------- | ---------- | ----------------------------------------------------------- |
| rich             | >=14.2.0   | Out of scope. CPython-heavy, ~150 modules, depends on pygments, markdown-it-py, uses dataclasses, enums, locale, threading. The Segment/Console/Style pipeline is what `_compositor.py` is built around — see below. |
| markdown-it-py   | >=2.1.0    | Out of scope for runtime needs (only used by `MarkdownViewer` widget and Rich's markdown renderer). Can be excluded if the widget is dropped. |
| mdit-py-plugins  | *          | Same — drop with MarkdownViewer.                            |
| pygments         | ^2.19.2    | Out of scope. Only needed for syntax highlighting widgets. Drop. |
| platformdirs     | >=3.6.0    | Needs shim. Trivial — Textual uses it for the CSS cache dir; replace with a fixed flash path or skip caching. |
| typing-extensions| ^4.4.0     | Needs shim. MicroPython has no `typing_extensions`; `Self`, `TypeAlias` references must become `# type: ignore` strings under `from __future__ import annotations`-equivalent (MP ignores annotations at runtime anyway). |

### Transitive concerns

- `rich` pulls in pygments + markdown-it-py + ipywidgets(optional).
- markdown-it-py pulls mdurl, optionally linkify-it-py + uc-micro-py.
- None of these have MicroPython forks. The realistic answer is that
  picolet must replace rich's Segment/Console rendering with its own
  thin renderer (Cell + Style + ANSI emitter) rather than try to port
  rich.

## Core types

### MessagePump (`src/textual/message_pump.py`)

- Declared with `class MessagePump(metaclass=_MessagePumpMeta):`.
- `_MessagePumpMeta.__new__` walks `class_dict.values()`, finds callables
  with `_textual_on` attributes set by the `@on(...)` decorator, and
  builds `_decorated_handlers: dict[type[Message], list[(handler,
  selectors)]]`. It also scans for `compute_<name>` paired with reactive
  attributes and raises `TooManyComputesError` if both private and
  public computes coexist.
- Imports `weakref.WeakSet` and `weakref.ref`; the parent is stored as
  `ref(parent)` to break cycles.
- Uses `asyncio.Event`, `Task`, `create_task`, `CancelledError`,
  `QueueEmpty`, `current_task`.
- Threading: `threading` imported, used for thread-id tracking.

### DOMNode (`src/textual/dom.py`)

- `class DOMNode(MessagePump)` — inherits the metaclass.
- `__init_subclass__(cls, inherit_css=True, inherit_bindings=True,
  inherit_component_classes=True)` walks `reversed(cls.__mro__)`,
  iterates each base's `__dict__`, picks out `Reactive` descriptors,
  collects CSS type names from `_css_bases(cls)`, calls
  `cls._merge_bindings()`, and uses `dir(cls)` to discover
  `compute_*` / `_compute_*` methods.
- Defines `_ClassesDescriptor` (a `__get__`/`__set__` descriptor) — no
  `__set_name__` here.
- Uses `from inspect import getfile` to attribute CSS source locations.
- No dataclasses, no weakref, no importlib.resources at this level.

### Widget (`src/textual/widget.py`)

- `class Widget(DOMNode)` — inherits metaclass via DOMNode.
- Heavy ClassVar use: `DEFAULT_CSS`, `COMPONENT_CLASSES`,
  `_PSEUDO_CLASSES`.
- Reactives declared as class attributes: `expand: Reactive[bool] =
  Reactive(False)`, etc. — these are picked up by `__init_subclass__`.
- Defines `_BorderTitle` descriptor (with `__set_name__`/`__set__`/`__get__`).
- Heavy `@overload`, `TypeVar`, `Generic` usage, plus `typing_extensions.Self`.

### Reactive (`src/textual/reactive.py`)

- Full descriptor: `__get__`, `__set__`, `__set_name__`.
- `__set_name__` registers the attribute name and looks for
  `compute_<name>` / `_compute_<name>` / `validate_<name>` /
  `watch_<name>` siblings on the owner — this is the implicit binding
  the rest of the framework relies on.
- Uses `from inspect import isawaitable` and `textual._callback.count_parameters`
  (which uses `inspect.signature` and special-cases `partial` and bound
  methods).

### Message (`src/textual/message.py`)

- `__init_subclass__(cls, bubble=None, verbose=False, no_dispatch=None,
  namespace=None)` derives `cls.handler_name = f"on_{...}"` from the
  qualname via `camel_to_snake`. This is how `on_button_pressed` style
  dispatch works without explicit registration.

### Screen (`src/textual/screen.py`)

- `class Screen(Generic[ScreenResultType], Widget)` — relies on Widget's
  reactive/binding machinery via inheritance.
- Uses `asyncio.Future` for `ResultCallback`.
- No new metaclass tricks of its own — the inherited ones are the
  problem.

### App (`src/textual/app.py`)

- Imports a large surface area: `asyncio`, `importlib`, `inspect`,
  `signal`, `sys`, `threading`, `uuid`, `warnings`, `concurrent.futures.Future`,
  `weakref.WeakKeyDictionary`/`WeakSet`,
  `contextlib.asynccontextmanager`/`contextmanager`/`redirect_stderr`/
  `redirect_stdout`, `pathlib.Path`, `mimetypes`, `inspect.currentframe`,
  `inspect.getfile`, `inspect.isclass`.
- Driver is loaded dynamically via `importlib.import_module(...)`.
- Uses `asyncio.run(...)` and falls back to `loop.run_until_complete(...)`.
- `WeakKeyDictionary[MessageTarget, ...]` for callback tracking.

### Animator (`src/textual/_animator.py`)

- Uses `@dataclass` on `SimpleAnimation`, `asyncio.Event`, `abc.ABC`/
  `abstractmethod`, `typing_extensions.Protocol`/`runtime_checkable`.
- Timer-driven; no threading.

### Timer (`src/textual/timer.py`)

- `asyncio.Event`, `Task`, `create_task`, `gather`, `CancelledError`.
- Uses `weakref.ref` for the event target.

### Compositor (`src/textual/_compositor.py`)

- Built entirely around `rich.segment.Segment`, `rich.style.Style`,
  `rich.console.Console`, `rich.control.Control`. This is the rendering
  hot loop. Porting Textual without porting rich means rewriting this
  module against a picolet-native Cell/Style/ANSI primitives.

### CSS subsystem (`src/textual/css/*`, 20 files)

- `tokenizer.py`: ~350 lines, regex-driven (`re.compile`), `NamedTuple`
  for tokens. Portable in principle (MicroPython `re` is a subset but
  covers what's used).
- `parse.py`: ~500 lines recursive descent over the token stream.
- `_style_properties.py`: ~24 descriptor classes (ScalarProperty,
  ColorProperty, BoxProperty, BorderProperty, …) each implementing the
  full descriptor protocol with `__set_name__`.
- `styles.py`: one `@dataclass` (`Styles`), the rest is descriptor
  attributes (80+ style rules) and properties.
- `stylesheet.py`: uses `LRUCache`, `NamedTuple`, `defaultdict`,
  `itertools.chain`, `operator`. No dataclasses, no weakref, no
  importlib.resources.

### Drivers (`src/textual/drivers/*`)

- `linux_driver.py`: `termios`, `tty`, `selectors`, `signal`
  (`SIGTSTP`/`SIGCONT`/`SIGWINCH`/`SIGTTOU`/`SIGTTIN`),
  `os.read`/`os.kill`/`os.getpid`/`os.isatty`, `threading.Event`/`Thread`,
  `codecs.getincrementaldecoder`. Puts stdin in raw mode via
  `tcsetattr`. Manages alt-screen, mouse, bracketed-paste, Kitty
  keyboard protocol via ANSI sequences.
- `windows_driver.py` / `win32.py`: ctypes + win32 console API.
- `headless_driver.py`: pure Python (`asyncio`, `shutil.get_terminal_size`).
  This is the only driver that is realistic to lift.
- `web_driver.py`: protocol over stdio for textual-web; depends on the
  Linux driver primitives.

### Context (`src/textual/_context.py`)

- 5 `contextvars.ContextVar` instances (`active_app`,
  `active_message_pump`, `prevent_message_types_stack`,
  `visible_screen_stack`, `message_hook`). MicroPython has no
  `contextvars`.

## CPython-only constructs (concrete inventory)

1. **Metaclasses** — `_MessagePumpMeta` (`message_pump.py`). MicroPython
   does not honor custom metaclasses; `class Foo(metaclass=Meta):`
   parses but `Meta.__new__` is not invoked the same way. The handler
   table this builds is structural to the entire `@on(...)` dispatch
   mechanism.
2. **`__init_subclass__`** — on `DOMNode` (heavy: MRO walk, dir-scan,
   binding merge) and `Message` (handler-name derivation). MicroPython
   does not call `__init_subclass__`.
3. **Descriptor protocol with `__set_name__`** — `Reactive`, ~24 style
   property classes in `_style_properties.py`, plus `_BorderTitle` in
   widget. MicroPython supports `__get__`/`__set__` for plain class
   attributes but `__set_name__` is **not** called.
4. **`weakref`** — `WeakSet`, `WeakKeyDictionary`, `ref`. Used in App,
   MessagePump, Timer. No `weakref` module in MicroPython.
5. **`contextvars.ContextVar`** — used framework-wide in `_context.py`,
   and read by `Message.__init__` to capture the active message pump.
   No `contextvars` in MicroPython.
6. **`concurrent.futures.Future`** — App uses it. MicroPython has none.
7. **`asyncio.Future`** — Screen `ResultCallback` returns one. Not
   present in MicroPython asyncio.
8. **`inspect.signature` / `inspect.currentframe` / `inspect.getfile`** —
   `_callback.count_parameters`, App's CSS-source attribution. Not
   available in MicroPython.
9. **`importlib.import_module`** — driver loading. MicroPython has
   `__import__` but no full `importlib`; can be shimmed for known
   driver names.
10. **`contextlib.asynccontextmanager`** — used by `App.run_test()` and
    several widgets. MicroPython micropython-lib has
    `contextlib.contextmanager` but not the async variant out of the
    box.
11. **`signal.SIGWINCH` / `SIGTSTP` / `SIGCONT`** — Linux driver. Not
    on bare-metal targets (no concept). Resize comes from a different
    channel anyway on embedded.
12. **`termios` / `tty` / `selectors`** — Linux driver only. Embedded
    UART has none of these.
13. **`threading.Thread` / `threading.Event`** — Linux driver input
    reader thread, writer thread. MicroPython `_thread` exists on some
    ports but is not a drop-in.
14. **`pathlib.Path`** — pervasive. MicroPython has it as a thin shim;
    works for simple cases.
15. **`@dataclass`** — used sparingly: `Binding`, `Styles`,
    `SimpleAnimation`, a handful of `Event` subclasses
    (`DescendantFocus` etc.). micropython-lib has a limited
    `dataclasses` module — should cover the frozen+default-value uses
    here but `field(default_factory=...)` and `__post_init__` need
    spot-checks.
16. **`typing_extensions.Self` / `typing_extensions.Protocol` /
    `runtime_checkable`** — used in widget, animator. Annotations are
    ignored at MicroPython runtime so `Self` is cosmetic, but
    `runtime_checkable` Protocol is real machinery (used by animator
    for `Animatable`).
17. **`@overload`** — pervasive. Cosmetic at runtime; safe to delete.
18. **`Generic[T]` / `TypeVar`** — pervasive in class bases (Screen,
    Reactive). MicroPython tolerates this only because `Generic[T]` at
    class-definition time has minimal runtime effect via `typing` —
    MicroPython has `typing` only as stubs; subscripting at class-base
    position works in the upython `typing` shim but is fragile.
19. **`uuid`** — App uses it. MicroPython lacks `uuid` on most ports;
    use `os.urandom` + hex.
20. **`warnings`** — App imports it. Not in MicroPython; shim to noop.
21. **`mimetypes`** — App imports it. Not in MicroPython; only needed
    for serving static assets in textual-serve and can be dropped.

## Portability assessment

### Realistic strategy

The least-bad path is to **not port Textual**. Take the conceptual
layers Textual got right — DOM tree, reactive properties driven by class
attributes, declarative bindings, message bubbling, CSS-like styling
with cascade and specificity — and reimplement them with class-creation
machinery that fits MicroPython.

Concretely:

- Replace `__init_subclass__` and `_MessagePumpMeta` with an explicit
  `@widget` class decorator that scans `vars(cls)` once at decoration
  time and records reactives, handlers, bindings, computes, etc. into
  `cls._reactives`, `cls._decorated_handlers`, `cls._merged_bindings`.
- Replace `__set_name__` on descriptors with the same decorator pass:
  iterate `vars(cls).items()` and call `descriptor._bind(name, cls)`
  where `__set_name__` would have fired.
- Replace `weakref` with explicit lifecycle hooks (close/dispose). The
  cycles Textual breaks with `weakref.ref` (parent pointers, animation
  callbacks) are tractable with explicit teardown.
- Replace `contextvars` with a thread-local-equivalent module global —
  MicroPython is single-thread per scheduler so a plain module-level
  list-as-stack works.
- Replace `rich.segment`/`Console` with a picolet Cell + Style + ANSI
  emitter; reuse Textual's geometry, color parsing, easing, and key
  tables as-is.
- Replace the Linux driver with a UART/USB-CDC reader that emits the
  same `events.Key`/`MouseEvent`/`Resize` objects.

This frames the work as porting concepts and select leaves, not the
class hierarchy itself.

### Leaves that are nearly drop-in

- `textual/geometry.py` (Size/Region/Offset/Spacing) — pure-Python
  NamedTuples with arithmetic. Tiny.
- `textual/color.py` — pure parsing + math.
- `textual/keys.py` and `textual/_character_to_key` tables — data only.
- `textual/_easing.py` — pure math.
- `textual/css/scalar.py` — pure parsing.
- `textual/strip.py` — depends on rich.Segment; needs adaptation but
  algorithm is portable.

## Open questions

1. Does MicroPython `re` cover the Textual tokenizer's named-group +
   alternation patterns, or does the tokenizer need to be rewritten as a
   hand-rolled scanner? (`re-pcre` extension exists on some ports.)
2. Is the target an embedded device with a UART pseudo-terminal, a Unix
   port host, or a hybrid? The driver layer's shape changes entirely
   between these.
3. Does picolet want declarative TCSS at all, or is a Python-side style
   DSL (`Style(border=("solid", "red"))`) sufficient? Dropping the CSS
   parser deletes ~2500 lines and ~24 descriptor classes worth of
   complexity.
4. Does picolet need animation/transition support v1, or can the
   animator + `scalar_animation` be deferred? Animator drags in
   `runtime_checkable Protocol`.
5. Does picolet want the App's screen-stack/modal-screen semantics, or
   is one-screen-at-a-time acceptable? Screen subclassing uses
   `Generic[ScreenResultType]` + `asyncio.Future` for result return —
   both are CPython artifacts.
6. Threading model — does the target port have `_thread`? If not, the
   App's threaded worker support is out and `threading.get_ident()`
   needs replacing.
7. Markdown / syntax-highlighted code blocks in v1? If yes, that pulls
   in markdown-it-py + pygments which is a non-starter; need a
   minimal-markdown shim or drop these widgets.
