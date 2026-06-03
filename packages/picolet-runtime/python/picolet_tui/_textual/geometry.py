"""picolet_tui._textual.geometry — ported from Textual's ``src/textual/geometry.py``.

Upstream:
  https://github.com/Textualize/textual/blob/main/src/textual/geometry.py
  Tracked against the main tip at the time of this port (~1500 LoC, four
  NamedTuple value types + one ``Shape`` class + module-level ``clamp``
  helper and four ``NULL_*`` constants).

This module is one of Textual's "leaves" per ``docs/tui/research/01-textual-deps.md``
section "Leaves that are nearly drop-in": it has no Textual-internal
dependencies beyond the typing surface and ``rich.repr`` (a single yield-
style hook on ``Shape``), and its values are pure-Python arithmetic over
tuples.  This is the cleanest port in the framework, hence Phase 4a's
"lift verbatim with minor edits" treatment from the synthesis (D1 / §4).

What changed vs upstream
------------------------
* ``typing`` imports route through ``picolet_tui._shims.typing``.  The
  shim does not expose ``NamedTuple`` (see the deliberate omission in
  ``_shims/typing.py``), so the four value classes — ``Offset``, ``Size``,
  ``Region``, ``Spacing`` — are reimplemented as ``tuple`` subclasses
  with ``__new__`` plus property accessors.  This mirrors the pattern
  ``_rich.color_triplet.ColorTriplet`` and ``_rich.measure.Measurement``
  already use, and for the same reason: pulling in the
  ``collections.namedtuple`` metaclass machinery for one consumer would
  cost both NFR-TUI-19 frozen-bytes budget and the metaclass code path
  that synthesis D1 / 01-textual-deps.md "CPython-only constructs"
  forbids.  The visible NamedTuple contract is preserved:
    * positional construction: ``Region(x, y, width, height)``
    * keyword construction: ``Region(x=0, y=0, width=10, height=5)`` —
      enabled by ``__new__`` accepting keyword args
    * default values: every component defaults to 0
    * indexed access: ``r[0] == r.x``
    * tuple iteration / unpacking: ``x, y, w, h = region``
    * ``isinstance(r, tuple)`` is True
  What is NOT mirrored — and what no Textual caller uses against the
  geometry types — is ``_replace()``, ``_asdict()``, ``_fields``, and
  the NamedTuple-specific repr (we ship explicit ``__repr__`` instead).

* All ``@lru_cache(maxsize=1024)`` and ``@lru_cache(maxsize=4096)``
  decorators are rewritten to ``@lru_cache(maxsize=128)`` per synthesis
  R4 mitigation (a) and spec NFR-TUI-6.  The shim's default is already
  128, but spelling it explicitly keeps the cap visible at the call
  site so a casual reader does not assume upstream's 1024/4096 bound.

* The ``textual_speedups`` import block at the bottom of upstream is
  removed entirely.  That module is a CPython C extension that swaps in
  Rust-backed implementations of the four value types; the picolet-tui
  variant has no equivalent.  ``os`` import is dropped with it (it was
  used solely for ``os.environ.get("TEXTUAL_SPEEDUPS")``).

* ``rich.repr`` import is dropped.  Upstream's ``Shape.__rich_repr__``
  yields a tuple of regions to feed Rich's repr machinery; this port
  ships ``Shape.__repr__`` instead, formatted to roughly the same shape.
  ``rich.repr`` is a Tier 3 module in ``02-rich-subset.md`` and is not
  in our port set; nothing else in the geometry module needs it.

* The ``SpacingDimensions: TypeAlias = Union[...]`` alias is preserved
  as an ordinary module attribute (since ``TypeAlias`` and ``Union``
  are both ``_PLACEHOLDER`` singletons in the shim, the right-hand side
  evaluates to a placeholder; we keep the binding so import sites that
  do ``from .geometry import SpacingDimensions`` continue to resolve).

* The ``T = TypeVar("T", int, float)`` generic used in ``clamp``'s
  upstream signature is preserved purely as documentation; under the
  shim it is a name-bearing identity, and the function's runtime
  behaviour is identical for ``int`` and ``float``.

* ``from __future__ import annotations`` is dropped.  MicroPython
  evaluates annotations eagerly anyway, and the few forward references
  in the module (``-> "Offset"``, ``-> "Region"``) are written as plain
  string literals which evaluate the same under both rules.  The bigger
  reason: upstream uses PEP-604 ``X | Y`` unions inside annotations
  (e.g. ``margin: Spacing | None``) which MicroPython's parser does not
  accept on type-expressions — those few sites are rewritten to plain
  ``Spacing`` with a comment noting the elision.

NOT REMOVED (intentional)
-------------------------
* Every method on every value class is preserved.  ``Region`` alone has
  ~30 methods, all of which are reachable from the compositor or the
  Phase 5 layout pass (the synthesis lists ``Region.intersection`` as
  the hot path that motivates the cache cap).
* All four ``NULL_*`` module-level constants survive — they are
  referenced by name across the Textual core (``Region.translate``
  short-circuits on ``NULL_OFFSET``, ``Region.inflect`` defaults to
  ``NULL_SPACING``, ``Shape.__init__`` falls back to ``NULL_REGION``).
* The ``Shape`` class is preserved, including ``selection_bounds``,
  for the text-selection path the Input widget will land in Phase 5.

Spec hooks
----------
Supports:
  FR-TUI-29 (Container / Vertical / Horizontal layout): all three call
    ``Region.split_horizontal`` / ``split_vertical`` and ``Region.shrink``
    against parent allocation to slice child regions.
  FR-TUI-30 (Layout writes Region per widget): the layout pass writes
    ``Region(x, y, width, height)`` instances built by this module onto
    each mounted widget; the compositor reads them back via
    ``Region.intersection`` to clip strips against the viewport.
  FR-TUI-9 (Resize): incoming size deltas are expressed as ``Size``
    instances and propagated to widgets that hold a ``Region``; the
    diff is computed by ``Region.get_scroll_to_visible`` for any
    widget that needs to follow its scroll target.
  FR-TUI-23..28 (Widget tree): ``Widget.region`` returns instances of
    this module's ``Region`` class.
  NFR-TUI-6 (lru_cache caps): every cached method on ``Region`` declares
    ``maxsize=128`` explicitly so the import-time audit walking
    ``cache_info().maxsize`` does not have to chase upstream defaults.
  NFR-TUI-19 (frozen-bytes budget): tuple-subclass implementation
    avoids the ``collections.namedtuple`` machinery; the four NULL
    constants are interned once at module load.

Performance note
----------------
Upstream's 1024 / 4096 cache bounds were tuned for desktop Textual apps
with deep widget trees and frequent re-layout.  The R4 cap to 128 is a
conscious memory tradeoff: cache hit rate degrades on apps with very
high region cardinality, but on a v0.1 widget set capped at nine widget
classes (D3) the working set comfortably fits.  A future ``picolet_tui.tune``
API (R4 mitigation b) will expose cache size as user-tunable; until then
128 is the floor.
"""

