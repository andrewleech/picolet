"""picolet_tui._rich.cells - character-cell-width helpers ported from Rich.

Upstream provenance
-------------------
Ported from Rich master
  https://github.com/Textualize/rich/blob/master/rich/cells.py
  SHA 46cebbb032f920eb096efbaf23cdc6fe9dd541f7 (Rich 14.x line).

Upstream is ~350 LoC of logic plus a multi-version Unicode width data
package (rich._unicode_data) that lazily selects between 21 width
tables.  Per synthesis D5 / NFR-TUI-13 the picolet-tui variant ships
exactly one table (Unicode 15.1.0) and dispenses with version
selection entirely; the data lives in the sibling ``_cell_widths``
module and is consumed here through three module-level constants.

What was REMOVED vs upstream
----------------------------
* ``rich._unicode_data`` package + ``load_cell_table(unicode_version)``
  dispatcher: replaced by a fixed import of ``WIDTHS`` /
  ``NARROW_TO_WIDE`` from ``_cell_widths``.  The ``unicode_version=``
  parameter is preserved on every public function for upstream
  signature parity (rule 1) but is *ignored* - the value is silently
  treated as "15.1.0".  This matches NFR-TUI-13 ("the 15.1.0 table is
  consulted unconditionally") and NFR-TUI-32 (locale-independent;
  ``UNICODE_VERSION`` env var is not read).

* The ``functools.lru_cache(maxsize=4096)`` decorators on
  ``get_character_cell_size`` and ``cached_cell_len`` are downgraded
  to ``maxsize=128`` per NFR-TUI-6 (synthesis R4 mitigation a).
  The shim in ``picolet_tui._shims.functools`` would clamp larger
  values silently but we set the constant explicitly so the import-
  time audit in NFR-TUI-6 has a stable source to grep.

* Upstream uses the walrus operator (``character_width :=``) in
  ``_cell_len`` / ``split_graphemes``.  MicroPython supports PEP 572
  but no other picolet-tui shim or _rich file uses it; we expand to
  named-variable form for stylistic uniformity (and to keep the
  freezer's bytecode generator on a path the rest of the codebase
  already exercises).

* ``from typing import Callable, NamedTuple, Sequence, Tuple`` is
  routed through the local typing shim.  ``NamedTuple`` is hand-rolled
  as a tuple subclass (same pattern as ``color_triplet.ColorTriplet``)
  because the typing shim deliberately omits it (frozen-bytes budget,
  NFR-TUI-19 - see ``picolet_tui/_shims/typing.py``).

* ``operator.itemgetter(2)`` is replaced with a plain ``lambda span:
  span[2]``.  ``operator`` is in micropython-lib but the one call site
  is hot enough that a closure is cheaper than the attribute lookup,
  and avoiding the import keeps this file tier-2 import-clean.

* The ``CellSpan = Tuple[int, int, int]`` alias is preserved as
  module-level metadata; the typing shim renders the subscript as a
  no-op placeholder.

Public surface (matches upstream exactly)
-----------------------------------------
* ``CellTable`` - NamedTuple-like class with the same three fields
  (``unicode_version``, ``widths``, ``narrow_to_wide``).  The default
  ``CELL_TABLE`` instance is exposed for callers that hold a reference
  to it.
* ``get_character_cell_size(character, unicode_version="auto") -> int``
* ``cached_cell_len(text, unicode_version="auto") -> int``
* ``cell_len(text, unicode_version="auto") -> int``
* ``split_graphemes(text, unicode_version="auto") -> tuple[list, int]``
* ``split_text(text, cell_position, unicode_version="auto") -> tuple[str, str]``
* ``set_cell_size(text, total, unicode_version="auto") -> str``
* ``chop_cells(text, width, unicode_version="auto") -> list[str]``

Re-evaluation of ``re`` usage (porting rule 6)
----------------------------------------------
This module contains no regex.  All width measurement is integer
arithmetic over ``WIDTHS`` plus codepoint membership tests against
``NARROW_TO_WIDE`` and ``_SINGLE_CELLS``.

Tier and dependency policy (porting rule 5)
-------------------------------------------
Tier 2 module.  Imports only:
* ``picolet_tui._shims.functools.lru_cache`` (Phase 2b shim)
* ``picolet_tui._shims.typing.Callable`` / ``Sequence`` / ``Tuple``
* ``picolet_tui._rich._cell_widths`` (Tier 1 data sibling)

No imports from later Rich tiers; specifically no ``segment``,
``style``, ``text``, ``console``, or any widget-level module.

Spec coverage
-------------
* FR-TUI-41 / FR-TUI-42 (Static / Label rendering) consume ``cell_len``
  via the trimmed ``Text`` and ``_wrap`` modules for truncation +
  soft-wrap break calculation.
* FR-TUI-51 (ProgressBar) reads ``cell_len`` to map fractional cell
  fills onto Unicode block characters.
* NFR-TUI-6 (lru_cache maxsize cap) - both caches set maxsize=128
  explicitly; the import-time test that walks ``cache_info().maxsize``
  will see the cap and pass.
* NFR-TUI-13 (single Unicode 15.1.0 table) - the import of
  ``_cell_widths`` is the only data source; no other table is
  reachable from this module.
* NFR-TUI-19 (frozen romfs budget) - logic-only here is ~250 LoC; the
  data sibling carries the ~670 LoC of constants.
* NFR-TUI-32 (locale independence) - ``unicode_version`` parameter is
  accepted for source-compat but never branches on the value.
"""

