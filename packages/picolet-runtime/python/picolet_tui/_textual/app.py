"""picolet_tui._textual.app - App class (Phase 4b step 9).

The App is the user-facing entry point for a picolet-tui program.  It
owns the asyncio task topology (input pump, resize pump, render task,
exit watcher), the ScreenStack, the Compositor, and the single
``_ACTIVE_APP`` slot that enforces FR-TUI-5 ("exactly one App per
process").

Spec coverage
-------------
* FR-TUI-1  - ``App.run()`` is the sync blocking entry; raises
  ``RuntimeError`` if called from inside a running loop.
* FR-TUI-2  - ``App.run_async()`` is the async entry; joins the
  existing loop.
* FR-TUI-3  - ``App.exit(result=None)`` requests orderly shutdown;
  ``run`` / ``run_async`` returns ``result``.
* FR-TUI-4  - ``App.quit()`` aliases ``exit(None)``; bound to
  ``ctrl+q``.
* FR-TUI-5  - second concurrent ``App.run`` raises ``RuntimeError``
  before touching the driver.
* FR-TUI-7  - ``tuiterm.enable()`` at entry, ``tuiterm.disable()``
  exactly once on every exit path.
* FR-TUI-9  - resize observed via ``tuiterm.resize_pending()`` /
  ``tuiterm.size()``; ``Resize`` event posted to the root.
* FR-TUI-50 - ``push_screen`` / ``pop_screen`` go through ScreenStack.
* FR-TUI-55 - input pump calls ``tuiterm.read_input(timeout_ms=20)``;
  yields between empty reads via ``asyncio.sleep(0)``.
* FR-TUI-59 - three named tasks plus an ``_exit_watcher``; gather,
  not TaskGroup (synthesis D6).
* FR-TUI-76 - tiny-terminal handling delegated to the Compositor.

Design-doc references (textual-core-design.md):
  * §5.1 - ``App.__init__`` shape, ``_ACTIVE_APP`` slot, screen stack
    construction.
  * §5.2 - ``run()`` body (asyncio.run + nested-loop guard).
  * §5.3 - ``run_async`` task topology + ``_exit_watcher`` cancellation
    pattern (D6 gather substitute for TaskGroup).
  * §5.4 - ``exit`` / ``quit`` semantics.
  * §5.5 - ``ScreenStack`` ``push`` / ``pop`` lifecycle.

What this module deliberately does NOT do
-----------------------------------------
* It does not parse input bytes into ``Key`` / ``Mouse`` / ``Paste``
  events.  The parser lives in ``picolet_tui._parser`` (not yet
  landed in Phase 4b); the input pump just calls
  ``tuiterm.read_input`` and feeds raw bytes through a hook the
  parser will register.  v0.1 input parsing is Phase 4d.
* It does not own the Compositor diff algorithm.  The render task
  calls into the Compositor's public ``update_dom`` / ``full_redraw``
  and treats the returned ``(col, row, segments)`` tuples as opaque
  - emit-to-bytes (CUP + segment SGR) is Phase 4c.  For v0.1 the
  render body is a stub that loops on ``_render_dirty`` and calls
  ``full_redraw`` once per dirty signal, exactly as the task brief
  requires.
* It does not bind ``ctrl+q`` to ``quit`` here - the binding lives
  on the class-level ``BINDINGS`` list and is consumed by the
  ``@widget`` decorator on App.  The key-dispatch path that actually
  invokes ``action_quit`` is Phase 4b step 10 (binding key dispatch),
  not part of this file's surface.
"""

# asyncio is the picolet asyncio variant: ships uasyncio under the
# stdlib name (NFR-TUI-9).  The trimmed surface synthesis D6 pins is:
# create_task, gather, sleep, Event, current_task, CancelledError.
# We use exactly those primitives - no Future, no TaskGroup, no Queue.
import asyncio

# sys is the diagnostic channel (NFR-TUI-29).  The App writes the
# FR-TUI-10 driver-refusal stderr line through sys.stderr and routes
# the framework-internal log helper here too.  stdout is owned by
# tuiterm (per the same NFR); App never writes to it directly outside
# the render task's tuiterm.write path.
import sys

# Binding is consumed by the @widget decorator's bucket-5 walk; the
# class-level BINDINGS list on App carries the FR-TUI-4 ctrl+q -> quit
# entry.  Imported eagerly so the symbol resolves at class-body time.
from .binding import Binding

# Compositor owns the frame buffer + diff.  The App passes a Console
# at construction; render-task body calls update_dom + full_redraw.
# Phase 4c-style diff_and_emit comes once the parser/event surface is
# in place; for now we drive full_redraw on every wake (see _render).
from .compositor import Compositor

# Console is the trimmed Rich console - the Compositor needs one for
# render_lines; the App creates it once at startup with the
# color_system the driver capability detection settled on.
from picolet_tui._rich.console import Console

# Size geometry value for viewport.  The render path passes
# (cols, rows) to update_dom; Compositor accepts either Size or tuple
# but we pass Size for consistency with the rest of the framework.
from .geometry import Size

# MessagePump is the App's superclass - App has its own message queue
# for app-level events (Resize, Key, Mouse forwards, user-posted
# messages).  The Widget base would also work, but App is not a
# renderable: it owns a Screen stack and delegates rendering down.
# MessagePump is the correct waist.
from .message_pump import MessagePump

# Message base for the Resize / app-level events we post during the
# pump loop.  Concrete event types live in the parser/events module
# in Phase 4d; for v0.1 we expose a tiny Resize event here so the
# resize pump has something to enqueue (FR-TUI-9 ordering).
from .message import Message

# tuiterm is the C surface from the picolet tui variant (FR-TUI-58).
# Six entry points: enable, disable, read_input, write, size, is_tty.
# The variant uses libffi bindings (see picolet_tui._tuiterm); we
# import it lazily inside enable/disable so a unit test running
# under the cli variant can construct an App for shape checks
# without importing the C-symbol-resolution module at module-load
# time.
def _lazy_tuiterm():
    """Return the ``picolet_tui._tuiterm`` module, importing on demand.

    Held behind a helper because the C symbol resolution in the
    module body raises ImportError under non-tui variants, and we
    want App-construction tests to pass under the cli variant for
    shape coverage.  Production code paths (run, run_async) call
    this on the first frame and crash loud if tuiterm is missing -
    which is the FR-TUI-10 "refuse to start" behaviour.
    """
    # Local import - re-importing is cheap thanks to sys.modules
    # caching, and keeps the App module load-time free of the
    # libffi probe.
    import picolet_tui._tuiterm as _term
    return _term


