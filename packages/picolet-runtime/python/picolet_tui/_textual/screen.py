"""picolet_tui._textual.screen - Screen + ScreenStack.

Screen is the Widget specialisation that the App's ScreenStack hosts;
ScreenStack is the LIFO collection of mounted Screens with a single
``current`` screen visible at any moment.  Together they implement the
"full-display layer" of the Textual surface: one logical viewport,
push to overlay (modal pickers, confirmations), pop to return.

Spec coverage:
  * FR-TUI-27 - Screen contributes ``tab`` / ``shift+tab`` BINDINGS for
                focus cycling.  The dispatch path lives in step 10; this
                module only ships the class-level declaration the @widget
                MRO merge will collect.
  * FR-TUI-50 - Stack semantics: push mounts and shows a new top, pop
                unmounts and returns to the previous, ``current`` (alias
                ``active``) is the visible screen, focus moves to the new
                top on both edges.  v0.1 spec §1.5 calls this the public
                ``Stack`` widget; under the hood it is an instance of
                ScreenStack hanging off the App (design §5.5).
  * Design doc §4.5 - Screen class shape: BINDINGS, __init__ slots
                (_focus_target, _dismiss_event, _dismiss_result), and
                the async dismiss() body.
  * Design doc §5.5 - ScreenStack class shape: push/pop/current,
                _on_hidden/_on_visible hooks on the screen, mount/remove
                via App.mount() and screen.remove().
  * D6 / synthesis - dismiss() uses asyncio.Event + result slot, not
                asyncio.Future (MicroPython's trimmed asyncio surface
                does not include Future).  See design doc §1.x summary
                point (5).

Design-doc references (textual-core-design.md):
  * §4.5 lines 613-643 - Screen class skeleton + dismiss().
  * §5.5 lines 759-787 - ScreenStack class skeleton.
  * §8 step 7 - implementation-order pin: Screen + ScreenStack land
                before App's initial-screen mount path (step 9).

Surface notes (reconciling task-brief vs design-doc):
  * Screen.__init__ matches Widget's positional-children signature from
    the design doc.  A keyword-only ``name`` was mentioned in the task
    brief for parity with the upstream Textual ``Screen(name=...)``
    surface; we accept and stash it for symmetry, but the framework does
    not consume the value in v0.1 (the screen's identity is its
    ``id`` / ``classes``, per the v0.1 spec's selector engine scope).
  * ScreenStack.current is the canonical accessor per the design doc;
    ``active`` is provided as an alias because the task-brief surface
    and upstream Textual's ``Screen``-stack helper both use that name.
  * ScreenStack.pop accepts an optional ``screen`` argument (design doc
    §5.5) so Screen.dismiss can pass ``self`` and pop the specific
    instance rather than relying on top-of-stack.  A bare ``pop()``
    pops the top.  Both return the popped screen for caller use.

What this module deliberately does NOT do:
  * Focus dispatch on ``tab`` / ``shift+tab``.  The binding is declared
    here; the matching + cycling lives in step 10's ``_dispatch_key``.
  * App resolution.  ``screen._app`` is set by ScreenStack.push when
    the screen is mounted onto the stack - until then it is None.
    Screen.dismiss tolerates a missing _app for unit-test scaffolds
    that exercise dismiss() without a real App attached.

The Event-plus-slot dismiss pattern (design §1.x summary, §4.5):
  * Screen owns an asyncio.Event and a plain attribute slot.
  * dismiss(result) writes the slot, sets the event, and calls back
    into ScreenStack.pop to unmount.
  * The caller of App.push_screen (Phase 4b step 9 - the App) awaits
    the same event and then reads the slot.  Because the slot is set
    *before* the event fires, the read after-the-wait is well-defined.
  * wait_for_dismiss() is the helper that bundles "await the event,
    return the slot value" into a single awaitable for callers that
    want the result without poking screen internals.
"""

# asyncio - Event is the D6-compatible primitive used in place of
# Future for the dismiss pattern.  The picolet asyncio package ships
# uasyncio under the stdlib name (NFR-TUI-9), so this import resolves
# on both CPython hosts and the picolet-tui runtime.
import asyncio

# Binding is the value type the BINDINGS class attribute declares.  The
# @widget decorator's bucket-5 walk picks up the BINDINGS list at
# decoration time; the merge with the Widget base's empty BINDINGS list
# happens in @widget's MRO merge (design doc §6.2).
from .binding import Binding

