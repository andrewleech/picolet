"""picolet_tui.widgets.button - Button widget (FR-TUI-46).

Button is the first *interactive* widget in the v0.1 set.  Unlike
Static / Label which are passive content holders, Button:

  * is focusable (``can_focus = True``);
  * binds the ``enter`` key (FR-TUI-46) to an ``action_press`` method;
  * posts a ``Button.Pressed`` message on activation;
  * carries a ``variant`` Reactive that selects one of five visual
    styles ("default" / "primary" / "success" / "warning" / "error").

Spec coverage:
  * FR-TUI-46 - ``Button(label, *, id=None, variant="default")`` renders
                a single-line clickable widget.  Focusable.  ``enter``
                (and, in a complete driver, ``space``) post
                ``Button.Pressed(button=self)``.  Left mouse-click within
                the button region posts the same message.
                ``variant ∈ {"default", "primary", "success", "warning",
                "error"}``.
  * FR-TUI-27 - ``BINDINGS`` declared at class scope; the @widget MRO
                merge picks it up.
  * §4.6     - Button contributes ``bindings`` ``enter`` (and the design
                doc lists ``space`` too; the v0.1 ``keys`` table covers
                ``enter`` cleanly so we ship that today and rely on the
                key-alias table for ``space`` once the driver lands the
                space-key event).
  * §3.1     - ``Button.Pressed`` is a nested Message subclass; the
                outer Button class is decorated with @widget so the
                bucket-1 reactive walk and bucket-5 bindings walk run.
                The nested Pressed class is *not* @widget-decorated
                because it carries no handlers - per the §3 note on
                "plain message subclasses that only carry data
                attributes are valid as plain ``class M(Message):``".

Design-doc references (textual-core-design.md §4.6, §3.1, §6.1):
  * §4.6 lists Button at the bottom of the inheritance table with the
    ``enter`` / ``space`` bindings.
  * §3.1 covers the nested-Message convention - ``Button.Pressed``
    inherits from Message, gets ``__init__`` arg ``button``.
  * §6.1 covers BINDINGS shorthand; we use the explicit ``Binding(...)``
    constructor for the single entry.

Deviations / decisions:
  * The design doc's §4.6 row mentions ``enter`` *and* ``space``.  We
    bind only ``enter`` here because the v0.1 KEY_ALIASES table (Phase
    4b agent 4) routes ``" "`` -> ``"space"`` but the dispatch path is
    not yet exercised for ``space`` in the harness tests.  When the
    space-key path lands, this list grows by one entry; no other code
    in this module changes.
  * The label is stored as a Reactive (separate from Static's
    ``content``).  Why: a Button's *label* is conceptually different
    from arbitrary content - it is always a string, never a Rich
    renderable.  Routing it through a dedicated Reactive lets
    watch_label do the variant-aware Text construction once per label
    change rather than per frame.
  * The five-variant style table is built at module scope, not per
    instance.  Style instances are immutable; sharing them across
    button instances is the same trick upstream Textual's CSS engine
    plays (each Style is a key in the style cache).
  * We do not yet style for focus / hover state.  The focus highlight
    is the compositor's job (the focused widget gets an inverted
    border or similar in v0.2 TCSS); for v0.1 the variant alone drives
    the cell colour.

Tier-2 deps:
  * picolet_tui._textual.binding.Binding - the BINDINGS class entry.
  * picolet_tui._textual.message.Message - the Pressed message base.

LoC target: ~180-250.  Actual: see end-of-reply summary.
"""

# Static is the renderable-host parent; Button extends it for the
# label-as-renderable plumbing (refresh, dirty bit, render-cache slot
# pattern).  The base class's @widget decoration is inherited via MRO,
# but Button itself must still be @widget-decorated for its own
# reactives + bindings to be captured.
from .static import Static

# Reactive descriptor for the label + variant slots.  Two reactives,
# not one: label and variant change independently and a write to one
# should not invalidate the other's cached state.  See watch_label /
# watch_variant below for the cache invalidation surface.
from .._textual.reactive import Reactive

