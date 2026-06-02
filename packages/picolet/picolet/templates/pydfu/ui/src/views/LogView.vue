<script setup lang="ts">
import { ref, inject, computed } from "vue";
import type { Ref } from "vue";
import type { LogEntry } from "../components/AuditStrip.vue";
import AuditStrip from "../components/AuditStrip.vue";

const logEntries = inject<Ref<LogEntry[]>>("logEntries")!;
const clearLog = inject<() => void>("clearLog")!;

const filterText = ref("");

const filtered = computed(() =>
  filterText.value
    ? logEntries.value.filter((e) =>
        e.message.toLowerCase().includes(filterText.value.toLowerCase()),
      )
    : logEntries.value,
);
</script>

<template>
  <div class="log-view reveal-main">
    <div class="log-toolbar">
      <input
        class="filter-input mono"
        type="text"
        v-model="filterText"
        placeholder="filter…"
      />
      <button class="btn" @click="clearLog">CLEAR</button>
    </div>
    <AuditStrip :entries="filtered" :expanded="true" />
  </div>
</template>

<style scoped>
.log-view {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.log-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule);
  background: var(--surface);
  flex-shrink: 0;
}

.filter-input {
  flex: 1;
  background: var(--chassis);
  border: 1px solid var(--rule);
  color: var(--text-pri);
  padding: 5px 8px;
  font-size: 12px;
  outline: none;
  border-radius: 0;
}

.filter-input:focus {
  border-color: var(--forge);
}
</style>
