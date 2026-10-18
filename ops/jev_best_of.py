"""Run deterministic test prefilters first. One bounded cached Choice compares the remaining candidate evidence. A semantic recommendation always escalates for review; only the unique deterministic passing candidate is selected without asking."""

import importlib.util
import os
import json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# How much of a candidate's code/diff rides in the state, per candidate. Kept
# small on purpose: ops/typesafe_client.py documents that accuracy falls as the
# state fills with content unrelated to the decision, and the decision here is
# about EVIDENCE (test_output, probe_results), not about re-deriving what the
# code does by reading all of it again.
CODE_CHARS = 3000
TEST_OUTPUT_CHARS = 1500
PROBE_CHARS = 4000
CANDIDATE_CAP = 8

# Existing callers may pass a display floor; execution always requires review.
CONF_ESCALATE_AT = 0.40

NONE_RIGHT = "none of these candidates is correct"


def _sibling(name):
    """Load a sibling ops/ module by path. ops/ is not a package, and the whole
    point of these files is that they carry no entrypoint, so there is nothing
    to import them as. Same plumbing jev_judge uses to reach typesafe_client."""
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load ops/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _passing_ids(candidates):
    """Candidates whose OWN test run exited 0. A fact, not a judgment."""
    return [c["id"] for c in candidates
            if type(c.get("test_exit_code")) is int and c["test_exit_code"] == 0]


def _has_evidence(candidate):
    return bool((candidate.get("probe_results")) or
                (candidate.get("test_output") or "").strip())


def _candidate_state(candidate):
    """One candidate's evidence, trimmed. Named fields, never bare code alone —
    the lesson this whole module exists to apply."""
    state = {"id": candidate["id"]}
    if candidate.get("probe_results"):
        state["probe_results_excerpt"] = json.dumps(candidate["probe_results"], sort_keys=True)[:PROBE_CHARS]
    if candidate.get("test_output"):
        state["test_output"] = candidate["test_output"][:TEST_OUTPUT_CHARS]
    if "test_exit_code" in candidate:
        state["test_exit_code"] = candidate["test_exit_code"]
    if candidate.get("code_or_diff"):
        state["code_or_diff"] = candidate["code_or_diff"][:CODE_CHARS]
    return state


def _choice_question(candidates, tsc):
    options = {}
    for c in candidates:
        blurb = (f"candidate `{c['id']}`: judge it from its evidence in "
                  f"state.candidates — test_output/test_exit_code and "
                  f"probe_results are what it actually did when run, and are "
                  f"more trustworthy than its code alone")
        options[c["id"]] = blurb
    options[NONE_RIGHT] = (
        "None of the candidates in state.candidates is actually correct for "
        "the task in state.task_text — including when every one of them fails "
        "its own test_output, or when the evidence is too thin (missing "
        "probe_results/test_output) to tell. Choose this rather than guessing.")
    return tsc.choice(
        "state.task_text describes what the code must do. state.candidates "
        "holds one or more attempts at it, each WITH THE EVIDENCE FROM RUNNING "
        "IT — test_output, test_exit_code, and probe_results (deterministic "
        "input -> actual output/exception, from running this exact candidate). "
        "Using that evidence first and the code second, which candidate is the "
        "correct solution?", options)


def select_candidate(task_text, candidates, *, client=None, judge=None,
                      conf_escalate_at=CONF_ESCALATE_AT, log_path=None):
    """Choose which of `candidates` actually solves `task_text`.

    `candidates` is a list of {"id", "code_or_diff", "probe_results",
    "test_output", "test_exit_code"}. Only "id" is required; the more evidence
    fields present, the better this performs (see module docstring).

    DETERMINISTIC PRE-FILTER FIRST: if exactly one candidate's own test run
    exited 0, it is picked without asking Jev at all, and that is recorded.
    Jev is asked only when zero or more-than-one candidates pass their own
    tests — the cases the deterministic facts do not already settle. A
    candidate list with no evidence at all is STILL sent to Jev rather than
    skipped; the module measures that this degrades accuracy, it does not
    forbid asking.

    Returns {"check": "best_of", "verdict": <candidate id> | "none" |
    "unavailable" | "review_required", "confidence": float | None, "escalate": bool,
    "detail": {...}}. NEVER raises. NEVER defaults to "the first candidate" on
    low confidence or on a "none" verdict — escalate=True means a human or a
    larger model looks. Semantic IDs are returned only in detail.advisory_candidate_id.
    """
    judge = judge or _sibling("jev_judge")
    candidates = list(candidates)
    if not candidates:
        return {"check": "best_of", "verdict": "none", "confidence": None,
                "escalate": True, "detail": {"reason": "no candidates given"}}

    passing = _passing_ids(candidates)
    if len(passing) == 1:
        chosen = passing[0]
        detail = {"reason": "deterministic prefilter: exactly one candidate's "
                             "own test run exited 0", "passing_ids": passing}
        judge.record("supervise.best_of", task_text[:200] if task_text else None,
                     {"model": None, "usage": None, "elapsed_ms": 0,
                      "answers": {"pick": {"type": "prefilter", "choice": chosen}}},
                     existing_decision=None,
                     note="deterministic_prefilter", log_path=log_path or judge.SHADOW_LOG)
        return {"check": "best_of", "verdict": chosen, "confidence": 1.0,
                "escalate": False, "detail": detail}

    if len(candidates) > CANDIDATE_CAP or len({c["id"] for c in candidates}) != len(candidates):
        return {"check":"best_of", "verdict":"unavailable", "confidence":None, "escalate":True,
                "detail":{"reason":"review candidate count or duplicate IDs before semantic comparison"}}

    subject = {
        "task_text": (task_text or "")[:6000],
        "candidates": [_candidate_state(c) for c in candidates],
    }
    tsc = client or _sibling("typesafe_client")
    question = {"pick": _choice_question(candidates, tsc)}

    try:
        answer = _sibling("jev_semantic").ask(subject, question, client=client, caller="jev_best_of", version="vendor-v1", transport=judge.judge)
    except Exception as exc:  # judge.JudgeUnavailable and anything else
        judge_mod = judge
        try:
            judge_mod.record("supervise.best_of",
                              task_text[:200] if task_text else None, None,
                              None, error=exc, log_path=log_path or judge_mod.SHADOW_LOG)
        except Exception:
            pass
        return {"check": "best_of", "verdict": "unavailable", "confidence": None,
                "escalate": True,
                "detail": {"reason": f"{type(exc).__name__}: {exc}",
                            "passing_ids": passing}}

    pick = answer["answers"]["pick"]
    choice_val = pick.get("choice")
    confidence = pick.get("confidence")
    confidence = None if confidence is None else float(confidence)

    verdict = "none" if choice_val in (None, NONE_RIGHT) else choice_val
    escalate = True  # candidate choice is advisory until validated on labeled tasks
    if verdict == "none":
        confidence_note = "verdict is 'none': caller decides, never coerced to a candidate id"
    else:
        confidence_note = "independent review required; confidence is not calibrated action authority"

    detail = {
        "reason": "evidence-based Jev choice",
        "passing_ids": passing,
        "any_evidence": any(_has_evidence(c) for c in candidates),
        "confidence_note": confidence_note,
        "model": answer.get("model"),
        "advisory_candidate_id": None if verdict == "none" else verdict,
    }
    judge.record("supervise.best_of", task_text[:200] if task_text else None,
                 answer, existing_decision=None, log_path=log_path or judge.SHADOW_LOG)
    return {"check": "best_of", "verdict": "review_required", "confidence": confidence,
            "escalate": escalate, "detail": detail}
