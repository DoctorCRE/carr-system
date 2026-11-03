# Inline Home review

Design before implementation. The data shape is a discriminated review reference
with kind, item_id, source_version and supported operations. Loading detail takes
only item_id and source_version. The server derives identity and operations again.

Two designs were considered. A universal execute-home-review command would route
writes through Home and duplicate domain validation. The selected design adds only
a read and returns arguments for existing commands. Callers retain owner receipts
for Undo. A resolved card never invents a completion event.

The read uses the existing actor-scoped v3 projection, exact email hydration and
the existing fact review read. A narrow migration grants the existing limited
partner-recipient view to the writer role used by Home. Pending call
proposals, email next steps, overdue assessments and first-contact drafts remain
outside this projection. Notifications, processing sessions and tour feedback have
no review operation. Reply send failures retain their existing recovery flow.

Throughput checkpoint. Blocking first steps are discovery and this chart. Module,
contract and tests share one interface, so one worker owns them. No mutable state
is added. The smallest safe decomposition is one read module plus existing owner
reads. Nested agents are forbidden by the task.

Named PostgreSQL tests follow chart edges. Owner submission and refresh use
complete-one-action. Stale submission and machine authority use that same command.
Reply detail, mailbox scope, fact detail and freshness are checked separately.
The chart models request transitions, not app UI.

Transition tests in home-review-postgres.test.mjs:

- Projected to detail loaded targets one task and changes no domain state.
- Detail loaded to owner submitted to refreshed completes one representative.
- Owner submitted to refused rechecks stale version and machine authority.
- Refreshed to projected uses owner receipt for Undo.
- Unsupported notification card refuses safely.

home-inbox-postgres.test.mjs additionally names the stale-source refusal,
another-actor refusal and refused-to-projected refresh transitions. It verifies
fact evidence and decision lists, resolved refusals and duplicate representatives.
The chart-to-spec check tests only the abstract state graph and TypeOK, not domain
guards. PostgreSQL tests are the executable authority and freshness checks.