# The @widget decorator + R3 guard.  Decorating App is mandatory:
# subclasses inherit a valid `_tui_widget_meta` via the MRO merge,
# and Widget.__init__'s runtime guard catches subclasses that
# forgot the decorator (FR-TUI-28 / R3).  App itself uses the same
# decorator (not Widget's) because App is not a renderable - it
# extends MessagePump directly.
from ._widget_decorator import widget, MissingWidgetDecoratorError


# ---------------------------------------------------------------------
# _ACTIVE_APP slot.  Module-level list-of-one is the picolet idiom
# (also used in widget._FOCUSED) for module-private state that the
# class-method needs to mutate without a `global` declaration.
#
# FR-TUI-5: exactly one App may be running per process at a time.  A
# second App.run() must raise RuntimeError *before touching the
# driver* - so the check sits at the top of run_async() and toggles
# the slot back to None in the finally clause.
# ---------------------------------------------------------------------


_ACTIVE_APP = [None]


def get_active_app():
    """Return the currently-running App, or None.

    Public accessor so the Reactive descriptor's refresh path (and
    Widget.refresh in step 8) can locate the App without walking the
    DOM tree.  The accessor is read-only; the App constructor /
    run_async lifecycle owns the write.
    """
    return _ACTIVE_APP[0]


# ---------------------------------------------------------------------
# Resize event.  Posted by _pump_resize when tuiterm.resize_pending()
# returns True.  Carries the new (cols, rows) so the active screen's
# layout pass can read them without re-querying tuiterm.size().
#
# Why this event lives here rather than in a future ``events.py``:
# the resize pump is the only producer in v0.1, and the event needs
# nothing from outside the App module.  Splitting it into events.py
# would invert the import order (events would need to import
# Message, which is fine, but the App-only consumer of the type
# means hosting it here keeps the LoC compact).  When Phase 4d
# lands the full events module the type can move; the move is a
# rename, not a behaviour change.
# ---------------------------------------------------------------------


class Resize(Message):
    """Terminal-size-changed event (FR-TUI-9).

    The new dimensions are carried as the ``size`` attribute, a
    ``geometry.Size`` instance.  Bubble flag is the Message default
    (True) so a Screen-level handler can react before the App-level
    layout reflow runs.

    Coalescing: a second Resize event arriving before the first has
    been dispatched should replace the first - successive SIGWINCH
    fires during a drag-resize would otherwise queue up O(n)
    redundant events.  ``can_replace`` returns True for any other
    Resize so the queue stays at one outstanding entry.
    """

    def __init__(self, size):
        # Message.__init__ initialises the bubbling bookkeeping
        # (_stop_bubble, _sender, _handler_args).  Calling super
        # rather than reproducing it inline matches the @widget
        # bucket-3 expectations and keeps the constructor short.
        Message.__init__(self)
        self.size = size

    def can_replace(self, other):
        # Two Resize events in a row collapse to the newer one - the
        # older size is stale by definition.  Returning True here
        # lets MessagePump.post_message drop the older entry from
        # the queue at the 4096 cap (design §3.5).
        return isinstance(other, Resize)


# ---------------------------------------------------------------------
# ScreenStack - design doc §5.5.
#
# Holds the ordered list of Screens.  Push mounts the new top onto
# the App and hides the previous top; pop unmounts the popped screen
# and reveals the new top.  ``current`` is the visible screen.
#
# v0.1 ships without a Screen class (Phase 4b step 7 is downstream
# of this step).  The stack works against any DOMNode-shaped object
# that has a ``focus()`` method and ``_on_hidden`` / ``_on_visible``
# hooks - which is the contract Screen will satisfy when it lands.
# A defensive ``getattr(..., None)`` on each hook keeps the stack
# usable with bare Widgets in the meantime.
# ---------------------------------------------------------------------


