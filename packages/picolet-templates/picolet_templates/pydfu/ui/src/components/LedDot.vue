<script setup lang="ts">
defineProps<{
  status: "ok" | "warn" | "alarm" | "idle" | "pulse";
}>();
</script>

<template>
  <span class="led-dot" :class="`led-${status}`" />
</template>

<style scoped>
.led-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
}

.led-idle {
  background: var(--led-idle);
}

.led-ok {
  background: var(--led-ok);
}

.led-warn {
  background: var(--led-warn);
}

.led-alarm {
  background: var(--led-alarm);
}

.led-pulse {
  background: var(--forge);
  animation: pulse 0.5s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
</style>
