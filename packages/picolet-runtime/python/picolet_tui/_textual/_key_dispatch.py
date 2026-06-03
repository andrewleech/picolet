"""picolet_tui._textual._key_dispatch - byte-stream parser + key dispatch.

Phase 4b/10. Implements the byte-stream-to-Event parser that consumes
``tuiterm.read_input`` output, plus the focused-widget binding walk that
turns a Key event into an action invocation.

Spec coverage:
  * FR-TUI-15 - ``events.Key(key, character, modifiers)`` decoded from
    research doc 04 \xa73's xterm/vt500 byte state machine.
  * FR-TUI-16 - SGR mouse decoding (``CSI < Cb ; Cx ; Cy M/m``).
  * FR-TUI-17 - bracketed paste: bytes between the ``200~`` and ``201~``
    markers MUST NOT be interpreted as keys (this is the security-style
    requirement that prevents pasted ESC sequences firing commands).
  * FR-TUI-18 - xterm modifier encoding (``CSI 1 ; N <final>`` where
    ``N = 1 + Shift + Alt*2 + Ctrl*4``) folded into ``Key.modifiers``.
  * FR-TUI-27 - ``BINDINGS`` walked by ``dispatch_key`` per design doc
    \xa76.3.

Design-doc references (textual-core-design.md):
  * \xa76.3 - dispatch walks focused -> ancestors -> app, calling
    ``getattr(node, "action_" + binding.action)()`` on first match.

Module split rationale:
  * The byte parser is a leaf - it depends only on ``Message`` (for the
    Event base classes), the ``keys`` table (for CSI / SS3 finals), and
    nothing in the widget hierarchy.  Keeping it out of ``message_pump``
    avoids a cycle: the App's ``_pump_input`` task imports both
    ``MessagePump`` (via Widget) and the parser, but the parser does not
    need MessagePump.
  * Dispatch lives here rather than in ``binding`` because dispatch
    needs to know about ``app`` and ``app.focused`` (the App object
    lands later in Phase 4b); ``binding`` is a leaf value type.

Parser shape (research doc 04 \xa73):
  GROUND -> ESCAPE -> CSI_ENTRY -> CSI_PARAM -> CSI_FINAL
         \\-> SS3 -> SS3_FINAL
         \\-> PASTE (between ``\\x1b[200~`` and ``\\x1b[201~``)

The parser is bytes-in / events-out and carries no global state.  Each
call to ``parse_input_bytes`` constructs a fresh ``_Parser`` instance,
runs it to completion, and discards it.  A future streaming variant
(``Parser`` as a long-lived object across reads) is an obvious upgrade
but unnecessary for v0.1: ``tuiterm.read_input`` returns full sequences
in practice because the underlying ``read(2)`` coalesces the burst the
terminal sent in response to one keystroke.  If we ever observe split
sequences in the wild, the fix is to retain ``_buf`` and ``_state``
between calls; the surface ``parse_input_bytes(bytes) -> list[Event]``
does not change.

MicroPython adjustments (from a hypothetical upstream parser):
  * No ``__future__`` annotations, no ``typing`` imports.
  * Byte literals everywhere - MicroPython's ``bytes.find`` is the same
    C-level scan as CPython's, so the bracketed-paste open/close
    detection runs at compiled-C speed.
  * No ``re`` import; the CSI parameter splitter is a manual loop over
    ASCII digits.  ``re`` is in the picolet shims but the byte-level
    parser is a hot path and a regex compile per call would dominate.
"""

# Message base for all our event types.  Bringing it in directly rather
# than via a re-export means this module has no dependency on the
# higher-level ``messages`` aggregate, which lands later in Phase 4b.
from .message import Message

# The Keys table - we read it for CSI / SS3 final-byte -> key-name maps.
# Importing the module not the class so we can do data lookups against
# the canonical strings without re-stating them here.
from . import keys as _keys


# ---------------------------------------------------------------------
# Event classes.
#
# Three top-level Event classes (Key, Mouse, Resize) plus the spec's
# Paste and the FR-TUI-16 Mouse subtypes.  Each is a plain Message
# subclass - per the message module docstring, plain data-carrying
# Message subclasses do NOT need @widget because @widget scans the
# handler-owning class (the receiver), not the message class itself.
# ---------------------------------------------------------------------


