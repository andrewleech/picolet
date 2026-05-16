# picolet._dispatcher — async IPC dispatcher (FR-IPC-{1,2,3,4,5}).
#
# Responsibilities:
#   - Command registry: @picolet.command async def name(args): ...
#   - Request/reply routing: picolet.invoke(name, args) over the transport
#   - Event channel: picolet.emit / picolet.on
#   - Run loop: picolet.run(transport=, main=)
#
# Wire format (architecture.md §IPC, FR-IPC-4) — newline-delimited JSON:
#
#   Request:   {"id": <int>, "cmd": <str>, "args": <obj>}
#   Reply OK:  {"id": <int>, "ok": true,  "result": <any-json>}
#   Reply Err: {"id": <int>, "ok": false, "error": {"type": <str>, "message": <str>}}
#   Event:     {"event": <str>, "data": <any-json>}
#
# State model: all dispatcher state lives on a ``Dispatcher`` instance.
# A module-level singleton ``_default`` is the implicit dispatcher used
# by the package-level ``command`` / ``invoke`` / ``emit`` / ``on`` /
# ``run`` shims, matching v1's "one transport per process" model.
# Tests and embedders that need state isolation can construct their own
# Dispatcher() and drive it directly.

import sys

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False

from ._errors import build_exception, error_payload, RemoteError
from ._transport import StdioTransport


# Defensive caps on concurrent in-flight invokes (one per direction).
# When either table grows beyond the cap, the affected path raises (for
# outbound) or replies with a structured error (for inbound).  This is a
# safety net against a runaway loop or a broken peer.
MAX_IN_FLIGHT = 1024
MAX_INBOUND_IN_FLIGHT = 1024


class _PendingInvoke:
    """Future-substitute for tracking an outbound invoke.

    MicroPython's asyncio has no Future class, so we wrap an Event with
    a result/exception slot and a wait() coroutine.
    """

    __slots__ = ("_evt", "_result", "_exc", "_set")

    def __init__(self):
        self._evt = asyncio.Event()
        self._result = None
        self._exc = None
        self._set = False

    def set_result(self, r):
        if self._set:
            return
        self._result = r
        self._set = True
        self._evt.set()

    def set_exception(self, exc):
        if self._set:
            return
        self._exc = exc
        self._set = True
        self._evt.set()

    async def wait(self):
        await self._evt.wait()
        if self._exc is not None:
            raise self._exc
        return self._result


