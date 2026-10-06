#!/usr/bin/env python3
"""rule-delivery-adjudication-apply.py — close the loop from an adjudication
finding back to the JIT trigger table's own inputs (WR-000019 slice S9).

WHY THIS EXISTS. audits/rule-delivery-shadow-adjudication-*.json files
(ops/rule-delivery-shadow-adjudication-selftest.py's own domain) classify each
shadow-window miss, and a good fraction of them are classified "explained" —
an independent reviewer replayed the source transcript and found the miss was
trigger-polysemy: a pack's keyword fired on an unrelated, ordinary use of the
same word (the recorded 2026-08-26 package's own recurring example: the
records-intake pack's word "merge" firing on an ordinary Git merge, six times
across eight explained findings). Nothing consumed that classification before
this slice — it sat in the adjudication file as a verdict with no mechanism
turning it into a config change, the exact "classification with no acted-on
consequence" gap this tool closes.

WHAT IT DOES. For every "explained" event whose `reason` names a pack (from
ops/config/rule-enforcement-map.json's `rule_packs`) immediately followed by
"fired on" and, within that span, one of that pack's OWN trigger words —
never a free-form NLP guess: the offending word must already be a member of
that pack's known, closed keyword vocabulary, so extraction is a lookup, not
a parse — it counts one occurrence of (pack, word). Any (pack, word) pair
reaching --min-occurrences (default 2, deliberately more than one anecdote)
across ALL adjudication files given becomes a proposed narrowing: add `word`
to ops/config/rule-triage.v1.json's `fallback_narrowing[pack].exclude_terms`,
which ops/rule-jit-compile.py already reads to drop excluded words from that
pack's fallback content_regex before compiling
ops/config/rule-jit-triggers.v1.json — the exact "derived-table INPUT" this
task is scoped to touch, never ops/config/rule-enforcement-map.json itself
(no cascade: see that compiler's own docstring).

--dry-run (DEFAULT) prints what would change and writes nothing. --apply
writes the narrowing into rule-triage.v1.json AND recompiles the trigger
table in the same run, so the two derived artifacts never drift apart for
even one commit. A (pack, word) pair already excluded is silently skipped
(idempotent re-runs).

WHAT THIS DOES NOT DO. It never edits ops/config/rule-enforcement-map.json's
own `rule_packs` keyword lists (those stay the drift gate's unchanged
telemetry source — see the note beside hooks/rule-pack-drift-gate.py's
load_packs()). It never edits rule CONTENT, admits, retires, or amends a
rule, or touches an adjudication file. It never removes a pack's fallback
trigger entirely as a way of narrowing it to zero words on its own initiative
— that already-covered edge case in the compiler needs no separate handling
here, but a caller narrowing every one of a pack's words across several runs
would see it happen and should treat that as worth a second look, not a
silent success.

USAGE:
  ops/rule-delivery-adjudication-apply.py [FILES...]              # dry run (default)
  ops/rule-delivery-adjudication-apply.py [FILES...] --apply      # write + recompile
  ops/rule-delivery-adjudication-apply.py --apply --min-occurrences 3
  (with no FILES, defaults to every audits/rule-delivery-shadow-adjudication-*.json)
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import importlib.util as _ilu


def _load_compiler():
    spec = _ilu.spec_from_file_location("rule_jit_compile", REPO / "ops" / "rule-jit-compile.py")
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compiler = _load_compiler()

TRIAGE_PATH = REPO / "ops" / "config" / "rule-triage.v1.json"
MAP_PATH = REPO / "ops" / "config" / "rule-enforcement-map.json"
DEFAULT_GLOB = str(REPO / "audits" / "rule-delivery-shadow-adjudication-*.json")
DEFAULT_MIN_OCCURRENCES = 2
FIRED_ON_RE = re.compile(r"\b([a-z][a-z0-9-]*) fired on\b", re.I)
FINDING_SPAN_CHARS = 120


def default_files() -> list[Path]:
    return sorted(Path(p) for p in glob.glob(DEFAULT_GLOB))


def load_adjudication(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "rule-delivery-shadow-adjudication/v1":
        raise ValueError(f"{path}: not a rule-delivery-shadow-adjudication/v1 file")
    return data


def word_pattern(word: str) -> re.Pattern:
    escaped = re.escape(word)
    left = r"\b" if re.match(r"\w", word[0]) else ""
    right = r"\b" if re.match(r"\w", word[-1]) else ""
    return re.compile(f"{left}{escaped}{right}", re.I)


def extract_findings(reason: str, rule_packs: dict) -> list[tuple[str, str]]:
    """[(pack, word), ...] — every (known pack, that pack's own trigger word)
    pair this reason text names as the source of an explained false positive.

    Grounded in each pack's closed keyword vocabulary rather than free
    parsing: a span is credited to a word only when that exact word is
    already one of the pack's own configured triggers, so this can never
    invent a narrowing target rule-jit-compile.py would not recognize.
    """
    findings: list[tuple[str, str]] = []
    for match in FIRED_ON_RE.finditer(reason):
        pack_name = match.group(1)
        # The trailing span is read by SLICING past the short "<pack> fired
        # on" match, not consumed as part of it, so finditer's next search
        # starts right after "fired on" rather than after a wide captured
        # span — otherwise one long span can swallow a second, nearby
        # "<other-pack> fired on ..." occurrence in the same reason string.
        span = reason[match.end():match.end() + FINDING_SPAN_CHARS]
        pack = rule_packs.get(pack_name)
        if not isinstance(pack, dict):
            continue
        for word in pack.get("triggers", []):
            if not isinstance(word, str) or not word.strip():
                continue
            if word_pattern(word).search(span):
                findings.append((pack_name, word))
                break  # first (leftmost-configured) matching word per span, deterministic
    return findings


def collect_occurrences(files: list[Path], rule_packs: dict) -> dict[tuple[str, str], list[str]]:
    """(pack, word) -> sorted list of event_ids citing it as explained."""
    occurrences: dict[tuple[str, str], list[str]] = {}
    for path in files:
        data = load_adjudication(path)
        for event in data.get("events", []):
            if not isinstance(event, dict) or event.get("proposed_disposition") != "explained":
                continue
            reason = event.get("reason")
            if not isinstance(reason, str):
                continue
            for finding in extract_findings(reason, rule_packs):
                occurrences.setdefault(finding, []).append(str(event.get("event_id", "")))
    for key in occurrences:
        occurrences[key].sort()
    return occurrences


def already_excluded(triage: dict, pack: str, word: str) -> bool:
    narrowing = triage.get("fallback_narrowing", {})
    if not isinstance(narrowing, dict):
        return False
    entry = narrowing.get(pack, {})
    if not isinstance(entry, dict):
        return False
    return word.strip().lower() in {
        str(t).strip().lower() for t in entry.get("exclude_terms", [])
    }


def proposed_changes(occurrences: dict[tuple[str, str], list[str]], triage: dict,
                     min_occurrences: int) -> list[dict[str, Any]]:
    changes = []
    for (pack, word), event_ids in sorted(occurrences.items()):
        if len(event_ids) < min_occurrences or already_excluded(triage, pack, word):
            continue
        changes.append({
            "pack": pack, "term": word, "occurrences": len(event_ids),
            "event_ids": event_ids,
        })
    return changes


def apply_changes(triage: dict, changes: list[dict[str, Any]]) -> dict:
    narrowing = triage.setdefault("fallback_narrowing", {})
    for change in changes:
        entry = narrowing.setdefault(change["pack"], {"exclude_terms": []})
        terms = entry.setdefault("exclude_terms", [])
        if change["term"] not in terms:
            terms.append(change["term"])
        terms.sort()
    return triage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*",
                        help="adjudication files (default: every "
                             "audits/rule-delivery-shadow-adjudication-*.json)")
    parser.add_argument("--min-occurrences", type=int, default=DEFAULT_MIN_OCCURRENCES,
                        help=f"minimum explained occurrences before narrowing "
                             f"(default {DEFAULT_MIN_OCCURRENCES})")
    parser.add_argument("--apply", action="store_true",
                        help="write the narrowing and recompile the trigger table "
                             "(default: dry run, writes nothing)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    files = [Path(f) for f in args.files] if args.files else default_files()
    if not files:
        print("rule-delivery-adjudication-apply: no adjudication files found", file=sys.stderr)
        return 1

    enforcement_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    rule_packs = enforcement_map.get("rule_packs", {})
    triage = json.loads(TRIAGE_PATH.read_text(encoding="utf-8"))

    occurrences = collect_occurrences(files, rule_packs)
    changes = proposed_changes(occurrences, triage, args.min_occurrences)

    result = {
        "dry_run": not args.apply,
        "files_read": [str(f) for f in files],
        "min_occurrences": args.min_occurrences,
        "changes": changes,
    }

    if not changes:
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("rule-delivery-adjudication-apply: no (pack, word) pair reached "
                  f"{args.min_occurrences} explained occurrences — nothing to narrow.")
        return 0

    if not args.apply:
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"rule-delivery-adjudication-apply: {len(changes)} narrowing(s) proposed "
                  "(DRY RUN — nothing written, re-run with --apply):")
            for change in changes:
                print(f"  {change['pack']}: exclude {change['term']!r} "
                      f"({change['occurrences']} explained finding(s): "
                      + ", ".join(eid[:12] for eid in change["event_ids"]) + ")")
        return 0

    apply_changes(triage, changes)
    TRIAGE_PATH.write_text(json.dumps(triage, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    triage_after = json.loads(TRIAGE_PATH.read_text(encoding="utf-8"))
    document = compiler.build_document(triage_after, enforcement_map)
    compiler.OUTPUT_PATH.write_bytes(compiler.canonical_bytes(document))
    result["triggers_recompiled"] = document["counts"]

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"rule-delivery-adjudication-apply: applied {len(changes)} narrowing(s) to "
              f"{TRIAGE_PATH} and recompiled {compiler.OUTPUT_PATH} "
              f"({document['counts']['triggers']} triggers).")
        for change in changes:
            print(f"  {change['pack']}: excluded {change['term']!r} "
                  f"({change['occurrences']} explained finding(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
