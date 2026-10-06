import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
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
            with self.subTest(failure=failure), patch("subprocess.run", run):
                try:
                    job.call_verb("record-lead-contact", args)
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
    def test_job_follows_local_mail_capture(self):
        chain=(Path(__file__).resolve().parents[1]/"bin/nightly.sh").read_text()
        capture=chain.index('step "mail capture (extract + match, writes nothing)"')
        job=chain.index('step "lead stages (evidence and approval drafts)"')
        self.assertGreater(job,capture)
        self.assertIn('bin/lead-stage-job.py',chain[job:job+140])


if __name__ == "__main__":
    unittest.main()
