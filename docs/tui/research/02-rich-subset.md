# Rich Library Subset for MicroPython / Textual Port

## Module Map

Rich (`rich/`) ships ~77 Python files totalling ~26,500 LoC plus a `_unicode_data/`
package of ~11,900 LoC of generated width tables. The package is monolithic — many
modules cross-import — but the layering is shallow once the leaves are identified.

Sorted by size (top of the file is the heaviest):

| File                | LoC   | Role                                                    |
|---------------------|-------|---------------------------------------------------------|
| `_emoji_codes.py`   | 3610  | Emoji name -> codepoint map (data only)                 |
| `console.py`        | 2698  | `Console`, `ConsoleOptions`, rendering pipeline         |
| `progress.py`       | 1716  | Progress bars                                           |
| `text.py`           | 1363  | `Text`, `Span`, styled text + wrapping                  |
| `pretty.py`         | 1016  | `Pretty` repr-style rendering                           |
| `table.py`          | 1015  | `Table`                                                 |
| `syntax.py`         | 988   | Pygments-backed code highlighting                       |
| `traceback.py`      | 924   | Enhanced exception rendering                            |
| `markdown.py`       | 802   | markdown-it -> Rich render                              |
| `style.py`          | 796   | `Style` (color, attrs, link, meta)                      |
| `segment.py`        | 780   | `Segment` named-tuple (text, style, control)            |
| `_win32_console.py` | 661   | Win32 legacy console support                            |
| `color.py`          | 621   | `Color`, `ColorType`, parsing, downgrade, 8-bit palette |
| `_spinners.py`      | 482   | Spinner data                                            |
| `box.py`            | 474   | Box-drawing character sets                              |
| `layout.py`         | 442   | Split layout regions                                    |
| `live.py`           | 404   | Live in-place updates                                   |
| `cells.py`          | 352   | Cell width measurement (wcwidth)                        |
| `align.py`          | 320   | `Align` renderable                                      |
| `panel.py`          | 317   | `Panel` renderable                                      |
| `_palettes.py`      | 309   | 16-/256-/Windows-palette data                           |
| `markup.py`         | 251   | `[bold red]...[/]` parser                               |
| `tree.py`           | 257   | `Tree` renderable                                       |
| `highlighter.py`    | 232   | `Highlighter`, `ReprHighlighter`                        |
| `control.py`        | 219   | ANSI control sequence helpers                           |
| `measure.py`        | 151   | `Measurement` (min/max width)                           |
| `terminal_theme.py` | 153   | `TerminalTheme` (default + SVG export palettes)         |
| `repr.py`           | 150   | `@rich_repr` decorator (uses `inspect.signature`)       |
| `protocol.py`       | 41    | `is_renderable`, `rich_cast`                            |
| `errors.py`         | 34    | Exception classes                                       |
| `color_triplet.py`  | 38    | `ColorTriplet(NamedTuple)`                              |
| `_wrap.py`          | 93    | `divide_line` word-wrap helper                          |

`_unicode_data/` contains 22 generated tables (one per Unicode version, ~600 lines
each) plus a 93-line loader. Only one version is needed at runtime; the loader
lazily `importlib.import_module()`s the matching file.

## Textual's Actual Rich Usage

Grepping Textual `main` for `from rich` / `import rich` yields 63 unique import
lines across ~30 distinct rich submodules. By frequency:

