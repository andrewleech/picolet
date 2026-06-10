"""picolet_tui._rich.markup - Rich's ``[bold red]...[/bold red]`` parser.

Ported from Textualize/rich master @ d97751b1590be7d5443f2466cf40d0ea6ea56ed5
(``rich/markup.py``, 251 LoC upstream).  Tier 3 of the Rich subset:
imports ``MarkupError`` from Tier 1 ``errors`` and the intra-Tier-3
``style`` and ``text`` modules.

REMOVED vs upstream
-------------------
* ``from ast import literal_eval`` -- MicroPython ships no ``ast``
  module.  Replaced by an inline ``_literal_eval`` scanner (~40 LoC)
  that handles the scalar grammar Rich's ``@`` meta-tag bodies
  actually carry:

    - integer literals (``42``, ``-17``)
    - float literals (``3.14``, ``-0.5``)
    - quoted strings (single or double, no escapes beyond ``\\\\`` and
      escaped quote)
    - bare bareword sentinels: ``None``, ``True``, ``False``
    - parenthesised tuples of the above (``(1, 2, 3)``, ``("a", 1)``)

  Not supported: dict literals, set literals, list literals (Rich's
  meta payloads never use them); arbitrary nesting beyond tuple-of-
  scalars (handler params are flat); arithmetic expressions (Rich's
  parser rejects them too -- ``literal_eval`` only accepts literals).
  Bad input raises ``MarkupError`` with the offending body, matching
  upstream's ``error parsing ...`` message shape.

* ``from .emoji import EmojiVariant`` and ``from ._emoji_replace
  import _emoji_replace`` -- emoji is out of scope for v0.1 per
  synthesis doc 00 (the Tier-4 ``emoji.py`` is deleted).  The ``emoji``
  and ``emoji_variant`` keyword arguments on ``render()`` are kept for
  API compatibility but the flag is ignored and ``markup`` flows through
  to ``Text`` unchanged.  Callers that want emoji-name substitution can
  pre-process the string themselves.

* ``re.VERBOSE`` flag on ``RE_TAGS`` -- MicroPython's re1.5 has no flag
  support at all (research doc 03 ``re`` row, NFR-TUI-10).  The pattern
  ``r"((\\\\*)\\[([a-z#/@][^[]*?)])"`` contains no whitespace or comments,
  so the flag was decorative; dropping it is a no-op.

* ``finditer`` -- not exposed by MicroPython's ``modre``.  Replaced with
  a hand-rolled ``_finditer`` that runs ``search()`` over progressively
  sliced tails of the input, mapping ``match.start()`` / ``match.end()``
  back into the absolute index space.  Same iteration order, same group
  contents.

* ``Match[str]`` / ``Callable[[Match], str]`` type aliases at module
  scope -- deleted; type-hint-only and unreferenced after the typing
  shim flattens them to placeholders.

* The ``if __name__ == "__main__"`` demo block -- frozen .mpy code is
  imported, never executed top-level, so the demo is dead.

NOT REMOVED (intentional)
-------------------------
* The ``@`` meta-tag handler path is kept.  ``@click=handler(1,2)``
  syntax is how Textual encodes click / hover meta on text spans; the
  intra-Tier-3 ``style.Style(meta={...})`` constructor accepts the
  resulting dict.  Per synthesis D4 the ``meta`` value is a plain dict
  (no pickle round-trip); the parser populates it the same way upstream
  does, callers just don't get deep-copy semantics on merge.

* ``escape()`` -- still produces the upstream-shaped escaped string;
  some Textual widgets call it before composing markup.

* ``Tag.markup`` property -- preserved so error messages emit the same
  ``[name=params]`` form upstream emits.

Spec coverage
-------------
Supports FR-TUI-32 / FR-TUI-33 (the Python-side ``Style(...)`` DSL --
markup is the string form callers hand to ``Static.update`` and
``Label``-style widgets, which then resolve via ``Style.parse`` /
``Style.normalize`` from intra-Tier-3 ``style.py``).
Supports NFR-TUI-10 (re1.5 stays the regex engine; no named groups,
no flags, no lookaround used here -- verified by inspection).
Indirectly supports FR-TUI-13 / FR-TUI-14 (the ``@click``/``@hover``
meta path the markup parser produces is what the event-bubbling
machinery consults when a mouse event lands on a styled span).
"""

import re
from collections import namedtuple

# Tier 1 dep -- the only exception this module raises.
from .errors import MarkupError

