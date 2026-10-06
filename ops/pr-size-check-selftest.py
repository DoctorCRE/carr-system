#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""Selftest for ops/pr-size-check.py, the warn-only PR decomposition note.

Hermetic: a throwaway git repository built through ops/git_env.fixture_env,
one branch per case cut from `main`, and a `main...<case>` range standing in
for `origin/main...HEAD`. It proves:

  * a change set under both thresholds prints nothing, and one sitting exactly
    ON a threshold prints nothing (the standard is "over", not "at");
  * over the line threshold warns, naming the counts, thresholds and top file;
  * over the file threshold warns;
  * a change made only of lockfiles, generated registries, minified assets,
    node_modules, vendor and out/ paths is silent;
  * a migration is never excluded, even one whose name looks generated;
  * a rename is judged on BOTH sides: migrations moved into vendor/ still
    count, and only a rename whose two sides are both excluded is left out;
  * a near-pure rename plus a separate content edit prints the mix line, and a
    pure rename alone does not; a binary modification or addition is a content
    edit, and a mode-only change is not;
  * the thresholds are the escalation router's own constants, not a copy;
  * the exit code is 0 in every case, including a range git cannot resolve;
  * the pre-push hook runs the note before any top-level exit, strips Git's
    hook variables, and discards its exit status.
