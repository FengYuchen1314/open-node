# Subscription IP access

Each product user can restrict durable subscription downloads to at most 32
IPv4 or IPv6 hosts and CIDR networks. An empty policy is unrestricted. Host
addresses are stored canonically as `/32` or `/128`; networks are normalized to
their network address and duplicates are removed.

The policy covers the user's long token, generated or custom short code, and
compatible legacy `/x` profile links. A rejected source receives the same `404`
response as an unknown subscription, so the policy does not disclose whether a
token exists. Temporary administrator-created shares keep their separate
recipient and lifetime semantics and are not governed by the source user's
policy.

Administrators edit the policy from the Users list. Subscribers can edit only
their own policy from Account > Security; writes use the isolated subscriber
cookie, Origin check and CSRF token. Policies are controller-only and do not
rotate credentials or restart an Agent.

## Source address

Open Node evaluates the ASGI client address. The bundled deployment places
Nginx on the same host and Uvicorn trusts its loopback proxy headers. For another
reverse proxy, configure Uvicorn's trusted forwarded IPs to that proxy's exact
address or network. Never trust forwarded headers from every source: doing so
would let a direct client spoof an allowed address.

Policy rows live in `subscription_ip_policies` and are removed with their
product user. Upgrades create this table without changing existing user,
subscription or traffic records; all existing subscriptions remain unrestricted
until a policy is saved.
