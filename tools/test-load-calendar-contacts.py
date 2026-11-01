#!/usr/bin/env python3
"""Synthetic coverage of the one-time calendar contact loader (WR-000637 C2/C3)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location("load_calendar_contacts", REPO / "tools/load-calendar-contacts.py")
assert spec is not None and spec.loader is not None
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
FREE = sorted(loader.FREE_MAIL)[0]

CONTACTS = [
    {"address": "known@firm-a.test", "ref": "V-ARC-001", "name": "Known Person", "exact": True},
    {"address": "colleague@firm-b.test", "ref": "V-LEN-002", "name": "Colleague One", "exact": True},
    {"address": "other@firm-c.test", "ref": "L-003", "name": "Pat Example", "exact": True},
]
VENDORS = [
    {"ID": "V-LEN-002", "Name": "Colleague One", "Company": "Firm B", "Email": "colleague@firm-b.test"},
    {"ID": "V-LEN-010", "Name": "Avery Stone", "Company": "firm b ", "Email": None},
    {"ID": "V-CPA-011", "Name": "Blake Rivers", "Company": "Solo", "Email": ""},
    {"ID": "V-CPA-012", "Name": "Solo Partner", "Company": "Solo", "Email": f"partner@{FREE}"},
    {"ID": "V-LEN-013", "Name": "Casey", "Company": "Firm B", "Email": None},
    {"ID": "V-GC-014", "Name": "Dana Field", "Company": "Firm D", "Email": "dana@firm-d.test (note) *(added in an export)*"},
]
GAPS = loader.vendor_gaps(VENDORS)
AVERY_CONTEXT = {"ref": "V-LEN-010", "base_version": 2, "party_version": 4, "party_name": "Avery Stone",
                 "party_email": None, "company": {"id": "org-b", "name": "Firm B"},
                 "company_domains": ["firm-b.test"]}


def plan(attendees, gaps=GAPS, contacts=CONTACTS):
    return loader.plan(attendees, contacts, gaps, internal_domain="carr.test")


class VendorGaps(unittest.TestCase):
    def test_gaps_are_emailless_vendors_with_their_firms_carried_domains(self):
        self.assertEqual(GAPS, [
            {"ref": "V-LEN-010", "name": "Avery Stone", "company": "firm b", "domains": ["firm-b.test"],
             "colleagues": ["Casey", "Colleague One"]},
            {"ref": "V-CPA-011", "name": "Blake Rivers", "company": "solo", "domains": [FREE],
             "colleagues": ["Solo Partner"]},
            {"ref": "V-LEN-013", "name": "Casey", "company": "firm b", "domains": ["firm-b.test"],
             "colleagues": ["Avery Stone", "Colleague One"]}])

    def test_null_and_blank_companies_never_pool_domains(self):
        vendors = [{"ID": "V-X-1", "Name": "Alex Stone", "Company": None, "Email": None},
                   {"ID": "V-X-2", "Name": "Other Vendor", "Company": None, "Email": "other@firm-q.test"},
                   {"ID": "V-X-3", "Name": "Ari Stone", "Company": "  ", "Email": None},
                   {"ID": "V-X-4", "Name": "Third Vendor", "Company": "", "Email": "third@firm-r.test"}]
        gaps = loader.vendor_gaps(vendors)
        self.assertEqual(gaps, [])
        self.assertEqual(plan({"A|2026-01-01": ["astone@firm-q.test", "astone@firm-r.test"]}, gaps)["fill"], [])


class Plan(unittest.TestCase):
    def test_internal_addresses_are_not_counted(self):
        p = plan({"Event|2026-01-01": ["me@carr.test", "known@firm-a.test"]})
        self.assertEqual(p["external"], 1)
        self.assertEqual(p["matched_existing"], 1)

    def test_bare_addresses_without_a_name_are_skipped_not_minted(self):
        p = plan({"Event|2026-01-01": ["stranger@firm-z.test"]})
        self.assertEqual(p["add"], [])
        self.assertEqual(p["skipped"], {"no_name": 1})

    def test_named_unknown_attendee_becomes_one_new_contact(self):
        p = plan({"A|2026-01-01": [{"email": "Robin.Vale@Firm-Z.test", "name": "Robin Vale"}],
                  "B|2026-01-02": ["robin.vale@firm-z.test"]})
        self.assertEqual(p["add"], [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}])

    def test_single_token_or_address_shaped_names_are_not_identities(self):
        p = plan({"A|2026-01-01": [{"email": "r@firm-z.test", "name": "Robin"},
                                   {"email": "s@firm-z.test", "name": "s@firm-z.test"}]})
        self.assertEqual(p["add"], [])
        self.assertEqual(p["skipped"], {"no_name": 2})

    def test_name_already_carried_with_another_address_is_skipped(self):
        p = plan({"A|2026-01-01": [{"email": "pat@elsewhere.test", "name": "  pat  EXAMPLE "}]})
        self.assertEqual(p["add"], [])
        self.assertEqual(p["skipped"], {"name_matches_existing_party": 1})

    def test_automated_and_malformed_addresses_are_skipped(self):
        p = plan({"A|2026-01-01": ["no-reply@service.test", "not an address"]})
        self.assertEqual(p["skipped"], {"automated": 1, "malformed": 1})

    def test_v2_dump_shape_is_read(self):
        p = plan({"schema": "calendar-events/v2",
                  "events": [{"emails": ["known@firm-a.test", "me@carr.test"]}]})
        self.assertEqual((p["external"], p["matched_existing"]), (1, 1))

    def test_vendor_email_fills_on_first_initial_surname_at_own_firm(self):
        p = plan({"A|2026-01-01": ["astone@firm-b.test"]})
        self.assertEqual(p["fill"], [{"ref": "V-LEN-010", "email": "astone@firm-b.test",
                                      "name": "Avery Stone", "company": "firm b"}])
        self.assertEqual(p["add"], [])

    def test_vendor_fill_accepts_first_dot_last(self):
        p = plan({"A|2026-01-01": ["avery.stone@firm-b.test"]})
        self.assertEqual([f["ref"] for f in p["fill"]], ["V-LEN-010"])

    def test_free_mail_is_never_a_firm_domain(self):
        p = plan({"A|2026-01-01": [f"brivers@{FREE}"]})
        self.assertEqual(p["fill"], [])

    def test_partial_names_never_fill(self):
        p = plan({"A|2026-01-01": ["casey@firm-b.test"]})
        self.assertEqual(p["fill"], [])

    def test_two_addresses_for_one_vendor_person_fill_nothing(self):
        p = plan({"A|2026-01-01": ["astone@firm-b.test", "avery.stone@firm-b.test"]})
        self.assertEqual(p["fill"], [])
        self.assertEqual(p["skipped"].get("ambiguous_vendor_email"), 2)

    def test_one_address_matching_two_vendor_people_fills_nothing(self):
        twins = GAPS + [{"ref": "V-LEN-019", "name": "Alex Stone", "company": "firm b",
                         "domains": ["firm-b.test"], "colleagues": []}]
        p = plan({"A|2026-01-01": ["astone@firm-b.test"]}, twins)
        self.assertEqual(p["fill"], [])

    def test_conflicting_display_name_never_fills(self):
        p = plan({"A|2026-01-01": [{"email": "astone@firm-b.test", "name": "Alex Stone"}]})
        self.assertEqual(p["fill"], [])
        self.assertEqual(p["add"], [{"email": "astone@firm-b.test", "name": "Alex Stone"}])

    def test_display_name_in_either_order_still_fills(self):
        p = plan({"A|2026-01-01": [{"email": "astone@firm-b.test", "name": "Stone, Avery"}]})
        self.assertEqual([f["ref"] for f in p["fill"]], ["V-LEN-010"])

    def test_emailed_colleague_with_the_same_initial_and_surname_blocks_the_fill(self):
        vendors = VENDORS + [{"ID": "V-LEN-020", "Name": "Alex Stone", "Company": "Firm B",
                              "Email": "alex.stone@firm-b.test"}]
        p = plan({"A|2026-01-01": ["astone@firm-b.test"]}, loader.vendor_gaps(vendors))
        self.assertEqual(p["fill"], [])
        self.assertEqual(p["skipped"].get("ambiguous_vendor_email"), 1)

    def test_carried_person_at_a_shared_domain_with_a_common_name_blocks_the_fill(self):
        contacts = CONTACTS + [{"address": "a.stone@firm-b.test", "ref": "L-030", "name": "Alex Stone", "exact": True}]
        p = plan({"A|2026-01-01": ["astone@firm-b.test"]}, contacts=contacts)
        self.assertEqual(p["fill"], [])
        self.assertEqual(p["skipped"].get("ambiguous_vendor_email"), 1)

    def test_counts_carry_no_personal_data(self):
        p = plan({"A|2026-01-01": [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}, "astone@firm-b.test"]})
        text = repr(loader.counts(p))
        for secret in ("robin", "vale", "firm-z", "astone", "v-len-010"):
            self.assertNotIn(secret, text.lower())


class ResolvedNames(unittest.TestCase):
    def test_partial_names_never_plan_a_party(self):
        for name in ("Robin", "R Vale", "R. Vale", "R V", "R. V.", "Robin V.", "R.V.", "Robin-Vale", "Robin.Vale", "robin@firm.test"):
            with self.subTest(name=name):
                p = plan({"event": [{"email": "robin@firm-z.test", "name": name}]})
                self.assertEqual(p["add"], [])
                self.assertEqual(p["skipped"].get("no_name"), 1)

    def test_full_names_plan_a_party(self):
        for name in ("Robin Vale", "Robin Q. Vale", "Zélori Varneth", "Zélori-Mavren Varneth"):
            with self.subTest(name=name):
                p = plan({"event": [{"email": "robin@firm-z.test", "name": name}]})
                self.assertEqual(len(p["add"]), 1)


class Apply(unittest.TestCase):
    def test_writes_go_only_through_add_party_and_update_party_contact(self):
        calls = []

        def call(verb, payload):
            calls.append((verb, payload))
            if verb == "add-party":
                return {"ok": True, "ref": "P-0100"}
            if verb == "vendor-assignment-context":
                return {"vendor": AVERY_CONTEXT}
            return {"ok": True, "updated": ["email"]}

        p = plan({"A|2026-01-01": [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}, "astone@firm-b.test"]})
        result = loader.apply(p, call)
        self.assertEqual([v for v, _ in calls], ["add-party", "vendor-assignment-context", "update-party-contact"])
        add, fill = calls[0][1], calls[2][1]
        self.assertEqual((add["name"], add["email"], add["kind"]), ("Robin Vale", "robin.vale@firm-z.test", "person"))
        self.assertNotIn("calendar_attestation", add)
        self.assertNotIn("force_new", add)
        self.assertEqual((fill["party"], fill["base_version"], fill["fields"]),
                         ("V-LEN-010", 4, {"email": "astone@firm-b.test"}))
        self.assertTrue(fill["source"].strip())
        self.assertNotEqual(add["idempotency_key"], fill["idempotency_key"])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["filled"], 1)
        self.assertEqual((result["refused"], result["uncertain"], result["fill_skipped"]), (0, 0, {}))

    def test_candidates_and_refusals_are_counted_never_retried(self):
        calls = []

        def call(verb, payload):
            calls.append(verb)
            if verb == "add-party":
                return {"ok": True, "candidates": [{"ref": "P-0200"}]}
            if verb == "vendor-assignment-context":
                return {"vendor": AVERY_CONTEXT}
            raise loader.VerbRefused("version_conflict")

        p = plan({"A|2026-01-01": [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}, "astone@firm-b.test"]})
        result = loader.apply(p, call)
        self.assertEqual(calls, ["add-party", "vendor-assignment-context", "update-party-contact"])
        self.assertEqual((result["add_returned_candidates"], result["filled"], result["refused"]), (1, 0, 1))
        self.assertEqual(result["errors"], {"version_conflict": 1})

    def test_an_uncertain_write_is_counted_and_never_retried(self):
        calls = []

        def call(verb, payload):
            calls.append(verb)
            raise loader.VerbUncertain("timeout")

        p = plan({"A|2026-01-01": [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}]})
        result = loader.apply(p, call)
        self.assertEqual(calls, ["add-party"])
        self.assertEqual((result["added"], result["uncertain"], result["errors"]), (0, 1, {"timeout": 1}))

    def test_fill_without_a_readable_party_version_is_skipped_unwritten(self):
        calls = []

        def call(verb, payload):
            calls.append(verb)
            return {"vendor": {**AVERY_CONTEXT, "party_version": None}}

        result = loader.apply(plan({"A|2026-01-01": ["astone@firm-b.test"]}), call)
        self.assertEqual(calls, ["vendor-assignment-context"])
        self.assertEqual(result["fill_skipped"], {"unreadable": 1})

    def assert_fresh_read_skips(self, change, reason):
        calls = []

        def call(verb, payload):
            calls.append(verb)
            return {"vendor": {**AVERY_CONTEXT, **change}}

        result = loader.apply(plan({"A|2026-01-01": ["astone@firm-b.test"]}), call)
        self.assertEqual(calls, ["vendor-assignment-context"])
        self.assertEqual((result["filled"], result["fill_skipped"]), (0, {reason: 1}))

    def test_an_email_filled_after_planning_is_never_overwritten(self):
        self.assert_fresh_read_skips({"party_email": "someone@firm-b.test", "party_version": 5}, "email_already_set")

    def test_a_renamed_party_is_not_filled(self):
        self.assert_fresh_read_skips({"party_name": "Avery Stone-Hill"}, "identity_changed")

    def test_a_party_without_a_company_is_not_filled(self):
        self.assert_fresh_read_skips({"company": None}, "company_changed")

    def test_a_same_named_company_that_does_not_carry_the_domain_is_not_filled(self):
        self.assert_fresh_read_skips({"company": {"id": "org-b2", "name": "Firm B"}, "company_domains": ["other.test"]},
                                     "domain_not_at_company")


class FakeWorker(BaseHTTPRequestHandler):
    """Stands in for the deployed Worker behind the real ./run.sh call path."""
    replies: dict = {}
    received: list = []
    delays: dict = {}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        name, args = body["params"]["name"], body["params"]["arguments"]
        FakeWorker.received.append((name, args))
        time.sleep(FakeWorker.delays.get(name, 0))
        is_error, value = FakeWorker.replies[name]
        reply = {"jsonrpc": "2.0", "id": body["id"],
                 "result": {"content": [{"type": "text", "text": json.dumps(value)}], "isError": is_error}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class CliBoundary(unittest.TestCase):
    """loader.run_verb through the real ./run.sh call, local-verb.mjs and its exit codes."""

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWorker)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        FakeWorker.received, FakeWorker.delays = [], {}
        FakeWorker.replies = {"vendor-assignment-context": (False, {"vendor": AVERY_CONTEXT}),
                              "update-party-contact": (True, {"error": "version_conflict"})}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict("os.environ", {"CARR_MCP_URL": f"http://127.0.0.1:{self.server.server_port}/mcp",
                                             "CARR_MCP_LOCAL_TOKEN": "synthetic-test-bearer",
                                             "CARR_MCP_ENV": str(Path(tmp.name) / "absent.env")})
        env.start()
        self.addCleanup(env.stop)
        self.argv = []
        real_run = subprocess.run

        def spy(argv, **kwargs):
            self.argv.append(list(argv))
            return real_run(argv, **kwargs)

        patch = mock.patch.object(loader.subprocess, "run", side_effect=spy)
        patch.start()
        self.addCleanup(patch.stop)

    def fill(self):
        return loader.apply(plan({"A|2026-01-01": ["astone@firm-b.test"]}), loader.run_verb)

    def assert_no_personal_data(self, *texts):
        for text in texts:
            for secret in ("astone", "avery", "stone", "firm-b"):
                self.assertNotIn(secret, text.lower())

    def test_a_tool_error_is_a_counted_refusal_and_the_payload_stays_off_argv(self):
        result = self.fill()
        self.assertEqual((result["filled"], result["refused"], result["errors"]), (0, 1, {"version_conflict": 1}))
        self.assertEqual(FakeWorker.received[-1][0], "update-party-contact")
        self.assertEqual(FakeWorker.received[-1][1]["fields"], {"email": "astone@firm-b.test"})
        self.assert_no_personal_data(json.dumps(self.argv), json.dumps(result))

    def test_an_unreachable_worker_is_uncertain_and_its_error_names_no_one(self):
        with mock.patch.dict("os.environ", {"CARR_MCP_URL": "http://127.0.0.1:9/mcp"}):
            with self.assertRaises(loader.VerbUncertain) as caught:
                loader.run_verb("update-party-contact", {"party": "V-LEN-010", "fields": {"email": "astone@firm-b.test"}})
        self.assertEqual(str(caught.exception), "exit_1")
        self.assert_no_personal_data(json.dumps(self.argv))

    def test_a_timeout_is_uncertain_and_never_retried(self):
        FakeWorker.replies["update-party-contact"] = (False, {"ok": True})
        FakeWorker.delays = {"update-party-contact": 6}
        with mock.patch.object(loader, "CALL_TIMEOUT", 3):
            result = self.fill()
        self.assertEqual((result["filled"], result["uncertain"], result["errors"]), (0, 1, {"timeout": 1}))
        time.sleep(4)
        self.assertEqual([n for n, _ in FakeWorker.received].count("update-party-contact"), 1)
        self.assert_no_personal_data(json.dumps(self.argv), json.dumps(result))


if __name__ == "__main__":
    unittest.main()
