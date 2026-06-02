# 00 - Phase 0 Synthesis: Textual-on-MicroPython for picolet

Status: precursor spec, fan-out of Phase 0 research
Date: 2026-06-03
Inputs: `01-textual-deps.md`, `02-rich-subset.md`, `03-mp-stdlib-coverage.md`, `04-terminal-handling.md`

## 1. Top-level recommendation

Do **not** port Textual. Build **picolet-tui**, a Textual-inspired framework
that reuses the conceptual layers (DOM, reactive properties, message
bubbling, declarative bindings, ANSI-rendering compositor) and lifts the
small pure-Python leaves verbatim (geometry, color, easing, key tables,
scalar parser, Rich `Segment`/`Style`/`Color`/`cells`).

Reasoning, in order of severity:

1. **Class-construction machinery is not portable.** MicroPython does
   not invoke `__init_subclass__`, does not honor custom metaclasses,
   and does not call `__set_name__` on descriptors. Textual's `DOMNode`,
   `_MessagePumpMeta`, `Reactive`, `Message`, and the ~24 CSS property
   descriptors all rely on these (see 01 §"CPython-only constructs"
   items 1-3). Replacing this with an explicit `@widget` class
   decorator that scans `vars(cls)` once at decoration time is a
   shallow but **pervasive** rewrite - every base class signature
   changes.
2. **Two stdlib gaps are load-bearing.** `re` (no flags, no named
   groups - 03 §"Per-module table") and `inspect.signature` (03 same
   §) sit under both Rich's markup parser and Textual's reactive
   dispatch. Either the regex engine swap in the C runtime or hand-
   rolled tokenizers are mandatory; `inspect.signature` is replaceable
   with `function.__code__.co_argcount` (01 needs_shim) but every
   reactive watcher/computer dispatch site has to call the replacement.
3. **Rich is large but the load-bearing subset is small.** Textual
   uses Rich purely as a render context (`Console(file=_NullFile())`
   driving `render_lines`), then composites the `Segment` strips
   itself (02 §"Textual's Actual Rich Usage"). The minimum viable
   Rich subset is ~7,500 LoC of code + ~700 LoC of one Unicode width
   table - an 80% cut. This is portable.
4. **The driver is rewriteable.** The terminal handling investigation
   (04) lands on a single `tuiterm` C module (~250 LoC Unix + ~300 LoC
   Windows) plus ~500 LoC of frozen Python for the byte-stream state
   machine, shared across platforms. Both platforms emit xterm VT
   bytes once their input modes are configured, so a single parser +
   key table covers Linux + Windows.

Scope estimate: **~12-15k LoC of frozen Python + ~550 LoC of C**, of
which ~5,500 is mechanical port (Rich leaves, Textual leaves, key
tables, color/cells), ~3,500 is the Textual-inspired core rewrite
(DOM, MessagePump, Reactive, compositor against picolet Cell/Style),
~2,000 is widgets, ~1,500 is the CSS-or-style-DSL surface (see
Decision D2), ~1,000 is the asyncio event loop integration and driver
Python layer, ~500 is the parser, ~550 C is `tuiterm`. Sizeable, but
self-contained: no external Python deps beyond what core MP +
micropython-lib ship.

## 2. Phased plan

The 7-phase outline holds, with two refinements: **Phase 2 needs a
shim-pack sub-phase** because nine stdlib shims (typing, dataclasses,
enum, weakref containers, functools.lru_cache + wraps + total_ordering,
selectors, threading.RLock, contextlib.AsyncExitStack) are prerequisite
to *any* of Phases 3-5 compiling, and **Phase 7 (AppHarness)** is a
test-driver concern that should land in parallel with Phase 4, not
after Phase 6, because Phases 4 and 5 are untestable headless without
it.

### Phase 1 - Spec (`tui-v0.1-spec.md`)

Output: a single document that pins the v0.1 surface area before any
code is written. Must answer the **open decisions** below (D1-D7) and
commit to:

- Target MicroPython version + build flags (`MICROPY_PY_WEAKREF`,
  `MICROPY_PY_COLLECTIONS_DEQUE`, `_ORDEREDDICT`, `_SELECT`,
  `MICROPY_PY_RE_*`).
- Regex engine decision: stay on re1.5 with hand-rolled tokenizers,
  or swap to pcre2/oniguruma. (03 open question 4.)
- Textual version pinned for source-of-truth lift (pre-0.50 to avoid
  `asyncio.TaskGroup`, or post-0.50 with `gather`-based back-port).
