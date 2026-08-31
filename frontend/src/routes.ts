import { lazy, type ComponentType, type LazyExoticComponent } from "react";

export interface WorkspaceRoute {
  path: string;
  name: string;
  component: LazyExoticComponent<ComponentType>;
  meta?: { subscriber: true };
}
export const routes: WorkspaceRoute[] = [
  { path: "/account", name: "account", component: lazy(() => import("./react/views/AccountView")), meta: { subscriber: true } },
  { path: "/account/external-subscriptions", name: "account-external-subscriptions", component: lazy(() => import("./react/views/AccountExternalSourcesView")), meta: { subscriber: true } },
  { path: "/account/renewals", name: "account-renewals", component: lazy(() => import("./react/views/RenewalRequestView")), meta: { subscriber: true } },
  { path: "/renewals", name: "renewals", component: lazy(() => import("./react/views/AdminRenewalsView")) },
  { path: "/certificates", name: "certificates", component: lazy(() => import("./react/views/CertificatesView")) },
  { path: "/access", name: "access", component: lazy(() => import("./react/views/AccessView")) },
  { path: "/", name: "overview", component: lazy(() => import("./react/views/DashboardView")) },
  { path: "/probe", name: "probe", component: lazy(() => import("./react/views/ProbeView")) },
  { path: "/config", name: "config", component: lazy(() => import("./react/views/ConfigView")) },
  { path: "/changes", name: "changes", component: lazy(() => import("./react/views/ChangesView")) },
  { path: "/subscriptions", name: "subscriptions", component: lazy(() => import("./react/views/SubscriptionsView")) },
  { path: "/templates", name: "templates", component: lazy(() => import("./react/views/TemplatesView")) },
  { path: "/notifications", name: "notifications", component: lazy(() => import("./react/views/NotificationsView")) },
  { path: "/system-settings", name: "system-settings", component: lazy(() => import("./react/views/SystemSettingsView")) },
  { path: "/backups", name: "backups", component: lazy(() => import("./react/views/BackupsView")) },
];
