import { describe, expect, it } from "vitest";

import { legacyRouteRedirects, routes } from "./routes";

describe("router", () => {
  it("registers exactly seven canonical administrator workspaces in sidebar order", () => {
    expect(routes.filter(route => !route.meta?.subscriber).map(route => route.path)).toEqual([
      "/servers", "/nodes", "/templates", "/plans", "/users", "/certificates", "/system-settings",
    ]);
  });

  it("keeps the three subscriber routes outside the administrator shell", () => {
    expect(routes.filter(route => route.meta?.subscriber).map(route => route.path)).toEqual([
      "/account", "/account/external-subscriptions", "/account/renewals",
    ]);
  });

  it("moves legacy deep links into query-selected aggregate tabs", () => {
    expect(Object.fromEntries(legacyRouteRedirects.map(route => [route.path, route.to]))).toMatchObject({
      "/": "/servers",
      "/config": "/servers?tab=egress",
      "/server-sharing": "/servers?tab=sharing",
      "/ddns": "/servers?tab=ddns",
      "/speedtests": "/nodes?tab=speed",
      "/node-topologies": "/nodes?tab=topologies",
      "/subscription-customizations": "/templates?tab=customizations",
      "/access": "/system-settings?tab=access",
      "/notifications": "/system-settings?tab=notifications",
      "/backups": "/system-settings?tab=backups",
      "/changes": "/system-settings?tab=changes",
      "/renewals": "/system-settings?tab=renewals",
      "/probe": "/system-settings?tab=probe",
    });
  });

  it("keeps certificate management as a canonical administrator workspace", () => {
    expect(routes.some(route => route.path === "/certificates")).toBe(true);
    expect(legacyRouteRedirects.some(route => route.path === "/certificates")).toBe(false);
  });
});
