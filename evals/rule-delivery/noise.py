#!/usr/bin/env python3
"""noise.py — measure what the eval can and cannot resolve, at baseline.

The system is deterministic, so run-to-run noise is exactly zero (selftest.py
checks that two runs agree byte for byte). The remaining noise is which cases
were sampled: the interval below resamples cases with replacement. A change
counts only if the PAIRED interval of (variant - baseline) on the held-out
split excludes zero."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_eval as R  # noqa: E402


def main():
    variant = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    out = {}
    for split in ("train", "test"):
        rows = R.load_run(variant, split)
        out[split] = {name: {"value": stat(rows), "ci95": list(R.boot_ci(rows, stat))}
                      for name, stat in R.STATS.items()}
        out[split]["n_cases"] = len(rows)
    # train vs test agreement (unpaired): is the split balanced within noise?
    import random
    rng = random.Random(20260929)
    tr, te = R.load_run(variant, "train"), R.load_run(variant, "test")
    agree = {}
    for name, stat in R.STATS.items():
        draws = sorted(stat([rng.choice(te) for _ in te]) - stat([rng.choice(tr) for _ in tr])
                       for _ in range(2000))
        agree[name] = {"test_minus_train": stat(te) - stat(tr),
                       "ci95": [draws[50], draws[1949]]}
    out["train_test_agreement"] = agree
    print(json.dumps(out, indent=1, sort_keys=True))
    with open(os.path.join(HERE, "runs", variant, "noise.json"), "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
