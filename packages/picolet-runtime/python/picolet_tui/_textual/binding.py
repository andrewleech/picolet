"""picolet_tui._textual.binding - Binding value type.

Implements the ``Binding`` dataclass-like value type and its ``_coerce``
shorthand normaliser per design doc §6.1.  The type is a leaf in the
Phase 4b dependency graph: no Widget, no MessagePump, no App.  Only the
``@widget`` decorator (agent 4b/02) and the future ``_dispatch_key``
(step 10) read from instances of this class.

Spec coverage:
  * FR-TUI-27 - ``BINDINGS = [Binding("ctrl+q", "quit", "Quit")]`` class
    attribute consumed by ``@widget``'s bucket-5 walk.
  * Design doc §6.1 - shorthand 2-/3-tuple acceptance for ``BINDINGS``
    entries (``("h", "show_help")`` is sugar for
    ``Binding("h", "show_help")``).
  * Design doc §6.2 - the merge algorithm in ``@widget`` consumes
    ``Binding.key`` as the dedup key; nothing else on the instance
    participates in merge.

What this module deliberately does NOT do:
  * Key dispatch.  ``binding.matches(key_event)`` belongs to Phase 4b
    step 10 (design doc §6.3) - the matching rule depends on the
    KEY_ALIASES table in ``keys`` and on the Key event shape that step 7
    finalises.  Keeping match logic out of this file lets step 10 land
    without forcing a revision to the value type's surface.
  * Merging.  ``_merge_bindings`` (§6.2) lives inside ``@widget`` (agent
    4b/02) because the merge mutates the meta dict the decorator owns.
    Putting the merge here would create a circular import between
    ``binding`` and the decorator module.

MicroPython adjustments (from upstream Textual ``binding.py``):
  * ``@dataclass(frozen=True)`` -> ``@dataclass`` with ``field(...)``
    sentinels and no ``frozen=True``.  Frozen would prevent the
    decoration-time mutation that ``_coerce`` does on the way in (the
    coercer constructs fresh instances rather than mutating, so freezing
    would technically be safe, but the picolet ``_shims.dataclasses``
    pays an extra ``object.__setattr__`` per field assignment when
    ``frozen=True`` is set - and the Binding is hot on the decoration
    path).  Bindings are conventionally treated as immutable by the
    framework; nothing in the keep-list mutates a Binding instance
    after construction.
  * Annotations dropped per the dataclass shim's MicroPython contract:
    field names are declared via ``name = field(default=...)`` rather
    than ``name: str = field(default=...)``.  CPython sees the
    annotations through normal class-body scoping; MicroPython's
    compiler drops them, and the shim picks up the bare ``field()``
    sentinels regardless.

Design-doc references (textual-core-design.md):
  * §6.1 - BINDINGS class attribute + shorthand acceptance.
  * §6.2 - the merge algorithm that consumes ``Binding.key``.
  * §6.3 - dispatch (step 10, not implemented here).
"""

from picolet_tui._shims.dataclasses import dataclass, field


# The dataclass is plain (not frozen) - see module docstring.  ``eq=True``
# is implicit; equality matters for the ``_merge_bindings`` test in
# ``test_bindings.py::test_merged_bindings`` which compares the merged
# list to an expected list of Binding instances.
@dataclass
class Binding:
    """A single key->action mapping declared on a Widget's BINDINGS list.

    Fields:
      * ``key`` - the key string from the ``keys`` table
        (e.g. ``"ctrl+q"``, ``"f1"``, ``"tab"``).  Resolved against the
        event ``.key`` value by the dispatcher in step 10; not validated
        here because validation requires the ``KEY_ALIASES`` table and a
        live Key event class to compare against.
      * ``action`` - the action name without the ``action_`` prefix.  The
        dispatcher (§6.3) does ``getattr(node, "action_" + action)``;
        keeping the prefix off the stored string saves one slice per
        dispatch and matches upstream Textual's convention.
      * ``description`` - human-readable footer string.  Empty string for
        unfooter'd bindings (Textual's convention: the footer widget
        hides any Binding whose ``description`` is empty).
      * ``show`` - whether the footer widget should display this binding.
        Distinct from ``description``: a binding can have a description
        but be hidden (``show=False``) because the surrounding widget
        renders the cue elsewhere.
      * ``key_display`` - optional override for the footer's key glyph.
        When ``None`` the footer calls ``keys.format_key(self.key)`` to
        produce ``↑`` / ``esc`` style glyphs.  Setting this to a string
        bypasses ``format_key`` entirely.
      * ``priority`` - if True, this binding fires before children get a
        crack at the key.  The §6.3 dispatcher walks priority bindings
        on the App and Screen levels first, then the normal
        focused-to-root walk for non-priority entries.  Step 10 wires
        the ordering; this module just stores the flag.
    """

    # Field order matters: positional construction ``Binding("d",
    # "toggle_dark", "Toggle dark")`` must bind to (key, action,
    # description).  The shim's ``_make_init`` walks the field list in
    # declaration order (dataclasses.py:266); see that file's MP
    # ordering note.
    key = field()
    action = field()
    description = field(default="")
    show = field(default=True)
    key_display = field(default=None)
    priority = field(default=False)

    @classmethod
    def _coerce(cls, value):
        """Normalise a BINDINGS entry to a Binding instance.

        Accepted shapes (design doc §6.1):
          * a ``Binding`` -> returned unchanged (identity, not a copy -
            Bindings are conventionally immutable, sharing is safe).
          * a 2-tuple ``(key, action)`` -> ``Binding(key, action)``.
          * a 3-tuple ``(key, action, description)`` ->
            ``Binding(key, action, description)``.
          * a dict -> ``Binding(**dict)``.  Unknown keys raise via the
            dataclass ``__init__``'s ``unexpected keyword argument`` path.

        Any other shape raises ``TypeError`` with the offending value in
        the message - the BINDINGS list is parsed at class-decoration
        time, so a bad entry surfaces at import rather than at runtime.
        Errors happening at module load time are debuggable in a way
        runtime KeyErrors during dispatch are not.
        """
        if isinstance(value, cls):
            return value
        # The tuple branch comes before the list branch because tuples
        # are the documented shorthand; lists are tolerated for
        # symmetry but the design doc only specifies tuples.  Both go
        # through the same length-dispatch logic.
        if isinstance(value, (tuple, list)):
            n = len(value)
            if n == 2:
                key, action = value
                return cls(key, action)
            if n == 3:
                key, action, description = value
                return cls(key, action, description)
            raise TypeError(
                "Binding shorthand tuple must be length 2 or 3, "
                "got length %d: %r" % (n, value)
            )
        if isinstance(value, dict):
            # Pass through as kwargs - this lets callers use the keyword
            # form for the optional flags (``{"key": "d", "action":
            # "toggle_dark", "priority": True}``) without writing out a
            # 6-tuple.  Unknown keys raise via the dataclass __init__,
            # which surfaces the bad name in the TypeError.
            return cls(**value)
        raise TypeError(
            "BINDINGS entry must be a Binding, tuple, list, or dict; "
            "got %r" % (value,)
        )
