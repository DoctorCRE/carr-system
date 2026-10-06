"""Evidence-only builders for typed cognition-job inputs.

The control plane invokes a builder before it can spend a cognition token.  A
builder may read canonical records, a command explicitly designated read-only,
or device evidence through the supplied collector.  It never calls a model,
performs I/O itself, or writes canonical state.  Missing, malformed, or
untraceable evidence is a refusal rather than a reason to make an input up.
"""
from __future__ import annotations

from lib.secret_redaction import redact_text

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from lib.control_plane import validate_proposal


class InputUnavailable(RuntimeError):
    """The named builder cannot make a truthful typed input from its evidence."""

    def __init__(self, builder_key: str, reason: str):
        self.builder_key = builder_key
        self.reason = reason
        super().__init__(f"input unavailable for {builder_key}: {reason}")


class NoEligibleRecords(RuntimeError):
    """The named builder's canonical queue is genuinely empty right now.

    Distinct from InputUnavailable: this is not malformed, missing, or
    contradictory evidence -- it is the queue's honest "no eligible records"
    state. contact-enrichment-weekly.SKILL.md and
    deal-history-research-weekly.SKILL.md both name this as the normal end
    state once the book is covered ("reports done and stops finding work"),
    not a failure. A caller must route this to a clean skip, the same
    terminal outcome EntrypointNotConfigured gets, never to ops.fail_job or
    a dead letter (rule 88e9b5eb: "not authorized" and "not possible" are
    different findings and must never be reported as the same one -- this is
    a third, equally distinct finding: "not needed right now").
    """

    def __init__(self, builder_key: str):
        self.builder_key = builder_key
        super().__init__(f"no eligible records for {builder_key}: queue is empty")


class EvidenceCollector(Protocol):
    """The only I/O seam used by input builders.

    Implementations must return evidence envelopes from a canonical database,
    an explicitly read-only command, or a device.  A collector has no mutation
    method by design, which keeps proposal-input construction separate from
    canonical writes.
    """

    def collect(self, *, builder_key: str, workflow_key: str) -> Iterable[Mapping[str, Any]]: ...


SOURCE_KINDS = frozenset({"canonical_db", "read_only_command", "device", "versioned_policy"})

# This deliberately enumerates the distinct manifest keys rather than deriving
# a catch-all route.  A new scheduled cognition path therefore fails closed
# until its evidence contract is reviewed and registered here.
AUDIT_BUILDERS = frozenset({
    "capability-program.next-candidate",
    "cc-release-diff",
    "system-health.monthly-evidence",
    "loops.next-actionable",
    "doctrine.review-due",
    "system.prune-candidates",
})
ENTITY_BUILDERS = frozenset({
    "entity-enrichment.next-40",
    "deal-history.next-slice",
})
MARKET_BUILDERS = frozenset({
    "content-fuel.next-rotation",
    "npi.weekly-delta",
    "radar.weekly-candidates",
})
BUILDER_CONTRACTS: dict[str, tuple[str, tuple[str, ...]]] = {
    **{key: ("audit.proposal", ()) for key in AUDIT_BUILDERS},
    **{key: ("research.entity-enrichment", ("subjects", "source_policy"))
       for key in ENTITY_BUILDERS},
    "content-fuel.next-rotation": ("research.content-fuel", ("lanes", "freshness_cutoff")),
    **{key: ("research.market-fuel", ("lanes", "freshness_cutoff"))
       for key in MARKET_BUILDERS if key != "content-fuel.next-rotation"},
    "idea-bank.oldest-unsurfaced": ("idea.resurface-proposal", ("ideas", "last_surfaced")),
    "linkedin.source-posts": ("social.engagement-proposal", ("platform", "source_posts", "voice_version")),
    "x.source-posts": ("social.engagement-proposal", ("platform", "source_posts", "voice_version")),
    "social.next-week-brief": ("social.batch-proposal", ("platforms", "source_refs", "voice_version")),
    "social.metrics-exports": ("social.metrics-proposal", ("platform_exports",)),
}


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _normalize_evidence(builder_key: str, raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, Mapping):
        raise InputUnavailable(builder_key, "collector returned a non-object evidence envelope")
    source_kind = raw.get("source_kind")
    source_ref = raw.get("source_ref")
    values = raw.get("values")
    if source_kind not in SOURCE_KINDS:
        raise InputUnavailable(
            builder_key,
            "evidence source_kind is not canonical_db, read_only_command, device, or versioned_policy",
        )
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise InputUnavailable(builder_key, "evidence lacks a non-empty source_ref")
    if not isinstance(values, Mapping):
        raise InputUnavailable(builder_key, "evidence values must be an object")
    return ({"source_kind": source_kind, "source_ref": source_ref}, dict(values))


