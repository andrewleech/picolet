"""picolet_tui._textual.reactive — Reactive descriptor.

Replaces Textual's ``src/textual/reactive.py`` ``Reactive`` /
``reactive`` for the MicroPython subset.  The upstream descriptor binds
its owner name via ``__set_name__``; MicroPython does not invoke
``__set_name__`` on descriptors (research doc 01, "CPython-only
constructs"), so the binding has been moved into an explicit
``_bind_name(name, owner)`` call that the ``@widget`` class decorator
issues exactly once per descriptor at class-decoration time.

Spec coverage
-------------
* FR-TUI-19 — ``Reactive(default, *, layout=False, init=True,
  always_update=False)`` constructor surface.  ``@widget`` is the only
  caller of ``_bind_name``; the descriptor never relies on
  ``__set_name__``.
* FR-TUI-20 — ``watch_<name>`` is resolved at decoration time (against
  ``vars(owner)`` so we can read ``__code__.co_argcount`` without paying
  bound-method materialisation per assignment).  The recorded arity is
  used at ``__set__`` time to dispatch ``(self, new)`` vs
  ``(self, old, new)`` watchers.
* FR-TUI-21 — Reads of a name with a ``compute_<name>`` sibling short-
  circuit to the compute method; writes raise ``ReactiveError``.  The
  compute lookup lives in ``cls._tui_widget_meta["computes"]`` which
  ``@widget`` populates.
* FR-TUI-22 — ``layout=True`` triggers ``instance.refresh(layout=True)``
  on assignment; ``always_update=True`` fires the watcher even when the
  new value compares equal to the old one.
* FR-TUI-31 — Layout pass triggered through ``refresh(layout=True)``;
  the descriptor is the originating site for the "Reactive(..., layout=
  True) is assigned" trigger enumerated in the spec.
* FR-TUI-77 — ``ReactiveError`` is the framework exception class; this
  module imports it from ``picolet_tui.errors`` (which is itself part of
  the Phase 4b errors module).

Design-doc reference
--------------------
``docs/tui/textual-core-design.md`` §2 (lines 196-336).  This file
follows the pseudo-code there exactly except where noted below.

Module path: ``picolet_tui._textual.reactive``.
"""

# The errors module (picolet_tui.errors, FR-TUI-77) is a Phase 4b leaf
# scheduled alongside this descriptor; the ``ReactiveError`` class is
# expected to live there.  Until that module lands we fall back to a
# private subclass so this file remains importable in the isolated
# test_reactive.py bootstrap (test plan from the design doc: "Tested
# standalone in test_reactive.py against a hand-rolled bootstrap class
# that manually calls _bind_name.").  When the framework errors module
# is in place, the fallback path is dead code.
try:
    from picolet_tui.errors import ReactiveError
except ImportError:
    class ReactiveError(Exception):
        """Raised on misuse of a Reactive descriptor.

        Fallback used only when ``picolet_tui.errors`` has not been
        loaded yet.  See FR-TUI-77 for the canonical class.
        """


# Marker the @widget decorator uses to recognise descriptor instances
# (design doc §1.1 bucket 1).  isinstance(value, Reactive) is the only
# check, per design doc §2.1: "Reactive does not inherit from any ABC".


def _invoke_watcher(watch, *args):
    """Call a watcher.

    Coroutine watchers should be scheduled on the asyncio loop rather
    than awaited synchronously (design doc §2.3 footnote: "_invoke_watcher
    posts the call onto the asyncio loop if the watcher is async def").
    Detection routes through the picolet ``_callback`` shim per the
    design doc; that shim does not currently expose
    ``iscoroutinefunction``, so we sniff via the conventional MicroPython
    attribute ``__code__.co_flags & 0x100`` (CO_COROUTINE) and fall back
    to a sync call.  This keeps the leaf testable without a running
    event loop — the MessagePump integration in §3 takes over once that
    layer is wired.
    """
    # Recognise coroutine functions without inspect.iscoroutinefunction:
    # CO_COROUTINE = 0x100 in CPython and matches MicroPython's layout.
    code = getattr(watch, "__code__", None)
    if code is not None and (code.co_flags & 0x100):
        coro = watch(*args)
        try:
            import asyncio
        except ImportError:
            # Tests without an asyncio loop receive a "fire and forget"
            # contract: returning the coroutine object lets the test
            # close() it; the framework dispatch site replaces this path
            # with ``loop.create_task`` once MessagePump is wired in §3.
            return coro
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except (RuntimeError, AttributeError):
            loop = None
        if loop is not None and getattr(loop, "create_task", None) is not None:
            return loop.create_task(coro)
        return coro
    return watch(*args)


