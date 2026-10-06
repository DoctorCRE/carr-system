"""Typed, fail-closed evidence facts for control-plane workflow decisions."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol


class FactUnavailable(RuntimeError): pass
class FactConflict(RuntimeError): pass
class FactProtocolError(ValueError): pass


class FactCollector(Protocol):
    """Read-only seam for canonical DB, device, command, or receipt evidence."""
    def collect(self, *, fact: str, workflow_key: str, stage: str) -> Iterable[Mapping[str, Any]]: ...


SOURCE_KINDS = frozenset({"canonical_db", "read_only_command", "device", "canonical_receipt"})
STAGES = ("routing", "filtering", "validation", "completion")

# Explicit registry: adding a manifest fact does not silently create its builder.
FACT_NAMES = frozenset("""runtime_errors.consumer_registered calendar.eventkit_bundle_registered command.execution_evidence_reconciles command.exit_zero command.receipt_persisted command.registered_args_selected command.workflow_marker_valid calendar.weekday capability.candidate_admitted capability.single_bounded_candidate content_fuel.cite_or_cut content_fuel.current content_fuel.deduped content_fuel.local_and_next_cold_lane content_fuel.primary_sources_only content_fuel.publication_firewall content_fuel.weekly_receipt_absent deal_history.discrepancies_proposal_only deal_history.identity_sources_direct deal_history.slice_size_within_policy deal_history.unverified_counterparties_exist enrichment.every_finding_has_source_observed_at_status enrichment.exactly_40_prioritized enrichment.reverification_priority_ordered enrichment.reverification_due_nonempty health.artifact_evidence health.live_evidence health.monthly_receipt_absent health.one_run_in_monthly_window health.registry_evidence idea.monthly_receipt_absent idea.oldest_or_least_recently_surfaced idea.shortlist_references_canonical_rows linkedin.collector_available linkedin.draft_only linkedin.link_and_relationship linkedin.network_priority linkedin.post_count_in_range linkedin.voice_valid linkedin.weekday loops.inside_data_class_grant loops.no_human_counterparty_or_event_blocker loops.proposal_has_evidence loops.system_owned_actionable_exist metrics.current_platform_windows metrics.numeric_types metrics.owned_accounts metrics.placement_ids metrics.placements_in_window metrics.source_timestamps mutation.production_absent nightly.one_instance_per_local_date notes.business_hour_weekday notes.canonical_schedule_owner npi.healthcare_provider_predicate npi.input_reconciliation npi.proposal_dedup npi.source_rows npi.territory_predicate npi.weekly_delta_unprocessed playbook.changes_proposal_only_until_gated playbook.due_policy_sections playbook.measured_failure_evidence playbook.monthly_receipt_absent playbook.sweep_receipt_present proposal.next_human_action proposal.no_canonical_write_authority proposal.receipt_persisted proposal.risks proposal.tests proposal.worktree radar.candidates_proposal_only radar.freshness_guard radar.lane_health_explicit radar.lanes_code_scored radar.overdue_pool radar.weekly_receipt_absent release.every_action_has_source release.newer_than_last_accepted_audit release.version_change_recorded restore.encrypted_dump_exists restore.non_interactive_credential runner.identity_bound social.cadence social.format social.next_week_uncovered social.no_replies social.publication_firewall social.schema social.source_verification social.topic_rotation social.writing_lint system_sweep.every_destructive_proposal_human_gated system_sweep.every_destructive_proposal_recoverable system_sweep.monthly_receipt_absent x.actual_post_read x.collector_available x.draft_count_in_range x.draft_only x.fresh_in_lane_posts x.no_duplicate_source x.no_likes_follows_or_posts x.voice_valid x.weekday_slot""".split())

FACT_NAMES = frozenset(
    (FACT_NAMES - {
        "content_fuel.cite_or_cut", "content_fuel.current", "content_fuel.deduped",
        "content_fuel.primary_sources_only", "content_fuel.publication_firewall",
    }) | {"content_fuel.post_provider_contract"}
)

# Kept explicit with the other manifest names: the initial NPI hardening
# replacement above must not accidentally drop the system-sweep predicate.
FACT_NAMES = frozenset(FACT_NAMES | {
    "proposal.input_reconciled_contract",
    "renewal.pool_imported",
    "renewal.source_complete",
    "renewal.source_run_sealed",
    "system_sweep.measured_stale_duplicate_or_oversized",
})

# Only claims that a completion receipt itself was persisted require the
# receipt envelope as their source.  Admission predicates such as
# ``monthly_receipt_absent`` are canonical queries *about* the immutable ledger;
# absence cannot itself be sourced from a receipt, and treating every fact whose
# English name contains "receipt" as receipt-only made all first runs invalid.
RECEIPT_FACTS = frozenset({"proposal.receipt_persisted", "command.receipt_persisted"})


def manifest_facts(manifest: Mapping[str, Any]) -> set[str]:
    return {fact for workflow in manifest.get("workflows", []) for stage in STAGES
            for fact in workflow.get(stage, {}).get("spec", {}).get("all_of", [])}


def registry_errors(manifest: Mapping[str, Any]) -> list[str]:
    declared = manifest_facts(manifest)
    return ([f"manifest fact has no registered builder: {x}" for x in sorted(declared - FACT_NAMES)] +
            [f"registered fact is not used by manifest: {x}" for x in sorted(FACT_NAMES - declared)])


def _value(fact: str, raw: Mapping[str, Any]) -> bool:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1 or raw.get("fact") != fact:
        raise FactProtocolError(f"{fact}: exact schema_version=1 and fact name are required")
    source = raw.get("source_kind")
    if source not in SOURCE_KINDS:
        raise FactProtocolError(f"{fact}: unsupported source_kind")
    if fact in RECEIPT_FACTS and source != "canonical_receipt":
        raise FactProtocolError(f"{fact}: receipt-backed fact requires canonical_receipt")
    if not isinstance(raw.get("source_ref"), str) or not raw["source_ref"].strip():
        raise FactProtocolError(f"{fact}: source_ref is required")
    observed = raw.get("observed_at")
    if not isinstance(observed, str):
        raise FactProtocolError(f"{fact}: observed_at is required")
    try: datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError as exc: raise FactProtocolError(f"{fact}: observed_at must be ISO-8601") from exc
    if type(raw.get("value")) is not bool:
        raise FactProtocolError(f"{fact}: value must be boolean, never a truthy proxy")
    return raw["value"]


def build_fact(fact: str, collector: FactCollector, *, workflow_key: str, stage: str) -> bool:
    if fact not in FACT_NAMES: raise FactUnavailable(f"unregistered fact: {fact}")
    if stage not in STAGES: raise FactProtocolError(f"unknown workflow stage: {stage}")
    observations = list(collector.collect(fact=fact, workflow_key=workflow_key, stage=stage))
    if not observations: raise FactUnavailable(f"{workflow_key}.{stage}: no canonical evidence for {fact}")
    values = {_value(fact, observation) for observation in observations}
    if len(values) != 1: raise FactConflict(f"{workflow_key}.{stage}: conflicting evidence for {fact}")
    return values.pop()


def build_stage_facts(workflow: Mapping[str, Any], stage: str, collector: FactCollector) -> dict[str, bool]:
    try: facts, key = workflow[stage]["spec"]["all_of"], workflow["key"]
    except (KeyError, TypeError) as exc: raise FactProtocolError(f"malformed {stage} workflow contract") from exc
    if not isinstance(facts, list) or not facts: raise FactProtocolError(f"{key}.{stage}: all_of is required")
    return {fact: build_fact(fact, collector, workflow_key=key, stage=stage) for fact in facts}


def evaluate_stage(workflow: Mapping[str, Any], stage: str, collector: FactCollector) -> bool:
    from lib.control_plane import evaluate_predicate
    return evaluate_predicate(workflow[stage], build_stage_facts(workflow, stage, collector))


class EnvelopeFactCollector:
    """Adapter for read-only evidence envelopes gathered by a runtime adapter."""
    def __init__(self, envelopes: Iterable[Mapping[str, Any]]):
        self.by_fact: dict[str, list[Mapping[str, Any]]] = {}
        for item in envelopes:
            if isinstance(item, Mapping) and isinstance(item.get("fact"), str):
                self.by_fact.setdefault(item["fact"], []).append(item)

    def collect(self, *, fact: str, workflow_key: str, stage: str) -> Iterable[Mapping[str, Any]]:
        return tuple(self.by_fact.get(fact, ()))


def fact_envelope(fact: str, value: bool, *, source_kind: str, source_ref: str,
                  observed_at: str | None = None) -> dict[str, Any]:
    """Make the only accepted representation of a runtime-derived fact."""
    return {"schema_version": 1, "fact": fact, "value": value,
            "source_kind": source_kind, "source_ref": source_ref,
            "observed_at": observed_at or datetime.now(timezone.utc).isoformat()}


class CompositeFactCollector:
    """Prefer runtime-derived evidence; device evidence is a supplement only."""
    def __init__(self, *collectors: FactCollector): self.collectors = collectors

    def collect(self, *, fact: str, workflow_key: str, stage: str) -> Iterable[Mapping[str, Any]]:
        primary = tuple(self.collectors[0].collect(fact=fact, workflow_key=workflow_key, stage=stage))
        # A runtime collector that knows a fact wins.  Supplementary device
        # observations can fill a genuinely unavailable device-only fact but
        # cannot conflict with or replace normal scheduler evidence.
        if primary:
            return primary
        return tuple(item for collector in self.collectors[1:]
                     for item in collector.collect(fact=fact, workflow_key=workflow_key, stage=stage))
