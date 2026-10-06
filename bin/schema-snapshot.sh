#!/bin/sh
# schema-snapshot.sh — write (or verify) db/schema.sql, the checked-in structure
# of the production database.
#
# WHY THIS EXISTS. The 130 migrations were treated as the way to build a
# database. On 2026-08-13 CI applied them to a genuinely empty Postgres for the
# first time and proved they cannot: several are DATA backfills carrying guards
# like "remapped ZERO deals — stop and report, do not force". Those guards are
# correct — they catch a backfill that silently did nothing to production — but
# they assert on business data that only production has, so an empty database
# legitimately refuses them. That is by design, not a bug, and no amount of
# adding rows fixes it.
#
# THE DECISION (2026-08-13, internal, mine to make per the escalation gate).
# Stop treating replay-from-zero as how an environment gets built. This snapshot
# is the structure; the migrations govern changes going FORWARD. Doctrine's
# requirement that "a fresh non-production environment can be reconstructed from
# repository declarations" is still met, because this file IS a repository
# declaration — and unlike the replay it actually works.
#
# The two rejected alternatives, recorded so nobody re-litigates them blind:
#   * Teach every data backfill to skip its guard when the table is empty. Keeps
#     the replay story literally true, but every future backfill has to remember
#     the pattern and forgetting it is SILENT — the guard just quietly stops
#     protecting anything. Ongoing discipline debt with nothing enforcing it.
#   * Split into replayable schema migrations and production-only data lanes.
#     Cleanest on paper, but it adds a judgment call to every future change, and
#     a wrong call puts a data move where it will be replayed against an empty
#     database — the exact failure being fixed.
#
# NO BUSINESS DATA, EVER. Three things go in and nothing else: the STRUCTURE
# (--schema-only), the APPLIED-MIGRATION LEDGER, and the REFERENCE VOCABULARY
# named in the explicit list below. No clients, no deals, no parties, no notes,
# no events. That is what makes this file safe to commit, and the vocabulary list
# is hand-checked rather than pattern-matched precisely so it can never widen
# into business data by accident. pipelines/staging-isolation-proof.sql asserts
# the business tables are empty after a load, so a mistake here is caught rather
# than discovered. --no-owner --no-acl for the same reason
# bin/backup-dump.sh uses them: an embedded OWNER TO / GRANT names roles a fresh
# database has never heard of, and the first such statement aborts the load.
#
# "Grants are rebuilt by the migrations, which are in git" — this file's
# original claim, and it has the same shelf life the role claim had: true only
# while the granting migrations are PENDING. Once the ledger absorbs them, the
# grants stop running anywhere, and a database built from this snapshot has the
# roles (preamble below) holding NOTHING. That is exactly how CI's migration
# class ran green while proving nothing about privileges — discovered 2026-08-14
# on PR #75, where verifying carr_writer's insert on lead meant replaying
# 0001-0004 by hand because the database CI builds could not answer the
# question. So the snapshot carries the app roles' ACLs itself: the CARR GRANTS
# section below reads them from production's catalogs and emits plain GRANTs,
# scoped to the app roles only so no other principal's ACLs enter the tree.
# tools/test-schema-snapshot-grants.py pins the shapes it must carry.
#
# THE ROLES THEMSELVES ARE NOT, and assuming they were is a bug this file shipped
# on 2026-08-14. The reasoning above was true only while every role-creating
# migration was still PENDING relative to this snapshot: 0115 declares
# carr_reader, carr_writer and carr_exporter (NOLOGIN privilege bundles), so while the
# ledger stopped at 0114 a fresh database got the roles by replaying it. The
# moment a refresh advanced the ledger PAST 0115, that migration stopped being
# pending, the role creation stopped running anywhere, and the next migration to
# grant against those roles died with `role "carr_jobs" does not exist` — which
# is exactly how CI failed on migration 0117.
#
# The snapshot is the base for every fresh database, so it has to carry the
# roles itself rather than inherit them from a migration it has already absorbed.
# The privilege bundles remain NOLOGIN. carr_jobs is different: it is the
# narrow unattended runtime identity, so the preamble creates it LOGIN with an
# unprinted random placeholder, or converts an older NOLOGIN placeholder. An
# already-login carr_jobs is left exactly as it is, including its password.
# Production is untouched because its existing carr_jobs is already LOGIN.
#
# Usage:
#   bin/schema-snapshot.sh            # regenerate db/schema.sql from production
#   bin/schema-snapshot.sh --check    # non-zero if the checked-in file is stale
#   bin/schema-snapshot.sh --from-disposable-local postgres://carr_ci@127.0.0.1:<port>/carr_ci
#       # generate only from the loopback disposable PG17 full-build target
#   bin/schema-snapshot.sh --from-disposable-local <dsn> --verify-only
#       # validate the disposable candidate without changing db/schema.sql
#   bin/schema-snapshot.sh --from-disposable-local <dsn> --output-candidate /absolute/new/file
#       # validate and write a disposable candidate without changing db/schema.sql
#
# The default mode needs Joe's production connection; the explicit disposable
# mode uses only the loopback CI database and cannot resolve a provider DSN.

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/db/schema.sql"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"

# PIN THE CLIENT MAJOR, because pg_dump's OUTPUT is version-dependent and this
# file is a tracked artifact a human reviews as a diff. pg_dump 18 emits every
# NOT NULL as a named table constraint; pg_dump 17 emits the same columns with a
# bare NOT NULL. Both dumps are correct and they describe the same database, but
# they differ by 256 lines, so whichever client a given machine happened to
# resolve first decided the diff. That is what happened on 2026-09-17: main's
# snapshot came from a machine whose libpq is 18 and the v28 branch's from one
# resolving Homebrew's postgresql@17, and 256 constraint lines churned with no
# schema change behind them (loop #614). Searching harder is not the fix — the
# SAME major has to win everywhere, or the artifact is not reproducible.
#
# Raising this is a deliberate change: bump PG_DUMP_MAJOR, regenerate, and land
# the resulting whole-file diff on its own, so the churn is reviewed once rather
# than arriving inside somebody else's branch.
PG_DUMP_MAJOR=18

pg_dump_major() {
  # "pg_dump (PostgreSQL) 18.4" / "pg_dump (PostgreSQL) 17.11 (Homebrew)" -> 18 / 17
  "$1" --version 2>/dev/null | sed -n 's/^pg_dump (PostgreSQL) \([0-9][0-9]*\).*/\1/p'
}

PG_DUMP=""
PG_DUMP_SEEN=""
for c in /opt/homebrew/opt/postgresql@$PG_DUMP_MAJOR/bin/pg_dump \
         /usr/local/opt/postgresql@$PG_DUMP_MAJOR/bin/pg_dump \
         /opt/homebrew/opt/libpq/bin/pg_dump /usr/local/opt/libpq/bin/pg_dump \
         /usr/lib/postgresql/$PG_DUMP_MAJOR/bin/pg_dump pg_dump; do
  command -v "$c" >/dev/null 2>&1 || continue
  m="$(pg_dump_major "$c")"
  [ -n "$m" ] || continue
  PG_DUMP_SEEN="$PG_DUMP_SEEN $c($m)"
  if [ "$m" = "$PG_DUMP_MAJOR" ]; then PG_DUMP="$c"; break; fi
done
if [ -z "$PG_DUMP" ]; then
  echo "schema-snapshot: no pg_dump $PG_DUMP_MAJOR found — the snapshot is pinned to that major" >&2
  echo "schema-snapshot: clients on PATH:${PG_DUMP_SEEN:- none}" >&2
  echo "schema-snapshot: install one (brew install postgresql@$PG_DUMP_MAJOR) rather than dumping with another major" >&2
  exit 69
fi

PSQL=""
for c in /opt/homebrew/opt/libpq/bin/psql /usr/local/opt/libpq/bin/psql psql; do
  if command -v "$c" >/dev/null 2>&1; then PSQL="$c"; break; fi
done
[ -n "$PSQL" ] || { echo "schema-snapshot: no psql found (needed for the grants section)" >&2; exit 69; }

# pg_dump's completion trailer has varied in its number of terminal blank
# records across client versions. Keep every interior blank line intact, but
# make the tracked snapshot's EOF a one-newline invariant.
EOF_NORMALIZER='
/^[[:space:]]*$/ { trailing = trailing $0 ORS; next }
{ printf "%s", trailing; trailing = ""; print }
'

normalise_eof() {
  awk "$EOF_NORMALIZER"
}
CHECK=0
VERIFY_ONLY=0
OUTPUT_CANDIDATE=""
URL=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --verify-only) VERIFY_ONLY=1 ;;
    --output-candidate)
      [ "$#" -ge 2 ] || { echo "schema-snapshot: --output-candidate needs an absolute, new path" >&2; exit 64; }
      OUTPUT_CANDIDATE="$2"; shift ;;
    --from-disposable-local)
      [ "$#" -ge 2 ] || { echo "schema-snapshot: --from-disposable-local needs a DSN" >&2; exit 64; }
      URL="$2"; shift ;;
    *) echo "schema-snapshot: unknown argument $1" >&2; exit 64 ;;
  esac
  shift
done