class Key(Message):
    """Key-press event (FR-TUI-15).

    Attributes:
      * ``key`` - the canonical key name string from
        ``picolet_tui._textual.keys.Keys`` (e.g. ``"ctrl+a"``, ``"up"``,
        ``"f5"``, ``"a"``).  Bindings match against this exact string.
      * ``character`` - the printable character the key produced, or
        ``None`` for non-printable keys.  Used by ``Input`` widgets to
        avoid re-decoding the key name back into a character.
      * ``modifiers`` - a set of modifier names (``"shift"``, ``"ctrl"``,
        ``"alt"``).  Empty set when the key has no modifiers separately
        encoded (the modifier-prefixed key forms like ``"ctrl+a"`` keep
        the modifier in ``.key`` for backwards compatibility with
        upstream Textual's binding strings - the ``modifiers`` set is
        the FR-TUI-18 xterm-encoded form for the cursor / function keys
        where the modifier is sent as a CSI parameter rather than baked
        into the key name).
    """

    def __init__(self, key, character=None, modifiers=None):
        # Always call Message.__init__ so _stop_bubble / _sender are
        # populated; otherwise the dispatch loop would AttributeError.
        Message.__init__(self)
        self.key = key
        self.character = character
        # Default-empty set rather than None so user handlers can do
        # ``"ctrl" in event.modifiers`` without a None guard.
        self.modifiers = set() if modifiers is None else set(modifiers)


class Mouse(Message):
    """Base class for mouse events (FR-TUI-16).

    The spec calls for distinct ``MouseDown`` / ``MouseUp`` / ``MouseMove``
    / ``MouseScrollUp`` / ``MouseScrollDown`` subtypes; this base lets
    widgets register one ``@on(Mouse)`` handler that catches all of
    them, while still allowing fine-grained per-subtype handlers.

    Attributes:
      * ``x``, ``y`` - 1-based screen coordinates from the SGR sequence.
      * ``button`` - button index (0=left, 1=middle, 2=right) or None
        for motion-only events.
      * ``modifiers`` - set of modifier names derived from Cb bits 4/8/16
        (shift / alt / ctrl).  Same shape as ``Key.modifiers``.
    """

    def __init__(self, x, y, button=None, modifiers=None):
        Message.__init__(self)
        self.x = x
        self.y = y
        self.button = button
        self.modifiers = set() if modifiers is None else set(modifiers)


class MouseDown(Mouse):
    """Mouse button press (FR-TUI-16)."""


class MouseUp(Mouse):
    """Mouse button release (FR-TUI-16)."""


class MouseMove(Mouse):
    """Pointer motion (with or without a button held)."""


class MouseScrollUp(Mouse):
    """Wheel scroll up (Cb bit 6 set, low bit 0)."""


class MouseScrollDown(Mouse):
    """Wheel scroll down (Cb bit 6 set, low bit 1)."""


class Resize(Message):
    """Terminal-size change (FR-TUI-9).

    Not produced by the byte parser - the App's ``_pump_resize`` task
    posts this directly from ``tuiterm.size()`` polling.  Kept in this
    module because it sits alongside Key/Mouse in the dispatch surface.
    """

    def __init__(self, cols, rows):
        Message.__init__(self)
        self.cols = cols
        self.rows = rows


class Paste(Message):
    """Bracketed-paste payload (FR-TUI-17).

    Bytes between ``CSI 200 ~`` and ``CSI 201 ~`` are collected verbatim
    and emitted as one Paste event regardless of payload size.  The
    parser MUST NOT interpret bytes inside the markers - that is the
    security-style requirement that prevents pasted ESC sequences
    firing application commands.
    """

    def __init__(self, text):
        Message.__init__(self)
        self.text = text


# ---------------------------------------------------------------------
# Byte tables.
#
# Mapping CSI / SS3 final bytes to canonical key names.  Kept module-
# private because the canonical names live in the ``keys`` module and
# this is just the inverse-lookup table the parser needs.
# ---------------------------------------------------------------------


# CSI final byte -> key name.  Built from research doc 04 \xa73's key
# table; values match ``Keys.*`` strings.
_CSI_FINAL_KEYS = {
    b"A": _keys.Keys.Up,
    b"B": _keys.Keys.Down,
    b"C": _keys.Keys.Right,
    b"D": _keys.Keys.Left,
    b"F": _keys.Keys.End,
    b"H": _keys.Keys.Home,
    b"Z": _keys.Keys.BackTab,         # shift+tab
}

