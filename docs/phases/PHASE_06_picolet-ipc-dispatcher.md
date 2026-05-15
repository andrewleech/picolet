# PH06 — picolet IPC dispatcher (pure-Python + transport abstraction)

## Plan

### Goal (restated)

Ship the message-passing layer that lets a remote peer (JS in a webview,
LVGL Python code, an external test harness over stdio) invoke commands
on a picolet app and receive responses asynchronously. PH06 is the
architectural foundation that PH07–PH10 (webview) and PH11–PH12 (LVGL)
build on top of; without it, neither renderer branch has a wire to
speak.

The phase closes the following requirements from
[docs/v1-spec.md](../v1-spec.md):

| Spec id | Requirement |
|---|---|
| FR-IPC-1 | `@picolet.command async def name(args): ...` registers a command on the Python side. |
| FR-IPC-2 | `await picolet.invoke(name, args)` from a peer returns the command's return value, or raises with the originating exception type and message preserved. |
| FR-IPC-3 | `picolet.emit(topic, data)` from Python pushes an event reachable by `picolet.on(topic, handler)` peers. |
| FR-IPC-4 | Messages are JSON; the wire format is documented in [architecture.md §IPC](../architecture.md#ipc-wire-format). |
| FR-IPC-5 | `asyncio` is the Python-side scheduler. |

PH06 is the first phase of the IPC branch off the critical path
(v1-plan.md §"Critical path") that lands after PH04+PH05 close and
before PH07 begins. It is **Linux-only** for the exit-gate verification;
the Python facade is target-agnostic and will run unchanged on the
Windows runtime once PH07's webview transport lands. The headless
stdio transport works identically on both runtimes today.

### Major design decision: pure-Python dispatcher, no C module in PH06

The v1-plan text for PH06 calls for `overlay/modules/picolet_ipc/` C source
"with JSON parser glue". After examining what the IPC layer actually has
to do, **PH06 ships a pure-Python `picolet` package with a transport
abstraction; no C module is added in this phase.** The C native-callback
glue is deferred to the phase that first needs it — PH07 for
`WebviewTransport` (postMessage from native code → Python) and PH11 for
`LocalTransport` (LVGL in-process queue with Python both sides). This is
a deliberate deviation from the literal v1-plan text and is recorded
here as part of the planning record.

**Rationale.** The work the IPC layer does in PH06 decomposes into four
steps:

1. Read a JSON message from a transport.
2. Parse it into a Python `dict`.
3. Dispatch to a registered `@picolet.command async def` handler (or
   resolve a pending invoke by id, or fan out to `picolet.on` subscribers).
4. Serialise the return value (or exception) back to JSON and write it
   to the transport.

Step 2 and step 4 are handled by MicroPython's built-in `json` C module
(`extmod/modjson.c`, confirmed present and enabled in
`overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` — gate 20 of
PH01 verifies `import json` succeeds). Step 3 is asyncio scheduling, on
which FR-IPC-5 mandates we lean. Step 1 and step 4's transport read/write
is the only thing that varies across renderer variants, and only PH07
and PH11 introduce variations that actually require native callbacks.
For PH06, the transport is stdin/stdout — pure Python `sys.stdin` /
`sys.stdout`. No native code is required.

**A C module in PH06 would be implementing nothing.** It would wrap
`json.loads` and `json.dumps` (already C), call into asyncio (already
C-backed via `_asyncio`), and dispatch into Python handler dicts (a
trivial Python operation). The supposed C-vs-Python boundary buys
nothing in PH06 because there is no native callback to bridge.

**This deviation does not weaken any exit-gate FR.** FR-IPC-{1,2,3,4,5}
are all about *what* the dispatcher does, not *how* it is implemented.
A pure-Python implementation satisfies each requirement directly:

- FR-IPC-1: `@picolet.command` is a Python decorator that registers into
  a dict. Trivial.
- FR-IPC-2: `picolet.invoke` returns an `asyncio.Future`-like awaitable
  resolved when the matching reply arrives.
- FR-IPC-3: `picolet.emit` writes an event message via the transport;
  `picolet.on` registers Python callbacks. Both pure Python.
- FR-IPC-4: JSON parse/serialise is `json.loads` / `json.dumps`. The
  built-in C module from MicroPython does this.
- FR-IPC-5: asyncio is the scheduler by construction — the dispatcher
  is an asyncio coroutine, command handlers are async, the run loop is
  `asyncio.run`.

PH07's `picolet_webview` overlay C module will export a callback function
to native code that, when fired by `postMessage`, pushes a JSON string
into an `asyncio.Queue` consumed by a new `WebviewTransport` class —
*that* is where C code becomes load-bearing because a non-Python thread
(the webview thread) writes to the queue. PH11's LVGL equivalent is
also an in-process queue but stays on the asyncio thread, so the
Python-only `LocalTransport` is sufficient.

**Decision log entry.** The developer logs this deviation as the first
commit in PH06:

```
[PH06] Decision: pure-Python picolet IPC dispatcher; defer C module to PH07/PH11.

The v1-plan text for PH06 calls for overlay/modules/picolet_ipc/ C source
"with JSON parser glue".  In practice the JSON parser glue is already
provided by MicroPython's built-in json module, and the dispatcher work
is dict-lookup + asyncio scheduling — both pure-Python and well within
MicroPython's asyncio scope.  A C module in PH06 would wrap functionality
that is already C-backed and add no expressive power.  The transport
abstraction (Transport protocol with recv/send) makes the renderer-specific
native callbacks (PH07 WebviewTransport, PH11 LocalTransport) drop in
without changing the dispatcher.  No FR-IPC-{1..5} is impacted by this
choice; all five are about behaviour, not implementation language.
```

### Architecture

#### Wire format (FR-IPC-4)

Mirrors [architecture.md §"IPC wire format"](../architecture.md#ipc-wire-format)
verbatim. The dispatcher must parse and emit exactly these shapes; no
extensions, no extra envelope fields. Future revisions land as a new
top-level `"v"` field with an accompanying spec change, not as an ad-hoc
addition by PH06.

```
Request:   {"id": <int>, "cmd": <str>, "args": <obj>}
Reply OK:  {"id": <int>, "ok": true,  "result": <any-json>}
Reply Err: {"id": <int>, "ok": false, "error": {"type": <str>, "message": <str>}}
Event:     {"event": <str>, "data": <any-json>}
```

Discriminator rules (used by the receive path to route a parsed
message):

- Has `"id"` and `"cmd"` → request → call the registered command,
  reply with id.
- Has `"id"` and `"ok"` → reply → resolve the pending invoke future for
  that id.
- Has `"event"` → event → dispatch to `picolet.on` subscribers; no reply.
- Anything else → drop with a structured warning on stderr (and, if the
  message had an `"id"`, send a reply error so the peer is unblocked).

The `id` field is an integer assigned by the **sender** of the request.
For the dispatcher's outgoing invokes the sender is the Python side and
ids are drawn from a monotonically increasing counter starting at 1.
Incoming request ids are echoed verbatim in the reply; the dispatcher
does not validate uniqueness or monotonicity of inbound ids (the peer
is responsible for its own id space).

`args` and `data` are optional but present-by-convention; if absent
they default to `null`. The dispatcher passes `args` to the handler as
a single positional argument exactly as decoded (typically a dict).
The handler decides how to unpack it. **No keyword expansion by
default** — the dispatcher does not `**args` into the handler. The
handler signature is `async def handler(args): ...` for one
positional argument. This matches FR-IPC-1's text
("`@picolet.command async def name(args): ...`") literally.

#### Transport protocol

```python
class Transport:
    async def recv(self):
        """Return the next decoded message dict, or None on EOF/close."""
        raise NotImplementedError

    async def send(self, msg):
        """Serialise msg to JSON and write to the transport."""
        raise NotImplementedError

    async def close(self):
        """Optional: release transport resources.  May be a no-op."""
        return None
```

A class with these three async methods is the only contract a transport
implementation has to satisfy. There is no abstract base class
import-time enforcement (MicroPython lacks `typing.Protocol` runtime
checking); the contract is the duck type. The dispatcher calls only
these three methods.

**Why `recv()` returns a parsed `dict`, not a raw string.** Two reasons.
First, transports may carry binary framing distinct from JSON
serialisation (a future binary-postMessage transport could carry MsgPack
or CBOR while the dispatcher stays JSON-shaped); putting the parse step
inside the transport keeps the dispatcher format-agnostic. Second, an
in-process `LocalTransport` (PH11) skips JSON entirely and passes Python
dicts directly — the JSON round-trip is wasted work on the same thread.
The dispatcher consumes dicts uniformly either way.

`recv()` returning `None` is the "transport closed cleanly" signal
(EOF on stdin, webview tore down, LVGL window closed). The dispatcher
exits its run loop without raising. Errors during recv/send (e.g.
malformed JSON, broken pipe) are raised; the dispatcher catches and
logs them but continues running for malformed-message cases, and
re-raises (terminating the run loop) for broken-pipe cases.

#### `StdioTransport` — the PH06 default

Reads JSON-per-line from `sys.stdin`, writes JSON-per-line to
`sys.stdout`. Each line is exactly one JSON message; the framing is
newline-delimited (newlines inside string values are escaped by
`json.dumps` so they cannot collide). EOF on stdin → `recv()` returns
`None`.

Sketch (final implementation goes in
`packages/picolet-runtime/python/picolet/_transport.py`):

```python
import sys
import json
import asyncio

class StdioTransport:
    def __init__(self, stdin=None, stdout=None):
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._closed = False

    async def recv(self):
        # Yield to the loop, then attempt a single readline.
        # On EOF readline returns '' which we map to None.
        while not self._closed:
            # The naive sys.stdin.readline() is *blocking*.  See "Risks"
            # below for the strategy here — we use a poll-based pattern
            # via select.poll() registered on stdin's fd, which asyncio's
            # _io_queue understands.  StreamReader wrapper to come.
            line = await self._readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue  # blank lines are skipped
            try:
                return json.loads(line)
            except ValueError as e:
                # Malformed JSON: log to stderr and keep listening.
                # The peer's framing is broken; if the broken message
                # carried an id we cannot extract it, so no reply.
                sys.stderr.write(
                    "picolet: malformed JSON on stdin: " + str(e) + "\n"
                )
                continue
        return None

    async def send(self, msg):
        line = json.dumps(msg)
        self._stdout.write(line)
        self._stdout.write("\n")
        # MicroPython's text mode stdout is line-buffered by default on
        # tty but block-buffered when redirected; flush explicitly so
        # the peer sees the reply immediately.
        self._stdout.flush()

    async def close(self):
        self._closed = True
```

The `_readline()` helper is the bit that requires care (Risk 3 below).
PH06's first cut uses asyncio's `_io_queue.queue_read` registered on
`sys.stdin`'s underlying fd, which on the unix port works because
`select.poll()` accepts file objects exposing a `fileno()`. The
`asyncio.stream` module's `open_connection` precedent (line 99-123 of
`extmod/asyncio/stream.py`) is the implementation reference.

#### `MockTransport` — for round-trip tests

```python
class MockTransport:
    def __init__(self, inbox=None):
        self._inbox = list(inbox or [])
        self._outbox = []
        self._closed = False
        self._evt = None  # set when a message arrives

    def feed(self, msg):
        """Test-side: enqueue a message for recv() to return."""
        self._inbox.append(msg)
        if self._evt is not None:
            self._evt.set()

    def drain(self):
        """Test-side: pop the outbox; the dispatcher's sent messages."""
        out = list(self._outbox)
        self._outbox.clear()
        return out

    async def recv(self):
        import asyncio
        while not self._closed:
            if self._inbox:
                return self._inbox.pop(0)
            self._evt = asyncio.Event()
            await self._evt.wait()
            self._evt = None
        return None

    async def send(self, msg):
        self._outbox.append(msg)

    async def close(self):
        self._closed = True
        if self._evt is not None:
            self._evt.set()
```

This lets a single-process test (`asyncio.run(test_round_trip())`) wire
two halves of a dispatcher together via two `MockTransport` instances
and verify both the request-reply and event paths without spawning
processes or touching real stdio.

#### Public Python API surface

The frozen `picolet` package exports:

```python
# packages/picolet-runtime/python/picolet/__init__.py
from ._dispatcher import command, invoke, emit, on, run
from ._transport import Transport, StdioTransport, MockTransport
from ._errors import RemoteError

__all__ = (
    "command", "invoke", "emit", "on", "run",
    "Transport", "StdioTransport", "MockTransport",
    "RemoteError",
)
```

| Symbol | Signature | Behaviour |
|---|---|---|
| `@picolet.command` | `def command(fn_or_name)` | Decorator. Used either as `@picolet.command` (bare) registering under `fn.__name__`, or as `@picolet.command("name")` registering under an explicit name. The wrapped function MUST be `async def` — TypeError raised at decoration time otherwise. FR-IPC-1. |
| `picolet.invoke(name, args=None)` | `async` | Sends a request to the peer; awaits the matching reply; returns the result or raises a remote exception. Returns nothing if the transport is closed before the reply arrives — raises `RemoteError("transport closed")`. FR-IPC-2. |
| `picolet.emit(topic, data=None)` | `async` | Writes an event message to the transport. No reply expected. Returns nothing. FR-IPC-3. |
| `picolet.on(topic, handler)` | sync | Registers `handler(data)` as a callback for inbound events on `topic`. `handler` may be sync or async; async handlers are scheduled as tasks. Returns an `unsubscribe()` closure. FR-IPC-3. |
| `picolet.run(transport=None, main=None)` | sync | Enters the asyncio event loop and runs the dispatcher until the transport returns `None` from `recv()` or `main` completes. `transport` defaults to `StdioTransport()`. `main` is an optional coroutine that runs alongside the dispatcher (for app-startup work). The dispatcher task and the main task race; whichever finishes first stops the loop, the other is cancelled. FR-IPC-5. |
| `picolet.RemoteError` | exception | Raised by `picolet.invoke` when the peer returns an error whose `"type"` is not a builtin class. Carries `.type_name` and `.message` attributes. |

**Why `emit` is async.** Symmetry with `invoke` and consistency with the
fact that `Transport.send` is async. Synchronous `emit` would either
block the event loop on a slow transport or silently queue without
backpressure. The async signature lets the caller `await picolet.emit(...)`
inside a command handler without worrying about the order of writes.
Apps that prefer fire-and-forget can do
`asyncio.create_task(picolet.emit(...))`.

**Why `on` is sync.** Subscription is a dict insert; no await needed.
The handler it registers may be async.

#### Exception preservation convention (FR-IPC-2)

JSON cannot carry Python class identity. The convention:

**Outbound (handler raised → reply error):**

```python
try:
    result = await handler(args)
    reply = {"id": req_id, "ok": True, "result": result}
except BaseException as e:
    reply = {
        "id": req_id,
        "ok": False,
        "error": {
            "type": type(e).__name__,
            "message": str(e),
        },
    }
```

The exception's `repr()` is not transmitted. The `__cause__` /
`__context__` chain is not transmitted. Stack traces are not transmitted
(by design — they leak path information and are not part of FR-IPC-2's
contract). If the application needs richer error metadata it can return
a structured error result with `ok=True` and let the JS or Python peer
unpack the shape itself.

**Inbound (reply error → re-raise locally in invoke):**

```python
def _reraise_remote(error):
    name = error.get("type", "Exception")
    msg = error.get("message", "")
    cls = _RESOLVE_EXCEPTION_TYPE(name)
    raise cls(msg)

_BUILTIN_EXCEPTION_NAMES = {
    "Exception", "RuntimeError", "ValueError", "TypeError",
    "KeyError", "IndexError", "AttributeError", "AssertionError",
    "ArithmeticError", "ZeroDivisionError", "OverflowError",
    "LookupError", "NameError", "NotImplementedError",
    "OSError", "StopIteration", "MemoryError",
    "FileNotFoundError",
}

def _RESOLVE_EXCEPTION_TYPE(name):
    if name in _BUILTIN_EXCEPTION_NAMES:
        return getattr(builtins, name)
    return RemoteError  # carries name + message; not a builtin
```

The dispatcher does **not** try to reach into the peer's package
namespace to instantiate user-defined exception classes; doing so would
let a malicious peer pick an arbitrary callable named in the receiver's
globals. Unknown types fall back to `RemoteError`, which preserves
both `name` and `message` as attributes:

```python
class RemoteError(Exception):
    def __init__(self, message, type_name=None):
        super().__init__(message)
        self.type_name = type_name or "RemoteError"
        self.message = message

    def __str__(self):
        if self.type_name and self.type_name != "RemoteError":
            return "{}: {}".format(self.type_name, self.message)
        return self.message
```

This satisfies FR-IPC-2's "preserved as far as practical" wording: the
type name is preserved as a string, the message is preserved verbatim,
and the receiving code can pattern-match on either `isinstance(e,
ValueError)` (for builtins) or `isinstance(e, RemoteError) and
e.type_name == "MyCustomError"` (for user types).

#### `BaseException` and `CancelledError`

`asyncio.CancelledError` is a `BaseException` subclass in MicroPython's
asyncio (`extmod/asyncio/core.py:18`). The dispatcher's command-execution
wrapper catches `BaseException` (not just `Exception`) to ensure a
cancelled command still produces a reply. Specifically: if a command is
cancelled (e.g. the transport is closing and pending tasks are torn
down), the reply must be `{"ok": false, "error": {"type":
"CancelledError", "message": "..."}}`, not a missing reply that would
leave the peer hanging. The dispatcher does not propagate the
`CancelledError` further once the reply is sent.

#### Dispatcher run-loop shape

```
picolet.run(transport, main=None):
    asyncio.run(_main(transport, main))

async def _main(transport, main):
    dispatcher_task = asyncio.create_task(_run_dispatcher(transport))
    if main is not None:
        main_task = asyncio.create_task(main())
        # Wait for either to finish, cancel the other.
        ...
    else:
        await dispatcher_task

async def _run_dispatcher(transport):
    while True:
        msg = await transport.recv()
        if msg is None:
            return  # EOF / closed
        # Route by shape, schedule a task per request so a slow handler
        # doesn't block the read loop.
        if "cmd" in msg and "id" in msg:
            asyncio.create_task(_handle_request(transport, msg))
        elif "ok" in msg and "id" in msg:
            _resolve_reply(msg)  # sync, just sets a future
        elif "event" in msg:
            _dispatch_event(msg)
        else:
            _log_malformed(msg)
```

The per-request `create_task` is what gives the dispatcher concurrent
in-flight handlers (FR-IPC-5 via FR-IPC-1; the spec doesn't mandate
parallelism but a serial dispatcher would surprise users used to the
JS side awaiting multiple `invoke()`s in parallel).

`_resolve_reply` looks up the pending future for the reply id and
sets its result or exception:

```python
_pending_invokes = {}  # id -> asyncio.Event-like wrapper carrying result

def _resolve_reply(msg):
    pending = _pending_invokes.pop(msg["id"], None)
    if pending is None:
        # Reply for unknown id — log and drop.
        return
    if msg.get("ok"):
        pending.set_result(msg.get("result"))
    else:
        pending.set_exception_from(msg.get("error") or {})
```

MicroPython's asyncio has no `Future` class. The "pending invoke"
abstraction is implemented as a small wrapper around `asyncio.Event`
plus a `result` / `exception` slot:

```python
class _PendingInvoke:
    def __init__(self):
        self._evt = asyncio.Event()
        self._result = None
        self._exc = None

    def set_result(self, r):
        self._result = r
        self._evt.set()

    def set_exception_from(self, err_dict):
        # Build an exception locally per the preservation convention.
        self._exc = _build_exception(err_dict)
        self._evt.set()

    async def wait(self):
        await self._evt.wait()
        if self._exc is not None:
            raise self._exc
        return self._result
```

`picolet.invoke` builds a `_PendingInvoke`, parks it in `_pending_invokes`
under a fresh id, sends the request, and awaits its `wait()`. This is
the standard "no-Future" pattern for MicroPython asyncio.

#### `picolet.on` and event fan-out

```python
_subscribers = {}  # topic -> list[handler]

def on(topic, handler):
    _subscribers.setdefault(topic, []).append(handler)
    def unsubscribe():
        lst = _subscribers.get(topic, [])
        if handler in lst:
            lst.remove(handler)
    return unsubscribe

def _dispatch_event(msg):
    topic = msg["event"]
    data = msg.get("data")
    for handler in list(_subscribers.get(topic, [])):
        if _is_coroutine_function(handler):
            asyncio.create_task(handler(data))
        else:
            try:
                handler(data)
            except Exception as e:
                # Subscriber errors do not affect the dispatcher.
                sys.stderr.write("picolet.on handler error: " + str(e) + "\n")
```

The `_is_coroutine_function` check uses `hasattr(fn, "__name__") and
asyncio.iscoroutinefunction(fn)` if `iscoroutinefunction` is available
in MicroPython's asyncio (it isn't in core MicroPython but the unix
port's frozen asyncio has the `Task` class which exposes `coro`). The
implementation falls back to calling the handler and checking whether
the return value is a coroutine, then scheduling it if so.

#### Re-entrant invoke

A command handler that itself calls `picolet.invoke` (sends a request to
the peer while servicing a request from the peer) must not deadlock.
The architecture supports this because:

- The receive loop is a separate asyncio task from the per-request
  handler task. The handler awaits `_PendingInvoke.wait()`, which
  yields to the loop. The receive loop continues running, picks up the
  incoming reply for the nested invoke, resolves the pending future,
  and the original handler resumes.
- `transport.send` must be safe to call from multiple tasks
  concurrently. `StdioTransport.send` is — it acquires no lock but
  writes a single JSON line + newline + flush in one logical step.
  MicroPython has cooperative scheduling so the write is atomic from
  the loop's perspective. Future transports (e.g. one that writes via
  an async stream) may need explicit lock guarding; this is logged as
  a contract for transport implementers.

