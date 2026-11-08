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

After a containing release deploys, a later read-only unit lists the four verbs and observes web-push-registry.v1 through an authenticated registry read. Controlled registration, owner-only listing and revocation writes remain a later authorized proof unit. This worker makes no production calls and claims no production delivery.
