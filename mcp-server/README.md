# MCP registration and checkout imports

`src/tools.js` imports `#verb-families`. Node discovers committed descriptors in
`src/tool-families/` read-only. Add a descriptor and its implementation to register
a family; no shared list edit is needed. Each descriptor exports exactly
`{source, createTools}`. Source is the reviewed logical locator; dependency binding
receives the existing bounded helper object. The four original families initialize
in their original order; new descriptor IDs append in bytewise order. Duplicate
verb names refuse registration. Inline handlers and envelope policy remain central.

## Reviewed operation contracts

`contracts/operations/<verb>.json` owns each expected operation row. The stable
`src/scac-mutation-registry.current.generated.js` facade retains seal metadata and
exports the composed operation view through `#operation-contracts`. Node imports
need only committed source and installed dependencies, including when install
scripts are disabled. Imports never write to the checkout.

`node ops/scac-mutation-inventory.mjs` compares actual tools with committed rows.
CI never regenerates expectations. Run `./run.sh regen registry` explicitly when
changing a contract, then review and commit only changed rows. It refuses source
conflicts and seal drift; the obsolete flat-file conflict repair is removed.
Retirement still requires the existing retirement ledger and loss checks.

## Worker assembly

Wrangler's Python custom build validates actual contracts and atomically publishes
both static adapters under ignored `.generated/`. Its aliases replace both Node
adapters; incorrect alias configuration refuses the build. Content-addressed
assembly directories contain static imports, expected rows and an input receipt.
A single atomic pointer publishes a complete pair after validation. A failed build
leaves the previous complete pair unchanged and stops Wrangler. Every build
revalidates inputs; there is no runtime cache, install hook or runtime generation.
The Worker receives static data/imports and performs no filesystem discovery.
Run `python3 tools/verify_registry_bundle.py` from the repository root for a
credential-free Wrangler dry-run and static-adapter graph check; scratch stays
under `scratchpad/` and is removed when verification finishes.

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
  and explicit regeneration; `tools/regen.py` preserves its seal.
- `lib/rule_routes.py` validates the committed operation shard directory for `ops/rule-route-coverage.py` and its selftest.
- `ops/scac_mutation_db_inventory.py validate_current_catalog` reads the
  migration-installed grant contract and compares it with the installed catalog.
  Its scheduler retirement and migration-control tests were included in the source audit.
- Grant/operation test consumers are `client-portal-register-postgres`,
  `client-portal-postgres`, `fact-store-postgres`, `phone-call-sessions-postgres`,
  `team-memory-postgres`, `work-queue-postgres`, `atlas-inventory-graph-read`,
  `current-mutation-registry-generation` and `siep-11-mutation-registry` under `test/`.
- `ops/ci-pushfloor.py`, `ops/local-floor-selftest.py`, `ops/ci-selftest.py`,
  `ops/action-risk-registry.py` and `bin/migrate-dell.sh` select or describe checks
  against that same path; they do not introduce an alternate registry.
