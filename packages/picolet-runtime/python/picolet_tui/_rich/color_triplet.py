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
  "Deliberately NOT implemented" block in ``picolet_tui/_shims/typing.py``.
  This port subclasses ``collections.namedtuple`` instead, which on
  MicroPython is a C builtin (zero frozen-bytes cost) and on CPython is
  the same machinery NamedTuple compiles down to.

  An earlier revision hand-rolled a ``tuple`` subclass with a custom
  ``__new__`` to dodge namedtuple — but ``tuple.__new__(cls, ...)``
  raises ``AttributeError`` on MicroPython (static ``__new__`` lookup on
  builtin types is unsupported), so the hand-rolled shape cannot
  instantiate at all there.  namedtuple subclassing works on both
  interpreters and is *cheaper* on MicroPython, not dearer; the old
  frozen-bytes rationale had it backwards.

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
compositor).
"""

from collections import namedtuple

from picolet_tui._shims.typing import Tuple


class ColorTriplet(namedtuple("ColorTriplet", ("red", "green", "blue"))):
    """The red, green, and blue components of a color.

    Construction is positional, matching Rich's NamedTuple signature::

        ColorTriplet(red, green, blue)

    Component values are ints in the 0..255 range; this class does not
    range-check (Rich does not either — validation happens upstream in
    ``Color.parse`` and ``Style`` construction, per FR-TUI-33).
    """

    __slots__ = ()

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
