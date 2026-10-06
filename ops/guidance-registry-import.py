#!/usr/bin/env python3
"""Dry-run-first operational importer for the reviewed typed Guidance Registry.

This is deliberately not an authority path.  Its default mode compiles the
checked-in enforcement map and reviewed TSV, resolves source-rule UUIDs from
the *ambient* DATABASE_URL read-only, validates an explicit doctrine mapping
plan, and renders the exact canonical activation-manifest preimage.  `--apply`
only stages then applies that reviewed digest through a separately authenticated
CARR_DB_WRITER_URL connection to the same live database; it cannot decide a
batch or activate/deactivate a registry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import psycopg


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib import guidance_registry as registry
from lib.reviewed_artifact import ReviewedArtifactError, assert_head_committed
DEFAULT_MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"
DEFAULT_MANIFEST = REPO / "audits" / "guidance-migration-manifest.v1.tsv"
DEFAULT_CURATION_REVIEW = REPO / "audits" / "guidance-situation-curation-review.v1.json"
MAPPING_PLAN_SCHEMA = "guidance-situation-mapping-plan/v1"
WRITER_ROLE = "carr_writer"


class ImportRefusal(ValueError):
    """A reviewable precondition failed before any writer call."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_mapping_plan(path: Path) -> dict[str, list[dict[str, str]]]:
    try:
        for artifact, relative in (
            (DEFAULT_CURATION_REVIEW,
             "audits/guidance-situation-curation-review.v1.json"),
            (DEFAULT_MANIFEST, "audits/guidance-migration-manifest.v1.tsv"),
            (DEFAULT_MAP, "ops/config/rule-enforcement-map.json"),
        ):
            assert_head_committed(REPO, artifact, relative)
    except ReviewedArtifactError as exc:
        raise ImportRefusal(str(exc)) from exc
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImportRefusal(f"mapping plan does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ImportRefusal(f"mapping plan is not JSON: {path}: {exc.msg}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != MAPPING_PLAN_SCHEMA:
        raise ImportRefusal(f"mapping plan must declare schema {MAPPING_PLAN_SCHEMA}")
    provenance = raw.get("review_provenance")
    expected_review_path = "audits/guidance-situation-curation-review.v1.json"
    if not isinstance(provenance, dict) or provenance.get("path") != expected_review_path:
        raise ImportRefusal("mapping plan must bind the checked-in v1 curation review path")
    if provenance.get("sha256") != file_sha256(DEFAULT_CURATION_REVIEW):
        raise ImportRefusal("mapping plan curation-review digest does not match the checked-in review")
    bindings = raw.get("doctrine_mappings")
    if not isinstance(bindings, dict):
        raise ImportRefusal("mapping plan requires doctrine_mappings object")
    review = json.loads(DEFAULT_CURATION_REVIEW.read_text(encoding="utf-8"))
    if (review.get("schema") != "guidance-situation-curation-review/v1"
            or review.get("review_state") != "proposed"):
        raise ImportRefusal("checked-in curation review schema/state is not the governed v1 proposal")
    for field, artifact, relative in (
        ("reviewed_source_manifest", DEFAULT_MANIFEST,
         "audits/guidance-migration-manifest.v1.tsv"),
        ("reviewed_base_inventory", DEFAULT_MAP,
         "ops/config/rule-enforcement-map.json"),
    ):
        item = review.get(field)
        if (not isinstance(item, dict) or item.get("path") != relative
                or item.get("sha256") != file_sha256(artifact)):
            raise ImportRefusal(f"checked-in curation review {field} provenance drifted")
    review_rows = review.get("doctrine_guidance")
    if not isinstance(review_rows, list):
        raise ImportRefusal("checked-in curation review has no doctrine_guidance array")
    expected = {row.get("guidance_id"): row for row in review_rows if isinstance(row, dict)}
    if len(expected) != len(review_rows) or set(bindings) != set(expected):
        raise ImportRefusal("mapping plan does not exactly cover the checked-in curation review")
    for guidance_id, planned in bindings.items():
        if not isinstance(planned, list):
            raise ImportRefusal(f"mapping plan bindings for {guidance_id} must be an array")
        review_row = expected[guidance_id]
        expected_mappings = review_row.get("mappings")
        if not isinstance(expected_mappings, list) or len(planned) != len(expected_mappings):
            raise ImportRefusal(f"mapping plan binding count differs from review for {guidance_id}")
        expected_shapes = sorted(
            (
                review_row.get("concept_key"),
                mapping.get("doctrine_section_id"),
                mapping.get("rationale"),
            )
            for mapping in expected_mappings if isinstance(mapping, dict)
        )
        actual_shapes = sorted(
            (
                binding.get("concept_key"),
                binding.get("doctrine_section_id"),
                binding.get("reason"),
            )
            for binding in planned if isinstance(binding, dict)
        )
        if len(actual_shapes) != len(planned) or actual_shapes != expected_shapes:
            raise ImportRefusal(f"mapping plan content differs from review for {guidance_id}")
        if any(not isinstance(binding.get("concept_id"), str) for binding in planned):
            raise ImportRefusal(f"mapping plan concept id is missing for {guidance_id}")
    return bindings


def resolve_mapping_plan(cur: Any, mappings: dict[str, list[dict[str, str]]]
                         ) -> dict[str, list[dict[str, str]]]:
    """Verify reviewed concept identities and approved exact bridges, then strip labels."""
    all_bindings = [binding for rows in mappings.values() for binding in rows]
    concept_ids = sorted({binding["concept_id"] for binding in all_bindings})
    section_ids = sorted({binding["doctrine_section_id"] for binding in all_bindings})
    concepts = cur.execute(
        "select id::text as concept_id,concept_key,status from retrieval_concept "
        "where id=any(%s::uuid[])", (concept_ids,)
    ).fetchall()
    concept_by_id = {row["concept_id"]: row for row in concepts}
    bridges = cur.execute(
        "select concept_id::text as concept_id,section_id::text as section_id "
        "from doctrine_concept_mapping where status='approved' "
        "and concept_id=any(%s::uuid[]) and section_id=any(%s::uuid[])",
        (concept_ids, section_ids),
    ).fetchall()
    approved = {(row["concept_id"], row["section_id"]) for row in bridges}
    canonical: dict[str, list[dict[str, str]]] = {}
    for guidance_id, rows in mappings.items():
        canonical[guidance_id] = []
        for binding in rows:
            concept_id = binding["concept_id"]
            concept = concept_by_id.get(concept_id)
            if (concept is None or concept.get("status") != "approved"
                    or concept.get("concept_key") != binding["concept_key"]):
                raise ImportRefusal(
                    f"mapping plan concept identity is not exactly approved for {guidance_id}"
                )
            pair = (concept_id, binding["doctrine_section_id"])
            if pair not in approved:
                raise ImportRefusal(
                    f"mapping plan exact doctrine bridge is not approved for {guidance_id}"
                )
            canonical[guidance_id].append({
                "concept_id": concept_id,
                "doctrine_section_id": binding["doctrine_section_id"],
                "reason": binding["reason"],
            })
    return canonical


def resolve_active_rules(rows: Iterable[Mapping[str, Any]], expected_source_ids: set[str]) -> dict[str, str]:
    """Resolve reviewed short IDs against the standing-context source of truth.

    The caller excludes intro_politics rules because they intentionally render
    to a separate introduction surface and are not part of standing-context's
    recited corpus. Refuse ambiguity, malformed UUIDs, inactive/missing sources,
    and eligible active extras. The exact-set comparison prevents an apparently
    harmless new standing rule from entering a reviewed batch under an old
    manifest.
    """
    by_short: dict[str, list[str]] = {}
    seen_full: set[str] = set()
    for row in rows:
        full = row.get("id")
        short = row.get("source_id")
        if not isinstance(full, str) or not isinstance(short, str):
            raise ImportRefusal("active rule inventory returned malformed identifier")
        if len(full) != 36 or full[8:9] != "-" or short != full[:8]:
            raise ImportRefusal(f"active rule inventory returned invalid UUID mapping for {short!r}")
        if full in seen_full:
            raise ImportRefusal(f"active rule inventory repeats UUID {full}")
        seen_full.add(full)
        by_short.setdefault(short, []).append(full)
    ambiguous = sorted(short for short, values in by_short.items() if len(values) != 1)
    if ambiguous:
        raise ImportRefusal("active rule short IDs are ambiguous: " + ",".join(ambiguous))
    resolved = {short: values[0] for short, values in by_short.items()}
    actual = set(resolved)
    if actual != expected_source_ids:
        missing = sorted(expected_source_ids - actual)
        extra = sorted(actual - expected_source_ids)
        raise ImportRefusal(
            "active rule inventory does not exactly match reviewed registry"
            + ("; missing=" + ",".join(missing) if missing else "")
            + ("; extra=" + ",".join(extra) if extra else ""))
    return {source_id: resolved[source_id] for source_id in sorted(expected_source_ids)}


def resolve_classifier_actor(rows: Iterable[Mapping[str, Any]], slug: str) -> str:
    ids = [row.get("id") for row in rows]
    if len(ids) != 1 or not isinstance(ids[0], str):
        raise ImportRefusal(f"classifier actor {slug!r} must resolve to exactly one active non-human actor")
    return ids[0]


def stage_and_apply(cur: Any, *, digest: str, canonical_manifest_text: str,
                    classifier_actor_id: str, stage_idempotency_key: str,
                    stage_reason: str, apply_idempotency_key: str,
                    apply_reason: str) -> tuple[Any, Any]:
    """The only writer sequence in this CLI; no decision/activation call exists."""
    batch_id = cur.execute(
        "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s) as id",
        (digest, canonical_manifest_text, classifier_actor_id,
         stage_idempotency_key, stage_reason),
    ).fetchone()["id"]
    apply_event_id = cur.execute(
        "select ops.apply_guidance_import_batch(%s,%s,%s,%s) as id",
        (batch_id, digest, apply_idempotency_key, apply_reason),
    ).fetchone()["id"]
    return batch_id, apply_event_id


def build_review_artifact(source_map: dict[str, Any], manifest: dict[str, Any],
                          active_rule_rows: Iterable[Mapping[str, Any]],
                          doctrine_mappings: dict[str, list[dict[str, str]]],
                          constitution_guidance_ids: Iterable[str], *,
                          map_digest: str, manifest_digest: str) -> tuple[dict[str, Any], bytes, str]:
    compiled, errors = registry.build_registry(source_map, manifest)
    errors.extend(registry.validate_registry(compiled))
    if errors:
        raise ImportRefusal("compiled registry refused: " + "; ".join(errors))
    source_ids = {item["source_id"] for item in compiled["items"]}
    source_rule_ids = resolve_active_rules(active_rule_rows, source_ids)
    activation_manifest, errors = registry.build_activation_manifest(
        compiled,
        constitution_guidance_ids=constitution_guidance_ids,
        source_manifest_provenance={
            "path": "audits/guidance-migration-manifest.v1.tsv",
            "sha256": manifest_digest,
            "manifest": "carr-guidance-migration",
            "schema_version": "1.0.0",
            "source_classification": "judgment_ambient",
            "entry_count": len(manifest.get("entries", [])),
        },
        base_inventory={
            "path": "ops/config/rule-enforcement-map.json",
            "sha256": map_digest,
            "active_source_ids": sorted(source_ids),
            "source_rule_ids": source_rule_ids,
        },
        situation_mapping_bindings=doctrine_mappings,
    )
    if errors:
        raise ImportRefusal("activation manifest refused: " + "; ".join(errors))
    canonical = registry.activation_manifest_bytes(activation_manifest)
    return activation_manifest, canonical, hashlib.sha256(canonical).hexdigest()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--enforcement-map", type=Path, default=DEFAULT_MAP)
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--mapping-plan", type=Path, required=True,
                   help="explicit guidance-situation-mapping-plan/v1 JSON")
    p.add_argument("--constitution-guidance-id", action="append", default=[],
                   help="primary guidance id selected for the 5–10-item constitution; repeat")
    p.add_argument("--apply", action="store_true",
                   help="after rendering the same review artifact, stage then apply it through writer functions")
    p.add_argument("--stage-idempotency-key")
    p.add_argument("--stage-reason")
    p.add_argument("--classifier-actor-slug", default="codex",
                   help="active non-human actor credited by the writer function when staging (default: codex)")
    p.add_argument("--apply-idempotency-key")
    p.add_argument("--apply-reason")
    p.add_argument("--output-manifest", type=Path,
                   help="write the exact canonical UTF-8 bytes reviewed by this run")
    return p


