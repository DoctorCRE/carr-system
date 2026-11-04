# Partner byte staging

Data shape is an immutable upload reservation keyed by server UUID, actor, session digest, tenant, deal and exact file metadata. A batch action UUID binds up to three reservations and 25 MiB. Receipt states follow chart.mmd. This unit never files an attachment.

The existing Deliverables branch owns curated private/shared deal deliverables, download and visibility. This upload owns temporary intake before canonical attachment filing. The purposes intersect at bounded byte validation but the lifecycles differ. Do not reuse its finalize endpoint or copy its canonical store. Office formats are refused in this unit; actual ZIP/Office validation is required before admitting them.

## System design

Architecture. One lifecycle module owns reservations, quota and receipt transitions. One HTTP adapter receives raw bytes after normal session, origin and CSRF checks. Registered metadata verbs require server supplied session identity. Browser MCP only receives that context with both CSRF and same-origin fetch metadata. Postgres owns state; carr_documents owns opaque temporary bytes. App owns neither.

Capacity. Two partners today, twenty at 10x. At most 25 MiB/file and action, three files/action, two receiving streams/actor, ten new reservations/actor/hour. A singleton quota row serializes short state transactions and caps all retained staging reservations at 100 MiB. Memory is preallocated once to declared length per stream, at most 50 MiB/actor plus request chunks. CPU/hash and staging capacity fail first.

Load spread and storage. Upload-ID indexes and actor/action indexes avoid list scans. Short transactions claim and finish bytes; no transaction spans R2. Receiving claims refuse concurrent retries and cancellation. Unknown outcomes reconcile using R2 object metadata. A worker lost during receiving requires expiry cleanup in P6b. Metadata is no-store and contains no object key. HTTP endpoints use the existing actor/session verb request budget.

Caching. None; current deal policy and actor activity are checked at each operation. Expired reservations retain quota until confirmed temporary-object cleanup. No canonical objects are deleted. A cancellation whose database result is uncertain retains its quota and requires object verification before any future filing. P6b supplies bounded expiry cleanup.

Availability and consistency. Postgres failure refuses; R2 failure retains a durable unknown receipt. Readback reconciles verified object metadata. Readback resolves completed unknown writes. An absent object remains unknown and charged until cleanup. Staged receipt is never filed. Auth is rechecked before and after R2, with the original session digest; a new session cannot inherit bytes.

Async. Browser drives operations. No queue in P6a. Expiry cleanup belongs to P6b; expired objects remain charged meanwhile.

Circuit breaker. R2 gets two attempts with bounded backoff per call. Three failed operations pause new reservations and byte claims for five minutes, with a structured warning. Reads remain available. Uncertain writes retain quota; cancel refunds only after confirmed deletion. DB failures are not retried.

Idempotency. Batch UUID plus file UUID binds exact metadata. Begin replays its receipt, changed metadata refuses. Byte retries use the same server object key and verified hash. Cancel UUID binds the upload; replay returns cancelled. The quota lock orders every reservation, claim and refund.

Alternatives. Holding a database transaction across R2 makes session races simpler but consumes scarce DB connections throughout phone transfers. Separate durable claims keep transactions short and expose uncertain outcomes for readback. The chosen latter design fails closed on cancellation and expiry.

Throughput checkpoint. Read existing auth and Deliverables first. No independent writer or delegate. Quota is shared mutable state and must serialize. P6a is the smallest verifiable unit; P6b owns canonical filing and expiry cleanup.

The chart is a design aid, not a checked TLA+ proof. Safety relies on immutable byte metadata and refusing cancellation while a stream may still write. No claim of model completeness.
