#!/usr/bin/env python3
"""Selftest for hooks/session-brief.py's nightly_verdict().

WHY THIS EXISTS. On 2026-08-14 the brief opened the session with five named
nightly failures — two golden-workflow assertions, the golden workflow suite,
schema snapshot drift, and the vault drift watch. Only the LAST of those was
still failing; the other four had been fixed hours earlier and their runs had
already gone green on the parts that mattered. The accumulator was deduped at
each run boundary but never reset, so the line reported the union of every
failure since the last fully green chain and read as one catastrophic run.

That is the same disease this function was written to cure. Its own docstring
says a line that prints every session is a line nobody reads; a line that names
four already-fixed failures is worse, because the one real failure hides among
them, and the session pays to rediscover which.

SECOND INCIDENT, 2026-08-21, same disease in loose_work(). The brief opened a
session with "1 tracked file(s) in ~/carr-system have sat uncommitted for 324h".
The only dirty tracked path was the git submodule tools/dictation-rig/vendor/quill,
whose actual edit was 29 hours old. getmtime() had been called straight on the
path git status printed, and for a gitlink that path is a DIRECTORY: a directory's
mtime does not move when a file nested inside it is edited, so the number was
frozen at the submodule's checkout date and could only ever climb. Worse, the
submodule was dirty by DESIGN — its diff is byte-identical to the committed patch
tools/dictation-rig/patches/0001-menubar-visible-recording.patch, which the build
applies — so the line was announcing a permanent steady state as if it were
someone's stranded work, which is precisely the "line nobody reads" failure.

Run: .venv/bin/python ops/session-brief-selftest.py
"""
import importlib.util
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import json
import re
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = os.path.join(os.path.dirname(HERE), "hooks", "session-brief.py")

# Keep every fixture below one owned root so a normal or failing interpreter
# exit cannot leave repo-local TMPDIR litter behind.
_ORIGINAL_MKDTEMP = tempfile.mkdtemp
_ORIGINAL_MKSTEMP = tempfile.mkstemp
_FIXTURE_ROOT = _ORIGINAL_MKDTEMP(prefix="session-brief-selftest-")
atexit.register(shutil.rmtree, _FIXTURE_ROOT, ignore_errors=True)
# Some platform git helpers create their own directory directly under TMPDIR;
# point that boundary at the owned root too, so those helpers are collected.
os.environ["TMPDIR"] = _FIXTURE_ROOT
os.environ["TMP"] = _FIXTURE_ROOT
os.environ["TEMP"] = _FIXTURE_ROOT


def _fixture_mkdtemp(suffix=None, prefix=None, dir=None):
    return _ORIGINAL_MKDTEMP(suffix=suffix, prefix=prefix,
                             dir=_FIXTURE_ROOT)


def _fixture_mkstemp(suffix=None, prefix=None, dir=None, text=False):
    return _ORIGINAL_MKSTEMP(suffix=suffix, prefix=prefix,
                             dir=_FIXTURE_ROOT, text=text)


tempfile.mkdtemp = _fixture_mkdtemp
tempfile.mkstemp = _fixture_mkstemp

# The one scrubber (ops/git_env.py). git hands every hook a GIT_DIR pointing
# at the repository that invoked it, and on 2026-08-14 that leaked a fixture
# commit onto live main — these fixtures commit into throwaway repos (parent
# AND a nested submodule) and must not be captured by an inherited GIT_DIR.
sys.path.insert(0, HERE)
from git_env import fixture_env  # noqa: E402

spec = importlib.util.spec_from_file_location("session_brief", BRIEF)
assert spec is not None and spec.loader is not None, f"cannot load {BRIEF}"
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

REPO = Path(HERE).parent

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def write_log(text):
    fd, path = tempfile.mkstemp(suffix=".log")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


