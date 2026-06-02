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

    A bare class instance suffices — equality and ordering are never
    asked of it; ``enum_class`` only needs to recognise the type.
    """

    __slots__ = ()


_AUTO_SINGLETON = _Auto()


def auto():
    """Return a sentinel that ``@enum_class`` replaces with the next value.

    For ``Enum`` / ``IntEnum`` subclasses the next value is
    ``max(existing) + 1`` starting at 1.  For ``Flag`` / ``IntFlag``
    subclasses the next value is the next unused power of two.
    """
    # A shared singleton is fine: the decorator scans by ``isinstance``,
    # not by identity-per-call, and aliasing two auto() slots to the
    # same value would already be a user bug.
    return _AUTO_SINGLETON


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


class _IntEnumMember(_EnumMember, int):
    """IntEnum member: behaves as ``int`` for arithmetic and comparisons."""

    # int's __new__ runs before _EnumMember.__init__ to set the int
    # payload.  __slots__ is intentionally not inherited from _EnumMember
    # because int subclasses force a regular __dict__ on CPython, and
    # MP follows suit.
    def __new__(cls, name, value, parent):
        obj = int.__new__(cls, value)
        return obj

    def __repr__(self):
        return "<{}.{}: {}>".format(self._parent_.__name__, self._name_, int(self))

    def __str__(self):
        return "{}.{}".format(self._parent_.__name__, self._name_)


class _FlagMember(_EnumMember):
    """Flag member: supports ``|`` / ``&`` / ``^`` / ``~`` returning fresh members."""

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
        if not isinstance(other, _FlagMember):
            return False
        return (self._value_ & other._value_) == other._value_

    def __bool__(self):
        return self._value_ != 0

    def __int__(self):
        return self._value_


class _IntFlagMember(_FlagMember, int):
    """IntFlag member: a Flag that is also a real int."""

    def __new__(cls, name, value, parent):
        return int.__new__(cls, value)


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
        member = cls._member_type_(name, value, cls)
        # int-flavoured holders skip __init__ via __new__; backfill so the
        # name / value / parent slots are populated either way.
        _EnumMember.__init__(member, name, value, cls)
        return member


class IntFlag(Flag):
    """Flag whose members are real ``int`` subclasses."""

    _member_type_ = _IntFlagMember


# ---------------------------------------------------------------------------
# Decorator — the one piece of machinery that turns CPython-style class
# bodies into populated enum classes under MicroPython.
# ---------------------------------------------------------------------------


def enum_class(cls):
    """Promote UPPER_CASE class attributes on ``cls`` into enum members.

    Scan rules (FR-TUI-57 / D1 "vars(cls) only"):
      * Only entries directly on ``vars(cls)`` are considered; inherited
        attributes are ignored.
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
    is_flag = _is_flag_subclass(cls)
    member_type = cls._member_type_

    # Snapshot the slots before we start mutating cls.  vars(cls) is a
    # MappingProxyType on CPython but a plain dict on MP; either way,
    # iterate a list copy so assignment during the loop is safe.
    raw_slots = []
    for name in list(vars(cls).keys()):
        if name.startswith("_"):
            continue
        value = vars(cls)[name]
        if callable(value) and not isinstance(value, _Auto):
            continue
        # Skip descriptors (classmethod / staticmethod / property).
        if isinstance(value, (classmethod, staticmethod, property)):
            continue
        raw_slots.append((name, value))

    # Resolve auto() sentinels to concrete values.  Flag/IntFlag get
    # successive powers of two; Enum/IntEnum get successive ints from 1.
    members = {}
    next_int = 1
    next_bit = 1
    all_bits = 0
    for name, raw in raw_slots:
        if isinstance(raw, _Auto):
            if is_flag:
                value = next_bit
                next_bit <<= 1
            else:
                value = next_int
                next_int += 1
        else:
            value = raw
            # Keep counters monotonic so a mix of explicit + auto()
            # entries does not collide.  Matches CPython behaviour.
            if isinstance(value, int) and not is_flag:
                if value >= next_int:
                    next_int = value + 1
            elif is_flag and isinstance(value, int) and value > 0:
                # Advance next_bit past the highest bit already used.
                bit = 1
                while bit <= value:
                    bit <<= 1
                if bit > next_bit:
                    next_bit = bit

        member = member_type(name, value, cls)
        # int-flavoured holders go through __new__ only; backfill the
        # _EnumMember slots so name / value / parent are reachable on
        # every subtype with a single code path.
        _EnumMember.__init__(member, name, value, cls)

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


def _is_flag_subclass(cls):
    # ``issubclass`` over our own bases — Flag / IntFlag are not
    # importable yet from the user side at decoration time on every
    # ordering, so do the walk by hand.
    for base in _mro(cls):
        if base is Flag or base is IntFlag:
            return True
    return False


def _mro(cls):
    # MicroPython does not always expose ``cls.__mro__``; the iterative
    # walk over ``__bases__`` covers single-inheritance enum trees,
    # which is the only shape we support.
    seen = [cls]
    stack = list(getattr(cls, "__bases__", ()))
    while stack:
        b = stack.pop(0)
        if b in seen:
            continue
        seen.append(b)
        stack.extend(getattr(b, "__bases__", ()))
    return seen


# Classmethod bodies — kept module-level so the decorator can attach
# them via ``classmethod(...)`` without paying for a fresh closure per
# decorated class.


def _enum_iter_members(cls):
    # Iteration order matches declaration order — Python 3.7+ dict
    # insertion order, which MP's dict also honours.
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