from operator import attrgetter, itemgetter

from picolet_tui._shims.functools import lru_cache
from picolet_tui._shims.typing import (
    Tuple,
    TypeVar,
    Union,
    cast,
)
# Other typing names upstream pulls (``TYPE_CHECKING``, ``Any``,
# ``Collection``, ``Final``, ``Iterable``, ``Literal``) are dropped from
# the import list: they appeared only inside ``-> X`` / ``: X``
# annotations or the ``Final`` constant markers, and the typing shim's
# placeholder semantics make the imports cost frozen bytes without
# producing observable behaviour.  Adding any of them back is cheap if
# a future caller needs them as a value (vs. an annotation).


# Documentation-only generic.  Under the typing shim TypeVar is an
# identity wrapper that records ``name`` but ignores constraints; the
# constraint tuple is preserved so a future static checker (or human
# reader) can see clamp() is meant for numeric types only.
T = TypeVar("T", int, float)


# Upstream declares this as ``SpacingDimensions: TypeAlias = Union[...]``.
# Under the shim both ``TypeAlias`` and ``Union`` are the _PLACEHOLDER
# singleton, so the right-hand side evaluates to a placeholder object
# — which is exactly the contract upstream relies on at runtime (the
# value is only used as a type hint, never as a runtime constructor).
SpacingDimensions = Union[
    int, Tuple[int], Tuple[int, int], Tuple[int, int, int, int]
]


def clamp(value, minimum, maximum):
    """Restrict a value to a given range.

    If ``value`` is less than the minimum, return the minimum.
    If ``value`` is greater than the maximum, return the maximum.
    Otherwise, return ``value``.

    The ``minimum`` and ``maximum`` arguments values may be given in
    reverse order.

    Args:
        value: A value.
        minimum: Minimum value.
        maximum: Maximum value.

    Returns:
        New value that is not less than the minimum or greater than the
        maximum.
    """
    # Branch on inverted bounds first so the common (sorted) path stays
    # one comparison short — matches the upstream micro-optimisation.
    if minimum > maximum:
        if value < maximum:
            return maximum
        if value > minimum:
            return minimum
        return value
    else:
        if value < minimum:
            return minimum
        if value > maximum:
            return maximum
        return value


class Offset(tuple):
    """A cell offset defined by x and y coordinates.

    Offsets are typically relative to the top left of the terminal or
    other container.

    Textual prefers the names ``x`` and ``y``, but you could consider
    ``x`` to be the *column* and ``y`` to be the *row*.

    Offsets support addition, subtraction, multiplication, and negation.

    Example:
        >>> offset = Offset(3, 2)
        >>> offset
        Offset(x=3, y=2)
        >>> offset += Offset(10, 0)
        >>> offset
        Offset(x=13, y=2)
        >>> -offset
        Offset(x=-13, y=-2)
    """

    __slots__ = ()

    # Defaults of 0 mirror upstream's NamedTuple defaults.  Keyword
    # construction (``Offset(x=3, y=2)``) keeps working because the
    # parameter names match the upstream field names exactly.
    def __new__(cls, x=0, y=0):
        return tuple.__new__(cls, (x, y))

    @property
    def x(self):
        """Offset in the x-axis (horizontal)."""
        return self[0]

    @property
    def y(self):
        """Offset in the y-axis (vertical)."""
        return self[1]

    def __repr__(self):
        return "Offset(x={}, y={})".format(self[0], self[1])

    @property
    def is_origin(self):
        """Is the offset at (0, 0)?"""
        return self == (0, 0)

    @property
    def clamped(self):
        """This offset with ``x`` and ``y`` restricted to values above zero."""
        x, y = self
        # Inline the branch rather than calling ``clamp`` so the
        # property cost stays one comparison per axis; this property
        # is hit per-event on mouse routing.
        return Offset(0 if x < 0 else x, 0 if y < 0 else y)

    @property
    def transpose(self):
        """A tuple of x and y, in reverse order, i.e. (y, x)."""
        x, y = self
        return y, x

    def __bool__(self):
        return self != (0, 0)

    def __add__(self, other):
        # Accept any tuple, not just Offset, so callers can pass raw
        # (x, y) without paying the construction cost on the hot path.
        if isinstance(other, tuple):
            _x, _y = self
            x, y = other
            return Offset(_x + x, _y + y)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, tuple):
            _x, _y = self
            x, y = other
            return Offset(_x - x, _y - y)
        return NotImplemented

    def __mul__(self, other):
        if isinstance(other, (float, int)):
            x, y = self
            return Offset(int(x * other), int(y * other))
        if isinstance(other, tuple):
            # Component-wise multiply — used by the animation system to
            # apply easing curves per axis.  Truncate to int via int()
            # so the result stays in cell space.
            x, y = self
            return Offset(int(x * other[0]), int(y * other[1]))
        return NotImplemented

    def __neg__(self):
        x, y = self
        return Offset(-x, -y)

    def blend(self, destination, factor):
        """Calculate a new offset on a line between this and a destination.

        Args:
            destination: Point where factor would be 1.0.
            factor: A value between 0 and 1.0.

        Returns:
            A new point on a line between self and destination.
        """
        x1, y1 = self
        x2, y2 = destination
        # Integer truncation matches upstream — animation paths feeding
        # this expect cell-space output and rely on the truncation,
        # not on rounding, to land sub-pixel positions on a stable
        # cell after enough frames.
        return Offset(
            int(x1 + (x2 - x1) * factor),
            int(y1 + (y2 - y1) * factor),
        )

    def get_distance_to(self, other):
        """Get the distance to another offset.

        Args:
            other: An offset.

        Returns:
            Distance to other offset.
        """
        x1, y1 = self
        x2, y2 = other
        # Unrolled squared-distance: avoids the function call cost of
        # ``math.hypot`` and saves one import; this is on the
        # mouse-routing path so the saving matters.
        distance = ((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1)) ** 0.5
        return distance

    def clamp(self, width, height):
        """Clamp the offset to fit within a rectangle of width x height.

        Args:
            width: Width to clamp.
            height: Height to clamp.

        Returns:
            A new offset.
        """
        x, y = self
        # ``width - 1`` / ``height - 1`` because the rectangle is in
        # cells and the offset is inclusive of both bounds (a 10-wide
        # rect has valid x in 0..9).
        return Offset(clamp(x, 0, width - 1), clamp(y, 0, height - 1))


