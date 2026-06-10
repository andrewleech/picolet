"""@widget class decorator + @on handler decorator.

Implements the single class-time introspection pass that replaces
upstream Textual's `_MessagePumpMeta`, `DOMNode.__init_subclass__`,
`Message.__init_subclass__`, and `Reactive.__set_name__`.  MicroPython
honours none of those CPython hooks (synthesis D1, design doc §1).

The decorator walks `vars(cls)` exactly once, classifies each attribute
into one of six buckets (reactives, computes, decorated handlers, name
handlers, bindings, ignored CSS), merges the parent meta along the MRO
with subclass-wins semantics, then stores the union at
`cls._tui_widget_meta`.  Cost is O(len(class body)).

`MissingWidgetDecoratorError` fires from two sites — `Widget.__init__`
on first instantiation, and `@widget` itself when a base class in the
MRO declares capturable artifacts but lacks `_tui_widget_meta`
(FR-TUI-28 R3 mitigation).

Public surface (exported through `picolet_tui` and re-exported through
`picolet_tui.errors`):

  widget(cls)                       — the class decorator
  on(message_type, selector=None)   — handler decorator (FR-TUI-13)
  MissingWidgetDecoratorError       — R3 / FR-TUI-28
  TooManyComputesError              — FR-TUI-21 collision
  PicoletTuiError                   — base; placeholder until
                                      picolet_tui.errors lands

The module deliberately defines the exception classes itself rather
than importing from `picolet_tui.errors`.  The `errors` module is a
Phase 4b integration-layer target (agent 6+); landing the decorator
first with a forward-declared base lets agents 1-5 ship in parallel
without an import cycle.  The integration agent re-exports these
names from `picolet_tui.errors` once that module exists.
"""

# Reactive is imported lazily inside widget() to avoid a hard
# import-time dependency on the sibling module's file name.  Phase 4b
# agent 1 picks the Reactive file path; this decorator only needs
# `isinstance(value, Reactive)` at decoration time, not at import
# time.  See _resolve_reactive_class below.

from .case import camel_to_snake


# ---------------------------------------------------------------------
# Exception hierarchy (FR-TUI-77).
#
# These live here as a forward-declared subset of picolet_tui.errors.
# When the integration agent lands picolet_tui.errors it should:
#   from picolet_tui._textual._widget_decorator import (
#       PicoletTuiError, MissingWidgetDecoratorError, TooManyComputesError,
#   )
# and re-export them.  Existing references inside _textual continue to
# work because the class objects are the same.
# ---------------------------------------------------------------------


class PicoletTuiError(Exception):
    """Base for all picolet-tui framework errors (FR-TUI-77)."""


class TooManyComputesError(PicoletTuiError):
    """Raised at @widget decoration time when a class declares both
    `compute_<name>` and `Reactive(<name>)` for the same name
    (FR-TUI-21).  The two halves are mutually exclusive: a reactive
    backed by a `compute_*` is a computed reactive, and computed
    reactives cannot be assigned.
    """


class MissingWidgetDecoratorError(PicoletTuiError):
    """Raised when a Widget subclass was not decorated with @widget.

    Fires from two sites:
      1. `Widget.__init__` (and Screen/App) on first instantiation —
         the R3 runtime guard (synthesis §3 risk register).
      2. `@widget` itself when a class in the MRO declares any of
         {Reactive, compute_*, @on-decorated, BINDINGS} but lacks
         `_tui_widget_meta` (FR-TUI-28 — prevents silent metadata loss
         from intermediate undecorated mixins).
    """

    def __init__(self, missing_cls, raised_from=None):
        # Store both classes as attributes for programmatic inspection.
        # `missing_cls` is the class that should have been decorated;
        # `raised_from` is the subclass whose decoration triggered the
        # MRO scan (None for the runtime-guard case).
        self.missing_cls = missing_cls
        self.raised_from = raised_from
        Exception.__init__(self, self._build_message())

    def _build_message(self):
        # The message text is locked by design doc §9.  Two variants:
        # FR-TUI-28 (MRO scan) adds the trailing "detected while
        # decorating subclass" sentence; the runtime-guard variant
        # omits it.
        name = getattr(self.missing_cls, "__name__", repr(self.missing_cls))
        msg = (
            "class %s is missing @widget.\n"
            "\n"
            "The class\n"
            "    %s\n"
            "extends Widget but was not decorated with @widget, so its "
            "reactives,\n"
            "@on-decorated handlers, BINDINGS, and compute_<name> methods are\n"
            "unwired.  MicroPython does not invoke __init_subclass__ or\n"
            "metaclasses, so the framework relies on the @widget decorator to\n"
            "populate cls._tui_widget_meta exactly once at class-decoration "
            "time.\n"
            "\n"
            "Fix:\n"
            "\n"
            "    from picolet_tui import widget\n"
            "\n"
            "    @widget\n"
            "    class %s(Screen):\n"
            "        ...\n"
            "\n"
            "See docs/tui/authoring-widgets.md, section "
            "\"The @widget decorator\"."
        ) % (name, name, name)
        if self.raised_from is not None:
            sub = getattr(
                self.raised_from, "__name__", repr(self.raised_from)
            )
            msg = msg + (
                "\n\nThis was detected while decorating subclass %s." % sub
            )
        return msg


