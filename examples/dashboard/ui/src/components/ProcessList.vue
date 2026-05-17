<script setup lang="ts">
import { computed } from 'vue'
import { type Tick } from '../store'

const props = defineProps<{
  latest: Tick | null
}>()

const procCount = computed(() => props.latest?.proc_count ?? 0)
const topProcs = computed(() => props.latest?.top_procs ?? [])
</script>

<template>
  <div class="widget grid-procs">
    <div class="widget-label">PROCESSES</div>

    <div style="display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px;">
      <span
        style="font-family: var(--font-mono); font-size: 42px; font-variant-numeric: tabular-nums; color: var(--ink);"
      >{{ procCount }}</span>
      <span class="unit-label">TOTAL</span>
    </div>

    <table class="proc-table" v-if="topProcs.length > 0">
      <thead>
        <tr>
          <th style="width: 60px;">PID</th>
          <th>NAME</th>
          <th style="width: 80px; text-align: right;">CPU %</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="p in topProcs"
          :key="p.pid"
          class="proc-row"
        >
          <td>{{ p.pid }}</td>
          <td>{{ p.name }}</td>
          <td
            class="cpu-col"
            style="text-align: right;"
            :class="{ high: p.cpu_pct > 50 }"
          >{{ p.cpu_pct.toFixed(1) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-else style="color: var(--ink-dim); font-size: 12px; margin-top: 4px;">
      Waiting for data…
    </div>
  </div>
</template>
