#!/usr/bin/env python3
"""ci-selftest.py — proves ops/ci.sh actually runs, refuses, and reports honestly.

WHY THIS EXISTS, and why it is embarrassing that it did not exist first. Every
other gate in this repo has a selftest. ops/ci.sh — the script that gates all the
others — shipped with none, and inside one afternoon it accumulated four defects
that were each found by RUNNING it rather than by testing it:

  1. ${STRICT:+--strict} expanded on STRICT=0, because :+ fires on any non-empty
     value and "0" is non-empty. --strict leaked into every local run.
  2. `declare -A` is bash 4; macOS ships bash 3.2.57, so the script died on line
     64 before running a single check.
  3. Under zsh, `for c in $CLASS_ORDER` does not word-split, so the loop ran ONCE
     with c set to the whole list, every check_$c was an unknown command, and the
     script still reached its success line and printed "CI passed — every class
     green". EIGHT CLASSES REPORTED GREEN, ZERO EXECUTED. Caught by a peer
     session, not by me.
  4. core.fileMode is false in this repo, so chmod +x never reached the index.
     ci.sh was 100644 in git, and CI died with exit 126 — found but not
     executable.

Case 3 is the one that matters most and the reason this file leads with it. A
promotion gate that reports green having run nothing is worse than no gate: it
converts "unverified" into "verified" silently, and every downstream claim
inherits the lie. Defects 1, 2 and 4 make the script fail loudly, which is
survivable. Defect 3 makes it succeed falsely.

The rule this file is the answer to (Joe, 2026-08-13, from Addy Osmani): write
the tests first, then the code that makes them pass. Applied late here, on
purpose, to the exact artifact that proved why.

    .venv/bin/python ops/ci-selftest.py     # exit 0 = all pass
"""

import atexit
import contextlib
import inspect
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import scrubbed_env  # noqa: E402

# WHY scrubbed_env AND NOT fixture_env. The Git read and scanner subprocess
# below must act on REPO ON PURPOSE — this file seeds a real defect into the
# real tree to prove CI catches it. What they must never do is inspect somewhere
# ELSE: GIT_DIR outranks cwd and every git hook exports it, and ops/githooks/
# pre-push runs ops/ci.sh which runs this file. scrubbed_env makes the scanner's
# internal `git ls-files` and our visibility check address this worktree. See
# ops/git_env.py. Loop #371.
CI = REPO / "ops" / "ci.sh"

# Assembled at runtime so this source file does not itself contain a
# credential-shaped literal. The scanner reads tracked files, and this IS one —
# a fixture that trips the gate it tests is a false positive forever.
SEED_DSN = "DATABASE_URL=" + "postgres://prod:" + "hunter2hunter2" + "@ep-x.neon.tech/carr"

RESULTS: list[tuple] = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


ANSI = re.compile(r"\x1b\[[0-9;]*m")


# ------------------------------------------------------ accepted machine state
# THE ONE ACCEPTED STATE in which the types class cannot be exercised at all.
# requirements.txt pins `mypy>=2.3; python_version >= "3.10"`, so on an older
# runtime mypy is not merely missing — it is deliberately not installable.
# Dell's Mac ships the Command Line Tools' Python 3.9.6 and has no package
# manager, which is a standing machine state, not a transient breakage.
#
# Named as an exact constant, and printed on the passing line, because a
# permanently chosen machine state must never read as a permanent failure and
# must never be normalised by habitually passing CARR_SKIP_CI on every push.
# The acceptance is deliberately narrow: on 3.10 or newer a missing mypy is a
# real defect, the seeded-error assertions below run unchanged, and
# test_mypy_pin_acceptance_is_narrow proves that boundary still bites.
MYPY_PIN_MIN_PYTHON = (3, 10)


