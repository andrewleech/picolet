# PH12 gate-6 fixture entry point (windows-x64).
#
# Standard lvgl-app shape: import picolet_ui, create a label, run pump
# for a few ticks, exit.  Mirrors PH11's hello-lvgl-min-e2e/src/main.py.
#
# The runtime auto-loads /rom/picolet.toml for [window] and [ui], opens
# the SDL2 window, calls lv.init(), creates the label, runs the pump
# until the test main coroutine exits.

import asyncio
import sys

import lvgl as lv
import picolet_ui


async def main():
    """Drive 30 task_handler ticks, then exit with the magic string."""
    scr = lv.screen_active()
    label = lv.label(scr)
    label.set_text("Hello, World")
    label.center()

    for _ in range(30):
        lv.tick_inc(5)
        lv.task_handler()
        await asyncio.sleep(0.005)

    text = label.get_text()
    print("PICOLET_LV_SANITY_OK size=800x600 label={}".format(
        text.replace(" ", "")
    ))
    sys.exit(0)


# Use _lvgl.run directly so we get pump=_lvgl_pump alongside the
# dispatcher.
from picolet_ui._lvgl import run as _lvgl_run
_lvgl_run(main=main)
