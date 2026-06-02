"""picolet_tui._rich.color — Rich's ``Color`` ported for MicroPython.

Upstream
--------
Source : https://raw.githubusercontent.com/Textualize/rich/master/rich/color.py
Tracked: Textualize/rich master @ 46cebbb032f920eb096efbaf23cdc6fe9dd541f7
         (rich/color.py, ~621 LoC, file is stable across recent releases —
         the truecolor/EIGHT_BIT/STANDARD downgrade math has not changed
         since Rich 10.x).

Role
----
Provides ``Color`` (name + ``ColorType`` + optional palette number +
optional ``ColorTriplet``), the ``ColorSystem`` ladder
(STANDARD/EIGHT_BIT/TRUECOLOR/WINDOWS), the ``ANSI_COLOR_NAMES`` name
table, and the ``downgrade()`` math that walks truecolor → 256 → 16
→ mono per the FR-TUI-40 color-detection ladder.

Removed vs upstream
-------------------
* ``@rich_repr`` class decorator and ``__rich_repr__`` method.
  ``rich.repr`` is Tier 1 but not yet ported (porting rule 5 — Tier 2
  may only import Tier 1 modules that exist), and the decorator is a
  debug-ergonomics convenience.  Drop both rather than introduce a
  conditional import; if a later tier ports ``repr.py`` re-apply on
  this class as a single line edit.
* ``Color.__rich__`` method.  It builds a ``Text.assemble(...)`` with
  a ``Style(color=self)`` to render the colour swatch when
  ``console.print(color)`` is called.  Both ``Text`` and ``Style`` are
  Tier 2+ modules; this method has no callers on the compositor path
  (the v0.1 widgets never print a Color directly), so it is removed
  rather than guarded.
* ``Color(NamedTuple)`` base.  The ``typing`` shim deliberately omits
  ``NamedTuple`` (see ``picolet_tui/_shims/typing.py`` "Deliberately
  NOT implemented" block; same precedent as ``color_triplet`` which
  hand-rolls a ``tuple`` subclass).  We mirror the NamedTuple contract
  manually: positional + keyword construction, indexed access,
  ``self.name`` / ``.type`` / ``.number`` / ``.triplet`` attribute
  access, hashability for ``@lru_cache`` keying.
* ``sys.platform == "win32"`` detection and the unused ``WINDOWS``
  module constant.  v0.1 binary targets are Linux + Windows VT
  (FR-TUI-7); the runtime never inspects this constant — the
  ``ColorType.WINDOWS`` / ``ColorSystem.WINDOWS`` enum values are
  retained because the downgrade math branches on them, but the
  module-level platform sniff is dead code.
* The ``__main__`` demo block (lines 594..621 upstream) builds a
  ``rich.table.Table`` to render the palette — Tier 3, out of scope.
* ``typing.TYPE_CHECKING`` block + the ``Text`` forward import.

Adjustments for MicroPython
---------------------------
* ``from enum import IntEnum`` → ``from picolet_tui._shims.enum``;
  the two IntEnum classes carry the explicit ``@enum_class`` decorator
  required by the shim (see ``_shims/enum.py`` rationale: MicroPython
  does not honour user metaclasses, so CPython's ``class X(IntEnum):``
  auto-population idiom is unavailable).
* ``from functools import lru_cache`` → shim path.  All three
  ``maxsize=1024`` sites drop to ``maxsize=128`` (NFR-TUI-6 / synthesis
  R4 mitigation a).  ``Color.parse`` and ``Color.downgrade`` are the
  hot paths; 128 entries comfortably holds a single frame's worth of
  unique colours under the FR-TUI-40 downgrade ladder.
* ``from colorsys import rgb_to_hls`` → inlined as a module-level
  ``_rgb_to_hls`` function (~12 lines of arithmetic).  MicroPython
  does not ship ``colorsys`` and synthesis ``02-rich-subset.md``
  §"Color Path" calls out this exact inline as the prescribed fix.
* ``re.VERBOSE`` flag removed.  MicroPython's ``re`` engine does not
  honour any flags at all (research doc 03 §"Per-module table" line on
  ``re``).  The pattern is folded onto a single whitespace-free line
  with the alternation arms preserved verbatim — the resulting regex
  is functionally identical because the upstream verbose form only
  uses whitespace for layout, not as a matched character.  Tested on
  re1.5 with the same input corpus as the upstream tests.

Spec coverage
-------------
* FR-TUI-33 — ``Color.parse`` accepts named colors, ``#rrggbb``,
  ``color(N)``, and ``rgb(r, g, b)``; bad input raises
  ``ColorParseError`` (re-raised as ``StyleError`` at the ``Style``
  construction layer above).
* FR-TUI-38 — the ``ColorSystem`` enum is the value space picked by
  the color-capability detection ladder.
* FR-TUI-40 — ``Color.downgrade()`` implements the truecolor → 256
  HLS-grayscale + 6-cube mapping and the 256 → 16 AERT match via
  ``STANDARD_PALETTE.match`` (the ``palette.Palette.match`` AERT
  distance).
* NFR-TUI-6 — every ``lru_cache`` on this module is capped at 128.
* NFR-TUI-7 — supports the colour-capability ladder by providing the
  ``ColorSystem`` value space the ladder selects.
* NFR-TUI-19 — counts against the ``_rich`` 60 KiB romfs sub-budget;
  the trimmed module sits well under the upstream 621 LoC.
"""

