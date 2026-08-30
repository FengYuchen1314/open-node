import assert from "node:assert/strict";
import test from "node:test";

import worker from "./index.ts";

test("hardens public assets without forwarding an asset cookie", async () => {
  const response = await worker.fetch(
    new Request("https://probe.example/"),
    {
      ASSETS: {
        fetch: async () => new Response("<html>probe</html>", {
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Set-Cookie": "origin_session=secret; Secure; HttpOnly",
          },
        }),
      },
    },
  );

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(await response.text(), "<html>probe</html>");
});

test("proxies only public data routes and strips credentials in both directions", async () => {
  const originalFetch = globalThis.fetch;
  let upstreamRequest;
  globalThis.fetch = async (input, init) => {
    upstreamRequest = new Request(input, init);
    return new Response('{"servers":[]}', {
      headers: {
        "Content-Type": "application/json",
        "Set-Cookie": "admin_session=secret; Secure; HttpOnly",
      },
    });
  };

  try {
    const response = await worker.fetch(
      new Request("https://probe.example/api/v1/public/probe-servers?region=eu", {
        headers: {
          Authorization: "Bearer browser-secret",
          Cookie: "browser_session=secret",
        },
      }),
      {
        MMWX_ORIGIN: "https://origin.example/control",
        PROBE_TOKEN: "worker-secret",
      },
    );

    assert.equal(upstreamRequest.url, "https://origin.example/control/api/v1/public/probe-servers?region=eu");
    assert.equal(upstreamRequest.headers.get("authorization"), null);
    assert.equal(upstreamRequest.headers.get("cookie"), null);
    assert.equal(upstreamRequest.headers.get("x-mmwx-probe-token"), "worker-secret");
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("does not expose administrator API paths through the SPA fallback", async () => {
  let assetRequests = 0;
  const response = await worker.fetch(
    new Request("https://probe.example/api/v1/public/probe-settings"),
    {
      ASSETS: {
        fetch: async () => {
          assetRequests += 1;
          return new Response("unexpected");
        },
      },
    },
  );

  assert.equal(response.status, 404);
  assert.equal(assetRequests, 0);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
});
