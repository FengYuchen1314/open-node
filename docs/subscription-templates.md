# Subscription Templates

Open Node stores custom Clash/Mihomo YAML and Surge profiles without a license,
activation key, or remote template service. Administrators use `/templates`;
subscribers use the `Templates` tab in `/account` when an administrator enables
personal editing.

## Selection

Each format is resolved independently in this order:

1. The subscriber's enabled, owned default.
2. The assigned plan's template.
3. The system default.
4. The built-in minimal template.

Changing a template or binding does not provision credentials, issue Agent
commands, or restart Xray. Subscription credentials, tokens and traffic remain
unchanged. Plan edits that only change template IDs are metadata-only.

Clash templates replace `proxies` with compatible assigned nodes. The
`__PROXY_NODES__` member expands in place and `__PROXY_PROVIDERS__` adds the
declared providers to `use`; native include/filter options remain client-side.
Group order, rules and provider definitions are preserved. Unknown members,
providers, cycles and generated-name collisions fail before download.

Surge templates replace only non-comment entries in `[Proxy]`. Other sections,
comments, scripts and remote rule/provider declarations are preserved as text;
the backend does not execute scripts or fetch template URLs. `[Proxy Group]`
cycles and explicit missing members fail before export. Generated values are
quoted and node names are normalized to prevent profile injection.

## Ownership And Safety

The library is limited to 200 files, 2 MiB per file and 16 MiB per catalog
payload. Filenames are case-insensitively unique and cannot contain paths or
control characters. YAML uses a safe loader with duplicate-key, alias-expansion,
cycle and depth limits. Rendered subscriptions are limited to 8 MiB.

Administrators can own, publish and bind any template. Subscribers can read
public templates but can edit only their own private files after permission is
enabled. Personal defaults must reference owned templates, not public files
owned by somebody else. User removal deletes the preference and leaves owned
files private with no owner; it never turns a private file public.

Writes and removals use content/binding revisions. Assigned files cannot be
removed until plan and default references are cleared. Catalog export stores
filenames rather than database IDs and import remaps plan/default references in
one transaction. Legacy catalogs that omit all template fields preserve the
existing library and bindings.

## API

Administrator routes are under `/api/v1/subscription-templates`; subscriber
routes mirror them under `/api/v1/account/subscription-templates`:

- `GET /`, `GET /{id}`, `GET /{id}/file`: list, inspect and download.
- `GET /starter?format=clash|surge`: obtain the built-in starter.
- `POST /`, `PUT /{id}`, `POST /{id}/remove`: guarded file lifecycle.
- `POST /preview`: render a draft without saving it.
- `GET /settings`, `PUT /settings`: system or personal defaults and permission.

Administrator settings accept `?username=...`; subscriber settings are always
scoped to the authenticated account. All responses are `no-store`, downloads
require the appropriate session, and subscriber writes require CSRF.

## Verification Boundary

The VPS smoke runs custom Clash output in pinned Mihomo and forwards real TCP
and UDP traffic for every compatible fixture node. It also generates a custom
Surge subscription from the real public endpoint, checks the exact compatible
node set, parses sections/groups independently, and exercises both browser
workspaces at 1440, 390 and 320 pixels.

Surge is proprietary macOS/iOS software and is not available on the Linux VPS.
The conversion follows the published Surge policy manuals and receives strict
server-side tests, but an actual Surge application import remains a separate
Apple-platform release gate. See [subscription formats](subscriptions.md) and
[VPS testing](testing.md#subscription-client-smoke).
