"""picolet_tui._rich.padding -- Rich ``Padding`` renderable port.

Upstream
--------
Source : https://raw.githubusercontent.com/Textualize/rich/master/rich/padding.py
Pinned : Textualize/rich master @ 45b7cef96bea4f0b6aa0d38c6376cd2eb265bef8
         (141 LoC upstream).

Role
----
CSS-style margin/padding wrapper around another renderable.  Tier 4 in
the synthesis port plan; depends only on Tier 1-3 (``measure``,
``segment``, ``style`` plus the typing shim).  Used by FR-TUI-30..31
(compositor) for widget padding and by FR-TUI-43 (layout DSL) for
``margin: N N N N`` translation -- both are spec-required surfaces of
v0.1, see docs/tui/tui-v0.1-spec.md.

Lives under NFR-TUI-19's ``_rich/`` 60 KiB romfs sub-budget.  No regex,
no caches, so NFR-TUI-6 (LRU <= 128) is trivially satisfied.

REMOVED vs upstream
-------------------
* ``JupyterMixin`` base class.  The mixin provides ``_repr_mimebundle_``
  for Jupyter's display protocol, which is irrelevant under MicroPython
  (no IPython, no Jupyter kernel) and pulls in ``rich.jupyter`` plus its
  segment-to-HTML pipeline -- explicitly out of scope per the synthesis
  trim list (research/02-rich-subset.md).  ``Padding`` is therefore a
  plain object; ``__rich_console__`` / ``__rich_measure__`` are picked
  up structurally by the compositor protocol (see ``_rich.protocol``).
* ``Padding.indent`` classmethod removed per task instructions; the one
  v0.1 caller (the layout DSL's ``indent:`` shorthand) constructs the
  tuple form directly.  Trimming ~12 LoC.
* ``Padding.from_str`` is not present in this upstream revision; no work
  to drop, but flagged here because the task description anticipated it.
* ``typing`` names route through ``picolet_tui._shims.typing`` -- the
  shim's placeholders evaluate ``Union[int, Tuple[int]]`` at import time
  without allocation (NFR-TUI-6).  ``PaddingDimensions`` therefore
  becomes a documentation-only alias bound to the placeholder.
* ``__main__`` smoke block dropped -- no ``rich.print`` in the runtime,
  and the demo would import ``Console`` which is post-Tier 4.

Spec mapping
------------
FR-TUI-30 (compositor renders Renderable trees), FR-TUI-31 (Segment is
the wire format), FR-TUI-43 (layout DSL padding/margin), NFR-TUI-19
(_rich/ romfs <= 60 KiB), NFR-TUI-6 (cache <= 128 -- N/A, no caches).
"""

# The typing imports route through the shim; ``Union`` / ``Tuple`` /
# ``Optional`` are _Placeholder singletons whose subscript evaluates to
# self, so the upstream annotation shapes survive verbatim.  Kept
# explicit (not ``from typing import *``) so the freezer sees each name.
from picolet_tui._shims.typing import (
    TYPE_CHECKING,
    List,
    Optional,
    Tuple,
    Union,
)

from picolet_tui._rich.measure import Measurement
from picolet_tui._rich.segment import Segment
from picolet_tui._rich.style import Style

if TYPE_CHECKING:  # always False under the shim; kept for IDE parity.
    # Forward-only references -- the Console / ConsoleOptions /
    # RenderableType / RenderResult types live in tier-5 ``console.py``
    # which has not been ported yet.  Importing them here would cycle.
    from picolet_tui._rich.console import (  # noqa: F401
        Console,
        ConsoleOptions,
        RenderableType,
        RenderResult,
    )


# Documentation alias.  At runtime this resolves to the typing-shim
# placeholder; static checkers will see the upstream-equivalent shape.
PaddingDimensions = Union[int, Tuple[int], Tuple[int, int], Tuple[int, int, int, int]]