| Rich module        | imports | Notes                                                          |
|--------------------|--------:|----------------------------------------------------------------|
| `rich.repr`        |      52 | `import rich.repr` — used purely as `@rich.repr.auto` decorator |
| `rich.style`       |      39 | `Style`, `NULL_STYLE`, `StyleType`                              |
| `rich.console`     |      37 | `Console`, `ConsoleOptions`, `RenderableType`, `RenderResult`, `Group`, `group`, `ConsoleRenderable` |
| `rich.text`        |      28 | `Text`, `TextType`                                              |
| `rich.segment`     |      28 | `Segment`, `Segments`                                           |
| `rich.terminal_theme` |   9 | `TerminalTheme`                                                |
| `rich.cells`       |       9 | `cell_len`, `get_character_cell_size`, `set_cell_size`         |
| `rich.measure`     |       8 | `Measurement`, `measure_renderables`                            |
| `rich.color`       |       8 | `Color`, `ColorSystem`, `ColorType`                             |
| `rich.highlighter` |       6 | `Highlighter`, `ReprHighlighter`                                |
| `rich.protocol`    |       5 | `is_renderable`, `rich_cast`                                    |
| `rich.table`       |       4 | `Table` (used inside Textual's `DataTable` only as opt-in)      |
| `rich.repr` (sym)  |       4 | `Result`, `rich_repr`                                           |
| `rich.padding`     |       4 | `Padding`                                                       |
| `rich.syntax`      |       3 | `Syntax` (only by `TextArea` syntax mode)                       |
| `rich.panel`       |       3 | `Panel`                                                         |
| `rich.align`       |       3 | `Align`, `AlignMethod`                                          |
| `rich.pretty`      |       2 | `Pretty`                                                        |
| `rich.markup`      |       2 | `render`                                                        |
| `rich.control`     |       2 | `Control`                                                       |
| `rich._wrap`       |       1 | `divide_line` (private API — Textual reaches into it)           |
| `rich.tree`        |       1 | `Tree`                                                          |
| `rich.traceback`   |       1 | `Traceback`                                                     |
| `rich.markdown`    |       1 | `Markdown` (only Markdown widget)                               |
| `rich.color_triplet` |     1 | `ColorTriplet`                                                  |

Critically: Textual does **not** drive its terminal through Rich's `Console.print()`.
It instantiates a `Console` configured with `file=_NullFile(), force_terminal=True,
markup=True, highlight=False, emoji=False` and uses it as a **render context** to
call `console.render_lines(renderable, options)` -> `list[list[Segment]]`. Textual's
own `_compositor.py` assembles these strips, then emits ANSI directly. So `Console`
is required mostly for `render_lines`, `options.update_*`, markup parsing, and
serving as the `console` argument passed into `__rich_console__` hooks.

The `Markdown`, `Syntax`, `Traceback`, `Pretty`, and `Tree` imports are each
guarded behind a single widget (MarkdownViewer, TextArea-with-syntax, error
screen, dev console, Tree widget when rendering Rich content). None are on the
core render path.

## Minimum Subset

A working "Rich-for-MicroPython" that can host Textual's compositor needs the
following ~7,800 lines, all of which are largely portable:

### Tier 1 — must be portable verbatim (or with trivial shims)

| Module             | LoC | MP portability concerns                                                  |
|--------------------|----:|--------------------------------------------------------------------------|
| `errors.py`        | 34  | Pure `class X(Exception)` — runs as-is                                   |
| `color_triplet.py` | 38  | `NamedTuple` only — runs as-is                                          |
| `protocol.py`      | 41  | Uses `typing.Protocol` only as a type hint; otherwise just `hasattr` checks |
| `_loop.py`         | 43  | Pure-Python iterator helpers                                            |
| `_pick.py`         | 17  | Two-arg "first non-None" picker                                        |
| `_wrap.py`         | 93  | One `re.compile(r"\s*\S+\s*")` — works in MP `re`                       |
| `palette.py`       | ~80 | `math.sqrt`, no other deps                                              |
| `_palettes.py`     | 309 | Static tuples of `(r,g,b)` — data only                                  |
| `terminal_theme.py`| 153 | Static `TerminalTheme` instances — data only                            |
| `repr.py`          | 150 | Needs `inspect.signature` shim (see Shims)                              |
| `measure.py`       | 151 | Pure logic + `Sequence`/`NamedTuple`                                    |
| `cells.py`         | 352 | Needs `_unicode_data` shim — see below                                  |
| `color.py`         | 621 | One `re.compile` (RE_COLOR), `colorsys.rgb_to_hls`, `@lru_cache`        |
| `segment.py`       | 780 | `NamedTuple` + many `@lru_cache`-decorated static helpers              |
| `style.py`         | 796 | **Uses `pickle.dumps/loads` for `meta`** — replace with `json` or skip  |
| `markup.py`        | 251 | `re.compile` + `ast.literal_eval` — `ast` is not in MP, must replace    |
| `protocol.py`      | 41  | Trivial                                                                  |
| `_unicode_data/__init__.py` | 93 | Uses `importlib.import_module` — fine in MP; bisects version table  |
| `_unicode_data/unicode15-1-0.py` (one version) | ~670 | Pure data; ship one table |

That's roughly **5,000 LoC of code + 670 LoC of one Unicode width table**.

### Tier 2 — needed for Textual's `Console` instantiation

| Module        | LoC   | Notes                                                                  |
|---------------|------:|------------------------------------------------------------------------|
| `console.py`  | 2698  | The big one. ~1,500 LoC of features Textual doesn't exercise (print, log, capture, export-svg, jupyter, pager, status). The trimmed surface is `Console.__init__`, `render`, `render_lines`, `render_str`, `_render_buffer`, `ConsoleOptions`, `Group`, `group`, `ConsoleRenderable`, `RichCast`, `_detect_color_system`. Achievable in ~600 LoC. |
| `text.py`     | 1363  | `Text`, `Span` — used heavily by Textual (`Text` is the universal styled-string type). Wraps `_wrap.divide_line`, calls into `cells`, `style`, `segment`. Mostly portable; uses `re`, `math.gcd`, `functools.reduce`. |
| `control.py`  | 219   | ANSI control sequence builder (`Control.move`, `.show_cursor` etc). Pure string fmt. |
| `align.py`    | 320   | Used by Text's `justify` paths; pure logic.                            |
| `padding.py`  | 141   | Pure logic.                                                            |
| `highlighter.py` | 232 | `NullHighlighter` + `ReprHighlighter`; the latter has heavy regex (~10 patterns) — keep `NullHighlighter`, stub `ReprHighlighter`. |
| `containers.py` | 167 | `Lines`, `Renderables` — list subclasses; portable.                    |

### Tier 3 — widget-level, can be lazy-loaded or replaced with stubs

| Module     | LoC  | Notes                                                                 |
|------------|-----:|-----------------------------------------------------------------------|
| `panel.py` | 317  | Used by Textual Panel widget. Self-contained.                         |
| `table.py` | 1015 | Only by DataTable when given Rich renderables. Can stub as `NotImplementedError`. |
| `tree.py`  | 257  | Single Textual widget. Skip until needed.                             |
| `box.py`   | 474  | Box-drawing character data. Used by Panel/Table.                      |

### Tier 4 — drop entirely on MicroPython

* `markdown.py` (802) — needs the `markdown-it-py` PyPI package (~3k LoC of its own, plus `mdurl`, `linkify-it-py`). Replace with a hand-rolled subset only if MarkdownViewer is required.
* `syntax.py` (988) — needs `pygments`. Skip — TextArea works without syntax highlighting.
* `traceback.py` (924) — uses `inspect`, `pygments`, frame walking. MicroPython's traceback object is much thinner; replace with `sys.print_exception`.
* `pretty.py` (1016) — uses `dataclasses`, `inspect`, `collections.abc`. Stub `Pretty(obj) -> Text(repr(obj))`.
* `_inspect.py` (272), `_win32_console.py` (661), `_windows.py`, `_windows_renderer.py`, `jupyter.py`, `pager.py`, `live.py` (404), `live_render.py`, `status.py`, `spinner.py`, `_spinners.py` (482), `prompt.py` (400), `logging.py` (305), `progress.py` (1716), `progress_bar.py` (223), `file_proxy.py`, `screen.py`, `scope.py`, `themes.py`, `theme.py`, `layout.py` (442), `columns.py` (187), `bar.py`, `json.py`, `rule.py`, `emoji.py`, `_emoji_codes.py` (3610), `_emoji_replace.py`, `_export_format.py`, `_log_render.py`, `diagnose.py`.

That eliminates ~14,000 LoC of Rich outright.

## Needed Shims

1. **`functools.lru_cache`** — MicroPython has no `functools.lru_cache`. Used ~15
   times in the Tier-1/2 modules (`color.parse`, `color.downgrade`,
   `color.get_ansi_codes`, `style.parse`, `style.__add__`, `cells.cached_cell_len`,
   `cells.get_character_cell_size`, `segment.split_cells`, `Palette.match`).
   Shim shape:
   ```python
   def lru_cache(maxsize=128, typed=False):
       def deco(fn):
           cache = {}
           def wrapper(*args):
               key = args
               if key not in cache:
                   if len(cache) >= maxsize:
                       cache.pop(next(iter(cache)))
                   cache[key] = fn(*args)
               return cache[key]
           return wrapper
       return deco
   ```
   Or, on memory-constrained targets, drop caching entirely (`def lru_cache(*a, **kw): return lambda fn: fn`).

2. **`pickle.dumps/loads` in `style.py`** — used only to serialise `Style.meta`
   (a user-supplied `dict[str, Any]`) so two `Style` instances merge their meta
   dicts without aliasing. Textual rarely sets meta on rendered styles. Replace
   with `json.dumps`/`json.loads` for the dict-only case, or drop meta entirely
   and store `self._meta = meta` directly (losing immutability of meta but
   keeping the API).

3. **`colorsys.rgb_to_hls`** — only used by `Color.downgrade()` to map a truecolor
   to the nearest 256-color when the terminal can't do truecolor. ~12 lines of
   pure arithmetic; inline a port. Already a known-portable transform.

4. **`ast.literal_eval` in `markup.py`** — used to parse the `=value` half of
   `[style=value]` markup. Restrict to `int`/`float`/quoted-str parsing
   (~15 lines) — Rich's actual style parameters are always strings or numbers.

5. **`inspect.signature` in `repr.py`** — only used by `@rich_repr` when the
   decorated class does NOT define `__rich_repr__` itself. Both Rich's own
   classes and Textual's classes always do (`def __rich_repr__(self): yield ...`).
   Make the `auto_rich_repr` branch raise `NotImplementedError` and require an
   explicit `__rich_repr__`. Zero practical regression.

6. **`importlib.import_module` in `_unicode_data/__init__.py`** — MicroPython
   supports `__import__()` and works with module attributes after import. Direct
   port works; or hard-code a single Unicode version table and skip the version
   selector.

7. **`typing.Protocol`, `Literal`, `runtime_checkable`** — used by
   `console.ConsoleRenderable`, `RichCast`. MicroPython's `typing` module
   (in `micropython-lib`) does not have these. Strip them — they're only type
   hints; `is_renderable()` already does `hasattr(x, '__rich_console__')`.

8. **`dataclass`** — `console.ConsoleOptions` uses `@dataclass`. ~20 fields with
   defaults. Hand-port to `class ConsoleOptions: __slots__ = (...)` with manual
   `__init__` plus an `update_*` method that returns a copy. Adds ~40 LoC, saves
   pulling `dataclasses` (not in MP).

9. **`threading.local`** — `Console` uses `_thread_locals` for re-entrant rendering.
   MicroPython single-thread case: replace `ConsoleThreadLocals(threading.local)`
   with a plain object.

## Color Path

Rich's color-system detection lives in `Console._detect_color_system()`
(`console.py` line 789):

```
if is_jupyter:              return TRUECOLOR
if not is_terminal or dumb: return None
if WINDOWS:
    if legacy_windows:       return WINDOWS
    feat = get_windows_console_features()
    return TRUECOLOR if feat.truecolor else EIGHT_BIT
else:
    color_term = environ.get("COLORTERM","").strip().lower()
    if color_term in ("truecolor","24bit"):
        return TRUECOLOR
    term = environ.get("TERM","").strip().lower()
    _name, _hyphen, colors = term.rpartition("-")  # "xterm-256color" -> "256color"
    return _TERM_COLORS.get(colors, STANDARD)

_TERM_COLORS = {
    "kitty":    EIGHT_BIT,
    "256color": EIGHT_BIT,
    "16color":  STANDARD,
}
```

`Color.downgrade(system)` (`color.py` 513) handles the actual conversion:
TRUECOLOR -> EIGHT_BIT uses HLS-based grayscale detection + 6x6x6 RGB cube
mapping; TRUECOLOR/EIGHT_BIT -> STANDARD uses `Palette.match()` with a
perceptual distance function (the `(2+R/256, 4, 2+(255-R)/256)`-weighted
Euclidean distance from the AERT spec, `palette.py` 50).

For MicroPython this collapses cleanly:

* Skip Jupyter/Windows branches entirely.
* Read `COLORTERM` / `TERM` via `os.environ` (works in MP unix port) or accept
  an explicit `color_system=` argument on Console construction. For embedded
  targets without env vars, force `EIGHT_BIT` and let the user override.
* The HLS-grayscale + 6-cube mapping is ~20 lines of integer arithmetic; the
  perceptual-distance palette match is ~10 lines plus a 16-entry data table.

So the entire detect-and-downgrade path is ~80 LoC, all portable. The expensive
piece is the `EIGHT_BIT_PALETTE` data table (256 RGB triples, ~270 LoC) needed
when downgrading from truecolor — but this is static data, not code.

## Excluded Features and Consequences

| Excluded                                | Lost capability                                                    | Mitigation                                                          |
|-----------------------------------------|--------------------------------------------------------------------|----------------------------------------------------------------------|
| `rich.markdown`                         | MarkdownViewer widget renders nothing                              | Stub Markdown widget to `Static(text)` until needed                  |
| `rich.syntax`                           | TextArea has no syntax highlighting                                | TextArea still works as plain editor                                 |
| `rich.traceback`                        | Pretty exception screens become plain `sys.print_exception` output | Acceptable on embedded; Textual will still show *some* trace          |
| `rich.pretty`                           | `Pretty(obj)` renders as `repr(obj)`                               | Adequate for debug output                                            |
| `rich.progress`, `progress_bar`         | Rich's progress bars unavailable                                   | Textual's own `ProgressBar` widget is independent                    |
| `rich.live`, `rich.status`, `rich.spinner` | Rich Live() context manager and spinners                        | Textual drives its own redraw loop; not used by widget code           |
| `rich.logging`                          | `RichHandler` logging adapter                                      | Use stdlib `logging`                                                  |
| `rich.layout`                           | Rich's column/row layout                                           | Textual has its own CSS layout engine                                 |
| `rich.tree`, `rich.table`, `rich.panel`, `rich.align` (Tier 3) | Specific widgets render as plain text  | Provide stubs that emit `Segment(text)`; iterate as time permits      |
| `rich.emoji` + `_emoji_codes` (~3700 LoC) | `:smile:` emoji shortcodes unsupported                          | Pass through as literal text — Textual disables emoji by default anyway |
| `rich._win32_console`, `rich._windows*` | Win32 legacy console                                               | N/A for MP unix port; embedded never needs it                        |
| `rich.jupyter`                          | Jupyter cell rendering                                             | N/A                                                                   |
| `rich.prompt`                           | `Prompt.ask()` interactive input                                   | Use Textual's own `Input` widget                                     |

**Summary of the cut**: from ~38,500 LoC (rich + unicode data), keep ~7,500 LoC
of code + ~700 LoC of one width table + ~600 LoC of palette data. That's an
~80% reduction. The retained subset gives Textual `Console.render_lines`,
`Segment`, `Style` (with full ANSI color + downgrade), `Text` (with wrapping),
`Measurement`, `markup.render`, `cell_len`, plus the `@rich.repr.auto` decorator
that's sprinkled across Textual.

## Open Questions

* **`Style.meta` strategy**: keep meta but switch `pickle` to `json` (limits
  meta to JSON-serialisable values — likely fine for Textual, which mostly
  stores hover/click handler IDs), or drop meta entirely? Need to audit
  Textual's `meta` usage.
* **Unicode version**: ship which single width table? `15-1-0.py` is ~670 LoC
  and covers modern emoji ranges. `9-0-0.py` is ~600 LoC and matches CPython
  3.9's `unicodedata`. Lighter targets might drop the table entirely and use
  `_SINGLE_CELL_UNICODE_RANGES` only (assume 1 cell for anything outside ASCII
  CJK ranges — wrong for emoji but tiny).
* **`lru_cache` sizing on MP**: Rich uses 1024-entry caches in hot paths. On
  embedded targets this may be too memory-heavy; might need to drop maxsize
  to 64-128 or disable entirely and accept the perf hit.
* **`Console` slimming**: 2,700 -> 600 LoC is the single largest piece of the
  port. Worth prototyping in isolation before committing — `render_lines` pulls
  in markup, highlighter, group rendering, options propagation. If it doesn't
  collapse cleanly, the alternative is to fork `Console` into a Textual-specific
  `RenderHost` and reduce Rich to "just the data types".
