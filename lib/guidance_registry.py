"""Pure validation and projection rules for CARR's typed Guidance Registry.

This module deliberately performs no database or network I/O.  The versioned
registry is validated once, then every consumer receives a deterministic
projection from the same primary classification.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Iterable


GUIDANCE_TYPES = (
    "constraint",
    "procedure",
    "doctrine",
    "rubric",
    "preference",
    "precedent",
    "example",
)

DELIVERY_PROJECTIONS = {
    "constraint": "constraint_enforcement",
    "procedure": "procedure_workflow",
    "doctrine": "doctrine_retrieval",
    "rubric": "verification_rubric",
    "preference": "scoped_preference",
    "precedent": "precedent_search",
    "example": "example_retrieval",
}

LIFECYCLE_STATUSES = {"proposed", "active", "retired", "superseded"}

ACTIVATION_MANIFEST_SCHEMA = "guidance-activation-manifest/v1"
ACTIVATION_MANIFEST_CANONICALIZATION = "utf8-json-sort-keys-compact-newline/v1"


def _present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value is not None


def _required(item: dict[str, Any], path: tuple[str, ...], label: str,
              errors: list[str]) -> Any:
    value: Any = item
    for key in path:
        if not isinstance(value, dict) or key not in value:
            errors.append(f"{item.get('guidance_id', '<unknown>')}: missing {label}")
            return None
        value = value[key]
    if not _present(value):
        errors.append(f"{item.get('guidance_id', '<unknown>')}: empty {label}")
    return value


def validate_registry(data: dict[str, Any]) -> list[str]:
    """Return every contract error; an empty list means the registry is valid."""
    errors: list[str] = []
    if data.get("registry") != "carr-guidance-registry":
        errors.append("registry: expected carr-guidance-registry")
    if data.get("schema_version") != "1.0.0":
        errors.append("registry: unsupported schema version")

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return errors + ["registry: items must be a non-empty array"]

    guidance_ids: set[str] = set()
    source_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in items:
        if not isinstance(raw, dict):
            errors.append("registry: every item must be an object")
            continue
        item = raw
        guidance_id = _required(item, ("guidance_id",), "guidance id", errors)
        source_id = _required(item, ("source_id",), "stable source id", errors)
        _required(item, ("source_clause",), "source clause", errors)
        is_primary = _required(item, ("is_primary",), "primary-record marker", errors)
        if not isinstance(is_primary, bool):
            errors.append(f"{guidance_id}: primary-record marker must be boolean")
        guidance_type = _required(item, ("guidance_type",), "guidance type", errors)
        _required(item, ("scope", "tenant"), "tenant scope", errors)
        _required(item, ("scope", "actor"), "actor scope", errors)
        _required(item, ("activation", "trigger"), "activation trigger", errors)
        _required(item, ("consumer",), "intended consumer", errors)
        _required(item, ("verification", "mechanism"), "verification mechanism", errors)
        _required(item, ("provenance", "source"), "source provenance", errors)
        preserved = _required(
            item, ("provenance", "preserve_source_record"),
            "source-record preservation flag", errors)
        if preserved is not True:
            errors.append(f"{guidance_id}: source record must be preserved")
        status = _required(item, ("lifecycle", "status"), "lifecycle status", errors)
        version = _required(item, ("lifecycle", "version"), "lifecycle version", errors)
        if status and status not in LIFECYCLE_STATUSES:
            errors.append(f"{guidance_id}: invalid lifecycle status {status!r}")
        if version is not None and (not isinstance(version, int) or version < 1):
            errors.append(f"{guidance_id}: lifecycle version must be a positive integer")

        if guidance_id in guidance_ids:
            errors.append(f"{guidance_id}: duplicate guidance id")
        elif isinstance(guidance_id, str):
            guidance_ids.add(guidance_id)
        if isinstance(source_id, str):
            source_items[source_id].append(item)

        if guidance_type not in GUIDANCE_TYPES:
            errors.append(f"{guidance_id}: unknown guidance type {guidance_type!r}")
            continue
        projection = _required(item, ("delivery", "projection"), "delivery projection", errors)
        expected = DELIVERY_PROJECTIONS[guidance_type]
        if projection and projection != expected:
            errors.append(
                f"{guidance_id}: delivery projection {projection!r} does not match {expected!r}")

        if guidance_type == "constraint":
            _required(item, ("delivery", "enforcement_control"), "enforcement control", errors)
            _required(item, ("delivery", "evidence"), "enforcement evidence", errors)
            _required(item, ("delivery", "tests"), "enforcement tests", errors)
        elif guidance_type == "procedure":
            _required(item, ("activation", "entry_condition"), "entry condition", errors)
            _required(item, ("verification", "completion_condition"), "completion condition", errors)
        elif guidance_type == "doctrine":
            _required(item, ("activation", "situation_mappings"), "situation mapping", errors)
        elif guidance_type == "rubric":
            _required(item, ("verification", "acceptance_criteria"), "acceptance criteria", errors)
            _required(item, ("verification", "verifier"), "verifier", errors)
        elif guidance_type == "preference":
            actor = item.get("scope", {}).get("actor")
            if actor in (None, "", "all"):
                errors.append(f"{guidance_id}: preference requires a scoped actor")

    for source_id, linked in sorted(source_items.items()):
        primary_count = sum(item.get("is_primary") is True for item in linked)
        if primary_count != 1:
            errors.append(
                f"{source_id}: expected exactly one primary guidance record, found {primary_count}")

    analyses = data.get("source_analysis", [])
    if not isinstance(analyses, list):
        errors.append("registry: source_analysis must be an array")
        analyses = []
    seen_analysis: set[str] = set()
    for analysis in analyses:
        if not isinstance(analysis, dict):
            errors.append("registry: every source analysis must be an object")
            continue
        source_id = analysis.get("source_id")
        if not _present(source_id):
            errors.append("source analysis: missing source id")
            continue
        if source_id in seen_analysis:
            errors.append(f"{source_id}: duplicate source analysis")
        seen_analysis.add(source_id)
        split_group = analysis.get("split_group_id")
        if analysis.get("mixed_type_detected") is True or _present(split_group):
            clause_types = analysis.get("clause_types")
            linked = source_items.get(str(source_id), [])
            linked_types = {i.get("guidance_type") for i in linked}
            linked_clauses = {i.get("source_clause") for i in linked}
            mixed_types_invalid = (
                analysis.get("mixed_type_detected") is True
                and (not isinstance(clause_types, list) or len(set(clause_types)) < 2
                     or len(linked_types) < 2))
            if (not isinstance(clause_types, list) or not clause_types
                    or len(linked) < 2 or len(linked_clauses) < 2
                    or not _present(split_group) or mixed_types_invalid
                    or any(i.get("split_group_id") != split_group for i in linked)):
                errors.append(
                    f"{source_id}: compound source requires explicit split records "
                    "with distinct clauses, declared types, and one split group")

    return errors


def coverage_errors(data: dict[str, Any], active_source_ids: Iterable[str]) -> list[str]:
    """Prove exact coverage without pretending a split is a duplicate primary."""
    active = set(active_source_ids)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in data.get("items", []):
        if isinstance(item, dict) and isinstance(item.get("source_id"), str):
            by_source[item["source_id"]].append(item)

    errors: list[str] = []
    missing = sorted(active - set(by_source))
    extra = sorted(set(by_source) - active)
    if missing:
        errors.append("missing active source(s): " + ", ".join(missing))
    if extra:
        errors.append("inactive/unknown source(s): " + ", ".join(extra))

    split_sources = {
        a.get("source_id")
        for a in data.get("source_analysis", [])
        if isinstance(a, dict) and _present(a.get("split_group_id"))
    }
    for source_id, items in sorted(by_source.items()):
        if source_id not in active or len(items) < 2:
            continue
        if source_id not in split_sources or any(not i.get("split_group_id") for i in items):
            errors.append(f"{source_id}: multiple unsplit primary records")
    return errors


def projection(data: dict[str, Any], guidance_type: str) -> list[dict[str, Any]]:
    """Return the stable delivery projection for one validated guidance type."""
    if guidance_type not in GUIDANCE_TYPES:
        raise ValueError(f"unknown guidance type: {guidance_type}")
    errors = validate_registry(data)
    if errors:
        raise ValueError("invalid guidance registry: " + "; ".join(errors))
    return sorted(
        (item for item in data["items"] if item["guidance_type"] == guidance_type),
        key=lambda item: (item["source_id"], item["guidance_id"]),
    )


def type_counts(data: dict[str, Any]) -> dict[str, int]:
    counts = Counter(item.get("guidance_type") for item in data.get("items", []))
    return {kind: counts.get(kind, 0) for kind in GUIDANCE_TYPES}


# The one rule surface the guidance registry does not compile. Kept as a
# named constant so the enforcement map, the SQL readers and the reviewed
# curation package all spell the same thing.
EXCLUDED_RULE_SURFACE = "intro_politics"


def excluded_source_ids(source_map: dict[str, Any]) -> set[str]:
    """Rule ids the enforcement map lists but this registry does not compile.

    Every caller that needs the exclusion asks here, so the enforcement map, the
    compiler and the tests can never drift into disagreeing about which rules
    the registry's corpus contains.
    """
    return {
        source_id
        for source_id, control in (source_map.get("rule_controls") or {}).items()
        if isinstance(control, dict)
        and control.get("rule_surface") == EXCLUDED_RULE_SURFACE
    }


def _actor_by_source(source_map: dict[str, Any]) -> dict[str, str]:
    active = source_map.get("active_rule_ids", {})
    actors: dict[str, str] = {}
    for source_id in active.get("shared", []):
        actors[str(source_id)] = "all"
    for source_id in active.get("joe", []):
        actors[str(source_id)] = "joe"
    for source_id in active.get("dell", []):
        actors[str(source_id)] = "dell"
    return actors


def _base_item(source_id: str, guidance_id: str, kind: str, actor: str,
               trigger: str, consumer: str, verification: str,
               lifecycle_status: str) -> dict[str, Any]:
    return {
        "guidance_id": guidance_id,
        "source_id": source_id,
        "source_clause": "whole",
        "guidance_type": kind,
        "is_primary": True,
        "scope": {"tenant": "carr", "actor": actor},
        "activation": {"trigger": trigger},
        "consumer": consumer,
        "verification": {"mechanism": verification},
        "provenance": {
            "source": "rule",
            "preserve_source_record": True,
            "classification_author": "typed-guidance-build",
            "classification_basis": "rule enforcement map plus reviewed migration manifest",
        },
        "lifecycle": {"status": lifecycle_status, "version": 1},
        "delivery": {"projection": DELIVERY_PROJECTIONS[kind]},
    }


def _manifest_item(source_id: str, actor: str, entry: dict[str, Any],
                   part: dict[str, Any] | None = None, index: int = 0) -> tuple[dict[str, Any], list[str]]:
    row = entry if part is None else {**entry, **part}
    # A split is a distinct reviewed clause.  ``row`` intentionally inherits
    # the parent context, but its declared ``type`` must win over the parent's
    # ``proposed_type``; otherwise every mixed source silently compiles as its
    # parent type.
    kind = (part.get("type") if part is not None else None) or (
        row.get("proposed_type") or row.get("guidance_type") or row.get("type"))
    suffix = "whole" if part is None else f"split-{index}"
    guidance_id = f"rule-{source_id}-{suffix}-v1"
    errors: list[str] = []
    if kind not in GUIDANCE_TYPES:
        return {}, [f"{source_id}: manifest has invalid guidance type {kind!r}"]
    trigger = row.get("activation_trigger") or "when the classified guidance is relevant"
    consumer = row.get("consumer") or "typed_guidance_consumer"
    verification = row.get("verification") or "reviewed delivery receipt"
    scoped_actor = row.get("actor") or actor
    if kind == "preference" and scoped_actor == "all":
        scoped_actor = "carr"
    item = _base_item(
        source_id, guidance_id, kind, scoped_actor, trigger, consumer,
        verification, "proposed")
    item["source_clause"] = row.get("source_clause") or row.get("clause") or "whole"
    item["is_primary"] = part is None
    # ``destination`` in the review manifest is a human-readable routing home;
    # the executable projection is deliberately derived from the primary type
    # so no manifest author can route one type through another type's loader.
    item["delivery"]["projection"] = DELIVERY_PROJECTIONS[kind]

    if kind == "constraint":
        control = row.get("enforcement_control")
        evidence = row.get("enforcement_evidence")
        tests = row.get("enforcement_tests")
        if not (_present(control) and _present(evidence) and _present(tests)):
            errors.append(
                f"{source_id}: proposed constraint lacks installed enforcement evidence/tests")
        else:
            item["delivery"].update({
                "enforcement_control": control, "evidence": evidence, "tests": tests})
    elif kind == "procedure":
        item["activation"]["entry_condition"] = row.get("entry_condition") or trigger
        item["verification"]["completion_condition"] = (
            row.get("completion_condition") or verification)
    elif kind == "doctrine":
        # A stable proposal key is enough at build time.  The WR-AI-006
        # curation path still has to approve and bind it to an active retrieval
        # concept before it can affect results.
        mappings = row.get("situation_mappings") or [f"guidance-{source_id}"]
        item["activation"]["situation_mappings"] = mappings
        item["delivery"]["situation_concepts"] = mappings
    elif kind == "rubric":
        item["verification"]["verifier"] = row.get("verifier") or "independent"
        item["verification"]["acceptance_criteria"] = (
            row.get("acceptance_criteria") or [verification])
    return item, errors


def build_registry(source_map: dict[str, Any], migration_manifest: dict[str, Any]
                   ) -> tuple[dict[str, Any], list[str]]:
    """Compile the current enforcement map and reviewed judgment manifest.

    Built gates become active constraint projections, session rails become
    active procedures, and judgment classifications remain proposed until the
    human review/activation named in the handoff.  An ``unbuilt`` row is a hard
    compile error: calling it an active constraint would overstate enforcement.
    """
    errors: list[str] = []
    controls = source_map.get("rule_controls", {})
    catalog = source_map.get("control_catalog", {})
    actors = _actor_by_source(source_map)
    # THE EXCLUDED SURFACE IS NOW DECLARED, NOT IMPLIED. Five SQL readers already
    # exclude rules whose scope kind is intro_politics — the vendor-introduction
    # doctrine that renders on its own surface — and the reviewed curation package
    # names the same exclusion in words: excluded_rule_surface: "intro_politics".
    # Until 2026-08-23 the enforcement map enforced it by simply not listing those
    # rules, which meant the map was NOT a complete inventory of active rules and
    # Phase 1's admission audit could never pass. The map now lists all of them and
    # tags this surface, so the exclusion the registry always intended is read from
    # the tag rather than from an absence.
    excluded = excluded_source_ids(source_map)
    controls = {k: v for k, v in controls.items() if k not in excluded}
    actors = {k: v for k, v in actors.items() if k not in excluded}
    active = set(actors)
    if set(controls) != active:
        missing = sorted(active - set(controls))
        extra = sorted(set(controls) - active)
        if missing:
            errors.append("source map lacks control rows: " + ", ".join(missing))
        if extra:
            errors.append("source map has inactive control rows: " + ", ".join(extra))

    ambient = {
        source_id for source_id, control in controls.items()
        if control.get("enforcement_class") == "judgment_ambient"
    }
    entries = migration_manifest.get("entries", [])
    manifest_by_id = {
        row.get("source_id"): row for row in entries if isinstance(row, dict)
    }
    if set(manifest_by_id) != ambient or len(manifest_by_id) != len(entries):
        missing = sorted(ambient - set(manifest_by_id))
        extra = sorted(set(manifest_by_id) - ambient)
        errors.append(
            "judgment manifest coverage mismatch"
            + (f"; missing={','.join(missing)}" if missing else "")
            + (f"; extra={','.join(str(x) for x in extra)}" if extra else "")
            + ("; duplicate source ids" if len(manifest_by_id) != len(entries) else ""))

    items: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []
    for source_id, control in controls.items():
        actor = actors.get(source_id, "all")
        klass = control.get("enforcement_class")
        if klass == "judgment_ambient":
            entry = manifest_by_id.get(source_id)
            if not entry:
                continue
            primary, item_errors = _manifest_item(source_id, actor, entry)
            errors.extend(item_errors)
            if primary:
                items.append(primary)
            splits = entry.get("split_records") or []
            split_group = f"rule-{source_id}-split-v1"
            clause_types = [entry.get("proposed_type")]
            if splits:
                primary["split_group_id"] = split_group
                if primary.get("source_clause") == "whole":
                    primary["source_clause"] = entry.get("primary_clause") or "judgment-clause"
                for index, split in enumerate(splits, start=1):
                    child, child_errors = _manifest_item(
                        source_id, actor, entry, split, index)
                    errors.extend(child_errors)
                    if child:
                        child["split_group_id"] = split_group
                        items.append(child)
                        clause_types.append(child["guidance_type"])
            unique_clause_types = list(dict.fromkeys(clause_types))
            analyses.append({
                "source_id": source_id,
                "mixed_type_detected": len(unique_clause_types) > 1,
                "clause_types": unique_clause_types,
                **({"split_group_id": split_group} if splits else {}),
                "rationale": entry.get("rationale") or "reviewed typed classification",
            })
            continue
        if klass == "unbuilt":
            errors.append(
                f"{source_id}: unbuilt control cannot enter the active Guidance projection")
            continue
        if klass == "surfacing":
            kind = "procedure"
            trigger = control.get("binding_moment") or "at task startup"
            item = _base_item(
                source_id, f"rule-{source_id}-whole-v1", kind, actor, trigger,
                "workflow_startup", "session rail delivery receipt", "active")
            item["activation"]["entry_condition"] = trigger
            item["verification"]["completion_condition"] = "configured session rail ran"
        elif klass in {"deny_gate", "schema", "stop_gate"}:
            kind = "constraint"
            trigger = control.get("binding_moment") or "at the governed boundary"
            item = _base_item(
                source_id, f"rule-{source_id}-whole-v1", kind, actor, trigger,
                "control_plane", control.get("failure_mode") or "governed refusal", "active")
        else:
            errors.append(f"{source_id}: unsupported enforcement class {klass!r}")
            continue

        control_name = control.get("control")
        evidence = catalog.get(control_name, {}) if control_name else {}
        if kind == "constraint":
            if not control_name or not evidence.get("implementation") or not evidence.get("test"):
                errors.append(f"{source_id}: built constraint lacks catalog evidence/tests")
            else:
                # The admission sync registers each enforcement point with its
                # refs JOINED ("a; b"), and the guidance_revision trigger checks
                # exact containment of that registered string — so the compiled
                # entry carries the joined form alongside the individual paths.
                impl_refs = list(evidence["implementation"])
                test_refs = list(evidence["test"])
                joined_impl = "; ".join(impl_refs)
                joined_test = "; ".join(test_refs)
                item["delivery"].update({
                    "enforcement_control": control_name,
                    "evidence": impl_refs + ([joined_impl] if len(impl_refs) > 1 else []),
                    "tests": test_refs + ([joined_test] if len(test_refs) > 1 else []),
                })
        items.append(item)

    result = {
        "registry": "carr-guidance-registry",
        "schema_version": "1.0.0",
        "items": items,
        "source_analysis": analyses,
    }
    return result, errors


def canonical_json_bytes(value: Any) -> bytes:
    """Return the portable UTF-8 activation-artifact preimage.

    This deliberately has its own named contract rather than guessing at a
    PostgreSQL JSONB rendering: recursively sorted JSON keys, compact
    separators, literal Unicode, and one trailing newline.
    """
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False) + "\n").encode("utf-8")


def activation_manifest_sha256(manifest: dict[str, Any]) -> str:
    """Return the SHA-256 of the named portable activation-artifact encoding."""
    return hashlib.sha256(activation_manifest_bytes(manifest)).hexdigest()


def _digest_metadata(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    """Normalize a reviewed external-artifact identity without reading it."""
    if not isinstance(value, dict):
        errors.append(f"activation manifest: {label} provenance must be an object")
        return {}
    path = value.get("path")
    digest = value.get("sha256")
    if not isinstance(path, str) or not path:
        errors.append(f"activation manifest: {label} provenance requires path")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)):
        errors.append(f"activation manifest: {label} provenance requires lowercase sha256")
    return {"path": path, "sha256": digest}


def build_activation_manifest(
        data: dict[str, Any], *, constitution_guidance_ids: Iterable[str],
        source_manifest_provenance: dict[str, Any],
        base_inventory: dict[str, Any],
        situation_mapping_bindings: dict[str, list[dict[str, str]]]
        ) -> tuple[dict[str, Any], list[str]]:
    """Build the deterministic, reviewable preimage for registry activation.

    This is pure compilation only: it reads no files, writes no state, and
    grants no approval.  A caller supplies the already-measured source TSV and
    enforcement-map identities so the resulting digest binds the *exact*
    reviewed input set, the compiled guidance revisions, and the selected
    constitution rather than merely a convenient raw-file hash.
    """
    errors = validate_registry(data)
    if errors:
        return {}, ["activation manifest: invalid compiled registry: " + "; ".join(errors)]

    source_manifest = _digest_metadata(
        source_manifest_provenance, "source manifest", errors)
    for field, expected in (
            ("manifest", "carr-guidance-migration"),
            ("schema_version", "1.0.0"),
            ("source_classification", "judgment_ambient")):
        actual = source_manifest_provenance.get(field) if isinstance(source_manifest_provenance, dict) else None
        if actual != expected:
            errors.append(
                f"activation manifest: source manifest provenance {field} must be {expected!r}")
        source_manifest[field] = actual
    entry_count = (source_manifest_provenance.get("entry_count")
                   if isinstance(source_manifest_provenance, dict) else None)
    if not isinstance(entry_count, int) or entry_count < 1:
        errors.append("activation manifest: source manifest provenance requires positive entry_count")
    elif entry_count != len(data.get("source_analysis", [])):
        errors.append("activation manifest: source manifest entry_count does not match compiled source analysis")
    source_manifest["entry_count"] = entry_count

    inventory = _digest_metadata(base_inventory, "base inventory", errors)
    active_source_ids = base_inventory.get("active_source_ids") if isinstance(base_inventory, dict) else None
    if (not isinstance(active_source_ids, list) or not active_source_ids
            or any(not isinstance(source_id, str) or not source_id for source_id in active_source_ids)):
        errors.append("activation manifest: base inventory requires active_source_ids")
        active_source_ids = []
    if len(set(active_source_ids)) != len(active_source_ids):
        errors.append("activation manifest: base inventory has duplicate active_source_ids")
    compiled_source_ids = {item["source_id"] for item in data["items"]}
    if set(active_source_ids) != compiled_source_ids:
        errors.append("activation manifest: base inventory ids do not exactly match compiled registry")
    inventory["active_source_ids"] = sorted(active_source_ids)
    inventory["active_source_count"] = len(active_source_ids)
    source_rule_ids = base_inventory.get("source_rule_ids") if isinstance(base_inventory, dict) else None
    if not isinstance(source_rule_ids, dict):
        errors.append("activation manifest: base inventory requires source_rule_ids")
        source_rule_ids = {}
    if set(source_rule_ids) != compiled_source_ids:
        errors.append("activation manifest: source_rule_ids do not exactly match compiled registry")
    for source_id, rule_id in source_rule_ids.items():
        if (not isinstance(rule_id, str) or len(rule_id) != 36
                or rule_id[8:9] != "-" or rule_id[13:14] != "-"
                or rule_id[18:19] != "-" or rule_id[23:24] != "-"
                or any(char not in "0123456789abcdef-" for char in rule_id)):
            errors.append(f"activation manifest: source_rule_ids[{source_id!r}] must be a lowercase UUID")
    inventory["source_rule_ids"] = {
        source_id: source_rule_ids[source_id]
        for source_id in sorted(source_rule_ids)
        if source_id in compiled_source_ids
    }

    constitution = list(constitution_guidance_ids)
    if (not constitution or any(not isinstance(guidance_id, str) or not guidance_id
                               for guidance_id in constitution)):
        errors.append("activation manifest: constitution_guidance_ids must be a non-empty string array")
    if len(set(constitution)) != len(constitution):
        errors.append("activation manifest: constitution_guidance_ids must be unique")
    compiled_guidance_ids = {item["guidance_id"] for item in data["items"]}
    unknown_constitution = sorted(set(constitution) - compiled_guidance_ids)
    if unknown_constitution:
        errors.append("activation manifest: constitution ids absent from compiled registry: "
                      + ", ".join(unknown_constitution))
    nonprimary_constitution = sorted(
        item["guidance_id"] for item in data["items"]
        if item["guidance_id"] in constitution and not item.get("is_primary"))
    if nonprimary_constitution:
        errors.append("activation manifest: constitution ids must select primary guidance: "
                      + ", ".join(nonprimary_constitution))
    if errors:
        return {}, errors

    doctrine_ids = {
        item["guidance_id"] for item in data["items"]
        if item["guidance_type"] == "doctrine"
    }
    if not isinstance(situation_mapping_bindings, dict) or set(situation_mapping_bindings) != doctrine_ids:
        missing = sorted(doctrine_ids - set(situation_mapping_bindings or {}))
        extra = sorted(set(situation_mapping_bindings or {}) - doctrine_ids)
        errors.append("activation manifest: situation bindings must exactly cover doctrine guidance"
                      + (f"; missing={','.join(missing)}" if missing else "")
                      + (f"; extra={','.join(extra)}" if extra else ""))
    def valid_uuid(value: Any) -> bool:
        return (isinstance(value, str) and len(value) == 36
                and value[8:9] == "-" and value[13:14] == "-"
                and value[18:19] == "-" and value[23:24] == "-"
                and all(char in "0123456789abcdef-" for char in value))
    for guidance_id, bindings in (situation_mapping_bindings or {}).items():
        if not isinstance(bindings, list) or not bindings:
            errors.append(
                f"activation manifest: doctrine bindings for {guidance_id} must be a non-empty array")
            continue
        for index, binding in enumerate(bindings, start=1):
            if not isinstance(binding, dict):
                errors.append(f"activation manifest: doctrine binding {guidance_id}[{index}] must be an object")
                continue
            for key in ("concept_id", "doctrine_section_id", "reason"):
                if not _present(binding.get(key)):
                    errors.append(f"activation manifest: doctrine binding {guidance_id}[{index}] missing {key}")
            for key in ("concept_id", "doctrine_section_id"):
                if not valid_uuid(binding.get(key)):
                    errors.append(
                        f"activation manifest: doctrine binding {guidance_id}[{index}] {key} must be a lowercase UUID")
    if errors:
        return {}, errors

    rationale_by_source = {
        analysis["source_id"]: analysis.get("rationale")
        for analysis in data.get("source_analysis", [])
        if isinstance(analysis, dict) and isinstance(analysis.get("source_id"), str)
    }
    entries = []
    for ordinal, item in enumerate(
            sorted(data["items"], key=lambda candidate: candidate["guidance_id"]), start=1):
        source_id = item["source_id"]
        activation = item["activation"]
        delivery = item["delivery"]
        if item["guidance_type"] == "doctrine":
            bindings = sorted(
                situation_mapping_bindings[item["guidance_id"]],
                key=lambda binding: (binding["concept_id"], binding["doctrine_section_id"], binding["reason"]))
            activation = {**activation, "situation_mappings": bindings}
            delivery = {**delivery, "situation_concepts": bindings}
        entries.append({
            "ordinal": ordinal,
            "guidance_id": item["guidance_id"],
            "source_rule_id": source_rule_ids[source_id],
            "source_clause": item["source_clause"],
            "is_primary": item["is_primary"],
            "split_group_key": item.get("split_group_id"),
            "guidance_type": item["guidance_type"],
            "scope": item["scope"],
            "activation": activation,
            "consumer": item["consumer"],
            "verification": item["verification"],
            "provenance": item["provenance"],
            "delivery": delivery,
            "lifecycle": {"version": item["lifecycle"]["version"]},
            "is_constitution": item["guidance_id"] in constitution,
            "reason": (rationale_by_source.get(source_id)
                       or item["provenance"]["classification_basis"]),
        })
    payload = {
        "schema": ACTIVATION_MANIFEST_SCHEMA,
        "canonicalization": ACTIVATION_MANIFEST_CANONICALIZATION,
        "registry": {
            "name": data["registry"],
            "schema_version": data["schema_version"],
        },
        "source_manifest": source_manifest,
        "base_inventory": inventory,
        "constitution_guidance_ids": sorted(constitution),
        "constitution_source_rule_ids": sorted({
            source_rule_ids[item["source_id"]]
            for item in data["items"] if item["guidance_id"] in constitution
        }),
        "entries": entries,
    }
    return payload, []


def activation_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the named portable digest preimage for an activation manifest."""
    if not isinstance(manifest, dict):
        raise ValueError("activation manifest must be an object")
    if manifest.get("schema") != ACTIVATION_MANIFEST_SCHEMA:
        raise ValueError("activation manifest has unsupported schema")
    if manifest.get("canonicalization") != ACTIVATION_MANIFEST_CANONICALIZATION:
        raise ValueError("activation manifest has unsupported canonicalization")
    return canonical_json_bytes(manifest)


