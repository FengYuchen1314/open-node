# Administrator Two-Factor Authentication

This feature protects the single local Open Node administrator. Subscriber
accounts retain their separate authentication and TOTP settings; neither
account type gains the other's permissions. Multi-administrator roles, security
event reporting, and an IP-ban console remain outside this feature.

## Configure and Use

Set `OPEN_NODE_SUBSCRIBER_TOTP_KEY` to a persistent Fernet key before enrollment.
Despite its historical name, this key now encrypts both subscriber and
administrator TOTP secrets. The Compose environment template leaves it empty:
a fresh installer deployment does not automatically enable MFA. Store the key
privately with the deployment configuration and include it in encrypted backups.
Generate it with `cryptography.fernet.Fernet.generate_key()` in the backend
environment; do not publish it, commit it, or replace it during an ordinary update.

In **Access → Administrator security**:

1. Select **Enable** and confirm the current password.
2. Scan the QR code with an authenticator, or enter the displayed secret.
3. Enter the current six-digit authenticator code to activate MFA. An unconfirmed
   secret never replaces an active factor; setup expires after ten minutes and
   only the session that started it may confirm it.
4. Save the ten one-time recovery codes somewhere private, then acknowledge them.
   They are shown once. The database stores only identity-bound hashes.

Subsequent password logins return a short-lived challenge, not an authenticated
session. Enter an authenticator code or unused recovery code to finish sign-in.
The challenge expires after five minutes or five failed verification attempts.
Second-factor submissions also share a persistent ten-per-minute account budget
across IP addresses and newly issued challenges. Unknown challenges do not consume
that account budget. Password login, verification, and authenticated security
changes additionally share the existing ten-per-minute source-IP limit.

Authenticator codes use 30-second intervals with one adjacent interval of clock
tolerance. A successfully used interval cannot be reused: wait for a new code
before another sensitive operation, or use a different recovery code.

**Require 2FA** prevents password-only access when no factor is enrolled. Enable
MFA before turning this policy on. If the policy is already active but a factor
is absent, login requires enrollment before issuing a session. Turn the policy
off before disabling a factor. Policy changes, disabling MFA, and generating
replacement codes require both the current password and a factor proof.

Enrollment, factor removal, recovery-code replacement, and policy changes revoke
other administrator sessions and outstanding login challenges. A normal Access
password change keeps the enrolled factor, revokes all sessions/challenges, and
cancels pending enrollment.

## Lost Authenticator or Encryption Key

Using a recovery code consumes only that code; MFA remains enabled. To replace a
lost authenticator, log in with one recovery code, make the policy optional if
necessary, disable the old factor, and enroll the replacement. Each protected
operation needs a fresh authenticator or recovery code. Replacement codes
invalidate the entire previous set.

If the encryption key is missing or replaced, TOTP verification fails closed.
Unused recovery codes still work because they do not require decryption. Recover
the original deployment key if possible: replacing it also affects subscriber
factors. The administrator CLI reset does not repair subscriber MFA.

If no recovery codes remain, use the local administrator recovery command over
SSH, following [administrator access](administrator-access.md). There is no
anonymous remote reset API. Back up the database and private configuration
first; the CLI does **not** make a backup or discover Docker deployments for you.
It uses the database selected by its process configuration.

`open-node-admin reset-password` revokes all administrator sessions and challenges,
removes the administrator's active/pending TOTP and recovery codes, clears the
shared login-limit records, and disables the mandatory-administrator-MFA policy.
It does not remove subscriptions or subscriber accounts. Re-enroll MFA after
recovery. This is deliberately more disruptive than a signed-in password change.

## Reference Behavior and Deliberate Differences

The implementation uses the supplied control-plane source at
`c12ce653bc07fe30426b7dfcb85076974b7be0e0`, including its
[TOTP/challenge implementation](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/auth/totp.go)
and [two-factor handlers](https://github.com/tajiaoyezi/miaomiaowuX/blob/c12ce653bc07fe30426b7dfcb85076974b7be0e0/internal/handler/two_factor.go).
It is a behavior reference, not a byte-compatible port:

- The reference recovery-code login disables TOTP and clears the set. Open Node
  keeps MFA enabled and consumes one code atomically.
- Open Node uses ten 80-bit recovery codes and encrypted TOTP storage, persistent
  hashed challenges bound to the password version, and replay protection.
- Mandatory administrator enrollment follows the interactive security example
  in the [official system-settings documentation](https://miaomiaowux.com/docs/en/system-settings/).
  This policy was not found in the pinned source's security-settings/login paths;
  it is a documented-product addition, not claimed source parity.
- The [official recovery instructions](https://miaomiaowux.com/docs/en/faq-common-ops/)
  also require local server access and clear MFA when resetting a lost password.
  Their automatic deployment discovery, backup, and multi-admin selection are
  not implemented by Open Node's single-administrator CLI.

## Verification

Run `backend/tests/test_auth.py` on the designated VPS for expiry, replay,
concurrent consumption across independent stores, key-loss recovery,
cross-IP/challenge budgets, password-reset revocation, and session-bound setup.
The production-browser gate is `scripts/vps/smoke-administrator-mfa.py`; see
[testing](testing.md) for the isolated environment and release evidence.
