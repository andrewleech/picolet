"""picolet_tui.widgets - the v0.1 widget set.

Each module under this package contributes one widget class
(Static, Label, Container, Vertical, Horizontal, Button, Input,
Stack, ProgressBar) per FR-TUI-41..51.  This ``__init__`` re-exports
them so the canonical user-facing import is::

    from picolet_tui.widgets import Static, Label, Button  # etc.

Why a sub-package rather than nine top-level modules: keeps the
public ``picolet_tui`` surface (App, Widget, @widget, Reactive...)
distinct from the concrete widget classes that build on it.  The
upstream Textual layout uses the same ``textual.widgets`` package
boundary; matching it minimises the migration delta for users
coming from upstream.

Phase 5 lands the widgets one at a time; each addition extends this
re-export list rather than replacing it.
"""

# Static is the renderable-host base class - every other v0.1 widget
# extends it (Label) or sits beside it (Container, Button, etc).
# Re-exported here so ``from picolet_tui.widgets import Static`` is
# the single import path users learn.
from .static import Static

# Label is the single-line Static subclass with an optional Style
# overlay (FR-TUI-42).  Imported next to Static because it inherits
# from it and shares the markup/content pipeline; the truncation hint
# baked into Label.render() is what makes it a "line" not a "block".
from .label import Label

# Vertical is the row-axis directional Container subclass (FR-TUI-44).
# Re-exported here so user code reaches it via the canonical
# ``from picolet_tui.widgets import Vertical`` path.
from .vertical import Vertical

# Stack is the screen-pile widget (FR-TUI-50) - holds an ordered set
# of children and renders exactly one at a time.  Push/pop discipline
# plus an ``active`` Reactive for index-based switching.
from .stack import Stack

# Horizontal is the column-axis directional Container subclass
# (FR-TUI-45).  Re-exported here so user code reaches it via the
# canonical ``from picolet_tui.widgets import Horizontal`` path.
from .horizontal import Horizontal

# Container is the non-directional grouping widget (FR-TUI-43); it
# holds children and renders nothing of its own.  Re-exported so the
# canonical user import is ``from picolet_tui.widgets import Container``.
from .container import Container

# Button (FR-TUI-46) - the first interactive widget; extends Static for
# the renderable-host plumbing and adds focus + enter-binding + Pressed.
from .button import Button

# ProgressBar (FR-TUI-51) - non-focusable percentage indicator with a
# reactive progress slot, Unicode-block bar, optional percentage / ETA
# fields, and an ASCII ``#`` fallback for mono colour systems.
from .progress_bar import ProgressBar

# Input (FR-TUI-47..49) - single-line text entry with a movable caret,
# Submitted/Changed messages, paste handling, and password masking.
from .input import Input


__all__ = ("Static", "Label", "Vertical", "Stack", "Horizontal", "Container", "Button", "ProgressBar", "Input")
