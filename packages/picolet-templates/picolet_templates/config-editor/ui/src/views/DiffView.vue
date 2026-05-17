<script setup lang="ts">
/**
 * DiffView — route /diff
 * Renders the unified diff returned by save() as a <pre> block.
 * Per-line colouring: + lines → --fg (phosphor green), - lines → --fg-dim,
 * @@ lines → --fg-dim italic, context → --fg-dim.
 * No syntax highlighting. No JS diff library (F11).
 */
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { state, resetState } from '../store'

const router = useRouter()

onMounted(() => {
  // If diff is empty (reached via direct navigation), go back to edit.
  if (!state.diff || state.diff.length === 0) {
    router.replace('/edit')
  }
})

// Line classifier (F11 / spec §"Diff view").
function lineClass(line: string): string {
  if (line.startsWith('+') && !line.startsWith('+++')) return 'diff-add'
  if (line.startsWith('-') && !line.startsWith('---')) return 'diff-del'
  if (line.startsWith('@@')) return 'diff-hunk'
  return 'diff-ctx'
}

function ruler(label: string): string {
  const avail = Math.max(0, 78 - label.length - 2)
  const left = Math.floor(avail / 2)
  const right = avail - left
  return '═'.repeat(left) + ' ' + label + ' ' + '═'.repeat(right)
}

function loadAnother() {
  resetState()
  router.push('/')
}
</script>

<template>
  <div class="diff-view terminal-frame">
    <!-- Header -->
    <div class="section-rule">{{ ruler('DIFF: ' + state.filePath) }}</div>
    <div class="section-title">{{ state.filePath }}</div>

    <!-- Nav -->
    <div class="action-row">
      <a href="#/edit" class="nav-link">[← BACK TO EDITOR]</a>
      <button class="btn" @click="loadAnother">[LOAD ANOTHER FILE]</button>
    </div>

    <div class="section-rule">{{ '═'.repeat(80) }}</div>

    <!-- Diff body -->
    <pre class="diff-output">
      <span
        v-for="(line, i) in state.diff"
        :key="i"
        :class="lineClass(line)"
      >{{ line }}
</span>
    </pre>

    <!-- No-diff message -->
    <div v-if="state.diff.length === 0" class="field-label" style="color: var(--fg-dim)">
      (no changes)
    </div>

    <div class="section-rule" style="margin-top: 1em">{{ '═'.repeat(80) }}</div>
    <div class="action-row">
      <a href="#/edit" class="nav-link">[← BACK TO EDITOR]</a>
      <button class="btn" @click="loadAnother">[LOAD ANOTHER FILE]</button>
    </div>
  </div>
</template>