def require_apply_args(args: argparse.Namespace) -> None:
    if not args.apply:
        return
    missing = [name for name in ("stage_idempotency_key", "stage_reason",
                                 "apply_idempotency_key", "apply_reason")
               if not getattr(args, name)]
    if missing:
        raise ImportRefusal("--apply requires explicit " + ", ".join("--" + name.replace("_", "-") for name in missing))
    if args.stage_idempotency_key == args.apply_idempotency_key:
        raise ImportRefusal("stage and apply require distinct idempotency keys")


def require_writer_dsn(env: Mapping[str, str]) -> str:
    """Return the separately scoped writer credential for canonical writes.

    `DATABASE_URL` is deliberately read/preview-only.  It must never become an
    implicit fallback for --apply, because that could turn an owner connection
    into a routine canonical-write path.
    """
    dsn = env.get("CARR_DB_WRITER_URL", "").strip()
    if not dsn:
        raise ImportRefusal("--apply requires CARR_DB_WRITER_URL; DATABASE_URL is preview-only")
    return dsn


def assert_writer_identity(cur: Any) -> None:
    """Fail closed unless the live write connection is the scoped writer role."""
    row = cur.execute(
        "select session_user::text as session_user, current_user::text as current_user"
    ).fetchone()
    # Migration 0352 (2026-08-27, Joe's order) granted the staging pair to
    # carr_authority so Joe's own login runs the staging without a minted
    # writer credential; the human authority login is therefore an accepted
    # write identity alongside the routine writer role.
    accepted_write_identities = {WRITER_ROLE, "carr_authority_joe"}
    if not isinstance(row, Mapping) or (
        row.get("session_user") not in accepted_write_identities
        or row.get("current_user") not in accepted_write_identities
        or row.get("session_user") != row.get("current_user")
    ):
        raise ImportRefusal(
            "writer connection identity refused: session_user and current_user must both be carr_writer or carr_authority_joe"
        )


