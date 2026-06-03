# picolet-tui Textual Core - Phase 4b Design

Status: design doc, locked input for Phase 4b implementation.
Date: 2026-06-03
Audience: the Phase 4b implementers porting `MessagePump`, `DOMNode`,
`Widget`, `Reactive`, `Message`, `Screen`, `Binding`, `App`, and
the `@widget` decorator into
`packages/picolet-runtime/python/picolet_tui/`.

Inputs locked upstream of this doc:

- `docs/tui/tui-v0.1-spec.md` - FR-TUI-1..78, NFR-TUI-1..32, §3.
- `docs/tui/research/00-synthesis.md` - decisions D1-D9, risk R3.
- `docs/tui/research/01-textual-deps.md` - CPython-only constructs.
- `packages/picolet-runtime/python/picolet_tui/_textual/` -
  Phase 4a leaves (`geometry`, `color`, `keys`, `_easing`, `scalar`,
  `case`).

This doc is **pseudo-code** that Phase 4b agents translate.  It is
not test-tier; it does not enumerate test names (the spec already
does in §4.4).  It is not requirement-tier; it cites FR-TUI-x ids
where it pins behaviour to the spec.

Sections:

1. The `@widget` decorator algorithm
2. Reactive descriptor design
3. MessagePump algorithm
4. DOMNode + Widget + Screen hierarchy
5. App class
6. Binding system
7. Compositor integration contract (4c preview)
8. Phase 4b implementation order
9. `MissingWidgetDecoratorError` diagnostics
10. Upstream-Textual compatibility migration story

## 1. The `@widget` decorator algorithm

The decorator is the **single** place class-time introspection lives
(synthesis D1).  MicroPython does not invoke `__init_subclass__`,
does not honour `metaclass=`, and does not call `__set_name__` on
descriptors.  Textual's `_MessagePumpMeta`, `DOMNode.__init_subclass__`,
`Message.__init_subclass__`, and `Reactive.__set_name__` are all
replaced by this one pass.

### 1.1 Bucket classification

Walk `vars(cls)` exactly once and bucket each name by what it is:

```python
def widget(cls):
    # Defensive: catch authoring mistakes early.
    if not isinstance(cls, type):
        raise TypeError("@widget must decorate a class, got %r" % type(cls))

    meta = {
        "reactives": {},          # name -> Reactive descriptor
        "computes": {},           # name -> compute_<name> method
        "handlers": {},           # type[Message] -> list[(method, selector)]
        "name_handlers": {},      # type[Message] -> list[(method, arity)]
        "bindings": [],           # list[Binding]
    }

    for name, value in vars(cls).items():
        # bucket 1: Reactive descriptors
        if isinstance(value, Reactive):
            meta["reactives"][name] = value
            value._bind_name(name, cls)            # replaces __set_name__

        # bucket 2: compute_<name> methods
        elif name.startswith("compute_") and callable(value):
            attr = name[len("compute_"):]
            meta["computes"][attr] = value

        # bucket 3: @on(MessageType, selector=...) decorated handlers
        elif callable(value) and getattr(value, "_tui_on", None):
            for sel in value._tui_on:
                bucket = meta["handlers"].setdefault(sel.message_type, [])
                bucket.append((value, sel))

        # bucket 4: on_<message_name_snake_case> name-dispatched handlers
        elif name.startswith("on_") and callable(value):
            # Resolution against actual Message types happens at handler
            # dispatch time (see §3.4); recorded here only as arity hint.
            arity = value.__code__.co_argcount    # includes 'self'
            meta.setdefault("name_handlers_by_name", {})[name] = (value, arity)

        # bucket 5: BINDINGS class attribute
        elif name == "BINDINGS" and isinstance(value, (list, tuple)):
            meta["bindings"].extend(Binding._coerce(b) for b in value)

        # bucket 6: TCSS / CSS - DEFERRED to v0.2.  Synthesis D2.
        elif name in ("DEFAULT_CSS", "CSS"):
            # silently ignored in v0.1; warned in mpm check (Phase 6).
            pass

        # bucket 7: default - no work.
        else:
            pass

    # Validate co-existence rules captured by the spec.
    _validate_no_compute_reactive_collision(meta, cls)   # FR-TUI-21

    # MRO merge - subclass wins.
    _merge_parent_meta(meta, cls)                        # §1.2

    cls._tui_widget_meta = meta
    cls._tui_widget_registered = True
    return cls
```

The walk is over `vars(cls)` only.  No `dir(cls)`, no
`inspect.getmembers`, no MRO-flattening dict.  Cost is O(len(class
body)), no descriptor wakes, no Python-level attribute access.

### 1.2 Inheritance handling

**Decision: re-merge parent meta into the child's meta at decoration
time.**  Each `@widget`-decorated class gets its own
`cls._tui_widget_meta` dict containing the union of its own
declarations and its decorated ancestors'.

Rationale:

