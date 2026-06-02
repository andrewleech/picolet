"""
Minimal ``dataclasses`` shim for picolet-tui's MicroPython runtime.

MicroPython ships no ``dataclasses`` module and micropython-lib has no
package for it (research doc 03 §"Per-module table"). Rich and Textual
lean on the decorator heavily — every renderable, reactive payload, and
Message-derived event is defined as a ``@dataclass``. This shim covers
the surface those modules actually use; nothing more.

Implemented:
  - ``@dataclass`` and ``@dataclass(frozen=..., kw_only=...)`` (kw_only
    accepted but ignored — see "deliberate omissions" below).
  - ``field(default=..., default_factory=..., init=..., repr=...,
    compare=...)`` returning a sentinel inspected by the decorator.
  - Synthesised ``__init__`` (positional + keyword), ``__repr__``,
    ``__eq__`` over the ``init=True`` / ``repr=True`` / ``compare=True``
    field subsets respectively.
  - ``frozen=True`` wires ``__setattr__`` / ``__delattr__`` to raise
    ``FrozenInstanceError``; the synthesised ``__init__`` uses
    ``object.__setattr__`` to bypass.
  - ``fields(cls_or_instance)`` returning a tuple of ``Field`` records.
  - ``asdict(instance)`` returning a plain dict (one level; no recursive
    walk into nested dataclasses/lists — Rich/Textual never call it
    recursively, and the cost is real).
  - ``replace(instance, **changes)`` returning a fresh instance.
  - ``MISSING`` sentinel (compared on identity, like CPython).

MicroPython porting note — annotation handling:
  MicroPython's compiler drops `x: int` annotations on the floor
  (`py/compile.c` skips the annotation parse node), so `__annotations__`
  is absent on user classes. The shim therefore identifies fields by
  scanning the class `__dict__` for `Field` sentinels produced by
  `field(...)`. On MicroPython, write each field as `name = field(...)`
  or `name = field(default=...)`. On CPython the standard
  `name: type = field(...)` and bare `name: type` annotation forms also
  work — annotations are honoured when present.

  A second MicroPython quirk: plain `dict` objects do not preserve
  insertion order (only class `__dict__` does, via a different C
  layout). The shim stores field order in a parallel
  `__dataclass_fields_order__` list and reads from it in `fields()`.
  `__dataclass_fields__` is still exposed as a dict for CPython-style
  introspection — its iteration order is unreliable on MP, callers
  should go through `fields()`.

Deliberately NOT implemented (and why):
  - ``slots=True`` — MicroPython does not rewrite ``__class__`` on the
    fly. Synthesis D6/D7 do not need it; Rich/Textual do not request it.
  - ``__post_init__`` — none of the keep-list call sites use it.
  - ``__match_args__`` — no structural pattern matching on MP.
  - ``InitVar`` — unused in the keep list.
  - ``weakref_slot``, ``kw_only`` per-field — synthesis D6 single-thread
    runtime has no use for either.
  - ``compare=False`` short-circuit on the class level (``@dataclass(eq=
    False)``) is honoured, but ``@dataclass(order=True)`` is rejected:
    no widget in the v0.1 set needs ordering on dataclass instances.
  - Recursive ``asdict`` / ``astuple`` — adds ~40 LoC and is a footgun
    when fields point at non-dataclass containers.

Supports: Phase 2b shim pack (synthesis §"Phase 2 - Foundation"); the
``picolet_tui._rich`` subtree (research doc 02 §"Needed Shims" item 1);
the Textual-inspired core in ``picolet_tui.message`` and the reactive
payload structs (FR-TUI-12, FR-TUI-19). Counts against the 20 KiB
``_shims`` romfs sub-budget (NFR-TUI-19).
"""


# ---------------------------------------------------------------------------
# Sentinels and public errors.
# ---------------------------------------------------------------------------

class _MissingType:
    # Identity-compared singleton; CPython's `dataclasses.MISSING` is the
    # same shape, so user code that does `f.default is MISSING` ports
    # across without edits.
    def __repr__(self):
        return "MISSING"


MISSING = _MissingType()


class FrozenInstanceError(AttributeError):
    pass


# ---------------------------------------------------------------------------
# Field descriptor.
# ---------------------------------------------------------------------------

class Field:
    # Mirrors CPython's `dataclasses.Field` for the attributes the shim
    # exposes through `fields()`. Anything not on this list is unsupported.
    __slots__ = ("name", "type", "default", "default_factory",
                 "init", "repr", "compare")

    def __init__(self, default, default_factory, init, repr, compare):
        self.name = None
        self.type = None
        self.default = default
        self.default_factory = default_factory
        self.init = init
        self.repr = repr
        self.compare = compare

    def __repr__(self):
        return "Field(name=%r, default=%r)" % (self.name, self.default)


def field(*, default=MISSING, default_factory=MISSING,
          init=True, repr=True, compare=True):
    """Return a sentinel ``Field`` inspected by ``@dataclass``.

    Rejecting both ``default`` and ``default_factory`` together matches
    CPython behaviour; downstream code in Rich relies on the rejection.
    """
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("cannot specify both default and default_factory")
    return Field(default, default_factory, init, repr, compare)


