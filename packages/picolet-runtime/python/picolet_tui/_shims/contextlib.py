"""picolet_tui._shims.contextlib — frozen replacement for the stdlib ``contextlib``.

Registered into ``sys.modules['contextlib']`` by ``picolet_tui._shims``.
Targets the surface area Textual (and the TuiHarness, FR-TUI-60..63) reach for:
``asynccontextmanager`` + ``AsyncExitStack`` (the hot path for
``App.run_test``), plus the sync ``contextmanager`` / ``ExitStack`` /
``nullcontext`` / ``closing`` / ``suppress`` chunk so downstream imports
don't fall through to micropython-lib's partial ``ucontextlib``.

Implemented:
    contextmanager              — generator decorator (PEP 343)
    asynccontextmanager         — async generator decorator (PEP 492 + PEP 525)
    ExitStack                   — sync cleanup stack with LIFO unwind
    AsyncExitStack              — async cleanup stack with LIFO unwind;
                                  mixes sync + async callbacks transparently
    nullcontext(enter_result)   — no-op CM, sync AND async (both protocols)
    closing(thing)              — calls .close() on exit
    suppress(*exc_types)        — swallows listed exception types
    redirect_stdout, redirect_stderr — std-stream redirector CMs

Deliberately NOT implemented:
    aclosing                    — uses ``__aexit__`` chain that Textual never
                                  reaches in v0.1 widgets
    ContextDecorator / AsyncContextDecorator
                                — Textual doesn't decorate functions with CMs
    chdir                       — bare-metal targets have no cwd
    AbstractContextManager / AbstractAsyncContextManager
                                — these are ABCs and the abc shim is a stub
                                  (research 03 §"Per-module table")

Supports:
    FR-TUI-60..63 (TuiHarness async lifecycle, async test surface)
    NFR-TUI-9, NFR-TUI-11, NFR-TUI-20 (single-thread asyncio model, D6 —
    no re-entrancy locking is needed inside the stacks)
"""

import sys


# -- Sync ``contextmanager`` --------------------------------------------------
#
# Lifted in shape from CPython's ``contextlib._GeneratorContextManager`` and
# trimmed: no ``ContextDecorator`` base (Textual never uses with-blocks as
# function decorators), no ``__class_getitem__`` (typing shim handles that),
# no ``_recreate_cm`` (only matters for decorator reuse).

class _GeneratorCM:
    def __init__(self, gen):
        self.gen = gen

    def __enter__(self):
        try:
            return next(self.gen)
        except StopIteration:
            raise RuntimeError("generator didn't yield") from None

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                next(self.gen)
            except StopIteration:
                return False
            raise RuntimeError("generator didn't stop")
        # An exception escaped the with-body; deliver it into the generator
        # so its ``finally`` / ``except`` clauses can run.  If the generator
        # swallows it (re-raising StopIteration) we report suppression by
        # returning True — unless the StopIteration *is* our injected exc,
        # which would otherwise hide a genuine bug.
        if exc is None:
            exc = exc_type()
        try:
            self.gen.throw(exc_type, exc, tb)
        except StopIteration as stop:
            return stop is not exc
        except BaseException as raised:
            # Re-raised something different — let it propagate, but don't
            # mask the original by returning True.
            if raised is exc:
                return False
            raise
        raise RuntimeError("generator didn't stop after throw()")


def contextmanager(func):
    def helper(*args, **kwds):
        return _GeneratorCM(func(*args, **kwds))
    return helper


# -- Async ``asynccontextmanager`` -------------------------------------------
#
# MicroPython's asyncio supports awaiting coroutines from ``__aenter__`` /
# ``__aexit__`` (extmod/asyncio).  The shape mirrors ``_GeneratorCM`` but
# drives the async generator via ``__anext__`` / ``athrow`` and ``await``s
# both.  Required by Textual's ``App.run_test`` pattern (see 00-synthesis
# §Phase 7).