- Lookup cost during message dispatch is one dict access per node
  (§3.4 `type(node)._tui_widget_meta`).  Delegating to
  parent meta would require either a chain walk per message (slow,
  scales with depth) or storing a parent pointer on each meta dict
  (added complexity for no benefit).
- Memory cost of the duplication is small: nine v0.1 widgets, no
  deep widget hierarchies in tui-pydfu or hello-tui, and each meta
  dict references the parent's descriptor objects rather than
  copying them.
- A flat per-class meta lets `type(node)._tui_widget_meta` be a
  single attribute lookup, matching the hot-path expectations of
  §3.4 in the spec.

Merge rule, subclass-wins:

```python
def _merge_parent_meta(meta, cls):
    # Walk MRO from grand-parent toward Widget; later iterations
    # override earlier ones, then child's own meta overrides all.
    own = {k: dict(v) if isinstance(v, dict) else list(v)
           for k, v in meta.items()}
    for base in reversed(cls.__mro__[1:]):                # parents first
        parent_meta = getattr(base, "_tui_widget_meta", None)
        if parent_meta is None:
            # Spec FR-TUI-28: if the parent declares any artifact that
            # @widget would have captured but lacks _tui_widget_meta,
            # raise MissingWidgetDecoratorError at decoration time.
            if _has_capturable_artifacts(base):
                raise MissingWidgetDecoratorError(base, raised_from=cls)
            continue
        for key in ("reactives", "computes", "handlers",
                    "name_handlers_by_name", "bindings"):
            _merge_one(meta, parent_meta, key)
    # Re-apply own to win over parents.
    for key, value in own.items():
        if isinstance(value, dict):
            meta[key].update(value)
        else:
            # bindings: subclass-declared come last, take precedence at
            # dispatch (last match wins for the same key chord).
            meta[key].extend(value)
```

`_has_capturable_artifacts(base)` scans `vars(base)` once for any
`Reactive`, `compute_*`, `@on`-decorated, or `BINDINGS` attribute.
Cost is paid at decoration time, not per message.

### 1.3 Runtime guard

Every `Widget.__init__` (and `Screen.__init__`, `App.__init__`)
asserts:

```python
if not getattr(type(self), "_tui_widget_registered", False):
    raise MissingWidgetDecoratorError(type(self))
```

This is the R3 mitigation (synthesis §3 risk register).  Cost:
one `getattr` per instantiation.  See §9 for the error message.

### 1.4 What the decorator does *not* do

- It does not install slots, slotted layouts, or `__slots__`.
  MicroPython slot support is incomplete.
- It does not synthesise `__hash__`, `__eq__`, or `__repr__`.
  Widgets are reference-identity.
- It does not call `super().__init_subclass__()`.  There is no
  `__init_subclass__` chain in the picolet-tui hierarchy.
- It does not register the class with any global registry.  Lookup
  is class-attribute-driven.

## 2. Reactive descriptor design

### 2.1 Constructor

```python
class Reactive:
    """A reactive attribute descriptor.

    Replaces the upstream Textual ``Reactive`` whose ``__set_name__``
    binding (CPython descriptor protocol) MicroPython does not call.
    The owning class's ``@widget`` decorator calls ``_bind_name`` at
    class-decoration time to perform the equivalent registration.
    """

    def __init__(
        self,
        default,
        *,
        layout=False,
        init=True,
        always_update=False,
    ):
        self._default = default
        self._layout = layout
        self._init = init
        self._always_update = always_update
        self._attr_name = None              # set by _bind_name
        self._private_name = None           # "_reactive_<name>"
        self._owner = None
        self._watch_name = None             # "watch_<name>"
        self._validate_name = None          # "validate_<name>"
        self._watch_arity = None            # 2 or 3 (with self)
```

`Reactive` does **not** inherit from any ABC; MicroPython's
`abc.abstractmethod` is shim-only and `isinstance(x, Reactive)`
is the only check `@widget` makes.

### 2.2 `_bind_name` (replaces `__set_name__`)

Called by `@widget` exactly once per `Reactive` on each owning class:

```python
def _bind_name(self, name, owner):
    self._attr_name = name
    self._private_name = "_reactive_" + name
    self._owner = owner
    # Resolve siblings - watch_<name>, validate_<name>.
    watch = vars(owner).get("watch_" + name)
    if watch is not None:
        self._watch_name = "watch_" + name
        # __code__.co_argcount includes 'self'.  arity 2 means
        # (self, new); arity 3 means (self, old, new).
        self._watch_arity = watch.__code__.co_argcount
    validate = vars(owner).get("validate_" + name)
    if validate is not None:
        self._validate_name = "validate_" + name
```

`watch_<name>` and `validate_<name>` resolution is done on the
**owner** at decoration time, not via `getattr(instance, ...)` at
runtime.  This is the only way to inspect `__code__.co_argcount`
without paying the cost on every assignment.

### 2.3 `__get__` and `__set__`

