# Lead reachability v1

`lead-board` with `workspace: "leads"` adds `reachability` to each board row and selected detail. The outer `lead-workspace.v1` contract remains additive. Consumers pin `lead-reachability.v1` and validate the fragment against `mcp-server/contracts/lead-reachability.v1.json`.

This version deliberately returns `classification: "research"` for every lead. `contact_eligible` describes lifecycle restrictions. `research_state` describes recorded research completeness. Neither proves a verified usable contact route. `record-finding` defines `verified` as an identity pass and accepts free-form values for email, cell and office_phone. `update-party-contact` promotes facts using a source string, without a typed finding-to-current-route binding. This projection does not infer that binding. A positive Reachable view remains unproven until canonical route verification supports it. Missing evidence never means a bad lead.

`evidence_state` reuses researchSummary's expiry, future-observation and conflict rules. `evidence` retains source, finding ID, observation, expiry and value. `evaluated_at` is the evaluation clock. `source_revision` is SHA-256 of the JSON array `[lead.base_version ?? null, contact_eligible ?? null, evidence]` in returned evidence order. A revision identifies the classification inputs, not a release or a freshness promise. Re-evaluate on refresh and expiry even when the source revision is unchanged.

The projection preserves board membership, ordering and exact IDs. Existing suppressed/merged/deleted filtering remains in the view. It makes one PostgreSQL statement and no external calls. Each lead reads at most 501 findings through the existing evidence query. More than 500 refuses the snapshot rather than claiming complete evidence. There is no cache, queue, write, enrichment, score threshold or outreach. At 10x leads, evidence lookup and JSON volume are the first capacity costs. The single statement gives consistent source inputs. Database failure refuses the read.

## Model and tests

The model abstracts snapshot refresh, evidence changes and eligibility changes. Authentication refusal remains owned by the existing registered tool dispatcher. This read introduces no session or authorization state. Evidence changes between requests produce new snapshots.

| Transition | Registered board test |
| --- | --- |
| sourced finding recorded | identity verification and free form contact findings never assert a usable route |
| evidence expires | snapshot refresh follows expiry, conflict, renewal, removal and contact restriction |
| discrepancy recorded | snapshot refresh follows expiry, conflict, renewal, removal and contact restriction |
| discrepancy expires | snapshot refresh follows expiry, conflict, renewal, removal and contact restriction |
| fresh finding recorded | snapshot refresh follows expiry, conflict, renewal, removal and contact restriction |
| evidence removed | snapshot refresh follows expiry, conflict, renewal, removal and contact restriction |
| contact restricted | snapshot refresh follows expiry, conflict, renewal, removal and contact restriction |
| restriction lifted | snapshot refresh follows expiry, conflict, renewal, removal and contact restriction |
| snapshot refreshed | source revision changes with source facts and preserves future evidence as unknown |

Regenerate from a scratch copy of this directory with the statechart skill's `from-chart reachability.mmd`. Set `JAVA=/opt/homebrew/opt/openjdk@21/bin/java` and `TLA2TOOLS` to a downloaded TLC jar. The checked model has eight states, twenty transitions and no broken rule.

## Production proof

After a containing CARR release is verified, call `./run.sh call lead-board '{"workspace":"leads"}'`. Compare exact lead IDs, finding IDs, values, sources, observation/expiry and source revisions independently with `prospect-research` for a verified-route candidate, incomplete lead and restricted or conflicting case. Contact eligibility alone must never be treated as reachable. No qualifying positive record yields insufficient evidence, exit 78. This v1 contract cannot certify a positive route. Do not manufacture a verification finding. The coordinator owns the containing-release proof; this build performs no production calls.