from collections import namedtuple

from picolet_tui._shims.functools import lru_cache
from picolet_tui._shims.typing import Callable, Sequence, Tuple


from picolet_tui._rich._cell_widths import (
    NARROW_TO_WIDE,
    UNICODE_VERSION,
    WIDTHS,
)


# Type alias for a (start_index, end_index, cell_width) span.  Preserved
# from upstream for any external annotation that imports the name; the
# typing shim renders Tuple[...] as a placeholder so no runtime cost.
CellSpan = Tuple[int, int, int]


# Replaces upstream's ``operator.itemgetter(2)``; the single call site
# is in ``_split_text`` and benefits from the closure being module-local.
def _span_get_cell_len(span):
    return span[2]


# Codepoint ranges that always render in exactly one cell.  Listing
# them up-front lets ``_is_single_cell_widths`` short-circuit on the
# common case of pure-Latin / box-drawing / Braille strings before any
# width-table lookup.  Identical to upstream's data.
_SINGLE_CELL_UNICODE_RANGES = [
    (0x20, 0x7E),  # Latin (excluding non-printable)
    (0xA0, 0xAC),
    (0xAE, 0x002FF),
    (0x00370, 0x00482),  # Greek / Cyrillic
    (0x02500, 0x025FC),  # Box drawing, box elements, geometric shapes
    (0x02800, 0x028FF),  # Braille
]

# Materialised frozenset for O(1) membership; built once at import time.
# Memory cost ~16 KB of small-int chr objects, paid back the first time
# a screen-full of Latin text avoids the bisect-over-WIDTHS loop.
_SINGLE_CELLS = frozenset(
    [
        character
        for _start, _end in _SINGLE_CELL_UNICODE_RANGES
        for character in map(chr, range(_start, _end + 1))
    ]
)


# When called with a string this returns True iff every codepoint is
# in _SINGLE_CELLS - i.e. the whole string is guaranteed-narrow and
# ``len(text)`` is exactly its cell width.  Bound here rather than as a
# def because frozenset.issuperset is a C method on CPython / native on
# MicroPython, faster than a hand-written wrapper.
_is_single_cell_widths = _SINGLE_CELLS.issuperset  # type: Callable


