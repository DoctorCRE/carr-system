#!/usr/bin/env python3
"""ops/session-crm-neon-selftest.py — prove the Neon-backed session CRM v0.

Tests the pipelines/session_crm_harvest.py collectors and the
pipelines/session_crm_brief.py formatter. No network, no database, no git,
no hermes CLI. All inputs are fixtures built in-memory. The harvest collectors
are tested by stubbing subprocess output; the brief formatter is a pure
function tested directly.

The upsert_to_neon() function is tested with a stubbed psycopg — we capture
the SQL it sends and prove the ON CONFLICT DO UPDATE shape, so a missing
version column (which would crash trg_touch_row on the UPDATE path) cannot
go green again.

    .venv/bin/python ops/session-crm-neon-selftest.py
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import migration_names, read_migration


import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from types import ModuleType
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
HARVEST_PATH = os.path.join(HERE, "..", "pipelines", "session_crm_harvest.py")
BRIEF_PATH = os.path.join(HERE, "..", "pipelines", "session_crm_brief.py")

failures: list[str] = []


def ok(name: str) -> None:
    print(f"  ok   {name}")


def fail(name: str, detail: str = "") -> None:
    print(f"  FAIL {name} {detail}")
    failures.append(name)


def load_module(name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"could not load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


harvest_mod = load_module("session_crm_harvest", HARVEST_PATH)
brief_mod = load_module("session_crm_brief", BRIEF_PATH)

REQUIRED_FIELDS = {"id", "kind", "title", "last_seen", "open_loop", "next_seat", "sources", "promise"}
KNOWN_KINDS = {"worktree", "pr", "hermes_session", "kanban", "promise"}


# ── test data ──────────────────────────────────────────────────────────────────

WORKTREE_PORCELAIN = """\
worktree /Users/booko/carr-system
HEAD df6aaf0811a92b63a787dfc8db383a3e673b7c84
branch refs/heads/main

worktree /Users/booko/carr-system/.claude/worktrees/session-crm-v0
HEAD df6aaf0811a92b63a787dfc8db383a3e673b7c84
branch refs/heads/session-crm-v0

