#!/usr/bin/env python3
"""session-brief.py — the ZERO-FILE local bootstrap (Joe, 2026-08-08:
"Couldn't that claude Md file just become the project instructions? And then
we truly don't have any Md files").

SessionStart hook for sessions rooted in the CARR vault: stdout injects the
briefing directive that CLAUDE.md used to carry, built FRESH from the store
each session — so unlike the file, it cannot go stale (the file lagged its
own rule counts by 4 within a day of the last refresh; measured 2026-08-08).

Three layers of the same directive, by surface:
  - MCP server `instructions` field  → every connector-holding session, all
    surfaces, Dell included (mcp.js)
  - Cowork project-instructions FIELD → the rev-7 pointer stub (no file)
  - THIS HOOK                         → local Claude Code, code not markdown

FAIL-SOFT IS A STOP RAIL, NOT SILENCE: if the store is unreachable the hook
still injects the identity line and the stop-and-say-so instruction — a blank
session that improvises files is the failure this whole build exists to end.
"""
from __future__ import annotations

import json
import os
import sys

PACK_DELIVERY_RAIL = (
    " RULE DELIVERY: use only exact canonical pack names from standing-context "
    "rule_delivery.pack_index. A name in packs_not_found is unknown and is NOT "
    "loaded. If observed work enters a pack absent from "
    "rule_delivery.declared_packs, call standing-context again with that canonical "
    "pack and read the result before acting. Shadow mode records drift without "
    "blocking, but does not waive this recall protocol."
)

STATIC_RAIL = (
    "CARR AI (Joe Bookout's healthcare-CRE system, partner Dell McCraney; "
    "business only — personal is Life AI). OPENING ACT: call standing-context "
    "FIRST and recite its rule counts in your first response. Doctrine and "
    "records live in the store (doctrine-index / search-doctrine / "
    "read-doctrine / catch-me-up); there are NO doctrine files. WRITE LAW "
    "(rule 14181e60): verbs only, never a .md file — a hard gate enforces it. "
    "Code lives in ~/carr-system, nowhere else. DELEGATION LATCH: if the "
    "partner says delegate, subagent, or cheapest qualified model for an active "
    "task, that authority survives new logins/data sources, phase changes, "
    "retries, continuation and compaction until the task ends or the partner "
    "revokes it. Choose the cheapest model that is still qualified to do the "
    "task correctly; this may be a peer-tier agent, never a forced downgrade. "
    "The main seat orchestrates, verifies and performs authorized "
    "writes; it does not reclaim the mechanical sweep. Before each phase, state "
    "the executor; a second inline mechanical tool call trips the delegation gate."
    + PACK_DELIVERY_RAIL
)


def dynamic_counts():
    env = os.path.expanduser("~/.config/carr/db.env")
    url = None
    with open(env) as fh:
        for line in fh:
            if line.startswith("CARR_DB_EXPORTER_URL="):
                url = line.split("=", 1)[1].strip().strip("\"'")
    if not url:
        raise RuntimeError("no exporter url")
    import psycopg
    with psycopg.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute(
            "select coalesce(personal_to,'shared'), count(*) from v_compiled_rules "
            "where coalesce(scope->>'kind','') <> 'intro_politics' "
            "group by 1")
        counts = dict(cur.fetchall())
        cur.execute(
            "select count(*) from loop_item where kind='action_required' "
            "and status='open'")
        ar = cur.fetchone()[0]
    shared = counts.pop("shared", 0)
    personal = ", ".join(f"{v} {k}-personal" for k, v in sorted(counts.items()))
    total = shared + sum(counts.values())

    # ── THE ENFORCEMENT RATIO (Joe, 2026-08-10, decision c2c02983) ──────────
    # Joe: "what mechanism is ensuring that you actually obey the rules of the
    # system? If it's just expected that you will remember to read the rules
    # then it's not adequate."
    #
    # He is right, and the session that heard it was the proof: it opened by
    # reciting 164 rule counts and then violated at least three of them —
    # handing him a shell command to paste, and putting bare identifiers in
    # front of him twice — each caught AFTER the fact by a Stop hook rather
    # than by having read the rule. The recitation was a count, not
    # comprehension.
    #
    # A bare count of 164 is therefore FALSE ASSURANCE: it reads as "164 rules
    # are in force" when the honest statement is "10 gates enforce a subset and
    # the rest are advisory". This line exists to stop the brief making a claim
    # the system cannot back. It is the smallest durable piece of the rule
    # monitor — the rest (a binding moment and a checkable signal per rule)
    # is the build that follows.
    #
    # ENFORCED means a gate that can DENY or BLOCK, not a rule that is merely
    # written down. Counted from the gate scripts actually wired into
    # settings.json, so this number falls on its own if a gate is removed.
    gates = 0
    try:
        settings = json.load(open(os.path.expanduser("~/.claude/settings.json")))
        gates = len({
            h.get("command", "")
            for group in settings.get("hooks", {}).values()
            for entry in group
            for h in entry.get("hooks", [])
            if "carr-system/hooks/" in h.get("command", "")
        })
    except Exception:
        pass

    ratio = ""
    if gates and total:
        ratio = (f" · ENFORCEMENT: {gates} gates can actually deny or block; the"
                 f" other ~{max(total - gates, 0)} rules are ADVISORY — reciting the"
                 f" count is not the same as obeying them, and this session will"
                 f" violate one it recited unless it checks before acting.")

    return (f" LIVE COUNTS to verify against standing-context: {shared} shared"
            + (f", {personal}" if personal else "")
            + (f" · {ar} action-required item(s) open" if ar else "")
            + ratio)


