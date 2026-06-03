"""picolet_tui.widgets.container - Container grouping widget.

Container is the non-directional grouping widget (FR-TUI-43): it
holds children and renders nothing of its own.  Vertical (FR-TUI-44)
and Horizontal (FR-TUI-45) are direction-pinned subclasses that live
in sibling modules (``widgets.vertical``, ``widgets.horizontal``);
they read ``DEFAULT_DIRECTION`` from this class to drive the Phase
4c compositor's row/column carving.

Spec coverage:
  * FR-TUI-29 - Container accepts width=/height= declarations; for
                v0.1 these flow through the Style DSL (FR-TUI-32)
                rather than the constructor, so the public kwargs
                here are just the synthesis-D3 nine-widget signature
                (``*children``, ``name``, ``id``, ``classes``).
  * FR-TUI-43 - ``Container(*children, **kw)`` non-directional; not
                focusable by default.
  * §7.1     - render() returns an empty string; children render in
                their own regions carved by the compositor.

Design-doc references (textual-core-design.md):
  * §7.1     - "Container renders an empty placeholder (children
                render themselves)."  We return ``""``.
  * §4.2     - children passed positionally to __init__ live in a
                pending list and graduate during the async mount path.

Direction routing for Phase 4c:
  ``DEFAULT_DIRECTION`` is the public read surface for the compositor.
  Container leaves it as ``"vertical"`` because FR-TUI-44 names
  Vertical as the direction Container falls back to "when a direction
  is required by layout".  Subclasses (Vertical, Horizontal) override
  in their own modules.

Deviations / decisions:
  * The task brief's pseudo-code uses an ``_mount_pending`` list
    populated inside Container.__init__.  The Widget base already
    carries an equivalent slot (``_pending_children``) populated from
    its own ``*children`` positional args, drained by ``Widget._mount``
    on the async mount path.  We forward ``*children`` straight to
    ``Widget.__init__`` so we inherit that existing wiring rather than
    introducing a parallel pending list - the alternative would risk
    double-mounts when the App's mount path drains both lists.
    ``compose()`` yields from ``_pending_children`` so callers that
    drive mount via compose() (App._mount_initial_screen per design
    §5.1) and callers that drive mount via the positional path both
    see the same children, exactly once.

    The double-yield safety: ``Widget._mount`` snapshots and clears
    ``_pending_children`` before iterating, so a subsequent compose()
    call (e.g. one the App makes explicitly after mount completes)
    sees an empty list - no duplicate child mounts.
  * ``can_focus`` stays False; FR-TUI-43 specifies a Container is
    focusable only "when ``can_focus=True`` is passed explicitly".
    We do not surface that constructor kwarg yet because the base
    Widget class does not accept it; routing it would require
    extending Widget.__init__ or shadowing the class attribute from
    the instance, both of which exceed the v0.1 scope for this
    file.  The constructor signature is forwards-compatible: a
    later patch can add ``can_focus=False`` as a keyword without
    breaking existing callers.
  * ``name`` is accepted for API parity with upstream Textual but
    stored on a private slot; Widget does not yet carry a name
    Reactive in Phase 4b.  Same treatment as Static (see static.py
    docstring).
  * No Reactive descriptors on Container itself.  Children mutate
    through the standard mount/remove async path; there is no
    per-instance content slot to watch.
"""

# Widget gives us the DOMNode topology, the message pump, the
# *children positional-arg handling (populating _pending_children),
# the R3 guard, and the default render() returning "".  We override
# render() with an explicit empty string anyway so the §7.1 contract
# is documented at this file's site rather than inherited silently.
from .._textual.widget import Widget

# @widget is mandatory on every Widget subclass per FR-TUI-28 / R3 -
# even subclasses that declare no Reactives or BINDINGS pay the
# decorator cost so the runtime guard in Widget.__init__ does not
# trip.  Container declares no class-level capturable artifacts, so
# the decorator's MRO walk inherits the Widget meta; the only work
# it does on this class is set ``_tui_widget_registered = True`` in
# vars(Container), which is the bit Widget.__init__ checks.
from .._textual._widget_decorator import widget


