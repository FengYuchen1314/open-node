# Open Node Architecture

## Repository Boundary

Open Node is intentionally a single repository. Backend, frontend, deployment
scripts, and verification utilities live together so feature contracts can be
changed atomically.

```text
open-node
|-- backend   FastAPI application and tests
|-- frontend  Vue 3 + Vuetify application and tests
|-- docs      migration notes and architecture decisions
`-- scripts   VPS bootstrap and verification helpers
```

## Product Boundary

The refactor tracks the active MMWX product line only:

- Control plane: `FengYuchen1314/miaomiaowuX`
- Agent: `FengYuchen1314/mmw-agent`
- Probe: `FengYuchen1314/mmwx-probe`
- Xray integration fork: `FengYuchen1314/Xray-core-mmwx`

The older `miaomiaowu` project and archived `NodeControll` rebuild are not
inputs for this implementation.

## No-License Contract

Open Node may have an open source software license, but the runtime product must
not require a license key. The backend exposes this as API data and tests assert
that:

- `license_required` is always `false`.
- paid entitlements are disabled.
- no external license server is configured.
- no feature gates are returned.

Future feature work should add capability flags only for availability, health,
configuration, or compatibility states. It must not introduce paid unlocks.

## Initial Runtime Shape

The backend serves JSON APIs under `/api/v1`. The frontend is a Vite application
that can point to the backend with `VITE_API_BASE_URL` or use same-origin API
paths in production.
