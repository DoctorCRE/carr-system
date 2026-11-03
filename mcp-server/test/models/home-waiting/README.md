# Explicit Home waiting relationship

`chart.mmd` was written before implementation. Unknown is distinct from a cleared wait only by the retained version and audit. Set and replacement require a current Home item version and a current independent waiting version. Completion clears the stored identity in its source transaction. Reads resolve display identity without changing rows.

The public command/projection tests in `../../home-waiting-postgres.test.mjs` execute name, replace, clear, name again, replay, stale version, missing/merged/deleted identity, cross-actor and foreign-tenant refusals, completion and reopen, concurrent replacements and completion racing a set. The existing Home PostgreSQL suite verifies the unchanged canonical source adapters and disposition semantics.

Generate the abstract model and chart outside the checkout:

```sh
node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-chart /absolute/path/to/chart.mmd --out /tmp/home-waiting-chart --png
```

Set `JAVA` and `TLA2TOOLS` to the installed Java binary and TLC jar. The abstract chart checks reachability only. It omits database transactions, identity merges, tenants, versions and receipts; the PostgreSQL reproductions verify those. The `never` comments describe the acceptance rules, not machine-checked invariants in this abstraction.
