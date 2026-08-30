import { renderToString } from "vue/server-renderer";
import { createSSRApp, h } from "vue";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import { describe, expect, it } from "vitest";

import ProbeView from "./ProbeView.vue";

async function renderProbe(publicOnly: boolean) {
  const app = createSSRApp({ render: () => h(ProbeView, { publicOnly }) });
  app.use(createVuetify({ components, ssr: true }));
  return renderToString(app);
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
    const html = await renderProbe(false);

    expect(html).toContain("Probe settings");
    expect(html).toContain("Worker access");
    expect(html).toContain("Scheduled probes");
  });
});
