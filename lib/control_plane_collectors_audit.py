"""Read-only evidence collectors for the six audit/system cognition builders.

The scheduler payload is deliberately not an evidence source.  Callers provide
only a narrow read adapter; this module turns canonical rows and immutable
ledger receipts into the ``facts`` object consumed by the audit predicates.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from lib.control_plane_inputs import InputUnavailable, NoEligibleRecords


class ReadOnlyQuery(Protocol):
    """A named, parameterized SELECT-only seam implemented by the jobs reader."""

    def fetch_all(self, query_key: str, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]: ...


AUDIT_BUILDERS = frozenset({
    "capability-program.next-candidate", "cc-release-diff",
    "system-health.monthly-evidence", "loops.next-actionable",
    "doctrine.review-due", "system.prune-candidates",
})

# Query keys are part of the contract: the production adapter may log and
# allowlist them, while the tests prove a builder cannot silently substitute a
# payload value or a differently-shaped query.
SQL: dict[str, str] = {
    "capability": """select w.id::text as id, w.state, w.project_context,
             w.session_id::text as session_id, w.session_state
           from ops.v_control_plane_capability_candidate w
           order by w.program_ordinal limit 1""",
    "release": """with completed as (
             select release_key, ended_at, source_ref,
                    row_number() over(order by ended_at desc, release_key desc) as rn
               from ops.release where state='complete' and ended_at is not null
           )
           select previous.release_key as release_from,
                  current.release_key as release_to,
                  current.ended_at as released_at,
                  current.source_ref
             from completed current join completed previous on previous.rn=2
            where current.rn=1""",
    "health_evidence": """select evidence_class, source_ref
           from ops.v_control_plane_health_evidence""",
    "loops": """select id::text as id, owner, state, counterparty_ref, event_blocker_ref
           from ops.v_control_plane_actionable_loops where state='actionable'""",
    "doctrine_due": """select id::text as id, slug, review_after
           from ops.v_control_plane_doctrine_due order by review_after""",
    "doctrine_failures": """select source_ref
           from ops.v_control_plane_doctrine_failures""",
    "system_candidates": """select subject_ref, measurement from ops.v_control_plane_system_prune_candidates
           where measurement in ('stale','duplicate','oversized')""",
    # Completion receipts are append-only (`ops.job_receipt`); state is derived
    # from their absence/presence, never supplied by a scheduled payload.
    "monthly_receipt": """select r.receipt_ref, r.created_at from ops.job_receipt r
           join ops.job j on j.id=r.job_id where j.definition_key=%s
             and j.mode=%s and r.kind='completion'
             and j.scheduled_for >=
                 (date_trunc('month', %s::timestamptz at time zone 'America/Chicago')
                  at time zone 'America/Chicago')
             and j.scheduled_for < %s::timestamptz
           order by r.created_at desc limit 1""",
    "release_receipt": """select r.created_at from ops.job_receipt r join ops.job j on j.id=r.job_id
           where j.definition_key='cc-update-audit' and j.mode=%s
             and r.kind='completion' and j.scheduled_for < %s::timestamptz
           order by r.created_at desc limit 1""",
    "sweep_receipt": """select r.receipt_ref, r.created_at from ops.job_receipt r
           join ops.job j on j.id=r.job_id
          where j.definition_key='system-sweep-monthly' and j.mode=%s
            and r.kind='completion'
            and j.scheduled_for >=
                (date_trunc('month', %s::timestamptz at time zone 'America/Chicago')
                 at time zone 'America/Chicago')
            and j.scheduled_for < %s::timestamptz
          order by r.created_at desc limit 1""",
}


def _rows(reader: ReadOnlyQuery, key: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]:
    try:
        rows = reader.fetch_all(key, SQL[key], params)
    except Exception as exc:
        raise InputUnavailable(key, f"canonical read failed: {type(exc).__name__}") from exc
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise InputUnavailable(key, "canonical read returned non-row data")
    return rows


def _text(row: Mapping[str, Any], field: str, key: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InputUnavailable(key, f"canonical row lacks {field}")
    return value


def _receipt_state(reader: ReadOnlyQuery, workflow_key: str, mode: str,
                   scheduled_for: datetime) -> str:
    params = (workflow_key, mode, scheduled_for, scheduled_for)
    return "absent" if not _rows(reader, "monthly_receipt", params) else "already_run"


def collect_audit_facts(builder_key: str, reader: ReadOnlyQuery, *, mode: str,
                        scheduled_for: datetime) -> dict[str, Any]:
    """Return exact audit-predicate fields, or refuse instead of guessing."""
    if builder_key not in AUDIT_BUILDERS:
        raise InputUnavailable(builder_key, "not an audit/system builder")
    if builder_key == "capability-program.next-candidate":
        rows = _rows(reader, "capability")
        if len(rows) != 1:
            raise InputUnavailable(builder_key, "expected exactly one current capability candidate")
        row = rows[0]
        context = row.get("project_context")
        if not isinstance(context, Mapping):
            raise InputUnavailable(builder_key, "candidate lacks canonical project context")
        if "requested_mutation" not in context:
            raise InputUnavailable(builder_key, "candidate context lacks requested_mutation")
        candidate = {"id": _text(row, "id", builder_key),
                     "admission_state": "admitted" if row.get("session_state") == "verification" else "",
                     # The query itself returns exactly one current Work Request
                     # and one verification session; that is the bounded scope,
                     # not the free-text project_context.scope description.
                     "scope": "single", "session_id": row.get("session_id")}
        if row.get("session_state") != "verification":
            raise InputUnavailable(builder_key, "candidate is not an admitted bounded verification candidate")
        mutation = context["requested_mutation"]
        if mutation is None:
            mutation = "none"
        if not isinstance(mutation, str):
            raise InputUnavailable(builder_key, "candidate requested_mutation must be text or null")
        return {"candidate": candidate, "requested_mutation": mutation,
                "runner_identity": f"worker:{row['session_id']}"}
    if builder_key == "cc-release-diff":
        rows = _rows(reader, "release")
        receipts = _rows(reader, "release_receipt", (mode, scheduled_for))
        if len(rows) != 1 or len(receipts) > 1:
            raise InputUnavailable(builder_key, "release sentinel or immutable audit receipt is ambiguous")
        row = rows[0]
        release = {"from": _text(row, "release_from", builder_key), "to": _text(row, "release_to", builder_key),
                   "released_at": _text(row, "released_at", builder_key),
                   "last_accepted_at": (_text(receipts[0], "created_at", builder_key)
                                        if receipts else "1970-01-01T00:00:00+00:00")}
        if release["from"] == release["to"]:
            raise InputUnavailable(builder_key, "release sentinel did not record a version change")
        return {"release": release, "release_source_ref": _text(row, "source_ref", builder_key)}
    if builder_key == "system-health.monthly-evidence":
        rows = _rows(reader, "health_evidence")
        evidence: dict[str, list[str]] = {"live": [], "registry": [], "artifact": []}
        for row in rows:
            category = row.get("evidence_class")
            if category in evidence:
                evidence[category].append(_text(row, "source_ref", builder_key))
        if not all(evidence.values()):
            raise InputUnavailable(builder_key, "monthly health evidence lacks live, registry, or artifact proof")
        return {"monthly_receipt_state": _receipt_state(
                    reader, "health-audit-monthly", mode, scheduled_for),
                "evidence": evidence}
    if builder_key == "loops.next-actionable":
        rows = _rows(reader, "loops")
        actions = [{"id": _text(row, "id", builder_key), "owner": row.get("owner"), "state": row.get("state"),
                    "counterparty": row.get("counterparty_ref"), "event_blocker": row.get("event_blocker_ref")}
                   for row in rows]
        if not actions:
            # Loops filed through the deferral gate carry an outside blocker,
            # so an empty actionable queue is the ordinary weekday state: a
            # clean skip, not a dead letter (rule 88e9b5eb). It dead-lettered
            # 13 straight shadow runs through 2026-10-07 before this.
            raise NoEligibleRecords(builder_key)
        return {"actions": actions}
    if builder_key == "doctrine.review-due":
        due, failures = _rows(reader, "doctrine_due"), _rows(reader, "doctrine_failures")
        if not due or not failures:
            raise InputUnavailable(builder_key, "due doctrine sections or measured failure evidence is absent")
        playbook_params = ("playbook-review-monthly", mode, scheduled_for, scheduled_for)
        sweep_params = (mode, scheduled_for, scheduled_for)
        return {"monthly_receipt_state": "absent" if not _rows(reader, "monthly_receipt", playbook_params) else "present",
                "sweep_receipt_state": "present" if _rows(reader, "sweep_receipt", sweep_params) else "absent",
                "due_sections": [{"id": _text(row, "id", builder_key), "slug": _text(row, "slug", builder_key),
                                  "review_after": _text(row, "review_after", builder_key)} for row in due],
                "failure_evidence_refs": [_text(row, "source_ref", builder_key) for row in failures]}
    rows = _rows(reader, "system_candidates")
    candidates = [{"subject_ref": _text(row, "subject_ref", builder_key), "measurement": row.get("measurement")} for row in rows]
    if not candidates or any(row["measurement"] not in {"stale", "duplicate", "oversized"} for row in candidates):
        raise InputUnavailable(builder_key, "no measured stale, duplicate, or oversized system candidates")
    return {"monthly_receipt_state": _receipt_state(
                reader, "system-sweep-monthly", mode, scheduled_for),
            "candidates": candidates}


def audit_evidence_envelope(builder_key: str, workflow_key: str, reader: ReadOnlyQuery,
                            *, mode: str, scheduled_for: datetime) -> dict[str, Any]:
    """Produce the standard provenance-bearing envelope for ``build_input``."""
    return {"source_kind": "canonical_db", "source_ref": f"db:audit:{builder_key}",
            "values": collect_audit_facts(
                builder_key, reader, mode=mode, scheduled_for=scheduled_for)}
