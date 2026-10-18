#!/usr/bin/env python3
"""Offline regression tests for the brief-check CLI and report interface.

Fixture briefs and PR JSON were captured from past work. Synthetic review
reproductions exercise parsing, verdicts, provenance and fake publication
transports. Paid Jev and live GitHub mutations are never used.
"""
from __future__ import annotations

import base64
import concurrent.futures
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(REPO, "ops", "fixtures", "brief-check")

_spec = importlib.util.spec_from_file_location("brief_check", os.path.join(REPO, "ops", "brief-check.py"))
assert _spec is not None and _spec.loader is not None, "cannot load ops/brief-check.py"
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)


_lock_root = tempfile.TemporaryDirectory()
_lock_env = patch.dict(os.environ, {"CARR_BRIEF_LOCK_ROOT": _lock_root.name})


def setUpModule():
    # Publication locks and journals never touch the operator's ~/.cache.
    _lock_env.start()


def tearDownModule():
    _lock_env.stop()
    _lock_root.cleanup()


def brief(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as handle:
        return handle.read()


def pr(number):
    with open(os.path.join(FIX, f"pr{number}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def in_base(*paths):
    present = set(paths)
    return lambda path: path in present


def verdicts(report):
    return [item["verdict"] for item in report["requirements"]]


class ParseRequirements(unittest.TestCase):
    def test_trailing_context_after_blank_is_not_an_obligation(self):
        source = ("BUILD (all required):\n1. Change hooks/escalation-gate.py\n\n"
                  "Context: the old notes live in docs/old-notes.md if useful.\n")
        report = bc.check(source, pr(1544), exists_in_base=in_base())
        self.assertEqual(report["requirements"][0]["text"], "Change hooks/escalation-gate.py")
        self.assertEqual(verdicts(report), ["met"])
        self.assertEqual(bc.report_status(report), 0)

    def test_mixed_case_conduct_section_ends_build_list(self):
        for separator in ("\n", "\n\n"):
            with self.subTest(separator=separator):
                source = ("BUILD (all required):\n1. Change hooks/escalation-gate.py" + separator
                          + "Rules for this job (all required):\n1. Never merge\n")
                self.assertEqual(bc.parse_requirements(source),
                                 [{"n": 1, "text": "Change hooks/escalation-gate.py"}])

    def test_blank_separated_items_and_indented_continuation_survive(self):
        source = ("BUILD (all required):\n1. First\n\n  continuation\n\n"
                  "  2. Second\n\n\n3. Third\n")
        self.assertEqual(bc.parse_requirements(source),
                         [{"n": 1, "text": "First continuation"},
                          {"n": 2, "text": "Second"}, {"n": 3, "text": "Third"}])

    def test_empty_obligations_are_rejected_before_grading(self):
        for source in ("1. \n", "1.\n", "1. \t\n",
                       "1. First\n2. \n\nContext: notes\n"):
            with self.subTest(source=source), self.assertRaisesRegex(bc.BriefError, "empty requirement"):
                bc.check("BUILD (all required):\n" + source, pr(1544))

    def test_empty_first_line_can_have_indented_obligation(self):
        self.assertEqual(bc.parse_requirements("BUILD (all required):\n1. \n  Add tests\n"),
                         [{"n": 1, "text": "Add tests"}])

    def test_gap_brief_build_list_excludes_the_job_rules(self):
        items = bc.parse_requirements(brief("gap1.md"))
        self.assertEqual([i["n"] for i in items], [1, 2, 3, 4, 5, 6])
        self.assertTrue(items[0]["text"].startswith("Measure first"))
        self.assertFalse(any("never merge" in i["text"] for i in items))

    def test_all_required_header_without_build_word(self):
        items = bc.parse_requirements(brief("board-size.md"))
        self.assertEqual(len(items), 4)
        self.assertTrue(items[3]["text"].startswith("Worktree:"))

    def test_markdown_heading_forms(self):
        self.assertEqual(len(bc.parse_requirements(brief("gatefix.md"))), 5)
        steps = bc.parse_requirements(brief("jevlint.md"))
        # The three numbered items under "The constraint you must design around"
        # are context, not the "Steps (all required)" list.
        self.assertEqual(len(steps), 6)
        self.assertTrue(steps[0]["text"].startswith("Pin jevlint"))

    def test_brief_without_a_required_list_is_an_error(self):
        with self.assertRaises(bc.BriefError):
            bc.parse_requirements("ROLE: x\n\nJust do the thing.\n")


class Verdicts(unittest.TestCase):
    def test_gatefix_against_its_merged_pr(self):
        report = bc.check(brief("gatefix.md"), pr(1544), exists_in_base=in_base("hooks/escalation-gate.py", "ops/ci.sh"))
        self.assertEqual(verdicts(report), ["needs judgment"] * 5)
        tests = report["requirements"][1]
        self.assertTrue(any("ops/escalation-gate-selftest.py" in e for e in tests["evidence"]))
        self.assertTrue(any("actions/runs/" in e for e in tests["evidence"]), tests["evidence"])

    def test_board_size_ci_command_is_evidenced_by_the_green_check(self):
        report = bc.check(brief("board-size.md"), pr(1553), exists_in_base=in_base("ops/ci.sh"))
        self.assertEqual(verdicts(report), ["needs judgment"] * 4)
        self.assertTrue(any("ops/ci.sh --strict" in e for e in report["requirements"][2]["evidence"]))

    def test_pending_ci_never_reads_as_met(self):
        report = bc.check(brief("gap9.md"), pr(1572), exists_in_base=in_base())
        self.assertNotIn("met", verdicts(report))
        self.assertNotIn("not met", verdicts(report))
        selftests = report["requirements"][4]
        self.assertTrue(any("complete obligation" in e for e in selftests["evidence"]))


    def test_root_cause_in_body_is_quoted_as_evidence(self):
        report = bc.check(brief("gap9.md"), pr(1572), exists_in_base=in_base())
        diagnose = report["requirements"][0]
        self.assertTrue(any(e.startswith("PR body:") and "Root cause" in e for e in diagnose["evidence"]), diagnose["evidence"])

    def test_tests_required_but_none_in_diff_is_not_met(self):
        stripped = copy.deepcopy(pr(1544))
        stripped["files"] = [f for f in stripped["files"] if "selftest" not in f["path"]]
        report = bc.check(brief("gatefix.md"), stripped, exists_in_base=in_base("hooks/escalation-gate.py"))
        self.assertEqual(report["requirements"][1]["verdict"], "not met")

    def test_red_ci_on_a_tests_requirement_is_not_met(self):
        red = copy.deepcopy(pr(1544))
        red["statusCheckRollup"][0]["conclusion"] = "FAILURE"
        report = bc.check(brief("gatefix.md"), red, exists_in_base=in_base("hooks/escalation-gate.py"))
        self.assertEqual(report["requirements"][1]["verdict"], "not met")
        self.assertTrue(any("FAILURE" in e for e in report["requirements"][1]["evidence"]))

    def test_named_new_file_missing_everywhere_is_not_met(self):
        # This job's own brief asks for ops/brief-check.py; an unrelated PR lacks it.
        report = bc.check(brief("gap17.md"), pr(1544), exists_in_base=in_base(), local_reference=lambda p: False)
        self.assertEqual(report["requirements"][1]["verdict"], "not met")
        self.assertTrue(any("ops/brief-check.py" in e for e in report["requirements"][1]["evidence"]))

    def test_local_untracked_reference_is_not_a_missing_deliverable(self):
        # gap17 item 4 cites out/orch/gaps/gap1.md, which lives untracked on disk.
        report = bc.check(brief("gap17.md"), pr(1544), exists_in_base=in_base(),
                          local_reference=lambda p: p.startswith("out/"))
        self.assertNotEqual(report["requirements"][3]["verdict"], "not met")

    def test_gitignored_inputs_and_either_home_are_not_missing_deliverables(self):
        # jevlint item 2 reads rules from out/orch/matt/*.md (gitignored inputs);
        # item 4 asks for "a script (tools/ or ops/)" and the PR used ops/.
        report = bc.check(brief("jevlint.md"), pr(1545), exists_in_base=in_base(),
                          local_reference=lambda p: p.startswith("out/"))
        self.assertNotEqual(report["requirements"][1]["verdict"], "not met")
        self.assertNotEqual(report["requirements"][3]["verdict"], "not met", report["requirements"][3]["evidence"])
        self.assertTrue(any("diff: `ops/`" in e for e in report["requirements"][3]["evidence"]))

    def test_list_shaped_body_ask_names_each_missing_part(self):
        # #1545's body covers what the rule catches and what was declined, but
        # carries no eval table and no calls-per-PR number.
        report = bc.check(brief("jevlint.md"), pr(1545), exists_in_base=in_base())
        item = report["requirements"][5]
        self.assertEqual(item["verdict"], "needs judgment")
        missing = next(e for e in item["evidence"] if e.startswith("PR body has nothing on:"))
        self.assertIn("eval table", missing)
        self.assertIn("calls-per-pr measurement", missing)
        self.assertNotIn("catche", missing)
        self.assertNotIn("declined", missing)

    def test_tests_first_step_of_jevlint(self):
        report = bc.check(brief("jevlint.md"), pr(1545), exists_in_base=in_base())
        self.assertEqual(report["requirements"][4]["verdict"], "needs judgment")

    def test_diff_evidence_links_pin_the_inspected_head(self):
        report = bc.check(brief("gatefix.md"), pr(1544), exists_in_base=in_base("hooks/escalation-gate.py"))
        head = report["head"]
        self.assertTrue(any(f"/blob/{head}/hooks/escalation-gate.py" in e for e in report["requirements"][0]["evidence"]))


class BriefStamp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "gap9.md")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(brief("gap9.md"))
        self.sha = hashlib.sha256(brief("gap9.md").encode()).hexdigest()

    def test_stamp_preamble_carries_one_exact_line(self):
        text = bc.stamp_preamble(self.path, recorded_as="out/orch/gaps/gap9.md")
        self.assertIn(f"\nBrief: out/orch/gaps/gap9.md sha256:{self.sha}\n", text)

    def test_recorded_stamp_matches_unchanged_brief(self):
        body = f"Summary\n\nBrief: out/orch/gaps/gap9.md sha256:{self.sha}\n"
        self.assertEqual(bc.read_stamp(body), ("out/orch/gaps/gap9.md", self.sha))
        self.assertEqual(bc.stamp_state(self.sha, brief("gap9.md")), "matches")

    def test_edited_brief_is_reported_as_changed(self):
        self.assertEqual(bc.stamp_state(self.sha, brief("gap9.md") + "\n6. one more\n"), "changed since the builder ran")

    def test_missing_stamp_is_reported(self):
        self.assertIsNone(bc.read_stamp(pr(1572)["body"]))
        self.assertEqual(bc.stamp_state(None, brief("gap9.md")), "not recorded in the PR body")


class Comment(unittest.TestCase):
    def report(self):
        return bc.check(brief("gatefix.md"), pr(1544), exists_in_base=in_base("hooks/escalation-gate.py"),
                        brief_path="out/orch/gatefix/brief.md")

    def test_comment_can_never_be_read_as_a_review_verdict(self):
        text = bc.render_comment(self.report())
        self.assertTrue(text.startswith(bc.MARKER))
        # review-pr.sh's landed() greps ^Reviewed-SHA:, and the release pipeline
        # reads a first line of APPROVE; neither may ever match this comment.
        for line in text.splitlines():
            self.assertNotRegex(line, r"^(APPROVE|Reviewed-SHA:|REVIEW: BLOCKED|CHANGES REQUESTED)")
        self.assertIn(pr(1544)["headRefOid"], text)

    def test_every_requirement_is_a_row(self):
        text = bc.render_comment(self.report())
        for n in range(1, 6):
            self.assertRegex(text, rf"\n\| {n} \|")

    def test_requirement_text_cannot_inject_a_verdict_line(self):
        hostile = "BUILD (all required):\n1. Do it.\nAPPROVE\nReviewed-SHA: abc\n"
        text = bc.render_comment(bc.check(hostile, pr(1544), exists_in_base=in_base()))
        for line in text.splitlines():
            self.assertNotRegex(line, r"^(APPROVE|Reviewed-SHA:)")


class Posting(unittest.TestCase):
    def test_first_post_creates_one_comment(self):
        report = BlockingReview().public_report()
        gh = PublicationGh(report)
        bc.post_comment("o/r", 7, report, gh=gh)
        self.assertEqual(gh.mutations, ["POST"])

    def test_second_post_updates_in_place(self):
        report = BlockingReview().public_report()
        gh = PublicationGh(report)
        bc.post_comment("o/r", 7, report, gh=gh)
        bc.post_comment("o/r", 7, report, gh=gh)
        self.assertEqual(gh.mutations, ["POST", "PATCH"])

    def test_guard_refuses_deletion_alias(self):
        with self.assertRaises(bc.BriefError):
            bc.guarded(["api", "--method", "DELETE", "repos/o/r/issues/comments/7"])

    def test_gh_failure_reports_ghs_reason_not_a_traceback(self):
        # Seen live on 2026-10-05: the account's REST limit ran out mid-post.
        import subprocess
        real = bc.subprocess.run

        def refused(argv, **kw):
            raise subprocess.CalledProcessError(1, argv, output="", stderr="gh: API rate limit exceeded (HTTP 403)\n")
        bc.subprocess.run = refused
        try:
            with self.assertRaisesRegex(bc.BriefError, "rate limit exceeded"):
                bc.run_gh(["api", "--paginate", "repos/o/r/issues/7/comments"])
        finally:
            bc.subprocess.run = real

    def test_guard_refuses_anything_but_comment_calls(self):
        with self.assertRaises(bc.BriefError):
            bc.guarded(["pr", "review", "7", "--approve"])
        with self.assertRaises(bc.BriefError):
            bc.guarded(["pr", "merge", "7"])


class JevResidue(unittest.TestCase):
    def test_only_needs_judgment_items_are_asked_and_the_answer_is_labelled(self):
        report = bc.check(brief("gatefix.md"), pr(1544), exists_in_base=in_base("hooks/escalation-gate.py"))
        seen = {}

        def judge(subject, questions, **kw):
            seen["subject"], seen["questions"], seen["kw"] = subject, questions, kw
            return {"answers": {q: {"type": "noul", "noul": 0.9} for q in questions}}

        bc.add_jev(report, judge=judge, noul=lambda text: {"type": "noul", "instructions": text})
        self.assertEqual(sorted(seen["questions"]), ["r1", "r2", "r3", "r4", "r5"])
        self.assertEqual(seen["kw"].get("caller"), "brief_check")
        self.assertEqual(verdicts(report), ["needs judgment"] * 5)
        self.assertIn("Jev (advisory", report["requirements"][0]["jev"])
        self.assertRegex(report["requirements"][2]["jev"], r"^Jev \(advisory, needs-judgment residue\): ")
        self.assertIn("Jev (advisory", bc.render_comment(report))

    def test_jev_failure_is_said_not_hidden(self):
        report = bc.check(brief("gatefix.md"), pr(1544), exists_in_base=in_base("hooks/escalation-gate.py"))

        def judge(subject, questions, **kw):
            raise RuntimeError("HTTP 402")

        bc.add_jev(report, judge=judge, noul=lambda text: text)
        self.assertRegex(report["requirements"][2]["jev"], r"Jev unavailable")
        self.assertEqual(report["requirements"][2]["verdict"], "needs judgment")


class BlockingReview(unittest.TestCase):
    def report(self, text, data=None, **kwargs):
        return bc.check("BUILD (all required):\n1. " + text, data or pr(1544), **kwargs)

    def test_03_blank_indented_and_conduct_sections(self):
        for header in ("## Rules (all required)", "BOUNDARIES (all required):"):
            source = header + "\n1. Never merge\n\nBUILD (all required):\n1. First\n\n  2. Second\n"
            self.assertEqual(bc.parse_requirements(source), [{"n": 1, "text": "First"}, {"n": 2, "text": "Second"}])

    def test_03_duplicate_numbers_are_rejected(self):
        with self.assertRaises(bc.BriefError):
            bc.parse_requirements("BUILD (all required):\n1. First\n1. Second")

    def test_04_unexamined_clauses_never_pass(self):
        for text in ("Change hooks/escalation-gate.py and prove zero regressions over 3 production runs",
                     "PR body: include the root cause and measurements"):
            data = pr(1544)
            data["body"] = "No root cause was found. Measurements were not taken."
            self.assertEqual(verdicts(self.report(text, data)), ["needs judgment"])

    def test_05_deleted_outputs_and_tests_are_not_delivered(self):
        data = pr(1544)
        for file in data["files"]:
            file.update(changeType="DELETED", additions=0, deletions=20)
        for text in ("Add hooks/escalation-gate.py", "Add tests"):
            self.assertEqual(verdicts(self.report(text, data)), ["not met"])

    def test_06_tests_need_executable_paths_and_execution_checks(self):
        for conclusion in ("SKIPPED", "NEUTRAL", "SUCCESS"):
            data = pr(1544)
            data["files"] = [{"path": "tests/data.json", "changeType": "ADDED"}]
            data["statusCheckRollup"] = [{"name": "secret scan", "status": "COMPLETED", "conclusion": conclusion}]
            self.assertEqual(verdicts(self.report("Add tests", data)), ["needs judgment"])
        for conclusion in ("SKIPPED", "NEUTRAL"):
            data["files"] = [{"path": "tests/run.test.py", "changeType": "ADDED"}]
            data["statusCheckRollup"][0].update(name="unit tests", conclusion=conclusion)
            self.assertEqual(verdicts(self.report("Add tests", data)), ["needs judgment"])

    def test_07_existing_tests_and_negation_do_not_require_new_tests(self):
        data = pr(1544)
        data["files"] = [{"path": "hooks/escalation-gate.py", "changeType": "MODIFIED"}]
        data["statusCheckRollup"] = [{"name": "ops/ci.sh --strict", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        self.assertEqual(verdicts(self.report("Run existing tests", data)), ["met"])
        self.assertNotIn("not met", verdicts(self.report("Update hooks/escalation-gate.py without adding tests", data)))

    def test_08_unavailable_git_revision_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True, env=fixture_env())
            lookup = bc._git_exists(directory, "origin/main")
            self.assertIsNone(lookup("ops/ci.sh"))
            self.assertEqual(verdicts(self.report("Create ops/ci.sh", exists_in_base=lookup)), ["needs judgment"])

    def test_08_checkout_identity_and_bound_base(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True, env=fixture_env())
            subprocess.run(["git", "-C", directory, "remote", "add", "origin", "https://github.com/wrong/repo.git"], check=True, env=fixture_env())
            with self.assertRaises(bc.BriefError):
                bc._bound_base(directory, "jbookout/carr-system", "a" * 40)

    def test_09_hash_conflict_invalidates_verdicts_and_exit_status(self):
        data = pr(1544)
        data["body"] = bc.stamp_line("brief name.md", "0" * 64)
        report = self.report("Change hooks/escalation-gate.py", data)
        self.assertNotIn("met", verdicts(report))
        self.assertEqual(bc.report_status(report), 2)
        self.assertEqual(bc.read_stamp(data["body"]), ("brief name.md", "0" * 64))

    def test_10_complete_or_chain(self):
        data = pr(1544)
        data["files"] = [{"path": "ops/baz.py", "changeType": "ADDED"}]
        self.assertEqual(verdicts(self.report("Create ops/foo.py or ops/bar.py or ops/baz.py", data, exists_in_base=in_base())), ["met"])

    def test_11_full_obligations_and_body_evidence(self):
        tail = "archive-reason, bounded-retry, and non-deletion clauses"
        text = "Do " + "long requirement " * 30 + tail
        self.assertIn(tail, bc.render_comment(self.report(text)))
        data = pr(1544)
        data["body"] = "padding " * 50 + "root cause and measurements"
        report = self.report("PR body: root cause and measurements", data)
        self.assertTrue(any("root cause and measurements" in e for e in report["requirements"][0]["evidence"]))

    def test_12_json_pages_with_whitespace(self):
        text = " " * 80 + '[{"id":1}]\n[{"id":2}]  '
        self.assertEqual(bc._json_stream(text), [{"id": 1}, {"id": 2}])

    def public_report(self):
        report = self.report("Create ops/new.py", exists_in_base=in_base())
        report["brief"] = "brief.md"
        report["_source"] = "BUILD (all required):\n1. Create ops/new.py"
        return report

    def test_13_concurrent_runs_converge(self):
        transport = PublicationGh(self.public_report())
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: bc.post_comment("o/r", 7, self.public_report(), gh=transport), range(2)))
        self.assertEqual(transport.mutations.count("POST"), 1)
        self.assertEqual(len(transport.comments), 1)

    def test_13_uncertain_post_is_read_back_before_retry(self):
        transport = PublicationGh(self.public_report())
        transport.uncertain = True
        bc.post_comment("o/r", 7, self.public_report(), gh=transport)
        bc.post_comment("o/r", 7, self.public_report(), gh=transport)
        self.assertEqual(transport.mutations.count("POST"), 1)

    def test_14_stale_head_is_refused(self):
        transport = PublicationGh(self.public_report())
        transport.head = "b" * 40
        with self.assertRaisesRegex(bc.BriefError, "head"):
            bc.post_comment("o/r", 7, self.public_report(), gh=transport)
        self.assertEqual(transport.mutations, [])

    def test_14_links_bind_inspected_head(self):
        report = self.report("Change hooks/escalation-gate.py")
        self.assertIn(pr(1544)["headRefOid"], report["requirements"][0]["evidence"][0])

    def test_14_head_change_during_preflight_never_publishes(self):
        transport = PublicationGh(self.public_report())
        transport.move_after_read = True
        with self.assertRaises(bc.BriefError):
            bc.post_comment("o/r", 7, self.public_report(), gh=transport)
        self.assertEqual(transport.mutations, [])

    def test_06_test_source_with_unrelated_check_is_unresolved(self):
        data = pr(1544)
        data["statusCheckRollup"] = [{"name": "secret scan", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        self.assertEqual(verdicts(self.report("Add tests", data)), ["needs judgment"])

    def test_06_execution_must_cover_the_delivered_test_lane(self):
        data = pr(1544)
        data["statusCheckRollup"] = [{"name": "ops/ci.sh --strict --only unit", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        self.assertEqual(verdicts(self.report("Add tests", data)), ["needs judgment"])
        data["statusCheckRollup"][0]["name"] = "ops/ci.sh --strict --only gates"
        self.assertEqual(verdicts(self.report("Add tests", data)), ["met"])

    def test_06_uncollected_live_tests_are_not_certified(self):
        data = pr(1544)
        data["files"] = [{"path": "tools/room-bridge/test_codex_live.py", "changeType": "ADDED"}]
        self.assertEqual(verdicts(self.report("Add tests", data)), ["needs judgment"])

    def test_04_changed_check_script_is_not_execution(self):
        data = pr(1544)
        data["files"] = [{"path": "ops/ci.sh", "changeType": "MODIFIED"}]
        data["statusCheckRollup"] = []
        self.assertEqual(verdicts(self.report("Run ops/ci.sh --strict", data)), ["needs judgment"])

    def test_04_partial_check_does_not_prove_full_execution(self):
        data = pr(1544)
        data["statusCheckRollup"] = [{"name": "ops/ci.sh --strict --only secret", "status": "COMPLETED", "conclusion": "SUCCESS"}]
        self.assertEqual(verdicts(self.report("Run ops/ci.sh --strict", data)), ["needs judgment"])

    def test_09_hash_conflict_does_not_call_jev(self):
        data = pr(1544)
        data["body"] = bc.stamp_line("brief.md", "0" * 64)
        report = self.report("Explain the architecture", data)
        with patch.object(bc, "_jev") as loader:
            bc.add_jev(report)
        loader.assert_not_called()

    def test_15_other_authors_marker_is_not_updated(self):
        transport = PublicationGh(self.public_report())
        transport.comments = [{"id": 15, "body": bc.MARKER, "user": {"login": "someone-else"}}]
        bc.post_comment("o/r", 7, self.public_report(), gh=transport)
        self.assertEqual(transport.mutations, ["POST"])

    def test_16_private_untracked_brief_is_never_published(self):
        report = self.report("CONFIDENTIAL_CLIENT_SENTINEL PRIVATE_EMAIL_SENTINEL")
        report["brief"] = "private.md"
        transport = PublicationGh(self.public_report())
        with self.assertRaises(bc.BriefError):
            bc.post_comment("o/r", 7, report, gh=transport)
        self.assertEqual(transport.mutations, [])

    def test_17_timeouts_and_optional_loader_failures_are_controlled(self):
        with patch.object(bc.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 30)):
            with self.assertRaises(bc.BriefError):
                bc.run_gh(["pr", "view", "7"])
        with patch.object(bc, "_jev", side_effect=ImportError("missing optional module")):
            report = self.report("Explain the architecture")
            bc.add_jev(report)
        self.assertIn("Jev unavailable", report["requirements"][0]["jev"])
        with patch.object(bc, "run_gh", return_value=json.dumps(pr(1544))):
            with self.assertRaises(bc.BriefError):
                bc.main(["o/r", "7", "--brief", "/nonexistent/brief.md"])

    def test_18_jev_shared_policy_and_observation(self):
        report = self.report("Explain the architecture")
        observations = []
        shared = bc._jev_policy()
        with patch.object(shared, "record", side_effect=lambda *args, **kw: observations.append((args, kw))), patch.object(bc, "_jev_policy", return_value=shared):
            bc.add_jev(report, judge=lambda *a, **kw: {"answers": {"r1": {"type": "noul", "noul": 0.9}}}, noul=lambda text: text)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0][1]["family"], "brief_requirement")
        self.assertEqual(observations[0][1]["downstream_action"], "advisory_only")
        self.assertIn(report["head"], observations[0][0][1])


class PublicationGh:
    def __init__(self, report):
        self.report = report
        self.comments = []
        self.mutations = []
        self.head = report["head"]
        self.uncertain = False

    def __call__(self, args, stdin=None):
        if args[:2] == ["pr", "view"]:
            result = json.dumps({"headRefOid": self.head})
            if getattr(self, "move_after_read", False):
                self.head = "b" * 40
            return result
        if args == ["api", "user"]:
            return json.dumps({"login": "publisher"})
        if any("/contents/" in arg for arg in args):
            return json.dumps({"type": "file", "content": base64.b64encode(self.report["_source"].encode()).decode()})
        if "--paginate" in args:
            return json.dumps(self.comments)
        method = args[args.index("-X") + 1]
        self.mutations.append(method)
        if method == "POST":
            self.comments.append({"id": 9, "body": stdin, "user": {"login": "publisher"}, "html_url": "https://github.com/o/r/pull/7#issuecomment-9"})
            if self.uncertain:
                self.uncertain = False
                raise bc.BriefError("uncertain POST")
        else:
            self.comments[-1]["body"] = stdin
        return json.dumps(self.comments[-1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
