# PH09 asset-load-e2e fixture: CSS and JS sub-asset loading gate.
#
# Verifies that external CSS and JS files referenced by relative URLs in
# index.html are served correctly by the picolet:// URI scheme handler and
# applied by WebKit.
#
# Gate G1: PICOLET_PH09_CSS_OK
#   body background == rgb(51, 102, 153) (#336699), set by style.css.
# Gate G2: PICOLET_PH09_JS_OK
#   asset-check event received with from="app.js" (app.js executed).

import sys
import asyncio
import picolet
import picolet_ui


async def watcher():
    result_evt = asyncio.Event()

    def on_asset_check(data):
        bg = data.get("bg", "")
        from_field = data.get("from", "")

        if from_field == "app.js":
            print("PICOLET_PH09_JS_OK")
        else:
            sys.stderr.write(
                "asset-load-e2e: unexpected from field: {}\n".format(from_field)
            )

        # #336699 = rgb(51, 102, 153)
        if "51" in bg and "102" in bg and "153" in bg:
            print("PICOLET_PH09_CSS_OK")
        else:
            sys.stderr.write(
                "asset-load-e2e: unexpected background: {}\n".format(bg)
            )

        result_evt.set()

    picolet.on("asset-check", on_asset_check)

    try:
        await asyncio.wait_for(result_evt.wait(), 15.0)
    except asyncio.TimeoutError:
        sys.stderr.write("asset-load-e2e: timed out waiting for asset-check\n")
        sys.exit(1)

    sys.exit(0)


def main():
    app = picolet_ui.Application()
    return app.run(main=watcher)


main()
