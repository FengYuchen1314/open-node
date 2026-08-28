# WARP Outbounds

Open Node provides free Cloudflare WARP registration and Xray outbounds without
an Open Node activation key or subscription. WARP+ is an optional, separate
Cloudflare account credential. A free device's provider-issued license string
does not make it a paid account. Cloudflare service availability and terms still
apply; Open Node does not grant access to paid Cloudflare features.

## Operations

The independent Agent supports the existing child paths:

| Method | Path | Body |
| --- | --- | --- |
| POST | `/api/child/warp/install` | `{"accept_terms":true}` for first registration |
| GET | `/api/child/warp/status` | None |
| POST | `/api/child/warp/license` | `{"license":"operator-owned-WARP-plus-key"}` |
| POST | `/api/child/warp/remove` | `{"confirm":true}` |

The control-plane wrappers are POST requests below
`/api/v1/servers/{server_id}/operations/warp/`, with the same action names.
The install wrapper defaults omitted consent to false. The Agent refuses a new
registration without literal boolean true. The remove wrapper also requires
literal boolean true. Existing registered devices can be refreshed/reapplied
with an empty install body, without creating another account.

The Dashboard offers install and removal confirmation, links the
[Cloudflare application terms](https://www.cloudflare.com/application/terms/),
and polls command completion. WARP+ input is optional and masked. Status shows
account type, interface addresses, registration time and local configuration
state; it does not claim a live tunnel handshake or Internet reachability.
Registration and heartbeats update the inventory's `warp_installed` flag.
Legacy heartbeat messages that omit this flag do not reset it.

## Routing And Ownership

Registration creates two tagged outbounds, `warp-v4` and `warp-v6`, using
`ForceIPv4v6` and `ForceIPv6v4` respectively. These are family preferences with
fallback, not strict family-only filters. Existing default outbounds, inbounds
and routing rules are preserved. Select the WARP tags in the existing routing
editor to send specific traffic through them; installation alone does not route
all users through Cloudflare.

The pinned official Xray runtime implements WireGuard. Open Node sets MTU 1280
and `noKernelTun:true`, explicitly selecting the userspace gVisor implementation.
No `wg-quick`, host interface, default route, sysctl or `CAP_NET_ADMIN` is needed.
See the [Xray WireGuard configuration](https://xtls.github.io/en/config/outbounds/wireguard.html)
and the [pinned TUN selection code](https://github.com/XTLS/Xray-core/blob/v26.3.27/proxy/wireguard/config.go).

Unmanaged tag collisions, duplicate tags and manual edits to owned outbound
definitions are rejected, not overwritten. Restore those definitions or migrate
the conflicting tags explicitly. Removal refuses outstanding route references,
balancer selectors/fallbacks, observatory selectors, proxy references, and use of WARP as the first/default
outbound. Change those settings first; removal must not silently turn a WARP
route into direct traffic.

## Persistence And Failure

`state_dir/warp.json` is an Agent-owned, mode-0600 file containing the device
identity, access token, WireGuard keys, provider configuration and last applied
outbounds. The Agent state directory remains private. Symlinks, hardlinks,
non-regular files, incorrect ownership and broad permissions are rejected.
Corrupt state is not silently discarded or replaced with a new registration.
It reports an error while ordinary Agent authentication and heartbeat continue.

The provider identity is persisted before peer parsing and Xray application,
so an incomplete configuration or failed activation can be refreshed or removed
without creating another device. Xray validates the full candidate before file
replacement. A private durable undo record coordinates state and configuration;
failed activation restores original files, and interrupted changes are recovered
before normal runtime startup. An intentionally stopped runtime remains stopped.

Removal first commits the local outbound removal, then requests provider device
deletion. A provider failure leaves `phase:removal_pending` and retains credentials
for retry. A crash cannot restore already-revoked outbounds. The local account
file is removed only after provider deletion succeeds or returns 404. Ordinary
Agent uninstall preserves this state along with other user data; explicitly
remove WARP before purging an installation if remote device deletion is wanted.

Provider calls use a fixed HTTPS API, certificate verification, no environment
proxy, no redirects, bounded deadlines and a 64 KiB response limit. Request and
provider error bodies are not echoed into status or logs. Device access tokens
are not sent to the control plane. Optional WARP+ keys are not stored in the
Agent account file; they are still part of the administrator's control-plane
command record, like other privileged command inputs.

WireGuard private keys necessarily appear in the owned Xray configuration.
An authorized full-config read, edit or snapshot can therefore contain them.
Protect configuration exports, database backups and administrator access. WARP
status/operation results and normal Agent logs do not expose these keys. UI
masking of WARP+ command input is not database encryption.

## Verification Boundary

The VPS-only smoke installs a non-root systemd Agent and uses verified HTTPS/WSS
and HTTP polling over HTTPS. A local TLS provider fixture supplies registration
responses, including nonzero reserved bytes, and an actual official Xray
WireGuard peer forwards real IPv4/IPv6 traffic. This checks the Agent's complete
registration, configuration and forwarding path, but does not prove Cloudflare's
public registration API, production network, or a real WARP+ account works.

The smoke never registers a Cloudflare device or accepts third-party terms.
Live provider registration/deletion requires explicit operator consent and
remains a separate release gate. The consumer API is not the supported
Cloudflare Zero Trust API and may change or reject third-party clients. A timeout
before a registration response leaves the remote outcome unknown: there is no
blind HTTP retry or guaranteed remote registration idempotency. Inspect provider
state before deliberately issuing a new registration command after such a failure.

See [testing.md](testing.md#native-warp-smoke) for the reproducible fixture command.
