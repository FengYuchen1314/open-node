// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { Suspense } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { legacyRouteRedirects, routes } from "./routes";

vi.mock("./react/views/NodesView", () => ({ default: () => <div>Canonical nodes workspace</div> }));
vi.mock("./react/views/PlansView", () => ({ default: () => <div>Canonical plans workspace</div> }));
vi.mock("./react/views/UsersView", () => ({ default: () => <div>Canonical users workspace</div> }));

afterEach(cleanup);

function renderRoute(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Suspense fallback={<div>Loading</div>}><Routes>
    {routes.map(({ path: routePath, component: Component }) => <Route key={routePath} path={routePath} element={<Component />} />)}
  </Routes></Suspense></MemoryRouter>);
}

describe("canonical management routes", () => {
  it("publishes exactly the six consolidated administrator workspaces", () => {
    expect(routes.filter(route => !route.meta).map(route => route.path)).toEqual([
      "/servers", "/nodes", "/templates", "/plans", "/users", "/system-settings",
    ]);
  });

  it.each([
    ["/nodes?tab=speed", "Canonical nodes workspace"],
    ["/plans?source=sidebar", "Canonical plans workspace"],
    ["/users?tab=assign", "Canonical users workspace"],
  ])("loads the intended workspace for the deep link %s", async (path, content) => {
    renderRoute(path);
    expect(await screen.findByText(content)).toBeTruthy();
  });

  it("keeps old node and subscription links pointed at their consolidated workspaces", () => {
    const redirects = Object.fromEntries(legacyRouteRedirects.map(route => [route.path, route.to]));
    expect(redirects["/subscriptions"]).toBe("/users");
    expect(redirects["/node-topologies"]).toBe("/nodes?tab=topologies");
    expect(redirects["/speedtests"]).toBe("/nodes?tab=speed");
  });
});
