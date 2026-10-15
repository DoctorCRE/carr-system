#!/usr/bin/env python3
"""Fail-closed static inventory of versioned Drive dependencies.

This is intentionally an audit, not a Drive probe.  It finds the repository's
literal Drive aliases and requires each occurrence to be classified in the
versioned registry.  A classification says whether an occurrence is a normal
runtime dependency, a projection, recovery/migration code, a test fixture, or
prose.  It never treats an unmounted path as evidence that the dependency is
gone.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from urllib.parse import unquote
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_REGISTRY = "ops/config/drive-dependencies.v1.json"
DEFAULT_SCHEMA = "ops/config/drive-dependencies.schema.v1.json"
# Git tracks this vendor dependency as a gitlink (mode 160000); it is external
# generated plumbing, not source text that this repository can classify.
GENERATED_GITLINKS = frozenset({"tools/dictation-rig/vendor/quill"})
ALIAS = re.compile(
    r"CARR_VAULT|\bVAULT_DEFAULT\b|\bDRIVE_ROOT\b|"
    r"(?:~|/Users/[^/\"']+)(?:/Library/CloudStorage/GoogleDrive-[^/\"']+)?/My Drive/CARR AI|"
    r"GoogleDrive-[^/\"'\s)]+/My Drive/CARR AI|"
    r"GoogleDrive-[^/\"'\s)%]+%2FMy%20Drive%2FCARR%20AI|"
    r"GoogleDrive-[^\"'\s)]+"
)
VALID_CLASSES = frozenset({"normal_runtime", "scheduled", "projection", "recovery", "migration_backfill", "policy_guard", "inventory_tooling", "test_fixture", "prose_only"})
OPERATIONAL_CLASSES = frozenset({"normal_runtime", "scheduled", "projection", "recovery", "migration_backfill"})


@dataclass(frozen=True)
class Reference:
    file: str
    line: int
    alias: str
    resolved_path: str
    excerpt: str

    @property
    def ref(self) -> str:
        return f"{self.file}:{self.line}"


class InventoryError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read registry {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InventoryError("registry root must be an object")
    return value


def _schema_path(root: Path) -> Path:
    return root / DEFAULT_SCHEMA


def _schema_validate(value: Any, schema: dict[str, Any], path: str = "registry") -> None:
    """Validate the exact JSON-Schema subset used by the checked-in contract."""
    if "const" in schema and value != schema["const"]:
        raise InventoryError(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise InventoryError(f"{path}: value is not in the schema enum")
    declared = schema.get("type")
    if declared is not None:
        allowed = declared if isinstance(declared, list) else [declared]
        type_checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        }
        if not any(name in type_checks and type_checks[name](value) for name in allowed):
            raise InventoryError(f"{path}: expected schema type {declared}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise InventoryError(f"{path}: schema required must be an array")
        missing = [key for key in required if key not in value]
        if missing:
            raise InventoryError(f"{path}: missing required properties {', '.join(missing)}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise InventoryError(f"{path}: schema properties must be an object")
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise InventoryError(f"{path}: forbidden properties {', '.join(extras)}")
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                _schema_validate(item, child, f"{path}.{key}")
    if isinstance(value, list):
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise InventoryError(f"{path}: requires at least {minimum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _schema_validate(item, item_schema, f"{path}[{index}]")
    if isinstance(value, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise InventoryError(f"{path}: string is shorter than {minimum}")
    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, int) and value < minimum:
            raise InventoryError(f"{path}: integer is below {minimum}")


def validate_registry(registry: dict[str, Any], *, schema: dict[str, Any]) -> list[dict[str, Any]]:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise InventoryError("checked-in Drive registry schema is not draft 2020-12")
    _schema_validate(registry, schema)
    if registry.get("schema_version") != 1 or registry.get("contract") != "drive-dependencies":
        raise InventoryError("registry must declare drive-dependencies schema_version 1")
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise InventoryError("registry.entries must be a non-empty list")
    seen: set[str] = set()
    binary_exclusions = registry.get("binary_exclusions")
    if not isinstance(binary_exclusions, list):
        raise InventoryError("registry.binary_exclusions must be an array")
    binary_patterns: set[str] = set()
    for exclusion in binary_exclusions:
        pattern = exclusion["path"]
        if pattern in binary_patterns:
            raise InventoryError(f"duplicate binary exclusion: {pattern}")
        binary_patterns.add(pattern)
    for entry in entries:
        if not isinstance(entry, dict):
            raise InventoryError("each registry entry must be an object")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise InventoryError("registry entry ids must be non-empty and unique")
        seen.add(identifier)
        if entry.get("class") not in VALID_CLASSES:
            raise InventoryError(f"{identifier}: invalid dependency class")
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources or not all(isinstance(x, str) and x for x in sources):
            raise InventoryError(f"{identifier}: sources must be non-empty path patterns")
        if not isinstance(entry.get("path_pattern"), str) or not entry["path_pattern"]:
            raise InventoryError(f"{identifier}: path_pattern is required")
        selectors = entry.get("reference_selectors")
        if selectors is not None:
            if not isinstance(selectors, list) or not selectors:
                raise InventoryError(f"{identifier}: reference_selectors must be a non-empty list when present")
            for selector in selectors:
                if not isinstance(selector, dict) or not isinstance(selector.get("source"), str) or not selector["source"] or not isinstance(selector.get("alias"), str) or not selector["alias"]:
                    raise InventoryError(f"{identifier}: each reference selector needs source and alias")
                if "excerpt" in selector and (not isinstance(selector["excerpt"], str) or not selector["excerpt"]):
                    raise InventoryError(f"{identifier}: selector excerpt must be a non-empty string")
                if "line" in selector and (not isinstance(selector["line"], int) or selector["line"] < 1):
                    raise InventoryError(f"{identifier}: selector line must be a positive integer")
        if entry["class"] in OPERATIONAL_CLASSES:
            for key in ("producer", "consumers", "canonicality", "replacement"):
                if key not in entry:
                    raise InventoryError(f"{identifier}: operational dependency requires {key}")
            if entry["canonicality"] not in {"canonical", "projection", "recovery_only"}:
                raise InventoryError(f"{identifier}: invalid canonicality")
    return entries


def _tracked_paths(root: Path) -> list[tuple[Path, str]]:
    """Use Git's tracked set; a non-repository test fixture falls back to its tree."""
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        return [(path, "100644") for path in sorted(root.rglob("*")) if path.is_file()]
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--stage", "-z"],
        capture_output=True, check=False,
    )
    if result.returncode == 0:
        tracked: list[tuple[Path, str]] = []
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            try:
                metadata, encoded_path = raw.split(b"\t", 1)
                mode, _object_id, stage = metadata.decode("ascii").split(" ")
                rel = encoded_path.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise InventoryError("git ls-files returned an undecodable tracked record") from exc
            if stage != "0":
                raise InventoryError(f"unmerged tracked path cannot be inventoried: {rel}")
            tracked.append((root / rel, mode))
        return tracked
    raise InventoryError("git ls-files --stage failed; tracked coverage is unavailable")


