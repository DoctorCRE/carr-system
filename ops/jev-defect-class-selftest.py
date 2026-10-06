"""Offline suite for ops/jev_defect_class.py. No credential, no network, no spend.

THIS SUITE USED TO ASSERT THE BUG. Its first version had a class called
OneRequestPerClassTests, checking that the module sent one request per class and
that no request saw another candidate. That was the reranking rule applied to a
roster it does not govern, and the tests locked it in — a suite can make a
mistake permanent as easily as it can catch one. The shape is now a single
Choice over the roster, measured at 69% top-1 and 88% top-8 in one request
against 38% and 81% in 320, and the cases below guard the new shape.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_defect_class.py"
SPEC = importlib.util.spec_from_file_location("jev_defect_class", MODULE_PATH)
assert SPEC and SPEC.loader
sel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sel)


CORPUS = [
    {"name": "dated-artifact-read-as-present-state", "occurrences": 29,
     "claimed": "Reported the August plan as the current one.",
     "actual": "It was superseded in September by a later revision nobody read."},
    {"name": "silent_failure", "occurrences": 2,
     "claimed": "Said the job completed.",
     "actual": "It exited non-zero and nobody looked at the exit code."},
    {"name": "verb-refuses-its-own-required-path", "occurrences": 2,
     "claimed": "Said the verb accepted the field.",
     "actual": "It refused the call and named no field in the error."},
]

PROPOSED = {"claimed": "Told the partner the dashboard was live.",
            "actual": "The dashboard had never been opened by a human."}


import os as _sem_os
import tempfile as _sem_tmp
from unittest.mock import patch as _sem_patch
class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        with _sem_tmp.TemporaryDirectory() as root, _sem_patch.dict(_sem_os.environ, CARR_JEV_SEMANTIC_CACHE=root+"/cache"):
            return super().run(result)


class _FakeJudge:
    """Reproduces the REAL response shape: answers nested under "answers", and
    a Choice answer carrying a probabilities mapping over its options.

    A fake that flattens either nesting hides a defect rather than catching one,
    which is how an earlier module shipped reading one level too shallow.
    """

    def __init__(self, probabilities, fail=False):
        self.probabilities = probabilities
        self.fail = fail
        self.calls = []

    def judge(self, state, questions, **kwargs):
        self.calls.append((state, questions))
        if self.fail:
            raise RuntimeError("service did not answer")
        return {"model":"jev-1.13.0", "answers": {"pick": {"type": "choice",
            "choice":max(questions["pick"]["criteria"],key=lambda k:self.probabilities.get(k,0)),
            "probabilities": dict(self.probabilities)}}}


class _FakeClient:
    @staticmethod
    def choice(instructions, options):
        if not isinstance(options, dict) or len(options) < 2:
            raise ValueError("a choice needs at least two options")
        return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def run(probabilities, corpus=None, **kwargs):
    fake = _FakeJudge(probabilities)
    got = sel.shortlist(PROPOSED, corpus if corpus is not None else CORPUS,
                        judge=fake, client=_FakeClient(), **kwargs)
    return got, fake


class OneRequestTests(SemanticTestCase):
    """The classes compete for one slot, so they belong in one Choice."""

    def test_the_whole_roster_goes_out_in_a_single_request(self):
        _, fake = run({"silent_failure": 0.9})
        self.assertEqual(len(fake.calls), 1, "320 requests became one; do not go back")

    def test_every_class_becomes_an_option(self):
        _, fake = run({"silent_failure": 0.9})
        options = fake.calls[0][1]["pick"]["criteria"]
        for existing in CORPUS:
            self.assertIn(existing["name"], options)

    def test_the_proposed_defect_is_the_state_not_an_option(self):
        _, fake = run({"silent_failure": 0.9})
        self.assertEqual(fake.calls[0][0]["proposed_defect"]["actual"],
                         PROPOSED["actual"])


class CapAndTrimTests(SemanticTestCase):
    """A Choice carries 255 options and the ledger holds 320 classes."""

    def test_a_roster_over_the_cap_is_trimmed_not_split(self):
        """Probabilities sum to one WITHIN a request, so two Choices produce
        numbers that cannot be compared. Pooling them scored 75% top-8 against
        88% for one clean request."""
        many = [{"name": f"class-{i}", "occurrences": 1,
                 "claimed": f"claimed {i}", "actual": f"actual {i}"}
                for i in range(400)]
        _, fake = run({"class-1": 0.9}, corpus=many)
        self.assertEqual(len(fake.calls), 1, "never split into two Choices")
        options = fake.calls[0][1]["pick"]["criteria"]
        self.assertLessEqual(len(options), sel.MAX_OPTIONS + 1)

    def test_the_trim_keeps_the_classes_that_share_words(self):
        """The trim is the cheap search stage, not the ranking. It must not
        drop the obvious candidate."""
        target = {"name": "dashboard-reported-live-before-a-human-opened-it",
                  "occurrences": 1, "claimed": "Told the partner the dashboard was live.",
                  "actual": "The dashboard had never been opened by a human."}
        noise = [{"name": f"unrelated-{i}", "occurrences": 1,
                  "claimed": "zzz qqq", "actual": "xxx yyy"} for i in range(400)]
        kept = sel.narrow(PROPOSED, noise + [target])
        self.assertIn(target["name"], [c["name"] for c in kept])

    def test_a_roster_under_the_cap_is_untouched(self):
        self.assertEqual(sel.narrow(PROPOSED, CORPUS), CORPUS)

    def test_rubrics_are_truncated_for_the_ranking_pass(self):
        """254 options carrying a full anchor each returns HTTP 400
        max_tokens_exceeded, which is how truncation stopped being optional."""
        wordy = [{"name": "wordy", "occurrences": 1, "claimed": "c" * 900,
                  "actual": "a" * 900}]
        _, fake = run({"wordy": 0.9}, corpus=wordy)
        rubric = fake.calls[0][1]["pick"]["criteria"]["wordy"]
        self.assertLess(len(rubric), sel.RUBRIC_CHARS + 60)

    def test_the_rubric_is_neither_the_bare_name_nor_three_examples(self):
        """Measured: name alone ranked the true class 99th, name with one
        anchor 1st, name with three illustrations 11th."""
        rubric = sel.rubric(CORPUS[0])
        self.assertIn("dated artifact read as present state", rubric)
        self.assertIn("superseded in September", rubric)


class NewKindOfMistakeTests(SemanticTestCase):
    """A Choice must return something unless it is given a way to decline, and
    a genuinely new mistake is exactly what deserves a new class name."""

    def test_the_none_option_is_always_offered(self):
        _, fake = run({"silent_failure": 0.9})
        self.assertIn(sel.NONE_OF_THESE, fake.calls[0][1]["pick"]["criteria"])

    def test_the_none_rubric_names_shared_subject_matter_as_the_trap(self):
        _, fake = run({"silent_failure": 0.9})
        rubric = fake.calls[0][1]["pick"]["criteria"][sel.NONE_OF_THESE]
        self.assertIn("SUBJECT MATTER", rubric)
        self.assertIn("mechanism", rubric)

    def test_the_none_probability_comes_back_to_the_caller(self):
        (candidates, declined), _ = run({"silent_failure": 0.3,
                                         sel.NONE_OF_THESE: 0.7})
        self.assertEqual(declined, 0.7)
        self.assertTrue(candidates, "the shortlist is still worth reading")

    def test_the_advice_shows_the_new_mistake_probability(self):
        fake = _FakeJudge({"silent_failure": 0.3, sel.NONE_OF_THESE: 0.7})
        note = sel.advise(PROPOSED, classes=CORPUS, judge=fake, client=_FakeClient())
        self.assertIn("new kind of", note)
        self.assertIn("0.70", note)


class RankingTests(SemanticTestCase):
    def test_best_first(self):
        (got, _), _ = run({"dated-artifact-read-as-present-state": 0.2,
                           "silent_failure": 0.6,
                           "verb-refuses-its-own-required-path": 0.15})
        self.assertEqual([name for name, _, _ in got],
                         ["silent_failure", "dated-artifact-read-as-present-state",
                          "verb-refuses-its-own-required-path"])

    def test_the_limit_caps_what_a_reader_is_shown(self):
        (got, _), _ = run({"dated-artifact-read-as-present-state": 0.4,
                           "silent_failure": 0.4,
                           "verb-refuses-its-own-required-path": 0.2}, limit=2)
        self.assertEqual(len(got), 2)

    def test_ties_break_deterministically(self):
        values = {"dated-artifact-read-as-present-state": 0.3,
                  "silent_failure": 0.3, "verb-refuses-its-own-required-path": 0.3}
        self.assertEqual(run(values)[0], run(values)[0],
                         "a shortlist that reshuffles is not readable")

    def test_the_occurrence_count_survives_the_ranking(self):
        """A class seen 29 times and one seen twice are different invitations,
        and the count is the only thing that says which is which."""
        (got, _), _ = run({"dated-artifact-read-as-present-state": 0.9})
        self.assertEqual(got[0][2], 29)

    def test_an_option_the_roster_does_not_hold_is_ignored(self):
        (got, _), _ = run({"invented-class": 0.9, "silent_failure": 0.05})
        self.assertEqual([name for name, _, _ in got], ["silent_failure"])


class SurvivesFailureTests(SemanticTestCase):
    def test_a_failed_request_yields_no_shortlist(self):
        got = sel.shortlist(PROPOSED, CORPUS, judge=_FakeJudge({}, fail=True),
                            client=_FakeClient())
        self.assertEqual(got, ([], None))

    def test_an_answer_with_no_probabilities_yields_no_shortlist(self):
        self.assertEqual(run({})[0], ([], None))

    def test_an_empty_corpus_yields_no_shortlist(self):
        self.assertEqual(sel.shortlist(PROPOSED, [], judge=_FakeJudge({}),
                                       client=_FakeClient()), ([], None))

    def test_a_store_that_cannot_be_reached_yields_no_corpus(self):
        def boom(sql):
            raise OSError("no route to host")
        self.assertEqual(sel.load_classes(runner=boom), [],
                         "a caller must carry on, not stop")

    def test_junk_lines_in_the_corpus_are_skipped(self):
        def runner(sql):
            return ('psql: NOTICE something\n'
                    '{"name": "a", "occurrences": 1, "claimed": "c", "actual": "x"}\n'
                    'not json at all\n{broken\n')
        self.assertEqual([c["name"] for c in sel.load_classes(runner=runner)], ["a"])

    def test_a_class_row_missing_its_text_does_not_raise(self):
        fake = _FakeJudge({"bare": 0.9})
        got = sel.shortlist(PROPOSED, [{"name": "bare"}], judge=fake,
                            client=_FakeClient())
        self.assertEqual([name for name, _, _ in got[0]], ["bare"])

    def test_the_advice_is_none_when_there_is_nothing_to_say(self):
        self.assertIsNone(sel.advise(PROPOSED, classes=[], judge=_FakeJudge({}),
                                     client=_FakeClient()))


class NeverPicksTests(SemanticTestCase):
    """69% top-1 is a good reading list and a bad autopilot."""

    def test_the_advice_says_it_is_not_a_decision(self):
        fake = _FakeJudge({"dated-artifact-read-as-present-state": 0.9})
        note = sel.advise(PROPOSED, classes=CORPUS, judge=fake, client=_FakeClient())
        self.assertIn("None of these is a decision", note)
        self.assertIn("choose", note)

    def test_the_advice_names_the_count_because_the_count_is_the_point(self):
        fake = _FakeJudge({"dated-artifact-read-as-present-state": 0.6,
                           "silent_failure": 0.3})
        note = sel.advise(PROPOSED, classes=CORPUS, judge=fake, client=_FakeClient())
        self.assertIn("seen 29 times", note)

    def test_the_library_files_nothing(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        body = source.split('"""', 2)[2]
        for forbidden in ("record-defect", "record_defect", "call-verb", "run.sh"):
            self.assertNotIn(forbidden, body,
                             f"{forbidden} outside the docstring would make this act")


class ShapeTests(SemanticTestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        self.assertIsNone(
            _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:").search(source),
            "no main guard")

    def test_the_corpus_query_takes_exactly_one_anchor_per_class(self):
        self.assertIn("limit 1", sel.CORPUS_SQL)

    def test_the_corpus_query_is_read_only(self):
        for forbidden in ("insert", "update ", "delete", "drop", "alter"):
            self.assertNotIn(forbidden, sel.CORPUS_SQL.lower())


if __name__ == "__main__":
    unittest.main(verbosity=1)