# ---------------------------------------------------------------------
# @on handler decorator (FR-TUI-13).
#
# `@on(MessageType, selector=None)` annotates a method with a list of
# `_Selector` records under `fn._tui_on`.  The @widget pass reads
# `fn._tui_on` and appends one (method, selector) tuple per selector
# into `meta["handlers"][selector.message_type]`.
#
# Stacking `@on` decorators is supported — a single method may handle
# multiple message types, or the same type via different selectors:
#
#     @on(Button.Pressed, selector="#save")
#     @on(Button.Pressed, selector="#load")
#     def either(self, event): ...
# ---------------------------------------------------------------------


class _OnSelector:
    """One entry in a method's `_tui_on` list.

    A standalone value class (rather than a tuple) so the dispatch
    side (§3.4) can call `selector.matches(node, message)` polymorphically
    once richer selectors (CSS-style ids/classes) land in v0.2.  For
    v0.1 the selector is an opaque token forwarded verbatim; D2 defers
    CSS-style selector parsing to v0.2.
    """

    def __init__(self, message_type, selector):
        self.message_type = message_type
        self.selector = selector

    def matches(self, node, message):
        # v0.1 has no CSS selector engine; a None selector matches
        # any node, and a non-None selector matches only when
        # node.id equals the selector string (the only form the v0.1
        # widgets use).  Phase 5 widget tests will pin this contract.
        if self.selector is None:
            return True
        # Resolve `#id` shorthand if present; otherwise compare raw.
        sel = self.selector
        if isinstance(sel, str) and sel.startswith("#"):
            return getattr(node, "id", None) == sel[1:]
        return getattr(node, "id", None) == sel


def on(message_type, selector=None):
    """Decorator that marks a method as a handler for `message_type`.

    Equivalent to upstream Textual's `textual.on`.  The picolet
    implementation does no class-time registration here — that is the
    @widget decorator's job (FR-TUI-13).  This decorator only records
    (fn, selector) in a module-level pending registry that @widget
    consumes during its class-dict walk.  A registry rather than a
    `fn._tui_on` attribute because MicroPython rejects attribute
    assignment on function objects.

    Args:
        message_type: a `Message` subclass.  Identity-compared at
            dispatch time, so the same class object must reach both
            sites.
        selector: optional selector token forwarded to `_OnSelector`.
            v0.1 accepts `None` (match any) or `"#id"` / `"id"`
            (match by `node.id`).  Full CSS selectors are D2 / v0.2.

    Returns:
        A decorator that returns the wrapped function unchanged after
        recording one `_OnSelector` against it in the pending registry.
    """

    sel_record = _OnSelector(message_type, selector)

    def _decorator(fn):
        # Stacking @on decorators appends additional records for the
        # same function object.  This matches upstream Textual
        # semantics and is the only way to register a single method
        # against multiple message types or selectors.
        _PENDING_ON.append((fn, sel_record))
        return fn

    return _decorator


# (fn, _OnSelector) records appended by @on and consumed (by function
# identity) by @widget's class walk.  Entries left behind belong to
# @on-decorated methods on classes never passed through @widget — the
# FR-TUI-28 / R3 authoring error, caught by the runtime guard.
_PENDING_ON = []


