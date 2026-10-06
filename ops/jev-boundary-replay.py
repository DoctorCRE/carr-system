#!/usr/bin/env python3
"""Offline labeled replay and conservative spend model for Jev boundary checks."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
FIXTURE = REPO / "ops/fixtures/jev-boundary/labeled-cases.json"


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "ops" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = load("jev_session_watch")
done = load("jev_done_checks")


class Client:
    def __init__(self, answers, *, old=False):
        self.answers = answers
        self.old = old
        self.calls = []

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}

    @staticmethod
    def choice(instructions, options):
        return {"type": "choice", "instructions": instructions, "criteria": options}

    @staticmethod
    def score(instructions, levels):
        return {"type": "score", "instructions": instructions, "criteria": levels}

    def ask(self, state, questions, **_kw):
        self.calls.append({"state": state, "questions": questions})
        result = {}
        for key, q in questions.items():
            alias = {"instructs_the_agent": "instructs",
                     "would_exceed_task": "exceeds",
                     "evidence_shows_omitted_failure": "omitted_failure"}.get(key, key)
            value = self.answers.get(alias)
            if key == "intended_path" and value == "path_0" and self.old:
                value = "ops/jev_build_advisory.py"
            if q["type"] == "noul":
                result[key] = {"type": "noul", "noul": 0.05 if value is None else value}
            elif q["type"] == "choice":
                result[key] = {"type": "choice", "choice": value or "none",
                               "confidence": 0.9}
            else:
                result[key] = {"type": "score", "score": 2 if value is None else value,
                               "confidence": 0.9}
        return {"model": "offline-labeled", "answers": result, "usage": {}}


class Judge:
    JudgeUnavailable = RuntimeError

    def __init__(self, client):
        self.client = client

    def _client(self):
        return self.client

    def judge(self, state, questions, **_kw):
        return self.client.ask(state, questions)

    def record(self, *_args, **_kw):
        pass


def flags(rows):
    out = set()
    for row in rows:
        verdict = row.get("verdict")
        check = row.get("check")
        if verdict == "planted_instruction": out.add("security")
        if check == "ci_result" and verdict == "failed": out.add("ci_failed")
        if check == "failure_triage" and verdict not in {"unavailable", "no_failure"}:
            out.add("failure")
        if check == "path_repair" and verdict == "path_found": out.add("path_repair")
        if check == "done_claim" and verdict == "unsupported": out.add("done_unsupported")
        if check == "done_claim" and verdict == "needs_review": out.add("done_review")
        if check == "review_triage" and verdict == "needs_review": out.add("review_high")
    return out


def evaluate_case(case, *, old, temp_dir):
    family = case["family"]
    client = Client(case.get("answers") or {}, old=old)
    judge = Judge(client)
    results = []
    if family == "tool":
        tool, inp, output = case["tool"], case["input"], case["output"]
        code = case.get("exit_code")
        if old:
            if tool == "Bash" and code not in (None, 0):
                results.append(watch.triage_failure(str(inp.get("command") or ""), output,
                                                    code, client=client))
                if "pytest" in str(inp.get("command") or ""):
                    results.append({"check": "ci_result", "verdict": "failed"})
            if "No such file" in output and inp.get("file_path"):
                results.append(watch.repair_path(inp["file_path"], str(REPO),
                               client=client, files=["ops/jev_build_advisory.py"]))
        else:
            results = watch.inspect_tool_event(tool, inp, output, code, "task", str(REPO),
                client=client, judge_module=judge,
                receipt_path=os.path.join(temp_dir, "tool-receipts.jsonl"))
    elif family in {"stop", "stop_repeat"}:
        if old:
            results.append(done.check_done_claim(case["final"], case["evidence"]))
            results.append(done.triage_review(case["diff"], "task"))
        else:
            # stop_repeat deliberately uses the same session and state marker.
            results = done.inspect_stop_boundary(case["final"], case["evidence"],
                case["diff"], "task", "repeated-stop" if family == "stop_repeat" else "stop")
            if family == "stop_repeat":
                results = done.inspect_stop_boundary(case["final"], case["evidence"],
                    case["diff"], "task", "repeated-stop")
                client.calls.clear()
    found = flags(results)
    if any(r.get("verdict") == "obligation" for r in results):
        found.add("prompt_obligation")
    return {"found": sorted(found), "calls": len(client.calls),
            "input_tokens_estimate": sum(len(json.dumps(c)) // 4 for c in client.calls)}


def log_row_hashes(paths):
    found = set()
    for path in paths:
        with open(REPO / path, errors="replace") as fh:
            for line in fh:
                try: json.loads(line)
                except ValueError: continue
                found.add((path, hashlib.sha256(line.rstrip("\n").encode()).hexdigest()))
    return found


def labeled_replay(*, verify_sources=True):
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["schema"] == "jev-boundary-calibration/v1"
    hashes = log_row_hashes(fixture["source_logs"]) if verify_sources else None
    rows = []
    for case in fixture["cases"]:
        if case["family"] == "intake":
            continue
        if hashes is not None:
            assert (case["source_log"], case["source_row_sha256"]) in hashes, case["id"]
        with tempfile.TemporaryDirectory() as tmp:
            before = evaluate_case(case, old=True, temp_dir=tmp)
            after = evaluate_case(case, old=False, temp_dir=tmp)
        expected = set(case["expected"])
        rows.append({"id": case["id"], "expected": sorted(expected),
                     "before": before, "after": after})
    for row in rows:
        for side in ("before", "after"):
            found = set(row[side]["found"])
            row[side]["detected"] = len(found & set(row["expected"]))
            row[side]["false_positives"] = len(found - set(row["expected"]))
    return rows


TARGET_FAMILIES = {
    "build": lambda q: "architecture_or_design" in q,
    "progress": lambda q: "stuck_in_loop" in q,
    "done": lambda q: "claims_supported" in q,
    "failure": lambda q: "failure_class" in q,
    "injection": lambda q: "instructs_the_agent" in q,
    "path": lambda q: "intended_path" in q,
    "bug": lambda q: "culprit_line" in q,
    "test": lambda q: any(x.startswith("relevant_") for x in q),
    "review": lambda q: any(x.startswith("risk_") for x in q),
}


def family_for(question_ids):
    q = set(question_ids or [])
    for name, predicate in TARGET_FAMILIES.items():
        if predicate(q): return name
    return None


def spend_replay(days=("2026-09-26", "2026-09-27")):
    """Actual recorded sessions and tokens; conservative after removes intake.

    Raw tool-result text and hook correlation are absent from this usage log,
    so batching/throttling savings are excluded from the numeric forecast.
    """
    by_day = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    with open(REPO / "out/jev-calls.jsonl", errors="replace") as fh:
        for line in fh:
            try: row = json.loads(line)
            except ValueError: continue
            day = str(row.get("ts") or "")[:10]
            if day not in days or not row.get("ok"): continue
            family = family_for(row.get("question_ids"))
            if not family: continue
            session = row.get("session") or "unknown"
            tokens = int((row.get("usage") or {}).get("input_tokens") or 0)
            by_day[day][session][family + "_calls"] += 1
            by_day[day][session][family + "_tokens"] += tokens
    out = {}
    for day, sessions in sorted(by_day.items()):
        before_calls = sum(sum(v for k,v in counts.items() if k.endswith("_calls"))
                           for counts in sessions.values())
        before_tokens = sum(sum(v for k,v in counts.items() if k.endswith("_tokens"))
                            for counts in sessions.values())
        build_calls = sum(c.get("build_calls",0) for c in sessions.values())
        build_tokens = sum(c.get("build_tokens",0) for c in sessions.values())
        per_session = {}
        for session, counts in sessions.items():
            calls = sum(v for k, v in counts.items() if k.endswith("_calls"))
            tokens = sum(v for k, v in counts.items() if k.endswith("_tokens"))
            per_session[session] = {
                "before_calls": calls,
                "after_calls_conservative": calls-counts.get("build_calls", 0),
                "before_input_tokens": tokens,
                "after_input_tokens_conservative": tokens-counts.get("build_tokens", 0),
            }
        out[day] = {"sessions": len(sessions), "before_calls": before_calls,
                    "after_calls_conservative": before_calls-build_calls,
                    "before_input_tokens": before_tokens,
                    "after_input_tokens_conservative": before_tokens-build_tokens,
                    "build_calls_removed": build_calls,
                    "build_input_tokens_removed": build_tokens,
                    "per_session": per_session}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="-")
    args = p.parse_args()
    fixture = json.loads(FIXTURE.read_text())
    have_logs = all((REPO / path).exists() for path in fixture["source_logs"])
    rows = labeled_replay(verify_sources=have_logs)
    spend = spend_replay() if have_logs else {}
    totals = {side: {
        "detections": sum(row[side]["detected"] for row in rows),
        "false_positives": sum(row[side]["false_positives"] for row in rows),
        "calls": sum(row[side]["calls"] for row in rows),
        "input_token_proxy": sum(row[side]["input_tokens_estimate"] for row in rows),
    } for side in ("before", "after")}
    result = {"schema":"jev-boundary-replay-result/v1", "model":"jev-1.13.0",
              "labeled":rows, "labeled_totals": totals, "spend":spend,
              "recorded_logs_available": have_logs,
              "limits":"Spend projection removes prompt intake only; log rows lack raw tool correlation, so batch and throttle savings are not credited."}
    body = json.dumps(result, sort_keys=True, indent=2)
    if args.output == "-": print(body)
    else: Path(args.output).write_text(body + "\n")


if __name__ == "__main__":
    main()
