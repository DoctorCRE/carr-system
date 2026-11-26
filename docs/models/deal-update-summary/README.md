# Deal update summary read lifecycle

The model checks the read lifecycle. SQL ordering and text coverage are exercised through the registered PostgreSQL test, not modeled by this finite chart. The producer is read-only. No prior summary behavior is replaced.

Regenerate the chart with `JAVA=/opt/homebrew/opt/openjdk@21/bin/java TLA2TOOLS=/tmp/J79-tla2tools.jar node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-chart history.mmd` in a scratch copy.

| Transition | Behavior test |
| --- | --- |
| Reading to Ready | multiple updates retain correction and unresolved disagreement with exact citations |
| Reading to Unavailable | malformed cursor and bounds refuse; empty is known empty; source failure never looks complete |
| Ready to Ready | continuation survives production read auditing and unrelated writes; registered projection isolates deals, pages ties, refuses arrivals and records source-bound extracts |
| Ready to Stale | revision covers older originals, all source families and new arrivals; registered projection isolates deals, pages ties, refuses arrivals and records source-bound extracts |
| Ready to Refused | continuation binds actor, deal and source revision; stale and retired reads refuse |
| Stale to Reading | registered projection isolates deals, pages ties, refuses arrivals and records source-bound extracts |
| Unavailable to Reading | malformed cursor and bounds refuse; empty is known empty; source failure never looks complete |
| Refused to Reading | continuation binds actor, deal and source revision; stale and retired reads refuse |

The consumer owns retries and retirement; a new call restarts the stateless producer. The finite graph does not prove access control, source hash equivalence or database ordering. TypeOK passing is only graph well-formedness.

The revision hashes every supported row of this deal in deterministic source/UUID order, including original retained fields, in the page statement snapshot. Audit counters, unrelated tables, foreign deals and unsupported events do not participate. Relevant arrivals (including backdated rows), edits and deletions invalidate continuation. The revision requires a full source scan per page; summary and page output remain bounded. No persisted revision or migration is needed. Hashes describe current content, so changes fully reverted before the next read are equivalent content.
