"""picolet_tui.widgets.static - Static widget (FR-TUI-41).

Static is the simplest concrete Widget: it wraps a single Rich
renderable (or a markup string) and emits it through ``render()``.
Every other content-bearing widget in the v0.1 set either subclasses
Static (Label, ProgressBar text overlay) or hosts a Static-like
content slot, so this file is the smallest possible surface that
exercises:

  * @widget on a user-facing subclass of Widget.
  * Reactive on the subclass (the ``content`` slot).
  * watch_<name> + refresh wiring through Widget.refresh.
  * The Rich markup -> Text conversion path (constructor flag).

Spec coverage:
  * FR-TUI-41 - ``Static(content="", *, expand=False, shrink=True)``;
                ``.update(renderable)`` replaces content and triggers
                a redraw.  Not focusable (Widget.can_focus = False by
                default; we do not override).
  * FR-TUI-52 - Accepts ``id`` / ``classes`` constructor kwargs and
                forwards to Widget.__init__.
  * §7.1     - render() returns a renderable shape the trimmed
                compositor handles: str, _rich.text.Text, or any
                object exposing __rich_console__.

Design-doc references (textual-core-design.md §7.1):

    def render(self):
        # Static renders self._content.
        return self._content

The shape we adopt is slightly richer than the doc's two-line stub:
because the constructor accepts a *markup string* as well as an
already-rendered ``Text`` (the ``markup=True`` default), we cache the
parsed renderable in a private slot so the per-frame ``render()``
path stays O(1) instead of re-parsing markup every frame.  The
Reactive ``content`` slot holds the *raw* user-supplied value (so
introspection and watchers see what the user wrote); the cached
renderable lives in ``_renderable`` and is refreshed by
``watch_content``.

Deviations / decisions:
  * ``DEFAULT_CSS`` is accepted as a class attribute for API parity
    with upstream Textual but is ignored (v0.2 - TCSS lands then).
  * ``name`` is accepted for API parity but not yet routed; Widget
    does not carry a name reactive in Phase 4b.  Passing it through
    Widget.__init__ would require extending DOMNode; out of scope.
  * Highlight protocol and Style.parse auto-hooks are out of v0.1
    scope (synthesis doc - emoji + highlight tier dropped).
"""

# Widget gives us the Reactive descriptor host, the refresh() stub,
# DOMNode-derived id/classes routing, and the R3 guard.  We extend it
# in the most direct way: one Reactive, one watcher, one render
# override.
from .._textual.widget import Widget

# Reactive descriptor for the ``content`` slot.  Declared at class
# scope so the @widget MRO walk picks it up - per FR-TUI-19, the
# decorator binds the name via _bind_name and installs the
# __get__/__set__ slots.
from .._textual.reactive import Reactive

# @widget is mandatory on every Widget subclass that declares
# Reactives, BINDINGS, or @on handlers (FR-TUI-28 / R3).  Static has
# the ``content`` Reactive plus a ``watch_content`` watcher, so the
# decorator is non-negotiable - omitting it would raise
# MissingWidgetDecoratorError from Widget.__init__ on first
# instantiation.
from .._textual._widget_decorator import widget

# Rich markup parser: turns ``[bold red]foo[/bold red]`` into a
# styled ``Text``.  Imported lazily inside the constructor / watcher
# rather than at module top so that downstream test code which only
# touches Widget metadata can import widgets.static without paying
# the markup-module import cost.  See `_render_markup` below.


# ---------------------------------------------------------------------
# _is_renderable - the duck-type check for "already a renderable".
#
# The Rich console protocol (§7.1) accepts three shapes from
# ``Widget.render()``:
#
#   1. str         - bare string, becomes one Segment.
#   2. Text        - styled text, already a sequence of segments.
#   3. anything with __rich_console__(console, options) - the full
#      Rich protocol.
#
# A str argument to Static() with markup=True is parsed through the
# markup module on first store; a str with markup=False is stored
# verbatim; anything else (Text or a __rich_console__-bearing object)
# is passed through unchanged.  The helper centralises the
# "is this already a renderable?" question so update() / __init__ /
# watch_content all agree.
# ---------------------------------------------------------------------


