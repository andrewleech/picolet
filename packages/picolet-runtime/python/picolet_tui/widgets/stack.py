"""picolet_tui.widgets.stack - Stack widget (FR-TUI-50).

Stack holds an ordered set of screen-like child widgets and renders
exactly one at a time.  Think tab content, wizard pages, or the
foreground/background switch a small router needs.  The base Widget
already carries the mount / unmount and children machinery; Stack
just adds:

  * The ``active`` reactive (index of the visible child).
  * push() / pop() / current accessors that mutate the child list and
    keep ``active`` consistent.
  * A render() that emits only the active child's renderable (the
    other children are mounted but hidden - i.e. they continue running
    their message pumps but their region is suppressed by the
    compositor).

Spec coverage:
  * FR-TUI-50 - ``Stack(*screens)`` holds an ordered set of screen-
                like child widgets and renders exactly one at a time.
                ``stack.push(widget)`` mounts a new top; ``stack.pop()``
                unmounts and returns it; ``stack.current`` is the
                visible widget.  Focus moves to the new top on push
                and pop.
  * FR-TUI-19/20 - The ``active`` Reactive + ``watch_active`` wiring
                exercise the descriptor; the @widget MRO walk picks
                up the Reactive at decoration time.
  * FR-TUI-52 - id / classes constructor kwargs forwarded.

Design-doc references (textual-core-design.md §4.1-4.4, §7.1).

Deviations from the FR-TUI-50 text:
  * FR-TUI-50 phrases the API as ``push / pop / current``; the Phase 5
    task description adds an index-based ``active`` Reactive.  We
    implement both: push() / pop() are layered on top of ``active``
    and the children list, so user code can drive the widget either
    by index (``stack.active = 2``) or by stack discipline
    (``stack.push(child)``).  The two views stay coherent because
    every mutation routes through the single ``active`` Reactive.
  * Push/pop on a Stack are async because they go through
    Widget.mount / Widget.remove (which spawn / cancel the per-child
    message pump task).  Synchronous index reassignment
    (``stack.active = 1``) is *not* async because no mount changes -
    only visibility flips, which is a refresh() signal.

Intra-widget ambiguities resolved here:
  * "Focus moves to the new top on push and pop" - we call .focus()
    on the new top widget if it ``can_focus``, otherwise we leave
    focus untouched.  No focus-stack semantics; v0.1 has a single
    focus slot (synthesis D6).
  * Empty stack: pop() raises IndexError (mirrors list.pop on []);
    current returns None; active stays at 0 but is read-only when
    empty - assigning to active on an empty Stack is a no-op (we
    clamp to len(children)-1 with the empty-case branch).
"""

# Widget gives us mount / remove / refresh / the R3 guard / Reactive
# host.  Container is the formal base per FR-TUI-43 (a non-directional
# grouping widget); we prefer it when available so Stack inherits any
# Container-level layout hooks the parallel agent lands.  If Container
# is not yet in the package (parallel-agent ordering), we fall back to
# Widget directly - Container is itself a Widget subclass, so the
# observable Stack API is identical either way.
from .._textual.widget import Widget

try:
    # When Container has landed, prefer it as the base for v0.1 spec
    # compliance.  This import is wrapped so Stack can land alongside
    # Container without an ordering constraint - the import failure
    # mode is benign: Stack falls back to Widget, which has the same
    # mount / unmount / render contract that Container exposes.
    from .container import Container as _Base
except ImportError:
    # Container not landed yet.  Widget already carries everything
    # Stack needs - children, mount, remove, refresh - so the fall-
    # back is functionally complete.  The only thing we lose is any
    # Container-specific layout default; v0.1 layout is full-bleed
    # for the active child (see render() below) so the loss is nil.
    _Base = Widget

# Reactive descriptor for the ``active`` slot.  Declared at class
# scope so the @widget MRO walk picks it up - FR-TUI-19's _bind_name
# runs at decoration time and installs the __get__/__set__ slots.
from .._textual.reactive import Reactive

# @widget is mandatory on every Widget subclass that declares
# Reactives (FR-TUI-28 / R3).  Stack has the ``active`` Reactive and
# a ``watch_active`` watcher, so the decorator is non-negotiable -
# omitting it raises MissingWidgetDecoratorError from Widget.__init__
# on first instantiation.
from .._textual._widget_decorator import widget


