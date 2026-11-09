# Bounded web push transport

Card: WR617.S1.J269

## System design

The data shape is an outbox row unique on notification, installation and subscription generation, plus an immutable attempt identity and increasing attempt number. PostgreSQL owns policy, due work, claims, attempts and provider/key circuits. The sender owns encrypted HTTP submission. Existing authenticated registry verbs supply identity and generation fencing. `read-web-push-delivery-status-v1` returns the owner's latest 100 deliveries through `web-push-delivery.v1`. It exposes neither endpoints nor keys.

Two designs were considered. Adding a queue service would add another persistence and replay authority. The selected Postgres outbox keeps claim, rate reservations, fencing and settlement behind one command interface. No source admission producer is enabled. Slices 6 and 7 must insert outbox intent within their source transaction, retaining canonical notification ownership and exact target references. Application roles have no direct table grants.

At present and 10x load, scheduled work remains at most four candidate claims per invocation and four persisted attempts per minute across concurrent consumers. Submission is serial. One database connection is used. Actor limits are twelve distinct logical notifications per rolling hour and forty per rolling day. Retries consume the global attempt budget. The first bottleneck is queue age during bursts and registry lock contention. Status exposes creation, due and expiry timestamps. There is no N+1 scan over subscriptions. The due index bounds candidate selection; rate queries use the attempt-time index. Historical retention remains a separate operational concern.

`ops.web_push_policy` is the single configuration home for batch size, actor caps, maximum lifetime, attempts, deadlines, backoff and breaker thresholds. Constraints bound increases to the reviewed ceilings. There is no cache of eligibility, permission, subscription or circuit state. Database unavailability refuses submission. Reads require current state.

The claim transaction records a fresh attempt UUID before HTTP. A second transaction obtains the same advisory lock used by registration, replacement, revocation and logout, checks source eligibility and current subscription generation, submits HTTP, settles and commits. It holds the lock until settlement. Revocation that commits first prevents submission. An already-started submission finishes before revocation can commit. Accepted messages cannot be recalled. A lost database settlement leaves a committed attempt for lease recovery, which records `unknown` without another HTTP call. A non-null matching fencing token and unexpired lease are required for both preflight and settlement. Missing, JSON-null, wrong and expired tokens return null without exposing transport data or changing the outbox or attempt.

Quiet hours use the existing timezone and overnight-window semantics. Disabled intent, quiet hours, volume caps and circuits persist a hold and bounded due time. Every hold rechecks source, generation and expiry. Expiry is capped by both outbox expiry and policy lifetime; HTTP TTL uses the remaining lifetime. There is no urgent bypass.

Explicit 429 and 5xx rejections retry with at most three attempts, exponential backoff, jitter and bounded Retry-After. 400 and other permanent responses stop. 404/410 expires that exact installation generation. Two authentication failures or five transient failures open a provider/key circuit for fifteen minutes. The registry lock serializes the first post-cooldown probe; rejection reopens and acceptance resets it. Timeout, connection loss and interrupted submission are terminal unknown outcomes. Provider acceptance is never phone delivery.

