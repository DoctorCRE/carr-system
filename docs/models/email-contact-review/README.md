# Email signature review

The intake retains contact snapshots and resolves signatures by exact sender address.
Multiple roles may name one party. Shared addresses, absent snapshots and conflicting
snapshots stay held. The signature stage runs independently of activity filing so a
previously filed message can still supply a finding when its full text arrives.

The data shape is one immutable observation per mailbox/source, with a source/party
binding and a list of frozen record-finding requests. The existing intake journal
owns transport attempts, backoff, refusal and acknowledgement. A pure planner owns
field comparison and provenance. This was chosen over a second retry queue because
the journal already persists payloads before sending and preserves lost-response
idempotency. Snapshot versions are evidence, never authority to update a party.

The statechart preceded implementation. TLC explored 80 states and 440 steps.
NoOverwrite, AtMostOne, FrozenRetry, FrozenAlias, DistinctSources and NoDeadlock
held. The chart omits 212 steps that stay in the same visible state, including
restart, ambiguous duplicate observation, disposition retention and repeat scans. The finite model abstracts transport and extraction;
synthetic Python tests exercise their actual implementation. It assumes the
record-layer idempotency envelope honors a repeated key and frozen payload.
No production submission was performed.

Transitions map to `ops/email-contact-review-selftest.py`.
Resolve/Held maps to `test_ambiguous_unmatched_suppressed_and_deal_only_are_held`.
Resolve/Prepared/Recorded maps to
`test_signature_change_creates_one_finding_and_never_overwrites_party`.
Submit/Retry/Recorded and Replay map to `test_replay_and_lost_response_use_frozen_arguments`.
Held/Unread maps to `test_held_source_can_resolve_when_full_text_arrives`.
Changed/Retry maps to the intake integration test below.
Retry/Changed maps to `test_changed_source_or_party_never_replays_or_rebinds`.
Submit/Refused and Retry/Refused map to `test_refusal_and_transient_failure_are_bounded`.
`IntakeTest.test_signature_stage_runs_for_filed_mail_and_keeps_retry_guard`
checks the real intake path cannot replay a request before source validation.

Reproduce with the installed pstack statechart runner and these files.
Set TLA2TOOLS to tla2tools.jar and JAVA to Java 11 or later when mise is absent.

Throughput checkpoint. Bootstrap and contract reads came first. Planning and intake
integration share the same state and run serially. There is one owner and no fan-out.
The smallest safe decomposition is a pure planner and the existing intake journal.

The sender snapshot has no atomic server-side identity precondition for findings.
The intake checks the current snapshot before each attempt; this does not provide
an atomic guard against a concurrent server-side merge during transport. Findings
carry the observed party ID and versions and never mutate identity or contact facts.

Ordinary record-finding replay receipts expire after 30 days in replay-receipt.ts.
Unacknowledged signature requests block after 29 days from their first attempt,
so a long outage cannot turn an expired receipt into a second finding. The one-day
margin is conservative. Acknowledged local receipts remain retained.
`test_lost_response_after_replay_window_is_held_without_resubmission` checks this
Retry/Refused transition. A blocked request needs review rather than a new key.

## Decoder alias reconciliation

Card: WR442.S2.J24

Signature review runs after the existing mailbox-scoped Message-ID reconciliation
and before activity filing checks. A proven alias moves the observation index,
retaining its original binding keys, request UUIDs, payloads and journal receipts.
Current aliases share that index during the scan. Different store keys or multiple
distinct current keyless identities make a Message-ID ambiguous. Reverse migration
retains the known store key, so a later conflicting key cannot inherit its finding.
Signature observations retain their reconciliation identities during pruning.

The model varies the decoder alias before and after preparation, uncertain transport,
acknowledgement and restart. `findings` is a set of request identities, rather than
a bounded scalar count. An ambiguous second source has its own finding identity.
FrozenAlias checks that alias changes retain the prepared request. DistinctSources
checks that the second source cannot inherit that identity. The model abstracts
mailbox lookup, body matching and extraction; Python integration tests exercise the
SQLite decoder, persisted intake state and fake record-layer receipts.

Additional transitions map to `IntakeTest` in `ops/outlook-intake-selftest.py`.