# NamedTuple-shaped container for the Unicode width data.  Matches
# upstream Rich's ``CellTable`` field-by-field (``unicode_version``,
# ``widths``, ``narrow_to_wide``) so callers that destructure or
# attribute-access the table work unchanged.  collections.namedtuple
# rather than a hand-rolled tuple subclass: ``tuple.__new__(cls, ...)``
# raises AttributeError on MicroPython, and namedtuple is a C builtin
# there (no frozen-bytes cost).  Same change as
# ``color_triplet.ColorTriplet``.
CellTable = namedtuple("CellTable", ("unicode_version", "widths", "narrow_to_wide"))


# Singleton table built from the bundled 15.1.0 data.  Exposed as
# ``CELL_TABLE`` for any caller that needs the upstream CellTable
# instance shape; internal code paths reach into WIDTHS / NARROW_TO_WIDE
# directly to skip the attribute hop.
CELL_TABLE = CellTable(UNICODE_VERSION, WIDTHS, NARROW_TO_WIDE)


@lru_cache(maxsize=128)
def get_character_cell_size(character, unicode_version="auto"):
    """Get the cell size of a single character.

    Args:
        character: A single character (one codepoint).
        unicode_version: Accepted for upstream signature parity; the
            value is ignored because the picolet-tui variant ships
            only the 15.1.0 table (NFR-TUI-13).

    Returns:
        Number of cells (0, 1 or 2) occupied by that character.
    """
    # Width upstream contract:
    #   * C0 controls (1..31)            -> 0
    #   * DEL (0x7F) and C1 (0x80..0x9F) -> 0
    #   * U+0000 special-cased to 0 by the table itself.
    # Compose those two conditions inline rather than via the table so
    # that the common ASCII fast path costs one comparison.
    codepoint = ord(character)
    if codepoint and codepoint < 32 or 0x7F <= codepoint < 0xA0:
        return 0

    table = WIDTHS

    last_entry = table[-1]
    if codepoint > last_entry[1]:
        # Past the highest assigned range -> assume one cell (matches
        # upstream's default for unassigned / private-use planes).
        return 1

    # Binary search the (start, end, width) intervals.  WIDTHS is
    # sorted and non-overlapping so a single bisect is sufficient.
    lower_bound = 0
    upper_bound = len(table) - 1

    while lower_bound <= upper_bound:
        index = (lower_bound + upper_bound) >> 1
        start, end, width = table[index]
        if codepoint < start:
            upper_bound = index - 1
        elif codepoint > end:
            lower_bound = index + 1
        else:
            return width
    return 1


@lru_cache(maxsize=128)
def cached_cell_len(text, unicode_version="auto"):
    """Get the number of cells required to display ``text``, cached.

    This function always caches; for one-shot measurements prefer
    ``cell_len`` which skips the cache for strings >= 512 chars.

    Args:
        text: Text to measure.
        unicode_version: Accepted for upstream signature parity; ignored.

    Returns:
        Cell width of ``text``.
    """
    return _cell_len(text, unicode_version)


def cell_len(text, unicode_version="auto"):
    """Get the cell length of a string as rendered in the terminal.

    Routes short strings through the LRU cache and long ones around
    it, matching upstream's heuristic for keeping cache entries small.

    Args:
        text: String to measure.
        unicode_version: Accepted for upstream signature parity; ignored.

    Returns:
        Length of string in terminal cells.
    """
    if len(text) < 512:
        return cached_cell_len(text, unicode_version)
    return _cell_len(text, unicode_version)


