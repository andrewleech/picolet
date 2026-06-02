"""picolet_tui._rich.containers - Rich's renderable container sequences.

Ported from Textualize/rich master @ 46cebbb032f920eb096efbaf23cdc6fe9dd541f7
(``rich/containers.py``, 167 LoC upstream).  Tier 4 of the Rich subset:
imports Tier 2 ``measure`` (for ``Measurement`` and ``measure_renderables``)
and Tier 2 ``cells`` (for ``cell_len``).

This module collects three small composite renderables used pervasively by
the trimmed Rich subset and by Textual's compositor:

* ``Renderables`` — sequence wrapper whose ``__rich_console__`` yields its
  children verbatim; the measurement is the union of child measurements.
* ``Lines`` — sequence of ``Text`` lines with in-place ``justify`` over
  ``left``/``center``/``right``/``full``; used by ``Text.wrap``.
* ``Group`` — composite renderable matching upstream ``rich.console.Group``.
  Upstream collocates ``Group`` with ``console.py`` (which we trim heavily);
  hoisting it here keeps the Group/decorator pair available without forcing
  callers to import the full ``console`` module — the rich-subset research
  doc (02 §Tier 2) explicitly notes ``containers.py`` is the natural home
  for portable sequence-of-renderables types.

REMOVED vs upstream
-------------------
* ``from itertools import zip_longest`` — used only inside ``Lines.justify``
  for the ``full`` justification branch's word-pair walk; replaced with an
  inline pad-shorter-with-None generator (``_zip_longest_pair``).  Avoids
  pulling in the full ``itertools`` shim for one call site and matches
  upstream's behaviour for the ``zip_longest(words, words[1:])`` shape
  (always a 2-iterable zip; CPython's ``zip_longest`` defaults
  ``fillvalue=None``, so the pad is ``None``, which the loop body already
  handles).

* ``from typing import TYPE_CHECKING, Iterable, Iterator, List, Optional,
  TypeVar, Union, overload`` — routed through ``picolet_tui._shims.typing``;
  every name resolves to a no-op placeholder.  The ``TYPE_CHECKING`` block
  importing ``console.Console`` / ``ConsoleOptions`` / ``JustifyMethod`` /
  ``OverflowMethod`` / ``RenderResult`` / ``RenderableType`` and
  ``text.Text`` is dropped entirely: ``TYPE_CHECKING`` is always ``False``
  in the shim, so the block was dead code, and keeping the names quoted
  forward-references inside annotations buys nothing under MicroPython.

* ``@overload`` decorators on ``Lines.__getitem__`` — kept as a single
  signature without the overloaded variants.  Upstream uses ``@overload``
  purely for static checkers; runtime dispatch is just ``self._lines[index]``
  which already handles both int and slice via list's own protocol.

* ``from .text import Text`` (inside ``Lines.justify``) — kept as a *lazy*
  import (matches upstream).  The ``text`` module is a separate Tier-3
  port; importing it eagerly at module scope here would either force a
  Tier ordering violation or cycle with ``text.py``'s use of ``cells``.
  The lazy import only fires when ``justify(..., justify="full")`` runs,
  and the spec's text wrapping path always has ``text`` already imported
  by then (the caller is typically ``Text.wrap`` itself).

NOT REMOVED (intentional)
-------------------------
* All four ``Lines.justify`` branches (``left``/``center``/``right``/
  ``full``).  The Textual ``Static`` widget and Rich ``Panel``/``Table``
  body wrap go through this path; trimming any branch would silently
  regress alignment for the most common widget.

* ``Group`` keeps its ``fit`` parameter and the lazy ``renderables``
  property.  Textual's ``compose()`` yields ``Group`` instances with
  ``fit=False`` to fill panel width; the upstream measurement logic
  (return ``Measurement(max_width, max_width)`` when ``fit=False``) is
  preserved exactly.

Spec coverage:
  FR-TUI-13 (renderable protocol) — ``Renderables``/``Group`` participate
    in the same ``__rich_console__`` dispatch ``protocol.is_renderable``
    gates.
  FR-TUI-14 (rich-cast chain) — Group's children flow through the same
    ``rich_cast`` unwrap loop when rendered.
  NFR-TUI-6 (cache budget) — no ``lru_cache`` here; the containers are
    pure sequence wrappers with no memoisation.
  NFR-TUI-19 (frozen-bytes budget) — aims under ~150 LoC; the only
    non-trivial branch (``Lines.justify("full")``) is preserved verbatim.
"""

from picolet_tui._shims.typing import (
    Iterable,
    Iterator,
    List,
    Optional,
    TypeVar,
    Union,
)

from .cells import cell_len
from .measure import Measurement, measure_renderables

T = TypeVar("T")


def _zip_longest_pair(left, right):
    """Yield ``(left_i, right_i_or_None)`` pairs until ``left`` exhausts.

    Upstream uses ``itertools.zip_longest(words, words[1:])`` which always
    pads ``right`` with ``None`` because ``words[1:]`` is exactly one
    element shorter.  Hard-coded to two iterables — that is the only
    shape ``Lines.justify`` ever passes — and assumes ``left`` is the
    longer side, which the upstream call site guarantees.  Avoids the
    full ``itertools`` shim for a single call site.
    """
    right_iter = iter(right)
    for value in left:
        try:
            other = next(right_iter)
        except StopIteration:
            other = None
        yield value, other


