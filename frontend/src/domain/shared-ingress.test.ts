import { describe, expect, it } from "vitest";

import type { SharedIngressRoute } from "./shared-ingress";
import {
  normalizeSharedIngressSni,
  normalizeSharedIngressUpstream,
  sharedIngressConfiguration,
  sharedIngressWebsiteDraft,
  validateSharedIngressDraft,
} from "./shared-ingress";

const routes: SharedIngressRoute[] = [{
  node_id: "11111111-1111-4111-8111-111111111111", profile: "vless-reality-vision",
  sni: "node.example.com", upstream_address: "127.0.0.1", upstream_port: 62041,
}];

describe("shared ingress domain", () => {
  it("normalizes exact SNI and absolute safe HTTP(S) upstreams", () => {
    expect(normalizeSharedIngressSni(" BÜCHER.example. ")).toBe("xn--bcher-kva.example");
    expect(normalizeSharedIngressSni("*.example.com")).toBeNull();
    expect(normalizeSharedIngressSni("bad_name.example")).toBeNull();
    expect(normalizeSharedIngressUpstream(" https://origin.example/app?q=1 ")).toBe("https://origin.example/app?q=1");
    expect(normalizeSharedIngressUpstream("https://user:secret@origin.example/app")).toBeNull();
    expect(normalizeSharedIngressUpstream("https://origin.example/app#private")).toBeNull();
  });

  it("requires globally unique SNI and reserves a separate internal website port", () => {
    const website = { ...sharedIngressWebsiteDraft(null), enabled: true, sni: "node.example.com", upstream_url: "http://127.0.0.1:8080", certificate_name: "site-cert", tls_port: 62041 };
    expect(validateSharedIngressDraft(routes, website)).toEqual(expect.arrayContaining([
      "网站 SNI 与节点路由重复，请更换域名。", "网站内部 TLS 端口无效或与节点运行端口冲突。",
    ]));
  });

  it("defaults HTTP redirection on and creates only a validated 443 declaration", () => {
    const website = { ...sharedIngressWebsiteDraft(null), enabled: true, sni: "SITE.example.com", upstream_url: "https://origin.example/app", certificate_name: "site.example.com" };
    expect(website.redirect_http).toBe(true);
    expect(sharedIngressConfiguration(null, routes, website)).toEqual({
      listen_port: 443, listen_ipv6: true, routes,
      website: { sni: "site.example.com", upstream_url: "https://origin.example/app", certificate_name: "site.example.com", redirect_http: true, tls_address: "127.0.0.1", tls_port: 62044 },
    });
    expect(sharedIngressConfiguration(null, [], { ...website, enabled: false })).toBeNull();
  });
});