class Padding:
    """Draw space around content.

    Args:
        renderable (RenderableType): String or other renderable.
        pad (Union[int, Tuple[int]]): Padding for top, right, bottom, and
            left borders.  May be specified with 1, 2, or 4 integers
            (CSS style).
        style (Union[str, Style], optional): Style for padding
            characters.  Defaults to ``"none"``.
        expand (bool, optional): Expand padding to fit available width.
            Defaults to ``True``.
    """

    def __init__(
        self,
        renderable,
        pad=(0, 0, 0, 0),
        *,
        style="none",
        expand=True,
    ):
        self.renderable = renderable
        self.top, self.right, self.bottom, self.left = self.unpack(pad)
        self.style = style
        self.expand = expand

    @staticmethod
    def unpack(pad):
        """Unpack padding specified in CSS style."""
        # Order matters: ``isinstance(pad, int)`` must run before the
        # ``len()`` arm because a 1-tuple ``(N,)`` would also satisfy
        # ``len(pad) == 1`` but does not have an ``__int__``.
        if isinstance(pad, int):
            return (pad, pad, pad, pad)
        if len(pad) == 1:
            _pad = pad[0]
            return (_pad, _pad, _pad, _pad)
        if len(pad) == 2:
            pad_top, pad_right = pad
            return (pad_top, pad_right, pad_top, pad_right)
        if len(pad) == 4:
            top, right, bottom, left = pad
            return (top, right, bottom, left)
        raise ValueError(
            "1, 2 or 4 integers required for padding; {} given".format(len(pad))
        )

    def __repr__(self):
        return "Padding({!r}, ({},{},{},{}))".format(
            self.renderable, self.top, self.right, self.bottom, self.left
        )

    def __rich_console__(self, console, options):
        # Upstream resolves ``self.style`` via ``console.get_style`` so a
        # string like ``"on blue"`` becomes a real Style.  We mirror that
        # exactly; the compositor's Console implementation must provide
        # ``get_style`` (FR-TUI-37 default-style merge).
        style = console.get_style(self.style)
        if self.expand:
            width = options.max_width
        else:
            # ``Measurement.get`` is the public Tier-2 entry point that
            # picks ``__rich_measure__`` if present, else falls back to
            # string measurement.
            width = min(
                Measurement.get(console, options, self.renderable).maximum
                + self.left
                + self.right,
                options.max_width,
            )
        render_options = options.update_width(width - self.left - self.right)
        if render_options.height is not None:
            render_options = render_options.update_height(
                height=render_options.height - self.top - self.bottom
            )
        # ``render_lines`` returns a list of segment-lists, one per row;
        # ``pad=True`` makes the compositor right-pad short lines so the
        # background style covers the full padded width.
        lines = console.render_lines(
            self.renderable, render_options, style=style, pad=True
        )
        _Segment = Segment

        left = _Segment(" " * self.left, style) if self.left else None
        right = (
            [_Segment("{}".format(" " * self.right), style), _Segment.line()]
            if self.right
            else [_Segment.line()]
        )
        blank_line = None  # type: Optional[List[Segment]]
        if self.top:
            blank_line = [_Segment("{}\n".format(" " * width), style)]
            # ``yield from`` over a list times N replicates the row N
            # times; matches upstream's blank_line * self.top pattern.
            for _ in range(self.top):
                yield from blank_line
        if left:
            for line in lines:
                yield left
                yield from line
                yield from right
        else:
            for line in lines:
                yield from line
                yield from right
        if self.bottom:
            blank_line = blank_line or [_Segment("{}\n".format(" " * width), style)]
            for _ in range(self.bottom):
                yield from blank_line

    def __rich_measure__(self, console, options):
        max_width = options.max_width
        extra_width = self.left + self.right
        # If the horizontal padding eats the entire viewport there is no
        # room left for the child renderable; return a degenerate
        # measurement so the compositor stops recursing.
        if max_width - extra_width < 1:
            return Measurement(max_width, max_width)
        measure_min, measure_max = Measurement.get(console, options, self.renderable)
        measurement = Measurement(measure_min + extra_width, measure_max + extra_width)
        measurement = measurement.with_maximum(max_width)
        return measurement
