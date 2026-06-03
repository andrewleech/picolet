"""Reactive watcher contract (FR-TUI-19..22, FR-TUI-31).

Exercises the Phase 4b Reactive descriptor exhaustively:

  * watch_<name>(self, new) and watch_<name>(self, old, new) both fire
    correctly — arity is recorded at @widget decoration time and the
    right number of arguments is passed (FR-TUI-20).
  * compute_<name> short-circuits reads via the descriptor and
    re-evaluates when a dependency reactive is mutated (FR-TUI-21).
  * always_update=True fires the watcher on every assignment, even when
    new == old (FR-TUI-22).
  * layout=True invokes ``refresh(layout=True)`` so the compositor's
    layout pass is scheduled (FR-TUI-31).

The tests construct synthetic Widget subclasses rather than using the
v0.1 widget set — the surface under test is the descriptor, not any
particular concrete widget.
"""
from __future__ import annotations

from picolet_tui._textual._widget_decorator import widget
from picolet_tui._textual.reactive import Reactive
from picolet_tui._textual.widget import Widget


@widget
class _WatcherArity3(Widget):
    """Two-arg watcher (old, new) per FR-TUI-20."""

    count = Reactive(0)

    def __init__(self):
        Widget.__init__(self)
        self.calls = []

    def watch_count(self, old, new):
        self.calls.append((old, new))


@widget
class _WatcherArity2(Widget):
    """One-arg watcher (new) per FR-TUI-20."""

    name = Reactive("")

    def __init__(self):
        Widget.__init__(self)
        self.calls = []

    def watch_name(self, new):
        self.calls.append(new)


@widget
class _Computed(Widget):
    """compute_<name> dependent on a sibling Reactive (FR-TUI-21).

    Per the v0.1 wiring, ``compute_doubled`` is registered into
    ``_tui_widget_meta["computes"]`` at decoration time.  It is called
    directly here (rather than through descriptor read) because
    FR-TUI-21 forbids declaring both a Reactive(doubled) AND a
    compute_doubled — the test exercises the registered method and
    asserts the dependency re-fires it.
    """

    base = Reactive(10)

    def compute_doubled(self):
        return self.base * 2


@widget
class _AlwaysUpdate(Widget):
    """always_update=True fires watcher even on no-op write (FR-TUI-22)."""

    val = Reactive(0, always_update=True)

    def __init__(self):
        Widget.__init__(self)
        self.calls = []

    def watch_val(self, old, new):
        self.calls.append((old, new))


@widget
class _LayoutFlag(Widget):
    """layout=True triggers refresh(layout=True) (FR-TUI-31)."""

    height = Reactive(0, layout=True)


# ---------------------------------------------------------------------- tests


def test_watch_receives_old_and_new() -> None:
    """A 3-arity watcher receives (old, new) on every effective assignment."""
    w = _WatcherArity3()
    w.count = 5
    w.count = 7
    assert w.calls == [(0, 5), (5, 7)]


def test_watch_arity_one_receives_only_new() -> None:
    """A 2-arity watcher (just ``new``) is dispatched without the old value."""
    w = _WatcherArity2()
    w.name = "alpha"
    w.name = "beta"
    assert w.calls == ["alpha", "beta"]


def test_watcher_does_not_fire_on_equal_value() -> None:
    """Default (always_update=False) skips watcher when new == old.

    Pins the FR-TUI-22 contract: the descriptor's ``__set__`` returns
    early when the new value compares equal to the stored value, with
    no watcher invocation and no refresh.  always_update=True overrides;
    see test_always_update_fires_on_equal_value.
    """
    w = _WatcherArity3()
    w.count = 5
    w.count = 5  # second write is a no-op
    assert w.calls == [(0, 5)]


def test_compute_method_registered_in_meta() -> None:
    """@widget bucket-2 walk files compute_<name> under meta['computes']."""
    meta = _Computed._tui_widget_meta
    assert "doubled" in meta["computes"], "compute_doubled not registered"
    # The function object itself is the value the decorator stored.
    assert meta["computes"]["doubled"] is _Computed.__dict__["compute_doubled"]


def test_compute_dependency_re_evaluates() -> None:
    """compute_<name> sees fresh dep values when a reactive dep changes.

    The compute method is plain Python — it reads ``self.base`` every
    time it is called.  This test verifies the contract from the user's
    POV: bumping the dependency reactive and re-calling the compute
    yields a value derived from the new dep.
    """
    w = _Computed()
    assert w.compute_doubled() == 20
    w.base = 7
    assert w.compute_doubled() == 14
    w.base = -3
    assert w.compute_doubled() == -6


def test_always_update_fires_on_equal_value() -> None:
    """always_update=True fires watch_<name> on every assignment (FR-TUI-22).

    Without the flag the descriptor's ``old == new`` early-out elides
    the watcher; with the flag it runs unconditionally.
    """
    w = _AlwaysUpdate()
    w.val = 0  # equal to default
    w.val = 0  # equal again
    w.val = 1
    # Three writes, three watcher calls.  The first two are (0, 0); the
    # third is (0, 1) because the previous stored value is the default
    # 0 (always_update DOES still store-and-update, just doesn't elide).
    assert w.calls == [(0, 0), (0, 0), (0, 1)]


def test_layout_flag_triggers_refresh_with_layout() -> None:
    """Reactive(layout=True) calls refresh(layout=True) on assignment (FR-TUI-31).

    Widget.refresh is a stub in Phase 4b that flags _dirty; here we
    patch refresh on the instance so we can observe the exact kwarg
    flow.  The instance-level override shadows the class method and is
    what Reactive.__set__ ends up calling via ``getattr(instance,
    "refresh", None)``.
    """
    w = _LayoutFlag()
    captured = []

    def fake_refresh(*, layout=False, repaint=True):
        captured.append({"layout": layout, "repaint": repaint})

    w.refresh = fake_refresh
    w.height = 10
    assert len(captured) == 1, "refresh not called exactly once"
    assert captured[0]["layout"] is True, "layout flag did not propagate to refresh"


def test_non_layout_reactive_refreshes_without_layout_flag() -> None:
    """Reactive(layout=False) (default) calls refresh() with no layout kwarg.

    Counter to test_layout_flag_triggers_refresh_with_layout: when the
    layout flag is not set, the descriptor takes the bare ``refresh()``
    path, not the ``refresh(layout=True)`` path.  This pins the
    branching inside Reactive.__set__.
    """
    w = _WatcherArity3()
    captured = []

    def fake_refresh(*args, **kwargs):
        captured.append(kwargs)

    w.refresh = fake_refresh
    w.count = 3
    assert len(captured) == 1
    # bare refresh() — layout kwarg absent (or False), and the
    # descriptor's __set__ took the non-layout branch.
    assert captured[0].get("layout", False) is False
