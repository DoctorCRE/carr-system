"""Call capture through the engine's doors, against a fake engine, with made-up records."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import engine_calls  # noqa: E402
import post_call  # noqa: E402
from lib.engine_mail import Reply  # noqa: E402

spec = importlib.util.spec_from_file_location("capture_bridge_engine_test", BIN / "capture-bridge.py")
assert spec and spec.loader
capture_bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture_bridge)

DEAL_A = "00000000-0000-4000-8000-00000000000a"
DEAL_B = "00000000-0000-4000-8000-00000000000b"
ACTIVITY = "00000000-0000-4000-8000-0000000000c1"


def report():
    return {
        "generated_at": "2026-10-26T14:00:00Z", "review_questions": [],
        "report": {"summary": "Walked the pipeline.", "decisions": ["Send the lease draft"], "open_questions": ["Parking?"]},
        "joe_tasks": [{"deal_id": DEAL_A, "title": "Call the landlord", "due_on": "2026-10-30",
                       "evidence": "Joe will call the landlord on Friday"}],
        "dell_tasks": [],
        "deal_updates": [
            {"deal_id": DEAL_A, "summary": "Lease draft is next.", "evidence": "we will send the lease draft Monday",
             "next_step": {"text": "Send the lease draft", "next_date": "2026-11-02", "apply": "automatic"},
             "phase_move": {"value": "negotiation", "apply": "review", "review_reason": "Unclear."}},
            {"deal_id": DEAL_B, "summary": "Tour soon.", "evidence": "we should tour the suite soon",
             "next_step": {"text": "Tour the suite", "next_date": None, "apply": "review"}, "phase_move": None},
        ],
        "draft_proposals": [{"deal_id": DEAL_B, "subject": "Tour", "evidence": "send them a note",
                             "content_hash": "0" * 64, "recipient_party_id": DEAL_B, "recipient_ref": "ref-b"}],
    }


class FakeEngine:
    """Answers each door like the spec; `refuse` maps a path to replies served first."""

    def __init__(self, refuse=None):
        self.calls, self.refuse, self.stored = [], {path: list(r) for path, r in (refuse or {}).items()}, {}

    def __call__(self, method, path, body):
        request = json.loads(body)
        self.calls.append((method, path, request))
        if self.refuse.get(path):
            return self.refuse[path].pop(0)
        first = request["idempotency_key"] not in self.stored
        answer = self.stored.setdefault(request["idempotency_key"], {
            "/jobs/activities": {"activity_id": ACTIVITY, "existing": False},
            "/jobs/next-steps": {"next_step_id": f"step-{len(self.stored)}", "change_id": 1},
            "/jobs/proposals": {"proposal_id": f"proposal-{len(self.stored)}", "status": "pending"},
        }[path])
        return Reply(201, answer if first else {**answer, "replayed": True})


class EngineCalls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "2026-10-26-call"
        self.session.mkdir()
        post_call.write_private(self.session / post_call.REPORT_FILE, report())
        post_call.write_private(self.session / "meta.json", {"started": "2026-10-26T13:30:00Z"})
        post_call.write_private(self.session / post_call.CONTEXT_FILE, {"recorder": "Joe"})
        post_call.write_private(self.session / "transcript.json", {"segments": [{"text": "Good morning."}]})

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, engine):
        return engine_calls.Run(engine, self.session, lambda _line: None).publish()

    def saved(self):
        return post_call.read_json(self.session / post_call.REPORT_FILE)

    def test_switch_is_off_unless_set_and_refuses_a_bad_setting(self):
        self.assertIsNone(engine_calls.connector({}))
        self.assertIsNone(engine_calls.connector({"engine_calls": False}))
        for bad in ({"engine_calls": "yes"}, {"engine_calls": True},
                    {"engine_calls": True, "engine_credentials": "relative/engine.env"}):
            with self.subTest(config=bad), self.assertRaises(ValueError):
                engine_calls.connector(bad)
        with patch.object(engine_calls.engine_mail, "connect") as connect:
            engine_calls.connector({"engine_calls": True, "engine_credentials": "/private/engine.env"})("joe")
        connect.assert_called_once_with(Path("/private/engine.env"), "joe", job="call_capture")

    def test_call_and_suggestions_reach_the_engine_and_leave_the_worker_only_drafts(self):
        engine = FakeEngine()
        self.assertTrue(self.publish(engine))
        paths = [path for _, path, _ in engine.calls]
        self.assertEqual(paths, ["/jobs/activities", "/jobs/proposals", "/jobs/next-steps",
                                 "/jobs/proposals", "/jobs/proposals"])
        call = engine.calls[0][2]
        self.assertEqual((call["kind"], call["happened_at"]), ("call", "2026-10-26T13:30:00+00:00"))
        self.assertEqual(call["about"], [{"deal_id": DEAL_A}, {"deal_id": DEAL_B}])
        self.assertEqual(call["summary"], "Walked the pipeline.\n\n- Send the lease draft\n\n- Parking?")
        task, step, phase, held = (request for _, _, request in engine.calls[1:])
        self.assertEqual({k: task[k] for k in ("kind", "deal_id", "what", "due_on", "activity_id")},
                         {"kind": "next_step", "deal_id": DEAL_A, "what": "Call the landlord",
                          "due_on": "2026-10-30", "activity_id": ACTIVITY})
        self.assertEqual(step, {"idempotency_key": step["idempotency_key"], "deal_id": DEAL_A,
                                "what": "Send the lease draft", "due_on": "2026-11-02"})
        self.assertEqual({k: phase[k] for k in ("kind", "deal_id", "evidence")},
                         {"kind": "phase_move", "deal_id": DEAL_A, "evidence": "we will send the lease draft Monday"})
        self.assertNotIn("due_on", held)
        saved = self.saved()
        self.assertTrue(saved["deal_updates"][0]["next_step"]["applied_automatically"])
        self.assertEqual(saved["automatic"]["applied"], 1)
        self.assertEqual(post_call._report_candidates(saved), saved["draft_proposals"])
        drafts, bindings = engine_calls.worker_drafts({
            "post_call_items": [{"kind": "assigned_action"}, {"kind": "email_draft"}],
            "bindings": [{"remote": "post_call", "index": 0}, {"remote": "legacy"}, {"remote": "post_call", "index": 1}]})
        self.assertEqual((drafts, bindings), ([{"kind": "email_draft"}], [{"remote": "post_call", "index": 1}]))

    def test_a_crashed_run_retries_with_the_same_keys_and_converges(self):
        engine = FakeEngine({"/jobs/proposals": [Reply(503, {"error": "database_unavailable"})]})
        self.assertFalse(self.publish(engine))
        first = [request["idempotency_key"] for _, _, request in engine.calls]
        self.assertTrue(self.publish(engine))
        self.assertTrue(self.publish(engine))
        retried = [request["idempotency_key"] for _, _, request in engine.calls[len(first):]]
        self.assertEqual(retried[0], first[1])
        self.assertLessEqual(set(retried), set(engine.stored))
        self.assertEqual(len(engine.stored), 5)
        self.assertEqual(post_call._report_candidates(self.saved()), self.saved()["draft_proposals"])

    def test_a_refused_caller_holds_the_session_and_records_nothing(self):
        engine = FakeEngine({"/jobs/activities": [Reply(403, {"error": "unknown_job"})]})
        self.assertFalse(self.publish(engine))
        self.assertEqual(len(engine.calls), 1)
        self.assertNotIn("engine_call", self.saved())
        self.assertEqual(len(post_call._report_candidates(self.saved())), 7)

    def test_a_refused_automatic_step_becomes_a_suggestion_with_the_reason(self):
        engine = FakeEngine({"/jobs/next-steps": [Reply(404, {"error": "not_found"})]})
        self.assertTrue(self.publish(engine))
        step = self.saved()["deal_updates"][0]["next_step"]
        self.assertEqual((step["apply"], step["engine"]["outcome"], step["engine"]["refused"]),
                         ("review", "stored", "not_found"))
        self.assertIn("not_found", step["review_reason"])
        fallback = engine.calls[3][2]
        self.assertEqual((engine.calls[3][1], fallback["kind"], fallback["activity_id"]),
                         ("/jobs/proposals", "next_step", ACTIVITY))

    def test_a_one_word_automatic_step_stays_a_suggestion(self):
        short = report()
        short["deal_updates"][0]["next_step"]["text"] = "Tour"
        post_call.write_private(self.session / post_call.REPORT_FILE, short)
        engine = FakeEngine()
        self.assertTrue(self.publish(engine))
        self.assertNotIn("/jobs/next-steps", [path for _, path, _ in engine.calls])
        step = self.saved()["deal_updates"][0]["next_step"]
        self.assertEqual((step["apply"], step["engine"]["refused"]), ("review", "below_confidence_bar"))

    def test_a_final_refusal_settles_the_suggestion(self):
        engine = FakeEngine({"/jobs/proposals": [Reply(201, {"proposal_id": "p", "status": "pending"}),
                                                 Reply(422, {"error": "phase_not_eligible"})]})
        self.assertTrue(self.publish(engine))
        self.assertEqual(self.saved()["deal_updates"][0]["phase_move"]["engine"], {"outcome": "phase_not_eligible"})
        calls = len(engine.calls)
        self.assertTrue(self.publish(engine))
        self.assertEqual(len(engine.calls), calls)

    def test_filing_waits_for_the_engine_then_counts_only_worker_items(self):
        without_drafts = {**report(), "draft_proposals": []}
        post_call.write_private(self.session / post_call.REPORT_FILE, without_drafts)
        empty = {"candidate_statuses": [], "candidates": {"pending": 0}, "post_call_report": {"candidate_count": 0}}
        with self.assertRaises(post_call.ContractError):
            post_call.verify_filing_dispositions(self.saved(), empty)
        self.assertTrue(self.publish(FakeEngine()))
        post_call.verify_filing_dispositions(self.saved(), empty)

    def test_publish_with_the_switch_on_files_only_drafts_with_the_worker(self):
        config = Path(self.tmp.name) / "capture.json"
        config.write_text(json.dumps({"engine_calls": True, "engine_credentials": "/private/engine.env"}))
        shaped = {"post_call_items": [{"kind": "assigned_action"}, {"kind": "email_draft"}],
                  "legacy_items": [{"kind": "next_step"}],
                  "bindings": [{"remote": "post_call"}, {"remote": "post_call"}, {"remote": "legacy"}]}
        posted, engine = [], FakeEngine()
        with (patch.object(capture_bridge, "CONFIG_PATH", config),
              patch.object(capture_bridge, "capture_token", return_value="token"),
              patch.object(capture_bridge.post_call, "sanitized_candidates", return_value=shaped),
              patch.object(capture_bridge, "status_cmd", return_value=0),
              patch.object(capture_bridge, "post_batch", side_effect=lambda *a: posted.append(a[2:4]) or True),
              patch.object(capture_bridge, "apply_automatic") as old_apply,
              patch.object(engine_calls.engine_mail, "connect", return_value=engine) as connect):
            self.assertEqual(capture_bridge.publish_cmd({"base_url": "https://worker.invalid"}, self.session), 0)
        self.assertEqual(posted, [("/capture/post-call/candidates", [{"kind": "email_draft"}]), ("/capture/candidates", [])])
        old_apply.assert_not_called()
        connect.assert_called_once_with(Path("/private/engine.env"), "joe", job="call_capture")
        self.assertEqual(engine.calls[0][1], "/jobs/activities")


if __name__ == "__main__":
    unittest.main()
