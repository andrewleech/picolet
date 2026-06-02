"""picolet_tui._rich.align - Rich's Align renderable, ported.

Ported from Textualize/rich master @ 46cebbb032f920eb096efbaf23cdc6fe9dd541f7
(``rich/align.py``, 320 LoC upstream including the ``__main__`` demo and
the deprecated ``VerticalCenter`` shim).  Tier 4 of the Rich subset for
picolet-tui: depends on Tier 1/2 (``segment``, ``measure``) plus the
``constrain`` Tier-4 sibling, which is imported lazily so we tolerate
either ordering when parallel agents land Tier 4.

Spec mapping
------------
* FR-TUI-30..31 (compositor) - Align is used by the Static/Label render
  path when ``Style(align=...)`` resolves to ``"left" | "center" |
  "right"``; it is the canonical horizontal-padding renderable.
* FR-TUI-32 (Style DSL ``align`` kwarg) - the layout engine instantiates
  Align via the public constructor with the same triple of method
  values the TCSS parser will accept later.
* FR-TUI-65 (Rich test corpus) - the upstream tests for Align construct
  the class directly and inspect the produced segment stream, so the
  public surface (constructor signature, classmethods ``left`` /
  ``center`` / ``right``, ``__rich_console__``, ``__rich_measure__``)
  must match byte-for-byte against upstream.
* NFR-TUI-19 (60 KiB ``_rich/`` sub-budget) - VerticalCenter (the
  deprecated upstream shim) and the ``if __name__ == "__main__"`` demo
  are dropped; the docstring on ``__init__`` is shortened.

REMOVED vs upstream
-------------------
* ``from .jupyter import JupyterMixin`` - JupyterMixin is the IPython
  ``_repr_mimebundle_`` adapter; picolet-tui has no Jupyter integration
  and the mixin's only contribution to runtime behaviour is the
  mimebundle method.  A local empty ``object``-subclass placeholder
  named ``_AlignBase`` stands in so the class statement remains
  identical in shape to upstream, but no IPython surface is exposed.
* ``VerticalCenter`` class - explicitly deprecated upstream (replaced by
  ``Align(..., vertical="middle")``) and unused by any picolet-tui or
  Textual call path we will port.  Dropping it saves ~55 LoC against
  the NFR-TUI-19 budget.
* The ``if __name__ == "__main__"`` demo block at the foot of the file
  (Panel + ReprHighlighter + Group) pulls in three heavyweight Rich
  modules (panel, highlighter, console) just for a manual sanity check.
  It is removed.
* ``from typing import TYPE_CHECKING, Iterable, Optional, Literal`` is
  routed through ``picolet_tui._shims.typing``; ``Literal`` and
  ``Optional`` collapse to identity placeholders there.
* ``from .constrain import Constrain`` is imported lazily inside
  ``__rich_console__`` so this module can be frozen before or after the
  sibling ``constrain.py`` (parallel-agent ordering hedge).  The lazy
  branch with a ``__rich_console__`` fallback (no Constrain available)
  is *not* added - failing the import at render time is the right
  signal; the compositor refuses to render Align without Constrain.

WHY a private ``_AlignBase`` instead of object directly
-------------------------------------------------------
Upstream's ``class Align(JupyterMixin)`` reserves the MRO slot for the
mixin; if a downstream pickle / unpickle round-trip ever inspects
``__bases__`` it expects a single named base.  Naming the placeholder
preserves that shape without exposing ``_repr_mimebundle_``.  This is
NOT a load-bearing decision - any subclass-able stub works - but it
keeps grep parity with upstream.
"""

from itertools import chain

from picolet_tui._shims.typing import TYPE_CHECKING, Iterable, Optional, Literal

from .measure import Measurement
from .segment import Segment

if TYPE_CHECKING:
    # Dead at runtime - typing shim's TYPE_CHECKING is always False.
    # Kept for documentation parity with upstream's layering comments.
    from .console import Console, ConsoleOptions, RenderableType, RenderResult
    from .style import StyleType


AlignMethod = Literal["left", "center", "right"]
VerticalAlignMethod = Literal["top", "middle", "bottom"]


class _AlignBase:
    """Placeholder for upstream's JupyterMixin base; see file docstring."""

    __slots__ = ()


