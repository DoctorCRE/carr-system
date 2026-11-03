# Outlook unread verification lifecycle

This is the supplied WR634.S1.J59 abstract lifecycle, bound to the implementation.
It models one attempt. A retry is a new instance. TLC checks TypeOK,
NoUnverifiedSorting and NoChangedSuccess. The generated summary records seven
states and seven transitions. The model does not prove Outlook's rendering,
parser grammar, account identity, scroll completeness, moves, crashes or timing.

`Row` accepts `Sender: ...` as read and `Unread, Sender: ...` as unread.
Other prefixes are uncertain. Only the explicit status slot leaves the key.
The leading unread form is a synthetic contract, still unverified live.

`Window.inbox_badge()` accepts one selected Inbox in an Inbox window.
The complete synthetic zero shape has exactly one direct child text, `Inbox`.
A positive count adds one child containing ASCII digits, optionally ` unread`.
Matching label and value on the same child are one observation. Conflicting
attributes, nonempty row attributes, nested children, empty children, extra
children, multiple counts, no selected Inbox,
or multiple selected Inboxes fail closed. A walk at the element cap or with a
degraded result is unknown. An absent Inbox is unknown. A missing numeric child
is zero only in the complete zero shape. No live badge-shape observation was
supplied. Normal-run verification must establish whether Outlook uses this shape.

The badge is still required. Two stable partial viewports do not prove mailbox
coverage. On mismatch the existing bounded traversal must produce two matching
scans, preserve initial observations, agree across tabs, match the badge and
finish before its deadline. No traversal or move behavior was relaxed.

## Edge evidence

All tests call the real Row, Window or preserve_unread interfaces.
`UnreadVerificationTest.test_lifecycle_edges_and_count_only_diagnostics` covers:

| Model edge | Guard in lib/outlook_triage.py | Test mode |
| --- | --- | --- |
| Waiting to Sorting | preserve_unread lines 329-346, _coverage lines 292-308 | stable; post_scan before action |
| Waiting to Skipped | preserve_unread lines 347-357 | initial_unknown, preaction_unknown, first_scan_change, second_scan_change, deadline |
| Sorting to Checking | action returns at line 359 | stable |
| Sorting to Changed/Failed | comparison at lines 373-376 | arrival, read mutate during callback |
| Checking to Changed/Failed | comparison at lines 373-376 | checking_arrival mutates during post-action badge read |
| Checking to Failed | post-action handler at lines 364-372 | post_unknown, post_scan |
| Checking to Succeeded | return at line 377 | stable |

The change is detected in the post-action check. The model's Sorting-to-Failed
edge abstracts mutation during the callback plus this later detection into one
step. It is not a claim of continuous observation during filing. The Checking
to Failed edge covers unavailable evidence as well as a known change.
Existing UnreadAgreementTest and RefreshTest cases retain duplicate-identity,
cross-tab conflict, unread-selection, changed-scan and deadline coverage.
BadgeDriverTest exercises the synthetic driver through filing and coded skips.

Coverage diagnostics contain only the fixed fields selected_inbox_rows,
badge_status, viewport_rows, viewport_unread, full_scan_rows, full_scan_unread,
scan_count and coverage. Counts sum tab observations; repeated cross-tab rows
can occur. Unread agreement separately deduplicates identities. Full-scan counts
are the last completed scan and scan_count includes attempted scans.

## Regenerate

Run from the repository root with Java, tla2tools and the statechart skill installed.

```sh
TLA2TOOLS=/tmp/j58-tla2tools.jar JAVA=/tmp/j58-java/Contents/Home/bin/java node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-spec ops/fixtures/outlook-triage-unread/UnreadFix.tla --png
python3 ops/outlook-triage-selftest.py
```

Commit the model, config, mapping, chart source, stories and summary.
The PNG is generated locally. The leak guard forbids opaque binary additions.
TLC scratch output is regenerated and need not be committed.
Live acceptance requires a later runtime refresh and observed normal runs.
