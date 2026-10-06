"""review_tiers — the Python reader for ops/config/review-tiers.v1.json.

doctrine: engineering-workflow-sop

One map decides how much review a changed path gets (engineering-workflow-sop
section 15, "Risk tier by path"). Before it, four places each kept their own
list and they disagreed. The consumers now ask this module:

    ops/jev_done_checks.py        review triage floors a file "high" at tier 3
    pipelines/run_codex_review.py council security lens arms at tier 2
    ops/jev_code_partition.py     drops review noise before a model reads code

The Worker's merge controller cannot read files at request time, so it reads
mcp-server/src/review-tiers.generated.js, which ops/sync-review-tiers.py
renders from the same JSON and checks for drift. The matching below and the
JS reader in mcp-server/src/review-tiers.js implement the same five string
operations; tier-vectors.v1.json and both test suites hold them equal.

Tier 3 never blocks a human: it means the merge controller declines to
auto-merge and a human presses merge (decision 8daefaba).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from functools import lru_cache
from typing import Iterable

REPO = os.getcwd() if __file__ == "<stdin>" else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(REPO, "ops", "config", "review-tiers.v1.json")
SCHEMA_VERSION = "review-tiers.v1"
MATCH_KINDS = ("path", "prefix", "suffix", "basename", "contains")
CLASSES = ("protected", "security_sensitive", "adversarial")
TIERS = (1, 2, 3)
TOP_TIER = 3

# Consumer thresholds, one place. The map says what a path IS; these say what
# each consumer does about it.
MERGE_REFUSAL_TIER = 3
TRIAGE_HIGH_FLOOR_TIER = 3
SECURITY_LENS_TIER = 2


def _is_tier(value) -> bool:
    """An int tier; a bool is refused even though True == 1 in Python."""
    return type(value) is int and value in TIERS


def validate(doc) -> list[str]:
    """Problems with a map document; empty means well formed."""
    problems = []
    if not isinstance(doc, dict):
        return ["map must be an object"]
    if doc.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("purpose", "provenance"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            problems.append(f"{key} missing")
    if not _is_tier(doc.get("default_tier")):
        problems.append("default_tier must be 1, 2 or 3")

    def check_rows(name, rows, tiered):
        if not isinstance(rows, list) or not rows:
            problems.append(f"{name} must be a non-empty list")
            return
        seen = set()
        for index, row in enumerate(rows):
            where = f"{name}[{index}]"
            if not isinstance(row, dict):
                problems.append(f"{where} must be an object")
                continue
            rid = row.get("id")
            if not isinstance(rid, str) or not rid:
                problems.append(f"{where}: id missing")
            elif rid in seen:
                problems.append(f"{where}: duplicate id {rid}")
            seen.add(rid)
            if row.get("match") not in MATCH_KINDS:
                problems.append(f"{where}: match must be one of {MATCH_KINDS}")
            if not isinstance(row.get("pattern"), str) or not row["pattern"]:
                problems.append(f"{where}: pattern missing")
            if "case_insensitive" in row and not isinstance(row["case_insensitive"], bool):
                problems.append(f"{where}: case_insensitive must be a boolean")
            if "basename_prefix" in row and (not isinstance(row["basename_prefix"], str)
                                             or not row["basename_prefix"]):
                problems.append(f"{where}: basename_prefix must be a non-empty string")
            if not isinstance(row.get("why"), str) or not row["why"].strip():
                problems.append(f"{where}: why missing")
            if tiered:
                if not _is_tier(row.get("tier")):
                    problems.append(f"{where}: tier must be 1, 2 or 3")
                if row.get("class") not in CLASSES:
                    problems.append(f"{where}: class must be one of {CLASSES}")

    check_rows("rules", doc.get("rules"), True)
    check_rows("noise_exclusions", doc.get("noise_exclusions"), False)
    check_rows("never_exclude", doc.get("never_exclude"), False)
    if "test_files" in doc:
        check_rows("test_files", doc["test_files"], False)
    if "change_size" in doc:
        size = doc["change_size"]
        if (not isinstance(size, dict) or set(size) != {"small_max_code_lines", "medium_max_code_lines"}
                or any(type(v) is not int or v < 0 for v in size.values())
                or size["small_max_code_lines"] > size["medium_max_code_lines"]):
            problems.append("change_size: expected ordered nonnegative integer limits")
    tunables = doc.get("tunable_scalars", [])
    if not isinstance(tunables, list):
        problems.append("tunable_scalars must be a list")
        tunables = []
    seen_tunables = set()
    for row in tunables:
        if (not isinstance(row, dict) or set(row) != {"path", "field", "minimum", "maximum"}
                or not isinstance(row["path"], str) or not row["path"]
                or not isinstance(row["field"], str) or not row["field"]
                or type(row["minimum"]) is not int or type(row["maximum"]) is not int
                or row["minimum"] < 0 or row["maximum"] < row["minimum"]):
            problems.append("tunable_scalars: expected a named integer field and nonnegative range")
        elif (row["path"], row["field"]) in seen_tunables:
            problems.append("tunable_scalars: duplicate path/field")
        else:
            seen_tunables.add((row["path"], row["field"]))
    return problems


def load(path: str = MAP_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _map() -> dict:
    doc = load()
    problems = validate(doc)
    if problems:
        raise ValueError(f"{MAP_PATH} is malformed: {problems[:3]}")
    return doc


def normalize(path) -> str | None:
    """Repo-relative, forward slashes, no leading './'. None when unusable."""
    if not isinstance(path, str) or not path:
        return None
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path or None


def matches(row: dict, path: str) -> bool:
    pattern = row["pattern"]
    subject = path
    basename_prefix = row.get("basename_prefix", "")
    if row.get("case_insensitive"):
        subject, pattern = subject.lower(), pattern.lower()
        basename_prefix = basename_prefix.lower()
    if not subject.rsplit("/", 1)[-1].startswith(basename_prefix):
        return False
    kind = row["match"]
    if kind == "path":
        return subject == pattern
    if kind == "prefix":
        return subject.startswith(pattern)
    if kind == "suffix":
        return subject.endswith(pattern)
    if kind == "basename":
        return subject.rsplit("/", 1)[-1] == pattern
    if kind == "contains":
        return pattern in subject
    raise ValueError(f"unknown match kind {kind!r}")


def tier_for_path(path, doc: dict | None = None) -> int:
    """The highest tier of every rule matching `path`, else the default.
    A path that cannot be read is the top tier: fail toward more review."""
    doc = doc or _map()
    normal = normalize(path)
    if normal is None:
        return TOP_TIER
    tier = doc["default_tier"]
    for row in doc["rules"]:
        if row["tier"] > tier and matches(row, normal):
            tier = row["tier"]
    return tier


def tier_for_paths(paths: Iterable, doc: dict | None = None) -> int:
    """A change set takes the highest tier of its paths."""
    doc = doc or _map()
    return max((tier_for_path(p, doc) for p in paths), default=doc["default_tier"])


def is_test_file(path, doc: dict | None = None) -> bool:
    doc = _map() if doc is None else doc
    normal = normalize(path)
    return normal is not None and any(matches(row, normal) for row in doc.get("test_files", []))


def change_summary(changes, doc=None):
    doc = _map() if doc is None else doc
    groups = {"code": [], "test": []}
    lines = {"code": 0, "test": 0}
    for change in changes:
        group = "test" if is_test_file(change["path"], doc) else "code"
        groups[group].append(change["path"])
        counts = [change.get(key) for key in ("additions", "deletions")]
        if any(type(v) is not int or v < 0 for v in counts):
            lines[group] = None
        elif lines[group] is not None:
            lines[group] += sum(counts)
    limits = doc.get("change_size")
    code = lines["code"]
    size = ("unknown" if code is None or limits is None else
            "small" if code <= limits["small_max_code_lines"] else
            "medium" if code <= limits["medium_max_code_lines"] else "large")
    return {"code_lines": code, "test_lines": lines["test"], "change_size": size,
            "code_paths": groups["code"], "test_paths": groups["test"]}


def is_review_noise(path, doc: dict | None = None) -> bool:
    """True when `path` is dropped before a model reads a diff. Never lowers a tier."""
    doc = doc or _map()
    normal = normalize(path)
    if normal is None:
        return False
    if is_test_file(normal, doc) or any(matches(row, normal) for row in doc["never_exclude"]):
        return False
    return any(matches(row, normal) for row in doc["noise_exclusions"])


def review_decision(changes, *, base, head, policy_revision, diff_digest, doc=None):
    """Dispatch classification only. Path-based merge and security controls stay intact."""
    doc = _map() if doc is None else doc
    problems = validate(doc)
    if problems:
        raise ValueError(f"invalid review policy: {problems}")
    summary = change_summary(changes, doc)
    bounded = bool(changes) and not summary["test_paths"]
    fields = []
    for change in changes:
        if change.get("mode_changed"):
            bounded = False
        before, after = change.get("before"), change.get("after")
        if not isinstance(before, dict) or not isinstance(after, dict) or before.keys() != after.keys():
            bounded = False
            continue
        changed = [key for key in before if json.dumps(before[key], sort_keys=True) !=
                   json.dumps(after[key], sort_keys=True)]
        if not changed:
            bounded = False
        for field in changed:
            rule = next((r for r in doc.get("tunable_scalars", [])
                         if r["path"] == change["path"] and r["field"] == field), None)
            if not rule or not all(type(v) is int and rule["minimum"] <= v <= rule["maximum"]
                                   for v in (before[field], after[field])):
                bounded = False
            else:
                fields.append({"path": change["path"], "field": field,
                               "before": before[field], "after": after[field]})
    return {"schema": "repository-review-decision/v1", "base": base, "head": head,
            "policy_revision": policy_revision, "diff_digest": diff_digest,
            "policy_digest": "sha256:" + hashlib.sha256(json.dumps(doc, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest(),
            "changed_paths": [c["path"] for c in changes], **summary,
            "lane": "tunable_scalar" if bounded else "review",
            "tier": 1 if bounded else max(tier_for_paths([c["path"] for c in changes], doc),
                                             2 if summary["test_paths"] else 1),
            "validated_fields": fields if bounded else [], "required_ci": True}


def _strict_json(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8")

    def nonfinite(_):
        raise ValueError("nonfinite JSON")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON field")
            value[key] = item
        return value
    return json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)


def main():
    parser = argparse.ArgumentParser(description="Revision-bound repository review decision")
    for name in ("base", "head", "policy-revision"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    def git(*argv):
        return subprocess.run(["git", *argv], cwd=REPO, capture_output=True, check=True, timeout=30).stdout
    base, head, policy = [git("rev-parse", "--verify", value + "^{commit}").decode().strip()
                          for value in (args.base, args.head, args.policy_revision)]
    doc = _strict_json(git("show", f"{policy}:ops/config/review-tiers.v1.json"))
    paths = git("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", base, head).decode().split("\0")[:-1]
    def content(revision, path):
        entry = git("ls-tree", revision, "--", path).decode()
        if not entry.startswith(("100644 blob ", "100755 blob ")):
            return None
        try:
            return _strict_json(git("show", f"{revision}:{path}"))
        except ValueError:
            return None
    counts = {}
    for entry in git("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--numstat", "-z", base, head).decode().split("\0"):
        if entry:
            added, deleted, path = entry.split("\t", 2)
            counts[path] = {"additions": None if added == "-" else int(added),
                            "deletions": None if deleted == "-" else int(deleted)}
    changes = [{"path": path, **counts[path], "before": content(base, path), "after": content(head, path),
                "mode_changed": git("ls-tree", base, "--", path)[:6] !=
                                git("ls-tree", head, "--", path)[:6]} for path in paths]
    diff = git("diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames", base, head)
    print(json.dumps(review_decision(changes, base=base, head=head, policy_revision=policy,
                                    diff_digest="sha256:" + hashlib.sha256(diff).hexdigest(), doc=doc)))


if __name__ == "__main__":
    main()
