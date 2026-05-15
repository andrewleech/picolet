# picolet_ui._toml — minimal TOML reader for [window] and [ui] tables.
#
# Why a hand-rolled parser instead of micropython-lib's full toml?
#
#   1. The runtime only reads two small tables: [window] and [ui].
#   2. The full toml library is ~12 KB frozen and pulls in re, datetime.
#   3. The size budget for the webview variant has headroom but every
#      Python module here is overhead for a CLI-style runtime that
#      already loads.
#
# Supported syntax (deliberately small):
#
#   [section]                section header (left-justified, no nesting)
#   key = "string"           double-quoted string
#   key = 'string'           single-quoted string (no escape processing)
#   key = 123                decimal integer
#   key = true / false       boolean
#   key = [1, 2, 3]          flat list of integers (used by size=[w,h])
#   key = ["a", "b"]         flat list of strings
#   # comment                line comment (ignored)
#   blank line               ignored
#
# NOT supported (deliberately):
#
#   nested tables, inline tables, multi-line strings, floats,
#   datetime, escape sequences inside strings.
#
# Returns a dict of section_name -> dict(key -> value).  Unknown
# sections are kept in the dict but picolet_ui only reads what it needs.

# Strip-on-import — keep size minimal.  __all__ is the public surface.
__all__ = ("loads",)


def loads(text):
    """Parse the TOML subset described above; return dict-of-dicts."""
    result = {}
    section = None
    current = None
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line or line[0] == "#":
            continue
        if line[0] == "[" and line[-1] == "]":
            section = line[1:-1].strip()
            current = {}
            result[section] = current
            continue
        if "=" not in line:
            continue  # silently drop garbage; the validator catches it upstream
        if current is None:
            # key=value before any section header — invalid TOML, skip.
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip trailing inline comment (only if not inside a quoted string).
        if "#" in val and not (val and val[0] in "\"'"):
            val = val.split("#", 1)[0].rstrip()
        current[key] = _parse_value(val)
    return result


def _parse_value(s):
    if not s:
        return ""
    c = s[0]
    if c == '"' and s[-1] == '"':
        # Minimal escape: \\ \" \n \t — JSON's subset.
        body = s[1:-1]
        return _unescape_dq(body)
    if c == "'" and s[-1] == "'":
        return s[1:-1]
    if c == "[" and s[-1] == "]":
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(p.strip()) for p in _split_list(inner)]
    if s == "true":
        return True
    if s == "false":
        return False
    # Try integer.
    try:
        return int(s)
    except ValueError:
        # Unknown shape — return the raw string.  The validator on the
        # build host is the authoritative gate; the runtime parser
        # tolerates spell-check-class errors.
        return s


def _unescape_dq(body):
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            if n == "n":
                out.append("\n")
            elif n == "t":
                out.append("\t")
            elif n == "\\":
                out.append("\\")
            elif n == '"':
                out.append('"')
            else:
                out.append(c)
                out.append(n)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _split_list(inner):
    """Split a flat list body on commas not inside quoted strings."""
    parts = []
    buf = []
    quote = None
    for c in inner:
        if quote is None and c in "\"'":
            quote = c
            buf.append(c)
        elif quote is not None and c == quote:
            quote = None
            buf.append(c)
        elif quote is None and c == ",":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    if buf:
        parts.append("".join(buf).strip())
    return parts
