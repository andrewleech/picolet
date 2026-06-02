"""picolet_tui._rich.text - Rich's styled Text class.

Ported from Textualize/rich master @ f564d4c82869540970d5f99622aab36e2aeb158a
(``rich/text.py``, ~1360 LoC upstream).  Tier 4 of the Rich subset: this
is the second-heaviest module in the port pack and underpins every
widget that emits styled prose (Static, Label, Tree node text, the
Console's render pipeline).

REMOVED vs upstream
-------------------
* ``from ._loop import loop_last`` -- inlined as ``_loop_last``.  The
  Tier-1 module ``_wrap`` already inlines the same helper; copying the
  five-line generator here avoids a third file dedicated to two
  generators.

* ``from ._pick import pick_bool`` -- inlined as ``_pick_bool`` for the
  same reason.  One inline use-site in ``__rich_console__``.

* ``from .containers import Lines`` -- ``rich.containers`` is not
  ported.  A pared-down ``Lines`` class is defined in this module
  (the same surface ``Text.wrap`` / ``Text.split`` / ``Text.divide``
  produces and that downstream tier-5 code -- ``console.render_lines``
  / Static rendering -- iterates).  Includes the ``justify`` helper
  inlined from ``containers.py`` because ``Text.wrap`` calls it.  The
  rest of ``containers.Renderables`` is out of scope.

* ``from .control import strip_control_codes`` -- inlined as
  ``_strip_control_codes``.  Single-call surface (one ``str.translate``
  against five codepoints); not worth a separate ``control.py`` port.

* ``from .emoji import EmojiVariant`` / ``from_markup(..., emoji=...)``
  emoji rendering -- DROPPED.  Synthesis doc 00 R2 lists emoji
  shortcode substitution as out-of-scope for v0.1.  The ``emoji`` and
  ``emoji_variant`` kwargs on ``from_markup`` are still accepted (for
  API parity with upstream Rich) but ignored, matching the
  ``markup.render`` shim in this same port pack.

* ``from .ansi import AnsiDecoder`` / ``from_ansi`` -- DROPPED.  Per
  the porting plan note (d): Textual emits only ``CSI m`` SGR and
  direct-cursor-position sequences, never the rich CSI subsequences
  the upstream ``AnsiDecoder`` parses.  ``from_ansi`` raises
  ``NotImplementedError``; if a v0.1 widget ever needs ANSI input
  parsing, port a narrow ``CSI m`` SGR decoder at that time.

* ``from .jupyter import JupyterMixin`` -- DROPPED.  No Jupyter HTML
  pathway in v0.1 (the runtime is a TTY).  ``Text`` inherits from
  ``object`` directly.  This also drops ``__html__`` and the SVG
  rendering surface.

* ``with_indent_guides()`` -- DROPPED per synthesis doc 00; pulls in
  ``re.MULTILINE`` (banned by NFR-TUI-10) and only used by Rich's code
  pretty-printer, which is out of v0.1 scope.  ``detect_indentation``
  is also dropped because its only consumer is ``with_indent_guides``
  and it likewise needs ``re.MULTILINE``.

* ``write_console_html()`` -- DROPPED (Jupyter / HTML output not in
  v0.1).  ``Text`` has no such method upstream anyway -- the comment
  is for the audit trail of features Console-level code might expect.

* ``__slots__`` -- DROPPED.  MicroPython's ``__slots__`` support is
  partial (works for inheritance from ``object`` but interacts oddly
  with the typing-shim ``Generic`` stand-in and certain Phase 5
  reactive descriptors).  The frozen-bytes savings are small for the
  Text class because ``_text`` and ``_spans`` are always populated.

* ``re.MULTILINE`` keyword flag and named groups -- BANNED by
  NFR-TUI-10.  ``detect_indentation`` is therefore removed; ``split``
  switches to a manual scanner instead of ``re.finditer(escape(sep))``
  for separator offsets (escape() is still safe; finditer is
  re-implemented via repeated ``str.find`` since MicroPython ``re``
  exposes ``re.compile().search()`` but not ``finditer``).

* ``highlight_regex`` -- preserves the call signature but only the
  whole-match (``style=...``) path works.  The named-group dispatch
  upstream uses (``match.groupdict()`` keyed by ``(?P<name>...)``) is
  unreachable on MicroPython ``re``.  Calls that supply a regex with
  named groups raise ``NotImplementedError`` (the named-group syntax
  itself is a re1.5 parse error, so the failure surfaces at
  ``re.compile`` time before reaching the loop).

* ``highlight_words`` ``case_sensitive=False`` -- upstream uses
  ``re.IGNORECASE``.  MicroPython re does not support flags as a
  ``re.compile`` kwarg; we lowercase both sides instead.  Equivalent
  for ASCII; Latin-1 case folding (e.g. ``ß``) is not exact but
  Textual's only call site (the legend / footer text) is ASCII.

* ``__init_subclass__`` / metaclass hooks -- upstream has none for
  Text; nothing to drop.  Listed here for completeness against the
  port rule 4 checklist.

* ``Span.split`` / ``Span.move`` / ``Span.right_crop`` / ``Span.extend``
  -- KEPT.  All four are called by ``Text`` internals or by Textual
  widgets that compose Text instances cell-by-cell.

NOT REMOVED (intentional)
-------------------------
* ``Text.assemble``, ``Text.from_markup``, ``Text.styled``, the
  ``append`` / ``append_text`` / ``append_tokens`` family, ``stylize``,
  ``stylize_before``, ``apply_meta``, ``on``, ``copy_styles``,
  ``copy``, ``blank_copy``, ``__eq__``, ``__contains__``, ``__add__``,
  ``__getitem__``, ``markup`` property, ``cell_len`` property,
  ``plain`` property + setter, ``spans`` property + setter,
  ``expand_tabs``, ``truncate``, ``pad`` / ``pad_left`` / ``pad_right``,
  ``align``, ``set_length``, ``rstrip`` / ``rstrip_end``,
  ``remove_suffix``, ``right_crop``, ``divide``, ``split``, ``join``,
  ``wrap``, ``fit``, ``render``, ``extend_style``, ``highlight_words``.
  Each is reachable from at least one tier-4/tier-5 caller listed in
  research doc 02 §"Textual's Actual Rich Usage" or from the Phase 4
  widget set (FR-TUI-41 Static, FR-TUI-42 Label).

* ``__rich_console__`` -- kept as the production yield-Segments path
  for the Static/Label render flow.  The port-plan note (g) about
  keeping render "as a stub" was about the upstream
  ``spans_to_console_rendering`` private helper, which is already
  rolled into ``render()`` here -- both methods produce Segments,
  ``render()`` for the no-wrap fast path and ``__rich_console__`` for
  the wrapped path.  Neither requires ``console.render_lines``.

* ``get_style_at_offset`` -- kept because ``Lines.justify("full")``
  calls it to colour-match inserted padding spaces.

Spec hooks
----------
Supports:
  FR-TUI-41 (Static) -- every Static.render returns a Text or a string;
    the Console pipeline normalises to Text and yields Segments via
    this module's ``__rich_console__``.
  FR-TUI-42 (Label) -- ``Label`` truncates with ``Text.truncate``
    (overflow="ellipsis") when the layout width is shorter than the
    label.  The "..." path is in ``truncate()`` below.
  FR-TUI-32 (Style DSL) -- ``Text.stylize`` / ``Text.from_markup``
    accept ``Union[str, Style]`` and dispatch to ``Style.parse``
    indirectly via the ``console.get_style`` call in ``render()``.
  NFR-TUI-6 (lru_cache budget) -- this module declares no caches.
    Upstream Text has no ``@lru_cache`` either; the cache-clamp rule
    is a no-op here.
  NFR-TUI-10 (re1.5 compat) -- regexes used:
    * ``\\s+$``               -- end-of-string whitespace, plain.
    * ``re.escape(separator)`` -- escape() is pure ASCII; safe.
    No named groups, no inline flags, no lookaround.  ``re.IGNORECASE``
    upstream is replaced with hand-folded lowercasing.
  NFR-TUI-19 (frozen budget) -- aim ~1000-1200 LoC.  Class surface
    matches upstream so Phase 4 widget code ports verbatim.
"""

