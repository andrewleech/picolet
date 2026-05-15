# picolet._transport — message transports for the IPC dispatcher.
#
# A transport is any object exposing three async methods:
#
#   async recv()  -> dict | None     (None signals EOF / closed)
#   async send(msg) -> None
#   async close() -> None
#
# There is no abstract base class — MicroPython has no runtime Protocol
# enforcement.  The contract is the duck type.  PH06 ships two concrete
# transports:
#
#   - StdioTransport: JSON-per-line over sys.stdin / sys.stdout.  The
#     default for headless cli apps.  Integrates with asyncio's IO queue
#     on Linux so the event loop stays responsive while waiting for
#     input.  Falls back to blocking readline on Windows (the unix-port
#     ``select.poll`` is not available there; PH06 is Linux-only for
#     exit-gate verification anyway).
#
#   - MockTransport: in-process transport for unit tests.  No JSON
#     round-trip, no real IO — just inbox/outbox lists.
#
# PH11 adds InProcessTransport: a paired in-process transport for the
# lvgl variant's FR-LV-4 case (Python-to-Python IPC via the same PH06
# dispatcher).  Unlike MockTransport, InProcessTransport JSON-encodes
# its messages so the wire format is byte-identical to StdioTransport
# and WebviewTransport — PH13 SBOM tooling and `picolet dev` log shapes
# don't need a special case for lvgl.

import sys
import json

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False

# Optional import — only available on the linux/unix port.  Windows has
# select.select but not select.poll, and select.poll is what asyncio's
# IOQueue uses, so detecting POLLIN here is also the right gate for
# integrating with the asyncio scheduler.
try:
    import select as _select
    _HAVE_POLL = hasattr(_select, "poll")
except ImportError:
    _select = None
    _HAVE_POLL = False

# The naked-yield readline pattern from extmod/asyncio/stream.py only
# parses on MicroPython — CPython rejects ``return value`` inside an
# async generator at compile time.  On CPython we always take the
# blocking-readline path; the production target is MicroPython so this
# is only a unit-test ergonomics concern.
_IS_MICROPYTHON = sys.implementation.name == "micropython"


# ---------------------------------------------------------------------------
# Transport (documentation-only base)
# ---------------------------------------------------------------------------


class Transport:
    """Documentation-only base class for transports.

    Implementations need only provide ``recv``, ``send``, and ``close``
    as async methods.  Subclassing is not required.

    Concurrency contract:
        - ``send`` may be called from multiple tasks concurrently.  The
          implementation MUST ensure that on-the-wire framing is atomic
          (one message is fully emitted before another starts).  For
          ``StdioTransport`` this is true by construction because
          ``send`` performs no ``await`` and MicroPython's scheduler is
          cooperative; for transports that do await mid-write the
          implementation must hold an ``asyncio.Lock``.
    """

    async def recv(self):
        raise NotImplementedError

    async def send(self, msg):
        raise NotImplementedError

    async def close(self):
        return None


# ---------------------------------------------------------------------------
# StdioTransport
# ---------------------------------------------------------------------------