# ---------------------------------------------------------------------
# Container.
# ---------------------------------------------------------------------


@widget
class Container(Widget):
    """A non-directional grouping widget (FR-TUI-43).

    Holds children and renders nothing itself; the Phase 4c
    compositor carves child regions out of the Container's allocated
    region using ``DEFAULT_DIRECTION`` (and the Style DSL's
    width/height declarations on each child) to decide axis and
    sizes.

    Not focusable by default per FR-TUI-43.  Users that want a
    focusable Container can subclass and override ``can_focus``.
    """

    # ------------------------------------------------------------------
    # Class attributes.
    # ------------------------------------------------------------------

    # The direction the compositor honours when laying out children.
    # Container is documented as non-directional in FR-TUI-43 but the
    # compositor still needs a fallback axis; FR-TUI-44 pins Vertical
    # as that fallback, so "vertical" is the sensible default.
    # Subclasses (Vertical, Horizontal) live in sibling modules and
    # override this string to pin the directional behaviour.
    DEFAULT_DIRECTION = "vertical"

    # DEFAULT_CSS is the upstream Textual convention for per-class
    # default styles.  Accepted (and ignored) for v0.1 API parity -
    # TCSS lands in v0.2 (synthesis D2).
    DEFAULT_CSS = ""

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(self, *children, name=None, id=None, classes=""):
        # Forward *children to Widget.__init__: that path appends each
        # one to self._pending_children and the async mount loop in
        # Widget._mount drains the list at mount time.  We rely on
        # that existing wiring rather than introducing a parallel
        # _mount_pending slot - the task brief's pseudo-code is a
        # sketch, not a contract, and aligning with the base class
        # avoids the double-mount risk that two separate pending
        # lists would introduce.
        Widget.__init__(self, *children, id=id, classes=classes)

        # ``name`` is accepted for API parity with upstream Textual
        # but not routed through a Reactive; Widget does not carry a
        # name slot in Phase 4b.  Same private-slot treatment as
        # Static.  User code reading widget.name will see what they
        # passed in.
        self._name = name

    # ------------------------------------------------------------------
    # compose() - yield the pending children.
    # ------------------------------------------------------------------

    def compose(self):
        """Yield the children passed positionally to __init__.

        Callers that drive mount via compose() (App._mount_initial_
        screen per design §5.1) get the same children as callers that
        drive mount via Widget._mount's positional path.  Because
        Widget._mount snapshots and clears _pending_children before
        iterating, this generator is safe to invoke after a mount
        has already completed - it simply yields nothing.

        Returns an iterator so the caller can stream-mount without
        building the full list first; this matches the Widget.compose
        default contract.
        """
        # Yield from the same list Widget._mount drains.  If mount
        # has already run, the list is empty and this is a zero-cost
        # iteration.  If mount has not yet run, the caller gets the
        # children in declaration order, which is the FR-TUI-23
        # ordering guarantee.
        for child in self._pending_children:
            yield child

    # ------------------------------------------------------------------
    # render() - empty placeholder.
    # ------------------------------------------------------------------

    def render(self):
        """Return an empty placeholder (§7.1).

        Container itself draws no cells: the Phase 4c compositor
        carves regions for each child based on DEFAULT_DIRECTION and
        per-child width/height declarations, then dispatches render()
        on each child into its own region.  The Container's own
        render() output covers only cells the children do not claim
        (e.g. if a child is smaller than its allocated region and
        the layout leaves a gap), and those cells are filled with
        the background per FR-TUI-35.

        Returns an empty string - the cheapest renderable shape the
        trimmed _rich.console knows how to drive (one zero-width
        Segment per row).
        """
        return ""
