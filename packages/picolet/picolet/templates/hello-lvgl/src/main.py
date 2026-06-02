# {{name}} — a minimal picolet LVGL app.
#
# Demonstrates:
#   - creating an LVGL label with a greeting
#   - a button that increments a counter on tap
#   - the _lvgl_run entry point (reads [window] from /rom/picolet.toml)
#
# Build and run (Linux):
#   picolet build --target linux-x64
#   xvfb-run ./target/linux-x64/{{name}}
import asyncio

import lvgl as lv
from picolet_ui._lvgl import run as _lvgl_run

_counter = 0


async def main():
    scr = lv.screen_active()

    # Greeting label.
    label = lv.label(scr)
    label.set_text("Hello, {{name}}")
    label.align(lv.ALIGN.TOP_MID, 0, 40)

    # Counter label — updated on button tap.
    counter_label = lv.label(scr)
    counter_label.set_text("Taps: 0")
    counter_label.align(lv.ALIGN.CENTER, 0, 20)

    # Tap button.
    btn = lv.button(scr)
    btn.set_size(120, 50)
    btn.align(lv.ALIGN.CENTER, 0, 80)
    btn_label = lv.label(btn)
    btn_label.set_text("Tap me")
    btn_label.center()

    def on_tap(event):
        global _counter
        _counter += 1
        counter_label.set_text("Taps: {}".format(_counter))

    btn.add_event_cb(on_tap, lv.EVENT.CLICKED, None)

    # Keep the event loop alive.
    while True:
        await asyncio.sleep(0.1)


_lvgl_run(main=main)
