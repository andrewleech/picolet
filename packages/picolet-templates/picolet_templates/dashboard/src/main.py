# dashboard — live system-metrics dashboard (picolet example).
#
# Python side: 1 Hz asyncio task reads /proc sources, maintains a 60-sample
# circular history, and pushes metrics:tick events.
#
# IPC commands:
#   get_history()  -> {"history": [...]}  # bootstrap the frontend on mount
#
# Events pushed:
#   metrics:tick   payload (see metrics_reader.collect() docstring)
#   metrics:error  {"message": str}       # on non-Linux or read failure
#
# FR-EX-4, FR-EX-5, FR-EX-6.
import asyncio
import picolet
import picolet_ui as ui

# Import metrics_reader — raises NotImplementedError on non-Linux (F8).
try:
    import metrics_reader
    _HAS_METRICS = True
    _METRICS_ERROR = ""
except NotImplementedError as _e:
    _HAS_METRICS = False
    _METRICS_ERROR = str(_e)

_history: list = []
_HISTORY_MAX = 60
_prev: dict = {}


@picolet.command
async def get_history(args):
    """Return the current 60-sample history for frontend bootstrap."""
    return {"history": _history}


async def _metrics_loop():
    """1 Hz background loop that reads /proc and pushes metrics:tick events."""
    global _prev

    if not _HAS_METRICS:
        picolet.emit("metrics:error", {"message": _METRICS_ERROR})
        return

    while True:
        await asyncio.sleep(1.0)
        try:
            tick, _prev = metrics_reader.collect(_prev)
        except Exception as e:
            picolet.emit("metrics:error", {"message": str(e)})
            continue
        if tick is not None:
            _history.append(tick)
            if len(_history) > _HISTORY_MAX:
                _history.pop(0)
            picolet.emit("metrics:tick", tick)


def main():
    # Schedule the metrics loop into the event loop before app.run() starts
    # driving it. In MicroPython's asyncio, create_task() before the loop
    # starts queues the coroutine for execution when the loop runs. This is
    # the same pattern used by pydfu/src/main.py. See R2 in PH22 plan.
    loop = asyncio.get_event_loop()
    loop.create_task(_metrics_loop())
    app = ui.Application()
    app.run()


main()
