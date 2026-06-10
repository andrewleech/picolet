"""picolet_tui._textual.color — Textual's Color ported for MicroPython.

Upstream
--------
Source : https://raw.githubusercontent.com/Textualize/textual/main/src/textual/color.py
Tracked: Textualize/textual main, ~856 LoC.

Role
----
Textual's ``Color`` wraps Rich's ``Color`` with alpha, an optional ANSI
index, an ``auto`` flag for contrast-resolved colours, RGB ↔ HSL/HSV
conversions, ``blend``/``tint`` mixing, the ``brightness`` /
``get_contrast_text`` pair (the 0.5-luma flip Textual uses where a
fuller WCAG calc would otherwise sit), and ``Color.parse`` for CSS-ish
colour strings.

Removed vs upstream
-------------------
* ``Lab`` / ``Luv`` colour space + ``rgb_to_lab`` / ``lab_to_rgb``.
  Upstream uses Lab only inside ``darken``/``lighten`` for
  perceptually-uniform luminance steps under animated transitions —
  synthesis D7 drops animation, so the upstream path has no v0.1 caller.
  ``darken``/``lighten`` here fall back to HLS-lightness arithmetic
  (Rich's own ``blend_rgb`` ergonomics).  Visible delta vs Lab is small
  in the 0–0.3 amount range where static widgets call them.
* ``Gradient``.  Animation-adjacent (synthesis D7); depends only on
  ``Color.blend`` so re-adding it in v0.2 is mechanical.
* ``@rich.repr.auto`` and ``__rich_repr__`` — debug ergonomics, not on
  the compositor path; same precedent as the trimmed ``_rich.color``.
* ``Color(NamedTuple)`` base — the typing shim deliberately omits
  ``NamedTuple``.  ``collections.namedtuple`` subclass instead (a C
  builtin on MicroPython, where a hand-rolled ``tuple`` subclass cannot
  work because ``tuple.__new__`` raises AttributeError), mirroring the
  ``_rich/color_triplet.py`` and ``_rich/color.py`` precedent.
  MicroPython's namedtuple has no constructor defaults, so all six
  fields are passed explicitly at every call site.
* ``typing_extensions.Final`` — superseded by the typing shim placeholder.
* ``from textual.suggestions import get_suggestion`` — optional
  spellcheck for unknown colour names; pulled a Levenshtein
  implementation for one error-message line.  ``ColorParseError`` still
  names the offending input, which is the FR-TUI-33 contract.
* ``from textual.css.scalar import percentage_string_to_float`` —
  inlined (four lines of identical arithmetic).
* ``from textual.css.tokenize import CLOSE_BRACE, COMMA, DECIMAL,
  OPEN_BRACE, PERCENT`` — inlined as module-level constants.
* ``from textual.geometry import clamp`` — inlined as a 3-arg local;
  ``geometry`` is a Phase 4a sibling not yet ported.
* ``from operator import itemgetter`` — the two ``_split_pairs`` getters
  fold to explicit slicing inline.

Adjustments for MicroPython
---------------------------
* ``rich.color`` / ``rich.color_triplet`` / ``rich.terminal_theme``
  imports re-routed to ``picolet_tui._rich`` (the trimmed Rich subset).
* ``colorsys`` is absent — ``rgb_to_hls`` / ``hls_to_rgb`` /
  ``rgb_to_hsv`` / ``hsv_to_rgb`` inlined as module-private helpers
  (synthesis 02 §"Needed Shims" calls these out).
* ``functools.lru_cache`` re-routed to the shim with ``maxsize=128``
  (NFR-TUI-6 / synthesis R4 mitigation a).  Upstream uses 1024 in five
  places; all dropped to 128.
* ``re.VERBOSE`` removed from the ``RE_COLOR`` compile — re1.5 ignores
  every flag (research 03 §"Per-module table").  The pattern is folded
  onto one whitespace-free line; alternation arms are preserved verbatim.

Spec coverage
-------------
* FR-TUI-33 — ``Color.parse`` accepts named colours, ``#RGB`` /
  ``#RGBA`` / ``#RRGGBB`` / ``#RRGGBBAA``, ``rgb(...)`` / ``rgba(...)``,
  ``hsl(...)`` / ``hsla(...)``.  Bad input raises ``ColorParseError``.
* FR-TUI-40 — ``Color.rich_color`` / ``Color.from_rich_color`` bridge
  to the Rich subset that owns the downgrade ladder.
* NFR-TUI-6 — every ``lru_cache`` is capped at 128.
* NFR-TUI-19 — counts against the ``_textual`` romfs sub-budget.
"""

