# migrations/ — the record layer's versioned DDL

*Scaffolded 2026-07-30 by the record-layer build session. Design doc:
`CARR AI/DNA/Deal Management/record-layer/` (blueprint + schema v2 + the
binding stress-test addendum). STATUS: schema v2 pending Joe's grain review
(blueprint §11) — nothing here has been applied to any database yet.*

## Rules

- **Forward-only, numbered.** `NNNN_name.sql`, applied in filename order by
  `tools/migrate.py` (`run.sh migrate`). A bad migration is fixed by a NEW
  migration; the runner sha-checks applied files and refuses drift.
- **Rehearse on a Neon branch first.** Risky changes run against a branch of
  production data before production (blueprint §4). Build sessions get
  BRANCH credentials only, never the production writer (addendum A14).
- **Bound an authorized production prefix explicitly.** When newer unrelated
  files are also pending, `bin/migrate-prod.sh --through NNNN_name.sql` lists
  the exact forward prefix selected and every later file held back. Add
  `--apply` only after that displayed prefix is approved. The boundary must be
  an exact checked-in filename; it cannot skip an earlier pending dependency
  or hand-pick files out of order.
- **0001 mirrors the vault schema file.** `0001_init.sql` is the applied copy
  of `record-layer/schema-draft-2026-07-30.sql` (v2). Until first apply, keep
  them identical — after first apply, the vault file freezes as the design
  record and changes land here as new migrations.
- **Every new migration declares its risk in its header** (the comment lines
  before the first statement), checked by `ops/migration-safety-gate.py`:

  ```sql
  -- rollback: <how to undo it>            (or: -- rollback: forward-only — <why>)
  -- expand-contract: contract — <why>     (drop, rename, type change, NOT NULL
                                            without default, truncate, unbounded delete)
  -- lock-review: <table size, why ok>     (non-concurrent index, validated
                                            constraint, table rewrite, vacuum full)
  ```

  Expand adds the new shape beside the old; contract removes the old shape only
  after every reader and writer has moved. A contract migration lands in its own
  PR, after the expand release.
- **db/schema.sql carries every migration.** A PR that adds a migration also
  regenerates the snapshot: `ops/migration-shadow.py --write` (PostgreSQL 18),
  or commit the `migration-shadow-schema-<sha>` artifact the CI migration job
  uploads. That job builds the base branch's snapshot on a throwaway
  PostgreSQL 18 cluster, applies the PR's migrations, and requires the committed
  snapshot to equal the result.
- **Seeds are honest.** `0002_seed.sql` seeds only documented vocab;
  lead stages come from the live registry at import, human-reviewed, never
  guessed.

## Files

| file | what |
|---|---|
| `0001_init.sql` | Full schema v2: tables, triggers, indexes, sequences (addendum §A + §D applied) |
| `0002_seed.sql` | Actors, reference vocab, system_config thresholds |
| (build day) `0003_roles_views.sql` | carr_writer / carr_reader roles + the views (v_deal_board, v_export_deals, v_integrity_digest, ...) — needs the Neon project to exist so grants bind to real roles |

## Two migrations name a file that is not themselves (2026-08-15)

`0131_ops_release.sql` raises `0130 FAILED: ...` from eleven places in its own
proof block, names its probe row `migration-0130-proof`, and says "before 0130 it
pointed at nothing". `0132_work_shape_revision.sql` raises `0130 FAILED` too and
describes "pre-0130 ready rows".

**Both mean themselves.** They were written as `0130`, and the series was
renumbered the same day to restore unique ordering. `0130` is now
`0130_compiled_rules_supersedes.sql`, about something else entirely, so anyone
following one of those messages lands in the wrong file.

**The labels cannot be corrected, by design.** `tools/migrate.py` records a
sha256 per applied migration and refuses any applied file whose content changed:
"was EDITED after being applied (sha mismatch). Write a new migration instead;
never rewrite an applied one." Both files are applied to production and staging,
so editing a comment in either would break every future migrate run on both.

**The rule this leaves behind.** A rename inside a checksummed, append-only
series gets exactly one chance to fix the file's own self-references, and that
chance closes the moment the migration applies anywhere. Renaming and rewriting
the internals are one commit, or they are never.

## Frozen numeric collisions (2026-08-16)

Migration identity in the database is the **full filename**: `schema_migrations`
uses `filename` as its primary key and stores that file's SHA-256. The four-digit
prefix is nevertheless a single global allocation namespace. Concurrent branches
historically produced a small number of duplicate prefixes, culminating in three
different `0169` files merging in sequence:

- `0169_control_plane_canary_fencing.sql`
- `0169_hermes_pilot_actor.sql`
- `0169_program5_release_binding.sql`

The Control Plane file was applied and read back in isolated staging before the
other two merged. **All three filenames and contents are now immutable.** An
environment with the Control Plane filename already in its ledger correctly sees
only the Hermes and Program 5 filenames as pending. A fresh environment applies
all three in lexical filename order. Renaming, deleting, consolidating, or editing
any of them would turn a harmless numeric ambiguity into a new pending migration
or an applied-file SHA mismatch.

`tools/migration_number_contract.py` freezes the exact filename sets for every
historical duplicate prefix, and `tools/migrate.py` refuses any new or altered
collision. `tools/next-migration.py` reports the frozen sets and still allocates
above the highest four-digit claim across `origin/main` and every local worktree.
The rule going forward is therefore simple: preserve the frozen history and use
the allocator immediately before creating every new migration.

### Exact pre-renumber ledger aliases

Isolated Control Plane staging also contains twelve filenames from an early
branch that was applied before its migrations were renumbered. Those absent
filenames are the only exception to the runner's missing-applied-file refusal:

| applied legacy filename | required forward migration |
|---|---|
| `0134_control_plane_admission.sql` | `0148_control_plane_admission.sql` |
| `0135_control_plane_jobs.sql` | `0149_control_plane_jobs.sql` |
| `0136_control_plane_job_fixes.sql` | `0150_control_plane_job_fixes.sql` |
| `0137_control_plane_admission_grants.sql` | `0151_control_plane_admission_grants.sql` |
| `0138_rule_writer_grants.sql` | `0152_rule_writer_grants.sql` |
| `0139_control_plane_resilience.sql` | `0153_control_plane_resilience.sql` |
| `0140_control_plane_cost_release.sql` | `0154_control_plane_cost_release.sql` |
| `0141_rule_applicability_wildcard.sql` | `0155_rule_applicability_wildcard.sql` |
| `0142_control_plane_input_grants.sql` | `0156_control_plane_input_grants.sql` |
| `0143_control_plane_runtime_guards.sql` | `0157_control_plane_runtime_guards.sql` |
| `0144_job_timeout_receipts.sql` | `0158_job_timeout_receipts.sql` |
| `0145_control_plane_evidence_grants.sql` | `0159_control_plane_evidence_grants.sql` |

The aliases do not mark their forward migrations applied: those files still run
and contain the idempotent convergence guards needed by an old-equivalent
schema. The runner merely accepts the exact legacy ledger rows as known history
while requiring every mapped forward file to remain present. Unrelated current
files in the same numeric band—especially `0134_release_abandon_reason.sql`,
`0135_situation_retrieval.sql`, and `0136_release_manifest_view_grant.sql`—are
not aliases and receive no exemption.
