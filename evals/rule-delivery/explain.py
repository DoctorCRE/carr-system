#!/usr/bin/env python3
"""explain.py — which trigger row or route delivered each rule (TRAIN split only).

Tuning may read train only; this refuses any other split."""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_eval as R  # noqa: E402


def why(world, case):
    """[(event kind, tool, rule id, rail, row/route key)] for every delivery."""
    out = []
    hook, rr = world.hook, world.rule_routes
    if case["prompt"].strip():
        rows = world.rtd.advise(case["prompt"], session_id=None, log_path=os.devnull,
                                rank=R._raise, ask=R._raise)
        keep = set(world.prompt_ids(case))
        text = case["prompt"]
        table = [r for r in world.rtd.prompt_rows()]
        hits = world.rtd.match(text, table, human=True)
        for rid in keep:
            srcs = [r["trigger_id"] for r in table if rid in r["rule_ids"]
                    and rid in world.rtd.match(text, [r], human=True)]
            out.append(("prompt", None, rid, "prompt_regex", ",".join(srcs) or "always_on/other"))
    for i, call in enumerate(case["tool_calls"]):
        payload = {"hook_event_name": "PreToolUse", "tool_name": call["tool_name"],
                   "tool_input": call.get("tool_input"), "session_id": "x", "tool_use_id": f"x{i}"}
        if hook._matches(payload):
            for rid in hook.scheduled_rule_ids():
                out.append(("tool", call["tool_name"], rid, "scheduled", ""))
            continue
        rows = hook.matched_triggers(payload)
        for row in rows:
            for rid in row["rule_ids"]:
                out.append(("tool", call["tool_name"], rid, "table:" + row["kind"], row["trigger_id"] + " " + row["pattern"][:60]))
        doc = hook.load_route_doc()
        verbs = rr.call_verbs(call["tool_name"], call.get("tool_input"))
        for rid, entry in doc["rules"].items():
            for route in entry["routes"]:
                if rr.route_matches(route, call["tool_name"], call.get("tool_input"), verbs):
                    out.append(("tool", call["tool_name"], rid, "route:" + route["kind"],
                                json.dumps({k: v for k, v in route.items() if k != "kind"})[:80]))
                    break
    return out


def main():
    split = "train"
    world = R.World()
    cases = [c for c in R.load_cases(split)]
    quiet = collections.Counter()
    keys = collections.Counter()
    for case in cases:
        if world.expected(case):
            continue
        gold = set(case["gold"]) | set(case["disputed"])
        for kind, tool, rid, rail, key in why(world, case):
            if rid in gold:
                continue
            quiet[(rail, key)] += 1
            keys[rid] += 1
    print("false deliveries on train should-not-fire cases, by rail/row:")
    for (rail, key), n in quiet.most_common(25):
        print(f"{n:4d}  {rail:22s} {key}")
    print("by rule:", keys.most_common(15))


if __name__ == "__main__":
    main()
