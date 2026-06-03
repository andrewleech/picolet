"""Textual CSS scalar parser, ported for the picolet-tui frozen runtime.

Upstream: ``textual/css/scalar.py`` (~380 LoC).  Phase 4a leaf per
``docs/tui/research/00-synthesis.md`` §"Phase 4 - Textual core" / 4a
"Leaves".  Flattened to ``picolet_tui._textual.scalar`` (no ``css/``
subdir) since synthesis decision D2 ships a Python-side ``Style(...)``
DSL instead of a TCSS parser — there is no CSS subsystem here to slot
under.  Phase 4b's ``Widget.width`` still exposes a ``Scalar``, which is
why this module survives D2.

Adjustments from upstream
-------------------------
1. ``from enum import Enum, unique`` -> ``from picolet_tui._shims.enum
   import Enum, enum_class``.  MicroPython has no ``enum`` module and
   the shim's ``@enum_class`` replaces ``@unique`` plus the implicit
   metaclass-driven member population — synthesis decision D1 (no
   metaclasses, no ``__init_subclass__``, no ``__set_name__``).  Members
   are still distinct values per upstream, so ``@unique``'s alias check
   is a no-op anyway.
2. ``from fractions import Fraction`` -> local ``_Fraction``.
   ``fractions`` is not in core MicroPython and not in micropython-lib.
   The only callers reduce to ``round(scalar.resolve(...))`` at the
   compositor's strip-mapping site, so a minimum mixed-precision rational
   that supports ``*``, ``+``, ``int()``, ``round()``, and ``__bool__``
   covers every observed use site (NFR-TUI-19 budget — pulling in a
   full ``fractions`` port would burn ~600 LoC for arithmetic the
   compositor immediately collapses to ints).  Promoting to a real
   ``Fraction`` shim is a v0.2 task if layout precision proves
   insufficient; tracked under R1's deferred-precision bucket.
3. ``import rich.repr`` -> ``picolet_tui._rich.repr``.  Same surface
   (``@auto(angular=True)``), but the trimmed Rich subset's ``auto``
   requires an explicit ``__rich_repr__`` (no ``inspect.signature``
   fallback per D9).  ``ScalarOffset`` already defines one upstream, so
   the decoration keeps working.
4. ``class Scalar(NamedTuple)`` / ``class ScalarOffset(NamedTuple)`` ->
   plain ``tuple`` subclasses with positional ``__new__`` and ``@property``
   accessors.  The typing-shim's ``NamedTuple`` is a ``_Placeholder``
   that cannot be subclassed (see ``_rich/color_triplet.py`` for the
   established pattern this mirrors).  ``isinstance(x, tuple)``,
   destructuring (``value, unit, _ = scalar``), ``scalar.value``, and
   ``scalar == (1.0, Unit.CELLS, Unit.WIDTH)`` all continue to work.
5. ``from textual.geometry import Offset, Size, clamp`` -> deferred
   local import inside ``ScalarOffset.resolve``.  Phase 4a lifts
   ``geometry`` in the same wave as this file; the deferred import
   tolerates either landing order.  ``clamp`` is needed once by
   ``percentage_string_to_float`` and is reimplemented inline there so
   the module-level import surface stays empty until ``geometry`` is
   imported by a caller.
6. ``@lru_cache(maxsize=4096)`` on ``Scalar.resolve`` -> dropped.  The
   shim caps every ``lru_cache`` at 128 entries per NFR-TUI-6 (R4
   mitigation a); a per-instance method cache would have keyed on
   ``self`` and ballooned past that budget.  Recomputation cost is
   dominated by a single ``Fraction(...)`` multiplication, which is
   cheap on the rational stand-in used here.  The class-level
   ``Scalar.parse`` cache (``maxsize=1024`` upstream, ``128`` after
   shim coercion) is kept because parsing is the only call site that
   sees raw user strings.
7. Type-annotation surface (``Iterable``, ``int | None``, ``Scalar |
   None``) stripped to bare names per the typing-shim contract — the
   PEP 604 union syntax (``int | None``) parses on MicroPython 1.20+
   but evaluates to a no-op object at import time and the freezer
   does not error.  Annotations on parameters are kept verbatim for
   IDE / docs round-tripping; runtime introspection is not asked of
   them.
8. The ``_MATCH_SCALAR`` regex (anchored, optional unit suffix) is kept
   verbatim.  The pattern uses only anchored basic alternation and
   character-class-free quantifiers — re1.5 (``extmod/modre.c``) handles
   it.  No flags (the upstream regex declares none either), no named
   groups, no backreferences; ``match.groups()`` is available iff
   ``MICROPY_PY_RE_MATCH_GROUPS`` is on, which the picolet-tui variant
   pins (see ``tui-v0.1-spec.md`` build flags).

Spec coverage
-------------
* FR-TUI-33 / FR-TUI-34 — Style DSL accepts ``width=10``, ``width="50%"``,
  ``width="1fr"``, and ``width="auto"`` by routing the string forms
  through ``Scalar.parse``.
* FR-TUI-43..45 — Container / Vertical / Horizontal use ``Scalar``
  units to express layout dimensions.
* NFR-TUI-6 / NFR-TUI-19 — the cache-size and ``_textual`` romfs
  budgets are honoured by the shim coercion in adjustment 6 and by
  the deferred ``geometry`` import in adjustment 5.
"""

