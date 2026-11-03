# Email signature review

Signature differences become unverified findings for an exactly matched sender.
The journal freezes the proposal, contact binding and UUID before transport.
Neither contact updater is called.

## Canonical sources

Run.signature_findings keeps the raw decoder identity before activity renames rows.
email_contact_review.resolve_sources retains mailbox-scoped canonical records with
alias sets, actual keys, frozen content proofs and Message-ID. The competing index
retains every canonical source sharing a Message-ID across absent scans.
Observations never move. Activity holders cannot lend keys to signature sources.

An existing alias selects its record. A new alias needs a unique compatible content
proof. Multiple current keys with identical proof cannot select a keyless predecessor.
Representations that could be existing sources stay held. Distinct keys excluding all
existing sources establish separate sources. The first simultaneous batch can establish
distinct keyless sources. A timestamp outside COPY_WINDOW establishes a new generation.
Retry validates the original sender, body, party, subject ref, frozen keys, Message-ID
and company binding. Restoration allows the same payload to retry. Version refresh
alone cannot mint another request. The existing requests journal owns transport.

Legacy observations seed records from their frozen locator and current identity.
Unknown legacy content cannot prove a new alias. Such an upgrade can remain held
until the original representation returns. No request is rebound.

## Design it twice

Candidate A used alias nodes, distinct-source edges and union-find roots. It needed
component merging and observation-root migration. Candidate B, chosen here, keeps
immutable observation keys and sets on each canonical record. Its interface is
resolve_sources(mailbox, mails, source_key, copy_window), returning one source or a
hold per representation. Intake retains binding validation and journal ownership.
This replaces observation movement, signature_ids, key borrowing and the old
single-source holder model. No server migration is needed.

## Formal verification

Canonical starts with two distinct frozen keyless signatures sharing a Message-ID.
It explores six additional scans, four aliases, one unknown representation, two
parties and bodies, advancing or pending activity, restart and lost-receipt retry.
Content proof is abstracted as (source, body); SQLite tests exercise actual hashes.

AtMostOne forbids duplicate findings. ChangedPartySourceHold forbids changed-party
or changed-body submission. UniqueProof forbids an unproven or competing assignment.
Fixed Canonical checks 18,122 states and 107,602 nonstuttering transitions.
Rekey, Holder and Ambiguity project the three earlier failure mechanisms and each
breaks AtMostOne and ChangedPartySourceHold. Ambiguity models A acquiring key 77,
B inheriting 77, then B acquiring 88 and replacing its observation. Initialization
represents the first two-row batch; an optional Scan represents A alone. The real
regression executes all five scans explicitly. UnsafeProof breaks UniqueProof.
These variants project the old mechanisms; they do not copy the full old intake.

Review separately covers preparation, refusal, failure, committed lost response,
replay and restoring held evidence. Its alias action assumes an already-proven alias.
Canonical checks proof selection. These models are not a joint proof of all storage
and transport interleavings. They omit hash collisions, indistinguishable keyless
decoder rows, arbitrary source counts, extraction and server identity races.
Integration tests cover source creation and migration.

Use Java 21. The exercised v1.8.0/tla2tools.jar asset reports TLC2
2026.11.03.180953, revision 309e489, SHA-256
e20447644bfabd29d6bd0fc2c0eb1473fb301c1a0e4dfe1961938ce5a191edbf.
Set TLA2TOOLS to that jar and JAVA to Java. From repository root:

```sh
model="$PWD/docs/models/email-contact-review"
runner=/Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs
node "$runner" from-spec "$model/Canonical.tla" --config "$model/Canonical.cfg" --map "$model/canonical-view.mjs" --out /tmp/email-canonical --png
node "$runner" from-spec "$model/Canonical.tla" --config "$model/CanonicalRekey.cfg" --map "$model/canonical-view.mjs" --expect-broken AtMostOne,ChangedPartySourceHold --out /tmp/email-rekey --png
node "$runner" from-spec "$model/Canonical.tla" --config "$model/CanonicalHolder.cfg" --map "$model/canonical-view.mjs" --expect-broken AtMostOne,ChangedPartySourceHold --out /tmp/email-holder --png
node "$runner" from-spec "$model/Canonical.tla" --config "$model/CanonicalAmbiguity.cfg" --map "$model/canonical-view.mjs" --expect-broken AtMostOne,ChangedPartySourceHold --out /tmp/email-ambiguity --png
node "$runner" from-spec "$model/Canonical.tla" --config "$model/CanonicalUnsafeProof.cfg" --map "$model/canonical-view.mjs" --expect-broken UniqueProof --out /tmp/email-unsafe-proof --png
node "$runner" from-spec "$model/Review.tla" --config "$model/Review.cfg" --map "$model/view.mjs" --out /tmp/email-review --png
.venv/bin/python ops/outlook-intake-selftest.py
.venv/bin/python ops/email-contact-review-selftest.py
```

Read summary.json and stories.md and inspect PNGs. Exceptions are failures.
canonical-chart.mmd is the fixed chart. Generated traces remain outside Git.

## Transition coverage

| Action or condition | Test |
| --- | --- |
| Scan, Resolve, competition, restart | test_signature_competing_history_preserves_both_sources |
| Changed party | test_signature_competing_history_holds_changed_party |
| Retry, lost receipt | test_signature_competing_history_lost_responses |
| Changed retry binding | test_signature_competing_history_lost_response_changed_party |
| Advancing activity holder | test_signature_competing_history_resolved_activity |
| Simultaneous upgrades | test_signature_competing_sources_upgrade_together |
| Unknown proof | test_signature_ambiguous_new_representation_waits_for_proof |
| Changed source, competing batch | test_signature_competing_batch_holds_changed_new_alias |
| Rekey | test_signature_keyless_keyed_repeat_preserves_observation |
| Rekey, lost receipt | test_signature_lost_response_keyless_keyed_repeat_preserves_request |
| Repeated reversal | test_signature_unresolved_activity_alias_reversal_freezes_snapshot |
| Party hold during reversal | test_signature_unresolved_activity_alias_reversal_holds_party |
| Legacy migration | test_signature_unresolved_activity_migrates_pre_index_observation |
| Old observation format | test_signature_alias_preserves_reviewed_head_observation_format |

ReviewTest covers preparation, loss, retry, refusal, expiry and changed bindings.
The older ambiguity assertion expected three findings after one keyless source
followed by two indistinguishable keyed candidates. Approach 2 deliberately retains
one finding and holds both candidates, resolving its conflict with unique proof.
