#!/usr/bin/env python3
"""ops/rule-route-coverage.py — every active rule has a delivery route that can fire.

# doctrine: rule-delivery-load-layers

WHY THIS EXISTS. Joe demands 100% recall from rule delivery. The design that
answers it (ops/config/rule-routes.v1.json, read by lib/rule_routes.py) gives
every active rule at least one route — boot, trigger, path_rule, gate, or its
surviving duplicate. A route file can rot in exactly the ways that look
finished and deliver nothing, so this check refuses them structurally:

  1. AN ACTIVE RULE WITH ZERO ROUTES, or a corpus rule the route file never
     mentions. Either is a rule no session is ever shown at its moment.
  2. A TRIGGER NAMING A VERB OR TOOL THAT DOES NOT EXIST — a verb the server
     does not serve (read from the generated registry that
     mcp-server/src/mutation-registry.js imports, which every verb in
     mcp-server/src/tools.js and the modules it spreads must appear in),
     a tool Claude Code does not have, or a tool the delivering hook
     (hooks/rule-pack-preuse-reselection.py) is never invoked for, because its
     PreToolUse matcher in ops/config/hooks.json does not admit it. Each is a
     trigger that can never fire.
  3. A STALE ID: a route-file entry for a rule that is not in the corpus.
  4. A CLASS-D RULE WITHOUT ITS GATE, a gate file that does not exist, or a
     class-d rule enforced only by a Stop hook with no trigger in front of it
     (the model must see the rule before acting, not only be blocked after) —
     unless the entry says in words why no tool call marks its moment.
  5. A CLASS-E DUPLICATE that does not name a surviving rule which itself has
     a non-duplicate route.
  6. A BASH PATTERN THAT IS NOT A REGULAR EXPRESSION, a trigger that names
     nothing, an unknown route kind, or rule text carried in the route file
     (it holds ids and matchers only).

Repository content only: no network, no database, no machine state. It runs
in ops/ci.sh's gates inventory loop.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib import rule_routes  # noqa: E402

# Gates enforced outside this repository's files. Named, so an invented
# external gate still fails.
EXTERNAL_GATES = frozenset({"github-ruleset:main-pr-only"})


def _gate_exists(repo: Path, gate: str) -> bool:
    if gate in EXTERNAL_GATES:
        return True
    return (Path(repo) / gate).is_file()


def problems(repo: Path, doc: dict, *, corpus_ids=None, classes=None, verbs=None,
             matcher=None, stop_hooks=None) -> list[str]:
    repo = Path(repo)
    corpus = set(rule_routes.corpus_ids(repo) if corpus_ids is None else corpus_ids)
    classes = rule_routes.rule_classes(repo) if classes is None else classes
    verbs = rule_routes.known_verbs(repo) if verbs is None else set(verbs)
    matcher = rule_routes.hook_matcher(repo) if matcher is None else matcher
    stops = rule_routes.stop_hooks(repo) if stop_hooks is None else set(stop_hooks)
    rules = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(rules, dict):
        return ["route file: no rules object"]
    out: list[str] = []
    if doc.get("schema") != rule_routes.ROUTES_SCHEMA:
        out.append(f"route file: schema is not {rule_routes.ROUTES_SCHEMA}")

    for rid in sorted(corpus - set(rules)):
        out.append(f"{rid}: in the corpus but missing from the route file")
    for rid in sorted(set(rules) - corpus):
        out.append(f"{rid}: in the route file but not an active corpus rule (stale id)")

    def delivers(rid: str) -> bool:
        entry = rules.get(rid)
        return isinstance(entry, dict) and any(
            isinstance(r, dict) and r.get("kind") in rule_routes.ROUTE_KINDS - {"duplicate"}
            for r in entry.get("routes") or ())

    for rid in sorted(rules):
        entry = rules[rid]
        if not isinstance(entry, dict):
            out.append(f"{rid}: route entry is not an object")
            continue
        extra = set(entry) - rule_routes.ENTRY_KEYS
        if extra:
            out.append(f"{rid}: route entry carries keys outside "
                       f"{sorted(rule_routes.ENTRY_KEYS)}: {sorted(extra)}")
        routes = entry.get("routes")
        if not isinstance(routes, list) or not routes:
            out.append(f"{rid}: no delivery route")
            continue
        cls = (classes.get(rid) or {}).get("class")
        kinds = []
        gates = []
        for route in routes:
            kind = route.get("kind") if isinstance(route, dict) else None
            kinds.append(kind)
            if kind not in rule_routes.ROUTE_KINDS:
                out.append(f"{rid}: unknown route kind {kind!r}")
                continue
            if kind == "trigger":
                shapes = [key for key in rule_routes.TRIGGER_KEYS
                          if not isinstance(route.get(key) or [], list)
                          or not all(isinstance(x, str) and x for x in route.get(key) or ())]
                if shapes:
                    out.append(f"{rid}: trigger {shapes} must be lists of non-empty strings")
                    continue
                names = [x for key in rule_routes.TRIGGER_KEYS for x in route.get(key) or ()]
                if not names:
                    out.append(f"{rid}: trigger route names no tool, verb or bash pattern")
                for tool in route.get("tools") or ():
                    if not rule_routes.tool_known(tool):
                        out.append(f"{rid}: trigger names tool {tool!r} which is not a known "
                                   "Claude Code or connector tool")
                    elif not rule_routes.tool_admitted(tool, matcher):
                        out.append(f"{rid}: trigger names tool {tool!r} which the delivering "
                                   f"hook's matcher {matcher!r} never admits")
                for verb in route.get("verbs") or ():
                    if verb not in verbs:
                        out.append(f"{rid}: trigger names unknown verb {verb!r}")
                for pattern in route.get("bash_patterns") or ():
                    try:
                        re.compile(pattern)
                    except re.error:
                        out.append(f"{rid}: bash pattern {pattern!r} is not a valid "
                                   "regular expression")
                if route.get("bash_patterns") and not rule_routes.tool_admitted("Bash", matcher):
                    out.append(f"{rid}: bash pattern trigger but the hook never sees Bash")
            elif kind == "path_rule":
                globs = route.get("path_globs") or []
                if (not isinstance(globs, list) or not globs
                        or not all(isinstance(g, str) and g for g in globs)):
                    out.append(f"{rid}: path_rule names no glob")
            elif kind == "gate":
                gate = route.get("gate")
                if not isinstance(gate, str) or not gate:
                    out.append(f"{rid}: gate route names no gate")
                elif not _gate_exists(repo, gate):
                    out.append(f"{rid}: gate {gate!r} does not exist")
                else:
                    gates.append(gate)
            elif kind == "duplicate":
                survivor = route.get("survivor")
                if survivor == rid or not delivers(survivor):
                    out.append(f"{rid}: duplicate survivor {survivor!r} is not a routed rule "
                               "with a delivery of its own")
        if cls == "d" and not gates:
            out.append(f"{rid}: class d rule has no gate route")
        if cls == "e" and "duplicate" not in kinds:
            out.append(f"{rid}: class e rule does not name its surviving duplicate")
        stop_only = gates and all(Path(g).name in stops for g in gates)
        if (cls == "d" and stop_only
                and not {"trigger", "path_rule"} & set(kinds)
                and not str(entry.get("no_trigger_reason") or "").strip()):
            out.append(f"{rid}: enforced only by Stop hook {gates} with no trigger route "
                       "and no no_trigger_reason; the model must see it before acting")
    return out


def boot_only_outside_layer0(doc: dict, load_layers: dict | None = None) -> list[str]:
    """Boot-only rules the session-start set may not carry: a `boot` route is
    only as good as the boot layer that loads it, and today that is layer0 (a
    pack-layer rule reaches a session only when its pack is declared). REPORTED,
    not failed: the boot layer itself (lib/rule_boot.py) lands separately, and
    this list is what it must cover or what must gain a trigger."""
    if load_layers is None:
        try:
            load_layers = json.loads((REPO / "ops/config/rule-enforcement-map.json").read_text(
                encoding="utf-8")).get("rule_load_layers") or {}
        except (OSError, ValueError):
            load_layers = {}
    out = []
    for rid, entry in sorted((doc.get("rules") or {}).items()):
        kinds = {r.get("kind") for r in entry.get("routes") or () if isinstance(r, dict)}
        if kinds == {"boot"} and (load_layers.get(rid) or {}).get("load_layer") != "layer0":
            out.append(rid)
    return out


def counts(doc: dict) -> dict:
    out: dict[str, int] = {}
    for entry in (doc.get("rules") or {}).values():
        for kind in sorted({r.get("kind") for r in entry.get("routes") or ()}):
            out[kind] = out.get(kind, 0) + 1
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--routes", help="a route file other than the committed one")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        doc = (json.loads(Path(args.routes).read_text(encoding="utf-8")) if args.routes
               else rule_routes.load_routes(REPO))
    except (OSError, ValueError) as exc:
        print(f"rule-route-coverage: could not read the route file: {exc}", file=sys.stderr)
        return 1
    found = problems(REPO, doc)
    if args.json:
        print(json.dumps({"ok": not found, "problems": found, "counts": counts(doc),
                          "boot_only_outside_layer0": boot_only_outside_layer0(doc)},
                         indent=2, sort_keys=True))
        return 1 if found else 0
    if found:
        print(f"rule-route-coverage: FAIL — {len(found)} problem(s)", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 1
    rules = doc.get("rules") or {}
    print(f"rule-route-coverage: OK — {len(rules)} rules routed; routes by kind "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts(doc).items())))
    outside = boot_only_outside_layer0(doc)
    if outside:
        print(f"rule-route-coverage: NOTE — {len(outside)} boot-only rule(s) are not layer0, so "
              "they reach a session only when their pack is declared or the boot layer "
              "carries them: " + ", ".join(outside))
    return 0


if __name__ == "__main__":
    sys.exit(main())
