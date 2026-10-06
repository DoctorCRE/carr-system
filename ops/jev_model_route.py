"""Pinned policy routes first. One cached semantic batch advises the route; absent labeled threshold validation, execution retains the policy abstention route."""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import random

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(REPO, "ops", "config", "model-routes.v1.json")
LOG_PATH = os.path.join(REPO, "out", "model-routes.jsonl")
CATALOG_PATH = os.path.join(REPO, "tools", "room-bridge", "queue-targets.json")
TIMEOUT_SECONDS = 30.0
MAX_TASK_CHARS = 4000


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy(path=None):
    with open(path or POLICY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def questions(policy, client):
    return {name: client.noul(q["instructions"], true=q["true"], false=q["false"])
            for name, q in policy["questions"].items()}


def pick_route(scores, policy):
    """The first question in policy order whose score clears its cutoff names the route; else None (abstain)."""
    for name in policy["order"]:
        q = policy["questions"][name]
        if scores.get(name, 0.0) >= q["cutoff"]:
            return q["route"]
    return None


def handoff_reason(answer, run_log):
    """Why a finished Flash run goes to its `then` desk, or None. Facts only, no model judgment:
    no answer; an answer its scripts never printed (flash_answer_checks says "invented"); or two consecutive turns
    that ran out of room with nothing to show (finish=length and empty)."""
    if not answer:
        return "no_answer"
    supports = [e["support"] for e in run_log if "support" in e]
    if supports and supports[-1] == "invented":
        return "invented"
    run = 0
    for e in run_log:
        run = run + 1 if (e.get("empty") and e.get("finish") == "length") else 0
        if run >= 2:
            return "runaway"
    return None


def decide(task, context="", *, flash_free=True, policy=None, judge=None, client=None, rng=random.random,
           log_path=LOG_PATH):
    """Route one task. Returns {route, model, effort, protocol, desk, then, scores, fallback, overflow, audit,
    jev_error}; the row is also appended to log_path. flash_free=False (Flash busy or offline) moves a Flash route
    onto the policy's overflow model rather than queueing."""
    policy = policy or load_policy()
    judge = judge or _sibling("jev_judge")
    scores, error = {}, None
    try:
        client = client or judge._client()
        answer = _sibling("jev_semantic").ask({"task": (task or "")[:MAX_TASK_CHARS], "context": (context or "")[:MAX_TASK_CHARS]},
                             questions(policy, client), timeout=TIMEOUT_SECONDS, client=client, caller="jev_model_route", version="vendor-v1", transport=judge.judge)
        scores = {k: round(float(v["noul"]), 3) for k, v in answer["answers"].items()}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    advisory_route = pick_route(scores, policy) if scores else None
    picked = None  # no labeled threshold receipt: policy abstention is the executable route
    route = picked or policy["abstain_route"]
    entry = dict(policy["routes"][route])
    overflow = entry.get("model") == "flash" and not flash_free
    if overflow:
        entry.update(model=policy["overflow"]["model"], effort=policy["overflow"]["effort"])
    row = {"route": route, "model": entry.get("model"), "effort": entry.get("effort"),
           "protocol": entry.get("protocol"), "desk": entry.get("desk"), "then": entry.get("then"),
           "scores": scores, "advisory_route": advisory_route, "review_required": bool(scores), "fallback": picked is None, "overflow": overflow,
           "audit": rng() < policy.get("audit_rate", 0.0), "jev_error": error}
    if log_path:
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                     "task": (task or "")[:300], **row}) + "\n")
        except OSError:
            pass
    return row


