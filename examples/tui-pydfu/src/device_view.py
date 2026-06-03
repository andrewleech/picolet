# device_view.py — DeviceListView: shows enumerated DFU devices, lets
# the user pick one, posts a Selected message when the user activates a
# row.
#
# This is the "list_devices()" surface of the webview app rendered as
# widgets instead of HTML.  No IPC layer is involved — pydfu_adapter
# returns the device dicts in-process and we build a column of Label +
# Button rows.  Selection bubbles up as DeviceListView.Selected; the
# parent (PyDfuApp) translates that into a Reactive write on FlashView.
#
# Spec touch-points:
#   FR-TUI-13   @on(Button.Pressed, "#device-NN") routes activation by id.
#   FR-TUI-19   ``devices`` Reactive holds the raw list; watch_devices
#               rebuilds the row layout when refresh() is requested.
#   FR-TUI-43   Container hosts the row stack; Vertical sequences rows.
#   FR-TUI-45   Horizontal arranges per-row label + button side by side.
#   FR-TUI-52   id / classes accepted through Widget.__init__.
#   FR-TUI-57   @widget decorator on every Container/Message-owning class.

import pydfu_adapter as dfu

# Widget base classes and decorators.  All names come from the public
# picolet_tui surface so this module reads like upstream Textual idiom.
from picolet_tui import (
    Button,
    Container,
    Horizontal,
    Label,
    Message,
    Reactive,
    Static,
    Vertical,
    on,
    widget,
)


# ---------------------------------------------------------------------------
# Why a custom Message:
# Bubbling a typed event lets the parent dispatch in one @on handler
# instead of brittle id matching against every per-row Button.Pressed.
# Message has no Reactives / handlers of its own, so per FR-TUI-28 it
# does NOT need the @widget decorator (the rule applies only to classes
# that own capturable artifacts).
# ---------------------------------------------------------------------------


class DeviceSelected(Message):
    """Bubbled when the user activates one of the device rows.

    Carries the canonical "<bus>:<addr>" id string and the device dict so
    the parent does not need to re-enumerate.  Routed through the
    standard MessagePump.post_message walk (FR-TUI-12).
    """

    def __init__(self, device_id, device):
        # Message.__init__ takes no args in v0.1; we store on self.
        Message.__init__(self)
        self.device_id = device_id
        self.device = device


# ---------------------------------------------------------------------------
# DeviceListView.
# ---------------------------------------------------------------------------


