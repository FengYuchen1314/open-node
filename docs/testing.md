# Testing

All tests for Open Node run on the VPS at `185.99.135.224` over SSH. Local work
is limited to editing and static inspection.

GitHub also runs the same repository-level language gates on every push and
pull request through `.github/workflows/ci.yml`: backend Ruff/pytest, Agent
Ruff/pytest/wheel build, frontend Vitest plus both the administrator and public
Probe production bundles, and Probe Worker behavior tests/type checking.
Actions are pinned to immutable revisions and receive only read access to
repository contents. This hosted CI is an independent clean checkout check; it
uses the configured Python interpreter under `sudo` for the Agent tests because
host-policy fixtures exercise real file ownership changes to a service UID.
Dependency installation, linting and wheel building remain unprivileged. The
privilege applies only to GitHub's disposable hosted test runner, not deployment.
Hosted CI does not replace the root-only Docker installer smoke, systemd lifecycle
smokes, protocol-runtime builds, or real forwarding checks on the designated
VPS.

## Remote Test Command

### Administrator Telegram notifications — unpublished integration

This is the first administrator-only notification slice described in the
[operator guide](notifications.md), not full Telegram Bot or renewal parity.
It is working-tree code after public `caf016c`; no notification code is in that
published commit and the production container has not been upgraded.
No real Telegram credentials or destination have been used.

The new root integration environment is
`/tmp/open-node-notifications-integration.v2sQeZ5w`. Its private Python virtualenv
was installed from the public baseline plus `tzdata`; other candidates' environments
are not installed into or rewritten. Source archives and failed rounds are retained.

| Component gate | Verified result |
| --- | --- |
| Durable store and domain | 108 passed, no skips, 16.804 s; strict Ruff and compile passed in `/tmp/open-node-notifications-store.CpeUz24C/source-r5`. Store SHA-256 `e36d28f43d299b42499d58c566777883717fb95a75c658ffebf1f840d1b6ed84`; test SHA-256 `e38977aa3c8529962db240d6d353ea36a40f617b5cad7f5b037dad8c4b8afc00`. Includes concurrent claims/CAS, full-expiry/user-incarnation dedupe, DST, quota-independent eligibility, unknown/late receipts, bounded safe retries, and missing/wrong-key refusal to clear ciphertext. |
| Telegram transport | 206 passed, no skips, 9.53 s; Ruff and compile passed in `/tmp/open-node-telegram-transport.w31Bc3jN/r3`. Real loopback HTTP/TLS fixtures run inside a fresh network namespace with no external route. Module SHA-256 `ac7d323cd34bfd8b8a64136df935742b76a20de9abbd111b68094983f4b0d9a6`; test SHA-256 `cd701429e5091266fe97adc7d22a90373c4de38da8e7efaa97f239a181e29bb3`. |
| API and worker integration | Root `source-r2`: 77 passed, 57.43 s, `evidence/root-focused-r2.log` and XML. Covers permissions, strict non-echoing request validation, idempotent request lookup/retry, actual app lifespan, cancellation and recovery. One root test exceeded the lint line-length limit; that formatting-only error was corrected in the next snapshot. Two additional real missing/wrong-key API tests passed in the unified run below, not these 77 results. |
| Unified backend | Root `source-r4`: **2298 passed, no skips**, 1023.21 s; strict Ruff and compile passed. Includes all six opt-in external-fetch TLS tests and the combined notification store, transport, API and worker. `evidence/backend-full-r4.log` SHA-256 `84a69f71a9ff176e688efba739c191c4a51501edcccdf431b853326f35b7d8bb`; XML SHA-256 `6dfae1fe69f67e3bfd199f7063d56fea7b3fec3a43fed95b380339a3dc8d39bf`. One Starlette/httpx deprecation warning is retained. |

Store tests read real inventory records and verify all non-notification tables
remain unchanged. Network tests include verified TLS/SNI, invalid certificates,
proxy/CA/key-log environment isolation, strict bounded HTTP framing, false-success
200, valid/invalid 429, redirection, disconnects and cancellation. They are
controlled fixtures, not a canary proving Telegram acceptance.

The root's first full-suite attempt (`source-r3`) stopped during collection:
the backend-only archive omitted `scripts/vps/sync-and-test.py`, which a backend
test imports. The error and XML are preserved in `evidence/backend-full-r3.*`;
this is not a full-suite pass. The replacement `source-r4` is a complete 530-file
repository snapshot, archive SHA-256
`d0a6148466ee94a57bd42bdb24ba4945f1ca1d0eea431255543c87fe7bd935dc`.
Its strict backend Ruff/compile and complete suite passed in a fresh
loopback-only namespace.
An assigned public-format address exists only inside that namespace, not on a
new host listener. No product SSRF/TLS bypass is enabled.

All 530 source-file hashes and the private Python dependency inventory remained
unchanged through the full run. The later unified `source-r5` contains 531 files,
archive SHA-256
`9e0ea5c8ecd4c637fc0d7a900a3b5fa1e678588becaf32e12a793c7404676916`.
Its backend application, tests and `pyproject.toml` are byte-identical to the
passed R4 source; the two `evidence/backend-executable-r*.sha256` manifests agree.
The R5 frontend type check and both production builds passed. Its complete
Vitest run passed **890 tests in 72 files**, no failures or pending tests,
995.86 s, in `frontend-gate-r5`. The original test process continued after an SSH
transport disconnect; a read-only recovery check found the same PID, growing
log, no host restart and no kernel OOM record. It was not rerun or counted as a
test failure. Native `full-vitest.json` SHA-256 is
`0f771f2a974e41f27b104454a72fe5aae3d0a6ed8d90a203b62b26f0f6104e28`;
log SHA-256 `2b71513bc40ee2ee37d03763f604f6876105fb1ad065c51f848bc1df73663569`.
All 531 source files, 24,024 shared dependency files and the 39 administrator / 3
Probe assets stayed unchanged. Combined asset manifest SHA-256 is
`39491685f0277eec9be2250e208817d4980d29f39ac91eafbcce1107ea8c3f61`.

The production-bundle browser and working-tree Docker gates below have passed.
Clean-commit CI and the exact-Git-revision Docker gate remain pending.
Earlier Chinese/external gates further below are separate
evidence and must not be relabeled as notification acceptance.

#### Notification production-browser gate

`scripts/vps/smoke-notifications-browser.py` passed in
`/tmp/open-node-notifications-browser-r4.H23Kcoyq`, using an independent copy of
the frozen R5 product and its built assets. The executed fixture SHA-256 is
`7210494597bc55d0102aa8aa80a170ee3789236b422a66092dc1e620a3f1b09a`;
strict Ruff and compile passed. This **browser R4** is not backend `source-r4`.

All nine phases passed: default-off/no-key behavior; token input clearing at
request start; save/preview making no sends; confirmed double-click creating one
durable request; lost POST-response reconciliation through read-only GETs;
clearing configuration cancelling only unsent work while retaining attempt
snapshots; real subscriber login followed by administrator-API 401 and separate
CSRF/Origin 403 checks; an actual 40-second worker lease recovering to unknown
without automatic replay; risk/target confirmation, configuration CAS and old
attempt rejection on manual retry; late acceptance updating only the old attempt;
and a real over-quota expiry candidate using the preview formatter, scheduler and
restart deduplication. The phase count groups related checks, not individual
assertions. Logout and expired-session UI flows were not newly exercised here.

The fixture uses the actual application, store and worker with a trusted local
transport replacement in a fresh loopback-only network namespace. It records
6 transport calls with 6 committed-claim checks, one deliberately failed receipt
commit and one late receipt. Product clock, lease and timeout constants are not
shortened. These are **not** real Telegram deliveries or acceptance evidence.

Console errors and page errors were both zero. All 12 viewport PNGs were checked
individually at 1440, 390 and 320 pixels: default settings, unknown-result warning,
retry confirmation, and preview/history. Text and controls fit; wide tables keep
their own horizontal scroll. Source/assets, both read-only Python environments,
the earlier frozen R4 source and production fingerprints remained unchanged.
The namespace had zero owned processes and zero listeners after cleanup.
The read-only `durable-receipt-audit.json` confirms that all six attempt leases
were 40.0 seconds, recovery occurred after 40.36 seconds, and the late accepted
receipt did not change the newer unknown attempt or its current identity.

Evidence hashes:

- `evidence/report.json`: `0834a7096d9bac0a22141642d68394f70dc196a164b39ed1df43b6b405429ee9`.
- `screenshots.sha256`: `17759b6f9392986b552a084d29e7fa1113e28df3d40a696a02b3377834166f6c`.
- `screenshots.tar.gz`: `4a6d8b14dae70f96cad02b9192c27c96f2a80960f5845b26a8b5baf9ffb47e39`.
- `visual-qa.json`: `3992c220d02ce0400baf04651fdce39226acec58cf413c30e807f00fd0283bf4`.
- `final-evidence.sha256`: `27d0cbfa2d5642aea62a2db0b771b9a6abcd256dc1c31c5dc70e39a2bb48aa62`.

The earlier browser attempts remain failed evidence: R1 selected Ant Design's
hidden accessibility option instead of the visible dropdown; R2 checked the
lost-response fixture before the POST completed; R3 used `/account/profile`
instead of the real `/account/me` identity endpoint. Before R4, a further static
review corrected the fixture's retry expectation to the actual HTTP 200 contract.
Only the smoke script changed between those attempts; product source did not.

#### Notification working-tree Docker gate

The final `scripts/vps/smoke-notifications-docker.py` fixture SHA-256 is
`4610687487d15a7c88209e3ec3cc92411a4893b0ccdb644845f4ab9a3d461a34`.
It passed all 16 phases in
`/tmp/open-node-notifications-docker-r5.dFPBVH4K/docker-r3` with image
`open-node:notifications-working-tree-r5`, ID
`sha256:2a0aa26bcce8bcddc034fa00bcbba9fc9beb6fb013336bca7bf2f8c63eaea796`.
Its OCI revision is **`working-tree-caf016c-notifications-r5`**, not a Git SHA.

It verified UID/GID 10001, read-only root, no capabilities, no-new-privileges,
`--network none`, all 39 administrator assets (1,789,471 bytes), and the
`/notifications` SPA index. Default-off/no-key, encrypted persistence/private
permissions, request idempotency, original-session restart, independent cold
backup/restore, missing/wrong-key refusal across restart, preservation of
ciphertext while disabling, and restoration of the original key all passed.
No real Telegram host was reachable and no delivery was marked accepted.

