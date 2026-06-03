"""picolet_tui.widgets.progress_bar - ProgressBar widget (FR-TUI-51).

ProgressBar is the v0.1 visual percentage indicator: a Unicode block
bar with optional trailing percentage / ETA fields.  It is the only
widget in the v0.1 set whose render output is a *composite* line built
from sub-segments (bar cells + spacer + numeric text), so it exercises
the Reactive -> watcher -> refresh path with a non-trivial render()
body.

Spec coverage:
  * FR-TUI-51 - constructor surface ``(total=100, *, show_percentage=
                True, show_eta=False)``; reactive ``progress`` (0..total);
                assignment / augmented assignment trigger redraw;
                Unicode block characters ``█ ▉ ▊ ▋
                ▌ ▍ ▎ ▏`` for fractional cells when
                colour mode is not mono; ASCII ``#`` otherwise.
                ``show_eta=True`` adds a right-aligned ``mm:ss`` field
                from a ring buffer of recent ``progress`` timestamps.
  * FR-TUI-52 - ``id`` / ``classes`` constructor kwargs, forwarded to
                Widget.__init__.
  * Design doc table at lines 651-660 - ProgressBar contributes
                ``progress`` to the reactives bucket; no bindings, no
                handlers.

Deviations from the design doc and v0.1 spec:
  * The v0.1 spec text (FR-TUI-51) is explicit that there is *no*
    ``bar.advance(n)`` method in v0.1.  The Phase 5 task description
    for this widget nonetheless mandates an ``update(*, total=None,
    progress=None, advance=None)`` method modelled on upstream
    Textual's ``ProgressBar.update``.  We follow the task description
    (the proximate instruction wins); the spec's intent - "no public
    advance method" - is preserved by routing ``advance`` through the
    ``update()`` keyword surface rather than exposing a bare
    ``advance()`` method.  Direct assignment (``bar.progress = n`` and
    ``bar.progress += n``) remains the canonical surface.
  * The task description adds two reactives that FR-TUI-51 does not
    mention: ``show_bar`` and ``total``.  ``show_bar`` is a useful
    cosmetic toggle (hide the bar, show only the percentage); ``total``
    being reactive lets callers retune the scale without recreating
    the widget.  Both fall under the "reactive sugar" the design doc
    allows widget authors to layer on top of the FR-listed minimum.
  * Mono fallback: FR-TUI-51 keys the fallback off ``color_system !=
    "mono"``.  The compositor / Console exposes a colour system via
    its own Console instance, but a Widget at render-time has no
    handle to that Console (the Console lives behind the compositor's
    private slot per design §7.3).  We expose ``self.mono`` as a plain
    boolean attribute defaulting to False; the harness / App sets it
    true for the mono-fallback acceptance test.  Wiring it to the
    compositor's colour system is a Phase 5b polish item.
  * ETA computation: the spec says "small ring buffer of the most
    recent ``progress`` assignment timestamps".  We keep a fixed-size
    deque of ``(monotonic_seconds, progress_value)`` tuples (default
    8 entries) and compute the ETA as the linear extrapolation from
    the oldest-to-newest delta.  ``time`` is imported lazily so
    test harnesses that monkey-patch the clock land before the first
    sample.  When fewer than two samples exist, ETA shows ``--:--``.

Why a separate cached ``_bar_text`` is not used (cf. Static): the
ProgressBar's render output depends on both ``progress`` and the
target ``_region.width`` (computed each frame by the compositor).
Caching by progress alone would invalidate the cache on every resize
anyway, so we build the Text per render call.  The build is O(width)
and render() is called once per dirty frame, not per asyncio tick;
this is well within the synthesis NFR-TUI-2 frame-time budget.
"""

# Widget brings the R3 guard, Reactive descriptor host, refresh()
# plumbing, and DOMNode-derived id/classes routing.  Same import shape
# as static.py - this widget extends the base directly rather than
# subclassing Static, because the render output is a composed Text
# rather than a single content slot.
from .._textual.widget import Widget

