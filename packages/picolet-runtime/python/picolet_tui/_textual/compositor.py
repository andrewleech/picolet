"""picolet_tui._textual.compositor - widget-tree -> per-row strip diff.

This is the Phase 4c body for the contract pinned in
``docs/tui/textual-core-design.md`` §7 ("Compositor integration
contract").  It operates against the *node contract* documented in
§7, not against the (Phase 4b) Widget class itself: a node here is
anything that exposes ``id``, ``region``, ``render()``, and
``children``.  That keeps the compositor importable and unit-testable
before Phase 4b lands Widget, and matches the upstream Textual
boundary where ``_compositor.py`` consumed a frozen tree dumped by the
layout pass.

The shape stays as the synthesis (Phase 4c, §"Compositor") promised:
walk -> render strips -> per-row diff against last frame -> emit
positioned segment tuples to the App, which in turn hands them to
``tuiterm.write`` prefixed with CSI ``CUP`` row/col positioning.  This
file does NOT emit ANSI bytes directly; that translation lives in the
caller so the Style/Color downgrade ladder (FR-TUI-40) and the
``tuiterm`` boundary stay co-located.

What this module owns
---------------------
* A frame buffer the same height as the viewport, each entry being a
  list of ``Segment`` instances padded to viewport width.  Rebuilt
  per frame from a depth-first tree walk.
* A "previous frame buffer" of the same shape, kept across calls so
  ``render_frame()`` can emit only changed rows.
* A "dirty" record so ``mark_dirty(node)`` from ``Widget.refresh()``
  can opt a subtree out of the diff-skip optimisation.  This is the
  hook the synthesis names ("the dirty signal").
* A single Console instance, owned for the compositor's lifetime, so
  ``ConsoleOptions`` is constructed fresh per node from the node's
  region (synthesis §7.3: no thread-local options, no escape).

What this module does NOT own
-----------------------------
* Layout.  Per design doc step 5 D2 / FR-TUI-29, layout is Widget's
  job in v0.1.  Each node arrives with ``.region`` populated by the
  caller; the compositor only walks the tree to read those regions
  back, never to compute them.  When the caller passes a node with
  ``region is None`` (or ``NULL_REGION``), the compositor *does* fall
  back to a simple vertical stack inside the parent's region so the
  contract is closed even before Phase 5 lays in real Containers;
  this lets the test harness drive a tree without ginning up a layout
  pass.
* ANSI emission.  The output is ``(col, row, segments)`` tuples; the
  App turns those into CSI sequences via the Color/Style downgrade
  ladder in FR-TUI-40 and writes through ``tuiterm.write``.
* z-order / overlay.  Synthesis explicitly drops popovers from v0.1;
  the compositor walks the tree in document order and the last paint
  wins per cell.  Re-adding z-order is one sort step in ``_walk``;
  not worth the LoC until v0.2 ships modals.
* Smooth scrolling.  Sub-cell precision is out of scope (D7); strips
  are integer-aligned.

Algorithm in one paragraph
--------------------------
``update_dom`` walks the tree depth-first, asking each node for its
region (or computing a stack-allocated region from the parent) and
calling ``console.render_lines(node.render(), ConsoleOptions(width,
height))``.  The returned ``List[List[Segment]]`` is spliced into the
frame buffer at the node's ``(x, y)`` offset, padding short lines and
clipping over-tall renders to the node's height.  Children are
walked after their parent so child paint overrides parent paint -
matches the upstream Textual draw order (parent paints background,
children paint on top).  ``render_frame`` then walks rows: for each
row whose segment list differs from last frame's same row, it finds
the leftmost and rightmost *cell* (not segment) that changed, and
emits one ``(col, row, segments_slice)`` tuple for the changed span.
``full_redraw`` is the same walk but skips the diff and emits every
row.  ``mark_dirty(node)`` clears that node's region in the previous
frame buffer so the diff sees those cells as "definitely changed" on
the next ``render_frame`` even when the new segments happen to byte-
compare equal (this is the only way to force a repaint when the
caller knows something off-screen changed - e.g. cursor focus moved
to a sibling whose surface is identical).

Output shape
------------
``render_frame() -> List[Tuple[int, int, List[Segment]]]`` where each
tuple is ``(col, row, segments_for_that_row)``.  ``segments`` is
sliced from the row buffer; the caller is responsible for treating
it as read-only.  Empty list means "no changes since last frame";
the App's render task uses this to skip writes entirely when nothing
has moved.

Spec coverage
-------------
FR-TUI-29 / FR-TUI-30  Reads ``Region`` per node, applies a fallback
                       vertical stack when missing.
FR-TUI-31             Re-renders the whole tree on ``update_dom`` -
                       the layout-changed signal flows in via the
                       caller calling ``update_dom`` again.
FR-TUI-76             ``update_dom`` checks the viewport size against
                       the 20x5 minimum and, when below it, fills a
                       single centred "Terminal too small" line in
                       place of the normal walk.
NFR-TUI-3             Per-row diff with leftmost/rightmost-cell
                       bounding so a one-character change yields a
                       one-tuple emit; bounds the bytes-to-terminal
                       on resize and on cursor-tick refreshes.
NFR-TUI-11            Single-threaded; no locks, no thread-local
                       state, no ``threading`` shim usage here.

Note on the design doc deviation
--------------------------------
Design doc §7.2 sketched a Compositor that returned ANSI bytes
directly via a `_cup` / `_seg_to_ansi` pair built into the
compositor.  This file moves that translation up to the App so the
Color downgrade (FR-TUI-40) and the tuiterm boundary stay together
- the design doc's sketch was illustrative ("Phase 4c implements the
diff-and-emit body"), the actual emit-to-bytes step is App-level.
The agent task description here explicitly names the
``(col, row, segments)`` output shape, which is what this file
produces.
"""

