# Open Node Probe Worker

This Cloudflare Worker publishes the read-only public probe surface without
exposing the Open Node origin probe endpoints directly.

## Routes

The worker accepts only `GET` requests and forwards these paths to the Open Node
origin:

| Worker path | Origin path |
| --- | --- |
| `/api/probe` | `/api/v1/public/probe-servers` |
| `/api/series` | `/api/v1/public/probe-series` |
| `/api/targets` | `/api/v1/public/probe-targets` |
| `/api/stream` | `/api/v1/public/probe-ws` |
| `/api/public/probe-servers` | `/api/v1/public/probe-servers` |
| `/api/public/probe-series` | `/api/v1/public/probe-series` |
| `/api/public/probe-targets` | `/api/v1/public/probe-targets` |
| `/api/public/probe-ws` | `/api/v1/public/probe-ws` |
| `/api/v1/public/probe-servers` | `/api/v1/public/probe-servers` |
| `/api/v1/public/probe-series` | `/api/v1/public/probe-series` |
| `/api/v1/public/probe-targets` | `/api/v1/public/probe-targets` |
| `/api/v1/public/probe-ws` | `/api/v1/public/probe-ws` |

All cookies and authorization headers are stripped before proxying. The worker
adds `X-MMwx-Probe-Token` from the `PROBE_TOKEN` secret so an Open Node origin can
enable `Worker token` mode and return `404` for direct public probe access.
Upstream `Set-Cookie` headers are removed, every Worker response receives
`X-Content-Type-Options: nosniff`, and unknown `/api/*` paths return JSON `404`
instead of falling through to the SPA.

The Worker publishes `frontend/dist-probe`, a dedicated read-only build. It does
not publish the control-plane router, sign-in shell, Probe settings, Worker-token
controls, private server picker, or scheduled-task controls. The shared
`ProbeView` runs in `publicOnly` mode and calls only the public server, series,
target-comparison, and stream routes listed above.

## Local Check

```bash
cd probe-worker
npm ci
cp .dev.vars.example .dev.vars
npm test
npm run typecheck
```

## Deploy

From the repository root, with your own Cloudflare account and a reachable HTTPS
Open Node origin. `deploy` builds the dedicated Vue public app before invoking
Wrangler:

```bash
npm --prefix frontend ci
npm --prefix probe-worker ci
cd probe-worker
npx wrangler secret put MMWX_ORIGIN
npx wrangler secret put PROBE_TOKEN
npm run deploy
```

To build only the static bundle without deploying it, run `npm run
build:assets`; the output is `frontend/dist-probe`.

Generate `PROBE_TOKEN` from the signed-in Open Node Probe settings panel, enable
Worker-token access and store that token as a Worker secret. Never put it into
the public Vue bundle. The underlying `POST /api/v1/probe/access-token` endpoint
requires administrator authentication and CSRF proof; it is not an anonymous
token-creation API.

## Browser Acceptance

The isolated VPS gate in `scripts/vps/smoke-public-probe-worker.py` compiles the
real Worker with Wrangler's dry-run mode and runs it directly in the locked
Miniflare/workerd runtime. It tests production assets, anonymous API/WebSocket
access, credential isolation, polling/reconnect and desktop/mobile layouts.
See [testing](../docs/testing.md#public-probe-worker-acceptance-2026-08-31) for
commands and the documented Wrangler development-proxy workaround. This is not
proof of a deployment into your Cloudflare account.