import re
from collections import namedtuple

from picolet_tui._shims.functools import lru_cache
from picolet_tui._shims.typing import Final, Optional, Tuple  # noqa: F401 — annotations only

from picolet_tui._rich.color import Color as RichColor, ColorType
from picolet_tui._rich.color_triplet import ColorTriplet
from picolet_tui._rich.terminal_theme import TerminalTheme  # noqa: F401 — exported


_TRUECOLOR = ColorType.TRUECOLOR


# ---------------------------------------------------------------------------
# Inlined helpers.  Each replaces a single import the runtime does not yet
# carry; the upstream code keeps these as separate modules but for a
# self-contained leaf the indirection costs more than it pays back.
# ---------------------------------------------------------------------------


def _clamp(value, minimum, maximum):
    """Three-arg clamp, identical to ``textual.geometry.clamp``."""
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _percentage_string_to_float(string):
    """Convert ``"20%"`` → ``0.2``, clamped to 0..1.

    Inlined from ``textual.css.scalar.percentage_string_to_float`` so the
    colour parser does not pull in the CSS leaf module.  The HSL parse
    paths feed S and L through this helper.
    """
    string = string.strip()
    if string.endswith("%"):
        return _clamp(float(string[:-1]) / 100.0, 0.0, 1.0)
    return _clamp(float(string) / 100.0, 0.0, 1.0)


# colorsys is not on MicroPython.  These four are direct ports of the
# CPython reference implementations; signatures + return shapes match.
# Kept module-private rather than re-exported because nothing outside
# this module needs them.


def _rgb_to_hls(r, g, b):
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


def _v(m1, m2, hue):
    hue = hue % 1.0
    if hue < 1.0 / 6.0:
        return m1 + (m2 - m1) * hue * 6.0
    if hue < 0.5:
        return m2
    if hue < 2.0 / 3.0:
        return m1 + (m2 - m1) * (2.0 / 3.0 - hue) * 6.0
    return m1


def _hls_to_rgb(h, l, s):
    if s == 0.0:
        return l, l, l
    if l <= 0.5:
        m2 = l * (1.0 + s)
    else:
        m2 = l + s - l * s
    m1 = 2.0 * l - m2
    return (
        _v(m1, m2, h + 1.0 / 3.0),
        _v(m1, m2, h),
        _v(m1, m2, h - 1.0 / 3.0),
    )


def _rgb_to_hsv(r, g, b):
    maxc = max(r, g, b)
    minc = min(r, g, b)
    rangec = maxc - minc
    v = maxc
    if minc == maxc:
        return 0.0, 0.0, v
    s = rangec / maxc
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
    return h, s, v


def _hsv_to_rgb(h, s, v):
    if s == 0.0:
        return v, v, v
    i = int(h * 6.0)
    f = (h * 6.0) - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = i % 6
    if i == 0:
        return v, t, p
    if i == 1:
        return q, v, p
    if i == 2:
        return p, v, t
    if i == 3:
        return p, q, v
    if i == 4:
        return t, p, v
    return v, p, q


# ---------------------------------------------------------------------------
# Inlined CSS tokenize fragments.  Used only by RE_COLOR.  Kept verbatim
# from textual/css/tokenize.py to preserve the upstream parse contract.
# ---------------------------------------------------------------------------

_DECIMAL = r"-?\d+\.?\d*"
_PERCENT = r"-?\d+\.?\d*%"
_COMMA = r"\s*,\s*"
_OPEN_BRACE = r"\(\s*"
_CLOSE_BRACE = r"\s*\)"


