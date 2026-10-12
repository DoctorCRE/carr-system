#!/usr/bin/env python3
"""ops/pr-board-selftest.py — the PR janitor's board, hermetic.

A fake `gh` (an injected GitHubReader runner replaying
ops/fixtures/pr-board/graphql-pages.json) and a throwaway git remote stand in
for GitHub; nothing here touches the network or the real repository.

What it proves:
  * every action class (MERGE-READY, RESTACK, FIX-CI, STALE, WAIT) is reached,
    each fixture PR lands on its named action, and each reason carries the
    measured fact behind it;
  * a source mutant that DROPS the STALE rule fails these expectations, and so
    does a mutant that MISLABELS the RESTACK rule — the expectations bite;
  * the merge-tree probe tells conflict from clean against main's current tip
    and leaves every ref in the probing checkout exactly where it was;
  * --file-loops files one deduplicated loop per owner lane, leaves an
    unchanged set alone, closes a lane's LOOP when its stale set empties, and
    calls no verb outside the loop verbs; the default run calls none at all;
  * every gh call is a GraphQL read: no mutation, no close, no push, no comment;
  * lib/pr_board.py stays a library (no shebang, no __main__), wired through
    run.sh and the nightly chain, read-only there.
"""
from __future__ import annotations

import copy
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env  # noqa: E402
from lib import pr_board  # noqa: E402
from lib.github_reader import GitHubReader  # noqa: E402

FIXTURE = json.loads((ROOT / "ops/fixtures/pr-board/graphql-pages.json").read_text())
NOW = dt.datetime.fromisoformat(FIXTURE["now"].replace("Z", "+00:00"))
SOURCE_PATH = ROOT / "lib/pr_board.py"

EXPECTED = {
    101: "MERGE-READY",  # green, clean, verdict a day old
    102: "WAIT",         # red, but pushed three hours ago: a session mid-iteration
    103: "STALE",        # red, conflicting, ten days quiet
    104: "STALE",        # draft, green, eight days quiet
    105: "STALE",        # red, twenty-five days quiet: suggested close
    106: "RESTACK",      # conflicts with main (probe)
    107: "RESTACK",      # cancelled: no verdict
    108: "RESTACK",      # green, but the verdict is nine days old
    109: "FIX-CI",       # red while it merges clean
    110: "WAIT",         # draft, green, clean, three days: held by the draft
    111: "MERGE-READY",  # a person's branch, own lane
    112: "RESTACK",      # probe silent, GitHub says conflicting
    113: "RESTACK",      # no checks on head for two days
    114: "FIX-CI",       # review requested changes
}
PROBE = {n: {"verdict": "clean", "files": [], "behind": 4, "source": "merge-tree"} for n in EXPECTED}
PROBE[103] = {"verdict": "conflict", "files": ["db/schema.sql"], "behind": 90, "source": "merge-tree"}
PROBE[106] = {"verdict": "conflict", "files": ["ops/ci.sh", "lib/a.py"], "behind": 30, "source": "merge-tree"}
PROBE[112] = {"verdict": "unknown", "files": [], "behind": None, "source": "merge-tree"}


class FakeGh:
    """subprocess.run's shape; replays the fixture pages in order."""

    def __init__(self, pages=None, *, returncode=0, stderr=""):
        self.pages = list(FIXTURE["pages"] if pages is None else pages)
        self.calls: list[list[str]] = []
        self.returncode, self.stderr = returncode, stderr

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        body = json.dumps(self.pages.pop(0)) if self.returncode == 0 else ""
        return types.SimpleNamespace(returncode=self.returncode, stdout=body, stderr=self.stderr)


def reader(fake=None):
    return GitHubReader(runner=fake or FakeGh(), sleep=lambda s: None, gh="gh", retry_delays=())


def board_from(module=pr_board, probe=PROBE):
    base, nodes = module.fetch_open_prs(reader(), "jbookout/carr-system")
    return module.build_board(base, nodes, probe, now=NOW)


def mismatches(module) -> dict:
    got = {p["number"]: p["action"] for p in board_from(module)["rows"]}
    return {n: (got.get(n), want) for n, want in EXPECTED.items() if got.get(n) != want}


def mutant(old: str, new: str) -> types.ModuleType:
    """lib/pr_board.py with exactly one source edit, loaded beside the real one."""
    src = SOURCE_PATH.read_text()
    assert src.count(old) == 1, f"mutation target not found exactly once: {old!r}"
    module = types.ModuleType("pr_board_mutant")
    module.__file__ = str(SOURCE_PATH)
    exec(compile(src.replace(old, new), str(SOURCE_PATH), "exec"), module.__dict__)
    return module


