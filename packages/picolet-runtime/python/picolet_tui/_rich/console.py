"""picolet_tui._rich.console — the Textual-compositor-only Console subset.

Ported from Textualize/rich master @ 285d9e94e268daf3ef159ca41e113a2e11b625e1
(``rich/console.py``, 2698 LoC upstream).  Tier 5 of the Rich subset:
the keystone above text/markup/segment/style/measure/protocol.

Synthesis §R2 ("the Console spike") authorises this aggressive trim:
Textual's compositor only ever calls ``Console.render_lines`` (with
``render`` / ``measure`` / ``render_str`` as transitive deps).  Every
other public method on upstream's Console is a user-facing print/log/
record/export path that the Textual RenderHost never touches.  If the
trim could not fit the API in <=~1200 LoC the spec authorised forking
into a Textual-specific RenderHost; the trim collapsed cleanly to the
~6 methods listed under "Goals" in the porting brief, well under
budget, so we keep the Console name and avoid the fork.

REMOVED vs upstream
-------------------
* ``capture`` / ``begin_capture`` / ``end_capture`` and the ``Capture``
  context manager — the App never re-reads its own rendered bytes; it
  hands segments straight to tuiterm.write.

* ``export_html`` / ``export_svg`` / ``export_text`` and the
  ``record=True`` buffer — the v0.1 spec ships no record/export path
  (synthesis 02 §"Rich modules out of scope").  Dropping these takes
  ``_emoji_replace``, ``_export_format``, ``themes``, ``_log_render``,
  ``terminal_theme`` (SVG default), ``pager.py``, ``styled.py`` with
  it from the call graph.

* ``log`` / ``print`` / ``out`` / ``status`` / ``rule`` / ``inspect``
  / ``live`` / ``screen`` / ``pager`` — every user-facing print API.
  Textual renders Widgets via ``render_lines``, never via ``print``.

* ``set_alt_screen`` / ``show_cursor`` / ``bell`` / ``control`` /
  ``update_screen`` / ``update_screen_lines`` and ``ScreenContext`` —
  the App owns the alt-screen toggle and cursor visibility through
  ``tuiterm`` directly (spec FR-TUI-3 / FR-TUI-4 / FR-TUI-6).  The
  Console subset never writes control sequences.

* ``input`` and ``_input`` — Textual reads keys from the App's event
  pump (FR-TUI-7..10), not via ``Console.input``.

* The whole "soft-wrap" branch and ``terminal`` detection — no probing
  of ``isatty``, ``os.get_terminal_size``, ``WindowsConsoleFeatures``,
  ``COLORTERM``, ``TERM``, or ``NO_COLOR``.  ``is_terminal`` is fixed
  to True (the App always paints to a terminal-shaped surface),
  ``encoding`` is fixed to ``"utf-8"``, ``size`` defaults to 80×24
  and is otherwise injected by the App when it wires the surface
  resize.

* ``_detect_color_system`` / ``ColorSystem`` autodetect — the App
  selects a color system once at startup and pins it on the Console
  (synthesis D5 "16-bit colour everywhere by default").  Per-Style
  downgrade is handled by ``color.py`` on the Style basis already.

* Theme/ThemeStack/``push_theme``/``pop_theme``/``use_theme`` — the
  v0.1 widget set has no theme switching API.  ``get_style`` falls
  through to ``Style.parse`` directly; widgets that need named styles
  resolve them through the global ``Style`` DSL.

* RenderHook / ``push_render_hook`` / ``pop_render_hook`` — only the
  ``progress``/``status`` paths register hooks upstream, both dropped.

* Thread-local buffer (``ConsoleThreadLocals``, ``_lock``,
  ``_enter_buffer`` / ``_exit_buffer`` / ``_check_buffer``,
  ``_buffer_index``) — synthesis D6 forbids worker threads, so the
  whole concurrency-buffering apparatus is unreachable.  ``_lock`` is
  the no-op RLock from the threading shim but it is never read.

* ``set_live`` / ``clear_live`` / Live integration — the Live
  context-manager is not in the v0.1 widget set; ``ProgressBar``
  (FR-TUI-51) is a pure Widget, not a Live overlay.

* ``__rich_console__`` ConsoleRenderable Protocol class and
  ``runtime_checkable`` decorator — Rich's renderable detection (and
  ours) uses ``hasattr(obj, "__rich_console__")``, not isinstance
  against the Protocol.  Per porting rule 4, ``runtime_checkable``
  Protocols are dropped.  ``RenderableType`` is a runtime union of
  whatever has the dunder; the alias is kept as a placeholder symbol
  for hint-only callers.

* The Jupyter branch (``_is_jupyter``, ``JUPYTER_DEFAULT_COLUMNS``,
  ``JUPYTER_DEFAULT_LINES``, ``_jupyter_renderable``) — Jupyter is
  the headline non-target environment.

* The Windows legacy console branch (``legacy_windows``,
  ``detect_legacy_windows``, ``WindowsConsoleFeatures``, ``WINDOWS``,
  ``safe_box``) — synthesis 03 says we target the post-VT Windows
  conhost / Linux unix terminal stack only.

* ``_check_buffer`` / ``_render_buffer`` / ``_write_buffer`` /
  ``_emit_segments`` — the segment->bytes path lives in
  ``tuiterm.write`` on the C side, not in Python.  ``render_lines``
  returns ``List[List[Segment]]`` and the caller iterates.

* ``inspect`` / ``show_locals`` / ``Traceback`` integration — out of
  v0.1 scope; the App formats exceptions via a smaller helper.

* ``_environ`` plumbing — see "no terminal probing" above.

NOT REMOVED (intentional)
-------------------------
* ``ConsoleDimensions`` — implemented as a tuple subclass (typing
  shim does not ship ``NamedTuple``, same pattern as
  ``measure.Measurement`` and ``color_triplet.ColorTriplet``).
  Tuple/index/named-attr access mirrors the NamedTuple contract Rich
  callers rely on.

* ``ConsoleOptions`` — kept as a ``__slots__`` class (not a
  ``@dataclass``; the shim's dataclass doesn't ship ``field``
  semantics we need here, and Console copies options ~once per
  render so allocation cost dominates anything else).  Public
  attribute list and ``copy / update / update_width / update_height
  / reset_height / update_dimensions`` are all preserved — Rich's
  renderables (Padding, Align, Group) call these directly.

* ``NewLine`` and ``Group`` and the ``@group`` decorator — Rich's
  intra-renderable composition primitives.  ``Padding`` / ``Align``
  / ``Text`` all yield them in their own ``__rich_console__``.

* ``Console.render`` / ``render_lines`` / ``render_str`` /
  ``measure`` / ``get_style`` / ``options`` / ``size`` / ``width`` /
  ``height`` — the Textual-compositor public surface.  Signatures are
  byte-identical to upstream so the upstream Rich test fixtures port
  unmodified where they touch only this subset.

Spec coverage
-------------
Supports FR-TUI-23..28 (Widget rendering): every Widget.render() call
flows through Console.render_lines to produce ``List[List[Segment]]``
for the compositor; ``options.max_width`` is the per-call cell budget
the layout pass computed.

Supports FR-TUI-29 (Container / Vertical / Horizontal layout) and
FR-TUI-30 (intrinsic sizing): ``measure`` and ``measure_renderables``
are the gate the layout pass calls.

Supports FR-TUI-32..37 (Style DSL): ``render_str`` is the parse path
from a user-supplied ``"[bold]..."`` string to a styled ``Text``
renderable.  ``get_style`` parses bare definitions on demand.

Supports NFR-TUI-19 (60 KiB ``_rich/`` budget): the trim brought
console.py from 2698 LoC upstream to ~580 LoC here, freeing room for
the Phase 3d widget renderables.

Supports NFR-TUI-6 (no `lru_cache(maxsize>128)`): no caches in this
module; the upstream caching path was in the deleted print/export
paths.

Supports NFR-TUI-10 (re1.5 subset): no regex in this module at all.

Supports synthesis D5 (color system selection): ``color_system`` is
an explicit __init__ argument the App pins once; no autodetect.
"""