def _merge_value(builder_key: str, field: str, current: Any, incoming: Any) -> Any:
    """Merge only losslessly; a contradictory record is not a model problem."""
    if current is _MISSING:
        return incoming
    if isinstance(current, list) and isinstance(incoming, list):
        return [*current, *incoming]
    if isinstance(current, dict) and isinstance(incoming, Mapping):
        merged = dict(current)
        for key, value in incoming.items():
            if key in merged and merged[key] != value:
                raise InputUnavailable(builder_key, f"conflicting evidence for {field}.{key}")
            merged[key] = value
        return merged
    if current != incoming:
        raise InputUnavailable(builder_key, f"conflicting evidence for {field}")
    return current


_MISSING = object()


def _registered_workflow(manifest: Mapping[str, Any], builder_key: str, workflow_key: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    workflows = manifest.get("workflows")
    contracts = manifest.get("cognition_jobs")
    if not isinstance(workflows, list) or not isinstance(contracts, list):
        raise InputUnavailable(builder_key, "manifest has no workflow/cognition registry")
    selected = [workflow for workflow in workflows
                if isinstance(workflow, dict)
                and workflow.get("execution", {}).get("kind") == "cognition"
                and workflow.get("execution", {}).get("input_builder") == builder_key]
    if workflow_key is not None:
        selected = [workflow for workflow in selected if workflow.get("key") == workflow_key]
    if len(selected) != 1:
        raise InputUnavailable(builder_key, "builder must resolve to exactly one registered cognition workflow")
    workflow = selected[0]
    cognition_key = workflow["execution"].get("cognition_job")
    matching = [contract for contract in contracts
                if isinstance(contract, dict) and contract.get("key") == cognition_key]
    if len(matching) != 1:
        raise InputUnavailable(builder_key, "workflow has no unique registered cognition contract")
    expected_contract = BUILDER_CONTRACTS.get(builder_key)
    if expected_contract is None or expected_contract[0] != cognition_key:
        raise InputUnavailable(builder_key, "builder contract is unregistered or mismatched")
    return workflow, matching[0]


def builder_registry(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Return the exact reviewed builder-to-cognition-contract registry.

    This fails closed when a manifest adds, removes, or retypes an input
    builder, rather than silently reusing a generic model-facing payload.
    """
    workflows = manifest.get("workflows")
    if not isinstance(workflows, list):
        raise InputUnavailable("registry", "manifest.workflows must be an array")
    observed: dict[str, str] = {}
    for workflow in workflows:
        if not isinstance(workflow, Mapping):
            continue
        execution = workflow.get("execution")
        if not isinstance(execution, Mapping) or execution.get("kind") != "cognition":
            continue
        builder_key = execution.get("input_builder")
        cognition_key = execution.get("cognition_job")
        if not isinstance(builder_key, str) or not isinstance(cognition_key, str):
            raise InputUnavailable("registry", "cognition workflow lacks input_builder or cognition_job")
        if builder_key in observed:
            raise InputUnavailable("registry", f"input builder is shared by multiple workflows: {builder_key}")
        observed[builder_key] = cognition_key
    if set(observed) != set(BUILDER_CONTRACTS):
        missing = sorted(set(observed) - set(BUILDER_CONTRACTS))
        stale = sorted(set(BUILDER_CONTRACTS) - set(observed))
        raise InputUnavailable("registry", f"unreviewed builders={missing}; stale builders={stale}")
    for builder_key, cognition_key in observed.items():
        if BUILDER_CONTRACTS[builder_key][0] != cognition_key:
            raise InputUnavailable("registry", f"{builder_key} has unexpected cognition contract {cognition_key}")
    return dict(observed)


def build_input(manifest: Mapping[str, Any], builder_key: str, collector: EvidenceCollector,
                *, workflow_key: str | None = None) -> dict[str, Any]:
    """Build one valid cognition input only from provenance-carrying evidence.

    The returned ``source_evidence`` is intentionally retained for every job
    type, including schemas that do not require it, so a later proposal can be
    traced without another collection pass.  It adds no authority.
    """
    registry = builder_registry(manifest)
    if builder_key not in registry:
        raise InputUnavailable(builder_key, "not in the exact registered builder set")
    workflow, contract = _registered_workflow(manifest, builder_key, workflow_key)
    try:
        raw_evidence = list(collector.collect(builder_key=builder_key, workflow_key=workflow["key"]))
    except (InputUnavailable, NoEligibleRecords):
        raise
    except Exception as exc:
        # NAME THE REASON, NOT JUST THE EXCEPTION CLASS. Until 2026-08-28 this
        # recorded only the type, so a dead-lettered social-batch run said
        # "read-only collector failed: CollectorUnavailable" and a session had
        # to re-derive the cause by reading the collector by hand. The
        # collectors already raise messages that say exactly which query or
        # policy was wrong; carrying that message costs nothing and is the same
        # lesson the entrypoint failure receipts learned a day earlier. Bounded
        # and redacted, because a collector message can quote a row it read.
        detail = redact_text(str(exc))[:500]
        raise InputUnavailable(
            builder_key,
            f"read-only collector failed: {type(exc).__name__}" + (f": {detail}" if detail else ""),
        ) from exc
    if not raw_evidence:
        raise InputUnavailable(builder_key, "no canonical/read-only/device evidence is available")

    provenance: list[dict[str, Any]] = []
    merged: dict[str, Any] = {}
    for raw in raw_evidence:
        trace, values = _normalize_evidence(builder_key, raw)
        provenance.append(trace)
        for field, value in values.items():
            if not _nonempty(value):
                continue
            merged[field] = _merge_value(builder_key, field, merged.get(field, _MISSING), value)

    _, required_fields = BUILDER_CONTRACTS[builder_key]
    payload: dict[str, Any]
    if registry[builder_key] == "audit.proposal":
        # The workflow is a registered definition, while every proposed claim
        # must travel with at least one actual source reference.
        # Audit inputs retain their typed canonical values.  Provenance alone
        # cannot support a policy predicate: the fact collector needs the
        # exact reviewed values (and still refuses an absent field).
        payload = {"workflow": workflow["key"], "evidence_refs": [item["source_ref"] for item in provenance],
                   "facts": merged}
    else:
        missing = [field for field in required_fields if field not in merged or not _nonempty(merged[field])]
        if missing:
            raise InputUnavailable(builder_key, f"evidence is missing required input fields: {', '.join(missing)}")
        # Preserve every non-conflicting canonical value, not only the minimum
        # schema fields.  Workflow predicates consume named policy fields from
        # this typed input; reducing it to provenance would make the runtime
        # delegate facts back to an external caller.
        payload = dict(merged)
    payload["source_evidence"] = provenance

    errors = validate_proposal(
        {"job_type": contract["key"], "schema_version": contract["input_schema_version"], "proposal": payload},
        contract["key"], contract["input_schema_version"], contract["input_schema"],
    )
    if errors:
        raise InputUnavailable(builder_key, "registered schema rejected evidence payload: " + "; ".join(errors))
    return payload
