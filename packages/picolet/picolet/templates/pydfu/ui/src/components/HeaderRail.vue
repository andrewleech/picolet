<script setup lang="ts">
import LedDot from "./LedDot.vue";
import { useRouter, useRoute } from "vue-router";

defineProps<{
  deviceSerial?: string;
  globalStatus: "ok" | "warn" | "alarm" | "idle" | "pulse";
}>();

const router = useRouter();
const route = useRoute();
</script>

<template>
  <header class="header-rail reveal-header">
    <div class="rail-left">
      <span class="app-name">PYDFU</span>
      <nav class="rail-nav">
        <a
          class="nav-link"
          :class="{ active: route.path === '/' }"
          @click.prevent="router.push('/')"
          href="/"
          >DEVICES</a
        >
        <a
          class="nav-link"
          :class="{ active: route.path === '/flash' }"
          @click.prevent="router.push('/flash')"
          href="/flash"
          >FLASH</a
        >
        <a
          class="nav-link"
          :class="{ active: route.path === '/log' }"
          @click.prevent="router.push('/log')"
          href="/log"
          >LOG</a
        >
      </nav>
    </div>
    <div class="rail-center" v-if="deviceSerial">
      <span class="serial-label">SERIAL:</span>
      <span class="serial-value">{{ deviceSerial }}</span>
    </div>
    <div class="rail-right">
      <LedDot :status="globalStatus" />
      <span class="status-text">{{ globalStatus.toUpperCase() }}</span>
    </div>
  </header>
</template>

<style scoped>
.header-rail {
  height: 40px;
  background: linear-gradient(180deg, #1c2028 0%, #12161a 100%);
  border-bottom: 1px solid var(--forge);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  flex-shrink: 0;
}

.rail-left {
  display: flex;
  align-items: center;
  gap: 20px;
}

.app-name {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--forge);
  letter-spacing: 0.2em;
}

.rail-nav {
  display: flex;
  gap: 16px;
}

.nav-link {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  color: var(--text-sec);
  text-decoration: none;
  text-transform: uppercase;
  padding: 2px 0;
  border-bottom: 1px solid transparent;
  cursor: pointer;
  transition: color 80ms, border-color 80ms;
}

.nav-link:hover,
.nav-link.active {
  color: var(--text-pri);
  border-bottom-color: var(--forge);
}

.rail-center {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-sec);
  display: flex;
  gap: 6px;
}

.serial-label {
  color: var(--text-sec);
}

.serial-value {
  color: var(--text-pri);
}

.rail-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-text {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-sec);
  letter-spacing: 0.1em;
}
</style>