from picolet_tui._shims.enum import IntEnum, enum_class
from picolet_tui._shims.functools import lru_cache
from picolet_tui._shims.typing import Optional, Tuple  # noqa: F401 — annotations only

import re

from ._palettes import EIGHT_BIT_PALETTE, STANDARD_PALETTE, WINDOWS_PALETTE
from .color_triplet import ColorTriplet
from .terminal_theme import DEFAULT_TERMINAL_THEME


# ---------------------------------------------------------------------------
# Enums.  Both must carry ``@enum_class`` explicitly — the shim cannot
# auto-populate from the class body without a metaclass.
# ---------------------------------------------------------------------------


@enum_class
class ColorSystem(IntEnum):
    """One of the 3 color system supported by terminals."""

    STANDARD = 1
    EIGHT_BIT = 2
    TRUECOLOR = 3
    WINDOWS = 4

    def __repr__(self):
        return "ColorSystem." + self.name

    def __str__(self):
        return repr(self)


@enum_class
class ColorType(IntEnum):
    """Type of color stored in Color class."""

    DEFAULT = 0
    STANDARD = 1
    EIGHT_BIT = 2
    TRUECOLOR = 3
    WINDOWS = 4

    def __repr__(self):
        return "ColorType." + self.name


ANSI_COLOR_NAMES = {
    "black": 0,
    "red": 1,
    "green": 2,
    "yellow": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
    "bright_black": 8,
    "bright_red": 9,
    "bright_green": 10,
    "bright_yellow": 11,
    "bright_blue": 12,
    "bright_magenta": 13,
    "bright_cyan": 14,
    "bright_white": 15,
    "grey0": 16,
    "gray0": 16,
    "navy_blue": 17,
    "dark_blue": 18,
    "blue3": 20,
    "blue1": 21,
    "dark_green": 22,
    "deep_sky_blue4": 25,
    "dodger_blue3": 26,
    "dodger_blue2": 27,
    "green4": 28,
    "spring_green4": 29,
    "turquoise4": 30,
    "deep_sky_blue3": 32,
    "dodger_blue1": 33,
    "green3": 40,
    "spring_green3": 41,
    "dark_cyan": 36,
    "light_sea_green": 37,
    "deep_sky_blue2": 38,
    "deep_sky_blue1": 39,
    "spring_green2": 47,
    "cyan3": 43,
    "dark_turquoise": 44,
    "turquoise2": 45,
    "green1": 46,
    "spring_green1": 48,
    "medium_spring_green": 49,
    "cyan2": 50,
    "cyan1": 51,
    "dark_red": 88,
    "deep_pink4": 125,
    "purple4": 55,
    "purple3": 56,
    "blue_violet": 57,
    "orange4": 94,
    "grey37": 59,
    "gray37": 59,
    "medium_purple4": 60,
    "slate_blue3": 62,
    "royal_blue1": 63,
    "chartreuse4": 64,
    "dark_sea_green4": 71,
    "pale_turquoise4": 66,
    "steel_blue": 67,
    "steel_blue3": 68,
    "cornflower_blue": 69,
    "chartreuse3": 76,
    "cadet_blue": 73,
    "sky_blue3": 74,
    "steel_blue1": 81,
    "pale_green3": 114,
    "sea_green3": 78,
    "aquamarine3": 79,
    "medium_turquoise": 80,
    "chartreuse2": 112,
    "sea_green2": 83,
    "sea_green1": 85,
    "aquamarine1": 122,
    "dark_slate_gray2": 87,
    "dark_magenta": 91,
    "dark_violet": 128,
    "purple": 129,
    "light_pink4": 95,
    "plum4": 96,
    "medium_purple3": 98,
    "slate_blue1": 99,
    "yellow4": 106,
    "wheat4": 101,
    "grey53": 102,
    "gray53": 102,
    "light_slate_grey": 103,
    "light_slate_gray": 103,
    "medium_purple": 104,
    "light_slate_blue": 105,
    "dark_olive_green3": 149,
    "dark_sea_green": 108,
    "light_sky_blue3": 110,
    "sky_blue2": 111,
    "dark_sea_green3": 150,
    "dark_slate_gray3": 116,
    "sky_blue1": 117,
    "chartreuse1": 118,
    "light_green": 120,
    "pale_green1": 156,
    "dark_slate_gray1": 123,
    "red3": 160,
    "medium_violet_red": 126,
    "magenta3": 164,
    "dark_orange3": 166,
    "indian_red": 167,
    "hot_pink3": 168,
    "medium_orchid3": 133,
    "medium_orchid": 134,
    "medium_purple2": 140,
    "dark_goldenrod": 136,
    "light_salmon3": 173,
    "rosy_brown": 138,
    "grey63": 139,
    "gray63": 139,
    "medium_purple1": 141,
    "gold3": 178,
    "dark_khaki": 143,
    "navajo_white3": 144,
    "grey69": 145,
    "gray69": 145,
    "light_steel_blue3": 146,
    "light_steel_blue": 147,
    "yellow3": 184,
    "dark_sea_green2": 157,
    "light_cyan3": 152,
    "light_sky_blue1": 153,
    "green_yellow": 154,
    "dark_olive_green2": 155,
    "dark_sea_green1": 193,
    "pale_turquoise1": 159,
    "deep_pink3": 162,
    "magenta2": 200,
    "hot_pink2": 169,
    "orchid": 170,
    "medium_orchid1": 207,
    "orange3": 172,
    "light_pink3": 174,
    "pink3": 175,
    "plum3": 176,
    "violet": 177,
    "light_goldenrod3": 179,
    "tan": 180,
    "misty_rose3": 181,
    "thistle3": 182,
    "plum2": 183,
    "khaki3": 185,
    "light_goldenrod2": 222,
    "light_yellow3": 187,
    "grey84": 188,
    "gray84": 188,
    "light_steel_blue1": 189,
    "yellow2": 190,
    "dark_olive_green1": 192,
    "honeydew2": 194,
    "light_cyan1": 195,
    "red1": 196,
    "deep_pink2": 197,
    "deep_pink1": 199,
    "magenta1": 201,
    "orange_red1": 202,
    "indian_red1": 204,
    "hot_pink": 206,
    "dark_orange": 208,
    "salmon1": 209,
    "light_coral": 210,
    "pale_violet_red1": 211,
    "orchid2": 212,
    "orchid1": 213,
    "orange1": 214,
    "sandy_brown": 215,
    "light_salmon1": 216,
    "light_pink1": 217,
    "pink1": 218,
    "plum1": 219,
    "gold1": 220,
    "navajo_white1": 223,
    "misty_rose1": 224,
    "thistle1": 225,
    "yellow1": 226,
    "light_goldenrod1": 227,
    "khaki1": 228,
    "wheat1": 229,
    "cornsilk1": 230,
    "grey100": 231,
    "gray100": 231,
    "grey3": 232,
    "gray3": 232,
    "grey7": 233,
    "gray7": 233,
    "grey11": 234,
    "gray11": 234,
    "grey15": 235,
    "gray15": 235,
    "grey19": 236,
    "gray19": 236,
    "grey23": 237,
    "gray23": 237,
    "grey27": 238,
    "gray27": 238,
    "grey30": 239,
    "gray30": 239,
    "grey35": 240,
    "gray35": 240,
    "grey39": 241,
    "gray39": 241,
    "grey42": 242,
    "gray42": 242,
    "grey46": 243,
    "gray46": 243,
    "grey50": 244,
    "gray50": 244,
    "grey54": 245,
    "gray54": 245,
    "grey58": 246,
    "gray58": 246,
    "grey62": 247,
    "gray62": 247,
    "grey66": 248,
    "gray66": 248,
    "grey70": 249,
    "gray70": 249,
    "grey74": 250,
    "gray74": 250,
    "grey78": 251,
    "gray78": 251,
    "grey82": 252,
    "gray82": 252,
    "grey85": 253,
    "gray85": 253,
    "grey89": 254,
    "gray89": 254,
    "grey93": 255,
    "gray93": 255,
}


