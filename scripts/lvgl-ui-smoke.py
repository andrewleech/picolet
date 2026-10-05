import asyncio
import ffi
import sys

import lvgl as lv

from picolet._test import snapshot
from picolet_ui._lvgl import LvglDisplay


WIDTH = 320
HEIGHT = 240
BACKGROUND = (18, 52, 86)
RECTANGLE = (240, 32, 64)
RECT_X = 40
RECT_Y = 50
RECT_WIDTH = 80
RECT_HEIGHT = 60


if len(sys.argv) != 2:
    raise SystemExit("usage: lvgl-ui-smoke.py OUTPUT.png")

output_path = sys.argv[1]
display = LvglDisplay("Picolet LVGL UI smoke", WIDTH, HEIGHT)
if display.display is None:
    raise RuntimeError("LvglDisplay did not create an SDL display")

# This fresh process creates one SDL window, whose initial window ID is 1.
sdl = ffi.open(None)
window = sdl.func("p", "SDL_GetWindowFromID", "I")(1)
flags = sdl.func("I", "SDL_GetWindowFlags", "p")(window)
title = sdl.func("s", "SDL_GetWindowTitle", "p")(window)
if title != "Picolet LVGL UI smoke" or not flags & 0x04 or flags & 0x48:
    raise RuntimeError("SDL window is not shown: title={} flags={}".format(title, flags))

screen = lv.screen_active()
screen.set_style_bg_color(lv.color_make(*BACKGROUND), lv.PART.MAIN)
screen.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)

rectangle = lv.obj(screen)
rectangle.set_pos(RECT_X, RECT_Y)
rectangle.set_size(RECT_WIDTH, RECT_HEIGHT)
rectangle.set_style_bg_color(lv.color_make(*RECTANGLE), lv.PART.MAIN)
rectangle.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)


async def render_frames():
    for _ in range(60):
        lv.tick_inc(5)
        lv.task_handler()
        await asyncio.sleep(0.005)


asyncio.run(render_frames())
png = snapshot()
with open(output_path, "wb") as output_file:
    output_file.write(png)

print(
    "PICOLET_LVGL_UI_SMOKE_OK output={} display={} dimensions={}x{} "
    "background_sample=(10,10):rgb{} rectangle_sample=(60,80):rgb{}".format(
        output_path,
        display.display,
        WIDTH,
        HEIGHT,
        BACKGROUND,
        RECTANGLE,
    )
)
