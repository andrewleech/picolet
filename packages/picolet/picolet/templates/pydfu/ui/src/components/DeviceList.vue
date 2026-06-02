<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import LedDot from "./LedDot.vue";

export interface DfuDevice {
  bus: number;
  addr: number;
  vid: number;
  pid: number;
  manufacturer?: string;
  product?: string;
  id: string; // "<bus>:<addr>"
}

defineProps<{
  selectedId?: string;
}>();

const emit = defineEmits<{
  "select-device": [device: DfuDevice];
}>();

const devices = ref<DfuDevice[]>([]);
let interval: ReturnType<typeof setInterval> | null = null;

async function refresh() {
  try {
    const raw = (await window.picolet.invoke("list_devices")) as Array<{
      bus: number;
      addr: number;
      vid: number;
      pid: number;
      manufacturer?: string;
      product?: string;
    }>;
    devices.value = raw.map((d) => ({
      ...d,
      id: `${d.bus}:${d.addr}`,
    }));
  } catch {
    // Swallow errors between polls — device may transiently disappear.
  }
}

onMounted(() => {
  refresh();
  interval = setInterval(refresh, 500);
});

onUnmounted(() => {
  if (interval !== null) clearInterval(interval);
});

function hexPad(n: number, width: number): string {
  return n.toString(16).padStart(width, "0");
}
</script>

<template>
  <div class="device-list">
    <div class="list-header">
      <span class="section-title">DFU Devices</span>
      <span class="device-count mono">{{ devices.length }} found</span>
    </div>
    <div class="list-body">
      <div
        v-if="devices.length === 0"
        class="empty-state mono"
      >
        — NO DFU DEVICES —
      </div>
      <div
        v-for="dev in devices"
        :key="dev.id"
        class="device-row"
        :class="{ selected: dev.id === selectedId }"
        @click="emit('select-device', dev)"
      >
        <LedDot :status="dev.id === selectedId ? 'ok' : 'idle'" />
        <div class="device-info">
          <span class="device-vid-pid mono"
            >{{ hexPad(dev.vid, 4) }}:{{ hexPad(dev.pid, 4) }}</span
          >
          <span class="device-loc mono">BUS {{ dev.bus }} DEV {{ dev.addr }}</span>
          <span class="device-name" v-if="dev.product">{{ dev.product }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.device-list {
  display: flex;
  flex-direction: column;
  width: 40%;
  border-right: 1px solid var(--rule);
  background: var(--surface);
}

.list-header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.device-count {
  font-size: 11px;
  color: var(--text-sec);
}

.list-body {
  flex: 1;
  overflow-y: auto;
}

.list-body::-webkit-scrollbar {
  width: 4px;
}

.list-body::-webkit-scrollbar-thumb {
  background: var(--rule);
}

.empty-state {
  padding: 20px 12px;
  color: var(--text-sec);
  font-size: 11px;
  letter-spacing: 0.1em;
}

.device-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--rule);
  cursor: pointer;
  transition: background 80ms;
}

.device-row:hover {
  background: rgba(255, 107, 26, 0.05);
}

.device-row.selected {
  background: rgba(255, 107, 26, 0.1);
  border-left: 2px solid var(--forge);
}

.device-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.device-vid-pid {
  font-size: 13px;
  color: var(--text-pri);
  letter-spacing: 0.06em;
}

.device-loc {
  font-size: 10px;
  color: var(--text-sec);
  letter-spacing: 0.08em;
}

.device-name {
  font-size: 11px;
  color: var(--text-sec);
  font-family: var(--font-body);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
