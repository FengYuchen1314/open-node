import { describe, expect, it } from "vitest";

import type { AgentCommand } from "../domain/inventory";
import type {
  ManagedNode,
  ProductUser,
  SubscriptionCatalogBundle,
  SubscriptionPlan,
  SubscriptionTemplatePreset,
} from "../domain/subscriptions";
import {
  assignSubscriptionPlan,
  createManagedNode,
  createManagedNodeFromPreset,
  createProductUser,
  createProductUserSubscriptionToken,
  createSubscriptionPlan,
  exportSubscriptionCatalog,
  getProductUserSubscriptionToken,
  getProductUserTraffic,
  importSubscriptionCatalog,
  listSubscriptionTemplatePresets,
  listManagedNodes,
  listProductUserCredentials,
  listProductUsers,
  listSubscriptionPlans,
  resetProductUserSubscriptionToken,
} from "./subscriptions";

const timestamp = "2026-08-27T00:00:00Z";

const productUser: ProductUser = {
  username: "alice@example.com",
  email: "alice@example.com",
  display_name: "Alice",
  role: "user",
  is_active: true,
  current_plan_id: null,
  plan_started_at: null,
  plan_expires_at: null,
  is_reset: false,
  reset_day: 0,
  created_at: timestamp,
  updated_at: timestamp,
};

const managedNode: ManagedNode = {
  id: "node_1",
  name: "Tokyo vless",
  server_id: "srv_1",
  protocol: "vless",
  node_type: "routed",
  inbound_tag: "vless-443",
  routed_outbound_tag: "tokyo-out",
  routed_rule_marktag: "route-tokyo",
  tag: "jp",
  tags: ["jp", "premium"],
  enabled: true,
  client_template: {
    id: "client-{username}",
    email: "{username}__tokyo",
  },
  config: {},
  created_at: timestamp,
  updated_at: timestamp,
};

const subscriptionPlan: SubscriptionPlan = {
  id: "plan_1",
  name: "Premium",
  description: "Premium routed bundle",
  traffic_limit_gb: 128,
  cycle_days: 30,
  is_reset: true,
  reset_day: 1,
  node_ids: ["node_1"],
  node_multipliers: { node_1: 1 },
  node_speed_limits: {},
  node_device_limits: {},
  speed_limit_mbps: 200,
  device_limit: 3,
  traffic_mode: "twoway",
  traffic_limit_bytes: 137_438_953_472,
  created_at: timestamp,
  updated_at: timestamp,
};

const subscriptionToken = {
  username: "alice@example.com",
  token: "token_1",
  short_code: "abcd1234",
  subscription_url: "http://testserver/api/v1/subscribe/token_1",
  short_url: "http://testserver/api/v1/subscribe/abcd1234",
  created_at: timestamp,
  updated_at: timestamp,
};

const subscriptionPreset: SubscriptionTemplatePreset = {
  id: "vless-vision-tls",
  name: "VLESS Vision TLS",
  description: "VLESS preset",
  protocol: "vless",
  node_type: "physical",
  inbound_tag: "vless-443",
  routed_outbound_tag: null,
  routed_rule_marktag: null,
  tag: "vless",
  tags: ["vless", "tls"],
  client_template: { email: "{username}__vless-443" },
  config: { type: "vless", server: "{server_domain}", port: 443 },
};