# ---------------------------------------------------------------------------
# Decorator.
# ---------------------------------------------------------------------------

# Class attribute that records the materialised field list on a decorated
# class. CPython names it `__dataclass_fields__`; keep the name so any
# upstream code that introspects through this attribute (rare in the keep
# list but it happens in Rich's `_inspect`) keeps working.
_FIELDS = "__dataclass_fields__"
_PARAMS = "__dataclass_params__"
# MicroPython's dict does not preserve insertion order — it uses hash
# bucketing for small dicts, which scrambles field declaration order.
# A parallel ordered list is the only portable canonical store; the
# `__dataclass_fields__` dict is kept for CPython introspection parity.
_FIELDS_ORDER = "__dataclass_fields_order__"


class _Params:
    __slots__ = ("frozen", "eq")

    def __init__(self, frozen, eq):
        self.frozen = frozen
        self.eq = eq


def _iter_mro(cls):
    # MicroPython does not expose `__mro__`. Walk `__bases__` depth-first
    # left-to-right (Python's C3 collapses to this for the single-base
    # case that dominates dataclass hierarchies). The walk yields each
    # class at most once, leaf first.
    seen = set()
    stack = [cls]
    out = []
    while stack:
        c = stack.pop(0)
        if id(c) in seen:
            continue
        seen.add(id(c))
        out.append(c)
        bases = getattr(c, "__bases__", ())
        stack[:0] = list(bases)
    return out


def _field_names_for(cls):
    # MicroPython discards `x: int` annotations at compile time
    # (`py/compile.c` skips the annotation node), so `__annotations__`
    # is unavailable on user classes. The shim therefore declares the
    # contract: a dataclass field is any class-level attribute that is
    # either (a) bound to a `Field` sentinel produced by `field(...)`,
    # or (b) annotated in `__annotations__` on CPython hosts where the
    # attribute exists. Bare value defaults (`y = 5`) are only picked
    # up when an annotation is also present — without annotations we
    # cannot tell apart "this is a field default" from "this is a
    # class constant", and silently grabbing the wrong attributes is
    # worse than requiring the call site to use `field()`.
    ann = getattr(cls, "__annotations__", None)
    if ann:
        return list(ann.keys())
    return [name for name, value in cls.__dict__.items()
            if isinstance(value, Field)]


def _collect_fields(cls):
    # Walk the MRO bottom-up so a subclass can shadow a parent field's
    # default without losing the field's position. CPython orders by
    # declaration in the leaf class with parent fields prepended;
    # replicate that.
    field_map = {}
    order = []
    mro = _iter_mro(cls)
    for base in reversed(mro):
        base_order = base.__dict__.get(_FIELDS_ORDER)
        base_fields = base.__dict__.get(_FIELDS)
        if base_order and base_fields:
            for fname in base_order:
                if fname not in field_map:
                    order.append(fname)
                field_map[fname] = base_fields[fname]

    names = _field_names_for(cls)
    ann = getattr(cls, "__annotations__", None) or {}
    for fname in names:
        raw = cls.__dict__.get(fname, MISSING)
        if isinstance(raw, Field):
            fobj = raw
            # Strip the sentinel off the class so instance attribute access
            # doesn't see a `Field` where a default value belongs.
            try:
                delattr(cls, fname)
            except AttributeError:
                pass
        else:
            fobj = Field(default=raw, default_factory=MISSING,
                         init=True, repr=True, compare=True)
        fobj.name = fname
        fobj.type = ann.get(fname)
        if fname not in field_map:
            order.append(fname)
        field_map[fname] = fobj
    return [field_map[name] for name in order]


def _make_init(fields_list, frozen):
    # Reject the "non-default after default" arrangement up-front so we
    # don't emit an __init__ that surprises the caller at runtime.
    saw_default = False
    for f in fields_list:
        if not f.init:
            continue
        has_default = (f.default is not MISSING
                       or f.default_factory is not MISSING)
        if has_default:
            saw_default = True
        elif saw_default:
            raise TypeError("non-default argument %r follows default argument"
                            % f.name)

    init_fields = [f for f in fields_list if f.init]
    # Closure capture: the generated function needs access to MISSING and
    # the per-field default_factory callables. Stash them on a dict the
    # function reads via cell variables.
    factories = {f.name: f.default_factory for f in init_fields
                 if f.default_factory is not MISSING}
    defaults = {f.name: f.default for f in init_fields
                if f.default is not MISSING}

    if frozen:
        def _setter(inst, name, value):
            object.__setattr__(inst, name, value)
    else:
        def _setter(inst, name, value):
            setattr(inst, name, value)

    def __init__(self, *args, **kwargs):
        # Walk init_fields in order; pull from positional first, then
        # keyword, then default/default_factory. A generic walker beats
        # exec'd source on MicroPython: exec() can't produce a function
        # with a real signature on MP anyway (no `inspect.signature`),
        # and the loss of TypeError clarity on bad calls is acceptable
        # for the keep-list call sites.
        if len(args) > len(init_fields):
            raise TypeError("__init__ takes %d positional arguments but %d were given"
                            % (len(init_fields), len(args)))
        for idx, f in enumerate(init_fields):
            if idx < len(args):
                if f.name in kwargs:
                    raise TypeError("got multiple values for %r" % f.name)
                _setter(self, f.name, args[idx])
                continue
            if f.name in kwargs:
                _setter(self, f.name, kwargs.pop(f.name))
                continue
            if f.name in factories:
                _setter(self, f.name, factories[f.name]())
                continue
            if f.name in defaults:
                _setter(self, f.name, defaults[f.name])
                continue
            raise TypeError("missing required argument: %r" % f.name)
        if kwargs:
            unknown = next(iter(kwargs))
            raise TypeError("unexpected keyword argument: %r" % unknown)

    return __init__


