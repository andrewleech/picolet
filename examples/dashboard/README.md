# dashboard

A live system-metrics dashboard. 1 Hz background task reads `/proc/stat`,
`/proc/meminfo`, `/proc/net/dev`, etc., maintains a 60-sample sliding window,
and pushes `metrics:tick` events to the Vue frontend. The UI renders CPU
percentage, memory usage, network throughput, and load average as sparkline
charts.

The data-dense example — packed grid, narrow margins, generous use of
sparklines.

## Screenshots

| Full dashboard | With warning banner |
|---|---|
| ![](screenshots/full-dashboard.png) | ![](screenshots/full-dashboard-with-warning.png) |

| CPU pinned state | Network active |
|---|---|
| ![](screenshots/cpu-pinned-state.png) | ![](screenshots/network-active-state.png) |

## Picolet features exercised

- Background `asyncio.create_task` loop driven entirely from Python.
- `picolet.emit(event, payload)` push events — the UI is mostly passive,
  re-rendering on every tick.
- One `@picolet.command` (`get_history`) for the frontend's mount-time
  bootstrap so the first paint already shows 60 seconds of history.
- Graceful degradation: imports `metrics_reader` which raises
  `NotImplementedError` on non-Linux platforms, surfaced to the UI as a
  `metrics:error` event with a friendly banner instead of a crash.
- Sparkline rendering in pure SVG via Vue's reactive bindings — no
  charting library, no canvas.

## Built binary size

| Target | Size |
|---|---|
| `linux-x64` | **1.06 MiB** |

## Build

```bash
cd examples/dashboard
npm install
picolet build
./target/linux-x64/dashboard
```

## Platform notes

The metrics reader uses `/proc/*` exclusively, so the dashboard surfaces
real data only on **Linux**. On Windows the UI loads but every panel shows
"unavailable" — the example deliberately doesn't fall back to per-platform
shims, to keep the source small and to demonstrate the error-surface
pattern.

## Layout

```
dashboard/
├── picolet.toml
├── package.json
├── src/
│   ├── main.py             # asyncio metrics loop + IPC
│   └── metrics_reader.py   # /proc parsers
└── ui/src/
    ├── App.vue
    └── components/
        ├── CpuPanel.vue
        ├── MemoryPanel.vue
        ├── NetworkPanel.vue
        └── Sparkline.vue
```