const catalogBundle: SubscriptionCatalogBundle = {
  version: 1,
  exported_at: timestamp,
  users: [
    {
      username: "alice@example.com",
      email: "alice@example.com",
      display_name: "Alice",
      role: "user",
      is_active: true,
      current_plan_name: "Premium",
      plan_started_at: timestamp,
      plan_expires_at: null,
      is_reset: true,
      reset_day: 1,
    },
  ],
  nodes: [
    {
      name: "Tokyo vless",
      server_name: "edge",
      protocol: "vless",
      node_type: "routed",
      inbound_tag: "vless-443",
      routed_outbound_tag: "tokyo-out",
      routed_rule_marktag: "route-tokyo",
      tag: "jp",
      tags: ["jp"],
      enabled: true,
      client_template: { email: "{username}__tokyo" },
      config: { type: "vless", server: "tokyo.example.com", port: 443 },
    },
  ],
  plans: [
    {
      name: "Premium",
      description: "Premium routed bundle",
      traffic_limit_gb: 128,
      cycle_days: 30,
      is_reset: true,
      reset_day: 1,
      node_names: ["Tokyo vless"],
      node_multipliers: { "Tokyo vless": 1 },
      node_speed_limits: {},
      node_device_limits: {},
      speed_limit_mbps: 200,
      device_limit: 3,
      traffic_mode: "twoway",
    },
  ],
  credentials: [],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function agentCommand(overrides: Partial<AgentCommand> = {}): AgentCommand {
  return {
    id: "cmd_1",
    server_id: "srv_1",
    request_id: "srv_1-plan",
    method: "POST",
    path: "/api/child/batch-apply",
    query: "",
    body: {},
    timeout_ms: 75000,
    stream: false,
    status: "leased",
    attempts: 1,
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

describe("subscriptions API client", () => {
  it("lists catalog resources without sending license headers", async () => {
    const calls: Array<{ headers: HeadersInit | undefined; url: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input.toString();
      calls.push({ url, headers: init?.headers });
      if (url.endsWith("/users")) {
        return jsonResponse({ users: [productUser], license_required: false });
      }
      if (url.endsWith("/nodes")) {
        return jsonResponse({ nodes: [managedNode], license_required: false });
      }
      return jsonResponse({ plans: [subscriptionPlan], license_required: false });
    };

    const users = await listProductUsers(fetcher);
    const nodes = await listManagedNodes(fetcher);
    const plans = await listSubscriptionPlans(fetcher);

    expect(users.license_required).toBe(false);
    expect(nodes.nodes[0].tags).toEqual(["jp", "premium"]);
    expect(plans.plans[0].traffic_limit_bytes).toBe(137_438_953_472);
    expect(calls).toEqual([
      { url: "/api/v1/users", headers: undefined },
      { url: "/api/v1/nodes", headers: undefined },
      { url: "/api/v1/plans", headers: undefined },
    ]);
  });

  it("creates catalog resources with JSON bodies only", async () => {
    const calls: Array<{ body: unknown; headers: HeadersInit | undefined; url: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const body = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      const url = input.toString();
      calls.push({ url, headers: init?.headers, body });
      if (url.endsWith("/users")) {
        return jsonResponse({ user: productUser, license_required: false }, 201);
      }
      if (url.endsWith("/nodes")) {
        return jsonResponse({ node: managedNode, license_required: false }, 201);
      }
      return jsonResponse({ plan: subscriptionPlan, license_required: false }, 201);
    };

    await createProductUser({ username: "alice@example.com", role: "user" }, fetcher);
    await createManagedNode(
      {
        name: "Tokyo vless",
        server_id: "srv_1",
        protocol: "vless",
        inbound_tag: "vless-443",
      },
      fetcher,
    );
    await createSubscriptionPlan(
      {
        name: "Premium",
        traffic_limit_gb: 128,
        node_ids: ["node_1"],
      },
      fetcher,
    );

    expect(calls.map((call) => call.headers)).toEqual([
      { "Content-Type": "application/json" },
      { "Content-Type": "application/json" },
      { "Content-Type": "application/json" },
    ]);
    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/users",
      "/api/v1/nodes",
      "/api/v1/plans",
    ]);
    expect(calls[2].body).toEqual({
      name: "Premium",
      traffic_limit_gb: 128,
      node_ids: ["node_1"],
    });
  });

  it("assigns plans through encoded user routes and returns provisioning batches", async () => {
    let requestUrl = "";
    let requestBody: unknown;
    const batchBody = {
      inbound_clients: [{ tag: "vless-443", client: { email: "alice__tokyo" } }],
      routing_user_additions: [{ marktag: "route-tokyo", user_email: "alice__tokyo" }],
      no_restart: false,
    };
    const fetcher: typeof fetch = async (input, init) => {
      requestUrl = input.toString();
      requestBody = JSON.parse(init?.body?.toString() ?? "{}") as unknown;
      return jsonResponse({
        user: { ...productUser, current_plan_id: "plan_1" },
        plan: subscriptionPlan,
        provisioning_batches: [
          {
            server_id: "srv_1",
            server_name: "edge",
            body: batchBody,
          },
        ],
        commands: [agentCommand({ body: batchBody })],
        warnings: [],
        license_required: false,
      });
    };

    const response = await assignSubscriptionPlan(
      "alice@example.com",
      {
        plan_id: "plan_1",
        queue_agent_commands: true,
        no_restart: false,
        command_timeout_ms: 75000,
      },
      fetcher,
    );

    expect(requestUrl).toBe("/api/v1/users/alice%40example.com/plan");
    expect(requestBody).toEqual({
      plan_id: "plan_1",
      queue_agent_commands: true,
      no_restart: false,
      command_timeout_ms: 75000,
    });
    expect(response.license_required).toBe(false);
    expect(response.commands[0].path).toBe("/api/child/batch-apply");
    expect(response.provisioning_batches[0].body).toEqual(batchBody);
  });

  it("lists and applies subscription node presets", async () => {
    const calls: Array<{ body?: unknown; headers: HeadersInit | undefined; url: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input.toString();
      calls.push({
        url,
        headers: init?.headers,
        body: init?.body ? JSON.parse(init.body.toString()) : undefined,
      });
      if (url.endsWith("/node-presets")) {
        return jsonResponse({ presets: [subscriptionPreset], license_required: false });
      }
      return jsonResponse({ node: managedNode, license_required: false }, 201);
    };

    const presets = await listSubscriptionTemplatePresets(fetcher);
    const node = await createManagedNodeFromPreset(
      "vless-vision-tls",
      {
        server_id: "srv_1",
        name: "Tokyo vless",
        host: "tokyo.example.com",
        port: 443,
      },
      fetcher,
    );

    expect(presets.presets[0].id).toBe("vless-vision-tls");
    expect(node.node.name).toBe("Tokyo vless");
    expect(calls).toEqual([
      { url: "/api/v1/node-presets", headers: undefined, body: undefined },
      {
        url: "/api/v1/node-presets/vless-vision-tls/nodes",
        headers: { "Content-Type": "application/json" },
        body: {
          server_id: "srv_1",
          name: "Tokyo vless",
          host: "tokyo.example.com",
          port: 443,
        },
      },
    ]);
  });

  it("exports and imports subscription catalog bundles", async () => {
    const calls: Array<{ body?: unknown; headers: HeadersInit | undefined; url: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input.toString();
      calls.push({
        url,
        headers: init?.headers,
        body: init?.body ? JSON.parse(init.body.toString()) : undefined,
      });
      if (url.includes("/catalog/export")) {
        return jsonResponse({ catalog: catalogBundle, license_required: false });
      }
      return jsonResponse({
        summary: {
          created_users: 1,
          updated_users: 0,
          created_nodes: 1,
          updated_nodes: 0,
          created_plans: 1,
          updated_plans: 0,
          imported_credentials: 0,
          warnings: [],
        },
        license_required: false,
      });
    };

    const exported = await exportSubscriptionCatalog(true, fetcher);
    const imported = await importSubscriptionCatalog(
      { catalog: exported.catalog, server_map: { edge: "srv_1" } },
      fetcher,
    );

    expect(exported.catalog.plans[0].node_names).toEqual(["Tokyo vless"]);
    expect(imported.summary.created_nodes).toBe(1);
    expect(calls).toEqual([
      {
        url: "/api/v1/catalog/export?include_credentials=true",
        headers: undefined,
        body: undefined,
      },
      {
        url: "/api/v1/catalog/import",
        headers: { "Content-Type": "application/json" },
        body: { catalog: catalogBundle, server_map: { edge: "srv_1" } },
      },
    ]);
  });

  it("manages subscription tokens and user credentials through encoded user routes", async () => {
    const calls: Array<{ method?: string; url: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input.toString();
      calls.push({ url, method: init?.method });
      if (url.endsWith("/credentials")) {
        return jsonResponse({
          username: "alice@example.com",
          credentials: [
            {
              id: "cred_1",
              username: "alice@example.com",
              node_id: "node_1",
              server_id: "srv_1",
              inbound_tag: "vless-443",
              protocol: "vless",
              email: "alice@example.com__vless-443",
              credential: { id: "uuid", email: "alice@example.com__vless-443" },
              created_at: timestamp,
              updated_at: timestamp,
            },
          ],
          license_required: false,
        });
      }
      if (url.endsWith("/traffic")) {
        return jsonResponse({
          username: "alice@example.com",
          upload: 170,
          download: 300,
          total: 470,
          entries: [
            {
              username: "alice@example.com",
              server_id: "srv_1",
              email: "alice@example.com__vless-443",
              upload: 170,
              download: 300,
              total: 470,
              last_reported_at: timestamp,
              updated_at: timestamp,
            },
          ],
          license_required: false,
        });
      }
      return jsonResponse({ subscription: subscriptionToken, license_required: false });
    };

    const getResponse = await getProductUserSubscriptionToken("alice@example.com", fetcher);
    const createResponse = await createProductUserSubscriptionToken("alice@example.com", fetcher);
    const resetResponse = await resetProductUserSubscriptionToken("alice@example.com", fetcher);
    const credentialsResponse = await listProductUserCredentials("alice@example.com", fetcher);
    const trafficResponse = await getProductUserTraffic("alice@example.com", fetcher);

    expect(getResponse.subscription.short_code).toBe("abcd1234");
    expect(createResponse.license_required).toBe(false);
    expect(resetResponse.subscription.subscription_url).toContain("/subscribe/token_1");
    expect(credentialsResponse.credentials[0].email).toBe("alice@example.com__vless-443");
    expect(trafficResponse.total).toBe(470);
    expect(calls).toEqual([
      {
        url: "/api/v1/users/alice%40example.com/subscription-token",
        method: undefined,
      },
      {
        url: "/api/v1/users/alice%40example.com/subscription-token",
        method: "POST",
      },
      {
        url: "/api/v1/users/alice%40example.com/subscription-token/reset",
        method: "POST",
      },
      {
        url: "/api/v1/users/alice%40example.com/credentials",
        method: undefined,
      },
      {
        url: "/api/v1/users/alice%40example.com/traffic",
        method: undefined,
      },
    ]);
  });
});