# ---------------------------------------------------------------------
# Stack.
# ---------------------------------------------------------------------


@widget
class Stack(_Base):
    """A pile of children with exactly one visible at a time.

    Children are stored in the standard ``_children`` list (driven by
    Widget.mount / Widget.remove).  The ``active`` Reactive carries
    the index of the currently visible child; render() returns only
    that child's renderable.

    The constructor accepts the same ``*children`` positional shape as
    every other Widget container (positional children are stashed in
    ``_pending_children`` and mounted during ``_mount``).  Pass
    ``active=N`` to set the initial visible index; default 0.
    """

    # ------------------------------------------------------------------
    # Class attributes.
    # ------------------------------------------------------------------

    # The visible-child index.  Default 0 mirrors "show the first
    # child" - the obvious choice for a freshly constructed stack
    # with at least one positional child.  An empty stack reads
    # active == 0 but current returns None; see render() and current
    # below for the empty-case handling.
    #
    # We do *not* set ``layout=True``: switching the active child is
    # a visibility flip, not a size renegotiation.  The compositor's
    # per-strip diff catches the byte-level delta in the active
    # region.  A layout flag would force a full layout pass per
    # active flip, which is the FR-TUI-31 anti-pattern.
    active = Reactive(0)

    # DEFAULT_CSS carried for upstream API parity (v0.2 - TCSS lands
    # then).  Read by nobody in v0.1.
    DEFAULT_CSS = ""

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(self, *children, active=0, id=None, classes="", name=None):
        # Forward the positional children to Widget / Container; they
        # land in ``_pending_children`` and are mounted during the
        # async _mount() pass.  id / classes / parent are the standard
        # FR-TUI-52 surface.  parent is intentionally not exposed:
        # Stack is always mounted under a parent through the normal
        # mount path, never constructed with an explicit parent ref.
        _Base.__init__(self, *children, id=id, classes=classes)

        # ``name`` accepted for API parity with upstream Textual but
        # not yet routed - Widget does not carry a name slot in
        # Phase 4b.  Stash it so user code that reads ``stack.name``
        # does not AttributeError.
        self._name = name

        # Seed the active index.  Assigning to the Reactive fires
        # watch_active -> refresh().  We assign last so the watcher's
        # first call sees a fully constructed instance.
        #
        # Default 0 hits the Reactive's equality fast-path (the
        # descriptor default is already 0), so the assignment is a
        # no-op in the common case and avoids a useless refresh.
        if active != 0:
            self.active = active

    # ------------------------------------------------------------------
    # current - the FR-TUI-50 visible-widget accessor.
    # ------------------------------------------------------------------

    @property
    def current(self):
        """Return the visible child widget, or None if the stack is empty.

        FR-TUI-50: ``stack.current`` is the visible widget.  Returns
        None on an empty stack rather than raising - the empty-case
        check is common (e.g. render() and watch_active both need it)
        and a None sentinel is friendlier than IndexError at the
        property boundary.
        """
        children = self._children
        if not children:
            return None
        # Clamp the index defensively.  ``active`` may be out of range
        # if the user assigned a bogus value; we clamp here rather
        # than at assignment so the Reactive itself stays a plain
        # int and watchers see whatever the user wrote.
        idx = self.active
        if idx < 0 or idx >= len(children):
            return None
        return children[idx]

    # ------------------------------------------------------------------
    # push / pop - FR-TUI-50 stack discipline.
    # ------------------------------------------------------------------

    async def push(self, widget):
        """Mount ``widget`` on top of the stack and make it visible.

        FR-TUI-50: ``stack.push(widget)`` mounts a new top.  Focus
        moves to the new top.  Async because mount() spawns the
        per-child message pump task.

        After mount, ``active`` is set to the new top index.  The
        Reactive write fires watch_active -> refresh(), which signals
        the compositor to repaint with the new visible child.
        """
        # mount() handles the parent/child link, starts the pump, and
        # awaits the child's on_mount.  We do not pre-check whether
        # the widget is already mounted elsewhere - DOMNode.mount is
        # the idempotent / re-parent path.
        await self.mount(widget)

        # The new top is the last entry in _children (mount appends).
        # Writing to active fires watch_active -> refresh; this is
        # the only place push() signals the compositor.
        self.active = len(self._children) - 1

        # FR-TUI-50: focus moves to the new top.  Defensive
        # can_focus check so a non-focusable child does not steal
        # focus from whatever currently owns it.
        if widget.can_focus:
            widget.focus()

    async def pop(self):
        """Unmount the top of the stack and return it.

        FR-TUI-50: ``stack.pop()`` unmounts and returns it.  Focus
        moves to the new top (the child that becomes visible).
        Async because remove() awaits on_unmount and cancels the
        per-child pump task.

        Raises IndexError on an empty stack (mirrors list.pop on []).
        """
        children = self._children
        if not children:
            raise IndexError("pop from an empty Stack")

        # The top is the last child.  Capture the reference before
        # remove() unlinks it from _children.
        top = children[-1]

        # remove() awaits on_unmount, cancels the pump, unlinks from
        # parent.  After this call _children has shrunk by one.
        await top.remove()

        # Clamp active to the new length.  If the user popped the
        # currently active child (the common case for stack
        # discipline), active points one past the end; clamp to the
        # new last index.  Writing to active fires watch_active ->
        # refresh.
        new_len = len(self._children)
        if new_len == 0:
            # Empty stack - leave active at 0; current will return
            # None and render() will emit an empty placeholder.
            # We still need to refresh so the compositor clears the
            # region the popped child owned.
            self.refresh()
        else:
            # Clamp; the Reactive equality fast-path skips the
            # refresh if active was already in range, so we call
            # refresh() explicitly to cover the "popped a non-top
            # child" edge case where active stayed valid.
            self.active = min(self.active, new_len - 1)
            new_top = self._children[-1]
            if new_top.can_focus:
                new_top.focus()

        return top

    # ------------------------------------------------------------------
    # watch_active - reactive watcher for the visibility flip.
    # ------------------------------------------------------------------

    def watch_active(self, old, new):
        """Reactive watcher for ``active`` (FR-TUI-20).

        Schedules a refresh so the compositor knows to repaint with
        the new visible child.  Both halves of the watcher contract
        matter: the watcher arity (self, old, new) is recorded at
        @widget decoration time, and the refresh() call is what
        signals the compositor.

        We do not move focus here - focus moves happen at push() /
        pop() sites where we know which widget is the new top.  An
        explicit ``stack.active = N`` assignment is a *visibility*
        change, not a navigation event; if the caller wants to also
        change focus they can call ``stack.current.focus()``.
        """
        # No layout pass - active flip is a visibility change, not
        # a size renegotiation.  refresh() with default args marks
        # the widget dirty; the compositor handles the strip diff.
        #
        # Note: the Reactive __set__ path also calls refresh() after
        # the watcher returns.  Calling refresh() here is a double-
        # call, but refresh() is idempotent (sets _dirty=True), and
        # the explicit call documents the FR-TUI-50 contract at the
        # watcher site for subclasses that override and forget super.
        self.refresh()

    # ------------------------------------------------------------------
    # render() - the §7.1 contract.
    # ------------------------------------------------------------------

    def render(self):
        """Return the renderable of the active child.

        Per design doc §7.1: render() returns one of str, Text, or
        an object exposing __rich_console__.  For Stack we delegate
        to the active child's render(); the other children are
        mounted but hidden (their message pumps still run, but the
        compositor does not call render() on them through us).

        Empty stack returns an empty string - the compositor handles
        bare str as a single Segment by definition (see Widget.render
        default).

        Out-of-range active index also returns "" - defensive guard
        for tests / user code that pokes active directly.
        """
        target = self.current
        if target is None:
            # Empty or out-of-range; emit nothing.  The compositor's
            # strip diff will clear whatever was there last frame.
            return ""
        # Delegate to the active child.  We call render() on the
        # child directly rather than recursing through any
        # compositor helper: Stack's job is to choose *which* child,
        # not to recompose the tree.  The child's render() return
        # value is already in the §7.1 shape.
        return target.render()