def nightly_verdict(log: str | None = None) -> str:
    """One line when the last nightly chain ended red, nothing when it was clean.

    Added 2026-08-10. The chain had been exiting non-zero for three nights and
    the only place that fact existed was line 1948 of out/nightly.log. It was
    found by a human reading an exit code by hand, not by any surface reporting
    it. A failure nobody is told about is indistinguishable from no failure, so
    the news now arrives at session start, where Joe cannot miss it.

    Deliberately SILENT on a good night: a line that prints every session is a
    line nobody reads by the end of the week.

    THE LAST run, not every run since the last green one (fixed 2026-08-14).
    The accumulator was deduped at each boundary but never reset, so a day with
    three red chains opened the next session by naming all of their failures at
    once. That morning it named five, four of which had already been fixed, and
    the session spent its first minutes working out which one was still real.
    Naming a fixed failure costs the same trust as printing on a green night."""
    log = log or os.path.expanduser("~/carr-system/out/nightly.log")
    if not os.path.exists(log):
        return ""
    fails: list[str] = []
    last_run_fails: list[str] = []
    last_ok = None
    for line in open(log, errors="replace"):
        if "  FAIL  " in line:
            fails.append(line.split("  FAIL  ", 1)[1].split(" (exit")[0].strip())
        elif "chain OK" in line or "FINISHED WITH FAILURES" in line:
            last_ok = "chain OK" in line
            if last_ok:
                last_run_fails = []
            else:
                seen, uniq = set(), []
                for f in fails:
                    if f not in seen:
                        seen.add(f)
                        uniq.append(f)
                last_run_fails = uniq
            # Either way this boundary ENDS a run: what follows belongs to the
            # next one, and FAIL lines past the final boundary belong to a chain
            # still in flight, which this verdict does not speak for.
            fails = []
    if last_ok is False and last_run_fails:
        return (f" ⚠ THE LAST NIGHTLY CHAIN FAILED: {', '.join(last_run_fails)}. "
                f"Say so in your first response; details in out/nightly.log and "
                f"the 'nightly chain result' row of `./run.sh health`.")
    return ""


def _newest_mtime(path):
    """Most recent mtime at `path`, descending into it when it is a directory.

    A tracked path is not always a file. A git submodule (gitlink) is a
    DIRECTORY, and a directory's own mtime records when entries were added to or
    removed from it — not when a file nested inside it was edited. Calling
    getmtime() straight on one therefore reports the moment the submodule was
    checked out, forever, and the age it implies only grows. Returns None when
    nothing readable is there, which the caller treats as "skip this path".
    """
    try:
        if not os.path.isdir(path):
            return os.path.getmtime(path)
    except OSError:
        return None
    newest = None
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            try:
                mt = os.path.getmtime(os.path.join(root, name))
            except OSError:
                continue
            if newest is None or mt > newest:
                newest = mt
    return newest