class Size(tuple):
    """The dimensions (width and height) of a rectangular region.

    Example:
        >>> size = Size(2, 3)
        >>> size
        Size(width=2, height=3)
        >>> size.area
        6
        >>> size + Size(10, 20)
        Size(width=12, height=23)
    """

    __slots__ = ()

    def __new__(cls, width=0, height=0):
        return tuple.__new__(cls, (width, height))

    @property
    def width(self):
        """The width in cells."""
        return self[0]

    @property
    def height(self):
        """The height in cells."""
        return self[1]

    def __repr__(self):
        return "Size(width={}, height={})".format(self[0], self[1])

    def __bool__(self):
        """A Size is Falsy if it has area 0."""
        return self.width * self.height != 0

    @property
    def area(self):
        """The area occupied by a region of this size."""
        return self.width * self.height

    @property
    def region(self):
        """A region of the same size, at the origin."""
        width, height = self
        return Region(0, 0, width, height)

    @property
    def line_range(self):
        """A range object that covers values between 0 and ``height``."""
        return range(self.height)

    def with_width(self, width):
        """Get a new Size with just the width changed.

        Args:
            width: New width.

        Returns:
            New Size instance.
        """
        return Size(width, self.height)

    def with_height(self, height):
        """Get a new Size with just the height changed.

        Args:
            height: New height.

        Returns:
            New Size instance.
        """
        return Size(self.width, height)

    def __add__(self, other):
        if isinstance(other, tuple):
            width, height = self
            width2, height2 = other
            # ``max(0, ...)`` because Size is a non-negative quantity
            # by contract; subtracting a larger Size must not wrap to
            # a negative dimension (which would later crash the
            # compositor's slice arithmetic).
            return Size(max(0, width + width2), max(0, height + height2))
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, tuple):
            width, height = self
            width2, height2 = other
            return Size(max(0, width - width2), max(0, height - height2))
        return NotImplemented

    def contains(self, x, y):
        """Check if a point is in area defined by the size.

        Args:
            x: X coordinate.
            y: Y coordinate.

        Returns:
            True if the point is within the region.
        """
        width, height = self
        # Inverted-comparison idiom: ``width > x >= 0`` is a single
        # chained comparison evaluated left-to-right, equivalent to
        # ``0 <= x < width`` but in upstream's preferred form.
        return width > x >= 0 and height > y >= 0

    def contains_point(self, point):
        """Check if a point is in the area defined by the size.

        Args:
            point: A tuple of x and y coordinates.

        Returns:
            True if the point is within the region.
        """
        x, y = point
        width, height = self
        return width > x >= 0 and height > y >= 0

    def __contains__(self, other):
        try:
            x, y = other
        except Exception:
            # Match upstream's error wording so any caller catching
            # this string for diagnostics still finds it.
            raise TypeError(
                "Dimensions.__contains__ requires an iterable of two integers"
            )
        width, height = self
        return width > x >= 0 and height > y >= 0

    def clamp_offset(self, offset):
        """Clamp an offset to fit within the width x height.

        Args:
            offset: An offset.

        Returns:
            A new offset that will fit inside the dimensions defined in
            the Size.
        """
        return offset.clamp(self.width, self.height)