# Widget is the base class.  Screen extends it with screen-stack
# bookkeeping + dismiss() + the tab-focus bindings.  The Widget
# constructor already accepts the *children / id / classes / parent
# shape we forward through.
from .widget import Widget

# The @widget decorator + R3 guard.  Decorating Screen is mandatory:
# Widget.__init__ raises MissingWidgetDecoratorError otherwise (the
# guard reads vars(cls).get("_tui_widget_registered") on first
# instantiation - inheritance from Widget's True is NOT enough, see
# widget.py lines 213-229 for the rationale).
from ._widget_decorator import widget


# ---------------------------------------------------------------------
# Screen.
# ---------------------------------------------------------------------


@widget
class Screen(Widget):
    """A full-display Widget hosted by the App's ScreenStack.

    Subclassed by user code (modal dialogs, picker overlays, the
    primary application screen).  The base class supplies:

      * The tab / shift+tab focus-cycle bindings (FR-TUI-27).
      * The Event+slot dismiss pattern (design §4.5, §1.x point 5).
      * Visibility hooks (_on_hidden / _on_visible) for ScreenStack
        to call on push/pop edges.

    A Screen is just a Widget that happens to live at the root of the
    visible tree - all the compose() / mount() / render() machinery
    is inherited.  The only piece of Widget state Screen overrides is
    its bindings (which @widget will merge with Widget's empty list).
    """

    # ------------------------------------------------------------------
    # Class-level attributes.
    # ------------------------------------------------------------------

    # FR-TUI-27 / design §4.5 lines 620-623.  The tab binding moves
    # focus forward through focusable descendants; shift+tab moves
    # backward.  Both are overridable per FR-TUI-27 - a user Screen
    # subclass that extends BINDINGS will have its entries merged
    # last-wins per the @widget MRO merge (§6.2), so a user override
    # of "tab" replaces this default.
    #
    # The action names ("focus_next" / "focus_previous") are resolved
    # by the dispatch path in step 10 - they look up an action_<name>
    # method on the Screen and call it.  Phase 4b step 7 (this
    # module) declares the binding; phase 4b step 10 implements the
    # action_focus_next / action_focus_previous methods on Screen.
    BINDINGS = [
        Binding("tab", "focus_next", "Focus next"),
        Binding("shift+tab", "focus_previous", "Focus previous"),
    ]

    # DEFAULT_CSS placeholder.  Upstream Textual subclasses set this to
    # a CSS string; the v0.1 design (D2) does not parse CSS, so the
    # attribute is present-but-ignored.  Carrying the slot lets user
    # Screen subclasses copy-paste from upstream examples without an
    # AttributeError at class body load time.  The @widget decorator's
    # bucket-6 ("ignored CSS") scan picks it up and discards it.
    DEFAULT_CSS = ""

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(self, *children, name=None, id=None, classes="", parent=None):
        # Widget.__init__ runs the R3 guard (so a user Screen subclass
        # missing @widget is rejected here), wires DOMNode topology,
        # and stashes positional children for the mount path to drain.
        # We forward parent through; in practice push() sets the
        # parent to the App, but a test scaffold may construct a
        # Screen with no parent and exercise dismiss() in isolation.
        Widget.__init__(
            self,
            *children,
            id=id,
            classes=classes,
            parent=parent,
        )

        # Upstream Textual exposes Screen(name=...) for debug / repr
        # purposes; v0.1 does not act on the value but we accept and
        # stash it so user code written against upstream Textual
        # compiles unchanged.  No reactive: name is set once at
        # construction and never changes.
        self.name = name

        # _focus_target tracks "which descendant should receive focus
        # when this screen becomes current".  ScreenStack.push() calls
        # screen.focus() which is Widget.focus() - that only sets the
        # module-level focus to self.  A future step (4b/10 dispatch
        # or 5.x widgets) may walk into _focus_target to delegate
        # focus to a specific descendant; for now the slot is None
        # and Widget.focus()'s can_focus check applies.
        self._focus_target = None

        # The Event + result slot dismiss pattern (design §1.x point 5,
        # §4.5 line 628-629).  The Event is the "screen has been
        # dismissed" signal; the slot is the value the screen returned
        # to its caller.  push_screen on the App side waits on the
        # event, then reads the slot - because the slot write happens
        # *before* the event set, the read is well-defined.
        #
        # asyncio.Event is the D6-approved substitute for the Future
        # the upstream Textual implementation uses.  No internal
        # buffering, no callback chain - just one bit of "did dismiss
        # happen yet" plus an explicit value slot.
        self._dismiss_event = asyncio.Event()
        self._dismiss_result = None

        # App back-reference.  Set by ScreenStack.push when this
        # screen is pushed onto the stack; None for bare-Screen test
        # scaffolds.  dismiss() guards against None so unit tests can
        # exercise dismiss() without a mounted App.
        self._app = None

    # ------------------------------------------------------------------
    # dismiss() - Event+slot pattern.
    # ------------------------------------------------------------------

    async def dismiss(self, result=None):
        """Dismiss this screen, returning ``result`` to the caller.

        The "caller" is whoever invoked App.push_screen(self) and is
        currently awaiting the resulting awaitable.  The Event+slot
        contract is:

          1. Write the result to ``_dismiss_result`` (atomic store; a
             concurrent reader sees either the old or the new value,
             never a torn write).
          2. Set ``_dismiss_event`` (wakes up the awaiter).
          3. Remove self from the ScreenStack via App._screen_stack.pop
             so the parent screen (if any) regains visibility + focus.

        Ordering matters: the slot write happens before the event set
        so an awaiter that does ``await event.wait()`` followed by
        ``return screen._dismiss_result`` sees the freshly-written
        value.  Reversing the order would produce a race where the
        awaiter reads None.

        The pop() call is awaited because ScreenStack.pop is async -
        it calls screen.remove() which is the async unmount path
        (Widget.remove cancels message pumps + awaits CancelledError).
        Skipping the await here would orphan the unmount task.

        Tolerates a missing ``_app`` for unit-test isolation: if no
        App has claimed this screen, the event-set + slot-write still
        happens but no stack pop is attempted.  The awaiter (if any)
        still wakes up with the result.

        FR-TUI-50 / design §4.5: this is the only path a screen exits
        via.  remove() called directly on a Screen is undefined - it
        would leave the screen stack pointing at a dead reference.
        """
        # Write the slot BEFORE setting the event.  See the docstring
        # for the ordering rationale.
        self._dismiss_result = result
        self._dismiss_event.set()

        # If a ScreenStack owns us, propagate the dismiss into a pop.
        # The pop() call handles the unmount + focus transfer for the
        # now-visible screen below us.  Guard for the None case so
        # bare-construct + dismiss() in tests doesn't crash.
        if self._app is not None:
            stack = getattr(self._app, "_screen_stack", None)
            if stack is not None:
                await stack.pop(self)

    # ------------------------------------------------------------------
    # Visibility hooks - called by ScreenStack on push/pop edges.
    # ------------------------------------------------------------------

    def _on_hidden(self):
        """Called when this screen is covered by a newer push.

        Default is a no-op.  Subclasses override to e.g. pause
        animations, drop heavyweight subscriptions, or save scroll
        position.  Sync rather than async because the hook runs
        synchronously inside ScreenStack.push - making it async would
        force every push() to await even when no screen overrides
        the hook, which is the common case.

        Symmetry: _on_visible is the corresponding "you are uncovered
        again" hook fired by pop().
        """
        # No-op base implementation.  Subclasses override.

    def _on_visible(self):
        """Called when this screen becomes the visible top via pop().

        Default is a no-op.  See _on_hidden for the rationale.
        Subclasses override to refresh state that may have changed
        while the screen was covered (e.g. re-read a model field
        that other code may have mutated).
        """
        # No-op base implementation.  Subclasses override.

    # ------------------------------------------------------------------
    # wait_for_dismiss() - helper for App.push_screen.
    # ------------------------------------------------------------------

    async def wait_for_dismiss(self):
        """Block until dismiss() is called; return the result.

        The canonical "await this screen until it goes away" awaitable.
        Used by App.push_screen (Phase 4b step 9) to bundle the
        event-wait + slot-read into a single coroutine the caller can
        await on:

            result = await app.push_screen(MyDialog())
            # ^ internally: await dialog.wait_for_dismiss()

        Exposed as a public method (rather than the caller doing
        ``await screen._dismiss_event.wait(); return screen._dismiss_result``)
        so the event-and-slot pair is a private implementation detail
        of Screen.  A future revision could swap the asyncio.Event for
        a different primitive without breaking callers.
        """
        await self._dismiss_event.wait()
        return self._dismiss_result


