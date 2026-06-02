"""picolet_tui._rich.segment - styled output fragment for the compositor.

Upstream Rich SHA: master @ Textualize/rich/rich/segment.py
  (fetched for Phase 3b; matches the head of master at port time).

Spec mapping
------------
Supports FR-TUI-32..37 (Style DSL emit), FR-TUI-30..31 (compositor),
and the test surface FR-TUI-65 (Rich test corpus must run against this
port).  Lives under NFR-TUI-19's `_rich/` 60 KiB romfs sub-budget; the
LRU cache cap in `_split_cells` respects NFR-TUI-6 (maxsize <= 128).

REMOVED vs upstream
-------------------
* ``NamedTuple`` subclassing.  MicroPython's ``collections.namedtuple``
  exists, but subclassing it with methods and ``@classmethod``
  factories does not work reliably.  Segment is reimplemented as a
  plain class with ``__slots__ = ('text', 'style', 'control')`` plus
  tuple-shaped iteration/indexing/equality.
* ``rich.repr.rich_repr`` decorator and ``__rich_repr__`` method.  The
  decorator pulls in ``inspect.signature`` which is out of scope for
  the runtime (callback.count_parameters covers the one site that
  actually needs arity).  The module still ships a plain ``__repr__``.
* ``@lru_cache(1024 * 16)`` on ``_split_cells`` is clamped to
  ``maxsize=128`` per NFR-TUI-6.
* ``operator.attrgetter`` / ``itertools.filterfalse`` imports replaced
  with inline ``lambda``s / hand-written loops; the trimmed runtime
  does not pay for those helpers.
* ``Style`` and the ``Console`` / ``ConsoleOptions`` / ``RenderResult``
  type hints are deferred (TYPE_CHECKING-only import or shim alias) so
  this module can land before tier-3 ``style.py``.

WHY a plain class with slots, not collections.namedtuple
--------------------------------------------------------
Upstream defines per-field annotations on a ``NamedTuple`` subclass and
then attaches methods.  MicroPython's ``namedtuple`` is a thin factory
and does not support method bodies in the class body.  Splitting the
factory call out and patching methods onto the returned class is
fragile (the read-only tuple slots break ``__hash__`` overrides).  A
plain class with ``__slots__`` and ``__iter__`` / ``__getitem__`` gives
the same destructuring shape (``text, style, control = segment``) with
less ceremony and works under both ports.
"""

from typing import TYPE_CHECKING, Any
from functools import lru_cache

from .cells import (
    _is_single_cell_widths,
    cached_cell_len,
    cell_len,
    get_character_cell_size,
    set_cell_size,
)
# IntEnum + the enum_class decorator are required because MicroPython
# does not honour the implicit ``class Foo(IntEnum): RED = 1`` capture
# (research doc 03 §"Per-module table" row "enum").  ControlType must
# carry @enum_class to populate __members__.
from picolet_tui._shims.enum import IntEnum, enum_class


if TYPE_CHECKING:
    # Tier-3 ordering: `style.py` is a sibling and may not be ported
    # yet when this module is frozen.  Hide the import behind
    # TYPE_CHECKING (False at runtime, see _shims/typing.py); the
    # runtime references are typed as Any so the binding still resolves.
    from .style import Style  # noqa: F401
    from .console import Console, ConsoleOptions, RenderResult  # noqa: F401
else:
    Style = Any  # runtime placeholder; segments accept any Style-shaped object


# ---------------------------------------------------------------------------
# ControlType - the IntEnum the compositor uses to tag non-printable
# segments.  Identical value table to upstream so a Rich-emitting
# downstream (e.g. parts of console.py) round-trips.
# ---------------------------------------------------------------------------


@enum_class
class ControlType(IntEnum):
    """Non-printable control codes which typically translate to ANSI codes."""

    BELL = 1
    CARRIAGE_RETURN = 2
    HOME = 3
    CLEAR = 4
    SHOW_CURSOR = 5
    HIDE_CURSOR = 6
    ENABLE_ALT_SCREEN = 7
    DISABLE_ALT_SCREEN = 8
    CURSOR_UP = 9
    CURSOR_DOWN = 10
    CURSOR_FORWARD = 11
    CURSOR_BACKWARD = 12
    CURSOR_MOVE_TO_COLUMN = 13
    CURSOR_MOVE_TO = 14
    ERASE_IN_LINE = 15
    SET_WINDOW_TITLE = 16


