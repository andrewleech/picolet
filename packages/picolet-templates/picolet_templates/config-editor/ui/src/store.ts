/**
 * store.ts — thin reactive shared state for config-editor.
 *
 * Uses Vue 3 Composition API module-level reactive() instead of Vuex/Pinia.
 * All three route views (PickerView, EditView, DiffView) import and mutate
 * this single object. No prop-drilling across route transitions.
 *
 * Limitation: state is in-memory only — not persisted across hard reloads.
 * This is acceptable for a single-file edit session. See O5 in phase plan.
 */
import { reactive } from 'vue'

export interface ValidationError {
  path: string
  message: string
}

export interface ConfigState {
  filePath: string
  format: string
  document: Record<string, unknown>
  schemaName: string
  errors: ValidationError[]
  diff: string[]
  pendingDocument: Record<string, unknown> | null
}

export const state = reactive<ConfigState>({
  filePath: '',
  format: '',
  document: {},
  schemaName: '',
  errors: [],
  diff: [],
  pendingDocument: null,
})

export function resetState(): void {
  state.filePath = ''
  state.format = ''
  state.document = {}
  state.schemaName = ''
  state.errors = []
  state.diff = []
  state.pendingDocument = null
}