def type_check_interpreter_version():
    """The Python bin/type-check.sh would actually use, not the one running us.

    That script prefers $REPO/.venv/bin/mypy and falls back to mypy on PATH, so
    the venv's interpreter is what decides whether mypy can exist at all.
    Reading our own sys.version_info would be wrong the moment the selftest and
    the venv differ, which is exactly the case on a machine whose venv was built
    from a different python than the one invoking this file.
    """
    venv_python = REPO / ".venv" / "bin" / "python"
    if venv_python.exists():
        probe = subprocess.run(
            [str(venv_python), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, text=True)
        parts = probe.stdout.split()
        if probe.returncode == 0 and len(parts) >= 2:
            return (int(parts[0]), int(parts[1]))
    return sys.version_info[:2]


def mypy_pin_excludes_this_machine():
    return type_check_interpreter_version() < MYPY_PIN_MIN_PYTHON


# ---------------------------------------------------------------- seed safety
# THIS FILE SEEDS REAL DAMAGE INTO THE LIVE WORKING TREE — that is the point of
# it, because a check is only proven by making it fail. The danger is what
# happens if the process does not reach its own cleanup line.
#
# It already did happen, on 2026-08-13 (loop #368). A seeded defect was left
# behind — the `state-as-of` verb missing from mcp-server/src/tools.js, 46
# deletions in one hunk — and the marker file that would have said so had
# already been removed. ops/ci.sh --only artifact then failed with "would REMOVE
# 1 verb(s): deployed 105, tree has 104", and because the pre-push hook runs the
# full ci.sh, that blocked EVERY session on this machine from pushing anything,
# for a reason unrelated to their own work. The failure names a verb deletion,
# so the natural first read is that a person deleted a verb on purpose.
#
# WHY try/finally IS NOT ENOUGH, which is what this file relied on before.
# `finally` runs on an exception and on a normal exit. It does NOT run on
# SIGKILL, on a machine losing power, or when a parent harness kills the process
# group on timeout — and a CI selftest is exactly the kind of long job something
# else kills. A harness that seeds real damage and relies on reaching its own
# cleanup line is one crash away from doing this again.
#
# WHAT REPLACES IT. The original bytes are written to a JOURNAL BEFORE anything
# is modified, and the journal is removed only after a successful restore. So
# the damage is never un-recorded: either the journal is absent (nothing is
# seeded) or it names every path and holds its original content. Recovery then
# needs no memory of what the run was doing.
#
#   * seeded_paths() restores on any ordinary exit path — normal, exception,
#     SIGINT, SIGTERM — via finally plus atexit plus signal handlers.
#   * On a kill that outruns all of those, the journal survives on disk, and the
#     NEXT run finds it, restores every path from it, and REFUSES to start.
#     Refusing matters: a run that silently repaired and continued would hide a
#     crash that has already cost this machine a full push outage once.
SEED_JOURNAL = REPO / "_ci_selftest_seed_journal.json"


def _restore_from_journal(journal_data):
    """Write every recorded path back to its original bytes. Returns the paths."""
    restored = []
    for rel, original in (journal_data.get("paths") or {}).items():
        target = REPO / rel
        if original is None:
            # Path did not exist before the seed: the seed created it, so the
            # restore is removal, not a write of the string "None".
            if target.exists():
                target.unlink()
                restored.append(rel)
            continue
        if not target.exists() or target.read_text() != original:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(original)
            restored.append(rel)
    return restored


def _recover_stale_journal():
    """Called at import. A journal on disk means a previous run was killed."""
    if not SEED_JOURNAL.exists():
        return
    try:
        data = json.loads(SEED_JOURNAL.read_text())
    except (OSError, ValueError) as exc:
        print(f"FATAL: {SEED_JOURNAL.name} exists but is unreadable ({exc}).\n"
              f"  A previous run seeded real damage into this tree and was killed before\n"
              f"  restoring it. The journal is the only record of what was changed, so it\n"
              f"  cannot be repaired automatically. Inspect the file and the tree by hand.",
              file=sys.stderr)
        sys.exit(1)
    restored = _restore_from_journal(data)
    SEED_JOURNAL.unlink(missing_ok=True)
    # THE SHAPE OF THIS OUTPUT IS LOAD-BEARING, not decoration. ops/ci.sh's
    # gates class runs each suite quietly and, on failure, prints the whole of
    # a short log or its last 80 lines. So a recovery and a genuinely broken
    # check reach the terminal looking identical, and telling them apart is the difference
    # between a thirty-second re-run and another evening like 2026-08-13. The
    # banner is repeated at the END as well as the start, because the tail is
    # what gets shown, and the last line is the ACTION rather than the diagnosis.
    bar = "=" * 68
    for line in (bar, "NOT A TEST FAILURE — a stale seed was recovered.", bar):
        print(line, file=sys.stderr)
    print("A previous run was killed while a seeded defect was live in the working",
          file=sys.stderr)
    print("tree. Every path it recorded has been restored from the journal:",
          file=sys.stderr)
    for rel in restored:
        print(f"    restored  {rel}", file=sys.stderr)
    if not restored:
        print("    (every recorded path was already correct — nothing to undo)",
              file=sys.stderr)
    print("\nRefusing to start is deliberate: a silent repair would hide a crash that",
          file=sys.stderr)
    print("blocked every push on this machine for hours on 2026-08-13.", file=sys.stderr)
    for line in (bar, "NOTHING IS BROKEN. RE-RUN THIS SUITE TO PROCEED.", bar):
        print(line, file=sys.stderr)
    # 75 is EX_TEMPFAIL — a transient condition the caller should retry, and
    # distinct from 1. ci.sh treats any nonzero as a failed class, which stays
    # correct because the push must still be blocked; the code simply carries
    # the distinction for any caller that wants "retry me" rather than "a check
    # is broken".
    sys.exit(75)


@contextlib.contextmanager
def seeded_paths(*rels):
    """Seed real damage into the named repo-relative paths, safely.

    Records each path's original bytes to the journal BEFORE yielding, and
    restores on every exit path this process can still control. A path that does
    not exist yet is recorded as None so its restore is a deletion.
    """
    originals = {}
    for rel in rels:
        p = REPO / rel
        originals[rel] = p.read_text() if p.exists() else None
    SEED_JOURNAL.write_text(json.dumps(
        {"pid": os.getpid(), "paths": originals}, indent=2))

    done = {"restored": False}

    def restore(*_args):
        if done["restored"]:
            return
        done["restored"] = True
        _restore_from_journal({"paths": originals})
        SEED_JOURNAL.unlink(missing_ok=True)

    prev = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            prev[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda s, f: (restore(), sys.exit(130)))
        except (ValueError, OSError):
            pass  # not on the main thread, or the platform refuses — finally still covers it
    atexit.register(restore)
    try:
        yield
    finally:
        restore()
        for sig, handler in prev.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


def run(args, env=None, shell_cmd=None, timeout=600):
    e = dict(os.environ)
    e.pop("CARR_CI_DATABASE_URL", None)
    if env:
        e.update(env)
    cmd = shell_cmd if shell_cmd else [str(CI), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=e, timeout=timeout)
    # Strip ANSI. ci.sh colours its verdicts, and an un-stripped regex looking
    # for "OK  artifact" never matches "\x1b[32mOK\x1b[0m    artifact" — which
    # made this file report the catastrophe case as failing when it was fine.
    return p.returncode, ANSI.sub("", (p.stdout or "") + (p.stderr or ""))


# ---------------------------------------------------------------- 1. the big one
def test_no_green_without_running():
    """The catastrophe case. Invoked through OTHER shells, ci.sh must never claim
    success while executing nothing. It re-execs itself under bash for exactly
    this reason; if that guard is ever removed, this fails."""
    for sh in ("zsh", "sh", "bash"):
        exe = shutil.which(sh)
        if not exe:
            continue
        rc, out = run([], shell_cmd=[exe, str(CI), "--only", "artifact"])
        claims_green = "CI passed" in out
        actually_ran = re.search(r"(OK|FAIL|SKIP)\s+artifact", out) is not None
        check(f"{sh}: never reports green without running a class",
              not (claims_green and not actually_ran),
              f"claimed green={claims_green} ran={actually_ran}")


def test_class_table_is_complete():
    """Every class in CLASS_ORDER needs a check_ function AND a description. A
    class listed but not implemented would silently never run — the same failure
    as case 3, arriving by a different door."""
    src = CI.read_text()
    order = re.search(r'^CLASS_ORDER="([^"]+)"', src, re.M)
    check("CLASS_ORDER is declared", order is not None)
    if not order:
        return
    classes = order.group(1).split()
    listed = set(re.findall(r"^\s+(\w+)\)\s+echo\s+\"", src, re.M))
    for c in classes:
        check(f"class '{c}' has a check_ function",
              re.search(rf"^check_{c}\(\)", src, re.M) is not None)
        check(f"class '{c}' has a description", c in listed)


# ---------------------------------------------------------------- 2. skip vs pass
def test_strict_turns_skip_into_failure():
    rc_loose, out_loose = run(["--only", "migration"])
    rc_strict, out_strict = run(["--strict", "--only", "migration"])
    check("a SKIP alone passes without --strict", rc_loose == 0 and "SKIP" in out_loose,
          f"rc={rc_loose}")
    check("the same SKIP fails under --strict", rc_strict == 1 and "SKIP" in out_strict,
          f"rc={rc_strict}")
    check("--strict failure explains why a skip counts",
          "stopped running" in out_strict.lower() or "skipped" in out_strict.lower())


def test_unknown_class_refuses():
    rc, out = run(["--only", "definitely-not-a-class"])
    check("an unknown class name exits 64, not 0", rc == 64, f"rc={rc}")


# ---------------------------------------------------------------- 3. safety
def test_migration_refuses_non_loopback():
    """The hard safety property. This check applies 130 forward migrations; if it
    ever accepts a remote DSN it would apply them to whatever it was pointed at.
    There is deliberately no override flag, so there is nothing to test around."""
    for dsn, label in [
        ("postgres://u:p@ep-steep-field.us-east-2.aws.neon.tech/carr", "a Neon host"),
        ("postgres://u:p@10.0.0.5:5432/carr", "a private IP"),
        ("postgres://u:p@db.internal:5432/carr", "an internal hostname"),
    ]:
        rc, out = run(["--only", "migration"], env={"CARR_CI_DATABASE_URL": dsn})
        check(f"migration refuses {label}", rc != 0 and "REFUSED" in out, f"rc={rc}")

    rc, out = run(["--only", "migration"],
                  env={"CARR_CI_DATABASE_URL": "postgres://u:p@localhost:5432/x"})
    check("migration ACCEPTS loopback (fails on no server, not on the guard)",
          "REFUSED" not in out)


# ---------------------------------------------------------------- 4. the exec bit
def test_tracked_scripts_are_executable_in_git():
    """core.fileMode is false in this repo, so chmod on disk never reaches the
    index. A script can be executable locally and 100644 in git, which is exactly
    how CI died with exit 126. Check the INDEX, not the filesystem — the
    filesystem is the thing that lies here."""
    out = subprocess.run(["git", "ls-files", "-s"], cwd=REPO, env=scrubbed_env(),
                         capture_output=True, text=True, check=True).stdout
    modes = {}
    for line in out.splitlines():
        mode, _, rest = line.partition(" ")
        modes[rest.split("\t", 1)[-1]] = mode

    # FAIL only on what is INVOKED AS A COMMAND. A shebang is not by itself a
    # requirement to be executable: bin/council-lib.sh is sourced and
    # fill-engine/fill_document.py is imported, and neither needs the bit. The
    # defect that killed CI was narrower than "has a shebang" — it was "something
    # runs this by path and the bit is missing".
    entry_points = [
        "ops/ci.sh", "ops/ci-selftest.py", "ops/ci-secret-scan.py",
        "ops/ci-dep-check.py", "ops/verb-count.sh", "ops/githooks/pre-push",
        "bin/deploy-worker.sh", "run.sh",
    ]
    missing = [p for p in entry_points
               if p in modes and modes[p] != "100755"]
    check("every CI entry point is executable in the index",
          not missing, f"non-executable: {', '.join(missing)}")

    # REPORT the wider pattern without failing on it. Silence here would hide a
    # real question (which of these are meant to be run directly?); failing would
    # assert an answer this test cannot actually determine.
    others = []
    for path, mode in modes.items():
        if mode != "100644" or path in entry_points:
            continue
        if any(p in path for p in ("node_modules", "vendor", ".venv", ".claude/worktrees")):
            continue
        try:
            with open(REPO / path, "rb") as fh:
                if fh.read(2) == b"#!":
                    others.append(path)
        except OSError:
            continue
    if others:
        print(f"        note: {len(others)} other tracked file(s) carry a shebang but are "
              f"not executable in the index. Not a failure — most are sourced or imported. "
              f"Worth a decision if any is meant to be run directly: {', '.join(sorted(others)[:5])}"
              f"{' …' if len(others) > 5 else ''}")


# ---------------------------------------------------------------- 5. the scanners
def test_secret_scanner_catches_and_respects_allow():
    scan = [sys.executable, str(REPO / "ops" / "ci-secret-scan.py")]
    rc, _ = subprocess.run(scan, cwd=REPO, env=scrubbed_env(), capture_output=True, text=True).returncode, None
    check("the tree is currently clean of shaped credentials", rc == 0, f"rc={rc}")

    # The scanner only visits `git ls-files`, so seed a file that is already
    # tracked instead of changing the index. This dedicated fixture has no
    # operational consumer; the journal records its original bytes before the
    # write and restores them on ordinary exit, signals, or stale-journal
    # recovery on the next independent run.
    seeded_rel = "ops/ci-secret-scan-fixture.txt"
    seeded = REPO / seeded_rel
    listed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", seeded_rel],
        cwd=REPO, env=scrubbed_env(), capture_output=True,
    )
    tracked = listed.returncode == 0
    check("the credential fixture path is tracked for the scan", tracked,
          f"git ls-files rc={listed.returncode}")
    with seeded_paths(seeded_rel):
        seeded.write_text(SEED_DSN + "\n")
        p = subprocess.run(scan, cwd=REPO, env=scrubbed_env(), capture_output=True, text=True)
        check("a seeded credential is caught", tracked and p.returncode == 1,
              "credential fixture was not tracked" if not tracked else f"rc={p.returncode}")
        check("the finding never prints the credential value",
              tracked and "hunter2" + "hunter2" not in (p.stdout + p.stderr))

        seeded.write_text(SEED_DSN + "  # ci-secret-scan" + ": allow — selftest fixture\n")
        p = subprocess.run(scan, cwd=REPO, env=scrubbed_env(), capture_output=True, text=True)
        check("an inline allow marker on the same line suppresses it",
              tracked and p.returncode == 0,
              "credential fixture was not tracked" if not tracked else f"rc={p.returncode}")