```python
def __get__(self, instance, owner):
    if instance is None:
        return self
    # Computed reactives short-circuit: compute_<name> always
    # produces the current value.  FR-TUI-21.
    meta = type(instance)._tui_widget_meta
    compute = meta["computes"].get(self._attr_name)
    if compute is not None:
        return compute(instance)
    try:
        return instance.__dict__[self._private_name]
    except KeyError:
        return self._default

def __set__(self, instance, new):
    meta = type(instance)._tui_widget_meta
    if self._attr_name in meta["computes"]:
        # FR-TUI-21: writing to a computed reactive raises.
        raise ReactiveError(
            "Cannot assign to computed reactive %r on %s" %
            (self._attr_name, type(instance).__name__)
        )
    old = instance.__dict__.get(self._private_name, self._default)
    if self._validate_name is not None:
        new = getattr(instance, self._validate_name)(new)
    if (not self._always_update) and old == new:
        # FR-TUI-22: always_update=True fires watcher even when unchanged.
        return
    instance.__dict__[self._private_name] = new
    if self._watch_name is not None:
        watch = getattr(instance, self._watch_name)
        if self._watch_arity == 2:                # (self, new)
            _invoke_watcher(watch, new)
        else:                                     # (self, old, new)
            _invoke_watcher(watch, old, new)
    if self._layout:
        instance.refresh(layout=True)             # FR-TUI-31
    else:
        instance.refresh()
```

`_invoke_watcher` posts the call onto the asyncio loop if the
watcher is `async def`; otherwise it calls directly.  Detection is
via `iscoroutinefunction` from the picolet `_callback` shim.

### 2.4 `Reactive.watch` / `Reactive.compute`

The descriptor itself exposes class-level helpers used by tests and
by Phase 4b code that wires reactives dynamically (e.g. inside
generic widgets):

```python
class Reactive:
    def watch(self, instance, callback):
        """Register an extra watcher on a specific instance."""
        instance.__dict__.setdefault(
            self._private_name + "_extra_watchers", []
        ).append(callback)

    def compute(self, instance):
        """Force a recompute for this descriptor on an instance."""
        meta = type(instance)._tui_widget_meta
        compute = meta["computes"].get(self._attr_name)
        if compute is None:
            return self.__get__(instance, type(instance))
        value = compute(instance)
        # Cache the recomputed value in the private slot for symmetry.
        instance.__dict__[self._private_name] = value
        return value
```

These are not strictly required by FR-TUI-19..22 but are cheap and
unblock test-side patterns the upstream Textual test suite uses.

## 3. MessagePump algorithm

### 3.1 Queue choice

```python
class MessagePump:
    def __init__(self, parent=None):
        self._queue = collections.deque()       # NFR-TUI-9 - deque enabled
        self._queue_cap = 4096                  # spec §3.4 soft cap
        self._parent = parent
        self._closing = False
        self._closed = False
        self._task = None                       # asyncio.Task for run loop
        self._wake = asyncio.Event()            # signal new message
```

`collections.deque` (NFR-TUI-9 build flag) replaces upstream
Textual's `asyncio.Queue`.  `asyncio.Queue` is available in
MicroPython but the deque-plus-Event pattern is what the pre-0.50
gather-based Textual pump used (D6 baseline), is cheaper, and gives
us explicit overflow control.

Overflow policy: drop **oldest** event, log to `App.log` (FR-TUI-78).
The synthesis explicitly picks oldest-drop to match pre-0.50 Textual;
newest-drop would let a wedged widget cause user-visible event loss
at the head of the queue.

### 3.2 Parent pointer

```python
self._parent = parent          # explicit reference, no weakref.ref
```

Upstream Textual uses `weakref.ref(parent)`.  MicroPython has
`weakref` (NFR-TUI-9) but the shim is for `WeakSet` / `WeakValueDictionary`
on top of `weakref.ref`; for parent pointers explicit lifecycle is
clearer and avoids one whole class of "parent garbage collected
mid-dispatch" bugs.

Lifecycle implications:

- A child holds a strong ref to its parent.  A parent's strong ref
  list of children is `self._children: list[MessagePump]`.
- This is a true reference cycle.  Resolved by explicit
  `_dispose()` at unmount time (FR-TUI-24): the parent removes the
  child from `_children`, the child sets `_parent = None` and
  drops any cached resources, the child's pump task is cancelled.
- No `__del__` is defined.  Cleanup is explicit.

### 3.3 `process_messages` coroutine

```python
async def process_messages(self):
    """Run loop for this pump.  Cancelled on _dispose."""
    while not self._closing:
        if not self._queue:
            self._wake.clear()
            await self._wake.wait()
            continue
        message = self._queue.popleft()
        try:
            await self._dispatch_self(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # FR-TUI-77: user-handler exceptions caught at dispatch
            # boundary, logged to stderr, bubbling continues.
            _log_handler_exception(self, message, exc)
    self._closed = True
```

