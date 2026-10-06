#!/usr/bin/env python3
"""jev-calibration-report.py — per-family, per-consequence-class Jev calibration.

Reads the judgment log (ops/jev_judge.record()), the outcome log
(ops/jev_calibration.record_outcome()) and the labeled fixtures, joins them,
and prints accuracy and reliability by entropy band and probability band on the
held-out split. A threshold is proposed only per family AND consequence class,
from validated labels on one recorded model, when the held-out split confirms
it at the target you pass. Pooled numbers are printed and always refused.

  ops/jev-calibration-report.py                                  # text
  ops/jev-calibration-report.py --target commit_warning=0.97     # propose
  ops/jev-calibration-report.py --json --family rule_binding
  ops/jev-calibration-report.py --target X=0.97 --emit-bands /tmp/candidate.json

No target is assumed. --emit-bands writes a CANDIDATE file for review and
refuses the live ops/config/jev-calibrated-bands.v1.json. Reads local files
only; it never calls Jev. Exit 2 on a bad argument or an invalid fixture.
"""

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("jev_calibration", HERE / "jev_calibration.py")
assert SPEC and SPEC.loader
cal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cal)


def parse_target(text):
    name, sep, value = text.partition("=")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError("a target is CLASS=ACCURACY, e.g. commit_warning=0.97")
    if name.strip() == cal.POOLED:
        raise argparse.ArgumentTypeError("a target names one consequence class, never '*'")
    try:
        accuracy = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not math.isfinite(accuracy) or not 0 < accuracy < 1:
        raise argparse.ArgumentTypeError("a target accuracy is strictly between 0 and 1")
    return name.strip(), accuracy


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--judgments", default=cal.JUDGMENT_LOG)
    parser.add_argument("--outcomes", default=cal.OUTCOME_LOG)
    parser.add_argument("--fixtures", default=str(cal.FIXTURE_DIR))
    parser.add_argument("--family", action="append", default=[],
                        help="report only these families (pooled cells then pool only them)")
    parser.add_argument("--target", action="append", default=[], type=parse_target,
                        help="CLASS=ACCURACY, one per consequence class to propose for")
    parser.add_argument("--bins", type=int, default=4, help="equal-count bands per axis")
    parser.add_argument("--z", type=float, default=1.96, help="Wilson interval z")
    parser.add_argument("--held-out-fraction", type=float, default=0.5,
                        help="split for judgments no fixture covers (stable hash of the subject)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--emit-bands", metavar="PATH")
    args = parser.parse_args(argv)
    if args.bins < 1 or not 0 < args.held_out_fraction < 1 or args.z <= 0:
        parser.error("--bins >= 1, 0 < --held-out-fraction < 1, --z > 0")
    if args.emit_bands and Path(args.emit_bands).resolve() == cal.LIVE_BANDS_PATH.resolve():
        parser.error("--emit-bands writes a candidate for review, never the live bands file")
    try:
        fixtures = cal.load_fixtures(args.fixtures)
    except cal.FixtureError as err:
        print(f"invalid fixture: {err}", file=sys.stderr)
        return 2
    stats = {"corrupt_lines": 0}
    judgments = cal.read_jsonl(args.judgments, stats)
    outcomes = cal.read_jsonl(args.outcomes, stats)
    joined = cal.join(judgments, outcomes, fixtures, held_out_fraction=args.held_out_fraction)
    units = joined["units"]
    if args.family:
        units = [u for u in units if u["family"] in set(args.family)]
    report = cal.report(units, targets=dict(args.target), bins=args.bins, z=args.z)
    report["skipped"] = {**joined["skipped"], **stats}
    report["sources"] = {"judgments": args.judgments, "outcomes": args.outcomes,
                         "fixtures": {f: d["version"] for f, d in sorted(fixtures.items())}}
    if args.emit_bands:
        Path(args.emit_bands).write_text(json.dumps(cal.bands_from_report(report), indent=2,
                                                    sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(cal.format_report(report))
        print(f"\nskipped: {report['skipped']}")
    return 0


sys.exit(main(sys.argv[1:]))
