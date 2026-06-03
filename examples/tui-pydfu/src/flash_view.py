# flash_view.py — FlashView: bottom half of the PyDfuApp UI.
#
# Carries an Input for the .dfu file path, a Flash button, a ProgressBar,
# and a status Static.  All three observable surfaces (progress, status,
# done/error) flow through Reactive attributes on this widget — the
# adapter's progress callback hops onto the asyncio loop with
# ``call_soon_threadsafe`` and writes to these reactives.  Watchers then
# refresh the dependent visual widgets.
#
# Replaces the webview app's IPC topics:
#
#   dfu:progress  ->  FlashView.progress = pct
#                     FlashView.status   = "Flashing 0x... done/total"
#   dfu:done      ->  FlashView.status   = "Done — N bytes."
#                     FlashView.flashing = False
#   dfu:error     ->  FlashView.status   = "Error: ..."
#                     FlashView.flashing = False
#
# Spec touch-points:
#   FR-TUI-13   @on(Button.Pressed) handlers for Flash / Abort.
#   FR-TUI-19   Reactives for device_id, dfu_path, progress, status,
#               flashing, total_bytes, done_bytes.
#   FR-TUI-20   watch_* methods update visible Static / ProgressBar state.
#   FR-TUI-43   Container hosts the vertical layout.
#   FR-TUI-47   Input(value=, placeholder=, max_length=) used for path.
#   FR-TUI-51   ProgressBar reactive progress slot.

import asyncio
import os

import pydfu_adapter as dfu

from picolet_tui import (
    Button,
    Container,
    Horizontal,
    Input,
    Label,
    ProgressBar,
    Reactive,
    Static,
    Vertical,
    on,
    widget,
)


# Default progress total used before a flash begins; ProgressBar requires
# total > 0, and the file size is unknown until read_dfu_file returns.
# We swap to the real total on flash start (see _begin_flash).
_INITIAL_TOTAL = 100


