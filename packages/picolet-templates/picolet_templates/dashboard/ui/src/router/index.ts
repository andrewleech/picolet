import { createRouter, createWebHashHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'

// Hash-based routing required: the picolet:// custom scheme does not support
// HTML5 history-mode routing (no server-side fallback). FR-VUE-2.
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: DashboardView },
  ],
})

export default router
