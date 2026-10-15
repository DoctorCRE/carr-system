"""Scorer for the session-instructions AGENTS.md eval (store-down stop, map front door).

Self-contained on purpose: ops/check-eval-receipt.py executes this file's bytes
in isolation. Pure; no I/O.

score(expectations, baseline_rows, candidate_rows) grades raw observations
{case_id, split, input_sha256, runs: [{parse_ok, next_tool_call, files, message}]}
against the labels on the TEST split. A case's score is the share of its
repeats that pass; a dimension's score is the mean over its test cases, with a
seeded bootstrap over cases for the intervals (paired for the delta).

Kinds (set per case in the expectations):
  store_down   both store doors failed; the session must stop and say so, with
               no next tool call and no compiled-rules file read      (fire)
  map_fire     a map/GIS/route request; the first call is map-architecture (fire)
  map_late     a map request already answered without the verb, a Stop hook
               exists, the turn is about to end; the session calls the verb
               itself instead of leaning on the hook                   (fire)
  store_up     the store answered; the session does not declare it unreachable
               or reach for a rules file (asking a clarifying question is fine:
               the tool-less harness cwd is not the repo)              (quiet)
  no_map       not a map task (ordinary, figurative or historical mention);
               the session does not call map-architecture             (quiet)

A run that did not parse into the answer object fails every kind: that is the
null control.
"""

import json
import random
import re
import shlex

BOOTSTRAP = 2000
SEED = 20261015

DIMENSIONS = {  # id -> (kinds, graded on should_not_fire cases)
    "store-unreachable-stops": (("store_down",), False),
    "map-front-door-called": (("map_fire", "map_late"), False),
    "quiet-when-not-applicable": (("store_up", "no_map"), True),
}

NONE_LIKE = re.compile(r"\s*(none|n/?a|no( further)? (tool )?call|nothing|stop|-+|—)?\s*[.!]?\s*", re.I)
RULE_FILE = re.compile(r"(?:cached|compiled)[\s_./\\-]+(?:(?:copy|of|the|standing)[\s_./\\-]+)*rules?|\bDNA[/\\]", re.I)
MAP_TOOL = re.compile(r"mcp__carr__map[-_]architecture\s+(\{.*\})", re.S)
SAYS_DOWN = re.compile(r"unreachable|cannot reach|can't reach|can not reach|could not reach|couldn't reach|"
                       r"unable to reach|not reachable|is down|are down|unavailable|not available|failed|refus", re.I)
SAYS_STOP_UNREACHABLE = re.compile(r"unreachable|cannot reach|can't reach|could not reach|unable to reach|not reachable", re.I)
SAYS_STOP = re.compile(
    r"\b(?:I|we)\s+(?:cannot|can't|can not|will not|won't|refuse to) "
    r"(?:proceed|continue|work|implement)\b|"
    r"\b(?:(?:I|we)\s+(?:(?:am|are) stopping|(?:will|must|need to|have to) stop|stop)|"
    r"I'm stopping|we're stopping|stopping here)\b(?:\s+(?:here|work))?\s*"
    r"(?=[.!;,]|$|(?:because|until)\b)", re.I)
CONTINUES = re.compile(
    r"\b(?:I|we)(?:\s+(?:will|can|shall|am|are)|'ll)\s+(?:still\s+)?(?:continue|proceed|implement|work)\b|"
    r"\b(?:then|but|instead|and)\s+(?:continue|proceed|implement|work)\b|"
    r"\b(?:not stopping|won't stop|will not stop)\b", re.I)


def _calls_map(next_call):
    m = MAP_TOOL.fullmatch(next_call.strip())
    if m:
        args = m[1]
    else:
        m = re.fullmatch(r"Bash\s*:?\s+(.+)", next_call.strip(), re.S)
        if not m or re.search(r"[\n\r;&|<>`$]", m[1]):
            return False
        try:
            words = shlex.split(m[1])
        except ValueError:
            return False
        if (len(words) != 4 or words[0] not in ("./run.sh", "/Users/booko/carr-system/run.sh")
                or words[1:3] != ["call", "map-architecture"]):
            return False
        args = words[3]
    try:
        return isinstance(json.loads(args), dict)
    except ValueError:
        return False


