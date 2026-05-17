<script setup lang="ts">
import { ref, watch, nextTick } from "vue";

export interface LogEntry {
  ts: number;
  message: string;
}

const props = defineProps<{
  entries: LogEntry[];
  expanded?: boolean;
}>();

const scrollEl = ref<HTMLElement | null>(null);

watch(
  () => props.entries.length,
  async () => {
    await nextTick();
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
    }
  },
);

function formatTs(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}
</script>

<template>
  <div class="audit-strip" :class="{ expanded }">
    <div class="strip-header">
      <span class="section-title">Audit Log</span>
    </div>
    <div class="strip-log" ref="scrollEl">
      <div v-if="entries.length === 0" class="log-empty">— no events —</div>
      <div v-for="(entry, i) in entries" :key="i" class="log-line">
        <span class="log-ts">{{ formatTs(entry.ts) }}</span>
        <span class="log-msg">{{ entry.message }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.audit-strip {
  height: 120px;
  background: #050708;
  border-top: 1px solid var(--rule);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.audit-strip.expanded {
  flex: 1;
  height: auto;
}

.strip-header {
  padding: 4px 10px;
  border-bottom: 1px solid var(--rule);
  flex-shrink: 0;
}

.strip-log {
  flex: 1;
  overflow-y: auto;
  padding: 4px 10px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--led-ok);
  line-height: 1.6;
}

.strip-log::-webkit-scrollbar {
  width: 4px;
}

.strip-log::-webkit-scrollbar-track {
  background: transparent;
}

.strip-log::-webkit-scrollbar-thumb {
  background: var(--rule);
}

.log-empty {
  color: var(--text-sec);
  font-style: italic;
}

.log-line {
  display: flex;
  gap: 10px;
  white-space: pre;
}

.log-ts {
  color: var(--text-sec);
  flex-shrink: 0;
}

.log-msg {
  color: var(--led-ok);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
