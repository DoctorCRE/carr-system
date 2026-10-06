#!/usr/bin/env python3
"""Repin the synthetic compiler fixture after a generated dependency lock update."""
import argparse
import copy
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools/room-bridge"))
import assurance_slice_compiler as compiler
import execution_contract

FIXTURE = pathlib.Path("control-room/contracts/fixtures/execution-fabric/assurance-compiler.valid.v1.json")


def repair(root, write=False):
    target = root / FIXTURE
    value = json.loads(target.read_text())
    updated = copy.deepcopy(value)
    contract = updated["assurance_slice"]
    for required in contract["required_tests"]:
        binding = required["environment"]["dependency_lock"]
        if binding["path"] != "requirements.lock":
            raise ValueError("unexpected dependency lock in synthetic compiler fixture")
        binding["digest"] = "sha256:" + hashlib.sha256((root / binding["path"]).read_bytes()).hexdigest()
    changed = contract != value["assurance_slice"]
    if changed:
        preimage = {key: item for key, item in contract.items()
                    if key not in {"contract_digest", "ownership_contract_digest", "lease_binding"}}
        contract["ownership_contract_digest"] = compiler.compile_ownership_contract_digest(preimage)
        contract = compiler._normalized_contract(contract)
        contract["contract_digest"] = execution_contract.canonical_digest(
            {key: item for key, item in contract.items() if key != "contract_digest"})
        updated["assurance_slice"] = contract
    result = compiler.compile_assurance_slice(updated)
    if not result["ok"]:
        raise ValueError(json.dumps(result["refusal"], sort_keys=True))
    if changed and write:
        target.write_text(json.dumps(updated, indent=2) + "\n")
        readback = compiler.compile_assurance_slice(json.loads(target.read_text()))
        if readback != result:
            raise ValueError("compiler fixture write readback disagrees")
    return {"changed": changed, "written": changed and write, "compiler_ok": True,
            "manifest_hash": result["manifest"]["manifest_hash"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        result = repair(args.repo.resolve(), args.write)
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["changed"] and not args.write else 0


if __name__ == "__main__":
    raise SystemExit(main())