`_dispatch_self` handles the local node's `@on` and `on_<name>`
handlers; bubbling to the parent is `_dispatch_bubble` (next
section).  The split lets a leaf-only message stay local and
avoids walking the chain when the local handler calls `stop()`.

### 3.4 Bubbling

A message posted to a leaf widget walks the DOM up through the
parent chain.  Each level consults two handler sources: `@on`-decorated
handlers (preferred) and name-based `on_<event>` (fallback per
FR-TUI-14).

```python
async def _dispatch(node, message):
    """Walk node -> parent -> ... -> root applying handlers in order.

    Returns silently when bubbling is stopped or the chain is exhausted.
    """
    while node is not None:
        meta = type(node)._tui_widget_meta
        message_type = type(message)

        # Preferred: @on-decorated handlers.
        for handler, selector in meta["handlers"].get(message_type, ()):
            if selector.matches(node, message):
                try:
                    result = await _invoke_handler(handler, node, message)
                except Exception as exc:
                    _log_handler_exception(node, message, exc)
                    continue                      # FR-TUI-77
                if result is True or message._stop_bubble:
                    return

        # Fallback: on_<message_name_snake_case>.
        name = "on_" + _camel_to_snake(message_type.__name__)
        name_handler = vars(type(node)).get(name)
        if name_handler is not None:
            arity = meta.get("name_handlers_by_name", {}).get(name)
            try:
                if arity is None or arity[1] == 1:
                    result = await _invoke_handler(name_handler, node)
                else:
                    result = await _invoke_handler(name_handler, node,
                                                   message)
            except Exception as exc:
                _log_handler_exception(node, message, exc)
                node = node._parent
                continue
            if result is True or message._stop_bubble:
                return

        node = node._parent
```

Three properties matter:

1. The `meta["handlers"]` dict is keyed on `type(message)`, so the
   per-node cost of a non-matching message is one dict miss.
2. `@on` handlers fire **before** name-based handlers at the same
   level (FR-TUI-14: "after `@on`-decorated handlers").  Read
   carefully: the spec says name-based fires *after* `@on`; the
   dispatch order in this section preserves that.
3. `event.stop()` halts immediately - no further handlers at this
   level fire, and no parent is visited.

`_camel_to_snake` is the same helper Phase 4a ported in
`_textual/case.py`.

### 3.5 Posting and back-pressure

```python
def post_message(self, message):
    if self._closing:
        return False
    if len(self._queue) >= self._queue_cap:
        # Drop oldest, log once per overflow event (rate-limited
        # by Compositor._dirty coalescing).
        dropped = self._queue.popleft()
        _log_queue_overflow(self, dropped)
    self._queue.append(message)
    self._wake.set()
    return True
```

`post_message` is sync (`asyncio.Queue.put` would be async).  Sync
post is the upstream pre-0.50 Textual behaviour and is required by
the timer path which fires from `loop.call_later` synchronously.

## 4. DOMNode + Widget + Screen hierarchy

The hierarchy is `MessagePump -> DOMNode -> Widget -> Screen`.  Each
level contributes its own `_tui_widget_meta` keys; the
`@widget` decorator merges them along the MRO.

### 4.1 DOMNode

```python
@widget
class DOMNode(MessagePump):
    """Tree node.  Bindings, classes, id."""

    BINDINGS = []                                   # base

    def __init__(self, *, id=None, classes="", parent=None):
        super().__init__(parent=parent)
        self.id = id
        self.classes = set(classes.split()) if classes else set()
        self._children = []
        self._region = NULL_REGION                  # set by layout
        self._dirty = True
```

What DOMNode contributes to meta:

- `bindings`: the base `BINDINGS = []`.  Subclasses extend.
- `name_handlers_by_name`: any `on_*` it defines (none in v0.1).

### 4.2 Widget

```python
@widget
class Widget(DOMNode):
    """Renderable, mountable, focusable.  Reactive home."""

    can_focus = False
    can_focus_children = True
    BINDINGS = []

    expand = Reactive(False)
    shrink = Reactive(True)

    def __init__(self, *children, id=None, classes="", parent=None):
        super().__init__(id=id, classes=classes, parent=parent)
        # R3 mitigation - the guard fires here.  See §1.3.
        if not getattr(type(self), "_tui_widget_registered", False):
            raise MissingWidgetDecoratorError(type(self))
        self._mounted_children = []
        self._mounted = False
        for child in children:
            self._pending_children.append(child)    # mounted in on_mount
```

What Widget contributes to meta:

- `reactives`: `expand`, `shrink`.
- `bindings`: still `[]` at this level; widget subclasses add.

### 4.3 mount / unmount lifecycle

