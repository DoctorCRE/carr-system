#!/usr/bin/env python3
"""Synthetic coverage of the one-time calendar contact loader (WR-000637 C2/C3)."""
import importlib.util
from pathlib import Path
import sys
import unittest

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


def plan(attendees, gaps=GAPS):
    return loader.plan(attendees, CONTACTS, gaps, internal_domain="carr.test")


class VendorGaps(unittest.TestCase):
    def test_gaps_are_emailless_vendors_with_their_firms_carried_domains(self):
        self.assertEqual(GAPS, [{"ref": "V-LEN-010", "name": "Avery Stone", "domains": ["firm-b.test"]},
                                {"ref": "V-CPA-011", "name": "Blake Rivers", "domains": [FREE]},
                                {"ref": "V-LEN-013", "name": "Casey", "domains": ["firm-b.test"]}])


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
        self.assertEqual(p["fill"], [{"ref": "V-LEN-010", "email": "astone@firm-b.test"}])
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
        twins = GAPS + [{"ref": "V-LEN-019", "name": "Alex Stone", "domains": ["firm-b.test"]}]
        p = plan({"A|2026-01-01": ["astone@firm-b.test"]}, twins)
        self.assertEqual(p["fill"], [])

    def test_counts_carry_no_personal_data(self):
        p = plan({"A|2026-01-01": [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}, "astone@firm-b.test"]})
        text = repr(loader.counts(p))
        for secret in ("robin", "vale", "firm-z", "astone", "v-len-010"):
            self.assertNotIn(secret, text.lower())


class Apply(unittest.TestCase):
    def test_writes_go_only_through_add_party_and_update_party_contact(self):
        calls = []

        def call(verb, payload):
            calls.append((verb, payload))
            if verb == "add-party":
                return {"ok": True, "ref": "P-0100"}
            if verb == "vendor-assignment-context":
                return {"vendor": {"ref": payload["vendor"], "base_version": 2, "party_version": 4}}
            return {"ok": True, "updated": ["email"]}

        p = plan({"A|2026-01-01": [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}, "astone@firm-b.test"]})
        result = loader.apply(p, call)
        self.assertEqual([v for v, _ in calls], ["add-party", "vendor-assignment-context", "update-party-contact"])
        add, fill = calls[0][1], calls[2][1]
        self.assertEqual((add["name"], add["email"], add["kind"]), ("Robin Vale", "robin.vale@firm-z.test", "person"))
        self.assertNotIn("force_new", add)
        self.assertEqual((fill["party"], fill["base_version"], fill["fields"]),
                         ("V-LEN-010", 4, {"email": "astone@firm-b.test"}))
        self.assertTrue(fill["source"].strip())
        self.assertNotEqual(add["idempotency_key"], fill["idempotency_key"])
        self.assertEqual(result, {"added": 1, "add_returned_candidates": 0, "filled": 1, "refused": 0})

    def test_candidates_and_refusals_are_counted_never_retried(self):
        calls = []

        def call(verb, payload):
            calls.append(verb)
            if verb == "add-party":
                return {"ok": True, "candidates": [{"ref": "P-0200"}]}
            if verb == "vendor-assignment-context":
                return {"vendor": {"party_version": 4}}
            return {"ok": False, "error": "version_conflict"}

        p = plan({"A|2026-01-01": [{"email": "robin.vale@firm-z.test", "name": "Robin Vale"}, "astone@firm-b.test"]})
        result = loader.apply(p, call)
        self.assertEqual(calls, ["add-party", "vendor-assignment-context", "update-party-contact"])
        self.assertEqual(result, {"added": 0, "add_returned_candidates": 1, "filled": 0, "refused": 1})


    def test_fill_without_a_readable_party_version_is_refused_unwritten(self):
        calls = []

        def call(verb, payload):
            calls.append(verb)
            return {"vendor": {"base_version": 2}}

        result = loader.apply(plan({"A|2026-01-01": ["astone@firm-b.test"]}), call)
        self.assertEqual(calls, ["vendor-assignment-context"])
        self.assertEqual(result["refused"], 1)


if __name__ == "__main__":
    unittest.main()
