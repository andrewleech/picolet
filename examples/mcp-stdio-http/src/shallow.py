"""Cheap top-level JSON-RPC inspection for large messages.

MicroPython's json.loads costs ~1.8 s/MB, which is unacceptable for multi-MB
tool results that are forwarded verbatim. We only need id/method, so for
large messages walk the top-level members and stop at the first container
once enough is known. Anything unexpected falls back to a full json.loads.
"""

import json

LARGE = 65536
_WS = b" \t\r\n"


def _skip_ws(raw, i, n):
    while i < n and raw[i] in _WS:
        i += 1
    return i


def _string_end(raw, i):
    """Index just past the closing quote of the string opening at i."""
    j = i + 1
    while True:
        q = raw.find(b'"', j)
        if q < 0:
            return -1
        k = q - 1
        bs = 0
        while k > i and raw[k] == 0x5C:
            bs += 1
            k -= 1
        if bs % 2 == 0:
            return q + 1
        j = q + 1


def peek(raw):
    """Return {"id"?, "method"?, marker keys} or None when unsure."""
    n = len(raw)
    i = _skip_ws(raw, 0, n)
    if i >= n or raw[i] != 0x7B:
        return None
    i += 1
    out = {}
    while True:
        i = _skip_ws(raw, i, n)
        if i >= n:
            return None
        if raw[i] == 0x7D:
            return out
        if raw[i] == 0x2C:
            i += 1
            continue
        if raw[i] != 0x22:
            return None
        e = _string_end(raw, i)
        if e < 0 or e - i > 64:
            return None
        key = raw[i + 1 : e - 1]
        if b"\\" in key:
            return None
        key = key.decode()
        i = _skip_ws(raw, e, n)
        if i >= n or raw[i] != 0x3A:
            return None
        i = _skip_ws(raw, i + 1, n)
        if i >= n:
            return None
        c = raw[i]
        if c in b"{[":
            if key in ("result", "error") and "id" in out:
                out[key] = True
                return out
            if key == "params" and "method" in out and "id" in out:
                out[key] = True
                return out
            return None
        if c == 0x22:
            e = _string_end(raw, i)
            if e < 0 or e - i > 4096:
                return None
            val = raw[i:e]
        else:
            e = i
            while e < n and raw[e] not in b",} \t\r\n":
                e += 1
            val = raw[i:e]
        if key in ("id", "method"):
            try:
                out[key] = json.loads(val.decode())
            except (ValueError, UnicodeError):
                return None
        i = e


def loads_msg(raw):
    """Parse a JSON-RPC message; large ones get a shallow view when possible."""
    if len(raw) > LARGE:
        got = peek(raw)
        if got is not None:
            return got
    return json.loads(raw.decode("utf-8"))
