"""Message bubbling reaches every ancestor's @on handler (FR-TUI-12, FR-TUI-68).

Builds a leaf -> mid -> root tree, posts a Message at the leaf, and
asserts that each level's @on-decorated handler fires in DOM order
(originating node first, then up the parent chain).

Also covers the FR-TUI-12 stop() contract: an early ancestor that calls
``message.stop()`` halts bubbling — no further parents see the message.

Uses pytest-asyncio because MessagePump.dispatch is a coroutine.  The
conftest documents the install step (uv sync picks pytest-asyncio up
from pyproject.toml's [dependency-groups].dev table).
"""
from __future__ import annotations

import pytest

from picolet_tui._textual._widget_decorator import on, widget
from picolet_tui._textual.message import Message
from picolet_tui._textual.widget import Widget


# ---------------------------------------------------------------------- fixtures


class _ClickEvent(Message):
    """Plain data-carrying Message subclass.

    Per message.py's docstring, message subclasses that only carry data
    do NOT need @widget — the decorator scans the *handler-owning*
    class (the widget receiving the message), not the message class.
    """


def _build_chain(handler_factory):
    """Build a leaf -> mid -> root chain of Widget instances.

    ``handler_factory`` is a callable that registers an @on(_ClickEvent)
    handler on a fresh class.  Returns (leaf, mid, root, fired_list).
    The fired_list captures the order handlers ran in so the test can
    assert against it.
    """
    fired = []

    @widget
    class _Leaf(Widget):
        @on(_ClickEvent)
        def h(self, event):
            fired.append("leaf")

    @widget
    class _Mid(Widget):
        @on(_ClickEvent)
        def h(self, event):
            fired.append("mid")

    @widget
    class _Root(Widget):
        @on(_ClickEvent)
        def h(self, event):
            fired.append("root")

    leaf = _Leaf()
    mid = _Mid()
    root = _Root()
    leaf._parent = mid
    mid._parent = root
    handler_factory(_Leaf, _Mid, _Root)
    return leaf, mid, root, fired


# ---------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_bubble_visits_every_ancestor_in_order() -> None:
    """A message posted at the leaf reaches mid then root in DOM order.

    FR-TUI-12 + FR-TUI-68: bubbling walks the DOM upward, firing each
    level's @on-decorated handler.  Order is originating-node first,
    then parent, then grandparent.
    """
    fired = []

    @widget
    class _Leaf(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("leaf")

    @widget
    class _Mid(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("mid")

    @widget
    class _Root(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("root")

    leaf = _Leaf()
    mid = _Mid()
    root = _Root()
    leaf._parent = mid
    mid._parent = root

    # _dispatch is the bubbling walk per design doc §3.4.  It is awaited
    # because handlers may be async; the @on handlers here are sync, but
    # the dispatch coroutine is the same.
    msg = _ClickEvent()
    await leaf._dispatch(msg)

    assert fired == ["leaf", "mid", "root"], "bubbling order wrong: %r" % fired


@pytest.mark.asyncio
async def test_stop_halts_propagation() -> None:
    """A handler that calls message.stop() prevents further bubbling.

    The mid-level handler stops the event; the root handler must not
    fire.  This is the FR-TUI-12 contract: ``stop()`` halts immediately
    and no parent is visited.
    """
    fired = []

    @widget
    class _Leaf(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("leaf")

    @widget
    class _Mid(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("mid")
            event.stop()

    @widget
    class _Root(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("root")

    leaf = _Leaf()
    mid = _Mid()
    root = _Root()
    leaf._parent = mid
    mid._parent = root

    msg = _ClickEvent()
    await leaf._dispatch(msg)

    assert fired == ["leaf", "mid"], "stop() did not halt bubbling: %r" % fired
    assert "root" not in fired


@pytest.mark.asyncio
async def test_bubble_skips_node_with_no_handler() -> None:
    """An intermediate node without a handler is silently walked through.

    Tree: leaf (handler) -> mid (no handler) -> root (handler).  The
    walk visits all three; the middle node's meta has no entry for
    _ClickEvent so dispatch is a one-dict-miss no-op, then continues.
    """
    fired = []

    @widget
    class _Leaf(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("leaf")

    @widget
    class _Mid(Widget):
        # No handlers — the dispatch walk passes through unchanged.
        pass

    @widget
    class _Root(Widget):
        @on(_ClickEvent)
        def handle(self, event):
            fired.append("root")

    leaf = _Leaf()
    mid = _Mid()
    root = _Root()
    leaf._parent = mid
    mid._parent = root

    msg = _ClickEvent()
    await leaf._dispatch(msg)

    assert fired == ["leaf", "root"]


@pytest.mark.asyncio
async def test_name_dispatched_on_event_fires_after_on_decorated() -> None:
    """FR-TUI-14: ``on_<msg_name>`` fires *after* @on-decorated handlers.

    A class that declares both an @on(MyEvent) handler and an on_my_event
    method (the snake-cased fallback name) should see both fire, with
    @on first and on_<name> second.  Pins the dispatcher's ordering at
    a single node.
    """
    # Use a no-underscore class so camel_to_snake yields a clean
    # ``my_event`` rather than ``_my_event`` (which would require the
    # handler to be named ``on__my_event``; ugly).
    class MyEvent(Message):
        pass

    fired = []

    @widget
    class _Leaf(Widget):
        @on(MyEvent)
        def decorated(self, event):
            fired.append("@on")

        def on_my_event(self, event):
            fired.append("name")

    leaf = _Leaf()
    msg = MyEvent()
    await leaf._dispatch(msg)

    # @on entries fire in registration order; the name-based fallback
    # fires after them per FR-TUI-14.
    assert fired == ["@on", "name"], "name-based fallback ordering wrong: %r" % fired
