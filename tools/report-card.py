#!/usr/bin/env python3
"""The system report card runner — rubric v2 (DRAFT).

Loop #220. This is the code half of the rebuild Joe ordered on 2026-08-06.

WHY A RUNNER EXISTS AT ALL, rather than a prompt that tells a model to go measure
things. Rule a8c55a47: a manual path and an automated path that do the same job
must be the same code. Under v1 they were not — a human read English prose out of
the report card and re-derived numbers by hand, while the fan-out workflow read
the same prose and re-derived them a second, different way. That is how the Aug 6
run produced a working-brain figure wrong by 4x that only a human caught
afterwards. Now both paths execute THIS file against THAT spec, and the numbers
either come from a command or are honestly absent.

WHAT THIS DELIBERATELY DOES NOT DO:
  - It does not grade. Scoring is judgment and stays with the graders (the
    health-audit workflow's fan-out, whose structural no-anchoring is the one
    part of v1 that worked and is kept).
  - It does not write TO THE VAULT OR THE RECORD LAYER. It DOES write two
    scratch files under out/report-card/ (the captured health and check output).
    That distinction is stated precisely because the first version of this
    docstring said flatly "It does not write", which is false and was caught by
    a red-team seat operating under a no-write mandate: it declined to run the
    tool because the assurance could not be trusted. A false assurance in the
    one place an operator checks is the same defect class as a guard that tests
    for non-empty instead of correct (rule a9ecd5b4). Results-to-database lands
    with its own migration once the category set survives the redteam.
  - It does not re-measure what run.sh health and check.sh already measure. It
    runs each ONCE and lets metric rows read their captured output. That single
    change is what retires v1's measure agent.

TWO MODES:
  --validate   Read the spec, check every structural rule, exit non-zero on any
               breach. Runs no commands, needs no database, takes milliseconds.
               THIS IS THE CHECK FOR THE SPEC ITSELF, and it was written before
               the spec was populated (rule 43e2ef76).
  --run        Capture health + check once, execute every metric's source
               command, sample the independent commands, report. Read-only.
"""

import argparse
import os
import random
import re
import subprocess
import time
import sys
import tomllib
import shlex
import json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(REPO, "ops", "report-card", "rubric.toml")
DEFAULT_RECOVERY_VAULT = os.path.join(
    os.path.expanduser("~"), "Library", "CloudStorage",
    "GoogleDrive-joe.bookout.carr.us@gmail.com", "My Drive", "CARR AI")

# How many independent_command rows the integrity sampler re-derives per run.
# Small on purpose: the point is a spot-check that costs little enough to run
# every time, not a full second measurement pass. Scope item 4 says "a small
# random sample" and means it.
INTEGRITY_SAMPLE = 3