class Renderables:
    """A list subclass which renders its contents to the console."""

    def __init__(self, renderables=None):
        self._renderables = (
            list(renderables) if renderables is not None else []
        )

    def __rich_console__(self, console, options):
        """Console render method to insert line-breaks."""
        yield from self._renderables

    def __rich_measure__(self, console, options):
        dimensions = [
            Measurement.get(console, options, renderable)
            for renderable in self._renderables
        ]
        if not dimensions:
            return Measurement(1, 1)
        _min = max(dimension.minimum for dimension in dimensions)
        _max = max(dimension.maximum for dimension in dimensions)
        return Measurement(_min, _max)

    def append(self, renderable):
        self._renderables.append(renderable)

    def __iter__(self):
        return iter(self._renderables)


class Lines:
    """A list subclass which can render to the console."""

    def __init__(self, lines=()):
        self._lines = list(lines)

    def __repr__(self):
        return "Lines({!r})".format(self._lines)

    def __iter__(self):
        return iter(self._lines)

    def __getitem__(self, index):
        # Upstream uses @overload for int vs slice; list's own dispatch
        # already handles both, so the single passthrough is correct.
        return self._lines[index]

    def __setitem__(self, index, value):
        self._lines[index] = value
        return self

    def __len__(self):
        return self._lines.__len__()

    def __rich_console__(self, console, options):
        """Console render method to insert line-breaks."""
        yield from self._lines

    def append(self, line):
        self._lines.append(line)

    def extend(self, lines):
        self._lines.extend(lines)

    def pop(self, index=-1):
        return self._lines.pop(index)

    def justify(self, console, width, justify="left", overflow="fold"):
        """Justify and overflow text to a given width.

        Args:
            console (Console): Console instance.
            width (int): Number of cells available per line.
            justify (str, optional): Default justify method for text:
                "left", "center", "full" or "right". Defaults to "left".
            overflow (str, optional): Default overflow for text: "crop",
                "fold", or "ellipsis". Defaults to "fold".
        """
        # Lazy import: text.py is a separate Tier-3 port and depends on
        # cells/measure itself, so a module-scope import would either
        # cycle or force a strict tier ordering at freeze time.  The
        # "full" branch is the only consumer.
        from .text import Text

        if justify == "left":
            for line in self._lines:
                line.truncate(width, overflow=overflow, pad=True)
        elif justify == "center":
            for line in self._lines:
                line.rstrip()
                line.truncate(width, overflow=overflow)
                line.pad_left((width - cell_len(line.plain)) // 2)
                line.pad_right(width - cell_len(line.plain))
        elif justify == "right":
            for line in self._lines:
                line.rstrip()
                line.truncate(width, overflow=overflow)
                line.pad_left(width - cell_len(line.plain))
        elif justify == "full":
            for line_index, line in enumerate(self._lines):
                if line_index == len(self._lines) - 1:
                    break
                words = line.split(" ")
                words_size = sum(cell_len(word.plain) for word in words)
                num_spaces = len(words) - 1
                spaces = [1 for _ in range(num_spaces)]
                index = 0
                if spaces:
                    while words_size + num_spaces < width:
                        spaces[len(spaces) - index - 1] += 1
                        num_spaces += 1
                        index = (index + 1) % len(spaces)
                tokens = []
                for index, (word, next_word) in enumerate(
                    _zip_longest_pair(words, words[1:])
                ):
                    tokens.append(word)
                    if index < len(spaces):
                        style = word.get_style_at_offset(console, -1)
                        next_style = next_word.get_style_at_offset(console, 0)
                        space_style = style if style == next_style else line.style
                        tokens.append(Text(" " * spaces[index], style=space_style))
                self[line_index] = Text("").join(tokens)


class Group:
    """Takes a group of renderables and renders them as a single renderable.

    Hoisted from upstream ``rich.console`` so the trimmed console.py port
    does not need to re-export it.  Identical public surface to
    ``rich.console.Group``: ``Group(*renderables, fit=True)``, ``.renderables``
    property, ``__rich_measure__`` and ``__rich_console__`` hooks.

    Args:
        renderables: Positional renderables to group.
        fit (bool, optional): Fit dimension of group to contents, or fill
            available space. Defaults to True.
    """

    def __init__(self, *renderables, fit=True):
        self._renderables = renderables
        self.fit = fit
        self._render = None

    @property
    def renderables(self):
        if self._render is None:
            self._render = list(self._renderables)
        return self._render

    def __rich_measure__(self, console, options):
        if self.fit:
            return measure_renderables(console, options, self.renderables)
        # Fill: claim the full available width.  Matches upstream Group;
        # Textual's compositor relies on this for fixed-width panels.
        return Measurement(options.max_width, options.max_width)

    def __rich_console__(self, console, options):
        yield from self.renderables