class Dispatcher:
    """Owns the per-process IPC dispatcher state.

    Module-level ``command``/``invoke``/``emit``/``on``/``run`` delegate
    to a default singleton (``_default``).  Tests that want state
    isolation can instantiate their own Dispatcher; embedders that want
    to side-channel an in-process peer endpoint can stash it on
    ``inprocess_peer`` (used by picolet_ui._lvgl.run when no transport is
    supplied).
    """

    def __init__(self):
        # Command name → async callable.  Populated by @picolet.command.
        self._commands = {}
        # Subscriber topic → list of handler callables (sync or async).
        self._subscribers = {}
        # Pending outbound invoke id → _PendingInvoke.
        self._pending_invokes = {}
        # Monotonically increasing id counter for outbound requests.
        self._next_invoke_id = 1
        # The currently active transport (set by ``run`` for the
        # duration of the loop).  None outside a ``run`` call.
        self._active_transport = None
        # Count of inbound _handle_request tasks currently executing.
        # Bumped before asyncio.create_task and decremented in the task
        # body's finally.  See _handle_request_wrapper.
        self._inbound_in_flight = 0
        # Optional side-channel: when picolet_ui (lvgl variant) sets up an
        # InProcessTransport pair, it stashes the user-facing endpoint
        # here so app code can picolet.invoke() into its own dispatcher.
        # Modules that wrap the dispatcher set this; nothing inside
        # _dispatcher uses it.
        self.inprocess_peer = None

    # ------------------------------------------------------------------
    # Command decorator (FR-IPC-1)
    # ------------------------------------------------------------------

    def command(self, fn_or_name):
        """Register an async function as an IPC command.

        Two forms:

            @picolet.command
            async def greet(args): ...           # registered as "greet"

            @picolet.command("greet_v2")
            async def greet(args): ...           # registered as "greet_v2"

        The wrapped function MUST be ``async def``; otherwise TypeError
        is raised at decoration time.  The handler is called with the
        request's ``args`` as a single positional argument.
        """
        if callable(fn_or_name):
            # Bare @picolet.command (no parentheses).
            return self._register_command(fn_or_name.__name__, fn_or_name)
        if isinstance(fn_or_name, str):
            name = fn_or_name

            def _decorate(fn):
                return self._register_command(name, fn)

            return _decorate
        raise TypeError(
            "picolet.command must be used as @picolet.command or @picolet.command('name')"
        )

    def _register_command(self, name, fn):
        if not _looks_like_coroutine_function(fn):
            raise TypeError(
                "picolet.command requires an async def function (got {})".format(
                    type(fn).__name__
                )
            )
        self._commands[name] = fn
        return fn

    # ------------------------------------------------------------------
    # Outbound invoke (FR-IPC-2)
    # ------------------------------------------------------------------

    async def invoke(self, name, args=None, timeout=None):
        """Send a request to the peer and await the reply (FR-IPC-2).

        Returns the result value on success.  On a remote error, raises:

          - the matching builtin exception class (ValueError, KeyError,
            etc.) when the peer's error ``type`` is in the allow-list, or
          - ``picolet.RemoteError`` carrying ``type_name`` + ``message``
            otherwise.

        Raises ``RemoteError("transport closed")`` if the transport is
        torn down while the reply is outstanding.

        ``timeout`` (seconds) wraps the await in ``asyncio.wait_for`` and
        raises ``asyncio.TimeoutError`` if no reply arrives in time.  The
        pending-invoke entry is removed in that case.
        """
        transport = self._active_transport
        if transport is None:
            raise RuntimeError("picolet.invoke called outside picolet.run")
        if not _HAVE_ASYNCIO:
            raise RuntimeError("picolet.invoke requires asyncio")
        if len(self._pending_invokes) >= MAX_IN_FLIGHT:
            raise RuntimeError("too many in-flight invokes")
        req_id = self._next_invoke_id
        self._next_invoke_id += 1
        pending = _PendingInvoke()
        self._pending_invokes[req_id] = pending
        try:
            await transport.send({"id": req_id, "cmd": name, "args": args})
        except BaseException:
            self._pending_invokes.pop(req_id, None)
            raise
        try:
            if timeout is None:
                return await pending.wait()
            return await asyncio.wait_for(pending.wait(), timeout)
        finally:
            # Remove the entry whether the reply arrived, timed out, or
            # the waiter was cancelled.  set_result/set_exception is
            # harmless if called against the now-dropped entry.
            self._pending_invokes.pop(req_id, None)

    # ------------------------------------------------------------------
    # Event channel (FR-IPC-3)
    # ------------------------------------------------------------------

    async def emit(self, topic, data=None):
        """Push an event to the peer.  No reply expected (FR-IPC-3)."""
        transport = self._active_transport
        if transport is None:
            raise RuntimeError("picolet.emit called outside picolet.run")
        await transport.send({"event": topic, "data": data})

    def on(self, topic, handler):
        """Register a handler for inbound events on ``topic`` (FR-IPC-3).

        ``handler`` may be sync or async.  Async handlers are scheduled
        as tasks on the event loop; sync handler errors are caught and
        logged so they cannot crash the dispatcher.

        Returns an ``unsubscribe()`` closure.
        """
        lst = self._subscribers.setdefault(topic, [])
        lst.append(handler)

        def _unsubscribe():
            lst2 = self._subscribers.get(topic)
            if lst2 is None:
                return
            try:
                lst2.remove(handler)
            except ValueError:
                pass

        return _unsubscribe

    # ------------------------------------------------------------------
    # Dispatch routing
    # ------------------------------------------------------------------

    async def _handle_request(self, transport, msg):
        req_id = msg.get("id")
        cmd_name = msg.get("cmd")
        args = msg.get("args")
        fn = self._commands.get(cmd_name)
        if fn is None:
            await transport.send({
                "id": req_id,
                "ok": False,
                "error": {
                    "type": "NameError",
                    "message": "no command: " + str(cmd_name),
                },
            })
            return
        try:
            result = await fn(args)
        except BaseException as e:
            # BaseException catches CancelledError too; we want the peer
            # to see a CancelledError reply rather than a hung request.
            await transport.send({
                "id": req_id,
                "ok": False,
                "error": error_payload(e),
            })
            return
        # Encode the result as JSON.  If the handler returned something
        # the JSON encoder can't serialise, turn that into a clean
        # structured error rather than letting the encoder failure
        # propagate.
        try:
            await transport.send({"id": req_id, "ok": True, "result": result})
        except (TypeError, ValueError) as e:
            await transport.send({
                "id": req_id,
                "ok": False,
                "error": {
                    "type": "TypeError",
                    "message": "command {!r} returned non-JSON-serialisable value: {}".format(
                        cmd_name, e
                    ),
                },
            })

    async def _handle_request_wrapper(self, transport, msg):
        """Bookkeeping wrapper so we can cap inbound concurrency (S5)."""
        try:
            await self._handle_request(transport, msg)
        finally:
            self._inbound_in_flight -= 1

    def _resolve_reply(self, msg):
        pending = self._pending_invokes.pop(msg.get("id"), None)
        if pending is None:
            # Reply for unknown id — log and drop.  This happens
            # benignly when an invoke was cancelled before the reply
            # arrived.
            return
        if msg.get("ok"):
            pending.set_result(msg.get("result"))
        else:
            pending.set_exception(build_exception(msg.get("error") or {}))

    def _dispatch_event(self, msg):
        topic = msg.get("event")
        data = msg.get("data")
        handlers = list(self._subscribers.get(topic, ()))
        for handler in handlers:
            try:
                ret = handler(data)
            except BaseException as e:
                sys.stderr.write(
                    "picolet.on handler error for topic {!r}: {}\n".format(topic, e)
                )
                continue
            # If the handler was a coroutine function, its return value
            # is a coroutine — schedule it as a task.
            if ret is not None and hasattr(ret, "send"):
                asyncio.create_task(_run_subscriber(topic, ret))

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def _run_dispatcher(self, transport):
        while True:
            try:
                msg = await transport.recv()
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                sys.stderr.write("picolet: transport recv error: {}\n".format(e))
                return
            if msg is None:
                return
            if not isinstance(msg, dict):
                _log_malformed(msg)
                continue
            if "cmd" in msg and "id" in msg:
                # Inbound concurrency cap (S5).  Reject when the
                # per-dispatcher in-flight count would exceed the cap;
                # send back a structured RuntimeError reply and drop the
                # message rather than spawning the task.
                if self._inbound_in_flight >= MAX_INBOUND_IN_FLIGHT:
                    try:
                        await transport.send({
                            "id": msg.get("id"),
                            "ok": False,
                            "error": {
                                "type": "RuntimeError",
                                "message": "too many concurrent requests",
                            },
                        })
                    except BaseException:
                        pass
                    continue
                self._inbound_in_flight += 1
                asyncio.create_task(
                    self._handle_request_wrapper(transport, msg)
                )
            elif "ok" in msg and "id" in msg:
                self._resolve_reply(msg)
            elif "event" in msg:
                self._dispatch_event(msg)
            else:
                # Anything else is a wire-format violation.  If the
                # message had an id, send back an error reply so the
                # peer isn't stuck forever waiting for one; otherwise
                # just log.
                if "id" in msg:
                    try:
                        await transport.send({
                            "id": msg.get("id"),
                            "ok": False,
                            "error": {
                                "type": "ValueError",
                                "message": "malformed request: missing cmd/event",
                            },
                        })
                    except BaseException:
                        pass
                else:
                    _log_malformed(msg)

    def run(self, transport=None, main=None):
        """Enter the asyncio loop and run the dispatcher (FR-IPC-5).

        ``transport`` defaults to ``StdioTransport()``.  ``main`` is an
        optional coroutine factory or coroutine that runs alongside the
        dispatcher; when ``main`` completes, the dispatcher is cancelled
        and ``run`` returns.

        Returns the value the ``main`` coroutine returned, or None.
        """
        if not _HAVE_ASYNCIO:
            raise RuntimeError("picolet.run requires asyncio")
        if transport is None:
            transport = StdioTransport()
        return asyncio.run(self._run_with_main(transport, main))

    async def _run_with_main(self, transport, main):
        self._active_transport = transport
        self._pending_invokes.clear()
        self._next_invoke_id = 1
        self._inbound_in_flight = 0
        done_event = asyncio.Event()
        main_result_box = [None, None]  # [value, exception]
        dispatcher_done_box = [False]

        async def _dispatcher_wrapper():
            try:
                await self._run_dispatcher(transport)
            finally:
                dispatcher_done_box[0] = True
                done_event.set()

        async def _main_wrapper(coro):
            try:
                main_result_box[0] = await coro
            except BaseException as e:
                main_result_box[1] = e
            finally:
                done_event.set()

        dispatcher_task = asyncio.create_task(_dispatcher_wrapper())
        main_task = None
        if main is not None:
            coro = main() if callable(main) else main
            main_task = asyncio.create_task(_main_wrapper(coro))
        try:
            await done_event.wait()
            if main_task is not None and main_result_box[1] is not None:
                # Main raised — propagate after teardown.
                return None  # raise happens in finally via re-raise
            return main_result_box[0]
        finally:
            # Honour the invoke() docstring contract (C3): when the
            # transport is torn down with replies outstanding, fail the
            # awaiters with a RemoteError("transport closed") rather
            # than letting them surface CancelledError.  set_exception
            # also wakes each pending invoke's Event; we then yield to
            # the loop so the awaiter's event-wait can resume and reach
            # its `raise self._exc` line BEFORE we cancel the task.
            # Cancelling a task whose Event-wait has been signalled but
            # not yet resumed still surfaces CancelledError because
            # asyncio's Task._must_cancel flag is checked at __step
            # entry — the only way to let the awaiter see the set
            # exception is to give the loop one tick to resume it.
            had_pending = bool(self._pending_invokes)
            for pending in list(self._pending_invokes.values()):
                pending.set_exception(RemoteError("transport closed"))
            self._pending_invokes.clear()
            if had_pending and main_task is not None and not main_task.done():
                # Give the awaiter a tick to surface RemoteError.  Cap
                # the drain at a few iterations so a misbehaving handler
                # cannot stall teardown indefinitely; if it hasn't
                # finished by then we fall through to cancellation.
                for _ in range(8):
                    if main_task.done():
                        break
                    try:
                        await asyncio.sleep(0)
                    except BaseException:
                        break
            # Whichever task finished first wins; cancel the other(s).
            for t in (dispatcher_task, main_task):
                if t is None:
                    continue
                try:
                    t.cancel()
                except BaseException:
                    pass
            # Drain cancellations so they don't surface as
            # "Task exception wasn't retrieved" in the loop's exception
            # handler.
            for t in (dispatcher_task, main_task):
                if t is None:
                    continue
                try:
                    await t
                except BaseException:
                    pass
            try:
                await transport.close()
            except BaseException:
                pass
            self._active_transport = None
            if main_task is not None and main_result_box[1] is not None:
                raise main_result_box[1]


