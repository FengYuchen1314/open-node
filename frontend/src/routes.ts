import { lazy, type ComponentType, type LazyExoticComponent } from "react";

export interface WorkspaceRoute {
  path: string;
  name: string;
  component: LazyExoticComponent<ComponentType>;
  meta?: { subscriber: true };
}

export interface LegacyRouteRedirect {
  path: string;
  to: string;
}

export const routes: WorkspaceRoute[] = [
  { path: "/account", name: "account", component: lazy(() => import("./react/views/AccountView")), meta: { subscriber: true } },
  { path: "/account/external-subscriptions", name: "account-external-subscriptions", component: lazy(() => import("./react/views/AccountExternalSourcesView")), meta: { subscriber: true } },
  { path: "/account/renewals", name: "account-renewals", component: lazy(() => import("./react/views/RenewalRequestView")), meta: { subscriber: true } },
  { path: "/servers", name: "servers", component: lazy(() => import("./react/views/ServerWorkspaceView")) },
  { path: "/nodes", name: "nodes", component: lazy(() => import("./react/views/NodesView")) },
  { path: "/templates", name: "templates", component: lazy(() => import("./react/views/TemplatesView")) },
  { path: "/plans", name: "plans", component: lazy(() => import("./react/views/PlansView")) },
  { path: "/users", name: "users", component: lazy(() => import("./react/views/UsersView")) },
  { path: "/certificates", name: "certificates", component: lazy(() => import("./react/views/CertificatesView")) },
  { path: "/system-settings", name: "system-settings", component: lazy(() => import("./react/views/SystemWorkspaceView")) },
];

export const legacyRouteRedirects: LegacyRouteRedirect[] = [
  { path: "/", to: "/servers" },
  { path: "/subscriptions", to: "/users" },
  { path: "/config", to: "/servers?tab=egress" },
  { path: "/server-sharing", to: "/servers?tab=sharing" },
  { path: "/ddns", to: "/servers?tab=ddns" },
  { path: "/speedtests", to: "/nodes?tab=speed" },
  { path: "/node-topologies", to: "/nodes?tab=topologies" },
  { path: "/subscription-customizations", to: "/templates?tab=customizations" },
  { path: "/access", to: "/system-settings?tab=access" },
  { path: "/notifications", to: "/system-settings?tab=notifications" },
  { path: "/backups", to: "/system-settings?tab=backups" },
  { path: "/changes", to: "/system-settings?tab=changes" },
  { path: "/renewals", to: "/system-settings?tab=renewals" },
  { path: "/probe", to: "/system-settings?tab=probe" },
];