# Reactive descriptor for the five tracked slots (total, progress,
# show_percentage, show_bar, show_eta).  Each one fires watch_<name>
# and calls refresh() on change.  See reactive.py for the per-write
# protocol.
from .._textual.reactive import Reactive

# @widget is mandatory whenever a Widget subclass declares Reactive,
# BINDINGS, or @on handlers (FR-TUI-28 / R3).  ProgressBar has five
# Reactives and a watch_progress watcher; without the decorator,
# Widget.__init__ would raise MissingWidgetDecoratorError on first
# instantiation.
from .._textual._widget_decorator import widget

# Text is the styled-segment container used for the render() return
# value.  We could return a bare str ("[####    ] 50%") but a Text
# lets us colour the filled and empty regions distinctly once the
# compositor's style layer (§7.2) is wired, without a follow-up
# render-path refactor.
from .._rich.text import Text


# ---------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------

# Module-level so they live in frozen bytes once rather than being
# rebuilt per class lookup.  The eighths string is indexed by integer
# (1..7) to pick the fractional-cell glyph for the partial cell at the
# bar's leading edge.
#
# Index meaning: ``_EIGHTHS[k]`` is the glyph occupying k/8ths of a
# cell from the left.  ``_FULL`` (█) and ``_EMPTY`` (space) frame
# the bar interior.  ``_MONO_FULL`` is the ASCII fallback per
# FR-TUI-51.

_FULL = "█"  # FULL BLOCK
_EMPTY = " "
_EIGHTHS = (
    "",          # 0/8 = empty cell - never indexed (we draw _EMPTY)
    "▏",    # 1/8 LEFT ONE EIGHTH BLOCK
    "▎",    # 2/8 LEFT ONE QUARTER BLOCK
    "▍",    # 3/8 LEFT THREE EIGHTHS BLOCK
    "▌",    # 4/8 LEFT HALF BLOCK
    "▋",    # 5/8 LEFT FIVE EIGHTHS BLOCK
    "▊",    # 6/8 LEFT THREE QUARTERS BLOCK
    "▉",    # 7/8 LEFT SEVEN EIGHTHS BLOCK
)

# ASCII fallback per FR-TUI-51 ("ASCII ``#`` blocks otherwise").
_MONO_FULL = "#"

# Default rendering width used when the widget is not yet mounted (no
# _region set by the compositor).  40 columns is the canonical Rich
# Progress default and matches the upstream tests' expectations.  The
# real width comes from self._region.width at render() time once
# mounted.
_DEFAULT_WIDTH = 40

# Number of (timestamp, progress) samples kept for ETA extrapolation.
# A small ring buffer trades a tiny memory cost for jitter resistance
# on the rate computation.  8 entries at ~10 Hz update rate gives
# ~0.8 s of history, which is enough to smooth out single-tick stalls
# without lagging real rate changes.
_ETA_RING_SIZE = 8


# ---------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------


