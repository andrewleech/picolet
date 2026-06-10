"""picolet_tui._textual.message_pump - MessagePump base class.

The MessagePump owns one message queue per node, drives the per-node
async dispatch loop, and walks the DOM parent chain when a message
bubbles.  It is the leaf-side substrate every DOMNode / Widget / Screen
/ App stands on.

Spec coverage:
  * FR-TUI-12 - DOM walk from originating widget toward root; ``stop()``
    halts.
  * FR-TUI-14 - name-based ``on_<message_name_snake_case>`` fallback,
    arity recorded at @widget decoration time, fires *after* the
    @on-decorated handlers at the same node.
  * FR-TUI-23..25 - mount / unmount lifecycle hooks (process_messages is
    started by the mount path and cancelled at remove time).
  * FR-TUI-68 - bubbling reaches ``@on(...)`` ancestors.
  * FR-TUI-77 - user-handler exceptions caught at the dispatch boundary,
    logged to stderr, bubbling continues.
  * NFR-TUI-9 / D6 - deque-plus-Event pump rather than asyncio.Queue.
  * NFR-TUI-29 - all internal diagnostics go to stderr.

Design-doc references (textual-core-design.md):
  * §3.1 - queue choice (deque + cap, soft 4096), parent pointer is an
    explicit strong ref.
  * §3.3 - the process_messages coroutine.
  * §3.4 - the bubbling _dispatch walk.
  * §3.5 - post_message overflow policy (oldest-drop, coalescing via
    Message.can_replace).
  * §4.3 - the mount/unmount path that owns this pump's lifecycle (the
    Widget agent calls into ``start_message_processing`` /
    ``stop_message_processing`` from there).
"""

# asyncio is the picolet asyncio - the variant ships uasyncio under the
# stdlib name (NFR-TUI-9).  No TaskGroup, no Future; we use gather and
# Event everywhere per D6.
import asyncio

# sys.stderr is the only diagnostic channel (NFR-TUI-29).  The pump
# writes user-handler tracebacks and queue-overflow notices here; the
# Compositor owns stdout.
import sys

# collections.deque - NFR-TUI-9 build flag enables it; without it the
# queue would have to be a list with O(n) popleft, which the synthesis
# rejects (§3.1).
from collections import deque

# camel_to_snake powers the ``on_<message_name>`` dispatch in §3.4.  The
# helper lives in _textual.case (Phase 4a leaf).
from .case import camel_to_snake

# Message + Selector live in the sibling module.  Note Message imports
# nothing from this module - the dependency is one-way to keep the
# bootstrap import order from cycling.
from .message import Message  # noqa: F401  - re-exported for downstream agents


# Per-pump queue cap.  Design §3.1 / spec §3.4.  Public attribute so
# tests and benchmark tools can probe it without touching internals.
_DEFAULT_QUEUE_CAP = 4096


def _log_to_stderr(msg):
    """Single funnel for framework diagnostics (NFR-TUI-29).

    Centralised so v0.2 can swap in App.log routing without touching
    every call site.  Kept module-private to discourage app code from
    writing through it - user logging goes through App.log per
    FR-TUI-78.
    """
    # write+flush rather than print() so the message reaches stderr
    # before any subsequent crash unwinds the loop.
    try:
        sys.stderr.write(msg)
        if not msg.endswith("\n"):
            sys.stderr.write("\n")
    except Exception:
        # Diagnostic emission must never break the pump.  Swallow.
        pass


def _log_handler_exception(node, message, exc):
    """FR-TUI-77 user-handler exception logger.

    The exception is logged with type, repr of the message, and the
    traceback; the caller resumes bubbling to the next ancestor.
    """
    try:
        import traceback
        tb = "".join(traceback.format_exception(type(exc), exc,
                                                getattr(exc, "__traceback__", None)))
    except Exception:
        # MicroPython's traceback module may not have format_exception
        # in some build flavours; fall back to repr.
        tb = repr(exc)
    _log_to_stderr(
        "User handler exception in %s while handling %r:\n%s" %
        (type(node).__name__, message, tb)
    )


def _log_queue_overflow(pump, dropped):
    """Queue overflow logger.

    Rate-limited only by the pump's own dispatch tempo: a wedged widget
    that overflows continuously produces one log line per drop, but the
    line is short and the typical run is bounded by the test that
    discovers the wedge.
    """
    _log_to_stderr(
        "MessagePump %s queue overflow: dropped oldest %r" %
        (type(pump).__name__, dropped)
    )