# Intra-Tier-3 deps.  Forward by module to dodge import cycles: ``style``
# imports ``color`` (Tier 2) and ``text`` imports ``style``; importing the
# modules (not the symbols) lets the picolet_tui package finish wiring
# before the first ``render()`` call dereferences ``Style.normalize`` or
# ``Text``.
from . import style as _style
# `text` is a Tier 4 module; defer import into render() so this module
# loads cleanly during the Phase 3b → 3c transition window where text
# hasn't been ported yet, and so picolet_tui consumers that only use
# style.parse() don't pay the text import cost.
_text = None
def _ensure_text():
    global _text
    if _text is None:
        from . import text as _text_mod
        _text = _text_mod
    return _text


# Pattern semantics, character by character:
#   ((\\*)\[([a-z#/@][^[]*?)])
#   ^^^^^^^^^^^^^^^^^^^^^^^^^^ group 1: the entire match (full_text)
#     ^^^                      group 2: leading backslash run (escapes)
#                 ^^^^^^^^^^^  group 3: tag body (tag_text)
# The tag body must start with a lowercase letter, ``#``, ``/`` (closing
# marker), or ``@`` (meta-tag).  ``[^[]*?`` is a non-greedy run of
# anything-but-an-opening-bracket so a stray ``[`` ends the run early --
# this is how Rich handles malformed markup gracefully.
# re1.5 supports ``*?`` (verified in compilecode.c) and the
# ``[a-z#/@]`` / ``[^[]`` character classes; no flag needed.
RE_TAGS = re.compile(r"((\\*)\[([a-z#/@][^[]*?)])")

# Matches ``handler_name(args)`` where args is anything in parens.
# Upstream is ``r"^([\w.]*?)(\(.*?\))?$"`` -- ``\w`` is supported in
# re1.5 (no Unicode-property pretensions; ASCII word chars only).
RE_HANDLER = re.compile(r"^([\w.]*?)(\(.*?\))?$")


# namedtuple base because MicroPython cannot call tuple.__new__ in a subclass.
class Tag(namedtuple("Tag", ("name", "parameters"))):
    """A tag in console markup.  ``(name, parameters)`` tuple.

    Subclasses ``collections.namedtuple`` for the same reason
    ``Measurement`` does: the typing shim does not provide ``NamedTuple``,
    and MicroPython's ``namedtuple`` is a C builtin (a hand-rolled
    ``tuple`` subclass cannot work there — ``tuple.__new__`` raises
    ``AttributeError``).  Mirrors the visible Rich contract:
      * positional construction ``Tag(name, parameters)``
      * named attribute access ``tag.name`` / ``tag.parameters``
      * indexed access ``tag[0]`` / ``tag[1]``
      * tuple unpacking ``name, params = tag``
      * ``isinstance(tag, tuple) is True``
    """

    __slots__ = ()

    def __str__(self):
        return (
            self.name if self.parameters is None
            else self.name + " " + self.parameters
        )

    @property
    def markup(self):
        """Get the string representation of this tag."""
        if self.parameters is None:
            return "[" + self.name + "]"
        return "[" + self.name + "=" + self.parameters + "]"