def test_dep_check_detects_a_stale_lock():
    req = REPO / "requirements.txt"
    original = req.read_text()
    dep = [sys.executable, str(REPO / "ops" / "ci-dep-check.py")]
    with seeded_paths("requirements.txt"):
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("dependency check passes on the committed tree", p.returncode == 0)

        req.write_text(original + "\nsome-package-that-is-not-locked>=1.0\n")
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("a requirements.txt edit makes the lock STALE",
              p.returncode == 1 and "STALE" in (p.stdout + p.stderr))

        req.write_text(original + "\n# a comment-only edit\n")
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("a comment-only edit does NOT invalidate the lock", p.returncode == 0)


def test_types_class_catches_a_seeded_type_error():
    """The `types` class must REFUSE a type error, not merely exist.

    The class landed in #60 and was proven to block BY HAND. No test was left
    behind, and this is the class that most needs one on its own evidence: #60's
    commit message records that its first attempt at that proof was a FALSE PASS.
    The seed was appended to tools/health-check.py, whose module body ends in
    sys.exit(rc); everything after a NoReturn call at module level is unreachable,
    mypy does not check unreachable code, and so the test measured dead code
    while reporting that the gate worked.

    That is exactly the failure this file leads with — a check reporting green
    having examined nothing — and a hand-run proof does not stop it returning on
    the next edit. Three PRs (#60, #65, #67) rewrote this class inside one hour,
    one of them silently dropping the exit-78 branch, which is the rate of change
    a permanent test is for.

    MODULE LEVEL IS LOAD-BEARING: mypy.ini sets check_untyped_defs = False, so an
    error inside an unannotated function body is not reported at all and this
    test would pass for the wrong reason a second time.
    """
    if mypy_pin_excludes_this_machine():
        found = type_check_interpreter_version()
        check(
            "types: ACCEPTED — Python %d.%d is below the mypy pin %d.%d, so mypy "
            "cannot be installed here and the seeded-error path cannot run"
            % (found[0], found[1], MYPY_PIN_MIN_PYTHON[0], MYPY_PIN_MIN_PYTHON[1]),
            True)
        rc, out = run(["--only", "types"])
        check("types still reports honestly on the accepted machine (skip, not pass)",
              rc == 0 and "SKIP" in out.upper(), f"rc={rc} out={out[-400:]}")
        return

    fixture = "tools/_ci_selftest_types_fixture.py"
    with seeded_paths(fixture):
        rc, out = run(["--only", "types"])
        check("types passes on the committed tree", rc == 0,
              f"rc={rc} out={out[-400:]}")

        (REPO / fixture).write_text(
            "# fixture written by ops/ci-selftest.py — removed on exit.\n"
            "# Module level on purpose: check_untyped_defs = False means an error\n"
            "# inside an unannotated function body would not be reported at all.\n"
            "x: int = 'not an int'\n")
        rc, out = run(["--only", "types"])
        check("a seeded type error makes the types class FAIL", rc == 1,
              f"rc={rc} out={out[-400:]}")
        check("the failing run names the types class",
              "types" in ANSI.sub("", out).lower(), f"out={out[-400:]}")

    rc, out = run(["--only", "types"])
    check("removing the seed turns the types class green again", rc == 0,
          f"rc={rc} out={out[-400:]}")


def test_type_check_script_resolves_mypy_in_both_homes():
    """bin/type-check.sh runs in two environments and must not fork.

    Joe's Mac has a .venv; the GitHub runner pip-installs requirements.lock into
    the system python and has none. The script prefers the venv and falls back to
    PATH.

    THE EXIT-78 BRANCH IS PINNED HERE BECAUSE IT WAS ALREADY LOST ONCE. Absent
    mypy exits 78 (EX_CONFIG), which ci.sh must read as SKIP — a skip that
    --strict then refuses in CI, so the check cannot go quietly missing while
    a machine without mypy is not told it has type errors. Two sessions built
    this class in parallel on 2026-08-14 and the one WITHOUT that branch merged
    second (#65 over #60), so a machine with no mypy read "mypy found shape
    mistakes": a false failure carrying a false explanation. #67 restored it.
    Nothing but a test stops the third rewrite dropping it again.
    """
    src = (REPO / "bin" / "type-check.sh").read_text()
    ci = CI.read_text()
    check("type-check.sh falls back to mypy on PATH", "command -v mypy" in src)
    check("absent mypy exits 78 (EX_CONFIG), not 0", "exit 78" in src)
    check("ci.sh still reads 78 as a SKIP rather than a type failure",
          re.search(r'-eq 78 \]', ci) is not None and
          re.search(r'skip types', ci) is not None)
    check("mypy is pinned in the lockfile so the runner has it",
          "mypy==" in (REPO / "requirements.lock").read_text())
    check("ci.sh's types class calls the script rather than mypy directly",
          "bin/type-check.sh" in ci)


def test_lock_is_not_platform_specific():
    """pip freeze drops environment markers, which made the lock Mac-only and
    killed the first CI run on pyobjc. Anything darwin-only must carry its
    marker."""
    lock = (REPO / "requirements.lock").read_text()
    bad = [l for l in lock.splitlines()
           if l.strip() and not l.startswith("#")
           and l.split("==")[0].lower().startswith("pyobjc")
           and "sys_platform" not in l]
    check("darwin-only packages in the lock carry a sys_platform marker",
          not bad, f"bare: {bad[:3]}")


def test_migration_filenames_match_the_runner():
    """Every migration must satisfy tools/migrate.py's own NAME_RE, and the
    ordered-insert file must sort where it claims to. CI rejected 0013a with
    'bad migration filename' AFTER it was written and pushed — a filename
    contract that is only enforced on a live database is one you find out about
    from a red runner rather than from a check."""
    src = (REPO / "tools" / "migrate.py").read_text()
    m = re.search(r'NAME_RE = re\.compile\(r"([^"]+)"\)', src)
    check("migrate.py's NAME_RE is readable from source", m is not None)
    if not m:
        return
    rx = re.compile(m.group(1))
    names = sorted(p.name for p in (REPO / "migrations").iterdir()
                   if p.suffix == ".sql")
    bad = [n for n in names if not rx.match(n)]
    check("every migration filename matches the runner's contract",
          not bad, f"rejected: {', '.join(bad[:4])}")

    # Ordering is the whole point of an inserted migration; assert it rather
    # than trusting the ASCII reasoning in the file's own header comment.
    inserted = [n for n in names if re.match(r"^\d{4}[a-z]_", n)]
    for n in inserted:
        stem = n[:4]
        nxt = f"{int(stem) + 1:04d}"
        before = [x for x in names if x.startswith(stem + "_")]
        after = [x for x in names if x.startswith(nxt + "_")]
        if before and after:
            i, b, a = names.index(n), names.index(before[0]), names.index(after[0])
            check(f"{n} sorts between {before[0]} and {after[0]}", b < i < a)


def test_known_gaps_all_expire():
    """A known gap suppresses a red class's exit code while a ruling is pending.
    That is only safe because it expires. An entry with no expiry, or one dated
    so far out it never bites, is a permanent exemption wearing a temporary
    label — which is the thing this mechanism must not become."""
    import datetime, json as _json
    scope = REPO / "ops" / "config" / "ci-check-scope.json"
    if not scope.exists():
        return
    gaps = _json.loads(scope.read_text()).get("known_gaps", [])
    today = datetime.date.today()
    for g in gaps:
        name = g.get("class", "?")
        exp = g.get("expires")
        check(f"known gap '{name}' has an expiry date", bool(exp))
        if not exp:
            continue
        try:
            d = datetime.date.fromisoformat(exp)
        except ValueError:
            check(f"known gap '{name}' expiry parses as a date", False, exp)
            continue
        check(f"known gap '{name}' expires within 30 days",
              d <= today + datetime.timedelta(days=30),
              f"{exp} is {(d - today).days} days out")
        check(f"known gap '{name}' names the loop carrying the ruling",
              bool(g.get("loop")))
    if not gaps:
        check("no known gaps outstanding (nothing suppressed)", True)


def test_no_env_claims_a_production_hostname():
    """The 2026-08-13 incident, pinned. A staging deploy took over
    api.doctorcre.com, api.practicecre.com and dealroom.doctorcre.com because
    wrangler inherits `routes` and [env.staging] did not override it. Production
    answered from the empty staging database for about two minutes."""
    import subprocess as sp
    toml = REPO / "mcp-server" / "wrangler.toml"
    checker = REPO / "ops" / "deploy-attachment-check.py"
    if not toml.exists() or not checker.exists():
        return
    src = toml.read_text()
    envs = re.findall(r"^\[env\.([A-Za-z0-9_-]+)\]", src, re.M)
    for env in sorted(set(envs)):
        r = sp.run([sys.executable, str(checker), str(toml), env],
                   capture_output=True, text=True)
        check(f"env '{env}' claims no production hostname", r.returncode == 0,
              (r.stdout + r.stderr).strip()[:120])

    # And the guard must still REFUSE the exact config that caused the incident.
    import tempfile as tf
    broken = "\n".join(l for l in src.splitlines() if l.strip() != "routes = []")
    with tf.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
        fh.write(broken)
        path = fh.name
    try:
        r = sp.run([sys.executable, str(checker), path, "staging"],
                   capture_output=True, text=True)
        check("removing `routes = []` is REFUSED, not silently allowed",
              r.returncode == 1, f"rc={r.returncode}")
    finally:
        os.unlink(path)


