<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { state } from './store'

// Screenshot mode — disable all CSS animations (NFR-EX-5).
// Also pre-populate reactive store from window.__initState if provided
// (used by generate_screenshots.py to pre-navigate to mid-flow states).
onMounted(() => {
  if (window.__PICOLET_SCREENSHOT_MODE__) {
    document.documentElement.classList.add('no-animation')
  }
  if (window.__initState) {
    const s = window.__initState
    if (s.filePath !== undefined) state.filePath = s.filePath
    if (s.format !== undefined) state.format = s.format
    if (s.document !== undefined) state.document = s.document as Record<string, unknown>
    if (s.schemaName !== undefined) state.schemaName = s.schemaName
    if (s.errors !== undefined) state.errors = s.errors as import('./store').ValidationError[]
    if (s.diff !== undefined) state.diff = s.diff
  }
})
</script>

<template>
  <RouterView />
</template>
