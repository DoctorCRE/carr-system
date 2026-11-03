# Home inbox settlement model

The v3 read derives automatic settlement from canonical task completion and sent replies. It preserves v1 and v2 and their opaque command versions. Reads write no domain rows or dispositions.

The model separates canonical work, manual disposition and explicit Undo. The 20 numbered PostgreSQL cases execute every visible arrow through registered verbs. The chart hides 12 stuttering steps and omits nine steps within one label. TLC checked 12 states and 51 steps. ResolvedOnly, OverrideWins, SnoozeWins and NoDeadlock hold.

The model excludes networking, actors, stale writes, truncation and source adapters. Separate PostgreSQL tests cover replay, actors, stale writes and bounds. Reopening in this model covers automatic settlement and explicit Undo. Manual settlement remains a separate choice.

Replacing v2 would break its deployed app parser. Separate SQL functions would repeat source and disposition logic. The v3 read transforms the existing actor-scoped snapshot and retains its command authority. The app must pin v3 and hydrate command receipts through v3. The auto_settled field grants no command authority.

Completion notifications and call-processing completion do not prove all partner work resolved. Fact-review and tour-feedback adapters remain follow-up work. The app still consumes v2, so this producer change alone changes no deployed app behavior.

Regenerate with pstack's statechart/scripts/statechart command, passing Inbox.tla, --config Inbox.cfg, --view view.mjs and a new --out directory. Java and TLC are required.