def test_mypy_pin_acceptance_is_narrow():
    """The accepted state must not quietly widen into "mypy is optional".

    Rule bd4a6d22 requires that everything outside a named acceptance still
    fails, and that a test proves it. The danger here is drift: someone raises
    MYPY_PIN_MIN_PYTHON to silence a red types class on a NEWER machine, and the
    type gate stops binding everywhere at once with nothing to catch it.
    """
    check("the mypy pin boundary is exactly 3.10",
          MYPY_PIN_MIN_PYTHON == (3, 10), f"got {MYPY_PIN_MIN_PYTHON}")
    for ver in ((3, 7), (3, 8), (3, 9)):
        check(f"Python {ver[0]}.{ver[1]} is inside the acceptance",
              ver < MYPY_PIN_MIN_PYTHON, f"{ver}")
    for ver in ((3, 10), (3, 11), (3, 12), (3, 13), (4, 0)):
        check(f"Python {ver[0]}.{ver[1]} is OUTSIDE it, so a missing mypy still fails",
              not (ver < MYPY_PIN_MIN_PYTHON), f"{ver}")
    pin = (REPO / "requirements.txt").read_text(encoding="utf-8")
    check("requirements.txt still carries the pin this acceptance is derived from",
          'python_version >= "3.10"' in pin,
          "the constant and the pin must move together, or the acceptance is a guess")


# --------------------------------------------- test files outside the reach
#
# THE SKIP LIST IS PART OF THE CHECK, not a config file somewhere else. A
# test-shaped file has exactly two honest states: collected by a loop in
# ci.sh, or excused here in writing. The third state — uncollected and
# unexplained — is what produced this check and then survived inside it, and
# it is indistinguishable from coverage by every means except a stopwatch.
# Keeping the excuse beside the assertion means a reviewer reads the reason
# in the same glance as the thing it excuses.
#
# EVERY ENTRY IS A DECISION SOMEONE MADE ON PURPOSE. Four assertions below
# stop this from decaying into a blanket suppressor: an entry naming a file
# that no longer exists fails, an entry naming a file that IS collected fails,
# an entry with a thin reason fails, and the reach assertion itself fails if
# the walk stops going deep.
UNCOLLECTED_BY_DECISION = {
    "tools/room-bridge/test_claude_desk_live.py":
        "LIVE, and deliberately not offline. Its own docstring says it asserts "
        "against no mock: it boots a REAL Claude Code session on a labelled "
        "socket and dispatches a task into it. That spends model quota and "
        "needs a working desk on the machine, so a merge gate is the wrong "
        "caller — a hosted runner would either hang or pass for the wrong "
        "reason. Run it by hand when the dispatch path changes.",
    "tools/room-bridge/test_codex_live_live.py":
        "LIVE, and it costs money. It boots a real Codex app-server and drives "
        "two dispatches through it; the docstring states it 'costs a small "
        "amount of Codex credit'. Measured here 2026-09-10: run offline with "
        "no server reachable it does not fail, it HANGS, and it was still "
        "hanging when the 13 other room-bridge suites had finished. A suite "
        "that hangs in a pooled gate burns the per-suite timeout and aborts "
        "the remaining gate selftests behind it. Run it by hand.",
    "docs/frontier-finding/breakglass_selftest.py":
        "Needs a disposable local PostgreSQL cluster, which it stands up "
        "itself (WR-000046 Artifact C harness). The gates class is repository "
        "content only — no machine state, no database — and local initdb is "
        "unavailable on Joe's Mac ('shmget: Operation not permitted'). Its "
        "database coverage belongs to the migration class, not this one.",
    "pipelines/doctrine_load_test.py":
        "Not a unit suite: it is the P6 preflight LOAD BAR. It requires "
        "DATABASE_URL for the runtime reader role against the production "
        "store, drives 20 concurrent sessions, and runs for 15 minutes by "
        "default. Network, database and wall-clock all disqualify it from a "
        "merge gate; it is run deliberately before a release.",
    "tools/dictation-rig/tests/test_call_mode.py":
        "Offline and correct, but not evaluated by this unit — it is a "
        "unittest package under tools/dictation-rig/tests/ that this lane did "
        "not run or vouch for. Named here so it is a known gap with an owner "
        "rather than an invisible one; collecting it is a follow-up that must "
        "run it first. Same status as tools/partner-line/tests/test_watch.py.",
    "tools/partner-line/tests/test_watch.py":
        "Offline and correct, but not evaluated by this unit — see the "
        "tools/dictation-rig entry. Its docstring claims no live socket and no "
        "network, so it is a good candidate to collect; this lane owned "
        "ops/ci.sh and ops/ci-selftest.py only and did not run it.",
    "tools/doc-convo/bin/test-brain-stream.sh":
        "Not evaluated by this unit. It lives in a bin/ directory beside the "
        "convo server rather than in a tests/ directory, and the three "
        "doc-convo shell tests appear to drive a running server. Named here as "
        "a known gap; collecting them owes a run first.",
    "tools/doc-convo/bin/test-convo-server.sh":
        "Not evaluated by this unit — see the test-brain-stream.sh entry.",
    "tools/doc-convo/bin/test-streaming.sh":
        "Not evaluated by this unit — see the test-brain-stream.sh entry.",
}

# What any reader of this tree would call a test file, in every naming style
# the repo actually uses: the hyphen form the script convention produces, the
# underscore form pytest's default discovery produces, the gate form, and the
# _test.py suffix form. Over-inclusive on purpose — a name this matches that
# is not a test costs one line on the list above, and that line is cheaper
# than the silence it replaces.
TEST_FILE_NAME = re.compile(r"""
    ^(?:
        test[-_].*\.(?:py|sh)        # test-foo.py, test_foo.py, test-foo.sh
      | .*[-_]selftest\.(?:py|sh)    # foo-selftest.py, breakglass_selftest.py
      | .*_test\.py                  # foo_test.py
    )$""", re.VERBOSE)

# Not source. Pruned BY NAME and by name only: out/ is gitignored scratch every
# class writes to, .claude/ holds sibling worktrees whose files are not this
# tree's, and the rest are installed, cached or generated.
#
# THE NAMES ARE THE WHOLE LIST, 2026-09-11. The walk also pruned every directory
# whose name began with a dot, which is a category and not a name, and a
# category prunes things nobody decided to prune. A tracked .github/test_foo.py
# would have been neither collected by a loop in ci.sh nor excused in writing
# below — the exact third state this check exists to make impossible, reappearing
# inside the check itself. Adding a dot-directory to this set is a decision
# someone makes once and a reader can see; matching the shape of a name is not.
# .claude/ is therefore listed explicitly rather than caught by its dot, and
# test_the_walk_prunes_named_roots_only holds the distinction from both sides.
UNWALKED_DIRS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "out", "_inputs",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".git", ".claude",
})


def _test_shaped_files(tree=None):
    """Every test-shaped file in the tree, at any depth. The depth is the point.

    os.walk does not follow symlinks, which is deliberate here: out/ is a
    symlink to the canonical checkout's out/ in every worktree, and following
    it would walk another tree's files into this assertion.

    `tree` exists so the pruning rule above can be driven against a fixture
    directory rather than only against this repository. A walk asserted only
    over the real tree can only be checked against what happens to be in it
    today, which is how the dot-directory hole below survived review.
    """
    tree = pathlib.Path(tree) if tree is not None else REPO
    found = set()
    for root, dirs, files in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d not in UNWALKED_DIRS)
        for name in files:
            if TEST_FILE_NAME.match(name):
                found.add(pathlib.Path(root, name)
                          .relative_to(tree).as_posix())
    return found


