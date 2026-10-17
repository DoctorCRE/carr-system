#!/usr/bin/env python3
import copy
import csv
import io
import json
import sys
import subprocess
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.routines import lead_signals as leads, runtime
from lib.record_call import VerbResult, OK

FIXTURE = json.loads((ROOT / "ops/fixtures/routines/leads.json").read_text())
NOW = datetime.fromisoformat(FIXTURE["as_of"])


def validate_payload(verb, payload):
    result = subprocess.run(["node", "--input-type=module", "-e", """
        import { TOOLS, assertRequiredArgs, coerceArgsToSchema } from './mcp-server/src/tools.js';
        import { assertClosedTopLevel } from './mcp-server/src/mutation-registry.js';
        let input = '';
        for await (const chunk of process.stdin) input += chunk;
        const {verb, payload} = JSON.parse(input);
        const tool = TOOLS[verb];
        if (!tool) throw new Error('unregistered verb: ' + verb);
        assertClosedTopLevel(verb, tool, payload);
        assertRequiredArgs(tool.inputSchema, payload);
        coerceArgsToSchema(tool.inputSchema, payload);
    """], input=json.dumps({"verb": verb, "payload": payload}), cwd=ROOT,
        capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise AssertionError(result.stderr)


def context(fixture=None, dry_run=True):
    ctx = runtime.Context("lead-signals-weekly", fixture=fixture, dry_run=dry_run, now=NOW)
    ctx.calls = []
    write = ctx.write
    def transport(verb, payload, *, profile):
        validate_payload(verb, payload)
        return VerbResult(verb, OK, {"ok": True, "loop_id": "fixture-loop"})
    def recorded_write(verb, args, key):
        with patch.object(runtime, "call_verb", side_effect=transport):
            result = write(verb, args, key)
        ctx.calls.append((verb, args, key))
        return result
    ctx.write = recorded_write
    return ctx


class LeadTests(unittest.TestCase):

    def test_real_contract_refuses_legacy_lead_field_and_unregistered_loop_field(self):
        with self.assertRaisesRegex(AssertionError, "unregistered_operation_fields"):
            validate_payload("new-lead", {"idempotency_key": "fixture", "party_id": "fixture", "score_basis": "estimate"})
        with self.assertRaisesRegex(AssertionError, "unregistered_operation_fields"):
            validate_payload("add-loop", {"idempotency_key": "fixture", "kind": "team_loop", "owner": "joe", "section": "hot"})

    def test_live_path_payloads_use_real_contract_and_restart_has_no_repeat_effects(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][0])
        raw.update({"Authorized Official First Name": "Example", "Authorized Official Last Name": "Principal"})
        csv_text = io.StringIO()
        writer = csv.DictWriter(csv_text, fieldnames=list(raw))
        writer.writeheader()
        writer.writerow(raw)
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            archive.writestr("npidata_pfile_20260928-20261004.csv", csv_text.getvalue())
        calls = []
        def transport(verb, payload, *, profile):
            validate_payload(verb, payload)
            calls.append((verb, payload))
            return VerbResult(verb, OK, {"parties": [], "organizations": []} if verb == "find" else {"ok": True})
        def fetch(url):
            return b'<a href="NPPES_Data_Dissemination_092826_100426_Weekly_V2.zip">weekly</a>' if url == leads.INDEX_URL else zipped.getvalue()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            journal = root / "state.json"
            ctx = runtime.Context("lead-signals-weekly", now=NOW, journal=journal)
            with patch.object(leads, "ROOT", root), patch.object(leads, "_fetch", side_effect=fetch), patch.object(runtime, "call_verb", side_effect=transport), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(runtime.run_module(ctx, leads), 0)
                self.assertEqual([args["query"] for verb, args in calls if verb == "find"], ["Example Dental", "Example Principal"])
                self.assertEqual([verb for verb, _ in calls if verb != "find"], ["add-loop", "add-loop"])
                self.assertEqual({effect["verb"] for effect in json.loads(journal.read_text())["effects"].values()}, {"add-loop"})
                calls.clear()
                restarted = runtime.Context("lead-signals-weekly", now=NOW, journal=journal)
                self.assertEqual(runtime.execute(restarted), 0)
                self.assertFalse(leads.prepare(restarted)["work"])
                self.assertEqual(calls, [])

    def test_failure_payloads_also_pass_real_verb_contracts(self):
        calls = []
        def transport(verb, payload, *, profile):
            validate_payload(verb, payload)
            calls.append((verb, payload))
            return VerbResult(verb, OK, {"loops": []} if verb == "loop-board" else {"ok": True})
        with patch.object(runtime, "call_verb", side_effect=transport):
            runtime.Context("lead-signals-weekly", now=NOW).failure("fixture source failure")
        self.assertEqual([verb for verb, _ in calls], ["loop-board", "add-loop"])
        self.assertEqual(calls[1][1]["blocker"], "other_lane")

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

    def test_unattended_run_emits_proposals_without_intake(self):
        ctx = context(FIXTURE, dry_run=False)
        plan = leads.prepare(ctx)
        result = leads.execute(ctx, plan)
        self.assertEqual({c[0] for c in ctx.calls}, {"add-loop"})
        self.assertEqual(len(result["proposed"]), 2)
        self.assertTrue(all(c[1]["kind"] == "team_loop" for c in ctx.calls))
        self.assertTrue(all(c[1]["marker"] == "none" and c[1]["owner"] == "claude" for c in ctx.calls))

    def test_sop_classification_including_synthetic_hospital(self):
        for case in FIXTURE["classification_cases"]:
            with self.subTest(case=case["name"]):
                raw = copy.deepcopy(FIXTURE["nppes_rows"][0])
                raw.update({"Provider Organization Name (Legal Business Name)": case["name"],
                            "Healthcare Provider Taxonomy Code_1": case["taxonomy"]})
                ctx = context({"nppes_rows": [raw]}, dry_run=False)
                result = leads.execute(ctx, leads.prepare(ctx))
                proposal = (result["proposed"] + result["holds"])[0]
                self.assertEqual(proposal["classification"], case["classification"])
                self.assertTrue(proposal["classification_reason"])
                self.assertEqual({c[0] for c in ctx.calls}, {"add-loop"})
                if case["classification"] == "EXCLUDE":
                    self.assertEqual(ctx.calls[0][1]["marker"], "none")
                    self.assertEqual(proposal["route"], "excluded")
        ctx = context(FIXTURE, dry_run=False)
        result = leads.execute(ctx, leads.prepare(ctx))
        person = next(p for p in result["proposed"] if p["kind"] == "person")
        self.assertEqual(person["classification"], "WATCH")

    def test_existing_client_vendor_and_prospect_are_intel_not_new_candidates(self):
        for kind, ref, route in (("client", "C-TEST", "client-intel"), ("vendor", "V-TEST", "vendor-intel"), ("lead", "L-TEST", "prospect-intel")):
            with self.subTest(kind=kind):
                fixture = {"nppes_rows": [FIXTURE["nppes_rows"][0]], "matches": {
                    "Example Dental": {"parties": [{"ref": ref, "kind": kind, "name": "Example Dental", "city": "Pensacola", "merged": False}]}}}
                ctx = context(fixture, dry_run=False)
                result = leads.execute(ctx, leads.prepare(ctx))
                self.assertEqual(result["proposed"], [])
                proposal = result["signals"][0]
                self.assertEqual(proposal["route"], route)
                self.assertEqual(proposal["existing_candidates"][0]["ref"], ref)
                self.assertEqual(proposal["identity_status"], "PENDING confirmation")
                self.assertEqual({c[0] for c in ctx.calls}, {"add-loop"})

    def test_ambiguous_and_retired_identity_matches_are_never_chosen(self):
        fixture = {"nppes_rows": [FIXTURE["nppes_rows"][0]], "matches": {
            "Example Dental": {"parties": [
                {"ref": "C-RETIRED", "kind": "client", "merged": True},
                {"ref": "C-TEST", "kind": "client", "name": "Example Dental"},
                {"ref": "V-TEST", "kind": "vendor", "name": "Example Dental"}]}}}
        ctx = context(fixture, dry_run=False)
        proposal = leads.execute(ctx, leads.prepare(ctx))["signals"][0]
        self.assertEqual(proposal["route"], "identity-review")
        self.assertEqual({c["ref"] for c in proposal["existing_candidates"]}, {"C-TEST", "V-TEST"})

    def test_hot_output_is_ranked_only_after_research_and_handoff_is_persisted(self):
        ctx = context(FIXTURE, dry_run=False)
        result = leads.execute(ctx, leads.prepare(ctx))
        self.assertEqual(result["ranked"], [])
        self.assertEqual(result["ranking_status"], "PENDING npi-sweep-weekly research and radar rubric")
        self.assertEqual([r["score"] for r in result["proposed"]], [4, 1])
        summary = json.loads(ctx.calls[-1][1]["body"])
        self.assertEqual(summary["proposed"], result["proposed"])
        self.assertEqual(summary["status"], "prepared")

    def test_authorized_official_is_preserved_without_inventing_research(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][0])
        raw.update({"Authorized Official First Name": "Example", "Authorized Official Last Name": "Principal", "Authorized Official Title or Position": "Owner"})
        ctx = context({"nppes_rows": [raw]}, dry_run=False)
        proposal = leads.execute(ctx, leads.prepare(ctx))["proposed"][0]
        self.assertEqual(proposal["authorized_official"], {"name": "Example Principal", "title": "Owner"})
        self.assertEqual(proposal["research_status"], "PENDING")
        self.assertEqual(proposal["space_need"], "VERIFY; unresearched, no probability or lease horizon assigned")

    def test_proposal_preserves_the_research_handoff(self):
        ctx = context(FIXTURE, dry_run=False)
        result = leads.execute(ctx, leads.prepare(ctx))
        self.assertEqual(result["ranked"], [])
        self.assertTrue(all(p["research_status"] == "PENDING" for p in result["proposed"]))
        self.assertTrue(all(p["research_owner"] == "npi-sweep-weekly" for p in result["proposed"]))
        self.assertIn("Winning it", result["proposed"][0]["next_action"])



if __name__ == "__main__":
    unittest.main()