def _take_pending_on(fn):
    """Pop and return every pending _OnSelector recorded for ``fn``."""
    taken = []
    kept = []
    for record in _PENDING_ON:
        if record[0] is fn:
            taken.append(record[1])
        else:
            kept.append(record)
    if taken:
        _PENDING_ON[:] = kept
    return taken


# ---------------------------------------------------------------------
# Reactive isinstance resolver.
#
# `Reactive` is a sibling module (`_textual._reactive`).  Importing it
# at the top of this file would couple the load order of two Phase 4b
# parallel agents; doing it inside `widget()` defers the lookup until
# user code actually decorates a class, by which point both modules
# are loaded.  Cost: one module-level dict access per decoration.
# ---------------------------------------------------------------------


_REACTIVE_CLS = None  # cached after first widget() call.


def _resolve_reactive_class():
    global _REACTIVE_CLS
    if _REACTIVE_CLS is not None:
        return _REACTIVE_CLS
    try:
        # The canonical location.  If Phase 4b agent 1 picks a
        # different file name, the import here is the single point of
        # change.  Agent 1 shipped the descriptor at `reactive.py`
        # (no leading underscore); the original placeholder name
        # `_reactive` is the historic value left in this comment for
        # archaeological context.
        from . import reactive as _reactive_mod
        _REACTIVE_CLS = _reactive_mod.Reactive
    except ImportError:
        # Phase 4b agent 1 not yet landed: fall back to a sentinel that
        # `isinstance` rejects.  This lets decorator unit tests run on
        # classes that declare no reactives.
        _REACTIVE_CLS = _NoReactive
    return _REACTIVE_CLS


class _NoReactive:
    """Sentinel placeholder when `_reactive` is not importable.

    `isinstance(x, _NoReactive)` is always False because user code
    never constructs this class.  Kept as a class object (not None)
    so `isinstance(value, _resolve_reactive_class())` is a single,
    unconditional call site.
    """


# ---------------------------------------------------------------------
# The @widget decorator.
# ---------------------------------------------------------------------


