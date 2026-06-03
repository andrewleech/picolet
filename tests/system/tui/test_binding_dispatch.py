"""Key event -> focused widget -> walk up to App -> fire action_*.

Spec coverage: FR-TUI-15 (Key event shape), FR-TUI-27 (BINDINGS table),
design doc §6.3 (the focused -> ancestors -> app walk).

The key-dispatch entry point is ``_textual._key_dispatch.dispatch_key``;
it consumes the focused widget set on the App and walks its parent chain.
Each test fabricates the relevant slice of the tree (focused widget +
maybe a parent + the App), posts a Key, and asserts the right action_*
method fired.

No event loop is required: dispatch_key is synchronous (the design doc
explicitly chose a sync API because v0.1 actions are not coroutines).
"""
from __future__ import annotations

from picolet_tui._textual._key_dispatch import Key, dispatch_key
from picolet_tui._textual._widget_decorator import widget
from picolet_tui._textual.app import App
from picolet_tui._textual.binding import Binding
from picolet_tui._textual.widget import Widget


def _make_key(key: str) -> Key:
    """Construct a Key event matching the binding key string.

    The dispatcher reads ``key_event.key`` only (FR-TUI-15); ``character``
    and ``modifiers`` are unused by ``_binding_matches`` for the simple
    case.  Modifier-prefixed key strings ("ctrl+q") go into ``key``
    directly per FR-TUI-18.
    """
    return Key(key=key, character=key if len(key) == 1 else None, modifiers=set())


def test_focused_widget_action_fires() -> None:
    """A binding on the focused widget invokes its action_* method.

    Walk order in dispatch_key is focused -> ancestors -> app; with no
    ancestors and a focused widget that owns the binding, the first
    candidate handles it and dispatch returns True.
    """
    fired = []

    @widget
    class _Focused(Widget):
        can_focus = True
        BINDINGS = [Binding("a", "do_a", "do a")]

        def action_do_a(self):
            fired.append("focused.do_a")

    @widget
    class _MyApp(App):
        pass

    app = _MyApp()
    focused = _Focused()
    focused._parent = app
    app.focused = focused

    result = dispatch_key(app, _make_key("a"))
    assert result is True, "dispatch_key returned False on a bound key"
    assert fired == ["focused.do_a"]


def test_unmatched_key_falls_off_the_top() -> None:
    """A key with no binding anywhere in the walk returns False (no action).

    Confirms the "exhausted" branch of dispatch_key: no fallback, no
    crash, just an empty-handed return.  User code uses the return
    value to feed the event back into the normal Message bubble in
    the design doc §6.3 contract.
    """
    fired = []

    @widget
    class _Focused(Widget):
        can_focus = True
        BINDINGS = [Binding("a", "do_a")]

        def action_do_a(self):
            fired.append("focused.do_a")

    @widget
    class _MyApp(App):
        pass

    app = _MyApp()
    focused = _Focused()
    focused._parent = app
    app.focused = focused

    result = dispatch_key(app, _make_key("z"))
    assert result is False
    assert fired == []


def test_binding_bubbles_to_app_when_widget_lacks_it() -> None:
    """A key bound only on the App fires the App's action_* (§6.3 walk).

    With the focused widget declaring no matching binding, the walk
    continues to the parent chain and finally hits the App-level
    BINDINGS list — exactly the "ctrl+q -> quit" path the App base
    class ships.
    """
    fired = []

    @widget
    class _Focused(Widget):
        # No BINDINGS — the walk must continue.  We still need a class
        # decoration to satisfy the FR-TUI-28 / R3 guard, even with an
        # empty meta contribution.
        can_focus = True
        BINDINGS = []

    @widget
    class _MyApp(App):
        BINDINGS = [Binding("q", "user_quit", "Quit")]

        def action_user_quit(self):
            fired.append("app.user_quit")

    app = _MyApp()
    focused = _Focused()
    focused._parent = app
    app.focused = focused

    result = dispatch_key(app, _make_key("q"))
    assert result is True
    assert fired == ["app.user_quit"]


def test_focused_takes_precedence_over_app() -> None:
    """When focused widget AND app both bind a key, focused wins (§6.3).

    The walk order is focused -> ancestors -> app, so the first node
    with both a matching binding and an action_* method wins.  Putting
    the same key on both the focused widget and the app exercises the
    "early return on first match" half of the dispatch contract.
    """
    fired = []

    @widget
    class _Focused(Widget):
        can_focus = True
        BINDINGS = [Binding("a", "do_a")]

        def action_do_a(self):
            fired.append("focused.do_a")

    @widget
    class _MyApp(App):
        BINDINGS = [Binding("a", "do_a")]

        def action_do_a(self):
            fired.append("app.do_a")

    app = _MyApp()
    focused = _Focused()
    focused._parent = app
    app.focused = focused

    dispatch_key(app, _make_key("a"))
    assert fired == ["focused.do_a"], "expected focused-wins ordering; got %r" % fired


def test_action_fallback_to_app_when_widget_action_missing() -> None:
    """If focused has the binding but no action_* method, the App's fires.

    dispatch_key first looks for ``getattr(node, "action_" + name)``;
    on miss it tries the App.  This pins the design doc §6.3 fall-
    through which lets a widget declare a binding key that delegates
    to an App-level action method.
    """
    fired = []

    @widget
    class _Focused(Widget):
        can_focus = True
        BINDINGS = [Binding("a", "do_a")]
        # NB: no action_do_a here — the fallback must reach the App.

    @widget
    class _MyApp(App):
        # The App carries the action but not the binding; the binding
        # lives on the focused widget and the action_* lookup falls
        # through to here.
        def action_do_a(self):
            fired.append("app.do_a")

    app = _MyApp()
    focused = _Focused()
    focused._parent = app
    app.focused = focused

    result = dispatch_key(app, _make_key("a"))
    assert result is True
    assert fired == ["app.do_a"]


def test_walk_traverses_intermediate_parent() -> None:
    """The walk visits parents of focused, not just focused-then-app.

    Tree: focused -> mid -> app.  Binding lives on ``mid``.  The walk
    must reach it; if the dispatcher skipped intermediate ancestors the
    key would fall through to the app and miss.
    """
    fired = []

    @widget
    class _Focused(Widget):
        can_focus = True
        BINDINGS = []

    @widget
    class _Mid(Widget):
        BINDINGS = [Binding("x", "do_x")]

        def action_do_x(self):
            fired.append("mid.do_x")

    @widget
    class _MyApp(App):
        pass

    app = _MyApp()
    mid = _Mid()
    focused = _Focused()
    focused._parent = mid
    mid._parent = app
    app.focused = focused

    result = dispatch_key(app, _make_key("x"))
    assert result is True
    assert fired == ["mid.do_x"]