# Re.VERBOSE-free.  re1.5 silently accepts but does not honour any flags
# (research 03 §"Per-module table"); the alternation arms are kept
# identical to upstream, only the layout whitespace is gone.
RE_COLOR = re.compile(
    r"^"
    r"\#([0-9a-fA-F]{3})$|"
    r"\#([0-9a-fA-F]{4})$|"
    r"\#([0-9a-fA-F]{6})$|"
    r"\#([0-9a-fA-F]{8})$|"
    + r"rgb" + _OPEN_BRACE + r"(" + _DECIMAL + _COMMA + _DECIMAL + _COMMA + _DECIMAL + r")" + _CLOSE_BRACE + r"$|"
    + r"rgba" + _OPEN_BRACE + r"(" + _DECIMAL + _COMMA + _DECIMAL + _COMMA + _DECIMAL + _COMMA + _DECIMAL + r")" + _CLOSE_BRACE + r"$|"
    + r"hsl" + _OPEN_BRACE + r"(" + _DECIMAL + _COMMA + _PERCENT + _COMMA + _PERCENT + r")" + _CLOSE_BRACE + r"$|"
    + r"hsla" + _OPEN_BRACE + r"(" + _DECIMAL + _COMMA + _PERCENT + _COMMA + _PERCENT + _COMMA + _DECIMAL + r")" + _CLOSE_BRACE + r"$"
)


# ---------------------------------------------------------------------------
# ANSI / named-colour tables.  Inlined from
# textual/_color_constants.py — the upstream module is 192 LoC of pure
# data and pulls no other deps, so inlining it here is cleaner than
# carrying a near-empty sibling.  Kept in sync with the upstream order
# (ANSI 0..15) so ``ANSI_COLORS.index(name)`` matches Rich's
# ``ANSI_COLOR_NAMES``.
# ---------------------------------------------------------------------------

ANSI_COLORS = [
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "bright_black", "bright_red", "bright_green", "bright_yellow",
    "bright_blue", "bright_magenta", "bright_cyan", "bright_white",
]

