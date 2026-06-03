"""picolet_tui._textual.widget - Widget base class.

Widget = DOMNode + MessagePump + renderable + focusable + reactive home.
This is the class user code subclasses to build visible UI; every
on-screen element (Button, Label, Input, Container, Screen) descends
from Widget.

Spec coverage:
  * FR-TUI-19..22 - Reactive descriptors live on Widget (expand,
                    shrink) and on its subclasses.
  * FR-TUI-23     - mount() returns when on_mount has run.
  * FR-TUI-24     - remove() is the async unmount; cancels the message
                    pump, dispatches on_unmount, unlinks from parent.
  * FR-TUI-25     - on_mount fires after the children are linked into
                    the parent's children list.
  * FR-TUI-28 / R3 - Widget.__init__ asserts type(self)._tui_widget_registered.
  * FR-TUI-31     - reactive layout=True triggers a layout refresh
                    (Widget.refresh forwards layout flag to the App).

Design-doc references (textual-core-design.md):
  * §4.2 - Widget constructor + R3 guard + Reactive declarations.
  * §4.3 - mount / unmount / remove async lifecycle.
  * §4.4 - refresh() + the App._render_dirty event signal.
  * §4.6 - meta contributions table (Widget contributes expand, shrink).
  * §7.1 - render() return contract; default returns content string.

The split between Widget and Screen / App:
  * Widget is the leaf-or-container renderable.  No screen stack, no
    event loop.
  * Screen extends Widget with dismiss() + bindings for tab navigation.
  * App owns the screen stack, the compositor, and the event loop.
Per the design doc Widget itself is decorated so its subclasses
inherit a valid ``_tui_widget_meta``; the @widget decorator's MRO
merge picks up the Reactive(expand) / Reactive(shrink) declarations
exactly once at class-decoration time.
"""

# asyncio is the picolet asyncio variant - it ships uasyncio under the
# stdlib name (NFR-TUI-9).  We need create_task for the message-pump
# lifecycle and Event for the mount-awaitable signal; both are in the
# trimmed surface synthesis D6 pins.
import asyncio

# DOMNode provides parent/children/topology + Reactive-decorator
# friendly empty meta on the base.  Widget extends it with renderable
# semantics.
from .dom_node import DOMNode

# Reactive descriptor for the expand/shrink class-level declarations.
# The @widget decorator (below) walks vars(Widget), finds these
# Reactive instances, and registers them via _bind_name - the same
# code path user widgets travel.
from .reactive import Reactive

# The @widget decorator + R3 guard exception.  Decorating Widget here
# is mandatory: subclasses inherit a valid `_tui_widget_meta` via the
# MRO merge, and the runtime guard in Widget.__init__ catches user
# subclasses that forgot the decorator (FR-TUI-28 / R3 mitigation).
from ._widget_decorator import widget, MissingWidgetDecoratorError

# message_pump helpers - _maybe_await lets us call on_mount / on_unmount
# without knowing whether the user wrote a plain def or async def.
from .message_pump import _maybe_await


# ---------------------------------------------------------------------
# Focus tracker.
#
# Upstream Textual stores the focused widget on the App via a weakref;
# picolet has weakref shims but the design (§3.2) prefers explicit
# refs for parent / focus / screen pointers to keep lifecycle clear.
# This module-level slot is the single source of truth for "which
# Widget currently owns the focus"; .focus() writes it, .blur()
# clears it.  It is a plain reference - the explicit unmount path in
# DOMNode.unmount() / Widget.remove() is responsible for clearing
# focus if the focused widget is being removed.
#
# Why a module-level list-of-one rather than a plain module variable:
# the assignment site (`Widget.focus`) needs to mutate the slot
# observably from any subclass without `global`.  A list-of-one is
# the picolet idiom for this (also used by the _ACTIVE_APP slot in
# the App module per design §5.1).
# ---------------------------------------------------------------------


_FOCUSED = [None]


def get_focused():
    """Return the currently focused widget, or None.

    Public accessor so the binding-dispatch code (§6.3) and tests can
    read the focus without touching the module-private slot.  No
    setter is exposed: focus is set by Widget.focus() / cleared by
    Widget.blur() and Widget.remove().
    """
    return _FOCUSED[0]


