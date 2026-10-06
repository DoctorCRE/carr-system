"""Deterministic kernel for the CARR control plane.

Provider schedulers and model providers are adapters.  This module owns the
portable decisions they are not allowed to make: registry validation, retry
timing, cache identity, proposal validation, and legacy-cutover evidence.
Database state transitions live in migration 0135 and expose the same rules to
every runner through stored functions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

WORKFLOW_KEYS = {
    "key", "version", "enabled", "risk", "recurrence", "execution",
    "state", "routing", "filtering", "validation", "retry", "deduplication",
    "completion", "legacy_schedule", "inventory",
}
EXECUTION_KINDS = {"deterministic", "cognition"}
RISKS = {"green", "yellow", "red"}
BACKOFFS = {"constant", "linear", "exponential"}
# An enabled deterministic canary must name a code-registered isolation guard.
# The registry is deliberately empty until an entrypoint has a real, tested
# destination boundary.  A manifest edit alone can never turn a live command
# into a canary.
CANARY_ISOLATION_GUARDS: frozenset[str] = frozenset({
    "calendar-fetch-daily.canary.v1", "notes-sweep-hourly.canary.v1",
    "nightly-record-layer.availability-matcher.canary.v1",
})
DECISION_FIELDS = ("routing", "filtering", "validation", "completion")
INVENTORY_FIELDS = {
    "trigger", "owner", "inputs", "canonical_reads", "canonical_writes",
    "external_dependencies", "authority", "current_completion_signal",
    "replacement_program", "acceptance", "retirement_approval",
}


def _all_true(spec: dict[str, Any], context: dict[str, Any]) -> bool:
    return all(context.get(fact) is True for fact in spec["all_of"])


PREDICATES = {"facts.all_true": _all_true}
PROPOSAL_GUARDS = {"weekly_social_no_quote_tweets"}


class EntrypointFailure(RuntimeError):
    """A deterministic entrypoint child exited nonzero.

    Carries the bounded, already-redacted stdout/stderr tails as structured
    fields so a caller can put them INTO stored failure/dead-letter evidence,
    instead of losing them to a generic exception's flat ``str(exc)`` cap
    (rule 1f3a7372: an unattended run must record what it FOUND).
    """

    def __init__(self, returncode: int, *, stdout_tail: str, stderr_tail: str):
        super().__init__(f"entrypoint exited {returncode}")
        self.returncode = returncode
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail


class EntrypointNotConfigured(EntrypointFailure):
    """A deterministic entrypoint child exited 78 (EX_CONFIG).

    This repo's standing convention (bin/nightly.sh and every other launchd
    chain) is that 78 means the step RAN, found a credential or setting it
    needs absent, wrote nothing, and said so on its own stdout/stderr -- not
    that it failed. Rule 88e9b5eb: "not authorized" and "not possible" are
    different findings and must never be reported as the same one.

    Subclasses EntrypointFailure so the stdout/stderr capture in
    ``_execute_deterministic`` and the JSON-tail encoding in
    ``_failure_detail`` stay ONE piece of code, not a duplicate for this exit
    code. Only the terminal ledger call a caller makes differs: run_once
    routes this to ``ops.skip_job``, never ``ops.fail_job`` -- no retry
    budget spent, no dead-letter, because a missing credential does not
    self-heal on a timer the way a transient failure might.
    """

    def __init__(self, *, stdout_tail: str, stderr_tail: str):
        RuntimeError.__init__(self, "entrypoint exited 78 (not configured)")
        self.returncode = 78
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail


def deterministic_args(execution: dict[str, Any], mode: str) -> list[str]:
    """Return the registered command arguments for one deterministic mode.

    Canary is a separate authority boundary, not an alias for live arguments.
    It stays unavailable until both the manifest and code name a tested
    isolation guard.  This function is used before enqueue and immediately
    before execution so neither a new schedule nor an old queued job can bypass
    the boundary.
    """
    if mode == "shadow":
        selected = execution.get("shadow_args")
    elif mode == "canary":
        canary = execution.get("canary")
        if not isinstance(canary, dict) or canary.get("enabled") is not True:
            reason = canary.get("reason") if isinstance(canary, dict) else None
            suffix = f": {reason}" if isinstance(reason, str) and reason else ""
            raise RuntimeError(f"deterministic canary isolation is disabled{suffix}")
        guard = canary.get("isolation_guard")
        if guard not in CANARY_ISOLATION_GUARDS:
            raise RuntimeError(f"deterministic canary isolation guard is not registered: {guard}")
        selected = canary.get("args")
    elif mode == "replay":
        raise RuntimeError("deterministic replay execution is disabled; use a versioned shadow or isolated canary contract")
    else:
        selected = execution.get("args")
    if not isinstance(selected, list) or not all(isinstance(x, str) for x in selected):
        raise RuntimeError(f"deterministic {mode} execution is not explicitly registered")
    return list(selected)


def resolve_auto_mode(canary_contract: dict[str, Any] | None,
                       acceptance_rows: list[dict[str, Any]]) -> str:
    """Resolve one workflow's HIGHEST mode tier permitted by the acceptance ladder.

    This is the same ladder ``ops.enqueue_job`` enforces (migration 0332),
    reimplemented as a pure function so ``tick --mode auto`` can pick a tier to
    try without a database round trip per candidate.  The database guard stays
    authoritative; a wrong resolution here only causes a refused enqueue, never
    an unguarded one.

    ``canary_contract`` is a workflow's ``execution.canary`` object (or None/
    missing, as for every cognition workflow).  ``acceptance_rows`` is the raw
    ``ops.workflow_acceptance`` row set for the workflow's current definition
    version, each item shaped ``{"mode": ..., "status": ...}``.
    """
    accepted = {row.get("mode") for row in acceptance_rows if row.get("status") == "accepted"}
    canary_disabled = isinstance(canary_contract, dict) and canary_contract.get("enabled") is False
    if canary_disabled:
        return "live" if "shadow" in accepted else "shadow"
    if "canary" in accepted:
        return "live"
    if "shadow" in accepted:
        return "canary"
    return "shadow"


def evaluate_predicate(decision: dict[str, Any], context: dict[str, Any]) -> bool:
    """Evaluate one registry decision without involving a model or scheduler.

    The scheduler/dispatcher is responsible for building the named facts from
    its typed inputs. This evaluator refuses prose-only and unknown predicates,
    making the stable predicate name and its required facts an executable API.
    """
    if not isinstance(decision, dict):
        raise TypeError("decision must be an object")
    key = decision.get("key")
    spec = decision.get("spec")
    if key not in PREDICATES:
        raise KeyError(f"unregistered predicate: {key}")
    if not isinstance(spec, dict):
        raise TypeError("predicate spec must be an object")
    facts = spec.get("all_of")
    if not isinstance(facts, list) or not facts or not all(isinstance(x, str) and x for x in facts):
        raise ValueError("facts.all_true requires a non-empty all_of string array")
    if not isinstance(context, dict):
        raise TypeError("predicate context must be an object")
    return PREDICATES[key](spec, context)


def predicate_seed_context(decision: dict[str, Any]) -> dict[str, bool]:
    """Build the positive fixture used by the manifest acceptance tests."""
    spec = decision.get("spec") if isinstance(decision, dict) else None
    if not isinstance(spec, dict):
        raise TypeError("predicate spec must be an object")
    facts = spec.get("all_of")
    if not isinstance(facts, list) or not facts or not all(isinstance(x, str) and x for x in facts):
        raise ValueError("facts.all_true requires a non-empty all_of string array")
    return {fact: True for fact in facts}


def _is_schema(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("type") in {
        "object", "array", "string", "number", "integer", "boolean", "null"
    }


def validate_manifest(manifest: dict[str, Any], *, repo: Path | None = None) -> list[str]:
    """Return every structural error; never repair or infer a missing contract."""
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    workflows = manifest.get("workflows")
    cognition_jobs = manifest.get("cognition_jobs")
    if not isinstance(workflows, list) or not workflows:
        return errors + ["workflows must be a non-empty array"]
    if not isinstance(cognition_jobs, list):
        return errors + ["cognition_jobs must be an array"]

    cognition: dict[str, dict[str, Any]] = {}
    for i, job in enumerate(cognition_jobs):
        prefix = f"cognition_jobs[{i}]"
        if not isinstance(job, dict) or not job.get("key"):
            errors.append(f"{prefix}.key is required")
            continue
        key = str(job["key"])
        if key in cognition:
            errors.append(f"duplicate cognition job {key}")
        cognition[key] = job
        if not isinstance(job.get("version"), int) or job["version"] < 1:
            errors.append(f"{prefix}.version must be a positive integer")
        for field in ("input_schema_version", "output_schema_version"):
            if not isinstance(job.get(field), int) or job[field] < 1:
                errors.append(f"{prefix}.{field} must be a positive integer")
        if not _is_schema(job.get("input_schema")):
            errors.append(f"{prefix}.input_schema must be a JSON schema")
        if not _is_schema(job.get("output_schema")):
            errors.append(f"{prefix}.output_schema must be a JSON schema")
        budget = job.get("budget", {})
        if not isinstance(budget.get("max_tokens"), int) or budget["max_tokens"] <= 0:
            errors.append(f"{prefix}.budget.max_tokens must be positive")
        if budget.get("max_cost_usd") is None or budget.get("timeout_seconds") is None:
            errors.append(f"{prefix}.budget requires max_cost_usd and timeout_seconds")
        if job.get("canonical_write_authority") is not False:
            errors.append(f"{prefix}.canonical_write_authority must be false")
        routes = job.get("provider_routes")
        if not isinstance(routes, list) or not routes or not all(isinstance(x, str) for x in routes):
            errors.append(f"{prefix}.provider_routes must be a non-empty string array")
        guard = job.get("proposal_guard")
        if guard is not None and guard not in PROPOSAL_GUARDS:
            errors.append(f"{prefix}.proposal_guard is not registered: {guard}")

    seen: set[str] = set()
    for i, workflow in enumerate(workflows):
        prefix = f"workflows[{i}]"
        if not isinstance(workflow, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = WORKFLOW_KEYS - set(workflow)
        if missing:
            errors.append(f"{prefix} missing {sorted(missing)}")
        key = str(workflow.get("key", ""))
        if not key:
            errors.append(f"{prefix}.key is required")
        elif key in seen:
            errors.append(f"duplicate workflow {key}")
        seen.add(key)
        if workflow.get("risk") not in RISKS:
            errors.append(f"{prefix}.risk must be one of {sorted(RISKS)}")
        if not isinstance(workflow.get("version"), int) or workflow.get("version", 0) < 1:
            errors.append(f"{prefix}.version must be positive")
        recurrence = workflow.get("recurrence", {})
        if not isinstance(recurrence, dict) or not recurrence.get("timezone"):
            errors.append(f"{prefix}.recurrence.timezone is required")
        if workflow.get("enabled") and not recurrence.get("cron"):
            if recurrence.get("kind") != "on_demand" or recurrence.get("schedule") is not None:
                errors.append(f"{prefix}.enabled workflow requires recurrence.cron or an explicit on_demand contract")

        execution = workflow.get("execution", {})
        kind = execution.get("kind") if isinstance(execution, dict) else None
        if kind not in EXECUTION_KINDS:
            errors.append(f"{prefix}.execution.kind must be deterministic or cognition")
        elif kind == "deterministic":
            path = execution.get("entrypoint")
            if not path:
                errors.append(f"{prefix}.execution.entrypoint is required")
            elif repo is not None and not (repo / path).exists():
                errors.append(f"{prefix}.execution.entrypoint does not exist: {path}")
            for mode_field in ("args", "shadow_args"):
                mode_args = execution.get(mode_field)
                if not isinstance(mode_args, list) or not all(isinstance(x, str) for x in mode_args):
                    errors.append(f"{prefix}.execution.{mode_field} must be an explicit string array")
            canary = execution.get("canary")
            if not isinstance(canary, dict) or not isinstance(canary.get("enabled"), bool):
                errors.append(f"{prefix}.execution.canary requires an explicit enabled boolean")
            elif canary["enabled"]:
                guard = canary.get("isolation_guard")
                if guard not in CANARY_ISOLATION_GUARDS:
                    errors.append(
                        f"{prefix}.execution.canary isolation guard is not registered: {guard}")
                canary_args = canary.get("args")
                if not isinstance(canary_args, list) or not all(isinstance(x, str) for x in canary_args):
                    errors.append(f"{prefix}.execution.canary.args must be an explicit string array")
            elif not isinstance(canary.get("reason"), str) or not canary["reason"].strip():
                errors.append(f"{prefix}.execution.canary disabled state requires a reason")
        elif kind == "cognition":
            job_key = execution.get("cognition_job")
            if job_key not in cognition:
                errors.append(f"{prefix}.execution.cognition_job is not registered: {job_key}")

        retry = workflow.get("retry", {})
        if retry.get("backoff") not in BACKOFFS:
            errors.append(f"{prefix}.retry.backoff must be one of {sorted(BACKOFFS)}")
        if not isinstance(retry.get("max_attempts"), int) or retry.get("max_attempts", 0) < 1:
            errors.append(f"{prefix}.retry.max_attempts must be positive")
        for field in ("state", "routing", "filtering", "validation",
                      "deduplication", "completion", "legacy_schedule", "inventory"):
            if not isinstance(workflow.get(field), dict):
                errors.append(f"{prefix}.{field} must be an object")
        inventory = workflow.get("inventory", {})
        missing_inventory = INVENTORY_FIELDS - set(inventory) if isinstance(inventory, dict) else INVENTORY_FIELDS
        if missing_inventory:
            errors.append(f"{prefix}.inventory missing {sorted(missing_inventory)}")
        elif any(inventory[field] in (None, "", [], {}) for field in INVENTORY_FIELDS):
            errors.append(f"{prefix}.inventory contains an empty required field")
        for field in DECISION_FIELDS:
            decision = workflow.get(field)
            if not isinstance(decision, dict):
                continue
            if "predicate" in decision:
                errors.append(f"{prefix}.{field} must not use a prose predicate")
            try:
                predicate_seed_context(decision)
                evaluate_predicate(decision, predicate_seed_context(decision))
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"{prefix}.{field} invalid: {exc}")
        if not workflow.get("deduplication", {}).get("key_template"):
            errors.append(f"{prefix}.deduplication.key_template is required")
        completion = workflow.get("completion", {})
        if not completion.get("key") or not completion.get("receipt_kind"):
            errors.append(f"{prefix}.completion requires executable key and receipt_kind")
    # Predicate names are a schema boundary, not an informal convention.  The
    # fact registry is intentionally explicit so a new manifest fact cannot
    # become an unbuilt boolean silently supplied by a scheduler.
    from lib.control_plane_facts import registry_errors
    errors.extend(registry_errors(manifest))
    return errors


def retry_delay_seconds(attempt: int, base_seconds: int, cap_seconds: int,
                        backoff: str = "exponential") -> int:
    """Delay before the next attempt; `attempt` is the attempt that just failed."""
    if attempt < 1 or base_seconds < 0 or cap_seconds < 0:
        raise ValueError("attempt must be >=1 and delays must be non-negative")
    if backoff == "constant":
        delay = base_seconds
    elif backoff == "linear":
        delay = base_seconds * attempt
    elif backoff == "exponential":
        delay = base_seconds * (2 ** (attempt - 1))
    else:
        raise ValueError(f"unsupported backoff: {backoff}")
    return min(delay, cap_seconds)


def cache_key(job_type: str, schema_version: int, payload: Any, *, provider: str | None = None) -> str:
    """Provider-neutral identity for a cognition result.

    `provider` is accepted to make accidental provider coupling visible at the
    call site, but deliberately does not participate in the digest.
    """
    del provider
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    raw = f"{job_type}\n{schema_version}\n{canonical}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_object_shape(value: Any, schema: dict[str, Any], path: str,
                           errors: list[str]) -> None:
    if schema.get("type") == "object":
        if not isinstance(value, dict):
            errors.append(f"{path} must be an object")
            return
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{path}.{field} is required")
        props = schema.get("properties", {})
        for field, child in props.items():
            if field in value:
                _validate_object_shape(value[field], child, f"{path}.{field}", errors)
    elif schema.get("type") == "array" and not isinstance(value, list):
        errors.append(f"{path} must be an array")
    elif schema.get("type") == "string" and not isinstance(value, str):
        errors.append(f"{path} must be a string")
    elif schema.get("type") == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
        errors.append(f"{path} must be an integer")
    elif schema.get("type") == "number" and not (isinstance(value, (int, float)) and not isinstance(value, bool)):
        errors.append(f"{path} must be a number")
    elif schema.get("type") == "boolean" and not isinstance(value, bool):
        errors.append(f"{path} must be a boolean")


def validate_proposal(envelope: Any, expected_job_type: str, expected_version: int,
                      output_schema: dict[str, Any]) -> list[str]:
    """Validate model output before policy or canonical-write code can see it."""
    errors: list[str] = []
    if not isinstance(envelope, dict):
        return ["envelope must be an object"]
    if envelope.get("job_type") != expected_job_type:
        errors.append("job_type does not match the claimed job")
    if envelope.get("schema_version") != expected_version:
        errors.append("schema_version does not match the registered output version")
    if "canonical_write" in envelope or "mutation" in envelope:
        errors.append("AI output may contain a proposal only, never a canonical write")
    if "proposal" not in envelope:
        errors.append("proposal is required")
    else:
        _validate_object_shape(envelope["proposal"], output_schema, "proposal", errors)
    allowed = {"job_type", "schema_version", "proposal", "cannot_decide", "evidence"}
    unknown = set(envelope) - allowed
    if unknown:
        errors.append(f"unknown envelope fields: {sorted(unknown)}")
    return errors


def can_disable_legacy(evidence: list[dict[str, Any]], *, minimum_accepted: int = 2) -> bool:
    """Cutover predicate: accepted receipts, including both shadow and canary."""
    accepted = [e for e in evidence
                if e.get("status") == "accepted" and e.get("receipt_ref")]
    modes = {e.get("mode") for e in accepted}
    return len(accepted) >= minimum_accepted and {"shadow", "canary"}.issubset(modes)