from collections import namedtuple
from picolet_tui._shims.typing import (
    Callable,
    Iterable,
    List,
    Optional,
)

from . import errors
from .color import ColorSystem
from .markup import render as render_markup
from .measure import Measurement, measure_renderables
from .protocol import is_renderable, rich_cast
from .segment import Segment
from .style import Style
from .text import Text


# Public type aliases.  Under MicroPython these become _Placeholder
# singletons via the typing shim, but the names must exist so callers
# that ``from picolet_tui._rich.console import RenderableType`` keep
# importing.  Upstream Rich also exports them.
HighlighterType = Callable
JustifyMethod = str  # "default" | "left" | "center" | "right" | "full"
OverflowMethod = str  # "fold" | "crop" | "ellipsis" | "ignore"
RenderableType = object  # any obj with __rich_console__ or a str
RenderResult = Iterable  # Iterable[Union[RenderableType, Segment]]


# --- ConsoleDimensions --------------------------------------------------------
# namedtuple subclass — MicroPython cannot call tuple.__new__ in a
# subclass, and its namedtuple is a C builtin (zero frozen-bytes cost).
# Tuple iteration / unpacking / indexed access all come from tuple; the
# named attributes come from namedtuple.

class ConsoleDimensions(namedtuple("ConsoleDimensions", ("width", "height"))):
    """Size of the terminal: ``ConsoleDimensions(width, height)``."""

    __slots__ = ()