The pinned `@block65/webcrypto-web-push` 2.0.0 builds RFC 8291 encrypted requests and RFC 8292 authentication using Web Crypto. Its request-building interface permits a caller-owned fetch with redirect refusal, deadline and controlled tests. No crypto algorithms are authored here. The initial `@negrel/webpush` candidate owns fetch, which prevents enforcing those transport controls without global replacement. Source evidence is [the selected library's release](https://github.com/block65/webcrypto-web-push/releases/tag/v2.0.0).

Private VAPID material comes only from Worker secret `WEB_PUSH_VAPID_PRIVATE_KEY`. Public key and subject use `VAPID_PUBLIC_KEY` and `WEB_PUSH_VAPID_SUBJECT`. `WEB_PUSH_ENABLED` must equal `true` and all bindings must exist before a database connection is opened. This PR provisions no secrets and does not activate sending. Encryption tests generate throwaway keys. The envelope contains schema version, notification UUID, target reference and a fixed generic category. Endpoint allowlisting matches the registry. Redirects refuse. Logs contain a fixed event and correlation UUID; SQL comments carry the same UUID. No provider response body, endpoint, key or source detail is logged.

The minute cron also hosts existing watch work, retaining its five-minute cadence. There is no KV, R2, AI, recursive invocation or unbounded retry work. The current spend doctrine is `cloudflare-edge-sop#06-observability-and-cost`, section `f04bdffb-e70e-464a-90d1-6e4b80d7b44c`, version 1, hash `d794fe7ca190ac398979dd314f10e2f56fd74b6b3057e6a79f851708f611710b`. It requires bounded retries, backoff, correlation IDs, sensitive-data-free logs and low-threshold usage notifications to both partners. Cloudflare has no hard spend cap. Account usage-alert settings are unverified in this source-only job.

## Lifecycle and verification

The checked Mermaid lifecycle is [checked-chart.mmd](checked-chart.mmd).

`sender.mmd` is the pre-code multi-region design. `PushSender.tla` refines it by requiring an idle submission before preflight cancellation and requiring an active generation before HTTP. `checked-chart.mmd` is a projection of the refined model. The PNG stays in the worker report artifacts because the repository rejects opaque binaries. TLC checks unknown attempts cannot retry and completed revocation prevents submission. The model abstracts attempt counts, wall clocks, quiet-hour arithmetic, circuits, network bytes and multiple consumers. Executed tests cover these omitted mechanisms.

Regenerate in a scratch directory. Copy `PushSender.tla`, `PushSender.cfg` and `PushSender.map.ts` there, then run the statechart skill's `from-spec` command with that spec, `--map`, `--out` and `--png`. Supply Java 21 through `JAVA` and TLC through `TLA2TOOLS`.

| Transition | Executed test |
| --- | --- |
| Pending to held, then due again | Quiet hours, disabled preferences and explicit rejection retry tests |
| Pending to submitting | Acceptance and concurrent consumer tests |
| Submitting to accepted | Controlled HTTP 201 and persisted acceptance tests |
| Submitting to failed | Controlled 400 and three-attempt-cap tests |
| Submitting to held then resubmitted | Controlled 429/5xx, Retry-After and capped backoff tests |
| Submitting to unknown | Timeout, connection-loss, crash recovery and second-drain tests |
| Pending/held to cancelled or expired | Revoked generation, resolved source and expiry tests |
| Claimed to cancelled before HTTP | Replacement-after-claim preflight test |
| Active to expired | Controlled 404/410 tests |
| Circuit closed to open | Authentication and transient threshold tests |
| Circuit open to probe to closed | Transient circuit recovery test |
| Duplicate or stale attempt | Unique triple, concurrent consumer, stale settlement and eight `preflight/settle refuses absent/null/wrong/expired attempt token without effects` tests |
| Status metadata | Real response-schema validation and secret-redaction test |
| Database unavailable or scheduler disabled | No-submission tests |

## Design and debt pass

Codebase design selected one durable command interface instead of distributing concurrency and policy across callers. HTTP is the real seam varied by production fetch and controlled-response tests.

Zero tech debt retained existing registry authority and notification records. No second inbox, compatibility mode, custom encryption, queue framework or event producer was added. Policy lives in one table. The app and software-factory need no changes because this slice changes only CARR transport.

## PRODUCTION PROOF

Live delivery is unverified. Real pushes and production calls are forbidden for this fix round. The verifier is runnable locally without either:

```sh
python3 ops/web-push-delivery-proof-selftest.py
```

Expected exit 0. This runs contract-checked positive and negative status fixtures through the CLI, including equal/descendant releases (0), mismatched phone identity or revoked submission (1), and older/unrelated/unknown releases or missing evidence (78). Fixtures create their own Git history, including in shallow checkouts.

After a containing release and an independently authorized controlled-device observation, run:

```sh
python3 ops/web-push-delivery-proof.py --merge-sha "$MERGE_SHA" --evidence /tmp/web-push-delivery-evidence.json
```

Set `MERGE_SHA` to this PR's actual merge commit from GitHub. Expected exit 0. The verifier uses the shared release reader for `https://api.doctorcre.com/release` and `git merge-base --is-ancestor` in the local repository. Fetch the merge and observed release objects beforehand. It makes one release read with no retry, caps evidence and response at 1 MiB, and charges HTTP, Git and schema validation to one deadline (20 seconds, maximum 60). It never sends a push, acquires credentials or launches a browser. Insufficient evidence exits 78; assertions against a containing release exit 1.

The evidence JSON contains `provenance`, `status`, `phone` and `revoked`. Capture `status` by calling the existing owner-only `read-web-push-delivery-status-v1` verb with `{}` in the signed-in app-origin MCP client. Its response must validate against `mcp-server/contracts/web-push-delivery.v1.json`. `provenance` declares the observed `release_sha`, `actor_slug` (`joe` or `dell`), `transport: "signed-in-app-origin/mcp"`, `app_origin: "https://app.doctorcre.com"` and `queries: [{"verb":"read-web-push-delivery-status-v1","args":{}}]`. This is replayed authenticated-read evidence, as with the registry verifier, rather than a machine CLI bypass of the human-only verb.

Supply the independent phone receipt in `phone`, with `release_sha`, `actor_slug`, `observed_at`, `outbox_id`, `notification_id`, `attempt_id`, `installation_id`, `generation`, `notification_observed: true` and `exact_card_opened: true`. The verifier matches every identity to one accepted status row. The external observer must record the phone notification and exact-card tap; provider acceptance cannot establish either. This tool checks correlation, not the authenticity of a human observation.

Supply `revoked` with `outbox_id`, `installation_id`, `generation` and `submissions: 0`. The same status read must include that cancelled row from an older generation of the same installation, with zero attempts and no attempt UUID. Missing rows (including rows beyond the latest-100 status window) are not a pass. An external observer supplies the zero-submission receipt. Runtime bindings and account usage alerts remain prerequisites to activation outside this worker's scope.