# ---------------------------------------------------------------------
# ScreenStack.
# ---------------------------------------------------------------------


class ScreenStack:
    """LIFO collection of Screens, one visible at a time.

    The App owns exactly one ScreenStack (App.__init__ does
    ``self._screen_stack = ScreenStack(self)`` per design §5.1).
    The stack's top is the visible screen; pushes shadow the previous
    top, pops uncover it.  Focus moves to the new top on both edges
    (FR-TUI-50).

    No @widget decoration on this class: it is a pure collection, not
    a DOMNode.  Screens it contains are DOMNodes (decorated above);
    the stack itself just owns the ordering + mount/remove plumbing.

    Implementation notes:
      * The list is the storage; index -1 is the top.  Plain list
        push (append) + remove (by identity) is O(n) on remove but
        the v0.1 spec budgets at most a handful of overlaid screens
        (typical: 1-3), so the cost is negligible.
      * push() is async because it awaits App.mount() (which runs the
        new screen's on_mount).  pop() is async because it awaits
        screen.remove() (Widget.remove cancels message pumps + awaits
        the cooperative shutdown).  These are non-trivial: the caller
        must be inside an event loop.
      * The hidden/visible visibility hooks fire synchronously around
        the mount/remove - they are courtesy hooks, not part of the
        async lifecycle.
    """

    def __init__(self, app):
        # App back-reference - the stack delegates mounting to
        # ``app.mount(screen)`` because that is where the App's tree
        # rooting lives (App is the parent of every Screen).
        # Storing the app rather than a weakref to keep the synthesis
        # D6 "no weakref" rule simple; the app owns the stack
        # exclusively so the cycle is broken at App shutdown.
        self._app = app

        # The actual storage.  Plain list because membership + index
        # operations dominate; no deque is needed because pops happen
        # at arbitrary positions (Screen.dismiss can pop a not-top
        # screen if some prior dismiss() was buffered - rare but
        # well-defined).
        self._stack = []

    # ------------------------------------------------------------------
    # push / pop.
    # ------------------------------------------------------------------

    async def push(self, screen):
        """Mount ``screen`` on top of the stack; transfer focus.

        FR-TUI-50.  The previous top (if any) is hidden via _on_hidden
        but stays mounted: its widgets still receive Messages, its
        message pump still runs.  This matches upstream Textual: a
        modal dialog overlays the main screen but does not tear it
        down.

        Mount sequence:
          1. _on_hidden on the previous top (sync, before the new
             screen takes over).
          2. Append the new screen to the stack and set its _app
             back-reference so screen.dismiss() can find us.
          3. Await App.mount(screen) - this is the async on_mount +
             pump-start path on the new screen.  Returns when the
             screen's own on_mount has been awaited (FR-TUI-23
             contract via Widget.mount).
          4. Move focus to the new top.  Widget.focus is sync and
             cheap.  This is the FR-TUI-50 focus-on-push edge.

        The order of steps 2 and 3 matters: we append BEFORE awaiting
        mount so the screen is reachable via .current during its own
        on_mount handler.  A screen that posts a message in on_mount
        and walks back up to find the App will see itself at the top
        of the stack.

        Tolerates an app with no .mount method (test scaffolds): in
        that case the screen is added to the stack and focus moves,
        but no async mount runs.  This is the minimum-viable path
        that lets test code that injects a stub App still drive the
        stack semantics.
        """
        # Hide the previous top before swapping it for the new screen.
        # Sync call, no await: the hook is a courtesy, not part of
        # the async lifecycle.
        if self._stack:
            self._stack[-1]._on_hidden()

        # Wire the screen->stack back-reference BEFORE the mount so
        # an on_mount handler on the screen that calls dismiss() can
        # find us.  Setting it after the mount would race.
        screen._app = self._app
        self._stack.append(screen)

        # Mount via the App when available.  The App.mount path is
        # what spins up the message pump and dispatches on_mount;
        # ScreenStack does not replicate it.  Test scaffolds that
        # pass a stub object without .mount skip this step.
        mount = getattr(self._app, "mount", None)
        if mount is not None:
            await mount(screen)

        # Focus transfer (FR-TUI-50 push edge).  Widget.focus is a
        # no-op if can_focus=False - which is the Screen default.
        # When focus dispatch lands (step 10) the action_focus_next
        # path will walk into the screen's children to find the
        # first focusable descendant; for now we set focus to the
        # screen itself, which is the upstream Textual convention.
        screen.focus()

    async def pop(self, screen=None):
        """Remove ``screen`` from the stack; uncover the previous top.

        FR-TUI-50.  Default target is the current top (index -1) -
        Screen.dismiss passes ``self`` to pop a specific screen,
        which may not be the top if pops are interleaved with pushes.

        Unmount sequence:
          1. Find the target (param or top).
          2. Remove from the stack list (so .current immediately
             reflects the new visible screen).
          3. Await screen.remove() - the async unmount: cancels the
             pump, walks descendants, fires on_unmount.
          4. _on_visible on the new top (if any).  Sync.
          5. Move focus to the new top (FR-TUI-50 pop edge).  Sync.

        Returns the popped screen so callers can chain (the upstream
        Textual surface returns the screen from pop).  If the target
        is not in the stack (already popped, or never pushed), the
        method is a no-op returning None - matches the
        "tolerate-already-gone" pattern the rest of the framework
        uses (DOMNode.unmount on a detached node, focus.blur on a
        non-focused widget).

        Tolerates a screen without a .remove method or an app
        without async remove plumbing the same way push() does:
        test scaffolds can drive the stack purely on the list
        operations.
        """
        # Resolve target.  An explicit screen arg overrides the
        # default-to-top behaviour.  Using ``is`` for the default
        # sentinel rather than None would be more rigorous but
        # passing None as a real screen reference is impossible
        # (a Screen is always truthy), so the None check is sufficient.
        if screen is None:
            if not self._stack:
                return None
            target = self._stack[-1]
        else:
            target = screen

        # Remove from list.  If the screen isn't in the stack, return
        # without raising - this is the idempotent path for the case
        # where dismiss() runs twice on the same screen (e.g. a
        # double-tap or a defensive cleanup in on_unmount).
        try:
            self._stack.remove(target)
        except ValueError:
            return None

        # Async unmount.  Widget.remove is the canonical teardown -
        # cancels the message pump, walks descendants, fires on_unmount.
        # Guard for objects without remove() so test scaffolds work.
        remove = getattr(target, "remove", None)
        if remove is not None:
            await remove()

        # Clear the back-reference so a re-push (or a stale
        # dismiss-after-pop) cannot find the now-defunct app via
        # screen._app.  This is the explicit unmount contract from
        # design §3.2 - explicit clear rather than weakref.
        target._app = None

        # Uncover the new top.  Both hooks are sync - _on_visible
        # because the screen is already mounted (its pump never
        # stopped), and focus because Widget.focus is a slot write.
        if self._stack:
            self._stack[-1]._on_visible()
            self._stack[-1].focus()

        return target

    # ------------------------------------------------------------------
    # Accessors.
    # ------------------------------------------------------------------

    @property
    def current(self):
        """The visible screen (top of stack), or None when empty.

        Canonical name per design doc §5.5.  ``active`` is the alias
        used elsewhere in the codebase for the same semantics; we
        expose both so call sites written against either name work.
        """
        return self._stack[-1] if self._stack else None

    @property
    def active(self):
        """Alias for ``current``.

        The task brief uses ``active``; the design doc uses ``current``.
        Both name the same thing - the visible screen.  Aliasing lets
        existing call sites stay valid regardless of which name the
        caller learned first.
        """
        return self.current

    # ------------------------------------------------------------------
    # Container protocol - len + iter for testability.
    # ------------------------------------------------------------------

    def __iter__(self):
        """Yield screens bottom-to-top.

        Iteration order is stack-bottom-first (insertion order in the
        list) which matches the visual stacking order: the first
        yielded screen is the one most-covered, the last is the
        currently visible top.  This is the order upstream Textual's
        ``app.screen_stack`` iteration uses and what test code reads
        when asserting on the stack contents.
        """
        return iter(self._stack)

    def __len__(self):
        """Number of screens currently in the stack.

        Used by tests to assert push/pop arithmetic and by the App's
        exit path to know whether any screens remain to be unmounted.
        """
        return len(self._stack)

    def __contains__(self, screen):
        """True if ``screen`` is currently mounted on this stack.

        Provided so test assertions can use ``screen in stack``
        instead of poking ``stack._stack``.  Identity check via the
        list ``in`` operator (Screen doesn't override __eq__).
        """
        return screen in self._stack