```python
async def mount(self, *children):
    for child in children:
        child._parent = self
        self._children.append(child)
        self._mounted_children.append(child)
        await child._mount()
    return _MountAwaitable(children)                # FR-TUI-23

async def _mount(self):
    self._task = asyncio.create_task(self.process_messages())
    await _maybe_await(self.on_mount)               # FR-TUI-25

async def remove(self):                             # FR-TUI-24
    # depth-first unmount of descendants
    for child in list(self._children):
        await child.remove()
    await _maybe_await(self.on_unmount)
    self._closing = True
    self._wake.set()                                # let process_messages exit
    if self._task is not None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
    if self._parent is not None:
        self._parent._children.remove(self)
        self._parent._mounted_children.remove(self)
        self._parent = None
```

`_MountAwaitable` is the lightweight `await`-able the spec calls for
(FR-TUI-23).  It resolves when every child's `on_mount` has run.

### 4.4 refresh -> compositor

```python
def refresh(self, *, layout=False, repaint=True):
    self._dirty = True
    if layout:
        # Layout pass is App-level - signal the App's dirty flag.
        app = _resolve_app(self)
        app._needs_layout = True
    app._render_dirty.set()                         # wake _render task
```

`_resolve_app` walks the parent chain to the `App` instance.  This
is one of the few places parent-chain walks happen at runtime; cost
is acceptable because `refresh` is comparatively rare (one per
reactive write that crosses an `==` check, plus mounts).

The `_render_dirty` asyncio.Event drives the `_render` task in
§3.4 of the spec.  Multiple refreshes within one frame coalesce
because `Event.set()` is idempotent.

### 4.5 Screen

```python
@widget
class Screen(Widget):
    """A full-display widget that the App's ScreenStack hosts."""

    BINDINGS = [
        Binding("tab", "focus_next", "Focus next"),
        Binding("shift+tab", "focus_previous", "Focus previous"),
    ]

    def __init__(self, *children, id=None, classes="", parent=None):
        super().__init__(*children, id=id, classes=classes, parent=parent)
        self._focus_target = None
        self._dismiss_event = asyncio.Event()
        self._dismiss_result = None

    async def dismiss(self, result=None):
        """D6: no asyncio.Future / concurrent.futures.Future.

        Uses an Event + slot pattern instead.
        """
        self._dismiss_result = result
        self._dismiss_event.set()
        await self._app._screen_stack.pop(self)
```

The `Event + slot` substitute for `asyncio.Future` is what the
synthesis Phase 4b notes mandate and is the pattern the
`test_screen.py::test_dismiss_returns_to_caller` test exercises.

What Screen contributes to meta:

- `bindings`: `tab` / `shift+tab` (FR-TUI-27).

### 4.6 mount / unmount metadata flow

| Level    | `reactives`    | `bindings`            | `handlers` |
|----------|----------------|-----------------------|-----------|
| MessagePump | (none)      | (none)                | (none)    |
| DOMNode  | (none)         | base `[]`             | (none)    |
| Widget   | `expand`, `shrink` | `[]`              | (none)    |
| Screen   | (none)         | `tab`, `shift+tab`    | (none)    |
| App      | (none)         | `ctrl+q -> quit`      | (none)    |
| Button (5) | (none)       | `enter`, `space`      | (none)    |
| Input (5) | `value`       | edit keys             | `Paste`   |
| ProgressBar (5) | `progress` | (none)             | (none)    |

User widgets layer their own reactives, bindings, and handlers on
top.  The `@widget` decorator merges each subclass's contributions
with the cumulative meta walked from the MRO at decoration time
(§1.2).

## 5. App class

### 5.1 Subclassing

```python
@widget
class App(MessagePump):
    """Subclassed by user apps."""

    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]   # FR-TUI-4

    def __init__(self):
        super().__init__(parent=None)
        if _ACTIVE_APP[0] is not None:
            raise RuntimeError("Only one App may run per process")  # FR-TUI-5
        self._screen_stack = ScreenStack(self)
        self._driver = None
        self._exit_result = None
        self._exit_requested = asyncio.Event()
        self._render_dirty = asyncio.Event()
        self._needs_layout = False

    def compose(self):
        """User override: yield root widgets."""
        return iter(())
```

### 5.2 `.run()` (sync entry)

```python
def run(self):
    """Blocking entry from sync code.  FR-TUI-1."""
    try:
        loop = asyncio.get_event_loop()
        running = loop.is_running()
    except RuntimeError:
        running = False
    if running:
        raise RuntimeError(
            "App.run() called from inside a running event loop; "
            "use 'await app.run_async()' instead."
        )
    return asyncio.run(self.run_async())
```

### 5.3 `.run_async()` (async entry, runtime path)

```python
async def run_async(self):
    """FR-TUI-2.  Joins the already-running loop.  Owns the driver."""
    _ACTIVE_APP[0] = self
    try:
        capabilities = await self._driver_enable()    # tuiterm.enable
        await self._mount_initial_screen()
        # Three tasks via gather - NOT TaskGroup.  D6, FR-TUI-59.
        try:
            await asyncio.gather(
                self._pump_input(),
                self._pump_resize(),
                self._render(),
                self._exit_watcher(),
            )
        except asyncio.CancelledError:
            pass
    finally:
        await self._driver_disable()                  # FR-TUI-7 - once
        _ACTIVE_APP[0] = None
    return self._exit_result
```