import re

from picolet_tui._shims.enum import Enum, enum_class
from picolet_tui._shims.functools import lru_cache
from picolet_tui._shims.typing import Iterable
from picolet_tui._rich import repr as _rich_repr


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScalarError(Exception):
    """Base class for exceptions raised by the Scalar class."""


class ScalarResolveError(ScalarError):
    """Raised for errors resolving scalars (unlikely to occur in practice)."""


class ScalarParseError(ScalarError):
    """Raised when a scalar couldn't be parsed from a string."""


# ---------------------------------------------------------------------------
# Unit enumeration.  ``@enum_class`` populates ``__members__`` and binds
# each UPPER_CASE class attribute to a fresh ``_EnumMember``; this is the
# explicit substitute for upstream's ``@unique`` + ``EnumMeta`` (D1).
# Member values match upstream so any caller that pickled a Unit value
# in v0.x test fixtures still resolves to the same logical unit.
# ---------------------------------------------------------------------------


@enum_class
class Unit(Enum):
    """Enumeration of the various units inherited from CSS."""

    CELLS = 1
    FRACTION = 2
    PERCENT = 3
    WIDTH = 4
    HEIGHT = 5
    VIEW_WIDTH = 6
    VIEW_HEIGHT = 7
    AUTO = 8


# Symbol tables.  Populated *after* the decorator runs so ``Unit.CELLS``
# resolves to the concrete member instance rather than the raw int 1
# that lived on the class body.
UNIT_SYMBOL = {
    Unit.CELLS: "",
    Unit.FRACTION: "fr",
    Unit.PERCENT: "%",
    Unit.WIDTH: "w",
    Unit.HEIGHT: "h",
    Unit.VIEW_WIDTH: "vw",
    Unit.VIEW_HEIGHT: "vh",
}

SYMBOL_UNIT = {v: k for k, v in UNIT_SYMBOL.items()}


# Anchored regex covers "-3", "3", "3.14", ".5", "10fr", "50%", "25vh".
# Trailing unit suffixes are optional (``UNIT_SYMBOL[Unit.CELLS] == ""``
# accepts the bare numeric form).  ``.match`` is bound module-level so
# parse() avoids re-resolving the attribute on every hit.
_MATCH_SCALAR = re.compile(r"^(-?\d+\.?\d*)(fr|%|w|h|vw|vh)?$").match


