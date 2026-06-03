"""picolet_tui.widgets.vertical - Vertical container (FR-TUI-44).

Vertical stacks its children top-to-bottom along the row axis.  It is
the layout default used by Container when a direction is required by
the layout pass (FR-TUI-29 / FR-TUI-44), so any code path that needs
a directional fallback can reach for Vertical without an explicit
``direction=`` argument.

Layout semantics (FR-TUI-29 / FR-TUI-44):
  * Children are placed in mount order from the top of the parent
    region downward.
  * Each child's row allocation is its declared ``height`` from
    Style if any, then its measured intrinsic height (FR-TUI-30).
  * Any height left over after fixed-size children are placed is
    distributed across children whose ``height`` is declared as the
    ``1fr`` Scalar; multiple ``1fr`` children share remaining space
    proportionally to their fr weight.
  * Per-child ``width=`` overrides are honoured (FR-TUI-44); the
    default width is the full parent width minus padding/border.
  * No animation on layout changes (FR-TUI-31 / D7).

The class deliberately carries no logic of its own beyond the
direction tag.  The actual row-stack arithmetic lives in the parent
``Container`` (or in a layout helper Container delegates to) keyed on
the ``DEFAULT_DIRECTION`` class attribute the design doc pins for the
directional subclasses.  Keeping the implementation in Container
(not duplicated across Vertical / Horizontal) makes the FR-TUI-29
"non-directional Container with direction-aware subclasses" model
literal in code: Vertical *is* Container with a different
DEFAULT_DIRECTION.

Spec coverage:
  * FR-TUI-29 - directional subclass of the non-directional Container.
  * FR-TUI-44 - Vertical stacks children top-to-bottom; honours
                per-child width overrides; distributes remaining
                height across 1fr children.
  * FR-TUI-52 - Accepts id / classes constructor kwargs via the
                Container -> Widget chain (no override needed here).

Design-doc references:
  * §7.1 textual-core-design.md - "Container renders an empty
    placeholder (children render themselves)".  Vertical inherits the
    same render() behaviour; children paint their own regions.
"""

# Container provides the topology, mount lifecycle, and the layout
# entry point that consults DEFAULT_DIRECTION.  Vertical is a thin
# specialisation; everything but the direction tag lives upstream.
from .container import Container

# @widget is mandatory on every Widget subclass that declares
# Reactives, BINDINGS, or @on handlers (FR-TUI-28 / R3).  Vertical
# itself declares no new artifacts, but @widget is still required
# because the runtime guard in Widget.__init__ checks vars(cls) for
# _tui_widget_registered - an inherited True does not satisfy the
# guard for a subclass that itself was not decorated.  See the
# rationale block in _textual/widget.py around the R3 check.
from .._textual._widget_decorator import widget


@widget
class Vertical(Container):
    """Container that stacks children top-to-bottom (FR-TUI-44).

    The directional behaviour is selected by ``DEFAULT_DIRECTION``;
    Container's layout pass reads this attribute to choose the row
    axis when distributing space.  No constructor override is needed:
    ``Vertical(*children, **kw)`` forwards to ``Container.__init__``
    unchanged, which is itself a Widget(*children, **kw) chain.
    """

    # The direction tag Container's layout pass keys off.  Storing
    # the string at class scope (not via Reactive) because the
    # direction is fixed for the type - it is not expected to change
    # at runtime, and a Reactive write here would needlessly trigger
    # layout passes that the FR-TUI-44 contract treats as constant.
    DEFAULT_DIRECTION = "vertical"
