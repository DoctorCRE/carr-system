"""Read the codebase for slop and for bugs, with a judgment instead of a grep.

WHY A JUDGMENT AND NOT MORE PATTERNS. Every signature worth scanning for here
is ambiguous at the pattern level, and the ambiguity is the whole problem. A
hook that catches every exception and returns is CORRECT — a gate that raises
wedges the session, so failing open is the design. The same three lines in a
data path are a failure nobody will ever see. 205 of them are in this tree and
a grep cannot tell you which kind it found. Neither can a rule that says "avoid
bare excepts"; it gets suppressed everywhere and stops meaning anything.

THE SHAPE, and it is the documented one rather than the obvious one. A cheap
deterministic pass finds CANDIDATE REGIONS. Only then does a judgment read
them, and it reads each region ONCE with every question asked together, since
independent questions about one subject belong in a single request — measured
by the vendor at 12.2x cheaper than asking them one at a time, and each
question is still scored on its own against the state.

QUESTIONS ARE NARROW ON PURPOSE. "Is this slop?" is a broad question hiding
several judgments, which is the documented mistake. Each question below decides
one thing, states its own criteria, and carries its full meaning in its text —
the ids are for this module and are never sent.

NOTHING HERE DECIDES ANYTHING. It produces a reading list ordered by
probability. A finding is a place to look, and the removal is a person's call
made against the code, because the cost of deleting a live failure path is
far higher than the cost of reading a false positive.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Region sizing. Big enough that the judgment can see why the code is there,
# small enough that the state is not filled with detail unrelated to the
# decision -- accuracy falls as a state fills with the irrelevant.
CONTEXT_BEFORE = 14
CONTEXT_AFTER = 10
MAX_REGION_CHARS = 2600
WORKERS = 8
TIMEOUT_SECONDS = 90.0
REPORT_AT = 0.55


def _client():
    spec = importlib.util.spec_from_file_location(
        "typesafe_client", os.path.join(REPO, "ops", "typesafe_client.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the cheap pass -------------------------------------------------------
#
# Each entry is a signature that is AMBIGUOUS by design: it must match the
# innocent case too, or the judgment has nothing to rule on and the pass is
# just a grep with extra steps.

SIGNATURES = (
    ("swallowed_failure",
     re.compile(r"except(?:[ \t]+(?:Exception|BaseException)[^\n:]*|[ \t]*):\n\s+(?:pass|return\b[^\n]*)\n")),
    # Only the shape that actually bites: a coercion carrying its own default.
    # str(x) alone is 2,770 places and nearly all of them are printing; str(x
    # or "") is the one that turns None into the truthy string "None" while
    # LOOKING like it defends against None.
    ("truthiness_coercion",
     re.compile(r"str\(\s*[^()\n]*\bor\b[^()\n]*\)")),
    ("mutable_default",
     re.compile(r"def\s+\w+\([^)]*=\s*(?:\[\]|\{\})[^)]*\)")),
    ("unclosed_resource",
     re.compile(r"^\s*\w+\s*=\s*(?:open|socket\.socket|subprocess\.Popen)\(", re.M)),
    ("check_then_use",
     re.compile(r"os\.path\.exists\([^)]+\)[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*(?:open|remove|unlink|rename)\(")),
    ("silent_numeric_fallback",
     re.compile(r"except\s*(?:\([^)]*\)|\w+)[^\n]*:\n\s+(?:\w+\s*=\s*(?:0|None|\[\]|\{\}|\"\"|''))\n")),
    ("broad_sleep_retry",
     re.compile(r"(?:time\.)?sleep\(\s*\d")),
)


def tracked_sources(repo=REPO, suffixes=(".py", ".mjs", ".js")):
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True,
                         cwd=repo, timeout=120).stdout
    return [f for f in map(os.fsdecode, out.split(b"\0"))
            if f.endswith(suffixes) and not f.startswith("node_modules")]


def regions(paths, repo=REPO):
    """Candidate regions, each carrying enough context to be judged."""
    found = []
    for rel in paths:
        try:
            text = open(os.path.join(repo, rel), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        lines = text.splitlines()
        for kind, pattern in SIGNATURES:
            for match in pattern.finditer(text):
                line_no = text[:match.start()].count("\n") + 1
                lo = max(0, line_no - 1 - CONTEXT_BEFORE)
                match_end_line = text[:match.end() - 1].count("\n") + 1
                hi = min(len(lines), max(match_end_line, line_no + CONTEXT_AFTER))
                full_snippet = "\n".join(lines[lo:hi])
                # Remove only surrounding context; preserve every matched line.
                anchor = line_no - 1
                while len(full_snippet) > MAX_REGION_CHARS and (lo < anchor or hi > match_end_line):
                    if lo < anchor and (hi <= match_end_line or
                                        anchor - lo >= hi - match_end_line):
                        lo += 1
                    else:
                        hi -= 1
                    full_snippet = "\n".join(lines[lo:hi])
                region_start = sum(len(line) + 1 for line in lines[:lo])
                region_end = region_start + len(full_snippet)
                # A match can sit beyond the cap on its own line. Move the
                # bounded character window around its columns, preserving the
                # matched source rather than only the beginning of that line.
                snippet_start = region_start
                if match.end() > region_start + MAX_REGION_CHARS:
                    snippet_start = max(region_start, match.start() - (MAX_REGION_CHARS - (match.end() - match.start())) // 2)
                snippet_end = min(region_end, snippet_start + MAX_REGION_CHARS)
                snippet = text[snippet_start:snippet_end]
                start_line = text[:snippet_start].count('\n') + 1
                end_line = start_line + full_snippet.count("\n")
                sent_end_line = (end_line if len(snippet) == len(full_snippet) else
                                 start_line + snippet.count("\n") - 1)
                found.append({"path": rel, "line": line_no, "kind": kind,
                              "code": snippet, "start_line": start_line,
                              "end_line": end_line,
                              "sent_end_line": sent_end_line,
                              "start_offset": snippet_start, "end_offset": snippet_end,
                              "match_start": match.start(), "match_end": match.end()})
    return _collapse(found)


def _collapse(found, window=CONTEXT_BEFORE + CONTEXT_AFTER):
    """One region per neighbourhood, carrying every reason it was flagged.

    Signatures overlap constantly -- a swallowed failure sits inside the same
    twenty lines as the numeric fallback it assigns. Judging that twice costs
    two requests and returns two nearly identical answers about one piece of
    code, which is a measurement error as much as a cost.
    """
    by_file = {}
    for item in found:
        by_file.setdefault(item["path"], []).append(item)
    out = []
    for path, items in by_file.items():
        items.sort(key=lambda i: i["line"])
        current = None
        for item in items:
            if (current and item["line"] - current["line"] <= window
                    and current["sent_end_line"] == current["end_line"]
                    and item["sent_end_line"] == item["end_line"]
                    and item["start_line"] <= current["end_line"] + 1
                    and current["start_line"] <= item["end_line"] + 1):
                lo = min(current["start_line"], item["start_line"])
                hi = max(current["end_line"], item["end_line"])
                source = {}
                for region in (current, item):
                    source.update(enumerate(region["code"].split("\n"), region["start_line"]))
                code = "\n".join(source[n] for n in range(lo, hi + 1))
                if len(code) > MAX_REGION_CHARS:
                    current = dict(item)
                    out.append(current)
                    continue
                current.update(code=code, start_line=lo, end_line=hi, sent_end_line=hi)
                if item["kind"] not in current["kind"].split("+"):
                    current["kind"] += "+" + item["kind"]
                continue
            current = dict(item)
            out.append(current)
    return out


def dead_functions(repo=REPO):
    """Module-level functions no tracked file mentions by name.

    Deliberately a separate pass: this one is NOT ambiguous enough to need a
    judgment on its own, but the judgment is still asked whether removing it
    would lose a behaviour, because a name reached only through getattr or a
    dispatch table looks identical to a dead one from here.
    """
    paths = [p for p in tracked_sources(repo) if p.endswith(".py")]
    corpus = {}
    for rel in paths:
        try:
            corpus[rel] = open(os.path.join(repo, rel), encoding="utf-8",
                               errors="ignore").read()
        except OSError:
            continue
    whole = "\n".join(corpus.values())
    counts = {}
    for rel, text in corpus.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                if len(re.findall(r"\b%s\b" % re.escape(node.name), whole)) <= 1:
                    counts[f"{rel}::{node.name}"] = node.lineno
    return counts


# --- the questions --------------------------------------------------------
#
# One judgment each. Written so a person reading only the question knows what
# a yes means, because a yes here sends someone to read code.

QUESTIONS = {
    "failure_leaves_no_trace":
        "The code in `region.code` handles a failure. Does that handling discard "
        "the failure with NO trace at all — no log line, no counter, no receipt, "
        "no re-raise, no returned error — so that when it fires nobody can "
        "afterwards tell that it did? Answer no if it records the failure "
        "anywhere, and answer no if the failure is genuinely uninteresting "
        "(a best-effort cleanup, an optional cache, a probe whose absence is "
        "the normal case).",

    "swallow_is_wrong_here":
        "Assume the code in `region.code` does swallow a failure silently. Is "
        "that WRONG in this specific place — meaning a real fault could pass "
        "through it and be read downstream as success? Answer no when failing "
        "quietly is the correct design here, which is the case for a hook or "
        "gate that must never wedge a session, for optional telemetry, and for "
        "cleanup that runs after the work is already done.",

    # REWRITTEN after a polarity check: the first version scored the harmless
    # printing case (0.62) ABOVE the real guard bug (0.44), because it asked
    # whether the coercion was present rather than whether anything downstream
    # was fooled by it. Presence is what the regex already found; the judgment
    # is only worth a request if it rules on the consequence.
    "truthy_coercion_bug":
        "In `region.code` a value is coerced with str(). Is the RESULT of that "
        "coercion then used in a truth test, an emptiness check, a guard, or a "
        "condition that decides whether to proceed? That is the bug, because "
        "str(None) is the four-character string 'None' and str([]) is '[]', "
        "both truthy, so the guard admits the very inputs it was written to "
        "reject. Answer NO when the coerced value is only formatted, printed, "
        "logged, concatenated or returned — those are correct uses of str() "
        "and no decision depends on them.",

    "boundary_bug":
        "Does the code in `region.code` contain an off-by-one, an inverted "
        "comparison, an inclusive bound that should be exclusive, or a slice "
        "or index that can run past the end of what it reads? Answer yes only "
        "for a concrete case you can point at, not for code that merely "
        "involves arithmetic.",

    "state_can_change_between":
        "Does the code in `region.code` check a condition about the world — a "
        "file existing, a lock being free, a row being absent — and then act on "
        "that condition in a separate step, so the world could change in "
        "between and the action fail or do the wrong thing? Answer no when the "
        "gap is harmless because the action is idempotent or the failure is "
        "caught and handled correctly.",

    "comment_no_longer_true":
        "Do the comments or the docstring in `region.code` describe behaviour "
        "the code does not have — naming a parameter, a return value, a file, a "
        "threshold or a sequence of steps that does not match what is written "
        "below them? Answer no for a comment that is merely terse or that "
        "explains why rather than what.",

    "unreachable_or_dead":
        "Is some part of the code in `region.code` unable to run — after an "
        "unconditional return or raise, inside a condition that cannot be true, "
        "or in a branch a preceding check already excluded?",

    "duplicated_and_diverged":
        "Does the code in `region.code` look like a copy of logic that belongs "
        "somewhere shared, such that fixing a bug here would leave the other "
        "copy wrong? Answer yes only when the code itself shows the sign — a "
        "comment saying it is copied, a near-identical helper name, or a "
        "constant that plainly has to agree with one defined elsewhere.",
}


def review_one(region, client=None, api_key=None):
    """Every question about one region, in ONE request."""
    return _review(region, None, client=client, api_key=api_key)[0]


def _review(region, task, client=None, api_key=None):
    """One request: the region questions, plus the task-fit ones when a task
    is known. Returns (scores, raw answer) so a shadow row can keep both."""
    tsc = client or _client()
    questions = {qid: tsc.noul(text) for qid, text in QUESTIONS.items()}
    state = {"region": {"path": region["path"], "line": region["line"],
                        "why_it_was_flagged": region["kind"],
                        "code": region["code"][:6000]}}
    if task:
        for qid, (text, true, false) in TASK_QUESTIONS.items():
            questions[qid] = tsc.noul(text, true=true, false=false)
        state["task"] = {"latest_human_request": task[:3000]}
    answer = _semantic().ask(state, questions, caller="jev_code_review", version="vendor-v1", client=tsc, timeout=TIMEOUT_SECONDS, api_key=api_key)
    scores = {qid: answer_value(body)
              for qid, body in (answer.get("answers") or {}).items()}
    model = answer.get("model")
    if isinstance(model, str) and model.strip():
        scores["_model"] = model
    return scores, answer


def answer_value(body):
    """Read an answer without guessing which key holds it.

    Every answer names its own primitive in `type`, and the answer sits under
    the key of that same name: a noul returns {"type":"noul","noul":0.93}, a
    choice returns {"type":"choice","choice":"blue", ...}. So the type IS the
    key, which is derived from the response rather than remembered.

    THIS COST A WHOLE SCAN. The first version of this function read
    body["probability"], a key that does not exist, and float(None or 0.0) is
    0.0 -- so 426 regions came back scored zero and looked like a clean
    codebase. It was caught by running two known-bad snippets through it and
    seeing them score the same as two known-good ones, which is the only
    reason it was caught at all. A reader that cannot fail loudly has to be
    checked against an answer known in advance.
    """
    kind = body.get("type")
    if kind and kind in body:
        value = body[kind]
        return float(value) if isinstance(value, (int, float)) else value
    raise KeyError(
        "answer names type %r but carries no key of that name: %r"
        % (kind, sorted(body)))


def review(candidates, *, workers=WORKERS, client=None, api_key=None,
           on_done=None):
    tsc = client or _client()
    results = []

    def one(region):
        try:
            return region, review_one(region, client=tsc, api_key=api_key)
        except Exception as exc:                       # a scan never aborts
            return region, {"_error": str(exc)[:160]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for region, scores in pool.map(one, candidates):
            region = dict(region)
            region["scores"] = scores
            results.append(region)
            if on_done:
                on_done(region)
    return results


def findings(results, floor=REPORT_AT):
    """Flatten to one row per question that cleared the floor.

    A region is not a finding; a QUESTION about a region is. The same twenty
    lines can be a correct fail-open and a stale comment at once, and folding
    those into one score would hide both.
    """
    rows = []
    for item in results:
        for qid, probability in (item.get("scores") or {}).items():
            if qid.startswith("_"):
                continue
            if probability >= floor:
                rows.append({"path": item["path"], "line": item["line"],
                             "question": qid, "probability": probability,
                             "flagged_as": item["kind"]})
    rows.sort(key=lambda r: -r["probability"])
    return rows


# --- task fit, acting -------------------------------------------------------
#
# JOE, 2026-09-23 (decision a98c2832, Jev supervision checks): the per-edit
# review judged the code against nothing but itself, so a change that was well
# written and not what was asked for went through unremarked. The two
# questions below read the change against the most recent human request. They
# ride in the SAME request as the questions above.
#
# Task fit shares the region request. Its provisional floor selects advice for
# review; no labeled threshold receipt authorizes a required action.

TASK_FIT_KIND = "post_write_task_fit"
TASK_FIT_ACT_AT = 0.85            # starting point carried over from the shadow era
ADVISORY_AT = 0.85                # the hook's REVIEW_AT, for the comparison row
TASK_TAIL_CHARS = 2000
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024

# id -> (question, true criterion, false criterion); the criteria agree with
# the question's polarity, as ops/typesafe_client.py noul() requires.
TASK_QUESTIONS = {
    "task_unrequested_or_contradicts": (
        "`task.latest_human_request` is the most recent instruction a person "
        "gave the session that wrote `region.code`. Does this change do "
        "something that request did not ask for, or contradict what it asked? "
        "Answer no when the change is plainly a step toward the request, "
        "including the tests, comments and small supporting edits it needs.",
        "The change adds behaviour the request did not ask for, or undoes or "
        "contradicts something the request asked for.",
        "The change is a step toward what the request asked for, or "
        "supporting work that step needs."),
    "task_goal_mistake": (
        "`task.latest_human_request` is the most recent instruction a person "
        "gave the session that wrote `region.code`. Does this change contain a "
        "concrete mistake that will make the request's goal fail: a wrong "
        "value, condition or target, or a step that does the opposite of what "
        "is needed? Answer yes only for a mistake you can point at in the "
        "code, not for code that is merely unfinished.",
        "The change contains a specific mistake that will stop the request's "
        "goal from being met.",
        "No specific mistake in the change would stop the request's goal from "
        "being met."),
}


def latest_task(transcript_path, *, tail_chars=TASK_TAIL_CHARS,
                tail_bytes=TRANSCRIPT_TAIL_BYTES):
    """The most recent human prompt in a Claude Code transcript, or None.

    The transcript is JSONL. A human prompt is a "user" entry whose message
    content is a string, or a list of text blocks with no tool_result; a
    tool_result is the harness talking, not the person. Only the last
    `tail_bytes` of the file are read, and only the last `tail_chars` of the
    prompt are kept, because the ask usually sits at the end of a long paste.
    Never raises: no task means no task judgment.
    """
    try:
        if not isinstance(transcript_path, str) or not transcript_path:
            return None
        with open(transcript_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - tail_bytes))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "user":
                continue
            message = row.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            text = None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                blocks = [b for b in content if isinstance(b, dict)]
                if any(b.get("type") == "tool_result" for b in blocks):
                    continue
                text = "\n".join(b["text"] for b in blocks
                                 if b.get("type") == "text"
                                 and isinstance(b.get("text"), str))
            if isinstance(text, str) and text.strip():
                return text.strip()[-tail_chars:]
        return None
    except Exception:              # an unreadable transcript is recorded as no task
        return None


def _judge_module():
    spec = importlib.util.spec_from_file_location(
        "jev_judge", os.path.join(REPO, "ops", "jev_judge.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_for_edit(region, payload, client=None, api_key=None, log_path=None):
    """Return region advice and optional task-fit review advice from one batch.

    A transport failure propagates to the existing unavailable receipt. Without
    task evidence, only the region questions are asked.
    """
    payload = payload if isinstance(payload, dict) else {}
    task = latest_task(payload.get("transcript_path"))
    record_kwargs = {} if log_path is None else {"log_path": log_path}
    subject = {"path": region.get("path"),
               "session_id": payload.get("session_id"),
               "tool_use_id": payload.get("tool_use_id")}

    def record(*args, **kwargs):
        try:
            _judge_module().record(TASK_FIT_KIND, *args, **kwargs, **record_kwargs)
        except Exception:          # the shadow log must never change the hook
            return None

    try:
        scores, answer = _review(region, task, client=client, api_key=api_key)
    except Exception as exc:
        if task:
            record(subject, {}, None, error=f"{type(exc).__name__}: {exc}"[:200])
        raise
    if not task:
        return scores
    task_scores = {qid: scores.pop(qid) for qid in TASK_QUESTIONS if qid in scores}
    if len(task_scores) != len(TASK_QUESTIONS) or not all(
            isinstance(value, (int, float)) for value in task_scores.values()):
        record(subject, {}, None, error="task_fit_answers_missing")
        return scores
    top = max(task_scores.values())
    would_block = top >= TASK_FIT_ACT_AT
    advisory = sorted(name for name, value in scores.items()
                      if not name.startswith("_")
                      and isinstance(value, (int, float)) and value >= ADVISORY_AT)
    record(dict(subject, would_block=would_block, threshold=TASK_FIT_ACT_AT,
                task_scores=task_scores),
           answer, {"advisory_findings": advisory, "effect": "advisory"},
           note="agreed" if would_block == bool(advisory) else "disagreed")
    if would_block:
        scores["_review_task_fit"] = top
    return scores


def _semantic():
    spec = importlib.util.spec_from_file_location("jev_semantic", os.path.join(REPO, "ops", "jev_semantic.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