This is exercised by gate 8 below.

### Frozen-manifest integration

The `picolet` Python package becomes part of the runtime's frozen-module
set, alongside asyncio. It lives **outside** the micropython submodule
tree (same rationale as `manifests/manifest_cli.py` in PH01) so it can
be edited and versioned in the picolet repo proper.

Canonical path:
```
packages/picolet-runtime/python/picolet/
    __init__.py
    _dispatcher.py
    _transport.py
    _errors.py
```

`manifest_cli.py` adds a single `freeze()` entry:

```python
# Append to the existing manifest_cli.py:
freeze("$(PICOLET_RUNTIME_ROOT)/python", "picolet")
```

The `$(PICOLET_RUNTIME_ROOT)` substitution is the same mechanism the
existing `FROZEN_MANIFEST` resolution uses — exported by
`scripts/build-runtime.sh` and consumed by manifestfile.py's
environment-variable expansion. The second argument to `freeze` is the
sub-path under the library root to freeze; `"picolet"` causes
`packages/picolet-runtime/python/picolet/*.py` to be frozen as the `picolet`
package.

The runtime's gate tests (PH01 / PH04 / PH06) confirm `import picolet`
succeeds.

**Naming clarification.** The framework has two `picolet` Python
namespaces that occupy different runtimes:

