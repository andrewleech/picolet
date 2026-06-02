<script setup lang="ts">
import { ref, inject, onMounted, onUnmounted } from "vue";
import type { Ref } from "vue";
import type { DfuDevice } from "../components/DeviceList.vue";
import LedDot from "../components/LedDot.vue";

const selectedDevice = inject<Ref<DfuDevice | undefined>>("selectedDevice");
const addLog = inject<(msg: string) => void>("addLog")!;

interface DfuElement {
  num: number;
  addr: number;
  size: number;
}

const dfuPath = ref<string>(localStorage.getItem("pydfu-last-path") || "");
const elements = ref<DfuElement[] | null>(null);
const readError = ref<string | null>(null);
const readLoading = ref(false);

type FlashStatus = "idle" | "running" | "done" | "error";
const flashStatus = ref<FlashStatus>("idle");
const flashPct = ref(0);
const flashError = ref<string | null>(null);
const flashDone = ref(false);

let unsubProgress: (() => void) | null = null;
let unsubDone: (() => void) | null = null;
let unsubError: (() => void) | null = null;

onMounted(() => {
  unsubProgress = window.picolet.on("dfu:progress", (data) => {
    const d = data as { addr: number; done: number; total: number; pct: number };
    flashPct.value = d.pct;
    addLog(`DFU progress: ${d.pct}% (${d.done}/${d.total} bytes @ 0x${d.addr.toString(16)})`);
  });
  unsubDone = window.picolet.on("dfu:done", () => {
    flashStatus.value = "done";
    flashDone.value = true;
    addLog("DFU flash complete.");
  });
  unsubError = window.picolet.on("dfu:error", (data) => {
    const d = data as { message: string };
    flashStatus.value = "error";
    flashError.value = d.message;
    addLog(`DFU error: ${d.message}`);
  });
});

onUnmounted(() => {
  unsubProgress?.();
  unsubDone?.();
  unsubError?.();
});

async function readDfu() {
  if (!dfuPath.value.trim()) return;
  readLoading.value = true;
  readError.value = null;
  elements.value = null;
  try {
    const result = (await window.picolet.invoke("read_dfu", {
      path: dfuPath.value.trim(),
    })) as DfuElement[];
    elements.value = result;
    localStorage.setItem("pydfu-last-path", dfuPath.value.trim());
    addLog(`DFU file parsed: ${result.length} element(s) from ${dfuPath.value.trim()}`);
  } catch (e) {
    readError.value = String(e);
    addLog(`DFU read error: ${e}`);
  } finally {
    readLoading.value = false;
  }
}

async function startFlash() {
  if (!selectedDevice?.value) {
    addLog("Flash failed: no device selected");
    return;
  }
  if (!elements.value) return;
  flashStatus.value = "running";
  flashPct.value = 0;
  flashDone.value = false;
  flashError.value = null;
  addLog(`Starting flash → device ${selectedDevice.value.id}, file ${dfuPath.value}`);
  try {
    await window.picolet.invoke("flash", {
      device_id: selectedDevice.value.id,
      dfu_path: dfuPath.value.trim(),
    });
  } catch (e) {
    flashStatus.value = "error";
    flashError.value = String(e);
    addLog(`Flash invoke error: ${e}`);
  }
}

async function abortFlash() {
  addLog("Aborting flash…");
  try {
    await window.picolet.invoke("abort_flash");
    flashStatus.value = "idle";
  } catch (e) {
    addLog(`Abort error: ${e}`);
  }
}

function hexAddr(n: number): string {
  return "0x" + n.toString(16).padStart(8, "0").toUpperCase();
}

function fmtSize(n: number): string {
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MiB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KiB";
  return n + " B";
}
</script>

