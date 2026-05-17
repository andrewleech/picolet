<script setup lang="ts">
import { computed } from 'vue'
import { type Tick } from '../store'
import { toLinePath } from '../utils/svg'
import { fmtBytes } from '../utils/format'

const props = defineProps<{
  history: Tick[]
}>()

const W = 400
const H = 120

const rxValues = computed(() => props.history.map(t => t.net_rx_bps))
const txValues = computed(() => props.history.map(t => t.net_tx_bps))

// Shared Y axis — both series scale to the same maximum. F12 in PH22 plan.
const yMax = computed(() => {
  const all = [...rxValues.value, ...txValues.value]
  return Math.max(...all, 1)
})

const rxPath = computed(() => toLinePath(rxValues.value, yMax.value, W, H))
const txPath = computed(() => toLinePath(txValues.value, yMax.value, W, H))

const latest = computed(() => {
  const h = props.history
  return h.length > 0 ? h[h.length - 1] : null
})

const rxStr = computed(() => fmtBytes(latest.value?.net_rx_bps))
const txStr = computed(() => fmtBytes(latest.value?.net_tx_bps))
</script>

<template>
  <div class="widget grid-net">
    <div class="widget-label">NETWORK</div>

    <!-- Current values -->
    <div style="display: flex; gap: 16px; margin-bottom: 8px; align-items: baseline;">
      <div style="display: flex; gap: 6px; align-items: baseline;">
        <span style="font-family: var(--font-mono); font-size: 20px; font-variant-numeric: tabular-nums; color: var(--chart);">{{ rxStr }}</span>
        <span class="unit-label">RX</span>
      </div>
      <div style="display: flex; gap: 6px; align-items: baseline;">
        <span style="font-family: var(--font-mono); font-size: 20px; font-variant-numeric: tabular-nums; color: var(--chart-2);">{{ txStr }}</span>
        <span class="unit-label">TX</span>
      </div>
    </div>

    <!-- Dual-line SVG chart -->
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      :width="W"
      :height="H"
      class="chart-svg"
      preserveAspectRatio="none"
    >
      <line :x1="0" :y1="H" :x2="W" :y2="H" stroke="var(--bg-2)" stroke-width="1" />

      <path
        v-if="rxPath"
        :d="rxPath"
        stroke="var(--chart)"
        stroke-width="1.5"
        fill="none"
        class="chart-line"
      />
      <path
        v-if="txPath"
        :d="txPath"
        stroke="var(--chart-2)"
        stroke-width="1.5"
        fill="none"
        class="chart-line"
      />
    </svg>

    <!-- Legend -->
    <div class="legend">
      <div class="legend-item">
        <div class="legend-dot" style="background: var(--chart);"></div>
        RX
      </div>
      <div class="legend-item">
        <div class="legend-dot" style="background: var(--chart-2);"></div>
        TX
      </div>
    </div>
  </div>
</template>