class Board(unittest.TestCase):
    def test_every_action_class_is_reached_and_each_pr_lands_on_its_action(self):
        self.assertEqual(mismatches(pr_board), {})
        board = board_from()
        self.assertEqual({p["action"] for p in board["rows"]}, set(pr_board.ACTIONS))
        self.assertEqual(sum(board["totals"]["by_action"].values()), len(EXPECTED))

    def test_each_reason_carries_its_measured_fact(self):
        rows = {p["number"]: p for p in board_from()["rows"]}
        must = {
            101: "review not required", 102: "last commit 3h ago", 103: "no commit for 10.0d",
            104: "draft", 105: "suggested: close", 106: "ops/ci.sh, lib/a.py", 107: "cancelled, no verdict",
            108: "green verdict is 9.0d old", 109: "red (ops/ci.sh --strict)", 110: "held by draft",
            112: "[github]", 113: "no checks on head", 114: "review requested changes",
        }
        for number, text in must.items():
            self.assertIn(text, rows[number]["reason"], number)
        self.assertEqual(rows[103]["decision"], "restack")
        self.assertEqual(rows[104]["decision"], "restack")
        self.assertEqual(rows[105]["decision"], "close")
        self.assertNotIn("decision", rows[106])

    def test_lanes_owners_and_totals(self):
        board = board_from()
        rows = {p["number"]: p for p in board["rows"]}
        self.assertEqual(rows[103]["lane"], "codex")
        self.assertEqual(rows[104]["lane"], "claude")       # worktree-* is a Claude Code worktree
        self.assertEqual(rows[105]["lane"], "unattributed")
        self.assertEqual((rows[111]["lane"], rows[111]["owner"]), ("author:dellmccraneycarrus-gif", "Dell"))
        t = board["totals"]
        self.assertEqual(t["by_action"], {"MERGE-READY": 2, "RESTACK": 5, "FIX-CI": 2, "STALE": 3, "WAIT": 2})
        self.assertEqual((t["open"], t["drafts"], t["conflicting"]), (14, 2, 3))
        # 103 ran 20 minutes, 104 ran 7; 105's run completed 25 days ago, outside the 14-day window.
        self.assertEqual(t["head_ci_minutes"]["stale"], 27)
        self.assertEqual(board["bound_action"], pr_board.BOUND_ACTION)
        text = pr_board.render(board)
        self.assertIn("BOUND ACTION  on STALE: one deduplicated loop per PR owner lane", text)
        self.assertIn("never closes, pushes, force-pushes or comments", text)

    def test_mutant_without_the_stale_rule_fails(self):
        broken = mutant('    ("STALE", _is_stale),\n', "")
        self.assertTrue(mismatches(broken), "dropping the STALE rule went unnoticed")
        self.assertEqual({n for n, (got, want) in mismatches(broken).items() if want == "STALE"}, {103, 104, 105})

    def test_mutant_mislabelling_restack_fails(self):
        broken = mutant('    ("RESTACK", _is_restack),', '    ("FIX-CI", _is_restack),')
        wrong = mismatches(broken)
        self.assertTrue(wrong, "mislabelling RESTACK went unnoticed")
        self.assertEqual({n for n, (got, want) in wrong.items()}, {106, 107, 108, 112, 113})