def _is_renderable(value):
    """True if ``value`` is a non-string renderable shape.

    Strings are excluded because they take the markup-parse branch;
    every other renderable shape (Text, custom __rich_console__) is
    passed through.
    """
    if isinstance(value, str):
        return False
    # The Rich protocol shape: any object exposing __rich_console__ is
    # a renderable.  We do not import Text here to keep the import
    # graph flat - the isinstance check via duck-typing is cheaper and
    # equivalent for the compositor contract.
    return True


def _render_markup(text):
    """Parse a Rich markup string into a ``Text`` instance.

    Imported lazily so tests that only touch Static metadata do not
    pay the markup module's import-time cost.  In normal runtime the
    first widget.update() call warms the import; subsequent calls
    hit the import cache.
    """
    # Local import - see module docstring rationale.  The markup
    # module's render() entry point is the public API and is the
    # exact function name upstream Textual reaches for in its own
    # Static implementation.
    from .._rich.markup import render as markup_render
    return markup_render(text)


# ---------------------------------------------------------------------
# Static.
# ---------------------------------------------------------------------


@widget
class Static(Widget):
    """A widget that renders a fixed Rich renderable.

    The simplest concrete Widget; every other content-host widget in
    v0.1 either extends Static (Label) or uses the same content slot
    pattern.

    Construction accepts either a string (parsed as Rich markup when
    ``markup=True``) or any Rich renderable (Text, or any object
    exposing ``__rich_console__``).  After construction, the content
    can be replaced via ``update()`` or by writing to the ``content``
    reactive directly.

    Not focusable.  Per FR-TUI-41 Static is a leaf renderable; the
    base Widget.can_focus default of False already covers this.
    """

    # ------------------------------------------------------------------
    # Class attributes.
    # ------------------------------------------------------------------

    # The Reactive slot.  The default of "" mirrors the constructor
    # default and gives a sensible read before update() is called.
    # We do not set ``layout=True``: a Static content change repaints
    # the cells it owns but does not change its allocation - the
    # compositor's per-strip diff catches the byte-level delta.  A
    # layout flag here would force a full layout pass per update,
    # which is the FR-TUI-31 anti-pattern.
    content = Reactive("")

    # DEFAULT_CSS is the upstream Textual convention for per-class
    # default styles.  Carrying it lets v0.2 (TCSS) drop in without
    # changing the Static API; for v0.1 the value is read by nobody.
    # Declared at class scope (not inside __init__) so subclasses can
    # override it the upstream way.
    DEFAULT_CSS = ""

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(
        self,
        renderable="",
        *,
        expand=False,
        shrink=True,
        markup=True,
        name=None,
        id=None,
        classes="",
    ):
        # Widget.__init__ runs the R3 guard, sets up the DOMNode
        # topology, installs the message pump.  We forward the
        # standard FR-TUI-52 kwargs (id, classes) and let the rest
        # land on this instance.
        Widget.__init__(self, id=id, classes=classes)

        # Capture the markup flag *before* assigning content - the
        # Reactive set fires watch_content which reads self._markup
        # to decide whether to parse.  Storing it on a private slot
        # rather than a Reactive: the value is fixed at construction
        # and never changes, so the Reactive descriptor's per-write
        # cost would be wasted.
        self._markup = markup

        # ``name`` is accepted for API parity with upstream Textual
        # but not yet routed - Widget does not carry a name slot in
        # Phase 4b.  Stash it on the instance so user code that reads
        # ``widget.name`` does not AttributeError.
        self._name = name

        # expand / shrink are surfaced as Widget-level Reactives (see
        # _textual.widget).  Assigning here goes through the Reactive
        # descriptor and stores into the private slot on this
        # instance.  We do this *after* Widget.__init__ so the
        # reactives are bound (the @widget decorator on Widget set up
        # the descriptors before this subclass even imported).
        self.expand = expand
        self.shrink = shrink

        # _renderable is the cached parsed form of self.content.  It
        # is what render() returns; watch_content keeps it in sync
        # with the Reactive ``content`` slot.  We seed it from the
        # constructor argument *before* writing to self.content so
        # the watcher's first call has something coherent to compare
        # against, then assign the Reactive last so the standard
        # set-path runs (including refresh()).
        self._renderable = self._coerce(renderable)

        # Writing to the Reactive fires watch_content -> refresh().
        # The watch_content side-effect re-coerces from the stored
        # value, which is the same object we just coerced; the
        # second coerce is the cost of "no init-time skip" - cheap
        # for a single widget and worth the consistency of every
        # content path running through watch_content.
        #
        # We deliberately do *not* set self.content = renderable here
        # when renderable equals the default "" - the Reactive's
        # equality fast-path would skip the watcher and leave
        # _renderable consistent already.  But for any non-default,
        # the assignment is what triggers the initial paint signal.
        if renderable != "":
            self.content = renderable

    # ------------------------------------------------------------------
    # update() - the FR-TUI-41 public mutation surface.
    # ------------------------------------------------------------------

    def update(self, renderable):
        """Replace the content; triggers a redraw.

        Per FR-TUI-41: ``Calling static.update(content) replaces the
        content and triggers a redraw.``  The redraw is implicit -
        assigning to the ``content`` Reactive fires watch_content
        which calls self.refresh().

        Returns None (upstream Textual returns None; we match).
        """
        # The Reactive set is the one entry point for content
        # changes - it fires the watcher, the watcher updates
        # _renderable, then calls refresh().  Going directly through
        # _renderable would skip the refresh signal.
        self.content = renderable

    # ------------------------------------------------------------------
    # watch_content - keeps the cached renderable + dirty state in sync.
    # ------------------------------------------------------------------

    def watch_content(self, old, new):
        """Reactive watcher for ``content`` (FR-TUI-20).

        Recomputes the cached renderable (parsing markup if needed)
        and schedules a refresh.  Both halves matter: the cached
        renderable so the next render() call returns the new value,
        and refresh() so the compositor knows to repaint.

        The watcher arity is (self, old, new) per FR-TUI-20 -
        recorded at @widget decoration time on this class.  We use
        only ``new``; ``old`` is here for the contract and for
        subclasses that want to compare.
        """
        # Recompute the cached renderable.  This is the only place
        # _renderable changes after construction; render() reads it
        # without locking because the message pump is single-threaded
        # (D6).
        self._renderable = self._coerce(new)

        # FR-TUI-41 explicit "triggers a redraw".  Widget.refresh
        # marks the widget dirty and (Phase 4c) signals the
        # compositor.  No layout pass - content size change is
        # absorbed by the strip diff.
        #
        # Note: the Reactive __set__ path *also* calls refresh()
        # after the watcher returns (see reactive.py).  Calling
        # refresh() here is therefore a double-call - but refresh()
        # is idempotent (sets _dirty=True), and an explicit call
        # here documents the FR-TUI-41 contract at the watcher site
        # for subclasses that override the watcher and forget to
        # invoke super.
        self.refresh()

    # ------------------------------------------------------------------
    # render() - the §7.1 contract.
    # ------------------------------------------------------------------

    def render(self):
        """Return the cached renderable (§7.1).

        Returns one of the three shapes the trimmed _rich.console
        knows how to drive: a str, a _rich.text.Text, or any object
        exposing __rich_console__.  The compositor wraps the return
        in a render_lines() call against a fresh ConsoleOptions
        sized to self._region.

        Fast: O(1).  All the markup parsing happened in
        watch_content; this method is the per-frame hot path and
        must stay cheap.
        """
        return self._renderable

    # ------------------------------------------------------------------
    # _coerce - the str / renderable router.
    # ------------------------------------------------------------------

    def _coerce(self, renderable):
        """Coerce a constructor / update argument to a renderable.

        Three cases:
          1. Already a renderable (Text or __rich_console__-bearing)
             - pass through unchanged.
          2. str with self._markup=True - parse through markup.render.
          3. str with self._markup=False - return verbatim; the str
             is a valid renderable shape per §7.1.

        Centralises the "what does Static accept" decision so the
        constructor, update(), and watch_content all agree.
        """
        if _is_renderable(renderable):
            return renderable
        # renderable is a str at this point.
        if self._markup:
            # Parse Rich markup into a Text.  This is where
            # ``[bold red]foo[/bold red]`` becomes a styled Text.
            return _render_markup(renderable)
        return renderable
