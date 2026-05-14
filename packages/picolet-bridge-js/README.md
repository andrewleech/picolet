# picolet-bridge-js

The JavaScript shim loaded into the webview frontend that exposes the IPC
bridge as `window.picolet`.

```js
// Call a Python @picolet.command and await the result.
const result = await window.picolet.invoke('greet', { name: 'World' });

// Subscribe to push events from Python (picolet.emit on the Python side).
const unsub = window.picolet.on('progress', (data) => { /* ... */ });
unsub();
```

Built as an ES module + UMD bundle and packed into the runtime's romfs at
build time. The runtime auto-injects a `<script>` tag at document load to
load the bundle before the user's frontend code runs. Not yet implemented.

## Wire format

See [docs/architecture.md](../../docs/architecture.md#ipc-wire-format).
