#!/usr/bin/env python3
"""Compare all recorded grades with the scorer pinned by review round 1.

Offline reproduction from the repository root:
  git show ab936364be41616ae7d5a59aadb3fd68445e90eb:evals/session-instructions/score.py > /private/tmp/session-score-before.py
  python3 evals/session-instructions/rescore_review.py /private/tmp/session-score-before.py
"""

import hashlib
import json
import sys
from pathlib import Path

import score

HERE = Path(__file__).resolve().parent
REVIEWED_HEAD = "ab936364be41616ae7d5a59aadb3fd68445e90eb"
REVIEWED_SCORER_SHA256 = "a58a15afdc45e8d049dc71f0fe1043ec5bbfa21acf954e2f3c4c6ecc6f1dd8ec"


def compare(previous_source):
    if hashlib.sha256(previous_source).hexdigest() != REVIEWED_SCORER_SHA256:
        raise ValueError("previous scorer does not match the reviewed head")
    previous = {"__name__": "reviewed_score"}
    exec(compile(previous_source, "reviewed_score.py", "exec"), previous)
    cases = json.loads((HERE / "expectations.v2.json").read_text())["cases"]
    counts, flips = {}, []
    for arm in ("baseline", "candidate"):
        rows = [json.loads(line) for line in (HERE / "evidence" / f"{arm}.v2.jsonl").read_text().splitlines()]
        counts[arm] = {"runs": 0, "previous_passes": 0, "current_passes": 0,
                       "true_to_false": 0, "false_to_true": 0}
        for row in rows:
            cid = row["case_id"]
            for repeat, run in enumerate(row["runs"]):
                before = previous["passes"](cases[cid]["kind"], run)
                after = score.passes(cases[cid]["kind"], run)
                counts[arm]["runs"] += 1
                counts[arm]["previous_passes"] += int(before)
                counts[arm]["current_passes"] += int(after)
                if before != after:
                    counts[arm]["true_to_false" if before else "false_to_true"] += 1
                    flips.append({"arm": arm, "case_id": cid, "repeat": repeat,
                                  "split": row["split"], "before": before, "after": after})
    return {"reviewed_head": REVIEWED_HEAD, "previous_scorer_sha256": REVIEWED_SCORER_SHA256,
            "current_scorer_sha256": hashlib.sha256((HERE / "score.py").read_bytes()).hexdigest(),
            "recorded_runs": sum(c["runs"] for c in counts.values()), "flipped_runs": len(flips),
            "counts": counts, "flips": flips,
            "limitation": "Closed stop grammar rejects unrecognized prose conservatively. A changed grade does not prove the historical answer continued work. No fresh measurement; receipt remains inconclusive and unsealed."}


if __name__ == "__main__":
    print(json.dumps(compare(Path(sys.argv[1]).read_bytes()), indent=2))
