#!/usr/bin/env python3
"""rule-replay-nightly.py -- nightly transcript-replay measurement loop
(WR-000019 slice S12, Obedience & Autonomy).

WHAT THIS IS. Every night this scores the prior window's real Claude Code
transcripts for this repo against the rules that were actually LIVE for each
turn, using ONLY the deterministic detectors this codebase already ships --
never a new NLP pass, never an LLM judgment call. It answers a narrow,
provable question per session: which rule-signals fired, how many times, and
what the (short, redacted) evidence was. It does NOT decide whether a fired
signal was a genuine violation -- see JUDGE STUB below.

THREE DETERMINISTIC SOURCES, each one reused rather than reimplemented:

  (a) JIT TRIGGER MATCH. hooks/rule-pack-preuse-reselection.py's own per-row
      matcher, `_row_matches(tool_name, tool_input, row)`, is imported
      UNCHANGED (via importlib -- this file is a gate under the repo's hooks/
      scope boundary and is never edited here) and run against every
      assistant tool_use call recorded in the transcript, using the exact
      compiled trigger table (ops/config/rule-jit-triggers.v1.json, loaded
      through lib.rule_delivery_preuse.load_trigger_table -- the same reader
      the live hook uses). A call this matches is a proof that the rule's
      SITUATION was live for that turn; it is not by itself proof of a
      violation -- flagging real violations among situational fires is
      exactly the judgment the LLM-judge stub below is reserved for.

  (b) GATE LEDGERS. For the four GATE-classified rule homes named in this
      slice's own scope (conduct, delegation, completion-evidence, and the
      S8 shadow writing-check), the session's own ledger rows are read
      through ops/gate-lifecycle-report.py's OWN `read_rows()` /
      `is_true_positive()` (imported, not duplicated) against that script's
      own ops/config/gate-lifecycle.json catch_metric for each gate -- the
      identical rule a human reads in that weekly report. A ledger row IS
      the catch; this script adds no judgment on top of it.

  (c) SHADOW WRITING-CHECK COUNT. A conduct-gate-shadow.jsonl row already
      names its own fired rule directly (rule 5be2f462); counted per session
      the same way as (b), since it lives in the same ledger family.

JUDGE STUB, ON PURPOSE. `llm_judge_not_evaluated()` below is the ONLY hook a
future slice needs to fill in with a real LLM-judge pass (e.g. "was this JIT
situational fire actually a violation, or a benign mention"). Every row this
script writes carries `"judge": "not_evaluated"` explicitly -- never omitted
-- so a later completion-evidence check can tell a judged row from a
never-judged one by field VALUE, not by the field's absence. Shipping the
deterministic loop now, with this stub named and documented, is this slice's
explicit scope boundary: NO LLM judge in S12.

TRANSCRIPT SOURCE. Claude Code transcripts live under
~/.claude/projects/<project-slug>/*.jsonl. Rather than reverse-engineer the
slug-mangling algorithm (untested, undocumented, and this repo runs from many
worktrees under .claude/worktrees/<name> as well as the canonical checkout),
this script verifies the actual `cwd` field recorded on each transcript's own
lines against the repo root -- correct by construction for the canonical
checkout AND every worktree under it, regardless of how a given Claude Code
version happens to mangle a path into a directory name. A project-slug-name
prefix filter is applied first purely as a speed optimization (skip clearly
unrelated project directories before opening any of their files).

OUTPUT. One JSONL row per (session, fired rule-signal) to
out/rule-replay-nightly.jsonl, plus one summary row per run (schema
rule-replay-nightly-summary/v1) appended to the same file.

DEFECT AUTOFEED, A HANDOFF NOT A FILING. When a signal matches a known
failure-class signature -- today exactly `shadow_writing_check` and
`delegation_material`, per this slice's explicit starting scope -- this
script appends a STAGED proposal to out/rule-replay-defect-proposals.jsonl
carrying every field record-defect's inputSchema requires. It NEVER calls
record-defect itself: a scheduled batch job has no session-level judgment
about whether a given finding is really worth a defect row (record-defect's
own claimed/actual honesty contract wants a human or an in-session judgment
call, not a cron job's pattern match), so filing is left to the next morning
session's review. Each staged row's `idempotency_key` is explicitly `null`
with a paired `idempotency_key_note` -- idempotency keys must be FRESH per
intended action, and staging is not the intended action; the session that
actually calls record-defect must mint its own. Staging is deduplicated by a
stable `finding_key` (signal_kind + session_id + gate_key) so the same
session's already-known finding is proposed at most once, however many
overlapping nightly windows see it again.

USAGE:
  ops/rule-replay-nightly.py [--since-hours 24] [--now ISO8601] [--no-append]

Env overrides (test isolation, same convention as CARR_LIFECYCLE_REPO):
  CARR_REPLAY_REPO             repo root for CODE (hooks/, lib/, the compiled
                                trigger table) -- default: this file's parent's
                                parent
  CARR_REPLAY_TARGET_REPO      which working tree's transcripts to score
                                (default: same as CARR_REPLAY_REPO)
  CARR_REPLAY_PROJECTS_DIR     ~/.claude/projects equivalent (default: that path)
  CARR_REPLAY_OUT_DIR          where THIS script writes its own two ledgers
                                (default: <repo>/out)
  CARR_REPLAY_LIFECYCLE_REPO   repo root for gate-lifecycle.json + its four
                                named ledgers (default: same as CARR_REPLAY_REPO)

Fixtures: ops/rule-replay-nightly-selftest.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get("CARR_REPLAY_REPO") or
            Path(__file__).resolve().parent.parent)
# OUT is independently overridable so a selftest can redirect this script's
# OWN writes (the replay ledger and the defect-proposal ledger) into a
# throwaway directory without redirecting REPO itself -- REPO also drives
# where hooks/rule-pack-preuse-reselection.py, lib/rule_delivery_preuse.py
# and the real compiled trigger table are read from, and those are real,
# read-only, shared code this script wants to exercise for real even under
# test. out/ on this machine is a symlink shared by every worktree, so a
# test that let REPO's default drag OUT along with it would write into that
# shared file -- exactly the flake this repo's own worktree tooling and
# other selftests (e.g. gate-lifecycle-report-selftest.py) are built to
# avoid.
OUT = Path(os.environ.get("CARR_REPLAY_OUT_DIR") or (REPO / "out"))
REPLAY_LOG = OUT / "rule-replay-nightly.jsonl"
DEFECT_PROPOSALS_LOG = OUT / "rule-replay-defect-proposals.jsonl"
TRIAGE_PATH = REPO / "ops" / "config" / "rule-triage.v1.json"
# The gate-lifecycle inputs (ops/config/gate-lifecycle.json + the four named
# ledgers it points at) are independently overridable for the same reason:
# a selftest needs synthetic ledger rows without ever touching the real,
# shared out/conduct-gate-shadow.jsonl etc.
LIFECYCLE_REPO = Path(os.environ.get("CARR_REPLAY_LIFECYCLE_REPO") or REPO)

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
PROJECTS_DIR = Path(os.environ.get("CARR_REPLAY_PROJECTS_DIR") or DEFAULT_PROJECTS_DIR)
# TARGET_REPO is normally identical to REPO -- the working tree whose
# transcripts get scored is normally the same tree this tool's own code and
# config live in. The two are split ONLY so this script can run from a
# worktree carrying newer merged code/config than the canonical checkout has
# caught up to yet (exactly this slice's own bootstrap situation) while still
# scoring the canonical checkout's real session history, without ever
# writing into the canonical out/ tree from a feature branch.
TARGET_REPO = Path(os.environ.get("CARR_REPLAY_TARGET_REPO") or REPO)

sys.path.insert(0, str(REPO))
from lib.rule_delivery_preuse import load_trigger_table, merge_trigger_delivery, tool_calls  # noqa:E402

# The four ledgers this slice's own scope names, mapped to the gate-lifecycle
# key that carries their catch_metric and the carrying_control that resolves
# to a set of implicated rule ids in rule-triage.v1.json. A small, explicit,
# hand-reviewed table -- same convention as rule-jit-compile.py's own
# STRUCTURAL_EXTRA_TRIGGERS -- because there is no machine-derivable mapping
# from a gate script name to the rule ids it enforces; carrying_control is
# the closest reviewed link that already exists.
GATE_SIGNAL_SPECS: dict[str, dict[str, object]] = {
    "conduct-stop-gate.py": {
        "signal_kind": "gate_catch", "carrying_control": "conduct_stop",
    },
    "conduct-stop-gate.py:chat-writing-shadow": {
        "signal_kind": "shadow_writing_check", "carrying_control": None,
    },
    "completion-evidence-gate.py": {
        "signal_kind": "gate_catch", "carrying_control": "completion_evidence",
    },
    "delegation-gate.py": {
        "signal_kind": "delegation_material",
        "carrying_control": "delegation_names_model_and_effort",
    },
}

# THE SIGNATURE MATCHER (mutation-tested; see ops/rule-replay-nightly-selftest.py).
# Exactly the two failure-class signatures this slice's DoD names as the
# starting scope for defect autofeed. Deliberately a closed set, not a
# heuristic -- widening it is a reviewed code change, not a config edit.
DEFECT_SIGNATURE_KINDS = frozenset({"shadow_writing_check", "delegation_material"})


def is_known_failure_signature(signal_kind: str) -> bool:
    """True for exactly the rule-signal kinds this slice autofeeds to the
    defect-proposal ledger. A JIT trigger fire or a plain gate_catch is a
    real, deterministic signal too, but neither one is (yet) a named
    failure-class signature -- that judgment call is scoped to a later
    slice with its own evidence bar, same as the LLM judge itself."""
    return signal_kind in DEFECT_SIGNATURE_KINDS


def llm_judge_not_evaluated(signal_kind: str, evidence: list[str]) -> dict:
    """STUB. A future slice's real LLM-judge pass over ambiguous or
    content-quality rule signals (e.g. "was this JIT situational fire an
    actual violation or a benign mention") attaches here. This slice ships
    the deterministic loop only -- WR-000019 S12's explicit scope boundary
    says NO LLM judge in this slice. Every emitted row carries this
    function's return value merged in, so "judge": "not_evaluated" is an
    explicit field VALUE on every row, never an absent key a later reader
    could mistake for "already judged and found clean".
    """
    del signal_kind, evidence  # unused in the stub; kept in the signature
    return {"judge": "not_evaluated",
            "judge_reason": "no LLM-judge in WR-000019 slice S12; deterministic loop only"}


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
    """ops/gate-lifecycle-report.py, imported so its read_rows()/
    is_true_positive() are REUSED for gate-ledger scoring, never
    reimplemented. CARR_LIFECYCLE_REPO is set (without clobbering an
    explicit override already present in the environment) so its
    module-level REPO constant resolves to LIFECYCLE_REPO -- normally the
    same repo this script itself is scoring, independently overridable for
    tests (see LIFECYCLE_REPO above)."""
    os.environ.setdefault("CARR_LIFECYCLE_REPO", str(LIFECYCLE_REPO))
    return load_module("gate_lifecycle_report_for_replay",
                        REPO / "ops" / "gate-lifecycle-report.py")


def preuse_matcher():
    """hooks/rule-pack-preuse-reselection.py's own `_row_matches`, imported
    UNCHANGED -- this file is a gate under this repo's hooks/ scope
    boundary and is never edited by this slice. Only the pure per-row
    matching function is used; the module's own REPO-bound globals
    (selector calls, receipt validation) are never invoked here."""
    mod = load_module("rule_pack_preuse_reselection_for_replay",
                       REPO / "hooks" / "rule-pack-preuse-reselection.py")
    return mod._row_matches  # noqa: SLF001 -- deliberate reuse, see docstring


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


# ── TRANSCRIPT DISCOVERY ─────────────────────────────────────────────────

def _slug_prefix_candidates(repo: Path) -> set[str]:
    """A fast, best-effort filter over project directory NAMES before any
    file is opened. Not the correctness check -- see discover_session_files's
    cwd verification -- just a speed optimization over ~60+ unrelated
    project directories that can otherwise share this machine's
    ~/.claude/projects/.
    """
    mangled = str(repo).replace("/", "-").replace(".", "-")
    return {mangled}


def _peek_cwd(path: Path, max_lines: int = 40) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                cwd = rec.get("cwd") if isinstance(rec, dict) else None
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        return None
    return None


def discover_session_files(repo: Path, projects_dir: Path,
                            window_start: datetime) -> list[Path]:
    """Every *.jsonl transcript under projects_dir whose own recorded `cwd`
    is this repo or a path under it (main checkout or any worktree), and
    whose mtime is at or after window_start (a session's mtime is its last
    write -- anything older cannot have a record inside the window)."""
    if not projects_dir.is_dir():
        return []
    repo_str = str(repo.resolve())
    prefixes = _slug_prefix_candidates(repo)
    matches: list[Path] = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Fast filter: only bother with directories whose name plausibly
        # names this repo tree (main or a worktree beneath it).
        if not any(entry.name.startswith(p) for p in prefixes):
            continue
        for jf in sorted(entry.glob("*.jsonl")):
            try:
                mtime = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime < window_start:
                continue
            cwd = _peek_cwd(jf)
            if not cwd:
                continue
            try:
                cwd_resolved = str(Path(cwd).resolve())
            except Exception:
                continue
            if cwd_resolved == repo_str or cwd_resolved.startswith(repo_str + os.sep):
                matches.append(jf)
    return matches


def iter_records(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


# ── EVIDENCE ──────────────────────────────────────────────────────────────

def excerpt_for_tool_call(tool_name: str, tool_input: object) -> str:
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        body = tool_input["command"]
    else:
        try:
            body = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
        except Exception:
            body = str(tool_input)
    return f"{tool_name}: {body}"[:120]


def excerpt_for_ledger_row(row: dict) -> str:
    for field in ("excerpt", "reason", "flag_classes"):
        value = row.get(field)
        if value:
            return str(value)[:120]
    skip = {"ts", "hook", "session"}
    rest = {k: v for k, v in row.items() if k not in skip}
    try:
        return json.dumps(rest, sort_keys=True, ensure_ascii=False)[:120]
    except Exception:
        return str(rest)[:120]


# ── SCORING ───────────────────────────────────────────────────────────────

def gate_rule_ids(triage: dict, carrying_control: str) -> list[str]:
    return sorted(r["id"] for r in triage.get("rules", [])
                  if r.get("home") == "gate" and r.get("carrying_control") == carrying_control)


def score_jit_triggers(files: list[Path], window_start: datetime, window_end: datetime,
                        row_matches, trigger_rows: list[dict]) -> tuple[dict, set]:
    """(agg, session_ids) -- agg keys (session_id, rule_id) -> accumulator."""
    agg: dict[tuple[str, str], dict] = {}
    session_ids: set[str] = set()
    for path in files:
        for record in iter_records(path):
            sid = record.get("sessionId")
            if isinstance(sid, str) and sid:
                ts = parse_ts(record.get("timestamp"))
                if ts is not None and window_start <= ts < window_end:
                    session_ids.add(sid)
            for tool_id, name, tool_input, context_sid in tool_calls(record):
                del tool_id
                if not isinstance(name, str) or not name:
                    continue
                ts = parse_ts(record.get("timestamp"))
                if ts is None or not (window_start <= ts < window_end):
                    continue
                effective_sid = context_sid if isinstance(context_sid, str) and context_sid else sid
                if not isinstance(effective_sid, str) or not effective_sid:
                    continue
                matched = [row for row in trigger_rows if row_matches(name, tool_input, row)]
                if not matched:
                    continue
                trigger_ids, _packs, rule_ids = merge_trigger_delivery(matched)
                excerpt = excerpt_for_tool_call(name, tool_input)
                for rule_id in rule_ids:
                    key = (effective_sid, rule_id)
                    bucket = agg.setdefault(key, {"count": 0, "trigger_ids": set(), "evidence": []})
                    bucket["count"] += 1
                    bucket["trigger_ids"].update(trigger_ids)
                    if len(bucket["evidence"]) < 3:
                        bucket["evidence"].append(excerpt)
    return agg, session_ids


def score_gate_ledgers(lc_mod, triage: dict, session_ids: set[str],
                        window_start: datetime, window_end: datetime) -> dict:
    """agg keys (session_id, gate_key) -> accumulator, restricted to sessions
    already known to be active in the window from transcript scoring."""
    metadata = load_json(LIFECYCLE_REPO / "ops" / "config" / "gate-lifecycle.json", {}) or {}
    gates = metadata.get("gates", {})
    agg: dict[tuple[str, str], dict] = {}
    for gate_key, spec in GATE_SIGNAL_SPECS.items():
        entry = gates.get(gate_key)
        if not entry:
            continue
        metric = entry["catch_metric"]
        rows = lc_mod.read_rows(metric)
        for row, ts in rows:
            if not isinstance(row, dict):
                continue
            sid = row.get("session")
            if sid not in session_ids:
                continue
            if ts is not None and not (window_start <= ts < window_end):
                continue
            if not lc_mod.is_true_positive(row, metric):
                continue
            carrying_control = spec["carrying_control"]
            if isinstance(carrying_control, str) and carrying_control:
                rule_ids = gate_rule_ids(triage, carrying_control)
            else:
                explicit = row.get("rule")
                rule_ids = [explicit] if isinstance(explicit, str) and explicit else []
            key = (sid, gate_key)
            bucket = agg.setdefault(key, {
                "count": 0, "rule_ids": set(rule_ids), "evidence": [],
                "signal_kind": spec["signal_kind"],
            })
            bucket["count"] += 1
            bucket["rule_ids"].update(rule_ids)
            if len(bucket["evidence"]) < 3:
                bucket["evidence"].append(excerpt_for_ledger_row(row))
    return agg


def build_replay(window_start: datetime, window_end: datetime):
    triage = load_json(TRIAGE_PATH, {"rules": []})
    trigger_rows = load_trigger_table(REPO)
    row_matches = preuse_matcher()
    lc_mod = lifecycle_module()

    files = discover_session_files(TARGET_REPO, PROJECTS_DIR, window_start)
    jit_agg, session_ids = score_jit_triggers(files, window_start, window_end,
                                              row_matches, trigger_rows)
    gate_agg = score_gate_ledgers(lc_mod, triage, session_ids, window_start, window_end)

    run_ts = now_iso()
    rows: list[dict] = []
    for (sid, rule_id), bucket in sorted(jit_agg.items()):
        judged = llm_judge_not_evaluated("jit_trigger", bucket["evidence"])
        rows.append({
            "schema": "rule-replay-nightly/v1",
            "run_ts": run_ts,
            "window_start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_end": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": sid,
            "signal_kind": "jit_trigger",
            "gate_key": None,
            "trigger_ids": sorted(bucket["trigger_ids"]),
            "rule_ids": [rule_id],
            "fire_count": bucket["count"],
            "evidence": bucket["evidence"],
            **judged,
        })
    for (sid, gate_key), bucket in sorted(gate_agg.items()):
        judged = llm_judge_not_evaluated(bucket["signal_kind"], bucket["evidence"])
        rows.append({
            "schema": "rule-replay-nightly/v1",
            "run_ts": run_ts,
            "window_start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_end": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": sid,
            "signal_kind": bucket["signal_kind"],
            "gate_key": gate_key,
            "trigger_ids": [],
            "rule_ids": sorted(bucket["rule_ids"]),
            "fire_count": bucket["count"],
            "evidence": bucket["evidence"],
            **judged,
        })

    signals_by_kind: dict[str, int] = {}
    for row in rows:
        signals_by_kind[row["signal_kind"]] = signals_by_kind.get(row["signal_kind"], 0) + 1

    summary = {
        "schema": "rule-replay-nightly-summary/v1",
        "run_ts": run_ts,
        "window_start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions_scanned_files": len(files),
        "sessions_scored": len(session_ids),
        "signal_rows": len(rows),
        "signals_by_kind": signals_by_kind,
    }
    return rows, summary


# ── DEFECT AUTOFEED ───────────────────────────────────────────────────────

def build_defect_proposal(row: dict) -> dict:
    signal_kind = row["signal_kind"]
    session_id = row["session_id"]
    evidence_text = "; ".join(row["evidence"])[:160]
    if signal_kind == "shadow_writing_check":
        defect_class = "shadow-writing-check-finding"
        claimed = "the assistant's final message satisfied writing-lint rule 5be2f462 (no banned construction)"
        actual = (f"conduct-stop-gate.py's shadow writing check found a banned "
                  f"construction in session {session_id}: {evidence_text}")
    else:  # delegation_material
        defect_class = "materially-under-delegated-session"
        claimed = f"session {session_id} delegated mechanical sweep work appropriately"
        actual = (f"delegation-gate.py's per-session ledger flagged session {session_id} "
                  f"as materially under-delegated: {evidence_text}")
    rule_violated = row["rule_ids"][0] if row["rule_ids"] else None
    return {
        "schema": "rule-replay-defect-proposal/v1",
        "staged_ts": now_iso(),
        "finding_key": f"{signal_kind}:{session_id}:{row.get('gate_key') or ''}",
        # Idempotency keys must be FRESH per intended action (rule 14181e60's
        # write law); staging is not the intended action of calling
        # record-defect, so none is minted here. The session that actually
        # files this proposal mints its own immediately before the call.
        "idempotency_key": None,
        "idempotency_key_note": ("mint a FRESH uuid4 immediately before calling "
                                  "record-defect -- this staged row carries none"),
        "defect_class": defect_class,
        "claimed": claimed,
        "actual": actual,
        "source_unread": row.get("session_id"),
        "rule_violated": rule_violated,
        "detected_by": "gate",
        "occurred_on": None,
        "session_key": session_id,
        "cost_note": None,
        "evidence": row["evidence"],
        "review_status": "staged",
    }


def stage_defect_proposals(rows: list[dict]) -> int:
    existing_keys: set[str] = set()
    if DEFECT_PROPOSALS_LOG.exists():
        for rec in iter_records(DEFECT_PROPOSALS_LOG):
            key = rec.get("finding_key")
            if isinstance(key, str):
                existing_keys.add(key)

    staged = 0
    os.makedirs(OUT, exist_ok=True)
    with DEFECT_PROPOSALS_LOG.open("a", encoding="utf-8") as fh:
        for row in rows:
            if not is_known_failure_signature(row["signal_kind"]):
                continue
            proposal = build_defect_proposal(row)
            if proposal["finding_key"] in existing_keys:
                continue
            existing_keys.add(proposal["finding_key"])
            fh.write(json.dumps(proposal, sort_keys=True) + "\n")
            staged += 1
    return staged


# ── CLI ───────────────────────────────────────────────────────────────────

def render_summary(summary: dict) -> str:
    lines = [
        f"rule-replay-nightly -- window {summary['window_start']} .. {summary['window_end']}",
        f"  files scanned: {summary['sessions_scanned_files']}   sessions scored: {summary['sessions_scored']}",
        f"  signal rows: {summary['signal_rows']}",
    ]
    for kind, count in sorted(summary["signals_by_kind"].items()):
        lines.append(f"    {kind}: {count}")
    lines.append(f"  defect proposals staged this run: {summary.get('defect_proposals_staged', 0)}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since-hours", type=int, default=24)
    ap.add_argument("--now", default=None, help="ISO8601 override, for tests")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-append", action="store_true",
                    help="skip both jsonl writes (tests / dry run)")
    args = ap.parse_args()

    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print(f"rule-replay-nightly: --now was not a valid ISO8601 timestamp: {args.now!r}",
              file=sys.stderr)
        return 1
    window_end = now
    window_start = now - timedelta(hours=max(1, args.since_hours))

    rows, summary = build_replay(window_start, window_end)

    staged = 0
    if not args.no_append:
        os.makedirs(OUT, exist_ok=True)
        with REPLAY_LOG.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.write(json.dumps(summary, sort_keys=True) + "\n")
        staged = stage_defect_proposals(rows)
    summary["defect_proposals_staged"] = staged

    if args.json:
        print(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str))
    else:
        print(render_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
