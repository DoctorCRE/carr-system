#!/usr/bin/env python3
"""ops/session-trace-archive-selftest.py -- selftest for
ops/session-trace-archive.py and its scope predicate
lib/carr_session_scope.py, entirely against synthetic temp directories.

Never touches ~/.claude, ~/.codex, or ~/carr-local: every case builds its own
tmp "claude projects", "codex sessions" and "archive root" and points
run_archive() at those, with a fixed synthetic home and checkout so the scope
rule is exercised deterministically on any machine. Fixture transcripts are
synthetic one-liners; no real transcript is ever read.

Each check returns (ok, detail) and prints one PASS/FAIL line; main() also
runs MUTANTS -- deliberately broken variants of the job -- and asserts each
one is caught by at least one check, so the suite fails on real regressions
rather than passing by construction.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import importlib.util  # noqa: E402

from lib import carr_session_scope as scope  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "session_trace_archive", os.path.join(HERE, "session-trace-archive.py"))
assert _SPEC is not None and _SPEC.loader is not None
sta = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sta)

HOME = "/Users/booko"
CHECKOUT = "/Users/booko/carr-system"
REPO_DIR = "-Users-booko-carr-system"
WORKTREE_DIR = "-Users-booko-carr-system--claude-worktrees-agent-x1"
VAULT_DIR = "-Users-booko-My-Drive-CARR-AI"
CLOUD_VAULT_DIR = ("-Users-booko-Library-CloudStorage-GoogleDrive-someone-gmail-com"
                   "-My-Drive-CARR-AI")
OTHER_DIR = "-Users-booko-life-ai"
OLD = time.time() - 3600  # well outside the 10-minute quiet window


def _mkdirs():
    root = Path(tempfile.mkdtemp(prefix="session-trace-archive-selftest."))
    claude = root / "claude-projects"
    codex = root / "codex-sessions"
    archive = root / "archive"
    claude.mkdir()
    codex.mkdir()
    return root, claude, codex, archive


def _write(path: Path, content: bytes, mtime: float | None = OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _run(run_archive, claude, codex, archive, now=None):
    return (run_archive or sta.run_archive)(
        claude_projects_dir=claude, codex_sessions_dir=codex, archive_root=archive,
        now=time.time() if now is None else now, home=HOME, checkout=CHECKOUT)


def _gz(archive: Path, key: str) -> Path:
    return archive / (key + ".gz")


def _unz(path: Path) -> bytes:
    with gzip.open(path, "rb") as fh:
        return fh.read()


def _mode_bits(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _codex_meta(cwd: str) -> bytes:
    return (json.dumps({"timestamp": "2026-09-01T00:00:00Z", "type": "session_meta",
                        "payload": {"id": "s1", "cwd": cwd}}) + "\n"
            + '{"type":"response_item","payload":{}}\n').encode()


@contextlib.contextmanager
def _tmp():
    root, claude, codex, archive = _mkdirs()
    try:
        yield root, claude, codex, archive
    finally:
        for p in root.rglob("*"):
            with contextlib.suppress(OSError):
                if not p.is_symlink():
                    os.chmod(p, 0o700)
        shutil.rmtree(root, ignore_errors=True)


# ── Checks ───────────────────────────────────────────────────────────────

def check_new_file_copied(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        src = claude / REPO_DIR / "s1.jsonl"
        _write(src, b'{"line": 1}\n')
        s = _run(run_archive, claude, codex, archive)
        dest = _gz(archive, f"claude/{REPO_DIR}/s1.jsonl")
        ok = s["copied"] == 1 and dest.exists() and _unz(dest) == src.read_bytes()
        return ok, f"summary={s}"


def check_unchanged_file_skipped(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        _write(claude / REPO_DIR / "s1.jsonl", b'{"line": 1}\n')
        _run(run_archive, claude, codex, archive)
        dest = _gz(archive, f"claude/{REPO_DIR}/s1.jsonl")
        before = dest.read_bytes()
        s = _run(run_archive, claude, codex, archive)
        ok = s["copied"] == 0 and s["skipped_unchanged"] == 1 and dest.read_bytes() == before
        return ok, f"summary2={s}"


def check_grown_file_recopied(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        src = claude / REPO_DIR / "s1.jsonl"
        _write(src, b'{"line": 1}\n')
        _run(run_archive, claude, codex, archive)
        _write(src, b'{"line": 1}\n{"line": 2}\n', mtime=OLD + 5)
        s = _run(run_archive, claude, codex, archive)
        dest = _gz(archive, f"claude/{REPO_DIR}/s1.jsonl")
        ok = s["copied"] == 1 and _unz(dest) == src.read_bytes()
        return ok, f"summary2={s}"


def check_same_size_rewrite_rearchived(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        src = claude / REPO_DIR / "s1.jsonl"
        _write(src, b'{"line": "a"}\n')
        _run(run_archive, claude, codex, archive)
        _write(src, b'{"line": "b"}\n', mtime=OLD + 5)  # same size, new mtime
        s = _run(run_archive, claude, codex, archive)
        dest = _gz(archive, f"claude/{REPO_DIR}/s1.jsonl")
        kept = list(dest.parent.glob("s1.superseded-*.jsonl.gz"))
        ok = (s["copied"] == 1 and _unz(dest) == b'{"line": "b"}\n'
              and len(kept) == 1 and _unz(kept[0]) == b'{"line": "a"}\n')
        return ok, f"summary2={s} kept={len(kept)}"


def check_shrunk_file_rearchived(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        src = claude / REPO_DIR / "s1.jsonl"
        _write(src, b'{"line": 1}\n{"line": 2}\n')
        _run(run_archive, claude, codex, archive)
        _write(src, b'{"line": 9}\n', mtime=OLD + 5)
        s = _run(run_archive, claude, codex, archive)
        dest = _gz(archive, f"claude/{REPO_DIR}/s1.jsonl")
        kept = list(dest.parent.glob("s1.superseded-*.jsonl.gz"))
        ok = (s["copied"] == 1 and _unz(dest) == b'{"line": 9}\n'
              and len(kept) == 1 and _unz(kept[0]) == b'{"line": 1}\n{"line": 2}\n')
        return ok, f"summary2={s} kept={len(kept)}"


def check_recent_file_skipped(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        now = time.time()
        _write(claude / REPO_DIR / "s1.jsonl", b'{"line": 1}\n', mtime=now - 60)
        s = _run(run_archive, claude, codex, archive, now=now)
        ok = (s["copied"] == 0 and s["skipped_recent"] == 1
              and not _gz(archive, f"claude/{REPO_DIR}/s1.jsonl").exists())
        return ok, f"summary={s}"


def check_archive_never_deletes(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        src1 = claude / REPO_DIR / "s1.jsonl"
        _write(src1, b'{"line": 1}\n')
        _write(claude / VAULT_DIR / "s2.jsonl", b'{"line": 1}\n')
        _run(run_archive, claude, codex, archive)
        src1.unlink()
        _run(run_archive, claude, codex, archive)
        d1 = _gz(archive, f"claude/{REPO_DIR}/s1.jsonl")
        d2 = _gz(archive, f"claude/{VAULT_DIR}/s2.jsonl")
        return d1.exists() and d2.exists(), f"d1={d1.exists()} d2={d2.exists()}"


def check_permissions(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        _write(claude / REPO_DIR / "s1.jsonl", b'{"line": 1}\n')
        _run(run_archive, claude, codex, archive)
        dest = _gz(archive, f"claude/{REPO_DIR}/s1.jsonl")
        manifest = archive / "manifest.jsonl"
        dirs_ok = all(_mode_bits(p) == 0o700
                      for p in (archive, archive / "claude", archive / "claude" / REPO_DIR))
        files_ok = _mode_bits(dest) == 0o600 and _mode_bits(manifest) == 0o600
        return dirs_ok and files_ok, f"dirs_ok={dirs_ok} files_ok={files_ok}"


def check_manifest_correct(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        content = b'{"line": 1}\n{"line": 2}\n'
        src = claude / REPO_DIR / "s1.jsonl"
        _write(src, content)
        _run(run_archive, claude, codex, archive)
        lines = [l for l in (archive / "manifest.jsonl").read_text().splitlines() if l.strip()]
        if len(lines) != 1:
            return False, f"n_lines={len(lines)}"
        row = json.loads(lines[0])
        st = src.stat()
        ok = (row.get("size") == len(content)
              and row.get("mtime_ns") == st.st_mtime_ns
              and row.get("sha256") == hashlib.sha256(content).hexdigest()
              and row.get("source") == "claude"
              and row.get("key") == f"claude/{REPO_DIR}/s1.jsonl"
              and str(claude) not in lines[0])  # no full source path recorded
        return ok, f"row={row}"


def check_missing_codex_dir_is_fine(run_archive=None):
    with _tmp() as (root, claude, _, archive):
        _write(claude / REPO_DIR / "s1.jsonl", b'{"line": 1}\n')
        try:
            s = _run(run_archive, claude, root / "no-codex", archive)
        except Exception as exc:  # noqa: BLE001 - a raise here IS the failure
            return False, f"raised: {type(exc).__name__}"
        return s["copied"] == 1 and not s["errors"], f"summary={s}"


def check_other_projects_not_archived(run_archive=None):
    """The flipped assertion: a non-CARR project (proj-b) is NOT archived."""
    with _tmp() as (_, claude, codex, archive):
        _write(claude / REPO_DIR / "s1.jsonl", b'{"line": 1}\n')
        _write(claude / "proj-b" / "s2.jsonl", b'{"line": 1}\n')
        _write(claude / OTHER_DIR / "s3.jsonl", b'{"line": 1}\n')
        _write(claude / "-private-tmp-CARR-AI-scratchpad" / "s4.jsonl", b'{"line": 1}\n')
        s = _run(run_archive, claude, codex, archive)
        leaked = [p for p in (archive / "claude").iterdir() if p.name != REPO_DIR] \
            if (archive / "claude").is_dir() else []
        ok = (s["copied"] == 1 and s["skipped_out_of_scope_projects"] == 3 and not leaked)
        return ok, f"summary={s} leaked={[p.name for p in leaked]}"


def check_worktree_and_vault_projects_included(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        for d in (WORKTREE_DIR, VAULT_DIR, CLOUD_VAULT_DIR):
            _write(claude / d / "s.jsonl", b'{"line": 1}\n')
        _write(claude / WORKTREE_DIR / "sess" / "subagents" / "a.jsonl", b'{"x": 1}\n')
        s = _run(run_archive, claude, codex, archive)
        ok = (s["copied"] == 4
              and _gz(archive, f"claude/{WORKTREE_DIR}/s.jsonl").exists()
              and _gz(archive, f"claude/{WORKTREE_DIR}/sess/subagents/a.jsonl").exists())
        return ok, f"summary={s}"


def check_codex_scoped_by_metadata_cwd(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        day = codex / "2026" / "09" / "01"
        _write(day / "rollout-carr.jsonl", _codex_meta(CHECKOUT + "/.claude/worktrees/w1"))
        _write(day / "rollout-vault.jsonl",
               _codex_meta(HOME + "/Library/CloudStorage/GoogleDrive-x/My Drive/CARR AI/Deals"))
        _write(day / "rollout-other.jsonl", _codex_meta(HOME + "/life-ai"))
        _write(day / "rollout-lookalike.jsonl", _codex_meta(CHECKOUT + "-other"))
        s = _run(run_archive, claude, codex, archive)
        base = "codex/2026/09/01/"
        ok = (s["copied"] == 2 and s["skipped_codex_out_of_scope"] == 2
              and _gz(archive, base + "rollout-carr.jsonl").exists()
              and _gz(archive, base + "rollout-vault.jsonl").exists()
              and not _gz(archive, base + "rollout-other.jsonl").exists()
              and not _gz(archive, base + "rollout-lookalike.jsonl").exists())
        return ok, f"summary={s}"


def check_codex_no_metadata_skipped(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        _write(codex / "rollout-empty.jsonl", b"")
        _write(codex / "rollout-garbage.jsonl", b"not json\n")
        _write(codex / "rollout-nocwd.jsonl",
               b'{"type":"session_meta","payload":{"id":"x"}}\n')
        s = _run(run_archive, claude, codex, archive)
        ok = (s["copied"] == 0 and s["skipped_codex_no_metadata"] == 3
              and not (archive / "codex").exists())
        return ok, f"summary={s}"


def check_gzip_round_trips(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        content = b"".join(b'{"n": %d, "pad": "%s"}\n' % (i, b"x" * (i % 97))
                           for i in range(5000))
        src = claude / REPO_DIR / "big.jsonl"
        _write(src, content)
        _run(run_archive, claude, codex, archive)
        dest = _gz(archive, f"claude/{REPO_DIR}/big.jsonl")
        raw = dest.read_bytes()
        ok = raw[:2] == b"\x1f\x8b" and _unz(dest) == content and len(raw) < len(content)
        return ok, f"gz={len(raw)} src={len(content)}"


def check_unreadable_file_is_error_run_continues(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        bad = claude / REPO_DIR / "locked.jsonl"
        _write(bad, b'{"line": 1}\n')
        _write(claude / REPO_DIR / "ok.jsonl", b'{"line": 1}\n')
        os.chmod(bad, 0)
        s = _run(run_archive, claude, codex, archive)
        errs = s["errors"]
        ok = (len(errs) == 1 and "locked.jsonl" in errs[0]
              and str(claude) not in errs[0] and "/" not in errs[0].split(":")[0][-40:]
              and s["copied"] == 1
              and _gz(archive, f"claude/{REPO_DIR}/ok.jsonl").exists()
              and not list(archive.rglob("*.partial-*")))
        return ok, f"summary={s}"


def check_stale_partials_removed(run_archive=None):
    with _tmp() as (_, claude, codex, archive):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        dead_pid = proc.pid
        live_pid = os.getppid()
        d = archive / "claude" / REPO_DIR
        d.mkdir(parents=True)
        dead = d / f"s1.jsonl.gz.partial-{dead_pid}"
        live = d / f"s2.jsonl.gz.partial-{live_pid}"
        dead.write_bytes(b"x")
        live.write_bytes(b"x")
        s = _run(run_archive, claude, codex, archive)
        ok = s["stale_partials_removed"] == 1 and not dead.exists() and live.exists()
        return ok, f"summary={s}"


def check_predicate_units(run_archive=None):
    yes = [REPO_DIR, WORKTREE_DIR, VAULT_DIR, CLOUD_VAULT_DIR]
    no = ["proj-b", OTHER_DIR, "-Users-booko-carr-systemx", "-Users-booko",
          "-private-tmp-claude-501--Users-booko-carr-system-abc-scratchpad",
          "-Users-booko-My-Drive-CARR-AI-scratchpad"]
    cwd_yes = [CHECKOUT, CHECKOUT + "/ops", HOME + "/My Drive/CARR AI",
               HOME + "/Library/CloudStorage/GoogleDrive-a/My Drive/CARR AI/x"]
    cwd_no = [HOME, CHECKOUT + "-other", HOME + "/My Drive/CARR AIx",
              HOME + "/Library/CloudStorage/GoogleDrive-a/My Drive/Life",
              "relative/carr-system", None, 7, CHECKOUT + "/../life"]
    bad = ([n for n in yes if not scope.is_carr_claude_project(n, checkout=CHECKOUT)]
           + [n for n in no if scope.is_carr_claude_project(n, checkout=CHECKOUT)]
           + [c for c in cwd_yes if not scope.is_carr_cwd(c, home=HOME, checkout=CHECKOUT)]
           + [c for c in cwd_no if scope.is_carr_cwd(c, home=HOME, checkout=CHECKOUT)])
    return not bad, f"misclassified={bad}"


def check_peak_memory_80mb(run_archive=None):
    """An 80MB synthetic transcript archives with bounded Python heap."""
    with _tmp() as (_, claude, codex, archive):
        src = claude / REPO_DIR / "huge.jsonl"
        src.parent.mkdir(parents=True)
        line = b'{"type":"synthetic","n":%08d,"pad":"' + b"y" * 180 + b'"}\n'
        with open(src, "wb") as fh:
            block = b"".join(line % i for i in range(4096))
            written = 0
            while written < 80 * 1024 * 1024:
                fh.write(block)
                written += len(block)
        os.utime(src, (OLD, OLD))
        tracemalloc.start()
        s = _run(run_archive, claude, codex, archive)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        dest = _gz(archive, f"claude/{REPO_DIR}/huge.jsonl")
        size_ok = s["copied"] == 1 and dest.exists()
        bound = 8 * 1024 * 1024
        return size_ok and peak < bound, (
            f"src={written / 2**20:.1f}MiB peak_traced={peak / 2**20:.2f}MiB "
            f"bound={bound / 2**20:.0f}MiB gz={dest.stat().st_size / 2**20:.2f}MiB")


CHECKS = [
    ("new file copied", check_new_file_copied),
    ("unchanged file skipped", check_unchanged_file_skipped),
    ("grown file re-copied", check_grown_file_recopied),
    ("same-size rewrite re-archived", check_same_size_rewrite_rearchived),
    ("shrunk file re-archived", check_shrunk_file_rearchived),
    ("recent file (<10min) skipped", check_recent_file_skipped),
    ("archive never deletes", check_archive_never_deletes),
    ("permissions are 0600/0700", check_permissions),
    ("manifest correct", check_manifest_correct),
    ("missing codex dir is fine", check_missing_codex_dir_is_fine),
    ("other projects (proj-b) NOT archived", check_other_projects_not_archived),
    ("worktree + vault project dirs included", check_worktree_and_vault_projects_included),
    ("codex scoped by metadata cwd", check_codex_scoped_by_metadata_cwd),
    ("codex without metadata skipped + counted", check_codex_no_metadata_skipped),
    ("gzip valid and round-trips", check_gzip_round_trips),
    ("unreadable file -> error, run continues", check_unreadable_file_is_error_run_continues),
    ("stale .partial-<pid> removed", check_stale_partials_removed),
    ("scope predicate units", check_predicate_units),
]
HEAVY_CHECKS = [("80MB peak memory bounded", check_peak_memory_80mb)]


def run_checks(run_archive=None, checks=CHECKS):
    passed = failed = 0
    for name, fn in checks:
        try:
            ok, detail = fn(run_archive)
        except Exception as exc:  # noqa: BLE001 - a crash is a failed check
            ok, detail = False, f"raised {type(exc).__name__}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name} ({detail})")
        passed += ok
        failed += not ok
    return passed, failed


# ── Mutants ──────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _patched(**attrs):
    saved = {k: getattr(sta, k) for k in attrs}
    for k, v in attrs.items():
        setattr(sta, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(sta, k, v)


def _delete_on_sync(**kwargs):
    summary = sta.run_archive(**kwargs)
    claude_archive = kwargs["archive_root"] / "claude"
    for existing in list(claude_archive.rglob("*.jsonl.gz")) if claude_archive.is_dir() else []:
        rel = existing.relative_to(claude_archive).as_posix()[: -len(".gz")]
        if not (kwargs["claude_projects_dir"] / rel).exists():
            existing.unlink()  # THE DEFECT: pruning the archive.
    return summary


MUTANTS: list[tuple[str, dict[str, Any], Any]] = [
    ("skip age check", {"QUIET_WINDOW_SECONDS": -1}, None),
    ("delete-on-sync", {}, _delete_on_sync),
    ("wrong perms", {"FILE_MODE": 0o644, "DIR_MODE": 0o755}, None),
    ("every claude project in scope", {"is_carr_claude_project": lambda *a, **k: True}, None),
    ("every codex session in scope", {"is_carr_cwd": lambda *a, **k: True}, None),
    ("codex metadata ignored", {"codex_session_cwd": lambda p: CHECKOUT}, None),
    ("size-only change detection",
     {"unchanged_since": lambda prior, st: prior.get("size") == st.st_size}, None),
    ("no stale-partial sweep", {"remove_stale_partials": lambda root: 0}, None),
    ("full path in errors", {"ref": lambda key: key, "_why": lambda exc: str(exc)}, None),
]


def run_mutants() -> bool:
    all_caught = True
    for name, attrs, runner in MUTANTS:
        with _patched(**attrs):
            _, failed = run_checks(run_archive=runner)
        caught = failed > 0
        print(f"=== mutant '{name}': {'CAUGHT' if caught else 'NOT CAUGHT (BAD)'} "
              f"({failed} check(s) failed) ===")
        all_caught = all_caught and caught
    return all_caught


def main() -> int:
    print("=== real implementation ===")
    passed, failed = run_checks(checks=CHECKS + HEAVY_CHECKS)
    print(f"real implementation: {passed} passed, {failed} failed")
    print()
    print("=== mutants (each must be caught) ===")
    mutants_ok = run_mutants()
    ok = failed == 0 and mutants_ok
    print()
    print("session-trace-archive-selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
