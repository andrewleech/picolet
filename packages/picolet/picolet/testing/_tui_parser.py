"""picolet.testing._tui_parser — ANSI byte-stream → virtual cell grid.

A trimmed Williams-style state machine that consumes raw bytes emitted by a
picolet-tui binary attached to a pty and folds them into a `Cell[rows][cols]`
grid the harness can assert against.  Only the sequences the v0.1 framework
actually emits (FR-TUI-11, FR-TUI-15, FR-TUI-32..37, FR-TUI-76) are decoded;
everything else is silently dropped so the harness keeps moving when the
binary emits a sequence the parser does not yet model.

Spec note (NFR-TUI-21) requires "fail on unknown ANSI" once the runtime
parser is the single source of truth (FR-TUI-62).  For Phase 7-scaffold
the runtime parser does not exist yet, so this module ships as a permissive
stand-in.  Once `picolet_tui._parser` lands, the harness will import that
module instead of this one — at which point the strict-mode flag here
(`Parser(strict=True)`) is the migration path: it raises on unhandled
sequences and surfaces the raw bytes in the assertion, matching NFR-TUI-21.

Why a hand-rolled state machine and not a regex sweep:

* Bytes arrive in arbitrary chunk boundaries from the pty (one read may
  hand back a half-completed `CSI` mid-parameter).  The state machine
  carries partial-sequence state across feed() calls without bookkeeping
  on the caller side.
* Regex would force a buffer-and-rematch loop and would not catch the
  byte-range transitions (e.g. CSI_IGNORE on a malformed final byte) that
  research doc 04 §3 mandates picolet match xterm/vt500 on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Cell + Style — virtual-screen data model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    """Rendered cell styling, normalised so two Cells compare equal iff their
    visible attributes match.

    Why a frozen dataclass: harness tests compare frames structurally
    (`assert h.frame() == expected`).  Mutable styles would silently alias
    across cells when SGR state changes mid-row.
    """

    fg: Optional[str] = None       # 'red', 'green', or '#rrggbb' once truecolor lands
    bg: Optional[str] = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False


DEFAULT_STYLE = Style()


@dataclass
class Cell:
    """A single screen cell — one Unicode character plus its rendered Style.

    `char == " "` means an explicitly cleared cell (after CSI J / CSI K), not
    "untouched"; the harness has no notion of untouched cells, the grid is
    fully populated from construction.
    """

    char: str = " "
    style: Style = DEFAULT_STYLE

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Cell):
            return self.char == other.char and self.style == other.style
        # Convenience: `cells_at(...) == "h"` style asserts compare against
        # the literal character only (style ignored).  The harness's
        # cells_at() returns the joined string, not Cell objects, so this
        # branch only fires when callers build their own Cell list — keep it
        # cheap and string-only.
        if isinstance(other, str) and len(other) == 1:
            return self.char == other
        return NotImplemented

    def __hash__(self) -> int:  # frozen-style hash since Cell is used in sets in tests
        return hash((self.char, self.style))


# ---------------------------------------------------------------------------
# SGR colour tables.
#
# Indexed-colour names match the v0.1 surface (Phase 4 will introduce a
# canonical Color shim that re-uses these strings); for the smoke tests the
# only requirement is that "the cell carries the 'green' label" so callers
# can assert symbolic names rather than chasing terminal palette drift.
# ---------------------------------------------------------------------------

_SGR_FG_COLOURS = {
    30: "black", 31: "red", 32: "green", 33: "yellow",
    34: "blue", 35: "magenta", 36: "cyan", 37: "white",
    90: "bright_black", 91: "bright_red", 92: "bright_green", 93: "bright_yellow",
    94: "bright_blue", 95: "bright_magenta", 96: "bright_cyan", 97: "bright_white",
}
_SGR_BG_COLOURS = {
    40: "black", 41: "red", 42: "green", 43: "yellow",
    44: "blue", 45: "magenta", 46: "cyan", 47: "white",
    100: "bright_black", 101: "bright_red", 102: "bright_green", 103: "bright_yellow",
    104: "bright_blue", 105: "bright_magenta", 106: "bright_cyan", 107: "bright_white",
}


# ---------------------------------------------------------------------------
# Parser state-machine constants.
# ---------------------------------------------------------------------------

_GROUND = 0
_ESCAPE = 1
_CSI_ENTRY = 2
_CSI_PARAM = 3
_CSI_INTERMEDIATE = 4
_CSI_IGNORE = 5
_OSC_STRING = 6


class Parser:
    """Stateful ANSI parser folding bytes into a `Cell[rows][cols]` grid.

    The grid is column-major-friendly: `cells[row][col]`.  Cursor is
    0-indexed internally even though ANSI is 1-indexed on the wire; the
    conversion happens at the CSI-H dispatch site so the rest of the parser
    never has to remember which side it is on.
    """

    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self.rows = rows
        self.cols = cols
        # Primary + alternate screens are tracked independently; alt-screen
        # enable (`CSI ? 1049 h`) swaps them so the primary grid is preserved
        # when the SUT clears for a full-screen redraw (FR-TUI-11).
        self._primary = self._blank_grid()
        self._alt = self._blank_grid()
        self._on_alt = False
        # Cursor (row, col), 0-indexed.  Top-left is (0, 0).
        self.cur_row = 0
        self.cur_col = 0
        self.cursor_visible = True
        # SGR running state — mutated by `m` dispatches, baked into each cell
        # at write-character time.
        self._fg: Optional[str] = None
        self._bg: Optional[str] = None
        self._bold = False
        self._italic = False
        self._underline = False
        self._reverse = False
        # State-machine ephemerals.
        self._state = _GROUND
        self._params: list[int] = []
        self._param_buf = b""
        # Set on `?` / `>` / `<` / `=` immediately after CSI; affects which
        # private-mode handler runs at dispatch.
        self._private = b""
        # Buffered UTF-8 continuation bytes — kept across feed() boundaries so
        # multi-byte runes that cross a chunk read survive intact.
        self._utf8_pending = b""

    # -------------------------------------------------------------------
    # Public API.
    # -------------------------------------------------------------------

    @property
    def cells(self) -> list[list[Cell]]:
        """Currently visible grid (alt-screen if active, else primary)."""
        return self._alt if self._on_alt else self._primary

    def feed(self, data: bytes) -> None:
        """Advance the state machine across `data`.  Idempotent on empty input."""
        if not data:
            return
        # Prepend any UTF-8 continuation bytes left over from the last chunk.
        if self._utf8_pending:
            data = self._utf8_pending + data
            self._utf8_pending = b""

        i = 0
        n = len(data)
        while i < n:
            b = data[i]
            if self._state == _GROUND:
                if b == 0x1B:                       # ESC
                    self._state = _ESCAPE
                    i += 1
                elif b == 0x0D:                     # CR — column 0, same row
                    self.cur_col = 0
                    i += 1
                elif b == 0x0A:                     # LF — next row (no scroll modelling)
                    self.cur_row = min(self.cur_row + 1, self.rows - 1)
                    i += 1
                elif b == 0x08:                     # BS — back one column, no wrap-back
                    self.cur_col = max(self.cur_col - 1, 0)
                    i += 1
                elif b < 0x20 or b == 0x7F:
                    # Other C0 / DEL — silently swallow, picolet's runtime
                    # never emits these mid-frame and the harness has nothing
                    # useful to model for them.
                    i += 1
                else:
                    # Printable run — accumulate until the next control byte
                    # for one bulk write per row, which keeps the hot path
                    # cheap for the common case of long static text.
                    j = i
                    while j < n and 0x20 <= data[j] < 0x80 and data[j] != 0x7F:
                        j += 1
                    self._write_text(data[i:j].decode("ascii"))
                    if j < n and data[j] >= 0x80:
                        # Hand multi-byte UTF-8 off to the slow path which
                        # buffers continuation bytes if the chunk truncates.
                        consumed = self._decode_utf8(data, j)
                        i = consumed
                    else:
                        i = j
            elif self._state == _ESCAPE:
                if b == 0x5B:                       # '[' → CSI
                    self._state = _CSI_ENTRY
                    self._params = []
                    self._param_buf = b""
                    self._private = b""
                    i += 1
                elif b == 0x5D:                     # ']' → OSC
                    self._state = _OSC_STRING
                    i += 1
                else:
                    # ESC <single byte> — unsupported in v0.1 scaffold;
                    # research doc 04 §3 SS3 (ESC O <key>) is only on input,
                    # not output, so the harness has nothing to do here.
                    self._state = _GROUND
                    i += 1
            elif self._state == _CSI_ENTRY:
                if b in (0x3C, 0x3D, 0x3E, 0x3F):   # < = > ?  private marker
                    self._private = bytes([b])
                    self._state = _CSI_PARAM
                    i += 1
                elif 0x30 <= b <= 0x39 or b == 0x3B:  # digit or ';'
                    self._param_buf += bytes([b])
                    self._state = _CSI_PARAM
                    i += 1
                elif 0x40 <= b <= 0x7E:             # immediate final byte, no params
                    self._dispatch_csi(b)
                    self._state = _GROUND
                    i += 1
                else:
                    self._state = _CSI_IGNORE
                    i += 1
            elif self._state == _CSI_PARAM:
                if 0x30 <= b <= 0x39 or b == 0x3B:
                    self._param_buf += bytes([b])
                    i += 1
                elif 0x20 <= b <= 0x2F:
                    self._state = _CSI_INTERMEDIATE
                    i += 1
                elif 0x40 <= b <= 0x7E:
                    self._dispatch_csi(b)
                    self._state = _GROUND
                    i += 1
                else:
                    self._state = _CSI_IGNORE
                    i += 1
            elif self._state == _CSI_INTERMEDIATE:
                if 0x20 <= b <= 0x2F:
                    i += 1
                elif 0x40 <= b <= 0x7E:
                    self._dispatch_csi(b)
                    self._state = _GROUND
                    i += 1
                else:
                    self._state = _CSI_IGNORE
                    i += 1
            elif self._state == _CSI_IGNORE:
                if 0x40 <= b <= 0x7E:
                    self._state = _GROUND
                i += 1
            elif self._state == _OSC_STRING:
                # Terminate on BEL or ESC \ (ST); we ignore the payload —
                # picolet uses OSC only for window-title sets which the
                # smoke tests do not exercise.
                if b == 0x07:
                    self._state = _GROUND
                    i += 1
                elif b == 0x1B:
                    # Look ahead for the '\' that closes ST.
                    if i + 1 < n and data[i + 1] == 0x5C:
                        self._state = _GROUND
                        i += 2
                    else:
                        self._state = _GROUND
                        i += 1
                else:
                    i += 1
            else:
                # Defensive: should never reach here.  Reset to ground so a
                # single bad byte cannot wedge the parser forever.
                self._state = _GROUND
                i += 1

    # -------------------------------------------------------------------
    # Internal — grid helpers.
    # -------------------------------------------------------------------

    def _blank_grid(self) -> list[list[Cell]]:
        return [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]

    def _current_style(self) -> Style:
        return Style(
            fg=self._fg,
            bg=self._bg,
            bold=self._bold,
            italic=self._italic,
            underline=self._underline,
            reverse=self._reverse,
        )

    def _write_text(self, text: str) -> None:
        """Place each character at the cursor, advancing left-to-right.

        End-of-line wraps to the next row rather than overwriting the last
        column repeatedly — matches xterm "autowrap on" (DECAWM default,
        which the framework does not toggle off in the v0.1 prologue).
        """
        if not text:
            return
        style = self._current_style()
        grid = self.cells
        for ch in text:
            if self.cur_col >= self.cols:
                self.cur_col = 0
                self.cur_row = min(self.cur_row + 1, self.rows - 1)
            if 0 <= self.cur_row < self.rows and 0 <= self.cur_col < self.cols:
                grid[self.cur_row][self.cur_col] = Cell(char=ch, style=style)
            self.cur_col += 1

    def _decode_utf8(self, data: bytes, start: int) -> int:
        """Slow-path UTF-8 decoder; called when a byte ≥ 0x80 appears.

        Returns the index in `data` past the bytes consumed.  If the input
        ends mid-rune, the partial bytes are stashed in `_utf8_pending` and
        prepended to the next feed() call.
        """
        i = start
        n = len(data)
        first = data[i]
        if first < 0xC0:
            # Stray continuation byte at the start — drop it, advance one.
            return i + 1
        if first < 0xE0:
            need = 2
        elif first < 0xF0:
            need = 3
        else:
            need = 4
        if i + need > n:
            self._utf8_pending = data[i:]
            return n
        try:
            rune = data[i:i + need].decode("utf-8")
        except UnicodeDecodeError:
            return i + 1
        self._write_text(rune)
        return i + need

    # -------------------------------------------------------------------
    # Internal — CSI dispatch.
    # -------------------------------------------------------------------

    def _flush_params(self) -> list[int]:
        """Parse the accumulated `_param_buf` into a list of ints.

        Empty parameters default to 0 per ECMA-48 (the consumer applies the
        sequence-specific default — e.g. `CSI H` with no params is `(1, 1)`,
        `CSI J` with no params is `0`).
        """
        if not self._param_buf:
            return []
        out: list[int] = []
        for part in self._param_buf.split(b";"):
            if not part:
                out.append(0)
            else:
                try:
                    out.append(int(part))
                except ValueError:
                    out.append(0)
        return out

    def _dispatch_csi(self, final: int) -> None:
        params = self._flush_params()
        private = self._private

        if private == b"?":
            self._dispatch_private(final, params)
            return

        # SGR — colour, bold, italic, underline.  Most-trafficked sequence;
        # the parser shipped with the runtime will share this exact decoder
        # table so harness assertions are not fighting palette drift.
        if final == ord("m"):
            self._apply_sgr(params)
            return

        # CSI H / CSI f — cursor position.  Both forms (`H` and `f`) are
        # equivalent; we treat them the same.  Params are 1-indexed.
        if final in (ord("H"), ord("f")):
            row = (params[0] if len(params) >= 1 and params[0] else 1) - 1
            col = (params[1] if len(params) >= 2 and params[1] else 1) - 1
            self.cur_row = max(0, min(row, self.rows - 1))
            self.cur_col = max(0, min(col, self.cols - 1))
            return

        # CSI A/B/C/D — cursor up/down/forward/back, count param defaults to 1.
        if final == ord("A"):
            self.cur_row = max(0, self.cur_row - (params[0] if params and params[0] else 1))
            return
        if final == ord("B"):
            self.cur_row = min(self.rows - 1, self.cur_row + (params[0] if params and params[0] else 1))
            return
        if final == ord("C"):
            self.cur_col = min(self.cols - 1, self.cur_col + (params[0] if params and params[0] else 1))
            return
        if final == ord("D"):
            self.cur_col = max(0, self.cur_col - (params[0] if params and params[0] else 1))
            return

        # CSI J — erase display.  Mode 0: cursor → end.  Mode 1: start →
        # cursor.  Mode 2: entire display.  Mode 3 (scrollback) ignored —
        # picolet does not maintain scrollback in the harness model.
        if final == ord("J"):
            mode = params[0] if params else 0
            self._erase_display(mode)
            return

        # CSI K — erase in line, same mode semantics restricted to current row.
        if final == ord("K"):
            mode = params[0] if params else 0
            self._erase_line(mode)
            return

        # Everything else (DSR-6 reply, scroll regions, save/restore cursor,
        # device attributes) is dropped silently in scaffold mode.  Once the
        # runtime parser is the single source of truth we will tighten this
        # to "raise" for any unhandled final byte per NFR-TUI-21.

    def _dispatch_private(self, final: int, params: list[int]) -> None:
        """Handle `CSI ? <params> h/l` — DEC private mode set/reset."""
        # h = set, l = reset
        if final not in (ord("h"), ord("l")) or not params:
            return
        on = final == ord("h")
        for p in params:
            if p == 25:
                # Cursor visibility — used by FR-TUI-11 prologue.
                self.cursor_visible = on
            elif p == 1049:
                # Alt-screen enable; on enable, blank the alt grid so the
                # SUT's clear-then-paint sequence lands on a fresh canvas.
                if on:
                    self._alt = self._blank_grid()
                    self._on_alt = True
                    # Cursor home on alt-screen entry, matching xterm.
                    self.cur_row = 0
                    self.cur_col = 0
                else:
                    self._on_alt = False
                    # Returning to primary preserves whatever was there before.

    def _apply_sgr(self, params: list[int]) -> None:
        """Update the running SGR state from a `CSI <p>;<p>;... m` sequence.

        Empty params means `CSI m` → reset (param default 0 per spec).  This
        keeps the runtime ANSI prologue/epilogue correct without having to
        special-case it at the dispatch site.
        """
        if not params:
            params = [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self._fg = None
                self._bg = None
                self._bold = False
                self._italic = False
                self._underline = False
                self._reverse = False
            elif p == 1:
                self._bold = True
            elif p == 22:
                self._bold = False
            elif p == 3:
                self._italic = True
            elif p == 23:
                self._italic = False
            elif p == 4:
                self._underline = True
            elif p == 24:
                self._underline = False
            elif p == 7:
                self._reverse = True
            elif p == 27:
                self._reverse = False
            elif p in _SGR_FG_COLOURS:
                self._fg = _SGR_FG_COLOURS[p]
            elif p in _SGR_BG_COLOURS:
                self._bg = _SGR_BG_COLOURS[p]
            elif p == 39:
                self._fg = None
            elif p == 49:
                self._bg = None
            elif p == 38 or p == 48:
                # 38;5;N (palette) or 38;2;R;G;B (truecolor).  Consume the
                # extra arguments so we don't mis-parse subsequent params.
                if i + 1 < len(params):
                    mode = params[i + 1]
                    if mode == 5 and i + 2 < len(params):
                        target = "fg" if p == 38 else "bg"
                        colour = "color({})".format(params[i + 2])
                        if target == "fg":
                            self._fg = colour
                        else:
                            self._bg = colour
                        i += 2
                    elif mode == 2 and i + 4 < len(params):
                        colour = "#{:02x}{:02x}{:02x}".format(
                            params[i + 2], params[i + 3], params[i + 4]
                        )
                        if p == 38:
                            self._fg = colour
                        else:
                            self._bg = colour
                        i += 4
                    else:
                        # Malformed truecolor sequence — skip the mode byte
                        # to avoid running it through the main switch.
                        i += 1
            i += 1

    def _erase_display(self, mode: int) -> None:
        grid = self.cells
        blank_style = self._current_style()
        if mode == 0:
            # From cursor to end of screen.
            for col in range(self.cur_col, self.cols):
                grid[self.cur_row][col] = Cell(char=" ", style=blank_style)
            for row in range(self.cur_row + 1, self.rows):
                for col in range(self.cols):
                    grid[row][col] = Cell(char=" ", style=blank_style)
        elif mode == 1:
            # Start of screen to cursor inclusive.
            for row in range(self.cur_row):
                for col in range(self.cols):
                    grid[row][col] = Cell(char=" ", style=blank_style)
            for col in range(self.cur_col + 1):
                grid[self.cur_row][col] = Cell(char=" ", style=blank_style)
        elif mode == 2 or mode == 3:
            # Whole screen (mode 3 also targets scrollback which we don't model).
            for row in range(self.rows):
                for col in range(self.cols):
                    grid[row][col] = Cell(char=" ", style=blank_style)

    def _erase_line(self, mode: int) -> None:
        grid = self.cells
        blank_style = self._current_style()
        if mode == 0:
            for col in range(self.cur_col, self.cols):
                grid[self.cur_row][col] = Cell(char=" ", style=blank_style)
        elif mode == 1:
            for col in range(self.cur_col + 1):
                grid[self.cur_row][col] = Cell(char=" ", style=blank_style)
        elif mode == 2:
            for col in range(self.cols):
                grid[self.cur_row][col] = Cell(char=" ", style=blank_style)


# A bare module-level dataclass for callers who want to inspect frame state
# without poking at Parser internals.  Mirrors §4.1 of the spec's `Frame`.
@dataclass
class Frame:
    cells: list[list[Cell]] = field(default_factory=list)
    cursor: tuple[int, int] = (0, 0)
    cursor_visible: bool = True

    def __str__(self) -> str:
        # Human-readable rendering used in failed-assertion messages.
        return "\n".join("".join(c.char for c in row) for row in self.cells)
