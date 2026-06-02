"""picolet_tui._rich.color_triplet — ported from Rich's ``rich/color_triplet.py``.

Upstream:
  https://github.com/Textualize/rich/blob/master/rich/color_triplet.py
  Tracked against rich master (Rich 14.x line; identical to the file shipped
  on the master tip at the time of this port — the upstream module is ~30
  LoC and has been stable for years).

What changed vs upstream
------------------------
* The upstream module declares ``class ColorTriplet(NamedTuple)``.  The
  picolet-tui ``typing`` shim does not implement ``NamedTuple`` — see the
  "Deliberately NOT implemented" block in
  ``picolet_tui/_shims/typing.py`` — because nothing else in the Rich
  subset listed in ``docs/tui/research/02-rich-subset.md`` needs it.
  Pulling the full ``collections.namedtuple`` / metaclass machinery in
  for one consumer would cost both frozen bytes (NFR-TUI-19) and import
  time, so we hand-roll a tuple subclass instead.

  The subclass mirrors the visible NamedTuple contract:
    * positional construction: ``ColorTriplet(r, g, b)``
    * indexed access: ``triplet[0] == triplet.red``
    * named attribute access: ``triplet.red`` / ``.green`` / ``.blue``
    * tuple iteration / unpacking: ``r, g, b = triplet``
    * ``isinstance(triplet, tuple)`` is True (same as NamedTuple)

  What it does NOT mirror — and what callers in this Rich subset never
  use — is ``_replace()``, ``_asdict()``, ``_fields``, and the
  NamedTuple-specific repr.  If a later port needs those, file back to
  the Phase 2 shim pack and add a ``NamedTuple`` shim there rather than
  pasting the machinery here.

* Type imports route through the local typing shim so that
  ``Tuple[float, float, float]`` is a no-op at import time.

Nothing else was removed.  The three properties (``hex``, ``rgb``,
``normalized``) are bit-identical to upstream.

Spec hooks
----------
Supports FR-TUI-33 (color value validation accepts ``rgb(r, g, b)`` and
``#rrggbb``), FR-TUI-40 (color downgrade — the downgrade math in
``_rich.color`` operates on ``ColorTriplet`` instances), and indirectly
FR-TUI-38 (color-system detection feeds downgraded triplets to the
compositor).  The frozen-bytes budget (NFR-TUI-19) is the reason this
file does not pull in ``collections.namedtuple``.
"""

from picolet_tui._shims.typing import Tuple


class ColorTriplet(tuple):
    """The red, green, and blue components of a color.

    Construction is positional, matching Rich's NamedTuple signature::

        ColorTriplet(red, green, blue)

    Component values are ints in the 0..255 range; this class does not
    range-check (Rich does not either — validation happens upstream in
    ``Color.parse`` and ``Style`` construction, per FR-TUI-33).
    """

    __slots__ = ()

    # tuple is immutable, so the constructor lives in __new__ rather than
    # __init__ — same shape CPython generates for a NamedTuple under the
    # hood.  Keeping the parameter names ``red`` / ``green`` / ``blue``
    # matches upstream so keyword construction (``ColorTriplet(red=1,
    # green=2, blue=3)``) keeps working for any caller that uses it.
    def __new__(cls, red, green, blue):
        return tuple.__new__(cls, (red, green, blue))

    @property
    def red(self):
        """Red component in 0 to 255 range."""
        return self[0]

    @property
    def green(self):
        """Green component in 0 to 255 range."""
        return self[1]

    @property
    def blue(self):
        """Blue component in 0 to 255 range."""
        return self[2]

    @property
    def hex(self) -> str:
        """get the color triplet in CSS style."""
        red, green, blue = self
        return "#{:02x}{:02x}{:02x}".format(red, green, blue)

    @property
    def rgb(self) -> str:
        """The color in RGB format.

        Returns:
            str: An rgb color, e.g. ``"rgb(100,23,255)"``.
        """
        red, green, blue = self
        return "rgb({},{},{})".format(red, green, blue)

    @property
    def normalized(self) -> Tuple[float, float, float]:
        """Convert components into floats between 0 and 1.

        Returns:
            Tuple[float, float, float]: A tuple of three normalized colour components.
        """
        red, green, blue = self
        return red / 255.0, green / 255.0, blue / 255.0
