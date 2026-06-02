"""picolet_tui._rich.palette — Rich's ``Palette`` ported for MicroPython.

Upstream: ``rich/palette.py`` at master SHA ``46cebbb`` (2026-06).
Source:   https://raw.githubusercontent.com/Textualize/rich/master/rich/palette.py

What this module provides
-------------------------
A thin wrapper around a sequence of ``(r, g, b)`` triples, with two
operations:

* ``palette[index]`` — return the entry as a ``ColorTriplet``.
* ``palette.match((r, g, b))`` — return the index of the palette entry
  whose AERT perceptual distance to ``(r, g, b)`` is smallest.

Used by ``rich.color.Color.downgrade()`` to map a truecolor RGB into
the 16-colour ANSI palette (the only consumer in the trimmed subset),
which is itself driven by the colour-system ladder in FR-TUI-38 and
NFR-TUI-7.  No other module in tier 1 or 2 imports ``palette``.

Removed vs upstream
-------------------
* ``__rich__()`` — built a ``rich.table.Table`` from ``rich.color`` /
  ``rich.style`` / ``rich.text`` to render the palette as a table when
  ``console.print(palette)`` is called.  All four of those modules sit
  in tier 2+ (or tier 3 in Table's case).  Tier 1 must not depend on
  them (porting rule 5); the method is a self-introspection convenience
  with no callers in the FR-TUI-40 downgrade path, so it is dropped
  rather than guarded.

* ``__main__`` demo (``ColorBox`` + ``colorsys.hls_to_rgb`` + a live
  Console) — out of scope per porting rule 4: pulls ``rich.color``,
  ``rich.console``, ``rich.segment``, ``rich.style`` which are not
  available at this tier.

* ``TYPE_CHECKING`` import of ``rich.table`` — used only to annotate
  the dropped ``__rich__`` return type; removed with the method.

Adjustments for MicroPython
---------------------------
* ``lru_cache(maxsize=1024)`` → ``lru_cache(maxsize=128)``.  Synthesis
  decision R4 (mitigation a) caps cache sizes in frozen modules to
  hold the per-instance dict-entry overhead within the NFR-TUI-19
  20 KiB ``_shims`` + ``_rich`` budget when Rich's hot paths are
  exercised under the worst-case colour-downgrade workload.  ``Color``
  itself calls ``palette.match`` at most once per unique RGB encountered
  in a frame, so 128 entries comfortably covers a typical terminal's
  active palette without growing under normal use.

* ``from functools import lru_cache`` → routed via the picolet-tui
  shim, which is registered into ``sys.modules['functools']`` at
  package import.

* ``from typing import Sequence, Tuple`` → routed via the typing
  shim (``Sequence`` / ``Tuple`` collapse to placeholder identities
  there; only used as annotations, never introspected).

Spec coverage
-------------
* FR-TUI-40 — ``palette.match`` is the AERT-distance leg of the
  ``256 → 16`` downgrade against ``EIGHT_BIT_PALETTE[:16]``.
* NFR-TUI-7 — supports the colour-capability ladder by providing
  the mono/16 fallback path's underlying lookup primitive.
"""
from math import sqrt

from picolet_tui._shims.functools import lru_cache
from picolet_tui._shims.typing import Sequence, Tuple  # noqa: F401 — annotations only

from .color_triplet import ColorTriplet


class Palette:
    """A palette of available colors."""

    def __init__(self, colors):
        # type: (Sequence[Tuple[int, int, int]]) -> None
        self._colors = colors

    def __getitem__(self, number):
        # type: (int) -> ColorTriplet
        return ColorTriplet(*self._colors[number])

    # Upstream caches 1024 entries; 128 is the synthesis R4 cap that
    # keeps Palette + Color caches collectively inside NFR-TUI-19.
    @lru_cache(maxsize=128)
    def match(self, color):
        # type: (Tuple[int, int, int]) -> int
        """Find a color from a palette that most closely matches a given color.

        Args:
            color (Tuple[int, int, int]): RGB components in range 0 > 255.

        Returns:
            int: Index of closes matching color.
        """
        red1, green1, blue1 = color
        # Bind to locals to shave attribute lookups in the inner loop —
        # the same micro-opt upstream uses; valuable on MicroPython
        # where attribute access through a closure is comparatively
        # expensive.
        _sqrt = sqrt
        get_color = self._colors.__getitem__

        def get_color_distance(index):
            # type: (int) -> float
            """Get the distance to a color."""
            red2, green2, blue2 = get_color(index)
            # AERT (Application of the Effective Texture Resolution) —
            # weighted RGB distance that approximates perceptual
            # similarity better than a flat Euclidean metric.  The shift
            # arithmetic is integer-friendly and stays within MP's small
            # int range for any 8-bit RGB pair.
            red_mean = (red1 + red2) // 2
            red = red1 - red2
            green = green1 - green2
            blue = blue1 - blue2
            return _sqrt(
                (((512 + red_mean) * red * red) >> 8)
                + 4 * green * green
                + (((767 - red_mean) * blue * blue) >> 8)
            )

        min_index = min(range(len(self._colors)), key=get_color_distance)
        return min_index