# ---------------------------------------------------------------------------
# Mixed-precision rational stand-in for ``fractions.Fraction``.
#
# Carries an exact integer numerator/denominator pair when both inputs
# are integers (so ``Fraction(size.width, 100)`` stays exact for the
# percent path) and falls through to float for the ``.value`` factor
# inherited from a possibly-fractional Scalar (``Scalar("3.14fr")``).
# The only consumer is ``round()`` or ``int()`` at the compositor's
# strip site, so we expose just enough of ``Fraction``'s surface to
# satisfy:
#   * ``a * b``     - resolve_*() arithmetic
#   * ``int(f)``    - ScalarOffset.resolve does ``round(...)`` which
#                     dispatches to ``__round__`` then ``__int__``
#   * ``bool(f)``   - ScalarOffset.__bool__ checks ``x.value or y.value``
#                     (operates on Scalar.value, not _Fraction; included
#                     defensively for future call sites)
#   * ``-f``        - resolve negation, no caller today
# Anything beyond multiplication and rounding (cross-type comparisons,
# float-comparable hashing, full Stern-Brocot reduction) is out of
# scope for v0.1; promote to a real shim if the layout engine ever
# composes two _Fraction values via subtraction or division.
# ---------------------------------------------------------------------------


class _Fraction:
    """Numerator/denominator pair used by ``Scalar.resolve``.

    Stores the value as a float internally when either operand is
    float-valued (the ``Scalar.value`` path), or as an exact int pair
    when both are integer.  Exposed as ``Fraction`` inside this module
    to keep the upstream call-site shape unchanged.
    """

    __slots__ = ("_num", "_den", "_is_int")

    def __init__(self, numerator, denominator=1):
        # Accept the ``Fraction(value)`` single-arg form upstream uses
        # in ``_resolve_cells`` / ``_resolve_fraction``.  ``value`` is
        # typically a Scalar.value (float) or a pre-built _Fraction.
        if isinstance(numerator, _Fraction):
            self._num = numerator._num
            self._den = numerator._den
            self._is_int = numerator._is_int
            return
        if isinstance(numerator, float) or isinstance(denominator, float):
            # Float collapse: the strip-coord rounding at the call site
            # erases sub-cell precision anyway.
            self._num = float(numerator) / float(denominator)
            self._den = 1.0
            self._is_int = False
        else:
            # Exact path.  Reduce by GCD so repeated multiplication
            # does not balloon the numerator across the long-lived
            # ``_FRACTION_ONE`` constant.
            n = int(numerator)
            d = int(denominator)
            if d == 0:
                raise ZeroDivisionError("denominator is zero")
            if d < 0:
                n = -n
                d = -d
            g = _gcd(abs(n), d) if n else d
            self._num = n // g
            self._den = d // g
            self._is_int = True

    def __mul__(self, other):
        if isinstance(other, _Fraction):
            if self._is_int and other._is_int:
                return _Fraction(self._num * other._num, self._den * other._den)
            return _Fraction(self._as_float() * other._as_float())
        if isinstance(other, (int, float)):
            if self._is_int and isinstance(other, int):
                return _Fraction(self._num * other, self._den)
            return _Fraction(self._as_float() * other)
        return NotImplemented

    __rmul__ = __mul__

    def __add__(self, other):
        if isinstance(other, _Fraction):
            if self._is_int and other._is_int:
                return _Fraction(
                    self._num * other._den + other._num * self._den,
                    self._den * other._den,
                )
            return _Fraction(self._as_float() + other._as_float())
        if isinstance(other, (int, float)):
            return _Fraction(self._as_float() + other)
        return NotImplemented

    __radd__ = __add__

    def __neg__(self):
        if self._is_int:
            return _Fraction(-self._num, self._den)
        return _Fraction(-self._as_float())

    def __bool__(self):
        return self._num != 0

    def _as_float(self):
        if self._is_int:
            return self._num / self._den
        return self._num

    def __float__(self):
        return float(self._as_float())

    def __int__(self):
        # Truncate toward zero, matching CPython ``int(Fraction(...))``.
        if self._is_int:
            # Integer division with sign correction for negative pairs.
            if (self._num < 0) ^ (self._den < 0):
                # Truncated (toward zero) negative division.
                return -((-self._num) // self._den) if self._num < 0 else -(
                    self._num // (-self._den)
                )
            return self._num // self._den
        return int(self._as_float())

    def __round__(self, ndigits=None):
        # ScalarOffset.resolve calls ``round(...)`` with no ndigits, so
        # we return an int — same shape as ``round(Fraction(7, 2))``
        # returning a Python int rounded half-to-even.
        if ndigits is None:
            # Half-to-even rounding to match CPython's int(round(...)).
            return int(round(self._as_float()))
        return round(self._as_float(), ndigits)

    def __repr__(self):
        if self._is_int:
            return "Fraction({}, {})".format(self._num, self._den)
        return "Fraction({!r})".format(self._num)


