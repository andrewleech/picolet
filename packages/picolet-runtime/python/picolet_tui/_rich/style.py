"""picolet_tui._rich.style -- Rich ``Style`` ported for picolet-tui.

Upstream
--------
Source : https://raw.githubusercontent.com/Textualize/rich/master/rich/style.py
Pinned : Textualize/rich master @ 46cebbb032f920eb096efbaf23cdc6fe9dd541f7
         (~796 LoC at port time).

Role
----
Carries the SGR-attribute bits, foreground/background ``Color``,
optional link URL, and a ``meta`` payload that downstream renderers
attach to ranges of text.  ``Style + Style`` is the right-hand-override
merge used by every layered renderer in the trimmed Rich tier and by
the picolet-tui compositor (see FR-TUI-37 ``default_style + styles``).

REMOVED vs upstream
-------------------
* ``@rich_repr`` class decorator + ``__rich_repr__`` method.  ``rich.repr``
  is Tier 1 in the synthesis port plan but has not yet been ported (same
  precedent as ``color.py``); the decorator is a debug-ergonomics
  convenience and not on the compositor path.  Drop both rather than
  introduce a conditional import.
* ``pickle.dumps / loads`` and the ``_meta`` bytes round-trip
  (synthesis D4).  ``Style.meta`` is now a plain dict stored on the
  instance; the public ``.meta`` property returns it as-is (caller may
  see live mutations of someone else's dict — matches FR-TUI-36 ref
  semantics).  ``__eq__`` consequently does deep-dict-equal on meta
  rather than comparing pickled byte strings.
* ``random.getrandbits`` + ``itertools.count`` link-id generator
  replaced with a private module-level counter and a CRC32-flavoured
  starting seed derived from ``id(object())``.  Avoids pulling
  ``random`` (it works on MP but adds a dep for nothing) and matches
  the "stable id per Style with link" invariant the compositor relies
  on (a different Style with the same link must get a different id so
  the OSC 8 sequence is reissued).
* ``Style.get_html_style`` removed.  HTML export is part of Rich's SVG
  export pipeline (Tier 4 in the trim list); FR-TUI-32..37 do not
  reference HTML output and no v0.1 widget calls it.  Trimming ~35 LoC.
* ``Style.test`` removed -- it writes ANSI directly to stdout, which
  collides with FR-TUI-78's stdout capture and NFR-TUI-29's "framework
  never writes to stdout outside the compositor" rule.
* ``StyleStack`` retained -- the markup parser (Tier 2, to be ported)
  builds on it.
* The complex named-group regex the prompt warned about does not exist
  in this upstream revision; ``Style.parse`` already walks
  ``style_definition.split()`` as a hand-rolled token loop.  Ported
  verbatim with the ``from None`` exception chaining stripped (MP
  ``raise`` syntax differs from CPython's "raise X from None" form).

Adjustments for MicroPython
---------------------------
* ``from functools import lru_cache`` -> shim path.  Every cache size
  > 128 is clamped to 128 per NFR-TUI-6 / synthesis R4.  The upstream
  caps are: ``Style.normalize`` 1024 -> 128; ``Style.parse`` 4096 ->
  128; ``Style._add`` 1024 -> 128; ``get_html_style`` removed; the 128
  cache on ``clear_meta_and_links`` is unchanged.
* ``from typing import Any, Dict, ...`` -> shim path.  Annotations are
  identity placeholders under the typing shim.
* ``from pickle import dumps, loads`` removed entirely (see D4 above).
* ``from operator import attrgetter`` -> inlined.  ``operator`` is
  available on MP but adding a dep for a single ``_hash_getter`` is
  not worth it; the hash function builds the tuple inline.
* ``raise ... from None`` chaining stripped -- MicroPython parses the
  ``from`` clause but does not store ``__cause__`` consistently and
  the upstream callers (``Style.normalize``) ignore the chain anyway.
* ``Style.STYLE_ATTRIBUTES`` class attribute kept as a plain dict --
  no enum, no frozenset.

Spec coverage
-------------
* FR-TUI-32 -- ``Style(...)`` is the keyword-only DSL surface that the
  v0.1 styling layer wraps.  The Rich ``Style`` ported here is the
  underlying carrier; the framework ``picolet_tui.style.Style`` (Phase
  4b) holds layout fields on top of it.
* FR-TUI-33 -- color/bgcolor validation happens via ``Color.parse``
  during ``_make_color``; bad input bubbles out as ``ColorParseError``.
* FR-TUI-36 -- ``.meta`` returns the live dict reference; no copy on
  read, no pickle round-trip.
* FR-TUI-37 -- ``__add__`` is the right-hand-override merge widget
  authors compose through.
* NFR-TUI-6 -- every wrapped callable's ``cache_info().maxsize <= 128``
  (clamps recorded in Adjustments above).
* NFR-TUI-19 -- counts against the ``_rich`` 60 KiB romfs sub-budget;
  removing pickle + html_style + test + rich_repr trims ~120 LoC from
  the upstream surface.
"""

