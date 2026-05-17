"""micro_yaml.py — minimal YAML subset parser for MicroPython.

Provenance: hand-written inline implementation for the picolet config-editor
example. Inspired by the nickovs/micropython-yaml approach but implemented
from scratch to avoid the CPython-only dependencies in that library.

Licence: MIT (same as the rest of the picolet project).

Scope (simple config-file YAML only):
  - Mappings (key: value)
  - Sequences (- item)
  - Nested mappings and sequences via indentation
  - Scalar types: null, bool (true/false/yes/no/on/off), int, float, string
  - Quoted strings (single and double)
  - Comments (# prefix on a line or inline)

NOT supported:
  - YAML anchors (&) and aliases (*)
  - Tags (!!)
  - Multi-document streams (---)
  - Block scalars (| and >)
  - Flow-style maps/sequences beyond simple cases

If unsupported syntax is encountered, YAMLError is raised with a message
describing the position. The caller (config_store.py) wraps this in a
user-facing error.
"""
from __future__ import annotations


class YAMLError(Exception):
    """Raised for any YAML parse error."""


def load(text: str):
    """Parse a YAML text string and return the corresponding Python object."""
    parser = _Parser(text)
    return parser.parse()


# ---------------------------------------------------------------------------
# Internal parser
# ---------------------------------------------------------------------------

_BOOL_TRUE  = frozenset({"true", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "no", "off"})


class _Parser:
    def __init__(self, text: str) -> None:
        self._lines: list[str] = text.splitlines()
        self._pos = 0  # current line index

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def parse(self):
        result = self._parse_block(0)
        return result

    # ------------------------------------------------------------------
    # Block-level parser — dispatches to mapping or sequence
    # ------------------------------------------------------------------

    def _parse_block(self, min_indent: int):
        """Parse a YAML block starting at the current position.

        Returns None if there are no more meaningful lines.
        """
        # Skip to the first non-empty, non-comment line at >= min_indent.
        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            stripped = line.lstrip()
            if not stripped or stripped.startswith('#'):
                self._pos += 1
                continue
            indent = len(line) - len(stripped)
            if indent < min_indent:
                return None
            break
        else:
            return None

        line = self._lines[self._pos]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if stripped.startswith('- ') or stripped == '-':
            return self._parse_sequence(indent)
        else:
            return self._parse_mapping(indent)

    # ------------------------------------------------------------------
    # Sequence: lines starting with '- '
    # ------------------------------------------------------------------

    def _parse_sequence(self, seq_indent: int) -> list:
        result = []
        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            stripped = line.lstrip()
            if not stripped or stripped.startswith('#'):
                self._pos += 1
                continue
            indent = len(line) - len(stripped)
            if indent < seq_indent:
                break
            if indent > seq_indent:
                # Unexpected deeper indent outside a sequence item.
                raise YAMLError(
                    f"line {self._pos + 1}: unexpected indent in sequence"
                )
            if not (stripped.startswith('- ') or stripped == '-'):
                break  # End of this sequence; caller will handle.

            # Consume the '- ' prefix.
            self._pos += 1
            after_dash = stripped[2:].strip() if stripped.startswith('- ') else ''

            if not after_dash:
                # Value is on next line(s) — nested block.
                value = self._parse_block(seq_indent + 2)
            else:
                # Inline value after the dash.
                if after_dash.startswith('{') or after_dash.startswith('['):
                    raise YAMLError(
                        f"line {self._pos}: flow style not supported"
                    )
                # Could be start of a mapping ("key: value") or a scalar.
                if ':' in after_dash and not after_dash.startswith('"') and not after_dash.startswith("'"):
                    colon_pos = after_dash.index(':')
                    # Make sure it is a mapping key (not a URL scheme like http://)
                    if colon_pos + 1 < len(after_dash) and after_dash[colon_pos + 1] == '/':
                        value = _parse_scalar(after_dash)
                    else:
                        # Inline mapping inside sequence item — synthesise a line.
                        # We push back and re-parse as a mapping at seq_indent+2.
                        # Trick: insert a synthetic indented line.
                        synthetic = ' ' * (seq_indent + 2) + after_dash
                        self._lines.insert(self._pos, synthetic)
                        value = self._parse_mapping(seq_indent + 2)
                else:
                    value = _parse_scalar(after_dash)
            result.append(value)
        return result

    # ------------------------------------------------------------------
    # Mapping: lines of the form 'key: value'
    # ------------------------------------------------------------------

    def _parse_mapping(self, map_indent: int) -> dict:
        result: dict = {}
        while self._pos < len(self._lines):
            line = self._lines[self._pos]
            stripped = line.lstrip()
            if not stripped or stripped.startswith('#'):
                self._pos += 1
                continue
            indent = len(line) - len(stripped)
            if indent < map_indent:
                break
            if indent > map_indent:
                raise YAMLError(
                    f"line {self._pos + 1}: unexpected indent {indent} (expected {map_indent})"
                )
            if stripped.startswith('- ') or stripped == '-':
                break  # Sequence at this level — caller handles.

            # Parse "key: value" (or "key:" with value on next line).
            key, _, rest = _split_mapping_line(stripped, self._pos + 1)
            self._pos += 1
            rest_stripped = rest.strip()

            if not rest_stripped:
                # Value on next line(s).
                value = self._parse_block(map_indent + 2)
                if value is None:
                    value = None
            elif rest_stripped.startswith('{') or rest_stripped.startswith('['):
                raise YAMLError(
                    f"line {self._pos}: flow style not supported"
                )
            else:
                # Strip inline comment.
                rest_stripped = _strip_inline_comment(rest_stripped)
                value = _parse_scalar(rest_stripped)

            result[key] = value
        return result


