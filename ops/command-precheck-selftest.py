"""Offline suite for ops/command_precheck.py and its dispatch. No credential, no network, no spend.

All command checks are deterministic. The cases that earn their
place are the ones that decide whether it is safe to put this in front of every
shell call in every session: it must never deny, it must fail open on every
failure it can meet, and it must not spend money on a command it has nothing to
say about.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO / "ops" / "command_precheck.py"
SPEC = importlib.util.spec_from_file_location("command_precheck", HOOK_PATH)
assert SPEC and SPEC.loader
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def run(payload, **patches):
    """Drive advisory() with a payload; return (0, note-or-empty).

    The tuple shape is kept from when this was a hook with an exit code, because
    every test below asserts the first element is 0 — that assertion IS the "it
    never denies" contract, and advisory() keeps it by never raising.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    with mock.patch.multiple(hook, **patches) if patches else _null():
        note = hook.advisory(payload)
    return 0, (note or "")


class _null:
    def __enter__(self): return None
    def __exit__(self, *a): return False


class AttendedFixture(unittest.TestCase):
    pass


class NeverDeniesTests(AttendedFixture):
    """A probabilistic refusal in front of every shell call turns a model's
    uncertainty into a blocked session. This must never happen."""




    def test_the_library_contains_no_denial(self):
        source = HOOK_PATH.read_text(encoding="utf-8")
        for forbidden in ("exit(2)", "exit (2)", '"block"', "'block'", "permissionDecision"):
            self.assertNotIn(forbidden, source,
                             f"{forbidden} would let this gate stop a session")

    def test_the_library_is_not_a_script_entrypoint(self):
        """A file with a shebang or a main guard is a NEW sealed ingress, and
        admitting one costs a registry successor with a production migration.
        Uses the inventory's own regex, not a substring search."""
        import re as _re
        source = HOOK_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        self.assertIsNone(
            _re.compile(r"if\s+__name__\s*==\s*[\"\']__main__[\"\']\s*:").search(source),
            "no main guard")

    def test_a_certain_failure_still_returns_a_note_not_a_refusal(self):
        code, out = run({"tool_name": "Bash", "tool_input": {"command": "./ops/ci.sh --nope"}},
                        check=lambda command, repo=None: (0.99, {"undeclared_options": ["--nope"]}, {"undeclared_option": 0.99}),
                        _log=lambda record: None)
        self.assertEqual(code, 0, "a warning is not a denial")
        self.assertIn("NOT been blocked", out)

    def test_the_warning_names_the_reason(self):
        _, out = run({"tool_name": "Bash", "tool_input": {"command": "x"}},
                     check=lambda command, repo=None: (0.95, {"paths_that_do_not_exist": ["ops/gone-for-good.py"]}, {"missing_path": 0.95}),
                     _log=lambda record: None)
        self.assertIn("ops/gone-for-good.py", out,
                      "a warning without its reason is noise a session learns to skip")


class FailsOpenTests(AttendedFixture):
    def test_unparseable_payload(self):
        self.assertEqual(run("not json at all")[0], 0)

    def test_a_missing_credential_is_silent(self):
        def boom(command, repo=None):
            raise RuntimeError("cannot read the checkout")
        code, out = run({"tool_name": "Bash", "tool_input": {"command": "git push"}}, check=boom)
        self.assertEqual(code, 0)
        self.assertEqual(out, "", "an outage must not print at the session")

    def test_a_service_outage_is_silent(self):
        def boom(command, repo=None):
            raise TimeoutError("service did not answer")
        self.assertEqual(run({"tool_name": "Bash",
                              "tool_input": {"command": "git push"}}, check=boom)[0], 0)

    def test_a_logging_failure_never_reaches_the_session(self):
        def boom(record):
            raise OSError("disk full")
        code, _ = run({"tool_name": "Bash", "tool_input": {"command": "git push"}},
                      check=lambda command, repo=None: (0.99, {"paths_that_do_not_exist": ["y"]}, {"missing_path": 0.99}), _log=boom)
        self.assertEqual(code, 0)


class SpendsNothingUnnecessarilyTests(AttendedFixture):
    """Three free filters run before any request. Each one is asserted, because
    a filter that silently stops working costs money on every command."""

    def test_a_non_bash_tool_never_reaches_the_judgment(self):
        called = []
        run({"tool_name": "Read", "tool_input": {"file_path": "x"}},
            check=lambda command, repo=None: called.append(command))
        self.assertEqual(called, [])

    def test_a_pure_read_never_reaches_the_judgment(self):
        called = []
        run({"tool_name": "Bash", "tool_input": {"command": "grep -n foo ops/ai_eval.py"}},
            check=lambda command, repo=None: called.append(command))
        self.assertEqual(called, [], "about two thirds of real traffic stops here")

    def test_an_empty_command_never_reaches_the_judgment(self):
        called = []
        run({"tool_name": "Bash", "tool_input": {"command": "   "}},
            check=lambda command, repo=None: called.append(command))
        self.assertEqual(called, [])


    def test_the_kill_switch_stops_everything(self):
        called = []
        with mock.patch.dict(os.environ, {"CARR_PRECHECK": "0"}):
            code, _ = run({"tool_name": "Bash", "tool_input": {"command": "./ops/ci.sh --nope"}},
                          check=lambda command, repo=None: called.append(command))
        self.assertEqual(code, 0)
        self.assertEqual(called, [])


class ModelRoomRouteTests(AttendedFixture):
    def test_rule_is_available_from_another_checkout(self):
        with tempfile.TemporaryDirectory() as other_checkout:
            self.assertIn("Model Room", hook.model_room_rule(other_checkout))