def _binary_matches(rel: str, exclusions: list[dict[str, Any]]) -> list[str]:
    return [str(item["path"]) for item in exclusions
            if fnmatch.fnmatchcase(rel, str(item["path"]))]


def _decode_text(rel: str, contents: bytes, exclusions: list[dict[str, Any]], used: set[str]) -> str | None:
    matches = _binary_matches(rel, exclusions)
    if len(matches) > 1:
        raise InventoryError(f"binary path has multiple exclusion rules: {rel}")
    if contents.startswith((b"\xff\xfe", b"\xfe\xff")):
        if matches:
            raise InventoryError(f"text file is incorrectly classified binary: {rel}")
        try:
            return contents.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise InventoryError(f"malformed UTF-16 tracked text: {rel}") from exc
    if b"\0" in contents:
        if not matches:
            raise InventoryError(f"ambiguous NUL-bearing tracked file lacks binary classification: {rel}")
        used.add(matches[0])
        return None
    if matches:
        raise InventoryError(f"binary exclusion matched a text-detectable file: {rel}")
    return contents.decode("utf-8", errors="replace")


def scan(root: Path, binary_exclusions: list[dict[str, Any]]) -> list[Reference]:
    refs: list[Reference] = []
    used_binary_rules: set[str] = set()
    for path, mode in _tracked_paths(root):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise InventoryError(f"tracked path escapes inventory root: {path}") from exc
        if mode == "160000":
            if rel not in GENERATED_GITLINKS:
                raise InventoryError(f"unregistered tracked Gitlink: {rel}")
            continue
        if rel in GENERATED_GITLINKS:
            raise InventoryError(f"registered Gitlink is no longer mode 160000: {rel}")
        if path.is_dir():
            raise InventoryError(f"tracked non-file is not an explicit mode-160000 Gitlink: {rel}")
        if path.is_symlink():
            raise InventoryError(f"tracked symbolic link cannot be safely scanned: {rel}")
        try:
            contents = path.read_bytes()
        except OSError as exc:
            raise InventoryError(f"cannot scan tracked file {rel}: {exc}") from exc
        text = _decode_text(rel, contents, binary_exclusions, used_binary_rules)
        if text is None:
            continue
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            for hit in ALIAS.finditer(line):
                refs.append(Reference(rel, number, hit.group(0),
                                      _resolved_reference_path(line, hit),
                                      line.strip()[:240]))
    declared = {str(item["path"]) for item in binary_exclusions}
    stale = sorted(declared - used_binary_rules)
    if stale:
        raise InventoryError(f"binary exclusion matched no NUL-bearing tracked file: {', '.join(stale)}")
    return refs


