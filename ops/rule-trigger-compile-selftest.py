#!/usr/bin/env python3
"""Offline deterministic trigger regressions and semantic advisory contracts."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")


def load(name, path, *, replace=None):
    """Load a module from `path`, optionally with one source substitution."""
    source = Path(path).read_text(encoding="utf-8")
    if replace:
        old, new = replace
        if old not in source:
            raise AssertionError(f"mutant anchor not found in {path}: {old!r}")
        source = source.replace(old, new, 1)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


RTC_PATH = REPO / "ops" / "rule_trigger_compile.py"
RTD_PATH = REPO / "ops" / "rule_trigger_delivery.py"
TSC_PATH = REPO / "ops" / "typesafe_client.py"
rtc = load("rule_trigger_compile_t", RTC_PATH)
rtd = load("rule_trigger_delivery_t", RTD_PATH)


class Client:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


RULES = [
    {"id": "aaaa0001", "gist": "git push discipline", "packs": ["engineering-git"],
     "statement": "Before you git push, fetch origin and compare against origin/main."},
    {"id": "aaaa0002", "gist": "vendor intro", "packs": ["vendor-intros"],
     "statement": "When introducing a vendor, check who-do-we-know first."},
    {"id": "aaaa0003", "gist": "judgment only", "packs": ["governance-rules"],
     "statement": "Say plainly when you are not sure."},
]


def entry(rule, mode="triggered", keywords=None, negatives=None, model="jev-test"):
    return {"id": rule["id"], "packs": rule["packs"],
            "statement_sha256": rtc.sha256_text(rule["statement"]), "mode": mode,
            "triggers": {"keywords": keywords or {}, "verbs": {}, "commands": {},
                         "paths": {}, "tools": {}},
            "negatives": negatives or {}, "always_on_probability": 0.1,
            "no_cue_probability": 0.1, "model": model}


def compiled_doc():
    return rtc.document([
        entry(RULES[0], keywords={"git push": 0.8, "push": 0.6},
              negatives={"push notification": 0.7}),
        entry(RULES[1], keywords={"vendor": 0.7}),
        entry(RULES[2], mode="residual"),
    ] + filler_entries())


def filler_entries():
    """Fillers are compiled (so they are neither stale nor missing) and match
    only a word no test prompt contains."""
    return [entry(rule, keywords={f"fillerword{i}": 0.6}) for i, rule in enumerate(FILLERS)]


def table_for(doc, tmp, extra=()):
    rows = rtc.trigger_rows(doc) + list(extra)
    path = os.path.join(tmp, "triggers.json")
    Path(path).write_text(json.dumps({"schema": "rule-jit-triggers/v1", "triggers": rows}),
                          encoding="utf-8")
    return path


# Fillers make the roster bigger than the binding capacity, so the ranking
# call decides what is judged and "always judged" is a real claim rather than
# an accident of a small roster.
FILLERS = [{"id": f"ffff{i:04d}", "gist": f"filler {i}", "packs": ["filler-pack"],
            "statement": f"Filler rule number {i} about an unrelated topic."}
           for i in range(40)]
FILLERS[0]["packs"] = ["governance-rules"]  # shares the residual rule's pack
FILLERS[1]["packs"] = ["vendor-intros"]  # shares the stale-test rule's pack
ROSTER = RULES + FILLERS
NOTIFICATION = ("<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n"
                "<summary>Agent \"x\" finished with an error, see https://example.com/log"
                "</summary>\n</task-notification>")


class Asker:
    """A single-rule binding request: records the rule each request was about."""

    def __init__(self, value=0.1, fail=False):
        self.calls = []
        self.value = value
        self.fail = fail

    def __call__(self, subject, questions, *, rule_id=None, **kwargs):
        self.calls.extend(subject["rules"])
        self.subjects = getattr(self, "subjects", []) + [subject]
        if self.fail:
            raise RuntimeError("synthetic outage")
        return {"model": "jev-1.13.0",
                "answers": {q: {"type": "noul", "noul": self.value} for q in questions}}

    def asked(self):
        return set(self.calls)


def run(module, text, tmp, *, session="s1", now=1000.0, doc=None, ask=None, rank=None,
        table=None, caches=None, rules=None, envelope=None, compiled="default"):
    doc = compiled_doc() if doc is None else doc
    delivered = caches or os.path.join(tmp, "d.json")
    return module.advise(
        text, session_id=session, now=now,
        triggers_path=table or table_for(doc, tmp),
        compiled=doc if compiled == "default" else compiled,
        rules=ROSTER if rules is None else rules,
        ask=ask if ask is not None else Asker(), client=Client,
        rank=rank,
        delivered_cache=delivered, envelope=envelope,
        log_path=os.path.join(tmp, "log.jsonl"))


def ids(rows):
    return [row["id"] for row in rows]



ENVELOPE_WITH_WORDS = "<task-notification>\n<task-id>a2</task-id>\n<status>completed</status>\n<summary>Agent x finished: git push to the vendor branch</summary>\n</task-notification>"
HUMAN_WITH_WORDS = "Agent x finished: git push to the vendor branch"
def prop_negative_masks(rtc_m, rtd_m):
    with tempfile.TemporaryDirectory() as tmp:
        masked = run(rtd_m, "send a push notification to the phone", tmp)
    with tempfile.TemporaryDirectory() as tmp:
        plain = run(rtd_m, "then git push the branch", tmp)
    return "aaaa0001" not in ids(masked) and "aaaa0001" in ids(plain)

def prop_dedupe(rtc_m, rtd_m):
    with tempfile.TemporaryDirectory() as tmp:
        first = run(rtd_m, "a vendor intro", tmp)
        second = run(rtd_m, "a vendor intro", tmp, now=1100.0)
        later = run(rtd_m, "a vendor intro", tmp, now=1000.0 + rtd_m.DEDUPE_TTL_SECONDS + 1)
    return ids(first) == ["aaaa0002"] and second == [] and ids(later) == ["aaaa0002"]

def prop_no_session_no_pooling(rtc_m, rtd_m):
    """No session id: nothing to dedupe against, so every message delivers
    afresh and no shared default key is ever written."""
    with tempfile.TemporaryDirectory() as tmp:
        first = run(rtd_m, "a vendor intro", tmp, session=None)
        again = run(rtd_m, "a vendor intro", tmp, session="  ", now=1010.0)
        wrote = os.path.exists(os.path.join(tmp, "d.json"))
    return ids(first) == ["aaaa0002"] and ids(again) == ["aaaa0002"] and not wrote

def prop_coverage_flags_empty_trigger(rtc_m, rtd_m):
    bad = rtc_m.document([entry(RULES[0]), entry(RULES[1], keywords={"vendor": 0.7}),
                          entry(RULES[2], mode="residual")])
    return any("triggered with no trigger" in p for p in rtc_m.coverage_problems(bad, RULES))

def prop_stale_detected(rtc_m, rtd_m):
    changed = [dict(RULES[0], statement="re-taught text"), RULES[1], RULES[2]]
    return rtc_m.stale_or_missing(compiled_doc(), changed) == ["aaaa0001"]

def prop_surface_floor(rtc_m, rtd_m):
    index = {"c00": ("candidate", "keyword", "push", "statement"),
             "c01": ("candidate", "keyword", "weather", "statement")}
    answer = {"model": "m", "answers": {"c00": {"noul": 0.5}, "c01": {"noul": 0.49},
                                       "always_on": {"noul": 0.1}, "no_cue": {"noul": 0.1}}}
    got = rtc_m.interpret(RULES[0], answer, index, model="m")
    return got["triggers"]["keywords"] == {"push": 0.5} and got["mode"] == "triggered"

def prop_envelope_no_compiled_rules(rtc_m, rtd_m):
    """A machine envelope (classified by the real ops/machine_envelope.py, not
    by the envelope= override) gets ZERO compiled-trigger rules; a human
    prompt carrying the same words still gets them."""
    with tempfile.TemporaryDirectory() as tmp:
        on_envelope = run(rtd_m, ENVELOPE_WITH_WORDS, tmp)
    with tempfile.TemporaryDirectory() as tmp:
        on_human = run(rtd_m, HUMAN_WITH_WORDS, tmp)
    compiled = [r for r in on_envelope if r["source"] == "compiled_trigger"]
    return (compiled == [] and ids(on_envelope) == []
            and {"aaaa0001", "aaaa0002"} <= {r["id"] for r in on_human
                                            if r["source"] == "compiled_trigger"})

def prop_prompt_floor(rtc_m, rtd_m):
    """Compiled cues retain the inclusive 0.50 floor; bare house words stay quiet."""
    doc = rtc_m.document([entry(RULES[0], keywords={"git push": 0.6, "push": 0.5, "quiet": 0.49,
                                                    "carr": 0.9, "carr surface": 0.9})])
    rows = [r for r in rtc_m.trigger_rows(doc) if r["kind"] == "prompt_regex"]
    return (len(rows) == 1 and re.search(rows[0]["pattern"], "git push now")
            and re.search(rows[0]["pattern"], "the carr surface")
            and re.search(rows[0]["pattern"], "push it")
            and not re.search(rows[0]["pattern"], "quiet")
            and not re.search(rows[0]["pattern"], "carr is fine"))

def prop_required_cues_degraded(rtc_m, rtd_m):
    """Production cues survive both a spent deadline and provider failures."""
    rules = rtc_m.pack_rules()
    doc = rtc_m.load_compiled()
    cases = [("Convene a red team of three reviewers for this design.", "81709f57"),
             ("Look at this https://x.com/someone/status/1234567890", "557838a5"),
             ("Which terms on the LOI template are negotiable?", "4399df76")]
    with tempfile.TemporaryDirectory() as tmp:
        table = os.path.join(tmp, "table.json")
        Path(table).write_text(json.dumps({"triggers": rtc_m.trigger_rows(doc)}))
        for prompt, required in cases:
            for deadline in (0, None):
                rows = rtd_m.advise(prompt, session_id=None, compiled=doc, rules=rules,
                    triggers_path=table, ask=Asker(fail=True), rank=None,
                    client=Client, deadline=deadline, log_path=os.devnull)
                if not any(row["id"] == required and row["source"] == "compiled_trigger"
                           for row in rows):
                    return False
    return True

def prop_no_house_word_candidate(rtc_m, rtd_m):
    rule = {"id": "cccc0001", "gist": "carr surfaces", "packs": ["governance-rules"],
            "statement": "Every CARR surface Claude builds shows its source. CARR CARR."}
    cands, _near = rtc_m.candidates(rule, all_rules=[rule] + RULES, pack_keywords={},
                                    verbs=set())
    values = {value for kind, value, _ in cands if kind == "keyword"}
    return "carr" not in values and "claude" not in values and "carr surface" in values


PROPERTIES = {name: globals()[name] for name in ['prop_negative_masks', 'prop_dedupe', 'prop_no_session_no_pooling', 'prop_coverage_flags_empty_trigger', 'prop_stale_detected', 'prop_surface_floor', 'prop_envelope_no_compiled_rules', 'prop_prompt_floor', 'prop_required_cues_degraded', 'prop_no_house_word_candidate']}
for name, prop in PROPERTIES.items():
    check(name, prop(rtc, rtd))
check("one semantic batch per human boundary", rtd.MAX_JEV_CALLS == 1)
ask = Asker(.99)
rank = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ranking must be code"))
selected, report = rtd.judge_budgeted("vendor intro", ROSTER, [r["id"] for r in RULES],
                                     client=Client, ask=ask, rank=rank)
check("all shortlist questions share one request", len(ask.subjects) == 1 and len(ask.calls) <= rtd.BIND_TOP_K)
check("high semantic confidence cannot bind rules", selected == {} and report["review_required"])
with tempfile.TemporaryDirectory() as tmp:
    cache = os.path.join(tmp,"judgments.json")
    doc = compiled_doc()
    common = dict(session_id=None, compiled=doc, rules=ROSTER, ask=Asker(.99), client=Client,
                  judgment_cache=cache, log_path=os.devnull, triggers_path=table_for(doc,tmp))
    rtd.advise("unrelated semantic question", **common)
    before = len(common["ask"].subjects)
    rtd.advise("unrelated semantic question", **common)
    check("complete shared input repeats reuse cache", len(common["ask"].subjects) == before)
with tempfile.TemporaryDirectory() as tmp:
    os.environ["CARR_JEV_SEMANTIC_CACHE"] = os.path.join(tmp,"semantic")
    calls: list = []
    def fake(state, questions, **kwargs):
        calls.append((state,questions,kwargs))
        return {"model":"jev-1.13.0", "answers":{k:{"type":"noul","noul":.99} for k in questions}}
    args=dict(all_rules=RULES,pack_keywords={},verbs=set(),history={},client=Client,ask=fake)
    proposed=rtc.compile_rule(RULES[0], **args)
    rtc.compile_rule(RULES[0], **args)
    check("compile is cached and pinned", len(calls)==1 and calls[0][2]["model"]=="jev-1.13.0")
    check("compile requires review before deterministic promotion", proposed["mode"]=="review_required")
committed=rtc.load_compiled()
check("committed triggers cover current rules", rtc.coverage_problems(committed,rtc.pack_rules())==[])
# Mutation probes keep teeth on the exact deterministic safety checks.
for number,(name,path,old,new) in enumerate([
    ("prop_negative_masks",RTD_PATH,'body = re.sub(row["negative_pattern"], " ", body, flags=re.I)',"body = body"),
    ("prop_prompt_floor",RTC_PATH,"p >= SURFACE_AT and k not in HOUSE_WORDS","p >= SURFACE_AT"),
    ("prop_envelope_no_compiled_rules",RTD_PATH,'human = not (_is_envelope(situation) if envelope is None else envelope)',"human = True"),
]):
    mutated=load("mutant_"+str(number),path,replace=(old,new))
    check("mutant killed: "+name, not PROPERTIES[name](mutated if path==RTC_PATH else rtc, mutated if path==RTD_PATH else rtd))
if FAILURES:
    raise SystemExit("rule-trigger-compile-selftest: FAIL "+str(FAILURES))
print("rule-trigger-compile-selftest: all cases passed")
