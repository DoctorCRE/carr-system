---
name: verify-email-contact-review
description: Verify canonical signature identity when editing reconciliation, findings, pruning or formal models.
---

# Verify signature review

Coverage is email_contact_review.resolve_sources, Run.signature_findings,
_best_copies and _prune. Correspondence was checked against WR442.S2.J28's
Approach 2 diff on base 26ef1e966. Check the diff before advancing this sync record.
Review separately models the existing Run.write journal.

Read docs/models/email-contact-review/README.md for the checksum, six exact runner
commands, bounds, omissions and named tests. Execute every command. Canonical must
pass all three invariants. Rekey, Holder and Ambiguity must break AtMostOne and
ChangedPartySourceHold. UnsafeProof must break UniqueProof. Review must pass all
configured invariants. Inspect summary.json, stories.md and PNGs. A parser or
adapter error never counts as an expected invariant failure.

Canonical starts with two frozen sources sharing a Message-ID and explores six
scans over four aliases and one unknown representation, two parties and bodies,
lost receipts, pending activity and restart. It omits arbitrary source counts,
creation, legacy migration, extraction and server races. Run the synthetic SQLite
intake suite for those source paths.

Mutation check: save lib/email_contact_review.py outside the checkout. In the unique
proof branch, replace selected = proven[0] with selected = source_key(ident),
followed by initialization of an empty source record containing aliases, keys,
proofs and message_id. Run
IntakeTest.test_signature_competing_history_preserves_both_sources through
ops/outlook-intake-selftest.py. It must fail with three findings. Restore the file
and rerun. Never commit the mutation. If it survives, repair this verification.
Also retain successful-upgrade tests; count-only assertions can miss permanent holds.
