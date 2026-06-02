"""picolet_tui — Textual-inspired TUI framework, frozen into the picolet-tui runtime.

See docs/tui/tui-v0.1-spec.md for the v0.1 surface area and FR/NFR ids
referenced throughout this package.  See docs/tui/research/ (00-synthesis.md
plus 01..04) for the Phase 0 investigations that motivated the rewrite
(rather than port) decision and pin the build flags + C boundary.

Public surface (Phase 4-5 will fill the classes in; Phase 2a ships
placeholders so downstream imports type-check and the freezer has a
non-empty package to walk).

  App, Screen, Widget         — lifecycle + DOM (FR-TUI-1..6, 23..28)
  Static, Label, Container,   — v0.1 widgets, D3 / FR-TUI-41..52
  Vertical, Horizontal,
  Button, Input, Stack,
  ProgressBar

  widget                       — class decorator (FR-TUI-57, D1)
  on                           — handler decorator (FR-TUI-13)
  Reactive                     — descriptor (FR-TUI-19..22)
  Message                      — base event type (FR-TUI-12)
  Binding                      — keymap entry
  Style                        — Python-side style DSL (FR-TUI-32..37, D2)

The _shims subpackage installs stdlib substitutes (dataclasses, typing,
enum, functools, weakref, contextlib, callback) into sys.modules; it must
import before any other picolet_tui module so downstream imports resolve.
Phase 2b populates it; this file imports it eagerly so the registration
ordering is locked in from day one.
"""

from . import _shims  # noqa: F401  — side-effect import; registers shims in sys.modules


class Widget:
    """Placeholder for FR-TUI-23..28; Phase 4b fills in MessagePump, mount, focus."""


class App:
    """Placeholder for FR-TUI-1..6; Phase 4b fills in run/run_async and the asyncio.gather pump."""


class Screen(Widget):
    """Placeholder for FR-TUI-50 backing class; Phase 4b fills in dismiss + screen-stack semantics."""


class Static(Widget):
    """Placeholder for FR-TUI-41."""


class Label(Static):
    """Placeholder for FR-TUI-42."""


class Container(Widget):
    """Placeholder for FR-TUI-43."""


class Vertical(Container):
    """Placeholder for FR-TUI-44."""


class Horizontal(Container):
    """Placeholder for FR-TUI-45."""


class Button(Widget):
    """Placeholder for FR-TUI-46."""


class Input(Widget):
    """Placeholder for FR-TUI-47..49."""


class Stack(Widget):
    """Placeholder for FR-TUI-50."""


class ProgressBar(Widget):
    """Placeholder for FR-TUI-51."""


class Message:
    """Placeholder for FR-TUI-12; Phase 4b fills bubbling + stop()."""


class Binding:
    """Placeholder; Phase 4b fills BINDINGS coercion."""


class Reactive:
    """Placeholder for FR-TUI-19..22; Phase 4b fills the descriptor + watch dispatch."""


class Style:
    """Placeholder for FR-TUI-32..37; Phase 4b fills the DSL surface."""


def widget(cls):
    """Placeholder for FR-TUI-57 / synthesis D1.  Phase 4b fills in the
    vars(cls) walk that populates cls._tui_widget_meta and replaces
    __set_name__ on Reactive descriptors.
    """
    cls._tui_widget_registered = True
    return cls


def on(message_type, selector=None):
    """Placeholder for FR-TUI-13.  Phase 4b fills in the _tui_on metadata
    that the @widget decorator collects into meta['handlers'].
    """
    def _decorator(fn):
        return fn
    return _decorator


__all__ = (
    "App",
    "Screen",
    "Widget",
    "Static",
    "Label",
    "Container",
    "Vertical",
    "Horizontal",
    "Button",
    "Input",
    "Stack",
    "ProgressBar",
    "Message",
    "Binding",
    "Reactive",
    "Style",
    "widget",
    "on",
)
