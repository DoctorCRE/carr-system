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
    out = [('prompt', None, rid, 'scope_lookup', 'frozen moment judgment')
           for rid in world.prompt_ids(case)]
    for i, call in enumerate(case['tool_calls']):
        out.extend(('tool', call['tool_name'], rid, 'scope_lookup', 'frozen moment judgment')
                   for rid in world.tool_ids(case, i, call))
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
