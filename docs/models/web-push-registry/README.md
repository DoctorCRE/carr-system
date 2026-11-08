# Web push subscription registry

Card: WR617.S1.J80

## System design

CARR owns subscriptions, endpoint ownership hashes, closed browser-session hashes and durable mutation receipts. The app owns browser permission and PushSubscription. This slice exposes web-push-registry.v1 through four verbs. It includes no sender, card targets or app changes.

The typed module validates provider URLs, UUIDs, canonical base64url keys and actual P-256 points. Postgres derives partner identity and owns endpoint ownership, generations, session fencing, installation caps and replay. Application roles have no direct access to the tables. Responses contain metadata only. Revocation clears endpoint and keys. Endpoint hashes prevent later cross-partner transfer. Receipts retain request SHA-256 and metadata, never raw arguments.

Two designs were considered. TypeScript row-lock orchestration would spread replay and ownership rules across callers. One Postgres command function keeps them in one transaction behind a typed adapter. The latter was chosen for locality. A global transaction advisory lock is sufficient for two partners. Twenty mutations per partner per minute and three active installations bound writes. At ten times the partner population, lock wait is the first bottleneck. Existing database deadlines and request budgets bound contention. Listing uses one indexed actor query without N+1 access. Historical receipts and revoked installations grow with use; retention is not part of this slice.

No cache serves registry state. Reads and writes require current Postgres state and refuse on failure. No external provider calls, retries, queues, scheduled handlers or circuit breakers apply. Logout invalidates the registry before deleting the KV session. Failure returns a fixed 503 and preserves login for retry. Registry success followed by KV failure leaves push revoked. The persisted closed-session fence prevents late registration after logout. Logout affects only installations currently bound to that session. A fresh authenticated session can explicitly re-enable an owned installation using its current generation.

Mutation UUIDs bind actor, authenticated session, operation and canonical JSON arguments. Duplicate requests replay a durable receipt. Changed arguments refuse. Receipts describe the original generation; clients must reconcile through listing before claiming a generation remains current. Logout is intrinsically idempotent and fences sessions even without a subscription. VAPID key material is absent. The initial supported key-version label is v1; configuration publication and sending belong to later slices.

## Lifecycle evidence

registry.mmd projects the supplied lifecycle onto this server slice. Browser support, permission and unconfirmed client responses belong to the app slice. Provider-driven expiry belongs to the sender. Expired is a reserved storage state with no partner mutation.

The unguarded chart compiled before code and found logout followed by registration could reactivate a signed-out session. The retained TLA+ model adds signed-in guards. TypeOK and NoActiveAfterLogout pass. The model abstracts one session and installation; disposable Postgres tests exercise real multi-partner and multi-connection behavior. The model leaves browser support and provider effects out.

Run the statechart skill's from-spec command against PushRegistry.tla with Java 21 and TLC. The initial chart was compiled with from-chart. The guarded model intentionally removes post-logout registration transitions from the initial chart. Generated output and logs remain in the worker report paths.

| Transition | Test in web-push-registry-postgres.test.mjs |
| --- | --- |
| Off to Active, Register | Register and duplicate request preserve one confirmed generation |
| Active to Active, Replace | Replace and concurrent replacement versus revocation fence stale generation |
| Active to Revoked, Revoke | Revoke and Enable advance generations and scrub revoked secrets |
| Revoked to Active, Enable | Revoke and Enable advance generations and scrub revoked secrets |
| Active to Revoked, Logout | Logout from Active, Off and Revoked is idempotent and rejects old-session registration |
| Off to Off, Logout | Logout from Active, Off and Revoked is idempotent and rejects old-session registration |
| Revoked to Revoked, Logout | Logout from Active, Off and Revoked is idempotent and rejects old-session registration |
| SignedIn to SignedOut, Logout | Logout from Active, Off and Revoked is idempotent and rejects old-session registration |

The auth route test proves logout ordering and database failure behavior. Registry responses pass the actual versioned response schema.

## Repository scope

DoctorCRE/carr-system owns this registry. jbookout/doctorcre-app needs no change because permission, worker and settings controls are slice 4. jbookout/software-factory needs no change because no orchestration or shared delivery policy changes.

## PRODUCTION PROOF

Run `python3 ops/web-push-registry-proof.py --merge-sha <expected-merge-commit> --evidence <signed-in-read-recording.json>` after deployment. The CLI reads `/release` through `lib/release_reader.py`, requires its production revision to contain the expected merge (equal or descendant), and validates recorded authenticated reads bound to that exact release. It exits 0 for passing evidence, 1 for an assertion failure on a containing release, and 78 for insufficient evidence, including older, unrelated or unknown releases and unavailable sessions. One total twenty-second deadline covers HTTP, git and response-contract validation. No registry mutation or push-provider request runs.

The recording contains `provenance` with `release_sha`, `actor_slug` (joe or dell), `transport` (`signed-in-app-origin/mcp`), `app_origin` (`https://app.doctorcre.com`), and `queries` in this order: `list-verbs` with `{ "filter": "web-push" }`, then `read-web-push-subscriptions-v1` with `{}`. Store their decoded registered responses as `catalog` and `registry`. Capture these reads through an existing authenticated browser session. The CLI consumes this evidence rather than acquiring session credentials or claiming that a local machine token is a browser partner. The registry response passes the same versioned JSON schema used by the registry suite. Listing may be empty; this proves the deployed read surface, not controlled registration or revocation.

Run `node --test mcp-server/test/web-push-registry-postgres.test.mjs` for offline CLI pass, failure and insufficient-evidence executions. That suite captures the real registered catalog and a real PostgreSQL registry response, builds a temporary git DAG without historical repository objects, and supplies `--fixture --repository` to exercise the same release gate and response parser. Fixture mode takes `release` from the evidence file and reports mode fixture; it is never production proof. All temporary evidence and git objects are removed after execution.

Controlled registration, owner-only listing and revocation writes remain a later authorized write-proof unit. No production observation is claimed by the fixture runs.

## Design and debt pass

Codebase-design keeps authority classification in the existing shared definition and tests the registered dispatcher plus the local guard. The proof uses the existing release reader and response schema rather than a second transport or response contract.

Zero-tech-debt replaces the deferred proof declaration with one executable observation. No compatibility mode, new runtime sender or generic proof framework is introduced.