<template>
  <div class="flash-view reveal-main">
    <!-- Left pane: file input + controls -->
    <div class="flash-left">
      <div class="pane-header">
        <span class="section-title">Firmware File</span>
      </div>

      <div class="pane-body">
        <!-- Device selection notice -->
        <div class="field-group">
          <label class="field-label section-title">Target Device</label>
          <div class="device-notice mono" v-if="selectedDevice">
            <LedDot status="ok" />
            <span>{{ selectedDevice.vid.toString(16).padStart(4,"0") }}:{{ selectedDevice.pid.toString(16).padStart(4,"0") }} ({{ selectedDevice.id }})</span>
          </div>
          <div class="device-notice mono warn" v-else>
            <LedDot status="warn" />
            <span>no device selected — go to DEVICES tab</span>
          </div>
        </div>

        <!-- Path input or file picker -->
        <div class="field-group">
          <label class="field-label section-title">DFU File Path</label>
          <div class="path-row">
            <input
              class="path-input mono"
              type="text"
              v-model="dfuPath"
              placeholder="/path/to/firmware.dfu"
              @keydown.enter="readDfu"
            />
          </div>
          <div class="file-picker-row">
            <label class="btn btn-read-dfu file-label" for="dfu-file-picker">BROWSE</label>
            <input
              id="dfu-file-picker"
              type="file"
              accept=".dfu"
              style="display: none"
              @change="(e) => {
                const f = (e.target as HTMLInputElement).files?.[0];
                if (f) dfuPath = (f as unknown as Record<string, string>).path ?? f.name;
              }"
            />
          </div>
        </div>

        <div class="btn-row">
          <button class="btn btn-read-dfu" @click="readDfu" :disabled="readLoading || !dfuPath.trim()">
            {{ readLoading ? "READING…" : "READ FILE" }}
          </button>
        </div>

        <div v-if="readError" class="read-error mono">ERROR: {{ readError }}</div>

        <!-- Flash controls -->
        <div class="flash-controls" v-if="elements">
          <div class="btn-row">
            <button
              class="btn btn-flash btn-primary"
              @click="startFlash"
              :disabled="flashStatus === 'running' || !selectedDevice"
            >
              FLASH
            </button>
            <button
              class="btn btn-danger"
              @click="abortFlash"
              :disabled="flashStatus !== 'running'"
            >
              ABORT
            </button>
          </div>
        </div>

        <!-- Progress bar -->
        <div class="progress-section" v-if="flashStatus !== 'idle'">
          <div class="progress-label section-title">
            Flash Progress — {{ flashPct }}%
          </div>
          <div class="progress-track">
            <div
              class="progress-bar"
              :style="{ width: flashPct + '%' }"
              :class="{ error: flashStatus === 'error' }"
            />
          </div>
          <div
            class="flash-status-done mono"
            v-if="flashStatus === 'done'"
          >
            ✓ FLASH COMPLETE
          </div>
          <div
            class="flash-status-error mono"
            v-if="flashStatus === 'error'"
          >
            ✗ ERROR: {{ flashError }}
          </div>
        </div>
      </div>
    </div>

    <!-- Right pane: DFU descriptor -->
    <div class="flash-right pane-divider">
      <div class="pane-header">
        <span class="section-title">DFU Descriptor</span>
      </div>
      <div class="pane-body">
        <div v-if="!elements" class="no-data mono">— load a DFU file —</div>
        <table v-else class="dfu-elements-table mono">
          <thead>
            <tr>
              <th class="th-cell">#</th>
              <th class="th-cell">Address</th>
              <th class="th-cell">Size</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="elem in elements" :key="elem.num">
              <td class="td-cell">{{ elem.num }}</td>
              <td class="td-cell">{{ hexAddr(elem.addr) }}</td>
              <td class="td-cell">{{ fmtSize(elem.size) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.flash-view {
  display: flex;
  flex: 1;
  min-height: 0;
}

.flash-left {
  width: 45%;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border-right: 1px solid var(--rule);
}

.flash-right {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.pane-header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule);
  flex-shrink: 0;
}

.pane-body {
  flex: 1;
  padding: 12px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pane-body::-webkit-scrollbar {
  width: 4px;
}

.pane-body::-webkit-scrollbar-thumb {
  background: var(--rule);
}

.field-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  display: block;
}

.device-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-pri);
  padding: 4px 0;
}

.device-notice.warn {
  color: var(--led-warn);
}

.path-row {
  display: flex;
  gap: 6px;
}

.path-input {
  flex: 1;
  background: var(--chassis);
  border: 1px solid var(--rule);
  color: var(--text-pri);
  padding: 6px 8px;
  font-size: 12px;
  outline: none;
  border-radius: 0;
}

.path-input:focus {
  border-color: var(--forge);
}

.file-picker-row {
  display: flex;
  gap: 6px;
}

.file-label {
  display: inline-block;
}

.btn-row {
  display: flex;
  gap: 8px;
}

.read-error {
  color: var(--led-alarm);
  font-size: 11px;
  padding: 4px 0;
}

.flash-controls {
  border-top: 1px solid var(--rule);
  padding-top: 12px;
}

.progress-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.progress-track {
  height: 6px;
  background: var(--rule);
  border: 1px solid #2a3040;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--forge);
  transition: width 120ms ease-out;
}

.progress-bar.error {
  background: var(--led-alarm);
}

.flash-status-done {
  font-size: 12px;
  color: var(--led-ok);
  letter-spacing: 0.1em;
}

.flash-status-error {
  font-size: 12px;
  color: var(--led-alarm);
  letter-spacing: 0.1em;
}

.no-data {
  color: var(--text-sec);
  font-size: 11px;
  padding: 8px 0;
  letter-spacing: 0.08em;
}

.dfu-elements-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.th-cell {
  text-align: left;
  color: var(--text-sec);
  font-weight: 400;
  padding: 4px 8px 4px 0;
  border-bottom: 1px solid var(--rule);
  letter-spacing: 0.06em;
  font-size: 10px;
}

.td-cell {
  padding: 4px 8px 4px 0;
  color: var(--text-pri);
  border-bottom: 1px solid var(--rule);
}
</style>
