#!/usr/bin/env python3
"""Offline checks for the controller's job request and result."""
import copy
import json
import sys
import subprocess
import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engineering_dispatch_adapter as adapter


class JobTests(unittest.TestCase):
    def setUp(self):
        self.job = {"repository": "jbookout/carr-system", "revision": "a" * 40,
            "permitted_work": {"objective": "Fix dispatch", "scope_boundary": "Dispatch only",
                "actions": ["repository:write-declared-scope"], "resource_refs": ["resource:dispatch"],
                "component_refs": [], "paths": ["tools/room-bridge/*.py"]},
            "checks": [{"check_ref": "check:dispatch"}]}
        self.result = {**copy.deepcopy(self.job),
            "checks": [{"check_ref": "check:dispatch", "state": "blocked", "evidence_refs": []}],
            "outcome": {"status": "blocked"}}

    def test_one_request_and_result(self):
        self.assertEqual(adapter.validate_job(self.job), self.job)
        self.assertEqual(adapter.validate_result(self.result, self.job), self.result)

    def test_result_cannot_widen_scope_or_change_revision(self):
        for field, value in (("revision", "b" * 40), ("repository", "elsewhere/repo")):
            result = {**self.result, field: value}
            with self.assertRaises(adapter.DispatchRefusal): adapter.validate_result(result, self.job)
        self.result["permitted_work"]["paths"].append("secrets/*")
        with self.assertRaises(adapter.DispatchRefusal): adapter.validate_result(self.result, self.job)

    def test_completion_requires_checks_and_output(self):
        self.result["outcome"]["status"] = "claimed_complete"
        with self.assertRaises(adapter.DispatchRefusal): adapter.validate_result(self.result, self.job)
        self.result["checks"][0].update(state="passed", evidence_refs=[{"ref": "test-output"}])
        self.result["outcome"].update(artifact_refs=["branch:fix"], evidence_refs=[{"ref": "test-output"}])
        adapter.validate_result(self.result, self.job)

    def test_scope_and_checks_are_required(self):
        for paths in (["../outside"], ["/outside"]):
            self.job["permitted_work"]["paths"] = paths
            with self.assertRaises(adapter.DispatchRefusal): adapter.validate_job(self.job)

    def test_execution_checks_actual_changed_paths(self):
        for path, refused in (("tools/room-bridge/fix.py", False), ("outside.txt", True)):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                tree = Path(directory)
                from ops.git_env import fixture_env
                env = fixture_env()
                subprocess.run(['git', 'init', '-q', '-b', 'main', directory], env=env, check=True)
                subprocess.run(['git', '-C', directory, '-c', 'user.name=Test',
                    '-c', 'user.email=test@users.noreply.github.com', 'commit', '--allow-empty', '-qm', 'Base'],
                    env=env, check=True)
                revision = subprocess.check_output(['git', '-C', directory, 'rev-parse', 'HEAD'], env=env, text=True).strip()
                job = {**self.job, "revision": revision}
                result = {**copy.deepcopy(self.result), "revision": revision}
                result["outcome"]["source_evidence"] = {"worktree_ref": directory,
                    "branch_ref": "main", "source_sha": revision}
                prepared = {"checkout_path": directory, "checkout_branch": "main", "checkout_revision": revision}
                def execute(*args, **kwargs):
                    target = tree / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("change")
                    return {"status": "completed", "result": json.dumps(result)}
                with patch.object(adapter, '_dedicated_codex_desk'), \
                     patch.object(adapter.codex_checkout, 'prepare', return_value=prepared):
                    if refused:
                        with self.assertRaisesRegex(adapter.DispatchRefusal, 'outside permitted work'):
                            adapter.run(job, dispatch_fn=execute)
                    else:
                        self.assertEqual(adapter.run(job, dispatch_fn=execute), result)


if __name__ == "__main__": unittest.main()
