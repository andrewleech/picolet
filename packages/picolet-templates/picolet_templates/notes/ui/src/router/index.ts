import { createRouter, createWebHashHistory } from "vue-router";
import ListView from "../views/ListView.vue";
import EditView from "../views/EditView.vue";
import AboutView from "../views/AboutView.vue";

// Hash-based routing required: the picolet:// custom scheme does not support
// HTML5 history-mode routing (no server-side fallback). F5 in phase plan.
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: ListView },
    { path: "/edit/:slug", component: EditView },
    { path: "/about", component: AboutView },
  ],
});

export default router;
