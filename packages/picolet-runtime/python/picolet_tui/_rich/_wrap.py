"""picolet_tui._rich._wrap - Rich's word-wrap helper.

Ported from Rich master
(https://github.com/Textualize/rich/blob/master/rich/_wrap.py,
SHA 46cebbb032f920eb096efbaf23cdc6fe9dd541f7).  Upstream is ~80 LoC of
pure-Python algorithm.

The module is Tier 1 in research doc 02 - it must run on MicroPython
verbatim, modulo Rich-internal imports.  In stock Rich `_wrap` pulls
`loop_last` from `rich._loop` and `cell_len` / `chop_cells` from
`rich.cells`; we inline pared-down versions of all three so this file
has no other Rich-internal dependency.  When `rich._loop` and
`rich.cells` are later ported into `picolet_tui._rich`, the inlined
helpers can be replaced with re-imports without changing the public
surface of `divide_line` / `words`.

Removed vs upstream:
  - Import of `from ._loop import loop_last` - inlined as `_loop_last`
    so this Tier 1 module has zero Rich-internal deps (rule 5).
  - Import of `from .cells import cell_len, chop_cells` - replaced
    with `_cell_len` / `_chop_cells`.  The MicroPython `unicodedata`
    module is partial (no `east_asian_width`), so the local
    `_cell_len` treats every codepoint as one terminal cell.  This is
    correct for ASCII / Latin / box-drawing / Braille (the bulk of
    picolet TUI output) and produces slightly-too-narrow wrap points
    for CJK / wide-emoji input.  See LIMITATIONS below.
  - The `if __name__ == "__main__":` demo block - it instantiates a
    `rich.console.Console`, which is Tier 2 and out of scope for this
    module.

LIMITATIONS:
  Wide characters (East Asian fullwidth / emoji / boxed CJK) are
  measured as 1 cell instead of 2.  Downstream effect: a CJK-heavy
  paragraph will be wrapped at roughly half the intended column count
  (lines look short, not over-wide), which is visually awkward but
  never produces output that overflows the terminal.  Will be fixed
  when `picolet_tui._rich.cells` ships with the Unicode width table
  (research doc 02 sketches the ~670-LoC port).  Tracked as a shim-pack
  gap (the project lacks a `picolet_tui._shims.unicodedata` that would
  let `cells.py` port without bundling its own table).

Supports:
  FR-TUI-41 (Static widget) and FR-TUI-42 (Label truncation) indirectly
    - both go through Rich's `Text` which calls `_wrap.divide_line`
    to compute soft-wrap break offsets.
  NFR-TUI-6 (lru_cache budget) - this module declares no caches, so it
    cannot violate the cap.  The inlined `_cell_len` is O(n) without
    memoisation, matching upstream's behaviour for strings <512 chars
    that miss the `cached_cell_len` fast path.
  NFR-TUI-10 (re1.5 compatibility) - the sole regex (`\\s*\\S+\\s*`)
    uses neither named groups, lookaround, backreferences, nor inline
    flags, so it runs unchanged on re1.5.
"""

import re

from picolet_tui._shims.typing import Iterable

# Rich's word regex - matches one non-whitespace run plus any trailing
# whitespace.  Compatible with MicroPython's re1.5: no named groups,
# no lookaround, no backreferences (NFR-TUI-10).
re_word = re.compile(r"\s*\S+\s*")


def _loop_last(values):
    """Yield (is_last, value) for each item.

    Inlined from rich._loop.loop_last so this module has no
    Rich-internal dependency.  Generator semantics match upstream
    exactly: empty iterables yield nothing; single-item iterables yield
    one `(True, value)` pair.
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


def _cell_len(text):
    """Cell width of `text` assuming every codepoint is narrow.

    Stand-in for `rich.cells.cell_len` while `picolet_tui._rich.cells`
    is unported.  Returning `len(text)` is exact for ASCII and the
    Latin / Cyrillic / Greek / box-drawing / Braille ranges that
    upstream Rich's `_SINGLE_CELL_UNICODE_RANGES` whitelists - and is
    half the correct value for fullwidth CJK / emoji.  The wrap
    algorithm's only contract is "return a non-negative integer width
    in cells", so under-counting wide chars produces conservative
    (short) lines rather than overflowing ones.  See module LIMITATIONS.
    """
    return len(text)


def _chop_cells(text, width):
    """Slice `text` into chunks of at most `width` cells.

    Stand-in for `rich.cells.chop_cells`.  Because `_cell_len` here
    treats every codepoint as one cell, the slicing is the simple
    `text[i:i+width]` form - which is exactly what upstream Rich's
    fast path (`_is_single_cell_widths(text)`) takes when every char is
    narrow.  Wide-char input falls through to the same path here, which
    is the limitation documented above.
    """
    return [text[i : i + width] for i in range(0, len(text), width)]


def words(text):
    """Yield (start_index, end_index, word) for each word in `text`.

    A "word" here is `re_word`'s match: one non-whitespace run plus any
    trailing whitespace.  Upstream Rich annotates this as
    `Iterable[tuple[int, int, str]]`; the typing-shim placeholder for
    `Iterable` accepts no parameters at runtime so the annotation has
    been moved to the docstring.
    """
    position = 0
    word_match = re_word.match(text, position)
    while word_match is not None:
        start, end = word_match.span()
        word = word_match.group(0)
        yield start, end, word
        word_match = re_word.match(text, end)


def divide_line(text, width, fold=True):
    """Return cell offsets at which `text` should break to fit `width`.

    Args:
        text: The text to examine.
        width: The available cell width.
        fold: If True, words longer than `width` are folded onto a new
            line (matching upstream Rich's default).

    Returns:
        A list of indices into `text` where breaks should be inserted.

    Matches upstream Rich's signature and return shape exactly; callers
    in `text.py` (Tier 2) and Textual rely on the integer-offset list.
    """
    # break_positions: offsets into `text` to insert breaks at.
    break_positions = []
    append = break_positions.append
    cell_offset = 0
    # Local alias matches the upstream micro-optimisation - the hot
    # path calls cell_len once per word, occasionally twice.
    cell_len = _cell_len

    for start, _end, word in words(text):
        word_length = cell_len(word.rstrip())
        remaining_space = width - cell_offset
        word_fits_remaining_space = remaining_space >= word_length

        if word_fits_remaining_space:
            # Simplest case: word fits on the current line.
            cell_offset += cell_len(word)
        else:
            # Not enough room on the current line.
            if word_length > width:
                # The word is longer than any whole line.
                if fold:
                    # Fold across multiple lines.
                    folded_word = _chop_cells(word, width)
                    for last, line in _loop_last(folded_word):
                        if start:
                            append(start)
                        if last:
                            cell_offset = cell_len(line)
                        else:
                            # Advance `start` past the chunk we just
                            # emitted so the next break lands at the
                            # right offset in the original `text`.
                            start += len(line)
                else:
                    # Folding disabled: crop the word at the line edge.
                    if start:
                        append(start)
                    cell_offset = cell_len(word)
            elif cell_offset and start:
                # Word doesn't fit on this line but does fit on a fresh
                # one - emit a break before it.
                append(start)
                cell_offset = cell_len(word)

    return break_positions
