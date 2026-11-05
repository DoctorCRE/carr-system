# Weekly email quality report

## System design

This command owns reporting only. The existing email proposal module owns filing and undo policy.
`Window` holds an explicit half-open action occurrence interval and a separate exclusive undo observation cutoff.
`report(window, read, clock)` accepts a read port and an injected clock. The port executes one SQL statement.
The CLI uses `psql` with an explicitly named libpq service. Synthetic mode uses a caller-supplied aggregate fixture.
The PostgreSQL tests execute the same query against synthetic canonical relations.

Two designs were considered. Loading event history into Python would duplicate SQL population logic and transfer private rows.
Set-based aggregation keeps joins and populations together and returns only counts. It is the chosen design.

At current and ten-times load, there is one query and no per-effect requests or N+1 reads.
The first limit is a scan of canonical event history and JSON effect references.
A read-only repeatable-read transaction has a 30-second statement timeout and a 35-second client timeout.
Action windows are at most eight elapsed days, including local weekly windows across clock changes. Observation extends at most 90 days from window end.
There is no cache, daemon, scheduler, mutation, or autonomous downgrade.
One consistent database snapshot supplies all counts. Late-recorded history can change a rerun even at a fixed cutoff.
Identical history, window, and cutoff produce identical output. There is no generated-at field.
The command fails closed on database, timeout, fixture, and output errors. Diagnostics never include database stderr or row payloads.
A new private JSON file is created exclusively with mode 0600. The human summary contains counts and rates only.
Loops traverse a fixed population list and bounded fixture input, never mailbox items.

## Population contract

The action window uses `event.occurred_at`, not the email's received date or proposal creation date.
Both effects and undos must have `recorded_at < undo_before`. Undos must also occur before that cutoff,
after the effect, and have a later mutation order. The cutoff is exclusive and cannot be in the future.

Only `inherit-email-thread` and `confirm-email-filing` effects joined through
`v_email_thread_filing_event` to canonical `event` and an `email_in` or `email_out` activity qualify.
The partner must own the mailbox. Ownership uses immutable `email_identity.mailbox_owner`, then
`email_source.mailbox_owner`, then the current deal owner for legacy rows, matching the deployed undo contract.
An optional mailbox account filter uses `email_identity.mailbox_account`; legacy unknown accounts do not match it.
Legacy deal ownership is current ownership and cannot reconstruct historical ownership.
Deleted or recalled mail retains real historical effects. It is not a duplicate proposal population.

Bulk effects have `import_migration` or `import_salesforce` cause and remain separate.
Automatic effects require the inheritance verb, `automation_job` cause, and no human quote.
Confirmed effects require the confirmation verb and `human_stated` cause.
Other provenance remains `unclassified`. Actor attribution requires the partner actor or a nonhuman actor sponsored by that partner.
An unrelated human actor cannot qualify through sponsorship. The report returns excluded unrelated-actor counts separately.
The actor kind alone never establishes automation.

Each automatic effect contributes once to its denominator, including effects subsequently undone.
An undo contributes once only through `new_value.event_id` pointing to that exact original effect,
with matching activity and deal, `human_correction` cause, and the owning human partner as actor.
Duplicate undo records cannot increase the numerator. Undos of effects outside the action window never enter this cohort.

`observed_rate` is the numerator divided by the denominator. Zero denominator produces null rate and comparison,
with status `no_evidenced_population`. Exactly five percent is `at_threshold`; only more than five percent is `above_threshold`.
The reported threshold is a comparison, not an instruction to change policy.
Partner confirmations, imports, and unclassified effects have counts but no automatic quality rate.
Reply sends, next steps, deal progress, lead creation, and other automatic types are unavailable in this contract.
Corrected misses are unavailable because the current history has no complete linked missed-action correction population.
Unavailable counts are null, never zero.

## Command reference

Run the command directly. No global registration is required.

```sh
python3 tools/email-weekly-quality.py --start 2026-10-26T00:00:00-05:00 --end 2026-11-02T00:00:00-06:00 --undo-before 2026-11-03T00:00:00-06:00 --partner joe --service carr_quality_reader --output /tmp/email-quality-private.json
```

A local week spanning the autumn clock change exceeds 168 hours. The command accepts up to eight elapsed days
so an explicit seven-calendar-day local window remains valid across that transition.
The service must already exist and have SELECT access to the canonical event, actor, activity, deal, and filing view.
The command does not discover credentials or obtain new grants. `carr_reader` has filing-view access but may lack full event access.
Use an already authorized read service. A permission failure is a failed report, not an empty population.
`--mailbox-account` optionally narrows the partner population. The account is retained only in private JSON.
`--fixture` replaces `--service` and accepts a JSON array of aggregate rows from the read-port contract.
Each row has `population`, `denominator`, and `numerator`; all five populations must occur exactly once.
Populations are `automatic`, `partner_confirmed`, `bulk_import`, `unclassified`, and `unrelated_actor`.
Fixture mode is labeled synthetic and does not establish production quality.

## Production proof

The coordinator runs the exact command above after delivery for that completed local weekly window.
The output path must not already exist. Keep its contents private.
Reconcile at least the `inherit-email-thread` automatic denominator and its distinct linked undo numerator to canonical history
using the query constant `REPORT_SQL` and its documented filters. Inspect the original event IDs in the authorized database console,
without publishing mailbox identifiers. Require numerator at most denominator and distinct effect IDs in both populations.
Prove the inverse with an unrelated human actor, an import, and an out-of-window effect, none of which enter automatic counts.
If the denominator is zero, record `no_evidenced_population`; that outcome establishes no quality score.
The declaration in `proofs/requirements/email-weekly-quality-report.json` is a plan, not a production receipt.

The registered Studio observer checks a supplied operational receipt only. It does not execute this command or reconcile SQL.
Without an actual receipt it remains BLOCKED. A count-only observer result cannot replace independent history reconciliation.
