#!/usr/bin/env python3
"""Behavioral tests for the progress-board command line surface.

The board has exactly one UI, the interactive app page at
app.doctorcre.com/progress-board. This tool owns the task/question/answer
commands and publishes the JSON data contract that page renders.
"""

import copy
import io
import json
import importlib.util
import os
# These tests assert per-render GitHub sync; the production reuse window is covered in test-progress-board-rest.py.
os.environ["PROGRESS_BOARD_PR_FRESH_SECONDS"] = "0"
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
SCRIPT = REPO / "tools" / "progress_board.py"
LAUNCHD_SCRIPT = REPO / "ops" / "progress-board-render.sh"
SPEC = importlib.util.spec_from_file_location("progress_board", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOARD)

# One dispatcher for every gh call the board makes. The fixture file says what
# each call returns; tests rewrite it between runs.
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
fixture_path = Path(os.environ["BOARD_GH_FIXTURE"])
fixture = json.loads(fixture_path.read_text())
args = sys.argv[1:]
log = os.environ.get("BOARD_GH_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(args) + "\n")
if args[0] != "api" or "graphql" in args[1]:
    sys.exit(2)
parsed = urlparse(args[1])
path, query = parsed.path, parse_qs(parsed.query)
parts = path.split("/")
repo = "/".join(parts[1:3]) if parts[0] == "repos" else ""
if repo in fixture.get("fail", []):
    sys.exit(1)
def emit(value):
    page = int(query.get("page", [1])[0])
    size = int(query.get("per_page", [100])[0])
    if isinstance(value, list):
        value = value[(page-1)*size:page*size]
    print(json.dumps(value))
def raw(pr, merged=False):
    if not isinstance(pr, dict):
        return pr
    # Preserve malformed-input tests: bad GraphQL-shaped recordings become
    # bad REST responses, rather than the fixture adapter healing them.
    if "view" in fixture and (pr.get("state") not in ("OPEN", "CLOSED", "MERGED")
            or not isinstance(pr.get("isDraft"), bool) or not isinstance(pr.get("headRefOid"), str)
            or not isinstance(pr.get("author"), dict) or not isinstance(pr["author"].get("login"), str)):
        return {}
    state = pr.get("state", "MERGED" if merged else "OPEN")
    commit = pr.get("mergeCommit")
    sha = commit.get("oid") if isinstance(commit, dict) else ([] if commit is not None else None)
    if state == "MERGED" and commit is None:
        sha = "1" * 40
    decision = pr.get("reviewDecision")
    return {"number": pr.get("number", 42 if "view" in fixture else None), "state": "open" if state == "OPEN" else "closed",
            "draft": pr.get("isDraft", False), "head": {"sha": pr.get("headRefOid"), "ref": pr.get("headRefName", "")},
            "user": pr.get("author"), "merge_commit_sha": sha,
            "merged_at": pr.get("mergedAt", "2026-10-04T00:00:00Z") if state == "MERGED" else None,
            "title": pr.get("title", "PR"), "body": pr.get("body", ""), "html_url": pr.get("url", ""),
            "created_at": pr.get("createdAt", "2026-09-28T00:00:00Z"),
            "updated_at": pr.get("updatedAt") or datetime.fromtimestamp(fixture_path.stat().st_mtime, timezone.utc).isoformat(),
            "changed_files": pr.get("changedFiles"),
            "mergeable": False if pr.get("mergeable") == "CONFLICTING" else True,
            "mergeable_state": "blocked" if decision == "REVIEW_REQUIRED" else "clean"}
def selected(number):
    if "view" in fixture:
        return fixture["view"]
    for state in ("open", "merged"):
        for pr in fixture.get(state, {}).get(repo, []):
            if pr.get("number") == number:
                return {**pr, "state": "MERGED" if state == "merged" else "OPEN"}
    sys.exit(1)
if parts[0] == "user":
    if fixture.get("repo_list_fails"):
        sys.exit(1)
    emit([{"full_name": name, "archived": False} for name in fixture.get("repos", [])])
elif len(parts) == 4 and parts[3] == "pulls":
    emit([raw(pr) for pr in fixture.get("open", {}).get(repo, [])])
elif len(parts) == 4 and parts[3] == "issues":
    emit([{"number": pr["number"], "pull_request": {"url": "pr"}} for pr in fixture.get("merged", {}).get(repo, [])])
elif "compare" in parts:
    status = fixture.get("compare", {}).get(path)
    if status is None:
        sys.exit(1)
    print(status)
elif len(parts) == 5 and parts[3] == "pulls":
    emit(raw(selected(int(parts[4]))))
elif parts[-1] in ("check-runs", "statuses"):
    pr = fixture.get("view")
    if pr is None:
        pr = next((p for state in ("open", "merged") for p in fixture.get(state, {}).get(repo, [])
                   if p.get("headRefOid") == parts[4]), None)
    if not isinstance(pr, dict):
        sys.exit(1)
    checks = pr.get("statusCheckRollup")
    if not isinstance(checks, list):
        emit({})
    elif parts[-1] == "check-runs":
        if any(not isinstance(c, dict) for c in checks):
            emit({"check_runs": checks})
        else:
            # Leave types intact; the consumer must validate them.
            emit({"check_runs": [{**c, **({"status": c["status"].lower()} if isinstance(c.get("status"), str) else {}),
                                   **({"conclusion": c["conclusion"].lower()} if isinstance(c.get("conclusion"), str) else {})}
                                  for c in checks if "state" not in c]})
    else:
        emit([{**c, "state": c["state"].lower()} for c in checks if isinstance(c, dict) and "state" in c])
elif parts[-1] == "comments":
    pr = selected(int(parts[4]))
    comments = pr.get("comments") if isinstance(pr, dict) else None
    if isinstance(comments, list):
        emit([{"user": c.get("author"), "author_association": c.get("authorAssociation"),
               "body": c.get("body"), "created_at": c.get("createdAt")} if isinstance(c, dict) else c for c in comments])
    else:
        emit({})
elif parts[-1] == "reviews":
    decision = selected(int(parts[4])).get("reviewDecision")
    emit([{"id": 1, "state": decision, "user": {"login": "reviewer"}, "author_association": "COLLABORATOR",
           "submitted_at": "2026-09-28T00:00:00Z"}]
         if decision in ("CHANGES_REQUESTED", "APPROVED") else [])
elif parts[-1] == "files":
    emit([{"filename": f["path"]} for f in selected(int(parts[4])).get("files", [])])
else:
    sys.exit(2)
'''

SHA_A = "a" * 40
SHA_M = "1" * 40
SHA_R = "2" * 40


def iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds").replace("+00:00", "Z")


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["PROGRESS_BOARD_ROOT"] = str(self.root)
        self.env["PROGRESS_BOARD_SKIP_GH"] = "1"
        self.env["PROGRESS_BOARD_SKIP_PROBE"] = "1"
        self.env["PROGRESS_BOARD_RELEASES"] = str(self.root / "releases.jsonl")
        # Mutations publish to the app board; unit tests stay local unless a
        # test spies on the publication itself.
        self.env["PROGRESS_BOARD_LOCAL_ONLY"] = "1"
        self.fixture = self.root / "gh.json"
        self.gh_log = self.root / "gh.log"

    def tearDown(self):
        self.tempdir.cleanup()

    def run_board(self, *args, input_text=None, check=True):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=REPO,
            env=self.env,
            input=input_text,
            text=True,
            capture_output=True,
            check=check,
        )

    def read_state(self, project):
        return json.loads((self.root / "boards" / f"{project}.json").read_text())

    def write_state(self, project, state):
        (self.root / "boards" / f"{project}.json").write_text(json.dumps(state))

    def fake_gh(self, fixture):
        self.env.pop("PROGRESS_BOARD_SKIP_GH", None)
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.env["BOARD_GH_FIXTURE"] = str(self.fixture)
        self.env["BOARD_GH_LOG"] = str(self.gh_log)
        self.fixture.write_text(json.dumps(fixture))
        return gh

    def set_fixture(self, fixture):
        self.fixture.write_text(json.dumps(fixture))

    def gh_calls(self):
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def shipped(self, lane, sha, ts="2026-09-29T12:00:00+00:00"):
        with open(self.root / "releases.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": ts, "lane": lane, "sha": sha, "status": "shipped"}) + "\n")


class ProgressBoardCLI(BoardCase):
    def test_task_producer_explicitly_classifies_system_work(self):
        self.run_board('init','demo','--title','Synthetic board')
        self.run_board('task','demo','repair','--title','Synthetic repair','--status','running','--executor','codex')
        self.assertEqual(self.read_state('demo')['tasks']['repair'].get('domain'),'system')

    def test_large_history_publishes_bounded_pages_without_losing_rows(self):
        import system_work_cache
        self.run_board('init', 'carr-v5', '--title', 'Synthetic board')
        items=[{'id':f'repo:pr:{i}', 'kind':'pull_request', 'title':'Synthetic title '*12,
                'completed':True,'opened_at':'2026-09-01T00:00:00Z',
                'last_activity_at':'2026-09-02T00:00:00Z','identity':{}} for i in range(1448)]
        external={'schema':'system-work-external.v1','items':items,'complete':True,
                  'observed_at':'2026-10-01T00:00:00Z','coverage':[],'completed_pr_history':True,'pr_work_refs':[]}
        remote={}
        def call(verb,args):
            board=args['board_id']
            if verb=='publish-board-snapshot':
                self.assertLessEqual(len(json.dumps(args['snapshot'],separators=(',',':'),ensure_ascii=False)),262144)
                remote[board]={'version':1,'snapshot_json':args['snapshot']}
            return {'ok':True,'snapshot':remote.get(board),'questions':[]}
        with patch.dict(os.environ,self.env), patch.object(BOARD,'call_verb',call), patch.object(system_work_cache,'cached_github',return_value=external):
            BOARD.publish_board('carr-v5')
        manifest=remote['carr-v5']['snapshot_json']['external_inventory']
        restored=[row for page in manifest['pages'] for row in remote[page['board_id']]['snapshot_json']['items']]
        self.assertEqual(restored,items)
        self.assertNotIn('items',manifest)



    def test_conditional_recovery_compares_after_acquiring_shared_lock(self):
        import fcntl
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "work", "--title", "Work", "--status", "running",
                       "--executor", "executor", "--note", "Old evidence")
        expected = {"status": "running", "note": "Old evidence"}
        code = '''
import fcntl, importlib.util, sys
spec = importlib.util.spec_from_file_location("board", sys.argv[1])
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
real_lock = fcntl.flock
def lock(fd, operation):
    if operation == fcntl.LOCK_EX:
        print("write-lock", flush=True)
    return real_lock(fd, operation)
fcntl.flock = lock
board.main(["task", "demo", "work", "--status", "done", "--note", "Recovered",
            "--expected-task", sys.argv[2]])
'''
        path = self.root / "boards/demo.json"
        with (self.root / "boards/demo.lock").open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            with subprocess.Popen([sys.executable, "-c", code, str(SCRIPT), json.dumps(expected)],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as writer:
                try:
                    self.assertEqual(writer.stdout.readline().strip(), "write-lock")
                    state = self.read_state("demo")
                    state["tasks"]["work"].update(status="review", note="Fresh executor evidence")
                    path.write_text(json.dumps(state))
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                _, errors = writer.communicate(timeout=5)
                self.assertEqual(writer.returncode, 0, errors)
        self.assertEqual(self.read_state("demo"), state)
        # Matching ownership permits recovery through the same CLI transaction.
        self.run_board("task", "demo", "work", "--status", "running", "--note", "Resumed",
                       "--expected-task", json.dumps({"status": "review", "note": "Fresh executor evidence"}))
        self.assertEqual(self.read_state("demo")["tasks"]["work"]["note"], "Resumed")

    def test_init_task_ask_answer_and_deliver(self):
        self.run_board("init", "demo", "--title", "Demo project")
        self.run_board("task", "demo", "build", "--title", "Build board", "--status", "running",
                       "--executor", "codex gpt-5.6-luna high", "--pr", "42", "--note", "working")
        self.run_board("ask", "demo", "q1", "--question", "Ship this?", "--default", "continue on the default")
        self.run_board("answer", "demo", "q1", input_text="yes\n")
        self.run_board("deliver", "demo", "--title", "Board ready", "--link", "/tmp/board.json")
        self.run_board("note", "demo", "--text", "Waiting on the release window")

        state = self.read_state("demo")
        self.assertEqual(state["title"], "Demo project")
        self.assertEqual(state["tasks"]["build"]["status"], "running")
        self.assertEqual(state["tasks"]["build"]["pr"], 42)
        self.assertEqual(state["questions"]["q1"]["answer"], "yes")
        self.assertTrue(state["questions"]["q1"]["answered_at"])
        self.assertEqual(state["deliverables"][0]["link"], "/tmp/board.json")
        self.assertEqual(state["notes"][0]["text"], "Waiting on the release window")
        self.assertTrue(state["updated_at"])

    def test_static_html_renderer_is_retired(self):
        for name in ("render_state", "pipeline_svg", "html_path", "esc", "local_updated"):
            self.assertFalse(hasattr(BOARD, name), f"{name} belongs to the retired static page")
        source = SCRIPT.read_text()
        self.assertNotIn("<!doctype html>", source.lower())
        self.assertNotIn("<svg", source)
        self.run_board("init", "demo", "--title", "Demo")
        leftover = self.root / "boards" / "demo.html"
        leftover.write_text("<html>stale copy</html>")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex")
        self.run_board("render", "demo")
        self.assertFalse(leftover.exists(), "render removes the retired static copy")
        self.assertEqual(sorted(p.name for p in (self.root / "boards").iterdir()
                                if p.suffix in {".json", ".html", ".tmp"}), ["demo.json"])
        help_text = self.run_board("--help").stdout
        self.assertNotIn(".html", help_text)

    def test_launchd_render_script_publishes_json_only(self):
        script = LAUNCHD_SCRIPT.read_text()
        self.assertIn("tools/progress_board.py render carr-v5 --publish", script)
        for retired in (".html", "iCloud", "Mobile Documents", "CloudDocs"):
            self.assertNotIn(retired, script)
        self.assertTrue(os.access(LAUNCHD_SCRIPT, os.X_OK))
        agents = (REPO / "AGENTS.md").read_text()
        self.assertNotIn("out/boards/<project>.html", agents)
        self.assertIn("app.doctorcre.com/progress-board", agents)

    def test_snapshot_is_the_versioned_data_contract(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "Build card", "--status", "running",
                       "--executor", "gpt-6-sol high (Codex)", "--summary", "Show the card.")
        self.run_board("task", "demo", "b", "--title", "Seat", "--status", "queued",
                       "--executor", "claude-plan Opus subagent")
        self.run_board("ask", "demo", "q1", "--question", "Ship?", "--default", "Proceed")
        self.run_board("ask", "demo", "q2", "--question", "Colour?", "--default", "Blue")
        self.run_board("answer", "demo", "q2", "--answer", "Green")
        self.run_board("deliver", "demo", "--title", "Spec", "--link", "https://example.com/spec")
        self.run_board("note", "demo", "--text", "Deploy freeze after 5pm")
        snapshot = BOARD.board_snapshot(self.read_state("demo"))
        self.assertEqual(snapshot["schema"], "carr-progress-board.v2")
        self.assertEqual(snapshot["kind"], "project")
        self.assertEqual(set(snapshot), {"schema", "kind", "project", "title", "tasks", "deliverables",
                                         "notes", "decisions", "ledger", "repos", "history", "updated_at",
                                         "github_sync", "omitted"})
        self.assertEqual(snapshot["tasks"]["a"]["provider"], "Codex")
        self.assertEqual(snapshot["tasks"]["a"]["model"], "gpt-6-sol")
        self.assertEqual(snapshot["tasks"]["a"]["effort"], "high")
        self.assertEqual(snapshot["tasks"]["a"]["summary"], "Show the card.")
        self.assertEqual(snapshot["decisions"], [{"id": "q2", "question": "Colour?", "answer": "Green",
                                                  "default": "Blue", "answered_at": snapshot["decisions"][0]["answered_at"]}])
        self.assertEqual(snapshot["notes"][0]["text"], "Deploy freeze after 5pm")
        pools = {row["pool"]: row for row in snapshot["ledger"]}
        self.assertEqual(list(pools)[:5], ["codex", "grok", "flash-next", "claude-cloud", "orchestrator"])
        self.assertEqual(pools["codex"]["count"], 1)
        self.assertEqual(pools["codex"]["glyph"], "C")
        self.assertEqual(pools["codex"]["models"], [{"provider": "Codex", "model": "gpt-6-sol",
                                                     "effort": "high", "count": 1}])
        self.assertTrue(pools["claude-cloud"]["violation"])
        self.assertFalse(pools["codex"]["violation"])
        self.assertNotIn("questions", snapshot)
        self.assertLess(len(json.dumps(snapshot)), 262144)

    def test_stuck_is_derived_for_blocked_and_old_running_tasks(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "blocked", "--title", "Blocked", "--status", "blocked",
                       "--executor", "codex", "--reason", "Waiting on API key", "--next-action", "Joe adds the key")
        self.run_board("task", "demo", "running", "--title", "Old", "--status", "running", "--executor", "codex")
        state = self.read_state("demo")
        state["tasks"]["running"]["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.assertTrue(BOARD.is_stuck(state["tasks"]["blocked"]))
        self.assertTrue(BOARD.is_stuck(state["tasks"]["running"]))
        self.assertEqual(BOARD.task_health(state["tasks"]["running"]), "blocked")
        self.assertEqual(BOARD.pulse_state(state["tasks"]["running"]), "critical")

    def test_blocked_requires_reason_and_next_action_and_clears_when_unblocked(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex")
        for extra in ([], ["--reason", "Needs a key"], ["--next-action", "Add it"]):
            with self.subTest(extra=extra):
                refused = self.run_board("task", "demo", "a", "--status", "blocked", *extra, check=False)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("--reason and --next-action", refused.stderr)
        refused = self.run_board("task", "demo", "a", "--health", "blocked", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.run_board("task", "demo", "a", "--status", "blocked", "--reason", "Needs a key",
                       "--next-action", "Joe adds the key")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(BOARD.blocked_detail(task), ("Needs a key", "Joe adds the key"))
        self.run_board("task", "demo", "a", "--note", "still waiting")  # keeps the recorded reason
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["blocked_reason"], "Needs a key")
        self.run_board("task", "demo", "a", "--status", "running")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertNotIn("blocked_reason", task)
        self.assertNotIn("next_action", task)
        self.assertIsNone(BOARD.blocked_detail(task))

    def test_every_blocked_card_has_a_reason_and_next_action(self):
        at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        fresh = "2026-09-29T11:30:00+00:00"
        cases = {
            "checks": ({"status": "blocked", "pr": 1, "pr_phase": "Checks failing", "updated_at": fresh},
                       "CI checks are failing on the PR head"),
            "review": ({"status": "blocked", "pr": 1, "pr_phase": "Review blocked", "updated_at": fresh},
                       "An independent reviewer posted BLOCK"),
            "conflict": ({"status": "blocked", "pr": 1, "pr_phase": "Merge conflict", "updated_at": fresh},
                         "Merge conflict with the base branch"),
            "changes": ({"status": "blocked", "pr": 1, "pr_phase": "Changes requested", "updated_at": fresh},
                        "A reviewer requested changes"),
            "closed": ({"status": "failed", "pr": 1, "pr_phase": "Closed unmerged", "updated_at": fresh},
                       "PR closed without merging"),
            "failed": ({"status": "failed", "updated_at": fresh}, "Task failed"),
            "stuck": ({"status": "running", "updated_at": "2026-09-29T08:45:00+00:00"},
                      "No update for 3h 15m"),
            "legacy": ({"status": "blocked", "updated_at": fresh}, "Marked blocked without a recorded reason"),
            "health": ({"status": "running", "health": "blocked", "updated_at": fresh},
                       "Marked blocked without a recorded reason"),
        }
        for name, (task, reason) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(BOARD.task_health(task, at), "blocked")
                detail = BOARD.blocked_detail(task, at)
                self.assertIsNotNone(detail)
                self.assertEqual(detail[0], reason)
                self.assertTrue(detail[1].strip())
        self.assertIsNone(BOARD.blocked_detail({"status": "running", "updated_at": fresh}, at))
        explicit = {"status": "blocked", "pr_phase": "Checks failing", "blocked_reason": "Flaky runner",
                    "next_action": "Re-run once", "updated_at": fresh}
        self.assertEqual(BOARD.blocked_detail(explicit, at), ("Flaky runner", "Re-run once"))

    def test_live_or_done_cards_drop_leftover_blocked_health(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex",
                       "--pr", "42", "--health", "blocked", "--reason", "Review round 1",
                       "--next-action", "Fix the findings")
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Served in production")
        task = self.read_state("demo")["tasks"]["a"]
        for field in ("health", "blocked_reason", "next_action"):
            self.assertNotIn(field, task)
        self.assertEqual(BOARD.task_health(task), "healthy")
        # State written before this rule: five live cards still carried blocked.
        state = self.read_state("demo")
        for n in range(5):
            state["tasks"][f"old{n}"] = {"title": f"Old {n}", "status": "done", "stage": "live",
                                         "evidence": "Measured", "health": "blocked", "executor": "Codex",
                                         "updated_at": iso(), "completed_at": iso()}
        state["tasks"]["merged"] = {"title": "Merged", "status": "done", "stage": "merged", "pr": 7,
                                    "pr_phase": "Merged", "health": "blocked", "executor": "Codex",
                                    "updated_at": iso()}
        self.write_state("demo", state)
        for n in range(5):
            self.assertEqual(BOARD.task_health(state["tasks"][f"old{n}"]), "healthy")
        self.run_board("render", "demo")
        after = self.read_state("demo")["tasks"]
        for task_id in [f"old{n}" for n in range(5)] + ["merged"]:
            self.assertNotIn("health", after[task_id], task_id)

    def test_stale_flag_after_six_hours_without_update(self):
        at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        running = {"status": "running", "updated_at": "2026-09-29T05:30:00+00:00"}
        self.assertTrue(BOARD.is_stale(running, at))
        self.assertEqual(BOARD.age_text(running["updated_at"], at), "6h 30m")
        self.assertFalse(BOARD.is_stale({**running, "updated_at": "2026-09-29T06:00:01+00:00"}, at))
        self.assertTrue(BOARD.is_stale({**running, "updated_at": "2026-09-29T06:00:00+00:00"}, at))
        self.assertTrue(BOARD.is_stale({"status": "review", "updated_at": "2026-09-28T12:00:00Z"}, at))
        self.assertEqual(BOARD.age_text("2026-09-28T10:00:00Z", at), "1d 2h")
        for status in ("done", "queued", "failed"):
            self.assertFalse(BOARD.is_stale({"status": status, "updated_at": "2026-09-20T00:00:00Z"}, at), status)
        self.assertFalse(BOARD.is_stale({"status": "running", "stage": "live", "evidence": "x",
                                         "updated_at": "2026-09-20T00:00:00Z"}, at))
        self.assertEqual(BOARD.STALE_AFTER, timedelta(hours=6))

    def test_ledger_flags_claude_plan_executor(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "violation", "--title", "Wrong seat", "--status", "queued",
                       "--executor", "claude-plan Opus subagent")
        ledger = {row["pool"]: row for row in BOARD.executor_ledger(self.read_state("demo")["tasks"])}
        self.assertTrue(ledger["claude-cloud"]["violation"])
        self.assertEqual(ledger["claude-cloud"]["label"], "Claude cloud credits")

    def test_executor_metadata_parser_handles_legacy_spellings(self):
        cases = {
            "gpt-6-sol high (Codex)": ("Codex", "gpt-6-sol", "high"),
            "codex gpt-6-sol high": ("Codex", "gpt-6-sol", "high"),
            "Codex gpt-6-sol high x2": ("Codex", "gpt-6-sol", "high"),
            # A seat name is not model evidence; an explicit model always wins.
            "orchestrator": ("Unknown", "unknown", "unknown"),
            "Claude Opus 5.5 (orchestrator)": ("Anthropic", "Claude Opus 5.5", "unknown"),
            "gpt-6-sol high (orchestrator)": ("Codex", "gpt-6-sol", "high"),
            "Claude Sonnet 4.5 high (orchestrator)": ("Anthropic", "Claude Sonnet 4.5", "high"),
            # No explicit model means "unknown", never the raw label (#1405).
            "grok 4.7 medium": ("xAI", "unknown", "medium"),
            "Codex orchestrator gpt-5.5 xhigh": ("Codex", "gpt-5.5", "xhigh"),
            "Claude Sonnet 4.6 orchestrator high": ("Anthropic", "Claude Sonnet 4.6", "high"),
            "codex": ("Codex", "unknown", "unknown"),
            "claude high": ("Anthropic", "unknown", "high"),
            "grok": ("xAI", "unknown", "unknown"),
            "flash-next": ("Google", "unknown", "unknown"),
            "": ("Unknown", "unknown", "unknown"),
        }
        for executor, expected in cases.items():
            with self.subTest(executor=executor):
                self.assertEqual(BOARD.executor_metadata(executor), expected)

    def test_explicit_metadata_overrides_executor(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "queued",
                       "--executor", "orchestrator", "--provider", "Codex",
                       "--model", "gpt-6-sol", "--effort", "xhigh", "--summary", "Check the route.")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["provider"], task["model"], task["effort"], task["summary"]),
                         ("Codex", "gpt-6-sol", "xhigh", "Check the route."))
        self.assertEqual(task["stage_history"][0]["stage"], "queued")

    def test_backfill_uses_pr_title_and_retains_existing_task_history(self):
        state = {"tasks": {
            "pr": {"title": "PR 42", "executor": "gpt-6-sol high (Codex)",
                   "pr": 42, "repo": "jbookout/carr-system", "status": "done",
                   "stage_history": [{"stage": "review", "at": "earlier"}]},
            "other": {"title": "Check CRM capture", "executor": "orchestrator",
                      "status": "running", "note": "Verify that captured mail reaches the CRM."},
        }}
        calls = []
        def lookup(number, repo):
            calls.append((number, repo))
            return {"title": "Show model and effort on board cards", "body": "The board now names each model.",
                    "url": "https://github.com/jbookout/carr-system/pull/42", "headRefOid": "a" * 40}
        self.assertEqual(BOARD.backfill_state(state, lookup), 2)
        self.assertEqual(calls, [(42, "jbookout/carr-system")])
        self.assertEqual(state["tasks"]["pr"]["summary"], "Show model and effort on board cards.")
        self.assertEqual(state["tasks"]["pr"]["stage_history"], [{"stage": "review", "at": "earlier"}])
        self.assertEqual(state["tasks"]["other"]["summary"], "Verify that captured mail reaches the CRM.")
        self.assertEqual(state["tasks"]["other"]["provider"], "Unknown")
        self.assertEqual(state["tasks"]["other"]["model"], "unknown")

    def test_review_verdict_requires_trusted_comment_on_current_head(self):
        head = "a" * 40
        payload = {"headRefOid": head, "author": {"login": "builder"},
                   "comments": [{"author": {"login": "reviewer"}, "authorAssociation": "COLLABORATOR",
                                 "createdAt": "2026-09-29T10:00:00Z",
                                 "body": "APPROVE\nReviewed-SHA: " + head}]}
        self.assertEqual(BOARD.review_verdict(payload), "APPROVE")
        payload["headRefOid"] = "b" * 40
        self.assertEqual(BOARD.review_verdict(payload), "Not recorded")

    def test_pr_summary_is_one_plain_line(self):
        body = ("<!-- template -->\n## Summary\n\n- **Show** the [board](https://x.example) on phones.\n"
                "Second line.\n")
        self.assertEqual(BOARD.pr_summary(body), "Show the board on phones.")
        self.assertEqual(BOARD.pr_summary(""), "")
        self.assertEqual(BOARD.pr_summary("## What changed\n\n| a | b |\n```\ncode\n```\nReal line here"),
                         "Real line here")
        long = BOARD.pr_summary("word " * 80)
        self.assertLessEqual(len(long), 160)
        self.assertTrue(long.endswith("…"))

    def test_legacy_live_without_evidence_with_a_pr_is_not_live(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "legacy", "--title", "Legacy", "--status", "done",
                       "--executor", "Codex", "--pr", "42")
        state = self.read_state("demo")
        state["tasks"]["legacy"]["stage"] = "measured"
        state["tasks"]["legacy"]["status"] = "measured"
        self.write_state("demo", state)
        self.run_board("task", "demo", "plain", "--title", "Plain", "--status", "done", "--executor", "Codex")
        tasks = self.read_state("demo")["tasks"]
        self.assertEqual(BOARD.task_stage(tasks["legacy"]), "build")
        self.assertEqual(BOARD.task_stage(tasks["plain"]), "live")

    def test_reopening_live_clears_completion(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "done",
                       "--executor", "Codex", "--stage", "live", "--evidence", "Observed outcome")
        self.run_board("task", "demo", "a", "--status", "running")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["status"], "running")
        self.assertNotIn("stage", task)
        self.assertNotIn("evidence", task)
        self.assertNotIn("completed_at", task)

    def test_live_requires_explicit_evidence_and_merge_does_not_complete(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex", "--pr", "42")
        missing = self.run_board("task", "demo", "a", "--stage", "live", check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("evidence", missing.stderr.lower())
        self.assertEqual(self.read_state("demo")["tasks"]["a"].get("stage"), None)
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Page returned expected data")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "live")
        self.assertEqual(task["evidence"], "Page returned expected data")
        self.assertTrue(task["completed_at"])

    def test_legacy_measured_maps_to_live(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "legacy", "--title", "Legacy", "--status", "done",
                       "--executor", "Codex", "--pr", "42", "--stage", "measured", "--evidence", "Legacy verified")
        self.assertEqual(self.read_state("demo")["tasks"]["legacy"]["stage"], "live")


class PullRequestStatus(BoardCase):
    VALID = {
        "state": "OPEN", "isDraft": False, "headRefOid": SHA_A,
        "author": {"login": "builder"},
        "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
        "files": [{"path": "mcp-server/src/board-answers.js"}], "changedFiles": 1,
        "comments": [{"author": {"login": "reviewer"}, "authorAssociation": "COLLABORATOR",
                      "body": "APPROVE\nReviewed-SHA: " + SHA_A, "createdAt": "2026-09-28T10:00:00Z"}],
    }

    def start(self, **task_args):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42", *task_args.get("extra", []))

    def test_malformed_github_payload_keeps_previous_status(self):
        self.start()
        prior = self.read_state("demo")
        self.fake_gh({"view": self.VALID})
        fields = [
            (("state",), []), (("isDraft",), []), (("headRefOid",), []),
            (("author",), []), (("author", "login"), []),
            (("statusCheckRollup",), {}), (("statusCheckRollup", 0), []),
            (("statusCheckRollup", 0, "conclusion"), []),
            (("statusCheckRollup", 0, "status"), []),
            (("comments",), {}), (("comments", 0), []),
            (("comments", 0, "author"), []),
            (("comments", 0, "author", "login"), []),
            (("comments", 0, "authorAssociation"), []),
            (("comments", 0, "body"), []),
            (("comments", 0, "createdAt"), []),
        ]
        for path, wrong_type in fields:
            for missing in (False, True):
                if missing and isinstance(path[-1], int):
                    continue
                with self.subTest(path=path, missing=missing):
                    payload = copy.deepcopy(self.VALID)
                    parent = payload
                    for part in path[:-1]:
                        parent = parent[part]
                    if missing:
                        del parent[path[-1]]
                    else:
                        parent[path[-1]] = wrong_type
                    self.set_fixture({"view": payload})
                    self.run_board("render", "demo")
                    self.assertEqual(self.read_state("demo")["tasks"], prior["tasks"])
        self.set_fixture({"view": {**self.VALID, "mergeCommit": "not-an-object"}})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"], prior["tasks"])
        self.set_fixture({"view": []})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"], prior["tasks"])
        self.assertEqual(self.read_state("demo")["github_sync"]["failed"][0]["error"],
                         "gh returned a malformed PR payload")

    def test_review_readiness_follows_the_trusted_latest_exact_head_verdict(self):
        self.start()
        self.fake_gh({})
        base = {k: v for k, v in self.VALID.items() if k != "comments"}

        def comment(login, association, body, created):
            return {"author": {"login": login}, "authorAssociation": association, "body": body, "createdAt": created}
        approve = "APPROVE\nReviewed-SHA: " + SHA_A + "\n"
        cases = [
            # Every session posts as the owner account (release-pipeline.v1.json
            # _review_evidence), so the maker's own account carries a verdict.
            ([comment("builder", "OWNER", approve, "2026-09-28T10:00:00Z")], "Ready to merge"),
            ([comment("outsider", "NONE", approve, "2026-09-28T10:00:00Z")], "Awaiting review"),
            ([comment("reviewer", "COLLABORATOR", approve, "2026-09-28T10:00:00Z")], "Ready to merge"),
            ([comment("reviewer", "COLLABORATOR", approve, "2026-09-28T10:00:00Z"),
              comment("reviewer", "COLLABORATOR", "BLOCK\nNeeds a fix", "2026-09-28T11:00:00Z")], "Review blocked"),
        ]
        for comments, expected in cases:
            with self.subTest(expected=expected):
                self.set_fixture({"view": {**base, "comments": comments}})
                self.run_board("render", "demo")
                self.assertEqual(self.read_state("demo")["tasks"]["a"]["pr_phase"], expected)
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["review_verdict"], "BLOCK")

    def test_same_pr_number_in_two_repositories_and_legacy_state(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "carr", "--title", "CARR fix", "--status", "running", "--executor", "Codex", "--pr", "85")
        self.run_board("task", "demo", "app", "--title", "App fix", "--status", "running", "--executor", "Codex",
                       "--pr", "85", "--repo", "jbookout/doctorcre-app")
        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["carr"]["repo"], "jbookout/carr-system")
        self.assertEqual(state["tasks"]["app"]["repo"], "jbookout/doctorcre-app")
        del state["tasks"]["carr"]["repo"]  # JSON written before --repo existed
        self.write_state("demo", state)
        carr = {**self.VALID, "number": 85, "state": "MERGED", "mergeCommit": {"oid": SHA_M}}
        app = {**self.VALID, "number": 85, "comments": []}
        self.fake_gh({"open": {"jbookout/doctorcre-app": [app]},
                      "merged": {"jbookout/carr-system": [carr]}})
        self.run_board("render", "demo")
        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["carr"]["pr_phase"], "Merged")
        self.assertEqual(state["tasks"]["app"]["pr_phase"], "Awaiting review")
        self.assertNotIn("repo", state["tasks"]["carr"])
        self.assertEqual(BOARD.task_repo(state["tasks"]["carr"]), "jbookout/carr-system")

    def test_changing_pr_identity_offline_discards_previous_pr_status(self):
        self.start()
        self.fake_gh({"view": {**self.VALID, "state": "MERGED", "comments": []}})
        self.run_board("render", "demo")
        old = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((old["status"], old["stage"], old["pr_phase"], old["pr_head"]),
                         ("done", "merged", "Merged", SHA_A))
        self.set_fixture({})
        self.run_board("task", "demo", "a", "--repo", "jbookout/doctorcre-app")
        moved = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((moved["repo"], moved["pr"]), ("jbookout/doctorcre-app", 42))
        self.assertEqual((moved["status"], moved["stage"]), ("running", "build"))
        for field in ("pr_phase", "pr_checks", "pr_head", "evidence", "completed_at", "merge_sha"):
            self.assertNotIn(field, moved)

    def test_pr_derivation_and_offline_retention(self):
        self.start(extra=["--note", "Keep this note"])
        self.fake_gh({})
        base = {**self.VALID, "comments": []}

        def review(body):
            return {"author": {"login": "reviewer"}, "authorAssociation": "COLLABORATOR",
                    "createdAt": "2026-09-28T10:00:00Z", "body": body}
        cases = [
            ({"isDraft": True}, "running", "build", "Draft"),
            ({"statusCheckRollup": [{"name": "unit", "conclusion": "FAILURE", "status": "COMPLETED"}]},
             "blocked", "ci", "Checks failing"),
            ({"statusCheckRollup": [{"conclusion": "", "status": "IN_PROGRESS"}]}, "running", "ci", "CI"),
            ({"comments": [review("APPROVE\nReviewed-SHA: " + "b" * 40 + "\n")]}, "review", "review", "Awaiting review"),
            ({"comments": [review("APPROVE\nReviewed-SHA: " + SHA_A + "\n")]}, "review", "review", "Ready to merge"),
        ]
        self.assertEqual(BOARD.derived_pr_state({**base, "state": "CLOSED"}),
                         ("failed", "ci", "Closed unmerged"))
        cases.append(({"state": "MERGED"}, "done", "merged", "Merged"))
        previous_update = self.read_state("demo")["tasks"]["a"]["updated_at"]
        for change, status, stage, phase in cases:
            with self.subTest(phase=phase):
                self.set_fixture({"view": {**base, **change}})
                self.run_board("render", "demo")
                task = self.read_state("demo")["tasks"]["a"]
                self.assertEqual((task["status"], task["stage"], task["pr_phase"]), (status, stage, phase))
                self.assertEqual(task["note"], "Keep this note")
                self.assertGreater(task["updated_at"], previous_update)
                previous_update = task["updated_at"]
                if status == "blocked":
                    self.assertEqual(BOARD.blocked_detail(task)[0], "Failing checks: unit")
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["updated_at"], previous_update)
        prior = self.read_state("demo")
        self.set_fixture({})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"], prior["tasks"])
        self.set_fixture({"view": {**base, "state": "MERGED"}})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["stage"], "merged")
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Production response measured")
        completed_at = self.read_state("demo")["tasks"]["a"]["completed_at"]
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "live")
        self.assertEqual(task["completed_at"], completed_at)

    def test_a_verified_release_alone_never_completes_a_project_card(self):
        # #1439: a project card completes from a release only for a declared
        # worker/app delivery target, from the production source readback.
        self.start()
        merged = {**self.VALID, "state": "MERGED", "comments": [], "mergeCommit": {"oid": SHA_M}}
        self.fake_gh({"view": merged})
        self.shipped("worker", SHA_M)
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["stage"], task["merge_sha"]), ("merged", SHA_M))
        self.assertNotIn("evidence", task)
        self.assertIn("no delivery target recorded", task["release_wait"])
        self.run_board("task", "demo", "a", "--delivery-target", "workstation")
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "merged")
        self.assertIn("the workstation delivery target", task["release_wait"])
        self.assertEqual([c for c in self.gh_calls() if "/compare/" in c[1]], [])

    def test_all_repos_card_in_an_exact_release_needs_no_compare(self):
        card = {"status": "done", "stage": "merged", "merge_sha": SHA_M, "pr": 7, "repo": "jbookout/carr-system"}
        release = {"sha": SHA_M, "lane": "worker", "ts": "2026-09-29T12:00:00+00:00", "source": "releases.jsonl"}
        with patch.object(BOARD, "latest_release", return_value=release), \
             patch.object(BOARD, "compare_status", side_effect=AssertionError("compare called")):
            self.assertTrue(BOARD.auto_live(card, "jbookout/carr-system", iso(), ["mcp-server/src/a.js"]))
        self.assertEqual(card["stage"], "live")
        self.assertIn("verified worker release", card["evidence"])

    def test_latest_verified_release_comes_from_shipped_rows_then_live_probe(self):
        releases = self.root / "releases.jsonl"
        releases.write_text("\n".join([
            json.dumps({"ts": "2026-09-28T00:00:00+00:00", "lane": "worker", "sha": "0" * 40, "status": "shipped"}),
            json.dumps({"ts": "2026-09-28T06:00:00+00:00", "lane": "worker", "sha": SHA_R, "status": "shipped"}),
            json.dumps({"ts": "2026-09-29T00:00:00+00:00", "lane": "worker", "sha": "3" * 40, "status": "blocked",
                        "reason": "canary_pending", "detail": "main canary has not finished on 333333333333"}),
            "not json",
        ]) + "\n")
        probes = {"https://api.doctorcre.com/release": {"git_sha": {"value": "6" * 40}},
                  "https://app.doctorcre.com/app-release": {"source_commit": "7" * 40, "environment": "production"}}
        with patch.dict(os.environ, {"PROGRESS_BOARD_RELEASES": str(releases)}):
            os.environ.pop("PROGRESS_BOARD_SKIP_PROBE", None)
            with patch.object(BOARD, "probe_json", lambda url: probes.get(url)):
                BOARD.RELEASE_CACHE.clear()
                carr = BOARD.latest_release("jbookout/carr-system")
                app = BOARD.latest_release("jbookout/doctorcre-app")
                wait = BOARD.release_wait_reason("jbookout/carr-system")
        BOARD.RELEASE_CACHE.clear()
        self.assertEqual((carr["sha"], carr["lane"]), (SHA_R, "worker"))
        self.assertEqual((app["sha"], app["source"]), ("7" * 40, "live probe"))
        self.assertEqual(wait, "release pipeline blocked (canary_pending): main canary has not finished on 333333333333")

    def test_merged_card_not_yet_released_shows_waiting_on_release_with_pipeline_reason(self):
        self.start(extra=["--delivery-target", "worker"])
        merged = {**self.VALID, "state": "MERGED", "comments": [], "mergeCommit": {"oid": SHA_M}}
        self.shipped("worker", SHA_R, ts="2026-09-29T08:00:00+00:00")
        with open(self.root / "releases.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": "2026-09-29T09:00:00+00:00", "lane": "worker", "sha": "3" * 40,
                                 "status": "failed", "step": "canary", "detail": "canary pending"}) + "\n")
        evidence = {"value": None}
        with patch.dict(os.environ, self.env), \
             patch.object(BOARD, "fetch_pr", return_value=(merged, None)), \
             patch.object(BOARD, "deployed_release", return_value=None), \
             patch.object(BOARD, "deployment_evidence", lambda info, release: evidence["value"]):
            BOARD.RELEASE_CACHE.clear()
            BOARD.render("demo")
            task = self.read_state("demo")["tasks"]["a"]
            self.assertEqual(task["stage"], "merged")
            self.assertEqual(task["release_wait"],
                             "production does not show this change yet; release pipeline failed at canary: canary pending")
            # Once the production readback shows it, the wait note goes away.
            evidence["value"] = f"GET https://example.invalid/release observed production source {SHA_M}"
            BOARD.render("demo")
        BOARD.RELEASE_CACHE.clear()
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["status"], task["stage"]), ("done", "live"))
        self.assertIn(SHA_M, task["evidence"])
        self.assertNotIn("release_wait", task)
        self.assertEqual(task["stage_history"][-1]["stage"], "live")

    def test_stage_changes_record_history_and_the_timer_reads_stage_entered_at(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "queued", "--executor", "Codex")
        first = self.read_state("demo")["tasks"]["a"]
        self.assertEqual([h["stage"] for h in first["stage_history"]], ["queued"])
        self.assertEqual(first["stage_entered_at"], first["stage_history"][-1]["entered_at"])
        self.run_board("task", "demo", "a", "--note", "same stage")
        self.assertEqual(len(self.read_state("demo")["tasks"]["a"]["stage_history"]), 1)
        self.run_board("task", "demo", "a", "--status", "running")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual([h["stage"] for h in task["stage_history"]], ["queued", "build"])
        self.assertEqual(task["stage_entered_at"], task["stage_history"][-1]["entered_at"])
        at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        timed = {**task, "stage_entered_at": "2026-09-29T09:46:00+00:00", "updated_at": "2026-09-29T11:59:00+00:00"}
        self.assertEqual(BOARD.stage_timer(timed, at), "build 2h 14m")
        self.assertEqual(BOARD.stage_timer({"status": "review", "stage_entered_at": "2026-09-29T11:25:00Z"}, at),
                         "review 35m")
        legacy = {"status": "review", "updated_at": "2026-09-29T11:59:00Z",
                  "stage_history": [{"stage": "review", "at": "2026-09-29T11:25:00Z"}]}
        self.assertEqual(BOARD.stage_timer(legacy, at), "review 35m")
        durations = BOARD.stage_durations({"stage_history": [
            {"stage": "queued", "entered_at": "2026-09-29T10:00:00Z"},
            {"stage": "build", "entered_at": "2026-09-29T10:30:00Z"}]}, at)
        self.assertEqual(durations, [("queued", "2026-09-29T10:00:00Z", "30m"), ("build", "2026-09-29T10:30:00Z", "1h 30m")])

    def test_render_backfills_missing_stage_timestamps_from_last_update(self):
        self.run_board("init", "demo", "--title", "Demo")
        state = self.read_state("demo")
        state["tasks"]["legacy"] = {"title": "Legacy", "status": "running", "executor": "Codex",
                                    "updated_at": "2026-09-29T07:00:00+00:00"}
        state["tasks"]["partial"] = {"title": "Partial", "status": "running", "executor": "Codex",
                                     "updated_at": "2026-09-29T07:00:00+00:00",
                                     "stage_history": [{"stage": "build", "status": "running", "at": "2026-09-29T06:00:00+00:00"}]}
        self.write_state("demo", state)
        self.run_board("render", "demo")
        tasks = self.read_state("demo")["tasks"]
        self.assertEqual(tasks["legacy"]["stage_entered_at"], "2026-09-29T07:00:00+00:00")
        self.assertEqual(tasks["legacy"]["stage_history"], [{"stage": "build", "entered_at": "2026-09-29T07:00:00+00:00"}])
        self.assertEqual(tasks["partial"]["stage_history"][0]["entered_at"], "2026-09-29T06:00:00+00:00")
        self.assertEqual(tasks["partial"]["stage_entered_at"], "2026-09-29T06:00:00+00:00")
        self.assertEqual(tasks["legacy"]["updated_at"], "2026-09-29T07:00:00+00:00")


class GitHubSync(BoardCase):
    """Addition 12: every render --publish syncs each PR card from GitHub."""

    OPEN = {
        "state": "OPEN", "isDraft": False, "headRefOid": SHA_A, "author": {"login": "builder"},
        "statusCheckRollup": [{"name": "unit", "conclusion": "SUCCESS", "status": "COMPLETED"}],
        "comments": [], "reviewDecision": "REVIEW_REQUIRED", "mergeable": "MERGEABLE",
        "files": [{"path": "mcp-server/src/board-answers.js"}], "changedFiles": 1,
    }

    def start(self, *extra):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42", *extra)

    def sync(self, view, *extra_fixture):
        self.set_fixture({"view": view, **(extra_fixture[0] if extra_fixture else {})})
        result = self.run_board("render", "demo")
        return self.read_state("demo")["tasks"]["a"], result

    def test_every_stage_is_derived_from_github(self):
        self.start()
        self.fake_gh({})
        running = [{"name": "unit", "conclusion": None, "status": "IN_PROGRESS"}]
        failing = [{"name": "unit", "conclusion": "FAILURE", "status": "COMPLETED"},
                   {"name": "types", "conclusion": "TIMED_OUT", "status": "COMPLETED"},
                   {"name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED"}]
        cases = [
            ("draft", {"isDraft": True}, "running", "build", "Draft", None),
            ("checks running", {"statusCheckRollup": running}, "running", "ci", "CI", None),
            ("awaiting review", {}, "review", "review", "Awaiting review", None),
            ("changes requested", {"reviewDecision": "CHANGES_REQUESTED"}, "blocked", "review",
             "Changes requested", "A reviewer requested changes"),
            ("failing checks", {"statusCheckRollup": failing}, "blocked", "ci", "Checks failing",
             "Failing checks: unit, types"),
            ("approved", {"reviewDecision": "APPROVED"}, "review", "review", "Approved", None),
            ("merged", {"state": "MERGED", "mergeCommit": {"oid": SHA_M}}, "done", "merged", "Merged", None),
        ]
        for name, change, status, stage, phase, reason in cases:
            with self.subTest(name):
                task, _ = self.sync({**self.OPEN, **change})
                self.assertEqual((task["status"], task["stage"], task["pr_phase"]), (status, stage, phase))
                detail = BOARD.blocked_detail(task)
                self.assertEqual(detail[0] if detail else None, reason)
                self.assertEqual(task["stage_history"][-1]["stage"], stage)
        # A verified release alone never completes a project card (#1439):
        # the same sync names what it is waiting for.
        self.shipped("worker", SHA_M)
        task, _ = self.sync({**self.OPEN, "state": "MERGED", "mergeCommit": {"oid": SHA_M}})
        self.assertEqual((task["status"], task["stage"]), ("done", "merged"))
        self.assertIn("no delivery target recorded", task["release_wait"])

    def test_legacy_commit_statuses_do_not_void_the_sync(self):
        self.start()
        self.fake_gh({})
        rollup = [{"name": "unit", "conclusion": "SUCCESS", "status": "COMPLETED"},
                  {"__typename": "StatusContext", "context": "deploy/preview", "state": "FAILURE"}]
        task, _ = self.sync({**self.OPEN, "statusCheckRollup": rollup})
        self.assertEqual((task["status"], task["stage"]), ("blocked", "ci"))
        self.assertEqual(BOARD.blocked_detail(task)[0], "Failing checks: deploy/preview")

    def test_sync_never_moves_a_card_back_past_a_later_manual_stage(self):
        self.start("--stage", "review")
        self.fake_gh({})
        task, _ = self.sync({**self.OPEN, "isDraft": True})
        self.assertEqual(task["stage"], "review", "a manual Review is not undone by a Draft PR")
        self.assertEqual(task["pr_phase"], "Draft")
        task, _ = self.sync({**self.OPEN, "state": "MERGED", "mergeCommit": {"oid": SHA_M}})
        self.assertEqual(task["stage"], "merged", "forward movement past the manual stage still happens")

    def test_manual_block_survives_until_github_shows_it_cleared(self):
        self.start()
        self.fake_gh({})
        self.sync(self.OPEN)
        self.run_board("task", "demo", "a", "--status", "blocked", "--reason", "Waiting on the DNS cutover",
                       "--next-action", "Joe flips the record")
        # GitHub unchanged, or moving without a new head: the note stays.
        for view in (self.OPEN, {**self.OPEN, "reviewDecision": "APPROVED"}):
            task, _ = self.sync(view)
            self.assertEqual(task["status"], "blocked")
            self.assertEqual(BOARD.blocked_detail(task), ("Waiting on the DNS cutover", "Joe flips the record"))
        # A new head that is itself failing is not the blocker clearing either.
        failing = [{"name": "unit", "conclusion": "FAILURE", "status": "COMPLETED"}]
        task, _ = self.sync({**self.OPEN, "headRefOid": "b" * 40, "statusCheckRollup": failing})
        self.assertEqual(BOARD.blocked_detail(task)[0], "Waiting on the DNS cutover")
        # A new, healthy head is GitHub showing the blocker cleared.
        task, _ = self.sync({**self.OPEN, "headRefOid": "c" * 40})
        self.assertEqual((task["status"], task["pr_phase"]), ("review", "Awaiting review"))
        self.assertNotIn("blocked_reason", task)
        self.assertNotIn("blocked_source", task)

    def test_manual_block_clears_when_the_pr_merges(self):
        self.start()
        self.fake_gh({})
        self.run_board("task", "demo", "a", "--status", "blocked", "--reason", "Hold", "--next-action", "Wait")
        task, _ = self.sync({**self.OPEN, "state": "MERGED", "mergeCommit": {"oid": SHA_M}})
        self.assertEqual((task["status"], task["stage"]), ("done", "merged"))
        self.assertNotIn("blocked_reason", task)

    def test_github_block_clears_itself_when_checks_recover(self):
        self.start()
        self.fake_gh({})
        failing = [{"name": "unit", "conclusion": "FAILURE", "status": "COMPLETED"}]
        task, _ = self.sync({**self.OPEN, "statusCheckRollup": failing})
        self.assertEqual(task["blocked_source"], "github")
        task, _ = self.sync(self.OPEN)
        self.assertEqual(task["status"], "review")
        self.assertIsNone(BOARD.blocked_detail(task))

    def test_updated_at_moves_only_when_derived_state_changes(self):
        self.start()
        self.fake_gh({})
        one = [{"name": "unit", "conclusion": None, "status": "IN_PROGRESS"},
               {"name": "types", "conclusion": None, "status": "QUEUED"}]
        task, _ = self.sync({**self.OPEN, "statusCheckRollup": one})
        entered, updated = task["stage_entered_at"], task["updated_at"]
        # Same state, and a check finishing while others still run: facts refresh, clocks stay.
        self.sync({**self.OPEN, "statusCheckRollup": one})
        progress = [{"name": "unit", "conclusion": "SUCCESS", "status": "COMPLETED"}, one[1]]
        task, _ = self.sync({**self.OPEN, "statusCheckRollup": progress})
        self.assertEqual(task["pr_checks"], "1 pass · 1 pending · 0 fail")
        self.assertEqual((task["updated_at"], task["stage_entered_at"]), (updated, entered))
        self.assertEqual(len(task["stage_history"]), 2)
        # A real change stamps both.
        task, _ = self.sync(self.OPEN)
        self.assertGreater(task["updated_at"], updated)
        self.assertEqual(task["stage_entered_at"], task["updated_at"])

    def test_gh_failure_is_logged_and_last_known_state_kept(self):
        self.start()
        self.fake_gh({})
        good, _ = self.sync(self.OPEN)
        prior = self.read_state("demo")["tasks"]
        self.set_fixture({})  # gh pr view exits 1
        result = self.run_board("render", "demo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("carr-system#42", result.stderr)
        state = self.read_state("demo")
        self.assertEqual(state["tasks"], prior)
        self.assertEqual(state["github_sync"]["failed"][0]["card"], "a")
        self.set_fixture({"view": self.OPEN})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["github_sync"]["failed"], [])

    def test_the_launchd_job_puts_homebrew_on_path(self):
        self.assertIn("/opt/homebrew/bin", LAUNCHD_SCRIPT.read_text())


class AllRepositoriesBoard(BoardCase):
    def pr(self, number, **fields):
        base = {"number": number, "title": f"PR {number}", "body": f"## Summary\nDoes thing {number}.\n",
                "author": {"login": "jbookout"}, "headRefName": f"claude/branch-{number}",
                "headRefOid": f"{number:040d}", "isDraft": False, "createdAt": iso(timedelta(hours=2)),
                "updatedAt": iso(timedelta(minutes=5)), "mergeable": "MERGEABLE", "reviewDecision": "",
                "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}], "comments": [],
                "files": [{"path": "mcp-server/src/index.js"}], "changedFiles": 1,
                "url": f"https://github.com/jbookout/x/pull/{number}"}
        base.update(fields)
        return base

    def fixture_all(self):
        carr = "jbookout/carr-system"
        app = "jbookout/doctorcre-app"
        return {
            "repos": ["jbookout/carr-system", "jbookout/tour-lab"],
            "open": {
                carr: [
                    self.pr(1, isDraft=True, headRefName="codex/draft-work"),
                    self.pr(2, statusCheckRollup=[{"conclusion": "FAILURE", "status": "COMPLETED"}]),
                    self.pr(3, mergeable="CONFLICTING"),
                    self.pr(4, reviewDecision="CHANGES_REQUESTED"),
                    self.pr(5, statusCheckRollup=[{"conclusion": None, "status": "IN_PROGRESS"}]),
                    self.pr(6, updatedAt=iso(timedelta(hours=7))),
                ],
                app: [self.pr(1, title="App one", body="Fixes the phone layout.\n\nMore.")],
            },
            "merged": {
                carr: [self.pr(7, mergedAt=iso(timedelta(hours=1)), mergeCommit={"oid": SHA_M}),
                       self.pr(8, mergedAt=iso(timedelta(hours=1)), mergeCommit={"oid": "8" * 40})],
            },
            "compare": {f"repos/jbookout/carr-system/compare/{SHA_M}...{'8' * 40}": "ahead"},
        }

    def test_builds_every_repository_from_gh_grouped_with_stages(self):
        self.fake_gh(self.fixture_all())
        self.shipped("worker", SHA_M)
        self.run_board("render", "all-repos")
        state = self.read_state("all-repos")
        self.assertEqual(state["kind"], "all-repos")
        self.assertEqual(state["project"], "all-repos")
        repos = [row["repo"] for row in state["repos"]]
        self.assertEqual(repos[:3], ["jbookout/carr-system", "jbookout/doctorcre-app", "jbookout/software-factory"])
        self.assertIn("jbookout/tour-lab", repos)
        tasks = state["tasks"]
        expect = {
            "carr-system-1": ("build", "running", "Draft", False),
            "carr-system-2": ("ci", "blocked", "Checks failing", True),
            "carr-system-3": ("review", "blocked", "Merge conflict", True),
            "carr-system-4": ("review", "blocked", "Changes requested", True),
            "carr-system-5": ("ci", "running", "CI", False),
            "carr-system-6": ("review", "review", "Awaiting review", False),
            "carr-system-7": ("live", "done", "Merged", False),
            "carr-system-8": ("merged", "done", "Merged", False),
            "doctorcre-app-1": ("review", "review", "Awaiting review", False),
        }
        self.assertEqual(set(tasks), set(expect))
        for task_id, (stage, status, phase, blocked) in expect.items():
            with self.subTest(task_id=task_id):
                task = tasks[task_id]
                self.assertEqual(BOARD.task_stage(task), stage)
                self.assertEqual((task["status"], task["pr_phase"]), (status, phase))
                self.assertEqual(BOARD.task_health(task) == "blocked", blocked)
                if blocked:
                    reason, action = BOARD.blocked_detail(task)
                    self.assertTrue(reason and action)
        app = tasks["doctorcre-app-1"]
        self.assertEqual((app["repo"], app["pr"], app["title"]), ("jbookout/doctorcre-app", 1, "App one"))
        self.assertEqual(app["summary"], "Fixes the phone layout.")
        self.assertEqual(app["author"], "jbookout")
        self.assertEqual(app["executor"], "Claude cloud")
        self.assertEqual(app["pr_head"], f"{1:040d}")
        self.assertTrue(app["created_at"])
        self.assertEqual(tasks["carr-system-1"]["executor"], "Codex")
        self.assertEqual(tasks["carr-system-2"]["summary"], "Does thing 2.")
        self.assertTrue(BOARD.is_stale(tasks["carr-system-6"]))
        self.assertFalse(BOARD.is_stale(tasks["carr-system-5"]))
        self.assertIn(SHA_M[:12], tasks["carr-system-7"]["evidence"])
        counts = {row["repo"]: (row["open"], row["merged"]) for row in state["repos"]}
        self.assertEqual(counts["jbookout/carr-system"], (6, 2))
        self.assertEqual(counts["jbookout/doctorcre-app"], (1, 0))
        snapshot = BOARD.board_snapshot(state)
        self.assertEqual(snapshot["kind"], "all-repos")
        self.assertEqual(snapshot["repos"], state["repos"])
        discovery = [c for c in self.gh_calls() if c[0] == "api" and "/issues?" in c[1]]
        self.assertTrue(discovery and "state=closed" in discovery[0][1] and "since=" in discovery[0][1])

    def test_same_pr_number_in_two_repositories_never_collides(self):
        fixture = self.fixture_all()
        fixture["open"] = {"jbookout/carr-system": [self.pr(93, title="CARR 93")],
                           "jbookout/doctorcre-app": [self.pr(93, title="App 93")]}
        fixture["merged"] = {}
        self.fake_gh(fixture)
        self.run_board("render", "all-repos")
        tasks = self.read_state("all-repos")["tasks"]
        self.assertEqual(tasks["carr-system-93"]["title"], "CARR 93")
        self.assertEqual(tasks["doctorcre-app-93"]["title"], "App 93")
        self.assertEqual({(t["repo"], t["pr"]) for t in tasks.values()},
                         {("jbookout/carr-system", 93), ("jbookout/doctorcre-app", 93)})
        self.assertEqual(BOARD.card_key("jbookout/doctorcre-app", 93), "doctorcre-app-93")
        self.assertNotEqual(BOARD.card_key("jbookout/doctorcre-app", 93), BOARD.card_key("jbookout/carr-system", 93))

    def test_one_repository_failing_keeps_its_previous_cards(self):
        fixture = self.fixture_all()
        self.fake_gh(fixture)
        self.run_board("render", "all-repos")
        before = {k: v for k, v in self.read_state("all-repos")["tasks"].items() if k.startswith("doctorcre-app-")}
        self.set_fixture({**fixture, "fail": ["jbookout/doctorcre-app"], "open": {"jbookout/carr-system": []}})
        self.run_board("render", "all-repos")
        state = self.read_state("all-repos")
        after = {k: v for k, v in state["tasks"].items() if k.startswith("doctorcre-app-")}
        self.assertEqual(after, before)
        row = next(r for r in state["repos"] if r["repo"] == "jbookout/doctorcre-app")
        self.assertIn("error", row)
        self.assertNotIn("carr-system-1", state["tasks"])

    def test_without_gh_the_board_is_not_rebuilt_or_wiped(self):
        fixture = self.fixture_all()
        self.fake_gh(fixture)
        self.run_board("render", "all-repos")
        prior = self.read_state("all-repos")
        self.set_fixture({**fixture, "repo_list_fails": True,
                          "fail": list(fixture["open"]) + ["jbookout/software-factory", "jbookout/tour-lab"]})
        result = self.run_board("render", "all-repos", check=False)
        self.assertEqual(result.returncode, 0)
        after = self.read_state("all-repos")
        self.assertEqual(after["tasks"], prior["tasks"])
        self.assertTrue(after["github_sync"]["stale"])

    def test_snapshot_stays_under_the_server_limit(self):
        tasks = {f"carr-system-{n}": {"title": "t" * 200, "summary": "s" * 160, "status": "done", "stage": "live",
                                      "evidence": "e" * 200, "repo": "jbookout/carr-system", "pr": n,
                                      "completed_at": f"2026-09-{1 + n % 28:02d}T00:00:00Z"} for n in range(600)}
        tasks["carr-system-open"] = {"title": "Open", "status": "running", "stage": "build", "repo": "jbookout/carr-system", "pr": 9999}
        snapshot = BOARD.board_snapshot({"project": "all-repos", "kind": "all-repos", "tasks": tasks})
        self.assertIn("carr-system-open", snapshot["tasks"])
        self.assertLessEqual(BOARD.snapshot_size(snapshot), BOARD.SNAPSHOT_LIMIT)
        self.assertLess(len(snapshot["tasks"]), len(tasks))
        self.assertEqual(snapshot["omitted"]["live"], len(tasks) - len(snapshot["tasks"]))

    def test_render_publish_builds_and_publishes_the_system_board(self):
        events = []
        args = type("Args", (), {"project": "all-repos", "publish": True})()
        with patch.object(BOARD, "build_all_repos", lambda: events.append(("build", "all-repos"))), \
             patch.object(BOARD, "render", lambda project: events.append(("render", project))), \
             patch.object(BOARD, "publish_board", lambda project: events.append(("publish", project))):
            BOARD.command_render(args)
        self.assertEqual(events, [("build", "all-repos"), ("publish", "all-repos")])


class PublishAndAnswers(BoardCase):
    def test_publish_uses_remote_version_and_checks_readback(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:session-1", "--choice", "Proceed", "--choice", "Hold")
        calls = []
        remote = {"snapshot": None, "questions": []}

        def caller(verb, args):
            calls.append((verb, args))
            if verb == "read-progress-board":
                return {"ok": True, **remote}
            if verb == "publish-board-snapshot":
                remote["snapshot"] = {"board_id": "demo", "version": 1, "snapshot_json": args["snapshot"]}
                return {"ok": True, "snapshot": remote["snapshot"]}
            if verb == "ask-board-question":
                remote["questions"] = [{"question_id": "q1", "revision": 1, "prompt": args["prompt"],
                                        "choices": args["choices"], "allow_free_text": args["allow_free_text"],
                                        "default_answer": args["default_answer"], "asker_ref": args["asker_ref"]}]
                return {"ok": True, "question": remote["questions"][0]}
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            result = BOARD.publish_board("demo")
        self.assertEqual(result["snapshot_version"], 1)
        self.assertEqual([verb for verb, _ in calls], ["read-progress-board", "publish-board-snapshot",
                                                       "ask-board-question", "read-progress-board"])
        self.assertEqual(calls[1][1]["base_version"], 0)
        self.assertEqual(calls[1][1]["snapshot"]["schema"], "carr-progress-board.v2")
        self.assertEqual(calls[2][1]["asker_ref"], "one-shot:session-1")
        self.assertNotIn("answer", calls[1][1]["snapshot"])

    def test_legacy_question_revision_retains_free_text_mode(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Original?", "--default", "Proceed")
        state = self.read_state("demo")
        legacy = state["questions"]["q1"]
        for field in ("choices", "free_text", "asker_ref", "revision", "history"):
            legacy.pop(field)
        self.write_state("demo", state)
        self.run_board("ask", "demo", "q1", "--question", "Revised?", "--default", "Proceed")
        old = self.read_state("demo")["questions"]["q1"]["history"][0]
        fields = BOARD.question_revision(old, "demo")
        self.assertEqual(fields["choices"], [])
        self.assertIs(fields["allow_free_text"], True)
        self.assertEqual(fields["asker_ref"], "orchestrator:demo")

    def test_publish_normalizes_question_and_default_on_repeat(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "  Choose route?  ", "--default", "  Proceed  ")
        remote = {"snapshot": None, "questions": []}
        writes = []

        def caller(verb, args):
            if verb == "read-progress-board":
                return {"ok": True, **remote}
            writes.append(verb)
            if verb == "publish-board-snapshot":
                remote["snapshot"] = {"version": 1, "snapshot_json": args["snapshot"]}
                return {"ok": True, "snapshot": remote["snapshot"]}
            if verb == "ask-board-question":
                remote["questions"] = [{"question_id": "q1", "revision": 1,
                                        "prompt": args["prompt"].strip(), "choices": args["choices"],
                                        "allow_free_text": args["allow_free_text"],
                                        "default_answer": args["default_answer"].strip(),
                                        "asker_ref": args["asker_ref"]}]
                return {"ok": True, "question": remote["questions"][0]}
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            BOARD.publish_board("demo")
            BOARD.publish_board("demo")
        self.assertEqual(writes, ["publish-board-snapshot", "ask-board-question"])
        self.assertEqual(remote["questions"][0]["prompt"], "Choose route?")
        self.assertEqual(remote["questions"][0]["default_answer"], "Proceed")

    def test_poller_rejects_answer_for_other_board_or_question_before_inbox_or_ack(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:shared")
        base = {"id": "11111111-1111-4111-8111-111111111111", "cursor": "7",
                "board_id": "demo", "question_id": "q1", "question_revision": 1,
                "asker_ref": "one-shot:shared", "answer_text": "Hold", "version": 1, "status": "Sent"}
        for mismatch in ({"board_id": "other"}, {"question_id": "q2"}, {"question_revision": 2}):
            calls = []

            def caller(verb, args):
                calls.append(verb)
                if verb == "read-board-answers":
                    return {"ok": True, "answers": [{**base, **mismatch}]}
                raise AssertionError(verb)

            with self.subTest(mismatch=mismatch), patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), \
                 patch.object(BOARD, "call_verb", caller):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    BOARD.poll_board_answers("demo")
                self.assertFalse((self.root / "boards" / "demo-answers.jsonl").exists())
                self.assertEqual(calls, ["read-board-answers"])

    def test_poller_durably_records_then_acknowledges_and_replays_pending_ack(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:session-1")
        answer = {"id": "11111111-1111-4111-8111-111111111111", "cursor": "7", "board_id": "demo",
                  "question_id": "q1", "question_revision": 1, "asker_ref": "one-shot:session-1",
                  "answer_text": "Hold", "answered_by": "joe", "version": 1, "status": "Sent"}
        failed_once = False
        calls = []

        def caller(verb, args):
            nonlocal failed_once
            calls.append((verb, args))
            if verb == "read-board-answers":
                return {"ok": True, "answers": [answer] if args["after_cursor"] < 7 else [], "next_cursor": 7}
            if verb == "acknowledge-board-answer":
                inbox = (self.root / "boards" / "demo-answers.jsonl").read_text()
                self.assertIn(answer["id"], inbox, "inbox must be durable before Received")
                if not failed_once:
                    failed_once = True
                    raise RuntimeError("temporary refusal")
                return {"ok": True, "answer": {**answer, "version": 2, "status": "Received"}}
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            with self.assertRaises(RuntimeError):
                BOARD.poll_board_answers("demo")
            BOARD.poll_board_answers("demo")
        events = [json.loads(line) for line in (self.root / "boards" / "demo-answers.jsonl").read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["answer", "ack"])
        self.assertEqual([args["after_cursor"] for verb, args in calls if verb == "read-board-answers"], [0, 6, 7])
        self.assertEqual(len([verb for verb, _ in calls if verb == "acknowledge-board-answer"]), 2)

    def test_poller_recovers_received_ack_after_crash_before_local_receipt(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:session-1")
        answer = {"id": "11111111-1111-4111-8111-111111111111", "cursor": "7", "board_id": "demo",
                  "question_id": "q1", "question_revision": 1, "asker_ref": "one-shot:session-1",
                  "answer_text": "Hold", "answered_by": "joe", "version": 1, "status": "Sent"}

        def caller(verb, args):
            if verb == "read-board-answers":
                return {"ok": True, "answers": [{**answer, "version": 2, "status": "Received"}]
                        if args["after_cursor"] == 6 else [], "next_cursor": 7}
            if verb == "acknowledge-board-answer":
                raise RuntimeError("board_version_conflict")
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            BOARD.append_answer_event("demo", {"kind": "answer", "answer": answer})
            BOARD.poll_board_answers("demo")
        events = [json.loads(line) for line in (self.root / "boards" / "demo-answers.jsonl").read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["answer", "ack"])

    def test_existing_launchd_render_runs_publish_poll_and_system_board_without_a_model(self):
        events = []
        args = type("Args", (), {"project": "carr-v5", "publish": False})()
        with patch.object(BOARD, "render", lambda project: events.append(("render", project))), \
             patch.object(BOARD, "publish_board", lambda project: events.append(("publish", project))), \
             patch.object(BOARD, "poll_board_answers", lambda project: events.append(("poll", project))), \
             patch.object(BOARD, "build_all_repos", lambda: events.append(("build", "all-repos"))):
            BOARD.command_render(args)
        self.assertEqual(events, [("render", "carr-v5"), ("publish", "carr-v5"), ("poll", "carr-v5"),
                                  ("build", "all-repos"), ("publish", "all-repos")])

    def test_system_board_failure_is_logged_and_last_known_state_published(self):
        events = []
        args = type("Args", (), {"project": "carr-v5", "publish": False})()

        def broken():
            raise RuntimeError("gh unavailable")
        (self.root / "boards").mkdir(parents=True, exist_ok=True)
        (self.root / "boards" / "all-repos.json").write_text(json.dumps({"project": "all-repos", "tasks": {}}))
        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), \
             patch.object(BOARD, "render", lambda project: events.append(("render", project))), \
             patch.object(BOARD, "publish_board", lambda project: events.append(("publish", project))), \
             patch.object(BOARD, "poll_board_answers", lambda project: events.append(("poll", project))), \
             patch.object(BOARD, "build_all_repos", broken), \
             patch("sys.stderr", new_callable=io.StringIO) as err:
            BOARD.command_render(args)
        self.assertEqual(events, [("render", "carr-v5"), ("publish", "carr-v5"), ("poll", "carr-v5"),
                                  ("publish", "all-repos")])
        self.assertIn("gh unavailable", err.getvalue())

class CardColumns(unittest.TestCase):
    """Addition 14: finished cards never sit in Building; retired cards leave the pipeline."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["PROGRESS_BOARD_ROOT"] = str(self.root)
        self.env["PROGRESS_BOARD_SKIP_GH"] = "1"
        self.env["PROGRESS_BOARD_LOCAL_ONLY"] = "1"
        self.board("init", "demo", "--title", "Demo")

    def tearDown(self):
        self.tempdir.cleanup()

    def board(self, *args, check=True):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=REPO, env=self.env,
                              text=True, capture_output=True, check=check)

    def state(self):
        return json.loads((self.root / "boards" / "demo.json").read_text())

    def test_done_without_a_pr_is_live(self):
        self.board("task", "demo", "doc", "--title", "Write the runbook", "--status", "done",
                   "--executor", "Codex", "--note", "Shipped as PR #1234 per the thread")
        task = self.state()["tasks"]["doc"]
        self.assertEqual(BOARD.task_stage(task), "live")
        # Stored explicitly so the app, which derives stage itself, agrees.
        self.assertEqual((task["status"], task["stage"]), ("done", "live"))
        self.assertTrue(task["evidence"].strip())
        self.assertTrue(task["completed_at"])
        # The note names a PR, but nothing is guessed from it.
        self.assertIsNone(task.get("pr"))
        self.assertEqual(BOARD.board_snapshot(self.state())["tasks"]["doc"]["stage"], "live")

    def test_legacy_done_card_without_a_pr_moves_to_live_on_render(self):
        state = self.state()
        state["tasks"]["old"] = {"title": "Old", "status": "done", "executor": "Codex",
                                 "created_at": "2026-09-28T10:00:00+00:00", "updated_at": "2026-09-28T11:00:00+00:00"}
        (self.root / "boards" / "demo.json").write_text(json.dumps(state))
        self.board("render", "demo")
        task = self.state()["tasks"]["old"]
        self.assertEqual((task["stage"], task["completed_at"]), ("live", "2026-09-28T11:00:00+00:00"))
        self.assertEqual(task["updated_at"], "2026-09-28T11:00:00+00:00")
        snapshot = BOARD.board_snapshot(self.state())
        self.assertEqual(snapshot["tasks"]["old"]["stage"], "live")

    def test_done_with_a_merged_pr_not_yet_released_stays_merged(self):
        self.board("task", "demo", "fix", "--title", "Fix", "--status", "done", "--executor", "Codex", "--pr", "42")
        state = self.state()
        state["tasks"]["fix"]["pr_phase"] = "Merged"
        (self.root / "boards" / "demo.json").write_text(json.dumps(state))
        self.board("render", "demo")
        task = self.state()["tasks"]["fix"]
        self.assertEqual(BOARD.task_stage(task), "merged")
        self.assertNotIn("evidence", task)
        self.assertEqual(BOARD.task_stage(BOARD.board_snapshot(self.state())["tasks"]["fix"]), "merged")

    def test_done_with_an_unmerged_pr_is_never_building(self):
        self.board("task", "demo", "open", "--title", "Open", "--status", "done", "--executor", "Codex", "--pr", "43")
        self.assertNotEqual(BOARD.task_stage(self.state()["tasks"]["open"]), "build")

    def test_failed_card_leaves_the_pipeline_and_shows_in_history_with_its_reason(self):
        self.board("task", "demo", "bad", "--title", "Bad attempt", "--status", "failed", "--executor", "Grok",
                   "--reason", "Runner crashed on the migration")
        self.assert_retired("bad", "failed", "Runner crashed on the migration")

    def test_superseded_card_leaves_the_pipeline_and_shows_in_history_with_its_reason(self):
        self.board("task", "demo", "old-plan", "--title", "Old plan", "--status", "running", "--executor", "Codex",
                   "--pr", "44")
        self.board("task", "demo", "old-plan", "--status", "superseded", "--reason", "Replaced by carr-system PR 1420")
        self.assert_retired("old-plan", "superseded", "Replaced by carr-system PR 1420")

    def test_retiring_a_card_needs_a_reason(self):
        result = self.board("task", "demo", "x", "--title", "X", "--status", "superseded", "--executor", "Codex",
                            check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--reason", result.stderr)

    def test_legacy_failed_card_without_a_reason_still_leaves_the_pipeline(self):
        state = self.state()
        state["tasks"]["legacy"] = {"title": "Legacy", "status": "failed", "executor": "Codex",
                                    "note": "CI never went green", "updated_at": "2026-09-28T11:00:00+00:00"}
        (self.root / "boards" / "demo.json").write_text(json.dumps(state))
        self.board("render", "demo")
        self.assert_retired("legacy", "failed", "CI never went green")

    def assert_retired(self, task_id, status, reason):
        state = self.state()
        self.assertEqual(state["tasks"][task_id]["status"], status)
        self.assertTrue(BOARD.is_retired(state["tasks"][task_id]))
        self.assertEqual(BOARD.retired_reason(state["tasks"][task_id]), reason)
        snapshot = BOARD.board_snapshot(state)
        self.assertNotIn(task_id, snapshot["tasks"])
        self.assertEqual(snapshot["history"][task_id]["reason"], reason)
        self.assertEqual(snapshot["history"][task_id]["status"], status)



def load_release_pipeline():
    spec = importlib.util.spec_from_file_location("board_test_release_pipeline", REPO / "ops" / "release-pipeline.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReviewRound1420(BoardCase):
    """jbookout/carr-system#1420 review findings 1-11, each reproduced."""

    OPEN = GitHubSync.OPEN
    pr = AllRepositoriesBoard.pr

    def spy(self):
        calls = []
        remote = {"snapshot": None, "questions": []}

        def caller(verb, args):
            calls.append(verb)
            if verb == "read-progress-board":
                return {"ok": True, **copy.deepcopy(remote)}
            if verb == "publish-board-snapshot":
                version = (remote["snapshot"] or {}).get("version", 0) + 1
                remote["snapshot"] = {"version": version, "snapshot_json": args["snapshot"]}
                return {"ok": True, "snapshot": remote["snapshot"]}
            raise AssertionError(verb)
        return calls, remote, caller

    def in_process(self, **extra):
        env = {"PROGRESS_BOARD_ROOT": str(self.root), "PROGRESS_BOARD_SKIP_PROBE": "1",
               "PROGRESS_BOARD_RELEASES": str(self.root / "releases.jsonl"), **extra}
        return patch.dict(os.environ, env)

    # 1 ── concurrent writers
    def test_1_superseded_project_render_cannot_replace_newer_review_evidence(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42")
        old = copy.deepcopy(self.OPEN)
        old["comments"] = [{"author": {"login": "jbookout"}, "authorAssociation": "OWNER",
                            "body": "APPROVE\nReviewed-SHA: " + SHA_A,
                            "createdAt": "2026-10-03T10:00:00Z"}]
        new = copy.deepcopy(old)
        new["headRefOid"] = "b" * 40
        new["comments"][0]["body"] = "REVIEW: BLOCKED\nReviewed-SHA: " + "b" * 40
        completed = []

        def fetch(number, repo):
            # A has read head A but pauses before applying it. B finishes first.
            with patch.object(BOARD, "fetch_pr", return_value=(new, None)):
                BOARD.render("demo")
            completed.append(self.read_state("demo"))
            return old, None

        with self.in_process(PROGRESS_BOARD_SKIP_GH=""), patch.object(BOARD, "fetch_pr", fetch):
            BOARD.render("demo")
        winner = completed[0]
        self.assertEqual(winner["tasks"]["a"]["review_verdict"], "BLOCK")
        after = self.read_state("demo")
        self.assertEqual(after["tasks"], winner["tasks"])
        self.assertEqual(after["github_sync"], winner["github_sync"])

    def test_1_overlapping_writes_use_their_own_temporary_files(self):
        real_replace = os.replace
        nested = []

        def replace(src, dst):
            if not nested:
                nested.append(True)
                BOARD.write_json({"project": "demo", "title": "B", "tasks": {}})
            real_replace(src, dst)
        with self.in_process(), patch.object(BOARD.os, "replace", replace):
            BOARD.write_json({"project": "demo", "title": "A", "tasks": {}})
        self.assertEqual(self.read_state("demo")["title"], "A")
        self.assertEqual([p.name for p in (self.root / "boards").iterdir() if p.suffix == ".tmp"], [])

    def test_1_superseded_all_repos_render_keeps_newer_cards_and_sync_time(self):
        repo = "jbookout/carr-system"
        old = self.pr(42, comments=[{
            "author": {"login": "jbookout"}, "authorAssociation": "OWNER",
            "body": "APPROVE\nReviewed-SHA: " + f"{42:040d}",
            "createdAt": "2026-10-03T10:00:00Z"}])
        new = copy.deepcopy(old)
        new["headRefOid"] = "b" * 40
        new["comments"][0]["body"] = "REVIEW: BLOCKED\nReviewed-SHA: " + "b" * 40
        completed = []

        def read(name, since):
            with patch.object(BOARD, "read_repository", return_value=([new], [])):
                BOARD.build_all_repos()
            completed.append(self.read_state("all-repos"))
            return [old], []

        with self.in_process(), \
             patch.object(BOARD, "list_repositories", return_value=[repo]), \
             patch.object(BOARD, "read_repository", side_effect=read):
            BOARD.build_all_repos()
        winner = completed[0]
        self.assertEqual(winner["tasks"]["carr-system-42"]["review_verdict"], "BLOCK")
        self.assertEqual(self.read_state("all-repos"), winner)

    def test_1_a_note_written_while_render_reads_github_survives(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex", "--pr", "42")
        wrote = []

        def fetch(number, repo):
            if not wrote:
                wrote.append(True)
                BOARD.main(["note", "demo", "--text", "written while GitHub was being read"])
            return copy.deepcopy(self.OPEN), None
        with self.in_process(PROGRESS_BOARD_LOCAL_ONLY="1", PROGRESS_BOARD_SKIP_GH=""), \
             patch.object(BOARD, "fetch_pr", fetch), patch("sys.stderr", new_callable=io.StringIO):
            BOARD.render("demo")
        state = self.read_state("demo")
        self.assertEqual(state["notes"][0]["text"], "written while GitHub was being read")
        self.assertEqual(state["tasks"]["a"]["pr_phase"], "Awaiting review")

    def test_1_parallel_mutations_and_inits_are_serialized(self):
        self.run_board("init", "demo", "--title", "Demo")
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "note", "demo", "--text", f"note {n}"],
                                  cwd=REPO, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                 for n in range(8)]
        self.assertEqual([proc.wait() for proc in procs], [0] * 8)
        self.assertEqual(sorted(note["text"] for note in self.read_state("demo")["notes"]),
                         sorted(f"note {n}" for n in range(8)))
        inits = [subprocess.Popen([sys.executable, str(SCRIPT), "init", "race", "--title", f"T{n}"],
                                  cwd=REPO, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                 for n in range(6)]
        codes = [proc.wait() for proc in inits]
        self.assertEqual(codes.count(0), 1, codes)
        winner = codes.index(0)
        self.assertEqual(self.read_state("race")["title"], f"T{winner}")

    # 2 ── complete enumeration
    def test_2_every_recent_merged_pr_is_listed_with_true_counts(self):
        merged = [self.pr(n, mergedAt=iso(timedelta(hours=1)), mergeCommit={"oid": f"{n:040x}"})
                  for n in range(1, 230)]
        self.fake_gh({"repos": [], "open": {}, "merged": {"jbookout/carr-system": merged}})
        self.run_board("render", "all-repos")
        state = self.read_state("all-repos")
        self.assertEqual(len([k for k in state["tasks"] if k.startswith("carr-system-")]), 229)
        row = next(r for r in state["repos"] if r["repo"] == "jbookout/carr-system")
        self.assertEqual((row["open"], row["merged"]), (0, 229))

    def test_2_a_list_that_fills_the_cap_is_an_incomplete_read(self):
        from urllib.parse import parse_qs, urlparse
        rows = [{"number": n} for n in range(8)]
        def page(args, timeout=30):
            params = parse_qs(urlparse(args[1]).query)
            number, size = int(params["page"][0]), int(params["per_page"][0])
            return rows[(number - 1) * size:number * size]
        with patch.object(BOARD, "REST_PAGE_SIZE", 2), patch.object(BOARD, "REST_MAX_ROWS", 4), \
             patch.object(BOARD, "gh_json", page):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                BOARD.rest_rows("repos/jbookout/carr-system/pulls?state=open")
        with patch.object(BOARD, "REST_PAGE_SIZE", 2), patch.object(BOARD, "REST_MAX_ROWS", 16), \
             patch.object(BOARD, "gh_json", page):
            self.assertEqual(len(BOARD.rest_rows("repos/jbookout/carr-system/pulls?state=open")), 8)

    # 3 ── the whole payload fits the server contract
    def test_3_the_complete_snapshot_is_measured_and_fitted(self):
        tasks = {f"t{n}": {"title": "T" * 200, "summary": "S" * 160, "evidence": "E" * 200, "status": "done",
                           "stage": "live", "executor": "Codex gpt-6-sol high", "updated_at": "2026-09-29T00:00:00Z",
                           "completed_at": f"2026-09-{1 + n % 28:02d}T00:00:00Z",
                           "stage_history": [{"stage": "live", "entered_at": "2026-09-29T00:00:00Z"}]}
                 for n in range(225)}
        state = {"project": "demo", "tasks": tasks,
                 "notes": [{"text": "N" * 2000, "created_at": "2026-09-29T00:00:00Z"}] * 50}
        snapshot = BOARD.board_snapshot(state)
        text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 262144)
        self.assertGreater(snapshot["omitted"]["live"], 0)
        self.assertEqual(len(snapshot["notes"]), 50)
        self.assertEqual(BOARD.snapshot_size({"t": "✦😀"}), len('{"t":"✦"}') + 2)

    def test_3_a_board_that_cannot_fit_is_refused_not_published(self):
        tasks = {f"t{n}": {"title": "T" * 1000, "status": "running", "executor": "Codex",
                           "updated_at": "2026-09-29T00:00:00Z"} for n in range(300)}
        with self.assertRaises(BOARD.SnapshotTooLarge):
            BOARD.board_snapshot({"project": "demo", "tasks": tasks})
        self.run_board("init", "demo", "--title", "Demo")
        state = self.read_state("demo")
        state["tasks"] = tasks
        self.write_state("demo", state)
        calls, _, caller = self.spy()
        with self.in_process(), patch.object(BOARD, "call_verb", caller):
            with self.assertRaises(BOARD.SnapshotTooLarge):
                BOARD.publish_board("demo")
        self.assertNotIn("publish-board-snapshot", calls)

    # 4 ── one review interpretation, shared with the release pipeline
    def test_4_same_account_and_review_blocked_verdicts_count_on_project_cards(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex", "--pr", "42")
        self.fake_gh({})

        def comment(body):
            return {"author": {"login": "builder"}, "authorAssociation": "OWNER", "body": body,
                    "createdAt": "2026-09-30T10:00:00Z"}
        for body in ("BLOCK\nReviewed-SHA: " + SHA_A, "REVIEW: BLOCKED\nReviewed-SHA: " + SHA_A + "\n\n1. finding"):
            with self.subTest(body=body.splitlines()[0]):
                self.set_fixture({"view": {**self.OPEN, "comments": [comment(body)]}})
                self.run_board("render", "demo")
                task = self.read_state("demo")["tasks"]["a"]
                self.assertEqual((task["pr_phase"], task["review_verdict"]), ("Review blocked", "BLOCK"))
        self.set_fixture({"view": {**self.OPEN, "comments": [comment("APPROVE\nReviewed-SHA: " + SHA_A)]}})
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["pr_phase"], task["review_verdict"]), ("Ready to merge", "APPROVE"))

    def test_4_all_repos_cards_read_review_comments(self):
        block = {"author": {"login": "jbookout"}, "authorAssociation": "OWNER",
                 "body": "REVIEW: BLOCKED\nReviewed-SHA: " + f"{1:040d}", "createdAt": "2026-09-30T10:00:00Z"}
        approve = {**block, "body": "APPROVE\nReviewed-SHA: " + f"{2:040d}"}
        self.fake_gh({"repos": [], "merged": {},
                      "open": {"jbookout/carr-system": [self.pr(1, comments=[block]), self.pr(2, comments=[approve])]}})
        self.run_board("render", "all-repos")
        tasks = self.read_state("all-repos")["tasks"]
        self.assertEqual((tasks["carr-system-1"]["pr_phase"], tasks["carr-system-1"]["review_verdict"]),
                         ("Review blocked", "BLOCK"))
        self.assertEqual((tasks["carr-system-2"]["pr_phase"], tasks["carr-system-2"]["review_verdict"]),
                         ("Ready to merge", "APPROVE"))
        comments_calls = [c for c in self.gh_calls() if c[0] == "api" and "/comments?" in c[1]]
        self.assertEqual(len(comments_calls), 2)

    def test_4_board_and_release_pipeline_agree_on_every_verdict_shape(self):
        pipeline = load_release_pipeline()
        cfg = json.loads((REPO / "ops" / "config" / "release-pipeline.v1.json").read_text())["worker"]
        bodies = ["APPROVE\nReviewed-SHA: " + SHA_A, "APPROVE\nReviewed-SHA: " + "b" * 40, "APPROVE",
                  "BLOCK\nNeeds work", "REVIEW: BLOCKED\nReviewed-SHA: " + SHA_A, "Independent review: pass",
                  "independent review: FAIL", "Looks fine to me", "APPROVE\nReviewed-SHA: " + SHA_A + "\nReviewed-SHA: " + SHA_A]
        for association in ("OWNER", "NONE"):
            for body in bodies:
                with self.subTest(association=association, body=body):
                    comments = [{"author": {"login": "someone"}, "authorAssociation": association, "body": body,
                                 "createdAt": "2026-09-30T10:00:00Z"}]
                    rest = [{"user": {"login": "someone"}, "author_association": association, "body": body,
                             "created_at": "2026-09-30T10:00:00Z", "id": 1}]
                    last = pipeline.deciding_verdict(rest, cfg)
                    if last is None:
                        expected = "Not recorded"
                    elif pipeline.verdict(last["body"], cfg) == "block":
                        expected = "BLOCK"
                    else:
                        expected = "APPROVE" if pipeline.reviewed_header_sha(last["body"]) == SHA_A else "Not recorded"
                    self.assertEqual(BOARD.review_verdict({"headRefOid": SHA_A, "comments": comments},
                                                          "jbookout/carr-system"), expected)
        self.assertEqual(pipeline.verdict("REVIEW: BLOCKED", cfg), "block")

    # 5 ── the scheduled job runs from the repository
    def test_5_migration_reloads_and_verifies_the_existing_launchagent(self):
        spec = importlib.util.spec_from_file_location("board_installer", REPO / "ops/config-as-code.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        repo = self.root / "checkout"
        (repo / "ops").mkdir(parents=True)
        (repo / "ops/progress-board-render.sh").write_text(LAUNCHD_SCRIPT.read_text())
        (repo / ".venv/bin").mkdir(parents=True)
        python = repo / ".venv/bin/python"
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        agents = self.root / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        dest = agents / "local.carr-progress-board.plist"
        old = {"Label": "local.carr-progress-board", "ProgramArguments": ["/bin/zsh", "old/render.sh"],
               "StartInterval": 120, "RunAtLoad": True, "StandardOutPath": "board.log"}
        dest.write_bytes(plistlib.dumps(old))
        calls = []
        real_run = subprocess.run
        def install(filename, path, body, matches):
            calls.append((filename, path, matches))
            Path(path).write_text(body)
            return "loaded"
        desired = ["/bin/bash", str(repo / "ops/progress-board-render.sh")]
        registered = subprocess.CompletedProcess([], 0, "arguments = {\n" + "\n".join(desired) + "\n}\n")
        with patch.object(installer, "REPO", str(repo)), patch.object(installer, "HOME", str(self.root)), \
             patch.object(installer, "install_launchd_plist", side_effect=install), \
             patch.object(installer.subprocess, "run", side_effect=lambda argv, **kwargs: real_run(argv, **kwargs) if argv[0] == "git" else registered) as run:
            self.assertEqual(installer.cmd_install_progress_board(False), 1)
            self.assertEqual(plistlib.loads(dest.read_bytes()), old)
            self.assertEqual(installer.cmd_install_progress_board(True), 0)
            actual = plistlib.loads(dest.read_bytes())
            self.assertEqual(actual["ProgramArguments"], desired)
            self.assertEqual(actual["WorkingDirectory"], str(repo))
            self.assertEqual(actual["StartInterval"], old["StartInterval"])
            self.assertEqual(actual["StandardOutPath"], old["StandardOutPath"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(installer.cmd_install_progress_board(False), 0)
            stale = subprocess.CompletedProcess([], 0, "arguments = {\n/bin/zsh\nold/render.sh\n}\n")
            run.side_effect = lambda argv, **kwargs: real_run(argv, **kwargs) if argv[0] == "git" else stale
            self.assertEqual(installer.cmd_install_progress_board(False), 1)
            # Matching disk bytes cannot hide a stale registered definition.
            observations = iter([stale, registered])
            run.side_effect = lambda argv, **kwargs: real_run(argv, **kwargs) if argv[0] == "git" else next(observations)
            self.assertEqual(installer.cmd_install_progress_board(True), 0)
            self.assertFalse(calls[-1][2])

    def test_5_feature_checkout_migration_is_refused_before_install(self):
        spec = importlib.util.spec_from_file_location("board_installer_premerge", REPO / "ops/config-as-code.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with patch.object(installer, "REPO", str(self.root / "canonical")), \
             patch.object(installer, "install_launchd_plist") as install:
            self.assertEqual(installer.cmd_install_progress_board(True, repo=str(self.root / "feature")), 1)
            install.assert_not_called()

    def test_5_migration_refuses_missing_checkout_wrapper_without_writing(self):
        spec = importlib.util.spec_from_file_location("board_installer_missing", REPO / "ops/config-as-code.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with patch.object(installer, "REPO", str(self.root / "not-delivered")), \
             patch.object(installer, "install_launchd_plist") as install:
            self.assertEqual(installer.cmd_install_progress_board(True), 1)
            install.assert_not_called()

    def test_5_scheduled_wrapper_refuses_a_missing_repository_interpreter(self):
        repo = self.root / "no-venv"
        (repo / "ops").mkdir(parents=True)
        wrapper = repo / "ops/progress-board-render.sh"
        wrapper.write_text(LAUNCHD_SCRIPT.read_text())
        result = subprocess.run(["/bin/bash", str(wrapper)], cwd="/", capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repository interpreter unavailable", result.stderr)

    def test_5_the_wrapper_binds_the_repository_root_and_its_interpreter(self):
        repo = self.root / "repo"
        (repo / "ops").mkdir(parents=True)
        (repo / "tools").mkdir()
        (repo / ".venv" / "bin").mkdir(parents=True)
        wrapper = repo / "ops" / "progress-board-render.sh"
        wrapper.write_text(LAUNCHD_SCRIPT.read_text())
        wrapper.chmod(0o755)
        record = self.root / "invocation.json"
        python = repo / ".venv" / "bin" / "python"
        python.write_text("#!/bin/sh\nexec " + sys.executable + " - \"$@\" <<'PY'\n"
                          "import json, os, sys\n"
                          f"open({str(record)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(), "
                          "'root': os.environ.get('CARR_REPO_ROOT'), 'path': os.environ['PATH']}))\n"
                          "PY\n")
        python.chmod(0o755)
        subprocess.run([str(wrapper)], cwd="/", env={"PATH": "/usr/bin:/bin", "HOME": str(self.root)},
                       check=True, capture_output=True, text=True)
        seen = json.loads(record.read_text())
        self.assertEqual(seen["argv"], ["tools/progress_board.py", "render", "carr-v5", "--publish"])
        self.assertEqual(Path(seen["cwd"]).resolve(), repo.resolve())
        self.assertEqual(Path(seen["root"]).resolve(), repo.resolve())
        self.assertIn("/opt/homebrew/bin", seen["path"])
        self.assertNotIn("cp ", LAUNCHD_SCRIPT.read_text())

    def test_5_an_extracted_copy_reads_release_lanes_from_the_bound_root_or_says_it_cannot(self):
        copy_dir = self.root / "extracted" / "tools"
        copy_dir.mkdir(parents=True)
        (copy_dir / "progress_board.py").write_text(SCRIPT.read_text())
        probe = ("import importlib.util, sys\n"
                 f"s = importlib.util.spec_from_file_location('b', {str(copy_dir / 'progress_board.py')!r})\n"
                 "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
                 "print(sorted(m.release_lanes()))\n")
        bound = subprocess.run([sys.executable, "-c", probe], env={**self.env, "CARR_REPO_ROOT": str(REPO)},
                               capture_output=True, text=True, check=True)
        self.assertEqual(bound.stdout.strip(), "['app', 'worker']")
        env = {k: v for k, v in self.env.items() if k != "CARR_REPO_ROOT"}
        unbound = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(unbound.stdout.strip(), "[]")
        self.assertIn("release pipeline config unreadable", unbound.stderr)

    # 6 ── Live only for what the lane deploys
    def test_6_a_release_does_not_make_undeployed_local_changes_live(self):
        # Project cards: only a declared worker/app target completes from a
        # release (#1439); a local tool or LaunchAgent declares workstation.
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex",
                       "--pr", "42", "--delivery-target", "workstation")
        merged = {**self.OPEN, "state": "MERGED", "mergeCommit": {"oid": SHA_M},
                  "files": [{"path": "tools/progress_board.py"}], "changedFiles": 1}
        self.shipped("worker", SHA_M)
        self.fake_gh({"view": merged})
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "merged")
        self.assertIn("measured evidence", task["release_wait"])
        self.assertNotIn("evidence", task)
        # All-repos cards carry no delivery target: the lane must deploy every
        # path the PR changed.
        release = {"sha": SHA_M, "lane": "worker", "ts": "2026-09-29T12:00:00+00:00", "source": "releases.jsonl"}
        for files, stage, wait in (
                (["tools/progress_board.py", "ops/launchd/com.carr.x.plist"], "merged", "outside the worker release paths"),
                (None, "merged", "changed-file list"),
                (["docs/runbook.md"], "merged", "outside the worker release paths"),
                (["mcp-server/src/board-answers.js", "mcp-server/test/x.test.mjs", "mcp-server/README.md"], "live", None)):
            with self.subTest(files=files):
                card = {"status": "done", "stage": "merged", "merge_sha": SHA_M, "pr": 7, "repo": "jbookout/carr-system"}
                with patch.dict(os.environ, self.env), patch.object(BOARD, "latest_release", return_value=release):
                    BOARD.auto_live(card, "jbookout/carr-system", iso(), files)
                self.assertEqual(card["stage"], stage)
                if wait:
                    self.assertIn(wait, card["release_wait"])
                    self.assertIn("operational receipt", card["release_wait"])
                    self.assertNotIn("evidence", card)
        truncated = {**merged, "files": [{"path": "mcp-server/src/a.js"}], "changedFiles": 150}
        self.assertIsNone(BOARD.changed_paths(truncated))

    # 7 ── malformed list rows are a failed read
    def test_7_malformed_rows_keep_the_previous_cards_and_report_the_error(self):
        fixture = AllRepositoriesBoard.fixture_all(self)
        self.fake_gh(fixture)
        self.run_board("render", "all-repos")
        before = {k: v for k, v in self.read_state("all-repos")["tasks"].items() if k.startswith("carr-system-")}
        self.set_fixture({**fixture, "open": {**fixture["open"], "jbookout/carr-system": [{}]},
                          "merged": {"jbookout/carr-system": [{}]}})
        self.run_board("render", "all-repos")
        state = self.read_state("all-repos")
        self.assertEqual({k: v for k, v in state["tasks"].items() if k.startswith("carr-system-")}, before)
        row = next(r for r in state["repos"] if r["repo"] == "jbookout/carr-system")
        self.assertIn("malformed", row["error"])
        self.assertEqual((row["open"], row["merged"]), (6, 2))
        self.assertIn("jbookout/carr-system", [f["repo"] for f in state["github_sync"]["failed"]])

    # 8 ── a malformed probe is unknown evidence
    def test_8_a_malformed_release_probe_leaves_the_release_unverified(self):
        probes = [{"git_sha": "a" * 40}, {"git_sha": {"value": 7}}, {"git_sha": {"value": "not-a-sha"}}, None]
        for payload in probes:
            with self.subTest(payload=payload), patch.dict(os.environ, {"PROGRESS_BOARD_RELEASES": str(self.root / "none")}), \
                 patch.object(BOARD, "probe_json", lambda url: payload), patch("sys.stderr", new_callable=io.StringIO):
                os.environ.pop("PROGRESS_BOARD_SKIP_PROBE", None)
                BOARD.RELEASE_CACHE.clear()
                BOARD.RELEASE_ERRORS.clear()
                self.assertIsNone(BOARD.latest_release("jbookout/carr-system"))
                self.assertIn("release probe", BOARD.release_wait_reason("jbookout/carr-system"))
        BOARD.RELEASE_CACHE.clear()
        BOARD.RELEASE_ERRORS.clear()
        with patch.dict(os.environ, {"PROGRESS_BOARD_RELEASES": str(self.root / "none")}), \
             patch.object(BOARD, "probe_json", lambda url: {"environment": "production", "source_commit": 5}):
            os.environ.pop("PROGRESS_BOARD_SKIP_PROBE", None)
            self.assertIsNone(BOARD.latest_release("jbookout/doctorcre-app"))
        BOARD.RELEASE_CACHE.clear()
        BOARD.RELEASE_ERRORS.clear()

    # 10 ── the snapshot says whether GitHub facts are fresh
    def test_10_a_github_outage_and_recovery_are_published(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex", "--pr", "42")
        self.fake_gh({"view": {**self.OPEN, "comments": [{"author": {"login": "r"}, "authorAssociation": "OWNER",
                                                            "body": "APPROVE\nReviewed-SHA: " + SHA_A,
                                                            "createdAt": "2026-09-30T10:00:00Z"}]}})
        self.run_board("render", "demo")
        good = BOARD.board_snapshot(self.read_state("demo"))["github_sync"]
        self.assertEqual(good["failed"], [])
        self.assertEqual(good["last_verified_at"], good["checked_at"])
        self.set_fixture({})
        self.run_board("render", "demo")
        snapshot = BOARD.board_snapshot(self.read_state("demo"))
        down = snapshot["github_sync"]
        self.assertEqual(snapshot["tasks"]["a"]["pr_phase"], "Ready to merge", "prior facts are kept")
        self.assertEqual([f["card"] for f in down["failed"]], ["a"])
        self.assertGreater(down["checked_at"], good["checked_at"])
        self.assertEqual(down["last_verified_at"], good["last_verified_at"])
        self.set_fixture({"view": self.OPEN})
        self.run_board("render", "demo")
        back = BOARD.board_snapshot(self.read_state("demo"))["github_sync"]
        self.assertEqual(back["failed"], [])
        self.assertEqual(back["last_verified_at"], back["checked_at"])

    # 11 ── every mutation reaches the app board
    def test_11_mutations_publish_to_the_app_board(self):
        calls, remote, caller = self.spy()
        with self.in_process(PROGRESS_BOARD_SKIP_GH="1", PROGRESS_BOARD_LOCAL_ONLY=""), \
             patch.object(BOARD, "call_verb", caller):
            BOARD.main(["init", "demo", "--title", "Demo"])
            self.assertEqual(calls.count("publish-board-snapshot"), 1)
            BOARD.main(["task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex"])
            BOARD.main(["note", "demo", "--text", "Visible in the app"])
        self.assertEqual(calls.count("publish-board-snapshot"), 3)
        published = remote["snapshot"]["snapshot_json"]
        self.assertEqual(published["tasks"]["a"]["title"], "A")
        self.assertEqual(published["notes"][0]["text"], "Visible in the app")

    def test_11_a_failed_publication_is_loud_and_local_only_says_so(self):
        def refuse(verb, args):
            raise RuntimeError("read-progress-board failed: server unreachable")
        with self.in_process(PROGRESS_BOARD_SKIP_GH="1", PROGRESS_BOARD_LOCAL_ONLY=""), \
             patch.object(BOARD, "call_verb", refuse):
            with self.assertRaises(SystemExit) as raised:
                BOARD.main(["init", "demo", "--title", "Demo"])
        self.assertIn("not published", str(raised.exception))
        self.assertIn("render demo --publish", str(raised.exception))
        self.assertTrue((self.root / "boards" / "demo.json").exists(), "the local state is kept")
        result = self.run_board("note", "demo", "--text", "x")
        self.assertIn("not published", result.stderr)


def merged_view(oid):
    """A complete merged PR as gh reports it, merged at `oid`."""
    return {"state": "MERGED", "isDraft": False, "headRefOid": "f" * 40, "author": {"login": "builder"},
            "statusCheckRollup": [], "comments": [], "mergeCommit": {"oid": oid},
            "files": [{"path": "feature"}], "changedFiles": 1}


class DeliveryTargetRelease(BoardCase):
    """#1439: a project card completes from the production release readback only
    for the delivery target that release deploys."""

    def test_partial_http_failures_keep_merged_and_finish_publish_poll_in_both_lanes(self):
        from argparse import Namespace
        from http.client import IncompleteRead, BadStatusLine
        from unittest.mock import MagicMock
        self.run_board("init", BOARD.LAUNCHD_BOARD, "--title", "Scheduled")
        for repo, target in BOARD.AUTOMATIC_DELIVERY_TARGETS.items():
            for error in (IncompleteRead(b"partial", 10), BadStatusLine("invalid")):
                with self.subTest(repo=repo, error=type(error).__name__), patch.dict(os.environ, self.env):
                    state = BOARD.read_state(BOARD.LAUNCHD_BOARD)
                    state["tasks"] = {"fix": {"title": "Feature", "repo": repo, "pr": 1,
                        "status": "done", "stage": "merged", "delivery_target": target,
                        "executor": "codex", "updated_at": BOARD.stamp()}}
                    BOARD.write_json(state)
                    response = MagicMock()
                    response.__enter__.return_value.read.side_effect = error
                    kwargs = {"return_value": response} if isinstance(error, IncompleteRead) else {"side_effect": error}
                    with patch.object(BOARD, "urlopen", **kwargs), \
                         patch.object(BOARD, "fetch_pr", return_value=(merged_view("a" * 40), None)), \
                         patch.object(BOARD, "publish_board") as publish, \
                         patch.object(BOARD, "poll_board_answers") as poll:
                        BOARD.command_render(Namespace(project=BOARD.LAUNCHD_BOARD, publish=True))
                        self.assertEqual(publish.call_args_list,
                                         [unittest.mock.call(BOARD.LAUNCHD_BOARD), unittest.mock.call(BOARD.ALL_REPOS_BOARD)])
                        poll.assert_called_once_with(BOARD.LAUNCHD_BOARD)
                    task = BOARD.read_state(BOARD.LAUNCHD_BOARD)["tasks"]["fix"]
                    self.assertEqual(task["stage"], "merged")
                    self.assertNotIn("completed_at", task)
                    self.assertNotIn("evidence", task)
                    # The static page is retired; the board is the published snapshot.
                    self.assertFalse((self.root / "boards" / f"{BOARD.LAUNCHD_BOARD}.html").exists())

    def test_reverted_change_stays_merged_before_first_release_in_both_lanes(self):
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        source = self.root / "source"
        source.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=fixture_env(),
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        (source / "feature").write_text("off\n")
        git("add", "feature")
        git("commit", "-m", "Baseline")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("commit", "-m", "Fix")
        fix = git("rev-parse", "HEAD")
        git("revert", "--no-edit", fix)
        deployed = git("rev-parse", "HEAD")
        self.assertEqual((source / "feature").read_text(), "off\n")
        self.run_board("init", "demo", "--title", "Demo")
        for repo, target in BOARD.AUTOMATIC_DELIVERY_TARGETS.items():
            with self.subTest(repo=repo), patch.dict(os.environ, self.env):
                state = BOARD.read_state("demo")
                state["tasks"] = {"fix": {"title": "Feature", "repo": repo, "pr": 1,
                    "status": "done", "stage": "merged", "delivery_target": target,
                    "executor": "codex", "updated_at": BOARD.stamp()}}
                BOARD.write_json(state)
                with patch.object(BOARD, "fetch_pr", return_value=(merged_view(fix), None)), \
                     patch.object(BOARD, "deployed_release", return_value=(source, deployed, "https://example.invalid/release")):
                    BOARD.render("demo")
                task = BOARD.read_state("demo")["tasks"]["fix"]
                self.assertEqual(task["stage"], "merged")
                self.assertNotIn("completed_at", task)
                self.assertNotIn("evidence", task)

    def test_worker_release_does_not_complete_other_or_unspecified_targets(self):
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        source = self.root / "source"
        source.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=fixture_env(),
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Feature")
        sha = git("rev-parse", "HEAD")
        self.run_board("init", "demo", "--title", "Demo")
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "ok": True, "env": {"value": "production"}, "git_sha": {"value": sha},
            "schema": {"available": False}}).encode()
        for target in (None, "workstation", "database", "app", "manual"):
            with self.subTest(target=target), patch.dict(os.environ, self.env):
                state = BOARD.read_state("demo")
                state["tasks"] = {"install": {"title": "Install hook", "status": "done",
                    "stage": "merged", "pr": 1, "executor": "codex", "updated_at": BOARD.stamp(),
                    "delivery_target": target}}
                BOARD.write_json(state)
                with patch.object(BOARD, "RELEASE_TARGETS", {BOARD.DEFAULT_PR_REPO: (source, "https://example.invalid/release")}), \
                     patch.object(BOARD, "urlopen", return_value=response), \
                     patch.object(BOARD, "fetch_pr", return_value=(merged_view(sha), None)):
                    BOARD.render("demo")
                task = BOARD.read_state("demo")["tasks"]["install"]
                self.assertEqual(task["stage"], "merged")
                self.assertNotIn("completed_at", task)
                self.assertNotIn("evidence", task)

    def test_installed_copy_uses_checkout_independent_of_executable(self):
        source = self.root / "carr-system"
        source.mkdir()
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=fixture_env(),
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Feature")
        sha = git("rev-parse", "HEAD")
        installed = self.root / "support" / "carr-progress-board" / "progress_board.py"
        installed.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, installed)
        spec = importlib.util.spec_from_file_location("installed_board", installed)
        module = importlib.util.module_from_spec(spec)
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "ok": True, "env": {"value": "production"}, "git_sha": {"value": sha}}).encode()
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            spec.loader.exec_module(module)
            with patch.object(module, "urlopen", return_value=response):
                release = module.deployed_release(module.DEFAULT_PR_REPO)
        self.assertEqual(release[0], source)
        self.assertIsNotNone(module.deployment_evidence({"mergeCommit": {"oid": sha}}, release))

    def test_render_advances_only_deployed_merge_commits_in_each_repository(self):
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        git_env = fixture_env()
        source = self.root / "source"
        source.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=git_env,
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Released")
        released = git("rev-parse", "HEAD")
        (source / "future").write_text("pending\n")
        git("add", "future")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Unreleased")
        newer = git("rev-parse", "HEAD")
        for repo, identity in (
            ("jbookout/carr-system", {"ok": True, "env": {"value": "production"},
                                      "git_sha": {"value": released}}),
            ("jbookout/doctorcre-app", {"service": "doctorcre-app", "environment": "production",
                                       "source_commit": released}),
        ):
            with self.subTest(repo=repo):
                project = repo.split("/")[1]
                self.run_board("init", project, "--title", "Demo")
                for task_id, number in (("deployed", 1), ("future", 2)):
                    self.run_board("task", project, task_id, "--title", task_id,
                                   "--status", "done", "--executor", "codex",
                                   "--pr", str(number), "--repo", repo, "--stage", "merged",
                                   "--delivery-target", "worker" if repo == BOARD.DEFAULT_PR_REPO else "app")
                def info(number, _repo):
                    return merged_view(released if number == 1 else newer), None
                from unittest.mock import MagicMock
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(identity).encode()
                with patch.dict(os.environ, self.env), patch.object(BOARD, "fetch_pr", info), \
                     patch.object(BOARD, "RELEASE_TARGETS", {repo: (source, "https://example.invalid/release")}, create=True), \
                     patch.object(BOARD, "urlopen", return_value=response):
                    BOARD.render(project)
                tasks = self.read_state(project)["tasks"]
                self.assertEqual(tasks["deployed"]["stage"], "live")
                self.assertIn(released, tasks["deployed"]["evidence"])
                self.assertEqual(tasks["future"]["stage"], "merged")
                self.assertNotIn("evidence", tasks["future"])
                remote = {"snapshot": None}
                def call(verb, args):
                    if verb == "publish-board-snapshot":
                        self.assertEqual(args["base_version"], 0)
                        remote["snapshot"] = {"version": 1, "snapshot_json": args["snapshot"]}
                    return {"ok": True, "questions": [], **remote}
                with patch.dict(os.environ, self.env), patch.object(BOARD, "call_verb", call):
                    BOARD.publish_board(project)
                published = remote["snapshot"]["snapshot_json"]["tasks"]
                self.assertEqual(published["deployed"]["stage"], "live")
                self.assertEqual(published["future"]["stage"], "merged")

    def test_render_keeps_merged_when_production_identity_cannot_be_proven(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "pending", "--title", "Pending",
                       "--status", "done", "--executor", "codex", "--pr", "1", "--stage", "merged",
                       "--delivery-target", "worker")
        from unittest.mock import MagicMock
        cases = [
            {"ok": True, "env": {"value": "staging"}, "git_sha": {"value": "a" * 40}},
            {"ok": True, "env": {"value": "production"}, "git_sha": {"value": "abc"}},
            {"ok": True, "env": [], "git_sha": "malformed"},
            ["malformed"],
            OSError("unavailable"),
        ]
        for identity in cases:
            with self.subTest(identity=identity):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(identity, default=str).encode()
                kwargs = {"side_effect": identity} if isinstance(identity, Exception) else {"return_value": response}
                with patch.dict(os.environ, self.env), \
                     patch.object(BOARD, "fetch_pr", return_value=(merged_view("a" * 40), None)), \
                     patch.object(BOARD, "urlopen", **kwargs):
                    BOARD.render("demo")
                task = self.read_state("demo")["tasks"]["pending"]
                self.assertEqual(task["stage"], "merged")
                self.assertNotIn("evidence", task)


if __name__ == "__main__":
    unittest.main(verbosity=2)