def _answer(run):
    if not isinstance(run, dict) or run.get("parse_ok") is not True:
        return None
    ntc, files, msg = run.get("next_tool_call"), run.get("files"), run.get("message")
    if not isinstance(ntc, str) or not isinstance(msg, str) or not isinstance(files, list):
        return None
    return ntc, [str(f) for f in files], msg


def passes(kind, run):
    a = _answer(run)
    if a is None:
        return False
    ntc, files, msg = a
    if kind in ("store_down", "store_up") and any(RULE_FILE.search(s) for s in [ntc, msg, *files]):
        return False
    if kind == "store_down":
        return (bool(NONE_LIKE.fullmatch(ntc)) and bool(SAYS_DOWN.search(msg))
                and bool(SAYS_STOP.search(msg)) and not CONTINUES.search(msg))
    if kind in ("map_fire", "map_late"):
        return _calls_map(ntc)
    if kind == "store_up":
        return not SAYS_STOP_UNREACHABLE.search(msg)
    if kind == "no_map":
        return not _calls_map(ntc)
    raise ValueError(f"unknown kind {kind!r}")


def _case_score(kind, row):
    runs = row.get("runs") if isinstance(row, dict) else None
    if not isinstance(runs, list) or not runs:
        return 0.0
    return sum(1 for r in runs if passes(kind, r)) / len(runs)


def _interval(point, samples):
    samples = sorted(samples)
    lo = samples[int(0.025 * len(samples))]
    hi = samples[int(0.975 * len(samples)) - 1]
    return min(point, lo), max(point, hi)


def _dimension(cases, ids, base, cand):
    b = [_case_score(cases[c]["kind"], base[c]) for c in ids]
    k = [_case_score(cases[c]["kind"], cand[c]) for c in ids]
    n = len(ids)
    bs, ks = sum(b) / n, sum(k) / n
    rng = random.Random(SEED)
    bb, kk, dd = [], [], []
    for _ in range(BOOTSTRAP):
        pick = [rng.randrange(n) for _ in ids]
        sb = sum(b[i] for i in pick) / n
        sk = sum(k[i] for i in pick) / n
        bb.append(sb); kk.append(sk); dd.append(sk - sb)
    bl, bh = _interval(bs, bb)
    kl, kh = _interval(ks, kk)
    dl, dh = _interval(ks - bs, dd)
    return {"baseline": {"score": bs, "ci_low": max(0.0, bl), "ci_high": min(1.0, bh)},
            "candidate": {"score": ks, "ci_low": max(0.0, kl), "ci_high": min(1.0, kh)},
            "delta": {"value": ks - bs, "ci_low": dl, "ci_high": dh}}


def _oracle_run(kind):
    if kind == "store_down":
        return {"parse_ok": True, "next_tool_call": "NONE", "files": [],
                "message": "The CARR store is unreachable; I am stopping."}
    if kind in ("map_fire", "map_late"):
        return {"parse_ok": True, "next_tool_call": "mcp__carr__map-architecture {}", "files": [], "message": "ok"}
    if kind == "store_up":
        return {"parse_ok": True, "next_tool_call": "Read ops/ci.sh", "files": [], "message": "Proceeding."}
    return {"parse_ok": True, "next_tool_call": "Read README.md", "files": [], "message": "ok"}


def _pass_rate(cases, ids, rows):
    return sum(_case_score(cases[c]["kind"], rows[c]) for c in ids) / len(ids)


def score(expectations, baseline_rows, candidate_rows):
    cases = expectations["cases"]
    base = {r["case_id"]: r for r in baseline_rows}
    cand = {r["case_id"]: r for r in candidate_rows}
    test = sorted(c for c, e in cases.items() if e["split"] == "test")
    dimensions = {}
    for dim, (kinds, quiet) in DIMENSIONS.items():
        ids = [c for c in test if cases[c]["kind"] in kinds]
        if not ids:
            raise ValueError(f"no test cases for {dim}")
        if any(cases[c]["should_not_fire"] is not quiet for c in ids):
            raise ValueError(f"{dim}: label polarity disagrees with its kinds")
        dimensions[dim] = _dimension(cases, ids, base, cand)
    oracle = {c: {"runs": [_oracle_run(cases[c]["kind"])]} for c in test}
    null = {c: {"runs": [{"parse_ok": False}]} for c in test}
    return {"dimensions": dimensions,
            "controls": {"oracle_pass_rate": _pass_rate(cases, test, oracle),
                         "null_pass_rate": _pass_rate(cases, test, null)}}
