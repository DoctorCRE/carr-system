#!/usr/bin/env python3
"""Fixtures for ops/mechanism-doctrine-gate.py.

The gate refuses a new gate, hook or scheduled job that does not name the
doctrine section explaining it. These cases pin the four ways that goes wrong:
covering something it should not, missing something it should catch, treating an
unreachable doctrine store as a pass, and treating an unreachable store as an
EMPTY one — which would fail every declaration on a runner and get the whole
check switched off within a day.

Run: .venv/bin/python ops/mechanism-doctrine-gate-selftest.py
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
GATE = REPO / "ops" / "mechanism-doctrine-gate.py"

# The one scrubber (ops/git_env.py), not the hand-rolled author-only env dict
# this file used to build: git hands every hook a GIT_DIR pointing at the
# repository that invoked it, and on 2026-08-14 that leaked a fixture commit
# onto live main. run_in_fixture()'s throwaway repo must not be reachable
# through an inherited GIT_DIR.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

spec = importlib.util.spec_from_file_location("mdg", GATE)
assert spec is not None and spec.loader is not None, f"cannot load {GATE}"
mdg: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mdg)

passed: int = 0
failures: list[str] = []


def check(name: str, cond: object, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


print("\nops/mechanism-doctrine-gate.py — knowledge ships with the mechanism")

# ── 1. what counts as a mechanism, and what deliberately does not ───────────
for mech_path, want in (
    ("ops/control-catalog-parity-gate.py", True),
    ("hooks/weekend-quiet-gate.py", True),
    ("ops/launchd/com.carr.notes-sweep.plist", True),
    ("ops/scheduled-tasks/notes-sweep-hourly.SKILL.md", True),
    # Not mechanisms: something else calls these, and the call site explains them.
    ("lib/r2_archive.py", False),
    ("tools/ops-record.py", False),
    ("bin/nightly.sh", False),
    ("ops/ci.sh", False),
    ("migrations/0276_work_in_progress_limit.sql", False),
    # A gate's own fixtures are not a second mechanism. These three are the only
    # cases the EXEMPT pattern decides: each one WOULD match a mechanism
    # predicate on its own, so if the exemption is removed they flip. The
    # obvious spellings do not test it — a -selftest.py file already fails the
    # -gate.py predicate, so it passes with or without the exemption, which is
    # how a mutation removing EXEMPT survived the first version of this suite.
    ("ops/fixtures/probe-gate.py", False),
    ("ops/test/probe-gate.py", False),
    ("hooks/fixtures/probe.py", False),
    ("ops/control-catalog-parity-gate-selftest.py", False),
    # A SHARED MODULE IN hooks/ IS NOT A GATE. This gate first classified every
    # hooks/*.py as a session gate — where the gates live, but not what makes
    # one — and refused hooks/turn_origin.py, a detector two gates import that
    # reads no payload and refuses nothing. Both real modules are listed because
    # one is pre-existing and one was the file that exposed it, and a predicate
    # keyed on the underscore naming habit rather than on behaviour would pass
    # both while still waving through an underscore-named gate.
    ("hooks/conduct_patterns.py", False),
    ("hooks/turn_origin.py", False),
    ("mcp-server/test/decision-short-id.test.mjs", False),
):
    covered = mdg.classify(mech_path) is not None
    check(f"{'covers' if want else 'ignores':7} {mech_path}", covered == want,
          f"wanted covered={want}, got {covered}")

# ── 2. the declaration itself ───────────────────────────────────────────────
cases = (
    ("# doctrine: mechanism-doctrine-admission", "mechanism-doctrine-admission"),
    ("#doctrine: some-slug", "some-slug"),
    # A plist can only carry this inside an XML comment, so the closer must be
    # accepted. It was not, and the docstring claimed otherwise — the fixture
    # found it, reading did not.
    ("<!-- doctrine: plist-style-slug -->", "plist-style-slug"),
    ("/* doctrine: c-style-slug */", "c-style-slug"),
    # But nothing else may trail: a sentence is not a declaration.
    ("<!-- doctrine: slug and some prose -->", None),
    ("# doctrine: a", None),                          # too short to be a real slug
    ("# see the doctrine: section for detail", None),  # prose, not a declaration
    ("# doctrine: with-dots.v1", "with-dots.v1"),
    ("no declaration here at all", None),
)
for text, want_slug in cases:
    m = mdg.DECLARATION.search(text)
    slug = m.group(1) if m else None
    check(f"declaration {text[:34]!r:38} -> {want_slug}", slug == want_slug, f"got {slug}")

# ── 3. an unreachable store is NOT an empty one ─────────────────────────────
# The case that would quietly destroy this check: if a runner with no credential
# returned an empty set instead of None, every declaration would be "not in the
# store" and the gate would fail every push until somebody switched it off.
#
# The first version of this block stubbed known_slugs and then asserted the stub
# returned None — it tested itself, and a mutation making the real function
# return set() survived it untouched. This drives the REAL function.
saved_repo = mdg.REPO
try:
    with tempfile.TemporaryDirectory() as empty:
        mdg.REPO = empty                       # no run.sh here, so the store is unreachable
        got = mdg.known_slugs()
    check("an unreachable store returns None, never an empty set",
          got is None, f"got {got!r} — an empty set would fail every declaration")
finally:
    mdg.REPO = saved_repo

# The store ANSWERING with zero documents is a third state, and it must collapse
# to None as well. A successful call returning an empty index would otherwise
# yield an empty set, and every declaration on earth would be "not in the store".
# The temp-directory case above does not reach this line — it returns early on
# the missing run.sh — which is why a mutation on it survived until this case
# existed.
class _Answered:
    returncode = 0
    stdout = '{"ok": true, "documents": []}'
    stderr = ""

saved_run = mdg.subprocess.run
try:
    mdg.subprocess.run = lambda *a, **k: _Answered()
    got = mdg.known_slugs()
    check("a store answering with ZERO documents also returns None, not an empty set",
          got is None, f"got {got!r} — an empty set fails every declaration")
finally:
    mdg.subprocess.run = saved_run

# And the reachable case must never answer with an empty set either: empty and
# unreachable are the same failure wearing different clothes.
live = mdg.known_slugs()
check("a reachable store answers with real slugs, never an empty set",
      live is None or len(live) > 0, f"got {live!r}")
if live:
    check("the slug this gate declares resolves in the live store",
          "mechanism-doctrine-admission" in live,
          "the gate names a document the store does not have")
else:
    print("  skip  live slug resolution — store unreachable from here")

# ── 4. end to end, against a real repository ────────────────────────────────
def run_in_fixture(files, base_files=None):
    """Build a throwaway git repo, commit base_files, add files, run the gate."""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        env = dict(fixture_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        def git(*a):
            return subprocess.run(["git", *a], cwd=td, env=env,
                                  capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        (td / "ops").mkdir(parents=True, exist_ok=True)
        (td / "hooks").mkdir(parents=True, exist_ok=True)
        for rel, body in (base_files or {}).items():
            f = td / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(body)
        (td / "seed").write_text("seed")
        git("add", "-A"); git("commit", "-qm", "base")
        base_sha = git("rev-parse", "HEAD").stdout.strip()
        for rel, body in files.items():
            f = td / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(body)
        git("add", "-A")
        # Point the gate's REPO at the fixture, and stub the store so the case
        # is about the diff and the declaration, not about the network.
        gate_src = GATE.read_text()
        (td / "ops" / "mechanism-doctrine-gate.py").write_text(gate_src)
        p = subprocess.run([sys.executable, str(td / "ops" / "mechanism-doctrine-gate.py"),
                            "--base", base_sha],
                           cwd=td, capture_output=True, text=True, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

DECLARED = "#!/usr/bin/env python3\n# doctrine: mechanism-doctrine-admission\nx = 1\n"
UNDECLARED = "#!/usr/bin/env python3\nx = 1\n"

rc, out = run_in_fixture({"ops/new-thing-gate.py": UNDECLARED})
check("a new gate with no doctrine declaration is REFUSED", rc == 1,
      f"exit {rc}: {out[:120]}")
check("the refusal names the file and the fix",
      "ops/new-thing-gate.py" in out and "# doctrine:" in out, out[:160])

rc, out = run_in_fixture({"ops/new-thing-gate.py": DECLARED})
check("a new gate that names a real doctrine section passes", rc == 0,
      f"exit {rc}: {out[:160]}")

rc, out = run_in_fixture({"lib/helper.py": UNDECLARED, "seed2": "x"})
check("a change adding no mechanism passes untouched", rc == 0, f"exit {rc}")

# MODIFYING an existing mechanism is out of scope on purpose — a gate that
# demanded a slug on every edit would be uninstalled inside a week.
rc, out = run_in_fixture({"ops/old-thing-gate.py": UNDECLARED + "# edited\n"},
                         base_files={"ops/old-thing-gate.py": UNDECLARED})
check("EDITING an existing mechanism is not covered", rc == 0,
      f"exit {rc}: {out[:160]}")

rc, out = run_in_fixture({"ops/new-thing-gate.py": UNDECLARED}, base_files={})

# ── 5. the default base is the MERGE BASE, not the moving tip of main ───────
# A branch forked before main deleted a mechanism still carries that file.
# Measured against origin/main it reads as ADDED by the branch; measured
# against the merge base it is nothing.  Six pull requests went red on
# 2026-10-12 for exactly this (defect ca048a85).
def run_in_forked_fixture(branch_files, base=None):
    """main: base commit with a retired hook -> delete it.  feature: forked at the
    base commit, adds branch_files.  origin/main points at main's tip.  The gate
    runs on feature with the given --base (None = the gate's own default)."""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        env = dict(fixture_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        def git(*a):
            return subprocess.run(["git", *a], cwd=td, env=env, capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        (td / "hooks").mkdir(); (td / "ops").mkdir()
        (td / "hooks" / "retired-gate.py").write_text(UNDECLARED + 'if __name__ == "__main__":\n    raise SystemExit(0)\n')
        (td / "seed").write_text("seed")
        git("add", "-A"); git("commit", "-qm", "base")
        git("branch", "feature")
        git("rm", "-q", "hooks/retired-gate.py"); git("commit", "-qm", "retire the hook")
        git("update-ref", "refs/remotes/origin/main", "main")
        git("checkout", "-q", "feature")
        for rel, body in branch_files.items():
            f = td / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(body)
        git("add", "-A"); git("commit", "-qm", "feature work")
        (td / "ops" / "mechanism-doctrine-gate.py").write_text(GATE.read_text())
        argv = [sys.executable, str(td / "ops" / "mechanism-doctrine-gate.py")]
        if base:
            argv += ["--base", base]
        p = subprocess.run(argv, cwd=td, capture_output=True, text=True, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

rc, out = run_in_forked_fixture({"lib/helper.py": UNDECLARED})
check("a branch forked before main retired a hook is NOT charged with adding it (default base)",
      rc == 0, f"exit {rc}: {out[:200]}")
rc, out = run_in_forked_fixture({"lib/helper.py": UNDECLARED}, base="origin/main")
check("measured against origin/main the same branch is wrongly refused (the old default)",
      rc == 1 and "hooks/retired-gate.py" in out, f"exit {rc}: {out[:200]}")
rc, out = run_in_forked_fixture({"ops/new-thing-gate.py": UNDECLARED})
check("a branch that really adds an undeclared mechanism is still refused under the merge base",
      rc == 1 and "ops/new-thing-gate.py" in out and "retired-gate" not in out, f"exit {rc}: {out[:200]}")
# A base that cannot be resolved must exit 2, never 0 — "could not look" is not
# "nothing to see".
p = subprocess.run([sys.executable, str(GATE), "--base", "refs/heads/no-such-ref-here"],
                   cwd=REPO, capture_output=True, text=True)
check("an unreadable base exits 2, not 0", p.returncode == 2,
      f"exit {p.returncode}: {(p.stdout + p.stderr)[:140]}")

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("MECHANISM DOCTRINE GATE SELFTEST PASSED: a new mechanism arrives with "
      "the knowledge of why it exists, or it does not arrive.")
