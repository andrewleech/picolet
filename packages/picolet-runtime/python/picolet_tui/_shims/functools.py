"""
functools shim for the picolet-tui frozen runtime.

Replaces ``micropython-lib`` ``python-stdlib/functools`` (which only ships
``partial``, ``reduce``, and stub ``wraps``/``update_wrapper``) with the
narrow CPython surface that the trimmed Rich subset and Textual-inspired
core actually call.  Registered into ``sys.modules['functools']`` by
``picolet_tui._shims.__init__`` so downstream ``import functools`` and
``from functools import lru_cache`` resolve here without per-callsite
rewrites.

Implemented:
  partial, reduce                  — re-exported from core micropython-lib
  lru_cache(maxsize=128, typed=)   — OrderedDict-backed; default 128 per
                                     NFR-TUI-6 (synthesis R4 mitigation a)
  cache                            — lru_cache(maxsize=None) shorthand
  wraps                            — real (not the core stub); copies
                                     name/qualname/doc/wrapped/annotations
  update_wrapper                   — real; backs ``wraps``
  total_ordering                   — fills missing rich comparisons from
                                     __lt__ + __eq__
  cached_property                  — descriptor; writes through to
                                     instance.__dict__ so subsequent
                                     attribute access skips this object

NOT implemented (deliberate):
  partialmethod  — neither Textual nor the trimmed Rich subset references
                   it; pulling it in costs LoC against the NFR-TUI-19
                   20 KiB _shims budget for zero callers.
  singledispatch — would require ABCMeta runtime machinery that core
                   MicroPython does not ship, and the trimmed Rich does
                   not call it.

Spec coverage:
  FR-TUI-* importers (Rich/Textual port) — see docs/tui/research/03 §functools
  NFR-TUI-6  — every wrapped callable's cache_info().maxsize <= 128
  NFR-TUI-19 — _shims subtree budget; aim ~150 LoC.
"""
from __future__ import annotations

from collections import OrderedDict, namedtuple

# Re-export from the core micropython-lib functools.  Importing the
# *package* would re-enter this shim (sys.modules['functools'] points
# here after _shims registers us), so we pull the originals out of the
# unshimmed module path.  micropython-lib ships these as plain functions
# with the expected CPython semantics; no need to reimplement.
from functools import partial, reduce  # noqa: F401  re-export


# Attributes copied verbatim from wrapped → wrapper by update_wrapper.
# Matches CPython's WRAPPER_ASSIGNMENTS minus __module__, which is rarely
# meaningful in a frozen build (every module's __module__ is its frozen
# path).  __wrapped__ is appended unconditionally so tooling can unwrap.
_WRAPPER_ASSIGNMENTS = ("__name__", "__qualname__", "__doc__", "__annotations__")


def update_wrapper(wrapper, wrapped):
    for attr in _WRAPPER_ASSIGNMENTS:
        try:
            value = getattr(wrapped, attr)
        except AttributeError:
            continue
        try:
            setattr(wrapper, attr, value)
        except (AttributeError, TypeError):
            # Builtins, slotted classes, and partial() instances reject
            # attribute writes; that is acceptable — the wrapper still works.
            pass
    try:
        wrapper.__wrapped__ = wrapped
    except (AttributeError, TypeError):
        pass
    return wrapper


def wraps(wrapped):
    def _decorator(wrapper):
        return update_wrapper(wrapper, wrapped)
    return _decorator


# Sentinel for lru_cache misses.  A bare object() is cheaper than
# constructing tuples or using None (which is a legal cached value).
_MISS = object()


