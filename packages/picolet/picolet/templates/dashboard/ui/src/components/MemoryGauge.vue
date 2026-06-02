<script setup lang="ts">
import { computed } from 'vue'
import { type Tick } from '../store'
import { toGaugePath, toGaugeBgPath } from '../utils/svg'
import { fmtPct } from '../utils/format'

const props = defineProps<{
  latest: Tick | null
}>()

const CX = 100
const CY = 100
const R = 72

const pct = computed(() => props.latest?.mem_pct ?? 0)
const usedMb = computed(() => props.latest?.mem_used_mb ?? 0)
const totalMb = computed(() => props.latest?.mem_total_mb ?? 0)

const bgPath = computed(() => toGaugeBgPath(CX, CY, R))
const valPath = computed(() => toGaugePath(pct.value, CX, CY, R))

const pctStr = computed(() => props.latest ? fmtPct(pct.value) : '--')

const arcColor = computed(() => {
  const v = pct.value
  if (v > 95) return 'var(--alarm)'
  if (v > 85) return 'var(--accent)'
  return 'var(--chart)'
})

const usedStr = computed(() => {
  if (!props.latest) return '--'
  return `${Math.round(usedMb.value)} / ${Math.round(totalMb.value)} MiB`
})
</script>

<template>
  <div class="widget grid-mem" style="display: flex; flex-direction: column; align-items: center; justify-content: center;">
    <div class="widget-label" style="align-self: flex-start;">MEMORY</div>

    <!-- Radial gauge — F10 in PH22 plan. -->
    <svg
      width="200"
      height="200"
      viewBox="0 0 200 200"
      style="margin: 0 auto; display: block;"
    >
      <!-- Background arc — full 270° sweep at dim opacity -->
      <path
        v-if="bgPath"
        :d="bgPath"
        stroke="var(--bg-2)"
        stroke-width="10"
        fill="none"
        stroke-linecap="butt"
      />
      <!-- Value arc -->
      <path
        v-if="valPath"
        :d="valPath"
        :stroke="arcColor"
        stroke-width="10"
        fill="none"
        stroke-linecap="butt"
        class="chart-line"
      />

      <!-- Central numeral -->
      <text
        :x="CX"
        :y="CY - 6"
        text-anchor="middle"
        dominant-baseline="auto"
        :fill="arcColor"
        font-family="'JetBrains Mono', monospace"
        font-size="36"
        font-variant-numeric="tabular-nums slashed-zero"
      >{{ pctStr }}</text>

      <!-- % unit -->
      <text
        :x="CX"
        :y="CY + 18"
        text-anchor="middle"
        dominant-baseline="auto"
        fill="var(--ink-dim)"
        font-family="'DM Sans', system-ui, sans-serif"
        font-size="11"
        letter-spacing="2"
      >%</text>
    </svg>

    <!-- Used / Total label -->
    <div class="unit-label" style="margin-top: 4px; color: var(--ink-soft);">{{ usedStr }}</div>
  </div>
</template>