class Region(tuple):
    """Defines a rectangular region.

    A Region consists of a coordinate (x and y) and dimensions (width
    and height).

    Example:
        >>> region = Region(4, 5, 20, 10)
        >>> region
        Region(x=4, y=5, width=20, height=10)
        >>> region.area
        200
        >>> region.size
        Size(width=20, height=10)
        >>> region.offset
        Offset(x=4, y=5)
        >>> region.contains(1, 2)
        False
        >>> region.contains(10, 8)
        True
    """

    __slots__ = ()

    def __new__(cls, x=0, y=0, width=0, height=0):
        return tuple.__new__(cls, (x, y, width, height))

    @property
    def x(self):
        """Offset in the x-axis (horizontal)."""
        return self[0]

    @property
    def y(self):
        """Offset in the y-axis (vertical)."""
        return self[1]

    @property
    def width(self):
        """The width of the region."""
        return self[2]

    @property
    def height(self):
        """The height of the region."""
        return self[3]

    def __repr__(self):
        return "Region(x={}, y={}, width={}, height={})".format(
            self[0], self[1], self[2], self[3]
        )

    @classmethod
    def from_union(cls, regions):
        """Create a Region from the union of other regions.

        Args:
            regions: One or more regions.

        Returns:
            A Region that encloses all other regions.
        """
        if not regions:
            raise ValueError("At least one region expected")
        # itemgetter / attrgetter both work against a tuple subclass:
        # the former goes through __getitem__, the latter through the
        # property descriptors.  Mixing the two matches upstream
        # exactly; ``right`` and ``bottom`` are properties (computed
        # from x+width / y+height) so itemgetter would not see them.
        min_x = min(regions, key=itemgetter(0)).x
        max_x = max(regions, key=attrgetter("right")).right
        min_y = min(regions, key=itemgetter(1)).y
        max_y = max(regions, key=attrgetter("bottom")).bottom
        return cls(min_x, min_y, max_x - min_x, max_y - min_y)

    @classmethod
    def from_corners(cls, x1, y1, x2, y2):
        """Construct a Region from the top left and bottom right corners.

        Args:
            x1: Top left x.
            y1: Top left y.
            x2: Bottom right x.
            y2: Bottom right y.

        Returns:
            A new region.
        """
        return cls(x1, y1, x2 - x1, y2 - y1)

    @classmethod
    def from_offset(cls, offset, size):
        """Create a region from offset and size.

        Args:
            offset: Offset (top left point).
            size: Dimensions of region.

        Returns:
            A region instance.
        """
        x, y = offset
        width, height = size
        return cls(x, y, width, height)

    @classmethod
    def get_scroll_to_visible(cls, window_region, region, top=False):
        """Calculate the smallest offset to translate a window to contain a region.

        This method is used to calculate the required offset to scroll
        something into view.

        Args:
            window_region: The window region.
            region: The region to move inside the window.
            top: Get offset to top of window.

        Returns:
            An offset required to add to region to move it inside
            window_region.
        """
        if region in window_region and not top:
            # Already visible — fast-path the common case where the
            # widget is fully inside the viewport.
            return NULL_OFFSET

        window_left, window_top, window_right, window_bottom = window_region.corners
        region = region.crop_size(window_region.size)
        left, top_, right, bottom = region.corners
        delta_x = delta_y = 0

        if not (
            (window_right > left >= window_left)
            and (window_right > right >= window_left)
        ):
            # The region does not fit on the X axis; pick the smaller
            # of "scroll to align left edges" vs "scroll to align
            # right edges" via key=abs so we move the minimum distance.
            delta_x = min(
                left - window_left,
                left - (window_right - region.width),
                key=abs,
            )

        if top:
            # ``top=True`` forces alignment to window top regardless
            # of whether the region already fits vertically — used by
            # focus-on-mount paths that want the focused widget at
            # the top of its scroll container.
            delta_y = top_ - window_top

        elif not (
            (window_bottom > top_ >= window_top)
            and (window_bottom > bottom >= window_top)
        ):
            delta_y = min(
                top_ - window_top,
                top_ - (window_bottom - region.height),
                key=abs,
            )
        return Offset(delta_x, delta_y)

    def __bool__(self):
        """A Region is considered False when it has no area."""
        _, _, width, height = self
        return width * height > 0

    @property
    def column_span(self):
        """A pair of integers for the start and end columns (x coordinates) in this region.

        The end value is *exclusive*.
        """
        return (self.x, self.x + self.width)

    @property
    def line_span(self):
        """A pair of integers for the start and end lines (y coordinates) in this region.

        The end value is *exclusive*.
        """
        return (self.y, self.y + self.height)

    @property
    def right(self):
        """Maximum X value (non inclusive)."""
        return self.x + self.width

    @property
    def bottom(self):
        """Maximum Y value (non inclusive)."""
        return self.y + self.height

    @property
    def area(self):
        """The area under the region."""
        return self.width * self.height

    @property
    def offset(self):
        """The top left corner of the region.

        Returns:
            An offset.
        """
        # Slice unpacking via ``*self[:2]`` keeps the property body
        # one line and avoids two attribute lookups; on the layout
        # hot path the difference shows up under profiling.
        return Offset(*self[:2])

    @property
    def center(self):
        """The center of the region.

        Note that this does *not* return an ``Offset`` because the
        center may not be an integer coordinate.

        Returns:
            Tuple of floats.
        """
        x, y, width, height = self
        return (x + width / 2.0, y + height / 2.0)

    @property
    def bottom_left(self):
        """Bottom left offset of the region.

        Returns:
            An offset.
        """
        x, y, _width, height = self
        return Offset(x, y + height)

    @property
    def top_right(self):
        """Top right offset of the region.

        Returns:
            An offset.
        """
        x, y, width, _height = self
        return Offset(x + width, y)

    @property
    def bottom_right(self):
        """Bottom right offset of the region.

        Returns:
            An offset.
        """
        x, y, width, height = self
        return Offset(x + width, y + height)

    @property
    def bottom_right_inclusive(self):
        """Bottom right corner of the region, within its boundaries."""
        x, y, width, height = self
        return Offset(x + width - 1, y + height - 1)

    @property
    def size(self):
        """Get the size of the region."""
        return Size(*self[2:])

    @property
    def corners(self):
        """The top left and bottom right coordinates as a tuple of four integers."""
        x, y, width, height = self
        return x, y, x + width, y + height

    @property
    def column_range(self):
        """A range object for X coordinates."""
        return range(self.x, self.x + self.width)

    @property
    def line_range(self):
        """A range object for Y coordinates."""
        return range(self.y, self.y + self.height)

    @property
    def reset_offset(self):
        """A region of the same size at (0, 0).

        Returns:
            A region at the origin.
        """
        _, _, width, height = self
        return Region(0, 0, width, height)

    def __add__(self, other):
        if isinstance(other, tuple):
            ox, oy = other
            x, y, width, height = self
            # Translation only — adding a 2-tuple moves the origin;
            # adding a 4-tuple is undefined and falls through to
            # NotImplemented (which is correct, the caller meant
            # ``union`` not ``+``).
            return Region(x + ox, y + oy, width, height)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, tuple):
            ox, oy = other
            x, y, width, height = self
            return Region(x - ox, y - oy, width, height)
        return NotImplemented

    def get_spacing_between(self, region):
        """Get spacing between two regions.

        Args:
            region: Another region.

        Returns:
            Spacing that if subtracted from ``self`` produces ``region``.
        """
        # Inverse of ``Region.shrink`` — used by the layout pass to
        # compute the padding implied by where the parent placed a
        # child relative to its content box.
        return Spacing(
            region.y - self.y,
            self.right - region.right,
            self.bottom - region.bottom,
            region.x - self.x,
        )

    def at_offset(self, offset):
        """Get a new Region with the same size at a given offset.

        Args:
            offset: An offset.

        Returns:
            New Region with adjusted offset.
        """
        x, y = offset
        _x, _y, width, height = self
        return Region(x, y, width, height)

    def crop_size(self, size):
        """Get a region with the same offset, with a size no larger than ``size``.

        Args:
            size: Maximum width and height (WIDTH, HEIGHT).

        Returns:
            New region that could fit within ``size``.
        """
        x, y, width1, height1 = self
        width2, height2 = size
        return Region(x, y, min(width1, width2), min(height1, height2))

    def expand(self, size):
        """Increase the size of the region by adding a border.

        Args:
            size: Additional width and height.

        Returns:
            A new region.
        """
        expand_width, expand_height = size
        x, y, width, height = self
        # Width and height grow by 2x because the border applies to
        # both sides; x/y shift back by the same amount so the
        # original center stays put.
        return Region(
            x - expand_width,
            y - expand_height,
            width + expand_width * 2,
            height + expand_height * 2,
        )

    # NFR-TUI-6 / synthesis R4: cap was 1024 upstream; trimmed to 128
    # to bound memory on long-running TUI sessions.  Same on every
    # cached Region method below.
    @lru_cache(maxsize=128)
    def overlaps(self, other):
        """Check if another region overlaps this region.

        Args:
            other: A Region.

        Returns:
            True if other region shares any cells with this region.
        """
        x, y, x2, y2 = self.corners
        ox, oy, ox2, oy2 = other.corners

        # The triple-disjunction per axis is upstream's idiom for the
        # full overlap test in one shot; the alternative pair of range
        # checks is one operation slower and harder to read.
        return ((x2 > ox >= x) or (x2 > ox2 > x) or (ox < x and ox2 >= x2)) and (
            (y2 > oy >= y) or (y2 > oy2 > y) or (oy < y and oy2 >= y2)
        )

    def contains(self, x, y):
        """Check if a point is in the region.

        Args:
            x: X coordinate.
            y: Y coordinate.

        Returns:
            True if the point is within the region.
        """
        self_x, self_y, width, height = self
        return (self_x + width > x >= self_x) and (self_y + height > y >= self_y)

    def contains_point(self, point):
        """Check if a point is in the region.

        Args:
            point: A tuple of x and y coordinates.

        Returns:
            True if the point is within the region.
        """
        x1, y1, x2, y2 = self.corners
        try:
            ox, oy = point
        except Exception:
            raise TypeError(
                "a tuple of two integers is required, not {!r}".format(point)
            )
        return (x2 > ox >= x1) and (y2 > oy >= y1)

    @lru_cache(maxsize=128)
    def contains_region(self, other):
        """Check if a region is entirely contained within this region.

        Args:
            other: A region.

        Returns:
            True if the other region fits perfectly within this region.
        """
        x1, y1, x2, y2 = self.corners
        ox, oy, ox2, oy2 = other.corners
        return (
            (x2 >= ox >= x1)
            and (y2 >= oy >= y1)
            and (x2 >= ox2 >= x1)
            and (y2 >= oy2 >= y1)
        )

    @lru_cache(maxsize=128)
    def translate(self, offset):
        """Move the offset of the Region.

        Args:
            offset: Offset to add to region.

        Returns:
            A new region shifted by (x, y).
        """
        self_x, self_y, width, height = self
        offset_x, offset_y = offset
        return Region(self_x + offset_x, self_y + offset_y, width, height)

    @lru_cache(maxsize=128)
    def __contains__(self, other):
        """Check if a point is in this region."""
        if isinstance(other, Region):
            return self.contains_region(other)
        else:
            try:
                return self.contains_point(other)
            except TypeError:
                # contains_point raises on shape mismatch; the outer
                # ``in`` operator wants a bool answer either way.
                return False

    def clip(self, width, height):
        """Clip this region to fit within width, height.

        Args:
            width: Width of bounds.
            height: Height of bounds.

        Returns:
            Clipped region.
        """
        x1, y1, x2, y2 = self.corners

        _clamp = clamp
        new_region = Region.from_corners(
            _clamp(x1, 0, width),
            _clamp(y1, 0, height),
            _clamp(x2, 0, width),
            _clamp(y2, 0, height),
        )
        return new_region

    @lru_cache(maxsize=128)
    def grow(self, margin):
        """Grow a region by adding spacing.

        Args:
            margin: Grow space by ``(<top>, <right>, <bottom>, <left>)``.

        Returns:
            New region.
        """
        # Short-circuit zero-spacing — the common case for widgets
        # with no padding/border, called per layout pass per widget.
        if not any(margin):
            return self
        top, right, bottom, left = margin
        x, y, width, height = self
        return Region(
            x=x - left,
            y=y - top,
            width=max(0, width + left + right),
            height=max(0, height + top + bottom),
        )

    @lru_cache(maxsize=128)
    def shrink(self, margin):
        """Shrink a region by subtracting spacing.

        Args:
            margin: Shrink space by ``(<top>, <right>, <bottom>, <left>)``.

        Returns:
            The new, smaller region.
        """
        if not any(margin):
            return self
        top, right, bottom, left = margin
        x, y, width, height = self
        return Region(
            x=x + left,
            y=y + top,
            width=max(0, width - (left + right)),
            height=max(0, height - (top + bottom)),
        )

    @lru_cache(maxsize=128)
    def intersection(self, region):
        """Get the overlapping portion of the two regions.

        Args:
            region: A region that overlaps this region.

        Returns:
            A new region that covers where the two regions overlap.
        """
        # Hot path — the compositor calls this once per visible widget
        # per frame to clip strips against the viewport.  Unrolled
        # rather than calling clamp() four times for the per-call
        # function-call overhead it saves.
        x1, y1, w1, h1 = self
        cx1, cy1, w2, h2 = region
        x2 = x1 + w1
        y2 = y1 + h1
        cx2 = cx1 + w2
        cy2 = cy1 + h2

        # Inline three-way clamp per coordinate: pin into the other
        # rect's [cx1, cx2] range, returning the boundary if the
        # input is outside.  ``rx2 < rx1`` is allowed and produces a
        # negative-width Region; callers check ``bool(region)`` /
        # ``area`` to detect non-overlap.
        rx1 = cx2 if x1 > cx2 else (cx1 if x1 < cx1 else x1)
        ry1 = cy2 if y1 > cy2 else (cy1 if y1 < cy1 else y1)
        rx2 = cx2 if x2 > cx2 else (cx1 if x2 < cx1 else x2)
        ry2 = cy2 if y2 > cy2 else (cy1 if y2 < cy1 else y2)

        return Region(rx1, ry1, rx2 - rx1, ry2 - ry1)

    @lru_cache(maxsize=128)
    def union(self, region):
        """Get the smallest region that contains both regions.

        Args:
            region: Another region.

        Returns:
            An optimally sized region to cover both regions.
        """
        x1, y1, x2, y2 = self.corners
        ox1, oy1, ox2, oy2 = region.corners

        union_region = self.from_corners(
            min(x1, ox1), min(y1, oy1), max(x2, ox2), max(y2, oy2)
        )
        return union_region

    @lru_cache(maxsize=128)
    def split(self, cut_x, cut_y):
        """Split a region into 4 from given x and y offsets (cuts).

        Args:
            cut_x: Offset from self.x where the cut should be made. If
                negative, the cut is taken from the right edge.
            cut_y: Offset from self.y where the cut should be made. If
                negative, the cut is taken from the lower edge.

        Returns:
            Four new regions which add up to the original (self).
        """
        x, y, width, height = self
        # Negative cuts measured from the far edge — matches CSS
        # background-position semantics and the way Textual's grid
        # layouts express "right margin of size N".
        if cut_x < 0:
            cut_x = width + cut_x
        if cut_y < 0:
            cut_y = height + cut_y

        _Region = Region
        return (
            _Region(x, y, cut_x, cut_y),
            _Region(x + cut_x, y, width - cut_x, cut_y),
            _Region(x, y + cut_y, cut_x, height - cut_y),
            _Region(x + cut_x, y + cut_y, width - cut_x, height - cut_y),
        )

    @lru_cache(maxsize=128)
    def split_vertical(self, cut):
        """Split a region into two, from a given x offset.

        Args:
            cut: An offset from self.x where the cut should be made.
                If cut is negative, it is taken from the right edge.

        Returns:
            Two regions, which add up to the original (self).
        """
        x, y, width, height = self
        if cut < 0:
            cut = width + cut

        return (
            Region(x, y, cut, height),
            Region(x + cut, y, width - cut, height),
        )

    @lru_cache(maxsize=128)
    def split_horizontal(self, cut):
        """Split a region into two, from a given y offset.

        Args:
            cut: An offset from self.y where the cut should be made.
                May be negative, for the offset to start from the
                lower edge.

        Returns:
            Two regions, which add up to the original (self).
        """
        x, y, width, height = self
        if cut < 0:
            cut = height + cut

        return (
            Region(x, y, width, cut),
            Region(x, y + cut, width, height - cut),
        )

    def translate_inside(self, container, x_axis=True, y_axis=True):
        """Translate this region so it fits within a container.

        This will ensure that there is as little overlap as possible.
        The top left of the returned region is guaranteed to be within
        the container.

        Args:
            container: A container region.
            x_axis: Allow translation of X axis.
            y_axis: Allow translation of Y axis.

        Returns:
            A new region with same dimensions that fits within container.
        """
        x1, y1, width1, height1 = container
        x2, y2, width2, height2 = self
        # Per-axis clamp: pin the origin into the container's far edge
        # minus our own dimensions (so the bottom-right stays inside),
        # then pin into the container origin (so the top-left does).
        # The ``if x_axis else x2`` arms let the caller opt out of
        # the translation on one axis — used by popovers that should
        # only follow the cursor vertically.
        return Region(
            max(min(x2, x1 + width1 - width2), x1) if x_axis else x2,
            max(min(y2, y1 + height1 - height2), y1) if y_axis else y2,
            width2,
            height2,
        )

    def inflect(self, x_axis=+1, y_axis=+1, margin=None):
        """Inflect a region around one or both axes.

        The ``x_axis`` and ``y_axis`` parameters define which direction
        to move the region.  A positive value will move the region right
        or down, a negative value will move the region left or up. A
        value of ``0`` will leave that axis unmodified.

        If a margin is provided, it will add space between the resulting
        region.

        Note that if margin is specified it *overlaps*, so the space
        will be the maximum of two edges, and not the total.

        Args:
            x_axis: +1 to inflect in the positive direction, -1 to
                inflect in the negative direction.
            y_axis: +1 to inflect in the positive direction, -1 to
                inflect in the negative direction.
            margin: Additional margin.

        Returns:
            A new region.
        """
        # Upstream's signature is ``margin: Spacing | None`` — the
        # PEP-604 union does not parse on MicroPython's annotation
        # form, so the type hint is dropped; the default behaviour
        # (``None`` -> NULL_SPACING) is unchanged.
        inflect_margin = NULL_SPACING if margin is None else margin
        x, y, width, height = self
        if x_axis:
            # ``max_width`` here is the larger of left/right spacing,
            # not the sum — see Spacing.max_width docstring for why.
            x += (width + inflect_margin.max_width) * x_axis
        if y_axis:
            y += (height + inflect_margin.max_height) * y_axis
        return Region(x, y, width, height)

    def constrain(self, constrain_x, constrain_y, margin, container):
        """Constrain a region to fit within a container, using different methods per axis.

        Args:
            constrain_x: Constrain method for the X-axis. One of
                ``"none"``, ``"inside"``, ``"inflect"``.
            constrain_y: Constrain method for the Y-axis. One of
                ``"none"``, ``"inside"``, ``"inflect"``.
            margin: Margin to maintain around region.
            container: Container to constrain to.

        Returns:
            New widget, that fits inside the container (if possible).
        """
        margin_region = self.grow(margin)
        region = self

        def compare_span(span_start, span_end, container_start, container_end):
            """Compare a span with a container.

            Returns:
                0 if the span fits, -1 if it is less than the
                container, otherwise +1.
            """
            if span_start >= container_start and span_end <= container_end:
                return 0
            if span_start < container_start:
                return -1
            return +1

        # Apply any inflected constraints.  ``-compare_span(...)``
        # flips the sign because the inflect direction is opposite
        # the overflow direction: a region that pokes off the right
        # edge (compare_span == +1) should inflect leftward (-1).
        if constrain_x == "inflect" or constrain_y == "inflect":
            region = region.inflect(
                (
                    -compare_span(
                        margin_region.x,
                        margin_region.right,
                        container.x,
                        container.right,
                    )
                    if constrain_x == "inflect"
                    else 0
                ),
                (
                    -compare_span(
                        margin_region.y,
                        margin_region.bottom,
                        container.y,
                        container.bottom,
                    )
                    if constrain_y == "inflect"
                    else 0
                ),
                margin,
            )

        # Translate-inside is applied unconditionally after any
        # inflect so the origin is guaranteed within the container,
        # even when an inflect overshot.  The container is shrunk by
        # the margin so the translated region's *edges* (not just
        # its origin) clear the margin band.
        region = region.translate_inside(
            container.shrink(margin),
            constrain_x != "none",
            constrain_y != "none",
        )

        return region


