#!/usr/bin/env python3
"""executor-tier-gate.py — make the executor an explicit choice, not a default.

WHY THIS EXISTS. Three active rules already say the executor must be named and
must be the cheapest one still qualified: the subagent cost gate (2dbb0ad8),
the executor-choice rule whose audit signal is a stated "executor: <tier>" line
(185013c6), and the rule reserving the frontier model for its best use cases
(fb110a39). All three are PROSE. Nothing enforced them, and on 2026-08-13 a
live test showed exactly what that costs: a `general-purpose` subagent spawned
with no `model` parameter reported back "You are powered by the model named
Opus 5 (1M context)". It inherited the parent tier silently, and nothing in the
transcript said so.

That is the whole leak. The fifteen custom CARR agents pin a model in their own
frontmatter, so they are safe by construction. The GENERIC types — the ones a
session reaches for when it wants a cheap mechanical sweep, `general-purpose`,
`Explore`, `Plan` — define no model at all, so per the Agent tool's own contract
they "inherit from the parent". The parent here is pinned to Opus. A session
intending a throwaway grep and forgetting one parameter buys the whole sweep at
top tier, and the report still reads clean.

WHAT THIS IS NOT. It is not the Claude Code 2.1.223 "restricted subagent model"
warning. That warning was assessed on 2026-08-13 and CANNOT fire here: nothing
on this machine restricts any model (no managed settings, no MDM profile, no
restricting key or env var), and none of CARR's model-request sites fall into
the four categories it covers. It was checked before this was built, so nobody
re-proposes it later as the fix for this.

WHAT IT CHECKS. One question, at the one detectable moment: an Agent spawn is
about to happen and NOTHING has named the tier it will run on.

  · `model` passed on the call            -> allow. The executor is named.
  · `subagent_type: fork`                 -> allow. The Agent tool documents
                                             model as ignored for forks; they
                                             always inherit the parent, so
                                             denying would demand the
                                             impossible.
  · a definition file pinning `model:`    -> allow. The job description names
                                             its own tier, which is the point
                                             of pinning it there.
  · none of the above, and Jev is
    confident (>= ACT_AT) in a tier    -> ALLOW WITH JEV'S PICK FILLED IN as
                                           the call's `model` (updatedInput),
                                           said in the context line. Jev acts:
                                           the tier is now a decision, made by
                                           Jev and visible (Joe, 2026-09-24,
                                           decision 5ec806a4: every Jev check
                                           acts).
  · none of the above, and Jev is
    unavailable or not confident       -> DENY, naming what it would have
                                           cost and how to fix it, exactly as
                                           before Jev acted. An abstaining
                                           judge never loosens the gate.

IT DENIES RATHER THAN WARNS, which is the opposite of
its neighbour rule-shape-gate.py, and the difference is deliberate. That gate
warns because blocking would make `teach` refuse a partner's own words, which
it must never do. Here the cost of a false stop is one round trip and one
extra parameter, while the cost of a warning is that it gets clicked past —
and prose that gets clicked past is precisely the failure being fixed. A gate
that only warns would be the same aspiration in a new costume. Jev acting
removes the round trip when it is confident, never the deny when it is not:
a vendor outage leaves the gate exactly as strict as it was before Jev.

FAILS OPEN on any error, like every other hook here. A gate that crashes must
never be able to stop work. Logged to out/hook-guard.log.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(os.path.expanduser("~/carr-system"))
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
# Decision c136a8e1-c135-4553-9e50-64c9640d12b7 (Joe, 2026-09-25)
# narrows 0b11c89b to judgment points. The orchestrator's 2026-10-02 PR 1407
# ruling retires C07's generated prompt-facet propagation deny in favor of
# batched boundary checks in hooks/jev-supervisor.py. Executor naming and
# exact routing-policy pin checks below remain enforced.
# Where a subagent definition may live. Project scope first: that is where the
# fifteen CARR agents are, and a project definition wins over a user-level one
# of the same name.
# Definitions that affect repository policy are versioned with the repository.
# A machine-local or synced definition would make the tier decision depend on
# ambient state, so it is deliberately not consulted by this normal hook.
AGENT_DIRS = [os.path.join(REPO, "claude-tree", "agents")]

# Forks are exempt by the tool's own contract, not by preference.
ALWAYS_INHERITS = {"fork"}

MODEL_FRONTMATTER = re.compile(r"^model\s*:\s*\S+", re.M)

# The routing policy (ops/jev_model_route.py dispatch, config ops/config/model-routes.v1.json) already chose the
# tier for a spawn whose executor line cites it, e.g. "executor: opus per routing pin merge_review". Advising a
# cheaper tier on that launch contradicts the policy on the same call, so this hook defers to it.
ROUTES_PATH = os.path.join(REPO, "ops", "config", "model-routes.v1.json")
ROUTING_LINE = re.compile(r"executor:\s*`?([\w.-]+)`?\s+per routing (?:pin ([\w-]+)|dispatch)\b", re.I)


def routing_decided(prompt, model):
    """The routing reason when the prompt's executor line cites the routing policy for this same model, else None.

    A pin counts only if it exists in the policy and its target dispatches this model, so a made-up pin name or a
    line naming a different model than the call leaves the advice in place. Any read failure means no deferral."""
    m = ROUTING_LINE.search(prompt or "")
    if not m or m.group(1).lower() != model.strip().lower():
        return None
    try:
        with open(ROUTES_PATH, encoding="utf-8") as fh:
            policy = json.load(fh)
        targets = {k: v for k, v in (policy.get("dispatch_targets") or {}).items() if isinstance(v, dict)}
        models = {str(t["subagent_model"]).lower() for t in targets.values() if t.get("subagent_model")}
        if m.group(2) is None:
            return "dispatch" if model.strip().lower() in models else None
        pin = (policy.get("pins") or {}).get(m.group(2))
        if not isinstance(pin, dict):
            return None
        pinned = (targets.get(pin.get("target")) or {}).get("subagent_model")
        return f"pin {m.group(2)}" if str(pinned).lower() == model.strip().lower() else None
    except Exception as exc:
        log(f"ROUTING(unreadable) {exc}")
        return None


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} executor-tier-gate {msg.rstrip()}\n")
    except Exception:
        pass


def definition_pins_model(subagent_type):
    """True when this subagent type has a definition file naming its own model.

    Only the frontmatter block is inspected — the first fenced `---` section —
    so the word "model:" appearing in an agent's prose body cannot be mistaken
    for a pin. Reads at most a few KB per call.
    """
    if not subagent_type:
        return False
    safe = os.path.basename(subagent_type)
    if safe != subagent_type:
        return False
    for d in AGENT_DIRS:
        path = os.path.join(d, f"{safe}.md")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except Exception:
            continue
        if not head.startswith("---"):
            continue
        end = head.find("\n---", 3)
        frontmatter = head[3:end] if end != -1 else head[3:]
        if MODEL_FRONTMATTER.search(frontmatter):
            return True
    return False


def jev_pick(desc, prompt, subagent_type, chosen):
    """Jev's cheapest-qualified tier for this spawn, or None. Never raises.

    Loop 615's fifth use (decision 62ceae36). The judgment is logged beside
    what the session chose, so the acting threshold is calibrated on real
    spawns before anything enforces on it.
    """
    try:
        # TEST HOOK ONLY (ops/executor-tier-gate-selftest.py), mirroring the
        # CARR_MIGRATE_PROD_RUN_DOOR precedent: "tier:probability" stands in for
        # Jev's answer, "none" for an unavailable judge. Never set by a real
        # session, so the real judge is always the default.
        stub = os.environ.get("CARR_EXECUTOR_TIER_JEV_STUB")
        if stub is not None:
            if stub == "none":
                return None
            tier, probability = stub.split(":")
            order = ("haiku", "sonnet", "opus", "fable")
            named = next((t for t in order if t in (chosen or "").lower()), None)
            is_cheaper = named is not None and order.index(tier) < order.index(named)
            return tier, float(probability), 0.60, is_cheaper
        # Fixture and CI runs (CARR_HOOK_FIXTURE, set by selftests and the
        # gates class) never make a live judgment: it would be nondeterministic
        # and bill every CI run. The gate then behaves exactly as before Jev.
        if os.environ.get("CARR_HOOK_FIXTURE", "").strip().lower() in ("1", "true", "yes"):
            return None
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "jev_executor_tier", os.path.join(REPO, "ops", "jev_executor_tier.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rec = module.recommend(desc, prompt, subagent_type)
        if rec is None:
            return None
        tier, probability, probabilities = rec
        try:
            judge_spec = importlib.util.spec_from_file_location(
                "jev_judge", os.path.join(REPO, "ops", "jev_judge.py"))
            judge = importlib.util.module_from_spec(judge_spec)
            judge_spec.loader.exec_module(judge)
            judge.record("executor_tier", (desc or "")[:120],
                         {"answers": {"tier": probabilities}}, chosen or None,
                         note={"pick": tier, "probability": probability,
                               "subagent_type": subagent_type})
        except Exception:
            pass
        return tier, probability, module.ACT_AT, module.cheaper(tier, chosen)
    except Exception as exc:
        log(f"JEV(unavailable) {exc}")
        return None


def advise(note):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": note,
        }
    }))
    sys.exit(0)


def allow_with_model(tool_input, tier, note):
    """Let the spawn run with `tier` set as its model: Jev's pick, acting."""
    updated = dict(tool_input)
    updated["model"] = tier
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": note,
            "updatedInput": updated,
            "additionalContext": note,
        }
    }))
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in ("Agent", "Task"):
            sys.exit(0)

        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        if not isinstance(ti, dict):
            sys.exit(0)

        model = ti.get("model")
        subagent_type = ti.get("subagent_type") or ti.get("subagentType") or ""
        desc = ti.get("description") or ""

        prompt = ti.get("prompt") or ""

        # The executor is named on the call. Jev may still think it is dearer
        # than the job needs; that is ADVICE, never a refusal, until the logged
        # judgments show the threshold can be trusted.
        if isinstance(model, str) and model.strip():
            routed = routing_decided(prompt, model)
            if routed:
                log(f"SKIP(routing {routed}) chosen={model} desc={desc[:80]}")
                sys.exit(0)
            pick = jev_pick(desc, prompt, subagent_type, model)
            if pick and pick[3] and pick[1] >= pick[2]:
                log(f"ADVISE chosen={model} jev={pick[0]}@{pick[1]:.2f} desc={desc[:80]}")
                advise(
                    f"EXECUTOR ADVICE (Jev, loop 615): this spawn names `{model}`, but Jev "
                    f"puts {pick[1]:.2f} on `{pick[0]}` being the cheapest tier that would still "
                    "do it correctly. If the task needs the dearer tier for a reason the "
                    "brief does not show, keep it and say why in the executor line; "
                    "otherwise respawn on the cheaper tier.")
            sys.exit(0)

        if subagent_type in ALWAYS_INHERITS:
            sys.exit(0)

        if definition_pins_model(subagent_type):
            sys.exit(0)

        # ACTING (Joe, 2026-09-24, decision 5ec806a4: "every jev check in the
        # system too is not a shadow"). No model was named. When Jev is
        # confident (>= ACT_AT) in a tier, the gate fills that tier in as the
        # call's model and lets the spawn run: the executor is then named, by
        # Jev, and the context line says so. When Jev abstains (unavailable,
        # erroring, or under ACT_AT) the deterministic deny below stands
        # unchanged -- an abstaining judge must never loosen the gate (the
        # first draft of this flip advised instead of denying there, which
        # would have let every spawn inherit Opus during a Jev outage).
        pick = jev_pick(desc, prompt, subagent_type, None)
        confident = bool(pick) and pick[1] >= pick[2]
        base_text = (
            "EXECUTOR NOT NAMED. This Agent call passes no `model`, and "
            f"`{subagent_type or 'the default type'}` has no model pinned in a definition file, "
            "so it will INHERIT THE PARENT TIER. On this machine the parent is pinned to Opus, "
            "which was confirmed live on 2026-08-13: a general-purpose subagent spawned without "
            "a model reported back that it was running on Opus 5. A mechanical sweep dispatched "
            "this way costs top-tier rates and nothing in the transcript says so.\n\n"
            "FIX: pass `model` explicitly on this call. Pick the CHEAPEST tier still qualified "
            "to do the job correctly:\n"
            "  haiku  — lookups, retrieval, mechanical extraction, single-file reads\n"
            "  sonnet — sweeps, code reading, research, most delegated implementation\n"
            "  opus   — judgment, verification of load-bearing findings, client-facing work\n"
            "  fable  — reserved for deep design and doctrine work; not a default\n\n"
            "If you genuinely want the parent tier, say so by passing it explicitly. The point "
            "is that the tier is a decision someone made, not one nobody noticed. Custom CARR "
            "agents that pin a model in their own frontmatter are exempt and need no parameter."
        )
        if confident:
            log(f"ALLOW(jev-picked) subagent_type={subagent_type or '(none)'} "
                f"jev={pick[0]}@{pick[1]:.2f} desc={desc[:80]}")
            allow_with_model(ti, pick[0], (
                f"EXECUTOR NAMED BY JEV: this spawn passed no `model`, so Jev picked `{pick[0]}` "
                f"at {pick[1]:.2f} (acting threshold {pick[2]:.2f}) as the cheapest tier that "
                "would still do it correctly, and the gate set it on the call. State it in the "
                f"executor line (\"executor: {pick[0]} (Jev's pick)\"). To use another tier, "
                "pass `model` explicitly; an explicit pass is never overridden."))
        jev_line = ""
        if pick:
            jev_line = (f"\n\nJEV'S PICK for this task: `{pick[0]}` at {pick[1]:.2f} "
                        "(below the acting threshold, so treat it as a hint and use the table).")
        log(f"DENY subagent_type={subagent_type or '(none)'} "
            f"jev={pick[0] if pick else '-'} desc={desc[:80]}")
        deny(base_text + jev_line)
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