def test_every_test_file_in_the_tree_is_collected():
    """A test the collector's glob does not match is not a passing test — it is
    no test at all, and it sits in the tree looking exactly like coverage.

    ops/ci.sh collects with shell globs, and those globs have been narrower than
    the tree three times now. Shell tests under tools/ were "collected by nobody
    and executed by nothing" until a second loop was added for them. Then three
    underscore-named Python tests — test_validate_exact_recovery_source,
    test_staging_recovery_rehearsal and test_displacement_turn_filter — never
    executed once between being committed and 2026-08-27, because the Python
    loop globbed only tools/test-*.py.

    THE THIRD TIME WAS THIS CHECK ITSELF, found 2026-09-10. It asserted the
    invariant against a tree it enumerated with `(REPO/"tools").glob(...)` and
    `(REPO/"ops").glob(...)` — two directory globs, ONE LEVEL DEEP, the very
    shape of the defect it existed to catch. tools/room-bridge/ held fifteen
    test files and dealroom/test four; every one of them was matched by no loop
    in ci.sh, and this assertion could not see a single one, because a file one
    directory deeper was not in the set it compared. It passed, every run,
    reporting an invariant it was not measuring. A checker that reports green
    having examined nothing is the same defect one level up, and this repository
    has been bitten by that exact shape before.

    So the tree side is now a real walk to any depth, and the depth is asserted
    below rather than assumed — a walk that silently flattens back to one level
    fails here instead of going quiet. The ci.sh side is still derived from
    ci.sh's source rather than restated here, because a copy of the globs would
    be a second contract to keep in sync, which is the same failure again."""
    ci = (REPO / "ops" / "ci.sh").read_text()
    patterns: list[str] = []
    for m in re.finditer(r"for t in ([^;]+); do", ci):
        # Shell tokens only. A loop over "$eligible" or a line continuation
        # contributes nothing to expand, and must not reach Path.glob.
        patterns += [tok for tok in m.group(1).split()
                     if re.fullmatch(r"[A-Za-z0-9_./*?\[\]-]+", tok)]
    check("ci.sh's selftest collection globs are readable from source",
          len(patterns) >= 2, f"found: {patterns}")
    if not patterns:
        return

    collected = set()
    for pat in patterns:
        collected |= {p.relative_to(REPO).as_posix() for p in REPO.glob(pat)}

    on_disk = _test_shaped_files()
    check("the tree still contains test files to collect", on_disk,
          "an empty set would make the assertion below vacuously true")

    # THE MUTATION GUARD ON THIS CHECK. The bug being fixed was an enumeration
    # that stopped at one directory level while claiming to describe the tree.
    # Nothing about a passing run distinguishes that from a correct one unless
    # the depth is asserted, so it is asserted: the tree really does hold test
    # files three and four directories down, and a walk that cannot see them is
    # broken no matter how green the line above reads.
    depths = {name.count("/") + 1 for name in on_disk}
    check("the walk reaches test files nested below the top two levels",
          max(depths, default=0) >= 3,
          f"deepest test file found is {max(depths, default=0)} levels — "
          f"tools/room-bridge/ (3) and tools/*/tests/ (4) exist, so a maximum "
          f"of 2 means this enumeration flattened and is measuring nothing")

    stale = sorted(p for p in UNCOLLECTED_BY_DECISION if p not in on_disk)
    check("every skip-list entry names a file that exists", not stale,
          f"gone or renamed, so the entry protects nothing: {', '.join(stale)}")

    contradicted = sorted(p for p in UNCOLLECTED_BY_DECISION if p in collected)
    check("no skip-list entry excuses a file ci.sh already collects",
          not contradicted,
          f"collected AND excused, so the reason is fiction: "
          f"{', '.join(contradicted)}")

    thin = sorted(p for p, why in UNCOLLECTED_BY_DECISION.items()
                  if len(why.strip()) < 60)
    check("every skip-list entry states a real reason", not thin,
          f"a reason too short to be one: {', '.join(thin)}")

    missed = sorted(on_disk - collected - set(UNCOLLECTED_BY_DECISION))
    check("every test file in the tree is collected by a loop in ci.sh "
          "or excused by name in UNCOLLECTED_BY_DECISION",
          not missed,
          f"never executed and never excused: {', '.join(missed)}")
    print(f"        reach: {len(on_disk)} test files in the tree, "
          f"{len(on_disk & collected)} collected by ci.sh, "
          f"{len(UNCOLLECTED_BY_DECISION)} excused by name")


def test_the_walk_prunes_named_roots_only():
    """A tracked test under a dot-directory must be REACHED, not silently dropped.

    THE HOLE THIS CLOSES. The walk above used to prune `d.startswith(".")` as
    well as the named set, and a category is not a name. Nothing in this tree
    decided that .github/ holds no tests; the prune simply swallowed every
    directory whose name began with a dot. A tracked .github/test_foo.py would
    then have been in none of the three states this whole section is built on —
    not collected by a loop in ci.sh, not excused in UNCOLLECTED_BY_DECISION,
    and not named as missing — which is the third state, uncollected and
    unexplained, reappearing inside the check written to make it impossible.

    DRIVEN AGAINST A FIXTURE TREE, NOT THIS ONE. The repository holds no
    dot-directory test file today, so the real tree cannot tell a correct prune
    from a blanket one: both give the same answer here and now. The fixture is a
    real `git init` with the file really added to the index, so the case is
    literally the review's shape — a TRACKED test under a dot-directory — and
    the .git/ the prune must still skip is a real .git/ rather than a prop.

    BOTH SIDES, because a walk that pruned nothing would also pass the half
    above: every named root is planted with a test-shaped file too, and each one
    must stay out of the result.
    """
    with tempfile.TemporaryDirectory(prefix="ci-selftest-walk-") as td:
        tree = pathlib.Path(td)
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True,
                       capture_output=True, env=scrubbed_env())

        reachable = ["tools/test_control.py", ".github/test_planted_dot_dir.py",
                     ".github/workflows/test_nested_dot_dir.py"]
        pruned = [f"{d}/test_planted.py" for d in sorted(UNWALKED_DIRS)]
        for rel in reachable + pruned:
            path = tree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")
        # TRACKED, not merely present: `git add` on the dot-directory file is
        # what makes this the case the review named. -f because a repo-level
        # ignore rule must not be what decides the outcome either.
        subprocess.run(["git", "add", "-f", *reachable], cwd=tree, check=True,
                       capture_output=True, env=scrubbed_env())
        tracked = subprocess.run(["git", "ls-files"], cwd=tree, check=True,
                                 capture_output=True, text=True,
                                 env=scrubbed_env()).stdout.split()
        check("the fixture's dot-directory test really is tracked",
              ".github/test_planted_dot_dir.py" in tracked, f"{tracked}")

        found = _test_shaped_files(tree)

    for rel in reachable:
        check(f"the walk reaches {rel}", rel in found, f"found: {sorted(found)}")
    still_pruned = sorted(rel for rel in pruned if rel in found)
    check("every name in UNWALKED_DIRS is still pruned",
          not still_pruned, f"walked into: {', '.join(still_pruned)}")
    check("the prune set is named roots, with no shape rule behind it",
          "startswith" not in inspect.getsource(_test_shaped_files),
          "a category prune is back; a dot-directory test would go silent again")


# ------------------------------------- 5b. what a failing gate is allowed to print
#
# fail_tail() prints a CHILD PROCESS'S captured output into the CI log. The
# window was widened on 2026-09-11 from twelve lines to the whole log under 200
# lines, because the failing line of a 32-check suite sat above a twelve-line
# tail and two hosted rounds were spent unable to read it. Widening the window
# widened the exposure with it: arbitrary child stdout, into a log that outlives
# the run and that more people can read than can read the tree.
#
# So both halves are asserted here — the window, which is the feature, and the
# redaction, which is what the feature costs if it is missing. The redaction is
# ops/ci-secret-scan.py's own --redact filter over its own PATTERNS list; these
# cases prove the wiring, and the scanner's pattern list stays the one place a
# shape is declared.

# A real github-token shape, present in this file ON PURPOSE so the assertions
# below are about a string the scanner genuinely matches rather than a stand-in
# that only looks like one. It is inert: 36 characters counting up.
FIXTURE_TOKEN = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"  # ci-secret-scan: allow — inert redaction fixture


def _fail_tail(log_path, py=None):
    """Drive ci.sh's REAL fail_tail(), lifted from its source.

    Lifted, not restated, for the reason this file keeps rediscovering: a copy
    of the body here would be a second contract, and a second contract drifts.
    The function needs only $PY and a log path, so bash can run the shipped
    bytes with cwd=REPO — which is what makes `ops/ci-secret-scan.py` resolve.
    """
    src = CI.read_text(encoding="utf-8")
    start = src.index("fail_tail() {")
    fn = src[start:src.index("\n# ------", start)]
    p = subprocess.run(
        ["bash", "-c", f'PY={shlex.quote(py or sys.executable)}\n{fn}\nfail_tail '
                       f'{shlex.quote(str(log_path))}'],
        cwd=str(REPO), capture_output=True, text=True, timeout=120)
    return ANSI.sub("", (p.stdout or "") + (p.stderr or ""))


def test_a_failing_gates_output_is_printed_whole_when_it_is_short():
    """The short branch: under 200 lines, the reader gets the whole log.

    This is the half the widening bought. A suite that prints one ok line per
    check and fails in the middle is unreadable through a tail, and that is not
    a hypothetical: it cost two hosted CI rounds on this very branch.
    """
    with tempfile.TemporaryDirectory(prefix="ci-selftest-failtail-") as td:
        log = pathlib.Path(td) / "gate-short.log"
        lines = [f"ok check {i}" for i in range(1, 31)]
        lines[4] = f"FAIL check 5: token={FIXTURE_TOKEN} leaked into the log"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = _fail_tail(log)

    printed = [ln for ln in out.splitlines() if ln.strip()]
    check("a short log is printed whole, not tailed",
          len(printed) == 30, f"{len(printed)} lines: {printed[:3]}")
    check("the first line of a short log reaches the reader",
          "ok check 1" in out, out[:400])
    check("and so does the last", "ok check 30" in out, out[-400:])
    check("the failing line is inside the window",
          "FAIL check 5" in out, out[:400])
    check("a token-shaped string in a short log is MASKED",
          FIXTURE_TOKEN not in out, "the raw credential reached the CI log")
    check("and the mask says what was removed, so the line stays diagnosable",
          "<redacted:github-token:40 chars>" in out, out[:400])