| Package | Lives in | Imported by | Runtime |
|---|---|---|---|
| `packages/picolet-cli/picolet/` | the build host (CPython) | the developer's shell | host |
| `packages/picolet-runtime/python/picolet/` | the user-app runtime (MicroPython) | the user's `main.py` | app |

They share the name `picolet` but never coexist in one Python process:
the build tool runs on the host before the app is even built, and the
runtime package runs inside the frozen MicroPython binary. The split
is intentional — the build tool's `picolet` is `picolet.build_cmd`,
`picolet.runtime_resolver`, etc.; the runtime's `picolet` is
`picolet.command`, `picolet.invoke`, etc. There is no namespace collision.

If the developer finds the dual naming confusing, an alternative is to
expose the runtime package as `picolet.rt` or `picoletrt`; the v1 spec
mandates `import picolet` literally in user code examples (FR-IPC-1's
`@picolet.command` wording, FR-WV-5's `window.picolet`) so the runtime
package must be importable as `picolet`. The host-side tool's name is
flexible but already shipped as `picolet` in PH02. Verdict: keep both
named `picolet`; the only impact is on agent context-switching during
implementation.

### Exit gate

| # | Condition | Verification command |
|---|---|---|
| 1 | `scripts/rebuild-integration.sh` exits 0 (no regression). | `./packages/picolet-runtime/scripts/rebuild-integration.sh` → exit 0. |
| 2 | `build-runtime.sh --target linux-x64 --variant cli` exits 0; binary still ≤ 1 MB (NFR-1 unchanged by the `picolet` package being frozen in). | `wc -c packages/picolet-runtime/build/picolet-runtime-linux-x64-cli` → ≤ 1 048 576. The `picolet` package is ~10 KB frozen, well within headroom from PH04's 565 760-byte measurement. |
| 3 | `import picolet` succeeds in the runtime. | `./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import picolet; print("picolet-ok")'` → `picolet-ok`. |
| 4 | All four public names exist and have correct shapes. | `./picolet-runtime-linux-x64-cli -c 'import picolet; print(callable(picolet.command), callable(picolet.invoke), callable(picolet.emit), callable(picolet.on), callable(picolet.run))'` → `True True True True True`. |
| 5 | **FR-IPC-1**: `@picolet.command` registers a command. | Run a fixture that decorates an async function and asserts the function is callable and the registration is present in the dispatcher's command table. See `tests/phase-06/test_command_decorator.py`. |
| 6 | **FR-IPC-2 round trip via stdio**: handler returns a value, peer sees it. | `tests/phase-06/run_stdio_round_trip.sh` builds a hello-cli-style app whose `main.py` registers `greet(name) -> f"hi {name}"` and calls `picolet.run()`. The script pipes one JSON request `{"id":1,"cmd":"greet","args":{"name":"world"}}` into stdin and asserts stdout's first line parses as `{"id":1,"ok":true,"result":"hi world"}`. |
| 7 | **FR-IPC-2 exception preservation**: handler raises ValueError, peer's invoke re-raises ValueError with the same message. | `tests/phase-06/test_exception_preservation.py` — same as gate 6 but the handler does `raise ValueError("oops")`. Stdout shows `{"id":N,"ok":false,"error":{"type":"ValueError","message":"oops"}}`. A second sub-test feeds this reply back into an `invoke()` call in a MockTransport pair and asserts `isinstance(e, ValueError) and str(e) == "oops"`. |
| 8 | **FR-IPC-2 re-entrant invoke** (handler-calls-invoke without deadlock). | `tests/phase-06/test_reentrant_invoke.py` — two `MockTransport`s wire up two dispatchers. Side A's handler `outer(args)` calls `picolet.invoke("inner", ...)` on side B and returns the result. Side B's `inner` handler returns immediately. Driver invokes A's `outer`; expects the wrapped result without hanging (test runs under `asyncio.wait_for(..., 1.0)`). |
| 9 | **FR-IPC-3 emit/on push semantics**. | `tests/phase-06/test_emit_on.py` — paired MockTransports. Side A subscribes to `"progress"` via `picolet.on`. Side B calls `await picolet.emit("progress", {"pct": 42})`. Side A's handler is invoked with `{"pct": 42}`. Test asserts the handler was called within 1 second. |
| 10 | **FR-IPC-4 wire format**: every outgoing message matches the spec exactly. | `tests/phase-06/test_wire_format.py` — drives the dispatcher with `MockTransport`, inspects the captured outbox, and asserts each message has only the keys the spec allows for its shape. No extra keys, no missing required keys. Covers request, ok-reply, error-reply, event. |
| 11 | **FR-IPC-5 asyncio is the scheduler**. | `tests/phase-06/test_asyncio_scheduler.py` — registers a command that does `await asyncio.sleep(0.05)` before returning. Asserts the result comes back and that during the sleep a second command request was processed concurrently (i.e. the handlers are not serialised). |
| 12 | Unknown command name returns a structured error. | `tests/phase-06/test_unknown_command.py` — sends `{"id":1,"cmd":"nope","args":null}`; expects `{"id":1,"ok":false,"error":{"type":"NameError","message":"no command: nope"}}` (or similar — type and message text locked in by the test as the canonical contract). |
| 13 | Malformed JSON on input is handled without killing the dispatcher. | `tests/phase-06/test_malformed_json.sh` — pipes `not-json\n{"id":1,"cmd":"greet","args":null}\n` to the runtime's stdin. Expects exit 0, stderr containing `picolet: malformed JSON on stdin`, stdout containing the reply for the second (well-formed) message. |
| 14 | Transport close on EOF cleanly stops the dispatcher. | `tests/phase-06/test_eof.sh` — pipes empty stdin to the runtime (effectively immediate EOF). Expects exit 0 within 1 second; no error on stderr. |
| 15 | `picolet.run` accepts a `main=` coroutine that races the dispatcher; whichever finishes first stops the loop. | `tests/phase-06/test_run_with_main.py` — calls `picolet.run(MockTransport(), main=async_done_in_50ms)`; expects `run` to return within 1 second. |
| 16 | The `picolet` package's frozen size does not push the runtime over NFR-1. | Same `wc -c` check as gate 2; called out separately because it's the NFR-1 regression test for this phase. |
| 17 | Concurrent in-flight invokes (depth 3) all return correctly. | `tests/phase-06/test_concurrent.py` — three `invoke` coroutines launched with `asyncio.gather`, each hitting a different command, all should return their respective results. Ordering of replies is allowed to differ from request order. |
| 18 | Cancellation: when transport closes mid-handler, the in-flight handler is cancelled and produces no spurious output. | `tests/phase-06/test_cancellation.py` — close the transport while a handler is sleeping; expect the handler task to be cancelled and the run loop to exit cleanly. |
| 19 | Re-running `build-runtime.sh` is idempotent (warm cache). | `time ./build-runtime.sh ...` second run completes in < 5 s with no `mpy-cross` invocations. Same idempotence check as PH01/PH04. |
| 20 | Windows runtime still builds and `import picolet` works there too (sanity check; PH06 is Linux-only for new tests but must not regress Windows). | `./build-runtime.sh --target windows-x64 --variant cli` exits 0; `./picolet-runtime-windows-x64-cli.exe -c 'import picolet; print("picolet-ok")'` → `picolet-ok`. |

Gates 5–11 directly map to FR-IPC-{1,2,3,4,5}; gates 12–18 cover the
operational edges that the FRs imply but don't spell out (malformed
input, EOF, cancellation, concurrency); gates 1–4 and 19–20 protect the
build pipeline.

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-IPC-{1,2,3,4,5} normative text; also FR-WV-5 (the JS-side bridge surface PH07 mirrors) and FR-LV-4 (the LVGL-side call shape PH11 mirrors). |
| `/home/anl/picolet/docs/v1-plan.md` §PH06 | Goal, deliverables, exit gate, model tiers. Note: this phase's "C module" wording is the explicit deviation point discussed under "Major design decision". |
| `/home/anl/picolet/docs/architecture.md` §"IPC wire format" | Request / reply / event JSON shapes, normative for FR-IPC-4. |
| `/home/anl/picolet/CLAUDE.md` | Branch, commit, dev-log policy. |
| `/home/anl/picolet/docs/phases/PHASE_01_picolet-runtime-linux-x64-cli.md` | Frozen-manifest pattern, `PICOLET_RUNTIME_ROOT` resolution. |
| `/home/anl/picolet/docs/phases/PHASE_03_end-to-end-build-cli-linux.md` | Overlay-as-file-level-replacement mechanic (not relevant to PH06 since no overlay C is added, but relevant for understanding the runtime structure the `picolet` package plugs into). |
| `/home/anl/picolet/docs/phases/PHASE_04_picolet-runtime-windows-x64-cli.md` | Windows runtime baseline; gate 20 confirms the `picolet` package freezes into both runtimes. |
| `/home/anl/picolet/docs/phases/PHASE_05_runtime-artifact-distribution.md` | Confirms that PH05 doesn't touch the frozen-manifest mechanism PH06 extends. |
| `/home/anl/picolet/packages/picolet-runtime/manifests/manifest_cli.py` | Current frozen manifest; PH06 appends one `freeze()` line. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/unix/variants/picolet-cli/mpconfigvariant.h` | Confirmed: asyncio is enabled via the frozen manifest (PH01); `json` is on as a built-in C module via `MICROPY_PY_JSON` (default in EXTRA_FEATURES). No variant changes needed for PH06. |
| `/home/anl/picolet/packages/picolet-runtime/overlay/ports/windows/variants/picolet-cli/mpconfigvariant.h` | Same configuration story on Windows; the runtime artifact ships asyncio + json by default for the cli variant. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/modjson.c` | Confirmed: parses string / bytes input, emits JSON. The IPC layer uses `json.loads(line)` and `json.dumps(msg)` directly — no custom parser needed. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/__init__.py` | The exposed asyncio surface (incl. lazy attrs for `Event`, `Lock`, `wait_for`, `gather`). PH06 uses `asyncio.Event`, `asyncio.create_task`, `asyncio.run`, `asyncio.gather`, `asyncio.wait_for`. All present. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/core.py` | `_io_queue.queue_read` / `queue_write` are the building blocks for non-blocking stdin reads. The `IOQueue` uses `select.poll()` on file descriptors; `sys.stdin` exposes `fileno()` so it should work directly. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/stream.py` | The `Stream` class is the precedent for an asyncio-aware readline-from-fd implementation. PH06's `StdioTransport._readline` mirrors lines 24–64 (`read` / `readline` methods). |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/funcs.py` | Confirmed: `wait_for`, `gather`, `wait_for_ms` are available. `asyncio.shield` is **not** present — see Risk 1. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/event.py` | Confirmed: `Event.set` / `wait` / `clear` / `is_set` all present; `ThreadSafeFlag` available — useful for PH07's webview-thread → asyncio-loop callback. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/lock.py` | `Lock` is present; PH06 uses it (or, more likely, avoids it) for the transport-write-serialisation contract. |
| `/home/anl/picolet/packages/picolet-runtime/micropython/extmod/asyncio/manifest.py` | Confirms `task.py` is NOT frozen via micropython-lib (it's provided by the C `_asyncio` module under `core.py`'s try-import fallback). PH06's freeze() call is structurally identical: pure-Python files plus the manifest mechanism. |
| `/home/anl/picolet/packages/picolet-templates/picolet_templates/hello-cli/src/main.py` | Current `main.py` is two lines (`print("Hello from {{name}}")`). PH06 does not modify hello-cli, but the gate-6 test app does follow the same `[app] entry = "src/main.py"` pattern. |
| `/home/anl/picolet/packages/picolet-cli/picolet/build_cmd.py` | The build pipeline freezes `[app] entry` to `main.mpy` and copies it into the romfs. For gate-6's test app, `picolet build` produces a working binary whose `main.py` does `import picolet; @picolet.command async def greet(...); picolet.run()`. No changes to build_cmd.py for PH06. |

