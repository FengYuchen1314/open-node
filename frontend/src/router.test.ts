import { describe, expect, it } from "vitest";

import { routes } from "./routes";

describe("router", () => {
  it("registers the config and subscription workspace routes", () => {
    const paths = routes.map((route) => route.path);

    expect(paths).toContain("/config");
    expect(paths).toContain("/subscriptions");
    expect(paths).toContain("/templates");
    expect(routes.find(route => route.path === "/account")?.meta?.subscriber).toBe(true);
    expect(routes.find(route => route.path === "/subscriptions")?.meta?.subscriber).toBeUndefined();
  });
});
