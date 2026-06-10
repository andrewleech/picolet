"""Pure-Python ``enum`` shim — decorator-driven, no metaclass.

Implemented:
  Enum, IntEnum                  — named-value sentinels.
  Flag, IntFlag                  — bitfield enums with ``|`` / ``&`` / ``^`` / ``~``.
  auto()                         — sequential value generator resolved at
                                   decoration time.
  @enum_class                    — class decorator that promotes UPPER_CASE
                                   class attributes into member instances.
  EnumMeta-equivalent surface    — ``cls.__members__`` (ordered dict),
                                   ``cls.iter_members()`` classmethod,
                                   ``cls.has_value(name)`` classmethod,
                                   ``cls.from_value(value)`` /
                                   ``cls.from_name(name)`` lookups.

Deliberately NOT implemented:
  * The real ``EnumMeta`` metaclass.  MicroPython does not honour
    user-defined metaclasses for class-body attribute capture (research
    doc 03 §"Per-module table" line on ``enum``), so the CPython idiom
    ``class Color(Enum): RED = 1`` cannot auto-populate members.  Users
    must apply ``@enum_class`` explicitly — the deviation is documented
    here and in ``docs/tui/migration-from-textual.md``.
  * ``NAME in EnumClass`` membership, ``iter(EnumClass)``,
    ``len(EnumClass)``, ``EnumClass['NAME']`` subscripting — all of
    those require a real metaclass to dispatch on the class object
    itself.  Use ``"NAME" in EnumClass.__members__``,
    ``EnumClass.iter_members()``, ``len(EnumClass.__members__)``, or
    ``EnumClass.__members__["NAME"]`` instead.
  * ``StrEnum`` (Python 3.11+), ``ReprEnum``, ``EnumCheck``,
    ``verify()``, ``nonmember()``, ``member()``, ``property`` aliasing,
    ``__init_subclass__`` hooks, ``_missing_`` customisation, and the
    ``_generate_next_value_`` override.  None of these appear on the
    Textual-inspired core path; carrying them would only burn the
    NFR-TUI-19 ``_shims`` 20 KiB romfs budget.
  * Aliases (two member names pointing to the same value collapse onto
    the first).  Textual's CSS ``Specificity`` Flag uses distinct
    values per bit, so the lossy collapse is acceptable.

Spec mapping:
  Phase 2b shim per ``docs/tui/research/00-synthesis.md`` §"Phase 2b"
  (~250 LoC budget) and ``docs/tui/research/03-mp-stdlib-coverage.md``
  §"Per-module table" row ``enum``.  Consumed by the Textual-inspired
  CSS specificity arithmetic (Flag) and by widget-internal pump-state
  sentinels (Enum / IntEnum).

Module path: picolet_tui._shims.enum

Usage::

    from enum import Enum, IntEnum, Flag, auto, enum_class

    @enum_class
    class Color(Enum):
        RED = 1
        GREEN = 2
        BLUE = 3

    @enum_class
    class Perm(Flag):
        READ = auto()
        WRITE = auto()
        EXEC = auto()

    rw = Perm.READ | Perm.WRITE
    assert Perm.READ in rw           # __contains__ on the member, not the class
    assert rw.value == 3
    assert list(Color.iter_members()) == [Color.RED, Color.GREEN, Color.BLUE]
    assert Color.has_value("RED")
    assert Color.from_value(2) is Color.GREEN
"""


from collections import OrderedDict


# ---------------------------------------------------------------------------
# auto() — sentinel value resolved at @enum_class decoration time.
#
# Each ``auto()`` call returns a distinct sentinel object so subsequent
# scans can tell "this slot wants an auto-assigned value" apart from
# "this slot is an int that happens to equal 1".  Identity comparison
# (``is _AUTO_SENTINEL``) would not work because each call needs a
# fresh sentinel that ``enum_class`` can spot and replace.
# ---------------------------------------------------------------------------


class _Auto:
    """Placeholder produced by ``auto()`` and replaced at decoration time.

    Carries a per-call creation counter: MicroPython's class ``__dict__``
    iteration is NOT insertion-ordered, so the decorator cannot recover
    declaration order from the scan.  The ``auto()`` calls themselves
    execute in declaration order inside the class body, so sorting
    sentinels by this counter reconstructs it — the same trick the
    dataclasses shim uses for ``field()``.
    """

    __slots__ = ("_order",)

    def __init__(self, order):
        self._order = order


