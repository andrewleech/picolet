"""picolet_tui._shims.weakref — strong-reference stand-in for CPython weakref.

************************************************************************
*  WARNING: THIS IS NOT REAL WEAKREF.                                   *
*                                                                       *
*  Every container here holds STRONG references to its members.  The   *
*  CPython contract — entries vanish when the last strong reference     *
*  outside the container is dropped — does NOT hold.  Do not rely on    *
*  garbage collection to clear these containers.  Call ``discard()`` /  *
*  ``clear()`` / ``finalize.detach()`` explicitly from the owner's      *
*  ``_dispose()`` hook (synthesis decision D7, FR-TUI-26).              *
*                                                                       *
*  ``ref()`` and ``proxy()`` are identity passthroughs; the ``ref``     *
*  callback never fires because there is no weakness to trigger it.    *
************************************************************************

What this shim provides (the API surface Textual/Rich import):
  - WeakSet                — dict-backed, id-keyed, strong refs
  - WeakValueDictionary    — dict-backed, key=user-key, value=strong ref
  - WeakKeyDictionary      — dict-backed, key=id(user-key), value=(key,val)
  - ref(obj, callback=None) — returns a zero-arg callable yielding obj
  - proxy(obj)             — returns obj unchanged
  - finalize(obj, fn, *a, **kw) — fires fn on .detach() / explicit
                                  .__call__(); no auto-fire on GC

What this shim deliberately does NOT do:
  - Weak references.  MicroPython core exposes ``weakref.ref`` only when
    ``MICROPY_PY_WEAKREF=1`` (NFR-TUI-9) and the unix-port object model
    does not currently feed finalisation callbacks back into Python in
    a way that lets us implement ``WeakSet`` faithfully.  Rather than
    pretend, we keep references strong and force the Textual port (D7)
    to expose explicit dispose hooks where parent->child cycles would
    have leaked.
  - ``WeakMethod``.  Not used by the Textual subset in scope (synthesis
    §6, decision list).
  - Callback fan-out on ``ref``.  CPython's ``ref`` callback fires when
    the referent is collected; with strong refs there is nothing to
    fire on.  We accept the argument to keep the signature compatible
    and discard it.

Spec coverage:
  FR-TUI-23..28  — Widget tree lifecycle; uses WeakSet for child
                   registration in the upstream Textual code.
  NFR-TUI-9      — Documents the weakref build flag; this shim renders
                   that flag irrelevant for the in-tree imports, since
                   we never call into core ``weakref``.
  NFR-TUI-19     — Counts against the 20 KiB ``_shims`` romfs budget;
                   target ~150 LoC.

Synthesis cross-reference: 00-synthesis.md §"Decisions" D7 mandates
that Textual's ``weakref.ref`` parent pointers are replaced with
explicit ``_dispose()`` hooks on ``MessagePump`` — i.e. the porting
work upstream of this shim guarantees we do not need real weakness.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

class WeakSet:
    """Set-like container; STRONG refs.  Identity-keyed via ``id()``.

    Identity keying matches CPython's WeakSet semantics for ``in`` checks
    against unhashable mutables, which Textual relies on for tracking
    mounted Widget instances.
    """

    def __init__(self, iterable=None):
        self._items = {}  # id(obj) -> obj
        if iterable is not None:
            for obj in iterable:
                self.add(obj)

    def add(self, obj):
        self._items[id(obj)] = obj

    def discard(self, obj):
        # Caller is responsible for invoking this from the owner's
        # _dispose() — there is no GC hook to do it implicitly.
        self._items.pop(id(obj), None)

    def remove(self, obj):
        del self._items[id(obj)]

    def clear(self):
        self._items.clear()

    def __contains__(self, obj):
        return id(obj) in self._items

    def __iter__(self):
        # Snapshot to tolerate mutation during iteration, which Textual
        # does in its message-pump teardown loop.
        return iter(list(self._items.values()))

    def __len__(self):
        return len(self._items)


class WeakValueDictionary:
    """Mapping with STRONG value refs.  Caller must ``del`` entries
    explicitly; nothing here will prune on GC."""

    def __init__(self, *args, **kwargs):
        self._data = dict(*args, **kwargs)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def __delitem__(self, key):
        del self._data[key]

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def pop(self, key, *args):
        return self._data.pop(key, *args)

    def clear(self):
        self._data.clear()

    def keys(self):
        return list(self._data.keys())

    def values(self):
        return list(self._data.values())

    def items(self):
        return list(self._data.items())


class WeakKeyDictionary:
    """Mapping keyed by object identity; STRONG refs on both sides.

    CPython keys this by weak reference to the key object; we key by
    ``id(key)`` and keep the original key alongside the value so the
    iteration protocol can return the user's key object (not the int
    id) — Textual's CSS-selector cache iterates keys to invalidate
    entries when a Screen is dismissed.
    """

    def __init__(self):
        self._data = {}  # id(key) -> (key, value)

    def __getitem__(self, key):
        return self._data[id(key)][1]

    def __setitem__(self, key, value):
        self._data[id(key)] = (key, value)

    def __delitem__(self, key):
        del self._data[id(key)]

    def __contains__(self, key):
        return id(key) in self._data

    def __iter__(self):
        return iter(k for k, _v in self._data.values())

    def __len__(self):
        return len(self._data)

    def get(self, key, default=None):
        slot = self._data.get(id(key))
        return slot[1] if slot is not None else default

    def pop(self, key, *args):
        slot = self._data.pop(id(key), None)
        if slot is not None:
            return slot[1]
        if args:
            return args[0]
        raise KeyError(key)

    def clear(self):
        self._data.clear()


# ---------------------------------------------------------------------------
# ref / proxy / finalize
# ---------------------------------------------------------------------------

def ref(obj, callback=None):
    """Return a zero-arg callable that yields ``obj``.

    The ``callback`` is accepted for signature compatibility and
    discarded — there is no weakness, so there is no death event to
    fire it on.
    """
    # Closure-over-obj keeps the reference strong by construction; this
    # is the whole point of the fallback.
    def _ref():
        return obj
    return _ref


def proxy(obj, callback=None):
    """Identity passthrough.

    Real ``proxy`` rebinds every attribute / dunder through a wrapper
    that nulls out once the referent dies; with strong refs there is
    nothing to null and no benefit to wrapping, so we hand back the
    original object."""
    return obj


class finalize:
    """Run ``fn(*args, **kwargs)`` on explicit ``.detach()`` / call.

    Unlike CPython's ``weakref.finalize``, this does NOT fire on GC of
    the referent — MicroPython has no equivalent of CPython's tp_finalize
    that we can hook safely from Python.  The Textual port (synthesis
    D7) calls finalisers from ``MessagePump._dispose()`` instead, so
    the GC-driven path is not load-bearing here.
    """

    def __init__(self, obj, fn, *args, **kwargs):
        # Referent kept strong on purpose so callers can still reach it
        # before they tear the finalizer down.
        self._obj = obj
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._alive = True

    def __call__(self):
        # Idempotent: re-firing is a no-op so callers can safely include
        # finalize() invocation in both an explicit dispose hook and a
        # belt-and-braces shutdown sweep.
        if not self._alive:
            return None
        self._alive = False
        try:
            return self._fn(*self._args, **self._kwargs)
        finally:
            self._obj = None
            self._fn = None
            self._args = ()
            self._kwargs = {}

    def detach(self):
        """Cancel without firing; returns the original (obj, fn, args, kwargs)
        on first call, ``None`` thereafter — matches CPython's signature
        closely enough for the Textual call sites that use it."""
        if not self._alive:
            return None
        self._alive = False
        result = (self._obj, self._fn, self._args, self._kwargs)
        self._obj = None
        self._fn = None
        self._args = ()
        self._kwargs = {}
        return result

    @property
    def alive(self):
        return self._alive


__all__ = (
    "WeakSet",
    "WeakValueDictionary",
    "WeakKeyDictionary",
    "ref",
    "proxy",
    "finalize",
)
