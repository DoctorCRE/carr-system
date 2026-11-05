# Client tour proposal lifecycle

Card: WR586.S5.J1.

CARR pulls a client's saved Tour List as a proposal. Only a partner acceptance
changes the existing Tour selection cart. The portal owns saved selection
revisions; CARR owns proposals, partner decisions, and acceptance receipts.
The contract is `workspace/contracts/client-tour-proposals.v1.json`.

The existing cart already provides immutable versions, current-version checks,
and canonical Tour property IDs. A second shortlist table would require every
reader to merge two authorities. The proposal table therefore records the
client revision and original cart IDs, and acceptance appends the existing cart.
Per-property decisions apply only the partner's current choices. Earlier choices
remain recorded without overriding intervening manual cart changes.

The read returns differences against the current cart. Acceptance must present
that cart's version ID, including null for an empty unversioned cart. A newer
portal revision supersedes unresolved proposals. Already accepted cart versions
remain part of the immutable history. Dismissal keeps the current cart, including
earlier partial choices.

The source adapter issues one GET to the registered slug on the fixed portal
origin. No caller chooses a URL or supplies a saved-list body. It rejects unknown
fields, wrong slugs, invalid revisions, duplicate IDs, oversized responses, and
unresolved properties. It never guesses property identity from display text.
Hosting authorization is an activation dependency. No existing session or
credential was inspected, and compatibility with the live endpoint is unproven.

The circuit breaker and minute limit are per Worker isolate. They bound local
fan-out without claiming a global provider quota. Failures refuse the pull;
existing proposals stay unchanged. The adapter does not retry or return stale
data. PostgreSQL serializes acceptance with existing cart writers by locking
the same Tour row. No proposal process or queue runs in the background.

`lifecycle.mmd` was authored before implementation. The PostgreSQL integration
test executes every persisted lifecycle transition. Pulled is a transient fetch
state, never a persisted proposal visible before validation. The chart omits
authentication, concurrent writers, property resolution, and capacity; tests
exercise these separately. The statechart tool generated TLA+ but could not run
TLC because `TLA2TOOLS` was unavailable. There is no formal-proof claim.

The PostgreSQL test loads production functions directly from migrations for
actor resolution, deal visibility, and cart append. Its minimal Tour tables omit
unrelated route, share, and render dependencies and their triggers. This test
does not replace strict full-schema hosted CI or the later production proof.

Run the focused tests with `node --experimental-strip-types --test
mcp-server/test/client-tour-proposals-postgres.test.mjs
mcp-server/test/client-tour-proposals-integration.test.mjs`. To replay the
pre-migration failure, run the integration test directly with `--baseline`.
That replay omits migration 1149; it is not an isolated origin/main run.

The morning brief reads stored pending and partially accepted proposals through
`ops.pending_client_tour_proposals`. It returns at most 20 owned deals, with one
extra database row used to detect truncation. Each line counts distinct additions
and removals against the current cart. Disabled portals and merged clients are
excluded. Reads do not fetch the portal, import proposals, or change cart rows.
Source failures remain unavailable; a successful zero-row read says none.