# ---------------------------------------------------------------------------
# Segment - the (text, style, control) tuple-shaped record produced by
# the renderer.  Implemented as a slotted class with tuple-style
# iteration/equality; see file docstring for rationale.
# ---------------------------------------------------------------------------


class Segment:
    """A piece of text with associated style.

    A Segment is one styled (or control-coded) span of console output.
    Most consumers destructure it as a 3-tuple::

        text, style, control = segment

    Equality and hashing are tuple-shaped to match upstream NamedTuple
    semantics, so segments can serve as dict keys and lru_cache keys.
    """

    # __slots__ pins the layout so segment construction stays cheap and
    # the frozen-bytes cost is bounded; matches the NamedTuple shape
    # users expect from upstream.
    __slots__ = ("text", "style", "control")

    def __init__(self, text, style=None, control=None):
        self.text = text
        self.style = style
        self.control = control

    # Tuple-shape protocol -------------------------------------------------
    #
    # Upstream is a NamedTuple, so callers freely write
    # ``text, style, control = segment`` and ``segment[0]``.  Implement
    # the two dunders that drive both forms.
    def __iter__(self):
        yield self.text
        yield self.style
        yield self.control

    def __getitem__(self, index):
        # Tuple-style integer indexing only; slicing is not part of the
        # NamedTuple-mimicked surface and would force materialising a
        # transient tuple per call.
        if index == 0:
            return self.text
        if index == 1:
            return self.style
        if index == 2:
            return self.control
        raise IndexError("Segment index out of range")

    def __len__(self):
        return 3

    def __eq__(self, other):
        if isinstance(other, Segment):
            return (self.text == other.text
                    and self.style == other.style
                    and self.control == other.control)
        if isinstance(other, tuple) and len(other) == 3:
            return (self.text, self.style, self.control) == other
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        # Hash on the same tuple shape as equality so equal segments
        # hash equal - required for lru_cache keying in _split_cells.
        return hash((self.text, self.style, self.control))

    def __repr__(self):
        if self.control is None:
            if self.style is None:
                return "Segment({!r})".format(self.text)
            return "Segment({!r}, {!r})".format(self.text, self.style)
        return "Segment({!r}, {!r}, {!r})".format(
            self.text, self.style, self.control
        )

    def __bool__(self):
        """Check if the segment contains text."""
        return bool(self.text)

    # Public API -----------------------------------------------------------

    @property
    def cell_length(self):
        """The number of terminal cells required to display ``self.text``.

        Control segments have zero cell length: they encode side-effect
        ANSI bytes, not visible glyphs.
        """
        return 0 if self.control else cell_len(self.text)

    @property
    def is_control(self):
        """True if this segment carries control codes rather than text."""
        return self.control is not None

    # ---- _split_cells: the hot path -------------------------------------
    #
    # The compositor calls ``segment.split_cells(cut)`` once per strip
    # boundary during paint.  Caching is critical: a stable widget that
    # re-renders the same Segment at the same cut position pays one
    # cell-width walk on first hit and zero thereafter.
    #
    # Upstream caches at 1024 * 16 entries; that would blow NFR-TUI-6's
    # maxsize <= 128 cap.  The clamp to 128 still covers the
    # frame-to-frame working set for the v0.1 widget surface (nine
    # widgets, ~30 visible strips per frame on a typical 80x24).
    @classmethod
    @lru_cache(maxsize=128)
    def _split_cells(cls, segment, cut):
        """Split a segment in to two at a given cell position.

        Splitting through a double-width character replaces it with two
        spaces so total cell width is preserved.
        """
        text, style, control = segment
        _Segment = Segment
        cell_length = segment.cell_length
        if cut >= cell_length:
            return segment, _Segment("", style, control)

        cell_size = get_character_cell_size

        # Seed pos by linear interpolation in *characters*, then walk by
        # one until the *cell* count matches.  Cheaper than a full
        # cell-width prefix scan because cuts cluster around the
        # interpolated guess in typical wide-character text.
        pos = int((cut / cell_length) * len(text))

        while True:
            before = text[:pos]
            cell_pos = cell_len(before)
            out_by = cell_pos - cut
            if not out_by:
                return (
                    _Segment(before, style, control),
                    _Segment(text[pos:], style, control),
                )
            if out_by == -1 and cell_size(text[pos]) == 2:
                # Cut would land inside a 2-cell char; replace that char
                # with two spaces (one in each half) to keep widths.
                return (
                    _Segment(text[:pos] + " ", style, control),
                    _Segment(" " + text[pos + 1:], style, control),
                )
            if out_by == +1 and cell_size(text[pos - 1]) == 2:
                return (
                    _Segment(text[: pos - 1] + " ", style, control),
                    _Segment(" " + text[pos:], style, control),
                )
            if cell_pos < cut:
                pos += 1
            else:
                pos -= 1

    def split_cells(self, cut):
        """Split this segment in two at column ``cut`` (cell-aware).

        Single-cell-only segments take a fast no-copy slice path; the
        wide-character fallback delegates to the cached ``_split_cells``.
        """
        text, style, control = self
        # Caller bugs are caught loudly here rather than yielding a
        # silently-empty leading segment.
        assert cut >= 0

        if _is_single_cell_widths(text):
            # Fast path: every char is 1 cell, so character index ==
            # cell index.  Skips the cache entirely.
            if cut >= len(text):
                return self, Segment("", style, control)
            return (
                Segment(text[:cut], style, control),
                Segment(text[cut:], style, control),
            )

        return self._split_cells(self, cut)

    @classmethod
    def line(cls):
        """Make a new line segment."""
        return cls("\n")

    # ---- Stream transforms (kept) --------------------------------------
    #
    # These are pure iterators over Segment streams; they avoid pulling
    # in Style or Console from later tiers.  ``apply_style`` is kept
    # because Textual's compositor uses it; ``strip_links`` is dropped
    # because it requires ``Style.update_link`` which is style-tier work.

    @classmethod
    def apply_style(cls, segments, style=None, post_style=None):
        """Apply (pre|post) style(s) to an iterable of segments.

        Returns an iterable of segments where the style is replaced by
        ``style + segment.style + post_style``, with control segments
        passed through untouched.  Style composition is delegated to
        the Style object via ``__add__``; this module never inspects
        Style internals.
        """
        result_segments = segments
        if style is not None:
            apply = style.__add__
            result_segments = (
                cls(text, None if control else apply(_style), control)
                for text, _style, control in result_segments
            )
        if post_style is not None:
            result_segments = (
                cls(
                    text,
                    (
                        None
                        if control
                        else (_style + post_style if _style else post_style)
                    ),
                    control,
                )
                for text, _style, control in result_segments
            )
        return result_segments

    @classmethod
    def filter_control(cls, segments, is_control=False):
        """Filter segments by their ``is_control`` flag.

        ``attrgetter('control')`` upstream pulls in ``operator``; the
        hand-written generator below saves the import.
        """
        if is_control:
            for s in segments:
                if s.control:
                    yield s
        else:
            for s in segments:
                if not s.control:
                    yield s

    @classmethod
    def split_lines(cls, segments):
        """Split a sequence of segments in to a list of lines.

        Yields one list-of-Segment per line; the trailing newline is
        consumed.  Control segments never participate in splitting.
        """
        line = []
        append = line.append

        for segment in segments:
            if "\n" in segment.text and not segment.control:
                text, style, _ = segment
                while text:
                    _text, new_line, text = text.partition("\n")
                    if _text:
                        append(cls(_text, style))
                    if new_line:
                        yield line
                        line = []
                        append = line.append
            else:
                append(segment)
        if line:
            yield line

    @classmethod
    def split_lines_terminator(cls, segments):
        """Like ``split_lines``, also yielding whether the line ended in '\\n'."""
        line = []
        append = line.append

        for segment in segments:
            if "\n" in segment.text and not segment.control:
                text, style, _ = segment
                while text:
                    _text, new_line, text = text.partition("\n")
                    if _text:
                        append(cls(_text, style))
                    if new_line:
                        yield (line, True)
                        line = []
                        append = line.append
            else:
                append(segment)
        if line:
            yield (line, False)

    @classmethod
    def split_and_crop_lines(
        cls, segments, length, style=None, pad=True, include_new_lines=True,
    ):
        """Split segments into lines and crop each to ``length`` cells.

        Lines shorter than ``length`` are space-padded when ``pad`` is
        True; control segments are emitted as-is and do not contribute
        to the visible width.
        """
        line = []
        append = line.append

        adjust_line_length = cls.adjust_line_length
        new_line_segment = cls("\n")

        for segment in segments:
            if "\n" in segment.text and not segment.control:
                text, segment_style, _ = segment
                while text:
                    _text, new_line, text = text.partition("\n")
                    if _text:
                        append(cls(_text, segment_style))
                    if new_line:
                        cropped_line = adjust_line_length(
                            line, length, style=style, pad=pad
                        )
                        if include_new_lines:
                            cropped_line.append(new_line_segment)
                        yield cropped_line
                        line = []
                        append = line.append
            else:
                append(segment)
        if line:
            yield adjust_line_length(line, length, style=style, pad=pad)

    @classmethod
    def adjust_line_length(cls, line, length, style=None, pad=True):
        """Resize a line to exactly ``length`` cells (crop or pad)."""
        line_length = sum(segment.cell_length for segment in line)

        if line_length < length:
            if pad:
                new_line = line + [cls(" " * (length - line_length), style)]
            else:
                new_line = line[:]
        elif line_length > length:
            new_line = []
            append = new_line.append
            line_length = 0
            for segment in line:
                segment_length = segment.cell_length
                if line_length + segment_length < length or segment.control:
                    append(segment)
                    line_length += segment_length
                else:
                    # Last segment needs cell-aware truncation; set_cell_size
                    # is the only entry point that handles wide chars at
                    # the boundary correctly.
                    text, segment_style, _ = segment
                    text = set_cell_size(text, length - line_length)
                    append(cls(text, segment_style))
                    break
        else:
            new_line = line[:]
        return new_line

    @classmethod
    def get_line_length(cls, line):
        """Sum the cell widths of every non-control segment in ``line``."""
        _cell_len = cell_len
        return sum(_cell_len(text) for text, style, control in line if not control)

    @classmethod
    def get_shape(cls, lines):
        """Return ``(width, height)`` for a list-of-lines of segments."""
        get_line_length = cls.get_line_length
        max_width = max(get_line_length(line) for line in lines) if lines else 0
        return (max_width, len(lines))

    @classmethod
    def set_shape(cls, lines, width, height=None, style=None, new_lines=False):
        """Resize a list-of-lines to the given enclosing rectangle."""
        _height = height or len(lines)

        blank = (
            [cls(" " * width + "\n", style)] if new_lines else [cls(" " * width, style)]
        )

        adjust_line_length = cls.adjust_line_length
        shaped_lines = lines[:_height]
        shaped_lines[:] = [
            adjust_line_length(line, width, style=style) for line in lines
        ]
        if len(shaped_lines) < _height:
            shaped_lines.extend([blank] * (_height - len(shaped_lines)))
        return shaped_lines

    @classmethod
    def align_top(cls, lines, width, height, style, new_lines=False):
        """Pad ``lines`` below to fill ``height`` rows."""
        extra_lines = height - len(lines)
        if not extra_lines:
            return lines[:]
        lines = lines[:height]
        blank = cls(" " * width + "\n", style) if new_lines else cls(" " * width, style)
        lines = lines + [[blank]] * extra_lines
        return lines

    @classmethod
    def align_bottom(cls, lines, width, height, style, new_lines=False):
        """Pad ``lines`` above to fill ``height`` rows."""
        extra_lines = height - len(lines)
        if not extra_lines:
            return lines[:]
        lines = lines[:height]
        blank = cls(" " * width + "\n", style) if new_lines else cls(" " * width, style)
        lines = [[blank]] * extra_lines + lines
        return lines

    @classmethod
    def align_middle(cls, lines, width, height, style, new_lines=False):
        """Pad ``lines`` above and below to centre within ``height`` rows."""
        extra_lines = height - len(lines)
        if not extra_lines:
            return lines[:]
        lines = lines[:height]
        blank = cls(" " * width + "\n", style) if new_lines else cls(" " * width, style)
        top_lines = extra_lines // 2
        bottom_lines = extra_lines - top_lines
        lines = [[blank]] * top_lines + lines + [[blank]] * bottom_lines
        return lines

    @classmethod
    def simplify(cls, segments):
        """Combine contiguous same-style segments.

        Run-length compaction over an iterable; reduces ANSI churn at
        emit time when a renderable emits many tiny same-styled pieces.
        Control segments break the run (they cannot be coalesced).
        """
        iter_segments = iter(segments)
        try:
            last_segment = next(iter_segments)
        except StopIteration:
            return

        _Segment = Segment
        for segment in iter_segments:
            if last_segment.style == segment.style and not segment.control:
                last_segment = _Segment(
                    last_segment.text + segment.text, last_segment.style
                )
            else:
                yield last_segment
                last_segment = segment
        yield last_segment

    @classmethod
    def strip_styles(cls, segments):
        """Yield segments with all styles cleared (text/control preserved)."""
        for text, _style, control in segments:
            yield cls(text, None, control)

    # NOTE: ``strip_links`` and ``remove_color`` are deliberately dropped
    # vs upstream.  Both reach into ``Style.update_link`` /
    # ``Style.without_color``, which are style-tier helpers that the
    # compositor does not exercise on the v0.1 widget set.  Reintroduce
    # narrowly if a Phase-4 widget needs them.

    @classmethod
    def divide(cls, segments, cuts):
        """Divide an iterable of segments into chunks at the given cell positions.

        ``cuts`` is an iterable of cell offsets in ascending order; each
        yield is a list of segments whose cumulative cell width matches
        the next cut.  Used by the compositor to slice rendered strips
        along widget boundaries.
        """
        split_segments = []
        add_segment = split_segments.append

        iter_cuts = iter(cuts)

        # Drain any leading 0-cuts; each yields an empty list.
        while True:
            cut = next(iter_cuts, -1)
            if cut == -1:
                return
            if cut != 0:
                break
            yield []
        pos = 0

        segments_clear = split_segments.clear
        segments_copy = split_segments.copy

        _cell_len = cached_cell_len
        for segment in segments:
            text, _style, control = segment
            while text:
                end_pos = pos if control else pos + _cell_len(text)
                if end_pos < cut:
                    add_segment(segment)
                    pos = end_pos
                    break

                if end_pos == cut:
                    add_segment(segment)
                    yield segments_copy()
                    segments_clear()
                    pos = end_pos

                    cut = next(iter_cuts, -1)
                    if cut == -1:
                        if split_segments:
                            yield segments_copy()
                        return

                    break

                else:
                    before, segment = segment.split_cells(cut - pos)
                    text, _style, control = segment
                    add_segment(before)
                    yield segments_copy()
                    segments_clear()
                    pos = cut

                cut = next(iter_cuts, -1)
                if cut == -1:
                    if split_segments:
                        yield segments_copy()
                    return

        yield segments_copy()