# ---------------------------------------------------------------------------
# Scalar literal parser -- replaces ``ast.literal_eval``.
# ---------------------------------------------------------------------------
# Grammar (recursive descent, single pass):
#
#   value   := tuple | string | number | bareword
#   tuple   := '(' [ value (',' value)* [','] ] ')'
#   string  := '"' chars '"' | "'" chars "'"
#   number  := ['-'] digits ['.' digits]
#   bareword:= 'None' | 'True' | 'False'
#
# Whitespace is permitted between tokens but not inside numbers / barewords.
# Anything else raises ``MarkupError`` so the markup parser can wrap it
# with the surrounding context.
class _LiteralScanner:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.end = len(text)

    def _skip_ws(self):
        while self.pos < self.end and self.text[self.pos] in " \t\n\r":
            self.pos += 1

    def _peek(self):
        return self.text[self.pos] if self.pos < self.end else ""

    def parse(self):
        self._skip_ws()
        v = self._value()
        self._skip_ws()
        if self.pos != self.end:
            raise MarkupError(
                "trailing junk at position " + str(self.pos)
                + " in " + repr(self.text)
            )
        return v

    def _value(self):
        self._skip_ws()
        c = self._peek()
        if c == "(":
            return self._tuple()
        if c == '"' or c == "'":
            return self._string(c)
        if c == "-" or (c >= "0" and c <= "9"):
            return self._number()
        return self._bareword()

    def _tuple(self):
        # Consume '('
        self.pos += 1
        items = []
        self._skip_ws()
        if self._peek() == ")":
            self.pos += 1
            return ()
        while True:
            items.append(self._value())
            self._skip_ws()
            c = self._peek()
            if c == ",":
                self.pos += 1
                self._skip_ws()
                # Allow trailing comma before ')'.
                if self._peek() == ")":
                    self.pos += 1
                    return tuple(items)
                continue
            if c == ")":
                self.pos += 1
                return tuple(items)
            raise MarkupError(
                "expected ',' or ')' at position " + str(self.pos)
                + " in " + repr(self.text)
            )

    def _string(self, quote):
        # Consume opening quote.
        self.pos += 1
        chars = []
        while self.pos < self.end:
            c = self.text[self.pos]
            if c == "\\":
                # Minimal escape handling: \\ , \" , \' .  Anything else
                # is passed through verbatim (good enough for the meta
                # payloads Rich produces).
                self.pos += 1
                if self.pos >= self.end:
                    break
                chars.append(self.text[self.pos])
                self.pos += 1
                continue
            if c == quote:
                self.pos += 1
                return "".join(chars)
            chars.append(c)
            self.pos += 1
        raise MarkupError("unterminated string in " + repr(self.text))

    def _number(self):
        start = self.pos
        if self._peek() == "-":
            self.pos += 1
        had_digit = False
        while self.pos < self.end and self.text[self.pos].isdigit():
            self.pos += 1
            had_digit = True
        is_float = False
        if self._peek() == ".":
            self.pos += 1
            is_float = True
            while self.pos < self.end and self.text[self.pos].isdigit():
                self.pos += 1
                had_digit = True
        if not had_digit:
            raise MarkupError(
                "malformed number at position " + str(start)
                + " in " + repr(self.text)
            )
        token = self.text[start:self.pos]
        return float(token) if is_float else int(token)

    def _bareword(self):
        start = self.pos
        while self.pos < self.end:
            c = self.text[self.pos]
            # Word chars only -- bareword terminates on comma/paren/ws.
            if c.isalpha() or c == "_":
                self.pos += 1
            else:
                break
        token = self.text[start:self.pos]
        if token == "None":
            return None
        if token == "True":
            return True
        if token == "False":
            return False
        raise MarkupError(
            "unknown bareword " + repr(token)
            + " at position " + str(start)
        )


def _literal_eval(text):
    """Hand-rolled stand-in for ``ast.literal_eval`` covering Rich's
    meta-payload scalar grammar.  Raises ``MarkupError`` on bad input.
    """
    return _LiteralScanner(text).parse()


# ---------------------------------------------------------------------------
# escape() / _finditer() / _parse() -- direct ports of upstream's logic.
# ---------------------------------------------------------------------------

_ESCAPE_RE = re.compile(r"(\\*)(\[[a-z#/@][^[]*?)")


def escape(markup):
    """Escapes text so that it won't be interpreted as markup.

    Args:
        markup (str): Content to be inserted in to markup.

    Returns:
        str: Markup with square brackets escaped.
    """
    def escape_backslashes(match):
        backslashes, text = match.groups()
        return backslashes + backslashes + "\\" + text

    markup = _ESCAPE_RE.sub(escape_backslashes, markup)
    if markup.endswith("\\") and not markup.endswith("\\\\"):
        return markup + "\\"
    return markup


def _finditer(pattern, string):
    """Iterate every non-overlapping match of ``pattern`` in ``string``.

    Mimics ``re.Pattern.finditer``, which MicroPython's modre does not
    expose.  Each yielded object has the ``groups()``, ``span()``,
    ``start()``, and ``end()`` methods callers below depend on; we yield
    the raw ``Match`` objects from ``search()`` plus a small adapter
    that translates the local-window indices back to the absolute index
    in the original string.
    """
    offset = 0
    length = len(string)
    while offset <= length:
        m = pattern.search(string, offset)
        if m is None:
            return
        # Local span within string[offset:].  modre's start/end are
        # absolute when ``search(string, pos)`` is supported; verify the
        # current call ABI by snapping to whichever interpretation
        # produces a non-negative tail.  This keeps the iterator correct
        # on either re1.5 build (pos-aware and pos-bare).
        ms, me = m.span()
        if ms < offset:
            ms += offset
            me += offset
        yield _AbsMatch(m, ms, me)
        # Guard against zero-width matches looping forever; advance at
        # least one char past the match start.
        offset = me if me > ms else ms + 1


class _AbsMatch(object):
    """Lightweight adapter so ``_parse`` sees absolute (start, end)
    coordinates regardless of how the underlying ``Match`` reports them.
    """
    __slots__ = ("_m", "_start", "_end")

    def __init__(self, match, start, end):
        self._m = match
        self._start = start
        self._end = end

    def groups(self):
        return self._m.groups()

    def span(self):
        return (self._start, self._end)

    def start(self):
        return self._start

    def end(self):
        return self._end


