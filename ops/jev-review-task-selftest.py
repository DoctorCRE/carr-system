"""Offline suite for the post-write task-fit shadow. No credential, no network.

ops/jev_code_review.py review_for_edit() asks two task-fit questions in the
same request as the region questions and RECORDS a would_block decision
beside the existing advisory one; it never blocks. A fake vendor client and a
temporary shadow log stand in, so this checks the contract the hook relies on:
a confident yes records would_block, an outage records an error and still
raises into the hook's existing "unavailable" path, a missing transcript asks
only the old questions, and the hook's printed receipt is unchanged apart from
at most one extra shadow finding.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ops"))
from git_env import fixture_env  # noqa:E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = load("jev_code_review_task_test", REPO / "ops/jev_code_review.py")
lint = load("lint_gate_task_test", REPO / "hooks/lint-gate.py")

TASK = "Add a retry to the invoice upload and keep the existing error log."


import os as _sem_os
import tempfile as _sem_tmp
from unittest.mock import patch as _sem_patch
class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        with _sem_tmp.TemporaryDirectory() as root, _sem_patch.dict(_sem_os.environ, CARR_JEV_SEMANTIC_CACHE=root+"/cache"):
            return super().run(result)


class FakeClient:
    def __init__(self, task_value=0.1, other_value=0.1, error=None):
        self.task_value, self.other_value, self.error = task_value, other_value, error
        self.calls = []

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}

    def ask(self, state, questions, timeout=None, api_key=None, **kwargs):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        return {"model": "jev-1.13.0", "usage": {},
                "answers": {qid: {"type": "noul",
                                  "noul": self.task_value if qid in review.TASK_QUESTIONS
                                  else self.other_value}
                            for qid in questions}}


def transcript(folder, prompt=TASK):
    path = Path(folder) / "t.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": "an older ask"}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": prompt}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "done"}]}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows) + "{broken\n")
    return str(path)


REGION = {"path": "src/a.py", "line": 0, "kind": "just written",
          "code": "try:\n    upload()\nexcept Exception:\n    pass\n"}


def rows(log):
    return [json.loads(line) for line in Path(log).read_text().splitlines()]


class ShadowTests(SemanticTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "shadow.jsonl")
        self.payload = {"transcript_path": transcript(self.tmp.name),
                        "session_id": "s", "tool_use_id": "t"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest_task_skips_tool_results_and_keeps_the_tail(self):
        self.assertEqual(review.latest_task(self.payload["transcript_path"]), TASK)
        long = transcript(self.tmp.name, "x" * 5000 + " the ask")
        got = review.latest_task(long)
        self.assertEqual(len(got), review.TASK_TAIL_CHARS)
        self.assertTrue(got.endswith("the ask"))

    def test_task_questions_ride_in_the_same_request_with_agreeing_criteria(self):
        client = FakeClient()
        review.review_for_edit(REGION, self.payload, client=client, log_path=self.log)
        self.assertEqual(len(client.calls), 1)
        state, questions = client.calls[0]
        self.assertEqual(state["task"], {"latest_human_request": TASK})
        self.assertTrue(set(review.QUESTIONS) | set(review.TASK_QUESTIONS)
                        <= set(questions))
        for qid in review.TASK_QUESTIONS:
            criteria = questions[qid]["criteria"]
            self.assertTrue(criteria["true"] and criteria["false"])

    def test_confident_yes_records_review_task_fit_and_marks_the_scores(self):
        scores = review.review_for_edit(REGION, self.payload,
                                        client=FakeClient(task_value=0.93),
                                        log_path=self.log)
        self.assertEqual(scores["_review_task_fit"], 0.93)
        self.assertFalse(set(review.TASK_QUESTIONS) & set(scores))
        [row] = rows(self.log)
        self.assertEqual(row["kind"], review.TASK_FIT_KIND)
        self.assertTrue(row["subject_ref"]["would_block"])
        self.assertEqual(row["existing_decision"],
                         {"advisory_findings": [], "effect": "advisory"})
        self.assertEqual(row["note"], "disagreed")

    def test_low_score_records_no_block(self):
        scores = review.review_for_edit(REGION, self.payload,
                                        client=FakeClient(task_value=0.84),
                                        log_path=self.log)
        self.assertNotIn("_review_task_fit", scores)
        [row] = rows(self.log)
        self.assertFalse(row["subject_ref"]["would_block"])
        self.assertEqual(row["note"], "agreed")

    def test_outage_records_an_error_and_still_raises(self):
        with self.assertRaises(TimeoutError):
            review.review_for_edit(REGION, self.payload,
                                   client=FakeClient(error=TimeoutError("slow")),
                                   log_path=self.log)
        [row] = rows(self.log)
        self.assertIn("TimeoutError", row["error"])

    def test_no_transcript_asks_only_the_old_questions_and_records_nothing(self):
        client = FakeClient(task_value=0.99)
        scores = review.review_for_edit(REGION, {"transcript_path": "/nonexistent"},
                                        client=client, log_path=self.log)
        self.assertEqual(set(client.calls[0][1]), set(review.QUESTIONS))
        self.assertNotIn("task", client.calls[0][0])
        self.assertNotIn("_review_task_fit", scores)
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(scores, review.review_one(REGION, client=FakeClient(task_value=0.99)))


FAKE_CLIENT_SOURCE = '''
import json, os
def noul(instructions, true=None, false=None):
    return {"type": "noul", "instructions": instructions}
def ask(state, questions, timeout=None, api_key=None, **kwargs):
    cfg = json.loads(os.environ["FAKE_JEV"])
    if cfg.get("error"):
        raise TimeoutError("fake outage")
    return {"model": "jev-1.13.0", "answers": {
        q: {"type": "noul", "noul": cfg["task"] if q.startswith("task_") else cfg["other"]}
        for q in questions}}
'''


class HookOutputTests(SemanticTestCase):
    """hooks/lint-gate.py code_review(): the printed receipt is unchanged.

    The hook loads the reviewer, the vendor client and jev_judge by path, so
    the fakes are swapped in at that one seam: the client becomes a scripted
    fake and jev_judge a copy whose shadow log lands in the temp directory.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = fixture_env()
        self.env["CARR_JEV_WORKER"] = ""
        self.repo = self.root / "repo"
        (self.repo / "src").mkdir(parents=True)
        self.target = self.repo / "src/a.py"
        self.target.write_text("value = 1\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, env=self.env)
        subprocess.run(["git", "add", "src/a.py"], cwd=self.repo, check=True, env=self.env)
        self.target.write_text("try:\n    upload()\nexcept Exception:\n    pass\nresult = 2\n")
        (self.root / "typesafe_client.py").write_text(FAKE_CLIENT_SOURCE)
        (self.root / "ops").mkdir()
        (self.root / "ops/jev_judge.py").write_text((REPO / "ops/jev_judge.py").read_text())
        self.log = self.root / "out/jev-judge.jsonl"
        self.transcript = transcript(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_worker_postwrite_hook_skips_jev_without_loading_reviewer(self):
        out = io.StringIO()
        with patch.dict(os.environ, CARR_JEV_WORKER="off"), \
                patch.object(lint, "_changed_code_paths") as paths, \
                contextlib.redirect_stdout(out):
            lint.code_review({"tool_name": "apply_patch", "tool_input": {"patch": "fixture"}})
        paths.assert_not_called()
        self.assertEqual(out.getvalue(), "", "no paid review or claimed Jev receipt")

    def test_worker_keeps_deterministic_writing_lint(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.target)}}
        response = subprocess.CompletedProcess([], 0, stdout="hard-ban fixture", stderr="")
        out = io.StringIO()
        with patch.dict(os.environ, CARR_JEV_WORKER="off"), \
                patch.object(lint.sys, "stdin", io.StringIO(json.dumps(payload))), \
                patch.object(lint, "_changed_code_paths") as paths, \
                patch.object(lint, "surface_for", return_value="email"), \
                patch.object(lint.os.path, "exists", return_value=True), \
                patch.object(lint.subprocess, "run", return_value=response) as runner, \
                patch.object(lint, "log"), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as exited:
                lint.main()
        self.assertEqual(exited.exception.code, 0)
        paths.assert_not_called()
        runner.assert_called_once()
        self.assertIn("WRITING-LINT", out.getvalue())

    def run_hook(self, cfg, transcript_path):
        Path(os.environ["CARR_JEV_SEMANTIC_CACHE"]).unlink(missing_ok=True)
        payload = {"tool_name": "Write", "cwd": str(self.repo),
                   "session_id": "s", "tool_use_id": "t",
                   "tool_input": {"file_path": str(self.target)}}
        if transcript_path:
            payload["transcript_path"] = transcript_path
        real_spec = importlib.util.spec_from_file_location
        root = self.root

        def redirect(name, location, *args, **kwargs):
            location = str(location)
            if location.endswith("ops/typesafe_client.py"):
                location = str(root / "typesafe_client.py")
            elif location.endswith("ops/jev_judge.py"):
                location = str(root / "ops/jev_judge.py")
            return real_spec(name, location, *args, **kwargs)

        out = io.StringIO()
        env = dict(self.env, FAKE_JEV=json.dumps(cfg))
        with patch.dict(os.environ, env, clear=True), \
                patch("importlib.util.spec_from_file_location", redirect), \
                contextlib.redirect_stdout(out):
            lint.code_review(payload)
        text = out.getvalue()
        self.assertEqual(len(text.strip().splitlines()), 1, "one JSON line, always")
        return json.loads(json.loads(text)["hookSpecificOutput"]["additionalContext"])

    def strip(self, receipt):
        return {k: v for k, v in receipt.items() if k != "receipt_id"}

    def test_output_unchanged_unless_review_task_fit_adds_one_finding(self):
        low = {"task": 0.1, "other": 0.9}
        baseline = self.run_hook(low, None)
        self.assertEqual(baseline["status"], "reviewed")
        self.assertTrue(baseline["findings"])
        self.assertTrue(all(f["effect"] == "advisory" for f in baseline["findings"]))
        self.assertEqual(self.strip(self.run_hook(low, self.transcript)),
                         self.strip(baseline))
        high = self.run_hook({"task": 0.95, "other": 0.9}, self.transcript)
        extra = [f for f in high["findings"] if f not in baseline["findings"]]
        self.assertEqual(high["findings"][:len(baseline["findings"])],
                         baseline["findings"])
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["effect"], "advisory")
        kinds = [row["kind"] for row in rows(self.log)]
        self.assertEqual(kinds, [review.TASK_FIT_KIND] * 2)

    def test_outage_is_the_same_unavailable_receipt_and_is_recorded(self):
        without = self.run_hook({"error": True}, None)
        with_task = self.run_hook({"error": True}, self.transcript)
        self.assertEqual(without["status"], "unavailable")
        self.assertEqual(self.strip(with_task), self.strip(without))
        [row] = rows(self.log)
        self.assertIn("TimeoutError", row["error"])


if __name__ == "__main__":
    unittest.main()