| Transition | Named test |
| --- | --- |
| AliasChange after acknowledgement, then Replay | `test_signature_keyless_keyed_repeat_preserves_observation` |
| AliasChange after uncertain Submit, then Retry | `test_signature_lost_response_keyless_keyed_repeat_preserves_request` |
| Reverse AliasChange | `test_signature_keyed_keyless_repeat_preserves_observation` |
| Restart with receipt | `test_signature_alias_restart_preserves_receipt` |
| Restart with uncertain request | `test_signature_alias_restart_preserves_uncertain_request` |
| AmbiguousDuplicate | `test_signature_ambiguous_message_ids_stay_separate` |
| AmbiguousDuplicate without store keys | `test_signature_ambiguous_keyless_message_ids_stay_separate` |
| Simultaneous aliases and Replay | `test_signature_simultaneous_aliases_share_frozen_request` |
| Reverse AliasChange then AmbiguousDuplicate | `test_signature_reverse_alias_keeps_store_key_conflict_guard` |
| AliasChange with earlier persisted observation format | `test_signature_alias_preserves_reviewed_head_observation_format` |
| AliasChange with Changed evidence | `test_signature_alias_changed_source_or_party_stays_held` |

With the statechart runner on PATH and TLA2TOOLS/JAVA configured, run from a scratch directory:

```sh
review_source="$(git rev-parse --show-toplevel)/docs/models/email-contact-review"
cd /tmp
node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-spec "$review_source/Review.tla" --config "$review_source/Review.cfg" \
  --map "$review_source/view.mjs" --out /tmp/email-contact-review-model
```

## Unresolved activity retries

Card: WR442.S2.J26

`mail_ids` owns activity retry identity. The independent `signature_ids` holder,
keyed by mailbox digest and normalized Message-ID, keeps the signature observation
reachable. Both participate in the existing ambiguity and store-key checks. Only
a holder with a signature observation contributes an extra prior. Activity-only
messages retain their existing mailbox-reset behavior. Pruning keeps signature
holders with their observations. Frozen request locators seed the index for
observations saved before this index existed, including the reviewed-head state.

`Sources.tla` models separate activity/signature holders and the observation index.
`SourcesBroken.cfg` selects lookup from reviewed commit
303e4051f68b6a374f3959942a43efb0efd0f9cb. TLC breaks AtMostOne and
ChangedPartySourceHold at depth 11. The trace freezes a keyless proposal, retains
unresolved activity, scans its keyed alias, changes the snapshot or party/body,
then returns to the keyless alias. The old lookup loses the observation. Both
review scripts returned `1, 1, 2` before the fix and `1, 1, 1` afterward.

`Sources.cfg` selects the independent holder. Both invariants hold across 194
model states and 450 nonstuttering transitions. Bounds are two aliases, snapshot
versions, parties and bodies, and resolved or unresolved activity. Restart
preserves persisted state. The companion Review model and journal tests cover
lost responses and refusal, rather than a cross-product of both models. Sources
abstracts mailbox lookup, ambiguity, extraction and the server identity race.
This finite check does not prove every possible Python execution.

Every Sources transition maps to an `IntakeTest` method.

| Transition | Named test |
| --- | --- |
| Review / initial proposal | `test_signature_keyless_keyed_repeat_preserves_observation` |
| Activity / unresolved | `test_signature_unresolved_activity_alias_reversal_freezes_snapshot` |
| Activity / resolved | `test_signature_keyed_keyless_repeat_preserves_observation` |
| Scan and Reconcile / repeated reversals | `test_signature_unresolved_activity_alias_reversal_freezes_snapshot` |
| ChangeSnapshot / version | `test_signature_unresolved_activity_alias_reversal_freezes_snapshot` |
| ChangeSnapshot / party | `test_signature_unresolved_activity_alias_reversal_holds_party` |
| ChangeSnapshot / body | `test_signature_unresolved_activity_alias_reversal_holds_source` |
| Restart and pre-index migration | `test_signature_unresolved_activity_migrates_pre_index_observation` |
| Review / lost response | `test_signature_unresolved_activity_alias_reversal_retries_lost_response` |
| Review / uncertain response and party change | `test_signature_unresolved_activity_alias_reversal_holds_uncertain_party` |
| Scan / reverse initial alias | `test_signature_unresolved_activity_reverse_alias_reversal` |

Run the same installed runner with Sources.tla, Sources.cfg and sources-view.mjs.
Use SourcesBroken.cfg and `--expect-broken AtMostOne,ChangedPartySourceHold` to
reproduce the reviewed logic. The local verify-email-contact-review skill gives
complete commands, tool versions and the real-code mutation check.
