# Test Salesforce deal synchronization.
import datetime as dt
import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.salesforce_deal_sync import build_plan
from lib import salesforce_deal_sync as sync


REPORT = "Opportunity ID,Opportunity Name,Account Name,Stage,Opportunity Owner,Close Date\n"
AS_OF = dt.date(2026, 10, 19)


class RecordLayer:
    def __init__(self, deals=()):
        self.deals = copy.deepcopy(list(deals))
        self.calls = []

    def __call__(self, verb, args):
        self.calls.append((verb, copy.deepcopy(args)))
        if verb == "read-deal-reconciliation":
            return {"deals": copy.deepcopy(self.deals)}
        if verb == "new-deal":
            if not {"client", "name", "deal_type", "phase"} <= args.keys():
                raise AssertionError("new-deal requires phase under the deployed contract")
            self.deals.append({"id": "new-deal", "name": args["name"], "base_version": 1,
                               "owner": None, **{k: args[k] for k in ("phase", "salesforce_id", "sf_close_date_placeholder") if k in args}})
            return {"ok": True, "deal_id": "new-deal"}
        row = next(d for d in self.deals if d["id"] == args["deal"])
        if args["base_version"] != row["base_version"]:
            raise RuntimeError("version_conflict")
        if verb == "update-deal":
            row.update(args["fields"])
        elif verb == "set-lead":
            row["owner"] = args["new_lead"]
        else:
            raise AssertionError(verb)
        row["base_version"] += 1
        return {"ok": True}