- Concrete v0.1 widget list (see Decision D3).
- Whether v0.1 ships TCSS parsing or a Python-side `Style(...)` DSL
  (Decision D2).
- Driver targets: Linux unix port + Windows MinGW (per 04). No macOS,
  no embedded UART in v0.1.
- Threading model: assume `_thread` is **off** for v0.1. No worker
  threads, no `threading.get_ident()`-keyed locks. Single asyncio
  loop, single thread.

Deliverable: `docs/tui/tui-v0.1-spec.md`. Closes all open questions
in 01-04 with explicit yes/no.

### Phase 2 - Foundation

Two sub-phases in series:

**2a. Variant + manifest skeleton.** New `mpconfigvariant` =
`picolet-tui` parallel to `picolet-cli` per 04 §4. Empty `tuiterm` C
shim that compiles but is not yet wired. Frozen-module manifest with
empty `picolet_tui` Python package. Hello-world picolet-tui binary
that prints "tui" and exits.

**2b. Shim pack.** Pure-Python shims for the gaps identified in 03:

- `dataclasses` (~200 LoC, no `frozen`, no `slots`, with
  `field(default_factory=...)`)
- `typing` (~150 LoC, makes every name callable + subscriptable,
  `Protocol` as plain class)
- `enum` (Enum + IntEnum + Flag, ~250 LoC total)
- `functools.lru_cache` (~40 LoC), `functools.wraps` (real, not
  no-op), `functools.total_ordering` (~30), `functools.cached_property`
- `weakref.WeakSet` / `WeakValueDictionary` / `WeakKeyDictionary`
  (~150 LoC over `weakref.ref`)
- `threading.RLock` / `Lock` / `Event` (~80 LoC over `_thread`)
- `selectors.SelectSelector` (~80 LoC over `select.poll`)
- `contextlib.AsyncExitStack` + `asynccontextmanager` + `nullcontext`
- `inspect.signature` replacement: `_callback.count_parameters()` that
  uses `fn.__code__.co_argcount` and special-cases `partial` + bound
  methods (01 needs_shim).

Each shim gets unit tests against the picolet-tui binary. Total
~1,200 LoC.

### Phase 3 - Rich subset

Port the ~7,500 LoC subset from 02 §"Minimum Subset", **Tier 1 + Tier 2
only**. Tier 3 (`panel`, `table`, `tree`, `box`) stubs that emit
`Segment(text)`. Tier 4 (`markdown`, `syntax`, `traceback`, `pretty`,
`live`, `progress`, `layout`, `_win32_console`, all emoji) deleted.

Order, smallest first to validate the shim pack early:
`errors` -> `color_triplet` -> `protocol` -> `_wrap` -> `_palettes`
-> `terminal_theme` -> `palette` -> `measure` -> `cells` (with
single Unicode 15.1 width table) -> `color` -> `segment` ->
`markup` (with the 15-line scalar parser replacing `ast.literal_eval`)
-> `style` (with `json` in place of `pickle.dumps` for meta, or meta
deleted - Decision D4) -> `containers` -> `padding` -> `align` ->
`highlighter` (NullHighlighter only) -> `repr` (require explicit
`__rich_repr__`, no `inspect.signature` fallback) -> `text` ->
`control` -> `console` (trimmed from 2,698 LoC to ~600).

Stop at `console`. Re-evaluate before going further: if `console`
collapses cleanly, the rest of Rich is dead code. If not, fork
`Console` into a Textual-specific `RenderHost` and reduce Rich to
data types (02 open question 4).

### Phase 4 - Textual core

Three pieces in series:

**4a. Leaves.** Lift verbatim with minor edits to remove `Generic[T]`
sub-scripting and `@overload`s: `geometry`, `color`, `keys`,
`_easing`, `css/scalar`, `case`. ~800 LoC.

**4b. Core classes.** Reimplement, not port: `MessagePump`, `DOMNode`,
`Widget`, `Reactive`, `Message`, `Screen`, `Binding`, `App`. The
shared design rule: every class that would have used
`__init_subclass__`, `metaclass=...`, or `__set_name__` on a
descriptor is constructed via an explicit `@widget` decorator that
scans `vars(cls)` once and populates `cls._reactives`,
`cls._decorated_handlers`, `cls._merged_bindings`,
`cls._computes`. The decorator is the single place all class-time
introspection lives. Estimated ~3,000 LoC.

`contextvars.ContextVar` (used in `_context.py` and `Message.__init__`)
replaced with a module-level list-as-stack - safe under the
single-thread assumption locked in Phase 1.

