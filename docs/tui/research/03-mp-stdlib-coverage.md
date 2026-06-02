# MicroPython stdlib coverage for Textual + Rich

Survey of what micropython-lib and core MicroPython ship for the standard-library
constructs that Textual and Rich actually import. Sources inspected:

- `packages/picolet-runtime/micropython/lib/micropython-lib/python-stdlib/`
- `packages/picolet-runtime/micropython/lib/micropython-lib/python-ecosys/`
- `packages/picolet-runtime/micropython/lib/micropython-lib/micropython/` (notably `ucontextlib`)
- `packages/picolet-runtime/micropython/lib/micropython-lib/unix-ffi/` (host-only)
- Core MicroPython C modules under `/home/anl/micropython/py/` and `/home/anl/micropython/extmod/`

Classification key:

- **core** — built into the MicroPython VM/runtime, no Python-level shim needed.
- **lib** — micropython-lib ships a `python-stdlib/<name>/` package that is usable as-is for the listed surface.
- **partial** — present but missing features the target code needs; gap noted.
- **absent** — neither core nor micropython-lib provides anything; needs a fresh shim or upstream port.
- **unix-only** — exists in `unix-ffi/`, depends on `ffi`/libc; will not run on bare-metal targets without rework.

## Per-module table

| Module | Status | Notes / gaps |
| --- | --- | --- |
| `dataclasses` | absent | Not in micropython-lib, not in core. Decorator with field/`__init__`/`__repr__` synthesis is doable in pure Python (~200 LOC) but no `frozen=True` slots, no `__slots__` auto-add on MP (no `__class__` rewrite), no `field(default_factory=...)` parity without a careful shim. Rich + Textual lean on it heavily (every renderable, every Widget reactive). |
| `typing` | absent | Nothing whatsoever. Textual and Rich annotate aggressively (`List[X]`, `Optional[Y]`, `Generic[T]`, `Protocol`, `TYPE_CHECKING`, `cast`, `overload`, `TypedDict`, `Literal`). Subscripting (`List[int]`) is evaluated at import time in many files, so a stub that makes `typing.List` callable / subscriptable and returns the runtime origin is mandatory. A no-op shim that returns `lambda *a, **kw: a[0] if a else None` for every name, plus a `_GenericAlias` that swallows `__getitem__`, is the minimum viable layer. |
| `contextlib` | lib | `python-stdlib/contextlib/contextlib.py` re-exports `ucontextlib` (which provides `contextmanager`, `_GeneratorContextManager`) and adds `closing`, `suppress`, `ExitStack`. **Missing: `AsyncExitStack`, `asynccontextmanager`, `nullcontext`, `aclosing`.** Textual uses `AsyncExitStack` (for async test harnesses) and `nullcontext` (cheap). |
| `functools` | partial | Only `partial`, `reduce`, and stubbed `wraps`/`update_wrapper` (`wraps` is a no-op that returns `lambda x: x` — decorator metadata is lost). **Missing: `lru_cache`, `cache`, `total_ordering`, `cached_property`, `singledispatch`, `partialmethod`.** Rich uses `lru_cache` extensively for color/style interning; that one alone is load-bearing for performance. `total_ordering` appears in a handful of Rich primitives. |
| `itertools` | partial | Pure-Python: `count`, `cycle`, `repeat`, `chain`, `islice`, `tee`, `starmap`, `accumulate`. **Missing: `product`, `permutations`, `combinations`, `combinations_with_replacement`, `zip_longest`, `groupby`, `compress`, `dropwhile`, `takewhile`, `filterfalse`, `chain.from_iterable`.** `chain.from_iterable` is the painful absence — it shows up in Textual's CSS rule flattening and Rich's segment merging. Easy to monkey-patch. |
| `inspect` | partial | Predicates only: `isfunction`, `isgenerator(function)`, `iscoroutine(function)` (aliased to generator — wrong for native coros), `ismethod`, `isclass`, `ismodule`. `getmembers` works. **`getargspec`/`signature`/`Parameter`/`getfullargspec`/`getsourcelines`/`getsource` are stubs that raise or return placeholders.** Textual's reactive descriptor system inspects function signatures (`signature(method).parameters`) to decide how many args to pass to watchers/computes — this is the single biggest stdlib blocker. Rich uses `inspect.signature` for `Console.print` argument introspection. |
| `weakref` | partial | Core MP `modweakref.c` exposes only `ref` and `finalize`. **No `WeakSet`, `WeakValueDictionary`, `WeakKeyDictionary`, no `proxy`, no `WeakMethod`.** Textual's `MessagePump` holds children in a `WeakSet` and Textual's reactive system uses `WeakValueDictionary` for the active-app registry. Pure-Python shims on top of `ref` are feasible (~50 LOC per container) but the `proxy` semantic (transparent attribute forwarding) is a real reimplementation. |
| `copy` | lib | Full `copy`/`deepcopy` from CPython's reference implementation, with dispatch tables and memo. Handles list/tuple/dict/set/OrderedDict, `__copy__`/`__deepcopy__`, `__reduce__`. Adequate for Textual's `_NodeList.copy()` and Rich's `Style.copy()`. |
| `enum` | absent | Not shipped. Textual uses plain `Enum` (`MessagePump._PumpState`), `IntEnum`, and `Flag` (in `lookup.py` for CSS specificity). A pure-Python shim is achievable but `Flag`'s bitwise composition + `__iter__` over set bits is non-trivial (~150 LOC). `IntEnum` is the lightest case — single-class inheriting `int` with name table. |
| `abc` | partial | `python-stdlib/abc/abc.py` is two lines: `class ABC: pass` and `def abstractmethod(f): return f`. **No `ABCMeta`, no `__subclasshook__`, no `register()`, no enforcement of abstract methods.** Textual's Widget hierarchy uses `ABC` mostly for documentation, not enforcement, so the stub is *probably* enough — but anything that does `isinstance(x, MyABC)` against a class registered with `MyABC.register(...)` will silently fail. Rich's `RichRenderable` Protocol-like check should be audited. |
| `collections` | core+lib | `deque`, `OrderedDict`, `namedtuple` all in core (`py/objdeque.c`, `py/modcollections.c`, `py/objnamedtuple.c`) gated on `MICROPY_PY_COLLECTIONS_DEQUE`/`_ORDEREDDICT` (both default on at the `EXTRA_FEATURES` ROM level — must verify the picolet build enables them). `defaultdict` ships separately as `collections-defaultdict` and is auto-imported by `python-stdlib/collections/__init__.py`. namedtuple lacks `_field_defaults`, `_replace` is present. `deque` supports `maxlen`, `append`, `appendleft`, `popleft`. **No `ChainMap`, no `Counter`, no `UserDict`/`UserList`/`UserString`.** Rich uses `Counter` in one place (`Console.export_html` palette dedup) — easy to fake. `MutableMapping` is exposed as a bare `class MutableMapping: pass` (no mixin methods), so anything that subclasses it for free `keys()/values()/items()` will break. |
| `re` | core (limited) | `extmod/modre.c` over `lib/re1.5/`. **No flags (`IGNORECASE`, `MULTILINE`, `DOTALL`, `VERBOSE`) at all.** No named groups (`(?P<name>...)`), no backreferences, no lookahead/lookbehind, no Unicode property escapes. `match.span()`, `match.start()`, `match.end()` are present iff `MICROPY_PY_RE_MATCH_SPAN_START_END` is enabled. `re.sub` is gated on `MICROPY_PY_RE_SUB`. This is the second-biggest blocker after `inspect`: Rich's markup parser uses verbose patterns with named groups for `[bold red on blue]` tags, and Textual's CSS tokenizer uses multiline mode with named groups. A shim is impractical — either rebuild MP with a fuller regex engine, or rewrite Rich's markup parser and Textual's CSS tokenizer by hand. |
| `struct` | core+lib | `python-stdlib/struct/struct.py` re-exports `ustruct` (in core) and adds the `Struct` class. Full pack/unpack for fixed-width formats. Adequate. |
| `argparse` | partial | 226-line minimal implementation. Supports `add_argument` with `action='store'/'store_true'/'append'`, positional + optional, `nargs`, `--help`. **No subparsers, no `argument_group`, no `mutually_exclusive_group`, no `type=` callable on every arg, no `choices=`, no env-var fallback.** Sufficient for a `picolet run --foo bar` CLI; insufficient if we re-host Textual's `textual` dev CLI verbatim. |
| `threading` | partial | 15-line wrapper over `_thread.start_new_thread`. **No `Lock`, `RLock`, `Event`, `Condition`, `Semaphore`, `Timer`, `local`, `current_thread()`.** Core MP `_thread` exposes `allocate_lock()` (returns a non-recursive lock with `acquire`/`release`/`__enter__`/`__exit__`). `MICROPY_PY_THREAD_RECURSIVE_MUTEX` exists at the C level but **only when the GIL is OFF** — picolet (CPython-mode unix port) will have GIL ON, so RLock from C is not available. A pure-Python `RLock` over `_thread.allocate_lock` + owner-thread tracking via `_thread.get_ident()` is the standard recipe (~30 LOC). Textual touches `RLock` in `MessagePump` and the screen-stack lock. |
| `signal` | unix-only | `unix-ffi/signal/signal.py` is a libc `signal(2)` wrapper via `ffilib`. Unix-only. Bare-metal MP has no signal concept. Textual installs `SIGWINCH` for terminal-resize and `SIGINT` for Ctrl-C handling. SIGWINCH does not apply if we are not running under a POSIX TTY anyway (picolet is going to ship its own pty/console layer). SIGINT can be replaced by MP's `KeyboardInterrupt` mechanism. |
| `select` / `selectors` | core + unix-only | Core MP ships `select` (gated on `MICROPY_PY_SELECT`) with `poll()` and optionally `select.select()` (`MICROPY_PY_SELECT_SELECT=1` by default). **`selectors` is not provided anywhere.** Textual's driver uses `selectors.DefaultSelector` to poll stdin + the event-loop wakeup pipe; need a thin shim that maps `selectors.SelectSelector` onto `select.poll`. |