def test_a_long_failing_gate_log_is_tailed_and_still_redacted():
    """The long branch: 80 lines off the end, and the same masking.

    Both properties are asserted against the SAME log, because they can fail
    independently: a tail that redacts nothing publishes the credential, and a
    redactor wired only into the short branch looks correct on every test that
    never gets past 200 lines.
    """
    with tempfile.TemporaryDirectory(prefix="ci-selftest-failtail-") as td:
        log = pathlib.Path(td) / "gate-long.log"
        lines = [f"ok check {i}" for i in range(1, 251)]
        lines[2] = f"early line 3: token={FIXTURE_TOKEN} above the window"
        lines[244] = f"FAIL check 245: token={FIXTURE_TOKEN} inside the window"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = _fail_tail(log)

    printed = [ln for ln in out.splitlines() if ln.strip()]
    check("a long log is tailed to 80 lines", len(printed) == 80,
          f"{len(printed)} lines")
    check("the tail starts where 80 lines from the end starts",
          "ok check 171" in out, out[:300])
    check("and does not reach back past it",
          "ok check 170" not in out, out[:300])
    check("the failing line inside the window is printed",
          "FAIL check 245" in out, out[-400:])
    check("a token-shaped string inside the tail is MASKED",
          FIXTURE_TOKEN not in out, "the raw credential reached the CI log")
    check("the mask names the shape and the length",
          "<redacted:github-token:40 chars>" in out, out[-400:])
    check("the line above the window is not printed at all",
          "early line 3" not in out, out[:300])


def test_fail_tail_withholds_the_window_when_it_cannot_redact():
    """FAIL-CLOSED. No redactor, no print — and the log path instead.

    The tempting failure direction is the other one: print raw when the filter
    is unavailable, on the grounds that a diagnosis matters more. That reasoning
    publishes a credential to avoid an inconvenience, and it is the shape a
    reviewer cannot see in a green run. Driven by pointing $PY at an interpreter
    that does not exist, which is the same condition as a broken or deleted
    scanner from this function's side.
    """
    with tempfile.TemporaryDirectory(prefix="ci-selftest-failtail-") as td:
        log = pathlib.Path(td) / "gate-noredactor.log"
        log.write_text(f"FAIL: token={FIXTURE_TOKEN}\nsecond line\n", encoding="utf-8")
        out = _fail_tail(log, py=str(pathlib.Path(td) / "no-such-interpreter"))

    check("no part of the log is printed when redaction is unavailable",
          "second line" not in out, out)
    check("and least of all the credential", FIXTURE_TOKEN not in out, out)
    check("the reader is told the window was withheld",
          "WITHHELD" in out, out)
    check("and where the captured log actually is",
          str(log) in out, out)


def test_gates_treats_only_78_as_not_configured():
    """Exit 78 in the gates loop must mean "not configured", and nothing else.

    The loop used to count every nonzero alike, so a selftest that correctly
    declined for want of a local dependency read as a red gate, and the only way
    past it was CARR_SKIP_CI on every push. The risk in the fix is that it
    widens: if an ordinary crash ever
    skipped too, this class would go quiet exactly when it should shout.

    THIS TEST IS STRUCTURAL, AND THAT IS A DELIBERATE DOWNGRADE — say so rather
    than pretend otherwise. The behavioural version (seed a fixture that exits 78
    beside one that exits 1, run the gates class, assert only the second is
    named) cannot work here for two independent reasons, both measured
    2026-08-19: the gates loop globs ops/*-selftest.py, so it re-enters THIS file
    recursively and the nested run's crash-safety restores the outer run's seeded
    paths — the fixtures are deleted before the loop reaches them; and even if
    they survived, the nested re-entry runs the slowest class in ci.sh a second
    and third time, costing more on every push forever than the bug it guards.

    This structural check binds the narrowness of the exception without adding
    a recursively executing fixture to the slowest CI class.
    """
    ci = (REPO / "ops" / "ci.sh").read_text(encoding="utf-8")
    body = ci[ci.index("check_gates()"):]
    body = body[:body.index("\n}")]

    check("the gates loop skips on exactly 78, not a range",
          '[ "$grc" -eq 78 ]' in body,
          "an -ge/-ne form here would swallow real failures")
    check("every other nonzero still routes to the failure list",
          '[ "$grc" -ne 0 ]' in body and 'failures="$failures $base"' in body,
          "the else-branch must still record failures")
    check("the 78 skip announces itself rather than passing silently",
          "NOT CONFIGURED (exit 78)" in body,
          "a silent skip is how coverage disappears without anyone noticing")
    for bad in ('-ge 78', '-ne 0 ] && continue', '|| true'):
        check(f"the gates loop does not weaken with {bad!r}", bad not in body, bad)


def test_gates_selftests_have_a_process_group_watchdog():
    """A disposable hanging child must become a visible, non-green gate failure.

    This drives the existing process-group helper directly rather than sleeping
    through the 120-second production budget. The source assertions bind the
    second half: exit 124 is named, the gate log is tailed, and the selftest
    loop stops instead of continuing toward a false green verdict.
    """
    helper = REPO / "bin" / "with-timeout.py"
    with tempfile.TemporaryDirectory() as td:
        log = pathlib.Path(td) / "gate.log"
        p = subprocess.run(
            [sys.executable, str(helper), "0.2", sys.executable, "-c",
             "import time; print('fixture-output', flush=True); time.sleep(2)"],
            capture_output=True, text=True, timeout=10)
        log.write_text(p.stdout + p.stderr)
        text = log.read_text()
        check("a hanging gate fixture exits 124", p.returncode == 124,
              f"rc={p.returncode}")
        check("the timeout is named in the captured gate log",
              "with-timeout: TIMEOUT" in text and "fixture-output" in text,
              text[-300:])

    ci = CI.read_text(encoding="utf-8")
    body = ci[ci.index("check_gates()"):]
    check("gate selftests use the existing timeout helper",
          "CI_TIMEOUT_HELPER" in body and "CI_SELFTEST_TIMEOUT_SECONDS" in body)
    check("exit 124 is classified as a named timeout",
          "TIMEOUT:$base" in body and "TIMEOUT:$sbase" in body)
    check("a timed-out gate aborts the remaining selftest loop",
          "gates_timed_out=1" in body and "break" in body)

    for code in (0, 7):
        p = subprocess.run(
            [sys.executable, str(helper), "10", sys.executable, "-c",
             f"raise SystemExit({code})"],
            capture_output=True, text=True, timeout=10)
        check(f"ordinary exit {code} remains unchanged", p.returncode == code,
              f"rc={p.returncode}")


# ------------------------------------------------- 6. the push floor stays a floor
# A gate path that exists only for this fixture, so the case never depends on the
# pairing state of a real gate: nobody can add ops/<this>-selftest.py and quietly
# turn the assertions vacuous.
FIXTURE_GATE = "hooks/zz-ci-selftest-fixture-gate.py"
# Not a real revision. The stub git below answers only for this exact token, and
# real git never sees it, so the fixture needs no history, no commit and no index.
FIXTURE_RANGE = "CI-SELFTEST-FLOOR-FIXTURE-RANGE"
# A floor that has kept its shape returns in about a second. This is a guard on a
# hung run, not the regression detector — that job belongs to _push_floor_body().
FLOOR_BUDGET_SECONDS = 60


def _ci_function_body(name, until):
    """One shell function's source out of ops/ci.sh, anchored on its DEFINITION.

    The anchors are "\\n<name>() {" and not the bare name, and that is not
    fussiness -- it is a defect this file walked into on 2026-09-10. The slice
    used to start at the first occurrence of the string "check_pushfloor()"
    anywhere in ci.sh, so a COMMENT added above that mentioned the function by
    name moved the window over the wrong region of the file. Two cases then
    asserted about text they were never pointed at, and the one that failed said
    "check_pushfloor calls check_gates" -- a sentence that sends the reader to
    look for a call that is not there. That is the same shape as the message this
    correction round was spent on: a check reporting confidently about something
    it was not actually measuring.
    """
    src = CI.read_text(encoding="utf-8")
    start = src.index(f"\n{name}() {{")
    return src[start:src.index(f"\n{until}() {{", start)]


def _push_floor_body():
    """The push floor's source, which is where the expensive path is visible."""
    return _ci_function_body("check_pushfloor", "check_dependency")


@contextlib.contextmanager
def _stub_git_answering_the_floor(changed_paths, *, main_paths=None, added_paths=(),
                                  main_tree_paths=(), main_readable=True):
    """PATH-shadow git so the floor sees a chosen diff, and real git does the rest.

    Model the pushed diff, the final diff from main, and main's admitted paths.
    Keeping those separate exercises imported files and branch-owned changes
    without creating commits or changing repository files. Other git calls,
    including the branch name, HEAD and status, pass through to the real git.
    """
    real = shutil.which("git")
    if main_paths is None:
        main_paths = changed_paths
    quoted_main = " ".join(shlex.quote(path) for path in main_paths)
    quoted_added = " ".join(shlex.quote(path) for path in added_paths)
    quoted_tree = " ".join(shlex.quote(path) for path in main_tree_paths)
    quoted_main_added = " ".join(shlex.quote(path) for path in added_paths
                                  if path in main_paths and path not in main_tree_paths)
    main_exit = "" if main_readable else "exit 7; "
    with tempfile.TemporaryDirectory(prefix="ci-selftest-stub-git-") as td:
        stub = pathlib.Path(td) / "git"
        stub.write_text(
            "#!/bin/sh\n"
            'case " $* " in *" diff --name-only origin/main HEAD "*)\n'
            f'  {main_exit}printf "%s\\n" {quoted_main}; exit 0 ;;\n'
            '  *" diff --diff-filter=A --name-only origin/main -- "*)\n'
            f'  {main_exit}printf "%s\\n" {quoted_main_added}; exit 0 ;;\n'
            '  *" ls-tree -r --name-only origin/main "*)\n'
            f'  {main_exit}printf "%s\\n" {quoted_tree}; exit 0 ;;\n'
            f'*" {FIXTURE_RANGE} "*)\n'
            '  case " $* " in *--diff-filter=ACMR*)\n'
            f'    printf "%s\\n" {" ".join(changed_paths)}; exit 0 ;;\n'
            # Filename admission independently reads newly pushed paths.
            # Ordinary fixtures add none; integration fixtures declare them.
            f'  *--diff-filter=ACR*) printf "%s\\n" {quoted_added}; exit 0 ;;\n'
            '  esac ;;\n'
            'esac\n'
            f'exec {shlex.quote(real or "git")} "$@"\n'
        )
        stub.chmod(0o755)
        yield {"PATH": f"{td}{os.pathsep}{os.environ.get('PATH', '')}"}


