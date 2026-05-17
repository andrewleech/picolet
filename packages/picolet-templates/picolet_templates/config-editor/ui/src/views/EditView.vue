<script setup lang="ts">
/**
 * EditView — route /edit
 * Renders each top-level key in state.document as a field row.
 * Nested dicts → sub-sections with ═════ rules.
 * Nesting > 2 levels → read-only <pre> fallback (O6).
 *
 * Field types: string→text input, number→number input,
 * boolean→monospace [x]/[ ] toggle, array of scalars→comma-separated text,
 * nested object→sub-section.
 *
 * [VALIDATE] → validate() IPC → inline magenta !! errors.
 * [SAVE] → save() IPC → navigate to /diff.
 * [← BACK] → navigate to /.
 */
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { state } from '../store'

const router = useRouter()

onMounted(() => {
  if (!state.filePath) {
    router.replace('/')
  }
})

// ---------------------------------------------------------------------------
// Field value helpers
// ---------------------------------------------------------------------------

function getNestedValue(doc: Record<string, unknown>, path: string): unknown {
  const parts = path.split('.')
  let cur: unknown = doc
  for (const p of parts) {
    if (cur === null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[p]
  }
  return cur
}

function setNestedValue(doc: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split('.')
  let cur: Record<string, unknown> = doc
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i]
    if (cur[p] === null || typeof cur[p] !== 'object') {
      cur[p] = {}
    }
    cur = cur[p] as Record<string, unknown>
  }
  cur[parts[parts.length - 1]] = value
}

// ---------------------------------------------------------------------------
// Error lookup
// ---------------------------------------------------------------------------

function errorsForPath(path: string) {
  return state.errors.filter(e => e.path === path || e.path.startsWith(path + '.'))
}

function hasError(path: string): boolean {
  return errorsForPath(path).length > 0
}

// ---------------------------------------------------------------------------
// Input handlers for fields
// ---------------------------------------------------------------------------

function onStringInput(path: string, e: Event) {
  const val = (e.target as HTMLInputElement).value
  setNestedValue(state.document, path, val)
}

function onNumberInput(path: string, e: Event) {
  const raw = (e.target as HTMLInputElement).value
  const n = raw.includes('.') ? parseFloat(raw) : parseInt(raw, 10)
  setNestedValue(state.document, path, isNaN(n) ? raw : n)
}

function onArrayInput(path: string, e: Event) {
  const raw = (e.target as HTMLInputElement).value
  // Split on comma, trim each item, try numeric conversion.
  const parts = raw.split(',').map(s => {
    const t = s.trim()
    const n = Number(t)
    return !isNaN(n) && t !== '' ? n : t
  })
  setNestedValue(state.document, path, parts)
}

