"""Pure-Python ``selectors`` shim over MicroPython's core ``select`` module.

Implemented:
  EVENT_READ, EVENT_WRITE       — int constants, CPython values.
  SelectorKey                    — (fileobj, fd, events, data) tuple-shape.
  BaseSelector                   — minimal abstract surface.
  SelectSelector                 — wraps ``select.select`` (always available).
  PollSelector                   — wraps ``select.poll`` (preferred when present).
  DefaultSelector                — alias of PollSelector if importable, else
                                   SelectSelector.

Deliberately NOT implemented:
  EpollSelector, KqueueSelector, DevpollSelector — platform back-ends with no
    portable MicroPython equivalent; the picolet-tui driver only needs stdin
    + a wake pipe, which ``poll`` handles on both targets (Linux unix port,
    Windows MinGW per research doc 04).
  ``BaseSelector.select(timeout=None)`` with sub-millisecond resolution — MP
    ``select.poll().poll()`` takes integer milliseconds; we round up.

Spec mapping:
  Phase 2b shim per ``docs/tui/research/00-synthesis.md`` §2 Phase 2b and
  ``docs/tui/research/03-mp-stdlib-coverage.md`` §"Per-module table" row
  ``select/selectors``. Consumed by the asyncio event-loop integration that
  backs FR-TUI-2 (App.run_async), FR-TUI-7 (driver enable), and FR-TUI-15..18
  (keyboard / mouse / paste event delivery from the tuiterm fd).
"""

import select


EVENT_READ = 1
EVENT_WRITE = 2


class SelectorKey(tuple):
    """``(fileobj, fd, events, data)`` quadruple with named attribute access.

    Subclassing ``tuple`` rather than using ``collections.namedtuple`` keeps
    the shim free of one more micropython-lib dependency at import time.
    """

    __slots__ = ()

    def __new__(cls, fileobj, fd, events, data):
        return tuple.__new__(cls, (fileobj, fd, events, data))

    @property
    def fileobj(self):
        return self[0]

    @property
    def fd(self):
        return self[1]

    @property
    def events(self):
        return self[2]

    @property
    def data(self):
        return self[3]


def _fileobj_to_fd(fileobj):
    if isinstance(fileobj, int):
        return fileobj
    # CPython falls back to fileobj.fileno(); MP file-like objects expose the
    # same method on raw streams and asyncio StreamReader/Writer transports.
    return fileobj.fileno()


class BaseSelector:
    def __init__(self):
        self._fd_to_key = {}

    def register(self, fileobj, events, data=None):
        if (not events) or (events & ~(EVENT_READ | EVENT_WRITE)):
            raise ValueError("invalid events: " + repr(events))
        fd = _fileobj_to_fd(fileobj)
        if fd in self._fd_to_key:
            raise KeyError("fd " + repr(fd) + " is already registered")
        key = SelectorKey(fileobj, fd, events, data)
        self._fd_to_key[fd] = key
        return key

    def unregister(self, fileobj):
        fd = _fileobj_to_fd(fileobj)
        try:
            return self._fd_to_key.pop(fd)
        except KeyError:
            raise KeyError("fd " + repr(fd) + " is not registered")

    def modify(self, fileobj, events, data=None):
        # The CPython reference unregisters + registers when events change and
        # mutates in place when only ``data`` changes; we always re-register
        # because MP ``poll`` requires it anyway.
        self.unregister(fileobj)
        return self.register(fileobj, events, data)

    def select(self, timeout=None):
        raise NotImplementedError

    def close(self):
        self._fd_to_key.clear()

    def get_map(self):
        return self._fd_to_key

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class SelectSelector(BaseSelector):
    def select(self, timeout=None):
        readers = [k.fd for k in self._fd_to_key.values() if k.events & EVENT_READ]
        writers = [k.fd for k in self._fd_to_key.values() if k.events & EVENT_WRITE]
        if timeout is not None and timeout < 0:
            timeout = 0
        r, w, _ = select.select(readers, writers, [], timeout)
        ready = {}
        for fd in r:
            ready[fd] = ready.get(fd, 0) | EVENT_READ
        for fd in w:
            ready[fd] = ready.get(fd, 0) | EVENT_WRITE
        return [(self._fd_to_key[fd], ev) for fd, ev in ready.items() if fd in self._fd_to_key]


class PollSelector(BaseSelector):
    def __init__(self):
        super().__init__()
        self._poll = select.poll()

    def register(self, fileobj, events, data=None):
        key = super().register(fileobj, events, data)
        mask = 0
        if events & EVENT_READ:
            mask |= select.POLLIN
        if events & EVENT_WRITE:
            mask |= select.POLLOUT
        self._poll.register(key.fd, mask)
        return key

    def unregister(self, fileobj):
        key = super().unregister(fileobj)
        self._poll.unregister(key.fd)
        return key

    def select(self, timeout=None):
        # MP ``poll.poll`` takes int milliseconds; ``None`` blocks indefinitely.
        if timeout is None:
            ms = -1
        elif timeout <= 0:
            ms = 0
        else:
            ms = int(timeout * 1000 + 0.5) or 1
        ready = []
        for fd, mask in self._poll.poll(ms):
            key = self._fd_to_key.get(fd)
            if key is None:
                continue
            ev = 0
            if mask & select.POLLIN:
                ev |= EVENT_READ
            if mask & select.POLLOUT:
                ev |= EVENT_WRITE
            ready.append((key, ev & key.events))
        return ready


# ``select.poll`` is the preferred back-end per research doc 03; fall back
# only on builds that omit ``MICROPY_PY_SELECT_POLL`` (rare; the picolet-tui
# unix and windows variants both enable it).
try:
    select.poll()
except (AttributeError, OSError):
    DefaultSelector = SelectSelector
else:
    DefaultSelector = PollSelector
