#!/usr/bin/env python3
"""sync-core-rule-ids.py — regenerate mcp-server/src/core-rule-ids.js from
ops/config/rule-triage.v1.json (WR-000019 slice S11, boot diet).

WHY A GENERATED JS MODULE INSTEAD OF A RUNTIME JSON READ. doctrine.js's
standing-context verb needs to know which rule short ids the S7 triage
classified `home: "core"` (20 of them), so it can deliver those in FULL TEXT
instead of a 110-char gist -- both in the shadow-mode core_preview measurement
this slice adds, and in the enforced-mode branch that ships once slice S13
flips ops.rule_delivery_policy. doctrine.js runs inside a Cloudflare Worker,
which has no filesystem at request time and no `fs` module to read a JSON
config file with. So the id list is checked in as a plain, dependency-free JS
module -- the same "source in the repo, render on the machine/runtime" pattern
ops/config-as-code.py already uses for machine config (Joe, 2026-08-03:
"shouldnt all code be in the repo? .json is code").

THIS SCRIPT IS THE ONLY WAY THAT MODULE IS MEANT TO BE PRODUCED. Hand edits to
mcp-server/src/core-rule-ids.js will be silently overwritten the next time this
runs, and will drift from ops/boot-budget-check.py's parity read in the
meantime -- exactly the two-homes disease config-as-code.py exists to avoid.

Usage:
    ./.venv/bin/python ops/sync-core-rule-ids.py            # regenerate
    ./.venv/bin/python ops/sync-core-rule-ids.py --check    # drift check; exit 1 if stale
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIAGE_PATH = os.path.join(REPO, "ops", "config", "rule-triage.v1.json")
OUT_PATH = os.path.join(REPO, "mcp-server", "src", "core-rule-ids.js")


def render(triage):
    core_ids = sorted(
        str(r["id"]).strip().lower()
        for r in triage.get("rules", [])
        if r.get("home") == "core" and r.get("id")
    )
    ids_js = ",\n  ".join(json.dumps(i) for i in core_ids)
    lines = [
        "// GENERATED -- do not hand-edit.",
        "// Source: ops/config/rule-triage.v1.json",
        "// Regenerate with: ./.venv/bin/python ops/sync-core-rule-ids.py",
        "// Drift check (CI): ./.venv/bin/python ops/sync-core-rule-ids.py --check",
        "//",
        "// WR-000019 slice S11 (boot diet). doctrine.js's standing-context verb reads",
        "// this to know which rule short ids the S7 triage classified `home: \"core\"`",
        "// (20 rules as of that triage), so it can deliver them in FULL TEXT rather",
        "// than a gist -- both in the shadow-mode core_preview measurement and in the",
        "// enforced-mode branch. Cloudflare Workers have no filesystem at request",
        "// time, so this is a checked-in module, never a runtime JSON read.",
        "",
        "export const CORE_RULE_TRIAGE_SOURCE = \"ops/config/rule-triage.v1.json\";",
        f"export const CORE_RULE_WORK_REQUEST = {json.dumps(triage.get('work_request'))};",
        f"export const CORE_RULE_TRIAGE_SLICE = {json.dumps(triage.get('slice'))};",
        f"export const CORE_RULE_COUNT = {len(core_ids)};",
        "",
        "export const CORE_RULE_IDS = Object.freeze([",
        (f"  {ids_js},") if core_ids else "",
        "]);",
        "",
    ]
    return "\n".join(l for l in lines if l != "" or True).rstrip("\n") + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                     help="exit 1 if mcp-server/src/core-rule-ids.js is stale")
    ap.add_argument("--triage", default=TRIAGE_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args(argv)

    with open(args.triage) as fh:
        triage = json.load(fh)
    rendered = render(triage)

    current = None
    if os.path.exists(args.out):
        with open(args.out) as fh:
            current = fh.read()

    if args.check:
        if current != rendered:
            print(f"STALE: {os.path.relpath(args.out, REPO)} does not match "
                  f"{os.path.relpath(args.triage, REPO)}.")
            print("Regenerate with: ./.venv/bin/python ops/sync-core-rule-ids.py")
            return 1
        print(f"OK: {os.path.relpath(args.out, REPO)} matches "
              f"{os.path.relpath(args.triage, REPO)}.")
        return 0

    with open(args.out, "w") as fh:
        fh.write(rendered)
    print(f"wrote {os.path.relpath(args.out, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
