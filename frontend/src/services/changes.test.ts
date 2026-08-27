import { describe, expect, it } from "vitest";

import type { AgentChangeSet } from "../domain/changes";
import type { AgentCommand } from "../domain/inventory";
import {
  createChangeSet,
  createRoutedOutboundChangeSet,
  dispatchChangeSet,
  getChangeSet,
  listChangeSets,
  rollbackChangeSet,
} from "./changes";

const timestamp = "2026-08-27T00:00:00Z";

const command: AgentCommand = {
  id: "cmd_1",
  server_id: "srv_1",
  request_id: "srv_1-change",
  method: "POST",
  path: "/api/child/xray/config",
  query: "",
  body: { config: "new" },
  timeout_ms: 5000,
  stream: false,
  status: "pending",
  attempts: 0,
  created_at: timestamp,
  updated_at: timestamp,
};

const changeSet: AgentChangeSet = {
  id: "change_1",
  name: "Rotate config",
  description: "Apply and rollback config writes.",
  status: "planned",
  rollback_on_failure: true,
  rollback_reason: "",
  steps: [
    {
      id: "step_1",
      change_set_id: "change_1",
      sequence: 1,
      server_id: "srv_1",
      label: "Write xray config",
      forward: {
        method: "POST",
        path: "/api/child/xray/config",
        body: { config: "new" },
        timeout_ms: 5000,
      },
      rollback: {
        method: "POST",
        path: "/api/child/xray/config",
        body: { config: "old" },
        timeout_ms: 5000,
      },
      forward_command: null,
      rollback_command: null,
      created_at: timestamp,
      updated_at: timestamp,
    },
  ],
  created_at: timestamp,
  updated_at: timestamp,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("change set API client", () => {
  it("lists change sets without sending license headers", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      return jsonResponse({ change_sets: [changeSet], license_required: false });
    };

    const response = await listChangeSets(fetcher);

    expect(requestUrl).toBe("/api/v1/change-sets");
    expect(headers).toBeUndefined();
    expect(response.license_required).toBe(false);
    expect(response.change_sets[0].steps[0].rollback?.body).toEqual({ config: "old" });
  });

  it("reads a single change set", async () => {
    let requestUrl = "";
    const fetcher: typeof fetch = async (input) => {
      requestUrl = input.toString();
      return jsonResponse({ change_set: changeSet, commands: [], warnings: [] });
    };

    const response = await getChangeSet("change_1", fetcher);

    expect(requestUrl).toBe("/api/v1/change-sets/change_1");
    expect(response.change_set.name).toBe("Rotate config");
  });

  it("creates change sets with JSON body", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    let requestBody: unknown;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      requestBody = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return jsonResponse({ change_set: changeSet, commands: [command], warnings: [] }, 201);
    };

    const response = await createChangeSet(
      {
        name: "Rotate config",
        dispatch: true,
        steps: [
          {
            server_id: "srv_1",
            label: "Write xray config",
            forward: { method: "POST", path: "/api/child/xray/config" },
            rollback: { method: "POST", path: "/api/child/xray/config" },
          },
        ],
      },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/change-sets");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(requestBody).toEqual({
      name: "Rotate config",
      dispatch: true,
      steps: [
        {
          server_id: "srv_1",
          label: "Write xray config",
          forward: { method: "POST", path: "/api/child/xray/config" },
          rollback: { method: "POST", path: "/api/child/xray/config" },
        },
      ],
    });
    expect(response.commands[0].path).toBe("/api/child/xray/config");
  });

  it("creates routed outbound change sets with JSON body", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    let requestBody: unknown;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      requestBody = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return jsonResponse(
        { change_set: changeSet, commands: [], warnings: [], license_required: false },
        201,
      );
    };

    const payload = {
      server_id: "srv_1",
      inbound_tag: "vless-443",
      inbound_protocol: "vless",
      label: "HK-T4",
      parent_ref: "p42",
      outbound: { protocol: "vless", settings: {} },
      dispatch: true,
    };
    const response = await createRoutedOutboundChangeSet(payload, fetcher);

    expect(requestUrl).toBe("/api/v1/change-sets/routed-outbound");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(requestBody).toEqual(payload);
    expect(response.license_required).toBe(false);
  });

  it("dispatches change sets without a JSON body", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    let body: BodyInit | null | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      body = init?.body;
      return jsonResponse({ change_set: changeSet, commands: [command], warnings: [] });
    };

    await dispatchChangeSet("change_1", fetcher);

    expect(requestUrl).toBe("/api/v1/change-sets/change_1/dispatch");
    expect(headers).toBeUndefined();
    expect(body).toBeUndefined();
  });

  it("rolls back change sets with JSON body", async () => {
    let requestUrl = "";
    let headers: HeadersInit | undefined;
    let requestBody: unknown;
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      headers = init?.headers;
      requestBody = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return jsonResponse({
        change_set: { ...changeSet, status: "rollback_queued", rollback_reason: "bad config" },
        commands: [command],
        warnings: [],
        license_required: false,
      });
    };

    const response = await rollbackChangeSet("change_1", { reason: "bad config" }, fetcher);

    expect(requestUrl).toBe("/api/v1/change-sets/change_1/rollback");
    expect(headers).toEqual({ "Content-Type": "application/json" });
    expect(requestBody).toEqual({ reason: "bad config" });
    expect(response.change_set.status).toBe("rollback_queued");
  });
});
