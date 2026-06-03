"""Keyboard key identifiers and helpers (Textual port).

Upstream
--------
Source : https://raw.githubusercontent.com/Textualize/textual/main/src/textual/keys.py
Static data tables describing every key name Textual recognises, the
modifier-prefixed combinations, the aliases between equivalent shortcut
spellings (``tab`` / ``ctrl+i``), and the display-friendly substitutions
used by the footer widget.

Role
----
Single source of truth for key-string identifiers consumed by Binding,
the input pump, and any widget that pattern-matches on event ``.key``.
Pure data plus four tiny pure functions; no event-loop coupling.

Adjustments for MicroPython
---------------------------
* ``class Keys(str, Enum):`` becomes a plain class with string class
  attributes.  CPython's ``StrEnum`` idiom (the ``(str, Enum)`` multiple
  inheritance trick) requires a real ``EnumMeta`` metaclass to splice
  ``str`` construction into member creation; the picolet ``enum`` shim
  is decorator-driven and explicitly does not implement ``StrEnum`` (see
  ``picolet_tui/_shims/enum.py`` "Deliberately NOT implemented" block).
  Consumers compare against ``.key`` strings (``event.key == "ctrl+a"``)
  rather than the enum member object, so dropping ``Enum`` is lossless
  for the v0.1 surface and saves the entire decorator round-trip.
* ``from functools import lru_cache`` removed.  The two cached helpers
  (``format_key``, ``key_to_character``) are only ever called from the
  footer-render path and from the input pump's printable-character
  fallback; the picolet shim caps ``lru_cache`` at maxsize=128 anyway
  (NFR-TUI-6) and the per-call work is a couple of dict lookups, so
  the cache buys nothing measurable.  Restore the decorator if a future
  profile shows ``format_key`` on a hot path.
* ``import unicodedata`` is guarded.  MicroPython core ships no
  ``unicodedata`` module and the picolet ``_shims`` pack does not
  provide one — see SHIM GAP note at the bottom of this docstring.
  When the module is missing, ``format_key`` and ``key_to_character``
  fall back to returning the key string unchanged / ``None``, which is
  the same behaviour CPython exhibits when ``unicodedata.lookup``
  raises ``KeyError`` for an unknown name.
* ``from __future__ import annotations`` dropped — MicroPython treats
  every annotation as a string already.

SHIM GAP
--------
``unicodedata`` is referenced by ``format_key`` (to produce printable
glyphs like ``!`` from ``"exclamation_mark"``) and by
``key_to_character`` / ``_character_to_key`` (round-tripping printable
ASCII through the Unicode character database).  Neither is on the v0.1
event-pump critical path — bindings match on the literal key string —
so the fallback returning the raw identifier is acceptable for the
binary cut.  A later phase that wants pretty footer glyphs will need to
either freeze a name-table subset into ``_shims/unicodedata.py`` or
keep an inline ``ASCII_NAME_TO_CHAR`` map covering the punctuation
that ``KEY_TO_UNICODE_NAME`` references.
"""

try:
    import unicodedata  # type: ignore
except ImportError:  # MicroPython: no unicodedata module.  See SHIM GAP above.
    unicodedata = None  # type: ignore


