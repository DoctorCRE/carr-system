# Schema after the machinery purge — 2026-10-23

Report only. The migration-derived census contains **504 tables and 5,620 columns**. Three exclusive scheduler ledgers have dependency-checked source-safe drop proposals; one unused cache evidence column is an optional smaller slice. The three named legacy-schedule tables lost their writer but retain SQL/administrative readers and are **not safe standalone drops**. Five product query slices identify 12 missing index access paths. No database or dump was accessed, and none of the SQL was executed.

## Ranked findings and delivery slices

| Rank | Slice | Disposition | Scope and dependency |
|---|---|---|---|
| P1 | D1 | Repair/retire callers first | Legacy schedule surface/provider/launchd projections are writerless while observation, disable and shared enqueue readers survive. |
| P2 | S1 | Complete forward SQL below | Retire cost-refusal admission function, metric view and ledger; preserve historical EXECUTE capability evidence. |
| P2 | S2 | Complete forward SQL below | Retire three lease-bound cache functions and cache-observation ledger; prune only corresponding retired tests. |
| P2 | S4 | After S1 | Retire cost reservation functions and ledger; S1 removes their last additional SQL caller. |
| P3 | S3 | Optional | Remove unused `cognition_result_cache.evidence_refs` if retaining the cache table temporarily. |
| P2 | Q1–Q4 | Index proposals | Leads, Home/deal timelines, morning triage, and vendor relationships lack matching access paths. |
| P3 | Q5 | Index proposal | Client-to-deal lookups lack a client-led index. |
| P2 | D2–D3 | Deferred, dependency-bound | Larger provider/cache and approval-trigger retirement needs shared-reader/grant/receipt decisions. |

“Source-safe” means the exclusive caller closure was checked in this repository and the proposed migration refuses unexpected dependencies. It does not prove live row retention, unmanaged callers, applied frontier, lock availability or query plans. “Unknown” in the appendix is an explicit unresolved attribution, never a recommendation to drop. Risk/lock headers, seal accounting and implementation rehearsal requirements are included with each migration; filenames are intentionally unreserved until implementation.

The appendix gives every table/column a writer/reader/default/trigger mapping and positive product-path evidence, with 1,806 source references. Dynamic SQL and overload limitations are stated rather than turning missing text matches into unsupported absence claims.


## Scope, assumptions, and evidence boundary

- Audited repository: `DoctorCRE/carr-system`, fetched `main` pinned to [`3e91aef47f8a97731befc4a0f46ee7dc0ca36db5`](https://github.com/DoctorCRE/carr-system/tree/3e91aef47f8a97731befc4a0f46ee7dc0ca36db5). All `file:line` references refer to this commit, not the initially attached, older checkout. Report date requested by the user: 2026-10-23 (America/Chicago).
- The request contained an interleaved integrity audit and schema audit. Both were treated as intended. Each report is delivered in its own draft PR containing only the requested Markdown file; no proposed runtime fixes or migrations are implemented here.
- Read `AGENTS.md` before repository work. Its evidence rules apply. The explicit user instructions to write these reports, open draft PRs, and avoid production effects take precedence over its generic record-layer-only writing and merge/delivery policy. The required codebase-design and zero-tech-debt skills were read and applied to the reports. No CARR record verbs, approval requests, production calls, database connections, or deployments were used.
- Reviewed source and Git history, using deletions since 2026-10-20 to seed the scan and following older history when a current defect required it. A reference to a deleted fixture name, an immutable historical migration, an explicitly archived document, a generated output with a surviving producer, or an external installed executable is not by itself a defect.
- The audit uses repository source, not installed macOS jobs, deployed verb discovery, another repository's application source, database statistics, or live database contents. “No caller” means no caller found in the stated source collections. Dynamic dispatch, external database clients, applied migration frontier, actual data retention, and live query plans remain unverified.
- The worktree helper created isolated trees but refused shared Python and Node runtimes because their installed packages did not match the pinned locks. That is an environment limitation, not a missing-source defect. Checks below used static parsers, dependency-free tools, and hermetic fixtures. Full strict CI and database reconstruction were not run; this report-only change does not modify runtime behavior.

- Final `main` refresh: `804a3794fb120dcfec980e11b958d205d90f6130` (#2178) adds `tools/codemap.py`, its test, a `run.sh map` entry, and a warning-only CI description check. The complete four-file delta from the audit pin was reviewed: new direct targets/imports resolve, it contains no SQL/schema changes, and it does not repair or invalidate the findings below. Line evidence remains pinned to `3e91aef4` for reproducibility.


## Ranked retirement findings and drop slices

These are source-safe candidates at `3e91aef47f8a97731befc4a0f46ee7dc0ca36db5`, not a claim that production contains no externally retained consumers or data. No database, dump, installed catalog, live caller log, or applied frontier was inspected. Each SQL block is a proposed **new forward migration body**, not an executed migration. Reserve the filename only when implementing. The explicit PostgreSQL `RESTRICT` behavior and pre/post checks of all 118 stored historical seals plus the current capability catalog deliberately abort on unexpected dependencies. Preserve any required historical evidence before an actual contract release.

### S1 — P2: remove the unused cost-refusal ledger (one table, all columns)

**Finding.** The generic scheduler purge deleted the runtime that could call the cost-admission function. `ops/control-plane-retirement.md:8-18` explicitly lists `ops.cost_refusal` among seven tables with no repository runtime consumer. `migrations/0165_control_plane_cost_refusals.sql:9` defines its ten columns: `id`, `job_id`, `attempt`, `route_key`, `estimated_cost_usd`, `monthly_budget_usd`, `spent_usd`, `reserved_usd`, `reason`, `refused_at`. Its only retained writer is the dormant `ops.admit_job_cost(uuid,uuid,text,numeric)` at `:27-69`; the only retained reader is `ops.v_cost_refusal_metric` at `:71-77` and that function's idempotency read at `:59`. No invocation of these objects remains in `mcp-server/src`, `lib`, or `tools`; repository searches found no additional non-migration consumer. `ops.reserve_job_cost` is a different function: the current version raises a budget exception (`migrations/0906_dot_database_design.sql:250-255`) rather than writing this ledger.

**Consequence.** A table, view, executable authority function, and append-only trigger survive solely for a deleted execution path. No path to DoctorCRE app verbs, morning brief, Leads, deal rooms, intake, or vendors was found in this source revision.

**Exact fix.** Drop its sole function and view before dropping the table. The table has SELECT grants only (`0165:79-82`), so its removal does not change the SCAC relation-DML category. Its single `carr_jobs` SECURITY DEFINER EXECUTE capability must remain accounted for using the existing retirement contract established by `0890` and exercised by `0935`; the block below preserves the old sealed row, not the executable function. It never rewrites a historical migration or sealed registry row.

```sql
-- rollback: forward-only; restore this retired scheduler subsystem only in a new migration.
-- expand-contract: contract; the generic scheduler caller was removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired ledger; actual row count and lock window
-- must be checked in the implementation rehearsal, with lock_timeout bounding acquisition.
SET LOCAL lock_timeout = '5s';

DO $retire_cost_capability$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  retired jsonb;
  next_contract jsonb;
  expected_key text :=
    'db-function-acl:ops.admit_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute';
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT contract - 'effect_class' - 'owner_package' - 'implementation_state'
                  - 'classification_authorizing' - 'source_locator'
    INTO STRICT retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = expected_key;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = expected_key) THEN
    RAISE EXCEPTION 'Cost-admission capability already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb)
      || jsonb_build_array(retired));
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_cost_capability$;

DROP FUNCTION ops.admit_job_cost(uuid,uuid,text,numeric) RESTRICT;
DROP VIEW ops.v_cost_refusal_metric RESTRICT;
DROP TABLE ops.cost_refusal RESTRICT;

DO $verify_cost_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Cost retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_cost_retirement$;
```

This is statically reviewable SQL. It still needs the normal disposable PostgreSQL 18 reconstruction and migration-shadow check when implemented; it was not executed for this audit. The shared `ops.refuse_job_evidence_rewrite()` is deliberately retained because job receipts and other live tables use it.

### S2 — P2: remove the unused cognition cache-observation ledger and lease-bound cache doors

**Finding.** `ops.cognition_cache_observation` and its complete column set (`id`, `job_id`, `attempt`, `workflow_key`, `workflow_version`, `mode`, `cache_key`, `observation_kind`, `dependency_ref`, `observed_at`) originate at `migrations/0195_control_plane_cache_observations.sql:8-24`. Its only writers are `get_cognition_cache_for_job` (`:34-66`), `put_cognition_cache_for_job` (current definition `migrations/0906_dot_database_design.sql:289-333`) and `invalidate_cognition_cache_for_job` (`0906:335-366`). No current product path invokes these doors. The remaining explicit invocations are retired-behavior tests: `ops/dot-database-design-db-gate.py:359-376`. `ops/control-plane-retirement.md:14-16` explicitly identifies this dead runtime family.

**Consequence.** Runtime lease, cache-contract and immutability machinery survives for a removed scheduler; its tests can fail future cleanup while proving no retained product flow.

**Exact fix.** Remove those three test-only assertions with the eventual implementation; preserve the product-related tests in the same file. Drop the three doors and observation table, retaining the older cache table and older unbound cache functions for a separate slice. The observation table has only SELECT grants (`0195:146-153`); account for its three removed `carr_jobs` function grants with the existing seal retirement mechanism.

```sql
-- rollback: forward-only; restore retired cache observations only in a new migration.
-- expand-contract: contract; the generic scheduler caller was removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired observation ledger; rehearse its actual
-- lock window and row-retention disposition before delivery.
SET LOCAL lock_timeout = '5s';

DO $retire_cache_capabilities$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  expected_keys text[] := ARRAY[
    'db-function-acl:ops.get_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text):carr_jobs:execute',
    'db-function-acl:ops.put_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_cache_key text, p_cognition_key text, p_cognition_version integer, p_output_schema_version integer, p_proposal jsonb, p_dependency_refs text[], p_ttl_seconds integer):carr_jobs:execute',
    'db-function-acl:ops.invalidate_cognition_cache_for_job(p_job_id uuid, p_lease_token uuid, p_dependency_ref text):carr_jobs:execute'
  ];
  retired jsonb;
  next_contract jsonb;
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator'
                  ORDER BY ingress_key)
    INTO retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = ANY(expected_keys);
  IF coalesce(jsonb_array_length(retired),0) <> cardinality(expected_keys) THEN
    RAISE EXCEPTION 'Expected cache capabilities absent from the sealed registry';
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(expected_keys)) THEN
    RAISE EXCEPTION 'A cache capability is already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb) || retired);
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_cache_capabilities$;

DROP FUNCTION ops.get_cognition_cache_for_job(uuid,uuid,text) RESTRICT;
DROP FUNCTION ops.put_cognition_cache_for_job(
  uuid,uuid,text,text,integer,integer,jsonb,text[],integer) RESTRICT;
DROP FUNCTION ops.invalidate_cognition_cache_for_job(uuid,uuid,text) RESTRICT;
DROP TABLE ops.cognition_cache_observation RESTRICT;

DO $verify_cache_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Cache retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_cache_retirement$;
```

### S3 — P3: remove the unused cache evidence column

`ops.cognition_result_cache.evidence_refs` is declared with an empty-array default at `migrations/0149_control_plane_jobs.sql:185`. Its implicit default writes and whole-row transport remain, but no code consumes the value. Neither original cache writer (`migrations/0153_control_plane_resilience.sql:84-91`) nor the retained lease-bound writer (`migrations/0906_dot_database_design.sql:318-327`) writes it. The latter writers use the separate `dependency_refs` column instead; an omitted `evidence_refs` receives the empty-array default. The reader loads `%rowtype` at `migrations/0195_control_plane_cache_observations.sql:39,56` but consumes only cache identity, version, expiry, invalidation and proposal at `:57-66`; `evidence_refs` is never read from that record. All non-generated migration files containing the cache relation name were checked for this field; its declaration was the sole occurrence. No repository product path names the table. This is a no-consumer field rather than a literally untouched field: the retained SQL reader performs whole-row transport and the default still fills new rows. This is an optional smaller slice if retaining the otherwise orphaned cache table temporarily; skip it if removing that table in a later complete scheduler retirement.

```sql
-- rollback: forward-only; re-add only with a real producer and consumer.
-- expand-contract: contract; no retained writer or consumer uses cache evidence_refs.
-- lock-review: metadata-only DROP COLUMN; ACCESS EXCLUSIVE acquisition bounded to 5s.
SET LOCAL lock_timeout = '5s';
ALTER TABLE ops.cognition_result_cache DROP COLUMN evidence_refs RESTRICT;
```

No column-specific DML capability was found for this field; table-level grants remain unchanged. Production value retention remains unverified.

### S4 — P2: remove cost reservations after S1

`ops.cost_reservation` is also explicitly runtime-orphaned at `ops/control-plane-retirement.md:17`. All nine columns (`id`, `job_id`, `attempt`, `route_key`, `estimated_cost_usd`, `actual_cost_usd`, `state`, `created_at`, `settled_at`) belong to provider cost admission and settlement (`migrations/0153_control_plane_resilience.sql:15-30`). Its surviving SQL writers/readers are the three lease-bound APIs `reserve_job_cost` / `settle_job_cost` (`migrations/0906_dot_database_design.sql:217-287`) and `release_job_cost` (`migrations/0154_control_plane_cost_release.sql:8-26`), plus the admission API removed by S1. No retained product source invokes these APIs. The remaining calls in `ops/dot-database-design-db-gate.py:368-370,540-556` test the retired provider-budget runtime.

**Exact fix.** Apply after S1, remove just those retired test cases, and retire the three function capabilities before dropping the ledger. This table has SELECT-only runtime grants (`migrations/0153_control_plane_resilience.sql:171`); its outward job/provider FKs do not require dropping those parent tables. Its no-delete trigger is removed with the table; retain the shared trigger function.

```sql
-- rollback: forward-only; restore provider cost reservation only in a new migration.
-- expand-contract: contract; generic provider execution was removed in PR #2100.
-- lock-review: ACCESS EXCLUSIVE on one retired reservation ledger; rehearse row retention
-- and lock duration. This slice requires the preceding cost-refusal retirement.
SET LOCAL lock_timeout = '5s';

DO $retire_reservation_capabilities$
DECLARE
  original jsonb := ops.scac_current_grant_contract();
  expected_keys text[] := ARRAY[
    'db-function-acl:ops.reserve_job_cost(p_job_id uuid, p_lease_token uuid, p_route_key text, p_estimated_cost_usd numeric):carr_jobs:execute',
    'db-function-acl:ops.settle_job_cost(p_reservation_id uuid, p_job_id uuid, p_lease_token uuid, p_input_tokens integer, p_output_tokens integer, p_actual_cost_usd numeric):carr_jobs:execute',
    'db-function-acl:ops.release_job_cost(p_reservation_id uuid, p_job_id uuid, p_lease_token uuid):carr_jobs:execute'
  ];
  retired jsonb;
  next_contract jsonb;
BEGIN
  IF to_regprocedure('ops.admit_job_cost(uuid,uuid,text,numeric)') IS NOT NULL
     OR to_regclass('ops.cost_refusal') IS NOT NULL THEN
    RAISE EXCEPTION 'Apply cost-refusal retirement before reservation retirement';
  END IF;
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Current or historical SCAC seal is not valid';
  END IF;
  SELECT jsonb_agg(contract - 'effect_class' - 'owner_package' - 'implementation_state'
                           - 'classification_authorizing' - 'source_locator'
                  ORDER BY ingress_key)
    INTO retired
    FROM ops.scac_mutation_registry_entry
   WHERE registry_version = original->>'registry_version'
     AND ingress_key = ANY(expected_keys);
  IF coalesce(jsonb_array_length(retired),0) <> cardinality(expected_keys) THEN
    RAISE EXCEPTION 'Expected reservation capabilities absent from sealed registry';
  END IF;
  IF EXISTS (SELECT 1 FROM jsonb_array_elements(
      coalesce(original->'retired_secdef_execute','[]'::jsonb)) item
      WHERE item->>'ingress_key' = ANY(expected_keys)) THEN
    RAISE EXCEPTION 'A reservation capability is already retired';
  END IF;
  next_contract := jsonb_set(original, '{retired_secdef_execute}',
    coalesce(original->'retired_secdef_execute','[]'::jsonb) || retired);
  EXECUTE format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb '
    'LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(next_contract::text) || '::jsonb');
END
$retire_reservation_capabilities$;

DROP FUNCTION ops.reserve_job_cost(uuid,uuid,text,numeric) RESTRICT;
DROP FUNCTION ops.settle_job_cost(uuid,uuid,uuid,integer,integer,numeric) RESTRICT;
DROP FUNCTION ops.release_job_cost(uuid,uuid,uuid) RESTRICT;
DROP TABLE ops.cost_reservation RESTRICT;

DO $verify_reservation_retirement$
BEGIN
  IF EXISTS (SELECT 1 FROM generate_series(1,118) AS v(n)
      WHERE ops.scac_mutation_registry_seal_valid(
        'scac-mutation-registry.v' || n::text) IS NOT TRUE)
     OR ops.scac_mutation_catalog_v118_current() IS NOT TRUE THEN
    RAISE EXCEPTION 'Reservation retirement changed an unaccounted SCAC capability';
  END IF;
END
$verify_reservation_retirement$;
```

### D1 — P1: the three named legacy-schedule candidates are writerless, not safely droppable

The later schedule-contract purge (`bfc5e60b`, PR #2158) removes `lib/control_plane_scheduler_cutover.py` and the synchronization code that populated these projections. `tools/workflow-registry.py:33-116` now writes `ops.job_definition`, but no longer writes any of these three tables. The earlier statement in `ops/control-plane-retirement.md:20-23` predates that removal and needs this qualification.

| Table and all affected columns | Last source writer | Surviving SQL use / consequence |
| --- | --- | --- |
| `ops.legacy_schedule_surface_registry`: `workflow_key`, `workflow_version`, `surface_id`, `locator`, `scheduler_kind`, `duplicate_group` | Removed synchronization in PR #2158; test inserts remain in `ops/workflow-cutover-r02-local-pg-gate.py:355,445,481` | `ops.enqueue_job` reads the registry for dual-write and duplicate-group gating (`migrations/0604_doctorcre_r02_dual_write_gating.sql:127-178`); `ops.retire_workflow_cutover_plan` reads it at `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:812-821`. |
| `ops.legacy_schedule_provider_contract`: `surface_id`, `workflow_key`, `workflow_version`, `locator`, `cron_expression`, `timezone`, `definition_relpath`, `definition_sha256` | Removed synchronization in PR #2158 | `ops.record_claude_scheduler_observation` declares/reads its row at `migrations/0180_claude_scheduler_observation_receipt.sql:62,90`; current `ops.disable_legacy_schedule` validates it in `migrations/0184_notes_duplicate_schedule_cutover.sql:133-156,177-200`. |
| `ops.legacy_schedule_launchd_contract`: `surface_id`, `workflow_key`, `workflow_version`, `locator`, `repo_plist_relpath`, `installed_plist_name`, `program_arguments`, `plist_sha256`, `schedule_sha256`, `timezone` | Removed synchronization in PR #2158 | `ops.record_launchd_scheduler_observation` declares/reads its row at `migrations/0182_launchd_scheduler_observation_receipt.sql:44,72`; current disable function uses it alongside the provider contract in `0184`. |

The `disable-legacy-schedule` verb is still registered and calls the SQL door (`mcp-server/src/tools.js:6754-6789`), as are workflow cutover handlers (`mcp-server/src/workflow-cutover.v5.js:170-271`). Thus these are surviving machinery callers, not three empty SQL-only projections that can simply be dropped. Old projection rows can drift because their reconciler is gone. A new environment cannot populate them through the retained registry CLI. That can make the remaining observation/disable doors fail or operate against stale definitions. Product-level use is indirect: shared job execution still exists for on-demand engineering, and health/schedule views read their observation receipts. No production usage claim is made.

**Fix slice.** First remove or replace the remaining schedule approval verbs and the dual-write branches in the shared enqueue function, then remove obsolete tests and native observation contracts. Do not drop `ops.job_definition`, `ops.job`, `ops.job_attempt`, `ops.job_receipt`, or all observation receipts merely because generic recurrence was removed. The final dependency-ordered table fragment is:

```sql
-- This is a deferred contract fragment, NOT a runnable standalone migration.
-- Prerequisite: replace/remove every SQL caller and reconcile SCAC EXECUTE grants.
DROP TABLE ops.legacy_schedule_provider_contract RESTRICT;
DROP TABLE ops.legacy_schedule_launchd_contract RESTRICT;
DROP TABLE ops.legacy_schedule_surface_registry RESTRICT;
```

No exact complete safe migration is asserted for this group: the required product/retirement decision is which retained administrative verbs and shared enqueue semantics remain. A naked three-table DROP would leave installed PL/pgSQL bodies broken even when PostgreSQL does not record their text references in `pg_depend`.

### D2 — P2: the rest of the dead generic scheduler requires one coherent retirement

After S1/S2/S4, the remaining four explicit runtime orphans are `ops.provider_route`, `ops.provider_observation`, `ops.cognition_job`, and `ops.cognition_result_cache`. All their columns are scheduler-only in retained source; SQL still reads/writes them. Definitions: `migrations/0149_control_plane_jobs.sql:7-65,179-192`; `migrations/0153_control_plane_resilience.sql:9-34`. Retained wrappers: `0153:37-180`, `0154:8-26`; current cost reserve/settle at `0906:217-287`. The provider route view expands `r.*` (`0149:40`), so individual route columns cannot be declared unread solely by missing source field names. The lease-bound cache and cost tests remain at `ops/dot-database-design-db-gate.py:359-376,540-556`.

After S1/S2/S4, a complete later slice must drop the old unbound cache functions; provider selection/observation functions; the provider-route view; and then the child tables before `provider_route`/`cognition_job`. Unlike S1/S2, these tables carry runtime DML grants (`0149:507-509`), so the fixed relation-DML digest in the current seal changes. The existing `retired_secdef_execute` mechanism alone is insufficient. Do not claim a runnable drop until a forward successor or a narrowly reviewed relation-grant retirement extension is generated and verified. Keep their whole column sets as conditional candidates, not individually “safe” fields.

### D3 — P2: approval triggers still attached to retained tables

| Surviving constraint / trigger | Evidence and current scope | Exact bounded next action |
| --- | --- | --- |
| `job_definition_cutover_requires_evidence` | `migrations/0149_control_plane_jobs.sql:441-459` requires accepted shadow and canary rows before setting `legacy_disabled_at`. It is purely the old schedule-cutover ceremony; no later DROP was found. The remaining disable verb still activates it. | In D1's caller-removal slice: `DROP TRIGGER job_definition_cutover_requires_evidence ON ops.job_definition; DROP FUNCTION ops.require_cutover_evidence() RESTRICT;`. Do not drop `legacy_disabled_at`: Atlas reads it (`mcp-server/src/atlas-inventory-graph.v5.js:285-295`) and health reads it (`tools/health-check.py:602,842`). |
| `staging_release_approval_is_typed` and `a_staging_release_plan_revision_invalidates_approval` | `migrations/0219_staging_release_approval_receipt.sql:34-83` still requires a Joe-owned typed receipt for promoted staging records. Current GitHub deployment no longer goes through the deleted staging approval gate (PR #2153). SQL approval function and historical receipt FKs remain. | Defer as one release-record contract slice: remove these two triggers and their trigger functions `ops.staging_release_approval_is_typed()` / `ops.staging_release_plan_revision_invalidates_approval()` only after replacing the approved-state contract and retiring `ops.approve_staging_release`. Preserve historical receipt rows until retention is decided. |
| `approved_by_actor='joe'` and approval-expiry CHECKs on `ops.staging_release_approval_receipt` | `migrations/0219_staging_release_approval_receipt.sql:12-18`; only historical staging approval machinery owns these semantics. | Remove with the table's complete retirement, not by weakening its historical evidence constraints in place. |
| `legacy_schedule_disable_receipt_append_only`, `legacy_schedule_observation_receipt_append_only`, and sibling receipt FKs/CHECK | `migrations/0176_legacy_schedule_disable_receipt.sql:37-45`, `0180:42-44`, `0184:12-29`. They preserve old approval observations but observations remain read by `mcp-server/src/schedule-board.js:34` and `tools/health-check.py:395`. | Retain with evidence tables until D1 replaces the remaining readers; dropping only the immutability controls yields no schema cleanup and weakens historical data. |

Do not classify `deployment_requires_a_live_approval` or `an_approved_release_names_its_approval` by their old names alone: migration `0540_release_readiness_without_repeat_approval.sql:10-23,363` explicitly broadened them to technical readiness without repeat human approval. Likewise, the heavy-build ready-plan trigger was already dropped at `migrations/0890_retire_heavy_build_runtime.sql:6`; reporting it as surviving would be false. Rule approval tables still have living rule lifecycle verbs and are not orphaned merely because local acceptance harnesses were removed.

## Migration and historical-seal constraints

1. `AGENTS.md:51-85` says authorized work proceeds without CARR plan/approval ceremony, but technical checks remain. `AGENTS.md:136-159` requires an isolated tree, and `AGENTS.md:196-209` makes `ops/ci.sh` the check entry point. Its explicit production guidance at `:304-307` delegates deployment to the workflow and migration runner. **AGENTS.md does not itself enumerate the SQL seal versions**; those constraints are in the migrations/runner below. This audit's no-production and report-only instructions take precedence over generic delivery instructions.
2. `migrations/README.md:39-41,99-108` and `tools/migrate.py:63-67,462-484` make applied SQL immutable. Fix stale comments and removed script paths by forward migration or explanatory current documentation; never edit old SQL to erase references. New contract migrations need rollback, expand-contract and lock-review headers (`migrations/README.md:55-68`). The number is reserved at implementation time (`:131-136`); this report reserves none.
3. The supported rebuild uses a PostgreSQL 18 snapshot and pending migrations, not an empty replay (`migrations/README.md:8-35,69-74`). The audit intentionally did not open that dump. Therefore static inventory is the schema implied by source, not verified reconstruction of the deployed catalog or every seed-dependent DO branch.
4. The latest historical SQL registry is **`scac-mutation-registry.v118`** (`migrations/0873_self_closing_records_scac_successor.sql:21,530-533`). Historical versions **v1 through v118** are checked by `ops.scac_policy_epoch_snapshot()` (`0873:583-625`); v1–v3 use the generic seal validator and v4–v118 use versioned seal functions. Preserve the old `scac_mutation_registry_version` / `entry` rows, version checks and policy-epoch chain. Do not delete old SQL seal functions because current generated JavaScript advertises only today's handler registry.
5. `migrations/0890_retire_heavy_build_runtime.sql:558-588` is the current catalog comparator. It accounts for removed SECURITY DEFINER EXECUTE capabilities using `ops.scac_current_grant_contract()->'retired_secdef_execute'`, but still compares the relation-DML category to 321 exact historical grant rows and its digest. `migrations/0935_retire_jev_gateway.sql:9-22` is the current precedent for a caller-removal DROP accompanied by this grant-retirement contract. S1/S2/S4 preserve that mechanism. S3 changes no table/column grants.
6. The ledger insert triggers a deferred policy-epoch refresh (`migrations/0455_siep12_policy_epoch.sql:452`). `tools/migrate.py:105-115` explains why schema change and matching capability change must commit together; listed atomic groups cannot be split with `--through`. Any new successor needed by D2 must share a transaction with its DDL. Never disable that trigger just to make a drop pass.

The existing catalog hardening check distinguishes current capabilities from stored history: `ops/definer-hardening-local-pg-gate.py:198-212` checks the v118 live catalog and then validates every historical stored seal separately. Dropping a runtime object does not itself erase a historical seal; changing its live capability set without the corresponding forward retirement contract breaks the current comparator.


## Product query and index audit

Scope: static source review at `3e91aef47f8a97731befc4a0f46ee7dc0ca36db5`. No database connection, schema dump, `EXPLAIN`, row-count measurement or latency measurement was used. “Hot path” below means an ordinary product read or recurring product routine, not measured traffic. Index absence was checked across all migration files, including multiline index declarations, table/column constraints, later index drops, and relation renames; this is a claim about the migration-derived schema, not unmanaged live indexes. New index names below have no matching declaration in these migrations.

The implementation slices below add access paths only. They do not drop data, rewrite historical migrations, change product semantics, or remove old indexes. Ordinary `CREATE INDEX` is deliberate: `tools/migrate.py:4` and `:487` require runner-owned transactions, and `CREATE INDEX CONCURRENTLY` cannot run within one. Each slice needs an unused forward migration number and the repository's normal migration checks. Use the existing lock/statement timeouts (`tools/migrate.py:444`, `:769`) and measure on a disposable fixture before rollout. Do not infer that a table is small from the outdated size comment at `tools/migrate.py:423`. No migration below was executed in this audit.

### Q1 — P2: Leads correspondence and stage-history reads lack matching access paths

**Product caller.** `lead-board` delegates its Leads workspace to `readLeadWorkspace` at `mcp-server/src/tools.js:3685`. That read builds the whole board and optionally the selected detail in `mcp-server/src/lead-workspace.js:11`. Every board row obtains its latest stage transition through a lateral query at `migrations/0847_lead_score_owner_reader_repair.sql:73`; detail fetches 100 correspondence rows and 100 stage changes at `migrations/0892_lead_stage_transition_proof.sql:23`.

**Exact query fragments.**

```sql
-- migrations/0892_lead_stage_transition_proof.sql:25
select id,lead_id,kind,occurred_at,summary,detail,source,connected
from activity where lead_id=b.id
order by occurred_at desc,id desc limit 100;

-- migrations/0847_lead_score_owner_reader_repair.sql:78
select ... from v_lead_stage_transition e
where e.lead_id=b.id order by e.mutation_order desc limit 1;
-- The same view is read with LIMIT 100 in 0892:31.
-- Its defining base predicate is 0892:13:
-- from event e where e.subject_type='lead' and e.new_value->>'stage' is not null
```

**Consequence and evidence.** There is no leading `activity.lead_id` index. `activity_deal_idx` indexes `deal_id` (`0001_init.sql:211`); `activity_vendor_contact_order` indexes `vendor_id` (`0824_vendor_relationship_contract.sql:23`). `activity_subject_timeline` indexes a CASE expression plus COALESCE (`0906_dot_database_design.sql:729`), which the direct `lead_id = b.id` query does not express. The event subject index is `(subject_type,subject_id,occurred_at)` (`0001_init.sql:174`), not `mutation_order`. It can filter lead events but cannot produce the requested stage mutation order. The board can repeatedly filter/sort each lead's historical events; detail can scan unrelated activities.

**Exact forward migration, slice Q1.**

```sql
-- rollback: forward-only; a successor may drop these performance-only indexes.
-- expand-contract: expand; no data or read contract changes.
-- lock-review: ordinary index builds take SHARE locks; retain runner timeouts.
create index activity_lead_timeline_idx
  on public.activity (lead_id, occurred_at desc, id desc)
  where lead_id is not null;
create index event_lead_stage_mutation_idx
  on public.event (subject_id, mutation_order desc)
  where subject_type = 'lead' and new_value->>'stage' is not null;
```

Validate on fixtures containing many unrelated activities and many non-stage events, checking both latest-transition and history queries and their plans. `event.mutation_order` is a generated identity from `0845_lead_archived_stage.sql:8`; no backfill or event rewrite is needed.

### Q2 — P2: Home agenda and deal timelines scan child tables by deal without matching indexes

**Product caller.** `read-home-agenda` joins dates and actions to active deals at `mcp-server/src/tools.js:9305`; deal detail reads those same projections at `:9223` and `:9232`. The deal board also aggregates checklist items per deal at `:3532`.

**Exact queries.**

```sql
-- tools.js:9223
select id,kind,to_jsonb(due_on)#>>'{}' as due_on,note,source,status
from v_deal_room_critical_date where deal_id=$1 order by due_on,id;

-- tools.js:9232 (projection shortened; WHERE/ORDER unchanged)
select n.id,n.owner,n.description,n.item_title as title,n.status
from v_deal_room_action n where n.deal_id=$1
order by n.created_order,n.id;

-- Home's corresponding open-action subset: tools.js:9312
select ... from v_deal_room_action a join scoped s on s.id=a.deal_id
where a.status='open'
order by a.deal_id,a.updated_at desc,a.id desc limit 10001;
```

`v_deal_room_critical_date` is a direct projection of `critical_date` (`0079_deal_room_api.sql:84`). `critical_date` has only its UUID primary key in its declaration (`0001_init.sql:472`) and no later added index. `v_deal_room_action` is a `UNION ALL` of `next_action` and `capture_post_call_action` (`0936_deal_alert_items.sql:20`). The latter has only primary key and unique candidate indexes (`0094_call_mode_post_call.sql:63`), neither led by `deal_id`. `next_action`'s two current indexes cover disjoint partial subsets: open untitled actions and titled deal items (`0936:14`). Neither covers the full per-deal history requested by deal detail. Do not call `one_open_next_action_per_owner` a general history index.

**Consequence.** Per-deal timelines lack a cheap way to isolate all dates and both action histories. The bounded Home snapshot still has to find all matching rows before its output limit; bounding the response does not create an access path. The indexes below support deal filtering and the detail's native ordering. The separate Home `updated_at` sort may remain; the date detail's `to_jsonb` alias can also retain a final expression sort.

**Exact forward migration, slice Q2.**

```sql
-- rollback: forward-only; a successor may drop these performance-only indexes.
-- expand-contract: expand; no data or read contract changes.
-- lock-review: ordinary index builds take SHARE locks; retain runner timeouts.
create index critical_date_deal_due_idx
  on public.critical_date (deal_id, due_on, id);
create index next_action_deal_history_idx
  on public.next_action (subject_id, created_at, id)
  where subject_type = 'deal';
create index capture_post_call_action_deal_history_idx
  on public.capture_post_call_action (deal_id, accepted_at, id);
```

Keep the existing unique indexes: they enforce product invariants, not just speed. Validate with mixed open/done/dropped and titled/untitled actions in both stores; ensure returned history and checklist order remain identical.

### Q3 — P2: Morning triage has no date/status access path on its accumulating queues

**Product caller.** `morning-brief` invokes `today-triage` at `mcp-server/src/tools.js:3371`. The handler runs `select * from v_today_triage order by due_on nulls last limit 50` (`:3337`). The latest definition is `migrations/0094_call_mode_post_call.sql:116`.

**Exact base predicates from that view.**

```sql
-- 0094:125; projection and reference-name joins omitted
select ... from next_action na
where na.status='open' and na.due_on is not null and na.due_on<=current_date
  and (na.hold_until is null or na.hold_until<=current_date);
-- 0094:135
select ... from capture_post_call_action pca
where pca.status='open' and pca.due_on is not null and pca.due_on<=current_date;
-- 0094:143
select ... from critical_date cd
where cd.status='open' and cd.due_on<=(current_date+14);
-- 0094:150
select ... from ingest_inbox i where i.status='new';
```

**Consequence and evidence.** Existing action indexes are subject-led, not due-date-led; Q2's proposed indexes are also deal-led. `ingest_inbox` has primary key plus `(source,external_id)` uniqueness (`0001_init.sql:931`), not a queue-state index. Closed/triaged history accumulates while every morning read needs the remaining open/new subset. The input predicates lack matching index prefixes. The `received_at::date` projection means the inbox index below is for locating new rows; it does not promise to eliminate the final date sort.

**Exact forward migration, slice Q3.**

```sql
-- rollback: forward-only; a successor may drop these performance-only indexes.
-- expand-contract: expand; no data or read contract changes.
-- lock-review: ordinary index builds take SHARE locks; retain runner timeouts.
create index next_action_open_due_idx
  on public.next_action (due_on, id)
  where status = 'open' and due_on is not null;
create index capture_post_call_action_open_due_idx
  on public.capture_post_call_action (due_on, id)
  where status = 'open' and due_on is not null;
create index critical_date_open_due_idx
  on public.critical_date (due_on, id)
  where status = 'open';
create index ingest_inbox_new_received_idx
  on public.ingest_inbox (received_at, id)
  where status = 'new';
```

`current_date` intentionally stays in the query, not the index predicate: it is not immutable and is unsuitable for a partial-index definition. Preserve `hold_until` evaluation. Validate at contrasting open/closed ratios; if a table is mostly open, a sequential scan can still be the right plan. No measured speedup is claimed.

### Q4 — P2: Vendor relationship projection repeatedly resolves party-to-vendor and party-to-referral rows without party-led indexes

**Product caller.** Vendor directory and detail include `vendorRelationshipJoin` (`mcp-server/src/workspace-business-read.js:386`, `:617`, `:696`). Its coverage subquery is executed for a vendor party (`mcp-server/src/vendor-relationship.js:106`), and `exactDealAssociations` joins vendor JSON evidence and referral rows to that party (`relationship-network.js:10`, `vendor-relationship.js:108`).

**Exact query fragments.**

```sql
-- vendor-relationship.js:106
select case when bool_and(s.deal_history_verified_at is not null)
       then min(s.deal_history_verified_at) end oldest
from public.vendor s
where s.party_id=v.party_id and s.merged_into is null;

-- relationship-network.js:15; joined as e.party_id=v.party_id by caller
select r.referred_by,r.deal_id,'referred',
       coalesce(r.occurred_on::timestamp at time zone 'UTC',r.created_at),r.note
from public.party_link_deal r;

-- relationship-network.js:104, product referral write lock lookup
select id from public.vendor
where party_id=$1 and merged_into is null order by id for update;
```

**Consequence and evidence.** Vendor's unique reference and relationship/category/merged indexes do not lead with `party_id` (`0001_init.sql:344`, `0047_vendor_relationship.sql:88`, `0050_vendor_category.sql:69`, `0069_enrichment_write_gap.sql:52`). `party_link_deal` has only `(link_id,deal_id)` primary key (`0839_relationship_deal_links.sql:14`). A directory that includes relationship coverage and counts lacks indexes for the party-specific subqueries. Planner decorrelation may reduce repeated work, so quantify the final generated directory statement before calling this a measured N+1 cost.

**Exact forward migration, slice Q4.**

```sql
-- rollback: forward-only; a successor may drop these performance-only indexes.
-- expand-contract: expand; no data or read contract changes.
-- lock-review: ordinary index builds take SHARE locks; retain runner timeouts.
create index vendor_live_party_idx
  on public.vendor (party_id, id)
  where merged_into is null;
create index party_link_deal_referrer_idx
  on public.party_link_deal (referred_by, deal_id);
```

Validate both ordinary vendor directory/detail reads and referral invalidation with multiple vendor rows per party. Do not create a unique index on `vendor.party_id`; the current product explicitly supports several rows and aggregates their coverage.

### Q5 — P3: Clients workspace and Leads lifecycle do per-client deal lookups without a client-led deal index

**Product callers and exact queries.** The Clients workspace selects `(select max(d.closed_on) from public.deal d where d.client_id=c.id)` for each row at `mcp-server/src/workspace-business-read.js:615`. Leads lifecycle asks `exists(select 1 from deal d where d.client_id=l.client_id)` at `migrations/0845_lead_archived_stage.sql:33`. Contact logging resolves the client's active deals through `select id,name from deal where client_id=$1 and outcome is null order by created_at desc` at `mcp-server/src/tools.js:5644`.

The initial `deal.client_id` is a foreign key, which does not create an index on the referencing column (`0001_init.sql:403`). No later explicit index or unique constraint starts with `deal.client_id`. Multiple per-client lookups therefore lack a matching access path.

**Exact forward migration, slice Q5.**

```sql
-- rollback: forward-only; a successor may drop this performance-only index.
-- expand-contract: expand; no data or read contract changes.
-- lock-review: ordinary index build takes SHARE lock; retain runner timeouts.
create index deal_client_closed_idx
  on public.deal (client_id, closed_on desc nulls last);
```

This supports client filtering, MAX date lookup, and lifecycle existence. It does not eliminate the separate active-deal `created_at` sort; add no second index for that without plan/volume evidence.

### Verified clean or explicitly retained

- Vendor contact history has an exact `(vendor_id,occurred_at desc,id desc)` partial index in `0824_vendor_relationship_contract.sql:23`, matching the vendor queries at `vendor-relationship.js:100`–`:103`. Do not add a duplicate.
- Introduction graph endpoint predicates have `party_link_from_idx` and `party_link_to_idx` (`0001_init.sql:388`) plus `party_link_via_idx` (`0051_intro_graph.sql:67`). The vendor introductions OR predicate at `vendor-relationship.js:104` has indexable branches. No “missing intro index” finding.
- Deal note history has `deal_note_thread_idx(deal_id,created_at desc,id desc)` (`0079_deal_room_api.sql:35`), matching the timeline's note query at `tools.js:9219`.
- Deal event cursor polling has the correctly ordered partial `event_deal_cursor_idx(recorded_at,id) where subject_type='deal'` (`0079_deal_room_api.sql:58`) and the cursor query uses both columns at `mcp-server/src/dealroom.js:76`. Single-deal event detail may still benefit from a different subject-led order, but it already has subject filtering and is not claimed as wholly unindexed.
- The recent table deletion `ops.jev_call_receipt` (`0935_retire_jev_gateway.sql:12`) has no current JS/Python consumer in `mcp-server/src`, `lib`, or `tools`; the remaining hits in `tools/migrate.py:147` and `:206` are historical migration-pair allowlists. They are not executable product reads of the dropped table. This narrow result is not proof that every dynamic relation reference is valid.
- Approval terminology does not prove schema orphaning: morning brief still invokes `ops.v5_a05_assurance_cadence_batch` (`tools.js:3451`) and `governance-queue` (`:3460`). These are product readers until that composition changes. `lead_stage_move` and `lead_contact_draft` still serve the Leads automation snapshot (`mcp-server/src/lead-automation.js:107`, `:112`) and writes (`:131`, `:150`). Their approval fields/constraints need semantic caller analysis rather than name-based deletion.

No query was executed; performance validation and unmanaged live-index discovery remain deliberately outside this report-only, no-database-access audit.


## Report validation and design/debt pass

Only this report is proposed for delivery. SQL blocks are recommendations, not migration files. Source citations were checked against the pinned tree; report whitespace and staged-only credential-pattern checks passed. Independent review corrected overbroad orphan claims, preserved current product readers, and checked the proposed SQL dependency closures. All 12 proposed index statements, four complete drop-migration bodies, the deferred schedule DROP fragment, and six PL/pgSQL DO bodies parse with the PostgreSQL parser. Parsing is not database execution or a migration acceptance result.

- **Codebase-design:** kept fixes at existing interfaces: repair one shared hook initializer, remove exclusive retired callers with their tests, and preserve shared product modules. No new runtime abstraction is introduced by the reports.
- **Zero-tech-debt:** grouped removals into complete source/SQL slices rather than restoring purged wrappers or keeping tests as their sole consumers. Explicitly retained live product/record readers and immutable historical evidence.


## Appendix: source-defined table and column inventory

This appendix enumerates **504 surviving source-defined tables and 5,620 columns**, reconstructed in filename order from all **588** migration SQL files at `3e91aef47f8a97731befc4a0f46ee7dc0ca36db5`. It uses no live database and does not read `db/schema.sql` or any other dump. `tools/migrate.py:11` warns that historical migrations are not a supported empty-database rebuild base; therefore these counts describe the repository declarations, not a verified deployed schema.

Every column appears exactly once under its table. Columns sharing the same evidence are grouped. Evidence IDs resolve to current source locations in the register below. **An empty cell is not a safe-drop decision.** It means no attributable use was found by this static model; dynamic SQL, runtime role access and external callers remain unknown. Only separately reviewed findings in the ranked sections justify a proposed drop.

Legend: **W** = explicit INSERT/UPDATE assignment; **R** = explicit column, `SELECT *`, row alias or JSON row read; **R?** = unqualified column shared by multiple joined relations (possible read); **D** = omitted-column INSERT default; **T** = assignment to `NEW.column` by a statically reconstructed trigger. DELETE callers are listed at table level because they remove rows rather than assign individual columns. Migration seed/backfill DML is not a surviving application writer. A declared default alone is not a business writer.

**Product** is positive evidence only: **yes** means a column read/write is on a statically traced source path in named Leads, deal rooms, intake, vendors, morning brief/Home or related DoctorCRE app modules/verbs; **unknown** means the model did not establish that path. SQL routines/views can be retained even when no current source call was found. The source-call flag in the evidence register distinguishes that case. Shared generic MCP helpers and record exports mean an absent product flag cannot establish an orphan.

Analysis bounds: parsed PostgreSQL AST for literal CREATE/ALTER/DROP/RENAME TABLE and column declarations, plus literal DDL inside PL/pgSQL DO blocks; **300** non-test `.py/.js/.mjs/.ts/.sql` files in `mcp-server/src`, `lib` and `tools`; SQL string literals, stored routine bodies, views, static trigger assignments, and manually checked dynamic update allowlists for deal/vendor/lead/party and literal `versionGuard` table arguments. Dynamic template SQL is carried as unresolved table-level evidence, applicable to every column. Tests, secret-named files and dumps were excluded. This is a syntactic lineage catalogue rather than an execution-frequency or query-plan measurement.

Known model limits: routine identities are collapsed by schema/name, so overload replacement/drop resolution needs manual confirmation; dynamic SQL/function names and dynamic column lists cannot be fully resolved; alias attribution spans a statement rather than every nested scope. Dynamic DO `EXECUTE` trigger creation/renaming (for example `migrations/0467_siep18_atomic_db_monitor_grants.sql:795`, `migrations/0890_retire_heavy_build_runtime.sql:411`) and dynamic view rewrites (`migrations/0756_dot_reader.sql:103`) are not expanded. Trigger and view dependencies shown here are therefore conservative observations, not an exhaustive proof of absence. Default/trigger writers and whole-row readers are deliberately retained instead of calling their columns unused.

### `ops.accepted_eval_golden_membership`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:432`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E688. **Table readers:** E340, E688.
**Additional lexical/dependency evidence:** E340, E688.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | E340 | E688 | — | unknown |
| `candidate_id` | E688 | E340 | — | — | — | unknown |
| `target_golden_set_ref`, `accepted_by_actor_id` | E688 | — | — | — | — | unknown |
| `accepted_at` | — | — | — | E688 | — | unknown |

### `ops.activation_reliability_telemetry`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:345`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E374. **Table readers:** E337, E374.
**Additional lexical/dependency evidence:** E337, E374.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E374 | — | unknown |
| `organization_tenant_id` | E374 | — | — | — | — | unknown |
| `attempt_receipt_id` | E374 | E337, E374 | — | — | — | unknown |
| `signal_id`, `trigger_ref`, `consumer_ref`, `enforcement`, `owner_ref`, `remedy_ref`, `verification_ref`, `auto_clear`, `state` | E374 | E337 | — | — | — | unknown |

### `ops.assurance_evidence_extension`

**Definition:** `migrations/0451_assurance_evidence_acceptance_persistence.sql:48`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E369. **Table readers:** E369, E372, E373.
**Unresolved SQL affecting any column:** E369. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E372, E373.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E369, E372, E373 | — | E369 | — | unknown |
| `receipt_id`, `manifest_id`, `evidence_digest`, `evidence`, `idempotency_key` | E369 | E369, E372, E373 | — | — | — | unknown |
| `created_at` | E369 | E369, E372, E373 | — | declared; caller unknown | — | unknown |

### `ops.assurance_execution_manifest`

**Definition:** `migrations/0451_assurance_evidence_acceptance_persistence.sql:11`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E370. **Table readers:** E45, E369, E370, E372, E373.
**Additional lexical/dependency evidence:** E45, E369, E370, E372, E373.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E45, E369, E370, E372, E373 | — | E370 | — | unknown |
| `organization_tenant_id`, `work_request_id`, `accepted_plan_id`, `slice_plan_id`, `slice_ref`, `lease_id`, `fencing_generation`, `repository_stage`, `repository_commit_sha`, `repository_tree_sha`, `compiler_id`, `compiler_version`, `input_digest`, `manifest_hash`, `applicable_rule_snapshot_digest`, `coordination_snapshot_digest`, `snapshot_valid_until`, `applicable_rules`, `coordination_snapshot`, `compiler_input`, `ownership_contract_digest`, `manifest`, `idempotency_key` | E370 | E45, E369, E370, E372, E373 | — | — | — | unknown |
| `created_at` | E370 | E45, E369, E370, E372, E373 | — | declared; caller unknown | — | unknown |

### `ops.assurance_health_evidence`

**Definition:** `migrations/0717_assurance_health_evidence_store.sql:78`. **Product path:** positive static evidence (column detail below).

**Table writers:** E371. **Table readers:** E43, E371.
**Unresolved SQL affecting any column:** E719. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E43, E371.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `recorded_at` | — | E43, E371 | — | E371 | — | unknown |
| `record_sequence` | — | E43, E371 | — | — | — | unknown |
| `tenant`, `workflow_key`, `workflow_version`, `work_request_id`, `layer`, `basis`, `status`, `subject_ref`, `evaluator_actor_id`, `evaluator_slug`, `evidence_ref`, `evidence_digest`, `observed_at`, `expires_at`, `detail`, `incident_refs`, `recovery_refs`, `receipt_digest`, `idempotency_key` | E371 | E43, E371 | — | — | — | unknown |

### `ops.assurance_owner_acceptance_fact`

**Definition:** `migrations/0451_assurance_evidence_acceptance_persistence.sql:69`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E372. **Table readers:** E372.
**Additional lexical/dependency evidence:** E372.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E372 | — | E372 | — | unknown |
| `organization_tenant_id`, `review_manifest_id`, `evidence_id`, `owner_actor_id`, `owner_actor_slug`, `owner_session_ref`, `owner_host_ref`, `decision`, `acceptance_digest`, `acceptance`, `idempotency_key` | E372 | E372 | — | — | — | unknown |
| `created_at` | E372 | E372 | — | declared; caller unknown | — | unknown |

### `ops.assurance_review_extension`

**Definition:** `migrations/0451_assurance_evidence_acceptance_persistence.sql:58`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E373. **Table readers:** E373.
**Unresolved SQL affecting any column:** E373. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E373 | — | E373 | — | unknown |
| `reviewer_fact_id`, `review_manifest_id`, `evidence_id`, `review_digest`, `review`, `idempotency_key` | E373 | E373 | — | — | — | unknown |
| `created_at` | E373 | E373 | — | declared; caller unknown | — | unknown |

### `ops.attempt_receipt`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:329`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E374. **Table readers:** E50, E326, E337, E340, E374, E688.
**Additional lexical/dependency evidence:** E50, E326, E337, E340, E374, E688.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E50, E326, E337, E340, E374, E688 | E340 | E374 | — | unknown |
| `idempotency_key` | E374 | E50, E326, E337, E374 | — | — | — | unknown |
| `organization_tenant_id` | E374 | E50, E326, E337, E374, E1514 | E340 | — | — | unknown |
| `work_request_id` | E374 | E50, E326, E337, E374, E688, E1514 | — | — | — | unknown |
| `plan_hash` | E374 | E50, E326, E337, E374, E1514 | — | — | — | unknown |
| `envelope_digest`, `attempt_id`, `activation_binding_id` | E374 | E50, E326, E337, E340, E374, E1514 | — | — | — | unknown |
| `execution_envelope_id` | E374 | E50, E326, E337, E374, E1514, E1515 | — | — | — | unknown |
| `receipt` | E374 | E50, E326, E337, E340, E374, E1514, E1515 | — | — | — | unknown |
| `created_at` | — | E50, E326, E337, E340, E374 | — | E374 | — | unknown |

### `ops.attempt_receipt_evaluation_attestation`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:365`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E50. **Table readers:** E50, E337.
**Unresolved SQL affecting any column:** E337. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E50.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E50 | — | E50 | — | unknown |
| `organization_tenant_id`, `dimension_refs`, `evaluator_policy_digest`, `lower_bound_ref`, `outcome_feedback_ref`, `outcome_feedback_hash`, `evidence_refs`, `attested_by_actor_id`, `idempotency_key` | E50 | E50 | — | — | — | unknown |
| `attempt_receipt_id`, `evaluator_kind`, `status` | E50 | E50, E337 | — | — | — | unknown |
| `check_ref` | E50 | E50, E337 | — | declared; caller unknown | — | unknown |
| `evaluator_ref`, `rubric_ref`, `evaluator_version`, `evaluator_digest`, `confidence`, `held_out_case_count`, `calibration_refs`, `independent` | E50 | E50 | — | declared; caller unknown | — | unknown |

### `ops.authority_receipt`

**Definition:** `migrations/0148_control_plane_admission.sql:81`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E11, E15, E17, E18, E34, E35, E127, E381, E429, E430, E1292, E1489. **Table readers:** E11, E15, E17, E18, E32, E34, E35, E127, E381, E429, E430, E704, E705, E707, E1292, E1489.
**Unresolved SQL affecting any column:** E12, E35, E422. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E11, E15, E17, E18, E32, E34, E127, E381, E429, E430, E704, E705, E707.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E11, E127, E381, E704, E705, E707 | — | E11, E15, E17, E18, E34, E35, E127, E381, E429, E430, E1292, E1489 | — | unknown |
| `idempotency_key` | E11, E15, E17, E18, E34, E35, E127, E381, E429, E430, E1292, E1489 | E11, E32, E127, E381 | — | — | — | unknown |
| `kind`, `subject_type`, `subject_id`, `actor_id`, `contract_hash` | E11, E15, E17, E18, E34, E35, E127, E381, E429, E430, E1292, E1489 | E11, E32, E127, E381, E704 | — | — | — | unknown |
| `decision` | E11, E15, E17, E18, E34, E35, E127, E381, E429, E430, E1292, E1489 | E11, E127, E381, E704, E705, E707 | — | — | — | unknown |
| `evidence_refs` | E11, E15, E17, E18, E34, E35, E127, E381, E429, E430, E1292, E1489 | — | — | declared; caller unknown | — | unknown |
| `created_at` | — | — | — | E11, E15, E17, E18, E34, E35, E127, E381, E429, E430, E1292, E1489 | — | unknown |

### `ops.benchmark_manifest_acceptance_receipt`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:567`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E53. **Table readers:** E53, E56, E58, E186.
**Unresolved SQL affecting any column:** E55. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E53, E56, E58, E186.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E53, E58, E186 | — | E53 | — | unknown |
| `draft_id` | E53 | E53, E56, E58, E186, E1517, E1558 | — | — | — | unknown |
| `benchmark_ref`, `review_id` | E53 | E53, E58, E186, E1517, E1558 | — | — | — | unknown |
| `idempotency_key`, `gate_id`, `producer_step_ref`, `status` | E53 | E53, E58, E186 | — | — | — | unknown |
| `accepted_payload_digest`, `portfolio_ref`, `portfolio_revision_id`, `portfolio_accepted_digest`, `portfolio_accepted_at`, `gate_zero_step_ref`, `gate_zero_outcome_digest`, `gate_zero_observed_at` | E53 | E53, E58, E186, E1517 | — | — | — | unknown |
| `accepted_by_actor_id` | E53 | E53, E58, E186, E1517, E1558 | — | — | E1517 | unknown |
| `accepted_at` | — | E53, E58, E186, E1517 | — | E53 | — | unknown |
| `accepted_by_session_ref` | — | E53, E58, E186, E1558 | — | — | E1558 | unknown |

### `ops.benchmark_manifest_browser`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:427`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E65. **Table readers:** E59, E63, E65.
**Unresolved SQL affecting any column:** E59. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E63, E65.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E65 | — | unknown |
| `draft_id` | E65 | E63 | E59 | — | — | unknown |
| `ordinal`, `name`, `version`, `build` | E65 | E63 | — | — | — | unknown |

### `ops.benchmark_manifest_concurrency`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:414`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E65. **Table readers:** E59, E63, E65.
**Unresolved SQL affecting any column:** E59. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E63, E65.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E65 | — | unknown |
| `draft_id` | E65 | E63 | E59 | — | — | unknown |
| `ordinal`, `concurrency_level` | E65 | E63 | — | — | — | unknown |

### `ops.benchmark_manifest_dimension`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:356`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E65. **Table readers:** E57, E59, E65.
**Unresolved SQL affecting any column:** E59. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E57, E65.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E65 | — | unknown |
| `draft_id`, `dimension` | E65 | E57, E59 | — | — | — | unknown |
| `ordinal`, `value` | E65 | E57 | — | — | — | unknown |

### `ops.benchmark_manifest_draft`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:319`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E65. **Table readers:** E53, E54, E55, E56, E58, E59, E63, E65, E66, E67, E187, E188, E193.
**Additional lexical/dependency evidence:** E53, E54, E55, E56, E58, E59, E63, E65, E66, E67, E187, E188, E193.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E53, E54, E55, E56, E58, E59, E63, E65, E66, E67, E187, E188, E193, E1518 | — | E65 | — | unknown |
| `benchmark_ref` | E65 | E53, E54, E56, E58, E59, E63, E65, E66, E187, E188, E1559 | — | — | — | unknown |
| `draft_version` | E65 | E54, E56, E58, E59, E63, E65, E66 | — | — | — | unknown |
| `idempotency_key`, `schema_version`, `payload_domain_tag`, `gate_id`, `producer_step_ref`, `producer_role`, `combiner`, `subject_digest`, `candidate_digest`, `policy_digest`, `cost_expectation_matrix_digest`, `samples_per_cell`, `warmup_runs`, `p95_aggregation_method` | E65 | E54, E58, E59, E63, E65, E66 | — | — | — | unknown |
| `outlier_rule` | E65 | E54, E58, E59, E63, E65, E66, E1520 | — | — | — | unknown |
| `payload_digest` | E65 | E54, E58, E59, E63, E65, E66, E1518 | — | — | — | unknown |
| `proposed_by_actor_id` | E65 | E54, E58, E59, E63, E65, E66, E67, E188, E193, E1520, E1559 | — | — | — | unknown |
| `created_at` | — | E54, E58, E59, E63, E65, E66 | — | E65 | — | unknown |
| `proposed_by_session_ref` | — | E54, E58, E59, E63, E65, E66, E188, E193, E1559 | — | — | E1559 | unknown |

### `ops.benchmark_manifest_evaluator`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:444`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E65. **Table readers:** E59, E63, E65.
**Unresolved SQL affecting any column:** E59. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E63, E65.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E65 | — | unknown |
| `draft_id` | E65 | E63 | E59 | — | — | unknown |
| `ordinal`, `actor_id`, `session_ref`, `authority_class` | E65 | E63 | — | — | — | unknown |

### `ops.benchmark_manifest_request_size`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:392`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E65. **Table readers:** E59, E63, E65.
**Unresolved SQL affecting any column:** E59. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E63, E65.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E65 | — | unknown |
| `draft_id` | E65 | E63 | E59 | — | — | unknown |
| `ordinal`, `percentile`, `bytes` | E65 | E63 | — | — | — | unknown |

### `ops.benchmark_manifest_review`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:471`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E68. **Table readers:** E54, E61, E68, E188.
**Unresolved SQL affecting any column:** E66. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E54, E61, E68, E188.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E54, E61, E68, E188, E1519 | — | E68 | — | unknown |
| `draft_id` | E68 | E54, E61, E68, E1521, E1560 | — | — | — | unknown |
| `idempotency_key`, `measurement_set_digest` | E68 | E54, E61, E68 | — | — | — | unknown |
| `reviewed_payload_digest`, `review_summary` | E68 | E54, E61, E68, E1521 | — | — | — | unknown |
| `verdict` | E68 | E54, E61, E68, E1519, E1521 | — | — | E1519, E1521 | unknown |
| `reviewer_actor_id` | E68 | E54, E61, E68, E188, E1521, E1560 | — | — | E1521 | unknown |
| `created_at` | — | E54, E61, E68 | — | E68 | — | unknown |
| `reviewer_session_ref` | — | E54, E61, E68, E188, E1560 | — | — | E1560 | unknown |

### `ops.benchmark_manifest_workload`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:377`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E65. **Table readers:** E59, E63, E65.
**Unresolved SQL affecting any column:** E59. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E63, E65.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E65 | — | unknown |
| `draft_id` | E65 | E59, E63 | E59 | — | — | unknown |
| `ordinal`, `workload_id`, `operation_mix_digest` | E65 | E63 | — | — | — | unknown |
| `weight_basis_points` | E65 | E59, E63 | — | — | — | unknown |

### `ops.benchmark_measurement_coverage_attestation`

**Definition:** `migrations/0508_foundation_assurance_minimum_receipt.sql:534`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E68. **Table readers:** E61, E68.
**Unresolved SQL affecting any column:** E62, E66. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E61, E68.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E61, E68 | — | E68 | — | unknown |
| `review_id`, `draft_id`, `coverage_proved_by`, `benchmark_payload_digest`, `measurement_set_digest`, `evaluation_digest`, `attested_by_actor_id` | E68 | E61, E68 | — | — | — | unknown |

### `ops.calendar_canary_receipt`

**Definition:** `migrations/0226_calendar_canary_record_layer.sql:7`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E424.
**Additional lexical/dependency evidence:** E375, E424.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E424 | — | declared; caller unknown | — | unknown |
| `job_id`, `attempt`, `source_snapshot_id`, `source_snapshot_digest`, `source_contact_count`, `output_digest`, `exact_count`, `domain_count`, `unknown_count` | — | E424 | — | — | — | unknown |

### `ops.calendar_canary_source_snapshot`

**Definition:** `migrations/0226_calendar_canary_record_layer.sql:3`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E121. **Table readers:** E121.
**Additional lexical/dependency evidence:** E121, E375.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E121 | — | E121 | — | unknown |
| `job_id`, `attempt`, `workflow_version`, `snapshot`, `snapshot_digest`, `contact_count` | E121 | E121 | — | — | — | unknown |

### `ops.calendar_prebrief_allowed_calendar`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:105`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E9, E214, E1500, E1695, E1696, E1697.
**Unresolved SQL affecting any column:** E94, E607. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E9, E208, E209, E214, E376, E418.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `sponsor`, `active_revision_id` | — | E9, E214, E1500, E1695, E1696, E1697 | — | — | — | yes |
| `calendar_keys` | — | E9, E214 | — | — | — | yes |
| `configuration_digest` | — | E9, E214, E1500 | — | — | — | yes |
| `configured_at` | — | E9 | — | declared; caller unknown | — | unknown |
| `configured_by` | — | E9 | — | — | — | unknown |

### `ops.calendar_prebrief_allowlist_receipt`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:92`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1500.
**Unresolved SQL affecting any column:** E9, E94, E607. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E209, E418.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1500 | — | declared; caller unknown | — | unknown |
| `sponsor`, `configuration_digest` | — | E1500 | — | — | — | unknown |
| `calendar_keys`, `configured_by` | — | — | — | — | — | unknown |
| `configured_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.calendar_prebrief_canary_event`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:256`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** —.
**Additional lexical/dependency evidence:** E208.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `captured_at` | — | — | — | declared; caller unknown | — | unknown |
| `sponsor`, `destination`, `occurrence_key`, `snapshot_at`, `allowlist_revision_id`, `source_attestation_id` | — | — | — | — | — | unknown |

### `ops.calendar_prebrief_canary_receipt`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:267`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1695.
**Additional lexical/dependency evidence:** E208.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | declared; caller unknown | — | unknown |
| `job_id`, `attempt`, `source_attestation_id`, `canonical_event_digest` | — | — | — | — | — | unknown |
| `sponsor`, `destination`, `snapshot_at`, `allowlist_revision_id`, `event_count` | — | E1695 | — | — | — | unknown |
| `captured_at` | — | E1695 | — | declared; caller unknown | — | unknown |

### `ops.calendar_prebrief_capture_challenge`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:226`. **Product path:** positive static evidence (column detail below).

**Table writers:** E214. **Table readers:** E214, E1691.
**Additional lexical/dependency evidence:** E214, E376.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E214, E1691 | — | E214 | — | yes |
| `job_id`, `attempt`, `lease_token`, `sponsor`, `mode`, `destination`, `scheduled_for`, `allowlist_revision_id`, `allowlist_digest` | E214 | E214, E1691 | — | — | — | yes |
| `resolver_identity`, `window_starts_at`, `window_ends_at`, `calendar_keys` | E214 | E214 | — | — | — | yes |
| `issued_at` | — | E214 | — | E214 | — | yes |

### `ops.calendar_prebrief_projection_event`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:148`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1696.
**Additional lexical/dependency evidence:** E209.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1696 | — | declared; caller unknown | — | unknown |
| `sponsor`, `occurrence_key`, `starts_at`, `ends_at`, `title`, `location`, `allowlist_revision_id` | — | E1696 | — | — | — | unknown |
| `calendar_key`, `event_key`, `snapshot_at` | — | — | — | — | — | unknown |
| `captured_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.calendar_prebrief_projection_participant`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:164`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1696.
**Additional lexical/dependency evidence:** E209.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `event_id`, `party_id`, `subject_type`, `subject_id`, `participant_ref` | — | E1696 | — | — | — | unknown |
| `captured_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.calendar_prebrief_projection_receipt`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:174`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E104, E1691, E1697.
**Additional lexical/dependency evidence:** E104, E209.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E104, E1691 | — | declared; caller unknown | — | unknown |
| `job_id`, `attempt`, `allowlist_digest`, `source_attestation_id` | — | E104, E1691 | — | — | — | unknown |
| `sponsor`, `snapshot_at`, `allowlist_revision_id` | — | E104, E1691, E1697 | — | — | — | unknown |
| `snapshot_digest` | — | E104 | — | — | — | unknown |
| `event_count`, `participant_count` | — | E104, E1697 | — | — | — | unknown |
| `captured_at` | — | E104, E1697 | — | declared; caller unknown | — | unknown |

### `ops.calendar_prebrief_runtime_activation_receipt`

**Definition:** `migrations/0250_calendar_prebrief_joe_runtime.sql:5`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E9. **Table readers:** E9, E338, E1500.
**Unresolved SQL affecting any column:** E94, E607. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E9, E338.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E9, E338, E1500 | — | E9 | — | unknown |
| `sponsor`, `allowlist_revision_id` | E9 | E338, E1500 | — | — | — | unknown |
| `app_evidence_digest` | E9 | E338 | — | — | — | unknown |
| `activated_by` | E9 | — | — | — | — | unknown |
| `activated_at` | — | E338, E1500 | — | E9 | — | unknown |

### `ops.calendar_prebrief_source_attestation_receipt`

**Definition:** `migrations/0229_calendar_prebrief_projection.sql:196`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E104, E1691.
**Additional lexical/dependency evidence:** E104, E208, E209, E376.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E104, E1691 | — | declared; caller unknown | — | unknown |
| `job_id`, `attempt`, `lease_token`, `sponsor`, `mode`, `destination`, `snapshot_at`, `allowlist_revision_id`, `capture_challenge_id`, `allowlist_digest` | — | E104, E1691 | — | — | — | unknown |
| `attestor_identity`, `observed_calendar_keys`, `event_count`, `canonical_event_digest`, `collector_key_fingerprint`, `signature_sha256`, `collector_version` | — | E104 | — | — | — | unknown |
| `attested_at` | — | E104 | — | declared; caller unknown | — | unknown |

### `ops.canonical_ownership_claim`

**Definition:** `migrations/0450_canonical_ownership_lease_kernel.sql:56`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E8. **Table readers:** E8, E48, E77, E80.
**Unresolved SQL affecting any column:** E48, E93. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E8, E77, E80.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `lease_id`, `claim_kind`, `claim_value`, `claim_mode`, `operation` | E8 | E8, E48, E77, E80 | — | — | — | unknown |
| `organization_tenant_id` | E8 | — | — | — | — | unknown |
| `created_at` | E8 | — | — | declared; caller unknown | — | unknown |

### `ops.canonical_ownership_dependency`

**Definition:** `migrations/0450_canonical_ownership_lease_kernel.sql:74`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E8. **Table readers:** E8, E44, E48, E87.
**Additional lexical/dependency evidence:** E8, E44, E48, E87.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `lease_id`, `dependency_slice_ref`, `required_state`, `observed_receipt_id`, `observed_reviewer_fact_id` | E8 | E44, E48, E87 | — | — | — | unknown |
| `organization_tenant_id`, `observed_envelope_id`, `evaluated_at` | E8 | E44, E87 | — | — | — | unknown |
| `created_at` | E8 | E44, E87 | — | declared; caller unknown | — | unknown |

### `ops.canonical_ownership_issuer_generation`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:260`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E47, E85.
**Unresolved SQL affecting any column:** E80, E278, E279. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E47, E85.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `principal`, `state` | — | E47, E85 | — | — | — | unknown |
| `generation` | — | E47 | — | — | — | unknown |
| `changed_at` | — | E47 | — | declared; caller unknown | — | unknown |

### `ops.canonical_ownership_lease`

**Definition:** `migrations/0450_canonical_ownership_lease_kernel.sql:15`. **Product path:** positive static evidence (column detail below).

**Table writers:** E8, E84, E160, E409, E416. **Table readers:** E8, E41, E42, E44, E45, E46, E47, E48, E80, E84, E87, E160, E339, E365, E369, E370, E372, E373, E409, E416.
**Unresolved SQL affecting any column:** E77. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E8, E41, E42, E44, E46, E47, E48, E80, E84, E87, E160, E339, E365, E369, E370, E372, E373, E409, E416.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E8, E42, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373, E409, E416 | E160 | E8 | — | unknown |
| `organization_tenant_id` | E8 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373 | E160 | — | — | unknown |
| `holder_actor_id`, `holder_actor_slug`, `holder_session_ref`, `holder_host_ref`, `work_request_version`, `work_request_digest`, `accepted_plan_digest`, `slice_plan_id`, `slice_plan_digest`, `slice_ref`, `subject_envelope_id`, `contract_digest`, `acquired_at`, `replaced_at`, `superseded_by_lease_id` | E8 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373 | — | — | — | unknown |
| `lease_token` | — | E8, E41, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373, E409, E416 | — | E8 | — | unknown |
| `fencing_generation` | — | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373, E409, E416 | E160 | E8 | — | unknown |
| `work_request_id`, `accepted_plan_id` | E8 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E365, E369, E370, E372, E373 | — | — | — | yes |
| `state` | E8, E84, E160, E409 | E8, E42, E44, E46, E47, E48, E80, E84, E87, E160, E339, E365, E369, E370, E372, E373, E409, E416 | — | E8 | — | yes |
| `renewed_at` | E416 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373 | — | — | — | unknown |
| `expires_at` | E8, E416 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E365, E369, E370, E372, E373, E409, E416 | — | — | — | yes |
| `released_at` | E84, E409 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373 | — | — | — | unknown |
| `created_at` | E8 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373 | — | declared; caller unknown | — | unknown |
| `updated_at` | E8, E84, E160, E409, E416 | E8, E44, E46, E47, E48, E80, E84, E87, E160, E339, E369, E370, E372, E373 | — | declared; caller unknown | — | unknown |

### `ops.canonical_ownership_lease_event`

**Definition:** `migrations/0450_canonical_ownership_lease_kernel.sql:91`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E8, E84, E160, E409, E416. **Table readers:** E8, E47, E84, E160, E409, E416.
**Additional lexical/dependency evidence:** E8, E47, E84, E160, E409, E416.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E47, E160 | E160 | — | — | unknown |
| `organization_tenant_id`, `fencing_generation` | E8, E84, E160, E409, E416 | E47, E160 | E160 | — | — | unknown |
| `lease_id`, `actor_id`, `session_ref`, `host_ref`, `cause`, `occurred_at` | E8, E84, E160, E409, E416 | E47, E160 | — | — | — | unknown |
| `event_kind` | E8, E84, E160, E409, E416 | E47, E84, E160 | — | — | — | unknown |
| `created_at` | E8, E84, E160, E409, E416 | E47, E160 | — | declared; caller unknown | — | unknown |

### `ops.canonical_ownership_merge_binding`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:306`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E278. **Table readers:** E82, E278.
**Additional lexical/dependency evidence:** E82, E278.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `runtime_binding_id`, `decision_id`, `decision_event_id`, `receipt_id`, `scope_id`, `head_sha`, `pr_number`, `decision_expires_at` | E278 | E82, E278 | — | — | — | unknown |
| `capability_token` | E278 | E278 | — | — | — | unknown |

### `ops.canonical_ownership_operation`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:727`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E80. **Table readers:** E80, E339.
**Additional lexical/dependency evidence:** E80, E339.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `runtime_binding_id`, `response` | E80 | E80, E339 | — | — | — | unknown |
| `request_digest`, `operation` | E80 | E80 | — | — | — | unknown |
| `created_at` | — | E80 | — | E80 | — | unknown |

### `ops.canonical_ownership_runtime_session`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:273`. **Product path:** positive static evidence (column detail below).

**Table writers:** E84, E278, E279. **Table readers:** E46, E47, E80, E82, E84, E86, E278, E279, E365.
**Unresolved SQL affecting any column:** E279. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E46, E47, E80, E82, E84, E86, E278, E365.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | E278, E279 | E46, E47, E80, E82, E84, E86, E278, E279 | — | declared; caller unknown | — | unknown |
| `idempotency_key`, `attempt`, `runtime_session_ref`, `ownership_session_ref`, `execution_host_ref`, `issuer_principal`, `capability_token_hash` | E278, E279 | E46, E47, E80, E84, E86, E278, E279 | — | — | — | unknown |
| `organization_tenant_id`, `actor_id`, `actor_slug`, `subject_envelope_id` | E278, E279 | E46, E47, E80, E82, E84, E86, E278, E279 | — | — | — | unknown |
| `work_request_id`, `accepted_plan_id`, `expires_at` | E278, E279 | E46, E47, E80, E82, E84, E86, E278, E279, E365 | — | — | — | yes |
| `phase` | E278 | E46, E47, E80, E82, E84, E86, E278, E279 | — | E279 | — | unknown |
| `state` | E84, E279 | E46, E47, E80, E82, E84, E86, E278, E279, E365 | — | E278, E279 | — | yes |
| `issued_at`, `created_at` | — | E46, E47, E80, E84, E86, E278, E279 | — | E278, E279 | — | unknown |
| `replaced_by_id` | — | E46, E47, E80, E84, E86, E278, E279 | — | — | — | unknown |

### `ops.capability_agent_session`

**Definition:** `migrations/0126_capability_program_evidence.sql:16`. **Product path:** positive static evidence (column detail below).

**Table writers:** E788, E791, E897, E898. **Table readers:** E52, E79, E81, E88, E89, E144, E146, E199, E201, E203, E279, E341, E365, E611, E612, E646, E784, E788, E790, E791, E794, E890, E892, E897, E898, E1078, E1668.
**Unresolved SQL affecting any column:** E86, E198, E199, E279. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E52, E79, E81, E88, E89, E144, E146, E147, E150, E152, E201, E203, E341, E365, E611, E612, E646.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E52, E79, E81, E88, E89, E144, E146, E199, E201, E203, E279, E341, E365, E611, E612, E784, E788, E790, E791, E794, E890, E892, E897, E898, E1078, E1555, E1668 | E341 | E898 | — | yes |
| `work_request_id` | E898 | E52, E79, E88, E89, E201, E279, E341, E611, E612, E784, E790, E791, E794, E890, E892, E897, E1078, E1525, E1668 | — | — | — | unknown |
| `executor_actor_id` | E898 | E52, E79, E81, E88, E89, E144, E146, E199, E201, E279, E341, E611, E612, E784, E790, E791, E890, E892, E898, E1525 | — | — | — | unknown |
| `created_by_actor_id` | E898 | E52, E88, E89, E201, E279, E341, E611, E612, E784, E790, E791, E1525 | — | — | — | unknown |
| `state` | E788, E791, E897 | E52, E88, E89, E201, E279, E341, E365, E611, E612, E784, E790, E791, E794, E890, E892, E897, E898, E1078, E1525, E1555, E1668 | E341 | E898 | E1555 | yes |
| `source_commit_sha` | E898 | E52, E88, E89, E201, E279, E341, E612, E784, E790, E791, E890, E892, E1525 | — | — | — | unknown |
| `worktree_ref` | E898 | E52, E88, E89, E201, E279, E341, E611, E612, E784, E790, E791, E890, E892, E1525 | — | — | — | unknown |
| `scope_ref` | E898 | E52, E88, E89, E201, E279, E341, E784, E790, E791, E890, E892, E1525 | — | — | — | unknown |
| `candidate_kind`, `candidate_evidence` | — | E52, E88, E89, E201, E279, E341, E784, E790, E791, E1525 | — | — | — | unknown |
| `candidate_fingerprint` | — | E52, E88, E89, E201, E279, E341, E784, E790, E791, E1525 | — | — | E1525 | unknown |
| `claimed_at` | — | E52, E88, E89, E201, E279, E341, E784, E790, E791 | — | E898 | — | unknown |
| `started_at`, `prepared_at` | — | E52, E88, E89, E201, E279, E341, E611, E784, E790, E791 | — | — | — | unknown |
| `completed_at` | E788 | E52, E88, E89, E201, E279, E341, E611, E784, E790, E791 | — | — | — | unknown |
| `cancelled_at` | E791, E897 | E52, E88, E89, E201, E279, E341, E611, E784, E790, E791 | — | — | — | unknown |
| `created_at` | — | E52, E88, E89, E201, E279, E341, E784, E790, E791, E892, E1078 | — | E898 | — | unknown |
| `updated_at` | — | E52, E88, E89, E201, E279, E341, E612, E784, E790, E791, E1078, E1525, E1668 | E341 | E898 | E1525 | unknown |
| `version` | E788, E791, E897 | E52, E88, E89, E201, E279, E341, E784, E788, E790, E791, E897 | — | E898 | — | unknown |
| `lease_expires_at` | E898 | E52, E88, E89, E144, E146, E201, E279, E341, E365, E784, E790, E791, E890, E892, E898, E1526 | — | — | — | yes |

### `ops.capability_verification`

**Definition:** `migrations/0126_capability_program_evidence.sql:62`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E786.
**Unresolved SQL affecting any column:** E89. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `attested_at` | — | E786 | — | declared; caller unknown | — | unknown |
| `build_session_id`, `work_request_id`, `candidate_fingerprint` | — | E786, E1527 | — | — | — | unknown |
| `verifier_actor_id` | — | E786, E1527 | — | — | E1527 | unknown |
| `outcome`, `verification_evidence_ref`, `source_ref`, `note` | — | E786 | — | — | — | unknown |

### `ops.cognition_cache_observation`

**Definition:** `migrations/0195_control_plane_cache_observations.sql:8`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E197, E213, E335. **Table readers:** E197, E213, E335.
**Additional lexical/dependency evidence:** E197, E213, E335.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `observed_at` | — | — | — | E197, E213, E335 | — | unknown |
| `job_id`, `attempt`, `workflow_key`, `workflow_version`, `mode`, `observation_kind` | E197, E213, E335 | — | — | — | — | unknown |
| `cache_key` | E197, E213, E335 | — | E213 | — | — | unknown |
| `dependency_ref` | E213 | — | — | — | — | unknown |

### `ops.cognition_job`

**Definition:** `migrations/0149_control_plane_jobs.sql:49`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E197, E335.
**Additional lexical/dependency evidence:** E197, E335.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `key`, `version`, `input_schema_version`, `output_schema_version`, `input_schema`, `output_schema`, `max_tokens`, `max_cost_usd`, `timeout_seconds`, `provider_routes` | — | E197, E335 | — | — | — | unknown |
| `cache_ttl_seconds`, `canonical_write_authority`, `active`, `registered_at` | — | E197, E335 | — | declared; caller unknown | — | unknown |

### `ops.cognition_result_cache`

**Definition:** `migrations/0149_control_plane_jobs.sql:179`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E212, E213, E334, E335. **Table readers:** E196, E197, E212, E213, E334, E335.
**Additional lexical/dependency evidence:** E196, E197, E212, E213, E334, E335.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `cache_key` | E334, E335 | E196, E197 | E213 | — | — | unknown |
| `cognition_key`, `cognition_version`, `output_schema_version` | E334, E335 | E197, E335 | — | — | — | unknown |
| `proposal`, `expires_at` | E334, E335 | E196, E197, E334, E335 | — | — | — | unknown |
| `evidence_refs`, `created_at` | — | E197 | — | E334, E335 | — | unknown |
| `validated_at` | E334, E335 | E197, E334, E335 | — | — | — | unknown |
| `invalidated_at` | E212, E213, E334, E335 | E196, E197, E212, E213 | — | — | — | unknown |
| `dependency_refs` | E334, E335 | E197, E212, E213, E334, E335 | — | declared; caller unknown | — | unknown |

### `ops.completion_disposition`

**Definition:** `migrations/0431_completion_register_schema.sql:236`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1662.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1662 | — | declared; caller unknown | — | unknown |
| `organization_tenant_id` | — | E1539, E1662 | E1662 | — | E1539 | unknown |
| `subject_id` | — | E1539, E1662 | — | — | — | unknown |
| `disposition`, `replacement_subject_id`, `decision_ref`, `decided_at` | — | E1662 | — | — | — | unknown |
| `rationale` | — | — | — | — | — | unknown |
| `decided_by_actor_id` | — | E1539 | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.completion_observation`

**Definition:** `migrations/0431_completion_register_schema.sql:180`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1660.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1660 | — | declared; caller unknown | — | unknown |
| `organization_tenant_id` | — | E1540, E1660 | — | — | E1540 | unknown |
| `subject_id`, `source_revision`, `coherent_revision`, `observation_kind`, `authority_class`, `content_digest`, `value_digest`, `redacted_value`, `evidence_locator` | — | E1660 | — | — | — | unknown |
| `receipt_id`, `source_kind`, `source_ref`, `observed_at`, `expires_at`, `collector_name`, `collector_version` | — | E1540, E1660 | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.completion_policy`

**Definition:** `migrations/0431_completion_register_schema.sql:125`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1661.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E1661 | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `policy_digest` | — | E1541, E1661 | — | — | E1541 | unknown |
| `policy_key`, `policy_version`, `capability_class`, `required_dimensions`, `default_freshness`, `state_precedence`, `effective_at` | — | E1541, E1661 | — | — | — | unknown |

### `ops.completion_receipt`

**Definition:** `migrations/0431_completion_register_schema.sql:141`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E109, E640, E641, E1661.
**Additional lexical/dependency evidence:** E109, E640, E641.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E109, E640, E641, E1661 | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `receipt_ref` | — | E109, E1661 | — | — | — | unknown |
| `collector_name`, `outcome`, `expires_at` | — | E109, E640, E641 | — | — | — | unknown |
| `collector_version`, `source_kind`, `source_ref`, `source_cursor`, `rows_observed`, `rows_changed`, `failure_class`, `started_at`, `evidence_digest` | — | E109 | — | — | — | unknown |
| `finished_at` | — | E109, E640 | — | — | — | unknown |
| `created_at` | — | E109 | — | declared; caller unknown | — | unknown |

### `ops.completion_relation`

**Definition:** `migrations/0431_completion_register_schema.sql:213`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `from_subject_id`, `to_subject_id`, `relation_kind`, `authority_class`, `source_kind`, `source_ref`, `source_revision`, `relation_digest` | — | — | — | — | — | unknown |

### `ops.completion_subject`

**Definition:** `migrations/0431_completion_register_schema.sql:165`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1661, E1662.
**Unresolved SQL affecting any column:** E108. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1661, E1662 | — | declared; caller unknown | — | unknown |
| `organization_tenant_id` | — | E1661, E1662 | E1662 | — | — | unknown |
| `stable_key`, `capability_class` | — | E1661, E1662 | — | — | — | unknown |
| `human_label`, `canonical_source_kind`, `canonical_source_ref`, `created_provenance` | — | E1662 | — | — | — | unknown |
| `created_at` | — | E1662 | — | declared; caller unknown | — | unknown |

### `ops.context_activation_binding`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:243`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E10. **Table readers:** E10, E49, E50, E111, E113, E206, E207, E215, E216, E337, E340, E374, E415.
**Unresolved SQL affecting any column:** E415. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E10, E49, E50, E111, E113, E206, E207, E215, E216, E337, E340, E374.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E10, E49, E111, E113, E206, E207, E216, E337, E340, E374, E415 | E50, E340 | E10 | — | unknown |
| `idempotency_key`, `bundle_digest`, `retrieval_policy`, `mode`, `compiler_ref`, `query_basis_digest`, `grounding_plan` | E10 | E10, E49, E216, E337, E340 | — | — | — | unknown |
| `organization_tenant_id` | E10 | E10, E49, E111, E113, E206, E207, E215, E216, E337, E340, E374, E415 | E340 | — | — | unknown |
| `work_request_id` | E10 | E10, E49, E111, E113, E206, E207, E215, E216, E337, E340, E374, E415 | — | — | — | unknown |
| `work_request_version`, `expires_at` | E10 | E10, E49, E111, E113, E206, E207, E215, E216, E337, E340, E374 | — | — | — | unknown |
| `plan_id` | E10 | E10, E49, E111, E113, E207, E216, E337, E340, E374 | E50 | — | — | unknown |
| `plan_hash` | E10 | E10, E49, E111, E113, E207, E216, E337, E340, E374 | — | — | — | unknown |
| `binding_id` | E10 | E10, E49, E111, E113, E206, E207, E215, E216, E337, E340, E415 | — | — | — | unknown |
| `issued_at` | E10 | E10, E49, E216, E337, E340 | — | declared; caller unknown | — | unknown |
| `created_at` | — | E10, E49, E216, E337, E340 | — | E10 | — | unknown |

### `ops.context_activation_item`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:265`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E10. **Table readers:** E10, E49, E340, E415.
**Unresolved SQL affecting any column:** E49, E216. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E10, E340, E415.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E340, E415 | E340 | E10 | — | unknown |
| `binding_id`, `canonical_ref`, `required` | E10 | E49, E340, E415 | — | — | — | unknown |
| `ordinal` | E10 | E10, E340, E415 | — | — | — | unknown |
| `artifact_kind`, `revision`, `content_digest`, `scope_redaction`, `trigger_ref`, `consumer_ref`, `delivery_mode`, `representation_kind`, `freshness`, `selection_reason`, `selection_rank` | E10 | E340, E415 | — | — | — | unknown |

### `ops.correspondence_adapter_consent`

**Definition:** `migrations/0700_governed_correspondence_store.sql:213`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E116. **Table readers:** E114, E115, E116, E118, E119.
**Unresolved SQL affecting any column:** E116. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E114, E115, E118, E119.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E114, E115, E116, E118, E119 | — | E116 | — | unknown |
| `tenant`, `adapter_kind`, `account_digest`, `read_operations`, `human_quote`, `consented_by_actor_id`, `idempotency_key` | E116 | E116, E118 | — | — | — | unknown |
| `partner_slug` | E116 | E115, E116, E118, E119 | — | — | — | unknown |
| `recorded_at` | — | E116, E118 | — | E116 | — | unknown |

### `ops.correspondence_adapter_consent_revocation`

**Definition:** `migrations/0700_governed_correspondence_store.sql:240`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E119. **Table readers:** E114, E115, E119.
**Additional lexical/dependency evidence:** E114, E115, E119.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `recorded_at` | — | E115, E119 | — | E119 | — | unknown |
| `consent_id` | E119 | E114, E115, E119 | — | — | — | unknown |
| `revoked_by_actor_id`, `human_quote`, `idempotency_key` | E119 | E115, E119 | — | — | — | unknown |

### `ops.correspondence_adapter_read_receipt`

**Definition:** `migrations/0700_governed_correspondence_store.sql:255`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E118. **Table readers:** E115, E117, E118, E120.
**Additional lexical/dependency evidence:** E115, E117, E118, E120.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E117, E118, E120 | — | E118 | — | unknown |
| `tenant`, `consent_id`, `adapter_kind`, `account_digest`, `source_system`, `native_id`, `native_id_epoch`, `thread_metadata`, `metadata_digest` | E118 | E117, E120 | — | — | — | unknown |
| `partner_slug` | E118 | E115, E117, E120 | — | — | — | unknown |
| `recorded_by_actor_id` | E118 | E117 | — | — | — | unknown |
| `recorded_at` | — | E117, E120 | — | E118 | — | unknown |
| `idempotency_key` | E118 | E117, E118 | — | — | — | unknown |

### `ops.correspondence_draft`

**Definition:** `migrations/0700_governed_correspondence_store.sql:298`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E117. **Table readers:** E115, E117.
**Additional lexical/dependency evidence:** E115, E117.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `requires_human_send`, `dispatchable`, `recorded_at` | — | E117 | — | E117 | — | unknown |
| `tenant`, `read_receipt_id`, `draft_kind`, `intended_participant_refs`, `draft_body`, `draft_digest`, `recorded_by_actor_id`, `idempotency_key` | E117 | E117 | — | — | — | unknown |
| `partner_slug` | E117 | E115, E117 | — | — | — | unknown |

### `ops.cost_ledger_cell`

**Definition:** `migrations/0519_producer_cost_ledger.sql:109`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E102, E211. **Table readers:** E102, E211, E269.
**Additional lexical/dependency evidence:** E102, E211, E269.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tree_ref` | E211 | E102, E269 | — | — | — | unknown |
| `ledger_version`, `state_digest` | E102, E211 | E102, E269 | — | — | — | unknown |
| `updated_at` | E102 | — | — | E211 | — | unknown |

### `ops.cost_ledger_entry`

**Definition:** `migrations/0519_producer_cost_ledger.sql:84`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E102. **Table readers:** E102.
**Additional lexical/dependency evidence:** E102.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tree_ref`, `sequence`, `entry_id`, `kind`, `node_id`, `amount_units`, `operation_id`, `occurred_at`, `reservation_id`, `liability_id`, `conversion_id`, `vendor_reference`, `basis_digest` | E102 | — | — | — | — | unknown |

### `ops.cost_ledger_operation`

**Definition:** `migrations/0519_producer_cost_ledger.sql:68`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E102. **Table readers:** E102.
**Additional lexical/dependency evidence:** E102.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tree_ref`, `operation_id`, `kind`, `argument_digest`, `outcome`, `refusal_reason_id`, `produced_version`, `correlation_id` | E102 | E102 | — | — | — | unknown |
| `applied_at` | — | E102 | — | E102 | — | unknown |

### `ops.cost_refusal`

**Definition:** `migrations/0165_control_plane_cost_refusals.sql:9`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E14. **Table readers:** E14, E1673.
**Additional lexical/dependency evidence:** E14.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E14 | — | E14 | — | unknown |
| `job_id`, `attempt`, `monthly_budget_usd`, `spent_usd`, `reserved_usd` | E14 | E14 | — | — | — | unknown |
| `route_key`, `estimated_cost_usd`, `reason` | E14 | E14, E1673 | — | — | — | unknown |
| `refused_at` | — | E14, E1673 | — | E14 | — | unknown |

### `ops.cost_reservation`

**Definition:** `migrations/0153_control_plane_resilience.sql:15`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E14, E411, E423, E616. **Table readers:** E14, E411, E423, E616.
**Additional lexical/dependency evidence:** E14, E411, E423, E616.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E14, E411, E423, E616 | — | E14, E423 | — | unknown |
| `job_id`, `attempt` | E14, E423 | E411, E423, E616 | — | — | — | unknown |
| `route_key`, `estimated_cost_usd` | E14, E423 | E14, E423, E616 | — | — | — | unknown |
| `actual_cost_usd` | E616 | E423, E616 | — | — | — | unknown |
| `state` | E411, E616 | E14, E411, E423, E616 | — | E14, E423 | — | unknown |
| `created_at` | — | E14, E616 | — | E14, E423 | — | unknown |
| `settled_at` | E411, E616 | E423, E616 | — | — | — | unknown |

### `ops.cost_scope_node`

**Definition:** `migrations/0519_producer_cost_ledger.sql:51`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E211. **Table readers:** E211.
**Additional lexical/dependency evidence:** E211.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tree_ref`, `node_id`, `parent_node_id`, `scope_kind`, `authorization_ceiling_units` | E211 | — | — | — | — | unknown |

### `ops.cost_scope_tree`

**Definition:** `migrations/0519_producer_cost_ledger.sql:42`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E211. **Table readers:** E211.
**Unresolved SQL affecting any column:** E211. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tree_ref`, `tree_id`, `tree_version` | E211 | — | — | — | — | unknown |
| `created_at` | — | — | — | E211 | — | unknown |

### `ops.deployment`

**Definition:** `migrations/0115_ops_observability.sql:248`. **Product path:** positive static evidence (column detail below).

**Table writers:** E393, E394, E1465. **Table readers:** E393, E394, E713, E929, E1465, E1687, E1692.
**Unresolved SQL affecting any column:** E132, E301, E318, E321, E410, E715, E716, E926. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E393, E394, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E393, E394, E713, E929, E1465, E1590, E1687, E1692 | — | E393, E394, E1465 | — | yes |
| `correlation_id` | E393, E394, E1465 | E713, E1687, E1692 | — | declared; caller unknown | — | yes |
| `service_id`, `started_at`, `ended_at`, `source_kind`, `source_ref` | E393, E394, E1465 | E1687, E1692 | — | — | — | yes |
| `environment` | E393, E394, E1465 | E1547, E1548, E1687, E1692 | — | — | — | yes |
| `state` | E393, E394, E1465 | E929, E1547, E1687, E1692 | — | — | — | yes |
| `git_sha` | E393, E394, E1465 | E1548, E1687, E1692 | — | — | — | yes |
| `release_ref`, `failure_class`, `detail` | E1465 | E1687, E1692 | — | — | — | yes |
| `deployed_by_actor`, `verb_count`, `schema_highest_migration`, `doctrine_generation`, `read_back_at`, `verification_evidence_ref` | E393, E394, E1465 | E1687 | — | — | — | unknown |
| `rollback_of` | — | E1687 | — | — | — | unknown |
| `observed_at` | E393, E394, E1465 | E1687, E1692 | — | declared; caller unknown | — | yes |
| `expires_at` | — | E1687, E1692 | — | — | — | yes |
| `release_id` | E393, E394, E1465 | E1548, E1687 | — | — | — | unknown |
| `provider`, `provider_version_id` | E393, E394, E1465 | E1547, E1548, E1687 | — | — | — | unknown |

### `ops.device_evidence_principal`

**Definition:** `migrations/0163_control_plane_device_evidence.sql:16`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E377, E378, E382, E385.
**Additional lexical/dependency evidence:** E377, E378, E382, E385.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `login_role`, `device_id` | — | E377, E378, E382, E385 | — | — | — | unknown |
| `active`, `provisioned_at` | — | E377, E378, E382, E385 | — | declared; caller unknown | — | unknown |

### `ops.device_evidence_receipt`

**Definition:** `migrations/0163_control_plane_device_evidence.sql:23`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E378. **Table readers:** E378.
**Additional lexical/dependency evidence:** E378.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E378 | — | E378 | — | unknown |
| `job_id`, `builder_key`, `workflow_key`, `workflow_version`, `mode`, `scheduled_for`, `device_id`, `observed_at`, `evidence`, `idempotency_key` | E378 | E378 | — | — | — | unknown |

### `ops.doc_conversation`

**Definition:** `migrations/0520_doc_conversation_store.sql:26`. **Product path:** positive static evidence (column detail below).

**Table writers:** E20, E122, E414, E617. **Table readers:** E20, E105, E122, E137, E138, E265, E414, E617, E712.
**Unresolved SQL affecting any column:** E20, E414. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E105, E122, E137, E138, E265, E617, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | E122 | E20, E105, E122, E137, E138, E265, E414, E617, E712 | — | declared; caller unknown | — | yes |
| `title` | E122, E414 | E122, E137, E265, E414, E617, E712 | E414 | — | — | yes |
| `created_by_actor` | E122 | E122, E137, E138, E265, E414, E617 | — | — | — | yes |
| `created_at` | — | E122, E137, E265, E414, E617 | — | E122 | — | yes |
| `updated_at` | E20, E414, E617 | E122, E137, E265, E414, E617 | — | E122 | — | yes |
| `pinned_at`, `archived_at` | E414 | E122, E137, E265, E414, E617 | — | — | — | yes |
| `visibility` | E122, E617 | E122, E137, E265, E414, E617 | — | declared; caller unknown | — | yes |
| `version` | E414 | E122, E137, E265, E414, E617 | — | E122 | — | yes |

### `ops.doc_conversation_grant`

**Definition:** `migrations/0520_doc_conversation_store.sql:61`. **Product path:** positive static evidence (column detail below).

**Table writers:** E617. **Table readers:** E137, E138, E265, E617.
**Unresolved SQL affecting any column:** E137, E414, E617. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E138, E265.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `conversation_id`, `grantee_actor`, `revoked_at` | E617 | E137, E138, E265, E617 | — | — | — | yes |
| `granted_at` | E617 | E137 | — | declared; caller unknown | — | yes |
| `granted_by_actor` | E617 | E137 | — | — | — | yes |

### `ops.doc_conversation_title_revision`

**Definition:** `migrations/0520_doc_conversation_store.sql:38`. **Product path:** positive static evidence (column detail below).

**Table writers:** E414. **Table readers:** E414.
**Additional lexical/dependency evidence:** E414.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `conversation_id`, `by_actor` | E414 | — | — | — | — | yes |
| `title` | E414 | — | E414 | — | — | yes |
| `at` | E414 | — | — | declared; caller unknown | — | yes |

### `ops.doc_conversation_turn`

**Definition:** `migrations/0520_doc_conversation_store.sql:46`. **Product path:** positive static evidence (column detail below).

**Table writers:** E20. **Table readers:** E20, E105, E137, E265, E266, E657, E712.
**Unresolved SQL affecting any column:** E137. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E20, E105, E265, E266, E657, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E20, E657 | — | — | — | yes |
| `conversation_id`, `sequence` | E20 | E20, E105, E137, E265, E266, E657, E712 | — | — | — | yes |
| `role`, `body`, `msg_id`, `origin_actor` | E20 | E20, E137, E657 | — | — | — | yes |
| `origin_channel` | E20 | E20, E137, E657 | — | declared; caller unknown | — | yes |
| `at` | — | E20, E137, E265, E657 | — | E20 | — | yes |

### `ops.doc_correction_proposal`

**Definition:** `migrations/0744_doc_suggestions.sql:48`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E325. **Table readers:** E266, E325.
**Additional lexical/dependency evidence:** E266, E325.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `suggestion_id`, `base_version`, `proposed_text` | E325 | E266, E325 | — | — | — | unknown |
| `source_conversation_id`, `source_sequence`, `proposed_by` | E325 | E325 | — | — | — | unknown |
| `proposed_at`, `status` | — | E266, E325 | — | E325 | — | unknown |

### `ops.doc_suggestion`

**Definition:** `migrations/0744_doc_suggestions.sql:3`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E128, E657. **Table readers:** E128, E266, E325, E657, E712.
**Additional lexical/dependency evidence:** E128, E266, E325, E657, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `conversation_id`, `polished_text` | E657 | E128, E266, E325, E657, E712 | — | — | — | unknown |
| `obligation_key`, `material_facts`, `source_sequence`, `original_text`, `uncertainty`, `contributor`, `source_at` | E657 | E128, E266, E325, E657 | — | — | — | unknown |
| `material_version`, `suggested_at` | E657 | E128, E266, E325, E657, E712 | — | E657 | — | unknown |
| `version` | E128, E657 | E128, E266, E325, E657 | — | E657 | — | unknown |
| `disposition` | E128, E657 | E128, E266, E325, E657, E712 | — | E657 | — | unknown |
| `dismissed_material_version`, `snoozed_material_version`, `snoozed_until` | E128 | E128, E266, E325, E657, E712 | — | — | — | unknown |
| `work_ref` | E128, E657 | E128, E266, E325, E657 | — | — | — | unknown |

### `ops.doc_suggestion_contribution`

**Definition:** `migrations/0744_doc_suggestions.sql:24`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E657. **Table readers:** E266, E657.
**Unresolved SQL affecting any column:** E657. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E266.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `request` | E657 | E657 | — | — | — | unknown |
| `suggestion_id`, `source_sequence`, `original_text`, `contributor`, `at` | E657 | E266, E657 | — | — | — | unknown |

### `ops.doc_suggestion_decision`

**Definition:** `migrations/0744_doc_suggestions.sql:40`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E128. **Table readers:** E128.
**Additional lexical/dependency evidence:** E128.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `suggestion_id`, `request`, `result`, `decided_by` | E128 | E128 | — | — | — | unknown |
| `at` | — | E128 | — | E128 | — | unknown |

### `ops.doc_suggestion_scan`

**Definition:** `migrations/0744_doc_suggestions.sql:34`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E105. **Table readers:** E105, E266, E712.
**Additional lexical/dependency evidence:** E105, E266, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key` | E105 | E105 | — | — | — | unknown |
| `conversation_id`, `through_sequence` | E105 | E105, E266, E712 | — | — | — | unknown |
| `completed_at` | — | E105 | — | E105 | — | unknown |

### `ops.doc_whats_new_lead_creation`

**Definition:** `migrations/0766_doc_whats_new_repair.sql:2`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E92. **Table readers:** E92, E712.
**Additional lexical/dependency evidence:** E92, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `lead_id`, `created_at`, `creation_xid` | E92 | E712 | — | — | — | unknown |

### `ops.doc_whats_new_watermark`

**Definition:** `migrations/0765_doc_whats_new.sql:4`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E272. **Table readers:** E272, E710.
**Additional lexical/dependency evidence:** E272, E710.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `partner_id` | E272 | E710 | — | — | — | unknown |
| `high_water`, `source_snapshot` | E272 | E272, E710 | — | — | — | unknown |

### `ops.enforcement_control_catalog`

**Definition:** `migrations/0194_atomic_rule_approval.sql:8`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E15, E32, E35, E72, E659, E690, E750.
**Unresolved SQL affecting any column:** E35, E421, E422, E659. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E15, E32, E72, E690.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `control_key` | — | E15, E35, E72, E659, E690, E750, E1507 | E15, E35 | — | — | unknown |
| `implementation_ref` | — | E72, E659, E690 | E15, E35 | — | — | unknown |
| `test_ref` | — | E15, E35, E72, E659, E690 | E15, E35 | — | — | unknown |
| `enforcement_class`, `verified_at` | — | E15, E32, E35, E72, E690, E750 | E15, E35 | — | — | unknown |
| `installed` | — | E15, E32, E35, E72, E690, E750 | — | declared; caller unknown | — | unknown |
| `updated_at` | — | E690 | — | declared; caller unknown | — | unknown |

### `ops.engineering_execution_envelope`

**Definition:** `migrations/0310_engineering_execution_fabric.sql:30`. **Product path:** positive static evidence (column detail below).

**Table writers:** E899. **Table readers:** E8, E44, E47, E52, E79, E80, E81, E82, E84, E86, E87, E144, E146, E148, E151, E154, E199, E201, E202, E203, E278, E279, E341, E365, E369, E620, E624, E628, E646, E889, E899, E900, E901.
**Unresolved SQL affecting any column:** E86, E155, E198, E199, E202, E203, E278, E279, E621. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E8, E44, E47, E52, E79, E80, E81, E82, E84, E87, E144, E146, E147, E148, E150, E151, E152, E154, E201, E341, E365, E369, E620, E624, E628, E646.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | E899 | E8, E44, E47, E52, E79, E80, E81, E82, E84, E86, E87, E144, E146, E148, E151, E154, E199, E201, E203, E278, E279, E341, E365, E369, E620, E624, E628, E889, E899, E900, E901 | E154, E341, E628, E899 | declared; caller unknown | — | yes |
| `job_id` | E899 | E47, E52, E79, E80, E84, E86, E144, E146, E148, E151, E154, E199, E201, E203, E278, E279, E341, E365, E369, E620, E624, E628, E889, E899, E900, E901 | E341 | — | — | yes |
| `work_request_id` | E899 | E8, E44, E47, E52, E79, E80, E82, E87, E144, E146, E151, E199, E201, E203, E278, E279, E341, E365, E369, E628, E889, E899, E900, E901, E1552 | — | — | — | yes |
| `accepted_plan_id` | E899 | E47, E52, E79, E80, E82, E148, E151, E199, E201, E278, E279, E341, E365, E369, E628, E889, E899, E900, E901, E1552 | E899 | — | — | yes |
| `slice_plan_id`, `slice_ref` | E899 | E8, E44, E47, E52, E79, E80, E81, E87, E144, E146, E151, E199, E201, E202, E203, E278, E279, E341, E365, E369, E620, E624, E628, E889, E899, E900, E901, E1552 | — | — | — | yes |
| `agent_session_id` | E899 | E47, E52, E79, E80, E81, E144, E146, E151, E199, E201, E202, E203, E278, E279, E341, E365, E369, E628, E889, E899, E900, E901, E1552 | — | — | — | yes |
| `state_version` | E899 | E47, E52, E79, E80, E82, E151, E199, E201, E278, E279, E341, E365, E369, E628, E899, E900, E901, E1552 | — | — | — | yes |
| `canonical_record_digest` | E899 | E47, E52, E79, E80, E151, E199, E201, E278, E279, E341, E365, E369, E628, E899, E900, E901, E1552 | — | — | — | yes |
| `envelope_digest` | E899 | E47, E52, E79, E80, E146, E151, E199, E201, E278, E279, E341, E365, E369, E628, E899, E900, E901 | — | — | — | yes |
| `envelope` | E899 | E47, E52, E79, E80, E151, E199, E201, E278, E279, E341, E365, E369, E628, E889, E899, E900, E901 | — | — | — | yes |
| `issued_at`, `supersession_reason` | E899 | E47, E52, E79, E80, E151, E199, E201, E278, E279, E341, E365, E369, E628, E899, E900, E901 | — | — | — | yes |
| `expires_at` | E899 | E47, E52, E79, E80, E144, E151, E199, E201, E278, E279, E341, E365, E369, E628, E899, E900, E901 | — | — | — | yes |
| `created_at` | — | E47, E52, E79, E80, E151, E199, E201, E278, E279, E341, E365, E369, E628, E899, E900, E901 | — | E899 | — | yes |
| `supersedes_envelope_id` | E899 | E8, E44, E47, E52, E79, E80, E82, E87, E151, E154, E199, E201, E278, E279, E341, E365, E369, E620, E624, E628, E899, E900, E901, E1552 | — | — | — | yes |

### `ops.engineering_reviewer_fact`

**Definition:** `migrations/0310_engineering_execution_fabric.sql:76`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E903. **Table readers:** E48, E79, E81, E151, E203, E373, E628, E903.
**Unresolved SQL affecting any column:** E203, E621. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E48, E79, E81, E147, E151, E373, E628.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E48, E79, E81, E151, E373, E628, E903 | E628 | E903 | — | unknown |
| `receipt_id`, `reviewer_actor_id` | E903 | E79, E81, E151, E203, E373, E628, E903, E1554 | — | — | — | unknown |
| `work_request_id`, `slice_ref`, `reviewer_session_ref` | E903 | E79, E151, E373, E628, E903, E1554 | — | — | — | unknown |
| `state` | E903 | E79, E151, E373, E628, E903, E1554 | — | — | E1554 | unknown |
| `fact` | E903 | E48, E79, E151, E373, E628, E903, E1554 | — | — | — | unknown |
| `idempotency_key` | E903 | E79, E151, E373, E628, E903 | — | — | — | unknown |
| `created_at` | — | E79, E81, E151, E373, E628, E903 | — | E903 | — | unknown |
| `contract_version` | — | E79, E151, E373, E628, E903, E1554 | — | — | E1554 | unknown |

### `ops.engineering_slice_plan`

**Definition:** `migrations/0310_engineering_execution_fabric.sql:12`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1, E48, E52, E79, E80, E81, E83, E144, E146, E151, E199, E201, E203, E365, E369, E370, E628, E895, E899, E1076, E1404, E1405.
**Unresolved SQL affecting any column:** E203, E621. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E1, E48, E52, E79, E80, E81, E83, E144, E146, E147, E151, E152, E153, E199, E201, E365, E369, E370, E628.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1, E48, E52, E79, E80, E81, E83, E144, E146, E151, E199, E201, E203, E369, E370, E628, E895, E899, E1076, E1404 | E628, E899 | declared; caller unknown | — | yes |
| `work_request_id` | — | E1, E48, E52, E79, E80, E81, E83, E151, E199, E201, E365, E369, E370, E628, E899, E1076, E1404, E1405, E1591 | — | — | — | yes |
| `accepted_plan_id` | — | E1, E48, E52, E79, E80, E83, E151, E199, E201, E365, E369, E370, E628, E895, E899, E1076, E1591 | E899 | — | — | yes |
| `accepted_plan_hash` | — | E48, E52, E79, E80, E83, E151, E199, E201, E369, E370, E628, E899, E1076, E1591 | — | — | — | unknown |
| `work_request_version` | — | E48, E52, E79, E80, E83, E151, E199, E201, E369, E370, E628, E899, E1076, E1404, E1591 | E628 | — | E1591 | unknown |
| `plan_digest` | — | E1, E48, E52, E79, E80, E83, E146, E151, E199, E201, E369, E370, E628, E895, E899, E1076 | — | — | — | yes |
| `plan` | — | E1, E48, E52, E79, E80, E83, E146, E151, E199, E201, E365, E369, E370, E628, E899, E1076, E1404, E1405 | — | — | — | yes |
| `idempotency_key` | — | E48, E52, E79, E80, E83, E151, E199, E201, E369, E370, E628, E899 | — | — | — | unknown |
| `created_at` | — | E48, E52, E79, E80, E83, E151, E199, E201, E369, E370, E628, E899, E1076, E1404 | E1076 | declared; caller unknown | — | unknown |

### `ops.engineering_slice_receipt`

**Definition:** `migrations/0310_engineering_execution_fabric.sql:57`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E47, E48, E79, E81, E82, E151, E201, E203, E278, E365, E369, E373, E628, E902.
**Unresolved SQL affecting any column:** E199, E203, E621. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E47, E48, E79, E81, E82, E147, E151, E152, E201, E278, E365, E369, E373, E628.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E47, E48, E79, E81, E82, E151, E201, E203, E278, E369, E373, E628, E902 | E628 | declared; caller unknown | — | unknown |
| `job_attempt_id`, `work_request_id`, `slice_ref`, `attempt_id` | — | E47, E79, E151, E201, E278, E369, E373, E628, E902 | — | — | — | unknown |
| `envelope_id` | — | E47, E79, E81, E82, E151, E201, E203, E278, E365, E369, E373, E628, E902 | — | — | — | yes |
| `executor_actor_id` | — | E47, E79, E151, E201, E203, E278, E369, E373, E628, E902 | — | — | — | unknown |
| `receipt_digest` | — | E47, E48, E79, E151, E201, E278, E369, E373, E628, E902 | — | — | — | unknown |
| `outcome`, `receipt` | — | E47, E79, E82, E151, E201, E278, E369, E373, E628, E902 | — | — | — | unknown |
| `created_at` | — | E47, E79, E81, E151, E201, E203, E278, E369, E373, E628, E902 | — | declared; caller unknown | — | unknown |

### `ops.engineering_stale_contract_fence`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:8`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E143, E145.
**Unresolved SQL affecting any column:** E329, E719. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E143, E145.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `generation`, `work_request_ref` | — | — | — | — | — | unknown |
| `plan_ref` | — | E143, E145 | — | — | — | yes |
| `plan_hash`, `migration_filenames` | — | E145 | — | — | — | yes |
| `successor_allowed`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `reason` | — | E143 | — | — | — | unknown |

### `ops.execution_envelope_v1`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:305`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E216. **Table readers:** E49, E50, E111, E206, E207, E216, E326, E337, E340, E374, E646, E703.
**Additional lexical/dependency evidence:** E49, E50, E111, E206, E207, E216, E326, E337, E340, E374, E646, E703.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E49, E50, E207, E216, E326, E337, E374, E703 | E340 | E216 | — | unknown |
| `idempotency_key` | E216 | E49, E50, E207, E216, E326, E337 | — | — | — | unknown |
| `organization_tenant_id` | E216 | E49, E50, E206, E207, E216, E326, E337, E374 | E340 | — | — | unknown |
| `work_request_id` | E216 | E49, E50, E207, E216, E326, E337, E374, E1557 | — | — | — | unknown |
| `plan_hash` | E216 | E49, E50, E207, E216, E326, E337, E374 | — | — | — | unknown |
| `activation_binding_id` | E216 | E49, E50, E111, E206, E207, E216, E326, E337, E340, E374 | — | — | — | unknown |
| `envelope_digest` | E216 | E49, E50, E207, E216, E326, E337, E340, E374, E1557 | — | — | E1557 | unknown |
| `envelope` | E216 | E49, E50, E207, E216, E326, E337, E1557 | — | — | E1557 | unknown |
| `runtime_profile` | E216 | E49, E50, E206, E207, E216, E326, E337, E340, E703, E1557 | — | — | E1557 | unknown |
| `execution_topology`, `configuration_digest` | E216 | E49, E50, E207, E216, E326, E337, E340, E1557 | — | — | E1557 | unknown |
| `evaluation_plan` | E216 | E49, E50, E207, E216, E326, E337, E340, E1557 | — | — | — | unknown |
| `issued_at` | E216 | E49, E50, E207, E216, E326, E337, E340 | — | declared; caller unknown | — | unknown |
| `expires_at` | E216 | E49, E50, E111, E206, E207, E216, E326, E337, E340 | — | — | — | unknown |
| `created_at` | — | E49, E50, E207, E216, E326, E337, E646 | — | E216 | — | unknown |

### `ops.execution_environment_conformance`

**Definition:** `migrations/0309_governed_execution_environment_providers.sql:39`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E51. **Table readers:** E51, E69, E206, E215, E342.
**Unresolved SQL affecting any column:** E70, E206, E687. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E51, E69, E215, E342.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E51, E69, E206, E215, E342 | — | E51 | — | unknown |
| `provider_id`, `status`, `observed_at` | E51 | E51, E69, E206, E215, E342 | — | — | — | unknown |
| `contract_ref`, `contract_digest`, `run_ref`, `run_digest`, `manifest_digest`, `implementation_digest`, `package_digest`, `configuration_schema_digest`, `check_refs`, `evidence_refs`, `observation`, `recorded_by_actor_id`, `idempotency_key` | E51 | E51, E69, E206, E342 | — | — | — | unknown |
| `created_at` | — | E51, E69, E206, E342 | — | E51 | — | unknown |

### `ops.execution_environment_provider`

**Definition:** `migrations/0309_governed_execution_environment_providers.sql:10`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E405. **Table readers:** E51, E69, E70, E206, E342, E405, E687.
**Unresolved SQL affecting any column:** E405. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E51, E69, E70, E206, E342, E687.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E51, E69, E70, E206, E342, E405, E687 | — | E405 | — | unknown |
| `provider_key`, `provider_version`, `source_class`, `backend_kind`, `manifest_digest`, `manifest` | E405 | E51, E69, E70, E206, E342, E405, E687 | — | — | — | unknown |
| `protected_builtin`, `created_by_actor_id`, `idempotency_key` | E405 | E51, E69, E70, E206, E405, E687 | — | — | — | unknown |
| `created_at` | — | E51, E69, E70, E206, E405, E687 | — | E405 | — | unknown |

### `ops.execution_environment_provider_event`

**Definition:** `migrations/0309_governed_execution_environment_providers.sql:25`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E405, E687. **Table readers:** E159, E405, E687.
**Additional lexical/dependency evidence:** E159, E405, E687.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E159, E687 | — | E405, E687 | — | unknown |
| `provider_id`, `to_state` | E405, E687 | E159, E687 | — | — | — | unknown |
| `from_state`, `evidence_refs`, `ruled_by_actor_id`, `idempotency_key` | E405, E687 | E687 | — | — | — | unknown |

### `ops.f01_corporate_artifact`

**Definition:** `migrations/0626_f01_record_source_authority.sql:938`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E173. **Table readers:** E173, E179, E181, E182.
**Additional lexical/dependency evidence:** E173, E179, E181, E182.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `artifact_id` | — | E179, E181, E182 | — | — | — | unknown |
| `tenant`, `envelope`, `envelope_digest`, `artifact_digest`, `source_system`, `source_account`, `native_id`, `native_id_epoch`, `native_version`, `content_digest`, `evidence_class`, `observed_at_text`, `observed_at`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E173 | E179, E181, E182 | — | — | — | unknown |

### `ops.f01_deletion_evaluation`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1405`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E174. **Table readers:** E172, E174.
**Additional lexical/dependency evidence:** E172, E174.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `evaluation_id` | — | E172, E174 | — | — | — | unknown |
| `tenant`, `artifact_digest`, `envelope`, `envelope_digest`, `evaluation_digest` | E174 | E172, E174 | — | — | — | unknown |
| `artifact_class`, `decision`, `reason_id`, `receipt_digest`, `policy_digest`, `hold_inventory_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E174 | E174 | — | — | — | unknown |

### `ops.f01_derivative_link`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1124`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E169. **Table readers:** E165, E169.
**Unresolved SQL affecting any column:** E169. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E165.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `derivative_link_id` | — | E165, E169 | — | — | — | unknown |
| `tenant`, `envelope`, `envelope_digest`, `link_digest`, `source_artifact_digest`, `derivative_kind`, `derivative_id`, `derivative_content_digest`, `producer_workflow`, `producer_run_ref`, `produced_at_text`, `produced_at`, `evidence_ref`, `evidence_digest`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E169 | E165, E169 | — | — | — | unknown |

### `ops.f01_document_current`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1312`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E175. **Table readers:** E172, E175.
**Additional lexical/dependency evidence:** E172, E175.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `document_id`, `document_version_id` | E175 | E172, E175 | — | — | — | unknown |
| `document_digest`, `updated_by`, `updated_at` | E175 | E175 | — | — | — | unknown |

### `ops.f01_document_source_provenance`

**Definition:** `migrations/0626_f01_record_source_authority.sql:4343`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E170. **Table readers:** E166, E167, E170.
**Additional lexical/dependency evidence:** E166, E167, E170.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `provenance_id` | — | E166, E167 | — | — | — | unknown |
| `tenant`, `document_id`, `version_no`, `document_digest`, `envelope`, `envelope_digest`, `provenance_digest`, `provenance_state`, `source_artifact_digest`, `derivative_link_digest`, `derivative_kind`, `derivative_id`, `producer_workflow`, `producer_run_ref`, `basis_statement`, `policy_digest`, `actor_slug`, `recorded_at_text`, `recorded_at`, `idempotency_key` | E170 | E166, E167 | — | — | — | unknown |

### `ops.f01_document_version`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1232`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E175. **Table readers:** E172, E175.
**Additional lexical/dependency evidence:** E172, E175.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `document_version_id` | — | E172, E175 | — | — | — | unknown |
| `tenant`, `document_id`, `version_no`, `envelope`, `envelope_digest`, `document_digest` | E175 | E172, E175 | — | — | — | unknown |
| `prior_document_digest`, `document_class`, `content_digest`, `preparation_state`, `delivery_state`, `signature_state`, `validity_state`, `version_state`, `object_key`, `object_sealed`, `onedrive_drive_id`, `onedrive_item_id`, `onedrive_filing_state`, `official_filing_state`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E175 | E175 | — | — | — | unknown |

### `ops.f01_field_event`

**Definition:** `migrations/0626_f01_record_source_authority.sql:755`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E161. **Table readers:** E161, E172.
**Additional lexical/dependency evidence:** E161, E172.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `event_id` | — | — | — | — | — | unknown |
| `tenant`, `entity`, `field`, `event_seq`, `envelope`, `envelope_digest`, `event_digest` | E161 | E172 | — | — | — | unknown |
| `previous_event_digest`, `event_kind`, `source_system`, `observed_at_text`, `observed_at`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E161 | — | — | — | — | unknown |

### `ops.f01_field_state`

**Definition:** `migrations/0626_f01_record_source_authority.sql:710`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E161. **Table readers:** E161, E163.
**Additional lexical/dependency evidence:** E161, E163.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `entity`, `field`, `envelope`, `envelope_digest`, `state_digest`, `value_digest`, `owner_source`, `account`, `native_id`, `native_id_epoch`, `event_seq`, `last_event_digest`, `observed_at_text`, `observed_at`, `policy_digest`, `updated_by`, `updated_at` | E161 | E161, E163 | — | — | — | unknown |

### `ops.f01_idempotency`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1524`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E162, E180. **Table readers:** E162, E178, E180.
**Unresolved SQL affecting any column:** E162, E178. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E180.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `operation`, `idempotency_key` | E162 | E162, E178, E180 | — | — | — | unknown |
| `request_digest`, `actor_slug`, `claimed_at` | E162 | E162, E178 | — | — | — | unknown |
| `result`, `result_digest`, `settled_at` | E180 | E162, E178 | — | — | — | unknown |

### `ops.f01_mutation_receipt`

**Definition:** `migrations/0626_f01_record_source_authority.sql:853`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E161. **Table readers:** E161, E172.
**Additional lexical/dependency evidence:** E161, E172.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `receipt_id` | — | E172 | — | — | — | unknown |
| `tenant`, `entity`, `field`, `envelope`, `envelope_digest`, `receipt_digest` | E161 | E172 | — | — | — | unknown |
| `transition_digest`, `event_digest`, `policy_digest`, `reason_id`, `actor_slug`, `recorded_at`, `idempotency_key` | E161 | — | — | — | — | unknown |

### `ops.f01_parsed_proposal`

**Definition:** `migrations/0626_f01_record_source_authority.sql:992`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E177. **Table readers:** E177.
**Additional lexical/dependency evidence:** E177.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `proposal_id` | — | — | — | — | — | unknown |
| `tenant`, `envelope`, `envelope_digest`, `proposal_digest`, `artifact_digest`, `source_system`, `source_account`, `confidence`, `observed_at_text`, `observed_at`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E177 | — | — | — | — | unknown |

### `ops.f01_policy_current`

**Definition:** `migrations/0626_f01_record_source_authority.sql:699`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E171. **Table readers:** E164, E171.
**Additional lexical/dependency evidence:** E164, E171.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `policy_seq`, `policy_digest`, `updated_by`, `updated_at` | E171 | E164, E171 | — | — | — | unknown |

### `ops.f01_policy_version`

**Definition:** `migrations/0626_f01_record_source_authority.sql:630`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E171. **Table readers:** E164, E171.
**Additional lexical/dependency evidence:** E164, E171.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `policy_seq` | — | E164, E171 | — | — | — | unknown |
| `tenant`, `registry_version`, `envelope`, `envelope_digest`, `policy_digest`, `prior_policy_digest`, `field_registry_digest`, `retention_registry_digest`, `domain_policy_digest`, `installed_by`, `installed_at_text`, `installed_at`, `idempotency_key` | E171 | E164 | — | — | — | unknown |

### `ops.f01_preservation_hold_current`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1388`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E176. **Table readers:** E168, E176.
**Additional lexical/dependency evidence:** E168, E176.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `hold_id`, `artifact_digest`, `hold_event_id`, `hold_digest`, `hold_state`, `hold_seq`, `updated_by`, `updated_at` | E176 | E168, E176 | — | — | — | unknown |

### `ops.f01_preservation_hold_event`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1325`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E176. **Table readers:** E168, E172, E176.
**Additional lexical/dependency evidence:** E168, E172, E176.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `hold_event_id` | — | E168, E176 | — | — | — | unknown |
| `tenant`, `hold_id`, `hold_seq`, `envelope`, `envelope_digest`, `hold_digest` | E176 | E168, E172, E176 | — | — | — | unknown |
| `artifact_digest`, `prior_hold_digest`, `hold_state`, `placed_at_text`, `released_at_text`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E176 | E168, E176 | — | — | — | unknown |

### `ops.f01_proposal_link`

**Definition:** `migrations/0626_f01_record_source_authority.sql:1033`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E177. **Table readers:** E172, E177.
**Unresolved SQL affecting any column:** E177. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E172.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `link_id` | — | E172 | — | — | — | unknown |
| `tenant`, `envelope`, `envelope_digest`, `link_digest`, `artifact_digest` | E177 | E172 | — | — | — | unknown |
| `proposal_digest`, `supersedes_link_digest`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E177 | — | — | — | — | unknown |

### `ops.f01_reconciliation_item`

**Definition:** `migrations/0626_f01_record_source_authority.sql:898`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E161. **Table readers:** E161, E172.
**Additional lexical/dependency evidence:** E161, E172.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `item_id` | — | E172 | — | — | — | unknown |
| `tenant`, `entity`, `field`, `envelope`, `envelope_digest`, `item_digest` | E161 | E172 | — | — | — | unknown |
| `conflict_kind`, `human_resolver_class`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E161 | — | — | — | — | unknown |

### `ops.f01_state_transition`

**Definition:** `migrations/0626_f01_record_source_authority.sql:812`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E161. **Table readers:** E161, E172.
**Additional lexical/dependency evidence:** E161, E172.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `transition_id` | — | E172 | — | — | — | unknown |
| `tenant`, `entity`, `field`, `envelope`, `envelope_digest`, `transition_digest` | E161 | E172 | — | — | — | unknown |
| `from_value_digest`, `to_value_digest`, `event_digest`, `policy_digest`, `actor_slug`, `recorded_at`, `idempotency_key` | E161 | — | — | — | — | unknown |

### `ops.foundation_assurance_evidence`

**Definition:** `migrations/0511_foundation_assurance_minimum_outcome.sql:214`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E192. **Table readers:** E187, E190, E191, E192.
**Additional lexical/dependency evidence:** E187, E190, E191, E192.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `evidence_digest`, `evidence_ref`, `benchmark_payload_digest`, `source_sha`, `source_tree`, `staging_provider_version`, `final_provider_version`, `release_id`, `config`, `evidence`, `seal`, `idempotency_key`, `stored_by_actor_id`, `stored_by_session_ref` | E192 | E187, E190, E191, E192 | — | — | — | unknown |
| `stored_at` | — | E187, E190, E191, E192 | — | E192 | — | unknown |

### `ops.foundation_assurance_production`

**Definition:** `migrations/0511_foundation_assurance_minimum_outcome.sql:235`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E191. **Table readers:** E190, E191.
**Unresolved SQL affecting any column:** E191. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E190.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | E191 | E191 | — | declared; caller unknown | — | unknown |
| `idempotency_key`, `actor_id`, `session_ref`, `artifact_digest`, `admission_id`, `event_id`, `result` | E191 | E191 | — | — | — | unknown |
| `verb`, `kind`, `evidence_digest`, `artifact` | E191 | E190, E191 | — | — | — | unknown |
| `recorded_at` | — | E191 | — | E191 | — | unknown |

### `ops.gate_zero_read_only_outcome`

**Definition:** `migrations/0502_gate_zero_read_only_outcome.sql:331`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E195. **Table readers:** E60, E195.
**Additional lexical/dependency evidence:** E60, E195.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `recorded_at` | — | E60, E195 | — | E195 | — | unknown |
| `idempotency_key`, `step_ref`, `receipt_producer_step_ref`, `gate_id`, `receipt_schema`, `producer_role`, `independent_oracle_ref`, `oracle_version`, `evidence_scope`, `subject_environment`, `negative_admission_result`, `producing_seat_ref`, `producing_actor_id`, `candidate_digest`, `subject_digest`, `policy_digest`, `environment_manifest_digest`, `fixture_set_digest`, `evidence_ref`, `receipt`, `outcome_digest`, `candidate_scoped_digest`, `status`, `comparator`, `observed_at`, `ttl_expires_at` | E195 | E60, E195 | — | — | — | unknown |

### `ops.guidance_authority_binding`

**Definition:** `migrations/0168_guidance_registry.sql:63`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E381. **Table readers:** E381, E705, E707.
**Unresolved SQL affecting any column:** E12. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E381, E705, E707.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E381, E705, E707 | — | E381 | — | unknown |
| `guidance_revision_id` | E381 | E705, E707, E1561 | — | — | — | unknown |
| `authority_receipt_id` | E381 | E381, E705, E707, E1561 | — | — | — | unknown |
| `contract_hash` | E381 | E1561 | — | — | — | unknown |
| `created_at` | — | — | — | E381 | — | unknown |

### `ops.guidance_import_apply_event`

**Definition:** `migrations/0170_guidance_import_lifecycle.sql:91`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E33. **Table readers:** E11, E33.
**Unresolved SQL affecting any column:** E33, E38, E129. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E11.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E33 | — | E33 | — | unknown |
| `batch_id` | E33 | E11, E33 | — | — | — | unknown |
| `manifest_digest`, `idempotency_key`, `applied_by`, `reason` | E33 | E33 | — | — | — | unknown |

### `ops.guidance_import_batch`

**Definition:** `migrations/0170_guidance_import_lifecycle.sql:54`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E653. **Table readers:** E11, E33, E129, E343, E653.
**Unresolved SQL affecting any column:** E37, E653. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E11, E33, E129, E343.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E11, E33, E129, E343, E653 | — | E653 | — | unknown |
| `manifest_digest` | E653 | E11, E33, E129, E343, E653 | — | — | — | unknown |
| `canonical_manifest_text`, `manifest_json`, `source_manifest_digest`, `classifier_actor_id` | E653 | E33, E129, E653 | — | — | — | unknown |
| `staging_key`, `reason` | E653 | E33, E129, E343, E653 | — | — | — | unknown |
| `created_at` | — | E33, E129, E343 | — | E653 | — | unknown |

### `ops.guidance_import_decision_event`

**Definition:** `migrations/0170_guidance_import_lifecycle.sql:113`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E129. **Table readers:** E11, E129, E343.
**Unresolved SQL affecting any column:** E38. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E11, E129, E343.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E129 | — | E129 | — | unknown |
| `batch_id` | E129 | E11, E129, E343 | — | — | — | unknown |
| `manifest_digest`, `state` | E129 | E11, E129 | — | — | — | unknown |
| `idempotency_key`, `authority_actor_id`, `reason` | E129 | E129 | — | — | — | unknown |

### `ops.guidance_import_entry`

**Definition:** `migrations/0170_guidance_import_lifecycle.sql:66`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E653. **Table readers:** E33, E129, E343, E653.
**Unresolved SQL affecting any column:** E37, E38. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E33, E129, E343, E653.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E33, E129 | — | E653 | — | unknown |
| `batch_id` | E653 | E33, E129, E343 | — | — | — | unknown |
| `ordinal`, `guidance_id`, `source_rule_id`, `source_clause`, `is_primary`, `split_group_key`, `guidance_type`, `scope`, `activation`, `consumer`, `verification`, `provenance`, `delivery`, `is_constitution`, `revision_reason` | E653 | E33, E129 | — | — | — | unknown |
| `situation_mappings` | E653 | E33, E129 | — | declared; caller unknown | — | unknown |

### `ops.guidance_import_mapping_execution`

**Definition:** `migrations/0170_guidance_import_lifecycle.sql:101`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E33, E129. **Table readers:** E33, E129.
**Unresolved SQL affecting any column:** E38, E129. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E33.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E129 | — | E33, E129 | — | unknown |
| `batch_id`, `entry_id`, `ordinal`, `proposed_mapping_id` | E33, E129 | E33, E129 | — | — | — | unknown |
| `concept_id`, `doctrine_section_id` | E33, E129 | E129 | — | — | — | unknown |
| `active_mapping_id` | E129 | E129 | — | — | — | unknown |

### `ops.guidance_intake`

**Definition:** `migrations/0148_control_plane_admission.sql:8`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E35, E1284, E1287, E1288, E1491. **Table readers:** E34, E35, E1284, E1286, E1287, E1288, E1491.
**Additional lexical/dependency evidence:** E34, E35.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E34, E35, E1286, E1287, E1288, E1491 | — | E35, E1284, E1287, E1491 | — | unknown |
| `lane`, `source_ref` | E35, E1284, E1287, E1491 | E34, E35, E1286, E1491 | — | — | — | unknown |
| `source_kind`, `statement`, `captured_by` | E35, E1284, E1287, E1491 | — | — | — | — | unknown |
| `state` | E35, E1284, E1287, E1288, E1491 | E1491 | — | declared; caller unknown | — | unknown |
| `normalized_contract` | E35, E1288, E1491 | E1491 | — | — | — | unknown |
| `captured_at` | — | E34, E35, E1286 | — | E35, E1284, E1287, E1491 | — | unknown |
| `updated_at` | E35, E1288, E1491 | — | — | E35, E1284, E1287, E1491 | — | unknown |
| `version` | E35, E1288, E1491 | E35, E1288, E1491 | — | E35, E1284, E1287, E1491 | — | unknown |

### `ops.guidance_item`

**Definition:** `migrations/0168_guidance_registry.sql:23`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E33. **Table readers:** E33, E129, E706, E1677.
**Unresolved SQL affecting any column:** E33, E38, E706. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E129.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E33, E129, E706, E1677 | — | E33 | — | unknown |
| `source_rule_id` | E33 | E33, E129, E706, E1677 | — | — | — | unknown |
| `guidance_intake_id` | — | E1677 | — | — | — | unknown |
| `source_clause` | E33 | E33, E129, E1677 | — | — | — | unknown |
| `is_primary` | E33 | E1677 | — | declared; caller unknown | — | unknown |
| `split_group_id` | E33 | E1677 | — | — | — | unknown |
| `created_by` | E33 | — | — | — | — | unknown |
| `created_at` | — | — | — | E33 | — | unknown |

### `ops.guidance_lifecycle_event`

**Definition:** `migrations/0168_guidance_registry.sql:73`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E381. **Table readers:** E129, E381, E1681.
**Additional lexical/dependency evidence:** E129, E381.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E129, E381, E1681 | — | E381 | — | unknown |
| `event_seq`, `supersedes_event_id` | — | E1681 | — | — | — | unknown |
| `guidance_revision_id` | E381 | E1562, E1681 | — | — | — | unknown |
| `state` | E381 | E381, E1562, E1681 | — | — | E1562 | unknown |
| `authority_binding_id` | E381 | E129, E381, E1562, E1681 | — | — | — | unknown |
| `reason` | E381 | E1681 | — | — | — | unknown |
| `created_at` | — | E1681 | — | E381 | — | unknown |

### `ops.guidance_registry`

**Definition:** `migrations/0168_guidance_registry.sql:16`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E11, E127, E129, E655, E830, E1675, E1680, E1682.
**Additional lexical/dependency evidence:** E11, E127, E129, E655.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E11, E127, E655, E1675, E1680, E1682 | — | declared; caller unknown | — | unknown |
| `singleton` | — | E129, E655, E1675, E1682 | — | declared; caller unknown | — | unknown |
| `created_by` | — | E11, E127, E129 | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.guidance_registry_event`

**Definition:** `migrations/0168_guidance_registry.sql:101`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E11, E127. **Table readers:** E11, E127, E1680.
**Additional lexical/dependency evidence:** E11, E127.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E11, E127, E1680 | — | E11, E127 | — | unknown |
| `event_seq` | — | E1680 | — | — | — | unknown |
| `registry_id`, `state` | E11, E127 | E1680 | — | — | — | unknown |
| `authority_receipt_id`, `manifest_digest`, `reason` | E11, E127 | E11, E127, E1680 | — | — | — | unknown |
| `created_at` | — | E1680 | — | E11, E127 | — | unknown |

### `ops.guidance_revision`

**Definition:** `migrations/0168_guidance_registry.sql:43`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E33. **Table readers:** E33, E129, E204, E381, E704, E706, E707, E1681.
**Unresolved SQL affecting any column:** E33, E38, E327. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E129, E204, E381, E704, E706, E707.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E33, E129, E204, E381, E704, E706, E707, E1681 | — | E33 | — | unknown |
| `guidance_item_id` | E33 | E33, E129, E204, E381, E704, E706, E1563, E1681 | — | — | — | unknown |
| `version` | E33 | E33, E129, E204, E706, E1563, E1681 | — | — | — | unknown |
| `guidance_type` | E33 | E204, E707, E1563, E1681 | — | — | E1563 | unknown |
| `scope`, `activation`, `verification`, `provenance`, `delivery` | E33 | E204, E1563, E1681 | — | — | — | unknown |
| `consumer`, `reason` | E33 | E204, E1681 | — | — | — | unknown |
| `is_constitution` | E33 | E204, E1563, E1681 | — | declared; caller unknown | — | unknown |
| `supersedes_revision_id` | — | E204, E1563, E1681 | — | — | — | unknown |
| `classified_by` | E33 | E1681 | — | — | — | unknown |
| `created_at` | — | E1681 | — | E33 | — | unknown |

### `ops.guidance_situation_mapping`

**Definition:** `migrations/0168_guidance_registry.sql:85`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E12, E327. **Table readers:** E12, E327, E1678.
**Additional lexical/dependency evidence:** E12, E327.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E12, E327, E1678 | — | E12, E327 | — | unknown |
| `mapping_seq` | — | E12, E1678 | — | — | — | unknown |
| `guidance_revision_id` | E12, E327 | E12, E1564, E1678 | — | — | — | unknown |
| `concept_id`, `doctrine_section_id`, `reason` | E12, E327 | E12, E1678 | — | — | — | unknown |
| `state` | E12, E327 | E12, E1564, E1678 | — | — | E1564 | unknown |
| `authority_binding_id` | E12 | E12, E1564, E1678 | — | — | — | unknown |
| `supersedes_mapping_id` | E12 | E12, E1678 | — | — | — | unknown |
| `created_at` | — | E12, E1678 | — | E12, E327 | — | unknown |

### `ops.incident`

**Definition:** `migrations/0115_ops_observability.sql:317`. **Product path:** positive static evidence (column detail below).

**Table writers:** E101, E916, E1393, E1396, E1452, E1455, E1459, E1462, E1482, E1486. **Table readers:** E101, E633, E713, E916, E917, E918, E923, E931, E932, E934, E941, E945, E946, E1081, E1391, E1392, E1393, E1396, E1451, E1452, E1454, E1455, E1456, E1457, E1459, E1460, E1461, E1462, E1463, E1477, E1478, E1479, E1480, E1481, E1482, E1483, E1486, E1692.
**Unresolved SQL affecting any column:** E132, E634, E715, E716, E914, E915, E921, E924, E927, E933, E936, E947, E1487. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E101, E633, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E101, E633, E713, E916, E918, E923, E931, E932, E945, E1391, E1393, E1396, E1478, E1479, E1480, E1481, E1482, E1483, E1486, E1692 | E1456, E1463, E1483 | E1393, E1482 | — | yes |
| `ref` | E1393, E1482 | E101, E713, E916, E917, E918, E923, E931, E932, E934, E941, E945, E946, E1081, E1392, E1451, E1452, E1454, E1455, E1456, E1457, E1459, E1460, E1461, E1462, E1463, E1477, E1692 | — | — | — | yes |
| `correlation_id` | E1393, E1482 | E713, E1478, E1480, E1483, E1692 | — | declared; caller unknown | — | yes |
| `title` | E1393, E1482 | E713, E941, E1081, E1457, E1460, E1692 | — | — | — | yes |
| `severity` | E1393, E1482 | E101, E713, E941, E945, E1451, E1460 | — | — | — | yes |
| `state` | E101, E916, E1393, E1396, E1452, E1455, E1459, E1482, E1486 | E101, E633, E713, E916, E917, E923, E931, E934, E941, E945, E1081, E1391, E1396, E1451, E1452, E1454, E1457, E1459, E1460, E1461, E1478, E1479, E1480, E1481, E1486, E1692 | — | declared; caller unknown | — | yes |
| `environment` | E1393, E1482 | E101, E713, E1457, E1460, E1692 | — | — | — | yes |
| `owner_actor` | E1393, E1482 | E945, E1081 | — | — | — | unknown |
| `next_action` | E101, E916, E1393, E1396, E1452, E1455, E1459, E1482, E1486 | E916, E1396, E1460, E1486 | — | — | — | unknown |
| `business_impact` | E916 | E916 | — | — | — | unknown |
| `banner_state`, `followup_disposition` | — | — | — | — | — | unknown |
| `detected_source` | E1393, E1482 | — | — | — | — | unknown |
| `detected_at` | E1393, E1482 | E713, E1081, E1451, E1460, E1478, E1480, E1692 | — | declared; caller unknown | — | yes |
| `monitoring_until` | E101, E1396, E1452, E1455, E1459, E1486 | E101, E934, E1396, E1454, E1457, E1486 | — | — | — | unknown |
| `recovery_evidence_ref` | E101, E1396, E1452, E1455, E1459, E1462, E1486 | E1396, E1454, E1457, E1461, E1462, E1486 | — | — | — | unknown |
| `resolved_at` | E101, E1455, E1459 | E713, E1081 | — | — | — | yes |
| `root_cause` | E101, E1455, E1459 | E931 | — | — | — | unknown |
| `reviewed_at` | — | E1081 | — | — | — | unknown |
| `source_kind` | E1393, E1482 | E101, E1451, E1692 | — | — | — | yes |
| `source_ref` | E1393, E1482 | E1692 | — | — | — | yes |
| `observed_at` | E101, E1393, E1396, E1452, E1482, E1486 | E713, E1081, E1692 | — | declared; caller unknown | — | yes |
| `expires_at` | E1393, E1396, E1482 | E1692 | E633 | — | — | yes |
| `signature` | E1393, E1482 | E101, E945, E1391, E1451, E1457, E1479, E1481 | — | — | — | unknown |
| `duplicate_of_id` | — | E923, E941 | — | — | — | unknown |
| `occurrence_count` | E1482, E1486 | E1451, E1460, E1486 | — | E1393 | — | unknown |
| `first_seen_at` | E1482 | — | — | — | — | unknown |
| `last_seen_at` | E101, E1482, E1486 | E101, E1081, E1460 | — | — | — | unknown |

### `ops.incident_fact`

**Definition:** `migrations/0115_ops_observability.sql:383`. **Product path:** positive static evidence (column detail below).

**Table writers:** E101, E913, E922, E925, E935, E1395, E1456, E1463, E1483, E1485. **Table readers:** E101, E713, E912, E913, E922, E925, E930, E935, E937, E942, E943, E1394, E1395, E1456, E1463, E1478, E1480, E1483, E1485, E1692.
**Unresolved SQL affecting any column:** E926. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E101, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E713, E1692 | E1456, E1463, E1483 | E101, E913, E922, E925, E935, E1395, E1456, E1463, E1483, E1485 | — | yes |
| `incident_id` | E101, E913, E922, E925, E935, E1395, E1456, E1463, E1483, E1485 | E713, E912, E930, E937, E942, E943, E1394, E1478, E1480, E1483, E1692 | — | — | — | yes |
| `text` | E101, E913, E922, E925, E935, E1395, E1456, E1463, E1483, E1485 | E713, E930, E937, E1692 | — | — | — | yes |
| `source_ref` | E101, E913, E922, E925, E935, E1395, E1456, E1463, E1483, E1485 | E713, E912, E930, E937, E943, E1394, E1478, E1480, E1483, E1692 | — | — | — | yes |
| `recorded_at` | — | E713, E930, E937, E943, E1692 | — | E101, E913, E922, E925, E935, E1395, E1456, E1463, E1483, E1485 | — | yes |

### `ops.incident_hypothesis`

**Definition:** `migrations/0115_ops_observability.sql:391`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E938.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | declared; caller unknown | — | unknown |
| `incident_id`, `text`, `confidence`, `settled_as` | — | E938 | — | — | — | unknown |
| `recorded_at` | — | E938 | — | declared; caller unknown | — | unknown |

### `ops.incident_link`

**Definition:** `migrations/0115_ops_observability.sql:406`. **Product path:** positive static evidence (column detail below).

**Table writers:** E911, E920, E1484. **Table readers:** E633, E713, E911, E919, E920, E928, E929, E939, E1484.
**Unresolved SQL affecting any column:** E132, E634, E716, E926. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E633, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `incident_id` | E911, E920, E1484 | E633, E713, E911, E919, E920, E928, E929, E939, E1484 | — | — | — | yes |
| `kind`, `ref` | E911, E920, E1484 | E633, E713, E919, E928, E929, E939 | — | — | — | yes |
| `note` | E911, E920, E1484 | E939 | — | — | — | unknown |

### `ops.incident_service`

**Definition:** `migrations/0115_ops_observability.sql:374`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E910. **Table readers:** E910, E940.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `incident_id`, `service_id` | E910 | E940 | — | — | — | unknown |

### `ops.j102_correction_receipt`

**Definition:** `migrations/0704_cre_lifecycle.sql:715`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E226. **Table readers:** E225, E226.
**Additional lexical/dependency evidence:** E225, E226.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `subject_kind`, `subject_id`, `envelope`, `envelope_digest`, `receipt_digest` | E226 | E225 | — | — | — | unknown |
| `receipt_seq` | — | E225, E226 | — | — | — | unknown |
| `correction_record_id`, `reason`, `prior_state_digest`, `corrected_by`, `corrected_at`, `idempotency_key` | E226 | — | — | — | — | unknown |

### `ops.j102_evidence_subject_link`

**Definition:** `migrations/0704_cre_lifecycle.sql:611`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E227. **Table readers:** E220, E225, E227.
**Additional lexical/dependency evidence:** E220, E225, E227.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `subject_kind`, `subject_id`, `envelope`, `envelope_digest`, `link_digest` | E227 | E220, E225 | — | — | — | unknown |
| `link_seq` | — | E220, E225, E227 | — | — | — | unknown |
| `evidence_source`, `evidence_ref`, `version_no`, `content_digest`, `associated_by`, `associated_by_class`, `associated_at`, `idempotency_key` | E227 | E220 | — | — | — | unknown |

### `ops.j102_first_party_record`

**Definition:** `migrations/0704_cre_lifecycle.sql:504`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E228. **Table readers:** E221, E228.
**Additional lexical/dependency evidence:** E221, E228.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `record_kind`, `record_id`, `envelope`, `envelope_digest`, `record_digest`, `bound_subject_kind`, `bound_subject_id`, `closing_date`, `recorded_by`, `recorded_by_class`, `recorded_at`, `idempotency_key` | E228 | E221 | — | — | — | unknown |

### `ops.j102_idempotency`

**Definition:** `migrations/0704_cre_lifecycle.sql:799`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E219, E233. **Table readers:** E219, E231, E233.
**Unresolved SQL affecting any column:** E219. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E231, E233.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `operation`, `idempotency_key` | E219 | E219, E231, E233 | — | — | — | unknown |
| `request_digest`, `actor_slug`, `claimed_at` | E219 | E219, E231 | — | — | — | unknown |
| `result`, `result_digest`, `settled_at` | E233 | E219, E231 | — | — | — | unknown |

### `ops.j102_migration_shadow_run`

**Definition:** `migrations/0704_cre_lifecycle.sql:5531`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E232. **Table readers:** E223, E232.
**Additional lexical/dependency evidence:** E223, E232.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `compared_rows`, `matching_rows`, `differing_rows`, `unlinked_rows`, `many_to_one_subjects`, `subjects_without_legacy_row`, `legacy_snapshot_digest`, `projection_snapshot_digest`, `clean`, `envelope`, `envelope_digest`, `run_digest`, `recorded_by`, `recorded_at`, `idempotency_key` | E232 | E223 | — | — | — | unknown |
| `run_seq` | — | E223, E232 | — | — | — | unknown |

### `ops.j102_reconciliation_item`

**Definition:** `migrations/0704_cre_lifecycle.sql:747`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E229. **Table readers:** E225, E229.
**Additional lexical/dependency evidence:** E225, E229.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `subject_kind`, `subject_id`, `envelope`, `envelope_digest`, `item_digest` | E229 | E225 | — | — | — | unknown |
| `item_seq` | — | E225, E229 | — | — | — | unknown |
| `conflict_kind`, `base_version_digest`, `current_version_digest`, `proposed_by`, `recorded_at` | E229 | — | — | — | — | unknown |

### `ops.j102_salesforce_reference`

**Definition:** `migrations/0704_cre_lifecycle.sql:677`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E230. **Table readers:** E224, E225, E230, E232.
**Additional lexical/dependency evidence:** E224, E225, E230, E232.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `opportunity_id` | E230 | E224, E225, E232 | — | — | — | unknown |
| `reference_seq` | — | E224, E225, E230, E232 | — | — | — | unknown |
| `opportunity_name`, `opportunity_phase`, `observed_at`, `recorded_by`, `recorded_at`, `idempotency_key` | E230 | — | — | — | — | unknown |
| `linked_subject_kind`, `linked_subject_id` | E230 | E224, E232 | — | — | — | unknown |
| `envelope`, `envelope_digest`, `reference_digest` | E230 | E225 | — | — | — | unknown |

### `ops.j102_subject_current`

**Definition:** `migrations/0704_cre_lifecycle.sql:322`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E218, E222. **Table readers:** E218, E222, E224, E229, E232, E234, E822.
**Unresolved SQL affecting any column:** E222. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E218, E224, E229, E232, E234.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `subject_kind`, `subject_id` | E218, E222 | E218, E222, E224, E229, E232, E234, E822 | — | — | — | unknown |
| `envelope` | E218, E222 | E218, E222, E229, E234, E822 | — | — | — | unknown |
| `envelope_digest`, `deal_state`, `updated_by`, `sponsoring_partner`, `updated_at` | E218, E222 | E218, E234 | — | — | — | unknown |
| `state_digest` | E218, E222 | E218, E224, E234 | — | — | — | unknown |
| `parent_id` | E218, E222 | E218, E234, E822 | — | — | — | unknown |

### `ops.j102_subject_event`

**Definition:** `migrations/0704_cre_lifecycle.sql:416`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E218, E222. **Table readers:** E218, E222, E225, E229.
**Additional lexical/dependency evidence:** E218, E222, E225, E229.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `tenant`, `subject_kind`, `subject_id`, `event_digest` | E218, E222 | E225, E229 | — | — | — | unknown |
| `event_seq` | — | E225, E229 | — | — | — | unknown |
| `event_kind`, `transition_id`, `recorded_by`, `sponsoring_partner`, `recorded_at`, `idempotency_key` | E218, E222 | — | — | — | — | unknown |
| `envelope`, `envelope_digest` | E218, E222 | E225 | — | — | — | unknown |

### `ops.j1_clock`

**Definition:** `migrations/0509_journey_one_clock_store.sql:354`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E236. **Table readers:** E235, E236, E238, E239, E240, E242, E245, E246, E249, E250.
**Unresolved SQL affecting any column:** E236. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E235, E238, E239, E240, E242, E245, E246, E249, E250.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E235, E236, E238, E239, E240, E245, E246, E249 | — | E236 | — | unknown |
| `clock_key` | E236 | E235, E236, E239, E240, E242, E245, E246, E249, E250 | — | — | — | unknown |
| `tenant`, `clock_ref` | E236 | E235, E236, E240, E245, E246, E250 | — | — | — | unknown |
| `state_schema_version`, `origin_receipt_digest`, `origin_at`, `origin_benchmark_manifest_digest` | E236 | E235, E236, E240, E245, E250 | — | — | — | unknown |
| `created_by_actor_id` | E236 | E235, E236, E240, E245 | — | — | — | unknown |
| `created_at` | — | E235, E236, E240, E245, E250 | — | E236 | — | unknown |

### `ops.j1_clock_revision`

**Definition:** `migrations/0509_journey_one_clock_store.sql:423`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E236. **Table readers:** E235, E236, E238, E239, E240, E241, E243, E245, E246, E248, E249.
**Unresolved SQL affecting any column:** E238, E240. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E235, E236, E239, E241, E243, E245, E246, E248, E249.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E235, E236, E238, E239, E240, E241, E243, E245, E246, E248, E249, E1565 | — | E236 | — | unknown |
| `clock_id`, `revision_ordinal` | E236 | E235, E236, E238, E239, E240, E245, E246, E248, E249, E1565 | — | — | — | unknown |
| `idempotency_key` | E236 | E235, E236, E240, E243, E245, E248 | — | — | — | unknown |
| `prior_history_digest`, `written_by_actor_id` | E236 | E235, E236, E240, E245, E246, E248, E1565 | — | — | — | unknown |
| `history_digest` | E236 | E235, E236, E240, E241, E245, E246, E248 | — | — | — | unknown |
| `state_schema_version` | E236 | E235, E236, E240, E245, E246, E248, E1565 | — | — | E1565 | unknown |
| `origin_receipt_digest`, `origin_at`, `origin_benchmark_manifest_digest`, `origin_receipt_ttl_policy_ms`, `base_deadline_at`, `base_deadline_resolution`, `miss_at`, `completion_receipt_digest`, `completion_observed_at`, `completion_receipt_ttl_policy_ms`, `completion_artifact_digest`, `completion_fixture_set_digest`, `evaluated_at` | E236 | E235, E236, E240, E245, E248, E1565 | — | — | — | unknown |
| `current_benchmark_manifest_digest`, `due_at`, `paused_ms`, `status` | E236 | E235, E236, E240, E245, E248 | — | — | — | unknown |
| `computed_by`, `projection_schema_version`, `verifier_ref`, `input_authority` | E236 | E235, E236, E240, E245, E246, E248 | — | — | — | unknown |
| `recorded_at` | — | E235, E236, E240, E245, E246, E248, E1565 | — | E236 | — | unknown |

### `ops.j1_clock_revision_event`

**Definition:** `migrations/0509_journey_one_clock_store.sql:576`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E236. **Table readers:** E235, E236, E244, E245.
**Additional lexical/dependency evidence:** E235, E236, E244, E245.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E236 | — | unknown |
| `revision_id`, `ordinal`, `event_digest` | E236 | E235, E244, E245 | — | — | — | unknown |
| `event_type`, `happened_at`, `kernel_recorded_at`, `evidence_digest`, `previous_event_digest` | E236 | E244, E245 | — | — | — | unknown |

### `ops.j1_clock_revision_pause_interval`

**Definition:** `migrations/0509_journey_one_clock_store.sql:557`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E236. **Table readers:** E236, E245, E247.
**Additional lexical/dependency evidence:** E236, E245, E247.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E236 | — | unknown |
| `revision_id`, `ordinal` | E236 | E245, E247 | — | — | — | unknown |
| `pause_id`, `ends_at` | E236 | E247 | — | — | — | unknown |

### `ops.j1_clock_scope_binding`

**Definition:** `migrations/0509_journey_one_clock_store.sql:392`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E237. **Table readers:** E235, E237, E250, E251, E252, E253.
**Unresolved SQL affecting any column:** E240. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E235, E237, E250, E251, E252, E253.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E235, E237, E251, E252 | — | E237 | — | unknown |
| `clock_scope_key` | E237 | E235, E237, E250, E251, E252, E253 | — | — | — | unknown |
| `clock_key` | E237 | E235, E237, E250, E251, E252 | — | — | — | unknown |
| `clock_scope`, `tenant` | E237 | E235, E237, E251 | — | — | — | unknown |
| `clock_scope_ref` | E237 | E235, E237, E250, E251 | — | — | — | unknown |
| `bound_by_actor_id` | E237 | E235, E237 | — | — | — | unknown |
| `bound_at` | — | E235, E237, E251 | — | E237 | — | unknown |

### `ops.j1_minimum_admission`

**Definition:** `migrations/0510_journey_one_clock_input_store.sql:310`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E191, E257. **Table readers:** E191, E254, E255, E256, E257, E258, E259, E260.
**Unresolved SQL affecting any column:** E191, E257. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E254, E255, E256, E258, E259, E260.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E191, E254, E255, E256, E257, E258, E259, E260, E1566 | — | E191, E257 | — | unknown |
| `inventory_id`, `admission_ordinal` | E191, E257 | E255, E256, E257, E258, E259, E260, E1566 | — | — | — | unknown |
| `idempotency_key` | E191, E257 | E254, E257, E258, E260 | — | — | — | unknown |
| `prior_admission_digest`, `admission_digest`, `receipt`, `receipt_digest`, `admitted_at`, `gate_id`, `receipt_producer_step_ref`, `observed_at`, `ttl_expires_at`, `status`, `minimum_receipt_ttl_policy_ms`, `minimum_environment_manifest_digest`, `written_by_actor_id` | E191, E257 | E255, E257, E258, E260, E1566 | — | — | — | unknown |
| `receipt_schema_ref`, `source_ref`, `input_authority` | E191, E257 | E255, E257, E258, E260 | — | — | — | unknown |
| `recorded_at` | — | E255, E257, E258, E260, E1566 | — | E191, E257 | — | unknown |

### `ops.j1_minimum_inventory`

**Definition:** `migrations/0510_journey_one_clock_input_store.sql:278`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E191, E263. **Table readers:** E191, E255, E256, E257, E258, E259, E260, E261, E262, E263.
**Unresolved SQL affecting any column:** E257. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E191, E255, E256, E258, E259, E260, E261, E262, E263.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E191, E255, E256, E257, E258, E259, E260, E263 | — | E191, E263 | — | unknown |
| `clock_scope_key` | E191, E263 | E191, E255, E256, E257, E258, E259, E260, E261, E262, E263 | — | — | — | unknown |
| `clock_scope`, `minimum_receipt_ttl_policy_ms`, `minimum_environment_manifest_digest` | E191, E263 | E257, E258, E260, E261, E263 | — | — | — | unknown |
| `clock_scope_ref`, `tenant` | E191, E263 | E255, E257, E258, E260, E261, E263 | — | — | — | unknown |
| `opened_by_actor_id` | E191, E263 | E257, E258, E260, E263 | — | — | — | unknown |
| `opened_at` | — | E257, E258, E260, E261, E263 | — | E191, E263 | — | unknown |

### `ops.job`

**Definition:** `migrations/0149_control_plane_jobs.sql:109`. **Product path:** positive static evidence (column detail below).

**Table writers:** E94, E95, E96, E106, E144, E149, E154, E184, E185, E205, E367, E635, E660. **Table readers:** E14, E47, E52, E94, E95, E96, E104, E106, E144, E146, E149, E154, E184, E185, E197, E203, E205, E210, E213, E214, E279, E335, E341, E365, E367, E378, E385, E411, E423, E616, E619, E624, E625, E628, E635, E640, E641, E646, E660, E760, E889, E1009, E1447, E1502, E1683, E1684, E1691, E1709.
**Unresolved SQL affecting any column:** E86, E155, E198, E199, E200, E202, E203, E279, E400, E621. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E14, E47, E52, E75, E94, E95, E96, E104, E106, E144, E146, E147, E149, E150, E152, E154, E156, E184, E185, E197, E205, E208, E209, E210, E213, E214, E293, E335, E341, E365, E367, E376, E378, E385, E411, E423, E608, E616, E619, E624, E625, E628, E635, E640, E641, E646, E660.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E146, E149, E154, E184, E185, E197, E203, E205, E210, E213, E214, E279, E335, E341, E365, E367, E378, E385, E411, E423, E616, E619, E624, E625, E628, E635, E640, E641, E660, E760, E889, E1009, E1447, E1683, E1684, E1691, E1709 | E154, E185, E341, E367, E628 | declared; caller unknown | — | yes |
| `definition_key` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E146, E149, E154, E184, E185, E197, E203, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E640, E641, E660, E760, E1009, E1502, E1683, E1684, E1691, E1709 | — | — | — | yes |
| `definition_version` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E146, E149, E154, E184, E185, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | — | — | — | yes |
| `correlation_id`, `created_at` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | — | declared; caller unknown | — | yes |
| `idempotency_key` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1691 | — | — | — | yes |
| `scheduled_for` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691, E1709 | — | — | — | yes |
| `mode` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E760, E1009, E1683, E1691, E1709 | — | declared; caller unknown | — | yes |
| `state` | E94, E95, E96, E106, E144, E149, E154, E184, E185, E367, E635, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E146, E149, E154, E184, E185, E197, E205, E210, E213, E214, E279, E335, E341, E365, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E760, E889, E1009, E1683, E1691 | E341 | declared; caller unknown | — | yes |
| `payload` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1691 | — | declared; caller unknown | — | yes |
| `attempt` | E94, E95, E96, E144 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E154, E184, E185, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | E154 | declared; caller unknown | — | yes |
| `max_attempts` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | — | — | — | yes |
| `next_attempt_at` | E149, E184, E367, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | — | declared; caller unknown | — | yes |
| `lease_owner` | E94, E95, E96, E106, E144, E149, E154, E184, E185, E367, E635, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E625, E628, E635, E660, E1683, E1691 | — | — | — | yes |
| `lease_token` | E94, E95, E96, E106, E144, E149, E154, E184, E185, E367, E635, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E146, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1691 | — | — | — | yes |
| `leased_until` | E94, E95, E96, E106, E144, E149, E154, E184, E185, E205, E367, E635, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E146, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | — | — | — | yes |
| `timeout_seconds` | — | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1447, E1691 | — | — | — | yes |
| `last_failure_class` | E149, E154, E184, E185, E367, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | — | — | — | yes |
| `last_failure_detail` | E149, E154, E184, E185, E367, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1691 | — | — | — | yes |
| `started_at` | E94, E95, E96, E144 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1683, E1691 | — | — | — | yes |
| `ended_at` | E106, E149, E154, E184, E185, E367, E635, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E1009, E1683, E1691 | — | — | — | yes |
| `updated_at` | E94, E95, E96, E106, E144, E149, E154, E184, E185, E205, E367, E635, E660 | E14, E47, E52, E94, E95, E96, E104, E106, E144, E149, E184, E197, E205, E210, E213, E214, E279, E335, E341, E367, E378, E385, E411, E423, E616, E619, E624, E628, E635, E660, E760, E1691 | E341 | declared; caller unknown | — | yes |

### `ops.job_attempt`

**Definition:** `migrations/0149_control_plane_jobs.sql:149`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E94, E95, E96, E106, E144, E149, E184, E367, E616, E635, E660. **Table readers:** E14, E47, E79, E94, E95, E96, E106, E144, E149, E184, E341, E367, E423, E616, E619, E624, E628, E635, E660, E1684.
**Unresolved SQL affecting any column:** E203, E621. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E14, E47, E79, E94, E95, E96, E106, E144, E149, E150, E152, E184, E341, E367, E423, E616, E619, E624, E628, E635, E660.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E47, E79, E341, E619, E624, E628 | E341, E367, E628 | E94, E95, E96, E144 | — | unknown |
| `job_id` | E94, E95, E96, E144 | E47, E79, E95, E96, E106, E144, E149, E184, E341, E367, E423, E616, E619, E624, E628, E635, E660, E1684 | E341 | — | — | unknown |
| `attempt` | E94, E95, E96, E144 | E47, E79, E106, E149, E184, E341, E367, E423, E616, E619, E624, E628, E635, E660 | — | — | — | unknown |
| `lease_owner` | E94, E95, E96, E144 | E47, E341, E619, E624, E628 | — | — | — | unknown |
| `lease_token` | E94, E95, E96, E144 | E47, E106, E149, E184, E341, E616, E619, E624, E628, E635, E660 | — | — | — | unknown |
| `provider_route`, `cost_usd` | E616 | E14, E47, E341, E423, E619, E624, E628, E1684 | — | — | — | unknown |
| `state` | E94, E95, E96, E106, E144, E149, E184, E367, E635, E660 | E47, E149, E341, E367, E619, E624, E628 | E341 | — | — | unknown |
| `started_at` | — | E14, E47, E341, E423, E619, E624, E628, E1684 | — | E94, E95, E96, E144 | — | unknown |
| `ended_at` | E106, E149, E184, E367, E635, E660 | E47, E341, E619, E624, E628 | — | — | — | unknown |
| `input_tokens`, `output_tokens` | E616 | E47, E341, E619, E624, E628, E1684 | — | — | — | unknown |
| `failure_class` | E149, E184, E367, E660 | E47, E341, E619, E624, E628 | — | — | — | unknown |
| `detail` | E149, E184, E367, E635, E660 | E47, E341, E619, E624, E628 | — | — | — | unknown |

### `ops.job_definition`

**Definition:** `migrations/0149_control_plane_jobs.sql:72`. **Product path:** positive static evidence (column detail below).

**Table writers:** E9, E135, E1503, E1504. **Table readers:** E9, E94, E95, E96, E135, E144, E146, E197, E213, E214, E335, E400, E433, E759, E1447, E1501, E1504, E1683.
**Unresolved SQL affecting any column:** E607. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E9, E75, E94, E95, E96, E135, E144, E146, E156, E197, E208, E209, E213, E214, E293, E298, E335, E400, E401, E406, E433.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `key` | E1504 | E9, E94, E95, E96, E135, E144, E146, E197, E213, E214, E335, E400, E433, E759, E1447, E1553, E1567, E1568, E1683 | — | — | — | yes |
| `version` | E1504 | E9, E94, E95, E96, E135, E144, E146, E197, E213, E214, E335, E400, E433, E759, E1447, E1501, E1553, E1567, E1568, E1683 | — | — | — | yes |
| `enabled` | E9, E1503, E1504 | E94, E95, E96, E144, E146, E759, E1447, E1504, E1568 | — | declared; caller unknown | — | unknown |
| `risk` | E1504 | E759, E1504, E1683 | — | — | — | unknown |
| `owner_actor` | E1504 | E9, E94, E214, E1504, E1683 | — | declared; caller unknown | — | yes |
| `execution_kind` | E1504 | E95, E96, E144, E197, E213, E335, E759, E1504, E1683 | — | — | — | unknown |
| `execution_contract` | E1504 | E95, E96, E144, E197, E335, E1504 | — | — | — | unknown |
| `inventory_contract`, `state_contract`, `routing_contract`, `filtering_contract`, `validation_contract`, `legacy_schedule` | E1504 | E1504 | — | declared; caller unknown | — | unknown |
| `recurrence` | E1504 | E1447, E1504 | — | — | — | unknown |
| `retry_policy` | E1504 | E433, E1504 | — | — | — | unknown |
| `deduplication`, `completion_contract` | E1504 | E1504 | — | — | — | unknown |
| `legacy_disabled_at` | E135 | E135, E759, E1447, E1567 | — | — | — | unknown |
| `legacy_disable_reason` | E135 | — | — | — | — | unknown |
| `registered_at` | — | E1447 | — | E1504 | — | unknown |
| `updated_at` | E9, E135, E1503, E1504 | E759 | — | E1504 | — | unknown |

### `ops.job_receipt`

**Definition:** `migrations/0149_control_plane_jobs.sql:167`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E106, E149, E154, E184, E185, E367, E635, E660. **Table readers:** E106, E149, E154, E184, E185, E367, E619, E624, E625, E628, E633, E634, E635, E640, E641, E660, E1447, E1683, E1691, E1709.
**Unresolved SQL affecting any column:** E203, E400, E621. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E106, E149, E150, E154, E184, E185, E367, E619, E624, E625, E628, E633, E634, E635, E640, E641, E660.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E619, E624, E625, E628, E633, E634, E640, E641, E1691 | E154, E185, E367, E628 | E106, E149, E154, E184, E185, E367, E635, E660 | — | unknown |
| `job_id` | E106, E149, E154, E184, E185, E367, E635, E660 | E619, E624, E625, E628, E640, E641, E1447, E1683, E1691, E1709 | — | — | — | unknown |
| `attempt` | E106, E149, E154, E184, E185, E367, E635, E660 | E619, E624, E628, E1447, E1691 | E154 | — | — | unknown |
| `kind` | E106, E149, E154, E184, E185, E367, E635, E660 | E619, E624, E628, E640, E641, E1447, E1691, E1709 | E628 | — | — | unknown |
| `receipt_ref` | E106, E149, E154, E184, E185, E367, E635, E660 | E619, E624, E628, E1691 | — | — | — | unknown |
| `evidence` | E106, E149, E154, E184, E185, E367, E635, E660 | E619, E624, E628, E633, E634, E1691, E1709 | — | declared; caller unknown | — | unknown |
| `created_at` | — | E619, E624, E628, E640, E1691, E1709 | — | E106, E149, E154, E184, E185, E367, E635, E660 | — | unknown |

### `ops.legacy_prior_staging_readback_allowlist`

**Definition:** `migrations/0222_legacy_prior_staging_readback.sql:11`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Unresolved SQL affecting any column:** E393. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `deployment_attempt_id` | — | — | — | — | — | unknown |
| `captured_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.legacy_schedule_disable_receipt`

**Definition:** `migrations/0176_legacy_schedule_disable_receipt.sql:22`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E135. **Table readers:** E135, E1293.
**Additional lexical/dependency evidence:** E135, E156, E431.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E135, E1293 | — | E135 | — | unknown |
| `receipt_ref` | E135 | E135, E1293 | — | — | — | unknown |
| `idempotency_key`, `workflow_key`, `workflow_version`, `surface_id`, `locator`, `reason`, `approved_by`, `pre_observation_ref`, `post_observation_ref`, `sibling_observation_ref`, `sibling_surface_id`, `sibling_locator`, `sibling_pre_observation_ref`, `sibling_post_observation_ref` | E135 | E135 | — | — | — | unknown |
| `approved_at` | — | E135 | — | E135 | — | unknown |

### `ops.legacy_schedule_launchd_contract`

**Definition:** `migrations/0182_launchd_scheduler_observation_receipt.sql:7`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E135, E382.
**Additional lexical/dependency evidence:** E135, E382.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `surface_id`, `workflow_key`, `workflow_version`, `locator`, `plist_sha256`, `schedule_sha256`, `timezone` | — | E135, E382 | — | — | — | unknown |
| `repo_plist_relpath`, `installed_plist_name`, `program_arguments` | — | E382 | — | — | — | unknown |

### `ops.legacy_schedule_observation_receipt`

**Definition:** `migrations/0180_claude_scheduler_observation_receipt.sql:23`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E377, E382. **Table readers:** E135, E377, E382, E1063, E1445.
**Additional lexical/dependency evidence:** E135, E377, E382.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E135, E377, E382 | E1063, E1445 | E377, E382 | — | unknown |
| `receipt_ref` | E377, E382 | E135, E377, E382, E1063 | — | — | — | unknown |
| `idempotency_key`, `locator`, `cron_expression`, `timezone`, `definition_sha256`, `provider_revision`, `source_fingerprint`, `device_id` | E377, E382 | E135, E377, E382 | — | — | — | unknown |
| `surface_id` | E377, E382 | E135, E377, E382, E1445 | — | — | — | unknown |
| `workflow_key` | E377, E382 | E135, E377, E382, E1063 | E1445 | — | — | unknown |
| `workflow_version` | E377, E382 | E135, E377, E382 | E1445 | — | — | unknown |
| `scheduler_state` | E377, E382 | E135, E377, E382, E1063, E1445 | — | — | — | unknown |
| `observed_at` | E377, E382 | E135, E377, E382, E1445 | E1063 | — | — | unknown |
| `created_at` | — | E135, E377, E382 | — | E377, E382 | — | unknown |
| `scheduler_kind` | E382 | E135, E377, E382 | — | E377 | — | unknown |

### `ops.legacy_schedule_provider_contract`

**Definition:** `migrations/0180_claude_scheduler_observation_receipt.sql:8`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E135, E377.
**Additional lexical/dependency evidence:** E135, E377.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `surface_id`, `workflow_key`, `workflow_version`, `locator`, `cron_expression`, `timezone`, `definition_relpath`, `definition_sha256` | — | E377 | — | — | — | unknown |

### `ops.legacy_schedule_surface_registry`

**Definition:** `migrations/0176_legacy_schedule_disable_receipt.sql:11`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E135.
**Unresolved SQL affecting any column:** E135. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E156, E431.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `workflow_key`, `workflow_version`, `surface_id`, `locator`, `scheduler_kind`, `duplicate_group` | — | E135 | — | — | — | unknown |

### `ops.meeting`

**Definition:** `migrations/0556_meeting_mode_store.sql:60`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E13, E140, E277, E328, E656. **Table readers:** E13, E97, E130, E140, E273, E277, E328, E383, E656.
**Unresolved SQL affecting any column:** E13, E140, E328, E656. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E97, E130, E273, E277, E383.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | E656 | E13, E97, E130, E140, E273, E277, E328, E383, E656 | — | — | — | unknown |
| `organization_tenant_id`, `platform`, `source_system`, `native_id`, `native_id_epoch`, `title`, `activation_intent`, `started_by_actor` | E656 | E13, E97, E130, E140, E273, E328, E383, E656 | — | — | — | unknown |
| `recording`, `started_at` | — | E13, E97, E130, E140, E273, E328, E383, E656 | — | E656 | — | unknown |
| `mode_state` | E140 | E13, E97, E130, E140, E273, E328, E383, E656 | — | E656 | — | unknown |
| `ended_by_actor`, `ended_at` | E140 | E13, E97, E130, E140, E273, E328, E383, E656 | — | — | — | unknown |
| `last_seq` | E277 | E13, E97, E130, E140, E273, E277, E328, E383, E656 | — | E656 | — | unknown |
| `last_note_number` | E13 | E13, E97, E130, E140, E273, E328, E383, E656 | — | E656 | — | unknown |
| `last_action_number` | E328 | E13, E97, E130, E140, E273, E328, E383, E656 | — | E656 | — | unknown |

### `ops.meeting_action`

**Definition:** `migrations/0556_meeting_mode_store.sql:122`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E130, E274, E328, E383. **Table readers:** E130, E274, E275, E328, E383.
**Unresolved SQL affecting any column:** E273. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E130, E274, E275, E328, E383.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `meeting_id`, `action_number` | E328 | E130, E274, E275, E328, E383 | — | — | — | unknown |
| `dedupe_key` | E328 | E130, E328, E383 | — | — | — | unknown |
| `state` | E130, E274, E328, E383 | E130, E274, E275, E328, E383 | — | — | — | unknown |
| `current_revision` | E328 | E130, E275, E328, E383 | — | — | — | unknown |
| `decided_revision`, `decided_by_actor` | E130, E274 | E130, E275, E328, E383 | — | — | — | unknown |
| `decided_at` | E130, E274 | E130, E328, E383 | — | — | — | unknown |
| `disposition`, `assignee_actor`, `operation_key` | E274 | E130, E275, E328, E383 | — | — | — | unknown |
| `outcome` | E383 | E130, E275, E328, E383 | — | — | — | unknown |

### `ops.meeting_action_decision_request`

**Definition:** `migrations/0906_dot_database_design.sql:575`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E130. **Table readers:** E130.
**Additional lexical/dependency evidence:** E130.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `request` | E130 | E130 | — | — | — | unknown |

### `ops.meeting_action_revision`

**Definition:** `migrations/0556_meeting_mode_store.sql:150`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E328. **Table readers:** E130, E275, E328, E383.
**Unresolved SQL affecting any column:** E273. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E130, E275, E328, E383.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | E328 | E130, E328, E383 | E275 | — | — | unknown |
| `meeting_id`, `action_number`, `revision`, `command` | E328 | E130, E275, E328, E383 | — | — | — | unknown |
| `summary`, `basis`, `source`, `processing_epoch`, `proposed_by_actor`, `proposed_by_instance` | E328 | E130, E328, E383 | — | — | — | unknown |
| `created_at` | — | E130, E328, E383 | — | E328 | — | unknown |

### `ops.meeting_note`

**Definition:** `migrations/0556_meeting_mode_store.sql:106`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E13. **Table readers:** E13.
**Unresolved SQL affecting any column:** E273. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E13.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `meeting_id`, `note_number`, `revision`, `body`, `author_actor`, `author_instance` | E13 | E13 | — | — | — | unknown |
| `created_at` | — | E13 | — | E13 | — | unknown |

### `ops.meeting_processing_lease`

**Definition:** `migrations/0556_meeting_mode_store.sql:93`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E97, E140. **Table readers:** E97, E140, E328.
**Unresolved SQL affecting any column:** E273. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E97, E140, E328.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `meeting_id` | E97 | E97, E140, E328 | — | — | — | unknown |
| `holder_actor`, `holder_instance`, `lease_epoch`, `acquired_at`, `expires_at` | E97 | E97, E328 | — | — | — | unknown |
| `released_at` | E97, E140 | E97, E140, E328 | — | — | — | unknown |

### `ops.meeting_stream`

**Definition:** `migrations/0556_meeting_mode_store.sql:178`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E277. **Table readers:** E277.
**Unresolved SQL affecting any column:** E130, E273, E277. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `meeting_id`, `seq`, `kind`, `actor_id`, `client_instance`, `note_number`, `action_number`, `revision`, `lease_epoch`, `idempotency_key` | E277 | — | — | — | — | unknown |
| `at` | — | — | — | E277 | — | unknown |

### `ops.model_role_current_pointer`

**Definition:** `migrations/0542_model_role_store.sql:622`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E290. **Table readers:** E282, E283, E285, E290.
**Additional lexical/dependency evidence:** E282, E283, E285, E290.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `set_at` | — | E282, E283, E285, E290 | — | E290 | — | unknown |
| `tenant`, `role_key`, `pointer_no`, `revision_id`, `revision_no`, `role_digest`, `expected_prior_revision_no`, `set_by_partner_slug`, `set_by_actor_id`, `acting_actor_id` | E290 | E282, E283, E285, E290, E1577 | — | — | — | unknown |
| `idempotency_key`, `authority_class`, `authority_grant_kind` | E290 | E282, E283, E285, E290 | — | — | — | unknown |

### `ops.model_role_revision`

**Definition:** `migrations/0542_model_role_store.sql:502`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E286. **Table readers:** E281, E283, E284, E285, E286, E288, E289, E290, E291.
**Unresolved SQL affecting any column:** E286. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E281, E283, E284, E285, E288, E289, E290, E291.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E281, E283, E284, E285, E286, E289, E290, E291 | — | E286 | — | unknown |
| `tenant`, `role_key`, `revision_no` | E286 | E283, E284, E285, E286, E288, E289, E290, E291, E1578 | — | — | — | unknown |
| `idempotency_key`, `role_digest`, `minimum_strength_ref` | E286 | E283, E284, E286, E289, E290, E291 | — | — | — | unknown |
| `schema_version`, `title`, `mission`, `recorded_by_actor_id` | E286 | E283, E284, E286, E289, E290, E291, E1578 | — | — | — | unknown |
| `authority_class` | E286 | E283, E284, E286, E289, E290, E291, E1578 | — | — | E1578 | unknown |
| `recorded_at` | — | E283, E284, E286, E289, E290, E291 | — | E286 | — | unknown |
| `created_xid` | — | E281, E283, E284, E286, E289, E290, E291 | — | E286 | — | unknown |

### `ops.model_role_revision_ref`

**Definition:** `migrations/0542_model_role_store.sql:594`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E286. **Table readers:** E286, E287, E291.
**Additional lexical/dependency evidence:** E286, E287, E291.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E286 | — | unknown |
| `revision_id`, `field`, `value` | E286 | E287, E291 | — | — | — | unknown |
| `ordinal` | E286 | E291 | — | — | — | unknown |

### `ops.model_role_revision_text`

**Definition:** `migrations/0542_model_role_store.sql:567`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E286. **Table readers:** E286, E291, E292.
**Additional lexical/dependency evidence:** E286, E291, E292.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E286 | — | unknown |
| `revision_id`, `field`, `ordinal`, `value` | E286 | E291, E292 | — | — | — | unknown |

### `ops.nightly_availability_canary_receipt`

**Definition:** `migrations/0227_nightly_availability_canary_record_layer.sql:18`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E426.
**Additional lexical/dependency evidence:** E384, E426.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E426 | — | declared; caller unknown | — | unknown |
| `job_id`, `attempt`, `source_snapshot_id`, `source_snapshot_digest`, `availability_count`, `open_search_count`, `match_count`, `output_digest` | — | E426 | — | — | — | unknown |

### `ops.nightly_availability_canary_source_snapshot`

**Definition:** `migrations/0227_nightly_availability_canary_record_layer.sql:8`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E123. **Table readers:** E123.
**Additional lexical/dependency evidence:** E123, E384.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E123 | — | E123 | — | unknown |
| `job_id`, `attempt`, `workflow_version`, `snapshot`, `snapshot_digest`, `availability_count`, `open_search_count` | E123 | E123 | — | — | — | unknown |

### `ops.notification`

**Definition:** `migrations/0521_r03_notifications.sql:30`. **Product path:** positive static evidence (column detail below).

**Table writers:** E280. **Table readers:** E6, E280, E294, E700.
**Additional lexical/dependency evidence:** E6, E280, E294, E700.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E6, E280, E294, E700 | — | E280 | — | yes |
| `subject_type`, `subject_ref`, `reason`, `severity`, `deep_link` | E280 | E294, E700 | — | — | — | yes |
| `event_ref`, `event_source` | E280 | E700 | — | — | — | yes |
| `recipient_actor` | E280 | E6, E280, E294, E700 | — | — | — | yes |
| `dedupe_key` | E280 | E280 | — | — | — | unknown |
| `created_at` | — | E294, E700 | — | E280 | — | yes |
| `correlation_id` | E280 | — | — | — | — | unknown |

### `ops.notification_delivery`

**Definition:** `migrations/0521_r03_notifications.sql:55`. **Product path:** positive static evidence (column detail below).

**Table writers:** E280. **Table readers:** E280, E294, E700, E1488.
**Additional lexical/dependency evidence:** E280, E294, E700.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `attempted_at` | — | E1488 | — | E280 | — | unknown |
| `notification_id`, `state` | E280 | E294, E700, E1488 | — | — | — | yes |
| `channel` | E280 | E294, E1488 | — | — | — | yes |
| `settled_at` | E280 | — | — | — | — | unknown |
| `failure_reason` | — | — | — | — | — | unknown |
| `held_until` | E280 | E294, E700 | — | — | — | yes |

### `ops.notification_preference`

**Definition:** `migrations/0521_r03_notifications.sql:75`. **Product path:** positive static evidence (column detail below).

**Table writers:** E613. **Table readers:** E270, E295, E296, E297, E613.
**Unresolved SQL affecting any column:** E280, E613. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E270, E295, E296, E297.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `actor` | E613 | E270, E295, E296, E297, E613 | — | — | — | yes |
| `device_opt_in`, `version` | E613 | E295, E296, E297, E613 | — | declared; caller unknown | — | yes |
| `quiet_hours_start`, `quiet_hours_end` | E613 | E295, E296, E297, E613 | — | — | — | yes |
| `timezone` | E613 | E270, E295, E296, E297, E613 | — | declared; caller unknown | — | yes |

### `ops.notification_preference_write`

**Definition:** `migrations/0527_notification_preferences.sql:59`. **Product path:** positive static evidence (column detail below).

**Table writers:** E613. **Table readers:** E613.
**Additional lexical/dependency evidence:** E613.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `actor`, `request_digest`, `result` | E613 | E613 | — | — | — | yes |
| `applied_at` | — | E613 | — | E613 | — | yes |

### `ops.notification_read`

**Definition:** `migrations/0521_r03_notifications.sql:68`. **Product path:** positive static evidence (column detail below).

**Table writers:** E6. **Table readers:** E6, E294, E700.
**Additional lexical/dependency evidence:** E6, E294, E700.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `notification_id`, `recipient_actor` | E6 | E6, E294, E700 | — | — | — | yes |
| `read_at` | — | E6, E294 | — | E6 | — | yes |

### `ops.npi_device_evidence_receipt`

**Definition:** `migrations/0167_control_plane_npi_device_evidence.sql:7`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E385. **Table readers:** E385.
**Additional lexical/dependency evidence:** E385.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E385 | — | E385 | — | unknown |
| `job_id`, `builder_key`, `workflow_key`, `workflow_version`, `mode`, `scheduled_for`, `device_id`, `observed_at`, `source_release`, `source_checksum`, `results`, `idempotency_key` | E385 | E385 | — | — | — | unknown |

### `ops.pitr_probe`

**Definition:** `migrations/0597_pitr_probe.sql:32`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E722. **Table readers:** E722.
**Additional lexical/dependency evidence:** E722.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `written_at` | — | E722, E1583 | — | E722 | E1583 | unknown |
| `nonce` | — | E722, E1583 | — | — | E1583 | unknown |
| `role` | E722 | E722 | — | — | — | unknown |

### `ops.portfolio_child_revision`

**Definition:** `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:241`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E309. **Table readers:** E304, E305, E307, E308, E309, E313, E314, E315.
**Additional lexical/dependency evidence:** E304, E305, E307, E308, E309, E313, E314, E315.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E305, E307 | — | E309 | — | unknown |
| `portfolio_revision_id`, `child_ref` | E309 | E304, E305, E307, E308, E313, E314, E315 | — | — | — | unknown |
| `child_ordinal` | E309 | E304, E305, E307, E308 | — | — | — | unknown |
| `child_version`, `accepted_plan_id` | E309 | E304, E305, E307 | — | — | — | unknown |
| `child_digest` | E309 | E304, E305, E307, E313, E314 | — | — | — | unknown |

### `ops.portfolio_node`

**Definition:** `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:264`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E309. **Table readers:** E305, E307, E308, E309, E315, E1070, E1398, E1399.
**Additional lexical/dependency evidence:** E305, E307, E308, E309, E315.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E307 | — | E309 | — | unknown |
| `portfolio_revision_id`, `node_ref` | E309 | E305, E307, E308, E315, E1070, E1398 | — | — | — | unknown |
| `node_kind` | E309 | E307, E308, E1398, E1399 | — | — | — | unknown |
| `ordinal`, `authority_class`, `effect_class`, `data_class`, `budget_identity`, `budget_ceiling`, `model_floor`, `recovery_ref`, `terminal_predicate` | E309 | E307, E308 | — | — | — | unknown |
| `parent_ref` | E309 | E307, E308, E315, E1398 | — | — | — | unknown |
| `child_ref` | E309 | E305, E307, E315, E1398 | — | — | — | unknown |
| `created_at` | — | E307, E1070, E1398 | E1070 | E309 | — | unknown |

### `ops.portfolio_node_edge`

**Definition:** `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:293`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E309. **Table readers:** E307, E308, E309, E315.
**Unresolved SQL affecting any column:** E313. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E307, E308, E309, E315.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E309 | — | unknown |
| `portfolio_revision_id`, `from_node_ref`, `to_node_ref` | E309 | E307, E308, E315 | — | — | — | unknown |

### `ops.portfolio_revision`

**Definition:** `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:212`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E309. **Table readers:** E64, E302, E303, E306, E307, E308, E309, E310, E311, E314, E315, E1070, E1398.
**Additional lexical/dependency evidence:** E64, E302, E303, E306, E307, E308, E309, E310, E311, E314, E315.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E64, E302, E303, E306, E307, E308, E309, E310, E311, E314, E315, E1070, E1398, E1587 | — | E309 | — | unknown |
| `portfolio_ref` | E309 | E302, E303, E306, E307, E308, E310, E314, E315, E1070 | — | — | — | unknown |
| `revision_version` | E309 | E303, E306, E308, E310, E314, E1070, E1398 | — | — | — | unknown |
| `idempotency_key`, `schema_version`, `accepted_schema_version`, `preimage`, `node_count`, `child_count` | E309 | E303, E308, E310, E314 | — | — | — | unknown |
| `graph_digest`, `edge_count` | E309 | E303, E308, E310, E314, E1587 | — | — | — | unknown |
| `accepted_digest` | E309 | E64, E303, E308, E310, E314, E1587 | — | — | — | unknown |
| `proposed_by_actor_id` | E309 | E303, E308, E310, E311, E314, E1585 | — | — | — | unknown |
| `created_at` | — | E303, E308, E310, E314 | E1070 | E309 | — | unknown |

### `ops.portfolio_revision_acceptance_receipt`

**Definition:** `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:320`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E302. **Table readers:** E64, E302, E306, E314, E640, E645, E646.
**Unresolved SQL affecting any column:** E316. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E64, E302, E306, E314, E640, E645, E646.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E302, E314, E640, E645, E646 | — | E302 | — | unknown |
| `portfolio_revision_id` | E302 | E64, E306, E314, E640, E645, E646, E1584 | — | — | — | unknown |
| `portfolio_ref` | E302 | E314, E640, E645, E1584 | — | — | — | unknown |
| `idempotency_key` | E302 | E314 | — | — | — | unknown |
| `accepted_digest`, `review_id` | E302 | E314, E1584 | — | — | — | unknown |
| `accepted_by_actor_id` | E302 | E314, E1584 | — | — | E1584 | unknown |
| `accepted_at` | — | E64, E314, E640, E646 | — | E302 | — | unknown |

### `ops.portfolio_revision_review`

**Definition:** `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:306`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E312. **Table readers:** E303, E312.
**Unresolved SQL affecting any column:** E310. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E303, E312.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E303, E312 | — | E312 | — | unknown |
| `portfolio_revision_id`, `reviewed_digest` | E312 | E303, E1586 | — | — | — | unknown |
| `idempotency_key`, `review_summary` | E312 | E303 | — | — | — | unknown |
| `verdict`, `reviewer_actor_id` | E312 | E303, E1586 | — | — | E1586 | unknown |
| `created_at` | — | E303 | — | E312 | — | unknown |

### `ops.program6_browser_action_challenge_redemption`

**Definition:** `migrations/0183_program6_browser_action_challenge.sql:10`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E402. **Table readers:** E402.
**Additional lexical/dependency evidence:** E402.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E402 | — | E402 | — | unknown |
| `token_digest`, `session_digest`, `action`, `material_digest`, `idempotency_key`, `redeemed_by_actor_id` | E402 | — | — | — | — | unknown |
| `redeemed_at` | — | — | — | E402 | — | unknown |

### `ops.program_controller_fact_ledger`

**Definition:** `migrations/0517_program_controller_seams.sql:136`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E386. **Table readers:** E386.
**Additional lexical/dependency evidence:** E386.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `fact_kind`, `recorded_by`, `target_table`, `target_id`, `result` | E386 | E386 | — | — | — | unknown |
| `recorded_at` | — | E386 | — | E386 | — | unknown |

### `ops.program_origin_head_observation`

**Definition:** `migrations/0517_program_controller_seams.sql:124`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E386. **Table readers:** E386, E1044.
**Additional lexical/dependency evidence:** E386.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E386 | — | E386 | — | unknown |
| `repository_root`, `origin_main_sha`, `observed_at` | E386 | E1044 | — | — | — | unknown |
| `observed_by` | E386 | — | — | — | — | unknown |
| `recorded_at` | — | E1044 | — | E386 | — | unknown |

### `ops.program_width_evidence`

**Definition:** `migrations/0517_program_controller_seams.sql:63`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E386. **Table readers:** E386, E1047.
**Additional lexical/dependency evidence:** E386.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E386 | — | E386 | — | unknown |
| `program_ref`, `evidence_class`, `bound_base_sha` | E386 | E1047 | — | — | — | unknown |
| `state`, `evidence_ref`, `observed_at` | E386 | E386, E1047 | — | — | — | unknown |
| `accepted_at` | E386 | E386 | — | — | — | unknown |
| `recorded_at` | — | — | — | E386 | — | unknown |

### `ops.program_width_state`

**Definition:** `migrations/0517_program_controller_seams.sql:77`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E386. **Table readers:** E386, E1046.
**Additional lexical/dependency evidence:** E386.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E386 | — | E386 | — | unknown |
| `program_ref` | E386 | E1046 | — | — | — | unknown |
| `current_width`, `requested_width` | E386 | E386, E1046 | — | — | — | unknown |
| `set_at` | E386 | — | — | E386 | — | unknown |

### `ops.proposed_eval_candidate`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:394`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E326. **Table readers:** E326, E337, E340, E688.
**Additional lexical/dependency evidence:** E326, E337, E340, E688.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E326, E337, E340, E688 | E337, E340 | E326 | — | unknown |
| `organization_tenant_id` | E326 | E326, E688 | E340 | — | — | unknown |
| `attempt_receipt_id` | E326 | E326, E337, E340, E688 | — | — | — | unknown |
| `candidate_ref`, `case_ref`, `target_golden_set_ref`, `source_digest`, `lane_ref`, `risk_class`, `provenance`, `split_target`, `basis` | E326 | E326, E340, E688 | — | — | — | unknown |
| `normalized_root_cause_key`, `context_binding` | E326 | E326, E688 | — | — | — | unknown |
| `lifecycle`, `promotion_state` | — | E326, E340, E688 | — | E326 | — | unknown |
| `created_at` | — | E326, E337, E340, E688 | E337 | E326 | — | unknown |

### `ops.proposed_eval_candidate_event`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:421`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E326, E688. **Table readers:** E326, E337, E340, E688.
**Additional lexical/dependency evidence:** E326, E337, E340, E688.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E326, E340, E688 | E337, E340 | E326, E688 | — | unknown |
| `candidate_id`, `event_kind` | E326, E688 | E326, E337, E340, E688 | — | — | — | unknown |
| `decided_by_actor_id` | E688 | E326, E688 | — | — | — | unknown |
| `decision_basis`, `idempotency_key` | E326, E688 | E326, E688 | — | — | — | unknown |
| `created_at` | — | E326, E340, E688 | E337 | E326, E688 | — | unknown |

### `ops.provider_observation`

**Definition:** `migrations/0149_control_plane_jobs.sql:19`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E387. **Table readers:** E387, E1686.
**Additional lexical/dependency evidence:** E387.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E387 | — | E387 | — | unknown |
| `route_key` | E387 | — | E1686 | — | — | unknown |
| `status`, `latency_ms`, `error_class`, `expires_at` | E387 | E1686 | — | — | — | unknown |
| `observed_at` | — | E1686 | — | E387 | — | unknown |
| `source_ref` | E387 | — | — | — | — | unknown |

### `ops.provider_route`

**Definition:** `migrations/0149_control_plane_jobs.sql:7`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E14, E423, E616, E1686.
**Additional lexical/dependency evidence:** E14, E423, E616.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `route_key` | — | E14, E423, E616, E1686 | E1686 | — | — | unknown |
| `priority`, `endpoint_ref`, `max_concurrency`, `monthly_budget_usd` | — | E14, E423, E1686 | — | — | — | unknown |
| `enabled`, `capability_tags`, `created_at`, `updated_at` | — | E14, E423, E1686 | — | declared; caller unknown | — | unknown |

### `ops.ready_plan_amendment`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:1022`. **Product path:** positive static evidence (column detail below).

**Table writers:** E329. **Table readers:** E1, E329, E365, E1084.
**Unresolved SQL affecting any column:** E139, E329. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E1, E365.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `plan_id`, `work_request_id` | E329 | E1, E329, E365, E1084 | — | — | — | yes |
| `predecessor_plan_id` | E329 | E1, E329, E365 | — | — | — | yes |
| `predecessor_plan_hash`, `base_version`, `amendment_hash`, `proposed_by_actor_id` | E329 | E1, E329 | — | — | — | yes |
| `proposed_at` | — | E1, E329, E1084 | — | E329 | — | yes |

### `ops.ready_plan_amendment_acceptance_receipt`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:1053`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1. **Table readers:** E1, E7, E136, E1084.
**Additional lexical/dependency evidence:** E1, E7, E136.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1, E7, E136, E1084 | — | E1 | — | yes |
| `work_request_id` | E1 | E1, E7, E136 | — | — | — | yes |
| `predecessor_plan_id` | E1 | E1, E136 | — | — | — | yes |
| `successor_plan_id` | E1 | E1, E7, E136, E1084 | — | — | — | yes |
| `plan_acceptance_receipt_id`, `idempotency_key`, `base_version`, `result_version`, `accepted_by_actor_id`, `census` | E1 | E1 | — | — | — | yes |
| `accepted_at` | E1 | E1, E136, E1084 | — | declared; caller unknown | — | yes |

### `ops.ready_plan_amendment_ack`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:1077`. **Product path:** positive static evidence (column detail below).

**Table writers:** E7. **Table readers:** E7, E136.
**Additional lexical/dependency evidence:** E7, E136.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `notice_id` | E7 | E7, E136 | — | — | — | yes |
| `actor_id`, `idempotency_key` | E7 | E7 | — | — | — | yes |
| `acknowledged_at` | — | E7, E136 | — | E7 | — | yes |

### `ops.ready_plan_amendment_notice`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:1069`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1. **Table readers:** E1, E7, E136.
**Additional lexical/dependency evidence:** E1, E7, E136.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E7, E136 | — | — | — | yes |
| `acceptance_receipt_id`, `actor_id` | E1 | E7, E136 | — | — | — | yes |
| `created_at` | — | E7 | — | E1 | — | yes |

### `ops.ready_plan_amendment_slice_compatibility`

**Definition:** `migrations/0532a_canonical_ownership_lease_activation.sql:1041`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1. **Table readers:** E1, E365.
**Additional lexical/dependency evidence:** E1, E365.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `successor_plan_id` | E1 | E1, E365 | — | — | — | yes |
| `predecessor_slice_plan_id`, `predecessor_plan_digest`, `slice_ref`, `predecessor_slice_digest` | E1 | — | — | — | — | yes |
| `disposition`, `reason`, `created_at` | — | — | — | E1 | — | yes |

### `ops.release`

**Definition:** `migrations/0131_ops_release.sql:42`. **Product path:** positive static evidence (column detail below).

**Table writers:** E36, E192, E336, E417. **Table readers:** E36, E134, E192, E267, E318, E319, E321, E323, E336, E393, E394, E413, E417, E641, E712, E1464, E1474, E1671, E1687, E1688.
**Unresolved SQL affecting any column:** E36, E132, E346, E716, E719. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E134, E192, E267, E318, E319, E321, E323, E336, E388, E393, E394, E413, E417, E636, E641, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E36, E134, E192, E267, E318, E319, E321, E323, E336, E393, E394, E413, E417, E641, E1464, E1474, E1588, E1593, E1597, E1626, E1659, E1687, E1688 | — | declared; caller unknown | — | unknown |
| `correlation_id`, `created_at` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1687 | — | declared; caller unknown | — | unknown |
| `release_key` | — | E36, E134, E192, E267, E318, E319, E321, E336, E393, E394, E417, E712, E1464, E1474, E1593, E1595, E1596, E1597, E1600, E1671, E1687, E1688 | — | — | — | unknown |
| `service_id` | — | E36, E134, E192, E318, E319, E321, E323, E336, E393, E394, E417, E1474, E1594, E1597, E1687 | — | — | — | unknown |
| `environment` | — | E36, E134, E192, E267, E318, E319, E321, E336, E393, E394, E413, E417, E641, E712, E1464, E1506, E1588, E1593, E1594, E1599, E1600, E1626, E1659, E1687, E1688 | — | — | E1588, E1593, E1600 | unknown |
| `state` | E36, E336, E417 | E36, E134, E192, E267, E318, E319, E321, E336, E393, E394, E413, E417, E641, E712, E1506, E1588, E1589, E1593, E1594, E1595, E1596, E1597, E1598, E1599, E1600, E1626, E1659, E1671, E1687, E1688 | — | declared; caller unknown | E1506, E1597, E1598 | unknown |
| `git_sha` | — | E36, E134, E192, E267, E318, E319, E321, E323, E336, E393, E394, E413, E417, E641, E1464, E1594, E1597, E1659, E1687 | — | — | — | unknown |
| `artifact_digest` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1594, E1671, E1687 | — | — | — | unknown |
| `dependency_lock_digest`, `config_fingerprint`, `performance_budget_ref` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1594, E1687 | — | — | — | unknown |
| `sbom_ref`, `declared_env_differences`, `asset_versions`, `security_evidence_ref`, `work_request_ref`, `failure_class`, `superseded_by`, `source_kind`, `source_ref`, `expires_at`, `abandoned_reason`, `maker_authority_verified` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1687 | — | — | — | unknown |
| `migration_set` | — | E36, E134, E192, E318, E319, E321, E323, E336, E393, E394, E417, E1594, E1687 | — | — | — | unknown |
| `schema_highest_migration`, `schema_applied_count`, `schema_ledger_sha256` | — | E36, E134, E192, E318, E319, E321, E323, E336, E393, E394, E417, E1594, E1600, E1687, E1688 | — | — | — | unknown |
| `maker_actor`, `maker_verification_ref`, `maker_session_user` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1505, E1687 | — | — | E1505 | unknown |
| `test_evidence_ref` | E192 | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1687 | — | — | — | unknown |
| `verifier_actor`, `verifier_evidence_ref` | E36 | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1589, E1626, E1659, E1687 | — | — | — | unknown |
| `rollback_ready` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1594, E1687 | — | declared; caller unknown | — | unknown |
| `rollback_plan_ref`, `recovery_strategy` | — | E36, E134, E192, E318, E319, E321, E323, E336, E393, E394, E417, E1474, E1594, E1687 | — | — | — | unknown |
| `plan_hash` | — | E36, E134, E192, E318, E319, E321, E323, E336, E393, E394, E413, E417, E1506, E1588, E1589, E1594, E1598, E1626, E1659, E1687 | — | — | — | unknown |
| `approved_by_actor`, `approval_expires_at` | E36 | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1506, E1588, E1598, E1626, E1659, E1687 | — | — | E1506, E1598 | unknown |
| `approved_at` | E36 | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1506, E1588, E1597, E1598, E1626, E1659, E1687 | — | — | E1506, E1598 | unknown |
| `observed_at` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1671, E1687 | — | declared; caller unknown | — | unknown |
| `updated_at` | — | E36, E134, E192, E267, E318, E319, E321, E336, E393, E394, E417, E712, E1598, E1687 | — | declared; caller unknown | E1598 | unknown |
| `ended_at` | — | E36, E134, E192, E267, E318, E319, E321, E336, E393, E394, E417, E712, E1687 | — | — | — | unknown |
| `provider` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E413, E417, E1464, E1597, E1599, E1659, E1687 | — | — | — | unknown |
| `provider_version_id` | — | E36, E134, E192, E318, E319, E321, E323, E336, E393, E394, E413, E417, E1464, E1597, E1599, E1659, E1687 | — | — | — | unknown |
| `performance_budget_ms` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1474, E1594, E1597, E1687 | — | — | — | unknown |
| `approval_receipt_id` | — | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1588, E1589, E1598, E1687 | — | — | E1598 | unknown |
| `staging_approval_receipt_id` | E36 | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1506, E1598, E1626, E1687 | — | — | E1506, E1598 | unknown |
| `readiness_receipt_id` | E336 | E36, E134, E192, E318, E319, E321, E336, E393, E394, E413, E417, E1588, E1598, E1659, E1687 | — | — | E1598 | unknown |
| `ready_at` | E336 | E36, E134, E192, E318, E319, E321, E336, E393, E394, E417, E1597, E1598, E1659, E1687 | — | — | E1598 | unknown |

### `ops.release_approval_receipt`

**Definition:** `migrations/0202_staging_release_readback_receipt.sql:201`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Unresolved SQL affecting any column:** E324. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | declared; caller unknown | — | unknown |
| `idempotency_key`, `release_id`, `recovery_run_id`, `recovery_bundle_id`, `plan_hash`, `approved_by_actor`, `approved_at`, `approval_expires_at`, `approval_sha256`, `evidence_ref`, `verifier_actor`, `verifier_evidence_ref` | — | — | — | — | — | unknown |

### `ops.release_readiness_receipt`

**Definition:** `migrations/0540_release_readiness_without_repeat_approval.sql:25`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E336. **Table readers:** E134, E336, E412, E413.
**Unresolved SQL affecting any column:** E417. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E134, E336, E412, E413.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E134, E336, E412, E413 | — | E336 | — | unknown |
| `idempotency_key`, `recovery_run_id`, `verifier_actor`, `verifier_evidence_ref`, `session_login`, `ready_at`, `receipt_sha256` | E336 | E134, E336, E412 | — | — | — | unknown |
| `release_id`, `git_sha`, `provider`, `provider_version_id`, `plan_hash` | E336 | E134, E336, E412, E413 | — | — | — | unknown |

### `ops.release_receipt`

**Definition:** `migrations/0517_program_controller_seams.sql:109`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E386. **Table readers:** E386, E1049.
**Additional lexical/dependency evidence:** E386.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E386 | — | E386 | — | unknown |
| `release_ref`, `slice_ref`, `head_sha`, `receipt_ref`, `receipt_kind`, `body` | E386 | E1049 | — | — | — | unknown |
| `recorded_at` | — | — | — | E386 | — | unknown |

### `ops.release_slice_member`

**Definition:** `migrations/0628_doctorcre_slice_done_marker.sql:687`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E133, E267, E641.
**Unresolved SQL affecting any column:** E132, E346, E716. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E133, E267, E388, E636, E641.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E133, E641 | — | declared; caller unknown | — | unknown |
| `release_id` | — | E267, E641 | — | — | — | unknown |
| `release_git_sha`, `slice_id` | — | E641 | — | — | — | unknown |
| `commit_sha`, `pr_number` | — | E133 | — | — | — | unknown |
| `subject`, `attribution`, `recorded_by_actor_slug` | — | — | — | — | — | unknown |
| `created_at` | — | E133 | — | declared; caller unknown | — | unknown |

### `ops.renewal_decision_source_run`

**Definition:** `migrations/0230_renewal_decision_delivery.sql:29`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Additional lexical/dependency evidence:** E608.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `recorded_at` | — | — | — | declared; caller unknown | — | unknown |
| `job_id`, `attempt`, `snapshot_at`, `member_count`, `source_snapshot_id` | — | — | — | — | — | unknown |

### `ops.renewal_decision_source_run_member`

**Definition:** `migrations/0230_renewal_decision_delivery.sql:42`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Additional lexical/dependency evidence:** E608.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `source_run_id`, `candidate_id`, `row_digest` | — | — | — | — | — | unknown |

### `ops.renewal_source_snapshot`

**Definition:** `migrations/0249_renewal_signed_source_ingress.sql:4`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E210. **Table readers:** E210.
**Additional lexical/dependency evidence:** E210, E608.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `job_id`, `attempt`, `provider`, `key_fingerprint`, `source_observed_at`, `payload_sha256`, `signature_sha256`, `row_count` | E210 | E210 | — | — | — | unknown |
| `recorded_at` | — | E210 | — | E210 | — | unknown |

### `ops.renewal_source_snapshot_member`

**Definition:** `migrations/0249_renewal_signed_source_ingress.sql:20`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E210. **Table readers:** E210.
**Unresolved SQL affecting any column:** E210. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E608.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `snapshot_id` | E210 | E210 | — | — | — | unknown |
| `source_key`, `candidate_id` | E210 | — | — | — | — | unknown |

### `ops.resource_observation`

**Definition:** `migrations/0580_resource_observation.sql:36`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E389. **Table readers:** E344, E389.
**Additional lexical/dependency evidence:** E344, E389.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E344, E389 | — | E389 | — | unknown |
| `provider`, `account`, `project`, `product`, `period`, `as_of`, `quantity`, `quantity_unit`, `allowance`, `policy`, `estimate`, `charge`, `measured_capacity`, `configured_capacity`, `model_route`, `state`, `reason`, `source`, `observed_at` | E389 | E344, E389 | — | — | — | unknown |

### `ops.resource_observation_receipt`

**Definition:** `migrations/0580_resource_observation.sql:76`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E389. **Table readers:** E389.
**Additional lexical/dependency evidence:** E389.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E389 | — | E389 | — | unknown |
| `resource_observation_id`, `idempotency_key`, `request_digest`, `recorded_by_actor_slug` | E389 | E389 | — | — | — | unknown |

### `ops.retired_build_admission_revision`

**Definition:** `migrations/0320_heavy_build_admission.sql:8`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `proposed_at` | — | — | — | declared; caller unknown | — | unknown |
| `admission_ref`, `work_request_id`, `plan_id`, `version`, `idempotency_key`, `tier`, `classifier_reasons`, `contract`, `builder_session_ref`, `admission_hash`, `proposed_by_actor_id` | — | — | — | — | — | unknown |

### `ops.retired_build_plan_review`

**Definition:** `migrations/0320_heavy_build_admission.sql:26`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `reviewed_at` | — | — | — | declared; caller unknown | — | unknown |
| `review_ref`, `admission_id`, `version`, `idempotency_key`, `verdict`, `reviewer_actor_id`, `reviewer_session_ref`, `review_summary`, `evidence_refs`, `gaps`, `review_hash` | — | — | — | — | — | unknown |

### `ops.rule_admission`

**Definition:** `migrations/0148_control_plane_admission.sql:30`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E15, E34, E35, E368, E1289, E1492. **Table readers:** E15, E32, E34, E35, E39, E73, E343, E368, E421, E761, E1077, E1289, E1406, E1407, E1437, E1490, E1492, E1674, E1689, E1690.
**Unresolved SQL affecting any column:** E34, E35, E422. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E15, E32, E39, E73, E343, E368, E421.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `rule_id` | E34, E35, E1289, E1492 | E15, E32, E39, E73, E343, E368, E421, E761, E1077, E1406, E1437, E1490, E1511, E1674, E1689, E1690 | — | — | — | unknown |
| `guidance_intake_id` | E34, E35, E1289, E1492 | E15, E35, E73, E368, E421, E1289, E1406, E1492 | — | — | — | unknown |
| `enforcement_class` | E34, E35, E368, E1289, E1492 | E15, E32, E35, E73, E343, E368, E421, E761, E1289, E1406, E1437, E1492, E1674, E1689, E1690 | — | — | — | unknown |
| `binding_moment` | E34, E35, E1289, E1492 | E15, E32, E35, E73, E343, E368, E421, E761, E1077, E1289, E1406, E1492, E1674, E1689, E1690 | — | — | — | unknown |
| `applicability` | E34, E35, E1289, E1492 | E15, E32, E35, E73, E368, E421, E1289, E1492, E1674, E1689 | — | declared; caller unknown | — | unknown |
| `projection`, `reachability` | E34, E35, E1289, E1492 | E15, E32, E35, E73, E368, E421, E1289, E1492, E1689 | — | — | — | unknown |
| `input_contract` | E34, E35, E1289, E1492 | E15, E32, E35, E73, E368, E421, E1289, E1492 | — | — | — | unknown |
| `fixture_refs` | E15, E34, E35, E1289, E1492 | E15, E32, E35, E73, E343, E368, E421, E1289, E1492 | — | declared; caller unknown | — | unknown |
| `state` | E34, E35, E1289, E1492 | E15, E32, E39, E73, E343, E368, E421, E761, E1077, E1406, E1407, E1437, E1492, E1674, E1689 | — | declared; caller unknown | — | unknown |
| `admitted_by` | E15, E34, E35, E1289, E1492 | E15, E32, E35, E73, E368, E421, E1289, E1492 | — | — | — | unknown |
| `admitted_at` | E34, E35, E1289, E1492 | E15, E35, E73, E343, E368, E421, E1289, E1492 | — | — | — | unknown |
| `reason` | E34, E35, E1289, E1492 | E15, E35, E73, E343, E368, E421, E761, E1077, E1289, E1406, E1490, E1492 | — | — | — | unknown |
| `version` | E15, E35, E1289, E1492 | E15, E35, E73, E368, E421, E1077, E1289, E1406, E1492, E1689 | — | E34, E35, E1289, E1492 | — | unknown |
| `updated_at` | E15, E35, E1289, E1492 | E15, E73, E368, E421, E761, E1077, E1406 | — | E34, E35, E1289, E1492 | — | unknown |
| `enforcement_status` | E15, E35 | E15, E32, E35, E73, E343, E368, E421, E761, E1690 | — | E34, E1289, E1492 | — | unknown |
| `coverage_detail` | E15, E35 | E15, E35, E73, E368, E421, E1690 | — | E34, E1289, E1492 | — | unknown |

### `ops.rule_admission_reclassification_receipt`

**Definition:** `migrations/0352_legacy_admission_reclassification_and_staging_authority.sql:28`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E368. **Table readers:** E368.
**Additional lexical/dependency evidence:** E368.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E368 | — | E368 | — | unknown |
| `idempotency_key`, `rule_id`, `enforcement_class_before`, `enforcement_class_after`, `actor_id`, `reason`, `legacy_admission`, `contract_hash` | E368 | E368 | — | — | — | unknown |

### `ops.rule_amendment_receipt`

**Definition:** `migrations/0349_versioned_rule_amendment.sql:53`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E17, E18. **Table readers:** E17, E18, E437.
**Unresolved SQL affecting any column:** E421. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E17, E18, E437.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E17, E18 | — | E17, E18 | — | unknown |
| `idempotency_key`, `new_statement`, `amended_by`, `rationale`, `contract_hash`, `amended_at`, `legacy_admission` | E17, E18 | E17, E18 | — | — | — | unknown |
| `rule_id`, `rule_version_before`, `rule_version_after`, `prior_statement_hash`, `new_statement_hash` | E17, E18 | E17, E18, E437 | — | — | — | unknown |
| `prior_scope`, `new_scope` | E17 | E17, E18 | — | — | — | unknown |

### `ops.rule_approval_lifecycle_anchor`

**Definition:** `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:260`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E32, E429.
**Unresolved SQL affecting any column:** E35. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E32, E429.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `approval_receipt_id` | — | E32, E429, E1605 | — | — | — | unknown |
| `rule_id` | — | E32, E429, E1605 | E429 | — | — | unknown |
| `rule_version_after` | — | E32, E429, E1605 | — | — | E1605 | unknown |
| `statement_hash` | — | E32, E429, E1605 | E429 | — | E1605 | unknown |
| `anchored_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.rule_approval_receipt`

**Definition:** `migrations/0194_atomic_rule_approval.sql:120`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E15, E35. **Table readers:** E15, E32, E35, E264, E421, E429, E690, E1490, E1690.
**Unresolved SQL affecting any column:** E15, E368, E403, E404, E421, E422. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E32, E35, E264, E429, E690.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E15, E32, E35, E421, E429, E690, E1690 | — | E15, E35 | — | unknown |
| `idempotency_key`, `actor_id`, `requested_control_keys`, `normalized_contract`, `contract_hash`, `evidence_refs` | E15, E35 | E15, E32, E35, E421 | — | — | — | unknown |
| `rule_id` | E15, E35 | E15, E32, E35, E264, E421, E690, E1490, E1690 | E429, E690 | — | — | unknown |
| `rule_version` | E15, E35 | E15, E32, E35, E421, E429, E690 | — | — | — | unknown |
| `statement_hash` | E15, E35 | E15, E32, E35, E421, E429, E690 | E429 | — | — | unknown |
| `policy_kind`, `enforcement_status` | E15, E35 | E15, E32, E35, E421, E690 | — | — | — | unknown |
| `installed_control_keys` | E15, E35 | E15, E35, E421, E690 | — | — | — | unknown |
| `reason` | E15, E35 | E15, E35, E421 | — | — | — | unknown |
| `created_at` | — | E15, E35, E264, E421, E429, E1690 | — | E15, E35 | — | unknown |

### `ops.rule_control_binding`

**Definition:** `migrations/0194_atomic_rule_approval.sql:57`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E72, E659. **Table readers:** E15, E32, E35, E72, E659, E690, E750, E764.
**Unresolved SQL affecting any column:** E35, E72, E421, E422, E659. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E15, E32, E690.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `rule_id` | E72, E659 | E15, E32, E35, E690, E750, E764, E1512 | E690 | — | — | unknown |
| `control_key` | E72, E659 | E32, E690, E764 | — | — | — | unknown |
| `statement_hash` | E72, E659 | E15, E32, E35, E72, E690 | — | — | — | unknown |
| `binding_contract` | E72, E659 | E72 | — | — | — | unknown |
| `bound_at` | E72 | E764 | — | E72, E659 | — | unknown |

### `ops.rule_delivery_activation_receipt`

**Definition:** `migrations/0317_atomic_rule_delivery_cutover.sql:39`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E439.
**Additional lexical/dependency evidence:** E439.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `from_mode`, `to_mode`, `changed_by`, `reason`, `map_digest`, `target_short_ids` | — | — | — | — | — | unknown |

### `ops.rule_delivery_activation_target`

**Definition:** `migrations/0317_atomic_rule_delivery_cutover.sql:7`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E439.
**Additional lexical/dependency evidence:** E439.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `short_id`, `expected_scope`, `expected_pack`, `from_control`, `from_enforcement_class`, `from_implementation_ref`, `from_test_ref`, `to_control`, `to_enforcement_class`, `to_implementation_ref`, `to_test_ref`, `map_digest` | — | — | — | — | — | unknown |

### `ops.rule_delivery_policy`

**Definition:** `migrations/0291_rule_delivery_layers.sql:132`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E438, E439, E590, E830.
**Unresolved SQL affecting any column:** E590. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E438, E439.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `singleton` | — | E439, E590 | — | declared; caller unknown | — | unknown |
| `mode` | — | E438, E439, E590, E830 | — | declared; caller unknown | — | unknown |
| `changed_by`, `reason` | — | — | — | — | — | unknown |
| `changed_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.rule_enforcement_fallback_receipt`

**Definition:** `migrations/0721_a02_rule_enforcement_coverage.sql:14`. **Product path:** positive static evidence (column detail below).

**Table writers:** E390. **Table readers:** E390, E690.
**Additional lexical/dependency evidence:** E390, E690.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E390 | — | E390 | — | yes |
| `idempotency_key`, `fallback_kind`, `procedure_ref`, `reason`, `recorded_by`, `recorded_by_slug` | E390 | E390 | — | — | — | yes |
| `rule_id` | E390 | E390, E690 | E690 | — | — | yes |
| `rule_version`, `statement_hash` | E390 | E390, E690 | — | — | — | yes |

### `ops.rule_enforcement_point`

**Definition:** `migrations/0148_control_plane_admission.sql:64`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E15, E35, E1290, E1291, E1493, E1494. **Table readers:** E15, E32, E35, E39, E690, E706, E763, E1290, E1291, E1493, E1494, E1674, E1689, E1690.
**Row deletion:** E1493.
**Unresolved SQL affecting any column:** E35, E421, E422. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E15, E32, E39, E690, E706.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | E39 | E15, E35, E1291, E1494 | — | unknown |
| `rule_id` | E15, E35, E1291, E1494 | E15, E32, E35, E39, E690, E706, E763, E1290, E1493, E1513, E1674, E1689, E1690 | E690 | — | — | unknown |
| `control_key` | E15, E35, E1291, E1494 | E15, E32, E35, E690, E706, E763, E1290, E1674, E1690 | E15, E35 | — | — | unknown |
| `implementation_ref` | E15, E35, E1291, E1494 | E15, E35, E706, E763, E1291, E1674 | E15, E35 | — | — | unknown |
| `test_ref` | E15, E35, E1291, E1494 | E15, E35, E690, E706, E1291, E1674 | E15, E35 | — | — | unknown |
| `enforcement_class` | E15, E35, E1291, E1494 | E15, E35, E763, E1291 | E15, E35 | — | — | unknown |
| `installed` | E15, E35, E1290, E1291, E1494 | E32, E39, E690, E706, E763, E1291, E1674, E1689, E1690 | — | declared; caller unknown | — | unknown |
| `verified_at` | E15, E35, E1290, E1291, E1494 | E15, E35, E690, E763, E1291, E1674 | E15, E35 | — | — | unknown |

### `ops.rule_f05_contract`

**Definition:** `migrations/0724_f05_live_rule_context.sql:5`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E71. **Table readers:** E71, E183.
**Additional lexical/dependency evidence:** E71, E183.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `binding_seq` | — | E71, E183 | — | — | — | unknown |
| `idempotency_key`, `input_contract`, `contract_hash`, `bound_by` | E71 | E71 | — | — | — | unknown |
| `rule_id`, `rule_version`, `statement_hash`, `contract` | E71 | E71, E183 | — | — | — | unknown |
| `bound_at` | E71 | E71 | — | declared; caller unknown | — | unknown |

### `ops.rule_insert_provenance`

**Definition:** `migrations/0785_rule_teach_supersession.sql:16`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E391. **Table readers:** E391.
**Unresolved SQL affecting any column:** E430. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E391.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `rule_id`, `taught_by`, `transaction_id` | E391 | — | — | — | — | unknown |

### `ops.rule_load_layer`

**Definition:** `migrations/0291_rule_delivery_layers.sql:74`. **Product path:** positive static evidence (column detail below).

**Table writers:** E73, E432, E1497, E1498. **Table readers:** E73, E432, E438, E440, E441, E590, E765, E830, E1413, E1497, E1498.
**Row deletion:** E432, E1498.
**Unresolved SQL affecting any column:** E34. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E73, E432, E438, E440, E441, E590.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `rule_id` | E73, E1497 | E73, E432, E438, E440, E441, E590, E765 | — | — | — | yes |
| `short_id` | E73, E1497 | E73, E440, E590, E765, E1497, E1498, E1607 | — | — | — | yes |
| `load_layer` | E73, E1497 | E73, E438, E440, E590, E765, E1497 | — | — | — | yes |
| `packs` | E73, E1497 | E73, E438, E440, E441, E590, E765, E1497, E1607 | — | declared; caller unknown | — | yes |
| `scope` | E73, E1497 | E73, E438, E440, E590, E765, E830, E1497 | — | — | — | yes |
| `why` | E73, E1497 | E73, E1497 | — | — | — | unknown |
| `source` | E73, E1497 | E73, E1497 | E590 | — | — | unknown |
| `map_digest` | E73, E1497 | E73, E590, E830, E1413, E1497 | — | — | — | yes |
| `updated_at` | E73, E1497 | E73 | — | E73, E1497 | — | unknown |

### `ops.rule_pack`

**Definition:** `migrations/0291_rule_delivery_layers.sql:55`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1495, E1496. **Table readers:** E438, E441, E590, E709, E762, E1495, E1496.
**Row deletion:** E1496.
**Additional lexical/dependency evidence:** E438, E441, E590, E709.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `pack` | E1495 | E441, E590, E709, E762, E1496 | — | — | — | yes |
| `title` | E1495 | E441, E590, E762, E1495 | — | — | — | yes |
| `description`, `triggers` | E1495 | E441, E590, E1495 | — | — | — | yes |
| `source` | E1495 | E762, E1495 | E590 | — | — | unknown |
| `updated_at` | E1495 | E762 | — | E1495 | — | unknown |

### `ops.rule_retirement_receipt`

**Definition:** `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:232`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E429, E430. **Table readers:** E429, E430.
**Unresolved SQL affecting any column:** E421. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E429, E430.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E429, E430 | — | E429, E430 | — | unknown |
| `idempotency_key`, `rule_id`, `rule_version_before`, `rule_version_after`, `statement_hash`, `previous_status`, `actor_id`, `reason`, `superseded_by`, `contract_hash`, `retired_at` | E429, E430 | E429 | — | — | — | unknown |
| `approval_receipt_id`, `legacy_admission`, `missing_approval_receipt` | E429 | E429 | — | — | — | unknown |
| `created_at` | — | E429 | — | E429, E430 | — | unknown |

### `ops.run`

**Definition:** `migrations/0115_ops_observability.sql:168`. **Product path:** positive static evidence (column detail below).

**Table writers:** E392, E394, E1475. **Table readers:** E101, E323, E392, E394, E713, E1009, E1063, E1438, E1450, E1453, E1458, E1475, E1667, E1685, E1691, E1692.
**Unresolved SQL affecting any column:** E132, E392, E393, E394, E410, E412, E715, E716, E926. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E101, E323, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E101, E323, E392, E394, E713, E1009, E1450, E1453, E1475, E1667, E1685, E1692 | E1063 | E392, E394, E1475 | — | yes |
| `correlation_id` | E392, E394, E1475 | E713, E1453, E1582, E1654, E1667, E1685, E1692 | — | declared; caller unknown | — | yes |
| `kind`, `ended_at` | E392, E394, E1475 | E1009, E1063, E1654, E1667, E1685, E1692 | — | — | — | yes |
| `service_id` | E392, E394, E1475 | E101, E323, E1009, E1438, E1450, E1453, E1458, E1582, E1654, E1667, E1685, E1692 | E1063 | — | — | yes |
| `environment` | E392, E394, E1475 | E101, E323, E1009, E1450, E1453, E1458, E1654, E1667, E1685, E1692 | E1063 | — | — | yes |
| `run_key` | E392, E394, E1475 | E101, E323, E1450, E1453, E1458, E1582, E1654, E1667, E1685, E1692 | — | — | — | yes |
| `state` | E392, E394, E1475 | E101, E323, E1009, E1063, E1450, E1453, E1458, E1654, E1667, E1685, E1692 | — | — | E1654 | yes |
| `failure_class` | E1475 | E1453, E1667, E1685, E1692 | — | — | — | yes |
| `exit_code`, `budget_ms` | E1475 | E1667, E1685 | — | — | — | unknown |
| `attempt` | E1475 | E1667, E1685 | — | E392, E394 | — | unknown |
| `started_at` | E392, E394, E1475 | E1438, E1654, E1667, E1685, E1692 | — | — | — | yes |
| `duration_ms` | — | E1667, E1685 | — | — | — | unknown |
| `source_kind` | E392, E394, E1475 | E1667, E1685, E1692 | — | — | — | yes |
| `source_ref` | E392, E394, E1475 | E1592, E1667, E1685, E1692 | — | — | — | yes |
| `observed_at` | E392, E394, E1475 | E101, E1450, E1453, E1458, E1667, E1685, E1692 | E1063 | declared; caller unknown | — | yes |
| `expires_at` | E1475 | E1667, E1685, E1692 | — | — | — | yes |
| `evidence_ref` | E392, E394, E1475 | E323, E1009, E1063, E1654, E1667, E1685 | — | — | — | yes |
| `detail` | E1475 | E1453, E1592, E1667, E1685, E1692 | — | — | — | yes |
| `release_id` | E392, E394, E1475 | E323, E1582, E1654, E1667, E1685 | — | — | — | unknown |
| `recovery_strategy`, `recovery_plan_ref` | E392, E394, E1475 | E1654, E1667, E1685 | — | — | — | unknown |
| `recovery_rehearsal_bundle_id` | E392, E394 | E323, E1654, E1667, E1685 | — | — | — | unknown |

### `ops.rw02_attended_run`

**Definition:** `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:29`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E448. **Table readers:** E443, E444, E448.
**Additional lexical/dependency evidence:** E443, E444, E448.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `seq` | — | E443, E444, E448 | — | — | — | unknown |
| `tenant`, `recorded_at` | — | E448 | — | E448 | — | unknown |
| `action_kind`, `outcome` | E448 | E443, E444, E448 | — | — | — | unknown |
| `run_ref` | E448 | E443, E448 | — | — | — | unknown |
| `stop_class`, `reason_id`, `recorded_by` | E448 | E448 | — | — | — | unknown |
| `execution_mode` | — | E443, E448 | — | E448 | — | unknown |

### `ops.rw02_consent_revocation`

**Definition:** `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:55`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E450. **Table readers:** E445, E450.
**Additional lexical/dependency evidence:** E445, E450.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `revoked_at` | — | E450 | — | E450 | — | unknown |
| `decision_id` | E450 | E445, E450 | — | — | — | unknown |
| `revoked_by`, `revoked_by_actor_id`, `human_quote`, `idempotency_key` | E450 | E450 | — | — | — | unknown |

### `ops.rw02_runtime_record`

**Definition:** `migrations/0726_salesforce_reconciliation_rw02_store.sql:4`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E447. **Table readers:** E442, E447, E449.
**Unresolved SQL affecting any column:** E447, E448. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E442, E449.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E442, E447, E449 | — | E447 | — | unknown |
| `tenant`, `operation`, `action_kind`, `outcome` | E447 | E442, E447, E449 | — | — | — | unknown |
| `idempotency_key`, `request_digest`, `step_key`, `record_digest`, `recorded_by` | E447 | E447, E449 | — | — | — | unknown |
| `recorded_at` | E447 | E442, E447, E449 | — | declared; caller unknown | — | unknown |

### `ops.scac_artifact_manifest`

**Definition:** `migrations/0456_siep13_artifact_registry.sql:6`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E452, E454.
**Unresolved SQL affecting any column:** E455. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E452, E454.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `artifact_digest`, `manifest_digest` | — | E452, E454, E1609 | — | — | — | unknown |
| `artifact_kind`, `media_type`, `byte_length`, `source_ref`, `source_digest`, `sbom_digest`, `provenance_digest`, `policy_epoch`, `policy_epoch_digest` | — | E452, E1609 | — | — | — | unknown |
| `created_at`, `root_trust_operational`, `production_enforcement_active` | — | E452 | — | declared; caller unknown | — | unknown |

### `ops.scac_artifact_signature`

**Definition:** `migrations/0456_siep13_artifact_registry.sql:26`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E452, E454, E456.
**Additional lexical/dependency evidence:** E452, E454, E456.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `signature_digest` | — | E456 | — | — | — | unknown |
| `manifest_digest` | — | E452, E454, E456, E1610 | — | — | — | unknown |
| `algorithm`, `public_key_bytes`, `signature_bytes`, `signed_payload_digest`, `signature_scope` | — | — | — | — | — | unknown |
| `signer_key_digest` | — | E454 | — | — | — | unknown |
| `recorded_at`, `root_trust_operational`, `production_enforcement_active` | — | — | — | declared; caller unknown | — | unknown |

### `ops.scac_artifact_transparency_entry`

**Definition:** `migrations/0456_siep13_artifact_registry.sql:44`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E452, E456.
**Additional lexical/dependency evidence:** E452, E456.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `entry_no`, `entry_digest`, `previous_entry_digest`, `signature_digest`, `statement_digest` | — | E456, E1611 | — | — | — | unknown |
| `manifest_digest` | — | E452, E456, E1611 | — | — | — | unknown |
| `entry_kind` | — | E456 | — | — | — | unknown |
| `included_at`, `root_trust_operational`, `production_enforcement_active` | — | E456 | — | declared; caller unknown | — | unknown |

### `ops.scac_capability_token_receipt`

**Definition:** `migrations/0465_siep17_token_challenge_authority.sql:94`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E592. **Table readers:** E451, E458, E592, E593.
**Additional lexical/dependency evidence:** E451, E458, E592, E593.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `token_ref_digest`, `challenge_id`, `challenge_digest`, `signed_payload_digest`, `signature_digest`, `issuer_key_digest`, `issuer_root_event_digest`, `external_verification_receipt_digest`, `issued_at`, `expires_at`, `record_idempotency_key` | E592 | E451, E458, E592, E593 | — | — | — | unknown |
| `cryptographic_state`, `admission_state`, `routing_eligible`, `privileges_active`, `production_enforcement_active`, `recorded_at` | — | E451, E458, E592, E593 | — | E592 | — | unknown |

### `ops.scac_device_benchmark_receipt`

**Definition:** `migrations/0460_siep15_device_enrollment.sql:86`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E451, E461.
**Additional lexical/dependency evidence:** E451, E461.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `device_ref`, `facts_digest`, `passed` | — | E451, E461, E1612 | — | — | — | unknown |
| `benchmark_kind`, `profile_digest`, `metrics_digest`, `observed_at`, `receipt_digest`, `signed_payload_digest` | — | E1612 | — | — | — | unknown |
| `schema_version`, `algorithm`, `signature_bytes`, `signature_digest`, `verifier_contract` | — | — | — | — | — | unknown |
| `cryptographic_device_state`, `routing_eligible`, `privileges_active`, `production_enforcement_active`, `recorded_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.scac_device_enrollment`

**Definition:** `migrations/0460_siep15_device_enrollment.sql:5`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E451, E460, E461, E462, E465.
**Unresolved SQL affecting any column:** E459. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E451, E460, E461, E462, E465.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `device_ref`, `sponsor`, `profile_key`, `device_key_digest`, `device_public_key`, `policy_epoch`, `policy_epoch_digest`, `facts_digest` | — | E451, E460, E461, E462, E465 | — | — | — | unknown |
| `lifecycle_state`, `optional_non_blocking`, `source_of_truth`, `critical_dependency`, `routing_eligible`, `privileges_active`, `production_enforcement_active`, `recorded_at` | — | E451, E460, E461, E462, E465 | — | declared; caller unknown | — | unknown |

### `ops.scac_device_fact_receipt`

**Definition:** `migrations/0460_siep15_device_enrollment.sql:28`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E461.
**Unresolved SQL affecting any column:** E451, E460. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E461.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `device_ref` | — | E461, E1613 | — | — | — | unknown |
| `schema_version`, `observed_at`, `algorithm`, `signature_bytes`, `signature_digest`, `verifier_contract` | — | — | — | — | — | unknown |
| `facts`, `facts_digest`, `signed_payload_digest` | — | E1613 | — | — | — | unknown |
| `cryptographic_device_state`, `recorded_at`, `production_enforcement_active` | — | — | — | declared; caller unknown | — | unknown |

### `ops.scac_exact_effect_contract`

**Definition:** `migrations/0469_siep18_exact_effects_trusted_principal.sql:11`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E597. **Table readers:** E464, E597.
**Additional lexical/dependency evidence:** E464, E597.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `registry_version`, `ingress_key`, `registry_entry_digest`, `contract`, `contract_digest`, `idempotency_key`, `recorded_by` | E597 | E464, E597 | — | — | — | unknown |
| `recorded_at`, `production_enforcement_active` | — | E464, E597 | — | E597 | — | unknown |

### `ops.scac_mutation_registry_entry`

**Definition:** `migrations/0454_siep11_mutation_registry.sql:25`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E451, E465, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E597, E598, E1762.
**Unresolved SQL affecting any column:** E451, E598. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E465, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E597.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `registry_version`, `ingress_key`, `contract` | — | E451, E465, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E597, E598, E1762 | — | — | — | yes |
| `ingress_kind`, `entry_digest` | — | E451, E465, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E597, E598 | — | — | — | unknown |
| `effect_class`, `source_locator` | — | E451, E465, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E597, E598 | — | — | — | unknown |
| `registered_at` | — | E451, E465, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E597, E598 | — | declared; caller unknown | — | unknown |

### `ops.scac_mutation_registry_version`

**Definition:** `migrations/0454_siep11_mutation_registry.sql:5`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E467, E468, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E596, E1762.
**Unresolved SQL affecting any column:** E466. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E467, E468, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E596.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `registry_version` | — | E467, E468, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E596, E1762 | — | — | — | yes |
| `program_key`, `package_key`, `charter_digest`, `catalog_projection`, `entry_set_digest`, `mcp_default_deny_source_guarded`, `db_metadata_authority`, `runtime_projection_authorizing`, `non_mcp_default_deny_operational`, `atomic_database_mediation_operational`, `direct_database_grant_cutover`, `production_enforcement_active` | — | E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E596 | — | — | — | unknown |
| `registry_digest`, `entry_count`, `source_entry_count` | — | E467, E468, E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E596 | — | — | — | unknown |
| `sealed_at` | — | E469, E470, E471, E472, E473, E474, E475, E476, E477, E478, E479, E480, E481, E482, E483, E484, E485, E486, E487, E488, E489, E490, E491, E492, E493, E494, E495, E496, E497, E498, E499, E500, E501, E502, E503, E504, E505, E506, E507, E508, E509, E510, E511, E512, E513, E514, E515, E516, E517, E518, E519, E520, E521, E522, E523, E524, E525, E526, E527, E528, E529, E530, E531, E532, E533, E534, E535, E536, E537, E538, E539, E540, E541, E542, E543, E544, E545, E546, E547, E548, E549, E550, E551, E552, E553, E554, E555, E556, E557, E558, E559, E560, E561, E562, E563, E564, E565, E566, E567, E568, E569, E570, E571, E572, E573, E574, E575, E576, E577, E578, E579, E580, E581, E582, E583, E584, E585, E586, E587, E596 | — | declared; caller unknown | — | unknown |

### `ops.scac_operation_effect_binding`

**Definition:** `migrations/0467_siep18_atomic_db_monitor_grants.sql:51`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E598. **Table readers:** E451, E598.
**Additional lexical/dependency evidence:** E451, E598.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `registry_version`, `ingress_key`, `privilege_bundle`, `registry_entry_digest`, `effect_keys`, `effect_set_digest`, `binding_digest`, `idempotency_key`, `recorded_by` | E598 | E451, E598 | — | — | — | unknown |
| `recorded_at`, `production_enforcement_active` | — | E451, E598 | — | E598 | — | unknown |

### `ops.scac_policy_epoch`

**Definition:** `migrations/0455_siep12_policy_epoch.sql:19`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E589. **Table readers:** E183, E451, E465, E588, E589, E591, E596.
**Unresolved SQL affecting any column:** E453, E459, E603. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E183, E451, E465, E588, E589, E591, E596.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `epoch` | E589 | E183, E451, E465, E588, E589, E591, E596 | — | — | — | unknown |
| `epoch_digest` | E589 | E451, E465, E588, E589, E591, E596 | — | — | — | unknown |
| `previous_epoch`, `previous_epoch_digest`, `program_key`, `tenant_scope`, `policy_domain`, `registry_version`, `registry_digest`, `doctrine_generation`, `doctrine_projection_digest`, `rule_projection_digest`, `schema_applied_count`, `schema_highest_migration`, `schema_ledger_digest`, `source_digest`, `source_session_user`, `source_relation` | E589 | E451, E465, E588, E589, E596 | — | — | — | unknown |
| `created_at` | E589 | E451, E465, E588, E589, E596 | — | declared; caller unknown | — | unknown |
| `atomic_database_mediation_operational`, `production_enforcement_active` | — | E451, E465, E588, E589, E596 | — | E589 | — | unknown |

### `ops.scac_pop_challenge`

**Definition:** `migrations/0465_siep17_token_challenge_authority.sql:40`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E465. **Table readers:** E451, E458, E459, E465, E592, E593.
**Additional lexical/dependency evidence:** E451, E458, E459, E465, E592, E593.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `challenge_id` | E465 | E451, E458, E459, E465, E592, E593 | — | declared; caller unknown | — | unknown |
| `schema_version`, `tenant_scope`, `environment`, `principal_digest`, `device_ref`, `device_key_digest`, `facts_digest`, `workload_digest`, `policy_epoch`, `policy_epoch_digest`, `registry_version`, `registry_digest`, `ingress_key`, `mutation_kind`, `target_surface`, `operation_manifest_digest`, `request_digest`, `idempotency_digest`, `nonce_bytes`, `nonce_digest`, `issued_at`, `expires_at`, `challenge_digest`, `request_fingerprint`, `issue_idempotency_key` | E465 | E451, E458, E459, E465, E592, E593 | — | — | — | unknown |
| `production_enforcement_active` | — | E451, E458, E459, E465, E592, E593 | — | E465 | — | unknown |

### `ops.scac_pop_challenge_consumption`

**Definition:** `migrations/0465_siep17_token_challenge_authority.sql:80`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E459. **Table readers:** E459, E592.
**Unresolved SQL affecting any column:** E465. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E459, E592.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `challenge_id` | E459 | E459, E592 | — | — | — | unknown |
| `challenge_digest`, `pop_verification_digest`, `consumption_digest`, `consume_idempotency_key` | E459 | E459 | — | — | — | unknown |
| `consumed_at`, `token_intent_state`, `production_enforcement_active` | — | E459 | — | E459 | — | unknown |

### `ops.scac_reference_monitor_mode_event`

**Definition:** `migrations/0467_siep18_atomic_db_monitor_grants.sql:71`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E605. **Table readers:** E595, E605.
**Additional lexical/dependency evidence:** E595, E605.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `event_no`, `event_digest`, `previous_event_digest`, `mode`, `reason_digest`, `grant_digest`, `idempotency_key`, `recorded_by` | E605 | E595, E605 | — | — | — | unknown |
| `recorded_at`, `production_enforcement_active` | — | E595, E605 | — | E605 | — | unknown |

### `ops.scac_reference_monitor_receipt`

**Definition:** `migrations/0467_siep18_atomic_db_monitor_grants.sql:89`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E451. **Table readers:** E451, E594.
**Additional lexical/dependency evidence:** E451, E594.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `admission_id` | E451 | E451, E594 | — | declared; caller unknown | — | unknown |
| `token_ref_digest`, `operation_manifest_digest`, `request_payload_digest`, `idempotency_digest`, `ingress_key`, `effect_keys`, `effect_set_digest`, `operation_effect_binding_digest`, `policy_epoch`, `policy_epoch_digest`, `registry_version`, `registry_digest`, `principal_digest`, `session_principal`, `privilege_bundle`, `grant_digest`, `backend_pid`, `transaction_id`, `admission_idempotency_key`, `decision`, `monitor_state`, `direct_grant_guarded` | E451 | E451, E594 | — | — | — | unknown |
| `production_enforcement_active`, `recorded_at` | — | E451, E594 | — | E451 | — | unknown |

### `ops.scac_root_custodian_attestation`

**Definition:** `migrations/0458_siep14_root_trust.sql:51`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E602.
**Additional lexical/dependency evidence:** E602.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `event_digest`, `custodian_key_digest` | — | E602, E1614 | — | — | — | unknown |
| `algorithm`, `public_key_bytes`, `signature_bytes`, `verifier_contract` | — | — | — | — | — | unknown |
| `signature_digest` | — | E602, E1614 | — | — | E1614 | unknown |
| `signed_payload_digest` | — | E1614 | — | — | — | unknown |
| `recorded_at`, `production_trust_active` | — | — | — | declared; caller unknown | — | unknown |

### `ops.scac_root_custodian_set_member`

**Definition:** `migrations/0458_siep14_root_trust.sql:43`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E602, E603.
**Unresolved SQL affecting any column:** E601. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E602, E603.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `custodian_set_digest`, `custodian_key_digest` | — | E602, E603 | — | — | — | unknown |
| `recorded_at`, `production_trust_active` | — | — | — | declared; caller unknown | — | unknown |

### `ops.scac_root_trust_event`

**Definition:** `migrations/0458_siep14_root_trust.sql:15`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E601, E602, E603.
**Additional lexical/dependency evidence:** E601, E602, E603.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `event_no`, `event_digest`, `previous_event_digest`, `subject_key_digest`, `replacement_key_digest`, `threshold`, `custodian_set_digest`, `custodian_approval_digests`, `recovery_receipt_digest`, `policy_epoch`, `policy_epoch_digest` | — | E601, E602, E603, E1615 | — | — | — | unknown |
| `action` | — | E601, E602, E603, E1615 | — | — | E1615 | unknown |
| `recorded_at`, `production_trust_active` | — | E601, E602, E603 | — | declared; caller unknown | — | unknown |

### `ops.scac_root_trust_key`

**Definition:** `migrations/0458_siep14_root_trust.sql:5`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `key_digest`, `algorithm`, `public_key_bytes`, `key_purpose` | — | — | — | — | — | unknown |
| `recorded_at`, `production_trust_active` | — | — | — | declared; caller unknown | — | unknown |

### `ops.scac_token_issuer_binding`

**Definition:** `migrations/0467_siep18_atomic_db_monitor_grants.sql:10`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E599. **Table readers:** E593, E599.
**Additional lexical/dependency evidence:** E593, E599.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `issuer_key_digest`, `issuer_root_event_digest`, `certification_receipt_digest`, `idempotency_key`, `recorded_by` | E599 | E593, E599 | — | — | — | unknown |
| `binding_state`, `recorded_at`, `production_enforcement_active` | — | E593, E599 | — | E599 | — | unknown |

### `ops.scac_token_kill_switch_event`

**Definition:** `migrations/0465_siep17_token_challenge_authority.sql:9`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E606. **Table readers:** E604, E606.
**Additional lexical/dependency evidence:** E604, E606.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `event_no`, `event_digest`, `previous_event_digest`, `action`, `reason_digest`, `idempotency_key`, `recorded_by` | E606 | E604, E606 | — | — | — | unknown |
| `recorded_at`, `production_enforcement_active` | — | E604, E606 | — | E606 | — | unknown |

### `ops.scac_token_revocation_event`

**Definition:** `migrations/0465_siep17_token_challenge_authority.sql:26`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E600. **Table readers:** E458, E600.
**Unresolved SQL affecting any column:** E451, E459, E465, E592. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E458, E600.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `event_id`, `recorded_at`, `production_enforcement_active` | — | E600 | — | E600 | — | unknown |
| `subject_kind`, `subject_digest` | E600 | E458, E600 | — | — | — | unknown |
| `reason_digest`, `idempotency_key`, `recorded_by` | E600 | E600 | — | — | — | unknown |

### `ops.scac_token_verification_binding`

**Definition:** `migrations/0467_siep18_atomic_db_monitor_grants.sql:26`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E593. **Table readers:** E451, E593.
**Additional lexical/dependency evidence:** E451, E593.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `token_ref_digest`, `token_payload`, `token_payload_digest`, `challenge_digest`, `issuer_key_digest`, `issuer_root_event_digest`, `verification_binding_digest`, `idempotency_key`, `verifier_contract` | E593 | E451, E593 | — | — | — | unknown |
| `recorded_at`, `production_enforcement_active` | — | E451, E593 | — | E593 | — | unknown |

### `ops.scac_trusted_principal_binding`

**Definition:** `migrations/0469_siep18_exact_effects_trusted_principal.sql:28`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E457. **Table readers:** E457.
**Additional lexical/dependency evidence:** E457.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `binding_id` | E457 | E457 | — | declared; caller unknown | — | unknown |
| `principal_manifest`, `principal_digest`, `actor_id`, `actor_slug`, `session_principal`, `privilege_bundle`, `backend_pid`, `transaction_id`, `binding_digest`, `idempotency_key`, `source_contract` | E457 | E457 | — | — | — | unknown |
| `recorded_at`, `production_enforcement_active` | — | E457 | — | E457 | — | unknown |

### `ops.service`

**Definition:** `migrations/0115_ops_observability.sql:95`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1469, E1473. **Table readers:** E101, E756, E757, E758, E768, E940, E944, E1009, E1063, E1390, E1438, E1450, E1453, E1458, E1466, E1468, E1469, E1471, E1472, E1473, E1476, E1671, E1687, E1691, E1692.
**Additional lexical/dependency evidence:** E101.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E101, E757, E758, E768, E940, E944, E1009, E1063, E1390, E1438, E1450, E1453, E1458, E1466, E1468, E1471, E1476, E1687, E1691, E1692 | E1063 | E1469 | — | yes |
| `key` | E1469 | E101, E756, E757, E758, E768, E940, E944, E1009, E1063, E1390, E1438, E1450, E1453, E1458, E1468, E1471, E1472, E1473, E1671, E1687, E1691, E1692 | — | — | — | yes |
| `name` | E1469 | E756, E940, E1063, E1469, E1687, E1691 | — | — | — | unknown |
| `purpose`, `family`, `repo_path`, `runbook_ref` | E1469 | E1469, E1687 | — | — | — | unknown |
| `criticality` | E1469 | E756, E940, E1453, E1466, E1469, E1476, E1687, E1691 | — | declared; caller unknown | — | unknown |
| `owner_actor`, `runtime` | E1469 | E1063, E1469, E1687 | — | — | — | unknown |
| `registered_at` | — | E1687 | — | E1469 | — | unknown |
| `retired_at` | E1469, E1473 | E756, E944, E1063, E1390, E1453, E1472, E1671, E1687, E1691 | — | — | — | unknown |
| `updated_at` | E1469, E1473 | E756, E1671, E1687 | — | declared; caller unknown | — | unknown |

### `ops.service_dependency`

**Definition:** `migrations/0115_ops_observability.sql:123`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1471. **Table readers:** E758, E1471.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `service_id`, `depends_on_id` | E1471 | E758 | — | — | — | unknown |
| `note` | E1471 | — | — | — | — | unknown |

### `ops.service_environment`

**Definition:** `migrations/0115_ops_observability.sql:136`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1470. **Table readers:** E757, E1063, E1470, E1475, E1691.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `service_id`, `environment` | E1470 | E757, E1063, E1475, E1691 | E1063 | — | — | unknown |
| `endpoint`, `deploy_mechanism` | E1470 | E757, E1470 | — | — | — | unknown |
| `expected_cadence_seconds` | E1470 | E1063, E1470, E1475, E1691 | — | — | — | unknown |
| `cadence_grace_seconds` | E1470 | E1063, E1470, E1475, E1691 | — | declared; caller unknown | — | unknown |
| `notes` | E1470 | E1470 | — | — | — | unknown |
| `updated_at` | E1470 | E757 | — | declared; caller unknown | — | unknown |

### `ops.settings_change`

**Definition:** `migrations/0118_settings_change.sql:37`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1467. **Table readers:** E1467.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1467 | — | E1467 | — | unknown |
| `recorded_at`, `correlation_id` | — | — | — | E1467 | — | unknown |
| `kind`, `target`, `reason`, `outcome`, `session_id`, `actor`, `command`, `environment` | E1467 | — | — | — | — | unknown |

### `ops.siep_command_receipt`

**Definition:** `migrations/0324_siep_program_authority.sql:120`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E618, E622, E631, E634. **Table readers:** E618, E622, E631, E634.
**Unresolved SQL affecting any column:** E619. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E618, E622, E631, E634.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key`, `verb`, `package_key`, `request_digest`, `result`, `recorded_actor_id` | E618, E622, E631, E634 | E618, E622, E631, E634 | — | — | — | unknown |
| `recorded_at` | — | E618, E622, E631, E634 | — | E618, E622, E631, E634 | — | unknown |

### `ops.siep_component_alias`

**Definition:** `migrations/0324_siep_program_authority.sql:72`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E626, E632.
**Additional lexical/dependency evidence:** E626, E632.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `alias_key` | — | E626, E632 | — | — | — | unknown |
| `package_key` | — | — | E626, E632 | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.siep_evidence_link`

**Definition:** `migrations/0324_siep_program_authority.sql:78`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E619. **Table readers:** E619, E623, E628, E629, E633, E634.
**Unresolved SQL affecting any column:** E618, E622, E631, E634. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E619, E623, E628, E629, E633.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E619, E628, E634 | E628, E634 | E619 | — | unknown |
| `package_key` | E619 | E619, E623, E628, E633, E634 | E628, E629 | — | — | unknown |
| `evidence_kind` | E619 | E619, E623, E628, E629, E633, E634 | E628 | — | — | unknown |
| `ledger_kind`, `ledger_id`, `evidence_digest`, `source_observed_at` | E619 | E619, E623, E628, E633, E634 | — | — | — | unknown |
| `work_request_version`, `manifest_digest` | E619 | E619, E623, E628, E634 | E628 | — | — | unknown |
| `note`, `attested_session_principal`, `idempotency_key` | E619 | E619, E628 | — | — | — | unknown |
| `linked_actor_id` | E619 | E619, E628, E634 | — | — | — | unknown |
| `linked_at` | — | E619, E628, E634 | — | E619 | — | unknown |

### `ops.siep_job_evidence_binding`

**Definition:** `migrations/0324_siep_program_authority.sql:134`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E621. **Table readers:** E619, E620, E621, E624, E628.
**Additional lexical/dependency evidence:** E619, E620, E621, E624, E628.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `job_id` | E621 | E619, E620, E621, E624, E628, E1617 | — | — | — | unknown |
| `package_key`, `work_request_version` | E621 | E619, E621, E624, E628, E1617 | E628 | — | — | unknown |
| `manifest_digest`, `evidence_kind` | E621 | E619, E621, E624, E628 | E628 | — | — | unknown |
| `definition_key`, `definition_version` | E621 | E619, E621, E624, E628, E1617 | — | — | — | unknown |
| `bound_by_actor_id`, `idempotency_key` | E621 | E619, E621, E624, E628 | — | — | — | unknown |
| `bound_at` | — | E619, E621, E624, E628 | — | E621 | — | unknown |
| `engineering_contract_version` | — | E619, E620, E621, E624, E628, E1617 | — | — | E1617 | unknown |

### `ops.siep_lane_lock`

**Definition:** `migrations/0324_siep_program_authority.sql:103`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E618, E622, E631, E634. **Table readers:** E618, E622, E631, E633, E634.
**Row deletion:** E631, E634.
**Unresolved SQL affecting any column:** E634. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E618, E622, E631, E633.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `lane_key`, `package_key`, `holder_actor_id`, `executor_tier`, `session_ref`, `lease_token`, `idempotency_key` | E618 | E618, E622, E631, E634 | — | — | — | unknown |
| `work_request_version` | E618, E622, E634 | E618, E622, E631, E634 | — | — | — | unknown |
| `acquired_at` | E618 | E618, E622, E631, E634 | — | E618 | — | unknown |
| `expires_at` | E618 | E618, E622, E631, E634 | E633 | — | — | unknown |

### `ops.siep_package_contract`

**Definition:** `migrations/0324_siep_program_authority.sql:47`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E618, E619, E621, E622, E623, E626, E628, E629, E630, E631, E632, E633, E634, E1666.
**Unresolved SQL affecting any column:** E203, E618, E622, E634. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E619, E621, E623, E626, E628, E629, E630, E631, E632, E633.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `package_key` | — | E618, E619, E621, E622, E623, E626, E628, E629, E630, E631, E633, E634, E1666 | E626, E628, E629, E632 | — | — | unknown |
| `work_request_id` | — | E618, E619, E621, E622, E623, E626, E628, E629, E630, E631, E633, E634, E1666 | — | — | — | unknown |
| `lane_key`, `test_contract`, `rollback_contract`, `required_evidence_kinds` | — | E618, E619, E622, E626, E628, E630, E631, E634 | — | — | — | unknown |
| `minimum_executor_tier`, `delivery_contract` | — | E618, E619, E622, E626, E628, E629, E630, E631, E634 | — | — | — | unknown |
| `approval_gate` | — | E618, E619, E622, E623, E626, E628, E629, E630, E631, E634 | — | declared; caller unknown | — | unknown |
| `created_at` | — | E618, E619, E622, E628, E630, E631, E634 | — | declared; caller unknown | — | unknown |

### `ops.siep_program_dependency`

**Definition:** `migrations/0324_siep_program_authority.sql:64`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E623, E626, E628, E629, E1666.
**Unresolved SQL affecting any column:** E622, E634. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E623, E626, E628, E629.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `package_key` | — | E623, E628, E629, E1666 | E626, E628, E629 | — | — | unknown |
| `depends_on_package_key` | — | E623, E626, E628, E629, E1666 | — | — | — | unknown |
| `created_at` | — | E628 | — | declared; caller unknown | — | unknown |

### `ops.slice_checkable_done_registration`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:214`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E346.
**Unresolved SQL affecting any column:** E110. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E346, E406, E407, E614.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slice_id`, `registered_by_actor_slug`, `idempotency_key`, `catalog_revision_id` | — | E346 | — | — | — | unknown |
| `created_at`, `registered_via` | — | E346 | — | declared; caller unknown | — | unknown |

### `ops.slice_checkable_done_registry`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:227`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E346, E638, E639.
**Additional lexical/dependency evidence:** E346, E406, E407, E636, E638, E639.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E346, E639 | — | declared; caller unknown | — | unknown |
| `slice_id`, `criterion` | — | E346, E638, E639 | — | — | — | unknown |
| `evidence_kind`, `workflow_key`, `workflow_version`, `acceptance_mode`, `transition_to_stage`, `live_check_source`, `live_check_key`, `write_required_reason` | — | E346, E639 | — | — | — | unknown |

### `ops.slice_checkpoint`

**Definition:** `migrations/0517_program_controller_seams.sql:87`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E386. **Table readers:** E386, E1048.
**Additional lexical/dependency evidence:** E386.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E386 | — | E386 | — | unknown |
| `slice_ref`, `checkpoint_ref`, `base_commit_sha`, `worktree_ref`, `next_step_ref`, `reconstruction_source`, `recorded_at` | E386 | E1048 | — | — | — | unknown |
| `checkpoint_digest`, `lease_id` | E386 | — | — | — | — | unknown |
| `completed_step_refs`, `record_evidence_refs`, `inherited_transcript_used` | E386 | E1048 | — | declared; caller unknown | — | unknown |
| `written_at` | — | E1048 | — | E386 | — | unknown |

### `ops.slice_completion_mark`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:164`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E110. **Table readers:** E110, E299, E345, E346, E643, E1070, E1076, E1079.
**Additional lexical/dependency evidence:** E110, E271, E299, E330, E345, E346, E614, E642, E643.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E110, E345, E346 | — | E110 | — | unknown |
| `slice_id` | E110 | E110, E299, E345, E346, E643, E1070, E1076 | E1079 | — | — | unknown |
| `status` | E110 | E110, E345, E346, E1070, E1076, E1079 | — | — | — | unknown |
| `criteria_receipt`, `reason`, `marked_by_actor_slug`, `idempotency_key`, `confirmed_proposal_id` | E110 | E110, E345, E346 | — | — | — | unknown |
| `mark_seq` | — | E110, E299, E345, E346, E643, E1070, E1076, E1079 | — | — | — | unknown |
| `created_at` | — | E110, E345, E346 | E1070, E1076, E1079 | E110 | — | unknown |
| `marked_via` | E110 | E110, E345, E346, E643 | — | — | — | unknown |

### `ops.slice_completion_proposal`

**Definition:** `migrations/0628_doctorcre_slice_done_marker.sql:832`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E110, E299, E346, E1079.
**Additional lexical/dependency evidence:** E110, E299, E330, E346.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E110, E299, E346, E1079 | — | declared; caller unknown | — | unknown |
| `slice_id` | — | E110, E299, E346, E1079 | E1079 | — | — | unknown |
| `criteria_receipt`, `reason`, `idempotency_key` | — | E110, E299, E346 | — | — | — | unknown |
| `proposed_by_actor_slug`, `basis_mark_seq`, `proposal_seq` | — | E110, E299, E346, E1079 | — | — | — | unknown |
| `created_at` | — | E110, E299, E346, E1079 | E1079 | declared; caller unknown | — | unknown |

### `ops.slice_criterion_binding`

**Definition:** `migrations/0628_doctorcre_slice_done_marker.sql:241`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E639.
**Unresolved SQL affecting any column:** E346. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E636, E639.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E639 | — | declared; caller unknown | — | unknown |
| `slice_id`, `criterion`, `evidence_kind`, `live_check_source`, `live_check_key`, `write_required_reason`, `bound_member_id`, `bound_via`, `bound_by_actor_slug`, `reason`, `idempotency_key`, `bind_seq` | — | E639 | — | — | — | unknown |

### `ops.slice_marker_seat`

**Definition:** `migrations/0628_doctorcre_slice_done_marker.sql:102`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Unresolved SQL affecting any column:** E644. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E271.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `actor_slug`, `note` | — | — | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.slice_source_lease`

**Definition:** `migrations/0517_program_controller_seams.sql:33`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E386. **Table readers:** E386, E1045.
**Additional lexical/dependency evidence:** E386.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E386 | — | E386 | — | unknown |
| `slice_ref`, `released_at` | E386 | E386, E1045 | — | — | — | unknown |
| `worktree_ref`, `worktree_path`, `branch_ref`, `base_commit_sha`, `source_paths`, `database_disposition`, `reuse_disposition` | E386 | E1045 | — | — | — | unknown |
| `database_resources`, `serialized_surfaces`, `repository_actions`, `model_roles` | E386 | E1045 | — | declared; caller unknown | — | unknown |
| `held_by_actor`, `correlation_id` | E386 | — | — | — | — | unknown |
| `opened_at` | — | — | — | E386 | — | unknown |

### `ops.source_merge_plan_scope`

**Definition:** `migrations/0470_source_merge_authority_projection.sql:9`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E90. **Table readers:** E80, E82, E90, E278.
**Additional lexical/dependency evidence:** E80, E82, E90, E278.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E80, E82, E278 | — | E90 | — | unknown |
| `organization_tenant_id`, `accepted_by_actor_id`, `repository`, `base_branch`, `authorized_paths`, `scope_digest` | E90 | E80, E278 | — | — | — | unknown |
| `work_request_id`, `accepted_plan_id`, `acceptance_receipt_id` | E90 | E80, E82, E278 | — | — | — | unknown |
| `created_at` | E90 | E80, E278 | — | declared; caller unknown | — | unknown |

### `ops.sourced_work_request_outcome_feedback`

**Definition:** `migrations/0179_program6_sourced_outcome_feedback.sql:8`. **Product path:** positive static evidence (column detail below).

**Table writers:** E331. **Table readers:** E2, E50, E107, E300, E331, E713.
**Unresolved SQL affecting any column:** E49, E331. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E2, E50, E107, E300, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E2, E50, E107, E300, E331 | E50 | E331 | — | yes |
| `work_request_id`, `feedback_hash`, `feedback_ref` | E331 | E2, E50, E107, E300, E331 | — | — | — | yes |
| `feedback_version`, `work_request_version`, `plan_acceptance_receipt_id`, `criterion_results`, `evidence_refs`, `outcome`, `blocker_code`, `result_summary`, `observed_minutes`, `interaction_surface`, `heavy_session_used`, `manual_context_transfers` | E331 | E2, E107, E300, E331 | — | — | — | yes |
| `idempotency_key`, `preimage` | E331 | E2, E107, E331 | — | — | — | yes |
| `plan_id` | E331 | E2, E50, E107, E300, E331 | E50 | — | — | yes |
| `created_at` | — | E2, E107, E300, E331 | — | E331 | — | yes |

### `ops.sourced_work_request_outcome_feedback_acceptance_receipt`

**Definition:** `migrations/0179_program6_sourced_outcome_feedback.sql:33`. **Product path:** positive static evidence (column detail below).

**Table writers:** E2, E107. **Table readers:** E2, E50, E107, E126, E300, E713, E1412.
**Unresolved SQL affecting any column:** E2, E49, E107. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E50, E126, E300, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E2, E107, E300, E713 | E50 | E2, E107 | — | yes |
| `work_request_id` | E2, E107 | E2, E107, E126, E1412 | — | — | — | yes |
| `feedback_id` | E2, E107 | E2, E50, E107, E300, E713 | — | — | — | yes |
| `idempotency_key` | E2, E107 | E2, E107, E1412 | — | — | — | yes |
| `base_version`, `feedback_hash`, `result_version` | E2, E107 | E2, E107 | — | — | — | yes |
| `accepted_by_actor_id` | E2, E107 | E2, E107, E713, E1412 | — | — | — | yes |
| `accepted_at` | — | E2, E107, E713, E1412 | — | E2, E107 | — | yes |
| `acceptance_basis` | E107 | E2, E107 | — | E2 | — | yes |
| `bound_receipt_refs` | E107 | E2, E107 | — | — | — | yes |

### `ops.sourced_work_request_plan`

**Definition:** `migrations/0177_program6_sourced_ready_plan.sql:10`. **Product path:** positive static evidence (column detail below).

**Table writers:** E329, E332. **Table readers:** E1, E2, E3, E7, E10, E78, E82, E90, E103, E107, E111, E113, E126, E136, E139, E143, E148, E207, E216, E279, E300, E304, E305, E307, E309, E329, E331, E332, E340, E374, E628, E647, E649, E713, E734, E739, E1664.
**Unresolved SQL affecting any column:** E48, E86, E139, E332, E370, E415, E648. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E1, E2, E3, E7, E10, E78, E82, E90, E103, E107, E111, E113, E126, E136, E143, E148, E207, E216, E279, E300, E304, E305, E307, E309, E329, E331, E340, E374, E628, E647, E649, E713, E734, E739.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1, E2, E3, E7, E10, E78, E82, E90, E103, E107, E111, E113, E126, E136, E139, E143, E148, E207, E216, E279, E300, E304, E305, E307, E309, E329, E331, E332, E374, E628, E647, E649, E713, E734, E739, E1664 | E340, E628 | E329, E332 | — | yes |
| `work_request_id` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E111, E113, E126, E139, E143, E148, E207, E216, E279, E300, E329, E331, E332, E374, E628, E649, E713, E734, E739 | — | — | — | yes |
| `plan_version` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E126, E139, E143, E148, E216, E279, E329, E331, E332, E628, E713 | — | — | — | yes |
| `idempotency_key` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E139, E148, E216, E279, E329, E331, E332, E628, E713 | — | — | — | yes |
| `work_request_version` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E139, E143, E148, E216, E279, E329, E331, E332, E628, E649, E713, E734, E739 | E628 | — | — | yes |
| `preimage` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E139, E143, E148, E216, E279, E329, E331, E332, E628, E713 | — | — | — | yes |
| `scope_summary`, `dependency_refs`, `observability_ref`, `caps` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E139, E143, E148, E216, E279, E329, E331, E332, E628, E649, E713 | — | — | — | yes |
| `runbook_ref`, `runbook_section_id`, `runbook_revision_id`, `runbook_content_hash` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E126, E139, E148, E216, E279, E329, E331, E332, E628, E649, E713 | — | — | — | yes |
| `recovery_ref` | E329, E332 | E1, E2, E3, E10, E90, E103, E107, E139, E143, E148, E216, E279, E329, E331, E332, E340, E628, E649, E713 | — | — | — | yes |
| `plan_hash` | E329, E332 | E1, E2, E3, E10, E78, E82, E90, E103, E107, E111, E113, E136, E139, E143, E148, E207, E216, E279, E300, E309, E329, E331, E332, E374, E628, E647, E649, E713, E1664 | — | — | — | yes |
| `plan_ref` | E329, E332 | E1, E2, E3, E7, E10, E78, E82, E90, E103, E107, E136, E139, E143, E148, E216, E279, E300, E304, E305, E307, E309, E329, E331, E332, E628, E647, E649, E713 | — | — | — | yes |
| `created_at` | — | E1, E2, E3, E10, E90, E103, E107, E139, E148, E216, E279, E329, E331, E332, E628, E713 | — | E329, E332 | — | yes |

### `ops.sourced_work_request_plan_acceptance_receipt`

**Definition:** `migrations/0177_program6_sourced_ready_plan.sql:33`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1, E3. **Table readers:** E1, E2, E3, E10, E80, E82, E107, E111, E113, E139, E143, E207, E278, E300, E309, E329, E331, E374, E647, E649, E713, E1412.
**Unresolved SQL affecting any column:** E1, E86, E139, E216, E279, E366, E415, E648. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E2, E3, E10, E80, E82, E107, E111, E113, E143, E207, E278, E300, E309, E329, E331, E374, E647, E649, E713.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1, E2, E3, E80, E82, E107, E278, E300, E331, E649, E713, E1622 | — | E1, E3 | — | yes |
| `work_request_id` | E1, E3 | E1, E2, E3, E82, E107, E111, E113, E139, E143, E207, E278, E300, E329, E331, E374, E647, E649, E1622 | — | — | — | yes |
| `plan_id` | E1, E3 | E1, E2, E3, E10, E82, E107, E111, E113, E139, E143, E207, E278, E300, E309, E329, E331, E374, E647, E649, E713, E1622 | — | — | — | yes |
| `idempotency_key`, `shape_rationale` | E1, E3 | E1, E2, E3, E107, E278, E331 | — | — | — | yes |
| `base_version`, `shape_fixed_surface_ref` | E1, E3 | E1, E2, E3, E107, E278, E331, E649 | — | — | — | yes |
| `plan_hash` | E1, E3 | E1, E2, E3, E10, E82, E107, E111, E113, E143, E207, E278, E300, E309, E331, E374, E649, E1622 | — | — | — | yes |
| `accepted_by_actor_id` | E1, E3 | E1, E2, E3, E107, E143, E278, E331, E649, E713, E1622 | — | — | — | yes |
| `accepted_at` | — | E1, E2, E3, E107, E143, E278, E331, E649, E713, E1622 | — | E1, E3 | — | yes |
| `result_version` | E1, E3 | E1, E2, E3, E10, E80, E82, E107, E111, E113, E139, E143, E207, E278, E300, E329, E331, E374, E647, E649 | — | — | — | yes |

### `ops.sourced_work_request_plan_shape_binding_receipt`

**Definition:** `migrations/0306_sourced_work_shape_disposition.sql:30`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1, E3. **Table readers:** E1, E3.
**Unresolved SQL affecting any column:** E2, E3, E107, E331, E648. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E1.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E1, E3 | — | yes |
| `plan_acceptance_receipt_id`, `work_request_id`, `disposition`, `fixed_surface_ref`, `rationale`, `decided_by_actor_id`, `decided_at` | E1, E3 | — | — | — | — | yes |

### `ops.sourced_work_request_shape_disposition_correction_receipt`

**Definition:** `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:57`. **Product path:** positive static evidence (column detail below).

**Table writers:** E615. **Table readers:** E347, E615, E651.
**Unresolved SQL affecting any column:** E615, E651. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E347.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `decided_at` | — | E347, E615, E651 | — | E615 | — | yes |
| `work_request_id`, `idempotency_key`, `original_receipt_id`, `base_version`, `result_version`, `disposition`, `fixed_surface_ref`, `rationale`, `decided_by_actor_id` | E615 | E347, E615, E651 | — | — | — | yes |

### `ops.sourced_work_request_shape_disposition_receipt`

**Definition:** `migrations/0306_sourced_work_shape_disposition.sql:8`. **Product path:** positive static evidence (column detail below).

**Table writers:** E615. **Table readers:** E347, E615, E651.
**Unresolved SQL affecting any column:** E652. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E347, E615, E651.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `decided_at` | — | E347, E615, E651 | — | E615 | — | yes |
| `work_request_id`, `idempotency_key`, `base_version`, `result_version`, `disposition`, `fixed_surface_ref`, `rationale`, `decided_by_actor_id` | E615 | E347, E615, E651 | — | — | — | yes |

### `ops.staging_deployment_attempt`

**Definition:** `migrations/0202_staging_release_readback_receipt.sql:50`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E318. **Table readers:** E98, E318, E393, E394.
**Additional lexical/dependency evidence:** E98, E318, E393, E394.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `prepared_at` | — | E98, E318, E393, E394 | — | E318 | — | unknown |
| `idempotency_key`, `recovery_attempt_id`, `recovery_step`, `correlation_id`, `rehearsal_release_id`, `observed_release_id`, `prior_release_id`, `service_id`, `environment`, `git_sha`, `provider`, `expected_provider_tag`, `declared_migration_set_sha256`, `declared_migration_count`, `declared_schema_highest_migration`, `declared_schema_applied_count`, `declared_schema_ledger_sha256`, `writer_session_user` | E318 | E98, E318, E393, E394 | — | — | — | unknown |

### `ops.staging_deployment_claim`

**Definition:** `migrations/0202_staging_release_readback_receipt.sql:86`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E98. **Table readers:** E98.
**Unresolved SQL affecting any column:** E318, E393, E394. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E98.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `deployment_attempt_id`, `writer_session_user` | E98 | — | — | — | — | unknown |
| `claimed_at` | — | — | — | E98 | — | unknown |

### `ops.staging_forward_fix_rehearsal_attempt`

**Definition:** `migrations/0315_program5_forward_fix_rehearsal.sql:28`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E319. **Table readers:** E99, E319, E348, E392.
**Additional lexical/dependency evidence:** E99, E319, E348, E392.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E99, E319, E392 | — | E319 | — | unknown |
| `idempotency_key`, `expected_provider_tag`, `declared_migration_set_sha256`, `declared_migration_count`, `declared_schema_highest_migration`, `declared_schema_applied_count`, `declared_schema_ledger_sha256` | E319 | E99, E319, E348, E392 | — | — | — | unknown |
| `correlation_id`, `release_id`, `service_id`, `environment`, `git_sha`, `provider`, `candidate_provider_version_id`, `recovery_strategy`, `recovery_plan_ref`, `plan_hash` | E319 | E99, E319, E392 | — | — | — | unknown |
| `writer_session_user` | E319 | E99, E319, E392 | — | declared; caller unknown | — | unknown |

### `ops.staging_forward_fix_rehearsal_claim`

**Definition:** `migrations/0315_program5_forward_fix_rehearsal.sql:56`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E99. **Table readers:** E99.
**Unresolved SQL affecting any column:** E319, E392. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E99.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `rehearsal_attempt_id` | E99 | — | — | — | — | unknown |
| `writer_session_user` | E99 | — | — | declared; caller unknown | — | unknown |
| `claimed_at` | — | — | — | E99 | — | unknown |

### `ops.staging_forward_fix_rehearsal_result`

**Definition:** `migrations/0315_program5_forward_fix_rehearsal.sql:64`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E392. **Table readers:** E99, E319, E323, E392.
**Unresolved SQL affecting any column:** E708. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E99, E319, E323, E392.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E99, E319, E323, E392 | — | E392 | — | unknown |
| `idempotency_key`, `rehearsal_attempt_id`, `provider_version_id`, `provider_tag`, `verb_count`, `schema_highest_migration`, `schema_applied_count`, `schema_ledger_sha256`, `doctrine_generation`, `program6_actions_enabled`, `projection_sha256`, `evidence_ref` | E392 | E99, E319, E392 | — | — | — | unknown |
| `observed_at`, `writer_session_user` | E392 | E99, E319, E392 | — | declared; caller unknown | — | unknown |

### `ops.staging_recovery_rehearsal_bundle`

**Definition:** `migrations/0202_staging_release_readback_receipt.sql:141`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E392, E394. **Table readers:** E323, E392, E394, E708.
**Unresolved SQL affecting any column:** E392, E393, E394. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E323, E708.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E323, E392, E394, E708 | — | E392, E394 | — | unknown |
| `recovery_attempt_id`, `correlation_id`, `prior_release_id`, `environment`, `current_before_receipt_id`, `prior_after_rollback_receipt_id`, `current_after_restore_receipt_id`, `bundle_sha256`, `writer_session_user` | E392, E394 | E708 | — | — | — | unknown |
| `current_release_id`, `service_id`, `recovery_strategy`, `recovery_plan_ref`, `plan_hash`, `declared_migration_set_sha256`, `declared_migration_count`, `declared_schema_highest_migration`, `declared_schema_applied_count`, `declared_schema_ledger_sha256`, `evidence_ref`, `completed_at` | E392, E394 | E323, E708 | — | — | — | unknown |
| `recorded_at` | — | E708 | — | E392, E394 | — | unknown |
| `forward_fix_result_id`, `candidate_git_sha`, `candidate_provider_version_id` | E392 | E323, E708 | — | — | — | unknown |

### `ops.staging_release_approval_receipt`

**Definition:** `migrations/0219_staging_release_approval_receipt.sql:7`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E36. **Table readers:** E36, E654.
**Additional lexical/dependency evidence:** E36, E654.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E36, E654 | — | E36 | — | unknown |
| `idempotency_key`, `release_id`, `plan_hash`, `approved_by_actor`, `approved_at`, `approval_expires_at`, `verifier_actor`, `verifier_evidence_ref`, `approval_sha256`, `evidence_ref` | E36 | E36, E654 | — | — | — | unknown |

### `ops.staging_release_readback_receipt`

**Definition:** `migrations/0202_staging_release_readback_receipt.sql:93`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E393, E394. **Table readers:** E318, E393, E394.
**Unresolved SQL affecting any column:** E98, E333, E393, E708. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E318, E394.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `recorded_at` | — | E318, E393, E394 | — | E393, E394 | — | unknown |
| `idempotency_key`, `deployment_attempt_id`, `recovery_attempt_id`, `recovery_step`, `correlation_id`, `deployment_id`, `rehearsal_release_id`, `observed_release_id`, `prior_release_id`, `service_id`, `environment`, `git_sha`, `provider`, `provider_version_id`, `provider_tag`, `verb_count`, `schema_highest_migration`, `schema_applied_count`, `declared_migration_set_sha256`, `declared_migration_count`, `declared_schema_applied_count`, `declared_schema_ledger_sha256`, `doctrine_generation`, `projection_sha256`, `evidence_ref`, `observed_at`, `writer_session_user`, `program6_actions_enabled` | E393, E394 | E318, E393, E394 | — | — | — | unknown |

### `ops.staging_replacement_project_contract`

**Definition:** `migrations/0322_clean_staging_replacement_contract.sql:16`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E320. **Table readers:** E320, E349, E395.
**Additional lexical/dependency evidence:** E320, E349, E395.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E320, E349, E395 | — | E320 | — | unknown |
| `idempotency_key`, `schema_version`, `tree_mode`, `git_sha`, `source_tree_oid`, `source_tree_sha256`, `source_tree_entry_count`, `artifact_sha256`, `config_sha256`, `dependency_sha256`, `migration_ledger`, `migration_count`, `migration_highest`, `migration_ledger_sha256`, `prior_staging_project_id`, `replacement_project_id`, `replacement_branch_id`, `replacement_endpoint_id`, `expected_synthetic_data_count`, `expected_production_overlap_count` | E320 | E320, E395 | — | — | — | unknown |
| `writer_session_user` | E320 | E320, E395 | — | declared; caller unknown | — | unknown |
| `created_at` | — | E320, E395 | — | E320 | — | unknown |

### `ops.staging_replacement_project_receipt`

**Definition:** `migrations/0322_clean_staging_replacement_contract.sql:51`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E395. **Table readers:** E320, E349, E395.
**Additional lexical/dependency evidence:** E320, E349, E395.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E320, E349, E395 | — | E395 | — | unknown |
| `idempotency_key`, `schema_version` | E395 | E320, E395 | — | — | — | unknown |
| `contract_id`, `git_sha`, `source_tree_oid`, `source_tree_sha256`, `source_tree_entry_count`, `artifact_sha256`, `config_sha256`, `dependency_sha256`, `prior_staging_project_id`, `replacement_project_id`, `replacement_branch_id`, `replacement_endpoint_id`, `live_migration_ledger`, `live_migration_count`, `live_migration_highest`, `live_migration_ledger_sha256`, `synthetic_data_count`, `production_overlap_count`, `receipt_sha256`, `evidence_ref` | E395 | E320, E349, E395 | — | — | — | unknown |
| `writer_session_user` | E395 | E320, E395 | — | declared; caller unknown | — | unknown |
| `observed_at` | E395 | E320, E349, E395 | — | declared; caller unknown | — | unknown |

### `ops.staging_restore_only_attempt`

**Definition:** `migrations/0295_staging_restore_only_recovery.sql:12`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E321. **Table readers:** E100, E321, E396.
**Additional lexical/dependency evidence:** E100, E321, E396.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E100, E321, E396 | — | E321 | — | unknown |
| `idempotency_key`, `recovery_attempt_id`, `correlation_id`, `rehearsal_release_id`, `prior_release_id`, `service_id`, `environment`, `git_sha`, `provider`, `target_provider_version_id`, `recovery_strategy`, `rollback_plan_ref`, `plan_hash`, `expected_provider_tag`, `declared_migration_set_sha256`, `declared_migration_count`, `declared_schema_highest_migration`, `declared_schema_applied_count`, `declared_schema_ledger_sha256` | E321 | E100, E321, E396 | — | — | — | unknown |
| `writer_session_user` | E321 | E100, E321, E396 | — | declared; caller unknown | — | unknown |

### `ops.staging_restore_only_claim`

**Definition:** `migrations/0295_staging_restore_only_recovery.sql:45`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E100. **Table readers:** E100.
**Unresolved SQL affecting any column:** E321, E396. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E100.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `restore_attempt_id` | E100 | — | — | — | — | unknown |
| `writer_session_user` | E100 | — | — | declared; caller unknown | — | unknown |
| `claimed_at` | — | — | — | E100 | — | unknown |

### `ops.staging_restore_only_result`

**Definition:** `migrations/0295_staging_restore_only_recovery.sql:53`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E396. **Table readers:** E100, E321, E396, E640, E641.
**Additional lexical/dependency evidence:** E100, E321, E396, E640, E641.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E100, E321, E396, E640, E641 | — | E396 | — | unknown |
| `idempotency_key`, `restore_attempt_id`, `provider_version_id`, `provider_tag`, `verb_count`, `schema_highest_migration`, `schema_applied_count`, `doctrine_generation`, `program6_actions_enabled`, `reason`, `result_sha256`, `evidence_ref` | E396 | E100, E321, E396 | — | — | — | unknown |
| `status` | E396 | E100, E321, E396, E640, E641 | — | — | — | unknown |
| `observed_at` | — | E100, E321, E396, E640 | — | E396 | — | unknown |
| `writer_session_user` | E396 | E100, E321, E396 | — | declared; caller unknown | — | unknown |

### `ops.studio_leader`

**Definition:** `migrations/0900_studio_failover_leader.sql:3`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E686. **Table readers:** E686, E751.
**Additional lexical/dependency evidence:** E686.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `singleton` | — | E686, E751 | — | declared; caller unknown | — | unknown |
| `host` | E686 | E686, E751 | — | — | — | unknown |
| `epoch` | E686 | E686, E751 | — | declared; caller unknown | — | unknown |
| `fence_evidence`, `changed_at` | E686 | — | — | declared; caller unknown | — | unknown |

### `ops.tour`

**Definition:** `migrations/0318_tour_operations_foundation.sql:79`. **Product path:** positive static evidence (column detail below).

**Table writers:** E4, E124. **Table readers:** E4, E29, E30, E124, E268, E322, E351, E361, E397, E665, E666, E671, E676.
**Additional lexical/dependency evidence:** E4, E29, E30, E124, E268, E322, E351, E361, E397, E665, E666, E671, E676.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E4, E29, E30, E124, E268, E322, E351, E361, E397, E665, E666, E671, E676 | — | E124 | — | yes |
| `organization_tenant_id` | E124 | E4, E29, E30, E268, E322, E351, E361, E397, E665, E666, E671, E676 | — | — | — | yes |
| `tour_name`, `tour_status` | E124 | E268, E322, E351, E397 | — | — | — | yes |
| `route_version` | E4, E124 | E29, E268, E322, E351, E361, E397, E676 | — | — | — | yes |
| `canonical_dataset_version` | E124 | E322, E397, E671 | — | — | — | yes |
| `created_at` | — | E322, E397 | — | E124 | — | yes |
| `updated_at` | E4 | E268, E322, E351, E397 | — | E124 | — | yes |
| `subject_type`, `subject_id` | E124 | E322, E351, E397 | — | — | — | yes |
| `subject_bound_at` | E124 | E322, E397 | — | — | — | yes |

### `ops.tour_audit_event`

**Definition:** `migrations/0318_tour_operations_foundation.sql:160`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `event_type`, `entity_type`, `entity_id`, `actor_id`, `occurred_at`, `event_digest`, `payload` | — | — | — | — | — | unknown |

### `ops.tour_cheat_sheet_revision`

**Definition:** `migrations/0318_tour_operations_foundation.sql:121`. **Product path:** positive static evidence (column detail below).

**Table writers:** E21, E428. **Table readers:** E21, E351, E428.
**Additional lexical/dependency evidence:** E21, E351, E428.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E21, E351, E428 | — | E21, E428 | — | yes |
| `organization_tenant_id`, `tour_id`, `revision_number`, `content` | E21, E428 | E21, E351, E428 | — | — | — | yes |
| `editor_actor_id`, `status`, `parent_revision_id`, `content_digest` | E21, E428 | E21, E428 | — | — | — | yes |
| `revision_kind` | E21, E428 | E21, E351, E428 | — | declared; caller unknown | — | yes |
| `restored_from_revision_id` | E428 | E21, E428 | — | — | — | yes |

### `ops.tour_conflict_resolution_receipt`

**Definition:** `migrations/0318_tour_operations_foundation.sql:69`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E359.
**Unresolved SQL affecting any column:** E668. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E359.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `conflict_id`, `selected_field_assertion_id` | — | E359, E1629 | — | — | — | yes |
| `rationale`, `evidence`, `resolver_actor_id`, `receipt_digest` | — | — | — | — | — | unknown |
| `resolved_at` | — | E359 | — | — | — | yes |

### `ops.tour_coordinate_entrance_verification_receipt`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:228`. **Product path:** positive static evidence (column detail below).

**Table writers:** E23. **Table readers:** E23, E362, E609, E610, E670.
**Additional lexical/dependency evidence:** E23, E362, E609, E610, E670.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E23, E362, E609, E610, E670 | E610 | E23 | — | yes |
| `organization_tenant_id`, `property_id`, `coordinate_candidate_id` | E23 | E362, E609, E610, E670, E1630 | — | — | — | yes |
| `verifier_actor_id`, `evidence_reference`, `native_navigation_proof`, `receipt_digest` | E23 | E610 | — | — | — | yes |
| `verified_at` | E23 | E609, E610 | — | — | — | yes |
| `created_at` | — | E610 | E610 | E23 | — | yes |

### `ops.tour_fact_conflict`

**Definition:** `migrations/0318_tour_operations_foundation.sql:56`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E359.
**Unresolved SQL affecting any column:** E663, E664, E668. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E359.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `opened_at` | — | E359 | — | declared; caller unknown | — | yes |
| `organization_tenant_id`, `property_id`, `field_key`, `state` | — | E359 | — | — | — | yes |

### `ops.tour_fact_conflict_participant`

**Definition:** `migrations/0318_tour_operations_foundation.sql:62`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Unresolved SQL affecting any column:** E664. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `conflict_id`, `organization_tenant_id`, `field_assertion_id` | — | E1628 | — | — | — | unknown |
| `participant_role` | — | — | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.tour_field_assertion`

**Definition:** `migrations/0318_tour_operations_foundation.sql:40`. **Product path:** positive static evidence (column detail below).

**Table writers:** E24. **Table readers:** E24, E351, E352, E357, E359, E363, E610, E667, E668, E672.
**Unresolved SQL affecting any column:** E663, E664, E668. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E24, E351, E352, E357, E359, E363, E610, E667, E672.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E24, E351, E352, E357, E359, E363, E610, E667, E668, E672 | E610 | E24 | — | yes |
| `organization_tenant_id` | E24 | E351, E352, E357, E359, E363, E610, E667, E668, E672, E1627 | — | — | — | yes |
| `property_id` | E24 | E351, E357, E359, E610 | E357 | — | — | yes |
| `field_key` | E24 | E351, E357, E359, E610, E672, E1627 | — | — | — | yes |
| `value` | E24 | E351, E352, E357, E359, E363, E610, E667, E672 | — | — | — | yes |
| `source_evidence_id` | E24 | E357, E359, E610, E668, E1627 | — | — | — | yes |
| `rights_receipt_id`, `observed_at` | E24 | E357, E359, E610, E1627 | — | — | — | yes |
| `effective_from`, `review_state` | E24 | E351, E357, E359, E610 | — | — | — | yes |
| `effective_to`, `confidence`, `data_classification` | E24 | E357, E359, E610 | — | — | — | yes |
| `created_at` | — | E357, E610 | E610 | E24 | — | yes |

### `ops.tour_jurisdiction_dataset`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:162`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E358, E610, E683.
**Additional lexical/dependency evidence:** E358, E610, E683.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E358, E610, E683 | E610 | declared; caller unknown | — | yes |
| `organization_tenant_id` | — | E358, E610, E683, E1631 | — | — | — | yes |
| `jurisdiction_type`, `authoritative_source_locator`, `dataset_version`, `dataset_digest` | — | E610, E683 | — | — | — | yes |
| `state_code`, `county_name`, `source_crs`, `review_state` | — | E358, E610, E683 | — | — | — | yes |
| `source_evidence_id`, `rights_receipt_id` | — | E610, E683, E1631 | — | — | — | yes |
| `as_of` | — | E358, E610, E683, E1631 | E358 | — | — | yes |
| `created_at` | — | E610, E683 | E610 | declared; caller unknown | — | yes |

### `ops.tour_map_promotion_receipt`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:70`. **Product path:** positive static evidence (column detail below).

**Table writers:** E397. **Table readers:** E397, E671.
**Additional lexical/dependency evidence:** E397, E671.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E397 | — | E397 | — | yes |
| `organization_tenant_id`, `projection_id`, `decision`, `canonical_dataset_version`, `route_version` | E397 | E671 | — | — | — | yes |
| `receipt_sequence` | — | E671 | — | — | — | yes |
| `reviewer_actor_id`, `reviewed_at`, `decision_reason`, `brief_version`, `selected_prototype_id`, `component_registry_version`, `provider_rights_receipt_ids`, `mobile_test_evidence`, `native_navigation_test_evidence`, `offline_test_evidence`, `required_checks`, `receipt_digest` | E397 | — | — | — | — | yes |
| `created_at` | — | — | — | E397 | — | yes |

### `ops.tour_pdf_human_review`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:120`. **Product path:** positive static evidence (column detail below).

**Table writers:** E398. **Table readers:** E351, E353, E355, E398.
**Unresolved SQL affecting any column:** E399. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E351, E353, E355, E398.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E355, E398 | E355 | E398 | — | yes |
| `organization_tenant_id`, `render_job_id` | E398 | E351, E353, E355 | E355 | — | — | yes |
| `render_result_id` | E398 | E353, E355 | — | — | — | yes |
| `qc_run_digest`, `reviewed_at`, `review_receipt_digest`, `reason`, `reviewer_actor_id` | E398 | E355 | — | — | — | yes |
| `decision` | E398 | E351, E353, E355 | — | — | — | yes |
| `created_at` | — | E355 | — | E398 | — | yes |

### `ops.tour_pdf_render_job`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:91`. **Product path:** positive static evidence (column detail below).

**Table writers:** E419. **Table readers:** E351, E353, E354, E355, E399, E419.
**Additional lexical/dependency evidence:** E351, E353, E354, E355, E399, E419.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E351, E353, E354, E355, E399, E419 | E355 | E419 | — | yes |
| `organization_tenant_id` | E419 | E351, E353, E354, E355, E399 | E355 | — | — | yes |
| `projection_id` | E419 | E351, E355, E399 | — | — | — | yes |
| `requested_by_actor_id`, `request`, `projection_digest`, `packet_digest`, `template_digest`, `renderer_digest`, `qc_ruleset_digest`, `expected_property_count` | E419 | E355, E399 | — | — | — | yes |
| `created_at` | — | E351, E355, E399 | — | E419 | — | yes |

### `ops.tour_pdf_render_result`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:105`. **Product path:** positive static evidence (column detail below).

**Table writers:** E399. **Table readers:** E351, E353, E354, E355, E398, E399.
**Additional lexical/dependency evidence:** E351, E353, E354, E355, E398, E399.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E351, E353, E354, E355, E398, E399 | E355 | E399 | — | yes |
| `organization_tenant_id`, `render_job_id` | E399 | E351, E353, E354, E355, E398, E399 | E355 | — | — | yes |
| `status` | E399 | E351, E354, E355, E398 | — | — | — | yes |
| `artifact_ref`, `page_count`, `qc_run_digest`, `completed_at` | E399 | E351, E355, E398 | — | — | — | yes |
| `artifact_digest`, `storage_ref`, `content_length` | E399 | E351, E353, E354, E355, E398 | — | — | — | yes |
| `blocking_finding_count` | E399 | E351, E355, E398 | — | declared; caller unknown | — | yes |
| `attempt_count` | E399 | E351, E353, E354, E355, E398, E399 | — | — | — | yes |
| `created_at` | — | E351, E355, E398 | — | E399 | — | yes |

### `ops.tour_property`

**Definition:** `migrations/0318_tour_operations_foundation.sql:23`. **Product path:** positive static evidence (column detail below).

**Table writers:** E408. **Table readers:** E358, E408, E610.
**Unresolved SQL affecting any column:** E30. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E358, E408, E610.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E358, E408, E610 | E610 | E408 | — | yes |
| `organization_tenant_id`, `property_status` | E408 | E358, E408, E610 | — | — | — | yes |
| `created_at` | — | E610 | E610 | E408 | — | yes |
| `retired_at` | — | E610 | — | — | — | yes |

### `ops.tour_property_address_assertion`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:75`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E358.
**Additional lexical/dependency evidence:** E358.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `source_evidence_id`, `rights_receipt_id`, `observed_at` | — | E1637 | — | — | — | unknown |
| `property_id`, `address_value`, `address_role`, `effective_to`, `confidence`, `review_state` | — | — | — | — | — | unknown |
| `effective_from` | — | E358 | — | — | — | yes |

### `ops.tour_property_building_assertion`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:117`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E358.
**Additional lexical/dependency evidence:** E358.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `source_evidence_id`, `rights_receipt_id`, `observed_at` | — | E358, E1638 | — | — | — | yes |
| `property_id`, `building_identifier`, `building_name`, `review_state` | — | E358 | — | — | — | yes |
| `confidence` | — | — | — | — | — | unknown |

### `ops.tour_property_coordinate_candidate`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:204`. **Product path:** positive static evidence (column detail below).

**Table writers:** E22. **Table readers:** E22, E362, E609, E670.
**Unresolved SQL affecting any column:** E397, E681. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E22, E362, E609, E670.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E22, E362, E609, E670 | — | E22 | — | yes |
| `organization_tenant_id`, `coordinate_role` | E22 | E362, E609, E670, E1639 | — | — | — | yes |
| `property_id` | E22 | E609, E670 | — | — | — | yes |
| `latitude`, `longitude` | E22 | E362 | — | — | — | yes |
| `precision_class`, `access_notes` | E22 | — | — | — | — | yes |
| `source_evidence_id`, `rights_receipt_id` | E22 | E609, E670, E1639 | — | — | — | yes |
| `provider` | E22 | E1639 | — | — | — | yes |
| `observed_at` | E22 | E609, E1639 | — | — | — | yes |
| `review_state` | E22 | E362, E609, E670 | — | — | — | yes |
| `created_at` | — | — | — | E22 | — | yes |

### `ops.tour_property_identifier_alias`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:32`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `property_id`, `identifier_assertion_id`, `source_evidence_id`, `rights_receipt_id`, `observed_at` | — | E1640 | — | — | — | unknown |
| `alias_kind`, `alias_value`, `normalized_alias`, `alias_digest` | — | — | — | — | — | unknown |

### `ops.tour_property_identifier_assertion`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:9`. **Product path:** positive static evidence (column detail below).

**Table writers:** E25. **Table readers:** E25, E408.
**Unresolved SQL affecting any column:** E680, E682. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E25, E408.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E25, E408 | — | E25 | — | yes |
| `organization_tenant_id`, `property_id`, `identifier_scheme`, `normalized_identifier` | E25 | E408, E1641 | — | — | — | yes |
| `identifier_value`, `confidence`, `assertion_digest` | E25 | — | — | — | — | yes |
| `source_evidence_id`, `rights_receipt_id`, `observed_at` | E25 | E1641 | — | — | — | yes |
| `review_state` | E25 | E408, E1641 | — | — | E1641 | yes |
| `created_at` | — | E408 | — | E25 | — | yes |

### `ops.tour_property_identity_lineage`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:53`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** —.
**Unresolved SQL affecting any column:** E28. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `successor_property_id`, `source_evidence_id`, `rights_receipt_id`, `effective_at` | — | E1642 | — | — | — | unknown |
| `predecessor_property_id` | — | E1642 | — | — | E1642 | unknown |
| `relationship`, `recorded_by_actor_id`, `rationale`, `lineage_digest` | — | — | — | — | — | unknown |

### `ops.tour_property_jurisdiction_assertion`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:183`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E358, E610.
**Additional lexical/dependency evidence:** E358, E610.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E610 | E610 | declared; caller unknown | — | yes |
| `organization_tenant_id`, `jurisdiction_dataset_id`, `jurisdiction_name`, `source_evidence_id`, `rights_receipt_id` | — | E610, E1643 | — | — | — | yes |
| `property_id`, `assertion_method`, `review_state` | — | E610 | — | — | — | yes |
| `as_of` | — | E610, E1643 | E358 | — | — | yes |
| `determination_status` | — | E610, E1643 | — | declared; caller unknown | — | yes |

### `ops.tour_property_membership`

**Definition:** `migrations/0318_tour_operations_foundation.sql:85`. **Product path:** positive static evidence (column detail below).

**Table writers:** E4. **Table readers:** E4, E268, E350, E352, E357, E361, E362, E363, E609, E670, E672, E676.
**Unresolved SQL affecting any column:** E419, E609, E666, E667, E668, E669. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E4, E268, E350, E352, E357, E361, E362, E363, E670, E672, E676.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `selected_at` | — | — | — | E4 | — | yes |
| `organization_tenant_id`, `tour_id`, `route_version` | E4 | E268, E350, E352, E357, E361, E362, E363, E609, E670, E672, E676, E1632 | — | — | — | yes |
| `property_id` | E4 | E350, E352, E357, E361, E362, E363, E609, E676 | E357 | — | — | yes |
| `route_sequence` | E4 | E350, E352, E357, E362, E363 | — | — | — | yes |
| `route_label` | E4 | E350, E352, E357, E362, E363, E672, E1644 | — | — | — | yes |
| `assertion_set_digest` | E4 | — | — | — | — | yes |

### `ops.tour_property_parcel_assertion`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:96`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E358.
**Additional lexical/dependency evidence:** E358.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `source_evidence_id`, `rights_receipt_id` | — | E1645 | — | — | — | unknown |
| `property_id`, `parcel_identifier`, `parcel_source_locator`, `source_crs`, `geometry_digest`, `geometry_method`, `review_state` | — | — | — | — | — | unknown |
| `as_of` | — | E1645 | E358 | — | — | unknown |

### `ops.tour_property_provider_projection`

**Definition:** `migrations/0428_tour_property_identity_jurisdiction.sql:138`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `rights_receipt_id`, `source_evidence_id`, `observed_at` | — | E1646 | — | — | — | unknown |
| `property_id`, `provider`, `projection_kind`, `provider_record_id`, `payload_digest`, `retention_expires_at`, `review_state` | — | — | — | — | — | unknown |

### `ops.tour_public_asset`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:37`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E427.
**Additional lexical/dependency evidence:** E427.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `projection_id`, `asset_ref`, `media_type`, `content_length`, `alt`, `caption` | — | E427 | — | — | — | unknown |
| `property_id`, `storage_ref`, `artifact_digest` | — | — | — | — | — | unknown |

### `ops.tour_public_projection`

**Definition:** `migrations/0318_tour_operations_foundation.sql:95`. **Product path:** positive static evidence (column detail below).

**Table writers:** E125, E669. **Table readers:** E125, E217, E268, E350, E351, E352, E356, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E670, E671, E672, E676.
**Unresolved SQL affecting any column:** E665, E667, E673. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E125, E217, E268, E350, E351, E352, E356, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E670, E671, E672, E676.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E125, E217, E268, E350, E351, E352, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E670, E671, E672, E676, E1647 | E610 | E125 | — | yes |
| `organization_tenant_id` | E125 | E217, E268, E350, E351, E352, E356, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E670, E671, E672, E676, E1633, E1647 | — | — | — | yes |
| `tour_id` | E125 | E217, E268, E350, E351, E352, E356, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E670, E671, E672, E676, E1633 | — | — | — | yes |
| `projection_version` | E125 | E217, E350, E351, E352, E356, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E676 | — | — | — | yes |
| `route_version` | E125 | E217, E350, E351, E352, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E670, E671, E672, E676, E1633 | — | — | — | yes |
| `as_of` | E125 | E217, E350, E351, E352, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E1633 | — | — | — | yes |
| `facts_only` | E125 | E217, E350, E352, E357, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669 | — | — | — | yes |
| `projection_digest` | E125, E669 | E217, E350, E351, E352, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E676, E1647 | — | — | — | yes |
| `derived_render_digest` | — | E217, E350, E352, E357, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669 | — | — | — | yes |
| `status` | E125, E669 | E217, E350, E351, E352, E357, E359, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669, E670, E676, E1633, E1647 | — | — | — | yes |
| `created_at` | — | E217, E350, E352, E357, E361, E362, E363, E397, E419, E436, E609, E610, E662, E668, E669 | E610 | E125 | — | yes |

### `ops.tour_public_projection_fact`

**Definition:** `migrations/0318_tour_operations_foundation.sql:103`. **Product path:** positive static evidence (column detail below).

**Table writers:** E609. **Table readers:** E352, E359, E363, E609, E610, E662, E672.
**Unresolved SQL affecting any column:** E609, E669. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E352, E359, E363, E610, E662, E672.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E352, E363, E610 | E610 | E609 | — | yes |
| `organization_tenant_id`, `projection_id`, `field_assertion_id`, `display_field_key` | E609 | E352, E359, E363, E610, E662, E672, E1634, E1635 | — | — | — | yes |
| `property_id` | E609 | E352, E359, E363, E610, E662, E1634, E1635 | — | — | — | yes |
| `route_version` | E609 | E610, E662, E1635 | — | — | E1635 | yes |
| `created_at` | — | E610 | E610 | E609 | — | yes |

### `ops.tour_public_projection_map_point`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:53`. **Product path:** positive static evidence (column detail below).

**Table writers:** E609. **Table readers:** E362, E609, E662, E670.
**Unresolved SQL affecting any column:** E397. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E362, E609, E662, E670.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E609 | — | yes |
| `organization_tenant_id`, `projection_id`, `property_id`, `coordinate_candidate_id`, `entrance_verification_receipt_id` | E609 | E362, E662, E670 | — | — | — | yes |
| `route_version` | E609 | E362, E662 | — | — | — | yes |

### `ops.tour_public_projection_seal_receipt`

**Definition:** `migrations/0318_tour_operations_foundation.sql:113`. **Product path:** positive static evidence (column detail below).

**Table writers:** E609. **Table readers:** E217, E352, E359, E361, E362, E363, E609, E610, E676.
**Unresolved SQL affecting any column:** E419, E609, E668, E669, E673. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E217, E352, E359, E361, E362, E363, E610, E676.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E217, E610 | E610 | E609 | — | yes |
| `organization_tenant_id`, `projection_id`, `canonical_projection_digest` | E609 | E217, E352, E359, E361, E362, E363, E610, E676, E1636 | — | — | — | yes |
| `sealed_at`, `actor_id`, `receipt_digest` | E609 | E217, E610 | — | — | — | yes |
| `sealed_state` | E609 | E217, E610, E1636 | — | — | — | yes |

### `ops.tour_publication`

**Definition:** `migrations/0318_tour_operations_foundation.sql:153`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at`, `state_changed_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `projection_id`, `publication_state`, `projection_digest` | — | E1648 | — | — | — | unknown |
| `actor_id` | — | — | — | — | — | unknown |

### `ops.tour_qc_finding`

**Definition:** `migrations/0318_tour_operations_foundation.sql:146`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `artifact_type`, `artifact_id`, `check_id`, `severity`, `state`, `evidence`, `resolved_at` | — | — | — | — | — | unknown |

### `ops.tour_rights_receipt`

**Definition:** `migrations/0318_tour_operations_foundation.sql:7`. **Product path:** positive static evidence (column detail below).

**Table writers:** E26, E434. **Table readers:** E26, E357, E358, E359, E434, E609, E670, E674.
**Unresolved SQL affecting any column:** E4, E397, E661, E668, E684, E685. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E26, E357, E358, E359, E434, E609, E670, E674.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E26, E357, E358, E359, E434, E609, E670, E674 | — | E26, E434 | — | yes |
| `organization_tenant_id`, `provider`, `policy_key`, `receipt_version` | E26, E434 | E357, E358, E359, E434, E609, E670, E674, E1649 | — | — | — | yes |
| `sku`, `receipt_digest`, `terms_url`, `reviewed_at`, `reviewer`, `intended_use` | E26, E434 | E434, E674 | — | — | — | yes |
| `allowed_field_classes`, `allowed_use_classes`, `effective_at`, `revoked_at`, `status` | E26, E434 | E357, E358, E359, E434, E609, E670, E674 | — | — | — | yes |
| `expires_at` | E26 | E357, E358, E359, E434, E609, E670, E674 | — | — | — | yes |
| `supersedes_receipt_id` | E26, E434 | E434, E674, E1649 | — | — | — | yes |
| `created_at` | — | E434, E674 | — | E26, E434 | — | yes |

### `ops.tour_route_stop`

**Definition:** `migrations/0429_tour_domain_route_cheat_sheet.sql:30`. **Product path:** positive static evidence (column detail below).

**Table writers:** E27. **Table readers:** E4, E27, E28, E322, E351, E675.
**Unresolved SQL affecting any column:** E4, E322. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E27, E28, E351, E675.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E27, E28, E322, E351, E675 | — | E27 | — | yes |
| `organization_tenant_id`, `route_version_id`, `property_id`, `route_sequence` | E27 | E4, E28, E322, E351, E675 | — | — | — | yes |
| `route_label`, `stop_state` | E27 | E4, E322, E351, E675 | — | — | — | yes |
| `appointment_start`, `appointment_end` | E27 | E322, E351, E675 | — | — | — | yes |
| `locked_appointment`, `dwell_minutes`, `buffer_minutes`, `access_coordinate_status` | E27 | E322, E351, E675 | — | declared; caller unknown | — | yes |
| `created_by_actor_id` | E27 | E322 | — | — | — | yes |
| `created_at` | — | E322 | — | E27 | — | yes |
| `assertion_set_digest` | E27 | E4, E322, E675 | — | — | — | yes |

### `ops.tour_route_stop_transition`

**Definition:** `migrations/0429_tour_domain_route_cheat_sheet.sql:47`. **Product path:** positive static evidence (column detail below).

**Table writers:** E28. **Table readers:** E28, E675.
**Unresolved SQL affecting any column:** E4. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E28, E675.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E28, E675 | — | E28 | — | yes |
| `organization_tenant_id`, `old_route_version_id`, `new_route_version_id`, `old_route_stop_id`, `new_route_stop_id`, `disposition` | E28 | E675 | — | — | — | yes |
| `old_route_sequence`, `new_route_sequence`, `created_by_actor_id` | E28 | — | — | — | — | yes |
| `created_at` | — | — | — | E28 | — | yes |

### `ops.tour_route_version`

**Definition:** `migrations/0429_tour_domain_route_cheat_sheet.sql:13`. **Product path:** positive static evidence (column detail below).

**Table writers:** E29, E124. **Table readers:** E4, E27, E28, E29, E124, E322, E351, E356, E675.
**Unresolved SQL affecting any column:** E4, E29. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E27, E28, E124, E322, E351, E356, E675.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E4, E27, E28, E29, E322, E351, E356, E675 | — | E29, E124 | — | yes |
| `organization_tenant_id`, `tour_id` | E29, E124 | E4, E27, E28, E29, E322, E351, E356, E675 | — | — | — | yes |
| `route_version` | E29, E124 | E4, E29, E322, E351, E356, E675 | — | — | — | yes |
| `base_route_version_id`, `routing_provider`, `routing_rights_receipt_id`, `routing_response_digest`, `routing_policy_key` | E29 | E4, E322, E675 | — | — | — | yes |
| `start_point`, `end_point` | E29, E124 | E4, E322, E675 | — | — | — | yes |
| `routing_source` | E29, E124 | E4, E322, E351, E675 | — | — | — | yes |
| `routing_request` | E29, E124 | E4, E322, E675 | — | declared; caller unknown | — | yes |
| `created_by_actor_id` | E29, E124 | E4, E322 | — | — | — | yes |
| `created_at` | — | E4, E322, E351 | — | E29, E124 | — | yes |

### `ops.tour_route_version_acceptance`

**Definition:** `migrations/0429_tour_domain_route_cheat_sheet.sql:63`. **Product path:** positive static evidence (column detail below).

**Table writers:** E4. **Table readers:** E4, E322, E351, E356.
**Unresolved SQL affecting any column:** E4, E27, E28, E29. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E322, E351, E356.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E4, E351 | — | E4 | — | yes |
| `organization_tenant_id`, `route_version_id` | E4 | E4, E322, E351, E356 | — | — | — | yes |
| `tour_id`, `supersedes_acceptance_id`, `expected_prior_route_version`, `accepted_by_actor_id`, `acceptance_digest` | E4 | E4 | — | — | — | yes |
| `accepted_at` | — | E4 | — | E4 | — | yes |

### `ops.tour_selection_cart_version`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:11`. **Product path:** positive static evidence (column detail below).

**Table writers:** E30. **Table readers:** E30, E360.
**Additional lexical/dependency evidence:** E30, E360.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E30, E360 | — | E30 | — | yes |
| `organization_tenant_id`, `tour_id`, `selection_version`, `property_ids` | E30 | E30, E360 | — | — | — | yes |
| `base_selection_version_id`, `selection_digest`, `created_by_actor_id` | E30 | E30 | — | — | — | yes |

### `ops.tour_share_feedback_event`

**Definition:** `migrations/0749_tour_client_feedback.sql:48`. **Product path:** positive static evidence (column detail below).

**Table writers:** E723. **Table readers:** E350, E723.
**Additional lexical/dependency evidence:** E350, E723.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E350, E723 | — | E723 | — | yes |
| `event_order` | — | E350, E723 | — | — | — | yes |
| `organization_tenant_id`, `projection_id`, `property_id`, `action`, `shortlisted`, `comment` | E723 | E350, E723 | — | — | — | yes |
| `share_grant_id`, `idempotency_key`, `payload_digest` | E723 | E723 | — | — | — | unknown |

### `ops.tour_share_grant`

**Definition:** `migrations/0318_tour_operations_foundation.sql:128`. **Product path:** positive static evidence (column detail below).

**Table writers:** E217, E436. **Table readers:** E158, E217, E268, E351, E364, E435, E436, E677, E678, E679.
**Unresolved SQL affecting any column:** E217, E436. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E158, E268, E351, E364, E435, E677, E678, E679.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E158, E217, E351, E364, E435, E436, E677, E678, E679, E1651 | E364 | E217, E436 | — | yes |
| `organization_tenant_id` | E217, E436 | E158, E268, E351, E364, E435, E436, E677, E678, E679, E1651 | — | — | — | yes |
| `projection_id` | E217, E436 | E158, E268, E351, E364, E436, E678, E679, E1651 | — | — | — | yes |
| `grant_version` | E217, E436 | E158, E351, E364, E436, E678, E679, E1651 | — | — | — | yes |
| `token_digest`, `audience`, `receipt_digest`, `created_by_actor_id` | E217, E436 | E158, E364, E436, E678, E679 | — | — | — | yes |
| `permission_scopes`, `expires_at`, `status` | E217, E436 | E158, E351, E364, E436, E678, E679 | — | — | — | yes |
| `rotated_from_grant_id` | E436 | E158, E351, E364, E436, E678, E679, E1651 | — | — | E1651 | yes |
| `created_at` | — | E158, E351, E364, E436, E677, E678, E679 | E364 | E217, E436 | — | yes |
| `revoked_at` | — | E158, E364, E436, E678, E679 | E364 | — | — | yes |

### `ops.tour_share_grant_revocation_receipt`

**Definition:** `migrations/0318_tour_operations_foundation.sql:138`. **Product path:** positive static evidence (column detail below).

**Table writers:** E435. **Table readers:** E158, E351, E364, E435, E679.
**Unresolved SQL affecting any column:** E435, E436. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E158, E351, E364, E679.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E158, E351, E364 | E364 | E435 | — | yes |
| `organization_tenant_id`, `share_grant_id` | E435 | E158, E351, E364, E679, E1650 | — | — | — | yes |
| `revoked_at` | E435 | E158, E364, E1650 | E364 | — | — | yes |
| `actor_id`, `receipt_digest` | E435 | E158 | — | — | — | yes |
| `reason` | E435 | E158, E364 | — | — | — | yes |
| `created_at` | — | E158 | E364 | E435 | — | yes |

### `ops.tour_share_session`

**Definition:** `migrations/0430_tour_delivery_data_plane.sql:24`. **Product path:** positive static evidence (column detail below).

**Table writers:** E158. **Table readers:** E158, E679.
**Additional lexical/dependency evidence:** E158, E679.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | E158 | — | yes |
| `organization_tenant_id`, `share_grant_id`, `session_digest`, `permission_scopes`, `expires_at` | E158 | E679 | — | — | — | yes |
| `audit_digest` | E158 | — | — | — | — | yes |

### `ops.tour_source_evidence`

**Definition:** `migrations/0318_tour_operations_foundation.sql:30`. **Product path:** positive static evidence (column detail below).

**Table writers:** E31. **Table readers:** E31, E357, E358, E359, E609, E661, E668, E670, E684.
**Unresolved SQL affecting any column:** E661, E668, E684. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E31, E357, E358, E359, E609, E670.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E31, E357, E358, E359, E609, E661, E668, E670, E684 | — | E31 | — | yes |
| `organization_tenant_id` | E31 | E357, E358, E359, E609, E661, E668, E670, E684, E1652 | — | — | — | yes |
| `stable_locator`, `evidence_class` | E31 | E358 | — | — | — | yes |
| `retrieved_at` | E31 | E358, E359, E1652 | — | — | — | yes |
| `retrieval_status` | E31 | E358, E359 | — | — | — | yes |
| `content_digest`, `data_classification` | E31 | — | — | — | — | yes |
| `rights_receipt_id` | E31 | E357, E358, E359, E609, E670, E1652 | — | — | — | yes |
| `created_at` | — | — | — | E31 | — | yes |
| `rights_provider`, `rights_policy_key` | E31 | E357, E359, E609, E661, E668, E670, E684, E1652 | — | — | — | yes |

### `ops.v5_a03_adjudication`

**Definition:** `migrations/0719_doctorcre_a03_review_store.sql:231`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E695. **Table readers:** E694, E695.
**Unresolved SQL affecting any column:** E691. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E694, E695.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E694, E695 | — | E695 | — | unknown |
| `case_id`, `outcome`, `disputed_finding_refs`, `receipt_digest` | E695 | E694 | — | — | — | unknown |
| `adjudicator_participant_id`, `idempotency_key` | E695 | — | — | — | — | unknown |
| `created_at` | — | E694 | — | E695 | — | unknown |

### `ops.v5_a03_case_outcome`

**Definition:** `migrations/0719_doctorcre_a03_review_store.sql:250`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E695, E699. **Table readers:** E692, E694, E695, E699.
**Unresolved SQL affecting any column:** E691. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E692, E694, E695, E699.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E694 | — | E695, E699 | — | unknown |
| `case_id` | E695, E699 | E692, E694 | — | — | — | unknown |
| `outcome`, `decided_by` | E695, E699 | E694 | — | — | — | unknown |
| `round_id`, `adjudication_id` | E695, E699 | — | — | — | — | unknown |

### `ops.v5_a03_finding_set`

**Definition:** `migrations/0719_doctorcre_a03_review_store.sql:176`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E696. **Table readers:** E694, E696, E699.
**Unresolved SQL affecting any column:** E695, E699. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E694, E696.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E694, E696 | — | E696 | — | unknown |
| `case_id`, `round_ordinal`, `dimension`, `finding_refs` | E696 | E694, E699 | — | — | — | unknown |
| `reviewer_participant_id` | E696 | — | — | — | — | unknown |
| `reviewed_set_digest`, `submission_state`, `enumerated_before_repair` | E696 | E694 | — | — | — | unknown |
| `idempotency_key` | E696 | E696 | — | — | — | unknown |
| `created_at` | — | E694 | — | E696 | — | unknown |

### `ops.v5_a03_review_case`

**Definition:** `migrations/0719_doctorcre_a03_review_store.sql:126`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E693. **Table readers:** E693, E694, E695, E696, E697, E698, E699.
**Unresolved SQL affecting any column:** E693, E699. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E694, E695, E696, E697, E698.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E693, E694, E695, E696, E697, E698, E699 | — | E693 | — | unknown |
| `tenant`, `change_ref`, `maker_actor_id`, `maker_session_ref` | E693 | E693, E694, E695, E696, E697, E699 | — | — | — | unknown |
| `delivered_set_digest` | E693 | E693, E694, E695, E696, E697, E698, E699 | — | — | — | unknown |
| `idempotency_key` | E693 | E693, E695, E696, E697, E699 | — | — | — | unknown |
| `created_at` | — | E693, E695, E696, E697, E699 | — | E693 | — | unknown |

### `ops.v5_a03_review_participant`

**Definition:** `migrations/0719_doctorcre_a03_review_store.sql:149`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E693, E697. **Table readers:** E693, E694, E695, E696, E697, E699.
**Unresolved SQL affecting any column:** E695, E699. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E693, E694, E696, E697.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E694, E695, E696, E697 | — | E693, E697 | — | unknown |
| `case_id`, `role`, `actor_id` | E693, E697 | E694, E695, E696, E697, E699 | — | — | — | unknown |
| `dimension` | E693, E697 | E694, E696, E697 | — | — | — | unknown |
| `session_ref` | E693, E697 | E694, E695, E696, E697 | — | — | — | unknown |
| `context_binding` | E693, E697 | E694, E697 | — | — | — | unknown |
| `idempotency_key` | E693, E697 | E697 | — | — | — | unknown |
| `created_at` | — | E694, E696, E697 | — | E693, E697 | — | unknown |

### `ops.v5_a03_review_round`

**Definition:** `migrations/0719_doctorcre_a03_review_store.sql:201`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E699. **Table readers:** E692, E694, E695, E696, E698, E699.
**Unresolved SQL affecting any column:** E693, E699. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E692, E694, E695, E696, E698.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E694, E695, E699 | — | E699 | — | unknown |
| `case_id` | E699 | E692, E694, E695, E696, E698, E699 | — | — | — | unknown |
| `round_ordinal` | E699 | E692, E694, E695, E698, E699 | — | — | — | unknown |
| `batch_repair_digest`, `repaired_finding_refs`, `regression_suite_ref`, `detections` | E699 | E694, E695 | — | — | — | unknown |
| `checks_executed`, `state` | E699 | E694, E695, E699 | — | — | — | unknown |
| `post_repair_artifact_digest` | E699 | E694, E695, E698 | — | — | — | unknown |
| `sealed_by_actor_id`, `idempotency_key` | E699 | E695 | — | — | — | unknown |
| `created_at` | — | E694, E695 | — | E699 | — | unknown |

### `ops.v5_a05_cadence_receipt`

**Definition:** `migrations/0617_delivery_cadence_a05.sql:67`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E702. **Table readers:** E701, E702.
**Additional lexical/dependency evidence:** E701, E702.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E701, E702 | — | E702 | — | unknown |
| `organization_tenant_id` | — | E701, E702, E1653 | — | — | E1653 | unknown |
| `subject_type`, `subject_ref`, `expires_at`, `replan_of`, `created_by`, `idempotency_key` | E702 | E701, E702 | — | — | — | unknown |
| `issued_at`, `evidence` | E702 | E701, E702 | — | declared; caller unknown | — | unknown |

### `ops.work_request`

**Definition:** `migrations/0114_ops_schema_work_request.sql:51`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1, E3, E19, E91, E131, E615, E622, E634, E658, E689, E787, E792, E1423. **Table readers:** E1, E2, E3, E7, E10, E19, E40, E74, E78, E81, E82, E90, E91, E103, E107, E111, E112, E113, E126, E131, E132, E136, E139, E142, E143, E144, E146, E148, E151, E199, E201, E203, E206, E207, E215, E216, E278, E279, E300, E329, E331, E332, E340, E341, E347, E366, E374, E415, E615, E618, E619, E621, E622, E623, E626, E628, E629, E630, E631, E633, E634, E647, E649, E650, E652, E658, E688, E689, E713, E716, E717, E719, E734, E739, E783, E785, E787, E789, E792, E896, E1027, E1067, E1075, E1076, E1078, E1084, E1402, E1403, E1404, E1405, E1411, E1412, E1413, E1414, E1417, E1418, E1420, E1422, E1423, E1424, E1435, E1666, E1692.
**Unresolved SQL affecting any column:** E49, E86, E279, E337, E366, E415, E618, E622, E634, E658, E793, E1069, E1408, E1409, E1410, E1416. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E1, E2, E3, E7, E10, E19, E40, E74, E78, E81, E82, E90, E91, E103, E107, E111, E112, E113, E126, E131, E132, E136, E139, E142, E143, E144, E146, E147, E148, E151, E152, E199, E201, E203, E206, E207, E215, E216, E278, E300, E329, E331, E332, E340, E341, E347, E374, E615, E619, E621, E623, E626, E628, E629, E630, E631, E633, E647, E649, E650, E652, E688, E689, E713, E716, E717, E719, E734, E739.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1, E2, E3, E7, E10, E19, E40, E74, E78, E81, E82, E90, E91, E103, E107, E111, E112, E113, E126, E131, E136, E139, E142, E143, E144, E146, E148, E151, E199, E201, E203, E206, E207, E215, E216, E278, E279, E300, E329, E331, E332, E341, E347, E366, E374, E415, E615, E618, E619, E621, E622, E623, E626, E628, E629, E630, E631, E633, E634, E647, E649, E650, E652, E658, E688, E689, E713, E716, E717, E719, E734, E739, E783, E785, E787, E789, E792, E896, E1067, E1075, E1076, E1078, E1084, E1402, E1403, E1404, E1405, E1411, E1412, E1413, E1417, E1418, E1420, E1422, E1423, E1424, E1528, E1556, E1620, E1623, E1656, E1658, E1666, E1692 | E112, E340, E341, E628, E634 | E91 | — | yes |
| `ref` | E91 | E1, E2, E3, E7, E10, E19, E40, E74, E82, E90, E91, E103, E107, E111, E112, E113, E126, E131, E132, E136, E139, E143, E151, E199, E206, E207, E215, E216, E278, E279, E300, E329, E331, E332, E340, E341, E347, E374, E415, E615, E618, E619, E621, E622, E626, E628, E629, E630, E631, E634, E647, E649, E650, E652, E658, E688, E689, E713, E716, E717, E719, E783, E785, E787, E789, E792, E1027, E1067, E1075, E1076, E1084, E1402, E1404, E1411, E1412, E1413, E1414, E1417, E1418, E1420, E1422, E1423, E1424, E1538, E1556, E1656, E1666, E1692 | — | — | — | yes |
| `state` | E3, E19, E91, E131, E622, E634, E658, E689, E787, E792 | E1, E2, E3, E10, E19, E40, E74, E82, E90, E91, E103, E107, E112, E126, E131, E139, E142, E143, E216, E278, E279, E300, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E629, E630, E631, E633, E634, E649, E650, E652, E658, E689, E713, E716, E717, E783, E785, E787, E789, E792, E1027, E1067, E1075, E1076, E1084, E1414, E1420, E1422, E1423, E1424, E1435, E1528, E1529, E1538, E1556, E1620, E1623, E1656, E1658, E1666, E1692 | E341 | declared; caller unknown | E1623, E1658 | yes |
| `title` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E126, E132, E139, E143, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E626, E628, E629, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1027, E1067, E1075, E1076, E1078, E1084, E1402, E1404, E1413, E1414, E1420, E1422, E1423, E1424, E1556, E1666, E1692 | — | — | — | yes |
| `desired_outcome` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E132, E139, E143, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1413, E1423, E1556, E1666 | — | — | — | yes |
| `acceptance_criteria` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E132, E139, E143, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1413, E1414, E1423, E1556, E1666 | — | declared; caller unknown | — | yes |
| `origin_ref` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1423, E1666 | — | — | — | yes |
| `requester_actor`, `sourced_capture_sequence` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1666 | — | — | — | yes |
| `owner_actor` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E142, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E629, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1067, E1075, E1076, E1078, E1084, E1414, E1423, E1538, E1656, E1666 | — | — | — | yes |
| `executor_actor` | E622, E792 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E142, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E629, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1414, E1423, E1435, E1538, E1620, E1656, E1666 | — | — | — | yes |
| `blocker_code` | — | E1, E2, E3, E10, E19, E40, E74, E82, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E633, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1414, E1423, E1666, E1692 | — | — | — | yes |
| `blocker_detail` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1027, E1414, E1423, E1666 | — | — | — | yes |
| `verification_accepted_at` | E634, E787 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1620, E1666 | — | — | — | yes |
| `verification_evidence_ref` | E634, E787 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1528, E1620, E1666 | — | — | — | yes |
| `exit_reason` | E131, E658 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E131, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E658, E689, E713, E716, E783, E785, E787, E789, E792, E1423, E1623, E1666 | — | — | — | yes |
| `superseded_by` | E658 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E658, E689, E713, E716, E783, E785, E787, E789, E792, E1423, E1623, E1666 | — | — | — | yes |
| `captured_at` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E126, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E623, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1666, E1692 | — | E91 | — | yes |
| `claimed_at` | E622, E792 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1414, E1423, E1666 | — | — | — | yes |
| `started_at` | E634, E792 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1414, E1423, E1666 | — | — | — | yes |
| `closed_at` | E131, E634, E658, E787 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E131, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E623, E628, E630, E631, E634, E650, E652, E658, E689, E713, E716, E783, E785, E787, E789, E792, E1423, E1620, E1666 | — | — | — | yes |
| `updated_at` | E1, E3, E19, E131, E615, E622, E634, E658, E689, E787, E792, E1423 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1027, E1414, E1423, E1435, E1666, E1692 | E341 | E91 | — | yes |
| `correlation_id` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1666, E1692 | — | — | — | yes |
| `program_key` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E633, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1414, E1423, E1528, E1529, E1530, E1619, E1620, E1666 | — | — | — | yes |
| `program_ordinal` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E626, E628, E629, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1078, E1414, E1423, E1530, E1666 | — | — | — | yes |
| `disposition`, `existing_status` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1666 | — | — | — | yes |
| `project_context` | — | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1414, E1423, E1666 | — | E91 | — | yes |
| `completion_kind` | E634, E787 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1528, E1538, E1620, E1666 | — | — | E1538 | yes |
| `completion_evidence` | E634, E787 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1528, E1538, E1620, E1666 | — | — | — | yes |
| `version` | E1, E3, E19, E131, E615, E622, E634, E658, E689, E787, E792, E1423 | E1, E2, E3, E10, E19, E40, E74, E82, E90, E91, E103, E107, E111, E112, E113, E131, E139, E143, E206, E207, E215, E216, E278, E279, E300, E329, E331, E332, E341, E347, E374, E615, E618, E619, E621, E622, E628, E629, E630, E631, E634, E647, E649, E650, E652, E658, E689, E713, E716, E783, E785, E787, E789, E792, E1078, E1084, E1413, E1420, E1422, E1423, E1424, E1556, E1620, E1623, E1658, E1666 | — | E91 | E1623 | yes |
| `shape_disposition` | E3, E615, E1423 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1402, E1403, E1420, E1422, E1423, E1424, E1623, E1658, E1666 | — | — | E1623, E1658 | yes |
| `shape_fixed_surface_ref` | E3, E615, E1423 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1420, E1422, E1423, E1424, E1623, E1658, E1666 | — | — | E1623 | yes |
| `shape_rationale` | E3, E615, E1423 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1420, E1422, E1423, E1424, E1623, E1658, E1666 | — | — | E1623 | yes |
| `shape_decided_by_actor_id`, `shape_decided_at` | E3, E615, E1423 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1420, E1423, E1623, E1658, E1666 | — | — | E1623 | yes |
| `organization_tenant_id` | E91 | E1, E2, E3, E10, E19, E40, E74, E82, E90, E91, E103, E107, E112, E113, E126, E139, E206, E207, E215, E216, E278, E279, E300, E329, E331, E332, E341, E347, E374, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E688, E689, E713, E716, E734, E739, E783, E785, E787, E789, E792, E1027, E1067, E1075, E1076, E1078, E1084, E1402, E1403, E1404, E1405, E1413, E1414, E1423, E1435, E1529, E1666 | E340 | — | E1529 | yes |
| `doctrine_section_id` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E126, E139, E216, E278, E279, E300, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1423, E1666 | — | — | — | yes |
| `doctrine_revision_id` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E126, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1423, E1666 | — | — | — | yes |
| `capture_idempotency_key` | E91 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E126, E131, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E658, E689, E716, E783, E785, E787, E789, E792, E1420, E1422, E1423, E1424, E1623, E1666 | — | — | — | yes |
| `triage_classification`, `triaged_by_actor_id` | E689 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1423, E1623, E1666 | — | — | — | yes |
| `triaged_at` | E689 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E649, E650, E652, E689, E713, E716, E783, E785, E787, E789, E792, E1417, E1418, E1423, E1623, E1666 | — | — | — | yes |
| `joe_answer_text`, `joe_answered_by_actor_id`, `joe_answered_at` | E19 | E1, E2, E3, E10, E19, E40, E74, E90, E91, E103, E107, E112, E139, E216, E278, E279, E329, E331, E332, E341, E347, E615, E618, E619, E621, E622, E628, E630, E631, E634, E650, E652, E689, E716, E783, E785, E787, E789, E792, E1423, E1666 | — | — | — | yes |

### `ops.work_request_criterion_evidence`

**Definition:** `migrations/0872_self_closing_records.sql:439`. **Product path:** positive static evidence (column detail below).

**Table writers:** E74. **Table readers:** E74, E107.
**Additional lexical/dependency evidence:** E74, E107.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E74, E107 | — | E74 | — | yes |
| `work_request_id`, `criterion_id`, `receipt_ref` | E74 | E74, E107 | — | — | — | yes |
| `receipt_kind`, `bound_by_actor_id`, `idempotency_key` | E74 | E74 | — | — | — | yes |
| `created_at` | — | E74 | — | E74 | — | yes |

### `ops.work_request_execution_assignment`

**Definition:** `migrations/0303_evidence_activation_reliability.sql:289`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E40. **Table readers:** E40, E70, E111, E206, E207, E215, E216.
**Additional lexical/dependency evidence:** E40, E70, E111, E206, E207, E215, E216.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E40, E70, E206, E215, E216, E1657 | — | E40 | — | unknown |
| `work_request_id` | E40 | E40, E70, E111, E206, E207, E215, E216 | — | — | — | unknown |
| `profile_id` | E40 | E40, E111, E207, E216 | — | — | — | unknown |
| `sponsoring_human_id` | E40 | E40, E207, E216 | — | — | — | unknown |
| `environment`, `policy_ref`, `policy_digest`, `idempotency_key` | E40 | E40, E216 | — | — | — | unknown |
| `created_at` | — | E40, E216 | — | E40 | — | unknown |

### `ops.work_request_execution_environment_binding`

**Definition:** `migrations/0309_governed_execution_environment_providers.sql:61`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E69. **Table readers:** E69, E70, E206, E215.
**Additional lexical/dependency evidence:** E69, E70, E206, E215.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E70, E206 | — | E69 | — | unknown |
| `assignment_id`, `provider_id`, `conformance_id` | E69 | E70, E206, E215 | — | — | — | unknown |
| `requirement`, `requirement_digest`, `configuration`, `configuration_digest`, `binding`, `binding_digest` | E69 | E69, E70, E206 | — | — | — | unknown |

### `ops.work_request_joe_answer_receipt`

**Definition:** `migrations/0575_answer_needs_joe_work_request.sql:71`. **Product path:** positive static evidence (column detail below).

**Table writers:** E19. **Table readers:** E19.
**Additional lexical/dependency evidence:** E19.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `answered_at` | — | E19 | — | E19 | — | yes |
| `work_request_id`, `idempotency_key`, `base_version`, `answer_text`, `scope_confirmed`, `evidence_ref`, `acceptance_criteria_digest`, `answered_by_actor_id`, `result_version` | E19 | E19 | — | — | — | yes |

### `ops.work_request_triage_receipt`

**Definition:** `migrations/0175_program6_human_sourced_triage.sql:14`. **Product path:** positive static evidence (column detail below).

**Table writers:** E689. **Table readers:** E689, E1412.
**Unresolved SQL affecting any column:** E648. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E689.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `triaged_at` | — | E689 | — | E689 | — | yes |
| `work_request_id`, `idempotency_key`, `base_version`, `classification`, `triaged_by_actor_id`, `result_version` | E689 | E689 | — | — | — | yes |

### `ops.work_request_withdrawal_receipt`

**Definition:** `migrations/0426_withdraw_a_work_request_captured_in_error.sql:39`. **Product path:** positive static evidence (column detail below).

**Table writers:** E131, E658. **Table readers:** E131, E658.
**Unresolved SQL affecting any column:** E648. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E131, E658.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | E131, E658 | — | yes |
| `work_request_id`, `idempotency_key`, `base_version`, `result_version`, `final_state`, `exit_reason`, `superseded_by`, `withdrawn_by_actor_id` | E131, E658 | — | — | — | — | yes |
| `withdrawn_at` | E131, E658 | — | — | declared; caller unknown | — | yes |

### `ops.work_shape_revision`

**Definition:** `migrations/0132_work_shape_revision.sql:60`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1426. **Table readers:** E718, E1067, E1075, E1402, E1403, E1421, E1425, E1426, E1693.
**Unresolved SQL affecting any column:** E3, E332, E615. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E718.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E1067, E1075, E1402, E1421, E1425, E1426, E1693 | — | E1426 | — | unknown |
| `work_request_id` | E1426 | E718, E1067, E1075, E1402, E1403, E1421, E1425, E1426, E1624, E1693 | — | — | — | unknown |
| `work_request_version` | E1426 | E718, E1421, E1425, E1426, E1624, E1693 | — | — | — | unknown |
| `version` | E1426 | E718, E1067, E1075, E1402, E1421, E1425, E1426, E1693 | — | — | — | unknown |
| `trinity`, `hidden_assumption`, `repo_searches`, `maintained_repos`, `archetypes`, `chosen_key`, `mind_changing_fact`, `source_url`, `created_by_actor_id` | E1426 | E1421, E1425, E1426, E1693 | — | — | — | unknown |
| `builder_brief` | E1426 | E1067, E1421, E1425, E1426, E1693 | — | — | — | unknown |

### `ops.workflow_acceptance`

**Definition:** `migrations/0149_control_plane_jobs.sql:195`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E400. **Table readers:** E400, E420, E641, E769, E1445.
**Unresolved SQL affecting any column:** E135. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E16, E156, E400, E420, E641.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E400, E641, E769 | E1445 | E400 | — | unknown |
| `workflow_key`, `workflow_version` | E400 | E420, E641, E769, E1445 | E1445 | — | — | unknown |
| `mode`, `status` | E400 | E420, E641, E769, E1445 | — | — | — | unknown |
| `receipt_ref` | E400 | E769 | — | — | — | unknown |
| `accepted_by` | E400 | — | — | — | — | unknown |
| `note` | — | — | — | — | — | unknown |
| `created_at` | — | E769 | — | E400 | — | unknown |

### `ops.workflow_caller`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:126`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E720.
**Additional lexical/dependency evidence:** E401, E720.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `workflow_key`, `workflow_version` | — | E720 | E720 | — | — | unknown |
| `caller_locator`, `caller_kind`, `blocked_reason`, `evidence_ref` | — | E720 | — | — | — | unknown |
| `status` | — | E720 | E720 | declared; caller unknown | — | unknown |
| `updated_by_actor_slug` | — | — | — | — | — | unknown |
| `updated_at` | — | E720 | — | declared; caller unknown | — | unknown |

### `ops.workflow_caller_history`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:378`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Additional lexical/dependency evidence:** E401.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `recorded_at` | — | — | — | declared; caller unknown | — | unknown |
| `workflow_key`, `workflow_version`, `caller_locator`, `caller_kind`, `status`, `blocked_reason`, `evidence_ref`, `actor_slug` | — | — | — | — | — | unknown |

### `ops.workflow_cutover_plan`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:60`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E641, E720, E1082.
**Additional lexical/dependency evidence:** E16, E76, E156, E298, E431, E641, E720.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E641, E720, E1082 | — | declared; caller unknown | — | unknown |
| `workflow_key` | — | E641, E720, E1082 | E720 | — | — | unknown |
| `workflow_version` | — | E641, E720 | E720 | — | — | unknown |
| `stage`, `created_at`, `updated_at` | — | E720, E1082 | — | declared; caller unknown | — | unknown |
| `status` | — | E720, E1082 | E720 | declared; caller unknown | — | unknown |
| `superseded_by`, `recovery_plan` | — | E720 | — | — | — | unknown |
| `supersede_reason`, `idempotency_key` | — | — | — | — | — | unknown |
| `opened_by_actor_slug` | — | E1082 | — | — | — | unknown |

### `ops.workflow_cutover_plan_cancel`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:513`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Additional lexical/dependency evidence:** E76.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `plan_id`, `stage_at_cancel`, `reason`, `actor_slug`, `idempotency_key` | — | — | — | — | — | unknown |
| `cancelled_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.workflow_cutover_retire_receipt_use`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:359`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.
**Additional lexical/dependency evidence:** E431.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `receipt_id`, `plan_id` | — | — | — | — | — | unknown |
| `used_at` | — | — | — | declared; caller unknown | — | unknown |

### `ops.workflow_cutover_stage_transition`

**Definition:** `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:98`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E641, E720.
**Additional lexical/dependency evidence:** E16, E298, E431, E641, E720.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E641 | — | declared; caller unknown | — | unknown |
| `plan_id`, `to_stage` | — | E641, E720 | — | — | — | unknown |
| `from_stage`, `evidence_ref`, `reason` | — | E720 | — | — | — | unknown |
| `actor_slug`, `idempotency_key` | — | — | — | — | — | unknown |
| `occurred_at` | — | E720 | — | declared; caller unknown | — | unknown |

### `public.action_class_successor`

**Definition:** `migrations/0708_action_class_successor_registry.sql:40`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E724, E738.
**Additional lexical/dependency evidence:** E724, E738.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `policy_requirements`, `data_requirements`, `model_requirements`, `status`, `created_at` | — | E738 | — | declared; caller unknown | — | unknown |
| `action_class` | — | E724, E738 | — | — | — | unknown |
| `title`, `goal`, `owner`, `activation_predicate`, `actor_id` | — | E738 | — | — | — | unknown |
| `idempotency_key` | — | — | — | — | — | unknown |

### `public.activity`

**Definition:** `migrations/0001_init.sql:190`. **Product path:** positive static evidence (column detail below).

**Table writers:** E996, E1271. **Table readers:** E712, E987, E988, E993, E996, E1239, E1264, E1265, E1271, E1727, E1748, E1752, E1762, E1765, E1773, E1780, E1803, E1805.
**Unresolved SQL affecting any column:** E1148, E1157, E1212, E1397. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E712, E987, E988, E996, E1239, E1271, E1727, E1752, E1765, E1773, E1780, E1803 | E1773, E1805 | E996 | — | yes |
| `occurred_at` | E996 | E987, E988, E1239, E1727, E1752, E1765, E1773, E1780, E1803 | — | — | — | yes |
| `recorded_at` | — | E712, E988, E1752, E1765, E1773, E1803 | E1762 | E996 | — | yes |
| `actor_id`, `summary` | E996 | E712, E988, E1727, E1752, E1765, E1773, E1780, E1803 | — | — | — | yes |
| `kind` | E996 | E988, E993, E1239, E1727, E1752, E1765, E1773, E1803, E1805 | E1773 | — | — | yes |
| `detail` | E996 | E988, E1727, E1752, E1765, E1773, E1780, E1803 | — | — | — | yes |
| `owed` | E996 | E988, E1752, E1765, E1773, E1803 | — | — | — | yes |
| `deal_id` | — | E712, E988, E1264, E1265, E1727, E1748, E1765, E1773, E1780, E1803 | — | — | — | yes |
| `client_id` | — | E712, E988, E1264, E1265, E1748, E1752, E1765, E1773, E1780, E1803 | E1765 | — | — | yes |
| `lead_id` | E996 | E712, E987, E988, E993, E1239, E1264, E1265, E1748, E1752, E1765, E1773, E1780, E1803 | — | — | — | yes |
| `vendor_id` | E1271 | E712, E988, E1264, E1265, E1271, E1748, E1765, E1773, E1780, E1803, E1805 | — | — | — | yes |
| `source` | E996 | E988, E1727, E1752, E1765, E1773 | — | declared; caller unknown | — | yes |
| `version`, `updated_at` | — | E988, E1508, E1765, E1773 | — | E996 | E1508 | yes |
| `updated_by` | — | E988, E1765, E1773 | — | — | — | yes |
| `connected` | — | E988, E1239, E1765, E1773, E1805 | — | — | — | yes |

### `public.activity_kind`

**Definition:** `migrations/0017_vocab_ref_tables.sql:102`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1765.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `is_contact` | — | E1765 | — | — | — | yes |
| `label` | — | — | — | — | — | unknown |

### `public.actor`

**Definition:** `migrations/0001_init.sql:92`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1, E2, E3, E5, E8, E11, E12, E15, E17, E18, E19, E20, E23, E34, E35, E40, E44, E50, E51, E52, E53, E54, E71, E73, E74, E79, E81, E82, E87, E107, E127, E129, E130, E131, E136, E144, E146, E151, E157, E183, E186, E188, E189, E193, E194, E199, E201, E203, E207, E210, E216, E255, E270, E275, E276, E278, E279, E280, E282, E283, E285, E290, E302, E303, E317, E341, E368, E369, E371, E372, E373, E379, E380, E381, E390, E397, E398, E400, E402, E405, E429, E438, E439, E457, E611, E615, E617, E618, E619, E621, E622, E623, E625, E628, E630, E631, E634, E655, E658, E687, E688, E689, E693, E694, E697, E700, E704, E711, E712, E713, E714, E731, E738, E741, E784, E796, E814, E815, E816, E817, E819, E820, E821, E865, E866, E869, E888, E891, E893, E894, E905, E966, E970, E1006, E1013, E1015, E1016, E1017, E1021, E1022, E1025, E1087, E1117, E1131, E1138, E1143, E1160, E1168, E1169, E1183, E1188, E1233, E1361, E1366, E1387, E1412, E1427, E1499, E1663, E1679, E1696, E1705, E1706, E1707, E1723, E1725, E1726, E1727, E1728, E1734, E1736, E1737, E1741, E1742, E1743, E1744, E1749, E1750, E1751, E1752, E1754, E1756, E1758, E1759, E1762, E1763, E1766, E1779, E1780, E1781, E1788, E1793, E1795, E1796, E1799, E1800, E1803, E1804.
**Unresolved SQL affecting any column:** E49, E86, E97, E108, E141, E157, E183, E203, E273, E383, E422, E430, E653, E656, E659, E737, E740, E813, E824, E828, E1054, E1058, E1240, E1430, E1431. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E1, E2, E3, E5, E8, E11, E12, E15, E17, E18, E19, E20, E23, E34, E35, E40, E44, E50, E51, E52, E53, E54, E71, E73, E74, E79, E81, E82, E87, E107, E127, E129, E130, E131, E136, E144, E146, E147, E151, E152, E186, E188, E189, E193, E194, E199, E201, E207, E210, E216, E255, E270, E275, E276, E278, E279, E280, E282, E283, E285, E290, E302, E303, E317, E341, E368, E369, E371, E372, E373, E379, E380, E381, E390, E397, E398, E400, E402, E405, E429, E438, E439, E457, E611, E615, E617, E618, E619, E621, E622, E623, E625, E628, E630, E631, E634, E655, E658, E687, E688, E689, E693, E694, E697, E700, E704, E711, E712, E713, E714, E731, E738, E741.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1, E2, E3, E5, E8, E11, E12, E15, E17, E18, E19, E20, E23, E34, E35, E40, E44, E50, E51, E52, E53, E54, E71, E73, E74, E79, E81, E82, E87, E107, E127, E129, E130, E131, E136, E144, E146, E151, E157, E183, E186, E188, E189, E193, E194, E199, E201, E203, E207, E210, E216, E255, E270, E276, E278, E279, E280, E282, E283, E285, E290, E302, E303, E317, E341, E368, E369, E371, E372, E373, E379, E380, E381, E390, E397, E398, E400, E402, E405, E429, E438, E439, E457, E611, E615, E617, E618, E619, E621, E622, E623, E625, E628, E630, E631, E634, E655, E658, E687, E688, E689, E693, E694, E697, E700, E704, E711, E712, E713, E714, E731, E738, E741, E784, E796, E821, E865, E866, E869, E888, E891, E893, E894, E905, E966, E970, E1006, E1015, E1016, E1087, E1117, E1131, E1138, E1143, E1160, E1168, E1169, E1183, E1188, E1233, E1361, E1366, E1387, E1412, E1427, E1499, E1551, E1663, E1679, E1696, E1705, E1706, E1707, E1723, E1725, E1726, E1727, E1728, E1734, E1736, E1737, E1741, E1742, E1743, E1744, E1749, E1750, E1751, E1752, E1754, E1756, E1758, E1759, E1762, E1763, E1766, E1779, E1780, E1781, E1788, E1793, E1795, E1799, E1800, E1803, E1804 | E210, E275, E628, E814, E815, E816, E817, E819, E820, E821, E1013, E1017, E1021, E1022, E1025 | declared; caller unknown | — | yes |
| `slug` | — | E1, E2, E3, E5, E8, E11, E12, E15, E17, E18, E19, E20, E23, E34, E35, E40, E44, E50, E51, E52, E53, E54, E71, E73, E74, E79, E82, E87, E107, E127, E129, E130, E131, E136, E144, E146, E151, E157, E183, E186, E188, E189, E193, E194, E199, E201, E207, E210, E216, E255, E270, E275, E276, E278, E279, E280, E282, E283, E285, E290, E302, E303, E317, E341, E368, E369, E371, E372, E373, E379, E380, E381, E390, E397, E398, E400, E402, E405, E429, E438, E457, E611, E615, E617, E618, E619, E621, E622, E623, E625, E628, E630, E631, E634, E655, E658, E687, E688, E689, E694, E700, E711, E712, E713, E714, E731, E738, E741, E784, E796, E814, E815, E816, E817, E819, E820, E821, E865, E866, E869, E888, E891, E893, E894, E905, E966, E970, E1013, E1015, E1016, E1017, E1021, E1022, E1025, E1087, E1117, E1131, E1138, E1143, E1160, E1168, E1169, E1183, E1188, E1233, E1361, E1366, E1387, E1412, E1427, E1499, E1551, E1663, E1679, E1696, E1705, E1706, E1707, E1723, E1725, E1726, E1727, E1728, E1734, E1736, E1737, E1741, E1742, E1743, E1744, E1751, E1752, E1756, E1759, E1762, E1763, E1766, E1779, E1780, E1781, E1788, E1793, E1795, E1796, E1799, E1800, E1803, E1804 | — | — | — | yes |
| `kind` | — | E1, E2, E3, E11, E12, E15, E17, E18, E19, E34, E35, E40, E50, E51, E52, E53, E54, E71, E127, E129, E131, E144, E146, E157, E189, E194, E199, E201, E207, E216, E270, E276, E278, E279, E280, E283, E290, E302, E303, E317, E368, E372, E373, E381, E400, E402, E405, E429, E439, E457, E615, E622, E628, E687, E688, E689, E700, E704, E711, E712, E714, E741, E821, E891, E893, E894, E1006, E1551 | E628, E1017, E1022 | — | — | yes |
| `display_name` | — | E2, E3, E19, E40, E50, E51, E52, E71, E131, E207, E216, E278, E279, E372, E373, E400, E402, E405, E457, E615, E622, E628, E655, E687, E688, E689, E712, E714, E821, E1117, E1188, E1233, E1705, E1707, E1737, E1749, E1750, E1754, E1758, E1766 | — | — | — | yes |
| `email` | — | E2, E3, E19, E40, E50, E51, E52, E71, E131, E207, E216, E278, E279, E372, E373, E400, E402, E405, E457, E615, E622, E628, E687, E688, E689, E714, E821, E1117 | — | — | — | yes |
| `active` | — | E1, E2, E3, E8, E11, E15, E17, E18, E19, E34, E35, E40, E44, E50, E51, E52, E53, E54, E71, E79, E82, E87, E127, E129, E130, E131, E144, E146, E151, E188, E189, E194, E199, E201, E203, E207, E216, E270, E278, E279, E280, E283, E290, E302, E303, E317, E341, E368, E369, E372, E373, E380, E400, E402, E405, E429, E457, E615, E617, E618, E619, E621, E622, E623, E628, E630, E631, E634, E687, E688, E689, E693, E697, E700, E711, E714, E741, E821, E891, E893, E894, E970, E1143, E1188, E1233, E1361, E1387, E1551 | — | declared; caller unknown | — | yes |

### `public.actor_profile`

**Definition:** `migrations/0001_init.sql:105`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `actor_id`, `key`, `value` | — | — | — | — | — | unknown |
| `updated_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.agent_profile`

**Definition:** `migrations/0284_named_agent_profiles.sql:26`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E755. **Table readers:** E40, E111, E207, E216, E752, E754, E755.
**Unresolved SQL affecting any column:** E207. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E40, E111, E216.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E40, E111, E207, E216, E755 | — | declared; caller unknown | — | unknown |
| `profile_key` | — | E40, E111, E207, E216, E752, E754, E755 | — | — | — | unknown |
| `display_name` | — | E40, E216, E754, E755 | — | — | — | unknown |
| `charter`, `sponsor_scope` | — | E40, E216, E754, E755 | — | declared; caller unknown | — | unknown |
| `current_model`, `current_desk`, `status` | E755 | E40, E207, E216, E754, E755 | — | — | — | unknown |
| `version` | E755 | E40, E216, E752, E754, E755 | — | declared; caller unknown | — | unknown |
| `created_at` | — | E40, E216 | — | declared; caller unknown | — | unknown |
| `updated_at` | E755 | E40, E216 | — | declared; caller unknown | — | unknown |

### `public.agent_profile_assignment`

**Definition:** `migrations/0284_named_agent_profiles.sql:45`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E753. **Table readers:** E753.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E753 | — | E753 | — | unknown |
| `profile_id`, `model`, `desk`, `status`, `ruled_by`, `ruling_basis`, `note`, `idempotency_key` | E753 | — | — | — | — | unknown |
| `created_at` | — | — | — | E753 | — | unknown |

### `public.agreement`

**Definition:** `migrations/0001_init.sql:692`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `client_id`, `kind`, `signed_on`, `expires_on`, `tail_months`, `status`, `doc_attachment`, `note`, `created_by`, `updated_by` | — | — | — | — | — | unknown |
| `version`, `updated_at` | — | E1509 | — | declared; caller unknown | E1509 | unknown |

### `public.ammo_item`

**Definition:** `migrations/0001_init.sql:863`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `status`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `kind`, `body`, `provenance`, `expires_on`, `evidence` | — | — | — | — | — | unknown |

### `public.attachment`

**Definition:** `migrations/0001_init.sql:972`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E745.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `subject_type`, `subject_id`, `r2_key`, `filename`, `mime`, `sha256`, `created_by` | — | — | — | — | — | unknown |
| `bytes`, `deleted_at` | — | E745 | — | — | — | unknown |

### `public.availability`

**Definition:** `migrations/0001_init.sql:613`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E123, E1762, E1792.
**Additional lexical/dependency evidence:** E123.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `observed_at` | — | E123, E1792 | — | declared; caller unknown | — | unknown |
| `space_id`, `source`, `rate_norm_sf_yr`, `opex_sf_yr`, `available_on`, `note` | — | E123, E1792 | — | — | — | unknown |
| `status` | — | E123, E1792 | E1762 | — | — | unknown |
| `rate_amount`, `rate_basis`, `rate_norm_gross_sf_yr` | — | E123, E1516, E1792 | — | — | — | unknown |
| `norm_owed` | — | E123, E1516, E1762, E1792 | — | declared; caller unknown | E1516 | yes |

### `public.board_answer`

**Definition:** `migrations/0740_board_answers.sql:42`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E779, E781, E782. **Table readers:** E779, E781, E782, E1028.
**Unresolved SQL affecting any column:** E777, E780. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E779, E781, E782, E1028 | — | E779 | — | unknown |
| `cursor` | — | E779, E781, E782 | — | — | — | unknown |
| `organization_tenant_id`, `sponsoring_human_slug`, `board_id`, `question_id`, `question_revision` | E779 | E779, E781, E782, E1028 | — | — | — | unknown |
| `asker_ref`, `answer_text`, `answered_by`, `answered_by_actor_id` | E779 | E779, E781, E782 | — | — | — | unknown |
| `sent_at` | — | E779, E781, E782 | — | E779 | — | unknown |
| `received_at`, `received_by_actor_id`, `received_for_ref` | E781 | E779, E781, E782 | — | — | — | unknown |
| `applied_at`, `applied_by_actor_id`, `effect_ref` | E782 | E779, E781, E782 | — | — | — | unknown |
| `default_overridden` | E779 | E779, E781, E782 | — | declared; caller unknown | — | unknown |
| `version` | E781, E782 | E779, E781, E782 | — | E779 | — | unknown |

### `public.board_question`

**Definition:** `migrations/0740_board_answers.sql:16`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E772, E773, E774. **Table readers:** E772, E773, E774, E778, E1028.
**Unresolved SQL affecting any column:** E777. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E772, E774 | — | E772, E774 | — | unknown |
| `organization_tenant_id`, `sponsoring_human_slug`, `board_id`, `question_id`, `revision` | E772, E774 | E772, E773, E774, E778, E1028 | — | — | — | unknown |
| `current` | E772, E773, E774 | E772, E773, E774, E778, E1028 | — | declared; caller unknown | — | unknown |
| `prompt` | E772, E774 | E772, E774, E1028 | — | — | — | unknown |
| `choices`, `allow_free_text` | E772, E774 | E772, E774, E778 | — | declared; caller unknown | — | unknown |
| `default_answer`, `asker_ref` | E772, E774 | E772, E774, E778 | — | — | — | unknown |
| `asked_by_actor_id` | E772, E774 | E772, E774 | — | — | — | unknown |
| `asked_at` | — | E772, E774, E1028 | — | E772, E774 | — | unknown |
| `updated_at` | E773 | E772, E774 | — | E772, E774 | — | unknown |

### `public.board_snapshot`

**Definition:** `migrations/0740_board_answers.sql:4`. **Product path:** positive static evidence (column detail below).

**Table writers:** E770, E771. **Table readers:** E770, E771, E775, E776, E1029, E1067, E1068, E1071, E1072, E1074, E1151.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E770, E771 | — | E770 | — | unknown |
| `organization_tenant_id`, `sponsoring_human_slug`, `board_id` | E770 | E770, E771, E775, E776, E1029, E1067, E1068, E1071, E1072, E1074, E1151 | — | — | — | yes |
| `version` | E770, E771 | E770, E771, E776, E1068, E1072, E1074 | — | declared; caller unknown | — | unknown |
| `snapshot_json` | E770, E771 | E770, E771, E775, E776, E1029, E1067, E1068, E1071, E1072, E1074, E1151 | — | — | — | yes |
| `updated_by_actor_id` | E770, E771 | — | — | — | — | unknown |
| `updated_at` | E771 | E770, E771, E775, E776, E1029, E1068 | — | E770 | — | unknown |

### `public.building`

**Definition:** `migrations/0001_init.sql:548`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1192. **Table readers:** E123, E978, E983, E1119, E1191, E1192, E1721, E1740.
**Additional lexical/dependency evidence:** E123.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E123, E978, E983, E1119, E1191, E1192, E1721, E1740 | — | E1192 | — | yes |
| `parcel_id`, `class`, `year_built`, `stories`, `status`, `status_source` | — | — | — | — | — | unknown |
| `address`, `city` | E1192 | E123, E978, E1119, E1191, E1721, E1740 | — | — | — | yes |
| `state` | E1192 | E123, E978, E1119, E1721, E1740 | — | — | — | yes |
| `zip` | E1192 | E978 | — | — | — | yes |
| `name` | E1192 | E123, E1740 | — | — | — | yes |
| `sub_type` | — | E123 | — | — | — | unknown |
| `merged_into` | — | E978, E1191 | — | — | — | yes |
| `version`, `updated_at` | — | E1522 | — | E1192 | E1522 | yes |
| `created_at` | — | — | — | E1192 | — | yes |
| `created_by`, `updated_by` | E1192 | — | — | — | — | yes |

### `public.building_ownership`

**Definition:** `migrations/0001_init.sql:575`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1198. **Table readers:** E1198, E1721, E1784.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1784 | — | E1198 | — | yes |
| `building_id` | E1198 | E1721 | — | — | — | yes |
| `party_id`, `kind` | E1198 | E1721, E1784 | — | — | — | yes |
| `from_on` | — | — | — | — | — | unknown |
| `to_on` | — | E1721 | — | — | — | unknown |
| `source`, `created_by` | E1198 | — | — | — | — | yes |
| `created_at` | — | — | — | E1198 | — | yes |

### `public.cadence_rule`

**Definition:** `migrations/0019_wave2_machinery_tables.sql:82`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `active` | — | — | — | declared; caller unknown | — | unknown |
| `lane`, `subject_type`, `trigger`, `interval_days`, `action_template`, `status_filter` | — | — | — | — | — | unknown |

### `public.campaign`

**Definition:** `migrations/0001_init.sql:815`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1330, E1332. **Table readers:** E1105, E1106, E1107, E1329, E1330, E1331, E1332, E1337, E1698, E1789, E1793.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1105, E1106, E1329, E1330, E1331, E1332, E1337, E1698, E1789, E1793 | — | E1330 | — | unknown |
| `name` | E1330 | E1105, E1106, E1107, E1329, E1337, E1698, E1789, E1793 | — | — | — | unknown |
| `goal`, `success_criterion` | E1330 | E1105, E1106, E1698 | — | — | — | unknown |
| `status` | E1330, E1332 | E1105, E1106, E1107, E1329, E1332, E1698 | — | declared; caller unknown | — | unknown |
| `starts_on`, `ends_on` | E1330 | E1105, E1106, E1329, E1330, E1698 | — | — | — | unknown |
| `channels` | E1330 | E1105, E1106, E1523, E1698 | — | declared; caller unknown | — | unknown |
| `outcome_verdict`, `outcome_note`, `scored_at` | E1332 | E1105, E1106, E1698 | — | — | — | unknown |
| `coverage_at_scoring`, `scored_by` | E1332 | E1105, E1106 | — | — | — | unknown |
| `version` | — | E1105, E1106, E1330, E1331, E1524 | — | E1330 | E1524 | unknown |
| `created_at` | — | E1105, E1106, E1107, E1331 | — | E1330 | — | unknown |
| `created_by` | E1330 | E1105, E1106 | — | — | — | unknown |
| `updated_at` | — | E1105, E1106, E1524 | — | E1330 | E1524 | unknown |
| `updated_by` | E1330, E1332 | E1105, E1106 | — | — | — | unknown |

### `public.candidate_pool`

**Definition:** `migrations/0023_prospect_pool.sql:32`. **Product path:** positive static evidence (column detail below).

**Table writers:** E210, E1208, E1211. **Table readers:** E210, E746, E1203, E1204, E1208, E1209, E1210, E1211, E1694, E1702, E1712, E1713.
**Additional lexical/dependency evidence:** E210, E608.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1203, E1204, E1208, E1209, E1210, E1211, E1694, E1702 | E210 | E210 | — | yes |
| `source` | E210 | E1204, E1210, E1694, E1702, E1712, E1713 | — | — | — | yes |
| `source_key` | E210 | E1204, E1210, E1694, E1712 | — | — | — | yes |
| `source_seq`, `source_row`, `address` | E210 | E210, E1694 | — | — | — | unknown |
| `name` | E210 | E210, E1204, E1210, E1694, E1702 | — | — | — | yes |
| `org_name`, `city`, `county`, `email`, `phone`, `segment`, `est_basis` | E210 | E210, E1204, E1694, E1702 | — | — | — | yes |
| `vertical`, `state` | E210 | E210, E1204, E1694, E1702, E1712 | — | — | — | yes |
| `segment_play`, `dup_basis` | — | E1694, E1702 | — | — | — | yes |
| `score` | E210 | E1694, E1702, E1713 | — | — | — | yes |
| `score_basis` | E210 | E1694, E1702 | — | — | — | yes |
| `est_lease_event` | E210 | E210, E1204, E1694, E1702, E1713 | — | — | — | yes |
| `status` | E210, E1208, E1211 | E210, E1204, E1208, E1210, E1211, E1694, E1702, E1712, E1713 | — | declared; caller unknown | — | yes |
| `promoted_lead_id` | E1208 | E1210, E1694 | — | — | — | yes |
| `dup_tier`, `dup_ref` | — | E1204, E1694, E1702 | — | — | — | yes |
| `dup_subject_type`, `dup_subject_id` | — | E1694 | — | — | — | unknown |
| `dup_do_not_contact` | — | E1694, E1702 | — | E210 | — | yes |
| `version` | — | E1203, E1209, E1694, E1702 | — | E210 | — | yes |
| `created_at` | — | E1203, E1209, E1694, E1702, E1712 | — | E210 | — | yes |
| `created_by` | E210 | E1694 | — | — | — | unknown |
| `updated_at` | — | E1694, E1713 | — | E210 | — | unknown |
| `updated_by` | E210, E1208, E1211 | E210, E1694 | — | — | — | yes |
| `declined_at`, `declined_by`, `decline_reason` | E1211 | E1694 | — | — | — | yes |

### `public.capture_candidate`

**Definition:** `migrations/0081_capture_bridge.sql:24`. **Product path:** positive static evidence (column detail below).

**Table writers:** E803, E1381, E1383. **Table readers:** E799, E802, E803, E804, E809, E812, E1380, E1381, E1383, E1699.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E804, E1380, E1381, E1383, E1699 | E799, E812 | E803 | — | yes |
| `session_id` | E803 | E802, E804, E1699 | E799, E809, E812 | — | — | yes |
| `idempotency_key` | E803 | E802, E804 | — | — | — | unknown |
| `batch_hash` | E803 | E802 | — | — | — | unknown |
| `item_index` | E803 | E804 | — | — | — | unknown |
| `kind` | E803 | E1380, E1699 | E812 | — | — | yes |
| `payload` | E803 | E1380, E1699 | — | — | — | yes |
| `evidence_quote`, `confidence` | E803 | E1699 | — | — | — | yes |
| `status` | E1381, E1383 | E1380, E1699 | E799, E809, E812 | E803 | — | yes |
| `resolved_by`, `resolution_note`, `resolved_at` | E1381, E1383 | — | — | — | — | unknown |
| `resulting_ref` | E1383 | E1380 | E812 | — | — | unknown |
| `created_at` | — | E1699 | — | E803 | — | yes |

### `public.capture_post_call_action`

**Definition:** `migrations/0094_call_mode_post_call.sql:63`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1134, E1162, E1167, E1214, E1388. **Table readers:** E1134, E1162, E1167, E1169, E1214, E1388, E1726, E1804.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1134, E1162, E1167, E1214, E1388, E1726, E1804 | — | E1388 | — | yes |
| `candidate_id`, `accepted_by` | E1388 | — | — | — | — | unknown |
| `deal_id`, `owner_id`, `description` | E1388 | E1134, E1162, E1167, E1169, E1214, E1726, E1804 | — | — | — | yes |
| `due_on` | E1388 | E1134, E1167, E1169, E1214, E1726, E1804 | — | — | — | yes |
| `accepted_at` | — | E1726 | — | E1388 | — | yes |
| `status` | E1134, E1162, E1167, E1214 | E1134, E1162, E1167, E1169, E1214, E1726, E1804 | — | E1388 | — | yes |
| `updated_at` | E1134, E1162, E1167, E1214 | E1726 | — | E1388 | — | yes |
| `completed_at` | E1134, E1162, E1167, E1214 | — | — | — | — | yes |
| `item_title` | E1388 | E1134, E1162, E1167, E1169, E1214, E1726 | — | — | — | yes |
| `item_bullets` | E1388 | E1726 | — | declared; caller unknown | — | yes |
| `source_ref` | E1388 | E1726 | — | — | — | yes |

### `public.capture_post_call_candidate`

**Definition:** `migrations/0094_call_mode_post_call.sql:6`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E807, E1385, E1386, E1389. **Table readers:** E799, E806, E807, E808, E809, E812, E1384, E1385, E1386, E1389.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E808, E1384, E1385, E1386, E1389 | E799, E812 | E807 | — | unknown |
| `session_id` | E807 | E806, E808 | E799, E809, E812 | — | — | unknown |
| `idempotency_key` | E807 | E806, E808 | — | — | — | unknown |
| `batch_hash` | E807 | E806 | — | — | — | unknown |
| `item_index` | E807 | E808 | — | — | — | unknown |
| `kind` | E807 | E1384 | E812 | — | — | unknown |
| `deal_id`, `assignee_slug`, `action_description`, `due_on`, `recipient_party_id`, `recipient_ref`, `email_subject`, `body_sha256`, `evidence_quote` | E807 | E1384 | — | — | — | unknown |
| `confidence` | E807 | — | — | — | — | unknown |
| `status` | E1385, E1386, E1389 | E1384 | E799, E809, E812 | E807 | — | unknown |
| `resolved_by`, `resolution_note`, `resolved_at` | E1385, E1386, E1389 | — | — | — | — | unknown |
| `resulting_ref` | E1389 | E1384 | E812 | — | — | unknown |
| `created_at` | — | — | — | E807 | — | unknown |

### `public.capture_post_call_report`

**Definition:** `migrations/0094_call_mode_post_call.sql:54`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E811. **Table readers:** E799, E801, E805, E810, E811, E812.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `session_id` | E811 | E799, E801, E805, E810, E812 | E799, E812 | — | — | unknown |
| `idempotency_key` | E811 | E810 | — | — | — | unknown |
| `report_sha256` | E811 | E810, E812 | — | — | — | unknown |
| `candidate_count` | E811 | E799, E810, E812 | — | — | — | unknown |
| `filed_at` | — | — | — | E811 | — | unknown |

### `public.capture_session`

**Definition:** `migrations/0081_capture_bridge.sql:6`. **Product path:** positive static evidence (column detail below).

**Table writers:** E797, E800. **Table readers:** E795, E797, E800, E812, E1699, E1701.
**Unresolved SQL affecting any column:** E798. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E795, E800, E812, E1699, E1701 | E812 | E797 | — | yes |
| `nonce` | E797 | E795 | — | — | — | unknown |
| `device_id`, `started_at` | E797 | E1701 | — | — | — | yes |
| `actor_id`, `mode`, `consent_announced_at`, `session_token_hash` | E797 | — | — | — | — | unknown |
| `expires_at` | E797 | E1699, E1701 | — | — | — | yes |
| `state` | E797, E800 | E800, E812, E1701 | — | declared; caller unknown | — | yes |
| `state_at` | E797, E800 | E800, E1701 | — | — | — | yes |
| `state_detail` | E800 | — | — | — | — | unknown |
| `created_at` | — | — | — | E797 | — | unknown |
| `post_call` | E797 | E812 | — | declared; caller unknown | — | unknown |

### `public.claude_continuity_checkpoint`

**Definition:** `migrations/0485_claude_continuity.sql:25`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `checkpoint_version` | — | — | — | declared; caller unknown | — | unknown |
| `leaf_id` | — | E1531 | — | — | — | unknown |
| `state`, `cursor`, `transcript_digest`, `source_observed_at` | — | — | — | — | — | unknown |
| `compaction_generation` | — | E1531 | — | declared; caller unknown | — | unknown |
| `created_at`, `updated_at` | — | E1531 | — | declared; caller unknown | E1531 | unknown |

### `public.claude_continuity_event`

**Definition:** `migrations/0485_claude_continuity.sql:54`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `checkpoint_version`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `surface_principal_actor_id`, `leaf_id`, `event_type`, `cursor`, `transcript_digest`, `observed_at`, `telemetry`, `idempotency_key` | — | — | — | — | — | unknown |

### `public.claude_continuity_leaf`

**Definition:** `migrations/0485_claude_continuity.sql:5`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E611, E612.
**Additional lexical/dependency evidence:** E611, E612.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `transcript_path_digest` | — | E1532 | — | — | — | unknown |
| `surface_principal_actor_id`, `owner_actor_id`, `project_affinity`, `native_agent_id` | — | E612, E1532 | — | — | — | unknown |
| `session_id`, `parent_session_id` | — | E611, E612, E1532 | — | — | — | unknown |
| `latest_cwd`, `latest_model_id` | — | E612 | — | — | — | unknown |
| `created_at` | — | E1532, E1533 | — | declared; caller unknown | E1533 | unknown |
| `updated_at` | — | E612, E1533 | — | declared; caller unknown | E1533 | unknown |

### `public.claude_continuity_revision`

**Definition:** `migrations/0485_claude_continuity.sql:38`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `checkpoint_id`, `checkpoint_version`, `state`, `cursor`, `transcript_digest`, `source_observed_at`, `compaction_generation`, `created_by_actor_id` | — | — | — | — | — | unknown |

### `public.client`

**Definition:** `migrations/0001_init.sql:280`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1223, E1224, E1365, E1372. **Table readers:** E123, E380, E395, E712, E978, E981, E1010, E1011, E1012, E1050, E1055, E1113, E1116, E1178, E1217, E1218, E1223, E1224, E1229, E1264, E1265, E1266, E1268, E1351, E1360, E1365, E1368, E1370, E1372, E1436, E1449, E1700, E1703, E1704, E1705, E1708, E1709, E1710, E1721, E1723, E1725, E1728, E1747, E1749, E1750, E1751, E1752, E1753, E1762, E1764, E1765, E1768, E1770, E1772, E1774, E1785, E1787, E1794, E1795.
**Unresolved SQL affecting any column:** E828, E1054. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E123, E380, E395, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E123, E380, E712, E978, E1010, E1012, E1050, E1055, E1116, E1178, E1217, E1218, E1223, E1224, E1229, E1264, E1265, E1351, E1360, E1365, E1368, E1370, E1372, E1700, E1703, E1704, E1705, E1709, E1710, E1721, E1723, E1725, E1728, E1747, E1749, E1750, E1751, E1752, E1753, E1764, E1765, E1768, E1770, E1772, E1774, E1794, E1795 | E1011, E1436 | E1224, E1365, E1372 | — | yes |
| `roster_ref` | E1224, E1365, E1372 | E123, E1113, E1116, E1264, E1370, E1436, E1449, E1703, E1708, E1709, E1721, E1723, E1725, E1728, E1747, E1749, E1750, E1751, E1753, E1768, E1785, E1787, E1794 | — | — | — | yes |
| `party_id` | E1224, E1365, E1372 | E123, E712, E978, E981, E1010, E1012, E1050, E1055, E1113, E1116, E1218, E1229, E1264, E1265, E1368, E1370, E1703, E1705, E1708, E1709, E1710, E1723, E1725, E1728, E1749, E1750, E1751, E1753, E1764, E1768, E1772, E1774, E1785, E1787, E1794, E1795 | E1011, E1265, E1266, E1268, E1436, E1787 | — | — | yes |
| `client_type` | E1365, E1372 | E1351, E1360, E1368, E1703, E1725, E1753 | — | — | — | yes |
| `vertical` | E1224, E1365, E1372 | E1116, E1705, E1750, E1753, E1795 | — | — | — | yes |
| `subtype` | E1224 | E1116, E1753 | — | — | — | yes |
| `status` | E1224, E1365, E1372 | E1218, E1436, E1703, E1704, E1705, E1708, E1709, E1728, E1747, E1749, E1750, E1753, E1770, E1794 | E1762 | — | — | yes |
| `etl_status` | — | E1753 | — | — | — | unknown |
| `acquisition_source` | E1224, E1365, E1372 | E1749, E1753 | — | — | — | yes |
| `acquisition_detail` | E1224 | E1753 | — | — | — | yes |
| `notes_path` | — | E1749, E1750, E1752, E1753 | — | — | — | unknown |
| `version` | — | E1217, E1223, E1534, E1704, E1770 | — | E1224, E1365, E1372 | E1534 | yes |
| `created_at` | — | E1217, E1709, E1710 | E1436, E1762 | E1224, E1365, E1372 | — | yes |
| `created_by` | E1224, E1365, E1372 | E380, E1795 | — | — | — | yes |
| `updated_at` | — | E1534, E1753 | — | E1224, E1365, E1372 | E1534 | yes |
| `updated_by` | E1223, E1224, E1365, E1372 | — | — | — | — | yes |
| `owner_id` | E1224, E1365, E1372 | E1705, E1749, E1750 | — | — | — | yes |
| `owner_label` | E1224, E1365, E1372 | E1705, E1749, E1750, E1753 | — | — | — | yes |
| `merged_into` | — | E978, E1012, E1050, E1055, E1113, E1178, E1218, E1229, E1264, E1368, E1370, E1700, E1703, E1704, E1705, E1708, E1725, E1749, E1750, E1764, E1768, E1770, E1772, E1774, E1785, E1794 | E1266, E1268, E1436 | — | — | yes |
| `contact_label` | — | E1116, E1749, E1753 | — | — | — | unknown |
| `deal_type_label`, `specialty_type_label` | — | E1749, E1750, E1753 | — | — | — | unknown |
| `possible_duplicate_label` | — | E1749 | — | — | — | unknown |
| `notes` | — | E1749, E1753 | — | — | — | unknown |
| `prospect_engagement_status` | E1223 | E1218, E1704, E1728, E1770 | — | E1224, E1365, E1372 | — | yes |

### `public.client_status`

**Definition:** `migrations/0001_init.sql:274`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1432, E1705, E1749, E1750.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label` | — | E1705, E1749, E1750 | E1432 | — | — | yes |
| `sort` | — | E1432 | — | declared; caller unknown | — | yes |
| `is_active_pipeline` | — | E1432, E1750 | — | declared; caller unknown | — | yes |
| `note` | — | — | — | — | — | unknown |

### `public.client_type`

**Definition:** `migrations/0001_init.sql:269`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1432.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label` | — | — | E1432 | — | — | unknown |

### `public.code_subject`

**Definition:** `migrations/0101_code_review_subject.sql:65`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1103. **Table readers:** E1102, E1103, E1104, E1706, E1793.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1102, E1103, E1104, E1706, E1793 | — | E1103 | — | unknown |
| `repo` | E1103 | E1102, E1104, E1706, E1793 | — | — | — | unknown |
| `commit_sha` | E1103 | E1102, E1103, E1104, E1706, E1793 | — | — | — | unknown |
| `label` | — | E1793 | — | — | — | unknown |
| `note` | — | — | — | — | — | unknown |
| `created_at` | — | — | — | E1103 | — | unknown |
| `created_by` | E1103 | — | — | — | — | unknown |

### `public.codex_continuity_checkpoint`

**Definition:** `migrations/0480_codex_continuity.sql:5`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E816, E817. **Table readers:** E612, E815, E816, E817, E819.
**Unresolved SQL affecting any column:** E813. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E612.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E819 | E815, E816, E817, E819 | E816 | — | unknown |
| `organization_tenant_id` | E816 | E815, E817, E819, E1535 | — | — | — | unknown |
| `owner_actor_id` | E816 | E612, E815, E817, E819, E1535 | — | — | — | unknown |
| `native_task_id` | E816 | E612, E815, E816, E817, E819, E1535 | — | — | — | unknown |
| `project_id`, `cwd` | E816 | E612, E816, E817, E819, E1535 | — | — | — | unknown |
| `state`, `cursor` | E816, E817 | E816, E817 | — | — | — | unknown |
| `checkpoint_version` | E817 | E815, E816, E817 | — | E816 | — | unknown |
| `created_at` | — | E1535, E1536 | — | E816 | E1536 | unknown |
| `updated_at` | E817 | E612, E815, E816, E817, E1536 | — | E816 | E1536 | unknown |
| `reference_manifest` | E816, E817 | E816, E817 | — | declared; caller unknown | — | unknown |

### `public.codex_continuity_event`

**Definition:** `migrations/0480_codex_continuity.sql:35`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E814, E820, E821.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E821 | E814, E820, E821 | declared; caller unknown | — | unknown |
| `organization_tenant_id`, `owner_actor_id`, `native_task_id`, `project_id`, `cwd` | — | E814, E820, E821 | — | — | — | unknown |
| `event_type`, `transcript_ref`, `idempotency_key` | — | E821 | — | — | — | unknown |
| `cursor` | — | E820, E821 | — | — | — | unknown |
| `created_at` | — | E814, E820, E821 | — | declared; caller unknown | — | unknown |

### `public.codex_continuity_revision`

**Definition:** `migrations/0480_codex_continuity.sql:22`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E818. **Table readers:** E818, E819.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E819 | E819 | E818 | — | unknown |
| `checkpoint_id`, `checkpoint_version`, `state`, `cursor` | E818 | E819 | — | — | — | unknown |
| `created_by_actor_id` | E818 | — | — | — | — | unknown |
| `created_at` | — | E819 | — | E818 | — | unknown |
| `reference_manifest` | E818 | E819 | — | declared; caller unknown | — | unknown |

### `public.commission`

**Definition:** `migrations/0001_init.sql:761`. **Product path:** positive static evidence (column detail below).

**Table writers:** E986. **Table readers:** E985, E986, E1729, E1764, E1801.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E985, E986, E1729, E1764 | — | declared; caller unknown | — | yes |
| `deal_id` | — | E985, E1729, E1764, E1801 | — | — | — | yes |
| `gross_amount` | — | E1764, E1801 | — | — | — | unknown |
| `status` | E986 | E985, E1729, E1764, E1801 | — | — | — | yes |
| `invoiced_on` | — | E985, E1764 | — | — | — | unknown |
| `received_on` | E986 | E985, E986, E1729, E1764 | — | — | — | yes |
| `version` | — | E985, E986, E1537, E1764 | — | declared; caller unknown | E1537 | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `created_by` | — | — | — | — | — | unknown |
| `updated_at` | — | E1537 | — | declared; caller unknown | E1537 | unknown |
| `updated_by` | E986 | — | — | — | — | unknown |
| `due_on` | — | E1764 | — | — | — | unknown |

### `public.commission_allocation`

**Definition:** `migrations/0001_init.sql:776`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1784.
**Unresolved SQL affecting any column:** E729. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1510, E1784 | — | declared; caller unknown | — | unknown |
| `commission_id`, `parent_id` | — | E1510 | — | — | — | unknown |
| `actor_id`, `fraction`, `computed_amount` | — | — | — | — | — | unknown |
| `party_id`, `kind` | — | E1784 | — | — | — | unknown |

### `public.comp`

**Definition:** `migrations/0001_init.sql:789`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `is_estimate`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `space_id`, `building_id`, `executed_on`, `term_months`, `rate_amount`, `rate_basis`, `rate_norm_sf_yr`, `ti_amount`, `escalator`, `source`, `source_row`, `created_by` | — | — | — | — | — | unknown |

### `public.contact_state`

**Definition:** `migrations/0046_party_identity.sql:41`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label`, `contactable`, `sort` | — | — | — | — | — | unknown |

### `public.content_piece`

**Definition:** `migrations/0001_init.sql:822`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1338, E1339, E1344. **Table readers:** E1333, E1334, E1335, E1338, E1339, E1340, E1344, E1711, E1715, E1716, E1719, E1789.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1333, E1334, E1335, E1338, E1339, E1340, E1344, E1711, E1715, E1716, E1719, E1789 | — | declared; caller unknown | — | unknown |
| `campaign_id` | E1338, E1339 | E1333, E1334, E1339, E1340, E1789 | — | — | — | unknown |
| `author_id`, `body_path`, `lint_passed` | — | — | — | — | — | unknown |
| `kind` | — | E1711, E1789 | — | — | — | unknown |
| `topic` | — | E1711 | — | — | — | unknown |
| `features` | — | E1715, E1716 | — | declared; caller unknown | — | unknown |
| `status` | E1344 | E1333, E1334, E1344, E1711, E1716, E1719, E1789 | — | declared; caller unknown | — | unknown |
| `version` | — | E1333, E1334, E1335, E1542 | — | declared; caller unknown | E1542 | unknown |
| `created_at` | — | E1335 | — | declared; caller unknown | — | unknown |
| `updated_at` | — | E1542, E1719 | — | declared; caller unknown | E1542 | unknown |
| `updated_by` | E1338, E1339, E1344 | — | — | — | — | unknown |

### `public.critical_date`

**Definition:** `migrations/0001_init.sql:472`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1171. **Table readers:** E712, E1171, E1705, E1730, E1804.
**Additional lexical/dependency evidence:** E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E712, E1171, E1705, E1730, E1804 | — | E1171 | — | yes |
| `deal_id`, `kind`, `due_on` | E1171 | E712, E1705, E1730, E1804 | — | — | — | yes |
| `note` | E1171 | E1705, E1730, E1804 | — | — | — | yes |
| `source` | E1171 | E1730 | — | — | — | yes |
| `status` | — | E712, E1705, E1730, E1804 | — | E1171 | — | yes |
| `version` | — | E1543 | — | E1171 | E1543 | yes |
| `created_at` | — | — | — | E1171 | — | yes |
| `created_by` | E1171 | — | — | — | — | yes |
| `updated_at` | — | E712, E1543 | — | E1171 | E1543 | yes |
| `updated_by` | — | — | — | — | — | unknown |

### `public.deal`

**Definition:** `migrations/0001_init.sql:401`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1128, E1136, E1145, E1165, E1174, E1175, E1180, E1186, E1373. **Table readers:** E224, E232, E380, E395, E712, E727, E971, E973, E975, E978, E1050, E1055, E1116, E1125, E1128, E1132, E1136, E1139, E1140, E1145, E1163, E1165, E1172, E1174, E1175, E1176, E1177, E1179, E1180, E1181, E1182, E1186, E1190, E1229, E1252, E1253, E1264, E1265, E1369, E1373, E1436, E1699, E1700, E1703, E1709, E1721, E1723, E1724, E1728, E1732, E1746, E1749, E1750, E1751, E1762, E1764, E1765, E1774, E1782, E1794, E1795, E1801, E1802.
**Unresolved SQL affecting any column:** E828, E1054, E1141, E1146, E1173, E1174, E1428, E1429, E1434. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E224, E232, E380, E395, E712, E727.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E224, E232, E380, E712, E727, E971, E973, E975, E978, E1050, E1055, E1116, E1125, E1128, E1132, E1136, E1139, E1140, E1145, E1163, E1165, E1172, E1175, E1177, E1179, E1180, E1181, E1182, E1186, E1190, E1229, E1252, E1253, E1264, E1265, E1369, E1373, E1699, E1700, E1709, E1721, E1723, E1724, E1728, E1732, E1749, E1750, E1751, E1764, E1765, E1782, E1794, E1795, E1801, E1802 | E1132, E1436 | E1175, E1373 | — | yes |
| `client_id` | E1175, E1180, E1373 | E380, E978, E1050, E1055, E1116, E1179, E1190, E1229, E1252, E1253, E1264, E1265, E1703, E1709, E1721, E1723, E1728, E1749, E1750, E1751, E1764, E1765, E1774, E1794, E1795, E1801 | E1436, E1765, E1801 | — | — | yes |
| `name` | E1175, E1373 | E380, E712, E727, E978, E1116, E1176, E1190, E1253, E1369, E1699, E1721, E1723, E1724, E1728, E1732, E1751, E1764, E1782, E1794, E1802 | E1436 | — | — | yes |
| `salesforce_id` | E1174, E1175 | E224, E232, E1174, E1176, E1436, E1709, E1724, E1751 | — | — | — | yes |
| `deal_type` | E1174, E1175, E1373 | E1116, E1174, E1721, E1723, E1728, E1732, E1751, E1782 | — | — | — | yes |
| `phase` | E1174, E1175, E1373 | E224, E232, E975, E978, E1116, E1174, E1700, E1721, E1723, E1724, E1728, E1732, E1746, E1749, E1750, E1751, E1764, E1782, E1794, E1802 | — | — | — | yes |
| `segment` | E1174, E1175, E1373 | E1116, E1174, E1723, E1728, E1732, E1751 | — | — | — | yes |
| `outcome` | E1174 | E224, E232, E727, E1050, E1174, E1253, E1369, E1700, E1721, E1723, E1724, E1728, E1746, E1749, E1750, E1751, E1764, E1801, E1802 | — | — | — | yes |
| `closed_on` | E1174 | E224, E232, E1174, E1721, E1723, E1724, E1728, E1751, E1764 | — | — | — | yes |
| `won_value` | E1174 | E1174, E1801 | — | — | — | yes |
| `sf_commission_placeholder` | E1175 | E1751 | — | — | — | yes |
| `sf_close_date_placeholder` | E1174, E1175 | E1174, E1751 | — | — | — | yes |
| `notes_path` | E1174, E1175 | E1174, E1723, E1751 | — | — | — | yes |
| `source_row` | — | E1751 | — | — | — | unknown |
| `version` | — | E971, E973, E975, E978, E1172, E1177, E1181, E1545, E1724 | — | E1175, E1373 | E1545 | yes |
| `created_at` | — | E1172, E1177, E1181, E1253, E1709, E1749, E1750 | E1436, E1762 | E1175, E1373 | — | yes |
| `created_by` | E1175, E1373 | — | — | — | — | yes |
| `updated_at` | — | E1545 | E1132 | E1175, E1373 | E1545 | yes |
| `updated_by` | E1128, E1136, E1145, E1165, E1174, E1175, E1180, E1186, E1373 | E1174 | — | — | — | yes |
| `lane` | E1174, E1175, E1373 | E1174, E1723, E1724, E1728, E1751, E1764 | E1801 | — | — | yes |
| `city` | E1174, E1175, E1373 | E1174, E1728, E1732, E1751 | E1436 | — | — | yes |
| `owner` | E1186, E1373 | E727, E1132, E1182, E1728, E1732, E1764 | — | — | — | yes |
| `attention` | — | E1728, E1732 | — | E1175, E1373 | — | yes |
| `next_date` | E1128, E1136, E1165 | E1125, E1132, E1140, E1163, E1728, E1732 | — | — | — | yes |
| `operating_state` | E1145 | E727, E1139, E1728, E1732 | — | E1175, E1373 | — | yes |
| `parking_reason`, `parking_note` | E1145 | E1139, E1728, E1732 | — | — | — | yes |
| `parked_at` | E1145 | E1728, E1732 | — | — | — | yes |
| `parked_by` | E1145 | E1728 | — | — | — | yes |
| `invoiced_on` | E1174 | E975, E978, E1174, E1723, E1724, E1728, E1764 | — | — | — | yes |

### `public.deal_conflict`

**Definition:** `migrations/0079_deal_room_api.sql:37`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1349, E1379. **Table readers:** E1349, E1378, E1379.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1349, E1378, E1379 | — | E1349 | — | unknown |
| `deal_id`, `field`, `value_a`, `event_a`, `value_b` | E1349 | E1378 | — | — | — | unknown |
| `actor_a`, `actor_b` | E1349 | — | — | — | — | unknown |
| `status` | E1379 | E1349, E1378 | — | E1349 | — | unknown |
| `resolved_by`, `winner`, `resolved_at` | E1379 | — | — | — | — | unknown |
| `created_at` | — | — | — | E1349 | — | unknown |

### `public.deal_invoice_email`

**Definition:** `migrations/0843_automation_reason_undo_archive_invoice.sql:26`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E972, E977, E984. **Table readers:** E972, E973, E974, E976, E977, E979, E984.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E972, E973, E974, E976, E977, E979, E984 | — | E972 | — | unknown |
| `evidence_ref`, `occurred_at`, `email_date` | E972 | E973, E974, E979 | — | — | — | unknown |
| `from_address`, `deal_name`, `client_name`, `property_address`, `created_by` | E972 | E974, E979 | — | — | — | unknown |
| `status` | E977, E984 | E973, E974, E976, E979 | — | E972 | — | unknown |
| `deal_id`, `prior_phase`, `applied_at`, `applied_by`, `reason` | E984 | E973, E974, E979 | — | — | — | unknown |
| `prior_invoiced_on`, `phase_event_id`, `invoice_event_id` | E984 | E974, E979 | — | — | — | unknown |
| `undone_at`, `undone_by` | E977 | E973, E974, E979 | — | — | — | unknown |

### `public.deal_lane`

**Definition:** `migrations/0061_national_account.sql:162`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label`, `sort`, `note` | — | — | — | — | — | unknown |

### `public.deal_market_assignment`

**Definition:** `migrations/0090_deal_room_workspaces.sql:19`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1359, E1375. **Table readers:** E1358, E1359, E1375, E1728.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `deal_id` | E1359, E1375 | E1358, E1728 | — | — | — | yes |
| `agent_name` | E1359, E1375 | E1358, E1359, E1728 | — | — | — | yes |
| `agent_party_id` | E1359 | E1359, E1728 | — | — | — | yes |
| `market` | E1359, E1375 | E1358, E1359 | — | — | — | unknown |
| `source`, `set_by` | E1359, E1375 | E1359 | — | — | — | unknown |
| `set_at` | E1359 | — | — | E1359, E1375 | — | unknown |

### `public.deal_note`

**Definition:** `migrations/0079_deal_room_api.sql:27`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1127, E1350. **Table readers:** E1126, E1127, E1350, E1728, E1736.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1126, E1127, E1350, E1728, E1736 | — | E1127, E1350 | — | yes |
| `deal_id`, `kind`, `text` | E1127, E1350 | E1126, E1728, E1736 | — | — | — | yes |
| `actor_id` | E1127, E1350 | E1736 | — | — | — | yes |
| `created_at` | E1127 | E1126, E1127, E1350, E1728, E1736 | — | E1350 | — | yes |

### `public.deal_participant`

**Definition:** `migrations/0001_init.sql:433`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1142, E1144, E1184, E1185, E1200, E1374. **Table readers:** E380, E727, E1117, E1118, E1142, E1144, E1184, E1185, E1199, E1200, E1264, E1265, E1374, E1436, E1709, E1720, E1721, E1723, E1737, E1751, E1782, E1784, E1795.
**Additional lexical/dependency evidence:** E380, E727.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1720, E1737, E1784 | E1436 | E1144, E1185, E1200, E1374 | — | yes |
| `deal_id` | E1144, E1185, E1200, E1374 | E380, E727, E1117, E1118, E1142, E1184, E1199, E1436, E1709, E1720, E1721, E1723, E1737, E1751, E1782, E1795 | — | — | — | yes |
| `actor_id` | E1144, E1185, E1374 | E380, E1117, E1184, E1544, E1720, E1723, E1737, E1751, E1795 | — | — | — | yes |
| `party_id` | E1200 | E727, E1118, E1199, E1264, E1544, E1709, E1720, E1721, E1737, E1782, E1784 | E1265, E1436 | — | — | yes |
| `role` | E1144, E1185, E1200, E1374 | E380, E727, E1117, E1118, E1142, E1184, E1199, E1544, E1720, E1721, E1723, E1737, E1751, E1782, E1784, E1795 | — | — | — | yes |
| `from_at` | — | E1720 | — | E1144, E1185, E1200, E1374 | — | yes |
| `to_at` | E1142, E1184 | E380, E727, E1117, E1118, E1142, E1184, E1199, E1720, E1723, E1737, E1751, E1782, E1795 | — | — | — | yes |
| `set_by` | E1144, E1185, E1200, E1374 | E1720 | — | — | — | yes |

### `public.deal_phase`

**Definition:** `migrations/0001_init.sql:395`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1723, E1749, E1750, E1751.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug` | — | E1723, E1749, E1750, E1751 | — | — | — | yes |
| `label` | — | E1749, E1750, E1751 | — | — | — | unknown |
| `sort` | — | E1723 | — | declared; caller unknown | — | yes |

### `public.deal_presence_lease`

**Definition:** `migrations/0079_deal_room_api.sql:18`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1348. **Table readers:** E1348, E1741.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `actor_id`, `deal_id`, `field` | E1348 | E1741 | — | — | — | yes |
| `expires_at` | E1348 | E1348, E1741 | — | — | — | yes |

### `public.deal_reattach_log`

**Definition:** `migrations/0061_national_account.sql:218`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `deal_id`, `from_client`, `to_client`, `from_segment`, `reason` | — | — | — | — | — | unknown |
| `moved_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.deal_review_item`

**Definition:** `migrations/0090_deal_room_workspaces.sql:52`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1355. **Table readers:** E1355, E1357, E1728, E1742.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `session_id` | E1355 | E1357, E1728, E1742 | — | — | — | yes |
| `deal_id` | E1355 | E1728, E1742 | — | — | — | yes |
| `disposition` | E1355 | E1355, E1357, E1728, E1742 | — | — | — | yes |
| `note` | E1355 | E1355 | — | — | — | unknown |
| `reviewed_at` | E1355 | E1728 | — | E1355 | — | yes |

### `public.deal_review_session`

**Definition:** `migrations/0090_deal_room_workspaces.sql:33`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1353, E1356. **Table readers:** E1352, E1353, E1354, E1356, E1725, E1728, E1742.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1352, E1353, E1354, E1356, E1728, E1742 | — | E1353 | — | yes |
| `workspace_kind`, `started_by` | E1353 | E1352, E1354, E1356, E1742 | — | — | — | yes |
| `account_client_id` | E1353 | E1352, E1354, E1356, E1725, E1742 | — | — | — | yes |
| `started_at` | — | E1353, E1354, E1742 | — | E1353 | — | yes |
| `ended_at` | E1356 | E1354, E1356, E1725, E1742 | — | — | — | yes |
| `status` | E1356 | E1352, E1354, E1356, E1725, E1728, E1742 | — | E1353 | — | yes |
| `summary` | E1356 | E1354, E1742 | — | — | — | yes |

### `public.deal_type_ref`

**Definition:** `migrations/0017_vocab_ref_tables.sql:20`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label`, `sort` | — | — | — | — | — | unknown |

### `public.defect`

**Definition:** `migrations/0103_defect_log.sql:35`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1086. **Table readers:** E112, E415, E928, E1085, E1086, E1744, E1745.
**Additional lexical/dependency evidence:** E112, E415.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E112, E415, E928, E1085, E1086, E1744 | E112 | E1086 | — | yes |
| `occurred_on` | E1086 | E112, E415, E928, E1085, E1086, E1744, E1745 | — | declared; caller unknown | — | yes |
| `defect_class` | E1086 | E112, E415, E928, E1085, E1744, E1745 | — | — | — | yes |
| `claimed` | E1086 | E112, E415, E928, E1085, E1744 | — | — | — | yes |
| `actual` | E1086 | E112, E415, E928, E1744 | — | — | — | yes |
| `source_unread`, `rule_violated`, `detected_by` | E1086 | E112, E415, E1744, E1745 | — | — | — | yes |
| `session_key`, `cost_note`, `created_by` | E1086 | E112, E415, E1744 | — | — | — | yes |
| `created_at` | — | E112, E415, E1085, E1744 | — | E1086 | — | yes |

### `public.defect_resolution`

**Definition:** `migrations/0872_self_closing_records.sql:66`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E928, E1744.
**Unresolved SQL affecting any column:** E715, E716. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E928 | — | declared; caller unknown | — | unknown |
| `defect_id`, `evidence_kind`, `evidence_ref` | — | E928, E1546, E1744 | — | — | — | unknown |
| `note`, `idempotency_key` | — | — | — | — | — | unknown |
| `resolved_by` | — | E1744 | — | — | — | unknown |
| `created_at` | — | E1744 | — | declared; caller unknown | — | unknown |

### `public.deprecation`

**Definition:** `migrations/0049_deprecation_register.sql:26`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1672.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `object_name`, `object_kind`, `safe_to_drop_after`, `dropped_at` | — | E1672 | — | — | — | unknown |
| `replaced_by`, `reason` | — | — | — | — | — | unknown |
| `deprecated_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.diagnostic_route`

**Definition:** `migrations/0098_investigation_control_plane.sql:39`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E957, E958, E959, E963, E966.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `route_key` | — | E957, E958, E959, E963, E966 | — | — | — | unknown |
| `signal_kind` | — | E957, E959 | — | — | — | unknown |
| `from_kind`, `relation`, `test_verb` | — | E957, E959, E966 | — | — | — | unknown |
| `to_kind` | — | E957, E958, E959, E966 | — | — | — | unknown |
| `input_contract`, `active` | — | E957, E959 | — | declared; caller unknown | — | unknown |
| `minimum_effect` | — | E957, E959, E963 | — | — | — | unknown |
| `created_by` | — | E959 | — | — | — | unknown |
| `created_at` | — | E959 | — | declared; caller unknown | — | unknown |

### `public.doc_template`

**Definition:** `migrations/0001_init.sql:894`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1249, E1250. **Table readers:** E1248, E1249, E1250, E1251, E1256.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1248, E1249, E1250, E1251, E1256 | — | E1250 | — | yes |
| `slug` | E1250 | E1248, E1251 | — | — | — | yes |
| `name` | E1249, E1250 | E1251 | — | — | — | yes |
| `source_path` | E1249, E1250 | E1251, E1256 | — | — | — | yes |
| `template_version` | E1249, E1250 | E1248, E1251 | — | — | — | yes |
| `field_map` | E1249, E1250 | E1248, E1251, E1256 | — | — | — | yes |
| `output_kinds` | E1249, E1250 | E1251 | — | declared; caller unknown | — | yes |
| `active` | E1249 | E1251 | — | E1250 | — | yes |
| `created_at` | — | E1251 | — | E1250 | — | yes |
| `created_by` | E1250 | E1251 | — | — | — | yes |

### `public.doctrine_change_item`

**Definition:** `migrations/0075_doctrine_store.sql:105`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E880. **Table readers:** E880.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `change_set_id`, `section_id`, `expected_version`, `proposed_body` | E880 | — | — | — | — | unknown |
| `op` | E880 | — | — | declared; caller unknown | — | unknown |

### `public.doctrine_change_set`

**Definition:** `migrations/0075_doctrine_store.sql:88`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E852, E874, E883. **Table readers:** E852, E874, E883.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E852, E874, E883 | — | E852, E874 | — | unknown |
| `actor_id`, `session_key`, `idempotency_key` | E852, E874 | — | — | — | — | unknown |
| `state` | E852, E874, E883 | — | — | declared; caller unknown | — | unknown |
| `gate_run_id` | — | — | — | — | — | unknown |
| `created_at` | — | — | — | E852, E874 | — | unknown |
| `committed_at` | E852, E883 | — | — | — | — | unknown |

### `public.doctrine_claim`

**Definition:** `migrations/0075_doctrine_store.sql:239`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E871, E873. **Table readers:** E837, E870, E871, E873.
**Row deletion:** E873.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `section_id`, `holder_actor_id` | E871 | E837, E870, E873 | — | — | — | unknown |
| `holder_session_key`, `expires_at` | E871 | E837, E870 | — | — | — | unknown |
| `purpose` | E871 | — | — | — | — | unknown |
| `created_at` | E871 | — | — | E871 | — | unknown |

### `public.doctrine_concept_mapping`

**Definition:** `migrations/0135_situation_retrieval.sql:92`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E732, E737, E740. **Table readers:** E39, E463, E732, E737, E740, E742, E1676, E1797, E1798.
**Unresolved SQL affecting any column:** E12. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E39, E463, E732, E737, E740, E742.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E463, E737, E740, E742, E1676, E1798 | E39 | E737 | — | unknown |
| `concept_id` | E737 | E39, E463, E740, E742, E1676, E1797, E1798 | E742 | — | — | unknown |
| `section_id` | E737 | E39, E463, E732, E742, E1676, E1797, E1798 | — | — | — | unknown |
| `role`, `rationale`, `proposer_id`, `approver_id`, `approved_at` | E737 | E463, E742, E1798 | — | — | — | unknown |
| `weight` | E737 | E463, E742, E1798 | — | declared; caller unknown | — | unknown |
| `status` | E732, E737, E740 | E39, E463, E732, E740, E742, E1676, E1797, E1798 | E39, E742 | declared; caller unknown | — | unknown |
| `version` | E732, E740 | E463, E732, E740, E742, E1798 | — | E737 | — | unknown |
| `review_after` | — | E463, E742, E1798 | — | — | — | unknown |
| `retired_at` | E740 | E463, E742, E1798 | — | — | — | unknown |
| `repair_reason` | E732 | E463, E742, E1798 | — | — | — | unknown |
| `created_at` | — | E463, E742, E1798 | — | E737 | — | unknown |
| `updated_at` | E732, E740 | E463, E742, E1798 | — | E737 | — | unknown |

### `public.doctrine_document`

**Definition:** `migrations/0075_doctrine_store.sql:31`. **Product path:** positive static evidence (column detail below).

**Table writers:** E847. **Table readers:** E3, E91, E126, E300, E329, E332, E463, E637, E713, E728, E737, E749, E833, E836, E847, E855, E863, E866, E867, E868, E872, E875, E884, E885, E1415, E1444, E1669, E1798.
**Unresolved SQL affecting any column:** E1419, E1441. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E3, E91, E126, E300, E329, E332, E463, E637, E713, E728, E737.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E3, E91, E126, E300, E329, E332, E637, E713, E728, E737, E749, E833, E847, E855, E863, E866, E867, E868, E872, E875, E884, E885, E1415, E1669, E1798 | — | E847 | — | yes |
| `slug` | E847 | E3, E91, E126, E329, E332, E637, E728, E737, E749, E833, E836, E863, E866, E867, E868, E872, E884, E885, E1669, E1798 | — | — | — | yes |
| `title` | E847 | E91, E728, E749, E833, E867, E868, E872 | — | — | — | yes |
| `content_class` | E847 | E91, E728, E749, E833, E866, E867, E868, E872, E884, E885 | — | — | — | yes |
| `visibility` | E847 | E3, E91, E126, E300, E332, E713, E728, E749, E833, E866, E867, E868, E872, E884, E885, E1415 | — | declared; caller unknown | — | yes |
| `owner_actor_id` | E847 | E91, E728, E866, E867, E868, E872, E884, E885 | — | — | — | yes |
| `review_policy_id` | E847 | E91, E855, E868, E872, E875 | — | — | — | yes |
| `created_by` | E847 | E91, E868, E872 | — | — | — | yes |
| `created_at` | — | E91, E868, E872 | — | E847 | — | yes |
| `updated_at` | — | E91, E867, E868, E872 | — | E847 | — | yes |
| `title_search_vector` | — | E91, E728, E868, E872 | — | — | — | yes |

### `public.doctrine_edge`

**Definition:** `migrations/0075_doctrine_store.sql:179`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E858, E859. **Table readers:** E463, E766, E833, E841, E842, E858, E859, E886.
**Additional lexical/dependency evidence:** E463.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E766, E841 | — | E859 | — | unknown |
| `source_section_id` | E859 | E766, E833, E841, E842, E858, E886 | — | — | — | unknown |
| `target_section_id`, `edge_type` | E859 | E766, E833, E841, E842, E886 | — | — | — | unknown |
| `scope` | E859 | E833, E886 | — | declared; caller unknown | — | unknown |
| `introduced_by_revision_id` | E859 | — | — | — | — | unknown |
| `retired_by_revision_id` | E858 | E766, E833, E841, E842, E858, E886 | — | — | — | unknown |
| `created_at` | — | E766 | — | E859 | — | unknown |

### `public.doctrine_edge_type`

**Definition:** `migrations/0075_doctrine_store.sql:164`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E463, E766, E839, E840.
**Additional lexical/dependency evidence:** E463.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `edge_type` | — | E766, E839, E840 | — | — | — | unknown |
| `acyclic` | — | E766, E840 | — | — | — | unknown |
| `precedence_rank`, `description` | — | — | — | — | — | unknown |

### `public.doctrine_gate_check`

**Definition:** `migrations/0075_doctrine_store.sql:255`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E463, E843.
**Additional lexical/dependency evidence:** E463.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `check_key`, `severity`, `impl_key` | — | E843 | — | — | — | unknown |
| `description` | — | — | — | — | — | unknown |
| `applies_to`, `config`, `enabled` | — | E843 | — | declared; caller unknown | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.doctrine_gate_finding`

**Definition:** `migrations/0075_doctrine_store.sql:307`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E845, E1014. **Table readers:** E845, E1014, E1670.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `run_id`, `check_key`, `passed` | E845, E1014 | E1670 | — | — | — | unknown |
| `severity`, `message` | E845, E1014 | — | — | — | — | unknown |
| `path` | E845, E1014 | — | — | declared; caller unknown | — | unknown |

### `public.doctrine_gate_run`

**Definition:** `migrations/0075_doctrine_store.sql:296`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E844, E1014. **Table readers:** E844, E1014, E1670.
**Unresolved SQL affecting any column:** E1440. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | E1014 | E844, E1014, E1670 | — | E844 | — | unknown |
| `change_set_id` | E844 | — | — | — | — | unknown |
| `dry_run` | E844 | — | — | E1014 | — | unknown |
| `actor_id`, `finished_at`, `result` | E844, E1014 | — | — | — | — | unknown |
| `started_at` | — | E1670 | — | E844, E1014 | — | unknown |
| `report` | E844, E1014 | — | — | declared; caller unknown | — | unknown |

### `public.doctrine_link`

**Definition:** `migrations/0075_doctrine_store.sql:150`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E856, E857. **Table readers:** E463, E767, E856, E857.
**Row deletion:** E856.
**Additional lexical/dependency evidence:** E463.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E767 | — | E857 | — | unknown |
| `source_section_id` | E857 | E767, E856 | — | — | — | unknown |
| `target_kind`, `target_id` | E857 | E767 | — | — | — | unknown |
| `role` | E857 | E767 | — | declared; caller unknown | — | unknown |

### `public.doctrine_meta`

**Definition:** `migrations/0075_doctrine_store.sql:217`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E832. **Table readers:** E590, E829, E832, E835, E862, E864, E1057.
**Unresolved SQL affecting any column:** E1443. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E590.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E590, E832, E835, E862, E864, E1057 | E829 | — | — | unknown |
| `generation` | E832 | E590, E829, E832, E835, E862, E864, E1057 | — | declared; caller unknown | — | unknown |
| `updated_at` | E832 | — | — | declared; caller unknown | — | unknown |

### `public.doctrine_migration_batch`

**Definition:** `migrations/0075_doctrine_store.sql:322`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E747, E748.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `source_hashes` | — | — | — | declared; caller unknown | — | unknown |
| `batch_no`, `phase`, `row_counts`, `error`, `started_at`, `finished_at` | — | — | — | — | — | unknown |
| `source_paths` | — | E747, E748 | — | — | — | unknown |
| `state` | — | E747 | — | declared; caller unknown | — | unknown |

### `public.doctrine_review_policy`

**Definition:** `migrations/0075_doctrine_store.sql:196`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E463, E846, E855, E875.
**Additional lexical/dependency evidence:** E463.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E846, E855, E875 | — | declared; caller unknown | — | unknown |
| `name`, `content_classes` | — | E846 | — | — | — | unknown |
| `max_age_days` | — | E855, E875 | — | — | — | unknown |
| `revalidate_on_dep_change` | — | — | — | declared; caller unknown | — | unknown |

### `public.doctrine_revision`

**Definition:** `migrations/0075_doctrine_store.sql:115`. **Product path:** positive static evidence (column detail below).

**Table writers:** E853, E881. **Table readers:** E3, E91, E112, E126, E329, E332, E415, E463, E637, E650, E728, E749, E833, E853, E863, E866, E881, E888.
**Unresolved SQL affecting any column:** E91. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E3, E112, E126, E329, E332, E415, E463, E637, E650, E728.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E3, E91, E112, E126, E329, E332, E415, E637, E650, E728, E749, E833, E853, E863, E866, E881 | E112 | E853, E881 | — | yes |
| `section_id` | E853, E881 | E3, E91, E112, E126, E329, E332, E650, E888 | — | — | — | yes |
| `version`, `actor_id`, `session_key` | E853, E881 | E112, E888 | — | — | — | yes |
| `parent_revision_id`, `change_set_id`, `commit_message` | E853, E881 | E112 | — | — | — | yes |
| `body` | E853, E881 | E3, E112, E126, E332, E637, E833, E863, E866 | — | — | — | yes |
| `plain_text` | E853, E881 | E3, E112, E126, E329, E332, E415, E728, E749 | — | — | — | yes |
| `content_hash` | E853, E881 | E3, E112, E126, E329, E332, E415, E650, E833, E866 | — | — | — | yes |
| `created_at` | — | E112, E888 | — | E853, E881 | — | yes |
| `search_vector` | — | E112, E728 | — | — | — | yes |

### `public.doctrine_section`

**Definition:** `migrations/0075_doctrine_store.sql:63`. **Product path:** positive static evidence (column detail below).

**Table writers:** E850, E851, E854, E860, E878, E879, E882. **Table readers:** E3, E39, E91, E126, E300, E329, E332, E463, E637, E713, E728, E737, E749, E823, E833, E838, E848, E849, E850, E851, E854, E860, E863, E866, E867, E876, E877, E878, E879, E882, E884, E885, E887, E1415, E1669, E1676, E1797, E1798.
**Unresolved SQL affecting any column:** E1416, E1419, E1441, E1442. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E3, E39, E91, E126, E300, E329, E332, E463, E637, E713, E728, E737.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E3, E39, E91, E126, E300, E329, E332, E713, E728, E737, E823, E833, E838, E848, E850, E851, E854, E860, E866, E867, E876, E878, E879, E882, E884, E885, E887, E1415, E1550, E1669, E1676, E1797, E1798 | E39 | E850, E878 | — | yes |
| `document_id` | E850, E878 | E3, E91, E126, E300, E329, E332, E637, E713, E728, E737, E749, E833, E848, E849, E850, E863, E866, E867, E876, E877, E878, E884, E885, E1415, E1669, E1676, E1798 | — | — | — | yes |
| `section_key` | E850, E878 | E3, E91, E126, E329, E332, E637, E713, E728, E737, E749, E833, E848, E850, E863, E866, E876, E878, E884, E885, E1676, E1798 | — | — | — | yes |
| `title` | E850, E851, E878, E879 | E91, E126, E713, E728, E749, E833, E848, E850, E863, E866, E876, E878, E884, E885 | — | — | — | yes |
| `ordinal` | E850, E878 | E91, E749, E833, E848, E849, E850, E866, E876, E877, E878, E884 | — | declared; caller unknown | — | yes |
| `status` | E860 | E3, E39, E91, E126, E329, E332, E637, E713, E728, E737, E749, E823, E833, E838, E848, E850, E863, E866, E867, E876, E878, E884, E885, E887, E1415, E1550, E1669, E1676, E1797, E1798 | E39 | E850, E878 | — | yes |
| `current_revision_id` | E854, E882 | E3, E91, E126, E329, E332, E637, E713, E728, E749, E833, E848, E850, E863, E866, E876, E878, E884, E1415, E1797, E1798 | — | — | — | yes |
| `current_version` | E854, E860, E882 | E91, E833, E848, E850, E860, E863, E866, E876, E878, E884, E885 | — | E850, E878 | — | yes |
| `body_hash` | E854, E882 | E91, E823, E833, E848, E850, E876, E878, E884 | — | — | — | yes |
| `review_after` | E854, E882 | E91, E833, E848, E850, E854, E866, E867, E876, E878, E882, E884, E1669 | — | — | — | yes |
| `updated_at` | E854, E860, E882 | E91, E848, E850, E876, E878, E884 | — | E850, E878 | — | yes |
| `title_search_vector` | — | E91, E728, E848, E850, E876, E878, E884 | — | — | — | yes |

### `public.doctrine_slug_alias`

**Definition:** `migrations/0075_doctrine_store.sql:53`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E463, E836, E872.
**Additional lexical/dependency evidence:** E463.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `alias_slug` | — | E836, E872 | — | — | — | unknown |
| `document_id` | — | E872 | — | — | — | unknown |
| `replaced_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.doctrine_snapshot`

**Definition:** `migrations/0075_doctrine_store.sql:227`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E833. **Table readers:** E463, E833, E861.
**Additional lexical/dependency evidence:** E463.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `document_id` | E833 | E861 | — | — | — | unknown |
| `generation`, `snapshot_json`, `content_hash` | E833 | E833, E861 | — | — | — | unknown |
| `built_at` | E833 | E833, E861 | — | declared; caller unknown | — | unknown |

### `public.document`

**Definition:** `migrations/0001_init.sql:911`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1254. **Table readers:** E1254, E1255, E1256, E1733.
**Unresolved SQL affecting any column:** E1258. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1254, E1255, E1256, E1733 | — | E1254 | — | yes |
| `template_id` | E1254 | E1255, E1256 | — | — | — | yes |
| `deal_id`, `note` | E1254 | E1255, E1733 | — | — | — | yes |
| `client_id`, `prepared_by` | E1254 | E1255 | — | — | — | yes |
| `prepared_at` | — | E1254, E1255, E1733 | — | E1254 | — | yes |
| `working_attachment`, `pdf_attachment` | — | E1255 | — | — | — | yes |
| `lint_passed`, `leak_check_passed` | — | E1255, E1733 | — | — | — | yes |
| `sent_status` | E1254 | E1255, E1733 | — | declared; caller unknown | — | yes |

### `public.event`

**Definition:** `migrations/0001_init.sql:154`. **Product path:** positive static evidence (column detail below).

**Table writers:** E157, E191, E618, E622, E630, E631, E634, E1170, E1303, E1306, E1308. **Table readers:** E34, E82, E157, E191, E278, E280, E445, E618, E619, E622, E623, E624, E625, E628, E630, E631, E634, E646, E712, E905, E980, E987, E1005, E1006, E1087, E1124, E1137, E1138, E1147, E1170, E1294, E1303, E1305, E1306, E1307, E1308, E1376, E1377, E1728, E1734, E1738, E1739, E1743, E1759, E1762, E1771, E1788, E1803.
**Unresolved SQL affecting any column:** E450, E627, E659, E825, E827, E828. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E34, E82, E157, E191, E278, E280, E445, E618, E619, E622, E623, E624, E625, E628, E630, E631, E634, E646, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E34, E82, E157, E191, E278, E445, E619, E623, E624, E625, E628, E630, E712, E980, E987, E1005, E1006, E1137, E1138, E1147, E1303, E1305, E1306, E1307, E1308, E1376, E1377, E1573, E1734, E1738, E1743, E1771, E1788, E1803 | E280, E628 | E157, E191, E618, E622, E630, E631, E634, E1170, E1303 | — | yes |
| `occurred_at` | E157, E191, E618, E622, E630, E631, E634, E1170, E1303 | E82, E157, E278, E445, E619, E623, E624, E628, E630, E646, E1006, E1738, E1739, E1743, E1759, E1771, E1788, E1803 | — | — | — | yes |
| `recorded_at` | E1170 | E34, E157, E278, E619, E624, E628, E630, E712, E905, E980, E987, E1005, E1006, E1087, E1124, E1138, E1147, E1307, E1377, E1728, E1734, E1738, E1759, E1803 | E1762 | E157, E191, E618, E622, E630, E631, E634, E1303 | — | yes |
| `actor_id` | E157, E191, E618, E622, E630, E631, E634, E1170, E1303 | E157, E278, E619, E623, E624, E625, E628, E630, E634, E905, E1006, E1087, E1138, E1734, E1743, E1759, E1771, E1788, E1803 | — | — | — | yes |
| `verb` | E157, E191, E618, E622, E630, E631, E634, E1170, E1303 | E34, E82, E157, E278, E445, E619, E623, E624, E625, E628, E630, E712, E905, E1006, E1087, E1124, E1305, E1573, E1618, E1734, E1738, E1759, E1788, E1803 | E1762 | — | E1573, E1618 | yes |
| `subject_type` | E157, E191, E618, E622, E630, E631, E634, E1170, E1303 | E34, E82, E157, E278, E445, E619, E624, E628, E630, E712, E905, E980, E987, E1005, E1137, E1138, E1147, E1294, E1307, E1376, E1377, E1573, E1728, E1734, E1738, E1739, E1759, E1771, E1788, E1803 | — | — | E1573 | yes |
| `subject_id` | E157, E191, E618, E622, E630, E631, E634, E1170, E1303 | E34, E82, E157, E278, E445, E619, E624, E628, E630, E646, E712, E905, E980, E987, E1005, E1006, E1087, E1124, E1137, E1138, E1147, E1294, E1305, E1307, E1376, E1377, E1728, E1734, E1738, E1739, E1743, E1759, E1771, E1788, E1803 | — | — | — | yes |
| `field` | E191, E618, E622, E630, E631, E634, E1170 | E157, E278, E619, E624, E628, E630, E712, E980, E987, E1005, E1087, E1137, E1138, E1147, E1294, E1307, E1376, E1377, E1728, E1734, E1738, E1739, E1759, E1788, E1803 | — | — | — | yes |
| `old_value` | E618, E622, E630, E631, E634, E1170 | E157, E278, E619, E624, E628, E630, E905, E1006, E1087, E1376, E1734, E1738, E1739, E1759, E1771, E1788 | — | — | — | yes |
| `new_value` | E157, E191, E618, E622, E630, E631, E634, E1170, E1303, E1306, E1308 | E34, E82, E157, E278, E619, E623, E624, E628, E630, E712, E905, E987, E1005, E1006, E1087, E1124, E1138, E1294, E1305, E1307, E1376, E1618, E1728, E1734, E1738, E1739, E1743, E1759, E1771, E1788, E1803 | — | — | — | yes |
| `cause` | E157, E191, E618, E622, E630, E631, E634, E1170, E1303 | E157, E278, E619, E624, E628, E630, E987, E1006, E1738, E1743, E1759, E1771 | — | — | — | yes |
| `human_quote` | E1170, E1303, E1306 | E82, E157, E278, E445, E619, E624, E628, E630, E1305, E1743, E1759, E1803 | — | — | — | yes |
| `agent_rationale` | E191, E618, E622, E630, E631, E634, E1170, E1303, E1306 | E157, E278, E619, E624, E628, E630, E1305, E1738, E1743, E1771, E1788 | — | — | — | yes |
| `idempotency_key` | E191, E618, E622, E630, E631, E634, E1170, E1303 | E157, E278, E619, E624, E628, E630, E1147, E1771, E1788 | — | — | — | yes |
| `via`, `client_id` | E1170, E1303 | E157, E278, E619, E624, E628, E630, E1738 | — | — | — | yes |
| `sponsoring_human_slug` | E1170, E1303 | E82, E157, E278, E445, E619, E624, E628, E630 | — | — | — | unknown |
| `personal_scope` | E1170, E1303 | E157, E278, E619, E624, E628, E630, E1376 | — | — | — | unknown |
| `authorization_class` | E1170, E1303 | E82, E157, E278, E619, E624, E628, E630 | — | — | — | unknown |
| `organization_tenant_id` | E1170, E1303 | E82, E157, E278, E619, E624, E628, E630, E905, E1376 | — | — | — | unknown |
| `correlation_id` | E1170, E1303 | E157, E278, E619, E624, E628, E630 | — | — | — | unknown |
| `mutation_order` | — | E157, E278, E619, E624, E628, E630, E1771, E1788 | — | — | — | yes |

### `public.experiment`

**Definition:** `migrations/0001_init.sql:876`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | declared; caller unknown | — | unknown |
| `hypothesis`, `piece_ids`, `started_on`, `verdict`, `verdict_note`, `decided_on` | — | — | — | — | — | unknown |

### `public.export_run`

**Definition:** `migrations/0001_init.sql:991`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1762.
**Unresolved SQL affecting any column:** E1439, E1446. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | declared; caller unknown | — | unknown |
| `target` | — | E1762 | — | — | — | yes |
| `ran_at` | — | E1762 | — | declared; caller unknown | — | yes |
| `row_count`, `checksum`, `file_sha` | — | — | — | — | — | unknown |
| `status` | — | — | E1762 | — | — | unknown |

### `public.feature_switch`

**Definition:** `migrations/0902_feature_switches.sql:3`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E721. **Table readers:** E721, E904, E906, E907, E908.
**Additional lexical/dependency evidence:** E721.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E721, E904, E906, E907, E908 | — | E721 | — | unknown |
| `organization_tenant_id`, `name`, `description`, `default_enabled`, `enabled`, `audience`, `owner`, `expected_removal_on`, `retired_at`, `retirement_loop_id` | E721 | E721, E904, E906, E907, E908 | — | — | — | unknown |
| `version` | E721 | E721, E904, E906, E907, E908 | — | E721 | — | unknown |

### `public.growth_snapshot`

**Definition:** `migrations/0071_forgetting_policy.sql:28`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1760.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `taken_on`, `table_name`, `row_count` | — | E1760 | — | — | — | unknown |

### `public.industry_event`

**Definition:** `migrations/0738_industry_events.sql:6`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1345. **Table readers:** E1345, E1346.
**Unresolved SQL affecting any column:** E1347. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `version`, `created_at`, `updated_at` | — | E1345, E1346 | — | E1345 | — | unknown |
| `organization_tenant_id`, `title`, `organizer`, `kind`, `starts_at`, `ends_at`, `location`, `url`, `relevance_note`, `owner_partner`, `source` | E1345 | E1345, E1346 | — | — | — | unknown |
| `is_virtual`, `attendance_intent`, `status` | E1345 | E1345, E1346 | — | declared; caller unknown | — | unknown |
| `created_by_actor_id`, `updated_by_actor_id` | E1345 | — | — | — | — | unknown |

### `public.ingest_inbox`

**Definition:** `migrations/0001_init.sql:931`. **Product path:** positive static evidence (column detail below).

**Table writers:** E210, E1158. **Table readers:** E210, E1158, E1159, E1761, E1762, E1804.
**Unresolved SQL affecting any column:** E948. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E210.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1158, E1159, E1804 | — | E210 | — | yes |
| `received_at` | — | E1761, E1804 | — | E210 | — | yes |
| `source` | E210 | E210, E1158, E1804 | — | — | — | yes |
| `external_id` | E210 | E210 | — | — | — | unknown |
| `payload` | E210 | E1804 | — | — | — | yes |
| `status` | E210, E1158 | E210, E1158, E1159, E1761, E1804 | E1762 | declared; caller unknown | — | yes |
| `filed_refs`, `triage_note` | E210, E1158 | — | — | — | — | yes |

### `public.investigation_branch`

**Definition:** `migrations/0098_investigation_control_plane.sql:82`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E960, E965. **Table readers:** E958, E960, E961, E963, E965, E966, E967, E1763.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E958, E960, E961, E963, E965, E966, E1763 | — | E960 | — | unknown |
| `run_id` | E960 | E958, E960, E961, E963, E965, E966, E967, E1763 | — | — | — | unknown |
| `parent_branch_id`, `hypothesis`, `opened_by` | E960 | E960, E963, E965, E966 | — | — | — | unknown |
| `route_key`, `depth` | E960 | E958, E960, E963, E965, E966 | — | — | — | unknown |
| `test_input` | E960 | E960, E963, E965, E966 | — | declared; caller unknown | — | unknown |
| `status` | E965 | E958, E960, E961, E963, E965, E966, E967, E1763 | — | E960 | — | unknown |
| `effect_size`, `adjudication`, `adjudicated_by`, `adjudicated_at` | E965 | E960, E963, E965, E966 | — | — | — | unknown |
| `opened_at` | — | E960, E963, E965, E966 | — | E960 | — | unknown |

### `public.investigation_evidence`

**Definition:** `migrations/0098_investigation_control_plane.sql:104`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E962. **Table readers:** E962, E964, E966.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `recorded_at` | — | E962, E966 | — | E962 | — | unknown |
| `branch_id` | E962 | E962, E964, E966 | — | — | — | unknown |
| `contributor_id`, `scope`, `query_or_tool`, `raw_facts`, `evidence_refs`, `uncertainty` | E962 | E962, E966 | — | — | — | unknown |
| `nothing_found`, `exclusions` | E962 | E962, E966 | — | declared; caller unknown | — | unknown |

### `public.investigation_run`

**Definition:** `migrations/0098_investigation_control_plane.sql:60`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E954, E968. **Table readers:** E953, E954, E956, E961, E963, E968, E1080, E1763.
**Unresolved SQL affecting any column:** E949. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E953, E954, E956, E961, E963, E968, E1080, E1763 | — | E954 | — | unknown |
| `signal_id` | E954 | E953, E954, E956, E968, E1763 | — | — | — | unknown |
| `objective` | E954 | E954, E968, E1080, E1763 | — | — | — | unknown |
| `owner_actor_id` | E954 | E953, E954, E963, E968, E1080, E1763 | — | — | — | unknown |
| `max_depth` | E954 | E954, E968, E1763 | — | declared; caller unknown | — | unknown |
| `status` | E968 | E953, E954, E956, E961, E963, E968, E1080, E1763 | — | E954 | — | unknown |
| `conclusion`, `confidence`, `strongest_alternative`, `alternative_disposition`, `termination_reason` | E968 | E954, E968, E1763 | — | — | — | unknown |
| `opened_at` | — | E954, E968, E1080, E1763 | — | E954 | — | unknown |
| `closed_at` | E968 | E954, E968, E1080, E1763 | — | — | — | unknown |

### `public.lead`

**Definition:** `migrations/0001_init.sql:310`. **Product path:** positive static evidence (column detail below).

**Table writers:** E991, E1002, E1007, E1202, E1207, E1216, E1221, E1234, E1236, E1241. **Table readers:** E395, E712, E987, E988, E991, E993, E995, E997, E998, E999, E1001, E1002, E1004, E1007, E1011, E1113, E1152, E1202, E1207, E1216, E1220, E1221, E1222, E1232, E1234, E1235, E1236, E1237, E1238, E1241, E1264, E1265, E1266, E1268, E1436, E1700, E1708, E1710, E1747, E1752, E1753, E1754, E1762, E1766, E1768, E1769, E1770, E1772, E1773, E1774, E1785, E1787, E1791, E1794, E1801.
**Unresolved SQL affecting any column:** E828, E1054, E1240, E1241. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E395, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E712, E987, E988, E991, E993, E995, E997, E998, E999, E1001, E1002, E1004, E1007, E1202, E1207, E1216, E1220, E1221, E1222, E1232, E1234, E1235, E1236, E1237, E1238, E1264, E1265, E1549, E1700, E1710, E1752, E1753, E1754, E1766, E1768, E1769, E1770, E1772, E1773, E1774, E1791, E1794, E1801 | E1011, E1436, E1773 | E1202, E1207 | — | yes |
| `registry_ref` | E1202, E1207 | E712, E987, E995, E997, E998, E1113, E1264, E1708, E1747, E1754, E1766, E1768, E1769, E1773, E1785, E1787, E1791, E1794 | — | — | — | yes |
| `party_id` | E1202, E1207 | E712, E987, E993, E995, E998, E999, E1001, E1113, E1152, E1220, E1264, E1265, E1708, E1710, E1747, E1754, E1766, E1768, E1769, E1773, E1774, E1785, E1787, E1794 | E1011, E1265, E1266, E1268, E1436, E1787 | — | — | yes |
| `lane` | E1202, E1207, E1241 | E987, E1241, E1766, E1769, E1773, E1801 | E1801 | — | — | yes |
| `stage` | E991, E1002, E1007, E1202, E1207, E1216, E1241 | E987, E988, E991, E993, E997, E998, E999, E1001, E1004, E1152, E1220, E1238, E1241, E1708, E1747, E1754, E1766, E1769, E1773, E1774, E1794 | — | — | — | yes |
| `score` | E1202, E1241 | E987, E1241, E1766, E1769, E1773 | — | — | — | yes |
| `suppressed` | E1216, E1241 | E987, E991, E993, E997, E998, E999, E1001, E1152, E1220, E1238, E1241, E1747, E1754, E1766, E1769, E1773, E1774 | — | E1202, E1207 | — | yes |
| `est_lease_event` | E1207, E1241 | E987, E1241, E1754, E1766, E1769, E1773 | — | — | — | yes |
| `last_touch` | — | E987, E1766, E1773 | — | — | — | yes |
| `next_action_date` | E1241 | E987, E1241, E1766, E1769, E1773 | — | — | — | yes |
| `client_id` | E1221, E1236 | E987, E1220, E1747, E1766, E1768, E1770, E1773, E1774, E1801 | E1436, E1801 | — | — | yes |
| `version` | — | E987, E991, E998, E1001, E1004, E1220, E1222, E1232, E1235, E1237, E1569, E1766, E1773 | — | E1202, E1207 | E1569 | yes |
| `created_at` | — | E987, E1232, E1235, E1237, E1549, E1710, E1754, E1766, E1773, E1801 | E1436, E1762 | E1202, E1207 | — | yes |
| `created_by` | E1202, E1207 | E987, E1773 | — | — | — | yes |
| `updated_at` | — | E987, E1569, E1766, E1773 | — | E1202, E1207 | E1569 | yes |
| `updated_by` | E991, E1002, E1007, E1202, E1207, E1216, E1221, E1234, E1236, E1241 | E987, E1241, E1773 | — | — | — | yes |
| `source_type`, `source_detail` | E1202, E1207, E1241 | E987, E1241, E1754, E1773 | — | — | — | yes |
| `segment` | E1202, E1207, E1241 | E987, E1241, E1754, E1766, E1769, E1773 | — | — | — | yes |
| `report_back_due`, `drip_added`, `sf_deal`, `notes` | E1241 | E987, E1241, E1754, E1773 | — | — | — | yes |
| `drip_campaign` | E1241 | E987, E1241, E1747, E1754, E1773 | — | — | — | yes |
| `notes_path` | E1241 | E987, E1241, E1752, E1753, E1754, E1773 | — | — | — | yes |
| `event_source` | E1207, E1241 | E987, E1241, E1754, E1773 | — | — | — | yes |
| `event_confidence` | E1241 | E987, E1241, E1754, E1766, E1769, E1773 | — | — | — | yes |
| `owner_id` | E1202, E1207, E1234, E1241 | E712, E987, E993, E1241, E1754, E1766, E1773, E1774 | — | — | — | yes |
| `owner_label` | E1202, E1207, E1234, E1241 | E987, E1241, E1754, E1766, E1773 | — | — | — | yes |
| `est_lease_event_raw`, `report_back_due_raw`, `drip_added_raw` | — | E987, E1754, E1773 | — | — | — | yes |
| `score_reason` | E1202, E1241 | E987, E1241, E1772, E1773 | — | — | — | yes |

### `public.lead_contact_draft`

**Definition:** `migrations/0811_lead_stage_automation.sql:22`. **Product path:** positive static evidence (column detail below).

**Table writers:** E994, E1000. **Table readers:** E989, E993, E994, E997, E999, E1000.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E989, E994, E997, E999, E1000 | — | E994 | — | yes |
| `lead_id` | E994 | E989, E993, E997, E999 | — | — | — | yes |
| `party_id`, `owner_id`, `subject`, `body`, `scheduled_for`, `time_zone`, `created_by` | E994 | E989, E997, E999 | — | — | — | yes |
| `requires_human_send`, `dispatchable`, `created_at` | — | E989, E997, E999 | — | E994 | — | yes |
| `approved_at`, `approved_by` | E1000 | E989, E997, E999 | — | — | — | yes |

### `public.lead_lane`

**Definition:** `migrations/0001_init.sql:306`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1801.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label` | — | — | — | — | — | unknown |

### `public.lead_stage`

**Definition:** `migrations/0001_init.sql:303`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1754, E1766, E1767.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label` | — | E1754, E1766, E1767 | — | — | — | yes |
| `sort` | — | E1766, E1767 | — | declared; caller unknown | — | yes |

### `public.lead_stage_move`

**Definition:** `migrations/0811_lead_stage_automation.sql:2`. **Product path:** positive static evidence (column detail below).

**Table writers:** E990, E992, E1003, E1008. **Table readers:** E988, E990, E992, E998, E1001, E1003, E1004, E1006, E1008, E1766.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E990, E992, E998, E1001, E1003, E1004, E1006, E1008, E1766 | — | E990 | — | yes |
| `lead_id`, `from_stage` | E990 | E988, E998, E1001, E1004, E1006, E1766 | — | — | — | yes |
| `to_stage`, `evidence_ref` | E990 | E998, E1001, E1004, E1006, E1766 | — | — | — | yes |
| `activity_id` | E990 | E988, E998, E1001, E1004, E1006 | — | — | — | yes |
| `strength` | E990, E992 | E990, E998, E1001, E1004 | — | — | — | yes |
| `status` | E990, E992, E1003, E1008 | E988, E990, E998, E1001, E1004, E1006, E1766 | — | — | — | yes |
| `created_at` | — | E998, E1001, E1004, E1766 | — | E990 | — | yes |
| `created_by` | E990 | E998, E1001, E1004 | — | — | — | yes |
| `approved_at`, `approved_by` | E1003 | E998, E1001, E1004, E1006 | — | — | — | yes |
| `reason` | E990 | E990, E998, E1001, E1004, E1766 | — | — | — | yes |
| `undone_at`, `undone_by` | E1008 | E998, E1001, E1004, E1766 | — | — | — | yes |

### `public.lease`

**Definition:** `migrations/0001_init.sql:727`. **Product path:** positive static evidence (column detail below).

**Table writers:** E380. **Table readers:** E80, E380, E1122, E1705, E1731, E1795.
**Additional lexical/dependency evidence:** E80, E380.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E80, E380, E1122, E1705, E1731 | — | E380 | — | yes |
| `deal_id` | E380 | E380, E1122, E1705, E1731, E1795 | — | — | — | yes |
| `client_id` | E380 | E1122, E1705, E1795 | — | — | — | yes |
| `premises_id`, `rate_amount`, `rate_basis`, `rate_norm_sf_yr`, `escalator`, `ti_amount`, `free_rent_months`, `opex_structure`, `doc_attachment` | — | E1122, E1705 | — | — | — | yes |
| `executed_on`, `commencement_on` | E380 | E1122, E1705, E1731 | — | — | — | yes |
| `expiration_on`, `evidence_kind`, `evidence_ref` | E380 | E1122, E1705, E1731, E1795 | — | — | — | yes |
| `term_months`, `created_by`, `updated_by`, `owner_id`, `superseded_at`, `supersedes_lease_id` | E380 | E1122, E1705 | — | — | — | yes |
| `options_note` | — | E1122, E1705, E1731 | — | — | — | yes |
| `source` | E380 | E1122, E1705, E1731, E1795 | — | declared; caller unknown | — | yes |
| `version` | — | E380, E1122, E1570, E1705, E1731 | — | E380 | E1570 | yes |
| `created_at` | — | E1122, E1705, E1795 | — | E380 | — | yes |
| `updated_at` | — | E1122, E1570, E1705, E1795 | — | E380 | E1570 | yes |
| `status` | E380 | E380, E1122, E1705, E1731, E1795 | — | declared; caller unknown | — | yes |

### `public.loop_amendment`

**Definition:** `migrations/0706_amend_closed_loop.sql:28`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1327. **Table readers:** E731, E1327.
**Additional lexical/dependency evidence:** E731.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | E731 | — | E1327 | — | unknown |
| `loop_id`, `prior_outcome`, `new_outcome`, `prior_resolution`, `new_resolution`, `reason`, `actor_id` | E1327 | E731 | — | — | — | unknown |
| `idempotency_key` | E1327 | — | — | — | — | unknown |

### `public.loop_block`

**Definition:** `migrations/0024_loop_item.sql:56`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1322. **Table readers:** E1090, E1311, E1316, E1321, E1322, E1324, E1755, E1756, E1776, E1777, E1778, E1779.
**Unresolved SQL affecting any column:** E1091, E1318, E1319, E1320. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1090, E1311, E1316, E1321, E1322, E1324, E1755, E1756, E1776, E1777, E1778, E1779 | — | declared; caller unknown | — | unknown |
| `rel_path` | — | E1311, E1316, E1324, E1755, E1756, E1779 | — | — | — | unknown |
| `kind` | — | E1311, E1316, E1755, E1756 | — | — | — | unknown |
| `seq`, `header_cols` | — | E1755 | — | — | — | unknown |
| `block_key` | — | E1090, E1311, E1316, E1324, E1755, E1756, E1776, E1777, E1778, E1779 | — | — | — | unknown |
| `prose_md` | E1322 | E1755 | — | declared; caller unknown | — | unknown |
| `col_order` | — | E1311, E1755 | — | — | — | unknown |
| `renders_closed` | — | E1755 | — | declared; caller unknown | — | unknown |
| `version` | — | E1321, E1571 | — | declared; caller unknown | E1571 | unknown |
| `created_at` | — | E1321 | — | declared; caller unknown | — | unknown |
| `created_by` | — | — | — | — | — | unknown |
| `updated_at` | — | E1571 | — | declared; caller unknown | E1571 | unknown |
| `updated_by` | E1322 | — | — | — | — | unknown |

### `public.loop_domain`

**Definition:** `migrations/0038_loop_domain.sql:35`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1309, E1310, E1755.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug` | — | E1309, E1310, E1755 | — | — | — | unknown |
| `label` | — | E1755 | — | — | — | unknown |
| `sort` | — | E1310, E1755 | — | — | — | unknown |

### `public.loop_item`

**Definition:** `migrations/0024_loop_item.sql:130`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1313, E1328. **Table readers:** E128, E157, E270, E446, E829, E834, E909, E1026, E1073, E1090, E1092, E1093, E1312, E1313, E1314, E1315, E1323, E1326, E1328, E1400, E1401, E1755, E1756, E1775, E1776, E1777, E1778, E1779.
**Unresolved SQL affecting any column:** E1091, E1153, E1154, E1155, E1156, E1317, E1325. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E128, E157, E270, E446.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E128, E157, E270, E446, E909, E1073, E1090, E1312, E1313, E1314, E1315, E1323, E1326, E1328, E1755, E1776, E1777, E1778, E1779 | E829 | E1313 | — | unknown |
| `kind` | E1313 | E157, E270, E829, E834, E1026, E1073, E1090, E1092, E1312, E1315, E1775, E1776, E1777, E1778, E1779 | — | — | — | unknown |
| `number` | E1313 | E128, E157, E829, E834, E909, E1026, E1090, E1092, E1312, E1315, E1400, E1755, E1756, E1776, E1777, E1778, E1779 | — | — | — | unknown |
| `block_id` | E1313 | E157, E1090, E1093, E1755, E1756, E1776, E1777, E1778, E1779 | — | — | — | unknown |
| `render_seq` | E1313 | E157, E829, E834, E1093, E1755, E1779 | — | — | — | unknown |
| `col_order` | — | E157, E1755 | — | — | — | unknown |
| `title` | E1313 | E157, E829, E834, E1026, E1073, E1400, E1755, E1756, E1776, E1777, E1778, E1779 | — | — | — | unknown |
| `body` | E1313 | E157, E829, E834, E909, E1026, E1755, E1756, E1776, E1777, E1778, E1779 | — | — | — | unknown |
| `owner` | E1313 | E157, E270, E829, E834, E909, E1026, E1073, E1755, E1756, E1776, E1777, E1778, E1779 | — | — | — | unknown |
| `since_text` | E1313 | E157, E1073, E1755, E1776, E1779 | — | — | — | unknown |
| `unblocks` | E1313 | E157, E1026, E1755, E1779 | — | — | — | unknown |
| `source_note` | E1313 | E157, E1312, E1755, E1779 | — | — | — | unknown |
| `closed_text` | — | E157, E1755, E1779 | — | — | — | unknown |
| `outcome` | E1328 | E157, E1755, E1779 | — | — | — | unknown |
| `extra_cells` | E1313 | E157, E1073, E1755 | — | declared; caller unknown | — | unknown |
| `marker` | E1313 | E157, E270, E1026, E1090, E1775, E1776, E1777, E1778, E1779 | — | declared; caller unknown | — | unknown |
| `marker_literal` | E1313 | E157, E1755, E1777, E1779 | — | — | — | unknown |
| `due_on` | E1313 | E157, E270, E1090, E1777, E1778, E1779 | — | — | — | unknown |
| `drift_critical` | E1313 | E157, E1778, E1779 | — | declared; caller unknown | — | unknown |
| `status` | E1313, E1328 | E128, E157, E270, E446, E829, E834, E909, E1026, E1073, E1090, E1312, E1315, E1400, E1401, E1755, E1756, E1775, E1776, E1777, E1778, E1779 | — | declared; caller unknown | — | unknown |
| `close_outcome` | E1328 | E157, E1090, E1756, E1779 | — | — | — | unknown |
| `closed_by` | — | E157, E1756 | — | — | — | unknown |
| `closed_at` | — | E157, E1756, E1779 | — | — | — | unknown |
| `tier` | E1313 | E128, E157, E270, E1073, E1400, E1401, E1779 | — | — | — | unknown |
| `personal_to` | E1313 | E128, E157, E270, E829, E1073, E1779 | — | — | — | unknown |
| `version` | — | E157, E909, E1073, E1314, E1323, E1326, E1400, E1572, E1779 | — | E1313 | E1572 | unknown |
| `created_at` | — | E157, E1026, E1073, E1314, E1323, E1326, E1776, E1778, E1779 | — | E1313 | — | unknown |
| `created_by` | E1313 | E157, E270 | — | — | — | unknown |
| `updated_at` | — | E157, E1073, E1400, E1572, E1779 | — | E1313 | E1572 | unknown |
| `updated_by` | E1313, E1328 | E157 | — | — | — | unknown |
| `domain` | E1313 | E157, E1026, E1073, E1312, E1400, E1755, E1775, E1776, E1778 | — | — | — | unknown |
| `blocker_class` | E1313 | E157, E1026, E1090, E1776, E1778 | — | — | — | unknown |
| `blocker_detail` | E1313 | E157, E1026, E1778 | — | — | — | unknown |

### `public.marketing_subject`

**Definition:** `migrations/0066_marketing_campaign_and_measurement.sql:146`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E726, E1112, E1279, E1280, E1793.
**Additional lexical/dependency evidence:** E726.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1279, E1793 | — | declared; caller unknown | — | yes |
| `subject_type`, `slug`, `retired_at` | — | E726, E1112, E1279, E1280 | — | — | — | yes |
| `label` | — | E1793 | — | — | — | unknown |
| `note`, `created_by` | — | — | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.media_recommendation`

**Definition:** `migrations/0111_media_recommendation.sql:25`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1781.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `kind`, `tags`, `priority`, `status`, `recommended_on`, `personal_to`, `updated_at`, `version` | — | E1781 | — | declared; caller unknown | — | unknown |
| `title`, `author`, `why`, `observed_pattern`, `finished_on`, `created_by` | — | E1781 | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `updated_by` | — | — | — | — | — | unknown |

### `public.memory_evidence`

**Definition:** `migrations/0299_memory_kernel.sql:128`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1018, E1024. **Table readers:** E1018, E1020, E1024.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1018, E1020, E1024 | — | E1018, E1024 | — | unknown |
| `memory_id`, `source_type`, `source_ref`, `observation`, `observed_by_actor_id` | E1018, E1024 | E1020 | — | — | — | unknown |
| `human_quote` | E1018 | E1020 | — | — | — | unknown |
| `provenance` | E1018, E1024 | E1020 | — | declared; caller unknown | — | unknown |
| `observed_at`, `created_at` | — | E1020 | — | E1018, E1024 | — | unknown |

### `public.memory_item`

**Definition:** `migrations/0299_memory_kernel.sql:21`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1017, E1021, E1022, E1023, E1025. **Table readers:** E112, E415, E733, E1017, E1019, E1020, E1021, E1022, E1023, E1025.
**Additional lexical/dependency evidence:** E112, E415, E733.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E112, E415, E733, E1019, E1020, E1023 | E112, E1017, E1021, E1022, E1025 | E1017, E1023 | — | yes |
| `organization_tenant_id` | E1017, E1023 | E112, E415, E733, E1019, E1020, E1021, E1022, E1023, E1025, E1574, E1575, E1576 | — | — | — | yes |
| `work_request_id`, `work_request_version`, `plan_id` | E1017, E1023 | E112, E415, E733, E1020, E1022, E1023, E1574, E1575, E1576 | — | — | — | yes |
| `kind` | E1017, E1023 | E112, E415, E733, E1019, E1020, E1023, E1574, E1575 | E1017, E1022 | — | — | yes |
| `statement` | E1017, E1023 | E112, E415, E733, E1017, E1019, E1020, E1023, E1574 | — | — | — | yes |
| `context` | E1017, E1023 | E112, E415, E733, E1017, E1019, E1020, E1022, E1023, E1574, E1575 | — | — | — | yes |
| `scope` | E1017, E1023 | E112, E415, E733, E1017, E1019, E1020, E1021, E1022, E1023, E1025, E1574, E1575 | — | — | — | yes |
| `owner_actor_id` | E1017, E1023 | E112, E733, E1017, E1019, E1020, E1021, E1022, E1023, E1025, E1574, E1575 | — | — | — | yes |
| `observed_by_actor_id` | E1017, E1023 | E112, E733, E1020, E1574 | — | — | — | yes |
| `status` | E1021, E1022, E1025 | E112, E415, E733, E1017, E1019, E1020, E1021, E1022, E1023, E1025, E1574, E1575 | — | E1017, E1023 | E1574 | yes |
| `confidence` | E1017, E1023 | E112, E415, E733, E1017, E1019, E1020, E1023, E1574, E1575 | — | declared; caller unknown | — | yes |
| `version` | E1021, E1022, E1025 | E112, E415, E733, E1017, E1019, E1020, E1021, E1022, E1023, E1025, E1574, E1575 | — | E1017, E1023 | — | yes |
| `promoted_by_actor_id`, `promoted_at` | E1021 | E112, E733, E1020, E1574, E1575 | — | — | — | yes |
| `corrected_by_actor_id`, `correction_reason`, `corrected_at` | E1022 | E112, E733, E1020, E1574, E1575 | — | — | — | yes |
| `forgotten_by_actor_id`, `forget_reason`, `forgotten_at` | E1025 | E112, E733, E1020, E1574, E1575 | — | — | — | yes |
| `predecessor_id`, `lineage_root_id` | E1023 | E112, E733, E1020, E1023, E1574, E1575 | — | — | — | yes |
| `created_at` | — | E112, E733, E1017, E1019, E1020, E1574, E1575 | — | E1017, E1023 | E1575 | yes |
| `updated_at` | E1021, E1022, E1025 | E112, E733, E1020, E1575 | — | E1017, E1023 | E1575 | yes |
| `search_vector` | — | E112, E733, E1019, E1020 | — | — | — | yes |

### `public.national_account_owner`

**Definition:** `migrations/0090_deal_room_workspaces.sql:8`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1362, E1367. **Table readers:** E1362, E1367, E1725, E1728.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `account_client_id` | E1362, E1367 | E1725, E1728 | — | — | — | yes |
| `owner_actor_id` | E1362, E1367 | E1362, E1725, E1728 | — | — | — | yes |
| `set_by` | E1362, E1367 | E1362 | — | — | — | unknown |
| `set_at` | E1362 | — | — | E1362, E1367 | — | unknown |

### `public.negotiation_claim`

**Definition:** `migrations/0063_counterparty_observation.sql:199`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1247. **Table readers:** E1247, E1720.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1247, E1720 | — | E1247 | — | yes |
| `round_id`, `claim_type`, `stated_floor`, `stated_floor_basis`, `quote`, `note`, `created_by` | E1247 | E1720 | — | — | — | yes |
| `loggable`, `created_at` | — | E1720 | — | E1247 | — | yes |
| `source` | E1247 | E1720 | — | declared; caller unknown | — | yes |

### `public.negotiation_claim_type`

**Definition:** `migrations/0063_counterparty_observation.sql:146`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1098, E1099, E1720.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label`, `falsifiable` | — | E1098, E1099, E1720 | — | — | — | unknown |
| `derived` | — | E1098, E1099, E1720 | — | declared; caller unknown | — | unknown |
| `reversal_test` | — | E1098, E1720 | — | — | — | unknown |
| `sort` | — | E1099, E1720 | — | — | — | unknown |

### `public.negotiation_round`

**Definition:** `migrations/0001_init.sql:493`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1121, E1245, E1735, E1783.
**Unresolved SQL affecting any column:** E1246. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1121, E1735, E1783 | — | declared; caller unknown | — | yes |
| `deal_id`, `round_no`, `side` | — | E1121, E1245, E1735, E1783 | — | — | — | yes |
| `proposed_on`, `rate_amount`, `rate_basis`, `rate_norm_sf_yr`, `ti_amount`, `ti_basis`, `free_rent_months`, `term_months`, `escalator`, `expires_on` | — | E1121, E1735, E1783 | — | — | — | yes |
| `options_note`, `created_by`, `updated_by` | — | E1121 | — | — | — | unknown |
| `opex_note`, `note` | — | E1121, E1735 | — | — | — | yes |
| `source` | — | E1121, E1735 | — | declared; caller unknown | — | yes |
| `version`, `updated_at` | — | E1121, E1579 | — | declared; caller unknown | E1579 | unknown |
| `created_at` | — | E1121, E1783 | — | declared; caller unknown | — | unknown |
| `submarket_condition` | — | E1121, E1783 | — | — | — | unknown |

### `public.next_action`

**Definition:** `migrations/0001_init.sql:451`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1129, E1130, E1133, E1161, E1164, E1166, E1213, E1215, E1275, E1276, E1382. **Table readers:** E712, E1129, E1130, E1132, E1133, E1135, E1161, E1164, E1166, E1168, E1213, E1215, E1273, E1274, E1275, E1276, E1382, E1696, E1705, E1726, E1728, E1746, E1750, E1754, E1758, E1804.
**Unresolved SQL affecting any column:** E826. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E712, E1130, E1133, E1135, E1164, E1166, E1213, E1215, E1273, E1275, E1276, E1382, E1696, E1705, E1726, E1754, E1758, E1804 | E1132 | E1130, E1164, E1215 | — | yes |
| `subject_type` | E1130, E1164, E1215 | E712, E1129, E1132, E1133, E1135, E1161, E1166, E1168, E1213, E1273, E1274, E1696, E1705, E1726, E1746, E1750, E1754, E1758, E1804 | — | — | — | yes |
| `subject_id` | E1130, E1164, E1215, E1276 | E712, E1129, E1132, E1133, E1135, E1161, E1166, E1168, E1213, E1273, E1274, E1696, E1705, E1726, E1750, E1754, E1758, E1804 | — | — | — | yes |
| `owner_id` | E1130, E1164, E1215 | E712, E1129, E1133, E1161, E1166, E1168, E1213, E1273, E1274, E1696, E1705, E1726, E1750, E1804 | — | — | — | yes |
| `due_on` | E1130, E1164, E1215 | E712, E1133, E1135, E1166, E1168, E1696, E1705, E1726, E1746, E1750, E1754, E1758, E1804 | — | — | — | yes |
| `description` | E1130, E1164, E1215 | E712, E1132, E1133, E1135, E1166, E1168, E1213, E1273, E1696, E1705, E1726, E1750, E1754, E1758, E1804 | — | — | — | yes |
| `status` | E1129, E1133, E1161, E1166, E1213, E1275, E1382 | E712, E1129, E1132, E1133, E1135, E1161, E1166, E1168, E1213, E1273, E1274, E1696, E1705, E1726, E1746, E1750, E1754, E1758, E1804 | — | E1130, E1164, E1215 | — | yes |
| `version` | — | E1580 | — | E1130, E1164, E1215 | E1580 | yes |
| `created_at` | — | E1696, E1705, E1726, E1750, E1754, E1758 | — | E1130, E1164, E1215 | — | yes |
| `created_by` | E1130, E1164, E1215 | — | — | — | — | yes |
| `updated_at` | E1130, E1164 | E712, E1135, E1580, E1726 | E1132 | E1215 | E1580 | yes |
| `updated_by` | E1129, E1130, E1133, E1161, E1166, E1213, E1275, E1276, E1382 | — | — | — | — | yes |
| `hold_until` | — | E712, E1696, E1705, E1726, E1804 | — | — | — | yes |
| `item_title` | — | E1129, E1132, E1133, E1135, E1161, E1166, E1168, E1213, E1382, E1726 | — | — | — | yes |
| `item_bullets` | — | E1726 | — | E1130, E1164, E1215 | — | yes |
| `source_ref` | — | E1726 | — | — | — | yes |

### `public.org_merge_log`

**Definition:** `migrations/0059_org_identity.sql:127`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `party_id`, `from_org`, `to_org`, `identity_key` | — | — | — | — | — | unknown |
| `merged_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.parcel`

**Definition:** `migrations/0001_init.sql:538`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | — | declared; caller unknown | — | unknown |
| `county`, `state`, `parcel_no`, `source` | — | — | — | — | — | unknown |

### `public.participant_role`

**Definition:** `migrations/0017_vocab_ref_tables.sql:203`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E743.
**Additional lexical/dependency evidence:** E743.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `side` | — | E743 | — | — | — | unknown |
| `label` | — | — | — | — | — | unknown |

### `public.partner_room_turn`

**Definition:** `migrations/0192_partner_room.sql:27`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1030. **Table readers:** E379, E611, E1030, E1031, E1032, E1786.
**Additional lexical/dependency evidence:** E379, E611.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E379, E611, E1030, E1031, E1786 | — | — | — | unknown |
| `room_id` | E1030 | E611, E1031, E1786 | — | declared; caller unknown | — | unknown |
| `at` | — | E611, E1030, E1032, E1786 | E611 | E1030 | — | unknown |
| `sponsor`, `seat` | E1030 | E611, E1031, E1032, E1786 | — | — | — | unknown |
| `kind`, `origin_actor` | E1030 | E611, E1031, E1032, E1786 | — | declared; caller unknown | — | unknown |
| `body` | E1030 | E611, E1032, E1786 | — | — | — | unknown |
| `msg_id` | E1030 | E379, E611, E1031, E1032, E1786 | — | — | — | unknown |
| `origin_channel` | E1030 | E1031, E1032, E1786 | — | declared; caller unknown | — | unknown |

### `public.party`

**Definition:** `migrations/0001_init.sql:245`. **Product path:** positive static evidence (column detail below).

**Table writers:** E735, E1040, E1041, E1189, E1197, E1205, E1206, E1244, E1267, E1364, E1371. **Table readers:** E123, E395, E425, E712, E727, E735, E978, E981, E987, E993, E995, E997, E998, E999, E1001, E1011, E1012, E1033, E1034, E1035, E1036, E1038, E1039, E1040, E1041, E1042, E1043, E1050, E1055, E1056, E1089, E1113, E1116, E1118, E1152, E1187, E1189, E1196, E1197, E1201, E1205, E1206, E1219, E1229, E1242, E1244, E1257, E1259, E1263, E1264, E1265, E1267, E1363, E1364, E1368, E1370, E1371, E1433, E1436, E1703, E1705, E1708, E1709, E1710, E1720, E1721, E1722, E1723, E1725, E1728, E1737, E1747, E1749, E1750, E1751, E1753, E1754, E1758, E1764, E1766, E1768, E1769, E1772, E1773, E1774, E1784, E1785, E1787, E1788, E1794, E1795, E1805, E1806.
**Unresolved SQL affecting any column:** E222, E736, E828, E1037, E1054, E1243, E1244, E1397. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E123, E395, E425, E712, E727, E735.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E123, E425, E712, E727, E735, E978, E981, E987, E993, E995, E997, E998, E999, E1001, E1011, E1012, E1033, E1034, E1035, E1036, E1038, E1039, E1040, E1041, E1042, E1043, E1050, E1055, E1056, E1089, E1113, E1116, E1118, E1152, E1187, E1189, E1196, E1197, E1201, E1205, E1206, E1219, E1229, E1242, E1257, E1259, E1263, E1264, E1267, E1363, E1364, E1368, E1370, E1371, E1433, E1703, E1705, E1708, E1709, E1710, E1720, E1721, E1722, E1723, E1725, E1728, E1737, E1747, E1749, E1750, E1751, E1753, E1754, E1758, E1764, E1766, E1768, E1769, E1772, E1773, E1774, E1784, E1785, E1787, E1788, E1794, E1795, E1805, E1806 | E1011, E1436, E1773, E1805 | E735, E1189, E1197, E1205, E1206, E1364, E1371 | — | yes |
| `kind` | E735, E1189, E1197, E1205, E1206, E1364, E1371 | E735, E1034, E1036, E1039, E1257, E1363, E1720, E1721, E1772, E1773, E1788 | E1436, E1773 | — | — | yes |
| `name` | E735, E1040, E1189, E1197, E1205, E1206, E1364, E1371 | E123, E712, E727, E735, E978, E993, E997, E998, E1033, E1034, E1036, E1039, E1043, E1116, E1118, E1187, E1196, E1257, E1363, E1368, E1370, E1703, E1705, E1708, E1710, E1720, E1721, E1722, E1723, E1725, E1728, E1737, E1747, E1749, E1750, E1751, E1753, E1754, E1758, E1766, E1768, E1769, E1772, E1773, E1784, E1785, E1787, E1788, E1794, E1795, E1805, E1806 | E1436 | — | — | yes |
| `org_id` | E1041, E1189, E1197, E1206, E1371 | E1033, E1034, E1042, E1116, E1265, E1370, E1436, E1703, E1710, E1720, E1749, E1754, E1758, E1768, E1772, E1773, E1784, E1794, E1795 | — | — | — | yes |
| `phone` | E1189, E1206, E1244 | E1034, E1244, E1436, E1710, E1720, E1749, E1754, E1758, E1773, E1795 | — | — | — | yes |
| `email` | E1189, E1206, E1244 | E425, E727, E995, E1034, E1152, E1187, E1244, E1257, E1436, E1710, E1720, E1749, E1754, E1758, E1773, E1795 | — | — | — | yes |
| `city` | E1189, E1206, E1244, E1371 | E1034, E1116, E1187, E1196, E1244, E1257, E1705, E1710, E1720, E1721, E1749, E1750, E1754, E1766, E1769, E1773, E1794, E1795 | E1436 | — | — | yes |
| `state` | E1189, E1206, E1371 | E1034, E1116, E1201, E1705, E1720, E1721, E1749, E1750, E1758, E1766, E1769, E1773, E1795 | — | — | — | yes |
| `npi` | — | E1034, E1720, E1773 | — | — | — | yes |
| `notes_path`, `contact_state_reason`, `contact_state_cadence`, `contact_state_set_by`, `contact_state_set_at` | — | E1720, E1773 | — | — | — | yes |
| `merged_into` | E1267 | E425, E712, E735, E978, E987, E993, E995, E997, E998, E999, E1001, E1012, E1033, E1036, E1039, E1050, E1055, E1089, E1113, E1187, E1196, E1219, E1229, E1257, E1259, E1263, E1363, E1370, E1433, E1703, E1705, E1708, E1720, E1721, E1764, E1768, E1772, E1773, E1774, E1784, E1785, E1794, E1795 | E1436 | — | — | yes |
| `deleted_at` | — | E425, E712, E735, E978, E987, E993, E995, E997, E998, E999, E1001, E1012, E1033, E1036, E1039, E1050, E1055, E1219, E1229, E1257, E1433, E1436, E1705, E1708, E1720, E1764, E1768, E1772, E1773, E1774, E1784, E1785, E1794, E1795 | — | — | — | yes |
| `version` | — | E1242, E1581, E1720, E1773 | — | E735, E1189, E1197, E1205, E1206, E1364, E1371 | E1581 | yes |
| `created_at` | — | E1242, E1264, E1720, E1773 | E1436 | E735, E1189, E1197, E1205, E1206, E1364, E1371 | — | yes |
| `created_by` | E735, E1189, E1197, E1205, E1206, E1364, E1371 | E1720, E1773 | — | — | — | yes |
| `updated_at` | — | E1581, E1720, E1773 | — | E735, E1189, E1197, E1205, E1206, E1364, E1371 | E1581 | yes |
| `updated_by` | E735, E1040, E1041, E1189, E1197, E1205, E1206, E1244, E1267, E1364, E1371 | E1244, E1720, E1773 | — | — | — | yes |
| `specialty` | E1189, E1206 | E1720, E1754, E1766, E1769, E1773, E1794 | — | — | — | yes |
| `county` | E1189, E1206, E1244 | E1244, E1436, E1710, E1720, E1754, E1766, E1769, E1773 | — | — | — | yes |
| `title` | E1244 | E1244, E1436, E1708, E1710, E1720, E1758, E1773, E1806 | — | — | — | yes |
| `ref` | — | E727, E1703, E1708, E1720, E1773, E1784, E1785, E1787, E1788, E1794 | — | — | — | yes |
| `contact_state` | — | E987, E993, E995, E997, E998, E999, E1001, E1152, E1219, E1436, E1705, E1708, E1709, E1710, E1720, E1773, E1774, E1794 | — | E735, E1189, E1197, E1205, E1206, E1364, E1371 | — | yes |
| `contact_state_until` | — | E1705, E1720, E1773 | — | — | — | yes |
| `cell` | E1244 | E1034, E1244, E1436, E1710, E1720, E1773 | — | — | — | yes |

### `public.party_link`

**Definition:** `migrations/0001_init.sql:376`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1114, E1260, E1262. **Table readers:** E1052, E1114, E1115, E1260, E1261, E1262, E1264, E1265, E1784, E1787, E1805.
**Unresolved SQL affecting any column:** E1054, E1397. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1052, E1114, E1115, E1260, E1261, E1262, E1784 | E1805 | E1114, E1260 | — | yes |
| `from_party`, `to_party` | E1114, E1260 | E1115, E1261, E1264, E1265, E1784, E1787, E1805 | — | — | — | yes |
| `kind` | E1114, E1260 | E1115, E1261, E1784, E1787, E1805 | — | — | — | yes |
| `note` | E1114, E1260 | E1787 | — | — | — | yes |
| `source`, `created_by` | E1114, E1260 | — | — | — | — | yes |
| `created_at` | — | E1787 | — | E1114, E1260 | — | yes |
| `via_party` | E1260, E1262 | E1261, E1262, E1264, E1265, E1784, E1805 | — | — | — | yes |
| `occurred_on` | E1260, E1262 | E1261, E1262 | E1052 | — | — | yes |

### `public.party_link_deal`

**Definition:** `migrations/0839_relationship_deal_links.sql:6`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1052. **Table readers:** E1050, E1052.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `link_id`, `created_by` | E1052 | — | — | — | — | yes |
| `deal_id`, `referred_by` | E1052 | E1050, E1052 | — | — | — | yes |
| `occurred_on` | E1052 | E1050 | E1052 | — | — | yes |
| `note` | E1052 | E1050 | — | — | — | yes |
| `created_at` | — | E1050 | — | E1052 | — | yes |

### `public.party_link_kind`

**Definition:** `migrations/0020_party_link_kind_vocabulary.sql:50`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1094, E1095.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug` | — | E1094, E1095 | — | — | — | unknown |
| `label` | — | — | — | — | — | unknown |
| `sort` | — | E1095 | — | — | — | unknown |

### `public.placement`

**Definition:** `migrations/0001_init.sql:844`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1108, E1109, E1110, E1111, E1336, E1711, E1714, E1715, E1716, E1717, E1718, E1789.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1108, E1109, E1110, E1111, E1715, E1716, E1717, E1718, E1789 | — | declared; caller unknown | — | unknown |
| `piece_id` | — | E1108, E1109, E1110, E1111, E1336, E1711, E1715, E1716, E1718, E1789 | — | — | — | unknown |
| `platform` | — | E1108, E1109, E1110, E1111, E1715, E1717, E1718, E1789 | — | — | — | unknown |
| `external_id` | — | E1108, E1109, E1110, E1111, E1717, E1718, E1789 | — | — | — | unknown |
| `url` | — | E1108, E1109, E1110, E1111, E1789 | — | — | — | unknown |
| `scheduled_at` | — | E1108, E1109, E1110, E1111, E1711, E1714, E1717 | — | — | — | unknown |
| `live_at` | — | E1108, E1109, E1110, E1111, E1336, E1711, E1715, E1717, E1789 | — | — | — | unknown |

### `public.placement_measurement`

**Definition:** `migrations/0066_marketing_campaign_and_measurement.sql:370`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1341, E1343. **Table readers:** E1341, E1343, E1789.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1341 | — | E1341, E1343 | — | unknown |
| `placement_id`, `source`, `outcome`, `reason` | E1341, E1343 | E1789 | — | — | — | unknown |
| `attempted_at` | E1341, E1343 | E1341, E1789 | — | declared; caller unknown | — | unknown |
| `metric_kinds` | E1341, E1343 | — | — | declared; caller unknown | — | unknown |
| `note`, `recorded_by` | E1341, E1343 | — | — | — | — | unknown |

### `public.placement_metric`

**Definition:** `migrations/0001_init.sql:853`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1342. **Table readers:** E1342, E1715, E1716, E1790.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `placement_id` | E1342 | E1715, E1716, E1790 | — | — | — | unknown |
| `observed_at`, `source` | E1342 | E1790 | — | declared; caller unknown | — | unknown |
| `kind` | E1342 | E1342, E1790 | — | — | — | unknown |
| `value` | E1342 | E1790 | — | — | — | unknown |

### `public.premises`

**Definition:** `migrations/0001_init.sql:653`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1194. **Table readers:** E978, E1119, E1120, E1194, E1721, E1740.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E978, E1119, E1120, E1194, E1721, E1740 | — | E1194 | — | yes |
| `deal_id` | E1194 | E978, E1119, E1120, E1721, E1740 | — | — | — | yes |
| `label` | E1194 | E1119, E1740 | — | — | — | yes |
| `created_at` | — | E1119, E1120, E1740 | — | E1194 | — | yes |
| `created_by` | E1194 | — | — | — | — | yes |

### `public.premises_space`

**Definition:** `migrations/0001_init.sql:660`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1195. **Table readers:** E978, E982, E983, E1119, E1195, E1721, E1740.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `premises_id` | E1195 | E978, E1119, E1721, E1740 | — | — | — | yes |
| `space_id` | E1195 | E978, E982, E983, E1119, E1721, E1740 | — | — | — | yes |

### `public.record_flag`

**Definition:** `migrations/0001_init.sql:954`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1272, E1278, E1281. **Table readers:** E1034, E1264, E1265, E1272, E1278, E1281, E1436, E1706, E1709, E1748, E1793.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1034, E1272, E1281, E1706, E1748, E1793 | E1436 | E1278, E1281 | — | yes |
| `subject_type` | E1278, E1281 | E1034, E1264, E1265, E1272, E1436, E1706, E1709, E1748, E1793 | — | — | — | yes |
| `subject_id` | E1272, E1278, E1281 | E1034, E1264, E1265, E1272, E1436, E1706, E1709, E1748, E1793 | — | — | — | yes |
| `kind` | E1278, E1281 | E1034, E1264, E1706, E1709, E1748, E1793 | E1436 | — | — | yes |
| `value` | E1278, E1281 | E1034, E1264, E1706, E1748, E1793 | E1748 | — | — | yes |
| `source`, `created_by` | E1278, E1281 | E1706, E1793 | — | — | — | yes |
| `observed_at` | E1281 | E1034, E1281, E1436, E1706, E1748, E1793 | — | E1278 | — | yes |
| `expires_on` | E1281 | E1034, E1436, E1709, E1748, E1793 | — | — | — | yes |

### `public.record_source`

**Definition:** `migrations/0001_init.sql:223`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1304. **Table readers:** E445, E1304, E1743, E1780.
**Additional lexical/dependency evidence:** E445.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `entity_type`, `entity_id`, `source_system` | E1304 | E445, E1743, E1780 | — | — | — | yes |
| `external_key` | E1304 | E1743, E1780 | — | — | — | yes |
| `imported_at` | — | — | — | E1304 | — | unknown |

### `public.registration`

**Definition:** `migrations/0001_init.sql:712`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1784.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1784 | — | declared; caller unknown | — | unknown |
| `deal_id`, `premises_id`, `registered_on`, `doc_attachment`, `created_by` | — | — | — | — | — | unknown |
| `registered_with_party`, `method` | — | E1784 | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.research_site`

**Definition:** `migrations/0849_research_site.sql:16`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1060, E1062. **Table readers:** E1059, E1060, E1061, E1062.
**Unresolved SQL affecting any column:** E1058. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1060, E1061, E1062 | — | E1060 | — | unknown |
| `host` | E1060 | E1060, E1061, E1062 | — | — | — | unknown |
| `url`, `note` | E1060 | E1061 | — | — | — | unknown |
| `topics` | E1060 | E1059, E1061 | — | — | — | unknown |
| `added_by_actor_id` | E1060 | — | — | — | — | unknown |
| `added_at` | — | E1060 | — | E1060 | — | unknown |
| `removed_at` | E1062 | E1059, E1060, E1061, E1062 | — | — | — | unknown |
| `removed_by_actor_id`, `removal_reason` | E1062 | — | — | — | — | unknown |

### `public.retrieval_concept`

**Definition:** `migrations/0135_situation_retrieval.sql:34`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E737, E740. **Table readers:** E39, E737, E740, E742, E1676, E1797.
**Additional lexical/dependency evidence:** E39, E737, E740, E742.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E39, E737, E740, E742, E1676, E1797 | E39 | E737 | — | unknown |
| `concept_key` | E737 | E737, E742, E1676, E1797 | — | — | — | unknown |
| `label`, `review_after` | E737 | E742, E1797 | — | — | — | unknown |
| `definition`, `proposer_id`, `approver_id`, `approved_at` | E737 | E742 | — | — | — | unknown |
| `status` | E737, E740 | E39, E737, E740, E742, E1676, E1797 | E39, E742 | declared; caller unknown | — | unknown |
| `version` | E740 | E740, E742 | — | E737 | — | unknown |
| `retired_at` | E740 | E742 | — | — | — | unknown |
| `created_at` | — | E742 | — | E737 | — | unknown |
| `updated_at` | E740 | E742 | — | E737 | — | unknown |

### `public.retrieval_phrase`

**Definition:** `migrations/0135_situation_retrieval.sql:53`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E737, E740. **Table readers:** E737, E740, E742.
**Additional lexical/dependency evidence:** E737, E740, E742.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E737, E740, E742 | — | E737 | — | unknown |
| `concept_id` | E737 | E740, E742 | E742 | — | — | unknown |
| `display_phrase`, `match_mode`, `source`, `source_ref`, `proposer_id`, `approver_id`, `approved_at` | E737 | E742 | — | — | — | unknown |
| `normalized_phrase`, `review_after` | — | E742 | — | — | — | unknown |
| `min_similarity`, `weight` | E737 | E742 | — | declared; caller unknown | — | unknown |
| `status` | E737, E740 | E740, E742 | E742 | declared; caller unknown | — | unknown |
| `version` | E740 | E740, E742 | — | E737 | — | unknown |
| `retired_at` | E740 | E742 | — | — | — | unknown |
| `created_at` | — | E742 | — | E737 | — | unknown |
| `updated_at` | E740 | E742 | — | E737 | — | unknown |

### `public.retrieval_proposal`

**Definition:** `migrations/0135_situation_retrieval.sql:117`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E737, E1064. **Table readers:** E343, E439, E737, E1064, E1065, E1066, E1083.
**Additional lexical/dependency evidence:** E343, E439, E737.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E343, E439, E737, E1064, E1065, E1066, E1083 | — | E1064 | — | unknown |
| `proposal_type` | E1064 | E343, E737, E1064, E1065, E1066, E1602, E1603 | — | — | — | unknown |
| `payload`, `proposer_id` | E1064 | E343, E737, E1066, E1602, E1603 | — | — | — | unknown |
| `reason` | E1064 | E343, E737, E1066, E1083, E1602, E1603 | — | — | — | unknown |
| `status` | E737, E1064 | E343, E439, E737, E1064, E1065, E1066, E1083, E1602, E1603 | — | declared; caller unknown | — | unknown |
| `reviewer_id` | E737 | E439, E737, E1066 | — | — | — | unknown |
| `resulting_row_ids` | E737 | E737, E1066 | — | E1064 | — | unknown |
| `idempotency_key` | E1064 | E737, E1065, E1066, E1602, E1603 | — | — | — | unknown |
| `version` | E737 | E343, E737, E1064, E1065, E1066, E1083 | — | E1064 | — | unknown |
| `created_at` | — | E343, E737, E1064, E1065, E1066, E1083, E1602, E1603 | — | E1064 | — | unknown |
| `reviewed_at` | E737 | E737, E1066, E1083 | — | — | — | unknown |

### `public.retrieval_query_log`

**Definition:** `migrations/0135_situation_retrieval.sql:150`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E730. **Table readers:** E730.
**Additional lexical/dependency evidence:** E730.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E730 | — | E730 | — | unknown |
| `normalized_hash`, `result_count`, `score_bands`, `selected_row_ids`, `policy_id`, `policy_version`, `explicit_hit`, `scope_ref` | E730 | — | — | — | — | unknown |
| `created_at` | — | — | — | E730 | — | unknown |

### `public.retrieval_ranking_policy`

**Definition:** `migrations/0135_situation_retrieval.sql:136`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E725, E742.
**Additional lexical/dependency evidence:** E725, E742.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `policy_id`, `golden_suite_digest` | — | E725, E742 | — | — | — | unknown |
| `version`, `created_at` | — | E742 | — | declared; caller unknown | — | unknown |
| `formula`, `config`, `approved_at` | — | E742 | — | — | — | unknown |
| `status` | — | E725, E742 | E742 | declared; caller unknown | — | unknown |
| `is_default` | — | E725, E742 | — | declared; caller unknown | — | unknown |

### `public.room_dispatch_ack`

**Definition:** `migrations/0531_room_dispatch_spine.sql:104`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E5. **Table readers:** E5, E611.
**Additional lexical/dependency evidence:** E5, E611.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E5, E611 | — | — | — | unknown |
| `dispatch_ref` | E5 | E5, E611 | E611 | — | — | unknown |
| `stage`, `by_actor`, `evidence` | E5 | E5, E611 | — | — | — | unknown |
| `at` | — | E5, E611 | E611 | E5 | — | unknown |

### `public.room_dispatch_link`

**Definition:** `migrations/0531_room_dispatch_spine.sql:76`. **Product path:** positive static evidence (column detail below).

**Table writers:** E379. **Table readers:** E379, E611.
**Unresolved SQL affecting any column:** E5, E719. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E379, E611.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E379, E611 | — | — | — | unknown |
| `turn_id`, `session_id`, `work_request_id` | E379 | E379, E611 | — | — | — | unknown |
| `dispatch_ref` | E379 | E379, E611 | E611 | — | — | unknown |
| `at` | — | E379 | E611 | E379 | — | unknown |
| `written_by` | E379 | E379 | — | — | — | unknown |

### `public.rule`

**Definition:** `migrations/0001_init.sql:114`. **Product path:** positive static evidence (column detail below).

**Table writers:** E17, E18, E35, E429, E430, E1283, E1299. **Table readers:** E15, E17, E18, E32, E34, E35, E39, E71, E72, E73, E112, E183, E343, E368, E390, E415, E429, E430, E438, E440, E441, E590, E655, E659, E690, E831, E1123, E1282, E1283, E1285, E1295, E1296, E1297, E1298, E1299, E1300, E1437, E1448, E1490, E1499, E1665, E1679, E1689, E1690, E1707, E1744, E1799.
**Unresolved SQL affecting any column:** E37, E404, E422, E429, E659. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E15, E17, E18, E32, E34, E35, E39, E71, E72, E73, E112, E183, E343, E368, E390, E415, E430, E438, E440, E441, E590, E655, E690.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E15, E17, E18, E32, E34, E35, E71, E72, E73, E112, E183, E343, E368, E390, E415, E429, E430, E438, E440, E441, E590, E655, E659, E690, E831, E1123, E1282, E1283, E1285, E1295, E1296, E1297, E1298, E1299, E1300, E1437, E1448, E1490, E1499, E1601, E1604, E1606, E1665, E1679, E1689, E1690, E1707, E1744, E1799 | E39, E112 | E1283 | — | yes |
| `statement` | E17, E18, E1283, E1299 | E15, E17, E18, E32, E34, E35, E71, E72, E73, E112, E183, E343, E368, E390, E415, E429, E430, E590, E655, E659, E690, E831, E1123, E1285, E1295, E1297, E1490, E1604, E1679, E1707, E1744, E1799 | — | — | — | yes |
| `human_quote` | E1283, E1299 | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E343, E368, E390, E429, E430, E655, E659, E1297, E1604, E1679, E1707 | — | — | — | yes |
| `taught_by` | E1283 | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E655, E659, E831, E1490, E1604, E1606, E1679, E1707 | — | — | — | yes |
| `scope` | E17, E1283, E1299 | E15, E17, E18, E34, E35, E39, E71, E72, E73, E112, E183, E343, E368, E390, E429, E430, E590, E659, E1297, E1604, E1707, E1799 | — | declared; caller unknown | — | yes |
| `personal_to` | E1283 | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E438, E590, E655, E659, E831, E1283, E1295, E1499, E1604, E1665, E1707, E1799 | — | — | — | yes |
| `status` | E35, E429, E430 | E15, E17, E18, E32, E34, E35, E71, E72, E73, E112, E183, E343, E368, E390, E429, E430, E438, E440, E441, E590, E655, E659, E690, E831, E1123, E1282, E1285, E1295, E1297, E1437, E1490, E1499, E1601, E1604, E1689, E1690, E1707, E1799 | E39 | E1283 | E1601, E1604 | yes |
| `activated_by` | E35 | E15, E17, E18, E32, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E659, E1490, E1604 | — | — | — | yes |
| `activated_at` | E35 | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E659, E1604, E1679, E1707 | — | — | — | yes |
| `enforcement` | E35 | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E659, E1604, E1707 | — | E1283 | — | yes |
| `supersedes` | E1283 | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E659, E1604, E1707 | — | — | — | yes |
| `created_at` | — | E15, E17, E18, E32, E34, E35, E71, E72, E73, E112, E183, E343, E368, E390, E429, E430, E655, E659, E831, E1298, E1604, E1679, E1799 | — | E1283 | — | yes |
| `version` | — | E15, E17, E18, E32, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E659, E690, E1297, E1298, E1300, E1604, E1608, E1799 | — | E1283 | E1608 | yes |
| `updated_at` | — | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E659, E1604, E1608 | — | E1283 | E1608 | yes |
| `retired_by`, `retired_at` | E429, E430 | E15, E17, E18, E34, E35, E71, E72, E73, E112, E183, E368, E390, E429, E430, E659, E1604 | — | — | — | yes |

### `public.search_candidate`

**Definition:** `migrations/0001_init.sql:677`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `confirmed_by_joe` | — | — | — | declared; caller unknown | — | unknown |
| `search_id`, `premises_id`, `tier`, `rank`, `reason` | — | — | — | — | — | unknown |

### `public.sensitive_blob`

**Definition:** `migrations/0001_init.sql:182`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `value`, `scrubbed_at` | — | — | — | — | — | unknown |

### `public.session_work`

**Definition:** `migrations/0193_session_work.sql:29`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E612.
**Additional lexical/dependency evidence:** E612.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `kind`, `title` | — | E612 | — | — | — | unknown |
| `last_seen`, `open_loop` | — | E612 | — | declared; caller unknown | — | unknown |
| `next_seat`, `promise`, `kin` | — | — | — | — | — | unknown |
| `sources`, `created_at` | — | — | — | declared; caller unknown | — | unknown |
| `version`, `updated_at` | — | E1616 | — | declared; caller unknown | E1616 | unknown |

### `public.signal_event`

**Definition:** `migrations/0098_investigation_control_plane.sql:8`. **Product path:** positive static evidence (column detail below).

**Table writers:** E950, E955, E969. **Table readers:** E280, E700, E950, E951, E952, E955, E956, E969, E1763, E1800.
**Unresolved SQL affecting any column:** E949. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E280, E700.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E700, E950, E951, E952, E955, E956, E969, E1763, E1800 | E280 | E950 | — | yes |
| `producer` | E950 | E700, E950, E951, E952, E1800 | — | — | — | yes |
| `signal_key`, `metric_name`, `observed_value`, `baseline_value`, `threshold_value`, `comparison`, `severity`, `detected_at`, `evidence_refs`, `created_by` | E950 | E950, E951, E952, E1800 | — | — | — | unknown |
| `signal_kind` | E950 | E700, E950, E951, E952, E956, E1763, E1800 | — | — | — | yes |
| `subject_type`, `subject_ref` | E950 | E950, E951, E952, E1763, E1800 | — | — | — | unknown |
| `payload` | E950 | E950, E951, E952, E1800 | — | declared; caller unknown | — | unknown |
| `status` | E955, E969 | E950, E951, E952, E1800 | — | E950 | — | unknown |
| `created_at` | — | E950, E951, E952 | — | E950 | — | unknown |

### `public.source_capture`

**Definition:** `migrations/0070_source_capture.sql:42`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** E1302. **Table readers:** E1301, E1302, E1757.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E1302, E1757 | — | E1302 | — | unknown |
| `captured_on` | E1302 | E1301, E1302, E1757 | — | — | — | unknown |
| `session`, `source_url` | E1302 | E1301, E1757 | — | — | — | unknown |
| `visibility` | E1302 | E1757 | — | declared; caller unknown | — | unknown |
| `status`, `merge_note` | E1302 | E1301, E1757 | — | declared; caller unknown | — | unknown |
| `version`, `updated_at` | — | E1621 | — | E1302 | E1621 | unknown |
| `created_at` | — | E1757 | — | E1302 | — | unknown |
| `created_by`, `updated_by` | E1302 | — | — | — | — | unknown |

### `public.space`

**Definition:** `migrations/0001_init.sql:590`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1193. **Table readers:** E123, E978, E982, E983, E1119, E1193, E1721, E1740.
**Additional lexical/dependency evidence:** E123.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E123, E978, E982, E983, E1119, E1193, E1721, E1740 | — | E1193 | — | yes |
| `building_id` | E1193 | E123, E978, E983, E1119, E1721, E1740 | — | — | — | yes |
| `suite` | E1193 | E123, E978, E1119, E1740 | — | — | — | yes |
| `floor`, `condition`, `created_by`, `updated_by` | E1193 | — | — | — | — | yes |
| `area_amount` | E1193 | E123, E1119, E1740 | — | — | — | yes |
| `area_basis` | E1193 | E1119, E1740 | — | — | — | yes |
| `version`, `updated_at` | — | E1625 | — | E1193 | E1625 | yes |
| `created_at` | — | — | — | E1193 | — | yes |

### `public.space_search`

**Definition:** `migrations/0001_init.sql:667`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E123.
**Additional lexical/dependency evidence:** E123.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id`, `status` | — | E123 | — | declared; caller unknown | — | unknown |
| `client_id`, `spec` | — | E123 | — | — | — | unknown |
| `deal_id`, `report_path`, `created_by` | — | — | — | — | — | unknown |
| `created_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.submarket_condition`

**Definition:** `migrations/0063_counterparty_observation.sql:104`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E1096, E1097, E1722.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug` | — | E1096, E1097, E1722 | — | — | — | unknown |
| `label`, `sort` | — | E1097 | — | — | — | unknown |
| `tightness` | — | E1097, E1722 | — | — | — | unknown |
| `note` | — | — | — | — | — | unknown |

### `public.system_config`

**Definition:** `migrations/0001_init.sql:74`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** E744, E1088, E1748.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `key` | — | E744, E1088, E1748 | — | — | — | unknown |
| `value` | — | E744, E1088 | E1748 | — | — | unknown |
| `note`, `updated_by` | — | — | — | — | — | unknown |
| `updated_at` | — | — | — | declared; caller unknown | — | unknown |

### `public.tool_call`

**Definition:** `migrations/0001_init.sql:145`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1150. **Table readers:** E383, E446, E1149, E1150, E1412, E1762.
**Unresolved SQL affecting any column:** E1148. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E383, E446.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `idempotency_key` | E1150 | E383, E446, E1149, E1412 | — | — | — | yes |
| `verb` | E1150 | E383, E446 | E1762 | — | — | unknown |
| `actor_id`, `via`, `authorization_class` | E1150 | E383, E1412 | — | — | — | yes |
| `request_hash` | E1150 | E383, E1149 | — | — | — | unknown |
| `response` | E1150 | E383, E446, E1149 | — | — | — | unknown |
| `created_at` | — | E383, E446 | E1762 | E1150 | — | unknown |
| `client_id`, `sponsoring_human_slug`, `personal_scope`, `organization_tenant_id`, `correlation_id` | E1150 | E383 | — | — | — | unknown |

### `public.tool_read_call`

**Definition:** `migrations/0108_tool_read_call.sql:117`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1013. **Table readers:** E1013, E1762.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | — | E1013 | E1013 | — | unknown |
| `verb` | E1013 | — | E1762 | — | — | unknown |
| `actor_slug`, `actor_id`, `error_kind`, `via`, `client_id`, `organization_tenant_id`, `sponsoring_human_slug`, `personal_scope`, `authorization_class` | E1013 | — | — | — | — | unknown |
| `ok` | E1013 | — | — | declared; caller unknown | — | unknown |
| `created_at` | — | — | E1762 | E1013 | — | unknown |

### `public.vendor`

**Definition:** `migrations/0001_init.sql:344`. **Product path:** positive static evidence (column detail below).

**Table writers:** E1053, E1225, E1231, E1277. **Table readers:** E395, E712, E1050, E1051, E1053, E1113, E1225, E1226, E1231, E1264, E1265, E1266, E1268, E1277, E1433, E1436, E1700, E1708, E1710, E1758, E1762, E1785, E1787, E1794, E1805, E1806.
**Unresolved SQL affecting any column:** E828, E1054, E1230, E1231, E1269, E1270, E1397. Column absence below remains unknown.
**Additional lexical/dependency evidence:** E395, E712.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `id` | — | E712, E1051, E1053, E1225, E1226, E1264, E1265, E1277, E1700, E1710, E1758, E1794, E1805 | E1436, E1805 | E1225 | — | yes |
| `vendor_ref` | E1225 | E1113, E1264, E1708, E1758, E1785, E1787, E1794, E1805, E1806 | — | — | — | yes |
| `party_id` | E1225 | E712, E1050, E1051, E1113, E1264, E1265, E1433, E1708, E1710, E1758, E1785, E1787, E1794, E1805, E1806 | E1265, E1266, E1268, E1436, E1787 | — | — | yes |
| `category` | E1225 | E1758, E1806 | — | — | — | yes |
| `verticals` | E1231 | E1231, E1436, E1710, E1758 | — | — | — | yes |
| `stage` | E1225, E1231 | E1231, E1758, E1794 | — | — | — | yes |
| `owner_id`, `owner_label` | E1225 | E1758 | — | — | — | yes |
| `referral_active`, `offers`, `seeking`, `rivalry_group`, `intro_notes` | E1231 | E1231, E1758 | — | — | — | yes |
| `territory` | E1231 | E1231, E1433, E1758, E1806 | — | — | — | yes |
| `originated`, `enrich`, `links_label` | — | E1758 | — | — | — | unknown |
| `out_of_market` | E1231 | E1231, E1758 | — | E1225 | — | yes |
| `last_touch` | — | — | — | — | — | unknown |
| `version` | — | E1226, E1655 | — | E1225 | E1655 | yes |
| `created_at` | — | E1226, E1710 | E1436, E1762 | E1225 | — | yes |
| `created_by` | E1225 | — | — | — | — | yes |
| `updated_at` | — | E1655 | — | E1225 | E1655 | yes |
| `updated_by` | E1053, E1225, E1231, E1277 | E1231 | — | — | — | yes |
| `relationship_level` | — | E1436, E1708, E1710, E1805, E1806 | — | — | — | unknown |
| `disposition` | — | E1436, E1710, E1805, E1806 | — | E1225 | — | yes |
| `is_target` | — | E1436, E1710, E1806 | — | E1225 | — | yes |
| `category_slug` | E1231 | E1231, E1436, E1710, E1758, E1806 | — | — | — | yes |
| `merged_into` | E1277 | E1050, E1051, E1433, E1758, E1794, E1805, E1806 | E1266, E1268, E1436 | — | — | yes |
| `loan_programs`, `trust_override` | E1231 | E1231 | — | — | — | yes |
| `deal_history_verified_at` | E1053, E1231 | E1231 | — | — | — | yes |
| `deal_evidence` | E1231 | E1050, E1231 | — | E1225 | — | yes |

### `public.vendor_category`

**Definition:** `migrations/0050_vendor_category.sql:27`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1227, E1433, E1758.
**Unresolved SQL affecting any column:** E1228. Column absence below remains unknown.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug` | — | E1227, E1758 | E1433 | — | — | yes |
| `label` | — | E1758 | E1433 | — | — | unknown |
| `sort` | — | E1227 | E1433 | — | — | yes |

### `public.vendor_disposition`

**Definition:** `migrations/0047_vendor_relationship.sql:57`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1433.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug`, `label`, `sort` | — | — | E1433 | — | — | unknown |
| `workable` | — | E1433 | — | — | — | yes |

### `public.vendor_relationship_level`

**Definition:** `migrations/0047_vendor_relationship.sql:44`. **Product path:** unknown; no positive named-product path established by the model.

**Table writers:** —. **Table readers:** —.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `level`, `label`, `note` | — | — | — | — | — | unknown |

### `public.vendor_stage`

**Definition:** `migrations/0001_init.sql:338`. **Product path:** positive static evidence (column detail below).

**Table writers:** —. **Table readers:** E1100, E1101, E1433, E1758.

| Columns | W | R | R? | D | T | Product |
|---|---|---|---|---|---|---|
| `slug` | — | E1100, E1101, E1758 | E1433 | — | — | unknown |
| `label` | — | E1101, E1758 | E1433 | — | — | unknown |
| `sort` | — | — | E1433 | declared; caller unknown | — | unknown |

## Inventory evidence register

`Source` entries name current files and lines. `Routine`, `View`, and `Trigger` entries name surviving SQL declarations with their migration location. **Source link** indicates only that source text or a traced stored routine/view calls the SQL object; it does not establish an exercised product path or installed database state. Trigger entries are declarations. Overloaded/dynamic call resolution remains subject to the bounds above.

| ID | Writer/reader | Location | Source link |
|---|---|---|---|
| E1 | Routine `ops.accept_ready_plan_amendment` | `migrations/0890_retire_heavy_build_runtime.sql:8` | traced from source text |
| E2 | Routine `ops.accept_sourced_work_request_outcome_feedback` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:864` | traced from source text |
| E3 | Routine `ops.accept_sourced_work_request_plan` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:571` | traced from source text |
| E4 | Routine `ops.accept_tour_route_version` | `migrations/0759_tour_reviewed_route_digest.sql:34` | traced from source text |
| E5 | Routine `ops.acknowledge_dispatch` | `migrations/0531_room_dispatch_spine.sql:528` | traced from source text |
| E6 | Routine `ops.acknowledge_notification` | `migrations/0521_r03_notifications.sql:271` | traced from source text |
| E7 | Routine `ops.acknowledge_ready_plan_amendment` | `migrations/0532a_canonical_ownership_lease_activation.sql:1859` | traced from source text |
| E8 | Routine `ops.acquire_canonical_ownership_lease` | `migrations/0856_qualify_security_definer_dependencies.sql:66` | traced from source text |
| E9 | Routine `ops.activate_calendar_prebrief_joe_live` | `migrations/0250_calendar_prebrief_joe_runtime.sql:13` | not established |
| E10 | Routine `ops.activate_context_bundle` | `migrations/0856_qualify_security_definer_dependencies.sql:321` | traced from source text |
| E11 | Routine `ops.activate_guidance_registry` | `migrations/0856_qualify_security_definer_dependencies.sql:374` | traced from source text |
| E12 | Routine `ops.activate_guidance_situation_mapping` | `migrations/0856_qualify_security_definer_dependencies.sql:459` | traced from source text |
| E13 | Routine `ops.add_meeting_note` | `migrations/0906_dot_database_design.sql:449` | traced from source text |
| E14 | Routine `ops.admit_job_cost` | `migrations/0165_control_plane_cost_refusals.sql:27` | not established |
| E15 | Routine `ops.adopt_legacy_active_rule_v1` | `migrations/0867_adopt_active_legacy_rule_approval.sql:159` | traced from source text |
| E16 | Routine `ops.advance_workflow_cutover_stage` | `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:586` | traced from source text |
| E17 | Routine `ops.amend_rule_scope` | `migrations/0886_active_rule_scope_backfill.sql:34` | traced from source text |
| E18 | Routine `ops.amend_rule_statement` | `migrations/0886_active_rule_scope_backfill.sql:286` | traced from source text |
| E19 | Routine `ops.answer_work_request_for_joe` | `migrations/0575_answer_needs_joe_work_request.sql:92` | traced from source text |
| E20 | Routine `ops.append_doc_conversation_turn` | `migrations/0520_doc_conversation_store.sql:111` | traced from source text |
| E21 | Routine `ops.append_tour_cheat_sheet_revision` | `migrations/0429_tour_domain_route_cheat_sheet.sql:196` | traced from source text |
| E22 | Routine `ops.append_tour_coordinate_candidate` | `migrations/0428_tour_property_identity_jurisdiction.sql:411` | traced from source text |
| E23 | Routine `ops.append_tour_entrance_verification_receipt` | `migrations/0428_tour_property_identity_jurisdiction.sql:421` | traced from source text |
| E24 | Routine `ops.append_tour_field_assertion` | `migrations/0427_tour_rights_projection_hardening.sql:410` | traced from source text |
| E25 | Routine `ops.append_tour_property_identifier_assertion` | `migrations/0428_tour_property_identity_jurisdiction.sql:401` | traced from source text |
| E26 | Routine `ops.append_tour_rights_receipt` | `migrations/0427_tour_rights_projection_hardening.sql:358` | traced from source text |
| E27 | Routine `ops.append_tour_route_stop` | `migrations/0429_tour_domain_route_cheat_sheet.sql:289` | traced from source text |
| E28 | Routine `ops.append_tour_route_stop_transition` | `migrations/0429_tour_domain_route_cheat_sheet.sql:142` | traced from source text |
| E29 | Routine `ops.append_tour_route_version` | `migrations/0429_tour_domain_route_cheat_sheet.sql:264` | traced from source text |
| E30 | Routine `ops.append_tour_selection_cart_version` | `migrations/0430_tour_delivery_data_plane.sql:305` | traced from source text |
| E31 | Routine `ops.append_tour_source_evidence` | `migrations/0427_tour_rights_projection_hardening.sql:393` | traced from source text |
| E32 | Routine `ops.applicable_rules` | `migrations/0856_qualify_security_definer_dependencies.sql:616` | traced from source text |
| E33 | Routine `ops.apply_guidance_import_batch` | `migrations/0170_guidance_import_lifecycle.sql:416` | not established |
| E34 | Routine `ops.approve_rule` | `migrations/0904_one_step_gate_rule_approval.sql:31` | traced from source text |
| E35 | Routine `ops.approve_rule_receipt_activation_v1` | `migrations/0856_qualify_security_definer_dependencies.sql:682` | traced from source text |
| E36 | Routine `ops.approve_staging_release` | `migrations/0219_staging_release_approval_receipt.sql:93` | not established |
| E37 | Routine `ops.assert_guidance_import_inventory` | `migrations/0856_qualify_security_definer_dependencies.sql:922` | traced from source text |
| E38 | Routine `ops.assert_guidance_import_materialization` | `migrations/0170_guidance_import_lifecycle.sql:702` | traced from source text |
| E39 | Routine `ops.assert_guidance_registry_coverage` | `migrations/0856_qualify_security_definer_dependencies.sql:947` | traced from source text |
| E40 | Routine `ops.assign_execution_profile` | `migrations/0856_qualify_security_definer_dependencies.sql:993` | not established |
| E41 | Routine `ops.assurance_all_tokens_absent` | `migrations/0451_assurance_evidence_acceptance_persistence.sql:157` | not established |
| E42 | Routine `ops.assurance_append_lineage_current` | `migrations/0538_canonical_ownership_assurance_binding.sql:322` | not established |
| E43 | Routine `ops.assurance_health_label` | `migrations/0717_assurance_health_evidence_store.sql:247` | traced from source text |
| E44 | Routine `ops.assurance_lease_lineage_current` | `migrations/0451_assurance_evidence_acceptance_persistence.sql:165` | not established |
| E45 | Routine `ops.assurance_manifest_currentness` | `migrations/0451_assurance_evidence_acceptance_persistence.sql:981` | not established |
| E46 | Routine `ops.assurance_terminal_evidence_lineage_current` | `migrations/0538_canonical_ownership_assurance_binding.sql:294` | not established |
| E47 | Routine `ops.assurance_terminal_receipt_lineage_current` | `migrations/0538_canonical_ownership_assurance_binding.sql:211` | not established |
| E48 | Routine `ops.assurance_validate_compiler_input` | `migrations/0451_assurance_evidence_acceptance_persistence.sql:293` | not established |
| E49 | Routine `ops.attempt_receipt_binding_valid` | `migrations/0309_governed_execution_environment_providers.sql:431` | not established |
| E50 | Routine `ops.attest_attempt_receipt_evaluation` | `migrations/0856_qualify_security_definer_dependencies.sql:1021` | not established |
| E51 | Routine `ops.attest_execution_environment_conformance` | `migrations/0856_qualify_security_definer_dependencies.sql:1088` | not established |
| E52 | Routine `ops.authenticated_canonical_ownership_controller_binding` | `migrations/0532a_canonical_ownership_lease_activation.sql:502` | not established |
| E53 | Routine `ops.benchmark_accept_manifest_draft` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1872` | not established |
| E54 | Routine `ops.benchmark_acceptance_guard` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1290` | not established |
| E55 | Routine `ops.benchmark_content_frozen_after_acceptance` | `migrations/0508_foundation_assurance_minimum_receipt.sql:683` | not established |
| E56 | Routine `ops.benchmark_current_accepted_draft` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1447` | not established |
| E57 | Routine `ops.benchmark_dimension_array` | `migrations/0508_foundation_assurance_minimum_receipt.sql:727` | not established |
| E58 | Routine `ops.benchmark_draft_integrity_error` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1460` | not established |
| E59 | Routine `ops.benchmark_draft_structure_error` | `migrations/0508_foundation_assurance_minimum_receipt.sql:833` | not established |
| E60 | Routine `ops.benchmark_gate_zero_outcome` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1069` | not established |
| E61 | Routine `ops.benchmark_measurement_coverage_binding` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1148` | not established |
| E62 | Routine `ops.benchmark_pass_requires_attestation` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1833` | not established |
| E63 | Routine `ops.benchmark_payload_preimage` | `migrations/0508_foundation_assurance_minimum_receipt.sql:739` | not established |
| E64 | Routine `ops.benchmark_portfolio_prerequisite` | `migrations/0508_foundation_assurance_minimum_receipt.sql:971` | not established |
| E65 | Routine `ops.benchmark_propose_manifest_draft` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1596` | not established |
| E66 | Routine `ops.benchmark_readback` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1511` | not established |
| E67 | Routine `ops.benchmark_review_guard` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1240` | not established |
| E68 | Routine `ops.benchmark_review_manifest_draft` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1699` | not established |
| E69 | Routine `ops.bind_execution_environment_to_assignment` | `migrations/0309_governed_execution_environment_providers.sql:327` | not established |
| E70 | Routine `ops.bind_execution_environment_to_envelope` | `migrations/0309_governed_execution_environment_providers.sql:365` | not established |
| E71 | Routine `ops.bind_f05_rule_contract` | `migrations/0724_f05_live_rule_context.sql:35` | traced from source text |
| E72 | Routine `ops.bind_rule_controls` | `migrations/0867_adopt_active_legacy_rule_approval.sql:49` | traced from source text |
| E73 | Routine `ops.bind_rule_delivery` | `migrations/0856_qualify_security_definer_dependencies.sql:1243` | traced from source text |
| E74 | Routine `ops.bind_work_request_criterion_evidence` | `migrations/0872_self_closing_records.sql:613` | traced from source text |
| E75 | Routine `ops.calendar_canary_live_job` | `migrations/0226_calendar_canary_record_layer.sql:15` | not established |
| E76 | Routine `ops.cancel_workflow_cutover_plan` | `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:528` | traced from source text |
| E77 | Routine `ops.canonical_ownership_claim_projection` | `migrations/0532a_canonical_ownership_lease_activation.sql:833` | not established |
| E78 | Routine `ops.canonical_ownership_currentness` | `migrations/0532a_canonical_ownership_lease_activation.sql:131` | traced from source text |
| E79 | Routine `ops.canonical_ownership_dependency_state` | `migrations/0507a_engineering_slice_plan_validators.sql:2159` | traced from source text |
| E80 | Routine `ops.canonical_ownership_lifecycle` | `migrations/0532a_canonical_ownership_lease_activation.sql:737` | not established |
| E81 | Routine `ops.canonical_ownership_lock_lineage` | `migrations/0450_canonical_ownership_lease_kernel.sql:214` | traced from source text |
| E82 | Routine `ops.canonical_ownership_merge_binding_valid` | `migrations/0532a_canonical_ownership_lease_activation.sql:320` | traced from source text |
| E83 | Routine `ops.canonical_ownership_plan_dependencies` | `migrations/0507a_engineering_slice_plan_validators.sql:2099` | traced from source text |
| E84 | Routine `ops.canonical_ownership_release_for_job` | `migrations/0532a_canonical_ownership_lease_activation.sql:878` | not established |
| E85 | Routine `ops.canonical_ownership_runtime_principal_valid` | `migrations/0532a_canonical_ownership_lease_activation.sql:352` | traced from source text |
| E86 | Routine `ops.canonical_ownership_trusted_context` | `migrations/0532a_canonical_ownership_lease_activation.sql:547` | traced from source text |
| E87 | Routine `ops.canonical_ownership_validate_live` | `migrations/0450_canonical_ownership_lease_kernel.sql:974` | traced from source text |
| E88 | Routine `ops.capability_attestation_guard` | `migrations/0126_capability_program_evidence.sql:135` | not established |
| E89 | Routine `ops.capability_program_close_guard` | `migrations/0126_capability_program_evidence.sql:184` | not established |
| E90 | Routine `ops.capture_source_merge_plan_scope` | `migrations/0470_source_merge_authority_projection.sql:64` | not established |
| E91 | Routine `ops.capture_sourced_work_request` | `migrations/0174_program6_sourced_work_request_capture.sql:64` | traced from source text |
| E92 | Routine `ops.capture_whats_new_lead_creation` | `migrations/0766_doc_whats_new_repair.sql:11` | not established |
| E93 | Routine `ops.check_canonical_ownership_lease` | `migrations/0450_canonical_ownership_lease_kernel.sql:1068` | traced from source text |
| E94 | Routine `ops.claim_calendar_prebrief_joe_live_job` | `migrations/0250_calendar_prebrief_joe_runtime.sql:39` | not established |
| E95 | Routine `ops.claim_job` | `migrations/0335_engineering_controller_currentness.sql:1911` | not established |
| E96 | Routine `ops.claim_job_mode` | `migrations/0335_engineering_controller_currentness.sql:1948` | not established |
| E97 | Routine `ops.claim_meeting_processing` | `migrations/0556_meeting_mode_store.sql:349` | traced from source text |
| E98 | Routine `ops.claim_staging_deployment_attempt` | `migrations/0202_staging_release_readback_receipt.sql:433` | not established |
| E99 | Routine `ops.claim_staging_forward_fix_rehearsal` | `migrations/0315_program5_forward_fix_rehearsal.sql:221` | not established |
| E100 | Routine `ops.claim_staging_restore_only_attempt` | `migrations/0295_staging_restore_only_recovery.sql:180` | not established |
| E101 | Routine `ops.clear_recovered_incident` | `migrations/0294_incident_fingerprint_and_success_clears.sql:292` | traced from source text |
| E102 | Routine `ops.commit_cost_ledger_operation` | `migrations/0519_producer_cost_ledger.sql:224` | not established |
| E103 | Routine `ops.compile_context_bundle` | `migrations/0303_evidence_activation_reliability.sql:745` | traced from source text |
| E104 | Routine `ops.complete_calendar_prebrief_joe_live_job` | `migrations/0250_calendar_prebrief_joe_runtime.sql:176` | not established |
| E105 | Routine `ops.complete_doc_suggestion_scan` | `migrations/0744_doc_suggestions.sql:122` | traced from source text |
| E106 | Routine `ops.complete_job` | `migrations/0906_dot_database_design.sql:118` | not established |
| E107 | Routine `ops.complete_sourced_work_request_on_bound_evidence` | `migrations/0872_self_closing_records.sql:499` | traced from source text |
| E108 | Routine `ops.completion_disposition_before_insert` | `migrations/0431_completion_register_schema.sql:335` | not established |
| E109 | Routine `ops.completion_observation_before_insert` | `migrations/0431_completion_register_schema.sql:303` | not established |
| E110 | Routine `ops.confirm_slice_completions` | `migrations/0628_doctorcre_slice_done_marker.sql:1346` | traced from source text |
| E111 | Routine `ops.context_activation_brief_assignment` | `migrations/0303_evidence_activation_reliability.sql:881` | not established |
| E112 | Routine `ops.context_activation_compiler_items` | `migrations/0303_evidence_activation_reliability.sql:81` | traced from source text |
| E113 | Routine `ops.context_activation_receipt_binding` | `migrations/0303_evidence_activation_reliability.sql:926` | not established |
| E114 | Routine `ops.correspondence_consent_in_force` | `migrations/0700_governed_correspondence_store.sql:385` | traced from source text |
| E115 | Routine `ops.correspondence_readiness` | `migrations/0700_governed_correspondence_store.sql:558` | traced from source text |
| E116 | Routine `ops.correspondence_record_adapter_consent` | `migrations/0700_governed_correspondence_store.sql:402` | traced from source text |
| E117 | Routine `ops.correspondence_record_draft` | `migrations/0700_governed_correspondence_store.sql:512` | not established |
| E118 | Routine `ops.correspondence_record_read_receipt` | `migrations/0700_governed_correspondence_store.sql:482` | traced from source text |
| E119 | Routine `ops.correspondence_revoke_adapter_consent` | `migrations/0700_governed_correspondence_store.sql:444` | traced from source text |
| E120 | Routine `ops.correspondence_thread_readback` | `migrations/0700_governed_correspondence_store.sql:582` | traced from source text |
| E121 | Routine `ops.create_calendar_canary_source_snapshot` | `migrations/0856_qualify_security_definer_dependencies.sql:1467` | not established |
| E122 | Routine `ops.create_doc_conversation` | `migrations/0523_doc_conversation_write_doors.sql:57` | traced from source text |
| E123 | Routine `ops.create_nightly_availability_canary_source_snapshot` | `migrations/0856_qualify_security_definer_dependencies.sql:1489` | not established |
| E124 | Routine `ops.create_tour_domain` | `migrations/0429_tour_domain_route_cheat_sheet.sql:96` | traced from source text |
| E125 | Routine `ops.create_tour_public_projection_draft` | `migrations/0427_tour_rights_projection_hardening.sql:427` | traced from source text |
| E126 | Routine `ops.current_sourced_work_requests` | `migrations/0498_f09_workflow_truth_and_scac_successor.sql:188` | traced from source text |
| E127 | Routine `ops.deactivate_guidance_registry` | `migrations/0856_qualify_security_definer_dependencies.sql:1523` | traced from source text |
| E128 | Routine `ops.decide_doc_suggestion` | `migrations/0906_dot_database_design.sql:6` | traced from source text |
| E129 | Routine `ops.decide_guidance_import_batch` | `migrations/0856_qualify_security_definer_dependencies.sql:1581` | traced from source text |
| E130 | Routine `ops.decide_meeting_action` | `migrations/0906_dot_database_design.sql:580` | traced from source text |
| E131 | Routine `ops.decline_sourced_work_request` | `migrations/0914_decline_triaged_work_request.sql:60` | traced from source text |
| E132 | Routine `ops.defect_resolution_evidence_mismatch` | `migrations/0872_self_closing_records.sql:161` | not established |
| E133 | Routine `ops.defect_resolution_evidence_receipt` | `migrations/0872_self_closing_records.sql:116` | not established |
| E134 | Routine `ops.deployment_requires_a_live_approval` | `migrations/0540_release_readiness_without_repeat_approval.sql:363` | not established |
| E135 | Routine `ops.disable_legacy_schedule` | `migrations/0184_notes_duplicate_schedule_cutover.sql:35` | traced from source text |
| E136 | Routine `ops.discover_ready_plan_amendments` | `migrations/0532a_canonical_ownership_lease_activation.sql:1829` | traced from source text |
| E137 | Routine `ops.doc_conversation_facts` | `migrations/0520_doc_conversation_store.sql:169` | traced from source text |
| E138 | Routine `ops.doc_suggestion_visible` | `migrations/0744_doc_suggestions.sql:60` | traced from source text |
| E139 | Routine `ops.effective_ready_plan` | `migrations/0532a_canonical_ownership_lease_activation.sql:1344` | traced from source text |
| E140 | Routine `ops.end_meeting` | `migrations/0556_meeting_mode_store.sql:838` | traced from source text |
| E141 | Routine `ops.enforce_completion_capsule` | `migrations/0281_a_decline_is_not_built_work.sql:84` | not established |
| E142 | Routine `ops.enforce_work_in_progress_limit` | `migrations/0906_dot_database_design.sql:143` | traced from source text |
| E143 | Routine `ops.engineering_admission_source` | `migrations/0532a_canonical_ownership_lease_activation.sql:989` | traced from source text |
| E144 | Routine `ops.engineering_claim_slice` | `migrations/0335_engineering_controller_currentness.sql:266` | traced from source text |
| E145 | Routine `ops.engineering_contract_stale` | `migrations/0532a_canonical_ownership_lease_activation.sql:45` | traced from source text |
| E146 | Routine `ops.engineering_controller_binding` | `migrations/0335_engineering_controller_currentness.sql:347` | traced from source text |
| E147 | Routine `ops.engineering_enqueue_slice_job` | `migrations/0335_engineering_controller_currentness.sql:1462` | traced from source text |
| E148 | Routine `ops.engineering_envelope_currentness` | `migrations/0532a_canonical_ownership_lease_activation.sql:97` | traced from source text |
| E149 | Routine `ops.engineering_fail_claim` | `migrations/0335_engineering_controller_currentness.sql:867` | traced from source text |
| E150 | Routine `ops.engineering_finalize_slice_receipt` | `migrations/0335_engineering_controller_currentness.sql:793` | traced from source text |
| E151 | Routine `ops.engineering_passport_facts` | `migrations/0335_engineering_controller_currentness.sql:916` | traced from source text |
| E152 | Routine `ops.engineering_record_slice_receipt` | `migrations/0507a_engineering_slice_plan_validators.sql:858` | traced from source text |
| E153 | Routine `ops.engineering_register_slice_plan` | `migrations/0616_f03_retire_legacy_slice_plan_registration.sql:38` | traced from source text |
| E154 | Routine `ops.engineering_retire_permanently_ineligible_jobs` | `migrations/0335_engineering_controller_currentness.sql:241` | traced from source text |
| E155 | Routine `ops.engineering_work_request_currentness_guard` | `migrations/0335_engineering_controller_currentness.sql:72` | not established |
| E156 | Routine `ops.enqueue_job` | `migrations/0604_doctorcre_r02_dual_write_gating.sql:49` | traced from source text |
| E157 | Routine `ops.enqueue_loop_notification` | `migrations/0736_loop_notification_queue.sql:58` | not established |
| E158 | Routine `ops.exchange_tour_share_token` | `migrations/0430_tour_delivery_data_plane.sql:674` | traced from source text |
| E159 | Routine `ops.execution_environment_provider_current_state` | `migrations/0309_governed_execution_environment_providers.sql:94` | traced from source text |
| E160 | Routine `ops.expire_canonical_ownership_leases` | `migrations/0450_canonical_ownership_lease_kernel.sql:1175` | not established |
| E161 | Routine `ops.f01_apply_observation` | `migrations/0626_f01_record_source_authority.sql:2403` | traced from source text |
| E162 | Routine `ops.f01_claim_idempotency` | `migrations/0626_f01_record_source_authority.sql:2152` | traced from source text |
| E163 | Routine `ops.f01_current_field_state` | `migrations/0626_f01_record_source_authority.sql:1723` | traced from source text |
| E164 | Routine `ops.f01_current_policy` | `migrations/0626_f01_record_source_authority.sql:1670` | traced from source text |
| E165 | Routine `ops.f01_derivative_links` | `migrations/0626_f01_record_source_authority.sql:1869` | traced from source text |
| E166 | Routine `ops.f01_document_source_history` | `migrations/0626_f01_record_source_authority.sql:4681` | not established |
| E167 | Routine `ops.f01_document_version_source` | `migrations/0626_f01_record_source_authority.sql:4636` | traced from source text |
| E168 | Routine `ops.f01_hold_inventory` | `migrations/0626_f01_record_source_authority.sql:1819` | traced from source text |
| E169 | Routine `ops.f01_insert_derivative_link` | `migrations/0626_f01_record_source_authority.sql:2883` | traced from source text |
| E170 | Routine `ops.f01_insert_document_source_provenance` | `migrations/0626_f01_record_source_authority.sql:4720` | traced from source text |
| E171 | Routine `ops.f01_install_policy` | `migrations/0626_f01_record_source_authority.sql:2304` | traced from source text |
| E172 | Routine `ops.f01_read` | `migrations/0626_f01_record_source_authority.sql:3538` | traced from source text |
| E173 | Routine `ops.f01_record_artifact` | `migrations/0626_f01_record_source_authority.sql:2658` | traced from source text |
| E174 | Routine `ops.f01_record_deletion_evaluation` | `migrations/0626_f01_record_source_authority.sql:3346` | traced from source text |
| E175 | Routine `ops.f01_record_document` | `migrations/0626_f01_record_source_authority.sql:4863` | traced from source text |
| E176 | Routine `ops.f01_record_hold` | `migrations/0626_f01_record_source_authority.sql:3249` | traced from source text |
| E177 | Routine `ops.f01_record_proposal` | `migrations/0626_f01_record_source_authority.sql:2753` | traced from source text |
| E178 | Routine `ops.f01_replay_outcome` | `migrations/0626_f01_record_source_authority.sql:2095` | traced from source text |
| E179 | Routine `ops.f01_retention_clock` | `migrations/0626_f01_record_source_authority.sql:2038` | traced from source text |
| E180 | Routine `ops.f01_settle_idempotency` | `migrations/0626_f01_record_source_authority.sql:2201` | traced from source text |
| E181 | Routine `ops.f01_stored_artifact` | `migrations/0626_f01_record_source_authority.sql:1750` | traced from source text |
| E182 | Routine `ops.f01_stored_artifact_by_identity` | `migrations/0626_f01_record_source_authority.sql:1785` | traced from source text |
| E183 | Routine `ops.f05_rule_universe` | `migrations/0724_f05_live_rule_context.sql:143` | traced from source text |
| E184 | Routine `ops.fail_job` | `migrations/0335_engineering_controller_currentness.sql:2031` | not established |
| E185 | Routine `ops.fence_definition_jobs` | `migrations/0169_control_plane_canary_fencing.sql:12` | not established |
| E186 | Routine `ops.foundation_assurance_accepted_manifest` | `migrations/0511_foundation_assurance_minimum_outcome.sql:337` | not established |
| E187 | Routine `ops.foundation_assurance_benchmark_review_material` | `migrations/0511_foundation_assurance_minimum_outcome.sql:323` | not established |
| E188 | Routine `ops.foundation_assurance_benchmark_session_guard` | `migrations/0511_foundation_assurance_minimum_outcome.sql:160` | not established |
| E189 | Routine `ops.foundation_assurance_identity_actor` | `migrations/0511_foundation_assurance_minimum_outcome.sql:113` | not established |
| E190 | Routine `ops.foundation_assurance_producer_material` | `migrations/0511_foundation_assurance_minimum_outcome.sql:357` | not established |
| E191 | Routine `ops.foundation_assurance_record_production` | `migrations/0514_foundation_assurance_null_replay_probe.sql:8` | not established |
| E192 | Routine `ops.foundation_assurance_store_evidence` | `migrations/0511_foundation_assurance_minimum_outcome.sql:267` | not established |
| E193 | Routine `ops.foundation_assurance_subject_maker` | `migrations/0511_foundation_assurance_minimum_outcome.sql:351` | not established |
| E194 | Routine `ops.gate_zero_producer_actor_id` | `migrations/0502_gate_zero_read_only_outcome.sql:299` | traced from source text |
| E195 | Routine `ops.gate_zero_record_read_only_outcome` | `migrations/0506_gate_zero_applied_0505_recovery.sql:62` | traced from source text |
| E196 | Routine `ops.get_cognition_cache` | `migrations/0153_control_plane_resilience.sql:67` | not established |
| E197 | Routine `ops.get_cognition_cache_for_job` | `migrations/0195_control_plane_cache_observations.sql:34` | not established |
| E198 | Routine `ops.guard_engineering_actor_authority_update` | `migrations/0335_engineering_controller_currentness.sql:45` | not established |
| E199 | Routine `ops.guard_engineering_envelope_supersession` | `migrations/0335_engineering_controller_currentness.sql:1841` | not established |
| E200 | Routine `ops.guard_engineering_job_definition_currentness_update` | `migrations/0335_engineering_controller_currentness.sql:100` | not established |
| E201 | Routine `ops.guard_engineering_reviewer_fact_insert` | `migrations/0335_engineering_controller_currentness.sql:970` | not established |
| E202 | Routine `ops.guard_engineering_session_terminalization` | `migrations/0325_engineering_claim_envelope_eligibility.sql:157` | not established |
| E203 | Routine `ops.guard_siep_engineering_evidence_binding` | `migrations/0335_engineering_controller_currentness.sql:1302` | not established |
| E204 | Routine `ops.guidance_revision_contract_hash` | `migrations/0168_guidance_registry.sql:138` | traced from source text |
| E205 | Routine `ops.heartbeat_job` | `migrations/0335_engineering_controller_currentness.sql:1986` | not established |
| E206 | Routine `ops.hermes_runtime_admission_for_brief` | `migrations/0309_governed_execution_environment_providers.sql:659` | not established |
| E207 | Routine `ops.hermes_runtime_admission_for_brief_v1` | `migrations/0856_qualify_security_definer_dependencies.sql:1667` | not established |
| E208 | Routine `ops.ingest_calendar_prebrief_canary_projection` | `migrations/0229_calendar_prebrief_projection.sql:748` | traced from source text |
| E209 | Routine `ops.ingest_calendar_prebrief_projection` | `migrations/0856_qualify_security_definer_dependencies.sql:1787` | traced from source text |
| E210 | Routine `ops.ingest_renewal_signed_snapshot` | `migrations/0856_qualify_security_definer_dependencies.sql:1999` | traced from source text |
| E211 | Routine `ops.initialize_cost_scope_tree` | `migrations/0519_producer_cost_ledger.sql:169` | not established |
| E212 | Routine `ops.invalidate_cognition_cache` | `migrations/0153_control_plane_resilience.sql:95` | not established |
| E213 | Routine `ops.invalidate_cognition_cache_for_job` | `migrations/0906_dot_database_design.sql:335` | not established |
| E214 | Routine `ops.issue_calendar_prebrief_capture_contract` | `migrations/0229_calendar_prebrief_projection.sql:427` | traced from source text |
| E215 | Routine `ops.issue_execution_envelope_v1` | `migrations/0309_governed_execution_environment_providers.sql:403` | not established |
| E216 | Routine `ops.issue_execution_envelope_v1_without_environment_gate` | `migrations/0856_qualify_security_definer_dependencies.sql:2041` | not established |
| E217 | Routine `ops.issue_tour_share_grant` | `migrations/0749_tour_client_feedback.sql:16` | traced from source text |
| E218 | Routine `ops.j102_apply_transition` | `migrations/0704_cre_lifecycle.sql:2741` | traced from source text |
| E219 | Routine `ops.j102_claim_idempotency` | `migrations/0704_cre_lifecycle.sql:1090` | traced from source text |
| E220 | Routine `ops.j102_evidence_subject_link` | `migrations/0704_cre_lifecycle.sql:964` | traced from source text |
| E221 | Routine `ops.j102_first_party_record` | `migrations/0704_cre_lifecycle.sql:935` | traced from source text |
| E222 | Routine `ops.j102_initialize_subject` | `migrations/0704_cre_lifecycle.sql:4100` | traced from source text |
| E223 | Routine `ops.j102_migration_readiness` | `migrations/0704_cre_lifecycle.sql:5775` | traced from source text |
| E224 | Routine `ops.j102_migration_shadow_snapshot` | `migrations/0704_cre_lifecycle.sql:5581` | traced from source text |
| E225 | Routine `ops.j102_read` | `migrations/0704_cre_lifecycle.sql:5838` | traced from source text |
| E226 | Routine `ops.j102_record_correction` | `migrations/0704_cre_lifecycle.sql:5022` | traced from source text |
| E227 | Routine `ops.j102_record_evidence_subject_link` | `migrations/0704_cre_lifecycle.sql:4855` | traced from source text |
| E228 | Routine `ops.j102_record_first_party_fact` | `migrations/0704_cre_lifecycle.sql:4770` | traced from source text |
| E229 | Routine `ops.j102_record_reconciliation_item` | `migrations/0704_cre_lifecycle.sql:5148` | traced from source text |
| E230 | Routine `ops.j102_record_salesforce_reference` | `migrations/0704_cre_lifecycle.sql:4947` | traced from source text |
| E231 | Routine `ops.j102_replay_outcome` | `migrations/0704_cre_lifecycle.sql:1045` | traced from source text |
| E232 | Routine `ops.j102_run_migration_shadow` | `migrations/0704_cre_lifecycle.sql:5605` | traced from source text |
| E233 | Routine `ops.j102_settle_idempotency` | `migrations/0704_cre_lifecycle.sql:1130` | traced from source text |
| E234 | Routine `ops.j102_subject` | `migrations/0704_cre_lifecycle.sql:893` | traced from source text |
| E235 | Routine `ops.j1_clock_append_guard` | `migrations/0509_journey_one_clock_store.sql:1244` | not established |
| E236 | Routine `ops.j1_clock_append_revision` | `migrations/0509_journey_one_clock_store.sql:1539` | not established |
| E237 | Routine `ops.j1_clock_bind_scope` | `migrations/0509_journey_one_clock_store.sql:1177` | not established |
| E238 | Routine `ops.j1_clock_child_frozen_after_next_revision` | `migrations/0509_journey_one_clock_store.sql:674` | not established |
| E239 | Routine `ops.j1_clock_head` | `migrations/0509_journey_one_clock_store.sql:1058` | not established |
| E240 | Routine `ops.j1_clock_history` | `migrations/0509_journey_one_clock_store.sql:1460` | not established |
| E241 | Routine `ops.j1_clock_history_state` | `migrations/0509_journey_one_clock_store.sql:890` | not established |
| E242 | Routine `ops.j1_clock_lock` | `migrations/0509_journey_one_clock_store.sql:1104` | not established |
| E243 | Routine `ops.j1_clock_revision_by_idempotency_key` | `migrations/0509_journey_one_clock_store.sql:1071` | not established |
| E244 | Routine `ops.j1_clock_revision_event_rows` | `migrations/0509_journey_one_clock_store.sql:843` | not established |
| E245 | Routine `ops.j1_clock_revision_integrity_error` | `migrations/0509_journey_one_clock_store.sql:906` | not established |
| E246 | Routine `ops.j1_clock_revision_json` | `migrations/0509_journey_one_clock_store.sql:1026` | not established |
| E247 | Routine `ops.j1_clock_revision_pause_interval_rows` | `migrations/0509_journey_one_clock_store.sql:833` | not established |
| E248 | Routine `ops.j1_clock_revision_scalars` | `migrations/0509_journey_one_clock_store.sql:796` | not established |
| E249 | Routine `ops.j1_clock_revisions` | `migrations/0509_journey_one_clock_store.sql:1082` | not established |
| E250 | Routine `ops.j1_clock_row` | `migrations/0509_journey_one_clock_store.sql:1000` | not established |
| E251 | Routine `ops.j1_clock_scope_binding_json` | `migrations/0509_journey_one_clock_store.sql:1142` | not established |
| E252 | Routine `ops.j1_clock_scope_bindings` | `migrations/0509_journey_one_clock_store.sql:1159` | not established |
| E253 | Routine `ops.j1_clock_scope_lock` | `migrations/0509_journey_one_clock_store.sql:1126` | not established |
| E254 | Routine `ops.j1_minimum_admission_by_idempotency_key` | `migrations/0510_journey_one_clock_input_store.sql:528` | not established |
| E255 | Routine `ops.j1_minimum_admission_json` | `migrations/0510_journey_one_clock_input_store.sql:460` | not established |
| E256 | Routine `ops.j1_minimum_admissions` | `migrations/0510_journey_one_clock_input_store.sql:518` | not established |
| E257 | Routine `ops.j1_minimum_append_admission` | `migrations/0510_journey_one_clock_input_store.sql:1011` | not established |
| E258 | Routine `ops.j1_minimum_append_guard` | `migrations/0510_journey_one_clock_input_store.sql:723` | not established |
| E259 | Routine `ops.j1_minimum_head` | `migrations/0510_journey_one_clock_input_store.sql:506` | not established |
| E260 | Routine `ops.j1_minimum_history` | `migrations/0510_journey_one_clock_input_store.sql:557` | not established |
| E261 | Routine `ops.j1_minimum_inventory_row` | `migrations/0510_journey_one_clock_input_store.sql:445` | not established |
| E262 | Routine `ops.j1_minimum_lock` | `migrations/0510_journey_one_clock_input_store.sql:425` | not established |
| E263 | Routine `ops.j1_minimum_open_inventory` | `migrations/0510_journey_one_clock_input_store.sql:663` | not established |
| E264 | Routine `ops.legacy_rule_admission_note` | `migrations/0351_legacy_rule_lifecycle_admission.sql:118` | traced from source text |
| E265 | Routine `ops.list_doc_conversations` | `migrations/0525_doc_conversation_list.sql:86` | traced from source text |
| E266 | Routine `ops.list_doc_suggestions` | `migrations/0744_doc_suggestions.sql:145` | traced from source text |
| E267 | Routine `ops.list_shipped_releases` | `migrations/0628_doctorcre_slice_done_marker.sql:792` | traced from source text |
| E268 | Routine `ops.list_tour_library` | `migrations/0430_tour_delivery_data_plane.sql:328` | traced from source text |
| E269 | Routine `ops.lock_cost_ledger_cell` | `migrations/0519_producer_cost_ledger.sql:206` | not established |
| E270 | Routine `ops.loop_notification_candidate` | `migrations/0736_loop_notification_queue.sql:34` | not established |
| E271 | Routine `ops.mark_slice_progress` | `migrations/0628_doctorcre_slice_done_marker.sql:1147` | traced from source text |
| E272 | Routine `ops.mark_whats_new_seen` | `migrations/0765_doc_whats_new.sql:36` | traced from source text |
| E273 | Routine `ops.meeting_facts` | `migrations/0556_meeting_mode_store.sql:871` | traced from source text |
| E274 | Routine `ops.meeting_mode_accept` | `migrations/0556_meeting_mode_store.sql:480` | traced from source text |
| E275 | Routine `ops.meeting_mode_action_view` | `migrations/0556_meeting_mode_store.sql:502` | traced from source text |
| E276 | Routine `ops.meeting_mode_actor` | `migrations/0556_meeting_mode_store.sql:230` | traced from source text |
| E277 | Routine `ops.meeting_mode_append` | `migrations/0556_meeting_mode_store.sql:243` | traced from source text |
| E278 | Routine `ops.mint_canonical_ownership_merge_session` | `migrations/0532a_canonical_ownership_lease_activation.sql:604` | not established |
| E279 | Routine `ops.mint_canonical_ownership_runtime_session` | `migrations/0532a_canonical_ownership_lease_activation.sql:366` | traced from source text |
| E280 | Routine `ops.mint_notification` | `migrations/0617_delivery_cadence_a05.sql:475` | traced from source text |
| E281 | Routine `ops.model_role_content_guard` | `migrations/0542_model_role_store.sql:948` | not established |
| E282 | Routine `ops.model_role_current_revision` | `migrations/0542_model_role_store.sql:1431` | not established |
| E283 | Routine `ops.model_role_pointer_guard` | `migrations/0542_model_role_store.sql:1256` | not established |
| E284 | Routine `ops.model_role_preimage` | `migrations/0542_model_role_store.sql:1018` | not established |
| E285 | Routine `ops.model_role_readback` | `migrations/0542_model_role_store.sql:1480` | not established |
| E286 | Routine `ops.model_role_record_revision` | `migrations/0542_model_role_store.sql:1550` | not established |
| E287 | Routine `ops.model_role_ref_array` | `migrations/0542_model_role_store.sql:1006` | not established |
| E288 | Routine `ops.model_role_revision_guard` | `migrations/0542_model_role_store.sql:1202` | not established |
| E289 | Routine `ops.model_role_revision_readback` | `migrations/0542_model_role_store.sql:1391` | not established |
| E290 | Routine `ops.model_role_set_current_revision` | `migrations/0542_model_role_store.sql:1633` | not established |
| E291 | Routine `ops.model_role_structure_error` | `migrations/0542_model_role_store.sql:1099` | not established |
| E292 | Routine `ops.model_role_text_array` | `migrations/0542_model_role_store.sql:989` | not established |
| E293 | Routine `ops.nightly_availability_canary_live_job` | `migrations/0227_nightly_availability_canary_record_layer.sql:33` | not established |
| E294 | Routine `ops.notification_feed_facts` | `migrations/0617_delivery_cadence_a05.sql:577` | traced from source text |
| E295 | Routine `ops.notification_morning_release_at` | `migrations/0617_delivery_cadence_a05.sql:429` | traced from source text |
| E296 | Routine `ops.notification_preference_facts` | `migrations/0617_delivery_cadence_a05.sql:364` | traced from source text |
| E297 | Routine `ops.notification_quiet_now` | `migrations/0617_delivery_cadence_a05.sql:330` | traced from source text |
| E298 | Routine `ops.open_workflow_cutover_plan` | `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:423` | traced from source text |
| E299 | Routine `ops.pending_slice_completion_proposals` | `migrations/0628_doctorcre_slice_done_marker.sql:1488` | traced from source text |
| E300 | Routine `ops.pending_sourced_work_request_outcome_feedback` | `migrations/0181_program6_pending_outcome_feedback_readback.sql:7` | traced from source text |
| E301 | Routine `ops.performance_receipt_requires_read_back` | `migrations/0172_program5_release_assurance.sql:238` | not established |
| E302 | Routine `ops.portfolio_accept_revision` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:1198` | traced from source text |
| E303 | Routine `ops.portfolio_acceptance_guard` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:739` | not established |
| E304 | Routine `ops.portfolio_accepted_preimage` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:518` | traced from source text |
| E305 | Routine `ops.portfolio_child_preimage` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:475` | traced from source text |
| E306 | Routine `ops.portfolio_current_accepted_revision` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:825` | traced from source text |
| E307 | Routine `ops.portfolio_descendant_binding` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:904` | traced from source text |
| E308 | Routine `ops.portfolio_graph_preimage` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:407` | traced from source text |
| E309 | Routine `ops.portfolio_propose_revision` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:1110` | traced from source text |
| E310 | Routine `ops.portfolio_readback` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:1029` | traced from source text |
| E311 | Routine `ops.portfolio_review_guard` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:685` | not established |
| E312 | Routine `ops.portfolio_review_revision` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:1177` | traced from source text |
| E313 | Routine `ops.portfolio_revision_complete` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:637` | not established |
| E314 | Routine `ops.portfolio_revision_integrity_error` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:841` | traced from source text |
| E315 | Routine `ops.portfolio_revision_structure_valid` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:561` | traced from source text |
| E316 | Routine `ops.portfolio_structure_frozen_after_acceptance` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:368` | not established |
| E317 | Routine `ops.portfolio_writer_actor_id` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:178` | traced from source text |
| E318 | Routine `ops.prepare_staging_deployment_attempt` | `migrations/0202_staging_release_readback_receipt.sql:305` | not established |
| E319 | Routine `ops.prepare_staging_forward_fix_rehearsal` | `migrations/0315_program5_forward_fix_rehearsal.sql:156` | not established |
| E320 | Routine `ops.prepare_staging_replacement_project` | `migrations/0322_clean_staging_replacement_contract.sql:97` | not established |
| E321 | Routine `ops.prepare_staging_restore_only_attempt` | `migrations/0297_restore_only_provider_uuid_repair.sql:16` | not established |
| E322 | Routine `ops.prepare_tour_route_version` | `migrations/0430_tour_delivery_data_plane.sql:379` | traced from source text |
| E323 | Routine `ops.program5_exact_recovery_rehearsal` | `migrations/0315_program5_forward_fix_rehearsal.sql:371` | not established |
| E324 | Routine `ops.program5_release_approval_is_joe_owned` | `migrations/0540_release_readiness_without_repeat_approval.sql:284` | not established |
| E325 | Routine `ops.propose_doc_correction` | `migrations/0744_doc_suggestions.sql:236` | traced from source text |
| E326 | Routine `ops.propose_eval_candidate` | `migrations/0303_evidence_activation_reliability.sql:1159` | not established |
| E327 | Routine `ops.propose_guidance_situation_mapping` | `migrations/0168_guidance_registry.sql:450` | not established |
| E328 | Routine `ops.propose_meeting_action` | `migrations/0556_meeting_mode_store.sql:540` | traced from source text |
| E329 | Routine `ops.propose_ready_plan_amendment` | `migrations/0890_retire_heavy_build_runtime.sql:260` | traced from source text |
| E330 | Routine `ops.propose_slice_completion` | `migrations/0628_doctorcre_slice_done_marker.sql:1275` | traced from source text |
| E331 | Routine `ops.propose_sourced_work_request_outcome_feedback` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:712` | traced from source text |
| E332 | Routine `ops.propose_sourced_work_request_plan` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:460` | traced from source text |
| E333 | Routine `ops.protect_staging_readback_deployment` | `migrations/0202_staging_release_readback_receipt.sql:264` | not established |
| E334 | Routine `ops.put_cognition_cache` | `migrations/0153_control_plane_resilience.sql:75` | not established |
| E335 | Routine `ops.put_cognition_cache_for_job` | `migrations/0906_dot_database_design.sql:289` | not established |
| E336 | Routine `ops.qualify_program5_release` | `migrations/0544_release_readiness_plan_hash_format.sql:13` | not established |
| E337 | Routine `ops.read_attempt_receipt_reliability` | `migrations/0303_evidence_activation_reliability.sql:1125` | traced from source text |
| E338 | Routine `ops.read_calendar_prebrief_joe_activation` | `migrations/0250_calendar_prebrief_joe_runtime.sql:33` | not established |
| E339 | Routine `ops.read_canonical_ownership_operation` | `migrations/0532a_canonical_ownership_lease_activation.sql:853` | not established |
| E340 | Routine `ops.read_context_activation` | `migrations/0303_evidence_activation_reliability.sql:769` | traced from source text |
| E341 | Routine `ops.read_doc_outcome_cards_successor` | `migrations/0546_read_doc_outcome_cards_successor.sql:4` | traced from source text |
| E342 | Routine `ops.read_execution_environment_providers` | `migrations/0309_governed_execution_environment_providers.sql:735` | traced from source text |
| E343 | Routine `ops.read_governance_queue` | `migrations/0856_qualify_security_definer_dependencies.sql:2146` | traced from source text |
| E344 | Routine `ops.read_resource_dashboard` | `migrations/0580_resource_observation.sql:213` | traced from source text |
| E345 | Routine `ops.read_slice_completion` | `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:1237` | traced from source text |
| E346 | Routine `ops.read_slice_done_state` | `migrations/0628_doctorcre_slice_done_marker.sql:1598` | traced from source text |
| E347 | Routine `ops.read_sourced_work_request_shape_disposition_lineage` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:1006` | traced from source text |
| E348 | Routine `ops.read_staging_forward_fix_rehearsal_declaration` | `migrations/0315_program5_forward_fix_rehearsal.sql:331` | not established |
| E349 | Routine `ops.read_staging_replacement_project_receipt` | `migrations/0322_clean_staging_replacement_contract.sql:369` | not established |
| E350 | Routine `ops.read_tour_feedback` | `migrations/0749_tour_client_feedback.sql:154` | traced from source text |
| E351 | Routine `ops.read_tour_internal_detail` | `migrations/0813_tour_internal_subject_read.sql:3` | traced from source text |
| E352 | Routine `ops.read_tour_packet_for_render` | `migrations/0608_tour_client_field_allowlist.sql:408` | traced from source text |
| E353 | Routine `ops.read_tour_pdf_artifact_for_download` | `migrations/0430_tour_delivery_data_plane.sql:813` | traced from source text |
| E354 | Routine `ops.read_tour_pdf_artifact_for_review` | `migrations/0430_tour_delivery_data_plane.sql:822` | traced from source text |
| E355 | Routine `ops.read_tour_pdf_render` | `migrations/0430_tour_delivery_data_plane.sql:830` | traced from source text |
| E356 | Routine `ops.read_tour_projection_creation_metadata` | `migrations/0430_tour_delivery_data_plane.sql:428` | traced from source text |
| E357 | Routine `ops.read_tour_projection_seal_candidates` | `migrations/0430_tour_delivery_data_plane.sql:440` | traced from source text |
| E358 | Routine `ops.read_tour_property_evidence` | `migrations/0754_tour_property_evidence.sql:4` | traced from source text |
| E359 | Routine `ops.read_tour_public_projection` | `migrations/0427_tour_rights_projection_hardening.sql:448` | traced from source text |
| E360 | Routine `ops.read_tour_selection_cart` | `migrations/0430_tour_delivery_data_plane.sql:322` | traced from source text |
| E361 | Routine `ops.read_tour_share_feedback` | `migrations/0749_tour_client_feedback.sql:132` | not established |
| E362 | Routine `ops.read_tour_share_map` | `migrations/0608_tour_client_field_allowlist.sql:443` | not established |
| E363 | Routine `ops.read_tour_share_packet` | `migrations/0608_tour_client_field_allowlist.sql:384` | not established |
| E364 | Routine `ops.read_tour_sharing_library` | `migrations/0430_tour_delivery_data_plane.sql:646` | traced from source text |
| E365 | Routine `ops.ready_plan_amendment_census` | `migrations/0532a_canonical_ownership_lease_activation.sql:1510` | traced from source text |
| E366 | Routine `ops.ready_plan_amendment_slice_plan_guard` | `migrations/0532a_canonical_ownership_lease_activation.sql:1102` | not established |
| E367 | Routine `ops.reap_expired_jobs` | `migrations/0164_control_plane_ledger_retry_modes.sql:15` | traced from source text |
| E368 | Routine `ops.reclassify_legacy_rule_admission` | `migrations/0856_qualify_security_definer_dependencies.sql:2185` | not established |
| E369 | Routine `ops.record_assurance_evidence_extension` | `migrations/0451_assurance_evidence_acceptance_persistence.sql:1030` | not established |
| E370 | Routine `ops.record_assurance_execution_manifest` | `migrations/0451_assurance_evidence_acceptance_persistence.sql:776` | not established |
| E371 | Routine `ops.record_assurance_health_evidence` | `migrations/0717_assurance_health_evidence_store.sql:140` | not established |
| E372 | Routine `ops.record_assurance_owner_acceptance` | `migrations/0484_assurance_owner_acceptance_practical_decision_window.sql:21` | not established |
| E373 | Routine `ops.record_assurance_review_extension` | `migrations/0451_assurance_evidence_acceptance_persistence.sql:1243` | not established |
| E374 | Routine `ops.record_attempt_receipt` | `migrations/0303_evidence_activation_reliability.sql:1010` | not established |
| E375 | Routine `ops.record_calendar_canary_receipt` | `migrations/0226_calendar_canary_record_layer.sql:42` | not established |
| E376 | Routine `ops.record_calendar_prebrief_verified_envelope` | `migrations/0229_calendar_prebrief_projection.sql:471` | traced from source text |
| E377 | Routine `ops.record_claude_scheduler_observation` | `migrations/0180_claude_scheduler_observation_receipt.sql:46` | not established |
| E378 | Routine `ops.record_device_evidence` | `migrations/0163_control_plane_device_evidence.sql:43` | not established |
| E379 | Routine `ops.record_dispatch_link` | `migrations/0531_room_dispatch_spine.sql:430` | traced from source text |
| E380 | Routine `ops.record_executed_lease` | `migrations/0856_qualify_security_definer_dependencies.sql:2273` | traced from source text |
| E381 | Routine `ops.record_guidance_decision` | `migrations/0856_qualify_security_definer_dependencies.sql:2359` | traced from source text |
| E382 | Routine `ops.record_launchd_scheduler_observation` | `migrations/0182_launchd_scheduler_observation_receipt.sql:28` | not established |
| E383 | Routine `ops.record_meeting_action_outcome` | `migrations/0556_meeting_mode_store.sql:770` | traced from source text |
| E384 | Routine `ops.record_nightly_availability_canary_receipt` | `migrations/0227_nightly_availability_canary_record_layer.sql:76` | not established |
| E385 | Routine `ops.record_npi_device_evidence` | `migrations/0167_control_plane_npi_device_evidence.sql:27` | not established |
| E386 | Routine `ops.record_program_controller_fact` | `migrations/0517_program_controller_seams.sql:153` | traced from source text |
| E387 | Routine `ops.record_provider_observation` | `migrations/0153_control_plane_resilience.sql:48` | not established |
| E388 | Routine `ops.record_release_slice_members` | `migrations/0628_doctorcre_slice_done_marker.sql:734` | traced from source text |
| E389 | Routine `ops.record_resource_observation` | `migrations/0580_resource_observation.sql:91` | traced from source text |
| E390 | Routine `ops.record_rule_enforcement_fallback` | `migrations/0721_a02_rule_enforcement_coverage.sql:61` | traced from source text |
| E391 | Routine `ops.record_rule_insert_provenance` | `migrations/0785_rule_teach_supersession.sql:22` | not established |
| E392 | Routine `ops.record_staging_forward_fix_rehearsal` | `migrations/0315_program5_forward_fix_rehearsal.sql:238` | not established |
| E393 | Routine `ops.record_staging_release_readback` | `migrations/0222_legacy_prior_staging_readback.sql:66` | not established |
| E394 | Routine `ops.record_staging_release_readback_program6` | `migrations/0218_staging_readback_program6_posture.sql:18` | not established |
| E395 | Routine `ops.record_staging_replacement_project` | `migrations/0322_clean_staging_replacement_contract.sql:221` | not established |
| E396 | Routine `ops.record_staging_restore_only_result` | `migrations/0295_staging_restore_only_recovery.sql:195` | not established |
| E397 | Routine `ops.record_tour_map_promotion_receipt` | `migrations/0430_tour_delivery_data_plane.sql:498` | traced from source text |
| E398 | Routine `ops.record_tour_pdf_human_review` | `migrations/0430_tour_delivery_data_plane.sql:838` | traced from source text |
| E399 | Routine `ops.record_tour_pdf_render_result` | `migrations/0583_tour_pdf_storage_ref_regex_repetition_bound.sql:34` | traced from source text |
| E400 | Routine `ops.record_workflow_acceptance` | `migrations/0856_qualify_security_definer_dependencies.sql:2441` | traced from source text |
| E401 | Routine `ops.record_workflow_caller` | `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:876` | traced from source text |
| E402 | Routine `ops.redeem_program6_browser_action_challenge` | `migrations/0183_program6_browser_action_challenge.sql:37` | traced from source text |
| E403 | Routine `ops.refuse_approved_rule_contract_rewrite` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:380` | not established |
| E404 | Routine `ops.refuse_live_approved_control_rewrite` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:407` | not established |
| E405 | Routine `ops.register_execution_environment_provider` | `migrations/0856_qualify_security_definer_dependencies.sql:2475` | not established |
| E406 | Routine `ops.register_slice_checkable_done` | `migrations/0628_doctorcre_slice_done_marker.sql:578` | traced from source text |
| E407 | Routine `ops.register_slice_criteria_from_catalog` | `migrations/0628_doctorcre_slice_done_marker.sql:520` | traced from source text |
| E408 | Routine `ops.register_tour_property` | `migrations/0565_tour_property_registration.sql:37` | traced from source text |
| E409 | Routine `ops.release_canonical_ownership_lease` | `migrations/0450_canonical_ownership_lease_kernel.sql:1154` | traced from source text |
| E410 | Routine `ops.release_completion_requires_a_read_back` | `migrations/0540_release_readiness_without_repeat_approval.sql:395` | not established |
| E411 | Routine `ops.release_job_cost` | `migrations/0154_control_plane_cost_release.sql:8` | not established |
| E412 | Routine `ops.release_readiness_is_typed` | `migrations/0540_release_readiness_without_repeat_approval.sql:99` | not established |
| E413 | Routine `ops.release_technical_readiness_current` | `migrations/0540_release_readiness_without_repeat_approval.sql:139` | not established |
| E414 | Routine `ops.rename_doc_conversation` | `migrations/0906_dot_database_design.sql:509` | traced from source text |
| E415 | Routine `ops.render_context_activation_for_brief` | `migrations/0303_evidence_activation_reliability.sql:837` | traced from source text |
| E416 | Routine `ops.renew_canonical_ownership_lease` | `migrations/0450_canonical_ownership_lease_kernel.sql:1131` | traced from source text |
| E417 | Routine `ops.reopen_program5_release_rehearsal` | `migrations/0540_release_readiness_without_repeat_approval.sql:160` | not established |
| E418 | Routine `ops.replace_calendar_prebrief_allowlist` | `migrations/0856_qualify_security_definer_dependencies.sql:2559` | not established |
| E419 | Routine `ops.request_tour_pdf_render` | `migrations/0430_tour_delivery_data_plane.sql:748` | traced from source text |
| E420 | Routine `ops.require_cutover_evidence` | `migrations/0149_control_plane_jobs.sql:441` | not established |
| E421 | Routine `ops.require_rule_admission` | `migrations/0886_active_rule_scope_backfill.sql:136` | not established |
| E422 | Routine `ops.require_rule_approval_lifecycle_anchor` | `migrations/0856_qualify_security_definer_dependencies.sql:2594` | not established |
| E423 | Routine `ops.reserve_job_cost` | `migrations/0906_dot_database_design.sql:217` | not established |
| E424 | Routine `ops.resolve_calendar_canary_receipt` | `migrations/0226_calendar_canary_record_layer.sql:54` | not established |
| E425 | Routine `ops.resolve_calendar_prebrief_email_ref` | `migrations/0856_qualify_security_definer_dependencies.sql:2652` | traced from source text |
| E426 | Routine `ops.resolve_nightly_availability_canary_receipt` | `migrations/0227_nightly_availability_canary_record_layer.sql:93` | not established |
| E427 | Routine `ops.resolve_tour_public_asset` | `migrations/0430_tour_delivery_data_plane.sql:741` | not established |
| E428 | Routine `ops.restore_tour_cheat_sheet_revision` | `migrations/0429_tour_domain_route_cheat_sheet.sql:206` | traced from source text |
| E429 | Routine `ops.retire_rule` | `migrations/0893_retire_rule_without_approval_receipt.sql:17` | traced from source text |
| E430 | Routine `ops.retire_superseded_rule` | `migrations/0785_rule_teach_supersession.sql:38` | traced from source text |
| E431 | Routine `ops.retire_workflow_cutover_plan` | `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:721` | traced from source text |
| E432 | Routine `ops.retired_rule_delivery_cleanup` | `migrations/0463_retired_rule_delivery_cleanup.sql:4` | not established |
| E433 | Routine `ops.retry_delay_seconds` | `migrations/0149_control_plane_jobs.sql:278` | traced from source text |
| E434 | Routine `ops.revoke_tour_rights_receipt` | `migrations/0427_tour_rights_projection_hardening.sql:376` | traced from source text |
| E435 | Routine `ops.revoke_tour_share_grant` | `migrations/0430_tour_delivery_data_plane.sql:636` | traced from source text |
| E436 | Routine `ops.rotate_tour_share_grant` | `migrations/0749_tour_client_feedback.sql:31` | traced from source text |
| E437 | Routine `ops.rule_amendment_reaches` | `migrations/0349_versioned_rule_amendment.sql:123` | traced from source text |
| E438 | Routine `ops.rule_delivery_audit_counts` | `migrations/0316_rule_delivery_audit_counts.sql:6` | traced from source text |
| E439 | Routine `ops.rule_delivery_cutover_preflight` | `migrations/0452_siep02_rule_delivery_authority.sql:24` | not established |
| E440 | Routine `ops.rule_delivery_plan` | `migrations/0291_rule_delivery_layers.sql:163` | traced from source text |
| E441 | Routine `ops.rule_pack_index` | `migrations/0291_rule_delivery_layers.sql:186` | traced from source text |
| E442 | Routine `ops.rw02_action_evidence` | `migrations/0726_salesforce_reconciliation_rw02_store.sql:241` | traced from source text |
| E443 | Routine `ops.rw02_attended_runs` | `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:166` | traced from source text |
| E444 | Routine `ops.rw02_consecutive_clean` | `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:94` | traced from source text |
| E445 | Routine `ops.rw02_consent_record` | `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:184` | traced from source text |
| E446 | Routine `ops.rw02_loop_episodes` | `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:262` | traced from source text |
| E447 | Routine `ops.rw02_record` | `migrations/0726_salesforce_reconciliation_rw02_store.sql:157` | traced from source text |
| E448 | Routine `ops.rw02_record_run` | `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:111` | traced from source text |
| E449 | Routine `ops.rw02_replay` | `migrations/0726_salesforce_reconciliation_rw02_store.sql:135` | traced from source text |
| E450 | Routine `ops.rw02_revoke_consent` | `migrations/0733_salesforce_rw02_safe_stop_run_store.sql:212` | traced from source text |
| E451 | Routine `ops.scac_admit_mutation` | `migrations/0467_siep18_atomic_db_monitor_grants.sql:511` | not established |
| E452 | Routine `ops.scac_artifact_integrity_state` | `migrations/0456_siep13_artifact_registry.sql:174` | not established |
| E453 | Routine `ops.scac_artifact_manifest_insert_guard` | `migrations/0456_siep13_artifact_registry.sql:80` | not established |
| E454 | Routine `ops.scac_artifact_root_binding_state` | `migrations/0458_siep14_root_trust.sql:255` | not established |
| E455 | Routine `ops.scac_artifact_signature_insert_guard` | `migrations/0456_siep13_artifact_registry.sql:98` | not established |
| E456 | Routine `ops.scac_artifact_transparency_insert_guard` | `migrations/0456_siep13_artifact_registry.sql:119` | not established |
| E457 | Routine `ops.scac_bind_trusted_principal` | `migrations/0469_siep18_exact_effects_trusted_principal.sql:226` | not established |
| E458 | Routine `ops.scac_capability_token_status` | `migrations/0465_siep17_token_challenge_authority.sql:493` | not established |
| E459 | Routine `ops.scac_consume_verified_pop_challenge` | `migrations/0465_siep17_token_challenge_authority.sql:366` | not established |
| E460 | Routine `ops.scac_device_benchmark_insert_guard` | `migrations/0460_siep15_device_enrollment.sql:163` | not established |
| E461 | Routine `ops.scac_device_enrollment_status` | `migrations/0460_siep15_device_enrollment.sql:179` | not established |
| E462 | Routine `ops.scac_device_fact_insert_guard` | `migrations/0460_siep15_device_enrollment.sql:149` | not established |
| E463 | Routine `ops.scac_doctrine_projection_digest` | `migrations/0455_siep12_policy_epoch.sql:58` | traced from source text |
| E464 | Routine `ops.scac_exact_effect_union` | `migrations/0469_siep18_exact_effects_trusted_principal.sql:178` | not established |
| E465 | Routine `ops.scac_issue_pop_challenge` | `migrations/0856_qualify_security_definer_dependencies.sql:2800` | not established |
| E466 | Routine `ops.scac_mutation_catalog_v118_current` | `migrations/0890_retire_heavy_build_runtime.sql:558` | traced from source text |
| E467 | Routine `ops.scac_mutation_catalog_v2_current` | `migrations/0457_siep13_forward_mutation_registry.sql:125` | traced from source text |
| E468 | Routine `ops.scac_mutation_catalog_v3_current` | `migrations/0532b_ready_plan_amendment_scac_successor.sql:210` | traced from source text |
| E469 | Routine `ops.scac_mutation_registration` | `migrations/0454_siep11_mutation_registry.sql:67` | not established |
| E470 | Routine `ops.scac_mutation_registration_v10` | `migrations/0471_source_merge_catalog_registry_successor.sql:66` | not established |
| E471 | Routine `ops.scac_mutation_registration_v100` | `migrations/0763_progress_directory_scac_successor.sql:25` | not established |
| E472 | Routine `ops.scac_mutation_registration_v101` | `migrations/0767_doc_whats_new_scac_successor.sql:29` | not established |
| E473 | Routine `ops.scac_mutation_registration_v102` | `migrations/0768_confirm_merge_human_only_scac_successor.sql:23` | not established |
| E474 | Routine `ops.scac_mutation_registration_v103` | `migrations/0786_find_rule_scac_successor.sql:25` | not established |
| E475 | Routine `ops.scac_mutation_registration_v104` | `migrations/0787_jev_cap_scac_successor.sql:25` | not established |
| E476 | Routine `ops.scac_mutation_registration_v105` | `migrations/0807_observatory_room_read_scac_successor.sql:23` | not established |
| E477 | Routine `ops.scac_mutation_registration_v106` | `migrations/0812_lead_automation_scac_successor.sql:25` | not established |
| E478 | Routine `ops.scac_mutation_registration_v107` | `migrations/0825_doc_activity_scac_successor.sql:23` | not established |
| E479 | Routine `ops.scac_mutation_registration_v108` | `migrations/0827_system_work_scac_successor.sql:25` | not established |
| E480 | Routine `ops.scac_mutation_registration_v109` | `migrations/0840_relationship_scac_successor.sql:25` | not established |
| E481 | Routine `ops.scac_mutation_registration_v11` | `migrations/0481_codex_continuity_registry_activation.sql:72` | not established |
| E482 | Routine `ops.scac_mutation_registration_v110` | `migrations/0842_invoice_tracker_scac_successor.sql:25` | not established |
| E483 | Routine `ops.scac_mutation_registration_v111` | `migrations/0844_automation_undo_scac_successor.sql:25` | not established |
| E484 | Routine `ops.scac_mutation_registration_v112` | `migrations/0846_leads_scac_successor.sql:25` | not established |
| E485 | Routine `ops.scac_mutation_registration_v113` | `migrations/0848_lead_score_owner_scac_successor.sql:17` | not established |
| E486 | Routine `ops.scac_mutation_registration_v114` | `migrations/0850_research_site_scac_successor.sql:17` | not established |
| E487 | Routine `ops.scac_mutation_registration_v115` | `migrations/0853_party_identity_scac_successor.sql:17` | not established |
| E488 | Routine `ops.scac_mutation_registration_v116` | `migrations/0857_dot_hardening_scac_successor.sql:19` | not established |
| E489 | Routine `ops.scac_mutation_registration_v117` | `migrations/0869_loop_residue_scac_successor.sql:17` | not established |
| E490 | Routine `ops.scac_mutation_registration_v118` | `migrations/0873_self_closing_records_scac_successor.sql:17` | traced from source text |
| E491 | Routine `ops.scac_mutation_registration_v12` | `migrations/0486_claude_continuity_registry_activation.sql:72` | not established |
| E492 | Routine `ops.scac_mutation_registration_v13` | `migrations/0487_claude_startup_registry_activation.sql:54` | not established |
| E493 | Routine `ops.scac_mutation_registration_v14` | `migrations/0488_claude_actor_hydration_registry_activation.sql:54` | not established |
| E494 | Routine `ops.scac_mutation_registration_v15` | `migrations/0489_claude_config_preservation_registry_activation.sql:54` | not established |
| E495 | Routine `ops.scac_mutation_registration_v16` | `migrations/0490_codex_compaction_checkpoint_registry_activation.sql:54` | not established |
| E496 | Routine `ops.scac_mutation_registration_v17` | `migrations/0491_backup_guard_status_registry_activation.sql:54` | not established |
| E497 | Routine `ops.scac_mutation_registration_v18` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:1093` | not established |
| E498 | Routine `ops.scac_mutation_registration_v19` | `migrations/0493_incident_work_request_link_scac_successor.sql:220` | not established |
| E499 | Routine `ops.scac_mutation_registration_v2` | `migrations/0455_siep12_policy_epoch.sql:324` | traced from source text |
| E500 | Routine `ops.scac_mutation_registration_v20` | `migrations/0494_codex_continuity_archive_registry.sql:55` | not established |
| E501 | Routine `ops.scac_mutation_registration_v21` | `migrations/0495_r06_hooks_correctness_scac_successor.sql:55` | not established |
| E502 | Routine `ops.scac_mutation_registration_v22` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:1248` | not established |
| E503 | Routine `ops.scac_mutation_registration_v23` | `migrations/0497_r07_repo_hygiene_janitor_and_scac_successor.sql:55` | not established |
| E504 | Routine `ops.scac_mutation_registration_v24` | `migrations/0498_f09_workflow_truth_and_scac_successor.sql:297` | not established |
| E505 | Routine `ops.scac_mutation_registration_v25` | `migrations/0501_scheduled_job_admission_and_scac_successor.sql:59` | not established |
| E506 | Routine `ops.scac_mutation_registration_v26` | `migrations/0503_gate_zero_outcome_and_scac_successor.sql:136` | not established |
| E507 | Routine `ops.scac_mutation_registration_v27` | `migrations/0512_foundation_assurance_scac_successor.sql:55` | not established |
| E508 | Routine `ops.scac_mutation_registration_v28` | `migrations/0516_deal_field_change_provenance_and_scac_successor.sql:55` | not established |
| E509 | Routine `ops.scac_mutation_registration_v29` | `migrations/0518_program_controller_seams_scac_successor.sql:57` | not established |
| E510 | Routine `ops.scac_mutation_registration_v3` | `migrations/0457_siep13_forward_mutation_registry.sql:11` | traced from source text |
| E511 | Routine `ops.scac_mutation_registration_v30` | `migrations/0522_producer_trio_scac_successor.sql:67` | not established |
| E512 | Routine `ops.scac_mutation_registration_v31` | `migrations/0524_doc_conversation_write_doors_scac_successor.sql:57` | not established |
| E513 | Routine `ops.scac_mutation_registration_v32` | `migrations/0526_doc_conversation_list_scac_successor.sql:57` | not established |
| E514 | Routine `ops.scac_mutation_registration_v33` | `migrations/0528_notification_preferences_scac_successor.sql:57` | not established |
| E515 | Routine `ops.scac_mutation_registration_v34` | `migrations/0530_session_identity_scac_successor.sql:57` | not established |
| E516 | Routine `ops.scac_mutation_registration_v35` | `migrations/0532_room_dispatch_spine_scac_successor.sql:58` | not established |
| E517 | Routine `ops.scac_mutation_registration_v36` | `migrations/0532b_ready_plan_amendment_scac_successor.sql:59` | not established |
| E518 | Routine `ops.scac_mutation_registration_v37` | `migrations/0539_canonical_ownership_assurance_scac_successor.sql:36` | not established |
| E519 | Routine `ops.scac_mutation_registration_v38` | `migrations/0541_release_readiness_scac_successor.sql:37` | not established |
| E520 | Routine `ops.scac_mutation_registration_v39` | `migrations/0543_model_role_store_scac_successor.sql:26` | not established |
| E521 | Routine `ops.scac_mutation_registration_v4` | `migrations/0459_siep14_forward_mutation_registry.sql:11` | not established |
| E522 | Routine `ops.scac_mutation_registration_v40` | `migrations/0545_release_readiness_hotfix_scac_successor.sql:23` | not established |
| E523 | Routine `ops.scac_mutation_registration_v41` | `migrations/0547_read_doc_outcome_cards_scac_successor.sql:23` | not established |
| E524 | Routine `ops.scac_mutation_registration_v42` | `migrations/0548_b09_atomic_migration_repair_scac_successor.sql:23` | not established |
| E525 | Routine `ops.scac_mutation_registration_v43` | `migrations/0549_jev_process_scac_successor.sql:23` | not established |
| E526 | Routine `ops.scac_mutation_registration_v44` | `migrations/0550_jev_hook_activation_scac_successor.sql:23` | not established |
| E527 | Routine `ops.scac_mutation_registration_v45` | `migrations/0551_deal_conflict_scac_successor.sql:23` | not established |
| E528 | Routine `ops.scac_mutation_registration_v46` | `migrations/0552_deal_undo_scac_successor.sql:23` | not established |
| E529 | Routine `ops.scac_mutation_registration_v47` | `migrations/0553_hermes_dispatch_link_scac_successor.sql:23` | not established |
| E530 | Routine `ops.scac_mutation_registration_v48` | `migrations/0555_rule_delivery_scac_successor.sql:23` | not established |
| E531 | Routine `ops.scac_mutation_registration_v49` | `migrations/0557_meeting_mode_scac_successor.sql:25` | not established |
| E532 | Routine `ops.scac_mutation_registration_v5` | `migrations/0461_siep15_forward_mutation_registry.sql:11` | not established |
| E533 | Routine `ops.scac_mutation_registration_v50` | `migrations/0558_machine_role_headroom_scac_successor.sql:23` | not established |
| E534 | Routine `ops.scac_mutation_registration_v51` | `migrations/0559_schema_snapshot_dedup_scac_successor.sql:23` | not established |
| E535 | Routine `ops.scac_mutation_registration_v52` | `migrations/0560_jev_handoff_scac_successor.sql:23` | not established |
| E536 | Routine `ops.scac_mutation_registration_v53` | `migrations/0561_openssl_resolution_scac_successor.sql:23` | not established |
| E537 | Routine `ops.scac_mutation_registration_v54` | `migrations/0562_machine_paths_scac_successor.sql:23` | not established |
| E538 | Routine `ops.scac_mutation_registration_v55` | `migrations/0563_logitech_keymap_optout_scac_successor.sql:23` | not established |
| E539 | Routine `ops.scac_mutation_registration_v56` | `migrations/0564_ci_split_scac_successor.sql:23` | not established |
| E540 | Routine `ops.scac_mutation_registration_v57` | `migrations/0566_tour_property_registration_scac_successor.sql:23` | not established |
| E541 | Routine `ops.scac_mutation_registration_v58` | `migrations/0567_tour_registration_atomic_pair_scac_successor.sql:23` | not established |
| E542 | Routine `ops.scac_mutation_registration_v59` | `migrations/0568_jev_combined_scac_successor.sql:23` | not established |
| E543 | Routine `ops.scac_mutation_registration_v6` | `migrations/0462_siep16_forward_mutation_registry.sql:11` | not established |
| E544 | Routine `ops.scac_mutation_registration_v60` | `migrations/0569_call_mode_origin_scac_successor.sql:23` | not established |
| E545 | Routine `ops.scac_mutation_registration_v61` | `migrations/0570_quill_speaker_labels_scac_successor.sql:23` | not established |
| E546 | Routine `ops.scac_mutation_registration_v62` | `migrations/0572_required_args_nullable_scac_successor.sql:23` | not established |
| E547 | Routine `ops.scac_mutation_registration_v63` | `migrations/0576_answer_needs_joe_work_request_scac_successor.sql:23` | not established |
| E548 | Routine `ops.scac_mutation_registration_v64` | `migrations/0578_answer_joe_atomic_pair_scac_successor.sql:23` | not established |
| E549 | Routine `ops.scac_mutation_registration_v65` | `migrations/0581_resource_observation_scac_successor.sql:23` | not established |
| E550 | Routine `ops.scac_mutation_registration_v66` | `migrations/0582_resource_observation_migrate_py_reseal.sql:23` | not established |
| E551 | Routine `ops.scac_mutation_registration_v67` | `migrations/0584_timebomb_audit_scac_successor.sql:23` | not established |
| E552 | Routine `ops.scac_mutation_registration_v68` | `migrations/0585_release_pipeline_scac_successor.sql:23` | not established |
| E553 | Routine `ops.scac_mutation_registration_v69` | `migrations/0588_jev_call_receipt_scac_successor.sql:23` | not established |
| E554 | Routine `ops.scac_mutation_registration_v7` | `migrations/0464_siep16_integrated_mutation_registry.sql:11` | not established |
| E555 | Routine `ops.scac_mutation_registration_v70` | `migrations/0589_jev_call_receipt_migrate_py_reseal.sql:23` | not established |
| E556 | Routine `ops.scac_mutation_registration_v71` | `migrations/0600_nightly_exports_daytime_retry_scac_successor.sql:23` | not established |
| E557 | Routine `ops.scac_mutation_registration_v72` | `migrations/0603_doctorcre_r02_scac_successor.sql:23` | not established |
| E558 | Routine `ops.scac_mutation_registration_v73` | `migrations/0609_deploy_worker_do_migration_reseal.sql:23` | not established |
| E559 | Routine `ops.scac_mutation_registration_v74` | `migrations/0614_journey_one_clock_door_scac_successor.sql:23` | not established |
| E560 | Routine `ops.scac_mutation_registration_v75` | `migrations/0618_delivery_cadence_a05_scac_successor.sql:23` | not established |
| E561 | Routine `ops.scac_mutation_registration_v76` | `migrations/0625_global_boundaries_door_scac_successor.sql:23` | not established |
| E562 | Routine `ops.scac_mutation_registration_v77` | `migrations/0627_f01_record_source_authority_scac_successor.sql:23` | not established |
| E563 | Routine `ops.scac_mutation_registration_v78` | `migrations/0629_doctorcre_slice_done_marker_scac_successor.sql:23` | not established |
| E564 | Routine `ops.scac_mutation_registration_v79` | `migrations/0701_governed_correspondence_scac_successor.sql:23` | not established |
| E565 | Routine `ops.scac_mutation_registration_v8` | `migrations/0466_siep17_forward_mutation_registry.sql:11` | not established |
| E566 | Routine `ops.scac_mutation_registration_v80` | `migrations/0705_cre_lifecycle_scac_successor.sql:23` | not established |
| E567 | Routine `ops.scac_mutation_registration_v81` | `migrations/0707_amend_closed_loop_scac_successor.sql:27` | not established |
| E568 | Routine `ops.scac_mutation_registration_v82` | `migrations/0709_action_class_successor_registry_scac_successor.sql:26` | not established |
| E569 | Routine `ops.scac_mutation_registration_v83` | `migrations/0718_assurance_health_evidence_store_scac_successor.sql:23` | not established |
| E570 | Routine `ops.scac_mutation_registration_v84` | `migrations/0720_doctorcre_a03_review_scac_successor.sql:23` | not established |
| E571 | Routine `ops.scac_mutation_registration_v85` | `migrations/0722_a02_rule_enforcement_coverage_scac_successor.sql:24` | not established |
| E572 | Routine `ops.scac_mutation_registration_v86` | `migrations/0723_session_trace_archive_scac_successor.sql:24` | not established |
| E573 | Routine `ops.scac_mutation_registration_v87` | `migrations/0725_f05_live_rule_context_scac_successor.sql:24` | not established |
| E574 | Routine `ops.scac_mutation_registration_v88` | `migrations/0727_salesforce_reconciliation_rw02_scac_successor.sql:24` | not established |
| E575 | Routine `ops.scac_mutation_registration_v89` | `migrations/0730_provision_engineering_controller_scac_successor.sql:23` | not established |
| E576 | Routine `ops.scac_mutation_registration_v9` | `migrations/0468_siep18_forward_mutation_registry.sql:26` | not established |
| E577 | Routine `ops.scac_mutation_registration_v90` | `migrations/0731_rule_boot_standing_context_scac_successor.sql:23` | not established |
| E578 | Routine `ops.scac_mutation_registration_v91` | `migrations/0734_salesforce_rw02_safe_stop_scac_successor.sql:23` | not established |
| E579 | Routine `ops.scac_mutation_registration_v92` | `migrations/0737_incident_triage_scac_successor.sql:32` | not established |
| E580 | Routine `ops.scac_mutation_registration_v93` | `migrations/0739_industry_events_scac_successor.sql:27` | not established |
| E581 | Routine `ops.scac_mutation_registration_v94` | `migrations/0741_board_answers_scac_successor.sql:25` | not established |
| E582 | Routine `ops.scac_mutation_registration_v95` | `migrations/0743_schedule_board_scac_successor.sql:25` | not established |
| E583 | Routine `ops.scac_mutation_registration_v96` | `migrations/0745_doc_suggestions_scac_successor.sql:25` | not established |
| E584 | Routine `ops.scac_mutation_registration_v97` | `migrations/0748_codex_session_read_scac_successor.sql:23` | not established |
| E585 | Routine `ops.scac_mutation_registration_v98` | `migrations/0750_tour_client_feedback_scac_successor.sql:25` | not established |
| E586 | Routine `ops.scac_mutation_registration_v99` | `migrations/0755_property_evidence_scac_successor.sql:25` | not established |
| E587 | Routine `ops.scac_mutation_registry_seal_valid` | `migrations/0873_self_closing_records_scac_successor.sql:261` | traced from source text |
| E588 | Routine `ops.scac_policy_epoch_chain_state` | `migrations/0873_self_closing_records_scac_successor.sql:632` | not established |
| E589 | Routine `ops.scac_policy_epoch_refresh` | `migrations/0476_settlement_scoped_epoch_refresh.sql:22` | traced from source text |
| E590 | Routine `ops.scac_policy_epoch_snapshot_v2` | `migrations/0455_siep12_policy_epoch.sql:158` | traced from source text |
| E591 | Routine `ops.scac_policy_epoch_status` | `migrations/0455_siep12_policy_epoch.sql:294` | not established |
| E592 | Routine `ops.scac_record_capability_token_receipt` | `migrations/0465_siep17_token_challenge_authority.sql:432` | not established |
| E593 | Routine `ops.scac_record_token_verification_binding` | `migrations/0467_siep18_atomic_db_monitor_grants.sql:365` | not established |
| E594 | Routine `ops.scac_reference_monitor_guard` | `migrations/0467_siep18_atomic_db_monitor_grants.sql:662` | not established |
| E595 | Routine `ops.scac_reference_monitor_mode` | `migrations/0467_siep18_atomic_db_monitor_grants.sql:213` | not established |
| E596 | Routine `ops.scac_reference_monitor_state` | `migrations/0873_self_closing_records_scac_successor.sql:784` | not established |
| E597 | Routine `ops.scac_register_exact_effect_contract` | `migrations/0469_siep18_exact_effects_trusted_principal.sql:54` | not established |
| E598 | Routine `ops.scac_register_operation_effect_binding` | `migrations/0467_siep18_atomic_db_monitor_grants.sql:442` | not established |
| E599 | Routine `ops.scac_register_token_issuer_binding` | `migrations/0467_siep18_atomic_db_monitor_grants.sql:322` | not established |
| E600 | Routine `ops.scac_revoke_token_subject` | `migrations/0465_siep17_token_challenge_authority.sql:224` | not established |
| E601 | Routine `ops.scac_root_attestation_insert_guard` | `migrations/0458_siep14_root_trust.sql:209` | not established |
| E602 | Routine `ops.scac_root_trust_chain_state` | `migrations/0458_siep14_root_trust.sql:113` | not established |
| E603 | Routine `ops.scac_root_trust_event_insert_guard` | `migrations/0458_siep14_root_trust.sql:168` | not established |
| E604 | Routine `ops.scac_token_control_snapshot` | `migrations/0465_siep17_token_challenge_authority.sql:161` | not established |
| E605 | Routine `ops.scac_transition_reference_monitor_mode` | `migrations/0467_siep18_atomic_db_monitor_grants.sql:706` | not established |
| E606 | Routine `ops.scac_transition_token_kill_switch` | `migrations/0465_siep17_token_challenge_authority.sql:191` | not established |
| E607 | Routine `ops.schedule_calendar_prebrief_joe_live_job` | `migrations/0307_calendar_prebrief_joe_schedule_time_and_failure.sql:32` | not established |
| E608 | Routine `ops.seal_renewal_decision_source_run` | `migrations/0856_qualify_security_definer_dependencies.sql:2956` | traced from source text |
| E609 | Routine `ops.seal_tour_public_projection` | `migrations/0430_tour_delivery_data_plane.sql:180` | traced from source text |
| E610 | Routine `ops.search_tour_properties` | `migrations/0430_tour_delivery_data_plane.sql:231` | traced from source text |
| E611 | Routine `ops.session_dispatch_history` | `migrations/0531_room_dispatch_spine.sql:194` | traced from source text |
| E612 | Routine `ops.session_identity_facts` | `migrations/0529_session_identity_reads.sql:98` | traced from source text |
| E613 | Routine `ops.set_notification_preference` | `migrations/0527_notification_preferences.sql:181` | traced from source text |
| E614 | Routine `ops.set_slice_mark_hold` | `migrations/0628_doctorcre_slice_done_marker.sql:1529` | traced from source text |
| E615 | Routine `ops.set_sourced_work_request_shape_disposition` | `migrations/0890_retire_heavy_build_runtime.sql:102` | traced from source text |
| E616 | Routine `ops.settle_job_cost` | `migrations/0906_dot_database_design.sql:260` | not established |
| E617 | Routine `ops.share_doc_conversation` | `migrations/0523_doc_conversation_write_doors.sql:127` | traced from source text |
| E618 | Routine `ops.siep_acquire_lane_lock` | `migrations/0324_siep_program_authority.sql:968` | not established |
| E619 | Routine `ops.siep_attach_evidence` | `migrations/0324_siep_program_authority.sql:652` | not established |
| E620 | Routine `ops.siep_bind_evidence_job` | `migrations/0335_engineering_controller_currentness.sql:1390` | not established |
| E621 | Routine `ops.siep_bind_evidence_job_unchecked_0324` | `migrations/0324_siep_program_authority.sql:592` | not established |
| E622 | Routine `ops.siep_claim_package` | `migrations/0324_siep_program_authority.sql:468` | not established |
| E623 | Routine `ops.siep_current_approval` | `migrations/0324_siep_program_authority.sql:557` | not established |
| E624 | Routine `ops.siep_current_evidence_digest` | `migrations/0335_engineering_controller_currentness.sql:1423` | not established |
| E625 | Routine `ops.siep_evidence_actor` | `migrations/0324_siep_program_authority.sql:529` | not established |
| E626 | Routine `ops.siep_manifest_digest` | `migrations/0324_siep_program_authority.sql:342` | not established |
| E627 | Routine `ops.siep_program_transition_guard` | `migrations/0324_siep_program_authority.sql:1099` | not established |
| E628 | Routine `ops.siep_read_evidence_graph` | `migrations/0453_siep06a_evidence_graph.sql:14` | not established |
| E629 | Routine `ops.siep_read_program` | `migrations/0324_siep_program_authority.sql:449` | not established |
| E630 | Routine `ops.siep_record_joe_decision` | `migrations/0324_siep_program_authority.sql:403` | not established |
| E631 | Routine `ops.siep_release_lane_lock` | `migrations/0324_siep_program_authority.sql:1035` | not established |
| E632 | Routine `ops.siep_resolve_package` | `migrations/0324_siep_program_authority.sql:364` | not established |
| E633 | Routine `ops.siep_terminal_status` | `migrations/0324_siep_program_authority.sql:778` | not established |
| E634 | Routine `ops.siep_transition_package` | `migrations/0324_siep_program_authority.sql:839` | not established |
| E635 | Routine `ops.skip_job` | `migrations/0383_control_plane_not_configured_state.sql:40` | not established |
| E636 | Routine `ops.slice_bind_insert` | `migrations/0628_doctorcre_slice_done_marker.sql:394` | not established |
| E637 | Routine `ops.slice_catalog_checkable_done` | `migrations/0628_doctorcre_slice_done_marker.sql:151` | traced from source text |
| E638 | Routine `ops.slice_completion_evaluate` | `migrations/0628_doctorcre_slice_done_marker.sql:1056` | traced from source text |
| E639 | Routine `ops.slice_effective_binding` | `migrations/0628_doctorcre_slice_done_marker.sql:346` | traced from source text |
| E640 | Routine `ops.slice_evidence_candidate` | `migrations/0628_doctorcre_slice_done_marker.sql:1021` | traced from source text |
| E641 | Routine `ops.slice_evidence_resolves` | `migrations/0628_doctorcre_slice_done_marker.sql:938` | traced from source text |
| E642 | Routine `ops.slice_mark_complete_insert` | `migrations/0628_doctorcre_slice_done_marker.sql:1210` | traced from source text |
| E643 | Routine `ops.slice_mark_held` | `migrations/0628_doctorcre_slice_done_marker.sql:869` | traced from source text |
| E644 | Routine `ops.slice_marker_seat_actor` | `migrations/0628_doctorcre_slice_done_marker.sql:119` | traced from source text |
| E645 | Routine `ops.slice_portfolio_acceptance_current` | `migrations/0628_doctorcre_slice_done_marker.sql:920` | traced from source text |
| E646 | Routine `ops.slice_portfolio_acceptance_effect_count` | `migrations/0628_doctorcre_slice_done_marker.sql:891` | traced from source text |
| E647 | Routine `ops.source_merge_authority_projection` | `migrations/0532a_canonical_ownership_lease_activation.sql:210` | traced from source text |
| E648 | Routine `ops.sourced_work_request_is_immutable` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:354` | not established |
| E649 | Routine `ops.sourced_work_request_outcome_feedback_preimage` | `migrations/0179_program6_sourced_outcome_feedback.sql:71` | traced from source text |
| E650 | Routine `ops.sourced_work_request_plan_preimage` | `migrations/0303_evidence_activation_reliability.sql:210` | traced from source text |
| E651 | Routine `ops.sourced_work_request_shape_disposition_lineage` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:84` | traced from source text |
| E652 | Routine `ops.sourced_work_shape_revision_requires_effective_required` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:977` | not established |
| E653 | Routine `ops.stage_guidance_import_batch` | `migrations/0856_qualify_security_definer_dependencies.sql:2992` | not established |
| E654 | Routine `ops.staging_release_approval_is_typed` | `migrations/0219_staging_release_approval_receipt.sql:51` | not established |
| E655 | Routine `ops.standing_guidance` | `migrations/0382_standing_guidance_reader_boundary.sql:16` | traced from source text |
| E656 | Routine `ops.start_meeting` | `migrations/0556_meeting_mode_store.sql:277` | traced from source text |
| E657 | Routine `ops.suggest_doc_work` | `migrations/0906_dot_database_design.sql:57` | traced from source text |
| E658 | Routine `ops.supersede_sourced_work_request` | `migrations/0888_supersede_triaged_work_request.sql:312` | traced from source text |
| E659 | Routine `ops.sync_system_rule_control_bindings` | `migrations/0856_qualify_security_definer_dependencies.sql:3073` | not established |
| E660 | Routine `ops.timeout_job` | `migrations/0335_engineering_controller_currentness.sql:2065` | not established |
| E661 | Routine `ops.tour_assertion_rights_guard` | `migrations/0427_tour_rights_projection_hardening.sql:88` | not established |
| E662 | Routine `ops.tour_canonical_projection_digest` | `migrations/0430_tour_delivery_data_plane.sql:147` | traced from source text |
| E663 | Routine `ops.tour_conflict_participant_guard` | `migrations/0318_tour_operations_foundation.sql:195` | not established |
| E664 | Routine `ops.tour_conflict_resolution_guard` | `migrations/0318_tour_operations_foundation.sql:200` | not established |
| E665 | Routine `ops.tour_membership_seal_guard` | `migrations/0318_tour_operations_foundation.sql:227` | not established |
| E666 | Routine `ops.tour_projection_creation_guard` | `migrations/0427_tour_rights_projection_hardening.sql:157` | not established |
| E667 | Routine `ops.tour_projection_fact_client_allowlist_guard` | `migrations/0608_tour_client_field_allowlist.sql:307` | not established |
| E668 | Routine `ops.tour_projection_fact_guard` | `migrations/0427_tour_rights_projection_hardening.sql:184` | not established |
| E669 | Routine `ops.tour_projection_seal_guard` | `migrations/0427_tour_rights_projection_hardening.sql:282` | not established |
| E670 | Routine `ops.tour_public_map_projection_evidence_ready` | `migrations/0430_tour_delivery_data_plane.sql:471` | traced from source text |
| E671 | Routine `ops.tour_public_map_projection_ready` | `migrations/0430_tour_delivery_data_plane.sql:591` | traced from source text |
| E672 | Routine `ops.tour_public_projection_client_safe` | `migrations/0608_tour_client_field_allowlist.sql:358` | traced from source text |
| E673 | Routine `ops.tour_publication_creation_guard` | `migrations/0427_tour_rights_projection_hardening.sql:169` | not established |
| E674 | Routine `ops.tour_rights_lineage_guard` | `migrations/0427_tour_rights_projection_hardening.sql:40` | not established |
| E675 | Routine `ops.tour_route_review_digest` | `migrations/0759_tour_reviewed_route_digest.sql:6` | traced from source text |
| E676 | Routine `ops.tour_share_feedback_target` | `migrations/0749_tour_client_feedback.sql:78` | not established |
| E677 | Routine `ops.tour_share_revocation_receipt_guard` | `migrations/0427_tour_rights_projection_hardening.sql:303` | not established |
| E678 | Routine `ops.tour_share_rotation_guard` | `migrations/0318_tour_operations_foundation.sql:205` | not established |
| E679 | Routine `ops.tour_share_session_grant` | `migrations/0430_tour_delivery_data_plane.sql:690` | not established |
| E680 | Routine `ops.tour_slice3_alias_guard` | `migrations/0428_tour_property_identity_jurisdiction.sql:280` | not established |
| E681 | Routine `ops.tour_slice3_entrance_receipt_guard` | `migrations/0428_tour_property_identity_jurisdiction.sql:344` | not established |
| E682 | Routine `ops.tour_slice3_identifier_guard` | `migrations/0428_tour_property_identity_jurisdiction.sql:271` | not established |
| E683 | Routine `ops.tour_slice3_jurisdiction_assertion_guard` | `migrations/0428_tour_property_identity_jurisdiction.sql:324` | not established |
| E684 | Routine `ops.tour_slice3_rights_guard` | `migrations/0428_tour_property_identity_jurisdiction.sql:249` | not established |
| E685 | Routine `ops.tour_source_rights_guard` | `migrations/0427_tour_rights_projection_hardening.sql:62` | not established |
| E686 | Routine `ops.transfer_studio_leader` | `migrations/0900_studio_failover_leader.sql:15` | traced from source text |
| E687 | Routine `ops.transition_execution_environment_provider` | `migrations/0856_qualify_security_definer_dependencies.sql:3222` | not established |
| E688 | Routine `ops.transition_proposed_eval_candidate` | `migrations/0856_qualify_security_definer_dependencies.sql:3266` | not established |
| E689 | Routine `ops.triage_sourced_work_request` | `migrations/0175_program6_human_sourced_triage.sql:84` | traced from source text |
| E690 | Routine `ops.v5_a02_rule_enforcement_coverage` | `migrations/0876_a02_fallback_rung_human_only.sql:66` | traced from source text |
| E691 | Routine `ops.v5_a03_assert_case_open` | `migrations/0719_doctorcre_a03_review_store.sql:323` | not established |
| E692 | Routine `ops.v5_a03_case_status` | `migrations/0719_doctorcre_a03_review_store.sql:311` | not established |
| E693 | Routine `ops.v5_a03_open_review_case` | `migrations/0719_doctorcre_a03_review_store.sql:349` | not established |
| E694 | Routine `ops.v5_a03_read_review_case` | `migrations/0719_doctorcre_a03_review_store.sql:657` | not established |
| E695 | Routine `ops.v5_a03_record_adjudication` | `migrations/0719_doctorcre_a03_review_store.sql:598` | not established |
| E696 | Routine `ops.v5_a03_record_finding_set` | `migrations/0719_doctorcre_a03_review_store.sql:440` | not established |
| E697 | Routine `ops.v5_a03_record_participant` | `migrations/0719_doctorcre_a03_review_store.sql:398` | not established |
| E698 | Routine `ops.v5_a03_round_subject_digest` | `migrations/0719_doctorcre_a03_review_store.sql:338` | not established |
| E699 | Routine `ops.v5_a03_seal_review_round` | `migrations/0719_doctorcre_a03_review_store.sql:484` | not established |
| E700 | Routine `ops.v5_a05_assurance_cadence_batch` | `migrations/0906_dot_database_design.sql:368` | traced from source text |
| E701 | Routine `ops.v5_a05_cadence_status` | `migrations/0617_delivery_cadence_a05.sql:123` | not established |
| E702 | Routine `ops.v5_a05_record_cadence_receipt` | `migrations/0617_delivery_cadence_a05.sql:262` | not established |
| E703 | Routine `ops.validate_attempt_environment_evidence` | `migrations/0309_governed_execution_environment_providers.sql:617` | not established |
| E704 | Routine `ops.validate_guidance_authority_binding` | `migrations/0168_guidance_registry.sql:272` | not established |
| E705 | Routine `ops.validate_guidance_lifecycle_event` | `migrations/0168_guidance_registry.sql:310` | not established |
| E706 | Routine `ops.validate_guidance_revision` | `migrations/0168_guidance_registry.sql:158` | not established |
| E707 | Routine `ops.validate_guidance_situation_mapping` | `migrations/0168_guidance_registry.sql:337` | not established |
| E708 | Routine `ops.validate_recovery_rehearsal_run` | `migrations/0315_program5_forward_fix_rehearsal.sql:347` | not established |
| E709 | Routine `ops.validate_rule_load_layer_packs` | `migrations/0291_rule_delivery_layers.sql:114` | not established |
| E710 | Routine `ops.whats_new_context` | `migrations/0765_doc_whats_new.sql:22` | traced from source text |
| E711 | Routine `ops.whats_new_partner_id` | `migrations/0765_doc_whats_new.sql:10` | traced from source text |
| E712 | Routine `ops.whats_new_section` | `migrations/0766_doc_whats_new_repair.sql:30` | traced from source text |
| E713 | Routine `ops.work_request_card` | `migrations/0577_work_request_card_admits_needs_joe.sql:92` | traced from source text |
| E714 | Routine `ops.work_request_completion_actor` | `migrations/0872_self_closing_records.sql:382` | traced from source text |
| E715 | Routine `ops.work_request_evidence_receipt_kind` | `migrations/0872_self_closing_records.sql:276` | traced from source text |
| E716 | Routine `ops.work_request_evidence_receipt_mismatch` | `migrations/0872_self_closing_records.sql:319` | traced from source text |
| E717 | Routine `ops.work_request_evidence_requires_live_request` | `migrations/0888_supersede_triaged_work_request.sql:132` | not established |
| E718 | Routine `ops.work_request_shape_gate` | `migrations/0132_work_shape_revision.sql:105` | not established |
| E719 | Routine `ops.work_request_supersession_strands` | `migrations/0888_supersede_triaged_work_request.sql:179` | traced from source text |
| E720 | Routine `ops.workflow_cutover_board` | `migrations/0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql:969` | traced from source text |
| E721 | Routine `ops.write_feature_switch` | `migrations/0902_feature_switches.sql:35` | traced from source text |
| E722 | Routine `ops.write_pitr_probe` | `migrations/0597_pitr_probe.sql:73` | not established |
| E723 | Routine `ops.write_tour_share_feedback` | `migrations/0749_tour_client_feedback.sql:98` | not established |
| E724 | Routine `public.action_class_successor_gate` | `migrations/0708_action_class_successor_registry.sql:128` | not established |
| E725 | Routine `public.assert_situation_retrieval_golden` | `migrations/0283_concept_scores_stop_being_free.sql:198` | traced from source text |
| E726 | Routine `public.campaign_channels_valid` | `migrations/0066_marketing_campaign_and_measurement.sql:317` | not established |
| E727 | Routine `public.capture_call_context` | `migrations/0094_call_mode_post_call.sql:79` | traced from source text |
| E728 | Routine `public.current_retrievable_doctrine` | `migrations/0135_situation_retrieval.sql:168` | traced from source text |
| E729 | Routine `public.enforce_allocation_tree` | `migrations/0906_dot_database_design.sql:697` | not established |
| E730 | Routine `public.log_retrieval_query` | `migrations/0856_qualify_security_definer_dependencies.sql:5` | traced from source text |
| E731 | Routine `public.loop_amendment_history` | `migrations/0706_amend_closed_loop.sql:87` | traced from source text |
| E732 | Routine `public.mark_retrieval_mappings_for_repair` | `migrations/0135_situation_retrieval.sql:332` | not established |
| E733 | Routine `public.memory_item_insert_valid` | `migrations/0856_qualify_security_definer_dependencies.sql:25` | not established |
| E734 | Routine `public.memory_item_plan_anchor_valid` | `migrations/0299_memory_kernel.sql:105` | not established |
| E735 | Routine `public.org_party_id` | `migrations/0059_org_identity.sql:251` | traced from source text |
| E736 | Routine `public.party_reference_counts` | `migrations/0852_party_identity_correction_view.sql:45` | traced from source text |
| E737 | Routine `public.promote_retrieval_proposal` | `migrations/0135_situation_retrieval.sql:434` | traced from source text |
| E738 | Routine `public.read_action_class_successors` | `migrations/0708_action_class_successor_registry.sql:103` | not established |
| E739 | Routine `public.resolve_memory_plan_anchor` | `migrations/0299_memory_kernel.sql:8` | traced from source text |
| E740 | Routine `public.retire_retrieval_curation` | `migrations/0135_situation_retrieval.sql:399` | traced from source text |
| E741 | Routine `public.retrieval_visibility_actor_id` | `migrations/0856_qualify_security_definer_dependencies.sql:3351` | traced from source text |
| E742 | Routine `public.search_doctrine_situations` | `migrations/0856_qualify_security_definer_dependencies.sql:3361` | traced from source text |
| E743 | Routine `public.trg_deal_participant_side` | `migrations/0906_dot_database_design.sql:409` | not established |
| E744 | Source `lib/r2_archive.py:104` | `lib/r2_archive.py:104` | direct source |
| E745 | Source `lib/r2_archive.py:192` | `lib/r2_archive.py:192` | direct source |
| E746 | Source `lib/record_sources.py:416` | `lib/record_sources.py:416` | direct source |
| E747 | Source `lib/record_sources.py:565` | `lib/record_sources.py:565` | direct source |
| E748 | Source `lib/record_sources.py:587` | `lib/record_sources.py:587` | direct source |
| E749 | Source `lib/record_sources.py:612` | `lib/record_sources.py:612` | direct source |
| E750 | Source `lib/rule_approval_worklist.py:80` | `lib/rule_approval_worklist.py:80` | direct source |
| E751 | Source `lib/studio_failover.py:112` | `lib/studio_failover.py:112` | direct source |
| E752 | Source `mcp-server/src/agent-profiles.js:108` | `mcp-server/src/agent-profiles.js:108` | direct source |
| E753 | Source `mcp-server/src/agent-profiles.js:119` | `mcp-server/src/agent-profiles.js:119` | direct source |
| E754 | Source `mcp-server/src/agent-profiles.js:62` | `mcp-server/src/agent-profiles.js:62` | direct source |
| E755 | Source `mcp-server/src/agent-profiles.js:99` | `mcp-server/src/agent-profiles.js:99` | direct source |
| E756 | Source `mcp-server/src/atlas-inventory-graph.v5.js:229` | `mcp-server/src/atlas-inventory-graph.v5.js:229` | direct source |
| E757 | Source `mcp-server/src/atlas-inventory-graph.v5.js:246` | `mcp-server/src/atlas-inventory-graph.v5.js:246` | direct source |
| E758 | Source `mcp-server/src/atlas-inventory-graph.v5.js:268` | `mcp-server/src/atlas-inventory-graph.v5.js:268` | direct source |
| E759 | Source `mcp-server/src/atlas-inventory-graph.v5.js:284` | `mcp-server/src/atlas-inventory-graph.v5.js:284` | direct source |
| E760 | Source `mcp-server/src/atlas-inventory-graph.v5.js:304` | `mcp-server/src/atlas-inventory-graph.v5.js:304` | direct source |
| E761 | Source `mcp-server/src/atlas-inventory-graph.v5.js:322` | `mcp-server/src/atlas-inventory-graph.v5.js:322` | direct source |
| E762 | Source `mcp-server/src/atlas-inventory-graph.v5.js:341` | `mcp-server/src/atlas-inventory-graph.v5.js:341` | direct source |
| E763 | Source `mcp-server/src/atlas-inventory-graph.v5.js:356` | `mcp-server/src/atlas-inventory-graph.v5.js:356` | direct source |
| E764 | Source `mcp-server/src/atlas-inventory-graph.v5.js:377` | `mcp-server/src/atlas-inventory-graph.v5.js:377` | direct source |
| E765 | Source `mcp-server/src/atlas-inventory-graph.v5.js:392` | `mcp-server/src/atlas-inventory-graph.v5.js:392` | direct source |
| E766 | Source `mcp-server/src/atlas-inventory-graph.v5.js:408` | `mcp-server/src/atlas-inventory-graph.v5.js:408` | direct source |
| E767 | Source `mcp-server/src/atlas-inventory-graph.v5.js:437` | `mcp-server/src/atlas-inventory-graph.v5.js:437` | direct source |
| E768 | Source `mcp-server/src/atlas-inventory-graph.v5.js:471` | `mcp-server/src/atlas-inventory-graph.v5.js:471` | direct source |
| E769 | Source `mcp-server/src/atlas-inventory-graph.v5.js:489` | `mcp-server/src/atlas-inventory-graph.v5.js:489` | direct source |
| E770 | Source `mcp-server/src/board-answers.js:123` | `mcp-server/src/board-answers.js:123` | direct source |
| E771 | Source `mcp-server/src/board-answers.js:128` | `mcp-server/src/board-answers.js:128` | direct source |
| E772 | Source `mcp-server/src/board-answers.js:158` | `mcp-server/src/board-answers.js:158` | direct source |
| E773 | Source `mcp-server/src/board-answers.js:189` | `mcp-server/src/board-answers.js:189` | direct source |
| E774 | Source `mcp-server/src/board-answers.js:195` | `mcp-server/src/board-answers.js:195` | direct source |
| E775 | Source `mcp-server/src/board-answers.js:215` | `mcp-server/src/board-answers.js:215` | direct source |
| E776 | Source `mcp-server/src/board-answers.js:231` | `mcp-server/src/board-answers.js:231` | direct source |
| E777 | Source `mcp-server/src/board-answers.js:235` | `mcp-server/src/board-answers.js:235` | direct source |
| E778 | Source `mcp-server/src/board-answers.js:265` | `mcp-server/src/board-answers.js:265` | direct source |
| E779 | Source `mcp-server/src/board-answers.js:275` | `mcp-server/src/board-answers.js:275` | direct source |
| E780 | Source `mcp-server/src/board-answers.js:305` | `mcp-server/src/board-answers.js:305` | direct source |
| E781 | Source `mcp-server/src/board-answers.js:329` | `mcp-server/src/board-answers.js:329` | direct source |
| E782 | Source `mcp-server/src/board-answers.js:358` | `mcp-server/src/board-answers.js:358` | direct source |
| E783 | Source `mcp-server/src/capability-program.js:117` | `mcp-server/src/capability-program.js:117` | direct source |
| E784 | Source `mcp-server/src/capability-program.js:137` | `mcp-server/src/capability-program.js:137` | direct source |
| E785 | Source `mcp-server/src/capability-program.js:163` | `mcp-server/src/capability-program.js:163` | direct source |
| E786 | Source `mcp-server/src/capability-program.js:329` | `mcp-server/src/capability-program.js:329` | direct source |
| E787 | Source `mcp-server/src/capability-program.js:332` | `mcp-server/src/capability-program.js:332` | direct source |
| E788 | Source `mcp-server/src/capability-program.js:333` | `mcp-server/src/capability-program.js:333` | direct source |
| E789 | Source `mcp-server/src/capability-program.js:335` | `mcp-server/src/capability-program.js:335` | direct source |
| E790 | Source `mcp-server/src/capability-program.js:404` | `mcp-server/src/capability-program.js:404` | direct source |
| E791 | Source `mcp-server/src/capability-program.js:409` | `mcp-server/src/capability-program.js:409` | direct source |
| E792 | Source `mcp-server/src/capability-program.js:410` | `mcp-server/src/capability-program.js:410` | direct source |
| E793 | Source `mcp-server/src/capability-program.js:75` | `mcp-server/src/capability-program.js:75` | direct source |
| E794 | Source `mcp-server/src/capability-program.js:82` | `mcp-server/src/capability-program.js:82` | direct source |
| E795 | Source `mcp-server/src/capture.js:105` | `mcp-server/src/capture.js:105` | direct source |
| E796 | Source `mcp-server/src/capture.js:107` | `mcp-server/src/capture.js:107` | direct source |
| E797 | Source `mcp-server/src/capture.js:112` | `mcp-server/src/capture.js:112` | direct source |
| E798 | Source `mcp-server/src/capture.js:133` | `mcp-server/src/capture.js:133` | direct source |
| E799 | Source `mcp-server/src/capture.js:160` | `mcp-server/src/capture.js:160` | direct source |
| E800 | Source `mcp-server/src/capture.js:177` | `mcp-server/src/capture.js:177` | direct source |
| E801 | Source `mcp-server/src/capture.js:276` | `mcp-server/src/capture.js:276` | direct source |
| E802 | Source `mcp-server/src/capture.js:288` | `mcp-server/src/capture.js:288` | direct source |
| E803 | Source `mcp-server/src/capture.js:294` | `mcp-server/src/capture.js:294` | direct source |
| E804 | Source `mcp-server/src/capture.js:303` | `mcp-server/src/capture.js:303` | direct source |
| E805 | Source `mcp-server/src/capture.js:325` | `mcp-server/src/capture.js:325` | direct source |
| E806 | Source `mcp-server/src/capture.js:343` | `mcp-server/src/capture.js:343` | direct source |
| E807 | Source `mcp-server/src/capture.js:348` | `mcp-server/src/capture.js:348` | direct source |
| E808 | Source `mcp-server/src/capture.js:363` | `mcp-server/src/capture.js:363` | direct source |
| E809 | Source `mcp-server/src/capture.js:413` | `mcp-server/src/capture.js:413` | direct source |
| E810 | Source `mcp-server/src/capture.js:424` | `mcp-server/src/capture.js:424` | direct source |
| E811 | Source `mcp-server/src/capture.js:434` | `mcp-server/src/capture.js:434` | direct source |
| E812 | Source `mcp-server/src/capture.js:448` | `mcp-server/src/capture.js:448` | direct source |
| E813 | Source `mcp-server/src/codex-continuity.js:311` | `mcp-server/src/codex-continuity.js:311` | direct source |
| E814 | Source `mcp-server/src/codex-continuity.js:317` | `mcp-server/src/codex-continuity.js:317` | direct source |
| E815 | Source `mcp-server/src/codex-continuity.js:347` | `mcp-server/src/codex-continuity.js:347` | direct source |
| E816 | Source `mcp-server/src/codex-continuity.js:387` | `mcp-server/src/codex-continuity.js:387` | direct source |
| E817 | Source `mcp-server/src/codex-continuity.js:401` | `mcp-server/src/codex-continuity.js:401` | direct source |
| E818 | Source `mcp-server/src/codex-continuity.js:411` | `mcp-server/src/codex-continuity.js:411` | direct source |
| E819 | Source `mcp-server/src/codex-continuity.js:448` | `mcp-server/src/codex-continuity.js:448` | direct source |
| E820 | Source `mcp-server/src/codex-continuity.js:485` | `mcp-server/src/codex-continuity.js:485` | direct source |
| E821 | Source `mcp-server/src/codex-continuity.js:492` | `mcp-server/src/codex-continuity.js:492` | direct source |
| E822 | Source `mcp-server/src/cre-lifecycle-store.v5.js:3134` | `mcp-server/src/cre-lifecycle-store.v5.js:3134` | direct source |
| E823 | Source `mcp-server/src/deal-checklist.js:110` | `mcp-server/src/deal-checklist.js:110` | direct source |
| E824 | Source `mcp-server/src/deal-items.js:108` | `mcp-server/src/deal-items.js:108` | direct source |
| E825 | Source `mcp-server/src/deal-items.js:145` | `mcp-server/src/deal-items.js:145` | direct source |
| E826 | Source `mcp-server/src/deal-items.js:164` | `mcp-server/src/deal-items.js:164` | direct source |
| E827 | Source `mcp-server/src/doc-activity.js:92` | `mcp-server/src/doc-activity.js:92` | direct source |
| E828 | Source `mcp-server/src/doc-activity.js:94` | `mcp-server/src/doc-activity.js:94` | direct source |
| E829 | Source `mcp-server/src/doctrine.js:1010` | `mcp-server/src/doctrine.js:1010` | direct source |
| E830 | Source `mcp-server/src/doctrine.js:1041` | `mcp-server/src/doctrine.js:1041` | direct source |
| E831 | Source `mcp-server/src/doctrine.js:1077` | `mcp-server/src/doctrine.js:1077` | direct source |
| E832 | Source `mcp-server/src/doctrine.js:112` | `mcp-server/src/doctrine.js:112` | direct source |
| E833 | Source `mcp-server/src/doctrine.js:119` | `mcp-server/src/doctrine.js:119` | direct source |
| E834 | Source `mcp-server/src/doctrine.js:1214` | `mcp-server/src/doctrine.js:1214` | direct source |
| E835 | Source `mcp-server/src/doctrine.js:1248` | `mcp-server/src/doctrine.js:1248` | direct source |
| E836 | Source `mcp-server/src/doctrine.js:181` | `mcp-server/src/doctrine.js:181` | direct source |
| E837 | Source `mcp-server/src/doctrine.js:194` | `mcp-server/src/doctrine.js:194` | direct source |
| E838 | Source `mcp-server/src/doctrine.js:220` | `mcp-server/src/doctrine.js:220` | direct source |
| E839 | Source `mcp-server/src/doctrine.js:228` | `mcp-server/src/doctrine.js:228` | direct source |
| E840 | Source `mcp-server/src/doctrine.js:237` | `mcp-server/src/doctrine.js:237` | direct source |
| E841 | Source `mcp-server/src/doctrine.js:240` | `mcp-server/src/doctrine.js:240` | direct source |
| E842 | Source `mcp-server/src/doctrine.js:261` | `mcp-server/src/doctrine.js:261` | direct source |
| E843 | Source `mcp-server/src/doctrine.js:275` | `mcp-server/src/doctrine.js:275` | direct source |
| E844 | Source `mcp-server/src/doctrine.js:302` | `mcp-server/src/doctrine.js:302` | direct source |
| E845 | Source `mcp-server/src/doctrine.js:307` | `mcp-server/src/doctrine.js:307` | direct source |
| E846 | Source `mcp-server/src/doctrine.js:375` | `mcp-server/src/doctrine.js:375` | direct source |
| E847 | Source `mcp-server/src/doctrine.js:380` | `mcp-server/src/doctrine.js:380` | direct source |
| E848 | Source `mcp-server/src/doctrine.js:409` | `mcp-server/src/doctrine.js:409` | direct source |
| E849 | Source `mcp-server/src/doctrine.js:425` | `mcp-server/src/doctrine.js:425` | direct source |
| E850 | Source `mcp-server/src/doctrine.js:428` | `mcp-server/src/doctrine.js:428` | direct source |
| E851 | Source `mcp-server/src/doctrine.js:432` | `mcp-server/src/doctrine.js:432` | direct source |
| E852 | Source `mcp-server/src/doctrine.js:435` | `mcp-server/src/doctrine.js:435` | direct source |
| E853 | Source `mcp-server/src/doctrine.js:441` | `mcp-server/src/doctrine.js:441` | direct source |
| E854 | Source `mcp-server/src/doctrine.js:448` | `mcp-server/src/doctrine.js:448` | direct source |
| E855 | Source `mcp-server/src/doctrine.js:453` | `mcp-server/src/doctrine.js:453` | direct source |
| E856 | Source `mcp-server/src/doctrine.js:488` | `mcp-server/src/doctrine.js:488` | direct source |
| E857 | Source `mcp-server/src/doctrine.js:491` | `mcp-server/src/doctrine.js:491` | direct source |
| E858 | Source `mcp-server/src/doctrine.js:494` | `mcp-server/src/doctrine.js:494` | direct source |
| E859 | Source `mcp-server/src/doctrine.js:499` | `mcp-server/src/doctrine.js:499` | direct source |
| E860 | Source `mcp-server/src/doctrine.js:531` | `mcp-server/src/doctrine.js:531` | direct source |
| E861 | Source `mcp-server/src/doctrine.js:555` | `mcp-server/src/doctrine.js:555` | direct source |
| E862 | Source `mcp-server/src/doctrine.js:557` | `mcp-server/src/doctrine.js:557` | direct source |
| E863 | Source `mcp-server/src/doctrine.js:582` | `mcp-server/src/doctrine.js:582` | direct source |
| E864 | Source `mcp-server/src/doctrine.js:602` | `mcp-server/src/doctrine.js:602` | direct source |
| E865 | Source `mcp-server/src/doctrine.js:63` | `mcp-server/src/doctrine.js:63` | direct source |
| E866 | Source `mcp-server/src/doctrine.js:662` | `mcp-server/src/doctrine.js:662` | direct source |
| E867 | Source `mcp-server/src/doctrine.js:697` | `mcp-server/src/doctrine.js:697` | direct source |
| E868 | Source `mcp-server/src/doctrine.js:71` | `mcp-server/src/doctrine.js:71` | direct source |
| E869 | Source `mcp-server/src/doctrine.js:710` | `mcp-server/src/doctrine.js:710` | direct source |
| E870 | Source `mcp-server/src/doctrine.js:731` | `mcp-server/src/doctrine.js:731` | direct source |
| E871 | Source `mcp-server/src/doctrine.js:738` | `mcp-server/src/doctrine.js:738` | direct source |
| E872 | Source `mcp-server/src/doctrine.js:74` | `mcp-server/src/doctrine.js:74` | direct source |
| E873 | Source `mcp-server/src/doctrine.js:759` | `mcp-server/src/doctrine.js:759` | direct source |
| E874 | Source `mcp-server/src/doctrine.js:790` | `mcp-server/src/doctrine.js:790` | direct source |
| E875 | Source `mcp-server/src/doctrine.js:802` | `mcp-server/src/doctrine.js:802` | direct source |
| E876 | Source `mcp-server/src/doctrine.js:811` | `mcp-server/src/doctrine.js:811` | direct source |
| E877 | Source `mcp-server/src/doctrine.js:826` | `mcp-server/src/doctrine.js:826` | direct source |
| E878 | Source `mcp-server/src/doctrine.js:829` | `mcp-server/src/doctrine.js:829` | direct source |
| E879 | Source `mcp-server/src/doctrine.js:833` | `mcp-server/src/doctrine.js:833` | direct source |
| E880 | Source `mcp-server/src/doctrine.js:838` | `mcp-server/src/doctrine.js:838` | direct source |
| E881 | Source `mcp-server/src/doctrine.js:842` | `mcp-server/src/doctrine.js:842` | direct source |
| E882 | Source `mcp-server/src/doctrine.js:849` | `mcp-server/src/doctrine.js:849` | direct source |
| E883 | Source `mcp-server/src/doctrine.js:859` | `mcp-server/src/doctrine.js:859` | direct source |
| E884 | Source `mcp-server/src/doctrine.js:86` | `mcp-server/src/doctrine.js:86` | direct source |
| E885 | Source `mcp-server/src/doctrine.js:880` | `mcp-server/src/doctrine.js:880` | direct source |
| E886 | Source `mcp-server/src/doctrine.js:891` | `mcp-server/src/doctrine.js:891` | direct source |
| E887 | Source `mcp-server/src/doctrine.js:907` | `mcp-server/src/doctrine.js:907` | direct source |
| E888 | Source `mcp-server/src/doctrine.js:98` | `mcp-server/src/doctrine.js:98` | direct source |
| E889 | Source `mcp-server/src/engineering-runtime.js:1787` | `mcp-server/src/engineering-runtime.js:1787` | direct source |
| E890 | Source `mcp-server/src/engineering-runtime.js:1805` | `mcp-server/src/engineering-runtime.js:1805` | direct source |
| E891 | Source `mcp-server/src/engineering-runtime.js:1822` | `mcp-server/src/engineering-runtime.js:1822` | direct source |
| E892 | Source `mcp-server/src/engineering-runtime.js:1829` | `mcp-server/src/engineering-runtime.js:1829` | direct source |
| E893 | Source `mcp-server/src/engineering-runtime.js:1839` | `mcp-server/src/engineering-runtime.js:1839` | direct source |
| E894 | Source `mcp-server/src/engineering-runtime.js:1840` | `mcp-server/src/engineering-runtime.js:1840` | direct source |
| E895 | Source `mcp-server/src/engineering-runtime.js:1941` | `mcp-server/src/engineering-runtime.js:1941` | direct source |
| E896 | Source `mcp-server/src/engineering-runtime.js:1946` | `mcp-server/src/engineering-runtime.js:1946` | direct source |
| E897 | Source `mcp-server/src/engineering-runtime.js:1962` | `mcp-server/src/engineering-runtime.js:1962` | direct source |
| E898 | Source `mcp-server/src/engineering-runtime.js:1971` | `mcp-server/src/engineering-runtime.js:1971` | direct source |
| E899 | Source `mcp-server/src/engineering-runtime.js:1987` | `mcp-server/src/engineering-runtime.js:1987` | direct source |
| E900 | Source `mcp-server/src/engineering-runtime.js:2021` | `mcp-server/src/engineering-runtime.js:2021` | direct source |
| E901 | Source `mcp-server/src/engineering-runtime.js:2050` | `mcp-server/src/engineering-runtime.js:2050` | direct source |
| E902 | Source `mcp-server/src/engineering-runtime.js:2207` | `mcp-server/src/engineering-runtime.js:2207` | direct source |
| E903 | Source `mcp-server/src/engineering-runtime.js:2228` | `mcp-server/src/engineering-runtime.js:2228` | direct source |
| E904 | Source `mcp-server/src/feature-switches.js:139` | `mcp-server/src/feature-switches.js:139` | direct source |
| E905 | Source `mcp-server/src/feature-switches.js:141` | `mcp-server/src/feature-switches.js:141` | direct source |
| E906 | Source `mcp-server/src/feature-switches.js:21` | `mcp-server/src/feature-switches.js:21` | direct source |
| E907 | Source `mcp-server/src/feature-switches.js:58` | `mcp-server/src/feature-switches.js:58` | direct source |
| E908 | Source `mcp-server/src/feature-switches.js:89` | `mcp-server/src/feature-switches.js:89` | direct source |
| E909 | Source `mcp-server/src/feature-switches.js:94` | `mcp-server/src/feature-switches.js:94` | direct source |
| E910 | Source `mcp-server/src/incident.js:1019` | `mcp-server/src/incident.js:1019` | direct source |
| E911 | Source `mcp-server/src/incident.js:1027` | `mcp-server/src/incident.js:1027` | direct source |
| E912 | Source `mcp-server/src/incident.js:1039` | `mcp-server/src/incident.js:1039` | direct source |
| E913 | Source `mcp-server/src/incident.js:1044` | `mcp-server/src/incident.js:1044` | direct source |
| E914 | Source `mcp-server/src/incident.js:1056` | `mcp-server/src/incident.js:1056` | direct source |
| E915 | Source `mcp-server/src/incident.js:1069` | `mcp-server/src/incident.js:1069` | direct source |
| E916 | Source `mcp-server/src/incident.js:1128` | `mcp-server/src/incident.js:1128` | direct source |
| E917 | Source `mcp-server/src/incident.js:1134` | `mcp-server/src/incident.js:1134` | direct source |
| E918 | Source `mcp-server/src/incident.js:1176` | `mcp-server/src/incident.js:1176` | direct source |
| E919 | Source `mcp-server/src/incident.js:1191` | `mcp-server/src/incident.js:1191` | direct source |
| E920 | Source `mcp-server/src/incident.js:1199` | `mcp-server/src/incident.js:1199` | direct source |
| E921 | Source `mcp-server/src/incident.js:1265` | `mcp-server/src/incident.js:1265` | direct source |
| E922 | Source `mcp-server/src/incident.js:1278` | `mcp-server/src/incident.js:1278` | direct source |
| E923 | Source `mcp-server/src/incident.js:1330` | `mcp-server/src/incident.js:1330` | direct source |
| E924 | Source `mcp-server/src/incident.js:1353` | `mcp-server/src/incident.js:1353` | direct source |
| E925 | Source `mcp-server/src/incident.js:1357` | `mcp-server/src/incident.js:1357` | direct source |
| E926 | Source `mcp-server/src/incident.js:496` | `mcp-server/src/incident.js:496` | direct source |
| E927 | Source `mcp-server/src/incident.js:600` | `mcp-server/src/incident.js:600` | direct source |
| E928 | Source `mcp-server/src/incident.js:614` | `mcp-server/src/incident.js:614` | direct source |
| E929 | Source `mcp-server/src/incident.js:620` | `mcp-server/src/incident.js:620` | direct source |
| E930 | Source `mcp-server/src/incident.js:626` | `mcp-server/src/incident.js:626` | direct source |
| E931 | Source `mcp-server/src/incident.js:630` | `mcp-server/src/incident.js:630` | direct source |
| E932 | Source `mcp-server/src/incident.js:658` | `mcp-server/src/incident.js:658` | direct source |
| E933 | Source `mcp-server/src/incident.js:683` | `mcp-server/src/incident.js:683` | direct source |
| E934 | Source `mcp-server/src/incident.js:696` | `mcp-server/src/incident.js:696` | direct source |
| E935 | Source `mcp-server/src/incident.js:706` | `mcp-server/src/incident.js:706` | direct source |
| E936 | Source `mcp-server/src/incident.js:760` | `mcp-server/src/incident.js:760` | direct source |
| E937 | Source `mcp-server/src/incident.js:811` | `mcp-server/src/incident.js:811` | direct source |
| E938 | Source `mcp-server/src/incident.js:814` | `mcp-server/src/incident.js:814` | direct source |
| E939 | Source `mcp-server/src/incident.js:817` | `mcp-server/src/incident.js:817` | direct source |
| E940 | Source `mcp-server/src/incident.js:819` | `mcp-server/src/incident.js:819` | direct source |
| E941 | Source `mcp-server/src/incident.js:822` | `mcp-server/src/incident.js:822` | direct source |
| E942 | Source `mcp-server/src/incident.js:824` | `mcp-server/src/incident.js:824` | direct source |
| E943 | Source `mcp-server/src/incident.js:844` | `mcp-server/src/incident.js:844` | direct source |
| E944 | Source `mcp-server/src/incident.js:925` | `mcp-server/src/incident.js:925` | direct source |
| E945 | Source `mcp-server/src/incident.js:959` | `mcp-server/src/incident.js:959` | direct source |
| E946 | Source `mcp-server/src/incident.js:987` | `mcp-server/src/incident.js:987` | direct source |
| E947 | Source `mcp-server/src/incident.js:998` | `mcp-server/src/incident.js:998` | direct source |
| E948 | Source `mcp-server/src/index.js:185` | `mcp-server/src/index.js:185` | direct source |
| E949 | Source `mcp-server/src/investigation.js:131` | `mcp-server/src/investigation.js:131` | direct source |
| E950 | Source `mcp-server/src/investigation.js:187` | `mcp-server/src/investigation.js:187` | direct source |
| E951 | Source `mcp-server/src/investigation.js:200` | `mcp-server/src/investigation.js:200` | direct source |
| E952 | Source `mcp-server/src/investigation.js:271` | `mcp-server/src/investigation.js:271` | direct source |
| E953 | Source `mcp-server/src/investigation.js:276` | `mcp-server/src/investigation.js:276` | direct source |
| E954 | Source `mcp-server/src/investigation.js:282` | `mcp-server/src/investigation.js:282` | direct source |
| E955 | Source `mcp-server/src/investigation.js:285` | `mcp-server/src/investigation.js:285` | direct source |
| E956 | Source `mcp-server/src/investigation.js:305` | `mcp-server/src/investigation.js:305` | direct source |
| E957 | Source `mcp-server/src/investigation.js:309` | `mcp-server/src/investigation.js:309` | direct source |
| E958 | Source `mcp-server/src/investigation.js:332` | `mcp-server/src/investigation.js:332` | direct source |
| E959 | Source `mcp-server/src/investigation.js:344` | `mcp-server/src/investigation.js:344` | direct source |
| E960 | Source `mcp-server/src/investigation.js:350` | `mcp-server/src/investigation.js:350` | direct source |
| E961 | Source `mcp-server/src/investigation.js:382` | `mcp-server/src/investigation.js:382` | direct source |
| E962 | Source `mcp-server/src/investigation.js:390` | `mcp-server/src/investigation.js:390` | direct source |
| E963 | Source `mcp-server/src/investigation.js:419` | `mcp-server/src/investigation.js:419` | direct source |
| E964 | Source `mcp-server/src/investigation.js:430` | `mcp-server/src/investigation.js:430` | direct source |
| E965 | Source `mcp-server/src/investigation.js:439` | `mcp-server/src/investigation.js:439` | direct source |
| E966 | Source `mcp-server/src/investigation.js:460` | `mcp-server/src/investigation.js:460` | direct source |
| E967 | Source `mcp-server/src/investigation.js:489` | `mcp-server/src/investigation.js:489` | direct source |
| E968 | Source `mcp-server/src/investigation.js:495` | `mcp-server/src/investigation.js:495` | direct source |
| E969 | Source `mcp-server/src/investigation.js:500` | `mcp-server/src/investigation.js:500` | direct source |
| E970 | Source `mcp-server/src/investigation.js:87` | `mcp-server/src/investigation.js:87` | direct source |
| E971 | Source `mcp-server/src/invoice-automation.js:102` | `mcp-server/src/invoice-automation.js:102` | direct source |
| E972 | Source `mcp-server/src/invoice-automation.js:123` | `mcp-server/src/invoice-automation.js:123` | direct source |
| E973 | Source `mcp-server/src/invoice-automation.js:134` | `mcp-server/src/invoice-automation.js:134` | direct source |
| E974 | Source `mcp-server/src/invoice-automation.js:143` | `mcp-server/src/invoice-automation.js:143` | direct source |
| E975 | Source `mcp-server/src/invoice-automation.js:146` | `mcp-server/src/invoice-automation.js:146` | direct source |
| E976 | Source `mcp-server/src/invoice-automation.js:147` | `mcp-server/src/invoice-automation.js:147` | direct source |
| E977 | Source `mcp-server/src/invoice-automation.js:153` | `mcp-server/src/invoice-automation.js:153` | direct source |
| E978 | Source `mcp-server/src/invoice-automation.js:36` | `mcp-server/src/invoice-automation.js:36` | direct source |
| E979 | Source `mcp-server/src/invoice-automation.js:49` | `mcp-server/src/invoice-automation.js:49` | direct source |
| E980 | Source `mcp-server/src/invoice-automation.js:58` | `mcp-server/src/invoice-automation.js:58` | direct source |
| E981 | Source `mcp-server/src/invoice-automation.js:72` | `mcp-server/src/invoice-automation.js:72` | direct source |
| E982 | Source `mcp-server/src/invoice-automation.js:74` | `mcp-server/src/invoice-automation.js:74` | direct source |
| E983 | Source `mcp-server/src/invoice-automation.js:76` | `mcp-server/src/invoice-automation.js:76` | direct source |
| E984 | Source `mcp-server/src/invoice-automation.js:97` | `mcp-server/src/invoice-automation.js:97` | direct source |
| E985 | Source `mcp-server/src/invoice-tracker.js:35` | `mcp-server/src/invoice-tracker.js:35` | direct source |
| E986 | Source `mcp-server/src/invoice-tracker.js:45` | `mcp-server/src/invoice-tracker.js:45` | direct source |
| E987 | Source `mcp-server/src/lead-automation.js:100` | `mcp-server/src/lead-automation.js:100` | direct source |
| E988 | Source `mcp-server/src/lead-automation.js:107` | `mcp-server/src/lead-automation.js:107` | direct source |
| E989 | Source `mcp-server/src/lead-automation.js:112` | `mcp-server/src/lead-automation.js:112` | direct source |
| E990 | Source `mcp-server/src/lead-automation.js:131` | `mcp-server/src/lead-automation.js:131` | direct source |
| E991 | Source `mcp-server/src/lead-automation.js:143` | `mcp-server/src/lead-automation.js:143` | direct source |
| E992 | Source `mcp-server/src/lead-automation.js:150` | `mcp-server/src/lead-automation.js:150` | direct source |
| E993 | Source `mcp-server/src/lead-automation.js:158` | `mcp-server/src/lead-automation.js:158` | direct source |
| E994 | Source `mcp-server/src/lead-automation.js:168` | `mcp-server/src/lead-automation.js:168` | direct source |
| E995 | Source `mcp-server/src/lead-automation.js:198` | `mcp-server/src/lead-automation.js:198` | direct source |
| E996 | Source `mcp-server/src/lead-automation.js:208` | `mcp-server/src/lead-automation.js:208` | direct source |
| E997 | Source `mcp-server/src/lead-automation.js:238` | `mcp-server/src/lead-automation.js:238` | direct source |
| E998 | Source `mcp-server/src/lead-automation.js:241` | `mcp-server/src/lead-automation.js:241` | direct source |
| E999 | Source `mcp-server/src/lead-automation.js:250` | `mcp-server/src/lead-automation.js:250` | direct source |
| E1000 | Source `mcp-server/src/lead-automation.js:254` | `mcp-server/src/lead-automation.js:254` | direct source |
| E1001 | Source `mcp-server/src/lead-automation.js:264` | `mcp-server/src/lead-automation.js:264` | direct source |
| E1002 | Source `mcp-server/src/lead-automation.js:268` | `mcp-server/src/lead-automation.js:268` | direct source |
| E1003 | Source `mcp-server/src/lead-automation.js:269` | `mcp-server/src/lead-automation.js:269` | direct source |
| E1004 | Source `mcp-server/src/lead-automation.js:279` | `mcp-server/src/lead-automation.js:279` | direct source |
| E1005 | Source `mcp-server/src/lead-automation.js:282` | `mcp-server/src/lead-automation.js:282` | direct source |
| E1006 | Source `mcp-server/src/lead-automation.js:289` | `mcp-server/src/lead-automation.js:289` | direct source |
| E1007 | Source `mcp-server/src/lead-automation.js:300` | `mcp-server/src/lead-automation.js:300` | direct source |
| E1008 | Source `mcp-server/src/lead-automation.js:302` | `mcp-server/src/lead-automation.js:302` | direct source |
| E1009 | Source `mcp-server/src/lead-automation.js:310` | `mcp-server/src/lead-automation.js:310` | direct source |
| E1010 | Source `mcp-server/src/lead-workspace.js:41` | `mcp-server/src/lead-workspace.js:41` | direct source |
| E1011 | Source `mcp-server/src/lead-workspace.js:43` | `mcp-server/src/lead-workspace.js:43` | direct source |
| E1012 | Source `mcp-server/src/lead-workspace.js:47` | `mcp-server/src/lead-workspace.js:47` | direct source |
| E1013 | Source `mcp-server/src/mcp.js:525` | `mcp-server/src/mcp.js:525` | direct source |
| E1014 | Source `mcp-server/src/mcp.js:556` | `mcp-server/src/mcp.js:556` | direct source |
| E1015 | Source `mcp-server/src/mcp.js:817` | `mcp-server/src/mcp.js:817` | direct source |
| E1016 | Source `mcp-server/src/memory.js:104` | `mcp-server/src/memory.js:104` | direct source |
| E1017 | Source `mcp-server/src/memory.js:116` | `mcp-server/src/memory.js:116` | direct source |
| E1018 | Source `mcp-server/src/memory.js:123` | `mcp-server/src/memory.js:123` | direct source |
| E1019 | Source `mcp-server/src/memory.js:150` | `mcp-server/src/memory.js:150` | direct source |
| E1020 | Source `mcp-server/src/memory.js:176` | `mcp-server/src/memory.js:176` | direct source |
| E1021 | Source `mcp-server/src/memory.js:196` | `mcp-server/src/memory.js:196` | direct source |
| E1022 | Source `mcp-server/src/memory.js:217` | `mcp-server/src/memory.js:217` | direct source |
| E1023 | Source `mcp-server/src/memory.js:224` | `mcp-server/src/memory.js:224` | direct source |
| E1024 | Source `mcp-server/src/memory.js:229` | `mcp-server/src/memory.js:229` | direct source |
| E1025 | Source `mcp-server/src/memory.js:247` | `mcp-server/src/memory.js:247` | direct source |
| E1026 | Source `mcp-server/src/needs-joe.js:192` | `mcp-server/src/needs-joe.js:192` | direct source |
| E1027 | Source `mcp-server/src/needs-joe.js:203` | `mcp-server/src/needs-joe.js:203` | direct source |
| E1028 | Source `mcp-server/src/needs-joe.js:220` | `mcp-server/src/needs-joe.js:220` | direct source |
| E1029 | Source `mcp-server/src/needs-joe.js:256` | `mcp-server/src/needs-joe.js:256` | direct source |
| E1030 | Source `mcp-server/src/partner-room.js:209` | `mcp-server/src/partner-room.js:209` | direct source |
| E1031 | Source `mcp-server/src/partner-room.js:217` | `mcp-server/src/partner-room.js:217` | direct source |
| E1032 | Source `mcp-server/src/partner-room.js:305` | `mcp-server/src/partner-room.js:305` | direct source |
| E1033 | Source `mcp-server/src/party-identity.js:167` | `mcp-server/src/party-identity.js:167` | direct source |
| E1034 | Source `mcp-server/src/party-identity.js:221` | `mcp-server/src/party-identity.js:221` | direct source |
| E1035 | Source `mcp-server/src/party-identity.js:251` | `mcp-server/src/party-identity.js:251` | direct source |
| E1036 | Source `mcp-server/src/party-identity.js:263` | `mcp-server/src/party-identity.js:263` | direct source |
| E1037 | Source `mcp-server/src/party-identity.js:272` | `mcp-server/src/party-identity.js:272` | direct source |
| E1038 | Source `mcp-server/src/party-identity.js:283` | `mcp-server/src/party-identity.js:283` | direct source |
| E1039 | Source `mcp-server/src/party-identity.js:293` | `mcp-server/src/party-identity.js:293` | direct source |
| E1040 | Source `mcp-server/src/party-identity.js:303` | `mcp-server/src/party-identity.js:303` | direct source |
| E1041 | Source `mcp-server/src/party-identity.js:315` | `mcp-server/src/party-identity.js:315` | direct source |
| E1042 | Source `mcp-server/src/party-identity.js:324` | `mcp-server/src/party-identity.js:324` | direct source |
| E1043 | Source `mcp-server/src/party-identity.js:326` | `mcp-server/src/party-identity.js:326` | direct source |
| E1044 | Source `mcp-server/src/program-controller-census.v5.js:120` | `mcp-server/src/program-controller-census.v5.js:120` | direct source |
| E1045 | Source `mcp-server/src/program-controller-census.v5.js:126` | `mcp-server/src/program-controller-census.v5.js:126` | direct source |
| E1046 | Source `mcp-server/src/program-controller-census.v5.js:133` | `mcp-server/src/program-controller-census.v5.js:133` | direct source |
| E1047 | Source `mcp-server/src/program-controller-census.v5.js:136` | `mcp-server/src/program-controller-census.v5.js:136` | direct source |
| E1048 | Source `mcp-server/src/program-controller-census.v5.js:141` | `mcp-server/src/program-controller-census.v5.js:141` | direct source |
| E1049 | Source `mcp-server/src/program-controller-census.v5.js:149` | `mcp-server/src/program-controller-census.v5.js:149` | direct source |
| E1050 | Source `mcp-server/src/relationship-network.js:10` | `mcp-server/src/relationship-network.js:10` | direct source |
| E1051 | Source `mcp-server/src/relationship-network.js:104` | `mcp-server/src/relationship-network.js:104` | direct source |
| E1052 | Source `mcp-server/src/relationship-network.js:105` | `mcp-server/src/relationship-network.js:105` | direct source |
| E1053 | Source `mcp-server/src/relationship-network.js:108` | `mcp-server/src/relationship-network.js:108` | direct source |
| E1054 | Source `mcp-server/src/relationship-network.js:18` | `mcp-server/src/relationship-network.js:18` | direct source |
| E1055 | Source `mcp-server/src/relationship-network.js:93` | `mcp-server/src/relationship-network.js:93` | direct source |
| E1056 | Source `mcp-server/src/relationship-network.js:99` | `mcp-server/src/relationship-network.js:99` | direct source |
| E1057 | Source `mcp-server/src/release.js:140` | `mcp-server/src/release.js:140` | direct source |
| E1058 | Source `mcp-server/src/research-sites.js:132` | `mcp-server/src/research-sites.js:132` | direct source |
| E1059 | Source `mcp-server/src/research-sites.js:139` | `mcp-server/src/research-sites.js:139` | direct source |
| E1060 | Source `mcp-server/src/research-sites.js:167` | `mcp-server/src/research-sites.js:167` | direct source |
| E1061 | Source `mcp-server/src/research-sites.js:173` | `mcp-server/src/research-sites.js:173` | direct source |
| E1062 | Source `mcp-server/src/research-sites.js:196` | `mcp-server/src/research-sites.js:196` | direct source |
| E1063 | Source `mcp-server/src/schedule-board.js:8` | `mcp-server/src/schedule-board.js:8` | direct source |
| E1064 | Source `mcp-server/src/situation-retrieval.js:101` | `mcp-server/src/situation-retrieval.js:101` | direct source |
| E1065 | Source `mcp-server/src/situation-retrieval.js:109` | `mcp-server/src/situation-retrieval.js:109` | direct source |
| E1066 | Source `mcp-server/src/situation-retrieval.js:307` | `mcp-server/src/situation-retrieval.js:307` | direct source |
| E1067 | Source `mcp-server/src/system-work-census.v5.js:100` | `mcp-server/src/system-work-census.v5.js:100` | direct source |
| E1068 | Source `mcp-server/src/system-work-census.v5.js:109` | `mcp-server/src/system-work-census.v5.js:109` | direct source |
| E1069 | Source `mcp-server/src/system-work-census.v5.js:14` | `mcp-server/src/system-work-census.v5.js:14` | direct source |
| E1070 | Source `mcp-server/src/system-work-census.v5.js:19` | `mcp-server/src/system-work-census.v5.js:19` | direct source |
| E1071 | Source `mcp-server/src/system-work-census.v5.js:194` | `mcp-server/src/system-work-census.v5.js:194` | direct source |
| E1072 | Source `mcp-server/src/system-work-census.v5.js:204` | `mcp-server/src/system-work-census.v5.js:204` | direct source |
| E1073 | Source `mcp-server/src/system-work-census.v5.js:27` | `mcp-server/src/system-work-census.v5.js:27` | direct source |
| E1074 | Source `mcp-server/src/system-work-census.v5.js:290` | `mcp-server/src/system-work-census.v5.js:290` | direct source |
| E1075 | Source `mcp-server/src/system-work-census.v5.js:40` | `mcp-server/src/system-work-census.v5.js:40` | direct source |
| E1076 | Source `mcp-server/src/system-work-census.v5.js:45` | `mcp-server/src/system-work-census.v5.js:45` | direct source |
| E1077 | Source `mcp-server/src/system-work-census.v5.js:60` | `mcp-server/src/system-work-census.v5.js:60` | direct source |
| E1078 | Source `mcp-server/src/system-work-census.v5.js:65` | `mcp-server/src/system-work-census.v5.js:65` | direct source |
| E1079 | Source `mcp-server/src/system-work-census.v5.js:69` | `mcp-server/src/system-work-census.v5.js:69` | direct source |
| E1080 | Source `mcp-server/src/system-work-census.v5.js:77` | `mcp-server/src/system-work-census.v5.js:77` | direct source |
| E1081 | Source `mcp-server/src/system-work-census.v5.js:81` | `mcp-server/src/system-work-census.v5.js:81` | direct source |
| E1082 | Source `mcp-server/src/system-work-census.v5.js:84` | `mcp-server/src/system-work-census.v5.js:84` | direct source |
| E1083 | Source `mcp-server/src/system-work-census.v5.js:87` | `mcp-server/src/system-work-census.v5.js:87` | direct source |
| E1084 | Source `mcp-server/src/system-work-census.v5.js:90` | `mcp-server/src/system-work-census.v5.js:90` | direct source |
| E1085 | Source `mcp-server/src/system-work-census.v5.js:97` | `mcp-server/src/system-work-census.v5.js:97` | direct source |
| E1086 | Source `mcp-server/src/tools.js:10039` | `mcp-server/src/tools.js:10039` | direct source |
| E1087 | Source `mcp-server/src/tools.js:1014` | `mcp-server/src/tools.js:1014` | direct source |
| E1088 | Source `mcp-server/src/tools.js:1040` | `mcp-server/src/tools.js:1040` | direct source |
| E1089 | Source `mcp-server/src/tools.js:1089` | `mcp-server/src/tools.js:1089` | direct source |
| E1090 | Source `mcp-server/src/tools.js:1208` | `mcp-server/src/tools.js:1208` | direct source |
| E1091 | Source `mcp-server/src/tools.js:1219` | `mcp-server/src/tools.js:1219` | direct source |
| E1092 | Source `mcp-server/src/tools.js:1257` | `mcp-server/src/tools.js:1257` | direct source |
| E1093 | Source `mcp-server/src/tools.js:1264` | `mcp-server/src/tools.js:1264` | direct source |
| E1094 | Source `mcp-server/src/tools.js:1334` | `mcp-server/src/tools.js:1334` | direct source |
| E1095 | Source `mcp-server/src/tools.js:1336` | `mcp-server/src/tools.js:1336` | direct source |
| E1096 | Source `mcp-server/src/tools.js:1352` | `mcp-server/src/tools.js:1352` | direct source |
| E1097 | Source `mcp-server/src/tools.js:1354` | `mcp-server/src/tools.js:1354` | direct source |
| E1098 | Source `mcp-server/src/tools.js:1363` | `mcp-server/src/tools.js:1363` | direct source |
| E1099 | Source `mcp-server/src/tools.js:1367` | `mcp-server/src/tools.js:1367` | direct source |
| E1100 | Source `mcp-server/src/tools.js:1399` | `mcp-server/src/tools.js:1399` | direct source |
| E1101 | Source `mcp-server/src/tools.js:1401` | `mcp-server/src/tools.js:1401` | direct source |
| E1102 | Source `mcp-server/src/tools.js:1531` | `mcp-server/src/tools.js:1531` | direct source |
| E1103 | Source `mcp-server/src/tools.js:1535` | `mcp-server/src/tools.js:1535` | direct source |
| E1104 | Source `mcp-server/src/tools.js:1541` | `mcp-server/src/tools.js:1541` | direct source |
| E1105 | Source `mcp-server/src/tools.js:1556` | `mcp-server/src/tools.js:1556` | direct source |
| E1106 | Source `mcp-server/src/tools.js:1561` | `mcp-server/src/tools.js:1561` | direct source |
| E1107 | Source `mcp-server/src/tools.js:1564` | `mcp-server/src/tools.js:1564` | direct source |
| E1108 | Source `mcp-server/src/tools.js:1624` | `mcp-server/src/tools.js:1624` | direct source |
| E1109 | Source `mcp-server/src/tools.js:1626` | `mcp-server/src/tools.js:1626` | direct source |
| E1110 | Source `mcp-server/src/tools.js:1627` | `mcp-server/src/tools.js:1627` | direct source |
| E1111 | Source `mcp-server/src/tools.js:1632` | `mcp-server/src/tools.js:1632` | direct source |
| E1112 | Source `mcp-server/src/tools.js:1672` | `mcp-server/src/tools.js:1672` | direct source |
| E1113 | Source `mcp-server/src/tools.js:1688` | `mcp-server/src/tools.js:1688` | direct source |
| E1114 | Source `mcp-server/src/tools.js:1719` | `mcp-server/src/tools.js:1719` | direct source |
| E1115 | Source `mcp-server/src/tools.js:1729` | `mcp-server/src/tools.js:1729` | direct source |
| E1116 | Source `mcp-server/src/tools.js:1881` | `mcp-server/src/tools.js:1881` | direct source |
| E1117 | Source `mcp-server/src/tools.js:1904` | `mcp-server/src/tools.js:1904` | direct source |
| E1118 | Source `mcp-server/src/tools.js:1913` | `mcp-server/src/tools.js:1913` | direct source |
| E1119 | Source `mcp-server/src/tools.js:1922` | `mcp-server/src/tools.js:1922` | direct source |
| E1120 | Source `mcp-server/src/tools.js:1934` | `mcp-server/src/tools.js:1934` | direct source |
| E1121 | Source `mcp-server/src/tools.js:1939` | `mcp-server/src/tools.js:1939` | direct source |
| E1122 | Source `mcp-server/src/tools.js:1954` | `mcp-server/src/tools.js:1954` | direct source |
| E1123 | Source `mcp-server/src/tools.js:2043` | `mcp-server/src/tools.js:2043` | direct source |
| E1124 | Source `mcp-server/src/tools.js:2085` | `mcp-server/src/tools.js:2085` | direct source |
| E1125 | Source `mcp-server/src/tools.js:2296` | `mcp-server/src/tools.js:2296` | direct source |
| E1126 | Source `mcp-server/src/tools.js:2298` | `mcp-server/src/tools.js:2298` | direct source |
| E1127 | Source `mcp-server/src/tools.js:2302` | `mcp-server/src/tools.js:2302` | direct source |
| E1128 | Source `mcp-server/src/tools.js:2306` | `mcp-server/src/tools.js:2306` | direct source |
| E1129 | Source `mcp-server/src/tools.js:2313` | `mcp-server/src/tools.js:2313` | direct source |
| E1130 | Source `mcp-server/src/tools.js:2317` | `mcp-server/src/tools.js:2317` | direct source |
| E1131 | Source `mcp-server/src/tools.js:2352` | `mcp-server/src/tools.js:2352` | direct source |
| E1132 | Source `mcp-server/src/tools.js:2358` | `mcp-server/src/tools.js:2358` | direct source |
| E1133 | Source `mcp-server/src/tools.js:2365` | `mcp-server/src/tools.js:2365` | direct source |
| E1134 | Source `mcp-server/src/tools.js:2370` | `mcp-server/src/tools.js:2370` | direct source |
| E1135 | Source `mcp-server/src/tools.js:2386` | `mcp-server/src/tools.js:2386` | direct source |
| E1136 | Source `mcp-server/src/tools.js:2390` | `mcp-server/src/tools.js:2390` | direct source |
| E1137 | Source `mcp-server/src/tools.js:2406` | `mcp-server/src/tools.js:2406` | direct source |
| E1138 | Source `mcp-server/src/tools.js:2419` | `mcp-server/src/tools.js:2419` | direct source |
| E1139 | Source `mcp-server/src/tools.js:2445` | `mcp-server/src/tools.js:2445` | direct source |
| E1140 | Source `mcp-server/src/tools.js:2448` | `mcp-server/src/tools.js:2448` | direct source |
| E1141 | Source `mcp-server/src/tools.js:2449` | `mcp-server/src/tools.js:2449` | direct source |
| E1142 | Source `mcp-server/src/tools.js:2456` | `mcp-server/src/tools.js:2456` | direct source |
| E1143 | Source `mcp-server/src/tools.js:2460` | `mcp-server/src/tools.js:2460` | direct source |
| E1144 | Source `mcp-server/src/tools.js:2463` | `mcp-server/src/tools.js:2463` | direct source |
| E1145 | Source `mcp-server/src/tools.js:2469` | `mcp-server/src/tools.js:2469` | direct source |
| E1146 | Source `mcp-server/src/tools.js:2480` | `mcp-server/src/tools.js:2480` | direct source |
| E1147 | Source `mcp-server/src/tools.js:2517` | `mcp-server/src/tools.js:2517` | direct source |
| E1148 | Source `mcp-server/src/tools.js:3206` | `mcp-server/src/tools.js:3206` | direct source |
| E1149 | Source `mcp-server/src/tools.js:334` | `mcp-server/src/tools.js:334` | direct source |
| E1150 | Source `mcp-server/src/tools.js:344` | `mcp-server/src/tools.js:344` | direct source |
| E1151 | Source `mcp-server/src/tools.js:3468` | `mcp-server/src/tools.js:3468` | direct source |
| E1152 | Source `mcp-server/src/tools.js:3626` | `mcp-server/src/tools.js:3626` | direct source |
| E1153 | Source `mcp-server/src/tools.js:3777` | `mcp-server/src/tools.js:3777` | direct source |
| E1154 | Source `mcp-server/src/tools.js:3784` | `mcp-server/src/tools.js:3784` | direct source |
| E1155 | Source `mcp-server/src/tools.js:3839` | `mcp-server/src/tools.js:3839` | direct source |
| E1156 | Source `mcp-server/src/tools.js:3870` | `mcp-server/src/tools.js:3870` | direct source |
| E1157 | Source `mcp-server/src/tools.js:4001` | `mcp-server/src/tools.js:4001` | direct source |
| E1158 | Source `mcp-server/src/tools.js:4045` | `mcp-server/src/tools.js:4045` | direct source |
| E1159 | Source `mcp-server/src/tools.js:4051` | `mcp-server/src/tools.js:4051` | direct source |
| E1160 | Source `mcp-server/src/tools.js:4086` | `mcp-server/src/tools.js:4086` | direct source |
| E1161 | Source `mcp-server/src/tools.js:4097` | `mcp-server/src/tools.js:4097` | direct source |
| E1162 | Source `mcp-server/src/tools.js:4100` | `mcp-server/src/tools.js:4100` | direct source |
| E1163 | Source `mcp-server/src/tools.js:4107` | `mcp-server/src/tools.js:4107` | direct source |
| E1164 | Source `mcp-server/src/tools.js:4109` | `mcp-server/src/tools.js:4109` | direct source |
| E1165 | Source `mcp-server/src/tools.js:4121` | `mcp-server/src/tools.js:4121` | direct source |
| E1166 | Source `mcp-server/src/tools.js:4147` | `mcp-server/src/tools.js:4147` | direct source |
| E1167 | Source `mcp-server/src/tools.js:4151` | `mcp-server/src/tools.js:4151` | direct source |
| E1168 | Source `mcp-server/src/tools.js:4158` | `mcp-server/src/tools.js:4158` | direct source |
| E1169 | Source `mcp-server/src/tools.js:4162` | `mcp-server/src/tools.js:4162` | direct source |
| E1170 | Source `mcp-server/src/tools.js:418` | `mcp-server/src/tools.js:418` | direct source |
| E1171 | Source `mcp-server/src/tools.js:4204` | `mcp-server/src/tools.js:4204` | direct source |
| E1172 | Source `mcp-server/src/tools.js:4323` | `mcp-server/src/tools.js:4323` | direct source |
| E1173 | Source `mcp-server/src/tools.js:4324` | `mcp-server/src/tools.js:4324` | direct source |
| E1174 | Source `mcp-server/src/tools.js:4326` | `mcp-server/src/tools.js:4326` | direct source |
| E1175 | Source `mcp-server/src/tools.js:4381` | `mcp-server/src/tools.js:4381` | direct source |
| E1176 | Source `mcp-server/src/tools.js:4393` | `mcp-server/src/tools.js:4393` | direct source |
| E1177 | Source `mcp-server/src/tools.js:4435` | `mcp-server/src/tools.js:4435` | direct source |
| E1178 | Source `mcp-server/src/tools.js:4443` | `mcp-server/src/tools.js:4443` | direct source |
| E1179 | Source `mcp-server/src/tools.js:4448` | `mcp-server/src/tools.js:4448` | direct source |
| E1180 | Source `mcp-server/src/tools.js:4458` | `mcp-server/src/tools.js:4458` | direct source |
| E1181 | Source `mcp-server/src/tools.js:4483` | `mcp-server/src/tools.js:4483` | direct source |
| E1182 | Source `mcp-server/src/tools.js:4484` | `mcp-server/src/tools.js:4484` | direct source |
| E1183 | Source `mcp-server/src/tools.js:4485` | `mcp-server/src/tools.js:4485` | direct source |
| E1184 | Source `mcp-server/src/tools.js:4487` | `mcp-server/src/tools.js:4487` | direct source |
| E1185 | Source `mcp-server/src/tools.js:4490` | `mcp-server/src/tools.js:4490` | direct source |
| E1186 | Source `mcp-server/src/tools.js:4492` | `mcp-server/src/tools.js:4492` | direct source |
| E1187 | Source `mcp-server/src/tools.js:4517` | `mcp-server/src/tools.js:4517` | direct source |
| E1188 | Source `mcp-server/src/tools.js:454` | `mcp-server/src/tools.js:454` | direct source |
| E1189 | Source `mcp-server/src/tools.js:4562` | `mcp-server/src/tools.js:4562` | direct source |
| E1190 | Source `mcp-server/src/tools.js:4625` | `mcp-server/src/tools.js:4625` | direct source |
| E1191 | Source `mcp-server/src/tools.js:4637` | `mcp-server/src/tools.js:4637` | direct source |
| E1192 | Source `mcp-server/src/tools.js:4646` | `mcp-server/src/tools.js:4646` | direct source |
| E1193 | Source `mcp-server/src/tools.js:4661` | `mcp-server/src/tools.js:4661` | direct source |
| E1194 | Source `mcp-server/src/tools.js:4668` | `mcp-server/src/tools.js:4668` | direct source |
| E1195 | Source `mcp-server/src/tools.js:4672` | `mcp-server/src/tools.js:4672` | direct source |
| E1196 | Source `mcp-server/src/tools.js:4687` | `mcp-server/src/tools.js:4687` | direct source |
| E1197 | Source `mcp-server/src/tools.js:4707` | `mcp-server/src/tools.js:4707` | direct source |
| E1198 | Source `mcp-server/src/tools.js:4726` | `mcp-server/src/tools.js:4726` | direct source |
| E1199 | Source `mcp-server/src/tools.js:4734` | `mcp-server/src/tools.js:4734` | direct source |
| E1200 | Source `mcp-server/src/tools.js:4739` | `mcp-server/src/tools.js:4739` | direct source |
| E1201 | Source `mcp-server/src/tools.js:4785` | `mcp-server/src/tools.js:4785` | direct source |
| E1202 | Source `mcp-server/src/tools.js:4792` | `mcp-server/src/tools.js:4792` | direct source |
| E1203 | Source `mcp-server/src/tools.js:4817` | `mcp-server/src/tools.js:4817` | direct source |
| E1204 | Source `mcp-server/src/tools.js:4819` | `mcp-server/src/tools.js:4819` | direct source |
| E1205 | Source `mcp-server/src/tools.js:4836` | `mcp-server/src/tools.js:4836` | direct source |
| E1206 | Source `mcp-server/src/tools.js:4841` | `mcp-server/src/tools.js:4841` | direct source |
| E1207 | Source `mcp-server/src/tools.js:4854` | `mcp-server/src/tools.js:4854` | direct source |
| E1208 | Source `mcp-server/src/tools.js:4863` | `mcp-server/src/tools.js:4863` | direct source |
| E1209 | Source `mcp-server/src/tools.js:4897` | `mcp-server/src/tools.js:4897` | direct source |
| E1210 | Source `mcp-server/src/tools.js:4899` | `mcp-server/src/tools.js:4899` | direct source |
| E1211 | Source `mcp-server/src/tools.js:4920` | `mcp-server/src/tools.js:4920` | direct source |
| E1212 | Source `mcp-server/src/tools.js:4982` | `mcp-server/src/tools.js:4982` | direct source |
| E1213 | Source `mcp-server/src/tools.js:4996` | `mcp-server/src/tools.js:4996` | direct source |
| E1214 | Source `mcp-server/src/tools.js:5000` | `mcp-server/src/tools.js:5000` | direct source |
| E1215 | Source `mcp-server/src/tools.js:5009` | `mcp-server/src/tools.js:5009` | direct source |
| E1216 | Source `mcp-server/src/tools.js:5022` | `mcp-server/src/tools.js:5022` | direct source |
| E1217 | Source `mcp-server/src/tools.js:5071` | `mcp-server/src/tools.js:5071` | direct source |
| E1218 | Source `mcp-server/src/tools.js:5073` | `mcp-server/src/tools.js:5073` | direct source |
| E1219 | Source `mcp-server/src/tools.js:5076` | `mcp-server/src/tools.js:5076` | direct source |
| E1220 | Source `mcp-server/src/tools.js:5079` | `mcp-server/src/tools.js:5079` | direct source |
| E1221 | Source `mcp-server/src/tools.js:5093` | `mcp-server/src/tools.js:5093` | direct source |
| E1222 | Source `mcp-server/src/tools.js:5099` | `mcp-server/src/tools.js:5099` | direct source |
| E1223 | Source `mcp-server/src/tools.js:5106` | `mcp-server/src/tools.js:5106` | direct source |
| E1224 | Source `mcp-server/src/tools.js:5134` | `mcp-server/src/tools.js:5134` | direct source |
| E1225 | Source `mcp-server/src/tools.js:5164` | `mcp-server/src/tools.js:5164` | direct source |
| E1226 | Source `mcp-server/src/tools.js:5183` | `mcp-server/src/tools.js:5183` | direct source |
| E1227 | Source `mcp-server/src/tools.js:5195` | `mcp-server/src/tools.js:5195` | direct source |
| E1228 | Source `mcp-server/src/tools.js:5198` | `mcp-server/src/tools.js:5198` | direct source |
| E1229 | Source `mcp-server/src/tools.js:5217` | `mcp-server/src/tools.js:5217` | direct source |
| E1230 | Source `mcp-server/src/tools.js:5231` | `mcp-server/src/tools.js:5231` | direct source |
| E1231 | Source `mcp-server/src/tools.js:5233` | `mcp-server/src/tools.js:5233` | direct source |
| E1232 | Source `mcp-server/src/tools.js:5262` | `mcp-server/src/tools.js:5262` | direct source |
| E1233 | Source `mcp-server/src/tools.js:5266` | `mcp-server/src/tools.js:5266` | direct source |
| E1234 | Source `mcp-server/src/tools.js:5268` | `mcp-server/src/tools.js:5268` | direct source |
| E1235 | Source `mcp-server/src/tools.js:5287` | `mcp-server/src/tools.js:5287` | direct source |
| E1236 | Source `mcp-server/src/tools.js:5292` | `mcp-server/src/tools.js:5292` | direct source |
| E1237 | Source `mcp-server/src/tools.js:5318` | `mcp-server/src/tools.js:5318` | direct source |
| E1238 | Source `mcp-server/src/tools.js:5338` | `mcp-server/src/tools.js:5338` | direct source |
| E1239 | Source `mcp-server/src/tools.js:5359` | `mcp-server/src/tools.js:5359` | direct source |
| E1240 | Source `mcp-server/src/tools.js:5376` | `mcp-server/src/tools.js:5376` | direct source |
| E1241 | Source `mcp-server/src/tools.js:5378` | `mcp-server/src/tools.js:5378` | direct source |
| E1242 | Source `mcp-server/src/tools.js:5431` | `mcp-server/src/tools.js:5431` | direct source |
| E1243 | Source `mcp-server/src/tools.js:5435` | `mcp-server/src/tools.js:5435` | direct source |
| E1244 | Source `mcp-server/src/tools.js:5437` | `mcp-server/src/tools.js:5437` | direct source |
| E1245 | Source `mcp-server/src/tools.js:5503` | `mcp-server/src/tools.js:5503` | direct source |
| E1246 | Source `mcp-server/src/tools.js:5515` | `mcp-server/src/tools.js:5515` | direct source |
| E1247 | Source `mcp-server/src/tools.js:5525` | `mcp-server/src/tools.js:5525` | direct source |
| E1248 | Source `mcp-server/src/tools.js:5579` | `mcp-server/src/tools.js:5579` | direct source |
| E1249 | Source `mcp-server/src/tools.js:5588` | `mcp-server/src/tools.js:5588` | direct source |
| E1250 | Source `mcp-server/src/tools.js:5597` | `mcp-server/src/tools.js:5597` | direct source |
| E1251 | Source `mcp-server/src/tools.js:5628` | `mcp-server/src/tools.js:5628` | direct source |
| E1252 | Source `mcp-server/src/tools.js:5640` | `mcp-server/src/tools.js:5640` | direct source |
| E1253 | Source `mcp-server/src/tools.js:5644` | `mcp-server/src/tools.js:5644` | direct source |
| E1254 | Source `mcp-server/src/tools.js:5679` | `mcp-server/src/tools.js:5679` | direct source |
| E1255 | Source `mcp-server/src/tools.js:5725` | `mcp-server/src/tools.js:5725` | direct source |
| E1256 | Source `mcp-server/src/tools.js:5750` | `mcp-server/src/tools.js:5750` | direct source |
| E1257 | Source `mcp-server/src/tools.js:578` | `mcp-server/src/tools.js:578` | direct source |
| E1258 | Source `mcp-server/src/tools.js:5797` | `mcp-server/src/tools.js:5797` | direct source |
| E1259 | Source `mcp-server/src/tools.js:5853` | `mcp-server/src/tools.js:5853` | direct source |
| E1260 | Source `mcp-server/src/tools.js:5890` | `mcp-server/src/tools.js:5890` | direct source |
| E1261 | Source `mcp-server/src/tools.js:5900` | `mcp-server/src/tools.js:5900` | direct source |
| E1262 | Source `mcp-server/src/tools.js:5917` | `mcp-server/src/tools.js:5917` | direct source |
| E1263 | Source `mcp-server/src/tools.js:5990` | `mcp-server/src/tools.js:5990` | direct source |
| E1264 | Source `mcp-server/src/tools.js:6007` | `mcp-server/src/tools.js:6007` | direct source |
| E1265 | Source `mcp-server/src/tools.js:6031` | `mcp-server/src/tools.js:6031` | direct source |
| E1266 | Source `mcp-server/src/tools.js:6069` | `mcp-server/src/tools.js:6069` | direct source |
| E1267 | Source `mcp-server/src/tools.js:6112` | `mcp-server/src/tools.js:6112` | direct source |
| E1268 | Source `mcp-server/src/tools.js:6119` | `mcp-server/src/tools.js:6119` | direct source |
| E1269 | Source `mcp-server/src/tools.js:6167` | `mcp-server/src/tools.js:6167` | direct source |
| E1270 | Source `mcp-server/src/tools.js:6193` | `mcp-server/src/tools.js:6193` | direct source |
| E1271 | Source `mcp-server/src/tools.js:6200` | `mcp-server/src/tools.js:6200` | direct source |
| E1272 | Source `mcp-server/src/tools.js:6203` | `mcp-server/src/tools.js:6203` | direct source |
| E1273 | Source `mcp-server/src/tools.js:6210` | `mcp-server/src/tools.js:6210` | direct source |
| E1274 | Source `mcp-server/src/tools.js:6213` | `mcp-server/src/tools.js:6213` | direct source |
| E1275 | Source `mcp-server/src/tools.js:6216` | `mcp-server/src/tools.js:6216` | direct source |
| E1276 | Source `mcp-server/src/tools.js:6219` | `mcp-server/src/tools.js:6219` | direct source |
| E1277 | Source `mcp-server/src/tools.js:6224` | `mcp-server/src/tools.js:6224` | direct source |
| E1278 | Source `mcp-server/src/tools.js:631` | `mcp-server/src/tools.js:631` | direct source |
| E1279 | Source `mcp-server/src/tools.js:6322` | `mcp-server/src/tools.js:6322` | direct source |
| E1280 | Source `mcp-server/src/tools.js:6326` | `mcp-server/src/tools.js:6326` | direct source |
| E1281 | Source `mcp-server/src/tools.js:6370` | `mcp-server/src/tools.js:6370` | direct source |
| E1282 | Source `mcp-server/src/tools.js:6463` | `mcp-server/src/tools.js:6463` | direct source |
| E1283 | Source `mcp-server/src/tools.js:6473` | `mcp-server/src/tools.js:6473` | direct source |
| E1284 | Source `mcp-server/src/tools.js:6494` | `mcp-server/src/tools.js:6494` | direct source |
| E1285 | Source `mcp-server/src/tools.js:6557` | `mcp-server/src/tools.js:6557` | direct source |
| E1286 | Source `mcp-server/src/tools.js:6574` | `mcp-server/src/tools.js:6574` | direct source |
| E1287 | Source `mcp-server/src/tools.js:6577` | `mcp-server/src/tools.js:6577` | direct source |
| E1288 | Source `mcp-server/src/tools.js:6589` | `mcp-server/src/tools.js:6589` | direct source |
| E1289 | Source `mcp-server/src/tools.js:6593` | `mcp-server/src/tools.js:6593` | direct source |
| E1290 | Source `mcp-server/src/tools.js:6614` | `mcp-server/src/tools.js:6614` | direct source |
| E1291 | Source `mcp-server/src/tools.js:6619` | `mcp-server/src/tools.js:6619` | direct source |
| E1292 | Source `mcp-server/src/tools.js:6629` | `mcp-server/src/tools.js:6629` | direct source |
| E1293 | Source `mcp-server/src/tools.js:6785` | `mcp-server/src/tools.js:6785` | direct source |
| E1294 | Source `mcp-server/src/tools.js:683` | `mcp-server/src/tools.js:683` | direct source |
| E1295 | Source `mcp-server/src/tools.js:6877` | `mcp-server/src/tools.js:6877` | direct source |
| E1296 | Source `mcp-server/src/tools.js:6884` | `mcp-server/src/tools.js:6884` | direct source |
| E1297 | Source `mcp-server/src/tools.js:6947` | `mcp-server/src/tools.js:6947` | direct source |
| E1298 | Source `mcp-server/src/tools.js:6957` | `mcp-server/src/tools.js:6957` | direct source |
| E1299 | Source `mcp-server/src/tools.js:7013` | `mcp-server/src/tools.js:7013` | direct source |
| E1300 | Source `mcp-server/src/tools.js:7024` | `mcp-server/src/tools.js:7024` | direct source |
| E1301 | Source `mcp-server/src/tools.js:7071` | `mcp-server/src/tools.js:7071` | direct source |
| E1302 | Source `mcp-server/src/tools.js:7083` | `mcp-server/src/tools.js:7083` | direct source |
| E1303 | Source `mcp-server/src/tools.js:7140` | `mcp-server/src/tools.js:7140` | direct source |
| E1304 | Source `mcp-server/src/tools.js:7177` | `mcp-server/src/tools.js:7177` | direct source |
| E1305 | Source `mcp-server/src/tools.js:7237` | `mcp-server/src/tools.js:7237` | direct source |
| E1306 | Source `mcp-server/src/tools.js:7268` | `mcp-server/src/tools.js:7268` | direct source |
| E1307 | Source `mcp-server/src/tools.js:7329` | `mcp-server/src/tools.js:7329` | direct source |
| E1308 | Source `mcp-server/src/tools.js:7355` | `mcp-server/src/tools.js:7355` | direct source |
| E1309 | Source `mcp-server/src/tools.js:7436` | `mcp-server/src/tools.js:7436` | direct source |
| E1310 | Source `mcp-server/src/tools.js:7438` | `mcp-server/src/tools.js:7438` | direct source |
| E1311 | Source `mcp-server/src/tools.js:7536` | `mcp-server/src/tools.js:7536` | direct source |
| E1312 | Source `mcp-server/src/tools.js:7548` | `mcp-server/src/tools.js:7548` | direct source |
| E1313 | Source `mcp-server/src/tools.js:7560` | `mcp-server/src/tools.js:7560` | direct source |
| E1314 | Source `mcp-server/src/tools.js:7625` | `mcp-server/src/tools.js:7625` | direct source |
| E1315 | Source `mcp-server/src/tools.js:7706` | `mcp-server/src/tools.js:7706` | direct source |
| E1316 | Source `mcp-server/src/tools.js:7780` | `mcp-server/src/tools.js:7780` | direct source |
| E1317 | Source `mcp-server/src/tools.js:7796` | `mcp-server/src/tools.js:7796` | direct source |
| E1318 | Source `mcp-server/src/tools.js:7822` | `mcp-server/src/tools.js:7822` | direct source |
| E1319 | Source `mcp-server/src/tools.js:7845` | `mcp-server/src/tools.js:7845` | direct source |
| E1320 | Source `mcp-server/src/tools.js:7853` | `mcp-server/src/tools.js:7853` | direct source |
| E1321 | Source `mcp-server/src/tools.js:7862` | `mcp-server/src/tools.js:7862` | direct source |
| E1322 | Source `mcp-server/src/tools.js:7875` | `mcp-server/src/tools.js:7875` | direct source |
| E1323 | Source `mcp-server/src/tools.js:7911` | `mcp-server/src/tools.js:7911` | direct source |
| E1324 | Source `mcp-server/src/tools.js:8003` | `mcp-server/src/tools.js:8003` | direct source |
| E1325 | Source `mcp-server/src/tools.js:8016` | `mcp-server/src/tools.js:8016` | direct source |
| E1326 | Source `mcp-server/src/tools.js:8071` | `mcp-server/src/tools.js:8071` | direct source |
| E1327 | Source `mcp-server/src/tools.js:8125` | `mcp-server/src/tools.js:8125` | direct source |
| E1328 | Source `mcp-server/src/tools.js:8139` | `mcp-server/src/tools.js:8139` | direct source |
| E1329 | Source `mcp-server/src/tools.js:8233` | `mcp-server/src/tools.js:8233` | direct source |
| E1330 | Source `mcp-server/src/tools.js:8280` | `mcp-server/src/tools.js:8280` | direct source |
| E1331 | Source `mcp-server/src/tools.js:8349` | `mcp-server/src/tools.js:8349` | direct source |
| E1332 | Source `mcp-server/src/tools.js:8402` | `mcp-server/src/tools.js:8402` | direct source |
| E1333 | Source `mcp-server/src/tools.js:8469` | `mcp-server/src/tools.js:8469` | direct source |
| E1334 | Source `mcp-server/src/tools.js:8475` | `mcp-server/src/tools.js:8475` | direct source |
| E1335 | Source `mcp-server/src/tools.js:8504` | `mcp-server/src/tools.js:8504` | direct source |
| E1336 | Source `mcp-server/src/tools.js:8511` | `mcp-server/src/tools.js:8511` | direct source |
| E1337 | Source `mcp-server/src/tools.js:8528` | `mcp-server/src/tools.js:8528` | direct source |
| E1338 | Source `mcp-server/src/tools.js:8535` | `mcp-server/src/tools.js:8535` | direct source |
| E1339 | Source `mcp-server/src/tools.js:8538` | `mcp-server/src/tools.js:8538` | direct source |
| E1340 | Source `mcp-server/src/tools.js:8541` | `mcp-server/src/tools.js:8541` | direct source |
| E1341 | Source `mcp-server/src/tools.js:8641` | `mcp-server/src/tools.js:8641` | direct source |
| E1342 | Source `mcp-server/src/tools.js:8707` | `mcp-server/src/tools.js:8707` | direct source |
| E1343 | Source `mcp-server/src/tools.js:8715` | `mcp-server/src/tools.js:8715` | direct source |
| E1344 | Source `mcp-server/src/tools.js:8726` | `mcp-server/src/tools.js:8726` | direct source |
| E1345 | Source `mcp-server/src/tools.js:8771` | `mcp-server/src/tools.js:8771` | direct source |
| E1346 | Source `mcp-server/src/tools.js:8798` | `mcp-server/src/tools.js:8798` | direct source |
| E1347 | Source `mcp-server/src/tools.js:8839` | `mcp-server/src/tools.js:8839` | direct source |
| E1348 | Source `mcp-server/src/tools.js:9393` | `mcp-server/src/tools.js:9393` | direct source |
| E1349 | Source `mcp-server/src/tools.js:9422` | `mcp-server/src/tools.js:9422` | direct source |
| E1350 | Source `mcp-server/src/tools.js:9457` | `mcp-server/src/tools.js:9457` | direct source |
| E1351 | Source `mcp-server/src/tools.js:9503` | `mcp-server/src/tools.js:9503` | direct source |
| E1352 | Source `mcp-server/src/tools.js:9507` | `mcp-server/src/tools.js:9507` | direct source |
| E1353 | Source `mcp-server/src/tools.js:9512` | `mcp-server/src/tools.js:9512` | direct source |
| E1354 | Source `mcp-server/src/tools.js:9532` | `mcp-server/src/tools.js:9532` | direct source |
| E1355 | Source `mcp-server/src/tools.js:9544` | `mcp-server/src/tools.js:9544` | direct source |
| E1356 | Source `mcp-server/src/tools.js:9564` | `mcp-server/src/tools.js:9564` | direct source |
| E1357 | Source `mcp-server/src/tools.js:9570` | `mcp-server/src/tools.js:9570` | direct source |
| E1358 | Source `mcp-server/src/tools.js:9594` | `mcp-server/src/tools.js:9594` | direct source |
| E1359 | Source `mcp-server/src/tools.js:9596` | `mcp-server/src/tools.js:9596` | direct source |
| E1360 | Source `mcp-server/src/tools.js:9618` | `mcp-server/src/tools.js:9618` | direct source |
| E1361 | Source `mcp-server/src/tools.js:9620` | `mcp-server/src/tools.js:9620` | direct source |
| E1362 | Source `mcp-server/src/tools.js:9623` | `mcp-server/src/tools.js:9623` | direct source |
| E1363 | Source `mcp-server/src/tools.js:9642` | `mcp-server/src/tools.js:9642` | direct source |
| E1364 | Source `mcp-server/src/tools.js:9649` | `mcp-server/src/tools.js:9649` | direct source |
| E1365 | Source `mcp-server/src/tools.js:9654` | `mcp-server/src/tools.js:9654` | direct source |
| E1366 | Source `mcp-server/src/tools.js:9658` | `mcp-server/src/tools.js:9658` | direct source |
| E1367 | Source `mcp-server/src/tools.js:9660` | `mcp-server/src/tools.js:9660` | direct source |
| E1368 | Source `mcp-server/src/tools.js:9683` | `mcp-server/src/tools.js:9683` | direct source |
| E1369 | Source `mcp-server/src/tools.js:9688` | `mcp-server/src/tools.js:9688` | direct source |
| E1370 | Source `mcp-server/src/tools.js:9691` | `mcp-server/src/tools.js:9691` | direct source |
| E1371 | Source `mcp-server/src/tools.js:9698` | `mcp-server/src/tools.js:9698` | direct source |
| E1372 | Source `mcp-server/src/tools.js:9704` | `mcp-server/src/tools.js:9704` | direct source |
| E1373 | Source `mcp-server/src/tools.js:9711` | `mcp-server/src/tools.js:9711` | direct source |
| E1374 | Source `mcp-server/src/tools.js:9716` | `mcp-server/src/tools.js:9716` | direct source |
| E1375 | Source `mcp-server/src/tools.js:9719` | `mcp-server/src/tools.js:9719` | direct source |
| E1376 | Source `mcp-server/src/tools.js:9741` | `mcp-server/src/tools.js:9741` | direct source |
| E1377 | Source `mcp-server/src/tools.js:9751` | `mcp-server/src/tools.js:9751` | direct source |
| E1378 | Source `mcp-server/src/tools.js:9773` | `mcp-server/src/tools.js:9773` | direct source |
| E1379 | Source `mcp-server/src/tools.js:9788` | `mcp-server/src/tools.js:9788` | direct source |
| E1380 | Source `mcp-server/src/tools.js:9807` | `mcp-server/src/tools.js:9807` | direct source |
| E1381 | Source `mcp-server/src/tools.js:9809` | `mcp-server/src/tools.js:9809` | direct source |
| E1382 | Source `mcp-server/src/tools.js:9843` | `mcp-server/src/tools.js:9843` | direct source |
| E1383 | Source `mcp-server/src/tools.js:9855` | `mcp-server/src/tools.js:9855` | direct source |
| E1384 | Source `mcp-server/src/tools.js:9877` | `mcp-server/src/tools.js:9877` | direct source |
| E1385 | Source `mcp-server/src/tools.js:9881` | `mcp-server/src/tools.js:9881` | direct source |
| E1386 | Source `mcp-server/src/tools.js:9888` | `mcp-server/src/tools.js:9888` | direct source |
| E1387 | Source `mcp-server/src/tools.js:9904` | `mcp-server/src/tools.js:9904` | direct source |
| E1388 | Source `mcp-server/src/tools.js:9915` | `mcp-server/src/tools.js:9915` | direct source |
| E1389 | Source `mcp-server/src/tools.js:9921` | `mcp-server/src/tools.js:9921` | direct source |
| E1390 | Source `mcp-server/src/trace.js:203` | `mcp-server/src/trace.js:203` | direct source |
| E1391 | Source `mcp-server/src/trace.js:244` | `mcp-server/src/trace.js:244` | direct source |
| E1392 | Source `mcp-server/src/trace.js:267` | `mcp-server/src/trace.js:267` | direct source |
| E1393 | Source `mcp-server/src/trace.js:287` | `mcp-server/src/trace.js:287` | direct source |
| E1394 | Source `mcp-server/src/trace.js:309` | `mcp-server/src/trace.js:309` | direct source |
| E1395 | Source `mcp-server/src/trace.js:313` | `mcp-server/src/trace.js:313` | direct source |
| E1396 | Source `mcp-server/src/trace.js:319` | `mcp-server/src/trace.js:319` | direct source |
| E1397 | Source `mcp-server/src/vendor-relationship.js:91` | `mcp-server/src/vendor-relationship.js:91` | direct source |
| E1398 | Source `mcp-server/src/work-inventory-census.v5.js:104` | `mcp-server/src/work-inventory-census.v5.js:104` | direct source |
| E1399 | Source `mcp-server/src/work-inventory-census.v5.js:114` | `mcp-server/src/work-inventory-census.v5.js:114` | direct source |
| E1400 | Source `mcp-server/src/work-inventory-census.v5.js:127` | `mcp-server/src/work-inventory-census.v5.js:127` | direct source |
| E1401 | Source `mcp-server/src/work-inventory-census.v5.js:136` | `mcp-server/src/work-inventory-census.v5.js:136` | direct source |
| E1402 | Source `mcp-server/src/work-inventory-census.v5.js:149` | `mcp-server/src/work-inventory-census.v5.js:149` | direct source |
| E1403 | Source `mcp-server/src/work-inventory-census.v5.js:160` | `mcp-server/src/work-inventory-census.v5.js:160` | direct source |
| E1404 | Source `mcp-server/src/work-inventory-census.v5.js:173` | `mcp-server/src/work-inventory-census.v5.js:173` | direct source |
| E1405 | Source `mcp-server/src/work-inventory-census.v5.js:185` | `mcp-server/src/work-inventory-census.v5.js:185` | direct source |
| E1406 | Source `mcp-server/src/work-inventory-census.v5.js:205` | `mcp-server/src/work-inventory-census.v5.js:205` | direct source |
| E1407 | Source `mcp-server/src/work-inventory-census.v5.js:215` | `mcp-server/src/work-inventory-census.v5.js:215` | direct source |
| E1408 | Source `mcp-server/src/work-inventory-census.v5.js:76` | `mcp-server/src/work-inventory-census.v5.js:76` | direct source |
| E1409 | Source `mcp-server/src/work-inventory-census.v5.js:86` | `mcp-server/src/work-inventory-census.v5.js:86` | direct source |
| E1410 | Source `mcp-server/src/work-linkage.js:64` | `mcp-server/src/work-linkage.js:64` | direct source |
| E1411 | Source `mcp-server/src/work-request-intake.js:1069` | `mcp-server/src/work-request-intake.js:1069` | direct source |
| E1412 | Source `mcp-server/src/work-request-intake.js:356` | `mcp-server/src/work-request-intake.js:356` | direct source |
| E1413 | Source `mcp-server/src/work-request-intake.js:39` | `mcp-server/src/work-request-intake.js:39` | direct source |
| E1414 | Source `mcp-server/src/work-request-intake.js:460` | `mcp-server/src/work-request-intake.js:460` | direct source |
| E1415 | Source `mcp-server/src/work-request-intake.js:581` | `mcp-server/src/work-request-intake.js:581` | direct source |
| E1416 | Source `mcp-server/src/work-request-intake.js:611` | `mcp-server/src/work-request-intake.js:611` | direct source |
| E1417 | Source `mcp-server/src/work-request-intake.js:854` | `mcp-server/src/work-request-intake.js:854` | direct source |
| E1418 | Source `mcp-server/src/work-request-intake.js:901` | `mcp-server/src/work-request-intake.js:901` | direct source |
| E1419 | Source `mcp-server/src/work-request-read.js:14` | `mcp-server/src/work-request-read.js:14` | direct source |
| E1420 | Source `mcp-server/src/work-shape.js:169` | `mcp-server/src/work-shape.js:169` | direct source |
| E1421 | Source `mcp-server/src/work-shape.js:171` | `mcp-server/src/work-shape.js:171` | direct source |
| E1422 | Source `mcp-server/src/work-shape.js:199` | `mcp-server/src/work-shape.js:199` | direct source |
| E1423 | Source `mcp-server/src/work-shape.js:214` | `mcp-server/src/work-shape.js:214` | direct source |
| E1424 | Source `mcp-server/src/work-shape.js:241` | `mcp-server/src/work-shape.js:241` | direct source |
| E1425 | Source `mcp-server/src/work-shape.js:259` | `mcp-server/src/work-shape.js:259` | direct source |
| E1426 | Source `mcp-server/src/work-shape.js:265` | `mcp-server/src/work-shape.js:265` | direct source |
| E1427 | Source `mcp-server/src/workspace-business-read.js:229` | `mcp-server/src/workspace-business-read.js:229` | direct source |
| E1428 | Source `mcp-server/src/workspace-business-read.js:296` | `mcp-server/src/workspace-business-read.js:296` | direct source |
| E1429 | Source `mcp-server/src/workspace-business-read.js:317` | `mcp-server/src/workspace-business-read.js:317` | direct source |
| E1430 | Source `mcp-server/src/workspace-business-read.js:364` | `mcp-server/src/workspace-business-read.js:364` | direct source |
| E1431 | Source `mcp-server/src/workspace-business-read.js:477` | `mcp-server/src/workspace-business-read.js:477` | direct source |
| E1432 | Source `mcp-server/src/workspace-business-read.js:495` | `mcp-server/src/workspace-business-read.js:495` | direct source |
| E1433 | Source `mcp-server/src/workspace-business-read.js:499` | `mcp-server/src/workspace-business-read.js:499` | direct source |
| E1434 | Source `mcp-server/src/workspace-business-read.js:612` | `mcp-server/src/workspace-business-read.js:612` | direct source |
| E1435 | Source `mcp-server/src/workspace-command-center.js:66` | `mcp-server/src/workspace-command-center.js:66` | direct source |
| E1436 | Source `tools/enrichment-queue.py:84` | `tools/enrichment-queue.py:84` | direct source |
| E1437 | Source `tools/export-rule-admission.py:64` | `tools/export-rule-admission.py:64` | direct source |
| E1438 | Source `tools/health-check.py:1927` | `tools/health-check.py:1927` | direct source |
| E1439 | Source `tools/health-check.py:2307` | `tools/health-check.py:2307` | direct source |
| E1440 | Source `tools/health-check.py:2715` | `tools/health-check.py:2715` | direct source |
| E1441 | Source `tools/health-check.py:2717` | `tools/health-check.py:2717` | direct source |
| E1442 | Source `tools/health-check.py:2721` | `tools/health-check.py:2721` | direct source |
| E1443 | Source `tools/health-check.py:2722` | `tools/health-check.py:2722` | direct source |
| E1444 | Source `tools/health-check.py:2723` | `tools/health-check.py:2723` | direct source |
| E1445 | Source `tools/health-check.py:383` | `tools/health-check.py:383` | direct source |
| E1446 | Source `tools/health-check.py:566` | `tools/health-check.py:566` | direct source |
| E1447 | Source `tools/health-check.py:601` | `tools/health-check.py:601` | direct source |
| E1448 | Source `tools/migrate.py:90` | `tools/migrate.py:90` | direct source |
| E1449 | Source `tools/migrate.py:96` | `tools/migrate.py:96` | direct source |
| E1450 | Source `tools/ops-record.py:1022` | `tools/ops-record.py:1022` | direct source |
| E1451 | Source `tools/ops-record.py:1055` | `tools/ops-record.py:1055` | direct source |
| E1452 | Source `tools/ops-record.py:1081` | `tools/ops-record.py:1081` | direct source |
| E1453 | Source `tools/ops-record.py:1116` | `tools/ops-record.py:1116` | direct source |
| E1454 | Source `tools/ops-record.py:1255` | `tools/ops-record.py:1255` | direct source |
| E1455 | Source `tools/ops-record.py:1271` | `tools/ops-record.py:1271` | direct source |
| E1456 | Source `tools/ops-record.py:1282` | `tools/ops-record.py:1282` | direct source |
| E1457 | Source `tools/ops-record.py:1299` | `tools/ops-record.py:1299` | direct source |
| E1458 | Source `tools/ops-record.py:1322` | `tools/ops-record.py:1322` | direct source |
| E1459 | Source `tools/ops-record.py:1350` | `tools/ops-record.py:1350` | direct source |
| E1460 | Source `tools/ops-record.py:1368` | `tools/ops-record.py:1368` | direct source |
| E1461 | Source `tools/ops-record.py:1485` | `tools/ops-record.py:1485` | direct source |
| E1462 | Source `tools/ops-record.py:1503` | `tools/ops-record.py:1503` | direct source |
| E1463 | Source `tools/ops-record.py:1508` | `tools/ops-record.py:1508` | direct source |
| E1464 | Source `tools/ops-record.py:1597` | `tools/ops-record.py:1597` | direct source |
| E1465 | Source `tools/ops-record.py:1619` | `tools/ops-record.py:1619` | direct source |
| E1466 | Source `tools/ops-record.py:1638` | `tools/ops-record.py:1638` | direct source |
| E1467 | Source `tools/ops-record.py:1665` | `tools/ops-record.py:1665` | direct source |
| E1468 | Source `tools/ops-record.py:231` | `tools/ops-record.py:231` | direct source |
| E1469 | Source `tools/ops-record.py:253` | `tools/ops-record.py:253` | direct source |
| E1470 | Source `tools/ops-record.py:273` | `tools/ops-record.py:273` | direct source |
| E1471 | Source `tools/ops-record.py:292` | `tools/ops-record.py:292` | direct source |
| E1472 | Source `tools/ops-record.py:300` | `tools/ops-record.py:300` | direct source |
| E1473 | Source `tools/ops-record.py:304` | `tools/ops-record.py:304` | direct source |
| E1474 | Source `tools/ops-record.py:403` | `tools/ops-record.py:403` | direct source |
| E1475 | Source `tools/ops-record.py:428` | `tools/ops-record.py:428` | direct source |
| E1476 | Source `tools/ops-record.py:477` | `tools/ops-record.py:477` | direct source |
| E1477 | Source `tools/ops-record.py:809` | `tools/ops-record.py:809` | direct source |
| E1478 | Source `tools/ops-record.py:851` | `tools/ops-record.py:851` | direct source |
| E1479 | Source `tools/ops-record.py:868` | `tools/ops-record.py:868` | direct source |
| E1480 | Source `tools/ops-record.py:882` | `tools/ops-record.py:882` | direct source |
| E1481 | Source `tools/ops-record.py:898` | `tools/ops-record.py:898` | direct source |
| E1482 | Source `tools/ops-record.py:907` | `tools/ops-record.py:907` | direct source |
| E1483 | Source `tools/ops-record.py:933` | `tools/ops-record.py:933` | direct source |
| E1484 | Source `tools/ops-record.py:952` | `tools/ops-record.py:952` | direct source |
| E1485 | Source `tools/ops-record.py:963` | `tools/ops-record.py:963` | direct source |
| E1486 | Source `tools/ops-record.py:993` | `tools/ops-record.py:993` | direct source |
| E1487 | Source `tools/ops-spool.py:2` | `tools/ops-spool.py:2` | direct source |
| E1488 | Source `tools/restore-watermark.py:938` | `tools/restore-watermark.py:938` | direct source |
| E1489 | Source `tools/sync-rule-admission.py:105` | `tools/sync-rule-admission.py:105` | direct source |
| E1490 | Source `tools/sync-rule-admission.py:42` | `tools/sync-rule-admission.py:42` | direct source |
| E1491 | Source `tools/sync-rule-admission.py:65` | `tools/sync-rule-admission.py:65` | direct source |
| E1492 | Source `tools/sync-rule-admission.py:77` | `tools/sync-rule-admission.py:77` | direct source |
| E1493 | Source `tools/sync-rule-admission.py:97` | `tools/sync-rule-admission.py:97` | direct source |
| E1494 | Source `tools/sync-rule-admission.py:99` | `tools/sync-rule-admission.py:99` | direct source |
| E1495 | Source `tools/sync-rule-load-layers.py:100` | `tools/sync-rule-load-layers.py:100` | direct source |
| E1496 | Source `tools/sync-rule-load-layers.py:108` | `tools/sync-rule-load-layers.py:108` | direct source |
| E1497 | Source `tools/sync-rule-load-layers.py:113` | `tools/sync-rule-load-layers.py:113` | direct source |
| E1498 | Source `tools/sync-rule-load-layers.py:124` | `tools/sync-rule-load-layers.py:124` | direct source |
| E1499 | Source `tools/sync-rule-load-layers.py:65` | `tools/sync-rule-load-layers.py:65` | direct source |
| E1500 | Source `tools/workflow-registry.py:47` | `tools/workflow-registry.py:47` | direct source |
| E1501 | Source `tools/workflow-registry.py:68` | `tools/workflow-registry.py:68` | direct source |
| E1502 | Source `tools/workflow-registry.py:75` | `tools/workflow-registry.py:75` | direct source |
| E1503 | Source `tools/workflow-registry.py:87` | `tools/workflow-registry.py:87` | direct source |
| E1504 | Source `tools/workflow-registry.py:92` | `tools/workflow-registry.py:92` | direct source |
| E1505 | Trigger `a_release_maker_session_provenance@ops.release` | `migrations/0504_release_maker_session_provenance.sql:233` | trigger declaration |
| E1506 | Trigger `a_staging_release_plan_revision_invalidates_approval@ops.release` | `migrations/0219_staging_release_approval_receipt.sql:47` | trigger declaration |
| E1507 | Trigger `active_approved_control_immutable@ops.enforcement_control_catalog` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:421` | trigger declaration |
| E1508 | Trigger `activity_touch@public.activity` | `migrations/0001_init.sql:212` | trigger declaration |
| E1509 | Trigger `agreement_touch@public.agreement` | `migrations/0001_init.sql:708` | trigger declaration |
| E1510 | Trigger `allocation_tree@public.commission_allocation` | `migrations/0906_dot_database_design.sql:716` | trigger declaration |
| E1511 | Trigger `approved_rule_admission_immutable@ops.rule_admission` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:393` | trigger declaration |
| E1512 | Trigger `approved_rule_control_binding_immutable@ops.rule_control_binding` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:403` | trigger declaration |
| E1513 | Trigger `approved_rule_enforcement_point_immutable@ops.rule_enforcement_point` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:398` | trigger declaration |
| E1514 | Trigger `attempt_receipt_binding_valid@ops.attempt_receipt` | `migrations/0303_evidence_activation_reliability.sql:659` | trigger declaration |
| E1515 | Trigger `attempt_receipt_environment_evidence@ops.attempt_receipt` | `migrations/0309_governed_execution_environment_providers.sql:649` | trigger declaration |
| E1516 | Trigger `availability_norm@public.availability` | `migrations/0001_init.sql:649` | trigger declaration |
| E1517 | Trigger `benchmark_acceptance_guard@ops.benchmark_manifest_acceptance_receipt` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1440` | trigger declaration |
| E1518 | Trigger `benchmark_draft_complete@ops.benchmark_manifest_draft` | `migrations/0508_foundation_assurance_minimum_receipt.sql:946` | trigger declaration |
| E1519 | Trigger `benchmark_pass_requires_attestation@ops.benchmark_manifest_review` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1850` | trigger declaration |
| E1520 | Trigger `benchmark_proposal_guard@ops.benchmark_manifest_draft` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1236` | trigger declaration |
| E1521 | Trigger `benchmark_review_guard@ops.benchmark_manifest_review` | `migrations/0508_foundation_assurance_minimum_receipt.sql:1286` | trigger declaration |
| E1522 | Trigger `building_touch@public.building` | `migrations/0001_init.sql:569` | trigger declaration |
| E1523 | Trigger `campaign_channels_check@public.campaign` | `migrations/0066_marketing_campaign_and_measurement.sql:334` | trigger declaration |
| E1524 | Trigger `campaign_touch@public.campaign` | `migrations/0066_marketing_campaign_and_measurement.sql:306` | trigger declaration |
| E1525 | Trigger `capability_agent_session_guard_before_write@ops.capability_agent_session` | `migrations/0126_capability_program_evidence.sql:131` | trigger declaration |
| E1526 | Trigger `capability_agent_session_lease_immutable_before_write@ops.capability_agent_session` | `migrations/0335_engineering_controller_currentness.sql:37` | trigger declaration |
| E1527 | Trigger `capability_attestation_guard_before_insert@ops.capability_verification` | `migrations/0126_capability_program_evidence.sql:157` | trigger declaration |
| E1528 | Trigger `capability_program_close_guard_before_update@ops.work_request` | `migrations/0126_capability_program_evidence.sql:216` | trigger declaration |
| E1529 | Trigger `capability_program_closed_immutable_before_update@ops.work_request` | `migrations/0127_capability_program_immutability.sql:32` | trigger declaration |
| E1530 | Trigger `capability_program_identity_guard_before_update@ops.work_request` | `migrations/0126_capability_program_evidence.sql:176` | trigger declaration |
| E1531 | Trigger `claude_continuity_checkpoint_guard@public.claude_continuity_checkpoint` | `migrations/0485_claude_continuity.sql:125` | trigger declaration |
| E1532 | Trigger `claude_continuity_leaf_identity_immutable@public.claude_continuity_leaf` | `migrations/0485_claude_continuity.sql:105` | trigger declaration |
| E1533 | Trigger `claude_continuity_leaf_timestamps@public.claude_continuity_leaf` | `migrations/0485_claude_continuity.sql:86` | trigger declaration |
| E1534 | Trigger `client_touch@public.client` | `migrations/0001_init.sql:300` | trigger declaration |
| E1535 | Trigger `codex_continuity_checkpoint_identity_immutable@public.codex_continuity_checkpoint` | `migrations/0480_codex_continuity.sql:93` | trigger declaration |
| E1536 | Trigger `codex_continuity_checkpoint_timestamps@public.codex_continuity_checkpoint` | `migrations/0480_codex_continuity.sql:75` | trigger declaration |
| E1537 | Trigger `commission_touch@public.commission` | `migrations/0001_init.sql:773` | trigger declaration |
| E1538 | Trigger `completion_capsule@ops.work_request` | `migrations/0278_completion_needs_a_capsule_and_a_second_pair_of_eyes.sql:189` | trigger declaration |
| E1539 | Trigger `completion_disposition_insert_guard@ops.completion_disposition` | `migrations/0431_completion_register_schema.sql:371` | trigger declaration |
| E1540 | Trigger `completion_observation_insert_guard@ops.completion_observation` | `migrations/0431_completion_register_schema.sql:368` | trigger declaration |
| E1541 | Trigger `completion_policy_insert_guard@ops.completion_policy` | `migrations/0431_completion_register_schema.sql:365` | trigger declaration |
| E1542 | Trigger `content_piece_touch@public.content_piece` | `migrations/0001_init.sql:841` | trigger declaration |
| E1543 | Trigger `critical_date_touch@public.critical_date` | `migrations/0001_init.sql:487` | trigger declaration |
| E1544 | Trigger `deal_participant_side@public.deal_participant` | `migrations/0060_participant_sides.sql:217` | trigger declaration |
| E1545 | Trigger `deal_touch@public.deal` | `migrations/0001_init.sql:425` | trigger declaration |
| E1546 | Trigger `defect_resolution_evidence_resolves@public.defect_resolution` | `migrations/0872_self_closing_records.sql:228` | trigger declaration |
| E1547 | Trigger `deployment_provider_identity_immutable@ops.deployment` | `migrations/0171_program5_provider_version.sql:150` | trigger declaration |
| E1548 | Trigger `deployment_requires_a_live_approval@ops.deployment` | `migrations/0131_ops_release.sql:254` | trigger declaration |
| E1549 | Trigger `doc_whats_new_lead_creation@public.lead` | `migrations/0766_doc_whats_new_repair.sql:20` | trigger declaration |
| E1550 | Trigger `doctrine_section_retrieval_repair_after_update@public.doctrine_section` | `migrations/0135_situation_retrieval.sql:343` | trigger declaration |
| E1551 | Trigger `engineering_actor_authority_guard_before_update@public.actor` | `migrations/0335_engineering_controller_currentness.sql:66` | trigger declaration |
| E1552 | Trigger `engineering_envelope_supersession_guard@ops.engineering_execution_envelope` | `migrations/0311_sponsored_engineering_executor_authority.sql:75` | trigger declaration |
| E1553 | Trigger `engineering_job_definition_currentness_guard_before_update@ops.job_definition` | `migrations/0335_engineering_controller_currentness.sql:120` | trigger declaration |
| E1554 | Trigger `engineering_reviewer_fact_contract_guard@ops.engineering_reviewer_fact` | `migrations/0335_engineering_controller_currentness.sql:1289` | trigger declaration |
| E1555 | Trigger `engineering_session_terminalization_guard@ops.capability_agent_session` | `migrations/0325_engineering_claim_envelope_eligibility.sql:187` | trigger declaration |
| E1556 | Trigger `engineering_work_request_currentness_guard_before_update@ops.work_request` | `migrations/0335_engineering_controller_currentness.sql:93` | trigger declaration |
| E1557 | Trigger `execution_envelope_environment_binding@ops.execution_envelope_v1` | `migrations/0309_governed_execution_environment_providers.sql:393` | trigger declaration |
| E1558 | Trigger `foundation_assurance_benchmark_acceptance_session@ops.benchmark_manifest_acceptance_receipt` | `migrations/0511_foundation_assurance_minimum_outcome.sql:208` | trigger declaration |
| E1559 | Trigger `foundation_assurance_benchmark_draft_session@ops.benchmark_manifest_draft` | `migrations/0511_foundation_assurance_minimum_outcome.sql:202` | trigger declaration |
| E1560 | Trigger `foundation_assurance_benchmark_review_session@ops.benchmark_manifest_review` | `migrations/0511_foundation_assurance_minimum_outcome.sql:205` | trigger declaration |
| E1561 | Trigger `guidance_authority_binding_validate@ops.guidance_authority_binding` | `migrations/0168_guidance_registry.sql:306` | trigger declaration |
| E1562 | Trigger `guidance_lifecycle_event_validate@ops.guidance_lifecycle_event` | `migrations/0168_guidance_registry.sql:333` | trigger declaration |
| E1563 | Trigger `guidance_revision_validate@ops.guidance_revision` | `migrations/0168_guidance_registry.sql:268` | trigger declaration |
| E1564 | Trigger `guidance_situation_mapping_validate@ops.guidance_situation_mapping` | `migrations/0168_guidance_registry.sql:360` | trigger declaration |
| E1565 | Trigger `j1_clock_append_guard@ops.j1_clock_revision` | `migrations/0509_journey_one_clock_store.sql:1452` | trigger declaration |
| E1566 | Trigger `j1_minimum_admission_guard@ops.j1_minimum_admission` | `migrations/0510_journey_one_clock_input_store.sql:999` | trigger declaration |
| E1567 | Trigger `job_definition_cutover_requires_evidence@ops.job_definition` | `migrations/0149_control_plane_jobs.sql:457` | trigger declaration |
| E1568 | Trigger `job_definition_fence_queued_jobs@ops.job_definition` | `migrations/0169_control_plane_canary_fencing.sql:59` | trigger declaration |
| E1569 | Trigger `lead_touch@public.lead` | `migrations/0001_init.sql:329` | trigger declaration |
| E1570 | Trigger `lease_touch@public.lease` | `migrations/0001_init.sql:758` | trigger declaration |
| E1571 | Trigger `loop_block_touch@public.loop_block` | `migrations/0024_loop_item.sql:224` | trigger declaration |
| E1572 | Trigger `loop_item_touch@public.loop_item` | `migrations/0024_loop_item.sql:226` | trigger declaration |
| E1573 | Trigger `loop_notification_on_event@public.event` | `migrations/0736_loop_notification_queue.sql:153` | trigger declaration |
| E1574 | Trigger `memory_item_immutable_core@public.memory_item` | `migrations/0299_memory_kernel.sql:206` | trigger declaration |
| E1575 | Trigger `memory_item_insert_valid@public.memory_item` | `migrations/0299_memory_kernel.sql:102` | trigger declaration |
| E1576 | Trigger `memory_item_plan_anchor_valid@public.memory_item` | `migrations/0299_memory_kernel.sql:125` | trigger declaration |
| E1577 | Trigger `model_role_pointer_guard@ops.model_role_current_pointer` | `migrations/0542_model_role_store.sql:1382` | trigger declaration |
| E1578 | Trigger `model_role_revision_guard@ops.model_role_revision` | `migrations/0542_model_role_store.sql:1249` | trigger declaration |
| E1579 | Trigger `negotiation_round_touch@public.negotiation_round` | `migrations/0001_init.sql:525` | trigger declaration |
| E1580 | Trigger `next_action_touch@public.next_action` | `migrations/0001_init.sql:469` | trigger declaration |
| E1581 | Trigger `party_touch@public.party` | `migrations/0001_init.sql:266` | trigger declaration |
| E1582 | Trigger `performance_receipt_requires_read_back@ops.run` | `migrations/0172_program5_release_assurance.sql:265` | trigger declaration |
| E1583 | Trigger `pitr_probe_server_stamp@ops.pitr_probe` | `migrations/0597_pitr_probe.sql:70` | trigger declaration |
| E1584 | Trigger `portfolio_acceptance_guard@ops.portfolio_revision_acceptance_receipt` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:811` | trigger declaration |
| E1585 | Trigger `portfolio_proposal_guard@ops.portfolio_revision` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:735` | trigger declaration |
| E1586 | Trigger `portfolio_review_guard@ops.portfolio_revision_review` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:715` | trigger declaration |
| E1587 | Trigger `portfolio_revision_complete@ops.portfolio_revision` | `migrations/0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql:675` | trigger declaration |
| E1588 | Trigger `program5_release_approval_is_joe_owned@ops.release` | `migrations/0219_staging_release_approval_receipt.sql:88` | trigger declaration |
| E1589 | Trigger `program5_release_verifier_is_immutable@ops.release` | `migrations/0205_program5_approval_verifier.sql:48` | trigger declaration |
| E1590 | Trigger `protect_staging_readback_deployment@ops.deployment` | `migrations/0202_staging_release_readback_receipt.sql:273` | trigger declaration |
| E1591 | Trigger `ready_plan_amendment_slice_plan_current@ops.engineering_slice_plan` | `migrations/0532a_canonical_ownership_lease_activation.sql:1118` | trigger declaration |
| E1592 | Trigger `reject_fabricated_drill_row@ops.run` | `migrations/0225_ops_run_fixture_row_guard.sql:63` | trigger declaration |
| E1593 | Trigger `release_approval_requires_recovery_rehearsal@ops.release` | `migrations/0172_program5_release_assurance.sql:232` | trigger declaration |
| E1594 | Trigger `release_assurance_immutable@ops.release` | `migrations/0202_staging_release_readback_receipt.sql:42` | trigger declaration |
| E1595 | Trigger `release_completed_append_only_delete@ops.release` | `migrations/0301_release_schema_provenance_gate.sql:163` | trigger declaration |
| E1596 | Trigger `release_completed_append_only_update@ops.release` | `migrations/0301_release_schema_provenance_gate.sql:146` | trigger declaration |
| E1597 | Trigger `release_completion_requires_a_read_back@ops.release` | `migrations/0172_program5_release_assurance.sql:350` | trigger declaration |
| E1598 | Trigger `release_plan_revision_invalidates_approval@ops.release` | `migrations/0131_ops_release.sql:217` | trigger declaration |
| E1599 | Trigger `release_provider_identity_immutable@ops.release` | `migrations/0171_program5_provider_version.sql:145` | trigger declaration |
| E1600 | Trigger `release_schema_declaration_matches_live@ops.release` | `migrations/0301_release_schema_provenance_gate.sql:124` | trigger declaration |
| E1601 | Trigger `retired_rule_delivery_cleanup@public.rule` | `migrations/0463_retired_rule_delivery_cleanup.sql:20` | trigger declaration |
| E1602 | Trigger `retrieval_proposal_guard_before_delete@public.retrieval_proposal` | `migrations/0135_situation_retrieval.sql:396` | trigger declaration |
| E1603 | Trigger `retrieval_proposal_guard_before_update@public.retrieval_proposal` | `migrations/0135_situation_retrieval.sql:394` | trigger declaration |
| E1604 | Trigger `rule_activation_requires_admission@public.rule` | `migrations/0349_versioned_rule_amendment.sql:401` | trigger declaration |
| E1605 | Trigger `rule_approval_lifecycle_anchor_valid@ops.rule_approval_lifecycle_anchor` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:325` | trigger declaration |
| E1606 | Trigger `rule_insert_provenance@public.rule` | `migrations/0785_rule_teach_supersession.sql:32` | trigger declaration |
| E1607 | Trigger `rule_load_layer_packs_exist@ops.rule_load_layer` | `migrations/0291_rule_delivery_layers.sql:128` | trigger declaration |
| E1608 | Trigger `rule_touch@public.rule` | `migrations/0068_rule_version.sql:45` | trigger declaration |
| E1609 | Trigger `scac_artifact_manifest_insert_exact@ops.scac_artifact_manifest` | `migrations/0456_siep13_artifact_registry.sql:155` | trigger declaration |
| E1610 | Trigger `scac_artifact_signature_insert_exact@ops.scac_artifact_signature` | `migrations/0456_siep13_artifact_registry.sql:157` | trigger declaration |
| E1611 | Trigger `scac_artifact_transparency_insert_exact@ops.scac_artifact_transparency_entry` | `migrations/0456_siep13_artifact_registry.sql:159` | trigger declaration |
| E1612 | Trigger `scac_device_benchmark_insert_exact@ops.scac_device_benchmark_receipt` | `migrations/0460_siep15_device_enrollment.sql:213` | trigger declaration |
| E1613 | Trigger `scac_device_fact_insert_exact@ops.scac_device_fact_receipt` | `migrations/0460_siep15_device_enrollment.sql:211` | trigger declaration |
| E1614 | Trigger `scac_root_attestation_insert_exact@ops.scac_root_custodian_attestation` | `migrations/0458_siep14_root_trust.sql:236` | trigger declaration |
| E1615 | Trigger `scac_root_event_insert_exact@ops.scac_root_trust_event` | `migrations/0458_siep14_root_trust.sql:234` | trigger declaration |
| E1616 | Trigger `session_work_touch@public.session_work` | `migrations/0193_session_work.sql:49` | trigger declaration |
| E1617 | Trigger `siep_engineering_evidence_binding_contract_guard@ops.siep_job_evidence_binding` | `migrations/0335_engineering_controller_currentness.sql:1382` | trigger declaration |
| E1618 | Trigger `siep_joe_decision_event_guard_before_write@public.event` | `migrations/0324_siep_program_authority.sql:399` | trigger declaration |
| E1619 | Trigger `siep_program_identity_guard_before_update@ops.work_request` | `migrations/0324_siep_program_authority.sql:1096` | trigger declaration |
| E1620 | Trigger `siep_program_transition_guard_before_update@ops.work_request` | `migrations/0324_siep_program_authority.sql:1131` | trigger declaration |
| E1621 | Trigger `source_capture_touch@public.source_capture` | `migrations/0070_source_capture.sql:66` | trigger declaration |
| E1622 | Trigger `sourced_plan_acceptance_captures_source_merge_scope@ops.sourced_work_request_plan_acceptance_receipt` | `migrations/0470_source_merge_authority_projection.sql:95` | trigger declaration |
| E1623 | Trigger `sourced_work_request_is_immutable@ops.work_request` | `migrations/0426_withdraw_a_work_request_captured_in_error.sql:215` | trigger declaration |
| E1624 | Trigger `sourced_work_shape_revision_requires_effective_required@ops.work_shape_revision` | `migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql:999` | trigger declaration |
| E1625 | Trigger `space_touch@public.space` | `migrations/0001_init.sql:604` | trigger declaration |
| E1626 | Trigger `staging_release_approval_is_typed@ops.release` | `migrations/0219_staging_release_approval_receipt.sql:80` | trigger declaration |
| E1627 | Trigger `tour_assertion_rights_guard@ops.tour_field_assertion` | `migrations/0318_tour_operations_foundation.sql:264` | trigger declaration |
| E1628 | Trigger `tour_conflict_participant_guard@ops.tour_fact_conflict_participant` | `migrations/0318_tour_operations_foundation.sql:270` | trigger declaration |
| E1629 | Trigger `tour_conflict_resolution_guard@ops.tour_conflict_resolution_receipt` | `migrations/0318_tour_operations_foundation.sql:271` | trigger declaration |
| E1630 | Trigger `tour_coordinate_entrance_verification_receipt_guard@ops.tour_coordinate_entrance_verification_receipt` | `migrations/0428_tour_property_identity_jurisdiction.sql:372` | trigger declaration |
| E1631 | Trigger `tour_jurisdiction_dataset_rights_guard@ops.tour_jurisdiction_dataset` | `migrations/0428_tour_property_identity_jurisdiction.sql:366` | trigger declaration |
| E1632 | Trigger `tour_membership_seal_guard@ops.tour_property_membership` | `migrations/0318_tour_operations_foundation.sql:265` | trigger declaration |
| E1633 | Trigger `tour_projection_creation_guard@ops.tour_public_projection` | `migrations/0318_tour_operations_foundation.sql:266` | trigger declaration |
| E1634 | Trigger `tour_projection_fact_client_allowlist@ops.tour_public_projection_fact` | `migrations/0608_tour_client_field_allowlist.sql:335` | trigger declaration |
| E1635 | Trigger `tour_projection_fact_guard@ops.tour_public_projection_fact` | `migrations/0318_tour_operations_foundation.sql:267` | trigger declaration |
| E1636 | Trigger `tour_projection_seal_guard@ops.tour_public_projection_seal_receipt` | `migrations/0318_tour_operations_foundation.sql:268` | trigger declaration |
| E1637 | Trigger `tour_property_address_assertion_rights_guard@ops.tour_property_address_assertion` | `migrations/0428_tour_property_identity_jurisdiction.sql:358` | trigger declaration |
| E1638 | Trigger `tour_property_building_assertion_rights_guard@ops.tour_property_building_assertion` | `migrations/0428_tour_property_identity_jurisdiction.sql:362` | trigger declaration |
| E1639 | Trigger `tour_property_coordinate_candidate_rights_guard@ops.tour_property_coordinate_candidate` | `migrations/0428_tour_property_identity_jurisdiction.sql:370` | trigger declaration |
| E1640 | Trigger `tour_property_identifier_alias_rights_guard@ops.tour_property_identifier_alias` | `migrations/0428_tour_property_identity_jurisdiction.sql:354` | trigger declaration |
| E1641 | Trigger `tour_property_identifier_assertion_rights_guard@ops.tour_property_identifier_assertion` | `migrations/0428_tour_property_identity_jurisdiction.sql:352` | trigger declaration |
| E1642 | Trigger `tour_property_identity_lineage_rights_guard@ops.tour_property_identity_lineage` | `migrations/0428_tour_property_identity_jurisdiction.sql:356` | trigger declaration |
| E1643 | Trigger `tour_property_jurisdiction_assertion_rights_guard@ops.tour_property_jurisdiction_assertion` | `migrations/0428_tour_property_identity_jurisdiction.sql:368` | trigger declaration |
| E1644 | Trigger `tour_property_membership_client_label@ops.tour_property_membership` | `migrations/0608_tour_client_field_allowlist.sql:351` | trigger declaration |
| E1645 | Trigger `tour_property_parcel_assertion_rights_guard@ops.tour_property_parcel_assertion` | `migrations/0428_tour_property_identity_jurisdiction.sql:360` | trigger declaration |
| E1646 | Trigger `tour_property_provider_projection_rights_guard@ops.tour_property_provider_projection` | `migrations/0428_tour_property_identity_jurisdiction.sql:364` | trigger declaration |
| E1647 | Trigger `tour_public_projection_append_only@ops.tour_public_projection` | `migrations/0427_tour_rights_projection_hardening.sql:352` | trigger declaration |
| E1648 | Trigger `tour_publication_creation_guard@ops.tour_publication` | `migrations/0427_tour_rights_projection_hardening.sql:346` | trigger declaration |
| E1649 | Trigger `tour_rights_lineage_guard@ops.tour_rights_receipt` | `migrations/0318_tour_operations_foundation.sql:269` | trigger declaration |
| E1650 | Trigger `tour_share_revocation_receipt_guard@ops.tour_share_grant_revocation_receipt` | `migrations/0427_tour_rights_projection_hardening.sql:349` | trigger declaration |
| E1651 | Trigger `tour_share_rotation_guard@ops.tour_share_grant` | `migrations/0318_tour_operations_foundation.sql:272` | trigger declaration |
| E1652 | Trigger `tour_source_rights_guard@ops.tour_source_evidence` | `migrations/0318_tour_operations_foundation.sql:263` | trigger declaration |
| E1653 | Trigger `v5_a05_cadence_receipt_stamp_tenant@ops.v5_a05_cadence_receipt` | `migrations/0617_delivery_cadence_a05.sql:104` | trigger declaration |
| E1654 | Trigger `validate_recovery_rehearsal_run@ops.run` | `migrations/0202_staging_release_readback_receipt.sql:299` | trigger declaration |
| E1655 | Trigger `vendor_touch@public.vendor` | `migrations/0001_init.sql:370` | trigger declaration |
| E1656 | Trigger `work_in_progress_limit@ops.work_request` | `migrations/0276_work_in_progress_limit.sql:99` | trigger declaration |
| E1657 | Trigger `work_request_execution_environment_bind@ops.work_request_execution_assignment` | `migrations/0309_governed_execution_environment_providers.sql:350` | trigger declaration |
| E1658 | Trigger `work_request_shape_gate@ops.work_request` | `migrations/0132_work_shape_revision.sql:195` | trigger declaration |
| E1659 | Trigger `z_release_readiness_is_typed@ops.release` | `migrations/0540_release_readiness_without_repeat_approval.sql:134` | trigger declaration |
| E1660 | View `ops.completion_current_observation` | `migrations/0431_completion_register_schema.sql:420` | not established |
| E1661 | View `ops.completion_dimension_matrix` | `migrations/0431_completion_register_schema.sql:437` | not established |
| E1662 | View `ops.completion_projection` | `migrations/0431_completion_register_schema.sql:512` | not established |
| E1663 | View `ops.system_work_actor_scope` | `migrations/0826_system_work_census_read_scope.sql:2` | traced from source text |
| E1664 | View `ops.system_work_plan_scope` | `migrations/0826_system_work_census_read_scope.sql:4` | traced from source text |
| E1665 | View `ops.system_work_rule_scope` | `migrations/0826_system_work_census_read_scope.sql:3` | traced from source text |
| E1666 | View `ops.v_capability_program_next` | `migrations/0324_siep_program_authority.sql:327` | not established |
| E1667 | View `ops.v_check_run` | `migrations/0115_ops_observability.sql:244` | not established |
| E1668 | View `ops.v_control_plane_capability_candidate` | `migrations/0162_control_plane_input_views.sql:8` | not established |
| E1669 | View `ops.v_control_plane_doctrine_due` | `migrations/0162_control_plane_input_views.sql:194` | not established |
| E1670 | View `ops.v_control_plane_doctrine_failures` | `migrations/0162_control_plane_input_views.sql:200` | not established |
| E1671 | View `ops.v_control_plane_health_evidence` | `migrations/0162_control_plane_input_views.sql:167` | not established |
| E1672 | View `ops.v_control_plane_system_prune_candidates` | `migrations/0162_control_plane_input_views.sql:208` | not established |
| E1673 | View `ops.v_cost_refusal_metric` | `migrations/0165_control_plane_cost_refusals.sql:71` | not established |
| E1674 | View `ops.v_guidance_constraint` | `migrations/0168_guidance_registry.sql:563` | not established |
| E1675 | View `ops.v_guidance_current` | `migrations/0170_guidance_import_lifecycle.sql:638` | traced from source text |
| E1676 | View `ops.v_guidance_doctrine_retrieval` | `migrations/0168_guidance_registry.sql:602` | not established |
| E1677 | View `ops.v_guidance_materialized_current` | `migrations/0170_guidance_import_lifecycle.sql:605` | traced from source text |
| E1678 | View `ops.v_guidance_materialized_situation_mapping_current` | `migrations/0170_guidance_import_lifecycle.sql:631` | traced from source text |
| E1679 | View `ops.v_guidance_precedent` | `migrations/0168_guidance_registry.sql:578` | not established |
| E1680 | View `ops.v_guidance_registry_state` | `migrations/0168_guidance_registry.sql:616` | traced from source text |
| E1681 | View `ops.v_guidance_revision_state` | `migrations/0168_guidance_registry.sql:524` | traced from source text |
| E1682 | View `ops.v_guidance_situation_mapping_current` | `migrations/0170_guidance_import_lifecycle.sql:648` | not established |
| E1683 | View `ops.v_job_control` | `migrations/0149_control_plane_jobs.sql:476` | traced from source text |
| E1684 | View `ops.v_job_cost` | `migrations/0149_control_plane_jobs.sql:485` | not established |
| E1685 | View `ops.v_job_run` | `migrations/0115_ops_observability.sql:242` | traced from source text |
| E1686 | View `ops.v_provider_route` | `migrations/0149_control_plane_jobs.sql:34` | not established |
| E1687 | View `ops.v_release_manifest` | `migrations/0172_program5_release_assurance.sql:91` | not established |
| E1688 | View `ops.v_release_schema_provenance` | `migrations/0302_release_schema_provenance_collation.sql:10` | not established |
| E1689 | View `ops.v_rule_applicability` | `migrations/0148_control_plane_admission.sql:109` | not established |
| E1690 | View `ops.v_rule_enforcement_status` | `migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql:425` | traced from source text |
| E1691 | View `ops.v_service_environment_health` | `migrations/0308_calendar_prebrief_service_health.sql:18` | traced from source text |
| E1692 | View `ops.v_trace` | `migrations/0123_trace_incident_recurrence.sql:69` | traced from source text |
| E1693 | View `ops.v_work_shape_current` | `migrations/0132_work_shape_revision.sql:99` | not established |
| E1694 | View `public.prospect_pool` | `migrations/0048_candidate_pool.sql:40` | traced from source text |
| E1695 | View `public.v_calendar_prebrief_canary_snapshot_status` | `migrations/0229_calendar_prebrief_projection.sql:850` | not established |
| E1696 | View `public.v_calendar_prebrief_events` | `migrations/0507_export_views_one_row_per_subject.sql:130` | not established |
| E1697 | View `public.v_calendar_prebrief_snapshot_status` | `migrations/0229_calendar_prebrief_projection.sql:844` | not established |
| E1698 | View `public.v_campaign_scorecard` | `migrations/0066_marketing_campaign_and_measurement.sql:510` | traced from source text |
| E1699 | View `public.v_capture_candidate_queue` | `migrations/0081_capture_bridge.sql:56` | traced from source text |
| E1700 | View `public.v_capture_coverage` | `migrations/0034_coverage_and_denominators.sql:26` | not established |
| E1701 | View `public.v_capture_session_status` | `migrations/0081_capture_bridge.sql:51` | traced from source text |
| E1702 | View `public.v_claim_card` | `migrations/0086_candidate_claim_card.sql:112` | traced from source text |
| E1703 | View `public.v_client_account` | `migrations/0061_national_account.sql:336` | traced from source text |
| E1704 | View `public.v_client_engagement_status` | `migrations/0920_prospect_engagement_status.sql:22` | traced from source text |
| E1705 | View `public.v_client_lease_radar` | `migrations/0814_lease_radar_read.sql:3` | traced from source text |
| E1706 | View `public.v_code_finding` | `migrations/0101_code_review_subject.sql:168` | not established |
| E1707 | View `public.v_compiled_rules` | `migrations/0130_compiled_rules_supersedes.sql:71` | traced from source text |
| E1708 | View `public.v_contact` | `migrations/0054_contact_type.sql:31` | not established |
| E1709 | View `public.v_control_plane_deal_history_queue` | `migrations/0387_control_plane_record_queue_priority_tiers.sql:185` | not established |
| E1710 | View `public.v_control_plane_enrichment_queue` | `migrations/0387_control_plane_record_queue_priority_tiers.sql:65` | not established |
| E1711 | View `public.v_control_plane_idea_candidates` | `migrations/0162_control_plane_input_views.sql:130` | not established |
| E1712 | View `public.v_control_plane_npi_delta` | `migrations/0162_control_plane_input_views.sql:99` | not established |
| E1713 | View `public.v_control_plane_radar_candidates` | `migrations/0162_control_plane_input_views.sql:120` | not established |
| E1714 | View `public.v_control_plane_social_coverage` | `migrations/0162_control_plane_input_views.sql:145` | not established |
| E1715 | View `public.v_control_plane_social_feature_cells` | `migrations/0191_learning_feature_cell_view.sql:21` | not established |
| E1716 | View `public.v_control_plane_social_measured_pieces` | `migrations/0189_social_pull_collector_views.sql:44` | not established |
| E1717 | View `public.v_control_plane_social_metric_exports` | `migrations/0162_control_plane_input_views.sql:153` | not established |
| E1718 | View `public.v_control_plane_social_placement_identity` | `migrations/0189_social_pull_collector_views.sql:36` | not established |
| E1719 | View `public.v_control_plane_social_sources` | `migrations/0162_control_plane_input_views.sql:139` | not established |
| E1720 | View `public.v_counterparty_bluff` | `migrations/0064_counterparty_scorecard.sql:301` | not established |
| E1721 | View `public.v_counterparty_history` | `migrations/0027_review_fixes_graph_views.sql:54` | traced from source text |
| E1722 | View `public.v_counterparty_scorecard` | `migrations/0064_counterparty_scorecard.sql:399` | not established |
| E1723 | View `public.v_deal_board` | `migrations/0911_deal_board_client_identity.sql:3` | traced from source text |
| E1724 | View `public.v_deal_reconciliation_read` | `migrations/0782_deal_invoice_read_fields.sql:67` | traced from source text |
| E1725 | View `public.v_deal_room_account` | `migrations/0771_local_deal_board_evidence.sql:51` | traced from source text |
| E1726 | View `public.v_deal_room_action` | `migrations/0936_deal_alert_items.sql:20` | traced from source text |
| E1727 | View `public.v_deal_room_activity` | `migrations/0090_deal_room_workspaces.sql:113` | traced from source text |
| E1728 | View `public.v_deal_room_board` | `migrations/0936_deal_alert_items.sql:28` | traced from source text |
| E1729 | View `public.v_deal_room_commission` | `migrations/0922_deal_checklist_commissions.sql:2` | traced from source text |
| E1730 | View `public.v_deal_room_critical_date` | `migrations/0079_deal_room_api.sql:84` | traced from source text |
| E1731 | View `public.v_deal_room_current_lease` | `migrations/0800_deal_timeline_lease_read.sql:3` | traced from source text |
| E1732 | View `public.v_deal_room_deal` | `migrations/0092_deal_operating_state.sql:43` | not established |
| E1733 | View `public.v_deal_room_document` | `migrations/0090_deal_room_workspaces.sql:141` | traced from source text |
| E1734 | View `public.v_deal_room_event` | `migrations/0079_deal_room_api.sql:61` | traced from source text |
| E1735 | View `public.v_deal_room_negotiation` | `migrations/0090_deal_room_workspaces.sql:135` | traced from source text |
| E1736 | View `public.v_deal_room_note` | `migrations/0079_deal_room_api.sql:80` | traced from source text |
| E1737 | View `public.v_deal_room_participant` | `migrations/0090_deal_room_workspaces.sql:119` | traced from source text |
| E1738 | View `public.v_deal_room_phase_change` | `migrations/0771_local_deal_board_evidence.sql:76` | traced from source text |
| E1739 | View `public.v_deal_room_phase_start` | `migrations/0916_deal_phase_start_dates.sql:3` | traced from source text |
| E1740 | View `public.v_deal_room_premises` | `migrations/0090_deal_room_workspaces.sql:127` | traced from source text |
| E1741 | View `public.v_deal_room_presence` | `migrations/0079_deal_room_api.sql:71` | traced from source text |
| E1742 | View `public.v_deal_room_session` | `migrations/0090_deal_room_workspaces.sql:97` | traced from source text |
| E1743 | View `public.v_decision_entry` | `migrations/0110_decision_entry_occurred_at.sql:32` | traced from source text |
| E1744 | View `public.v_defect` | `migrations/0872_self_closing_records.sql:246` | not established |
| E1745 | View `public.v_defect_class` | `migrations/0103_defect_log.sql:102` | traced from source text |
| E1746 | View `public.v_detector_health` | `migrations/0034_coverage_and_denominators.sql:56` | not established |
| E1747 | View `public.v_drip_conflict` | `migrations/0045_drip_conflict.sql:51` | not established |
| E1748 | View `public.v_expired_verification` | `migrations/0104_reverify_queue_supersession.sql:54` | traced from source text |
| E1749 | View `public.v_export_clients` | `migrations/0018_client_status_rationalization.sql:226` | traced from source text |
| E1750 | View `public.v_export_clients_active` | `migrations/0018_client_status_rationalization.sql:265` | not established |
| E1751 | View `public.v_export_deals` | `migrations/0074_deal_city_lane.sql:76` | traced from source text |
| E1752 | View `public.v_export_dossier_analysis` | `migrations/0028_dossier_analysis.sql:154` | not established |
| E1753 | View `public.v_export_dossier_subject` | `migrations/0028_dossier_analysis.sql:109` | not established |
| E1754 | View `public.v_export_leads` | `migrations/0507_export_views_one_row_per_subject.sql:36` | traced from source text |
| E1755 | View `public.v_export_loops` | `migrations/0042_export_loops_domain.sql:25` | not established |
| E1756 | View `public.v_export_loops_closed` | `migrations/0024_loop_item.sql:392` | not established |
| E1757 | View `public.v_export_source_captures` | `migrations/0070_source_capture.sql:78` | not established |
| E1758 | View `public.v_export_vendors` | `migrations/0507_export_views_one_row_per_subject.sql:80` | traced from source text |
| E1759 | View `public.v_field_history` | `migrations/0106_precedent_and_point_in_time.sql:84` | traced from source text |
| E1760 | View `public.v_growth_slope` | `migrations/0071_forgetting_policy.sql:39` | not established |
| E1761 | View `public.v_ingest_backlog` | `migrations/0071_forgetting_policy.sql:77` | not established |
| E1762 | View `public.v_integrity_digest` | `migrations/0879_verb_usage_digest.sql:44` | traced from source text |
| E1763 | View `public.v_investigation` | `migrations/0098_investigation_control_plane.sql:134` | traced from source text |
| E1764 | View `public.v_invoice_tracker` | `migrations/0841_invoice_tracker.sql:5` | traced from source text |
| E1765 | View `public.v_last_touch` | `migrations/0033_deal_inherits_client_touch.sql:23` | traced from source text |
| E1766 | View `public.v_lead_board` | `migrations/0847_lead_score_owner_reader_repair.sql:10` | traced from source text |
| E1767 | View `public.v_lead_board_stage` | `migrations/0293_lead_board_read_model.sql:37` | traced from source text |
| E1768 | View `public.v_lead_client_link` | `migrations/0102_lead_client_link.sql:47` | traced from source text |
| E1769 | View `public.v_lead_hot` | `migrations/0004_roles_views.sql:89` | traced from source text |
| E1770 | View `public.v_lead_prospect_engagement` | `migrations/0920_prospect_engagement_status.sql:28` | traced from source text |
| E1771 | View `public.v_lead_stage_transition` | `migrations/0892_lead_stage_transition_proof.sql:4` | traced from source text |
| E1772 | View `public.v_lead_workspace` | `migrations/0847_lead_score_owner_reader_repair.sql:53` | traced from source text |
| E1773 | View `public.v_lead_workspace_detail` | `migrations/0892_lead_stage_transition_proof.sql:16` | traced from source text |
| E1774 | View `public.v_lead_workspace_lifecycle` | `migrations/0845_lead_archived_stage.sql:21` | traced from source text |
| E1775 | View `public.v_loop_bell_cap` | `migrations/0041_classify_loops.sql:142` | not established |
| E1776 | View `public.v_loop_no_blocker` | `migrations/0083_loop_gate_cutover.sql:37` | not established |
| E1777 | View `public.v_loop_promotion_due` | `migrations/0024_loop_item.sql:320` | not established |
| E1778 | View `public.v_loop_proximity` | `migrations/0084_loop_proximity.sql:50` | traced from source text |
| E1779 | View `public.v_loops` | `migrations/0024_loop_item.sql:272` | not established |
| E1780 | View `public.v_md_ledger_entry` | `migrations/0030_md_ledger_render.sql:35` | not established |
| E1781 | View `public.v_media_recommendation` | `migrations/0111_media_recommendation.sql:67` | not established |
| E1782 | View `public.v_negotiation_deal` | `migrations/0064_counterparty_scorecard.sql:180` | not established |
| E1783 | View `public.v_negotiation_position` | `migrations/0064_counterparty_scorecard.sql:119` | not established |
| E1784 | View `public.v_orphaned_edge` | `migrations/0062_orphaned_edge.sql:187` | not established |
| E1785 | View `public.v_orphaned_role` | `migrations/0054_contact_type.sql:77` | not established |
| E1786 | View `public.v_partner_room_turn` | `migrations/0298_partner_room_origin.sql:24` | traced from source text |
| E1787 | View `public.v_party_graph` | `migrations/0020_party_link_kind_vocabulary.sql:167` | traced from source text |
| E1788 | View `public.v_party_identity_correction` | `migrations/0852_party_identity_correction_view.sql:8` | not established |
| E1789 | View `public.v_placement_measurement` | `migrations/0066_marketing_campaign_and_measurement.sql:423` | traced from source text |
| E1790 | View `public.v_placement_metric_latest` | `migrations/0066_marketing_campaign_and_measurement.sql:409` | traced from source text |
| E1791 | View `public.v_pool` | `migrations/0023_prospect_pool.sql:172` | not established |
| E1792 | View `public.v_rate_normalized` | `migrations/0004_roles_views.sql:24` | not established |
| E1793 | View `public.v_record_flag_subject` | `migrations/0101_code_review_subject.sql:118` | traced from source text |
| E1794 | View `public.v_ref_index` | `migrations/0069_enrichment_write_gap.sql:64` | traced from source text |
| E1795 | View `public.v_renewal_decision_queue` | `migrations/0279_carr_lease_renewal_provider.sql:140` | traced from source text |
| E1796 | View `public.v_renewal_decision_queue_status` | `migrations/0279_carr_lease_renewal_provider.sql:171` | traced from source text |
| E1797 | View `public.v_retrieval_concepts_without_targets` | `migrations/0135_situation_retrieval.sql:354` | not established |
| E1798 | View `public.v_retrieval_mapping_repair` | `migrations/0135_situation_retrieval.sql:347` | not established |
| E1799 | View `public.v_rule_lookup` | `migrations/0785_rule_teach_supersession.sql:3` | traced from source text |
| E1800 | View `public.v_signal_queue` | `migrations/0098_investigation_control_plane.sql:127` | traced from source text |
| E1801 | View `public.v_source_attribution` | `migrations/0027_review_fixes_graph_views.sql:96` | traced from source text |
| E1802 | View `public.v_stale_records` | `migrations/0004_roles_views.sql:99` | traced from source text |
| E1803 | View `public.v_subject_timeline` | `migrations/0082_timeline_event_summary.sql:64` | traced from source text |
| E1804 | View `public.v_today_triage` | `migrations/0094_call_mode_post_call.sql:116` | traced from source text |
| E1805 | View `public.v_vendor_level_suggestion` | `migrations/0069_enrichment_write_gap.sql:152` | not established |
| E1806 | View `public.v_vendor_needs_type` | `migrations/0069_enrichment_write_gap.sql:221` | not established |

### Inventory validation

- Every table name is unique and each of the 5,620 columns appears in exactly one table/column group.
- All 588 migration files parse as PostgreSQL SQL; literal nested DO DDL parsed without errors. No dynamic CREATE TABLE command was found in the migration search.
- Defaults changed by ALTER COLUMN, table/column renames, table drops and inherited columns are accounted for in the declaration catalogue.
- The dependency model also retained 165 view declarations, 1598 routine names and 513 literal trigger declarations. Dynamic declarations and overloads limit these dependency counts.
- Source extraction found 1290 candidate SQL strings. 4771 source/routine/view SQL units parsed. 11115 PL/pgSQL expressions, partial strings or dynamic SQL units were not complete standalone SQL and fell back to lexical evidence; these failures are a coverage limitation, not application failures.
- No database connection, migration execution, production write or deploy was performed.