@case("Claude and connector-held Codex receive the same actionable pack protocol")
def _(assert_):
    rail = getattr(sb, "PACK_DELIVERY_RAIL", "")
    assert_("canonical pack names" in rail and "packs_not_found" in rail,
            f"session brief lacks exact-name refusal protocol: {rail!r}")
    assert_("call standing-context again" in rail and "shadow" in rail.lower(),
            f"session brief lacks drift remedy/shadow boundary: {rail!r}")

    mcp = (REPO / "mcp-server/src/mcp.js").read_text(encoding="utf-8")
    match = re.search(r"const RULE_DELIVERY_RAIL = `([^`]*)`;", mcp)
    assert_(match is not None and match.group(1) == rail,
            "MCP initialize rail does not exactly match the Claude session brief")
    assert_("+ RULE_DELIVERY_RAIL" in mcp,
            "shared connector instructions do not install the rule-delivery rail")

    commands = []
    for relative in ("claude-tree/settings/carr-ai-project.settings.json",
                     "claude-tree/settings/my-drive-root.settings.json"):
        document = json.loads((REPO / relative).read_text(encoding="utf-8"))
        commands.append([
            hook["command"]
            for group in document["hooks"]["SessionStart"]
            for hook in group["hooks"]
            if "session-brief.py" in hook.get("command", "")
        ])
    assert_(commands[0] == commands[1] and len(commands[0]) == 1,
            f"Claude session-brief install differs across managed roots: {commands!r}")


# One failing run, then a second run that failed on ONE different step. This is
# the real 2026-08-14 shape, reduced.
TWO_RUNS = """\
2026-08-14T13:08:00Z  START golden workflow suite
2026-08-14T13:08:17Z  FAIL  golden workflow suite (read verbs) (exit 1)
2026-08-14T13:08:23Z  ===== nightly chain FINISHED WITH FAILURES — see above =====
2026-08-14T13:36:34Z  FAIL  vault drift watch (check, first) (exit 2)
2026-08-14T13:39:27Z  START golden workflow suite
2026-08-14T13:43:27Z  OK    golden workflow suite (read verbs)
2026-08-14T13:43:34Z  ===== nightly chain FINISHED WITH FAILURES — see above =====
"""


@case("the verdict names only the LAST run's failures, not every failure since green")
def _(assert_):
    path = write_log(TWO_RUNS)
    out = sb.nightly_verdict(log=path)
    assert_("vault drift watch" in out,
            f"the last run's real failure must be named: {out!r}")
    assert_("golden workflow suite" not in out,
            f"a step that failed in an EARLIER run and passed in the last one must "
            f"not be reported as current: {out!r}")


@case("a clean final run says nothing at all")
def _(assert_):
    path = write_log(TWO_RUNS + "2026-08-14T14:10:00Z  ===== nightly chain OK =====\n")
    out = sb.nightly_verdict(log=path)
    assert_(out == "", f"a green chain must print nothing, got {out!r}")


@case("repeats within one run are still deduped")
def _(assert_):
    path = write_log(
        "2026-08-14T01:00:00Z  FAIL  vault drift watch (check, first) (exit 2)\n"
        "2026-08-14T01:05:00Z  FAIL  vault drift watch (check, first) (exit 2)\n"
        "2026-08-14T01:09:00Z  ===== nightly chain FINISHED WITH FAILURES =====\n")
    out = sb.nightly_verdict(log=path)
    assert_(out.count("vault drift watch") == 1,
            f"one step failing twice in a run should be named once: {out!r}")


@case("failures from a run still in flight are not attributed to the last finished one")
def _(assert_):
    # A chain that is running RIGHT NOW has emitted FAIL lines past the last
    # boundary marker. Those belong to a run that has not finished, so the
    # verdict still describes the last COMPLETED run.
    path = write_log(TWO_RUNS + "2026-08-14T14:20:00Z  FAIL  exports (7 targets) (exit 1)\n")
    out = sb.nightly_verdict(log=path)
    assert_("exports" not in out,
            f"an unfinished run's failure must not join the last verdict: {out!r}")


@case("a missing log is silent rather than fatal — the brief must never fail on this")
def _(assert_):
    out = sb.nightly_verdict(log="/nonexistent/nightly.log")
    assert_(out == "", f"a missing log should produce no line, got {out!r}")


# ── loose_work(): a tracked path can be a DIRECTORY (2026-08-21) ─────────────

def _git(cwd, *args):
    return subprocess.run(("git",) + args, cwd=cwd, capture_output=True,
                          text=True, timeout=30, env=fixture_env())