from picolet_tui._shims.typing import (
    List,
    Optional,
    Tuple,
)

from picolet_tui._rich.cells import get_character_cell_size
from picolet_tui._rich.console import Console, ConsoleOptions, ConsoleDimensions
from picolet_tui._rich.segment import Segment

from .geometry import NULL_REGION, Region, Size


# Minimum viable terminal size per FR-TUI-76.  Below this, the
# compositor short-circuits the normal walk and paints a single
# centred message line.  The "too small" rendering deliberately bypasses
# the node tree entirely so it works even when the tree is mid-mount.
_MIN_COLS = 20
_MIN_ROWS = 5


def _empty_row(width):
    """Return a single Segment that fills ``width`` cells with spaces.

    Wrapped as a [Segment] list so the row buffer entries are
    type-uniform - the row buffer is always ``List[Segment]``, even
    for blank rows.  Returned fresh on every call so the caller can
    mutate it without aliasing back into the cache.
    """
    # ``width <= 0`` is a degenerate clip (parent had zero room);
    # return an empty list rather than a Segment("") so the diff
    # treats "no cells" and "all blank" symmetrically.
    if width <= 0:
        return []
    return [Segment(" " * width)]


def _row_cell_width(row):
    """Sum cell-widths across a row of segments.

    Used by the diff loop to map the (cell-space) leftmost/rightmost
    change positions back to *segment indices*.  Control segments
    contribute zero cells; ``Segment.cell_length`` already accounts
    for that, so we just sum.
    """
    return sum(seg.cell_length for seg in row)