def _gcd(a, b):
    # MicroPython's math.gcd may or may not be present depending on the
    # build config; the loop is six lines and avoids the dependency.
    while b:
        a, b = b, a % b
    return a


# Module-private alias so the body below reads identically to upstream.
# Callers do not see ``_Fraction``; the public surface is the resolved
# numeric returned from ``Scalar.resolve``.
Fraction = _Fraction
_FRACTION_ONE = _Fraction(1)


# ---------------------------------------------------------------------------
# Per-unit resolver functions.  These are upstream-verbatim shapes;
# arithmetic dispatches to ``_Fraction`` instead of ``fractions.Fraction``
# but the call signature, argument order, and return shape are identical.
# ---------------------------------------------------------------------------


def _resolve_cells(value, size, viewport, fraction_unit):
    """Resolve an explicit cell count, i.e. ``width: 10``."""
    return Fraction(value)


def _resolve_fraction(value, size, viewport, fraction_unit):
    """Resolve a fraction unit, i.e. ``width: 2fr``."""
    return fraction_unit * Fraction(value)


def _resolve_width(value, size, viewport, fraction_unit):
    """Resolve a width-percentage unit, i.e. ``width: 50w``."""
    return Fraction(value) * Fraction(size.width, 100)


def _resolve_height(value, size, viewport, fraction_unit):
    """Resolve a height-percentage unit, i.e. ``height: 12h``."""
    return Fraction(value) * Fraction(size.height, 100)


def _resolve_view_width(value, size, viewport, fraction_unit):
    """Resolve a viewport-width-percentage unit, i.e. ``width: 25vw``."""
    return Fraction(value) * Fraction(viewport.width, 100)


def _resolve_view_height(value, size, viewport, fraction_unit):
    """Resolve a viewport-height-percentage unit, i.e. ``height: 25vh``."""
    return Fraction(value) * Fraction(viewport.height, 100)


# Dispatch table.  PERCENT is *not* an entry — Scalar.resolve substitutes
# the percent_unit's resolver before lookup, matching upstream.  AUTO
# also has no entry: callers must check ``scalar.is_auto`` before calling
# resolve(), and falling through raises ScalarResolveError via the KeyError
# guard in the resolve() body.
RESOLVE_MAP = {
    Unit.CELLS: _resolve_cells,
    Unit.FRACTION: _resolve_fraction,
    Unit.WIDTH: _resolve_width,
    Unit.HEIGHT: _resolve_height,
    Unit.VIEW_WIDTH: _resolve_view_width,
    Unit.VIEW_HEIGHT: _resolve_view_height,
}


def get_symbols(units):
    """Return the symbol strings for an iterable of ``Unit`` members.

    Args:
        units: A number of units.

    Returns:
        List of symbols.
    """
    return [UNIT_SYMBOL[unit] for unit in units]


# ---------------------------------------------------------------------------
# Scalar.  Subclassing ``tuple`` rather than ``NamedTuple`` because the
# typing-shim's ``NamedTuple`` is a ``_Placeholder`` and cannot back a
# user class (the convention mirrors ``_rich/color_triplet.py``).
# Positional construction matches upstream's NamedTuple signature so
# ``Scalar(1.0, Unit.CELLS, Unit.WIDTH)`` and destructuring
# ``value, unit, percent_unit = scalar`` both continue to work.
# ---------------------------------------------------------------------------