def load(path=SPEC):
    with open(path, "rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------------------
# VALIDATION — the structural rules the rubric must satisfy to be admissible.
#
# Each check here exists because v1 violated it and the violation cost something
# real. The comments name which, so a future reader can tell a live rail from
# defensive noise.
# ---------------------------------------------------------------------------

def validate(spec):
    errs, warns = [], []

    cats = {c["key"]: c for c in spec.get("category", [])}
    lanes = {l["key"] for l in spec.get("lane", [])}
    dims = {d["key"]: d for d in spec.get("dimension", [])}

    if not cats:
        errs.append("no categories defined")
    if not lanes:
        errs.append("no lanes defined")
    # Joe's framing, 2026-08-09: categories are scored ON dimensions. A category
    # with no declared axis is exactly the v1 failure — a bare 0-100 whose
    # meaning each grader invented for itself, which is why v1's twelve scores
    # were never comparable to one another.
    if not dims:
        errs.append("no dimensions defined — Joe's framing requires named scoring axes")

    # A category pointing at a lane that does not exist is the seam gap
    # it-support.md warns about — a second carving creeping in by typo.
    for key, c in cats.items():
        # LANES IS A LIST, 0..n — changed from a single string on 2026-08-09 by
        # the red team's taxonomy seat. A category can span seats (record_quality
        # routes to vendors, leads AND pipeline) and the old single-string field
        # forced exactly one wrong answer: it handed the record-quality defects
        # (blank owners, duplicate orgs, null last_touch) to the seat that owns
        # the repo. `lane` is a good ROUTING ADDRESS and a bad coverage partition.
        if "lane" in c:
            errs.append(f"category {key}: uses retired field 'lane' — use lanes = [...]")
        for lane in c.get("lanes", []):
            if lane not in lanes:
                errs.append(f"category {key}: lane '{lane}' is not a defined lane")
        # A category owned by no seat must still name WHO a corrective goes to.
        # "cross-cutting: no single COO seat owns it" is a comment, and the
        # flywheel cannot escalate to a comment.
        if not c.get("lanes") and not c.get("routes_to"):
            errs.append(f"category {key}: no lanes and no routes_to — a corrective "
                        f"from this category has no addressee")
        for field in ("label", "kind", "added_on"):
            if not c.get(field):
                errs.append(f"category {key}: missing required field '{field}'")
        if c.get("kind") not in ("business", "structural", "gate"):
            errs.append(f"category {key}: kind must be business|structural|gate")
        # A GATE is a precondition, not a peer of the scored categories, and it
        # must never reach the aggregator (ruled 2026-08-10, scope item 9). It
        # therefore has to declare which way it fails and what a FAIL does —
        # rule 590b11e1 applied to the gate itself, since a gate with no bound
        # action is the same undefined-response defect as an unbound metric.
        if c.get("kind") == "gate":
            if c.get("trend_carries"):
                errs.append(f"category {key}: a gate cannot carry a trend — it has "
                            f"no score to trend")
            for field in ("gate_polarity", "gate_bound_action"):
                if not c.get(field):
                    errs.append(f"category {key}: gate must declare '{field}'")
        # trend_carries is a claim about comparability and must be explicit.
        # v1's Aug 6 column silently inherited a rubric change and produced an
        # 88 -> 69 delta that reads as collapse and is not one.
        if "trend_carries" not in c:
            errs.append(f"category {key}: must state trend_carries true|false")

        # Every category declares WHICH axes it is scored on. Not all of them:
        # staleness is meaningless for Proven business outcomes, and forcing
        # every category onto every axis manufactures rows nobody believes.
        declared = c.get("dimensions", [])
        if not declared:
            errs.append(f"category {key}: declares no dimensions — nothing to score it on")
        for d in declared:
            if d not in dims:
                errs.append(f"category {key}: unknown dimension '{d}'")

    # Every lane must be covered by at least one category, or the seat has no
    # grader and nothing will ever report on it. This is the coverage rule that
    # only an inventory-first pass can enforce.
    covered = {l for c in cats.values() for l in c.get("lanes", [])}
    for lane in sorted(lanes - covered):
        errs.append(f"lane '{lane}' has no category — uncovered seat")

    # JUDGMENT ROWS GET THE SAME STRUCTURAL CHECKS AS METRICS. They were
    # validated by nothing at all until 2026-08-09, which mattered because they
    # carry the large majority of the instrument's weight — the half with no
    # command was the half with no checking.
    for j in spec.get("judgment", []):
        q = (j.get("question") or "")[:40]
        if j.get("category") not in cats:
            errs.append(f"judgment '{q}': unknown category '{j.get('category')}'")
        jd = j.get("dimension")
        if not jd:
            errs.append(f"judgment '{q}': no dimension — a grader would pick one, "
                        f"and next month's grader would pick differently")
        elif jd not in dims:
            errs.append(f"judgment '{q}': unknown dimension '{jd}'")

    seen = set()
    for m in spec.get("metric", []):
        key = m.get("key", "<unnamed>")
        if key in seen:
            errs.append(f"metric {key}: duplicate key")
        seen.add(key)

        cat_key = m.get("category")
        if cat_key not in cats:
            errs.append(f"metric {key}: unknown category '{cat_key}'")

        # A metric must say which axis it scores, and that axis must be one its
        # own category actually declares — otherwise the number lands on a
        # scoreboard row that does not exist.
        mdim = m.get("dimension")
        if not mdim:
            errs.append(f"metric {key}: no dimension — a number with no axis cannot be scored")
        elif mdim not in dims:
            errs.append(f"metric {key}: unknown dimension '{mdim}'")
        elif cat_key in cats and mdim not in cats[cat_key].get("dimensions", []):
            errs.append(f"metric {key}: dimension '{mdim}' is not declared by "
                        f"category '{cat_key}'")

        # RULE 590b11e1, the hard one. A metric with no bound action is refused
        # at build time, not flagged at read time. "A metric for which no
        # response can be defined is not collected."
        if not m.get("bound_action"):
            errs.append(f"metric {key}: no bound_action — refused by rule 590b11e1")

        # RULE a9ecd5b4's corollary: state what the number is measured FROM, so
        # a figure that keeps printing after its substrate changed is catchable.
        # This is the exact failure that made Token efficiency 45 meaningless.
        if not m.get("measured_from"):
            errs.append(f"metric {key}: no measured_from — rule a9ecd5b4 corollary")

        if not m.get("question"):
            errs.append(f"metric {key}: no question — a metric nobody can state in "
                        "English is a number without a consumer")

        if not m.get("added_on"):
            errs.append(f"metric {key}: no added_on")

        source_mode = m.get("source_mode", "both")
        if source_mode not in ("canonical", "recovery", "both"):
            errs.append(f"metric {key}: source_mode must be canonical|recovery|both")

        # An empty source_command is LEGAL and deliberate: it declares a known
        # gap out loud. It is a warning, never silence — v1 accumulated
        # unmeasurable rows precisely by staying quiet about them.
        if not m.get("source_command"):
            warns.append(f"metric {key}: no source_command — carried as a declared gap, "
                         "not collected")

        # Only rows with a second path can ever be cross-checked, so the sampler
        # has nothing to work with if too few carry one.
        if not m.get("independent_command"):
            warns.append(f"metric {key}: no independent_command — cannot be sampled by "
                         "the measurement-integrity check")

    # The integrity category is the whole reason scope item 4 exists; losing it
    # silently would be the rebuild repeating v1's mistake in one edit.
    if "measurement_integrity" not in cats:
        errs.append("no measurement_integrity category — scope item 4 requires it")
    # And it must stay a GATE. Ruled 2026-08-10 (scope item 9): scored and
    # averaged, a run with a corrupt instrument still publishes an authoritative
    # overall diluted across the other cells, which is exactly what Aug 6 did.
    # Promoting it back to a scored category would re-open that hole in one edit,
    # so the validator refuses it rather than trusting a comment to hold.
    elif cats["measurement_integrity"].get("kind") != "gate":
        errs.append("measurement_integrity must be kind='gate' — it is a precondition "
                    "for the other scores, not a peer of them (ruled 2026-08-10); "
                    "a corrupt instrument must BLOCK publication, not average into it")

    # DIMENSION COVERAGE. Which axes actually have a sourced number behind them,
    # and which are pure judgment? This is the audit's own "coverage of the
    # unwatched" pointed at itself: an axis with zero collected metrics is being
    # scored entirely on argument, which is legitimate for effectiveness and a
    # warning sign for anything meant to be measurable.
    collected = {}
    for m in spec.get("metric", []):
        if m.get("dimension") and m.get("source_command"):
            collected[m["dimension"]] = collected.get(m["dimension"], 0) + 1
    for dkey, d in dims.items():
        n = collected.get(dkey, 0)
        if n == 0 and d.get("sourced_from") != "judgment":
            warns.append(f"dimension '{dkey}' is declared {d.get('sourced_from')} but has "
                         f"0 collected metrics — currently scored on argument alone")

    # A superseded category must be tombstoned or its history becomes unfindable
    # (rule def3e84e / 7105955b).
    tombed = {t.get("label") for t in spec.get("tombstone", [])}
    for c in cats.values():
        sup = c.get("supersedes", "")
        if sup and " · " not in sup and sup not in tombed:
            warns.append(f"category {c['key']}: supersedes '{sup}' with no tombstone row")

    return errs, warns


# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------

def _grade(val, threshold):
    """Compare a metric's value against its warn/fail lines.

    UNPARSED IS A REAL VERDICT, not a shrug. A row whose command returns prose
    where a number was promised cannot be compared to anything, and the old code
    printed it as if it were fine — `doctrine_stale_sections` returned a whole
    sentence against `warn=1, fail=5` and read as healthy on every run. Saying
    UNPARSED out loud is what turns that into a fixable finding instead of a
    number nobody questions.
    """
    if not threshold:
        return "OK"
    m = re.search(r"-?\d+(?:\.\d+)?", val)
    if not m:
        return "UNPARSED"
    n = float(m.group())
    fail, warn = threshold.get("fail"), threshold.get("warn")
    # Direction is inferred as higher-is-worse because every seeded row counts
    # defects (stale rows, drifted files, disagreements). A metric where higher
    # is BETTER must say so; that field is the next thing to add here and is
    # tracked on loop #220.
    if fail is not None and n >= fail:
        return "FAIL"
    if warn is not None and n >= warn:
        return "WARN"
    return "OK"


def capture(cmd, timeout=600, env=None):
    """Run a command, return (rc, stdout). Never raises — a failing evidence
    source is a finding to report, not a crash that hides every other row."""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        # A hang is rule a9ecd5b4's limit case: no exit code exists, so an
        # exit-code check can never catch it. Name it explicitly.
        return 124, f"TIMEOUT after {timeout}s"
    except Exception as exc:                       # noqa: BLE001
        return 1, f"ERROR {exc}"


def _evidence_source(recovery, vault):
    if not recovery:
        return {"mode": "canonical", "contract": "canonical-record-control-v1",
                "vault": None}
    normalized = os.path.realpath(os.path.abspath(os.path.expanduser(str(vault))))
    return {"mode": "recovery", "contract": "drive-projection-recovery-v1",
            "vault": normalized}


def _metric_mode_matches(metric, recovery):
    wanted = metric.get("source_mode", "both")
    actual = "recovery" if recovery else "canonical"
    return wanted in ("both", actual)


def run(spec, skip_evidence=False, recovery=False, reason="", vault=None,
        evidence_dir=None):
    env = dict(os.environ)
    env.pop("CARR_VAULT", None)
    if recovery:
        env["CARR_RECOVERY_REASON"] = reason
        env["CARR_VAULT"] = str(vault)
        env["VAULT"] = str(vault)
    else:
        env.pop("VAULT", None)
    scratch = evidence_dir or os.path.join(REPO, "out", "report-card")
    os.makedirs(scratch, exist_ok=True)

    health_p = os.path.join(scratch, "health.txt")
    check_p = os.path.join(scratch, "check.txt")
    evidence_meta_p = os.path.join(scratch, "evidence-source.json")
    expected_source = _evidence_source(recovery, vault)

    if skip_evidence and os.path.exists(health_p) and os.path.exists(check_p):
        # AGE IS PRINTED AND BOUNDED. The old code reused the cache silently with
        # no timestamp, so a September run against an August capture would print
        # identical confident numbers and say nothing — a staleness instrument
        # with no staleness guard on its own evidence. 26h matches the dead-man
        # window health-check.py already uses everywhere else.
        try:
            with open(evidence_meta_p, encoding="utf-8") as fh:
                evidence_meta = json.load(fh)
        except (OSError, ValueError):
            print("evidence: REFUSED — cached capture has no trustworthy source-mode metadata")
            return 1
        if evidence_meta.get("source") != expected_source:
            print(f"evidence: REFUSED — cached source {evidence_meta.get('source')!r} "
                  f"does not exactly match required source {expected_source!r}")
            return 1
        age_h = (time.time() - os.path.getmtime(health_p)) / 3600.0
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(health_p)))
        if age_h > 26:
            print(f"evidence: REFUSED — cached capture is {age_h:.1f}h old "
                  f"(captured {stamp}, limit 26h). Re-run without --skip-evidence.")
            return 1
        print(f"evidence: reusing cache from {stamp} ({age_h:.1f}h old)")
    else:
        print("evidence: capturing run.sh health (this takes minutes) ...")
        recovery_args = (f' --recovery --reason {shlex.quote(reason)} '
                         f'--vault {shlex.quote(str(vault))}'
                         if recovery else "")
        rc, out = capture(f'"{REPO}/run.sh" health{recovery_args}', env=env)
        with open(health_p, "w") as fh:
            fh.write(out)
        print(f"  health rc={rc}, {len(out.splitlines())} lines")

        print("evidence: capturing run.sh check ...")
        rc, out = capture(f'"{REPO}/run.sh" check{recovery_args}', env=env)
        with open(check_p, "w") as fh:
            fh.write(out)
        print(f"  check  rc={rc}, {len(out.splitlines())} lines")
        with open(evidence_meta_p, "w", encoding="utf-8") as fh:
            json.dump({"source": expected_source,
                       "captured_at": time.time()}, fh, sort_keys=True)

    env["HEALTH"] = health_p
    env["CHECK"] = check_p
    env["REPO"] = REPO

    results, drifted, gaps, breaches = [], [], [], []
    for m in spec.get("metric", []):
        key, cmd = m["key"], m.get("source_command", "")
        if not cmd:
            gaps.append((key, "no source_command"))
            continue
        if not _metric_mode_matches(m, recovery):
            gaps.append((key, f"source_mode={m.get('source_mode')}; mixed semantics refused"))
            continue
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)
        val = p.stdout.strip()
        if p.returncode != 0 or not val:
            # RUBRIC DRIFT, not "unmeasurable". The distinction is the whole
            # point: v1 returned unmeasurable and nothing ever acted on it.
            drifted.append((key, p.returncode, (p.stderr or "").strip()[:120]))
            results.append((key, m, "—", p.returncode, "DRIFT"))
            continue

        # THRESHOLDS ARE NOW EVALUATED. Until 2026-08-09 they were not: the word
        # `threshold` appeared nowhere in this file outside the spec, so every
        # threshold and every bound_action was decorative prose. Four independent
        # red-team seats found that same defect, which makes it the highest-
        # confidence finding of the panel. Rule 590b11e1 was being enforced at
        # AUTHORING time (you cannot add a row without a bound_action) and
        # ignored at EXECUTION time (nothing ever fired one).
        state = _grade(val, m.get("threshold") or {})
        if state in ("WARN", "FAIL"):
            breaches.append((key, m, val, state))
        results.append((key, m, val, p.returncode, state))

    print(f"\nmetrics — {len(results)} executed, {len(gaps)} declared gaps, "
          f"{len(drifted)} drifted, {len(breaches)} over threshold")
    for key, m, val, rc, state in results:
        mark = {"OK": "  ", "WARN": "!!", "FAIL": "XX", "DRIFT": "??",
                "UNPARSED": "??"}.get(state, "  ")
        print(f"  {mark} {state:8} {key:28} {m['category']:24} {val[:44]}")
    # The bound action prints INLINE on every breach. Rule 590b11e1's audit
    # signal is the render itself: a metric with no response is visibly naked.
    for key, m, val, state in breaches:
        print(f"\n  {state} {key} = {val}")
        print(f"       → {m.get('bound_action', '(none)')[:300]}")
    for key, reason in gaps:
        print(f"  -- {key:28} UNAVAILABLE — {reason}; not collected")
    if drifted:
        print("\nRUBRIC DRIFT — these rows could not produce a value:")
        for key, rc, err in drifted:
            print(f"  {key:28} rc={rc} {err}")
        print("  A row drifting on two consecutive runs is a rubric defect, "
              "not a system defect. Fix the spec.")

    # --- measurement-layer integrity: sample and re-derive -------------------
    disagreed = 0
    sampleable = [m for m in spec.get("metric", [])
                  if m.get("independent_command") and _metric_mode_matches(m, recovery)]
    if sampleable:
        picked = random.sample(sampleable, min(INTEGRITY_SAMPLE, len(sampleable)))
        print(f"\nmeasurement integrity — re-deriving {len(picked)} of "
              f"{len(sampleable)} sampleable metrics by a second path")
        for m in picked:
            a = subprocess.run(m["source_command"], shell=True, capture_output=True,
                               text=True, env=env).stdout.strip()
            b = subprocess.run(m["independent_command"], shell=True,
                               capture_output=True, text=True, env=env).stdout.strip()
            verdict = "AGREE" if a == b else "DISAGREE"
            print(f"  {verdict:8} {m['key']:28} primary={a[:24]!r} second={b[:24]!r}")
            if verdict == "DISAGREE":
                disagreed += 1
                print("           A disagreement is the finding. Neither number is "
                      "trusted until one is proven.")
    else:
        print("\nmeasurement integrity — NO sampleable metrics. The audit cannot "
              "currently grade its own graders, which is scope item 4 unmet.")

    # A REAL EXIT CODE. `return 0` was unconditional until 2026-08-09, so every
    # metric could drift and every sample DISAGREE and the lane still reported
    # green — which meant wiring --run into any scheduled job would have produced
    # a watcher that could never fail. A red-team seat called this decisive, and
    # it is: it is the difference between a check and a decoration.
    rc = 0
    if drifted or any(s in ("FAIL", "UNPARSED") for *_, s in results) or disagreed:
        rc = 1
    print(f"\nexit {rc} — {len(drifted)} drifted · {len(breaches)} over threshold · "
          f"{disagreed} integrity disagreement(s)")
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--validate", action="store_true",
                    help="structural check of the spec; runs no commands")
    ap.add_argument("--run", action="store_true",
                    help="capture evidence and execute every metric (read-only)")
    ap.add_argument("--skip-evidence", action="store_true",
                    help="reuse cached health/check output from a previous run")
    ap.add_argument("--spec", default=SPEC)
    ap.add_argument("--recovery", action="store_true",
                    help="include legacy Drive projection evidence")
    ap.add_argument("--reason", help="required reason for recovery mode")
    ap.add_argument("--vault", help="recovery Drive root")
    ap.add_argument("--evidence-dir", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.vault and not args.recovery:
        ap.error("--vault is recovery-only; pass --recovery")
    if args.recovery:
        args.reason = (args.reason or os.environ.get("CARR_RECOVERY_REASON", "")).strip()
        if not args.reason:
            ap.error("--recovery requires a nonblank --reason")
        args.vault = args.vault or os.environ.get("CARR_VAULT") or DEFAULT_RECOVERY_VAULT
        print(f"REPORT CARD RECOVERY MODE — NONCANONICAL Drive projections — "
              f"reason: {args.reason}", file=sys.stderr)
    else:
        os.environ.pop("CARR_VAULT", None)

    spec = load(args.spec)
    meta = spec.get("meta", {})
    print(f"rubric {meta.get('version','?')} — {meta.get('status','?')}")
    print(f"spec: {args.spec}\n")

    if args.validate or not args.run:
        errs, warns = validate(spec)
        cats = spec.get("category", [])
        print(f"structure: {len(cats)} categories "
              f"({sum(1 for c in cats if c.get('kind')=='business')} business, "
              f"{sum(1 for c in cats if c.get('kind')=='structural')} structural, "
              f"{sum(1 for c in cats if c.get('kind')=='gate')} gate) · "
              f"{len(spec.get('lane',[]))} lanes · "
              f"{len(spec.get('dimension',[]))} dimensions · "
              f"{len(spec.get('metric',[]))} metrics · "
              f"{len(spec.get('judgment',[]))} judgment rows · "
              f"{len(spec.get('tombstone',[]))} tombstones")
        # A gate produces no score, so its axes must not be counted as scores.
        # Counting them would inflate the very number this rubric exists to make
        # honest, and "34 scores per run" is exactly the figure a reader quotes.
        scores = sum(len(c.get("dimensions", []))
                     for c in cats if c.get("kind") != "gate")
        print(f"scoreboard: {scores} category-by-dimension scores per run "
              f"(not {len(cats)*len(spec.get('dimension',[]))} — categories declare "
              f"only the axes that apply)")
        carried = [c["label"] for c in cats if c.get("trend_carries")]
        print(f"trend carries for {len(carried)} of {len(cats)} categories; "
              f"{len(cats)-len(carried)} start fresh baselines")
        for w in warns:
            print(f"  WARN  {w}")
        for e in errs:
            print(f"  FAIL  {e}")
        print(f"\n{'VALIDATION FAILED' if errs else 'validation OK'} — "
              f"{len(errs)} error(s), {len(warns)} warning(s)")
        if errs:
            return 1
        if not args.run:
            return 0

    return run(spec, skip_evidence=args.skip_evidence, recovery=args.recovery,
               reason=args.reason or "", vault=args.vault,
               evidence_dir=args.evidence_dir)


if __name__ == "__main__":
    sys.exit(main())
