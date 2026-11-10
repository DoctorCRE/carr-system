# Conversion production proof

Run `python3 ops/ci-evidence.py prove-conversion --pr 3147` after merge.
Replace 3147 with the conversion PR number. `--merge-sha` accepts a full merge
commit instead. Exit 0 proves release containment, exact merged-tree CI and
health. Exit 78 means insufficient evidence. Exit 1 means a failed assertion
on a containing release. This proves conversion delivery and strict CI, not
new business behavior that needs its own acceptance observation.

## System design

Extend the existing CI evidence command. The data shape is a merged commit,
a validated production release identity, one GitHub GraphQL snapshot of that
commit's Actions suites, and a health reply. The shared release reader owns
HTTP transport. Git owns ancestry. CI evidence owns proof classification.

The lightest interface is a PR number. Alternatives were another per-PR Python
block (repeats the three reported parser/exit bugs) and a new proof service
(adds deployment and storage without a second consumer). This command removes
the premise that individual conversion authors should compose production proofs.

One GraphQL read resolves the PR merge commit and its check suites together.
Only the CI workflow's merge_group suite on the merge commit is admissible.
This is stronger than tree equality and matches the queue's observed behavior
(PR 3114 retained the merge-group SHA on main). If a future queue rewrites the
commit with an identical tree, refuse with 78 until evidence is available.
Required class names come from the merged workflow, not today's checkout.
Strict aggregation, class jobs, shard coverage and every numbered MCP shard
must be successful. Missing, incomplete or truncated evidence refuses with 78;
completed failed required checks on a containing release fail with 1.

All reads and Git commands share one wall-clock deadline, default 45 seconds,
maximum 120 seconds. External calls have no retries. The first unavailable
source opens the circuit and returns 78. There is no polling or fan-out.
At 10x usage GitHub rate limits break first; each invocation still makes one
GitHub read and two HTTP reads. No cache, queue, database access, writes,
idempotency keys or persistent state apply. Git may fetch missing commit
objects into the caller's local repository once per SHA. Unknown or shallow
ancestry refuses rather than inferring containment. Fresh release and health
reads are required; stale evidence is not served.

The flow is sequential: snapshot, release, ancestry, CI assertions, health.
This order prevents a red CI result from misclassifying an older release.
Unreadable health returns 78; an observed non-200 health or ok=false returns 1.
The whole operation ends at the deadline, including stalled body reads.

## Repository scope

DoctorCRE/carr-system owns this runtime and its queue evidence.
doctorcre-app has a different release contract and squash queue; this CARR
proof does not apply. software-factory has no CARR production release or
conversion runtime; this proof does not apply.