### Files to create

| Path | Purpose |
|---|---|
| `packages/picolet-runtime/python/picolet/__init__.py` | Re-export the public API. Tiny — just imports from the private submodules. |
| `packages/picolet-runtime/python/picolet/_dispatcher.py` | Command registry, request/reply routing, pending-invoke tracking, event subscription, `picolet.run`. The bulk of PH06's Python code. |
| `packages/picolet-runtime/python/picolet/_transport.py` | `Transport` interface (duck-typed via docstring), `StdioTransport`, `MockTransport`. |
| `packages/picolet-runtime/python/picolet/_errors.py` | `RemoteError` and the type-resolution table for FR-IPC-2's exception preservation. |
| `packages/picolet-runtime/tests/phase-06/run.sh` | Tester harness driver. Mirrors `tests/phase-04/run.sh` structure (build runtime, then run each gate's named test, print PASS/FAIL summary). |
| `packages/picolet-runtime/tests/phase-06/test_app/main.py` | Test fixture: registers `greet`, `add`, `boom`, `slow`, `nested_inner`, `nested_outer` commands; calls `picolet.run()`. Used by the stdio round-trip tests (gates 6, 7, 11–14). |
| `packages/picolet-runtime/tests/phase-06/test_app/picolet.toml` | The `[app] entry = "main.py"` config so `picolet build` produces a runnable binary from this fixture. |
| `packages/picolet-runtime/tests/phase-06/test_command_decorator.py` | Gate 5. |
| `packages/picolet-runtime/tests/phase-06/run_stdio_round_trip.sh` | Gate 6 driver. |
| `packages/picolet-runtime/tests/phase-06/test_exception_preservation.py` | Gate 7. |
| `packages/picolet-runtime/tests/phase-06/test_reentrant_invoke.py` | Gate 8. |
| `packages/picolet-runtime/tests/phase-06/test_emit_on.py` | Gate 9. |
| `packages/picolet-runtime/tests/phase-06/test_wire_format.py` | Gate 10. |
| `packages/picolet-runtime/tests/phase-06/test_asyncio_scheduler.py` | Gate 11. |
| `packages/picolet-runtime/tests/phase-06/test_unknown_command.py` | Gate 12. |
| `packages/picolet-runtime/tests/phase-06/test_malformed_json.sh` | Gate 13 driver. |
| `packages/picolet-runtime/tests/phase-06/test_eof.sh` | Gate 14 driver. |
| `packages/picolet-runtime/tests/phase-06/test_run_with_main.py` | Gate 15. |
| `packages/picolet-runtime/tests/phase-06/test_concurrent.py` | Gate 17. |
| `packages/picolet-runtime/tests/phase-06/test_cancellation.py` | Gate 18. |

Tests written in Python that exercise the in-process dispatcher (gates
5, 7–12, 15, 17, 18) are intended to run against **CPython** with the
`picolet` package on `sys.path`, not inside the MicroPython runtime —
they exercise the dispatcher logic, not the runtime integration. The
gate-6 round-trip and gate-3 import gates exercise the actual frozen
package in the built binary; that's what proves the integration is
real. (Same split PH05 used: unit tests in CPython, integration tests
in the runtime binary.)

If the runtime-side tests need to run under MicroPython (e.g. to catch
MicroPython-specific asyncio behaviour the CPython tests would miss),
the developer can add a small `tests/phase-06/run_under_runtime.sh`
that points the runtime binary at each `.py` test file via the
`-c "import test_module; test_module.run()"` pattern. This is logged
as a contingency; the primary plan is CPython-on-the-host for unit
tests.

### Files to modify

| Path | Change |
|---|---|
| `packages/picolet-runtime/manifests/manifest_cli.py` | Append one `freeze("$(PICOLET_RUNTIME_ROOT)/python", "picolet")` call so the `picolet` package is included in the frozen manifest. Update the header comment to note that picolet is now frozen. |
| `packages/picolet-runtime/scripts/build-runtime.sh` | None expected. `PICOLET_RUNTIME_ROOT` is already exported (PH01 step 1). Listed here for completeness — if the manifest substitution mechanic fails for `$(PICOLET_RUNTIME_ROOT)/python`, the developer adds an explicit `export PICOLET_PYTHON_DIR=$PICOLET_RUNTIME_ROOT/python` line. |
| `packages/picolet-runtime/tests/phase-04/run.sh` | None expected — PH04's tests should still pass unchanged after the frozen-manifest extension. Listed here as the regression surface. |

### Sequence the developer follows

All from `/home/anl/picolet` on `dev`.

**1. Log the design decision.**
```
git commit --allow-empty -s -m "[PH06] Decision: pure-Python picolet IPC dispatcher; defer C module to PH07/PH11" -m "..."
```
Body covers the rationale spelled out under "Major design decision".

**2. Lay down the `picolet` package skeleton.**
```
mkdir -p packages/picolet-runtime/python/picolet
```
Write `__init__.py`, `_dispatcher.py`, `_transport.py`, `_errors.py`
following the API sketches above. Start with the bare minimum that
satisfies gate 5 (`@picolet.command` registration), then layer on the
receive loop, then `invoke`, then `emit`/`on`.

**3. Extend the frozen manifest.**
Append the `freeze()` line to `manifests/manifest_cli.py`.

**4. Build the runtime and confirm gate 3 (`import picolet`).**
```
./packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant cli
./packages/picolet-runtime/build/picolet-runtime-linux-x64-cli -c 'import picolet; print("ok")'
```

**5. Walk through the gates one at a time.** Implement enough of the
dispatcher to satisfy each gate, commit, move on. Suggested commit
breakdown:
- `[PH06] Add picolet package skeleton; freeze in manifest_cli.` (gate 3, 4)
- `[PH06] Implement @picolet.command decorator + dispatcher receive loop.` (gates 5, 6, 12)
- `[PH06] Add picolet.invoke and pending-reply tracking.` (gates 7, 8, 17)
- `[PH06] Add picolet.emit / picolet.on event channel.` (gates 9, 10)
- `[PH06] Add cancellation + EOF handling.` (gates 13, 14, 18)
- `[PH06] Add picolet.run main-coroutine race.` (gate 15)
- `[PH06] Verify NFR-1 still holds; confirm Windows build.` (gates 2, 16, 19, 20)

**6. Land the test harness.**
```
git add packages/picolet-runtime/tests/phase-06/
git commit -s -m "[PH06] Add phase-06 test harness covering gates 1-20"
```

**7. Run the full gate suite.**
```
bash packages/picolet-runtime/tests/phase-06/run.sh
```
Expect all gates green.

**8. Confirm Windows non-regression.**
```
./packages/picolet-runtime/scripts/build-runtime.sh --target windows-x64 --variant cli
./packages/picolet-runtime/build/picolet-runtime-windows-x64-cli.exe -c 'import picolet; print("ok")'
```

### Foreseeable risks

**Risk 1: MicroPython's asyncio is a subset of CPython's.**

The dispatcher uses these asyncio names; the table below records
availability in MicroPython's frozen `extmod/asyncio` (confirmed during
planning by reading `__init__.py`, `core.py`, `event.py`, `funcs.py`,
`lock.py`, `stream.py`):

