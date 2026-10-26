# Lead dedup scorecard

WR-AI-008 measures the lead-sweep dedupe rule with ten labelled, synthetic
pairs. The labels are agent-authored policy examples pending human
adjudication. These results do not estimate production accuracy or complete the
Work Request's human-adjudicated fixture requirement. Capture identity
evaluation remains a separate slice.

Run `node evals/lead-dedup/scorecard.mjs` from the repository root. It prints JSON
with the workflow source hash, each label and rationale, observed decisions,
presented leads, confusion counts, and error rates. It makes no network requests
and writes no records. The Node VM executes the repository's actual workflow;
only the metadata export is removed to support its top-level return. Finder
agents return synthetic leads and verifier agents return no checks. The VM has no
provided network, database, process, or write functions. This is a trusted-source
evaluation adapter, not a security sandbox for arbitrary code.

The fixture has six duplicate pairs and four distinct pairs.

| Result | First slice | Current rule |
| --- | ---: | ---: |
| Correct merge | 4 | 6 |
| Correct split | 2 | 4 |
| False merge | 2 (50% of distinct) | 0 |
| False split | 2 (33.33% of duplicate) | 0 |

The first slice failed on name-only merging, a collision within the first 24
location characters, location whitespace, and location taking precedence over
shared practice corroboration. The current rule merges two leads only when their
normalized names match, they share at least one normalized corroborating field
(practice or the whole location), and no corroborating field present on both
differs. The workflow still retains low-confidence and unverified leads,
combines duplicate source evidence, and promotes confidence when a duplicate
observation is HIGH. It never merges canonical identities.

The automatic regression check is
`node --test mcp-server/test/lead-dedup-scorecard.test.mjs`, also discovered by
the repository unit suite. Tests pin the fixture counts and one probe per merge
rule. A policy change must review the labels and update expected outcomes
deliberately. Zero class denominators produce null rates.

Human review of each pair's label and rationale remains required before treating
this fixture as adjudicated evidence. No human reviewer or signoff is fabricated.