import re
from operator import itemgetter

from picolet_tui._shims.functools import partial
from picolet_tui._shims.typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Union,
)

from ._wrap import divide_line
from .cells import cell_len, set_cell_size
from .measure import Measurement
from .segment import Segment
from .style import Style


# ---------------------------------------------------------------------------
# Module constants and helpers.
# ---------------------------------------------------------------------------

DEFAULT_JUSTIFY = "default"
DEFAULT_OVERFLOW = "fold"

# Used by ``rstrip_end`` to locate the trailing whitespace run.  Plain
# ASCII-class regex; re1.5 supports ``\s`` and ``+`` and ``$`` with no
# flags (verified against MICROPY_PY_RE_MATCH_GROUPS=1 builds).
_re_whitespace = re.compile(r"\s+$")

TextType = Union[str, "Text"]
"""A plain string or a :class:`Text` instance."""

GetStyleCallable = Callable

# Five control codepoints upstream Rich strips on every Text construction.
# Inlined from ``rich.control.STRIP_CONTROL_CODES`` so we don't pull the
# whole ``control`` module in just for one ``str.translate`` table.
# MicroPython's ``str.translate`` accepts a {int: None} dict directly.
_CONTROL_STRIP_TRANSLATE = {
    7: None,   # BEL
    8: None,   # BS
    11: None,  # VT
    12: None,  # FF
    13: None,  # CR
}


def _strip_control_codes(text):
    """Remove the five visible control codes Rich filters from input.

    Mirrors ``rich.control.strip_control_codes`` exactly; pulled in as
    a private helper so this module has no dep on ``control.py`` (which
    is itself not yet ported -- the rest of its surface is ANSI escape
    construction, which Textual handles via tuiterm in picolet).
    """
    return text.translate(_CONTROL_STRIP_TRANSLATE)


def _loop_last(values):
    """Yield (is_last, value) for each item.

    Inlined from ``rich._loop.loop_last``; see ``_wrap.py`` for the
    matching copy and rationale.  Generator semantics match upstream:
    empty iterables yield nothing.
    """
    iter_values = iter(values)
    try:
        previous_value = next(iter_values)
    except StopIteration:
        return
    for value in iter_values:
        yield False, previous_value
        previous_value = value
    yield True, previous_value


def _pick_bool(*values):
    """First non-None bool, else the last value.

    Inlined from ``rich._pick.pick_bool``.  Used once in
    ``__rich_console__`` to merge instance / option no_wrap settings.
    """
    assert values, "1 or more values required"
    value = None
    for value in values:
        if value is not None:
            return value
    return bool(value)


