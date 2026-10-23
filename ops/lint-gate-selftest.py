#!/usr/bin/env python3
"""Selftest for hooks/lint-gate.py, the PostToolUse writing-lint gate.

Offline and deterministic: the real hook runs as a subprocess with HOME, the
guard log, and CARR_ROOT (so run.sh is a scripted stand-in) all
pointed at a temporary directory. Nothing touches the vault, the network or the
canonical checkout's out/ folder.

What it holds:
  * surface_for() scoping -- SURFACES map a vault path to a linter surface,
    INTERNAL folders win over any surface, GENERATED renders are skipped, and
    anything outside the vault, in a scratchpad, or of a non-text type is None.
  * end to end, a surface write runs `run.sh lint <path> --surface <s>` and puts
    a HARD BAN or REVIEW message in front of the session; an internal or
    generated write never reaches run.sh at all.
  * FAILS OPEN AND SILENT: bad stdin, a broken run.sh, a payload that
    lacks session IDs, and a malformed tool_input all exit 0 with no
    traceback and no lint message.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "lint-gate.py"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "ops"))
from lib.selftest_harness import HookSandbox, load_hook  # noqa:E402
lint = load_hook("lint-gate")
VAULT_TAIL = Path(lint.VAULT).relative_to(Path.home()).parts

class SurfaceScopingTests(unittest.TestCase):
    """surface_for(), in process, against the module's own VAULT constant."""

    def v(self, rel):
        return os.path.join(lint.VAULT, rel)

    def test_surfaces_map_by_first_matching_fragment(self):
        cases = {
            "Marketing/Social Media/post.md": "social",
            "DNA/Marketing/voice.md": "social",
            "Brand/Marketing/flyer.html": "social",
            "Outreach/cold-open.md": "email",
            "DNA/templates.md": "email",
            "intake/new-client.md": "proposal",
            "Output/benefit.md": "proposal",
            "Clients/acme-benefit-summary.txt": "proposal",
            "Clients/proposals/draft.md": "proposal",
            "DNA/Deal Management/proposal-letter.md": "proposal",
            "Web/GBP/profile.md": "web",
            "Web/SEO/keywords.md": "web",
            "Web/landing-page.html": "web",
        }
        for rel, surface in cases.items():
            self.assertEqual(lint.surface_for(self.v(rel)), surface, rel)

    def test_matching_is_case_insensitive(self):
        self.assertEqual(lint.surface_for(self.v("OUTREACH/x.md")), "email")

    def test_internal_folders_beat_any_surface(self):
        for rel in ("DNA/Deal Management/record-layer/synthetic-onboarding-runbook.md",
                    "DNA/Team/Marketing/starter-kit.md",
                    "00_Context/Outreach/notes.md",
                    "Automation/Outreach/job.md",
                    "Archive/Marketing/old.md",
                    "idea-inbox/landing idea.md",
                    "_to_delete/proposals/x.md",
                    "_asset_staging/Marketing/raw.md"):
            self.assertIsNone(lint.surface_for(self.v(rel)), rel)

    def test_generated_renders_are_skipped(self):
        for name in lint.GENERATED:
            self.assertIsNone(lint.surface_for(self.v(f"Outreach/{name}")), name)

    def test_outside_vault_scratch_and_wrong_type_are_skipped(self):
        self.assertIsNone(lint.surface_for("/elsewhere/Outreach/x.md"))
        self.assertIsNone(lint.surface_for(self.v("Outreach/scratchpad/x.md")))
        self.assertIsNone(lint.surface_for(self.v("Outreach/out/x.md")))
        self.assertIsNone(lint.surface_for(self.v("Outreach/.generations/x.md")))
        self.assertIsNone(lint.surface_for(self.v("Outreach/sheet.xlsx")))
        self.assertIsNone(lint.surface_for(self.v("Clients/notes.md")))

    def test_vault_is_derived_from_home(self):
        self.assertTrue(lint.VAULT.startswith(os.path.expanduser("~")))


class HookFixture(unittest.TestCase):
    """The real hook, spawned with HOME, CARR_ROOT stubbed."""

    def setUp(self):
        self.sandbox = self.enterContext(HookSandbox())
        self.root = self.sandbox.root
        self.home = self.sandbox.home
        self.vault = self.home.joinpath(*VAULT_TAIL)
        self.vault.mkdir(parents=True)
        self.carr = self.sandbox.carr
        self.run_sh = self.sandbox.run_sh
        self.guard_log = self.sandbox.guard_log
        self.env = self.sandbox.env

    def spawn(self, payload, **env):
        res = self.sandbox.fire("lint-gate", payload, env=env, timeout=60)
        self.assertEqual(res.code, 0, res.stderr)
        self.assertEqual(res.stderr, "", "the hook must never speak on stderr")
        return [envelope["hookSpecificOutput"] for envelope in res.envelopes]

    @staticmethod
    def write_payload(path, **extra):
        return {"tool_name": "Write", "session_id": "s", "tool_use_id": "t",
                "tool_input": {"file_path": str(path), "content": "x"}, **extra}

    @staticmethod
    def lint_messages(outputs):
        return [o["additionalContext"] for o in outputs
                if o["additionalContext"].startswith("WRITING-LINT")]


