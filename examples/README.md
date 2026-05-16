# examples

The canonical examples for picolet apps are the project templates in
[`packages/picolet-templates/`](../packages/picolet-templates/).

Each template is a complete, runnable picolet app that can be generated via
`picolet init`:

- `hello-cli` — headless CLI app (no renderer)
- `hello-webview` — app using the GTK/WebKit webview renderer
- `hello-lvgl` — app using the LVGL renderer

To bootstrap a new app from a template:

```
picolet init hello-cli my-app
cd my-app
picolet build
picolet run
```

See [`packages/picolet-templates/README.md`](../packages/picolet-templates/README.md)
for details on the template layout and [`docs/v1-spec.md`](../docs/v1-spec.md)
for the full picolet application model.
