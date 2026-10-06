"""
rerender_observed_comments.py -- WR-000046 comparison seat (slice-compare).

Applies the diff_comments() fix documented in gen_frontier_manifest.py
(function-comment identities were rendered via pg_identify_object(), which
uses fully-schema-qualified built-in type names and drops parameter names,
instead of the pg_get_function_identity_arguments-based form used for every
other `function:` target in this same manifest) to the ALREADY-DELIVERED
docs/frontier-finding/frontier-touched-objects.v1.json, WITHOUT opening any
database connection.

This is a pure re-render: for every `comment:function:<schema>.<name>(<args>)`
target already present in the manifest, the schema-qualified bare name
(the text before the first "(") is looked up against the manifest's own
`targets` array, which already carries the CORRECT `function:<schema>.<name>
(<name> <type>, ...)` identity for that same function (captured from the real
pg_proc / pg_get_function_identity_arguments family, unaffected by this bug).
No function name in migrations/0454-0471 is overloaded (verified separately:
zero (schema, name) collisions across the 109 real functions), so bare-name
resolution is unambiguous. Three places in the JSON carry the wrong string
and are corrected in lockstep: `targets`, `by_file[<file>]`, and
`detail.comments[*].target`.

Usage:
    python3 rerender_observed_comments.py
Reads frontier-touched-objects.v1.original-from-seat.json (the untouched
artifact copied from the observation seat's worktree; never modified) and
writes frontier-touched-objects.v1.corrected.json.
"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_PATH = SCRIPT_DIR / "frontier-touched-objects.v1.original-from-seat.json"
OUT_PATH = SCRIPT_DIR / "frontier-touched-objects.v1.corrected.json"


def build_function_lookup(targets):
    lookup = {}
    for t in targets:
        if t.startswith("function:"):
            paren = t.index("(")
            bare = t[len("function:"):paren]
            if bare in lookup and lookup[bare] != t:
                raise SystemExit("REFUSING: overload collision on %r (%r vs %r) -- "
                                  "bare-name resolution is not safe here" % (bare, lookup[bare], t))
            lookup[bare] = t
    return lookup


def resolve_comment_target(target, lookup):
    if not target.startswith("comment:function:"):
        return target, False
    inner = target[len("comment:"):]  # "function:<schema>.<name>(<args>)"
    paren = inner.index("(")
    bare = inner[len("function:"):paren]
    resolved = lookup.get(bare)
    if resolved is None:
        return target, False
    new_target = "comment:%s" % resolved
    return new_target, new_target != target


def main(argv: list[str] | None = None) -> int:
    with open(SOURCE_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    lookup = build_function_lookup(manifest["targets"])

    changed = 0

    new_targets = []
    for t in manifest["targets"]:
        new_t, did_change = resolve_comment_target(t, lookup)
        new_targets.append(new_t)
        if did_change:
            changed += 1
    manifest["targets"] = sorted(set(new_targets), key=lambda s: s.encode("utf-8"))

    for filename, file_targets in manifest["by_file"].items():
        new_file_targets = []
        for t in file_targets:
            new_t, _ = resolve_comment_target(t, lookup)
            new_file_targets.append(new_t)
        manifest["by_file"][filename] = sorted(set(new_file_targets), key=lambda s: s.encode("utf-8"))

    for entry in manifest["detail"].get("comments", []):
        new_t, _ = resolve_comment_target(entry["target"], lookup)
        entry["target"] = new_t

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=True)
        f.write("\n")

    sys.stderr.write(
        "rerendered %d comment:function: targets -> %s\n" % (changed, OUT_PATH.name)
    )
    return 0
