#!/usr/bin/env python3
"""Write split.json: the frozen case-id -> split map, fixed before any edit.

Run once, at baseline. selftest.py fails if the working set no longer matches,
so a trigger edit cannot move a case between train and test."""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_eval  # noqa: E402


def main():
    cases = run_eval.load_cases("all")
    mapping = {c["id"]: c["split"] for c in sorted(cases, key=lambda c: c["id"])}
    payload = json.dumps(mapping, sort_keys=True).encode()
    doc = {"schema": "rule-delivery-split/v1", "sha256": hashlib.sha256(payload).hexdigest(),
           "counts": {s: sum(1 for v in mapping.values() if v == s) for s in ("train", "test")},
           "rule": ("v2 trace cases keep the repo's own split (30% held out per stratum, "
                    "seed rule-delivery-eval-v2); hard and real-replay cases split by "
                    "sha256('rule-delivery-hard-v1:'+id)[0] % 2. Tuning reads train only."),
           "split": mapping}
    with open(os.path.join(HERE, "split.json"), "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=0, sort_keys=True)
    print(doc["counts"], doc["sha256"])


if __name__ == "__main__":
    main()