# --- ConsoleOptions -----------------------------------------------------------
# Kept as a __slots__ class rather than a @dataclass: shim's @dataclass
# does not give us a measurable win here, and the slot layout shrinks
# the per-options heap footprint (Console.options is called once per
# render dispatch).

class NoChange:
    """Sentinel for ConsoleOptions.update() — distinguishes "leave as is"
    from "set to None" the same way upstream Rich does.
    """
    pass


NO_CHANGE = NoChange()


class ConsoleOptions:
    """Options handed to a renderable's ``__rich_console__`` method.

    Public attributes (all writable; renderables in Tier 4 will
    mutate copies via ``.update(...)``):

      size              — ConsoleDimensions
      legacy_windows    — fixed False (synthesis: no legacy Windows)
      min_width         — int, the smallest acceptable render width
      max_width         — int, the largest acceptable render width
      is_terminal       — fixed True
      encoding          — fixed "utf-8"
      max_height        — int, container height (starts as terminal h)
      justify           — Optional[str]
      overflow          — Optional[str]
      no_wrap           — Optional[bool]
      highlight         — Optional[bool]
      markup            — Optional[bool]
      height            — Optional[int]
    """

    __slots__ = (
        "size",
        "legacy_windows",
        "min_width",
        "max_width",
        "is_terminal",
        "encoding",
        "max_height",
        "justify",
        "overflow",
        "no_wrap",
        "highlight",
        "markup",
        "height",
    )

    def __init__(
        self,
        *,
        size,
        legacy_windows=False,
        min_width=1,
        max_width=80,
        is_terminal=True,
        encoding="utf-8",
        max_height=25,
        justify=None,
        overflow=None,
        no_wrap=False,
        highlight=None,
        markup=None,
        height=None,
    ):
        self.size = size
        self.legacy_windows = legacy_windows
        self.min_width = min_width
        self.max_width = max_width
        self.is_terminal = is_terminal
        self.encoding = encoding
        self.max_height = max_height
        self.justify = justify
        self.overflow = overflow
        self.no_wrap = no_wrap
        self.highlight = highlight
        self.markup = markup
        self.height = height

    @property
    def ascii_only(self):
        # encoding is pinned to "utf-8" by the App, so this is False in
        # practice; kept for upstream API parity.
        return not self.encoding.startswith("utf")

    def copy(self):
        # Keyword construction through __init__ rather than the CPython
        # `cls.__new__(cls)` + slot-walk trick: MicroPython cannot call
        # __new__ on a user class.  __init__ assigns exactly the slot
        # list, so this is the same shallow copy.
        return ConsoleOptions(
            size=self.size,
            legacy_windows=self.legacy_windows,
            min_width=self.min_width,
            max_width=self.max_width,
            is_terminal=self.is_terminal,
            encoding=self.encoding,
            max_height=self.max_height,
            justify=self.justify,
            overflow=self.overflow,
            no_wrap=self.no_wrap,
            highlight=self.highlight,
            markup=self.markup,
            height=self.height,
        )

    def update(
        self,
        *,
        width=NO_CHANGE,
        min_width=NO_CHANGE,
        max_width=NO_CHANGE,
        justify=NO_CHANGE,
        overflow=NO_CHANGE,
        no_wrap=NO_CHANGE,
        highlight=NO_CHANGE,
        markup=NO_CHANGE,
        height=NO_CHANGE,
    ):
        """Return a copy with the given fields updated."""
        options = self.copy()
        if not isinstance(width, NoChange):
            options.min_width = options.max_width = max(0, width)
        if not isinstance(min_width, NoChange):
            options.min_width = min_width
        if not isinstance(max_width, NoChange):
            options.max_width = max_width
        if not isinstance(justify, NoChange):
            options.justify = justify
        if not isinstance(overflow, NoChange):
            options.overflow = overflow
        if not isinstance(no_wrap, NoChange):
            options.no_wrap = no_wrap
        if not isinstance(highlight, NoChange):
            options.highlight = highlight
        if not isinstance(markup, NoChange):
            options.markup = markup
        if not isinstance(height, NoChange):
            if height is not None:
                options.max_height = height
            options.height = None if height is None else max(0, height)
        return options

    def update_width(self, width):
        """Return a copy with min_width == max_width == width."""
        options = self.copy()
        options.min_width = options.max_width = max(0, width)
        return options

    def update_height(self, height):
        """Return a copy with height and max_height set."""
        options = self.copy()
        options.max_height = options.height = height
        return options

    def reset_height(self):
        """Return a copy with height cleared (max_height kept)."""
        options = self.copy()
        options.height = None
        return options

    def update_dimensions(self, width, height):
        """Return a copy with width and height both set."""
        options = self.copy()
        options.min_width = options.max_width = max(0, width)
        options.height = options.max_height = height
        return options


