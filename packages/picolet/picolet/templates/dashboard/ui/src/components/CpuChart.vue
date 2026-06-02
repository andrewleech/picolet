<script setup lang="ts">
import { computed } from 'vue'
import { type Tick } from '../store'
import { toLinePath, toAreaPath } from '../utils/svg'
import { fmtPct } from '../utils/format'

const props = defineProps<{
  history: Tick[]
}>()

const W = 600
const H = 160

const cpuValues = computed(() => props.history.map(t => t.cpu))

const linePath = computed(() => toLinePath(cpuValues.value, 100, W, H))
const areaPath = computed(() => toAreaPath(cpuValues.value, 100, W, H))

const current = computed(() => {
  const h = props.history
  return h.length > 0 ? h[h.length - 1].cpu : null
})

const currentStr = computed(() => current.value != null ? fmtPct(current.value) : '--')

const numClass = computed(() => {
  const v = current.value
  if (v == null) return 'big-num'
  if (v > 95) return 'big-num alarm'
  if (v > 85) return 'big-num warn'
  return 'big-num'
})

const strokeColor = computed(() => {
  const v = current.value
  if (v == null) return 'var(--chart)'
  if (v > 95) return 'var(--alarm)'
  if (v > 85) return 'var(--accent)'
  return 'var(--chart)'
})
</script>

<template>
  <div class="widget grid-cpu">
    <div class="widget-label">CPU USAGE</div>

    <!-- Current value overlay -->
    <div style="display: flex; align-items: baseline; gap: 6px; margin-bottom: 8px;">
      <span :class="numClass" class="cpu-value">{{ currentStr }}</span>
      <span class="unit-label">%</span>
    </div>

    <!-- SVG line chart — hand-rolled, no chart library. F9 in PH22 plan. -->
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      :width="W"
      :height="H"
      class="chart-svg"
      preserveAspectRatio="none"
    >
      <!-- Subtle area fill at 6% opacity -->
      <path
        v-if="areaPath"
        :d="areaPath"
        :fill="strokeColor"
        fill-opacity="0.06"
        class="chart-area"
      />
      <!-- Crisp 1.5px line stroke -->
      <path
        v-if="linePath"
        :d="linePath"
        :stroke="strokeColor"
        stroke-width="1.5"
        fill="none"
        class="chart-line"
      />
      <!-- Baseline rule -->
      <line :x1="0" :y1="H" :x2="W" :y2="H" stroke="var(--bg-2)" stroke-width="1" />
      <!-- 85% warning threshold marker -->
      <line
        :x1="0" :y1="H * 0.15" :x2="W" :y2="H * 0.15"
        stroke="var(--accent)" stroke-width="0.5" stroke-dasharray="4 4" opacity="0.4"
      />
    </svg>
  </div>
</template>