# @widget is mandatory - Button declares reactives, a BINDINGS list, and
# an action_press method.  Omitting it raises MissingWidgetDecoratorError
# from Widget.__init__ on first instantiation (R3 guard).
from .._textual._widget_decorator import widget

# Binding is the value type used inside BINDINGS lists.  We could write
# the shorthand 2-tuple ``("enter", "press")``, but the explicit
# Binding(..., description=...) form is closer to upstream Textual and
# documents the footer description in-line.
from .._textual.binding import Binding

# Message is the base class for the nested Pressed event.  Importing
# the symbol directly (not the module) so the ``class Pressed(Message):``
# below reads cleanly.
from .._textual.message import Message

# Text is the styled-string carrier returned from render().  The
# compositor's render-lines path accepts a Text directly (§7.1 contract).
from .._rich.text import Text

# Style.parse turns a style-spec string ("bold white on blue") into a
# Style instance the Text can carry on its base style slot.  We use
# Style.parse (not Style(...) constructor) so the variant table reads
# as the same style-spec a user would type in TCSS / DEFAULT_CSS in
# v0.2 - the migration cost between v0.1 and v0.2 stays minimal.
from .._rich.style import Style


# -----------------------------------------------------------------------
# Variant style table.
#
# The five FR-TUI-46 variants map to canonical Rich style strings.  The
# strings are parsed once at module load (Style.parse caches per the
# style module's 128-entry LRU); subsequent button instances share the
# Style values.
#
# Why module-scope, not class-scope: a class attribute would still
# parse once (Python evaluates class bodies once at class creation),
# but module-scope keeps the table next to the FR-TUI-46 enumeration
# in this docstring, which makes it easier to spot when the spec
# changes.
# -----------------------------------------------------------------------

_VARIANT_STYLES = {
    # "default" - bold white text, no background colour.  The footer
    # widget's footer rule already paints the bg; the button just
    # bolds its label so it reads as a clickable element rather than
    # a plain string.
    "default": Style.parse("bold white"),
    # "primary" - the call-to-action variant; bold white on blue is
    # the upstream Textual default for a primary button.
    "primary": Style.parse("bold white on blue"),
    # "success" - bold white on green; matches the green-go convention
    # used in every other terminal app.
    "success": Style.parse("bold white on green"),
    # "warning" - bold black on yellow; black on yellow because white
    # on yellow has poor contrast on common terminal palettes.
    "warning": Style.parse("bold black on yellow"),
    # "error" - bold white on red; the destructive-action variant.
    "error": Style.parse("bold white on red"),
}


# -----------------------------------------------------------------------
# Button.
# -----------------------------------------------------------------------


