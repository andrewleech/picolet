<script setup lang="ts">
import { computed } from 'vue'
import { state } from '../store'
import TopStrip from '../components/TopStrip.vue'
import CpuChart from '../components/CpuChart.vue'
import MemoryGauge from '../components/MemoryGauge.vue'
import SparklineStrip from '../components/SparklineStrip.vue'
import NetworkChart from '../components/NetworkChart.vue'
import DiskChart from '../components/DiskChart.vue'
import ProcessList from '../components/ProcessList.vue'

const history = computed(() => state.history)
const latest = computed(() => state.latest)
const error = computed(() => state.error)
</script>

<template>
  <div class="dashboard-root">
    <!-- Error banner — shown when Python side reports a metrics:error. -->
    <div v-if="error" class="error-banner">
      METRICS ERROR: {{ error }}
    </div>

    <!-- Sticky header strip -->
    <TopStrip :latest="latest" />

    <!-- 12-column asymmetric grid — spec layout (F1 in PH22 aesthetic spec). -->
    <div class="dashboard-grid">
      <!-- CPU line chart: col 1–8, row 1–2 -->
      <CpuChart :history="history" />

      <!-- Memory radial gauge: col 9–12, row 1–2 -->
      <MemoryGauge :latest="latest" />

      <!-- Per-core sparkline strip: col 1–12, row 3 -->
      <SparklineStrip :history="history" />

      <!-- Network throughput: col 1–6, row 4 -->
      <NetworkChart :history="history" />

      <!-- Disk I/O: col 7–12, row 4 -->
      <DiskChart :history="history" />

      <!-- Process list: col 1–12, row 5–6 -->
      <ProcessList :latest="latest" />
    </div>
  </div>
</template>

<style scoped>
.dashboard-root {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
</style>