class Reactive:
    """A reactive attribute descriptor (FR-TUI-19..22).

    Constructor matches the upstream Textual ``Reactive`` keyword
    surface a v0.1 author actually uses:

        Reactive(default, *, layout=False, init=True, always_update=False)

    Drop-in differences from upstream:
      * ``init``, ``layout``, ``always_update`` are the only keywords
        carried over.  ``repaint``, ``bindings``, and ``recompose`` are
        omitted - they are not in the FR-TUI-19 surface and would only
        burden the NFR-TUI-19 frozen-bytes budget.
      * ``__set_name__`` is replaced by ``_bind_name`` (design doc §2.2);
        the ``@widget`` decorator is the sole caller.
      * ``Reactive`` does not inherit from any ABC; ``isinstance(value,
        Reactive)`` is the only check the decorator and Phase 4b code
        need (design doc §2.1).
    """

    def __init__(
        self,
        default,
        *,
        layout=False,
        init=True,
        always_update=False,
    ):
        self._default = default
        self._layout = layout
        self._init = init
        self._always_update = always_update
        # Set by _bind_name; left ``None`` to make "used before bind"
        # surface as a clean AttributeError rather than a stale string.
        self._attr_name = None
        self._private_name = None
        self._owner = None
        self._watch_name = None
        self._validate_name = None
        # Arity 2 means (self, new); arity 3 means (self, old, new).
        # Recorded at decoration time so per-write cost is one int
        # compare rather than per-write ``__code__.co_argcount`` access.
        self._watch_arity = None

    # ------------------------------------------------------------------ bind

    def _bind_name(self, name, owner):
        """Bind this descriptor to ``owner.<name>``.

        Called by ``@widget`` exactly once per ``Reactive`` instance on
        each owning class (design doc §1.1 bucket 1, §2.2).  Replaces
        the descriptor protocol's ``__set_name__`` hook that MicroPython
        does not invoke.

        ``watch_<name>`` / ``validate_<name>`` sibling methods are
        resolved against ``vars(owner)`` (not ``getattr(owner, ...)``)
        so the arity introspection sees the raw function object with
        ``__code__`` rather than a bound descriptor.
        """
        self._attr_name = name
        # Private slot lives in instance ``__dict__`` so each instance
        # carries its own backing store - see design doc §2 head note:
        # "Use vars(instance) for backing storage to avoid
        # weakref-of-instance."
        self._private_name = "_reactive_" + name
        self._owner = owner
        watch = vars(owner).get("watch_" + name)
        if watch is not None:
            self._watch_name = "watch_" + name
            # __code__.co_argcount includes ``self``; arity 2 means
            # (self, new) and arity 3 means (self, old, new), per
            # FR-TUI-20.  No support for *args / **kwargs watchers in
            # v0.1 - the spec table fixes the two shapes.
            self._watch_arity = watch.__code__.co_argcount
        validate = vars(owner).get("validate_" + name)
        if validate is not None:
            self._validate_name = "validate_" + name

    # ------------------------------------------------------- descriptor slots

    def __get__(self, instance, owner):
        """Return the current value.

        Order of resolution (FR-TUI-21):
          1. Class access (``instance is None``) returns the descriptor
             itself - matches the descriptor-protocol convention; lets
             tests and ``@widget`` introspect the bound metadata.
          2. If ``compute_<name>`` is registered on the owning class,
             call it and return its value (computed reactives are read-
             through, never cached on the read path).
          3. Otherwise return the per-instance private slot, or the
             descriptor default if the slot has not been written yet.
        """
        if instance is None:
            return self
        # Computed reactives short-circuit reads; FR-TUI-21.  The
        # ``_tui_widget_meta`` dict is populated by ``@widget``; an
        # unregistered class is a "missing decorator" path covered by
        # FR-TUI-28 and the @widget decorator itself (not here).
        meta = getattr(type(instance), "_tui_widget_meta", None)
        if meta is not None:
            compute = meta["computes"].get(self._attr_name)
            if compute is not None:
                return compute(instance)
        try:
            return instance.__dict__[self._private_name]
        except KeyError:
            return self._default

    def __set__(self, instance, new):
        """Assign a new value.

        Order of operations on a non-computed reactive (design doc §2.3,
        FR-TUI-19..22):

          1. Reject writes to a computed name (FR-TUI-21).
          2. Read the *previous* value from the private slot (or the
             descriptor default if never set).
          3. Run ``validate_<name>(self, new)`` if present; the return
             value replaces ``new`` and feeds the comparison.
          4. If ``always_update`` is False and ``old == new``, return
             without firing watchers or refreshing (FR-TUI-22).
          5. Store the validated value in the private slot.
          6. Fire ``watch_<name>`` with the arity recorded at
             ``_bind_name`` time (FR-TUI-20).
          7. Fire any extra watchers registered via
             ``Reactive.watch(instance, callback)``; same arity
             contract.
          8. Refresh the owning widget: ``layout=True`` flag triggers a
             layout pass (FR-TUI-31), otherwise a paint-only refresh.
             ``refresh`` is best-effort: a hand-rolled bootstrap class
             without a ``refresh`` method (the §2 test plan) skips the
             call silently.

        The interleaving "store, then notify, then refresh" matches the
        upstream Textual semantics: a watcher that reads its own
        reactive sees the *new* value (instance dict already updated)
        rather than recursing through the descriptor and hitting the
        old one.
        """
        meta = getattr(type(instance), "_tui_widget_meta", None)
        if meta is not None and self._attr_name in meta["computes"]:
            # FR-TUI-21: writing to a computed reactive is a runtime
            # error.  The class-time mutual-exclusion check
            # (TooManyComputesError) lives in @widget; this branch
            # catches "compute_<name> was added after the descriptor
            # was bound" and other late paths.
            raise ReactiveError(
                "Cannot assign to computed reactive %r on %s"
                % (self._attr_name, type(instance).__name__)
            )
        old = instance.__dict__.get(self._private_name, self._default)
        if self._validate_name is not None:
            new = getattr(instance, self._validate_name)(new)
        if (not self._always_update) and old == new:
            # FR-TUI-22: ``always_update=True`` fires the watcher even
            # when unchanged.  The early-out elides both watcher and
            # refresh - matches upstream Textual.
            return
        instance.__dict__[self._private_name] = new
        # Primary watcher (declared on the class as ``watch_<name>``).
        if self._watch_name is not None:
            watch = getattr(instance, self._watch_name)
            if self._watch_arity == 2:
                _invoke_watcher(watch, new)
            else:
                _invoke_watcher(watch, old, new)
        # Extra per-instance watchers registered via ``Reactive.watch``.
        # Stored on the instance dict so they live and die with the
        # instance - matches the "no weakref-of-instance" rule in §2.
        extra = instance.__dict__.get(self._private_name + "_extra_watchers")
        if extra:
            for cb in extra:
                # Extra watchers follow the same arity convention as the
                # primary; we sniff each callback because they are
                # registered post-decoration and not subject to the
                # class-time arity scan.
                arity = _count_args_excluding_self(cb)
                if arity == 1:
                    _invoke_watcher(cb, new)
                else:
                    _invoke_watcher(cb, old, new)
        refresh = getattr(instance, "refresh", None)
        if refresh is not None:
            if self._layout:
                # FR-TUI-31: layout=True schedules a layout pass.
                refresh(layout=True)
            else:
                refresh()

    # --------------------------------------- public extension API for tests

    def watch(self, instance, callback):
        """Register an extra watcher on a specific instance.

        Design doc §2.4: stores the callback in a per-instance list on
        the instance ``__dict__``.  Callbacks are invoked after the
        primary ``watch_<name>`` method on every effective assignment.
        Arity is determined per-call (the registration site does not
        require a class-decoration pass), so both ``cb(new)`` and
        ``cb(old, new)`` shapes are accepted.
        """
        key = self._private_name + "_extra_watchers"
        instance.__dict__.setdefault(key, []).append(callback)

    def compute(self, instance):
        """Force a recompute for this descriptor on ``instance``.

        Design doc §2.4: if a ``compute_<name>`` method is registered,
        call it, cache the value in the private slot for symmetry with
        ``__set__``, and return the value.  Otherwise behaves like a
        normal read of the descriptor.  Provided for tests and Phase 4b
        widgets that wire reactives dynamically.
        """
        meta = getattr(type(instance), "_tui_widget_meta", None)
        if meta is None:
            return self.__get__(instance, type(instance))
        compute = meta["computes"].get(self._attr_name)
        if compute is None:
            return self.__get__(instance, type(instance))
        value = compute(instance)
        instance.__dict__[self._private_name] = value
        return value


def _count_args_excluding_self(fn):
    """Positional arity, sans self / bound first arg.

    Local to this module to avoid a circular import on the ``_shims``
    package during early bootstrap; functionally identical to
    ``picolet_tui._shims.callback.count_parameters`` for the shapes the
    extra-watcher path can encounter (plain function, bound method,
    lambda).  Builtins are not part of the registration contract.
    """
    # Bound methods: __self__ has already consumed the leading slot.
    if hasattr(fn, "__func__"):
        return fn.__func__.__code__.co_argcount - 1
    code = getattr(fn, "__code__", None)
    if code is not None:
        # Plain function / lambda - no implicit self, all positional
        # arguments are user-visible.
        return code.co_argcount
    # Conservative fallback: treat as (new) so callbacks fire with the
    # new value rather than being silently dropped.
    return 1
