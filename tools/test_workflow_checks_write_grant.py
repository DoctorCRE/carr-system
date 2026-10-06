"""CI guard (V5-F08 review H2): only backup-nightly.yml may write Checks.

The restore verifier (tools/restore-watermark.py) binds a nightly backup copy to
its run through the "Backup artifact" Check, whose external_id is free text. A
workflow on main allowed `checks: write` could write an identical envelope, so
that permission is confined to the one workflow that produces the Check:

  * `checks: write` anywhere but .github/workflows/backup-nightly.yml fails;
  * `write-all` anywhere fails (it includes checks: write);
  * every workflow must declare a top-level permissions block with no write
    scope, so new jobs inherit read-only permissions rather than repository
    defaults; existing jobs may explicitly declare their required write scopes.

Read as text: the venv carries no YAML parser, and the shapes checked here are
the plain block-mapping forms these workflows use (a flow mapping such as
`permissions: {checks: write}` is caught by the same pattern).

  .venv/bin/python -m unittest tools/test_workflow_checks_write_grant.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
ALLOWED = "backup-nightly.yml"
CHECKS_WRITE = re.compile(r"(?<![\w-])checks\s*:\s*['\"]?write\b")
WRITE_ALL = re.compile(r"permissions\s*:\s*['\"]?write-all\b")
SCOPE_WRITE = re.compile(r"(?<![\w-])[\w-]+\s*:\s*['\"]?write\b")


def _code(text: str) -> list[str]:
    """Lines with YAML comments dropped (a '#' preceded by whitespace or at column 0)."""
    return [re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines()]


def violations(name: str, text: str) -> list[str]:
    lines = _code(text)
    found = []
    in_defaults = False
    declared_defaults = False
    for n, line in enumerate(lines, 1):
        if CHECKS_WRITE.search(line) and name != ALLOWED:
            found.append(f"{name}:{n}: checks: write outside {ALLOWED}")
        if WRITE_ALL.search(line):
            found.append(f"{name}:{n}: permissions: write-all")
        if re.match(r"\S", line):
            in_defaults = bool(re.match(r"permissions\s*:", line))
            declared_defaults |= in_defaults
        if in_defaults and SCOPE_WRITE.search(line):
            found.append(f"{name}:{n}: top-level permissions grant write access")
    if not declared_defaults:
        found.append(f"{name}: missing read-only top-level permissions block")
    return found


class ChecksWriteGrant(unittest.TestCase):
    def test_new_workflows_cannot_inherit_write_or_repository_defaults(self):
        workflow = ("on: push\npermissions:\n  contents: read\njobs:\n  publish:\n"
                    "    permissions:\n      contents: write\n    runs-on: x\n")
        self.assertEqual(violations("new-controller.yml", workflow), [])
        for default in ("contents: write", "contents: 'write'", "packages: write"):
            with self.subTest(default=default):
                self.assertTrue(violations("new-controller.yml",
                                           workflow.replace("contents: read", default, 1)))
        self.assertTrue(violations("new-controller.yml",
                                   workflow.replace("permissions:\n  contents: read\n", "", 1)))

    def test_top_level_flow_defaults_are_read_only_even_for_backup(self):
        for name in ("new-controller.yml", ALLOWED):
            for default in ("{contents: write}", "{contents: 'write', checks: read}",
                            "{checks: write}"):
                with self.subTest(name=name, default=default):
                    self.assertTrue(violations(name, f"permissions: {default}\njobs:\n  a:\n    runs-on: x\n"))
            self.assertEqual(violations(name, "permissions: {contents: read}\njobs:\n  a:\n    runs-on: x\n"), [])

    def test_all_workflows_have_read_only_defaults_and_only_backup_jobs_may_write_checks(self):
        files = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
        self.assertTrue(files, "no workflows found")
        found = [v for f in files for v in violations(f.name, f.read_text())]
        self.assertEqual(found, [])
        self.assertTrue(CHECKS_WRITE.search((WORKFLOWS / ALLOWED).read_text()),
                        "backup-nightly.yml no longer writes its Check; the restore verifier cannot bind a run")

    def test_the_guard_catches_each_shape(self):
        top = "on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: x\n"
        self.assertEqual(violations("other.yml", top), [])
        self.assertTrue(violations("other.yml", top.replace("contents: read", "checks: write")))
        self.assertTrue(violations("other.yml", top.replace("contents: read", "checks: 'write'")))
        self.assertTrue(violations("other.yml", "on: push\npermissions: {checks: write}\njobs:\n  a:\n    x: 1\n"))
        self.assertTrue(violations("other.yml", "on: push\npermissions: write-all\njobs:\n  a:\n    x: 1\n"))
        self.assertEqual(violations("other.yml", top + "# checks: write (a comment)\n"), [])
        backup_job = top + "    permissions:\n      checks: write\n"
        self.assertEqual(violations(ALLOWED, backup_job), [])
        self.assertTrue(violations(ALLOWED, top.replace("contents: read", "checks: write")))
        self.assertTrue(violations(ALLOWED, "on: push\npermissions: write-all\njobs:\n  a:\n    x: 1\n"))
        per_job = ("on: push\njobs:\n  a:\n    permissions:\n      contents: read\n    runs-on: x\n"
                   "  b:\n    runs-on: x\n")
        missing_default = ["other.yml: missing read-only top-level permissions block"]
        self.assertEqual(violations("other.yml", per_job), missing_default)
        self.assertEqual(violations("other.yml", per_job.replace("  b:\n    runs-on: x\n",
                                                                 "  b:\n    permissions:\n      contents: read\n")), missing_default)
        job_level = ("on: push\njobs:\n  a:\n    permissions:\n      checks: write\n")
        self.assertTrue(violations("other.yml", job_level))


if __name__ == "__main__":
    unittest.main()
