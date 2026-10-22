#!/usr/bin/env python3
"""Consolidation stays a reviewed, replayable record-verb operation."""
import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("consolidate", Path(__file__).with_name("jev-outage-consolidate.py"))
assert spec is not None and spec.loader is not None
consolidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consolidate)


class Records:
    def __init__(self):
        self.rows = {str(n): {"loop_id": f"loop-{n}", "number": str(n), "kind": "open_loop",
                     "domain": "system", "status": "open", "owner": "claude", "body": consolidate.BODY,
                     "blocker_class": "other_lane", "blocker_detail": "Restore Jev evidence files",
                     "source_note": None, "version": 1, "created_at": f"2026-10-{n:02}T00:00:00Z"}
                     for n in (1, 2, 3)}
        self.rows["4"] = dict(self.rows["3"], loop_id="loop-4", number="4", body="Edited by a partner")
        self.writes = []
        self.fail_close = None

    def __call__(self, name, payload):
        if name == "loop-board":
            rows = [dict(row, label=row["body"]) for row in self.rows.values() if row["status"] == "open"]
            return {"count": len(rows), "loops": rows}
        if name == "read-loop" and "source_note" in payload:
            row = next((row for row in self.rows.values()
                        if row["source_note"] == payload["source_note"] and row["status"] == "open"), None)
            return {"loop": copy.deepcopy(row)}
        row = (self.rows[payload["number"]] if "number" in payload else
               next(row for row in self.rows.values() if row["loop_id"] == payload["loop_id"]))
        if name == "read-loop":
            return {"loop": copy.deepcopy(row)}
        self.writes.append((name, dict(payload)))
        if name == "close-loop" and payload["loop_id"] == self.fail_close:
            self.fail_close = None
            raise RuntimeError("lost response")
        assert row["version"] == payload["base_version"]
        row["version"] += 1
        if name == "update-loop":
            row["source_note"] = payload["source_note"]
        elif name == "close-loop":
            row.update(status=payload["resolution"], close_outcome=payload["outcome"])
        else:
            raise AssertionError(name)
        return {"ok": True}


class ConsolidationTests(unittest.TestCase):
    def test_dry_run_selects_oldest_and_excludes_edited_row(self):
        records = Records()
        plan = consolidate.collect(records)
        self.assertEqual(plan["canonical"]["number"], "1")
        self.assertEqual(plan["counts"], {"matched": 3, "retain": 1, "supersede": 2, "excluded": 1})
        self.assertEqual(records.writes, [])

    def test_existing_tagged_incident_stays_canonical(self):
        records = Records()
        records.rows["2"]["source_note"] = consolidate.INCIDENT_IDENTITY
        self.assertEqual(consolidate.collect(records)["canonical"]["number"], "2")

    def test_changed_failure_reason_does_not_hide_existing_canonical(self):
        records = Records()
        records.rows["4"].update(source_note=consolidate.INCIDENT_IDENTITY, body="TypeSafe billing failure",
                                  blocker_class="human_only", owner="Joe")
        plan = consolidate.collect(records)
        self.assertEqual(plan["canonical"]["number"], "4")
        self.assertEqual(plan["counts"]["supersede"], 3)
        self.assertEqual(consolidate.apply(plan, records, merged=lambda _: None)["retained"], "4")

    def test_apply_refuses_before_merge_and_sends_no_mutations(self):
        records = Records()
        def unmerged(_):
            raise ValueError("PR is not merged")
        with self.assertRaisesRegex(ValueError, "not merged"):
            consolidate.apply(consolidate.collect(records), records, merged=unmerged)
        self.assertEqual(records.writes, [])

    def test_apply_preserves_history_with_explicit_successors(self):
        records = Records()
        plan = consolidate.collect(records)
        result = consolidate.apply(plan, records, merged=lambda _: None)
        self.assertEqual(result, {"retained": "1", "superseded": 2, "verified": True})
        self.assertEqual(records.rows["1"]["status"], "open")
        self.assertEqual(records.rows["4"]["status"], "open")
        for row in (records.rows["2"], records.rows["3"]):
            self.assertEqual(row["body"], consolidate.BODY)
            self.assertEqual(row["status"], "dropped")
            self.assertTrue(row["close_outcome"].startswith("SUPERSEDED by Jev outage loop #1"))
        self.assertEqual({p["successor_loop"] for n, p in records.writes if n == "close-loop"}, {"loop-1"})

    def test_partial_apply_reads_progress_before_retry(self):
        records = Records()
        plan = consolidate.collect(records)
        records.fail_close = "loop-3"
        with self.assertRaisesRegex(RuntimeError, "lost response"):
            consolidate.apply(plan, records, merged=lambda _: None)
        consolidate.apply(plan, records, merged=lambda _: None)
        self.assertEqual(sum(name == "update-loop" for name, _ in records.writes), 1)
        self.assertEqual(sum(p.get("loop_id") == "loop-2" for _, p in records.writes), 1)
        closes = [p for name, p in records.writes if name == "close-loop" and p["loop_id"] == "loop-3"]
        self.assertEqual(closes[0], closes[1])

    def test_changed_snapshot_aborts_before_any_mutation(self):
        records = Records()
        plan = consolidate.collect(records)
        records.rows["3"]["version"] += 1
        with self.assertRaisesRegex(ValueError, "changed"):
            consolidate.apply(plan, records, merged=lambda _: None)
        self.assertEqual(records.writes, [])

    def test_new_active_canonical_aborts_old_plan(self):
        records = Records()
        plan = consolidate.collect(records)
        records.rows["2"]["source_note"] = consolidate.INCIDENT_IDENTITY
        with self.assertRaisesRegex(ValueError, "another canonical"):
            consolidate.apply(plan, records, merged=lambda _: None)
        self.assertEqual(records.writes, [])

    def test_apply_requires_the_deployed_identity_contract(self):
        records = Records()
        plan = consolidate.collect(records)
        def old_worker(name, payload):
            if name == "read-loop" and "source_note" in payload:
                return {"loop": None, "identity_lookup": "unavailable_before_merge"}
            return records(name, payload)
        with self.assertRaisesRegex(ValueError, "deploy"):
            consolidate.apply(plan, old_worker, merged=lambda _: None)
        self.assertEqual(records.writes, [])

    def test_capped_selection_is_not_a_complete_plan(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            consolidate.collect(lambda *_: {"count": 300, "loops": []})


if __name__ == "__main__":
    unittest.main()
