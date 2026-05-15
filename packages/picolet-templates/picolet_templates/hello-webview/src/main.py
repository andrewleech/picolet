# {{name}} — a minimal picolet webview app.
import picolet

# Pick the webview package matching the target. linux-x64 builds freeze
# picolet_ui (WebKitGTK 4.1); windows-x64 builds freeze picolet_ui_win
# (WebView2). The runtime variant determines which one is importable.
try:
    import picolet_ui as ui
except ImportError:
    import picolet_ui_win as ui


@picolet.command
async def greet(args):
    name = args.get("name", "World")
    return "Hello, " + name


@picolet.command
async def fail_example(args):
    raise ValueError("this is an example error")


def main():
    app = ui.Application()
    return app.run()


main()
