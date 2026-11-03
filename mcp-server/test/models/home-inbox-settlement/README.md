# Home inbox settlement model

The v3 read derives automatic settlement from canonical task completion, sent replies, resolved fact reviews and received tour feedback. It preserves v1 and v2 and their opaque command versions. Reads write no domain rows or dispositions.

The model separates canonical work, manual disposition and explicit Undo. The numbered PostgreSQL cases execute the work lifecycle through registered verbs. Adapter cases exercise both new named resolution transitions and their command behavior. Repeated reads cover stuttering across the work lifecycle and after adapter Undo. The chart has 20 visible arrows. The chart hides 28 stuttering steps and omits 15 steps within one label. TLC checked 28 states and 113 steps. ResolvedOnly, OverrideWins, SnoozeWins and NoDeadlock hold.

The model excludes networking, actors, stale writes, truncation and source storage. Separate PostgreSQL tests cover replay, actors, stale writes and bounds. Reopening in this model covers automatic settlement and explicit Undo. Manual settlement remains a separate choice.

Replacing v2 would break its deployed app parser. The v2 and v3 SQL reads share one disposition placement function. The v3 read transforms the actor-scoped v3 snapshot and retains its command authority. The app must pin v3 and hydrate command receipts through v3. The auto_settled field grants no command authority.

Completion notifications and call-processing completion do not prove all partner work resolved. Fact review uses the canonical pending predicate, including new observations and extraction abstentions. Tour feedback means receipt of a client shortlist or comment for the exact share grant, as AC-INBOX specifies. It does not claim that a broker has acted on the feedback. The adapter tests execute both named resolution transitions, Undo, replay, snooze, stale versions and actor isolation. The app still consumes v2, so this producer change alone changes no deployed app behavior.

Regenerate with pstack's statechart/scripts/statechart command, passing Inbox.tla, --config Inbox.cfg, --view view.mjs and a new --out directory. Java and TLC are required.