class StdioTransport:
    """JSON-per-line transport over ``sys.stdin`` / ``sys.stdout``.

    Framing is newline-delimited JSON: one message per line.  Newlines
    inside JSON strings are escaped by ``json.dumps`` so they cannot
    collide with the framing.

    EOF on stdin → ``recv()`` returns ``None``; the dispatcher's run
    loop exits cleanly.

    Malformed JSON on input is logged to stderr and skipped; the
    transport keeps reading.  The peer's id is lost in this case, so no
    reply is sent.
    """

    def __init__(self, stdin=None, stdout=None):
        # The CPython unit tests inject text-mode file-likes here; the
        # frozen runtime uses sys.stdin / sys.stdout by default.  On
        # MicroPython unix port sys.stdin.buffer is the underlying
        # FileIO that select.poll() accepts.
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        # Shared state for the MicroPython recv generator: ``[buf,
        # closed_flag]``.  The generator mutates these in place across
        # yields.
        self._state = [b"", False]
        # Pre-resolved fd-like object to poll on; None if we are
        # falling back to a blocking readline.
        self._poll_target = None
        self._init_poll_target()

    def _init_poll_target(self):
        """Decide whether the asyncio IOQueue can be used for this stdin.

        On the linux/unix port we get the underlying binary FileIO via
        ``sys.stdin.buffer``; ``select.poll`` can register it and
        ``asyncio._io_queue.queue_read`` can schedule a wake-up.

        On the windows port ``select.poll`` raises NotImplementedError
        on file fds, so we fall back to blocking ``readline`` — PH06's
        StdioTransport on Windows is not concurrency-friendly, but
        ``import picolet`` works and that is all PH06 requires on
        Windows.  PH10/PH12 will revisit the Windows transport story
        when the webview/LVGL variants land.
        """
        if not _HAVE_POLL or not _HAVE_ASYNCIO:
            return
        target = getattr(self._stdin, "buffer", None) or self._stdin
        # Confirm select.poll accepts this object before committing to it.
        try:
            p = _select.poll()
            p.register(target, _select.POLLIN)
            p.unregister(target)
        except (OSError, NotImplementedError, TypeError):
            return
        self._poll_target = target

    async def recv(self):
        if self._state[1]:
            return None
        if self._poll_target is not None and _IS_MICROPYTHON:
            # MicroPython: delegate to the generator-coroutine in
            # _stdio_mp.  ``await`` on a generator-coroutine is the
            # standard MicroPython asyncio pattern (see how
            # extmod/asyncio/stream.py composes Stream.readline with
            # async def callers).  CPython rejects ``return <value>``
            # inside a generator-based coroutine at compile time, so
            # that path lives in its own module that CPython never
            # imports.
            from . import _stdio_mp
            return await _stdio_mp.recv_loop(self._poll_target, self._state)
        # Blocking fallback — used on the windows port and in CPython
        # tests where the user passed a non-pollable file-like.
        return await self._recv_blocking()

    async def _recv_blocking(self):
        if _HAVE_ASYNCIO:
            await asyncio.sleep(0)
        line = self._stdin.readline()
        if not line:
            return None
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeError as e:
                sys.stderr.write(
                    "picolet: non-utf8 bytes on stdin: " + str(e) + "\n"
                )
                return await self.recv()
        line = line.strip()
        if not line:
            return await self.recv()
        try:
            return json.loads(line)
        except (ValueError, Exception) as e:
            sys.stderr.write(
                "picolet: malformed JSON on stdin: " + str(e) + "\n"
            )
            return await self.recv()

    async def send(self, msg):
        # No ``await`` inside the body — keeps the on-the-wire framing
        # atomic without an explicit lock under MicroPython's
        # cooperative scheduler (see Transport.__doc__).
        line = json.dumps(msg)
        self._stdout.write(line)
        self._stdout.write("\n")
        try:
            self._stdout.flush()
        except (AttributeError, OSError):
            # Some stream wrappers omit flush.  The next write will
            # surface a real failure.
            pass

    async def close(self):
        self._state[1] = True


# ---------------------------------------------------------------------------
# MockTransport
# ---------------------------------------------------------------------------


class MockTransport:
    """In-process transport for unit tests.

    Two MockTransport instances can be wired together to form a pair
    (``pair_a, pair_b = MockTransport.pair()``) where messages ``send``
    from one are received by the other.

    Standalone, ``feed`` and ``drain`` provide test-driven inbox /
    outbox semantics.
    """

    def __init__(self, inbox=None, peer=None):
        self._inbox = list(inbox or [])
        self._outbox = []
        self._closed = False
        self._evt = None
        # When _peer is set, send(msg) deposits into the peer's inbox
        # instead of into our own outbox.  This is the "paired" mode.
        self._peer = peer

    @classmethod
    def pair(cls):
        a = cls()
        b = cls()
        a._peer = b
        b._peer = a
        return a, b

    # -------- test driver hooks --------

    def feed(self, msg):
        """Test-side: enqueue a message for the next recv() call."""
        self._inbox.append(msg)
        if self._evt is not None:
            self._evt.set()

    def drain(self):
        """Test-side: pop everything sent through this transport."""
        out = list(self._outbox)
        self._outbox.clear()
        return out

    @property
    def closed(self):
        return self._closed

    # -------- Transport contract --------

    async def recv(self):
        while not self._closed:
            if self._inbox:
                return self._inbox.pop(0)
            # Wait until something is fed or the transport is closed.
            if not _HAVE_ASYNCIO:
                raise RuntimeError("MockTransport.recv requires asyncio")
            self._evt = asyncio.Event()
            await self._evt.wait()
            self._evt = None
        return None

    async def send(self, msg):
        if self._peer is not None and not self._peer._closed:
            self._peer.feed(msg)
        # Always also record in the local outbox for inspection by tests.
        self._outbox.append(msg)

    async def close(self):
        self._closed = True
        if self._evt is not None:
            # Wake any pending recv() so it returns None.
            self._evt.set()


