# Legacy Agent Migration

Open Node's independently implemented Agent is the default, license-free
runtime. It uses HTTPS/WSS with node tokens and does not require this legacy
encryption extension. Existing MMWX Agents can connect over WebSocket using
their `securechan-v1` protocol while they are migrated.

## Establish a Controller Identity

Use the deployed controller's Python environment to create a private Ed25519
identity explicitly:

```bash
python -m open_node.agent_identity create /var/lib/open-node/agent-identity/seed
```

For the shipped Compose deployment:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm -T \
  --no-deps --entrypoint python open-node -m open_node.agent_identity create \
  /var/lib/open-node/agent-identity/seed
```

The command creates a private directory and a mode-0600, 32-byte seed owned
by the service account. Existing files are never overwritten. Its output
contains only the public key and SHA-256 fingerprint. Keep the seed in the
private persistent volume and include it in backups.

Set the following in `deploy/.env`, then recreate the service:

```dotenv
OPEN_NODE_AGENT_IDENTITY_FILE=/var/lib/open-node/agent-identity/seed
```

For a non-Compose deployment, set this environment variable before starting
FastAPI. The path must be absolute. An empty or absent setting disables only
the legacy extension, not the independent Agent's normal transport.

If retaining an existing MMWX identity, migrate its raw 32-byte Ed25519 seed
through an administrator-controlled channel instead of creating a different
key. Give the service account ownership, keep mode 0600, and verify its public
key against the old controller's known pin. Do not paste a seed into the web
interface, a command argument, a repository or a support log. This is not a
PEM file, TLS private key or 64-byte expanded Ed25519 private key.

Startup refuses malformed, exposed, symlinked or wrongly owned identity
files. It does not silently regenerate a missing or damaged configured key.
Use `python -m open_node.agent_identity show /absolute/path/to/seed` to inspect
public metadata without modifying the file. The same public key and
fingerprint appear under **Access > Legacy Agent identity**; the private key
and its filesystem path are never returned to the browser.

## Move an Existing Agent

1. Back up the old Agent configuration, runtime configuration and controller
   identity. Keep the previous controller available until recovery is verified.
2. Create the corresponding server in Open Node and obtain its new node token.
   Old controller tokens are not automatically imported or trusted.
3. Verify the new controller's public key through its authenticated Access
   page or the local identity CLI. Do not learn or replace a pin from an
   unauthenticated network response.
4. Update the Agent's configuration, preserving its runtime paths and settings:

   ```yaml
   master_url: https://panel.example.com
   token: <new-open-node-token>
   connection_mode: websocket
   master_public_key: <verified-base64-public-key>
   ```

5. Restart that Agent. Verify its initial scan/config snapshot, telemetry and
   a harmless system-info command before queuing mutations. An existing config
   may require snapshot review; reconnecting does not automatically overwrite
   runtime state.
6. Verify reconnects after controller and Agent restarts. Migrate other nodes
   only after the first node works end to end.

Both `/api/remote/ws` and `/api/v1/agents/ws` support the exchange. A wrong pin
must fail; do not remove `master_public_key` to work around a mismatch. TLS
certificate validation is still required. The legacy envelope is not a
replacement for HTTPS/WSS, operator authentication or careful key handling.
When a signing identity is configured, `/api/remote/ws` requires key exchange
before authentication. A legacy client with an absent or malformed pin cannot
silently downgrade that endpoint to JSON. The independent Agent's native
`/api/v1/agents/ws` endpoint continues to accept its normal TLS-protected JSON.

Legacy MMWX HTTP/pull callbacks, including `X-Key-Exchange`/`X-Encrypted`, are
not the independent Agent's HTTP lease/result API. Switch those existing
Agents to WebSocket as above, or replace them with the independent Agent
using [agent-deployment.md](agent-deployment.md). Do not point an old HTTP-only
Agent at the new lease endpoints and assume reporting proves command support.

The unmodified legacy binary retains its own behavior and runtime settings.
Open Node does not import licensing entitlements or rely on that binary for
the license-free deployment. The independent Agent remains the supported path
for eliminating the old runtime implementation entirely.

## Protocol and Recovery Boundaries

The compatibility layer uses `cryptography` for X25519, Ed25519, HKDF-SHA256
and directional AES-256-GCM. It matches the inspected
[Agent wire implementation](https://github.com/FengYuchen1314/mmw-agent/blob/f2ba522b08d8839b3eaea94f0745e3ab2af71b84/internal/securechan/securechan.go):
the master signs its ephemeral public key, then the Agent authenticates with
its node token inside the encrypted channel. This is compatibility with that
wire format, not a newly designed cryptographic protocol or a security audit
of the legacy binary.

Each connection has fresh ephemeral keys and sequence state. Once a key
exchange succeeds, all application messages must be encrypted binary frames.
Plaintext downgrade, invalid tags, sequence zero, duplicate and out-of-window
packets are rejected. A forged packet cannot advance the receive window.
The initial exchange/authentication has a ten-second deadline, and sequence
numbers never wrap. Stream frames and command acknowledgments use the same
encrypted channel as authentication and RPC requests.

Application messages are limited to 4 MiB of UTF-8 JSON. New oversized commands
and change-set payloads are rejected before queuing. Previously stored,
unattempted oversized commands are retained as `skipped` with a reason, and
their dependents are skipped. Already-attempted oversized work is not resent
or declared complete: its original outcome must be resolved. History remains
readable; reducing a future command's size does not erase earlier records.

Controller identity rotation is an operator migration: preserve the old seed,
update verified Agent pins, and coordinate restarts. Open Node does not perform
automatic trust-on-first-use, key replacement, HTTP fallback or plaintext retry
after a failed encrypted handshake. A legacy binary may attempt its own HTTP
fallback, but those callbacks are not implemented by Open Node. See
[testing.md](testing.md) for verification.
