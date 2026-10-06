#!/usr/bin/env python3
"""rule-route-coverage-selftest.py — the paired suite for ops/rule-route-coverage.py.

Written before the check. Builds its own small corpus, class table, verb
registry and route file, proves the clean fixture passes, then plants one
mutant at a time and requires the check to KILL each one by name. The real
repository's route file is checked last, because that is the file that
actually decides what a session is shown.

Jev ranked the mutants for this gate (verification_selection, 2026-09-26; the
probability that a mutant is both realistic and silently costs recall): a
corpus rule the route file never mentions (0.86), an active rule with no route
(0.77), a trigger naming a verb the registry does not have (0.73), a duplicate
whose survivor has no delivery of its own (0.68), a tool the delivering hook is
never invoked for (0.67), a Stop-only class-d rule with no trigger in front of
it (0.67), rule text carried in the route file (0.58), an invalid bash regex
(0.54), an unknown tool (0.53), a missing gate file (0.51), an empty trigger
(0.51), and a stale id (0.39). The top six are the core; every one is planted
and must be killed.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


coverage = load("rule_route_coverage_selftest_target", REPO / "ops/rule-route-coverage.py")
routes_lib = load("rule_routes_selftest_lib", REPO / "lib/rule_routes.py")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")


# ---------------------------------------------------------------- fixture

CORPUS = ["aaaa0001", "aaaa0002", "aaaa0003", "aaaa0004", "aaaa0005", "aaaa0006"]
CLASSES = {
    "aaaa0001": {"class": "a", "layer": "layer0", "moment": "any turn"},
    "aaaa0002": {"class": "b", "layer": "pack", "moment": "spawning an agent"},
    "aaaa0003": {"class": "c", "layer": "pack", "moment": "deal work"},
    "aaaa0004": {"class": "d", "layer": "control", "moment": "a stop gate"},
    "aaaa0005": {"class": "e", "layer": "pack", "moment": "duplicate of aaaa0003"},
    "aaaa0006": {"class": "d", "layer": "control", "moment": "a pre-use gate"},
}
VERBS = {"new-deal", "update-deal", "close-loop", "add-loop"}
MATCHER = "Bash|Write|Edit|MultiEdit|Agent|WebFetch|WebSearch|Artifact|mcp__.*"
STOP_HOOKS = {"completion-evidence-gate.py"}

CLEAN = {
    "schema": routes_lib.ROUTES_SCHEMA,
    "rules": {
        "aaaa0001": {"moment": "any turn", "routes": [{"kind": "boot"}]},
        "aaaa0002": {"moment": "spawning an agent", "routes": [
            {"kind": "trigger", "tools": ["Agent"], "verbs": [], "bash_patterns":
             [r"\bcodex\s+exec\b"]}]},
        "aaaa0003": {"moment": "deal work", "routes": [
            {"kind": "trigger", "tools": ["mcp__*__create_draft"],
             "verbs": ["new-deal", "update-deal"], "bash_patterns": []},
            {"kind": "path_rule", "path_globs": ["*dealroom/*.html"]}]},
        "aaaa0004": {"moment": "a stop gate", "routes": [
            {"kind": "gate", "gate": "hooks/completion-evidence-gate.py"},
            {"kind": "trigger", "tools": [], "verbs": ["close-loop"],
             "bash_patterns": [r"\bgit\s+push\b"]}]},
        "aaaa0005": {"moment": "duplicate of aaaa0003", "routes": [
            {"kind": "duplicate", "survivor": "aaaa0003"}]},
        "aaaa0006": {"moment": "a pre-use gate", "routes": [
            {"kind": "gate", "gate": "hooks/weekend-quiet-gate.py"}]},
    },
}


def run(doc: dict, **overrides) -> list[str]:
    kwargs = dict(corpus_ids=CORPUS, classes=CLASSES, verbs=VERBS, matcher=MATCHER,
                  stop_hooks=STOP_HOOKS)
    kwargs.update(overrides)
    return coverage.problems(REPO, doc, **kwargs)


clean_problems = run(CLEAN)
check("the clean fixture passes", clean_problems == [], clean_problems)


def mutant(name: str, mutate, expect: str, **overrides) -> None:
    doc = copy.deepcopy(CLEAN)
    mutate(doc)
    found = run(doc, **overrides)
    check(f"mutant killed: {name}", any(expect in line for line in found), found)


mutant("active rule with zero routes",
       lambda d: d["rules"]["aaaa0002"].__setitem__("routes", []),
       "aaaa0002: no delivery route")
mutant("corpus rule missing from the route file",
       lambda d: d["rules"].pop("aaaa0003"),
       "aaaa0003: in the corpus but missing from the route file")
mutant("trigger names a verb the registry does not have",
       lambda d: d["rules"]["aaaa0003"]["routes"][0]["verbs"].append("summon-deal"),
       "aaaa0003: trigger names unknown verb 'summon-deal'")
mutant("stale id in the route file",
       lambda d: d["rules"].__setitem__("deadbeef", {"moment": "x", "routes": [{"kind": "boot"}]}),
       "deadbeef: in the route file but not an active corpus rule")
mutant("tool the delivering hook is never invoked for",
       lambda d: d["rules"]["aaaa0002"]["routes"][0]["tools"].append("Read"),
       "aaaa0002: trigger names tool 'Read'")
mutant("tool name that exists nowhere",
       lambda d: d["rules"]["aaaa0002"]["routes"][0]["tools"].append("Teleport"),
       "aaaa0002: trigger names tool 'Teleport'")
mutant("connector tool that is not a known connector tool",
       lambda d: d["rules"]["aaaa0003"]["routes"][0]["tools"].append("mcp__*__levitate"),
       "aaaa0003: trigger names tool 'mcp__*__levitate'")
mutant("gate route points at a file that does not exist",
       lambda d: d["rules"]["aaaa0006"]["routes"][0].__setitem__("gate", "hooks/no-such-gate.py"),
       "aaaa0006: gate 'hooks/no-such-gate.py' does not exist")
mutant("class-d rule with no gate route",
       lambda d: d["rules"]["aaaa0006"].__setitem__("routes", [
           {"kind": "trigger", "tools": ["Agent"], "verbs": [], "bash_patterns": []}]),
       "aaaa0006: class d rule has no gate route")
mutant("Stop-hook-only class-d rule with no trigger in front of it",
       lambda d: d["rules"]["aaaa0004"].__setitem__("routes", [d["rules"]["aaaa0004"]["routes"][0]]),
       "aaaa0004: enforced only by Stop hook")
mutant("duplicate whose survivor has no delivery of its own",
       lambda d: d["rules"]["aaaa0005"]["routes"][0].__setitem__("survivor", "aaaa0006x"),
       "aaaa0005: duplicate survivor 'aaaa0006x'")
mutant("class-e rule with no duplicate route",
       lambda d: d["rules"]["aaaa0005"].__setitem__("routes", [{"kind": "boot"}]),
       "aaaa0005: class e rule does not name its surviving duplicate")
mutant("bash pattern that is not a valid regular expression",
       lambda d: d["rules"]["aaaa0002"]["routes"][0]["bash_patterns"].append("(unclosed"),
       "aaaa0002: bash pattern '(unclosed' is not a valid regular expression")
mutant("trigger route that matches nothing at all",
       lambda d: d["rules"]["aaaa0002"]["routes"].__setitem__(
           0, {"kind": "trigger", "tools": [], "verbs": [], "bash_patterns": []}),
       "aaaa0002: trigger route names no tool, verb or bash pattern")
mutant("unknown route kind",
       lambda d: d["rules"]["aaaa0002"]["routes"].append({"kind": "vibes"}),
       "aaaa0002: unknown route kind 'vibes'")
mutant("route file carrying rule text",
       lambda d: d["rules"]["aaaa0002"].__setitem__("statement", "the whole rule"),
       "aaaa0002: route entry carries keys outside")

mutant("non-string tool entry (would crash a naive matcher)",
       lambda d: d["rules"]["aaaa0002"]["routes"][0]["tools"].append(7),
       "aaaa0002: trigger ['tools'] must be lists of non-empty strings")
mutant("null path glob",
       lambda d: d["rules"]["aaaa0003"]["routes"][1]["path_globs"].append(None),
       "aaaa0003: path_rule names no glob")
mutant("empty path glob",
       lambda d: d["rules"]["aaaa0003"]["routes"][1].__setitem__("path_globs", [""]),
       "aaaa0003: path_rule names no glob")

# Boot-only rules outside layer0 are reported (not failed) for the boot layer.
check("boot-only rules outside layer0 are reported",
      coverage.boot_only_outside_layer0(CLEAN, {"aaaa0001": {"load_layer": "pack"}})
      == ["aaaa0001"])
check("a layer0 boot-only rule is not reported",
      coverage.boot_only_outside_layer0(CLEAN, {"aaaa0001": {"load_layer": "layer0"}}) == [])

# An exemption must be explicit and reasoned, never a silent pass.
exempt = copy.deepcopy(CLEAN)
exempt["rules"]["aaaa0004"]["routes"] = [exempt["rules"]["aaaa0004"]["routes"][0]]
exempt["rules"]["aaaa0004"]["no_trigger_reason"] = "the moment is message composition; no tool fires"
check("a Stop-only class-d rule with a written no_trigger_reason passes",
      run(exempt) == [], run(exempt))

# The registry reader follows the stable current projection as well as legacy fixtures.
with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / 'mcp-server/src'
    src.mkdir(parents=True)
    (src / 'mutation-registry.js').write_text('export {SCAC_MUTATION_OPERATIONS} from "./scac-mutation-registry.current.generated.js";\n')
    (src / 'scac-mutation-registry.current.generated.js').write_text('{"ingress_key":"mcp-tool:example"}\n')
    check("verb reader follows the current projection", routes_lib.known_verbs(Path(tmp)) == {'example'})

# The verb registry reader is a real reader, not a fixture: it must see verbs
# declared in tools.js AND in the modules tools.js spreads in.
real_verbs = routes_lib.known_verbs(REPO)
check("verb registry reader finds verbs from tools.js and its modules",
      {"new-deal", "standing-context", "map-architecture", "teach", "add-loop"} <= real_verbs,
      sorted(real_verbs)[:10])
check("verb registry reader does not invent a verb", "summon-deal" not in real_verbs)

# The hook matcher reader returns the delivering hook's real matcher.
matcher = routes_lib.hook_matcher(REPO)
check("hook matcher reader finds the JIT rail's registration",
      isinstance(matcher, str) and "mcp__" in matcher and "Agent" in matcher, matcher)

# The real repository: every active rule routed, every matcher resolvable.
real = coverage.problems(REPO, routes_lib.load_routes(REPO))
check("the committed route file passes the coverage gate", real == [], real[:20])

# The command line exits nonzero on a planted mutant, zero on the real file.
with tempfile.TemporaryDirectory() as tmp:
    bad = copy.deepcopy(routes_lib.load_routes(REPO))
    first = sorted(bad["rules"])[0]
    bad["rules"][first]["routes"] = []
    path = Path(tmp) / "routes.json"
    path.write_text(json.dumps(bad))
    rc_bad = coverage.main(["--routes", str(path)])
    rc_good = coverage.main([])
check("command line fails on a planted unrouted rule", rc_bad == 1, rc_bad)
check("command line passes on the committed route file", rc_good == 0, rc_good)

if FAILURES:
    print("rule-route-coverage-selftest: FAIL")
    for failure in FAILURES:
        print("  " + failure)
    raise SystemExit(1)
print("rule-route-coverage-selftest: all cases passed")