# ---------------------------------------------------------------------------
# InProcessTransport (PH11, FR-LV-4)
# ---------------------------------------------------------------------------


class InProcessTransport:
    """Paired in-process Transport for Python-Python IPC (FR-LV-4).

    Constructed via the ``pair()`` classmethod which returns two
    endpoint instances sharing two queues.  send() on endpoint A
    routes to recv() on endpoint B and vice-versa.  Both endpoints
    run on the same asyncio loop.

    Wire format is byte-identical to ``StdioTransport`` and
    ``WebviewTransport``: every send/recv round-trips through
    ``json.dumps`` / ``json.loads``.  This costs microseconds per call
    but keeps the dispatcher and PH13 SBOM tooling oblivious to the
    in-process case.

    Pair-close semantics: ``close()`` on either endpoint marks itself
    closed.  Subsequent ``recv()`` returns None (the EOF signal the
    dispatcher honours); a pending recv() on the peer is woken with
    None too (its outgoing queue is its peer's incoming queue, so
    closing one side means the peer cannot deliver new messages).
    """

    @classmethod
    def pair(cls):
        """Return two paired InProcessTransport endpoints.

        Each endpoint has its own incoming buffer; sending on A
        appends to B's incoming buffer (and signals B's recv), and
        vice-versa.
        """
        if not _HAVE_ASYNCIO:
            raise RuntimeError(
                "InProcessTransport.pair requires asyncio"
            )
        a = cls()
        b = cls()
        a._peer = b
        b._peer = a
        return a, b

    def __init__(self):
        if not _HAVE_ASYNCIO:
            raise RuntimeError(
                "InProcessTransport requires asyncio"
            )
        # ``_inbox`` is the list this endpoint reads from in recv().
        # ``send()`` deposits into the peer's _inbox (not our own).
        # MicroPython's asyncio has Event but not Queue, so we
        # roll our own queue out of a list + an Event.
        self._inbox = []
        self._evt = None  # asyncio.Event lazily constructed inside recv
        self._closed = False
        self._peer = None

    @property
    def closed(self):
        return self._closed

    def _wake(self):
        """Signal any pending recv() that new data (or close) arrived."""
        if self._evt is not None:
            self._evt.set()

    async def recv(self):
        # Drain anything sitting in the inbox before checking closed —
        # close() races with send() and a final send right before
        # close should still be readable by the peer.
        while True:
            if self._inbox:
                raw = self._inbox.pop(0)
                if raw is None:
                    # Close sentinel.
                    return None
                try:
                    return json.loads(raw)
                except (ValueError, Exception) as e:
                    sys.stderr.write(
                        "picolet: InProcessTransport malformed JSON: " + str(e) + "\n"
                    )
                    continue
            if self._closed:
                return None
            self._evt = asyncio.Event()
            await self._evt.wait()
            self._evt = None

    async def send(self, msg):
        if self._closed:
            return
        line = json.dumps(msg)
        peer = self._peer
        if peer is None or peer._closed:
            # Peer is gone: drop silently.  Matches StdioTransport's
            # write-to-closed-stdout behaviour.
            return
        peer._inbox.append(line)
        peer._wake()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        # Wake any recv() blocked on this side.
        self._wake()
        # Wake the peer's recv() by enqueueing a close sentinel onto
        # its inbox so it can return None promptly.
        peer = self._peer
        if peer is not None and not peer._closed:
            peer._inbox.append(None)
            peer._wake()