def _zip_longest(a, b):
    """Tiny ``itertools.zip_longest`` for two iterables, fillvalue=None.

    Used by ``Lines.justify`` ("full" mode); MicroPython's
    ``itertools`` lacks ``zip_longest``.  Two-sequence specialisation
    keeps the helper to four lines.
    """
    la = list(a)
    lb = list(b)
    n = max(len(la), len(lb))
    for i in range(n):
        yield (la[i] if i < len(la) else None,
               lb[i] if i < len(lb) else None)


# ---------------------------------------------------------------------------
# Span -- a (start, end, style) tuple subclass.
#
# Upstream uses ``typing.NamedTuple``.  The picolet typing shim does not
# export NamedTuple (see _shims/typing.py rationale), so we follow the
# same tuple-subclass pattern Tag / Measurement use.  Visible surface
# matches the NamedTuple contract: positional construction, indexed
# access, named-attribute access, tuple iteration, ``isinstance(s,
# tuple)`` -> True.  ``_replace`` / ``_asdict`` are unused by any
# caller and omitted.
# ---------------------------------------------------------------------------


class Span(tuple):
    """A marked-up region in some text: ``(start, end, style)``."""

    def __new__(cls, start, end, style):
        return tuple.__new__(cls, (start, end, style))

    @property
    def start(self):
        return self[0]

    @property
    def end(self):
        return self[1]

    @property
    def style(self):
        return self[2]

    def __repr__(self):
        return "Span(" + str(self[0]) + ", " + str(self[1]) + ", " + repr(self[2]) + ")"

    def __bool__(self):
        return self[1] > self[0]

    def split(self, offset):
        """Split a span in to 2 from a given offset."""
        if offset < self[0]:
            return self, None
        if offset >= self[1]:
            return self, None
        start, end, style = self
        span1 = Span(start, min(end, offset), style)
        span2 = Span(span1.end, end, style)
        return span1, span2

    def move(self, offset):
        """Move start and end by a given offset."""
        start, end, style = self
        return Span(start + offset, end + offset, style)

    def right_crop(self, offset):
        """Crop the span at the given offset."""
        start, end, style = self
        if offset >= end:
            return self
        return Span(start, min(offset, end), style)

    def extend(self, cells):
        """Extend the span by the given number of cells."""
        if cells:
            start, end, style = self
            return Span(start, end + cells, style)
        return self


# ---------------------------------------------------------------------------
# Lines -- container of Text instances with a justify pass.
#
# Upstream lives in ``rich.containers``; inlined here because (a) the
# rest of containers.py (Renderables / pretty-print bits) is out of
# scope for v0.1, and (b) Text.wrap / Text.split / Text.divide all
# return Lines, so making Lines a sibling of Text in the same module
# avoids a one-class file.
# ---------------------------------------------------------------------------


