"""Scorer for the jev-judgments paid-call admission eval. Pure; no I/O.

score(expectations, baseline_rows, candidate_rows) grades raw observations
({case_id, split, input_sha256, paid}) against the labels on the TEST split:

  wasted-call-refusal       should_not_fire cases that made no paid call
  judgment-point-retention  should-fire cases that still made their paid call

Each is a pass rate with a Wilson interval; the paired delta interval comes
from a fixed-seed bootstrap over cases, so the scorer is deterministic and a
re-run of the checker reproduces the receipt exactly. A row whose `paid` is not
a boolean fails both dimensions (that is the null control).
"""

import math
import random

DIMENSIONS = {
    "wasted-call-refusal": True,       # graded on should_not_fire cases
    "judgment-point-retention": False,  # graded on should-fire cases
}
BOOTSTRAP = 2000
SEED = 20261004


def _passes(expect, row):
    paid = row.get("paid") if isinstance(row, dict) else None
    if not isinstance(paid, bool):
        return False
    return (not paid) if expect["should_not_fire"] else paid


def _wilson(k, n):
    if n == 0:
        return 0.0, 0.0, 0.0
    z = 1.959963984540054
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    # Clamp so the interval always contains its own point estimate; at k == n
    # the closed form lands a float hair under 1.0.
    return p, min(p, max(0.0, centre - half)), max(p, min(1.0, centre + half))


def _dimension(cases, ids, base, cand):
    b = [1 if _passes(cases[c], base.get(c)) else 0 for c in ids]
    k = [1 if _passes(cases[c], cand.get(c)) else 0 for c in ids]
    bs, bl, bh = _wilson(sum(b), len(b))
    ks, kl, kh = _wilson(sum(k), len(k))
    rng = random.Random(SEED)
    deltas = []
    for _ in range(BOOTSTRAP):
        pick = [rng.randrange(len(ids)) for _ in ids]
        deltas.append((sum(k[i] for i in pick) - sum(b[i] for i in pick)) / len(ids))
    deltas.sort()
    lo, hi = deltas[int(0.025 * BOOTSTRAP)], deltas[int(0.975 * BOOTSTRAP) - 1]
    return {"baseline": {"score": bs, "ci_low": bl, "ci_high": bh},
            "candidate": {"score": ks, "ci_low": kl, "ci_high": kh},
            "delta": {"value": ks - bs, "ci_low": lo, "ci_high": hi}}


def _rate(cases, ids, rows):
    return sum(1 for c in ids if _passes(cases[c], rows.get(c))) / len(ids) if ids else 0.0


def score(expectations, baseline_rows, candidate_rows):
    cases = expectations["cases"]
    base = {r["case_id"]: r for r in baseline_rows}
    cand = {r["case_id"]: r for r in candidate_rows}
    test = sorted(c for c, e in cases.items() if e["split"] == "test")
    dimensions = {}
    for dim, on_should_not_fire in DIMENSIONS.items():
        ids = [c for c in test if cases[c]["should_not_fire"] is on_should_not_fire]
        if not ids:
            raise ValueError(f"no test cases for {dim}")
        dimensions[dim] = _dimension(cases, ids, base, cand)
    oracle = {c: {"paid": not cases[c]["should_not_fire"]} for c in test}
    null = {c: {"paid": None} for c in test}
    return {"dimensions": dimensions,
            "controls": {"oracle_pass_rate": _rate(cases, test, oracle),
                         "null_pass_rate": _rate(cases, test, null)}}