# A hosted disposable build needs the exact generated bytes for a second restore
# without changing the tracked source file. Keep that route deliberately more
# constrained than the ordinary writer: it only creates a NEW, absolute path
# outside the repository, so no caller can redirect a CI candidate through a
# relative path, an existing symlink, or db/schema.sql itself. The existing
# --verify-only remains the validation-only interface.
if [ -n "$OUTPUT_CANDIDATE" ]; then
  [ -n "$URL" ] \
    || { echo "schema-snapshot: --output-candidate requires --from-disposable-local" >&2; exit 64; }
  [ "$CHECK" = 0 ] && [ "$VERIFY_ONLY" = 0 ] \
    || { echo "schema-snapshot: --output-candidate cannot be combined with --check or --verify-only" >&2; exit 64; }
  case "$OUTPUT_CANDIDATE" in
    /*) ;;
    *) echo "schema-snapshot: --output-candidate must be an absolute path" >&2; exit 64 ;;
  esac
  CANDIDATE_PARENT="$(dirname "$OUTPUT_CANDIDATE")"
  [ -d "$CANDIDATE_PARENT" ] \
    || { echo "schema-snapshot: --output-candidate parent does not exist" >&2; exit 64; }
  CANDIDATE_PARENT="$(cd "$CANDIDATE_PARENT" && pwd -P)"
  OUTPUT_CANDIDATE="$CANDIDATE_PARENT/$(basename "$OUTPUT_CANDIDATE")"
  case "$OUTPUT_CANDIDATE" in
    "$REPO"/*) echo "schema-snapshot: --output-candidate must be outside the repository" >&2; exit 64 ;;
  esac
  [ ! -e "$OUTPUT_CANDIDATE" ] && [ ! -L "$OUTPUT_CANDIDATE" ] \
    || { echo "schema-snapshot: --output-candidate must name a new non-symlink file" >&2; exit 64; }
fi

if [ -n "$URL" ]; then
  printf '%s\n' "$URL" | grep -Eq '^postgres://carr_ci@127\.0\.0\.1:[0-9]{4,5}/carr_ci$' \
    || { echo "schema-snapshot: disposable source must be passwordless carr_ci on 127.0.0.1/carr_ci" >&2; exit 64; }
else
  [ -x "$NEONCTL" ] || { echo "schema-snapshot: neonctl not found at $NEONCTL" >&2; exit 69; }
  URL="$("$NEONCTL" connection-string production \
          --project-id steep-field-48688294 --role-name neondb_owner 2>/dev/null)"
  [ -n "$URL" ] || { echo "schema-snapshot: could not obtain the production connection string" >&2; exit 1; }
fi

# libpq service files keep credentials out of process arguments.  The URL is
# passed to the converter only on stdin, then unset before any database client
# starts.  The generated file has owner-only permissions and is removed on exit.
SERVICE_FILE="$(mktemp)"
PASSFILE="$(mktemp)"
trap 'rm -f "$SERVICE_FILE" "$PASSFILE"' 0
SNAPSHOT_PY="$REPO/.venv/bin/python"
[ -x "$SNAPSHOT_PY" ] || SNAPSHOT_PY=python3
if ! printf '%s' "$URL" | "$SNAPSHOT_PY" "$REPO/ops/schema-snapshot-connection.py" --write-service "$SERVICE_FILE" --write-passfile "$PASSFILE"; then
  rm -f "$SERVICE_FILE" "$PASSFILE"
  echo "schema-snapshot: could not prepare the private database connection" >&2
  exit 1
fi
unset URL
export PGSERVICE=schema_snapshot PGSERVICEFILE="$SERVICE_FILE" PGPASSFILE="$PASSFILE"

# Some bounded build seeds belong only to schema that has actually entered the
# source ledger.  A production-truth snapshot taken immediately before that
# migration must leave the seed pending with the migration; embedding it early
# makes the pending migration fail on its own primary key.
RENEWAL_SOURCE_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0230_renewal_decision_delivery.sql')" \
  2>/dev/null)"
case "$RENEWAL_SOURCE_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the renewal-delivery ledger state" >&2; exit 1 ;;
esac

RULE_DELIVERY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0291_rule_delivery_layers.sql')" \
  2>/dev/null)"
case "$RULE_DELIVERY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the rule-delivery ledger state" >&2; exit 1 ;;
esac

CANONICAL_OWNERSHIP_ACTIVATION_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0532a_canonical_ownership_lease_activation.sql')" \
  2>/dev/null)"
case "$CANONICAL_OWNERSHIP_ACTIVATION_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the canonical-ownership activation ledger state" >&2; exit 1 ;;
esac

DOT_READER_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0756_dot_reader.sql')" \
  2>/dev/null)"
case "$DOT_READER_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Dot login ledger state" >&2; exit 1 ;;
esac

RULE_DELIVERY_CUTOVER_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0317_atomic_rule_delivery_cutover.sql')" \
  2>/dev/null)"
case "$RULE_DELIVERY_CUTOVER_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the rule-delivery cutover ledger state" >&2; exit 1 ;;
esac

RULE_DELIVERY_REFRESH_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0332_refresh_rule_delivery_activation_preimage.sql')" \
  2>/dev/null)"
case "$RULE_DELIVERY_REFRESH_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the rule-delivery refresh ledger state" >&2; exit 1 ;;
esac

RULE_DELIVERY_RULESET_CONTROL_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0348_pr_only_main_ruleset_control.sql')" \
  2>/dev/null)"
case "$RULE_DELIVERY_RULESET_CONTROL_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the rule-delivery ruleset-control ledger state" >&2; exit 1 ;;
esac

RULE_DELIVERY_DIGEST_REPIN_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0363_rule_delivery_activation_digest_repin.sql')" \
  2>/dev/null)"
case "$RULE_DELIVERY_DIGEST_REPIN_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the rule-delivery digest-repin ledger state" >&2; exit 1 ;;
esac

SOURCE_MERGE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0471_source_merge_catalog_registry_successor.sql')" \
  2>/dev/null)"
case "$SOURCE_MERGE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the source-merge registry ledger state" >&2; exit 1 ;;
esac

CODEX_CONTINUITY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0481_codex_continuity_registry_activation.sql')" \
  2>/dev/null)"
case "$CODEX_CONTINUITY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Codex continuity registry ledger state" >&2; exit 1 ;;
esac

CLAUDE_CONTINUITY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0486_claude_continuity_registry_activation.sql')" \
  2>/dev/null)"
case "$CLAUDE_CONTINUITY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Claude continuity registry ledger state" >&2; exit 1 ;;
esac

CLAUDE_STARTUP_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0487_claude_startup_registry_activation.sql')" \
  2>/dev/null)"
case "$CLAUDE_STARTUP_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Claude startup registry ledger state" >&2; exit 1 ;;
esac

CLAUDE_ACTOR_HYDRATION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0488_claude_actor_hydration_registry_activation.sql')" \
  2>/dev/null)"
case "$CLAUDE_ACTOR_HYDRATION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Claude actor hydration registry ledger state" >&2; exit 1 ;;
esac

CLAUDE_CONFIG_PRESERVATION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0489_claude_config_preservation_registry_activation.sql')" \
  2>/dev/null)"
case "$CLAUDE_CONFIG_PRESERVATION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Claude config-preservation registry ledger state" >&2; exit 1 ;;
esac

CODEX_COMPACTION_CHECKPOINT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0490_codex_compaction_checkpoint_registry_activation.sql')" \
  2>/dev/null)"
case "$CODEX_COMPACTION_CHECKPOINT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Codex compaction-checkpoint registry ledger state" >&2; exit 1 ;;
esac

BACKUP_GUARD_STATUS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0491_backup_guard_status_registry_activation.sql')" \
  2>/dev/null)"
case "$BACKUP_GUARD_STATUS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the backup guard/status registry ledger state" >&2; exit 1 ;;
esac

SOURCED_SHAPE_FORWARD_CORRECTION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0492_sourced_shape_forward_correction_and_scac_successor.sql')" \
  2>/dev/null)"
case "$SOURCED_SHAPE_FORWARD_CORRECTION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the sourced shape forward-correction registry ledger state" >&2; exit 1 ;;
esac

INCIDENT_WORK_REQUEST_LINK_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0493_incident_work_request_link_scac_successor.sql')" \
  2>/dev/null)"
case "$INCIDENT_WORK_REQUEST_LINK_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the incident/work-request registry ledger state" >&2; exit 1 ;;
esac

CONTINUITY_ARCHIVE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0494_codex_continuity_archive_registry.sql')" \
  2>/dev/null)"
case "$CONTINUITY_ARCHIVE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the continuity-archive registry ledger state" >&2; exit 1 ;;
esac

R06_HOOKS_CORRECTNESS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0495_r06_hooks_correctness_scac_successor.sql')" \
  2>/dev/null)"
case "$R06_HOOKS_CORRECTNESS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the R06 hooks-correctness registry ledger state" >&2; exit 1 ;;
esac

DOCTORCRE_PORTFOLIO_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0496_doctorcre_portfolio_hierarchy_and_scac_successor.sql')" \
  2>/dev/null)"
case "$DOCTORCRE_PORTFOLIO_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the DoctorCRE portfolio registry ledger state" >&2; exit 1 ;;
esac

R07_REPO_HYGIENE_JANITOR_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0497_r07_repo_hygiene_janitor_and_scac_successor.sql')" \
  2>/dev/null)"
case "$R07_REPO_HYGIENE_JANITOR_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the R07 repo-hygiene janitor registry ledger state" >&2; exit 1 ;;
esac

V5_F09_WORKFLOW_TRUTH_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0498_f09_workflow_truth_and_scac_successor.sql')" \
  2>/dev/null)"
case "$V5_F09_WORKFLOW_TRUTH_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the V5-F09 workflow truth registry ledger state" >&2; exit 1 ;;
esac

V5_SCHEDULED_JOB_ADMISSION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0501_scheduled_job_admission_and_scac_successor.sql')" \
  2>/dev/null)"
case "$V5_SCHEDULED_JOB_ADMISSION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the V5 scheduled job admission registry ledger state" >&2; exit 1 ;;
esac

GATE_ZERO_OUTCOME_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0503_gate_zero_outcome_and_scac_successor.sql')" \
  2>/dev/null)"
case "$GATE_ZERO_OUTCOME_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Gate Zero outcome registry ledger state" >&2; exit 1 ;;
esac

FOUNDATION_ASSURANCE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0512_foundation_assurance_scac_successor.sql')" \
  2>/dev/null)"
case "$FOUNDATION_ASSURANCE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the foundation-assurance registry ledger state" >&2; exit 1 ;;
esac

DEAL_FIELD_PROVENANCE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0516_deal_field_change_provenance_and_scac_successor.sql')" \
  2>/dev/null)"
case "$DEAL_FIELD_PROVENANCE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the deal-field provenance registry ledger state" >&2; exit 1 ;;
esac

# WR-000110. 0518 is the registry successor half of the atomic (0517,0518)
# group, so probing the SUCCESSOR and not the domain migration is what says
# the v29 registry surface exists. A snapshot taken between the two would be
# taken inside a transaction that has not committed, which cannot happen.
PROGRAM_CONTROLLER_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0518_program_controller_seams_scac_successor.sql')" \
  2>/dev/null)"
case "$PROGRAM_CONTROLLER_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the program controller registry ledger state" >&2; exit 1 ;;
esac

# WR-000111/112/113. 0522 is the registry successor half of the atomic
# (0519,0520,0521,0522) group, so probing the SUCCESSOR and not any of the three
# domain migrations is what says the v30 registry surface exists. A snapshot
# taken between them would be taken inside a transaction that has not
# committed, which cannot happen.
PRODUCER_TRIO_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0522_producer_trio_scac_successor.sql')" \
  2>/dev/null)"
case "$PRODUCER_TRIO_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the producer trio registry ledger state" >&2; exit 1 ;;
esac

# WR-000114. 0524 is the registry successor half of the atomic (0523,0524)
# group, so probing the SUCCESSOR and not the domain migration is what says the
# v31 registry surface exists. A snapshot taken between the two would be taken
# inside a transaction that has not committed, which cannot happen.
DOC_CONVERSATION_WRITE_DOORS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0524_doc_conversation_write_doors_scac_successor.sql')" \
  2>/dev/null)"
case "$DOC_CONVERSATION_WRITE_DOORS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Doc conversation write-door registry ledger state" >&2; exit 1 ;;
esac

# WR-000115. 0526 is the registry successor half of the atomic (0525,0526)
# group, so probing the SUCCESSOR and not the domain migration is what says the
# v32 registry surface exists. A snapshot taken between the two would be taken
# inside a transaction that has not committed, which cannot happen.
DOC_CONVERSATION_LIST_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0526_doc_conversation_list_scac_successor.sql')" \
  2>/dev/null)"
case "$DOC_CONVERSATION_LIST_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Doc conversation list registry ledger state" >&2; exit 1 ;;
esac

# WR-000116. 0528 is the registry successor half of the atomic (0527,0528)
# group, so probing the SUCCESSOR and not the domain migration is what says the
# v33 registry surface exists. A snapshot taken between the two would be taken
# inside a transaction that has not committed, which cannot happen.
NOTIFICATION_PREFERENCES_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0528_notification_preferences_scac_successor.sql')" \
  2>/dev/null)"
case "$NOTIFICATION_PREFERENCES_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the notification preference registry ledger state" >&2; exit 1 ;;
esac

# WR-000119. 0532 is the registry successor half of the atomic (0531,0532)
# group, so probing the SUCCESSOR and not the domain migration is what says the
# v35 registry surface exists. A snapshot taken between the two would be taken
# inside a transaction that has not committed, which cannot happen.
DISPATCH_SPINE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0532_room_dispatch_spine_scac_successor.sql')" \
  2>/dev/null)"
case "$DISPATCH_SPINE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the dispatch spine registry ledger state" >&2; exit 1 ;;
esac

# WR-000125. 0532a and 0532b are one atomic suffix after the already-applied
# v35 predecessor. A snapshot may expose v36 only when the registry successor
# itself is present; the paired domain migration is checked as a second
# readback so a partial suffix can never masquerade as a current snapshot.
WR125_DOMAIN_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0532a_canonical_ownership_lease_activation.sql')" \
  2>/dev/null)"
case "$WR125_DOMAIN_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the WR125 ownership ledger state" >&2; exit 1 ;;
esac
WR125_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0532b_ready_plan_amendment_scac_successor.sql')" \
  2>/dev/null)"
case "$WR125_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the WR125 SCAC ledger state" >&2; exit 1 ;;
esac

# WR-000130. 0539 advances the registry after 0538 assurance binding.
WR130_ASSURANCE_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0538_canonical_ownership_assurance_binding.sql')" \
  2>/dev/null)"
case "$WR130_ASSURANCE_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the WR130 assurance ledger state" >&2; exit 1 ;;
esac
WR130_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0539_canonical_ownership_assurance_scac_successor.sql')" \
  2>/dev/null)"
case "$WR130_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the WR130 SCAC ledger state" >&2; exit 1 ;;
esac
WR132_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0541_release_readiness_scac_successor.sql')" \
  2>/dev/null)"
case "$WR132_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the WR132 SCAC ledger state" >&2; exit 1 ;;
esac
MODEL_ROLE_STORE_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0542_model_role_store.sql')" \
  2>/dev/null)"
case "$MODEL_ROLE_STORE_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Model Room role-store ledger state" >&2; exit 1 ;;
esac
MODEL_ROLE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0543_model_role_store_scac_successor.sql')" \
  2>/dev/null)"
case "$MODEL_ROLE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Model Room SCAC ledger state" >&2; exit 1 ;;
esac

WR130_HOTFIX_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0545_release_readiness_hotfix_scac_successor.sql')" \
  2>/dev/null)"
case "$WR130_HOTFIX_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the WR130 hotfix SCAC ledger state" >&2; exit 1 ;;
esac
B09_OUTCOME_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0547_read_doc_outcome_cards_scac_successor.sql')" \
  2>/dev/null)"
case "$B09_OUTCOME_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the B09 SCAC ledger state" >&2; exit 1 ;;
esac
if [ "$B09_OUTCOME_REGISTRY_APPLIED" = t ] && [ "$WR130_HOTFIX_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: B09 v41 is applied without v40 predecessor" >&2
  exit 1
fi
B09_ATOMIC_REPAIR_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0548_b09_atomic_migration_repair_scac_successor.sql')" \
  2>/dev/null)"
case "$B09_ATOMIC_REPAIR_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the B09 v42 registry ledger state" >&2; exit 1 ;;
esac
if [ "$B09_ATOMIC_REPAIR_REGISTRY_APPLIED" = t ] && [ "$B09_OUTCOME_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: B09 v42 is applied without v41 predecessor" >&2
  exit 1
fi
JEV_PROCESS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0549_jev_process_scac_successor.sql')" \
  2>/dev/null)"
case "$JEV_PROCESS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Jev v43 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JEV_PROCESS_REGISTRY_APPLIED" = t ] && [ "$B09_ATOMIC_REPAIR_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Jev v43 is applied without v42 predecessor" >&2
  exit 1
fi
JEV_HOOK_ACTIVATION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0550_jev_hook_activation_scac_successor.sql')" \
  2>/dev/null)"
case "$JEV_HOOK_ACTIVATION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Jev hook v44 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JEV_HOOK_ACTIVATION_REGISTRY_APPLIED" = t ] && [ "$JEV_PROCESS_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Jev hook v44 is applied without v43 predecessor" >&2
  exit 1
fi
DEAL_CONFLICT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0551_deal_conflict_scac_successor.sql')" \
  2>/dev/null)"
case "$DEAL_CONFLICT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Deal Room v45 registry ledger state" >&2; exit 1 ;;
esac
if [ "$DEAL_CONFLICT_REGISTRY_APPLIED" = t ] && [ "$JEV_HOOK_ACTIVATION_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Deal Room v45 is applied without v44 predecessor" >&2
  exit 1
fi
DEAL_UNDO_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0552_deal_undo_scac_successor.sql')" \
  2>/dev/null)"
case "$DEAL_UNDO_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Deal Room undo v46 registry ledger state" >&2; exit 1 ;;
esac
if [ "$DEAL_UNDO_REGISTRY_APPLIED" = t ] && [ "$DEAL_CONFLICT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Deal Room undo v46 is applied without v45 predecessor" >&2
  exit 1
fi
HERMES_DISPATCH_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0553_hermes_dispatch_link_scac_successor.sql')" \
  2>/dev/null)"
case "$HERMES_DISPATCH_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the Hermes dispatch v47 registry ledger state" >&2; exit 1 ;;
esac
if [ "$HERMES_DISPATCH_REGISTRY_APPLIED" = t ] && [ "$DEAL_UNDO_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Hermes dispatch v47 is applied without v46 predecessor" >&2
  exit 1
fi
RULE_DELIVERY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0555_rule_delivery_scac_successor.sql')" \
  2>/dev/null)"
case "$RULE_DELIVERY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read rule delivery v48 registry ledger state" >&2; exit 1 ;;
esac
if [ "$RULE_DELIVERY_REGISTRY_APPLIED" = t ] && [ "$HERMES_DISPATCH_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: rule delivery v48 is applied without v47 predecessor" >&2
  exit 1
fi
MEETING_MODE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0557_meeting_mode_scac_successor.sql')" \
  2>/dev/null)"
case "$MEETING_MODE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read meeting mode v49 registry ledger state" >&2; exit 1 ;;
esac
if [ "$MEETING_MODE_REGISTRY_APPLIED" = t ] && [ "$RULE_DELIVERY_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: meeting mode v49 is applied without v48 predecessor" >&2
  exit 1
fi
MACHINE_ROLE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0558_machine_role_headroom_scac_successor.sql')" \
  2>/dev/null)"
case "$MACHINE_ROLE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read machine role v50 registry ledger state" >&2; exit 1 ;;
esac
if [ "$MACHINE_ROLE_REGISTRY_APPLIED" = t ] && [ "$MEETING_MODE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: machine role v50 is applied without v49 predecessor" >&2
  exit 1
fi
SNAPSHOT_DEDUP_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0559_schema_snapshot_dedup_scac_successor.sql')" \
  2>/dev/null)"
case "$SNAPSHOT_DEDUP_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read snapshot dedup v51 registry ledger state" >&2; exit 1 ;;
esac
if [ "$SNAPSHOT_DEDUP_REGISTRY_APPLIED" = t ] && [ "$MACHINE_ROLE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: snapshot dedup v51 is applied without v50 predecessor" >&2
  exit 1
fi
JEV_HANDOFF_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0560_jev_handoff_scac_successor.sql')" \
  2>/dev/null)"
case "$JEV_HANDOFF_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read jev handoff v52 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JEV_HANDOFF_REGISTRY_APPLIED" = t ] && [ "$SNAPSHOT_DEDUP_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: jev handoff v52 is applied without v51 predecessor" >&2
  exit 1
fi
OPENSSL_RESOLUTION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0561_openssl_resolution_scac_successor.sql')" \
  2>/dev/null)"
case "$OPENSSL_RESOLUTION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read openssl resolution v53 registry ledger state" >&2; exit 1 ;;
esac
if [ "$OPENSSL_RESOLUTION_REGISTRY_APPLIED" = t ] && [ "$JEV_HANDOFF_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: openssl resolution v53 is applied without v52 predecessor" >&2
  exit 1
fi
MACHINE_PATHS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0562_machine_paths_scac_successor.sql')" \
  2>/dev/null)"
case "$MACHINE_PATHS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read machine paths v54 registry ledger state" >&2; exit 1 ;;
esac
if [ "$MACHINE_PATHS_REGISTRY_APPLIED" = t ] && [ "$OPENSSL_RESOLUTION_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: machine paths v54 is applied without v53 predecessor" >&2
  exit 1
fi
LOGITECH_KEYMAP_OPTOUT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0563_logitech_keymap_optout_scac_successor.sql')" \
  2>/dev/null)"
case "$LOGITECH_KEYMAP_OPTOUT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read logitech keymap opt-out v55 registry ledger state" >&2; exit 1 ;;
esac
if [ "$LOGITECH_KEYMAP_OPTOUT_REGISTRY_APPLIED" = t ] && [ "$MACHINE_PATHS_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: logitech keymap opt-out v55 is applied without v54 predecessor" >&2
  exit 1
fi
CI_SPLIT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0564_ci_split_scac_successor.sql')" \
  2>/dev/null)"
case "$CI_SPLIT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read ci split v56 registry ledger state" >&2; exit 1 ;;
esac
if [ "$CI_SPLIT_REGISTRY_APPLIED" = t ] && [ "$LOGITECH_KEYMAP_OPTOUT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: ci split v56 is applied without v55 predecessor" >&2
  exit 1
fi
TOUR_PROPERTY_REGISTRATION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0566_tour_property_registration_scac_successor.sql')" \
  2>/dev/null)"
case "$TOUR_PROPERTY_REGISTRATION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read tour property registration v57 registry ledger state" >&2; exit 1 ;;
esac
if [ "$TOUR_PROPERTY_REGISTRATION_REGISTRY_APPLIED" = t ] && [ "$CI_SPLIT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: tour property registration v57 is applied without v56 predecessor" >&2
  exit 1
fi
TOUR_REGISTRATION_ATOMIC_PAIR_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0567_tour_registration_atomic_pair_scac_successor.sql')" \
  2>/dev/null)"
case "$TOUR_REGISTRATION_ATOMIC_PAIR_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read tour registration atomic pair v58 registry ledger state" >&2; exit 1 ;;
esac
if [ "$TOUR_REGISTRATION_ATOMIC_PAIR_REGISTRY_APPLIED" = t ] && [ "$TOUR_PROPERTY_REGISTRATION_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: tour registration atomic pair v58 is applied without v57 predecessor" >&2
  exit 1
fi
JEV_COMBINED_V59_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0568_jev_combined_scac_successor.sql')" \
  2>/dev/null)"
case "$JEV_COMBINED_V59_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read jev combined v59 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JEV_COMBINED_V59_REGISTRY_APPLIED" = t ] && [ "$TOUR_REGISTRATION_ATOMIC_PAIR_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: jev combined v59 is applied without v58 predecessor" >&2
  exit 1
fi
CALL_MODE_ORIGIN_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0569_call_mode_origin_scac_successor.sql')" \
  2>/dev/null)"
case "$CALL_MODE_ORIGIN_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read call mode origin v60 registry ledger state" >&2; exit 1 ;;
esac
if [ "$CALL_MODE_ORIGIN_REGISTRY_APPLIED" = t ] && [ "$JEV_COMBINED_V59_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: call mode origin v60 is applied without v59 predecessor" >&2
  exit 1
fi
QUILL_SPEAKER_LABELS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0570_quill_speaker_labels_scac_successor.sql')" \
  2>/dev/null)"
case "$QUILL_SPEAKER_LABELS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read quill speaker labels v61 registry ledger state" >&2; exit 1 ;;
esac
if [ "$QUILL_SPEAKER_LABELS_REGISTRY_APPLIED" = t ] && [ "$CALL_MODE_ORIGIN_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: quill speaker labels v61 is applied without v60 predecessor" >&2
  exit 1
fi
REQUIRED_ARGS_NULLABLE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0572_required_args_nullable_scac_successor.sql')" \
  2>/dev/null)"
case "$REQUIRED_ARGS_NULLABLE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read required args nullable v62 registry ledger state" >&2; exit 1 ;;
esac
if [ "$REQUIRED_ARGS_NULLABLE_REGISTRY_APPLIED" = t ] && [ "$QUILL_SPEAKER_LABELS_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: required args nullable v62 is applied without v61 predecessor" >&2
  exit 1
fi
ANSWER_JOE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0576_answer_needs_joe_work_request_scac_successor.sql')" \
  2>/dev/null)"
case "$ANSWER_JOE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read answering Joe v63 registry ledger state" >&2; exit 1 ;;
esac
if [ "$ANSWER_JOE_REGISTRY_APPLIED" = t ] && [ "$REQUIRED_ARGS_NULLABLE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: answering Joe v63 is applied without v62 predecessor" >&2
  exit 1
fi
ANSWER_JOE_ATOMIC_PAIR_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0578_answer_joe_atomic_pair_scac_successor.sql')" \
  2>/dev/null)"
case "$ANSWER_JOE_ATOMIC_PAIR_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read answering Joe atomic pair v64 registry ledger state" >&2; exit 1 ;;
esac
if [ "$ANSWER_JOE_ATOMIC_PAIR_REGISTRY_APPLIED" = t ] && [ "$ANSWER_JOE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: answering Joe atomic pair v64 is applied without v63 predecessor" >&2
  exit 1
fi
RESOURCE_OBSERVATION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0581_resource_observation_scac_successor.sql')" \
  2>/dev/null)"
case "$RESOURCE_OBSERVATION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read resource observation v65 registry ledger state" >&2; exit 1 ;;
esac
if [ "$RESOURCE_OBSERVATION_REGISTRY_APPLIED" = t ] && [ "$ANSWER_JOE_ATOMIC_PAIR_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: resource observation v65 is applied without v64 predecessor" >&2
  exit 1
fi
RESOURCE_OBSERVATION_MIGRATE_PY_RESEAL_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0582_resource_observation_migrate_py_reseal.sql')" \
  2>/dev/null)"
case "$RESOURCE_OBSERVATION_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read resource observation migrate.py reseal v66 registry ledger state" >&2; exit 1 ;;
esac
if [ "$RESOURCE_OBSERVATION_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" = t ] && [ "$RESOURCE_OBSERVATION_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: resource observation migrate.py reseal v66 is applied without v65 predecessor" >&2
  exit 1
fi
TIMEBOMB_AUDIT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0584_timebomb_audit_scac_successor.sql')" \
  2>/dev/null)"
case "$TIMEBOMB_AUDIT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read timebomb audit v67 registry ledger state" >&2; exit 1 ;;
esac
if [ "$TIMEBOMB_AUDIT_REGISTRY_APPLIED" = t ] && [ "$RESOURCE_OBSERVATION_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: timebomb audit v67 is applied without v66 predecessor" >&2
  exit 1
fi
RELEASE_PIPELINE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0585_release_pipeline_scac_successor.sql')" \
  2>/dev/null)"
case "$RELEASE_PIPELINE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read release pipeline v68 registry ledger state" >&2; exit 1 ;;
esac
if [ "$RELEASE_PIPELINE_REGISTRY_APPLIED" = t ] && [ "$TIMEBOMB_AUDIT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: release pipeline v68 is applied without v67 predecessor" >&2
  exit 1
fi
JEV_CALL_RECEIPT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0588_jev_call_receipt_scac_successor.sql')" \
  2>/dev/null)"
case "$JEV_CALL_RECEIPT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read jev call receipt v69 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JEV_CALL_RECEIPT_REGISTRY_APPLIED" = t ] && [ "$RELEASE_PIPELINE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: jev call receipt v69 is applied without v68 predecessor" >&2
  exit 1
fi
JEV_CALL_RECEIPT_MIGRATE_PY_RESEAL_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0589_jev_call_receipt_migrate_py_reseal.sql')" \
  2>/dev/null)"
case "$JEV_CALL_RECEIPT_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read jev call receipt migrate.py reseal v70 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JEV_CALL_RECEIPT_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" = t ] && [ "$JEV_CALL_RECEIPT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: jev call receipt migrate.py reseal v70 is applied without v69 predecessor" >&2
  exit 1
fi
NIGHTLY_EXPORTS_DAYTIME_RETRY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0600_nightly_exports_daytime_retry_scac_successor.sql')" \
  2>/dev/null)"
case "$NIGHTLY_EXPORTS_DAYTIME_RETRY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read nightly exports daytime retry v71 registry ledger state" >&2; exit 1 ;;
esac
if [ "$NIGHTLY_EXPORTS_DAYTIME_RETRY_REGISTRY_APPLIED" = t ] && [ "$JEV_CALL_RECEIPT_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: nightly exports daytime retry v71 is applied without v70 predecessor" >&2
  exit 1
fi
V5_R02_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0603_doctorcre_r02_scac_successor.sql')" \
  2>/dev/null)"
case "$V5_R02_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read V5-R02 v72 registry ledger state" >&2; exit 1 ;;
esac
if [ "$V5_R02_REGISTRY_APPLIED" = t ] && [ "$NIGHTLY_EXPORTS_DAYTIME_RETRY_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: V5-R02 v72 is applied without v71 predecessor" >&2
  exit 1
fi
DEPLOY_WORKER_DO_MIGRATION_RESEAL_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0609_deploy_worker_do_migration_reseal.sql')" \
  2>/dev/null)"
case "$DEPLOY_WORKER_DO_MIGRATION_RESEAL_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read deploy-worker DO migration reseal v73 registry ledger state" >&2; exit 1 ;;
esac
if [ "$DEPLOY_WORKER_DO_MIGRATION_RESEAL_REGISTRY_APPLIED" = t ] && [ "$V5_R02_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: deploy-worker DO migration reseal v73 is applied without v72 predecessor" >&2
  exit 1
fi
JOURNEY_ONE_CLOCK_DOOR_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0614_journey_one_clock_door_scac_successor.sql')" \
  2>/dev/null)"
case "$JOURNEY_ONE_CLOCK_DOOR_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read journey-one clock door v74 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JOURNEY_ONE_CLOCK_DOOR_REGISTRY_APPLIED" = t ] && [ "$DEPLOY_WORKER_DO_MIGRATION_RESEAL_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: journey-one clock door v74 is applied without v73 predecessor" >&2
  exit 1
fi
DELIVERY_CADENCE_A05_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0618_delivery_cadence_a05_scac_successor.sql')" \
  2>/dev/null)"
case "$DELIVERY_CADENCE_A05_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read delivery cadence A05 v75 registry ledger state" >&2; exit 1 ;;
esac
if [ "$DELIVERY_CADENCE_A05_REGISTRY_APPLIED" = t ] && [ "$JOURNEY_ONE_CLOCK_DOOR_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: delivery cadence A05 v75 is applied without v74 predecessor" >&2
  exit 1
fi
GLOBAL_BOUNDARIES_DOOR_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0625_global_boundaries_door_scac_successor.sql')" \
  2>/dev/null)"
case "$GLOBAL_BOUNDARIES_DOOR_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read global boundaries door v76 registry ledger state" >&2; exit 1 ;;
esac
if [ "$GLOBAL_BOUNDARIES_DOOR_REGISTRY_APPLIED" = t ] && [ "$DELIVERY_CADENCE_A05_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: global boundaries door v76 is applied without v75 predecessor" >&2
  exit 1
fi
F01_RECORD_SOURCE_AUTHORITY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0627_f01_record_source_authority_scac_successor.sql')" \
  2>/dev/null)"
case "$F01_RECORD_SOURCE_AUTHORITY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read F01 record source authority v77 registry ledger state" >&2; exit 1 ;;
esac
if [ "$F01_RECORD_SOURCE_AUTHORITY_REGISTRY_APPLIED" = t ] && [ "$GLOBAL_BOUNDARIES_DOOR_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: F01 record source authority v77 is applied without v76 predecessor" >&2
  exit 1
fi
V5_SLICE_DONE_MARKER_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0629_doctorcre_slice_done_marker_scac_successor.sql')" \
  2>/dev/null)"
case "$V5_SLICE_DONE_MARKER_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read V5 slice done marker v78 registry ledger state" >&2; exit 1 ;;
esac
if [ "$V5_SLICE_DONE_MARKER_REGISTRY_APPLIED" = t ] && [ "$F01_RECORD_SOURCE_AUTHORITY_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: V5 slice done marker v78 is applied without v77 predecessor" >&2
  exit 1
fi
GOVERNED_CORRESPONDENCE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0701_governed_correspondence_scac_successor.sql')" \
  2>/dev/null)"
case "$GOVERNED_CORRESPONDENCE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read governed correspondence v79 registry ledger state" >&2; exit 1 ;;
esac
if [ "$GOVERNED_CORRESPONDENCE_REGISTRY_APPLIED" = t ] && [ "$V5_SLICE_DONE_MARKER_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: governed correspondence v79 is applied without v78 predecessor" >&2
  exit 1
fi
CRE_LIFECYCLE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0705_cre_lifecycle_scac_successor.sql')" \
  2>/dev/null)"
case "$CRE_LIFECYCLE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read J102 CRE lifecycle v80 registry ledger state" >&2; exit 1 ;;
esac
if [ "$CRE_LIFECYCLE_REGISTRY_APPLIED" = t ] && [ "$GOVERNED_CORRESPONDENCE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: J102 CRE lifecycle v80 is applied without v79 predecessor" >&2
  exit 1
fi
AMEND_CLOSED_LOOP_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0707_amend_closed_loop_scac_successor.sql')" \
  2>/dev/null)"
case "$AMEND_CLOSED_LOOP_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read amend-closed-loop v81 registry ledger state" >&2; exit 1 ;;
esac
if [ "$AMEND_CLOSED_LOOP_REGISTRY_APPLIED" = t ] && [ "$CRE_LIFECYCLE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: amend-closed-loop v81 is applied without v80 predecessor" >&2
  exit 1
fi
ACTION_CLASS_SUCCESSOR_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0709_action_class_successor_registry_scac_successor.sql')" \
  2>/dev/null)"
case "$ACTION_CLASS_SUCCESSOR_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read V5-D01 action-class-successor v82 registry ledger state" >&2; exit 1 ;;
esac
if [ "$ACTION_CLASS_SUCCESSOR_REGISTRY_APPLIED" = t ] && [ "$AMEND_CLOSED_LOOP_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: V5-D01 action-class-successor v82 is applied without v81 predecessor" >&2
  exit 1
fi
ASSURANCE_HEALTH_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0718_assurance_health_evidence_store_scac_successor.sql')" \
  2>/dev/null)"
case "$ASSURANCE_HEALTH_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read V5-A01 assurance-health v83 registry ledger state" >&2; exit 1 ;;
esac
if [ "$ASSURANCE_HEALTH_REGISTRY_APPLIED" = t ] && [ "$ACTION_CLASS_SUCCESSOR_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: V5-A01 assurance-health v83 is applied without v82 predecessor" >&2
  exit 1
fi
V5_A03_REVIEW_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0720_doctorcre_a03_review_scac_successor.sql')" \
  2>/dev/null)"
case "$V5_A03_REVIEW_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read V5-A03 review v84 registry ledger state" >&2; exit 1 ;;
esac
if [ "$V5_A03_REVIEW_REGISTRY_APPLIED" = t ] && [ "$ASSURANCE_HEALTH_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: V5-A03 review v84 is applied without v83 predecessor" >&2
  exit 1
fi
V5_A02_RULE_ENFORCEMENT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0722_a02_rule_enforcement_coverage_scac_successor.sql')" \
  2>/dev/null)"
case "$V5_A02_RULE_ENFORCEMENT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read V5-A02 rule enforcement v85 registry ledger state" >&2; exit 1 ;;
esac
if [ "$V5_A02_RULE_ENFORCEMENT_REGISTRY_APPLIED" = t ] && [ "$V5_A03_REVIEW_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: V5-A02 rule enforcement v85 is applied without v84 predecessor" >&2
  exit 1
fi
SESSION_TRACE_ARCHIVE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0723_session_trace_archive_scac_successor.sql')" \
  2>/dev/null)"
case "$SESSION_TRACE_ARCHIVE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read session trace archive v86 registry ledger state" >&2; exit 1 ;;
esac
if [ "$SESSION_TRACE_ARCHIVE_REGISTRY_APPLIED" = t ] && [ "$V5_A02_RULE_ENFORCEMENT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: session trace archive v86 is applied without v85 predecessor" >&2
  exit 1
fi
F05_RULE_CONTEXT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0725_f05_live_rule_context_scac_successor.sql')" \
  2>/dev/null)"
case "$F05_RULE_CONTEXT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read V5-F05 rule-context v87 registry ledger state" >&2; exit 1 ;;
esac
if [ "$F05_RULE_CONTEXT_REGISTRY_APPLIED" = t ] && [ "$SESSION_TRACE_ARCHIVE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: V5-F05 rule-context v87 is applied without v86 predecessor" >&2
  exit 1
fi
SALESFORCE_RECONCILIATION_RW02_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0727_salesforce_reconciliation_rw02_scac_successor.sql')" \
  2>/dev/null)"
case "$SALESFORCE_RECONCILIATION_RW02_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Salesforce reconciliation RW02 v88 registry ledger state" >&2; exit 1 ;;
esac
if [ "$SALESFORCE_RECONCILIATION_RW02_REGISTRY_APPLIED" = t ] && [ "$F05_RULE_CONTEXT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Salesforce reconciliation RW02 v88 is applied without v87 predecessor" >&2
  exit 1
fi
PROVISION_ENGINEERING_CONTROLLER_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0730_provision_engineering_controller_scac_successor.sql')" \
  2>/dev/null)"
case "$PROVISION_ENGINEERING_CONTROLLER_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read provision engineering controller v89 registry ledger state" >&2; exit 1 ;;
esac
if [ "$PROVISION_ENGINEERING_CONTROLLER_REGISTRY_APPLIED" = t ] && [ "$SALESFORCE_RECONCILIATION_RW02_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: provision engineering controller v89 is applied without v88 predecessor" >&2
  exit 1
fi
RULE_BOOT_STANDING_CONTEXT_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0731_rule_boot_standing_context_scac_successor.sql')" \
  2>/dev/null)"
case "$RULE_BOOT_STANDING_CONTEXT_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read rule-boot standing-context v90 registry ledger state" >&2; exit 1 ;;
esac
if [ "$RULE_BOOT_STANDING_CONTEXT_REGISTRY_APPLIED" = t ] && [ "$PROVISION_ENGINEERING_CONTROLLER_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: rule-boot standing-context v90 is applied without v89 predecessor" >&2
  exit 1
fi
SALESFORCE_RW02_SAFE_STOP_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0734_salesforce_rw02_safe_stop_scac_successor.sql')" \
  2>/dev/null)"
case "$SALESFORCE_RW02_SAFE_STOP_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Salesforce RW02 safe stop v91 registry ledger state" >&2; exit 1 ;;
esac
if [ "$SALESFORCE_RW02_SAFE_STOP_REGISTRY_APPLIED" = t ] && [ "$RULE_BOOT_STANDING_CONTEXT_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Salesforce RW02 safe stop v91 is applied without v90 predecessor" >&2
  exit 1
fi
INCIDENT_TRIAGE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0737_incident_triage_scac_successor.sql')" \
  2>/dev/null)"
case "$INCIDENT_TRIAGE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read incident triage v92 registry ledger state" >&2; exit 1 ;;
esac
if [ "$INCIDENT_TRIAGE_REGISTRY_APPLIED" = t ] && [ "$SALESFORCE_RW02_SAFE_STOP_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: incident triage v92 is applied without v91 predecessor" >&2
  exit 1
fi
INDUSTRY_EVENTS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0739_industry_events_scac_successor.sql')" \
  2>/dev/null)"
case "$INDUSTRY_EVENTS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read industry events v93 registry ledger state" >&2; exit 1 ;;
esac
if [ "$INDUSTRY_EVENTS_REGISTRY_APPLIED" = t ] && [ "$INCIDENT_TRIAGE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: industry events v93 is applied without v92 predecessor" >&2
  exit 1
fi
BOARD_ANSWERS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0741_board_answers_scac_successor.sql')" \
  2>/dev/null)"
case "$BOARD_ANSWERS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read board answers v94 registry ledger state" >&2; exit 1 ;;
esac
if [ "$BOARD_ANSWERS_REGISTRY_APPLIED" = t ] && [ "$INDUSTRY_EVENTS_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: board answers v94 is applied without v93 predecessor" >&2
  exit 1
fi
SCHEDULE_BOARD_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0743_schedule_board_scac_successor.sql')" \
  2>/dev/null)"
case "$SCHEDULE_BOARD_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read schedule board v95 registry ledger state" >&2; exit 1 ;;
esac
if [ "$SCHEDULE_BOARD_REGISTRY_APPLIED" = t ] && [ "$BOARD_ANSWERS_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: schedule board v95 is applied without v94 predecessor" >&2
  exit 1
fi
DOC_SUGGESTIONS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0745_doc_suggestions_scac_successor.sql')" \
  2>/dev/null)"
case "$DOC_SUGGESTIONS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Doc suggestions v96 registry ledger state" >&2; exit 1 ;;
esac
if [ "$DOC_SUGGESTIONS_REGISTRY_APPLIED" = t ] && [ "$SCHEDULE_BOARD_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Doc suggestions v96 is applied without v95 predecessor" >&2
  exit 1
fi
CODEX_SESSION_READ_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0748_codex_session_read_scac_successor.sql')" \
  2>/dev/null)"
case "$CODEX_SESSION_READ_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Codex session read v97 registry ledger state" >&2; exit 1 ;;
esac
if [ "$CODEX_SESSION_READ_REGISTRY_APPLIED" = t ] && [ "$DOC_SUGGESTIONS_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Codex session read v97 is applied without v96 predecessor" >&2
  exit 1
fi

TOUR_FEEDBACK_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0750_tour_client_feedback_scac_successor.sql')" \
  2>/dev/null)"
case "$TOUR_FEEDBACK_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Tour feedback v98 registry ledger state" >&2; exit 1 ;;
esac
if [ "$TOUR_FEEDBACK_REGISTRY_APPLIED" = t ] && [ "$CODEX_SESSION_READ_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Tour feedback v98 is applied without v97 predecessor" >&2
  exit 1
fi

PROPERTY_EVIDENCE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0755_property_evidence_scac_successor.sql')" \
  2>/dev/null)"
case "$PROPERTY_EVIDENCE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read property evidence v99 registry ledger state" >&2; exit 1 ;;
esac
if [ "$PROPERTY_EVIDENCE_REGISTRY_APPLIED" = t ] && [ "$TOUR_FEEDBACK_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: property evidence v99 is applied without v98 predecessor" >&2
  exit 1
fi


PROGRESS_DIRECTORY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0763_progress_directory_scac_successor.sql')" \
  2>/dev/null)"
case "$PROGRESS_DIRECTORY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Progress directory v100 registry ledger state" >&2; exit 1 ;;
esac
if [ "$PROGRESS_DIRECTORY_REGISTRY_APPLIED" = t ] && [ "$PROPERTY_EVIDENCE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Progress directory v100 is applied without v99 predecessor" >&2
  exit 1
fi

WHATS_NEW_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0767_doc_whats_new_scac_successor.sql')" \
  2>/dev/null)"
case "$WHATS_NEW_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read catch-up v101 registry ledger state" >&2; exit 1 ;;
esac
if [ "$WHATS_NEW_REGISTRY_APPLIED" = t ] && [ "$PROGRESS_DIRECTORY_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: catch-up v101 is applied without v100 predecessor" >&2
  exit 1
fi

CONFIRM_MERGE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0768_confirm_merge_human_only_scac_successor.sql')" \
  2>/dev/null)"
case "$CONFIRM_MERGE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read confirm-merge v102 registry ledger state" >&2; exit 1 ;;
esac
if [ "$CONFIRM_MERGE_REGISTRY_APPLIED" = t ] && [ "$WHATS_NEW_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: confirm-merge v102 is applied without v101 predecessor" >&2
  exit 1
fi

FIND_RULE_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0786_find_rule_scac_successor.sql')" \
  2>/dev/null)"
case "$FIND_RULE_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read find-rule v103 registry ledger state" >&2; exit 1 ;;
esac
if [ "$FIND_RULE_REGISTRY_APPLIED" = t ] && [ "$CONFIRM_MERGE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: find-rule v103 is applied without v102 predecessor" >&2
  exit 1
fi


JEV_CAP_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0787_jev_cap_scac_successor.sql')" \
  2>/dev/null)"
case "$JEV_CAP_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Jev cap v104 registry ledger state" >&2; exit 1 ;;
esac
if [ "$JEV_CAP_REGISTRY_APPLIED" = t ] && [ "$FIND_RULE_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Jev cap v104 is applied without v103 predecessor" >&2
  exit 1
fi

OBSERVATORY_ROOM_READ_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0807_observatory_room_read_scac_successor.sql')" \
  2>/dev/null)"
case "$OBSERVATORY_ROOM_READ_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Observatory room read v105 registry ledger state" >&2; exit 1 ;;
esac
if [ "$OBSERVATORY_ROOM_READ_REGISTRY_APPLIED" = t ] && [ "$JEV_CAP_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Observatory room read v105 is applied without v104 predecessor" >&2
  exit 1
fi

LEAD_AUTOMATION_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0812_lead_automation_scac_successor.sql')" \
  2>/dev/null)"
case "$LEAD_AUTOMATION_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read lead automation v106 registry ledger state" >&2; exit 1 ;;
esac
if [ "$LEAD_AUTOMATION_REGISTRY_APPLIED" = t ] && [ "$OBSERVATORY_ROOM_READ_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: lead automation v106 is applied without v105 predecessor" >&2
  exit 1
fi

DOC_ACTIVITY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0825_doc_activity_scac_successor.sql')" \
  2>/dev/null)"
case "$DOC_ACTIVITY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Doc activity v107 registry ledger state" >&2; exit 1 ;;
esac
if [ "$DOC_ACTIVITY_REGISTRY_APPLIED" = t ] && [ "$LEAD_AUTOMATION_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Doc activity v107 is applied without v106 predecessor" >&2
  exit 1
fi



SYSTEM_WORK_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0827_system_work_scac_successor.sql')" \
  2>/dev/null)"
case "$SYSTEM_WORK_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read system-work v108 registry ledger state" >&2; exit 1 ;;
esac
if [ "$SYSTEM_WORK_REGISTRY_APPLIED" = t ] && [ "$DOC_ACTIVITY_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: system-work v108 is applied without v107 predecessor" >&2
  exit 1
fi

RELATIONSHIP_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0840_relationship_scac_successor.sql')" \
  2>/dev/null)"
case "$RELATIONSHIP_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read relationship v109 registry ledger state" >&2; exit 1 ;;
esac
if [ "$RELATIONSHIP_REGISTRY_APPLIED" = t ] && [ "$SYSTEM_WORK_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: relationship v109 is applied without v108 predecessor" >&2
  exit 1
fi

INVOICE_TRACKER_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0842_invoice_tracker_scac_successor.sql')" \
  2>/dev/null)"
case "$INVOICE_TRACKER_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read invoice tracker v110 registry state" >&2; exit 1 ;;
esac
if [ "$INVOICE_TRACKER_REGISTRY_APPLIED" = t ] && [ "$RELATIONSHIP_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: invoice tracker v110 is applied without v109 predecessor" >&2; exit 1
fi

AUTOMATION_UNDO_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0844_automation_undo_scac_successor.sql')" \
  2>/dev/null)"
case "$AUTOMATION_UNDO_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read audited automation v111 registry ledger state" >&2; exit 1 ;;
esac
if [ "$AUTOMATION_UNDO_REGISTRY_APPLIED" = t ] && [ "$INVOICE_TRACKER_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: audited automation v111 is applied without v110 predecessor" >&2
  exit 1
fi

LEADS_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0846_leads_scac_successor.sql')" \
  2>/dev/null)"
case "$LEADS_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read Leads v112 registry ledger state" >&2; exit 1 ;;
esac
if [ "$LEADS_REGISTRY_APPLIED" = t ] && [ "$AUTOMATION_UNDO_REGISTRY_APPLIED" != t ]; then
  echo "schema-snapshot: Leads v112 is applied without v111 predecessor" >&2
  exit 1
fi

# WR-000117. 0530 is the registry successor half of the atomic (0529,0530)
# group, so probing the SUCCESSOR and not the domain migration is what says the
# v34 registry surface exists. A snapshot taken between the two would be taken
# inside a transaction that has not committed, which cannot happen.
SESSION_IDENTITY_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0530_session_identity_scac_successor.sql')" \
  2>/dev/null)"
case "$SESSION_IDENTITY_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the session identity registry ledger state" >&2; exit 1 ;;
esac

# pg_dump renders timestamptz in the server session timezone; pin it so the
# Production and disposable-local paths serialize identical instants alike.
export PGOPTIONS='-c timezone=UTC'

TMP="$(mktemp)"
SCHEMA_BODY="$(mktemp)"
trap 'rm -f "$TMP" "$SCHEMA_BODY" "$SERVICE_FILE" "$PASSFILE"' 0

# THE ROLE PREAMBLE, first in the file so the roles exist before anything that
# could reference them. See the header for why this cannot be left to 0115.
cat > "$TMP" <<'ROLES'
--
-- CARR ROLE PREAMBLE (bin/schema-snapshot.sh) — not produced by pg_dump.
--
-- This dump is --no-owner --no-acl, so it names no roles and grants nothing.
-- The roles still have to EXIST before the pending migrations that grant to
-- them run, and they can no longer be got by replaying 0115: once this
-- snapshot's ledger passed 0115 that migration stopped being pending anywhere.
-- carr_exporter aged into the same trap by way of 0006 and joined the list on
-- 2026-08-14, when the grants section below started carrying its privileges.
-- carr_authority aged into it by way of 0161 and joined on 2026-08-19: the
-- refresh that carried the ledger past 0161 stopped that migration replaying,
-- and the next rebuild failed five db-gates with `role "carr_authority" does
-- not exist`. That is the third time this exact trap has been sprung, so the
-- rule it teaches is worth stating plainly: ANY migration that creates a role
-- must add that role here in the same change, because the day its ledger entry
-- lands is the day it stops creating anything.
-- carr_device_evidence made it FOUR, the same day and on this same branch, by
-- way of 0163. It surfaced only after the guidance and retrieval seed rows were
-- added, because control-plane-db-gate could not reach the privilege check that
-- names it until the gates ahead of it stopped failing — and when it did reach
-- it, it did not fail cleanly: `has_function_privilege('carr_device_evidence',
-- ...)` RAISES on a missing role, so the gate crashed with a traceback instead
-- of a finding. Four for four, every one caught by a rebuild rather than by the
-- change that created the role.
-- carr_gate_zero_producer made it FIVE by way of 0502, and it is the first one
-- the SCAC catalog caught rather than a db-gate: 0503 seals the v26 function
-- ACL projection at 467 rows, one of which is this role's execute grant on
-- ops.gate_zero_record_read_only_outcome. A rebuild without the role observes
-- 466 and the restore guard refuses the whole snapshot with `restored SCAC
-- registry failed exact historical, current, or per-entry contract seals` —
-- a message that names the registry and not the missing role, so the fifth
-- spring of this trap cost a bisect of the guard's five sub-conditions. Five
-- for five, still none caught by the change that created the role.
--
-- All repository-owned roles whose creating migrations are in the snapshot
-- ledger are created here. carr_backup (LOGIN) is deliberately NOT: it is the backup credential,
-- bin/backup-dump.sh supplies it, no gate asks for it, and creating a second
-- login role with a placeholder password to satisfy nothing is a cost with no
-- buyer. If a gate ever needs it, add it the way carr_jobs is added, not by
-- widening a pattern.
-- carr_reader, carr_writer, carr_exporter, carr_authority,
-- carr_device_evidence, the four calendar-prebrief roles, and the renewal
-- source-attestor role are privilege
-- bundles, so they stay NOLOGIN. carr_jobs, carr_gate_zero_producer, and
-- carr_foundation_assurance_oracle are narrow machine identities, and a fresh
-- rebuild must make each of them LOGIN. That is not cosmetic:
-- the v26 role-authority projection counts the NOLOGIN carr_* roles, so minting
-- either of these as NOLOGIN moves that count off its seal and breaks the
-- restore in a second place. carr_jobs takes a fresh random placeholder
-- password, generated in-process and never selected, logged, or written into
-- this dump; carr_gate_zero_producer takes none at all, for the reason recorded
-- at its branch below. An already-login role is left completely unchanged.
--
do $$
declare
  r text;
  jobs_can_login boolean;
  jobs_placeholder text;
  producer_can_login boolean;
  foundation_oracle_can_login boolean;
begin
  foreach r in array array[
    'carr_reader','carr_writer','carr_exporter','carr_authority','carr_device_evidence',
    'carr_calendar_prebrief_jobs','carr_calendar_prebrief_canary_jobs',
    'carr_calendar_prebrief_attestors','carr_calendar_prebrief_email_resolver',
    'carr_program5_forward_fix_verifiers',
    'carr_renewal_source_attestors',
    'carr_foundation_assurance_oracle'
  ] loop
    if r = 'carr_foundation_assurance_oracle' then
      select rolcanlogin into foundation_oracle_can_login
        from pg_roles where rolname = r;
      if not found then
        execute format('create role %I login', r);
      elsif not foundation_oracle_can_login then
        execute format('alter role %I login', r);
      end if;
    elsif not exists (select 1 from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
  end loop;
  select rolcanlogin into jobs_can_login from pg_roles where rolname='carr_jobs';
  if not found then
    jobs_placeholder := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
    execute format('create role %I login password %L', 'carr_jobs', jobs_placeholder);
  elsif not jobs_can_login then
    jobs_placeholder := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
    execute format('alter role %I login password %L', 'carr_jobs', jobs_placeholder);
  end if;

  -- THE GATE ZERO PRODUCER SEAT TAKES NO PASSWORD, and that is the difference
  -- between the two machine logins rather than an oversight. 0502 creates it as
  -- `create role carr_gate_zero_producer login` with nothing else, and
  -- ops/staging-database-login-provision-db-gate.py reads the seat's baseline
  -- back as (rolcanlogin, rolpassword is null, rolconfig is null) = (t,t,t)
  -- before it provisions anything — a snapshot that minted the seat with
  -- carr_jobs' placeholder password failed that gate with (t,f,t). The seat is
  -- credentialed by the provisioning path the gate exercises, not by this file,
  -- so reproduce 0502's shape exactly and leave an existing role alone.
  select rolcanlogin into producer_can_login from pg_roles where rolname='carr_gate_zero_producer';
  if not found then
    execute format('create role %I login', 'carr_gate_zero_producer');
  elsif not producer_can_login then
    execute format('alter role %I login', 'carr_gate_zero_producer');
  end if;

  -- THE AGING TRAP ONE LEVEL DOWN: not creating a role, but joining one.
  -- 0273 grants the carr_authority bundle to the human authority login roles,
  -- and the day a refresh carried this ledger past 0273 that grant stopped
  -- replaying anywhere. Nothing else carried it: the CARR GRANTS section below
  -- admits only app roles and neondb_owner as members, and neither login role
  -- is either. So a database rebuilt from this file had carr_authority holding
  -- its 24 grants and NOBODY holding carr_authority — the ledger claiming 0273
  -- applied while the file it vouches for described a database where it never
  -- had. Found by an independent review seat, loop #506 finding 3.
  --
  -- WHY THE LOGIN ROLES ARE NOT CREATED HERE, only joined. They are human
  -- authority credentials, provisioned in the database provider's console
  -- outside this repository. Minting a local carr_authority_joe so a grant has
  -- somewhere to land would manufacture a login that authenticates as Joe's
  -- authority principal on every machine that rebuilds. That is the carr_backup
  -- reasoning above, except the cost is a security regression rather than an
  -- unused role.
  --
  -- SO EACH IS GUARDED AND THE ABSENCE IS ANNOUNCED, which is 0273's own shape
  -- reproduced rather than production's current state copied. Reproducing the
  -- loop is what makes this self-maintaining: carr_authority_dell does not
  -- exist today (the control-plane contract marks Dell's authority login
  -- optional_nonblocking), and on the day it is provisioned a rebuild grants him
  -- too with no edit here. Announced rather than skipped in silence, per rule
  -- 88e9b5eb: "not authorized" and "not possible" are different findings.
  -- Re-granting an existing membership is a no-op, so this is idempotent.
  foreach r in array array['carr_authority_joe', 'carr_authority_dell'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant carr_authority to %I', r);
    else
      raise notice 'authority login role % is absent, so the carr_authority membership 0273 grants it was not applied', r;
    end if;
  end loop;

end $$;

ROLES

# Do not pre-create a role from a migration the snapshot has not absorbed: a
# pending migration remains the one source of truth for it. Once 0532a is in the
# ledger, reproduce its repository-owned issuer bundle and generation logins.
if [ "$CANONICAL_OWNERSHIP_ACTIVATION_APPLIED" = t ]; then
cat >> "$TMP" <<'CANONICAL_OWNERSHIP_ROLES'

-- CARR CANONICAL OWNERSHIP ISSUER ROLES (bin/schema-snapshot.sh) — 0532a.
do $carr_ownership_issuer_roles$
declare r text;
begin
  if not exists (select 1 from pg_roles where rolname='carr_ownership_issuer') then
    create role carr_ownership_issuer nologin noinherit nobypassrls;
  end if;
  if exists (select 1 from pg_roles where rolname='carr_ownership_issuer' and
    (rolcanlogin or rolinherit or rolbypassrls or rolsuper or rolcreaterole or rolcreatedb)) then
    raise exception 'ownership issuer capability role has unsafe attributes';
  end if;
  foreach r in array array['carr_ownership_issuer_g1', 'carr_ownership_issuer_g2'] loop
    if not exists (select 1 from pg_roles where rolname=r) then
      execute format('create role %I login noinherit nobypassrls',r);
    end if;
    if exists (select 1 from pg_roles where rolname=r and
      (not rolcanlogin or rolinherit or rolbypassrls or rolsuper or rolcreaterole or rolcreatedb)) then
      raise exception 'ownership issuer login has unsafe attributes: %',r;
    end if;
    execute format('grant carr_ownership_issuer to %I',r);
  end loop;
end $carr_ownership_issuer_roles$;
CANONICAL_OWNERSHIP_ROLES
fi

# Pending 0756 owns creation. After release, restore a passwordless login
# before pg_dump's role-bound read policies. Never reactivate an existing role.
if [ "$DOT_READER_APPLIED" = t ]; then
cat >> "$TMP" <<'DOT_READER_ROLES'
do $dot_snapshot_role$
begin
  if not exists (select 1 from pg_roles where rolname='dot_reader') then
    create role dot_reader login noinherit nosuperuser nocreatedb nocreaterole
      noreplication nobypassrls connection limit 2;
    if current_user <> 'neondb_owner' then
      grant dot_reader to neondb_owner with admin true, inherit false, set false;
      execute format('revoke dot_reader from %I',current_user);
    end if;
    alter role dot_reader set statement_timeout='30s';
    alter role dot_reader set lock_timeout='5s';
  end if;
end $dot_snapshot_role$;
DOT_READER_ROLES
fi

# Keep the raw dump separate so pg_dump's exit status cannot be hidden behind a
# filter pipeline. Production has an externally provisioned carr_backup login,
# so pg_dump renders 0475's role-conditional policy as an unconditional CREATE
# POLICY naming that role. A disposable rebuild deliberately does NOT mint this
# production credential; preserve the migration's original conditional boundary
# in the portable snapshot instead of making CI depend on a fake login.
if ! "$PG_DUMP" --schema-only --no-owner --no-acl > "$SCHEMA_BODY"; then
  echo "schema-snapshot: pg_dump failed — nothing written" >&2
  exit 1
fi
if ! awk '
function emit_carr_backup_policy(policy, table) {
  print "do $carr_backup_snapshot_policy$"
  print "begin"
  print "  if exists (select 1 from pg_roles where rolname = '\''carr_backup'\'') then"
  print "    create policy " policy " on " table
  print "      for select to carr_backup using (true);"
  print "  end if;"
  print "end"
  print "$carr_backup_snapshot_policy$;"
}
function emit_missing_carr_backup_policy(policy, table, name) {
  if (carr_backup_policy_seen[table]) return
  split(table, name, ".")
  print "-- Name: " name[2] " " policy "; Type: POLICY; Schema: " name[1] "; Owner: -"
  print "--"
  print ""
  emit_carr_backup_policy(policy, table)
  print ""
  print ""
  print "--"
  carr_backup_policy_seen[table] = 1
}
# EVERY carr_backup read-all policy, not only 0475s. 0573 added one on
# public.memory_item, and pg_dump rendered it unconditionally, so the portable
# snapshot named a login a disposable rebuild never mints and CI refused it
# ("role carr_backup does not exist").
$0 ~ /^CREATE POLICY [a-z_][a-z0-9_]* ON [a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]* FOR SELECT TO carr_backup USING \(true\);$/ {
  split($0, words, " ")
  emit_carr_backup_policy(words[3], words[5])
  carr_backup_policy_seen[words[5]] = 1
  next
}
$0 == "-- Name: work_request; Type: ROW SECURITY; Schema: ops; Owner: -" {
  emit_missing_carr_backup_policy("carr_backup_full_read", "ops.work_request")
}
$0 == "-- Name: memory_item; Type: ROW SECURITY; Schema: public; Owner: -" {
  emit_missing_carr_backup_policy("carr_backup_full_read_memory_item", "public.memory_item")
}
{ print }
' "$SCHEMA_BODY" >> "$TMP"; then
  echo "schema-snapshot: could not preserve the carr_backup policy boundary — nothing written" >&2
  exit 1
fi

cat >> "$TMP" <<'SNAPSHOT_DATA_RESTORE_BEGIN'

-- CARR SNAPSHOT DATA RESTORE TRANSACTION BEGIN
-- The schema body above installs deferred policy-epoch triggers. Keep the
-- ledger and every bounded configuration seed below in one transaction so a
-- restore can expose only the final coherent epoch, never a half-restored one.
BEGIN;

SNAPSHOT_DATA_RESTORE_BEGIN

# THE CARR GRANTS SECTION. --no-acl stays — a raw ACL dump names Neon's own
# principals and whatever login roles neonctl has minted per environment, and
# the first grant naming an absent role aborts the load. Instead the app roles'
# privileges are read from the catalogs and emitted as plain GRANTs, scoped by
# grantee to the NOLOGIN bundles plus the narrow carr_jobs login the preamble
# creates. Membership
# bundles may additionally name neondb_owner (0005/0006 grant it the bundles),
# which every loading environment already has: .github/workflows/ci.yml creates
# it for the same reason.
#
# The section rides AFTER the structure because a grant on a table that does
# not exist yet aborts the load, and BEFORE the ledger because it is structure,
# not data. SIX shapes, each ordered deterministically so --check reports
# drift and nothing else: PUBLIC revokes, schema usage, relation grants (tables,
# views, sequences), column-scoped grants (0021, 0117 — where the columns
# OUTSIDE the list are the point), function execute (0094, 0106), and
# memberships.
#
# THE PUBLIC REVOKES COME FIRST AND ARE THE ONLY SHAPE THAT TAKES A PRIVILEGE
# AWAY, added 2026-08-19. Postgres grants EXECUTE on every new function to
# PUBLIC by default, and PUBLIC includes every role, so a migration that means
# to keep a function to one principal has to revoke that default explicitly —
# 0168 does exactly this: `revoke all on function ops.record_guidance_decision
# (uuid,text,text,text) from public,carr_writer`. This section emitted only
# GRANTs, so a database built from the snapshot got every function back at its
# permissive default while the revoke that tightened it sat in a migration the
# ledger had already absorbed.
#
# That is the same aging trap as the roles, one level down, and it fails in the
# more dangerous direction: a rebuilt environment is LOOSER than production, not
# broken, so nothing refuses to start — CI's db-gates caught it as
# "carr_writer can execute authority function ops.record_guidance_decision",
# "browser challenge ledger grant leaked" and "raw plan/acceptance authority
# leaked" only because those gates assert the boundary directly. Production is
# unaffected: it holds the revokes already, and this file is never loaded into
# it.
#
# Read from production's catalogs like everything else here. A NULL proacl means
# the function still carries the default, so there is nothing to revoke; a
# non-null proacl that does not name PUBLIC (grantee 0) is a function whose
# default was deliberately taken away, and that is what gets emitted.
GRANTS_SQL="$(mktemp)"
cat > "$GRANTS_SQL" <<'GRANTSQL'
-- Force pg_get_function_identity_arguments() to schema-qualify composite
-- argument types.  Otherwise a type visible through the dump connection's
-- search_path is rendered bare and the later grants section cannot load under
-- a fresh session's search_path.
set search_path = '';

-- Built from nspname + proname + identity arguments rather than from
-- oid::regprocedure, which omits the schema for anything the current
-- search_path already covers. That produced bare `revoke all on function
-- capture_call_context(uuid[])` lines, which resolve to whatever the LOADING
-- session's search_path happens to point at — the one thing a snapshot must
-- never depend on.
select format('revoke all on function %s.%s(%s) from public;',
              quote_ident(n.nspname), quote_ident(p.proname),
              pg_get_function_identity_arguments(p.oid))
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
 where n.nspname in ('ops', 'public')
   and p.proacl is not null
   and not exists (select 1
                     from aclexplode(p.proacl) a
                    where a.grantee = 0
                      and a.privilege_type = 'EXECUTE')
 order by 1;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_program5_forward_fix_verifiers'),
         ('carr_renewal_source_attestors'), ('carr_gate_zero_producer'),
         ('carr_foundation_assurance_oracle'), ('carr_ownership_issuer'), ('dot_reader')
)
select format('grant %s on schema %s to %s;',
              string_agg(distinct lower(a.privilege_type), ', '
                         order by lower(a.privilege_type)),
              n.nspname, r.rolname)
  from pg_namespace n
  cross join lateral aclexplode(n.nspacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
 group by n.nspname, r.rolname
 order by n.nspname, r.rolname;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_program5_forward_fix_verifiers'),
         ('carr_renewal_source_attestors'), ('carr_gate_zero_producer'),
         ('carr_foundation_assurance_oracle'), ('carr_ownership_issuer'), ('dot_reader')
)
select format('grant %s on %s %s.%s to %s;',
              string_agg(distinct lower(a.privilege_type), ', '
                         order by lower(a.privilege_type)),
              case c.relkind when 'S' then 'sequence' else 'table' end,
              n.nspname, c.relname, r.rolname)
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(c.relacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
 group by n.nspname, c.relname, c.relkind, r.rolname
 order by n.nspname, c.relname, r.rolname;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_program5_forward_fix_verifiers'),
         ('carr_renewal_source_attestors'), ('carr_gate_zero_producer'),
         ('carr_foundation_assurance_oracle'), ('carr_ownership_issuer'), ('dot_reader')
)
select format('grant %s (%s) on table %s.%s to %s;',
              lower(a.privilege_type),
              string_agg(att.attname, ', ' order by att.attnum),
              n.nspname, c.relname, r.rolname)
  from pg_attribute att
  join pg_class c on c.oid = att.attrelid
  join pg_namespace n on n.oid = c.relnamespace
  cross join lateral aclexplode(att.attacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
   and not att.attisdropped
 group by n.nspname, c.relname, r.rolname, a.privilege_type
 order by n.nspname, c.relname, r.rolname, lower(a.privilege_type);

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_program5_forward_fix_verifiers'),
         ('carr_renewal_source_attestors'), ('carr_gate_zero_producer'),
         ('carr_foundation_assurance_oracle'), ('carr_ownership_issuer'), ('dot_reader')
)
select format('grant execute on function %s.%s(%s) to %s;',
              n.nspname, p.proname,
              pg_get_function_identity_arguments(p.oid), r.rolname)
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  cross join lateral aclexplode(p.proacl) a
  join pg_roles r on r.oid = a.grantee
 where r.rolname in (select rolname from app)
   and lower(a.privilege_type) = 'execute'
 order by n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), r.rolname;

with app(rolname) as (
  values ('carr_reader'), ('carr_writer'), ('carr_jobs'), ('carr_exporter'), ('carr_authority'), ('carr_device_evidence'),
         ('carr_calendar_prebrief_jobs'), ('carr_calendar_prebrief_canary_jobs'),
         ('carr_calendar_prebrief_attestors'), ('carr_calendar_prebrief_email_resolver'),
         ('carr_program5_forward_fix_verifiers'),
         ('carr_renewal_source_attestors'), ('carr_gate_zero_producer'),
         ('carr_foundation_assurance_oracle'), ('carr_ownership_issuer'), ('dot_reader')
), membership_member(rolname) as (
  select rolname from app
  union all values ('neondb_owner'), ('carr_ownership_issuer_g1'), ('carr_ownership_issuer_g2')
)
-- pg_auth_members permits different grantors for the same role/member pair.
-- The snapshot has no grantor field, so render each semantically identical
-- membership exactly once without deduplicating any object ACL shape above.
select distinct format('grant %s to %s;', gr.rolname, mem.rolname)
  from pg_auth_members m
  join pg_roles gr  on gr.oid  = m.roleid
  join pg_roles mem on mem.oid = m.member
 where gr.rolname in (select rolname from app)
   and not gr.rolcanlogin
   and mem.rolname in (select rolname from membership_member)
 order by 1;
GRANTSQL

cat >> "$TMP" <<'GRANTHDR'
--
-- CARR GRANTS (bin/schema-snapshot.sh) — not produced by pg_dump.
--
-- The app roles' privileges, read from production's catalogs. Without them a
-- database built from this file has the roles holding nothing, and CI's
-- migration class answers has_table_privilege() with false for everything —
-- the 2026-08-14 gap. Grantees are scoped to the preamble's four roles (plus
-- neondb_owner, membership bundles only) so no other principal's ACLs can
-- enter the tree. Shapes pinned by tools/test-schema-snapshot-grants.py.
--

GRANTHDR

if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 -f "$GRANTS_SQL" >> "$TMP"; then
  rm -f "$GRANTS_SQL"
  echo "schema-snapshot: could not read the app-role grants — nothing written" >&2
  exit 1
fi
rm -f "$GRANTS_SQL"

# An empty grants section is the truncation problem in a new coat: production
# certainly holds grants for these roles, so zero emitted statements means the
# read failed in a way psql did not report, and committing the result would
# silently define a database where the roles hold nothing — the exact gap this
# section exists to close.
if ! grep -q '^grant .* to carr_' "$TMP"; then
  echo "schema-snapshot: grants section came back empty — nothing written" >&2
  exit 1
fi

# THE APPLIED-MIGRATION LEDGER RIDES ALONG, and it is the difference between a
# snapshot that works and one that does not. schema_migrations is BUILD
# metadata, not business data — it records which migration filenames have run.
# Without its rows, a database loaded from this file has the full structure but
# an empty ledger, so the runner would try to apply all 130 migrations again and
# die on the first CREATE TABLE that already exists. With them, the loaded
# database honestly reports itself up to date and the ONLY thing pending is a
# genuinely new migration — which is exactly the question worth gating a change
# on: does this new change apply cleanly to the database we actually have?
if ! "$PG_DUMP" --data-only --no-owner --no-acl --table=schema_migrations >> "$TMP"; then
  echo "schema-snapshot: could not dump the applied-migration ledger — nothing written" >&2
  exit 1
fi

# Database ACLs and creator defaults are outside pg_dump --no-acl. Keep this
# outside the closed CARR GRANTS grammar, after its ledger dump boundary.
if [ "$DOT_READER_APPLIED" = t ]; then
# Object/schema/function ACLs have already been replayed from the source.
# Reconstruct only the remaining database ACLs and observed creator defaults;
# never reapply 0756's initial blanket policy over an authorized narrowing.
if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP" <<'DOT_READER_STATE'
select 'do $dot_snapshot_grants$ begin';
select format('execute format(%L,current_database());',
              format('revoke %s on database %%I from %s;',privilege,grantee))
  from (values ('connect','public'),('create','public'),('temporary','public'),
               ('connect','dot_reader')) reset(privilege,grantee);
select format('execute format(%L,current_database());',
              format('grant %s on database %%I to %s%s;',lower(a.privilege_type),
                     case when a.grantee=0 then 'public' else 'dot_reader' end,
                     case when a.is_grantable then ' with grant option' else '' end))
  from pg_database d cross join lateral aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
 where d.datname=current_database()
   and (a.grantee=0 or (a.grantee='dot_reader'::regrole and a.privilege_type='CONNECT'));
select format('revoke create on schema %I from public;',n.nspname)
  from pg_namespace n where n.nspname in ('public','ops');
select format('grant create on schema %I to public;',n.nspname)
  from pg_namespace n cross join lateral aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
 where n.nspname in ('public','ops') and a.grantee=0 and a.privilege_type='CREATE';

-- pg_dump omits default ACLs under --no-acl. Preserve schema-specific absence
-- as absence and replay grants only where the catalog actually carries them.
-- Source connection-owner defaults follow the dump's owner remapping; other
-- creators retain their explicit, already-declared role identity.
select format('alter default privileges%s%s grant %s on %s to dot_reader%s;',
              case when d.defaclrole=current_user::regrole then ''
                   else format(' for role %I',r.rolname) end,
              case when d.defaclnamespace=0 then '' else format(' in schema %I',n.nspname) end,
              lower(a.privilege_type),
              case d.defaclobjtype when 'r' then 'tables' when 'S' then 'sequences' when 'f' then 'functions' end,
              case when a.is_grantable then ' with grant option' else '' end)
  from pg_default_acl d join pg_roles r on r.oid=d.defaclrole
  left join pg_namespace n on n.oid=d.defaclnamespace
  cross join lateral aclexplode(d.defaclacl) a
 where a.grantee='dot_reader'::regrole and d.defaclobjtype in ('r','S','f')
   and (d.defaclnamespace=0 or n.nspname in ('public','ops')) order by 1;
select format('alter default privileges%s revoke execute on functions from public;',
              case when d.defaclrole=current_user::regrole then ''
                   else format(' for role %I',r.rolname) end)
  from pg_default_acl d join pg_roles r on r.oid=d.defaclrole
 where d.defaclobjtype='f' and d.defaclnamespace=0
   and not exists(select 1 from aclexplode(d.defaclacl) a
                  where a.grantee=0 and a.privilege_type='EXECUTE');
select format('alter default privileges%s in schema %I grant execute on functions to public;',
              case when d.defaclrole=current_user::regrole then ''
                   else format(' for role %I',r.rolname) end,n.nspname)
  from pg_default_acl d join pg_roles r on r.oid=d.defaclrole
  join pg_namespace n on n.oid=d.defaclnamespace
  cross join lateral aclexplode(d.defaclacl) a
 where d.defaclobjtype='f' and n.nspname in ('public','ops')
   and a.grantee=0 and a.privilege_type='EXECUTE';
select 'end $dot_snapshot_grants$;';
DOT_READER_STATE
then
  echo "schema-snapshot: could not read Dot authorization state — nothing written" >&2
  exit 1
fi
fi

# REFERENCE VOCABULARY IS A THIRD CATEGORY, and leaving it out made the first
# staging build useless. It is neither structure nor business data: closed sets
# like the eight deal phases or the six client statuses, seeded by migrations,
# pointed at by foreign keys. A database with the structure and none of these
# cannot hold a single row — client.status is a foreign key to client_status,
# which was empty. Verified on the first staging load: 115 tables, 0 statuses.
#
# THE LIST IS EXPLICIT AND HAND-CHECKED, never pattern-matched. Every table below
# was counted on production first (2 to 18 rows each, all closed vocabularies).
# A pattern like "small tables" or "tables seeded by a migration" would sweep in
# client, party and event, which the same migrations also insert into — dumping
# those would put real client data in a tracked file, which is the one outcome
# this whole design exists to prevent.
#
# DELIBERATELY EXCLUDED, and worth stating rather than leaving to inference:
#   * system_config (21 rows) — operational settings, not vocabulary, and the
#     kind of table where a value that should never be committed can appear.
#     Staging can set its own.
#   * client, party, deal, event and every other business table — the point.
# `actor` (12 rows) IS included: internal actor identities (joe, dell, system,
# automation) that events carry foreign keys to. Not client data.
#
# TWO SEEDED CONFIGURATION TABLES JOINED ON 2026-08-19, on Joe's ruling, and they
# widen this list's category from "closed vocabulary" to "rows production has
# that a rebuild silently lacks". Both are data steps inside migrations the
# ledger has now absorbed, so the migration no longer replays and a database
# built from this file gets the table and none of its rows — the same aging trap
# as the roles and the PUBLIC revokes above, one level further in:
#   * ops.guidance_registry (1 row) — the registry's own header row, created by
#     0168's `insert into ops.guidance_registry(created_by)`. Without it
#     guidance-registry-db-gate fails on "expected one row ... where singleton".
#   * retrieval_proposal (10 rows, all pending) — 0135's own "Seed evidence
#     only" block, which it says "activate nothing until Joe or Dell approves
#     the batch". Without them situation-retrieval-db-gate fails on "expected 10
#     pending seed proposals, found 0".
#   * retrieval_ranking_policy (2 rows) — the versioned ranking policies,
#     coequal-normalized-v1 (active, default) and lexical-dominant-v1
#     (candidate). The active row carries golden_suite_digest, which
#     assert_situation_retrieval_golden() looks up BY DIGEST; with no policy row
#     the lookup returns null and it raises "golden suite digest mismatch".
#     Found only after adding the two above, because the gate could not reach
#     this check until it got past the proposals.
#   * The exact two reviewed ops.enforcement_control_catalog controls are
#     verified separately and appended as deterministic SQL below — never via
#     the vocabulary pg_dump. Their fixed implementation/test references,
#     enforcement classes, and installed/verification metadata contain no
#     client, deal, event, secret, or runtime usage data. 0194 is already in a
#     rebuilt snapshot's ledger, so its seed does not replay; without this
#     controlled block the 0228 lifecycle cannot validate pinned rules. Do not
#     add rule_control_binding or any receipt/rule table: those are per-rule
#     history, not bounded internal control configuration.
#   * ops.rule_delivery_policy (exactly 1 row) and
#     ops.rule_delivery_activation_target (the exact ledger-appropriate row set) are the bounded
#     configuration for the already-existing scoped rule-delivery cutover.
#     0291 and 0317 seed them, but once those migrations enter this snapshot's
#     ledger they no longer replay. Omitting the rows produced mode:null and no
#     cutover target set on a fresh rebuild. Carry these two tables only; never
#     add ops.rule_delivery_observation, ops.rule_delivery_activation_receipt,
#     or any other runtime/evidence table to this list.
#   * The disabled renewal-radar-source-daily v1 job definition is a fixed
#     internal contract required by 0230's lease-bound delivery gate. Carry
#     only that exact row; never widen this to arbitrary job/runtime rows.
#
# I counted every other table 0135 and 0168 seed, so the next rebuild does not
# discover a fourth one the same way: doctrine_concept_mapping,
# retrieval_concept, retrieval_phrase, ops.guidance_authority_binding,
# ops.guidance_situation_mapping and ops.guidance_registry_event are all EMPTY on
# production and need nothing. retrieval_query_log has 185 rows and is
# deliberately EXCLUDED — it is a usage log, not configuration, it carries the
# text of real queries, and no gate asks for it.
# HAND-CHECKED BEFORE ADDING, which is the rule this list lives by: counted on
# production (exactly 1 and exactly 10, all ten still pending), and every payload
# read. They are search concepts and phrases about this system's own runbooks —
# "record layer outage diagnosis", "how the operating playbook learns from
# mistakes". No client, party, deal, note or event appears in either table.
#
# THE LINE THAT STILL HOLDS, so this widening does not become a licence: a table
# qualifies because someone read its rows and can say what is in them, never
# because a migration seeded it. "Tables seeded by a migration" would sweep in
# client, party and event, which the same migrations also insert into.
VOCAB_TABLES="activity_kind client_status client_type contact_state deal_lane \
deal_phase deal_type_ref lead_lane lead_stage loop_domain negotiation_claim_type \
participant_role party_link_kind vendor_category vendor_disposition \
vendor_relationship_level diagnostic_route submarket_condition doctrine_edge_type \
doctrine_review_policy actor retrieval_proposal retrieval_ranking_policy \
ops.guidance_registry ops.rule_delivery_policy \
ops.rule_delivery_activation_target ops.slice_marker_seat"

VOCAB_ARGS=""
for t in $VOCAB_TABLES; do
  VOCAB_ARGS="$VOCAB_ARGS --table=$t"
done

# shellcheck disable=SC2086
if ! "$PG_DUMP" --data-only --no-owner --no-acl $VOCAB_ARGS >> "$TMP"; then
  echo "schema-snapshot: could not dump the reference vocabulary — nothing written" >&2
  exit 1
fi

# The control catalog is deliberately NOT a --table dump: carrying every row
# would let arbitrary implementation prose into a tracked snapshot. Instead the
# REPOSITORY names which controls may be carried, the source is verified to hold
# every one of them, and only those are rendered — so an applied seed ledger
# cannot hide missing mutable seed rows, and nothing reaches the snapshot that
# this repository has not declared.
#
# WIDENED 2026-08-22, from two hand-listed keys to the full declared set, and the
# reason is the trap that made it necessary. The catalog is what approve-rule
# consults, and until this week it held three controls against the 59 the
# repository described, so approving a rule enforced by any of the rest needed a
# hand-written migration first. Migrations 0274 and 0275 seeded it from
# ops/config/rule-enforcement-map.json and its companion class file. But the
# moment those migrations entered Production's ledger they entered this snapshot
# too, so a database rebuilt from the snapshot considered them applied, never ran
# them, and came up with an empty catalog — failing
# ops/control-catalog-parity-gate.py on 60 absent controls. A snapshot that
# carries a migration's LEDGER ROW but not the rows it seeded describes a
# database nobody can rebuild.
#
# THE BOUNDARY THAT MATTERS IS UNCHANGED, and it is not the number two: it is
# that the key list comes from the repository rather than from whatever happens
# to be in Production. ops/sync_control_catalog.py compiles it from the same two
# declaration files the seeding migrations were generated from, so a row can only
# ride along in this snapshot if a reviewed repository change put its key there.
# A control present in the source and absent from the declarations is NOT
# rendered — it is left for ops/control-catalog-parity-gate.py to report, which
# is how ci_gates was found on 2026-08-22.
# The declared control keys, compiled from the repository's own declarations by
# the same module that generates the seeding migrations. A quoted, comma-joined
# SQL list; the count is asserted separately so a silently empty list cannot pass.
CATALOG_PY="$REPO/.venv/bin/python"
[ -x "$CATALOG_PY" ] || CATALOG_PY=python3
CURRENTNESS_PY="$REPO/ops/schema-snapshot-currentness.py"
if ! DECLARED_KEYS="$("$CATALOG_PY" - "$REPO" <<'DECLARED_CONTROL_KEYS'
import importlib.util, pathlib, sys
repo = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("scc", repo / "ops" / "sync_control_catalog.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
keys = sorted(r["control_key"] for r in mod.compile_catalog())
if not keys:
    raise SystemExit("no declared control keys")
print(",".join("'" + k.replace("'", "''") + "'" for k in keys))
DECLARED_CONTROL_KEYS
)"; then
  echo "schema-snapshot: could not compile the declared control keys — nothing written" >&2
  exit 1
fi
DECLARED_COUNT="$(printf '%s' "$DECLARED_KEYS" | tr ',' '\n' | grep -c .)"
if [ "$DECLARED_COUNT" -lt 2 ]; then
  echo "schema-snapshot: declared control key list is implausibly short — nothing written" >&2
  exit 1
fi

# THE SAME DECLARATIONS AGAIN, THIS TIME WHOLE ROWS RATHER THAN KEYS.
#
# WHY THIS EXISTS (loop #506 finding 2 — the open-loop record of the first
# independent review of a Production release, raised by one seat as a major
# finding and by the other as a minor one). The check below used to compile the
# declared KEY list from the repository and then verify only that those keys
# were present, pinning full identity for exactly two legacy controls. Every
# other declared control had its implementation_ref, test_ref, enforcement_class
# and installed flag copied straight out of the source database and rendered
# into db/schema.sql as though it matched the declaration. A declared control
# whose Production row had drifted rode into a tracked file unreviewed.
#
# It was not a hole into Production — ops/control-catalog-parity-gate.py catches
# it downstream — but it was a hole in the claim this generator exists to make:
# that an unreviewed row cannot reach a tracked file. Now every declared control
# is compared field by field before anything is rendered, so the two legacy pins
# are no longer a special case; both are inside the compiled set and are checked
# by the same comparison as everything else.
if ! DECLARED_ROWS="$("$CATALOG_PY" - "$REPO" <<'DECLARED_CONTROL_ROWS'
import importlib.util, pathlib, sys
repo = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("scc", repo / "ops" / "sync_control_catalog.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def q(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return "'" + str(value).replace("'", "''") + "'"


rows = sorted(mod.compile_catalog(), key=lambda r: r["control_key"])
if not rows:
    raise SystemExit("no declared controls")
print(",\n      ".join(
    "("
    + ", ".join(q(r[c]) for c in
                ("control_key", "implementation_ref", "test_ref", "enforcement_class"))
    + ", " + q(bool(r["installed"])) + ")"
    for r in rows))
DECLARED_CONTROL_ROWS
)"; then
  echo "schema-snapshot: could not compile the declared controls — nothing written" >&2
  exit 1
fi

# Composed into a FILE rather than substituted with sed, because the declared
# values are repository paths and sed's own delimiter lives in every one of them.
CONTROL_VERIFY_SQL="$(mktemp)"
{
  cat <<'VERIFY_HEAD'
do $carr_control_catalog$
declare drifted text;
begin
  select string_agg(d.control_key, ', ' order by d.control_key) into drifted
    from (values
VERIFY_HEAD
  printf '      %s\n' "$DECLARED_ROWS"
  cat <<'VERIFY_TAIL'
    ) as d(control_key, implementation_ref, test_ref, enforcement_class, installed)
    left join ops.enforcement_control_catalog c
      on  c.control_key        = d.control_key::text
      and c.implementation_ref = d.implementation_ref::text
      and c.test_ref           = d.test_ref::text
      and c.enforcement_class  = d.enforcement_class::text
      and c.installed          = d.installed
      and (not d.installed or c.verified_at is not null)
   where c.control_key is null;
  if drifted is not null then
    raise exception 'schema snapshot refused: exact reviewed control catalog is missing or drifted: %', drifted;
  end if;
end
$carr_control_catalog$;
VERIFY_TAIL
} > "$CONTROL_VERIFY_SQL"

# A DO block writes nothing to stdout, so a refusal appends no rows. It runs
# BEFORE the render below for that reason: render first and a drifted catalog
# would append half a file before anything raised.
if ! "$PSQL" -X -q -v ON_ERROR_STOP=1 -f "$CONTROL_VERIFY_SQL"
then
  rm -f "$CONTROL_VERIFY_SQL"
  echo "schema-snapshot: exact reviewed control catalog is missing or drifted — nothing written" >&2
  exit 1
fi

cat >> "$TMP" <<'CONTROL_CATALOG_HEADER'
-- CARR REVIEWED CONTROL CATALOG (bin/schema-snapshot.sh) — exact declared seed.
-- Every key here is declared in ops/config/rule-enforcement-map.json and its
-- companion class file, and the source was verified to hold all of them
-- immediately before this block was written. The following safely quoted rows
-- preserve the source verification and update timestamps.
-- never dump arbitrary ops.enforcement_control_catalog rows.
CONTROL_CATALOG_HEADER

if ! sed -e "s/__DECLARED_KEYS__/$DECLARED_KEYS/g" <<'CONTROL_CATALOG_ROWS' | "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP"
select format(
  'insert into ops.enforcement_control_catalog (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at,updated_at) values (%L,%L,%L,%L,%L,%L::timestamptz,%L::timestamptz) on conflict (control_key) do nothing;',
  control_key,implementation_ref,test_ref,enforcement_class,installed,
  to_char(verified_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  to_char(updated_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
  from ops.enforcement_control_catalog
 where control_key in (__DECLARED_KEYS__)
 order by control_key;
CONTROL_CATALOG_ROWS
then
  echo "schema-snapshot: could not render the reviewed control catalog — nothing written" >&2
  exit 1
fi

if [ "$RENEWAL_SOURCE_APPLIED" = t ]; then
cat >> "$TMP" <<'RENEWAL_SOURCE_JOB'
-- CARR RENEWAL SOURCE JOB (bin/schema-snapshot.sh) — exact disabled contract.
-- This is the one canonical 0230 definition needed to reconstruct its FK and
-- gate surface after 0230 has entered the migration ledger. It carries no run,
-- lease, client, party, deal, event, or credential data.
insert into ops.job_definition
  (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
   inventory_contract,recurrence,state_contract,routing_contract,
   filtering_contract,validation_contract,retry_policy,deduplication,
   completion_contract,legacy_schedule)
values
  ('renewal-radar-source-daily',1,false,'yellow','system','deterministic',
   '{"entrypoint":"ops.seal_renewal_decision_source_run","activation":"pending source-run adapter"}'::jsonb,
   '{"owner":"ops.job","inputs":["renewal-radar candidate import"],"canonical_writes":["ops.renewal_decision_source_run","ops.renewal_decision_source_run_member"]}'::jsonb,
   '{"cron":null,"timezone":"America/Chicago","source":"disabled pending source-run adapter"}'::jsonb,
   '{"owner":"ops.job","initial":"queued"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_complete"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.pool_imported"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_run_sealed"]}}'::jsonb,
   '{"max_attempts":2,"backoff":"exponential","base_seconds":60,"cap_seconds":600,"timeout_seconds":300}'::jsonb,
   '{"key_template":"renewal-radar-source-daily:{scheduled_for}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["renewal.source_run_sealed"]},"receipt_kind":"renewal_source_run"}'::jsonb,
   '{"provider":"none","status":"disabled","activation":"explicit source-run adapter required"}'::jsonb)
on conflict (key,version) do nothing;

RENEWAL_SOURCE_JOB
fi

if [ "$RULE_DELIVERY_APPLIED" = t ]; then
cat >> "$TMP" <<'RULE_DELIVERY_POLICY'
-- CARR RULE DELIVERY POLICY (bin/schema-snapshot.sh) — safe rebuild default.
-- The bounded vocabulary dump preserves an existing singleton. If the source
-- store lacks it, 0291 will not replay once its ledger row is in the snapshot,
-- so this fallback creates only the fail-safe shadow default.
insert into ops.rule_delivery_policy (singleton,mode,changed_by,reason)
values (true,'shadow','schema-snapshot',
        'Fresh rebuild default: scoped delivery remains shadow until governed cutover evidence exists.')
on conflict (singleton) do nothing;

RULE_DELIVERY_POLICY
fi

if [ "$RULE_DELIVERY_CUTOVER_APPLIED" = t ]; then
  # Preserve the exact ledger-visible postimage. A source with 0363 already
  # applied gets the current eight-row set; earlier ledgers keep the historical
  # nine-row preimages needed by their pending guarded transitions. 0471 adds
  # its control through the separate catalog source and does not mutate this
  # human-review-pinned rule map.
  if [ "$RULE_DELIVERY_DIGEST_REPIN_APPLIED" = t ]; then
cat >> "$TMP" <<'RULE_DELIVERY_ACTIVATION_TARGETS_POST_0363'
-- CARR RULE DELIVERY ACTIVATION TARGETS POST-0363 (bin/schema-snapshot.sh) — exact reviewed cutover config.
insert into ops.rule_delivery_activation_target
  (short_id,expected_scope,expected_pack,
   from_control,from_enforcement_class,from_implementation_ref,from_test_ref,
   to_control,to_enforcement_class,to_implementation_ref,to_test_ref,map_digest)
values
 ('25fcddee','shared','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904'),
 ('3fa17fa0','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904'),
 ('72e06bdf','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904'),
 ('113b3833','joe','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904'),
 ('57d13061','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904'),
 ('c66dc739','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904'),
 ('49533583','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904'),
 ('557838a5','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904')
on conflict (short_id) do nothing;

RULE_DELIVERY_ACTIVATION_TARGETS_POST_0363
  elif [ "$RULE_DELIVERY_REFRESH_APPLIED" = t ]; then
  if [ "$RULE_DELIVERY_RULESET_CONTROL_APPLIED" = t ]; then
cat >> "$TMP" <<'RULE_DELIVERY_ACTIVATION_TARGETS_POST_0348'
-- CARR RULE DELIVERY ACTIVATION TARGETS POST-0348 (bin/schema-snapshot.sh) — exact reviewed cutover config.
insert into ops.rule_delivery_activation_target
  (short_id,expected_scope,expected_pack,
   from_control,from_enforcement_class,from_implementation_ref,from_test_ref,
   to_control,to_enforcement_class,to_implementation_ref,to_test_ref,map_digest)
values
 ('25fcddee','shared','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('3fa17fa0','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('72e06bdf','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('581cb3fe','shared','delegation-council','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('113b3833','joe','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('57d13061','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('c66dc739','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('49533583','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'),
 ('557838a5','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218')
on conflict (short_id) do nothing;

RULE_DELIVERY_ACTIVATION_TARGETS_POST_0348
  else
cat >> "$TMP" <<'RULE_DELIVERY_ACTIVATION_TARGETS_POST_0332'
-- CARR RULE DELIVERY ACTIVATION TARGETS POST-0332 (bin/schema-snapshot.sh) — exact reviewed cutover config.
insert into ops.rule_delivery_activation_target
  (short_id,expected_scope,expected_pack,
   from_control,from_enforcement_class,from_implementation_ref,from_test_ref,
   to_control,to_enforcement_class,to_implementation_ref,to_test_ref,map_digest)
values
 ('25fcddee','shared','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('3fa17fa0','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('72e06bdf','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('581cb3fe','shared','delegation-council','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('113b3833','joe','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('57d13061','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('c66dc739','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('49533583','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997'),
 ('557838a5','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py','c0f3a9cc4fd407b346f44f09d7f05885051cfcc6c14c3f6c077e54a2a5448997')
on conflict (short_id) do nothing;

RULE_DELIVERY_ACTIVATION_TARGETS_POST_0332
  fi
  else
cat >> "$TMP" <<'RULE_DELIVERY_ACTIVATION_TARGETS_PRE_0332'
-- CARR RULE DELIVERY ACTIVATION TARGETS PRE-0332 (bin/schema-snapshot.sh) — exact reviewed cutover config.
insert into ops.rule_delivery_activation_target
  (short_id,expected_scope,expected_pack,
   from_control,from_enforcement_class,from_implementation_ref,from_test_ref,
   to_control,to_enforcement_class,to_implementation_ref,to_test_ref,map_digest)
values
 ('25fcddee','shared','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('3fa17fa0','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('72e06bdf','shared','client-deal','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('581cb3fe','shared','delegation-council','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('113b3833','joe','governance-rules','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('57d13061','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('c66dc739','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('49533583','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a'),
 ('557838a5','joe','joe-comms','session_boot','surfacing','hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js','command:python3 hooks/gate-integrity.py --selftest','pack_delivery','stop_gate','hooks/rule-pack-drift-gate.py','ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py','266ebb98076361b74cc2e22e5ea96380b2d3d1946b2d5d06b23ff349a5c98d9a')
on conflict (short_id) do nothing;

RULE_DELIVERY_ACTIVATION_TARGETS_PRE_0332
  fi
fi

# TWO GOVERNED EXECUTION SEEDS JOINED ON 2026-08-25, after 0309 and 0310
# entered the production ledger.  Neither is a vocabulary row and neither is
# safe to recover by dumping the whole table: execution-environment tables
# contain append-only provider evidence, while ops.job_definition contains
# live runtime contracts.  A snapshot that carries the ledger but omits these
# two bounded repository-declared rows cannot bind a fresh Work Request to the
# active Hermes local environment or admit an Engineering Passport slice.
#
# The values below are the exact reviewed built-in seed from 0309 and the
# final engineering-slice:v1 contract from 0310 through 0312.  They are deliberately
# rendered from repository text, with no production payload or observed
# timestamp copied into the tracked snapshot.  The fixed conformance time is
# a historical source observation, not a freshness claim; the provider's
# active state and passed conformance remain the authoritative admission facts.
cat >> "$TMP" <<'GOVERNED_EXECUTION_SEEDS'
-- CARR GOVERNED EXECUTION SEEDS (bin/schema-snapshot.sh) — exact bounded repository declarations.
-- 0309's protected hermes-local provider, its passed conformance, and its
-- complete lifecycle stream are required because 0309 is already in the
-- snapshot ledger and therefore will not replay its data block.
do $carr_governed_execution_seeds$
declare joe_id uuid; provider_id uuid; conformance_id uuid; base jsonb;
  manifest jsonb; manifest_digest text; run_digest text; observation jsonb;
  observed_at_value timestamptz;
begin
  select id into joe_id from public.actor where slug='joe' and kind='human' and active;
  if joe_id is null then raise exception 'schema snapshot requires active Joe actor for hermes-local seed'; end if;
  base := jsonb_build_object(
    'schema_version','execution-environment-provider.v1','provider_key','hermes-local','provider_version',1,
    'display_name','Hermes Local Terminal','source_class','built_in','backend_kind','local',
    'implementation_ref','hermes:tools.environments.local.LocalEnvironment',
    'implementation_digest','sha256:7d680c252bedc88ff7b80d50a5bfbdb9b926823d8bbc521f606e7b58237cbc1e',
    'capability_refs',jsonb_build_array('environment:exec','environment:filesystem','environment:process'),
    'operation_refs',jsonb_build_array('operation:create','operation:exec','operation:cancel','operation:destroy','operation:health'),
    'isolation_class','host_process','egress_policy_ref','egress:host-governed','secret_policy_ref','secrets:never-in-manifest',
    'persistence_mode','session_scoped','resource_policy_ref','resources:bounded-local-v1','cleanup_policy_ref','cleanup:process-tree-v1',
    'threat_model_ref','threat-model:local-trusted-input-v1','conformance_contract_ref','conformance:execution-environment-v1',
    'conformance_contract_digest','sha256:'||encode(public.digest('conformance:execution-environment-v1','sha256'),'hex'),
    'configuration_schema_digest','sha256:'||encode(public.digest('hermes:terminal.backend:local:v1','sha256'),'hex'),
    'package_provenance',jsonb_build_object('package_ref','package:nous-hermes-agent','package_digest','sha256:'||encode(public.digest('hermes-upstream:1bbb6e5bce56e721ab685af4cd87df21bbff4d35','sha256'),'hex'),'signature_ref','signature:upstream-git-commit','sbom_ref','sbom:hermes-installed-tree'),
    'collision_policy','protected_builtin','contains_secrets',false);
  manifest_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(base),'sha256'),'hex');
  manifest := base||jsonb_build_object('manifest_digest',manifest_digest);
  insert into ops.execution_environment_provider(provider_key,provider_version,source_class,backend_kind,manifest_digest,manifest,protected_builtin,created_by_actor_id,idempotency_key)
  values('hermes-local',1,'built_in','local',manifest_digest,manifest,true,joe_id,'03090000-0000-4000-8000-000000000001')
  returning id into provider_id;
  observed_at_value := '2026-08-25T00:00:00Z'::timestamptz;
  observation := jsonb_build_object(
    'schema_version','execution-environment-conformance.v1','provider_ref','environment-provider:hermes-local:v1',
    'manifest_digest',manifest_digest,'implementation_digest',manifest->>'implementation_digest',
    'package_digest',manifest->'package_provenance'->>'package_digest','package_revision_ref','git:706f33d42415d706b8f93dd299f4b317428e4a6b',
    'configuration_schema_digest',manifest->>'configuration_schema_digest','contract_ref',manifest->>'conformance_contract_ref',
    'contract_digest',manifest->>'conformance_contract_digest','run_ref','conformance-run:hermes-local-release-20260825',
    'status','passed','check_results',jsonb_build_object(
      'check:base-environment-contract-present',true,'check:cleanup-contract-declared',true,'check:hermes-version-exact',true,
      'check:implementation-digest-exact',true,'check:local-environment-present',true,'check:package-provenance-exact',true,
      'check:package-tree-clean',true,'check:source-secret-scan',true,'check:terminal-backend-local',true),
    'version_ref','Hermes Agent v0.20.5 (2026.8.19) · upstream 1bbb6e5b · local 706f33d4 (+1 carried commit)',
    'backend_kind','local','evidence_refs',jsonb_build_array('evidence:hermes-version-readback','evidence:terminal-backend-readback','evidence:installed-environment-contract'),
    'contains_secrets',false,'observed_at',to_jsonb(observed_at_value));
  run_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(observation-'observed_at'),'sha256'),'hex');
  observation := observation||jsonb_build_object('run_digest',run_digest);
  insert into ops.execution_environment_conformance(provider_id,contract_ref,contract_digest,run_ref,run_digest,manifest_digest,implementation_digest,package_digest,configuration_schema_digest,status,check_refs,evidence_refs,observation,observed_at,recorded_by_actor_id,idempotency_key)
  values(provider_id,manifest->>'conformance_contract_ref',manifest->>'conformance_contract_digest',observation->>'run_ref',run_digest,manifest_digest,manifest->>'implementation_digest',manifest->'package_provenance'->>'package_digest',manifest->>'configuration_schema_digest','passed',to_jsonb(array(select key from jsonb_each(observation->'check_results') order by key)),observation->'evidence_refs',observation,observed_at_value,joe_id,'03090000-0000-4000-8000-000000000002')
  returning id into conformance_id;
  insert into ops.execution_environment_provider_event(provider_id,from_state,to_state,evidence_refs,ruled_by_actor_id,idempotency_key) values
    (provider_id,null,'discovered',jsonb_build_array('evidence:tony-simons-terminal-provider-source'),joe_id,'03090000-0000-4000-8000-000000000003'),
    (provider_id,'discovered','quarantined',jsonb_build_array('evidence:provider-contract-review'),joe_id,'03090000-0000-4000-8000-000000000004'),
    (provider_id,'quarantined','conformance_passed',jsonb_build_array('evidence:test-execution-environment-unit'),joe_id,'03090000-0000-4000-8000-000000000005'),
    (provider_id,'conformance_passed','shadow',jsonb_build_array('evidence:hermes-local-existing-baseline'),joe_id,'03090000-0000-4000-8000-000000000006'),
    (provider_id,'shadow','canary',jsonb_build_array('evidence:hermes-local-config-readback'),joe_id,'03090000-0000-4000-8000-000000000007'),
    (provider_id,'canary','active',jsonb_build_array('evidence:joe-approved-provider-foundation'),joe_id,'03090000-0000-4000-8000-000000000008');
end
$carr_governed_execution_seeds$;

-- The exact enabled on-demand engineering-slice:v1 job contract through 0312.
-- A post-0312 snapshot marks 0311/0312 applied in its migration ledger, so a
-- fresh rebuild will not replay their contract updates.  This INSERT uses
-- ON CONFLICT DO NOTHING deliberately: it must therefore already be the final
-- sponsored, lease-bound controller declaration rather than the old 0310 row.
-- It remains the existing ops.job queue projection, not a second workflow or
-- task store.
insert into ops.job_definition
  (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
   inventory_contract,state_contract,routing_contract,filtering_contract,
   recurrence,validation_contract,retry_policy,deduplication,completion_contract,legacy_schedule)
values
  ('engineering-slice',1,true,'yellow','hermes','deterministic',
   '{"entrypoint":"mcp-server/src/engineering-runtime.js","export":"runEngineeringWorker","args":["room-bridge-engineering-controller"],"shadow_args":[],"canary":{"enabled":false,"reason":"fresh native Codex execution has no isolated canary adapter"}}'::jsonb,
   '{"trigger":"MCP admission only; no scheduler","owner":"ops.job dispatcher","inputs":["accepted Work Request","accepted plan revision","typed engineering slice"],"canonical_reads":["ops.work_request","ops.sourced_work_request_plan","ops.engineering_slice_plan","ops.job_definition"],"canonical_writes":["ops.job","ops.engineering_execution_envelope","ops.engineering_slice_receipt","ops.engineering_reviewer_fact"],"external_dependencies":["room-bridge lease-bound controller","Codex Desktop fresh-native-session adapter"],"authority":"server-derived sponsored Codex execution with a closed repository action allowlist; no caller-selected identity, authority, model, action, or native session","current_completion_signal":"lease-bound typed receipt plus independent reviewer fact","replacement_program":"ops.job_definition:engineering-slice:v1","acceptance":"typed envelope, receipt, dependency, and independent-review gates","retirement_approval":"Joe approval after replacement evidence"}'::jsonb,
   '{"states":["queued","running","succeeded","failed","timed_out"]}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["capability.candidate_admitted","runner.identity_bound"]},"description":"an accepted capability candidate and bound runner identity admit the slice"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["command.registered_args_selected"]},"description":"only the registered fresh Codex adapter is selected"}'::jsonb,
   '{"kind":"on_demand","schedule":null,"cron":null,"timezone":"America/Chicago","source":"MCP admit-engineering-slice only"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["command.exit_zero","command.workflow_marker_valid"]},"description":"the bounded adapter succeeds and returns its typed workflow marker"}'::jsonb,
   '{"max_attempts":2,"backoff":"constant","base_seconds":30,"cap_seconds":300,"timeout_seconds":1800}'::jsonb,
   '{"key_template":"engineering-slice:{plan_digest}:{work_request}:{slice_ref}:generation:{generation}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["command.receipt_persisted","command.execution_evidence_reconciles"]},"description":"lease-bound typed receipt persists and reconciles to the issued envelope","receipt_kind":"engineering_slice"}'::jsonb,
   '{"provider":"none","status":"disabled","disable_requires":"no scheduler exists; on-demand MCP admission only"}'::jsonb)
on conflict (key,version) do nothing;
GOVERNED_EXECUTION_SEEDS

# doctrine_meta is a singleton bootstrap rather than reference vocabulary: its
# live generation advances with successful doctrine commits and must never be
# copied into a tracked rebuild declaration.  A rebuilt database always starts
# from the canonical counter value, exactly as 0075 originally established.
cat >> "$TMP" <<'DOCTRINE_META'
--
-- CARR DOCTRINE META BOOTSTRAP (bin/schema-snapshot.sh) — canonical, not
-- production data.  A snapshot rebuild starts generation at zero.
--

insert into public.doctrine_meta (id, generation) values (1, 0);

DOCTRINE_META

# THE SIEP MANIFEST AND ITS WORK REQUESTS joined on 2026-08-27, the sixth entry
# in the seeded-configuration category above, and the first whose seed is SEALED.
# The trap is the control-catalog one a level deeper (incident INC-20260827-04's
# sibling, defect class snapshot-cannot-rebuild-sealed-seeded-manifest): 0324
# seeds ops.siep_package_contract / siep_program_dependency / siep_component_alias
# and then seals all three with unconditional before-insert triggers, and the
# package contract carries NOT NULL foreign keys into ops.work_request rows that
# 0324 itself creates. Once 0324 enters this snapshot's ledger nothing replays,
# the vocabulary dump cannot pass the seals, and a rebuilt database has SIEP
# structure with an empty, unseedable manifest — siep-program-local-pg-gate
# fails on the full package set (found live on the first post-0324 snapshot,
# pull request 712, closed unmerged).
#
# THE SHAPE CHOSEN, of the three recorded on WR-000016: a deterministic block
# that restores the exact production rows with the seals and the two
# work-request insert guards (plus the shape gate) disabled for the restore
# only, then re-enabled, then VERIFIED — the block ends by asserting
# ops.siep_manifest_digest() equals the digest read from production at snapshot
# time, so the seal's intent (no unreviewed manifest) survives the trip: a
# tampered block fails the rebuild instead of building quietly. The manifest
# rows are literal in reviewed migration 0324, so the production dump IS the
# reviewed content, and the digest proves it twice — here at write time and
# again at load time. The SIEP work requests are internal program rows created
# by 0324's own literal package list (requester joe, owner joe, no client,
# party, deal, event, doctrine or credential reference; their only outbound
# foreign key is public.actor, which this snapshot already carries).
# jsonb_populate_record keeps the block column-list-free so a later work_request
# column cannot silently rot it. The ref sequence is advanced to production's
# value so a rebuilt database cannot mint a colliding WR ref. Never widen this
# to siep_command_receipt, siep_lane_lock, or any other runtime/evidence table.
SIEP_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0324_siep_program_authority.sql')" \
  2>/dev/null)"
case "$SIEP_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the SIEP ledger state" >&2; exit 1 ;;
esac

if [ "$SIEP_APPLIED" = t ]; then
  SIEP_DIGEST="$("$PSQL" -Atqc "select ops.siep_manifest_digest()" 2>/dev/null)"
  case "$SIEP_DIGEST" in
    sha256:*) ;;
    *) echo "schema-snapshot: SIEP manifest digest unreadable — nothing written" >&2; exit 1 ;;
  esac
  SIEP_PKG_COUNT="$("$PSQL" -Atqc "select count(*) from ops.siep_package_contract" 2>/dev/null)"
  if [ -z "$SIEP_PKG_COUNT" ] || [ "$SIEP_PKG_COUNT" -lt 40 ]; then
    echo "schema-snapshot: SIEP package set implausibly small ($SIEP_PKG_COUNT) — nothing written" >&2
    exit 1
  fi

  cat >> "$TMP" <<'SIEP_HEADER'
-- CARR SIEP MANIFEST AND PROGRAM WORK REQUESTS (bin/schema-snapshot.sh) —
-- exact sealed rows, digest-verified below. The seals and the work-request
-- insert guards are disabled ONLY for this byte-exact restore of rows that
-- already passed them on production; the closing DO block refuses the whole
-- rebuild if the restored manifest does not hash to production's reviewed
-- digest. Never dump receipt, lock, or evidence tables here.
alter table ops.work_request disable trigger work_request_shape_gate;
alter table ops.work_request disable trigger work_in_progress_limit;
alter table ops.work_request disable trigger completion_capsule;
alter table ops.siep_package_contract disable trigger siep_package_contract_sealed_before_insert;
alter table ops.siep_program_dependency disable trigger siep_program_dependency_sealed_before_insert;
alter table ops.siep_component_alias disable trigger siep_component_alias_sealed_before_insert;
SIEP_HEADER

  if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP" <<'SIEP_ROWS'
select format('insert into ops.work_request select * from jsonb_populate_record(null::ops.work_request, %L::jsonb) on conflict (id) do nothing;', to_jsonb(w))
  from ops.work_request w
 where exists (select 1 from ops.siep_package_contract c where c.work_request_id = w.id)
 order by w.ref;
select format('insert into ops.siep_package_contract select * from jsonb_populate_record(null::ops.siep_package_contract, %L::jsonb) on conflict do nothing;', to_jsonb(c))
  from ops.siep_package_contract c order by c.package_key;
select format('insert into ops.siep_program_dependency select * from jsonb_populate_record(null::ops.siep_program_dependency, %L::jsonb) on conflict do nothing;', to_jsonb(d))
  from ops.siep_program_dependency d order by d.package_key, d.depends_on_package_key;
select format('insert into ops.siep_component_alias select * from jsonb_populate_record(null::ops.siep_component_alias, %L::jsonb) on conflict do nothing;', to_jsonb(a))
  from ops.siep_component_alias a order by a.alias_key;
select format('select pg_catalog.setval(%L, %s, true);', 'ops.work_request_ref_seq', last_value) from ops.work_request_ref_seq;
SIEP_ROWS
  then
    echo "schema-snapshot: could not render the SIEP manifest block — nothing written" >&2
    exit 1
  fi

  cat >> "$TMP" <<SIEP_FOOTER
alter table ops.siep_component_alias enable trigger siep_component_alias_sealed_before_insert;
alter table ops.siep_program_dependency enable trigger siep_program_dependency_sealed_before_insert;
alter table ops.siep_package_contract enable trigger siep_package_contract_sealed_before_insert;
alter table ops.work_request enable trigger completion_capsule;
alter table ops.work_request enable trigger work_in_progress_limit;
alter table ops.work_request enable trigger work_request_shape_gate;
do \$carr_siep_manifest\$
begin
  if ops.siep_manifest_digest() is distinct from '$SIEP_DIGEST' then
    raise exception 'SIEP manifest restore does not match the reviewed production digest $SIEP_DIGEST — refuse the rebuild';
  end if;
end
\$carr_siep_manifest\$;

SIEP_FOOTER
fi

# THE SCAC MUTATION REGISTRY is bounded, immutable security configuration.
# Once 0468 enters this snapshot's ledger, the seed chain no longer replays;
# omitting a ledger-visible successor would leave its exact lookups empty.
# Carry only the sealed version headers and their exact entry sets. Policy
# epochs, monitor receipts, token evidence, and other runtime state stay out.
SCAC_REGISTRY_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0468_siep18_forward_mutation_registry.sql')" \
  2>/dev/null)"
case "$SCAC_REGISTRY_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the SCAC registry ledger state" >&2; exit 1 ;;
esac

scac_select_registry() {
  SCAC_SELECTION="$(python3 "$REPO/ops/registry_chain.py" snapshot "$1")" || exit 1
  eval "$SCAC_SELECTION"
}

if [ "$SCAC_REGISTRY_APPLIED" = t ]; then
  if [ "$WR130_HOTFIX_REGISTRY_APPLIED" = t ]; then
    [ "$MODEL_ROLE_REGISTRY_APPLIED" = t ] || {
      echo "schema-snapshot: WR130 hotfix v40 is applied without v39" >&2
      exit 1
    }
    [ "$WR132_REGISTRY_APPLIED" = t ] && [ "$MODEL_ROLE_STORE_APPLIED" = t ] || {
      echo "schema-snapshot: WR130 hotfix v40 registry is applied without its v39, v38 and 0542 predecessors" >&2
      exit 1
    }
    scac_select_registry 40
    if [ "$B09_OUTCOME_REGISTRY_APPLIED" = t ]; then
      scac_select_registry 41
      if [ "$B09_ATOMIC_REPAIR_REGISTRY_APPLIED" = t ]; then
        scac_select_registry 42
        if [ "$JEV_PROCESS_REGISTRY_APPLIED" = t ]; then
          scac_select_registry 43
          if [ "$JEV_HOOK_ACTIVATION_REGISTRY_APPLIED" = t ]; then
            scac_select_registry 44
            if [ "$DEAL_CONFLICT_REGISTRY_APPLIED" = t ]; then
              scac_select_registry 45
              if [ "$DEAL_UNDO_REGISTRY_APPLIED" = t ]; then
                scac_select_registry 46
                if [ "$HERMES_DISPATCH_REGISTRY_APPLIED" = t ]; then
                  scac_select_registry 47
                  if [ "$RULE_DELIVERY_REGISTRY_APPLIED" = t ]; then
                    scac_select_registry 48
                    if [ "$MEETING_MODE_REGISTRY_APPLIED" = t ]; then
                      scac_select_registry 49
                      if [ "$MACHINE_ROLE_REGISTRY_APPLIED" = t ]; then
                        scac_select_registry 50
                        if [ "$SNAPSHOT_DEDUP_REGISTRY_APPLIED" = t ]; then
                          scac_select_registry 51
                          if [ "$JEV_HANDOFF_REGISTRY_APPLIED" = t ]; then
                            scac_select_registry 52
                            if [ "$OPENSSL_RESOLUTION_REGISTRY_APPLIED" = t ]; then
                              scac_select_registry 53
                              if [ "$MACHINE_PATHS_REGISTRY_APPLIED" = t ]; then
                                scac_select_registry 54
                                if [ "$LOGITECH_KEYMAP_OPTOUT_REGISTRY_APPLIED" = t ]; then
                                  scac_select_registry 55
                                  if [ "$CI_SPLIT_REGISTRY_APPLIED" = t ]; then
                                    scac_select_registry 56
                                    if [ "$TOUR_PROPERTY_REGISTRATION_REGISTRY_APPLIED" = t ]; then
                                      scac_select_registry 57
                                      if [ "$TOUR_REGISTRATION_ATOMIC_PAIR_REGISTRY_APPLIED" = t ]; then
                                        scac_select_registry 58
                                        if [ "$JEV_COMBINED_V59_REGISTRY_APPLIED" = t ]; then
                                          scac_select_registry 59
                                          if [ "$CALL_MODE_ORIGIN_REGISTRY_APPLIED" = t ]; then
                                            scac_select_registry 60
                                            if [ "$QUILL_SPEAKER_LABELS_REGISTRY_APPLIED" = t ]; then
                                              scac_select_registry 61
                                              if [ "$REQUIRED_ARGS_NULLABLE_REGISTRY_APPLIED" = t ]; then
                                                scac_select_registry 62
                                                if [ "$ANSWER_JOE_REGISTRY_APPLIED" = t ]; then
                                                  scac_select_registry 63
                                                  if [ "$ANSWER_JOE_ATOMIC_PAIR_REGISTRY_APPLIED" = t ]; then
                                                    scac_select_registry 64
                                                    if [ "$RESOURCE_OBSERVATION_REGISTRY_APPLIED" = t ]; then
                                                      scac_select_registry 65
                                                      if [ "$RESOURCE_OBSERVATION_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" = t ]; then
                                                        scac_select_registry 66
                                                        if [ "$TIMEBOMB_AUDIT_REGISTRY_APPLIED" = t ]; then
                                                          scac_select_registry 67
                                                          if [ "$RELEASE_PIPELINE_REGISTRY_APPLIED" = t ]; then
                                                            scac_select_registry 68
                                                            if [ "$JEV_CALL_RECEIPT_REGISTRY_APPLIED" = t ]; then
                                                              scac_select_registry 69
                                                              if [ "$JEV_CALL_RECEIPT_MIGRATE_PY_RESEAL_REGISTRY_APPLIED" = t ]; then
                                                                scac_select_registry 70
                                                                if [ "$NIGHTLY_EXPORTS_DAYTIME_RETRY_REGISTRY_APPLIED" = t ]; then
                                                                  scac_select_registry 71
                                                                  if [ "$V5_R02_REGISTRY_APPLIED" = t ]; then
                                                                    scac_select_registry 72
                                                                    if [ "$DEPLOY_WORKER_DO_MIGRATION_RESEAL_REGISTRY_APPLIED" = t ]; then
                                                                      scac_select_registry 73
                                                                      if [ "$JOURNEY_ONE_CLOCK_DOOR_REGISTRY_APPLIED" = t ]; then
                                                                        scac_select_registry 74
                                                                        if [ "$DELIVERY_CADENCE_A05_REGISTRY_APPLIED" = t ]; then
                                                                          scac_select_registry 75
                                                                          if [ "$GLOBAL_BOUNDARIES_DOOR_REGISTRY_APPLIED" = t ]; then
                                                                            scac_select_registry 76
                                                                            if [ "$F01_RECORD_SOURCE_AUTHORITY_REGISTRY_APPLIED" = t ]; then
                                                                              scac_select_registry 77
                                                                              if [ "$V5_SLICE_DONE_MARKER_REGISTRY_APPLIED" = t ]; then
                                                                                scac_select_registry 78
                                                                                if [ "$GOVERNED_CORRESPONDENCE_REGISTRY_APPLIED" = t ]; then
                                                                                  scac_select_registry 79
                                                                                  if [ "$CRE_LIFECYCLE_REGISTRY_APPLIED" = t ]; then
                                                                                    scac_select_registry 80
                                                                                    if [ "$AMEND_CLOSED_LOOP_REGISTRY_APPLIED" = t ]; then
                                                                                      scac_select_registry 81
                                                                                      if [ "$ACTION_CLASS_SUCCESSOR_REGISTRY_APPLIED" = t ]; then
                                                                                        scac_select_registry 82
                                                                                        if [ "$ASSURANCE_HEALTH_REGISTRY_APPLIED" = t ]; then
                                                                                          scac_select_registry 83
                                                                                          if [ "$V5_A03_REVIEW_REGISTRY_APPLIED" = t ]; then
                                                                                            scac_select_registry 84
             if [ "$V5_A02_RULE_ENFORCEMENT_REGISTRY_APPLIED" = t ]; then
               scac_select_registry 85
               if [ "$SESSION_TRACE_ARCHIVE_REGISTRY_APPLIED" = t ]; then
                 scac_select_registry 86
                 if [ "$F05_RULE_CONTEXT_REGISTRY_APPLIED" = t ]; then
                   scac_select_registry 87
                   if [ "$SALESFORCE_RECONCILIATION_RW02_REGISTRY_APPLIED" = t ]; then
                     scac_select_registry 88
                     if [ "$PROVISION_ENGINEERING_CONTROLLER_REGISTRY_APPLIED" = t ]; then
                       scac_select_registry 89
                       if [ "$RULE_BOOT_STANDING_CONTEXT_REGISTRY_APPLIED" = t ]; then
                         scac_select_registry 90
                         if [ "$SALESFORCE_RW02_SAFE_STOP_REGISTRY_APPLIED" = t ]; then
                           scac_select_registry 91
                           if [ "$INCIDENT_TRIAGE_REGISTRY_APPLIED" = t ]; then
                             scac_select_registry 92
                             if [ "$INDUSTRY_EVENTS_REGISTRY_APPLIED" = t ]; then
                               scac_select_registry 93
                               if [ "$BOARD_ANSWERS_REGISTRY_APPLIED" = t ]; then
                                 scac_select_registry 94
                                 if [ "$SCHEDULE_BOARD_REGISTRY_APPLIED" = t ]; then
                                   scac_select_registry 95
                                   if [ "$DOC_SUGGESTIONS_REGISTRY_APPLIED" = t ]; then
                                     scac_select_registry 96
                                     if [ "$CODEX_SESSION_READ_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 97
                                     if [ "$TOUR_FEEDBACK_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 98
                                     if [ "$PROPERTY_EVIDENCE_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 99
                                     if [ "$PROGRESS_DIRECTORY_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 100
                                     if [ "$WHATS_NEW_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 101
                                     if [ "$CONFIRM_MERGE_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 102
                                     if [ "$FIND_RULE_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 103
                                     if [ "$JEV_CAP_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 104
                                     if [ "$OBSERVATORY_ROOM_READ_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 105
                                     if [ "$LEAD_AUTOMATION_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 106
                                     if [ "$DOC_ACTIVITY_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 107
                                     if [ "$SYSTEM_WORK_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 108
                                     if [ "$RELATIONSHIP_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 109
                                     if [ "$INVOICE_TRACKER_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 110
                                     if [ "$AUTOMATION_UNDO_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 111
                                     if [ "$LEADS_REGISTRY_APPLIED" = t ]; then
                                       scac_select_registry 112
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                     fi
                                   fi
                                 fi
                               fi
                             fi
                           fi
                         fi
                       fi
                     fi
                   fi
                 fi
               fi
             fi
                                                                                          fi
                                                                                        fi
                                                                                      fi
                                                                                    fi
                                                                                  fi
                                                                                fi
                                                                              fi
                                                                            fi
                                                                          fi
                                                                        fi
                                                                      fi
                                                                    fi
                                                                  fi
                                                                fi
                                                              fi
                                                            fi
                                                          fi
                                                        fi
                                                      fi
                                                    fi
                                                  fi
                                                fi
                                              fi
                                            fi
                                          fi
                                        fi
                                      fi
                                    fi
                                  fi
                                fi
                              fi
                            fi
                          fi
                        fi
                      fi
                    fi
                  fi
                fi
              fi
            fi
          fi
        fi
      fi
    fi
  elif [ "$MODEL_ROLE_REGISTRY_APPLIED" = t ]; then
    [ "$WR132_REGISTRY_APPLIED" = t ] && [ "$MODEL_ROLE_STORE_APPLIED" = t ] || {
      echo "schema-snapshot: Model Room v39 registry is applied without its v38 and 0542 predecessors" >&2
      exit 1
    }
    scac_select_registry 39
  elif [ "$WR132_REGISTRY_APPLIED" = t ]; then
    [ "$WR130_REGISTRY_APPLIED" = t ] || {
      echo "schema-snapshot: WR132 v38 registry is applied without its v37 predecessor" >&2
      exit 1
    }
    scac_select_registry 38
  elif [ "$WR130_REGISTRY_APPLIED" = t ]; then
    [ "$WR130_ASSURANCE_APPLIED" = t ] && [ "$WR125_REGISTRY_APPLIED" = t ] && [ "$WR125_DOMAIN_APPLIED" = t ] || {
      echo "schema-snapshot: WR130 v37 registry is applied without its 0538 and WR125 predecessors" >&2
      exit 1
    }
    scac_select_registry 37
  elif [ "$WR125_REGISTRY_APPLIED" = t ]; then
    [ "$WR125_DOMAIN_APPLIED" = t ] || {
      echo "schema-snapshot: WR125 v36 registry is applied without its 0532a domain suffix" >&2
      exit 1
    }
    scac_select_registry 36
  elif [ "$DISPATCH_SPINE_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 35
    # Same one-behind rule as every branch below: the v34 full entry set became
    # readable only once 0532 sealed it, so this branch is where that seal is
    # first required. All 34 sealed histories are covered here.
  elif [ "$SESSION_IDENTITY_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 34
    # Same one-behind rule as every branch below: the v33 full entry set became
    # readable only once 0530 sealed it, so this branch is where that seal is
    # first required. All 33 sealed histories are covered here.
  elif [ "$NOTIFICATION_PREFERENCES_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 33
    # Same one-behind rule as every branch below: the v32 full entry set became
    # readable only once 0528 sealed it, so this branch is where that seal is
    # first required. All 32 sealed histories are covered here.
  elif [ "$DOC_CONVERSATION_LIST_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 32
    # Same one-behind rule as every branch below: the v31 full entry set became
    # readable only once 0526 sealed it, so this branch is where that seal is
    # first required. All 31 sealed histories are covered here.
  elif [ "$DOC_CONVERSATION_WRITE_DOORS_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 31
    # Same one-behind rule as every branch below: the v30 full entry set became
    # readable only once 0524 sealed it, so this branch is where that seal is
    # first required. All 30 sealed histories are covered here.
  elif [ "$PRODUCER_TRIO_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 30
    # Same one-behind rule as every branch below: the v29 full entry set became
    # readable only once 0522 sealed it, so this branch is where that seal is
    # first required. All 29 sealed histories are covered here.
  elif [ "$PROGRAM_CONTROLLER_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 29
    # Same one-behind rule as every branch below: the v28 full entry set became
    # readable only once 0518 sealed it, so this branch is where that seal is
    # first required. All 28 sealed histories are covered here.
  elif [ "$DEAL_FIELD_PROVENANCE_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 28
    # Same one-behind rule as every branch below: the v27 full entry set became
    # readable only once 0516 sealed it, so this branch is where that seal is
    # first required. All 27 sealed histories are covered here.
  elif [ "$FOUNDATION_ASSURANCE_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 27
  elif [ "$GATE_ZERO_OUTCOME_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 26
    # Same one-behind rule as every branch below: the v25 full entry set became
    # readable only once 0503 sealed it, so this branch is where that seal is
    # first required. All 25 sealed histories are covered here.
  elif [ "$V5_SCHEDULED_JOB_ADMISSION_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 25
    # Same one-behind rule as every branch below: the v24 full entry set became
    # readable only once 0501 sealed it, so this branch is where that seal is
    # first required. All 24 sealed histories are covered here.
  elif [ "$V5_F09_WORKFLOW_TRUTH_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 24
    # Same one-behind rule as every branch below: the v23 full entry set became
    # readable only once 0498 sealed it, so this branch is where that seal is
    # first required. All 23 sealed histories are covered here.
  elif [ "$R07_REPO_HYGIENE_JANITOR_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 23
    # Same one-behind rule as every branch below: the v22 full entry set became
    # readable only once 0497 sealed it, so this branch is where that seal is
    # first required. All 22 sealed histories are covered here.
  elif [ "$DOCTORCRE_PORTFOLIO_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 22
    # Same one-behind rule as every branch below: the v21 full entry set became
    # readable only once 0496 sealed it, so this branch is where that seal is
    # first required. All 21 sealed histories are covered here.
  elif [ "$R06_HOOKS_CORRECTNESS_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 21
    # A full entry set covers the DB-catalog-projected ACL rows as well as the
    # source rows, so it is only readable from a database that already applied
    # the migration: the seal file trails the frontier by one, and the v20 set
    # lands with the release that applies 0495.
  elif [ "$CONTINUITY_ARCHIVE_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 20
  elif [ "$INCIDENT_WORK_REQUEST_LINK_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 19
  elif [ "$SOURCED_SHAPE_FORWARD_CORRECTION_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 18
  elif [ "$BACKUP_GUARD_STATUS_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 17
  elif [ "$CODEX_COMPACTION_CHECKPOINT_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 16
  elif [ "$CLAUDE_CONFIG_PRESERVATION_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 15
  elif [ "$CLAUDE_ACTOR_HYDRATION_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 14
  elif [ "$CLAUDE_STARTUP_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 13
  elif [ "$CLAUDE_CONTINUITY_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 12
  elif [ "$CODEX_CONTINUITY_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 11
  elif [ "$SOURCE_MERGE_REGISTRY_APPLIED" = t ]; then
    scac_select_registry 10
    # +2 on 2026-09-01: the Repo Hygiene Program's R02 landing (Joe's ruling
    # 7f48abf6) adds two script entrypoints, hooks/canonical-edit-gate.py and
    # ops/untracked-anomaly-report.py, so v10's source set grows by two beyond
    # R05's base of 805. v1-v9 are sealed history and sum to 12660. WR-000048's
    # regeneration atom adds two more script entrypoints on top of main's #853
    # count (tools/run-breakglass.py, the tracked break-glass launcher, and
    # tools/migrate-prod-support.py, the migration door's receipt/escalation
    # command), so v10 current grows by two: 1456 -> 1458, source 812 -> 814,
    # total 12660 + 1458 = 14118. All three re-derived live from the generator.
  else
    scac_select_registry 9
  fi
  # The historical refusal arms retain their existing prerequisite checks.
  # Successors appended to the manifest select themselves from the same ledger.
  SCAC_APPLIED_MIGRATIONS="$("$PSQL" -Atqc 'select filename from schema_migrations order by filename')" || exit 1
  SCAC_SUCCESSOR_SELECTION="$(printf '%s\n' "$SCAC_APPLIED_MIGRATIONS" | python3 "$REPO/ops/registry_chain.py" successor-snapshot "$SCAC_CURRENT_NUMBER")" || exit 1
  eval "$SCAC_SUCCESSOR_SELECTION"
  case "$SCAC_EXPECTED_CURRENT_DIGEST$SCAC_EXPECTED_CURRENT_SOURCE_SET$SCAC_EXPECTED_CURRENT_CATALOG" in
    ''|*[!0-9a-f]*) echo "schema-snapshot: reviewed SCAC v${SCAC_CURRENT_NUMBER} runtime seals are malformed" >&2; exit 1 ;;
  esac
  [ "${#SCAC_EXPECTED_CURRENT_DIGEST}" -eq 64 ] &&
    [ "${#SCAC_EXPECTED_CURRENT_SOURCE_SET}" -eq 64 ] && [ "${#SCAC_EXPECTED_CURRENT_CATALOG}" -eq 64 ] || {
    echo "schema-snapshot: reviewed SCAC v${SCAC_CURRENT_NUMBER} source or catalog seal is malformed" >&2; exit 1
  }
  SCAC_EXPECTED_CURRENT_DIGEST="sha256:$SCAC_EXPECTED_CURRENT_DIGEST"
  SCAC_EXPECTED_CURRENT_SOURCE_SET="sha256:$SCAC_EXPECTED_CURRENT_SOURCE_SET"
  SCAC_EXPECTED_CURRENT_CATALOG="sha256:$SCAC_EXPECTED_CURRENT_CATALOG"
  SCAC_FULL_SET_SEALS="$REPO/ops/config/scac-registry-full-entry-set-seals.json"
  SCAC_FULL_SET_SQL="$(node -e '
    const fs=require("fs"); const seals=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));
    const count=Number(process.argv[2]); const current=Number(process.argv[3]);
    const allKeys=Array.from({length:current},(_,i)=>`scac-mutation-registry.v${i+1}`);
    const keys=allKeys.slice(0,count);
    if (Object.keys(seals).sort().join("|")!==allKeys.sort().join("|") ||
        !Number.isInteger(count) || !Number.isInteger(current) ||
        count<9 || count>current || current<10 ||
        allKeys.some(key=>!/^sha256:[0-9a-f]{64}$/.test(seals[key]))) process.exit(2);
    const quote=String.fromCharCode(39);
    const literal=value=>quote+String(value).replaceAll(quote,quote+quote)+quote;
    process.stdout.write(keys.map(key=>`(${literal(key)},${literal(seals[key])})`).join(","));
  ' "$SCAC_FULL_SET_SEALS" "$SCAC_FULL_SET_SEAL_COUNT" "$SCAC_CURRENT_NUMBER")" || {
    echo "schema-snapshot: immutable SCAC full-entry-set seals are unavailable or malformed" >&2; exit 1
  }
  # WHAT THIS CHECK ASKS, AND WHAT IT DELIBERATELY DOES NOT. Every comparison
  # below is about the registry being INTERNALLY CONSISTENT: the version rows,
  # their counts, their digests recomputed from their own entries, the sealed
  # entry sets, and the current version's row against the reviewed runtime file.
  # All of it is a property of the data, so it answers the same way on any
  # machine that applied the same migrations.
  #
  # ${SCAC_CURRENT_CATALOG_FUNCTION} USED TO BE ONE OF THESE ARMS AND IS NOT ANY
  # MORE. That function re-derives the catalog from the LIVE CLUSTER and also
  # carries the role-escalation guard, so it answers a different question — is
  # the cluster's privilege state clean RIGHT NOW — and roles are cluster-wide,
  # not database-scoped. The disposable-Postgres lane runs 52 acceptance
  # programs against one cluster before this point, and at least one of them
  # mints a probe role matching ^carr_ with an escalated grant on purpose, as
  # its own permanent mutation test. That made writing a schema snapshot depend
  # on the order the gates happened to run in, and on 2026-09-03 it failed the
  # database class while all twelve genuine consistency arms were true. Proven
  # by injection: creating one such role on a passing database flipped this arm
  # alone and reproduced the failure exactly; dropping the role restored it.
  #
  # THE ASSERTION IS NOT DELETED, it is left where it belongs and still fires in
  # two places that are about privilege rather than about snapshots: migration
  # 0471 raises from inside the database when the live catalog is not current,
  # and ops/siep18-reference-monitor-local-pg-gate.py asserts the function true
  # and then asserts it false under a deliberately escalated probe role. The
  # emitted snapshot below also keeps its own `not ${SCAC_CURRENT_CATALOG_FUNCTION}`
  # guard, so a snapshot still refuses to seed onto a cluster with drifted
  # privileges. Only the WRITE decision stopped asking a privilege question.
  SCAC_REGISTRY_EXACT="$("$PSQL" -Atqc \
    "select count(*)=$SCAC_VERSION_COUNT
       and array_agg(registry_version order by split_part(registry_version,'.v',2)::integer)=array[$SCAC_VERSION_ARRAY]::text[]
       and sum(entry_count)=$SCAC_TOTAL_ENTRY_COUNT
       and bool_and(entry_count=(select count(*) from ops.scac_mutation_registry_entry e
                                  where e.registry_version=v.registry_version))
       and bool_and(entry_set_digest=(select 'sha256:'||encode(public.digest(
             convert_to(coalesce(string_agg(e.entry_digest,',' order by e.ingress_key collate \"C\"),''),'UTF8'),
             'sha256'),'hex') from ops.scac_mutation_registry_entry e
             where e.registry_version=v.registry_version))
       and not exists(select 1 from (values $SCAC_FULL_SET_SQL) expected(registry_version,entry_set_digest)
         left join ops.scac_mutation_registry_version sealed using(registry_version)
         where sealed.entry_set_digest is distinct from expected.entry_set_digest)
       and not exists(select 1 from ops.scac_mutation_registry_entry e
         where e.entry_digest is distinct from 'sha256:'||encode(public.digest(
           convert_to(ops.scac_canonical_json(e.contract),'UTF8'),'sha256'),'hex'))
       and not exists(select 1 from unnest(array[$SCAC_HISTORICAL_ARRAY]) historical(registry_version)
         where not ops.scac_mutation_registry_seal_valid(historical.registry_version))
       and (select registry_digest='$SCAC_EXPECTED_CURRENT_DIGEST' and entry_count=$SCAC_CURRENT_ENTRY_COUNT and source_entry_count=$SCAC_CURRENT_SOURCE_COUNT
              from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v$SCAC_CURRENT_NUMBER')
       and (select 'sha256:'||encode(public.digest(convert_to(string_agg(e.entry_digest,',' order by e.entry_digest collate \"C\"),'UTF8'),'sha256'),'hex')='$SCAC_EXPECTED_CURRENT_SOURCE_SET'
              from ops.scac_mutation_registry_entry e where e.registry_version='scac-mutation-registry.v$SCAC_CURRENT_NUMBER'
                and e.ingress_kind not in ('db_function_acl','db_relation_acl','db_column_acl'))
       and (select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(v.catalog_projection),'UTF8'),'sha256'),'hex')='$SCAC_EXPECTED_CURRENT_CATALOG'
              from ops.scac_mutation_registry_version v where v.registry_version='scac-mutation-registry.v$SCAC_CURRENT_NUMBER')
     from ops.scac_mutation_registry_version v" 2>/dev/null)"
  [ "$SCAC_REGISTRY_EXACT" = t ] || {
    echo "schema-snapshot: SCAC v1-v${SCAC_CURRENT_NUMBER} registry is missing or internally drifted — nothing written" >&2
    # NAME THE ARM THAT FAILED. The check above is one long AND across nine
    # independent comparisons, so on failure it said only "drifted" and left
    # the reader to re-derive the whole reseal cascade blind. That cost two
    # rounds on 2026-09-03 and sent one session re-pinning a seal that was not
    # the cause. This re-evaluates the SAME comparisons one at a time and
    # prints each — it is diagnostic only and runs ONLY after the gate has
    # already decided to fail, so it cannot turn a red check green.
    "$PSQL" -Atqc \
      "select 'arm version_count            = '||(count(*)=$SCAC_VERSION_COUNT)::text
         ||E'\n'||'arm version_array           = '||(array_agg(registry_version order by split_part(registry_version,'.v',2)::integer)=array[$SCAC_VERSION_ARRAY]::text[])::text
         ||E'\n'||'arm total_entry_count       = '||(sum(entry_count)=$SCAC_TOTAL_ENTRY_COUNT)::text||' (observed '||sum(entry_count)||', expected $SCAC_TOTAL_ENTRY_COUNT)'
         ||E'\n'||'arm per_version_counts      = '||bool_and(entry_count=(select count(*) from ops.scac_mutation_registry_entry e where e.registry_version=v.registry_version))::text
         ||E'\n'||'arm entry_set_recompute     = '||bool_and(entry_set_digest=(select 'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(e.entry_digest,',' order by e.ingress_key collate \"C\"),''),'UTF8'),'sha256'),'hex') from ops.scac_mutation_registry_entry e where e.registry_version=v.registry_version))::text
         ||E'\n'||'arm sealed_entry_sets       = '||(not exists(select 1 from (values $SCAC_FULL_SET_SQL) expected(registry_version,entry_set_digest) left join ops.scac_mutation_registry_version sealed using(registry_version) where sealed.entry_set_digest is distinct from expected.entry_set_digest))::text
         ||E'\n'||'arm entry_digest_self       = '||(not exists(select 1 from ops.scac_mutation_registry_entry e where e.entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(e.contract),'UTF8'),'sha256'),'hex')))::text
         ||E'\n'||'arm historical_seals_valid  = '||(not exists(select 1 from unnest(array[$SCAC_HISTORICAL_ARRAY]) historical(registry_version) where not ops.scac_mutation_registry_seal_valid(historical.registry_version)))::text
         ||E'\n'||'arm current_version_row     = '||(select (registry_digest='$SCAC_EXPECTED_CURRENT_DIGEST' and entry_count=$SCAC_CURRENT_ENTRY_COUNT and source_entry_count=$SCAC_CURRENT_SOURCE_COUNT)::text||' (digest '||registry_digest||', entries '||entry_count||', source '||source_entry_count||')' from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v$SCAC_CURRENT_NUMBER')
         ||E'\n'||'arm current_source_set      = '||(select (('sha256:'||encode(public.digest(convert_to(string_agg(e.entry_digest,',' order by e.entry_digest collate \"C\"),'UTF8'),'sha256'),'hex'))='$SCAC_EXPECTED_CURRENT_SOURCE_SET')::text from ops.scac_mutation_registry_entry e where e.registry_version='scac-mutation-registry.v$SCAC_CURRENT_NUMBER' and e.ingress_kind not in ('db_function_acl','db_relation_acl','db_column_acl'))
         ||E'\n'||'arm current_catalog_digest  = '||(select (('sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(v2.catalog_projection),'UTF8'),'sha256'),'hex'))='$SCAC_EXPECTED_CURRENT_CATALOG')::text from ops.scac_mutation_registry_version v2 where v2.registry_version='scac-mutation-registry.v$SCAC_CURRENT_NUMBER')
         ||E'\n'||'live_catalog_fn (not an arm)= '||($SCAC_CURRENT_CATALOG_FUNCTION)::text
       from ops.scac_mutation_registry_version v" >&2 2>/dev/null ||
      echo "schema-snapshot: the per-arm breakdown could not be read" >&2
    "$PSQL" -Atqc \
      "select 'sealed entry-set mismatch '||expected.registry_version||': expected '||expected.entry_set_digest||', observed '||coalesce(sealed.entry_set_digest,'<missing>')
         from (values $SCAC_FULL_SET_SQL) expected(registry_version,entry_set_digest)
         left join ops.scac_mutation_registry_version sealed using(registry_version)
        where sealed.entry_set_digest is distinct from expected.entry_set_digest
        order by expected.registry_version" >&2 2>/dev/null ||
      echo "schema-snapshot: sealed entry-set mismatch details could not be read" >&2
    exit 1
  }

  cat >> "$TMP" <<SCAC_REGISTRY_HEADER

-- CARR SCAC MUTATION REGISTRY V1-V${SCAC_CURRENT_NUMBER} (bin/schema-snapshot.sh) — immutable,
-- internally digest-verified security configuration. The append-only triggers
-- are disabled only while restoring the exact sealed rows and re-enabled
-- before the closing verification block.
alter table ops.scac_mutation_registry_version disable trigger scac_mutation_registry_version_sealed;
alter table ops.scac_mutation_registry_entry disable trigger scac_mutation_registry_entry_sealed;
SCAC_REGISTRY_HEADER

  # EACH DISTINCT ENTRY IS WRITTEN ONCE (2026-09-23). Every sealed version
  # carries its complete entry set, so rendering each version's rows in full
  # repeated ~2,000 near-identical entries per seal: v1-v50 was 84,414 rows but
  # only 4,275 distinct entries, 109.6 MB of a 114.8 MB file, and the push of
  # the 0558 refresh was refused by GitHub's 100 MiB limit (GH001). An entry is
  # fully determined by its entry_digest apart from registry_version and
  # registered_at, and each version has one registered_at, so the file carries
  # a pool of distinct entries (grouped by the version that introduced them, so
  # a new seal appends lines rather than rewriting them) plus each version's
  # ordered digest list, and the restore joins them back into byte-identical
  # rows. Both premises are asserted below before anything is rendered; the
  # closing verification block still recomputes every count and digest from
  # the restored rows, so a lossy rendering cannot restore silently.
  if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP" <<'SCAC_REGISTRY_ROWS'
do $carr_scac_render_premises$
begin
  if exists(select 1 from ops.scac_mutation_registry_entry e group by e.entry_digest
            having count(distinct to_jsonb(e) - 'registry_version' - 'registered_at') > 1) then
    raise exception 'an SCAC entry_digest carries differing row content; the deduplicated rendering would be lossy';
  end if;
  if exists(select 1 from ops.scac_mutation_registry_entry e group by e.registry_version
            having count(distinct e.registered_at) > 1) then
    raise exception 'an SCAC registry version carries more than one registered_at; the deduplicated rendering would be lossy';
  end if;
end
$carr_scac_render_premises$;
select format(
  'insert into ops.scac_mutation_registry_version select * from jsonb_populate_recordset(null::ops.scac_mutation_registry_version, %L::jsonb) on conflict (registry_version) do nothing;',
  jsonb_agg(to_jsonb(v) order by v.registry_version collate "C"))
from ops.scac_mutation_registry_version v;
select 'create temporary table carr_scac_entry_pool (entry_digest text primary key, entry jsonb not null) on commit drop;';
with ordinal as (
  select v.registry_version,
         coalesce((regexp_match(v.registry_version, '\.v([0-9]+)$'))[1]::int, 0) as n
  from ops.scac_mutation_registry_version v
), first_seen as (
  select distinct on (e.entry_digest) e.entry_digest, e.ingress_key, o.n, o.registry_version,
         to_jsonb(e) - 'registry_version' - 'registered_at' as entry
  from ops.scac_mutation_registry_entry e join ordinal o using (registry_version)
  order by e.entry_digest, o.n, o.registry_version collate "C"
)
select format(
  'insert into pg_temp.carr_scac_entry_pool select p->>''entry_digest'', p from jsonb_array_elements(%L::jsonb) p;',
  jsonb_agg(f.entry order by f.ingress_key collate "C", f.entry_digest collate "C"))
from first_seen f
group by f.n, f.registry_version order by f.n, f.registry_version collate "C";
select format(
  'insert into ops.scac_mutation_registry_entry select r.* from jsonb_array_elements_text(%L::jsonb) with ordinality d(entry_digest, n) join pg_temp.carr_scac_entry_pool p using (entry_digest) cross join lateral jsonb_populate_record(null::ops.scac_mutation_registry_entry, p.entry || %L::jsonb) r order by d.n on conflict (registry_version,ingress_key) do nothing;',
  jsonb_agg(e.entry_digest order by e.ingress_key collate "C"),
  jsonb_build_object('registry_version', e.registry_version, 'registered_at', min(e.registered_at)))
from ops.scac_mutation_registry_entry e
group by e.registry_version order by e.registry_version;
select 'drop table pg_temp.carr_scac_entry_pool;';
SCAC_REGISTRY_ROWS
  then
    echo "schema-snapshot: could not render the exact SCAC registry — nothing written" >&2
    exit 1
  fi

  cat >> "$TMP" <<SCAC_REGISTRY_FOOTER
-- Settle every deferred policy-epoch event raised by the ledger/configuration
-- restore while the exact registry rows are present and the sealed triggers
-- are still disabled. Re-enabling first makes settlement depend on COMMIT
-- internals and can reject a valid portable rebuild.
set constraints all immediate;
alter table ops.scac_mutation_registry_entry enable trigger scac_mutation_registry_entry_sealed;
alter table ops.scac_mutation_registry_version enable trigger scac_mutation_registry_version_sealed;
do \$carr_scac_registry\$
begin
  if not (select count(*)=${SCAC_VERSION_COUNT} and sum(entry_count)=${SCAC_TOTAL_ENTRY_COUNT} and
      bool_and(entry_count=(select count(*) from ops.scac_mutation_registry_entry e
                            where e.registry_version=v.registry_version)) and
      bool_and(entry_set_digest=(select 'sha256:'||encode(public.digest(
        convert_to(coalesce(string_agg(e.entry_digest,',' order by e.ingress_key collate "C"),''),'UTF8'),
        'sha256'),'hex') from ops.scac_mutation_registry_entry e
        where e.registry_version=v.registry_version))
    from ops.scac_mutation_registry_version v) then
    raise exception 'restored SCAC v1-v${SCAC_CURRENT_NUMBER} registry is incomplete or digest-drifted';
  end if;
  if exists(select 1 from (values ${SCAC_FULL_SET_SQL}) expected(registry_version,entry_set_digest)
       left join ops.scac_mutation_registry_version sealed using(registry_version)
       where sealed.entry_set_digest is distinct from expected.entry_set_digest) then
    raise exception 'restored SCAC registry does not match immutable full-entry-set seals';
  end if;
  if exists(select 1 from unnest(array[${SCAC_HISTORICAL_ARRAY}]) historical(registry_version)
       where not ops.scac_mutation_registry_seal_valid(historical.registry_version)) or
     not (select registry_digest='${SCAC_EXPECTED_CURRENT_DIGEST}' and entry_count=${SCAC_CURRENT_ENTRY_COUNT} and source_entry_count=${SCAC_CURRENT_SOURCE_COUNT}
            from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v${SCAC_CURRENT_NUMBER}') or
     not (select 'sha256:'||encode(public.digest(convert_to(string_agg(e.entry_digest,',' order by e.entry_digest collate "C"),'UTF8'),'sha256'),'hex')='${SCAC_EXPECTED_CURRENT_SOURCE_SET}'
            from ops.scac_mutation_registry_entry e where e.registry_version='scac-mutation-registry.v${SCAC_CURRENT_NUMBER}'
              and e.ingress_kind not in ('db_function_acl','db_relation_acl','db_column_acl')) or
     not (select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(v.catalog_projection),'UTF8'),'sha256'),'hex')='${SCAC_EXPECTED_CURRENT_CATALOG}'
            from ops.scac_mutation_registry_version v where v.registry_version='scac-mutation-registry.v${SCAC_CURRENT_NUMBER}') or
     not ${SCAC_CURRENT_CATALOG_FUNCTION} or
     exists(select 1 from ops.scac_mutation_registry_entry e
       where e.entry_digest is distinct from 'sha256:'||encode(public.digest(
         convert_to(ops.scac_canonical_json(e.contract),'UTF8'),'sha256'),'hex')) then
    raise exception 'restored SCAC registry failed exact historical, current, or per-entry contract seals';
  end if;
end
\$carr_scac_registry\$;

SCAC_REGISTRY_FOOTER
fi

# A truncated dump is the failure mode that matters: pg_dump has lost a Neon
# connection mid-stream before (2026-08-07, on the nightly backup). A short file
# that parses is worse than no file, because it would silently define a smaller
# database. Require the terminator pg_dump writes last.
if ! grep -q 'PostgreSQL database dump complete' "$TMP"; then
  echo "schema-snapshot: dump has no completion marker — treating as truncated, nothing written" >&2
  exit 1
fi

# Normalise the two things pg_dump varies between identical dumps, so --check
# reports STRUCTURE drift and nothing else. A check that cries on every run is a
# check people stop reading.
#   * the version banner, which moves whenever the client or server is upgraded;
#   * \restrict / \unrestrict, which carry a fresh RANDOM token per dump (a psql
#     restore guard). Left in, every single check would report the file stale
#     while the schema was byte-identical — which is exactly how a drift check
#     gets ignored and then removed.
sed -e '/^-- Dumped from database version/d' \
    -e '/^-- Dumped by pg_dump version/d' \
    -e '/^\\restrict /d' \
    -e '/^\\unrestrict /d' "$TMP" | normalise_eof > "$TMP.clean"
mv "$TMP.clean" "$TMP"

# THE DOCTRINE VALIDATION REGISTRY — the seventh instance, and the first one the
# check below found by being WRONG rather than by being silent. It sat in the
# classification as "runtime evidence", which it is not: doctrine_gate_check is
# the registry itself, and 0075's own comment states the contract — "A NEW GATE
# IS A FUNCTION AND A ROW". Results live in doctrine_gate_finding.
#
# WHAT A REBUILD LOST. 0075 seeds 11 rows, every one severity=block and enabled,
# and nothing writes the table at runtime. runGates() in mcp-server/src/doctrine.js
# selects the enabled checks and treats an empty set as nothing to enforce, so a
# database rebuilt from a snapshot carrying 0075's ledger row but not its rows ran
# NO doctrine gates at all and let every write through — silently, because zero
# findings is indistinguishable from zero problems.
#
# Rendered from the source rather than hand-listed, and column-list-free so a
# later column cannot rot the block.
cat >> "$TMP" <<'DOCTRINE_GATE_CHECK_HEADER'

-- CARR DOCTRINE VALIDATION REGISTRY (bin/schema-snapshot.sh) — the gate rows
-- themselves, not their findings. Without these a rebuilt database enforces no
-- doctrine gates and says nothing about it.
DOCTRINE_GATE_CHECK_HEADER

if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP" <<'DOCTRINE_GATE_CHECK_ROWS'
select format(
  'insert into public.doctrine_gate_check select * from jsonb_populate_record(null::public.doctrine_gate_check, %L::jsonb) on conflict (check_key) do nothing;',
  to_jsonb(g)) from public.doctrine_gate_check g order by g.check_key;
DOCTRINE_GATE_CHECK_ROWS
then
  echo "schema-snapshot: could not render the doctrine validation registry — nothing written" >&2
  exit 1
fi

# THE NAMED AGENT PROFILE ROSTER — the eighth instance, and the second found by
# the classification being WRONG rather than silent. It sat excluded on the claim
# that its readers tolerate an empty set. They do not: bot-brief throws
# profile_not_found and its hint says "new profiles are a migration", and nothing
# inserts the table at runtime — agent-profiles.js only UPDATEs. A rebuild
# without these rows fails the bot brief for every named profile.
cat >> "$TMP" <<'AGENT_PROFILE_HEADER'

-- CARR NAMED AGENT PROFILES (bin/schema-snapshot.sh) — the seeded roster. No
-- runtime path creates these; a rebuild without them breaks the bot brief.
AGENT_PROFILE_HEADER

if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP" <<'AGENT_PROFILE_ROWS'
select format(
  'insert into public.agent_profile select * from jsonb_populate_record(null::public.agent_profile, %L::jsonb) on conflict (profile_key) do nothing;',
  to_jsonb(p)) from public.agent_profile p order by p.profile_key;
AGENT_PROFILE_ROWS
then
  echo "schema-snapshot: could not render the named agent profiles — nothing written" >&2
  exit 1
fi

# 0532a makes two small, migration-owned ownership control tables. The stale
# fence is the closed predecessor deny-list and the issuer-generation table is
# the two-principal active/draining selector. Neither has a runtime writer that
# may be reconstructed from business evidence; once their migration joins the
# ledger a fresh rebuild needs the exact bounded rows to preserve the ownership
# refusal boundary and its authenticated issuer route.
if [ "$CANONICAL_OWNERSHIP_ACTIVATION_APPLIED" = t ]; then
  CANONICAL_OWNERSHIP_SEEDS_EXACT="$("$PSQL" -Atqc \
    "select
       (select count(*) = 2
          and count(*) filter (where generation=1 and work_request_ref='WR-000122'
            and plan_ref='PLAN-954b03dd464d-v1'
            and plan_hash='sha256:954b03dd464dab9986bc705d01fdb197edd29b23ad10ca673da53ad0ac03c27d'
            and migration_filenames=array['0535_ready_plan_amendment.sql','0536_ready_plan_amendment_scac_successor.sql']::text[]
            and reason='Superseded through WR-000125 and its accepted WR-000126 replacement authority; historical rows remain exact but are permanently non-executable.'
            and successor_allowed=false) = 1
          and count(*) filter (where generation=2 and work_request_ref='WR-000120'
            and plan_ref='PLAN-339b2976b387-v1'
            and plan_hash='sha256:339b2976b3876fe56291f0e7ba6732d8448b7539779f4d71a2e1891f2a2ad5ca'
            and migration_filenames=array['0533_read_doc_outcome_cards.sql','0534_read_doc_outcome_cards_scac_successor.sql']::text[]
            and reason='Frozen predecessor plan must return only through a separately proposed, reviewed, and accepted same-request successor; its burned filenames are permanently non-executable.'
            and successor_allowed=true) = 1
        from ops.engineering_stale_contract_fence)
       and
       (select count(*) = 2
          and count(*) filter (where principal='carr_ownership_issuer_g1'
            and generation=1 and state='active') = 1
          and count(*) filter (where principal='carr_ownership_issuer_g2'
            and generation=2 and state='draining') = 1
        from ops.canonical_ownership_issuer_generation)" 2>/dev/null)"
  [ "$CANONICAL_OWNERSHIP_SEEDS_EXACT" = t ] || {
    echo "schema-snapshot: canonical ownership seed rows are missing or drifted — nothing written" >&2
    exit 1
  }
  cat >> "$TMP" <<'CANONICAL_OWNERSHIP_SEEDS_HEADER'

-- CARR CANONICAL OWNERSHIP SEEDS (bin/schema-snapshot.sh) — 0532a's exact
-- stale-contract fences and two issuer-generation rows, not runtime sessions.
CANONICAL_OWNERSHIP_SEEDS_HEADER
  if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP" <<'CANONICAL_OWNERSHIP_SEEDS_ROWS'
select format(
  'insert into ops.engineering_stale_contract_fence select * from jsonb_populate_record(null::ops.engineering_stale_contract_fence, %L::jsonb) on conflict (generation) do nothing;',
  to_jsonb(f))
  from ops.engineering_stale_contract_fence f
 order by f.generation;
select format(
  'insert into ops.canonical_ownership_issuer_generation select * from jsonb_populate_record(null::ops.canonical_ownership_issuer_generation, %L::jsonb) on conflict (principal) do nothing;',
  to_jsonb(g))
  from ops.canonical_ownership_issuer_generation g
 order by g.generation;
CANONICAL_OWNERSHIP_SEEDS_ROWS
  then
    echo "schema-snapshot: could not render canonical ownership seeds — nothing written" >&2
    exit 1
  fi
fi

# THE COMPLETION DEFAULT POLICY is bounded internal configuration, not runtime
# evidence. Migration 0431 seeds exactly one versioned row. Once its ledger
# entry enters this snapshot, the migration no longer replays; omitting the row
# leaves every completion subject without a policy and the projection empty.
# Carry only that exact row. Subjects, observations, relations, dispositions,
# and receipts remain runtime evidence and are never dumped here.
COMPLETION_REGISTER_APPLIED="$("$PSQL" -Atqc \
  "select exists (select 1 from schema_migrations where filename='0431_completion_register_schema.sql')" \
  2>/dev/null)"
case "$COMPLETION_REGISTER_APPLIED" in
  t|f) ;;
  *) echo "schema-snapshot: could not read the completion-register ledger state" >&2; exit 1 ;;
esac

if [ "$COMPLETION_REGISTER_APPLIED" = t ]; then
  COMPLETION_POLICY_EXACT="$("$PSQL" -Atqc \
    "select count(*) = 1 and bool_and(
       organization_tenant_id = 'carr-internal'
       and id = '00000000-0000-4000-8000-000000000431'::uuid
       and policy_key = 'completion-default'
       and policy_version = 1
       and capability_class = 'default'
       and required_dimensions = array[
         'canonical_owner','intended_consumer','workflow_trigger',
         'retrieval_admission','enforcement_closure','operator_surface',
         'telemetry','canonical_implementation','activation','live_readback','rollback'
       ]::text[]
       and default_freshness = interval '24 hours'
       and state_precedence = array[
         'conflicting','canceled','superseded','unknown_stale','blocked','planned',
         'built_unmerged','merged_unactivated','active_unproven',
         'partially_built','operational'
       ]::text[]
       and effective_at = '2026-08-25T00:00:00Z'::timestamptz
       and created_at = '2026-08-25T00:00:00Z'::timestamptz
     ) from ops.completion_policy" 2>/dev/null)"
  [ "$COMPLETION_POLICY_EXACT" = t ] || {
    echo "schema-snapshot: completion default policy is missing or drifted — nothing written" >&2
    exit 1
  }

  cat >> "$TMP" <<'COMPLETION_POLICY_HEADER'

-- CARR COMPLETION DEFAULT POLICY (bin/schema-snapshot.sh) — one exact,
-- source-verified configuration row. Runtime completion evidence is excluded.
COMPLETION_POLICY_HEADER
  if ! "$PSQL" -X -Atq -v ON_ERROR_STOP=1 >> "$TMP" <<'COMPLETION_POLICY_ROW'
select format(
  'select set_config(''carr.organization_tenant_id'', %L, false);%sinsert into ops.completion_policy select * from jsonb_populate_record(null::ops.completion_policy, %L::jsonb) on conflict (organization_tenant_id, policy_key, policy_version) do nothing;%sreset carr.organization_tenant_id;',
  organization_tenant_id, chr(10), to_jsonb(p), chr(10))
  from ops.completion_policy p
 where organization_tenant_id = 'carr-internal'
   and policy_key = 'completion-default'
   and policy_version = 1;
COMPLETION_POLICY_ROW
  then
    echo "schema-snapshot: could not render the completion default policy — nothing written" >&2
    exit 1
  fi
fi

# THE SIXTH INSTANCE WAS CAUGHT BY HAND; THE SEVENTH IS CAUGHT HERE. Every block
# above this line was written one at a time, each after a database rebuilt from
# this file failed a db-gate days later: the role preamble, the control catalog,
# the guidance registry and retrieval seeds, the rule-delivery policy and
# targets, the governed execution providers, and now the sealed SIEP manifest.
# Six blocks, one shape — a migration seeds rows, this snapshot's ledger absorbs
# the migration so it never replays, and the rows are simply gone. Until now
# nothing would have noticed the next one.
#
# It runs HERE, against the composed artifact and before the write/--check
# branch, for two reasons. The artifact is the only place holding both halves of
# the question — the applied-migration ledger and the data statements — so the
# check reads the exact bytes about to be committed rather than asking
# production a second question whose answer could differ. And refusing before
# the write means a rejected run leaves db/schema.sql untouched.
#
# IT NEVER ADDS A TABLE. What a table carries into a tracked file stays a
# reviewed decision, per the rule this file already lives by: a table qualifies
# because someone read its rows and can say what is in them, never because a
# migration seeded it. All this does is refuse to let an unclassified one pass
# in silence.
cat >> "$TMP" <<'SNAPSHOT_DATA_RESTORE_COMMIT'

-- CARR SNAPSHOT DATA RESTORE TRANSACTION COMMIT
COMMIT;

SNAPSHOT_DATA_RESTORE_COMMIT

if ! "$CATALOG_PY" "$REPO/ops/snapshot-seed-coverage.py" "$REPO" "$TMP"; then
  exit 1
fi

# Several bounded configuration blocks are appended after pg_dump's random
# tokens and banners are stripped above. Normalize EOF again only after the
# complete artifact exists, so the final generated file—not an intermediate
# prefix—owns the one-newline invariant.
normalise_eof < "$TMP" > "$TMP.clean"
mv "$TMP.clean" "$TMP"

if [ "$VERIFY_ONLY" = "1" ]; then
  echo "schema snapshot: disposable candidate valid; tracked snapshot unchanged"
  exit 0
fi

if [ -n "$OUTPUT_CANDIDATE" ]; then
  # Open with noclobber for the write itself, so a path created after argument
  # validation cannot be overwritten or followed through a symlink.
  if ! (set -C; cat "$TMP" > "$OUTPUT_CANDIDATE") 2>/dev/null; then
    echo "schema-snapshot: could not create disposable candidate at a new path" >&2
    exit 1
  fi
  echo "wrote disposable schema candidate $OUTPUT_CANDIDATE ($(wc -l < "$OUTPUT_CANDIDATE" | tr -d ' ') lines; tracked snapshot unchanged)"
  exit 0
fi

if [ "$CHECK" = "1" ]; then
  if [ ! -f "$OUT" ]; then
    echo "schema-snapshot: $OUT does not exist — run bin/schema-snapshot.sh" >&2
    exit 1
  fi
  # The snapshot preserves the live work-request allocator so a rebuild remains
  # monotonic, but ordinary Work Request creation advances it independently of
  # schema. Ignore only that exact statement's decimal current value. The
  # comparator requires one exact statement in each file and compares every
  # other byte, so a missing line or altered sequence/call shape still refuses.
  if "$CATALOG_PY" - "$CURRENTNESS_PY" "$OUT" "$TMP" <<'CURRENTNESS_PYTHON'
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("schema_snapshot_currentness", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit(1)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raise SystemExit(0 if module.snapshots_match(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])) else 1)
CURRENTNESS_PYTHON
  then
    echo "schema snapshot: current"
    exit 0
  fi
  echo "schema-snapshot: db/schema.sql is STALE — production's structure has moved." >&2
  echo "Regenerate and commit it: bin/schema-snapshot.sh" >&2
  diff "$OUT" "$TMP" | head -40 >&2
  exit 1
fi

mkdir -p "$REPO/db"
cp "$TMP" "$OUT"
echo "wrote $OUT ($(wc -l < "$OUT" | tr -d ' ') lines: structure + migration ledger + reference vocabulary; no business data)"