class DeterministicCheckTests(AttendedFixture):
    def test_missing_path_warns_and_existing_path_does_not(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ops" / "exists.py"
            path.parent.mkdir()
            path.write_text("pass\n")
            self.assertEqual(hook.check("python3 ops/exists.py", root), (None, {}, {}))
            probability, facts, reasons = hook.check("git add ops/gone.py", root)
            self.assertEqual(probability, 1.0)
            self.assertEqual(facts, {"paths_that_do_not_exist": ["ops/gone.py"]})
            self.assertEqual(reasons, {"missing_path": 1.0})

    def test_globs_heredocs_and_creating_commands_do_not_warn(self):
        with tempfile.TemporaryDirectory() as root:
            for command in ("git add tools/example*",
                            "python3 - <<'PY'\nprint('ops/gone.py')\nPY",
                            "mkdir -p ops/new && ls ops/new",
                            "git checkout origin/main -- ops/gone.py && cat ops/gone.py"):
                self.assertIsNone(hook.check(command, root)[0], command)

    def test_outside_repository_paths_do_not_warn(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(hook.check("cat ../ops/gone.py", root), (None, {}, {}))


class RepoRootTests(AttendedFixture):
    """The hook runs in the canonical checkout; the session usually does not.

    The first live run of this pre-check warned at 0.94 about a file the session
    had just written, because canonical had never seen it. A gate that cries
    wolf on every new file in every worktree gets scrolled past.
    """

    def test_the_session_directory_wins_over_this_files_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            Path(os.path.join(root, ".git")).write_text("gitdir: elsewhere\n")
            self.assertEqual(hook.repo_root(root), root)

    def test_a_subdirectory_resolves_to_its_checkout(self):
        """A command can run from a subdirectory while naming paths from the
        root, so cwd is not itself the answer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            Path(os.path.join(root, ".git")).mkdir()
            deep = os.path.join(root, "a", "b", "c")
            os.makedirs(deep)
            self.assertEqual(hook.repo_root(deep), root)

    def test_no_checkout_above_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(hook.repo_root(os.path.join(tmp, "nowhere")), hook.REPO)

    def test_an_empty_cwd_falls_back(self):
        self.assertEqual(hook.repo_root(""), hook.REPO)
        self.assertEqual(hook.repo_root(None), hook.REPO)

    def test_the_payload_cwd_actually_reaches_the_fact_gatherer(self):
        """Without this the fix is inert: repo_root can be perfect and the
        value still never leave advisory()."""
        seen = []
        run({"tool_name": "Bash", "tool_input": {"command": "./ops/ci.sh --nope"},
             "cwd": "/somewhere/else"},
            check=lambda command, repo=None: seen.append(repo) or (None, {}, {}))
        self.assertEqual(seen, [hook.REPO],
                         "a cwd outside any checkout must still be passed explicitly")

    def test_direct_exec_command_uses_its_workdir(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            Path(os.path.join(tmp, ".git")).mkdir()
            run({"tool_name": "exec_command",
                 "tool_input": {"cmd": "./ops/ci.sh --nope", "workdir": tmp},
                 "cwd": "/wrong"},
                check=lambda command, repo=None: seen.append((command, repo)) or
                (None, {}, {}))
            self.assertEqual(seen, [("./ops/ci.sh --nope", hook.repo_root(tmp))])



class ThresholdTests(AttendedFixture):
    def test_below_the_floor_is_silent_but_still_logged(self):
        logged = []
        code, out = run({"tool_name": "Bash", "tool_input": {"command": "git push"}},
                        check=lambda command, repo=None: (hook.WARN_AT - 0.01, {"paths_that_do_not_exist": ["b"]}, {"missing_path": hook.WARN_AT - 0.01}),
                        _log=logged.append)
        self.assertEqual(out, "", "below the floor nothing is said")
        self.assertEqual(len(logged), 1, "but it is recorded, so the floor can be re-derived")
        self.assertFalse(logged[0]["warned"])

    def test_the_floor_is_inclusive(self):
        _, out = run({"tool_name": "Bash", "tool_input": {"command": "git push"}},
                     check=lambda command, repo=None: (hook.WARN_AT, {"paths_that_do_not_exist": ["b"]}, {"missing_path": hook.WARN_AT}),
                     _log=lambda record: None)
        self.assertIn("PRE-CHECK", out)


class ReadDetectionTests(AttendedFixture):
    def test_reads_are_recognised(self):
        for command in ("grep -n foo bar.py", "cat ops/ci.sh", "git status",
                        "sed -n '1,10p' x.py", "ls ops | wc -l",
                        "git log --oneline -5 | head -3"):
            self.assertTrue(hook.is_pure_read(command), command)

    def test_writes_are_never_mistaken_for_reads(self):
        for command in ("git push origin HEAD", "rm -rf build", "cat x > y",
                        "grep -n foo bar.py && git commit -m x",
                        "echo hi > /tmp/f", "cp a b", "sed -i s/a/b/ x"):
            self.assertFalse(hook.is_pure_read(command), command)

    def test_a_read_piped_into_a_write_is_not_a_read(self):
        self.assertFalse(hook.is_pure_read("cat x | tee /tmp/out"))


class LogSizeTests(AttendedFixture):
    def test_the_log_is_capped_by_size_not_by_count(self):
        """A count cap is not a size cap: one enormous line defeats it."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "log.jsonl")
            Path(path).write_text("x" * (hook.LOG_MAX_BYTES + 10_000) + "\n")
            with mock.patch.object(hook, "LOG", path):
                hook._log({"command": "y", "p": 0.1, "facts": {}, "warned": False})
            self.assertLess(os.path.getsize(path), hook.LOG_MAX_BYTES,
                            "the cap must actually shrink an oversized log")


if __name__ == "__main__":
    unittest.main(verbosity=1)