`_exit_watcher` is the gather-friendly equivalent of upstream
Textual's `Done` future: when `self._exit_requested` fires, the
watcher cancels its siblings via `asyncio.current_task()` walk on
the gather's parent group.

### 5.4 `.exit()` / `.quit()`

```python
def exit(self, result=None):
    """FR-TUI-3.  Orderly shutdown."""
    self._exit_result = result
    self._exit_requested.set()

def quit(self):
    """FR-TUI-4.  Alias for exit(None), bound to ctrl+q."""
    self.exit(None)
```

`exit` is synchronous because timers and signal handlers call it
from sync contexts.  The async tear-down is driven by
`_exit_watcher`.

### 5.5 ScreenStack

```python
class ScreenStack:
    def __init__(self, app):
        self._app = app
        self._stack = []

    async def push(self, screen):                       # FR-TUI-50
        if self._stack:
            self._stack[-1]._on_hidden()
        self._stack.append(screen)
        await self._app.mount(screen)
        screen.focus()

    async def pop(self, screen=None):
        target = screen or self._stack[-1]
        self._stack.remove(target)
        await target.remove()
        if self._stack:
            self._stack[-1]._on_visible()
            self._stack[-1].focus()

    @property
    def current(self):
        return self._stack[-1] if self._stack else None
```

## 6. Binding system

### 6.1 BINDINGS class attribute

`BINDINGS` is a list of `Binding` tuples declared on the class:

```python
class MyScreen(Screen):
    BINDINGS = [
        Binding("d", "toggle_dark", "Toggle dark"),
        Binding("ctrl+r", "refresh", "Refresh"),
        ("h", "show_help"),                  # shorthand 2-tuple
    ]
```

`Binding._coerce` normalises shorthand 2-tuples into full
`Binding(key, action, description="")` instances at decoration time.

### 6.2 Merging across MRO

Inside `@widget`, the BINDINGS bucket is built bottom-up:

```python
def _merge_bindings(meta, parent_meta):
    # Parent bindings first, then child's overwrite-or-append.
    # "Last match wins for the same key chord" - so child's
    # binding for the same key shadows the parent's.
    merged = list(parent_meta["bindings"])
    by_key = {b.key: i for i, b in enumerate(merged)}
    for b in meta["bindings"]:
        if b.key in by_key:
            merged[by_key[b.key]] = b
        else:
            merged.append(b)
    meta["bindings"] = merged
```

### 6.3 Key-press dispatch

A `Key` event delivered by the input pump walks from the focused
widget up the parent chain, consulting each level's BINDINGS:

```python
async def _dispatch_key(focused, key_event):
    node = focused
    while node is not None:
        meta = type(node)._tui_widget_meta
        for binding in meta["bindings"]:
            if binding.matches(key_event):
                action = getattr(node, "action_" + binding.action, None)
                if action is None:
                    # Bubble to App-level action lookup.
                    action = getattr(_resolve_app(node),
                                     "action_" + binding.action, None)
                if action is not None:
                    result = await _invoke_action(action, node, key_event)
                    if result is not False:    # explicit False -> keep walking
                        return
        node = node._parent
    # Fell off the top - dispatch as a normal Key Message for any
    # @on(Key) handlers in the tree.
    await _dispatch(focused, key_event)
```

The action lookup is one `getattr` per node walked.  Bindings are
not consulted for non-key messages.

## 7. Compositor integration contract (Phase 4c preview)

Phase 4b owns the *interface* to the compositor; Phase 4c implements
the diff-and-emit body.  This section pins the contract.

### 7.1 `Widget.render`

```python
def render(self):
    """Return a Rich RenderableType.

    Default returns the widget's content string; subclasses override.
    Static renders self._content; Label renders self._text; Container
    renders an empty placeholder (children render themselves).
    """
    return self._content
```

`render()` returns one of:

- `str` - bare string, becomes a single `Segment`.
- `_rich.text.Text` - styled text, already a sequence of segments.
- Any object with a `__rich_console__(console, options)` method -
  the Rich protocol.

The compositor's contract is *only* with `render()`-returning shapes
that the trimmed `_rich.console` knows how to drive.  Phase 3
constrained that set: no `Markdown`, no `Syntax`, no `Traceback`,
no `Live`, no `Progress`.

### 7.2 Compositor diff algorithm

The 4c compositor maintains a per-frame strip cache:

