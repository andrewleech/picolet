<script setup lang="ts">
/**
 * PickerView — route /
 * Typed file-path input with directory autocomplete.
 * Typed schema-name input with schema-list autocomplete.
 * [LOAD] triggers load() IPC → navigates to /edit on success.
 *
 * F6, F7, F8 from the phase plan.
 * Aesthetic: 80ch terminal frame, ASCII dividers, ▌ cursor separator.
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { state } from '../store'

interface DirEntry { name: string; is_dir: boolean }

const router = useRouter()

const filePath = ref('')
const schemaName = ref('')
const loadError = ref('')

// File autocomplete
const fileSuggestions = ref<DirEntry[]>([])
const showFileSuggestions = ref(false)
const fileInputEl = ref<HTMLInputElement | null>(null)

// Schema autocomplete
const schemaSuggestions = ref<string[]>([])
const showSchemaSuggestions = ref(false)
const schemaInputEl = ref<HTMLInputElement | null>(null)

let fileDebounceTimer: ReturnType<typeof setTimeout> | null = null

onMounted(async () => {
  // Pre-load schema list for quick autocomplete on focus.
  try {
    const result = await window.picolet.invoke('list_schemas') as string[]
    schemaSuggestions.value = result
  } catch (_e) {
    schemaSuggestions.value = []
  }
})

// ---------------------------------------------------------------------------
// File path input handlers
// ---------------------------------------------------------------------------

async function onFileInput() {
  loadError.value = ''
  const val = filePath.value
  if (!val.endsWith('/')) {
    showFileSuggestions.value = false
    return
  }
  if (fileDebounceTimer) clearTimeout(fileDebounceTimer)
  fileDebounceTimer = setTimeout(async () => {
    try {
      const entries = await window.picolet.invoke('list_dir', { path: val }) as DirEntry[]
      fileSuggestions.value = Array.isArray(entries) ? entries : []
      showFileSuggestions.value = fileSuggestions.value.length > 0
    } catch (_e) {
      fileSuggestions.value = []
      showFileSuggestions.value = false
    }
  }, 150)
}

function onFileKeydown(e: KeyboardEvent) {
  if (e.key === 'Tab' && showFileSuggestions.value && fileSuggestions.value.length > 0) {
    e.preventDefault()
    const first = fileSuggestions.value[0]
    filePath.value = filePath.value + first.name + (first.is_dir ? '/' : '')
    showFileSuggestions.value = false
    onFileInput()
  }
  if (e.key === 'Escape') {
    showFileSuggestions.value = false
  }
  if (e.key === 'Enter') {
    handleLoad()
  }
}

function selectFileSuggestion(entry: DirEntry) {
  filePath.value = filePath.value + entry.name + (entry.is_dir ? '/' : '')
  showFileSuggestions.value = false
  fileInputEl.value?.focus()
  onFileInput()
}

// ---------------------------------------------------------------------------
// Schema name input handlers
// ---------------------------------------------------------------------------

function onSchemaFocus() {
  showSchemaSuggestions.value = schemaSuggestions.value.length > 0
}

function onSchemaKeydown(e: KeyboardEvent) {
  if (e.key === 'Tab' && showSchemaSuggestions.value && schemaSuggestions.value.length > 0) {
    e.preventDefault()
    schemaName.value = schemaSuggestions.value[0]
    showSchemaSuggestions.value = false
  }
  if (e.key === 'Escape') {
    showSchemaSuggestions.value = false
  }
  if (e.key === 'Enter') {
    handleLoad()
  }
}

function selectSchemaSuggestion(name: string) {
  schemaName.value = name
  showSchemaSuggestions.value = false
  schemaInputEl.value?.focus()
}

function hideFileSuggestions() {
  setTimeout(() => { showFileSuggestions.value = false }, 150)
}

function hideSchemaSuggestions() {
  setTimeout(() => { showSchemaSuggestions.value = false }, 150)
}

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------

async function handleLoad() {
  loadError.value = ''
  const path = filePath.value.trim()
  if (!path) {
    loadError.value = '!! file path is required'
    return
  }
  try {
    const result = await window.picolet.invoke('load', { path }) as {
      ok?: boolean
      error?: string
      format: string
      document: Record<string, unknown>
      schema_hint: string | null
    }
    if (result.ok === false) {
      loadError.value = `!! ${result.error ?? 'load failed'}`
      return
    }
    state.filePath = path
    state.format = result.format
    state.document = result.document
    state.schemaName = schemaName.value.trim() || result.schema_hint || ''
    state.errors = []
    state.diff = []
    router.push('/edit')
  } catch (e) {
    loadError.value = `!! ${String(e)}`
  }
}
</script>

<template>
  <div class="picker-view terminal-frame">
    <!-- Section header -->
    <div class="section-rule">{{ '═'.repeat(80) }}</div>
    <div class="section-title">CONFIG EDITOR</div>
    <div class="section-rule">{{ '═'.repeat(80) }}</div>

    <!-- Load error banner -->
    <div v-if="loadError" class="banner-error">{{ loadError }}</div>

    <!-- File path input -->
    <div class="field-group suggestions-wrap">
      <div class="field-row">
        <span class="field-label">file&nbsp;&nbsp;&nbsp;= ▌&nbsp;</span>
        <div class="field-input">
          <input
            ref="fileInputEl"
            v-model="filePath"
            class="file-path-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            placeholder="/path/to/config.toml"
            @input="onFileInput"
            @keydown="onFileKeydown"
            @blur="hideFileSuggestions"
          />
        </div>
      </div>
      <ul v-if="showFileSuggestions" class="suggestions">
        <li
          v-for="entry in fileSuggestions"
          :key="entry.name"
          @mousedown.prevent="selectFileSuggestion(entry)"
        >{{ entry.name }}{{ entry.is_dir ? '/' : '' }}</li>
      </ul>
    </div>

    <!-- Schema name input -->
    <div class="field-group suggestions-wrap">
      <div class="field-row">
        <span class="field-label">schema = ▌&nbsp;</span>
        <div class="field-input">
          <input
            ref="schemaInputEl"
            v-model="schemaName"
            class="schema-name-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            placeholder="(optional schema name)"
            @focus="onSchemaFocus"
            @keydown="onSchemaKeydown"
            @blur="hideSchemaSuggestions"
          />
        </div>
      </div>
      <ul v-if="showSchemaSuggestions" class="suggestions">
        <li
          v-for="name in schemaSuggestions"
          :key="name"
          @mousedown.prevent="selectSchemaSuggestion(name)"
        >{{ name }}</li>
      </ul>
    </div>

    <div class="action-row">
      <button class="btn btn-primary btn-load" @click="handleLoad">[LOAD]</button>
    </div>
  </div>
</template>
