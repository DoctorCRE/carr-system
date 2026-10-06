"""Offline suite for ops/jev_precheck.py. No credential, no network, no spend.

This module calls no model, so the whole suite is deterministic. The cases that
earn their place are the four real failures from 2026-09-18 whose deciding fact
this module has to produce, plus the controls that stop it producing a fact
about everything.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_precheck.py"
SPEC = importlib.util.spec_from_file_location("jev_precheck", MODULE_PATH)
assert SPEC and SPEC.loader
pre = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pre)

COMMAND_SPEC = importlib.util.spec_from_file_location(
    "command_precheck", OPS / "command_precheck.py")
assert COMMAND_SPEC and COMMAND_SPEC.loader
command_precheck = importlib.util.module_from_spec(COMMAND_SPEC)
COMMAND_SPEC.loader.exec_module(command_precheck)


class CodexCommandReachabilityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, CARR_JEV_WORKER=""))

    def test_codex_wrapper_sends_the_literal_command_to_the_judgment(self):
        payload = {"tool_name": "functions.exec", "cwd": str(OPS.parent),
                   "tool_input": 'const r=await tools.exec_command({cmd:"python3 ops/missing.py"}); text(r);'}
        with mock.patch.object(command_precheck, "check", return_value=(0.93, {}, {})) as judge, \
             mock.patch.object(command_precheck, "_log"):
            note = command_precheck.advisory(payload)
        judge.assert_called_once_with("python3 ops/missing.py", str(OPS.parent))
        self.assertIn("PRE-CHECK", note)

    def test_dynamic_js_command_is_not_guessed(self):
        payload = {"tool_name": "functions.exec", "cwd": str(OPS.parent),
                   "tool_input": 'await tools.exec_command({cmd: generatedCommand});'}
        with mock.patch.object(command_precheck, "check") as judge:
            self.assertIsNone(command_precheck.advisory(payload))
        judge.assert_not_called()

    def test_nested_exec_command_shape_is_supported(self):
        self.assertEqual(command_precheck._commands(
            {"tool_name": "exec_command", "tool_input": {"cmd": "python3 ops/missing.py"}}),
            ["python3 ops/missing.py"])

    def test_credential_command_never_reaches_jev_or_the_log(self):
        raw = 'curl -H "Authorization: Bearer private-token" https://example.test'
        payload = {"tool_name": "functions.exec", "cwd": str(OPS.parent),
                   "tool_input": f'await tools.exec_command({{cmd:{json.dumps(raw)}}});'}
        with mock.patch.object(command_precheck, "check") as judge, \
             mock.patch.object(command_precheck, "_log") as log:
            self.assertIsNone(command_precheck.advisory(payload))
        judge.assert_not_called()
        log.assert_not_called()

    def test_codex_nested_workdir_controls_path_facts(self):
        payload = {"tool_name": "functions.exec", "cwd": "/wrong/tree", "tool_input":
                   'await tools.exec_command({cmd:"python3 ops/missing.py",workdir:"/right/tree"});'}
        self.assertEqual(command_precheck._command_cwd(payload), "/right/tree")
        payload["tool_input"] += ' await tools.exec_command({cmd:"other",workdir:"/another/tree"});'
        self.assertIsNone(command_precheck._command_cwd(payload))


class RealFailureTests(unittest.TestCase):
    """The four commands that actually failed, and the fact that decided each.

    Each of these cost a round trip on 2026-09-18. If this module stops
    producing the deciding fact, the judgment downstream goes back to scoring
    0.44 on everything, which is the measured value of a starved state.
    """

    def test_ops_is_not_a_package(self):
        facts = pre.environment_facts("from . import jev_judge  # in ops/jev_rule_select.py")
        notes = " ".join(facts.get("import_notes", []))
        self.assertIn("__init__.py", notes)
        self.assertIn("ops", notes)

    def test_a_script_that_does_not_declare_the_flag(self):
        facts = pre.environment_facts("./ops/ci.sh --class types")
        self.assertIn("undeclared_options", facts,
                      "ci.sh has no --class option and exits on an unknown argument")
        self.assertIn("--class", " ".join(facts["undeclared_options"]))

    def test_a_forced_delete_is_refused_before_it_runs(self):
        facts = pre.environment_facts(
            "find . -name '__pycache__' -prune -exec rm -rf {} +")
        self.assertIn("guard_refusals", facts)
        self.assertIn("recursive", " ".join(facts["guard_refusals"]))

    def test_the_interface_of_a_module_the_command_touches(self):
        """The nested-answer defect: reading judge()'s result one level too
        shallow. The fix is knowing what that module actually exposes."""
        facts = pre.environment_facts("./.venv/bin/python -c 'import ops/jev_judge.py'")
        self.assertIn("module_interfaces", facts)
        signatures = " ".join(facts["module_interfaces"]["ops/jev_judge.py"])
        self.assertIn("judge(", signatures)


class ControlTests(unittest.TestCase):
    """A fact-gatherer that produces a fact about everything is worthless.

    These are the negative controls. Without them the module could return its
    whole vocabulary on every command and every test above would still pass.
    """

    def test_an_ordinary_read_produces_no_alarm(self):
        facts = pre.environment_facts("grep -n 'def project_scorecard' ops/ai_eval.py")
        for alarming in ("guard_refusals", "undeclared_options",
                         "paths_that_do_not_exist", "import_notes"):
            self.assertNotIn(alarming, facts, f"a plain grep must not raise {alarming}")

    def test_a_plain_git_command_produces_no_alarm(self):
        facts = pre.environment_facts("git fetch -q origin && git merge --no-edit origin/main")
        self.assertNotIn("guard_refusals", facts)
        self.assertNotIn("undeclared_options", facts)

    def test_a_non_forced_delete_is_not_reported_as_refused(self):
        self.assertEqual(pre.guard_refusals("rm /tmp/scratch.txt"), [])
        self.assertNotEqual(pre.guard_refusals("rm -rf /tmp/scratch"), [])

    def test_paths_outside_the_repository_are_not_claimed_about(self):
        """Claiming a fact about a machine this module cannot see invites a
        confident wrong answer.

        The escape case matters more than the obvious one and is why this test
        names it: a path that walks UP out of the repository still starts with a
        repository-shaped first segment, so only the guard stops it. Mutation
        testing caught that the earlier version of this test passed with that
        guard removed, because /etc/hosts was already excluded for a different
        reason and nothing exercised the boundary."""
        facts = pre.environment_facts("cat /etc/hosts ~/.config/carr/typesafe.env")
        self.assertEqual(facts.get("paths_that_do_not_exist", []), [])
        escaping = pre.referenced_paths("cat ops/../../../etc/passwd")
        self.assertEqual([f["path"] for f in escaping], [],
                         "a path walking out of the repository must be dropped")


class HonestUnknownTests(unittest.TestCase):
    """"Could not tell" and "nothing there" must never look the same."""

    def test_a_script_that_is_not_there_reports_none_not_empty(self):
        """The file that cannot be opened at all, which is a different path
        through the function from the one whose parser cannot be followed.
        Mutation testing caught that nothing covered it: turning that branch
        into an empty list left the whole suite green, and an empty list there
        would mean a missing script is reported as accepting no options."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pre.declared_flags("ops/absent.sh", repo=tmp))
            self.assertEqual(pre.unknown_flags("./ops/absent.sh --anything", repo=tmp), [],
                             "a script that is not there yields no option finding")

    def test_a_non_shell_file_reports_none_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(os.path.join(tmp, "thing.py")).write_text("x = 1\n")
            self.assertIsNone(pre.declared_flags("thing.py", repo=tmp),
                              "options are not read from a Python file at all")

    def test_unreadable_options_report_none_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "weird.sh")
            Path(script).write_text("#!/bin/sh\nexec python3 -m thing \"$@\"\n")
            self.assertIsNone(pre.declared_flags("weird.sh", repo=tmp),
                              "a script whose options cannot be read must report None")

    def test_an_unreadable_flag_set_raises_no_undeclared_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(os.path.join(tmp, "ops")).mkdir()
            Path(os.path.join(tmp, "ops", "weird.sh")).write_text(
                "#!/bin/sh\nexec python3 -m thing \"$@\"\n")
            self.assertEqual(pre.unknown_flags("./ops/weird.sh --anything", repo=tmp), [],
                             "unknown must not become a confident refusal")

    def test_a_script_that_truly_takes_no_options_reports_an_empty_list(self):
        """The regression Jev caught in this module's own first version. It
        returned `sorted(flags) or None`, collapsing a readable script that
        declares no options into the could-not-read case, so a bogus flag
        passed to such a script was missed entirely."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(os.path.join(tmp, "ops")).mkdir()
            Path(os.path.join(tmp, "ops", "plain.sh")).write_text(
                "#!/bin/bash\nset -e\necho hello\n")
            self.assertEqual(pre.declared_flags("ops/plain.sh", repo=tmp), [],
                             "a script that never reads an argument accepts none")
            self.assertNotEqual(
                pre.unknown_flags("./ops/plain.sh --bogus", repo=tmp), [],
                "a bogus flag on a no-option script must be reported")

    def test_a_script_whose_parser_cannot_be_followed_still_reports_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(os.path.join(tmp, "ops")).mkdir()
            Path(os.path.join(tmp, "ops", "weird.sh")).write_text(
                "#!/bin/sh\nexec python3 -m thing \"$@\"\n")
            self.assertIsNone(pre.declared_flags("ops/weird.sh", repo=tmp),
                              "it reads $@, so its options are unknown not absent")

    def test_an_unparseable_module_reports_none_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(os.path.join(tmp, "broken.py")).write_text("def (:\n")
            self.assertIsNone(pre.module_interface("broken.py", repo=tmp))

    def test_a_module_with_no_public_names_reports_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(os.path.join(tmp, "quiet.py")).write_text("def _hidden():\n    pass\n")
            self.assertEqual(pre.module_interface("quiet.py", repo=tmp), [])


class NoSideEffectTests(unittest.TestCase):
    def test_interfaces_are_read_without_importing(self):
        """Importing a module to learn its shape runs everything at its top
        level, which is the exact side effect a pre-check exists to avoid."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("exec_module", "__import__", "importlib", "eval(", "exec("):
            self.assertNotIn(forbidden, source,
                             f"{forbidden} would run the code being inspected")

    def test_the_module_never_runs_a_command(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "os.popen", "pty."):
            self.assertNotIn(forbidden, source)

    def test_the_module_calls_no_model(self):
        """Gathering facts and judging them are separate jobs. Keeping them
        apart is what lets this whole suite run offline and deterministic.

        Checks the CODE, not the prose. The first version of this test read the
        whole file and failed on the docstring naming a sibling module, which
        is a citation rather than a call — a test that cannot tell those apart
        will be silenced by weakening it, and then it guards nothing."""
        import ast as _ast
        tree = _ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, _ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("typesafe_client", "jev_judge", "urllib", "requests",
                          "socket", "http", "subprocess"):
            self.assertNotIn(forbidden, imported,
                             f"{forbidden} would make this module more than a reader")
        self.assertEqual(imported, {"ast", "os", "re"},
                         "this module reads text on disk and nothing else")

    def test_the_module_is_not_a_script_entrypoint(self):
        """Uses the sealed inventory's own detector, not a substring search."""
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        self.assertIsNone(
            _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:").search(source),
            "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
