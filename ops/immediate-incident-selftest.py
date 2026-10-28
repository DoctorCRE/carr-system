#!/usr/bin/env python3
"""Hermetic acceptance checks for immediate failure-to-incident wiring."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
RECORD = REPO / "tools" / "ops-record.py"
FAILURES: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=FAILURES)


# The day the fake server counts under. Any fixed value works — the point is
# that it comes from the server's answer rather than from this process's clock.
SERVER_DAY = "20260819"


class FakeCursor:
    """Only the incident statements used by the focused helper."""

    def __init__(self) -> None:
        self.incidents: list[dict] = []
        self.links: set[tuple[str, str, str]] = set()
        self.facts: list[tuple[str, str, str]] = []
        self.events: list[str] = []
        self._one: tuple[Any, ...] | None = None

    def execute(self, sql: str, params=()) -> None:
        normalized = " ".join(sql.split())
        if "pg_advisory_xact_lock" in normalized:
            if "ops.incident.ref-allocation" in normalized:
                self.events.append("ref_lock")
            else:
                self.events.append("correlation_lock")
            self._one = (None,)
        elif ("from ops.incident i" in normalized
              and "i.correlation_id = %s" in normalized
              and normalized.startswith("select i.id")):
            self.events.append("correlation_lookup")
            corr = params[0]
            correlation_ref = f"correlation:{corr}"
            row = next((
                x for x in self.incidents
                if x["correlation"] == corr
                or any(incident == x["id"] and source_ref == correlation_ref
                       for incident, _, source_ref in self.facts)
            ), None)
            self._one = (row["id"],) if row else None
        elif "from ops.incident" in normalized and "signature = %s" in normalized:
            signature = params[0]
            row = next((x for x in self.incidents if x["signature"] == signature), None)
            self._one = (row["id"],) if row else None
        elif "coalesce(max(substring(ref" in normalized:
            # TWO values since 2026-08-18: the day and the sequence now come
            # out of the same answer, so the label cannot disagree with the
            # number it was counted against on a server that is not on UTC.
            # See ops/incident-numbering-selftest.py.
            self.events.append("ref_allocate")
            self._one = (SERVER_DAY, len(self.incidents) + 1)
        elif normalized.startswith("insert into ops.incident "):
            incident_id = f"incident-{len(self.incidents) + 1}"
            self.incidents.append({
                "id": incident_id,
                "correlation": params[1],
                "signature": params[-2],
                # A new incident opens at one occurrence, which is what the real
                # insert writes. Anything above one here had to be counted.
                "occurrences": 1,
                "state": "detected",
                "recovery_evidence_ref": None,
                "monitoring_until": None,
            })
            self._one = (incident_id,)
        elif normalized.startswith("insert into ops.incident_link "):
            link = (params[0], params[1], params[2])
            inserted = link not in self.links
            self.links.add(link)
            self._one = (params[0],) if inserted else None
        elif (normalized.startswith("insert into ops.incident_fact ")
              and "select %s, %s, %s" in normalized):
            incident_id, fact, source_ref = params[:3]
            original = next(x for x in self.incidents if x["id"] == incident_id)
            inserted = (original["correlation"] != params[4]
                        and not any(i == incident_id and ref == source_ref
                                    for i, _, ref in self.facts))
            if inserted:
                self.facts.append((incident_id, fact, source_ref))
            self._one = ("fact-correlation",) if inserted else None
        elif normalized.startswith("insert into ops.incident_fact "):
            self.facts.append((params[0], params[1], params[2]))
            self._one = None
        elif normalized.startswith("update ops.incident set occurrence_count"):
            # The recurrence heartbeat (migration 0293). It runs only when a
            # NEW piece of evidence attached to an incident that was already
            # open, so counting it here is what proves the writer does not
            # inflate the number on a replayed row.
            incident_id = params[-1]
            row = next(x for x in self.incidents if x["id"] == incident_id)
            row["occurrences"] += 1
            self.events.append("occurrence_bump")
            # A failure recorded against an incident being watched means the
            # watch found something, so the row goes back to detected.
            if row["state"] == "monitoring":
                row["state"] = "detected"
                row["recovery_evidence_ref"] = None
                row["monitoring_until"] = None
            self._one = None
        else:
            raise AssertionError(f"unexpected SQL: {normalized[:180]}")

    def fetchone(self):
        return self._one


def main() -> int:
    print("immediate-incident-selftest: failed writes raise one linked incident")
    spec = importlib.util.spec_from_file_location("ops_record_incident_test", RECORD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    helper = getattr(module, "_record_failure_incident", None)
    check("1. one shared immediate-incident helper exists", callable(helper))
    if not callable(helper):
        return 1

    cur = FakeCursor()
    common = dict(
        cur=cur,
        service_id="service-1",
        service_key="carr-mcp",
        criticality="critical",
        environment="production",
    )
    helper(**common, correlation_id="11111111-1111-4111-8111-111111111111",
           source_kind="run", source_id="run-1", source_label="golden.performance",
           state="timed_out", failure_class="performance_gate_failed",
           detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="11111111-1111-4111-8111-111111111111",
           source_kind="deployment", source_id="deployment-1",
           source_label="worker promotion", state="failed",
           failure_class="production_readback_mismatch",
           detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="22222222-2222-4222-8222-222222222222",
           source_kind="run", source_id="run-2", source_label="backup.verify",
           state="failed", failure_class="backup_verification_failed",
           detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="11111111-1111-4111-8111-111111111111",
           source_kind="run", source_id="run-1", source_label="golden.performance",
           state="timed_out", failure_class="performance_gate_failed",
           detail="CANARY-CLIENT-SECRET")

    check("2. same correlation deduplicates to one open incident",
          len(cur.incidents) == 2, f"incidents={cur.incidents}")
    check("2a. each helper call locks correlation before incident lookup",
          cur.events.count("correlation_lock") == 4
          and cur.events[0:2] == ["correlation_lock", "correlation_lookup"],
          f"events={cur.events}")
    ref_locks = [i for i, event in enumerate(cur.events) if event == "ref_lock"]
    ref_allocations = [i for i, event in enumerate(cur.events)
                       if event == "ref_allocate"]
    check("2aa. new refs serialize globally before max+1 allocation",
          len(ref_locks) == len(ref_allocations) == 2
          and all(lock_at < alloc_at
                  for lock_at, alloc_at in zip(ref_locks, ref_allocations)),
          f"events={cur.events}")
    first_id = cur.incidents[0]["id"] if cur.incidents else "missing"
    first_links = {(kind, ref) for incident, kind, ref in cur.links
                   if incident == first_id}
    check("2b. failed run and failed deployment both link to that incident",
          first_links == {("run", "run-1"), ("deployment", "deployment-1")},
          f"links={first_links}")
    check("2c. each new link adds one sourced fact", len(cur.facts) == 3)
    check("2cc. replaying the same source link adds no duplicate fact",
          len(cur.links) == 3 and len(cur.facts) == 3)
    check("2d. facts are redacted rather than copying caller detail",
          all("CANARY-CLIENT-SECRET" not in text for _, text, _ in cur.facts))

    recurring = FakeCursor()
    recurring_common = dict(common, cur=recurring)
    old_corr = "55555555-5555-4555-8555-555555555555"
    new_corr = "66666666-6666-4666-8666-666666666666"
    helper(**recurring_common, correlation_id=old_corr,
           source_kind="run", source_id="run-old",
           source_label="performance.release", state="failed",
           failure_class="performance_budget_exceeded", detail=None)
    recurring.incidents[0].update(
        state="monitoring", recovery_evidence_ref="ops.run:old-green",
        monitoring_until=module.datetime(2026, 8, 26, tzinfo=module.timezone.utc))
    helper(**recurring_common, correlation_id=new_corr,
           source_kind="run", source_id="run-new",
           source_label="performance.release", state="failed",
           failure_class="performance_budget_exceeded", detail=None)
    helper(**recurring_common, correlation_id=new_corr,
           source_kind="deployment", source_id="deployment-new",
           source_label="deployment", state="failed",
           failure_class="production_readback_mismatch", detail=None)
    recurring_links = {(kind, ref) for _, kind, ref in recurring.links}
    correlation_facts = [ref for _, _, ref in recurring.facts
                         if ref.startswith("correlation:")]
    check("2e. a recurring run and its new-correlation deployment stay one journey",
          len(recurring.incidents) == 1
          and recurring_links == {
              ("run", "run-old"), ("run", "run-new"),
              ("deployment", "deployment-new")},
          f"incidents={recurring.incidents} links={recurring_links}")
    check("2f. recurrence correlation is traceable and idempotent",
          correlation_facts == [f"correlation:{new_corr}"],
          f"correlation facts={correlation_facts}")
    # Migration 0293. Before it, all three of these arrivals left the row
    # reading exactly as it read after the first one, so a reader could not
    # tell a job failing constantly from one that failed once.
    check("2g. each recurrence counts on the open row rather than opening a new one",
          recurring.incidents[0]["occurrences"] == 3,
          f"occurrences={recurring.incidents[0]['occurrences']}")
    check("2h. the first arrival opens at one occurrence and counts nothing",
          recurring.events.count("occurrence_bump") == 2,
          f"events={recurring.events}")
    check("2ha. a new linked failure invalidates recovery and returns monitoring to detected",
          recurring.incidents[0]["state"] == "detected"
          and recurring.incidents[0]["recovery_evidence_ref"] is None
          and recurring.incidents[0]["monitoring_until"] is None,
          f"incident={recurring.incidents[0]}")

    # A later green observation is allowed to establish a new recovery. The
    # recurrence invalidates stale evidence; it does not poison the row or
    # bypass the ordinary close preconditions.
    recurring.incidents[0].update(
        state="monitoring", recovery_evidence_ref="ops.run:new-green",
        monitoring_until=module.datetime(2026, 8, 24, tzinfo=module.timezone.utc))
    later_ok, later_error, _ = module.resolve_preconditions(
        recurring.incidents[0], root_cause="new recovery held",
        now=module.datetime(2026, 8, 25, tzinfo=module.timezone.utc))
    check("2hb. a new recovery can later satisfy the existing close guard",
          later_ok, later_error or "")

    # A replayed row is the case the count must survive: tools/ops-spool.py
    # retries anything it could not land, and ops.incident_link's primary key
    # already refuses the second link. If the bump were counted off the CALL
    # instead of off that refusal, a retried flush would inflate the number
    # while adding no new evidence.
    replayed = FakeCursor()
    replay_common = dict(common, cur=replayed)
    for _ in range(3):
        helper(**replay_common,
               correlation_id="77777777-7777-4777-8777-777777777777",
               source_kind="run", source_id="run-replayed",
               source_label="launchd.run", state="failed",
               failure_class="exit_1", detail=None)
    check("2i. replaying one run row does not inflate the count",
          len(replayed.incidents) == 1
          and replayed.incidents[0]["occurrences"] == 1
          and len(replayed.links) == 1,
          f"occurrences={replayed.incidents[0]['occurrences']} "
          f"links={len(replayed.links)}")

    protected = FakeCursor()
    protected_common = dict(common, cur=protected)
    helper(**protected_common,
           correlation_id="88888888-8888-4888-8888-888888888888",
           source_kind="run", source_id="run-protected",
           source_label="launchd.run", state="failed",
           failure_class="exit_1", detail=None)
    protected.incidents[0].update(
        state="monitoring", recovery_evidence_ref="ops.run:protected-green",
        monitoring_until=module.datetime(2026, 8, 26, tzinfo=module.timezone.utc))
    helper(**protected_common,
           correlation_id="88888888-8888-4888-8888-888888888888",
           source_kind="run", source_id="run-protected",
           source_label="launchd.run", state="failed",
           failure_class="exit_1", detail=None)
    check("2j. replaying the same ledger row preserves monitoring and its recovery evidence",
          protected.incidents[0]["state"] == "monitoring"
          and protected.incidents[0]["recovery_evidence_ref"] == "ops.run:protected-green"
          and protected.incidents[0]["monitoring_until"]
              == module.datetime(2026, 8, 26, tzinfo=module.timezone.utc),
          f"incident={protected.incidents[0]}")

    before = (len(cur.incidents), len(cur.links), len(cur.facts))
    helper(**common, correlation_id="33333333-3333-4333-8333-333333333333",
           source_kind="run", source_id="run-skip", source_label="optional.step",
           state="skipped", failure_class=None, detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="44444444-4444-4444-8444-444444444444",
           source_kind="run", source_id="run-cancel", source_label="cancelled.step",
           state="cancelled", failure_class=None, detail="CANARY-CLIENT-SECRET")
    check("3. skipped and cancelled observations open nothing",
          before == (len(cur.incidents), len(cur.links), len(cur.facts)))

    source = RECORD.read_text(encoding="utf-8")
    deployment_body = source[source.index("def cmd_deployment"):source.index("def cmd_settings_change")]
    check("4. run and deployment call the helper in their insert transaction",
          source.count("_record_failure_incident(") >= 4
          and source.count("conn.transaction()") >= 2
          and "_record_failure_incident(" in deployment_body)
    check("4a. helper takes a correlation advisory transaction lock",
          "pg_advisory_xact_lock" in source)
    check("4aa. helper takes the global ref-allocation lock after correlation",
          "ops.incident.ref-allocation" in source)
    check("4b. routine/write authority modes remain distinct",
          'connect("routine")' in source and 'connect("write")' in source)

    print()
    if FAILURES:
        print(f"immediate-incident-selftest: {len(FAILURES)} FAILED")
        return 1
    print("immediate-incident-selftest: immediate incidents are linked and deduplicated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
