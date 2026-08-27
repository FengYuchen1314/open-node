import type { RouteRecordRaw } from "vue-router";

import ConfigView from "./views/ConfigView.vue";
import DashboardView from "./views/DashboardView.vue";
import ProbeView from "./views/ProbeView.vue";
import SubscriptionsView from "./views/SubscriptionsView.vue";

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
  {
    path: "/subscriptions",
    name: "subscriptions",
    component: SubscriptionsView,
  },
];
