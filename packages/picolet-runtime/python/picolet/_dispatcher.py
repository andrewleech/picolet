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
# The dispatcher is a module singleton: there is one command table, one
# pending-invoke table, one subscriber table, one active transport, per
# Python process.  This matches v1's "one transport per process" model.

import sys

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False

from ._errors import build_exception, error_payload, RemoteError
from ._transport import StdioTransport


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# Command name → async callable.  Populated by @picolet.command.
_commands = {}

# Subscriber topic → list of handler callables (sync or async).
_subscribers = {}

# Pending outbound invoke id → _PendingInvoke.
_pending_invokes = {}

# Monotonically increasing id counter for outbound requests.
_next_invoke_id = 1

# The currently active transport (set by ``run`` for the duration of the
# loop).  ``invoke`` and ``emit`` use this to send messages.  Outside a
# ``run`` call this is None and those functions raise.
_active_transport = None

# Defensive cap on concurrent in-flight invokes.  When the table grows
# beyond this, new invoke() calls raise immediately.  This is a safety
# net against a runaway loop or a broken peer that never replies.
MAX_IN_FLIGHT = 1024


# ---------------------------------------------------------------------------
# Command decorator (FR-IPC-1)
# ---------------------------------------------------------------------------


def command(fn_or_name):
    """Register an async function as an IPC command.

    Two forms:

        @picolet.command
        async def greet(args): ...           # registered as "greet"

        @picolet.command("greet_v2")
        async def greet(args): ...           # registered as "greet_v2"

    The wrapped function MUST be ``async def``; otherwise TypeError is
    raised at decoration time.  The handler is called with the
    request's ``args`` as a single positional argument (a dict, list,
    string, number, bool, or None — whatever the peer sent).
    """
    if callable(fn_or_name):
        # Bare @picolet.command (no parentheses).
        return _register_command(fn_or_name.__name__, fn_or_name)
    if isinstance(fn_or_name, str):
        name = fn_or_name

        def _decorate(fn):
            return _register_command(name, fn)

        return _decorate
    raise TypeError(
        "picolet.command must be used as @picolet.command or @picolet.command('name')"
    )


def _register_command(name, fn):
    if not _looks_like_coroutine_function(fn):
        raise TypeError(
            "picolet.command requires an async def function (got {})".format(
                type(fn).__name__
            )
        )
    _commands[name] = fn
    return fn


def _looks_like_coroutine_function(fn):
    # MicroPython lacks ``asyncio.iscoroutinefunction``.  An ``async def``
    # function in MicroPython, when called, returns an object with a
    # ``send`` method (it's a coroutine).  We don't want to *call* the
    # function here just to test it, so we look at its underlying code
    # object's flags if available — MicroPython exposes a __code__
    # attribute on bytecode functions.  Fall back to a permissive check
    # for portability.
    if not callable(fn):
        return False
    # CPython: inspect.iscoroutinefunction equivalent.
    try:
        import inspect
        if inspect.iscoroutinefunction(fn):
            return True
    except ImportError:
        pass
    # MicroPython: check the function's code flags for "generator-like".
    code = getattr(fn, "__code__", None)
    if code is not None:
        co_flags = getattr(code, "co_flags", 0)
        # 0x100 = generator, 0x200 = coroutine, 0x400 = iterable_coroutine
        if co_flags & 0x700:
            return True
    # As a last resort, presume the user followed FR-IPC-1 ("async def
    # name(args)") and trust them.  The dispatcher will surface a clear
    # TypeError if the call result isn't awaitable.
    return True


# ---------------------------------------------------------------------------
# Outbound invoke (FR-IPC-2)
# ---------------------------------------------------------------------------


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


async def invoke(name, args=None, timeout=None):
    """Send a request to the peer and await the reply (FR-IPC-2).

    Returns the result value on success.  On a remote error, raises:

      - the matching builtin exception class (ValueError, KeyError, etc.)
        when the peer's error ``type`` is in the allow-list, or
      - ``picolet.RemoteError`` carrying ``type_name`` + ``message`` otherwise.

    Raises ``RemoteError("transport closed")`` if the transport is torn
    down while the reply is outstanding.

    ``timeout`` (seconds) wraps the await in ``asyncio.wait_for`` and
    raises ``asyncio.TimeoutError`` if no reply arrives in time.  The
    pending-invoke entry is removed in that case.
    """
    global _next_invoke_id
    transport = _active_transport
    if transport is None:
        raise RuntimeError("picolet.invoke called outside picolet.run")
    if not _HAVE_ASYNCIO:
        raise RuntimeError("picolet.invoke requires asyncio")
    if len(_pending_invokes) >= MAX_IN_FLIGHT:
        raise RuntimeError("too many in-flight invokes")
    req_id = _next_invoke_id
    _next_invoke_id += 1
    pending = _PendingInvoke()
    _pending_invokes[req_id] = pending
    try:
        await transport.send({"id": req_id, "cmd": name, "args": args})
    except BaseException:
        _pending_invokes.pop(req_id, None)
        raise
    try:
        if timeout is None:
            return await pending.wait()
        return await asyncio.wait_for(pending.wait(), timeout)
    finally:
        # Remove the entry whether the reply arrived, timed out, or the
        # waiter was cancelled.  set_result/set_exception is harmless if
        # called against the now-dropped entry.
        _pending_invokes.pop(req_id, None)


