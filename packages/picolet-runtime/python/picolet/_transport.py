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
        self._closed = False
        # Buffer accumulating partial lines from non-blocking reads.
        # Always bytes on linux (we read from .buffer); always str on
        # CPython tests that pass a text-mode StringIO.
        self._buf = None
        # Pre-resolved fd-like object to poll on; None if we're falling
        # back to a blocking readline.
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
        self._buf = b"" if hasattr(self._stdin, "buffer") else ""

    async def recv(self):
        while not self._closed:
            line = await self._readline()
            if line is None or line == "" or line == b"":
                return None
            # Normalise to str for json.loads.
            if isinstance(line, bytes):
                try:
                    line = line.decode("utf-8")
                except UnicodeError as e:
                    sys.stderr.write(
                        "picolet: non-utf8 bytes on stdin: " + str(e) + "\n"
                    )
                    continue
            line = line.strip()
            if not line:
                # Blank line — skip without warning.
                continue
            try:
                return json.loads(line)
            except (ValueError, Exception) as e:
                # Catching Exception too because MicroPython's json raises
                # SyntaxError-ish or ValueError depending on input; in either
                # case the framing of this line is unrecoverable.
                sys.stderr.write(
                    "picolet: malformed JSON on stdin: " + str(e) + "\n"
                )
                continue
        return None

    async def _readline(self):
        if self._poll_target is not None:
            return await self._readline_async()
        # Blocking fallback — used on the windows port and in CPython
        # tests where the user passed a non-pollable file-like.
        return await self._readline_blocking()

    async def _readline_async(self):
        # Mirror extmod/asyncio/stream.py:Stream.readline — yield on the
        # io queue, then drain the fd's readline (which is non-blocking
        # once poll has signalled readable, per the FileIO semantics on
        # the unix port).
        from asyncio.core import _io_queue
        s = self._poll_target
        buf = self._buf
        empty = b"" if isinstance(buf, (bytes, bytearray)) else ""
        nl = b"\n" if isinstance(buf, (bytes, bytearray)) else "\n"
        while True:
            # Look for an already-buffered complete line.
            i = buf.find(nl)
            if i >= 0:
                line = buf[: i + 1]
                self._buf = buf[i + 1 :]
                return line
            # No newline buffered — wait for more data.
            try:
                yield _io_queue.queue_read(s)
            except Exception:
                # If the loop tore down our queue entry, treat as EOF.
                self._buf = empty
                return empty
            try:
                chunk = s.readline()
            except (OSError, ValueError):
                self._buf = empty
                return empty
            if chunk is None:
                # Non-blocking read returned "no data right now"; loop.
                continue
            if chunk == empty:
                # EOF.  Emit any partial line we had buffered, then
                # subsequent calls return empty.
                if buf:
                    self._buf = empty
                    return buf
                return empty
            buf += chunk

    async def _readline_blocking(self):
        # Falls back to a synchronous readline.  Yields to the loop once
        # so the dispatcher can still cooperate with other tasks
        # *between* messages — but a single readline is blocking.  This
        # branch is only hit on the windows port (no select.poll) and
        # in CPython tests that pass an unpollable StringIO.
        if _HAVE_ASYNCIO:
            await asyncio.sleep(0)
        line = self._stdin.readline()
        return line

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
        self._closed = True


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
