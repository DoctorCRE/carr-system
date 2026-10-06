#!/usr/bin/env python3
"""Static seeded checks for the typed Guidance Registry migration."""
from __future__ import annotations

import os
import re
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(REPO, "migrations", "0168_guidance_registry.sql")
STANDING_CONTEXT_BOUNDARY_MIGRATION = os.path.join(
    REPO, "migrations", "0199_guidance_standing_context_boundary.sql"
)
READER_BOUNDARY_MIGRATION = os.path.join(
    REPO, "migrations", "0382_standing_guidance_reader_boundary.sql"
)


def main() -> int:
    if not os.path.exists(MIGRATION):
        print("guidance-registry-migration-selftest: FAIL — migration 0168 is missing", file=sys.stderr)
        return 1
    sql = open(MIGRATION, encoding="utf-8").read().lower()
    compact = re.sub(r"\s+", " ", sql)
    boundary_sql = open(STANDING_CONTEXT_BOUNDARY_MIGRATION, encoding="utf-8").read().lower()
    boundary_compact = re.sub(r"\s+", " ", boundary_sql)
    if not os.path.exists(READER_BOUNDARY_MIGRATION):
        print("guidance-registry-migration-selftest: FAIL — migration 0382 is missing", file=sys.stderr)
        return 1
    reader_sql = open(READER_BOUNDARY_MIGRATION, encoding="utf-8").read().lower()
    reader_compact = re.sub(r"\s+", " ", reader_sql)
    checks = {
        "canonical item spine": "create table ops.guidance_item" in compact,
        "append-only revisions": "create table ops.guidance_revision" in compact
            and "guidance_revision_append_only" in compact,
        "deterministic append order": compact.count("generated always as identity unique") >= 3
            and "order by le.event_seq desc" in compact
            and "m.mapping_seq desc" in compact
            and "order by ge.event_seq desc" in compact,
        "stable rule provenance": "source_rule_id" in compact
            and "references rule(id) on delete restrict" in compact,
        "exact receipt/revision binding": "create table ops.guidance_authority_binding" in compact
            and "guidance_revision_id" in compact and "authority_receipt_id" in compact
            and "contract_hash" in compact
            and "guidance_revision_contract_hash" in compact,
        "lifecycle binding is exact": "validate_guidance_lifecycle_event" in compact,
        "mapping binding is exact": "validate_guidance_situation_mapping" in compact,
        "split identity": "source_clause" in compact and "is_primary" in compact
            and "split_group_id" in compact,
        "seven-type vocabulary": all(
            f"'{kind}'" in compact for kind in (
                "constraint", "procedure", "doctrine", "rubric",
                "preference", "precedent", "example")),
        "typed revision validator": "validate_guidance_revision" in compact,
        "rule-backed constitution and precedent":
            "constitution and precedent revisions require a source_rule_id" in compact
            and "new.is_constitution or new.guidance_type='precedent'" in compact,
        "constraint evidence is installed": "constraint revision requires an installed enforcement point" in compact,
        "constraint projection": "v_guidance_constraint" in compact,
        "procedure projection": "v_guidance_procedure" in compact,
        "doctrine retrieval bridge": "v_guidance_doctrine_retrieval" in compact
            and "doctrine_concept_mapping" in compact,
        "rubric projection": "v_guidance_rubric" in compact,
        "preference projection": "v_guidance_preference" in compact,
        "precedent projection": "v_guidance_precedent" in compact,
        "example projection": "v_guidance_example" in compact,
        "standing-context projection": "standing_guidance" in compact,
        "standing-guidance reader boundary is pinned security definer":
            "create or replace function ops.standing_guidance" in reader_compact
            and "security definer" in reader_compact
            and "set search_path = pg_catalog, ops, public, pg_temp" in reader_compact,
        "standing-guidance reader boundary fully qualifies dependencies": all(
            dependency in reader_compact for dependency in (
                "from ops.v_guidance_current",
                "join public.rule",
                "join public.actor teacher",
                "left join public.actor owner",
                "from ops.v_guidance_registry_state",
                "join ops.guidance_registry",
                "from ops.applicable_rules",
            )),
        "standing-guidance reader boundary preserves narrow execute grants":
            "revoke all on function ops.standing_guidance(text,text,text,text) from public"
            in reader_compact
            and "grant execute on function ops.standing_guidance(text,text,text,text) to carr_reader,carr_writer"
            in reader_compact,
        "coverage gate": "assert_guidance_registry_coverage" in compact,
        "standing corpus excludes intro-politics surface":
            boundary_compact.count(
                "coalesce(scope->>'kind','') <> 'intro_politics'"
            ) >= 3
            and "standing-context active rules" in boundary_compact,
        "guarded activation": "activate_guidance_registry" in compact
            and "between 5 and 10" in compact,
        "human authority lifecycle": "record_guidance_decision" in compact
            and "authority_actor_slug()" in compact
            and "to carr_authority" in compact,
        "registry activation cannot bypass gate":
            "guidance_registry_event to carr_writer" not in compact
            and "activate_guidance_registry(uuid,text,text,text) from public,carr_writer" in compact,
        "registry activation mints its own session-bound receipt":
            "p_idempotency_key text" in compact
            and "authority_actor_slug()" in compact
            and "insert into ops.authority_receipt" in compact
            and "registry_owner <> authority_actor" in compact
            and "pg_advisory_xact_lock" in compact
            and "guidance registry activation" in compact,
        "authority functions deny public execute":
            "record_guidance_decision(uuid,text,text,text) from public,carr_writer" in compact
            and "activate_guidance_situation_mapping(uuid,uuid,text) from public,carr_writer" in compact,
        "writer cannot mint lifecycle authority":
            "grant insert on ops.guidance_item,ops.guidance_revision, ops.guidance_authority_binding" not in compact,
        "no bulk rule mutation": "update rule " not in compact
            and "delete from rule" not in compact,
        "no implicit rule backfill": "insert into ops.guidance_item" not in compact
            or " from rule" not in compact,
    }
    failed = [name for name, passed in checks.items() if not passed]
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    if failed:
        print("guidance-registry-migration-selftest: failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    print(f"guidance-registry-migration-selftest: {len(checks)} contract checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