def _cell_len(text, unicode_version):
    """Width computation for ``cell_len`` / ``cached_cell_len``.

    Three layered fast paths:
      1. All-narrow whitelist  -> return len(text).
      2. No ZWJ / VS-16 either -> sum per-character widths.
      3. General case          -> walk codepoints honouring ZWJ and
                                  variation-selector-16 modifiers.
    """
    if _is_single_cell_widths(text):
        return len(text)

    # "‍" is zero width joiner; "️" is variation selector 16.
    # When neither is present, no codepoint mutates the width of its
    # neighbour, so a plain sum is exact.
    if "‍" not in text and "️" not in text:
        return sum(
            get_character_cell_size(character, unicode_version)
            for character in text
        )

    # Slow path: walk codepoints, applying ZWJ / VS-16 semantics.
    narrow_to_wide = NARROW_TO_WIDE
    total_width = 0
    last_measured_character = None
    SPECIAL = {"‍", "️"}

    index = 0
    character_count = len(text)

    while index < character_count:
        character = text[index]
        if character in SPECIAL:
            if character == "‍":
                # ZWJ swallows itself and the following codepoint
                # (handled by the next loop iteration measuring the
                # join target instead of a standalone glyph).
                index += 1
            elif last_measured_character:
                # VS-16 promotes the previous narrow emoji-base to
                # its emoji presentation, which is two cells wide.
                # Add one cell only if the base was in the
                # narrow->wide promotion set; otherwise no effect.
                if last_measured_character in narrow_to_wide:
                    total_width += 1
                last_measured_character = None
        else:
            character_width = get_character_cell_size(
                character, unicode_version
            )
            if character_width:
                last_measured_character = character
                total_width += character_width
        index += 1

    return total_width


def split_graphemes(text, unicode_version="auto"):
    """Divide ``text`` into grapheme spans + total cell length.

    Spans cover every index with no gaps; some graphemes may have a
    cell length of zero (e.g. lone ZWJs, control codes).

    Args:
        text: String to split.
        unicode_version: Accepted for upstream signature parity; ignored.

    Returns:
        Tuple ``(spans, total_cell_length)`` where ``spans`` is a list
        of ``(start_index, end_index, cell_width)`` tuples.
    """
    narrow_to_wide = NARROW_TO_WIDE
    codepoint_count = len(text)
    index = 0
    last_measured_character = None

    total_width = 0
    spans = []
    SPECIAL = {"‍", "️"}

    while index < codepoint_count:
        character = text[index]
        if character in SPECIAL:
            if not spans:
                # Defensive: ZWJ or VS-16 at string start has no base
                # to attach to.  Emit a zero-width span so downstream
                # code can still iterate without index gaps.
                spans.append((index, index + 1, 0))
                index += 1
                continue
            if character == "‍":
                # ZWJ joins the previous grapheme to whatever follows.
                # Extend the previous span by one (ZWJ) plus, when
                # available, the next codepoint as well.
                step = 2 if index < (codepoint_count - 1) else 1
                index += step
                start, _end, cell_length = spans[-1]
                spans[-1] = (start, index, cell_length)
            else:
                # VS-16: extend the previous span by one codepoint and
                # promote its width if the base was narrow-but-promotable.
                index += 1
                if last_measured_character:
                    start, _end, cell_length = spans[-1]
                    if last_measured_character in narrow_to_wide:
                        last_measured_character = None
                        cell_length += 1
                        total_width += 1
                    spans[-1] = (start, index, cell_length)
                else:
                    # No previous character to mutate.  Should not
                    # occur in practice (would imply VS-16 follows a
                    # zero-width span), but handle defensively.
                    start, _end, cell_length = spans[-1]
                    spans[-1] = (start, index, cell_length)
            continue

        character_width = get_character_cell_size(character, unicode_version)
        if character_width:
            last_measured_character = character
            spans.append((index, index + 1, character_width))
            total_width += character_width
            index += 1
        else:
            # Zero-width codepoint attaches to the previous span if
            # one exists; otherwise emit a stand-alone zero-width span.
            if spans:
                start, _end, cell_length = spans[-1]
                spans[-1] = (start, index + 1, cell_length)
                index += 1
            else:
                spans.append((index, index + 1, 0))
                index += 1

    return (spans, total_width)