class Scalar(tuple):
    """A numeric value paired with a unit and a percent-unit hint."""

    __slots__ = ()

    def __new__(cls, value, unit, percent_unit):
        # Materialise as a 3-tuple so destructuring + index access stay
        # cheap.  ``unit`` and ``percent_unit`` are Unit members; ``value``
        # is float (or int that float() will coerce cleanly).
        return tuple.__new__(cls, (value, unit, percent_unit))

    # Named accessors — equivalent to NamedTuple's auto-generated
    # field properties.  Keep names + docstrings aligned with upstream
    # for tooling and ``help(Scalar)`` ergonomics.

    @property
    def value(self):
        return self[0]

    @property
    def unit(self):
        return self[1]

    @property
    def percent_unit(self):
        return self[2]

    def __str__(self):
        value, unit, _ = self
        if unit == Unit.AUTO:
            return "auto"
        # ``value.is_integer()`` is a float method; ints have no such
        # attribute on MicroPython, so coerce to float first.  The
        # branch matches upstream's "drop the .0 when whole" formatting.
        v = float(value)
        if v == int(v):
            return "{}{}".format(int(v), self.symbol)
        return "{}{}".format(v, self.symbol)

    @property
    def is_cells(self):
        """Check if the Scalar is explicit cells."""
        return self.unit == Unit.CELLS

    @property
    def is_percent(self):
        """Check if the Scalar is a percentage unit."""
        return self.unit == Unit.PERCENT

    @property
    def is_fraction(self):
        """Check if the unit is a fraction."""
        return self.unit == Unit.FRACTION

    @property
    def cells(self):
        """Return the integer cell count, or ``None`` if the unit is not CELLS."""
        value, unit, _ = self
        return int(value) if unit == Unit.CELLS else None

    @property
    def fraction(self):
        """Return the integer fraction count, or ``None`` if the unit is not FRACTION."""
        value, unit, _ = self
        return int(value) if unit == Unit.FRACTION else None

    @property
    def symbol(self):
        """The string suffix for this unit (e.g. ``"fr"``, ``"%"``, ``""``)."""
        return UNIT_SYMBOL[self.unit]

    @property
    def is_auto(self):
        """Check if this is an auto unit."""
        return self.unit == Unit.AUTO

    @classmethod
    def from_number(cls, value):
        """Build a Scalar with an explicit-cells unit.

        Args:
            value: A number of cells.

        Returns:
            New Scalar.
        """
        return cls(float(value), Unit.CELLS, Unit.WIDTH)

    @classmethod
    @lru_cache(maxsize=1024)
    def parse(cls, token, percent_unit=None):
        """Parse a string into a Scalar.

        Args:
            token: A string containing a scalar, e.g. ``"3.14fr"``.
            percent_unit: The unit to substitute for ``%``; defaults to
                ``Unit.WIDTH`` (kept as ``None`` in the signature so the
                ``@lru_cache`` key remains hashable across call sites
                that omit the argument — Unit members hash on identity
                regardless, but ``None`` deduplicates the default-arg
                cache slot).

        Raises:
            ScalarParseError: If the value is not a valid scalar.

        Returns:
            New scalar.
        """
        if percent_unit is None:
            percent_unit = Unit.WIDTH
        if token.lower() == "auto":
            return cls(1.0, Unit.AUTO, Unit.AUTO)
        match = _MATCH_SCALAR(token)
        if match is None:
            raise ScalarParseError("{!r} is not a valid scalar".format(token))
        value, unit_name = match.groups()
        return cls(float(value), SYMBOL_UNIT[unit_name or ""], percent_unit)

    def resolve(self, size, viewport, fraction_unit=None):
        """Resolve a unit-bearing Scalar to a numeric cell dimension.

        Args:
            size: Size of the container.
            viewport: Size of the viewport (typically terminal size).
            fraction_unit: Size of one ``1fr`` slice.  Defaults to a
                unit fraction; the layout engine overrides this once
                it has computed total free space.

        Raises:
            ScalarResolveError: If the unit is unknown (e.g. AUTO has
                been routed here by mistake — callers must branch on
                ``is_auto`` first).

        Returns:
            A ``Fraction`` (the local mixed-precision rational) carrying
            the resolved cell dimension.  The compositor rounds this
            to an int at strip-mapping time.
        """
        value, unit, percent_unit = self

        if unit == Unit.PERCENT:
            unit = percent_unit
        try:
            resolver = RESOLVE_MAP[unit]
        except KeyError:
            raise ScalarResolveError(
                "expected dimensions; found {!r}".format(str(self))
            )
        return resolver(value, size, viewport, fraction_unit or _FRACTION_ONE)

    def copy_with(self, value=None, unit=None, percent_unit=None):
        """Return a Scalar copy with optionally-overridden components.

        Args:
            value: The new value, or None to keep the same value.
            unit: The new unit, or None to keep the same unit.
            percent_unit: The new percent_unit, or None to keep the
                same percent_unit.
        """
        return Scalar(
            value if value is not None else self.value,
            unit if unit is not None else self.unit,
            percent_unit if percent_unit is not None else self.percent_unit,
        )


