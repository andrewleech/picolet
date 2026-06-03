"""picolet_tui.widgets.horizontal - Horizontal widget (FR-TUI-45).

Horizontal arranges its children left-to-right.  It is the row-axis
analogue of Vertical; the only structural difference is the class
attribute ``DEFAULT_DIRECTION`` consumed by the Phase 4c layout pass
to pick the main axis along which ``1fr`` children distribute free
space.

Spec coverage:
  * FR-TUI-45 - ``Horizontal(*children, **kw)`` arranges children
                left-to-right; honours per-child ``height=`` overrides
                and distributes remaining width across ``1fr`` children.
  * FR-TUI-29 - Accepts explicit ``width=``/``height=`` (int cells or
                Scalar string forms ``"50%"``, ``"1fr"``, ``"auto"``)
                routed through Scalar.parse for the string cases.
  * FR-TUI-52 - Accepts ``id`` / ``classes`` constructor kwargs and
                forwards to Container.__init__ (which forwards to
                Widget.__init__).
  * §7.1     - Container.render() returns an empty renderable; the
                compositor walks _mounted_children for the actual
                paint.  We do not override render() - children render
                themselves.

Design-doc references (textual-core-design.md §7.1):
    Static renders self._content; Label renders self._text; Container
    renders an empty placeholder (children render themselves).

Why this file is ~40 LoC of code rather than the 100+ Vertical might
imply: Horizontal is a *direction marker*.  All the heavy lifting
(child-mount plumbing, width/height Scalar coercion, render fallback)
belongs in Container.  Horizontal's only job is to set the class
attribute that tells the layout pass which axis is "main".  Doing
more here would either duplicate Container behaviour or pre-empt
Vertical's mirror choices.

Deviations / decisions:
  * ``DEFAULT_CSS`` is accepted for API parity with upstream Textual
    but ignored in v0.1 (TCSS lands in v0.2 - matches Static).
  * We do not declare a Reactive on this class.  Direction is a
    structural property, not a per-frame mutable; making it Reactive
    would invite users to write ``self.direction = "vertical"`` at
    runtime, which would require an in-place layout re-flow that the
    Phase 4c compositor is not designed to handle mid-frame.
  * No on_<event> handlers or BINDINGS - the bare grouping widget
    is not focusable by default (Container inherits Widget's
    ``can_focus = False``).  @widget is still required because
    Container declares Reactives and we inherit through the MRO; the
    R3 guard fires on the direct class, not the MRO walk.
"""

# Container is the non-directional grouping base (FR-TUI-43).  It owns
# the *children child mount plumbing, the width/height Scalar coercion,
# and the empty-placeholder render().  Horizontal subclasses it solely
# to flip the layout-direction class attribute.
from .container import Container

# @widget is mandatory on every Widget-derived class (FR-TUI-28 / R3).
# Container declares Reactives (expand/shrink inherited from Widget,
# plus its own width/height); the runtime guard in Widget.__init__
# checks vars(type(self)) for _tui_widget_registered, which inherited
# True does not satisfy - we must decorate the direct class.
from .._textual._widget_decorator import widget


@widget
class Horizontal(Container):
    """A container that arranges its children left-to-right.

    Mirror of Vertical with ``DEFAULT_DIRECTION = "horizontal"``.  The
    layout pass (Phase 4c) reads this class attribute to pick the
    main axis: ``"horizontal"`` means ``1fr`` children expand along
    the row, while ``height=`` overrides on individual children pin
    their cross-axis allocation.

    Constructor signature matches Container; we add no new parameters.
    Children passed positionally are stashed on ``_pending_children``
    by Widget.__init__ and drained at mount time.
    """

    # DEFAULT_DIRECTION is the contract this whole class exists to
    # express.  Phase 4c's layout pass consults
    # ``type(self).DEFAULT_DIRECTION`` (not an instance attribute) so
    # we declare it at class scope.  Storing the string at class scope
    # (not via Reactive) because the direction is fixed for the type -
    # a Reactive write would needlessly trigger layout passes that the
    # FR-TUI-45 contract treats as constant.
    DEFAULT_DIRECTION = "horizontal"
