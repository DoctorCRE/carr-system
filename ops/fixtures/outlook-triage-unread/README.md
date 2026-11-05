# Outlook unread verification lifecycle

Historical model for the retired UI triage route. WR634.S1.J102 removes that
route and its exclusive tests. The active runner model and verification are in
`docs/outlook-triage-runner.md`. This model does not describe current filing.

This is the supplied WR634.S1.J59 abstract lifecycle, bound to the implementation.
It models one attempt. A retry is a new instance. TLC checks TypeOK,
NoUnverifiedSorting and NoChangedSuccess. The generated summary records eight
states and nine transitions. The model does not prove Outlook's rendering,
parser grammar, account identity, scroll completeness, moves, crashes or continuous timing. Budget expiry is an abstract event,
not a model of elapsed seconds.

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
| Waiting to Sorting | preserve_unread lines 336-353 | stable; post_scan before action |
| Waiting to Skipped | preserve_unread lines 354-364 | initial_unknown, preaction_unknown, first_scan_change, second_scan_change, deadline; UnreadRoundFixTest.test_deadline_at_final_badge_skips_before_callback |
| Sorting to Checking | action returns at line 378 | stable |
| Sorting to Changed/Failed | comparison at lines 385-410 | arrival, read mutate during callback |
| Sorting to Skipped | file_inbox lines 311-318, preserve_unread lines 380-381 | UnreadRoundFixTest.test_deadline_at_each_selection_point_skips |
| Checking to Changed/Failed | comparisons at lines 385-410 | checking_arrival; UnreadRoundFixTest.test_checking_offscreen_arrival_fails_without_settling; UnreadVerificationTest.test_checking_never_discards_an_observed_change |
| Checking to Failed | post-action handler at lines 400-404 | post_unknown, post_scan; UnreadVerificationTest.test_unknown_closing_badge_fails_after_rows |
| Checking to Skipped | preserve_unread lines 402-403,409-410 | UnreadVerificationTest.test_deadline_during_checking_skips_on_both_scan_paths |
| Checking to Succeeded | return at line 411 | stable |

The change is detected in the post-action check. The model's Sorting-to-Failed
edge abstracts mutation during the callback plus this later detection into one
step. It is not a claim of continuous observation during filing. The Checking
to Failed edge covers unavailable evidence as well as a known change. Budget
expiry on either post-action scan path skips with coverage_deadline.
Post-action observations are bracketed by badge reads even without full scans.
Any observed badge or unread identity/read-state change fails immediately, before
later scans or deadline checks can replace that evidence. Each post-action
scan checks exposed rows immediately and compares a completed scan against the
original unread set. UnreadVerificationTest.test_scan_change_wins_over_deadline_expiry
proves changed observations fail even when the scan exhausts the budget. The closing badge is the observation
boundary; an unobserved arrival after it is outside this guarantee. The same
120-second budget covers initial coverage, final authorization and each row and
folder selection. Expiry stops filing with a coded skip. Window.move retains its
picker cancellation when authorization expires.
Existing UnreadAgreementTest and RefreshTest cases retain duplicate-identity,
cross-tab conflict, unread-selection, changed-scan and deadline coverage.
BadgeDriverTest exercises the synthetic driver through filing and coded skips.

Coverage diagnostics contain only the fixed fields selected_inbox_rows,
badge_status, viewport_rows, viewport_unread, full_scan_rows, full_scan_unread,
scan_count and coverage. Counts sum tab observations; repeated cross-tab rows
can occur. Unread agreement separately deduplicates identities. Full-scan counts
are the last completed scan and scan_count includes attempted scans. Moved rules
are aggregated counts. unread_changed counts observed row changes; badge-only
changes can fail without a known changed-row count. No report emits label hashes.
UnreadRoundFixTest.test_all_emitted_diagnostics_have_no_hash_values scans stdout,
outcome detail and the persisted report for 12-to-64-character hex values.

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
