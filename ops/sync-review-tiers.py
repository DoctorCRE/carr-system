#!/usr/bin/env python3
"""sync-review-tiers.py — render the review-tier map for the Worker and for
the cross-language test, and prove both renders are current.

doctrine: engineering-workflow-sop

WHY A GENERATED MODULE. The source-merge controller
(mcp-server/src/source-merge-policy.js) is imported by engineering-runtime.js,
which mcp.js imports, so it ships inside the Cloudflare Worker bundle, and a
Worker has no filesystem at request time. The map therefore ships as a
checked-in module, the same pattern as ops/sync-rule-boot-classes.py. Python
consumers read the JSON directly through lib/review_tiers.py.

TWO OUTPUTS, one source (ops/config/review-tiers.v1.json):
  mcp-server/src/review-tiers.generated.js      the map without its prose
  ops/fixtures/review-tiers/tier-vectors.v1.json tier and noise decision of
      the Python reader for a fixed probe set; the JS test asserts its own
      reader returns the same, which is what holds the two readers equal.

THIS SCRIPT IS THE ONLY WAY EITHER FILE IS MEANT TO BE PRODUCED, and --check
is the same code path, so the write and the parity check cannot drift apart
(rule a8c55a47). ops/review-tiers-selftest.py calls check().

Usage:
    python3 ops/sync-review-tiers.py            # regenerate both files
    python3 ops/sync-review-tiers.py --check    # exit 1 when either is stale
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_REL = "ops/config/review-tiers.v1.json"
MODULE_PATH = os.path.join(REPO, "mcp-server", "src", "review-tiers.generated.js")
VECTORS_PATH = os.path.join(REPO, "ops", "fixtures", "review-tiers", "tier-vectors.v1.json")
BASELINE_PATH = os.path.join(REPO, "ops", "fixtures", "review-tiers", "pre-change-baseline.v1.json")
ROW_KEYS = ("id", "tier", "class", "match", "pattern", "case_insensitive", "basename_prefix")


def _reader():
    spec = importlib.util.spec_from_file_location("review_tiers", os.path.join(REPO, "lib", "review_tiers.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strip(rows):
    """Rows without their prose, keys in a fixed order, absent keys absent."""
    return [{key: row[key] for key in ROW_KEYS if key in row} for row in rows]


def map_digest(doc):
    body = json.dumps({"default_tier": doc["default_tier"], "rules": _strip(doc["rules"]),
                       "noise_exclusions": _strip(doc["noise_exclusions"]),
                       "never_exclude": _strip(doc["never_exclude"]),
                       "test_files": _strip(doc.get("test_files", [])),
                       "change_size": doc.get("change_size"),
                       "tunable_scalars": doc.get("tunable_scalars", [])},
                      sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def render_module(doc):
    def block(name, rows):
        lines = [f"  {name}: Object.freeze(["]
        lines += [f"    Object.freeze({json.dumps(row, separators=(', ', ': '))})," for row in _strip(rows)]
        lines.append("  ]),")
        return lines

    lines = [
        "// GENERATED -- do not hand-edit.",
        f"// Source: {SOURCE_REL}",
        "// Regenerate with: python3 ops/sync-review-tiers.py",
        "// Drift check (ops/review-tiers-selftest.py): python3 ops/sync-review-tiers.py --check",
        "//",
        "// The review-tier map without its prose. mcp-server/src/review-tiers.js reads it;",
        "// the source-merge controller asks that reader. A Worker has no filesystem, so",
        "// this is a checked-in module.",
        "",
        f"export const REVIEW_TIERS_SOURCE = {json.dumps(SOURCE_REL)};",
        f"export const REVIEW_TIERS_DIGEST = {json.dumps(map_digest(doc))};",
        "",
        "export const REVIEW_TIERS = Object.freeze({",
        f"  default_tier: {int(doc['default_tier'])},",
        "  tunable_scalars: Object.freeze(" + json.dumps(doc.get("tunable_scalars", [])) + "),",
        *block("rules", doc["rules"]),
        *block("noise_exclusions", doc["noise_exclusions"]),
        *block("never_exclude", doc["never_exclude"]),
        *block("test_files", doc.get("test_files", [])),
        "  change_size: Object.freeze(" + json.dumps(doc["change_size"]) + "),",
        "});",
        "",
    ]
    return "\n".join(lines)


def probe_paths(doc):
    """A fixed probe set: every baseline path, plus one hit per rule, plus a
    near miss per rule, so each match kind is exercised in both languages."""
    with open(BASELINE_PATH, encoding="utf-8") as handle:
        probes = list(json.load(handle)["paths"])
    for row in doc["rules"] + doc["noise_exclusions"] + doc["never_exclude"] + doc.get("test_files", []):
        pattern, kind = row["pattern"], row["match"]
        if kind == "path":
            probes += [pattern, pattern + ".bak", "x/" + pattern]
        elif kind == "prefix":
            probes += [pattern + "probe.py", "x/" + pattern + "probe.py"]
        elif kind == "suffix":
            basename = row["basename_prefix"] + "probe" if "basename_prefix" in row else "x"
            probes += ["probe/" + basename + pattern, "probe/" + basename + pattern + ".txt"]
            if "basename_prefix" in row:
                probes += ["probe/x" + basename + pattern, "probe/" + basename + "/x" + pattern]
        elif kind == "basename":
            probes += ["probe/" + pattern, "probe/x" + pattern]
        elif kind == "contains":
            probes += ["probe/" + pattern + "/x.py", "probe/" + pattern.upper() + "/x.py"]
    seen, ordered = set(), []
    for path in probes:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def render_vectors(doc):
    rt = _reader()
    vectors = [{"path": p, "tier": rt.tier_for_path(p, doc), "noise": rt.is_review_noise(p, doc), "test": rt.is_test_file(p, doc)}
               for p in probe_paths(doc)]
    value = {
        "schema_version": "review-tier-vectors.v1",
        "generated_by": "ops/sync-review-tiers.py",
        "source": SOURCE_REL,
        "map_digest": map_digest(doc),
        "vectors": vectors,
        "change_vectors": [
            {"changes": [{"path": "lib/example.py", "additions": 2, "deletions": 1},
                         {"path": "tests/test_example.py", "additions": 400, "deletions": 0}],
             "summary": {"code_lines": 3, "test_lines": 400, "change_size": "small",
                         "code_paths": ["lib/example.py"], "test_paths": ["tests/test_example.py"]}, "path_tier": 1},
            {"changes": [{"path": "tests/test_example.py", "additions": 400, "deletions": 0}],
             "summary": {"code_lines": 0, "test_lines": 400, "change_size": "small",
                         "code_paths": [], "test_paths": ["tests/test_example.py"]}, "path_tier": 1},
            {"changes": [{"path": "ops/example-selftest.py", "additions": 400, "deletions": 0}],
             "summary": {"code_lines": 0, "test_lines": 400, "change_size": "small",
                         "code_paths": [], "test_paths": ["ops/example-selftest.py"]}, "path_tier": 3},
        ],
    }
    value["policy_digest"] = "sha256:" + hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for row in value["change_vectors"]:
        row["decision"] = rt.review_decision(row["changes"], base="a" * 40, head="b" * 40,
            policy_revision="c" * 40, diff_digest="sha256:" + "d" * 64, doc=doc)
    return json.dumps(value, indent=1) + "\n"


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def check():
    """Problems, empty when the map is valid and both renders are current."""
    rt = _reader()
    doc = rt.load()
    problems = [f"map: {p}" for p in rt.validate(doc)]
    if problems:
        return problems
    if _read(MODULE_PATH) != render_module(doc):
        problems.append(f"{os.path.relpath(MODULE_PATH, REPO)} is stale: run python3 ops/sync-review-tiers.py")
    if _read(VECTORS_PATH) != render_vectors(doc):
        problems.append(f"{os.path.relpath(VECTORS_PATH, REPO)} is stale: run python3 ops/sync-review-tiers.py")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        problems = check()
        for problem in problems:
            print(f"FAIL {problem}")
        if not problems:
            print("ok review-tier module and vectors are current")
        return 1 if problems else 0
    rt = _reader()
    doc = rt.load()
    problems = rt.validate(doc)
    if problems:
        for problem in problems:
            print(f"FAIL map: {problem}")
        return 1
    with open(MODULE_PATH, "w", encoding="utf-8") as handle:
        handle.write(render_module(doc))
    with open(VECTORS_PATH, "w", encoding="utf-8") as handle:
        handle.write(render_vectors(doc))
    print(f"wrote {os.path.relpath(MODULE_PATH, REPO)} and {os.path.relpath(VECTORS_PATH, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