from picolet_tui._shims.functools import lru_cache
from picolet_tui._shims.typing import (  # noqa: F401 -- annotations only
    Any, Dict, Iterable, List, Optional, Type, Union, cast,
)

from . import errors
from .color import Color, ColorParseError, ColorSystem, blend_rgb
from .terminal_theme import DEFAULT_TERMINAL_THEME, TerminalTheme


# Style instances and style definitions are often interchangeable on the
# caller side; the framework treats either as a "style spec".
StyleType = Union[str, "Style"]


# Link-id generator.  Upstream uses ``itertools.count(getrandbits(24))``;
# we substitute a plain counter with a process-startup seed.  The
# invariant is "two Style instances with the same link string get
# different ids so the OSC 8 emitter reissues" -- the counter alone is
# sufficient; the seed only varies the visible id between processes,
# which has no functional consequence on the compositor.
_link_id_counter = id(object()) & 0xFFFFFF


def _next_link_id():
    global _link_id_counter
    _link_id_counter += 1
    return _link_id_counter


class _Bit:
    """Descriptor returning the tri-state value of one attribute bit.

    The descriptor reads two parallel bitfields on the owner instance:
    ``_set_attributes`` (1 if the user explicitly set this attribute,
    either True or False) and ``_attributes`` (the actual True/False
    value when set).  Returns ``None`` when unset so callers can
    distinguish "explicitly turned off" from "inherits".
    """

    __slots__ = ("bit",)

    def __init__(self, bit_no):
        self.bit = 1 << bit_no

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        if obj._set_attributes & self.bit:
            return obj._attributes & self.bit != 0
        return None