def load_catalog(path=None):
    with open(path or CATALOG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def dispatch(task, context="", *, pin=None, audit_pin=False, flash_free=True,
             policy=None, catalog=None, judge=None, client=None, rng=random.random, log_path=LOG_PATH):
    """Return the selected desk and its route evidence.

    A known pin skips the paid route unless audit_pin explicitly requests a
    comparison sample. Policy pins take precedence over the judged route;
    unpinned work retains the configured target for its judged category.
    An unknown pin raises ValueError. One receipt is logged.
    """
    policy = policy or load_policy()
    catalog = catalog or load_catalog()
    pins = {k: v for k, v in policy["pins"].items() if not k.startswith("_")}
    if pin is not None and pin not in pins:
        raise ValueError(f"unknown pin {pin!r}; known: {', '.join(sorted(pins))}")
    # Explicit policy pins need no paid routing call. An audit is an explicit
    # sample, so the comparison route is calculated only for that request.
    if pin is not None and not audit_pin:
        row = {"route": None, "scores": {}, "fallback": False, "overflow": False,
               "audit": False, "jev_error": None}
    else:
        row = decide(task, context, flash_free=flash_free, policy=policy, judge=judge, client=client, rng=rng,
                     log_path=None)
    # decide() flags overflow for any route whose model is Flash, but only a route that actually queues to the Flash
    # target is affected by Flash being busy; code and script spawns use their configured fallback.
    routed_target = policy["queue_targets"][row["route"]] if row["route"] else None
    if row["route"] in ("script", "code") and routed_target == "flash":
        # Flash's script and code protocols run only on a queued task that names its data, or its project and test
        # (tools/room-bridge/flash_wire.py); an in-process spawn has none to hand it, and Jev's abstention also lands
        # on script, so both keep the configured fallback desk.
        routed_target = policy["queue_targets"]["fallback"]
    overflow = row["overflow"] and routed_target == "flash"
    routed_model = routed_effort = None
    if overflow:
        routed_target = policy["queue_targets"]["fallback"]
        routed_model, routed_effort = policy["overflow"]["model"], policy["overflow"]["effort"]
    elif routed_target is not None:
        routed_model = policy["dispatch_targets"][routed_target]["subagent_model"]
        routed_effort = policy["dispatch_targets"][routed_target]["effort"]
    routed = ({"route": row["route"], "target": routed_target, "subagent_model": routed_model}
              if routed_target is not None else None)
    if pin is None:
        target, subagent_model, effort, reason = routed_target, routed_model, routed_effort, None
    else:
        target, reason = pins[pin]["target"], pins[pin]["reason"]
        subagent_model = policy["dispatch_targets"][target]["subagent_model"]
        effort = policy["dispatch_targets"][target]["effort"]
    out = {"route": row["route"], "target": target, "desk": catalog["targets"][target].get("desk"),
           "subagent_model": subagent_model, "effort": effort,
           "advisory_route": row.get("advisory_route"), "review_required": row.get("review_required", False),
           "pin": pin, "pin_reason": reason, "routed": routed, "scores": row["scores"], "fallback": row["fallback"],
           "overflow": overflow, "audit": row["audit"], "jev_error": row["jev_error"]}
    if log_path:
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                     "kind": "dispatch", "task": (task or "")[:300], **out}) + "\n")
        except OSError:
            pass
    return out


def desk_for(kind, policy=None):
    """The Model Room desk a hand-off of this kind goes to: "code" -> the code route's fixer, anything else -> the
    escalate route's desk. flash-run reads its escalation desks through this."""
    policy = policy or load_policy()
    if kind == "code":
        return policy["routes"]["code"]["then"]["desk"]
    return policy["routes"]["escalate"]["desk"]


if __name__ == "__main__":
    import argparse
    import sys
    # `dispatch` first: what a dispatcher runs for this task, e.g.
    #   jev_model_route.py dispatch "task" [--context ...] [--pin merge_review] [--flash-busy]
    # Anything else: show the bare route decision (the original CLI).
    if sys.argv[1:2] == ["dispatch"]:
        ap = argparse.ArgumentParser(description="What a dispatcher runs for this task (route, or pin and why).")
        ap.add_argument("task")
        ap.add_argument("--context", default="")
        ap.add_argument("--pin")
        ap.add_argument("--audit-pin", action="store_true")
        ap.add_argument("--flash-busy", action="store_true")
        a = ap.parse_args(sys.argv[2:])
        try:
            print(json.dumps(dispatch(a.task, a.context, pin=a.pin, audit_pin=a.audit_pin,
                                      flash_free=not a.flash_busy), indent=1))
        except ValueError as exc:
            print(f"jev_model_route: {exc}", file=sys.stderr)
            raise SystemExit(2)
    else:
        ap = argparse.ArgumentParser(description="Show where the Model Room would route a task.")
        ap.add_argument("task")
        ap.add_argument("--context", default="")
        ap.add_argument("--flash-busy", action="store_true")
        a = ap.parse_args()
        print(json.dumps(decide(a.task, a.context, flash_free=not a.flash_busy), indent=1))
