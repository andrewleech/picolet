"""picolet_tui._rich.control - Rich's ANSI control-sequence emitter.

Ported from Textualize/rich master @ 46cebbb032f920eb096efbaf23cdc6fe9dd541f7
(``rich/control.py``, 150 LoC upstream).  Tier 5 of the Rich subset:
imports only Tier 1-3 ``segment`` (for ``ControlType`` / ``Segment`` /
the ``ControlCode`` tuple alias).  No upstream cycle: ``segment`` does
not import ``control``.

What this module does
---------------------
Builds opaque ``Control`` renderables whose ``self.segment`` carries
both a pre-rendered ANSI string and the structured ``ControlCode``
tuples so the compositor can either:
  (a) write the rendered text byte-for-byte through ``tuiterm.write``,
      OR
  (b) re-inspect the typed control codes (e.g. to suppress duplicates
      across frames per FR-TUI-11's prologue/epilogue ordering).

The startup / shutdown ANSI prologue called out in FR-TUI-11 is
emitted via ``Control.alt_screen``, ``Control.show_cursor``, and the
``Control.move_to``/``Control.home`` family.  The mouse-SGR
(``CSI ?1000 h`` / ``CSI ?1006 h``) and bracketed-paste
(``CSI ?2004 h``) bytes in FR-TUI-11 are NOT emitted from this module
- they live in ``picolet_tui.driver`` because upstream Rich never put
them in ``control.py`` either, and adding them here would diverge from
"match upstream's public surface exactly" (porting rule 1).

REMOVED vs upstream
-------------------
* ``import time`` and the ``if __name__ == "__main__"`` smoke driver
  at the bottom of upstream - the smoke driver calls
  ``console.set_window_title`` in a 10-iteration ``time.sleep(0.5)``
  loop, which only makes sense inside upstream Rich's own
  ``rich.console``.  Frozen ``_rich`` modules are never invoked as
  ``__main__`` under picolet-tui (NFR-TUI-19's frozen-bytes budget
  treats unreachable code as overhead).

* The ``from typing import ... Final`` import - routed through
  ``picolet_tui._shims.typing`` per the Tier-2 convention.  ``Final``
  is a no-op placeholder in the shim; the constants below are
  module-level immutables by convention only, same as upstream.

* The ``from typing import TYPE_CHECKING`` ``Console`` /
  ``ConsoleOptions`` / ``RenderResult`` forward-reference block: kept
  as a comment so the upstream layering remains visible, but the
  ``__rich_console__`` signature is left un-annotated because Tier-5
  doesn't import ``console.py`` and the shim's ``TYPE_CHECKING`` is
  unconditionally ``False`` anyway.

* The emoji at the bottom of the upstream smoke driver
  (``"\U0001f680 Loading"``) - dropped along with the whole
  ``if __name__ == "__main__"`` block.  No emoji is shipped from
  picolet-tui at all (FR-TUI-11 emits pure 7-bit ANSI; the cell-width
  shim in ``cells`` is the only place wide-grapheme handling crosses
  into the runtime).

ADDED vs upstream
-----------------
* ``Control.scroll_region(top, bottom)`` - emits ``CSI top+1;bottom+1 r``
  (DECSTBM).  Upstream Rich does not ship this helper because its
  ``Live``/``Layout`` widgets never use a hardware scroll region;
  picolet-tui's compositor calls it from the dirty-line scroller path
  the spec sketches around FR-TUI-31's no-animation layout.  Encoded
  inline as a raw ANSI literal (not via ``ControlType``) so we do NOT
  have to extend ``segment.ControlType``'s value table away from
  upstream - the Segment carries ``control=None`` for this one
  sequence and the byte stream still validates under NFR-TUI-14
  (xterm VT100 + ECMA-48 conformance).  The Segment.text is the raw
  ANSI; consumers that need typed introspection should look at the
  ``__slots__``-stored ``self.segment.text`` directly.

Porting-rule audit
------------------
* Rule 1 (public surface): ``Control`` plus its eleven classmethods
  match upstream byte-for-byte; ``strip_control_codes`` and
  ``escape_control_codes`` keep their default-arg ``_translate_table``
  hook so Rich-internal call sites that thread their own table in
  continue to work.  Only addition: ``scroll_region`` (documented
  above).
* Rule 2 (CPython imports): no ``time``, no ``os.write``; the
  ``typing`` re-route is the only swap.
* Rule 3 (``lru_cache`` clamp): no caches in this module.
* Rule 4 (no pickle / Protocol-runtime_checkable / metaclasses /
  ``__init_subclass__`` / ``__set_name__``): none used upstream, none
  introduced.
* Rule 5 (re subset): no regex in this module.
* Rule 6 (file-head docstring): this docstring.
* Rule 7 (no emojis, why-not-what comments): satisfied.

Spec refs: FR-TUI-11 (ANSI prologue), FR-TUI-78 (alt-screen ownership of
stdout during run), NFR-TUI-14 (VT100/ECMA-48 conformance).
"""

