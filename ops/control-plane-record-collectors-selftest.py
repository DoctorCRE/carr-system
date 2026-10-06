#!/usr/bin/env python3
"""Contract checks for canonical record cognition collectors (no database)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.control_plane_collectors_records import CanonicalRecordCollector, QUERIES
from lib.control_plane_inputs import InputUnavailable, NoEligibleRecords


class FakeReadOnlyAdapter:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def fetch_all(self, *, query_key, sql, params=()):
        self.calls.append((query_key, sql, tuple(params)))
        return self.rows.get(query_key, [])


def check(label, condition):
    if not condition: raise AssertionError(label)
    print(f"ok: {label}")


def _find_tiered_queue_migration() -> str:
    """Locate the migration that rewrote the two queue views into priority
    bands (0387), by content rather than a hardcoded filename -- a
    migration's number is only fixed once it is actually applied (README:
    "Forward-only, numbered"), so a rename before merge must not silently
    stop this check from running.
    """
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        text = path.read_text()
        if "lead_client_gaps" in text and "v_control_plane_enrichment_queue" in text:
            return text
    raise AssertionError("no migration defines the tiered enrichment/deal-history queues")


def check_queue_migration_bands() -> None:
    """Static, no-database check that the queue-rewrite migration's SQL
    source actually names every priority band the SKILL.md files require,
    excludes do_not_contact, and scopes the re-verification band to contact
    subject types. Complements (does not replace) the migration's own
    self-check DO block, which asserts the same facts against the deparsed
    view once it is actually applied to a database.
    """
    text = _find_tiered_queue_migration()
    check("migration reads the scoped re-verification queue",
          "v_expired_verification" in text)
    check("migration scopes subject_type to party/vendor/lead/client",
          "'party', 'vendor', 'lead', 'client'" in text)
    check("migration implements the vendor-needs-type band",
          "category_slug is null" in text)
    check("migration implements the vendor-missing-city/county band",
          "p.city is null or p.county is null" in text)
    check("migration implements the vendor-missing-verticals band",
          "v.verticals is null or cardinality(v.verticals) = 0" in text)
    check("migration implements the leads/clients-missing-fields band",
          "p.title is null or p.org_id is null or p.email is null" in text)
    check("migration implements the deal-history active_deal/engaged band",
          "c.status in ('active_deal', 'engaged')" in text)
    check("migration implements the deal-history roster_ref anomaly band",
          "c.roster_ref is null" in text)
    check("every band excludes do_not_contact",
          text.count("do_not_contact") >= 6)


def main() -> int:
    check_queue_migration_bands()
    instant = datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    policies = {
        "entity_research_policy": {"mode":"direct-primary-sources", "proposal_only":True},
        "deal_history_research_policy": {"mode":"direct-identity-sources", "proposal_only":True},
    }
    enrichment: list[dict[str, Any]] = [{"subject_type":"party", "subject_id":str(i),
                   "reverification_due":"expired", "current_verification_status":"not_current",
                   "priority":i, "expired_at":f"2026-01-{i:02d}"} for i in range(1, 41)]
    rows = {
        "entity-enrichment.next-40": enrichment,
        "deal-history.next-slice": [{"subject_type":"candidate", "subject_id":str(i),
          "verification":"unverified", "priority":i + 1, "source_class":"canonical_counterparty",
          "slice_limit":15, "enrichment_subject_count":30,
          "enrichment_scheduled_for":"2026-08-13T14:00:00+00:00",
          "enrichment_mode":"shadow", "sizing_state":"receipt_bound"} for i in range(15)],
        "content-fuel.next-rotation": [
          {"lane":"local", "temperature":"local", "source_class":"primary", "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"},
          {"lane":"cold", "temperature":"cold", "source_class":"primary", "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"}],
        "npi.weekly-delta": [{"lane":"npi", "territory_match":True, "entity_type":"healthcare_provider", "freshness_cutoff":"2026-08-01T00:00:00Z", "delta_state":"unprocessed"}],
        "radar.weekly-candidates": [
          {"lane":"corp", "score":4, "fresh":True, "overdue":True, "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"},
          {"lane":"renewal", "score":2.5, "fresh":True, "overdue":False, "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"}],
    }
    adapter = FakeReadOnlyAdapter(rows)
    collector = CanonicalRecordCollector(adapter, scheduled_for=instant, mode="shadow", policies=policies)
    for key in QUERIES:
        evidence = collector.collect(builder_key=key, workflow_key="fixture")
        check(f"{key} emits canonical provenance", evidence[0]["source_kind"] == "canonical_db" and evidence[0]["source_ref"] == f"db:{key}")
        check(f"{key} has structured predicate input",
              isinstance(evidence[0].get("values"), dict))
    check("all collector queries use SELECT-only read surfaces",
          len(adapter.calls) == len(QUERIES) and all(call[1].lstrip().lower().startswith("select")
                                                   for call in adapter.calls))
    # 0387: a short week is the NORMAL end state as the book gets covered
    # (both SKILL.md files: "reports done and stops finding work"), so a
    # 1..40 queue -- not just an exact 40 -- must be accepted, never refused.
    short = dict(rows)
    short["entity-enrichment.next-40"] = enrichment[:12]
    evidence = CanonicalRecordCollector(FakeReadOnlyAdapter(short), scheduled_for=instant, mode="shadow",
                                        policies=policies).collect(builder_key="entity-enrichment.next-40", workflow_key="fixture")
    check("a 12-row enrichment queue is accepted, not refused",
          len(evidence[0]["values"]["subjects"]) == 12)
    full = dict(rows)
    full["entity-enrichment.next-40"] = enrichment
    evidence = CanonicalRecordCollector(FakeReadOnlyAdapter(full), scheduled_for=instant, mode="shadow",
                                        policies=policies).collect(builder_key="entity-enrichment.next-40", workflow_key="fixture")
    check("a full 40-row enrichment queue is accepted",
          len(evidence[0]["values"]["subjects"]) == 40)
    # A gap (or duplicate) in the priority sequence is a broken ordering,
    # not a short week, and must still refuse regardless of row count.
    gapped = dict(rows)
    gapped["entity-enrichment.next-40"] = [
        {**row, "priority": row["priority"] + 1 if row["priority"] >= 20 else row["priority"]}
        for row in enrichment[:-1]
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(gapped), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="entity-enrichment.next-40", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("a gap in the enrichment priority sequence fails closed", refused)
    duplicated = dict(rows)
    duplicated["entity-enrichment.next-40"] = [
        {**row, "priority": 5} if row["priority"] == 6 else row for row in enrichment
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(duplicated), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="entity-enrichment.next-40", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("a duplicate enrichment priority fails closed", refused)
    never_recorded = dict(rows)
    never_recorded["entity-enrichment.next-40"] = [
        {**row, "reverification_due": "not_recorded"} for row in enrichment[:5]
    ]
    evidence = CanonicalRecordCollector(FakeReadOnlyAdapter(never_recorded), scheduled_for=instant,
                                        mode="shadow", policies=policies).collect(
        builder_key="entity-enrichment.next-40", workflow_key="fixture")
    check("never-verified profile-gap subjects (reverification_due=not_recorded) are accepted",
          all(s["reverification_due"] == "not_recorded" for s in evidence[0]["values"]["subjects"]))
    empty = dict(rows)
    empty["entity-enrichment.next-40"] = []
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(empty), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="entity-enrichment.next-40", workflow_key="fixture")
    except NoEligibleRecords: clean_no_work = True
    except InputUnavailable: clean_no_work = False
    else: clean_no_work = False
    check("an empty enrichment queue is a clean no-work signal, not a refusal", clean_no_work)
    bad = dict(rows)
    bad["npi.weekly-delta"] = [{"lane":"npi", "territory_match":False,
                                 "entity_type":"healthcare_provider",
                                 "freshness_cutoff":"2026-08-01T00:00:00Z",
                                 "delta_state":"unprocessed"}]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(bad), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="npi.weekly-delta", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("semantic predicate violation fails closed", refused)
    tail = dict(rows)
    tail["deal-history.next-slice"] = [{"subject_type":"party", "subject_id":str(i),
        "verification":"unverified", "priority":i + 1, "source_class":"canonical_counterparty",
        "slice_limit":25, "enrichment_subject_count":12,
        "enrichment_scheduled_for":"2026-08-13T14:00:00+00:00",
        "enrichment_mode":"shadow", "sizing_state":"receipt_bound"} for i in range(3)]
    evidence = CanonicalRecordCollector(FakeReadOnlyAdapter(tail), scheduled_for=instant,
                                        mode="shadow", policies=policies).collect(
        builder_key="deal-history.next-slice", workflow_key="fixture")
    check("final partial deal-history slice stays within its canonical cap",
          evidence[0]["values"]["slice_limit"] == 25 and len(evidence[0]["values"]["subjects"]) == 3)
    fabricated = dict(rows)
    fabricated["entity-enrichment.next-40"] = [
        {**row, "current_verification_status":"current"} for row in enrichment
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(fabricated), scheduled_for=instant,
                                 mode="shadow", policies=policies).collect(
            builder_key="entity-enrichment.next-40", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("expired evidence cannot be relabeled as currently verified", refused)
    wrong_mode = dict(rows)
    deal_rows = rows["deal-history.next-slice"]
    assert isinstance(deal_rows, list)
    wrong_mode["deal-history.next-slice"] = [
        {**row, "enrichment_mode":"canary"} for row in deal_rows
        if isinstance(row, dict)
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(wrong_mode), scheduled_for=instant,
                                 mode="shadow", policies=policies).collect(
            builder_key="deal-history.next-slice", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("another mode's Thursday receipt cannot size this job", refused)
    missing_receipt = dict(rows)
    missing_receipt["deal-history.next-slice"] = [
        {**row, "sizing_state":"receipt_missing"} for row in deal_rows
        if isinstance(row, dict)
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(missing_receipt), scheduled_for=instant,
                                 mode="shadow", policies=policies).collect(
            builder_key="deal-history.next-slice", workflow_key="fixture")
    except InputUnavailable:
        refused = True
    else:
        refused = False
    check("visible receipt-missing backlog cannot become an execution slice", refused)

    # 0387: the same short-week/malformed-order/empty-queue coverage as
    # entity-enrichment.next-40 above, for deal-history.next-slice.
    twelve = dict(rows)
    twelve["deal-history.next-slice"] = [{"subject_type":"client", "subject_id":str(i),
        "verification":"unverified", "priority":i + 1, "source_class":"canonical_counterparty",
        "slice_limit":25, "enrichment_subject_count":12,
        "enrichment_scheduled_for":"2026-08-13T14:00:00+00:00",
        "enrichment_mode":"shadow", "sizing_state":"receipt_bound"} for i in range(12)]
    evidence = CanonicalRecordCollector(FakeReadOnlyAdapter(twelve), scheduled_for=instant,
                                        mode="shadow", policies=policies).collect(
        builder_key="deal-history.next-slice", workflow_key="fixture")
    check("a 12-row deal-history queue is accepted, not refused",
          len(evidence[0]["values"]["subjects"]) == 12)
    dh_gapped = dict(rows)
    dh_gapped["deal-history.next-slice"] = [
        {**row, "priority": row["priority"] + 1 if row["priority"] >= 8 else row["priority"]}
        for row in deal_rows if isinstance(row, dict)
    ][:-1]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(dh_gapped), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="deal-history.next-slice", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("a gap in the deal-history priority sequence fails closed", refused)
    dh_duplicated = dict(rows)
    dh_duplicated["deal-history.next-slice"] = [
        {**row, "priority": 3} if row.get("priority") == 4 else row
        for row in deal_rows if isinstance(row, dict)
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(dh_duplicated), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="deal-history.next-slice", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("a duplicate deal-history priority fails closed", refused)
    dh_empty = dict(rows)
    dh_empty["deal-history.next-slice"] = []
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(dh_empty), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="deal-history.next-slice", workflow_key="fixture")
    except NoEligibleRecords: clean_no_work = True
    except InputUnavailable: clean_no_work = False
    else: clean_no_work = False
    check("an empty deal-history queue is a clean no-work signal, not a refusal", clean_no_work)
    return 0


if __name__ == "__main__": raise SystemExit(main())