# --- Renderables --------------------------------------------------------------
# NewLine and Group are the two intra-renderable composition primitives
# Tier 4 renderables (Padding, Align, Text) yield directly. Keep them in
# console.py so the import path matches upstream Rich.

class NewLine:
    """A renderable that emits N newline segments. Used by Padding /
    Align to insert blank lines between renderables.
    """

    def __init__(self, count=1):
        self.count = count

    def __rich_console__(self, console, options):
        yield Segment("\n" * self.count)


class Group:
    """Wraps an iterable of renderables; renders them in sequence with
    optional fit-to-content sizing (vs fill-available-width).

    Used by the ``@group`` decorator and by call sites that want a
    composite renderable they can hand to Padding/Align.
    """

    def __init__(self, *renderables, fit=True):
        self._renderables = renderables
        self.fit = fit
        # Cache the realised renderables list so repeated rendering
        # of the same group does not re-walk the iterable.
        self._render = None

    @property
    def renderables(self):
        if self._render is None:
            self._render = list(self._renderables)
        return self._render

    def __rich_measure__(self, console, options):
        if self.fit:
            return measure_renderables(console, options, self.renderables)
        else:
            return Measurement(options.max_width, options.max_width)

    def __rich_console__(self, console, options):
        yield from self.renderables


def group(fit=True):
    """Decorator: turn a method yielding renderables into one returning a Group.

    Usage:

        @group()
        def render_header(self):
            yield Text(self.title)
            yield Padding(self.body, 1)

    The decorated method returns Group(...) so it can be yielded back
    out of another renderable's ``__rich_console__``.
    """

    def decorator(method):
        # No @wraps here — functools.wraps from the shim is fine but
        # the resulting wrapper is never introspected at runtime under
        # MicroPython (no inspect module to walk it). Skip the cost.
        def _replace(*args, **kwargs):
            renderables = method(*args, **kwargs)
            return Group(*renderables, fit=fit)

        return _replace

    return decorator