"""
from __future__ import annotations

import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()
spec = importlib.util.spec_from_file_location("pr_size_check", HERE / "pr-size-check.py")
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check  # dataclasses resolve their module by name
spec.loader.exec_module(check)

import jev_intake  # noqa: E402  (stdlib-only; the thresholds' owner)

checks: list[tuple[str, bool]] = []


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=ENV,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write(repo: Path, rel: str, lines: int, tag: str = "x") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{tag} line {i}\n" for i in range(lines)))


def case(repo: Path, name: str, build) -> tuple[int, str]:
    """Cut `name` from main, apply build(repo), commit, run the check."""
    git(repo, "switch", "-q", "-c", name, "main")
    build(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", name)
    git(repo, "switch", "-q", "main")
    return run(repo, f"main...{name}")


def run(repo: Path, rng: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    saved = {k: os.environ.pop(k, None) for k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")}
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = check.main(["pr-size-check.py", rng, str(repo)])
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    return rc, out.getvalue() + err.getvalue()


LIMIT_LINES = jev_intake.ROUTE_MAX_DIFF_LINES
LIMIT_FILES = jev_intake.ROUTE_MAX_FILES

checks.append(("thresholds are the escalation router's constants (ops/jev_intake.py)",
               check.thresholds() == (LIMIT_LINES, LIMIT_FILES)))
checks.append(("a migration path is never excluded, even one named like generated output",
               check.is_excluded("migrations/0999_add.generated.sql") is None
               and check.is_excluded("db/migrations/0001_vendor.lock") is None))
checks.append(("lockfiles, generated registries, minified, node_modules, vendor and out/ are excluded",
               all(check.is_excluded(p) for p in (
                   "mcp-server/package-lock.json", "requirements.lock", "skills-lock.json",
                   "mcp-server/src/scac-mutation-registry.v27.generated.js",
                   "db/schema.sql", "web/app.min.js", "web/app.min.css",
                   "mcp-server/node_modules/x/index.js",
                   "dealroom/reports/vendor/maplibre/maplibre-gl.mjs",
                   "out/ci.log"))))
checks.append(("an ordinary source path is not excluded",
               check.is_excluded("ops/pr-size-check.py") is None
               and check.is_excluded("tools/outline.py") is None))

report = check.assess([check.Change("lib/fix.py", 3, "M"),
                       check.Change("tests/test_fix.py", 400, "A")], LIMIT_LINES, LIMIT_FILES)
checks.append(("test evidence cannot inflate code size or code file count",
               report.lines == 3 and report.files == 1 and not report.warn))
report = check.assess([check.Change("ops/fix-selftest.py", 400, "A")], LIMIT_LINES, LIMIT_FILES)
checks.append(("test-only evidence is reported separately",
               report.lines == 0 and report.files == 0 and report.test_lines == 400 and report.test_files == 1))
report = check.assess([check.Change("tests/moved.py", 400, "R", 95, "lib/code.py")], LIMIT_LINES, LIMIT_FILES)
checks.append(("moving code into tests cannot hide a code change",
               report.lines == 400 and report.files == 1))

with tempfile.TemporaryDirectory(prefix="pr-size-check-") as tmp:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=ENV)
    git(repo, "config", "user.email", "selftest@example.invalid")
    git(repo, "config", "user.name", "selftest")
    write(repo, "lib/big.py", 200, "big")
    write(repo, "lib/other.py", 20, "other")
    write(repo, "lib/a.py", 10, "a")
    write(repo, "lib/tool.sh", 3, "tool")
    write(repo, "vendor/keep.js", 40, "keep")
    (repo / "lib" / "bin.dat").write_bytes(b"\0before")
    for i in range(LIMIT_FILES + 1):
        write(repo, f"migrations/{i:04d}_m.sql", 2, f"m{i}")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")

    def small(r: Path) -> None:
        write(r, "lib/a.py", 15, "a2")
        write(r, "lib/new.py", 5)
    rc, text = case(repo, "small", small)
    checks.append(("under both thresholds: silent, exit 0", rc == 0 and text == ""))

    def hyphenated_evidence(r: Path) -> None:
        write(r, "tools/progress_board.py", 3)
        write(r, "tools/test-progress-board.py", 400)
    rc, text = case(repo, "hyphenated-evidence", hyphenated_evidence)
    checks.append(("hyphenated Python test evidence does not produce a size warning", rc == 0 and text == ""))
    for name, path in (("suffix-near-miss", "tools/test-progress-board.py.bak"),
                       ("directory-near-miss", "tools/test-progress-board/source.py")):
        rc, text = case(repo, name, lambda r, path=path: write(r, path, LIMIT_LINES + 50))
        checks.append((f"{name} counts as code and warns", rc == 0 and "hard to review in one piece" in text))

    def at_threshold(r: Path) -> None:
        per, extra = divmod(LIMIT_LINES, LIMIT_FILES)
        for i in range(LIMIT_FILES):
            write(r, f"edge/f{i}.py", per + (extra if i == 0 else 0))
    rc, text = case(repo, "edge", at_threshold)
    checks.append((f"exactly {LIMIT_LINES} lines in {LIMIT_FILES} files: silent (over, not at)",
                   rc == 0 and text == ""))

    rc, text = case(repo, "lines", lambda r: write(r, "lib/huge.py", LIMIT_LINES + 50))
    checks.append(("over the line threshold: warns with counts, thresholds and the top file",
                   rc == 0 and f"{LIMIT_LINES + 50} changed lines" in text
                   and f"threshold {LIMIT_LINES}" in text and "lib/huge.py" in text
                   and "warning only" in text))

    def many(r: Path) -> None:
        for i in range(LIMIT_FILES + 1):
            write(r, f"many/f{i}.py", 1)
    rc, text = case(repo, "files", many)
    checks.append(("over the file threshold: warns",
                   rc == 0 and f"{LIMIT_FILES + 1} files" in text
                   and f"threshold {LIMIT_FILES}" in text))

    def noise(r: Path) -> None:
        write(r, "mcp-server/package-lock.json", 2000)
        write(r, "requirements.lock", 900)
        write(r, "mcp-server/src/scac-mutation-registry.v99.generated.js", 800)
        write(r, "web/app.min.js", 700)
        write(r, "mcp-server/node_modules/pkg/index.js", 600)
        write(r, "dealroom/vendor/lib.mjs", 500)
        write(r, "out/report.txt", 400)
        write(r, "db/schema.sql", 400)
    rc, text = case(repo, "noise", noise)
    checks.append(("a lockfile/generated/vendored-only change: silent", rc == 0 and text == ""))

    rc, text = case(repo, "migration", lambda r: write(r, "migrations/0999_add.generated.sql",
                                                       LIMIT_LINES + 10))
    checks.append(("a migration is counted: warns and names it",
                   rc == 0 and "migrations/0999_add.generated.sql" in text))

    def move(r: Path) -> None:
        (r / "pkg").mkdir(exist_ok=True)
        git(r, "mv", "lib/big.py", "pkg/big.py")
    rc, text = case(repo, "pure-move", move)
    checks.append(("a pure rename alone: silent", rc == 0 and text == ""))

    def move_and_edit(r: Path) -> None:
        move(r)
        write(r, "lib/other.py", 21, "other")
    rc, text = case(repo, "mix", move_and_edit)
    checks.append(("a near-pure rename plus a separate edit: prints the mix line",
                   rc == 0 and "mixes moves with edits; consider splitting" in text))

    def migrations_into_vendor(r: Path) -> None:
        for i in range(LIMIT_FILES + 1):
            git(r, "mv", f"migrations/{i:04d}_m.sql", f"vendor/{i:04d}_m.sql")
    rc, text = case(repo, "migrations-to-vendor", migrations_into_vendor)
    checks.append(("migrations renamed into an excluded directory still count (either side)",
                   rc == 0 and f"{LIMIT_FILES + 1} files" in text))

    def vendor_to_vendor(r: Path) -> None:
        (r / "third_party" / "vendor").mkdir(parents=True, exist_ok=True)
        git(r, "mv", "vendor/keep.js", "third_party/vendor/keep.js")
        write(r, "lib/other.py", 21, "other")
    rc, text = case(repo, "vendor-to-vendor", vendor_to_vendor)
    checks.append(("a rename with BOTH sides excluded is left out (no mix line)",
                   rc == 0 and text == ""))

    def move_and_binary_edit(r: Path) -> None:
        move(r)
        (r / "lib" / "bin.dat").write_bytes(b"\0after")
    rc, text = case(repo, "mix-binary-edit", move_and_binary_edit)
    checks.append(("a near-pure rename plus a binary content edit: prints the mix line",
                   rc == 0 and "mixes moves with edits; consider splitting" in text))

    def move_and_binary_add(r: Path) -> None:
        move(r)
        (r / "lib" / "new.bin").write_bytes(b"\0new")
    rc, text = case(repo, "mix-binary-add", move_and_binary_add)
    checks.append(("a near-pure rename plus a binary addition: prints the mix line",
                   rc == 0 and "mixes moves with edits; consider splitting" in text))

    def move_and_binary_mode_only(r: Path) -> None:
        move(r)
        os.chmod(r / "lib" / "bin.dat", 0o755)
    rc, text = case(repo, "binary-mode-only", move_and_binary_mode_only)
    checks.append(("a near-pure rename plus a chmod-only change on a BINARY file: silent",
                   rc == 0 and text == ""))

    def move_and_mode_only(r: Path) -> None:
        move(r)
        os.chmod(r / "lib" / "tool.sh", 0o755)
    rc, text = case(repo, "mode-only", move_and_mode_only)
    checks.append(("a near-pure rename plus a mode-only change: silent (mode is not content)",
                   rc == 0 and text == ""))

    rc, text = run(repo, "no-such-ref...main")
    checks.append(("an unresolvable range: exit 0, nothing printed", rc == 0 and text == ""))

hook = (HERE / "githooks" / "pre-push").read_text()
hook_lines = hook.splitlines()
call = next((i for i, line in enumerate(hook_lines) if "pr-size-check.py" in line
             and "origin/main...HEAD" in line), None)
checks.append(("the pre-push hook invokes the note on origin/main...HEAD and discards its status",
               call is not None and hook_lines[call].rstrip().endswith("|| true")))
top_level_exit = next((i for i, line in enumerate(hook_lines) if re.match(r"exit\b", line)), None)
checks.append(("no top-level exit precedes the note, so it is reachable",
               call is not None and top_level_exit is not None and top_level_exit > call))
block = "\n".join(hook_lines[max(0, (call or 0) - 8):(call or 0) + 1])
checks.append(("the note runs with Git's hook variables stripped",
               "-u GIT_DIR" in block and "-u GIT_INDEX_FILE" in block and "-u GIT_WORK_TREE" in block))

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
print(f"pr-size-check-selftest: {len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
