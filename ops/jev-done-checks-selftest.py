#!/usr/bin/env python3
"""Offline suite for ops/jev_done_checks.py. No credential, no network, no spend.

The module asks no model, so nothing stands in for a vendor. build_handoff is
checked against its {"pack", "kept", "dropped"} contract; only git is stubbed.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(OPS))
from git_env import fixture_env  # noqa: E402

SPEC = importlib.util.spec_from_file_location("jev_done_checks", OPS / "jev_done_checks.py")
assert SPEC and SPEC.loader
jdc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jdc)


# --------------------------------------------------------------- fakes

class FakeGitEnv:
    @staticmethod
    def scrubbed_env():
        return dict(fixture_env())


# --------------------------------------------------------- #13 test quality

GOOD_TEST = """
def test_withdraw_rejects_over_balance():
    account = Account(balance=10)
    with pytest.raises(InsufficientFunds):
        account.withdraw(50)
    assert account.balance == 10
"""

WEAK_TEST = """
def test_it_runs():
    result = do_the_thing()
    assert result
"""

CODE_UNDER_TEST = "def withdraw(self, amount):\n    if amount > self.balance:\n        raise InsufficientFunds()\n"




# --------------------------------------------------------- #14 done claim



# --------------------------------------------------------- #17 review triage

DIFF_TWO_FILES = """diff --git a/src/widgets.py b/src/widgets.py
index 111..222 100644
--- a/src/widgets.py
+++ b/src/widgets.py
@@ -1,3 +1,4 @@
+# a comment
 def widget():
     return 1
diff --git a/src/auth.py b/src/auth.py
index 333..444 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,3 +1,4 @@
+def check_password(pw):
+    return pw == stored_hash
"""






# --------------------------------------------------------- #24 fact check



# --------------------------------------------------------- #10 handoff pack

def make_repo():
    tmp = tempfile.mkdtemp()
    env = {**fixture_env(), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=tmp, env=env, check=True)
    path = os.path.join(tmp, "widget.py")
    with open(path, "w") as fh:
        fh.write("def widget():\n    return 1\n")
    subprocess.run(["git", "add", "widget.py"], cwd=tmp, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp, env=env, check=True)
    with open(path, "a") as fh:
        fh.write("\ndef widget2():\n    return 2\n")
    return tmp


def write_transcript(path, notes):
    with open(path, "w") as fh:
        for i, text in enumerate(notes):
            rec = {"type": "assistant", "message": {"role": "assistant",
                   "content": [{"type": "text", "text": text}]}}
            fh.write(json.dumps(rec) + "\n")


class HandoffTests(unittest.TestCase):
    def test_empty_inputs_yield_empty_pack(self):
        result = jdc.build_handoff("", None, [], None)
        self.assertEqual(result, {"pack": "", "kept": [], "dropped": []})

    def test_under_budget_keeps_everything(self):
        repo = make_repo()
        transcript = os.path.join(repo, "transcript.jsonl")
        write_transcript(transcript, ["Working on the widget change."])
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = jdc.build_handoff("Add widget2().", transcript, ["widget.py"],
                                       None)
        finally:
            os.chdir(cwd)
        self.assertIn("task", result["kept"])
        self.assertIn("diff:widget.py", result["kept"])
        self.assertEqual(result["dropped"], [])
        self.assertIn("Add widget2", result["pack"])

    def test_over_budget_drops_low_relevance_items(self):
        repo = make_repo()
        transcript = os.path.join(repo, "transcript.jsonl")
        write_transcript(transcript, ["An old, unrelated aside about lunch plans."])
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = jdc.build_handoff("Add widget2().", transcript, ["widget.py"], None,
                                       max_chars=250)
        finally:
            os.chdir(cwd)
        self.assertTrue(any(n.startswith("assistant_note_0") for n in result["dropped"]))
        self.assertIn("task", result["kept"])
        self.assertIn("diff:widget.py", result["kept"])
        self.assertLessEqual(len(result["pack"]), 250)

    def test_over_budget_keeps_task_and_failure_first(self):
        repo = make_repo()
        transcript = os.path.join(repo, "transcript.jsonl")
        write_transcript(transcript, ["note one", "note two", "note three"])
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = jdc.build_handoff("Add widget2().", transcript, ["widget.py"],
                                       "Traceback: boom", max_chars=80)
        finally:
            os.chdir(cwd)
        self.assertIn("task", result["kept"])
        self.assertTrue(len(result["pack"]) <= 80)

    def test_failure_output_is_collected(self):
        result = jdc.build_handoff("Fix the bug.", None, [], "Traceback: KeyError")
        self.assertIn("last_failure", result["kept"])
        self.assertIn("KeyError", result["pack"])




if __name__ == "__main__":
    unittest.main()