# --- _NullFile ----------------------------------------------------------------
# Replaces upstream's ``rich._null_file.NULL_FILE``: a write-and-discard
# sink. Console.__init__ accepts it as the file argument so the public
# constructor signature stays compatible with callers that pass file=...
# even though we never call .write() on it (Textual wires segment output
# through tuiterm directly).

class _NullFile:
    """Discards everything written to it. Default Console.file."""

    closed = False

    def write(self, _text):
        pass

    def flush(self):
        pass

    def close(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return -1

    def readable(self):
        return False

    def writable(self):
        return True


NULL_FILE = _NullFile()


# --- Console ------------------------------------------------------------------

# Color-system name -> ColorSystem enum, used by the explicit init arg.
# No "auto" — the App pins this once based on tuiterm's capability
# probe (synthesis D5).
COLOR_SYSTEMS = {
    "standard": ColorSystem.STANDARD,
    "256": ColorSystem.EIGHT_BIT,
    "truecolor": ColorSystem.TRUECOLOR,
    "windows": ColorSystem.WINDOWS,
}

_COLOR_SYSTEMS_NAMES = {system: name for name, system in COLOR_SYSTEMS.items()}


# Module-private highlighter sentinel — null highlighter that returns
# its input unchanged. Used when render_str's highlight kwarg resolves
# False. Lazily imported to avoid the highlighter.py import at module
# load (it pulls regex tables).
_null_highlighter = None


def _get_null_highlighter():
    global _null_highlighter
    if _null_highlighter is None:
        from .highlighter import NullHighlighter
        _null_highlighter = NullHighlighter()
    return _null_highlighter


class Console:
    """The Textual-compositor-only Console.

    Constructor accepts (and silently ignores beyond storing) the
    upstream Rich kwargs that user code or third-party widgets may
    still pass at import time — see synthesis 02 §"Console kwargs
    Textual passes" for the curated subset.

    The only methods the compositor exercises are:

      options        — default ConsoleOptions for this Console
      size / width / height
      render(renderable, options) -> Iterable[Segment]
      render_lines(renderable, options, *, style=None, pad=True,
                   new_lines=False) -> List[List[Segment]]
      render_str(text, *, style="", markup=None, ...) -> Text
      measure(renderable, *, options=None) -> Measurement
      get_style(name, *, default=None) -> Style

    Args (only the ones we honour):
      color_system    — "standard"/"256"/"truecolor"/"windows"/None
      width, height   — pin the console size; None falls back to 80/24
      file            — pinned to _NullFile(); accepted for API parity
      stdin           — accepted, ignored (App owns input)
      markup          — default for render_str's markup arg
      highlight       — default for render_str's highlight arg
      style           — default style applied to all segments
      no_color        — drops color from emitted segments (Style does
                        the actual downgrade per-style)
      tab_size        — used by Text's expand_tabs
      legacy_windows  — accepted, pinned to False
    """

    def __init__(
        self,
        *,
        color_system="truecolor",
        force_terminal=None,
        force_jupyter=None,
        force_interactive=None,
        soft_wrap=False,
        theme=None,
        stderr=False,
        file=None,
        stdin=None,
        quiet=False,
        width=None,
        height=None,
        style=None,
        no_color=None,
        tab_size=8,
        record=False,
        markup=True,
        emoji=True,
        emoji_variant=None,
        highlight=True,
        log_time=True,
        log_path=True,
        log_time_format="[%X]",
        highlighter=None,
        legacy_windows=None,
        safe_box=True,
        get_datetime=None,
        get_time=None,
        _environ=None,
    ):
        # We accept and silently drop the print/log/record/jupyter
        # kwargs so callers that pass them via **kwargs do not blow up
        # at construction time. The dropped path is documented in the
        # module docstring under REMOVED.
        del force_terminal, force_jupyter, force_interactive, soft_wrap
        del theme, stderr, quiet, record, emoji, emoji_variant
        del log_time, log_path, log_time_format
        del safe_box, get_datetime, get_time, _environ

        # Resolve color system. None / unknown -> None (monochrome).
        if color_system is None:
            self._color_system = None
        elif color_system in COLOR_SYSTEMS:
            self._color_system = COLOR_SYSTEMS[color_system]
        else:
            # "auto" upstream means probe; here we treat unknown as
            # TRUECOLOR — the App overrides this anyway.
            self._color_system = ColorSystem.TRUECOLOR

        # File sink — accept any object with .write/.flush; default to
        # _NullFile(). Public attribute so widgets that introspect it
        # (rare) keep working.
        self._file = file if file is not None else NULL_FILE
        self.stdin = stdin

        # Pinned per the trim brief.
        self.is_terminal = True
        self.is_dumb_terminal = False
        self.is_jupyter = False
        self.is_interactive = True
        self.encoding = "utf-8"
        self.legacy_windows = False
        self.stderr = False
        self.quiet = False
        self.soft_wrap = False
        self.record = False
        self.safe_box = True

        # Markup / highlight defaults. render_str consults these when
        # its own kwargs are None.
        self._markup = markup
        self._emoji = False  # v0.1 drops emoji name substitution
        self._emoji_variant = None
        self._highlight = highlight

        # Size: explicit (width, height) override; otherwise 80x24.
        # No terminal probing — see REMOVED.
        self._width = width
        self._height = height

        # Global style applied at the start of every render (e.g. for
        # widgets that paint a uniform background). None == no global.
        self.style = style

        # no_color forces Style.render to drop color codes. The App
        # wires this from the COLORTERM env once at startup.
        self.no_color = bool(no_color) if no_color is not None else False

        self.tab_size = tab_size

        # Highlighter: a callable that takes a Text and returns a Text
        # with auto-highlights applied (e.g. number / repr-syntax
        # spans).  Upstream defaults to ReprHighlighter, but the
        # ported RegexHighlighter subclass raises NotImplementedError
        # at call time because re1.5 doesn't support its named-group
        # patterns (research doc 03 ``re`` row, NFR-TUI-10).  Default
        # to NullHighlighter — Textual widgets that want syntax
        # highlighting will install their own callable explicitly.
        if highlighter is None:
            from .highlighter import NullHighlighter
            self.highlighter = NullHighlighter()
        else:
            self.highlighter = highlighter

        # The buffer / live-stack / theme-stack / render-hooks lists
        # are kept as empty placeholders so third-party widgets that
        # check ``len(console._render_hooks)`` etc. don't crash. None
        # of them are populated by this Console subset.
        self._buffer = []
        self._live_stack = []
        self._render_hooks = []
        self._is_alt_screen = False

    def __repr__(self):
        return "<console width={} {}>".format(
            self.width, self._color_system
        )

    # --- File / size accessors ------------------------------------------------

    @property
    def file(self):
        # Upstream returns sys.stdout / sys.stderr depending on the
        # stderr flag; we return whatever _NullFile / user-supplied
        # sink we were given. The Console never writes through it.
        return self._file

    @file.setter
    def file(self, new_file):
        self._file = new_file

    @property
    def color_system(self):
        # Return the upstream-shaped string name (e.g. "truecolor") or
        # None. Style.render consults this when emitting ANSI codes.
        if self._color_system is None:
            return None
        return _COLOR_SYSTEMS_NAMES.get(self._color_system)

    @property
    def options(self):
        """Default ConsoleOptions for this Console."""
        size = self.size
        return ConsoleOptions(
            size=size,
            legacy_windows=self.legacy_windows,
            min_width=1,
            max_width=size.width,
            is_terminal=self.is_terminal,
            encoding=self.encoding,
            max_height=size.height,
        )

    @property
    def size(self):
        """Get the size of the console.

        Returns ``ConsoleDimensions(width, height)``. With both
        ``_width`` and ``_height`` set the pin wins; otherwise the
        unset axis falls back to 80 (width) or 24 (height). The App
        re-sets these on every SIGWINCH-equivalent.
        """
        width = self._width if self._width is not None else 80
        height = self._height if self._height is not None else 24
        return ConsoleDimensions(width, height)

    @size.setter
    def size(self, new_size):
        width, height = new_size
        self._width = width
        self._height = height

    @property
    def width(self):
        return self.size.width

    @width.setter
    def width(self, width):
        self._width = width

    @property
    def height(self):
        return self.size.height

    @height.setter
    def height(self, height):
        self._height = height

    # --- Render pipeline ------------------------------------------------------

    def measure(self, renderable, *, options=None):
        """Measure a renderable's min/max width without rendering it.

        Returns a ``Measurement(minimum, maximum)``; layout passes use
        this to decide column allocation before any segments are
        produced.
        """
        return Measurement.get(self, options or self.options, renderable)

    def render(self, renderable, options=None):
        """Render an object into an iterable of ``Segment`` instances.

        The recursive heart of the compositor: any renderable yielded
        by a parent's ``__rich_console__`` that is not already a
        Segment is fed back through ``render`` until it bottoms out
        in segments. Strings are routed via ``render_str`` so markup
        / highlight defaults apply.
        """
        _options = options or self.options
        if _options.max_width < 1:
            # Defensive: a zero-width allocation means "nothing fits".
            # Returning early prevents downstream renderables from
            # producing a divide-by-zero or recursing on empty cells.
            return

        renderable = rich_cast(renderable)
        if hasattr(renderable, "__rich_console__") and not isinstance(renderable, type):
            render_iterable = renderable.__rich_console__(self, _options)
        elif isinstance(renderable, str):
            text_renderable = self.render_str(
                renderable, highlight=_options.highlight, markup=_options.markup
            )
            render_iterable = text_renderable.__rich_console__(self, _options)
        else:
            raise errors.NotRenderableError(
                "Unable to render {!r}; "
                "a str, Segment or object with __rich_console__ method is required".format(
                    renderable
                )
            )

        try:
            iter_render = iter(render_iterable)
        except TypeError:
            raise errors.NotRenderableError(
                "object {!r} is not renderable".format(render_iterable)
            )

        # Reset height before recursing: a parent's max_height applies
        # to the parent's slot only; children get an unconstrained
        # height and the parent crops in render_lines via islice.
        _options = _options.reset_height()
        _Segment = Segment
        for render_output in iter_render:
            if isinstance(render_output, _Segment):
                yield render_output
            else:
                # Recurse — flatten nested renderables into segments.
                for seg in self.render(render_output, _options):
                    yield seg

    def render_lines(
        self,
        renderable,
        options=None,
        *,
        style=None,
        pad=True,
        new_lines=False,
    ):
        """Render a renderable into a list-of-lines-of-segments.

        This is the ONLY method Textual's compositor actually calls on
        Console. Output shape: ``List[List[Segment]]`` where each
        inner list is one terminal row and the segments within are
        column-ordered.

        ``style``: applied to every emitted segment as a base style
                   (e.g. a panel's background).
        ``pad``:   if True, short lines get padded with a space-
                   segment to the full ``max_width`` so the compositor
                   doesn't see ragged rows.
        ``new_lines``: if True, an explicit Segment("\\n") is appended
                       to each row. The compositor wants False (it
                       paints by row coordinate), the few callers that
                       export to text want True.
        """
        # No _lock — the threading shim's RLock is a no-op and we
        # never run on more than one thread (synthesis D6).
        render_options = options or self.options
        _rendered = self.render(renderable, render_options)
        if style:
            _rendered = Segment.apply_style(_rendered, style)

        render_height = render_options.height
        if render_height is not None:
            render_height = max(0, render_height)

        cropped = Segment.split_and_crop_lines(
            _rendered,
            render_options.max_width,
            include_new_lines=new_lines,
            pad=pad,
            style=style,
        )
        # Plain bounded loop, not itertools.islice: micropython-lib's
        # islice rejects None bounds and raises RuntimeError (PEP 479)
        # when the source is shorter than the bound.
        if render_height is None:
            lines = list(cropped)
        else:
            lines = []
            for line in cropped:
                lines.append(line)
                if len(lines) >= render_height:
                    break
        if render_options.height is not None:
            extra_lines = render_options.height - len(lines)
            if extra_lines > 0:
                # Pad the rendered output up to the requested height
                # with blank-line segments. The compositor needs the
                # row count to match the layout's slot height exactly.
                pad_line = [
                    (
                        [
                            Segment(" " * render_options.max_width, style),
                            Segment("\n"),
                        ]
                        if new_lines
                        else [Segment(" " * render_options.max_width, style)]
                    )
                ]
                lines.extend(pad_line * extra_lines)

        return lines

    def render_str(
        self,
        text,
        *,
        style="",
        justify=None,
        overflow=None,
        emoji=None,
        markup=None,
        highlight=None,
        highlighter=None,
    ):
        """Convert a string to a ``Text`` instance with markup / highlight applied.

        The markup parser is the Tier 3 ``markup.render`` (square-
        bracket DSL); the highlighter is the per-Console default
        (ReprHighlighter unless overridden).  ``emoji`` is accepted
        for API parity but ignored — v0.1 has no emoji-name table.
        """
        # Emoji is ignored — see module docstring REMOVED.  Reduce
        # the kwarg dance to just markup / highlight.
        del emoji

        markup_enabled = markup or (markup is None and self._markup)
        highlight_enabled = highlight or (highlight is None and self._highlight)

        if markup_enabled:
            rich_text = render_markup(text, style=style)
            rich_text.justify = justify
            rich_text.overflow = overflow
        else:
            rich_text = Text(
                text,
                justify=justify,
                overflow=overflow,
                style=style,
            )

        if highlight_enabled:
            _highlighter = highlighter or self.highlighter
        else:
            _highlighter = None
        if _highlighter is not None:
            highlight_text = _highlighter(str(rich_text))
            highlight_text.copy_styles(rich_text)
            return highlight_text

        return rich_text

    def get_style(self, name, *, default=None):
        """Resolve a style name to a ``Style`` instance.

        Upstream consults a Theme stack first; we go straight to
        ``Style.parse`` because the v0.1 widget set has no theme
        switching (see REMOVED).  Returns ``default`` (also parsed)
        if the name doesn't parse.
        """
        if isinstance(name, Style):
            return name

        try:
            return Style.parse(name)
        except errors.StyleSyntaxError as error:
            if default is not None:
                return self.get_style(default)
            raise errors.MissingStyle(
                "Failed to get style {!r}; {}".format(name, error)
            )

    # --- No-op stubs ----------------------------------------------------------
    # Methods that downstream Textual code calls in code paths we don't
    # exercise but that need to exist so the attribute lookup succeeds.
    # All silently no-op.

    def push_render_hook(self, hook):
        # Hooks would mutate the renderable list pre-render. Unused.
        self._render_hooks.append(hook)

    def pop_render_hook(self):
        if self._render_hooks:
            self._render_hooks.pop()

    def set_live(self, live):
        # Live is dropped (see REMOVED) but Textual's import path may
        # touch this attribute. Track the stack so len() reports right.
        self._live_stack.append(live)
        return len(self._live_stack) == 1

    def clear_live(self):
        if self._live_stack:
            self._live_stack.pop()

    def __enter__(self):
        # Upstream enters a buffer context here; we have no buffer.
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None


# --- Module exports -----------------------------------------------------------
# Keep the public names upstream Rich exports so ``from rich.console
# import X`` (rewritten to picolet_tui._rich.console) keeps resolving.

__all__ = (
    "Console",
    "ConsoleDimensions",
    "ConsoleOptions",
    "Group",
    "NewLine",
    "NullFile",
    "NULL_FILE",
    "RenderableType",
    "RenderResult",
    "HighlighterType",
    "JustifyMethod",
    "OverflowMethod",
    "group",
)

# Upstream Rich exposes _NullFile under the alias NullFile in newer
# versions; mirror it.
NullFile = _NullFile
