<script setup lang="ts">
import { computed } from 'vue'
import { type Tick } from '../store'
import { fmtUptime, fmtLoad } from '../utils/format'

const props = defineProps<{
  latest: Tick | null
}>()

const hostname = computed(() => props.latest?.hostname ?? '—')
const uptime = computed(() => fmtUptime(props.latest?.uptime_s))
const load1 = computed(() => fmtLoad(props.latest?.load[0]))
const load5 = computed(() => fmtLoad(props.latest?.load[1]))
const load15 = computed(() => fmtLoad(props.latest?.load[2]))
</script>

<template>
  <div class="top-strip">
    <span class="hostname">{{ hostname }}</span>

    <div class="metric-group">
      <span class="metric-label">UPTIME</span>
      <span class="metric-value">{{ uptime }}</span>
    </div>

    <div class="metric-group">
      <span class="metric-label">LOAD</span>
      <span class="metric-value">{{ load1 }}</span>
      <span class="metric-label" style="margin-left: 4px;">{{ load5 }}</span>
      <span class="metric-label" style="margin-left: 4px;">{{ load15 }}</span>
    </div>
  </div>
</template>