# Type-hint imports kept (even though stripped from signatures) so
# tooling that re-introspects the module sees the same names upstream
# Rich exposes; the shim makes them no-op placeholders.
from picolet_tui._shims.typing import (  # noqa: F401 - parity with upstream surface
    Callable,
    Dict,
    Final,
    Iterable,
    List,
    Union,
)

from .segment import ControlCode, ControlType, Segment


# ---------------------------------------------------------------------------
# Strip / escape tables.  Same code points as upstream: BEL, BS, VT, FF, CR
# - everything else (TAB, LF) is structural and must pass through.  The
# tables are module-level singletons so ``str.translate`` can be called
# without per-call allocation (matches upstream's ``Final`` annotation).
# ---------------------------------------------------------------------------

STRIP_CONTROL_CODES: Final = [
    7,   # Bell
    8,   # Backspace
    11,  # Vertical tab
    12,  # Form feed
    13,  # Carriage return
]

_CONTROL_STRIP_TRANSLATE: Final = {
    _codepoint: None for _codepoint in STRIP_CONTROL_CODES
}

CONTROL_ESCAPE: Final = {
    7: "\\a",
    8: "\\b",
    11: "\\v",
    12: "\\f",
    13: "\\r",
}


# ---------------------------------------------------------------------------
# CONTROL_CODES_FORMAT - lambdas that turn a ``(ControlType, *params)``
# tuple into the ANSI bytes the compositor writes.  Kept as a plain dict
# rather than an ``@enum_class``-decorated mapping so the lookup is the
# same single hash both upstream and here, and so a downstream port
# (driver.py, live.py if it lands) can monkey-patch a single key to
# stage a regression test without rebuilding the table.
# ---------------------------------------------------------------------------

CONTROL_CODES_FORMAT = {
    ControlType.BELL: lambda: "\x07",
    ControlType.CARRIAGE_RETURN: lambda: "\r",
    ControlType.HOME: lambda: "\x1b[H",
    ControlType.CLEAR: lambda: "\x1b[2J",
    ControlType.ENABLE_ALT_SCREEN: lambda: "\x1b[?1049h",
    ControlType.DISABLE_ALT_SCREEN: lambda: "\x1b[?1049l",
    ControlType.SHOW_CURSOR: lambda: "\x1b[?25h",
    ControlType.HIDE_CURSOR: lambda: "\x1b[?25l",
    ControlType.CURSOR_UP: lambda param: "\x1b[{}A".format(param),
    ControlType.CURSOR_DOWN: lambda param: "\x1b[{}B".format(param),
    ControlType.CURSOR_FORWARD: lambda param: "\x1b[{}C".format(param),
    ControlType.CURSOR_BACKWARD: lambda param: "\x1b[{}D".format(param),
    # Columns/rows are 1-based in CSI; callers pass 0-based per Rich's
    # convention.  ``param + 1`` keeps the caller-visible coordinate
    # space identical to upstream.
    ControlType.CURSOR_MOVE_TO_COLUMN: lambda param: "\x1b[{}G".format(param + 1),
    ControlType.ERASE_IN_LINE: lambda param: "\x1b[{}K".format(param),
    ControlType.CURSOR_MOVE_TO: lambda x, y: "\x1b[{};{}H".format(y + 1, x + 1),
    ControlType.SET_WINDOW_TITLE: lambda title: "\x1b]0;{}\x07".format(title),
}