`weakref.ref` parent pointers replaced with explicit `_dispose()`
hooks on `MessagePump`. `concurrent.futures.Future` and
`asyncio.Future` replaced with a simple awaitable `Event + slot`
pattern for `Screen.dismiss(result)`.

**4c. Compositor.** Rewrite `_compositor.py` against the Rich subset
from Phase 3. The shape stays the same (render layers -> strips ->
diff against last frame -> emit ANSI), but `rich.segment.Segment`,
`rich.style.Style`, `rich.console.Console.render_lines` come from
the trimmed Rich subset, not upstream. ~600 LoC.

### Phase 5 - Widgets (v0.1 set)

Minimum viable widget set, in order of dependency:

1. `Static` - the renderable-host base. Everything else extends it.
2. `Label` - one-line `Static` with style helpers.
3. `Container` / `Vertical` / `Horizontal` - layout primitives.
4. `Button` - first interactive widget; validates the event-bubbling
   path end-to-end.
5. `Input` - first focus-management widget; validates the
   keystroke-routing path and bracketed-paste handling.
6. `Stack` (the picolet integration target - mirrors what tui-pydfu
   needs in Phase 6).

Each widget is ~150-400 LoC; total ~1,800 LoC. **No** `DataTable`,
`Tree`, `MarkdownViewer`, `TextArea`, `RichLog`, `Tabs`, `OptionList`,
`Switch`, `RadioSet`, `Sparkline`, or any animation-driven widget in
v0.1.

### Phase 6 - tui-pydfu example + hello-tui template

Two deliverables:

- `examples/tui-pydfu/` - rebuild the existing pydfu progress UI as
  a picolet-tui app. Uses Static + Container + Button + a custom
  ProgressBar widget (the seventh widget, justified by the example).
  This is the integration test: real picolet binary, real USB DFU
  driver, real terminal.
- `templates/hello-tui/` - the picolet `mpm new hello-tui ...`
  template scaffold. One screen, one Label, one Input, one Button,
  one click handler. Documented in `docs/tui/getting-started.md`.

### Phase 7 - AppHarness TUI driver (lands in parallel with 4b)

Textual's `App.run_test()` uses
`contextlib.asynccontextmanager` + a headless driver
(`shutil.get_terminal_size` only). Lift this pattern: a
`HeadlessDriver` that fakes `tuiterm.read_input`/`poll_resize`/`write`,
feeding pre-scripted bytes and capturing emitted ANSI. The whole
asynccontextmanager rewrite is small (~150 LoC after the
`AsyncExitStack` shim from Phase 2b lands).

**Why parallel with 4b**: without AppHarness, Phases 4-5 have no
test loop other than "build the binary and eyeball it". With it,
each widget can have a deterministic test that scripts keystrokes
and asserts on the captured strip diff. Worth the 1-week investment
up-front.

## 3. Risk register

| # | Risk | Likelihood | Blast radius | Mitigation |
|---|---|---|---|---|
| R1 | `re1.5` lacks named groups + flags. Forces hand-rolled tokenizer for both Rich markup and Textual CSS. | High (default state) | Medium-High - 500-1000 LoC of hand-rolled scanners, slower than regex, easy to get wrong on edge cases. | Decide in Phase 1 whether to swap the C regex engine. If kept on re1.5, accept the cost up-front and write the tokenizers as the first thing in Phase 3 (markup) and Phase 4a (CSS, if D2 = ship CSS). Test parity against CPython `re` with a fixed corpus of markup + CSS strings. |
| R2 | `Console` trim doesn't collapse cleanly from 2,700 LoC to ~600. Pulls in markup, highlighter, group rendering, options propagation, capture, export-svg paths that interlock. | Medium | High - if `Console` won't shrink, the Rich port balloons by another 1,500-2,000 LoC or forces a `RenderHost` fork. | Prototype the `Console` trim as a spike before Phase 3 commits, on a CPython host with `pip install rich` and an aggressive `del`-method script. If the surface won't compress under 800 LoC, fork to a `RenderHost` that exposes only `__init__`, `render_lines`, `render_str`, `ConsoleOptions` and reduce Rich to data types (02 open question 4). |
| R3 | `@widget` class decorator drift - widget authors forget to apply it, classes silently lose their reactive/binding/handler wiring at runtime with no `__init_subclass__` to catch it. | High once external widget authors exist; Medium internally | Medium - silent bugs in user code that look like "my watcher isn't firing". | Two-layer defence: (a) `Widget.__init__` asserts `cls._widget_registered is True` and raises a clear `MissingWidgetDecoratorError` on first instantiation. (b) lint rule in picolet's pre-commit / `mpm check` that flags any subclass of `Widget` without `@widget`. Document in `docs/tui/authoring-widgets.md` in Phase 6. |
| R4 | Memory pressure from `lru_cache` instances. Rich uses `maxsize=1024` in hot paths (Color.parse, Style.parse, get_character_cell_size, segment.split_cells). On unix port this is fine; if we ever push to a constrained target, it dominates. | Low for desktop unix port; High if embedded follows | Medium - silently bad behaviour, OOM on long-running TUI sessions. | (a) Phase 2b shim defaults `maxsize=128` not 1024. (b) Provide a `picolet_tui.tune.disable_caches()` API for embedded use. (c) Add an `mpm tui stats` command (Phase 7) that dumps cache hit/miss + entry counts. |
| R5 | Windows VT input quirks - QUICK_EDIT_MODE not actually disabled, ENABLE_EXTENDED_FLAGS forgotten, console host pre-1809 surfaces in CI on a stale runner, mintty/Cygwin stdin masquerades as console. | Medium (Windows path always has surprises) | Medium - works on dev workstation, breaks for a chunk of users. | (a) `tuiterm.enable()` returns a capabilities struct so the App can log what actually got set. (b) Hard refuse to start with a clear error if `SetConsoleMode(hOut, ENABLE_VIRTUAL_TERMINAL_PROCESSING)` fails (04 §"What does not work"). (c) CI matrix includes Windows 10 22H2 + Windows 11 + Windows Terminal + conhost separately. (d) Document mintty/Cygwin as "use Windows Terminal" rather than supporting both. |