def _format_mmss(seconds):
    """Format a non-negative second count as ``mm:ss``.

    Used by the ETA renderer.  Returns ``--:--`` for None (insufficient
    samples) or negative inputs (clock skew / non-monotonic progress).
    Caps display at ``99:59`` rather than overflowing to three-digit
    minutes - upstream Rich does the same.
    """
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(seconds)
    # Cap; >99 minutes of ETA is effectively "unknown" for a TUI.
    if total >= 99 * 60 + 59:
        return "99:59"
    return "%02d:%02d" % (total // 60, total % 60)


# ---------------------------------------------------------------------
# ProgressBar.
# ---------------------------------------------------------------------


@widget
class ProgressBar(Widget):
    """A non-focusable percentage indicator (FR-TUI-51).

    Render output shape::

        ████████▉      53%  00:12

    The interior block run grows by fractional cells; the right-hand
    fields ``NN%`` and ``mm:ss`` are conditional on ``show_percentage``
    and ``show_eta``.  The bar itself is suppressed when
    ``show_bar=False``.

    Public mutation surface per FR-TUI-51:
      * ``bar.progress = n`` - direct assignment.
      * ``bar.progress += n`` - augmented assignment (same code path).
      * ``bar.update(progress=..., total=..., advance=...)`` - keyword
        sugar for callers porting from upstream Textual.  See module
        docstring for the spec-vs-task tension that admits this.

    Not focusable.  Inherits ``Widget.can_focus = False``; no override.
    """

    # ------------------------------------------------------------------
    # Reactive slots (FR-TUI-19 declarations).
    # ------------------------------------------------------------------

    # ``total`` is the upper bound for ``progress``.  Reactive so the
    # callers can rescale the bar at runtime (e.g. when a download's
    # advertised content-length changes after the first chunk).
    # ``layout=False`` because a total change does not change the
    # widget's own size - only the fill ratio.
    total = Reactive(100)

    # ``progress`` is the current value in ``[0, total]``.  Negative
    # values and values above ``total`` are clamped in
    # ``validate_progress`` so the render() arithmetic never produces
    # NaN or a bar wider than its region.  Per FR-TUI-51 every
    # assignment triggers a redraw - which is exactly what the
    # Reactive descriptor's refresh()-call already does.
    progress = Reactive(0)

    # Display toggles.  Each is a Reactive so user code that wants to
    # animate "hide percentage while computing" can set it from a
    # watcher and rely on the automatic redraw.  Defaults match
    # FR-TUI-51 wording: percentage on, ETA off.
    show_percentage = Reactive(True)

    # ``show_bar`` is the task-description-added toggle - not in
    # FR-TUI-51 directly.  See module-doc deviation note.  Useful for
    # "show percentage only, no bar" layouts.
    show_bar = Reactive(True)

    show_eta = Reactive(False)

    # DEFAULT_CSS parity with upstream Textual.  No styles are read
    # from this in v0.1; carrying the slot lets v0.2 TCSS land without
    # an API change.
    DEFAULT_CSS = ""

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(
        self,
        total=100,
        progress=0,
        *,
        show_percentage=True,
        show_bar=True,
        show_eta=False,
        mono=False,
        name=None,
        id=None,
        classes="",
    ):
        # Widget.__init__ runs the R3 guard, sets up DOMNode topology,
        # installs the per-instance message pump.  Forward the
        # FR-TUI-52 standard kwargs unchanged.
        Widget.__init__(self, id=id, classes=classes)

        # ``name`` mirrors the Static accept-and-stash pattern; widgets
        # do not yet carry a name slot, but accepting the kwarg keeps
        # upstream Textual user code that always passes ``name=`` from
        # raising TypeError.
        self._name = name

        # ETA ring buffer.  Stored as a plain list (Phase 4b ships
        # without a deque shim); we cap growth in _record_progress by
        # discarding from the front.  Allocated up front so the first
        # render() does not pay an O(n) initial-grow cost.
        self._eta_samples = []

        # Mono fallback flag.  See module docstring for why this is an
        # instance attribute rather than a Reactive: it is set once
        # (by the harness or by Phase 5b App wiring) and read on every
        # render(); the per-write Reactive overhead is unjustified.
        self.mono = mono

        # Assign the display reactives.  These go through the
        # descriptor and store into the per-instance private slot.
        # We do this *after* Widget.__init__ so the @widget MRO merge
        # on this class has already bound the descriptor names.
        self.show_percentage = show_percentage
        self.show_bar = show_bar
        self.show_eta = show_eta

        # ``total`` first, then ``progress``, so validate_progress
        # (which clamps against ``self.total``) sees the user's
        # intended ceiling rather than the default 100.  Without this
        # ordering, ``ProgressBar(total=50, progress=30)`` would clamp
        # progress to 30 (fine for this case) but ``ProgressBar(
        # total=200, progress=150)`` would clamp to 100, which is the
        # wrong answer.
        self.total = total
        self.progress = progress

    # ------------------------------------------------------------------
    # update() - the task-description public mutation surface.
    # ------------------------------------------------------------------

    def update(self, *, total=None, progress=None, advance=None):
        """Mutate any of total / progress; ``advance`` adds to progress.

        Keyword-only so call sites are self-documenting (``bar.update(
        progress=50)`` rather than ``bar.update(50)`` which is
        ambiguous against the ``total`` slot).  Each non-None argument
        goes through the corresponding Reactive's __set__, which fires
        watch_<name>, refresh(), and the implicit redraw signal.

        Argument order: ``total`` first (so a combined ``update(
        total=200, progress=150)`` does not transiently clamp progress
        against the old total), then ``progress``, then ``advance``.
        ``advance`` reads through the descriptor *after* the explicit
        ``progress=`` assignment, so ``update(progress=10, advance=5)``
        ends up at 15.
        """
        # total before progress so validate_progress clamps against
        # the new ceiling, not the stale one.  Same rationale as in
        # __init__.
        if total is not None:
            self.total = total
        if progress is not None:
            self.progress = progress
        if advance is not None:
            # Augmented assignment goes through __set__ exactly once;
            # the watcher and refresh fire once, not twice.  This is
            # the same shape as ``bar.progress += advance`` which is
            # the v0.1 spec's canonical public mutation surface.
            self.progress = self.progress + advance

    # ------------------------------------------------------------------
    # Reactive validators.
    # ------------------------------------------------------------------

    def validate_progress(self, value):
        """Clamp progress into ``[0, self.total]`` (FR-TUI-51).

        Runs inside the Reactive __set__ path before the equality
        check, so a write of an out-of-range value that clamps to the
        existing slot is a no-op redraw - matches upstream Textual.

        ``self.total`` is read through the descriptor; if it has not
        been bound yet (during the very first __init__ where this
        runs before ``self.total = total``), the descriptor returns
        the default 100, which is the correct ceiling for the
        initial-progress write.
        """
        if value < 0:
            return 0
        ceiling = self.total
        if value > ceiling:
            return ceiling
        return value

    def validate_total(self, value):
        """Clamp total to a non-negative integer.

        A negative total would invert the bar arithmetic; a total of
        zero is permitted (the render path special-cases it to "0%").
        """
        if value < 0:
            return 0
        return value

    # ------------------------------------------------------------------
    # Reactive watchers.
    # ------------------------------------------------------------------

    def watch_progress(self, old, new):
        """Record an ETA sample and trigger a redraw.

        The Reactive __set__ already calls refresh() after this
        watcher returns, so the explicit refresh() call here is for
        the FR-TUI-51 documentation contract ("Assignment triggers a
        redraw") - we mirror Static's pattern of an explicit
        refresh() at the watcher site for subclassers who override
        and forget to invoke super.

        Arity 3 (self, old, new) - declared so subclasses can compute
        per-write deltas (e.g. animated easing); the @widget decorator
        records the arity at class-decoration time and the descriptor
        dispatches accordingly.
        """
        # Record the (timestamp, value) sample iff the ETA field is
        # active.  Off-path - if no caller asked for ETA, we skip the
        # time-module import entirely.  ``time`` is imported lazily
        # because the picolet runtime's MicroPython port exposes
        # ``time.monotonic`` but the import-time cost on a memory-
        # constrained device is non-trivial.
        if self.show_eta:
            self._record_progress(new)

        # Explicit refresh().  The Reactive __set__ already calls it
        # after the watcher returns (see reactive.py); this duplicate
        # call is the documented FR-TUI-51 redraw signal at the
        # watcher site and is idempotent (refresh() just sets
        # _dirty = True).
        self.refresh()

    def watch_total(self, old, new):
        """Trigger a redraw on total change.

        The Reactive __set__ already calls refresh() automatically,
        but we provide the watcher so subclasses can hook the event
        (e.g. to re-fetch ETA samples from a stored source on a
        scale change).  Calling self.refresh() here is idempotent and
        documents the redraw contract at the call site.
        """
        self.refresh()

    # ------------------------------------------------------------------
    # ETA bookkeeping.
    # ------------------------------------------------------------------

    def _record_progress(self, value):
        """Append a (timestamp, value) sample to the ETA ring buffer.

        Capped at ``_ETA_RING_SIZE`` entries; older samples drop from
        the front.  ``time.monotonic`` is the picolet asyncio loop's
        canonical clock; we import it lazily so widgets that never
        enable ETA do not pay the import cost.
        """
        # Lazy import - see method docstring rationale.  The first
        # call warms the import cache; subsequent calls hit the cached
        # module.  ``time`` is in the MicroPython core - no shim
        # needed.
        import time as _time
        now = _time.monotonic()
        self._eta_samples.append((now, value))
        # Cap from the front.  Use slice assignment rather than
        # repeated pop(0) to keep the operation O(n) once rather than
        # O(n^2) over the trim.
        if len(self._eta_samples) > _ETA_RING_SIZE:
            self._eta_samples = self._eta_samples[-_ETA_RING_SIZE:]

    def _compute_eta_seconds(self):
        """Extrapolate seconds-to-completion from the ring buffer.

        Returns None when fewer than two samples exist (insufficient
        data) or when the rate is non-positive (progress stalled or
        reversed) - both cases render as ``--:--``.

        The estimate uses oldest-to-newest delta rather than a per-
        sample regression: the buffer is small enough that the
        two-point estimate is statistically equivalent and an order
        of magnitude cheaper.  Upstream Rich uses the same shape.
        """
        samples = self._eta_samples
        if len(samples) < 2:
            return None
        t0, p0 = samples[0]
        t1, p1 = samples[-1]
        dt = t1 - t0
        dp = p1 - p0
        if dt <= 0 or dp <= 0:
            # Stalled or reversed - upstream Rich shows --:-- here.
            return None
        remaining = self.total - p1
        if remaining <= 0:
            return 0
        # Linear extrapolation.  Rate (units/sec) * remaining gives
        # seconds-to-completion.  We compute as remaining * dt / dp
        # to avoid a division by a possibly small dp twice.
        return remaining * dt / dp

    # ------------------------------------------------------------------
    # render() - the §7.1 contract.
    # ------------------------------------------------------------------

    def render(self):
        """Build a Text containing bar + percentage + ETA (§7.1).

        Returned as a Text instance so the compositor's
        render_lines() call sees a single-line styled renderable.
        The output shape is::

            <bar><space><pct>%<space><mm:ss>

        with ``<bar>`` suppressed when ``show_bar`` is False,
        ``<pct>%`` suppressed when ``show_percentage`` is False, and
        ``<mm:ss>`` suppressed when ``show_eta`` is False.  Multiple
        suppressions collapse cleanly (no orphan spaces).

        Width comes from ``self._region.width`` when the widget has
        been laid out (compositor sets ``_region`` per Phase 4c
        contract); otherwise we fall back to ``_DEFAULT_WIDTH``.

        Fast path: the heavy lifting (markup parse, span allocation)
        is avoided - we build a Text by appending plain strings.  The
        styled-region split lands in Phase 5b when the compositor's
        style layer is online.
        """
        # Width discovery.  ``_region`` is initialised to a 0-width
        # NULL_REGION sentinel by DOMNode and overwritten by the layout
        # pass once the widget has been mounted into an App.  In
        # standalone unit tests (no App, no layout) the region stays
        # null; treat that as "fall back to default width".  We can't
        # use ``getattr(..., None)`` here because the slot is always
        # present - the width-is-zero check is the meaningful one.
        region = getattr(self, "_region", None)
        if region is not None and region.width > 0:
            total_width = region.width
        else:
            total_width = _DEFAULT_WIDTH

        # Compute the fill ratio.  Special-case total == 0 to "empty
        # bar, 0%" rather than ZeroDivisionError - matches upstream
        # Rich Progress's behaviour on a zero-total task.
        if self.total <= 0:
            ratio = 0.0
        else:
            ratio = self.progress / self.total
            # Clamp - validate_progress already enforces this, but a
            # subclass that bypasses validators or a race with total
            # change could let ratio escape [0, 1].  Defensive.
            if ratio < 0:
                ratio = 0.0
            elif ratio > 1:
                ratio = 1.0

        # Reserve space for the trailing fields.  Each is 5 cells
        # (``" 100%"`` worst case, ``" mm:ss"`` always 6 with the
        # leading space).  Sum them up so the bar width is
        # total_width - reserved.
        reserved = 0
        if self.show_percentage:
            # " 100%" = 5 cells worst case; we always reserve 5 to
            # avoid the bar twitching by one cell as progress crosses
            # 99 -> 100%.
            reserved += 5
        if self.show_eta:
            # " mm:ss" = 6 cells with leading space.
            reserved += 6

        # Build the Text incrementally.  Start with an empty Text and
        # append plain strings; the segments land unstyled, which is
        # the v0.1 wire format.  Phase 5b styles the filled / empty
        # regions distinctly.
        result = Text()

        if self.show_bar:
            bar_width = total_width - reserved
            if bar_width < 1:
                # Region too narrow to fit anything sensible; the
                # compositor crops oversized output to region width
                # anyway, but a bar_width of 0 or negative would
                # produce empty output here - which is the right
                # answer (no bar fits).
                bar_width = 0
            result.append(self._build_bar(bar_width, ratio))

        if self.show_percentage:
            # Right-padded into the reserved 5 cells so the bar
            # doesn't reflow on the 9% -> 10% transition.  ``%3d`` is
            # the format-string spelling that gives "  9%" / " 10%" /
            # "100%".
            pct = int(ratio * 100)
            result.append(" %3d%%" % pct)

        if self.show_eta:
            eta = self._compute_eta_seconds()
            result.append(" " + _format_mmss(eta))

        return result

    # ------------------------------------------------------------------
    # _build_bar - the per-cell glyph computation.
    # ------------------------------------------------------------------

    def _build_bar(self, width, ratio):
        """Return a ``width``-cell string for the bar interior.

        Mono mode uses ASCII ``#`` for filled cells and space for
        empty (FR-TUI-51 fallback).  Colour mode uses the eighths
        glyphs for fractional cells at the boundary.

        Algorithm (colour path):
          1. Convert ``ratio * width`` to a count of *eighths* of a
             cell (``ratio * width * 8`` rounded down).
          2. Full cells: ``eighths // 8``.
          3. Fractional cell: ``eighths % 8`` -> glyph index, drawn at
             position ``full_cells`` iff > 0.
          4. Remaining cells fill with spaces.

        Algorithm (mono path):
          1. Full cells: ``int(round(ratio * width))``.
          2. The rest are spaces.
          No fractional cells - the ``#`` glyph is unsplittable.
        """
        if width <= 0:
            return ""

        if self.mono:
            # FR-TUI-51 ASCII fallback.  Round to the nearest cell so
            # the bar visibly fills the rightmost column on ratio=1.
            full = int(round(ratio * width))
            if full > width:
                full = width
            return _MONO_FULL * full + _EMPTY * (width - full)

        # Colour path - eighths granularity.
        eighths = int(ratio * width * 8)
        full_cells = eighths // 8
        partial = eighths % 8
        # Cap at width: a ratio of exactly 1 produces eighths ==
        # width * 8, full_cells == width, partial == 0 - no overflow.
        # The check below guards against floating-point edge cases
        # where ratio is just above 1 due to subclass mischief.
        if full_cells > width:
            full_cells = width
            partial = 0

        # Build: full block run + optional partial glyph + empty pad.
        parts = [_FULL * full_cells]
        used = full_cells
        if partial > 0 and used < width:
            parts.append(_EIGHTHS[partial])
            used += 1
        if used < width:
            parts.append(_EMPTY * (width - used))
        return "".join(parts)