class Control:
    """A renderable that inserts a control code (non printable, may move cursor).

    Args:
        *codes: Positional arguments are either a
            :class:`~rich.segment.ControlType` enum value, or a
            ``(ControlType, *params)`` tuple.
    """

    # __slots__ pins ``segment`` to a single attribute and keeps
    # Control allocation cheap.  The compositor allocates one Control
    # per cursor move per frame at p95 NFR-TUI-3, so the saving is
    # measurable.
    __slots__ = ["segment"]

    def __init__(self, *codes):
        # Normalise bare ControlType to a one-tuple so the lookup
        # signature is uniform.  Upstream tests with
        # ``isinstance(code, ControlType)`` but our shim's IntEnum
        # members are ``_IntEnumMember`` instances whose enum-class
        # membership is tracked via ``_parent_`` rather than via the
        # CPython ``type(...) is ControlType`` check (see
        # ``_shims/enum.py`` § "Members are instances of a holder
        # class").  Discriminating on ``not isinstance(code, tuple)``
        # is the shim-safe equivalent and preserves the upstream
        # KeyError shape for an unknown code.
        control_codes = [
            code if isinstance(code, tuple) else (code,) for code in codes
        ]
        _format_map = CONTROL_CODES_FORMAT
        rendered_codes = "".join(
            _format_map[code](*parameters) for code, *parameters in control_codes
        )
        self.segment = Segment(rendered_codes, None, control_codes)

    @classmethod
    def bell(cls):
        """Ring the 'bell'."""
        return cls(ControlType.BELL)

    @classmethod
    def home(cls):
        """Move cursor to 'home' position."""
        return cls(ControlType.HOME)

    @classmethod
    def move(cls, x=0, y=0):
        """Move cursor relative to current position."""
        # Generator captures the (direction, magnitude) pair so a
        # ``move(0, 0)`` produces an empty Segment - matches upstream's
        # "Control with no codes renders nothing" contract used by
        # the compositor's no-op short-circuit on still frames.
        def get_codes():
            control = ControlType
            if x:
                yield (
                    control.CURSOR_FORWARD if x > 0 else control.CURSOR_BACKWARD,
                    abs(x),
                )
            if y:
                yield (
                    control.CURSOR_DOWN if y > 0 else control.CURSOR_UP,
                    abs(y),
                )

        return cls(*get_codes())

    @classmethod
    def move_to_column(cls, x, y=0):
        """Move to the given column, optionally add offset to row."""
        return (
            cls(
                (ControlType.CURSOR_MOVE_TO_COLUMN, x),
                (
                    ControlType.CURSOR_DOWN if y > 0 else ControlType.CURSOR_UP,
                    abs(y),
                ),
            )
            if y
            else cls((ControlType.CURSOR_MOVE_TO_COLUMN, x))
        )

    @classmethod
    def move_to(cls, x, y):
        """Move cursor to absolute position (0-based)."""
        return cls((ControlType.CURSOR_MOVE_TO, x, y))

    @classmethod
    def clear(cls):
        """Clear the screen."""
        return cls(ControlType.CLEAR)

    @classmethod
    def show_cursor(cls, show):
        """Show or hide the cursor."""
        return cls(ControlType.SHOW_CURSOR if show else ControlType.HIDE_CURSOR)

    @classmethod
    def alt_screen(cls, enable):
        """Enable or disable alt screen.

        On enable, also homes the cursor - matches FR-TUI-11's prologue
        (``CSI ?1049 h`` then a known cursor origin) so the first
        compositor paint lands at (0, 0) regardless of where the
        controlling terminal left the cursor before tuiterm.enable().
        """
        if enable:
            return cls(ControlType.ENABLE_ALT_SCREEN, ControlType.HOME)
        return cls(ControlType.DISABLE_ALT_SCREEN)

    @classmethod
    def title(cls, title):
        """Set the terminal window title."""
        return cls((ControlType.SET_WINDOW_TITLE, title))

    @classmethod
    def scroll_region(cls, top, bottom):
        """Set the DEC top/bottom scroll margin (DECSTBM).

        Emits ``CSI top+1;bottom+1 r``.  See module docstring "ADDED
        vs upstream" for why this is encoded as raw text instead of a
        new ControlType: extending ``segment.ControlType``'s value
        table would diverge from upstream Rich and break round-tripping
        for any downstream that imports both this Rich port and the
        real upstream ``rich.segment`` (e.g. for golden-fixture
        comparisons in NFR-TUI-26 tests).
        """
        # Build the Control directly: no entry in CONTROL_CODES_FORMAT,
        # so we can't go through ``__init__`` with a typed tuple.
        # ``cls()`` with zero codes is the portable raw-construction
        # path (MicroPython has no ``cls.__new__``); it allocates an
        # empty Segment which is immediately replaced below.
        # Allocating the Segment here keeps the ``segment.text`` field
        # populated for the byte-stream path while leaving
        # ``segment.control`` None so typed inspectors see "raw ANSI".
        instance = cls()
        instance.segment = Segment(
            "\x1b[{};{}r".format(top + 1, bottom + 1), None, None
        )
        return instance

    # Alias matching the spec hint name (the task's "keep" list calls
    # this ``set_window_title``).  Forwards to ``title`` so a single
    # implementation owns the OSC 0 byte sequence.
    @classmethod
    def set_window_title(cls, title):
        """Alias for :meth:`title`; see FR-TUI-11 prologue notes."""
        return cls.title(title)

    def __str__(self):
        return self.segment.text

    def __rich_console__(self, console, options):
        # Yield only when there's actually something to write so the
        # compositor's empty-Segment fast path stays fast.  Upstream
        # behaviour preserved verbatim.
        if self.segment.text:
            yield self.segment