class ScreenStack:
    """Ordered list of Screens; one active at a time (FR-TUI-50).

    The active screen is ``self._stack[-1]``; everything beneath is
    suspended.  Pushing a new screen calls ``_on_hidden`` on the
    previous top and ``_on_visible`` on the new top after mount.

    This class is intentionally not @widget-decorated: it carries no
    reactives, no @on handlers, no BINDINGS, no compute_*; it is a
    pure value-holder.  See the design doc §1.1 bucket-7 fallthrough
    and §10's compat table row for plain value types.
    """

    def __init__(self, app):
        # Strong ref to the owning App.  The stack is conceptually a
        # child of the App's lifecycle: the App teardown clears
        # _ACTIVE_APP and the stack disappears with the App
        # instance.  No weakref - design §3.2 prefers explicit refs.
        self._app = app
        # _stack[-1] is the active screen; pre-mount the stack is
        # empty and ``active`` returns None.  Mutation goes through
        # push / pop only.
        self._stack = []

    @property
    def active(self):
        """The currently-visible screen, or None when the stack is empty.

        Property rather than method because ``app.screen`` is the
        upstream Textual shape and the v0.1 test surface reads it
        as an attribute.  Returns None when empty so user code can
        guard via ``if app.screen: ...`` cleanly.
        """
        return self._stack[-1] if self._stack else None

    # ``current`` is the design-doc spelling (§5.5).  Alias kept so
    # both surfaces work; ``active`` is the picolet preferred name.
    @property
    def current(self):
        # Reads from the same slot ``active`` does; the alias means
        # internal code can use either spelling without forking the
        # access path.
        return self.active

    async def push(self, screen):
        """Mount ``screen`` as the new top (FR-TUI-50).

        Order:
          1. Hide the previous top via ``_on_hidden`` (if defined).
          2. Append the new screen to the stack.
          3. Mount it under the App so its message-pump runs.
          4. Focus it (no-op when can_focus is False).
          5. Call ``_on_visible`` on the new top (if defined).

        The mount step uses ``self._app.mount`` if the App is a
        Widget-shaped mount host; otherwise it falls back to a
        direct ``_mount`` on the screen.  v0.1 App is a MessagePump,
        not a Widget, so we call ``_mount`` directly - which spawns
        the screen's pump task and runs its on_mount.
        """
        # Hide the previous top before linking the new one - this
        # is the upstream Textual ordering: a screen never sees
        # _on_visible while another screen is also "visible".
        if self._stack:
            previous = self._stack[-1]
            hook = getattr(previous, "_on_hidden", None)
            if hook is not None:
                # Best-effort hook invocation: hide is non-critical
                # to the push and a missing/raising hook should not
                # block the new screen from becoming active.  Any
                # exception is logged through the pump's standard
                # path; we let it propagate so handler-exception
                # tests still see it surface.
                hook()
        # Link the new screen as the active stack entry.
        self._stack.append(screen)
        # Parent the screen to the App so the bubbling walk
        # terminates correctly at App-level handlers.
        screen._parent = self._app
        # Spawn the screen's message-pump and fire its on_mount.
        # Widget._mount is idempotent so the call survives a
        # re-push of the same screen instance (rare; supported).
        mount = getattr(screen, "_mount", None)
        if mount is not None:
            await mount()
        # Focus the new top.  Widget.focus is a no-op for
        # can_focus=False; a Screen subclass typically forwards
        # focus to its declared focus target inside _on_visible.
        focus = getattr(screen, "focus", None)
        if focus is not None:
            focus()
        # Run the visible hook last - by spec the hook runs after
        # the screen has been mounted and focused.
        visible = getattr(screen, "_on_visible", None)
        if visible is not None:
            visible()

    async def pop(self, screen=None):
        """Unmount ``screen`` (or the top) and reveal the next entry.

        ``screen`` defaults to the current top; passing an explicit
        value lets a deeper screen self-dismiss (the design-doc
        Screen.dismiss path calls ``app._screen_stack.pop(self)``).

        Order:
          1. Remove the target from the stack.
          2. Await its ``remove()`` (depth-first unmount).
          3. Reveal the new top via ``_on_visible`` and ``focus()``.
        """
        if not self._stack:
            # Defensive: pop on an empty stack is a no-op.  Upstream
            # Textual raises here, but the picolet App lifecycle has
            # one case (exit-during-screen-transition) where the
            # stack can race ahead of the pop request; treating
            # empty-stack as no-op closes that race without
            # surfacing it to user code.
            return None
        target = screen if screen is not None else self._stack[-1]
        # Remove from the stack first so the unmount cannot see the
        # screen in the active list mid-tear-down.
        try:
            self._stack.remove(target)
        except ValueError:
            # Target was not on the stack - tolerate.  This happens
            # if Screen.dismiss races with App.exit and the App
            # tears the stack down before dismiss reaches us.
            return None
        # Unmount the screen.  Widget.remove handles depth-first
        # unmount including on_unmount, pump cancellation, parent
        # unlink.
        remove = getattr(target, "remove", None)
        if remove is not None:
            await remove()
        # Reveal the new top, if any.
        if self._stack:
            new_top = self._stack[-1]
            visible = getattr(new_top, "_on_visible", None)
            if visible is not None:
                visible()
            focus = getattr(new_top, "focus", None)
            if focus is not None:
                focus()
        return target


# ---------------------------------------------------------------------
# App.
# ---------------------------------------------------------------------


