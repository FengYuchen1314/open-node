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
npm install
cp .dev.vars.example .dev.vars
npm test
npm run typecheck
```

## Deploy

`deploy` builds the dedicated Vue public app before invoking Wrangler:

```bash
npm --prefix ../frontend install
cd ../probe-worker
npm install
npx wrangler secret put MMWX_ORIGIN
npx wrangler secret put PROBE_TOKEN
npm run deploy
```

To build only the static bundle without deploying it, run `npm run
build:assets`; the output is `frontend/dist-probe`.

Generate `PROBE_TOKEN` from the Open Node Probe settings panel or with:

```bash
curl -X POST https://your-origin.example/api/v1/probe/access-token
```