## Adjacent modules worth flagging

- `asyncio` — core MP ships `extmod/asyncio/` (`Task`, `Event`, `Lock`, `Queue`, `gather`, `wait_for`, streams). Textual's async runtime should mostly map. **No `asyncio.TaskGroup`** (Python 3.11+), **no `asyncio.timeout()`** context manager, **no `asyncio.to_thread`**, **no `loop.run_in_executor`**. Textual >=0.50 uses `TaskGroup` in `App._process_messages` — that has to be back-ported to `gather` with manual exception aggregation.
- `datetime` — `python-stdlib/datetime/` is pure-Python and full enough for Rich's traceback timestamps.
- `types` — present in core (`MICROPY_PY_BUILTINS_TYPES`). `MappingProxyType` and `SimpleNamespace` need spot-checking.
- `traceback` — `python-stdlib/traceback/` exists; Rich's `Traceback` renderer post-processes `sys.exc_info()` and `__traceback__` frames, but MP tracebacks don't expose `tb_frame.f_locals` in the same way — Rich's variable-context rendering will degrade.

## Which gaps will hurt the port most

Ranked by blast radius on the Textual + Rich port:

1. **`re` flags + named groups.** Forces a rewrite of Rich's markup parser, Textual's CSS tokenizer, and any user-supplied regex in widgets. Either swap the regex engine in core MP (re1.5 -> something larger) or carry hand-rolled tokenizers. This is the single decision that most shapes the project.
2. **`inspect.signature`.** Textual's reactive descriptor system is built on it. Without it, every `watch_*` / `compute_*` / event handler dispatch has to be rewritten to use a registration decorator that captures arity explicitly. Doable, invasive, must touch every widget.
3. **`typing` shim.** Mechanical but unavoidable. ~150 LOC stub gets the imports to resolve; many subscripts (`List[Foo]`) need to return *something* both `isinstance`-able and `__class_getitem__`-able.
4. **`dataclasses`.** Core to how reactives, messages, and Rich renderables are defined. A 200-line shim covering `@dataclass`, `field()`, `__init__`/`__repr__`/`__eq__` synthesis, `frozen=False` only, is the realistic target. `frozen=True` and `slots=True` should be unsupported and patched out at the call sites.
5. **`enum.Flag`.** Used in Textual's CSS specificity arithmetic. Pure-Python implementation is fine but non-trivial.
6. **`weakref.WeakSet` / `WeakValueDictionary`.** Buildable on `weakref.ref`. Without it, Textual's parent-child tracking has a real risk of leaking widget trees when screens pop.
7. **`functools.lru_cache`.** Rich's color and style caches assume it. Trivial pure-Python LRU using `OrderedDict`; do this once and move on.
8. **`selectors`.** Thin shim over `select.poll`. Half a page of code.
9. **`asyncio.TaskGroup`.** Pin Textual to a pre-TaskGroup version, or back-port with `gather`. The latter loses cancellation semantics on partial failure.
10. **`threading.RLock`.** ~30 LOC over `_thread.allocate_lock`. Get it out of the way early.

The first two items dominate. If `re` cannot be expanded and `inspect.signature` cannot be added, Textual is not portable in any reasonable sense — every higher-altitude shim is downstream of those two.

## Open questions

- Does picolet's MicroPython build set `MICROPY_PY_COLLECTIONS_DEQUE` / `_ORDEREDDICT` to 1? Both default to "EXTRA_FEATURES" ROM level — confirm the runtime's mpconfig.
- Will picolet ship `MICROPY_PY_WEAKREF=1`? Required for any of the weak-container shims to work.
- Is the host-only `unix-ffi/select` epoll wrapper needed, or is core `select.poll` sufficient for the picolet pty driver?
- Is there appetite to swap `re1.5` for `oniguruma`/`pcre2` at compile time? That would close the largest single gap in one change.
- What Textual version are we targeting? Pre-0.50 avoids `asyncio.TaskGroup`; post-0.50 means back-porting.