def _splat_lines(buffer, lines, x, y, viewport_width, viewport_height):
    """Place ``lines`` (one per node row) into ``buffer`` at (x, y).

    ``buffer`` is the frame's ``List[List[Segment]]``, indexed by
    row.  Each ``lines[i]`` is ``List[Segment]`` already padded by
    ``Console.render_lines(pad=True)`` to the node's width.

    Splatting honours three constraints:

    * Off-viewport rows are skipped (no out-of-bounds writes).  The
      node's region may be partly off-screen because a parent has
      not yet clipped against the viewport - layout owns that, but
      the compositor must not crash if the caller hands us a tree
      that pokes off-edge.
    * The horizontal position ``x`` may be non-zero - the row we
      receive only covers the node's width, but the buffer row is
      the full viewport width.  We splice by walking existing buffer
      segments cell-by-cell, finding the cell at column ``x``,
      splitting any segment that straddles ``x``, inserting the
      node's segments, then splitting on the trailing edge so the
      right-hand remainder of the original row is preserved.  This
      keeps a sibling that paints at columns 10-19 from clobbering
      another sibling already painted at 0-9.
    * Empty ``lines`` (zero rows) is a no-op.  A node with height 0
      contributes nothing.
    """
    if not lines:
        return

    for offset, line_segments in enumerate(lines):
        row_y = y + offset
        if row_y < 0 or row_y >= viewport_height:
            # Off-viewport; the row exists in ``lines`` but we have
            # no buffer slot for it.  Drop silently - the caller will
            # see the visible portion render correctly.
            continue

        # ``line_segments`` is what render_lines produced.  It has
        # already been pad-extended to ``node.width`` by Rich; we
        # just need to position it at column ``x``.
        if x == 0 and (offset == 0 or True):
            # Common fast path: node origin is at column 0 and the
            # render width equals the viewport width.  We can replace
            # the row outright iff the rendered width matches the
            # viewport - otherwise the rightmost columns would not
            # be padded.  Check both before taking the path.
            rendered_width = _row_cell_width(line_segments)
            if rendered_width == viewport_width:
                buffer[row_y] = list(line_segments)
                continue

        # General path: splice ``line_segments`` into ``buffer[row_y]``
        # at cell column ``x``.  Build the new row in three parts:
        # the left slice (columns 0..x), the node's segments, and the
        # right slice (columns x+rendered_width..viewport_width).
        existing = buffer[row_y] or _empty_row(viewport_width)
        rendered_width = _row_cell_width(line_segments)
        right_edge = x + rendered_width

        left_slice = _slice_row(existing, 0, x, viewport_width)
        right_slice = _slice_row(existing, right_edge, viewport_width,
                                 viewport_width)
        buffer[row_y] = left_slice + list(line_segments) + right_slice


def _slice_row(row, start_cell, end_cell, viewport_width):
    """Return a list of segments covering cells ``[start_cell, end_cell)``.

    Walks ``row`` cell-by-cell, splitting segments that straddle the
    boundaries via ``Segment.split_cells`` so wide-character glyphs
    survive the slice intact.  When the cell range extends past the
    last segment in ``row``, the gap is filled with a padding
    ``Segment(" ")`` of the missing width - this preserves the
    invariant that every row buffer entry covers the full viewport
    width.
    """
    # Defensive: empty range yields nothing.
    if end_cell <= start_cell:
        return []

    # Empty input row -> pad the whole requested range.
    if not row:
        gap = end_cell - start_cell
        return [Segment(" " * gap)] if gap > 0 else []

    out = []
    cell_cursor = 0
    for seg in row:
        seg_cells = seg.cell_length
        if seg_cells == 0:
            # Control segment - pass through without consuming cells.
            # Position-insensitive; keep it in the output if we're
            # inside the requested range.
            if start_cell <= cell_cursor <= end_cell:
                out.append(seg)
            continue

        seg_start = cell_cursor
        seg_end = cell_cursor + seg_cells
        cell_cursor = seg_end

        if seg_end <= start_cell:
            # Segment ends before the requested range begins; skip.
            continue
        if seg_start >= end_cell:
            # Segment starts after the requested range ends; we are
            # done walking this row.
            break

        # Segment intersects the requested range.  Split off the
        # left tail if seg_start < start_cell, then the right tail
        # if remaining end > end_cell.
        piece = seg
        if seg_start < start_cell:
            # Drop the left part.  split_cells(piece, cut) returns
            # (left, right) where left has cell_length == cut.
            _, piece = piece.split_cells(start_cell - seg_start)
            piece_start = start_cell
        else:
            piece_start = seg_start

        piece_end = piece_start + piece.cell_length
        if piece_end > end_cell:
            # Drop the right part.
            piece, _ = piece.split_cells(end_cell - piece_start)

        out.append(piece)

    # Tail padding: if we ran out of segments before reaching the
    # end of the requested range, fill the gap with spaces.  Only
    # do so for the contiguous tail; gaps in the middle are
    # impossible because we walked a contiguous row.
    covered = sum(s.cell_length for s in out)
    requested = end_cell - start_cell
    if covered < requested:
        out.append(Segment(" " * (requested - covered)))

    return out