# CSI with a tilde finaliser - parameter selects the key.  ``CSI N ~``
# where N picks from this table.  The tilde-finals are how xterm encodes
# the legacy DEC function keys.
_CSI_TILDE_KEYS = {
    1: _keys.Keys.Home,           # xterm "Home" alternate
    2: _keys.Keys.Insert,
    3: _keys.Keys.Delete,
    4: _keys.Keys.End,            # xterm "End" alternate
    5: _keys.Keys.PageUp,
    6: _keys.Keys.PageDown,
    7: _keys.Keys.Home,           # rxvt "Home"
    8: _keys.Keys.End,            # rxvt "End"
    11: _keys.Keys.F1,
    12: _keys.Keys.F2,
    13: _keys.Keys.F3,
    14: _keys.Keys.F4,
    15: _keys.Keys.F5,
    17: _keys.Keys.F6,
    18: _keys.Keys.F7,
    19: _keys.Keys.F8,
    20: _keys.Keys.F9,
    21: _keys.Keys.F10,
    23: _keys.Keys.F11,
    24: _keys.Keys.F12,
}

# SS3 (ESC O) final byte -> key name.  Used by terminals in application
# cursor-key mode and for F1-F4 on some terminals.
_SS3_FINAL_KEYS = {
    b"A": _keys.Keys.Up,
    b"B": _keys.Keys.Down,
    b"C": _keys.Keys.Right,
    b"D": _keys.Keys.Left,
    b"F": _keys.Keys.End,
    b"H": _keys.Keys.Home,
    b"P": _keys.Keys.F1,
    b"Q": _keys.Keys.F2,
    b"R": _keys.Keys.F3,
    b"S": _keys.Keys.F4,
}

# Control-byte -> Keys.Control{X} name.  The 26 letters plus the few
# named control codes (tab, enter, escape, backspace) the keys table
# canonicalises.
#
# Note: \x00 is Ctrl-@ / Ctrl-Space - both spellings are valid bindings
# (Keys.ControlAt aliases to "ctrl-at" and Keys.ControlSpace).  We emit
# "ctrl+@" because that is the canonical form Keys.ControlAt holds.
_CONTROL_BYTE_KEYS = {
    0x00: _keys.Keys.ControlAt,
    0x08: _keys.Keys.Backspace,    # Ctrl-H / BS
    0x09: _keys.Keys.Tab,          # Ctrl-I / HT
    0x0A: _keys.Keys.ControlJ,     # LF (also aliased to "newline")
    0x0D: _keys.Keys.Enter,        # Ctrl-M / CR
    0x1B: _keys.Keys.Escape,       # ESC
    0x1C: _keys.Keys.ControlBackslash,
    0x1D: _keys.Keys.ControlSquareClose,
    0x1E: _keys.Keys.ControlCircumflex,
    0x1F: _keys.Keys.ControlUnderscore,
    0x7F: _keys.Keys.Backspace,    # DEL - what most terminals send for BS
}


# Bracketed-paste sentinels.  Module constants because both the parser
# fast path and the streaming-state machine reference them; keeping the
# literal in one place avoids a typo where the open/close drift apart.
_PASTE_OPEN = b"\x1b[200~"
_PASTE_CLOSE = b"\x1b[201~"


# ---------------------------------------------------------------------
# Parser.
# ---------------------------------------------------------------------


def parse_input_bytes(buf):
    """Convert raw tuiterm bytes into a list of Event Messages.

    Stateless w.r.t. callers - constructs a fresh parser per call, runs
    it to completion, returns the accumulated event list.  See module
    docstring for the streaming-vs-batch tradeoff.

    Empty input returns an empty list (which is the spec-correct
    behaviour for ``tuiterm.read_input(0)`` returning ``b""``).
    """
    if not buf:
        return []
    parser = _Parser(buf)
    return parser.run()


