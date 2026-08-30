import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

import ProbeView from "../react/views/ProbeView";
import ProbeAdministrationPanel from "../react/components/ProbeAdministrationPanel";

async function renderProbe(publicOnly: boolean) {
  return renderToStaticMarkup(createElement(ProbeView, { publicOnly }));
}

describe("probe surface modes", () => {
  it("omits every administrator control from the public-only surface", async () => {
    const html = await renderProbe(true);

    expect(html).toContain("Public probe");
    expect(html).toContain("Probe nodes");
    expect(html).not.toContain("Probe settings");
    expect(html).not.toContain("Worker access");
    expect(html).not.toContain("Scheduled probes");
    expect(html).not.toContain("Dispatch due");
  });

  it("keeps administrator controls in the authenticated control-plane view", async () => {
    const html = renderToStaticMarkup(createElement(ProbeAdministrationPanel, {
      accessToken: "", onSettings: () => {}, onAccessToken: () => {}, onRefresh: () => {},
    }));

    expect(html).toContain("Probe settings");
    expect(html).toContain("Worker access");
    expect(html).toContain("Scheduled probes");
  });
});