# Plain class, not an Enum — see module docstring for the rationale.
# Members exposed as class attributes whose values are the wire-format
# strings the input pump emits and bindings match against.
class Keys:
    """List of keys for use in key bindings.

    All values are strings; ``Keys.ControlA == "ctrl+a"`` holds.  Treat
    the class as a typed namespace rather than an enum — there is no
    ``Keys.iter_members()`` and ``isinstance(x, Keys)`` is meaningless.
    """

    Escape = "escape"  # Also Control-[
    ShiftEscape = "shift+escape"
    Return = "return"

    ControlAt = "ctrl+@"  # Also Control-Space.

    ControlA = "ctrl+a"
    ControlB = "ctrl+b"
    ControlC = "ctrl+c"
    ControlD = "ctrl+d"
    ControlE = "ctrl+e"
    ControlF = "ctrl+f"
    ControlG = "ctrl+g"
    ControlH = "ctrl+h"
    ControlI = "ctrl+i"  # Tab
    ControlJ = "ctrl+j"  # Newline
    ControlK = "ctrl+k"
    ControlL = "ctrl+l"
    ControlM = "ctrl+m"  # Carriage return
    ControlN = "ctrl+n"
    ControlO = "ctrl+o"
    ControlP = "ctrl+p"
    ControlQ = "ctrl+q"
    ControlR = "ctrl+r"
    ControlS = "ctrl+s"
    ControlT = "ctrl+t"
    ControlU = "ctrl+u"
    ControlV = "ctrl+v"
    ControlW = "ctrl+w"
    ControlX = "ctrl+x"
    ControlY = "ctrl+y"
    ControlZ = "ctrl+z"

    Control1 = "ctrl+1"
    Control2 = "ctrl+2"
    Control3 = "ctrl+3"
    Control4 = "ctrl+4"
    Control5 = "ctrl+5"
    Control6 = "ctrl+6"
    Control7 = "ctrl+7"
    Control8 = "ctrl+8"
    Control9 = "ctrl+9"
    Control0 = "ctrl+0"

    ControlShift1 = "ctrl+shift+1"
    ControlShift2 = "ctrl+shift+2"
    ControlShift3 = "ctrl+shift+3"
    ControlShift4 = "ctrl+shift+4"
    ControlShift5 = "ctrl+shift+5"
    ControlShift6 = "ctrl+shift+6"
    ControlShift7 = "ctrl+shift+7"
    ControlShift8 = "ctrl+shift+8"
    ControlShift9 = "ctrl+shift+9"
    ControlShift0 = "ctrl+shift+0"

    ControlBackslash = "ctrl+backslash"
    ControlSquareClose = "ctrl+right_square_bracket"
    ControlCircumflex = "ctrl+circumflex_accent"
    ControlUnderscore = "ctrl+underscore"

    Left = "left"
    Right = "right"
    Up = "up"
    Down = "down"
    Home = "home"
    End = "end"
    Insert = "insert"
    Delete = "delete"
    PageUp = "pageup"
    PageDown = "pagedown"

    ControlLeft = "ctrl+left"
    ControlRight = "ctrl+right"
    ControlUp = "ctrl+up"
    ControlDown = "ctrl+down"
    ControlHome = "ctrl+home"
    ControlEnd = "ctrl+end"
    ControlInsert = "ctrl+insert"
    ControlDelete = "ctrl+delete"
    ControlPageUp = "ctrl+pageup"
    ControlPageDown = "ctrl+pagedown"

    ShiftLeft = "shift+left"
    ShiftRight = "shift+right"
    ShiftUp = "shift+up"
    ShiftDown = "shift+down"
    ShiftHome = "shift+home"
    ShiftEnd = "shift+end"
    ShiftInsert = "shift+insert"
    ShiftDelete = "shift+delete"
    ShiftPageUp = "shift+pageup"
    ShiftPageDown = "shift+pagedown"

    ControlShiftLeft = "ctrl+shift+left"
    ControlShiftRight = "ctrl+shift+right"
    ControlShiftUp = "ctrl+shift+up"
    ControlShiftDown = "ctrl+shift+down"
    ControlShiftHome = "ctrl+shift+home"
    ControlShiftEnd = "ctrl+shift+end"
    ControlShiftInsert = "ctrl+shift+insert"
    ControlShiftDelete = "ctrl+shift+delete"
    ControlShiftPageUp = "ctrl+shift+pageup"
    ControlShiftPageDown = "ctrl+shift+pagedown"

    BackTab = "shift+tab"  # shift + tab

    F1 = "f1"
    F2 = "f2"
    F3 = "f3"
    F4 = "f4"
    F5 = "f5"
    F6 = "f6"
    F7 = "f7"
    F8 = "f8"
    F9 = "f9"
    F10 = "f10"
    F11 = "f11"
    F12 = "f12"
    F13 = "f13"
    F14 = "f14"
    F15 = "f15"
    F16 = "f16"
    F17 = "f17"
    F18 = "f18"
    F19 = "f19"
    F20 = "f20"
    F21 = "f21"
    F22 = "f22"
    F23 = "f23"
    F24 = "f24"

    ControlF1 = "ctrl+f1"
    ControlF2 = "ctrl+f2"
    ControlF3 = "ctrl+f3"
    ControlF4 = "ctrl+f4"
    ControlF5 = "ctrl+f5"
    ControlF6 = "ctrl+f6"
    ControlF7 = "ctrl+f7"
    ControlF8 = "ctrl+f8"
    ControlF9 = "ctrl+f9"
    ControlF10 = "ctrl+f10"
    ControlF11 = "ctrl+f11"
    ControlF12 = "ctrl+f12"
    ControlF13 = "ctrl+f13"
    ControlF14 = "ctrl+f14"
    ControlF15 = "ctrl+f15"
    ControlF16 = "ctrl+f16"
    ControlF17 = "ctrl+f17"
    ControlF18 = "ctrl+f18"
    ControlF19 = "ctrl+f19"
    ControlF20 = "ctrl+f20"
    ControlF21 = "ctrl+f21"
    ControlF22 = "ctrl+f22"
    ControlF23 = "ctrl+f23"
    ControlF24 = "ctrl+f24"

    # Matches any key.
    Any = "<any>"

    # Special.
    ScrollUp = "<scroll-up>"
    ScrollDown = "<scroll-down>"

    # For internal use: key which is ignored.
    # (The key binding for this key should not do anything.)
    Ignore = "<ignore>"

    # Some 'Key' aliases (for backwardshift+compatibility).
    ControlSpace = "ctrl-at"
    Tab = "tab"
    Space = "space"
    Enter = "enter"
    Backspace = "backspace"

    # ShiftControl was renamed to ControlShift in
    # 888fcb6fa4efea0de8333177e1bbc792f3ff3c24 (20 Feb 2020).
    ShiftControlLeft = ControlShiftLeft
    ShiftControlRight = ControlShiftRight
    ShiftControlHome = ControlShiftHome
    ShiftControlEnd = ControlShiftEnd


