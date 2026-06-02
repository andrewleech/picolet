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
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: radial-gradient(
    circle at 30% 30%,
    color-mix(in srgb, var(--led-color) 80%, white) 0%,
    var(--led-color) 50%,
    color-mix(in srgb, var(--led-color) 60%, black) 100%
  );
  box-shadow:
    0 0 0 1px rgba(0, 0, 0, 0.6),
    0 0 4px var(--led-color),
    inset 0 1px 0 0 rgba(255, 255, 255, 0.2);
  flex-shrink: 0;
}

.led-idle  { --led-color: var(--led-idle); }
.led-ok    { --led-color: var(--led-ok); }
.led-warn  { --led-color: var(--led-warn); }
.led-alarm { --led-color: var(--led-alarm); }

.led-pulse {
  --led-color: var(--forge);
  animation: led-pulse 0.5s ease-in-out infinite alternate;
}

@keyframes led-pulse {
  from {
    box-shadow:
      0 0 0 1px rgba(0, 0, 0, 0.6),
      0 0 2px var(--led-color),
      inset 0 1px 0 0 rgba(255, 255, 255, 0.2);
  }
  to {
    box-shadow:
      0 0 0 1px rgba(0, 0, 0, 0.6),
      0 0 6px var(--led-color),
      inset 0 1px 0 0 rgba(255, 255, 255, 0.2);
  }
}
</style>