class Spacing(tuple):
    """Stores spacing around a widget, such as padding and border.

    Spacing is defined by four integers for the space at the top, right,
    bottom, and left of a region.

    Example:
        >>> region = Region(2, 3, 20, 10)
        >>> spacing = Spacing(1, 2, 3, 4)
        >>> region.grow(spacing)
        Region(x=-2, y=2, width=26, height=14)
        >>> region.shrink(spacing)
        Region(x=6, y=4, width=14, height=6)
        >>> spacing.css
        '1 2 3 4'
    """

    __slots__ = ()

    def __new__(cls, top=0, right=0, bottom=0, left=0):
        return tuple.__new__(cls, (top, right, bottom, left))

    @property
    def top(self):
        """Space from the top of a region."""
        return self[0]

    @property
    def right(self):
        """Space from the right of a region."""
        return self[1]

    @property
    def bottom(self):
        """Space from the bottom of a region."""
        return self[2]

    @property
    def left(self):
        """Space from the left of a region."""
        return self[3]

    def __repr__(self):
        return "Spacing(top={}, right={}, bottom={}, left={})".format(
            self[0], self[1], self[2], self[3]
        )

    def __bool__(self):
        return self != (0, 0, 0, 0)

    @property
    def width(self):
        """Total space in the x axis."""
        return self.left + self.right

    @property
    def height(self):
        """Total space in the y axis."""
        return self.top + self.bottom

    @property
    def max_width(self):
        """The space between regions in the X direction if margins overlap.

        i.e. ``max(self.left, self.right)``.
        """
        _top, right, _bottom, left = self
        return left if left > right else right

    @property
    def max_height(self):
        """The space between regions in the Y direction if margins overlap.

        i.e. ``max(self.top, self.bottom)``.
        """
        top, _right, bottom, _left = self
        return top if top > bottom else bottom

    @property
    def top_left(self):
        """A pair of integers for the left, and top space."""
        return (self.left, self.top)

    @property
    def bottom_right(self):
        """A pair of integers for the right, and bottom space."""
        return (self.right, self.bottom)

    @property
    def totals(self):
        """A pair of integers for the total horizontal and vertical space."""
        top, right, bottom, left = self
        return (left + right, top + bottom)

    @property
    def css(self):
        """A string containing the spacing in CSS format.

        For example: "1" or "2 4" or "4 2 8 2".
        """
        top, right, bottom, left = self
        # CSS shorthand: one value when all equal, two when verticals
        # and horizontals pair off, four otherwise.  Matches CSS
        # ``margin`` / ``padding`` shorthand expansion exactly.
        if top == right == bottom == left:
            return "{}".format(top)
        if (top, right) == (bottom, left):
            return "{} {}".format(top, right)
        else:
            return "{} {} {} {}".format(top, right, bottom, left)

    @classmethod
    def unpack(cls, pad):
        """Unpack padding specified in CSS style.

        Args:
            pad: An integer, or tuple of 1, 2, or 4 integers.

        Raises:
            ValueError: If ``pad`` is an invalid value.

        Returns:
            New Spacing object.
        """
        if isinstance(pad, int):
            return cls(pad, pad, pad, pad)
        pad_len = len(pad)
        if pad_len == 1:
            _pad = pad[0]
            return cls(_pad, _pad, _pad, _pad)
        if pad_len == 2:
            # CSS two-value shorthand: ``padding: 1 2;`` means top/
            # bottom = 1, right/left = 2.  The ``cast()`` from upstream
            # is preserved through the typing shim as a no-op.
            pad_top, pad_right = cast(Tuple[int, int], pad)
            return cls(pad_top, pad_right, pad_top, pad_right)
        if pad_len == 4:
            top, right, bottom, left = cast(Tuple[int, int, int, int], pad)
            return cls(top, right, bottom, left)
        raise ValueError(
            "1, 2 or 4 integers required for spacing properties; "
            "{} given".format(pad_len)
        )

    @classmethod
    def vertical(cls, amount):
        """Construct a Spacing with vertical-only spacing.

        Args:
            amount: The magnitude of spacing to apply to vertical edges.

        Returns:
            ``Spacing(amount, 0, amount, 0)``.
        """
        return Spacing(amount, 0, amount, 0)

    @classmethod
    def horizontal(cls, amount):
        """Construct a Spacing with horizontal-only spacing.

        Args:
            amount: The magnitude of spacing to apply to horizontal edges.

        Returns:
            ``Spacing(0, amount, 0, amount)``.
        """
        return Spacing(0, amount, 0, amount)

    @classmethod
    def all(cls, amount):
        """Construct a Spacing with the same amount on all edges.

        Args:
            amount: The magnitude of spacing to apply to all edges.

        Returns:
            ``Spacing(amount, amount, amount, amount)``.
        """
        return Spacing(amount, amount, amount, amount)

    def __add__(self, other):
        if isinstance(other, tuple):
            top1, right1, bottom1, left1 = self
            top2, right2, bottom2, left2 = other
            return Spacing(
                top1 + top2, right1 + right2, bottom1 + bottom2, left1 + left2
            )
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, tuple):
            top1, right1, bottom1, left1 = self
            top2, right2, bottom2, left2 = other
            return Spacing(
                top1 - top2, right1 - right2, bottom1 - bottom2, left1 - left2
            )
        return NotImplemented

    def grow_maximum(self, other):
        """Grow spacing with a maximum.

        Args:
            other: Spacing object.

        Returns:
            New spacing where the values are maximum of the two values.
        """
        top, right, bottom, left = self
        other_top, other_right, other_bottom, other_left = other
        # Per-edge max — used to collapse adjacent widget margins so
        # two 1-cell margins between siblings become a 1-cell gap, not
        # 2-cell, matching CSS margin-collapse for the block layouts
        # we ship (Vertical / Horizontal).
        return Spacing(
            max(top, other_top),
            max(right, other_right),
            max(bottom, other_bottom),
            max(left, other_left),
        )