| Name | MicroPython asyncio | Substitute if missing |
|---|---|---|
| `asyncio.run` | yes (`core.py` line 247) | n/a |
| `asyncio.create_task` | yes (`core.py` line 143) | n/a |
| `asyncio.gather` | yes (`funcs.py` line 65) | n/a |
| `asyncio.wait_for` | yes (`funcs.py` line 24) | n/a |
| `asyncio.Event` | yes (`event.py` line 8) | n/a |
| `asyncio.Lock` | yes (`lock.py`) | n/a |
| `asyncio.iscoroutinefunction` | **no** | duck-type test: call once, check return type for `__next__` attr, schedule if found. |
| `asyncio.shield` | **no** | use a top-level try/except and re-raise CancelledError manually; the only use case for `shield` (protecting transport.send from cancellation) can be avoided by structuring the dispatcher so cancellation never targets the send. |
| `asyncio.Future` | **no** | the `_PendingInvoke` wrapper around `asyncio.Event` is the substitute. |
| `asyncio.TimeoutError` | yes (`core.py` line 22) | n/a |
| `asyncio.CancelledError` | yes (`core.py` line 18) | n/a |

Mitigation: every dispatcher pattern documented above sticks to the
"yes" rows. If a future PR wants to use `shield` or `Future`, it
either lands a polyfill in `picolet._compat` or is rejected.

