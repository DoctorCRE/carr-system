# Exact-card targets

The resolver accepts a reference and returns one current authorized card. Reply references retain the producer's `al1_` bytes. Post-call review and feedback references use the same bounded `al1_` base64url format with `cr` and `fe` kind codes and a source version. These are addresses, never credentials. V1 is unchanged.

The alternatives were a Home projection scan and exact source reads. Exact reads keep pagination out of authorization and target identity. The typed module owns reference parsing, lifecycle projection and response parsing. The stable database function owns post-call recorder ownership and feedback grant ownership before detail projection. Reply hydration reuses the registered reply resolver from PR 3073. Domain commands retain all write authority. This module performs no writes.

A review targets the capture session, including its candidate and change metadata. Its version hashes the permitted detail, including candidate states. A feedback target names one immutable event, never the newest event on a grant. Its version is the event order. Revoked, rotated and expired grants retire targets. Received feedback is resolved and offers no operations. Resolved source state takes precedence over reference staleness, so an original push opens a completed review as resolved. Stale pending targets return the current version and detail with no operations. Retired targets offer neither detail nor operations. Unknown and foreign targets return only `action_link_not_found`.

At current and tenfold load, an indexed target lookup and per-session candidate reads replace a Home scan. The first capacity limit is an unusually large single call's candidate collection. No cache or stale fallback exists. Database failures refuse. Each request performs sequential authorization and hydration; no queue, external provider, fan-out, automatic retry or new write exists. External circuit breakers, send idempotency and provider rate limits are inapplicable. Existing authenticated request limits apply. Reply hydration rechecks its source before returning; subsequent domain commands retain their existing concurrency checks.

`CardTargets.tla` models observable read outcomes, detail visibility and operation availability. It does not model SQL query planning, database concurrency, authentication implementation or notification delivery. PostgreSQL tests exercise those read permissions through the registered verb. The model check observed seven states, fourteen model transitions and no invariant violations. Regenerate in a scratch directory with the statechart skill's `from-spec CardTargets.tla`, its adjacent config and map, and `TLA2TOOLS` and `JAVA` configured. `statechart.mmd` is its generated chart.

Transition coverage in `action-card-targets-postgres.test.mjs`:

| Transition | Named test, for both partners |
| --- | --- |
| Zero pending decisions to resolved | processing completion without decisions is resolved |
| Requested to current view | current source resolves repeatedly without mutation |
| Requested to foreign refusal | foreign and missing refuse identically before detail |
| Requested to missing refusal | foreign and missing refuse identically before detail |
| Current view to stale | changed source is stale then current version restores review |
| Stale to current view | changed source is stale then current version restores review |
| Foreign request followed by owner request | foreign and missing refuse identically before detail |
| Current view to retired | retired sources hide detail and operations |
| Current view to missing | deleted source becomes missing |
| Stale reference to resolved | an original push reference shows resolved after canonical review completes; reviewed source resolves with no operations |
| Current view to completed review | reviewed source resolves with no operations |

The proof CLI requires the expected merge commit and a containing production release. An explicitly supplied authorized human read wrapper receives only `resolve-action-link-v2` and exact reference JSON. It unwraps MCP content into the registered response or refusal payload. The proof accepts the closed handler or public refusal shape and compares every field except the per-request correlation ID. No partner credential is opened by the proof. Without that wrapper or enough evidence it exits 78. Assertion failures on a containing release exit 1; success exits 0. The shared release reader owns HTTP transport. Fixture mode is always labeled and never proves production.

The app slice must pin the merged CARR commit and `mcp-server/contracts/action-link-resolve.v2.json`, preserving the bounded references and their distinct `rd`, `cr` and `fe` kind codes. No app or software-factory runtime change belongs in this producer slice.