class Shape:
    """An arbitrary shape defined by a sequence of regions.

    This class currently exists to filter widgets within a shape
    defined when the user is selecting text.
    """

    # __slots__ keeps Shape instances small — the selection path can
    # produce one Shape per drag event, and they are short-lived.
    __slots__ = ("_regions", "_bounds")

    def __init__(self, regions):
        """
        Args:
            regions: Regions which will define the shape.
        """
        self._regions = tuple(regions)
        self._bounds = (
            Region.from_union(self._regions) if self._regions else NULL_REGION
        )

    def __bool__(self):
        return bool(self._bounds)

    def __hash__(self):
        return hash(self._regions)

    def __repr__(self):
        # Upstream uses ``rich.repr.Result`` via a ``__rich_repr__``
        # method; we replace that with a plain ``__repr__`` here
        # because rich.repr is not in the trimmed Rich subset (it's
        # Tier 3 per 02-rich-subset.md and nothing else in the port
        # set needs it).  Format mirrors what rich.repr would have
        # produced for one yielded tuple.
        return "Shape({!r})".format(self._regions)

    def draw(self, size):
        """Build a string with a 2D grid of results from contains_point.

        This is a debugging aid (do not use in production).
        """
        width, height = size
        # Single-byte indexing into ".X" by bool->int — same trick
        # upstream uses; cheaper than an if/else expression in the
        # inner loop.
        map_lines = []
        for y in range(height):
            map_lines.append(
                [".X"[self.contains_point(Offset(x, y))] for x in range(width)]
            )
        return "\n".join("".join(line) for line in map_lines)

    @property
    def regions(self):
        """The regions in the shape."""
        return self._regions

    @property
    def bounds(self):
        """A region that encloses the shape."""
        return self._bounds

    @property
    def area(self):
        """Cells covered by the shape."""
        # TODO: Currently does not handle overlapping regions — same
        # caveat as upstream; the selection-bounds construction below
        # never produces overlapping regions so this is correct in
        # practice for the v0.1 caller.
        return sum(region.area for region in self._regions)

    @classmethod
    def selection_bounds(cls, container, start, end):
        """Get a shape that would be constructed by a user selecting text.

        The shape would look something like this::

                XXXXXXXXXX <- top
            XXXXXXXXXXXXXX
            XXXXXXXXXXXXXX <- middle
            XXXXXXXXXXXXXX
            XXXXXXXXX      <- bottom

        Args:
            container: The container region for the selection.
            start: The start offset.
            end: The end offset.

        Returns:
            A new shape covering the selection bounds.
        """
        # Compare by transpose so the (y, x) ordering picks the
        # earlier point in reading order — selections always flow
        # top-to-bottom regardless of drag direction.
        if start.transpose > end.transpose:
            end, start = start, end
        start_x, start_y = start
        end_x, end_y = end

        def get_regions():
            """Yield regions to cover selection bounds."""
            # Special case: selection spans full container width on
            # both endpoints, collapses to one big region — saves the
            # compositor diffing three separate strips when the user
            # has whole-line-selected with shift+down.
            if start_x == container.x and end_x == container.right:
                yield Region(
                    container.x,
                    start_y,
                    container.width,
                    end_y - start_y + 1,
                )

            elif start.y == end.y:
                # Single-line selection — the common click+drag case.
                yield Region(
                    start_x,
                    start_y,
                    end_x - start_x,
                    1,
                )

            else:
                # Multi-line selection: top fragment, optional middle
                # band that covers full container width, bottom
                # fragment.  Yielded as three regions so the
                # compositor can clip each independently.
                yield Region(
                    start_x,
                    start_y,
                    container.right - start_x,
                    1,
                )
                if end.y - start.y > 1:
                    yield Region(
                        container.x,
                        start_y + 1,
                        container.width,
                        end_y - start_y - 1,
                    )
                yield Region(
                    container.x,
                    end_y,
                    end_x - container.x,
                    1,
                )

        return Shape(get_regions())

    def overlaps(self, region):
        """Does a region overlap this shape?

        Args:
            region: A Region to check.

        Returns:
            ``True`` if any part of the shape overlaps the region.
        """
        return any(shape_region.overlaps(region) for shape_region in self._regions)

    def contains_point(self, offset):
        """Check if the given offset is within the shape.

        Args:
            offset: An offset.

        Returns:
            ``True`` if the given offset is anywhere within the shape.
        """
        return any(region.contains_point(offset) for region in self._regions)


# Module-level interned constants — every call site that defaults to
# "no offset / no region / no spacing" reuses these singletons rather
# than allocating fresh tuples, which matters on the layout hot path.
# ``Final`` is a typing-shim placeholder; it does not enforce anything
# at runtime but documents intent.

NULL_OFFSET = Offset(0, 0)
"""An ``Offset`` constant for (0, 0)."""

NULL_REGION = Region(0, 0, 0, 0)
"""A ``Region`` constant for a null region (at origin, zero area)."""

NULL_SIZE = Size(0, 0)
"""A ``Size`` constant for a null size (with zero area)."""

NULL_SPACING = Spacing(0, 0, 0, 0)
"""A ``Spacing`` constant for no space."""
