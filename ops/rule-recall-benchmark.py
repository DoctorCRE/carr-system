#!/usr/bin/env python3
"""Measure full-text availability on the unchanged 72-case test split."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def measure(standing):
    code = '''import {renderRuleBoot, ruleBootPage, paginate} from "./mcp-server/src/rule-boot.js";
let raw=""; for await (const part of process.stdin) raw+=part;
const d=JSON.parse(raw); const rows=[...d.shared_rules,...d.personal_rules];
const rendered=renderRuleBoot(rows,"joe"); const page=await ruleBootPage(rows,"joe");
console.log(JSON.stringify({...rendered,digest:page.digest,pages:paginate(rendered.text).length,
approx_tokens:page.approx_tokens,budget_tokens:page.budget_tokens,over_budget:page.over_budget}));'''
    result = subprocess.run(["node", "--input-type=module", "-e", code], input=json.dumps(standing),
                            cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
    boot = json.loads(result.stdout)
    rules = {r["id"]: r["statement"].strip() for r in standing["shared_rules"] + standing["personal_rules"]}
    delivered = {rid for rid in boot["always_on_ids"] if rules.get(rid) and rules[rid] in boot["text"]}
    path = ROOT / "ops/fixtures/rule-delivery-eval/cases.v2.json"
    fixture = path.read_bytes()
    cases = [c for c in json.loads(fixture)["cases"] if c["split"] == "test"]
    observations = [{"id": c["id"], "gold": c["gold"], "available": sorted(set(c["gold"]) & delivered),
                     "missing": sorted(set(c["gold"]) - delivered)} for c in cases]
    total = sum(len(c["gold"]) for c in cases)
    hits = sum(len(c["available"]) for c in observations)
    return {"schema": "rule-recall-benchmark/v1", "fixture_sha256": hashlib.sha256(fixture).hexdigest(),
            "cases": len(cases), "required_applications": total, "available_applications": hits,
            "recall": hits / total, "boot_rules": len(delivered), "boot_digest": boot["digest"],
            "approx_tokens": boot["approx_tokens"], "budget_tokens": boot["budget_tokens"], "pages": boot["pages"],
            "over_budget": boot["over_budget"], "observations": observations,
            "scope": "Full-text availability after verified complete boot; no obedience or deployed-adapter claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    measured = measure(json.loads(args.standing.read_text()))
    args.output.write_text(json.dumps(measured, indent=2) + "\n")
    print(json.dumps({k: v for k, v in measured.items() if k != "observations"}))
    return int(measured["recall"] != 1 or measured["over_budget"])


if __name__ == "__main__":
    raise SystemExit(main())