def loose_work(repo=None):
    """One line when tracked work has sat outside git past a night, else nothing.

    Added 2026-08-10, when Joe asked whether a routine should force every session
    to commit every two hours. It should not: a scheduled `git commit -a` is the
    exact operation git-writer-gate.py was built to block on 2026-08-09, after a
    sweep took another session's in-flight files and cost an hour of rebuilding.
    A timer cannot tell whose file is whose, cannot tell finished work from a
    half-applied edit, and would replace the commit message with 'auto-commit'.

    So this reports and never acts. TRACKED FILES ONLY and a 12-hour clock, for
    the same reason the health row uses them: untracked generated assets would
    make this speak every single session, and a line that always prints is a line
    nobody reads by the end of the week.

    SUBMODULES, 2026-08-21. Two separate ways this line lied about one path.
    (1) `--ignore-submodules=dirty`: a vendored submodule whose WORK TREE is
    dirty is not stranded work when the build itself dirties it — the dictation
    rig applies tools/dictation-rig/patches/*.patch on every build, so quill is
    permanently modified by design and was being announced nightly as somebody's
    lost edit. A moved gitlink COMMIT is still real uncommitted work and is
    still reported; only work-tree dirt is ignored. (2) `_newest_mtime`: a
    gitlink path is a DIRECTORY, and a directory's mtime does not move when a
    file nested inside it changes, so getmtime() on one returns the date the
    submodule was first checked out and the age only ever climbs. That reported
    a 29-hour-old edit as 324 hours old."""
    import subprocess
    if repo is None:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run(
        ["git", "status", "--porcelain", "--ignore-submodules=dirty"],
        cwd=repo, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        return ""
    oldest = None
    count = 0
    for row in out.stdout.splitlines():
        if not row.strip() or row.startswith("??"):
            continue
        rel = row[3:].strip().strip('"').split(" -> ")[-1]
        mt = _newest_mtime(os.path.join(repo, rel))
        if mt is None:
            continue
        count += 1
        if oldest is None or mt < oldest:
            oldest = mt
    if not count or oldest is None:
        return ""
    import time
    hours = (time.time() - oldest) / 3600.0
    if hours < 12:
        return ""
    return (f" ⚠ {count} tracked file(s) in ~/carr-system have sat uncommitted for "
            f"{hours:.0f}h. Mention it; commit by NAMING PATHS, never -a — the tree "
            f"may hold another session's work.")


def _seed_evidence_rows(repo=None):
    """Parse Work Request refs, states, and evidence paths from the seed migration.

    Fail-soft fallback when the exporter DB is down: the migration SQL is the
    canonical seed, and it carries the same project_context.evidence lists the
    store does. This cannot confirm live state, but it can confirm that code
    exists on disk — which is the whole point of the latch. A store outage
    cannot hide landed work through this path.

    GENERALIZED 2026-08-21: parses any program_key, not only
    carr-ai-engineering-suite-v1. The state is read from the migration row
    when present (the seed uses 'ready' for all rows); the ref and evidence
    are extracted the same way for any program.
    """
    import json as _json
    import re
    if repo is None:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    migration = os.path.join(repo, "migrations", "0125_ai_capability_program.sql")
    if not os.path.exists(migration):
        return []
    sql = open(migration, encoding="utf-8").read()
    # The seed rows look like:
    # (N, 'carr-ai-engineering-suite-v1', 'WR-AI-00X', 'Title', 'build', ...,
    #  '{"scope":"...","evidence":["path/a","path/b"],...}'::jsonb, 'joe', 'joe')
    # Generalized: any program_key, not only carr-ai-engineering-suite-v1.
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
        program_key = m.group(1)
        ctx_json = m.group(5)
        try:
            ctx = _json.loads(ctx_json)
        except Exception:
            continue
        ev = ctx.get("evidence", [])
        if not isinstance(ev, list):
            ev = []
        rows.append({"ref": ref, "state": state, "evidence": ev,
                     "program_key": program_key})
    return rows


def _oldest_ref(rows):
    """Return the oldest ref string from rows, or None when empty."""
    if not rows:
        return None
    # Sort by ref to get the oldest ordinal; refs are like WR-AI-006.
    return sorted(r.get("ref", "?") for r in rows)[0]


def _has_uncommitted_writes(repo):
    """True when this checkout has this session's own uncommitted tracked writes.

    Reuses the loose-work helpers already in this module (loose_tracked in
    loose-work-gate.py) rather than reimplementing git status parsing. A
    session that cannot tell whose file is whose must not act on the whole
    tree — but the brief can report it.

    Returns True when `git status --porcelain --ignore-submodules=dirty` has
    any tracked-modified or added entries (not untracked — untracked is not
    a session's abandoned edit).
    """
    import subprocess
    out = subprocess.run(
        ["git", "status", "--porcelain", "--ignore-submodules=dirty",
         "--untracked-files=no"],
        cwd=repo, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        return False
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        return True
    return False


def close_before_open_brief(repo=None):
    """The CLOSE-BEFORE-OPEN line when any open work blocks a new front, else ''.

    Added 2026-08-21, generalized from built_unclosed_brief. The opslang state
    machine has three stages — CONCEPTUAL, IMPLEMENTATION, CONSTRUCTION — and
    a session must name its STAGE and its WORK REQUEST. If it cannot, it is a
    side quest and must stop or file, not build.

    This brief fires when any of these are true:
      - built-unclosed: code on disk (evidence paths exist) but not
        confirmed_closed — the build landed but nobody attested it.
      - implementation-open: a work request in claimed/in_progress/verification
        — an active build session that a new side quest must not interrupt.
      - uncommitted writes: this checkout has tracked-modified files that have
        not been committed (reuses loose-work helpers, not a reimplementation).

    Names the oldest item. Does not dump a 51-row list.

    FAIL-SOFT: when the exporter DB is down, fall back to the seed migration's
    evidence paths checked against the repo. A store outage cannot hide landed
    work through this path. The seed says state='ready' for all rows, which is
    the conservative worst case — it over-reports rather than under-reports.

    REPO is the checkout root (the worktree), not a hardcoded ~/carr-system.
    The caller passes the dirname of hooks/ — the repo this hook is running
    from — so a worktree session checks its own tree, not the main checkout.
    """
    if repo is None:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Import the detector from the repo's ops/ directory
    sys.path.insert(0, os.path.join(repo, "ops"))
    try:
        import built_unclosed as _bu
    except Exception:
        return ""
    rows = _bu.load_live_rows()
    db_unreachable = rows is None
    if rows is None:
        # DB down or not configured — fall back to the seed migration
        rows = _seed_evidence_rows(repo)
    if not rows:
        rows = _seed_evidence_rows(repo)
    if not rows:
        return ""

    classified = _bu.detect_all_open(rows, repo)
    built_unclosed = classified["built_unclosed"]
    impl_open = classified["implementation_open"]
    concept_open = classified["conceptual_open"]

    # FAIL CLOSED when the DB is unreachable: the seed migration says
    # state='ready' for all rows, but the live state may include rows in
    # claimed/in_progress/verification or captured/triaged that the seed
    # cannot see. Do not silently treat a DB outage as "no open
    # implementation" — report it so the partner knows the state is unknown.
    if db_unreachable and not impl_open and not concept_open:
        oldest = _oldest_ref(rows)
        parts_open = (
            f"implementation/conceptual state UNKNOWN (exporter DB "
            f"unreachable; seed fallback reports 'ready' but live state may "
            f"have open work — oldest ref in seed: {oldest})"
        )
    else:
        parts_open = None

    parts = []

    if built_unclosed:
        oldest = _oldest_ref(built_unclosed)
        parts.append(
            f"{len(built_unclosed)} built-unclosed work request(s) have code on disk "
            f"and are not confirmed_closed (oldest: {oldest})"
        )

    if impl_open:
        oldest = _oldest_ref(impl_open)
        parts.append(
            f"{len(impl_open)} implementation-open work request(s) are in "
            f"claimed/in_progress/verification (oldest: {oldest})"
        )

    if concept_open:
        oldest = _oldest_ref(concept_open)
        parts.append(
            f"{len(concept_open)} conceptual-open work request(s) are in "
            f"captured/triaged (oldest: {oldest})"
        )

    if parts_open:
        parts.append(parts_open)

    # Uncommitted writes: this checkout has tracked-modified files
    try:
        if _has_uncommitted_writes(repo):
            parts.append(
                "this checkout has uncommitted tracked writes"
            )
    except Exception:
        pass  # the brief must never fail on this

    if not parts:
        return ""

    summary = "; ".join(parts)
    return (
        f"\nCLOSE-BEFORE-OPEN: {summary}. "
        f"Do not start a new conceptual plan, implementation, or construction "
        f"while these are open. Close or park them first. "
        f"Run ops/built_unclosed.py for details."
    )


def main():
    try:
        json.load(sys.stdin)          # hook payload; nothing needed from it
    except Exception:
        pass
    extra = ""
    # CLOSE-BEFORE-OPEN goes FIRST — before any other extra line, so a session
    # that skims the top of the brief cannot miss it.
    try:
        cbb = close_before_open_brief()
        if cbb:
            extra = cbb
    except Exception:
        pass                          # the brief must never fail on this
    try:
        sys.path.insert(0, os.path.expanduser("~/carr-system/.venv/lib"))
        extra += dynamic_counts()
    except Exception:
        extra += (" (store unreachable at session start — STOP AND SAY SO "
                 "before doing anything that would normally need it)")
    try:
        extra += nightly_verdict()
    except Exception:
        pass                          # the brief must never fail on this
    try:
        extra += loose_work()
    except Exception:
        pass
    print(STATIC_RAIL + extra)
    sys.exit(0)


if __name__ == "__main__":
    main()