worktree /Users/booko/carr-system/.claude/worktrees/silly-kalam-ee0b2d
HEAD f53045ec275eb71ed5282ea4857f3be421115aaf
branch refs/heads/claude/jolly-wilson-b71903
"""

PR_JSON = json.dumps([
    {"number": 344, "title": "Number incidents off one clock", "isDraft": False, "state": "OPEN", "headRefName": "claude/relaxed-snyder-ba6f34", "updatedAt": "2026-08-19T02:07:21Z"},
    {"number": 335, "title": "Dell's Mac runs its own tasks", "isDraft": True, "state": "OPEN", "headRefName": "fix/dell-tasks-off-drive-t83", "updatedAt": "2026-08-19T03:02:45Z"},
])

SESSIONS_OUTPUT = """\
Title                        Workspace          Last Active   ID
\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
Build Session CRM v0 in Ne   \u2014                  just now      20260818_225354_71dcb9
Work kanban task t_34f0d25   \u2014                  just now      20260818_225302_b64dc7
Report agent_principal_id    carr-system        1h ago        20260818_210114_7e8e3a
"""

KANBAN_JSON = json.dumps([
    {"id": "t_93cfd1ee", "title": "STANDING: land or kill", "status": "blocked", "assignee": None},
    {"id": "t_34f0d25d", "title": "Implement session-crm v0 harvest", "status": "running", "assignee": "builder"},
    {"id": "t_bfeef202", "title": "Reap attached worktrees", "status": "done", "assignee": "default"},
])


# ── test: row shape ───────────────────────────────────────────────────────────

def test_worktree_rows() -> None:
    """Worktree collector parses --porcelain output into valid rows."""
    with patch.object(harvest_mod, "_run", return_value=(0, WORKTREE_PORCELAIN, "")):
        rows = harvest_mod.collect_worktrees()

    if len(rows) != 3:
        fail("worktree count", f"expected 3, got {len(rows)}")
        return

    for r in rows:
        missing = REQUIRED_FIELDS - set(r.keys())
        if missing:
            fail(f"worktree {r.get('id', '?')}", f"missing {sorted(missing)}")
            return
        if r["kind"] != "worktree":
            fail(f"worktree {r['id']}", f"kind is {r['kind']}")
            return

    # main tree should have kin="main"
    main_row = [r for r in rows if r.get("kin") == "main"]
    if len(main_row) != 1:
        fail("worktree main kin", f"expected 1 main, got {len(main_row)}")
        return

    ok("worktree rows: 3 parsed, all required fields, main kin tagged")


def test_pr_rows() -> None:
    """PR collector parses gh JSON into valid rows."""
    with patch.object(harvest_mod, "_run", return_value=(0, PR_JSON, "")):
        rows = harvest_mod.collect_prs()

    if len(rows) != 2:
        fail("pr count", f"expected 2, got {len(rows)}")
        return

    for r in rows:
        missing = REQUIRED_FIELDS - set(r.keys())
        if missing:
            fail(f"pr {r.get('id', '?')}", f"missing {sorted(missing)}")
            return
        if r["kind"] != "pr":
            fail(f"pr {r['id']}", f"kind is {r['kind']}")
            return

    # Draft PR should have next_seat = "reviewer"
    draft = [r for r in rows if "draft" in r["title"]]
    if len(draft) != 1:
        fail("pr draft", "expected 1 draft")
        return
    if draft[0]["next_seat"] != "reviewer":
        fail("pr draft seat", f"expected reviewer, got {draft[0]['next_seat']}")
        return

    ok("pr rows: 2 parsed, draft tagged with reviewer seat")


def test_session_rows() -> None:
    """Hermes sessions collector parses the fixed-width table."""
    with patch.object(harvest_mod, "_run", return_value=(0, SESSIONS_OUTPUT, "")):
        rows = harvest_mod.collect_hermes_sessions()

    if len(rows) != 3:
        fail("session count", f"expected 3, got {len(rows)}")
        return

    for r in rows:
        missing = REQUIRED_FIELDS - set(r.keys())
        if missing:
            fail(f"session {r.get('id', '?')}", f"missing {sorted(missing)}")
            return
        if r["kind"] != "hermes_session":
            fail(f"session {r['id']}", f"kind is {r['kind']}")
            return

    ok("session rows: 3 parsed, all required fields")


def test_kanban_rows() -> None:
    """Kanban collector parses JSON output."""
    with patch.object(harvest_mod, "_run", return_value=(0, KANBAN_JSON, "")):
        rows = harvest_mod.collect_kanban()

    if len(rows) != 3:
        fail("kanban count", f"expected 3, got {len(rows)}")
        return

    for r in rows:
        missing = REQUIRED_FIELDS - set(r.keys())
        if missing:
            fail(f"kanban {r.get('id', '?')}", f"missing {sorted(missing)}")
            return
        if r["kind"] != "kanban":
            fail(f"kanban {r['id']}", f"kind is {r['kind']}")
            return

    # Done task should have open_loop = False
    done = [r for r in rows if not r["open_loop"]]
    if len(done) != 1:
        fail("kanban done", f"expected 1 closed, got {len(done)}")
        return

    # Blocked task should have next_seat = "unassigned"
    blocked = [r for r in rows if r["next_seat"] == "unassigned"]
    if len(blocked) != 1:
        fail("kanban unassigned", f"expected 1 unassigned, got {len(blocked)}")
        return

    ok("kanban rows: 3 parsed, done closed, blocked unassigned")


def test_promise_rows() -> None:
    """Promise collector returns the seeded promises."""
    rows = harvest_mod.collect_promises()

    if len(rows) < 4:
        fail("promise count", f"expected >= 4, got {len(rows)}")
        return

    for r in rows:
        missing = REQUIRED_FIELDS - set(r.keys())
        if missing:
            fail(f"promise {r.get('id', '?')}", f"missing {sorted(missing)}")
            return
        if r["kind"] != "promise":
            fail(f"promise {r['id']}", f"kind is {r['kind']}")
            return
        if not r["promise"]:
            fail(f"promise {r['id']}", "promise text is null")
            return

    # Check for the specific seeded promises
    ids = {r["id"] for r in rows}
    expected_ids = {
        "promise:codex-trees-stay",
        "promise:phone-doc-no-claude",
        "promise:loop-455-waits-fable",
        "promise:bot-mode-parked",
    }
    if not expected_ids.issubset(ids):
        fail("promise ids", f"missing {expected_ids - ids}")
        return

    ok(f"promise rows: {len(rows)} seeded, all have promise text, all 4 seeds present")


# ── test: harvest merge ───────────────────────────────────────────────────────

def test_harvest_merge() -> None:
    """Full harvest merges sources and produces valid rows."""
    def fake_run(cmd, *, timeout=15):
            if cmd[:2] == ["git", "worktree"]:
                return (0, WORKTREE_PORCELAIN, "")
            elif cmd[:2] == ["gh", "pr"]:
                return (0, PR_JSON, "")
            elif cmd[:2] == ["hermes", "sessions"]:
                return (0, SESSIONS_OUTPUT, "")
            elif cmd[:2] == ["hermes", "kanban"]:
                return (0, KANBAN_JSON, "")
            return (127, "", f"unknown: {cmd}")

    with patch.object(harvest_mod, "_run", side_effect=fake_run):
        rows = harvest_mod.harvest()

    errors = harvest_mod.validate_rows(rows)
    if errors:
        fail("harvest validation", errors[0])
        return

    # Should have worktrees (3) + prs (2) + sessions (3) + kanban (3) + promises (4) = 15
    # But merge is by id, so no dupes expected here
    if len(rows) != 15:
        fail("harvest count", f"expected 15, got {len(rows)}")
        return

    # Every row must have all required fields
    for r in rows:
        missing = REQUIRED_FIELDS - set(r.keys())
        if missing:
            fail(f"harvest {r.get('id', '?')}", f"missing {sorted(missing)}")
            return

    ok(f"harvest: {len(rows)} rows merged, 0 validation errors")


# ── test: validate_rows catches bad rows ──────────────────────────────────────

def test_validate_catches_missing() -> None:
    """validate_rows must fail if a required field is missing."""
    bad_rows = [
        {"id": "test:1", "kind": "worktree", "title": "T", "last_seen": "2026-01-01T00:00:00+00:00",
         "open_loop": True, "next_seat": None, "sources": [], "promise": None},
        {"id": "test:2", "kind": "promise", "title": "P", "last_seen": "2026-01-01T00:00:00+00:00",
         "open_loop": True, "next_seat": None, "sources": [], "promise": None},  # null promise!
    ]
    errors = harvest_mod.validate_rows(bad_rows)
    if not errors:
        fail("validate catches promise null", "no errors for null promise on promise-kind row")
        return

    # Should report the promise issue
    found_promise_err = any("promise" in e for e in errors)
    if not found_promise_err:
        fail("validate promise err", f"errors: {errors}")
        return

    ok("validate_rows: catches promise-kind row with null promise")


def test_validate_catches_unknown_kind() -> None:
    """validate_rows must fail if kind is not in the known set."""
    bad_rows = [
        {"id": "test:x", "kind": "UNKNOWN", "title": "X", "last_seen": "2026-01-01T00:00:00+00:00",
         "open_loop": True, "next_seat": None, "sources": [], "promise": None},
    ]
    errors = harvest_mod.validate_rows(bad_rows)
    if not errors:
        fail("validate unknown kind", "no errors for unknown kind")
        return
    ok("validate_rows: catches unknown kind")


# ── test: brief formatter ──────────────────────────────────────────────────────

def test_brief_sections() -> None:
    """Brief produces DOING, STALE, PROMISES sections."""
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)

    rows = [
        # Recent open item (doing)
        {"id": "kanban:t1", "kind": "kanban", "title": "Implement harvest", "last_seen": now.isoformat(),
         "open_loop": True, "next_seat": "builder", "sources": ["hermes_kanban_list"], "promise": None, "kin": None},
        # Stale item (>4h old)
        {"id": "worktree:main", "kind": "worktree", "title": "main",
         "last_seen": (now - timedelta(hours=6)).isoformat(),
         "open_loop": True, "next_seat": None, "sources": ["git_worktree_list"], "promise": None, "kin": "main"},
        # Closed item (should not appear in doing or stale)
        {"id": "kanban:t2", "kind": "kanban", "title": "Done task",
         "last_seen": (now - timedelta(hours=1)).isoformat(),
         "open_loop": False, "next_seat": None, "sources": ["hermes_kanban_list"], "promise": None, "kin": None},
        # Promise
        {"id": "promise:codex-trees-stay", "kind": "promise", "title": "Codex trees stay",
         "last_seen": now.isoformat(), "open_loop": True, "next_seat": "joe",
         "sources": ["seeded_promises"], "promise": "Codex control-plane trees stay.", "kin": None},
    ]

    text = brief_mod.format_brief(rows, now=now)

    if "DOING" not in text:
        fail("brief doing", "missing DOING section")
        return
    if "STALE" not in text:
        fail("brief stale", "missing STALE section")
        return
    if "PROMISES" not in text:
        fail("brief promises", "missing PROMISES section")
        return

    # The stale worktree should be in the STALE section
    if "main" not in text:
        fail("brief stale content", "stale item 'main' not in output")
        return

    # The promise text should appear
    if "Codex control-plane trees stay" not in text:
        fail("brief promise content", "promise text not in output")
        return

    # The closed item should NOT appear in DOING or STALE
    if "Done task" in text:
        fail("brief closed leak", "closed task appeared in brief")
        return

    # KIN section should appear (the main worktree has kin="main")
    if "KIN" not in text:
        fail("brief kin", "missing KIN section despite kin=main row")
        return

    ok("brief: DOING/STALE/PROMISES/KIN sections present, closed excluded, content correct")


def test_brief_empty() -> None:
    """Brief on empty rows does not crash."""
    text = brief_mod.format_brief([])
    if "DOING" not in text:
        fail("brief empty", "missing DOING on empty")
        return
    ok("brief: handles empty row list without crashing")


def test_brief_stale_threshold() -> None:
    """Items exactly at 4h are NOT stale; items at 4h+1s ARE stale."""
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)

    rows = [
        {"id": "test:at4h", "kind": "kanban", "title": "At 4h",
         "last_seen": (now - timedelta(hours=4, seconds=0)).isoformat(),
         "open_loop": True, "next_seat": None, "sources": [], "promise": None, "kin": None},
        {"id": "test:over4h", "kind": "kanban", "title": "Over 4h",
         "last_seen": (now - timedelta(hours=4, seconds=1)).isoformat(),
         "open_loop": True, "next_seat": None, "sources": [], "promise": None, "kin": None},
    ]

    text = brief_mod.format_brief(rows, now=now)

    # "At 4h" should NOT be in the stale section
    if "STALE" in text:
        stale_section = text.split("STALE")[1]
        if "At 4h" in stale_section:
            fail("brief threshold", "4h-exact item shown as stale")
            return

    # "Over 4h" SHOULD be in the stale section
    stale_section = text.split("STALE")[1] if "STALE" in text else ""
    if "Over 4h" not in stale_section:
        fail("brief threshold", "4h+1s item NOT shown as stale")
        return

    ok("brief: 4h boundary correct (exactly 4h = not stale, 4h+1s = stale)")


# ── test: harvest is deterministic (no model) ─────────────────────────────────

def test_harvest_deterministic() -> None:
    """Running harvest twice with the same stubs produces identical rows."""
    def fake_run(cmd, *, timeout=15):
            if cmd[:2] == ["git", "worktree"]:
                return (0, WORKTREE_PORCELAIN, "")
            elif cmd[:2] == ["gh", "pr"]:
                return (0, PR_JSON, "")
            elif cmd[:2] == ["hermes", "sessions"]:
                return (0, SESSIONS_OUTPUT, "")
            elif cmd[:2] == ["hermes", "kanban"]:
                return (0, KANBAN_JSON, "")
            return (127, "", f"unknown: {cmd}")

    with patch.object(harvest_mod, "_run", side_effect=fake_run):
        rows1 = harvest_mod.harvest()

    with patch.object(harvest_mod, "_run", side_effect=fake_run):
        rows2 = harvest_mod.harvest()

    # Compare ids and kinds (last_seen will differ by microseconds, so sort and compare structural fields)
    ids1 = sorted(r["id"] for r in rows1)
    ids2 = sorted(r["id"] for r in rows2)
    if ids1 != ids2:
        fail("deterministic ids", f"run1: {ids1}\nrun2: {ids2}")
        return

    # Compare kinds, titles, promises for each id
    by_id1 = {r["id"]: r for r in rows1}
    by_id2 = {r["id"]: r for r in rows2}
    for rid in ids1:
        if by_id1[rid]["kind"] != by_id2[rid]["kind"]:
            fail(f"deterministic {rid}", f"kind: {by_id1[rid]['kind']} vs {by_id2[rid]['kind']}")
            return
        if by_id1[rid]["title"] != by_id2[rid]["title"]:
            fail(f"deterministic {rid}", "title differs")
            return
        if by_id1[rid]["promise"] != by_id2[rid]["promise"]:
            fail(f"deterministic {rid}", "promise differs")
            return

    ok(f"deterministic: 2 runs produced identical structure ({len(rows1)} rows)")


# ── test: upsert_to_neon proves the UPDATE half ───────────────────────────────

def test_upsert_to_neon() -> None:
    """upsert_to_neon sends INSERT ... ON CONFLICT DO UPDATE and counts insert vs update.

    This is the test that proves the UPDATE half. Without a version column,
    trg_touch_row() would crash on the ON CONFLICT DO UPDATE path because the
    trigger does `new.version := old.version + 1` and session_work would have no
    version field. We stub psycopg so no database is needed, and assert the SQL
    shape plus the insert/update accounting.
    """
    # ── stub psycopg ──────────────────────────────────────────────────────────
    seen_ids: set[str] = set()
    executed_sql: list[str] = []

    class FakeCursor:
        def execute(self, sql, params=None):
            executed_sql.append(sql)
            # The RETURNING clause is: returning (xmax = 0) as was_insert
            # For a fresh insert, xmax = 0 → True. For an update, xmax != 0 → False.
            row_id = params[0] if params else "?"
            is_insert = row_id not in seen_ids
            seen_ids.add(row_id)
            self._result = (is_insert,)

        def fetchone(self):
            return self._result

        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self): return FakeCursor()
        def commit(self): pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

    from unittest.mock import MagicMock
    fake_psycopg = MagicMock()
    fake_psycopg.connect = lambda dsn: FakeConn()

    original_psycopg = sys.modules.get("psycopg")
    sys.modules["psycopg"] = fake_psycopg
    try:
        rows = [
            {"id": "worktree:main", "kind": "worktree", "title": "main",
             "last_seen": "2026-08-19T00:00:00+00:00", "open_loop": True,
             "next_seat": None, "sources": ["git"], "promise": None, "kin": "main"},
            {"id": "worktree:main", "kind": "worktree", "title": "main",
             "last_seen": "2026-08-19T01:00:00+00:00", "open_loop": True,
             "next_seat": None, "sources": ["git"], "promise": None, "kin": "main"},
            {"id": "pr:344", "kind": "pr", "title": "#344 test",
             "last_seen": "2026-08-19T00:00:00+00:00", "open_loop": True,
             "next_seat": None, "sources": ["gh"], "promise": None, "kin": None},
        ]
        inserted, updated = harvest_mod.upsert_to_neon(rows, "postgresql://fake")
    finally:
        if original_psycopg is not None:
            sys.modules["psycopg"] = original_psycopg
        else:
            sys.modules.pop("psycopg", None)

    if not executed_sql:
        fail("upsert no sql", "upsert_to_neon executed no SQL")
        return

    # Every statement must contain ON CONFLICT DO UPDATE
    for sql in executed_sql:
        sql_l = sql.lower()
        if "on conflict" not in sql_l or "do update" not in sql_l:
            fail("upsert sql shape", f"missing ON CONFLICT DO UPDATE")
            return

    # First row = insert, second row (same id) = update, third = insert
    if inserted != 2:
        fail("upsert insert count", f"expected 2 inserts, got {inserted}")
        return
    if updated != 1:
        fail("upsert update count", f"expected 1 update, got {updated}")
        return

    # The migration must have a version column — without it trg_touch_row()
    # crashes on the UPDATE path. This is the guard that prevents a missing
    # version from going green again.
    mig_path = Path(HERE).parent / "migrations" / "0193_session_work.sql"
    sql_text = read_migration(mig_path.parent.parent, mig_path.name)
    if "version" not in sql_text:
        fail("upsert version column", "migration 0193 has no 'version' column — trg_touch_row will crash")
        return

    ok(f"upsert: {inserted} inserts + {updated} updates via ON CONFLICT DO UPDATE, migration has version column")


# ── test: migration file exists and is well-formed ────────────────────────────

def test_migration_exists() -> None:
    """The migration file exists and contains the session_work table."""
    mig_path = Path(HERE).parent / "migrations" / "0193_session_work.sql"
    if not mig_path.name in migration_names(mig_path.parent.parent):
        fail("migration exists", f"not found at {mig_path}")
        return

    sql = read_migration(mig_path.parent.parent, mig_path.name)
    required = [
        "create table if not exists session_work",
        "version     int not null default 1",
        "grant insert, update on session_work to carr_jobs",
        "grant select on session_work to carr_reader",
        "no role in this schema deletes",
    ]
    for fragment in required:
        if fragment not in sql:
            fail("migration content", f"missing fragment: {fragment}")
            return

    ok("migration: 0193_session_work.sql exists and contains required DDL (incl. version column)")


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    print("session-crm-neon-selftest")
    print()

    test_migration_exists()
    test_worktree_rows()
    test_pr_rows()
    test_session_rows()
    test_kanban_rows()
    test_promise_rows()
    test_harvest_merge()
    test_validate_catches_missing()
    test_validate_catches_unknown_kind()
    test_brief_sections()
    test_brief_empty()
    test_brief_stale_threshold()
    test_harvest_deterministic()
    test_upsert_to_neon()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("PASSED: all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
