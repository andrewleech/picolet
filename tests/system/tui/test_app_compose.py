"""compose() yields the expected widget tree (FR-TUI-1, FR-TUI-13, FR-TUI-42, FR-TUI-46, FR-TUI-47).

Mirrors the hello-tui template's HelloApp shape:

    @widget
    class HelloApp(App):
        BINDINGS = [Binding("q", "quit", "quit")]
        def compose(self):
            yield Label("...")
            yield Input(placeholder="...")
            yield Button("Submit", id="submit")

The test instantiates that class shape (without running the event loop)
and asserts ``compose()`` yields one Label, one Input, and one Button in
that order, identified by type only (the implementation may attach a
Style or wrap the content). No TuiHarness is required: compose() is a
pure generator yielding fresh widget instances per call, and the App can
be constructed without enabling the tuiterm driver (FR-TUI-7 only runs
when ``run_async`` is awaited, not on ``__init__``).
"""
from __future__ import annotations

# Reach the real implementations directly.  picolet_tui.__init__ still
# carries the Phase 2a placeholder classes; the v0.1 surface is wired
# through the _textual subpackage and the widgets subpackage.  Pulling
# the real symbols out of those modules is what the runtime variant
# does at boot (the placeholders are dead code by Phase 4b's end).
from picolet_tui._textual.app import App
from picolet_tui._textual.binding import Binding
from picolet_tui._textual._widget_decorator import widget, on
from picolet_tui.widgets import Button, Input, Label


@widget
class _HelloApp(App):
    """Stand-in for the hello-tui template's HelloApp.

    Carrying BINDINGS + an @on handler exercises the bucket-3 / bucket-5
    paths of the @widget decorator at decoration time.  The class body
    matches the template at packages/picolet/picolet/templates/hello-tui/
    src/main.py, minus the ``query_one`` call inside on_submit (which is
    a Phase 4d API not required for the compose-shape test).
    """

    BINDINGS = [Binding("q", "quit", "quit")]

    def compose(self):
        # Label first, then Input, then Button — matches the template's
        # visual order and the FR-TUI-13 docstring's @on(Button.Pressed,
        # "#submit") binding target.
        yield Label("Hello from picolet-tui!")
        yield Input(placeholder="type here...")
        yield Button("Submit", id="submit")

    @on(Button.Pressed, "#submit")
    def on_submit(self, event):
        # Body unused in this test; presence exercises the @on decorator's
        # _tui_on attribute attachment + the @widget bucket-3 walk.
        pass


def test_compose_yields_three_widgets_in_order() -> None:
    """compose() yields Label, Input, Button — once, in that order."""
    app = _HelloApp()
    children = list(app.compose())
    assert len(children) == 3, "expected 3 yielded widgets, got %d" % len(children)
    assert isinstance(children[0], Label), "first child must be Label, got %s" % type(children[0]).__name__
    assert isinstance(children[1], Input), "second child must be Input, got %s" % type(children[1]).__name__
    assert isinstance(children[2], Button), "third child must be Button, got %s" % type(children[2]).__name__


def test_compose_is_repeatable() -> None:
    """Repeated compose() calls must produce fresh widget instances.

    compose() is a generator method — every invocation re-runs the body
    and yields *new* widget objects.  This matches upstream Textual: the
    same App can be re-mounted without leaking state from the previous
    compose pass.  Pinning the contract here catches accidental
    caching-at-class-scope refactors.
    """
    app = _HelloApp()
    first = list(app.compose())
    second = list(app.compose())
    assert len(first) == 3 and len(second) == 3
    # Identity must differ: a cached compose result would surface as
    # `first[i] is second[i]` for every i.
    for i, (a, b) in enumerate(zip(first, second)):
        assert a is not b, "compose() returned shared instance at index %d" % i
        assert type(a) is type(b), "compose() returned different types at index %d" % i


def test_app_carries_bindings_and_on_handler() -> None:
    """@widget on the App captured BINDINGS and the @on(Button.Pressed) handler.

    Validates the FR-TUI-13 / FR-TUI-57 contract: the class-time decorator
    pass populated _tui_widget_meta with the BINDINGS list and the
    @on-decorated handler — no runtime introspection happens at compose()
    time, so the metadata must already be in place after class definition.
    """
    meta = _HelloApp._tui_widget_meta

    # BINDINGS — the user's "q" -> quit plus the inherited ctrl+q -> quit
    # from App.  Both must be present; subclass-wins / extend semantics
    # is verified by the dedicated test_binding_dispatch.py file.
    bindings_keys = [b.key for b in meta["bindings"]]
    assert "q" in bindings_keys, "user BINDINGS entry missing from meta"
    # @on(Button.Pressed, "#submit") — the handler must be filed under
    # the Button.Pressed message class in meta["handlers"].
    pressed_handlers = meta["handlers"].get(Button.Pressed, ())
    assert len(pressed_handlers) == 1, "expected 1 Button.Pressed handler, got %d" % len(pressed_handlers)
    handler_fn, selector = pressed_handlers[0]
    assert handler_fn.__name__ == "on_submit"
    # Selector carries the "#submit" id query (FR-TUI-13).
    assert selector.selector == "#submit" or getattr(selector, "query", None) == "#submit"
