# picolet_ui_win._loop — asyncio + Win32 message-pump integration (PH10).
#
# Mirrors picolet_ui._loop structurally: a 5 ms-tick task that runs
# alongside the dispatcher.  Each tick:
#   1. Drains the C overlay's inbound ring buffer; feeds each JSON
#      string into the active transport's _deliver_raw().
#   2. Calls picolet_wv2_pump_messages() to dispatch any pending Win32
#      messages on the STA thread (this also fires the WebView2
#      WebMessageReceived handler when a postMessage is in flight).
#
# The pump task runs on the same asyncio thread as the dispatcher
# (Option C per AD4 — STA affinity).  WebView2's completion handlers
# fire from inside DispatchMessageW, which we call here.

import sys

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False


# Pump tick (seconds).  See AD4: 5 ms == 200 Hz, same as PH07's GTK pump.
# Tune by overriding picolet_ui_win.PUMP_INTERVAL_S before run().
PUMP_INTERVAL_S = 0.005


async def _win_pump(transport):
    """Drain inbound ring + pump Win32 messages forever.

    Cancelled when the dispatcher task exits.  We tolerate transport
    being None so the standalone-window probe (run_sanity_test) can
    re-use this code without a transport.
    """
    from . import _win_ffi
    try:
        while True:
            # 1. Drain the inbound ring.  Each pop yields a malloc'd C
            # string; we copy into Python str and immediately
            # picolet_wv2_free_inbound the original.
            for _ in range(64):  # per-tick cap to keep asyncio responsive
                ptr = _win_ffi.picolet_wv2_poll_inbound()
                if not ptr:
                    break
                try:
                    s = _win_ffi.ffi_string(ptr)
                finally:
                    _win_ffi.picolet_wv2_free_inbound(ptr)
                if transport is not None:
                    try:
                        transport._deliver_raw(s)
                    except BaseException as e:
                        sys.stderr.write(
                            "picolet_ui_win: _deliver_raw raised: {}\n".format(e)
                        )

            # 2. Pump Win32 messages.  WebMessageReceived handlers fire
            # from inside DispatchMessageW; pushing inbound straight into
            # the ring on the next iteration.
            try:
                _win_ffi.picolet_wv2_pump_messages()
            except BaseException as e:
                sys.stderr.write(
                    "picolet_ui_win: pump_messages raised: {}\n".format(e)
                )

            await asyncio.sleep(PUMP_INTERVAL_S)
    except asyncio.CancelledError:
        raise


async def _run_with_pump(transport, main, dispatcher_run):
    pump_task = asyncio.create_task(_win_pump(transport))
    try:
        return await dispatcher_run(transport, main)
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except BaseException:
            pass


def run(transport, main=None):
    """Enter asyncio.run() with the dispatcher and the Win32 pump."""
    if not _HAVE_ASYNCIO:
        raise RuntimeError("picolet_ui_win.run requires asyncio")
    from picolet._dispatcher import _run_with_main
    return asyncio.run(_run_with_pump(transport, main, _run_with_main))
