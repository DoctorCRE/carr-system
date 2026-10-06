#!/usr/bin/env python3
"""Pure contract tests for migrating the active enforcement map to admission."""
from __future__ import annotations
import sys
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO))


def main() -> int:
    from lib.rule_admission import BACKFILL_REASON, admission_contract, backfill_owns_row
    failures=[]
    ran=[]
    def check(name,condition):
        ran.append(name)
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition: failures.append(name)
    catalog={"gate":{"implementation":["hooks/gate.py"],"test":["ops/gate-test.py"],
                      "failure_mode":"deny"}}
    built=admission_contract("aaaaaaaa","shared",{
        "category":"hard_pre_action","enforcement_class":"deny_gate",
        "binding_moment":"before write","control":"gate","exceptions":"none"},catalog)
    check("built machine control is admitted",built["state"]=="admitted" and built["enforcement_class"]=="machine_enforceable")
    check("built control carries implementation and fixture evidence",
          built["enforcement_points"][0]["implementation_ref"]=="hooks/gate.py"
          and built["fixture_refs"]==["ops/gate-test.py"])
    ambient=admission_contract("bbbbbbbb","joe",{
        "category":"judgment_advisory","enforcement_class":"judgment_ambient",
        "why_unenforceable":"requires contextual judgment"},catalog)
    check("genuine judgment is explicitly admitted without fake code",
          ambient["state"]=="admitted" and ambient["enforcement_class"]=="judgment_advisory"
          and ambient["enforcement_points"]==[])
    unbuilt=admission_contract("cccccccc","shared",{
        "category":"judgment_advisory","enforcement_class":"unbuilt",
        "planned_control":"build it"},catalog)
    check("unbuilt machine promise remains visibly needs_revision",
          unbuilt["state"]=="needs_revision" and unbuilt["enforcement_points"]==[])
    check("all four D-04 dimensions are explicit",
          all(built[k] for k in ("applicability","projection","reachability","enforcement_class")))
    reviewed=json.loads((REPO/"ops/config/rule-enforcement-map.json").read_text(encoding="utf-8"))
    cognition=admission_contract("5e89c211","shared",
                                 reviewed["rule_controls"]["5e89c211"],
                                 reviewed["control_catalog"])
    point=cognition["enforcement_points"]
    check("cognition-token rule backfills as machine-enforceable",
          cognition["state"]=="admitted" and cognition["enforcement_class"]=="machine_enforceable"
          and cognition["binding_moment"]=="before a cognition workflow is registered or dispatched")
    check("cognition-token rule carries installed manifest and database fixtures",
          cognition["fixture_refs"]==["ops/control-plane-db-gate.py", "ops/control-plane-selftest.py"]
          and len(point)==1 and point[0]["control_key"]=="control-plane-workflow-manifest"
          and point[0]["enforcement_class"]=="deny_gate")
    check("cognition-token admission contract matches actual typed dispatcher boundary",
          cognition["input_contract"]["required"]==[
              "execution", "input_schema_version", "output_schema_version", "budget",
              "canonical_write_authority"]
          and cognition["projection"]["targets"]==["workflow-manifest", "typed-broker"])
    # WHO OWNS THE ROW. Added 2026-08-23 after the first real run against
    # Production stopped on migration 0228's immutability trigger: the backfill
    # tried to rewrite the contract of a rule Joe had already approved.
    check("a rule with no contract yet is the backfill's to write",
          backfill_owns_row(None,False) is True)
    check("a row the backfill itself wrote stays the backfill's, so re-runs are idempotent",
          backfill_owns_row(BACKFILL_REASON,False) is True)
    check("an approved rule is never rewritten, whoever wrote its contract",
          backfill_owns_row(None,True) is False
          and backfill_owns_row(BACKFILL_REASON,True) is False)
    check("a contract admitted by hand through admit-rule is never overwritten",
          backfill_owns_row("Joe's explicit approval order, given live this session",False) is False)
    print(f"\nrule-admission-backfill-selftest: {len(ran)-len(failures)}/{len(ran)} passed")
    return 1 if failures else 0


if __name__=="__main__": raise SystemExit(main())
