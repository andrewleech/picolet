"""picolet_tui._rich.measure — Rich's min/max width sizing helpers.

Ported from Textualize/rich master @ 46cebbb032f920eb096efbaf23cdc6fe9dd541f7
(``rich/measure.py``, 151 LoC upstream).  Tier 2 of the Rich subset:
imports only Tier 1 (``errors``, ``protocol``) plus the local typing shim.
The ``Console`` / ``ConsoleOptions`` types in the original ``TYPE_CHECKING``
block are forward-declared (not imported) so this module does not cycle on
``console.py`` — matching upstream Rich's own layering.

REMOVED vs upstream
-------------------
* ``from typing import TYPE_CHECKING, Callable, NamedTuple, Optional, Sequence``
  routed through ``picolet_tui._shims.typing``.  ``NamedTuple`` is NOT
  exported by that shim (see the comment block in ``_shims/typing.py``),
  so ``Measurement`` subclasses ``collections.namedtuple`` instead.
  MicroPython's ``namedtuple`` is a C builtin (zero frozen-bytes cost)
  and a hand-rolled ``tuple`` subclass cannot work there at all:
  ``tuple.__new__(cls, ...)`` raises ``AttributeError`` on MicroPython,
  so namedtuple is the only viable base.

  Note that MicroPython's namedtuple may not provide ``_replace()``,
  ``_asdict()``, or ``_fields`` — callers in this Rich subset never
  use them.  ``operator.itemgetter`` in ``measure_renderables`` works
  fine against any tuple subclass.

* The ``TYPE_CHECKING`` block that imports ``console.Console``,
  ``ConsoleOptions``, ``RenderableType`` is kept but the symbol
  ``TYPE_CHECKING`` is pulled from the typing shim (always ``False``).
  The block is dead code at runtime; left in place for documentation
  parity with upstream.

* Type-hint annotations on signatures are stripped of their string
  forward-references (e.g. ``"Console"`` -> bare) because keeping the
  quoted strings buys nothing at runtime under MicroPython.  Signatures
  remain positionally and by-keyword identical to upstream — the rule
  in this port pack is "match upstream's public surface exactly", and
  Rich's call sites never pass arguments by annotation type.

NOT REMOVED (intentional)
-------------------------
* All five ``Measurement`` methods (``normalize``, ``with_maximum``,
  ``with_minimum``, ``clamp``, ``get``) plus the module-level
  ``measure_renderables`` are preserved.  Textual's compositor and the
  Tier 2 ``align`` / ``padding`` ports both call into ``Measurement.get``
  and ``measure_renderables`` directly (02 §"Textual's Actual Rich
  Usage" lists ``rich.measure`` at 8 imports).

Spec hooks
----------
Supports:
  FR-TUI-30 (intrinsic sizing): a child's "measured intrinsic size" in
    the layout-pass priority order is computed by ``Measurement.get`` —
    it is the gate the layout pass calls when the parent allocation
    plus declared width/height do not pin the child down.
  FR-TUI-29 (Container / Vertical / Horizontal layout): the layout
    primitives call ``measure_renderables`` to size auto-width children
    against the available column budget.
  FR-TUI-13 / FR-TUI-14 (renderable dispatch): delegates to
    ``protocol.is_renderable`` and ``protocol.rich_cast`` from Tier 1,
    so the renderable detection path is shared with the rest of Rich.
  NFR-TUI-19 (frozen-bytes budget): ``collections.namedtuple`` is a C
    builtin on MicroPython, so the base class costs nothing; the module
    clocks in at ~70 SLoC (excluding this docstring).
"""

from collections import namedtuple

from picolet_tui._shims.typing import (
    TYPE_CHECKING,
    Callable,
    Optional,
)

from . import errors
from .protocol import is_renderable, rich_cast

if TYPE_CHECKING:
    # Forward references only — never executed under MicroPython since
    # the typing shim hard-codes TYPE_CHECKING = False.  Mirrors upstream
    # Rich so a sufficiently smart static checker can still resolve the
    # console types without us creating a Tier 2 -> Tier 2 cycle.
    from .console import Console, ConsoleOptions, RenderableType