class SalesforceSyncTests(unittest.TestCase):
    def plan(self, rows, deals=(), clients=(), expected_count=None):
        return build_plan(REPORT + rows, deals=list(deals), clients=list(clients), as_of=AS_OF,
                          expected_count=expected_count if expected_count is not None else len(rows.splitlines()))

    def test_missing_opportunity_proposes_create_and_partner_owner(self):
        plan = build_plan(REPORT + "006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n",
                          deals=[], clients=[{"ref": "C-001", "name": "Example Practice"}],
                          as_of=AS_OF, expected_count=1)
        self.assertEqual(plan["counts"], {"source": 1, "create": 1, "update": 0, "unchanged": 0,
                                          "held": 0, "out_of_scope": 0})
        item = plan["items"][0]
        self.assertEqual(item["kind"], "create")
        self.assertEqual(item["fields"], {"phase": "legal", "salesforce_id": "006000000000001",
                                         "sf_close_date_placeholder": "2026-12-01"})
        self.assertEqual(item["owner"], "dell")
        self.assertEqual(item["client"], "C-001")

    def test_id_wins_over_name_and_updates_only_changed_values(self):
        plan = build_plan(REPORT + "006000000000001,Renamed opportunity,Example Practice,Negotiation,Joe Bookout,2026-11-01\n",
                          deals=[{"id": "deal-1", "name": "Old name", "salesforce_id": "006000000000001AAA",
                                  "base_version": 4, "phase": "research", "owner": "dell",
                                  "sf_close_date_placeholder": "2026-12-01"},
                                 {"id": "deal-2", "name": "Renamed opportunity", "salesforce_id": "006000000000002"}],
                          clients=[], as_of=AS_OF, expected_count=1)
        item = plan["items"][0]
        self.assertEqual(item["kind"], "update")
        self.assertEqual(item["deal"], "deal-1")
        self.assertEqual(item["owner"], "joe")
        self.assertEqual(item["fields"], {"phase": "negotiation", "sf_close_date_placeholder": "2026-11-01"})

    def test_unique_name_matching_backfills_id_with_consistent_client(self):
        p = self.plan("006000000000001,Example expansion,Example Practice,Research,Joe Bookout,2026-12-01\n",
                      deals=[{"id": "d1", "name": "Example expansion", "client_name": "Example Practice",
                              "base_version": 1, "phase": "research", "owner": "joe"}])
        self.assertEqual(p["items"][0]["deal"], "d1")
        self.assertEqual(p["items"][0]["fields"]["salesforce_id"], "006000000000001")

    def test_company_alone_cannot_claim_an_unlinked_opportunity(self):
        p = self.plan("006000000000001,Second office,Example Practice,Research,Joe,2026-12-01\n",
                      deals=[{"id": "d1", "name": "First office", "client_name": "Example Practice",
                              "base_version": 1, "phase": "research", "owner": "joe"}],
                      clients=[{"ref": "C-001", "name": "Example Practice"}])
        self.assertEqual(p['items'][0]['kind'], 'held')
        self.assertEqual(p['items'][0]['reason'], 'reviewed_alias_required')
        self.assertNotIn('deal', p['items'][0])

    def test_second_opportunity_for_same_client_creates_a_distinct_deal(self):
        p = self.plan("006000000000002,Second office,Example Practice,Legal,Dell McCraney,2026-12-01\n",
                      deals=[{"id": "d1", "name": "First office", "client_name": "Example Practice",
                              "salesforce_id": "006000000000001", "base_version": 1}],
                      clients=[{"ref": "C-001", "name": "Example Practice"}])
        self.assertEqual(p["items"][0]["kind"], "create")

    def test_same_name_under_another_client_is_held(self):
        p = self.plan("006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n",
                      deals=[{"id": "d1", "name": "Example expansion", "client_name": "Other Practice", "base_version": 1}])
        self.assertEqual(p["items"][0]["kind"], "held")
        records = RecordLayer([{"id": "d1", "name": "Example expansion", "client_name": "Other Practice", "base_version": 1}])
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(sync.apply_plan(p, records, Path(folder) / 'receipts'), {'verified': 0, 'held': 1})
        self.assertFalse(any(v != 'read-deal-reconciliation' for v, _ in records.calls))

    def test_full_id_csv_cannot_claim_a_same_name_deal_with_unresolved_client(self):
        for client_name in (None, '', '   '):
            with self.subTest(client_name=client_name):
                deal = {'id': 'd1', 'name': 'Example office', 'client_name': client_name,
                        'salesforce_id': None, 'phase': 'research', 'owner': 'dell', 'base_version': 1}
                plan = self.plan('006000000000001AAA,Example office,Example Practice,Legal,Dell,2026-12-01\n',
                                 deals=[deal])
                records = RecordLayer([deal])
                with tempfile.TemporaryDirectory() as folder:
                    result = sync.apply_plan(plan, records, Path(folder) / 'receipts')
                self.assertEqual(result, {'verified': 0, 'held': 1})
                self.assertFalse(any(v != 'read-deal-reconciliation' for v, _ in records.calls))
                self.assertIsNone(records.deals[0]['salesforce_id'])
                self.assertEqual(records.deals[0]['phase'], 'research')

    def test_ambiguous_or_conflicting_identity_is_held(self):
        row = "006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n"
        for deals in [
            [{"id": "d1", "name": "Example expansion", "salesforce_id": "006000000000002"}],
            [{"id": "d1", "name": "One", "client_name": "Example Practice"},
             {"id": "d2", "name": "Two", "client_name": "Example Practice"}],
            [{"id": "d1", "name": "Example expansion"}, {"id": "d2", "name": "Example expansion"}],
        ]:
            with self.subTest(deals=deals):
                self.assertEqual(self.plan(row, deals=deals)["counts"]["held"], 1)

    def test_duplicate_source_identity_or_target_never_produces_writes(self):
        row = "006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n"
        for rows in [row + row, row + row.replace("000000000001", "000000000002")]:
            p = self.plan(rows, deals=[{"id": "d1", "name": "Example expansion", "base_version": 1}])
            self.assertEqual(p["counts"]["held"], 2)
            self.assertEqual(p["counts"]["update"], 0)

    def test_empty_cli_input_stops_cleanly_before_any_record_read(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'empty.csv'
            report.write_text('')
            with contextlib.redirect_stderr(io.StringIO()):
                code = sync.main([str(report), '--expected-count', '0', '--out', str(Path(folder) / 'plan')],
                    call_verb=lambda verb, args: self.fail('empty input must not read records'))
            self.assertEqual(code, 1)

    def test_closed_lost_maps_to_lost_and_fully_closed_out_means_won(self):
        lost = self.plan("006000000000001,Example office,Example Practice,Closed Lost,Joe,2026-10-01\n",
                         clients=[{"ref": "C-001", "name": "Example Practice"}])['items'][0]
        self.assertEqual(lost['kind'], 'create')
        self.assertEqual(lost['fields'].get('phase'), 'pending')
        self.assertEqual(lost['fields']['outcome'], 'lost')
        won = self.plan("006000000000001,Example office,Example Practice,Fully Closed Out,Joe,2026-10-01\n",
                        clients=[{"ref": "C-001", "name": "Example Practice"}])['items'][0]
        self.assertEqual(won['fields']['phase'], 'closed')
        self.assertEqual(won['fields']['outcome'], 'won')

    def test_lost_preserves_phase_and_cannot_reclassify_a_won_deal(self):
        row = '006000000000001,Example office,Example Practice,Closed Lost,Joe,2026-10-01\n'
        deal = {'id': 'd1', 'name': 'Example office', 'salesforce_id': '006000000000001',
                'phase': 'legal', 'owner': 'joe', 'base_version': 1}
        plan = self.plan(row, deals=[deal])
        self.assertNotIn('phase', plan['items'][0]['fields'])
        records = RecordLayer([deal])
        with tempfile.TemporaryDirectory() as folder:
            sync.apply_plan(plan, records, Path(folder) / 'receipts')
        self.assertEqual(records.deals[0]['phase'], 'legal')
        self.assertEqual(records.deals[0]['outcome'], 'lost')
        self.assertEqual(self.plan(row, deals=records.deals)['counts']['unchanged'], 1)
        self.assertEqual(self.plan(row, deals=[{**deal, 'phase': 'closed', 'outcome': 'won'}])['counts']['held'], 1)

    def test_closed_deal_is_never_reopened_or_reclassified(self):
        p = self.plan("006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n",
                      deals=[{"id": "d1", "name": "Example expansion", "salesforce_id": "006000000000001",
                              "phase": "closed", "outcome": "won", "base_version": 1}])
        self.assertEqual(p["items"][0]["kind"], "held")
        self.assertEqual(p["items"][0]["reason"], "closed_state_conflict")

    def test_scope_includes_all_open_and_ninety_days_of_closed(self):
        p = self.plan("006000000000001,Old open,Example Practice,Research,Joe Bookout,2025-01-01\n"
                      "006000000000002,Recent won,Example Practice,Closed Won,Dell McCraney,2026-10-01\n"
                      "006000000000003,Old lost,Example Practice,Closed Lost,Dell McCraney,2025-01-01\n"
                      "006000000000004,Other owner,Other Practice,Research,Someone Else,2026-12-01\n",
                      clients=[{"ref": "C-001", "name": "Example Practice"}])
        self.assertEqual(p["counts"]["create"], 2)
        self.assertEqual(p["counts"]["out_of_scope"], 2)
        self.assertEqual(p["items"][1]["fields"]["outcome"], "won")
        self.assertNotIn("closed_on", p["items"][1]["fields"])

    def test_unmapped_values_or_missing_clients_are_held(self):
        for stage, owner in [("Custom stage", "Joe Bookout"), ("Legal", "Joe Bookout")]:
            p = self.plan(f"006000000000001,Example expansion,Unknown Practice,{stage},{owner},2026-12-01\n")
            self.assertEqual(p["counts"]["held"], 1)

    def test_incomplete_or_malformed_report_fails_before_planning(self):
        row = "006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n"
        for report, count in [(REPORT + row, 2), (REPORT + row.replace("2026-12-01", "2026-02-30"), 1),
                              (REPORT + row.replace("006000000000001", "not-an-id"), 1),
                              ("Name,Stage\nExample,Legal\n", 1)]:
            with self.subTest(report=report):
                with self.assertRaises(ValueError):
                    build_plan(report, deals=[], clients=[], as_of=AS_OF, expected_count=count)

    def test_apply_creates_sets_owner_verifies_and_second_plan_is_unchanged(self):
        row = "006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n"
        p = self.plan(row, clients=[{"ref": "C-001", "name": "Example Practice"}])
        records = RecordLayer()
        with tempfile.TemporaryDirectory() as tmp:
            result = sync.apply_plan(p, records, Path(tmp) / "receipts.jsonl")
        self.assertEqual(result, {"verified": 1, "held": 0})
        writes = [(v, a) for v, a in records.calls if v != "read-deal-reconciliation"]
        self.assertEqual([v for v, _ in writes], ["new-deal", "set-lead"])
        for _, args in writes:
            uuid.UUID(args["idempotency_key"])
        self.assertEqual(self.plan(row, deals=records.deals)["counts"]["unchanged"], 1)

    def test_old_company_matching_plans_require_a_new_dry_run(self):
        plan = self.plan("006000000000001,Example office,Example Practice,Legal,Dell,2026-12-01\n",
                         clients=[{"ref": "C-001", "name": "Example Practice"}])
        for version in ('salesforce-deal-sync.v1', 'salesforce-deal-sync.v2'):
            plan['schema'] = version
            plan['plan_digest'] = sync.fingerprint({k: v for k, v in plan.items() if k != 'plan_digest'})
            with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, 'plan_changed'):
                sync.apply_plan(plan, lambda verb, args: self.fail('old plan must stop before a record read'),
                                Path(folder) / 'receipts')

    def test_stale_or_edited_plan_performs_no_writes(self):
        p = self.plan("006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n",
                      clients=[{"ref": "C-001", "name": "Example Practice"}])
        for edited, records in [(False, RecordLayer([{"id": "changed", "base_version": 2}])), (True, RecordLayer())]:
            plan = copy.deepcopy(p)
            if edited:
                plan["items"][0]["owner"] = "joe"
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
                sync.apply_plan(plan, records, Path(tmp) / "receipts.jsonl")
            self.assertFalse(any(v != "read-deal-reconciliation" for v, _ in records.calls))

    def test_cli_dry_run_saves_private_plan_without_writes_then_applies(self):
        records = RecordLayer()
        def call(verb, args):
            if verb == "find":
                return {"parties": [{"kind": "client", "name": "Example Practice", "ref": "C-001", "merged": False}]}
            return records(verb, args)
        with tempfile.TemporaryDirectory() as tmp:
            report, plan = Path(tmp) / "report.csv", Path(tmp) / "plan.json"
            report.write_text(REPORT + "006000000000001,Example expansion,Example Practice,Legal,Dell McCraney,2026-12-01\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(sync.main([str(report), "--expected-count", "1", "--as-of", "2026-10-19", "--out", str(plan)], call), 0)
            self.assertEqual(json.loads(output.getvalue())["create"], 1)
            self.assertEqual(plan.stat().st_mode & 0o777, 0o600)
            self.assertFalse(any(v != "read-deal-reconciliation" for v, _ in records.calls))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main(["--apply", str(plan)], call), 0)
            self.assertEqual(records.deals[0]["owner"], "dell")

    def test_concurrent_change_to_later_deal_is_not_overwritten(self):
        deals = [{"id": f"d{i}", "name": f"Office {i}", "salesforce_id": f"00600000000000{i}",
                  "base_version": 1, "phase": "research", "owner": "dell", "sf_close_date_placeholder": "2026-12-01"}
                 for i in (1, 2)]
        p = self.plan("006000000000001,Office 1,Example Practice,Legal,Dell McCraney,2026-12-01\n"
                      "006000000000002,Office 2,Example Practice,Legal,Dell McCraney,2026-12-01\n", deals=deals)
        records = RecordLayer(deals)
        def concurrent(verb, args):
            result = records(verb, args)
            if verb == "update-deal" and args["deal"] == "d1":
                records.deals[1].update(phase="negotiation", base_version=2)
            return result
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            sync.apply_plan(p, concurrent, Path(tmp) / "receipts.jsonl")
        self.assertEqual(records.deals[1]["phase"], "negotiation")

    def test_update_guards_identity_and_closed_state_against_disjoint_rebase(self):
        deals = [{"id": "d1", "name": "Example expansion", "salesforce_id": "006000000000001",
                  "phase": "research", "outcome": None, "owner": "dell", "base_version": 1}]
        p = self.plan("006000000000001,Example expansion,Example Practice,Research,Dell McCraney,2026-12-01\n", deals=deals)
        records = RecordLayer(deals)
        with tempfile.TemporaryDirectory() as tmp:
            sync.apply_plan(p, records, Path(tmp) / "receipts.jsonl")
        fields = next(args["fields"] for verb, args in records.calls if verb == "update-deal")
        self.assertEqual(fields, {"salesforce_id": "006000000000001", "phase": "research", "outcome": None,
                                  "sf_close_date_placeholder": "2026-12-01", "closed_on": None, "invoiced_on": None})

    def test_new_closed_deal_cannot_mark_won_after_a_concurrent_identity_change(self):
        plan = self.plan("006000000000001,Example office,Example Practice,Closed Won,Dell,2026-10-01\n",
                         clients=[{"ref": "C-001", "name": "Example Practice"}])
        records = RecordLayer()
        def concurrent(verb, args):
            if verb == 'update-deal':
                row = records.deals[0]
                row['salesforce_id'] = '006000000000002'
                row['base_version'] += 1
                if 'salesforce_id' in args['fields']:
                    return {'ok': False, 'error': 'version_conflict'}
                args = {**args, 'base_version': row['base_version']}
            return records(verb, args)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            sync.apply_plan(plan, concurrent, Path(folder) / 'receipts')
        self.assertIsNone(records.deals[0].get('outcome'))
        self.assertEqual(records.deals[0]['salesforce_id'], '006000000000002')

    def test_interrupted_create_is_journaled_and_reconciled_without_duplicate(self):
        row = "006000000000001,Example expansion,Example Practice,Closed Won,Dell McCraney,2026-10-01\n"
        p = self.plan(row, clients=[{"ref": "C-001", "name": "Example Practice"}])
        records = RecordLayer()
        def interrupted(verb, args):
            result = records(verb, args)
            if verb == "new-deal":
                raise TimeoutError("response lost")
            return result
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "receipts.jsonl"
            with self.assertRaises(TimeoutError):
                sync.apply_plan(p, interrupted, journal)
            self.assertEqual(json.loads(journal.read_text().splitlines()[-1])["status"], "pending")
            writes = len(records.calls)
            with self.assertRaises(ValueError):
                sync.apply_plan(p, records, journal)
            self.assertEqual(len(records.calls), writes + 1)
            repair = self.plan(row, deals=records.deals)
            self.assertEqual(repair["counts"]["update"], 1)
            self.assertEqual(sync.apply_plan(repair, records, Path(tmp) / "repair.jsonl")["verified"], 1)
        self.assertEqual(len(records.deals), 1)
        self.assertEqual(records.deals[0]["outcome"], "won")
        self.assertEqual(records.deals[0]["owner"], "dell")


if __name__ == "__main__":
    unittest.main()
