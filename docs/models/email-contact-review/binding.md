# Atomic signature binding

The observed binding is {party_id, party_version, sender, email}. The sender is the
message address and email is the raw stored email field in the contact snapshot.
The server locks the contact tables for the transaction, validates the live ref,
party version, stored email and unique exact sender, and inserts only on agreement.
A held response has no finding, event or replay receipt. A refreshed request can
succeed and replays exactly once through the existing envelope.

Candidate A uses row locks on the role and party plus a sender lookup. It cannot
exclude a concurrent new party with the same sender. Candidate B uses short SHARE
table locks on party and role tables, then reuses the existing contact reader for
exact sender rules. B is chosen to cover insert phantoms without requiring every
identity writer to adopt a new advisory lock. This blocks contact mutations for
the short finding transaction and preserves parallel finding reads/writes.

Throughput checkpoint. One bounded server change, one caller update, one PostgreSQL
regression suite. No migration. Run the focused suite before broader CI.

Binding is a companion to Review.tla and preserves that model's invariants unchanged.
It abstracts party, role, sender and version changes to a two-valued snapshot.
Submit is the atomic validation and insert transaction. PostgreSQL locks prevent
Change during Submit. Changes after commit are legitimate and are outside this
model's terminal Recorded state. It omits extraction, transport failures and
arbitrary source counts, which remain in Review and Canonical.

| New transition | Executed test |
| --- | --- |
| Change then Submit to Held | stale snapshot holds without receipts and current retry records once |
| Sender change then Submit to Held | changed sender and ambiguous sender hold |
| Ref reassignment then Submit to Held | changed role binding holds |
| Refresh then Submit to Recorded then Replay | stale snapshot holds without receipts and current retry records once |
| Submit locks out Change until commit | finding transaction excludes competing identity writes |
| Change commits before waiting Submit | waiting finding validates after a competing party change |

Regenerate with Java 21 and TLA2TOOLS pointing at the pinned jar documented in README.
node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-spec docs/models/email-contact-review/Binding.tla --config docs/models/email-contact-review/Binding.cfg --map docs/models/email-contact-review/binding-view.mjs --out /tmp/j32-binding --png

Executed TLC summary reports 10 model states, 16 model transitions, five chart
states and eight chart transitions. All three invariants hold. The rendered
PNG was inspected. The replay self-loop stutters and is tested at the handler.
Generated stories.md and summary.json remain in /tmp/j32-binding.

The Model the Domain principle keeps the observed binding in one object instead
of adding independent checks to every caller. Codebase design retains one server
validation seam and reuses the deployed exact-sender reader. The debt pass adds
no parallel email parser or identity resolver. The additional code replaces the
unconditional signature-finding write; no existing guard can be deleted safely.
