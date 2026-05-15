# picolet_ui._loop — asyncio + UI-pump integration.
#
# PH07.  Option C from the planner's D2: GTK pumped from an asyncio
# task on the asyncio thread.  No threading.  See the [PH07] Decision
# commit for the alternatives examined and rejected.
#
# The pump tick is tunable via `PUMP_INTERVAL_S` (module attribute);
# default 5 ms.  Apps that need lower latency or lower idle CPU can
# override before calling `run()`.
#
# PH11 adds `_lvgl_pump` alongside `_gtk_pump`.  The two pumps are
# mutually exclusive at runtime (one renderer per variant).  Selection
# happens in `picolet_ui.run()` via the `[ui] renderer` table in
# /rom/picolet.toml.

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

# LVGL tick advance per pump iteration, in milliseconds.  lv.tick_inc()
# accepts ms (not seconds); LVGL upstream documents 5 ms as the canonical
# task_handler interval.  Apps with animation requirements may lower
# this; apps that prioritise idle CPU may raise it.  Mismatches between
# the slept time and tick_inc produce animation glitches but not crashes.
LVGL_TICK_MS = 5


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


async def _lvgl_pump():
    """Advance LVGL's tick counter and drain its task queue.

    PH11.  Option C from the planner's D3: LVGL pumped from an asyncio
    task on the asyncio thread.  No threading.  Same pattern as the
    GTK pump but the call sequence is different:

      1. lv.tick_inc(LVGL_TICK_MS) — advance LVGL's monotonic counter
         by the amount we are about to sleep.  Required because the
         picolet runtime drives task_handler itself rather than relying
         on LVGL's optional pthread tick thread (which would require
         MICROPY_PY_THREAD=1 and LV_USE_OS != LV_OS_NONE).
      2. lv.task_handler() — drain LVGL's internal task queue.
         Handles animations, dirty-region redraw, and SDL2 input
         events the binding has marshalled into LVGL's input devices.
      3. asyncio.sleep(LVGL_TICK_MS / 1000) — yield to asyncio for the
         next batch of dispatcher work / inbound IPC messages.

    The SDL2 driver's event_loop hook fires inside task_handler;
    nothing else needs to drain SDL2.  If SDL2's queue floods, the
    pump is still bounded by the 5 ms sleep — animation glitches may
    appear but asyncio cannot starve indefinitely (mirrors PH07's
    GTK pump 32-iteration cap, less applicable here since task_handler
    is itself bounded by lv_timer_handler's internal logic).
    """
    import lvgl as lv  # local import: heavy C module; only the lvgl variant has it
    try:
        while True:
            lv.tick_inc(LVGL_TICK_MS)
            lv.task_handler()
            await asyncio.sleep(LVGL_TICK_MS / 1000.0)
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


async def _run_with_pump(transport, main, dispatcher_run, pump=None):
    """Race the dispatcher + pump.  Cancel both when either is done.

    dispatcher_run is picolet._dispatcher._run_with_main — passed in to
    avoid a hard import dependency at module load time (picolet might
    not be imported yet when picolet_ui is bare-imported).

    `pump` is the coroutine *function* (not a coroutine object) to use
    as the renderer pump.  Defaults to `_gtk_pump` for backwards
    compatibility with PH07 callers; PH11 callers pass `_lvgl_pump`
    explicitly via `picolet_ui.run()`.
    """
    pump_fn = pump if pump is not None else _gtk_pump
    pump_task = asyncio.create_task(pump_fn())
    try:
        return await dispatcher_run(transport, main)
    finally:
        pump_task.cancel()
        try:
            await pump_task
        except BaseException:
            pass


def run(transport, main=None, pump=None):
    """Enter the asyncio loop with both dispatcher and renderer pump.

    Mirrors `picolet.run` but adds the pump task alongside.  Used by user
    code as `picolet_ui.run(transport=WebviewTransport(...))` (webview)
    or `picolet_ui.run(transport=transport, pump=_lvgl_pump)` (lvgl).

    Default pump is `_gtk_pump` for PH07 backwards compatibility.
    """
    if not _HAVE_ASYNCIO:
        raise RuntimeError("picolet_ui.run requires asyncio")
    _maybe_take_threaded_branch()
    from picolet._dispatcher import _run_with_main
    return asyncio.run(_run_with_pump(transport, main, _run_with_main, pump))
