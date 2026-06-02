"""threading — single-thread no-op shim for picolet_tui.

VERY LOUD: there is no actual threading here. The picolet_tui runtime
runs a single asyncio loop in a single OS thread (synthesis D6,
NFR-TUI-7). All Textual-style "thread primitives" Textual itself
imports are satisfied by trivial implementations that never block and
never contend. ``Thread`` itself constructs so module-level
``threading.Thread`` references at import time do not explode, but
``.start()`` refuses loudly — spec D6 prohibits worker threads in
v0.1.

Implemented:
  Lock, RLock                — context-manager + acquire/release
  Event                      — set/clear/is_set/wait
  Condition                  — Lock + asyncio.Event backing
  current_thread / main_thread — singleton with .name = 'MainThread'
  get_ident                  — always 0 (single-thread assumption)
  Thread                     — import-only stub; .start() raises

Deliberately NOT implemented (and why):
  Semaphore, BoundedSemaphore — Textual core does not use them
  Timer                      — replaced by asyncio.call_later patterns
  local                      — irrelevant under one thread
  Barrier                    — no use site in the v0.1 surface
  daemon thread machinery    — see D6

Supports: FR-TUI-1..6 (App lifecycle imports of threading.RLock from
Textual's MessagePump survive at import time), NFR-TUI-7 (no native
thread state), NFR-TUI-19 (stays within the 20 KiB _shims budget).
"""

from __future__ import annotations

import asyncio


# Synthesis D6: get_ident is a constant because the only thread is "this
# one". Textual's MessagePump uses get_ident purely to assert it is on
# the loop thread; returning a stable sentinel keeps those asserts true.
def get_ident() -> int:
    return 0


class _NullLock:
    """Backing impl for both Lock and RLock — re-entry is trivially safe
    when there is only one thread, so there is no behavioural reason to
    distinguish Lock from RLock here.
    """

    __slots__ = ()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return True

    def release(self) -> None:
        return None

    def __enter__(self) -> "_NullLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


Lock = _NullLock
RLock = _NullLock


class Event:
    """Async-friendly Event. ``wait(timeout)`` is best-effort: if the
    flag is already set we return True immediately; otherwise we return
    False without sleeping, because synchronously blocking the single
    thread would deadlock the asyncio loop. Code that needs to await an
    Event should use ``asyncio.Event`` directly.
    """

    __slots__ = ("_flag",)

    def __init__(self) -> None:
        self._flag = False

    def set(self) -> None:
        self._flag = True

    def clear(self) -> None:
        self._flag = False

    def is_set(self) -> bool:
        return self._flag

    def wait(self, timeout: float | None = None) -> bool:
        return self._flag


class Condition:
    """Lock + asyncio.Event composition. notify/notify_all wake the
    inner asyncio.Event so any awaiter on ``wait_async`` (a non-stdlib
    extension Textual does not call) can resume. The stdlib ``wait``
    signature returns immediately — same rationale as Event.wait.
    """

    def __init__(self, lock: _NullLock | None = None) -> None:
        self._lock = lock if lock is not None else _NullLock()
        self._evt = asyncio.Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "Condition":
        self._lock.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.__exit__(exc_type, exc, tb)

    def wait(self, timeout: float | None = None) -> bool:
        return False

    def notify(self, n: int = 1) -> None:
        self._evt.set()

    def notify_all(self) -> None:
        self._evt.set()


class _MainThread:
    """Singleton returned by current_thread/main_thread — only the .name
    attribute is exercised by Textual (logged in error paths).
    """

    __slots__ = ()
    name = "MainThread"
    daemon = False

    def is_alive(self) -> bool:
        return True

    def getName(self) -> str:
        return self.name


_MAIN = _MainThread()


def current_thread() -> _MainThread:
    return _MAIN


def main_thread() -> _MainThread:
    return _MAIN


class Thread:
    """Import-only stub. Construction succeeds so ``Thread(target=...)``
    at module scope does not fail import; ``.start()`` raises so any
    runtime attempt to spawn a worker is caught immediately. See
    docs/tui/research/00-synthesis.md decision D6 for the rationale.
    """

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, *, daemon=None):
        self._target = target
        self.name = name or "Thread-stub"
        self.daemon = bool(daemon)

    def start(self) -> None:
        raise NotImplementedError(
            "picolet_tui forbids worker threads (synthesis D6); "
            "convert the workload to an asyncio task"
        )

    def join(self, timeout: float | None = None) -> None:
        return None

    def is_alive(self) -> bool:
        return False