class _Parser:
    """Internal byte-stream state machine.

    Not a public class - users go through ``parse_input_bytes``.  The
    class form gives us one place to share ``_buf`` / ``_pos`` / ``_out``
    across the per-state handler methods without threading them through
    every call.
    """

    def __init__(self, buf):
        # Bytes object, not bytearray - the parser is read-only, and
        # ``bytes.find`` returns the same -1 sentinel as bytearray.
        self._buf = buf
        # Position cursor.  Each handler advances past the bytes it
        # consumed and returns; the main ``run`` loop dispatches by
        # the byte at ``_buf[_pos]``.
        self._pos = 0
        # Accumulator.  Plain list because the caller will iterate
        # once; no need for deque here.
        self._out = []

    def run(self):
        """Drive the state machine until the buffer is exhausted."""
        n = len(self._buf)
        while self._pos < n:
            b = self._buf[self._pos]
            if b == 0x1B:
                # ESC - branch on what follows.  An ESC at end-of-buffer
                # with no follow-up byte emits a bare Escape key, which
                # is the right behaviour: the user pressed Esc alone.
                self._handle_escape()
            elif b < 0x20 or b == 0x7F:
                # Control byte (0x00-0x1F except ESC already handled, or
                # the DEL at 0x7F which most terminals send for BS).
                self._emit_control_byte(b)
                self._pos += 1
            else:
                # Printable ASCII (0x20-0x7E) and the high bytes
                # (0x80-0xFF) the parser passes through as raw chars.
                # The keys table canonicalises printable chars as
                # themselves (Keys.Space is "space" but bindings can
                # also match the literal " ").  We emit the literal
                # character for simplicity; widgets that want the named
                # form use the alias in their Binding string.
                ch = chr(b)
                self._out.append(Key(key=ch, character=ch))
                self._pos += 1
        return self._out

    # -----------------------------------------------------------------
    # Per-state handlers.  Each advances ``_pos`` past the bytes it
    # consumed and appends to ``_out``.  None of them call ``run``
    # recursively - the main loop drives.
    # -----------------------------------------------------------------

    def _handle_escape(self):
        """Process a 0x1B byte and whatever follows it."""
        # Look at the next byte to decide which sub-state we enter.
        # No follow-up byte = bare Escape (the user pressed Esc alone
        # and we are at end-of-buffer).
        nxt = self._peek(1)
        if nxt is None:
            self._out.append(Key(key=_keys.Keys.Escape))
            self._pos += 1
            return

        if nxt == 0x5B:   # '['
            # CSI - check for bracketed paste first because the open
            # marker is six bytes and matches greedy on the prefix.
            if self._buf[self._pos:self._pos + len(_PASTE_OPEN)] == _PASTE_OPEN:
                self._handle_paste()
                return
            self._handle_csi()
            return

        if nxt == 0x4F:   # 'O'
            self._handle_ss3()
            return

        # ESC followed by something else - this is the "Alt + key"
        # convention most terminals use.  Emit a Key whose .key string
        # is "alt+<ch>" (or "alt+escape" for ESC ESC).  This matches
        # the upstream Textual decoding for FR-TUI-15.
        if nxt == 0x1B:
            # ESC ESC -> Alt+Escape.
            self._out.append(
                Key(key="alt+escape", modifiers=("alt",))
            )
            self._pos += 2
            return
        # Plain Alt+<ascii>.  We name the key as "alt+<char>" so
        # bindings can match the canonical Textual form.
        ch = chr(nxt)
        self._out.append(
            Key(key="alt+" + ch, character=None, modifiers=("alt",))
        )
        self._pos += 2

    def _handle_csi(self):
        """Process an ESC '[' ... <final> sequence.

        Sub-cases:
          * Mouse: ``CSI < params M/m`` (SGR mouse, the only mode v0.1
            negotiates).
          * Bracketed paste markers: already handled by _handle_escape
            before reaching here.
          * Function/cursor keys with parameters: ``CSI N ; M <final>``.
          * Plain cursor keys: ``CSI <final>`` (no parameters).
        """
        # _pos points at the ESC.  Advance past "ESC [" to the first
        # parameter byte.
        start = self._pos + 2

        # SGR mouse: the parameter list starts with '<' (0x3C).  We
        # recognise this prefix and route to the dedicated handler so
        # the generic CSI param parser does not have to special-case
        # the negative-looking first byte.
        if start < len(self._buf) and self._buf[start] == 0x3C:
            self._handle_mouse(start + 1)
            return

        # Collect parameters and find the final byte.
        params, final, end = self._scan_csi_params(start)
        if final is None:
            # Malformed / truncated - skip the entire malformed
            # sequence rather than emitting a confused Key.  The
            # design doc and FR-TUI-77 both prefer silent drop over
            # noisy bogus events; the parser's stderr-log channel is
            # the message_pump's, not this module's.
            self._pos = end
            return

        # Dispatch on final byte + parameters.
        self._dispatch_csi(params, final)
        self._pos = end

    def _scan_csi_params(self, start):
        """Read CSI parameter bytes until a final (0x40-0x7E) or EOB.

        Returns ``(params, final, end)``:
          * ``params`` - list[int] of decimal parameter values; missing
            parameters become 0 (xterm convention).
          * ``final`` - the final byte (1-byte ``bytes``), or None if
            we ran out of input before finding one.
          * ``end`` - position just past the final byte (or end of buf
            if truncated).
        """
        params = []
        current = None        # None means "no digits seen for this slot yet"
        pos = start
        n = len(self._buf)
        while pos < n:
            b = self._buf[pos]
            if 0x30 <= b <= 0x39:        # '0'-'9'
                if current is None:
                    current = 0
                current = current * 10 + (b - 0x30)
            elif b == 0x3B:              # ';' parameter separator
                params.append(0 if current is None else current)
                current = None
            elif 0x40 <= b <= 0x7E:      # final byte
                params.append(0 if current is None else current)
                # Return the final byte as a 1-byte bytes object for
                # table lookups (table keys are bytes literals).
                return params, bytes((b,)), pos + 1
            else:
                # Intermediate / private-marker bytes (0x20-0x2F, 0x3C-
                # 0x3F).  Skip them - we already special-cased '<' for
                # SGR mouse; anything else is something we do not parse.
                pass
            pos += 1
        # Ran off the end without a final byte.
        return params, None, pos

    def _dispatch_csi(self, params, final):
        """Turn a parsed CSI (params, final) into a Key event.

        Handles the FR-TUI-18 ``CSI 1 ; N <final>`` modifier encoding:
        ``N = 1 + Shift + Alt*2 + Ctrl*4``.  Modifiers are folded into
        the ``Key.modifiers`` set; the ``.key`` string stays the bare
        cursor / function-key name so bindings written as either
        ``"ctrl+up"`` or with ``modifiers={"ctrl"}`` work.

        For the modifier-baked form we ALSO emit a modifier-prefixed
        ``.key`` (e.g. ``"ctrl+up"``) so existing Textual bindings
        match without rewriting.  Last-binding-wins in @widget's merge
        (\xa76.2) means widgets that want the modifier-set form can use
        a more-specific binding.
        """
        # Tilde-terminated sequences: ``CSI N ~`` (or ``CSI N ; M ~``).
        if final == b"~":
            if not params:
                return
            key_name = _CSI_TILDE_KEYS.get(params[0])
            if key_name is None:
                return
            # Modifier is in the SECOND param slot for tilde-finals;
            # ``CSI 15 ~`` (no modifier) gives params==[15] and we
            # must not treat the 15 as a modifier code.  Only the
            # ``CSI N ; M ~`` form carries a modifier in params[1].
            if len(params) >= 2:
                mods = self._decode_modifier_param([params[1]])
            else:
                mods = set()
            full_key = self._prefix_modifiers(key_name, mods)
            self._out.append(
                Key(key=full_key, modifiers=mods)
            )
            return

        # Letter-terminated sequences: ``CSI <final>`` or
        # ``CSI 1 ; M <final>``.
        key_name = _CSI_FINAL_KEYS.get(final)
        if key_name is None:
            return

        # The ``CSI 1 ; N <letter>`` form: params==[1, N].  The plain
        # ``CSI <letter>`` form gives params==[0] (because the scanner
        # defaults missing parameters to 0).
        if len(params) >= 2:
            mods = self._decode_modifier_param(params)
        else:
            mods = set()

        full_key = self._prefix_modifiers(key_name, mods)
        self._out.append(Key(key=full_key, modifiers=mods))

    def _decode_modifier_param(self, params):
        """Decode the xterm modifier parameter (FR-TUI-18).

        ``params[-1]`` for the modifier slot - it is the last parameter
        in both ``CSI N ; M ~`` (where N is the key and M is the
        modifier) and ``CSI 1 ; M <letter>`` (where 1 is a fixed prefix
        and M is the modifier).  The encoding is
        ``M = 1 + Shift + Alt*2 + Ctrl*4`` - so M==1 means no
        modifiers, M==5 means Ctrl-only, M==4 means Ctrl (== Ctrl*4 +
        1, but xterm sends 5 there; we treat any M==4 as Ctrl for
        rxvt compat where the +1 offset is omitted).
        """
        if not params:
            return set()
        m = params[-1]
        # xterm: subtract 1 before masking.  rxvt: send the raw mask.
        # We try the xterm decode first because that is what bracketed
        # terminals (xterm, gnome-terminal, kitty, wezterm) send;
        # rxvt is a fallback for the few terminals that ship without
        # the +1.  When m==0 (the "no modifier" default the scanner
        # returns when a slot is empty), we return the empty set.
        if m == 0 or m == 1:
            return set()
        bits = m - 1 if m >= 2 else m
        mods = set()
        if bits & 1:
            mods.add("shift")
        if bits & 2:
            mods.add("alt")
        if bits & 4:
            mods.add("ctrl")
        return mods

    def _prefix_modifiers(self, key_name, mods):
        """Combine a base key name with a modifier set into a binding-
        compatible single string.

        Order: ctrl, then shift, then alt.  This matches the order used
        by ``Keys.ControlShiftLeft = "ctrl+shift+left"`` - keeping the
        prefix order canonical means a Binding declared as
        ``"ctrl+shift+left"`` and one emitted by the parser via this
        function are byte-equal.
        """
        if not mods:
            return key_name
        # Already-modifier-prefixed keys (the Control* table) double
        # up if we naively prepend; guard by checking the existing
        # prefix.  In v0.1 the only path that reaches here for a
        # Control* key is the unlikely "Ctrl+Shift+letter" case, which
        # terminals send via CSI rather than as a raw control byte -
        # so we conservatively prepend only the missing modifiers.
        parts = []
        if "ctrl" in mods and "ctrl" not in key_name:
            parts.append("ctrl")
        if "shift" in mods and "shift" not in key_name:
            parts.append("shift")
        if "alt" in mods and "alt" not in key_name:
            parts.append("alt")
        if not parts:
            return key_name
        return "+".join(parts) + "+" + key_name

    def _handle_ss3(self):
        """Process an ESC 'O' <final> sequence (function keys / cursor
        in application mode).  Two bytes plus the final = 3 bytes total.
        """
        if self._pos + 2 >= len(self._buf):
            # Truncated SS3 - skip what we have, the next parse_input
            # call will consume the rest from the same buffer (or in
            # the streaming variant, from the carry-over).
            self._pos = len(self._buf)
            return
        final = bytes((self._buf[self._pos + 2],))
        key_name = _SS3_FINAL_KEYS.get(final)
        if key_name is not None:
            self._out.append(Key(key=key_name))
        self._pos += 3

    def _handle_mouse(self, params_start):
        """Process an SGR mouse sequence ``CSI < Cb ; Cx ; Cy M/m``.

        ``params_start`` points at the first parameter byte (just
        after the '<').  Returns nothing; appends a Mouse subclass
        instance to ``_out`` and advances ``_pos`` past the final.
        """
        params, final, end = self._scan_csi_params(params_start)
        # Need at least three params (Cb, Cx, Cy) and a final byte.
        if final is None or len(params) < 3:
            self._pos = end
            return

        cb = params[0]
        cx = params[1]
        cy = params[2]

        # Decode modifiers from Cb bits (research doc 04 \xa73.2).
        mods = set()
        if cb & 4:
            mods.add("shift")
        if cb & 8:
            mods.add("alt")
        if cb & 16:
            mods.add("ctrl")

        # Decode button / event type from Cb low bits and bit 6 (wheel).
        is_wheel = bool(cb & 64)
        is_motion = bool(cb & 32)

        if is_wheel:
            # Bit 0 of the low nibble: 0=up, 1=down.
            if cb & 1:
                event = MouseScrollDown(x=cx, y=cy, modifiers=mods)
            else:
                event = MouseScrollUp(x=cx, y=cy, modifiers=mods)
        elif is_motion:
            # Motion-while-pressed.  Button index in the low bits is
            # still meaningful (which button is being dragged).
            event = MouseMove(
                x=cx, y=cy, button=(cb & 3), modifiers=mods
            )
        else:
            # Press or release; final byte M=press, m=release.
            button = cb & 3
            if final == b"M":
                event = MouseDown(
                    x=cx, y=cy, button=button, modifiers=mods
                )
            else:
                event = MouseUp(
                    x=cx, y=cy, button=button, modifiers=mods
                )

        self._out.append(event)
        self._pos = end

    def _handle_paste(self):
        """Process a bracketed-paste payload (FR-TUI-17).

        Already verified ``_buf[_pos:_pos+len(_PASTE_OPEN)] ==
        _PASTE_OPEN`` in the caller.  Searches for the matching close
        marker; if not present in this buffer, swallows everything to
        end-of-buffer (a streaming parser would carry over; v0.1's
        non-streaming variant treats it as a truncated paste and emits
        whatever it found).
        """
        body_start = self._pos + len(_PASTE_OPEN)
        # bytes.find returns -1 if not found.  We search from body_start
        # so the open marker itself does not match accidentally.
        close_at = self._buf.find(_PASTE_CLOSE, body_start)
        if close_at == -1:
            # Truncated paste - everything from body_start to EOB is
            # the (partial) payload.  Emit it as the only paste we
            # can produce for this buffer.
            payload = self._buf[body_start:]
            self._out.append(Paste(_decode_utf8_lossy(payload)))
            self._pos = len(self._buf)
            return
        payload = self._buf[body_start:close_at]
        self._out.append(Paste(_decode_utf8_lossy(payload)))
        # Skip past the close marker.
        self._pos = close_at + len(_PASTE_CLOSE)

    # -----------------------------------------------------------------
    # Small helpers.
    # -----------------------------------------------------------------

    def _peek(self, offset):
        """Return ``_buf[_pos + offset]`` as an int, or None if EOB."""
        target = self._pos + offset
        if target >= len(self._buf):
            return None
        return self._buf[target]

    def _emit_control_byte(self, b):
        """Map a 0x00-0x1F or 0x7F byte to a Key event.

        Uses the _CONTROL_BYTE_KEYS table for the named control codes
        (Tab, Enter, Escape, Backspace, the special Control* ones) and
        synthesises ``"ctrl+<letter>"`` for the rest of 0x01-0x1A.
        """
        named = _CONTROL_BYTE_KEYS.get(b)
        if named is not None:
            # character is None for control keys (they have no
            # printable representation).
            self._out.append(Key(key=named, character=None))
            return
        if 0x01 <= b <= 0x1A:
            # Ctrl-A through Ctrl-Z: synthesise as "ctrl+<letter>".
            # 0x01 -> 'a' (0x61), 0x02 -> 'b', ..., 0x1A -> 'z'.
            letter = chr(b + 0x60)
            self._out.append(Key(key="ctrl+" + letter, character=None))
            return
        # Other control bytes (0x1C-0x1F not in the table, etc.) fall
        # through silently.  The named table covers the cases the v0.1
        # binding surface cares about.


