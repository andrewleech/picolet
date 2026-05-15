# {{name}} — a minimal picolet CLI app.
#
# Run it:
#   ./target/linux-x64/{{name}}
#
# The @picolet.command decorator registers IPC handlers.  On the cli
# runtime no transport is started automatically; these handlers are
# exercised when you add transport wiring.  The print below runs
# unconditionally so the app is immediately testable.
import picolet


@picolet.command
async def greet(args):
    name = args.get("name", "World")
    return "Hello, " + name


print("Hello from {{name}}")