def widget(cls):
    """Decorate a class as a picolet-tui widget.

    Performs the single class-time introspection pass that wires up
    reactives, computed reactives, @on handlers, name-dispatched
    `on_<event>` handlers, and BINDINGS.  See design doc §1.

    The decorator is idempotent in the sense that decorating the same
    class twice is harmless — the second pass simply re-builds the
    same meta dict.  It is **not** safe to call twice with different
    class bodies; user code should decorate once at class definition.

    Raises:
        TypeError: `cls` is not a class.
        TooManyComputesError: a name is declared as both `Reactive`
            and `compute_<name>` (FR-TUI-21).
        MissingWidgetDecoratorError: a base class in the MRO declares
            capturable artifacts but lacks `_tui_widget_meta`
            (FR-TUI-28 / R3).
    """
    # Defensive: catch authoring mistakes like `@widget` on a function
    # or `@widget()` (call instead of pass).  isinstance(cls, type) is
    # MicroPython-portable; `inspect.isclass` is not in the shim.
    if not isinstance(cls, type):
        raise TypeError(
            "@widget must decorate a class, got %r" % type(cls)
        )

    Reactive = _resolve_reactive_class()

    meta = {
        # name -> Reactive descriptor (own only at this point).
        "reactives": {},
        # name -> bound compute_<name> method (function on the class).
        "computes": {},
        # type[Message] -> list[(method, _OnSelector)].
        "handlers": {},
        # method-name (e.g. "on_button_pressed") -> (method, arity).
        # Arity includes `self`; 1 means (self,), 2 means (self, msg).
        "name_handlers_by_name": {},
        # list[Binding].  Order matters: subclass-declared come last
        # so last-match-wins at key-dispatch time (§6.3).
        "bindings": [],
    }

    # Single pass over cls.__dict__ (vars() does not exist on
    # MicroPython).  No dir(), no MRO walk here — that is
    # _merge_parent_meta's job.  No descriptor wakes, no
    # getattr-on-class.  NB: iteration order is arbitrary on
    # MicroPython; nothing below may depend on declaration order.
    for name, value in cls.__dict__.items():
        # bucket 1: Reactive descriptors.
        if isinstance(value, Reactive):
            meta["reactives"][name] = value
            # MicroPython does not call __set_name__; this is the
            # explicit replacement.  The descriptor resolves its
            # watch_<name> / validate_<name> siblings against the
            # owner here.
            value._bind_name(name, cls)
            continue

        # bucket 2: compute_<name> methods.
        if (
            name.startswith("compute_")
            and len(name) > len("compute_")
            and callable(value)
        ):
            attr = name[len("compute_"):]
            meta["computes"][attr] = value
            continue

        # bucket 3: @on-decorated handlers.
        # `_tui_on` is a list of _OnSelector records appended by the
        # @on decorator above.  A method may carry multiple selectors;
        # each fans out into its own entry in meta["handlers"].
        if callable(value):
            tui_on = _take_pending_on(value)
            if tui_on:
                for sel in tui_on:
                    bucket = meta["handlers"].setdefault(
                        sel.message_type, []
                    )
                    bucket.append((value, sel))
                # Fall through to bucket 4: a method tagged with @on
                # may also be named on_<event>.  The dispatch order
                # (@on first, name-based second per FR-TUI-14) is
                # preserved by §3.4, not here.

        # bucket 4: on_<event> name-dispatched handlers.
        if (
            name.startswith("on_")
            and len(name) > len("on_")
            and callable(value)
        ):
            # __code__.co_argcount includes `self`.  Arity 1 = (self,);
            # arity 2 = (self, message).  Dispatch (§3.4) uses this to
            # choose the call signature without a per-call inspect.
            arity = _co_argcount(value)
            meta["name_handlers_by_name"][name] = (value, arity)
            continue

        # bucket 5: BINDINGS class attribute.
        if name == "BINDINGS" and isinstance(value, (list, tuple)):
            # Lazy import: Binding is Phase 4b agent 5's file, and the
            # decorator only needs it for coercion.  We try the import
            # and fall back to leaving the values uncoerced if Binding
            # is not yet importable (decorator unit tests that declare
            # no BINDINGS take the fast path).
            coerced = _coerce_bindings(value)
            meta["bindings"].extend(coerced)
            continue

        # bucket 6: TCSS / CSS — silently ignored in v0.1 (D2).
        if name in ("DEFAULT_CSS", "CSS"):
            continue

        # bucket 7: default — no work.

    # FR-TUI-21: a name cannot be both Reactive and compute_<name>
    # on the same class.  The check runs before MRO merge so the
    # error message names the offending class directly.
    _validate_no_compute_reactive_collision(meta, cls)

    # MRO merge.  Subclass wins — `own` is captured before parents
    # are merged in, then re-applied on top.
    _merge_parent_meta(meta, cls)

    # Store the union.  The runtime guard in Widget.__init__ checks
    # `_tui_widget_registered`; the dispatch hot paths read
    # `_tui_widget_meta` (§3.4, §6.3).
    cls._tui_widget_meta = meta
    cls._tui_widget_registered = True
    return cls


# ---------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------


def _co_argcount(fn):
    """Return `fn.__code__.co_argcount`, treating attribute-missing as
    arity-2 (the safe default for callables wrapped by decorators
    that strip `__code__`).  Phase 4b's own handlers always carry
    `__code__`; this guard is defensive.
    """
    code = getattr(fn, "__code__", None)
    if code is None:
        return 2
    return code.co_argcount


def _validate_no_compute_reactive_collision(meta, cls):
    """FR-TUI-21: forbid a name from being both Reactive and computed.

    A class may declare `count = Reactive(0)` *or* `compute_count(self)`,
    not both.  The two halves are independently verified per the spec:
    this is the collision-at-decoration half; the assignment-rejection
    half lives in `Reactive.__set__`.
    """
    reactives = meta["reactives"]
    computes = meta["computes"]
    collisions = []
    for name in computes:
        if name in reactives:
            collisions.append(name)
    if collisions:
        names = ", ".join(sorted(collisions))
        raise TooManyComputesError(
            "class %s declares both Reactive and compute_<name> for: %s"
            % (cls.__name__, names)
        )