_auto_counter = [0]


def auto():
    """Return a sentinel that ``@enum_class`` replaces with the next value.

    For ``Enum`` / ``IntEnum`` subclasses values count up from 1 in
    declaration order.  For ``Flag`` / ``IntFlag`` subclasses each slot
    takes the next power of two.  Mixing ``auto()`` with explicit values
    in one class raises at decoration time — declaration order of the
    explicit slots is unrecoverable on MicroPython, so the CPython
    "continue from the last explicit value" semantics cannot be honoured.
    """
    _auto_counter[0] += 1
    return _Auto(_auto_counter[0])


# ---------------------------------------------------------------------------
# Member instances.  CPython's ``Enum`` returns instances of the enum
# class itself; mimicking that exactly would mean rewriting ``__new__``
# on every user class.  We approximate: members are instances of a
# dedicated holder class (``_EnumMember`` / ``_IntEnumMember`` /
# ``_FlagMember``) that snapshots its parent class so ``isinstance``
# checks against the user's class still succeed via a registered
# ``__class__`` proxy.  Good enough for ``Foo.BAR is Foo.BAR``,
# ``Foo.BAR == Foo.BAR``, ``int(Foo.BAR) == 1``, and ``str(Foo.BAR)``
# pretty-print — which is the whole surface Textual + Rich touch.
# ---------------------------------------------------------------------------


class _EnumMember:
    """Holder for a single Enum member: name, value, parent class.

    No ``__slots__`` — int subclasses (``_IntEnumMember`` /
    ``_IntFlagMember``) mix this in alongside ``int``, and CPython
    rejects the dual ``__slots__`` layout.  The romfs cost of a dict
    per member is acceptable: typical enums have < 16 members.
    """

    def __init__(self, name, value, parent):
        self._name_ = name
        self._value_ = value
        self._parent_ = parent

    @property
    def name(self):
        return self._name_

    @property
    def value(self):
        return self._value_

    def __repr__(self):
        return "<{}.{}: {!r}>".format(self._parent_.__name__, self._name_, self._value_)

    def __str__(self):
        return "{}.{}".format(self._parent_.__name__, self._name_)

    def __hash__(self):
        return hash((self._parent_, self._name_))

    def __eq__(self, other):
        if isinstance(other, _EnumMember):
            return self._parent_ is other._parent_ and self._name_ == other._name_
        return NotImplemented


class _IntEnumMember(int):
    """IntEnum member: behaves as ``int`` for arithmetic and comparisons.

    Subclasses ``int`` directly with NO custom ``__new__``: MicroPython
    cannot call a builtin base's ``__new__`` from a subclass, so the
    payload goes in via plain construction (``_IntEnumMember(value)``)
    and ``_new_member`` backfills ``_name_`` / ``_value_`` / ``_parent_``
    afterwards (instance attribute assignment on int subclasses works on
    both interpreters).  ``_EnumMember`` cannot be a base because its
    three-argument ``__init__`` collides with int construction.
    """

    @property
    def name(self):
        return self._name_

    @property
    def value(self):
        return self._value_

    def __repr__(self):
        return "<{}.{}: {}>".format(self._parent_.__name__, self._name_, int(self))

    def __str__(self):
        return "{}.{}".format(self._parent_.__name__, self._name_)


class _FlagOps:
    """Bitwise-operator mixin shared by Flag and IntFlag members.

    Pure-Python and ``__init__``-free so it can sit in front of either
    ``_EnumMember`` or ``int`` in the bases without affecting
    construction.
    """

    def __or__(self, other):
        return self._parent_._combine(self, other, _flag_or)

    def __and__(self, other):
        return self._parent_._combine(self, other, _flag_and)

    def __xor__(self, other):
        return self._parent_._combine(self, other, _flag_xor)

    def __invert__(self):
        return self._parent_._invert(self)

    def __contains__(self, other):
        # ``Perm.READ in rw`` — Flag's standard containment test:
        # ``other`` is a subset of ``self`` iff ``self & other == other``.
        if not isinstance(other, (_FlagMember, _IntFlagMember)):
            return False
        return (self._value_ & other._value_) == other._value_

    def __bool__(self):
        return self._value_ != 0

    def __int__(self):
        return self._value_