@widget
class Button(Static):
    """A clickable button.

    Fires ``Button.Pressed`` when activated (enter key while focused,
    or - once mouse dispatch lands - a left click within the button
    region).  The ``variant`` reactive selects one of five visual
    styles per FR-TUI-46.

    Construction::

        button = Button("Submit", variant="primary", id="submit")

    Activation handler::

        @on(Button.Pressed)
        def handle_press(self, event):
            self.log("button pressed:", event.button.label)
    """

    # ------------------------------------------------------------------
    # Nested Pressed message (design §3.1, FR-TUI-46).
    # ------------------------------------------------------------------

    class Pressed(Message):
        """Posted by Button on activation.

        Carries a reference to the button that fired it so handlers
        attached to a parent (e.g. Screen-level ``@on(Button.Pressed)``)
        can distinguish which button posted the event without relying
        on the selector form (which v0.1 supports but the @on parser
        treats as no-filter pending the selector agent).

        Not decorated with @widget: Pressed has no reactives, no
        handlers, no bindings - the design §3 carve-out applies.
        """

        def __init__(self, button):
            # Message.__init__ sets up _stop_bubble / _sender / the
            # _handler_args reservation; calling it is the one
            # framework contract every Message subclass owes.
            Message.__init__(self)
            # The originating Button.  Read by parent handlers via
            # ``event.button``; this mirrors upstream Textual's
            # ``Button.Pressed.button`` attribute name verbatim, so
            # @on(Button.Pressed) handlers ported from upstream code
            # do not need to rename the field.
            self.button = button

    # ------------------------------------------------------------------
    # Class attributes.
    # ------------------------------------------------------------------

    # The enter binding.  ``action="press"`` resolves at dispatch time
    # to ``self.action_press()`` (the dispatcher prefixes ``action_``).
    # ``description="Press"`` is the footer label string per design
    # §6.1.  Single-entry list; user subclasses extend via the standard
    # ``BINDINGS = Button.BINDINGS + [...]`` pattern.
    BINDINGS = [Binding("enter", "press", "Press")]

    # The label Reactive.  Default "" so a bare ``Button()`` reads
    # cleanly even before update().  Writes fire watch_label which
    # rebuilds the cached Text in the variant's style.
    label = Reactive("")

    # The variant Reactive.  Default "default" matches the FR-TUI-46
    # signature.  Writes fire watch_variant which also rebuilds the
    # cached Text - same target as watch_label, since variant changes
    # affect the style spans on the rendered Text.
    variant = Reactive("default")

    # Focus permission.  Set as a class attribute (not a Reactive)
    # because focus eligibility is a fixed property of the Button type
    # per design §4.2 - every Button instance is focusable.  Widget's
    # base ``can_focus = False`` is shadowed here.
    can_focus = True

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(
        self,
        label="",
        *,
        variant="default",
        name=None,
        id=None,
        classes="",
    ):
        # Static.__init__ runs Widget.__init__ (R3 guard, DOMNode
        # topology, message pump) and binds the ``content`` slot to
        # the empty default.  We pass ``markup=False`` because the
        # button label is plain text - applying Rich markup to a
        # button caption is a v0.2 feature; v0.1 keeps the rendering
        # path explicit so the variant styles compose cleanly with
        # the label text (no inline span surprises from markup).
        Static.__init__(
            self,
            "",
            markup=False,
            name=name,
            id=id,
            classes=classes,
        )

        # Validate the variant up-front so an obvious typo
        # (``variant="primery"``) fails fast at construction rather
        # than silently rendering as the default.  The check is here
        # rather than in watch_variant because watch_variant fires on
        # every reactive write, including the init-time write below,
        # and a validate_<name> hook would be heavier-weight than this
        # one-line check.
        if variant not in _VARIANT_STYLES:
            raise ValueError(
                "Button variant must be one of "
                + ", ".join(sorted(_VARIANT_STYLES.keys()))
                + "; got " + repr(variant)
            )

        # Writing to the Reactive fires watch_label, which rebuilds the
        # cached Text via _render_label_with_variant().  The variant
        # write below would rebuild a second time; we accept the
        # double-build because the watch path is the single source of
        # truth for the cached Text and going around it would
        # duplicate the variant-style logic at the init site.
        self.label = label

        # Variant write fires watch_variant which rebuilds the cached
        # Text against the (now-set) label.  Order matters: if we
        # assigned variant first, watch_variant would see the still-
        # default label and the cached Text would be stale until the
        # next label assignment.
        self.variant = variant

    # ------------------------------------------------------------------
    # action_press - the FR-TUI-46 activation method.
    # ------------------------------------------------------------------

    def action_press(self):
        """Post a Button.Pressed message.

        Called by the binding dispatcher (§6.3) when the ``enter``
        binding fires.  Also called directly from on_click() so the
        mouse and keyboard paths share one activation surface.

        post_message is sync (NFR-TUI-11); no awaits.  The bubble
        walk happens after the pump's next tick, so this method
        returns immediately after the message lands in the queue.
        """
        # Construct + post.  The Pressed payload carries self so any
        # parent handler can read the button identity without a
        # selector lookup.
        self.post_message(self.Pressed(self))

    # ------------------------------------------------------------------
    # on_click - mouse activation surface.
    # ------------------------------------------------------------------

    def on_click(self):
        """Activate on left-click (FR-TUI-46 mouse path).

        The v0.1 mouse-event router does not yet land in Phase 4b -
        MouseDown decoding is FR-TUI-16 and lives in the driver
        layer.  When that path lands, the router will call
        ``widget.on_click(event)`` for any MouseDown within the
        widget's region; the no-arg signature here is the upstream
        Textual convention for "I do not care which button or where".

        Why route through action_press rather than posting Pressed
        directly: action_press is the single activation surface;
        wiring both the keyboard and mouse paths through it means a
        future change to the activation contract (e.g. ripple
        animation, debounce) lands in one method.
        """
        self.action_press()

    # ------------------------------------------------------------------
    # watch_label - reactive watcher for the label slot.
    # ------------------------------------------------------------------

    def watch_label(self, old, new):
        """Reactive watcher for ``label`` (FR-TUI-20).

        Rebuilds the cached Text and triggers a refresh.  The cached
        Text lives in self._renderable (inherited from Static) so
        render() returns it without rebuilding.
        """
        # Re-render against the current variant.  The Reactive write
        # path also calls refresh() after the watcher returns; the
        # explicit call here documents the FR-TUI-41 redraw contract
        # at the watcher site.
        self._renderable = self._render_label_with_variant()
        self.refresh()

    # ------------------------------------------------------------------
    # watch_variant - reactive watcher for the variant slot.
    # ------------------------------------------------------------------

    def watch_variant(self, old, new):
        """Reactive watcher for ``variant`` (FR-TUI-20).

        Rebuilds the cached Text against the new variant style.
        Same cache target as watch_label; both watchers go through
        _render_label_with_variant() so the rebuild logic is one
        place.

        Validate the new value here too - a runtime write to
        ``self.variant = "bogus"`` should fail as loudly as the
        constructor would.  Falling through silently would render
        with the previous variant which is the worst possible UX.
        """
        if new not in _VARIANT_STYLES:
            raise ValueError(
                "Button variant must be one of "
                + ", ".join(sorted(_VARIANT_STYLES.keys()))
                + "; got " + repr(new)
            )
        self._renderable = self._render_label_with_variant()
        self.refresh()

    # ------------------------------------------------------------------
    # render() - the §7.1 contract.
    # ------------------------------------------------------------------

    def render(self):
        """Return the cached Text (§7.1).

        Static's render() returns self._renderable; we follow the same
        contract.  The cached Text is rebuilt by watch_label /
        watch_variant; the per-frame cost is one attribute read.
        """
        return self._renderable

    # ------------------------------------------------------------------
    # _render_label_with_variant - the cache rebuilder.
    # ------------------------------------------------------------------

    def _render_label_with_variant(self):
        """Build a styled Text from self.label and self.variant.

        Returns a Text instance carrying the variant's Style as its
        base style.  The compositor's render-lines path applies the
        base style to every cell of the resulting Segments, so the
        button reads as one continuous colour-coded run.

        Called from:
          * __init__ (indirectly, via the watch_label / watch_variant
            chain triggered by the constructor's reactive writes);
          * watch_label, on every label change;
          * watch_variant, on every variant change.
        """
        # Look up the variant's Style.  Both reactive writes validate
        # the variant up-front, so this lookup is unconditional - a
        # KeyError here would mean the validation got bypassed,
        # which is itself a bug worth surfacing rather than silently
        # falling back to the default.
        style = _VARIANT_STYLES[self.variant]

        # The button label gets a one-space pad on each side so a
        # variant background colour ("primary" / "success" / etc.)
        # paints a visible band around the label rather than just
        # under the glyphs.  ``[ Submit ]`` reads as a button; bare
        # ``Submit`` does not.  No padding for "default" would be
        # inconsistent across variants; the constant pad keeps the
        # layout predictable.
        padded = " " + self.label + " "

        # Text(text, style=...) puts the style on the Text's base
        # slot.  Per the Text contract, the base style applies to
        # every cell unless a span overrides it; we add no spans so
        # the variant style covers the whole label.
        return Text(padded, style=style, end="")
