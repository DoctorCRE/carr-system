#!/usr/bin/env python3
"""Acceptance checks for the one ordered AI capability portfolio.

Tier 1 runs in every CI job without credentials. Tier 2 activates only when
DATABASE_URL is already supplied (normally through db-tap against isolated
staging) and rolls every mutation back.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration


import os
import pathlib
import re
import json

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = REPO / "migrations" / "0125_ai_capability_program.sql"
REPRIORITIZATION = REPO / "migrations" / "0129_reprioritize_rag_benchmark.sql"
FAILED: list[str] = []
APPROVED_TITLES = [
    "LLM evaluation harness", "Structured-output parser", "Function-calling router",
    "Guardrails system", "AI gateway", "RAG pipeline", "Agent loop / ReAct",
    "Data-curation and deduplication pipeline", "Synthetic-data generator",
    "Knowledge-graph builder", "Semantic router", "Prompt caching",
    "Code-interpreter sandbox", "Text-to-SQL", "Graph RAG", "Vector database / HNSW",
    "Embedding model", "Adversarial-attack generator", "Whisper-style ASR",
    "Text-to-speech pipeline", "Small language model", "Inference server",
    "Quantization library", "Feature store", "Recommendation system", "Vector database driver",
    "Reasoner / Chain-of-Thought implementation", "Interpretability / SAE tooling",
    "LoRA trainer", "PEFT library", "Model-distillation pipeline", "DPO loss",
    "RLHF / PPO pipeline", "Model merger", "KV-cache paging", "Speculative decoding",
    "Tokenizer", "Transformer", "Vision Transformer", "Multimodal projector / CLIP",
    "Diffusion model", "Audio Spectrogram Transformer", "Logit processor",
    "State Space Model / Mamba", "Mixture-of-Experts routing layer",
    "Distributed training / FSDP / tensor parallelism", "Autograd engine",
    "Matrix multiplication kernel", "Softmax optimization", "FlashAttention CUDA kernel",
    "Neural Architecture Search",
]


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def tier1() -> None:
    print("TIER 1 — static program and authority contract")
    sql = read_migration(MIGRATION.parent.parent, MIGRATION.name)
    rows = re.findall(
        r"\(\s*(\d+)\s*,\s*'carr-ai-engineering-suite-v1'\s*,\s*'(WR-AI-\d+)'\s*,\s*'([^']+)'\s*,\s*'(build|extend|adopt|decline)'\s*,[\s\S]*?'(\{[^']+\})'::jsonb,\s*'joe',\s*'joe'\)",
        sql,
    )
    check("exactly 51 canonical Work Requests are seeded", len(rows) == 51, str(len(rows)))
    check("ordinals are contiguous 1..51", [int(r[0]) for r in rows] == list(range(1, 52)))
    check("refs are contiguous and stable", [r[1] for r in rows] == [f"WR-AI-{n:03d}" for n in range(1, 52)])
    check("all 51 approved titles retain their exact usefulness order",
          [r[2] for r in rows] == APPROVED_TITLES)
    check("the current projection cannot skip an unfinished predecessor",
          "p.program_ordinal < w.program_ordinal" in sql and "p.state <> 'confirmed_closed'" in sql)
    check("scheduled jobs receive read but not mutation grants",
          "grant select on ops.v_capability_program_next to carr_reader, carr_writer, carr_jobs" in sql
          and "grant update" not in sql.lower().split("carr_jobs")[-1])
    required_context = ["scope", "non_goals", "prerequisites", "first_deliverable",
                        "rollback_exit", "data_risk", "effort", "completion_definition"]
    parsed_contexts = []
    for sequence, ref, _title, _disposition, context in rows:
        try:
            parsed_contexts.append((sequence, ref, json.loads(context)))
        except json.JSONDecodeError as exc:
            check(f"{ref} has valid project context JSON", False, str(exc))
    expected_keys = set(required_context + ["evidence"])
    for sequence, ref, context in parsed_contexts:
        check(f"{ref} has the exact session context shape", set(context) == expected_keys,
              f"keys={sorted(context)}")
        check(f"{ref} has non-placeholder build context",
              all(isinstance(context.get(key), str) and context[key].strip()
                  for key in ["scope", "first_deliverable", "rollback_exit", "data_risk", "effort", "completion_definition"]),
              str(context))
        check(f"{ref} has list-shaped constraints and evidence",
              all(isinstance(context.get(key), list) for key in ["non_goals", "prerequisites", "evidence"]),
              str(context))

    reorder_sql = read_migration(REPRIORITIZATION.parent.parent, REPRIORITIZATION.name)
    expected_refs = ["WR-AI-006"] + [f"WR-AI-{n:03d}" for n in range(1, 6)] + [f"WR-AI-{n:03d}" for n in range(7, 52)]
    proof_match = re.search(r"expected text\[\] := array\[([\s\S]*?)\];", reorder_sql)
    effective_refs = re.findall(r"'(WR-AI-\d+)'", proof_match.group(1)) if proof_match else []
    check("RAG is reprioritized first without losing or renaming another project", effective_refs == expected_refs,
          str(effective_refs))
    check("reprioritization refuses any in-flight program",
          "non_ready <> 0 or sessions <> 0" in reorder_sql
          and "lock table ops.capability_agent_session in share row exclusive mode" in reorder_sql)
    check("ordinal guard is restored inside the same transaction",
          "disable trigger capability_program_identity_guard_before_update" in reorder_sql
          and "enable trigger capability_program_identity_guard_before_update" in reorder_sql
          and reorder_sql.rstrip().endswith("commit;"))

    # ── THE BUILD-DISCIPLINE LATCH (2026-08-21) ─────────────────────────────
    # capability-program said completed=0/51 and a session read that as "nothing
    # is built" and started a rebuild of work already on main. completed counts
    # confirmed_closed attestations only — not code on disk. The verb must say so
    # so the number cannot be misread as build truth.
    cp_src = (REPO / "mcp-server" / "src" / "capability-program.js").read_text(encoding="utf-8")
    check("capability-program returns landed_in_repo: null",
          "landed_in_repo: null" in cp_src,
          "the Worker cannot stat the repo; null is the honest value")
    check("capability-program returns built_unclosed: []",
          "built_unclosed: []" in cp_src,
          "the Worker cannot detect disk-landed work; [] is the honest default")
    check("capability-program hint names the local detection path",
          "Run ops/built_unclosed.py" in cp_src and "CLOSE-BEFORE-BUILD" in cp_src
          and "completed counts confirmed_closed only" in cp_src,
          "the hint must say completed counts confirmed_closed only and name CLOSE-BEFORE-BUILD")
    check("sessionBrief first line is the build-discipline warning",
          "This card may already have code" in cp_src
          and "completed!=closed is not permission to rebuild" in cp_src
          and "Run ops/built_unclosed.py before writing" in cp_src,
          "sessionBrief must open with the CLOSE-BEFORE-BUILD warning")


def tier2() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("TIER 2 — SKIP (DATABASE_URL not set; run through staging db-tap)")
        return
    print("TIER 2 — live isolated schema, all writes rolled back")
    try:
        import psycopg
    except ImportError as exc:
        check("psycopg is installed", False, str(exc))
        return

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*), min(program_ordinal), max(program_ordinal) from ops.work_request where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            summary = cur.fetchone()
            assert summary is not None
            count, first, last = summary
            check("store contains the complete ordered program", (count, first, last) == (51, 1, 51), str((count, first, last)))

            cur.execute("select program_ordinal, ref from ops.v_capability_program_next where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            check("the RAG benchmark is the sole initial head", cur.fetchall() == [(1, "WR-AI-006")])

            cur.execute("select has_table_privilege('carr_jobs','ops.v_capability_program_next','SELECT'), has_table_privilege('carr_jobs','ops.work_request','UPDATE')")
            privileges = cur.fetchone()
            check("scheduled role can read the projection but cannot update Work Requests",
                  privileges == (True, False), str(privileges))

            cur.execute("select count(*) from ops.work_request where program_key=%s and not (project_context ?& array['scope','non_goals','prerequisites','first_deliverable','rollback_exit','data_risk','effort','completion_definition'])",
                        ("carr-ai-engineering-suite-v1",))
            incomplete = cur.fetchone()
            assert incomplete is not None
            check("all stored project contexts are build-complete", incomplete[0] == 0)

            # Prove both halves of the database boundary with rolled-back
            # evidence: an arbitrary direct close is refused, while a frozen
            # candidate plus an independent stored pass exposes exactly one
            # successor. Nothing from this proof persists.
            cur.execute("savepoint queue_handoff")
            cur.execute("savepoint arbitrary_close")
            arbitrary_close_refused = False
            try:
                cur.execute("""
                    update ops.work_request
                       set state='confirmed_closed', completion_kind='extended',
                           completion_evidence='{"selftest":true}'::jsonb,
                           verification_evidence_ref='selftest:verifier', closed_at=now()
                     where program_key=%s and program_ordinal=1
                """, ("carr-ai-engineering-suite-v1",))
            except psycopg.Error:
                arbitrary_close_refused = True
                cur.execute("rollback to savepoint arbitrary_close")
            if not arbitrary_close_refused:
                cur.execute("rollback to savepoint arbitrary_close")
            check("direct SQL cannot close the queue with invented evidence", arbitrary_close_refused)

            cur.execute("""
                select w.id,
                       (select id from actor where slug='system' and active),
                       (select id from actor where slug='joe' and active)
                  from ops.work_request w
                 where w.program_key=%s and w.program_ordinal=1
            """, ("carr-ai-engineering-suite-v1",))
            identities = cur.fetchone()
            assert identities is not None
            work_request_id, executor_id, verifier_id = identities
            check("staging has distinct active actors for the evidence proof",
                  bool(executor_id and verifier_id and executor_id != verifier_id))

            cur.execute("""
                insert into ops.capability_agent_session
                  (work_request_id, executor_actor_id, created_by_actor_id,
                   source_commit_sha, worktree_ref)
                values (%s,%s,%s,%s,%s)
                returning id
            """, (work_request_id, executor_id, verifier_id, "a" * 40, "selftest-worktree"))
            session_row = cur.fetchone()
            assert session_row is not None
            session_id = session_row[0]
            cur.execute("update ops.capability_agent_session set state='in_progress', started_at=now(), version=version+1 where id=%s", (session_id,))
            candidate = {"artifact_ref": "selftest:artifact", "candidate_commit_sha": "b" * 40,
                         "acceptance_test_refs": ["selftest:acceptance"]}
            cur.execute("""
                update ops.capability_agent_session
                   set state='verification', candidate_kind='extended',
                       candidate_evidence=%s::jsonb, prepared_at=now(), version=version+1
                 where id=%s returning candidate_fingerprint
            """, (json.dumps(candidate), session_id))
            fingerprint_row = cur.fetchone()
            assert fingerprint_row is not None
            fingerprint = fingerprint_row[0]
            cur.execute("update ops.work_request set state='verification' where id=%s", (work_request_id,))
            cur.execute("""
                insert into ops.capability_verification
                  (build_session_id, work_request_id, verifier_actor_id, outcome,
                   verification_evidence_ref, source_ref, candidate_fingerprint)
                values (%s,%s,%s,'pass','selftest:acceptance','selftest:staging',%s)
                returning id
            """, (session_id, work_request_id, verifier_id, fingerprint))
            pass_row = cur.fetchone()
            assert pass_row is not None
            pass_id = pass_row[0]
            cur.execute("""
                update ops.work_request
                   set state='confirmed_closed', verification_accepted_at=now(),
                       verification_evidence_ref=%s, closed_at=now(), completion_kind='extended',
                       completion_evidence=jsonb_build_object('candidate', %s::jsonb)
                 where id=%s
            """, (str(pass_id), json.dumps(candidate), work_request_id))
            cur.execute("select program_ordinal from ops.v_capability_program_next where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            check("an independently verified close exposes exactly project 2", cur.fetchall() == [(2,)])
            cur.execute("""
                update ops.capability_agent_session
                   set state='completed', completed_at=now(), version=version+1
                 where id=%s
            """, (session_id,))

            def refused_after_savepoint(label: str, statement: str, params: tuple[object, ...]) -> None:
                cur.execute("savepoint immutable_probe")
                refused = False
                try:
                    cur.execute(statement, params)
                except psycopg.Error:
                    refused = True
                cur.execute("rollback to savepoint immutable_probe")
                check(label, refused)

            refused_after_savepoint(
                "closed completion evidence cannot be rewritten",
                "update ops.work_request set completion_evidence='{}'::jsonb where id=%s",
                (work_request_id,),
            )
            refused_after_savepoint(
                "a closed capability project cannot be reopened",
                "update ops.work_request set state='verification', closed_at=null where id=%s",
                (work_request_id,),
            )
            refused_after_savepoint(
                "verification attestations cannot be rewritten",
                "update ops.capability_verification set note='forged' where id=%s",
                (pass_id,),
            )
            refused_after_savepoint(
                "a completed session candidate kind cannot be rewritten",
                "update ops.capability_agent_session set candidate_kind='built' where id=%s",
                (session_id,),
            )
            refused_after_savepoint(
                "a completed capability session is fully immutable",
                "update ops.capability_agent_session set completed_at=now() + interval '1 second' where id=%s",
                (session_id,),
            )
            cur.execute("rollback to savepoint queue_handoff")
            cur.execute("select program_ordinal from ops.v_capability_program_next where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            check("rollback restores project 1 as head", cur.fetchall() == [(1,)])
        conn.rollback()


if __name__ == "__main__":
    tier1()
    tier2()
    if FAILED:
        print(f"\nFAILED: {len(FAILED)}")
        sys.exit(1)
    print("\nPASS: capability program contract")
