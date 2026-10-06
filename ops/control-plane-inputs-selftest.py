#!/usr/bin/env python3
"""Acceptance checks for deterministic typed cognition-input builders."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.control_plane import validate_proposal
from lib.control_plane_inputs import (  # noqa: E402 - test asserts this module exists
    InputUnavailable,
    build_input,
    builder_registry,
)


class FixtureCollector:
    """Read-only fixture collector; its interface is the production seam."""

    def __init__(self, fixtures: dict[str, list[dict[str, object]]]):
        self.fixtures = fixtures
        self.calls: list[tuple[str, str]] = []

    def collect(self, *, builder_key: str, workflow_key: str):
        self.calls.append((builder_key, workflow_key))
        return self.fixtures.get(builder_key, [])


def contract_fixture(contract: dict[str, object]) -> dict[str, object]:
    key = contract["key"]
    if key == "research.entity-enrichment":
        return {"subjects": ["party:P-0001"], "source_policy": {"mode": "direct-only"}}
    if key in {"research.market-fuel", "research.content-fuel"}:
        return {"lanes": ["local-healthcare"], "freshness_cutoff": "2026-08-01T00:00:00Z"}
    if key == "social.batch-proposal":
        return {"platforms": ["linkedin"], "source_refs": ["record:content-fuel:1"], "voice_version": 1}
    if key == "social.engagement-proposal":
        return {"platform": "linkedin", "source_posts": ["post:123"], "voice_version": 1}
    if key == "social.metrics-proposal":
        return {"platform_exports": ["export:linkedin:2026-08-15"]}
    if key == "idea.resurface-proposal":
        return {"ideas": ["idea:42"], "last_surfaced": {"idea:42": "2026-07-01"}}
    assert key == "audit.proposal", key
    return {}


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok: {label}")


def main() -> int:
    manifest = json.loads((ROOT / "ops/config/control-plane-workflows.v1.json").read_text())
    cognition = {contract["key"]: contract for contract in manifest["cognition_jobs"]}
    cognition_workflows = [w for w in manifest["workflows"] if w["execution"]["kind"] == "cognition"]
    expected = {w["execution"]["input_builder"] for w in cognition_workflows}
    registry = builder_registry(manifest)
    check("stable input-builder registry covers every manifest key exactly", set(registry) == expected)

    fixtures: dict[str, list[dict[str, object]]] = {}
    for workflow in cognition_workflows:
        builder = workflow["execution"]["input_builder"]
        fixtures[builder] = [{
            "source_kind": "canonical_db",
            "source_ref": f"fixture:{builder}",
            "values": contract_fixture(cognition[workflow["execution"]["cognition_job"]]),
        }]
    collector = FixtureCollector(fixtures)
    for workflow in cognition_workflows:
        builder = workflow["execution"]["input_builder"]
        contract = cognition[workflow["execution"]["cognition_job"]]
        payload = build_input(manifest, builder, collector, workflow_key=workflow["key"])
        errors = validate_proposal(
            {"job_type": contract["key"], "schema_version": contract["input_schema_version"], "proposal": payload},
            contract["key"], contract["input_schema_version"], contract["input_schema"],
        )
        check(f"{builder} produces its registered typed input", not errors)
        check(f"{builder} retains evidence provenance", bool(payload.get("source_evidence")))

    one = cognition_workflows[0]
    try:
        build_input(manifest, one["execution"]["input_builder"], FixtureCollector({}), workflow_key=one["key"])
    except InputUnavailable:
        refused = True
    else:
        refused = False
    check("missing evidence refuses instead of inventing an input", refused)

    bad = FixtureCollector({one["execution"]["input_builder"]: [{
        "source_kind": "network_guess", "source_ref": "guess:1", "values": {},
    }]})
    try:
        build_input(manifest, one["execution"]["input_builder"], bad, workflow_key=one["key"])
    except InputUnavailable:
        refused = True
    else:
        refused = False
    check("unapproved provenance refuses", refused)
    # A COLLECTOR FAILURE MUST NAME ITS REASON. Until 2026-08-28 the wrapper
    # kept only the exception class, so a dead-lettered social-batch run
    # recorded "read-only collector failed: CollectorUnavailable" and the real
    # cause had to be re-derived by reading the collector by hand.
    class _Exploding:
        def collect(self, *, builder_key, workflow_key):
            raise RuntimeError("social policy requires platforms and integer voice_version")

    class _Leaky:
        def collect(self, *, builder_key, workflow_key):
            raise RuntimeError("failed reading postgresql://svc:hunter2@db.internal:5432/carr")  # ci-secret-scan: allow — synthetic fixture proving redaction; no real credential

    def _capture(collector) -> str:
        try:
            build_input(manifest, one["execution"]["input_builder"], collector, workflow_key=one["key"])
        except InputUnavailable as exc:
            return str(exc)
        return ""

    detail = _capture(_Exploding())
    check("a collector failure carries its own message",
          "social policy requires platforms and integer voice_version" in detail)
    check("a collector failure still names the exception class", "RuntimeError" in detail)
    check("a credential in a collector message is redacted", "hunter2" not in _capture(_Leaky()))
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
