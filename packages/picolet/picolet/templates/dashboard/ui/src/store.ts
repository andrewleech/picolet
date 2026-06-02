/**
 * store.ts — reactive shared state for the dashboard.
 *
 * Module-level reactive() — no Vuex/Pinia. All components import this
 * single state object. The metrics:tick handler in App.vue writes to it;
 * components read from it via computed() or direct template binding.
 */
import { reactive } from 'vue'

/** One sample from the Python metrics_reader.collect() return value. */
export interface Tick {
  ts: number
  cpu: number
  cores: number[]
  mem_pct: number
  mem_used_mb: number
  mem_total_mb: number
  load: [number, number, number]
  net_rx_bps: number
  net_tx_bps: number
  disk_read_bps: number
  disk_write_bps: number
  proc_count: number
  top_procs: { pid: number; name: string; cpu_pct: number }[]
  hostname: string
  uptime_s: number
}

export interface MetricsState {
  /** Up to 60 most recent ticks, oldest first. */
  history: Tick[]
  /** Most recent tick, or null before the first event arrives. */
  latest: Tick | null
  /** True after get_history() has returned (even if the list is empty). */
  initialized: boolean
  /** Set on metrics:error event. */
  error: string | null
}

export const state = reactive<MetricsState>({
  history: [],
  latest: null,
  initialized: false,
  error: null,
})
