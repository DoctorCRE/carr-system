# MCP registration and checkout imports

`src/tools.js` imports the four family descriptors directly from committed source.
A verb added to an existing family changes that family and its implementation.
A new family adds an import and an entry in the small `toolFamilies` array.
There is no generated family index or install-time registration step. A normal
`git pull --ff-only` leaves the import graph ready to use with installed dependencies.

## SCAC registry is a separate decision

`src/scac-mutation-registry.current.generated.js` is committed and remains the
single registry consumed by production and gates. It contains the reviewed
seal/grant expectations and generated operation contracts. There is no separate
input registry. Runtime imports never run a generator.

`node ops/scac-mutation-inventory.mjs`, from the repository root, checks the
committed operation contracts and exact regenerated text without writing.
The gates class, npm type/test entry points and Wrangler build run that check.
An added or changed verb must explicitly run `./run.sh regen registry` and commit
the result. CI and deploy verify the merged file rather than silently updating it.

The registry is still a shared file. Independent changes to separated operation
rows merge cleanly; adjacent additions can conflict. After resolving source files,
`./run.sh regen registry` resolves an operations-only Git conflict and stages that
file. It refuses conflicts in database seals or grants, preserving those for
explicit review. It restores the original conflict on generation failure.
GitHub can merge an already conflict-free combined registry and required checks
verify it. A conflicting PR must run the repair command on its branch before
entering the merge queue. No merge driver is required.

## Test suites

Add suites as `test/*.test.js` or `test/*.test.mjs`; the runner discovers them.
Names ending in `-postgres.test.mjs` or `-postgres.test.js` run alone. Suites that
import `disposable-postgres.mjs` or `postgres-database.mjs`, or launch `initdb`,
also run alone even with other names. Browser isolation remains in place.

## Consumers checked for checkout safety

The source audit covered all eight workflow files under `.github/workflows/`.
No workflow needs a generation step to make a runtime import resolve.

| Consumer | Import/read path checked |
| --- | --- |
| `doc-drift.yml`, check job | `scripts/doc-drift/ci.py` / `check.py` builds `Tree`, which imports `tools.js` with Node; dependency installation uses `--ignore-scripts`. |
| `doc-drift.yml`, Instruction drift verdict | `ops/ci-evidence.py drift-verdict` propagates the check job's terminal result. It is Python-only and does not itself import tools. |
| `ci.yml`, class and fixture jobs, merge-group events | `ops/ci.sh`, `ops/ci-unit.py`, npm tests/types, source and artifact checks, `ops/local-pg-ci.py`. |
| `main-canary.yml` | The same canonical CI classes, plus `ops/ci-evidence.py` evidence handling. |
| `deploy.yml`, production and staging | Locked dependency install, candidate sealing, `ops/verb-count.sh`, migration runner, Wrangler build and Worker release readback. Production writes were not executed in this audit. |
| `fallow.yml` | Static import analysis after dependency install; no generated runtime input remains. |
| `review-gate.yml` | Python review gate and its selftests; no tools import identified in the entry points. |
| `edge-liveness.yml` | Python incident-delivery entry point; no local tools import identified. |
| `backup-nightly.yml` | Backup/status Python and shell entry points; no local tools import identified. No backup, restore, send or production operation was executed. |
| Worker runtime | `src/index.js`, `src/mcp.js`, `src/release.js`, `src/dealroom-web.js`, `src/atlas-inventory-graph.v5.js` import tools directly. Wrangler bundles their static graph. |
| Studio/MacBook local CLI | `run.sh` -> `tools/call-verb.py` -> `mcp-server/local-verb.mjs`. Its normal HTTPS path is zero-install; its explicit break-glass loader imports tools. Both retain their existing authority checks. Fresh-clone schema read was exercised on this host; no remote machine execution is claimed. |
| Local tests and types | `mcp-server/run-tests.mjs`, all MCP test modules/helpers, `generate-verb-types.mjs`, npm test/typecheck; no prepare hook rewrites source. |
| Verb count and archived checkout comparison | `ops/verb-count.sh`, `ops/verb-loss-vs-base.sh`, `ops/verb-loss-guard-selftest.py`; committed source is imported with the established dependency symlink topology. No scratch regeneration helper remains. |
| Release verification | `ops/release-smoke.py` imports the exact candidate `src/tools.js` for its expected tool inventory. |
| Other direct import selftests | `ops/catchup-writer-selftest.py`, `ops/routine-leads-selftest.py`, `ops/schema-reconstruction-selftest.py`. |
| Source-only readers | `ops/abilities-manifest.py`, `ops/guard-selftest.py`, `ops/loop-guard-selftest.py`, `ops/store-markup-scan.py`, `ops/dot-database-design-db-gate.py`, and `tools/export-rule-admission.py` inspect source rather than execute its imports. |

## SCAC consumers checked separately

- `src/mutation-registry.js` imports the committed operations, seal and flags for
  runtime dispatch. Its callers include tools, MCP, rule/identity/retrieval,
  lifecycle and release surfaces through that static graph.
- `src/atlas-inventory-graph.v5.js` imports operation rows directly, alongside
  tools and mutation-registry identity.
- `ops/scac-mutation-inventory.mjs` imports the same committed file for checks
  and explicit regeneration; `tools/regen.py` preserves its seals/grants.
- `lib/rule_routes.py` follows the import in mutation-registry and reads the same
  file for `ops/rule-route-coverage.py` and its selftest.
- `ops/scac_mutation_db_inventory.py validate_current_catalog` imports the grant
  expectations and compares them with the installed SQL contract. Its scheduler
  retirement and migration-control tests were included in the source audit.
- Grant/operation test consumers are `client-portal-register-postgres`,
  `client-portal-postgres`, `fact-store-postgres`, `phone-call-sessions-postgres`,
  `team-memory-postgres`, `work-queue-postgres`, `atlas-inventory-graph-read`,
  `current-mutation-registry-generation` and `siep-11-mutation-registry` under `test/`.
- `ops/ci-pushfloor.py`, `ops/local-floor-selftest.py`, `ops/ci-selftest.py`,
  `ops/action-risk-registry.py` and `bin/migrate-dell.sh` select or describe checks
  against that same path; they do not introduce an alternate registry.
