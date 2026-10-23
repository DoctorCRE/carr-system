#!/usr/bin/env python3
"""Controller request contracts and direct Codex execution without model calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engineering_dispatch_adapter as adapter


class JobTests(unittest.TestCase):
    def setUp(self):
        self.job = {"repository": "DoctorCRE/carr-system", "revision": "a" * 40,
            "permitted_work": {"objective": "Fix dispatch", "scope_boundary": "Dispatch only",
                "actions": ["repository:write-declared-scope"], "resource_refs": ["resource:dispatch"],
                "component_refs": [], "paths": ["allowed/*"]},
            "checks": [{"check_ref": "check:dispatch"}]}
        self.result = {**copy.deepcopy(self.job),
            "checks": [{"check_ref": "check:dispatch", "state": "blocked", "evidence_refs": []}],
            "outcome": {"status": "blocked"}}

    def test_native_preflight_passes_the_controller_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'codex'
            binary.write_text('#!/bin/sh\nprintf "codex fixture\\n"\n')
            binary.chmod(0o755)
            environment = {**os.environ, 'PATH': temporary + os.pathsep + os.environ['PATH']}
            process = subprocess.run([sys.executable, str(Path(adapter.__file__)), '--preflight'],
                                     env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(process.stdout),
                         {'ok': True, 'executor': 'codex exec', 'version': 'codex fixture'})
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const source = fs.readFileSync(process.argv[1], 'utf8');
const preflight = source.slice(source.indexOf('function preflightCodexExecutor()'), source.indexOf('async function main()'));
const stdout = fs.readFileSync(0, 'utf8');
const context = { PYTHON: 'python', ADAPTER: 'adapter', safeAdapterEnv: () => ({}),
    ControllerError: class extends Error {}, spawn: () => {
        const child = new EventEmitter(); child.stdout = new EventEmitter();
        process.nextTick(() => { child.stdout.emit('data', stdout); child.emit('close', 0); });
        return child;
    }};
vm.runInNewContext(preflight + 'preflightCodexExecutor()', context)
    .catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(['node', '-e', script, str(adapter.REPO / 'mcp-server/bin/run-engineering-dispatch.mjs')],
                                input=process.stdout, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_one_request_and_result(self):
        self.assertEqual(adapter.validate_job(self.job), self.job)
        self.assertEqual(adapter.validate_result(self.result, self.job), self.result)

    def test_result_cannot_widen_scope_or_change_revision(self):
        for field, value in (("revision", "b" * 40), ("repository", "elsewhere/repo")):
            with self.assertRaises(adapter.DispatchRefusal):
                adapter.validate_result({**self.result, field: value}, self.job)
        self.result["permitted_work"]["paths"].append("secrets/*")
        with self.assertRaises(adapter.DispatchRefusal):
            adapter.validate_result(self.result, self.job)

    def test_completion_requires_checks_and_output(self):
        self.result["outcome"]["status"] = "claimed_complete"
        with self.assertRaises(adapter.DispatchRefusal):
            adapter.validate_result(self.result, self.job)
        self.result["checks"][0].update(state="passed", evidence_refs=[{"ref": "test-output"}])
        self.result["outcome"].update(artifact_refs=["branch:fix"], evidence_refs=[{"ref": "test-output"}])
        adapter.validate_result(self.result, self.job)

    def test_scope_and_checks_are_required(self):
        for paths in (["../outside"], ["/outside"]):
            self.job["permitted_work"]["paths"] = paths
            with self.assertRaises(adapter.DispatchRefusal):
                adapter.validate_job(self.job)

    def execute_fixture(self, change="allowed/fix.py", result_change=""):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        source = folder / "source"
        subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c",
                        "user.email=test@users.noreply.github.com", "commit", "--allow-empty", "-qm", "Base"], check=True)
        self.job["revision"] = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        binary = folder / "codex"
        marker = folder / "checkout-path"
        binary.write_text("#!/usr/bin/env python3\n" + f'''
import json, os, pathlib, subprocess, sys
if sys.argv[1:] == ['--version']:
    print('codex fixture')
    raise SystemExit(0)
args = sys.argv[1:]
assert args[0] == 'exec' and '--dangerously-bypass-hook-trust' in args
assert 'DATABASE_URL' not in os.environ and 'CARR_TOKEN' not in os.environ
repo = pathlib.Path(args[args.index('-C') + 1])
pathlib.Path({str(marker)!r}).write_text(str(repo))
job = json.loads(args[-1].rsplit('\\n', 1)[-1])
path = repo / {change!r}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text('change')
revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
branch = subprocess.check_output(['git', '-C', str(repo), 'branch', '--show-current'], text=True).strip()
result = {{**job, 'checks': [{{'check_ref':'check:dispatch', 'state':'blocked', 'evidence_refs':[]}}],
    'outcome':{{'status':'blocked', 'source_evidence':{{'worktree_ref':str(repo), 'branch_ref':branch, 'source_sha':revision}}}}}}
{result_change}
pathlib.Path(args[args.index('-o') + 1]).write_text(json.dumps(result))
''')
        binary.chmod(0o755)
        with patch.object(adapter, "REPOSITORY_URL", str(source)), patch.dict(os.environ, {
            "PATH": str(folder) + os.pathsep + os.environ["PATH"],
            "DATABASE_URL": "synthetic", "CARR_TOKEN": "synthetic"}):
            try:
                return adapter.run(self.job)
            finally:
                self.assertFalse(Path(marker.read_text()).exists(), "clone must be deleted after success or refusal")

    def test_direct_execution_retains_checked_source_and_deletes_clone(self):
        returned = self.execute_fixture()
        source = returned["outcome"]["source_evidence"]
        self.assertRegex(source["worktree_ref"], r"^worktree:[0-9a-f]{64}$")
        self.assertRegex(source["branch_ref"], r"^branch:[0-9a-f]{64}$")
        self.assertEqual(source["source_sha"], self.job["revision"])

    def test_direct_execution_checks_actual_changed_paths(self):
        with self.assertRaisesRegex(adapter.DispatchRefusal, "outside permitted work"):
            self.execute_fixture(change="outside.txt")

    def test_direct_execution_refuses_invented_source_metadata(self):
        for statement in ("result['outcome']['source_evidence']['worktree_ref'] = '/other'",
                          "result['outcome']['source_evidence']['branch_ref'] = 'other'",
                          "result['outcome']['source_evidence']['source_sha'] = 'b' * 40"):
            with self.subTest(statement=statement), self.assertRaises(adapter.DispatchRefusal):
                self.execute_fixture(result_change=statement)


if __name__ == "__main__":
    unittest.main()
