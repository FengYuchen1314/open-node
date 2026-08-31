# React and Ant Design frontend

The administrator console and subscriber portal use React 19, TypeScript, Vite
and the official `antd` package. The current dependency pins are React 19.2.7,
Ant Design 6.6.2 and `@ant-design/icons` 6.3.2. Vue, Vuetify, Pinia, the Vue
router/compiler and the MDI font are no longer runtime or build dependencies.

The integration follows [Ant Design's Vite guide](https://ant.design/docs/react/use-with-vite/).
It uses standard Layout, Menu, Form, Table, Tabs, Modal, Drawer and feedback
components with the default theme. The probe can choose the built-in light or
dark algorithm. Project CSS handles spacing, responsive layout, code blocks and
SVG charts; it does not replace Ant Design's component appearance. V6-specific
APIs and icon compatibility follow the [official migration guide](https://ant.design/docs/react/migration-v6/).

## Interface language

The current candidate defaults to Simplified Chinese throughout the administrator
console, subscriber portal and independent public Probe. All three entry points
use the official `antd/locale/zh_CN` locale, following
[Ant Design's localization guide](https://ant.design/docs/react/i18n-cn/).
Page language/title, navigation, forms, accessible labels, confirmation warnings,
status labels, empty states and date/number display are localized explicitly.
The prior published React rewrite used English; Chinese acceptance and publication
are tracked separately in [testing.md](testing.md#simplified-chinese-interface).

Localization does not modify API routes, payload keys, enum values, protocol names,
commands, configuration source, user-supplied titles/names or raw diagnostic logs.
`src/i18n/zh-CN.ts` translates display states; it is not a schema or security
validator. API clients first retain their response/secret checks and use the
bounded allowlist in `src/i18n/messages.ts` and `src/services/request-error.ts` for
known errors. Unknown upstream text uses a Chinese context fallback, not raw
provider bodies or credentials. Validation paths expose only known schema fields.

The stock Ant Design two-character button spacing remains enabled. Explicit
operation labels keep accessible names stable without changing that appearance;
built-in confirmation/cancellation controls use the official Chinese defaults.

## Application boundaries

| Route | Workspace |
| --- | --- |
| `/` | Servers, traffic, Agent bootstrap, commands, diagnostics and lifecycle |
| `/subscriptions` | Users, nodes, plans, limits, profiles, invitations and legacy imports |
| `/templates` | Administrator Clash/Surge template workspace |
| `/changes` | Change planning, dispatch, compensation, rollback and acceptance |
| `/config` | Agent configuration, runtime inventory, takeover, snapshots and deployments |
| `/certificates` | Issuance, DNS providers, ACME accounts, jobs, versions and deployment |
| `/probe` | Node status, history, comparisons and private probe administration |
| `/access` | Administrator password, MFA policy and public Agent identity |
| `/account` | Separate subscriber sign-in, subscription links, routes, templates and security |

`src/main.tsx` mounts the application. `src/react/App.tsx` gates management
routes on the administrator session and lazy-loads workspaces from `src/routes.ts`.
The `/account` route loads only the subscriber session; subscriber roles never
grant administrator permissions. Failed workspace loads offer a reload action
without rendering raw exception data.

The framework-independent types and API clients remain in `src/domain` and
`src/services`. FastAPI routes, cookie/CSRF contracts, SQLite state and Agent
protocols are unchanged by this rewrite. A memory-only observable store connects
session updates to React's `useSyncExternalStore`; authentication and installation
secrets are not persisted in localStorage or sessionStorage.

Asynchronous view scopes invalidate callbacks after disposal, target changes or
replacement operations. Sensitive dialogs keep passwords, enrollment QR data,
recovery codes and installation commands local and clear them on completion or
close. Recovery codes require explicit acknowledgment. Mutation guards and
existing backend revisions/confirmation tokens remain in place.

Numeric fields still render the official Ant Design `InputNumber`, through a
small `StrictInputNumber` adapter. It preserves incomplete or invalid drafts
instead of silently clamping a negative quota, rounding a fractional connection
limit, or restoring an old port on blur/Enter. An optional field becomes `null`
only when explicitly emptied; malformed or non-finite input stays invalid.
Each submitting form checks its own backend-compatible bounds. This matters
especially where zero means unlimited. Probe settings and scheduled-task writes
also stay disabled until their initial data has loaded successfully.

Asynchronous action buttons use stable accessible labels matching their visible
text. A loading indicator must not become part of an action's name after a
failed request or prevent assistive technology from finding the retry action.

## Independent public Probe build

`public-probe/main.tsx` produces `dist-probe`. Its compile-time public flag
excludes the administrator settings/task module, router and authenticated API
clients. `src/services/probe-public.ts` sends only read-only public requests with
cookies omitted, no cache, no referrer and redirects rejected. A Worker access
token is a server-side Worker secret, not a frontend environment variable or URL
parameter.

The read-only build retains HTTP polling while WebSocket is idle or reconnecting,
range selection, node filters and ping/system history. The Worker independently
enforces its route/method allowlist and strips credentials in both directions.
The public bundle's deep links remain read-only; `/access` on that deployment
does not become an administrator login page.

## Build and verification

Use the designated VPS and an isolated source checkout, not the production
database or service. The Vite React plugin requires Node 20.19+ or 22.12+; the
pinned Docker Node stage remains the production build path.

```bash
cd /path/to/isolated/open-node/frontend
npm ci
npm run typecheck
npm test
npm run build
npm run build:probe
```

`dist` is still served by FastAPI in the single Docker image. No frontend
development server, separate web host, or change to the root installer is needed.
The optional public Worker deploys `dist-probe` separately.

Vitest retains the domain/API tests and adds real Ant Design DOM behavior tests.
Some jsdom suites skip expensive CSS visibility inference and disable animation
only in their test wrappers; those tests are not responsive-layout evidence.
Production-bundle Playwright gates check actual desktop/narrow-screen rendering,
accessible controls, secret cleanup, MFA, Agent installation and subscription
flows against disposable VPS services. [Testing records](testing.md) distinguish
those results from earlier Vue runs and from unverified external deployments.
