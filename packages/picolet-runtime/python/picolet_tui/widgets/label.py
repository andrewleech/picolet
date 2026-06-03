"""picolet_tui.widgets.label - Label widget (FR-TUI-42).

A Label is a single-line ``Static`` subclass with one extra knob: an
optional ``Style`` that is applied to whatever Static would have
rendered.  It is the smallest "Static + one styling override" widget,
and is the canonical example used in the design doc (§7.1) of how to
extend a content host without re-implementing the markup parsing path.

Spec coverage:
  * FR-TUI-42 - ``Label(text="")`` is a single-line Static subclass.
                We accept the upstream-Textual positional name
                ``renderable`` to stay consistent with the Static
                constructor (the design doc's render() comment uses
                "label renders self._text" - we route ``text`` through
                Static's ``content`` reactive rather than introducing
                a parallel slot, which would double the watcher cost
                and create two sources of truth).  An assignable
                ``text`` property maps onto ``content`` for users
                reaching for the FR-TUI-42 surface.
  * FR-TUI-52 - id / classes kwargs forwarded through Static.

Design-doc references (textual-core-design.md §7.1):

    Static renders self._content; Label renders self._text;
    Container renders an empty placeholder ...

We collapse "self._text" into Static's existing ``content`` reactive
because:

  1. Static.update() / Static.watch_content / Static._renderable are
     the entire content-mutation pipeline already.  Adding a second
     Reactive(text) on Label would either shadow that path (forcing
     us to override watch_content) or run alongside it (two
     refresh()es per assignment) - neither is justified for a
     single-line variant.
  2. The truncation behaviour FR-TUI-42 calls for ("never wraps;
     overflow truncated with U+2026") is a *render-time* concern, not
     a storage one.  Truncation lives in render(), not in a watcher.

Style handling:
  The ``style`` constructor kwarg is a Rich ``Style`` instance (or
  None).  When set, render() wraps Static's output in a Text styled
  with self._style.  We use ``stylize_before`` rather than overwriting
  ``Text.style`` so any per-span styling from markup is preserved -
  the Label-level style acts as a *base*, not a *replace*.

Deviations / decisions:
  * The FR-TUI-42 truncation-with-ellipsis behaviour ultimately
    depends on the compositor knowing the region width.  In Phase 4b
    no region is wired up yet (that arrives with the compositor in
    Phase 4c), so the truncation hook is *prepared* but not invoked.
    render() returns the styled renderable; the compositor's
    render_lines call already applies Text.truncate via the
    ConsoleOptions(overflow="ellipsis") path - we set that default by
    constructing the wrapping Text with no_wrap=True and overflow=
    "ellipsis" so the existing render path does the work.
  * ``markup`` defaults to True via Static; Label does not override.
  * ``name`` accepted for API parity; not yet routed (matches Static).
"""

# Static gives us the content Reactive, the markup parsing path, the
# update() surface, and the watch_content -> refresh wiring.  Label
# only adds a style overlay and a truncation hint at render time -
# every other content-mutation concern is inherited.
from .static import Static

# @widget is mandatory on every Widget subclass declaring Reactives,
# BINDINGS, or @on handlers (FR-TUI-28 / R3).  Label inherits a
# Reactive (``content``) from Static, but per the R3 contract the
# decorator must be applied to *every* class in the MRO that adds
# anything - and even classes that add nothing get the decorator
# applied for the constructor-time guard to see a populated
# _tui_widget_meta on this concrete class.  Omitting it would raise
# MissingWidgetDecoratorError from Widget.__init__.
from .._textual._widget_decorator import widget

# Text is the styled-renderable shape we use to apply self._style when
# Static.render() handed us a bare str.  Imported at module scope
# rather than lazily because Label.render() is the per-frame hot path
# and a stable module-level binding avoids a per-call import lookup.
from .._rich.text import Text


# ---------------------------------------------------------------------
# Label.
# ---------------------------------------------------------------------