function toggleBool(path: string) {
  const cur = getNestedValue(state.document, path)
  setNestedValue(state.document, path, !cur)
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function handleValidate() {
  if (!state.schemaName) {
    state.errors = []
    return
  }
  try {
    const result = await window.picolet.invoke('validate', {
      format: state.format,
      document: state.document,
      schema_name: state.schemaName,
    }) as { ok: boolean; errors?: Array<{ path: string; message: string }>; error?: string }
    if (result.ok) {
      state.errors = result.errors ?? []
    }
  } catch (_e) {
    // IPC error — leave errors as-is
  }
}

async function handleSave() {
  try {
    const result = await window.picolet.invoke('save', {
      path: state.filePath,
      format: state.format,
      document: state.document,
    }) as { ok: boolean; diff?: string[]; error?: string }
    if (result.ok) {
      state.diff = result.diff ?? []
      router.push('/diff')
    }
  } catch (_e) {
    // IPC error
  }
}

// ---------------------------------------------------------------------------
// Section ruler helper
// ---------------------------------------------------------------------------
function ruler(label: string): string {
  const avail = Math.max(0, 78 - label.length - 2)
  const left = Math.floor(avail / 2)
  const right = avail - left
  return '═'.repeat(left) + ' ' + label + ' ' + '═'.repeat(right)
}

// ---------------------------------------------------------------------------
// Type detection helpers
// ---------------------------------------------------------------------------
function valueType(v: unknown): string {
  if (v === null || v === undefined) return 'null'
  if (typeof v === 'boolean') return 'boolean'
  if (typeof v === 'number') return 'number'
  if (typeof v === 'string') return 'string'
  if (Array.isArray(v)) return 'array'
  if (typeof v === 'object') return 'object'
  return 'unknown'
}

function isScalarArray(v: unknown[]): boolean {
  return v.every(i => typeof i !== 'object' || i === null)
}

function isDeepObject(v: unknown): boolean {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return false
  return Object.values(v as Record<string, unknown>).some(
    child => typeof child === 'object' && child !== null && !Array.isArray(child)
  )
}

// String representation of a scalar value for input display
function scalarString(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}
</script>

<template>
  <div class="edit-view terminal-frame">
    <!-- Header -->
    <div class="section-rule">{{ ruler(state.filePath + ' (' + state.format + ')') }}</div>
    <div class="section-title">{{ state.filePath }}</div>

    <!-- Nav -->
    <div class="action-row">
      <a href="#/" class="nav-link">[← BACK]</a>
      <button class="btn btn-primary btn-validate" @click="handleValidate">[VALIDATE]</button>
      <button class="btn btn-primary btn-save" @click="handleSave">[SAVE]</button>
    </div>

    <!-- Schema info (dim, if set) -->
    <div v-if="state.schemaName" class="field-group">
      <span class="field-label" style="color: var(--fg-dim)">schema = {{ state.schemaName }}</span>
    </div>

    <!-- Global errors (path = "" means top-level) -->
    <div v-for="err in state.errors.filter(e => !e.path)" :key="err.message" class="banner-error">
      !! {{ err.message }}
    </div>

    <!-- Render each top-level key -->
    <template v-for="(value, key) in state.document" :key="String(key)">
      <!-- Sub-section: nested object -->
      <template v-if="valueType(value) === 'object'">
        <div class="section-rule">{{ ruler(String(key)) }}</div>

        <!-- Render each key inside the sub-object -->
        <template v-for="(childVal, childKey) in (value as Record<string, unknown>)" :key="String(childKey)">
          <div class="field-group">
            <!-- Deep nesting (> 2 levels) → read-only pre fallback (O6) -->
            <template v-if="valueType(childVal) === 'object' && !isDeepObject(value)">
              <div class="section-rule" style="margin-left: 2ch">{{ ruler(String(childKey)) }}</div>
              <div v-for="(gcVal, gcKey) in (childVal as Record<string, unknown>)" :key="String(gcKey)" class="field-group">
                <div v-if="valueType(gcVal) === 'object'" class="field-group">
                  <span class="field-label" :class="{ 'has-error': hasError(key + '.' + childKey + '.' + gcKey) }">
                    {{ gcKey }} = ▌&nbsp;
                  </span>
                  <pre class="raw-value">{{ JSON.stringify(gcVal, null, 2) }}</pre>
                  <span v-for="err in errorsForPath(key + '.' + childKey + '.' + gcKey)" :key="err.message" class="field-error">
                    !! {{ err.message }}
                  </span>
                </div>
                <template v-else>
                  <div class="field-row">
                    <span
                      class="field-label"
                      :class="{ 'has-error': hasError(String(key) + '.' + String(childKey) + '.' + String(gcKey)) }"
                    >{{ gcKey }} = ▌&nbsp;</span>
                    <!-- boolean -->
                    <div v-if="valueType(gcVal) === 'boolean'" class="field-input">
                      <button
                        class="bool-toggle"
                        :data-key="String(key) + '.' + String(childKey) + '.' + String(gcKey)"
                        @click="toggleBool(String(key) + '.' + String(childKey) + '.' + String(gcKey))"
                      >{{ gcVal ? '[x]' : '[ ]' }}</button>
                    </div>
                    <!-- number -->
                    <div v-else-if="valueType(gcVal) === 'number'" class="field-input">
                      <input
                        type="number"
                        :data-key="String(key) + '.' + String(childKey) + '.' + String(gcKey)"
                        :value="String(gcVal)"
                        @input="onNumberInput(String(key) + '.' + String(childKey) + '.' + String(gcKey), $event)"
                        spellcheck="false"
                        autocomplete="off"
                      />
                    </div>
                    <!-- string / array / other -->
                    <div v-else class="field-input">
                      <input
                        type="text"
                        :data-key="String(key) + '.' + String(childKey) + '.' + String(gcKey)"
                        :value="scalarString(gcVal)"
                        @input="valueType(gcVal) === 'array'
                          ? onArrayInput(String(key) + '.' + String(childKey) + '.' + String(gcKey), $event)
                          : onStringInput(String(key) + '.' + String(childKey) + '.' + String(gcKey), $event)"
                        spellcheck="false"
                        autocomplete="off"
                      />
                    </div>
                  </div>
                  <span
                    v-for="err in errorsForPath(String(key) + '.' + String(childKey) + '.' + String(gcKey))"
                    :key="err.message"
                    class="field-error"
                  >!! {{ err.message }}</span>
                </template>
              </div>
            </template>

            <!-- Deep-nested object fallback (O6) -->
            <template v-else-if="valueType(childVal) === 'object'">
              <div class="field-row">
                <span
                  class="field-label"
                  :class="{ 'has-error': hasError(String(key) + '.' + String(childKey)) }"
                >{{ childKey }} = ▌&nbsp;</span>
              </div>
              <pre class="raw-value">{{ JSON.stringify(childVal, null, 2) }}</pre>
              <span v-for="err in errorsForPath(String(key) + '.' + String(childKey))" :key="err.message" class="field-error">
                !! {{ err.message }}
              </span>
            </template>

            <!-- Scalar / array child -->
            <template v-else>
              <div class="field-row">
                <span
                  class="field-label"
                  :class="{ 'has-error': hasError(String(key) + '.' + String(childKey)) }"
                >{{ childKey }} = ▌&nbsp;</span>
                <!-- boolean -->
                <div v-if="valueType(childVal) === 'boolean'" class="field-input">
                  <button
                    class="bool-toggle"
                    :data-key="String(key) + '.' + String(childKey)"
                    @click="toggleBool(String(key) + '.' + String(childKey))"
                  >{{ childVal ? '[x]' : '[ ]' }}</button>
                </div>
                <!-- number -->
                <div v-else-if="valueType(childVal) === 'number'" class="field-input">
                  <input
                    type="number"
                    :data-key="String(key) + '.' + String(childKey)"
                    :value="String(childVal)"
                    @input="onNumberInput(String(key) + '.' + String(childKey), $event)"
                    spellcheck="false"
                    autocomplete="off"
                  />
                </div>
                <!-- array of scalars -->
                <div v-else-if="valueType(childVal) === 'array' && isScalarArray(childVal as unknown[])" class="field-input">
                  <input
                    type="text"
                    :data-key="String(key) + '.' + String(childKey)"
                    :value="(childVal as unknown[]).join(', ')"
                    @input="onArrayInput(String(key) + '.' + String(childKey), $event)"
                    spellcheck="false"
                    autocomplete="off"
                  />
                </div>
                <!-- string -->
                <div v-else class="field-input">
                  <input
                    type="text"
                    :data-key="String(key) + '.' + String(childKey)"
                    :value="scalarString(childVal)"
                    @input="onStringInput(String(key) + '.' + String(childKey), $event)"
                    spellcheck="false"
                    autocomplete="off"
                  />
                </div>
              </div>
              <span
                v-for="err in errorsForPath(String(key) + '.' + String(childKey))"
                :key="err.message"
                class="field-error"
              >!! {{ err.message }}</span>
            </template>
          </div>
        </template>
      </template>

      <!-- Top-level scalar / array -->
      <template v-else>
        <div class="field-group">
          <div class="field-row">
            <span
              class="field-label"
              :class="{ 'has-error': hasError(String(key)) }"
            >{{ key }} = ▌&nbsp;</span>
            <!-- boolean -->
            <div v-if="valueType(value) === 'boolean'" class="field-input">
              <button
                class="bool-toggle"
                :data-key="String(key)"
                @click="toggleBool(String(key))"
              >{{ value ? '[x]' : '[ ]' }}</button>
            </div>
            <!-- number -->
            <div v-else-if="valueType(value) === 'number'" class="field-input">
              <input
                type="number"
                :data-key="String(key)"
                :value="String(value)"
                @input="onNumberInput(String(key), $event)"
                spellcheck="false"
                autocomplete="off"
              />
            </div>
            <!-- array of scalars -->
            <div v-else-if="valueType(value) === 'array' && isScalarArray(value as unknown[])" class="field-input">
              <input
                type="text"
                :data-key="String(key)"
                :value="(value as unknown[]).join(', ')"
                @input="onArrayInput(String(key), $event)"
                spellcheck="false"
                autocomplete="off"
              />
            </div>
            <!-- string / other -->
            <div v-else class="field-input">
              <input
                type="text"
                :data-key="String(key)"
                :value="scalarString(value)"
                @input="onStringInput(String(key), $event)"
                spellcheck="false"
                autocomplete="off"
              />
            </div>
          </div>
          <span v-for="err in errorsForPath(String(key))" :key="err.message" class="field-error">
            !! {{ err.message }}
          </span>
        </div>
      </template>
    </template>

    <div class="section-rule" style="margin-top: 1em">{{ '═'.repeat(80) }}</div>
    <div class="action-row">
      <a href="#/" class="nav-link">[← BACK]</a>
      <button class="btn btn-primary btn-validate" @click="handleValidate">[VALIDATE]</button>
      <button class="btn btn-primary btn-save" @click="handleSave">[SAVE]</button>
    </div>
  </div>
</template>
