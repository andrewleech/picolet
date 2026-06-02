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

from collections import OrderedDict

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


def lru_cache(maxsize=128, typed=False):
    """Bounded LRU cache decorator.

    ``maxsize=None`` is unbounded (used by ``cache``).  ``typed=True``
    distinguishes ``f(1)`` from ``f(1.0)`` by mixing each arg's type
    into the key, matching CPython.
    """
    def _decorator(fn):
        cache = OrderedDict()
        hits = 0
        misses = 0

        def _make_key(args, kwargs):
            if kwargs:
                key = args + (_MISS,) + tuple(sorted(kwargs.items()))
            else:
                key = args
            if typed:
                key = key + tuple(type(a) for a in args)
                if kwargs:
                    key = key + tuple(type(v) for v in kwargs.values())
            return key

        def _wrapper(*args, **kwargs):
            nonlocal hits, misses
            key = _make_key(args, kwargs)
            value = cache.get(key, _MISS)
            if value is not _MISS:
                # Manual move-to-end: OrderedDict.move_to_end exists on
                # MicroPython's collections.OrderedDict, but using
                # pop+reinsert keeps us portable to any dict-like with
                # insertion-order iteration.
                del cache[key]
                cache[key] = value
                hits += 1
                return value
            value = fn(*args, **kwargs)
            cache[key] = value
            misses += 1
            if maxsize is not None and len(cache) > maxsize:
                # popitem(last=False) → evict the LRU entry.  Falls back
                # to a manual iter-and-pop on dicts lacking the kwarg.
                try:
                    cache.popitem(last=False)
                except TypeError:
                    oldest = next(iter(cache))
                    del cache[oldest]
            return value

        def cache_info():
            return _CacheInfo(hits, misses, maxsize, len(cache))

        def cache_clear():
            nonlocal hits, misses
            cache.clear()
            hits = 0
            misses = 0

        _wrapper.cache_info = cache_info
        _wrapper.cache_clear = cache_clear
        update_wrapper(_wrapper, fn)
        return _wrapper

    return _decorator


class _CacheInfo(tuple):
    """Drop-in for CPython's ``functools._CacheInfo`` named tuple.

    The NFR-TUI-6 import-time test reads ``.maxsize``; Rich's own
    diagnostics read ``.hits``/``.misses``.  Subclassing tuple keeps
    pickling/repr cheap without depending on collections.namedtuple.
    """
    __slots__ = ()

    def __new__(cls, hits, misses, maxsize, currsize):
        return tuple.__new__(cls, (hits, misses, maxsize, currsize))

    @property
    def hits(self):
        return self[0]

    @property
    def misses(self):
        return self[1]

    @property
    def maxsize(self):
        return self[2]

    @property
    def currsize(self):
        return self[3]


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
        instance.__dict__[name] = value
        return value
