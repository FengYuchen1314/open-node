import { createRouter, createWebHistory } from "vue-router";

import DashboardView from "./views/DashboardView.vue";
import ProbeView from "./views/ProbeView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "overview",
      component: DashboardView,
    },
    {
      path: "/probe",
      name: "probe",
      component: ProbeView,
    },
  ],
});
