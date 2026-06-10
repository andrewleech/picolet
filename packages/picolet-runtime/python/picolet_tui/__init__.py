"""picolet_tui — Textual-inspired TUI framework, frozen into the picolet-tui runtime.

See docs/tui/tui-v0.1-spec.md for the v0.1 surface area and FR/NFR ids
referenced throughout this package.  See docs/tui/research/ (00-synthesis.md
plus 01..04) for the Phase 0 investigations that motivated the rewrite
(rather than port) decision and pin the build flags + C boundary.

Public surface re-exported from the implementation submodules:

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
  Style                        — Rich-subset style (FR-TUI-32..37, D2)

The _shims subpackage installs stdlib substitutes (dataclasses, typing,
enum, functools, weakref, contextlib, callback) into sys.modules; it
must import before any other picolet_tui module so downstream imports
resolve.  The import is eager so the registration ordering is locked
in.
"""

from . import _shims  # noqa: F401  — side-effect: registers shims in sys.modules

from ._textual.app import App
from ._textual.binding import Binding
from ._textual.message import Message, on
from ._textual.reactive import Reactive
from ._textual.screen import Screen
from ._textual.widget import Widget
from ._textual._widget_decorator import widget
from ._rich.style import Style
from .widgets import (
    Button,
    Container,
    Horizontal,
    Input,
    Label,
    ProgressBar,
    Stack,
    Static,
    Vertical,
)


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