def _coerce_bindings(value):
    """Coerce a BINDINGS class attribute into a list of Binding
    instances.

    Accepts either Binding instances or shorthand 2-/3-tuples per
    design doc §6.1:

        Binding("d", "toggle_dark", "Toggle dark")
        ("h", "show_help")                    # 2-tuple shorthand
        ("ctrl+r", "refresh", "Refresh")     # 3-tuple shorthand

    Binding lives in a sibling module owned by Phase 4b agent 5.
    Until that module lands we leave the raw values in place; the
    bindings-dispatch site (§6.3) handles the un-coerced case by
    looking up `.key`, `.action`, and `.description` on whatever
    the value happens to be.
    """
    try:
        from . import _binding as _binding_mod
        Binding = _binding_mod.Binding
    except ImportError:
        # Phase 4b agent 5 not yet landed: forward the raw list.  The
        # decorator's contract is to populate meta["bindings"]; the
        # exact value type is the binding module's contract.
        return list(value)
    out = []
    for entry in value:
        if isinstance(entry, Binding):
            out.append(entry)
        elif hasattr(Binding, "_coerce"):
            out.append(Binding._coerce(entry))
        else:
            # Last-ditch fallback: pass through.  Phase 4b agent 5's
            # contract guarantees _coerce, so this branch is dead
            # code in shipped builds.
            out.append(entry)
    return out


def _has_capturable_artifacts(base):
    """Scan `vars(base)` once for any artifact @widget would capture.

    A base class with any Reactive, `compute_*`, `@on`-decorated
    method, or `BINDINGS` attribute is required to carry
    `_tui_widget_meta`; raising at decoration time prevents the
    silent-metadata-loss failure mode (FR-TUI-28).

    Cost: O(len(base body)) per parent, paid once at child-class
    decoration.  Not on the dispatch hot path.
    """
    # Lazy resolve Reactive — see widget() above.
    Reactive = _resolve_reactive_class()
    for name, value in base.__dict__.items():
        if isinstance(value, Reactive):
            return True
        if (
            name.startswith("compute_")
            and len(name) > len("compute_")
            and callable(value)
        ):
            return True
        if callable(value) and getattr(value, "_tui_on", None):
            return True
        if name == "BINDINGS" and isinstance(value, (list, tuple)) and value:
            # An empty BINDINGS = [] is the base-class convention and
            # does not require @widget on the empty-declaring class.
            # Only non-empty BINDINGS is "capturable artifact" territory.
            return True
    return False