def test_migration_structure_is_checked_before_slow_work():
    floor = _push_floor_body()
    migration = _ci_function_body("check_migration", "check_binding")
    command = '"$PY" ops/migration-order-gate.py'
    check("migration structure runs in the push floor before gate closure",
          command in floor and floor.index(command) < floor.index('local gate_surface='))
    check("migration structure runs before schema loading and the database probe",
          command in migration and migration.index(command) < migration.index('local dsn='))


def test_push_floor_defers_the_gates_class_instead_of_running_it():
    """A touched gate with no paired selftest is NAMED, not paid for locally.

    check_pushfloor() used to answer "this push touched a gate whose blast radius
    I cannot predict" by running the whole gates class on the push path. The push
    floor is the only thing between a session and --no-verify, and --no-verify
    disables the entire hook — owner check and secret scan included — so a floor
    that can turn one push into minutes does not get skipped occasionally, it
    gets skipped as a habit. Nothing stopped being checked: `gates` is still a
    class and hosted `ops/ci.sh --strict` is the required check on main.

    THE REGRESSION IS DETECTED FROM SOURCE, BEFORE ANYTHING RUNS. A reintroduced
    call is a fact about the file, so this case reads it and stops. That is what
    keeps the test from running the very class it exists to keep off the push
    path — the failure it is looking for is exactly the one that would make
    running it expensive.
    """
    body = _push_floor_body()
    if "check_gates" in body:
        check("the floor no longer calls the gates class as a fallback", False,
              "check_pushfloor calls check_gates — refusing to run the class to confirm it")
        return
    check("the floor no longer calls the gates class as a fallback", True)

    t0 = time.monotonic()
    with _stub_git_answering_the_floor([FIXTURE_GATE]) as stub_env:
        try:
            rc, out = run(["--only", "pushfloor"],
                          env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                          timeout=FLOOR_BUDGET_SECONDS)
        except subprocess.TimeoutExpired:
            check("the floor returns promptly on the unpaired-gate shape", False,
                  f"still running after {FLOOR_BUDGET_SECONDS}s")
            return
    elapsed = time.monotonic() - t0

    gate_name = pathlib.Path(FIXTURE_GATE).stem
    check("the unpaired gate is still detected and named",
          gate_name in out and "deferred" in out, out[-600:])
    check("the deferral says where the class actually runs",
          "hosted" in out.lower(), out[-600:])
    check("the gates class produced no verdict on the push path",
          not re.search(r"(OK|FAIL|SKIP)\s+gates\b", out), out[-600:])
    check("the floor returns promptly on the unpaired-gate shape",
          elapsed < FLOOR_BUDGET_SECONDS, f"{elapsed:.0f}s")
    check("naming a deferred gate is not itself a failure", rc == 0, f"rc={rc}")


def test_push_floor_distinguishes_imported_main_paths_from_branch_changes():
    inherited_name = "mcp-server/test/doctorcre-v5-review.test.mjs"
    inherited_gate = "hooks/zz-ci-selftest-fixture-inherited-gate.py"
    with _stub_git_answering_the_floor(
            [inherited_name, inherited_gate, FIXTURE_GATE],
            main_paths=[FIXTURE_GATE], added_paths=[inherited_name],
            main_tree_paths=[inherited_name, inherited_gate]) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("importing admitted main paths does not fail the push floor", rc == 0, out[-900:])
    check("main-identical gates are excluded but the branch's gate remains checked",
          pathlib.Path(inherited_gate).stem not in out
          and pathlib.Path(FIXTURE_GATE).stem in out, out[-900:])
    owned_name = "ops/report-v2.json"
    with _stub_git_answering_the_floor(
            [owned_name], main_paths=[owned_name], added_paths=[owned_name]) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("a branch-owned forbidden filename is still refused",
          rc != 0 and owned_name in out and "path-hygiene" in out, out[-900:])
    historical_gate = "hooks/zz-ci-selftest-historical-v2.py"
    with _stub_git_answering_the_floor(
            [historical_gate], main_paths=[historical_gate],
            added_paths=[historical_gate], main_tree_paths=[historical_gate]) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("editing an imported historical path is checked without rejudging its name",
          rc == 0 and pathlib.Path(historical_gate).stem in out, out[-900:])
    with _stub_git_answering_the_floor(
            [owned_name], added_paths=[owned_name], main_readable=False) as stub_env:
        rc, out = run(["--only", "pushfloor"],
                      env={"CARR_CI_RANGE": FIXTURE_RANGE, **stub_env},
                      timeout=FLOOR_BUDGET_SECONDS)
    check("an unreadable main preserves the full push-floor scope",
          rc != 0 and owned_name in out and "path-hygiene" in out, out[-900:])


def test_strict_still_owns_the_gates_class():
    """The deferral is scoped to a --only run, so hosted strict never takes it.

    Asserted from source: the behavioural proof would mean running the gates
    class from a file ops/ci.sh runs inside that class. test_class_table_is_complete
    independently proves `gates` is still a real class with a check_ behind it.
    """
    body = _ci_function_body("check_pushfloor", "check_dependency")

    check("the deferral only fires on a class-scoped (--only) run",
          '[ -n "$ONLY" ]' in body, "guard missing — hosted would defer too")
    check("and never when the gates class is already selected",
          "! selected gates" in body)


# ------------------------------------------------- 25. the hosted split
def _hosted_workflow(filename="ci.yml"):
    """Workflow as a dict, parsed by the js-yaml the server already carries."""
    import json
    out = subprocess.run(
        ["node", "-e",
         "const y=require('js-yaml');const fs=require('fs');"
         "process.stdout.write(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))",
         str(REPO / ".github" / "workflows" / filename)],
        cwd=REPO / "mcp-server", capture_output=True, text=True, timeout=60)
    check(f"{filename} parses", out.returncode == 0, out.stderr[-300:])
    return json.loads(out.stdout) if out.returncode == 0 else {}


def test_hosted_ci_runs_classes_in_parallel_behind_one_required_context():
    """Joe's ruling 2026-09-23 (audit item 3 of 8): the hosted run is split into
    parallel jobs by class so a PR waits for the slowest class, not the sum.

    WHAT MUST STAY TRUE, because five files pin it: the required status check
    on main is the single context `ops/ci.sh --strict`. The ruleset, the
    automerge pilot and the backup workflow all name it. So the split keeps a
    job with exactly that name, and that job is a GATE over the class jobs: it
    needs them, runs `always()` so a red class still reports the context, and
    fails unless every class job succeeded. Every class in CLASS_ORDER runs in
    exactly one class job; a class listed nowhere would silently stop running
    hosted, which is the whole failure this suite exists to catch.
    """
    wf = _hosted_workflow()
    jobs = wf.get("jobs") or {}
    gates = [k for k, v in jobs.items() if v.get("name") == "ops/ci.sh --strict"]
    check("exactly one job carries the required context name", len(gates) == 1, gates)
    if len(gates) != 1:
        return
    gate = jobs[gates[0]]
    needs = gate.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    check("the required-context job needs at least one class job", bool(needs), needs)
    check("the required-context job runs even when a class job fails",
          "always()" in str(gate.get("if", "")), gate.get("if"))
    steps = " ".join(json.dumps(st) for st in gate.get("steps", []))
    check("the required-context job fails unless every needed job succeeded",
          all(f"needs.{n}.result" in steps for n in needs) and "exit 1" in steps, steps[:200])
    src = CI.read_text()
    order = re.search(r'^CLASS_ORDER="([^"]+)"', src, re.M)
    classes = order.group(1).split() if order else []
    ran = []
    for n in needs:
        job = jobs.get(n) or {}
        groups = ((job.get("strategy") or {}).get("matrix") or {}).get("classes") or []
        for g in groups:
            ran.extend(str(g).replace(",", " ").split())
        run_lines = " ".join(str(st.get("run", "")) for st in job.get("steps", []))
        check(f"class job '{n}' runs ops/ci.sh --strict --only its matrix classes",
              "ops/ci.sh --strict --only" in run_lines and "matrix.classes" in run_lines,
              run_lines[-160:])
    check("every CLASS_ORDER class runs in exactly one class job",
          sorted(ran) == sorted(classes) and len(ran) == len(set(ran)),
          {"ran": ran, "order": classes})