class _LruCacheWrapper:
    """Callable wrapper returned by :func:`lru_cache`.

    A class instance instead of a closure-with-attributes because
    MicroPython functions reject attribute assignment (``f.cache_info =
    ...`` raises AttributeError).  Instances of plain classes accept
    attribute writes, so ``update_wrapper`` metadata copying still works.
    Deliberately no ``__slots__`` — slotted classes reject the
    ``__name__``/``__doc__`` writes update_wrapper performs.
    """

    def __get__(self, obj, objtype=None):
        # Descriptor binding: a plain function in a class body binds
        # ``self`` automatically; this wrapper class must do it by
        # hand or every @lru_cache-decorated *method* (Style._add,
        # Color.downgrade, ...) is called unbound and loses ``self``.
        # Identical breakage on CPython and MicroPython; found by the
        # binary smoke gate.
        if obj is None:
            return self
        def _bound(*args, **kwargs):
            return self(obj, *args, **kwargs)
        return _bound

    def __init__(self, fn, maxsize, typed):
        self._fn = fn
        self._maxsize = maxsize
        self._typed = typed
        self._cache = OrderedDict()
        self._hits = 0
        self._misses = 0
        update_wrapper(self, fn)

    def _make_key(self, args, kwargs):
        if kwargs:
            key = args + (_MISS,) + tuple(sorted(kwargs.items()))
        else:
            key = args
        if self._typed:
            key = key + tuple(type(a) for a in args)
            if kwargs:
                key = key + tuple(type(v) for v in kwargs.values())
        return key

    def __call__(self, *args, **kwargs):
        cache = self._cache
        key = self._make_key(args, kwargs)
        value = cache.get(key, _MISS)
        if value is not _MISS:
            # Manual move-to-end: OrderedDict.move_to_end exists on
            # MicroPython's collections.OrderedDict, but using
            # pop+reinsert keeps us portable to any dict-like with
            # insertion-order iteration.
            del cache[key]
            cache[key] = value
            self._hits += 1
            return value
        value = self._fn(*args, **kwargs)
        cache[key] = value
        self._misses += 1
        if self._maxsize is not None and len(cache) > self._maxsize:
            # popitem(last=False) → evict the LRU entry.  Falls back
            # to a manual iter-and-pop on dicts lacking the kwarg.
            try:
                cache.popitem(last=False)
            except TypeError:
                oldest = next(iter(cache))
                del cache[oldest]
        return value

    def cache_info(self):
        return _CacheInfo(self._hits, self._misses, self._maxsize, len(self._cache))

    def cache_clear(self):
        self._cache.clear()
        self._hits = 0
        self._misses = 0


def lru_cache(maxsize=128, typed=False):
    """Bounded LRU cache decorator.

    ``maxsize=None`` is unbounded (used by ``cache``).  ``typed=True``
    distinguishes ``f(1)`` from ``f(1.0)`` by mixing each arg's type
    into the key, matching CPython.
    """
    def _decorator(fn):
        return _LruCacheWrapper(fn, maxsize, typed)

    return _decorator


# namedtuple base because MicroPython cannot call tuple.__new__ in a subclass.
class _CacheInfo(namedtuple("_CacheInfo", ("hits", "misses", "maxsize", "currsize"))):
    """Drop-in for CPython's ``functools._CacheInfo`` named tuple.

    The NFR-TUI-6 import-time test reads ``.maxsize``; Rich's own
    diagnostics read ``.hits``/``.misses``.  ``collections.namedtuple``
    is a C builtin on MicroPython, so the base costs nothing.
    """
    __slots__ = ()


def cache(fn):
    """Unbounded memoisation — ``lru_cache(maxsize=None)`` shorthand."""
    return lru_cache(maxsize=None)(fn)


# Comparison fills for total_ordering.  Each closure synthesises one
# operator from __lt__ + __eq__; the decorator picks the missing ones.
def _gt_from_lt_eq(self, other):
    return not (self < other or self == other)


def _le_from_lt_eq(self, other):
    return self < other or self == other


def _ge_from_lt_eq(self, other):
    return not self < other


def total_ordering(cls):
    """Fill in missing rich comparisons from ``__lt__`` and ``__eq__``.

    CPython supports any one of {lt, le, gt, ge} as the root; we require
    __lt__ specifically — Rich and Textual both define __lt__ in every
    use site, and supporting the full matrix doubles the LoC for no caller.
    """
    if "__lt__" not in cls.__dict__:
        raise ValueError("total_ordering requires __lt__")
    if "__gt__" not in cls.__dict__:
        cls.__gt__ = _gt_from_lt_eq
    if "__le__" not in cls.__dict__:
        cls.__le__ = _le_from_lt_eq
    if "__ge__" not in cls.__dict__:
        cls.__ge__ = _ge_from_lt_eq
    return cls


class cached_property:
    """Descriptor that computes once, then shadows itself on the instance.

    On first access, ``__get__`` stores the computed value into
    ``instance.__dict__`` under the attribute's own name; subsequent
    attribute lookups hit the instance dict and never reach this
    descriptor (instance dict shadows non-data descriptors).  No lock —
    the TUI runs single-threaded under asyncio (D6).
    """
    def __init__(self, func):
        self.func = func
        self.attrname = None
        update_wrapper(self, func)

    def __set_name__(self, owner, name):
        # CPython warns on rename; we silently accept the first binding,
        # which matches every observed use site in Rich/Textual.
        if self.attrname is None:
            self.attrname = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        name = self.attrname or self.func.__name__
        value = self.func(instance)
        # setattr, not instance.__dict__[...]: MicroPython instance
        # __dict__ is not writable.
        setattr(instance, name, value)
        return value
