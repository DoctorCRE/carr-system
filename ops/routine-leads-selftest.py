#!/usr/bin/env python3
import copy
import csv
import io
import json
import sys
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.routines import lead_signals as leads

FIXTURE = json.loads((ROOT / "ops/fixtures/routines/leads.json").read_text())
NOW = datetime.fromisoformat(FIXTURE["as_of"])


def context(fixture=None, dry_run=True):
    calls = []
    def write(verb, args, key):
        calls.append((verb, args, key))
        return {"party_id": "test-party", "ref": "L-TEST", "ok": True}
    def read(*_):
        raise AssertionError("fixture run must not read records")
    def review(title, body, key):
        calls.append(("review", title, body, key))
        return {"ok": True}
    return SimpleNamespace(fixture=fixture, dry_run=dry_run, now=NOW, read=read, write=write, review_item=review, calls=calls)


class LeadTests(unittest.TestCase):

    def test_consumed_candidates_exit_without_repeat_effects(self):
        fixture = copy.deepcopy(FIXTURE)
        first = leads.prepare(context(fixture))
        fixture["consumed_keys"] = [r["source_key"] for r in first["candidates"]]
        ctx = context(fixture)
        repeated = leads.prepare(ctx)
        self.assertFalse(repeated["work"])
        self.assertEqual(repeated["candidates"], [])
        self.assertEqual(ctx.calls, [])

    def test_territory_does_not_take_wrong_state_or_missing_geography(self):
        self.assertTrue(leads.in_territory({"city": "Pensacola", "state": "FL"}))
        self.assertTrue(leads.in_territory({"county": "Baldwin County", "state": "AL"}))
        self.assertFalse(leads.in_territory({"city": "Mobile", "state": "CA"}))
        self.assertFalse(leads.in_territory({"state": "FL"}))

    def test_nppes_includes_weak_and_unknown_taxonomy(self):
        rows = leads.parse_nppes(FIXTURE["nppes_rows"], as_of=NOW.date(), source=leads.INDEX_URL)
        self.assertEqual({r["npi"] for r in rows}, {"1000000001", "1000000004"})
        self.assertEqual(len(leads.candidates(rows)), 2)
        weak = next(r for r in leads.candidates(rows) if r["npi"] == "1000000004")
        self.assertEqual(weak["score"], 1)

    def test_enumeration_window_boundaries_and_future(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][0])
        for enumeration, expected in (("09/21/2026", 1), ("09/20/2026", 0), ("10/06/2026", 0)):
            raw["Provider Enumeration Date"] = enumeration
            self.assertEqual(len(leads.parse_nppes([raw], as_of=NOW.date(), source=leads.INDEX_URL)), expected)
        raw["Provider Enumeration Date"] = "not-a-date"
        with self.assertRaises(ValueError):
            leads.parse_nppes([raw], as_of=NOW.date(), source=leads.INDEX_URL)

    def test_weekly_publications_reject_stale_and_wrong_host(self):
        link = '<a href="NPPES_Data_Dissemination_092826_100426_Weekly_V2.zip">weekly</a>'
        self.assertEqual(len(leads.weekly_files(link, NOW.date())), 1)
        with self.assertRaises(ValueError):
            leads.weekly_files(link, NOW.date().replace(month=11))
        with self.assertRaises(ValueError):
            leads.weekly_files(link.replace('href="', 'href="https://wrong.example/'), NOW.date())

    def test_secondary_practice_location_archive_parser(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][2])
        location = {"NPI": raw["NPI"], "Provider Secondary Practice Location Address - City Name": "Pensacola", "Provider Secondary Practice Location Address - State Name": "FL", "Provider Secondary Practice Location Address - Postal Code": "32501"}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for filename, row in (("npidata_pfile_20260928-20261004.csv", raw), ("pl_pfile_20260928-20261004.csv", location)):
                text = io.StringIO()
                writer = csv.DictWriter(text, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
                archive.writestr(filename, text.getvalue())
        rows = leads.parse_weekly_zip(output.getvalue(), as_of=NOW.date(), source=leads.INDEX_URL)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["city"], "Pensacola")
        self.assertFalse(rows[0]["mailing_only"])
        with self.assertRaises(zipfile.BadZipFile):
            leads.parse_weekly_zip(b"not-a-zip", as_of=NOW.date(), source=leads.INDEX_URL)

    def test_mailing_only_is_visible_with_lower_estimate(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][2])
        raw.update({"Provider Business Mailing Address Postal Code": "32501", "Provider Business Mailing Address State Name": "FL"})
        rows = leads.candidates(leads.parse_nppes([raw], as_of=NOW.date(), source=leads.INDEX_URL))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], 1)

    def test_empty_predicate_and_dry_run_no_reads_or_writes(self):
        empty = context({"nppes_rows": [], "pools": {}, "claims": []})
        self.assertFalse(leads.prepare(empty)["work"])
        ctx = context(FIXTURE)
        plan = leads.prepare(ctx)
        result = leads.execute(ctx, plan)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(ctx.calls, [])

    def test_write_estimates_only_to_new_stage(self):
        ctx = context(FIXTURE, dry_run=False)
        plan = leads.prepare(ctx)
        leads.execute(ctx, plan)
        writes = [c for c in ctx.calls if c[0] == "new-lead"]
        self.assertEqual(len(writes), 2)
        self.assertTrue(all(c[1]["stage"] == "new" for c in writes))
        self.assertTrue(all(1 <= c[1]["score"] <= 10 for c in writes))
        keys = [c[2] for c in writes]
        ctx.calls.clear()
        leads.execute(ctx, plan)
        self.assertEqual(keys, [c[2] for c in ctx.calls if c[0] == "new-lead"])

    def test_ambiguous_identity_goes_to_app(self):
        ctx = context(FIXTURE, dry_run=False)
        ctx.write = lambda *_: {"needs_confirm": True, "candidates": [{"name": "Possible match"}]}
        result = leads.execute(ctx, leads.prepare(ctx))
        self.assertEqual(len(result["created"]), 0)
        self.assertEqual(len(result["reviews"]), 2)



if __name__ == "__main__":
    unittest.main()
