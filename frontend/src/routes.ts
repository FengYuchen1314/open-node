import type { RouteRecordRaw } from "vue-router";

import ConfigView from "./views/ConfigView.vue";
import DashboardView from "./views/DashboardView.vue";
import ProbeView from "./views/ProbeView.vue";

export const routes: RouteRecordRaw[] = [
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
  {
    path: "/config",
    name: "config",
    component: ConfigView,
  },
];
