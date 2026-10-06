#!/usr/bin/env python3
"""close-before-open-gate.py — the side-quest brake.

WHY THIS EXISTS. The session brief says CLOSE-BEFORE-OPEN, but a session that
does not read the brief can still start a side quest by creating a new file
under design/, phase0/, control-room/contracts/, or a roadmap/build-plan/
action-plan path. This PreToolUse gate blocks that write when open work
exists — the same open classification session-brief.py computes.

GENERALIZED 2026-08-21. The opslang state machine has three stages:

  CONCEPTUAL:     captured → triaged → ready
  IMPLEMENTATION: claimed → in_progress → verification
  CONSTRUCTION:   verification → awaiting_release → released → confirmed_closed

A session must name its STAGE and its WORK REQUEST. If it cannot, it is a side
quest and must stop or file, not build. This gate blocks new conceptual
artifacts when any of these are open:
  - built-unclosed: code on disk, not confirmed_closed
  - implementation-open: claimed/in_progress/verification

WHAT IT DENIES, when open work exists:
  - a NEW file under design/, phase0/, control-room/contracts/
  - a NEW *roadmap* / *build-plan* / *action-plan* path (any directory)

WHAT IT DOES NOT BLOCK:
  - edits to existing evidence/test/hook files that implement THIS latch
  - bugfixes under ops/, hooks/, mcp-server/ for this branch
  - commits / tests
  - the Edit tool on an existing file (creation is the tell, not editing)
  - Bash (not this gate's lane)

FAIL OPEN ON ERROR, CLOSED ONLY ON A REAL MATCH — same convention as
record-home-gate.py and bash-write-gate.py: a gate that wedges a session
costs more than the marginal safety of failing closed on a single-operator
machine.

Run: wired as a PreToolUse hook in .claude/settings.json and
ops/config/codex-hooks.json.
"""
from __future__ import annotations

import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")

# Directories where a new file starts a side quest.
SIDE_QUEST_DIRS = [
    "design/",
    "phase0/",
    "control-room/contracts/",
]

# Filename patterns that start a side quest regardless of directory.
SIDE_QUEST_PATTERNS = [
    re.compile(r"roadmap", re.IGNORECASE),
    re.compile(r"build.plan", re.IGNORECASE),
    re.compile(r"action.plan", re.IGNORECASE),
]


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"close-before-open-gate {msg.rstrip()}\n")
    except Exception:
        pass


def _repo_root():
    """The repo root, from CARR_REPO_ROOT env or script-relative.

    Realpath'd so it matches os.path.realpath on file paths — on macOS
    /var/folders symlinks to /private/var/folders, and without this the
    relpath would be ../../../../../../private/var/... and prefix matching
    would silently fail.
    """
    env = os.environ.get("CARR_REPO_ROOT")
    if env and os.path.isdir(env):
        return os.path.realpath(env)
    return os.path.realpath(REPO)


def _load_open_work(repo):
    """Load and classify open work requests. Returns (built_unclosed, impl_open).

    Returns ([], []) when no open work is detected — the gate allows everything
    in that case. Uses the generalized load_live_rows(program_key=None) which
    queries all programs, and detect_all_open which classifies by stage.

    FAIL CLOSED FOR IMPL/CONCEPT WHEN THE DB IS DOWN: when load_live_rows
    returns None, the exporter DB is unreachable. The seed migration says
    state='ready' for all rows, but the LIVE state may include rows in
    claimed/in_progress/verification or captured/triaged that the seed cannot
    see. Rather than silently treating a DB outage as "no open implementation,"
    return a sentinel impl-open row so the gate blocks a new front. The seed's
    evidence paths are still used for built_unclosed — code on disk is
    checkable without the DB.
    """
    sys.path.insert(0, os.path.join(repo, "ops"))
    try:
        import built_unclosed as _bu
    except Exception:
        return [], []
    rows = _bu.load_live_rows()
    if rows is None:
        # DB down: use the seed for built_unclosed (evidence on disk is
        # checkable), but fail closed for impl_open — the live state may
        # have in_progress rows the seed cannot see.
        seed_rows = _seed_evidence_rows(repo)
        if not seed_rows:
            return [], []
        classified = _bu.detect_all_open(seed_rows, repo)
        impl = classified["implementation_open"]
        concept = classified["conceptual_open"]
        if not impl and not concept:
            # The seed says 'ready' for everything, but the DB is down —
            # the live state is unknown. Fail closed: block a new front
            # because implementation work may be in progress.
            impl = [{"ref": seed_rows[0].get("ref", "?"),
                     "state": "unknown (DB unreachable)",
                     "evidence": []}]
        return classified["built_unclosed"], impl
    if not rows:
        return [], []
    classified = _bu.detect_all_open(rows, repo)
    return classified["built_unclosed"], classified["implementation_open"]


