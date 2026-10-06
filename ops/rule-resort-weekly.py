#!/usr/bin/env python3
"""rule-resort-weekly.py -- weekly re-sort report: the piece that makes the
219-rule rulebook a MANAGED population instead of a fixed one (WR-000019
slice S12, Obedience & Autonomy).

WHAT THIS DOES, once a week. Reads three things this repo already produces
and proposes exact triage/lifecycle edits for a human PR -- nothing here
writes to ops/config/rule-triage.v1.json, ops/config/gate-lifecycle.json, or
any record-layer verb. PROPOSALS ONLY, same contract as
ops/gate-lifecycle-report.py's own report.

  1. JIT RULES WITH REPEATED VIOLATION SIGNALS -> GATE CANDIDATES.
     Reads out/rule-replay-nightly.jsonl's `jit_trigger` rows for the window
     and sums fire_count / distinct sessions per rule id, restricted to rule
     ids that came from a SEEDED detector (ops/config/rule-jit-triggers.v1.json
     source == "seeded_detector") -- never a pack_fallback trigger, which is
     shared keyword-bucket noise across every unseeded rule in a pack and
     would make every rule in a busy pack look like a "repeated signal" for
     no rule-specific reason. A rule clearing BOTH is_gate_candidate()
     thresholds is proposed for promotion, cited against the exact evidence
     rows (session ids + short excerpts) that produced the count -- this is
     a volume proxy over a DETERMINISTIC signal (the situation recurred),
     not a violation judgment; see rule-replay-nightly.py's own judge-stub
     docstring for why that judgment call is intentionally deferred.

  2. GATES WITH NO TRUE CATCHES -> DOWNGRADE PROPOSALS.
     ops/gate-lifecycle-report.py's own build() is imported and called
     directly -- its proposals are reproduced VERBATIM in this report, never
     re-derived by a second copy of the same quiet-window logic (rule
     a8c55a47). This satisfies the DoD's own instruction to delegate rather
     than duplicate.

  3. CORE RULES WHOSE SITUATIONS NEVER FIRED -> JIT CANDIDATES.
     A "core"-home rule (rule-triage.v1.json) still rides pack membership in
     ops/config/rule-enforcement-map.json's rule_load_layers (triage's `home`
     field is Slice S7's RECOMMENDATION, not yet applied to the live map).
     The compiled JIT trigger table never covers a core-home rule by
     construction (ops/rule-jit-compile.py's compiler filters to
     home == "jit" only), so this report falls back to the OLDER
     pack-keyword shadow signal instead: out/rule-delivery-shadow.jsonl's
     own `needed` field (which pack keyword sets actually matched a turn's
     content this window). A core-home rule with at least one pack and
     NONE of its packs ever appearing in `needed` across the window is
     proposed as a JIT candidate -- its situation, by the one proxy
     available for it, never actually recurred. A core-home rule with NO
     pack membership at all has no proxy signal either way and is reported
     as a data gap, never guessed at either verdict.

SCHEDULING. Weekly, guarded the way bin/notes-sweep-post.sh already gates its
own business-hours window: the check lives INSIDE this script (ISO weekday
via datetime, 1 == Monday), not in a shell conditional around the nightly
chain's step() call, so bin/nightly.sh calls this unconditionally every
night and the script itself decides whether today is its day. `--force`
bypasses the guard for a manual or CI run.

OUTPUT: a markdown report to stdout, plus one JSONL summary row appended to
out/rule-resort-weekly.jsonl.

USAGE:
  ops/rule-resort-weekly.py [--days 7] [--force] [--no-append] [--json]

Env overrides (test isolation, same convention as rule-replay-nightly.py):
  CARR_RESORT_REPO            repo root, for the triage/map/trigger-table
                               config reads (default: this file's parent's
                               parent)
  CARR_RESORT_OUT_DIR         where the replay/shadow ledgers are read from
                               and the report row is written (default:
                               <repo>/out)
  CARR_RESORT_LIFECYCLE_REPO  repo root gate-lifecycle-report.py's own
                               build() reads (default: same as CARR_RESORT_REPO)

Fixtures: ops/rule-resort-weekly-selftest.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get("CARR_RESORT_REPO") or
            Path(__file__).resolve().parent.parent)
# OUT is independently overridable (same reasoning as rule-replay-nightly.py's
# own CARR_REPLAY_OUT_DIR) so a selftest can point this script at synthetic
# replay/shadow ledgers and a throwaway report log without ever touching the
# real, worktree-shared out/ directory.
OUT = Path(os.environ.get("CARR_RESORT_OUT_DIR") or (REPO / "out"))
REPLAY_LOG = OUT / "rule-replay-nightly.jsonl"
SHADOW_LOG = OUT / "rule-delivery-shadow.jsonl"
TRIAGE_PATH = REPO / "ops" / "config" / "rule-triage.v1.json"
MAP_PATH = REPO / "ops" / "config" / "rule-enforcement-map.json"
TRIGGERS_PATH = REPO / "ops" / "config" / "rule-jit-triggers.v1.json"
REPORT_LOG = OUT / "rule-resort-weekly.jsonl"
LIFECYCLE_REPO = Path(os.environ.get("CARR_RESORT_LIFECYCLE_REPO") or REPO)

# THE WEEKLY THRESHOLDS (mutation-tested; see ops/rule-resort-weekly-selftest.py).
# A JIT rule needs BOTH a minimum total fire count AND a minimum number of
# DISTINCT sessions before it is proposed as a gate candidate -- fire count
# alone can come from one chatty session; session spread alone can come from
# one-off single fires that never repeat. Requiring both is what "repeated"
# means in the DoD wording, not "happened once, loudly".
DEFAULT_MIN_FIRES = 10
DEFAULT_MIN_SESSIONS = 3


def is_gate_candidate(total_fires: int, distinct_sessions: int, *,
                       min_fires: int = DEFAULT_MIN_FIRES,
                       min_sessions: int = DEFAULT_MIN_SESSIONS) -> bool:
    return total_fires >= min_fires and distinct_sessions >= min_sessions


def is_scheduled_weekday(dt: datetime) -> bool:
    """ISO weekday: 1 == Monday. This report runs once a week, on Monday,
    the same cadence bin/notes-sweep-post.sh's own weekday guard uses `date
    +%u` for (1=Monday ... 7=Sunday)."""
    return dt.isoweekday() == 1


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lifecycle_module():
    os.environ.setdefault("CARR_LIFECYCLE_REPO", str(LIFECYCLE_REPO))
    return load_module("gate_lifecycle_report_for_resort",
                        REPO / "ops" / "gate-lifecycle-report.py")


def parse_ts(value):
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


# ── 1. JIT -> GATE CANDIDATES ─────────────────────────────────────────────

def seeded_rule_ids(triggers_doc: dict) -> set[str]:
    ids: set[str] = set()
    for row in triggers_doc.get("triggers", []):
        if row.get("source") == "seeded_detector":
            ids.update(row.get("rule_ids", []))
    return ids


def jit_gate_candidates(window_start: datetime, window_end: datetime,
                        triggers_doc: dict, *, min_fires: int, min_sessions: int) -> list[dict]:
    seeded = seeded_rule_ids(triggers_doc)
    fires: dict[str, int] = {}
    sessions: dict[str, set] = {}
    evidence: dict[str, list] = {}
    for row in iter_jsonl(REPLAY_LOG):
        if row.get("schema") != "rule-replay-nightly/v1" or row.get("signal_kind") != "jit_trigger":
            continue
        ts = parse_ts(row.get("run_ts"))
        if ts is not None and not (window_start <= ts < window_end):
            continue
        for rid in row.get("rule_ids", []):
            if rid not in seeded:
                continue
            fires[rid] = fires.get(rid, 0) + row.get("fire_count", 0)
            sessions.setdefault(rid, set()).add(row.get("session_id"))
            bucket = evidence.setdefault(rid, [])
            if len(bucket) < 5:
                bucket.append({
                    "session_id": row.get("session_id"),
                    "fire_count": row.get("fire_count"),
                    "evidence": row.get("evidence", [])[:1],
                })

    candidates = []
    for rid, total in sorted(fires.items(), key=lambda kv: (-kv[1], kv[0])):
        distinct = len(sessions.get(rid, set()))
        if is_gate_candidate(total, distinct, min_fires=min_fires, min_sessions=min_sessions):
            candidates.append({
                "rule_id": rid,
                "total_fires": total,
                "distinct_sessions": distinct,
                "evidence_rows": evidence.get(rid, []),
            })
    return candidates


# ── 2. GATE DOWNGRADE PROPOSALS (delegated) ───────────────────────────────

def gate_downgrade_proposals(lc_mod, days: int) -> dict:
    return lc_mod.build(days)


# ── 3. CORE -> JIT CANDIDATES ──────────────────────────────────────────────

def needed_packs_in_window(window_start: datetime, window_end: datetime) -> set[str]:
    union: set[str] = set()
    for row in iter_jsonl(SHADOW_LOG):
        ts = parse_ts(row.get("ts"))
        if ts is None or not (window_start <= ts < window_end):
            continue
        needed = row.get("needed")
        if isinstance(needed, list):
            union.update(needed)
    return union


def core_jit_candidates(window_start: datetime, window_end: datetime,
                        triage: dict, enforcement_map: dict) -> tuple[list[dict], list[str]]:
    layers = enforcement_map.get("rule_load_layers", {})
    needed = needed_packs_in_window(window_start, window_end)
    candidates = []
    data_gaps = []
    for rule in triage.get("rules", []):
        if rule.get("home") != "core":
            continue
        rid = rule["id"]
        packs = layers.get(rid, {}).get("packs", [])
        if not packs:
            data_gaps.append(rid)
            continue
        if not (set(packs) & needed):
            candidates.append({
                "rule_id": rid,
                "current_packs": sorted(packs),
                "reason": (f"none of this core-recommended rule's current pack(s) "
                          f"{sorted(packs)} appeared in any rule-delivery-shadow.jsonl "
                          f"'needed' row across the report window."),
            })
    return candidates, data_gaps


# ── RENDER + SUMMARY ───────────────────────────────────────────────────────

def build_report(days: int, *, min_fires: int, min_sessions: int) -> dict:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=days)
    window_end = now

    triggers_doc = load_json(TRIGGERS_PATH, {"triggers": []})
    triage = load_json(TRIAGE_PATH, {"rules": []})
    enforcement_map = load_json(MAP_PATH, {})
    lc_mod = lifecycle_module()

    jit_candidates = jit_gate_candidates(window_start, window_end, triggers_doc,
                                        min_fires=min_fires, min_sessions=min_sessions)
    gate_report = gate_downgrade_proposals(lc_mod, days)
    core_candidates, core_data_gaps = core_jit_candidates(window_start, window_end,
                                                          triage, enforcement_map)

    return {
        "schema": "rule-resort-weekly/v1",
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
        "min_fires": min_fires,
        "min_sessions": min_sessions,
        "jit_to_gate_candidates": jit_candidates,
        "gate_downgrade_proposals": gate_report["proposals"],
        "gate_report_data_gaps": gate_report["data_gaps"],
        "core_to_jit_candidates": core_candidates,
        "core_to_jit_data_gaps": core_data_gaps,
    }


def render(report: dict) -> str:
    lines = [
        f"# rule re-sort weekly report -- {report['generated']} "
        f"({report['days']}-day window)",
        "",
        "Proposals only -- nothing here edits rule-triage.v1.json, "
        "gate-lifecycle.json, or any record. Each section names the exact "
        "edit for a PR.",
        "",
        "## 1. JIT rules with repeated signals -> gate candidates "
        f"(>= {report['min_fires']} fires AND >= {report['min_sessions']} sessions)",
    ]
    if report["jit_to_gate_candidates"]:
        for c in report["jit_to_gate_candidates"]:
            lines.append(f"  - {c['rule_id']}: {c['total_fires']} fires across "
                        f"{c['distinct_sessions']} sessions -> propose "
                        f"ops/config/rule-triage.v1.json home: jit -> gate")
            for ev in c["evidence_rows"][:3]:
                lines.append(f"      evidence: session {ev['session_id']} "
                            f"({ev['fire_count']} fires) {ev['evidence']}")
    else:
        lines.append("  none this window.")

    lines.append("")
    lines.append("## 2. Gates with no true catches -> downgrade/retirement proposals "
                f"(delegated to gate-lifecycle-report.py, {report['days']}-day windows)")
    if report["gate_downgrade_proposals"]:
        for p in report["gate_downgrade_proposals"]:
            lines.append(f"  - {p['gate']}: {p['proposal']['action']} -- "
                        f"{p['proposal']['reason']}")
    else:
        lines.append("  none this window.")
    if report["gate_report_data_gaps"]:
        lines.append(f"  data gaps (no timestamp field, excluded): "
                    f"{', '.join(report['gate_report_data_gaps'])}")

    lines.append("")
    lines.append("## 3. Core rules whose situation never fired -> JIT candidates")
    if report["core_to_jit_candidates"]:
        for c in report["core_to_jit_candidates"]:
            lines.append(f"  - {c['rule_id']} (packs: {', '.join(c['current_packs'])}) "
                        f"-> propose ops/config/rule-triage.v1.json home: core -> jit")
            lines.append(f"      {c['reason']}")
    else:
        lines.append("  none this window.")
    if report["core_to_jit_data_gaps"]:
        lines.append(f"  data gaps (no pack membership to test, excluded): "
                    f"{', '.join(report['core_to_jit_data_gaps'])}")

    return "\n".join(lines)


def append_report_row(report: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    row = {
        "schema": report["schema"],
        "ts": report["generated"],
        "days": report["days"],
        "jit_to_gate_candidate_count": len(report["jit_to_gate_candidates"]),
        "jit_to_gate_candidates": [c["rule_id"] for c in report["jit_to_gate_candidates"]],
        "gate_downgrade_proposal_count": len(report["gate_downgrade_proposals"]),
        "gate_downgrade_proposals": [
            {"gate": p["gate"], "action": p["proposal"]["action"]}
            for p in report["gate_downgrade_proposals"]
        ],
        "core_to_jit_candidate_count": len(report["core_to_jit_candidates"]),
        "core_to_jit_candidates": [c["rule_id"] for c in report["core_to_jit_candidates"]],
    }
    with REPORT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--force", action="store_true",
                    help="run even when today is not the scheduled weekday")
    ap.add_argument("--min-fires", type=int, default=DEFAULT_MIN_FIRES)
    ap.add_argument("--min-sessions", type=int, default=DEFAULT_MIN_SESSIONS)
    ap.add_argument("--no-append", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    if not args.force and not is_scheduled_weekday(now):
        print(f"rule-resort-weekly: SKIP -- today ({now.strftime('%A')}) is not the "
              "scheduled weekday (Monday); pass --force to run anyway")
        return 0

    report = build_report(max(1, args.days), min_fires=args.min_fires,
                          min_sessions=args.min_sessions)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))

    if not args.no_append:
        append_report_row(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