def test_hosted_migration_budget_covers_observed_acceptance_runtime():
    """PR1121's strict migration job was killed at 20 minutes, while its
    separate exact-head DB acceptance succeeded after 23m51s. Allow at least
    30 minutes including setup, without relaxing the other groups' budgets.
    Both workflows run the canonical migration class; its budget must also
    cover the separate database lane. Read the actual job/matrix wiring,
    so an unused budget cannot pass.

    The database lane runs that class and then its own acceptance programs.
    On 2026-10-04 it routinely took 22-24 minutes, and six branches
    (PR 1470's run 37182226032 among them) were cancelled at a 25-minute cap
    after every check had passed. It needs the same headroom.
    """
    job = _hosted_workflow()["jobs"]["classes"]
    groups = job["strategy"]["matrix"]["classes"]
    budgets = re.fullmatch(
        r"\$\{\{ matrix\.classes == 'migration' && (\d+) \|\| (\d+) \}\}",
        str(job["timeout-minutes"]))
    check("class jobs select a bounded migration-specific budget", budgets is not None)
    if budgets is None:
        return
    migration_budget, other_budget = map(int, budgets.groups())
    database_jobs = _hosted_workflow("db-acceptance.yml").get("jobs") or {}
    database_budget = (database_jobs.get("acceptance") or {}).get("timeout-minutes")
    check("database acceptance declares a finite job budget",
          isinstance(database_budget, int) and database_budget > 0, database_budget)
    check("hosted migration budget covers the database lane budget",
          isinstance(database_budget, int) and migration_budget >= database_budget > 0,
          {"migration_minutes": migration_budget, "database_minutes": database_budget})
    bounded_headroom = range(30, 36)
    check("database lane has bounded headroom over its observed 24-minute run",
          database_budget in bounded_headroom, database_budget)
    migration = [migration_budget for group in groups if group == "migration"]
    check("migration job has bounded headroom over the observed 24-minute run",
          len(migration) == 1 and migration[0] in bounded_headroom, migration)
    other = [other_budget for group in groups if group != "migration"]
    check("other class groups retain their 20-minute budgets",
          bool(other) and all(budget == 20 for budget in other), other)


def test_gate_replay_has_an_independent_required_class():
    """PR1546's gates passed at 1101s, then cleanup hit the 20-minute cap.

    Its 295s replay must run in a separate required job, preserving the cap
    and every check instead of making the already long job wait for replay.
    """
    src = CI.read_text()
    order = re.search(r'^CLASS_ORDER="([^"]+)"', src, re.M)
    check("full local CI includes the replay class",
          order is not None and "replay" in order.group(1).split())
    gates_body = src.split("check_gates() {", 1)[1].split("\ncheck_", 1)[0]
    check("gates no longer serializes the real-fixture replay",
          '"$PY" ops/gate-replay.py' not in gates_body)
    job = _hosted_workflow()["jobs"]["classes"]
    groups = job["strategy"]["matrix"]["classes"]
    check("hosted replay runs once as its own required matrix job",
          groups.count("replay") == 1)
    check("gates and replay retain the existing 20-minute cap",
          job["timeout-minutes"] == "${{ matrix.classes == 'migration' && 35 || 20 }}")
    with tempfile.TemporaryDirectory() as tmp:
        fixture = pathlib.Path(tmp) / "gate-replay.py"
        fixture.write_text("import os,sys\n"
                           "print('gate-replay: OK replay fixture ran')\n"
                           "sys.exit(int(os.environ['REPLAY_FIXTURE_RC']))\n")
        # Invoke the class through ci.sh's normal --only interface. Replace
        # only the replay program with a cheap fixture, keeping its handling.
        script = pathlib.Path(tmp) / "ci.sh"
        body = src.replace('"$PY" ops/gate-replay.py', f'"$PY" {shlex.quote(str(fixture))}')
        # The script resolves the repository from its own path.
        body = re.sub(r'^REPO=.*$', f'REPO={shlex.quote(str(REPO))}', body, flags=re.M)
        script.write_text(body)
        for child_rc, expected_rc in ((0, 0), (1, 1), (78, 1), (124, 1)):
            out = subprocess.run(["bash", str(script), "--strict", "--only", "replay"],
                                 cwd=REPO, env=scrubbed_env(dict(os.environ,
                                     REPLAY_FIXTURE_RC=str(child_rc))),
                                 capture_output=True, text=True, timeout=10)
            check(f"strict replay propagates child exit {child_rc}",
                  out.returncode == expected_rc and "replay fixture ran" in out.stdout + out.stderr,
                  out.stdout + out.stderr)


def test_hosted_zsh_setup_does_not_refresh_working_indexes():
    """PR 1465 spent its entire job budget in apt update before any class ran.

    Execute the workflow's setup with a synthetic apt, not a second installer.
    A working install must never refresh; stale indexes must still be repaired;
    an unavailable mirror must fail setup rather than green-light missing zsh.
    """
    wf = _hosted_workflow()
    setup = next(st for st in wf["jobs"]["classes"]["steps"]
                 if st.get("name") == "Install zsh")
    check("zsh setup has a three-minute step deadline",
          0 < setup.get("timeout-minutes", 0) <= 3)
    with tempfile.TemporaryDirectory(prefix="ci-zsh-setup-") as tmp:
        fixture = pathlib.Path(tmp)
        sudo = fixture / "sudo"
        sudo.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
log = pathlib.Path(os.environ["CI_SETUP_CALLS"])
calls = json.loads(log.read_text()) if log.exists() else []
calls.append(sys.argv[1:])
log.write_text(json.dumps(calls))
args = sys.argv[1:]
mode = os.environ["CI_SETUP_FIXTURE"]
if mode == "working" and "update" in args:
    sys.exit(91)
if mode != "working" and len(calls) == 1:
    sys.exit(100)
if mode == "unavailable" and "update" in args:
    sys.exit(100)
if mode == "retry-failed" and len(calls) == 3:
    sys.exit(100)
''')
        sudo.chmod(0o755)
        for mode, expected in (("working", ["install"]),
                               ("stale", ["install", "update", "install"]),
                               ("unavailable", ["install", "update"]),
                               ("retry-failed", ["install", "update", "install"])):
            log = fixture / (mode + ".json")
            env = scrubbed_env()
            env.update(PATH=str(fixture) + os.pathsep + os.environ["PATH"],
                       CI_SETUP_CALLS=str(log), CI_SETUP_FIXTURE=mode)
            ran = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", setup["run"]],
                                 cwd=fixture, env=env, capture_output=True, text=True,
                                 timeout=10)
            calls = json.loads(log.read_text()) if log.exists() else []
            actions = [next((arg for arg in args if arg in ("install", "update")), "unknown")
                       for args in calls]
            check(f"zsh setup {mode} uses the required install/refresh path",
                  actions == expected, actions)
            check(f"zsh setup {mode} propagates its outcome",
                  (ran.returncode != 0) == (mode in ("unavailable", "retry-failed")),
                  ran.returncode)
            check(f"zsh setup {mode} bounds every apt network request",
                  bool(calls) and all(args[0] == "apt-get" and
                      all(option in args for option in ("Acquire::Retries=1",
                          "Acquire::http::Timeout=15", "Acquire::https::Timeout=15"))
                      for args in calls), calls)
        check("zsh remains a required installed package",
              all("zsh" in args for args in calls if "install" in args))


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv) == ["--collection-only"]:
        test_every_test_file_in_the_tree_is_collected()
        return 1 if any(not ok for _, ok, _ in RESULTS) else 0
    for fn in (test_no_green_without_running,
               test_class_table_is_complete,
               test_strict_turns_skip_into_failure,
               test_unknown_class_refuses,
               test_migration_refuses_non_loopback,
               test_tracked_scripts_are_executable_in_git,
               test_secret_scanner_catches_and_respects_allow,
               test_dep_check_detects_a_stale_lock,
               test_types_class_catches_a_seeded_type_error,
               test_type_check_script_resolves_mypy_in_both_homes,
               test_lock_is_not_platform_specific,
               test_migration_filenames_match_the_runner,
               test_known_gaps_all_expire,
               test_no_env_claims_a_production_hostname,
               test_mypy_pin_acceptance_is_narrow,
               test_every_test_file_in_the_tree_is_collected,
               test_the_walk_prunes_named_roots_only,
               test_a_failing_gates_output_is_printed_whole_when_it_is_short,
               test_a_long_failing_gate_log_is_tailed_and_still_redacted,
               test_fail_tail_withholds_the_window_when_it_cannot_redact,
               test_gates_treats_only_78_as_not_configured,
               test_gates_selftests_have_a_process_group_watchdog,
               test_migration_structure_is_checked_before_slow_work,
               test_push_floor_defers_the_gates_class_instead_of_running_it,
               test_push_floor_distinguishes_imported_main_paths_from_branch_changes,
               test_strict_still_owns_the_gates_class,
               test_hosted_ci_runs_classes_in_parallel_behind_one_required_context,
               test_hosted_migration_budget_covers_observed_acceptance_runtime,
               test_gate_replay_has_an_independent_required_class,
               test_hosted_zsh_setup_does_not_refresh_working_indexes):
        try:
            fn()
        except Exception as exc:  # a crashing case is a failing case, never a silent skip
            check(f"{fn.__name__} raised", False, repr(exc))

    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\nci-selftest: {len(RESULTS) - failed}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    # BEFORE ANY TEST RUNS. A journal on disk means a previous run was killed
    # with seeded damage live in the tree; this restores it and refuses to
    # start. Deliberately not inside main(), so no future reordering of the
    # test list can end up running a test before the tree is known good.
    _recover_stale_journal()
    sys.exit(main())
