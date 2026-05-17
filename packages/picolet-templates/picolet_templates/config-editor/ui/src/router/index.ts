import { createRouter, createWebHashHistory } from 'vue-router'
import PickerView from '../views/PickerView.vue'
import EditView from '../views/EditView.vue'
import DiffView from '../views/DiffView.vue'

// Hash-based routing required: the picolet:// custom scheme does not support
// HTML5 history-mode routing (no server-side fallback). FR-VUE-2.
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/',     component: PickerView },
    { path: '/edit', component: EditView },
    { path: '/diff', component: DiffView },
  ],
})

export default router
