<script setup lang="ts">
import { onMounted } from "vue";
import { RouterView } from "vue-router";

// Screenshot mode — disable all CSS animations (NFR-EX-5)
onMounted(() => {
  if (window.__PICOLET_SCREENSHOT_MODE__) {
    document.documentElement.classList.add("no-animation");
  }
});
</script>

<template>
  <RouterView />
</template>