## 4. Estimated effort

**Size: large.** Not by any single piece - by the sum.

- Rich subset port: **medium**, ~5,000 LoC mechanical + ~600 LoC
  trimmed `Console`. 3-4 weeks for one solo engineer including shim
  validation. The `Console` trim is the spike that decides the
  schedule.
- Textual-inspired core (Phase 4): **medium-large**, ~3,500 LoC
  rewrite. 4-5 weeks. The `@widget` decorator design is the highest-
  judgement piece.
- Widget set (Phase 5): **small-medium**, ~1,800 LoC. 2 weeks once
  the core is stable.
- Stdlib shim pack (Phase 2b): **small**, ~1,200 LoC. 1 week, but
  must land first.
- `tuiterm` C module + parser (Phase 2a + parser portion of 4 + 7):
  **small-medium**, ~550 LoC C + ~500 LoC Python parser. 2 weeks
  including Windows cross-build wiring.
- AppHarness + tests + examples + docs: **medium**, ~1,500 LoC of
  test infrastructure + 2 examples + 3 docs. 2-3 weeks.

**Total: ~12,000-15,000 LoC, ~14-17 weeks of one focused solo
engineer**, i.e. 3.5-4 months. The unknowns that could push it to 5
months are R1 (regex engine decision pushes Phase 3 left by 2 weeks
if "rewrite tokenizers" wins) and R2 (Console trim fails, forcing
RenderHost fork = +2 weeks in Phase 3).

The unknowns that could **shorten** it: shipping Style DSL instead of
TCSS in v0.1 (Decision D2) cuts ~2,500 LoC and ~24 descriptor classes
of complexity, probably saving 2 weeks. Deferring all animation (no
`@dataclass SimpleAnimation`, no `runtime_checkable Protocol`) saves
1 week.

## 5. Concrete decisions (taken; rationale)

**D1. Reimplement, don't port, Textual's class hierarchy.** Rationale:
01's CPython-only inventory items 1-3 (metaclass, `__init_subclass__`,
`__set_name__`) are non-shimable. The `@widget` decorator pattern is
the right shape and gives a clearer mental model anyway. Cost: every
widget author has to remember the decorator (R3).

