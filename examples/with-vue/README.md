# with-vue

The minimal Picolet + Vue 3 + Vite + TypeScript example. Two IPC handlers,
one push-event stream, one Vue component — every primitive the bridge exposes,
nothing more.

Intended as a stripped-down starting point for new apps. The four other examples
(`notes`, `config-editor`, `dashboard`, `pydfu`) build on the same shape.

## What it does

- `ping(ts) -> {pong: ts}` — round-trip latency check.
- `get_info() -> {platform, python, uname}` — Python-side runtime info surfaced
  to the Vue UI.
- A background asyncio task emits `clock:tick` every second; the UI subscribes
  via `window.picolet.on(...)`.

## Picolet features exercised

- `@picolet.command` — register Python coroutines as JS-callable RPCs.
- `picolet.emit(event, payload)` — push events from Python to JS.
- `window.picolet.invoke(name, args)` — JS-side RPC client.
- `window.picolet.on(event, handler)` — JS-side event subscriber.
- Vue + Vite + TypeScript via the `[ui.frontend] framework = "vue"` table —
  `picolet dev` runs Vite dev server, `picolet build` runs `npm run build`
  and folds the output into the romfs.

## Built binary size

| Target | Size |
|---|---|
| `linux-x64` | **798 KiB** |

## Build

```bash
cd examples/with-vue
npm install
picolet build
./target/linux-x64/with-vue
```

Live dev loop with hot-reload of Vue + Python:

```bash
picolet dev
```

## Layout

```
with-vue/
├── picolet.toml        # [app], [ui], [ui.frontend], [window]
├── package.json        # Vue 3, Vite, TypeScript
├── src/main.py         # picolet.command handlers + emit loop
└── ui/src/
    ├── main.ts         # Vue mount + window.picolet wiring
    └── App.vue         # single-component UI
```
