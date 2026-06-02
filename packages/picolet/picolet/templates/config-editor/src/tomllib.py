"""tomllib.py — minimal TOML 1.0 parser for MicroPython.

Vendored single-file implementation for the picolet config-editor example.
Implements the read-only interface matching Python 3.11 stdlib tomllib:
    load(fp)       — parse binary-mode file object
    loads(s)       — parse string

Licence: MIT (Picolet project).

Scope:
  - String (basic, literal, multi-line basic, multi-line literal)
  - Integer (decimal, hex 0x, octal 0o, binary 0b, with _ separators)
  - Float (including inf, nan)
  - Boolean (true, false)
  - Datetime, Date, Time — parsed as plain strings (not datetime objects);
    round-trip fidelity is preserved since they serialise back verbatim.
  - Array
  - Inline table
  - Standard table [header]
  - Array of tables [[header]]
  - Dotted keys
  - Comments

Known limitation: datetime values are returned as str, not datetime objects.
This is acceptable for the config-editor use case (they are displayed and
saved verbatim). The _toml_dumps() serialiser in config_store.py handles
string values correctly.
"""
from __future__ import annotations
import re as _re


class TOMLDecodeError(ValueError):
    """Raised when input is not valid TOML."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(fp) -> dict:
    """Parse a binary-mode file object as TOML."""
    return loads(fp.read().decode("utf-8"))


def loads(s: str) -> dict:
    """Parse a TOML string and return a Python dict."""
    parser = _Parser(s)
    return parser.parse()


# ---------------------------------------------------------------------------
# Lexer / token helpers
# ---------------------------------------------------------------------------

_WS = frozenset(" \t")
_BARE_KEY = _re.compile(r"[A-Za-z0-9_-]+")
_INT_RE = _re.compile(
    r"[+-]?"
    r"(?:0x[0-9A-Fa-f][0-9A-Fa-f_]*"
    r"|0o[0-7][0-7_]*"
    r"|0b[01][01_]*"
    r"|[0-9][0-9_]*)"
)
_FLOAT_RE = _re.compile(
    r"[+-]?(?:inf|nan|[0-9][0-9_]*(?:\.[0-9][0-9_]*)?(?:[eE][+-]?[0-9][0-9_]*)?)"
)
_DATETIME_RE = _re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)
_DATE_RE = _re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_RE = _re.compile(r"\d{2}:\d{2}:\d{2}")


def _err(msg: str, pos: int, src: str) -> TOMLDecodeError:
    line = src[:pos].count('\n') + 1
    col = pos - src[:pos].rfind('\n')
    return TOMLDecodeError(f"{msg} (line {line}, col {col})")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, src: str) -> None:
        self._s = src
        self._pos = 0
        self._root: dict = {}
        self._cur: dict = self._root
        self._implicit: set = set()  # set of key tuples created implicitly

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _at_end(self) -> bool:
        return self._pos >= len(self._s)

    def _ch(self) -> str:
        return self._s[self._pos] if self._pos < len(self._s) else ''

    def _advance(self, n: int = 1) -> None:
        self._pos += n

    def _skip_ws(self) -> None:
        while self._pos < len(self._s) and self._s[self._pos] in _WS:
            self._pos += 1

    def _skip_ws_and_newlines(self) -> None:
        while self._pos < len(self._s) and self._s[self._pos] in (' ', '\t', '\n', '\r'):
            self._pos += 1

    def _skip_comment(self) -> None:
        if self._ch() == '#':
            while self._pos < len(self._s) and self._s[self._pos] != '\n':
                self._pos += 1

    def _skip_to_newline_or_comment(self) -> None:
        self._skip_ws()
        self._skip_comment()
        if not self._at_end() and self._ch() not in ('\n', '\r'):
            raise _err(f"unexpected character {self._ch()!r} after value", self._pos, self._s)

    # -------------------------------------------------------------------------
    # Top-level parse
    # -------------------------------------------------------------------------

    def parse(self) -> dict:
        while not self._at_end():
            self._skip_ws()
            c = self._ch()
            if c in ('\n', '\r'):
                self._advance()
                continue
            if c == '#':
                self._skip_comment()
                continue
            if c == '[':
                self._advance()
                if self._ch() == '[':
                    self._advance()
                    self._parse_array_of_tables()
                else:
                    self._parse_table()
            else:
                # Key = value
                key_parts = self._parse_key()
                self._skip_ws()
                if self._ch() != '=':
                    raise _err("expected '=' after key", self._pos, self._s)
                self._advance()
                self._skip_ws()
                value = self._parse_value()
                self._set_value(self._cur, key_parts, value, allow_super_table=False)
                self._skip_to_newline_or_comment()
        return self._root

    # -------------------------------------------------------------------------
    # Table headers
    # -------------------------------------------------------------------------

    def _parse_table(self) -> None:
        key_parts = self._parse_key()
        self._skip_ws()
        if self._ch() != ']':
            raise _err("expected ']'", self._pos, self._s)
        self._advance()
        self._skip_to_newline_or_comment()
        self._cur = self._navigate_to(key_parts, create_table=True)

    def _parse_array_of_tables(self) -> None:
        key_parts = self._parse_key()
        self._skip_ws()
        if not self._s[self._pos:self._pos + 2] == ']]':
            raise _err("expected ']]'", self._pos, self._s)
        self._advance(2)
        self._skip_to_newline_or_comment()
        # Navigate to the parent and append a new dict to the array.
        target = self._root
        for i, part in enumerate(key_parts[:-1]):
            if part not in target:
                target[part] = {}
                self._implicit.add(tuple(key_parts[:i + 1]))
            nxt = target[part]
            if isinstance(nxt, list):
                nxt = nxt[-1]
            target = nxt
        last = key_parts[-1]
        if last not in target:
            target[last] = []
        arr = target[last]
        if not isinstance(arr, list):
            raise _err(f"key {last!r} is not an array of tables", self._pos, self._s)
        new_table: dict = {}
        arr.append(new_table)
        self._cur = new_table

    def _navigate_to(self, key_parts: list, *, create_table: bool) -> dict:
        node = self._root
        for i, part in enumerate(key_parts):
            path = tuple(key_parts[:i + 1])
            if part not in node:
                if create_table:
                    node[part] = {}
                    self._implicit.add(path)
                else:
                    raise _err(f"key {part!r} not found", self._pos, self._s)
            nxt = node[part]
            if isinstance(nxt, list):
                nxt = nxt[-1]
            if not isinstance(nxt, dict):
                raise _err(f"key {part!r} is not a table", self._pos, self._s)
            node = nxt
        return node

    # -------------------------------------------------------------------------
    # Key parsing
    # -------------------------------------------------------------------------

    def _parse_key(self) -> list:
        """Parse a (possibly dotted) key. Returns a list of string parts."""
        parts = [self._parse_simple_key()]
        while True:
            self._skip_ws()
            if self._ch() != '.':
                break
            self._advance()
            self._skip_ws()
            parts.append(self._parse_simple_key())
        return parts

    def _parse_simple_key(self) -> str:
        c = self._ch()
        if c == '"':
            return self._parse_basic_string()
        if c == "'":
            return self._parse_literal_string()
        m = _BARE_KEY.match(self._s, self._pos)
        if not m:
            raise _err("expected a key", self._pos, self._s)
        self._pos = m.end()
        return m.group(0)

    # -------------------------------------------------------------------------
    # Value dispatch
    # -------------------------------------------------------------------------

    def _parse_value(self):
        c = self._ch()
        if c == '"':
            if self._s[self._pos:self._pos + 3] == '"""':
                return self._parse_ml_basic_string()
            return self._parse_basic_string()
        if c == "'":
            if self._s[self._pos:self._pos + 3] == "'''":
                return self._parse_ml_literal_string()
            return self._parse_literal_string()
        if c == '[':
            return self._parse_array()
        if c == '{':
            return self._parse_inline_table()
        if self._s[self._pos:self._pos + 4] == 'true':
            self._advance(4)
            return True
        if self._s[self._pos:self._pos + 5] == 'false':
            self._advance(5)
            return False
        # Datetime / Date / Time — check before float/int (has hyphens).
        m = _DATETIME_RE.match(self._s, self._pos)
        if m:
            end = m.end()
            # Possible timezone suffix.
            suffix_re = _re.compile(r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
            sm = suffix_re.match(self._s, end)
            if sm:
                end = sm.end()
            val = self._s[self._pos:end]
            self._pos = end
            return val  # str; see known limitation in module docstring
        m = _DATE_RE.match(self._s, self._pos)
        if m and (self._pos + m.end() - self._pos >= len(self._s) or
                  self._s[m.end()] in (' ', '\t', '\n', '\r', '#', ']', '}')):
            val = m.group(0)
            self._pos = m.end()
            return val
        m = _TIME_RE.match(self._s, self._pos)
        if m and (self._pos + m.end() - self._pos >= len(self._s) or
                  self._s[m.end()] in (' ', '\t', '\n', '\r', '#', ']', '}')):
            val = m.group(0)
            self._pos = m.end()
            return val
        # Float (must try before int to handle 1.0, inf, nan).
        m = _FLOAT_RE.match(self._s, self._pos)
        if m:
            raw = m.group(0)
            if '.' in raw or 'e' in raw or 'E' in raw or raw.lstrip('+-') in ('inf', 'nan'):
                self._pos = m.end()
                clean = raw.replace('_', '')
                if clean.lstrip('+-') == 'inf':
                    return float('inf') if not clean.startswith('-') else float('-inf')
                if clean.lstrip('+-') == 'nan':
                    return float('nan')
                return float(clean)
        # Integer.
        m = _INT_RE.match(self._s, self._pos)
        if m:
            raw = m.group(0)
            self._pos = m.end()
            clean = raw.replace('_', '')
            if clean.startswith(('0x', '-0x', '+0x')):
                return int(clean, 16)
            if clean.startswith(('0o', '-0o', '+0o')):
                return int(clean, 8)
            if clean.startswith(('0b', '-0b', '+0b')):
                return int(clean, 2)
            return int(clean, 10)
        raise _err(f"unexpected character {c!r} in value", self._pos, self._s)

    # -------------------------------------------------------------------------
    # String parsers
    # -------------------------------------------------------------------------

    def _parse_basic_string(self) -> str:
        self._advance()  # opening "
        parts = []
        while self._pos < len(self._s):
            c = self._s[self._pos]
            if c == '"':
                self._advance()
                return ''.join(parts)
            if c == '\\':
                self._advance()
                e = self._ch()
                self._advance()
                escapes = {
                    'b': '\b', 't': '\t', 'n': '\n', 'f': '\f',
                    'r': '\r', '"': '"', '\\': '\\',
                }
                if e in escapes:
                    parts.append(escapes[e])
                elif e == 'u':
                    hex4 = self._s[self._pos:self._pos + 4]
                    self._advance(4)
                    parts.append(chr(int(hex4, 16)))
                elif e == 'U':
                    hex8 = self._s[self._pos:self._pos + 8]
                    self._advance(8)
                    parts.append(chr(int(hex8, 16)))
                else:
                    raise _err(f"invalid escape \\{e}", self._pos, self._s)
            else:
                parts.append(c)
                self._advance()
        raise _err("unterminated basic string", self._pos, self._s)

    def _parse_literal_string(self) -> str:
        self._advance()  # opening '
        end = self._s.find("'", self._pos)
        if end == -1:
            raise _err("unterminated literal string", self._pos, self._s)
        val = self._s[self._pos:end]
        self._pos = end + 1
        return val

    def _parse_ml_basic_string(self) -> str:
        self._advance(3)  # opening """
        # Trim optional leading newline.
        if self._ch() == '\n':
            self._advance()
        parts = []
        while self._pos < len(self._s):
            if self._s[self._pos:self._pos + 3] == '"""':
                self._advance(3)
                return ''.join(parts)
            c = self._s[self._pos]
            if c == '\\':
                self._advance()
                e = self._ch()
                if e in (' ', '\t', '\n'):
                    # Line-ending backslash: skip whitespace.
                    while self._ch() in (' ', '\t', '\n', '\r'):
                        self._advance()
                    continue
                self._advance()
                escapes = {
                    'b': '\b', 't': '\t', 'n': '\n', 'f': '\f',
                    'r': '\r', '"': '"', '\\': '\\',
                }
                if e in escapes:
                    parts.append(escapes[e])
                else:
                    raise _err(f"invalid escape \\{e}", self._pos, self._s)
            else:
                parts.append(c)
                self._advance()
        raise _err("unterminated multi-line basic string", self._pos, self._s)

    def _parse_ml_literal_string(self) -> str:
        self._advance(3)  # opening '''
        if self._ch() == '\n':
            self._advance()
        end = self._s.find("'''", self._pos)
        if end == -1:
            raise _err("unterminated multi-line literal string", self._pos, self._s)
        val = self._s[self._pos:end]
        self._pos = end + 3
        return val

    # -------------------------------------------------------------------------
    # Array and inline table
    # -------------------------------------------------------------------------

    def _parse_array(self) -> list:
        self._advance()  # opening [
        result = []
        while True:
            self._skip_ws_and_newlines()
            self._skip_comment()
            self._skip_ws_and_newlines()
            if self._ch() == ']':
                self._advance()
                return result
            if self._at_end():
                raise _err("unterminated array", self._pos, self._s)
            result.append(self._parse_value())
            self._skip_ws_and_newlines()
            self._skip_comment()
            self._skip_ws_and_newlines()
            if self._ch() == ',':
                self._advance()
            elif self._ch() == ']':
                self._advance()
                return result
            else:
                raise _err(f"expected ',' or ']' in array, got {self._ch()!r}", self._pos, self._s)

    def _parse_inline_table(self) -> dict:
        self._advance()  # opening {
        result: dict = {}
        first = True
        while True:
            self._skip_ws()
            if self._ch() == '}':
                self._advance()
                return result
            if self._at_end():
                raise _err("unterminated inline table", self._pos, self._s)
            if not first:
                if self._ch() != ',':
                    raise _err("expected ',' in inline table", self._pos, self._s)
                self._advance()
                self._skip_ws()
            first = False
            key_parts = self._parse_key()
            self._skip_ws()
            if self._ch() != '=':
                raise _err("expected '=' in inline table", self._pos, self._s)
            self._advance()
            self._skip_ws()
            value = self._parse_value()
            self._set_value(result, key_parts, value, allow_super_table=False)

    # -------------------------------------------------------------------------
    # Value assignment with dotted key support
    # -------------------------------------------------------------------------

    def _set_value(
        self,
        target: dict,
        key_parts: list,
        value,
        *,
        allow_super_table: bool,
    ) -> None:
        for part in key_parts[:-1]:
            if part not in target:
                target[part] = {}
            nxt = target[part]
            if not isinstance(nxt, dict):
                raise _err(
                    f"cannot use {part!r} as a table key: already has a non-table value",
                    self._pos,
                    self._s,
                )
            target = nxt
        last = key_parts[-1]
        if last in target:
            raise _err(f"key {last!r} already defined", self._pos, self._s)
        target[last] = value
