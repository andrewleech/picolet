"""picolet_tui._rich.highlighter - Rich's pluggable text highlighter hook.

Ported from Textualize/rich master @ 624b30c5979ec602291a2fff0be8d222060f86d1
(``rich/highlighter.py``, 232 LoC upstream).  Tier 4 of the Rich subset.

The only highlighter actually wired into Textual's render path under
v0.1 is ``NullHighlighter`` (the no-op).  Per research doc 02 §Rich-
subset (row ``highlighter.py``), we keep ``NullHighlighter`` and stub
the regex-driven highlighters as ``NotImplementedError`` -- their
upstream patterns lean on MicroPython-incompatible ``re`` features
(named groups ``(?P<name>...)``, lookbehind ``(?<!...)``, inline-cond
back-references ``(?(hyphen)-)``, the ``re.X`` ``VERBOSE`` flag).
Rewriting them as hand scanners would burn ~600 LoC for a feature path
no v0.1 widget walks; the synthesis (R2 console-trim) calls them out
as drop-on-port candidates.

REMOVED vs upstream
-------------------
* ``RegexHighlighter`` -- iterates ``self.highlights`` and calls
  ``text.highlight_regex``.  ``Text.highlight_regex`` is itself a
  regex-driven span injector; both rely on full CPython ``re``.
  Replaced with a class that raises ``NotImplementedError`` on
  ``highlight()``.  Construction is permitted so isinstance checks and
  subclass declarations elsewhere don't blow up at import time.

* ``ReprHighlighter`` -- ~10 named-group patterns covering ipv4, ipv6,
  eui48/64, uuid, call, number, path, str, url, bool/None.  MicroPython
  ``re`` rejects named groups outright, and several of the patterns
  also use negative lookbehind (``(?<!\\w)``).  Stubbed: subclasses
  ``RegexHighlighter`` so the inheritance chain stays valid; class
  attributes preserved as empty lists.

* ``JSONHighlighter`` -- same family, plus a ``re.finditer`` post-pass
  using ``(?P<str>...)`` and ``(?<!\\\\w)``.  Stubbed.  Note that
  ``rich.json``'s pretty-print path *would* import this, but ``json``
  is itself out of scope for v0.1 (no ``rich.json`` shim).

* ``ISO8601Highlighter`` -- a wall of named groups and inline
  conditionals (``(?(hyphen)-)``).  Stubbed.

* ``_combine_regex`` helper -- only used by the stubbed highlighters.
  Kept as a one-line ``"|".join`` for source-compat in case anything
  imports it from this module.

* ``abc.ABC`` / ``abc.abstractmethod`` -- MicroPython ships no ``abc``
  module.  ``Highlighter`` becomes a plain class; ``highlight()`` is a
  ``def`` that raises ``NotImplementedError`` instead of being marked
  ``@abstractmethod``.  Subclass discipline is the caller's problem;
  ``NullHighlighter`` overrides correctly.

* The ``if __name__ == "__main__":`` demo block -- pulls in
  ``rich.console.Console`` for an interactive smoke print; not
  shippable bytecode.

* ``typing.ClassVar`` / ``typing.Sequence`` / ``typing.Union`` -- the
  picolet_tui ``_shims.typing`` re-export stubs them, but per the Rich
  port style elsewhere (markup, style) we drop the imports outright
  and use bare annotations only where they appear in __slots__-style
  attribute defaults.

Spec refs
---------
FR-TUI-1..6 (App entry surfaces) -- no direct touch; highlighter sits
behind Console's render pipeline, which Textual instantiates with the
default ``NullHighlighter`` for v0.1.
NFR-TUI-1 (runtime romfs budget) / NFR-TUI-20 (≤120 KiB frozen .mpy)
-- this trim drops ~150 LoC of regex blob from the bytecode footprint.
"""

# ``text.Text`` is imported lazily inside ``__call__`` because
# Tier-4 ``text.py`` may not yet exist on disk in parallel-port builds
# and to avoid a hard cycle: ``text`` imports ``style`` which imports
# the markup parser, which in turn imports ``errors`` -- adding
# highlighter to that head-of-module chain risks circularity if
# ``text`` ever wants to expose a default highlighter for repr.


def _combine_regex(*regexes):
    """Combine regexes with ``|``.  Kept for source-compat only --
    the stubbed highlighters below never actually feed the result to
    the ``re`` engine on MicroPython.
    """
    return "|".join(regexes)


class Highlighter:
    """Base class for highlighters.

    Upstream uses ``abc.ABC`` + ``@abstractmethod``; MicroPython has
    neither, so this is a plain class and ``highlight()`` raises
    ``NotImplementedError``.  ``NullHighlighter`` overrides it with a
    no-op, which is the only behaviour v0.1 needs.
    """

    def __call__(self, text):
        # Lazy import: see file-head note.
        from .text import Text

        if isinstance(text, str):
            highlight_text = Text(text)
        elif isinstance(text, Text):
            highlight_text = text.copy()
        else:
            raise TypeError("str or Text instance required, not %r" % (text,))
        self.highlight(highlight_text)
        return highlight_text

    def highlight(self, text):
        raise NotImplementedError("Highlighter subclass must implement highlight()")


class NullHighlighter(Highlighter):
    """A highlighter that does nothing -- the v0.1 default.

    Textual's Console picks this up when no user highlighter is
    configured, which matches research doc 02's "keep NullHighlighter"
    decision exactly.
    """

    def highlight(self, text):
        # Intentional no-op.  Matches upstream behaviour.
        return None


class RegexHighlighter(Highlighter):
    """Stub.  Upstream iterates ``self.highlights`` and dispatches to
    ``Text.highlight_regex`` -- both rely on full CPython ``re``
    (named groups, lookbehind).  MicroPython's ``re`` rejects those,
    and v0.1 has no widget that asks for regex highlighting.
    """

    highlights = []
    base_style = ""

    def highlight(self, text):
        raise NotImplementedError(
            "RegexHighlighter is not available on MicroPython "
            "(named-group / lookbehind regex unsupported)"
        )


class ReprHighlighter(RegexHighlighter):
    """Stub.  See ``RegexHighlighter``."""

    base_style = "repr."
    highlights = []


class JSONHighlighter(RegexHighlighter):
    """Stub.  See ``RegexHighlighter``."""

    base_style = "json."
    highlights = []
    JSON_STR = ""
    JSON_WHITESPACE = {" ", "\n", "\r", "\t"}


class ISO8601Highlighter(RegexHighlighter):
    """Stub.  See ``RegexHighlighter``."""

    base_style = "iso8601."
    highlights = []
