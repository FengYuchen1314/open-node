import { renderToString } from "vue/server-renderer";
import { createSSRApp, h } from "vue";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import { describe, expect, it } from "vitest";
import WarpStatus from "./WarpStatus.vue";

async function render(body: unknown) {
  const app = createSSRApp({ render: () => h(WarpStatus, { body }) });
  app.use(createVuetify({ components, ssr: true }));
  return renderToString(app);
}

describe("WARP status", () => {
  it("does not mistake free provider credentials for WARP+", async () => {
    const html = await render({ phase: "configured", installed: true, registered: true,
      account_type: "free", license_active: false, addr_v4: "172.16.0.2", addr_v6: "fd00::2",
      registered_at: "2026-08-28T02:54:55.840001+00:00",
      private_key: "not-for-display", access_token: "not-for-display" });
    expect(html).toContain("Free WARP");
    expect(html).toContain("Outbounds configured");
    expect(html).not.toContain("not-for-display");
    expect(html).not.toContain("Connected");
    expect(html).toContain("02:54 UTC");
  });
  it("shows pending removal instead of success", async () => {
    const html = await render({ phase: "removal_pending", registered: true, installed: false });
    expect(html).toContain("Removal pending");
    expect(html).not.toContain("Outbounds configured");
  });
  it("supports absent and legacy status without rendering arbitrary HTML", async () => {
    expect(await render(null)).toContain("Not installed");
    const html = await render({ installed: true, license_active: true, addr_v4: "<script>bad</script>" });
    expect(html).toContain("WARP+");
    expect(html).not.toContain("<script>");
  });
});