def begin_read_only(cur: Any) -> None:
    """Make the generic preview connection incapable of canonical writes."""
    cur.execute("set transaction read only")


def apply_reviewed_batch(writer_dsn: str, *, digest: str,
                         canonical_manifest_text: str,
                         classifier_actor_slug: str, stage_idempotency_key: str,
                         stage_reason: str, apply_idempotency_key: str,
                         apply_reason: str) -> tuple[Any, Any]:
    """Apply a reviewed artifact only through a separately authenticated writer."""
    with psycopg.connect(writer_dsn) as write_conn:
        with write_conn.cursor(row_factory=psycopg.rows.dict_row) as write_cur:
            # This must precede every writer-function call and even the
            # writer-side actor lookup.  An owner DSN, an arbitrary login, or
            # SET ROLE mismatch cannot become a routine path.
            assert_writer_identity(write_cur)
            actor_rows = write_cur.execute(
                "select id::text as id from actor where slug=%s "
                "and kind in ('automation','system') and active",
                (classifier_actor_slug,)).fetchall()
            classifier_actor_id = resolve_classifier_actor(actor_rows, classifier_actor_slug)
            batch_id, apply_event_id = stage_and_apply(
                write_cur, digest=digest, canonical_manifest_text=canonical_manifest_text,
                classifier_actor_id=classifier_actor_id,
                stage_idempotency_key=stage_idempotency_key, stage_reason=stage_reason,
                apply_idempotency_key=apply_idempotency_key, apply_reason=apply_reason)
            write_conn.commit()
            return batch_id, apply_event_id


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        require_apply_args(args)
        if not args.constitution_guidance_id:
            raise ImportRefusal("at least one --constitution-guidance-id is required")
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise ImportRefusal("DATABASE_URL is required; this CLI never accepts a DSN argument")
        writer_dsn = require_writer_dsn(os.environ) if args.apply else None
        if args.enforcement_map.resolve() != DEFAULT_MAP.resolve():
            raise ImportRefusal("--enforcement-map must use the checked-in reviewed v1 path")
        if args.manifest.resolve() != DEFAULT_MANIFEST.resolve():
            raise ImportRefusal("--manifest must use the checked-in reviewed v1 path")
        source_map = json.loads(args.enforcement_map.read_text(encoding="utf-8"))
        manifest, errors = registry.load_migration_manifest(str(args.manifest))
        if errors:
            raise ImportRefusal("reviewed migration manifest refused: " + "; ".join(errors))
        reviewed_mappings = load_mapping_plan(args.mapping_plan)
        with psycopg.connect(dsn) as read_conn:
            with read_conn.cursor(row_factory=psycopg.rows.dict_row) as read_cur:
                begin_read_only(read_cur)
                preview, preview_errors = registry.build_registry(source_map, manifest)
                if preview_errors:
                    raise ImportRefusal("compiled registry refused: " + "; ".join(preview_errors))
                mappings = resolve_mapping_plan(read_cur, reviewed_mappings)
                active_rows = read_cur.execute(
                    "select id::text as id, left(id::text,8) as source_id from rule "
                    "where status='active' "
                    "and coalesce(scope->>'kind','') <> 'intro_politics' order by id").fetchall()
                artifact, canonical, digest = build_review_artifact(
                    source_map, manifest, active_rows, mappings, args.constitution_guidance_id,
                    map_digest=file_sha256(args.enforcement_map), manifest_digest=file_sha256(args.manifest))
                review = {
                    "ok": True, "mode": "apply" if args.apply else "dry_run",
                    "manifest_digest": digest, "canonicalization": artifact["canonicalization"],
                    "canonical_manifest_utf8": canonical.decode("utf-8"),
                    "entry_count": len(artifact["entries"]),
                    "constitution_guidance_ids": artifact["constitution_guidance_ids"],
                }
                if args.output_manifest:
                    # A reviewed digest artifact must never silently overwrite
                    # another run's preimage.  Pick a new path deliberately.
                    with args.output_manifest.open("xb") as fh:
                        fh.write(canonical)
                    review["output_manifest"] = str(args.output_manifest)
        if args.apply:
            assert writer_dsn is not None  # narrowed by require_writer_dsn above
            batch_id, apply_event_id = apply_reviewed_batch(
                writer_dsn, digest=digest,
                canonical_manifest_text=canonical.decode("utf-8"),
                classifier_actor_slug=args.classifier_actor_slug,
                stage_idempotency_key=args.stage_idempotency_key, stage_reason=args.stage_reason,
                apply_idempotency_key=args.apply_idempotency_key, apply_reason=args.apply_reason)
            review.update({"batch_id": str(batch_id), "apply_event_id": str(apply_event_id)})
        print(json.dumps(review, sort_keys=True, indent=2))
        return 0
    except (ImportRefusal, OSError, json.JSONDecodeError, psycopg.Error) as exc:
        print(f"guidance-registry-import: REFUSED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