def _seed_evidence_rows(repo):
    """Parse evidence paths from the seed migration (fail-soft fallback).

    Generalized: parses any program_key, not only carr-ai-engineering-suite-v1.
    """
    migration = os.path.join(repo, "migrations", "0125_ai_capability_program.sql")
    if not os.path.exists(migration):
        return []
    sql = open(migration, encoding="utf-8").read()
    pattern = re.compile(
        r"\(\s*\d+\s*,\s*'([^']+)'\s*,\s*"
        r"'(WR-[^']+)'\s*,\s*'[^']*'\s*,\s*"
        r"'(build|extend|adopt|decline)'"
        r"[\s\S]*?'(claimed|in_progress|verification|captured|triaged|ready|"
        r"awaiting_release|released|confirmed_closed|blocked|failed|needs_joe)'"
        r"[\s\S]*?'(\{[^']+\})'::jsonb"
    )
    rows = []
    for m in pattern.finditer(sql):
        ref = m.group(2)
        state = m.group(4)
        try:
            ctx = json.loads(m.group(5))
        except Exception:
            continue
        ev = ctx.get("evidence", [])
        if not isinstance(ev, list):
            ev = []
        rows.append({"ref": ref, "state": state, "evidence": ev})
    return rows


def _is_new_file(path, cwd):
    """True when the file does not already exist on disk (a creation, not an edit)."""
    full = path
    if not os.path.isabs(full):
        full = os.path.join(cwd or os.getcwd(), path)
    full = os.path.realpath(full)
    return not os.path.exists(full)


def _is_side_quest(path, cwd):
    """True when the path matches a side-quest pattern.

    Matches:
      - under design/, phase0/, control-room/contracts/ (directory prefix)
      - filename containing roadmap/build-plan/action-plan
    """
    full = path
    if not os.path.isabs(full):
        full = os.path.join(cwd or os.getcwd(), path)
    full = os.path.realpath(full)

    repo = _repo_root()
    try:
        rel = os.path.relpath(full, repo).replace(os.sep, "/")
    except Exception:
        rel = path

    # Directory prefixes
    for d in SIDE_QUEST_DIRS:
        if rel.startswith(d) or rel == d.rstrip("/"):
            return True

    # Filename patterns
    basename = os.path.basename(rel)
    for pat in SIDE_QUEST_PATTERNS:
        if pat.search(basename):
            return True

    return False


def check(tool, ti, cwd):
    """Return a denial reason string, or None to allow."""
    if tool in ("apply_patch", "functions.apply_patch"):
        body = ti if isinstance(ti, str) else (ti.get("patch") or ti.get("input") or "")
        for path in re.findall(r"^\*\*\* Add File: (.+)$", body, re.M):
            reason = check("Write", {"file_path": path}, cwd)
            if reason:
                return reason
        return None
    path = ti.get("file_path") or ti.get("filePath") or ""
    if not path:
        return None

    # Only block NEW files (creation, not editing)
    if not _is_new_file(path, cwd):
        return None

    repo = _repo_root()
    built_unclosed, impl_open = _load_open_work(repo)
    if not built_unclosed and not impl_open:
        return None

    if _is_side_quest(path, cwd):
        all_open = built_unclosed + impl_open
        # Deduplicate by ref — a verification row with evidence on disk is
        # both built-unclosed and implementation-open.
        seen = set()
        unique = []
        for r in all_open:
            ref = r.get("ref", "?")
            if ref not in seen:
                seen.add(ref)
                unique.append(r)
        refs = ", ".join(r.get("ref", "?") for r in unique[:10])
        return (
            f"CLOSE-BEFORE-OPEN: {len(unique)} open work request(s) block a "
            f"new front: {refs}. Do not start a new conceptual plan, "
            f"implementation, or construction. Close or park them first. "
            f"Run ops/built_unclosed.py for details."
        )

    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        cwd = payload.get("cwd") or os.getcwd()

        # Only Write/Edit/MultiEdit/apply_patch are in this gate's lane
        if tool not in ("Write", "Edit", "MultiEdit", "apply_patch",
                         "functions.apply_patch"):
            sys.exit(0)

        reason = check(tool, ti, cwd)
        if reason:
            log(f"DENY {tool} :: {reason[:220]}")
            print(f"BLOCKED by the CARR close-before-open gate: {reason}",
                  file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