class Align(_AlignBase):
    """Align a renderable by padding with spaces on the relevant sides.

    Args:
        renderable: A console renderable.
        align: One of "left", "center", or "right".
        style: Optional style applied to the padding background.
        vertical: Optional vertical align, one of "top", "middle",
            "bottom".  When None, no vertical padding is added.
        pad: When True, the trailing edge is padded with spaces so each
            line reaches the available width; when False the right edge
            (and the right half in centre mode) is left ragged.
        width: Restrict contents to this width; None means use the
            measured intrinsic width.
        height: Total render height; None fits to contents.

    Raises:
        ValueError: ``align`` or ``vertical`` outside its enum.
    """

    def __init__(
        self,
        renderable,
        align="left",
        style=None,
        *,
        vertical=None,
        pad=True,
        width=None,
        height=None,
    ):
        if align not in ("left", "center", "right"):
            raise ValueError(
                'invalid value for align, expected "left", "center", or "right" '
                "(not %r)" % (align,)
            )
        if vertical is not None and vertical not in ("top", "middle", "bottom"):
            raise ValueError(
                'invalid value for vertical, expected "top", "middle", or "bottom" '
                "(not %r)" % (vertical,)
            )
        self.renderable = renderable
        self.align = align
        self.style = style
        self.vertical = vertical
        self.pad = pad
        self.width = width
        self.height = height

    def __repr__(self):
        return "Align(%r, %r)" % (self.renderable, self.align)

    @classmethod
    def left(cls, renderable, style=None, *, vertical=None, pad=True,
             width=None, height=None):
        """Align a renderable to the left."""
        return cls(renderable, "left", style=style, vertical=vertical,
                   pad=pad, width=width, height=height)

    @classmethod
    def center(cls, renderable, style=None, *, vertical=None, pad=True,
               width=None, height=None):
        """Align a renderable to the centre."""
        return cls(renderable, "center", style=style, vertical=vertical,
                   pad=pad, width=width, height=height)

    @classmethod
    def right(cls, renderable, style=None, *, vertical=None, pad=True,
              width=None, height=None):
        """Align a renderable to the right."""
        return cls(renderable, "right", style=style, vertical=vertical,
                   pad=pad, width=width, height=height)

    def __rich_console__(self, console, options):
        # Lazy import: Constrain is a Tier-4 sibling.  Parallel-agent
        # ordering means it may or may not be on disk at port time, but
        # by the time the compositor calls __rich_console__ both
        # modules are in the frozen tree and the import resolves.
        from .constrain import Constrain

        align = self.align
        width = console.measure(self.renderable, options=options).maximum
        rendered = console.render(
            Constrain(
                self.renderable,
                width if self.width is None else min(width, self.width),
            ),
            options.update(height=None),
        )
        lines = list(Segment.split_lines(rendered))
        width, height = Segment.get_shape(lines)
        lines = Segment.set_shape(lines, width, height)
        new_line = Segment.line()
        excess_space = options.max_width - width
        style = console.get_style(self.style) if self.style is not None else None

        def generate_segments():
            if excess_space <= 0:
                # Exact fit - emit each line and a newline.
                for line in lines:
                    yield from line
                    yield new_line

            elif align == "left":
                # Pad on the right only.
                pad_seg = Segment(" " * excess_space, style) if self.pad else None
                for line in lines:
                    yield from line
                    if pad_seg:
                        yield pad_seg
                    yield new_line

            elif align == "center":
                # Split the excess between the two sides; the left half
                # is always materialised, the right half only when the
                # caller asked for padding.
                left_count = excess_space // 2
                pad_seg = Segment(" " * left_count, style)
                pad_right = (
                    Segment(" " * (excess_space - left_count), style)
                    if self.pad else None
                )
                for line in lines:
                    if left_count:
                        yield pad_seg
                    yield from line
                    if pad_right:
                        yield pad_right
                    yield new_line

            elif align == "right":
                # Padding goes entirely on the left.
                pad_seg = Segment(" " * excess_space, style)
                for line in lines:
                    yield pad_seg
                    yield from line
                    yield new_line

        # Blank-line shape depends on whether the caller asked us to
        # paint background - a styled space-run versus a bare newline.
        blank_line = (
            Segment(("%s\n" % (" " * (self.width or options.max_width))), style)
            if self.pad
            else Segment("\n")
        )

        def blank_lines(count):
            if count > 0:
                for _ in range(count):
                    yield blank_line

        vertical_height = self.height or options.height
        if self.vertical and vertical_height is not None:
            if self.vertical == "top":
                bottom_space = vertical_height - height
                iter_segments = chain(generate_segments(), blank_lines(bottom_space))
            elif self.vertical == "middle":
                top_space = (vertical_height - height) // 2
                bottom_space = vertical_height - top_space - height
                iter_segments = chain(
                    blank_lines(top_space),
                    generate_segments(),
                    blank_lines(bottom_space),
                )
            else:  # self.vertical == "bottom"
                top_space = vertical_height - height
                iter_segments = chain(blank_lines(top_space), generate_segments())
        else:
            iter_segments = generate_segments()

        # Style is resolved once above; re-resolving inside the branch
        # below is the upstream behaviour (and is cheap - get_style
        # caches).  Kept verbatim so the test corpus sees the same call
        # ordering against console.get_style.
        if self.style:
            style = console.get_style(self.style)
            iter_segments = Segment.apply_style(iter_segments, style)
        yield from iter_segments

    def __rich_measure__(self, console, options):
        # Delegate to Measurement.get; matches upstream exactly so the
        # compositor's measure pass treats Align as transparent.
        return Measurement.get(console, options, self.renderable)