# ---------------------------------------------------------------------
# _MountAwaitable - FR-TUI-23 lightweight awaitable.
#
# The design doc §4.3 calls for mount() to return "an awaitable that
# resolves when every child's on_mount has run".  An asyncio.Future
# would be the idiomatic primitive on CPython, but D6 bans Future
# (it is not in MicroPython's trimmed asyncio surface).  The picolet
# substitute is an Event the caller can `await` directly; we wrap it
# in a tiny class so `await widget.mount(child)` returns something
# that walks like a future for the caller's purposes.
# ---------------------------------------------------------------------


class _MountAwaitable:
    """Awaitable returned by Widget.mount() per FR-TUI-23.

    Resolves once every child's on_mount handler has been awaited.
    The constructor takes the children that were mounted; the
    coroutine simply yields to the event loop one extra tick so any
    on_mount tasks the pump scheduled have a chance to run before
    the caller's `await mount(...)` returns.
    """

    def __init__(self, children):
        # Stash the children so test code can introspect them via
        # the awaitable.  Not used by the await machinery itself.
        self.children = children

    def __await__(self):
        # Yield once.  on_mount was already awaited inline in
        # Widget.mount() before this awaitable was constructed (see
        # the body of mount() below); the yield here is a courtesy
        # for callers that schedule additional follow-up coroutines
        # in on_mount and want them to start before the mount() call
        # returns control.
        #
        # The `from` syntax routes through asyncio.sleep(0)'s own
        # __await__ generator, which is the canonical "yield to the
        # loop one tick" idiom in both upstream asyncio and the
        # MicroPython port.
        return asyncio.sleep(0).__await__()


# ---------------------------------------------------------------------
# Widget.
# ---------------------------------------------------------------------