class Lines:
    """A list-like container of Text instances."""

    def __init__(self, lines=()):
        self._lines = list(lines)

    def __repr__(self):
        return "Lines(" + repr(self._lines) + ")"

    def __iter__(self):
        return iter(self._lines)

    def __getitem__(self, index):
        return self._lines[index]

    def __setitem__(self, index, value):
        self._lines[index] = value
        return self

    def __len__(self):
        return len(self._lines)

    def __rich_console__(self, console, options):
        """Insert line-breaks between Text instances."""
        for line in self._lines:
            yield line

    def append(self, line):
        self._lines.append(line)

    def extend(self, lines):
        self._lines.extend(lines)

    def pop(self, index=-1):
        return self._lines.pop(index)

    def justify(self, console, width, justify="left", overflow="fold"):
        """Justify and overflow text to a given width."""
        if justify == "left":
            for line in self._lines:
                line.truncate(width, overflow=overflow, pad=True)
        elif justify == "center":
            for line in self._lines:
                line.rstrip()
                line.truncate(width, overflow=overflow)
                line.pad_left((width - cell_len(line.plain)) // 2)
                line.pad_right(width - cell_len(line.plain))
        elif justify == "right":
            for line in self._lines:
                line.rstrip()
                line.truncate(width, overflow=overflow)
                line.pad_left(width - cell_len(line.plain))
        elif justify == "full":
            for line_index, line in enumerate(self._lines):
                if line_index == len(self._lines) - 1:
                    break
                words = line.split(" ")
                words_size = sum(cell_len(word.plain) for word in words)
                num_spaces = len(words) - 1
                spaces = [1 for _ in range(num_spaces)]
                index = 0
                if spaces:
                    while words_size + num_spaces < width:
                        spaces[len(spaces) - index - 1] += 1
                        num_spaces += 1
                        index = (index + 1) % len(spaces)
                tokens = []
                # ``zip_longest`` not in MicroPython itertools -- inline.
                for index, (word, next_word) in enumerate(
                    _zip_longest(words, words[1:])
                ):
                    tokens.append(word)
                    if index < len(spaces):
                        style = word.get_style_at_offset(console, -1)
                        next_style = next_word.get_style_at_offset(console, 0)
                        space_style = style if style == next_style else line.style
                        tokens.append(Text(" " * spaces[index], style=space_style))
                self[line_index] = Text("").join(tokens)


# ---------------------------------------------------------------------------
# Text -- the styled-string class.
# ---------------------------------------------------------------------------


class Text:
    """Text with color / style.

    Args:
        text: Default unstyled text.  Defaults to "".
        style: Base style for text (``Union[str, Style]``).  Defaults to "".
        justify: "left" | "center" | "full" | "right" | None.
        overflow: "crop" | "fold" | "ellipsis" | "ignore" | None.
        no_wrap: Disable text wrapping, or None for default.
        end: Character to end text with.  Defaults to ``"\\n"``.
        tab_size: Number of spaces per tab, or None for console.tab_size.
        spans: A list of predefined style spans.  Defaults to None.
    """

    def __init__(
        self,
        text="",
        style="",
        *,
        justify=None,
        overflow=None,
        no_wrap=None,
        end="\n",
        tab_size=None,
        spans=None,
    ):
        sanitized_text = _strip_control_codes(text)
        self._text = [sanitized_text]
        self.style = style
        self.justify = justify
        self.overflow = overflow
        self.no_wrap = no_wrap
        self.end = end
        self.tab_size = tab_size
        self._spans = list(spans) if spans else []
        self._length = len(sanitized_text)

    # -- Dunders -----------------------------------------------------------

    def __len__(self):
        return self._length

    def __bool__(self):
        return bool(self._length)

    def __str__(self):
        return self.plain

    def __repr__(self):
        return "<text " + repr(self.plain) + " " + repr(self._spans) + " " + repr(self.style) + ">"

    def __add__(self, other):
        if isinstance(other, (str, Text)):
            result = self.copy()
            result.append(other)
            return result
        return NotImplemented

    def __eq__(self, other):
        if not isinstance(other, Text):
            return NotImplemented
        return self.plain == other.plain and self._spans == other._spans

    def __ne__(self, other):
        # MicroPython does not auto-derive __ne__ from __eq__ for all
        # paths -- be explicit so ``text != other_text`` works
        # symmetrically against str.
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        # Match upstream (Text is unhashable by virtue of mutable spans /
        # NamedTuple-free shape).  ``None`` poisons hash to mirror upstream.
        return None

    def __contains__(self, other):
        if isinstance(other, str):
            return other in self.plain
        if isinstance(other, Text):
            return other.plain in self.plain
        return False

    def __getitem__(self, key):
        """Index or slice into the text.

        Slicing with step != 1 is unsupported (upstream raises TypeError);
        we preserve that.  ``slice`` is the builtin -- not a parameter
        called ``slice`` -- so we can still use the name in upstream form.
        """
        def get_text_at(offset):
            return Text(
                self.plain[offset],
                spans=[
                    Span(0, 1, span_style)
                    for start, end, span_style in self._spans
                    if end > offset >= start
                ],
                end="",
            )

        if isinstance(key, int):
            return get_text_at(key)
        # Slice path.
        start, stop, step = key.indices(len(self.plain))
        if step == 1:
            lines = self.divide([start, stop])
            return lines[1]
        raise TypeError("slices with step!=1 are not supported")

    # -- Properties --------------------------------------------------------

    @property
    def cell_len(self):
        """Get the number of cells required to render this text."""
        return cell_len(self.plain)

    @property
    def markup(self):
        """Get console markup to render this Text."""
        # Local import: markup imports text lazily; pulling markup at
        # module top would create a cycle.
        from .markup import escape

        output = []
        plain = self.plain
        markup_spans = [
            (0, False, self.style),
        ]
        for span in self._spans:
            markup_spans.append((span.start, False, span.style))
        for span in self._spans:
            markup_spans.append((span.end, True, span.style))
        markup_spans.append((len(plain), True, self.style))
        markup_spans.sort(key=itemgetter(0, 1))

        position = 0
        append = output.append
        for offset, closing, style in markup_spans:
            if offset > position:
                append(escape(plain[position:offset]))
                position = offset
            if style:
                if closing:
                    append("[/" + str(style) + "]")
                else:
                    append("[" + str(style) + "]")
        return "".join(output)

    @property
    def plain(self):
        """Get the text as a single string."""
        if len(self._text) != 1:
            self._text[:] = ["".join(self._text)]
        return self._text[0]

    @plain.setter
    def plain(self, new_text):
        """Set the text to a new value."""
        if new_text != self.plain:
            sanitized_text = _strip_control_codes(new_text)
            self._text[:] = [sanitized_text]
            old_length = self._length
            self._length = len(sanitized_text)
            if old_length > self._length:
                self._trim_spans()

    @property
    def spans(self):
        """Get a reference to the internal list of spans."""
        return self._spans

    @spans.setter
    def spans(self, spans):
        """Set spans (copies the input list)."""
        self._spans = list(spans)

    # -- Classmethods ------------------------------------------------------

    @classmethod
    def from_markup(
        cls,
        text,
        *,
        style="",
        emoji=True,
        emoji_variant=None,
        justify=None,
        overflow=None,
        end="\n",
    ):
        """Create Text instance from markup.

        ``emoji`` / ``emoji_variant`` accepted for API parity but
        ignored (emoji shortcode substitution is out of v0.1 scope --
        synthesis doc 00 R2).
        """
        # Local import: avoids the markup -> text -> markup cycle.
        from .markup import render

        rendered_text = render(text, style, emoji=emoji, emoji_variant=emoji_variant)
        rendered_text.justify = justify
        rendered_text.overflow = overflow
        rendered_text.end = end
        return rendered_text

    @classmethod
    def from_ansi(
        cls,
        text,
        *,
        style="",
        justify=None,
        overflow=None,
        no_wrap=None,
        end="\n",
        tab_size=8,
    ):
        """Create a Text object from a string containing ANSI escape codes.

        DROPPED for v0.1: the upstream ``AnsiDecoder`` handles the full
        CSI subsequence catalogue (cursor save/restore, OSC links, etc.)
        which neither Textual nor any v0.1 widget produces.  Raising
        ``NotImplementedError`` (rather than silently passing the text
        through) signals to the caller that the input would be mis-
        rendered.  See module docstring for the porting plan around a
        narrow CSI-m SGR decoder if it's ever needed.
        """
        raise NotImplementedError(
            "Text.from_ansi is not implemented in picolet_tui v0.1"
        )

    @classmethod
    def styled(
        cls,
        text,
        style="",
        *,
        justify=None,
        overflow=None,
    ):
        """Construct a Text instance with a pre-applied style.

        A style applied via this constructor is not used to pad the text
        when it is justified (matches upstream semantics).
        """
        styled_text = cls(text, justify=justify, overflow=overflow)
        styled_text.stylize(style)
        return styled_text

    @classmethod
    def assemble(
        cls,
        *parts,
        style="",
        justify=None,
        overflow=None,
        no_wrap=None,
        end="\n",
        tab_size=8,
        meta=None,
    ):
        """Combine strings / Texts / (str, style) tuples into a Text."""
        text = cls(
            style=style,
            justify=justify,
            overflow=overflow,
            no_wrap=no_wrap,
            end=end,
            tab_size=tab_size,
        )
        append = text.append
        for part in parts:
            if isinstance(part, (Text, str)):
                append(part)
            else:
                append(*part)
        if meta:
            text.apply_meta(meta)
        return text

    # -- Copy / blank ------------------------------------------------------

    def blank_copy(self, plain=""):
        """Return a new Text instance with copied metadata only."""
        return Text(
            plain,
            style=self.style,
            justify=self.justify,
            overflow=self.overflow,
            no_wrap=self.no_wrap,
            end=self.end,
            tab_size=self.tab_size,
        )

    def copy(self):
        """Return a copy of this instance."""
        copy_self = Text(
            self.plain,
            style=self.style,
            justify=self.justify,
            overflow=self.overflow,
            no_wrap=self.no_wrap,
            end=self.end,
            tab_size=self.tab_size,
        )
        copy_self._spans[:] = self._spans
        return copy_self

    # -- Stylize / meta ----------------------------------------------------

    def stylize(self, style, start=0, end=None):
        """Apply a style to the text, or a portion of the text."""
        if not style:
            return
        length = len(self)
        if start < 0:
            start = length + start
        if end is None:
            end = length
        if end < 0:
            end = length + end
        if start >= length or end <= start:
            return
        self._spans.append(Span(start, min(length, end), style))

    def stylize_before(self, style, start=0, end=None):
        """Apply a style ahead of any other style already present."""
        if not style:
            return
        length = len(self)
        if start < 0:
            start = length + start
        if end is None:
            end = length
        if end < 0:
            end = length + end
        if start >= length or end <= start:
            return
        self._spans.insert(0, Span(start, min(length, end), style))

    def apply_meta(self, meta, start=0, end=None):
        """Apply metadata to the text, or a portion of the text."""
        style = Style.from_meta(meta)
        self.stylize(style, start=start, end=end)

    def on(self, meta=None, **handlers):
        """Apply event handlers (used by Textual).  Returns self."""
        meta = {} if meta is None else meta
        for key, value in handlers.items():
            meta["@" + key] = value
        self.stylize(Style.from_meta(meta))
        return self

    # -- Misc string ops ---------------------------------------------------

    def remove_suffix(self, suffix):
        """Remove a suffix if it exists."""
        if self.plain.endswith(suffix):
            self.right_crop(len(suffix))

    def get_style_at_offset(self, console, offset):
        """Get the style of a character at the given offset."""
        if offset < 0:
            offset = len(self) + offset
        get_style = console.get_style
        style = get_style(self.style).copy()
        for start, end, span_style in self._spans:
            if end > offset >= start:
                style += get_style(span_style, default="")
        return style

    def extend_style(self, spaces):
        """Extend Text by spaces having the same style as the last char."""
        if spaces <= 0:
            return
        spans = self.spans
        new_spaces = " " * spaces
        if spans:
            end_offset = len(self)
            self._spans[:] = [
                span.extend(spaces) if span.end >= end_offset else span
                for span in spans
            ]
            self._text.append(new_spaces)
            self._length += spaces
        else:
            self.plain += new_spaces

    # -- Highlighting ------------------------------------------------------

    def highlight_regex(self, re_highlight, style=None, *, style_prefix=""):
        """Highlight text matching a regular expression.

        DEGRADED vs upstream: only the whole-match ``style=...`` path
        works.  The named-group dispatch (``(?P<name>...)`` -> apply
        style with that name) is unreachable on MicroPython ``re``
        because re1.5 rejects the named-group syntax at compile time
        (NFR-TUI-10).  If a caller compiles the pattern themselves with
        named groups, ``re.compile`` raises before we get here; if they
        pass the pattern as a string and it contains ``(?P<``, the
        same ``re.compile`` below raises.

        Args:
            re_highlight: A compiled regex or pattern string.  Named
                groups are forbidden.
            style: Optional style for whole match, or a callable that
                receives the matched text and returns a style.
            style_prefix: Currently unused (kept for signature parity).
        """
        count = 0
        append_span = self._spans.append
        plain = self.plain

        if isinstance(re_highlight, str):
            # re.compile() raises here on named groups under re1.5.
            re_highlight = re.compile(re_highlight)

        # MicroPython re does not expose ``Pattern.finditer``.  Walk
        # via repeated ``search(string, pos)``.  Guards zero-width
        # matches by advancing one position when needed.
        pos = 0
        plain_len = len(plain)
        while pos <= plain_len:
            match = re_highlight.search(plain, pos)
            if match is None:
                break
            start, end = match.span()
            if style is not None:
                match_style = style(plain[start:end]) if callable(style) else style
                if match_style is not None and end > start:
                    append_span(Span(start, end, match_style))
            count += 1
            # Advance past the match; protect against zero-width.
            pos = end if end > start else start + 1
        # Note: the named-group ``match.groupdict()`` loop upstream
        # walks here.  Intentionally omitted -- see docstring.
        del style_prefix  # silence unused-arg lint in this trimmed form
        return count

    def highlight_words(self, words, style, *, case_sensitive=True):
        """Highlight whole-string occurrences of ``words`` with a style.

        DEGRADED vs upstream: ``case_sensitive=False`` is implemented by
        lowercasing both sides instead of ``re.IGNORECASE`` (which
        MicroPython re does not accept as a flag).  Equivalent for
        ASCII; non-ASCII case folding diverges from CPython here.
        """
        if not words:
            return 0
        plain = self.plain
        search_plain = plain if case_sensitive else plain.lower()
        search_words = list(words) if case_sensitive else [w.lower() for w in words]

        count = 0
        for word in search_words:
            word_len = len(word)
            if word_len == 0:
                continue
            start = 0
            while True:
                idx = search_plain.find(word, start)
                if idx == -1:
                    break
                self._spans.append(Span(idx, idx + word_len, style))
                count += 1
                start = idx + word_len
        return count

    # -- Trim / pad / align ------------------------------------------------

    def rstrip(self):
        """Strip whitespace from end of text."""
        self.plain = self.plain.rstrip()

    def rstrip_end(self, size):
        """Remove whitespace beyond a width at the end of the text."""
        text_length = len(self)
        if text_length > size:
            excess = text_length - size
            whitespace_match = _re_whitespace.search(self.plain)
            if whitespace_match is not None:
                whitespace_count = len(whitespace_match.group(0))
                self.right_crop(min(whitespace_count, excess))

    def set_length(self, new_length):
        """Set new length of the text, clipping or padding as needed."""
        length = len(self)
        if length != new_length:
            if length < new_length:
                self.pad_right(new_length - length)
            else:
                self.right_crop(length - new_length)

    def truncate(self, max_width, *, overflow=None, pad=False):
        """Truncate text if longer than ``max_width`` cells."""
        _overflow = overflow or self.overflow or DEFAULT_OVERFLOW
        if _overflow != "ignore":
            length = cell_len(self.plain)
            if length > max_width:
                if _overflow == "ellipsis":
                    self.plain = set_cell_size(self.plain, max_width - 1) + "…"
                else:
                    self.plain = set_cell_size(self.plain, max_width)
            if pad and length < max_width:
                spaces = max_width - length
                self._text = [self.plain + (" " * spaces)]
                self._length = len(self.plain)

    def _trim_spans(self):
        """Remove or modify any spans that are over the end of the text."""
        max_offset = len(self.plain)
        new_spans = []
        for span in self._spans:
            if span.start >= max_offset:
                continue
            if span.end < max_offset:
                new_spans.append(span)
            else:
                new_spans.append(Span(span.start, min(max_offset, span.end), span.style))
        self._spans[:] = new_spans

    def pad(self, count, character=" "):
        """Pad left and right with a given number of characters."""
        assert len(character) == 1, "Character must be a string of length 1"
        if count:
            pad_characters = character * count
            self.plain = pad_characters + self.plain + pad_characters
            self._spans[:] = [
                Span(start + count, end + count, span_style)
                for start, end, span_style in self._spans
            ]

    def pad_left(self, count, character=" "):
        """Pad the left with a given character."""
        assert len(character) == 1, "Character must be a string of length 1"
        if count:
            self.plain = (character * count) + self.plain
            self._spans[:] = [
                Span(start + count, end + count, span_style)
                for start, end, span_style in self._spans
            ]

    def pad_right(self, count, character=" "):
        """Pad the right with a given character."""
        assert len(character) == 1, "Character must be a string of length 1"
        if count:
            self.plain = self.plain + (character * count)

    def align(self, align, width, character=" "):
        """Align text to a given width.

        ``align`` is one of "left", "center", "right".  Anything else
        falls into the right-align branch (matches upstream).
        """
        self.truncate(width)
        excess_space = width - cell_len(self.plain)
        if excess_space:
            if align == "left":
                self.pad_right(excess_space, character)
            elif align == "center":
                left = excess_space // 2
                self.pad_left(left, character)
                self.pad_right(excess_space - left, character)
            else:
                self.pad_left(excess_space, character)

    # -- Append family -----------------------------------------------------

    def append(self, text, style=None):
        """Add text with an optional style.  Returns self."""
        if not isinstance(text, (str, Text)):
            raise TypeError("Only str or Text can be appended to Text")

        if len(text):
            if isinstance(text, str):
                sanitized_text = _strip_control_codes(text)
                self._text.append(sanitized_text)
                offset = len(self)
                text_length = len(sanitized_text)
                if style:
                    self._spans.append(Span(offset, offset + text_length, style))
                self._length += text_length
            else:  # Text instance
                if style is not None:
                    raise ValueError(
                        "style must not be set when appending Text instance"
                    )
                text_length = self._length
                if text.style:
                    self._spans.append(
                        Span(text_length, text_length + len(text), text.style)
                    )
                self._text.append(text.plain)
                # Iterate the copy so the source list can mutate if needed.
                for start, end, span_style in list(text._spans):
                    self._spans.append(
                        Span(start + text_length, end + text_length, span_style)
                    )
                self._length += len(text)
        return self

    def append_text(self, text):
        """Append another Text instance.  More performant than ``append``."""
        text_length = self._length
        if text.style:
            self._spans.append(Span(text_length, text_length + len(text), text.style))
        self._text.append(text.plain)
        for start, end, span_style in list(text._spans):
            self._spans.append(
                Span(start + text_length, end + text_length, span_style)
            )
        self._length += len(text)
        return self

    def append_tokens(self, tokens):
        """Append iterable of ``(content, style)`` tuples.  Returns self."""
        append_text = self._text.append
        append_span = self._spans.append
        offset = len(self)
        for content, style in tokens:
            content = _strip_control_codes(content)
            append_text(content)
            if style:
                append_span(Span(offset, offset + len(content), style))
            offset += len(content)
        self._length = offset
        return self

    def copy_styles(self, text):
        """Copy spans (only) from another Text instance."""
        self._spans.extend(text._spans)

    # -- Split / divide / join ---------------------------------------------

    def split(self, separator="\n", *, include_separator=False, allow_blank=False):
        """Split rich text into lines, preserving styles.

        Replaces upstream's ``re.finditer(re.escape(separator), text)``
        with a hand scanner using ``str.find`` -- MicroPython re lacks
        ``finditer``.  Semantics match upstream exactly.
        """
        assert separator, "separator must not be empty"
        text = self.plain
        if separator not in text:
            return Lines([self.copy()])

        sep_len = len(separator)
        if include_separator:
            offsets = []
            pos = 0
            text_len = len(text)
            while pos < text_len:
                idx = text.find(separator, pos)
                if idx == -1:
                    break
                offsets.append(idx + sep_len)
                pos = idx + sep_len
            lines = self.divide(offsets)
        else:
            def flatten_spans():
                pos = 0
                text_len = len(text)
                while pos < text_len:
                    idx = text.find(separator, pos)
                    if idx == -1:
                        break
                    yield idx
                    yield idx + sep_len
                    pos = idx + sep_len

            lines = Lines(
                line for line in self.divide(flatten_spans())
                if line.plain != separator
            )

        if not allow_blank and text.endswith(separator):
            lines.pop()
        return lines

    def divide(self, offsets):
        """Divide text into lines at the given offsets."""
        _offsets = list(offsets)

        if not _offsets:
            return Lines([self.copy()])

        text = self.plain
        text_length = len(text)
        divide_offsets = [0] + _offsets + [text_length]
        line_ranges = list(zip(divide_offsets, divide_offsets[1:]))

        style = self.style
        justify = self.justify
        overflow = self.overflow
        new_lines = Lines(
            Text(text[start:end], style=style, justify=justify, overflow=overflow)
            for start, end in line_ranges
        )
        if not self._spans:
            return new_lines

        _line_appends = [line._spans.append for line in new_lines._lines]
        line_count = len(line_ranges)

        for span_start, span_end, span_style in self._spans:
            # Bisect for the line containing span_start.  Upstream uses
            # an open-coded binary search; we keep it identical so the
            # benchmark profile carries over.
            lower_bound = 0
            upper_bound = line_count
            start_line_no = (lower_bound + upper_bound) // 2

            while True:
                line_start, line_end = line_ranges[start_line_no]
                if span_start < line_start:
                    upper_bound = start_line_no - 1
                elif span_start > line_end:
                    lower_bound = start_line_no + 1
                else:
                    break
                start_line_no = (lower_bound + upper_bound) // 2

            if span_end < line_end:
                end_line_no = start_line_no
            else:
                end_line_no = lower_bound = start_line_no
                upper_bound = line_count

                while True:
                    line_start, line_end = line_ranges[end_line_no]
                    if span_end < line_start:
                        upper_bound = end_line_no - 1
                    elif span_end > line_end:
                        lower_bound = end_line_no + 1
                    else:
                        break
                    end_line_no = (lower_bound + upper_bound) // 2

            for line_no in range(start_line_no, end_line_no + 1):
                line_start, line_end = line_ranges[line_no]
                new_start = max(0, span_start - line_start)
                new_end = min(span_end - line_start, line_end - line_start)
                if new_end > new_start:
                    _line_appends[line_no](Span(new_start, new_end, span_style))

        return new_lines

    def right_crop(self, amount=1):
        """Remove ``amount`` characters from the end of the text."""
        max_offset = len(self.plain) - amount
        new_spans = []
        for span in self._spans:
            if span.start >= max_offset:
                continue
            if span.end < max_offset:
                new_spans.append(span)
            else:
                new_spans.append(Span(span.start, min(max_offset, span.end), span.style))
        self._spans[:] = new_spans
        self._text = [self.plain[:-amount]]
        self._length -= amount

    def join(self, lines):
        """Join Text instances together with ``self`` as the separator."""
        new_text = self.blank_copy()

        def iter_text():
            if self.plain:
                for last, line in _loop_last(lines):
                    yield line
                    if not last:
                        yield self
            else:
                for line in lines:
                    yield line

        extend_text = new_text._text.extend
        append_span = new_text._spans.append
        extend_spans = new_text._spans.extend
        offset = 0
        for text in iter_text():
            extend_text(text._text)
            if text.style:
                append_span(Span(offset, offset + len(text), text.style))
            extend_spans(
                Span(offset + start, offset + end, style)
                for start, end, style in text._spans
            )
            offset += len(text)
        new_text._length = offset
        return new_text

    # -- Tabs --------------------------------------------------------------

    def expand_tabs(self, tab_size=None):
        """Convert tabs to spaces."""
        if "\t" not in self.plain:
            return
        if tab_size is None:
            tab_size = self.tab_size
        if tab_size is None:
            tab_size = 8

        new_text = []
        append = new_text.append

        for line in self.split("\n", include_separator=True):
            if "\t" not in line.plain:
                append(line)
            else:
                cell_position = 0
                parts = line.split("\t", include_separator=True)
                for part in parts:
                    if part.plain.endswith("\t"):
                        part._text[-1] = part._text[-1][:-1] + " "
                        cell_position += part.cell_len
                        tab_remainder = cell_position % tab_size
                        if tab_remainder:
                            spaces = tab_size - tab_remainder
                            part.extend_style(spaces)
                            cell_position += spaces
                    else:
                        cell_position += part.cell_len
                    append(part)

        result = Text("").join(new_text)
        self._text = [result.plain]
        self._length = len(self.plain)
        self._spans[:] = result._spans

    # -- Render ------------------------------------------------------------

    def __rich_console__(self, console, options):
        """Yield Segments for the wrapped + justified text."""
        tab_size = console.tab_size if self.tab_size is None else self.tab_size
        justify = self.justify or options.justify or DEFAULT_JUSTIFY
        overflow = self.overflow or options.overflow or DEFAULT_OVERFLOW

        lines = self.wrap(
            console,
            options.max_width,
            justify=justify,
            overflow=overflow,
            tab_size=tab_size or 8,
            no_wrap=_pick_bool(self.no_wrap, options.no_wrap, False),
        )
        all_lines = Text("\n").join(lines)
        for segment in all_lines.render(console, end=self.end):
            yield segment

    def __rich_measure__(self, console, options):
        text = self.plain
        lines = text.splitlines()
        max_text_width = max(cell_len(line) for line in lines) if lines else 0
        words = text.split()
        min_text_width = (
            max(cell_len(word) for word in words) if words else max_text_width
        )
        return Measurement(min_text_width, max_text_width)

    def render(self, console, end=""):
        """Render the text as Segments.

        Fast path for spanless text yields a single Segment.  Otherwise
        builds a sorted (offset, leaving, style_id) event list and walks
        it, maintaining a tiny style stack -- this is upstream's
        ``spans_to_console_rendering`` rolled into the method body.
        """
        text = self.plain
        if not self._spans:
            yield Segment(text)
            if end:
                yield Segment(end)
            return

        get_style = partial(console.get_style, default=Style.null())

        enumerated_spans = list(enumerate(self._spans, 1))
        style_map = {index: get_style(span.style) for index, span in enumerated_spans}
        style_map[0] = get_style(self.style)

        spans = [(0, False, 0)]
        for index, span in enumerated_spans:
            spans.append((span.start, False, index))
        for index, span in enumerated_spans:
            spans.append((span.end, True, index))
        spans.append((len(text), True, 0))
        spans.sort(key=itemgetter(0, 1))

        stack = []
        style_cache = {}
        combine = Style.combine

        def get_current_style():
            styles = tuple(style_map[_style_id] for _style_id in sorted(stack))
            cached_style = style_cache.get(styles)
            if cached_style is not None:
                return cached_style
            current_style = combine(styles)
            style_cache[styles] = current_style
            return current_style

        for (offset, leaving, style_id), (next_offset, _, _) in zip(spans, spans[1:]):
            if leaving:
                stack.remove(style_id)
            else:
                stack.append(style_id)
            if next_offset > offset:
                yield Segment(text[offset:next_offset], get_current_style())
        if end:
            yield Segment(end)

    # -- Wrap / fit --------------------------------------------------------

    def wrap(self, console, width, *, justify=None, overflow=None,
             tab_size=8, no_wrap=None):
        """Word wrap the text."""
        wrap_justify = justify or self.justify or DEFAULT_JUSTIFY
        wrap_overflow = overflow or self.overflow or DEFAULT_OVERFLOW

        no_wrap = _pick_bool(no_wrap, self.no_wrap, False) or overflow == "ignore"

        lines = Lines()
        for line in self.split(allow_blank=True):
            if "\t" in line.plain:
                line.expand_tabs(tab_size)
            if no_wrap:
                if overflow == "ignore":
                    lines.append(line)
                    continue
                new_lines = Lines([line])
            else:
                offsets = divide_line(str(line), width, fold=wrap_overflow == "fold")
                new_lines = line.divide(offsets)
                for sub_line in new_lines:
                    sub_line.rstrip_end(width)
            if wrap_justify:
                new_lines.justify(
                    console, width, justify=wrap_justify, overflow=wrap_overflow
                )
            for sub_line in new_lines:
                sub_line.truncate(width, overflow=wrap_overflow)
            lines.extend(new_lines)
        return lines

    def fit(self, width):
        """Fit the text into ``width`` by chopping into lines."""
        lines = Lines()
        append = lines.append
        for line in self.split():
            line.set_length(width)
            append(line)
        return lines


# Re-export hint for the markup module's lazy import (it does
# ``_text.Text`` and ``_text.Span``).  No __all__ -- consumers import
# the names directly.
