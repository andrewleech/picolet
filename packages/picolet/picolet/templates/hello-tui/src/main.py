# {{name}} — minimal picolet-tui demo (FR-TUI-1, FR-TUI-13, FR-TUI-42,
# FR-TUI-46, FR-TUI-47).
#
# Demonstrates the three pillars of the v0.1 surface:
#   * @widget class registration (FR-TUI-57, synthesis D1)
#   * BINDINGS keymap with a quit action (FR-TUI-15, FR-TUI-1)
#   * @on(Message, selector) handler dispatch with bubbling (FR-TUI-13)
#
# Run it:
#   ./target/linux-x64/{{name}}
#
# Press q to quit; press Submit (or focus it with tab and press enter) to
# replace the label text.  The label uses Static.update() rather than
# direct reactive assignment so the example also exercises the
# update path (FR-TUI-41).
from picolet_tui import App, Binding, Button, Input, Label, on, widget


@widget
class HelloApp(App):
    """Demo app — quit binding plus a button that mutates the label.

    The class is decorated with @widget because it owns BINDINGS and an
    @on handler; FR-TUI-57 requires every class that contributes
    handlers, bindings, reactives, or computes to be class-registered
    via the decorator (no __init_subclass__, no metaclass).
    """

    BINDINGS = [
        Binding("q", "quit", "quit"),
    ]

    def compose(self):
        yield Label("Hello from picolet-tui!")
        yield Input(placeholder="type here...")
        yield Button("Submit", id="submit")

    @on(Button.Pressed, "#submit")
    def on_submit(self, event):
        # Replace the label text in-place.  query_one returns the first
        # matching widget; the Label is unique in this app so the bare
        # type query is unambiguous.
        self.query_one(Label).update("submitted!")


HelloApp().run()