class ReadsOnly(unittest.TestCase):
    def test_pagination_carries_the_cursor_and_every_call_is_a_graphql_read(self):
        fake = FakeGh()
        base, nodes = pr_board.fetch_open_prs(reader(fake), "jbookout/carr-system")
        self.assertEqual((base["oid"], len(nodes)), ("f" * 40, 14))
        self.assertEqual(len(fake.calls), 2)
        self.assertNotIn("cursor=CURSOR-1", fake.calls[0])
        self.assertIn("cursor=CURSOR-1", fake.calls[1])
        for argv in fake.calls:
            self.assertEqual(argv[:3], ["gh", "api", "graphql"])
            query = next(a for a in argv if a.startswith("query="))
            self.assertTrue(query.startswith("query=query("), query[:40])
            self.assertNotIn("mutation", query)

    def test_source_names_no_github_or_git_write(self):
        src = SOURCE_PATH.read_text()
        for forbidden in ('"push"', '"--force"', '"pr", "close"', '"pr", "comment"', '"pr", "merge"',
                          '"update-ref"', '"checkout"', '"reset"', '"mutation'):
            self.assertNotIn(forbidden, src)

    def test_unauthenticated_gh_is_not_configured_not_an_empty_backlog(self):
        fake = FakeGh(returncode=1, stderr="HTTP 401: Bad credentials; try gh auth login")
        with redirect_stdout(io.StringIO()) as out:
            rc = pr_board.main(["--no-probe", "--no-write"], reader=reader(fake), now=NOW,
                               call=self._no_call)
        self.assertEqual(rc, 78)
        self.assertIn("UNKNOWN", out.getvalue())

    def test_default_run_writes_the_report_and_files_nothing(self):
        tmp = Path(tempfile.mkdtemp(prefix="pr-board-"))
        self.addCleanup(shutil.rmtree, tmp)
        with redirect_stdout(io.StringIO()) as out:
            rc = pr_board.main(["--no-probe", "--json", "--out", str(tmp / "pr-board.json")],
                               reader=reader(), now=NOW, call=self._no_call)
        self.assertEqual(rc, 0)
        report = json.loads((tmp / "pr-board.json").read_text())
        self.assertEqual(report["schema"], "pr-board/v1")
        self.assertEqual(json.loads(out.getvalue())["totals"], report["totals"])
        self.assertNotIn("loops", report)
        # Without the probe, GitHub's mergeable is the only conflict source.
        rows = {p["number"]: p for p in report["rows"]}
        self.assertEqual(rows[103]["conflict"]["source"], "github")
        self.assertEqual(rows[109]["conflict"]["verdict"], "unknown")

    @staticmethod
    def _no_call(verb, payload):
        raise AssertionError(f"read-only run called {verb}")


class Loops(unittest.TestCase):
    ALLOWED = {"loop-board", "read-loop", "add-loop", "update-loop", "close-loop"}

    def setUp(self):
        self.loops: dict[str, dict] = {}
        self.verbs: list[tuple[str, dict]] = []

    def call(self, verb, payload):
        self.assertIn(verb, self.ALLOWED)
        self.verbs.append((verb, payload))
        if verb == "loop-board":
            return {"ok": True, "loops": [{"number": n, "label": "", "body": l["body"]}
                                          for n, l in self.loops.items() if l["status"] == "open"]}
        if verb == "read-loop":
            return {"ok": True, "loop": self.loops[payload["number"]]}
        if verb == "add-loop":
            self.assertEqual((payload["kind"], payload["domain"], payload["blocker"]),
                             ("open_loop", "system", "ruling"))
            self.assertTrue(payload["blocker_detail"] and payload["idempotency_key"].startswith("pr-board-"))
            number = str(len(self.loops) + 1)
            self.loops[number] = {"loop_id": f"id-{number}", "version": 1, "status": "open",
                                  "body": payload["body"], "owner": payload["owner"]}
            return {"ok": True}
        loop = next(l for l in self.loops.values() if l["loop_id"] == payload["loop_id"])
        self.assertEqual(payload["base_version"], loop["version"])
        loop["version"] += 1
        if verb == "update-loop":
            loop["body"] = payload["body"]
        else:
            loop["status"] = "closed"
        return {"ok": True}

    def test_one_loop_per_lane_deduplicated_then_closed_when_empty(self):
        board = board_from()
        first = pr_board.file_loops(board, self.call)
        self.assertEqual([(a["lane"], a["verb"], a["prs"]) for a in first],
                         [("claude", "add-loop", [104]), ("codex", "add-loop", [103]),
                          ("unattributed", "add-loop", [105])])
        bodies = sorted(l["body"] for l in self.loops.values())
        self.assertTrue(all("restack, fix, or close" in b and "never closes" in b for b in bodies))
        self.assertIn("[pr-board:codex]", next(b for b in bodies if "#103" in b))

        # Same stale set a day later: nothing is rewritten.
        later = board_from()
        later["generated_at"] = "2026-10-13T16:00:00Z"
        self.verbs.clear()
        second = pr_board.file_loops(later, self.call)
        self.assertEqual({a["verb"] for a in second}, {"unchanged"})
        self.assertFalse([v for v, _ in self.verbs if v in ("add-loop", "update-loop", "close-loop")])

        # 105 is gone and 104's suggestion changes: one update, one close.
        changed = copy.deepcopy(later)
        changed["rows"] = [p for p in changed["rows"] if p["number"] != 105]
        next(p for p in changed["rows"] if p["number"] == 104)["decision"] = "close"
        third = pr_board.file_loops(changed, self.call)
        self.assertEqual({(a["lane"], a["verb"]) for a in third},
                         {("claude", "update-loop"), ("codex", "unchanged"), ("unattributed", "close-loop")})
        self.assertEqual(sum(l["status"] == "open" for l in self.loops.values()), 2)

    def test_file_loops_flag_reaches_the_record_verbs(self):
        with redirect_stdout(io.StringIO()):
            rc = pr_board.main(["--no-probe", "--no-write", "--file-loops"], reader=reader(), now=NOW,
                               call=self.call)
        self.assertEqual(rc, 0)
        self.assertEqual(sum(v == "add-loop" for v, _ in self.verbs), 3)