**Risk 2: `main.py` becomes the user app's entry point — how does the
dispatcher coordinate with arbitrary user code?**

The user's `main.py` calls `picolet.run()` (or `picolet.run(main=...)`).
The `main=` argument is a coroutine that runs alongside the dispatcher
for app-startup work. Two patterns are supported:

```python
# Pattern A — pure server: register commands, await the dispatcher.
@picolet.command
async def greet(args): return f"hi {args['name']}"

picolet.run()  # blocks forever; exits on transport EOF
```

```python
# Pattern B — server with init: do setup work, then handle requests.
async def boot():
    await load_config()
    print("ready")

@picolet.command
async def greet(args): return f"hi {args['name']}"

picolet.run(main=boot)  # boot() runs; when it returns, the dispatcher
                      # keeps running until transport EOF
```

```python
# Pattern C — finite app: run a coroutine to completion, then exit.
async def app():
    await picolet.emit("started", {"ts": ticks_ms()})
    await asyncio.sleep(10)
    await picolet.emit("done", {})

picolet.run(main=app)  # when app() returns, picolet.run() returns.
```

The dispatcher runs as a task in parallel with `main`. When `main`
completes, the dispatcher is cancelled and `picolet.run` returns. When
the dispatcher's `transport.recv()` returns `None`, `main` (if any) is
cancelled and `picolet.run` returns. This is the standard "race two
tasks, whichever finishes first wins" idiom; gate 15 tests it.