Six explicit stops completed within 0.30 seconds each. They returned SIGTERM/143;
the fixture requires matching start/finish PID logs from **that current start**,
one completed application shutdown, no OOM/error, and completion within the
30-second grace period. Uvicorn 0.52.4 in the image matches the
[official version's signal re-raise behavior](https://github.com/Kludex/uvicorn/blob/0.52.4/uvicorn/server.py).
Seventeen independent positive/negative checks cover this stop criterion.
The initial R1 failure from incompatible Docker local-log options and R2 failure
from an exit-zero-only fixture remain preserved; neither was relabeled a pass.

The seven label-owned containers and three disposable volumes were removed;
an independent second check found no leftovers. All 531 R5 source files, 39
assets and 424 protected production/shared-candidate files remained unchanged.
The production container was not restarted or upgraded. Evidence hashes:

- `report.json`: `5f737c803554c5f94356efbcd31d785c53d758a3095602c1a50d7700c44c3598`.
- `independent-postcheck.json`: `7dac7f0a97740ff96227a29c322b332e94204643762c2a0b7669809a3af929e8`.
- `executed-source.tar`: `0baf9494cff019f64572c4017783243a7299f61cb05bc5252556e3edc4871447`.
- `final-evidence.sha256`: `206c49cf1349355abfe3dc6eb9bed418659d62903bfbe205d85d5542fff4a11b`.

### External subscriptions

This gate concerns the new administrator-managed, explicitly confirmed
Clash/Mihomo YAML source workflow. It is separate from the already published
React rewrite and does not establish full subscription-ecosystem parity.
The **English-language baseline**, before the subsequent Chinese UI requirement,
is frozen in
`/tmp/open-node-external-integration.YG95YRYU/source` on the VPS, based on
`0ffc07215244abcf69fb8e6935171082e0522747` plus the external-source changes.
The full-suite results below belong to that frozen source. Chinese UI work is
tested in new private directories; the English snapshot is not overwritten.
Neither these working-source checks nor the working-tree Docker image establish
a published Git revision or acceptance of the Chinese interface.

Focused checks passed: the parser's **402 tests** and Ruff, the fetcher's
**230 tests** including all six actual TLS cases, and the store/API integration
suite's **48 tests**. The final TLS rerun uses a new private network namespace
with only loopback; it does not bind the VPS public interface. Evidence:
`parser-source-options-after.log` in `/tmp/open-node-external-parser.6U8AFhSs`,
and `external-fetch-tls-r7.log` / `external-store-r6.log` in the integration
directory. The 48-test focused run predates the parser's final default-field
refinement; the complete backend run below covers the combined final source.

The combined English baseline completed these independent gates:

| Gate | Result and evidence |
| --- | --- |
| Backend | 1933 collected; **1927 passed, 6 skipped**, 853.21 s, `backend-full-r7.log`. All six opt-in real-TLS cases separately passed within the 230-test fetcher run in `external-fetch-tls-r7.log`. One Starlette/httpx warning retained. |
| Agent | **605 passed**, 10.92 s, `agent-full-r7-shortpath.log`. An earlier deep temporary path exceeded Linux's AF_UNIX limit; the unchanged suite passed with a fresh short private basetemp. Both raw logs remain. |
| Frontend | **570 tests / 65 files passed**, 883.85 s, `frontend-gate.E8vXRaoF/vitest.json`; SHA-256 `42bf4632e1977c67cc5275dc2e9cba93120b29a8f2212c76fb77cc584847e687`. Type checking and both production bundles passed. |
| Probe Worker | **5 passed** and type checking passed, `worker-r7.log`. |
| Lint/package | Backend and Agent Ruff passed, `backend-agent-ruff-r7.log`; the Agent wheel built privately without replacing any published release asset. |

Paths in that table are relative to `/tmp/open-node-external-integration.YG95YRYU`.
The backend/Agent/Worker product-and-test manifest remained identical across the
runs (208 files; SHA-256
`f4af434404e6ef030624b0f1585041fbd63cbf9041a9cabf95aef6d451e869ea`).
The frontend's 170-file source/configuration manifest also remained identical;
the 38 administrator and 3 public-Probe artifacts matched the browser bundle.
Ant Design row-key, Probe SVG-height and bundle-size warnings are retained in
the logs, not suppressed or counted as failed assertions.

The English Docker gate in `/root/open-node-external-docker-r7.b457dZfx` passed
using `open-node:external-working-tree-r7`, image ID
`sha256:09f49a038d68c93462aceea862199455608b326a504498caa26d194630486b47`.
Its OCI revision is **`working-tree-0ffc072-external-r7`**, not a Git commit.
The final `docker-evidence-r3/report.json` SHA-256 is
`c59673ed57db55d420bccf70247427abea7cd65a146f098b4c78745b11ecab79`.
It checked UID/GID 10001, a read-only/capability-dropped container, all 38 assets,
10 SPA routes, three viewport sizes, original sessions/data across restarts,
encrypted external-source persistence in the existing private volume, a real
verified-HTTPS child fetch with safe rejection of non-YAML input, missing raw
secrets in logs/argv, and unchanged production identity. Only its own labeled
temporary container and volume were removed. Earlier fixture-only header/error
shape mistakes and their cleanup reports remain separate from this final pass.

The production-bundle browser and native-client gate passed on that combined
source in `/tmp/open-node-external-browser-r3.XaVySQeW`. Its `evidence/report.json`
records every backend application file and frontend asset SHA-256; the report
SHA-256 is `925896e5ff2f07daa5bd9f4dd61cbc506b5ea2e397dacf712db6c6fb406aae84`.
The final parser fingerprint in this source is
`681e8769f2b7751411deba917bb942c4e0c6d267a2b55b42508483ed1f66d341`.

`scripts/vps/smoke-external-subscriptions.py` enters a fresh Linux network
namespace and verifies its identity and loopback-only netlink state before
opening any listener or assigning an address. Its local HTTPS provider uses
the normal public-IP/TLS fetch path, with a fixture-only trusted CA; no product
private-address or insecure-TLS bypass is enabled. It exercises:

- Real browser source creation, write-only editing, explicit fetch, selecting
  new nodes, acknowledging existing-node changes, confirmation and receipt
  recovery. Fifteen masked screenshots cover source/node forms, preview,
  confirmation footers and saved details at 1440/390/320 pixels.
- Complete primary-subscription loading in Mihomo v1.19.30 and real managed and
  external VLESS traffic through official Xray v26.3.27. The destination rejects
  direct traffic; both the original and rotated upstream credentials work
  through the selected proxy. Other parser-supported protocols are not claimed
  as traffic-tested by this fixture.
- Credential rotation, new/missing nodes, owner isolation, read-only preview,
  identical confirmation retry, real TLS/gzip, HTML/empty/redirect/gzip-bomb
  rejection and preservation of the active snapshot.
- Unchanged managed catalog/credentials/ledger, encrypted database contents,
  missing-key fail-closed without replacement, cold database plus key-directory
  restoration and the original administrator session.

The fixed native binary digests are checked before execution:

```text
Mihomo 1.19.30  8ad44e28fe72be4640254b96741b677f4074991b99186cc4486a1c28ded02b1a
Xray 26.3.27   8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed
```

Reproduce only on the isolated VPS, with the backend's browser dependencies and
Chromium installed in a private test environment. Build the administrator
frontend first, then use the verified native binaries and a new evidence path:

```bash
PYTHONPATH="$PWD/backend/app" /path/to/private/venv/bin/python \
  scripts/vps/smoke-external-subscriptions.py \
  --mihomo /path/to/verified/mihomo --xray /path/to/verified/xray \
  --output /absolute/new/private-evidence
```

Temporary runtime processes, TLS keys, databases and the namespace are removed
when the fixture exits; only the masked screenshots and source-bound report
remain. Error handling emits the failure stage/type and source locations, never
raw Playwright errors that could contain a password or provider URL. None of
these tests upgrades the production container or proves public HTTPS,
PostgreSQL concurrency, a customer provider's reachability, or off-site backup.

### Simplified Chinese interface

The later user requirement makes Simplified Chinese the default for the
administrator console, subscriber portal, public Probe, document language/title
and Ant Design's built-in component text. The implementation uses the official
`antd/locale/zh_CN` provider and explicit display labels, not DOM replacement.
API paths, enum values, protocol/configuration names, commands, raw diagnostics
and operator-provided content are unchanged. New Probe settings use Chinese
defaults; existing customized titles/descriptions are not rewritten.

The first unified Chinese product snapshot is frozen at
`/tmp/open-node-zh-release.fp33Igbt/source-r2`. The source archive
`source-zh-final-r2.tar` SHA-256 is
`e105396bbd5e215fa26f05478c2b1d760d1d9d911707f1b8a60aafbe12f10ff2`.
Subsequent browser-fixture selector/format corrections have separate manifests;
they do not rewrite that archive. The later certificate-message correction,
R4 source, exact-commit clean image and completed publication are recorded below.
The English baseline above is earlier evidence, not relabeled as Chinese acceptance.

| Unified working-source gate | Result and evidence |
| --- | --- |
| Frontend | **762 tests / 70 files passed**, 819.43 s; no failed/skipped tests. `frontend-full-r2.json` SHA-256 `fe312372e1802185f446f67f68bb716f4fb0295fd1376cd65a6194eb33f8cab6`. Includes all 36 external-panel tests and the final Chinese conflict/legacy-import warnings. |
| Frontend builds | Forced TypeScript project check and both main/Probe Vite builds passed: `typecheck-r2.log`, `build-main-r2.log`, `build-probe-r2.log`. |
| Backend | 1933 collected, **1927 passed / 6 opt-in skipped** in `/tmp/open-node-zh-integration.3ISDjgiA/backend-full-zh-r1.log`, SHA-256 `eeef2d6a2fcbcfdf44d0b56536f4d38099169da9962d7d278ce7ba059b657129`. The progress log and node-ID cache agree; `backend-verified-source-r2-match.log` proves the frozen R2 backend is identical. Ruff passed. |
| Real TLS fetcher | **230 passed, no skips**, 4.49 s, `external-fetch-tls-r1.log`; all six opt-in TLS cases ran in a new verified loopback-only network namespace, without a product SSRF or TLS bypass. |
| Agent | **605 passed**, 10.07 s, `agent-full-zh-r2.log`, plus Ruff. A fresh short private basetemp avoids the known AF_UNIX path limit. Agent source and published release assets are unchanged. |
| Probe Worker | **5 passed**, 180.996 ms, and type checking passed: `worker-tests-r2.log`, `worker-typecheck-r2.log`; private source with read-only dependency reuse. |

Unless otherwise qualified, paths in this section are relative to
`/tmp/open-node-zh-release.fp33Igbt`. The frontend source manifests before and
after the full run both hash to
`788b7cba49d20e9a6ff8b7929429ef6185d0a30dbcf2a1aead2472e1419e7d98`.
Backend, Agent and Worker before/after manifests also match. The final **41
assets** (38 main, 3 Probe) are in `frontend-assets-r2.tar.gz`, SHA-256
`2188112fa06f80cb12692e95ac71aa60c4826bafca5c10f190019a577016fe55`;
`frontend-assets-r2.sha256` hashes to
`480e182eeed4c37070c0275490639626a56ab61046640956ac990408d819f662`.
The following R2 real-browser gates use these exact assets, checked before/after:

| Chinese production-bundle workflow | Passing evidence and scope |
| --- | --- |
| External subscriptions | `external-browser-r2/report.json`, SHA-256 `6f69dc2171d1b9c9c2ae16021749fe22c3e6abd21b367e99d37bda344edb1c24`; 15 masked 1440/390/320 screenshots, preview/confirm/receipt, credential rotation, managed plus external VLESS forwarding, ownership and key/DB restoration. All boundary checks described in the English gate were rerun in a fresh namespace. |
| Operator UI | `operator-browser-r2.log` and `operator-browser-r2/`; 16 screenshots. Server creation, Nginx paths/sites, tunnel fields, certificate import/EAB forms/downloads, Probe tasks/tokens, Agent fingerprint, password change and expired-session handling. This is not a real certificate-issuance or tunnel-deployment gate. |
| Administrator MFA | `/tmp/open-node-zh-admin-mfa-gate.nUeYE2Ru/gate.log`, SHA-256 `171be7c1ea13432fafb5c707d6711a5d7930f4f214bb9e5a726e2acb8a2e5707`; enrollment, acknowledgment, challenge, mandatory policy, recovery, regeneration, disable and local CLI recovery; eight masked screenshots. |
| Subscriber account | `/tmp/open-node-zh-account-websocket.2j9JXUsQ/gate.log`, SHA-256 `ad2c9fa7806a729eab631b304df5ef25e101ff520a9edd0437bde5c2be7db41f`; 55.98 s, 12 masked screenshots. Real billing/forwarding, MFA/replay/recovery, sessions, password/link reset, administrator recovery and isolation. This Chinese rerun uses WebSocket only; prior English HTTP coverage stays separate. |
| Legacy import | `/tmp/open-node-zh-legacy-gate.NEfsSiok/gate.log`, SHA-256 `34908bbced1aa3498a8cd8bb4e1e3be3d7d2030c3f0cb0a004c8d545d5e7cd24`; four screenshots, mapping/confirmation safeguards, visible administrator-to-subscriber warning, legacy links, real stock-Xray forwarding, TOTP/recovery and foreign-key integrity. The role value itself is asserted by backend tests. |
| Bootstrap tickets | `/tmp/open-node-zh-bootstrap-browser.UsEJ81C4/evidence/report.json`, SHA-256 `61b1661e9dc0290f9e327318f26a31baa586f7db1cb4d5c4a9c712bcf870f3af`; 12 screenshots, replacement/reissue invalidation, revocation and synthetic registration UI. This rerun does not claim natural ticket expiry or a newly installed systemd Agent. |
| Anonymous Probe Worker | `/tmp/open-node-zh-worker-r2.ZrdktcNX/evidence/report.json`, SHA-256 `9668465c9950edd4e9bce8716300bd22c2ed6c1770be3e2e82b7474ac06b0c3d`; nine screenshots, HTTP/WS aliases, no credentials/cookies, private/mutation rejection, malformed frames/reconnect, idle fallback polling, ranges/themes/deep links. Actual Miniflare on the VPS, not a Cloudflare account deployment. |

The Chinese source-built Docker gate passed in
`/root/open-node-zh-docker.3r5SMaqB` with image
`open-node:zh-external-working-tree-re105396b`, ID
`sha256:3d08d1fe00f156d56b94bf451ddf1a8c6d62a563db714f1fef0e9c733d33d702`.
Its OCI revision is **`working-tree-0ffc072-zh-external-r2`**, not an exact Git
commit. The `evidence-r1/report.json` SHA-256 is
`99edc7ca6f83b91bff60a3a3b01f98b7ef4e9de35bdd99142036c2cd9b69d256`.
Fresh `npm ci` and the image build reproduced all 38 main assets byte for byte.
The gate checked UID/GID 10001, read-only root/capability drop, ten SPA routes
and reserved 404s, three viewports, original sessions and rows across restart,
encrypted-source key permissions/persistence, verified outbound HTTPS with safe
non-YAML rejection, and no raw secrets in logs/argv. Its labeled temporary
container and volume were removed after ownership checks; production did not
change.

The first Chinese certificate-administration browser gate stopped on a nested
Ant Design selector. Its corrected R2 fixture passed real certificate operations,
but visual inspection found a successful already-revoked receipt translated as
failure. The R2 `visual-review.json` in
`/tmp/open-node-zh-certificate-admin-r2.KG0Xt0St` explicitly records semantic
failure; it is not a final Chinese pass. The first full subscription-client gate
also stopped at Clash VLESS-gRPC TCP before reaching templates. Failures remain
at `/tmp/open-node-zh-certificate-admin.eJ5qvqzH` and
`/tmp/open-node-zh-clients.z2DJK0Gr`. The original gRPC failure's cause is unknown;
later success must not be relabeled as a proven protocol fix or environment cause.

Earlier Chinese focused runs (including the interrupted 62-test subscription
run) and the intermediate R1 bundle remain historical, not extra tests added to
762. Production retains its original image, data volume and service identity;
none of these fixtures verifies public HTTPS or external account deployment.

#### R4 certificate messages and final-source reruns

The current product snapshot is `/tmp/open-node-zh-release.fp33Igbt/source-r4`.
Its `source-zh-final-r4.tar` SHA-256 is
`5c8d6008d20c692710e9e4718b935e87a3558c2172f400c5dbb6d9ccf6fdec04`.
Relative to R2, only the Chinese message dictionary and two frontend test files
change: 18 precise translations cover the remaining fixed certificate-worker
outcomes; all 22 fixed/bounded values are tested, including success, skipped,
queued, unknown and failure. No substring match accepts arbitrary provider text.
The focused **187 tests passed**, adding **34** tests (27 service and 7 React).
Evidence: `/tmp/open-node-zh-cert-receipt.VsZWRAxp/focused.json`, SHA-256
`edfb3d19db3ada8b510a710efaca2c52a48a5f20ceceb60e97a4c5ace0cf562c`.

Forced type checking and both R4 production builds passed. Initial type checking
with a cross-directory dependency symlink raised TS2742 in the unchanged
`renderUi` helper; using an identical private physical dependency copy passed,
without changing application or helper code. Both logs remain. The frontend
source manifest hashes to
`612b5ad954e65cd5496f81d6b6d1c4572c0d22a935a31dc3d5aa43920d33d075`.
The final 41-asset manifest `frontend-assets-r4.sha256` hashes to
`9ba29231b866707dfe9afa4205bd1f2090f0e37cb761e360f3f135ef126ab6cd`;
`frontend-assets-r4.tar.gz` hashes to
`f9b1a53884fdae6a884f74b2377cc0ef82fbcad3470dcb04125e566cd4baa4f7`.
`backend-verified-source-r4-match.log` proves the complete previously tested
backend is identical. Agent and Worker code are unchanged.

The combined R4 frontend run passed **796 tests in 70 files**, 852.45 s, with
zero failed or skipped tests. `frontend-full-r4.json` SHA-256 is
`80fa75de132b3a20cf8053f9640ec8b9cc9fa46af9f1cb9e36ae7bd3146f8968`.
Source and all 41 assets matched their manifests after the run. These 796 tests
include the 34 added regressions; focused counts are not added a second time.

The following R4 gates have also completed:

- External subscriptions: `external-browser-r4/report.json` under the release
  directory, SHA-256
  `f310ce18ff5ec70abf2033eab35daed35dd7936536e3d7a7300b81ba8a5a97b6`.
  Repeats the full real HTTPS/Mihomo/stock-Xray, secret/ownership, failure
  preservation, rotation and cold-restore gate with 15 new masked screenshots.
- Certificate administration: `/tmp/open-node-zh-certificate-final-r4.Iygak2KU`,
  49.17 s; real HTTP-01/EAB, account update, renewal, version revocation,
  unknown-result retry/reconciliation and backend restart recovery passed.
  All 15 screenshots passed layout and Chinese semantic review, including a
  same-row success-state/precise already-revoked receipt assertion. `report.json`
  SHA-256 `c98aebc6231c0a727237a0f05d7657b1ecbc789d01ca8678e502f36f7777e8f8`;
  `visual-review.json` SHA-256
  `afeb9cc1c07bae3021581466d9ad9c5740d4a7b88e365cbb00fcf8022d3efbd3`.
  This gate does not cover remote certificate deployment or validation-host
  selection in the creation form. Namespace, temporary processes and data were
  fully cleaned, with no host DNS or production changes.
- Full subscription clients and templates:
  `/tmp/open-node-zh-clients-r4.zcFROMyb`, 58.417 s, exit 0. All 154 labeled
  TCP/UDP checks passed across default/custom Clash, sing-box, selected Xray,
  URI list and Base64; the unselected full Xray export also forwarded.
  Compatibility reports, node URLs, stale-response isolation, template
  permissions/CAS and identity stability passed, with nine masked screenshots.
  `gate.log` SHA-256
  `f53e7f507859c37f2cc08f6a20c891ee13aaa17126abafb030a80c7cf2a8a4ad`.
  New diagnostic metadata preserves the original curl command, timeouts and
  exact response-body equality; no failed probe occurred in this run. The first
  gRPC failure was not reproduced. All 299 product files, 49 Python fixtures,
  41 assets and six pinned native inputs remained unchanged, and the owned
  Agent root/unit/user were removed. A second independent full run at
  `/tmp/open-node-zh-clients-r4.drS6Jzhj` also passed, 55.561 s, with identical
  source, assets, native inputs, assertions and timeouts and no failed TCP probe.
  Its `gate.log` SHA-256 is
  `232be0e3fde2e483ea7893b1ef75b25f282c8f11b15206fb5e0da27ca6875a2a`.
  Both complete repetitions retain the original failed run and unknown cause.
- Operator UI: `operator-browser-r4.log` and `operator-browser-r4/` under the
  release directory; the entire R2 operator scope was rerun with 16 new
  screenshots, unchanged source/assets and exit 0.
- Anonymous Probe Worker: `/tmp/open-node-zh-worker-r4.L4wUF4T5/evidence/report.json`,
  SHA-256 `56c4bc924389a54d4f6996ae7406db0edf8d1dea9959ee1ff7fdf391f02ef854`;
  same full local Miniflare HTTP/WS/polling/reconnect/security/range/theme/deep-link
  scope, exit 0. Source, fixtures, assets, dependencies and production remained
  unchanged; all nine screenshots passed visual review. `visual-qa.json` SHA-256
  `b09bb90656940d6047e1ecf38d7fd33e5d5aff3f5603bffa0b691a6e0306dc7d`.
  This is still not a Cloudflare account deployment.

All 28 changed Python browser/native fixtures pass strict E/F/I/UP/B Ruff and
byte compilation in R4; backend and Agent Ruff also pass. The changed-fixture
manifest SHA-256 is
`01c8c37c7f9ecb8fcadbe74201a12866e8d7ea254f6c035f4019d47b90175352`.

R3 was only an intermediate source archive; it was not built or accepted as a
release. R2 browser evidence for unaffected workflows remains explicitly R2,
not a claim those screenshots came from the R4 bundle.

#### Exact published revision

Feature commit `998839ba06429d47de2e12b5562b4a4c4cad6a62` was published on public
`main` after its independent clean-checkout
[CI run 33359846368](https://github.com/FengYuchen1314/open-node/actions/runs/33359846368)
completed successfully on 2026-08-31. All four jobs passed: backend **1927 passed,
6 opt-in skipped**, 703.33 s; Agent **605 passed**, 10.23 s; frontend **796 tests /
70 files passed** plus main/Probe builds; Worker **5 passed** plus typecheck.
The six real TLS cases passed separately in the isolated VPS gate above; they
were not executed by this hosted-CI run. Later documentation-only commits do
not change the tested feature revision.

A fresh public clone at `/root/open-node-zh-commit-998839b.c3ycWOn7/source`
was clean at that exact SHA. Every non-documentation tracked file matched the
verified R4 snapshot; the manifest SHA-256 is
`4052a9519ac6bf9971e7bb3d4138695877d35dfe8776a81d04a94d6e4365311a`.
The source-built image
`open-node:zh-external-commit-998839ba06429d47de2e12b5562b4a4c4cad6a62`
has ID `sha256:77b0d0faed6aa4f3e2195eebb44be8c506c6a62bc624363c3ab4cb2f2eba8b04`
and an OCI revision label equal to the full Git SHA, not a working-tree label.

The image passed the same non-root/read-only, static route/404, three-viewport,
restart/session/data, encrypted-source key and real HTTPS-fetch boundaries as
the R2 Docker gate. All 38 packaged frontend files matched the final R4 assets
byte for byte. The exact-clone source and standalone assets remained unchanged.
`evidence-r1/report.json` SHA-256 is
`5ce7fb209a29a7ba1e57dee8674b8fd6c1793841fab5c63386ad0f610d1bfd23`.
The owned temporary container and volume were removed after identity checks;
production's source, image, instance, start time and restart count did not change.

### React and standard Ant Design migration (2026-08-31)

These results belong to the new React frontend, not the 268-test Vue baseline
below. The rewrite keeps the FastAPI API, session/CSRF contracts, Agent protocol
and single-image Docker deployment. It removes Vue, Vuetify, Pinia and their
compiler/router dependencies. Architecture and build instructions are in
[frontend.md](frontend.md).

The published feature commit is
`50897f928226c9fef2ab7d0f68de0c3aad46156a`. Its clean-checkout
[GitHub run 33330624705](https://github.com/FengYuchen1314/open-node/actions/runs/33330624705)
passed all four jobs: backend **1,253 tests** (618.73 s), Agent **605 tests**
(10.67 s), frontend **509 tests in 63 files** (535.20 s) and Probe Worker
**5 tests** (164.53 ms). Backend/Agent Ruff, the Agent wheel, frontend type
checking and both production bundles, and Worker type checking also passed.
The only backend warning was the known Starlette/httpx deprecation. Subsequent
documentation-only commits do not change this tested product source; this run
must not be attributed to a different commit.

The final frozen working-source run passed **509 tests in 63 files**
(638.05 s), with no unhandled errors, in
`/tmp/open-node-react-release.xaSu8WDc/frontend-tests.json`. It includes the
existing domain/API tests and real Ant Design DOM tests for the migrated views.
An earlier consolidation passed the same 509 tests in 644.30 s at
`/tmp/open-node-react-accessible.OTOVWliF/frontend-tests.json`. The intervening
Plan/Limiter checks passed 36 tests in three files; the user limit editor's
seven tests also passed after its gutter correction. These are overlapping
reruns, not additional tests to add to 509.

Final working-source builds are at
`/tmp/open-node-react-release.xaSu8WDc/source`. Both the administrator and
independent public Probe builds pass TypeScript and Vite. The frozen
`browser-assets.tar.gz` SHA-256 is
`da85b9cc62b5d78dfae10dbb2f85d3d4ff79e935514f894c67761eabfc64fb4c`.
Relative to the earlier consolidation, the only product-source changes are layout
corrections in `PlanManagementDialog`, `UserLimitEditor`, `LimiterPanel` and
`ProbeAdministrationPanel`; no backend or shared styles changed.

Production-bundle browser evidence is kept in separate immutable fixtures:

| Workflows | Passing evidence |
| --- | --- |
| Administrator shell, server creation, Nginx paths, tunnel forms, certificate import/download, Probe settings/tasks/tokens, password change and session expiry | Final source's parent directory: `operator.log`, `operator-proof/`; includes 1440/390/320 Probe-title and header-button geometry |
| Administrator MFA enrollment, recovery-code acknowledgment, challenge, policy, regeneration, disable and CLI recovery | Same parent: `administrator-mfa.log`, `administrator-mfa-proof/`; private material masked in screenshots |
| Subscriber portal, MFA/recovery, password/link reset, device revocation, user isolation and live forwarding | `/tmp/open-node-react-account-r5.ErZXGOSk/evidence/{websocket,http}.log`; both full transports pass on the final bundle, 24 private screenshots |
| Panel Agent bootstrap, server edit/delete and traffic | `/root/open-node-react-bootstrap-browser.gEUopOkd/react-dashboard-r2-evidence.json`; 24 screenshots, real systemd/runtime traffic and owned-resource cleanup |
| Certificates and dependent change sets | `/tmp/open-node-react-control-browser.FPqNskNQ/r2/evidence/`; real ACME/EAB/revocation/recovery plus ordered apply/rollback and compensation |
| Native limiter | Same root's `r3/evidence/limiter.log`; 18-protocol traffic, speed/connection enforcement, automatic-rule expiry, restart, revision conflicts and 1440/390/320 controls |
| Plans, node aliases, automatic speed rules and user limits | `/tmp/open-node-react-catalog.WDzjZMFf/evidence/r4-{plan-management-websocket,plan-node-aliases-websocket,plan-speed-rules-websocket,user-limits-websocket}` |
| Node management and legacy MMWX import | Same catalog root: `r4-node-management-http` and `r4-legacy-mmwx-stock`; node WebSocket evidence is also retained |
| Subscription access, clients/templates, links and user management | Same catalog root: `r2-subscription-access-websocket-v2`, `r2-subscription-clients`, `r2-subscription-links-websocket`, `r2-user-management-websocket` |

All ten catalog scripts completed their full gates. The client gate did not use
the templates-only shortcut: it ran real Mihomo 1.19.30, sing-box 1.13.19 and the
project's custom Xray. The final legacy-import gate uses official Xray 26.3.27
for standard VLESS; it does not test an Agent transport. Surge format/template
checks are not proof of a running Surge client.

The independent public Probe gate is at
`/tmp/open-node-react-browser.NyIq0V6p/public-probe-theme/report.json`. It uses
real Wrangler/Miniflare/workerd, HTTP and WebSocket, idle-stream polling,
disconnect/retry/reconnect, light/dark/system themes and credential stripping.
The final build's public JS and CSS are byte-identical to that tested bundle:
JS SHA-256 `3a1fc930fd7603da5b8a313aac9c5359dbe3915a6dcb5d8016d93cf103b26eb6`,
CSS `9fd60fb31ba60054d1203f3a99a81dbb50ca9d748e34a9cd293c9b721fda4db1`.
This is a local Cloudflare-runtime test on the VPS, not a deployment to a
customer's Cloudflare account.

The frozen working-source Docker gate passed against
`open-node:react-working-tree-r5`, image
`sha256:bc17d752fab8644a9ba7fbbf69077e9387c24e5d5840c01926ede7868f5dd3c1`,
running as UID/GID 10001 with the Compose read-only/capability restrictions.
All 39 served files match the frozen assets and image. Ten SPA deep links,
five reserved-path 404 cases and a non-HTML navigation 404 check pass; three
original browser sessions plus a server record survive restart. Three viewport
sizes and all eight administrator
lazy routes pass. The private helper also passes eight ownership/cleanup safety
controls. Its report is
`/root/open-node-react-bootstrap-browser.gEUopOkd/r5-docker-4/report.json`,
SHA-256 `201773b8df09bf2dcce869155f44edcedb3e834969fb0ebbc0516352a6b1f26c`.
The fixture follows Docker's newly allocated loopback port after restart while
preserving the original cookie values; it does not treat a stale test URL as a
product failure. The labeled container and volume were removed after verification.

The final source-provenance gate then cloned the exact GitHub `50897f9` commit
into `/root/open-node-react-commit-50897.0MDZIwd3/source`. A fresh `npm ci` and
both builds produced the same **39 administrator + 3 public Probe files** as
the frozen working-source bundle, with byte comparisons and sorted SHA-256
manifests agreeing and no extra or missing files. The checkout remained clean;
tracked Vue files and Vue dependencies in both the lockfile and installed tree
were zero.

The image was rebuilt from a pure Git archive, without local `node_modules` or
generated assets in its build context. Archive SHA-256:
`0d1e3b0886d3c03897a34b665d5e9f6b6a7acdadd0857978e8bb5c2a40da078b`.
The private test tag is `open-node:react-working-tree-r50897`, image ID
`sha256:e0dabde00261b3c4178a62dc367325a47b8bdf3736df0ed700ac99c157708d65`;
its OCI revision is the full `50897f9` commit, not a working-tree label. The
unchanged Docker helper repeated the full asset, route, three-viewport login,
original-session/data restart and eight ownership-safety gates successfully.
Its container and volume were removed; production remained unchanged. Report:
`/root/open-node-react-commit-50897.0MDZIwd3/source-proof.json`, SHA-256
`ff6cc9c18e7507f7311493ee94776b9489d7c1aea4357654fa48c8a7a4004a04`.
The report proves application-file identity, not bit-identical whole images.
The backend application Git tree remains
`31760f22ffae9c562b3b4a9949744b6b976163bf`, identical to `a677280`.

Real browser failures drove the retained regression checks: invalid numeric
drafts must not be clamped into valid quotas/ports on blur or Enter; loading
icons must not change action names; uploads must not retain private File lists;
and narrow-screen dialogs, grid gutters and headers must remain usable.
Test-only corrections scope queries to their actual form/dialog, wait for
closing popup animations and compare raw traffic with raw traffic separately
from two-way charged usage. They do not remove business assertions or enlarge
test timeouts. Ant Design Form's short presentation timers are drained before
the sign-in test's jsdom window is disposed, without suppressing errors.

The production container, image, start time, restart count and Git checkout
remain unchanged; the shared candidate remains clean at `6ca84e2`. No public
DNS/TLS, reverse-proxy subpath, customer Worker deployment or off-site backup
claim follows from these isolated checks.

### Committed Agent bootstrap and hosted-runner fixture fix (2026-08-31)

The feature commit is `1515a7bd56a2dbf257d861fe8760038a9329bae4`; its
host-fixture correction is `a677280ece64a71d7ee4e8c4f0720cd819bcf584`. Both are
published on `main`. The following historical results precede the React/Ant
Design rewrite; the frontend counts here describe the former Vue baseline.

- Clean-checkout [GitHub run 33325869097](https://github.com/FengYuchen1314/open-node/actions/runs/33325869097)
  passed all four jobs at `a677280`: backend **1,253 passed** (640.48 s), Agent
  **605 passed** (10.31 s), frontend **268 tests in 37 files** and both production
  bundles, and Probe Worker **5 tests** plus type checking. The backend emitted
  only the known Starlette/httpx warning.
- The earlier `1515a7b` hosted run failed during the installer fixture's parent
  ownership/mode precheck because it depended on the runner's real `/opt`.
  The correction makes that fixture use its owned temporary install base and
  explicit private directory modes. The actual installer still defaults to
  `/opt`, with unchanged rejection of unsafe parents; regression cases reject
  both `0775` and `0777` before creating a job directory.
- An independent VPS reproduction runs all **124 host-installer tests** as the
  existing unprivileged `nobody` account with `umask 0002`. It passed, along
  with Ruff, in `/tmp/open-node-ci-owner.wVwaXkVt`. No production paths or
  service accounts were adopted by those fixtures.
- The exact clean `a677280` checkout at
  `/tmp/open-node-bootstrap-owner-fix.ZBzZ9ILY/source` passed backend Ruff and
  **323 focused tests** (76.60 s): 75 API, 98 store, 124 installer and 26
  authentication cases. Log: the parent directory's `backend-focused.log`.
- The same checkout's real-systemd smoke reran the actual installer bytes,
  SHA-256 `00e18bc0c4c55a461b1b811c4e4faa636f558590325e3a2e26827e15cb468913`,
  against the unchanged public Agent 0.3.0a0 assets and official Xray v26.3.27.
  Forced WebSocket and HTTP both reached non-root Agent/runtime readiness and
  forwarded **1,223,915** and **1,102,535** downlink bytes. Wrong-nonce replay,
  redemption after registration and repeated installation were refused; no
  secret leaks or cleanup errors were reported. Evidence:
  `/tmp/open-node-bootstrap-owner-fix.ZBzZ9ILY/real-bootstrap/report.json`.

The feature-only `1515a7b` verification also checked all 142 tracked backend
files (2,201,738 bytes) against Git and the complete 1,250-test VPS source tree.
Its exact frontend/Worker/browser rerun is retained under
`/tmp/open-node-bootstrap-exact-ui-1515a7b`; 53 Compose preflight checks and
two isolation negative controls are at
`/root/open-node-bootstrap-committed-check.OmEteOtb/results/report.json`.
These counts are not added to the later CI or focused totals.

Production remained on container `c2594ea5b436950a92e310f320b072bfe5bbeda15b178672b4d14008e6e841aa`,
image `open-node:cb1eb0c`, start time `2026-08-29T12:59:02.442246035Z`, with
zero restarts. The shared candidate remained clean at `6ca84e2`. This is a
source publication, not a production upgrade or proof of public DNS/TLS,
reverse-proxy subpaths, or automatic transport fallback.

### Panel-issued Agent installation (2026-08-31)

The feature was tested in private VPS source snapshots based on `6ca84e2` with
the bootstrap changes overlaid. The shared candidate stayed clean at `6ca84e2`;
production stayed on `open-node:cb1eb0c`. These working-source checks are distinct
from clean-checkout CI after the feature is committed.

- Complete backend gate: **1,250 passed** (694.29 s), with only the known
  Starlette/httpx deprecation warning, in
  `/tmp/open-node-bootstrap-feature.3peB48sZ/backend-suite.log`. The SSH output
  session disconnected near the end; the VPS process continued and wrote the
  complete successful pytest summary. This is the retained VPS result, not an
  inferred success from that disconnected shell.
- Focused backend gate: **320 passed** (76.06 s), comprising 98 ticket-store,
  75 API/release-helper, 121 host-installer and 26 authentication tests. This
  includes concurrent claims, hash-only tickets, nonce/expiry/replay bounds,
  post-claim reissue refusal, Origin/CSRF, bounded JSON, persistent rate limiting,
  HTTPS/path validation, release-source/hash validation, owned paths and secret
  redaction. Backend Ruff passed.
- Frontend: **268 tests in 37 files** (7.65 s), administrator and probe-only
  production builds passed. The added cases cover late request invalidation,
  close/target change/disposal, replacement/revoke/claim, existing heartbeat,
  registration, failures and no persistent command storage.
- Probe Worker: **5 behavior tests** and TypeScript checks passed, including
  GET/POST/DELETE rejection of all Agent-bootstrap endpoints without contacting
  the origin or asset fallback, plus write rejection of public Probe routes.
- Production browser gate passed against disposable FastAPI/SQLite: disabled
  configuration, explicit mobile issuance/copy, close/reopen clearing, command
  replacement, revocation, claim vs. registration, existing-heartbeat refusal,
  installer checksum binding, cache/privacy headers and no page errors. The
  final registration and heartbeat are explicit API fixtures, **not** proof of
  installation. Desktop/mobile screenshots mask commands and manual tokens.
- Separate real-systemd bootstrap passed over forced **WebSocket and HTTP**
  using the published Agent 0.3.0a0 artifacts and official Xray v26.3.27. The
  gate exercises the actual panel-generated HTTPS command, claim, non-root
  process/runtime readiness, a subsequently configured VLESS inbound and live
  HTTP forwarding. Control-plane downlink counters observed **1,223,915 bytes**
  and **1,092,420 bytes**, respectively. Wrong-nonce replay returned 401;
  registration blocked redemption (401) and ticket reissue (409). Re-running
  the command was refused with PID/config/unit unchanged; logs leaked no Agent
  token and both owned fixtures were cleaned.
- Root installer compatibility: **53 preflight checks** passed for legacy and
  new Compose files, exact runtime environment matching, inherited-shell
  isolation and safe URL values. A separate real Docker fixture passed fresh
  install, same-source enable, identical-value no-op and same-source disable,
  keeping the administrator/inventory and two immutable stopped-volume backups.
  The non-root image could read the bundled installer/manifest, and HTTP
  resources plus configured state matched in all three deployment states.
  Owned fixture cleanup completed and the production snapshot was unchanged.
- Additional fixture safety controls passed after review: 15 injected `GIT_*`
  variables could not change an external sentinel repository's files, index,
  configuration, refs or objects, including through the imported Git helper.
  A mocked pre-existing namespace caused **zero cleanup calls**. These controls
  created no Docker resources and are reproducible with
  `--safety-negative-controls`.

The real bootstrap gate's only command deviation is appending its restricted
`--test-directory` option. Its root-URL loopback HTTPS control plane uses a
private trusted CA; GitHub downloads still use Debian system trust. It does not
prove public DNS/TLS, reverse-proxy subpath operation or an actual Auto-to-HTTP
fallback event. Those must not be inferred from running each transport
separately. The backend and browser fixture never use the production database.

From a reviewed isolated feature checkout, with the backend/browser dependencies
and Chromium installed:

```bash
npm --prefix frontend run build
backend/.venv/bin/python scripts/vps/smoke-agent-bootstrap-browser.py \
  --output /tmp/open-node-bootstrap-browser-reviewed-revision
sudo backend/.venv/bin/python scripts/vps/smoke-agent-bootstrap.py \
  --output /tmp/open-node-bootstrap-real-reviewed-revision
sudo python3 scripts/vps/smoke-installer-bootstrap-setting.py \
  --safety-negative-controls --guarded-update \
  --output /tmp/open-node-bootstrap-setting-reviewed-revision
```

The latter two require a disposable Debian 12 amd64 VPS with root, systemd,
Docker/Compose and the documented host tools. They create and remove only
explicitly owned test resources. Failed cleanup retains private recovery inputs;
inspect the reported exact fixture before doing anything else.

VPS evidence locations:

- `/tmp/mmwx-agent-bootstrap-store.lE9RnE` — frozen backend overlay and focused gate.
- `/tmp/open-node-bootstrap-ui.a8xVJ6VG/frontend-final.log` — final frontend gate.
- `/tmp/open-node-bootstrap-browser-20260831-final/report.json` — browser workflow.
- `/tmp/open-node-bootstrap-real-gate-20260831a/report.json` — real double-transport bootstrap.
- `/tmp/open-node-bootstrap-setting-guarded-20260831b/report.json` — root installer
  matrix, Docker setting transitions, image resource/API checks and cleanup.
- `/tmp/open-node-bootstrap-setting-safety-20260831a/report.json` — Git environment
  isolation and refused-namespace cleanup negative controls.

The Agent release itself is independently pinned to committed source
`6ca84e21202950bf5ee4754a8ae20e28dbde42ed`, not the newer control-plane overlay.
Its exact four assets passed pre-upload service/lifecycle gates, fresh anonymous
download/tag/BUILD/wheel/tar verification and default-GitHub-source WebSocket/HTTP
upgrade, VLESS forwarding and rollback (104.95 s). See
[the release record](releases/agent-0.3.0a0.md). The previous wheel in the upgrade
gate is a synthetic fixture; this is not a claim of a tested 0.2-to-0.3 migration.

### Administrator MFA acceptance (2026-08-30/31)

All commands below target the isolated `/opt/open-node/mmwx-parity-candidate`
checkout, never the production service or database.

- At `45515b6`, the complete backend suite passed: **955 tests** (621.28 s),
  with one known Starlette/httpx deprecation warning. This supersedes the earlier
  948-test result at `ee16ed3`.
- At `58b33af`, the expanded authentication suite passed: **26 tests** (23.68 s),
  including a persisted cross-IP/cross-challenge verification budget, two-store
  contention at the final allowed attempt, key-loss recovery and
  local/password-change invalidation. The concurrent-budget regression was
  added after the complete 955-test run; the two counts are separate evidence.
- The subsequent clean-checkout GitHub backend job at `58b33af` passed
  **956 tests** (561.06 s), including that added regression. This is separate
  hosted-CI evidence, not a claim that the earlier VPS run contained 956 tests.
- At `fb1aaaf`, frontend **239 tests**, main production build and probe-only
  production build passed. The remaining build messages are chunk-size warnings.
- The real production-frontend browser smoke passed at both `fb1aaaf` and
  `45515b6`: enrollment, recovery-code
  acknowledgement, mandatory policy, password-only challenge denial, recovery
  login, code replacement, policy removal, disablement and local reset followed
  by mandatory enrollment. Desktop (1440 px) and mobile (390 px) screenshots
  were inspected; authenticator secrets and QR codes are masked in artifacts.
  The script also checks horizontal overflow and absence of secrets from browser
  storage, and disposes its private SQLite database and loopback process.
- The independent Agent suite passed **605 tests** and Ruff on the VPS;
  the Probe Worker passed **3 behavior tests** and TypeScript checks. At
  `58b33af`, all four GitHub clean-checkout jobs passed. The Agent job now
  exercises real ownership changes with its installed
  interpreter under `sudo`, instead of failing on the hosted runner's missing
  `chown` privilege.

Run after building the frontend on the VPS:

```bash
cd /opt/open-node/mmwx-parity-candidate
backend/.venv/bin/python -m pip install -e './backend[browser]'
backend/.venv/bin/python -m playwright install chromium
backend/.venv/bin/python scripts/vps/smoke-administrator-mfa.py \
  --output /tmp/open-node-admin-mfa-reviewed-revision
```

The smoke creates random fixture credentials and an encryption key; it does not
read deployment secrets. Its CLI reset is performed only against the disposable
database. It is not evidence of public HTTPS deployment, multi-administrator
support, or automatic recovery backups. See [administrator security](administrator-security.md).

### Public Probe Worker acceptance (2026-08-31)

With the candidate's dependencies and Playwright Chromium installed, run on the
isolated VPS checkout:

```bash
npm --prefix frontend ci
npm --prefix frontend run build:probe
npm --prefix probe-worker ci
backend/.venv/bin/python scripts/vps/smoke-public-probe-worker.py \
  --output /tmp/open-node-public-probe-worker-reviewed-revision
```

The gate uses `wrangler deploy --dry-run` to compile the actual Worker, then
executes it in Cloudflare's Miniflare/workerd with the real production
`frontend/dist-probe` assets. It retains the repository's compatibility settings,
SPA fallback and `run_worker_first` policy. It neither deploys nor logs into a
Cloudflare account. All listeners are ephemeral loopback ports; the upstream is
a disposable fixture, not the production control plane or its database.

Passed using Wrangler **4.127.0**, Miniflare **5.20260826.0-alpha** and workerd
**1.20260826.1**, as locked by `probe-worker/package-lock.json`:

- Production HTML, JS and CSS byte/hash checks, nine HTTP aliases and three real
  WebSocket aliases; token replacement, bidirectional credential stripping,
  security headers, private-route 404, write-method 405 and no followed redirects.
- Anonymous Chromium requests use only the public API surface. Complete headers,
  HTTP bodies, WebSocket frames, DOM and browser storage are checked for leaked
  Worker credentials; cookies remain absent and there are no page errors.
- Both status and target polling continue while an established WebSocket sends
  no frames. Malformed frames do not break subsequent live updates. Polling
  continues through a forced disconnect and rejected reconnect, then automatic
  reconnect applies a new live snapshot.
- Target ranges, ping/system series, public-only deep links and 1440/390px
  layouts passed. Desktop/mobile screenshots were visually checked. The report
  records runtime versions, asset/bundle hashes and observed requests, without
  retaining the generated secret. Private runtime files and processes are removed.

The earlier `wrangler dev --local` attempts failed because its development
ProxyWorker exited on a connection error. The official SDK tracker describes
the same fatal error handling in [issue 15317](https://github.com/cloudflare/workers-sdk/issues/15317)
and a related five-second connection-reuse race in
[issue 14641](https://github.com/cloudflare/workers-sdk/issues/14641).
The gate uses the [official dry-run bundle](https://developers.cloudflare.com/workers/wrangler/bundling/)
with direct Miniflare instead; it does not patch dependencies, change application
polling intervals or mock the Worker's fetch implementation to bypass the failure.
The adapter targets the locked Miniflare v5 API and must be reviewed when updating
those dependencies. Build/runtime failures retain a stage report and redacted logs.

This closes the local anonymous Worker/browser gate, not a real Cloudflare
deployment, custom-domain/TLS setup, production-origin connectivity or all visual
themes from the reference probe. Those remain distinct operational/parity checks.

### Repository-wide runner

From Windows PowerShell in the repository root:

```powershell
.\scripts\vps\sync-and-test.ps1
```

The script pushes the named local branch, records its exact commit and uses
the default SSH key for `root@185.99.135.224`. The VPS needs Python 3.11+ and Git
before the first call. It clones into a missing/empty target or fast-forwards
an existing clean checkout with the matching origin and branch. Local edits,
untracked files, divergence, symlinked paths, incoming ignored-file conflicts,
and a remote branch that moved after the push stop the update. Nothing is
reset or recursively removed. Uncommitted local Windows edits are not tested.

The default target is `/opt/open-node`; `-RemoteDir` can select a direct,
non-hidden child. Use a separate checkout for tests when the default directory
serves a live process. This helper does not stop services or back up databases;
follow [deployment.md](deployment.md) for production upgrades. The script then
bootstraps the Debian test host (unless `-SkipBootstrap` is set) and runs:

1. Python venv and Node.js bootstrap;
2. backend dependency installation;
3. backend pytest suite;
4. independent-agent dependency installation, Ruff, pytest, and wheel build;
5. frontend dependency installation;
6. frontend Vitest suite;
7. frontend production build;
8. probe Worker dependency installation and TypeScript checks.

The checkout safety tests use disposable local Git repositories on the VPS.
For the actual PowerShell-to-SSH path, run:

```bash
python3 scripts/vps/smoke-sync-and-test.py --pwsh /path/to/pwsh
```

This root-only fixture starts its own loopback `sshd`, generates temporary
client/host keys, and uses strict host-key checking. It verifies quoted branch
and repository names, the exact tested revision, bootstrap selection, and
preservation of a dirty checkout. It uses fixture bootstrap/test commands to
check the launch contract, not as a substitute for the application suites.
It does not change the existing SSH daemon, authorized keys or live checkout;
its temporary direct-child checkout and SSH files are removed on exit.

## Direct VPS Command

If the repository is already checked out on the VPS:

```bash
cd /opt/open-node
bash scripts/vps/run-tests.sh
```

The runner removes stale local Agent wheels before building. In the same shell,
resolve the single artifact once and reuse it in the smoke commands below:

```bash
mapfile -t AGENT_WHEELS < <(
  find "$PWD/agent/dist" -maxdepth 1 -type f -name 'open_node_agent-*.whl' -print | sort
)
if (( ${#AGENT_WHEELS[@]} != 1 )); then
  printf 'expected exactly one built Agent wheel, found %s\n' "${#AGENT_WHEELS[@]}" >&2
  exit 1
fi
AGENT_WHEEL="${AGENT_WHEELS[0]}"
```

## Legacy MMWX Identity Smoke

With the frontend built and an Xray binary available on the VPS:

```bash
PYTHONPATH=backend/app backend/.venv/bin/python \
  scripts/vps/smoke-legacy-mmwx.py \
  --xray /absolute/path/to/xray \
  --output /tmp/open-node-legacy-mmwx-screenshots
```

The isolated fixture creates an active-main-shaped MMWX SQLite database, runs the
mode-0600 exporter, uploads the result through the preview/confirmation dialog
and explicit package mapping, then verifies secret clearing. It checks imported
multi-file assignments, administrator profile editing, subscriber profile selection,
bcrypt-to-Argon2id upgrade, original TOTP, one-use legacy recovery and source-admin
demotion. Long/generated/custom keys and direct file, file+user and package+user
`/x` links all render the same valid profile; one `/x` result forwards real VLESS
traffic. Screenshots and overflow checks cover 1440px, 390px and 320px. See
[legacy-mmwx-identities.md](legacy-mmwx-identities.md) for raw/template/rule limits.

## Subscriber Limit Smoke

On the designated VPS, with the frontend built and the independent Agent wheel
and free native-limiter Xray binary available:

```bash
python scripts/vps/smoke-user-limits.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-user-limits-screenshots \
  --transport websocket
```

Repeat with `--transport http`. The isolated root/systemd fixture installs a
non-root Agent and verifies real speed/connection caps, explicit unlimited,
inheritance, Agent restart persistence, offline quota withdrawal, unchanged
credentials and charged usage, and unrelated-user forwarding. Browser checks
cover stale forms, numeric validation, user overrides and subscriber visibility
at 1440px, 390px and 320px widths. See [user-limits.md](user-limits.md).

## Custom Subscription Link Smoke

Use the same VPS prerequisites, built frontend, Agent wheel and free Xray
binary as the subscriber-limit fixture:

```bash
python scripts/vps/smoke-subscription-links.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-subscription-link-screenshots \
  --transport websocket
```

Repeat with `--transport http`. Operator/subscriber browser edits, password
and second-factor proof, stale/colliding values, clearing, custom-URL downloads
and complete link reset are checked against real forwarding and an unchanged
runtime PID. The same run creates a [temporary subscription link](temporary-subscriptions.md)
through the administrator UI, copies it, consumes its access limit with Xray and
URI-list downloads, proves real forwarding, checks exhaustion and revokes it.
Screenshots and overflow checks cover 1440px, 390px and 320px. The temporary
Agent installation is removed after the run. See
[subscription-links.md](subscription-links.md) for permanent link identity and
security rules.

## Plan Alias Smoke

With the same VPS prerequisites and built frontend:

```bash
python scripts/vps/smoke-plan-node-aliases.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-plan-alias-screenshots \
  --transport websocket
```

Repeat with `--transport http`. The isolated fixture checks browser creation,
alias editing, stale revisions, saved enable/disable state, clearing, all five
export formats and a subscriber's downloaded Xray configuration forwarding
real traffic. Credentials, subscription keys, the unrelated plan and runtime
PID remain unchanged. It captures 1440/390/320px views and removes its temporary
Agent installation. See [plan-management.md](plan-management.md) for semantics.

## Plan Speed Rules Smoke

Use the current Agent wheel, built frontend and a free Xray binary reporting
`user_auto_speed_rules: 1` on the VPS:

```bash
python scripts/vps/smoke-plan-speed-rules.py \
  --xray /absolute/path/to/xray \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-plan-rule-screenshots \
  --transport websocket
```

Repeat with `--transport http`. Real clients exercise sustained and burst
activation, measured throttling, expiry, an unrelated plan, hot refresh and
restart persistence. Browser coverage includes creation, ordered edits,
invalid values, continuous typing, clearing and preservation from Config >
Limits. Exports, credentials and subscription keys remain unchanged.
Screenshots cover 1440/390/320px. The fixture removes its non-root Agent.

## Subscription Client Smoke

Build the frontend and [patched runtime](fork-runtime.md) on the VPS. Use the
backend development environment with Playwright Chromium, the current Agent
wheel, Nginx, Mihomo v1.19.30 (digest below) and official sing-box v1.13.19.
The `sing-box-1.13.19-linux-amd64.tar.gz` SHA-256 is
`ef88a9e577d474210867bd708933d042e9b70106529df2656182c9db90106aa1`.

```bash
python scripts/vps/smoke-subscription-clients.py \
  --xray /tmp/open-node-runtime-build/xray \
  --mihomo /absolute/path/to/mihomo \
  --sing-box /absolute/path/to/sing-box \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-subscription-screenshots
```

Pass `--templates-only` to retain the full 18-variant fixture while running only
the custom-template API, Mihomo forwarding and administrator/subscriber browser
workflow. Surge output is validated from the real endpoint but not imported
into the proprietary Apple client on this Linux host.

This disposable root/systemd fixture installs a non-root Agent and provisions
18 inbound variants. It validates complete native exports, switches selectors,
tests each selected Xray node, and feeds unchanged URI/Base64 payloads to the
pinned Mihomo parser. Every compatible entry must forward TCP and UDP. Mieru is
covered over both TCP and UDP underlays only after a fresh Agent scan reports
strict integer `mieru_udp_target: 1`; the fixture checks both transports and
the backend's fail-closed capability gate. Explicit expected node sets prevent
a broken converter from passing by excluding everything.

It also verifies that the Shadowsocks 2022 shared key stays out of imported
node metadata and compatibility reports. Browser checks cover the format report,
Xray selection, selected URLs, desktop/mobile/narrow layout, and delayed
responses during format/user changes. Consult [subscriptions.md](subscriptions.md)
for the exact version-specific boundaries; this fixture is not an assertion
that arbitrary protocol extension fields are portable.

The template workflow additionally covers CRUD revisions, plan bindings,
unchanged credentials/tokens/runtime PID, custom Clash group order, real Mihomo
TCP/UDP forwarding, custom Surge section/node validation, personal permission,
and 1440/390/320px screenshots for both workspaces.

Verified on 2026-08-29 (UTC), Debian 12 x86-64 on the designated VPS:

- Backend: 913 tests; Agent: 544 tests; frontend: 32 files and 216 tests,
  TypeScript checks and production build. Ruff, targeted formatting and probe
  Worker TypeScript checks passed.
- All 18 inbound variants passed their supported native client formats and
  unchanged URI/Base64 imports with real TCP/UDP target traffic. Mieru TCP and
  UDP underlays both passed through Mihomo v1.19.30; its executable SHA-256 was
  `8ad44e28fe72be4640254b96741b677f4074991b99186cc4486a1c28ded02b1a`.
  The sing-box v1.13.19 executable SHA-256 was
  `7e9dcd7239c49478a576d79f272751e5ed1c2aba7cc08ab1b2bd69c00c904ba1`.
- The custom Clash/Surge API, real Mihomo forwarding and both template browser
  workspaces passed. Generated credentials, subscription tokens and runtime
  identity remained stable.
- The patched runtime SHA-256 was
  `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
  matching-source SHA-256 was
  `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`.
  Its `build.json` records four MPL-2.0 patches, package and race tests, and
  successful module verification.
- Existing Starlette/httpx deprecation, npm install-script approval and frontend
  bundle-size warnings remain.

These results do not close the other [migration gates](migration-map.md).

## Fork Protocol Smoke

Build the optional [compatibility runtime](fork-runtime.md), its unmodified
reference executable, and the current Agent wheel on the VPS. Obtain Mihomo
v1.19.30's `mihomo-linux-amd64-compatible-v1.19.30.gz` from its official release.
Verify the gzip SHA-256 before extraction:
`db214c7a2517e63c150d123178d16d102e03a241ccdae4e5e07ffbe9cf56c6f9`.
The tested Go 1.26.7 Linux amd64 tarball has SHA-256
`ffb5f8de10c62550dfddab66b36b57030721e0a44a3218e9e1181d7b59f121ca`.

```bash
python scripts/vps/smoke-protocol-runtime.py \
  --xray /tmp/open-node-runtime-build/xray \
  --reference /tmp/open-node-runtime-build/xray-reference \
  --mihomo /absolute/path/to/mihomo \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx
```

Use the backend development environment. The fixture requires root/systemd
only to install disposable dedicated non-root Agents and remove their units
and accounts afterward. All listeners are loopback-only; there is no public
provider registration. Both HTTPS lease and WSS paths use a trusted fixture CA.
The source runtime is first exercised unchanged, followed by the installed
Agent's patched runtime using the same configuration. The tests then import
nodes, assign a plan, consume actual subscribed credentials, check per-user
statistics, rotate non-managed credentials, exercise direct zero-user listeners,
withdraw managed access, restart the service with suspended listeners and
reactivate the same catalog credentials. Direct empty-user edits must keep each
TCP/UDP listener owned only by the new fork PID and reject old credentials;
managed withdrawal must remove every listener and preserve the private recovery
template across an Agent restart. The smoke also checks invalid-write
preservation and refusal of an official Xray switch without changing the fork
PID.

AnyTLS, every Snell variant and both Mieru underlays cover TCP and UDP target
bytes. Mieru additionally covers transformed UDP echo, DNS, a 4096-byte packet,
multiple targets on one association, user-attributed statistics and three fresh
negative associations. The UDP targets accept traffic only from Xray's explicit
loopback egress address, preventing Mihomo local-direct behavior from becoming a
false positive. The unmodified reference runtime is the Mieru UDP negative
control. Snell v6 uses the free fork client. Complete mixed exports are covered
separately by the subscription-client smoke above.
Other architectures, multi-file takeover and public-provider staging are not
established by these tests.

Verified on 2026-08-29 (UTC), Debian 12 x86-64 on the designated VPS:

- The complete smoke passed independently over WebSocket and HTTP with exit
  code zero. Both used disposable non-root systemd Agents, trusted local TLS,
  real Mihomo and the pinned fork client.
- The unmodified reference accepted all original TCP paths but rejected Mieru
  UDP target traffic over both underlays. The patched runtime passed TCP/UDP,
  DNS, multi-target, large-packet, statistics, rotation, direct zero-user,
  managed suspension, Agent-restart persistence and exact reactivation checks.
- The failed official-Xray migration preserved the config bytes and the exact
  running fork PID. Every deliberate runtime restart removed the prior PID;
  `fuser` proved each active TCP/UDP listener belonged only to the replacement
  process, while managed suspension left no owner.
- The runtime SHA-256 was
  `7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
  the unmodified reference SHA-256 was
  `b0f43766871def4cad3952b9cecd2f4dfd4ac4dd9771866e9e778980682e5cbb`.
  Fixed source revision was `d3fdae5833a92070414db588ee9893264147b789`.
- Matching source SHA-256 was
  `1674ecc92af85bbc0c0d9cc5094b1cd13845a5585d67486a97460a0efda80675`.
  Patch SHA-256 values were
  `0914ab8149646801904d91f6229520acbe6cae1e749229fb5c8e129fee458814`
  (empty users),
  `d85463cfdf6b0c5ca3f17f046e2bf78e1dc44a1e21146baff9faf804137708d7`
  (AnyTLS UDP),
  `3841a90cae74b978de31671057a3bb05ec84589d86cecdf34222175f318da506`
  (Mieru UDP) and
  `91e7f33c3752f5fb8f46852e89cdc02ef2cfd7479657e154e26e5ea184c7d644`
  (limiter). Go 1.26.7 package, race and module-verification gates passed.

## Host Policy Smoke

Build the current Agent wheel on the VPS. Keep a trusted pre-policy bootstrap
checkout (including its sibling lifecycle modules) to exercise old-helper
compatibility. Use the backend test environment, Debian Nginx and the pinned
NextTrace binary from [agent-diagnostics.md](agent-diagnostics.md):

```bash
python scripts/vps/smoke-host-policy.py \
  --wheel "$AGENT_WHEEL" \
  --nexttrace /path/to/verified/nexttrace \
  --nginx /path/to/nginx \
  --previous-bootstrap /path/to/previous-checkout/agent/app/open_node_agent/service.py
```

The root-only fixture installs isolated non-root systemd services using the
previous installer and copied lifecycle helpers. It exercises both HTTPS leases
and WSS, actual TCP/ICMP and IPv4/IPv6 NextTrace results, capability removal,
unchanged PID on no-op, checksum-verified executable replacement failure,
SIGKILL during the transaction, old-bootstrap refusal, and separate helper
restart recovery. It preserves helper hashes and boot-enable preferences,
verifies stopped Agent/Xray intent, performs a real remote wheel upgrade through
the old helper, and checks VLESS forwarding after transitions. A deliberately
faulty fixture wheel exits only under the newly granted raw capability, proving
rollback after a real systemd startup failure. No fixture wheels are published.
GeoIP is disabled; this smoke does not query public IP/ASN providers or register
public accounts. The designated VPS denies unprivileged ICMP datagram sockets
(`ping_group_range: 1 0`), so removing the raw capability also denies ICMP
fallback there. The smoke does not change that global setting. It removes the
isolated units/accounts on exit.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 360 tests; Agent: 269 tests; frontend: 98 tests and production build.
- Agent Ruff and the new smoke's Ruff checks passed. Existing Starlette/httpx
  deprecation and frontend bundle-size warnings remain.
- The full host-policy smoke passed over both transports using the previous
  bootstrap from commit `84d0bc3`, including genuine process termination and
  startup failure, private recovery metadata and unchanged helper hashes.
- The separate systemd installation/upgrade/rollback/uninstall smoke passed.
- The current remote lifecycle helper passed its complete regression smoke,
  including interrupted staging/switch/removal, durable final callbacks,
  retained data, real VLESS forwarding and confirmed desktop/mobile/narrow
  browser actions. Desktop and narrow/mobile screenshots were inspected.
- These results do not establish other OS/architecture coverage, public
  provider registration, fork-specific protocols or the remaining migration
  gates in [migration-map.md](migration-map.md).

## Native WARP Smoke

Build the current Agent wheel and frontend on the VPS. Use the backend test
environment with Playwright/Chromium and a trusted Debian Nginx executable:

```bash
python scripts/vps/smoke-warp.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx \
  --output /tmp/open-node-warp-shots
```

The root-only fixture installs disposable non-root systemd services and uses a
local TLS provider fixture with actual Xray WireGuard peers. Tests cover both
Agent transports, explicit first-registration consent, free-account status,
real IPv4/IPv6 encrypted forwarding, reapply, optional account/config updates,
Agent restart, blocked referenced-outbound removal, retryable provider failure,
preserved direct traffic, private state and non-disclosure in WARP results/logs.
Browser checks cover 1440px, 390px and 320px confirmation/result layouts. Host
routes and interface names must be unchanged after cleanup.

This does not create a public Cloudflare account or establish public-provider
compatibility. Live registration and deletion require operator acceptance of
Cloudflare terms. See [warp.md](warp.md#verification-boundary). The wheel is a
source build, not a replacement for immutable published Agent 0.1.0 artifacts.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 269 tests; Agent: 231 tests; frontend: 98 tests and production build.
- Agent/backend Ruff checks and the WARP smoke passed. Existing Starlette/httpx
  deprecation and frontend bundle-size warnings remain.
- The full non-root WARP fixture smoke passed over both transports, including
  real encrypted IPv4/IPv6 forwarding, restart, provider-failure recovery and
  inspection of desktop/mobile/narrow screenshots. No routes or interfaces changed.
- A fresh installation of the unmodified built wheel passed the independent
  Agent runtime smoke on both transports, including real VLESS traffic,
  provisioning/revocation, statistics, failed configuration recovery and
  persistent stopped-runtime intent.
- These results do not verify Cloudflare public registration or a paid WARP+
  account. No public provider terms were accepted by the test harness.

## Native Diagnostics Smoke

Build the current Agent wheel and frontend on the VPS first. Use the backend
test environment with Playwright/Chromium installed, a trusted Debian Nginx
binary, and the pinned NextTrace Tiny executable documented in
[agent-diagnostics.md](agent-diagnostics.md):

```bash
python scripts/vps/smoke-diagnostics.py \
  --wheel "$AGENT_WHEEL" \
  --nexttrace /path/to/verified/nexttrace \
  --nginx /path/to/nginx \
  --output /tmp/open-node-diagnostic-shots
```

The root-only fixture installs disposable non-root services, uses its own
trusted HTTPS/WSS gateway, and removes owned units/accounts on exit. It checks
real TCP and ICMP fallback, DNS failure, IPv4/IPv6 TCP trace hops, public
ASN/geolocation evidence, history ingestion, log ownership/clearing, persistent
VLESS traffic, and a default service without raw socket privileges. The public
GeoIP check needs upstream connectivity; it is not substituted with fixture
metadata. Browser checks cover 1440px, 390px and 320px layouts, real queued
probes, confirmed log clearing and scheduled return-route creation.

Verified on 2026-08-28 (UTC), on the designated Debian 12 x86-64 VPS:

- Backend: 267 tests; Agent: 182 tests; frontend: 95 tests and production build.
- Ruff passed for the Agent and diagnostic smoke. Existing backend deprecation
  and frontend bundle-size warnings remain.
- The installed non-root Agent passed the complete diagnostic smoke over both
  transports, including default-denied raw-socket behavior, public NextTrace
  ASN evidence, real scheduled-task dispatch, VLESS forwarding after log
  clearing, and inspected desktop/mobile/narrow screenshots.
- The separate real systemd install/upgrade/rollback/failure/recovery/uninstall
  smoke passed again. Fixture cleanup reported no remaining owned resources.

These checks do not establish broader OS/tool support, automatic in-place
permission changes, cross-version public-release upgrades, or completion of
the remaining [migration gates](migration-map.md). Agent 0.2.0 is a source
build here, not a replacement for the immutable published 0.1.0 assets.

## Control Plane Deployment Smoke

On the designated VPS, with Docker Compose and a trusted Nginx binary:

```bash
backend/.venv/bin/pip install -e 'backend[dev,browser]'
backend/.venv/bin/playwright install --with-deps chromium
AGENT_ENV="$(mktemp -d /tmp/open-node-package-agent.XXXXXX)"
python3 -m venv "$AGENT_ENV"
"$AGENT_ENV/bin/pip" install "$AGENT_WHEEL"
OPEN_NODE_IMAGE_TAG=local OPEN_NODE_REVISION="$(git rev-parse HEAD)" \
  docker compose --env-file /dev/null -f deploy/compose.yaml build
backend/.venv/bin/python scripts/vps/smoke-control-plane.py \
  --image-tag local \
  --agent-python "$AGENT_ENV/bin/python" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --output /tmp/open-node-package-shots
```

Build the Agent wheel with the normal test runner first. The image tag must
identify the image built from the checkout under test.
This smoke uses the shipped Compose file and HTTPS proxy template. It creates
randomized projects with loopback-only ports, private named volumes, a local
TLS identity, and a private Nginx prefix. No public CA, DNS account, host
certificate store, production service, or existing volume is modified.

It verifies non-root/read-only runtime restrictions, an empty installation,
administrator creation and recovery, Secure/HttpOnly/SameSite cookies,
Origin/CSRF rejection, SPA route reloads, API/static-file boundaries, and an
actual WSS probe stream. It then checks session, inventory, and encrypted-key
persistence after container/network recreation, a stopped-volume backup
restored into a new project, a changed-image upgrade, and explicit rollback
after a deliberately broken release fails startup. Temporary candidate images
and owned volumes are removed afterward. No arbitrary future database
downgrade, multi-host deployment, or zero-downtime upgrade is claimed.

The installed Agent also connects through HTTPS/WSS using only the fixture
CA, with TLS verification enabled. The full real-Xray forwarding, client
provisioning/revocation, failed-restart rollback, config recovery, telemetry,
and persistent-deduplication smoke runs on both transports against the
container. It uses the pinned Xray archive documented below; the optional
`--xray-archive` argument reuses a copy without bypassing its checksum.

The full operator browser smoke runs against the production image through
HTTPS at desktop 1440x900 and mobile 390x844. HTTP and WSS clients validate the
fixture certificate and hostname. Chromium allows only the generated fixture
SPKI via its per-process test switch, not a blanket TLS bypass. Screenshots
remain at `--output`; fixture credentials are not written there.

## Independent-Agent Smoke

After the normal test runner, install the built wheel into a separate environment
and run the real-runtime smoke on the VPS (Linux x86-64, Python 3.11+, and curl):

```bash
AGENT_ENV="$(mktemp -d /tmp/open-node-agent-wheel.XXXXXX)"
python3 -m venv "$AGENT_ENV"
"$AGENT_ENV/bin/pip" install "$AGENT_WHEEL"
backend/.venv/bin/python scripts/vps/smoke-open-node-agent.py --agent-python "$AGENT_ENV/bin/python"
```

The smoke downloads the official
[Xray v26.3.27 Linux 64-bit release](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27)
and verifies the archive SHA-256
`23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae`.
`--xray-archive /absolute/path/Xray-linux-64.zip` can reuse a downloaded archive;
the same digest check is mandatory. It extracts only the binary into a private
temporary directory, never installs over a host Xray binary, and deletes its
runtime fixtures on completion. The separate wheel environment remains available
for inspection. No MMWX image or activation server is involved.

For each transport (WebSocket and HTTP), the test starts disposable FastAPI,
Agent, Xray server/client, and HTTP fixture processes. All listeners use loopback
and ephemeral ports. It checks actual SOCKS-to-VLESS forwarding, new-client
provisioning and revocation without removing other users, per-user traffic
reporting, invalid config rejection with protocol-sized error messages, failed
runtime restart with file/traffic rollback, recovery test/write/restart, and
persistence of users and stop intent across Agent restarts. Redelivery is
simulated by requeuing one completed non-idempotent command only in the fixture
database; a second execution would fail, so a cached successful result proves
restart deduplication. Owned process groups are terminated on exit.

This proves the managed official-Xray VLESS path, not every protocol, encrypted
legacy-agent migration, systemd mode, or host install/upgrade/uninstall lifecycle.

## Native Limiter Smoke

Build the [free runtime](fork-runtime.md), Agent wheel and frontend on the VPS.
With the backend development environment, Chromium and a verified Mihomo binary:

```bash
backend/.venv/bin/python scripts/vps/smoke-native-limiter.py \
  --xray /absolute/path/to/free-runtime/xray \
  --mihomo /absolute/path/to/mihomo \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-native-limits
```

The fixture installs a dedicated non-root Agent over trusted HTTPS/WSS, imports
18 protocol variants and provisions their plan caps. It measures actual
combined upload/download rates and UDP target forwarding where supported,
checks real Vision TLS traffic, live cap changes on existing connections,
shared parallel buckets and admission quotas, automatic rules and persistence.
Its browser portion exercises desktop/mobile/narrow limit editing, stale
revisions and confirmed removal. It does not reuse existing host services.
Both Mieru underlays carry UDP targets and retain the authenticated user's
limiter context.

Core unit tests cover policy persistence, private files, stale revisions,
concurrent admission, live bucket updates and automatic rule timing. Run
`go test -race ./common/nodelimits` inside the matching source tree with an
isolated C compiler on the VPS. Do not run tests or builds on the local workstation.

Verified again on 2026-08-29 on the designated Linux amd64 VPS: the real smoke
measured all 18 TCP variants and all 18 UDP-target variants, including Mieru
TCP/UDP underlays. It also passed Vision TLS bulk, hot caps, shared credential
aliases, admission/release, parallel slot release, sustained/burst activation
and expiry, restart persistence and desktop/mobile/narrow editing. The current
runtime SHA-256 is
`7386109a5664ed83e23e38e48b41f09dddedf5092f09f51e35d182eb9fba2154`;
the rebuilt Agent wheel SHA-256 is
`a049c7b76a34341b01c3de6705edd8fa888011054330bb42b9133e371ed552f2`.
These results do not establish arbitrary OS, external-service or public-provider
compatibility.

## Agent Service Lifecycle

After building the Agent wheel, run the following on the designated VPS as root:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-service.py \
  --wheel "$AGENT_WHEEL"
```

This requires a running systemd manager plus `useradd`, `runuser`, and curl.
It uses the same pinned official Xray archive as the independent-runtime smoke;
`--xray-archive` can reuse that archive without skipping digest verification.
The fixture creates a uniquely named `open-node-agent-<id>.service`, dedicated
non-login account, and `/opt/open-node-agent-smoke-<id>` directory. It does not
reuse existing MMWX services, tokens, databases, unit names, or install roots.

The test verifies failed first installation and corrected-input retry, non-root
systemd readiness/hardening, real forwarding and runtime edits, successful
upgrade, explicit rollback, failed preflight without stopping the old process,
failed-start rollback, and recovery after forcibly terminating the deployment
process during a recorded switch. It also kills the Agent process to verify
systemd restart and Xray child cleanup, then checks uninstall/reinstall with
config/journal preservation and explicit purge of only owned files/account.

Good and deliberately broken candidate wheels are generated only inside the
test fixture with updated wheel records. They are not published artifacts.
Fixtures are removed at the end; failures print the service journal and report
any cleanup that needs attention. Stopped-Agent upgrades and path/ownership
guards have additional focused unit tests. External `runtime_mode: systemd`
and arbitrary future schema rollback are not covered by this smoke.

## Xray Multifile Takeover Smoke

Build the frontend and Agent wheel on the designated VPS. Run with the backend
development environment, Playwright Chromium, systemd, polkit and trusted Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-xray-takeover.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /absolute/path/to/nginx \
  --output /tmp/open-node-takeover-screenshots
```

The root-only fixture creates a disposable root-owned virtual environment and
dedicated non-root services. It obtains official Xray 26.3.27 using the same
pinned archive digest as the runtime smoke. An existing verified archive can
be supplied with `--xray-archive`. It never operates on an existing MMWX service.

Both HTTPS polling and WSS exercise repeated explicit JSON/JSONC inputs plus
a directory. A separate polling case uses only `-confdir`, with an existing
target inside it. Conflicting credentials, outbound order and routing distinguish
the actual core's merge from generic JSON merging. Real VLESS traffic verifies
the source and consolidated layouts, newly provisioned users and Agent restarts.
Checks cover secret-free GET previews, stale checksums, exact original-byte
backups, unchanged unit definitions, neutralized secondary files, repeated no-op
requests, and consolidation of a stopped service without starting it.

Fixture-only wheels inject real SIGKILLs after the prepared, stopping and
activating records and after the first config replacement. Restarted Agents
restore files and forwarding; interrupted commands are redelivered and return
409, not a manufactured success. An independent file edit blocks recovery until
the host repairs it. A real occupied listener makes Xray activation fail and
verifies delayed rollback after the port is released. These modified wheels are
never published. The unmodified wheel then reruns the existing external-systemd
fixture over both transports, including ownership and authorization guards.

Browser checks exercise preview, explicit acknowledgment, checksum-bound apply,
command completion and actual forwarding at 1440x900, 390x844 and 320x740.
The dialog scrolls internally, keeps actions visible and wraps long paths and
checksums. Unit tests also cover read-only previews during pending recovery,
backup-before-commit ordering, input/output size limits and file safety.

Recorded verification on 2026-08-28 (UTC) on the designated VPS:

- Backend: 451 tests; Agent: 434 tests; frontend: 99 tests, totaling 984.
- Frontend production build, Ruff and probe Worker TypeScript checks passed.
- The installed-wheel takeover fixture passed both control transports, the
  directory-only case, all crash/failure cases and both original systemd regressions.
- Desktop, mobile and narrow browser workflows passed; screenshots were inspected.
- The final Agent wheel SHA-256 is
  `b971c38c455a0a5adc5a7f74fb703a54f25301923da17a07a4ab74acc3731b77`.
- Existing Starlette/httpx deprecation and frontend bundle-size warnings remain.

The verified host scope remains Debian 12 x86-64 and official Xray 26.3.27.
Other runtime/OS combinations, arbitrary host-process adoption and crash recovery
for ordinary config mutations are not established by this workflow. See
[takeover boundaries](xray-takeover.md) and the other [migration gates](migration-map.md).

## External Systemd Smoke

Build the Agent wheel and install it into a separate, root-owned virtual
environment readable by the disposable service account. On the designated
systemd/polkit VPS, with the existing smoke dependencies and trusted Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-external-systemd.py \
  --agent-python /path/to/installed-agent/bin/python \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx
```

The root-only fixture creates unique non-root accounts, independent Agent/Xray
units and exact polkit rules. For HTTPS polling and WSS it verifies actual
VLESS forwarding, provisioning, user stats, invalid-write rejection, failed
restart rollback, Agent restart without Xray interruption, remembered stop
intent, binding mismatch while the Agent stays online, and grant revocation
without stopping the host-owned runtime. It rejects aliases, mismatched binary
paths and writable unit files. Negative permission checks cover unrelated
services, manager reload and enablement. The polling fixture also exercises
`CAP_NET_BIND_SERVICE` on both services. Modified rules cannot be overwritten
or removed; fixture resources are cleaned up after the run.

This proves the [documented single-file binding](external-systemd.md), not
multi-file takeover, other OS/architectures, public providers, or a durable
rollback after a crash in the middle of an ordinary config mutation.

Recorded verification for this milestone on the designated Debian 12 VPS:
365 Agent tests, 387 backend tests, 98 frontend tests, the production frontend
build, and Ruff checks passed. The final installed wheel passed the external
fixture over both transports. The independent managed-runtime smoke and real
host install/upgrade/rollback/interruption/uninstall smoke also passed. These
results do not close the other [migration gates](migration-map.md).

## Remote Agent Lifecycle

Build the Agent wheel and production frontend assets first. On the designated VPS,
with the browser/cryptography dependencies and a trusted Nginx binary:

```bash
backend/.venv/bin/python scripts/vps/smoke-agent-lifecycle.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --output /tmp/open-node-agent-lifecycle-shots
```

The fixture uses separate HTTPS release and controller endpoints with explicit
local CA trust. Both native transports perform version/digest-pinned upgrades,
rollback, wrong-digest rejection, failed-preflight/start recovery, and actual
VLESS forwarding after changes. Mismatched wheel metadata and redirects outside
the host-approved source are rejected. Unix socket ownership and a foreign-UID request
test cover both filesystem permissions and the peer-credential boundary.

One-shot candidate-wheel pauses allow the test to kill the maintenance cgroup
during package staging and service switching. It checks persisted recovery,
unchanged configuration, old-version traffic, explicit interrupted results,
request deduplication, expired-lease redelivery, skipped dependent commands, and
a new explicit retry after staging recovery. A paused shutdown verifies recovery
from a crash during removal, before the Agent service finishes stopping.
Final uninstall reports are temporarily rejected by the fixture proxy, proving
the controller cannot claim completion before acknowledgment. Worker restart,
eventual reporting, worker shutdown and data-preserving reinstall are checked.

The browser checks explicit version/SHA input, confirmation, actual command
completion and resumed progress at 1440, 390 and 320 pixel widths. It also reopens
the uninstall dialog while the Agent is gone but its callback is still blocked,
and waits for the actual acknowledgment before displaying completion. Chromium
trusts only the fixture SPKI; the Agent and host downloader use normal TLS
verification. Screenshots remain in `--output` without fixture credentials.

After publishing the matching wheel, verify the actual default GitHub release
source separately, using that exact release artifact on the designated VPS:

```bash
PUBLISHED_AGENT_WHEEL=/absolute/path/to/published/open_node_agent-wheel.whl
backend/.venv/bin/python scripts/vps/smoke-agent-release.py \
  --wheel "$PUBLISHED_AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx
```

This performs real public release downloads without a test mirror, checks the
wheel pin and running release identity, sends VLESS traffic and rolls back on
both transports. Its controller remains a private trusted HTTPS fixture.

## Nginx And Certificate Smoke

On the root-accessible systemd VPS, supply a trusted Nginx binary and matching
stream module. Debian packages can be downloaded and extracted into a disposable
directory with `apt-get download` and `dpkg-deb -x`, without installing a global
service. Install `cryptography` in the smoke runner environment, then run:

```bash
backend/.venv/bin/pip install cryptography
backend/.venv/bin/python scripts/vps/smoke-nginx.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

An optional `--xray-archive` uses the existing pinned-digest Xray fixture. The
test installs a separate non-root Agent service for each transport, then checks
real HTTP, verified TLS, leaf serial rotation, key mismatch rejection, actual
reverse-proxy and stream response bytes, invalid configuration and occupied-port
rollback, exact stream cleanup, private file boundaries, site deletion, logs,
independent stop intent, Agent/Nginx crashes, durable interrupted-file recovery,
and data-preserving uninstall/reinstall. Test certificates are local fixtures;
no public CA or real domain validation is used. Fixture units/accounts and
directories are purged after the run, with existing services untouched.

## Atomic Tunnel Smoke

Use the same binary/module fixtures and built Agent wheel as the Nginx smoke:

```bash
backend/.venv/bin/python scripts/vps/smoke-tunnel.py \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

For each transport, this installs a fresh non-root systemd Agent and exercises
the real FastAPI tunnel planner and queue. It verifies fresh deployment without
prior Nginx installation, hostname-verified TLS SNI routing to static and proxy
sites, unmatched SNI reaching a fixed loopback TLS fallback, actual traffic
statistics, post-deployment snapshot refresh, stale-template rejection,
Nginx/Xray occupied-listener rollback, and owned stream-to-Xray listener
handover while preserving a neighboring stream server. It injects a durable
multi-file undo record with conflicting stored start intentions, restarts the
Agent, and verifies both running and intentionally stopped recovery. A failed
cold deployment must leave both services stopped. Unit tests also cover
command cancellation, corrupt intent records, and idempotent map merging.

This verifies official Xray v26.3.27 on Debian 12 x86-64, not an arbitrary
future Xray schema, zero-downtime switching, or fork-specific protocol support.

## ACME Lifecycle Smoke

On the same VPS, install the test-only DNS fixture dependency:

```bash
backend/.venv/bin/pip install -e 'backend[dev,browser,acme-test]'
```

Supply the verified lego v4.35.2 binary described in
[certificate setup](certificates.md#host-setup), and the
[Pebble v2.6.0 release](https://github.com/letsencrypt/pebble/releases/tag/v2.6.0).
The tested `pebble-linux-amd64.tar.gz` archive has SHA256
`ce5d87e1f674934c134b7cbcbc468e3df420994a17e77bdbf7aec611e2d373b9`.
Verify before extraction; the Pebble binary needs executable permission.

```bash
backend/.venv/bin/python scripts/vps/smoke-certificates.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so
```

This root-accessible systemd test needs free UDP/TCP port 53 on `127.0.0.1`
and `::1`. It binds exclusively and fails instead of replacing an existing
listener. Existing DNS services on other addresses remain untouched; neither
`/etc/hosts` nor `/etc/resolv.conf` is modified. The fixture's authoritative
NS is `localhost`, keeping lego's OS-level NS address lookup offline. ACME,
webhook, backend, Agent and Nginx listeners are all loopback-only.

The test does real DNS ownership validation, not Pebble's always-valid mode.
It verifies HTTPS CA trust, EAB account creation, apex plus wildcard SANs,
TXT presentation and cleanup, not-due skips, credential rejection retaining
the active certificate, forced renewal, backend restart persistence, and
actual elapsed-time automatic renewal of four-minute certificates. Real
non-root Agent services then deploy/reload the certificate and restore a
historical version, checking trusted TLS leaf serials and HTTP bytes for
both transports. Test services, accounts, DNS listeners and private state
are removed on completion. No public CA or real DNS account is used.

## HTTP-01 Lifecycle Smoke

Use the same VPS dependencies, pinned binaries and free loopback DNS ports as
the ACME lifecycle smoke above. Build the frontend on the VPS first. This test
also needs the existing Debian `www-data` account for an independently running
non-root Nginx:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-http.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/extracted/usr/sbin/nginx \
  --nginx-stream-module /path/to/extracted/usr/lib/nginx/modules/ngx_stream_module.so \
  --screenshots /tmp/open-node-http01-screenshots
```

The browser creates and issues standalone and webroot profiles without a DNS
provider. It checks mode-specific controls, wildcard rejection, CA consent,
renewal controls, collapsed/expanded EAB fields and 1440/390/320px layouts.
Pebble fetches actual challenge responses through a loopback fault-injection
hop and Nginx: standalone requests reach lego's listener, while webroot
requests read real public challenge files as a different Unix user.

The test covers SAN issuance, not-due skips, deliberate HTTP validation
failure, forced renewal, file/listener cleanup and active-version preservation.
It kills the backend while lego survives, verifies the inherited worker lock,
then kills lego and checks interrupted-job recovery and stale-token removal.
Both modes renew automatically after actual elapsed time. HTTP-issued
certificates are deployed to non-root Agent Nginx instances over WebSocket
and HTTP, with trusted TLS serial checks and version rollback.

The website's original content is checked unchanged, and all private vault
files are checked for private permissions. Test listeners, processes, Agent
services and data are disposable; public-CA orders and production websites
are not used. This does not prove an operator's public DNS/port-80 routing.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 317 tests; Agent: 231 tests; frontend: 98 tests and production build.
- HTTP-01 standalone/webroot and existing DNS-01/EAB lifecycle smokes passed,
  including real automatic renewal and trusted Agent TLS/version rollback.
- HTTP hard-crash recovery retained the old certificate and removed stale
  challenge responses only after the surviving lego process released its lock.
- The operator browser regression passed. HTTP forms and expanded EAB fields
  were checked at 1440px, 390px and 320px, including fully visible submit controls.
- Additive SQLite migration retained DNS/imported profiles. EAB-only HTTP
  catalogs also detect missing vault keys instead of generating a replacement.
- Ruff passed for changed backend modules and the HTTP smoke. Existing
  Starlette/httpx deprecation and frontend bundle-size warnings remain.

## Remote HTTP-01 Smoke

Build the frontend and current Agent wheel on the VPS. Use the backend
development/browser/ACME-test extras and the same pinned Pebble, Nginx and
official Xray artifacts as the existing ACME and Agent smokes:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-remote.py \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --wheel "$AGENT_WHEEL" \
  --nginx /path/to/nginx \
  --nginx-stream-module /path/to/ngx_stream_module.so \
  --screenshots /tmp/open-node-remote-http01-screenshots
```

The fixture starts a TLS-verified controller without lego or central HTTP-01
listeners. Real non-root systemd Agents connect over HTTPS polling and WSS.
An EAB-required Pebble CA reads standalone responses and owned Nginx webroots
on those nodes, through an observable fault-injection proxy. It never supplies
synthetic successful challenge data.

The workflow covers issue, not-due skip, failed validation retaining the old
version, forced renewal, node-disconnected cleanup and reconnect, actual TLS
deployment, account contact changes and elapsed-time automatic renewal. A
controller hard kill leaves the ACME child holding the inherited lock; after
the child is killed, recovery must reuse the same job/order and create a new
challenge lease after cleaning the old one.

Playwright creates remote profiles, selects validation nodes, checks wildcard
rejection and explicit terms/EAB fields, and reads issued versions. Layout
checks and screenshots cover 1440px, 390px and 320px. The test leaves existing
services untouched and removes its temporary systemd users/services/directories.
It does not use public CA orders or provider accounts.

Focused backend tests cover additive scan/profile migration, live capability
checks, command/lease receipts, cleanup retries, deletion protection, cancellation,
order-response loss, persisted CSR/key binding and public-only EAB payloads.
Agent tests cover host opt-in, exact HTTP host/path/token matching, expiry,
idempotent release-before-present ordering, restart, occupied ports, immutable
leases and filesystem replacement/link protection.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 387 tests; Agent: 304 tests; frontend: 98 tests and production build.
- Remote standalone/webroot issuance, EAB, HTTPS/WSS, cleanup after reconnect,
  inherited-lock/order recovery and elapsed-time renewal with live TLS passed.
- Existing DNS-01/EAB, control-plane HTTP-01 and account/revocation lifecycle
  smokes passed, including forced interruption and automatic renewal.
- Desktop/mobile/narrow screenshots were inspected; the changed Python code
  passed Ruff. Existing Starlette/httpx and frontend bundle-size warnings remain.

## Certificate Administration Smoke

Use the same pinned lego/Pebble binaries, backend development/browser/ACME-test
extras and free loopback DNS ports as the lifecycle smokes. Build the frontend
on the VPS first:

```bash
backend/.venv/bin/python scripts/vps/smoke-certificate-administration.py \
  --lego /path/to/lego-4.35.2/lego \
  --pebble /path/to/pebble-linux-amd64/linux/amd64/pebble \
  --screenshots /tmp/open-node-ca-admin-screenshots
```

The fixture uses real HTTP-01 issuance and an EAB-required CA. It preserves a key
left by failed registration, edits EAB before registration, updates the registered
CA contact while checking the original key/URI, and renews with lego afterward.
Historical-version revocation is independently checked at Pebble's management API.

A TLS-verified forwarding fixture deliberately loses accepted account/revocation
responses. Retries must query and reconcile actual CA state, including
`alreadyRevoked`. A backend hard kill while the helper holds a confirmed response
verifies inherited locking and durable receipt recovery without a duplicate request.
The test also checks forced new-key reissuance, imported certificate revocation,
duplicate blocking and ledger retention after profile deletion.

Playwright operates account/EAB and revoke/retry dialogs with real backend requests.
Screenshots and layout checks cover 1440px, 390px and 320px, including visible
confirmation controls, masked credentials and disabled revoked-version actions.
The fixture checks private permissions and removes temporary request files.
No public CA, DNS-provider credential, production certificate or website is used.

The focused `test_certificate_administration.py` suite additionally exercises
input/secret validation, additive schema migration, competing deployment/revocation
and import transactions, retained commands without targets, receipt mismatches,
graceful cancellation and revoked on-disk candidate recovery.

Verified on the designated Debian 12 x86-64 VPS:

- Backend: 360 tests; Agent: 231 tests; frontend: 98 tests and production build.
- The administration smoke passed with actual CA contact/status checks, lost
  responses, hard restart, duplicate/import protection and new-key reissuance.
- Existing DNS-01/EAB and HTTP-01 standalone/webroot smokes passed, including
  automatic renewal, both Agent transports, trusted TLS and version rollback.
- Operator UI regression and 1440/390/320px account/revocation layouts passed.
  The final browser run also checks the revocation icon's loaded glyph.
- Ruff passed for changed backend code and the new smoke. Existing
  Starlette/httpx deprecation and frontend bundle-size warnings remain.

## Reference-Agent Smoke

After installing the backend development dependencies, run this on the VPS
with Docker available:

```bash
docker pull ghcr.io/iluobei/mmw-agent@sha256:d9ff8cd1525947e1e535ca49d6b22f1b63ff28d393c46efea6f88eeb40e8840d
backend/.venv/bin/python scripts/vps/smoke-reference-agent.py
backend/.venv/bin/python scripts/vps/smoke-reference-agent.py --secure-channel
```

The script uses the unmodified `mmw-agent` 0.4.7 image pinned by digest. It
creates a private, internal Docker network, a temporary SQLite database and
config directories, and a backend listener on that bridge with an ephemeral
port. The agent has no host-network access, published ports, or host config
mounts. Container capabilities are dropped. Only disposable files are
modified, and the container, network, and backend are removed when it exits.

The smoke verifies actual `/api/remote/ws` authentication, the initial config
snapshot, an agent-validated config write, the automatic WebSocket refresh
and its returned config, restart-induced drift, and manual acceptance of the
pending config. It also checks sequential recovery validation/write and the
failure path: when the real agent returns HTTP 200 with `ok=false`, neither
the write nor restart is attempted and a previously repaired healthy config
is unchanged on disk. With `--secure-channel`, it also verifies rejection of
wrong and malformed pins before registration, encrypted round trips, and fresh
encrypted sessions after controller restart with the same stored identity.
Both modes run in external Xray mode without a live Xray process. They do not
prove forwarding traffic, embedded runtime behavior or legacy HTTP callbacks.
They do not make the reference image the distributable Open Node agent.

## Operator Browser Smoke

Install the optional browser dependencies and Chromium on the VPS, then run:

```bash
backend/.venv/bin/pip install -e 'backend[browser]'
backend/.venv/bin/python -m playwright install --with-deps chromium
backend/.venv/bin/python scripts/vps/smoke-operator-ui.py --output /tmp/open-node-ui-artifacts
```

The script creates a temporary administrator/database and starts disposable
FastAPI and Vite processes on loopback ports. It checks that private views do
not load before sign-in, rejects an incorrect password, creates a server
through the UI, verifies session persistence across reloads, changes the
password on mobile, checks rejection of the old password, signs out, and
expires a session to verify the UI returns to sign-in. It captures desktop
and mobile login/access screenshots and checks horizontal overflow and form
control bounds. Services and database files are removed on completion; only
the requested screenshots remain. No existing administrator is changed.

Certificate coverage also creates a DNS provider and profile, requires explicit
CA terms, imports a real PEM pair, downloads certificate/private key separately,
verifies secret fields clear on reopening, and checks desktop/mobile forms.
Private keys and provider credentials must not appear in browser storage.

The Access page also verifies the configured Agent public key/fingerprint,
native clipboard copy and desktop/mobile layout. The disposable browser fixture
creates its own private identity. The production-image smoke creates the seed
with the non-root container CLI and verifies refusal to overwrite it, private
permissions and identity preservation through container recreation and volume
backup/restore; its HTTPS browser run checks the same public metadata.

The reference-agent smoke also creates a temporary administrator and signs in
as the operator; the reference agent still authenticates only with its own
bootstrap token. No test disables management authentication.

## Managed Xray Release Smoke

With the backend's browser extra/Chromium and a built Agent wheel on the VPS:

```bash
backend/.venv/bin/python scripts/vps/smoke-xray-releases.py \
  --wheel "$AGENT_WHEEL" \
  --output /tmp/open-node-xray-release-shots < /dev/null
```

This root-only fixture installs dedicated non-root systemd Agents and uses
official Xray `v26.2.6` and `v26.3.27` archives. Each transport verifies real
version changes, process executable paths, actual VLESS forwarding, checksum
rejection, validation before stopping the old runtime, and geodata discovery.
It checks untouched root-owned bootstrap binaries and unchanged user config.

The ordinary wheel is exercised first. A separate fixture-only wheel then
supplies deterministic occupied-port and interruption faults while retaining
the real Xray binaries. The smoke verifies failed-start rollback, timeout
recovery, process-group crash recovery, an explicit interrupted-command result,
and restoration of the ordinary Agent wheel. Removal/reinstallation preserve
configuration and stopped intentions. Desktop/mobile browser checks submit
real version/checksum requests and require acknowledgment before rollback.
Temporary installations/accounts are purged; requested screenshots remain.

Unit coverage also checks archive/path/size boundaries, cached file integrity,
version mismatch, initial missing config, no-op reinstall preserving rollback,
unresolved transaction rejection and removal with a damaged config. See
[xray-releases.md](xray-releases.md) for ownership and recovery semantics.

## Multi-Node Change-Set Smoke

On the designated VPS, build the frontend, install the backend's `browser`
extra and Chromium, and install the Agent wheel into a separate environment.
Then run:

```bash
backend/.venv/bin/python scripts/vps/smoke-change-sets.py \
  --agent-python "$AGENT_ENV/bin/python" \
  --output /tmp/open-node-change-artifacts < /dev/null
```

This uses the same pinned official Xray archive as the independent-agent smoke
and accepts `--xray-archive` for a checksum-verified local copy. It starts an
authenticated disposable FastAPI controller with the production frontend,
two installed Agents and real VLESS traffic for WebSocket/WebSocket, HTTP/HTTP
and mixed transport pairs. Temporary gates verify forward ordering, reverse
rollback ordering, cancellation while a forward command is executing, and
automatic compensation after native Xray validation fails. Bootstrap and
newly provisioned client traffic are checked before and after recovery.

The mixed pair also exercises the real browser rollback-failure/retry workflow,
retained command history, incomplete compensation, and explicit acceptance
with a required reason and checkbox on desktop and mobile. Layout failures
retain screenshots and element-bound diagnostics. Temporary processes and
private state are removed; only requested artifacts remain. Unit tests cover
lease races, overlapping reservations, draining earlier sequences, late
rollback rejection, restart persistence and missing-column SQLite migration.

## Earlier Registration Invitation Verification

Registration invitations passed on the designated VPS:

- Backend full regression: 883 tests. Frontend: 32 files and 216 tests, Vue
  typecheck and production build. Ruff passed for all backend sources, tests and
  the new smoke script.
- Focused coverage passed digest-only persistence, working subscriber login,
  exact plan/runtime enrollment, generic invalid/revoked/expired/used responses,
  case-insensitive username retry, atomic concurrent claims, plan cleanup and
  administrator/public route isolation.
- The WebSocket smoke installed a temporary non-root Agent, claimed a plan-bound
  invitation, waited for the durable access command, exported the invited user's
  Xray configuration and forwarded 32 KiB of real TCP traffic. Reuse failed with
  the generic unavailable response and temporary services were removed.
- The production preview completed actual HTTP registration and subscriber login.
  Administrator invitation management and the invited account form passed at
  1440, 390 and 320 pixels without horizontal overflow or console errors.
- A copy of the prior preview database retained every count across 48 existing
  tables and two existing rows. Startup added an empty `registration_invitations`
  table and `PRAGMA foreign_key_check` remained clean.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not enable open anonymous signup or close the remaining
[migration gates](migration-map.md).

## Earlier Template Verification

Custom Clash and Surge templates passed on the designated VPS:

- Backend full regression: 858 tests. Frontend: 205 tests and production
  build. Ruff formatting and checks passed for all backend sources, tests and
  the subscription-client smoke script.
- The existing 18-variant client fixture passed real Mihomo, sing-box and Xray
  forwarding. Its focused template run passed administrator/subscriber CRUD,
  revision guards, personal permission, plan/system defaults and catalog
  remapping without changing credentials, tokens or the runtime PID.
- Custom Clash output was downloaded from the public endpoint, loaded by
  Mihomo and forwarded real TCP and UDP traffic. Custom Surge output from the
  same endpoint preserved the non-proxy profile text and matched the exact
  compatible node set under an independent parser.
- Administrator and subscriber workspaces passed at 1440, 390 and 320 pixels.
  Actual Surge application import remains an Apple-platform gate.
- Agent sources did not change. The prior 536-test Agent baseline, wheel and
  free-core artifacts were reused by the real lifecycle/client smoke.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Automatic Speed Rule Verification

Per-plan automatic speed rules passed on the designated VPS:

- Backend full regression: 815 tests. The final command-payload guard then
  passed 99 focused tests, including four new malformed-payload cases.
  Agent: 536 tests and wheel build; frontend: 202 tests and production build.
- The free core rebuilt successfully; protocol/core tests and the native
  limiter/dispatcher race tests passed. The existing multi-protocol smoke
  passed real TCP and supported UDP limits, Vision TLS bulk, shared connection
  quotas, live updates, sustained/burst rules, expiry and restart persistence.
- HTTP and WebSocket plan smokes passed create/edit/order/clear, validation,
  sequential input, native-editor preservation and independent subscribers.
  A 64 KiB echo took about 2.00 seconds under the automatic 0.5 Mbps combined
  cap, and under 1 ms for the other plan and after expiry, on this local VPS
  fixture. This is an enforcement check, not a network performance benchmark.
- Credentials, subscription exports and tokens stayed unchanged. Runtime
  policy survived restart; unchanged hot policy saves preserved active timers.
  Old Agent/core capability rejection, catalog roundtrips, legacy omission
  and additive schema upgrades passed focused tests.
- Desktop 1440px, mobile 390px and narrow 320px screenshots were inspected.
  Ruff passed for changed Python sources and smoke scripts. Temporary non-root
  Agent installations were removed after each smoke.

Verified Linux amd64 artifacts for this milestone:

- Agent wheel SHA-256:
  `7cf9f6463e13f691dbf198ded77fa49f3923cd600d64f507a47f2fb52a4374ca`.
- Free core SHA-256:
  `348434f6700cd49df8015c7707910fdc1bbfd196f9ea3fea05f8ed4189d4dc7a`.
- Matching MPL-2.0 source archive SHA-256:
  `4c0fa9c730ea58f88e3b0d5dca5b1a456085a3933a69d29eb73cf1dc79f63d43`.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Plan Alias Verification

Plan node aliases passed on the designated VPS:

- Full regression: backend 791 tests, Agent 522 tests, frontend 187 tests and
  production build passed. The earlier focused backend run passed 159 tests.
- All five subscription formats and previews use aliases before multipliers;
  reserved/original-name collisions, Unicode validation, isolated plans,
  preserved runtime records, legacy field omission, catalog remapping/rollback,
  node/server removal and repeated SQLite upgrades passed.
- Final HTTP and WebSocket browser runs passed creation, alias edits, stale
  revision rejection, disable/clear, and subscriber downloads. The downloaded
  Xray configuration forwarded real traffic while the runtime PID, credentials,
  subscription keys and unrelated plan remained unchanged.
- Desktop 1440px, mobile 390px and narrow 320px screenshots were inspected.
  Ruff passed for all changed Python sources and the smoke script. Temporary
  Agent installations were removed by the fixture.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Short-Code Verification

Custom subscription short-code verification on the designated VPS:

- Full regression: backend 765 tests, Agent 522 tests, frontend 177 tests and
  production build passed. After the final additive lookup-index change,
  84 focused backend tests passed, including a new query-plan check and both
  new-database and old-schema upgrade coverage.
- The final schema uses indexed lookups for long, generated and custom keys;
  the preceding table-scan query plan was reproduced and eliminated.
- The final WebSocket and HTTP runs passed operator/subscriber edits, stale
  revisions, case collisions, password/TOTP proof and actual browser downloads
  through the custom short URL. The downloaded Xray configuration forwarded
  real traffic. Clearing and resetting links preserved the runtime PID and
  node credentials; another subscriber kept forwarding.
- Desktop/mobile/narrow screenshots were inspected. Ruff passed for changed
  Python sources. Temporary Agent installations and private state were removed.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Subscriber Limit Verification

The subscriber-limit worktree passed on the designated VPS:

- Backend: 725 tests; Agent: 522 tests; frontend: 153 tests and production build.
- Ruff passed for the changed Python sources and the new smoke fixture.
- Real non-root installed Agents applied user/default/node speed and connection
  caps over trusted WebSocket and HTTP polling, including explicit unlimited,
  restored plan inheritance and persisted limits after an Agent restart.
- A paused Agent left existing forwarding available while quota withdrawal was
  pending. Reconnection denied the old credentials; raising the quota restored
  those same identities without resetting charged usage. Another subscriber
  kept forwarding throughout the quota changes.
- Browser checks covered stale saves, invalid values, subscriber visibility and
  1440px, 390px and 320px layouts. Screenshots were inspected. Fixture services
  and private state were removed after both transport runs.

The existing Starlette/httpx deprecation and frontend bundle-size warnings remain.
These results do not close the remaining [migration gates](migration-map.md).

## Earlier Release Verification

The managed Xray release worktree passed on the designated VPS:

- Backend: 252 tests; Agent: 110 tests; frontend: 87 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- Real non-root systemd Agents changed between official Xray v26.2.6 and
  v26.3.27 over WebSocket and HTTP. Tests checked actual executable paths,
  VLESS forwarding, archive geodata, untouched root-owned bootstrap files,
  unchanged user configuration and checksum/validation failures.
- Fixture-only faults verified occupied-port rollback, command timeout,
  process-group crash recovery and an explicit interrupted-command result.
  Agent wheel rollback retained the selected runtime. Removal, stopped
  reinstallation and explicit service start preserved configuration.
- Installed-Agent forwarding, provisioning, revocation, failed-start recovery,
  journal deduplication and stop-intent checks passed again on both transports.
- Host service upgrade/rollback/removal, real Nginx HTTP/TLS and certificate
  rotation, atomic tunnel recovery and all three multi-node transport pairings
  passed again. Fixture installations and accounts were removed afterward.
- Desktop 1440x900, mobile 390x844 and narrow 320x740 release dialogs submitted
  real version/checksum requests, displayed the complete checksum and required
  acknowledgment before rollback. Each change was followed by real forwarding.
- The production image passed HTTPS/WSS, installed-Agent forwarding, private
  identity and session persistence, volume backup/restore and image rollback.
  Its operator flow also verified the complete product name at 320px after
  compacting the edition badge; desktop/mobile screenshots were inspected.
- The unmodified pinned reference Agent passed encrypted authentication,
  controller restart, config refresh, drift acceptance and validation-gated
  recovery again.

These results do not close the remaining gates in
[migration-map.md](migration-map.md), including remote Agent lifecycle
handlers and broader protocol/host coverage. Existing Starlette/httpx
deprecation and frontend bundle-size warnings remain.

## Earlier Encrypted-Agent Verification

The encrypted-Agent and safe-sync worktree passed on the designated VPS:

- Backend: 242 tests; Agent: 86 tests; frontend: 84 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- The unmodified pinned reference Agent passed both plaintext compatibility
  and encrypted WebSocket auth, config writes, refresh, controller/Agent
  restart and recovery. Wrong pins and malformed-pin plaintext fallback were
  rejected without registering the Agent or issuing work.
- Replay/tamper/direction/sequence checks, handshake deadlines, private-key
  files, concurrent send order, UTF-8/finite JSON and oversized historical
  command handling passed. Attempted historical work is not falsely completed.
- The production image passed HTTPS/WSS, real Xray forwarding on both native
  transports, private identity creation, non-overwrite, recreation and volume
  restore. Public key/fingerprint display and real clipboard copy passed in
  the desktop/mobile operator flow; screenshots were inspected.
- The real two-node WebSocket/WebSocket, HTTP/HTTP and mixed-transport smoke
  passed again, including reverse compensation, in-flight cancellation and
  the desktop/mobile retry and explicit-acceptance workflows.
- The sync launcher passed real loopback SSH with key authentication and
  PowerShell 7.6.5 on Linux. Git fixtures verified non-destructive refusal of
  dirty/diverged/wrong-origin/wrong-branch checkouts and ignored-file conflicts.
  Windows PowerShell itself was not executed because tests run only on the VPS.

These results do not close the remaining runtime gates in
[migration-map.md](migration-map.md), including remote runtime lifecycle
handlers and broader protocol/host coverage. Existing Starlette/httpx
deprecation and frontend bundle-size warnings remain.

## Earlier Change-Set Verification

The coordinated change-set worktree passed on the designated VPS:

- Backend: 189 tests; Agent: 86 tests; frontend: 82 tests and production build.
- Ruff and Probe Worker TypeScript checks passed.
- Real two-node WebSocket/WebSocket, HTTP/HTTP and mixed-transport changes
  verified ordered execution, actual client forwarding, reverse compensation,
  cancellation in flight and automatic recovery after native validation failure.
- Desktop/mobile browser flows verified compensation retry, expanded command
  results, retained history, required acceptance reason/acknowledgment and live
  status. A deliberately delayed list response cannot overwrite a newer action.
- Independent installed-Agent and pinned reference-Agent smokes passed again,
  including snapshot refresh, validation-gated recovery and persistent journal
  behavior. No reference source is needed by the independent Agent.
- Missing-column SQLite upgrades preserve old command outcomes and pause legacy
  execution for review, including concurrent ordinary dependency sequences.

These results do not close the other runtime gates in
[migration-map.md](migration-map.md). Existing Starlette/httpx deprecation and
frontend bundle-size warnings remain.

## Earlier Certificate Verification

Certificate management was verified on the designated VPS:

- Backend: 167 tests; Agent: 86 tests; frontend: 77 tests and production build.
- Probe Worker type checks and Ruff passed.
- Real Pebble DNS-01/EAB, wildcard issuance, automatic and forced renewal,
  restart persistence, failure preservation, and trusted TLS/version rollback
  passed over both Agent transports.
- Browser certificate forms, terms confirmation, secret clearing and explicit
  PEM downloads passed on desktop and mobile; screenshots were inspected.
- Installed Agent, systemd lifecycle, Nginx, tunnel and reference-agent smokes
  passed again. No public-CA orders or real DNS credentials were used.

Public-provider staging and the remaining migration gates are not covered by
these results. Existing deprecation and bundle-size warnings remain.

## Previous Verification

On 2026-08-27 (UTC), the atomic-tunnel worktree passed on the VPS:

- Backend: 153 tests, including HTTP/WebSocket Nginx scan reporting, legacy SQLite
  scan-schema migration, anonymous management-route rejection, session
  persistence/expiry/revocation, CSRF/Origin rejection, concurrent login limiting,
  a password-reset/login race, administrator CLI recovery, and the existing
  inventory, dependency, migration, subscription, and change-set suites. Native
  tunnel coverage checks profile/capability selection, snapshot prerequisites,
  listener validation, generated paths/config, and post-deploy refresh.
- Independent agent: 86 tests, including private state/lock protection, TLS
  configuration, persistent deduplication, transport reconnects, heartbeats
  during commands, interrupted execution, bounded errors/subprocesses, atomic
  rollback, client edits, stop intent, network rate calculation, deployment
  ownership/path guards, package identity, activation recovery, and readiness checks.
  New coverage includes certificate matching/SAN/dates, file-boundary enforcement,
  include parsing/cycles, multi-file rollback, command cancellation, interrupted-file
  recovery, exact stream cleanup, separate stop intent, and master PID reuse guards.
  Coupled tunnel tests cover fresh files, map merging, stale snapshot rejection,
  start/cancellation rollback, durable file/intent recovery, invalid metadata,
  loopback stats discovery, and dynamic path rejection.
- Agent wheel: isolated build and installation into a separate environment;
  real Xray smoke passed over WebSocket and HTTP, including provisioning,
  revocation, actual forwarding/statistics, failed-start rollback, recovery,
  restart deduplication, and preserved stop intent.
- Real systemd lifecycle: failed first installation/retry, non-root service
  ownership and forwarding, upgrade/rollback, failed preflight and startup,
  interrupted-switch recovery, crash restart with child cleanup, data-preserving
  uninstall/reinstall, and explicit purge. No fixture units/accounts remain.
- Real Nginx: both transports with non-root Debian Nginx 1.22.1, verified HTTP/TLS,
  leaf serial rotation, key rejection, proxy/stream response bytes, occupied-listener
  rollback, exact stream cleanup, Agent and Nginx master crash recovery, interrupted
  file recovery, site deletion, and data-preserving service removal/reinstallation.
- Native tunnel: both transports with the real planner, queue, and installed
  wheel; verified TLS static/proxy/fallback bytes, traffic reporting, stale hash
  rejection, both runtime port conflicts, owned listener handover, and recovery
  of files plus running/stopped intentions. Failed cold deployment leaves no
  unwanted running service.
- Frontend: 74 tests, including session/CSRF request handling, expired-session
  transitions, waiting/skipped Vuetify component rendering, and the production build.
- Probe Worker: TypeScript checks.
- Ruff: backend, independent agent, and all six smoke scripts.
- Reference-agent smoke: all ten stages, with the pinned image.
- Chromium operator smoke: desktop 1440x900 and mobile 390x844 sign-in/access,
  server creation, reload persistence, password change, logout, and expiry. Nginx
  form defaults, page/control bounds, and full visibility of the active tab are
  also checked on both viewports, with configuration screenshots. Tunnel form
  checks cover default node-owned paths, duplicate/out-of-range port rejection,
  real request payloads, and single-line toggle text. Desktop and mobile
  screenshots were inspected after fixing the narrow desktop toggle layout.

The backend test run still reports a Starlette/httpx deprecation warning, and
the frontend build reports a large bundle warning. Neither is a failed check.
