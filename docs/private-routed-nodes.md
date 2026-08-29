# Private routed nodes

Private routed nodes let a subscriber combine two physical nodes from their current plan:

- the entry node accepts the subscriber's existing protocol credential;
- the exit node is rendered server-side as an Xray outbound;
- a user-scoped routing rule joins the entry to that outbound.

The feature is free and has no license check. Administrators control whether creation is enabled,
the maximum routes per subscriber, and the daily create/delete action limit.

## Safety boundary

Subscribers submit only a label plus entry and exit node IDs. Open Node constructs the outbound
from managed inventory and never accepts subscriber-supplied Xray configuration. Both nodes must
be enabled physical nodes in the subscriber's current plan, and the entry must have an authenticated
inbound tag.

## Durable lifecycle

Creation and deletion use coordinated Agent change sets with ordered rollback commands. The local
node, owner, generated credential, lifecycle state, change set, and commands are recorded in one
database transaction before any command can run.

A route enters subscriptions only after every creation command succeeds. Failed creation remains
disabled and auditable. Deletion removes the client before the routing rule and outbound; retries
are idempotent, while rollback restores the outbound and rule before restoring the client.

Disabling, expiry, or quota exhaustion removes runtime access through the normal subscription-access
reconciler while preserving the credential for later restoration. User deletion remains pending until
all owned private routes and runtime credentials have been withdrawn.
