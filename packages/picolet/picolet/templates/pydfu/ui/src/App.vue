<script setup lang="ts">
import { ref, provide, onMounted, onUnmounted, useTemplateRef } from "vue";
import { RouterView, useRoute } from "vue-router";
import HeaderRail from "./components/HeaderRail.vue";
import AuditStrip from "./components/AuditStrip.vue";
import type { LogEntry } from "./components/AuditStrip.vue";
import type { DfuDevice } from "./components/DeviceList.vue";

const route = useRoute();

// Screenshot mode — disable all CSS animations (NFR-EX-5 / R7)
const root = useTemplateRef<HTMLElement>("root");
onMounted(() => {
  if ((window as unknown as Record<string, unknown>).__PICOLET_SCREENSHOT_MODE__) {
    document.documentElement.classList.add("no-animation");
  }
});

// Shared state: selected DFU device
const selectedDevice = ref<DfuDevice | undefined>(undefined);
provide("selectedDevice", selectedDevice);
provide("setSelectedDevice", (d: DfuDevice) => {
  selectedDevice.value = d;
  addLog(`Device selected: ${d.vid.toString(16).padStart(4,"0")}:${d.pid.toString(16).padStart(4,"0")} @ bus ${d.bus} dev ${d.addr}`);
});

// Audit log
const logEntries = ref<LogEntry[]>([]);
provide("logEntries", logEntries);
provide("addLog", addLog);
provide("clearLog", () => { logEntries.value = []; });

function addLog(message: string) {
  logEntries.value.push({ ts: Date.now(), message });
}

// Global DFU event subscriptions for audit log
let unsubProgress: (() => void) | null = null;
let unsubDone: (() => void) | null = null;
let unsubError: (() => void) | null = null;

// Global flash status for the header LED
type LedStatus = "ok" | "warn" | "alarm" | "idle" | "pulse";
const headerStatus = ref<LedStatus>("idle");
const deviceSerial = ref<string | undefined>(undefined);

onMounted(() => {
  unsubProgress = window.picolet.on("dfu:progress", () => {
    headerStatus.value = "pulse";
  });
  unsubDone = window.picolet.on("dfu:done", () => {
    headerStatus.value = "ok";
    addLog("Flash complete.");
  });
  unsubError = window.picolet.on("dfu:error", (data) => {
    const d = data as { message?: string };
    headerStatus.value = "alarm";
    addLog(`Flash error: ${d.message ?? "unknown"}`);
  });
  addLog("PyDFU started.");
});

onUnmounted(() => {
  unsubProgress?.();
  unsubDone?.();
  unsubError?.();
});

// Sync device serial to header when device selected
import { watch } from "vue";
watch(selectedDevice, (dev) => {
  if (dev) {
    deviceSerial.value = `${dev.vid.toString(16).padStart(4,"0")}:${dev.pid.toString(16).padStart(4,"0")}`;
    headerStatus.value = "ok";
  } else {
    deviceSerial.value = undefined;
    headerStatus.value = "idle";
  }
});
</script>

<template>
  <div class="app-shell" ref="root">
    <HeaderRail :global-status="headerStatus" :device-serial="deviceSerial" />
    <main class="main-pane">
      <RouterView />
    </main>
    <!-- Audit strip is hidden on /log route (LogView renders its own expanded strip) -->
    <AuditStrip
      v-if="route.path !== '/log'"
      :entries="logEntries"
      class="reveal-strip"
    />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.main-pane {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