@widget
class Label(Static):
    """Display a line of text.

    Construction:

        Label("hello")
        Label("[bold]hello[/bold]", style=Style(color="cyan"))
        Label("plain", markup=False)

    The positional argument is forwarded to Static unchanged - markup
    is parsed by Static when ``markup=True``.  The ``style`` kwarg, if
    set, is layered over the rendered text as a base style; per-span
    markup styling wins on overlap (see ``stylize_before`` in
    _rich.text).

    Not focusable (inherits Widget.can_focus = False via Static).
    FR-TUI-42 explicitly classes Label as a non-interactive display
    widget; we do not override.
    """

    # DEFAULT_CSS placeholder - same pattern as Static.  v0.2 (TCSS)
    # is where this gains meaning.  Declared at class scope so a
    # subclass can override.
    DEFAULT_CSS = ""

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(
        self,
        renderable="",
        *,
        style=None,
        expand=False,
        shrink=True,
        markup=True,
        name=None,
        id=None,
        classes="",
    ):
        # Static.__init__ runs the R3 guard via Widget.__init__,
        # installs the content Reactive, seeds _renderable, and
        # registers id/classes through DOMNode.  Forward the standard
        # FR-TUI-41/52 kwargs verbatim; Label-specific state is
        # captured after super() returns so the watch_content -> refresh
        # path is fully wired before we layer our style on top.
        Static.__init__(
            self,
            renderable,
            expand=expand,
            shrink=shrink,
            markup=markup,
            name=name,
            id=id,
            classes=classes,
        )

        # The Label-level Style overlay.  None means "no base style";
        # a non-None Style is layered into the Text returned from
        # render() so per-span markup styling (from Static's markup
        # parse) wins on overlap.  Plain attribute (not a Reactive)
        # because changing the style at runtime is uncommon enough to
        # not justify the per-write watcher cost - users who need
        # reactive restyling can override the slot manually and call
        # self.refresh().
        self._style = style

    # ------------------------------------------------------------------
    # text property - the FR-TUI-42 named accessor.
    # ------------------------------------------------------------------

    @property
    def text(self):
        """The current text, as held in Static's ``content`` reactive.

        Why a property and not a separate Reactive: the design doc
        comment "Label renders self._text" is a *semantic* distinction
        between Static and Label, not a storage one.  Static already
        carries the content slot; duplicating it here would either
        force watch_content forwarding or risk drift between
        ``content`` and ``text``.
        """
        return self.content

    @text.setter
    def text(self, value):
        # Assigning to ``content`` goes through the Reactive __set__
        # path which fires watch_content -> _renderable update ->
        # refresh().  Identical to ``label.update(value)``.
        self.content = value

    # ------------------------------------------------------------------
    # render() - delegate to Static then apply self._style.
    # ------------------------------------------------------------------

    def render(self):
        """Return the styled renderable (§7.1).

        Calls Static.render() to get the cached renderable (a str, a
        Text, or any __rich_console__-bearing object), then applies
        self._style if set.  When Static returned a str we wrap it in
        a Text(no_wrap=True, overflow="ellipsis") so the compositor's
        render_lines path handles the FR-TUI-42 single-line
        truncation; when Static returned a Text we mutate a copy so
        the cached _renderable on Static stays untouched between
        frames.
        """
        # Delegate first.  Static.render is O(1) - it returns the
        # cached self._renderable.
        renderable = Static.render(self)

        # No style overlay and no truncation hint to add: pass through
        # unchanged.  The compositor handles bare str / Text just as
        # Static expects.
        if self._style is None and not isinstance(renderable, (str, Text)):
            # Custom __rich_console__ objects without a style overlay -
            # nothing to do here.  Truncation for these is the custom
            # renderable's own responsibility (FR-TUI-42 truncation
            # applies to the *text* slot, not arbitrary renderables).
            return renderable

        # Normalise to a Text so we have one shape to apply style and
        # truncation hints to.  Text construction copies the string
        # contents; the cached _renderable on Static is not mutated.
        if isinstance(renderable, str):
            # Bare str path: wrap with the single-line truncation hint
            # baked in.  no_wrap=True forbids the wrap pass; overflow=
            # "ellipsis" routes the FR-TUI-42 U+2026 behaviour to
            # Text.truncate inside the existing render_lines path.
            text = Text(renderable, no_wrap=True, overflow="ellipsis")
        elif isinstance(renderable, Text):
            # Existing Text path: copy so per-frame style application
            # does not accumulate spans on the cached object.  Set the
            # truncation hints on the copy.
            text = renderable.copy()
            text.no_wrap = True
            text.overflow = "ellipsis"
        else:
            # __rich_console__ object *with* a style overlay - fall
            # through to apply the style.  We cannot truncate an
            # arbitrary renderable from here; leave that to its own
            # rendering path.
            return renderable

        # Apply the Label-level Style as a *base* style, behind any
        # per-span styling the markup parser produced.  stylize_before
        # inserts the span at position 0 so the existing spans (which
        # come from Static's markup parse) layer over the top - this
        # is the upstream Textual semantic: Label-level style is the
        # default; markup wins on overlap.
        if self._style is not None:
            text.stylize_before(self._style, 0, len(text))

        return text