@widget
class FlashView(Container):
    """Bottom-half flash control panel for PyDfuApp.

    Why Reactive-driven state instead of direct widget mutation: the
    adapter's progress callback fires from a worker thread (see
    pydfu_adapter.flash_device — synchronous USB transfers wrapped in
    ``loop.run_in_executor``).  In the webview app the bridge was
    ``picolet.emit("dfu:progress", ...)`` which already hops the event
    onto the loop's task queue.  In the TUI variant we replicate that
    hop with ``loop.call_soon_threadsafe`` and route the resulting
    on-loop call into a Reactive write — which fires its watcher on the
    loop, which calls ProgressBar.refresh() / Static.update().  The
    widget code never sees the worker thread; the reactive surface is
    the thread boundary.

    State surface:
      device_id   : str | None — set by the parent when a device row is
                    activated.  watch_device_id reflects into the status
                    line.
      dfu_path    : str        — bound to the Input.value; the Flash
                    button reads this directly via Input.Changed handler.
      flashing    : bool       — True while a flash task is running.
                    Watcher disables the Flash button and surfaces the
                    Abort button.
      progress    : int        — bytes written so far; routed into the
                    ProgressBar's ``progress`` reactive.
      total_bytes : int        — total bytes to flash; used to scale the
                    ProgressBar's ``total``.
      status      : str        — human-readable status line.
    """

    # ---------------------------------------------------------------------
    # Reactive surface — see class docstring for invariants.
    # ---------------------------------------------------------------------

    device_id = Reactive(None)
    dfu_path = Reactive("")
    flashing = Reactive(False)
    progress = Reactive(0)
    total_bytes = Reactive(_INITIAL_TOTAL)
    status = Reactive("Select a device to flash.")

    def __init__(self, *, id=None, classes=""):
        # ---- visible children — kept on self so watchers can mutate ----
        #
        # Status header.  Static is the right vehicle because watch_status
        # calls update() to replace its content; FR-TUI-41 specifies that
        # path explicitly.
        self._status = Static("Select a device to flash.", id="flash-status")

        # File-path input.  placeholder, max_length per FR-TUI-47;
        # max_length is a defensive cap (POSIX PATH_MAX is 4096 on Linux,
        # 260 default on Windows).
        self._path_input = Input(
            value="",
            placeholder="path/to/firmware.dfu",
            max_length=4096,
            id="path-input",
        )

        # Action buttons.  variant="success" / "error" tints them in the
        # five-variant Button palette (FR-TUI-46).
        self._flash_btn = Button("Flash", id="flash", variant="success")
        self._abort_btn = Button("Abort", id="abort", variant="error")

        # Progress widget.  total is filled in on flash start; the
        # default of _INITIAL_TOTAL keeps the bar drawable before any
        # flash has begun (a zero-total bar would divide-by-zero in the
        # render path per ProgressBar.render's pct calc).
        self._progress = ProgressBar(
            total=_INITIAL_TOTAL,
            show_percentage=True,
            show_eta=True,
            id="flash-progress",
        )

        # ---- background flash task slot ----
        # Tracks the asyncio Task that runs the flash so abort_flash can
        # cancel it.  None when no flash is in flight; replaced on each
        # flash start.  We do NOT use a module-level global because
        # there may be more than one FlashView in a hypothetical future
        # multi-device dashboard variant — instance scope is the right
        # boundary even today.
        self._flash_task = None

        # Path-input label kept on self for symmetry with the other
        # children (so subclasses can re-style it without re-yielding).
        self._path_label = Label("File path:")

        # Button row Container, kept on self so test code can introspect
        # the structure via flash_view._button_row.
        self._button_row = Horizontal(self._flash_btn, self._abort_btn, id="flash-buttons")

        # Children passed positionally — compose() output is NOT
        # auto-mounted on non-root widgets in v0.1, so the canonical
        # mount path is *children -> _pending_children -> Widget._mount
        # drains.  See "v0.1 gaps" in the porting summary.
        Container.__init__(
            self,
            self._status,
            self._path_label,
            self._path_input,
            self._button_row,
            self._progress,
            id=id,
            classes=classes,
        )

    # ---------------------------------------------------------------------
    # Reactive watchers.
    # ---------------------------------------------------------------------

    def watch_device_id(self, old, new):
        """Surface the selected device id on the status line.

        The status reactive is the single point of truth for what the
        user sees; we update it here rather than writing to Static
        directly so that subsequent state transitions (flash start /
        progress / done) overwrite the same surface coherently.
        """
        if new is None:
            self.status = "Select a device to flash."
        elif not self.flashing:
            self.status = "Device {} ready; pick a .dfu file.".format(new)

    def watch_status(self, old, new):
        """Mirror the status reactive into the visible Static widget.

        Static.update() invalidates the renderable cache and schedules a
        refresh (FR-TUI-41).  Doing the indirection through a Reactive
        rather than calling _status.update() directly from every site
        keeps the reactive surface the canonical mutation point — tests
        watch FlashView.status, not the Static child.
        """
        self._status.update(new)

    def watch_progress(self, old, new):
        """Reflect bytes-written into the ProgressBar.

        ProgressBar exposes its own ``progress`` reactive (FR-TUI-51);
        writing to it triggers ProgressBar's own watcher which then
        invalidates the bar cache.  The two-hop chain
        (FlashView.progress -> ProgressBar.progress -> bar refresh) is
        deliberate: FlashView.progress is in "bytes" units while
        ProgressBar.progress is in "scale" units — they happen to share
        the same int representation here because we set the bar's total
        to total_bytes.
        """
        self._progress.progress = new

    def watch_total_bytes(self, old, new):
        """Re-scale the progress bar on flash start.

        ProgressBar accepts ``total`` as a reactive in v0.1 (per the
        widget's docstring deviation note); writing it triggers an
        internal refresh.  We avoid divide-by-zero by clamping to 1
        when the dfu file declares zero size — a degenerate case
        defensive against malformed firmware.
        """
        if new <= 0:
            new = 1
        self._progress.total = new

    def watch_flashing(self, old, new):
        """Drive button visibility from the flashing reactive.

        v0.1 has no Style display=none toggle, so we use Button.disabled
        semantics where available; in v0.1 there is no Button.disabled
        reactive either (the FR-TUI-46 table does not list one), so we
        leave both buttons mounted and rely on the Flash button's
        own handler to no-op when flashing is True.  When v0.2 ships
        Style toggles, this watcher gains a real implementation.

        The status reactive shifts to the operative phrase:
          flashing=True  -> "Flashing ..." (progress watcher refines)
          flashing=False -> resolved by watch_device_id default
        """
        # Empty-on-purpose — see docstring.  Kept declared so future
        # logic has the canonical site to grow into and so the
        # @widget MRO walk captures the watcher slot.
        pass

    # ---------------------------------------------------------------------
    # Input handlers — keep dfu_path in sync with the text field.
    # ---------------------------------------------------------------------

    @on(Input.Changed, "#path-input")
    def _on_path_changed(self, event):
        """Mirror Input.Changed into the dfu_path reactive.

        Why Reactive rather than reading from the Input on Flash press:
        a test that wants to drive the app programmatically can write
        ``flash_view.dfu_path = "/tmp/fw.dfu"`` and have the same code
        path execute as the keystroke flow would produce.  The Input is
        the canonical user-input surface; the reactive is the canonical
        programmatic surface (FR-TUI-19 design intent).
        """
        self.dfu_path = event.value

    # ---------------------------------------------------------------------
    # Button handlers.
    # ---------------------------------------------------------------------

    @on(Button.Pressed, "#flash")
    def _on_flash_pressed(self, event):
        """Start a flash operation.

        Replicates the webview app's @picolet.command flash handler
        (main.py around line 295) one-to-one — same precondition checks,
        same error sentinel for ".error.dfu", same async run_in_executor
        wiring.  The only differences are:
          (1) progress is written into self.progress rather than emitted
              via picolet.emit("dfu:progress", ...);
          (2) the function is sync (button handlers run on the loop
              thread, so we launch the worker via asyncio.create_task).
        """
        event.stop()

        if self.flashing:
            # Already in flight — ignore double-click rather than queue
            # a parallel flash (which would corrupt USB state).  No
            # status update because the user will see the running bar.
            return

        device_id = self.device_id
        dfu_path = self.dfu_path

        if not device_id:
            self.status = "Error: pick a device first."
            return
        if not dfu_path:
            self.status = "Error: enter a .dfu file path."
            return

        # Error sentinel path — matches the webview app exactly so the
        # same error-screenshot test fixture works under the TUI.
        if dfu_path.endswith(".error.dfu"):
            self.status = "Error: simulated flash error (sentinel path)."
            return

        # Pre-read the dfu file so a bad path / CRC error fails before
        # the worker task starts.  Returning early here also keeps the
        # flashing reactive False, so the watcher does not need a
        # rollback path.
        try:
            elements = dfu.read_dfu_file(dfu_path)
        except Exception as e:
            self.status = "Error: reading {}: {}".format(dfu_path, e)
            return
        if not elements:
            self.status = "Error: no elements in {}".format(dfu_path)
            return

        total = 0
        for elem in elements:
            total += elem.get("size", 0)
        if total <= 0:
            total = 1  # avoid divide-by-zero in ProgressBar.render

        # Update Reactives BEFORE creating the task so the on-loop
        # watchers fire in deterministic order: progress=0 first, then
        # total_bytes, then flashing=True.  The progress bar reflects
        # the new total before the first progress write arrives.
        self.progress = 0
        self.total_bytes = total
        self.status = "Flashing {} to {} ...".format(dfu_path, device_id)
        self.flashing = True

        # Schedule the worker.  We use create_task rather than ensure_future
        # because asyncio.ensure_future is one of the names the picolet
        # asyncio shim does not guarantee (NFR-TUI-9 subset).  The task is
        # stashed on self so abort_flash can cancel it.
        self._flash_task = asyncio.create_task(
            self._run_flash(device_id, dfu_path, elements, total)
        )

    @on(Button.Pressed, "#abort")
    def _on_abort_pressed(self, event):
        """Request flash cancellation.

        Cancellation here is two-step because the worker thread inside
        loop.run_in_executor cannot be killed: we cancel the asyncio
        Task (so the awaiter raises CancelledError when the executor
        returns) and we tell the adapter to send a DFU ABORT control
        request next time the inner loop checks.  In the mock path the
        adapter's abort_flash is a no-op; the cancellation alone is
        enough.
        """
        event.stop()
        if self._flash_task is not None and not self._flash_task.done():
            self._flash_task.cancel()
        dfu.abort_flash()

    # ---------------------------------------------------------------------
    # Worker.
    # ---------------------------------------------------------------------

    async def _run_flash(self, device_id, dfu_path, elements, total):
        """Body of the asyncio Task that drives pydfu_adapter.flash_device.

        Mirrors the webview app's _run() closure in main.py line ~329.
        Differences:
          * Progress is written via call_soon_threadsafe into a Reactive
            setter on self, not via picolet.emit("dfu:progress", ...).
          * Done / error states write self.status and self.flashing
            directly (which fires the buttons-visibility watcher).
        """
        loop = asyncio.get_event_loop()

        def _progress(addr, done, total):
            # Called from the worker thread.  We MUST NOT touch self
            # directly here — Reactive __set__ posts a Message into the
            # widget's pump, and the pump is single-threaded by design
            # (FR-TUI-54: no _thread, no threading).  Hop the call to
            # the loop, then run the reactive writes on-loop.
            loop.call_soon_threadsafe(self._on_progress, addr, done, total)

        try:
            await loop.run_in_executor(
                None,
                dfu.flash_device,
                device_id,
                elements,
                _progress,
            )
        except asyncio.CancelledError:
            self.status = "Flash cancelled."
            self.flashing = False
            return
        except Exception as e:
            self.status = "Error: {}".format(e)
            self.flashing = False
            return

        # Normal completion path.
        self.progress = total
        self.status = "Done — {} bytes written.".format(total)
        self.flashing = False
        self._flash_task = None

    def _on_progress(self, addr, done, total):
        """On-loop progress reflection.

        Lives on the loop thread (call_soon_threadsafe scheduled us); we
        can safely write to Reactive descriptors here.  Updating both
        self.progress and self.status keeps the user-visible state
        consistent: the bar position and the textual readout step
        together rather than drifting between frames.
        """
        if isinstance(addr, int):
            addr_str = "0x{:08X}".format(addr)
        else:
            addr_str = str(addr)
        # total may differ from self.total_bytes if the worker's view of
        # the element total drifts; we trust the worker's value because
        # it is computed from the same elements list we pre-totaled.
        self.progress = done
        self.status = "Flashing {}  {}/{} bytes".format(addr_str, done, total)