# Sentinel colour names that v0.1 ``Style(...)`` will reference directly.
# The trimmed table covers ``transparent`` + the 16 ANSI primaries +
# every name FR-TUI-33's parser surface promises (named CSS colours come
# from ``picolet_tui._rich.color.ANSI_COLOR_NAMES`` via the rgb tuple
# table when the runtime needs them; for v0.1 the surface is the ANSI
# set plus three sentinels).  Extension is mechanical — add (name,
# (r, g, b)) entries — and intentionally deferred until a v0.1 widget
# needs them.
COLOR_NAME_TO_RGB = {
    "transparent": (0, 0, 0, 0),
    "ansi_black": (0, 0, 0),
    "ansi_red": (128, 0, 0),
    "ansi_green": (0, 128, 0),
    "ansi_yellow": (128, 128, 0),
    "ansi_blue": (0, 0, 128),
    "ansi_magenta": (128, 0, 128),
    "ansi_cyan": (0, 128, 128),
    "ansi_white": (192, 192, 192),
    "ansi_bright_black": (128, 128, 128),
    "ansi_bright_red": (255, 0, 0),
    "ansi_bright_green": (0, 255, 0),
    "ansi_bright_yellow": (255, 255, 0),
    "ansi_bright_blue": (0, 0, 255),
    "ansi_bright_magenta": (255, 0, 255),
    "ansi_bright_cyan": (0, 255, 255),
    "ansi_bright_white": (255, 255, 255),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "magenta": (255, 0, 255),
    "cyan": (0, 255, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
}


# ---------------------------------------------------------------------------
# HSL / HSV.  ``collections.namedtuple`` subclasses (NamedTuple is absent
# from the typing shim; MicroPython cannot call tuple.__new__ in a
# subclass, and its namedtuple is a C builtin).  Three positional
# components, indexed access, named attribute access, hashable for
# lru_cache keying.
# ---------------------------------------------------------------------------


# namedtuple base because MicroPython cannot call tuple.__new__ in a subclass.
class HSL(namedtuple("HSL", ("h", "s", "l"))):
    """A color in HSL (Hue, Saturation, Lightness) format.

    Values are floats in 0..1; ``css`` renders the canonical
    ``hsl(deg,sat%,light%)`` form.
    """

    __slots__ = ()

    @property
    def css(self):
        h, s, l = self

        def _as_str(number):
            # rstrip mirrors upstream: ``25.0`` → ``25``, ``25.5`` → ``25.5``.
            return "{:.1f}".format(number).rstrip("0").rstrip(".")

        return "hsl({},{}%,{}%)".format(
            _as_str(h * 360), _as_str(s * 100), _as_str(l * 100)
        )


# namedtuple base because MicroPython cannot call tuple.__new__ in a subclass.
class HSV(namedtuple("HSV", ("h", "s", "v"))):
    """A color in HSV (Hue, Saturation, Value) format.  Floats 0..1."""

    __slots__ = ()


# ---------------------------------------------------------------------------
# ColorParseError.  Identical to upstream apart from dropping the
# get_suggestion call site that fed ``suggested_color``.
# ---------------------------------------------------------------------------


class ColorParseError(Exception):
    """A color failed to parse.

    ``suggested_color`` is retained on the type for API parity with
    upstream but is always ``None`` in this port (see module docstring,
    ``get_suggestion`` removal rationale).
    """

    def __init__(self, message, suggested_color=None):
        super().__init__(message)
        self.suggested_color = suggested_color


# ---------------------------------------------------------------------------
# Color.  Six-component tuple: r, g, b, a, ansi, auto.  Unlike the
# upstream NamedTuple there are no constructor defaults — MicroPython's
# namedtuple cannot carry them — so every call site passes all six
# fields explicitly.
# ---------------------------------------------------------------------------


# namedtuple base because MicroPython cannot call tuple.__new__ in a subclass.
class Color(namedtuple("Color", ("r", "g", "b", "a", "ansi", "auto"))):
    """A color with red/green/blue/alpha components, plus optional ANSI
    index and auto-contrast flag.

    Field layout matches the upstream NamedTuple, but construction
    requires all six fields (MicroPython's namedtuple has no defaults)::

        Color(r, g, b, a, ansi, auto)

    ``ansi`` carries the ANSI index when set (r/g/b are the resolved
    triplet kept for theme rendering); ``auto`` flags auto-contrast.
    """

    __slots__ = ()

    def __repr__(self):
        r, g, b, a, ansi, auto = self
        parts = ["{}, {}, {}".format(r, g, b)]
        if a != 1.0:
            parts.append("a={}".format(a))
        if ansi is not None:
            parts.append("ansi={}".format(ansi))
        if auto:
            parts.append("auto=True")
        return "Color({})".format(", ".join(parts))

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def automatic(cls, alpha_percentage=100.0):
        """Create an automatic color (resolves to white/black at render time)."""
        return cls(0, 0, 0, alpha_percentage / 100.0, None, True)

    @classmethod
    @lru_cache(maxsize=128)
    def from_rich_color(cls, rich_color, theme=None, foreground=True, ansi=True):
        """Adapt a ``_rich.color.Color`` into the Textual six-tuple form.

        Bridges the downgrade ladder owned by ``_rich.color`` (FR-TUI-40)
        back into the Textual surface that widgets paint against.
        """
        if rich_color is None:
            return TRANSPARENT
        if ansi:
            if rich_color.triplet is not None:
                r, g, b = rich_color.triplet
            else:
                r, g, b = 0, 0, 0
            if rich_color.type == ColorType.DEFAULT:
                return Color(r, g, b, 1.0, -1, False)
            if rich_color.type == ColorType.STANDARD:
                return Color(r, g, b, 1.0, rich_color.number, False)
        r, g, b = rich_color.get_truecolor(theme, foreground=foreground)
        return cls(
            r,
            g,
            b,
            1.0,
            rich_color.number if rich_color.is_system_defined else None,
            False,
        )

    @classmethod
    def from_hsl(cls, h, s, l):
        """Construct a Color from HSL components (all in 0..1)."""
        r, g, b = _hls_to_rgb(h, l, s)
        return cls(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5), 1.0, None, False
        )

    @classmethod
    def from_hsv(cls, h, s, v):
        """Construct a Color from HSV components (all in 0..1)."""
        r, g, b = _hsv_to_rgb(h, s, v)
        return cls(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5), 1.0, None, False
        )

    # ------------------------------------------------------------------
    # Read-only views.  Each returns a fresh value; widgets cache locally
    # if they need to amortise.
    # ------------------------------------------------------------------

    @property
    def inverse(self):
        r, g, b, a, _, _ = self
        return Color(255 - r, 255 - g, 255 - b, a, None, False)

    @property
    def is_transparent(self):
        return self.a == 0 and self.ansi is None

    @property
    def clamped(self):
        """Return a copy with every component coerced into its valid range."""
        r, g, b, a, ansi, auto = self
        return Color(
            _clamp(r, 0, 255),
            _clamp(g, 0, 255),
            _clamp(b, 0, 255),
            _clamp(a, 0.0, 1.0),
            ansi,
            auto,
        )

    @property
    def rich_color(self):
        """The corresponding ``_rich.color.Color`` for compositor emission.

        Not lru-cached on the instance — the upstream ``@lru_cache(1024)``
        decoration of a property is a CPython implementation detail that
        relies on the descriptor protocol behaving exactly so; the
        functools shim does not promise the same cache-on-property
        semantics and the win is negligible for v0.1 frame counts.
        """
        r, g, b, _a, ansi, _ = self
        if ansi is not None:
            return RichColor.parse("default") if ansi < 0 else RichColor.from_ansi(ansi)
        return RichColor(
            "#{:02x}{:02x}{:02x}".format(r, g, b),
            _TRUECOLOR,
            None,
            ColorTriplet(r, g, b),
        )

    @property
    def normalized(self):
        r, g, b, _a, _, _ = self
        return (r / 255, g / 255, b / 255)

    @property
    def rgb(self):
        r, g, b, _, _, _ = self
        return (r, g, b)

    @property
    def hsl(self):
        r, g, b = self.normalized
        h, l, s = _rgb_to_hls(r, g, b)
        return HSL(h, s, l)

    @property
    def hsv(self):
        r, g, b = self.normalized
        h, s, v = _rgb_to_hsv(r, g, b)
        return HSV(h, s, v)

    @property
    def brightness(self):
        """ITU-R BT.601 luma in 0..1 — drives ``get_contrast_text``."""
        r, g, b = self.normalized
        return (299 * r + 587 * g + 114 * b) / 1000

    @property
    def hex(self):
        r, g, b, a, ansi, _ = self.clamped
        if ansi is not None:
            return "ansi_default" if ansi == -1 else "ansi_" + ANSI_COLORS[ansi]
        if a == 1:
            return "#{:02X}{:02X}{:02X}".format(r, g, b)
        return "#{:02X}{:02X}{:02X}{:02X}".format(r, g, b, int(a * 255))

    @property
    def hex6(self):
        r, g, b, _a, _, _ = self.clamped
        return "#{:02X}{:02X}{:02X}".format(r, g, b)

    @property
    def css(self):
        r, g, b, a, ansi, auto = self
        if auto:
            alpha_percentage = _clamp(a, 0.0, 1.0) * 100.0
            if alpha_percentage == 100:
                return "auto"
            if not alpha_percentage % 1:
                return "auto {}%".format(int(alpha_percentage))
            return "auto {:.1f}%".format(alpha_percentage)
        if ansi is not None:
            return "ansi_default" if ansi == -1 else "ansi_" + ANSI_COLORS[ansi]
        return "rgb({},{},{})".format(r, g, b) if a == 1 else "rgba({},{},{},{})".format(r, g, b, a)

    @property
    def monochrome(self):
        """Luma-weighted grayscale (Rec. 709 coefficients)."""
        r, g, b, a, _, _ = self
        gray = round(r * 0.2126 + g * 0.7152 + b * 0.0722)
        return Color(gray, gray, gray, a, None, False)

    # ------------------------------------------------------------------
    # Mutators (each returns a new Color — the type is immutable).
    # ------------------------------------------------------------------

    def with_alpha(self, alpha):
        r, g, b, _, _, _ = self
        return Color(r, g, b, alpha, None, False)

    def multiply_alpha(self, alpha):
        if self.ansi is not None:
            return self
        r, g, b, a, _ansi, auto = self
        return Color(r, g, b, a * alpha, None, auto)

    @lru_cache(maxsize=128)
    def blend(self, destination, factor, alpha=None):
        """Linear-RGB blend toward ``destination`` at ``factor`` in 0..1.

        Upstream caches 1024; the NFR-TUI-6 ceiling is 128.  The cache
        is keyed on (self, destination, factor, alpha) — both ends are
        ``Color`` tuples which hash by component, and the inputs reach
        steady state quickly inside any single frame's render path.
        """
        if destination.auto:
            destination = self.get_contrast_text(destination.a)
        if destination.ansi is not None:
            return destination
        if factor <= 0:
            return self
        if factor >= 1:
            return destination
        r1, g1, b1, a1, _, _ = self
        r2, g2, b2, a2, _, _ = destination
        if alpha is None:
            new_alpha = a1 + (a2 - a1) * factor
        else:
            new_alpha = alpha
        return Color(
            int(r1 + (r2 - r1) * factor),
            int(g1 + (g2 - g1) * factor),
            int(b1 + (b2 - b1) * factor),
            new_alpha,
            None,
            False,
        )

    @lru_cache(maxsize=128)
    def tint(self, color):
        """Composite ``color`` over ``self`` using ``color.a`` as weight.

        Differs from ``blend`` in that the result's alpha stays the
        background's alpha — Textual uses ``tint`` to paint semi-
        transparent overlays without disturbing the layer's opacity.
        """
        r1, g1, b1, a1, ansi1, _ = self
        if ansi1 is not None:
            return self
        r2, g2, b2, a2, ansi2, _ = color
        if ansi2 is not None:
            return self
        return Color(
            int(r1 + (r2 - r1) * a2),
            int(g1 + (g2 - g1) * a2),
            int(b1 + (b2 - b1) * a2),
            a1,
            None,
            False,
        )

    def __add__(self, other):
        if isinstance(other, Color):
            return self.blend(other, other.a, 1.0)
        if other is None:
            return self
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, Color):
            return self.blend(other, other.a, 1.0)
        if other is None:
            return self
        return NotImplemented

    # ------------------------------------------------------------------
    # Parser.  FR-TUI-33 is normative on the accepted forms.
    # ------------------------------------------------------------------

    @classmethod
    @lru_cache(maxsize=128)
    def parse(cls, color_text):
        """Parse a colour name or CSS-style colour string.

        Accepts (per FR-TUI-33 plus the upstream surface):
          - ``"transparent"`` / ``"ansi_<name>"`` / a key in
            ``COLOR_NAME_TO_RGB``
          - ``"#RGB"`` / ``"#RGBA"`` / ``"#RRGGBB"`` / ``"#RRGGBBAA"``
          - ``"rgb(R,G,B)"`` / ``"rgba(R,G,B,A)"``
          - ``"hsl(H,S%,L%)"`` / ``"hsla(H,S%,L%,A)"``

        Anything else raises ``ColorParseError``.  Pre-existing
        ``Color`` instances pass through unchanged so callers do not
        need to type-narrow before calling.
        """
        if isinstance(color_text, Color):
            return color_text
        if color_text == "ansi_default":
            return cls(0, 0, 0, 1.0, -1, False)
        if color_text.startswith("ansi_"):
            name = color_text[5:]
            try:
                ansi = ANSI_COLORS.index(name)
            except ValueError:
                pass
            else:
                triplet = COLOR_NAME_TO_RGB.get(color_text)
                if triplet is not None:
                    return cls(triplet[0], triplet[1], triplet[2], 1.0, ansi, False)
        color_from_name = COLOR_NAME_TO_RGB.get(color_text)
        if color_from_name is not None:
            # Entries are (r, g, b) except "transparent", which carries
            # an explicit alpha as a 4-tuple.
            if len(color_from_name) == 4:
                r, g, b, a = color_from_name
            else:
                r, g, b = color_from_name
                a = 1.0
            return cls(r, g, b, a, None, False)
        color_match = RE_COLOR.match(color_text)
        if color_match is None:
            raise ColorParseError(
                "failed to parse {!r} as a color".format(color_text)
            )
        (
            rgb_hex_triple,
            rgb_hex_quad,
            rgb_hex,
            rgba_hex,
            rgb,
            rgba,
            hsl,
            hsla,
        ) = color_match.groups()

        if rgb_hex_triple is not None:
            r, g, b = rgb_hex_triple
            return cls(
                int(r + r, 16), int(g + g, 16), int(b + b, 16), 1.0, None, False
            )
        if rgb_hex_quad is not None:
            r, g, b, a = rgb_hex_quad
            return cls(
                int(r + r, 16),
                int(g + g, 16),
                int(b + b, 16),
                int(a + a, 16) / 255.0,
                None,
                False,
            )
        if rgb_hex is not None:
            # Explicit slicing replaces operator.itemgetter (one less
            # dep, one less closure allocation).
            r = int(rgb_hex[0:2], 16)
            g = int(rgb_hex[2:4], 16)
            b = int(rgb_hex[4:6], 16)
            return cls(r, g, b, 1.0, None, False)
        if rgba_hex is not None:
            r = int(rgba_hex[0:2], 16)
            g = int(rgba_hex[2:4], 16)
            b = int(rgba_hex[4:6], 16)
            a = int(rgba_hex[6:8], 16)
            return cls(r, g, b, a / 255.0, None, False)
        if rgb is not None:
            r, g, b = [_clamp(int(float(value)), 0, 255) for value in rgb.split(",")]
            return cls(r, g, b, 1.0, None, False)
        if rgba is not None:
            float_r, float_g, float_b, float_a = [
                float(value) for value in rgba.split(",")
            ]
            return cls(
                _clamp(int(float_r), 0, 255),
                _clamp(int(float_g), 0, 255),
                _clamp(int(float_b), 0, 255),
                _clamp(float_a, 0.0, 1.0),
                None,
                False,
            )
        if hsl is not None:
            h_s, s_s, l_s = hsl.split(",")
            h = float(h_s) % 360 / 360
            s = _percentage_string_to_float(s_s)
            l = _percentage_string_to_float(l_s)
            return Color.from_hsl(h, s, l)
        if hsla is not None:
            h_s, s_s, l_s, a_s = hsla.split(",")
            h = float(h_s) % 360 / 360
            s = _percentage_string_to_float(s_s)
            l = _percentage_string_to_float(l_s)
            a = _clamp(float(a_s), 0.0, 1.0)
            return Color.from_hsl(h, s, l).with_alpha(a)
        # Unreachable — RE_COLOR matched but none of the named groups
        # captured.  Defensive only.
        raise AssertionError("RE_COLOR matched but no group captured")

    # ------------------------------------------------------------------
    # Darken / lighten.  Upstream goes through Lab for perceptually
    # uniform lightness steps; v0.1 drops the Lab path with animation
    # (synthesis D7) and falls back to HLS-lightness arithmetic.  This
    # tracks Rich's own ``rich.color.blend_rgb`` ergonomics and every
    # pre-CSS-color-mix() palette adjustment in the wild.  Visible
    # difference vs Lab is small for amount ≤ 0.3 (the static-widget
    # range); larger amounts diverge but no v0.1 widget calls them.
    # ------------------------------------------------------------------

    @lru_cache(maxsize=128)
    def darken(self, amount, alpha=None):
        """Decrease lightness by ``amount`` (0..1) via HLS adjustment."""
        r, g, b = self.normalized
        h, l, s = _rgb_to_hls(r, g, b)
        l = _clamp(l - amount, 0.0, 1.0)
        nr, ng, nb = _hls_to_rgb(h, l, s)
        return Color(
            int(nr * 255 + 0.5),
            int(ng * 255 + 0.5),
            int(nb * 255 + 0.5),
            self.a if alpha is None else alpha,
            None,
            False,
        ).clamped

    def lighten(self, amount, alpha=None):
        """Inverse of ``darken`` (positive ``amount`` brightens)."""
        return self.darken(-amount, alpha)

    @lru_cache(maxsize=128)
    def get_contrast_text(self, alpha=0.95):
        """Pick off-white or off-black for legible text against ``self``.

        The 0.5 split point is the well-worn "perceived brightness"
        threshold (ITU-R BT.601 weights, see ``brightness``).  Stricter
        WCAG contrast checks are out of scope for v0.1 — widgets that
        need them can call ``brightness`` directly.
        """
        return (WHITE if self.brightness < 0.5 else BLACK).with_alpha(alpha)


