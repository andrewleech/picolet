<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { state, type Tick } from './store'

onMounted(async () => {
  // Screenshot mode: disable all CSS animations for deterministic captures.
  // NFR-EX-5.
  if (window.__PICOLET_SCREENSHOT_MODE__) {
    document.documentElement.classList.add('no-animation')
  }

  // Register event listeners before calling get_history() so no tick is
  // missed between the history fetch and the first live event.
  const unsubTick = window.picolet.on('metrics:tick', (data) => {
    const tick = data as Tick
    state.history.push(tick)
    if (state.history.length > 60) {
      state.history.shift()
    }
    state.latest = tick
  })

  const unsubError = window.picolet.on('metrics:error', (data) => {
    const d = data as { message: string }
    state.error = d.message
  })

  // Bootstrap history — populates the charts immediately on mount.
  try {
    const result = await window.picolet.invoke('get_history', null) as { history: Tick[] }
    if (result?.history?.length) {
      state.history = result.history
      state.latest = result.history[result.history.length - 1]
    }
  } catch (e) {
    // Non-fatal: live events will populate the store as they arrive.
    console.warn('get_history failed:', e)
  }

  state.initialized = true

  // Signal AppHarness that the frontend is ready for test driving. FR-TEST.
  ;(window.picolet as unknown as Record<string, unknown>).__ready__ = true

  return () => {
    unsubTick()
    unsubError()
  }
})
</script>

<template>
  <RouterView />
</template>
