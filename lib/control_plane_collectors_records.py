"""Typed, read-only canonical-record collectors for cognition inputs.

The scheduler must not manufacture semantic facts in its payload.  These
collectors are the small adapter-facing seam for the five record-backed
builders: they accept rows only from a read-only query adapter, normalize the
rows into the exact predicate shapes, and refuse incomplete or contradictory
record evidence.  They deliberately do not open a connection themselves;
runtime wiring supplies the least-privilege ``carr_jobs`` adapter.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from lib.control_plane_inputs import InputUnavailable, NoEligibleRecords

# A short week -- fewer than the cap's worth of eligible records -- is the
# NORMAL end state as the book gets covered (both SKILL.md files: "reports
# done and stops finding work"). These two builders' queues get shorter over
# time by design, so an empty result from either is a clean no-work signal,
# never a refusal. content-fuel/npi/radar are NOT in this set: those always
# expect a fixed shape (two rotation lanes, etc.), so an empty read there
# stays a genuine InputUnavailable.
NO_WORK_WHEN_EMPTY: frozenset[str] = frozenset({
    "entity-enrichment.next-40",
    "deal-history.next-slice",
})

# Bands 1-4 of the enrichment queue (0387) are never-verified profile gaps,
# not stale record_flag rows -- there is no expired/unstamped_volatile
# reason to report, so the view marks them 'not_recorded'. Keep this in sync
# with migrations/0387_control_plane_record_queue_priority_tiers.sql.
REVERIFICATION_DUE_REASONS: frozenset[str] = frozenset({
    "expired", "unstamped_volatile", "not_recorded",
})


class ReadOnlyQueryAdapter(Protocol):
    """The sole database seam.  Implementations may execute only SELECTs."""

    def fetch_all(self, *, query_key: str, sql: str,
                  params: Sequence[object] = ()) -> Sequence[Mapping[str, Any]]: ...


# Query keys are stable contracts for an adapter.  The SQL is intentionally
# restricted to named read surfaces; an installation without the corresponding
# canonical view must return no rows and therefore refuse, rather than falling
# back to scheduler-supplied values.
QUERIES: dict[str, str] = {
    "entity-enrichment.next-40": """
      select subject_type, subject_id::text, reverification_due,
             current_verification_status, priority, expired_at
        from v_control_plane_enrichment_queue
       where current_verification_status = 'not_current'
       order by priority asc, subject_id asc
       limit 40""",
    "deal-history.next-slice": """
      select subject_type, subject_id::text, verification, priority, source_class,
             slice_limit, enrichment_subject_count, enrichment_scheduled_for,
             enrichment_mode, sizing_state
        from v_control_plane_deal_history_queue
       where verification = 'unverified'
         and enrichment_mode = %s
         and enrichment_scheduled_for >=
             (date_trunc('week', %s::timestamptz at time zone 'America/Chicago')
              at time zone 'America/Chicago')
         and enrichment_scheduled_for < %s::timestamptz
       order by priority asc, subject_id asc
       limit 25""",
    "content-fuel.next-rotation": """
      select lane, temperature,
             date_trunc('week', %s::timestamptz at time zone 'America/Chicago')
               at time zone 'America/Chicago' as freshness_cutoff,
             case when exists (
               select 1 from ops.job_receipt r join ops.job j on j.id=r.job_id
                where j.definition_key='content-fuel-harvest-weekly'
                  and j.mode=%s and r.kind='completion'
                  and j.scheduled_for >=
                    (date_trunc('week', %s::timestamptz at time zone 'America/Chicago')
                     at time zone 'America/Chicago')
                  and j.scheduled_for < %s::timestamptz
             ) then 'present' else 'absent' end as previous_receipt_state
        from v_control_plane_content_fuel_rotation
       order by lane asc""",
    "npi.weekly-delta": """
      select lane, territory_match, entity_type,
             date_trunc('week', %s::timestamptz at time zone 'America/Chicago')
               at time zone 'America/Chicago' as freshness_cutoff,
             delta_state
        from v_control_plane_npi_delta
       where territory_match is true
         and entity_type = 'healthcare_provider'
         and created_at >=
             (date_trunc('week', %s::timestamptz at time zone 'America/Chicago')
              at time zone 'America/Chicago')
         and created_at < %s::timestamptz
       order by lane asc""",
    "radar.weekly-candidates": """
      select lane, score,
             updated_at >= (%s::timestamptz - interval '90 days') as fresh,
             (est_lease_event <= ((%s::timestamptz at time zone 'America/Chicago')::date
                                   + interval '18 months')
              or updated_at < (%s::timestamptz - interval '7 days')) as overdue,
             %s::timestamptz - interval '90 days' as freshness_cutoff,
             case when exists (
               select 1 from ops.job_receipt r join ops.job j on j.id=r.job_id
                where j.definition_key='radar-weekly' and j.mode=%s
                  and r.kind='completion'
                  and j.scheduled_for >=
                    (date_trunc('week', %s::timestamptz at time zone 'America/Chicago')
                     at time zone 'America/Chicago')
                  and j.scheduled_for < %s::timestamptz
             ) then 'present' else 'absent' end as previous_receipt_state
        from v_control_plane_radar_candidates
       order by score desc, lane asc""",
}


def _text(row: Mapping[str, Any], field: str, builder: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InputUnavailable(builder, f"canonical row lacks non-empty {field}")
    return value


def _bool(row: Mapping[str, Any], field: str, builder: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise InputUnavailable(builder, f"canonical row lacks boolean {field}")
    return value


def _number(row: Mapping[str, Any], field: str, builder: str) -> int | float:
    value = row.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise InputUnavailable(builder, f"canonical row lacks numeric {field}")
    return value


def _cutoff(rows: Sequence[Mapping[str, Any]], builder: str) -> str:
    values = {_text(row, "freshness_cutoff", builder) for row in rows}
    if len(values) != 1:
        raise InputUnavailable(builder, "canonical rows disagree on freshness_cutoff")
    return next(iter(values))


class CanonicalRecordCollector:
    """Produce provenance-bearing evidence envelopes from reviewed read views."""

    def __init__(self, adapter: ReadOnlyQueryAdapter, *, scheduled_for: datetime,
                 mode: str, policies: Mapping[str, Mapping[str, Any]]):
        self.adapter = adapter
        self.scheduled_for = scheduled_for
        self.mode = mode
        self.policies = policies

    def collect(self, *, builder_key: str, workflow_key: str) -> list[dict[str, Any]]:
        if builder_key not in QUERIES:
            raise InputUnavailable(builder_key, "not a canonical-record collector")
        params: Sequence[object] = ()
        if builder_key == "deal-history.next-slice":
            params = (self.mode, self.scheduled_for, self.scheduled_for)
        elif builder_key == "content-fuel.next-rotation":
            params = (self.scheduled_for, self.mode, self.scheduled_for,
                      self.scheduled_for)
        elif builder_key == "npi.weekly-delta":
            params = (self.scheduled_for, self.scheduled_for, self.scheduled_for)
        elif builder_key == "radar.weekly-candidates":
            params = (self.scheduled_for, self.scheduled_for, self.scheduled_for,
                      self.scheduled_for, self.mode, self.scheduled_for,
                      self.scheduled_for)
        rows = list(self.adapter.fetch_all(
            query_key=builder_key, sql=QUERIES[builder_key], params=params))
        if not rows:
            if builder_key in NO_WORK_WHEN_EMPTY:
                raise NoEligibleRecords(builder_key)
            raise InputUnavailable(builder_key, "no canonical record evidence is available")
        if not all(isinstance(row, Mapping) for row in rows):
            raise InputUnavailable(builder_key, "read adapter returned a non-object row")
        values = _values(builder_key, rows, expected_mode=self.mode)
        evidence = [{"source_kind": "canonical_db", "source_ref": f"db:{builder_key}",
                     "values": values}]
        policy_name = {
            "entity-enrichment.next-40": "entity_research_policy",
            "deal-history.next-slice": "deal_history_research_policy",
        }.get(builder_key)
        if policy_name is not None:
            policy = self.policies.get(policy_name)
            if not isinstance(policy, Mapping):
                raise InputUnavailable(builder_key, f"missing versioned {policy_name}")
            expected_mode = ("direct-primary-sources" if builder_key == "entity-enrichment.next-40"
                             else "direct-identity-sources")
            if policy.get("mode") != expected_mode or policy.get("proposal_only") is not True:
                raise InputUnavailable(builder_key, f"invalid versioned {policy_name}")
            evidence.append({"source_kind": "versioned_policy",
                             "source_ref": f"policy:control-plane-collector-policy.v1:{policy_name}",
                             "values": {"source_policy": dict(policy)}})
        return evidence


def _values(builder: str, rows: Sequence[Mapping[str, Any]], *, expected_mode: str) -> dict[str, Any]:
    if builder == "entity-enrichment.next-40":
        # A short week is normal (0387): accept any queue from 1 up to the
        # 40-record slice cap. An empty queue never reaches this function --
        # CanonicalRecordCollector.collect raises NoEligibleRecords for that
        # builder before _values is called.
        if not 1 <= len(rows) <= 40:
            raise InputUnavailable(builder, "re-verification queue must contain between 1 and 40 rows")
        subjects = [{"subject_type": _text(r, "subject_type", builder),
                     "subject_id": _text(r, "subject_id", builder),
                     "reverification_due": _text(r, "reverification_due", builder),
                     "current_verification_status": _text(r, "current_verification_status", builder),
                     "priority": _number(r, "priority", builder),
                     "expired_at": _text(r, "expired_at", builder)} for r in rows]
        if any(s["current_verification_status"] != "not_current"
               or s["reverification_due"] not in REVERIFICATION_DUE_REASONS
               for s in subjects):
            raise InputUnavailable(builder, "enrichment queue contains a current or non-due subject")
        # Rows arrive `order by priority asc`, so a genuinely malformed
        # queue -- a duplicate priority, a gap, or a priority that does not
        # start at 1 -- fails this equality regardless of row count.
        if [s["priority"] for s in subjects] != list(range(1, len(subjects) + 1)):
            raise InputUnavailable(builder, "enrichment queue is not in canonical re-verification order")
        return {"subjects": subjects}
    if builder == "deal-history.next-slice":
        limits = {_number(row, "slice_limit", builder) for row in rows}
        if len(limits) != 1 or next(iter(limits)) not in (15, 25):
            raise InputUnavailable(builder, "deal-history slice lacks its canonical 15/25 cap")
        slice_limit = int(next(iter(limits)))
        if not 1 <= len(rows) <= slice_limit:
            raise InputUnavailable(builder, "deal-history slice exceeds its canonical cap")
        subjects = [{"subject_type": _text(r, "subject_type", builder),
                     "subject_id": _text(r, "subject_id", builder),
                     "verification": _text(r, "verification", builder),
                     "priority": _number(r, "priority", builder),
                     "source_class": _text(r, "source_class", builder)} for r in rows]
        # Rows arrive `order by priority asc`, so a duplicate priority, a
        # gap, or a priority that does not start at 1 fails this equality
        # regardless of row count -- same malformed-order guard as
        # entity-enrichment.next-40.
        if [s["priority"] for s in subjects] != list(range(1, len(subjects) + 1)):
            raise InputUnavailable(builder, "deal-history queue is not in canonical priority order")
        if any(s["verification"] != "unverified" for s in subjects):
            raise InputUnavailable(builder, "deal-history queue includes a verified subject")
        if any(s["source_class"] != "canonical_counterparty" for s in subjects):
            raise InputUnavailable(builder, "deal-history queue is not the canonical counterparty projection")
        counts = {_number(row, "enrichment_subject_count", builder) for row in rows}
        modes = {_text(row, "enrichment_mode", builder) for row in rows}
        scheduled = {_text(row, "enrichment_scheduled_for", builder) for row in rows}
        sizing_states = {_text(row, "sizing_state", builder) for row in rows}
        if len(counts) != 1 or len(modes) != 1 or len(scheduled) != 1 or sizing_states != {"receipt_bound"}:
            raise InputUnavailable(builder, "deal-history rows disagree on enrichment receipt evidence")
        mode_value = next(iter(modes))
        count = int(next(iter(counts)))
        if mode_value != expected_mode:
            raise InputUnavailable(builder, "deal-history enrichment receipt mode does not match the job")
        if slice_limit != (15 if count >= 30 else 25):
            raise InputUnavailable(builder, "deal-history cap does not reconcile to Thursday enrichment count")
        return {"subjects": subjects, "slice_limit": slice_limit,
                "enrichment_subject_count": count,
                "enrichment_scheduled_for": next(iter(scheduled)),
                "enrichment_mode": mode_value}
    if builder == "content-fuel.next-rotation":
        if len(rows) != 2:
            raise InputUnavailable(builder, "content-fuel rotation must contain exactly two lanes")
        content_lanes: list[dict[str, Any]] = [{"lane": _text(r, "lane", builder),
                  "temperature": _text(r, "temperature", builder)} for r in rows]
        state = {_text(r, "previous_receipt_state", builder) for r in rows}
        if ({x["temperature"] for x in content_lanes} != {"local", "cold"}
                or len({x["lane"] for x in content_lanes}) != 2):
            raise InputUnavailable(builder, "rotation is not one distinct local and one cold lane")
        if state != {"absent"}:
            raise InputUnavailable(builder, "weekly content-fuel receipt is not absent")
        return {"lanes": content_lanes, "freshness_cutoff": _cutoff(rows, builder),
                "previous_receipt_state": "absent"}
    if builder == "npi.weekly-delta":
        npi_lanes: list[dict[str, Any]] = [{"lane": _text(r, "lane", builder),
                  "territory_match": _bool(r, "territory_match", builder),
                  "entity_type": _text(r, "entity_type", builder)} for r in rows]
        state = {_text(r, "delta_state", builder) for r in rows}
        if not all(x["territory_match"] and x["entity_type"] == "healthcare_provider" for x in npi_lanes):
            raise InputUnavailable(builder, "NPI rows fail territory or healthcare-provider predicate")
        if state != {"unprocessed"}:
            raise InputUnavailable(builder, "NPI delta is not unprocessed")
        return {"lanes": npi_lanes, "freshness_cutoff": _cutoff(rows, builder),
                "delta_state": "unprocessed"}
    assert builder == "radar.weekly-candidates"
    radar_lanes: list[dict[str, Any]] = [{"lane": _text(r, "lane", builder), "score": _number(r, "score", builder),
              "fresh": _bool(r, "fresh", builder), "overdue": _bool(r, "overdue", builder)} for r in rows]
    state = {_text(r, "previous_receipt_state", builder) for r in rows}
    if not all(x["fresh"] for x in radar_lanes) or not any(x["overdue"] for x in radar_lanes):
        raise InputUnavailable(builder, "radar rows fail freshness or overdue-pool predicate")
    if state != {"absent"}:
        raise InputUnavailable(builder, "weekly radar receipt is not absent")
    return {"lanes": radar_lanes, "freshness_cutoff": _cutoff(rows, builder),
            "previous_receipt_state": "absent"}
