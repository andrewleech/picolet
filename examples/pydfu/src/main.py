# pydfu — DFU firmware flasher (picolet example).
#
# Registers five IPC commands consumed by the Vue frontend:
#   list_devices        -> list of DFU device dicts
#   read_dfu            -> parse a .dfu file, return elements list
#   get_memory_layout   -> memory segment list for a device
#   flash               -> start async flash task, emits dfu:progress / dfu:done / dfu:error
#   abort_flash         -> cancel the running flash task
#
# device_id convention: "<bus>:<addr>" string (e.g. "1:1"). O4 in phase plan.
import asyncio

import picolet
import picolet_ui as ui
import pydfu_adapter as dfu


@picolet.command
async def list_devices(args):
    """Enumerate USB DFU devices. Returns list of device dicts."""
    return dfu.list_dfu_devices()


@picolet.command
async def read_dfu(args):
    """Parse a .dfu file. Returns list of element dicts.

    args: {"path": "/path/to/firmware.dfu"} or a bare string path.
    """
    path = args.get("path") if isinstance(args, dict) else args
    if not path:
        return {"ok": False, "error": "no path provided"}
    return dfu.read_dfu_file(path)


@picolet.command
async def get_memory_layout(args):
    """Return memory layout segments for a device.

    args: {"device_id": "<bus>:<addr>"} or bare device_id string.
    """
    device_id = args.get("device_id") if isinstance(args, dict) else args
    return dfu.get_memory_layout(device_id)


_flash_task = None


@picolet.command
async def flash(args):
    """Start a DFU flash operation asynchronously.

    args: {"device_id": "<bus>:<addr>", "dfu_path": "/path/to/fw.dfu"}

    Returns immediately with {"ok": True, "status": "started"}.
    Progress events: dfu:progress {"addr", "done", "total", "pct"}
    Completion:      dfu:done    {"ok": True}
    Error:           dfu:error   {"message": str}

    Error sentinel: if dfu_path ends with ".error.dfu", a simulated error
    is emitted immediately (used by tests and mock flash error screenshots).
    """
    global _flash_task

    device_id = args.get("device_id") if isinstance(args, dict) else None
    dfu_path = args.get("dfu_path") if isinstance(args, dict) else None

    if not device_id or not dfu_path:
        return {"ok": False, "error": "device_id and dfu_path required"}

    # Error sentinel path — emit error immediately without reading file.
    if str(dfu_path).endswith(".error.dfu"):
        picolet.emit("dfu:error", {"message": "simulated flash error (sentinel path)"})
        return {"ok": False, "error": "simulated error"}

    try:
        elements = dfu.read_dfu_file(dfu_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not elements:
        return {"ok": False, "error": "no elements in dfu file"}

    async def _run():
        def _progress(addr, done, total):
            pct = (done * 100) // total if total else 0
            asyncio.get_event_loop().create_task(
                picolet.emit("dfu:progress", {
                    "addr": addr,
                    "done": done,
                    "total": total,
                    "pct": pct,
                })
            )

        try:
            dfu.flash_device(device_id, elements, _progress)
            picolet.emit("dfu:done", {"ok": True})
        except asyncio.CancelledError:
            picolet.emit("dfu:error", {"message": "flash cancelled"})
        except Exception as e:
            picolet.emit("dfu:error", {"message": str(e)})

    _flash_task = asyncio.create_task(_run())
    return {"ok": True, "status": "started"}


@picolet.command
async def abort_flash(args):
    """Abort the running flash task."""
    global _flash_task
    if _flash_task is not None and not _flash_task.done():
        _flash_task.cancel()
    dfu.abort_flash()
    return {"ok": True}


def main():
    app = ui.Application()
    app.run()


main()