MANIFEST_FIELDS = (
    "source_id", "plain_name", "old_classification", "proposed_type",
    "rationale", "destination", "activation_trigger", "consumer",
    "verification", "split_records_json",
)

MANIFEST_PLACEHOLDERS = {
    "rationale": {"Contextual guidance from current rule text."},
    "destination": {"typed-guidance manifest"},
    "activation_trigger": {"when this rule situation occurs"},
    "verification": {"review guidance use and supporting evidence"},
}


def load_migration_manifest(path: str) -> tuple[dict[str, Any], list[str]]:
    """Read and validate the reviewable judgment-classification TSV."""
    errors: list[str] = []
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            if tuple(reader.fieldnames or ()) != MANIFEST_FIELDS:
                return {"entries": []}, [
                    "manifest columns must be exactly: " + ", ".join(MANIFEST_FIELDS)]
            for line_number, row in enumerate(reader, start=2):
                source_id = (row.get("source_id") or "").strip()
                prefix = source_id or f"line {line_number}"
                if len(source_id) != 8 or any(c not in "0123456789abcdef" for c in source_id):
                    errors.append(f"{prefix}: source_id must be eight lowercase hex characters")
                for field in MANIFEST_FIELDS[:-1]:
                    if not (row.get(field) or "").strip():
                        errors.append(f"{prefix}: empty {field}")
                if row.get("plain_name", "").strip() == source_id:
                    errors.append(f"{prefix}: plain_name cannot be the source id")
                if row.get("old_classification") != "judgment_ambient":
                    errors.append(f"{prefix}: old classification must be judgment_ambient")
                kind = row.get("proposed_type")
                if kind not in GUIDANCE_TYPES:
                    errors.append(f"{prefix}: invalid proposed type {kind!r}")
                if kind == "constraint":
                    errors.append(
                        f"{prefix}: ambient guidance cannot become a constraint without installed evidence")
                for field, banned in MANIFEST_PLACEHOLDERS.items():
                    if (row.get(field) or "").strip() in banned:
                        errors.append(f"{prefix}: placeholder {field} is not reviewable")
                rationale = (row.get("rationale") or "").strip().lower()
                trigger = (row.get("activation_trigger") or "").strip().lower()
                verification = (row.get("verification") or "").strip().lower()
                if (rationale.startswith("guidance for ")
                        or (trigger.startswith("when ") and trigger.endswith(" is relevant"))
                        or verification == "review application against the rule's stated outcome"):
                    errors.append(f"{prefix}: generated boilerplate is not a reviewable classification")
                try:
                    splits = json.loads(row.get("split_records_json") or "")
                except json.JSONDecodeError as exc:
                    errors.append(f"{prefix}: split_records_json is invalid: {exc.msg}")
                    splits = []
                if not isinstance(splits, list):
                    errors.append(f"{prefix}: split_records_json must be an array")
                    splits = []
                normalized_splits: list[dict[str, Any]] = []
                for split_index, split in enumerate(splits, start=1):
                    if not isinstance(split, dict):
                        errors.append(f"{prefix}: split {split_index} must be an object")
                        continue
                    required = (
                        "clause", "type", "destination", "activation_trigger",
                        "consumer", "verification",
                    )
                    missing = [key for key in required if not _present(split.get(key))]
                    if missing:
                        errors.append(
                            f"{prefix}: split {split_index} missing {', '.join(missing)}")
                    split_type = split.get("type")
                    if split_type not in GUIDANCE_TYPES:
                        errors.append(f"{prefix}: split {split_index} has invalid type {split_type!r}")
                    if split_type == "constraint":
                        errors.append(
                            f"{prefix}: split {split_index} cannot claim a constraint without installed evidence")
                    if (split.get("clause") ==
                            "separable mechanical clause; enforcement-backlog candidate"):
                        errors.append(
                            f"{prefix}: split {split_index} uses generated boilerplate instead of an actual clause")
                    normalized_splits.append(split)
                entries.append({
                    "source_id": source_id,
                    "name": row.get("plain_name", "").strip(),
                    "old_classification": row.get("old_classification", "").strip(),
                    "proposed_type": kind,
                    "rationale": row.get("rationale", "").strip(),
                    "destination": row.get("destination", "").strip(),
                    "activation_trigger": row.get("activation_trigger", "").strip(),
                    "consumer": row.get("consumer", "").strip(),
                    "verification": row.get("verification", "").strip(),
                    "split_records": normalized_splits,
                })
    except OSError as exc:
        return {"entries": []}, [f"cannot read migration manifest: {exc}"]
    return {
        "manifest": "carr-guidance-migration",
        "schema_version": "1.0.0",
        "source_classification": "judgment_ambient",
        "entries": entries,
    }, errors