# ---------------------------------------------------------------------------
# Module-level helpers and the default singleton
# ---------------------------------------------------------------------------


def _looks_like_coroutine_function(fn):
    # MicroPython lacks ``asyncio.iscoroutinefunction``.  Two-step check:
    #
    # 1. CPython: ``inspect.iscoroutinefunction`` is the canonical test.
    # 2. MicroPython: ``async def`` functions surface as
    #    ``type(fn).__name__ == "generator"`` (or ``"closure"`` for
    #    closures), and a *plain* def function is ``"function"``.  This
    #    is a slightly indirect probe but it's what the runtime
    #    actually exposes — there is no ``__code__`` on bytecode
    #    functions in the unix-port build.
    #
    # We deliberately do NOT fall back to "permissive: trust the user"
    # — gate 5 mandates a TypeError at decoration time for the wrong
    # function shape.
    if not callable(fn):
        return False
    try:
        import inspect
        return bool(inspect.iscoroutinefunction(fn))
    except ImportError:
        pass
    # MicroPython path.
    type_name = type(fn).__name__
    # Plain ``def`` functions appear as "function".  Anything else
    # (generator, closure-wrapped coroutine) we accept; the dispatcher
    # will surface a clear TypeError at call time if the result is not
    # awaitable.
    return type_name != "function"