class Probe(unittest.TestCase):
    """merge-tree against a real throwaway remote: objects in, no ref moved."""

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True,
                              text=True, check=True).stdout.strip()

    def commit(self, repo, path, text, message):
        (repo / path).write_text(text)
        self.git(repo, "add", path)
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def setUp(self):
        self.env = fixture_env()
        self.env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        self.tmp = Path(tempfile.mkdtemp(prefix="pr-board-probe-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        work, self.remote, self.clone = self.tmp / "work", self.tmp / "remote.git", self.tmp / "clone"
        self.git(self.tmp, "init", "-q", "-b", "main", str(work))
        root = self.commit(work, "a.txt", "one\n", "root")
        self.commit(work, "b.txt", "b\n", "b")
        self.git(self.tmp, "init", "-q", "--bare", str(self.remote))
        self.git(work, "push", "-q", str(self.remote), "main:refs/heads/main")
        self.git(self.tmp, "clone", "-q", str(self.remote), str(self.clone))
        # PR 1 edits a.txt one way; PR 2 adds an unrelated file. Then main
        # edits a.txt the other way AFTER the clone, so only a fetch sees it.
        self.git(work, "checkout", "-q", "-b", "pr1", root)
        self.pr1 = self.commit(work, "a.txt", "pr-one\n", "pr1")
        self.git(work, "checkout", "-q", "-b", "pr2", "main")
        self.pr2 = self.commit(work, "c.txt", "c\n", "pr2")
        self.git(work, "checkout", "-q", "main")
        self.main = self.commit(work, "a.txt", "main-two\n", "main moves")
        self.git(work, "push", "-q", str(self.remote), "main:refs/heads/main",
                 f"{self.pr1}:refs/pull/1/head", f"{self.pr2}:refs/pull/2/head")

    def test_conflict_and_clean_against_mains_current_tip_with_no_ref_moved(self):
        before = self.git(self.clone, "for-each-ref")
        probed = pr_board.probe_conflicts(str(self.clone), {"name": "main", "oid": self.main},
                                          [{"number": 1, "head_oid": self.pr1},
                                           {"number": 2, "head_oid": self.pr2}], env=self.env)
        self.assertEqual((probed[1]["verdict"], probed[1]["files"]), ("conflict", ["a.txt"]))
        self.assertEqual((probed[2]["verdict"], probed[2]["behind"]), ("clean", 1))
        self.assertEqual(probed[1]["behind"], 2)
        self.assertEqual(self.git(self.clone, "for-each-ref"), before)
        self.assertFalse((self.clone / ".git/FETCH_HEAD").exists())

    def test_an_unfetchable_head_is_unknown_never_clean(self):
        probed = pr_board.probe_conflicts(str(self.clone), {"name": "main", "oid": self.main},
                                          [{"number": 9, "head_oid": "e" * 40}], env=self.env)
        self.assertEqual(probed[9]["verdict"], "unknown")


class Wiring(unittest.TestCase):
    def test_library_is_not_a_script_ingress(self):
        src = SOURCE_PATH.read_text()
        self.assertFalse(src.startswith("#!"))
        self.assertIsNone(re.search(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:", src))

    def test_run_sh_and_the_nightly_chain_reach_it_read_only(self):
        run_sh = (ROOT / "run.sh").read_text()
        self.assertRegex(run_sh, r"\n  pr-board\)\s+shift; .*from lib import pr_board; sys\.exit\(pr_board\.main")
        steps = [ln for ln in (ROOT / "bin/nightly.sh").read_text().splitlines()
                 if ln.startswith("step ") and "pr-board" in ln]
        self.assertEqual(len(steps), 1)
        self.assertTrue(steps[0].rstrip().endswith("./run.sh pr-board"), steps[0])


if __name__ == "__main__":
    unittest.main()