def _must(res, what):
    """Raise with git's own stderr when a setup step fails.

    These cases used to swallow setup failures and quietly pass. That hid a
    real one: a bare CI runner has no global git identity, so the commit INSIDE
    the submodule failed, the gitlink never moved, and the assertion failed on
    the runner while passing on a laptop that had a global user.email. A setup
    step that cannot run must say which one and why, not skip.
    """
    if res.returncode != 0:
        raise AssertionError(
            f"setup step failed ({what}): rc={res.returncode} "
            f"stderr={res.stderr.strip()!r} stdout={res.stdout.strip()!r}")
    return res


def _ident(path):
    """Give a repo its own identity — never inherited from the machine.

    Applied to the submodule work tree too, not just the outer repos: a commit
    made inside vendor/dep runs in ITS repo, with ITS config, and a CI runner
    has no global fallback to borrow.
    """
    _git(path, "config", "user.email", "selftest@carr.local")
    _git(path, "config", "user.name", "selftest")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "protocol.file.allow", "always")
    return path


def _repo(path):
    """A throwaway git repo with an identity, so commits work on any machine."""
    os.makedirs(path, exist_ok=True)
    _must(_git(path, "init", "-q", "-b", "main"), f"git init {path}")
    return _ident(path)


def _seed_parent_with_submodule(base):
    """upstream repo + parent repo with it vendored at vendor/dep.

    Returns (parent, submodule_path), or (None, None) when this git cannot add
    a file-path submodule at all — the one environment difference worth
    tolerating, and it is reported rather than silently passed.
    """
    up = _repo(os.path.join(base, "upstream"))
    with open(os.path.join(up, "vendored.txt"), "w") as fh:
        fh.write("original\n")
    _must(_git(up, "add", "-A"), "stage upstream")
    _must(_git(up, "commit", "-qm", "seed"), "commit upstream")

    parent = _repo(os.path.join(base, "parent"))
    with open(os.path.join(parent, "README"), "w") as fh:
        fh.write("parent\n")
    _must(_git(parent, "add", "-A"), "stage parent")
    _must(_git(parent, "commit", "-qm", "seed"), "commit parent")

    add = _git(parent, "-c", "protocol.file.allow=always", "submodule", "add",
               "-q", up, "vendor/dep")
    if add.returncode != 0:
        return None, None
    _must(_git(parent, "commit", "-qm", "vendor dep"), "commit gitlink")
    sub = os.path.join(parent, "vendor", "dep")
    _ident(sub)                      # the submodule commits with its OWN config
    return parent, sub


@case("newest mtime of a DIRECTORY comes from the files inside, not the directory itself")
def _(assert_):
    root = tempfile.mkdtemp()
    inner = os.path.join(root, "sub")
    os.makedirs(inner)
    deep = os.path.join(inner, "nested")
    os.makedirs(deep)
    f = os.path.join(deep, "edited.txt")
    with open(f, "w") as fh:
        fh.write("x")
    # The 324h shape: the directory looks ancient, the file inside is recent.
    old = 1_000_000.0
    new = 2_000_000.0
    os.utime(f, (new, new))
    os.utime(deep, (old, old))
    os.utime(inner, (old, old))
    got = sb._newest_mtime(inner)
    assert_(got == new,
            f"a directory's age must come from its newest file ({new}), got {got}")
    assert_(got != os.path.getmtime(inner),
            "the directory's own mtime must not be what is reported")


@case("newest mtime of a plain FILE is still just that file's mtime")
def _(assert_):
    fd, f = tempfile.mkstemp()
    os.close(fd)
    os.utime(f, (1_500_000.0, 1_500_000.0))
    got = sb._newest_mtime(f)
    assert_(got == 1_500_000.0, f"expected the file's own mtime, got {got}")


@case("a submodule that is only DIRTY INSIDE is not reported as stranded work")
def _(assert_):
    parent, sub = _seed_parent_with_submodule(tempfile.mkdtemp())
    if parent is None:
        print("      (skipped: this git cannot add a file-path submodule)")
        return

    # Dirty the submodule's WORK TREE only — the recorded commit does not move.
    # This is the applied-patch steady state the dictation rig lives in.
    with open(os.path.join(sub, "vendored.txt"), "w") as fh:
        fh.write("patched by the build\n")
    ancient = 1_000_000.0             # make the gitlink DIRECTORY look 300h+ old
    os.utime(sub, (ancient, ancient))

    porcelain = _git(parent, "status", "--porcelain").stdout
    assert_(porcelain.strip() != "",
            "precondition: plain git status should see the dirty submodule, "
            f"got {porcelain!r} — the case would prove nothing")

    out = sb.loose_work(repo=parent)
    assert_(out == "",
            f"a submodule dirty only in its work tree is the designed state and "
            f"must produce no line, got {out!r}")


