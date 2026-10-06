#!/usr/bin/env python3
"""Flash rule delivery uses the full corpus and exposes semantic advice for review.

The selector runs through its production interface with an offline binding transport.
Suggestions never become an authoritative system-prompt block.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fr = _load("flash_run", os.path.join(HERE, "flash-run.py"))
# The same module pick_rules loads, read here only to compare its roster and text.
rtd = _load("rule_trigger_delivery_flash_test", os.path.join(REPO, "ops", "rule_trigger_delivery.py"))
rtc = _load("rule_trigger_compile_flash_test", os.path.join(REPO, "ops", "rule_trigger_compile.py"))

FAILURES: list[str] = []
# Verification rules: built a check must compare against the known-good shape, and a
# changed system needs a before/after comparison. Neither is a pack-layer rule, so the
# partner-message roster (pack_rules) cannot offer them; Flash has no boot load and no
# enforcing hooks to deliver them any other way.
VERIFY_RULES = ("a9ecd5b4", "a6e6ab4e")


def check(name, fn):
    try:
        fn()
        print(f"  ok    {name}")
    except AssertionError as exc:
        FAILURES.append(name)
        print(f"  FAIL  {name}: {exc!r}")


class Paid:
    def __init__(self, noul=0.9, bind_raises=None, drop=()):
        self.noul, self.bind_raises, self.drop = noul, bind_raises, set(drop)
        self.calls = []

    def rank(self, *args, **kwargs):
        raise AssertionError("shortlisting must be deterministic")

    def ask(self, state, questions):
        self.calls.append((state, questions))
        if self.bind_raises:
            raise self.bind_raises
        return {"model": "jev-1.13.0", "answers": {
            key: {"noul": self.noul} for key in questions
            if key.removeprefix("bind_") not in self.drop}}

    def judge(self):
        return {"rank": self.rank, "ask": self.ask, "client": FakeClient(), "titles": {}}


class FakeClient:
    @staticmethod
    def noul(instructions, true="", false=""):
        return {"instructions": instructions, "criteria": {"true": true, "false": false}}


def roster_is_the_whole_active_corpus():
    corpus = rtd.load_rules()
    pack = {rule["id"] for rule in rtc.pack_rules()}
    observed = []
    def select(text, rules, always, **kwargs):
        observed.extend(rules)
        return {}, {"rank_status": "ok", "bind_status": "none"}
    with patch.object(rtd, "judge_budgeted", side_effect=select), patch.object(fr, "_lib", return_value=rtd):
        rules, note = fr.pick_rules("build a CI check for the export")
    assert note is None and rules == [], (rules, note)
    assert {rule["id"] for rule in observed} == {rule["id"] for rule in corpus}
    for rule_id in VERIFY_RULES:
        assert rule_id not in pack
        assert rule_id in {rule["id"] for rule in observed}


def semantic_advice_is_visible_but_never_delivered():
    paid = Paid()
    rules, note = fr.pick_rules("compare artifact against what it should be before and after", **paid.judge())
    assert rules == [] and fr.rules_block(rules) is None, rules
    assert note and "review required" in note, note
    assert len(paid.calls) == 1, paid.calls
    state, _ = paid.calls[0]
    assert "no git" in state["situation"] and "compare artifact" in state["situation"]
    assert "a9ecd5b4" in state["rules"], sorted(state["rules"])
    assert "a9ecd5b4" in note, note


def bind_outage_is_reported_not_an_empty_success():
    paid = Paid(bind_raises=TimeoutError("jev timed out"))
    rules, note = fr.pick_rules("build a CI check", **paid.judge())
    assert rules == [], rules
    assert note and "unavailable" in note, note
    assert len(paid.calls) == 1


def partial_bind_exposes_review_and_the_gap():
    paid = Paid(drop=["a9ecd5b4"])
    rules, note = fr.pick_rules("compare artifact against what it should be before and after", **paid.judge())
    assert rules == [], rules
    assert note and "partial" in note and "review required" in note, note


def low_scores_are_an_empty_success():
    rules, note = fr.pick_rules("build a CI check", **Paid(noul=0.1).judge())
    assert rules == [] and note is None, (rules, note)


def deadline_is_reported():
    paid = Paid()
    rules, note = fr.pick_rules("build a CI check", deadline=0.0, **paid.judge())
    assert rules == [] and not paid.calls, (rules, paid.calls)
    assert note and "deadline" in note, note


def fails_open_when_the_selector_breaks():
    rules, note = fr.pick_rules("fix a bug", titles=object(),
                                rank=Paid().rank, ask=Paid().ask,
                                client=FakeClient())
    assert rules == [], rules
    assert note and "AttributeError" in note, note


def block_is_empty_without_rules():
    assert fr.rules_block([]) is None


def block_carries_statement_and_caps_length():
    rule = {"id": "e65efc68", "gist": "WRITE THE TEST BEFORE THE THING", "statement": "x" * 5000}
    block = fr.rules_block([rule] * 9)
    assert block.count("e65efc68") == fr.MAX_TASK_RULES, block.count("e65efc68")
    assert len(block) < 700 * fr.MAX_TASK_RULES + 400, len(block)
    assert "WRITE THE TEST BEFORE THE THING" in block


with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CARR_JEV_SEMANTIC_CACHE=tmp):
    check("roster is the whole active corpus, not the pack layer", roster_is_the_whole_active_corpus)
    check("semantic advice is visible but never delivered", semantic_advice_is_visible_but_never_delivered)
    check("a binding outage is reported, not an empty success", bind_outage_is_reported_not_an_empty_success)
    check("a partial binding exposes review and the gap", partial_bind_exposes_review_and_the_gap)
    check("low scores are an empty success", low_scores_are_an_empty_success)
    check("a deadline is reported", deadline_is_reported)
    check("fails open when the selector breaks", fails_open_when_the_selector_breaks)
    check("no rules -> no block", block_is_empty_without_rules)
    check("block carries rule text and caps its size", block_carries_statement_and_caps_length)

if FAILURES:
    print(f"flash-run rules: {len(FAILURES)} FAILED")
    sys.exit(1)
print("flash-run rules: every assertion held")