def _normalized_alias(alias: str) -> str:
    if alias in {"CARR_VAULT", "VAULT_DEFAULT", "DRIVE_ROOT"}:
        return "{{VAULT}}"
    if "GoogleDrive-" in alias or "My Drive/CARR AI" in alias:
        return "{{VAULT}}"
    return alias


def _resolved_reference_path(line: str, hit: re.Match[str]) -> str:
    suffix = line[hit.end():]
    # `${CARR_VAULT}/x` ends the regex hit before the closing brace.  Strip
    # exactly that syntax brace so the path suffix cannot collapse to root.
    if suffix.startswith("}"):
        suffix = suffix[1:]
    match = re.match(r"((?:/|%2F)[^\"'`)\],;]*)", suffix, flags=re.IGNORECASE)
    tail = unquote(match.group(1)).rstrip() if match else ""
    return _normalized_alias(hit.group(0)) + tail


def _expand_braces(pattern: str) -> list[str]:
    match = re.search(r"(?<!\{)\{([^{}]+)\}(?!\})", pattern)
    if not match:
        return [pattern]
    expanded: list[str] = []
    for choice in match.group(1).split(","):
        expanded.extend(_expand_braces(
            pattern[:match.start()] + choice + pattern[match.end():]))
    return expanded


def _path_matches(reference_path: str, declared: str) -> bool:
    for pattern in _expand_braces(declared):
        if fnmatch.fnmatchcase(reference_path, pattern):
            return True
        if reference_path == "{{VAULT}}" and pattern == "{{VAULT}}/**":
            return True
    return False


def matches(entry: dict[str, Any], reference: Reference) -> bool:
    """Match a classification to this exact source *and* Drive reference.

    Source globs alone are deliberately insufficient: the declared path must
    name the normalized alias found in the reference.  This prevents a broad
    file grouping from silently claiming a newly introduced alias as an
    unrelated dependency.
    """
    if not any(fnmatch.fnmatchcase(reference.file, source) for source in entry["sources"]):
        return False
    alias = _normalized_alias(reference.alias)
    if not _path_matches(reference.resolved_path, str(entry["path_pattern"])):
        return False
    selectors = entry.get("reference_selectors")
    if selectors is None:
        return True
    if not isinstance(selectors, list) or not selectors:
        return False
    for selector in selectors:
        if not isinstance(selector, dict):
            continue
        source = selector.get("source")
        alias_pattern = selector.get("alias")
        excerpt_pattern = selector.get("excerpt", "*")
        line = selector.get("line")
        if (isinstance(source, str) and isinstance(alias_pattern, str)
                and isinstance(excerpt_pattern, str)
                and fnmatch.fnmatchcase(reference.file, source)
                and fnmatch.fnmatchcase(alias, alias_pattern)
                and fnmatch.fnmatchcase(reference.excerpt, excerpt_pattern)
                and (line is None or line == reference.line)):
            return True
    return False


def audit(root: Path, registry: dict[str, Any]) -> tuple[list[Reference], list[Reference], list[tuple[Reference, list[str]]]]:
    entries = validate_registry(registry, schema=_read_json(_schema_path(root)))
    tracked = [path.relative_to(root).as_posix() for path, _mode in _tracked_paths(root)]
    stale = [f"{entry['id']}: {source}" for entry in entries for source in entry["sources"]
             if not any(fnmatch.fnmatchcase(rel, source) for rel in tracked)]
    if stale:
        raise InventoryError(f"sources match no tracked path: {'; '.join(stale)}")
    refs = scan(root, list(registry["binary_exclusions"]))
    uncovered: list[Reference] = []
    multiple: list[tuple[Reference, list[str]]] = []
    for ref in refs:
        ids = [str(entry["id"]) for entry in entries if matches(entry, ref)]
        if not ids:
            uncovered.append(ref)
        elif len(ids) > 1:
            multiple.append((ref, ids))
    return refs, uncovered, multiple


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--audit-only", action="store_true", help="print references without failing uncovered rows")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    registry_path = args.registry or root / DEFAULT_REGISTRY
    try:
        refs, uncovered, multiple = audit(root, _read_json(registry_path))
    except InventoryError as exc:
        print(f"drive-dependency-inventory FAILED: {exc}", file=sys.stderr)
        return 2
    for ref in refs:
        print(f"{ref.ref}\t{ref.alias}\t{ref.resolved_path}\t{ref.excerpt}")
    if multiple:
        for ref, ids in multiple:
            print(f"MULTIPLE {ref.ref}: {', '.join(ids)}", file=sys.stderr)
    if uncovered:
        for ref in uncovered:
            print(f"UNCOVERED {ref.ref}: {ref.alias}", file=sys.stderr)
    if args.audit_only:
        return 0
    if uncovered or multiple:
        print(f"drive-dependency-inventory FAILED: refs={len(refs)} uncovered={len(uncovered)} multiple={len(multiple)}", file=sys.stderr)
        return 1
    print(f"drive-dependency-inventory passed: {len(refs)} references exactly classified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
