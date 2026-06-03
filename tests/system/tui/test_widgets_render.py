"""All nine v0.1 widgets instantiate and render to a non-None renderable.

Spec coverage: FR-TUI-41 (Static), FR-TUI-42 (Label), FR-TUI-43 (Container),
FR-TUI-44 (Vertical), FR-TUI-45 (Horizontal), FR-TUI-46 (Button), FR-TUI-47
(Input), FR-TUI-50 (Stack), FR-TUI-51 (ProgressBar).

This is a contract-validation pass — no terminal, no layout, no compositor.
Every widget's ``render()`` method must return a renderable shape (a Rich
``Text``, a ``str``, or any object exposing ``__rich_console__``) per
design doc §7.1.  None would mean the compositor's render_lines call
crashes with a TypeError downstream; ruling it out at the unit-test
layer is cheaper than catching it in a TuiHarness smoke run.
"""
from __future__ import annotations

import pytest

from picolet_tui.widgets import (
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


# Test data: (widget_class, constructor_args_callable).  A callable is
# used so each parameterised case instantiates a fresh widget — sharing
# instances across parameterised assertions would pollute state between
# the .render() calls.
_WIDGET_CASES = [
    ("Static", lambda: Static("hello")),
    ("Label", lambda: Label("hello")),
    ("Container", lambda: Container()),
    ("Vertical", lambda: Vertical()),
    ("Horizontal", lambda: Horizontal()),
    ("Button", lambda: Button("Click")),
    ("Input", lambda: Input(value="")),
    ("Stack", lambda: Stack()),
    ("ProgressBar", lambda: ProgressBar(total=100)),
]


@pytest.mark.parametrize("name,factory", _WIDGET_CASES, ids=[c[0] for c in _WIDGET_CASES])
def test_widget_render_returns_non_none(name: str, factory) -> None:
    """Every v0.1 widget's render() must return a non-None renderable.

    The compositor's contract (design doc §7.1) is that render() returns
    one of: ``str``, ``_rich.text.Text``, or any object exposing
    ``__rich_console__``.  ``None`` is not on that list — it would crash
    ``Console.render`` in the compositor.
    """
    w = factory()
    rendered = w.render()
    assert rendered is not None, "%s.render() returned None" % name


@pytest.mark.parametrize("name,factory", _WIDGET_CASES, ids=[c[0] for c in _WIDGET_CASES])
def test_widget_has_widget_meta(name: str, factory) -> None:
    """Every concrete widget class is @widget-decorated (FR-TUI-28 / R3).

    The runtime guard in Widget.__init__ raises MissingWidgetDecoratorError
    when the direct class lacks _tui_widget_registered; instantiating the
    widget through the factory above implicitly exercises that guard.
    A separate assertion on _tui_widget_meta makes the contract explicit.
    """
    w = factory()
    cls = type(w)
    meta = getattr(cls, "_tui_widget_meta", None)
    assert meta is not None, "%s class missing _tui_widget_meta (forgot @widget)" % name
    # Sanity: every meta dict carries the four bucket keys the @widget
    # decorator populates.  An empty list/dict for a bucket is fine; the
    # key being absent is not.
    for key in ("reactives", "handlers", "bindings"):
        assert key in meta, "%s meta missing %r key" % (name, key)


def test_static_content_reactive_present() -> None:
    """Static has a content Reactive declared (FR-TUI-41).

    Static's content is the canonical Reactive in the v0.1 widget set;
    every other content-bearing widget routes its mutation through this
    descriptor.  The presence check guards against a regression where
    the Reactive declaration is moved off the class body.
    """
    assert "content" in Static._tui_widget_meta["reactives"]


def test_button_press_binding_present() -> None:
    """Button's BINDINGS list contains the ``enter`` -> ``press`` mapping (FR-TUI-46)."""
    bindings = Button._tui_widget_meta["bindings"]
    keys = [b.key for b in bindings]
    assert "enter" in keys, "Button missing 'enter' binding; got %r" % keys


def test_input_value_reactive_present() -> None:
    """Input has a ``value`` Reactive (FR-TUI-47).

    The Input widget's primary state surface is the ``value`` reactive
    — every assignment fires Changed(value=...) and every Submit posts
    Submitted(value=...).  Verifying the descriptor is registered on
    the class pins the Phase 4b wiring.
    """
    assert "value" in Input._tui_widget_meta["reactives"]


def test_progress_bar_progress_reactive_present() -> None:
    """ProgressBar has a ``progress`` Reactive (FR-TUI-51).

    FR-TUI-51 explicitly forbids an ``advance(n)`` method in v0.1 — the
    public mutation surface is bare and augmented assignment on the
    ``progress`` Reactive.  Verifying it is registered is the test-
    side guarantee that the augmented-assignment path resolves.
    """
    assert "progress" in ProgressBar._tui_widget_meta["reactives"]
