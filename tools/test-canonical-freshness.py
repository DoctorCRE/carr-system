#!/usr/bin/env python3
"""WR-000040 AC-FRESH: the fast-forward refuses rather than destroys, the
watchdog is independent of it, and the page it sends is a call the verb accepts.

Every git case builds REAL throwaway repositories in a temporary directory and
drives the real command against them. Nothing here mocks git, because the
properties under test are properties of git's behaviour: that `merge --ff-only`
refuses a diverged branch, and that a modified tracked file survives a run.

THE TWO CASES THAT CANNOT BE STAGED BY A FIXTURE ALONE — a git command that
fails while the fetch succeeds, and an edit that arrives DURING the fetch — swap
the module's `_Git` wrapper for a subclass of it. Neither subclass fakes a
result: the failing one runs the same real git against a path that is not a
repository, so the nonzero exit is git's own, and the racing one writes a real
file into the real fixture at the real moment. What is substituted is when
things happen, never what git says about them.

The cases that matter are the negatives. A fast-forward job that works on a
clean tree proves very little; one that provably declines to touch a dirty one,
and provably leaves the edit intact afterwards, is the whole safety argument.

THE PAGING PATH IS DRIVEN END TO END against a stand-in `run.sh` planted in the
fixture. That is the half the first version of this file could not see: it
disabled paging in every case, so a `report-problem` call missing four of its
five registered fields passed every test and would have been refused by the
verb at the only moment it mattered. The stand-in records the argv it was
handed, so the assertions are about the ACTUAL payload rather than about the
code that builds it.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JANITOR = REPO_ROOT / "tools" / "repo-hygiene-janitor.py"
LIBRARY = REPO_ROOT / "lib" / "canonical_freshness.py"

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "ops"))
import canonical_freshness as freshness  # noqa: E402
from git_env import GIT_LOCATION_VARS, fixture_env  # noqa: E402

# THE AMBIENT ENVIRONMENT IS PART OF THIS FIXTURE, because git reads GIT_DIR
# before it reads the directory it was given and every git hook exports it. The
# in-process cases below call the library directly, and the library builds its
# own git calls from the ambient environment — as it must, since in production
# it is talking to a real checkout and has no fixture to scrub for. So the scrub
# happens here, once, to the environment every child git will inherit.
# ops/ci.sh already does this before invoking the suite; doing it again makes
# the file safe to run by hand, under a hook, from anywhere.
for _var in GIT_LOCATION_VARS:
    os.environ.pop(_var, None)

OK = freshness.OK
REFUSED_TRACKED_DIRT = freshness.REFUSED_TRACKED_DIRT
REFUSED_AHEAD = freshness.REFUSED_AHEAD
REFUSED_WRONG_BRANCH = freshness.REFUSED_WRONG_BRANCH
FAILED = freshness.FAILED
ALARM = freshness.ALARM

# The verb's registered contract, restated here from
# mcp-server/src/work-request-intake.js so this file fails when the payload
# drifts from it rather than when someone edits the payload builder.
REPORT_PROBLEM_FIELDS = {"idempotency_key", "situation", "title", "desired_outcome",
                         "acceptance_criteria"}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
CRITERION_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{1,63}$")


def fixture_git_env(cwd=None):
    """ops/git_env.py's fixture environment, plus this file's broader sweep.

    `fixture_env()` drops every variable git consults before the working
    directory and points config discovery at /dev/null, which is the part that
    matters and the part that must not be re-implemented here — a scrub list in
    two places is a scrub list that drifts, silently, into modifying the wrong
    repository. The extra pass keeps this file's original wider rule (any other
    GIT_* the environment carries goes too) without discarding the two variables
    `fixture_env()` deliberately SETS.
    """
    env = fixture_env()
    deliberate = {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"}
    for key in [k for k in env if k.startswith("GIT_") and k not in deliberate]:
        env.pop(key)
    if cwd is not None:
        env["HOME"] = str(cwd)
    return env


def git(cwd, *args, when=None):
    env = fixture_git_env(cwd)
    env.update({
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    })
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    return subprocess.run(["git", "-C", str(cwd), *args], env=env,
                          capture_output=True, text=True, check=True)


def run_command(repo, *args):
    """The real entrypoint, not the library: the plist will invoke this path."""
    # os.environ was scrubbed of git's location variables at import, so the
    # janitor — and the library it loads — cannot be redirected out of the
    # fixture by a hook that exported GIT_DIR.
    env = dict(os.environ)
    env["HOME"] = str(repo)
    return subprocess.run([sys.executable, str(JANITOR), "--repository", str(repo), *args],
                          env=env, capture_output=True, text=True)


# A stand-in run.sh. It records the argv it was handed to a file OUTSIDE the
# repository — writing inside would make the fixture dirty and destroy the very
# property the paging tests check — and exits the way the caller asked.
FAKE_RUN_SH = """#!/bin/sh
printf '%s\\n' "$3" > "$CARR_FAKE_PAGER_LOG"
printf '%s' "$CARR_FAKE_PAGER_STDOUT"
exit ${CARR_FAKE_PAGER_EXIT:-0}
"""


class Fixture:
    """An origin and a clone of it, both real, in a directory that goes away."""

    def __init__(self, stack):
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "--quiet", "--initial-branch=main", ".")
        (self.origin / "tracked.txt").write_text("one\n")
        git(self.origin, "add", "tracked.txt")
        # Dated two days back, so the clone's HEAD is genuinely old and the
        # staleness bar can be tested at its real value rather than at zero.
        self.old_stamp = f"{int(time.time()) - 48 * 3600} +0000"
        git(self.origin, "commit", "--quiet", "-m", "one", when=self.old_stamp)

        self.clone = self.root / "canonical"
        subprocess.run(["git", "clone", "--quiet", str(self.origin), str(self.clone)],
                       env=fixture_git_env(self.root), check=True, capture_output=True)
        self.pager_log = self.root / "pager-argv.txt"

    def advance_origin(self, n=1):
        for i in range(n):
            (self.origin / "tracked.txt").write_text(f"origin-{i}\n")
            git(self.origin, "commit", "--quiet", "-am", f"origin {i}")

    def head(self, where):
        return git(where, "rev-parse", "HEAD").stdout.strip()

    def index_state(self):
        """Everything a page could plausibly disturb: the index, the worktree,
        and the commit the branch points at."""
        return (git(self.clone, "status", "--porcelain", "--untracked-files=all").stdout,
                git(self.clone, "rev-parse", "HEAD").stdout,
                git(self.clone, "diff", "--cached", "--name-status").stdout)

    def install_fake_run_sh(self):
        """Left UNTRACKED on purpose. Committing the stand-in would put the
        clone one commit ahead of origin, which is itself an alarm condition —
        the fixture would then manufacture the very finding under test."""
        script = self.clone / "run.sh"
        script.write_text(FAKE_RUN_SH)
        script.chmod(0o755)
        return script

    def paged_payload(self):
        return json.loads(self.pager_log.read_text())


def page_env(fixture, *, exit_code=0, stdout='{"ok":true}'):
    env = dict(os.environ)
    env.update({"HOME": str(fixture.clone),
                "CARR_FAKE_PAGER_LOG": str(fixture.pager_log),
                "CARR_FAKE_PAGER_EXIT": str(exit_code),
                "CARR_FAKE_PAGER_STDOUT": stdout})
    return env


def run_watchdog_with_pager(fixture, *args, exit_code=0, stdout='{"ok":true}'):
    return subprocess.run([sys.executable, str(JANITOR), "--repository", str(fixture.clone),
                           "--canonical-freshness", "watchdog", *args],
                          env=page_env(fixture, exit_code=exit_code, stdout=stdout),
                          capture_output=True, text=True)


@contextlib.contextmanager
def git_wrapper(cls):
    """Run the library with a different `_Git` subclass for the duration.

    Deliberately not a mocking library: `cls` must be a real subclass that still
    shells out to real git, so every assertion downstream is still an assertion
    about git's behaviour.
    """
    assert issubclass(cls, freshness._Git)
    original = freshness._Git
    freshness._Git = cls
    try:
        yield
    finally:
        freshness._Git = original


class FailingObservationGit(freshness._Git):
    """Real git, really failing, for the three observation commands.

    `status`, `rev-list` and `log` are run against a path that is not a
    repository, so they exit nonzero for git's own reasons. `fetch` is left
    alone and succeeds against the real fixture — which is precisely the shape
    the false green needed: the observations fail, the fetch does not, and the
    first revision of this module read the failures as zero dirt, an unknown
    count and age zero, then returned OK.
    """

    FAILING = ("status", "rev-list", "log")

    def __call__(self, *args):
        if args and args[0] in self.FAILING:
            return subprocess.run(
                ["git", "-C", str(self.repository / "not-a-repository"), *args],
                env=fixture_git_env(self.repository), capture_output=True, text=True)
        return super().__call__(*args)


class EditDuringFetchGit(freshness._Git):
    """Somebody starts editing canonical while the fetch is in flight.

    The edit is a real write to the real fixture, made at the real moment the
    fast-forward is waiting on the network. A guard sampled only before the
    fetch cannot see it, and `merge --ff-only` would overwrite it.
    """

    def __call__(self, *args):
        if args and args[0] == "fetch":
            (self.repository / "tracked.txt").write_text(
                "somebody started editing during the fetch\n")
        return super().__call__(*args)


class SubmoduleFixture(Fixture):
    """The base fixture plus a real submodule at vendor/sub, cloned recursively."""

    SUB = "vendor/sub"

    def __init__(self, stack):
        super().__init__(stack)
        self.sub_origin = self.root / "sub-origin"
        self.sub_origin.mkdir()
        git(self.sub_origin, "init", "--quiet", "--initial-branch=main", ".")
        (self.sub_origin / "ui.swift").write_text("upstream\n")
        git(self.sub_origin, "add", "ui.swift")
        git(self.sub_origin, "commit", "--quiet", "-m", "sub one")
        git(self.origin, "-c", "protocol.file.allow=always", "submodule", "add",
            "--quiet", str(self.sub_origin), self.SUB)
        git(self.origin, "commit", "--quiet", "-m", "add submodule")
        git(self.clone, "pull", "--quiet", "--ff-only")
        git(self.clone, "-c", "protocol.file.allow=always", "submodule", "update",
            "--quiet", "--init")

    def patch_submodule_content(self):
        (self.clone / self.SUB / "ui.swift").write_text("patched by the build\n")

    def move_submodule_pointer_in_origin(self):
        (self.sub_origin / "ui.swift").write_text("upstream two\n")
        git(self.sub_origin, "commit", "--quiet", "-am", "sub two")
        git(self.origin / self.SUB, "pull", "--quiet", "--ff-only")
        git(self.origin, "commit", "--quiet", "-am", "bump submodule")


def library_fast_forward(repo):
    out, err = io.StringIO(), io.StringIO()
    code = freshness.fast_forward(repo, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class AcceptedSubmoduleDirtTests(unittest.TestCase):
    """ACCEPTED_DIRTY_SUBMODULES: content-only dirt in a NAMED submodule passes,
    and everything outside that exact acceptance still refuses."""

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.fx = SubmoduleFixture(self.stack)
        self.fx.patch_submodule_content()
        saved = freshness.ACCEPTED_DIRTY_SUBMODULES
        self.addCleanup(setattr, freshness, "ACCEPTED_DIRTY_SUBMODULES", saved)

    def test_named_submodule_content_dirt_fast_forwards_and_keeps_the_patch(self):
        freshness.ACCEPTED_DIRTY_SUBMODULES = (SubmoduleFixture.SUB,)
        self.fx.advance_origin(1)
        code, out, err = library_fast_forward(self.fx.clone)
        self.assertEqual(code, OK, out + err)
        self.assertIn(f"accepted by name: {SubmoduleFixture.SUB}", out)
        self.assertEqual(self.fx.head(self.fx.clone), self.fx.head(self.fx.origin))
        self.assertEqual((self.fx.clone / SubmoduleFixture.SUB / "ui.swift").read_text(),
                         "patched by the build\n")

    def test_the_same_dirt_in_an_unnamed_submodule_still_refuses(self):
        freshness.ACCEPTED_DIRTY_SUBMODULES = ("vendor/other",)
        self.fx.advance_origin(1)
        before = self.fx.head(self.fx.clone)
        code, out, err = library_fast_forward(self.fx.clone)
        self.assertEqual(code, REFUSED_TRACKED_DIRT, out + err)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_a_merge_that_moves_the_named_submodule_pointer_refuses(self):
        freshness.ACCEPTED_DIRTY_SUBMODULES = (SubmoduleFixture.SUB,)
        self.fx.move_submodule_pointer_in_origin()
        before = self.fx.head(self.fx.clone)
        code, out, err = library_fast_forward(self.fx.clone)
        self.assertEqual(code, REFUSED_TRACKED_DIRT, out + err)
        self.assertIn("moves the recorded commit", err)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_ordinary_file_dirt_beside_an_accepted_submodule_still_refuses(self):
        freshness.ACCEPTED_DIRTY_SUBMODULES = (SubmoduleFixture.SUB,)
        self.fx.advance_origin(1)
        (self.fx.clone / "tracked.txt").write_text("somebody's edit\n")
        code, out, err = library_fast_forward(self.fx.clone)
        self.assertEqual(code, REFUSED_TRACKED_DIRT, out + err)
        self.assertEqual((self.fx.clone / "tracked.txt").read_text(), "somebody's edit\n")


class CanonicalFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.fx = Fixture(self.stack)

    # -- the fast-forward -----------------------------------------------------

    def test_current_tree_is_left_alone(self):
        before = self.fx.head(self.fx.clone)
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertIn("already current", result.stdout)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_a_behind_tree_fast_forwards(self):
        self.fx.advance_origin(3)
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), self.fx.head(self.fx.origin))

    def test_tracked_dirt_refuses_AND_the_edit_survives(self):
        """The case the whole command exists for."""
        self.fx.advance_origin(2)
        edit = self.fx.clone / "tracked.txt"
        edit.write_text("somebody was working on this\n")
        before = self.fx.head(self.fx.clone)

        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")

        self.assertEqual(result.returncode, REFUSED_TRACKED_DIRT, result.stdout + result.stderr)
        self.assertIn("REFUSED", result.stderr)
        # Both halves matter: it did not move the branch, AND it did not eat the
        # edit to be able to move it.
        self.assertEqual(self.fx.head(self.fx.clone), before)
        self.assertEqual(edit.read_text(), "somebody was working on this\n")

    def test_untracked_paths_do_not_block_the_fast_forward(self):
        """AC-CLEAN routes untracked paths to Joe; blocking on them means never running."""
        self.fx.advance_origin(1)
        (self.fx.clone / "debris.txt").write_text("awaiting a ruling\n")
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), self.fx.head(self.fx.origin))
        self.assertTrue((self.fx.clone / "debris.txt").exists())

    def test_a_tree_ahead_of_origin_refuses_rather_than_rewriting(self):
        (self.fx.clone / "local.txt").write_text("local commit\n")
        git(self.fx.clone, "add", "local.txt")
        git(self.fx.clone, "commit", "--quiet", "-m", "local")
        before = self.fx.head(self.fx.clone)

        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")

        self.assertEqual(result.returncode, REFUSED_AHEAD, result.stdout + result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_dry_run_moves_nothing(self):
        self.fx.advance_origin(2)
        before = self.fx.head(self.fx.clone)
        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward", "--dry-run")
        self.assertEqual(result.returncode, OK, result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_fast_forward_refuses_a_branch_that_is_not_main(self):
        """AC-FRESH is about main, and a feature branch is the dangerous case.

        A clean feature branch that is an ancestor of origin/main fast-forwards
        SUCCESSFULLY. Nothing errors, the job reports OK, somebody's branch has
        silently been dragged onto main's tip — and main itself is exactly as
        stale as it was before the job ran.
        """
        self.fx.advance_origin(2)
        git(self.fx.clone, "checkout", "--quiet", "-b", "feature-branch")
        before = self.fx.head(self.fx.clone)

        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")

        self.assertEqual(result.returncode, REFUSED_WRONG_BRANCH,
                         result.stdout + result.stderr)
        self.assertIn("not main", result.stderr)
        # Both halves: the feature branch did not move, and neither did main.
        self.assertEqual(self.fx.head(self.fx.clone), before)
        self.assertEqual(
            git(self.fx.clone, "rev-parse", "main").stdout.strip(), before)
        self.assertNotEqual(before, self.fx.head(self.fx.origin))

    def test_fast_forward_refuses_a_detached_head(self):
        """The boundary case of "exactly main": a detached HEAD reads as the
        branch name "HEAD", which is not main, and needs no special case."""
        self.fx.advance_origin(1)
        before = self.fx.head(self.fx.clone)
        git(self.fx.clone, "checkout", "--quiet", "--detach", before)

        result = run_command(self.fx.clone, "--canonical-freshness", "fast-forward")

        self.assertEqual(result.returncode, REFUSED_WRONG_BRANCH,
                         result.stdout + result.stderr)
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_a_failed_observation_never_reads_as_a_clean_tree(self):
        """The fast-forward half of the false-green defect.

        `git status` fails; the fetch would succeed. A default that turned the
        failure into an empty string made this look like a clean tree, and the
        job went on to move the branch on the strength of a measurement it never
        took. It has to FAIL instead: not knowing whether the tree holds
        somebody's edit is not the same as knowing it does not.
        """
        self.fx.advance_origin(2)
        before = self.fx.head(self.fx.clone)
        out, err = io.StringIO(), io.StringIO()

        with git_wrapper(FailingObservationGit):
            code = freshness.fast_forward(self.fx.clone, out=out, err=err)

        self.assertEqual(code, FAILED, out.getvalue() + err.getvalue())
        self.assertIn("could not read `git status`", err.getvalue())
        self.assertEqual(self.fx.head(self.fx.clone), before)

    def test_an_edit_arriving_during_the_fetch_still_refuses(self):
        """The clean-tree guard is a measurement, not a prediction.

        The tree is clean when the job looks, and somebody starts editing while
        the fetch is in flight. A guard sampled only before the fetch cannot see
        that, and `merge --ff-only` would overwrite the edit — the exact
        destruction this command exists to refuse.
        """
        self.fx.advance_origin(2)
        before = self.fx.head(self.fx.clone)
        out, err = io.StringIO(), io.StringIO()

        with git_wrapper(EditDuringFetchGit):
            code = freshness.fast_forward(self.fx.clone, out=out, err=err)

        self.assertEqual(code, REFUSED_TRACKED_DIRT, out.getvalue() + err.getvalue())
        self.assertIn("before merge", err.getvalue())
        self.assertEqual(self.fx.head(self.fx.clone), before)
        self.assertEqual((self.fx.clone / "tracked.txt").read_text(),
                         "somebody started editing during the fetch\n")

    # -- the watchdog ---------------------------------------------------------

    def test_watchdog_is_quiet_on_a_clean_current_tree(self):
        """Old HEAD, but NOT behind origin: age alone is never an alarm."""
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog",
                             "--no-page", "--max-age-hours", "24")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertIn("head_age_hours=48", result.stdout)

    def test_watchdog_alarms_on_tracked_dirt(self):
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog", "--no-page")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("tracked path(s) modified", result.stderr)

    def test_watchdog_reports_untracked_paths_without_alarming(self):
        """The named distinction: counted and listed, never a page."""
        (self.fx.clone / "debris.txt").write_text("awaiting a ruling\n")
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog", "--no-page")
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertIn("debris.txt", result.stdout)
        self.assertIn("untracked=1", result.stdout)

    def test_watchdog_alarms_when_canonical_is_ahead(self):
        (self.fx.clone / "local.txt").write_text("local\n")
        git(self.fx.clone, "add", "local.txt")
        git(self.fx.clone, "commit", "--quiet", "-m", "local")
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog", "--no-page")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("ahead of origin/main", result.stderr)

    def test_watchdog_alarms_on_a_stale_head_past_the_bar(self):
        """A 48h-old HEAD that is behind origin is past the one-day bar.

        The bar is exclusive on purpose: a HEAD exactly at the limit is AT it,
        not past it, so a daily job that lands a minute late does not page.
        """
        self.fx.advance_origin(1)
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog",
                             "--no-page", "--max-age-hours", "24")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("behind origin/main", result.stderr)

    def test_watchdog_alarms_when_canonical_is_not_on_main(self):
        """A checkout parked on a feature branch is not a fresh canonical, and
        the fast-forward cannot run against it — so the watchdog has to say so
        rather than measure main's freshness from somewhere that is not main."""
        git(self.fx.clone, "checkout", "--quiet", "-b", "feature-branch")
        result = run_command(self.fx.clone, "--canonical-freshness", "watchdog", "--no-page")
        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("not main", result.stderr)
        self.assertIn("branch=feature-branch", result.stdout)

    def test_watchdog_pages_when_it_could_not_observe_the_repository(self):
        """The watchdog half of the false-green defect, and the worse half.

        `status`, `rev-list` and `log` all fail; the FETCH SUCCEEDS, so the one
        condition that used to alarm on an unreadable repository does not fire.
        With failures rendered as defaults the watchdog saw zero dirt, an
        unknown count and age zero, found nothing to say, and returned OK about
        a repository it had been unable to look at at all.

        The page is captured through the injected pager so the assertion is
        about the payload the verb would receive, not about the log line.
        """
        paged = []
        out, err = io.StringIO(), io.StringIO()

        def capture(payload):
            paged.append(payload)
            return True, "captured"

        with git_wrapper(FailingObservationGit):
            code = freshness.watchdog(self.fx.clone, out=out, err=err, pager=capture)

        self.assertEqual(code, ALARM, out.getvalue() + err.getvalue())
        self.assertIn("UNKNOWN", err.getvalue())
        self.assertIn("`git status --porcelain`", err.getvalue())
        self.assertEqual(len(paged), 1)
        self.assertEqual(set(paged[0]), REPORT_PROBLEM_FIELDS)
        self.assertIn("unobserved", paged[0]["title"])
        # The absent measurements are SPELLED as absent in the durable record,
        # never as zeroes somebody could act on.
        self.assertIn("tracked_modified=unobserved", paged[0]["situation"])
        self.assertIn("behind=unobserved", paged[0]["situation"])
        self.assertIn("head_age_hours=unobserved", paged[0]["situation"])

    def test_watchdog_does_not_read_the_fast_forward_job(self):
        """Independence, checked as a property of the source rather than a claim.

        Sharing a module with the job it watches is fine; CALLING it is not. If
        the watchdog ever learns to ask the fast-forward how it did, it stops
        being able to alarm about that job never having run at all.
        """
        import inspect
        body = "\n".join(line for line in inspect.getsource(freshness.watchdog).splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("fast_forward", body)
        self.assertNotIn("canonical-fast-forward.log", body)

    # -- the page -------------------------------------------------------------

    def test_the_page_sends_the_complete_registered_payload(self):
        """The defect this file previously could not see.

        `report-problem` validates an EXACT key set — idempotency_key,
        situation, title, desired_outcome, acceptance_criteria — and refuses
        anything short of it with invalid_report_problem. A page that sends only
        `situation` is not a durable record, it is a rejected call, and the
        watchdog would report it as having paged.
        """
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        result = run_watchdog_with_pager(self.fx)

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        payload = self.fx.paged_payload()
        self.assertEqual(set(payload), REPORT_PROBLEM_FIELDS)
        self.assertRegex(payload["idempotency_key"], UUID_RE)
        self.assertTrue(0 < len(payload["situation"]) <= 1000)
        self.assertTrue(0 < len(payload["title"]) <= 200)
        self.assertTrue(0 < len(payload["desired_outcome"]) <= 2000)
        self.assertTrue(1 <= len(payload["acceptance_criteria"]) <= 12)
        ids = set()
        for criterion in payload["acceptance_criteria"]:
            self.assertEqual(set(criterion), {"id", "text"})
            self.assertRegex(criterion["id"], CRITERION_ID_RE)
            self.assertTrue(0 < len(criterion["text"]) <= 500)
            ids.add(criterion["id"])
        self.assertEqual(len(ids), len(payload["acceptance_criteria"]))
        # The measured facts reach the record, not just the word "alarm".
        self.assertIn("tracked_modified=1", payload["situation"])
        self.assertIn("tracked-dirt", payload["title"])

    def test_each_run_mints_its_own_idempotency_key(self):
        """Two runs that see the same dirty tree are two observations. Reusing a
        key would silently collapse the second into the first."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        run_watchdog_with_pager(self.fx)
        first = self.fx.paged_payload()["idempotency_key"]
        run_watchdog_with_pager(self.fx)
        second = self.fx.paged_payload()["idempotency_key"]

        self.assertNotEqual(first, second)

    def test_a_rejected_page_is_reported_and_never_silences_the_exit(self):
        """Route 1 failing must not suppress route 2."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        result = run_watchdog_with_pager(self.fx, exit_code=1,
                                         stdout='{"error":"invalid_report_problem"}')

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("did not land", result.stderr)
        self.assertNotIn("paged report-problem", result.stdout)

    def test_a_zero_exit_carrying_an_error_body_is_a_rejection(self):
        """The verb reports a refusal inside its payload, so reading only the
        exit status would call a rejection a successful page."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")

        result = run_watchdog_with_pager(self.fx, exit_code=0,
                                         stdout='{"error":"invalid_report_problem"}')

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertIn("verb refused: invalid_report_problem", result.stderr)

    def test_paging_leaves_the_worktree_and_the_index_untouched(self):
        """An alarm channel that dirties the tree it is alarming about would
        manufacture the next alarm."""
        self.fx.install_fake_run_sh()
        (self.fx.clone / "tracked.txt").write_text("edited in canonical\n")
        before = self.fx.index_state()

        result = run_watchdog_with_pager(self.fx)

        self.assertEqual(result.returncode, ALARM, result.stdout + result.stderr)
        self.assertEqual(self.fx.index_state(), before)

    def test_the_page_never_fires_when_there_is_nothing_to_page_about(self):
        self.fx.install_fake_run_sh()
        result = run_watchdog_with_pager(self.fx)
        self.assertEqual(result.returncode, OK, result.stdout + result.stderr)
        self.assertFalse(self.fx.pager_log.exists())

    # -- the shape ------------------------------------------------------------

    def test_the_library_is_not_a_scac_ingress(self):
        """The reason the machinery lives in lib/ at all.

        ops/scac-mutation-inventory.mjs counts a tracked .py as a script
        entrypoint when it carries a shebang or an `if __name__ == "__main__":`
        block, and the sealed inventory's review overlay cannot add a row. This
        module must stay reachable only through an entrypoint that already holds
        one.
        """
        source = LIBRARY.read_text()
        self.assertFalse(source.startswith("#!"), "a shebang would register a new ingress")
        # The classifier's own regex, copied from isScriptEntrypoint. It is
        # UNANCHORED, so a guard quoted inside a docstring registers the file
        # just as surely as a real one — which is exactly how this assertion
        # first fired.
        guard = re.compile(r"""if\s+__name__\s*==\s*["']__main__["']\s*:""")
        self.assertIsNone(guard.search(source),
                          "a __main__ guard anywhere in the source registers a new ingress")
        mode = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--stage",
                               "lib/canonical_freshness.py"],
                              capture_output=True, text=True, check=True).stdout.split()
        self.assertTrue(mode, "lib/canonical_freshness.py is not tracked")
        self.assertEqual(mode[0], "100644", "an executable bit would register a new ingress")

    def test_both_agents_are_declared_and_carry_the_ruled_cadence(self):
        """The inverse of the assertion this file carried until the activation.

        PR 991 withheld both plists because a new ops/launchd/*.plist is a new
        launchd-workflow row in a sealed inventory `current_source_review`
        cannot add to, and because no cadence had been ruled. Joe ruled the
        cadence on 2026-09-11 (decision fa8ae14b-e6fe-4e1b-ae98-0b6eb5a255f8):
        the fast-forward daily, the watchdog hourly, paging on TRACKED dirt
        only. So the definitions now have to exist, and each one has to carry
        the cadence it was ruled rather than whatever a later edit drifts it to.

        WHAT THIS PINS AND WHY EACH PIN IS HERE:

        * Both files exist and are tracked, because an untracked plist is not a
          declaration — config-as-code would still install it and nothing in the
          repository would say what runs.
        * Neither is definition-only. `isDefinitionOnlyLaunchd` in
          ops/scac-mutation-inventory.mjs reads a plist with no trigger and no
          RunAtLoad as a reviewed artifact rather than a deployed service, and
          such an agent is exempt from ops.service closure. A freshness job with
          no moment at which launchd would fire it is the exact false green this
          whole slice exists to refuse, so the trigger is asserted here.
        * Each is registered in ops/config/services.json with a deploy
          mechanism, which is what makes the exemption above inapplicable and
          puts both jobs under `ops-record health`.
        * The fast-forward runs its own mode, and the watchdog runs its own, on
          separate agents. One agent running both would mean that the instant
          launchd drops it, nothing is left to say the fast-forward stopped —
          which is the single failure the watchdog is for.
        """
        services = json.loads(
            (REPO_ROOT / "ops" / "config" / "services.json").read_text(encoding="utf-8"))
        by_mechanism = {
            environment.get("deploy_mechanism"): service["key"]
            for service in services["services"]
            for environment in service.get("environments", [])
        }
        expected = {
            "com.carr.canonical-fast-forward.plist": ("fast-forward", "StartCalendarInterval"),
            "com.carr.canonical-dirty-watchdog.plist": ("watchdog", "StartCalendarInterval"),
        }
        for name, (mode, trigger) in expected.items():
            path = REPO_ROOT / "ops" / "launchd" / name
            self.assertTrue(path.exists(), name)
            tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch",
                                      f"ops/launchd/{name}"], capture_output=True, text=True)
            self.assertEqual(tracked.returncode, 0, f"{name} is not tracked")
            body = path.read_text(encoding="utf-8")
            # Comments are stripped first: this file's plists explain themselves
            # at length, and a key named in prose is not a key launchd reads.
            keys = re.sub(r"<!--.*?-->", "", body, flags=re.S)
            self.assertIn(f"<key>{trigger}</key>", keys,
                          f"{name} has no cadence launchd would fire")
            self.assertIn(f"<string>{mode}</string>", keys, f"{name} runs the wrong mode")
            self.assertIn("--canonical-freshness", keys, name)
            self.assertEqual(by_mechanism.get(f"ops/launchd/{name}"),
                             {"fast-forward": "canonical-fast-forward",
                              "watchdog": "canonical-dirty-watchdog"}[mode],
                             f"{name} is not registered in services.json")
        # The two modes are on two agents, not one.
        self.assertNotEqual(*[
            (REPO_ROOT / "ops" / "launchd" / name).read_text(encoding="utf-8")
            for name in expected])


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
