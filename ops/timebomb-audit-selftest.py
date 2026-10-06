#!/usr/bin/env python3
"""Offline tests for ops/timebomb-audit.py.

NOTHING HERE REACHES THE NETWORK. Jev is always a FakeClient injected
through the `ask=` seams ops/timebomb-audit.py exposes on
reader_sanity_check() and judge_regions() -- the same pattern
ops/jev-judge-selftest.py uses for jev_judge.py. `ops/ci.sh`'s gates class
is repository content only (no network calls, WR-000006/2026-08-23 council)
and this file honours that.

Covers, per the build's own DoD:
  - signature matching, including the git("log", "-N") wrapper shape that
    is the whole reason find_candidates.py's arg-list signature exists;
  - ledger suppression by (path, normalised-code-hash);
  - the deterministic headroom computation (git window + literal date);
  - the two failure paths that must FAIL LOUDLY rather than look clean:
    Jev unreachable, and the reader check itself failing;
  - filing (record-defect + the room queue turn) only happens on NEW
    findings -- never on a clean run, and never after a failed run.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("timebomb-audit.py")
SPEC = importlib.util.spec_from_file_location("timebomb_audit", MODULE_PATH)
assert SPEC and SPEC.loader
tba = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tba)


# ── fakes ────────────────────────────────────────────────────────────────

import os as _sem_os
import tempfile as _sem_tmp
from unittest.mock import patch as _sem_patch
class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        with _sem_tmp.TemporaryDirectory() as root, _sem_patch.dict(_sem_os.environ, CARR_JEV_SEMANTIC_CACHE=root+"/cache"):
            return super().run(result)


class FakeTsc:
    """Stands in for ops/typesafe_client.py -- just enough of the surface
    timebomb-audit.py actually calls (noul/choice/ask)."""

    def noul(self, instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}

    def choice(self, instructions, options):
        return {"type": "choice", "instructions": instructions, "options": options}


class FakeJcr:
    @staticmethod
    def answer_value(body):
        kind = body.get("type")
        if kind and kind in body:
            return body[kind]
        raise KeyError(kind)


class ScriptedAsk:
    """A callable standing in for tsc.ask(state, questions, **kwargs), keyed
    by the region's own path so different fixtures get different answers.
    `raises` makes every call blow up (the Jev-unreachable path)."""

    def __init__(self, by_path=None, raises=None):
        self.by_path = by_path or {}
        self.raises = raises
        self.calls = []

    def __call__(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        if self.raises:
            raise self.raises
        path = state["region"]["path"]
        scores = self.by_path.get(path, {"breaks_without_code_change": 0.1,
                                          "fails_silently": 0.1, "horizon": "never"})
        answers = {qid: {"type": "noul", "noul": scores[qid]} if qid != "horizon"
                   else {"type": "choice", "choice": scores[qid]}
                   for qid in questions}
        return {"model": "jev-1.13.0", "answers": answers}


def bad_good_ask():
    """An ask() whose known-bad/known-good scores satisfy reader_sanity_check."""
    return ScriptedAsk({
        "known_bad_example.py": {"breaks_without_code_change": 0.95,
                                  "fails_silently": 0.8, "horizon": "days"},
        "known_good_example.py": {"breaks_without_code_change": 0.05,
                                   "fails_silently": 0.05, "horizon": "never"},
    })


# ── signature matching ───────────────────────────────────────────────────

class SignatureTests(SemanticTestCase):
    def _regions(self, text):
        lines = text.splitlines()
        found = []
        for kind, pattern in tba.SIGNATURES:
            if kind == "zero_padded_migration":
                continue
            filt = tba.CONTEXT_FILTERS.get(kind)
            for match in pattern.finditer(text):
                if filt and not filt(tba._window_text(text, match.start()),
                                      tba._match_line(text, match.start())):
                    continue
                line_no = text[:match.start()].count("\n") + 1
                found.append((kind, line_no, match.group(0)))
        return found

    def test_git_log_wrapper_call_arg_shape_matches(self):
        # The seed example this whole build exists to catch: a wrapper
        # function call, not literal "git log -N" text.
        text = ("def find_pre_feature_commit(repo):\n"
                "    for sha in git('log', '-160', '--format=%H').split():\n"
                "        pass\n")
        kinds = {k for k, _line, _m in self._regions(text)}
        self.assertIn("git_log_call_arg", kinds)

    def test_literal_git_log_dash_n_matches(self):
        text = "result = subprocess.run(['git', 'log', '-40'])\n"
        # bracketed list form also satisfies the call-arg signature
        kinds = {k for k, _l, _m in self._regions(text)}
        self.assertIn("git_log_call_arg", kinds)

    def test_comment_only_date_is_dropped(self):
        text = ("# decided on 2026-08-09, see loop #532 for context\n"
                "def handler():\n"
                "    return True\n")
        kinds = {k for k, _l, _m in self._regions(text)}
        self.assertNotIn("hardcoded_date_literal", kinds)

    def test_date_compared_against_now_is_kept(self):
        text = ("EXPIRES = \"2026-10-01\"\n"
                "if today() >= EXPIRES:\n"
                "    raise RuntimeError('expired')\n")
        kinds = {k for k, _l, _m in self._regions(text)}
        self.assertIn("hardcoded_date_literal", kinds)

    def test_sql_limit_without_order_by_is_kept_with_order_by_dropped(self):
        unordered = "SELECT * FROM loop_item LIMIT 50;\n"
        ordered = "SELECT * FROM loop_item ORDER BY created_at LIMIT 50;\n"
        self.assertIn("sql_limit_no_order", {k for k, _l, _m in self._regions(unordered)})
        self.assertNotIn("sql_limit_no_order", {k for k, _l, _m in self._regions(ordered)})


# ── ledger suppression ───────────────────────────────────────────────────

class LedgerTests(SemanticTestCase):
    def test_normalize_code_ignores_trailing_whitespace_and_blank_lines(self):
        a = "line one\nline two   \n\n"
        b = "\nline one\nline two\n"
        self.assertEqual(tba.normalize_code(a), tba.normalize_code(b))

    def test_same_code_same_hash_different_code_different_hash(self):
        h1 = tba.region_hash("git('log', '-160')")
        h2 = tba.region_hash("git('log', '-160')")
        h3 = tba.region_hash("git('log', '-5')")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_ledger_suppresses_matching_entry_and_lets_changed_code_through(self):
        code = "git('log', '-160', '--format=%H')"
        region = {"path": "tools/x.py", "code": code}
        region["_ledger_key"] = tba.ledger_key(region["path"], region["code"])
        ledger = {"schema": "timebomb-triage/v1", "entries": {
            region["_ledger_key"]: {"verdict": "false_positive", "reason": "test seed"},
        }}
        self.assertTrue(tba.suppressed_by_ledger(region, ledger))

        changed = {"path": "tools/x.py", "code": "git('log', '-5', '--format=%H')"}
        changed["_ledger_key"] = tba.ledger_key(changed["path"], changed["code"])
        self.assertFalse(tba.suppressed_by_ledger(changed, ledger))

    def test_load_ledger_missing_file_is_empty_not_an_error(self):
        ledger = tba.load_ledger(Path("/nonexistent/does-not-exist.json"))
        self.assertEqual(ledger["entries"], {})

    def test_seeded_ledger_file_is_valid_and_matches_hash_scheme(self):
        seeded = tba.load_ledger(tba.REPO / "ops" / "config" / "timebomb-triage.v1.json")
        self.assertEqual(seeded.get("schema"), "timebomb-triage/v1")
        self.assertGreaterEqual(len(seeded["entries"]), 10)
        for key, entry in seeded["entries"].items():
            self.assertEqual(key, f"{entry['path']}#{entry['hash']}")
            self.assertIn(entry["verdict"], ("false_positive", "fixed"))
            self.assertTrue(entry.get("reason"))

    def test_migrations_are_excluded_structurally_not_only_by_ledger(self):
        sources = tba.tracked_sources(tba.REPO)
        self.assertFalse(any(s.startswith("migrations/") for s in sources))


# ── deterministic headroom ───────────────────────────────────────────────

class HeadroomTests(SemanticTestCase):
    TODAY = date(2026, 9, 24)
    FAST_VELOCITY = (5000, 20.0)  # 20 commits/day

    def test_tight_git_window_is_a_deterministic_finding(self):
        region = {"kind": "git_log_call_arg", "code": "irrelevant",
                  "match_texts": {"git_log_call_arg": "\"log\", \"-40\""}}
        headroom = tba.compute_headroom(region, self.FAST_VELOCITY, self.TODAY)
        self.assertIsNotNone(headroom)
        self.assertTrue(headroom["deterministic_finding"])
        self.assertEqual(headroom["window_commits"], 40)

    def test_get_latest_commit_idiom_is_not_a_finding(self):
        region = {"kind": "git_log_call_arg", "code": "irrelevant",
                  "match_texts": {"git_log_call_arg": "\"log\", \"-1\""}}
        headroom = tba.compute_headroom(region, self.FAST_VELOCITY, self.TODAY)
        self.assertIsNone(headroom)

    def test_fetch_depth_is_excluded_from_headroom_entirely(self):
        region = {"kind": "git_depth", "code": "irrelevant",
                  "match_texts": {"git_depth": "fetch-depth: 1"}}
        headroom = tba.compute_headroom(region, self.FAST_VELOCITY, self.TODAY)
        self.assertIsNone(headroom)

    def test_upcoming_date_within_threshold_is_a_finding(self):
        region = {"kind": "hardcoded_date_literal", "code": "irrelevant",
                  "match_texts": {"hardcoded_date_literal": "2026-10-01"}}
        headroom = tba.compute_headroom(region, None, self.TODAY)
        self.assertIsNotNone(headroom)
        self.assertTrue(headroom["deterministic_finding"])
        self.assertEqual(headroom["headroom_days"], 7)

    def test_past_narrative_date_without_max_age_is_not_a_finding(self):
        region = {"kind": "hardcoded_date_literal", "code": "decided on 2026-08-09",
                  "match_texts": {"hardcoded_date_literal": "2026-08-09"}}
        headroom = tba.compute_headroom(region, None, self.TODAY)
        self.assertIsNotNone(headroom)
        self.assertFalse(headroom["deterministic_finding"])

    def test_past_date_with_explicit_max_age_is_a_finding(self):
        region = {"kind": "hardcoded_date_literal",
                  "code": "issued 2026-08-09, max_age_days=14",
                  "match_texts": {"hardcoded_date_literal": "2026-08-09"}}
        headroom = tba.compute_headroom(region, None, self.TODAY)
        self.assertIsNotNone(headroom)
        self.assertTrue(headroom["deterministic_finding"])
        self.assertEqual(headroom["max_age_days"], 14)

    def test_far_future_date_is_not_a_finding(self):
        region = {"kind": "hardcoded_date_literal", "code": "irrelevant",
                  "match_texts": {"hardcoded_date_literal": "2030-01-01"}}
        headroom = tba.compute_headroom(region, None, self.TODAY)
        self.assertFalse(headroom["deterministic_finding"])

    def test_no_git_velocity_available_returns_no_git_headroom(self):
        region = {"kind": "git_log_window", "code": "irrelevant",
                  "match_texts": {"git_log_window": "git log -40"}}
        headroom = tba.compute_headroom(region, None, self.TODAY)
        self.assertIsNone(headroom)


# ── reader sanity check ──────────────────────────────────────────────────

class ReaderCheckTests(SemanticTestCase):
    def test_passes_when_bad_scores_high_and_good_scores_low(self):
        ok, detail = tba.reader_sanity_check(FakeJcr(), FakeTsc(), ask=bad_good_ask())
        self.assertTrue(ok, detail)

    def test_fails_when_reader_returns_identical_scores(self):
        reader = FakeJcr()
        reader.answer_value = lambda *args, **kwargs: 0.0
        ok, detail = tba.reader_sanity_check(reader, FakeTsc())
        self.assertFalse(ok)
        self.assertIn("IDENTICALLY", detail)

    def test_reader_sanity_never_calls_a_model(self):
        unreachable = mock.Mock(side_effect=AssertionError("no paid sanity check"))
        ok, detail = tba.reader_sanity_check(FakeJcr(), FakeTsc(), ask=unreachable)
        self.assertTrue(ok, detail)
        unreachable.assert_not_called()


# ── judging + the zero-judged failure path ───────────────────────────────

class JudgeRegionsTests(SemanticTestCase):
    def test_all_regions_erroring_reports_zero_judged(self):
        regions = [{"path": "a.py", "line": 1, "kind": "git_log_window", "code": "x"}]
        judged, errors = tba.judge_regions(regions, FakeJcr(), FakeTsc(),
                                            ask=ScriptedAsk(raises=RuntimeError("boom")))
        self.assertEqual(errors, 1)
        self.assertEqual(len(judged) - errors, 0)

    def test_successful_judging_reads_scores_via_answer_value(self):
        regions = [{"path": "a.py", "line": 1, "kind": "git_log_window", "code": "x"}]
        ask = ScriptedAsk({"a.py": {"breaks_without_code_change": 0.9,
                                     "fails_silently": 0.7, "horizon": "days"}})
        judged, errors = tba.judge_regions(regions, FakeJcr(), FakeTsc(), ask=ask)
        self.assertEqual(errors, 0)
        self.assertEqual(judged[0]["scores"]["breaks_without_code_change"], 0.9)


# ── end-to-end run(): clean vs. new-findings vs. failure, filing gated on each ──

class RunTests(SemanticTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = Path(self.tmp.name) / "out"
        self.triage_path = Path(self.tmp.name) / "triage.json"
        self.triage_path.write_text(json.dumps({"schema": "timebomb-triage/v1", "entries": {}}))
        self._patches = [
            mock.patch.object(tba, "OUT_DIR", self.out_dir),
            mock.patch.object(tba, "TRIAGE_PATH", self.triage_path),
            mock.patch.object(tba, "_commit_velocity", return_value=(1000, 10.0)),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _fixed_scan(self, regions):
        return mock.patch.object(tba, "scan_repo", return_value=regions)

    def test_clean_run_files_nothing(self):
        with self._fixed_scan([]), \
             mock.patch.object(tba, "load_jev_modules", return_value=(FakeJcr(), FakeTsc())), \
             mock.patch.object(tba, "reader_sanity_check", return_value=(True, "ok")), \
             mock.patch.object(tba, "call_verb") as call_verb:
            rc = tba.run(now=datetime(2026, 9, 24, tzinfo=timezone.utc))
        self.assertEqual(rc, 0)
        call_verb.assert_not_called()

    def test_new_finding_files_defect_and_queue_turn_exactly_once(self):
        region = {"path": "tools/x.py", "line": 1, "kind": "git_log_call_arg",
                  "code": "git('log', '-160')",
                  "match_texts": {"git_log_call_arg": "\"log\", \"-160\""}}
        ask = ScriptedAsk({"tools/x.py": {"breaks_without_code_change": 0.9,
                                           "fails_silently": 0.8, "horizon": "days"}})

        real_judge_regions = tba.judge_regions
        calls = []

        def fake_call_verb(verb, args):
            calls.append(verb)
            if verb == "record-defect":
                return True, {"ok": True, "defect_id": 42}
            return True, {"ok": True}

        with self._fixed_scan([region]), \
             mock.patch.object(tba, "load_jev_modules", return_value=(FakeJcr(), FakeTsc())), \
             mock.patch.object(tba, "reader_sanity_check", return_value=(True, "ok")), \
             mock.patch.object(tba, "judge_regions",
                                side_effect=lambda regions, jcr, tsc: real_judge_regions(
                                    regions, jcr, tsc, ask=ask)), \
             mock.patch.object(tba, "call_verb", side_effect=fake_call_verb):
            rc = tba.run(now=datetime(2026, 9, 24, tzinfo=timezone.utc))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["record-defect", "add-room-turn"])
        self.assertEqual(ask.calls, [], "exact headroom resolves before Jev")

    def test_dry_run_never_files_even_with_new_findings(self):
        region = {"path": "tools/x.py", "line": 1, "kind": "git_log_call_arg",
                  "code": "git('log', '-160')",
                  "match_texts": {"git_log_call_arg": "\"log\", \"-160\""}}
        ask = ScriptedAsk({"tools/x.py": {"breaks_without_code_change": 0.9,
                                           "fails_silently": 0.8, "horizon": "days"}})
        real_judge_regions = tba.judge_regions
        with self._fixed_scan([region]), \
             mock.patch.object(tba, "load_jev_modules", return_value=(FakeJcr(), FakeTsc())), \
             mock.patch.object(tba, "reader_sanity_check", return_value=(True, "ok")), \
             mock.patch.object(tba, "judge_regions",
                                side_effect=lambda regions, jcr, tsc: real_judge_regions(
                                    regions, jcr, tsc, ask=ask)), \
             mock.patch.object(tba, "call_verb") as call_verb:
            rc = tba.run(dry_run=True, now=datetime(2026, 9, 24, tzinfo=timezone.utc))
        self.assertEqual(rc, 0)
        call_verb.assert_not_called()

    def test_failed_reader_check_fails_loudly_and_files_nothing(self):
        with self._fixed_scan([{"path": "a.py", "line": 1, "kind": "git_log_window", "code": "x"}]), \
             mock.patch.object(tba, "load_jev_modules", return_value=(FakeJcr(), FakeTsc())), \
             mock.patch.object(tba, "reader_sanity_check", return_value=(False, "reader is broken")), \
             mock.patch.object(tba, "call_verb") as call_verb:
            rc = tba.run(now=datetime(2026, 9, 24, tzinfo=timezone.utc))
        self.assertEqual(rc, 1)
        call_verb.assert_not_called()

    def test_jev_unreachable_at_module_load_fails_loudly_and_files_nothing(self):
        with self._fixed_scan([{"path": "a.py", "line": 1, "kind": "git_log_window", "code": "x"}]), \
             mock.patch.object(tba, "load_jev_modules", side_effect=RuntimeError("no network")), \
             mock.patch.object(tba, "call_verb") as call_verb:
            rc = tba.run(now=datetime(2026, 9, 24, tzinfo=timezone.utc))
        self.assertEqual(rc, 1)
        call_verb.assert_not_called()

    def test_zero_regions_judged_despite_candidates_fails_loudly(self):
        region = {"path": "tools/x.py", "line": 1, "kind": "append_only_log",
                  "code": "append(log)",
                  "match_texts": {"git_log_call_arg": "\"log\", \"-160\""}}
        def all_error(regions, jcr, tsc):
            return [dict(r, scores={"_error": "boom"}) for r in regions], len(regions)

        with self._fixed_scan([region]), \
             mock.patch.object(tba, "load_jev_modules", return_value=(FakeJcr(), FakeTsc())), \
             mock.patch.object(tba, "reader_sanity_check", return_value=(True, "ok")), \
             mock.patch.object(tba, "judge_regions", side_effect=all_error), \
             mock.patch.object(tba, "call_verb") as call_verb:
            rc = tba.run(now=datetime(2026, 9, 24, tzinfo=timezone.utc))
        self.assertEqual(rc, 1)
        call_verb.assert_not_called()

    def test_semantic_high_score_is_review_only(self):
        region = {"path":"tools/semantic.py", "line":1, "kind":"append_only_log",
                  "code":"append(log)"}
        real_judge = tba.judge_regions
        ask = ScriptedAsk({"tools/semantic.py":{"breaks_without_code_change":.99,
                                                    "fails_silently":.99}})
        with self._fixed_scan([region]), \
             mock.patch.object(tba, "load_jev_modules", return_value=(FakeJcr(),FakeTsc())), \
             mock.patch.object(tba, "judge_regions", side_effect=lambda r,j,t: real_judge(r,j,t,ask=ask)), \
             mock.patch.object(tba, "call_verb") as verb:
            self.assertEqual(tba.run(now=datetime(2026,9,24,tzinfo=timezone.utc)), 0)
        verb.assert_not_called()
        report = json.loads((self.out_dir/"2026-09-24.json").read_text())
        self.assertTrue(report["review_required"])
        self.assertEqual(report["regions"], [])
        self.assertEqual(len(report["semantic_advice"]), 1)
        self.assertNotIn("horizon", ask.calls[0][1], "numeric runway belongs in code")

    def test_queue_turn_body_carries_required_grammar_and_report_ref(self):
        payload = tba.queue_enqueue_turn("2026-09-29", "out/timebomb-audit/2026-09-29.json", "defect #7")
        first_line = payload["body"].splitlines()[0]
        self.assertTrue(first_line.startswith(
            "@queue enqueue target=claude-desktop cap=repo-write priority=P1 runtime=3h "
            "key=timebomb-2026-09-29 :: "))
        self.assertIn("out/timebomb-audit/2026-09-29.json", payload["body"])
        self.assertIn("defect #7", payload["body"])
        self.assertEqual(payload["room"], "model-room")
        self.assertEqual(payload["seat"], "claude")
        # msg_id is deterministic per run-date key, for transport-level dedup
        # on top of the @queue grammar's own key= dedup.
        again = tba.queue_enqueue_turn("2026-09-29", "a different report path", "a different ref")
        self.assertEqual(payload["msg_id"], again["msg_id"])


if __name__ == "__main__":
    unittest.main()