# namedtuple base because MicroPython cannot call tuple.__new__ in a subclass.
class Measurement(namedtuple("Measurement", ("minimum", "maximum"))):
    """Stores the minimum and maximum widths (in characters) required to render an object.

    Construction is positional, matching Rich's NamedTuple signature::

        Measurement(minimum, maximum)

    Component values are ints (cells); negative values are tolerated at
    construction time and pinned to ``>= 0`` by ``normalize()``, matching
    upstream behaviour.
    """

    __slots__ = ()

    @property
    def span(self) -> int:
        """Get difference between maximum and minimum."""
        return self.maximum - self.minimum

    def normalize(self) -> "Measurement":
        """Get measurement that ensures that minimum <= maximum and minimum >= 0.

        Returns:
            Measurement: A normalized measurement.
        """
        minimum, maximum = self
        # Pin minimum into [0, maximum]; then rebuild with both legs
        # clamped at zero.  Matches upstream's two-step clamp exactly —
        # the redundant outer max() calls preserve the corner case where
        # the input had maximum < 0.
        minimum = min(max(0, minimum), maximum)
        return Measurement(max(0, minimum), max(0, max(minimum, maximum)))

    def with_maximum(self, width: int) -> "Measurement":
        """Get a RenderableWith where the widths are <= width.

        Args:
            width (int): Maximum desired width.

        Returns:
            Measurement: New Measurement object.
        """
        minimum, maximum = self
        return Measurement(min(minimum, width), min(maximum, width))

    def with_minimum(self, width: int) -> "Measurement":
        """Get a RenderableWith where the widths are >= width.

        Args:
            width (int): Minimum desired width.

        Returns:
            Measurement: New Measurement object.
        """
        minimum, maximum = self
        width = max(0, width)
        return Measurement(max(minimum, width), max(maximum, width))

    def clamp(
        self, min_width: Optional[int] = None, max_width: Optional[int] = None
    ) -> "Measurement":
        """Clamp a measurement within the specified range.

        Args:
            min_width (int): Minimum desired width, or ``None`` for no minimum. Defaults to None.
            max_width (int): Maximum desired width, or ``None`` for no maximum. Defaults to None.

        Returns:
            Measurement: New Measurement object.
        """
        measurement = self
        if min_width is not None:
            measurement = measurement.with_minimum(min_width)
        if max_width is not None:
            measurement = measurement.with_maximum(max_width)
        return measurement

    @classmethod
    def get(cls, console, options, renderable) -> "Measurement":
        """Get a measurement for a renderable.

        Args:
            console (~rich.console.Console): Console instance.
            options (~rich.console.ConsoleOptions): Console options.
            renderable (RenderableType): An object that may be rendered with Rich.

        Raises:
            errors.NotRenderableError: If the object is not renderable.

        Returns:
            Measurement: Measurement object containing range of character widths required to render the object.
        """
        _max_width = options.max_width
        if _max_width < 1:
            return Measurement(0, 0)
        if isinstance(renderable, str):
            # console.render_str is the markup-aware path; it returns a
            # Text instance which then has its own __rich_measure__ hook.
            renderable = console.render_str(
                renderable, markup=options.markup, highlight=False
            )
        renderable = rich_cast(renderable)
        if is_renderable(renderable):
            # __rich_measure__ is the optional hook a renderable exposes
            # to declare its own min/max width — if absent, Rich falls
            # back to "I'll take whatever you give me", i.e. (0, max).
            get_console_width: Optional[Callable] = getattr(
                renderable, "__rich_measure__", None
            )
            if get_console_width is not None:
                render_width = (
                    get_console_width(console, options)
                    .normalize()
                    .with_maximum(_max_width)
                )
                if render_width.maximum < 1:
                    return Measurement(0, 0)
                return render_width.normalize()
            else:
                return Measurement(0, _max_width)
        else:
            raise errors.NotRenderableError(
                "Unable to get render width for {!r}; "
                "a str, Segment, or object with __rich_console__ method is required".format(
                    renderable
                )
            )


def measure_renderables(console, options, renderables) -> "Measurement":
    """Get a measurement that would fit a number of renderables.

    Args:
        console (~rich.console.Console): Console instance.
        options (~rich.console.ConsoleOptions): Console options.
        renderables (Iterable[RenderableType]): One or more renderable objects.

    Returns:
        Measurement: Measurement object containing range of character widths required to
            contain all given renderables.
    """
    if not renderables:
        return Measurement(0, 0)
    get_measurement = Measurement.get
    measurements = [
        get_measurement(console, options, renderable) for renderable in renderables
    ]
    # itemgetter on a tuple subclass works under MicroPython — it just
    # calls __getitem__, which is inherited from tuple.  Pulling min/max
    # separately matches upstream's intent: the bounding measurement is
    # not necessarily contributed by a single renderable.
    measured_width = Measurement(
        # lambdas, not operator.itemgetter: micropython-lib's operator
        # module has no itemgetter, and freezing it for two sort keys is
        # not worth the dependency.
        max(measurements, key=lambda m: m[0]).minimum,
        max(measurements, key=lambda m: m[1]).maximum,
    )
    return measured_width