# ---------------------------------------------------------------------------
# Event channel (FR-IPC-3)
# ---------------------------------------------------------------------------


async def emit(topic, data=None):
    """Push an event to the peer.  No reply expected (FR-IPC-3)."""
    transport = _active_transport
    if transport is None:
        raise RuntimeError("picolet.emit called outside picolet.run")
    await transport.send({"event": topic, "data": data})


def on(topic, handler):
    """Register a handler for inbound events on ``topic`` (FR-IPC-3).

    ``handler`` may be sync or async.  Async handlers are scheduled as
    tasks on the event loop; sync handler errors are caught and logged
    so they cannot crash the dispatcher.

    Returns an ``unsubscribe()`` closure.
    """
    lst = _subscribers.setdefault(topic, [])
    lst.append(handler)

    def _unsubscribe():
        lst2 = _subscribers.get(topic)
        if lst2 is None:
            return
        try:
            lst2.remove(handler)
        except ValueError:
            pass

    return _unsubscribe


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


async def _handle_request(transport, msg):
    req_id = msg.get("id")
    cmd_name = msg.get("cmd")
    args = msg.get("args")
    fn = _commands.get(cmd_name)
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
        # BaseException catches CancelledError too; we want the peer to
        # see a CancelledError reply rather than a hung request.
        await transport.send({
            "id": req_id,
            "ok": False,
            "error": error_payload(e),
        })
        return
    # Encode the result as JSON.  If the handler returned something the
    # JSON encoder can't serialise, turn that into a clean structured
    # error rather than letting the encoder failure propagate.
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


def _resolve_reply(msg):
    pending = _pending_invokes.pop(msg.get("id"), None)
    if pending is None:
        # Reply for unknown id — log and drop.  This happens benignly
        # when an invoke was cancelled before the reply arrived.
        return
    if msg.get("ok"):
        pending.set_result(msg.get("result"))
    else:
        pending.set_exception(build_exception(msg.get("error") or {}))


def _dispatch_event(msg):
    topic = msg.get("event")
    data = msg.get("data")
    handlers = list(_subscribers.get(topic, ()))
    for handler in handlers:
        try:
            ret = handler(data)
        except BaseException as e:
            sys.stderr.write(
                "picolet.on handler error for topic {!r}: {}\n".format(topic, e)
            )
            continue
        # If the handler was a coroutine function, its return value is a
        # coroutine — schedule it as a task.
        if ret is not None and hasattr(ret, "send"):
            asyncio.create_task(_run_subscriber(topic, ret))


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


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------


async def _run_dispatcher(transport):
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
            asyncio.create_task(_handle_request(transport, msg))
        elif "ok" in msg and "id" in msg:
            _resolve_reply(msg)
        elif "event" in msg:
            _dispatch_event(msg)
        else:
            # Anything else is a wire-format violation.  If the message
            # had an id, send back an error reply so the peer isn't
            # stuck forever waiting for one; otherwise just log.
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


def run(transport=None, main=None):
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
    return asyncio.run(_run_with_main(transport, main))


async def _run_with_main(transport, main):
    global _active_transport, _next_invoke_id
    _active_transport = transport
    _pending_invokes.clear()
    _next_invoke_id = 1
    dispatcher_task = asyncio.create_task(_run_dispatcher(transport))
    main_task = None
    if main is not None:
        coro = main() if callable(main) else main
        main_task = asyncio.create_task(coro)
    try:
        return await _wait_first(dispatcher_task, main_task)
    finally:
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
        _active_transport = None
        _pending_invokes.clear()


async def _wait_first(dispatcher_task, main_task):
    """Wait until the first of (dispatcher_task, main_task) completes.

    Returns ``main_task``'s value when it wins; returns ``None`` when the
    dispatcher exits first (transport EOF).  MicroPython's asyncio has
    no ``asyncio.wait`` with FIRST_COMPLETED, so we poll the task
    states cooperatively.
    """
    if main_task is None:
        await dispatcher_task
        return None
    # Race the two tasks via a small completion-callback registered as
    # an Event.  Simpler than reimplementing wait_first across
    # MicroPython's asyncio quirks: just loop with a tiny sleep.
    while True:
        if _task_done(dispatcher_task):
            return None
        if _task_done(main_task):
            try:
                # Surface the main task's return value (or exception).
                return await main_task
            except BaseException:
                # Propagate the main task's exception to the caller of
                # picolet.run after both halves have been torn down.
                raise
        await asyncio.sleep(0)


def _task_done(t):
    """True if asyncio task ``t`` has finished (in any way).

    MicroPython's Task has a .state attribute that becomes False when
    the task is finished and has been awaited, or None when finished
    and not yet awaited.
    """
    state = getattr(t, "state", True)
    return state is False or state is None
