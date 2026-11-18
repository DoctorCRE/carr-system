# Test lead stage job execution and scheduling.
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch
import subprocess
import traceback

spec = importlib.util.spec_from_file_location("lead_job", Path(__file__).resolve().parents[1] / "bin/lead-stage-job.py")
assert spec is not None and spec.loader is not None
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class LeadStageJobTests(unittest.TestCase):
    def test_subprocess_failures_never_disclose_contact_inputs(self):
        args = {"counterparty_address": "private-sentinel@example.test", "native_ref": "local-mail:private-sentinel"}
        for failure in ("timeout", "launch"):
            def run(argv, **kwargs):
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="private-sentinel")
                raise OSError("private-sentinel launch failure")
            with self.subTest(failure=failure), patch.object(job.record_call, "_run_process", run):
                try:
                    job.require_record_reply("record-lead-contact", args)
                except Exception:
                    rendered = traceback.format_exc()
                else:
                    self.fail("subprocess failure must stop the job")
                self.assertIn("RuntimeError: record-lead-contact failed", rendered)
                self.assertNotIn("private-sentinel", rendered)

    def test_dry_run_is_only_a_read(self):
        calls = []
        job.run_job(lambda v, a: calls.append((v, a)) or {"moves": []}, dry_run=True)
        self.assertEqual(calls, [("lead-stage-preview", {})])

    def test_capture_replay_keys_and_no_send(self):
        calls = []
        evidence = [{"lead": "L-EXAMPLE", "native_ref": "local-mail:synthetic-1", "counterparty_address": "example@example.test",
                     "kind": "email_in", "occurred_at": "2026-10-01T12:00:00Z", "automated": False}]
        for _ in range(2):
            job.run_job(lambda v, a: calls.append((v, a)) or {}, evidence=evidence)
        self.assertEqual(calls[0][1]["idempotency_key"], calls[2][1]["idempotency_key"])
        self.assertEqual([v for v, _ in calls], ["record-lead-contact", "advance-leads"] * 2)

    def test_rejects_raw_content_before_any_write(self):
        calls = []
        with self.assertRaises(ValueError):
            job.run_job(lambda *a: calls.append(a), evidence=[{"body": "synthetic content"}])
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            job.run_job(lambda *a: calls.append(a), evidence=[], dry_run=True)

    def test_invoice_batch_uses_capture_then_shared_job_with_stable_replay_keys(self):
        invoice = {"native_ref":"local-mail:synthetic-invoice", "from_address":"invoices@example.test",
                   "deal_name":"Synthetic Lease", "client_name":"Synthetic Practice", "occurred_at":"2026-10-03T12:00:00Z"}
        calls=[]
        for _ in range(2):
            job.run_job(lambda v,a:calls.append((v,a)), evidence=[invoice])
        self.assertEqual([v for v,a in calls], ["record-deal-invoice","advance-leads"]*2)
        self.assertEqual(calls[0][1]["idempotency_key"],calls[2][1]["idempotency_key"])
        calls=[]
        with self.assertRaises(ValueError):
            job.run_job(lambda v,a:calls.append(v),evidence=[invoice,{**invoice,"body":"Synthetic"}])
        self.assertEqual(calls,[])

    def test_capture_failure_stops_job(self):
        calls = []
        def call(verb, args):
            calls.append(verb)
            raise RuntimeError("synthetic failure")
        with self.assertRaises(RuntimeError):
            job.run_job(call, evidence=[{"lead": "L-EXAMPLE", "native_ref": "local:synthetic", "counterparty_address": "example@example.test",
                                        "kind": "meeting", "occurred_at": "2026-10-01T12:00:00Z"}])
        self.assertEqual(calls, ["record-lead-contact"])


class ScheduleTest(unittest.TestCase):
    def test_job_follows_calendar_archive(self):
        chain=(Path(__file__).resolve().parents[1]/"bin/nightly.sh").read_text()
        calendar=chain.index('step "calendar archive"')
        job=chain.index('step "lead stages"')
        self.assertGreater(job,calendar)
        self.assertIn('bin/lead-stage-job.py',chain[job:job+140])


class EngineInvoiceCloseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.credentials = self.root / "engine.env"
        self.job = job.EngineInvoiceCloses(self.credentials, self.root / "receipts")
        self.invoice = job.InvoiceClose("10000000-0000-4000-8000-000000000001",
                                        "10000000-0000-4000-8000-000000000002",
                                        "10000000-0000-4000-8000-000000000003", "fixture-partner")
        self.calls = []

    def call(self, verb, args):
        self.calls.append((verb, args))
        return {"moves": [], "invoice_closes": [], "drafts_prepared": 0}

    def test_nightly_applies_for_deal_partner_with_crash_safe_key(self):
        i = self.invoice
        requests = []
        answers = [503, 503, 503, 200]

        def door(method, path, body):
            requests.append((method, path, json.loads(body)))
            status = answers.pop(0)
            return job.engine_mail.Reply(status, {} if status == 503 else {
                "invoice_id": i.invoice_id, "deal_id": i.deal_id, "payment_id": i.payment_id,
                "status": "applied", "application_id": None, "replayed": True,
            })

        with patch.object(job, "list_engine_invoice_closes", return_value=[i]), \
                patch.object(job.engine_mail, "connect", return_value=door) as connect:
            first = job.run_job(self.call, engine_invoices=self.job)
            result = job.run_job(self.call, engine_invoices=self.job)
        self.assertEqual(first["engine_invoice_closes"], {"unavailable": 1})
        self.assertEqual(result["engine_invoice_closes"], {"applied": 1})
        self.assertEqual(len(requests), 4)
        self.assertEqual(requests, [requests[0]] * len(requests))
        method, path, body = requests[0]
        self.assertEqual((method, path), ("POST", f"/jobs/invoice-emails/{i.invoice_id}/apply"))
        self.assertEqual(body, {"deal_id": i.deal_id, "payment_id": i.payment_id,
                                "idempotency_key": body["idempotency_key"]})
        self.assertEqual(job.uuid.UUID(body["idempotency_key"]).version, 5)
        connect.assert_called_with(self.credentials, i.for_user, job="lead_stage")
        self.assertEqual([verb for verb, _ in self.calls], ["advance-leads", "advance-leads"])
        self.assertTrue(all(args["engine_invoice_closes"] is True for _, args in self.calls))
        self.assertNotEqual(self.calls[0][1]["idempotency_key"], self.calls[1][1]["idempotency_key"])

    def test_refusal_is_final_on_later_runs(self):
        reply = job.engine_mail.Reply(422, {"rule": "invoice_email_needs_user"})
        changed = self.invoice._replace(payment_id="10000000-0000-4000-8000-000000000004")
        with patch.object(job, "list_engine_invoice_closes", side_effect=[[self.invoice], [changed]]), \
                patch.object(job.engine_mail, "connect") as connect:
            connect.return_value.return_value = reply
            for _ in range(2):
                result = job.run_job(self.call, engine_invoices=self.job)
                self.assertEqual(result["engine_invoice_closes"], {"refused": 1})
        connect.return_value.assert_called_once()

    def test_lists_as_lead_stage_and_applies_only_the_engine_selected_target(self):
        i = self.invoice
        with patch.object(job.engine_mail, "connect") as connect:
            list_door, apply_door = connect.return_value, Mock()
            connect.side_effect = [list_door, apply_door]
            list_door.side_effect = [job.engine_mail.Reply(503, {}), job.engine_mail.Reply(200, {"invoices": [
                {"invoice_id": "10000000-0000-4000-8000-000000000004", "target": None},
                {"invoice_id": i.invoice_id, "target": {
                    "deal_id": i.deal_id, "payment_id": i.payment_id, "for_user": i.for_user,
                }, "evidence_ref": "local-mail:synthetic-invoice"},
            ]})]
            apply_door.return_value = job.engine_mail.Reply(200, {
                "invoice_id": i.invoice_id, "deal_id": i.deal_id, "payment_id": i.payment_id,
                "status": "attached", "application_id": "10000000-0000-4000-8000-000000000005",
            })
            result = job.run_job(self.call, engine_invoices=self.job)
        self.assertEqual(result["engine_invoice_closes"], {"attached": 1})
        self.assertEqual(connect.call_args_list, [
            call(self.credentials, job="lead_stage"),
            call(self.credentials, i.for_user, job="lead_stage"),
        ])
        self.assertEqual(list_door.call_args_list, [call("GET", "/jobs/invoice-emails", None)] * 2)
        apply_door.assert_called_once()
        self.assertEqual(apply_door.call_args.args[:2], ("POST", f"/jobs/invoice-emails/{i.invoice_id}/apply"))
        self.assertEqual([verb for verb, _ in self.calls], ["advance-leads"])
        self.assertIs(self.calls[0][1]["engine_invoice_closes"], True)

    def test_refused_list_fails_without_falling_back_to_legacy_invoices(self):
        with patch.object(job.engine_mail, "connect") as connect:
            connect.return_value.return_value = job.engine_mail.Reply(403, {})
            with self.assertRaisesRegex(RuntimeError, "invoice list"):
                job.run_job(self.call, engine_invoices=self.job)
        self.assertEqual([verb for verb, _ in self.calls], ["advance-leads"])
        self.assertIs(self.calls[0][1]["engine_invoice_closes"], True)
        connect.return_value.assert_called_once_with("GET", "/jobs/invoice-emails", None)

    def test_malformed_list_does_not_apply_any_invoice(self):
        i = self.invoice
        target = {"deal_id": i.deal_id, "payment_id": i.payment_id, "for_user": i.for_user}
        invoices = [{"invoice_id": i.invoice_id, "target": target},
                    {"invoice_id": "10000000-0000-4000-8000-000000000004",
                     "target": {**target, "payment_id": "../invalid"}}]
        with patch.object(job.engine_mail, "connect") as connect:
            connect.return_value.return_value = job.engine_mail.Reply(200, {"invoices": invoices})
            with self.assertRaisesRegex(RuntimeError, "^Invalid engine invoice list target$"):
                job.run_job(self.call, engine_invoices=self.job)
        connect.return_value.assert_called_once_with("GET", "/jobs/invoice-emails", None)

    def test_switched_dry_run_never_applies_an_invoice(self):
        with patch.object(job, "list_engine_invoice_closes", return_value=[self.invoice]), \
                patch.object(job.engine_mail, "connect") as connect:
            result = job.run_job(self.call, dry_run=True, engine_invoices=self.job)
        self.assertEqual(result["engine_invoice_closes"], {"candidates": 1})
        self.assertEqual(self.calls, [("lead-stage-preview", {"engine_invoice_closes": True})])
        connect.assert_not_called()

    def test_cli_switch_off_keeps_legacy_arguments(self):
        with patch.object(job.Path, "home", return_value=self.root), \
                patch.object(job, "require_record_reply", side_effect=self.call), \
                patch.object(job, "list_engine_invoice_closes") as list_invoices, \
                patch("builtins.print"), \
                patch.object(job.sys, "argv", ["lead-stage-job"]):
            job.main()
            settings = self.root / ".config/carr/outlook.json"
            settings.parent.mkdir(parents=True)
            settings.touch(mode=0o600)
            settings.write_text('{"engine_lead_stage": false}')
            job.main()
        list_invoices.assert_not_called()
        self.assertEqual([verb for verb, _ in self.calls], ["advance-leads", "advance-leads"])
        self.assertTrue(all(set(args) == {"idempotency_key", "time_zone"} for _, args in self.calls))


if __name__ == "__main__":
    unittest.main()