# Unicode db contains some obscure names; map them onto more common terms
# so binding strings stay readable.
KEY_NAME_REPLACEMENTS = {
    "solidus": "slash",
    "reverse_solidus": "backslash",
    "commercial_at": "at",
    "hyphen_minus": "minus",
    "plus_sign": "plus",
    "low_line": "underscore",
}
REPLACED_KEYS = {value: key for key, value in KEY_NAME_REPLACEMENTS.items()}

# Friendly punctuation key names back to their Unicode database names.
# The forward direction (Unicode -> friendly) replaces spaces and dashes
# with underscores, which is not reversible by string transforms alone.
KEY_TO_UNICODE_NAME = {
    "exclamation_mark": "EXCLAMATION MARK",
    "quotation_mark": "QUOTATION MARK",
    "number_sign": "NUMBER SIGN",
    "dollar_sign": "DOLLAR SIGN",
    "percent_sign": "PERCENT SIGN",
    "left_parenthesis": "LEFT PARENTHESIS",
    "right_parenthesis": "RIGHT PARENTHESIS",
    "plus_sign": "PLUS SIGN",
    "hyphen_minus": "HYPHEN-MINUS",
    "full_stop": "FULL STOP",
    "less_than_sign": "LESS-THAN SIGN",
    "equals_sign": "EQUALS SIGN",
    "greater_than_sign": "GREATER-THAN SIGN",
    "question_mark": "QUESTION MARK",
    "commercial_at": "COMMERCIAL AT",
    "left_square_bracket": "LEFT SQUARE BRACKET",
    "reverse_solidus": "REVERSE SOLIDUS",
    "right_square_bracket": "RIGHT SQUARE BRACKET",
    "circumflex_accent": "CIRCUMFLEX ACCENT",
    "low_line": "LOW LINE",
    "grave_accent": "GRAVE ACCENT",
    "left_curly_bracket": "LEFT CURLY BRACKET",
    "vertical_line": "VERTICAL LINE",
    "right_curly_bracket": "RIGHT CURLY BRACKET",
}

