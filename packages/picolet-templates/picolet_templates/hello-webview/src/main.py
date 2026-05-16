# {{name}} — a minimal picolet webview app.
import picolet
import picolet_ui as ui


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