The user's main.py is NOT required to import picolet — if it doesn't,
the runtime auto-run path still executes it as before, and the
dispatcher simply doesn't start. This preserves backwards compatibility
with PH01-style cli apps.

**Risk 3: `StdioTransport` blocking the asyncio event loop.**

MicroPython's `sys.stdin.readline()` is a blocking call. If we naively
do `line = sys.stdin.readline()` inside the dispatcher coroutine, the
event loop stops for the duration of the read — which means concurrent
in-flight handlers cannot make progress while waiting for the next
inbound message, defeating FR-IPC-5's concurrent-asyncio promise.

The fix is to register `sys.stdin` with asyncio's `_io_queue` via
`select.poll()`. The `extmod/asyncio/stream.py` `Stream` class
(particularly its `read` / `readline` methods, lines 24–64) is the
working precedent — it does exactly this for socket streams. The
mechanism:

```python
import select, sys, asyncio
from asyncio.core import _io_queue, cur_task

class StdinStream:
    def __init__(self):
        self._fd = sys.stdin
        self._buf = b""

    def readline(self):
        # asyncio-yielding readline.  Yields to the loop via _io_queue
        # until the fd has data, then drains until \n.
        while True:
            i = self._buf.find(b"\n")
            if i >= 0:
                line = self._buf[:i+1]
                self._buf = self._buf[i+1:]
                return line
            yield _io_queue.queue_read(self._fd)
            chunk = self._fd.read(...)  # non-blocking
            if not chunk:
                # EOF
                if self._buf:
                    line = self._buf; self._buf = b""
                    return line
                return None
            self._buf += chunk
```

There is a subtlety: `sys.stdin` in MicroPython's unix port may be in
text mode, in which case `read()` returns `str` not `bytes`, and the
fd may not behave correctly with `select.poll`. The developer's first
implementation should test whether `sys.stdin.buffer.fileno()` works
(it does on CPython; the MicroPython unix port exposes `sys.stdin` as
a stream-like object that `select.poll` can register, per the `Stream`
class precedent).