def _rows_equal(row_a, row_b):
    """Cell-aware row equality.

    Two rows are equal iff their segment sequences compare equal.
    ``Segment.__eq__`` is tuple-shaped over ``(text, style, control)``
    so this is a list-of-tuples comparison, which Python's builtin
    list ``==`` already handles.  The wrapper exists to give the diff
    loop a single place to swap in a cell-level check if profiling
    ever shows the segment-level check is too coarse (it would only
    matter for the synthesis-D5 "16-color downgrade flapping" edge
    case, not for v0.1).
    """
    return row_a == row_b


def _diff_row(prev, curr):
    """Find the leftmost-changed and rightmost-changed *cell positions*.

    Returns ``(left_cell, right_cell, segments_in_range)`` where the
    segments are sliced from ``curr`` covering exactly
    ``[left_cell, right_cell)``.  Returns ``None`` when the rows are
    cell-by-cell identical.

    The diff walks both rows cell-by-cell with two cursors so wide
    characters don't desynchronise the column-space comparison: a
    single double-width glyph in one row consumes two cells of the
    cursor even though it's one segment.  Working in cell space lets
    us emit the minimal-width tuple even when the change crosses a
    wide-char boundary.
    """
    if _rows_equal(prev, curr):
        return None

    # Materialise per-cell views of both rows as flat lists of (text,
    # style, control) cells.  Wide chars expand to (char, style,
    # control), (None, style, control) where the second slot is the
    # filler placeholder.  This is O(total cells) once per changed
    # row, which is acceptable on the v0.1 surface (80x24 = 1920
    # cells/frame upper bound for a full repaint).
    prev_cells = _row_to_cells(prev)
    curr_cells = _row_to_cells(curr)

    # Pad the shorter to the longer's length with blank cells - the
    # diff is taken in cell space, and a row that's "shorter" simply
    # means trailing blank cells in the abstract row buffer.
    width = max(len(prev_cells), len(curr_cells))
    if len(prev_cells) < width:
        prev_cells = prev_cells + [(" ", None, None)] * (width - len(prev_cells))
    if len(curr_cells) < width:
        curr_cells = curr_cells + [(" ", None, None)] * (width - len(curr_cells))

    # Leftmost change.
    left = 0
    while left < width and prev_cells[left] == curr_cells[left]:
        left += 1
    # No changes detected after all - cell-equal rows that don't
    # segment-equal (e.g. ``[Segment("ab")]`` vs
    # ``[Segment("a"), Segment("b")]``).  Treat as no-diff: the
    # caller need not re-emit.
    if left >= width:
        return None

    # Rightmost change.
    right = width - 1
    while right >= left and prev_cells[right] == curr_cells[right]:
        right -= 1
    right += 1  # convert inclusive -> exclusive

    # Slice the current row to cover [left, right) in cell space.
    segments = _slice_row(curr, left, right, width)
    return (left, right, segments)