@widget
class DeviceListView(Container):
    """Top half of the PyDfuApp UI: enumerated DFU device list.

    Why a Container (not Vertical): the row stack is vertical, but the
    *view* may include a header line ("Detected DFU devices:") followed
    by the row stack.  Putting both into a generic Container and letting
    the inner ``Vertical`` carry the row direction keeps the header
    placement orthogonal to the row layout — FR-TUI-43 vs FR-TUI-44.

    devices Reactive: stores the raw list returned by
    ``pydfu_adapter.list_dfu_devices()``.  Mutation triggers
    ``watch_devices``, which clears the row container and rebuilds it.
    The list is a plain Python list; equality of two list values compares
    element-wise, so a re-scan that yields the same devices does NOT
    fire watch_devices unnecessarily (FR-TUI-19 default ``init=True``
    semantics).
    """

    # Reactive declaration.  ``layout=True`` would force a layout pass on
    # every write; we use layout=False because the row Container itself
    # is recreated, and Widget.mount drives the layout invalidation
    # downstream.  ``init=True`` (the FR-TUI-19 default) means the
    # initial assignment in __init__ fires watch_devices once during
    # compose so the view starts populated.
    devices = Reactive([])

    # selected_id Reactive: the currently highlighted device id, or None.
    # Watched by the parent indirectly (we post DeviceSelected as the
    # primary mutation signal); kept here for tests / introspection.
    selected_id = Reactive(None)

    def __init__(self, *, id=None, classes=""):
        # Header label is created once and kept on self so refresh paths
        # do not re-create it (Static carries its own dirty bit).
        self._header = Static("Detected DFU devices:", id="device-header")

        # Row container — Vertical so children stack top-to-bottom in
        # FR-TUI-44's row-axis direction.  Per-row widgets are mounted
        # into _row_box dynamically from watch_devices.
        self._row_box = Vertical(id="device-rows")

        # Empty-state Static.  Shown only when devices is [].  Kept on
        # self because watch_devices toggles its content.
        self._empty = Static("(none — connect a DFU-mode device or rerun with --mock)", id="device-empty")

        # Refresh button.  Bubbles Button.Pressed("#refresh"); the parent
        # PyDfuApp catches it and calls self.refresh_devices() (which
        # rewrites self.devices and triggers watch_devices).
        self._refresh = Button("Refresh", id="refresh", variant="primary")

        # Forward children positionally so Widget.__init__ stages them
        # on _pending_children and Widget._mount drains them on first
        # mount.  This is the documented mount path in v0.1; compose()
        # output on non-root widgets is NOT auto-mounted (see
        # "v0.1 gaps" in the summary — only App.compose drives mounting,
        # via App._mount_initial_screen / ScreenStack.push).  Passing
        # the children here makes the mount work regardless.
        Container.__init__(
            self,
            self._header,
            self._row_box,
            self._empty,
            self._refresh,
            id=id,
            classes=classes,
        )

    # ---------------------------------------------------------------------
    # Reactive watchers.
    # ---------------------------------------------------------------------

    def watch_devices(self, old, new):
        """Rebuild the row stack when the device list changes.

        Why rebuild rather than diff: the device count in real-world
        operation is single-digit; recreating four Button + Label widgets
        on every refresh is well below the FR-TUI-37 render budget.  The
        v0.1 widget API has no reorder primitive, so a diff path would
        amount to "remove all + mount all" with extra bookkeeping.

        Async lifecycle: Widget.remove() is async (FR-TUI-24) and
        Widget.mount is async (FR-TUI-23).  Watchers are sync per
        FR-TUI-20, so we wrap the swap in a coroutine and schedule it
        via ``asyncio.create_task``.  Pre-mount writes (before the
        loop is up — e.g. the init=True initial assignment from
        Reactive's constructor) hit the early-out branch and stage the
        new rows on ``_pending_children`` for Widget._mount to drain on
        first mount.
        """
        # Lazy import — keeps the device_view module importable in
        # contexts where the freezer walks Python statically without an
        # asyncio loop available.
        import asyncio

        # Build the new row widgets up front.  This is cheap (each row
        # is one Horizontal + Label + Button) and lets the swap
        # coroutine reference a closed-over list rather than recomputing
        # from `new` after the await points.
        new_rows = [self._build_row(d) for d in new]

        # Toggle empty-state visibility.  Static.update is sync; doing it
        # here (not in the async swap) means the placeholder text flips
        # in the same render frame the rows do.
        if new:
            self._empty.update(" ")
        else:
            self._empty.update("(none — connect a DFU-mode device or rerun with --mock)")

        # Pre-mount fast path: Reactive's init=True default fires
        # watch_devices once during __init__ (after the descriptor is
        # bound, before App.run_async starts the pump).  At that point
        # the row container has no live children and no running loop,
        # so we stage rows on _pending_children and let the standard
        # Widget._mount drain do the work.
        if not self._row_box._mounted:
            self._row_box._pending_children.extend(new_rows)
            return

        # Live path: post-mount Refresh.  Schedule an async swap.
        async def _swap_rows():
            # Tear down old rows depth-first.  Iterate over a snapshot
            # because remove() mutates _mounted_children.
            for child in list(self._row_box._mounted_children):
                await child.remove()
            # Mount the new rows.  mount() accepts *children and links
            # them in iteration order; the awaitable resolves after the
            # last on_mount fires.
            if new_rows:
                await self._row_box.mount(*new_rows)

        asyncio.create_task(_swap_rows())

    def _build_row(self, d):
        """Construct a single device row.

        Layout per row:  [VID:PID Manufacturer Product  ]  [Select]

        Why the id namespace ``device-<bus>:<addr>``: the @on selector
        machinery (FR-TUI-13) matches on the rendered widget id; pinning
        the id to the device's canonical "<bus>:<addr>" string means the
        Button.Pressed handler can recover the device id from
        ``event.button.id`` without a side table.
        """
        device_id = d.get("id") or "{}:{}".format(d["bus"], d["addr"])

        # Single-line description.  We render VID/PID as 4-hex and put
        # manufacturer / product after them; the 80-cell baseline (see
        # picolet.toml) is wide enough for the worst-case STMicro string.
        descr = "{:>5}  {:04X}:{:04X}  {}  {}".format(
            device_id,
            d.get("vid", 0),
            d.get("pid", 0),
            d.get("manufacturer", ""),
            d.get("product", ""),
        )

        label = Label(descr)
        select = Button("Select", id="device-" + device_id, variant="primary")
        # Horizontal so the select button sits to the right of the label.
        # FR-TUI-45 specifies left-to-right ordering of children.
        return Horizontal(label, select, id="device-row-" + device_id)

    # ---------------------------------------------------------------------
    # Event handlers.
    # ---------------------------------------------------------------------

    @on(Button.Pressed)
    def _on_any_button(self, event):
        """Catch every Button.Pressed in our subtree.

        The @on decorator with no selector matches every Button.Pressed
        the message-pump walk routes to us (FR-TUI-13).  We route by
        button id rather than by selector because (a) the v0.1 selector
        grammar is "#id" / ".class" only, no compound selectors, and
        (b) doing the dispatch here keeps the bubbling target singular
        instead of forcing the parent app to register one @on per
        device id.

        The "refresh" id routes back through the parent; we re-enumerate
        in-place rather than emitting a Refresh message because pydfu_adapter
        is a sync call and the resulting state is local to this view.
        """
        btn = event.button
        if btn.id == "refresh":
            self.refresh_devices()
            event.stop()
            return

        if btn.id and btn.id.startswith("device-"):
            device_id = btn.id[len("device-"):]
            # Look up the matching device dict so the bubbled message
            # carries the full record, not just the id string.
            chosen = None
            for d in self.devices:
                d_id = d.get("id") or "{}:{}".format(d["bus"], d["addr"])
                if d_id == device_id:
                    chosen = d
                    break
            self.selected_id = device_id
            self.post_message(DeviceSelected(device_id, chosen))
            event.stop()

    # ---------------------------------------------------------------------
    # Public API.
    # ---------------------------------------------------------------------

    def refresh_devices(self):
        """Re-enumerate DFU devices and update the Reactive.

        Called from on_mount (initial population) and from the Refresh
        button handler.  The adapter call is synchronous and fast
        (USB enumeration completes in <50 ms on the mock path; the real
        path is dominated by libusb's own poll); running it on the
        asyncio loop without an executor is acceptable for v0.1.  If
        real-hardware enumeration ever exceeds NFR-TUI-11's 16 ms input
        budget, this should be moved to ``loop.run_in_executor``.
        """
        self.devices = dfu.list_dfu_devices()

    async def on_mount(self):
        """Populate the device list on first mount (FR-TUI-25)."""
        # The Reactive assignment fires watch_devices, which mounts the
        # row widgets onto self._row_box.  We do not need to await the
        # mount completion here — the pump will pick up the pending
        # children on its next tick.
        self.refresh_devices()
