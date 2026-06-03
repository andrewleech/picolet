# tui-pydfu — DFU firmware flasher rendered as a TUI (picolet example).
#
# The webview pydfu app at examples/pydfu/ ships the same USB / DFU code
# path; this directory swaps the Vue + HTML front end for a picolet_tui
# widget tree.  Everything below ``pydfu_adapter`` is byte-identical; the
# only differences live in this file, device_view.py, and flash_view.py.
#
# Compose hierarchy:
#
#   PyDfuApp (App)
#     └── Vertical
#           ├── DeviceListView (Container)   — top half: device list
#           └── FlashView      (Container)   — bottom half: file + bar
#
# IPC equivalence:
#
#   webview command          tui equivalent
#   ----------------------   ----------------------------------------
#   list_devices()           DeviceListView.refresh_devices()
#   read_dfu(path)           pydfu_adapter.read_dfu_file (pre-flash)
#   get_memory_layout(id)    pydfu_adapter.get_memory_layout
#   flash(id, path)          FlashView Flash button -> _begin_flash
#   abort_flash()            FlashView Abort button -> dfu.abort_flash
#
#   webview event topic      tui equivalent
#   ----------------------   ----------------------------------------
#   dfu:progress             FlashView.progress / status reactive writes
#   dfu:done                 FlashView.status / flashing reactive writes
#   dfu:error                FlashView.status / flashing reactive writes
#
# Spec touch-points:
#   FR-TUI-1, 6   App.run() entry; main.py-as-entry per picolet.toml.
#   FR-TUI-4      ctrl+q -> quit binding inherited from base App.
#   FR-TUI-12, 13 Bubbled DeviceSelected message handled with @on.
#   FR-TUI-50     ScreenStack not exercised — single-screen v0.1 app.
#   FR-TUI-56     Compatibility note: --mock / PICOLET_PYDFU_MOCK still
#                 toggles the mock adapter, identical to webview app.

import os
import sys

from picolet.romfs_extract import extract_dir

# ---------------------------------------------------------------------------
# Windows DLL extraction — runs BEFORE any module that imports _usb.core,
# because core.py calls ffi.open at module load time and Windows LoadLibrary
# cannot read from the MicroPython romfs VFS.  Identical to the webview
# main.py prologue; no behavioural change.
# ---------------------------------------------------------------------------

_native_lib_dir = extract_dir("/rom/src/_usb", subdir="picolet_pydfu")
if _native_lib_dir != "/rom/src/_usb":
    import _usb
    _usb._native_lib_dir = _native_lib_dir

# ---------------------------------------------------------------------------
# Pre-import argv scan: --mock has to be processed before pydfu_adapter
# is imported, because the adapter's module-level mock initialisation
# reads PICOLET_PYDFU_MOCK at import time (see pydfu_adapter.py line ~37).
# ---------------------------------------------------------------------------

if "--mock" in sys.argv:
    os.putenv("PICOLET_PYDFU_MOCK", "1")

# Now safe to import the adapter and the TUI surface.
from picolet_tui import App, Binding, Vertical, on, widget

from device_view import DeviceListView, DeviceSelected
from flash_view import FlashView


# ---------------------------------------------------------------------------
# PyDfuApp.
# ---------------------------------------------------------------------------


@widget
class PyDfuApp(App):
    """Top-level TUI app for tui-pydfu.

    Owns one DeviceListView and one FlashView, vertically stacked.  The
    only handler at this level is the DeviceSelected bubble — when a row
    in DeviceListView is activated, we forward the selection into
    FlashView.device_id, which kicks the flash workflow into the
    "ready" state.

    Why a flat single-screen App rather than push_screen of two screens:
    the device list and the flash form must be visible simultaneously
    in v0.1 (the user needs to see which device they picked while
    entering the path).  push_screen renders only the active screen
    (FR-TUI-50 Stack semantics); a Vertical of two Containers is the
    right primitive.

    BINDINGS extends the base App's ctrl+q quit (FR-TUI-4) with a 'q'
    shorthand for terminal users who do not have Ctrl-Q free (some
    terminal emulators reserve it for XON/XOFF flow control).  The MRO
    merge in @widget preserves the inherited ctrl+q entry.
    """

    BINDINGS = [
        Binding("q", "quit", "quit"),
    ]

    def __init__(self):
        # Build the two child views eagerly.  Holding them on self lets
        # the DeviceSelected handler write to FlashView.device_id without
        # a child lookup walk (no query_one in v0.1 — see "v0.1 gaps"
        # in the summary).
        self._device_view = DeviceListView(id="devices")
        self._flash_view = FlashView(id="flash")
        App.__init__(self)

    def compose(self):
        # Single root: a Vertical wrapping the two views.  The compose
        # contract for App is "yield Screen-shaped widgets" (FR-TUI-50);
        # a Container at the root counts as a screen-shaped tree because
        # App._mount_initial_screen does not require the yielded widget
        # to be a Screen instance — any Widget tree works in v0.1.
        yield Vertical(self._device_view, self._flash_view, id="root")

    # ---------------------------------------------------------------------
    # Cross-view wiring.
    # ---------------------------------------------------------------------

    @on(DeviceSelected)
    def _on_device_selected(self, event):
        """Route a DeviceListView.Selected bubble into FlashView state.

        This is the entire integration surface between the two views;
        keeping it here (rather than letting FlashView observe
        DeviceListView directly) preserves the parent-mediates-children
        invariant the @on dispatch walk relies on (FR-TUI-12 §3.4).
        """
        # Reactive write — FlashView.watch_device_id reflects the change
        # into the visible status line.
        self._flash_view.device_id = event.device_id
        event.stop()


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def main():
    # No CLI subcommand routing in this TUI variant — the webview app's
    # `pydfu list / read / flash` subcommands are intentionally absent
    # here.  The CLI surface lives in the cli-only variant (examples/
    # pydfu/) so each example exercises one renderer cleanly; the TUI
    # app's job is to validate the picolet-tui surface end-to-end, not
    # to multiplex with the CLI flow.
    #
    # If a future merge folds the two examples back together, the CLI
    # router from examples/pydfu/src/main.py drops in unchanged before
    # App.run().
    app = PyDfuApp()
    app.run()


main()