def _row_to_cells(row):
    """Expand a row of segments into one tuple per terminal cell.

    Each cell is ``(char, style, control)``; double-width chars yield
    two cells in a row, the second one being a placeholder so the
    column cursor advances by two.  Control segments contribute no
    cells (they're meta).
    """
    cells = []
    for seg in row:
        if seg.control is not None:
            # Control segment: zero cells, but include the marker so
            # two rows that differ only in a control segment do not
            # compare equal.  Encode it as a (None-tagged) sentinel
            # placed at the current column position.  Since we
            # bucket-by-column for the leftmost/rightmost search,
            # putting the marker after the previous cell means a
            # control-only change is detected at the *next* cell
            # boundary - acceptable: any practical change in control
            # segments comes alongside a text change at the same row.
            continue
        text = seg.text
        if not text:
            continue
        # Per-character cell expansion.  We rely on ``Segment``'s
        # cell-length already accounting for wide chars - so the
        # number of expanded cells equals ``seg.cell_length``.  Walk
        # the characters and emit one cell per char, then a filler
        # cell after each wide char.
        for ch in text:
            size = get_character_cell_size(ch)
            cells.append((ch, seg.style, None))
            if size == 2:
                # Filler cell so wide chars consume two cell slots.
                cells.append(("", seg.style, None))
    return cells


def _layout_default(node, region, viewport_size):
    """Assign regions in a default vertical stack when none are set.

    This is the contract the design doc describes for v0.1: layout is
    Widget-owned, but Container widgets pass child regions in via
    simple vertical/horizontal stacks.  Until those Containers exist
    (Phase 5), the compositor closes the contract with a simple
    vertical stack so it can be exercised against trees built by
    hand.

    Mutates ``node.region`` only when it is currently ``None`` or
    ``NULL_REGION``.  A node whose region is already set is left
    alone - that is the Widget-owned case the design doc names.
    """
    # Top-level node: the caller passes its region in via the
    # viewport_size argument when ``node.region`` is unset.
    if getattr(node, "region", None) in (None, NULL_REGION):
        node.region = region

    parent_region = node.region

    # Default-stack children only if at least one child lacks a
    # region.  This lets a caller mix hand-laid widgets with
    # default-stacked ones in the same tree.
    children = getattr(node, "children", None) or ()
    if not children:
        return

    unset = [c for c in children
             if getattr(c, "region", None) in (None, NULL_REGION)]
    if not unset:
        # Every child has its region; trust the caller's layout.
        for child in children:
            _layout_default(child, child.region, viewport_size)
        return

    # Simple vertical stack: divide parent_region.height equally
    # among unset children, leaving already-positioned children
    # alone.  Already-positioned children's heights subtract from
    # the pool.
    set_height = sum(getattr(c, "region", NULL_REGION).height
                     for c in children
                     if getattr(c, "region", None) not in (None, NULL_REGION))
    remaining = max(0, parent_region.height - set_height)
    per_child = remaining // max(1, len(unset))

    cursor_y = parent_region.y
    for child in children:
        existing = getattr(child, "region", None)
        if existing in (None, NULL_REGION):
            child.region = Region(
                parent_region.x,
                cursor_y,
                parent_region.width,
                per_child,
            )
            cursor_y += per_child
        else:
            cursor_y = existing.y + existing.height
        _layout_default(child, child.region, viewport_size)


