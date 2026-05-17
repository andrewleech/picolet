<script setup lang="ts">
import { computed } from 'vue'
import { type Tick } from '../store'
import { toLinePath } from '../utils/svg'

const props = defineProps<{
  history: Tick[]
}>()

const SW = 88
const SH = 28

// Determine core count from the last tick.
const coreCount = computed(() => {
  const h = props.history
  const last = h.length > 0 ? h[h.length - 1] : undefined
  return last?.cores?.length ?? 0
})

// Per-core sample arrays — built from history.
const coreHistories = computed((): number[][] => {
  const count = coreCount.value
  if (count === 0) return []
  const result: number[][] = Array.from({ length: count }, () => [])
  for (const tick of props.history) {
    if (tick.cores?.length === count) {
      for (let i = 0; i < count; i++) {
        result[i].push(tick.cores[i])
      }
    }
  }
  return result
})

// SVG path per core.
const corePaths = computed(() =>
  coreHistories.value.map(vals => toLinePath(vals, 100, SW, SH))
)

// Colour per core: alternate between chart and chart-2.
function coreColor(i: number): string {
  return i % 2 === 0 ? 'var(--chart)' : 'var(--chart-2)'
}
</script>

<template>
  <div class="widget grid-sparklines">
    <div class="widget-label">PER-CORE CPU</div>

    <div v-if="coreCount === 0" style="color: var(--ink-dim); font-size: 12px;">
      Waiting for data…
    </div>

    <!-- F11: flex row of small SVGs, one per CPU core. -->
    <div v-else class="sparkline-row">
      <div
        v-for="(path, i) in corePaths"
        :key="i"
        class="sparkline-item"
      >
        <svg
          :width="SW"
          :height="SH"
          :viewBox="`0 0 ${SW} ${SH}`"
          preserveAspectRatio="none"
        >
          <path
            v-if="path"
            :d="path"
            :stroke="coreColor(i)"
            stroke-width="1"
            fill="none"
            class="chart-line"
          />
          <line :x1="0" :y1="SH" :x2="SW" :y2="SH" stroke="var(--bg-2)" stroke-width="0.5" />
        </svg>
        <span class="sparkline-core-label">C{{ i }}</span>
      </div>
    </div>
  </div>
</template>
