#!/usr/bin/env python3
"""Run ops/ci.sh's pushfloor class: the diff-scoped checks a push must pass locally.

ci.sh calls this from the repository root and owns the class verdict. Detail goes to
stderr; the one stdout line is the verdict text. Exit 0 passes the class, 1 fails it.
Each check writes <logdir>/pushfloor-<name>.log and .rc, which ci.sh's exit trap
keeps only for failures.

--pre-pr [--body FILE] is ./run.sh pre-pr: the three checks hosted CI failed on in the
2026-10-28 retro (#2623, #2629, #2636, #2637), each costing a full CI round. It prints one
line per failure saying what to do and exits 1 on any failure.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RED, YELLOW, RESET = "\033[31m", "\033[33m", "\033[0m"
MIGRATION_STRUCTURE = re.compile(
    r"^(migrations/|db/migration-baseline\.json$|tools/migration_(number_contract|baseline|source)\.py$"
    r"|ops/migration-order-gate\.py$)")
TEST_COLLECTION = re.compile(r"(^ops/ci\.sh$|(^|/)(test[^/]*|[^/]*selftest[^/]*)\.(py|sh|mjs|js)$)")
SCAC_ATLAS = re.compile(
    r"^(migrations/[0-9]+_.*scac.*\.sql|mcp-server/src/scac-mutation-registry\.current\.generated\.js"
    r"|ops/config/scac-registry-.*\.json)$")
GATE_SURFACE = re.compile(
    r"^(hooks/[^/]+\.py|ops/ci\.sh|ops/githooks/|ops/config/codex-hooks\.json|ops/[^/]+-selftest\.py)")
# The detectors and their corpus. The hook runs both scanners on every push; their
# 64s qualification suite runs here only when one of them changes (WR-000537).
SECRET_DETECTOR = re.compile(r"^(security/|ops/ci-secret-scan\.py$|ops/githooks/pre-push$)")
MISSING_PACKAGE = re.compile(r"Error.*Cannot find package '")


def git_lines(*args):
    """Output lines of a git command, or None when it fails."""
    result = subprocess.run(["git", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.decode(errors="surrogateescape").splitlines() if line]


def words(lines):
    """Split like an unquoted shell expansion of newline-joined lines."""
    return " ".join(lines).split()


class Floor:
    def __init__(self, logdir, python):
        self.logdir = Path(logdir)
        self.python = python
        self.failures = []
        self.ran = []

    def run(self, name, *command):
        log = self.logdir / f"pushfloor-{name}.log"
        with log.open("wb") as out:
            try:
                rc = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT).returncode
            except OSError as error:
                out.write(f"{command[0]}: {error.strerror}\n".encode())
                rc = 127
        Path(f"{log}.rc").write_text(f"{rc}\n")
        return rc, log

    def py(self, name, script, *args):
        return self.run(name, self.python, script, *args)

    def fail(self, name, remedy, log=None, tail=None):
        if log is not None:
            lines = log.read_bytes().splitlines(keepends=True)
            sys.stderr.buffer.write(b"".join(lines if tail is None else lines[-tail:]))
            sys.stderr.flush()
        self.failures.append(name)
        print(f"        {RED}{name}{RESET} — {remedy}", file=sys.stderr, flush=True)


def changed_paths(diff_range):
    """Paths this push changes that also differ from main; main unreadable keeps them all."""
    if not diff_range:
        return []
    changed = git_lines("diff", "--name-only", "--diff-filter=ACMR", diff_range) or []
    main_changed = git_lines("diff", "--name-only", "origin/main", "HEAD")
    if main_changed is not None:
        changed = sorted(set(changed) & set(main_changed))
    return changed


def new_to_main(diff_range):
    added = git_lines("diff", "--name-only", "--diff-filter=ACR", diff_range) or []
    main_paths = git_lines("ls-tree", "-r", "--name-only", "origin/main")
    if main_paths is not None:
        added = sorted(set(added) - set(main_paths))
    return added


def check(floor, diff_range, gates_deferred):
    changed = changed_paths(diff_range)

    rc, log = floor.py("no-client-deliverables", "ops/no-client-deliverables-gate.py")
    if rc:
        floor.fail("no-client-deliverables", "a client-deliverable path is tracked. git rm it — "
                   "this repo is public and must carry no client record.", log, 12)

    rc, log = floor.py("hook-deletion", "ops/hook-deletion-check.py")
    if rc:
        floor.fail("hook-deletion", "remove hook registrations before deleting or renaming their scripts", log)

    if changed and Path("ops/githooks/path-hygiene-check.py").is_file():
        added = new_to_main(diff_range)
        if added:
            rc, log = floor.py("path-hygiene", "ops/githooks/path-hygiene-check.py", "--paths", *words(added))
            if rc:
                floor.fail("path-hygiene", "rename the path before it is published — depth or draft/final "
                           "naming (rule 0e22e34a). After the push it needs a history rewrite.", log, 12)

    if any(MIGRATION_STRUCTURE.search(path) for path in changed):
        floor.ran.append("migration-structure")
        rc, log = floor.py("migration-structure", "ops/migration-order-gate.py")
        if rc:
            floor.fail("migration-structure",
                       "reserve a forward migration number and repair every reference before push", log, 15)

    if any(TEST_COLLECTION.search(path) for path in changed):
        floor.ran.append("test-collection")
        rc, log = floor.py("test-collection", "ops/ci-selftest.py", "--collection-only")
        if rc:
            floor.fail("test-collection",
                       "include the new test in CI's collection or its named decision exception", log, 12)

    if any(SECRET_DETECTOR.search(path) for path in changed):
        floor.ran.append("secret-qualification")
        for test in sorted(Path("security").glob("test_*.py")):
            rc, log = floor.py(f"secret-{test.stem}", str(test))
            if rc:
                floor.fail(f"secret-{test.stem}", f"a changed detector fails its qualification: "
                           f".venv/bin/python {test}", log, 20)

    if any(path.endswith(".py") for path in changed):
        rc, log = floor.run("types", "./bin/type-check.sh")
        if rc == 78:
            print(f"        {YELLOW}not run{RESET}  types — mypy absent; the hosted types class still covers this",
                  file=sys.stderr, flush=True)
        else:
            floor.ran.append("types")
            if rc:
                floor.fail("types", "run the configured type check: ./bin/type-check.sh", log, 20)

    if any(SCAC_ATLAS.search(path) for path in changed):
        floor.ran.append("scac-atlas")
        rc, log = floor.run("scac-atlas", "node", "--test",
                            "mcp-server/test/atlas-inventory-graph-read.test.mjs",
                            "mcp-server/test/atlas-inventory-graph-web.test.mjs")
        if rc:
            tail = log.read_bytes().splitlines(keepends=True)[-18:]
            sys.stderr.buffer.write(b"".join(tail))
            missing = next((line for line in log.read_text(errors="replace").splitlines()
                            if MISSING_PACKAGE.search(line)), None)
            if missing is not None:
                sys.stderr.buffer.write(missing.encode(errors="replace") + b"\n")
                floor.fail("scac-atlas", "missing Node packages; run npm ci --prefix mcp-server before push")
            else:
                floor.fail("scac-atlas", "active registry changed; update the Atlas version contract "
                           "or repair its reader before push")

    surface = [path for path in changed if GATE_SURFACE.search(path)]
    if surface:
        floor.ran.append("gate-closure")
        unclassified = []
        hooks = [path for path in surface
                 if re.search(r"^hooks/[^/]+\.py$", path) and not path.endswith("-selftest.py")]
        for gate in words(hooks):
            base = Path(gate).name[:-len(".py")] if gate.endswith(".py") else Path(gate).name
            paired = f"ops/{base}-selftest.py"
            if not Path(paired).is_file():
                unclassified.append(base)
            elif paired not in surface:  # A changed selftest runs in the next loop.
                rc, log = floor.py(f"{base}-selftest", paired)
                if rc:
                    floor.fail(f"{base}-selftest", "the gate you changed fails its own acceptance test: "
                               f".venv/bin/python {paired}", log, 15)

        for paired in words(path for path in surface if re.search(r"^ops/[^/]+-selftest\.py$", path)):
            if Path(paired).is_file():
                name = Path(paired).name
                rc, log = floor.py(name, paired)
                if rc:
                    floor.fail(name, f"run it alone: .venv/bin/python {paired}", log, 15)

        for isolation in ("ops/hook-env-isolation-selftest.py", "ops/selftest-git-isolation-check.py"):
            if Path(isolation).is_file():
                name = Path(isolation).name
                rc, log = floor.py(name, isolation)
                if rc:
                    floor.fail(name, f"run it alone to iterate: .venv/bin/python {isolation}", log, 12)

        if unclassified and gates_deferred:
            floor.ran.append("gates-deferred")
            print(f"        {YELLOW}deferred{RESET}   gates — no paired selftest for: {' '.join(unclassified)}"
                  " — the full class runs hosted (required check on main)", file=sys.stderr)
            print("                   run it locally now: ops/ci.sh --only gates"
                  " · durable fix: add ops/<gate>-selftest.py", file=sys.stderr, flush=True)


def pull_request_files(span):
    """The changed-file records the review gate reads from GitHub, built from git."""
    files = []
    for line in git_lines("diff", "--name-status", "-M", span) or []:
        status, *paths = line.split("\t")
        files.append({"filename": paths[-1], "previous_filename": paths[0]} if status[0] in "RC"
                     else {"filename": paths[0]})
    return files


def pre_pr(python, body_file):
    """Failure lines for the leak guard, gate integrity and the review gate's Card line."""
    failures = []
    body = None
    if body_file is not None:
        try:
            body = Path(body_file).read_text()
        except (OSError, UnicodeError):
            failures.append("cannot read PR body: pass a readable UTF-8 file with --body")
    os.chdir(Path(__file__).resolve().parents[1])
    base, head = git_lines("merge-base", "origin/main", "HEAD"), git_lines("rev-parse", "HEAD")
    if not base or not head:
        return ["no merge base with origin/main: run git fetch origin main, then rerun ./run.sh pre-pr"]
    span = f"{base[0]}..{head[0]}"
    leak = subprocess.run([python, "security/leak_guard.py", "--range", span], capture_output=True)
    if leak.returncode:
        failures.append(f"leak guard failed: inspect findings with python3 security/leak_guard.py --range {span}")
    integrity = subprocess.run([python, "hooks/gate-integrity.py", "--strict"], capture_output=True)
    if integrity.returncode:
        failures.append("gate integrity failed: inspect findings with python3 hooks/gate-integrity.py --strict")
    if body_file is None:
        try:
            view = subprocess.run(["gh", "pr", "view", "--json", "body", "--jq", ".body"],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            body = view.stdout if view.returncode == 0 else None
        except OSError:
            pass
    if body is None and body_file is None:
        failures.append("no PR body to check and no open PR for this branch: write the body to a file "
                        "and rerun ./run.sh pre-pr --body <file>")
    elif body is not None:
        spec = importlib.util.spec_from_file_location("review_gate", "ops/review-gate.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        files = pull_request_files(span)
        if not gate.card_allowed({"body": body, "changed_files": len(files), "user": {}}, files):
            failures.append("PR body needs a line that is exactly Card: WR<n>.S<n>.J<n> with nothing "
                            "before or after it: add your brief's card line on its own line")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logdir")
    parser.add_argument("--pre-pr", action="store_true",
                        help="check this branch the way hosted CI will before git push / gh pr create")
    parser.add_argument("--body", help="with --pre-pr: PR body file (default: the open PR's body via gh)")
    parser.add_argument("--gates-deferred", action="store_true",
                        help="the gates class is not selected in this run")
    args = parser.parse_args(argv)
    if args.pre_pr:
        failures = pre_pr(sys.executable, args.body)
        print("\n".join(failures), end="\n" if failures else "")
        return int(bool(failures))
    if not args.logdir:
        parser.error("--logdir is required")
    started = int(time.time())
    floor = Floor(args.logdir, sys.executable)
    check(floor, os.environ.get("CARR_CI_RANGE", ""), args.gates_deferred)
    seconds = int(time.time()) - started
    if floor.failures:
        print(f"failed: {' '.join(floor.failures)} ({seconds}s)")
        return 1
    ran = f" + {' '.join(floor.ran)}" if floor.ran else ""
    print(f"{seconds}s · baseline, path shape{ran}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
