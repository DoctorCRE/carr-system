#!/usr/bin/env python3
"""Render the retained JavaScript review-tier map from its JSON source.

Python callers no longer classify review tiers. The existing parity vectors
remain fixtures for the JavaScript reader and are not generated here.

Usage:
    python3 ops/sync-review-tiers.py
    python3 ops/sync-review-tiers.py --check
"""
import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_REL = "ops/config/review-tiers.v1.json"
MODULE_PATH = os.path.join(REPO, "mcp-server", "src", "review-tiers.generated.js")
ROW_KEYS = ("id", "tier", "class", "match", "pattern", "case_insensitive", "basename_prefix")


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


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def check():
    """Problems, empty when the retained JavaScript render is current."""
    with open(os.path.join(REPO, SOURCE_REL), encoding="utf-8") as handle:
        doc = json.load(handle)
    problems = []
    if _read(MODULE_PATH) != render_module(doc):
        problems.append(f"{os.path.relpath(MODULE_PATH, REPO)} is stale: run python3 ops/sync-review-tiers.py")
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
            print("ok retained review-tier module is current")
        return 1 if problems else 0
    with open(os.path.join(REPO, SOURCE_REL), encoding="utf-8") as handle:
        doc = json.load(handle)
    with open(MODULE_PATH, "w", encoding="utf-8") as handle:
        handle.write(render_module(doc))
    print(f"wrote {os.path.relpath(MODULE_PATH, REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