@widget
class Widget(DOMNode):
    """A renderable, mountable, focusable DOMNode.

    Subclasses override ``render()`` to draw, ``compose()`` to yield
    children, and any number of ``on_<event>`` / ``@on``-decorated
    handlers to react.  The class-level Reactive descriptors at the
    bottom of this block (``expand``, ``shrink``) are inherited by
    every subclass; they show up in ``cls._tui_widget_meta["reactives"]``
    after @widget runs on the subclass.
    """

    # ------------------------------------------------------------------
    # Class-level attributes (design §4.2).
    # ------------------------------------------------------------------

    # Focus permissions.  ``can_focus`` is per-class because focus
    # eligibility is a fixed property of the widget type (a Button is
    # focusable, a Label is not); ``can_focus_children`` lets a
    # container opt out of "tab through my descendants".  Both are
    # plain class attributes - no Reactive - because they are not
    # expected to change at runtime (synthesis FR-TUI-19 scope).
    can_focus = False
    can_focus_children = True

    # Widget contributes an empty BINDINGS list at this layer; user
    # subclasses extend.  The @widget MRO merge ensures the empty
    # list is the base of the inheritance chain rather than
    # accidentally shadowing DOMNode's BINDINGS = [].
    BINDINGS = []

    # Reactives that every widget carries.  The @widget decorator on
    # this class binds these descriptors via _bind_name; the same
    # descriptor instances are visible (via MRO) on every subclass,
    # so a Button's expand reactive reads/writes through this same
    # descriptor object.  This is the design §4.6 meta-contribution
    # for the Widget level.
    expand = Reactive(False)
    shrink = Reactive(True)

    # ------------------------------------------------------------------
    # __init__ - R3 guard + topology + pending compose() children.
    # ------------------------------------------------------------------

    def __init__(self, *children, id=None, classes="", parent=None):
        # R3 mitigation (FR-TUI-28).  The check fires here, on first
        # instantiation, because MicroPython does not run
        # __init_subclass__ and a user subclass that forgot @widget
        # would otherwise produce a half-wired widget (no reactives,
        # no @on handlers, no BINDINGS).  The decorator on Widget
        # itself populates _tui_widget_registered = True, and that
        # value is inherited via the MRO by every properly decorated
        # subclass - so the check passes for "@widget class Foo(Widget):".
        # An undecorated subclass shadows the inherited attribute
        # only if it explicitly sets _tui_widget_registered = False,
        # which user code never does; in practice an undecorated
        # subclass simply inherits the True from Widget and the
        # guard silently passes.  The MRO scan in @widget itself
        # (_has_capturable_artifacts) catches the more interesting
        # case where an intermediate undecorated mixin declares
        # capturable artifacts.
        #
        # For the runtime guard to do useful work we need it to fail
        # closed when the *direct* class lacks decoration.  The check
        # here uses ``vars(type(self)).get(...)`` instead of getattr
        # so an inherited True does not satisfy a subclass that
        # itself was not decorated.  Trade-off documented at design
        # §1.3: false-negative possible if user copies the True
        # attribute manually; design rules that out.
        cls = type(self)
        cls_own = vars(cls).get("_tui_widget_registered", None)
        if cls is Widget:
            # Widget itself was decorated above; its vars() has the
            # flag.  The check is a no-op on the base.
            pass
        elif cls_own is not True:
            # The decorator did not run on this class.  Raising here
            # (rather than at class creation) is the only point in
            # MicroPython where we can intercept - there is no
            # __init_subclass__.  See _widget_decorator.py for the
            # error message body.
            raise MissingWidgetDecoratorError(cls)

        # DOMNode init does the topology + MessagePump setup.  Parent
        # is forwarded for the dispatch walk in §3.4.
        DOMNode.__init__(self, id=id, classes=classes, parent=parent)

        # Mount state.  ``_mounted`` flips True once on_mount has run;
        # ``_mounted_children`` mirrors ``_children`` but only for
        # children that have themselves completed mount.  The
        # distinction matters because compose() yields *pending*
        # children whose mount has not yet happened - those land in
        # _pending_children and graduate to _mounted_children once
        # _mount() finishes for each.
        self._mounted = False
        self._mounted_children = []
        self._pending_children = []

        # Stash compose-time children.  The design doc §4.2 puts these
        # on _pending_children for the mount path to drain; user code
        # writes ``MyWidget(child_a, child_b)`` and we mount them in
        # on_mount order.  We do *not* call self.mount() here because
        # mount is async and __init__ cannot await - synthesis D6
        # forbids running an event loop from __init__.
        for child in children:
            self._pending_children.append(child)

    # ------------------------------------------------------------------
    # compose() - user override hook.
    # ------------------------------------------------------------------

    def compose(self):
        """Yield child widgets to mount under this widget.

        The base implementation yields nothing.  User subclasses
        override to declare a tree:

            def compose(self):
                yield Button("Save", id="save")
                yield Button("Load", id="load")

        Called by the App / parent mount path after self.mount() has
        run on the bare widget; the returned widgets are mounted as
        children in iteration order.  Returns an iterable (typically
        a generator) so the caller can stream-mount without building
        the full child list first.

        Why a method rather than a class attribute: subclasses often
        need ``self.config`` / ``self.state`` to decide the tree shape.
        A class-level tree literal would not have access to instance
        state.
        """
        # Return an empty tuple (rather than yielding from a generator
        # function body) so the default doesn't pay the generator
        # bytecode overhead per widget.  Subclasses that override are
        # free to use a generator body.
        return ()

    # ------------------------------------------------------------------
    # mount / remove - async lifecycle.
    # ------------------------------------------------------------------

    async def mount(self, *children):
        """Mount one or more children under this widget.

        Implements FR-TUI-23.  For each child:
          1. Link into the children list and set parent pointer
             (delegates to DOMNode.mount for the sync topology bits).
          2. Append to _mounted_children to record post-mount state.
          3. Start the child's message-pump task.
          4. Await on_mount (FR-TUI-25) so the caller knows the child
             is fully initialised by the time mount() returns.

        Returns a _MountAwaitable - the spec calls for "an awaitable
        that resolves when every child's on_mount has run" (§4.3).
        Callers can ``await widget.mount(...)`` to chain follow-up
        work; the awaitable yields one extra tick after on_mount has
        already been awaited inline, which gives any tasks on_mount
        scheduled a chance to start.

        Multiple children: mount happens in iteration order, so
        on_mount for child N runs before mount starts for child N+1.
        This is upstream Textual's contract and matches the synthesis
        FR-TUI-23 ordering guarantee.
        """
        for child in children:
            # Sync topology - parent ref, children list append.  This
            # uses DOMNode.mount which handles the idempotent /
            # re-parent cases.
            DOMNode.mount(self, child)
            self._mounted_children.append(child)
            await child._mount()

        return _MountAwaitable(children)

    async def _mount(self):
        """Internal: spawn the pump and fire on_mount.

        Split out of mount() so the App's initial-screen path can
        drive the lifecycle for the root widget directly without
        going through a parent's mount().  The order is fixed by
        FR-TUI-25: pump first (so on_mount handlers that post
        messages have a working queue), then on_mount.

        Idempotent: a second call on an already-mounted widget is a
        no-op.  This is the safety net for compose-then-explicit-
        mount() patterns user code occasionally writes.
        """
        if self._mounted:
            return
        # Start the per-node message pump.  MessagePump.start_message_processing
        # is idempotent and returns the task; we don't need the
        # return value here.
        self.start_message_processing()

        # Drain any compose-time pending children.  These were
        # stashed in __init__ from positional args.  Compose() output
        # is drained by the App / parent mount path, not here -
        # there is no implicit compose() call inside _mount because
        # compose() output forms a *new* parent-child link, and the
        # mount-from-positional-args is already pinned to *this*
        # parent.
        pending = self._pending_children
        self._pending_children = []
        for child in pending:
            DOMNode.mount(self, child)
            self._mounted_children.append(child)
            await child._mount()

        # FR-TUI-25: on_mount fires after the children are linked.
        # _maybe_await handles both sync and async on_mount methods,
        # which is the upstream Textual convention.  Missing on_mount
        # is fine - getattr returns None and _maybe_await short-
        # circuits.
        on_mount = getattr(self, "on_mount", None)
        if on_mount is not None:
            await _maybe_await(on_mount())

        self._mounted = True

    async def remove(self):
        """Remove this widget from the tree (FR-TUI-24).

        Depth-first unmount: descendants first, then on_unmount on
        self, then cancel the pump task, then unlink from parent.
        Mirrors the design doc §4.3 pseudo-code.

        Clears focus if this widget was focused; the focus tracker
        is module-level state and an orphaned reference would leak.
        """
        # Depth-first - copy the list because each child.remove() will
        # mutate self._children via DOMNode.unmount.
        for child in list(self._children):
            await child.remove()

        # FR-TUI-24: on_unmount runs after descendants are gone but
        # before the pump shuts down, so the handler can still post
        # messages (e.g. to log a final state) and they will be
        # processed during the drain.
        on_unmount = getattr(self, "on_unmount", None)
        if on_unmount is not None:
            await _maybe_await(on_unmount())

        # Stop the pump - drain the queue, cancel the task, await
        # the CancelledError.  See message_pump.stop_message_processing
        # for the cooperative shutdown contract.
        await self.stop_message_processing()

        # Clear focus if we owned it.  Doing this *after* on_unmount
        # so handlers that key off "am I focused" still see the
        # correct state.
        if _FOCUSED[0] is self:
            _FOCUSED[0] = None

        # Detach from parent (sync topology).  DOMNode.unmount walks
        # the subtree but at this point _children is empty (we just
        # awaited each child.remove()), so it only does the parent
        # unlink.
        if self._parent is not None:
            try:
                self._parent._children.remove(self)
            except ValueError:
                pass
            # Also clear from _mounted_children on the parent if it is
            # a Widget (DOMNode has no _mounted_children).
            mounted = getattr(self._parent, "_mounted_children", None)
            if mounted is not None:
                try:
                    mounted.remove(self)
                except ValueError:
                    pass
            self._parent = None

        self._mounted = False

    # ------------------------------------------------------------------
    # Synchronous Widget.mount for compose()-style construction.
    # ------------------------------------------------------------------

    # The async mount() above is the post-app-start lifecycle path.
    # Compose-style construction in __init__ accepts positional
    # children, which are sync-linked in __init__ and mounted async
    # by _mount().  We do not expose a separate sync .mount() because
    # the design doc §4.3 only specifies the async form, and a sync
    # alias would invite the bug of starting the pump from sync
    # context (which would silently no-op in MicroPython's
    # uasyncio).

    # ------------------------------------------------------------------
    # render() - default returns content string.
    # ------------------------------------------------------------------

    def render(self):
        """Return a Rich RenderableType for this widget (§7.1).

        Default implementation returns an empty string; subclasses
        override.  The contract Phase 4c keys off is:

          * The return value is one of: str, _rich.text.Text, or an
            object exposing __rich_console__.
          * The compositor wraps the return in a render_lines() call
            with a fresh ConsoleOptions sized to self._region.

        For the step-6 skeleton we return a plain string because the
        Phase 4c compositor's rich.console.Console handles bare str
        as a single Segment by definition.  Subclasses that need
        styled output return a Text or a custom Renderable.

        Note: render() must be a *fast* sync method.  The compositor
        calls it once per dirty frame; an O(n) build per call is
        acceptable, an O(n^2) walk is not.  This is design doc §7
        territory.
        """
        return ""

    # ------------------------------------------------------------------
    # refresh() - signal the compositor.
    # ------------------------------------------------------------------

    def refresh(self, *, layout=False, repaint=True):
        """Mark this widget dirty so the next frame redraws it.

        For step 6 this is a NO-OP that returns; the design doc §4.4
        specifies it should walk the parent chain to the App and set
        the App's _render_dirty event, but the App + compositor
        wiring is Phase 4b agent 7-8 / Phase 4c work.  Leaving this
        as a documented stub lets step 6 land standalone while
        preserving the API surface every subclass and Reactive write
        calls into.

        The Reactive descriptor (reactive.py) calls ``refresh(layout=
        self._layout)`` on every reactive write after the watcher
        fires.  The stub here means a Reactive write does not crash
        even when no App is mounted - which is the behaviour
        unit tests for Reactive depend on.

        When step 8 lands the stub will be replaced with:

            self._dirty = True
            app = _resolve_app(self)
            if layout:
                app._needs_layout = True
            app._render_dirty.set()

        The keyword-only ``layout`` and ``repaint`` are part of the
        upstream Textual surface (and the Reactive descriptor calls
        them); we accept and ignore them here.
        """
        # Flag own dirty bit so the compositor's mark_dirty has a
        # consistent read when it does wire up.  No App resolution,
        # no event set.  Subclasses do not override this method -
        # they reach for App.refresh() if they need explicit App
        # interaction.
        self._dirty = True
        # `repaint` and `layout` are intentionally accepted-and-
        # ignored at this layer.  The keyword surface is preserved
        # so callers (Reactive, user code) compile against the
        # final API today.

    # ------------------------------------------------------------------
    # focus / blur.
    # ------------------------------------------------------------------

    def focus(self):
        """Set the module-level focus to this widget.

        FR-TUI-26-equivalent surface.  A widget with ``can_focus =
        False`` is silently rejected - the upstream Textual contract
        is to no-op, not to raise, because focus() is often called
        defensively (e.g. on screen activation).  The previous
        focused widget loses focus via the module slot overwrite;
        there is no "focus stack" in v0.1 (D6 / synthesis §5).

        Returns self for caller convenience (``focus().on_focus()``).
        """
        if not self.can_focus:
            return self
        _FOCUSED[0] = self
        return self

    def blur(self):
        """Clear focus if this widget currently owns it.

        No-op if some other widget is focused.  This guards against
        the pattern of "every widget calls blur() in on_unmount"
        accidentally stealing focus from an unrelated widget that
        happens to be active.
        """
        if _FOCUSED[0] is self:
            _FOCUSED[0] = None
        return self

    @property
    def has_focus(self):
        """True iff this widget currently owns the module focus.

        Property because Reactive watchers and bindings code key off
        ``widget.has_focus`` rather than the module accessor; the
        property is one identity compare and stays cheap.
        """
        return _FOCUSED[0] is self
