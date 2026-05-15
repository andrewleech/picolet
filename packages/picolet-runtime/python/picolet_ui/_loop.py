# picolet_ui._loop — asyncio + GMainLoop integration.
#
# PH07.  Option C from the planner's D2: GTK pumped from an asyncio
# task on the asyncio thread.  No threading.  See the [PH07] Decision
# commit for the alternatives examined and rejected.
#
# The pump tick is tunable via `PUMP_INTERVAL_S` (module attribute);
# default 5 ms.  Apps that need lower latency or lower idle CPU can
# override before calling `run()`.

import os
import sys

try:
    import asyncio
    _HAVE_ASYNCIO = True
except ImportError:
    asyncio = None
    _HAVE_ASYNCIO = False


# Default pump interval (seconds).  See [PH07] Decision: GTK pumped
# from asyncio task at 5ms tick.
PUMP_INTERVAL_S = 0.005


async def _gtk_pump():
    """Drain pending GTK events; yield to asyncio when idle.

    Runs alongside the picolet dispatcher.  When script-message callbacks
    fire from inside gtk_main_iteration_do, they execute synchronously
    in our task (modffi call_py_func_with_lock holds the scheduler
    lock for the duration of the Python call).  They append to the
    transport's _inbox and set _recv_event; the next asyncio tick
    resumes the dispatcher.
    """
    from . import _gtk_ffi
    try:
        while True:
            # Drain everything queued so we don't fall behind on UI events.
            # Cap the per-tick drain to 32 iterations to avoid starving
            # asyncio under a flood (e.g. animation timers).
            n = 0
            while _gtk_ffi.gtk_events_pending() and n < 32:
                _gtk_ffi.gtk_main_iteration_do(0)
                n += 1
            await asyncio.sleep(PUMP_INTERVAL_S)
    except asyncio.CancelledError:
        raise


def _worker_thread_pump_stub():
    """Worker-thread fallback (Option B) — gated behind PICOLET_WV_THREADED=1.

    PH07 ships only the gated-error message.  Implementation is
    deferred until gate 16 reveals starvation in the same-thread pump.
    """
    raise NotImplementedError(
        "picolet_ui: PICOLET_WV_THREADED=1 selects the worker-thread GTK "
        "pump (Option B), which is not implemented in PH07.  Unset the "
        "env var to use the default same-thread pump (Option C, 5 ms tick), "
        "or implement Option B in a follow-up phase.  See the [PH07] "
        "Decision commit body for rationale."
    )


def _maybe_take_threaded_branch():
    """Honour PICOLET_WV_THREADED=1.  Returns silently otherwise."""
    try:
        flag = os.environ.get("PICOLET_WV_THREADED")
    except (AttributeError, NotImplementedError):
        # MicroPython os.environ may be incomplete on some ports.
        flag = None
    if flag == "1":
        _worker_thread_pump_stub()


async def _run_with_pump(transport, main, dispatcher_run):
    """Race the dispatcher + pump.  Cancel both when either is done.

    dispatcher_run is picolet._dispatcher._run_with_main — passed in to
    avoid a hard import dependency at module load time (picolet might
    not be imported yet when picolet_ui is bare-imported).
    """
    pump_task = asyncio.create_task(_gtk_pump())
    try:
        return await dispatcher_run(transport, main)
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except BaseException:
            pass


def run(transport, main=None):
    """Enter the asyncio loop with both dispatcher and GTK pump.

    Mirrors `picolet.run` but adds the pump task alongside.  Used by user
    code as `picolet_ui.run(transport=WebviewTransport(...))`.
    """
    if not _HAVE_ASYNCIO:
        raise RuntimeError("picolet_ui.run requires asyncio")
    _maybe_take_threaded_branch()
    from picolet._dispatcher import _run_with_main
    return asyncio.run(_run_with_pump(transport, main, _run_with_main))