def _make_repr(cls_name, fields_list):
    repr_fields = [f for f in fields_list if f.repr]

    def __repr__(self):
        parts = []
        for f in repr_fields:
            parts.append("%s=%r" % (f.name, getattr(self, f.name, MISSING)))
        return "%s(%s)" % (cls_name, ", ".join(parts))
    return __repr__


def _make_eq(fields_list):
    compare_fields = [f for f in fields_list if f.compare]

    def __eq__(self, other):
        # CPython narrows equality to the exact class; subclasses with
        # extra fields would otherwise compare equal to their parent.
        if other.__class__ is not self.__class__:
            return NotImplemented
        for f in compare_fields:
            if getattr(self, f.name) != getattr(other, f.name):
                return False
        return True
    return __eq__


def _make_frozen_setattr():
    def __setattr__(self, name, value):
        raise FrozenInstanceError("cannot assign to field %r" % name)

    def __delattr__(self, name):
        raise FrozenInstanceError("cannot delete field %r" % name)
    return __setattr__, __delattr__


def _process_class(cls, frozen, eq):
    fields_list = _collect_fields(cls)
    # Build the lookup dict and the order list separately. The list is
    # the canonical ordered view; the dict is convenience for
    # CPython-style introspection (`cls.__dataclass_fields__["name"]`).
    field_dict = {}
    for f in fields_list:
        field_dict[f.name] = f
    setattr(cls, _FIELDS, field_dict)
    setattr(cls, _FIELDS_ORDER, [f.name for f in fields_list])
    setattr(cls, _PARAMS, _Params(frozen=frozen, eq=eq))

    cls.__init__ = _make_init(fields_list, frozen)
    cls.__repr__ = _make_repr(cls.__name__, fields_list)
    if eq:
        cls.__eq__ = _make_eq(fields_list)
    if frozen:
        cls.__setattr__, cls.__delattr__ = _make_frozen_setattr()
    return cls


def dataclass(cls=None, *, frozen=False, eq=True, kw_only=False, order=False):
    # `order=True` would require synthesising __lt__/__le__/__gt__/__ge__;
    # no v0.1 widget asks for it (synthesis Phase 5 widget list).
    if order:
        raise NotImplementedError("@dataclass(order=True) is not supported")
    # kw_only is accepted but does not change behaviour: MicroPython has no
    # PEP 570 / 3102 enforcement, and the generic *args/**kwargs walker in
    # _make_init treats every field as accepting either form already.

    def _wrap(c):
        return _process_class(c, frozen=frozen, eq=eq)

    if cls is None:
        return _wrap
    return _wrap(cls)


# ---------------------------------------------------------------------------
# Introspection helpers.
# ---------------------------------------------------------------------------

def fields(class_or_instance):
    try:
        flds = getattr(class_or_instance, _FIELDS)
        order = getattr(class_or_instance, _FIELDS_ORDER)
    except AttributeError:
        raise TypeError("fields() argument must be a dataclass or instance")
    # Use the order list — `flds.values()` order is unreliable on MP.
    return tuple(flds[name] for name in order)


def is_dataclass(obj):
    cls = obj if isinstance(obj, type) else type(obj)
    return hasattr(cls, _FIELDS)


def asdict(instance):
    if not is_dataclass(instance) or isinstance(instance, type):
        raise TypeError("asdict() should be called on dataclass instances")
    # Shallow only — deliberate, see module docstring.
    return {f.name: getattr(instance, f.name) for f in fields(instance)}


def replace(instance, **changes):
    if not is_dataclass(instance) or isinstance(instance, type):
        raise TypeError("replace() should be called on dataclass instances")
    # Build the kwargs from the live instance values, then overlay the
    # caller's changes. Init-only fields with a default_factory get
    # re-evaluated only when the caller didn't supply a replacement.
    kwargs = {}
    for f in fields(instance):
        if not f.init:
            continue
        if f.name in changes:
            kwargs[f.name] = changes.pop(f.name)
        else:
            kwargs[f.name] = getattr(instance, f.name)
    if changes:
        unknown = next(iter(changes))
        raise TypeError("replace() got unexpected keyword argument: %r" % unknown)
    return instance.__class__(**kwargs)
