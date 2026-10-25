# Lead dedup scorecard

WR-AI-008 first slice measures the existing lead-sweep rule with ten labelled,
synthetic pairs. The labels are agent-authored policy examples pending human
adjudication. These results do not estimate production accuracy or complete the
Work Request's human-adjudicated fixture requirement. Capture identity evaluation
and any rule changes remain separate slices.

Run `node evals/lead-dedup/scorecard.mjs` from the repository root. It prints JSON
with the workflow source hash, each label and rationale, observed decisions,
presented leads, confusion counts, and error rates. It makes no network requests
and writes no records. The Node VM executes the repository's actual workflow;
only the metadata export is removed to support its top-level return. Finder
agents return synthetic leads and verifier agents return no checks. The VM has no
provided network, database, process, or write functions. This is a trusted-source
evaluation adapter, not a security sandbox for arbitrary code.

The baseline has six duplicate pairs and four distinct pairs.

| Result | Count | Rate |
| --- | ---: | ---: |
| Correct merge | 4 | |
| Correct split | 2 | |
| False merge | 2 | 2 / 4 = 50% of distinct pairs |
| False split | 2 | 2 / 6 = 33.33% of duplicate pairs |

The failures are name-only merging, a collision within the first 24 location
characters, location whitespace, and location taking precedence over shared
practice corroboration. Each case has its own rationale in `pairs.v1.json`.
These failures remain visible in the scorecard. This slice preserves current
workflow behavior, including retaining low-confidence and unverified leads,
combining duplicate source evidence, and promoting confidence when a duplicate observation is HIGH.
It never merges canonical identities.

The automatic regression check is
`node --test mcp-server/test/lead-dedup-scorecard.test.mjs`, also discovered by
the repository unit suite. Tests pin the independently worked baseline counts
and failed case IDs. A policy change must review the labels and update expected
outcomes deliberately. Zero class denominators produce null rates.

Human review of each pair's label and rationale remains required before treating
this fixture as adjudicated evidence. No human reviewer or signoff is fabricated.