class _FlagMember(_FlagOps, _EnumMember):
    """Flag member: supports ``|`` / ``&`` / ``^`` / ``~`` returning fresh members."""


class _IntFlagMember(_FlagOps, int):
    """IntFlag member: a Flag that is also a real int.

    Same construct-then-backfill shape as ``_IntEnumMember`` — see its
    docstring for the MicroPython constraint that forces it.
    """

    @property
    def name(self):
        return self._name_

    @property
    def value(self):
        return self._value_

    def __repr__(self):
        return "<{}.{}: {}>".format(self._parent_.__name__, self._name_, int(self))

    def __str__(self):
        return "{}.{}".format(self._parent_.__name__, self._name_)


def _new_member(member_type, name, value, parent):
    """Construct an enum member of any flavour and backfill its identity.

    int-flavoured members must be built by passing the int payload to
    plain construction (the only way to set a builtin base's value on
    MicroPython); holder-flavoured members take the full triple in
    ``__init__``.  Backfill is unconditional so every flavour exposes
    ``_name_`` / ``_value_`` / ``_parent_`` the same way.
    """
    if issubclass(member_type, int):
        member = member_type(value)
        member._name_ = name
        member._value_ = value
        member._parent_ = parent
    else:
        member = member_type(name, value, parent)
    return member


def _flag_or(a, b):
    return a | b


def _flag_and(a, b):
    return a & b


def _flag_xor(a, b):
    return a ^ b


# ---------------------------------------------------------------------------
# Base classes.  Users subclass these in the usual ``class Foo(Enum)``
# shape; the decorator does the heavy lifting.  Methods live on the
# *class* side rather than the metaclass since MP gives us no metaclass.
# ---------------------------------------------------------------------------


class Enum:
    """Base class for value-bearing named sentinels.

    Subclass and apply ``@enum_class`` to populate members::

        @enum_class
        class State(Enum):
            IDLE = 1
            RUNNING = 2
    """

    _member_type_ = _EnumMember
    __members__ = {}


class IntEnum(Enum):
    """Enum whose members are real ``int`` subclasses."""

    _member_type_ = _IntEnumMember


class Flag(Enum):
    """Enum whose members compose under bitwise operators."""

    _member_type_ = _FlagMember

    @classmethod
    def _combine(cls, a, b, op):
        """Build a synthetic member representing a bitwise combination.

        Combinations are not registered in ``__members__``; they exist
        only as transient values held by callers.  Naming reflects the
        decomposition so ``repr(Perm.READ | Perm.WRITE)`` reads as
        ``<Perm.READ|WRITE: 3>`` — Textual-style debug ergonomics.
        """
        av = a._value_ if isinstance(a, _FlagMember) else int(a)
        bv = b._value_ if isinstance(b, _FlagMember) else int(b)
        new_value = op(av, bv)
        return cls._synthesize(new_value)

    @classmethod
    def _invert(cls, a):
        # Mask against the union of declared bits so ``~Perm.READ``
        # yields the other declared flags, not a giant negative int.
        mask = cls._all_bits_
        return cls._synthesize(~a._value_ & mask)

    @classmethod
    def _synthesize(cls, value):
        # Compose a name from the registered members that cover the bits.
        parts = [m._name_ for m in cls.__members__.values()
                 if m._value_ and (m._value_ & value) == m._value_]
        name = "|".join(parts) if parts else "0"
        return _new_member(cls._member_type_, name, value, cls)


class IntFlag(Flag):
    """Flag whose members are real ``int`` subclasses."""

    _member_type_ = _IntFlagMember


# ---------------------------------------------------------------------------
# Decorator — the one piece of machinery that turns CPython-style class
# bodies into populated enum classes under MicroPython.
# ---------------------------------------------------------------------------