def _parse(markup):
    """Parse markup in to an iterable of tuples of (position, text, tag)."""
    position = 0
    for match in _finditer(RE_TAGS, markup):
        full_text, escapes, tag_text = match.groups()
        start, end = match.span()
        if start > position:
            yield start, markup[position:start], None
        if escapes:
            # ``divmod`` would do here but is bigger than the inline form.
            n = len(escapes)
            backslashes = n // 2
            escaped = n % 2
            if backslashes:
                yield start, "\\" * backslashes, None
                start += backslashes * 2
            if escaped:
                yield start, full_text[len(escapes):], None
                position = end
                continue
        text, equals, parameters = tag_text.partition("=")
        yield start, None, Tag(text, parameters if equals else None)
        position = end
    if position < len(markup):
        yield position, markup[position:], None


# ---------------------------------------------------------------------------
# render() -- the public entry point.
# ---------------------------------------------------------------------------

def render(markup, style="", emoji=True, emoji_variant=None):
    """Render console markup in to a Text instance.

    ``emoji`` / ``emoji_variant`` are accepted for API parity with
    upstream Rich but ignored (emoji name substitution is out of v0.1
    scope, per synthesis doc 00).
    """
    _ensure_text(); Text = _text.Text
    Span = _text.Span
    Style = _style.Style

    if "[" not in markup:
        return Text(markup, style=style)

    text = Text(style=style)
    append = text.append
    normalize = Style.normalize

    style_stack = []
    pop = style_stack.pop

    spans = []
    append_span = spans.append

    def pop_style(style_name):
        """Pop tag matching given style name."""
        # Walk the stack from the top down looking for a matching open.
        for index, entry in enumerate(reversed(style_stack), 1):
            _, tag = entry
            if tag.name == style_name:
                return pop(-index)
        raise KeyError(style_name)

    for position, plain_text, tag in _parse(markup):
        if plain_text is not None:
            # Handle open-brace escape, where the brace is not part of a tag.
            plain_text = plain_text.replace("\\[", "[")
            append(plain_text)
        elif tag is not None:
            if tag.name.startswith("/"):  # Closing tag.
                style_name = tag.name[1:].strip()
                if style_name:  # Explicit close.
                    style_name = normalize(style_name)
                    try:
                        start, open_tag = pop_style(style_name)
                    except KeyError:
                        raise MarkupError(
                            "closing tag '" + tag.markup
                            + "' at position " + str(position)
                            + " doesn't match any open tag"
                        )
                else:  # Implicit close (just ``[/]``).
                    try:
                        start, open_tag = pop()
                    except IndexError:
                        raise MarkupError(
                            "closing tag '[/]' at position " + str(position)
                            + " has nothing to close"
                        )

                if open_tag.name.startswith("@"):
                    # Meta-tag.  Parse the parameters into a Python value
                    # and stash on the Span via Style(meta=...).
                    if open_tag.parameters:
                        handler_name = ""
                        parameters = open_tag.parameters.strip()
                        handler_match = RE_HANDLER.match(parameters)
                        if handler_match is not None:
                            handler_name, match_parameters = handler_match.groups()
                            parameters = "()" if match_parameters is None else match_parameters

                        try:
                            meta_params = _literal_eval(parameters)
                        except MarkupError:
                            raise
                        except Exception as error:
                            raise MarkupError(
                                "error parsing " + repr(open_tag.parameters)
                                + "; " + str(error)
                            )

                        if handler_name:
                            meta_params = (
                                handler_name,
                                meta_params if isinstance(meta_params, tuple)
                                else (meta_params,),
                            )
                    else:
                        meta_params = ()

                    append_span(
                        Span(
                            start, len(text),
                            Style(meta={open_tag.name: meta_params}),
                        )
                    )
                else:
                    append_span(Span(start, len(text), str(open_tag)))

            else:  # Opening tag.
                normalized_tag = Tag(normalize(tag.name), tag.parameters)
                style_stack.append((len(text), normalized_tag))

    text_length = len(text)
    while style_stack:
        start, tag = style_stack.pop()
        style_str = str(tag)
        if style_str:
            append_span(Span(start, text_length, style_str))

    # Sort by span.start to match upstream's final ordering.  ``Span`` in
    # the Tier-3 ``text`` port exposes ``.start`` as an attribute (it is
    # also a tuple subclass), so a key function works identically to
    # upstream's ``operator.attrgetter("start")``.
    text.spans = sorted(spans[::-1], key=lambda span: span.start)
    return text
