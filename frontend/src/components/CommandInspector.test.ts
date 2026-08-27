import { renderToString } from "vue/server-renderer";
import { createSSRApp, h } from "vue";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import { describe, expect, it } from "vitest";
import type { AgentCommand } from "../domain/inventory";
import CommandInspector from "./CommandInspector.vue";

describe("dependent commands", () => {
  it.each([
    ["waiting", "Waiting for prerequisite"],
    ["skipped", "Not executed"],
  ] as const)("renders the %s state", async (status, label) => {
    const command: AgentCommand = {
      id: "write-config",
      server_id: "edge",
      request_id: "write-config-request",
      method: "POST",
      path: "/api/child/xray/config",
      query: "",
      status,
      depends_on_command_id: "validate-config",
      attempts: 0,
      timeout_ms: 60_000,
      stream: false,
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:00Z",
    };
    const app = createSSRApp({
      render: () => h(CommandInspector, { commands: [command], streamFramesByCommand: {} }),
    });
    app.use(createVuetify({ components, ssr: true }));
    const html = await renderToString(app);
    expect(html).toContain(label);
    expect(html).toContain(status);
    expect(html).toContain("/api/child/xray/config");
  });
});