def _merge_parent_meta(meta, cls):
    """Merge parent metas along the MRO into `meta`, subclass-wins.

    Algorithm (design doc §1.2):

      1. Snapshot the child's own meta (`own`).
      2. Walk MRO from grand-parent toward `object`, merging each
         parent's meta into `meta`.
      3. Re-apply `own` on top so child declarations override parent
         declarations on name collision.

    For BINDINGS the merge is order-preserving with last-match-wins
    on key collision (§6.2): the child's bindings come after the
    parent's, and `_dispatch_key` walks the list, so the later entry
    shadows the earlier.

    Raises:
        MissingWidgetDecoratorError: a base in the MRO declares
            capturable artifacts but lacks `_tui_widget_meta`.
    """
    # Snapshot own data before merging, then reset meta slots to empty.
    # Dicts get shallow-copied; lists get list()-copied.  Resetting
    # the slots lets parents repopulate them in MRO order (deepest
    # first) and lets the child's own data be re-applied at the tail
    # for subclass-wins semantics.  The reactive *descriptor objects*
    # are shared by reference (intentional: there is one descriptor
    # per name per class).
    own = {}
    for key, value in meta.items():
        if isinstance(value, dict):
            own[key] = dict(value)
            meta[key] = {}
        else:
            own[key] = list(value)
            meta[key] = []

    # Two passes over the MRO:
    #
    # Pass A — FR-TUI-28 / R3 check:
    #     Walk every base in MRO[1:] and verify that any class
    #     declaring capturable artifacts (Reactive, compute_*, @on,
    #     non-empty BINDINGS) also carries `_tui_widget_meta`.  This
    #     prevents an intermediate undecorated mixin from silently
    #     dropping its metadata.
    #
    # Pass B — meta merge:
    #     Walk only the direct base classes (`cls.__bases__`) in
    #     reverse order.  Each direct base's `_tui_widget_meta` is
    #     already the flattened union of its own meta plus its
    #     ancestors' (by transitive prior decoration), so re-walking
    #     the full MRO would duplicate ancestor entries.  Walking
    #     `__bases__` keeps the merge depth at one and gives correct
    #     C3-linearised composition for multiple inheritance: the
    #     first base contributes first, the second base's entries
    #     override on collision, and the child's own entries win
    #     overall.
    #
    # MicroPython exposes `__bases__` but not `__mro__`; the breadth-
    # first walk below visits every ancestor, which is all Pass A
    # needs (it is an existence check, not an ordering-sensitive
    # merge — Pass B orders by `__bases__` directly).
    parents_for_check = []
    _stack = list(cls.__bases__)
    while _stack:
        _b = _stack.pop(0)
        if _b in parents_for_check:
            continue
        parents_for_check.append(_b)
        _stack.extend(getattr(_b, "__bases__", ()))
    for base in parents_for_check:
        if base is object:
            continue
        if getattr(base, "_tui_widget_meta", None) is not None:
            continue
        # FR-TUI-28 / R3 mitigation: a base with capturable artifacts
        # but no meta would silently lose its metadata.  Raise early —
        # at decoration time, with both class names in the message —
        # so the author fixes it before the bug ships.
        if _has_capturable_artifacts(base):
            raise MissingWidgetDecoratorError(base, raised_from=cls)

    # Pass B: merge direct parents only.  Reversed so the right-most
    # base (later in `class C(A, B):`) wins on dict-update collisions
    # — matching MicroPython's MRO order for left-to-right composition.
    direct_parents = list(cls.__bases__)
    for base in reversed(direct_parents):
        if base is object:
            continue
        parent_meta = getattr(base, "_tui_widget_meta", None)
        if parent_meta is None:
            continue
        _merge_one_parent(meta, parent_meta)

    # Re-apply own on top.
    #
    #   reactives, computes, name_handlers_by_name:
    #       dict[name -> single value].  update() lets the child
    #       replace a parent's same-named entry — that is the
    #       subclass-wins half of the contract.
    #
    #   handlers:
    #       dict[type[Message] -> list[(method, selector)]].  Both
    #       parent and child handlers must fire (FR-TUI-14 dispatch
    #       order is per-level), so the child's list is appended to
    #       the parent's rather than replacing it.
    #
    #   bindings:
    #       list[Binding].  Child entries appended after parent
    #       entries so last-match-wins at key dispatch (§6.2).
    for key, value in own.items():
        if key == "handlers":
            for msg_type, entries in value.items():
                meta[key].setdefault(msg_type, []).extend(entries)
        elif isinstance(value, dict):
            meta[key].update(value)
        else:
            meta[key].extend(value)


def _merge_one_parent(meta, parent_meta):
    """Merge one parent's meta into `meta`.

    Earlier parents are merged first; later parents overwrite earlier
    ones on key collision because dict.update is destructive.  The
    child's `own` is re-applied after this loop returns.
    """
    for key in (
        "reactives",
        "computes",
        "handlers",
        "name_handlers_by_name",
        "bindings",
    ):
        parent_value = parent_meta.get(key)
        if parent_value is None:
            continue
        slot = meta[key]
        if isinstance(slot, dict):
            # `handlers` is dict-of-list; merge by extending the list
            # so a parent and child can both register handlers for the
            # same message type.  Other dicts are name -> single-value
            # so update is the right call.
            if key == "handlers":
                for msg_type, entries in parent_value.items():
                    slot.setdefault(msg_type, []).extend(entries)
            else:
                slot.update(parent_value)
        else:
            # Lists (bindings).  Parent bindings prepended to the
            # child's list so child entries fall later in dispatch
            # order — last-match-wins (§6.2).  We accumulate parent
            # bindings here; the child's own list is re-applied by
            # the caller.
            slot.extend(parent_value)


# ---------------------------------------------------------------------
# Public attribute table.
# ---------------------------------------------------------------------

# Name-dispatch resolution helper for §3.4.  Exposed here so the
# message-pump module can call into a stable surface rather than
# duplicating the snake_case conversion at each dispatch site.
def _name_handler_lookup_name(message_type):
    """Return the on_<snake_case> method name for a Message subclass.

    Mirrors the `_camel_to_snake` helper from `case.py` with the
    `on_` prefix.  Used at dispatch time (§3.4); placed here so the
    @widget bucket walk and the dispatch path share a single
    canonical form.
    """
    return "on_" + camel_to_snake(message_type.__name__)