class ColorParseError(Exception):
    """The color could not be parsed."""


# Upstream uses re.VERBOSE so the pattern can be laid out across three
# lines with comments; MicroPython's re engine does not honour any flags
# (research doc 03 §"Per-module table"), so we fold the same alternation
# back onto a single whitespace-free line.  The match semantics are
# identical because the verbose form only used whitespace for layout.
RE_COLOR = re.compile(
    r"^\#([0-9a-f]{6})$|color\(([0-9]{1,3})\)$|rgb\(([\d\s,]+)\)$"
)


# ---------------------------------------------------------------------------
# Inline rgb_to_hls — MicroPython does not ship ``colorsys`` and pulling in
# the full module for one ~12-line routine is wasteful.  Lifted from the
# CPython reference implementation; same algorithm, integer-friendly under
# MicroPython's float type.  Used only by ``Color.downgrade`` on the
# truecolor → 256 grayscale-detection path.
# ---------------------------------------------------------------------------


def _rgb_to_hls(r, g, b):
    """Convert RGB (0..1 floats) to HLS — direct CPython colorsys port.

    Returns ``(h, l, s)`` in the 0..1 range.  Only the ``s`` (saturation)
    output is consulted by ``Color.downgrade`` (the grayscale-vs-colour
    branch), but we return the full triple so the call site reads the
    same as upstream.
    """
    maxc = max(r, g, b)
    minc = min(r, g, b)
    sumc = maxc + minc
    rangec = maxc - minc
    l = sumc / 2.0
    if minc == maxc:
        return 0.0, l, 0.0
    if l <= 0.5:
        s = rangec / sumc
    else:
        s = rangec / (2.0 - sumc)
    rc = (maxc - r) / rangec
    gc = (maxc - g) / rangec
    bc = (maxc - b) / rangec
    if r == maxc:
        h = bc - gc
    elif g == maxc:
        h = 2.0 + rc - bc
    else:
        h = 4.0 + gc - rc
    h = (h / 6.0) % 1.0
    return h, l, s


