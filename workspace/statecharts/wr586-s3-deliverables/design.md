# Partner deal deliverables

Partners need to upload a file to an authorized deal, finalize verified bytes privately, designate it shared, and download it under current authorization. Shared means designated publishable. Slice 4 owns actual client publication.

## Existing implementation

`deal-files.js` owns archived email attachment metadata. It cannot inspect bytes and has intake provenance. `requirement-upload.js` verifies bounded PDF bytes for a different grant and quarantine policy. `dealroom-web.js` resolves opaque partner sessions and owns browser CSRF checks. The main Worker already binds the private `carr_documents` bucket.

## Two designs

1. Extend `attachment` with upload and visibility fields. This reuses listing but makes email intake rows look partner uploaded and lets metadata writers bypass verification. Rejected because it splits verification ownership across unrelated writers.
2. Give partner deliverables their own lifecycle row. The domain module owns authorization, reservation quota, finalization and visibility. The byte adapter owns bounded transport and private object storage. The existing session gate supplies authentication and a revocation recheck. Chosen because callers cannot supply an object key and email semantics remain coherent.

## Throughput checkpoint

- Blocking first steps. Inspect authorization and upload patterns, model the lifecycle, and run the first failing content test before implementation.
- Independent workstreams. One worker owns this code-coupled migration, domain and transport change. No delegated work is allowed.
- Shared mutable state. A deal advisory lock protects reservation quota. A row lock protects uploads, finalization and visibility. Visibility compares the expected version under that lock.
- Smallest safe decomposition. Keep one domain module and one byte adapter. Reuse the existing partner session and registered verb implementations.

## Model and executed checks

The first TLC run preceded implementation. It checked 15 states. The refined model adds the transaction's uploading phase, rollback and expiration. TLC checks 19 states and 48 transitions. PrivateBeforeFinalization, DownloadAuthorized, VersionedVisibility and NoDeadlock hold. The chart hides self transitions, including refusals and replay. The model abstracts byte validation, tenant policy and the database's locking implementation. Real tests cover those omitted details.

The tests in `mcp-server/test/deal-deliverables-postgres.test.mjs` execute reservation, verified upload, private finalization, shared and private mutations, authorized download, replay, stale versions, forged keys, wrong deal, wrong partner, quota, bad content, hash mismatch, expiration and revocation. The Worker test executes the actual cookie and CSRF gate. Unit tests exercise each accepted content family and malformed input. The tests fail on the baseline because the capability module is absent. Only the initial unit test was run before its corresponding implementation. PostgreSQL and Worker baseline runs were made afterward, which is a test-first deviation.

## Design and debt pass

- codebase-design. Compared extending attachments against a dedicated lifecycle. Chose a domain module with a small registered-verb interface and a byte adapter at the session seam.
- zero-tech-debt. One limit configuration, one byte verifier and one session authority. Added no compatibility route or duplicated email writer. Existing email attachment behavior still passes its PostgreSQL suite.

## Blast radius

The change is safe only if the browser cannot select a raw object key or continue downloading after session revocation. Executed tests prove both through the registered input guard, the real session gate and the PostgreSQL byte adapter. Nearby `deal-files-postgres.test.mjs` and `dealroom-web.test.js` pass. This is synthetic local evidence, not production verification.

Content validation identifies file families. It does not claim malware scanning or full document parsing. Uploads are at most 25 MB. A deal may reserve 100 active files totaling 500 MB. Unused started reservations expire after 24 hours. Verified and finalized files stay with the deal.

## Reproduce the model

Set `TLA2TOOLS` to a local tla2tools.jar and `JAVA` to Java 11 or later. Run the installed pstack statechart script with `Deliverables.tla`, `--map view.ts`, and a new `--out` directory. The checked-in config names all three invariants. The model files are independent of production credentials.
The lightest bounded policy uses the existing byte adapter and immutable reservation digest. Read the request body before taking the write lock. Bound body and storage waits to 10 seconds. Retain private objects after failed requests because every retry must verify identical bytes against the reservation; late PUTs cannot change the accepted digest and no late DELETE can destroy a retry. Never finalize on timeout. A per-isolate bucket policy caps outstanding storage at four and admits 60 requests per partner per minute. Three consecutive storage failures open the circuit for 30 seconds and emit a structured error; one half-open probe restores it. Fail closed without retries. These are local protection guarantees, not global fleet limits. The existing authenticated session and deal grants remain authoritative.
Proof uses a read-only Python CLI to verify a partner-captured authenticated lifecycle receipt against release SHA, exact file digest, response contracts and read-only live list/download/revoked checks. The producer fixture executes real registered handlers and byte endpoints against disposable PostgreSQL. Pass and corrupt receipt cases invoke the same CLI.