If this turns out to be a brick wall, the fallback is to run the read
loop in a separate thread (MicroPython's unix port supports threading)
that drops parsed messages onto an `asyncio.Event`-gated queue. That
adds GIL-management complexity but is well-bounded; not the primary
plan.

**Risk 4: Memory — pending invokes accumulate by id.**

Each outbound `picolet.invoke` registers a `_PendingInvoke` in a dict
keyed by id, and the entry is removed when the reply arrives or the
invoke times out. If the peer never replies (broken peer, infinite
loop, etc.), the entry stays forever. With unbounded invokes this
leaks memory.

Mitigation: `picolet.invoke` takes an optional `timeout=` kwarg
defaulting to `None` (no timeout). When `timeout` is set, the invoke
is wrapped in `asyncio.wait_for(pending.wait(), timeout)`. On timeout
the entry is removed and `asyncio.TimeoutError` is raised. The PH06
default of `None` matches the spec text (FR-IPC-2 doesn't mandate a
timeout) but the option is there for apps that need it.

A second mitigation: cap the size of `_pending_invokes` at e.g. 1024.
When the cap is hit, new `invoke()` calls raise `RuntimeError("too
many in-flight invokes")` immediately. This is a defence against a
malicious peer or a runaway loop. Default cap: 1024 (logged in the
phase notes; tunable via a module attribute `picolet._dispatcher.MAX_IN_FLIGHT`).

**Risk 5: Re-entrancy and the transport-send lock.**

`transport.send` is called from many tasks: the per-request handler
that just produced a reply, the `picolet.emit` caller, the
`picolet.invoke` caller. If two of them interleave their writes the
on-the-wire framing breaks (two half-lines mixed).

Mitigation for `StdioTransport`: the implementation writes the full
JSON line + newline + flush in one logical step. MicroPython's
cooperative scheduler will not interrupt a sync sequence between
`await` points; `send`'s body has no `await` (the writes are sync,
buffered by stdout's text-mode buffer), so the framing is atomic.

For future transports where send IS async (e.g. waiting on a write
buffer to drain), the transport implementation MUST use an
`asyncio.Lock` internally to serialise. This is a contract on
transport authors, documented in `_transport.py`'s `Transport`
docstring.

**Risk 6: JSON-incompatible Python types in return values.**

A handler that returns a `bytes`, a `set`, a `tuple` (questionable —
some JSON libs encode as list, MicroPython's might error), or a
custom class will crash the JSON encoder. The error today would be a
nasty `TypeError` propagated to the peer as a generic error.

Mitigation: wrap the `json.dumps(result)` in a try/except; on failure
convert the exception to a structured error reply
`{"type": "TypeError", "message": "command 'X' returned non-JSON
serialisable value: <repr>"}`. The peer sees a clean error instead of
a corrupted transport.

The spec does not require picolet to canonicalise return values to JSON
shapes; it requires the *wire format* to be JSON. The contract on
handler authors is "return JSON-serialisable values". The mitigation
above turns a hard-to-debug crash into a clear error message.

**Risk 7: Frozen-manifest path resolution for `python/picolet`.**

The `freeze()` call uses `$(PICOLET_RUNTIME_ROOT)/python` as the library
root. If the manifestfile.py substitution doesn't expand
`$(PICOLET_RUNTIME_ROOT)` (because it's a custom env var, not the
standard `$(MPY_DIR)` / `$(MPY_LIB_DIR)`), the build fails with a
file-not-found.

Verify behaviour: the existing `manifest_cli.py` line 17 already uses
`$(MPY_DIR)/extmod/asyncio` and that works in PH01. The
`$(PICOLET_RUNTIME_ROOT)` substitution requires the build script to
export it before invoking `make`. PH01 already does this (gate 19 of
PH01's plan, confirmed in `build-runtime.sh`).

If the substitution fails despite this, the fallback is to symlink
`overlay/manifests/picolet -> ../../python/picolet` in the overlay tree
and use the `$(MPY_DIR)/manifests/picolet` path which is guaranteed to
work. Logged as contingency.

**Risk 8: `asyncio.iscoroutinefunction` absent in MicroPython.**

CPython has `asyncio.iscoroutinefunction(fn)`. MicroPython does not
(confirmed by reading `extmod/asyncio/__init__.py` — only the lazy
attrs in `_attrs` are exported, no `iscoroutinefunction`).

Mitigation: use the duck-type pattern
```python
def _is_coro_function(fn):
    # MicroPython's coroutine functions, when called, return objects
    # that have a 'send' method (they're generators/coroutines).
    # Call once on a no-arg fixture is impractical; instead check the
    # raw bytecode/qualname pattern or just call and check the result.
    # Simpler: register all handlers as coroutine functions by
    # convention (FR-IPC-1 mandates 'async def' anyway) and skip the
    # check entirely.
    return True  # picolet.command enforces async at decoration time.
```

Since `@picolet.command` is documented to require `async def` (the
decorator can verify by checking that the function's bytecode is
generator-style at decoration time), the dispatcher can trust that
command handlers are always coroutines. `picolet.on` handlers may be
either; the dispatcher checks the call's return value at runtime: if
it has `send` / `__next__`, schedule it; otherwise treat the call as
already-completed.

### Out of scope for PH06

- The webview transport (PH07): native `postMessage` callback bridged
  into the asyncio event loop. PH07 adds `overlay/modules/picolet_webview/`
  C source and a `WebviewTransport` Python class that consumes the
  callback's queue.
- The LVGL transport (PH11): in-process `LocalTransport` that bypasses
  JSON serialisation (since both ends are Python on the same thread).
- TypeScript type generation from the registered command table (post-v1,
  per architecture.md D3's "Consequence").
- JS-side bridge implementation (PH08).
- Real C native module bridging (PH07 onwards).
- Performance optimisation of the dispatcher (the cli/headless case is
  not performance-sensitive; the renderer cases will benchmark in their
  own phases).
- Hot-reloading the command table while the dispatcher is running
  (post-v1; the spec doesn't require it).
- Multi-transport routing (one dispatcher, multiple transports). v1's
  pattern is one transport per process.

### Spec traceability

| Spec id | Where closed in PH06 |
|---|---|
| FR-IPC-1 | `picolet.command` decorator in `_dispatcher.py`; registers into a module-level dict. Gate 5 verifies registration. Gate 6 verifies a registered command is callable via the wire. |
| FR-IPC-2 | `picolet.invoke` in `_dispatcher.py` returns the result on `ok=true` reply, raises the resolved exception type on `ok=false`. Exception preservation per the convention in `_errors.py`. Gates 6, 7, 8, 17 cover the four facets (return value, exception, re-entrant, concurrent). |
| FR-IPC-3 | `picolet.emit` writes an event to the transport; `picolet.on` registers handlers in a topic-keyed dict and the dispatcher fans inbound events out to all subscribers. Gate 9. |
| FR-IPC-4 | JSON shapes match [architecture.md](../architecture.md#ipc-wire-format) exactly. The dispatcher uses `json.loads` / `json.dumps`, no custom encoder. Gate 10 inspects every outgoing message against the spec's shape. |
| FR-IPC-5 | `picolet.run` uses `asyncio.run` to drive the event loop; the dispatcher itself is a coroutine; per-request handlers are tasks. Gate 11 verifies concurrent execution. |

## Notes for downstream phases

PH07's `WebviewTransport` should subclass nothing — it just exposes
`recv()`, `send()`, `close()` matching the duck-type contract. The C
side (the new `picolet_webview` overlay module) exports a callback that
the runtime registers on the webview's `postMessage` channel; the
callback drops the message string onto an `asyncio.Queue` (or, since
MicroPython lacks `asyncio.Queue`, an `asyncio.Event` + a Python list)
that `WebviewTransport.recv()` consumes.

PH11's `LocalTransport` is a pair-wired transport: two `LocalTransport`
instances share a queue in each direction. Used by LVGL's in-process
pattern where the "JS side" doesn't exist and the renderer calls Python
directly via a shim that uses the same transport contract for symmetry
with the webview variant.

Both follow this PH06 contract without changes.
