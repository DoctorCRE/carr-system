#!/usr/bin/env python3
"""close-before-open-gate-selftest.py — acceptance test for the side-quest brake.

WHY THIS EXISTS. The session brief says CLOSE-BEFORE-OPEN, but a session that
does not read the brief can still start a side quest by creating a new file
under design/, phase0/, control-room/contracts/, or a roadmap/build-plan path.
This PreToolUse gate blocks that write when open work exists.

GENERALIZED 2026-08-21. The gate now blocks on any open work —
built-unclosed (code on disk, not confirmed_closed) OR implementation-open
(claimed/in_progress/verification) — not only on built-unclosed. The
opslang state machine has three stages and the gate must cover all three.

Test-first (rule e65efc68): this file was written before the gate.

Run: python3 ops/close-before-open-gate-selftest.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GATE = os.path.join(REPO, "hooks", "close-before-open-gate.py")

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def _make_fixture_repo(root):
    """Create a fake repo with built_unclosed.py and evidence files on disk."""
    ops_dir = os.path.join(root, "ops")
    os.makedirs(ops_dir, exist_ok=True)
    shutil.copy(os.path.join(HERE, "built_unclosed.py"),
                os.path.join(ops_dir, "built_unclosed.py"))
    # Create evidence files that exist on disk
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    return root


def run(file_path, root, tool="Write", content="x = 1\n", cwd=None,
        raw_payload=None):
    """Drive the gate as a subprocess. Returns (exit_code, stderr)."""
    payload = raw_payload if raw_payload is not None else json.dumps({
        "tool_name": tool,
        "tool_input": {"file_path": file_path, "content": content},
        "cwd": cwd or os.path.dirname(file_path) or root,
    })
    env = {**os.environ, "CARR_REPO_ROOT": root}
    # Mock the DB URL so load_live_rows returns None and the seed fallback is
    # used.  Override HOME so fixtures cannot read ~/.config/carr/db.env —
    # without this the two impl-open cases pass on the live in_progress row,
    # not on the seed migration.  Hermetic or it is not a test.
    env["CARR_DB_EXPORTER_URL"] = ""
    env["HOME"] = root
    p = subprocess.run([sys.executable, GATE], input=payload,
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stderr


def _setup_unclosed(root):
    """Set up a fixture where built_unclosed is non-empty.

    The seed migration at migrations/0125 has evidence paths for WR-AI-001
    like ops/ai_eval.py and evals/ai/model-boundary.v1.json. We create those
    files on disk and point the gate at this root.
    """
    _make_fixture_repo(root)
    mig_dir = os.path.join(root, "migrations")
    os.makedirs(mig_dir, exist_ok=True)
    mig_src = os.path.join(REPO, "migrations", "0125_ai_capability_program.sql")
    if os.path.exists(mig_src):
        shutil.copy(mig_src, os.path.join(mig_dir, "0125_ai_capability_program.sql"))
    return root


# ── BLOCK: new file under design/, phase0/, control-room/contracts/ ───────

def test_block_design(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "design", "new-spec.v1.json"), root)
    check("new file under design/ is DENIED when open work exists",
          rc == 2, f"exit {rc} stderr={err[:200]}")
    check("the refusal names CLOSE-BEFORE-OPEN", "CLOSE-BEFORE-OPEN" in err, err[:200])


def test_block_phase0(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "phase0", "audit", "new-thing.v1.json"), root)
    check("new file under phase0/ is DENIED when open work exists",
          rc == 2, f"exit {rc} stderr={err[:200]}")


def test_block_contracts(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "control-room", "contracts", "new-contract.v1.json"), root)
    check("new file under control-room/contracts/ is DENIED when open work exists",
          rc == 2, f"exit {rc} stderr={err[:200]}")


# ── BLOCK: new roadmap/build-plan/action-plan path ─────────────────────────

def test_block_roadmap(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "roadmap-2026.v1.md"), root)
    check("new roadmap file is DENIED when open work exists",
          rc == 2, f"exit {rc} stderr={err[:200]}")


def test_block_build_plan(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "build-plan-q3.md"), root)
    check("new build-plan file is DENIED when open work exists",
          rc == 2, f"exit {rc} stderr={err[:200]}")


def test_block_action_plan(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "action-plan-sprint.md"), root)
    check("new action-plan file is DENIED when open work exists",
          rc == 2, f"exit {rc} stderr={err[:200]}")


# ── ALLOW: edits to existing evidence/test/hook files ──────────────────────

def test_allow_edit_existing_evidence(root):
    root = _setup_unclosed(root)
    # ops/ai_eval.py is an evidence path — editing it must be allowed
    rc, err = run(os.path.join(root, "ops", "ai_eval.py"), root,
                   content="x = 2\n")
    check("editing an existing evidence file is ALLOWED",
          rc == 0, f"exit {rc} stderr={err[:200]}")


def test_allow_edit_existing_hook(root):
    root = _setup_unclosed(root)
    # hooks/close-before-open-gate.py exists in the real repo
    rc, err = run(os.path.join(REPO, "hooks", "close-before-open-gate.py"),
                   root, content="# edited\n")
    check("editing an existing hook file is ALLOWED",
          rc == 0, f"exit {rc} stderr={err[:200]}")


# ── ALLOW: bugfixes under ops/, hooks/, mcp-server/ for this branch ────────

def test_allow_ops_bugfix(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "ops", "some-new-helper.py"), root)
    check("new file under ops/ is ALLOWED (bugfix lane)",
          rc == 0, f"exit {rc} stderr={err[:200]}")


def test_allow_hooks_bugfix(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "hooks", "some-new-gate.py"), root)
    check("new file under hooks/ is ALLOWED (bugfix lane)",
          rc == 0, f"exit {rc} stderr={err[:200]}")


def test_allow_mcp_server_bugfix(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "mcp-server", "src", "new-helper.js"), root)
    check("new file under mcp-server/ is ALLOWED (bugfix lane)",
          rc == 0, f"exit {rc} stderr={err[:200]}")


# ── ALLOW: commits / tests ──────────────────────────────────────────────────

def test_allow_selftest(root):
    root = _setup_unclosed(root)
    rc, err = run(os.path.join(root, "ops", "built-unclosed-selftest.py"), root)
    check("new selftest file is ALLOWED (test lane)",
          rc == 0, f"exit {rc} stderr={err[:200]}")


# ── ALLOW: when no open work exists ────────────────────────────────────────

def test_allow_when_nothing_open(root):
    root = tempfile.mkdtemp()
    # Empty repo: no evidence files, no migration. We must prevent the real
    # DB from being read — the gate's _exporter_url() reads ~/.config/carr/db.env
    # when the env var is empty, and the real DB has in_progress rows. Set
    # HOME to a temp dir so the db.env file is not found, and clear the env var.
    ops_dir = os.path.join(root, "ops")
    os.makedirs(ops_dir, exist_ok=True)
    shutil.copy(os.path.join(HERE, "built_unclosed.py"),
                os.path.join(ops_dir, "built_unclosed.py"))
    # Use a custom payload with a mock HOME so no db.env is found
    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"file_path": os.path.join(root, "design", "new-spec.v1.json"),
                        "content": "x = 1\n"},
        "cwd": root,
    })
    env = {**os.environ, "CARR_REPO_ROOT": root, "CARR_DB_EXPORTER_URL": "",
           "HOME": root}
    p = subprocess.run([sys.executable, GATE], input=payload,
                       capture_output=True, text=True, env=env)
    rc, err = p.returncode, p.stderr
    check("design/ is ALLOWED when no open work exists",
          rc == 0, f"exit {rc} stderr={err[:200]}")


# ── ALLOW: Edit tool on an existing forbidden path (not a new file) ─────────

def test_allow_edit_existing_design(root):
    root = _setup_unclosed(root)
    # Create the file first so it "exists"
    d = os.path.join(root, "design", "existing.v1.json")
    os.makedirs(os.path.dirname(d), exist_ok=True)
    open(d, "w").write("{}\n")
    rc, err = run(d, root, tool="Edit", content="{}\n")
    check("Edit on an existing design/ file is ALLOWED (not a new file)",
          rc == 0, f"exit {rc} stderr={err[:200]}")


# ── ALLOW: Bash tool is not this gate's lane ────────────────────────────────

def test_allow_bash(root):
    root = _setup_unclosed(root)
    rc, err = run("echo hi", root, tool="Bash")
    check("Bash tool is ALLOWED (not this gate's lane)",
          rc == 0, f"exit {rc} stderr={err[:200]}")


# ── BLOCK: implementation-open blocks even without evidence on disk ──────
# A work request in claimed/in_progress/verification has an active build
# session — the code may not be on disk yet, but opening a new front is a
# side quest. This test uses a custom payload that mocks the DB to return
# implementation-open rows.

def test_block_design_when_impl_open(root):
    """When implementation-open exists but no built-unclosed, design/ is still denied."""
    root = _make_fixture_repo(root)
    # No migration, no evidence files — but mock the DB to return impl-open rows
    # We need a custom gate invocation that patches load_live_rows. Since the
    # gate runs as a subprocess, we inject via the seed migration: create a
    # minimal migration file with a row in state 'in_progress' that has no
    # evidence on disk, so it's implementation-open but not built-unclosed.
    mig_dir = os.path.join(root, "migrations")
    os.makedirs(mig_dir, exist_ok=True)
    # Write a minimal seed migration that the gate's _seed_evidence_rows will parse
    # The gate's regex matches any program_key, WR-* ref, and a jsonb context
    mig = os.path.join(mig_dir, "0125_ai_capability_program.sql")
    open(mig, "w").write(
        "insert into ops.work_request\n"
        "  (program_ordinal, program_key, ref, title, disposition, state,\n"
        "   project_context, requester_actor, owner_actor)\n"
        "values\n"
        "  (1, 'test-suite-v1', 'WR-X-001', 'Test', 'build', 'in_progress',\n"
        "   '{\"scope\":\"test\",\"evidence\":[]}'::jsonb, 'joe', 'joe');\n"
    )
    # No evidence files exist on disk — so built_unclosed is empty, but
    # implementation_open is non-empty (state=in_progress)
    rc, err = run(os.path.join(root, "design", "new-spec.v1.json"), root)
    check("design/ is DENIED when implementation-open exists (no built-unclosed)",
          rc == 2, f"exit {rc} stderr={err[:200]}")
    check("the refusal names CLOSE-BEFORE-OPEN", "CLOSE-BEFORE-OPEN" in err, err[:200])


def test_allow_ops_when_impl_open(root):
    """Implementation-open does not block bugfixes under ops/."""
    root = _make_fixture_repo(root)
    mig_dir = os.path.join(root, "migrations")
    os.makedirs(mig_dir, exist_ok=True)
    mig = os.path.join(mig_dir, "0125_ai_capability_program.sql")
    open(mig, "w").write(
        "insert into ops.work_request\n"
        "  (program_ordinal, program_key, ref, title, disposition, state,\n"
        "   project_context, requester_actor, owner_actor)\n"
        "values\n"
        "  (1, 'test-suite-v1', 'WR-X-001', 'Test', 'build', 'in_progress',\n"
        "   '{\"scope\":\"test\",\"evidence\":[]}'::jsonb, 'joe', 'joe');\n"
    )
    rc, err = run(os.path.join(root, "ops", "some-helper.py"), root)
    check("ops/ is ALLOWED when implementation-open exists (bugfix lane)",
          rc == 0, f"exit {rc} stderr={err[:200]}")


def test_block_design_when_db_down(root):
    """When the DB is down and the seed fallback says 'ready' for everything,
    the gate still DENIES design/ — it fails closed for impl/concept because
    the live state is unknown and may have in_progress rows the seed cannot see."""
    root = _setup_unclosed(root)
    # _setup_unclosed already creates evidence files and copies the real seed
    # migration, which says state='ready' for all rows. With HOME overridden in
    # run(), load_live_rows returns None (no db.env), and the gate now fails
    # closed: impl_open is non-empty because the DB is unreachable.
    rc, err = run(os.path.join(root, "design", "new-spec.v1.json"), root)
    check("design/ is DENIED when DB is down (fail-closed for impl/concept)",
          rc == 2, f"exit {rc} stderr={err[:200]}")
    check("the refusal names CLOSE-BEFORE-OPEN", "CLOSE-BEFORE-OPEN" in err, err[:200])


def main():
    print("close-before-open gate")
    root = tempfile.mkdtemp(prefix="close-before-open-gate-selftest-")
    tests = [
        test_block_design,
        test_block_phase0,
        test_block_contracts,
        test_block_roadmap,
        test_block_build_plan,
        test_block_action_plan,
        test_allow_edit_existing_evidence,
        test_allow_edit_existing_hook,
        test_allow_ops_bugfix,
        test_allow_hooks_bugfix,
        test_allow_mcp_server_bugfix,
        test_allow_selftest,
        test_allow_when_nothing_open,
        test_allow_edit_existing_design,
        test_allow_bash,
        test_block_design_when_impl_open,
        test_allow_ops_when_impl_open,
        test_block_design_when_db_down,
    ]
    for t in tests:
        t(tempfile.mkdtemp())
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 0



# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
_dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b06'])

if __name__ == "__main__":
    sys.exit(main())
