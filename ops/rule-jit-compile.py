#!/usr/bin/env python3
"""rule-jit-compile.py — compile ops/config/rule-jit-triggers.v1.json from its
declared inputs, never by hand (WR-000019 slice S9).

WHY THIS EXISTS. Slice S9 generalizes the pre-tool-call rule-delivery rail
(hooks/rule-pack-preuse-reselection.py) beyond its one proven shape — an exact
top-level `run_in_background: true` Bash/functions.exec call bound to the
`scheduled-automation` pack — to a declarative trigger table covering MCP verb
calls, Bash command families, file-path writes, and a general content fallback.
That table is a COMPILED artifact, the same discipline rule-triage.v1.json
itself follows (S7's own docstring: "no rule was retired, amended, or taught by
producing this file") and the same reasoning rule a8c55a47 states for any
derived-vs-source pair: a manual and an automated path producing the same
shape must be the same code, so this script is the ONLY writer of
ops/config/rule-jit-triggers.v1.json.

INPUTS, all read-only and already reviewed elsewhere:
  ops/config/rule-triage.v1.json       — jit-home rows carry an optional
                                          `detector` field (kind, pattern),
                                          seeded for ~49 of the 126 JIT rules
                                          across five priority lanes (deal/LOI,
                                          vendor intros, council procedure,
                                          source study, comms) plus engineering
                                          git, using each rule's own
                                          jit_trigger_hint content. An optional
                                          top-level `fallback_narrowing` object
                                          (written only by
                                          ops/rule-delivery-adjudication-apply.py)
                                          strips proven false-positive keywords
                                          out of a pack's fallback pattern.
  ops/config/rule-enforcement-map.json — read-only for two things: `rule_packs`
                                          (the existing keyword lists, reused
                                          verbatim as the CONTENT of a pack's
                                          fallback trigger — this script never
                                          edits this file) and
                                          `rule_load_layers[id].packs` (JIT-rule
                                          pack membership, for building the
                                          fallback bucket).

OUTPUT SHAPE (ops/config/rule-jit-triggers.v1.json), one row per trigger:
  {
    "trigger_id":  stable 12-hex id, sha256(kind + "|" + pattern)[:12] —
                   independent of which rules are attached, so it does not
                   change when a rule's home moves or a pack's roster shifts.
    "kind":        "verb" | "bash_family" | "path_pattern" | "content_regex"
                   | "prompt_regex".
    "pattern":     interpretation depends on kind (see the hook for exact
                   matching semantics):
                     verb          — regex tested against payload.tool_name
                     bash_family   — regex tested against
                                     tool_input.command, only for
                                     tool_name in {Bash, functions.exec}
                     path_pattern  — fnmatch glob tested against any
                                     file_path/path/notebook_path key found
                                     in tool_input
                     content_regex — regex (lookaheads welcome) tested
                                     against a serialized blob of tool_name
                                     plus tool_input, the general fallback
                     prompt_regex  — regex tested against a partner message
                                     at UserPromptSubmit only (never a tool
                                     payload), after masking the row's
                                     optional `negative_pattern`
    "packs":       pack name(s) this trigger is understood to represent
                   (informational — the delivered rule set is the merge of
                   every trigger a call matches, not scoped per-pack at
                   delivery time).
    "rule_ids":    sorted short rule ids, capped at `max_rules_per_trigger`
                   (5) — the over-delivery bias lives in MATCHING liberally,
                   not in raising this cap; context stays lean by design.
    "source":      "seeded_detector"   — from a rule-triage detector field
                   "pack_fallback"     — the remaining un-seeded JIT rules
                                         of one pack, bucketed under that
                                         pack's own keyword list (narrowed
                                         by fallback_narrowing when present)
                   "structural_extra"  — a small, explicit, hand-reviewed
                                         list of non-JIT (gate-home)
                                         structural triggers this compiler
                                         still emits on purpose (see
                                         STRUCTURAL_EXTRA_TRIGGERS below);
                                         these are NOT derived from a triage
                                         detector field because gate-home
                                         rules are not triaged for one.
                   "prompt_cue"        — hand-reviewed prompt_regex cues taken
                                         from real logged partner prompts
                                         (PROMPT_CUE_TRIGGERS below); matched
                                         on human prompts only, never on a
                                         machine envelope.
                   "jev_compiled"      — from ops/config/rule-jev-triggers.v1.json
                                         (ops/rule-trigger-compile.py): Jev's
                                         once-per-rule judgment of which cues
                                         surface it, keyed to the rule's
                                         statement digest.
  }

DETERMINISM. Same inputs, byte-identical output, every time: rows are sorted
by (kind, pattern), rule_ids within a row are sorted, and the file is written
with sort_keys=True. `--check` recompiles in memory and diffs against the
committed file without writing, so CI can catch a hand-edit or a stale commit
(the same role rule-triage-selftest.py's own checks play for its file).

USAGE:
  ops/rule-jit-compile.py            # (re)write ops/config/rule-jit-triggers.v1.json
  ops/rule-jit-compile.py --check    # verify the committed file is exactly
                                      # what these inputs compile to; exit 1 on drift
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
TRIAGE_PATH = REPO / "ops" / "config" / "rule-triage.v1.json"
MAP_PATH = REPO / "ops" / "config" / "rule-enforcement-map.json"
OUTPUT_PATH = REPO / "ops" / "config" / "rule-jit-triggers.v1.json"
JEV_PATH = REPO / "ops" / "config" / "rule-jev-triggers.v1.json"

SCHEMA = "rule-jit-triggers/v1"
MAX_RULES_PER_TRIGGER = 5

# A small, explicit, hand-reviewed set of structural triggers for rules this
# slice's own scope boundary does not ask rule-triage.v1.json to carry a
# detector for: GATE-home rules whose binding moment is nonetheless useful to
# surface early, as a reminder, at the exact PreToolUse moment named here.
# Adding to this list is a reviewed code change, not a triage edit — unlike
# every other row in the compiled table, these are NOT derived from a triage
# detector field, because gate-home rules are not triaged for one (Part 2's
# scope is JIT-home rules only). Kept tiny and named on purpose (rule
# a8c55a47's reasoning cuts the other way here: this is the one shape that
# is deliberately NOT compiled from rule-triage.v1.json, and pretending
# otherwise would just move the same hand-maintenance problem one file over).
STRUCTURAL_EXTRA_TRIGGERS: tuple[dict[str, Any], ...] = (
    {
        "kind": "path_pattern",
        "pattern": "hooks/*.py",
        "packs": ["engineering-git"],
        "rule_ids": ["c0b38d80"],  # RE-BLESS THE GATE BASELINE IN THE SAME COMMIT
    },
    # A background agent reporting it finished IS the moment rule 86647daf
    # names ("the moment any substantive build/change/decision/lesson
    # finishes"). Replay of 2026-09-25: Jev judged that rule binding on 198
    # messages, nearly all of them agent-finished notifications, and no word
    # of its statement appears in one. The envelope is a structural fact, so
    # it is matched as one. prompt_regex rows run at UserPromptSubmit only.
    {
        "kind": "prompt_regex",
        "pattern": (r"^\s*<task-notification>(?=[\s\S]*<status>completed</status>)"
                    r"(?=[\s\S]*<summary>Agent\b)"),
        "packs": ["governance-rules"],
        "rule_ids": ["86647daf"],
    },
)


# Hand-reviewed cues taken from REAL logged partner prompts, not from the
# rules' own wording (2026-09-25 review of #1276: Jev's compile-time keywords,
# drawn from each rule's statement, recovered 3 of 30 human-prompt verdicts,
# because partners do not talk in the vocabulary rules are written in). Each
# row cites the logged prompts it came from (out/jev-rule-select.jsonl, where
# the judged path bound these rules). They fire on HUMAN prompts only — a
# machine envelope carries URLs and "error" in its own boilerplate — and, like
# the structural rows, they are a reviewed code change, not a judgment.
PROMPT_CUE_TRIGGERS: tuple[dict[str, Any], ...] = (
    # "Here's the article link\n\nhttps://x.com/av1dlive/status/...",
    # "https://x.com/i/grok/share/... What about this model?"
    {
        "pattern": r"\bhttps?://\S+",
        "packs": ["source-study"],
        "rule_ids": ["5e896ed6", "6437ae15", "98e74e7c"],
    },
    # the same two prompts: an x.com link is an X retrieval (57d13061)
    {
        "pattern": r"\bhttps?://(?:www\.|mobile\.)?(?:x|twitter)\.com/",
        "packs": ["joe-comms"],
        "rule_ids": ["57d13061"],
    },
    # "Just click my Carr.us@gmail account. The password is autofilled..."
    # A pasted terminal banner ("Last login: Thu Sep 24 11:01:21 on ttys001")
    # is not a login conversation: 2 of this cue's 7 firings on 282 logged
    # prompts were exactly that, so the banner line is masked first.
    {
        "pattern": r"\b(?:passwords?|log\s?-?ins?|autofill(?:ed)?|auto-filled)\b",
        "negative_pattern": r"(?m)^[ \t]*Last login:[^\n]*",
        "packs": ["joe-comms"],
        "rule_ids": ["c66dc739"],
    },
    # "It appears that flash got stuck on test 6",
    # "it didnt ask, it just said no such file"
    {
        "pattern": r"\b(?:stuck|no such file|errors?|errored|erroring)\b",
        "packs": ["governance-rules"],
        "rule_ids": ["d9ce2b08"],
    },
    # "look in my email folder for sapala"
    {
        "pattern": r"\b(?:e-?mails?|inbox|folder)\b",
        "packs": ["joe-comms"],
        "rule_ids": ["49533583", "c66dc739"],
    },
    # A request for a verifier: "independent review", "adversarial review",
    # "a Codex review". Rule 2b66211d binds the moment a checker is asked for
    # (it must get fresh context, not the maker's conversation).
    # evals/rule-delivery round 3.
    {
        "pattern": (r"\b(?:independent|adversarial)\s+(?:read-only\s+)?(?:review(?:er)?|"
                    r"verif\w+|check|pass)\b|\b(?:codex|grok)\s+review\b"),
        "packs": ["delegation-council"],
        "rule_ids": ["2b66211d"],
    },
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def trigger_id(kind: str, pattern: str) -> str:
    return hashlib.sha256(f"{kind}|{pattern}".encode("utf-8")).hexdigest()[:12]


def pack_keyword_pattern(pack: dict, exclude_terms: set[str]) -> str | None:
    """Rebuild the exact regex hooks/rule-pack-drift-gate.py's load_packs()
    would compile for this pack, minus any proven false-positive terms.

    Reusing the identical word-boundary construction keeps the fallback
    trigger's semantics recognizable as "the same pack, minus what
    adjudication narrowed" rather than a second, subtly different keyword
    matcher living beside the drift gate's.
    """
    words = [t for t in pack.get("triggers", []) if str(t).strip()
             and str(t).strip().lower() not in exclude_terms]
    if not words:
        return None
    parts = []
    for word in words:
        escaped = re.escape(word)
        left = r"\b" if re.match(r"\w", word[0]) else ""
        right = r"\b" if re.match(r"\w", word[-1]) else ""
        parts.append(f"{left}{escaped}{right}")
    return "|".join(parts)


def compile_triggers(triage: dict, enforcement_map: dict,
                     jev_document: dict | None = None) -> list[dict[str, Any]]:
    rules = triage.get("rules", [])
    by_id = {r["id"]: r for r in rules}
    jit_rules = [r for r in rules if r.get("home") == "jit"]

    fallback_narrowing = triage.get("fallback_narrowing", {})
    if not isinstance(fallback_narrowing, dict):
        fallback_narrowing = {}

    rule_load_layers = enforcement_map.get("rule_load_layers", {})
    rule_packs = enforcement_map.get("rule_packs", {})

    # 1. seeded detectors: group by (kind, pattern)
    groups: dict[tuple[str, str], set[str]] = {}
    seeded_ids: set[str] = set()
    for rule in jit_rules:
        detector = rule.get("detector")
        if not isinstance(detector, dict):
            continue
        kind = detector.get("kind")
        pattern = detector.get("pattern")
        if kind not in {"verb", "bash_family", "path_pattern", "content_regex"} \
                or not isinstance(pattern, str) or not pattern.strip():
            continue
        groups.setdefault((kind, pattern), set()).add(rule["id"])
        seeded_ids.add(rule["id"])

    rows: list[dict[str, Any]] = []
    for (kind, pattern), ids in groups.items():
        capped = sorted(ids)[:MAX_RULES_PER_TRIGGER]
        # The trigger's own packs are inferred from its member rules' existing
        # pack membership (rule-enforcement-map.json's rule_load_layers) — a
        # documentation/telemetry aid and the standing-context declared_packs
        # argument, never a second, hand-typed source of truth.
        packs = sorted({p for rid in capped
                        for p in rule_load_layers.get(rid, {}).get("packs", [])})
        rows.append({
            "trigger_id": trigger_id(kind, pattern),
            "kind": kind,
            "pattern": pattern,
            "packs": packs,
            "rule_ids": capped,
            "source": "seeded_detector",
        })

    # 2. pack fallback: JIT rules of a pack with no seeded detector
    pack_members: dict[str, list[str]] = {}
    for short, entry in rule_load_layers.items():
        if short not in by_id or by_id[short].get("home") != "jit":
            continue
        for pack_name in entry.get("packs", []):
            pack_members.setdefault(pack_name, []).append(short)

    for pack_name, pack in sorted(rule_packs.items()):
        members = pack_members.get(pack_name, [])
        unseeded = sorted(m for m in members if m not in seeded_ids)
        if not unseeded:
            continue
        exclude_terms = {
            str(t).strip().lower()
            for t in fallback_narrowing.get(pack_name, {}).get("exclude_terms", [])
        }
        pattern = pack_keyword_pattern(pack, exclude_terms)
        if pattern is None:
            continue
        rows.append({
            "trigger_id": trigger_id("content_regex", pattern),
            "kind": "content_regex",
            "pattern": pattern,
            "packs": [pack_name],
            "rule_ids": unseeded[:MAX_RULES_PER_TRIGGER],
            "source": "pack_fallback",
        })

    # 3. structural extras
    for extra in STRUCTURAL_EXTRA_TRIGGERS:
        rows.append({
            "trigger_id": trigger_id(extra["kind"], extra["pattern"]),
            "kind": extra["kind"],
            "pattern": extra["pattern"],
            "packs": list(extra["packs"]),
            "rule_ids": sorted(extra["rule_ids"])[:MAX_RULES_PER_TRIGGER],
            "source": "structural_extra",
        })

    # 3b. reviewed cues from real partner prompts (human prompts only)
    for cue in PROMPT_CUE_TRIGGERS:
        negative = cue.get("negative_pattern")
        row = {
            "trigger_id": trigger_id("prompt_regex", cue["pattern"] if not negative
                                     else f"{cue['pattern']}\0{negative}"),
            "kind": "prompt_regex",
            "pattern": cue["pattern"],
            "packs": list(cue["packs"]),
            "rule_ids": sorted(cue["rule_ids"])[:MAX_RULES_PER_TRIGGER],
            "source": "prompt_cue",
        }
        if negative:
            row["negative_pattern"] = negative
        rows.append(row)

    # 4. Jev-compiled triggers (ops/rule-trigger-compile.py): one judgment per
    # rule, made when the rule was taught or changed. prompt_regex rows are
    # matched against a partner message at UserPromptSubmit; verb,
    # bash_family and path_pattern rows join the PreToolUse rail as-is.
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in _jev_rows(jev_document):
        key = (row["kind"], row["pattern"], row.get("negative_pattern", ""))
        into = merged.setdefault(key, {**row, "rule_ids": []})
        into["rule_ids"] = sorted(set(into["rule_ids"]) | set(row["rule_ids"]))
        into["packs"] = sorted(set(into["packs"]) | set(row["packs"]))
    for (kind, pattern, negative), row in merged.items():
        row["rule_ids"] = row["rule_ids"][:MAX_RULES_PER_TRIGGER]
        row["trigger_id"] = trigger_id(kind, pattern if not negative else f"{pattern}\0{negative}")
        rows.append(row)

    rows.sort(key=lambda r: (r["kind"], r["pattern"], r.get("negative_pattern", "")))
    return rows


def _jev_rows(jev_document: dict | None) -> list[dict[str, Any]]:
    if not jev_document:
        return []
    spec = importlib.util.spec_from_file_location(
        "rule_trigger_compile", REPO / "ops" / "rule_trigger_compile.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.trigger_rows(jev_document)


def load_jev_document() -> dict | None:
    """The Jev-compiled per-rule triggers, or None when the file is absent.
    Absent means only the other three sources compile — the run-time side then
    falls back to judging messages rather than delivering nothing."""
    if not JEV_PATH.exists():
        return None
    return load_json(JEV_PATH)


_UNSET: Any = object()


def build_document(triage: dict, enforcement_map: dict,
                   jev_document: Any = _UNSET) -> dict[str, Any]:
    if jev_document is _UNSET:
        jev_document = load_jev_document()
    triggers = compile_triggers(triage, enforcement_map, jev_document)
    seeded = sum(1 for r in triggers if r["source"] == "seeded_detector")
    fallback = sum(1 for r in triggers if r["source"] == "pack_fallback")
    extra = sum(1 for r in triggers if r["source"] == "structural_extra")
    jev_compiled = sum(1 for r in triggers if r["source"] == "jev_compiled")
    seeded_rule_count = sum(len(r["rule_ids"]) for r in triggers if r["source"] == "seeded_detector")
    return {
        "schema": SCHEMA,
        "work_request": "WR-000019",
        "slice": "s9",
        "generated_note": (
            "Compiled by ops/rule-jit-compile.py from ops/config/rule-triage.v1.json's "
            "per-rule `detector` fields and ops/config/rule-enforcement-map.json's "
            "rule_packs/rule_load_layers. Never hand-edit this file — run the "
            "compiler again after changing either input, and `--check` verifies "
            "the two never drift apart."
        ),
        "generated_from": {
            "triage_sha256": file_sha256(TRIAGE_PATH),
            "map_sha256": file_sha256(MAP_PATH),
            **({"jev_triggers_sha256": file_sha256(JEV_PATH)}
               if jev_document is not None and JEV_PATH.exists() else {}),
        },
        "max_rules_per_trigger": MAX_RULES_PER_TRIGGER,
        "counts": {
            "triggers": len(triggers),
            "seeded_detector_triggers": seeded,
            "pack_fallback_triggers": fallback,
            "structural_extra_triggers": extra,
            "jev_compiled_triggers": jev_compiled,
            "seeded_detector_rule_ids": seeded_rule_count,
        },
        "triggers": triggers,
    }


def canonical_bytes(document: dict) -> bytes:
    return (json.dumps(document, indent=1, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed file matches a fresh compile; write nothing")
    args = parser.parse_args()

    triage = load_json(TRIAGE_PATH)
    enforcement_map = load_json(MAP_PATH)
    document = build_document(triage, enforcement_map)
    fresh = canonical_bytes(document)

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"rule-jit-compile --check: FAIL — {OUTPUT_PATH} does not exist")
            return 1
        current = OUTPUT_PATH.read_bytes()
        if current != fresh:
            print(f"rule-jit-compile --check: FAIL — {OUTPUT_PATH} is stale; "
                  "run `ops/rule-jit-compile.py` and commit the result")
            return 1
        print(f"rule-jit-compile --check: OK — {OUTPUT_PATH} matches its inputs "
              f"({document['counts']['triggers']} triggers)")
        return 0

    OUTPUT_PATH.write_bytes(fresh)
    counts = document["counts"]
    print(f"rule-jit-compile: wrote {OUTPUT_PATH} — {counts['triggers']} triggers "
          f"({counts['seeded_detector_triggers']} seeded covering "
          f"{counts['seeded_detector_rule_ids']} rules, "
          f"{counts['pack_fallback_triggers']} pack fallback, "
          f"{counts['structural_extra_triggers']} structural extra)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
