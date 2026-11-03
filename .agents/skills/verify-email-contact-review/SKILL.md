---
name: verify-email-contact-review
description: Verify source identity when editing signature reconciliation, findings, pruning or their formal models.
---

# Verify email signature review

Coverage is Run.reconcile, Run.signature_source, Run.signature_findings and _prune
in lib/outlook_intake.py. The Sources model describes reviewed commit
303e4051f68b6a374f3959942a43efb0efd0f9cb plus the WR442.S2.J26 independent holder
diff. Check that correspondence before advancing this sync record. Review covers
transport. Neither model proves server identity atomicity or extraction correctness.

Use TLC 2.19 (5a47802), Java 21 or later and the installed pstack runner. Set
TLA2TOOLS to tla2tools.jar and JAVA to the Java binary. Run from the repository root.

```sh
model="$PWD/docs/models/email-contact-review"
runner=/Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs
node "$runner" from-spec "$model/Review.tla" --config "$model/Review.cfg" --map "$model/view.mjs" --out /tmp/verify-email-review
node "$runner" from-spec "$model/Sources.tla" --config "$model/Sources.cfg" --map "$model/sources-view.mjs" --out /tmp/verify-email-sources
node "$runner" from-spec "$model/Sources.tla" --config "$model/SourcesBroken.cfg" --map "$model/sources-view.mjs" --expect-broken AtMostOne,ChangedPartySourceHold --out /tmp/verify-email-old-sources
.venv/bin/python ops/outlook-intake-selftest.py
.venv/bin/python ops/email-contact-review-selftest.py
```

AtMostOne forbids a second signature proposal for the same real source.
ChangedPartySourceHold forbids submission after the original party/body changes.
Sources explores two aliases, versions, parties and bodies, and activity either
resolved or persistently unresolved. Its fixed config must pass both invariants.
Its broken config must fail both. Review must pass all five configured invariants.
Read summary.json for counts, stories.md and tlc output for traces. Exceptions and
adapter failures are not passing or expected-broken results.

For a real-code mutation check, save the intake file outside the checkout, replace
`priors.add(signature_prior)` with `pass`, and run
`IntakeTest.test_signature_unresolved_activity_alias_reversal_holds_party` through
ops/outlook-intake-selftest.py. It must fail with two findings instead of one.
Restore the file and rerun. Never commit the mutation. If it no longer fails,
inspect the design and update model/test coverage before advancing the sync record.

Every transition has a named test in the model README. Add a test for each new
transition. If a trace cannot reproduce through persisted synthetic SQLite intake,
treat it as a modeling error until evidence says otherwise. Use
maintain-formal-verification-skill to update this coverage after later changes.