class Style:
    """A terminal style.

    A terminal style consists of a color (``color``), a background color
    (``bgcolor``), and a number of attributes (bold, italic, ...).  The
    attributes are tri-state: each can be on (``True``), off (``False``),
    or unset (``None``).
    """

    # ``__slots__`` mirrors upstream so the per-Style heap cost stays
    # at one slot table not a __dict__ -- meaningful at NFR-TUI-5
    # heap caps for long-running TUI sessions.
    __slots__ = (
        "_color",
        "_bgcolor",
        "_attributes",
        "_set_attributes",
        "_link",
        "_link_id",
        "_ansi",
        "_style_definition",
        "_hash",
        "_null",
        "_meta",
    )

    # Bit-position -> SGR parameter string.  Upstream literal preserved
    # verbatim; the renderer indexes into this with positional bits.
    _style_map = {
        0: "1",
        1: "2",
        2: "3",
        3: "4",
        4: "5",
        5: "6",
        6: "7",
        7: "8",
        8: "9",
        9: "21",
        10: "51",
        11: "52",
        12: "53",
    }

    # Lookup used by the ``parse`` token loop.  Both long and short
    # spellings (e.g. ``"b"`` for ``"bold"``) match upstream.
    STYLE_ATTRIBUTES = {
        "dim": "dim",
        "d": "dim",
        "bold": "bold",
        "b": "bold",
        "italic": "italic",
        "i": "italic",
        "underline": "underline",
        "u": "underline",
        "blink": "blink",
        "blink2": "blink2",
        "reverse": "reverse",
        "r": "reverse",
        "conceal": "conceal",
        "c": "conceal",
        "strike": "strike",
        "s": "strike",
        "underline2": "underline2",
        "uu": "underline2",
        "frame": "frame",
        "encircle": "encircle",
        "overline": "overline",
        "o": "overline",
    }

    def __init__(
        self,
        *,
        color=None,
        bgcolor=None,
        bold=None,
        dim=None,
        italic=None,
        underline=None,
        blink=None,
        blink2=None,
        reverse=None,
        conceal=None,
        strike=None,
        underline2=None,
        frame=None,
        encircle=None,
        overline=None,
        link=None,
        meta=None,
    ):
        self._ansi = None
        self._style_definition = None

        def _make_color(value):
            return value if isinstance(value, Color) else Color.parse(value)

        self._color = None if color is None else _make_color(color)
        self._bgcolor = None if bgcolor is None else _make_color(bgcolor)
        # Upstream packs 13 tri-state booleans into two parallel 13-bit
        # bitfields.  The "X is not None and N" idiom is the upstream
        # form -- exploiting Python's short-circuit: when X is None the
        # AND yields False which is 0; when X is not None it yields N
        # (the bit position).
        self._set_attributes = sum(
            (
                bold is not None,
                dim is not None and 2,
                italic is not None and 4,
                underline is not None and 8,
                blink is not None and 16,
                blink2 is not None and 32,
                reverse is not None and 64,
                conceal is not None and 128,
                strike is not None and 256,
                underline2 is not None and 512,
                frame is not None and 1024,
                encircle is not None and 2048,
                overline is not None and 4096,
            )
        )
        self._attributes = (
            sum(
                (
                    bold and 1 or 0,
                    dim and 2 or 0,
                    italic and 4 or 0,
                    underline and 8 or 0,
                    blink and 16 or 0,
                    blink2 and 32 or 0,
                    reverse and 64 or 0,
                    conceal and 128 or 0,
                    strike and 256 or 0,
                    underline2 and 512 or 0,
                    frame and 1024 or 0,
                    encircle and 2048 or 0,
                    overline and 4096 or 0,
                )
            )
            if self._set_attributes
            else 0
        )

        self._link = link
        # D4: store meta as a plain dict reference, no pickle.  The
        # public ``.meta`` property hands the same object back.  ``None``
        # is preserved (rather than coerced to ``{}``) so we can
        # distinguish "no meta supplied" from "empty meta" at merge time.
        self._meta = meta
        self._link_id = (
            "{}{}".format(_next_link_id(), id(self._meta) if self._meta else 0)
            if (link or meta)
            else ""
        )
        self._hash = None
        self._null = not (
            self._set_attributes or color or bgcolor or link or meta
        )

    @classmethod
    def null(cls):
        """Create a 'null' style equivalent to ``Style()``, but cached.

        ``NULL_STYLE`` is the singleton allocated once at module import;
        callers that want the canonical empty style should go through
        this method so identity comparisons (``style is Style.null()``)
        keep working.
        """
        return NULL_STYLE

    @classmethod
    def from_color(cls, color=None, bgcolor=None):
        """Create a new style with colours and no attributes.

        Args:
            color: A (foreground) ``Color`` or ``None``.
            bgcolor: A (background) ``Color`` or ``None``.
        """
        style = cls.__new__(Style)
        style._ansi = None
        style._style_definition = None
        style._color = color
        style._bgcolor = bgcolor
        style._set_attributes = 0
        style._attributes = 0
        style._link = None
        style._link_id = ""
        style._meta = None
        style._null = not (color or bgcolor)
        style._hash = None
        return style

    @classmethod
    def from_meta(cls, meta):
        """Create a new style carrying only meta data."""
        style = cls.__new__(Style)
        style._ansi = None
        style._style_definition = None
        style._color = None
        style._bgcolor = None
        style._set_attributes = 0
        style._attributes = 0
        style._link = None
        style._meta = meta
        style._link_id = "{}{}".format(_next_link_id(), id(meta) if meta else 0)
        style._hash = None
        style._null = not meta
        return style

    @classmethod
    def on(cls, meta=None, **handlers):
        """Create a blank style with meta information.

        Handlers are folded into the meta dict under ``"@<name>"`` keys
        so the compositor can dispatch click/hover events on the range.
        """
        meta = {} if meta is None else meta
        meta.update({"@" + key: value for key, value in handlers.items()})
        return cls.from_meta(meta)

    bold = _Bit(0)
    dim = _Bit(1)
    italic = _Bit(2)
    underline = _Bit(3)
    blink = _Bit(4)
    blink2 = _Bit(5)
    reverse = _Bit(6)
    conceal = _Bit(7)
    strike = _Bit(8)
    underline2 = _Bit(9)
    frame = _Bit(10)
    encircle = _Bit(11)
    overline = _Bit(12)

    @property
    def link_id(self):
        """Get the link id for the OSC 8 hyperlink sequence."""
        return self._link_id

    def __str__(self):
        """Re-generate the style definition from attributes.

        Cached on ``_style_definition``; ``parse() . __str__ ()`` is the
        normalisation round-trip used by ``normalize``.
        """
        if self._style_definition is None:
            attributes = []
            append = attributes.append
            bits = self._set_attributes
            # The bit-window guards (``& 0b...``) skip empty 4-bit
            # ranges in the common case.  Upstream literals preserved.
            if bits & 0b0000000001111:
                if bits & 1:
                    append("bold" if self.bold else "not bold")
                if bits & (1 << 1):
                    append("dim" if self.dim else "not dim")
                if bits & (1 << 2):
                    append("italic" if self.italic else "not italic")
                if bits & (1 << 3):
                    append("underline" if self.underline else "not underline")
            if bits & 0b0000111110000:
                if bits & (1 << 4):
                    append("blink" if self.blink else "not blink")
                if bits & (1 << 5):
                    append("blink2" if self.blink2 else "not blink2")
                if bits & (1 << 6):
                    append("reverse" if self.reverse else "not reverse")
                if bits & (1 << 7):
                    append("conceal" if self.conceal else "not conceal")
                if bits & (1 << 8):
                    append("strike" if self.strike else "not strike")
            if bits & 0b1111000000000:
                if bits & (1 << 9):
                    append("underline2" if self.underline2 else "not underline2")
                if bits & (1 << 10):
                    append("frame" if self.frame else "not frame")
                if bits & (1 << 11):
                    append("encircle" if self.encircle else "not encircle")
                if bits & (1 << 12):
                    append("overline" if self.overline else "not overline")
            if self._color is not None:
                append(self._color.name)
            if self._bgcolor is not None:
                append("on")
                append(self._bgcolor.name)
            if self._link:
                append("link")
                append(self._link)
            self._style_definition = " ".join(attributes) or "none"
        return self._style_definition

    def __bool__(self):
        """A Style is false if it has no attributes, colours, or links."""
        return not self._null

    def _make_ansi_codes(self, color_system):
        """Generate the SGR parameter string for this style.

        The cached ``_ansi`` field is keyed implicitly by the caller's
        chosen ``color_system``; callers that switch capability mid-run
        must clear the cache (the framework never does -- capability is
        latched once per FR-TUI-38).
        """
        if self._ansi is None:
            sgr = []
            append = sgr.append
            _style_map = self._style_map
            attributes = self._attributes & self._set_attributes
            if attributes:
                if attributes & 1:
                    append(_style_map[0])
                if attributes & 2:
                    append(_style_map[1])
                if attributes & 4:
                    append(_style_map[2])
                if attributes & 8:
                    append(_style_map[3])
                if attributes & 0b0000111110000:
                    for bit in range(4, 9):
                        if attributes & (1 << bit):
                            append(_style_map[bit])
                if attributes & 0b1111000000000:
                    for bit in range(9, 13):
                        if attributes & (1 << bit):
                            append(_style_map[bit])
            if self._color is not None:
                sgr.extend(self._color.downgrade(color_system).get_ansi_codes())
            if self._bgcolor is not None:
                sgr.extend(
                    self._bgcolor.downgrade(color_system).get_ansi_codes(
                        foreground=False
                    )
                )
            self._ansi = ";".join(sgr)
        return self._ansi

    @classmethod
    @lru_cache(maxsize=128)
    def normalize(cls, style):
        """Normalise a style definition.

        Two style definitions with the same effect produce the same
        normalised string -- used by Rich's markup parser to dedupe
        attribute spans.  Caches the round-trip; clamped to 128 per
        NFR-TUI-6.
        """
        try:
            return str(cls.parse(style))
        except errors.StyleSyntaxError:
            return style.strip().lower()

    @classmethod
    def pick_first(cls, *values):
        """Pick the first non-None style value."""
        for value in values:
            if value is not None:
                return value
        raise ValueError("expected at least one non-None style")

    def __eq__(self, other):
        if not isinstance(other, Style):
            return NotImplemented
        # D4 change: hash equality is sufficient for non-meta fields,
        # but two styles with different meta dict identities can still
        # be semantically equal.  Compare the full field tuple first,
        # then deep-compare meta (a plain dict ``==`` is element-wise).
        if (
            self._color is not other._color
            and self._color != other._color
        ):
            return False
        if (
            self._bgcolor is not other._bgcolor
            and self._bgcolor != other._bgcolor
        ):
            return False
        if self._attributes != other._attributes:
            return False
        if self._set_attributes != other._set_attributes:
            return False
        if self._link != other._link:
            return False
        # Meta deep-equal: ``None == None``, ``{} == {}``, and
        # ``{"a": 1} == {"a": 1}`` all hold under dict's __eq__.
        return self._meta == other._meta

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        # Cache the hash on first computation -- Style instances are
        # immutable from the outside (no public mutators on the bitfields
        # or colors), so the cached hash is stable for the instance
        # lifetime.  meta-bearing styles hash by id(self._meta) rather
        # than the dict contents -- dicts are unhashable, and Rich's
        # callers that put a Style into a set never mutate meta.
        if self._hash is not None:
            return self._hash
        # Inlined ``operator.attrgetter`` from upstream; building the
        # tuple inline avoids the attrgetter import for one call site.
        self._hash = hash(
            (
                self._color,
                self._bgcolor,
                self._attributes,
                self._set_attributes,
                self._link,
                id(self._meta) if self._meta is not None else 0,
            )
        )
        return self._hash

    @property
    def color(self):
        """The foreground color or ``None`` if unset."""
        return self._color

    @property
    def bgcolor(self):
        """The background color or ``None`` if unset."""
        return self._bgcolor

    @property
    def link(self):
        """Link URL, if set."""
        return self._link

    @property
    def transparent_background(self):
        """``True`` if the style has no background or the default one."""
        return self.bgcolor is None or self.bgcolor.is_default

    @property
    def background_style(self):
        """A new Style carrying the background colour only."""
        return Style(bgcolor=self.bgcolor)

    @property
    def meta(self):
        """The meta dict -- D4 reference semantics, no copy.

        FR-TUI-36: ``Style(meta={"id": "abc"}).meta is style.meta``.
        Returns ``{}`` (a fresh dict) when no meta was supplied so
        ``style.meta["x"] = 1`` does not crash on ``None``; the caller
        is responsible for not relying on that fresh dict surviving
        across calls (it doesn't -- pass an explicit meta to keep one).
        """
        return {} if self._meta is None else self._meta

    @property
    def without_color(self):
        """Get a copy of the style with color removed."""
        if self._null:
            return NULL_STYLE
        style = self.__new__(Style)
        style._ansi = None
        style._style_definition = None
        style._color = None
        style._bgcolor = None
        style._attributes = self._attributes
        style._set_attributes = self._set_attributes
        style._link = self._link
        style._link_id = "{}".format(_next_link_id()) if self._link else ""
        style._null = False
        style._meta = None
        style._hash = None
        return style

    @classmethod
    @lru_cache(maxsize=128)
    def parse(cls, style_definition):
        """Parse a style definition string into a ``Style``.

        Hand-rolled token loop -- upstream uses the same loop, no regex
        involved.  Tokens recognised:

        * ``"on <color>"``     -- background color
        * ``"not <attr>"``     -- explicitly clear an attribute
        * ``"link <url>"``     -- attach an OSC 8 link
        * bare attribute name  -- set the attribute (long or short form
                                  from ``STYLE_ATTRIBUTES``)
        * bare color name      -- foreground color

        Bad syntax raises ``errors.StyleSyntaxError``.  Cache clamped
        to 128 per NFR-TUI-6 (upstream was 4096).
        """
        if style_definition.strip() == "none" or not style_definition:
            return cls.null()

        STYLE_ATTRIBUTES = cls.STYLE_ATTRIBUTES
        color = None
        bgcolor = None
        attributes = {}
        link = None

        words = iter(style_definition.split())
        for original_word in words:
            word = original_word.lower()
            if word == "on":
                word = next(words, "")
                if not word:
                    raise errors.StyleSyntaxError(
                        "color expected after 'on'"
                    )
                try:
                    Color.parse(word)
                except ColorParseError as error:
                    # MP raise-from-None chaining stripped; the message
                    # carries the upstream context inline.
                    raise errors.StyleSyntaxError(
                        "unable to parse {!r} as background color; {}".format(
                            word, error
                        )
                    )
                bgcolor = word

            elif word == "not":
                word = next(words, "")
                attribute = STYLE_ATTRIBUTES.get(word)
                if attribute is None:
                    raise errors.StyleSyntaxError(
                        "expected style attribute after 'not', found {!r}".format(
                            word
                        )
                    )
                attributes[attribute] = False

            elif word == "link":
                word = next(words, "")
                if not word:
                    raise errors.StyleSyntaxError("URL expected after 'link'")
                link = word

            elif word in STYLE_ATTRIBUTES:
                attributes[STYLE_ATTRIBUTES[word]] = True

            else:
                try:
                    Color.parse(word)
                except ColorParseError as error:
                    raise errors.StyleSyntaxError(
                        "unable to parse {!r} as color; {}".format(word, error)
                    )
                color = word

        style = Style(color=color, bgcolor=bgcolor, link=link, **attributes)
        return style

    @classmethod
    def combine(cls, styles):
        """Combine an iterable of styles via repeated ``__add__``."""
        iter_styles = iter(styles)
        return sum(iter_styles, next(iter_styles))

    @classmethod
    def chain(cls, *styles):
        """Combine positional styles via repeated ``__add__``."""
        iter_styles = iter(styles)
        return sum(iter_styles, next(iter_styles))

    def copy(self):
        """Get a fresh ``Style`` carrying the same fields."""
        if self._null:
            return NULL_STYLE
        style = self.__new__(Style)
        style._ansi = self._ansi
        style._style_definition = self._style_definition
        style._color = self._color
        style._bgcolor = self._bgcolor
        style._attributes = self._attributes
        style._set_attributes = self._set_attributes
        style._link = self._link
        style._link_id = "{}".format(_next_link_id()) if self._link else ""
        style._hash = self._hash
        style._null = False
        # D4: alias meta on copy.  Two Style instances sharing one meta
        # dict will see each other's mutations -- callers that want
        # isolation call ``dict(style.meta)`` themselves.
        style._meta = self._meta
        return style

    @lru_cache(maxsize=128)
    def clear_meta_and_links(self):
        """Return a copy with link and meta data stripped."""
        if self._null:
            return NULL_STYLE
        style = self.__new__(Style)
        style._ansi = self._ansi
        style._style_definition = self._style_definition
        style._color = self._color
        style._bgcolor = self._bgcolor
        style._attributes = self._attributes
        style._set_attributes = self._set_attributes
        style._link = None
        style._link_id = ""
        style._hash = None
        style._null = False
        style._meta = None
        return style

    def update_link(self, link=None):
        """Return a copy with a different link URL."""
        style = self.__new__(Style)
        style._ansi = self._ansi
        style._style_definition = self._style_definition
        style._color = self._color
        style._bgcolor = self._bgcolor
        style._attributes = self._attributes
        style._set_attributes = self._set_attributes
        style._link = link
        style._link_id = "{}".format(_next_link_id()) if link else ""
        style._hash = None
        style._null = False
        style._meta = self._meta
        return style

    def render(self, text="", *, color_system=ColorSystem.TRUECOLOR,
               legacy_windows=False):
        """Render ANSI bytes for this style around ``text``.

        ``color_system=None`` short-circuits to plain text (mono path).
        ``legacy_windows=True`` suppresses the OSC 8 link wrap, since
        legacy conhost does not parse it.
        """
        if not text or color_system is None:
            return text
        attrs = self._ansi or self._make_ansi_codes(color_system)
        rendered = (
            "\x1b[" + attrs + "m" + text + "\x1b[0m" if attrs else text
        )
        if self._link and not legacy_windows:
            rendered = (
                "\x1b]8;id={};{}\x1b\\{}\x1b]8;;\x1b\\".format(
                    self._link_id, self._link, rendered
                )
            )
        return rendered

    @lru_cache(maxsize=128)
    def _add(self, style):
        """Right-side override merge -- the hot path for layered styles.

        Cached because the compositor merges the same parent+child
        Style pairs every frame.  Cache clamp 1024 -> 128 per
        NFR-TUI-6.
        """
        if style is None or style._null:
            return self
        if self._null:
            return style
        new_style = self.__new__(Style)
        new_style._ansi = None
        new_style._style_definition = None
        new_style._color = style._color or self._color
        new_style._bgcolor = style._bgcolor or self._bgcolor
        new_style._attributes = (
            self._attributes & ~style._set_attributes
        ) | (style._attributes & style._set_attributes)
        new_style._set_attributes = self._set_attributes | style._set_attributes
        new_style._link = style._link or self._link
        new_style._link_id = style._link_id or self._link_id
        new_style._null = style._null
        # D4 meta merge: shallow dict-merge with right-hand override.
        # Upstream did ``dumps({**self.meta, **style.meta})`` to round-
        # trip via pickle for immutability; we keep the dict-merge but
        # store the result directly.  The result is a new dict (so the
        # combined style does not alias either source's meta), but the
        # values inside are still references -- consistent with D4.
        if self._meta and style._meta:
            new_style._meta = {}
            new_style._meta.update(self._meta)
            new_style._meta.update(style._meta)
        else:
            new_style._meta = self._meta or style._meta
        new_style._hash = None
        return new_style

    def __add__(self, style):
        combined_style = self._add(style)
        # Links carry a unique id per Style; copy on link so two
        # composed styles with the same link still issue distinct OSC 8
        # sequences (the compositor uses the id to detect link reissue).
        return combined_style.copy() if combined_style.link else combined_style


# Cached singleton for ``Style.null()`` -- one instance for the lifetime
# of the process.  ``NULL_STYLE._null is True`` and ``bool(NULL_STYLE)``
# is False, so callers can use it as a falsy default.
NULL_STYLE = Style()


class StyleStack:
    """A stack of styles used by the markup parser to compose nested spans.

    Each ``push(style)`` combines ``style`` with the current top via
    ``__add__`` and stores the result; ``pop()`` discards the top and
    returns the new top.  The stack always carries at least one element
    (the default style passed at construction), so ``current`` never
    raises.
    """

    __slots__ = ("_stack",)

    def __init__(self, default_style):
        self._stack = [default_style]

    def __repr__(self):
        return "<stylestack {!r}>".format(self._stack)

    @property
    def current(self):
        """The style at the top of the stack."""
        return self._stack[-1]

    def push(self, style):
        """Combine ``style`` with the current top and push the result."""
        self._stack.append(self._stack[-1] + style)

    def pop(self):
        """Discard the top of the stack; return the new current."""
        self._stack.pop()
        return self._stack[-1]
