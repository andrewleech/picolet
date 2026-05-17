<script setup lang="ts">
import { ref, watch } from "vue";
import type { DfuDevice } from "./DeviceList.vue";

const props = defineProps<{
  device?: DfuDevice;
}>();

interface MemSegment {
  addr: number;
  last_addr: number;
  size: number;
  num_pages: number;
  page_size: number;
}

const memLayout = ref<MemSegment[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

watch(
  () => props.device,
  async (dev) => {
    if (!dev) {
      memLayout.value = [];
      error.value = null;
      return;
    }
    loading.value = true;
    error.value = null;
    try {
      const layout = (await window.picolet.invoke("get_memory_layout", {
        device_id: dev.id,
      })) as MemSegment[];
      memLayout.value = layout;
    } catch (e) {
      error.value = String(e);
      memLayout.value = [];
    } finally {
      loading.value = false;
    }
  },
);

function hexAddr(n: number): string {
  return "0x" + n.toString(16).padStart(8, "0").toUpperCase();
}

function fmtSize(n: number): string {
  if (n >= 1048576) return (n / 1048576).toFixed(0) + " MiB";
  if (n >= 1024) return (n / 1024).toFixed(0) + " KiB";
  return n + " B";
}
</script>

<template>
  <div class="device-detail">
    <div class="detail-header">
      <span class="section-title">Device Detail</span>
    </div>

    <div v-if="!device" class="no-device mono">
      — SELECT A DEVICE —
    </div>

    <div v-else class="detail-body">
      <!-- Device identity table -->
      <div class="detail-section">
        <div class="section-title" style="padding: 8px 12px 4px">Identity</div>
        <table class="detail-table mono">
          <tbody>
            <tr>
              <td class="cell-label">VID:PID</td>
              <td class="cell-value">
                {{ device.vid.toString(16).padStart(4, "0") }}:{{
                  device.pid.toString(16).padStart(4, "0")
                }}
              </td>
            </tr>
            <tr>
              <td class="cell-label">Bus/Addr</td>
              <td class="cell-value">{{ device.bus }} / {{ device.addr }}</td>
            </tr>
            <tr v-if="device.manufacturer">
              <td class="cell-label">Vendor</td>
              <td class="cell-value">{{ device.manufacturer }}</td>
            </tr>
            <tr v-if="device.product">
              <td class="cell-label">Product</td>
              <td class="cell-value">{{ device.product }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Memory layout -->
      <div class="detail-section">
        <div class="section-title" style="padding: 8px 12px 4px">Memory Layout</div>
        <div v-if="loading" class="loading-note mono">loading…</div>
        <div v-else-if="error" class="error-note mono">{{ error }}</div>
        <div v-else-if="memLayout.length === 0" class="loading-note mono">—</div>
        <table v-else class="detail-table mono">
          <thead>
            <tr>
              <th class="cell-label">Start</th>
              <th class="cell-label">End</th>
              <th class="cell-label">Pages</th>
              <th class="cell-label">Size</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(seg, i) in memLayout" :key="i">
              <td class="cell-value">{{ hexAddr(seg.addr) }}</td>
              <td class="cell-value">{{ hexAddr(seg.last_addr) }}</td>
              <td class="cell-value">{{ seg.num_pages }} × {{ fmtSize(seg.page_size) }}</td>
              <td class="cell-value">{{ fmtSize(seg.size) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.device-detail {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}

.detail-header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule);
  flex-shrink: 0;
}

.no-device {
  padding: 20px 12px;
  color: var(--text-sec);
  font-size: 11px;
  letter-spacing: 0.1em;
}

.detail-body {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 12px;
}

.detail-body::-webkit-scrollbar {
  width: 4px;
}

.detail-body::-webkit-scrollbar-thumb {
  background: var(--rule);
}

.detail-section {
  border-bottom: 1px solid var(--rule);
  padding-bottom: 8px;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.detail-table th,
.detail-table td {
  padding: 3px 12px;
  text-align: left;
}

.cell-label {
  color: var(--text-sec);
  white-space: nowrap;
  width: 90px;
  font-size: 10px;
  letter-spacing: 0.06em;
}

.cell-value {
  color: var(--text-pri);
}

.loading-note,
.error-note {
  padding: 6px 12px;
  font-size: 11px;
}

.error-note {
  color: var(--led-alarm);
}

.loading-note {
  color: var(--text-sec);
}
</style>