class LintPathTests(HookFixture):
    def vault_file(self, rel):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("draft\n")
        return path

    def test_surface_write_runs_the_linter_and_reports_a_hard_ban(self):
        path = self.vault_file("Marketing/Social Media/post.md")
        self.sandbox.reply("FAIL hard-ban: em dash\n")
        msgs = self.lint_messages(self.spawn(self.write_payload(path)))
        self.assertEqual(self.sandbox.calls(), [f"lint {path} --surface social"])
        self.assertEqual(len(msgs), 1)
        self.assertIn("HARD BAN HIT on Marketing/Social Media/post.md", msgs[0])
        self.assertIn("(surface: social)", msgs[0])
        self.assertIn("lint-gate REPORT", self.guard_log.read_text())

    def test_review_items_get_the_review_message(self):
        path = self.vault_file("Outreach/intro.md")
        self.sandbox.reply("REVIEW: hedge word\n")
        [msg] = self.lint_messages(self.spawn(self.write_payload(path)))
        self.assertIn("REVIEW items on Outreach/intro.md (surface: email)", msg)

    def test_clean_lint_is_silent(self):
        path = self.vault_file("Outreach/intro.md")
        self.sandbox.reply("ok 0 findings\n")
        self.assertEqual(self.lint_messages(self.spawn(self.write_payload(path))), [])
        self.assertEqual(len(self.sandbox.calls()), 1)

    def test_internal_and_generated_never_reach_run_sh(self):
        self.sandbox.reply("FAIL hard-ban\n")
        for rel in ("DNA/Deal Management/record-layer/spec.md",
                    "Outreach/open-loops.md"):
            path = self.vault_file(rel)
            self.assertEqual(self.lint_messages(self.spawn(self.write_payload(path))), [], rel)
        self.assertEqual(self.sandbox.calls(), [])

    def test_non_write_tools_are_ignored(self):
        path = self.vault_file("Outreach/intro.md")
        self.sandbox.reply("FAIL hard-ban\n")
        payload = {"tool_name": "Read", "session_id": "s", "tool_use_id": "t",
                   "tool_input": {"file_path": str(path)}}
        self.assertEqual(self.spawn(payload), [])
        self.assertEqual(self.sandbox.calls(), [])


class FailOpenTests(HookFixture):
    def test_unparseable_stdin_is_silent(self):
        self.assertEqual(self.spawn("{not json"), [])
        self.assertEqual(self.spawn(""), [])

    def test_a_broken_run_sh_is_silent_to_the_session_but_logged(self):
        path = self.vault / "Outreach" / "intro.md"
        path.parent.mkdir(parents=True)
        path.write_text("draft\n")
        self.run_sh.chmod(0o644)                       # PermissionError on exec
        self.sandbox.reply("FAIL hard-ban\n")
        self.assertEqual(self.lint_messages(self.spawn(self.write_payload(path))), [])
        self.assertIn("lint-gate ALLOW(internal-error)", self.guard_log.read_text())

    def test_a_payload_without_session_ids_does_not_crash(self):
        path = self.vault / "Outreach" / "intro.md"
        path.parent.mkdir(parents=True)
        path.write_text("draft\n")
        self.sandbox.reply("FAIL hard-ban\n")
        out = self.spawn({"tool_name": "Write",
                          "tool_input": {"file_path": str(path)}})
        self.assertEqual(len(self.lint_messages(out)), 1)

    def test_a_malformed_tool_input_does_not_crash(self):
        self.assertEqual(self.spawn({"tool_name": "Write", "session_id": "s",
                                     "tool_use_id": "t", "tool_input": "oops"}), [])


class CodeWriteTests(HookFixture):
    def test_code_write_is_silent_without_a_model_review_or_receipt(self):
        target = self.root / "a.py"
        target.write_text("try:\n    upload()\nexcept Exception:\n    pass\n")
        self.assertEqual(self.spawn(self.write_payload(target)), [])
        self.assertEqual(self.sandbox.calls(), [])


if __name__ == "__main__":
    unittest.main()