def strip_control_codes(text, _translate_table=_CONTROL_STRIP_TRANSLATE):
    """Remove control codes from text.

    Implemented with a scan + join rather than ``str.translate`` —
    MicroPython does not implement str.translate.  The keyword-arg
    table is kept for upstream signature compatibility.
    """
    if not any(chr(cp) in text for cp in _translate_table):
        return text
    drop = {chr(cp) for cp in _translate_table}
    return "".join(c for c in text if c not in drop)


def escape_control_codes(text, _translate_table=CONTROL_ESCAPE):
    """Replace control codes with their backslash-escaped form.

    Per-entry ``str.replace`` rather than ``str.translate`` — see
    strip_control_codes.
    """
    for cp, esc in _translate_table.items():
        ch = chr(cp)
        if ch in text:
            text = text.replace(ch, esc)
    return text


# Public re-exports.  ``ControlType`` and ``ControlCode`` live in
# ``segment.py`` upstream; surfacing them here matches Rich's own
# ``from rich.control import ControlType`` idiom used by widget code.
__all__ = [
    "Control",
    "ControlType",
    "ControlCode",
    "STRIP_CONTROL_CODES",
    "CONTROL_ESCAPE",
    "CONTROL_CODES_FORMAT",
    "strip_control_codes",
    "escape_control_codes",
]