# ---------------------------------------------------------------------------
# ScalarOffset.  Same NamedTuple-replacement pattern.  Decorated with
# ``_rich_repr.auto(angular=True)`` so it pretty-prints as
# ``<ScalarOffset 10 5>`` rather than the tuple default.  The Rich
# subset's ``auto`` requires an explicit ``__rich_repr__``, which the
# upstream class already provides (D9 — no inspect.signature fallback).
# ---------------------------------------------------------------------------


@_rich_repr.auto(angular=True)
class ScalarOffset(tuple):
    """An Offset built from two ``Scalar`` values.

    Used to animate between two scalar positions in upstream Textual;
    in picolet-tui v0.1 (animation deferred — D7) this carries the
    static layout offset for absolute positioning.
    """

    __slots__ = ()

    def __new__(cls, x, y):
        return tuple.__new__(cls, (x, y))

    @property
    def x(self):
        return self[0]

    @property
    def y(self):
        return self[1]

    @classmethod
    def null(cls):
        """Get a null scalar offset (0, 0)."""
        return NULL_SCALAR

    @classmethod
    def from_offset(cls, offset):
        """Create a ScalarOffset from a (column, row) integer pair.

        Args:
            offset: Offset in cells.

        Returns:
            New offset.
        """
        x, y = offset
        return cls(
            Scalar(x, Unit.CELLS, Unit.WIDTH),
            Scalar(y, Unit.CELLS, Unit.HEIGHT),
        )

    def __bool__(self):
        x, y = self
        return bool(x.value or y.value)

    def __rich_repr__(self):
        # Two unkeyed entries — the @auto decorator wraps the result in
        # ``<ScalarOffset 10 5>`` per the angular=True option above.
        yield None, str(self.x)
        yield None, str(self.y)

    def resolve(self, size, viewport):
        """Resolve the offset into an integer-cell ``Offset``.

        Args:
            size: Size of container.
            viewport: Size of viewport.

        Returns:
            Offset in cells.
        """
        # Deferred import — ``geometry`` lands in the same Phase 4a wave
        # but may not yet have populated the module cache when this
        # file is imported first by the freezer.  Caching the lookup
        # at function scope avoids re-resolving on every call once both
        # modules are loaded.
        from picolet_tui._textual.geometry import Offset  # noqa: PLC0415

        x, y = self
        return Offset(
            round(x.resolve(size, viewport)),
            round(y.resolve(size, viewport)),
        )


NULL_SCALAR = ScalarOffset(Scalar.from_number(0), Scalar.from_number(0))


def percentage_string_to_float(string):
    """Convert a percentage string e.g. ``"20%"`` to a float e.g. ``0.20``.

    The result is clamped to the inclusive ``[0.0, 1.0]`` range for the
    percentage form; bare numeric forms (no trailing ``%``) are returned
    unclamped so callers can use this helper for opacity-style values
    that already arrive in the 0..1 range.

    Args:
        string: The percentage string to convert.

    Returns:
        The float value.
    """
    string = string.strip()
    if string.endswith("%"):
        raw = float(string[:-1]) / 100.0
        # Inline clamp — pulling ``clamp`` from geometry would force the
        # module-level import this file is at pains to avoid (see
        # adjustment 5).  Two comparisons cost less than the import.
        if raw < 0.0:
            return 0.0
        if raw > 1.0:
            return 1.0
        return raw
    return float(string)


__all__ = (
    "Unit",
    "UNIT_SYMBOL",
    "SYMBOL_UNIT",
    "Scalar",
    "ScalarOffset",
    "ScalarError",
    "ScalarResolveError",
    "ScalarParseError",
    "NULL_SCALAR",
    "get_symbols",
    "percentage_string_to_float",
)