# Aliased identifiers — pressing ``ctrl+m`` and pressing ``enter`` both
# dispatch through ``key_enter`` (and ``key_ctrl_m``) so widget authors
# can bind either spelling.
KEY_ALIASES = {
    "tab": ["ctrl+i"],
    "enter": ["ctrl+m"],
    "escape": ["ctrl+left_square_brace"],
    "ctrl+at": ["ctrl+space"],
    "ctrl+j": ["newline"],
}

KEY_DISPLAY_ALIASES = {
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "backspace": "⌫",
    "escape": "esc",
    "enter": "⏎",
    "minus": "-",
    "space": "space",
    "pagedown": "pgdn",
    "pageup": "pgup",
    "delete": "del",
}


ASCII_KEY_NAMES = {"\t": "tab"}


def _get_unicode_name_from_key(key):
    """Best guess for the Unicode name of the char corresponding to ``key``.

    Pseudo-inverse of ``_character_to_key``.
    """
    return KEY_TO_UNICODE_NAME.get(key, key)


def _get_key_aliases(key):
    """Return all aliases for ``key``, including ``key`` itself."""
    return [key] + KEY_ALIASES.get(key, [])


def format_key(key):
    """Footer-friendly rendering of a ``Binding`` key string.

    Tries the display-alias table first (arrow glyphs, ``esc``), then
    falls back to the Unicode character name when one is registered.
    With no ``unicodedata`` available (MicroPython), the function returns
    the resolved identifier as-is rather than raising.
    """
    display_alias = KEY_DISPLAY_ALIASES.get(key)
    if display_alias:
        return display_alias

    original_key = REPLACED_KEYS.get(key, key)
    tentative_unicode_name = _get_unicode_name_from_key(original_key)
    if unicodedata is not None:
        try:
            unicode_name = unicodedata.lookup(tentative_unicode_name)
        except KeyError:
            pass
        else:
            if unicode_name.isprintable():
                return unicode_name
    return tentative_unicode_name


def key_to_character(key):
    """Return the printable character a key identifier maps to, or ``None``.

    Modifier-prefixed keys (anything with a ``+`` separator) always
    resolve to ``None`` — they are not printable characters.
    """
    _, separator, key = key.rpartition("+")
    if separator:
        # Modifier (other than shift) present; keys with modifiers are
        # never printable characters.
        return None
    if len(key) == 1:
        # Single-character identifiers are themselves the character.
        return key
    if unicodedata is None:
        return None
    try:
        return unicodedata.lookup(KEY_TO_UNICODE_NAME[key])
    except KeyError:
        pass
    try:
        return unicodedata.lookup(key.replace("_", " ").upper())
    except KeyError:
        pass
    return None


def _character_to_key(character):
    """Convert a single character to a key value.

    Undone by ``_get_unicode_name_from_key``.
    """
    if not character.isalnum():
        if unicodedata is not None:
            try:
                key = (
                    unicodedata.name(character)
                    .lower()
                    .replace("-", "_")
                    .replace(" ", "_")
                )
            except ValueError:
                key = ASCII_KEY_NAMES.get(character, character)
        else:
            # Without unicodedata the only safe transforms are the
            # explicit ASCII overrides; everything else passes through.
            key = ASCII_KEY_NAMES.get(character, character)
    else:
        key = character
    key = KEY_NAME_REPLACEMENTS.get(key, key)
    return key


def _normalize_key_list(keys):
    """Normalise a comma-separated key list, expanding single-letter shortcuts."""
    keys_list = [key.strip() for key in keys.split(",")]
    return ",".join(
        _character_to_key(key) if len(key) == 1 else key for key in keys_list
    )