# ---------------------------------------------------------------------------
# Segments / SegmentLines - light Console renderables that yield a
# pre-built segment stream.  These are *not* required by Textual's core
# compositor (which yields segments directly) but appear on Textual's
# Phase-7 widget set; keep them so test fixtures and downstream Rich
# code continues to type-check at runtime.
# ---------------------------------------------------------------------------


class Segments:
    """A simple renderable wrapping an iterable of Segment.

    Yields each segment from ``__rich_console__``; if ``new_lines`` is
    True, a newline segment is interleaved between segments.
    """

    def __init__(self, segments, new_lines=False):
        self.segments = list(segments)
        self.new_lines = new_lines

    def __rich_console__(self, console, options):
        if self.new_lines:
            line = Segment.line()
            for segment in self.segments:
                yield segment
                yield line
        else:
            for segment in self.segments:
                yield segment


class SegmentLines:
    """A simple renderable wrapping an iterable of lines of Segment."""

    def __init__(self, lines, new_lines=False):
        self.lines = list(lines)
        self.new_lines = new_lines

    def __rich_console__(self, console, options):
        if self.new_lines:
            new_line = Segment.line()
            for line in self.lines:
                for segment in line:
                    yield segment
                yield new_line
        else:
            for line in self.lines:
                for segment in line:
                    yield segment


# Type alias upstream exposes for downstream callers.  Keep as plain
# tuples; no runtime semantics beyond shape.
ControlCode = tuple


__all__ = (
    "Segment",
    "Segments",
    "SegmentLines",
    "ControlType",
    "ControlCode",
)