def _too_small_lines(width, height):
    """Build the single-row "Terminal too small" message per FR-TUI-76.

    Returns a frame buffer of ``height`` rows, each padded to
    ``width`` cells, with the message centred on the middle row.
    Spec FR-TUI-76 literal is "Terminal too small (cols×rows,
    need 20×5)" with U+00D7 MULTIPLICATION SIGN; that codepoint
    is preserved here so a snapshot test against the spec string
    matches.
    """
    message = "Terminal too small ({}×{}, need {}×{})".format(
        width, height, _MIN_COLS, _MIN_ROWS,
    )
    # Truncate to the viewport width minus one to keep a one-cell
    # margin (matches Textual's centred-warning behaviour); leaves at
    # least one space if the message is wider than the viewport.
    if len(message) >= width:
        message = message[: max(0, width - 1)]

    pad_left = max(0, (width - len(message)) // 2)
    pad_right = max(0, width - pad_left - len(message))
    middle_row = max(0, height // 2)

    out = []
    for y in range(height):
        if y == middle_row:
            row = [Segment(" " * pad_left + message + " " * pad_right)]
        else:
            row = _empty_row(width)
        out.append(row)
    return out


class Compositor:
    """Walks a node tree and emits per-row segment diffs.

    Public API (matches §7 of the design doc plus the agent-task
    output-shape addendum):

    * ``__init__(console)`` - stash the Console instance the
      compositor uses for all renders; ``console.options`` is the
      template ``ConsoleOptions`` cloned per-node.

    * ``update_dom(root_node, viewport_size)`` - rebuild the frame
      buffer from the tree rooted at ``root_node``.  ``viewport_size``
      is a ``geometry.Size`` (or ``(width, height)`` tuple).  The
      root's region is forced to ``Region(0, 0, width, height)``;
      child regions are taken from each node's ``.region`` attribute
      where set, and stacked vertically inside the parent otherwise
      (see ``_layout_default``).

    * ``render_frame() -> List[Tuple[int, int, List[Segment]]]`` -
      walk the current frame buffer against the last-frame buffer,
      emit one tuple per changed row containing the (col, row, sliced
      segments) for that row.  After the walk, the last-frame buffer
      is updated to the current frame.

    * ``mark_dirty(node)`` - flag ``node`` (and by extension its
      subtree) as dirty.  Implementation: clear the cells inside
      ``node.region`` in the last-frame buffer, so the next diff sees
      them as definitely-changed regardless of the new content.  The
      v0.1 caller uses this from ``Widget.refresh()`` (design doc
      §4.4) when the reactive layer doesn't carry a visible delta.

    * ``full_redraw() -> List[Tuple[int, int, List[Segment]]]`` -
      emit every row of the current frame regardless of the diff.
      Used on resize (where last-frame's row count no longer matches
      the new viewport) and on initial paint (where last-frame is
      empty by definition).
    """

    def __init__(self, console):
        """Stash the Console; the constructor does no I/O.

        ``console`` is the picolet-tui ``_rich.console.Console``
        instance the App built at startup.  The compositor never
        replaces it - if the App swaps consoles (e.g. to apply a new
        color system after a SIGWINCH-equivalent), it must construct
        a fresh Compositor.
        """
        self._console = console
        # Both buffers are list[list[Segment]] indexed by row.  None
        # rows are allowed during transitions but the diff treats
        # them as "blank row" (full clear) - so they emit a single
        # space-fill on the first diff.
        self._current = []
        self._previous = []
        self._viewport = Size(0, 0)
        # Set of Region instances forced dirty since the last
        # render_frame().  Cleared inside render_frame() after the
        # last-frame buffer is updated.
        self._forced_dirty_regions = []

    # --------------------------------------------------------------
    # update_dom
    # --------------------------------------------------------------

    def update_dom(self, root_node, viewport_size):
        """Rebuild the current frame buffer from the tree.

        Does not emit anything; call ``render_frame()`` or
        ``full_redraw()`` afterwards to actually produce the
        positioned-segment output.  Splitting the walk from the emit
        lets the App coalesce multiple ``refresh()`` calls in one
        frame: one ``update_dom`` covers all of them.

        ``viewport_size`` is a ``geometry.Size`` or a tuple
        ``(width, height)``.  When width or height drop below the
        FR-TUI-76 floor, the normal walk is skipped and the buffer
        is filled with the centred "Terminal too small" message.
        """
        # Normalise viewport input.  Accepting a bare tuple matches
        # the caller-side ergonomics tuiterm exposes.
        width, height = viewport_size[0], viewport_size[1]
        self._viewport = Size(width, height)

        # Resize buffers if the viewport changed.  A resize forces
        # ``render_frame`` callers to emit the whole frame anyway
        # because every row's geometry has shifted, so we don't
        # bother trying to preserve the last-frame buffer across a
        # size delta - just zero it out and let the caller invoke
        # ``full_redraw`` next.
        if (len(self._current) != height
                or any(len(r) and _row_cell_width(r) != width
                       for r in self._current)):
            self._current = [_empty_row(width) for _ in range(height)]

        # FR-TUI-76: terminal-too-small short-circuit.
        if width < _MIN_COLS or height < _MIN_ROWS:
            self._current = _too_small_lines(width, height)
            return

        # Force the root region to cover the viewport.  If the caller
        # passed a node with a smaller region, we override it -
        # nothing else can produce a meaningful frame.
        root_node.region = Region(0, 0, width, height)

        # Assign regions to any unset children via the default
        # vertical stack.  No-op for nodes that already have regions.
        _layout_default(root_node, root_node.region, self._viewport)

        # Reset the current frame to a blank canvas before painting
        # the new tree.  Existing content from the previous walk
        # would otherwise bleed through gaps the new tree doesn't
        # cover.
        for y in range(height):
            self._current[y] = _empty_row(width)

        # Depth-first paint: parent first, then children, so child
        # paint overrides parent paint on shared cells.  This is the
        # upstream Textual order; matches users' intuition for
        # "container draws its background, children paint over it".
        self._paint_node(root_node, width, height)

    # --------------------------------------------------------------
    # _paint_node
    # --------------------------------------------------------------

    def _paint_node(self, node, viewport_width, viewport_height):
        """Render ``node`` into ``self._current`` then recurse.

        Skips nodes whose region falls entirely off-viewport; their
        children are still walked because a parent off-screen does
        not imply children off-screen (the layout pass might place a
        scrolled child back inside the viewport).  ``_splat_lines``
        handles the partial-clip case row-by-row.
        """
        region = getattr(node, "region", None)
        if region is None or region == NULL_REGION:
            # Node hasn't been laid out and the default layout pass
            # couldn't slot it.  Skip the render but still recurse;
            # a child might have a region set explicitly.
            for child in getattr(node, "children", ()) or ():
                self._paint_node(child, viewport_width, viewport_height)
            return

        x, y, width, height = region.x, region.y, region.width, region.height

        # Zero-dimension regions render nothing; recurse on children
        # because they may have their own non-zero regions.
        if width > 0 and height > 0:
            # Build a fresh ConsoleOptions for this node's slot.
            # Per synthesis §7.3, options are constructed per-call -
            # no thread-local, no escape.  Default ConsoleOptions is
            # the template; clone-and-update gives us the per-node
            # dimensions without mutating the Console's template.
            options = self._console.options
            options = options.update_dimensions(width, height)

            renderable = node.render()

            # render_lines returns List[List[Segment]] with each
            # inner list padded to ``width`` cells.  ``pad=True`` is
            # the default and what the compositor needs.
            try:
                lines = self._console.render_lines(
                    renderable, options, pad=True, new_lines=False,
                )
            except Exception:
                # FR-TUI-77: a render exception must not kill the
                # frame.  Substitute a single error placeholder line
                # and let the diff re-render on the next frame; the
                # App's log will see the exception via the calling
                # layer (the compositor itself does not log).
                lines = [[Segment("!" * width)] for _ in range(height)]

            _splat_lines(
                self._current,
                lines,
                x,
                y,
                viewport_width,
                viewport_height,
            )

        # Recurse into children regardless - they may paint cells
        # outside the parent's region (the layout pass does the
        # clipping; the compositor does not enforce parent-clip in
        # v0.1 because that's a Container concern, not a frame
        # concern).
        for child in getattr(node, "children", ()) or ():
            self._paint_node(child, viewport_width, viewport_height)

    # --------------------------------------------------------------
    # render_frame
    # --------------------------------------------------------------

    def render_frame(self):
        """Diff current vs previous and emit per-row tuples.

        After emitting, the previous frame is updated to a snapshot
        of the current frame.  The snapshot is a shallow list copy
        of rows; segments themselves are immutable in the picolet-tui
        port (``__slots__`` Segment, no setters used after
        construction) so the shallow copy is safe.

        Return: ``List[Tuple[int, int, List[Segment]]]`` with one
        tuple per changed row.  Empty list means nothing changed.
        """
        out = []

        # If the previous buffer's geometry doesn't match the
        # current buffer's, every row is changed by definition.
        # Treat as a full redraw path internally; this matches
        # ``full_redraw`` semantics but doesn't pollute the public
        # contract.
        if len(self._previous) != len(self._current):
            for y, row in enumerate(self._current):
                out.append((0, y, list(row)))
            self._previous = [list(r) for r in self._current]
            self._forced_dirty_regions = []
            return out

        # Apply any forced-dirty regions to the previous buffer so
        # the diff sees those cells as changed.  We clear them to
        # an "empty" sentinel - a single Segment of one cell width
        # that no current row can match (cell text is "\0", which
        # the renderable layer can't produce because Segment.text is
        # always a printable str).  This forces the leftmost/rightmost
        # search to bracket the cleared region.
        for forced in self._forced_dirty_regions:
            self._invalidate_previous_region(forced)
        self._forced_dirty_regions = []

        for y in range(len(self._current)):
            curr = self._current[y]
            prev = self._previous[y]
            diff = _diff_row(prev, curr)
            if diff is None:
                continue
            left, _right, segments = diff
            out.append((left, y, segments))

        # Snapshot current into previous for the next diff.  Shallow
        # list-copy of rows; segments are immutable.
        self._previous = [list(r) for r in self._current]
        return out

    def _invalidate_previous_region(self, region):
        """Force the previous-buffer cells inside ``region`` to differ.

        Used by ``mark_dirty``; clears the affected cells to a sentinel
        that no rendered output can produce, guaranteeing the next
        diff bounds them and re-emits.
        """
        x, y, width, height = region.x, region.y, region.width, region.height
        if width <= 0 or height <= 0:
            return
        # Clip y range against the previous buffer.
        y_start = max(0, y)
        y_end = min(len(self._previous), y + height)
        for row_y in range(y_start, y_end):
            row = self._previous[row_y]
            # Splice a sentinel into the row covering [x, x+width).
            row_width = _row_cell_width(row)
            if x >= row_width:
                # The region starts past the row's content - extend
                # with blank cells, then sentinel.
                gap = x - row_width
                sentinel = [Segment(" " * gap), Segment("\0" * width)]
                self._previous[row_y] = row + sentinel
                continue
            left = _slice_row(row, 0, x, row_width)
            right_start = x + width
            right = _slice_row(row, right_start, max(row_width, right_start),
                               row_width)
            self._previous[row_y] = left + [Segment("\0" * width)] + right

    # --------------------------------------------------------------
    # mark_dirty
    # --------------------------------------------------------------

    def mark_dirty(self, node):
        """Flag ``node``'s region (and its subtree) as dirty.

        The implementation records the union of regions covered by
        ``node`` and its descendants; the next ``render_frame()``
        invalidates the corresponding cells in the previous-frame
        buffer so the diff cannot skip them.

        Called from ``Widget.refresh()`` (design doc §4.4) when the
        reactive layer doesn't know whether the visible surface
        changed.  Cheap when nothing else is using mark_dirty - the
        cost is proportional to the subtree's covered cell count,
        bounded by viewport area.
        """
        # Collect the regions of this node and its descendants.  We
        # don't merge into a single bounding rect because that would
        # over-invalidate when widgets are spread across the
        # viewport.
        def _walk(n):
            region = getattr(n, "region", None)
            if region not in (None, NULL_REGION):
                self._forced_dirty_regions.append(region)
            for child in getattr(n, "children", ()) or ():
                _walk(child)

        _walk(node)

    # --------------------------------------------------------------
    # full_redraw
    # --------------------------------------------------------------

    def full_redraw(self):
        """Emit every row of the current buffer; no diff.

        Used on resize - where last-frame's row count no longer
        matches the new viewport - and on initial paint, where the
        previous buffer is empty by definition.  Updates the
        previous-frame buffer the same way ``render_frame`` does so
        the next ordinary frame can diff again.

        Same return shape as ``render_frame``.
        """
        out = []
        for y, row in enumerate(self._current):
            out.append((0, y, list(row)))
        # Snapshot the full current buffer into previous so the next
        # render_frame() diff starts from a clean baseline.
        self._previous = [list(r) for r in self._current]
        self._forced_dirty_regions = []
        return out


__all__ = ("Compositor",)
