import { describe, expect, it } from "vitest";

import { routes } from "./routes";

describe("router", () => {
  it("registers the config workspace route", () => {
    const paths = routes.map((route) => route.path);

    expect(paths).toContain("/config");
  });
});