def _decode_utf8_lossy(data):
    """Decode bytes as UTF-8, replacing any decode error with U+FFFD.

    The paste payload is application data; we cannot raise on bad bytes
    because the user pasted what they pasted.  MicroPython's
    ``bytes.decode`` does not accept the ``errors=`` kwarg in every
    build, so we fall back to a manual replacement on UnicodeError.
    """
    try:
        return data.decode("utf-8")
    except Exception:
        # MicroPython may surface decode errors as ValueError or
        # UnicodeError depending on build; the catch-all is safe
        # because the only exceptions ``bytes.decode`` can raise are
        # decode-related.
        out = []
        for b in data:
            if b < 0x80:
                out.append(chr(b))
            else:
                # Replacement char for everything else - lossy but
                # safe.  Application code that needs strict decode
                # rules can re-parse ``Paste.text`` against the raw
                # bytes (which we discard here; future enhancement
                # could keep them on Paste._raw_bytes).
                out.append("�")
        return "".join(out)


# ---------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------


def dispatch_key(app, key_event):
    """Walk focused -> ancestors -> app, firing the first matching binding.

    Implements design doc \xa76.3 verbatim.  At each node:
      1. Read ``type(node)._tui_widget_meta["bindings"]``.
      2. For each Binding, compare its ``.key`` to ``key_event.key``
         (and, for modifier-set keys, the prefixed form).
      3. On match, call ``getattr(node, "action_" + binding.action)()``.
         If the node has no such method, try the app.  If neither does,
         continue walking.
      4. Return True on first action invocation; False if exhausted.

    The walk order is "focused widget, then its parents in order, then
    the app itself" - which is what the design doc pseudo-code spells
    out and what upstream Textual implements.

    This is a sync function despite the \xa76.3 pseudo-code's ``async
    def``: the design doc's async signature anticipates an
    ``_invoke_action`` that may await; v0.1's actions are all sync
    (FR-TUI-27 says actions are methods called on the node, no
    coroutine contract).  When actions need to await, this surface
    grows an async sibling - the same shape MessagePump's _dispatch
    takes.
    """
    # focused may be None - in that case the walk starts directly at
    # the app, which is the documented behaviour for "no widget has
    # the focus yet" (typical at startup before the first focus()).
    focused = getattr(app, "focused", None)

    # Build the walk list once so we do not recompute ancestors per
    # binding.  ``ancestors_with_self`` includes the focused node; the
    # app is appended explicitly at the end since the app is the
    # screen-stack owner, not technically a DOMNode ancestor.
    if focused is None:
        nodes = [app]
    else:
        # Avoid pulling in DOMNode here; walk the parent chain
        # inline so this module stays a leaf w.r.t. dom_node.
        nodes = [focused]
        node = focused._parent
        while node is not None:
            nodes.append(node)
            node = node._parent
        # App may already be the root - check identity to avoid the
        # duplicate-app case.
        if nodes[-1] is not app:
            nodes.append(app)

    for node in nodes:
        meta = getattr(type(node), "_tui_widget_meta", None)
        if meta is None:
            # Undecorated class in the walk - tolerated (no bindings to
            # consult), continue to parent.
            continue
        bindings = meta.get("bindings", ())
        for binding in bindings:
            if not _binding_matches(binding, key_event):
                continue
            action_name = "action_" + binding.action
            action = getattr(node, action_name, None)
            if action is None:
                # Bubble to the App-level action lookup per \xa76.3.
                # If ``node`` IS the app the second lookup is a tautology
                # but cheap (one getattr).
                action = getattr(app, action_name, None)
            if action is None:
                # No action defined - keep walking for a higher
                # binding that does have an action.  This is the
                # design doc's "explicit False -> keep walking"
                # behaviour expressed at the lookup layer rather than
                # via a return-value convention.
                continue
            # Invoke.  Synchronous - see function docstring.  If the
            # action raises, it propagates out (the App's task wrapper
            # logs it via FR-TUI-77 the same way handlers do).
            action()
            return True
    return False


