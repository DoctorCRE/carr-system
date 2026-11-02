# Count-only coverage census

`hxprobe census --immutable-copy <copy.hxd> <copied-Files-directory> <account-key>`
returns totals-only JSON. The `totals` map has fixed metric names and integer
counts. Per-message rows are never serialized. The output contains no subjects,
addresses, display names, body text, folder names, sidecar paths, or account keys.
Errors use the fixed text `census input rejected` and exit code 2.

Enable one census on the next scheduled intake after installing this revision through
the normal intake install path. Run this command as the intake's user:

```sh
python3 /Users/booko/carr-system/ops/outlook-census-request.py
```

The request is an empty 0600 regular file owned by the intake user. The command
fails if a request already exists and prints only a fixed status code. It resolves
the output directory from the intake user's validated Outlook settings. On another
machine, run the same wrapper from its checkout. Automation uses this wrapper too. No profile or executable can be
selected by a request. No LaunchAgent change or new macOS permission is needed.

Intake consumes the request once after its native helper creates the fresh private
`store.hxd` and `Files` copy. Before normal decoding it runs the configured hxprobe
census with the configured folder account. The subprocess has a 30-second timeout.
Failure logs only `coverage_census_failed=REASON` and normal intake continues.
The fixed reasons are `request_stat`, `request_consume`, `request_invalid`,
`setup`, `launch`, `timeout`, `exit`, `oversize`, `json`, `totals`, and `write`.
`setup` covers settings and temporary-file failures. `totals` covers missing,
unknown, empty, or invalid counts. `oversize` rejects stdout larger than 8 MiB.
No exception text, stderr, message content, or address is logged. No diagnostic retry occurs.
A request waits until intake actually creates a fresh snapshot.

The latest successful totals are atomically saved as 0600
`out/outlook-intake/coverage-census.json`. Only fixed metric labels and integer counts
are retained; the census emits no per-message entries. A failed attempt leaves the previous
successful result unchanged, so use the result file's modification time to distinguish
it from a new result. The existing intake finally block deletes the copy, decoded
database, sidecars, and diagnostic temporary output, including after a decoder error.
The native helper and its signing are unchanged. Never make a separate manual copy.

The census command rejects the live store filename, a neighboring HxStore.lock,
corrupt blocks, and unrecognized headers. It creates no snapshot and contacts no mail
server. All regression runs use synthetic stores.

The cohort contains decoded `IPM.Note*` messages with the selected typed account.
It includes unresolved folder references so their shapes remain visible. This is
an account census, not intake's admission verdict or proof of server-side folder
completeness. Rejected message layouts and unlinked child objects have global
counters because their account cannot safely be inferred.
`body_orphaned_all_accounts` counts body envelopes whose owner key is absent from
all observed message identity headers. `recipient_orphaned_all_accounts` counts
distinct recipient child identities with such an absent owner. A rejected message
layout with a readable identity is observed, so its children are not orphaned.
Unlinked counters count children whose owner identity could not be read at all.

`body_objects` counts linked body envelopes, including rejected revisions.
`body_absent` counts messages with no linked body envelope. `body_rejected` counts
linked envelopes the body parser rejects. Inline and sidecar counters count unique
accepted candidates. Sidecars are checked even when an inline candidate wins.
Resolution failures distinguish an unindexed path, an open failure, and gzip/read
failure. Body completeness uses the same resolution and preview fallback as the
normal export. The normal decoder's 32 MiB sidecar read limit still applies.

`recipient_objects` counts distinct linked child identities, including rejected
ones. `recipient_children` counts distinct children with at least one decoded
revision. `recipient_rejected` counts rejected linked revisions. The none, listed,
and conflict counters partition messages. Kind counters count exported recipients;
a conflicting child prevents export of that message's recipient list.

Missing subjects distinguish unset/conflicting slots from empty decoded values.
Unknown folder counters distinguish unset/conflicting current references,
uncatalogued targets, account mismatches, and unnamed catalog entries.

Do not infer missing local bytes from a preview alone. First rule out rejected
layouts, unlinked children, orphaned children, rejected body envelopes, and failed
sidecar reads. Zero orphan counters rule out only missing observed owner keys, not
wrong links to existing keys, rejected revisions, or unrecognized object shapes.
A zero absence count cannot establish full mailbox hydration on the mail server.

Synthetic regression coverage lives in `ops/hxstore-local-key-selftest.py`.

## Synthetic timeout sizing

On a 982,928,089-byte synthetic store with 140,000 messages, plain release
runs had a median of 5.03 seconds and ranged from 4.66 to 8.66 seconds.
Runs throttled with `taskpolicy -b` had a median of 12.36 seconds and ranged
from 11.14 to 13.70 seconds. The intake LaunchAgent sets no `ProcessType`, so
launchd lightly throttles it. The 30-second budget is about 2.2 times the
slowest throttled run. Stores over about 2 GB or runs on the 8-core MacBook Pro
may need a re-measure. This is a one-shot budget, not a latency guarantee for
larger stores, other hardware, or sidecar workloads.
The fixture used inline 3,000-character HTML bodies and one recipient per message.
No real store was opened. Reproduce the small cap regression with
`python3 ops/hxstore-local-key-selftest.py LocalKeyExportTests.test_large_census_emits_only_totals_below_intake_cap`.