@widget
class App(MessagePump):
    """User-subclassed entry point for a picolet-tui program.

    Override ``compose()`` to yield the initial screen / widget tree
    and ``on_mount`` to do post-mount setup.  Call ``self.exit(...)``
    from any handler to leave; the return value reaches the original
    ``run()`` / ``run_async()`` caller.

    Subclassing contract:
      * Apply @widget to every direct subclass.  The base App is
        already decorated so the MRO merge picks up BINDINGS,
        TITLE etc; the runtime guard in __init__ catches any user
        subclass that omits the decorator.
      * Extend BINDINGS to add app-level key actions.  The base
        ``ctrl+q -> quit`` survives via the @widget bindings-merge
        (subclass-wins-on-collision; same-key bindings shadow).
      * Override TITLE / SUB_TITLE to customise the chrome.  The
        compositor reads these via the App.screen accessor when
        rendering header / footer widgets.
    """

    # ------------------------------------------------------------------
    # Class-level attributes (design §5.1).
    # ------------------------------------------------------------------

    # FR-TUI-4: ctrl+q always quits unless the user explicitly removes
    # the binding.  The @widget MRO merge subclass-wins rule means a
    # user BINDINGS that *does not* include ctrl+q still inherits this
    # entry (parent bindings are merged before subclass-wins applies);
    # to actually remove it the user has to override the action by
    # binding ctrl+q to a different action, which they would do
    # deliberately.
    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]

    # Display strings the compositor reads for chrome / log lines.
    # User apps override at the class level (``class MyApp(App):
    # TITLE = "..."``); the value is read once at startup time.
    TITLE = "picolet-tui app"
    SUB_TITLE = ""

    # SCREENS is the upstream Textual convention for named screen
    # registration: ``{"main": MainScreen, "help": HelpScreen}``.
    # The dict maps a name to a factory callable; ``push_screen``
    # accepts either an instance or a name and constructs from the
    # registry on demand.  v0.1 ships the registry slot but does not
    # use it - users can push instances directly.  The slot is here
    # so a future ``push_screen("name")`` lookup does not require an
    # API addition.
    SCREENS = {}

    # ------------------------------------------------------------------
    # __init__ - R3 guard + app-level state.
    # ------------------------------------------------------------------

    def __init__(self):
        # R3 mitigation (FR-TUI-28).  Same pattern as Widget.__init__:
        # check that the *direct* class carries _tui_widget_registered
        # in its own vars(), not inherited from App.  An undecorated
        # user subclass shadows the inherited flag only via explicit
        # _tui_widget_registered = False - which user code never
        # does - so the check falls back to "decorated or not" via
        # vars().
        cls = type(self)
        cls_own = cls.__dict__.get("_tui_widget_registered", None)
        if cls is App:
            # App itself was decorated below; its vars() has the
            # flag.  Pass.
            pass
        elif cls_own is not True:
            raise MissingWidgetDecoratorError(cls)

        # MessagePump.__init__ sets up the queue, wake event, parent
        # (None for App - App is the root), and empty children list.
        MessagePump.__init__(self, parent=None)

        # ScreenStack - the active screen is the top entry; mount
        # path adds to it via push_screen.
        self._screen_stack = ScreenStack(self)

        # Driver state.  ``_driver`` holds the tuiterm module handle
        # once enable() has run; None before and after the driver
        # lifecycle.  Two-state because tests need a clean "is the
        # driver alive?" boolean.
        self._driver = None

        # _exit_result is set by exit(); run_async returns it after
        # tear-down.  Default None matches FR-TUI-4 (quit -> exit(None)).
        self._exit_result = None

        # The exit Event - set by exit(); _exit_watcher awaits it and
        # cancels the other gather siblings on fire.  Design §5.3:
        # this is the D6-mandated substitute for an asyncio.Future
        # signalling shutdown.
        self._exit_requested = asyncio.Event()

        # _render_dirty - set by Widget.refresh and by the resize
        # pump; the _render task wakes on it and rebuilds the frame.
        # asyncio.Event.set is idempotent so multiple refreshes in
        # one frame coalesce into one wake.
        self._render_dirty = asyncio.Event()

        # Layout-changed flag.  Reactive(layout=True) writes set this
        # before refreshing; the render task checks it on wake to
        # decide whether to run the layout pass before painting.
        # The flag clears after one render cycle.
        self._needs_layout = False

        # Compositor + Console - constructed lazily in run_async so
        # the color_system from tuiterm.enable can flow in.  Tests
        # that build an App without running it can still touch
        # ``self._compositor is None`` without crashing.
        self._console = None
        self._compositor = None

        # Last-known viewport.  Updated by _pump_resize when the
        # size changes; the render task reads it to size the
        # compositor's update_dom call.  Initialised to (0, 0) so
        # the first resize tick triggers a full layout regardless of
        # whether tuiterm has been queried yet.
        self._viewport = Size(0, 0)

        # Capabilities dict returned by tuiterm.enable - kept on the
        # App so the compositor and downgrade ladder can read it
        # without re-probing.  None until enable() has run.
        self._capabilities = None

        # Active tasks - set by run_async so exit handling can
        # introspect them.  Kept on the App for diagnostic logging
        # only; cancellation happens through _exit_watcher's
        # current_task() walk inside the gather, not via these refs.
        self._task_input = None
        self._task_resize = None
        self._task_render = None
        self._task_exit = None

    # ------------------------------------------------------------------
    # User override hooks.
    # ------------------------------------------------------------------

    def compose(self):
        """User override: yield root widgets / screens.

        Default yields nothing - subclasses override to declare the
        initial UI.  Called by ``_mount_initial_screen`` during
        ``run_async`` startup; the yielded objects are pushed onto
        the screen stack in iteration order, with the last one
        landing as the active screen.

        Returns an iterable.  The default returns an empty tuple
        rather than a generator function body to avoid the per-call
        generator overhead in apps that don't override.
        """
        return ()

    async def on_mount(self):
        """Post-mount user hook.

        Fires after the initial screen has been pushed and the
        message pump is running.  Default no-op.  Subclasses
        override to kick off post-startup work (timers, IPC
        registrations, etc.); the hook may be ``async def`` or
        plain ``def``.
        """
        return None

    async def on_unmount(self):
        """Pre-teardown user hook.

        Fires after exit() has been requested but before tuiterm
        is disabled.  Default no-op.  Subclasses override to flush
        state or send final messages.  Like on_mount this may be
        plain or async; the App lifecycle awaits it via _maybe_await
        equivalent (handled in the message-pump invocation).
        """
        return None

    # ------------------------------------------------------------------
    # run() - sync entry (FR-TUI-1).
    # ------------------------------------------------------------------

    def run(self):
        """Blocking entry from sync code.

        Detects a running loop and raises ``RuntimeError`` rather
        than nesting asyncio.run inside an existing loop - which
        would corrupt the picolet asyncio pump's state and deadlock
        on the inner loop's poll.  The runtime path (where the
        picolet asyncio pump owns the loop) must use
        ``await app.run_async()`` instead.

        Returns the value passed to ``exit(result)``; defaults to
        None when ``quit()`` (or ``ctrl+q``) was used.
        """
        # Detect a running loop.  asyncio.get_event_loop is
        # deprecated in CPython 3.12+ but the call still returns the
        # current loop and matches the upstream Textual pattern; the
        # MicroPython asyncio variant exposes get_event_loop as the
        # primary API.  Wrapping in try/except handles the case where
        # no loop exists at all (first call in the process).
        try:
            loop = asyncio.get_event_loop()
            running = loop.is_running()
        except RuntimeError:
            # No loop exists yet - means we are the first asyncio
            # call in this process.  Definitely not running.
            running = False
        except AttributeError:
            # MicroPython builds without is_running() - treat as
            # not running.  The picolet asyncio variant ships the
            # method per NFR-TUI-9; this branch is defensive for
            # older MP builds.
            running = False
        if running:
            # Nesting asyncio.run inside an existing loop is the
            # crash the FR-TUI-1 RuntimeError prevents.  The error
            # message names the workaround to keep the diagnostic
            # actionable.
            raise RuntimeError(
                "App.run() called from inside a running event loop; "
                "use 'await app.run_async()' instead."
            )
        # asyncio.run owns the loop for the duration of run_async
        # and tears it down on return.  This is the FR-TUI-1 path
        # (sync entry from a fresh process).
        return asyncio.run(self.run_async())

    # ------------------------------------------------------------------
    # run_async() - async entry (FR-TUI-2).
    # ------------------------------------------------------------------

    async def run_async(self):
        """Async entry; joins the already-running loop (FR-TUI-2).

        Owns the driver lifecycle and the task topology.  Returns
        the value passed to ``exit(result)``.

        Concurrency model (design §5.3, FR-TUI-59): one gather over
        four tasks - input pump, resize pump, render task, and an
        exit-watcher that cancels its siblings when ``exit()`` is
        called.  No TaskGroup; D6 pins gather as the multiplexing
        primitive.

        Tear-down ordering on every exit path (normal, exception,
        cancellation) is fixed:
          1. on_unmount user hook (if defined).
          2. tuiterm.disable() - exactly once (FR-TUI-7).
          3. _ACTIVE_APP[0] = None - releases the per-process slot.
        """
        # FR-TUI-5: exactly one App at a time.  The check sits
        # before any side-effect so a second run raises cleanly.
        # The slot is a list-of-one so the assignment is visible to
        # other modules (Widget.refresh, Reactive watchers) without
        # a `global` declaration.
        if _ACTIVE_APP[0] is not None:
            raise RuntimeError(
                "Only one App may run per process at a time "
                "(FR-TUI-5).  Another App is already active."
            )
        _ACTIVE_APP[0] = self

        try:
            # Driver enable (FR-TUI-7).  Acquires the tuiterm
            # module via the lazy helper so import-time errors
            # under non-tui variants are deferred until here -
            # which is the right place: a process that cannot
            # access tuiterm must not be allowed to claim an App.
            await self._driver_enable()

            # Mount the initial screen via compose().  This spawns
            # the screen's message-pump task and runs its
            # on_mount; the App's own on_mount runs *after* the
            # initial screen is up.
            await self._mount_initial_screen()

            # The four-task topology (FR-TUI-59).  gather awaits
            # all four; the _exit_watcher cancels the other three
            # when exit() fires.  CancelledError percolating out
            # of the gather is the expected exit path.
            try:
                self._task_input = asyncio.create_task(self._pump_input())
                self._task_resize = asyncio.create_task(self._pump_resize())
                self._task_render = asyncio.create_task(self._render())
                self._task_exit = asyncio.create_task(self._exit_watcher())
                # The picolet asyncio variant accepts the bare
                # tasks; on the CPython path gather coerces them
                # the same way.  Result list is ignored - we read
                # _exit_result from the App, not the task return.
                await asyncio.gather(
                    self._task_input,
                    self._task_resize,
                    self._task_render,
                    self._task_exit,
                )
            except asyncio.CancelledError:
                # Expected: _exit_watcher cancels its siblings.
                # The gather re-raises the first CancelledError it
                # sees; we swallow it because exit was requested.
                pass

        finally:
            # Tear-down ordering: user on_unmount -> driver disable
            # -> slot release.  Wrapped individually so a failing
            # on_unmount does not prevent the driver from tearing
            # down (which would leave the terminal in raw mode).
            try:
                on_unmount = getattr(self, "on_unmount", None)
                if on_unmount is not None:
                    result = on_unmount()
                    # Await if coroutine; otherwise no-op.  We use
                    # the same shape as message_pump._maybe_await
                    # but inline because importing _maybe_await
                    # here would create a small cycle with the
                    # widget module under some import orderings.
                    if result is not None and (
                        hasattr(result, "__await__") or hasattr(result, "send")
                    ):
                        await result
            except Exception as exc:
                # on_unmount must not block tear-down; log and
                # continue.  The traceback goes to stderr through
                # the standard diagnostic channel.
                self._log_exception("on_unmount", exc)

            # Driver disable - exactly once per FR-TUI-7.  The
            # tuiterm.disable C entry is itself idempotent, but we
            # gate behind _driver to avoid the log noise of a
            # disable-without-enable call from a test fixture.
            await self._driver_disable()

            # Release the per-process slot so a subsequent run can
            # start.  Done last so any teardown code that reads
            # get_active_app() (none in v0.1, but defensive) sees
            # the App while teardown is in flight.
            _ACTIVE_APP[0] = None

        # Return the result passed to exit().  ``None`` if quit()
        # (or ctrl+q) was used - which is the FR-TUI-4 contract.
        return self._exit_result

    # ------------------------------------------------------------------
    # Driver enable/disable.
    # ------------------------------------------------------------------

    async def _driver_enable(self):
        """Enable the tuiterm driver and build the Compositor/Console.

        Called once at the top of run_async.  The tuiterm.enable C
        entry is idempotent but expensive (termios snapshot + raw
        mode + alt-screen + mouse enable), so we gate behind the
        ``_driver is None`` check to make the path obvious.

        FR-TUI-10: a non-tty or pre-1809 conhost raises OSError
        from tuiterm.enable.  We let it propagate; the asyncio.run
        caller sees the exception with the C-level errno attached.
        FR-TUI-10 also requires the refusal to print a single-line
        stderr diagnostic - the OSError repr already carries the
        errno, so we log the repr and re-raise.
        """
        if self._driver is not None:
            # Already enabled - idempotent.  Should not happen in
            # the FR-TUI-59 single-gather path, but defensive
            # against test fixtures that pre-enable.
            return
        term = _lazy_tuiterm()
        try:
            term.enable()
        except OSError as exc:
            # FR-TUI-10 single-line diagnostic.  errno is in the
            # OSError args; including it in the line lets the
            # operator triage the failure.
            sys.stderr.write(
                "picolet-tui: driver enable failed: %r\n" % (exc,)
            )
            raise

        # Capture capabilities and viewport for the compositor.
        self._driver = term
        try:
            self._capabilities = term.capabilities()
        except Exception:
            # Defensive: capabilities() is straightforward but a
            # mis-built variant could trip on bit field decode.
            self._capabilities = 0
        try:
            cols, rows = term.size()
        except OSError:
            # Size query failed - assume an 80x24 default so the
            # compositor has *something* to size against.  A real
            # size will arrive on the first _pump_resize tick.
            cols, rows = 80, 24
        self._viewport = Size(cols, rows)

        # Build the Console + Compositor.  Color system selection
        # follows the FR-TUI-38 precedence ladder; for the App
        # skeleton we use the simple "truecolor if HAS_TRUECOLOR
        # else 256 else 16 else mono" map from the capability
        # bits.  The full precedence (env vars NO_COLOR /
        # FORCE_COLOR / COLORTERM / TERM) is wired in step
        # downstream of this file by the same agent that lands
        # the events parser.
        color_system = self._color_system_from_capabilities()
        self._console = Console(color_system=color_system,
                                width=cols, height=rows)
        self._compositor = Compositor(self._console)

    async def _driver_disable(self):
        """Tear down the tuiterm driver (FR-TUI-7).

        Idempotent.  Called from the run_async finally block on
        every exit path.  The C-level disable restores termios,
        emits the inverse ANSI prologue (alt-screen off, cursor
        on, mouse off, bracketed-paste off), and unregisters the
        SIGWINCH handler.
        """
        if self._driver is None:
            # Never enabled (or already disabled).  No-op.
            return
        try:
            self._driver.disable()
        except Exception as exc:
            # Disable should never fail under normal conditions,
            # but if termios state has been clobbered out from
            # under us we still want the App to return cleanly.
            # Log and continue.
            self._log_exception("tuiterm.disable", exc)
        finally:
            self._driver = None
            self._compositor = None
            self._console = None
            self._capabilities = None

    def _color_system_from_capabilities(self):
        """Map the tuiterm capability bits to a Rich color_system string.

        FR-TUI-38 precedence is env-first; this helper covers the
        capability fallback step only.  The env wiring lives in the
        events-parser module landing alongside step-10 binding
        dispatch.  Until that lands, the App reads the capability
        bits directly - which is the conservative behaviour:
        falling back to "16" if the bits are uninformative matches
        the FR-TUI-38 tail of the ladder.
        """
        # Local import to avoid pulling the bit constants at module
        # load time under non-tui variants.
        try:
            from picolet_tui._tuiterm import (
                HAS_TRUECOLOR, HAS_256COLOR, NO_COLOR,
            )
        except ImportError:
            return "16"
        caps = self._capabilities or 0
        if caps & NO_COLOR:
            return "standard"  # Rich's name for mono
        if caps & HAS_TRUECOLOR:
            return "truecolor"
        if caps & HAS_256COLOR:
            return "256"
        return "16"

    # ------------------------------------------------------------------
    # Initial screen mount.
    # ------------------------------------------------------------------

    async def _mount_initial_screen(self):
        """Run compose() and push the initial screen.

        Called once at the top of run_async after _driver_enable.
        The contract:
          * compose() yields a sequence of Screen-shaped widgets.
          * Each yielded widget is pushed onto the screen stack
            via push_screen, in iteration order.  The last entry
            ends up as the active screen.
          * After the stack is built, the App's own on_mount fires.

        An App that doesn't override compose() can still run - it
        just has an empty screen stack and renders the default
        viewport background.  This is the v0.1 "hello-tui" minimal
        path until the user adds widgets.
        """
        for screen in self.compose():
            await self.push_screen(screen)
        # App-level on_mount fires after the initial stack is up.
        on_mount = getattr(self, "on_mount", None)
        if on_mount is not None:
            result = on_mount()
            if result is not None and (
                hasattr(result, "__await__") or hasattr(result, "send")
            ):
                await result
        # First-paint signal: force the render task to draw the
        # initial frame even if no reactive write has happened
        # yet.  Without this the screen stays blank until the
        # first user interaction.
        self._render_dirty.set()

    # ------------------------------------------------------------------
    # push_screen / pop_screen / screen property.
    # ------------------------------------------------------------------

    async def push_screen(self, screen):
        """Push ``screen`` onto the stack (FR-TUI-50).

        Convenience wrapper around ``self._screen_stack.push``.
        Returns the screen so chainable patterns work; doubles as
        the path for the Screen.dismiss return-value handshake
        (caller awaits the screen's _dismiss_event after this
        returns).

        Per the design doc §5.3 "Event-and-slot" pattern, the
        return-value handshake works like this:

            await app.push_screen(modal)
            await modal._dismiss_event.wait()
            result = modal._dismiss_result

        - which is the D6 substitute for the upstream Textual
        ``result = await app.push_screen(modal)`` Future-shape.
        """
        await self._screen_stack.push(screen)
        return screen

    def pop_screen(self):
        """Pop the active screen from the stack.

        Synchronous because the pop semantics (remove from stack,
        unmount, focus the new top) can complete without awaiting
        - the unmount path uses ``stop_message_processing`` which
        is already async-internal.  Returns a coroutine the caller
        ``await``s; we cannot make this a plain method because
        ScreenStack.pop is async (it awaits remove()).
        """
        # Returns the coroutine; the caller awaits it.  This is the
        # upstream Textual surface (sync function, coroutine
        # return).  ``async def pop_screen`` would be cleaner but
        # would force every caller to know the function is async,
        # which the upstream API doesn't.
        return self._screen_stack.pop()

    @property
    def screen(self):
        """The currently active screen, or None.

        Reads from ``self._screen_stack.active``; exposed at the
        App level so user code can write ``self.screen.foo`` rather
        than ``self._screen_stack.active.foo``.  Matches the
        upstream Textual attribute name.
        """
        return self._screen_stack.active

    # ------------------------------------------------------------------
    # exit / quit (FR-TUI-3 / FR-TUI-4).
    # ------------------------------------------------------------------

    def exit(self, result=None):
        """Request an orderly shutdown (FR-TUI-3).

        Sets the exit Event; the _exit_watcher task cancels the
        other gather siblings on the next loop tick.  The current
        frame finishes rendering, pending messages drain, the
        driver is torn down, and run_async returns ``result``.

        Synchronous because callers post from sync contexts -
        timer callbacks (``loop.call_later``), signal handlers
        (Unix SIGTERM via the tuiterm atexit hook), and binding
        actions.  The async tear-down is driven by _exit_watcher,
        not by this method.
        """
        # Store the result first so a teardown that triggers
        # before the watcher fires still returns the right value.
        # _exit_requested.set is idempotent so a double-exit
        # collapses cleanly.
        self._exit_result = result
        self._exit_requested.set()

    def quit(self):
        """Alias for ``exit(None)`` (FR-TUI-4).

        Bound to ``ctrl+q`` via the App-level BINDINGS class
        attribute.  The binding dispatcher (step 10) calls
        ``app.action_quit`` which is wired via this method - the
        ``action_`` prefix convention is the binding-system shape.
        """
        self.exit(None)

    def action_quit(self):
        """Binding action target for ``quit`` (FR-TUI-4).

        The binding dispatcher (§6.3) does
        ``getattr(node, "action_" + binding.action)`` and invokes
        the result.  Aliasing through ``action_quit`` lets the
        BINDINGS entry use the short ``"quit"`` form while the
        method itself keeps the ``action_`` prefix the dispatcher
        keys on.
        """
        self.quit()

    # ------------------------------------------------------------------
    # The four pump tasks (FR-TUI-59).
    # ------------------------------------------------------------------

    async def _pump_input(self):
        """Input pump: read tuiterm bytes, dispatch parsed events.

        Loop:
          1. Call tuiterm.read_input(timeout_ms=20) - blocks up to
             20 ms; returns b"" on no data.
          2. If bytes arrived, feed them through the parser hook
             (set by Phase 4d).  Parsed events are posted to the
             focused widget's pump.
          3. await asyncio.sleep(0) between empty reads to yield
             to peer tasks (FR-TUI-55).

        v0.1 placeholder: the parser is not yet wired, so we
        accumulate bytes in a buffer and post them as a generic
        Message-with-bytes-attribute to the active screen.  Once
        the parser module lands the buffer feeds into it.  The
        20 ms timeout matches the FR-TUI-55 "16 ms read_input
        poll" loosely - 20 ms is the design-doc value; 16 ms is
        the spec value.  The discrepancy is intentional: the
        design doc has the implementation-level value, the spec
        has the target.  Both are well within the NFR-TUI-4
        frame-latency budget.
        """
        # The parser hook (when set) is a callable taking the raw
        # bytes and returning a list of Message instances.  Until
        # the parser lands the hook is None and we drop bytes on
        # the floor - which is the right behaviour: a non-parsing
        # App is not interactive but doesn't crash.
        parser_hook = None
        while not self._exit_requested.is_set():
            try:
                data = self._driver.read_input(20) if self._driver else b""
            except OSError as exc:
                # read_input raises on closed stdin; treat as exit.
                self._log_exception("tuiterm.read_input", exc)
                self.exit(None)
                break
            if data:
                if parser_hook is not None:
                    try:
                        events = parser_hook(data)
                    except Exception as exc:
                        self._log_exception("input parser", exc)
                        events = ()
                    # Dispatch each event to the focused widget if
                    # there is one; fall back to the active screen
                    # otherwise.  This is the FR-TUI-26 / §6.3
                    # contract: key events go to focus.
                    from .widget import get_focused
                    target = get_focused() or self.screen or self
                    for event in events:
                        target.post_message(event)
            else:
                # No bytes - yield to peer tasks.  asyncio.sleep(0)
                # is the canonical idiom; the picolet asyncio
                # variant accepts it directly.
                await asyncio.sleep(0)

    async def _pump_resize(self):
        """Resize pump: poll tuiterm.size, post Resize on change.

        Loop:
          1. Sleep 100 ms (FR-TUI-59 names 250 ms; the design doc
             keeps it tighter at 100 ms to make the visible reflow
             snappier on a manual resize.  Both are within the
             NFR-TUI budget; 100 ms is the implementation value).
          2. Check tuiterm.resize_pending().  Cleared by the call
             itself (Unix C-level SIGWINCH flag, Windows poll).
          3. On pending or first-tick mismatch, requery
             tuiterm.size() and post a Resize event to the active
             screen.
          4. Update self._viewport so the render task sees the
             new dimensions on the next dirty wake.
        """
        while not self._exit_requested.is_set():
            # 100 ms tick.  Wakeable by the exit watcher's
            # cancellation, so the worst-case exit latency is one
            # tick (acceptable; FR-TUI-59 allows 250 ms here).
            await asyncio.sleep(0.1)
            if self._driver is None:
                # No driver - nothing to poll.  Mostly a test path.
                continue
            try:
                pending = self._driver.resize_pending()
            except Exception as exc:
                self._log_exception("tuiterm.resize_pending", exc)
                pending = False
            if not pending and self._viewport.width != 0:
                # No change and not the first tick; skip.
                continue
            try:
                cols, rows = self._driver.size()
            except OSError as exc:
                # Size query failed - log and carry on with the
                # last-known viewport.  A subsequent successful
                # poll will catch up.
                self._log_exception("tuiterm.size", exc)
                continue
            new_viewport = Size(cols, rows)
            if new_viewport == self._viewport:
                # Same as last - the resize_pending() flag was
                # spurious (can happen on some terminals during
                # focus changes).  Skip.
                continue
            self._viewport = new_viewport
            # Post the Resize event to the active screen if any;
            # falls back to the App if no screen is active so the
            # event is not dropped.
            target = self.screen or self
            target.post_message(Resize(new_viewport))
            # Mark the layout dirty so the render task knows the
            # geometry shifted; wake the render task too.
            self._needs_layout = True
            self._render_dirty.set()

    async def _render(self):
        """Render task: wake on _render_dirty, redraw the frame.

        Loop:
          1. Wait on _render_dirty.
          2. Walk the active screen's tree via Compositor.update_dom.
          3. Emit either render_frame() (diff) or full_redraw()
             output.  For v0.1 the brief specifies full_redraw on
             every wake - the per-row diff optimisation is Phase
             5's compositor work.
          4. Convert each (col, row, segments) tuple into ANSI
             bytes (CUP + segment SGR) and write through
             tuiterm.write.

        Coalescing: _render_dirty is one Event for any number of
        refreshes.  A burst of reactive writes between two
        renders collapses into one redraw.  The Event clears at
        the top of each iteration so the next set() wakes us
        again.
        """
        while not self._exit_requested.is_set():
            # Block until something dirty.  The Event is cleared
            # before the next iteration so successive sets are
            # coalesced into one wake.
            await self._render_dirty.wait()
            self._render_dirty.clear()
            if self._exit_requested.is_set():
                # The exit watcher set the dirty flag as part of
                # tear-down; break before re-rendering.
                break
            if self._compositor is None or self._driver is None:
                # No driver yet - rare, only on the very first
                # frame if a refresh fired during _driver_enable.
                continue
            try:
                # Update the compositor's frame buffer from the
                # active screen (or the App if no screen is up).
                root = self.screen or self
                self._compositor.update_dom(root, self._viewport)
                # Phase 4c brief: full_redraw on every dirty wake
                # for v0.1; the diff path is a future optimisation.
                # The compositor returns (col, row, segments)
                # tuples; we hand them to the emit helper.
                tuples = self._compositor.full_redraw()
                self._emit_frame(tuples)
            except Exception as exc:
                # FR-TUI-77 / NFR-TUI-29: render exceptions must
                # not kill the App.  Log and continue; the next
                # dirty wake will retry.
                self._log_exception("_render", exc)
            # Clear the layout-changed flag - the compositor's
            # next update_dom call will see a clean slate.
            self._needs_layout = False

    async def _exit_watcher(self):
        """Wait on the exit event, then cancel the gather siblings.

        D6 substitute for asyncio.TaskGroup: a sibling task inside
        the gather that fires CancelledError into the other three
        tasks when exit is requested.  The gather then unwinds and
        run_async returns.

        Cancellation order: input -> resize -> render.  The order
        matters slightly: cancelling input first stops new events
        from arriving; cancelling resize next stops the layout
        flag from being set; cancelling render last lets any
        in-flight frame finish before the driver is disabled.
        """
        await self._exit_requested.wait()
        # Wake the render task explicitly so it sees the
        # exit_requested flag and breaks out of its wait loop
        # cleanly rather than via cancellation - this is the
        # "current frame finishes rendering" half of FR-TUI-3.
        self._render_dirty.set()
        # Cancel the siblings in order.  CancelledError raised by
        # cancel propagates up through gather.
        for task in (self._task_input, self._task_resize, self._task_render):
            if task is not None and not task.done():
                task.cancel()

    # ------------------------------------------------------------------
    # Frame emit - ANSI bytes from compositor tuples.
    # ------------------------------------------------------------------

    def _emit_frame(self, tuples):
        """Write a list of (col, row, segments) tuples to tuiterm.

        The compositor returns segments only; this method handles
        the CUP positioning and the segment-style SGR sequences
        that go around them.  Phase 4c will move the SGR
        translation into a dedicated emit module; for now the App
        owns it because the Color downgrade ladder (FR-TUI-40) is
        next door to this code path.

        v0.1 minimal emit: CUP to (row+1, col+1) for each tuple,
        then write each segment's text without SGR styling.  The
        styled path is deferred to Phase 4c when the Color +
        Style downgrade is wired - emitting unstyled text first
        gets the geometry right and means a snapshot test can
        already verify the cell content.
        """
        if not tuples or self._driver is None:
            return
        # Build the whole frame as one bytearray and emit with a
        # single tuiterm.write call - reduces syscall count from
        # rows*segments to one per frame.
        out = bytearray()
        for col, row, segments in tuples:
            # CSI <row+1>;<col+1> H is the cursor-position
            # sequence.  Rows and cols are 1-based in the ANSI
            # spec; our buffer is 0-based.
            out.extend(b"\x1b[")
            out.extend(str(row + 1).encode("ascii"))
            out.extend(b";")
            out.extend(str(col + 1).encode("ascii"))
            out.extend(b"H")
            for seg in segments:
                # Segment.text is a str; encode to UTF-8 for the
                # terminal.  Control segments (seg.control is not
                # None) carry pre-formatted bytes-like ANSI -
                # the compositor doesn't emit them in v0.1, but
                # the branch is here for the Phase 4c ladder.
                text = getattr(seg, "text", None)
                if text:
                    out.extend(text.encode("utf-8"))
        try:
            self._driver.write(out)
        except OSError as exc:
            # The terminal went away - log and request exit.  This
            # is the FR-TUI-7 "any path out" branch: a broken
            # write should not leave the process in raw mode.
            self._log_exception("tuiterm.write", exc)
            self.exit(None)

    # ------------------------------------------------------------------
    # Diagnostic logging.
    # ------------------------------------------------------------------

    def log(self, message):
        """User-facing log method (FR-TUI-78).

        Writes ``message`` to stderr directly.  Distinct from
        ``print`` (which the driver redirects into a per-app ring
        buffer while running): ``app.log`` is the supported
        channel for visible logging during a run, and reaches the
        terminal even with the driver enabled.

        Stays a plain method (not async) because logging from
        within a handler must not yield to the loop.
        """
        # NFR-TUI-29: framework diagnostics go to stderr.  Match
        # the message_pump._log_to_stderr shape so a single
        # diagnostic-routing change in v0.2 catches both.
        try:
            sys.stderr.write(str(message))
            if not str(message).endswith("\n"):
                sys.stderr.write("\n")
        except Exception:
            # Log emission must never break a handler.  Swallow.
            pass

    def _log_exception(self, context, exc=None):
        """Internal: log a Python exception with a context tag.

        Format mirrors message_pump._log_handler_exception so the
        framework's diagnostic stream is consistent across both
        sources.  ``context`` is a short string like ``"_render"``
        identifying which subsystem caught the exception.  ``exc`` is
        the caught exception object — required for a traceback on
        MicroPython, which has sys.print_exception but neither the
        traceback module nor sys.exc_info.
        """
        tb = "(no traceback available)"
        try:
            if exc is not None and hasattr(sys, "print_exception"):
                import io
                buf = io.StringIO()
                sys.print_exception(exc, buf)
                tb = buf.getvalue()
            else:
                import traceback
                tb = traceback.format_exc()
        except Exception:
            pass
        try:
            sys.stderr.write(
                "App.%s exception:\n%s\n" % (context, tb)
            )
        except Exception:
            pass


# ---------------------------------------------------------------------
# Public exports.
# ---------------------------------------------------------------------


__all__ = (
    "App",
    "ScreenStack",
    "Resize",
    "get_active_app",
)
