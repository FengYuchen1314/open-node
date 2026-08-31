import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import type { AgentCommand } from "../domain/inventory";
import CommandInspector from "../react/components/CommandInspector";

describe("dependent commands", () => {
  it.each([
    ["waiting", "等待前置命令", "等待中"],
    ["skipped", "未执行", "已跳过"],
  ] as const)("renders the %s state", async (status, label, statusLabel) => {
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
    const html = renderToStaticMarkup(createElement(CommandInspector, { commands: [command], streamFramesByCommand: {} }));
    expect(html).toContain(label);
    expect(html).toContain(statusLabel);
    expect(html).toContain("/api/child/xray/config");
  });
});