def enum_class(cls):
    """Promote UPPER_CASE class attributes on ``cls`` into enum members.

    Scan rules (FR-TUI-57 / D1 "class-dict only"):
      * Only entries directly on ``cls.__dict__`` are considered;
        inherited attributes are ignored.
      * Ordering: ``auto()`` slots are processed in declaration order
        (recovered from the sentinel's per-call counter); explicit
        slots are processed in value order.  Mixing the two raises
        TypeError — MicroPython class dicts do not preserve
        declaration order, so CPython's interleaving rule cannot be
        honoured.
      * Names starting with ``_`` are skipped.
      * Callables (``classmethod`` / ``staticmethod`` / plain functions)
        are left untouched.
      * ``auto()`` sentinels are resolved against the running value
        counter for the appropriate base class.

    The decorator binds:
      * ``cls.NAME = member`` for each promoted slot.
      * ``cls.__members__`` = ordered ``{name: member}`` dict.
      * ``cls._all_bits_`` (Flag/IntFlag only) = OR of all member values,
        used by ``__invert__`` to mask back into the declared bit space.

    Returns ``cls`` so it composes as a decorator.
    """
    member_type = cls._member_type_
    is_flag = issubclass(member_type, _FlagOps)

    # Snapshot the slots before we start mutating cls.  cls.__dict__
    # rather than vars(cls): MicroPython has no vars() builtin.  The
    # iteration order is NOT declaration order on MicroPython, which is
    # why the ordering rules below exist.
    raw_slots = []
    for name in list(cls.__dict__.keys()):
        if name.startswith("_"):
            continue
        value = cls.__dict__[name]
        if callable(value) and not isinstance(value, _Auto):
            continue
        # Skip descriptors (classmethod / staticmethod / property).
        if isinstance(value, (classmethod, staticmethod, property)):
            continue
        raw_slots.append((name, value))

    # Recover a deterministic order.  auto() slots sort by their per-call
    # creation counter (declaration order — the calls run in order even
    # though the dict scrambles).  Explicit slots sort by value, which
    # equals declaration order for every conventionally-written enum.
    # Mixing the two is rejected outright: CPython's "auto continues
    # from the last explicit value" rule needs declaration interleaving
    # we cannot see.
    autos = [(name, raw) for name, raw in raw_slots if isinstance(raw, _Auto)]
    explicits = [(name, raw) for name, raw in raw_slots if not isinstance(raw, _Auto)]
    if autos and explicits:
        raise TypeError(
            "%s mixes auto() with explicit values; unsupported because "
            "MicroPython class dicts do not preserve declaration order"
            % cls.__name__
        )
    autos.sort(key=lambda item: item[1]._order)
    explicits.sort(key=lambda item: item[1])

    # OrderedDict, not {}: plain MicroPython dicts do not preserve
    # insertion order, and __members__ iteration order is part of the
    # enum contract.
    members = OrderedDict()
    next_int = 1
    next_bit = 1
    all_bits = 0
    for name, raw in autos + explicits:
        if isinstance(raw, _Auto):
            if is_flag:
                value = next_bit
                next_bit <<= 1
            else:
                value = next_int
                next_int += 1
        else:
            value = raw

        member = _new_member(member_type, name, value, cls)
        members[name] = member
        setattr(cls, name, member)
        if is_flag and isinstance(value, int):
            all_bits |= value

    cls.__members__ = members
    if is_flag:
        cls._all_bits_ = all_bits

    # Lookup / iteration helpers as classmethods.  These cannot be
    # ``__iter__`` / ``__len__`` / ``__contains__`` because Python's
    # dunder dispatch looks them up on ``type(cls)`` (the metaclass),
    # not on ``cls`` itself; with no metaclass we expose explicitly
    # named helpers instead.
    cls.iter_members = classmethod(_enum_iter_members)
    cls.has_value = classmethod(_enum_has_value)
    cls.from_value = classmethod(_enum_from_value)
    cls.from_name = classmethod(_enum_from_name)

    return cls


# Classmethod bodies — kept module-level so the decorator can attach
# them via ``classmethod(...)`` without paying for a fresh closure per
# decorated class.


def _enum_iter_members(cls):
    # Iteration order: __members__ is an OrderedDict populated in
    # auto-declaration / explicit-value order (plain MicroPython dicts
    # do NOT preserve insertion order — see enum_class).
    return iter(cls.__members__.values())


def _enum_has_value(cls, name):
    return name in cls.__members__


def _enum_from_value(cls, value):
    for m in cls.__members__.values():
        if m._value_ == value:
            return m
    raise ValueError("{!r} is not a valid {}".format(value, cls.__name__))


def _enum_from_name(cls, name):
    try:
        return cls.__members__[name]
    except KeyError:
        raise KeyError("{} has no member {!r}".format(cls.__name__, name))


__all__ = (
    "Enum",
    "IntEnum",
    "Flag",
    "IntFlag",
    "auto",
    "enum_class",
)