@case("a submodule whose RECORDED COMMIT moved is still reported")
def _(assert_):
    parent, sub = _seed_parent_with_submodule(tempfile.mkdtemp())
    if parent is None:
        print("      (skipped: this git cannot add a file-path submodule)")
        return

    # A real second commit inside the submodule, then leave the parent's
    # gitlink pointing at the old one — a MOVED POINTER, not work-tree dirt.
    with open(os.path.join(sub, "vendored.txt"), "w") as fh:
        fh.write("v2\n")
    _must(_git(sub, "add", "-A"), "stage inside submodule")
    _must(_git(sub, "commit", "-qm", "v2"), "commit inside submodule")

    stale = 1_000_000.0               # the 12h clock must still see this as old
    for root, dirs, files in os.walk(sub):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            try:
                os.utime(os.path.join(root, name), (stale, stale))
            except OSError:
                pass
    os.utime(sub, (stale, stale))

    ignored = _git(parent, "status", "--porcelain",
                   "--ignore-submodules=dirty").stdout
    assert_(ignored.strip() != "",
            "precondition: a MOVED gitlink must survive --ignore-submodules=dirty, "
            f"got {ignored!r} — if this is empty the pointer never moved")

    out = sb.loose_work(repo=parent)
    assert_("uncommitted" in out,
            f"a moved submodule pointer IS uncommitted work and must be "
            f"reported, got {out!r}")


# ── close_before_open_brief(): the CLOSE-BEFORE-OPEN line ─────────────────
# Added 2026-08-21, generalized from built_unclosed_brief. The brief must
# begin with a hard CLOSE-BEFORE-OPEN line when any open work blocks a new
# front, and must NOT bloat when everything is closed.

@case("close_before_open_brief returns a CLOSE-BEFORE-OPEN line when evidence exists on disk")
def _(assert_):
    root = tempfile.mkdtemp()
    ops_dir = os.path.join(root, "ops")
    os.makedirs(ops_dir, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(HERE, "built_unclosed.py"), os.path.join(ops_dir, "built_unclosed.py"))
    # Create evidence files that exist on disk
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    # Monkeypatch load_live_rows to return rows with evidence that exists
    sys.path.insert(0, ops_dir)
    import built_unclosed as _bu_local
    _saved = _bu_local.load_live_rows
    _bu_local.load_live_rows = lambda: [
        {"ref": "WR-AI-006", "state": "ready",
         "evidence": ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]}
    ]
    try:
        out = sb.close_before_open_brief(repo=root)
        assert_("CLOSE-BEFORE-OPEN" in out,
                f"the brief must say CLOSE-BEFORE-OPEN when built work is unclosed: {out!r}")
        assert_("not confirmed_closed" in out,
                f"the line must name the problem: {out!r}")
        assert_("WR-AI-006" in out,
                f"the line must name the work request ref: {out!r}")
    finally:
        _bu_local.load_live_rows = _saved


@case("close_before_open_brief fires for implementation-open states")
def _(assert_):
    root = tempfile.mkdtemp()
    ops_dir = os.path.join(root, "ops")
    os.makedirs(ops_dir, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(HERE, "built_unclosed.py"), os.path.join(ops_dir, "built_unclosed.py"))
    sys.path.insert(0, ops_dir)
    import built_unclosed as _bu_local
    _saved = _bu_local.load_live_rows
    _bu_local.load_live_rows = lambda: [
        {"ref": "WR-AI-003", "state": "in_progress", "evidence": []}
    ]
    try:
        out = sb.close_before_open_brief(repo=root)
        assert_("CLOSE-BEFORE-OPEN" in out,
                f"implementation-open must fire the brief: {out!r}")
        assert_("implementation-open" in out,
                f"the line must name the stage: {out!r}")
        assert_("WR-AI-003" in out,
                f"the line must name the oldest ref: {out!r}")
    finally:
        _bu_local.load_live_rows = _saved