async def _run_subscriber(topic, coro):
    try:
        await coro
    except BaseException as e:
        sys.stderr.write(
            "picolet.on async handler error for topic {!r}: {}\n".format(topic, e)
        )


def _log_malformed(msg):
    sys.stderr.write(
        "picolet: dropping message with no recognised shape: {}\n".format(msg)
    )


# Default per-process dispatcher.  The public ``command`` / ``invoke``
# / ``emit`` / ``on`` / ``run`` shims delegate here.  Aliases below
# expose the singleton's mutable containers under their historical
# module-level names so tests and embedders that reach into dispatcher
# state continue to compose; rebinding the *scalar* names at module
# level no longer affects the singleton (use ``_default.<attr>``
# instead).
_default = Dispatcher()


def command(fn_or_name):
    return _default.command(fn_or_name)


async def invoke(name, args=None, timeout=None):
    return await _default.invoke(name, args, timeout)


async def emit(topic, data=None):
    return await _default.emit(topic, data)


def on(topic, handler):
    return _default.on(topic, handler)


def run(transport=None, main=None):
    return _default.run(transport, main)


# Convenience hooks for tests and embedders.  These delegate to
# ``_default`` and mirror the original module-globals API where it can
# be preserved cheaply.

def _resolve_reply(msg):
    return _default._resolve_reply(msg)


def _dispatch_event(msg):
    return _default._dispatch_event(msg)


async def _handle_request(transport, msg):
    return await _default._handle_request(transport, msg)


async def _run_dispatcher(transport):
    return await _default._run_dispatcher(transport)


async def _run_with_main(transport, main):
    return await _default._run_with_main(transport, main)
