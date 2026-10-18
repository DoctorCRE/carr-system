#!/usr/bin/env python3
"""Selftest for hooks/lint-gate.py, the PostToolUse writing-lint gate.

Offline and deterministic: the real hook runs as a subprocess with HOME, the
guard log, CARR_ROOT (so run.sh is a scripted stand-in) and the Jev client all
pointed at a temporary directory. Nothing touches the vault, the network or the
canonical checkout's out/ folder.

What it holds:
  * surface_for() scoping -- SURFACES map a vault path to a linter surface,
    INTERNAL folders win over any surface, GENERATED renders are skipped, and
    anything outside the vault, in a scratchpad, or of a non-text type is None.
  * end to end, a surface write runs `run.sh lint <path> --surface <s>` and puts
    a HARD BAN or REVIEW message in front of the session; an internal or
    generated write never reaches run.sh at all.
  * FAILS OPEN AND SILENT: bad stdin, a broken run.sh, a payload the post-write
    receipt cannot be built from, and a malformed tool_input all exit 0 with no
    traceback and no lint message.
  * the post-write Jev review receipt is schema jev-post-write-review/v2, carries
    exactly POSTWRITE_RECEIPT_KEYS, passes validate_postwrite_receipt, and its
    uncalibrated findings report effect "advisory_only" and cannot block writes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "lint-gate.py"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "ops"))
from git_env import fixture_env  # noqa:E402
from lib.selftest_harness import HookSandbox, load_hook  # noqa:E402
from lib.rule_delivery_preuse import (  # noqa:E402
    POSTWRITE_RECEIPT_KEYS, POSTWRITE_RECEIPT_SCHEMA, validate_postwrite_receipt,
)

RETIRED_EFFECTS = {"advisory_only", "shadow_would_block_advisory_only"}


lint = load_hook("lint-gate")
VAULT_TAIL = Path(lint.VAULT).relative_to(Path.home()).parts

# Swapped in for ops/typesafe_client.py at the one seam the reviewer loads it
# through. Loaded by sitecustomize in the child, so the real hook runs unedited.
FAKE_CLIENT = '''
import json, os
def noul(instructions, true=None, false=None):
    return {"type": "noul", "instructions": instructions}
def ask(state, questions, timeout=None, api_key=None, **kwargs):
    cfg = json.loads(os.environ["LINT_GATE_FAKE_JEV"])
    if cfg.get("error"):
        raise TimeoutError("fake outage")
    return {"model": "jev-1.13.0", "answers": {
        q: {"type": "noul", "noul": cfg["value"]} for q in questions}}
'''
SITECUSTOMIZE = '''
import importlib.util, os
_real = importlib.util.spec_from_file_location
def _redirect(name, location, *args, **kwargs):
    if str(location).endswith(os.path.join("ops", "typesafe_client.py")):
        location = os.environ["LINT_GATE_FAKE_CLIENT"]
    return _real(name, location, *args, **kwargs)
importlib.util.spec_from_file_location = _redirect
'''



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
    """The real hook, spawned with HOME, CARR_ROOT and the Jev client stubbed."""

    def setUp(self):
        self.sandbox = self.enterContext(HookSandbox())
        self.root = self.sandbox.root
        self.home = self.sandbox.home
        self.vault = self.home.joinpath(*VAULT_TAIL)
        self.vault.mkdir(parents=True)
        self.carr = self.sandbox.carr
        self.run_sh = self.sandbox.run_sh
        self.guard_log = self.sandbox.guard_log
        stub = self.root / "stub"
        stub.mkdir()
        (stub / "sitecustomize.py").write_text(SITECUSTOMIZE)
        (stub / "typesafe_client.py").write_text(FAKE_CLIENT)
        self.env = self.sandbox.env
        # Attended fake reviews run regardless of the parent worker mode.
        # Individual worker fixtures explicitly override this child setting.
        self.env.pop("CARR_JEV_WORKER", None)
        self.env.update({
            "HOME": str(self.home), "CARR_ROOT": str(self.carr),
            "CARR_HOOK_GUARD_LOG": str(self.guard_log),
            "PYTHONPATH": str(stub),
            "CARR_JEV_SEMANTIC_CACHE": str(self.root / "semantic-cache.json"),
            "LINT_GATE_FAKE_CLIENT": str(stub / "typesafe_client.py"),
            "LINT_GATE_FAKE_JEV": json.dumps({"value": 0.1}),
        })

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
    def receipts(outputs):
        found = []
        for out in outputs:
            try:
                body = json.loads(out["additionalContext"])
            except ValueError:
                continue
            if isinstance(body, dict) and body.get("schema") == POSTWRITE_RECEIPT_SCHEMA:
                found.append(body)
        return found

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

    def test_worker_mode_skips_judgment_and_preserves_writing_lint(self):
        path = self.vault_file("Outreach/intro.md")
        self.sandbox.reply("FAIL hard-ban: em dash\n")
        out = self.spawn(self.write_payload(path), CARR_JEV_WORKER="off")
        self.assertEqual(self.receipts(out), [])
        self.assertEqual(len(self.lint_messages(out)), 1)
        self.assertEqual(self.sandbox.calls(), [f"lint {path} --surface email"])

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
        # The receipt cannot validate without session_id / tool_use_id. That
        # must cost the receipt, never the write: exit 0, no traceback.
        path = self.vault / "Outreach" / "intro.md"
        path.parent.mkdir(parents=True)
        path.write_text("draft\n")
        self.sandbox.reply("FAIL hard-ban\n")
        out = self.spawn({"tool_name": "Write",
                          "tool_input": {"file_path": str(path)}})
        self.assertEqual(self.receipts(out), [])
        # the lint half still runs after the receipt half failed
        self.assertEqual(len(self.lint_messages(out)), 1)

    def test_a_malformed_tool_input_does_not_crash(self):
        self.assertEqual(self.spawn({"tool_name": "Write", "session_id": "s",
                                     "tool_use_id": "t", "tool_input": "oops"}), [])


class PostWriteReceiptTests(HookFixture):
    def make_repo(self):
        repo = self.root / "repo"
        (repo / "src").mkdir(parents=True)
        target = repo / "src" / "a.py"
        target.write_text("value = 1\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=self.env)
        subprocess.run(["git", "add", "src/a.py"], cwd=repo, check=True, env=self.env)
        # swallowed_failure is one of jev_code_review.SIGNATURES, so the diff
        # is a Jev candidate and the stubbed client is actually asked.
        target.write_text("try:\n    upload()\nexcept Exception:\n    pass\nvalue = 2\n")
        return target

    def only_receipt(self, outputs):
        [receipt] = self.receipts(outputs)
        self.assertEqual(receipt["schema"], "jev-post-write-review/v2")
        self.assertEqual(set(receipt), set(POSTWRITE_RECEIPT_KEYS))
        self.assertTrue(validate_postwrite_receipt(receipt, repo=REPO), receipt)
        return receipt

    def test_confident_findings_report_effect_advisory(self):
        target = self.make_repo()
        out = self.spawn(self.write_payload(target),
                         LINT_GATE_FAKE_JEV=json.dumps({"value": 0.95}))
        receipt = self.only_receipt(out)
        self.assertEqual(receipt["status"], "reviewed")
        self.assertEqual(receipt["models"], ["jev-1.13.0"], "the stub, not a live call")
        self.assertEqual(receipt["paths"][0]["status"], "jev_reviewed")
        self.assertTrue(receipt["findings"])
        effects = {f["effect"] for f in receipt["findings"]}
        self.assertEqual(effects, {"advisory"})
        self.assertFalse(effects & RETIRED_EFFECTS)

    def test_below_threshold_is_reviewed_with_no_findings(self):
        receipt = self.only_receipt(self.spawn(self.write_payload(self.make_repo())))
        self.assertEqual(receipt["status"], "reviewed")
        self.assertEqual(receipt["findings"], [])

    def test_a_judge_outage_is_an_unavailable_receipt_not_a_crash(self):
        out = self.spawn(self.write_payload(self.make_repo()),
                         LINT_GATE_FAKE_JEV=json.dumps({"error": True}))
        receipt = self.only_receipt(out)
        self.assertEqual(receipt["status"], "unavailable")
        self.assertIn("TimeoutError", receipt["reason"])

    def test_a_prose_write_is_a_skipped_receipt(self):
        path = self.vault / "Outreach" / "intro.md"
        path.parent.mkdir(parents=True)
        path.write_text("draft\n")
        receipt = self.only_receipt(self.spawn(self.write_payload(path)))
        self.assertEqual(receipt["status"], "skipped")
        self.assertEqual(receipt["reason"], "no_supported_code_paths")

    def test_worker_off_launcher_still_runs_attended_receipt_fixtures(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "PostWriteReceiptTests.test_below_threshold_is_reviewed_with_no_findings"],
            env={**fixture_env(), "CARR_JEV_WORKER": "off"},
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_retired_effect_label_survives_in_the_hook(self):
        source = HOOK.read_text()
        for label in RETIRED_EFFECTS:
            self.assertNotIn(f'"{label}"', source)


if __name__ == "__main__":
    unittest.main()
