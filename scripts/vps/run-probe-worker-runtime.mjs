#!/usr/bin/env node
// Test-only adapter for the Miniflare v5 API locked by probe-worker/package-lock.json.
// The Python smoke supplies a private config and a real Wrangler dry-run bundle.
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { basename, dirname, resolve } from "node:path";

if (process.platform !== "linux" || process.argv.length !== 3) {
  throw new Error("Run through smoke-public-probe-worker.py on the isolated Linux VPS");
}

const input = JSON.parse(await readFile(process.argv[2], "utf8"));
const require = createRequire(resolve(input.workerDirectory, "package.json"));
const { Miniflare } = require("miniflare");
const wrangler = input.wrangler;
const bundle = await readFile(input.bundlePath, "utf8");
const moduleName = basename(input.bundlePath);
const stopped = new Promise((done) => {
  process.once("SIGINT", done);
  process.once("SIGTERM", done);
});

// Using Miniflare directly avoids Wrangler's development-only ProxyController.
// Keep production asset routing and the compiled user Worker unchanged. This
// still uses Cloudflare's native asset service and workerd, not a JS fetch mock.
const runtime = new Miniflare({
  host: "127.0.0.1",
  port: input.port,
  cf: false,
  telemetry: { enabled: false },
  logRequests: false,
  resourceTmpPath: input.workDirectory,
  workers: [{
    config: {
      type: "worker",
      name: "open-node-probe-smoke",
      compatibilityDate: wrangler.compatibility_date,
      compatibilityFlags: wrangler.compatibility_flags,
      manifest: {
        mainModule: moduleName,
        modulesRoot: dirname(input.bundlePath),
        modules: { [moduleName]: { type: "esm", contents: bundle } },
      },
      env: {
        [wrangler.assets.binding]: { type: "assets" },
        MMWX_ORIGIN: { type: "text", value: input.bindings.MMWX_ORIGIN },
        PROBE_TOKEN: { type: "text", value: input.bindings.PROBE_TOKEN },
      },
      assets: {
        directory: wrangler.assets.directory,
        hasUserWorker: true,
        runWorkerFirst: wrangler.assets.run_worker_first,
        notFoundHandling: wrangler.assets.not_found_handling,
        htmlHandling: wrangler.assets.html_handling,
      },
    },
    dev: { rootPath: input.workDirectory, unsafeRegisterWorker: false },
  }],
});

try {
  await runtime.ready;
  console.log("Local Miniflare/workerd ready");
  await stopped;
} finally {
  await runtime.dispose();
}
