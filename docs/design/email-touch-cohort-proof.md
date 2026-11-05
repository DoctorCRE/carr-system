# Email-touch cohort proof

## System design

The operator supplies a local Unit 2 journal, its SHA256, and the immutable
reference `sha256:<same digest>`. The journal owns the expected actor, tenant,
source/version/key/role/target/occurrence bindings, exact application count,
occurrence window, optional paired recorded window, and deployed release SHA.
The established local-machine principal must match the journal's principal.
The database authenticates the connection supplied by `tools/db-tap.py run`.
Actor ID and slug are resolved together against canonical `actor`. This is a
local authenticated database observation, not an OAuth impersonation of that actor.

The Python command owns input validation, a single repeatable-read READ ONLY
snapshot, bounded acquisition, reconciliation, and a counts-only verdict. It
independently checks permanent event bindings and activity lineage instead of
requiring the unmerged email-touch reader module. It never sends or writes.
The four existing registered runners keep their own contracts.

Two designs were considered. Calling the JavaScript reconciliation reader
would reuse its submitted-expectation semantics but would miss extra touches
and unbound activities, and main does not yet contain that reader. Reading the
canonical tables in one snapshot also supports those inverse predicates.
The second design is selected. A future reader change requires checking this
independent proof against the versioned lineage contract.

Scope is intentionally stronger and narrower than a mailbox census. It covers
all email activities created by the declared actor on the declared canonical
targets in the half-open occurrence window, plus all that actor's application
events for those targets in that window and every expected key even outside
the window. Unit 2 must choose targets/windows containing only the intended
natural application cohort. Unrelated sources on those targets fail as extras.
It does not claim global orphan detection outside that population. Deleted or
merged targets fail as tombstoned, separately from missing and dangling.

The journal is at most 1 MiB with 1..500 unique effects. Each evidence query
returns at most 1001 rows and refuses above 1000 combined evidence rows. Three set-based queries avoid
N+1 reads. The outer command has a 60-second process alarm. Connection timeout
is 5 seconds and statement timeout is 10 seconds. The release GET has a
5-second deadline and a 64 KiB body cap. Any failure opens the circuit on its
first occurrence, returns a fixed reason and makes no retry or fallback.
At current load and 10x, memory and output remain capped; a broad cohort hits
the row cap or statement timeout first. Split cohorts at immutable window
boundaries. No fan-out, queue, caching, stale reads or write idempotency key
applies. Repeated observations are side-effect free; the journal is immutable
while database state can change between observations.

Throughput checkpoint. Blocking work is journal validation and evidence schema
inspection. Workstreams are serialized because this is one tightly coupled
command and acceptance suite. Mutable state belongs to one disposable fixture
or one read-only transaction. One worker owns the smallest safe decomposition.

```mermaid
stateDiagram-v2
    [*] --> Inputs
    Inputs --> Refused: journal absent, invalid or digest differs
    Inputs --> Acquire: valid immutable expectations
    Acquire --> Refused: identity, release, transport, deadline or row cap fails
    Acquire --> Reconcile: bounded snapshot acquired
    Reconcile --> Refused: predicate or count conservation fails
    Reconcile --> Proven: positive lineage and exact conservation
    Refused --> [*]
    Proven --> [*]
```

The CLI acceptance suite covers every edge. It exercises real disposable
PostgreSQL and the same entry point as production. No model checking is claimed.

## Invocation and Unit 2 contract

`python3 ops/email-touch-cohort-selftest.py` runs the disposable fixture command
in CI through existing selftest discovery, including positive and inverse cases.
No CI or gate script registration is needed.

Production uses `python3 tools/db-tap.py run ops/email-touch-cohort-proof.py`
followed by `--journal`, `--journal-ref`, and `--journal-sha256` with Unit 2's
actual immutable artifact values. No production invocation was run during build.
The runner requires DATABASE_URL and adds its own READ ONLY transaction even
when the transport has already set a read-only default.

Unit 2 must publish an acquired, frozen journal with schema
`email-touch-expectations.v1`, `principal_slug`, `actor_id`, `actor_slug`,
`tenant` equal to `carr-internal`, `release_sha` as a full deployed SHA,
`start`, `end`, optional `recorded_start` and `recorded_end`, `expected_count`,
and `expected`. Each expected entry has exactly `source_id`, `source_version`,
`original_key`, `role`, `target_type`, `target_id`, and `occurred_at`.
Roles map client/client, lead/prospect and vendor/vendor. IDs and original keys
are UUIDs; occurrences are normalized UTC milliseconds. Every expectation is
an application; caller-reported refusals are not successful lineage evidence.
Unit 2 must provide the original journal bytes, content digest, immutable
reference, acquisition provenance, and the chosen natural-intake population.
This runner does not fabricate that artifact or infer it from observed touches.
Missing journal fails with `missing_journal` before database access.

Only the fixed schema, verdict, reason and integer counts leave the command.
Exceptions and raw SQL diagnostics are suppressed. Fixture mode accepts only a
local Unix socket in a disposable `email-proof-*` directory and uses explicit
synthetic principal/release inputs. Its verdict is labeled `fixture`, never
production. It cannot use an ambient production DATABASE_URL.