```python
class Compositor:
    def __init__(self, console, cols, rows):
        self._console = console                  # picolet_tui._rich.console
        self._cols = cols
        self._rows = rows
        self._last_strips = [[] for _ in range(rows)]   # last frame
        self._current_strips = [[] for _ in range(rows)]

    def render(self, root):
        self._current_strips = [[] for _ in range(self._rows)]
        self._render_widget(root, root._region)
        return self._diff_and_emit()

    def _diff_and_emit(self):
        out = []
        for y in range(self._rows):
            if self._current_strips[y] != self._last_strips[y]:
                out.append(_cup(y, 0))               # CUP to start of row
                out.extend(_seg_to_ansi(seg)
                           for seg in self._current_strips[y])
        self._last_strips = self._current_strips
        return b"".join(out)
```

The contract Phase 4b owes Phase 4c:

- Every mounted widget has a populated `_region` (set by the layout
  pass).
- `widget.render()` returns one of the three shapes in §7.1.
- `widget._dirty` is True iff something has changed since the
  widget was last composited; the compositor clears it after
  emitting.
- `widget.styles` is a `Style` instance that can be queried for
  background, padding, border per FR-TUI-32..37.

### 7.3 ConsoleOptions threading

The trimmed `_rich.console.Console` instance is owned by the
compositor and never escapes.  Each `render_lines` call takes a
fresh `ConsoleOptions(width=region.width, height=region.height)`
constructed from the widget's region.  No thread-local
`ConsoleOptions` (D6 - single-thread).  No console capture stack
(Phase 3 dropped it).

## 8. Phase 4b implementation order

Recommended order, with the dependency and testability justification
for each step:

1. **`Reactive` descriptor** (no `@widget` yet).
   - Tested standalone against a hand-rolled bootstrap class that
     manually calls `reactive._bind_name(...)`.
   - Justification: Reactive is leaf in the dependency graph - no
     widget, no message, no pump.  Its assertions in
     `test_reactive.py` are mechanical and fast.

2. **`@widget` decorator skeleton.**
   - Just the `vars(cls)` walk plus bucket classification; no MRO
     merge yet.
   - Tested via `test_widget_decorator.py::test_decorator_populates_meta`.
   - Justification: lets Reactive's `_bind_name` integration land
     before MRO merge complicates things.

3. **`@widget` MRO merge + R3 guard.**
   - Adds `_merge_parent_meta`, `MissingWidgetDecoratorError`.
   - Tested by `test_missing_decorator_raises` and a new
     `test_mro_merge_subclass_wins`.
   - Justification: the merge is the highest-judgement piece;
     decoupling it lets the previous step ship and stabilise.

4. **`Message` + `MessagePump` minimal.**
   - `post_message` / `process_messages` / `_dispatch` / `_dispatch_self`.
   - Parent pointer, deque queue, wake event.
   - Tested by `test_message.py::test_bubble_to_on_decorator` and
     `::test_stop_propagation`.
   - Justification: now reactives can write, decorator can
     introspect, and messages can route - enough for `Static` to
     work in isolation.

5. **`Binding` value type + merge.**
   - The dataclass-like value type from `_shims.dataclasses` plus
     `Binding._coerce` and the bindings-merge half of `@widget`.
   - Tested by `test_bindings.py::test_merged_bindings`.
   - Justification: small, no runtime dependency on the App loop
     yet (key dispatch lands in step 8).

6. **`DOMNode` + `Widget`.**
   - The class skeletons from §4.1-4.4 minus mount/unmount.
   - `refresh` is a no-op for now (returns without waking anything).
   - Justification: enables Phase 4c to start prototyping the
     compositor against a static tree before the loop is alive.

7. **`Screen` + `ScreenStack`.**
   - Including the Event-plus-slot `dismiss` pattern.
   - Tested by `test_screen.py::test_dismiss_returns_to_caller`.
   - Justification: required by `App` initial-screen mounting in
     step 9.

8. **Mount/unmount lifecycle + `Widget.refresh`.**
   - Wires the asyncio.Task per pump, the `_render_dirty` event,
     and the `_MountAwaitable`.
   - Tested by a synthetic mount/unmount fixture and the
     reactive watcher tests (which need a real refresh path).

9. **`App` skeleton + `run_async` task topology.**
   - Just the gather of `_pump_input`, `_pump_resize`, `_render`,
     `_exit_watcher`; the bodies of the first three are stubs
     ("await sleep, return") until Phase 5 widgets and Phase 4c
     compositor land.
   - Tested by `test_app_run.py::test_run_returns_exit`,
     `::test_exit_returns_value`, `::test_nested_app_raises`.

10. **Binding key dispatch.**
    - `_dispatch_key` from §6.3.
    - Tested by `test_bindings.py` extensions plus a Screen-level
      `tab` test.
    - Justification: ordered last because it depends on a mounted
      tree with a focused widget - which needs steps 6-9 alive.

This is ten steps over ~3500 LoC; the spec budgets 4-5 weeks for
Phase 4b.  Steps 1-5 are mechanical and can be a single PR.  Steps
6-8 are the inflection point - they require Phase 4c to be ready
to consume the dirty signal.  Steps 9-10 are the integration layer.

## 9. `MissingWidgetDecoratorError` diagnostics