# ---------------------------------------------------------------------------
# Line-level helpers
# ---------------------------------------------------------------------------

def _split_mapping_line(stripped: str, lineno: int) -> tuple:
    """Split 'key: rest' → (key, ':', rest).

    Handles quoted keys. Raises YAMLError if no colon separator found.
    """
    if stripped.startswith('"') or stripped.startswith("'"):
        quote = stripped[0]
        end_q = stripped.index(quote, 1)
        key = stripped[1:end_q]
        after = stripped[end_q + 1:].lstrip()
        if not after.startswith(':'):
            raise YAMLError(f"line {lineno}: expected ':' after quoted key")
        return key, ':', after[1:]
    colon = stripped.find(': ')
    if colon == -1:
        # Key-only line (value is None / next line).
        if stripped.endswith(':'):
            return stripped[:-1], ':', ''
        raise YAMLError(f"line {lineno}: no ':' separator in mapping line: {stripped!r}")
    return stripped[:colon], ':', stripped[colon + 1:]


def _strip_inline_comment(s: str) -> str:
    """Remove trailing '# comment' from a scalar value string."""
    # Only strip if '#' is preceded by whitespace (not inside a string).
    i = 0
    in_quote = None
    while i < len(s):
        c = s[i]
        if in_quote:
            if c == in_quote and (i == 0 or s[i - 1] != '\\'):
                in_quote = None
        else:
            if c in ('"', "'"):
                in_quote = c
            elif c == '#' and i > 0 and s[i - 1] in (' ', '\t'):
                return s[:i].rstrip()
        i += 1
    return s


def _parse_scalar(s: str):
    """Convert a YAML scalar string to a Python value."""
    s = s.strip()
    # Null
    if s in ('null', 'Null', 'NULL', '~', ''):
        return None
    # Bool
    if s.lower() in _BOOL_TRUE:
        return True
    if s.lower() in _BOOL_FALSE:
        return False
    # Quoted string
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    # Integer
    try:
        return int(s, 0)
    except (ValueError, OverflowError):
        pass
    # Float
    try:
        return float(s)
    except ValueError:
        pass
    # Plain string
    return s
