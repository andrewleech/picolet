"""picolet_tui._rich.terminal_theme — ported from Rich.

Upstream: https://github.com/Textualize/rich/blob/master/rich/terminal_theme.py
Pinned to upstream commit 46cebbb032f920eb096efbaf23cdc6fe9dd541f7 (master at
port time). Upstream is ~153 LoC of mostly static palette data; this port
keeps the public class and the three theme constants Textual actually
references.

REMOVED vs upstream:
  - DIMMED_MONOKAI and NIGHT_OWLISH theme constants. They are unreferenced
    by Textual (see research/02-rich-subset.md §"Textual's Actual Rich
    Usage" — `rich.terminal_theme` exposes `TerminalTheme` only, and the
    SVG export path that consumes the named constants is Tier 4, which the
    synthesis deletes). Dropping them trims ~30 LoC of static tuples from
    the frozen romfs (NFR-TUI-20).
  - No other behavioural changes; the `TerminalTheme.__init__` signature
    and attribute names (`background_color`, `foreground_color`,
    `ansi_colors`) match upstream so any consumer that reaches into a
    theme by attribute still works.

Imports redirected per Phase 3a porting rules:
  - `typing.List/Optional/Tuple` -> picolet_tui._shims.typing
  - `.color_triplet.ColorTriplet` and `.palette.Palette` are sibling Tier 1
    modules in this package; they are referenced by relative import the
    same way upstream does. They are not loaded eagerly elsewhere — the
    static constants below trigger the only import-time call into them.

Spec coverage:
  - NFR-TUI-19 / NFR-TUI-20: keeps frozen-bytes footprint small by
    dropping the two unused themes.
  - No FR-TUI-* row depends on `terminal_theme`; it is included because
    Textual's `from rich.terminal_theme import TerminalTheme` import would
    otherwise fail at module load (research/02-rich-subset.md Tier 1).
"""

from picolet_tui._shims.typing import List, Optional, Tuple

from .color_triplet import ColorTriplet
from .palette import Palette

# Local alias kept identical to upstream so any external annotation that
# imports it (Textual does not, but third-party Rich consumers might) still
# resolves. The shim's Tuple is a placeholder, so subscripting is free.
_ColorTuple = Tuple[int, int, int]


class TerminalTheme:
    """A color theme used when exporting console content.

    Args:
        background (Tuple[int, int, int]): The background color.
        foreground (Tuple[int, int, int]): The foreground (text) color.
        normal (List[Tuple[int, int, int]]): A list of 8 normal intensity colors.
        bright (List[Tuple[int, int, int]], optional): A list of 8 bright colors, or None
            to repeat normal intensity. Defaults to None.
    """

    def __init__(
        self,
        background: _ColorTuple,
        foreground: _ColorTuple,
        normal: List[_ColorTuple],
        bright: Optional[List[_ColorTuple]] = None,
    ) -> None:
        self.background_color = ColorTriplet(*background)
        self.foreground_color = ColorTriplet(*foreground)
        # `normal + (bright or normal)` mirrors upstream: when `bright` is
        # omitted the palette repeats the normal band so indices 8..15
        # still resolve.
        self.ansi_colors = Palette(normal + (bright or normal))


DEFAULT_TERMINAL_THEME = TerminalTheme(
    (255, 255, 255),
    (0, 0, 0),
    [
        (0, 0, 0),
        (128, 0, 0),
        (0, 128, 0),
        (128, 128, 0),
        (0, 0, 128),
        (128, 0, 128),
        (0, 128, 128),
        (192, 192, 192),
    ],
    [
        (128, 128, 128),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (0, 0, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    ],
)

MONOKAI = TerminalTheme(
    (12, 12, 12),
    (217, 217, 217),
    [
        (26, 26, 26),
        (244, 0, 95),
        (152, 224, 36),
        (253, 151, 31),
        (157, 101, 255),
        (244, 0, 95),
        (88, 209, 235),
        (196, 197, 181),
        (98, 94, 76),
    ],
    [
        (244, 0, 95),
        (152, 224, 36),
        (224, 213, 97),
        (157, 101, 255),
        (244, 0, 95),
        (88, 209, 235),
        (246, 246, 239),
    ],
)

SVG_EXPORT_THEME = TerminalTheme(
    (41, 41, 41),
    (197, 200, 198),
    [
        (75, 78, 85),
        (204, 85, 90),
        (152, 168, 75),
        (208, 179, 68),
        (96, 138, 177),
        (152, 114, 159),
        (104, 160, 179),
        (197, 200, 198),
        (154, 155, 153),
    ],
    [
        (255, 38, 39),
        (0, 130, 61),
        (208, 132, 66),
        (25, 132, 233),
        (255, 44, 122),
        (57, 130, 128),
        (253, 253, 197),
    ],
)
