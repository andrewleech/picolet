"""picolet_tui._shims.callback — arity introspection without inspect.signature.

Implemented:
  count_parameters(fn) -> int   positional arity, excluding ``self``
  has_kwargs(fn)       -> bool  reads CO_VARKEYWORDS from fn.__code__.co_flags
  has_varargs(fn)      -> bool  reads CO_VARARGS from fn.__code__.co_flags

Deliberately NOT implemented:
  * The full inspect.Parameter / inspect.Signature object graph.
  * Keyword-only counting, annotation extraction, default introspection.
  * Source-line / __defaults__ / __kwdefaults__ inspection.
  These are not on the v0.1 dispatch path; research doc 03 §"Per-module
  table" calls inspect.signature out as the single biggest stdlib blocker,
  and the only thing Textual-style dispatch actually needs is the
  positional arity (FR-TUI-14, FR-TUI-20). Adding the rest would only
  pay a romfs cost against NFR-TUI-19.

Spec coverage:
  * FR-TUI-14 — name-based on_<message> dispatch records arity at @widget
    decoration time so handlers may be ``(self)`` or ``(self, message)``.
  * FR-TUI-20 — watch_<name>(self, new) vs watch_<name>(self, old, new)
    dispatch on Reactive assignment.
  * FR-TUI-57 / D1 — all class-time introspection lives in the @widget
    decorator; this module is its only arity oracle.

Module path: picolet_tui._shims.callback
"""

# CPython's bit layout for code.co_flags; MicroPython matches because
# the values are an ABI inherited from CPython's compile.h.
_CO_VARARGS = 0x04
_CO_VARKEYWORDS = 0x08


def count_parameters(fn):
    """Return the positional parameter count of ``fn``, excluding ``self``.

    The fallbacks unwrap functools.partial and bound methods before
    landing on __code__.co_argcount. Callable objects are introspected
    via type(fn).__call__ (one level of unwrap; nested __call__ chains
    are not a real-world dispatch shape on this code path). Builtins
    expose no __code__ on MicroPython, so the last resort is 1 — a
    sentinel chosen so on_<msg>(self, msg) dispatch errs toward "pass
    the message" rather than dropping it silently.
    """
    # functools.partial: positional args supplied at partial-construction
    # time have already consumed leading parameters of fn.func.
    if hasattr(fn, "func") and hasattr(fn, "args"):
        return count_parameters(fn.func) - len(fn.args)
    # Bound method: __self__ has consumed the leading ``self`` slot.
    if hasattr(fn, "__func__"):
        return count_parameters(fn.__func__) - 1
    code = getattr(fn, "__code__", None)
    if code is not None:
        return code.co_argcount
    # Callable instance — recurse on the unbound __call__ then drop self.
    call = getattr(type(fn), "__call__", None)
    if call is not None and hasattr(call, "__code__"):
        return call.__code__.co_argcount - 1
    return 1


def has_varargs(fn):
    """True if ``fn`` declares ``*args``. Builtins report False."""
    code = getattr(fn, "__code__", None)
    if code is None:
        return False
    return bool(code.co_flags & _CO_VARARGS)


def has_kwargs(fn):
    """True if ``fn`` declares ``**kwargs``. Builtins report False."""
    code = getattr(fn, "__code__", None)
    if code is None:
        return False
    return bool(code.co_flags & _CO_VARKEYWORDS)
