"""One bounded cached Choice recommends an executor tier. The pinned model is advisory: ACT_AT is above the probability domain until independently labeled calibration validates an automatic policy."""

from __future__ import annotations

import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cheapest first. The order is the cost order, and "cheaper" below means an
# earlier index.
TIERS = ("haiku", "sonnet", "opus", "fable")

RUBRICS = {
    "haiku": (
        "A lookup or mechanical job with one obvious correct procedure: find a "
        "file or symbol, extract fields, list or count things, read one or two "
        "files and report what they say. A wrong answer is cheap to notice."),
    "sonnet": (
        "A sweep, survey or ordinary implementation: read across many files and "
        "summarise, trace call sites, write or fix code to a clear spec, run "
        "and interpret tests. Needs care and competence, not deep judgment."),
    "opus": (
        "Judgment that carries weight: verifying load-bearing findings, "
        "adversarial review, security or data-privacy reasoning, client-facing "
        "prose, or a design choice with real trade-offs where a plausible "
        "wrong answer would be costly and hard to spot."),
    "fable": (
        "Only deep architecture or doctrine work that the other tiers would "
        "likely get wrong even with a clear brief: a new system design, a "
        "subtle cross-cutting correctness argument, or doctrine that binds "
        "future sessions. Never a default."),
}

ACT_AT = 1.01          # below this the pick is advice nobody should act on
TIMEOUT_SECONDS = 8.0  # a spawn waiting on a judgment has a person behind it
MAX_TASK_CHARS = 4000  # Jev reads 32k tokens; a brief longer than this is trimmed


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def question(client=None):
    tsc = client or _sibling("typesafe_client")
    return tsc.choice(
        "A session is about to hand the task in `state.task` to a subagent. "
        "Choose the CHEAPEST model tier that would still do this task "
        "correctly. Judge the work the task demands, not how important the "
        "surrounding project is, and not how long the brief is. When two tiers "
        "would both do it correctly, choose the cheaper one.",
        dict(RUBRICS))


def recommend(description, prompt, subagent_type="", *, judge=None, client=None, api_key=None):
    """(tier, probability, probabilities) or None when no judgment was obtained."""
    judge = judge or _sibling("jev_judge")
    task = {"description": (description or "")[:300],
            "subagent_type": subagent_type or "",
            "prompt": (prompt or "")[:MAX_TASK_CHARS]}
    try:
        semantic = _sibling("jev_semantic")
        answer = semantic.evaluate(semantic.JudgmentRequest(
            {"task": task}, {"tier": question(client)}, timeout=TIMEOUT_SECONDS,
            api_key=api_key, caller="jev_executor_tier", version="vendor-v1"),
            adapter=semantic.LiveAdapter(client=client, transport=judge.judge)).unwrap()
        probabilities = answer["answers"]["tier"].get("probabilities") or {}
    except Exception as exc:
        try:
            judge.record("executor_tier", (description or "")[:120], None, None, error=exc)
        except Exception:
            pass
        return None
    ranked = sorted(((tier, float(probabilities.get(tier, 0.0))) for tier in TIERS),
                    key=lambda item: -item[1])
    if not ranked or ranked[0][1] <= 0.0:
        return None
    tier, probability = ranked[0]
    return tier, probability, {t: round(p, 3) for t, p in ranked}


def cheaper(pick, chosen):
    """True when `pick` is a strictly cheaper tier than `chosen`."""
    names = {t: i for i, t in enumerate(TIERS)}
    chosen = (chosen or "").lower()
    for tier in TIERS:
        if tier in chosen:
            chosen = tier
            break
    return pick in names and chosen in names and names[pick] < names[chosen]
