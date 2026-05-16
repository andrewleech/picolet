# picolet._stdio_mp — MicroPython-only non-blocking stdin recv loop.
#
# This module uses the ``yield core._io_queue.queue_read(s)`` pattern
# from extmod/asyncio/stream.py, which is a raw generator-based
# coroutine.  ``return <value>`` inside a function that also
# ``yield``s is rejected by CPython's compiler (PEP 380 vs PEP 492
# tension) but accepted by MicroPython for plain generator-coroutines.
#
# This file is imported lazily by _transport.StdioTransport when
# running under MicroPython only.  CPython unit tests never import it,
# so the syntax is parsed only on the target where it works.

import json
import sys as _sys
from asyncio.core import _io_queue
from ._transport import MAX_MESSAGE_BYTES, _reject_oversized


# async (generator-coroutine; MicroPython convention)
def recv_loop(stream, state):
    """Drain one decoded JSON message from a non-blocking stream.

    ``stream`` must be readable via ``select.poll`` and expose a
    non-blocking ``readline()``.  ``state`` is a mutable list::

        [buf, closed_flag, stdout_ref, max_bytes]

    ``buf`` is the accumulated read buffer (bytes); ``closed_flag`` is a
    boolean signalling external close; ``stdout_ref`` is the stdout stream
    used for oversized-message error replies (may be None); ``max_bytes``
    is the per-message cap (S4).  Elements 2 and 3 are optional for
    backward compatibility and default to None and MAX_MESSAGE_BYTES.

    Partial lines persist across awaits.  Returns the next decoded dict,
    or None on EOF / close.  Malformed JSON lines are logged to stderr
    and skipped.  Lines exceeding max_bytes are logged, dropped, and (if
    an id is detectable) replied to with a structured error.
    """
    empty = b""
    nl = b"\n"
    stdout = state[2] if len(state) > 2 else None
    max_bytes = state[3] if len(state) > 3 else MAX_MESSAGE_BYTES
    while not state[1]:
        # Drain a single newline-terminated line.
        line = None
        while True:
            buf = state[0]
            # If the accumulated buffer exceeds the cap without a newline it
            # can never form a valid message.  Discard and keep reading.
            if len(buf) > max_bytes:
                _sys.stderr.write(
                    "picolet: inbound buffer too large ({}B); discarding\n".format(
                        len(buf)
                    )
                )
                _reject_oversized(stdout, buf)
                state[0] = empty
                buf = empty
            i = buf.find(nl)
            if i >= 0:
                line = buf[: i + 1]
                state[0] = buf[i + 1 :]
                break
            try:
                yield _io_queue.queue_read(stream)
            except Exception:
                # The loop tore down our queue entry.  Treat as EOF.
                state[0] = empty
                line = empty
                break
            try:
                chunk = stream.readline()
            except (OSError, ValueError):
                state[0] = empty
                line = empty
                break
            if chunk is None:
                # Non-blocking read returned "no data right now".
                continue
            if chunk == empty:
                # EOF.  Emit any partial line first.
                if state[0]:
                    line = state[0]
                    state[0] = empty
                else:
                    line = empty
                break
            state[0] += chunk
        if not line:
            return None
        # Enforce per-line cap before attempting UTF-8 decode.
        if len(line) > max_bytes:
            _reject_oversized(stdout, line)
            continue
        # Strip trailing whitespace / newline.
        try:
            stripped = line.decode("utf-8").strip()
        except UnicodeError as e:
            _sys.stderr.write("picolet: non-utf8 bytes on stdin: " + str(e) + "\n")
            continue
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except (ValueError, Exception) as e:
            _sys.stderr.write("picolet: malformed JSON on stdin: " + str(e) + "\n")
            continue
    return None
