<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";

const pingResult = ref<string>("");
const infoResult = ref<string>("");
const tickCount = ref<number>(0);
const lastTick = ref<number | null>(null);

let unsubTick: (() => void) | null = null;

async function doPing() {
  try {
    const result = await window.picolet.invoke("ping", { ts: Date.now() });
    const r = result as { pong: number };
    pingResult.value = `pong: ${r.pong}`;
  } catch (e) {
    pingResult.value = `error: ${e}`;
  }
}

async function doGetInfo() {
  try {
    const result = await window.picolet.invoke("get_info");
    const r = result as { platform: string; python: string; uname: string };
    infoResult.value = `${r.python} on ${r.platform}`;
  } catch (e) {
    infoResult.value = `error: ${e}`;
  }
}

onMounted(() => {
  unsubTick = window.picolet.on("ticker:tick", (data) => {
    const d = data as { ts: number };
    lastTick.value = d.ts;
    tickCount.value += 1;
  });
});

onUnmounted(() => {
  if (unsubTick) {
    unsubTick();
    unsubTick = null;
  }
});
</script>

<template>
  <div class="app">
    <h1>{{name}} demo</h1>
    <p class="subtitle">Vue 3 + Vite + TypeScript — picolet app</p>

    <section class="card">
      <h2>Invoke: ping</h2>
      <button @click="doPing">Ping Python</button>
      <p class="result" v-if="pingResult">{{ pingResult }}</p>
    </section>

    <section class="card">
      <h2>Invoke: get_info</h2>
      <button @click="doGetInfo">Get Python Info</button>
      <p class="result" v-if="infoResult">{{ infoResult }}</p>
    </section>

    <section class="card">
      <h2>Push events: ticker:tick</h2>
      <p>
        Ticks received: <strong>{{ tickCount }}</strong>
        <span v-if="lastTick !== null"> — last ts: {{ lastTick }}</span>
      </p>
    </section>
  </div>
</template>

<style scoped>
.app {
  font-family: system-ui, sans-serif;
  max-width: 600px;
  margin: 2rem auto;
  padding: 0 1rem;
}

h1 {
  font-size: 1.8rem;
  margin-bottom: 0.25rem;
}

.subtitle {
  color: #666;
  margin-bottom: 2rem;
}

.card {
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 1rem 1.5rem;
  margin-bottom: 1rem;
}

.card h2 {
  font-size: 1rem;
  margin: 0 0 0.75rem;
  color: #444;
}

button {
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 0.4rem 1rem;
  cursor: pointer;
  font-size: 0.9rem;
}

button:hover {
  background: #2563eb;
}

.result {
  margin-top: 0.5rem;
  font-family: monospace;
  font-size: 0.85rem;
  background: #f5f5f5;
  padding: 0.3rem 0.5rem;
  border-radius: 3px;
}
</style>