# ---------------------------------------------------------------------------
# Color — upstream NamedTuple, ported as a hand-rolled ``tuple`` subclass.
# The contract (positional / keyword construction, indexed and attribute
# access, hashability for ``@lru_cache``) is preserved; ``_replace`` /
# ``_asdict`` / ``_fields`` are NOT — Rich's call sites in the trimmed
# subset never use them.  Same precedent as ``color_triplet.ColorTriplet``.
# ---------------------------------------------------------------------------


class Color(tuple):
    """Terminal color definition.

    Construction mirrors the upstream NamedTuple::

        Color(name, type, number=None, triplet=None)

    Positional and keyword forms both work; the tuple layout is
    ``(name, type, number, triplet)`` so unpacking and indexed access
    behave identically to the upstream NamedTuple.
    """

    __slots__ = ()

    def __new__(cls, name, type, number=None, triplet=None):
        # ``type`` shadows the builtin deliberately to match upstream's
        # field name; the local lookup hides the builtin only inside
        # __new__ which never needs it.
        return tuple.__new__(cls, (name, type, number, triplet))

    @property
    def name(self):
        """The name of the color (typically the input to Color.parse)."""
        return self[0]

    @property
    def type(self):
        """The type of the color."""
        return self[1]

    @property
    def number(self):
        """The color number, if a standard color, or None."""
        return self[2]

    @property
    def triplet(self):
        """A triplet of color components, if an RGB color."""
        return self[3]

    def __repr__(self):
        return "Color({!r}, {!r}, number={!r}, triplet={!r})".format(
            self[0], self[1], self[2], self[3]
        )

    @property
    def system(self):
        """Get the native color system for this color."""
        if self.type == ColorType.DEFAULT:
            return ColorSystem.STANDARD
        # ColorType and ColorSystem share integer values 1..4 for
        # STANDARD/EIGHT_BIT/TRUECOLOR/WINDOWS; the cast preserves
        # upstream's behaviour without depending on the IntEnum cast
        # path (which our shim implements but at extra cost).
        return ColorSystem.from_value(int(self.type))

    @property
    def is_system_defined(self):
        """Check if the color is ultimately defined by the system."""
        return self.system not in (ColorSystem.EIGHT_BIT, ColorSystem.TRUECOLOR)

    @property
    def is_default(self):
        """Check if the color is a default color."""
        return self.type == ColorType.DEFAULT

    def get_truecolor(self, theme=None, foreground=True):
        """Get an equivalent color triplet for this color.

        Args:
            theme (TerminalTheme, optional): Optional terminal theme, or
                None to use default. Defaults to None.
            foreground (bool, optional): True for a foreground color, or
                False for background. Defaults to True.

        Returns:
            ColorTriplet: A color triplet containing RGB components.
        """
        if theme is None:
            theme = DEFAULT_TERMINAL_THEME
        if self.type == ColorType.TRUECOLOR:
            return self.triplet
        elif self.type == ColorType.EIGHT_BIT:
            return EIGHT_BIT_PALETTE[self.number]
        elif self.type == ColorType.STANDARD:
            return theme.ansi_colors[self.number]
        elif self.type == ColorType.WINDOWS:
            return WINDOWS_PALETTE[self.number]
        else:  # self.type == ColorType.DEFAULT
            return theme.foreground_color if foreground else theme.background_color

    @classmethod
    def from_ansi(cls, number):
        """Create a Color from its 8-bit ANSI number.

        Args:
            number (int): A number between 0-255 inclusive.

        Returns:
            Color: A new Color instance.
        """
        return cls(
            name="color({})".format(number),
            type=(ColorType.STANDARD if number < 16 else ColorType.EIGHT_BIT),
            number=number,
        )

    @classmethod
    def from_triplet(cls, triplet):
        """Create a truecolor RGB color from a triplet of values.

        Args:
            triplet (ColorTriplet): A color triplet containing red,
                green and blue components.

        Returns:
            Color: A new color object.
        """
        return cls(name=triplet.hex, type=ColorType.TRUECOLOR, triplet=triplet)

    @classmethod
    def from_rgb(cls, red, green, blue):
        """Create a truecolor from three color components in the range(0->255).

        Args:
            red (float): Red component in range 0-255.
            green (float): Green component in range 0-255.
            blue (float): Blue component in range 0-255.

        Returns:
            Color: A new color object.
        """
        return cls.from_triplet(ColorTriplet(int(red), int(green), int(blue)))

    @classmethod
    def default(cls):
        """Get a Color instance representing the default color.

        Returns:
            Color: Default color.
        """
        return cls(name="default", type=ColorType.DEFAULT)

    # Upstream caches 1024 entries; 128 is the NFR-TUI-6 / synthesis R4 cap.
    @classmethod
    @lru_cache(maxsize=128)
    def parse(cls, color):
        """Parse a color definition."""
        original_color = color
        color = color.lower().strip()

        if color == "default":
            return cls(color, type=ColorType.DEFAULT)

        color_number = ANSI_COLOR_NAMES.get(color)
        if color_number is not None:
            return cls(
                color,
                type=(ColorType.STANDARD if color_number < 16 else ColorType.EIGHT_BIT),
                number=color_number,
            )

        color_match = RE_COLOR.match(color)
        if color_match is None:
            raise ColorParseError(
                "{!r} is not a valid color".format(original_color)
            )

        color_24, color_8, color_rgb = color_match.groups()
        if color_24:
            triplet = ColorTriplet(
                int(color_24[0:2], 16),
                int(color_24[2:4], 16),
                int(color_24[4:6], 16),
            )
            return cls(color, ColorType.TRUECOLOR, triplet=triplet)

        elif color_8:
            number = int(color_8)
            if number > 255:
                raise ColorParseError(
                    "color number must be <= 255 in {!r}".format(color)
                )
            return cls(
                color,
                type=(ColorType.STANDARD if number < 16 else ColorType.EIGHT_BIT),
                number=number,
            )

        else:  # color_rgb
            components = color_rgb.split(",")
            if len(components) != 3:
                raise ColorParseError(
                    "expected three components in {!r}".format(original_color)
                )
            red, green, blue = components
            triplet = ColorTriplet(int(red), int(green), int(blue))
            if not all(component <= 255 for component in triplet):
                raise ColorParseError(
                    "color components must be <= 255 in {!r}".format(original_color)
                )
            return cls(color, ColorType.TRUECOLOR, triplet=triplet)

    # Upstream caches 1024 entries; 128 is the NFR-TUI-6 / synthesis R4 cap.
    @lru_cache(maxsize=128)
    def get_ansi_codes(self, foreground=True):
        """Get the ANSI escape codes for this color."""
        _type = self.type
        if _type == ColorType.DEFAULT:
            return ("39" if foreground else "49",)

        elif _type == ColorType.WINDOWS:
            number = self.number
            fore, back = (30, 40) if number < 8 else (82, 92)
            return (str(fore + number if foreground else back + number),)

        elif _type == ColorType.STANDARD:
            number = self.number
            fore, back = (30, 40) if number < 8 else (82, 92)
            return (str(fore + number if foreground else back + number),)

        elif _type == ColorType.EIGHT_BIT:
            return ("38" if foreground else "48", "5", str(self.number))

        else:  # _type == ColorType.TRUECOLOR
            red, green, blue = self.triplet
            return (
                "38" if foreground else "48",
                "2",
                str(red),
                str(green),
                str(blue),
            )

    # Upstream caches 1024 entries; 128 is the NFR-TUI-6 / synthesis R4 cap.
    # This is the FR-TUI-40 truecolor → 256 → 16 ladder.
    @lru_cache(maxsize=128)
    def downgrade(self, system):
        """Downgrade a color system to a system with fewer colors."""

        if self.type in (ColorType.DEFAULT, system):
            return self
        # Convert to 8-bit color from truecolor color
        if system == ColorSystem.EIGHT_BIT and self.system == ColorSystem.TRUECOLOR:
            _h, l, s = _rgb_to_hls(*self.triplet.normalized)
            # If saturation is under 15% assume it is grayscale
            if s < 0.15:
                gray = round(l * 25.0)
                if gray == 0:
                    color_number = 16
                elif gray == 25:
                    color_number = 231
                else:
                    color_number = 231 + gray
                return Color(self.name, ColorType.EIGHT_BIT, number=color_number)

            red, green, blue = self.triplet
            # 6x6x6 RGB cube: ANSI 256 places the colour cube at indices
            # 16..231 with a non-linear ramp.  The piecewise division
            # below maps 0..255 onto 0..5 according to the cube's actual
            # rung spacing (95 for the first rung, 40 per subsequent
            # rung).  Integer ``round`` is the standard nearest-rung
            # quantisation.
            six_red = red / 95 if red < 95 else 1 + (red - 95) / 40
            six_green = green / 95 if green < 95 else 1 + (green - 95) / 40
            six_blue = blue / 95 if blue < 95 else 1 + (blue - 95) / 40

            color_number = (
                16 + 36 * round(six_red) + 6 * round(six_green) + round(six_blue)
            )
            return Color(self.name, ColorType.EIGHT_BIT, number=color_number)

        # Convert to standard from truecolor or 8-bit
        elif system == ColorSystem.STANDARD:
            if self.system == ColorSystem.TRUECOLOR:
                triplet = self.triplet
            else:  # self.system == ColorSystem.EIGHT_BIT
                triplet = ColorTriplet(*EIGHT_BIT_PALETTE[self.number])

            color_number = STANDARD_PALETTE.match(triplet)
            return Color(self.name, ColorType.STANDARD, number=color_number)

        elif system == ColorSystem.WINDOWS:
            if self.system == ColorSystem.TRUECOLOR:
                triplet = self.triplet
            else:  # self.system == ColorSystem.EIGHT_BIT
                if self.number < 16:
                    return Color(self.name, ColorType.WINDOWS, number=self.number)
                triplet = ColorTriplet(*EIGHT_BIT_PALETTE[self.number])

            color_number = WINDOWS_PALETTE.match(triplet)
            return Color(self.name, ColorType.WINDOWS, number=color_number)

        return self


def parse_rgb_hex(hex_color):
    """Parse six hex characters in to RGB triplet."""
    assert len(hex_color) == 6, "must be 6 characters"
    color = ColorTriplet(
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )
    return color


def blend_rgb(color1, color2, cross_fade=0.5):
    """Blend one RGB color in to another."""
    r1, g1, b1 = color1
    r2, g2, b2 = color2
    new_color = ColorTriplet(
        int(r1 + (r2 - r1) * cross_fade),
        int(g1 + (g2 - g1) * cross_fade),
        int(b1 + (b2 - b1) * cross_fade),
    )
    return new_color
