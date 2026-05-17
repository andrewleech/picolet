import { createRouter, createWebHashHistory } from "vue-router";
import HomeView from "../views/HomeView.vue";
import FlashView from "../views/FlashView.vue";
import LogView from "../views/LogView.vue";

// Hash-based routing required: the picolet:// custom scheme does not support
// HTML5 history-mode routing (no server-side fallback). R4 in phase plan.
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: HomeView },
    { path: "/flash", component: FlashView },
    { path: "/log", component: LogView },
  ],
});

export default router;