def _binding_matches(binding, key_event):
    """Return True if ``binding.key`` matches ``key_event``.

    Two match cases:
      1. The binding key string equals ``key_event.key`` exactly.  This
         is the canonical case - covers ``Binding("ctrl+q", ...)``
         matching a Key whose ``.key == "ctrl+q"``.
      2. The binding key matches an alias of the event's key per
         ``keys.KEY_ALIASES``.  Lets ``Binding("tab", ...)`` fire on
         a Key whose ``.key == "ctrl+i"`` (which is what raw \\t bytes
         produce; tab is canonical, ctrl+i is the alias).

    Modifier set is not separately consulted - the parser folds
    modifiers into the prefixed key string (``"ctrl+up"``), which is
    what bindings declare against.  Splitting modifier match into a
    separate path would require parsing binding strings, which the
    Binding value type does not do.
    """
    if binding.key == key_event.key:
        return True
    # Alias check - both directions.  KEY_ALIASES maps canonical -> [aliases].
    aliases = _keys.KEY_ALIASES.get(binding.key)
    if aliases and key_event.key in aliases:
        return True
    # Inverse - the event might be the canonical and the binding the alias.
    event_aliases = _keys.KEY_ALIASES.get(key_event.key)
    if event_aliases and binding.key in event_aliases:
        return True
    return False