@case("close_before_open_brief is empty when no evidence paths exist on disk")
def _(assert_):
    root = tempfile.mkdtemp()
    ops_dir = os.path.join(root, "ops")
    os.makedirs(ops_dir, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(HERE, "built_unclosed.py"), os.path.join(ops_dir, "built_unclosed.py"))
    # Mock load_live_rows to return None so the seed fallback is used, and
    # the seed migration is not present in this temp dir either.
    sys.path.insert(0, ops_dir)
    import built_unclosed as _bu_local
    _saved = _bu_local.load_live_rows
    _bu_local.load_live_rows = lambda: None
    try:
        out = sb.close_before_open_brief(repo=root)
        assert_(out == "",
                f"empty brief when no built work is detected: {out!r}")
    finally:
        _bu_local.load_live_rows = _saved


@case("close_before_open_brief does not bloat when built_unclosed is empty")
def _(assert_):
    root = tempfile.mkdtemp()
    ops_dir = os.path.join(root, "ops")
    os.makedirs(ops_dir, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(HERE, "built_unclosed.py"), os.path.join(ops_dir, "built_unclosed.py"))
    # Create the evidence so the detector can find them, but mark all as confirmed_closed
    for p in ["ops/ai_eval.py"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    # Monkeypatch load_live_rows to return confirmed_closed rows
    original = sb.close_before_open_brief
    sys.path.insert(0, ops_dir)
    import built_unclosed as _bu_local
    _saved = _bu_local.load_live_rows
    _bu_local.load_live_rows = lambda: [
        {"ref": "WR-AI-001", "state": "confirmed_closed", "evidence": ["ops/ai_eval.py"]}
    ]
    try:
        out = sb.close_before_open_brief(repo=root)
        assert_(out == "",
                f"confirmed_closed rows must not produce a CLOSE-BEFORE-OPEN line: {out!r}")
    finally:
        _bu_local.load_live_rows = _saved


@case("close_before_open_brief fails closed when DB is unreachable and seed says 'ready'")
def _(assert_):
    root = tempfile.mkdtemp()
    ops_dir = os.path.join(root, "ops")
    os.makedirs(ops_dir, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(HERE, "built_unclosed.py"), os.path.join(ops_dir, "built_unclosed.py"))
    # No evidence files, no migration — just the detector module. Monkeypatch
    # load_live_rows to return None (DB unreachable) so the seed fallback is
    # used. With no migration present, _seed_evidence_rows returns [] and the
    # brief is empty. But when a migration IS present with state='ready' rows,
    # the brief must fail closed: report UNKNOWN state, not "".
    sys.path.insert(0, ops_dir)
    import built_unclosed as _bu_local
    _saved = _bu_local.load_live_rows
    _bu_local.load_live_rows = lambda: None
    # Create a minimal seed migration with state='ready' so the fallback has rows
    mig_dir = os.path.join(root, "migrations")
    os.makedirs(mig_dir, exist_ok=True)
    mig = os.path.join(mig_dir, "0125_ai_capability_program.sql")
    open(mig, "w").write(
        "insert into ops.work_request\n"
        "  (program_ordinal, program_key, ref, title, disposition, existing_status, state,\n"
        "   desired_outcome, acceptance_criteria, project_context, requester_actor, owner_actor)\n"
        "values\n"
        "  (1, 'test-suite-v1', 'WR-X-001', 'Test', 'build', 'absent', 'ready',\n"
        "   'outcome', 'criteria',\n"
        "   '{\"scope\":\"test\",\"evidence\":[]}'::jsonb, 'joe', 'joe');\n"
    )
    try:
        out = sb.close_before_open_brief(repo=root)
        assert_("CLOSE-BEFORE-OPEN" in out,
                f"DB down + seed 'ready' must still fire CLOSE-BEFORE-OPEN (fail closed): {out!r}")
        assert_("UNKNOWN" in out,
                f"the line must name the UNKNOWN state: {out!r}")
    finally:
        _bu_local.load_live_rows = _saved


def main():
    failures = []
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