async def _maybe_await(call_result):
    """Await ``call_result`` if it is a coroutine; otherwise pass through.

    Handler methods may be plain ``def`` or ``async def``.  The pump
    invokes the method synchronously, then awaits the result if it
    presents the ``__await__`` / ``send`` shape of a coroutine.  This
    is the pattern the picolet ``_shims.callback`` module deliberately
    leaves to the caller: arity introspection is cheap, coroutine
    detection is "is the result awaitable".
    """
    # MicroPython has no asyncio.iscoroutine; the duck-type check is to
    # see if the object is awaitable via __await__ or has a send method
    # (generator-coroutines).  Both upstream and MicroPython coroutines
    # carry __await__.
    if call_result is None:
        return None
    if hasattr(call_result, "__await__") or hasattr(call_result, "send"):
        return await call_result
    return call_result


async def _invoke_handler(handler, *args):
    """Call ``handler(*args)`` and await it if the result is awaitable.

    Centralised so the dispatch path has one place to decide
    sync-vs-async handler shape.  The same helper is used for both the
    @on-decorated branch and the on_<name> branch in §3.4.
    """
    result = handler(*args)
    return await _maybe_await(result)


class MessagePump:
    """Per-node message queue + dispatch loop.

    Each DOMNode (and therefore each Widget / Screen / App) instantiates
    one of these.  The queue is private to the node; bubbling happens
    by post_message walking to the parent's queue through the parent
    chain, but each node's *own* @on / on_<name> handlers are invoked
    by its own process_messages task before the message is forwarded.

    Parent pointer is an explicit strong reference (design §3.2,
    synthesis decision against weakref.ref).  Children carry strong
    refs to their parent and parents carry strong refs to children;
    the cycle is broken by explicit ``stop_message_processing`` (and
    by ``Widget.remove`` in agent 5's code) at unmount time.
    """

    def __init__(self, parent=None):
        # Deque + cap is the pre-0.50 Textual pattern (D6 baseline).
        # See module docstring re: NFR-TUI-9.
        # Two-argument construction: MicroPython's deque requires
        # (iterable, maxlen) and CPython accepts the same form.  The
        # cap is enforced manually in post_message (candidate-based
        # coalescing before drop-oldest), so the native maxlen is just
        # a backstop that the manual path keeps from ever triggering.
        self._queue = deque((), _DEFAULT_QUEUE_CAP)
        self._queue_cap = _DEFAULT_QUEUE_CAP

        # Explicit strong ref to parent - design §3.2.  See class
        # docstring re: weakref policy.
        self._parent = parent

        # Lifecycle flags.  ``_closing`` is the cooperative shutdown
        # flag (set by stop_message_processing); ``_closed`` flips to
        # True once process_messages observes _closing and exits.
        self._closing = False
        self._closed = False

        # The asyncio.Task running ``process_messages``.  None until
        # ``start_message_processing`` is called.  Cancelled and
        # awaited during shutdown.
        self._task = None

        # Wake signal: post_message sets it after appending, the loop
        # body clears it before going to sleep.  asyncio.Event.set is
        # idempotent, so multiple posts coalesce into one wake.
        self._wake = asyncio.Event()

        # Children list - populated by the mount path in the Widget
        # agent.  Initialised here because @widget-less subclasses
        # (Message subclasses, in particular) inherit __init__ and
        # would otherwise have this slot missing.  Mount/unmount is
        # Phase 4b agent 5's territory; we just declare the attribute.
        self._children = []

    # ------------------------------------------------------------------
    # Posting.
    # ------------------------------------------------------------------

    def post_message(self, message):
        """Enqueue ``message`` on this pump.

        Returns True on success, False if the pump is shutting down.
        Synchronous because timer callbacks (loop.call_later) and the
        FR-TUI-77 exception path post from sync contexts (design
        §3.5).

        Overflow policy: when ``len(queue) >= cap``, the oldest
        message is dropped (logged to stderr).  Before dropping,
        consult ``message.can_replace(oldest)`` to give coalescing-
        eligible message types a chance to compact the queue.  See
        the Message.can_replace docstring for the contract.
        """
        if self._closing:
            return False

        # Record sender for selector matching at dispatch time.
        # Setting it here rather than in Message.__init__ keeps Message
        # free of MessagePump knowledge (design §3 boundary).  ``self``
        # is the node posting *from* (i.e. the widget that called
        # ``self.post_message``); the dispatch walk starts from this
        # node and bubbles up.
        if isinstance(message, Message) and message._sender is None:
            message._sender = self

        if len(self._queue) >= self._queue_cap:
            # Coalescing path: if the incoming message claims it can
            # replace any queued message, drop that one and proceed
            # without growing the queue past cap.  Walk from oldest so
            # the more recent ones - which are more likely to be the
            # ones the user cares about - survive.
            replaced = False
            if isinstance(message, Message):
                for i in range(len(self._queue)):
                    candidate = self._queue[i]
                    if message.can_replace(candidate):
                        # MicroPython's deque supports neither
                        # __delitem__ nor rotate; cycle the queue
                        # through itself once, skipping the doomed
                        # element.  O(n) but rare (only at cap).
                        for j in range(len(self._queue)):
                            item = self._queue.popleft()
                            if j != i:
                                self._queue.append(item)
                        replaced = True
                        break
            if not replaced:
                # No coalescing available - drop the oldest, log it.
                # This is the spec's "drop oldest" policy explicitly.
                dropped = self._queue.popleft()
                _log_queue_overflow(self, dropped)

        self._queue.append(message)
        self._wake.set()
        return True

    # ------------------------------------------------------------------
    # Lifecycle - start/stop the dispatch loop.
    # ------------------------------------------------------------------

    def start_message_processing(self):
        """Spawn the asyncio.Task driving ``process_messages``.

        Called from the mount path (FR-TUI-23).  Idempotent: a second
        call while a task is already running is a no-op.  Returns the
        task so the mount-awaitable can chain off it if needed.
        """
        if self._task is not None:
            return self._task
        self._closing = False
        self._closed = False
        # asyncio.create_task is available in MicroPython's asyncio
        # (NFR-TUI-9).  No TaskGroup - synthesis D6 pins gather/Task as
        # the only multiplexing primitives we use.
        self._task = asyncio.create_task(self.process_messages())
        return self._task

    async def stop_message_processing(self):
        """Signal the dispatch loop to drain-and-exit, then await it.

        Called from ``Widget.remove`` (FR-TUI-24) - the Widget agent
        wires this in.  Cancellation cooperates with the loop's
        ``while not self._closing`` check; the wake event is set so
        an idle pump observes the flag immediately rather than
        blocking on ``await self._wake.wait()``.
        """
        self._closing = True
        # Wake an idle pump so it observes _closing and exits cleanly.
        # If the pump is mid-dispatch this is a no-op (set is idempotent
        # and clear-then-wait happens after the dispatch returns).
        self._wake.set()

        if self._task is None:
            self._closed = True
            return

        # Give the loop one tick to observe _closing and exit cleanly;
        # if it does not (because the current handler is blocking on
        # await), cancel.  This matches the design §4.3 unmount path:
        # set the flag, set the wake, cancel the task, await the
        # CancelledError.
        try:
            self._task.cancel()
            await self._task
        except asyncio.CancelledError:
            # Expected when cancel() landed mid-await.
            pass
        except Exception as exc:
            # Should not happen - process_messages catches user
            # exceptions itself - but if a framework-level exception
            # escaped, log it.
            _log_handler_exception(self, None, exc)

        self._task = None
        self._closed = True

    # ------------------------------------------------------------------
    # The dispatch loop.
    # ------------------------------------------------------------------

    async def process_messages(self):
        """Run loop for this pump.

        Yields back to the event loop on every iteration, both when
        the queue is empty (via ``await self._wake.wait()``) and
        between messages (via ``await self._dispatch(...)`` itself).
        Cancelled by ``stop_message_processing``.

        Per design §3.3, exceptions raised by user handlers are caught
        at the _dispatch boundary and logged; the pump itself never
        dies on a handler exception (FR-TUI-77).
        """
        while not self._closing:
            if not self._queue:
                # Idle - clear the wake flag and sleep until a new
                # message arrives or shutdown is signalled.  The
                # clear-then-wait order matters: if post_message sets
                # the flag between the queue-empty check and the
                # clear, the subsequent wait would block forever.
                # Re-check the queue after the wait to close that
                # window.
                self._wake.clear()
                # Re-check after clear: a post between len() and clear
                # would set the flag again, and wait() would return
                # immediately.  No race - asyncio is single-threaded
                # (NFR-TUI-11) and post_message is sync.
                if not self._queue and not self._closing:
                    await self._wake.wait()
                continue

            message = self._queue.popleft()
            try:
                await self._dispatch(message)
            except asyncio.CancelledError:
                # Cancellation aborts the loop; the finally on the
                # caller cleans up.
                raise
            except Exception as exc:
                # FR-TUI-77 - any non-CancelledError from dispatch is
                # a user-handler exception that escaped the inner
                # try/except in _dispatch.  Should be rare, but log
                # rather than crash.
                _log_handler_exception(self, message, exc)

        self._closed = True

    # ------------------------------------------------------------------
    # Dispatch - self-only and bubbling forms.
    # ------------------------------------------------------------------

    async def _dispatch_self(self, message):
        """Dispatch ``message`` against this node's handlers only.

        Used when a Message subclass opts out of bubbling
        (``Message.bubble = False`` at class level) - the pump still
        gives the originating node a chance to handle.  This is the
        split design §3.3 calls out: ``_dispatch_self`` for the
        local-only case, ``_dispatch`` for the chain walk.
        """
        await self._dispatch_at_node(self, message)

    async def _dispatch(self, message):
        """Walk node -> parent -> ... -> root, applying handlers.

        Implements design §3.4 verbatim.  At each node:
          1. Look up the node's _tui_widget_meta dict (one attr access).
          2. Fire @on-decorated handlers for ``type(message)``, in
             registration order, respecting their selectors.
          3. Fire the on_<message_name_snake_case> name-based handler
             if defined on this node's class (FR-TUI-14 says name-based
             fires *after* @on-decorated, so this ordering is the spec).
          4. Stop if any handler returned True OR set ``_stop_bubble``.

        The "fall off the top" case is no further work - there is no
        App-level "unhandled message" dispatch in v0.1.  The Key event
        path in §6.3 handles its own fall-through (re-dispatching as
        a normal Message); other messages simply terminate.

        Bubbling is governed by the *class-level* ``Message.bubble``
        flag.  If False, we dispatch only on this node and return.
        """
        # ``self`` is the originating node - the pump that received the
        # post.  Start the walk here, not at the parent.
        node = self

        # Class-level bubble opt-out.  The check is one getattr per
        # message because the design (§3.1) keeps the attribute at the
        # *class* level - and Message.bubble defaults to True - so the
        # check is essentially free for the common case.
        bubble = getattr(message, "bubble", True)

        while node is not None:
            stopped = await self._dispatch_at_node(node, message)
            if stopped:
                return
            if not bubble:
                # Opted-out message types stay on the originating node.
                return
            node = node._parent

    async def _dispatch_at_node(self, node, message):
        """Fire all handlers on ``node`` for ``message``.

        Returns True if dispatch should stop (handler returned True
        or message.stop() was called); False to continue to parent.

        Factored out so _dispatch_self (no walk) and _dispatch (walk)
        share the same inner code path.
        """
        # Acquire meta.  A node without ``_tui_widget_meta`` is one
        # that pre-dates the @widget decorator running on its class -
        # a base class like MessagePump itself, or a user oversight
        # the design's R3 guard (§1.3) catches at instantiation.  At
        # dispatch time we tolerate the absence: ``meta`` defaults to
        # an empty dict and the lookups below cleanly miss.
        meta = getattr(type(node), "_tui_widget_meta", None)
        if meta is None:
            # No @widget decoration - no handlers.  Bubble onward.
            return False

        message_type = type(message)

        # ---- @on-decorated handlers (preferred, per FR-TUI-14). ----
        handlers = meta.get("handlers", {})
        for entry in handlers.get(message_type, ()):
            handler, selector = entry
            # Selector match - v0.1 selectors are placeholders (Phase
            # 4b agent 5 lands the parser); selector.matches returns
            # True for the default no-filter case.
            try:
                if not selector.matches(node, message):
                    continue
            except Exception as exc:
                _log_handler_exception(node, message, exc)
                continue

            try:
                result = await _invoke_handler(handler, node, message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # FR-TUI-77 - log and continue to the next handler at
                # this level (do not abort the level on one bad
                # handler).
                _log_handler_exception(node, message, exc)
                continue

            # Handler signalled stop, or it called message.stop().
            if result is True or message._stop_bubble:
                return True

        # ---- Name-based on_<message_name> fallback. ----
        # FR-TUI-14: name-based fires *after* @on-decorated handlers.
        # The lookup matches the design §3.4 verbatim: name resolved
        # from the message class via camel_to_snake.  vars() rather
        # than getattr() so we do not pick up parent-class on_*
        # methods - the @widget MRO merge handled inheritance at
        # decoration time, and dispatch sees the per-class meta.
        name = "on_" + camel_to_snake(message_type.__name__)
        name_handler = type(node).__dict__.get(name)
        if name_handler is not None and callable(name_handler):
            # Arity hint, recorded at decoration time by @widget
            # bucket-4 (§1.1).  ``name_handlers_by_name`` maps the
            # textual on_<name> string to a (handler, arity) tuple.
            # If the meta is missing the key, arity defaults to 2
            # (self + message) - the safest "pass the message"
            # behaviour, matching the callback shim's sentinel.
            arity_info = meta.get("name_handlers_by_name", {}).get(name)
            if arity_info is None:
                arity = 2
            else:
                arity = arity_info[1]

            try:
                if arity == 1:
                    # ``def on_foo(self)`` - no message argument.
                    result = await _invoke_handler(name_handler, node)
                else:
                    # ``def on_foo(self, message)`` - default shape.
                    result = await _invoke_handler(name_handler,
                                                   node, message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log_handler_exception(node, message, exc)
                # Continue to bubble - the level's handlers are done
                # (at most one name-based handler per node).
                return False

            if result is True or message._stop_bubble:
                return True

        return False