# ---------------------------------------------------------------------------
# Gradient.  Used by ProgressBar candidates and v0.2 palette widgets.
# Animation is out (D7) but a sampled gradient is a useful static
# primitive; the implementation depends only on ``Color.blend`` so it
# costs no extra deps.
# ---------------------------------------------------------------------------


class Gradient:
    """A pre-sampled colour ramp between two or more stops.

    ``quality`` is the number of sample points; intermediate colours are
    linearly interpolated.  Construction validates that the first stop
    is at 0.0 and the last is at 1.0 — matching upstream.
    """

    def __init__(self, *stops, quality=50):
        parse = Color.parse
        self._stops = sorted(
            [
                (position, parse(color)) if isinstance(color, str) else (position, color)
                for position, color in stops
            ]
        )
        if len(stops) < 2:
            raise ValueError("At least 2 stops required.")
        if self._stops[0][0] != 0.0:
            raise ValueError("First stop must be 0.")
        if self._stops[-1][0] != 1.0:
            raise ValueError("Last stop must be 1.")
        self._quality = quality
        self._colors = None

    @classmethod
    def from_colors(cls, *colors, quality=50):
        """Build a gradient with evenly-spaced stops from a colour list."""
        if len(colors) < 2:
            raise ValueError("Two or more colors required.")
        stops = [(i / (len(colors) - 1), Color.parse(c)) for i, c in enumerate(colors)]
        return cls(*stops, quality=quality)

    @property
    def colors(self):
        """The sampled list, computed once on first access."""
        if self._colors is None:
            position = 0
            quality = self._quality
            colors = []
            (stop1, color1), (stop2, color2) = self._stops[0:2]
            for step_position in range(quality):
                step = step_position / (quality - 1)
                while step > stop2:
                    position += 1
                    (stop1, color1), (stop2, color2) = self._stops[
                        position : position + 2
                    ]
                colors.append(color1.blend(color2, (step - stop1) / (stop2 - stop1)))
            self._colors = colors
        return self._colors

    def get_color(self, position):
        """Look up the colour at ``position`` in 0..1 (clamped)."""
        if position <= 0:
            return self.colors[0]
        if position >= 1:
            return self.colors[-1]
        color_position = position * (self._quality - 1)
        color_index = int(color_position)
        color1, color2 = self.colors[color_index : color_index + 2]
        return color1.blend(color2, color_position % 1)

    def get_rich_color(self, position):
        """Same as ``get_color`` but returns the ``_rich.color.Color``."""
        return self.get_color(position).rich_color


# ---------------------------------------------------------------------------
# Sentinels.  Constructed after Color is defined so they can use the
# parser; TRANSPARENT specifically requires the named-colour table.
# ---------------------------------------------------------------------------

WHITE = Color(255, 255, 255, 1.0, None, False)
BLACK = Color(0, 0, 0, 1.0, None, False)
TRANSPARENT = Color.parse("transparent")
