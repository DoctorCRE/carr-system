# Email last-touch proof

## System design

The command owns readback only. Intake owns retained acknowledgements. PostgreSQL owns canonical activity and `v_last_touch`. No future touch-lineage contract is required.

The data shape is a bounded list of acknowledged requests and a snapshot of their targets, source activities, and latest eligible activity dates. `prove(state, read, account, start, end, clock)` is the interface. The clock and the snapshot read port are injected. Fixture and PostgreSQL adapters share that interface.

Two designs were considered. Loading each party through a public reader would hide SQL but produce N+1 calls and inherit reader filters. One set query over retained request targets preserves missing targets and checks the inverse against the canonical projection. The latter is the implementation.

Current load is at most 1000 retained requests in a window of at most 31 days. Ten-times load requires ten explicit windows, not an unbounded run. The first limit is the activity scan for the requested targets. A grouped set query spreads that work across all targets. There is no per-party query loop. Input is capped at 16 MiB. Returned data contains no message body or summary.

One repeatable-read, explicitly read-only transaction supplies the snapshot. Both connection defaults and transaction mode prohibit writes. Connection timeout is five seconds. Statement timeout is twenty seconds and lock timeout is two seconds. There is one query, no retry, no asynchronous work, no queue, and no circuit breaker because a failed bounded read exits unavailable. No cache, invalidation, eviction, or persistent storage is needed. Availability depends on the supplied private file and database. Failure produces a redacted error type. No credentials are discovered by this command.

There is no domain state transition. The command reads and returns a result. Lifecycle-marked emails retain their activity date on main. It reports lifecycle state without inventing removal semantics. A moved or corrected target/kind/date fails exact correspondence. Merged roles, merged parties, and deleted parties are exclusions. A role whose party is absent is dangling. Missing refs and missing acknowledged activity IDs are distinct failures. Legacy requests without source identity remain unknown even when their retained activity ID, target, kind, and occurrence agree.

## Readback contract

The fixed window is half-open over retained request `created_at`, in UTC instants supplied with offsets. It is not a filter on activity dates. The latest eligible activity includes unrelated newer contacts and historical import stamps. Its Chicago calendar date must equal `v_last_touch.last_touch`. The eligibility predicate mirrors migration `0953_morning_brief_chicago_calendar.sql`, including both legacy note exceptions and `activity_kind.is_contact`.

A successful acknowledgement requires `verb=log-activity`, `acknowledged=true`, email kind, and no refused disposition. The private source binding is the retained `activity_id` plus exact `email_identity`, target ref, kind, and occurrence instant. Identity uses main's trimmed `item_id`. The account must match the supplied account. No summary, address, source identifier, target ref, or timestamp from a party is emitted. Results identify requests only by SHA256 of the retained opaque request key. Retain the private input to resolve a binding. `examples` reports every corroborated request binding by subject type, so a vendor of interest is not hidden behind the first example.

Counts cover unique active targets in the retained request population. They do not claim coverage of all business records. Missing examples are unproven. Quiet-follow-ups and stale-records are never queried. A vendor corroboration therefore does not depend on quiet-reader inclusion.

## Run the command

Use the installed checkout and an explicitly retained intake state file. No mailbox is read. Supply an explicit account and offset-bearing window. `--account-file` reads one exact account from an explicit private UTF-8 file of at most 255 bytes. `--account` accepts the same value directly for synthetic fixtures. The command uses `DATABASE_URL` supplied by the existing read-only database wrapper.

```sh
.venv/bin/python tools/db-tap.py run tools/email-last-touch-proof.py --requests /tmp/email-last-touch-proof-private.json --account-file /tmp/email-last-touch-account.txt --start 2026-10-28T00:00:00-05:00 --end 2026-11-04T00:00:00-06:00
```

The coordinator must verify the account and choose a naturally populated bounded window from retained evidence. The example window is not evidence that examples exist. Never manufacture an email to make proof pass. A synthetic fixture contains a `rows` list in the snapshot shape returned by `SQL` and uses `--fixture FILE` instead of a database.

Exit zero requires no mismatches, unavailable identities or occurrences, missing or dangling targets, and corroborated client, lead, and vendor examples. Retired target exclusions do not fail otherwise complete proof. Exit one reports incomplete proof. Exit two reports unavailable input or readback. Lifecycle counts describe observations, not permission to act on a source.

## Verification and production limits

Run `python3 tools/test_email_last_touch_proof.py`. Its synthetic PostgreSQL test installs the actual main projection definition and executes the command SQL with `default_transaction_read_only=on`. The test also attempts a write in the same read-only adapter and requires PostgreSQL to refuse it.

After merge, the coordinator runs the installed command on a real retained window. Require zero correspondence and projection mismatches, separate unknowns and exclusions, and naturally existing corroborated examples for all three types. If a vendor outside the quiet reader is present, retain its opaque binding as evidence. An absent example remains unproven. The production declaration uses the existing Studio receipt observer, which cannot itself execute this new command. That observer is supplementary and remains blocked without a real receipt. Command output is the substantive readback evidence, not release smoke or source admission.