class _AsyncGeneratorCM:
    def __init__(self, agen):
        self.agen = agen

    async def __aenter__(self):
        try:
            return await self.agen.__anext__()
        except StopAsyncIteration:
            raise RuntimeError("async generator didn't yield") from None

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                await self.agen.__anext__()
            except StopAsyncIteration:
                return False
            raise RuntimeError("async generator didn't stop")
        if exc is None:
            exc = exc_type()
        try:
            await self.agen.athrow(exc_type, exc, tb)
        except StopAsyncIteration as stop:
            return stop is not exc
        except BaseException as raised:
            if raised is exc:
                return False
            raise
        raise RuntimeError("async generator didn't stop after athrow()")


def asynccontextmanager(func):
    def helper(*args, **kwds):
        return _AsyncGeneratorCM(func(*args, **kwds))
    return helper


# -- nullcontext --------------------------------------------------------------
#
# Implements both the sync and async CM protocols so the same instance can
# be used from either ``with`` or ``async with``.  Matches CPython 3.10+
# which extended nullcontext to be async-aware.

class nullcontext:
    def __init__(self, enter_result=None):
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, *exc):
        return False

    async def __aenter__(self):
        return self.enter_result

    async def __aexit__(self, *exc):
        return False


# -- closing ------------------------------------------------------------------

class closing:
    def __init__(self, thing):
        self.thing = thing

    def __enter__(self):
        return self.thing

    def __exit__(self, *exc):
        self.thing.close()
        return False


# -- suppress -----------------------------------------------------------------

class suppress:
    def __init__(self, *exceptions):
        self._exceptions = exceptions

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, self._exceptions)


# -- redirect_stdout / redirect_stderr ----------------------------------------
#
# MicroPython does allow assignment to ``sys.stdout`` / ``sys.stderr`` on
# the unix port, so we implement these for real rather than stubbing.  The
# Textual test harness uses them to capture rendered output during widget
# tests (FR-TUI-60).  We do NOT support nested redirects of the same
# stream from different threads because v0.1 is single-thread (D6).

class _RedirectStream:
    _stream = None  # subclass overrides to "stdout" or "stderr"

    def __init__(self, new_target):
        self._new_target = new_target
        self._old_targets = []

    def __enter__(self):
        self._old_targets.append(getattr(sys, self._stream))
        setattr(sys, self._stream, self._new_target)
        return self._new_target

    def __exit__(self, *exc):
        setattr(sys, self._stream, self._old_targets.pop())
        return False


class redirect_stdout(_RedirectStream):
    _stream = "stdout"


class redirect_stderr(_RedirectStream):
    _stream = "stderr"


# -- ExitStack ----------------------------------------------------------------
#
# Stores callbacks as (kind, payload) tuples.  ``kind`` is one of:
#   'cm'   payload = (exit_method, cm)        push() of a context manager
#   'cb'   payload = callable(*args, **kwds)  callback()
#
# CPython models each entry as a wrapper callable taking (exc_type, exc, tb)
# so it can route both paths through a single list; we keep them
# discriminated because MP closures are cheap but the indirection makes
# tracebacks harder to read.

