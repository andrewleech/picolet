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

const readValues = computed(() => props.history.map(t => t.disk_read_bps))
const writeValues = computed(() => props.history.map(t => t.disk_write_bps))

// Shared Y axis. F12 in PH22 plan.
const yMax = computed(() => {
  const all = [...readValues.value, ...writeValues.value]
  return Math.max(...all, 1)
})

const readPath = computed(() => toLinePath(readValues.value, yMax.value, W, H))
const writePath = computed(() => toLinePath(writeValues.value, yMax.value, W, H))

const latest = computed(() => {
  const h = props.history
  return h.length > 0 ? h[h.length - 1] : null
})

const readStr = computed(() => fmtBytes(latest.value?.disk_read_bps))
const writeStr = computed(() => fmtBytes(latest.value?.disk_write_bps))
</script>

<template>
  <div class="widget grid-disk">
    <div class="widget-label">DISK I/O</div>

    <!-- Current values -->
    <div style="display: flex; gap: 16px; margin-bottom: 8px; align-items: baseline;">
      <div style="display: flex; gap: 6px; align-items: baseline;">
        <span style="font-family: var(--font-mono); font-size: 20px; font-variant-numeric: tabular-nums; color: var(--chart);">{{ readStr }}</span>
        <span class="unit-label">READ</span>
      </div>
      <div style="display: flex; gap: 6px; align-items: baseline;">
        <span style="font-family: var(--font-mono); font-size: 20px; font-variant-numeric: tabular-nums; color: var(--chart-2);">{{ writeStr }}</span>
        <span class="unit-label">WRITE</span>
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
        v-if="readPath"
        :d="readPath"
        stroke="var(--chart)"
        stroke-width="1.5"
        fill="none"
        class="chart-line"
      />
      <path
        v-if="writePath"
        :d="writePath"
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
        READ
      </div>
      <div class="legend-item">
        <div class="legend-dot" style="background: var(--chart-2);"></div>
        WRITE
      </div>
    </div>
  </div>
</template>
