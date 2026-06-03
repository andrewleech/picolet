# {{name}}

Minimal picolet-tui starter app. Runs on the `picolet-runtime-*-tui`
binary and uses the frozen `picolet_tui` framework (Textual-inspired,
see `docs/tui/tui-v0.1-spec.md`).

## What it does

- Renders a `Label`, an `Input`, and a `Button` in the terminal alt-screen.
- Press **q** to quit (declared via `BINDINGS`).
- Press **Submit** (mouse click, or tab to focus and press enter) to
  replace the label text with `submitted!`.

The handler uses `@on(Button.Pressed, "#submit")` to filter by widget id
and `query_one(Label)` to mutate the label — the same dispatch and
query surface documented in FR-TUI-13 and FR-TUI-42.

## Build and run

```
picolet build --target linux-x64
./target/linux-x64/{{name}}
```

Cross-build for Windows:

```
picolet build --target windows-x64
```

The TUI refuses to start if stdin or stdout is not a tty (FR-TUI-10);
run from an interactive terminal or under the `TuiHarness` driver for
scripted tests.

## Source layout

```
src/
  main.py    — @widget App with BINDINGS, compose(), and an @on handler
```
