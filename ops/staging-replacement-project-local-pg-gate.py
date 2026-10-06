#!/usr/bin/env python3
"""Rollback-only DB gate for the clean staging replacement evidence contract."""

# ci: db-gate
# doctrine: runbook

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any, NoReturn
from urllib.parse import urlparse

import psycopg


def refuse(message: str) -> NoReturn:
    raise RuntimeError(message)


def one(cur: psycopg.Cursor[Any]) -> Any:
    row = cur.fetchone()
    if row is None:
        refuse("database returned no row")
    return row[0]


def rejects(cur: psycopg.Cursor[Any], statement: str, params: tuple[Any, ...] = ()) -> bool:
    cur.execute("savepoint staging_replacement_refusal")
    try:
        cur.execute(statement, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint staging_replacement_refusal")
        return True
    cur.execute("rollback to savepoint staging_replacement_refusal")
    return False


def ledger_digest(rows: list[tuple[str, str]]) -> str:
    material = b"".join(
        filename.encode("utf-8") + b"\0" + sha256.encode("utf-8") + b"\n"
        for filename, sha256 in sorted(rows, key=lambda row: row[0].encode("utf-8"))
    )
    return "sha256:" + hashlib.sha256(material).hexdigest()


def session(cur: psycopg.Cursor[Any], role: str) -> None:
    cur.execute(f"set session authorization {role}")


def owner(cur: psycopg.Cursor[Any]) -> None:
    cur.execute("reset session authorization")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "127.0.0.1", "localhost", "::1",
    }:
        refuse("staging replacement gate requires an explicit loopback DATABASE_URL")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("begin")
            cur.execute("select rolsuper from pg_roles where rolname=current_user")
            if one(cur) is not True:
                refuse("staging replacement gate requires a disposable local superuser")
            cur.execute(
                """do $$ begin
                     if not exists(select 1 from pg_roles
                                    where rolname='carr_program5_forward_fix_verifier') then
                       create role carr_program5_forward_fix_verifier nologin;
                     end if;
                   end $$"""
            )
            cur.execute(
                "grant carr_program5_forward_fix_verifiers "
                "to carr_program5_forward_fix_verifier"
            )
            cur.execute("select id from actor where slug='joe'")
            actor_id = one(cur)
            cur.execute(
                """insert into public.party(kind,name,created_by,updated_by)
                   values('org','Synthetic clean staging fixture',%s,%s)""",
                (actor_id, actor_id),
            )
            cur.execute(
                """select (select count(*) from public.party)
                         +(select count(*) from public.client)
                         +(select count(*) from public.deal)
                         +(select count(*) from public.lead)
                         +(select count(*) from public.vendor)"""
            )
            synthetic_count = one(cur)
            cur.execute(
                "select filename,sha256 from public.schema_migrations "
                'order by filename collate "C"'
            )
            rows = list(cur.fetchall())
            ledger: dict[str, str] = dict(rows)
            highest = rows[-1][0]
            digest = ledger_digest(rows)
            idem = uuid.uuid4()
            contract = {
                "schema_version": "clean-staging-replacement-contract.v1",
                "tree_mode": "full",
                "git_sha": "1" * 40,
                "source_tree_oid": "2" * 40,
                "source_tree_sha256": "sha256:" + "3" * 64,
                "source_tree_entry_count": 700,
                "artifact_sha256": "sha256:" + "4" * 64,
                "config_sha256": "sha256:" + "5" * 64,
                "dependency_sha256": "sha256:" + "6" * 64,
                "migration_ledger": ledger,
                "migration_count": len(rows),
                "migration_highest": highest,
                "migration_ledger_sha256": digest,
                "prior_staging_project_id": "old-staging-12345678",
                "replacement_project_id": "clean-staging-87654321",
                "replacement_branch_id": "br-clean-staging-12345678",
                "replacement_endpoint_id": "ep-clean-staging-12345678",
                "expected_synthetic_data_count": synthetic_count,
                "expected_production_overlap_count": 0,
            }
            observation = {
                "schema_version": "clean-staging-replacement-observation.v1",
                "git_sha": contract["git_sha"],
                "source_tree_oid": contract["source_tree_oid"],
                "source_tree_sha256": contract["source_tree_sha256"],
                "source_tree_entry_count": contract["source_tree_entry_count"],
                "artifact_sha256": contract["artifact_sha256"],
                "config_sha256": contract["config_sha256"],
                "dependency_sha256": contract["dependency_sha256"],
                "prior_staging_project_id": contract["prior_staging_project_id"],
                "replacement_project_id": contract["replacement_project_id"],
                "replacement_branch_id": contract["replacement_branch_id"],
                "replacement_endpoint_id": contract["replacement_endpoint_id"],
                "synthetic_data_count": synthetic_count,
                # Cross-project comparison is supplied by the governed controller.
                "production_overlap_count": 0,
            }

            session(cur, "carr_jobs")
            prepared = one(cur.execute(
                "select ops.prepare_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(contract)),
            ))
            replay = one(cur.execute(
                "select ops.prepare_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(contract)),
            ))
            if prepared["replayed"] is not False or replay["replayed"] is not True:
                refuse("prepare is not exact-replay idempotent")
            changed = dict(contract, config_sha256="sha256:" + "9" * 64)
            if not rejects(
                cur, "select ops.prepare_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(changed)),
            ):
                refuse("changed preparation replay was accepted")
            bounded = dict(contract, tree_mode="bounded-prefix")
            if not rejects(
                cur, "select ops.prepare_staging_replacement_project(%s,%s::jsonb)",
                (uuid.uuid4(), json.dumps(bounded)),
            ):
                refuse("bounded migration tree was accepted")
            unknown = dict(contract, held_back_migrations=[])
            if not rejects(
                cur, "select ops.prepare_staging_replacement_project(%s,%s::jsonb)",
                (uuid.uuid4(), json.dumps(unknown)),
            ):
                refuse("unknown contract key was accepted")
            production = dict(contract, replacement_project_id="steep-field-48688294")
            if not rejects(
                cur, "select ops.prepare_staging_replacement_project(%s,%s::jsonb)",
                (uuid.uuid4(), json.dumps(production)),
            ):
                refuse("Production project ID was accepted as replacement")
            if not rejects(cur, "update ops.staging_replacement_project_contract set tree_mode='full'"):
                refuse("carr_jobs has direct contract-table mutation")
            owner(cur)
            session(cur, "carr_writer")
            if not rejects(
                cur, "select ops.prepare_staging_replacement_project(%s,%s::jsonb)",
                (uuid.uuid4(), json.dumps(contract)),
            ):
                refuse("carr_writer prepared replacement evidence")

            owner(cur)
            fake_name = "9999_gate_extra.sql"
            cur.execute(
                "insert into public.schema_migrations(filename,sha256) values(%s,%s)",
                (fake_name, "a" * 64),
            )
            session(cur, "carr_program5_forward_fix_verifier")
            if not rejects(
                cur, "select ops.record_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(observation)),
            ):
                refuse("extra live migration was accepted")
            owner(cur)
            cur.execute("delete from public.schema_migrations where filename=%s", (fake_name,))
            altered_name, altered_sha = rows[0]
            cur.execute(
                "update public.schema_migrations set sha256=%s where filename=%s",
                ("b" * 64, altered_name),
            )
            session(cur, "carr_program5_forward_fix_verifier")
            if not rejects(
                cur, "select ops.record_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(observation)),
            ):
                refuse("changed live migration content was accepted")
            owner(cur)
            cur.execute(
                "update public.schema_migrations set sha256=%s where filename=%s",
                (altered_sha, altered_name),
            )
            cur.execute("delete from public.schema_migrations where filename=%s", (altered_name,))
            session(cur, "carr_program5_forward_fix_verifier")
            if not rejects(
                cur, "select ops.record_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(observation)),
            ):
                refuse("missing live migration was accepted")
            owner(cur)
            cur.execute(
                "insert into public.schema_migrations(filename,sha256) values(%s,%s)",
                (altered_name, altered_sha),
            )

            session(cur, "carr_program5_forward_fix_verifier")
            overlap = dict(observation, production_overlap_count=1)
            if not rejects(
                cur, "select ops.record_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(overlap)),
            ):
                refuse("nonzero Production overlap was accepted")
            wrong_count = dict(observation, synthetic_data_count=synthetic_count + 1)
            if not rejects(
                cur, "select ops.record_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(wrong_count)),
            ):
                refuse("caller synthetic count differing from DB was accepted")
            receipt = one(cur.execute(
                "select ops.record_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(observation)),
            ))
            receipt_replay = one(cur.execute(
                "select ops.record_staging_replacement_project(%s,%s::jsonb)",
                (idem, json.dumps(observation)),
            ))
            if receipt["replayed"] is not False or receipt_replay["replayed"] is not True:
                refuse("record is not exact-replay idempotent")
            readback = one(cur.execute(
                "select ops.read_staging_replacement_project_receipt(%s)",
                (receipt["receipt_id"],),
            ))
            if readback["live_migration_ledger"] != ledger \
                    or readback["synthetic_data_count"] != synthetic_count \
                    or readback["production_overlap_count"] != 0:
                refuse("receipt readback omitted exact evidence")
            owner(cur)
            if not rejects(
                cur, "update ops.staging_replacement_project_receipt set observed_at=observed_at"
            ):
                refuse("append-only receipt trigger allowed an owner update")
            session(cur, "carr_jobs")
            jobs_readback = one(cur.execute(
                "select ops.read_staging_replacement_project_receipt(%s)",
                (receipt["receipt_id"],),
            ))
            if jobs_readback["evidence_ref"] != receipt["evidence_ref"]:
                refuse("carr_jobs receipt projection differs from verifier projection")
            owner(cur)
            conn.rollback()
    print("staging-replacement-project-local-pg-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