def _split_text(text, cell_position, unicode_version="auto"):
    """Split ``text`` at ``cell_position``, splitting wide glyphs to spaces.

    If the requested cell position lands inside a double-width
    character, that character is rendered as two spaces - one trailing
    the left half, one leading the right half - which gives a clean
    column break without overlapping the wide glyph.

    Args:
        text: Text to split.
        cell_position: Offset in cells.
        unicode_version: Accepted for upstream signature parity; ignored.

    Returns:
        Tuple ``(left, right)`` of two strings whose concatenation is
        the original ``text`` (up to wide-glyph substitution).
    """
    if cell_position <= 0:
        return "", text

    spans, cell_length = split_graphemes(text, unicode_version)

    # Initial guess: linear interpolation by character / cell ratio.
    # Refined by the loop below; the guess is just to avoid scanning
    # spans from index 0 every time.
    offset = int((cell_position / cell_length) * len(spans))
    left_size = sum(map(_span_get_cell_len, spans[:offset]))

    while True:
        if left_size == cell_position:
            if offset >= len(spans):
                return text, ""
            split_index = spans[offset][0]
            return text[:split_index], text[split_index:]
        if left_size < cell_position:
            start, end, cell_size = spans[offset]
            if left_size + cell_size > cell_position:
                # Split lands inside a wide glyph - substitute spaces.
                return text[:start] + " ", " " + text[end:]
            offset += 1
            left_size += cell_size
        else:  # left_size > cell_position
            start, end, cell_size = spans[offset - 1]
            if left_size - cell_size < cell_position:
                return text[:start] + " ", " " + text[end:]
            offset -= 1
            left_size -= cell_size


def split_text(text, cell_position, unicode_version="auto"):
    """Public ``_split_text`` with the all-narrow fast path.

    Args:
        text: Text to split.
        cell_position: Offset in cells.
        unicode_version: Accepted for upstream signature parity; ignored.

    Returns:
        Tuple of two strings.
    """
    if _is_single_cell_widths(text):
        return text[:cell_position], text[cell_position:]
    return _split_text(text, cell_position, unicode_version)


def set_cell_size(text, total, unicode_version="auto"):
    """Pad or crop ``text`` so its cell width is exactly ``total``.

    Args:
        text: String to adjust.
        total: Desired size in cells.
        unicode_version: Accepted for upstream signature parity; ignored.

    Returns:
        A string with cell width equal to ``total`` (or empty when
        ``total`` is non-positive).
    """
    if _is_single_cell_widths(text):
        size = len(text)
        if size < total:
            return text + " " * (total - size)
        return text[:total]
    if total <= 0:
        return ""
    cell_size = cell_len(text)
    if cell_size == total:
        return text
    if cell_size < total:
        return text + " " * (total - cell_size)
    text, _ = _split_text(text, total, unicode_version)
    return text


def chop_cells(text, width, unicode_version="auto"):
    """Split ``text`` into lines that each fit within ``width`` cells.

    Args:
        text: The text to fold into width-bounded lines.
        width: The available width (number of cells).
        unicode_version: Accepted for upstream signature parity; ignored.

    Returns:
        A list of strings; each entry's cell width is <= ``width``.
        Concatenating the list reproduces ``text`` for the all-narrow
        case; for wide-glyph input, grapheme boundaries are respected
        (no glyph is split mid-codepoint).
    """
    if _is_single_cell_widths(text):
        return [text[index : index + width] for index in range(0, len(text), width)]
    spans, _ = split_graphemes(text, unicode_version)
    line_size = 0  # Running cell-count of the current line.
    lines = []
    line_offset = 0  # Codepoint offset where the current line begins.
    for start, _end, cell_size in spans:
        if line_size + cell_size > width:
            lines.append(text[line_offset:start])
            line_offset = start
            line_size = 0
        line_size += cell_size
    if line_size:
        lines.append(text[line_offset:])

    return lines
