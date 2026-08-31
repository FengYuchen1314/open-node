# Notifications: first-slice design and implementation boundaries

Status: implemented, not published. Unified regression, production-browser and
working-tree Docker gates passed; exact-Git-revision Docker and clean-checkout CI
remain pending. See the [operator guide](notifications.md) and [test record](testing.md).
Administrator Telegram configuration, preview, test and package-expiry reminders
are one independent slice, not full notification or renewal parity.

## Pinned reference and separate workflows

The control-plane reference is `tajiaoyezi/miaomiaowuX` at
`c12ce653bc07fe30426b7dfcb85076974b7be0e0`. Relevant entry points:

- [notify_config.go:107](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/notify_config.go#L107)
  owns administrator settings, preview and explicit test requests.
- [notify_global.go:7](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/notify_global.go#L7)
  holds the shared notifier;
  [notify/telegram.go:18](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/notify/telegram.go#L18)
  sends to Telegram. Reuse the workflow, not its HTTP-status-only success check.
- [notify_scheduler.go:115](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/notify_scheduler.go#L115)
  scans administrator package reminders at 09:00. Its username/date deduplication
  is in memory and marks before sending; neither behavior is suitable here.
- [telegram_binding.go:73](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/telegram_binding.go#L73)
  issues a one-use, 24-hour binding invite. Separately,
  [handlers_notify.go:19](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/tgbot/bot/handlers_notify.go#L19)
  implements user opt-in, a server-local 20:00 digest and 7/3/1-day reminders.
- [user_renewal.go:41](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/user_renewal.go#L41)
  claims a manual approval request before extending and provisioning a package.
  It prefers a future current expiry, then a future request-snapshot expiry,
  otherwise now. Approval is not payment-provider confirmation.
- [notify_server_renewal.go:92](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/notify_server_renewal.go#L92)
  can infer renewal from being online after a reset date. Do not reproduce that
  inference: neither online status nor traffic reset proves payment or renewal.

## First-slice contract

Support one administrator bot/chat destination, disabled by default. Provide
configuration GET/PUT, preview POST, test POST, delivery-status GET and explicit
retry POST under a new administrator-only `/notifications` route group.

Saving, reading settings, opening the page and previewing must never send inline.
Enabling reminders authorizes the independent scheduler; after local 09:00 it may
scan and dispatch soon after a save. This is not a promise of no background sends.
Preview uses the same formatter and eligibility rules as delivery; label sample
data explicitly when no real candidate exists. A test sends fixed text only on
an explicit click, even if scheduled reminders are disabled. Test/retry requests
use a client request UUID and return a repeatable task ID; polling cannot resend.

Expose a reminder switch, advance days (1-365, default 7), named timezone and
09:00 local scheduling boundary. Warn that subscriber names, plan names and
expiry times will leave the control plane for the selected Telegram chat.
Show accepted, failed and unknown outcomes, with duplicate-risk confirmation for
manual retry. Do not equate Telegram acceptance with a subscriber reading it.

User binding/opt-in, the 20:00 digest, threshold/server alerts, announcements,
webhooks, bot commands and manual renewal approval remain separate later slices.
There is no payment processing, automatic extension or license gate in this plan.

## Existing integration points

- [main.py](../backend/app/open_node/main.py): add an independent notification
  worker to `lifespan`, with bounded shutdown and persistent recovery. Register
  its store on app state and include these endpoints in `secret_request`.
- [api/router.py](../backend/app/open_node/api/router.py) and
  [api/auth.py](../backend/app/open_node/api/auth.py): use `private_router` and
  existing administrator session, CSRF and exact-Origin checks.
- [services/inventory.py](../backend/app/open_node/services/inventory.py): read
  `ProductUserModel.current_plan_id`, `plan_expires_at`, activity/removal state
  and `SubscriptionPlanModel`. Notification models use separate metadata and
  create their own five tables after inventory schema initialization.
  `assign_subscription_plan()` defaults to start plus cycle, not extension of
  an existing future expiry; notification code must not call it.
- Eligible users are active, not being removed, and have a current plan with a
  future expiry inside the window. Do not require quota `available`: an
  over-quota subscriber can still need an expiry reminder. Revalidate at send.
- `ServerModel.expires_at` can support later server reminders; provider prices,
  heartbeat and reset dates are metadata, not a payment ledger. Existing
  `subscription_user_traffic()`/quota totals are already weighted; never apply
  multipliers again. [server_traffic.py](../backend/app/open_node/services/server_traffic.py)
  uses UTC daily buckets, not arbitrary timezone-local yesterday totals.

## Persistence, transactions and delivery outcomes

Add independent configuration, outbox and attempt tables in the existing SQLite
database. Configuration needs a CAS revision, switches, encrypted bot token,
numeric chat ID string, advance days, IANA timezone, local time and destination
revision. Outbox rows need a unique semantic event key, source identity/expiry,
target snapshot, status and next-attempt timestamps. Attempts need a request
UUID, lease/fencing token, start/deadline/result timestamps, fixed error code and
optional Telegram message ID; retain earlier attempts when retrying.

The event key includes kind, user incarnation (`username` plus `created_at`),
plan ID and the full canonical UTC expiry, not only its date. Renaming a plan,
toggling reminders or rotating a token must not resend an accepted event.
Same-name recreated users must not inherit another user's deduplication state.
Retain deduplication while an event can still qualify, even if pruning history.

Use short transactions for unique enqueue, CAS claim, source/config revalidation
and receipt updates. Commit `sending` with its attempt token before HTTP; never
hold a database write lock across network work. Destination changes only reroute
unsent work; in-flight/unknown attempts retain their original target identity.
Disabling cannot recall a request already sent. Cancel stale unsent events when
the user disappears, removal starts, or the plan/expiry changes.

`InventoryStore._coordinated_session()` locks Server rows outside SQLite; it is
not a general notification lock. Use actual notification-row CAS/locking and
unique constraints, following [external_subscriptions.py](../backend/app/open_node/services/external_subscriptions.py)'s
short-write pattern. Keep SQLite as the deployment boundary until other database
behavior has separate verification. Never send from a request-scoped background
task, subscription download, Agent heartbeat or provisioning transaction.

Accept only a bounded valid `ok=true` response with a Message receipt, not HTTP
200 alone. Known 400/401/403 failures require configuration action; a valid 429
can use bounded `retry_after`. Only failures proven to precede request sending
may retry automatically. Read timeout, partial write, ambiguous 5xx/response or
crash after `sending` means `unknown`, not a fresh queue item.

Recover unstarted claims; do not blindly replay `sending` after restart. Wait
out the old request deadline/lease before confirmed manual retry, preserve its
attempt and fence late receipts. The documented [Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage)
has no client idempotency parameter: acceptance followed by a lost receipt or
local commit failure cannot be made exactly-once by a local outbox alone.

## Time, secrets and outbound boundary

Use `ZoneInfo`, default `Asia/Shanghai`, and store instants in UTC. Poll each
minute after the configured local 09:00 boundary; compare the future expiry
against now plus N local calendar days. Persist one event per current expiry.
Startup after the boundary immediately rescans current candidates through the
same deduplication; do not replay missed historical days or send "soon" for an
already expired plan. Timezone changes and repeated/skipped DST hours must not
reset accepted events. Display the actual expiry with its timezone.

Use a separate private notification key directory, not TOTP/certificate/external
keys. Follow [certificate_vault.py](../backend/app/open_node/services/certificate_vault.py)
and the external store's purpose-bound encryption: 0700 directory, 0600 key,
atomic creation, initialized marker and missing/wrong-key failure without data
replacement. Back up and restore the database and key together. GET returns
`has_token`, never token suffixes; writes explicitly keep, replace or clear it.

Allow only `https://api.telegram.org:443/bot<TOKEN>/sendMessage`, with bounded
ASCII token validation and a numeric chat ID. No configurable endpoints,
redirects, inherited proxies, TLS bypass or transparent transport retries.
Bound connect/total time and response bytes; send bounded plain text in POST JSON
without Markdown, links, subscription credentials or certificates. Disable link
previews and paid broadcasts. Apply per-chat pacing, including the conservative
group limit in the [Telegram FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

Never log full URLs, tokens, HTTP exception strings, response descriptions or
bodies; the token is part of the URL path, including HTTP-client debug logs.
Persist only fixed safe error codes. Reuse the [strict request reader](../backend/app/open_node/api/routes/external_subscriptions.py)
pattern for body size, duplicate fields and non-echoing validation; preserve
no-store/referrer protection. Do not retain tokens in frontend persistent state.

## Required gates before claiming implementation

All runtime gates belong in a new isolated VPS directory, not production or the
frozen candidate. Add executable tests for SQLite concurrent claims, expiry and
identity changes, quota-independent eligibility, clock/DST/restart boundaries,
and crash injection before send, after acceptance and before receipt commit.
Assert unknown results do not auto-resend and late receipts cannot overwrite a
new attempt. Verify no Agent command, package expiry or billing mutation occurs.

Use a real local HTTP/TLS fixture with test-only transport injection to exercise
429, false-success 200, redirects, oversize responses and disconnect after
acceptance. Assert fixed destinations, bounded retries and secret-free logs.
Production-browser gates must cover save/preview making zero sends, double-click
idempotency, safe unknown retry, authorization/CSRF, Chinese text and mobile UI.
Docker gates must cover non-root key permissions, restart, missing/wrong keys
and database-plus-key restoration. Record source hashes and actual outcomes.

A real Telegram canary requires separately authorized credentials and a test
chat. Fixtures do not prove real Telegram acceptance. Store, transport, API,
unified regression, production-browser and working-tree Docker gates passed in
isolated VPS directories. Exact-commit Docker and CI still precede publication.
No real Telegram delivery has been performed for this slice.
