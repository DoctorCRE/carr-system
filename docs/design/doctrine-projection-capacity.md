# Doctrine projection capacity

A doctrine write must remain possible as append-only revision history grows. The
policy epoch must continue to bind every field of every historical row.

The status publisher failed with PostgreSQL code `54000` and message
`total size of jsonb array elements exceeds the maximum of 268435455 bytes`.
The production request correlation was `8adae57b-e8c0-4af1-9c84-a9f97600b492`.
A local execution of the original `scac_doctrine_projection_digest()` over
synthetic retained revisions reproduces that message and identifies the function
in PostgreSQL's error context. This supports the deferred policy-epoch projection
as the cause. Production diagnostics redact SQL context, so the production
function name itself was not present in the captured log.

## System design

The module keeps its existing no-argument function interface and SHA-256 result
shape. The implementation hashes each complete canonical row together with its
table name, sorts the fixed-width hashes, and folds them into a SHA-256 chain.
The initial hash binds algorithm version `row-chain:v2`. Duplicate rows each
enter the fold. Empty input has a deterministic digest.

The old raw JSONB array fails above 256 MiB. A raw text aggregate would move the
failure to PostgreSQL's larger value limit. The replacement never constructs a
projection-wide body or digest array. Sorting hashes may spill under PostgreSQL
`work_mem`; the fold retains one 32-byte hash. Each individual row still must fit
PostgreSQL's existing value limits. At ten times the history, it still scans and
canonicalizes every row and sorts its hash. This is a capacity fix, with no
measured latency improvement claim. It does not solve unbounded historical scan
cost or repeated deferred-trigger work.

The function stays stable and security-definer with the existing hardened search
path and ACL. No epoch trigger, idempotency envelope, source table, or history is
deleted. The schema ledger's normal deferred trigger records a new epoch using
the new fingerprint. Previously recorded epochs remain immutable. Their stored
source digests remain evidence of the earlier algorithm.

Caching, load distribution, queues, external circuit breakers and network rate
limits do not apply to this SQL-only function. It returns no stale or fallback
fingerprint. SQL failure aborts the existing transaction. Existing epoch locks
serialize writes, and existing verb idempotency governs their retries.

## State transitions and checks

The function reads facts, hashes each complete row, sorts hashes, folds every
hash, and returns the fingerprint. SQL failure at any stage aborts the caller.
The regression exercises the real function in disposable PostgreSQL with no
production connection. It covers empty input, each source table, table identity,
insertion-order independence, historical edits, duplicate multiplicity, and a
257 MiB synthetic history plus an edit beyond the former limit.

Run `python3 ops/scac-doctrine-digest-selftest.py --legacy` to observe the original
failure. Run it without `--legacy` to check the replacement migration. The gates
class discovers the selftest through its existing filename pattern.

## Production recovery

The authorized archive creation also returned `unhandled_verb_failure`, with
correlation `83729414-fcfc-4847-84a4-14da65d3875d`. Shrinking one current section
cannot remove retained revisions from this global projection. Recovery therefore
requires the normal reviewed migration deployment before another archive write.
The worker does not merge or deploy.

After deployment, archive the current handoff verbatim and verify its digest,
then replace the latest handoff with archive pointers and the current status
block. Bound the publisher to a stated UTF-8 byte cap. The worker preserved the
publisher and current handoff because archive creation did not succeed.