class ExitStack:
    def __init__(self):
        self._stack = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Unwind in reverse order.  Any callback can swallow the in-flight
        # exception (by returning True from its __exit__), in which case we
        # carry on with no exception; any callback can also raise a new
        # exception, which replaces what we were carrying.
        suppressed = False
        pending_exc_type, pending_exc, pending_tb = exc_type, exc, tb
        while self._stack:
            kind, payload = self._stack.pop()
            try:
                if kind == "cm":
                    exit_fn, _cm = payload
                    if exit_fn(pending_exc_type, pending_exc, pending_tb):
                        suppressed = True
                        pending_exc_type, pending_exc, pending_tb = None, None, None
                else:  # 'cb'
                    cb, args, kwds = payload
                    cb(*args, **kwds)
            except BaseException as new_exc:
                pending_exc_type = type(new_exc)
                pending_exc = new_exc
                pending_tb = getattr(new_exc, "__traceback__", None)
                suppressed = False
        if pending_exc is not None and pending_exc is not exc:
            raise pending_exc
        return suppressed

    def enter_context(self, cm):
        # Match CPython's lookup-on-type semantics: __enter__/__exit__ are
        # resolved from the type, not the instance, so context managers
        # implemented as classes with descriptor methods behave correctly.
        cls = type(cm)
        result = cls.__enter__(cm)
        self._stack.append(("cm", (cls.__exit__, cm)))
        return result

    def push(self, cm):
        cls = type(cm)
        self._stack.append(("cm", (cls.__exit__, cm)))
        return cm

    def callback(self, callback, *args, **kwds):
        self._stack.append(("cb", (callback, args, kwds)))
        return callback

    def pop_all(self):
        new = ExitStack()
        new._stack = self._stack
        self._stack = []
        return new

    def close(self):
        self.__exit__(None, None, None)


# -- AsyncExitStack -----------------------------------------------------------
#
# Same storage discipline as ExitStack but unwinds with ``await`` when the
# entry is async.  ``kind`` adds two values:
#   'acm'  payload = (aexit_coro_fn, cm)      push_async_exit() / enter_async_context
#   'acb'  payload = async callable(*args)    push_async_callback()
#
# Textual's ``App.run_test`` uses this to layer the screen capture, virtual
# clock, and pty driver cleanup (00-synthesis §Phase 7).

class AsyncExitStack:
    def __init__(self):
        self._stack = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        suppressed = False
        pending_exc_type, pending_exc, pending_tb = exc_type, exc, tb
        while self._stack:
            kind, payload = self._stack.pop()
            try:
                if kind == "cm":
                    exit_fn, _cm = payload
                    if exit_fn(pending_exc_type, pending_exc, pending_tb):
                        suppressed = True
                        pending_exc_type, pending_exc, pending_tb = None, None, None
                elif kind == "acm":
                    aexit_fn, _cm = payload
                    if await aexit_fn(pending_exc_type, pending_exc, pending_tb):
                        suppressed = True
                        pending_exc_type, pending_exc, pending_tb = None, None, None
                elif kind == "cb":
                    cb, args, kwds = payload
                    cb(*args, **kwds)
                else:  # 'acb'
                    cb, args, kwds = payload
                    await cb(*args, **kwds)
            except BaseException as new_exc:
                pending_exc_type = type(new_exc)
                pending_exc = new_exc
                pending_tb = getattr(new_exc, "__traceback__", None)
                suppressed = False
        if pending_exc is not None and pending_exc is not exc:
            raise pending_exc
        return suppressed

    def enter_context(self, cm):
        cls = type(cm)
        result = cls.__enter__(cm)
        self._stack.append(("cm", (cls.__exit__, cm)))
        return result

    async def enter_async_context(self, cm):
        cls = type(cm)
        result = await cls.__aenter__(cm)
        self._stack.append(("acm", (cls.__aexit__, cm)))
        return result

    def push(self, cm):
        cls = type(cm)
        self._stack.append(("cm", (cls.__exit__, cm)))
        return cm

    def push_async_exit(self, cm):
        cls = type(cm)
        self._stack.append(("acm", (cls.__aexit__, cm)))
        return cm

    def callback(self, callback, *args, **kwds):
        self._stack.append(("cb", (callback, args, kwds)))
        return callback

    def push_async_callback(self, callback, *args, **kwds):
        self._stack.append(("acb", (callback, args, kwds)))
        return callback

    def pop_all(self):
        new = AsyncExitStack()
        new._stack = self._stack
        self._stack = []
        return new

    async def aclose(self):
        await self.__aexit__(None, None, None)


__all__ = (
    "contextmanager",
    "asynccontextmanager",
    "ExitStack",
    "AsyncExitStack",
    "nullcontext",
    "closing",
    "suppress",
    "redirect_stdout",
    "redirect_stderr",
)
