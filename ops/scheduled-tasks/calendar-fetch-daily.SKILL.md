---
name: calendar-fetch-daily
description: Weekday local EventKit calendar capture into the CARR record layer.
---

Run the registered weekday calendar capture through the local macOS EventKit
access bundle. This task reads the local Calendar store, matches attendees only
through the governed record-layer path, and records its result through the
control plane.

## Registered operation

The native launchd surface owns the weekday 07:20 America/Chicago cadence. Do
not create a duplicate scheduler or substitute a published-feed reader.

When executing the registered operation manually for an approved recovery or
readback, use the exact manifest entrypoint and arguments:

```sh
cd "{{REPO}}" && bin/calendar-eventkit-capture.sh --days 7
```

The access bundle must have macOS Calendar permission. If permission is denied,
the bundle or its protected output is unavailable, or the command emits a
`calendar-capture: FAIL` or `calendar-capture: REFUSE` line, report that result
plainly and leave the record layer unchanged. Do not work around the failure by
reading another calendar source or by creating a new task.

## Verify the bounded result

Successful live output is the finite aggregate marker:

`calendar-capture: source=eventkit mode=live scanned=N exact=N domain=N unknown=N writes=N failed=0`

Treat a nonzero `failed` count or a missing marker as a failed run. Report only
the aggregate marker or failure line; do not place attendee addresses, calendar
prose, event identifiers, tokens, or protected snapshots in task output.

The native scheduler owns recurrence. Do not add a
manual retry loop, bypass the registered mode, or claim completion from the
launch alone. There is no synced-file fallback on the normal path.