**D2. v0.1 ships a Python-side `Style(...)` DSL, not TCSS.** Rationale:
the CSS parser is ~2,500 LoC + 24 descriptor classes (01 §"CSS
subsystem"), depends on `re` features that re1.5 doesn't have (R1),
and is not on the critical path for any planned v0.1 widget. The DSL
is a thin wrapper around the same `Styles` instance and can be
populated by a CSS parser in v0.2 without breaking the widget API.

**D3. v0.1 widget set is exactly: Static, Label, Container,
Vertical, Horizontal, Button, Input, Stack, ProgressBar.** Rationale:
covers everything tui-pydfu needs, validates input + focus + layout
+ event bubbling. Tabs/DataTable/TextArea are v0.2 and require either
syntax-highlighting (pygments, dropped) or wide-character editing
(extra cells work). 9 widgets is enough to prove the framework.

**D4. Drop `Style.meta` entirely; store as plain dict, accept
aliasing.** Rationale: 02 §"Needed Shims" item 2 calls out that
`pickle` is the dependency. Textual's actual `meta` usage is click /
hover handler IDs and link refs, all JSON-safe. Switching to JSON is
fine but `Style.meta` is rarely mutated post-construction, so dropping
the deep-copy semantics is harmless. Saves the pickle/json shim.

**D5. Single Unicode width table: `unicode15-1-0.py` only (~670 LoC
of static data).** Rationale: 02 open question 2. Modern emoji
coverage matters for Input widget paste handling; the alternative
(drop the table, use `_SINGLE_CELL_UNICODE_RANGES` only) is wrong for
emoji and saves only 670 LoC of pure data that compresses well to
romfs.

**D6. Single-thread assumption locked.** No worker threads, no
`threading.get_ident()`-keyed locks, no `_thread` dependency in
v0.1. Rationale: 03 §"Adjacent modules" notes `asyncio.TaskGroup` is
absent; pre-0.50 Textual avoids it. Pinning to pre-0.50 Textual as
source and asserting single-thread means we can replace
`contextvars.ContextVar` with a module-level stack and skip RLock
ownership tracking entirely.

**D7. Drop animation in v0.1.** No `_animator.py` port. Rationale:
01 §"Animator" - drags in `runtime_checkable Protocol` and
`@dataclass`. The shim pack would cover both but animation is a
v0.2 polish item; no v0.1 widget needs it.

**D8. Frozen Python over loaded.** All of picolet-tui ships as frozen
`.mpy` in the variant. Rationale: 04 §4 measures the parser + key
table at ~6 KB romfs; the whole framework will land around ~80-120
KB romfs which is sub-1% of a typical embedded flash budget and gives
import-time amortisation.

**D9. Regex engine decision deferred to Phase 1 spec.** Rationale:
03 §"Open questions" item 4. Both paths (keep re1.5 + hand-rolled
tokenizers / swap to pcre2 at C build time) are tractable. The
deciding factor is whether other picolet workstreams want pcre2;
TUI alone doesn't justify a runtime-wide regex swap.

## 6. Pointers into the four research docs

For implementation detail, the synthesis defers to:

| Topic | See |
|---|---|
| Textual class-construction blockers + which leaves are drop-in | `01-textual-deps.md` §"Core types", §"CPython-only constructs", §"Leaves that are nearly drop-in" |
| Rich module-by-module trim list + LoC sizing | `02-rich-subset.md` §"Module Map", §"Minimum Subset", §"Excluded Features" |
| Per-shim recipe + LoC estimates for the Phase 2b pack | `03-mp-stdlib-coverage.md` §"Per-module table", §"Which gaps will hurt the port most" |
| `tuiterm` C API + Unix raw-mode + Windows VT incantations + ANSI parser state machine | `04-terminal-handling.md` §§1-4 |
| Specific code snippets to copy: Color downgrade math, lru_cache shim, _detect_color_system | `02-rich-subset.md` §"Needed Shims", §"Color Path" |
| Mouse SGR decoder + bracketed paste + xterm modifier encoding | `04-terminal-handling.md` §3 |
| Open questions still owned by Phase 1 spec | `01-textual-deps.md` §"Open questions" Q1-Q7, `02-rich-subset.md` §"Open Questions" all, `03-mp-stdlib-coverage.md` §"Open questions" all, `04-terminal-handling.md` §5 |

## 7. What Phase 1 spec must close

A non-exhaustive list of decisions the Phase 1 spec must put a stake
through, drawn from the 27 open questions across docs 01-04:

1. Regex engine: re1.5 + hand-tokenizers, or pcre2 swap at C build?
   (R1 lives here.)
2. Textual source version pin (pre-0.50 vs post-0.50 + TaskGroup
   back-port).
3. v0.1 widget set frozen (Decision D3 above is the proposal; confirm).
4. CSS in v0.1 or only Style DSL? (Decision D2 proposes DSL; confirm.)
5. Threading model frozen at single-thread? (Decision D6 proposes yes.)
6. Animation in v0.1? (Decision D7 proposes no.)
7. `Style.meta` strategy: drop, JSON-shim, or full pickle-shim?
   (Decision D4 proposes drop.)
8. Unicode width table version (D5 proposes 15.1.0).
9. `_thread` available in the picolet-tui variant build? If no,
   removes one whole class of bugs.
10. Driver targets locked: Linux + Windows VT only, no macOS, no
    pre-1809 Windows, no embedded UART in v0.1.
