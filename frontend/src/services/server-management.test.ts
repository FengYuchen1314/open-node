import { describe, expect, it } from "vitest";
import { getServerRemoval, getServerSettings, removeServer, updateServerSettings, type RemovalPreview } from "./server-management";

describe("server management API", () => {
  it("uses guarded settings and removal payloads", async () => {
    let count = 0;
    const settings = { name: "edge", domain: "edge.example", domain_v6: null, ip_address: null, ip_address_v6: null, ipv6_enabled: true };
    const fetcher: typeof fetch = async (url, init) => {
      expect(url.toString()).toBe("/api/v1/servers/id/" + ["settings", "settings", "removal", "remove"][count]);
      expect(init?.method ?? "GET").toBe(["GET", "PUT", "GET", "POST"][count]);
      if (count === 1) expect(JSON.parse(String(init?.body))).toEqual({ ...settings, expected_revision: "revision", sync_node_hosts: false });
      if (count === 3) expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: "revision", confirm_name: "edge", acknowledge_remote_runtime: true });
      count += 1;
      return new Response(JSON.stringify({ revision: "revision" }));
    };
    await getServerSettings("id", fetcher);
    await updateServerSettings("id", settings, "revision", false, fetcher);
    const preview = await getServerRemoval("id", fetcher);
    await removeServer("id", preview, "edge", fetcher);
    expect(count).toBe(4);
  });

  it("keeps stale-state and authentication errors visible", async () => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({ detail: "Removal details changed" }), { status: 409 });
    await expect(getServerRemoval("id", fetcher)).rejects.toThrow("Removal details changed");
    await expect(removeServer("id", { revision: "old" } as RemovalPreview, "edge", fetcher)).rejects.toThrow("Removal details changed");
  });

  it("shows field validation without echoing submitted input", async () => {
    const fetcher: typeof fetch = async () => new Response(JSON.stringify({ detail: [
      { loc: ["body", "domain"], msg: "Use a hostname", input: "private-input" },
    ] }), { status: 422 });
    await expect(getServerSettings("id", fetcher)).rejects.toThrow("domain: Use a hostname");
  });
});