The error fires from two sites: `Widget.__init__` (and `Screen.__init__`,
`App.__init__`) on first instantiation, and `@widget` itself when a
parent in the MRO declares capturable artifacts but lacks
`_tui_widget_meta` (FR-TUI-28).

Message text:

```
MissingWidgetDecoratorError: class MyScreen is missing @widget.

The class
    MyScreen
extends Widget but was not decorated with @widget, so its reactives,
@on-decorated handlers, BINDINGS, and compute_<name> methods are
unwired.  MicroPython does not invoke __init_subclass__ or
metaclasses, so the framework relies on the @widget decorator to
populate cls._tui_widget_meta exactly once at class-decoration time.

Fix:

    from picolet_tui import widget

    @widget
    class MyScreen(Screen):
        ...

See docs/tui/authoring-widgets.md, section "The @widget decorator".
```

For the FR-TUI-28 case (intermediate undecorated mixin in MRO), the
class name in the first line is the **base** class that is missing
the decorator, and a second sentence names the subclass that
triggered the check:

```
... but was not decorated with @widget ...
This was detected while decorating subclass MyButton.
```

The exception class itself lives in `picolet_tui.errors`
(FR-TUI-77).  `__init__(self, missing_cls, raised_from=None)` stores
both classes as attributes for programmatic inspection
(`exc.missing_cls`, `exc.raised_from`).

## 10. Upstream-Textual compatibility migration story

Note for `docs/tui/migration-from-textual.md` (Phase 6):

The picolet-tui surface is *Textual-shaped*, not Textual.  An app
written for upstream Textual moves to picolet-tui by applying the
checklist below.  The checklist is the migration doc's source.

| Upstream Textual                          | picolet-tui v0.1                    |
|-------------------------------------------|-------------------------------------|
| `class MyApp(App):`                       | `@widget` then `class MyApp(App):`  |
| `class MyWidget(Widget):`                 | `@widget` then `class MyWidget(Widget):` |
| `class MyMessage(Message):`               | `@widget` if it carries handlers; otherwise plain |
| `count = reactive(0)`                     | `count = Reactive(0)` (capitalised) |
| `count = var(0)`                          | not supported; use `Reactive(0, init=False)` |
| `@on(Button.Pressed)`                     | same import path, same decorator     |
| `def watch_count(self, old, new):`        | same                                |
| `def compute_total(self):`                | same                                |
| `BINDINGS = [...]`                        | same                                |
| `DEFAULT_CSS = "..."`                     | silently ignored; use `Style(...)` DSL |
| `App.run()`                               | works; new restriction: cannot be called from inside a running loop (FR-TUI-1) |
| `App.run_test()`                          | not in v0.1; use `TuiHarness` (Phase 7) |
| `App.animate(...)`                        | not in v0.1; D7 deferred to v0.2     |
| `asyncio.TaskGroup`-using app code        | works on Python 3.11+; the framework itself uses gather (D6) |
| `worker(...)` / `threading.Thread`        | not supported; single-thread (D6)    |
| `DataTable`, `Tree`, `TextArea`, ...       | not in v0.1; tracked for v0.2        |

The migration doc additionally enumerates each item in the v0.1
out-of-scope list (spec §0) with the v0.1 substitute or "deferred"
designation, per NFR-TUI-17.

---

Summary: 1116 lines at `/home/anl/picolet/docs/tui/textual-core-design.md`.
The seven most important decisions taken: (1) `@widget` re-merges
parent meta into per-class flat dicts rather than chain-walking at
dispatch time, trading a small memory cost for one-attribute-lookup
hot-path access; (2) `Reactive._bind_name(name, owner)` is the
explicit replacement for `__set_name__` and resolves
`watch_<name>` / `validate_<name>` siblings + their arities at
decoration time so per-write cost is one `getattr`; (3) MessagePump
uses `collections.deque` plus an `asyncio.Event` wake rather than
`asyncio.Queue`, matching pre-0.50 Textual's gather-based pump and
giving explicit oldest-drop overflow control at the 4096 cap;
(4) parent pointers are explicit strong refs with `_dispose()` at
unmount time rather than `weakref.ref`, eliminating "parent collected
mid-dispatch" failure modes at the cost of an explicit teardown
contract; (5) `Screen.dismiss` is implemented as `asyncio.Event` +
result slot rather than `asyncio.Future`, satisfying D6's MicroPython
asyncio gap without changing the user-visible `await screen.dismiss()`
shape; (6) `App.run_async` cancels via an `_exit_watcher` sibling task
inside the `asyncio.gather` rather than `TaskGroup`, pinning the
pre-0.50 Textual semantics D6 mandates; (7) the implementation order
in §8 lands `Reactive` and `@widget` first as leaves, then `Message`
+ `MessagePump`, then `Widget`/`Screen`/`App` as integration -
decoupling the highest-judgement pieces (decorator, MRO merge, R3
guard) from the integration layer so each can be tested and stabilised
in isolation.
