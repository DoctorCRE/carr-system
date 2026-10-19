#!/usr/bin/env python3
"""release-pipeline-selftest.py — offline proof of ops/release-pipeline.py.

Every production command is a FAKE: the runner records argv and returns scripted
output, GitHub and the live /release endpoint are in-memory, and the record-layer
door is a list. Git is real, against a throwaway origin + clone built outside
the checkout under ops/git_env.fixture_env(), because batching and change
classification are statements about real git history.

What is pinned, one test class each:
  classification   doc-only and test-only batches release nothing
  batching         N merges since the last release ship as ONE release of the
                   latest SHA
  stop on failure  a nonzero step stops the lane, records step/rc/log, enqueues
                   exactly one diagnosis turn; code failures wait for a new SHA
  staging guard    a failed staging step never reaches promotion
  kill switch      config flag, local override file, per-lane flag
  lock             a second concurrent tick runs nothing
  blockers         a missing credential files one loop, once, and runs nothing
  dry run          prints commands, executes none, writes no state
  schema supersede a new schema-snapshot PR closes the older open ones (close
                   only), leaves every other PR alone, and never fails on it
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from git_env import fixture_env  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("release_pipeline", HERE / "release-pipeline.py")
assert _SPEC is not None and _SPEC.loader is not None
rp = importlib.util.module_from_spec(_SPEC)
sys.modules["release_pipeline"] = rp
_SPEC.loader.exec_module(rp)
FIXTURE_ENV = fixture_env()
VERSION = "0f1e2d3c-4b5a-4968-8776-655443322110"
CF_TOKEN = "cf-selftest-token-must-never-be-echoed-9f8e7d"
DEPLOY_STEPS = {"wrangler-auth", "upload", "staging", "promote", "app-release", "rollback", "app-rollback"}
PRIOR = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"        # the Worker version serving before the release
PRIOR_APP = "5c4b3a29-1807-4f6e-9d5c-4b3a29180716"    # the app version serving before the release


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), env=FIXTURE_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


class FakeRunner:
    """Answers by step name (the log file's name), records everything."""

    def __init__(self, fail_at: str | None = None, pending: int = 0, live: dict | None = None,
                 wrangler_out: str = "You are logged in with an OAuth Token",
                 health_baseline_marker: bool = True, health_marker: bool = True,
                 health_baseline_findings: list | None = None, health_findings: list | None = None,
                 health_baseline_out_extra: str = "", health_out_extra: str = "",
                 health_baseline_write_json: bool = True, health_write_json: bool = True,
                 outputs: dict | None = None, smoke: dict | None = None):
        self.fail_at, self.pending, self.live = fail_at, pending, live
        # The release-smoke summaries, by step: {"smoke-post": ["invoices-list"]}
        # fails that journey in that run; None writes no summary (a crashed run).
        # Absent steps pass every journey.
        self.smoke = smoke or {}
        self.outputs = outputs or {}
        self.wrangler_out = wrangler_out
        # Defaults: a clean, COMPLETE health read with no findings, on both
        # the pre-promote baseline and the post-promote read — every scenario
        # above the HealthGate tests just wants the lane to finish. The
        # *_marker flags control whether tools/health-check.py's completion
        # line is the LAST NON-EMPTY line of stdout (point F); the
        # *_findings lists are what a real `--findings-json PATH` run would
        # have written (point C/D's schema: key/subject/count/hard_error/
        # time_rolling dicts); *_write_json=False simulates the file never
        # landing at all (a crash before _write_findings_json runs).
        self.health_baseline_marker = health_baseline_marker
        self.health_marker = health_marker
        self.health_baseline_findings = health_baseline_findings if health_baseline_findings is not None else []
        self.health_findings = health_findings if health_findings is not None else []
        self.health_baseline_out_extra = health_baseline_out_extra
        self.health_out_extra = health_out_extra
        self.health_baseline_write_json = health_baseline_write_json
        self.health_write_json = health_write_json
        self.calls: list[tuple[str, list[str]]] = []
        self.envs: dict[str, dict[str, str]] = {}
        # Which folder each named step ran in — added for point 1 of the
        # second round of review of PR #1237: the health baseline and the
        # post-promote health read must run in the SAME folder (the release
        # worktree, not the pipeline's own checkout), or a `db/schema.sql`
        # rewritten by this release's own migrate-apply spuriously diffs
        # against a baseline read from a different checkout.
        self.cwds: dict[str, str] = {}

    def _health_output(self, name, argv):
        """Write the --findings-json file (unless suppressed) and build the
        stdout text a real `./run.sh health` would print: any extra text the
        test asked for, then the completion marker last (unless suppressed)
        — mirroring tools/health-check.py's own contract exactly."""
        if name == "health-baseline":
            findings, extra, marker, write = (self.health_baseline_findings, self.health_baseline_out_extra,
                                              self.health_baseline_marker, self.health_baseline_write_json)
        else:
            findings, extra, marker, write = (self.health_findings, self.health_out_extra,
                                              self.health_marker, self.health_write_json)
        if "--findings-json" in argv and write:
            path = Path(argv[argv.index("--findings-json") + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"findings": findings, "generated_at": "2026-09-30T00:00:00Z"}))
        lines = [ln for ln in extra.splitlines() if ln]
        if marker:
            lines.append(rp.HEALTH_COMPLETE_MARKER)
        return "\n".join(lines) + ("\n" if lines else "")

    def run(self, argv, *, cwd, log, env, timeout=3600):
        stem = log.stem
        name = stem.split("-", 1)[1] if stem[:2].isdigit() else stem
        self.calls.append((name, list(argv)))
        self.envs[name] = dict(env)
        self.cwds[name] = str(cwd)
        assert "DATABASE_URL" not in env and not any(k.startswith("CARR_DB_") for k in env), \
            "a credential leaked into a step's environment"
        assert not any(CF_TOKEN in a for a in argv), "the deploy token reached argv"
        if name not in DEPLOY_STEPS:
            assert "CLOUDFLARE_API_TOKEN" not in env, f"the deploy token reached non-deploy step {name}"
        if name in ("health-baseline", "health"):
            return rp.Result(0 if name != self.fail_at else 7, self._health_output(name, argv))
        if name.startswith("smoke-"):
            return self._smoke_output(name, argv)
        if name == self.fail_at:
            failed_out = self.outputs.get(name, "boom")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(failed_out, encoding="utf-8")
            return rp.Result(7, failed_out)
        is_app_worktree = name == "app-worktree"
        is_app_worktree_cleanup = (name == "worktree-cleanup" and argv[:5] == [
            "git", "-C", str(cwd), "worktree", "remove"] and "/app-" in argv[-1])
        if is_app_worktree or name == "app-main-ref-refresh" or is_app_worktree_cleanup:
            proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True)
            return rp.Result(proc.returncode, proc.stdout + proc.stderr)
        if name == "release-key":
            key = argv[argv.index("--key") + 1]
            return rp.Result(0 if key.endswith("-01") else 2, "")
        out = {
            "staging-prepare": json.dumps({"ok": True, "receipt_id": "11111111-2222-4333-8444-555555555555"}),
            "migrate-plan": f"applied: 10   pending: {self.pending}",
            "upload": f"uploaded only\n  provider version: {VERSION}\n",
            "wrangler-auth": self.wrangler_out,
            **self.outputs,
        }.get(name, "ok")
        if name == "promote" and self.live is not None:
            upload = next(a for n, a in self.calls if n == "upload")
            self.live["sha"] = upload[upload.index("--release-sha") + 1]
            self.live["version"] = VERSION
        if name == "rollback" and self.live is not None:
            self.live["version"] = argv[argv.index("--promote-version") + 1]
        return rp.Result(0, out)

    def _smoke_output(self, name, argv):
        """Write the summary a real ops/release-smoke.py run would, at --out.
        A failure named `browser-journeys::<test>` fails the browser proof
        with that test named in evidence.failed_tests."""
        failed = self.smoke.get(name, [])
        if failed is None:
            return rp.Result(1, "Traceback: release-smoke crashed")
        lane = argv[argv.index('--lane') + 1]
        only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else None
        tests = sorted(f.split("::", 1)[1] for f in failed if "::" in f)
        failed = sorted({f.split("::", 1)[0] for f in failed if only is None or f.split("::", 1)[0] in only})
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)

        def evidence(f):
            if f not in failed:
                return {}
            row = {"tests": [{"title": f, "status": "failed",
                              "artifacts": [str(out / "browser/artifacts/a1/screenshots/x.png"),
                                            str(out / "browser/artifacts/a1/trace.zip")]}]}
            return {**row, "failed_tests": tests} if f == "browser-journeys" and tests else row
        probes = [{"id": f, "status": "fail" if f in failed else "pass", "ms": 3,
                   "detail": f"{f} broke" if f in failed else "", "evidence": evidence(f)}
                  for f in rp.SMOKE_LANE_JOURNEYS[lane] if only is None or f in only]
        value = {"schema": "carr-release-smoke.v1", "lane": argv[argv.index('--lane') + 1],
                 "sha": argv[argv.index('--sha') + 1], "phase": argv[argv.index('--phase') + 1],
                 "invocation_id": argv[argv.index('--invocation-id') + 1],
                 "ok": not failed, "failed": failed, "probes": probes}
        (out / "summary.json").write_text(json.dumps(value))
        return rp.Result(1 if failed else 0, "release-smoke: done")

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


def install_toolchain_wrangler(repo: Path) -> Path:
    """Write a stub wrangler where Pipeline.auth_wrangler looks for it."""
    wrangler = (rp.wrangler_toolchain(repo, repo / "out/release-pipeline")
                / "node_modules/.bin/wrangler")
    wrangler.parent.mkdir(parents=True, exist_ok=True)
    wrangler.write_text("#!/bin/sh\nexit 0\n")
    wrangler.chmod(0o755)
    return wrangler


class InstallingRunner(FakeRunner):
    """FakeRunner whose `wrangler-install` step writes the binary, as npm ci does."""

    def run(self, argv, *, cwd, log, env, timeout=3600):
        res = super().run(argv, cwd=cwd, log=log, env=env, timeout=timeout)
        if log.stem.endswith("wrangler-install") and res.rc == 0:
            bin_ = Path(cwd) / "node_modules/.bin/wrangler"
            bin_.parent.mkdir(parents=True, exist_ok=True)
            bin_.write_text("#!/bin/sh\nexit 0\n")
        return res


HEAD_DATE = "2026-09-29T00:00:00Z"


def pr_head(n: int) -> str:
    return f"feed{n:036x}"


class FakeGitHub:
    """Merged source with green CI and canary results unless overridden."""

    _pr_numbers: dict[str, int] = {}

    def __init__(self, canary: dict | None = None,
                 red_ci_prs: set | None = None, checks: list | None = None, raise_on: str | None = None,
                 ci_runs: dict | None = None):
        self.ci_runs = ci_runs or {}     # pr -> [(id, conclusion)], replacing the default single run
        self.canary, self.red_ci_prs = canary, red_ci_prs or set()
        self.checks = checks if checks is not None else [
            {"name": "test", "status": "completed", "conclusion": "success"}]
        self.raise_on = raise_on
        self.heads: dict[str, int] = {}
        self.merge_commit: dict[str, str] = {}   # commit -> the PR's merge_commit_sha, when not itself

    def pr_number(self, sha):
        return self._pr_numbers.setdefault(sha, 100 + len(self._pr_numbers))

    def pr_for_commit(self, sha):
        if self.raise_on == "pr_for_commit":
            raise ValueError("malformed GitHub JSON")
        n = self.pr_number(sha)
        self.heads[pr_head(n)] = n
        return {"number": n, "merged_at": "2026-09-30T00:00:00Z", "head": {"sha": pr_head(n)},
                "merge_commit_sha": self.merge_commit.get(sha, sha)}

    def commit_date(self, sha):
        return HEAD_DATE

    def runs_for(self, sha):
        if sha in self.heads:               # a PR head (see pr_for_commit)
            pr = self.heads[sha]
            if pr in self.ci_runs:
                return [{"id": i, "name": "CI", "event": "pull_request", "status": "completed",
                         "conclusion": c} for i, c in self.ci_runs[pr]]
            ok = pr not in self.red_ci_prs
            return [{"id": 40000 + pr, "name": "CI", "event": "pull_request", "status": "completed",
                     "conclusion": "success" if ok else "failure"}]
        if self.canary is None:
            return [{"id": 7, "name": "main canary", "event": "push", "status": "completed",
                     "conclusion": "success"}]
        state = self.canary.get(sha)
        if state is None:
            return []
        status, conclusion = state
        return [{"id": 7, "name": "main canary", "event": "push", "status": status, "conclusion": conclusion}]

    def jobs(self, run_id):
        return [{"name": "ops/ci.sh --strict", "conclusion": "success"},
                {"name": "ops/ci.sh --strict --only pushfloor unit secret", "conclusion": "success"}]


    def check_runs(self, sha):
        return self.checks


class Fixture:
    _history_root = None
    _base_sha = None

    @classmethod
    def _baseline_history(cls):
        # The initial Git history is identical for every scenario. Build it
        # once; each fixture copies both repositories before any mutation.
        if cls._history_root is None:
            cls._history_root = tempfile.TemporaryDirectory(prefix="release-baseline-")
            root = Path(cls._history_root.name)
            origin, repo = root / "origin.git", root / "repo"
            git(root, "init", "--bare", "-b", "main", str(origin))
            git(root, "clone", str(origin), str(repo))
            git(repo, "config", "user.email", "t@example.invalid")
            git(repo, "config", "user.name", "t")
            (repo / "README.md").write_text("x")
            (repo / "mcp-server").mkdir()
            (repo / "mcp-server" / "package.json").write_text('{"name": "mcp-server"}\n')
            (repo / "mcp-server" / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
            git(repo, "add", "README.md", "mcp-server")
            git(repo, "commit", "-q", "-m", "c")
            git(repo, "push", "-q", "origin", "HEAD:main")
            cls._base_sha = git(repo, "rev-parse", "HEAD")
        return Path(cls._history_root.name), cls._base_sha

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.slice_marks: list[tuple[str, str]] = []
        self.origin = tmp / "origin.git"
        self.repo = tmp / "repo"
        baseline, self.base = self._baseline_history()
        shutil.copytree(baseline / "origin.git", self.origin)
        shutil.copytree(baseline / "repo", self.repo)
        git(self.repo, "remote", "set-url", "origin", str(self.origin))
        # Machine state is not history, exactly as the real checkout's
        # .gitignore has it. Without this, commit()'s `git add -A` swept the
        # stub `.venv/bin/python` (written below) into every test's first
        # post-base commit, so a commit meant to be docs-only was no longer
        # canary-ignored; and it swept the pipeline's own out/release-pipeline
        # state into any commit made after a tick.
        (self.repo / ".git" / "info" / "exclude").write_text(".venv\nout/\n")
        self.cred = tmp / "cred"
        self.cred.mkdir()
        (self.cred / "db.env").write_text(
            "NEON_API_KEY='v'\nCARR_DB_JOBS_URL='v'\nCARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL='v'\n")
        (self.cred / "mcp-tokens.env").write_text("CARR_MCP_PROBE_TOKEN=v\n")
        (self.cred / "tokens.env").write_text(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
        # A stub `.venv/bin/python`, matching the real checkout's layout, so
        # the fail-closed venv check added for the coordinator's dangling-
        # symlink fix (a real run found `ln -s` happily linking a MISSING
        # source venv, producing hard errors that read like a code bug
        # rather than an environment one) does not break every OTHER test
        # in this file, which is not testing that behavior. Tests for the
        # fail-closed behavior itself remove this stub (see FailClosedVenv).
        venv_python = self.repo / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True, exist_ok=True)
        venv_python.write_text("#!/bin/sh\nexit 0\n")
        venv_python.chmod(0o755)
        # The token-check wrangler already installed, as on every tick after
        # the first; TokenCheckWrangler removes it to test the install.
        self.wrangler = install_toolchain_wrangler(self.repo)


    def commit(self, files: dict[str, str]) -> str:
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "c")
        git(self.repo, "push", "-q", "origin", "HEAD:main")
        return git(self.repo, "rev-parse", "HEAD")

    def config(self, **over) -> dict:
        cfg = json.loads((HERE / "config" / "release-pipeline.v1.json").read_text())
        cfg["local_off_file"] = str(self.tmp / "release-pipeline.off")
        cfg["credential_dir"] = str(self.cred)
        cfg["app"]["enabled"] = False
        cfg["app"]["repo_path"] = str(self.repo)
        cfg.update(over)
        return cfg

    def pipeline(self, runner, *, cfg=None, github=None, live=None, dry_run=False, verbs=None,
                 slice_marker=None):
        cfg = cfg or self.config()
        live = live if live is not None else {"sha": self.base}
        verbs = verbs if verbs is not None else []
        # Step 10 never spawns the real marker here: every run records the
        # (release_key, sha) it would have marked in self.slice_marks.
        if slice_marker is None:
            slice_marker = lambda key, sha: (self.slice_marks.append((key, sha)) or {"rc": 0})  # noqa: E731
        env = rp.child_env(FIXTURE_ENV)
        env.update({k: FIXTURE_ENV[k] for k in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")})
        # Fixture source stands in for the modules loaded by a production
        # process. Capture it at construction, before any simulated sync.
        loaded = {path: rp.git_blob((self.repo / path).read_bytes())
                  if (self.repo / path).is_file() else None for path in (
                      "ops/release-pipeline.py", "lib/credential_file.py",
                      "lib/github_reader.py", "lib/secret_redaction.py",
                      "ops/release-smoke.py", "ops/doctorcre-production-smoke.py",
                      "ops/config/release-pipeline.v1.json")}
        if isinstance(cfg, rp.LoadedConfig):
            loaded["ops/config/release-pipeline.v1.json"] = cfg.source_blob
        pipe = rp.Pipeline(cfg or self.config(), repo=self.repo, runner=runner,
                           github=lambda _r: github or FakeGitHub(),
                           http=lambda _u: {"git_sha": {"value": live["sha"]},
                                            "worker_version": {"id": live.get("version", PRIOR)}},
                           call_verb=lambda verb, args: (verbs.append((verb, args)) or (True, {"ok": True})),
                           slice_marker=slice_marker, controller_blobs=loaded,
                           dry_run=dry_run, env=env, today="2026-09-30", out=lambda _s: None)
        pipe.staging_ledger = lambda: {"candidate": "fixture", "ledger": {"fixture": "digest"}}
        pipe.sleep = lambda _seconds: None
        return pipe

    def state(self) -> dict:
        p = self.repo / "out/release-pipeline/state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def records(self) -> list[dict]:
        p = self.repo / "out/release-pipeline/releases.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()


class WorkerIdentityReconciliation(Base):
    def test_live_identity_replaces_stale_checkpoint_before_target_selection(self):
        live_sha = self.fx.commit({"mcp-server/src/a.js": "already live"})
        latest = self.fx.commit({"mcp-server/src/a.js": "next"})
        live = {"sha": live_sha}
        runner = FakeRunner(live=live)
        pipe = self.fx.pipeline(runner, live=live)
        pipe.store.save({"worker": {"last_released_sha": self.fx.base}})
        original = pipe.release_target

        def target(gh, cfg, base, head):
            self.assertEqual(base, live_sha)
            self.assertEqual(self.fx.state()["worker"]["last_released_sha"], live_sha)
            return original(gh, cfg, base, head)

        with mock.patch.object(pipe, "release_target", side_effect=target):
            self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        row = next(row for row in self.fx.records() if row["status"] == "live_identity_reconciled")
        self.assertEqual((row["from_sha"], row["sha"]), (self.fx.base, live_sha))
        self.assertEqual(row["provider_version_id"], PRIOR)

    def test_already_live_main_is_reconciled_without_redeployment_or_slice_mark(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "already live"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, live={"sha": latest})
        pipe.store.save({"worker": {"last_released_sha": self.fx.base,
                                    "failed_sha": "f" * 40, "failed_step": "migrate-apply"}})
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], "f" * 40)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.slice_marks, [])
        self.assertEqual([row["status"] for row in self.fx.records()], ["live_identity_reconciled"])

    def test_dry_run_uses_live_base_without_persisting_reconciliation(self):
        live_sha = self.fx.commit({"mcp-server/src/a.js": "already live"})
        self.fx.commit({"mcp-server/src/a.js": "next"})
        pipe = self.fx.pipeline(FakeRunner(), live={"sha": live_sha}, dry_run=True)
        pipe.store.save({"worker": {"last_released_sha": self.fx.base}})
        with mock.patch.object(pipe, "release_target", return_value=live_sha) as target:
            self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(target.call_args.args[2], live_sha)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], self.fx.base)
        self.assertEqual(self.fx.records(), [])

    def test_failed_reconciliation_receipt_cannot_advance_checkpoint_or_select_target(self):
        live_sha = self.fx.commit({"mcp-server/src/a.js": "already live"})
        self.fx.commit({"mcp-server/src/a.js": "next"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, live={"sha": live_sha})
        pipe.store.save({"worker": {"last_released_sha": self.fx.base}})
        with mock.patch.object(pipe.store, "record_durable", side_effect=OSError("fixture receipt failed")), \
             mock.patch.object(pipe, "release_target") as target:
            self.assertEqual(pipe.tick(["worker"]), 1)
        target.assert_not_called()
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], self.fx.base)
        self.assertEqual(runner.calls, [])

    def test_identity_outside_main_history_is_refused(self):
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, live={"sha": "e" * 40})
        pipe.store.save({"worker": {"last_released_sha": self.fx.base}})
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], self.fx.base)
        self.assertEqual(self.fx.records()[-1]["reason"], "history_diverged")
        self.assertEqual(runner.calls, [])

    def test_target_before_merged_live_side_branch_is_refused(self):
        candidate = self.fx.commit({"mcp-server/src/c.js": "candidate"})
        git(self.fx.repo, "checkout", "-q", "-b", "live-side", self.fx.base)
        (self.fx.repo / "mcp-server/s.js").write_text("already live")
        git(self.fx.repo, "add", "mcp-server/s.js")
        git(self.fx.repo, "commit", "-q", "-m", "side")
        live_sha = git(self.fx.repo, "rev-parse", "HEAD")
        git(self.fx.repo, "checkout", "-q", "main")
        git(self.fx.repo, "merge", "--no-ff", "-m", "merge side", "live-side")
        head = self.fx.commit({"mcp-server/src/h.js": "next"})
        live = {"sha": live_sha}
        runner = FakeRunner(live=live)
        gh = FakeGitHub(canary={candidate: ("completed", "success"),
                               head: ("in_progress", None)})
        pipe = self.fx.pipeline(runner, github=gh, live=live)
        pipe.store.save({"worker": {"last_released_sha": candidate}})

        self.assertEqual(pipe.tick(["worker"]), 0)
        row = self.fx.records()[-1]
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["reason"], "history_diverged")
        self.assertEqual(row["sha"], candidate)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], live_sha)
        self.assertEqual(live["sha"], live_sha)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.slice_marks, [])

    def test_docs_only_target_before_merged_live_side_branch_cannot_regress_checkpoint(self):
        candidate = self.fx.commit({"mcp-server/src/c.js": "same runtime", "docs/c.md": "candidate"})
        git(self.fx.repo, "checkout", "-q", "-b", "live-side", self.fx.base)
        runtime = self.fx.repo / "mcp-server/src/c.js"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_text("same runtime")
        (self.fx.repo / "README.md").write_text("live side")
        git(self.fx.repo, "add", "mcp-server/src/c.js", "README.md")
        git(self.fx.repo, "commit", "-q", "-m", "side")
        live_sha = git(self.fx.repo, "rev-parse", "HEAD")
        self.assertEqual(git(self.fx.repo, "rev-parse", f"{candidate}:mcp-server"),
                         git(self.fx.repo, "rev-parse", f"{live_sha}:mcp-server"))
        git(self.fx.repo, "checkout", "-q", "main")
        git(self.fx.repo, "merge", "--no-ff", "-m", "merge side", "live-side")
        merge = git(self.fx.repo, "rev-parse", "HEAD")
        head = self.fx.commit({"mcp-server/src/h.js": "next"})
        live = {"sha": live_sha}
        runner = FakeRunner(live=live)
        gh = FakeGitHub(canary={candidate: ("completed", "success"),
                               merge: ("in_progress", None), head: ("in_progress", None)})
        pipe = self.fx.pipeline(runner, github=gh, live=live)
        pipe.store.save({"worker": {"last_released_sha": candidate}})

        self.assertEqual(pipe.tick(["worker"]), 0)
        rows = self.fx.records()
        self.assertEqual([row["status"] for row in rows], ["live_identity_reconciled", "blocked"])
        self.assertEqual((rows[0]["from_sha"], rows[0]["sha"]), (candidate, live_sha))
        self.assertEqual((rows[-1]["reason"], rows[-1]["sha"]), ("history_diverged", candidate))
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], live_sha)
        self.assertEqual(live["sha"], live_sha)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.slice_marks, [])

    def test_unreadable_identity_refuses_even_when_checkpoint_equals_main(self):
        for response in [OSError("offline"), {}, {"git_sha": {"value": "short"}},
                         {"git_sha": "malformed"},
                         {"git_sha": {"value": self.fx.base}, "worker_version": {"id": "bad"}}]:
            with self.subTest(response=response):
                runner = FakeRunner()
                pipe = self.fx.pipeline(runner)
                pipe.store.save({"worker": {"last_released_sha": self.fx.base}})
                if isinstance(response, Exception):
                    pipe.http = mock.Mock(side_effect=response)
                else:
                    pipe.http = mock.Mock(return_value=response)
                pipe.tick(["worker"])
                self.assertEqual(runner.calls, [])
                self.assertEqual(self.fx.state()["worker"]["last_released_sha"], self.fx.base)
                self.assertEqual(self.fx.records()[-1]["reason"], "live_identity_unreadable")


class RejectionReconciliation(Base):
    def setUp(self):
        super().setUp()
        self.sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.gh = FakeGitHub()
        self.clock = 1000.0
        self.runner = FakeRunner()
        self.pipe = self.fx.pipeline(self.runner, github=self.gh)
        self.pipe.now = lambda: self.clock

    def wire_live(self):
        live = {"sha": self.fx.base}
        self.runner.live = live
        self.pipe.http = lambda _url: {"git_sha": {"value": live["sha"]},
                                          "worker_version": {"id": live.get("version", PRIOR)}}


    def test_health_recheck_is_bounded_and_recovery_runs_all_release_guards(self):
        self.runner.health_baseline_findings = [{"key": "reader", "subject": "", "count": 1,
            "hard_error": True, "time_rolling": False}]
        self.pipe.tick(["worker"])
        count = self.runner.names().count("health-baseline")
        self.pipe.tick(["worker"])
        self.assertEqual(self.runner.names().count("health-baseline"), count)
        self.clock += self.pipe.REJECTION_RECHECK_SECONDS
        self.runner.health_baseline_findings = []
        self.runner.fail_at = "staging"
        self.assertEqual(self.pipe.tick(["worker"]), 1)
        self.assertIn("staging", self.runner.names())
        self.assertNotIn("promote", self.runner.names())
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], self.sha)


    def test_canonical_staging_observer_hashes_real_provider_scope_and_exact_ledger(self):
        from decimal import Decimal
        module = rp.runpy.run_path(str(HERE.parent / "tools/staging-project-replacement.py"))
        scope = module["ProviderScope"]("project", "candidate", "branch", "endpoint", "host",
            17, "region", Decimal("0.25"), Decimal("1"))
        module["resolve_existing_scopes"] = mock.Mock(return_value=(None, None, scope))
        module["derive_dsn"] = mock.Mock(return_value=module["SecretDsn"](scope, "private-dsn-canary"))
        cursor = mock.MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = ("schema_migrations",)
        cursor.fetchall.return_value = [("0001_fixture.sql", "a" * 64)]
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value = cursor
        psycopg = mock.Mock(); psycopg.connect.return_value = conn
        module["psycopg"] = psycopg
        with mock.patch.object(rp.runpy, "run_path", return_value=module):
            snapshot = rp.Pipeline.staging_ledger(self.pipe)
        digest = rp.evidence_digest(snapshot)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(snapshot["ledger"]["migration_ledger"], {"0001_fixture.sql": "a" * 64})
        self.assertNotIn("private-dsn-canary", json.dumps(snapshot))
        self.assertIn("default_transaction_read_only=on", psycopg.connect.call_args.kwargs["options"])
        cursor.execute.assert_any_call('select filename,sha256 from public.schema_migrations order by filename collate "C"')

    def test_staging_ledger_reconciliation_permits_one_retry_and_retains_checks(self):
        self.runner.fail_at = "staging-prepare"
        ledger = {"candidate": "fixture", "ledger": {"migration": "old"}}
        self.pipe.staging_ledger = lambda: dict(ledger)
        self.assertEqual(self.pipe.tick(["worker"]), 1)
        self.pipe.tick(["worker"])
        self.assertEqual(self.runner.names().count("staging-prepare"), 1)
        self.clock += self.pipe.REJECTION_RECHECK_SECONDS
        ledger["ledger"] = {"migration": "reconciled"}
        self.assertEqual(self.pipe.tick(["worker"]), 1)
        self.pipe.tick(["worker"])
        self.assertEqual(self.runner.names().count("staging-prepare"), 2)
        self.assertNotIn("promote", self.runner.names())
        rejection = self.fx.state()["worker"]["rejection"]
        self.assertEqual(rejection["repair"]["owner"], "tools/staging-project-replacement.py")
        self.assertIn("staging_ledger_digest", rejection)

    def test_pre_rejection_staging_failure_acquires_a_baseline_then_recovers(self):
        self.wire_live()
        # The exact lane shape a staging failure persisted before rejections existed.
        self.pipe.store.save({"worker": {"failed_sha": self.sha, "failed_step": "staging-prepare",
                                         "failed_at": "2026-10-01T00:00:00+00:00"}})
        ledger = {"candidate": "fixture", "ledger": "before"}
        self.pipe.staging_ledger = lambda: dict(ledger)
        self.pipe.tick(["worker"])
        lane = self.fx.state()["worker"]
        self.assertEqual(lane["failed_sha"], self.sha)
        self.assertIn("staging_ledger_digest", lane["rejection"])
        self.assertGreater(lane["rejection"]["recheck_after"], self.clock)
        self.assertEqual(self.runner.calls, [])  # a first observation is a baseline, never recovery
        self.clock += self.pipe.REJECTION_RECHECK_SECONDS
        ledger["ledger"] = "reconciled"
        self.pipe.tick(["worker"])
        self.assertEqual(self.runner.names().count("staging-prepare"), 1)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], self.sha)

    def test_staging_recovery_reconciles_the_failed_target_behind_a_pending_head(self):
        self.wire_live()
        self.runner.fail_at = "staging-prepare"
        ledger = {"candidate": "fixture", "ledger": "before"}
        self.pipe.staging_ledger = lambda: dict(ledger)
        self.assertEqual(self.pipe.tick(["worker"]), 1)
        newer = self.fx.commit({"mcp-server/src/b.js": "2"})
        self.gh.canary = {self.sha: ("completed", "success"), newer: ("in_progress", None)}
        self.clock += self.pipe.REJECTION_RECHECK_SECONDS
        ledger["ledger"] = "reconciled"
        self.runner.fail_at = None
        self.pipe.tick(["worker"])
        self.assertEqual(self.runner.names().count("staging-prepare"), 2)
        lane = self.fx.state()["worker"]
        self.assertIsNone(lane["failed_sha"])
        self.assertEqual(lane["last_released_sha"], self.sha)  # the canary target, never the pending head

    def test_staging_observation_failures_never_wake_a_failed_release(self):
        self.runner.fail_at = "staging-prepare"
        self.pipe.staging_ledger = lambda: {"candidate": "fixture", "ledger": "before"}
        self.pipe.tick(["worker"])
        for error in [ValueError("empty acknowledgement"), RuntimeError("REFUSED"),
                      subprocess.CalledProcessError(7, "probe", stderr="private-error-canary"),
                      OSError("private-error-canary")]:
            with self.subTest(error=type(error).__name__):
                self.clock += self.pipe.REJECTION_RECHECK_SECONDS
                self.pipe.staging_ledger = mock.Mock(side_effect=error)
                self.pipe.tick(["worker"])
                self.assertEqual(self.runner.names().count("staging-prepare"), 1)
                self.assertNotIn("private-error-canary", self.pipe.store.state_path.read_text())
                self.assertNotIn("private-error-canary", self.pipe.store.records_path.read_text())

    def test_staging_recovery_resumes_interrupted_retirement(self):
        self.runner.fail_at = "staging-prepare"
        ledger = {"candidate": "fixture", "ledger": "before"}
        self.pipe.staging_ledger = lambda: dict(ledger)
        self.pipe.tick(["worker"])
        kept = Path(self.fx.state()["worker"]["failed_worktree"])
        git(self.fx.repo, "worktree", "add", "--detach", str(kept), self.sha)
        self.clock += self.pipe.REJECTION_RECHECK_SECONDS
        ledger["ledger"] = "reconciled"
        rename = rp.os.rename
        def interrupted(src, dst):
            rename(src, dst)
            raise OSError("interrupted after rename")
        with mock.patch.object(rp.os, "rename", side_effect=interrupted):
            self.pipe.tick(["worker"])
        self.clock += self.pipe.REJECTION_RECHECK_SECONDS
        self.pipe.tick(["worker"])
        self.assertEqual(self.runner.names().count("staging-prepare"), 2)
        self.assertIsNone(rp.worktree_registration(self.fx.repo, kept))

    def test_fingerprint_sink_keeps_no_body_or_exception_canaries(self):
        secret = "invented-client-identifier-canary"
        original = self.gh.pr_for_commit
        self.gh.pr_for_commit = lambda sha: {**original(sha), "body": secret}
        self.pipe.tick(["worker"])
        self.gh.pr_for_commit = mock.Mock(side_effect=ValueError(secret))
        self.pipe.tick(["worker"])
        raw = (self.pipe.store.state_path.read_bytes() + self.pipe.store.records_path.read_bytes())
        self.assertNotIn(secret.encode(), raw)


class FakeGitHubIdentity(unittest.TestCase):
    def test_red_ci_does_not_leak_to_a_commit_with_a_colliding_short_hash(self):
        green = "01bec640" + "0" * 32
        red = "04dec640" + "0" * 32
        red_pr = FakeGitHub().pr_number(red)
        gh = FakeGitHub(red_ci_prs={red_pr})
        green_source = gh.pr_for_commit(green)
        red_source = gh.pr_for_commit(red)
        self.assertNotEqual(green_source["number"], red_source["number"])
        self.assertEqual(gh.runs_for(green_source["head"]["sha"])[0]["conclusion"], "success")
        self.assertEqual(gh.runs_for(red_source["head"]["sha"])[0]["conclusion"], "failure")
        self.assertEqual(FakeGitHub().pr_number(green), green_source["number"])


class FixtureIsolation(unittest.TestCase):
    def test_fixtures_share_an_immutable_baseline_but_not_mutable_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first").mkdir()
            (root / "second").mkdir()
            with mock.patch.dict(FIXTURE_ENV, {
                "GIT_AUTHOR_DATE": "2026-09-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "2026-09-01T00:00:00Z",
            }):
                first = Fixture(root / "first")
            with mock.patch.dict(FIXTURE_ENV, {
                "GIT_AUTHOR_DATE": "2026-09-02T00:00:00Z",
                "GIT_COMMITTER_DATE": "2026-09-02T00:00:00Z",
            }):
                second = Fixture(root / "second")
            self.assertEqual(first.base, second.base)
            changed = first.commit({"mcp-server/src/isolated.js": "first only"})
            self.assertNotEqual(changed, first.base)
            self.assertEqual(git(second.repo, "rev-parse", "HEAD"), second.base)
            self.assertEqual(git(root, "--git-dir", str(second.origin),
                                 "rev-parse", "refs/heads/main"), second.base)
            self.assertFalse((second.repo / "mcp-server/src/isolated.js").exists())


class Classification(unittest.TestCase):
    cfg = json.loads((HERE / "config" / "release-pipeline.v1.json").read_text())

    def test_doc_and_test_only_release_nothing(self):
        w = self.cfg["worker"]
        self.assertFalse(rp.classify(["README.md", "ops/ci.sh", "docs/x.md"], w)[0])
        self.assertFalse(rp.classify(["mcp-server/test/a.test.mjs", "mcp-server/README.md"], w)[0])
        self.assertFalse(rp.classify(["migrations/README.md"], w)[0])

    def test_worker_code_migrations_and_config_release(self):
        w = self.cfg["worker"]
        self.assertEqual(rp.classify(["mcp-server/src/tools.js", "README.md"], w),
                         (True, ["mcp-server/src/tools.js"]))
        self.assertTrue(rp.classify(["migrations/0600_x.sql"], w)[0])
        self.assertTrue(rp.classify(["mcp-server/wrangler.toml"], w)[0])
        self.assertTrue(rp.classify(["ops/config/doctorcre-artifact.v1.json"], w)[0])

    def test_app_lane_counts_everything_but_docs_and_tests(self):
        a = self.cfg["app"]
        self.assertFalse(rp.classify(["README.md", "test/x.test.mjs", "reports/a.json"], a)[0])
        self.assertTrue(rp.classify(["src/worker.js"], a)[0])


    def test_globs_use_github_path_filter_semantics(self):
        hit = rp._glob_hit
        self.assertTrue(hit("README.md", "**/*.md"))            # ** matches zero dirs
        self.assertTrue(hit("a/b/c.md", "**/*.md"))
        self.assertTrue(hit("mcp-server/README.md", "mcp-server/**/*.md"))
        self.assertTrue(hit("mcp-server/x/y/z.md", "mcp-server/**/*.md"))
        self.assertTrue(hit("out/a/b.json", "out/**"))
        self.assertFalse(hit("docs/deep/x.md", "docs/*.md"))     # * never crosses /
        self.assertTrue(hit("docs/x.md", "docs/*.md"))
        self.assertFalse(hit("src/ab.js", "src/?.js"))
        self.assertFalse(hit("layout/x", "out/**"))
        self.assertFalse(hit("README.mdx", "**/*.md"))


def _workflow_paths_ignore(text: str) -> list[str]:
    """The push trigger's paths-ignore list, read without a YAML dependency:
    the block list directly under the `paths-ignore:` key."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "paths-ignore:":
            indent = len(line) - len(line.lstrip())
            out = []
            for item in lines[i + 1:]:
                if not item.strip() or item.lstrip().startswith("#"):
                    continue
                if len(item) - len(item.lstrip()) <= indent or not item.lstrip().startswith("- "):
                    break
                out.append(item.lstrip()[2:].strip().strip("\"'"))
            return out
    raise AssertionError("no paths-ignore block found")


class CanaryGlobDrift(unittest.TestCase):
    """canary_ignored_globs must be exactly the canary workflow's paths-ignore:
    if they drift, the pipeline either waits forever on a canary that will
    never run or walks past a commit the canary would have judged."""

    def test_config_mirrors_main_canary_paths_ignore(self):
        cfg = json.loads((HERE / "config" / "release-pipeline.v1.json").read_text())
        wf = (HERE.parent / ".github" / "workflows" / "main-canary.yml").read_text()
        self.assertEqual(sorted(_workflow_paths_ignore(wf)),
                         sorted(cfg["worker"]["canary_ignored_globs"]))

    def test_parser_reads_a_block_list(self):
        text = ("on:\n  push:\n    paths-ignore:\n      - \"a/**\"\n"
                "      # c\n      - '**/*.md'\n  workflow_dispatch:\n")
        self.assertEqual(_workflow_paths_ignore(text), ["a/**", "**/*.md"])


class CanaryAggregate(Base):
    def test_release_poll_wait_is_bounded_to_one_minute(self):
        import plistlib
        source = HERE / 'launchd/com.carr.release-pipeline.plist'
        schedule = plistlib.loads(source.read_bytes())['StartCalendarInterval']
        self.assertEqual([row['Minute'] for row in schedule], list(range(60)))

    def test_canary_verdict_uses_the_workflow_conclusion_without_jobs(self):
        pipeline = self.fx.pipeline(FakeRunner())
        class GH:
            status = 'completed'
            conclusion = 'success'
            def runs_for(self, sha):
                return [{'id': 9, 'name': 'main canary', 'status': self.status,
                         'conclusion': self.conclusion}]
            def jobs(self, run_id):
                raise AssertionError('canary verdict must not fetch redundant job evidence')
        gh = GH()
        cfg = self.fx.config()['worker']
        self.assertEqual(pipeline.canary_verdict(gh, cfg, 'a'*40), 'green')
        for conclusion in ['failure', 'timed_out', 'action_required', None]:
            gh.conclusion = conclusion
            self.assertEqual(pipeline.canary_verdict(gh, cfg, 'a'*40), 'red')
        for conclusion in ['skipped', 'neutral', 'cancelled']:
            gh.conclusion = conclusion
            self.assertEqual(pipeline.canary_verdict(gh, cfg, 'a'*40), 'cancelled')
        gh.status = 'in_progress'
        gh.conclusion = None
        self.assertEqual(pipeline.canary_verdict(gh, cfg, 'a'*40), 'pending')


class DurableReleaseEvidence(Base):
    def test_worker_release_log_carries_configured_live_url(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        cfg = self.fx.config()
        cfg["worker"]["live_url"] = "https://worker.example.invalid"
        pipe = self.fx.pipeline(FakeRunner(live=live), cfg=cfg, live=live)
        self.assertEqual(pipe.tick(["worker"]), 0)
        for row in self.fx.records():
            self.assertEqual(row["live_identity"]["url"], "https://worker.example.invalid")

    def test_failed_proof_append_cannot_advance_release_or_slices(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        pipe = self.fx.pipeline(FakeRunner(live=live), live=live)
        original = pipe.store.record

        def record(row):
            if row["status"] == "release_verified":
                raise OSError("owned fixture proof append failed")
            original(row)

        pipe.store.record = record
        self.assertEqual(pipe.tick(["worker"]), 1)
        self.assertNotEqual(self.fx.state()["worker"].get("last_released_sha"), latest)
        self.assertEqual(self.fx.slice_marks, [])

    def test_proof_is_durable_before_release_checkpoint_or_slice_marker(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        pipe = self.fx.pipeline(FakeRunner(live=live), live=live)
        original = pipe.store.record
        observations = []

        def record(row):
            if row["status"] == "release_verified":
                observations.append((self.fx.state().get("worker", {}).get("last_released_sha"),
                                     list(self.fx.slice_marks)))
            original(row)

        pipe.store.record = record
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(observations, [(None, [])])
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)

    def test_failed_terminal_append_keeps_checkpoint_open_after_verified_proof(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        pipe = self.fx.pipeline(FakeRunner(live=live), live=live)
        original = pipe.store.record

        def record(row):
            if row["status"] == "shipped":
                raise OSError("owned fixture terminal append failed")
            original(row)

        pipe.store.record = record
        self.assertEqual(pipe.tick(["worker"]), 1)
        self.assertNotEqual(self.fx.state()["worker"].get("last_released_sha"), latest)
        verified = [row for row in self.fx.records() if row["status"] == "release_verified"]
        self.assertEqual([row["sha"] for row in verified], [latest])
        self.assertEqual(self.fx.slice_marks, [(verified[0]["release_key"], latest)])

    def test_unreadable_written_proof_cannot_advance_release_or_slices(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        pipe = self.fx.pipeline(FakeRunner(live=live), live=live)
        with mock.patch.object(pipe.store, "records", return_value=[]):
            self.assertEqual(pipe.tick(["worker"]), 1)
        self.assertNotEqual(self.fx.state()["worker"].get("last_released_sha"), latest)
        self.assertEqual(self.fx.slice_marks, [])


class RecoveryCrash(BaseException):
    pass


class ReleaseJournalRecovery(Base):
    def case(self, lane, checkpoint=True):
        self.case_ordinal = getattr(self, "case_ordinal", 0) + 1
        latest = self.fx.commit({"mcp-server/src/a.js" if lane == "worker" else "src/worker.js": f"{lane}-{self.case_ordinal}"})
        live = ({"sha": self.fx.base, "version": PRIOR} if lane == "worker" else
                {"source_commit": self.fx.base, "environment": "production", "provider_version_id": PRIOR_APP})
        runner = FakeRunner(live=live if lane == "worker" else None)
        cfg = self.fx.config()
        cfg["app"]["enabled"] = True
        if lane == "app":
            original = runner.run

            def run(argv, **kwargs):
                result = original(argv, **kwargs)
                if argv[:2] == ["node", "scripts/release-production.mjs"]:
                    live.update(source_commit=latest, provider_version_id=VERSION)
                return result

            runner.run = run

        def pipeline():
            pipe = self.fx.pipeline(runner, cfg=cfg, live=live if lane == "worker" else None)
            if lane == "app":
                pipe.http = lambda _url: dict(live)
            return pipe

        pipe = pipeline()
        pipe.store.save({lane: {"last_released_sha": self.fx.base}} if checkpoint else {})
        pipe.store.records_path.unlink(missing_ok=True)
        self.fx.slice_marks.clear()
        return latest, live, runner, pipe, pipeline

    def assert_one_release(self, lane, latest, runner):
        self.assertEqual(runner.names().count("upload" if lane == "worker" else "app-release"), 1)
        if lane == "worker":
            self.assertEqual(runner.names().count("promote"), 1)
        rows = [row for row in rp.Store(self.fx.repo / "out/release-pipeline").records()
                if row.get("lane") == lane and row["status"] == "shipped"]
        self.assertEqual([row["sha"] for row in rows], [latest])

    def test_restart_after_shipped_before_checkpoint_never_redeploys_or_marks_twice(self):
        for lane in ["worker", "app"]:
            with self.subTest(lane=lane):
                latest, live, runner, pipe, restart = self.case(lane)
                original = pipe.store.save

                def save(state):
                    if state[lane].get("last_released_sha") == latest:
                        raise RecoveryCrash()
                    original(state)

                with mock.patch.object(pipe.store, "save", side_effect=save), self.assertRaises(RecoveryCrash):
                    pipe.tick([lane])
                marks = list(self.fx.slice_marks)
                original_row = self.fx.records()[-1]
                self.assertEqual(restart().tick([lane]), 0)
                self.assert_one_release(lane, latest, runner)
                self.assertEqual(self.fx.slice_marks, marks)
                self.assertEqual(self.fx.state()[lane]["last_released_sha"], latest)
                self.assertEqual([row for row in self.fx.records() if row["status"] == "shipped" and row.get("lane") == lane], [original_row])

    def test_restart_after_verified_proof_finishes_original_identity(self):
        for lane in ["worker", "app"]:
            with self.subTest(lane=lane):
                latest, live, runner, pipe, restart = self.case(lane)
                original = pipe.store.record_durable

                def record(row):
                    original(row)
                    if row["status"] == "release_verified":
                        raise RecoveryCrash()

                with mock.patch.object(pipe.store, "record_durable", side_effect=record), self.assertRaises(RecoveryCrash):
                    pipe.tick([lane])
                proof = self.fx.records()[-1]
                self.assertEqual(proof["live_identity"]["url"],
                                 "https://api.doctorcre.com" if lane == "worker" else "https://app.doctorcre.com")
                self.assertEqual(restart().tick([lane]), 0)
                self.assert_one_release(lane, latest, runner)
                final = self.fx.records()[-1]
                self.assertEqual(final["run_id"], proof["run_id"])
                if lane == "worker":
                    self.assertEqual(final["release_key"], proof["release_key"])
                    self.assertEqual(final["provider_version_id"], proof["provider_version_id"])

    def test_restart_after_failed_append_with_or_without_checkpoint_stays_open(self):
        for lane in ["worker", "app"]:
            for checkpoint in [True, False]:
                with self.subTest(lane=lane, checkpoint=checkpoint):
                    latest, live, runner, pipe, restart = self.case(lane, checkpoint)
                    with mock.patch.object(pipe.store, "record_durable", side_effect=OSError("fixture append failed")):
                        self.assertEqual(pipe.tick([lane]), 1)
                    before = len(runner.calls)
                    self.assertEqual(restart().tick([lane]), 0)
                    self.assertEqual(len(runner.calls), before)
                    self.assertEqual(self.fx.state()[lane].get("last_released_sha"), self.fx.base if checkpoint else None)
                    self.assertFalse([row for row in self.fx.records() if row.get("lane") == lane and row.get("sha") == latest and row["status"] == "shipped"])

    def test_failed_append_after_canary_target_behind_main_never_redeploys(self):
        for checkpoint in [True, False]:
            with self.subTest(checkpoint=checkpoint):
                latest, live, runner, pipe, restart = self.case("worker", checkpoint)
                self.fx.commit({"docs/recovery-later.md": f"main advanced-{self.case_ordinal}"})
                with mock.patch.object(pipe, "release_target", return_value=latest), \
                     mock.patch.object(pipe.store, "record_durable", side_effect=OSError("fixture append failed")):
                    self.assertEqual(pipe.tick(["worker"]), 1)
                before = len(runner.calls)
                again = restart()
                with mock.patch.object(again, "release_target", return_value=latest):
                    self.assertEqual(again.tick(["worker"]), 0)
                self.assertEqual(len(runner.calls), before)
                self.assertEqual(self.fx.state()["worker"].get("last_released_sha"), self.fx.base if checkpoint else None)
                self.assertFalse([row for row in self.fx.records() if row.get("lane") == "worker" and row["status"] == "shipped"])

    def test_restart_after_terminal_persistence_failure_finishes_original_proof(self):
        for lane in ["worker", "app"]:
            for checkpoint in [True, False]:
                for boundary in ["append", "readback"]:
                    with self.subTest(lane=lane, checkpoint=checkpoint, boundary=boundary):
                        latest, live, runner, pipe, restart = self.case(lane, checkpoint)
                        original_record = pipe.store.record
                        original_readback = pipe.store.readback_durable

                        def record(row):
                            if boundary == "append" and row["status"] == "shipped":
                                raise OSError("fixture terminal append failed")
                            original_record(row)

                        def readback(row):
                            if boundary == "readback" and row["status"] == "shipped":
                                raise OSError("fixture terminal readback failed")
                            original_readback(row)

                        with mock.patch.object(pipe.store, "record", side_effect=record), \
                             mock.patch.object(pipe.store, "readback_durable", side_effect=readback):
                            self.assertEqual(pipe.tick([lane]), 1)
                        marks = list(self.fx.slice_marks)
                        proof = next(row for row in self.fx.records() if row["status"] == "release_verified")
                        self.assertEqual(restart().tick([lane]), 0)
                        self.assert_one_release(lane, latest, runner)
                        self.assertEqual(self.fx.slice_marks, marks)
                        shipped = next(row for row in self.fx.records() if row["status"] == "shipped")
                        self.assertEqual(shipped["run_id"], proof["run_id"])
                        self.assertEqual(self.fx.state()[lane]["last_shipped_proof"], proof["proof_digest"])

    def test_restart_after_readback_failure_recovers_written_proof_without_redeploy(self):
        for lane in ["worker", "app"]:
            for checkpoint in [True, False]:
                with self.subTest(lane=lane, checkpoint=checkpoint):
                    latest, live, runner, pipe, restart = self.case(lane, checkpoint)
                    with mock.patch.object(pipe.store, "records", return_value=[]):
                        self.assertEqual(pipe.tick([lane]), 1)
                    self.assertEqual(restart().tick([lane]), 0)
                    self.assert_one_release(lane, latest, runner)

    def test_restart_after_marker_claim_or_launch_never_launches_twice(self):
        for boundary in ["claim", "launch"]:
            with self.subTest(boundary=boundary):
                latest, live, runner, pipe, restart = self.case("worker")
                original = pipe.store.record_durable
                original_marker = pipe.slice_marker

                def record(row):
                    original(row)
                    if boundary == "claim" and row["status"] == "slice_marker_claimed":
                        raise RecoveryCrash()

                def marker(key, sha):
                    original_marker(key, sha)
                    raise RecoveryCrash()

                with mock.patch.object(pipe.store, "record_durable", side_effect=record), \
                     mock.patch.object(pipe, "slice_marker", side_effect=marker if boundary == "launch" else original_marker), \
                     self.assertRaises(RecoveryCrash):
                    pipe.tick(["worker"])
                marks = list(self.fx.slice_marks)
                self.assertEqual(restart().tick(["worker"]), 0)
                self.assert_one_release("worker", latest, runner)
                self.assertEqual(self.fx.slice_marks, marks)
                self.assertIsNone(self.fx.records()[-1]["slice_marker"]["started"])

    def test_torn_terminal_append_restarts_without_duplicate_release_or_marker(self):
        for lane in ["worker", "app"]:
            with self.subTest(lane=lane):
                latest, live, runner, pipe, restart = self.case(lane)
                original = pipe.store.record

                def record(row):
                    if row["status"] == "shipped":
                        with pipe.store.records_path.open("ab") as fh:
                            fh.write(b'{"status":"shipped",')
                            fh.flush()
                            os.fsync(fh.fileno())
                        raise RecoveryCrash()
                    original(row)

                with mock.patch.object(pipe.store, "record", side_effect=record), self.assertRaises(RecoveryCrash):
                    pipe.tick([lane])
                marks = list(self.fx.slice_marks)
                self.assertEqual(restart().tick([lane]), 0)
                self.assert_one_release(lane, latest, runner)
                self.assertEqual(self.fx.slice_marks, marks)
                self.assertTrue(pipe.store.records_path.read_bytes().endswith(b"\n"))
                self.assertIn(b'{"status":"shipped",\n', pipe.store.records_path.read_bytes())

    def test_corrupt_attempt_binding_cannot_be_reconciled(self):
        for lane in ["worker", "app"]:
            with self.subTest(lane=lane):
                latest, live, runner, pipe, restart = self.case(lane)
                original = pipe.store.save

                def save(state):
                    if state[lane].get("last_released_sha") == latest:
                        raise RecoveryCrash()
                    original(state)

                with mock.patch.object(pipe.store, "save", side_effect=save), self.assertRaises(RecoveryCrash):
                    pipe.tick([lane])
                rows = self.fx.records()
                rows[-1]["run_id"] = "different-attempt"
                pipe.store.records_path.write_text("\n".join(json.dumps(row) for row in rows)+"\n")
                before = len(runner.calls)
                self.assertEqual(restart().tick([lane]), 0)
                self.assertEqual(len(runner.calls), before)
                self.assertEqual(self.fx.state()[lane].get("last_released_sha"), self.fx.base)

    def test_unreceipted_bootstrap_cannot_advance_source_only_checkpoint(self):
        for lane in ["worker", "app"]:
            with self.subTest(lane=lane):
                latest, live, runner, pipe, restart = self.case(lane, False)
                if lane == "worker":
                    live.update(sha=latest, version=VERSION)
                else:
                    live.update(source_commit=latest, provider_version_id=VERSION)
                self.fx.commit({"docs/bootstrap-later.md": f"later-{lane}"})
                self.assertEqual(pipe.tick([lane]), 0)
                self.assertIsNone(self.fx.state()[lane].get("last_released_sha"))
                self.assertFalse([row for row in self.fx.records() if row.get("lane") == lane and row["status"] in ["shipped", "no_release_needed"]])

    def test_completed_older_proofs_do_not_block_the_next_release(self):
        latest, live, runner, pipe, restart = self.case("worker")
        self.assertEqual(pipe.tick(["worker"]), 0)
        following = self.fx.commit({"mcp-server/src/a.js": "following"})
        following_runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(following_runner, live=live).tick(["worker"]), 0)
        self.assertEqual(following_runner.names().count("upload"), 1)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], following)
        self.assertEqual([row["sha"] for row in self.fx.records() if row["status"] == "shipped"], [latest, following])
        self.assertEqual(len(self.fx.slice_marks), 2)

    def test_changed_provider_identity_keeps_original_proof_incomplete(self):
        for lane in ["worker", "app"]:
            with self.subTest(lane=lane):
                latest, live, runner, pipe, restart = self.case(lane)
                original = pipe.store.record_durable

                def record(row):
                    original(row)
                    if row["status"] == "release_verified":
                        raise RecoveryCrash()

                with mock.patch.object(pipe.store, "record_durable", side_effect=record), self.assertRaises(RecoveryCrash):
                    pipe.tick([lane])
                live["version" if lane == "worker" else "provider_version_id"] = PRIOR
                before = len(runner.calls)
                self.assertEqual(restart().tick([lane]), 0)
                self.assertEqual(len(runner.calls), before)
                self.assertEqual(self.fx.state()[lane].get("last_released_sha"), self.fx.base)


class Batching(Base):
    def test_offline_live_poll_never_waits_on_the_wall_clock(self):
        with mock.patch.object(rp.time, "sleep", side_effect=AssertionError("real sleep in offline fixture")):
            pipe = self.fx.pipeline(FakeRunner())
            pipe.http = mock.Mock(return_value={"git_sha": {"value": self.fx.base}})
            matched, last, _, _ = pipe.await_live(pipe.cfg["worker"], "offline-poll",
                                                  lambda row: row["git_sha"]["value"] == "unserved-source",
                                                  attempts=3)
        self.assertFalse(matched)
        self.assertEqual(last["git_sha"]["value"], self.fx.base)
        self.assertEqual(pipe.http.call_count, 3)

    def test_many_merges_ship_once_at_the_latest_sha(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"mcp-server/src/b.js": "2"})
        latest = self.fx.commit({"docs/n.md": "3"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        uploads = [a for n, a in runner.calls if n == "upload"]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0][uploads[0].index("--release-sha") + 1], latest)
        upload = uploads[0]
        self.assertEqual(upload[upload.index("--verifier") + 1], "github-actions")
        self.assertEqual(upload[upload.index("--verifier-evidence") + 1],
                         upload[upload.index("--test-evidence") + 1])
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        shipped = [r for r in self.fx.records() if r["status"] == "shipped"]
        self.assertEqual([r["sha"] for r in shipped], [latest])
        self.assertEqual(shipped[0]["release_key"], "r-2026-09-30-02")  # -01 already exists
        promote = next(a for n, a in runner.calls if n == "promote")
        self.assertIn(VERSION, promote)
        self.assertEqual(runner.names().index("staging") + 1, runner.names().index("promote"))

    def test_a_shipped_release_runs_the_slice_marker_once_with_its_key(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), live=live).tick(["worker"]), 0)
        shipped = [r for r in self.fx.records() if r["status"] == "shipped"]
        self.assertEqual(self.fx.slice_marks, [(shipped[0]["release_key"], latest)])
        self.assertEqual(shipped[0]["slice_marker"], {"rc": 0})

    def test_a_failing_slice_marker_never_fails_the_release(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}

        def boom(_key, _sha):
            raise RuntimeError("marker exploded")

        rc = self.fx.pipeline(FakeRunner(live=live), live=live, slice_marker=boom).tick(["worker"])
        self.assertEqual(rc, 0)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["sha"]), ("shipped", latest))
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        self.assertIn("marker exploded", rec["slice_marker"]["error"])

    def test_no_slice_marker_without_a_shipped_release(self):
        self.fx.commit({"docs/n.md": "1"})                              # doc-only: nothing released
        self.assertEqual(self.fx.pipeline(FakeRunner()).tick(["worker"]), 0)
        self.fx.commit({"mcp-server/src/a.js": "1"})                    # a failed release
        self.assertEqual(self.fx.pipeline(FakeRunner(fail_at="staging-prepare")).tick(["worker"]), 1)
        self.fx.commit({"mcp-server/src/b.js": "1"})                    # a dry run
        self.assertEqual(self.fx.pipeline(FakeRunner(), dry_run=True).tick(["worker"]), 0)
        self.assertEqual(self.fx.slice_marks, [])

    def test_the_real_marker_is_started_detached_and_never_waited_for(self):
        # Step 10 must not hold the single-run lock: the marker is started in
        # its own session, its output goes to the run's log, and nothing waits
        # on it.
        pipe = self.fx.pipeline(FakeRunner())
        started = mock.MagicMock(pid=4242)
        with mock.patch.object(rp.subprocess, "Popen", return_value=started) as popen, \
                mock.patch.object(rp.subprocess, "run") as run:
            out = pipe._run_slice_marker("r-2026-09-30-01", "a" * 40)
        self.assertEqual(out, {"started": True, "pid": 4242, "release_sha": "a" * 40,
                               "log": str(pipe.run_dir / "slice-marker.log")})
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1:], [str(self.fx.repo / "ops" / "slice-done-marker.py"),
                                       "--release-key", "r-2026-09-30-01"])
        self.assertIs(kwargs["start_new_session"], True)
        self.assertIs(kwargs["stdin"], rp.subprocess.DEVNULL)
        self.assertEqual(set(kwargs["env"]) - {"HOME", "PATH", "LANG", "CARR_JEV_JOB"}, set())
        self.assertEqual(kwargs["env"]["CARR_JEV_JOB"], "release-pipeline.slice-marker",
                         "the detached marker's paid Jev calls are attributed to this job")
        run.assert_not_called()
        started.wait.assert_not_called()
        started.communicate.assert_not_called()

    def test_doc_only_batch_advances_without_release(self):
        latest = self.fx.commit({"docs/n.md": "3", "mcp-server/test/x.test.mjs": "t"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner)
        pipe.store.save({"worker": {"last_released_sha": self.fx.base}})
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        self.assertEqual(self.fx.records()[-1]["status"], "no_release_needed")

    def test_unicode_named_worker_source_is_not_skipped_as_docs_only(self):
        target = self.fx.commit({"mcp-server/src/unicodé.js": "source"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")
        self.assertEqual(self.fx.records()[-1]["sha"], target)
        self.assertIn("upload", runner.names())

    def test_trailing_space_source_name_is_not_treated_as_markdown(self):
        target = self.fx.commit({"mcp-server/src/runtime.md ": "source"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")
        self.assertEqual(self.fx.records()[-1]["sha"], target)
        self.assertIn("upload", runner.names())

    def test_released_main_is_a_noop(self):
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])

    def test_migrations_apply_only_when_pending(self):
        self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, pending=0)
        self.fx.pipeline(runner, live=live).tick(["worker"])
        self.assertIn("migrate-plan", runner.names())
        self.assertNotIn("migrate-apply", runner.names())


class StopOnFailure(Base):
    def test_failure_stops_records_dispatches_and_is_never_retried(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner, verbs = FakeRunner(fail_at="staging-prepare"), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        names = runner.names()
        self.assertEqual(names[-1], "staging-prepare")
        for later in ("staging-app-writer", "migrate-plan", "upload", "staging", "promote"):
            self.assertNotIn(later, names)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"], rec["rc"]), ("failed", "staging-prepare", 7))
        self.assertTrue(rec["log"].endswith("-staging-prepare.log"))
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])
        body = verbs[0][1]["body"]
        self.assertTrue(body.startswith("@queue enqueue target=claude-desktop cap=repo-write "))
        self.assertIn(f"key=release-fix-{sha[:8]} ", body)
        self.assertEqual(verbs[0][1]["room"], "model-room")
        # the next tick on the same SHA runs nothing and dispatches nothing more
        again = FakeRunner()
        self.assertEqual(self.fx.pipeline(again, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(again.calls, [])
        self.assertEqual(len(verbs), 1)

    def test_a_fix_forward_merge_is_released_fresh(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.pipeline(FakeRunner(fail_at="upload")).tick(["worker"])
        fixed = self.fx.commit({"mcp-server/src/a.js": "2"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], fixed)
        self.assertIsNone(self.fx.state()["worker"]["failed_sha"])

    def test_live_readback_mismatch_after_promotion_is_a_failure(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(live=None)  # production keeps serving the old SHA
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertEqual(self.fx.records()[-1]["step"], "verify-live")


class PrerequisiteRecovery(Base):
    def test_rejected_token_is_probed_until_repaired_without_redispatch(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs = []
        rejected = "Authentication error [code: 10000]"
        first = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": rejected})
        self.assertEqual(self.fx.pipeline(first, verbs=verbs).tick(["worker"]), 1)
        for _ in range(2):
            runner = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": rejected})
            self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 0)
            self.assertEqual(runner.names(), ["wrangler-auth"])
            self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])

    def test_auth_transport_failure_recovers_after_a_successful_probe(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs = []
        runner = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": "network unavailable"})
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_step"], "wrangler-auth")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])

    def test_auth_probe_install_failure_reaches_diagnosis_after_restart(self):
        for lane in ("worker", "app"):
            for fail_at in ("wrangler-install", None):
                with self.subTest(lane=lane, fail_at=fail_at), tempfile.TemporaryDirectory() as tmp:
                    fx = Fixture(Path(tmp))
                    sha = fx.commit({"mcp-server/src/a.js": "1", "src/worker.js": "1"})
                    cfg = fx.config()
                    cfg[lane]["enabled"] = True
                    verbs = []
                    def pipeline(runner):
                        pipe = fx.pipeline(runner, cfg=cfg, verbs=verbs)
                        if lane == "app":
                            pipe.http = lambda _u: {"source_commit": fx.base, "environment": "production"}
                        return pipe
                    (fx.cred / "tokens.env").write_text("")
                    self.assertEqual(pipeline(FakeRunner()).tick([lane]), 1)
                    (fx.cred / "tokens.env").write_text(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
                    shutil.rmtree(fx.repo / "out/release-pipeline/toolchain")
                    runner = FakeRunner(fail_at=fail_at)
                    self.assertEqual(pipeline(runner).tick([lane]), 1)
                    self.assertEqual(fx.state()[lane]["failed_step"], "wrangler-install")
                    self.assertEqual(fx.records()[-1]["step"], "wrangler-install")
                    self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn", "add-room-turn"])
                    retry = FakeRunner()
                    self.assertEqual(pipeline(retry).tick([lane]), 0)
                    self.assertEqual(retry.calls, [])
                    self.assertEqual(fx.state()[lane]["failed_sha"], sha)
                    self.assertEqual(len(verbs), 3)

    def test_second_auth_transport_failure_retains_first_diagnosis_across_restarts(self):
        for lane in ("worker", "app"):
            with self.subTest(lane=lane), tempfile.TemporaryDirectory() as tmp:
                fx = Fixture(Path(tmp))
                sha = fx.commit({"mcp-server/src/a.js": "1", "src/worker.js": "1"})
                cfg = fx.config()
                cfg[lane]["enabled"] = True
                verbs = []
                def pipeline(runner):
                    pipe = fx.pipeline(runner, cfg=cfg, verbs=verbs)
                    if lane == "app":
                        pipe.http = lambda _u: {"source_commit": fx.base, "environment": "production"}
                    return pipe
                first = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": "network unavailable"})
                self.assertEqual(pipeline(first).tick([lane]), 1)
                original_turn = verbs[0][1]
                for _ in range(2):
                    runner = FakeRunner(outputs={"wrangler-auth": "network unavailable"})
                    original_run = runner.run
                    def run(argv, **kw):
                        if kw["log"].stem.endswith("wrangler-auth"):
                            runner.fail_at = ("wrangler-auth" if "wrangler-auth" in runner.names() else None)
                        return original_run(argv, **kw)
                    runner.run = run
                    self.assertEqual(pipeline(runner).tick([lane]), 1)
                    self.assertEqual(runner.names(), ["wrangler-auth", "wrangler-auth"])
                    self.assertEqual(fx.state()[lane]["failed_step"], "wrangler-auth")
                    self.assertEqual(fx.state()[lane]["failed_sha"], sha)
                self.assertEqual(verbs, [("add-room-turn", original_turn)])
                self.assertEqual(fx.state()[lane]["dispatches"][sha], 1)

    def test_auth_probe_execution_failure_gets_its_own_diagnosis_after_restart(self):
        for lane in ("worker", "app"):
            for output in ("SyntaxError: Unexpected token", "Error [ERR_MODULE_NOT_FOUND]",
                           "unrecognized process failure", ""):
                with self.subTest(lane=lane, output=output), tempfile.TemporaryDirectory() as tmp:
                    fx = Fixture(Path(tmp))
                    sha = fx.commit({"mcp-server/src/a.js": "1", "src/worker.js": "1"})
                    cfg = fx.config()
                    cfg[lane]["enabled"] = True
                    verbs = []
                    def pipeline(runner):
                        pipe = fx.pipeline(runner, cfg=cfg, verbs=verbs)
                        if lane == "app":
                            pipe.http = lambda _u: {"source_commit": fx.base, "environment": "production"}
                        return pipe
                    (fx.cred / "tokens.env").write_text("")
                    self.assertEqual(pipeline(FakeRunner()).tick([lane]), 1)
                    (fx.cred / "tokens.env").write_text(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
                    runner = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": output})
                    self.assertEqual(pipeline(runner).tick([lane]), 1)
                    self.assertEqual(runner.names(), ["wrangler-auth"])
                    self.assertEqual(fx.state()[lane]["failed_step"], "wrangler-execution")
                    failure = fx.records()[-1]
                    self.assertEqual((failure["step"], failure["rc"]), ("wrangler-execution", 7))
                    self.assertEqual(Path(failure["log"]).read_text(), output)
                    self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn", "add-room-turn"])
                    self.assertIn("wrangler-execution", json.dumps(verbs[-1][1]))
                    for _ in range(2):
                        retry = FakeRunner()
                        self.assertEqual(pipeline(retry).tick([lane]), 0)
                        self.assertEqual(retry.calls, [])
                        self.assertEqual(fx.state()[lane]["failed_sha"], sha)
                    self.assertEqual(len(verbs), 3)

    def test_first_transport_failure_save_keeps_diagnosis_across_restarts(self):
        class Interrupted(BaseException):
            pass
        for lane in ("worker", "app"):
            with self.subTest(lane=lane), tempfile.TemporaryDirectory() as tmp:
                fx = Fixture(Path(tmp))
                sha = fx.commit({"mcp-server/src/a.js": "1", "src/worker.js": "1"})
                cfg = fx.config()
                cfg[lane]["enabled"] = True
                verbs = []
                def pipeline():
                    runner = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": "fetch failed"})
                    pipe = fx.pipeline(runner, cfg=cfg, verbs=verbs)
                    if lane == "app":
                        pipe.http = lambda _u: {"source_commit": fx.base, "environment": "production"}
                    return pipe, runner
                pipe, _ = pipeline()
                save = pipe.store.save
                def interrupt_after_failed_save(state):
                    save(state)
                    if state.get(lane, {}).get("failed_step") == "wrangler-auth":
                        raise Interrupted()
                pipe.store.save = interrupt_after_failed_save
                with self.assertRaises(Interrupted):
                    pipe.tick([lane])
                self.assertEqual(fx.state()[lane]["failed_sha"], sha)
                self.assertEqual(verbs, [])
                for _ in range(2):
                    retry, runner = pipeline()
                    self.assertEqual(retry.tick([lane]), 0)
                    self.assertEqual(runner.names(), ["wrangler-auth"])
                self.assertEqual([v for v, _ in verbs], ["add-room-turn"])
                self.assertEqual(fx.state()[lane]["dispatches"][sha], 1)
                self.assertEqual(fx.state()["pending_diagnoses"], {})

    def test_code_failures_still_require_a_fix_forward_merge(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        pipe = self.fx.pipeline(FakeRunner())
        for step in ("app-build", "npm-ci", "wrangler-install", "staging", "migrate-apply", "smoke-post"):
            with self.subTest(step=step):
                pipe.store.save({"worker": {"failed_sha": sha, "failed_step": step}})
                runner, verbs = FakeRunner(), []
                self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 0)
                self.assertEqual(runner.calls, [])
                self.assertEqual(verbs, [])
                self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)

    def test_tracked_venv_link_conflict_waits_for_fix_forward_after_restart(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        blob = git(self.fx.repo, "hash-object", "-w", "README.md")
        git(self.fx.repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},.venv")
        git(self.fx.repo, "commit", "-q", "-m", "tracked venv conflict")
        git(self.fx.repo, "push", "-q", "origin", "HEAD:main")
        sha = git(self.fx.repo, "rev-parse", "HEAD")
        verbs = []
        runner = RealGitRunner("")
        original_run = runner.run
        def run(argv, **kw):
            if argv[0] == "ln":
                runner.calls.append(("venv-link", list(argv)))
                result = subprocess.run(argv, cwd=kw["cwd"], env=kw["env"], capture_output=True, text=True)
                kw["log"].parent.mkdir(parents=True, exist_ok=True)
                kw["log"].write_text(result.stdout + result.stderr)
                return rp.Result(result.returncode, result.stdout + result.stderr)
            return original_run(argv, **kw)
        runner.run = run
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_step"], "venv-link")
        self.assertIn("File exists", Path(self.fx.records()[-1]["log"]).read_text())
        for _ in range(2):
            retry = FakeRunner()
            self.assertEqual(self.fx.pipeline(retry, verbs=verbs).tick(["worker"]), 0)
            self.assertEqual(retry.calls, [])
            self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])

    def test_missing_venv_is_probed_before_any_release_step_then_recovers(self):
        python = self.fx.repo / ".venv/bin/python"
        python.unlink()
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs = []
        self.assertEqual(self.fx.pipeline(FakeRunner(), verbs=verbs).tick(["worker"]), 1)
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        python.write_text("#!/bin/sh\nexit 0\n")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])


class PostReleaseProof(Base):
    """After every production release the live system is proven by
    ops/release-smoke.py: a journey that passed before the release and fails
    after it (twice: one retry absorbs a flake) marks the release FAILED, rolls
    the lane back and files one loop naming the journey and its evidence."""

    def worker(self, runner, verbs=None, live=None):
        live = live if live is not None else {"sha": self.fx.base}
        runner.live = live
        verbs = verbs if verbs is not None else []
        rc = self.fx.pipeline(runner, live=live, verbs=verbs).tick(["worker"])
        return rc, verbs, live

    def argv(self, runner, name):
        return next(a for n, a in runner.calls if n == name)


    def test_crash_cannot_reuse_an_earlier_green_summary(self):
        sha = self.fx.base
        old = self.fx.repo / rp.SMOKE_OUT / sha / 'post'
        old.mkdir(parents=True)
        (old / 'summary.json').write_text(json.dumps({'failed': [], 'probes': []}))
        pipe = self.fx.pipeline(FakeRunner(smoke={'smoke-post': None}))
        self.assertIsNone(pipe.smoke_run('worker', sha, 'post'))

    def test_smoke_summary_must_bind_source_and_required_probes(self):
        class WrongRunner(FakeRunner):
            def _smoke_output(inner, name, argv):
                result = super()._smoke_output(name, argv)
                path = Path(argv[argv.index('--out') + 1]) / 'summary.json'
                value = json.loads(path.read_text())
                value['sha'] = '0' * 40
                path.write_text(json.dumps(value))
                return result
        self.assertIsNone(self.fx.pipeline(WrongRunner()).smoke_run('worker', self.fx.base, 'post'))

    def test_app_summary_cannot_skip_required_browser_proof(self):
        class SkippedRunner(FakeRunner):
            def _smoke_output(inner, name, argv):
                result = super()._smoke_output(name, argv)
                path = Path(argv[argv.index('--out') + 1]) / 'summary.json'
                value = json.loads(path.read_text())
                next(p for p in value['probes'] if p['id'] == 'browser-journeys')['status'] = 'skip'
                path.write_text(json.dumps(value))
                return result
        self.assertIsNone(self.fx.pipeline(SkippedRunner()).smoke_run('app', self.fx.base, 'post'))

    def test_pass_ships_with_baseline_before_any_apply_and_proof_after_promotion(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        rc, verbs, _ = self.worker(runner)
        self.assertEqual(rc, 0)
        names = runner.names()
        self.assertLess(names.index("smoke-baseline"), names.index("staging-prepare"))
        self.assertLess(names.index("promote"), names.index("smoke-post"))
        self.assertNotIn("smoke-retry", names)
        self.assertNotIn("rollback", names)
        post = self.argv(runner, "smoke-post")
        self.assertEqual(post[post.index("--phase") + 1], "post")
        self.assertEqual(post[post.index("--sha") + 1], sha)
        self.assertTrue(post[post.index("--worker-dir") + 1].endswith("mcp-server"))
        evidence = Path(post[post.index("--out") + 1])
        self.assertEqual(evidence.parent, self.fx.repo / "out" / "release-smoke" / sha)
        self.assertTrue(evidence.name.startswith("post-"))
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "shipped")
        self.assertEqual(rec["post_release"]["regressions"], [])
        self.assertEqual(rec["post_release"]["evidence_dir"], str(evidence.parent))
        self.assertNotIn("add-loop", [v for v, _ in verbs])

    def test_fail_marks_failed_rolls_the_worker_back_and_files_one_loop(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(smoke={"smoke-post": ["invoices-list"], "smoke-retry": ["invoices-list"]})
        rc, verbs, live = self.worker(runner)
        self.assertEqual(rc, 1)
        rollback = self.argv(runner, "rollback")
        self.assertEqual(rollback[:3], ["bin/deploy-worker.sh", "--promote-version", PRIOR])
        self.assertEqual(rollback[rollback.index("--recovery-strategy") + 1], "rollback")
        self.assertEqual(rollback[rollback.index("--rollback-plan-ref") + 1], "runbooks/rollback-worker.md")
        self.assertEqual(live["version"], PRIOR)
        self.assertLess(runner.names().index("smoke-retry"), runner.names().index("rollback"))
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "post-release-smoke"))
        self.assertEqual(rec["post_release"]["regressions"], ["invoices-list"])
        self.assertEqual(rec["post_release"]["rollback"]["ok"], True)
        self.assertEqual(rec["post_release"]["rollback"]["served_version"], PRIOR)
        state = self.fx.state()["worker"]
        self.assertEqual(state["failed_sha"], sha)
        self.assertNotEqual(state.get("last_released_sha"), sha)
        loops = [a for v, a in verbs if v == "add-loop"]
        self.assertEqual(len(loops), 1)
        body = loops[0]["body"]
        self.assertIn("invoices-list", body)
        self.assertIn("invoices-list broke", body)
        self.assertIn("screenshots/x.png", body)
        self.assertIn("trace.zip", body)
        self.assertIn("rolled back", body)
        self.assertEqual(loops[0]["kind"], "open_loop")
        self.assertTrue(loops[0]["idempotency_key"])
        self.assertIn("add-room-turn", [v for v, _ in verbs])   # the fix-forward diagnosis still goes

    def test_a_journey_that_passes_on_its_one_retry_is_flaky_and_ships(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(smoke={"smoke-post": ["deal-board"]})
        rc, verbs, _ = self.worker(runner)
        self.assertEqual(rc, 0)
        retry = self.argv(runner, "smoke-retry")
        self.assertEqual(retry[retry.index("--only") + 1], "deal-board")
        self.assertEqual(runner.names().count("smoke-retry"), 1)
        self.assertNotIn("rollback", runner.names())
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["post_release"]["flaky"]), ("shipped", ["deal-board"]))
        self.assertNotIn("add-loop", [v for v, _ in verbs])

    def test_a_failure_already_present_before_the_release_is_not_this_releases(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(smoke={"smoke-baseline": ["invoices-list"], "smoke-post": ["invoices-list"],
                                   "smoke-retry": ["invoices-list"]})
        rc, _, _ = self.worker(runner)
        self.assertEqual(rc, 0)
        self.assertNotIn("rollback", runner.names())
        rec = self.fx.records()[-1]
        self.assertEqual(rec["post_release"]["preexisting"], ["invoices-list"])
        self.assertEqual(rec["post_release"]["regressions"], [])

    def test_identity_and_released_verbs_are_never_excused_by_the_baseline(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(smoke={"smoke-baseline": ["verb-registry"], "smoke-post": ["verb-registry"],
                                   "smoke-retry": ["verb-registry"]})
        rc, _, _ = self.worker(runner)
        self.assertEqual(rc, 1)
        self.assertEqual(self.fx.records()[-1]["post_release"]["regressions"], ["verb-registry"])

    def test_a_release_that_migrated_production_is_never_rolled_back(self):
        self.fx.commit({"mcp-server/src/a.js": "1", "migrations/0999_x.sql": "select 1;"})
        runner = FakeRunner(pending=1, smoke={"smoke-post": ["deal-board"], "smoke-retry": ["deal-board"]})
        rc, verbs, _ = self.worker(runner)
        self.assertEqual(rc, 1)
        self.assertNotIn("rollback", runner.names())
        rb = self.fx.records()[-1]["post_release"]["rollback"]
        self.assertFalse(rb["attempted"])
        self.assertIn("forward fix", rb["reason"])
        body = next(a for v, a in verbs if v == "add-loop")["body"]
        self.assertIn("NOT rolled back", body)

    def test_a_refused_rollback_is_recorded_and_still_fails_the_release(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(fail_at="rollback", smoke={"smoke-post": ["deal-board"], "smoke-retry": ["deal-board"]})
        rc, verbs, _ = self.worker(runner)
        self.assertEqual(rc, 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "post-release-smoke")
        self.assertEqual((rec["post_release"]["rollback"]["attempted"], rec["post_release"]["rollback"]["ok"]),
                         (True, False))
        self.assertIn("rollback FAILED", next(a for v, a in verbs if v == "add-loop")["body"])

    def test_a_rollback_production_never_reads_back_is_a_failed_rollback(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(smoke={"smoke-post": ["deal-board"], "smoke-retry": ["deal-board"]})
        live = {"sha": self.fx.base}
        runner.live = live
        orig = runner.run

        def run(argv, **kw):
            res = orig(argv, **kw)
            if "--promote-version" in argv and argv[argv.index("--promote-version") + 1] == PRIOR:
                live["version"] = VERSION      # exit 0, but production still serves the release
            return res
        runner.run = run  # type: ignore[method-assign]
        verbs: list = []
        pipe = self.fx.pipeline(runner, live=live, verbs=verbs)
        pipe.sleep = lambda _s: None
        self.assertEqual(pipe.tick(["worker"]), 1)
        rb = self.fx.records()[-1]["post_release"]["rollback"]
        self.assertEqual((rb["attempted"], rb["ok"], rb["served_version"]), (True, False, VERSION))
        self.assertIn("rollback FAILED", next(a for v, a in verbs if v == "add-loop")["body"])

    def test_an_unreadable_smoke_fails_the_release_but_never_rolls_back(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(smoke={"smoke-post": None, "smoke-retry": None})
        rc, verbs, _ = self.worker(runner)
        self.assertEqual(rc, 1)
        self.assertNotIn("rollback", runner.names())
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "post-release-smoke")
        self.assertFalse(rec["post_release"]["rollback"]["attempted"])
        self.assertEqual(len([v for v, _ in verbs if v == "add-loop"]), 1)

    def test_the_stage_has_its_own_off_switch(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        cfg = self.fx.config()
        cfg["worker"]["post_release_smoke"] = False
        runner = FakeRunner(smoke={"smoke-post": ["deal-board"], "smoke-retry": ["deal-board"]})
        live = {"sha": self.fx.base}
        runner.live = live
        self.assertEqual(self.fx.pipeline(runner, cfg=cfg, live=live).tick(["worker"]), 0)
        self.assertFalse([n for n in runner.names() if n.startswith("smoke-")])

    def app_cfg(self):
        cfg = self.fx.config()
        cfg["app"]["enabled"] = True
        return cfg

    def app(self, runner, sha, verbs):
        pipe = self.fx.pipeline(runner, cfg=self.app_cfg(), verbs=verbs)
        live = {"source_commit": self.fx.base, "environment": "production", "provider_version_id": PRIOR_APP}
        pipe.http = lambda _u: dict(live)
        orig = runner.run

        def run(argv, **kw):
            res = orig(argv, **kw)
            if argv[:2] == ["node", "scripts/release-production.mjs"]:
                live.update(source_commit=sha, provider_version_id=VERSION)
            if argv[:2] == ["node", "scripts/rollback-production.mjs"]:
                live.update(source_commit=self.fx.base, provider_version_id=argv[2])
            return res
        runner.run = run  # type: ignore[method-assign]
        return pipe.tick(["app"]), live

    def test_missing_browser_proof_cannot_be_excused_by_the_baseline(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner(smoke={name: ["browser-journeys"] for name in
                                  ("smoke-baseline", "smoke-post", "smoke-retry")})
        rc, _ = self.app(runner, sha, [])
        self.assertEqual(rc, 1)
        self.assertEqual(self.fx.records()[-1]["post_release"]["regressions"], ["browser-journeys"])

    def test_app_pass_runs_the_browser_journeys_from_the_release_checkout(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        rc, _ = self.app(runner, sha, [])
        self.assertEqual(rc, 0)
        names = runner.names()
        self.assertLess(names.index("smoke-baseline"), names.index("app-release"))
        self.assertLess(names.index("app-release"), names.index("smoke-post"))
        post = self.argv(runner, "smoke-post")
        self.assertEqual(post[post.index("--lane") + 1], "app")
        self.assertEqual(post[post.index("--app-dir") + 1], runner.cwds["app-release"])
        self.assertNotIn("--worker-dir", post)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_a_browser_test_already_failing_before_the_release_is_excused_per_test(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner(smoke={name: ["browser-journeys::a.e2e.ts::one"] for name in
                                  ("smoke-baseline", "smoke-post", "smoke-retry")})
        rc, _ = self.app(runner, sha, [])
        self.assertEqual(rc, 0)
        self.assertEqual(self.fx.records()[-1]["post_release"]["preexisting"], ["browser-journeys::a.e2e.ts::one"])

    def test_a_pre_existing_browser_failure_never_excuses_a_new_one(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner(smoke={"smoke-baseline": ["browser-journeys::a.e2e.ts::one"],
                                   "smoke-post": ["browser-journeys::a.e2e.ts::one", "browser-journeys::b.e2e.ts::two"],
                                   "smoke-retry": ["browser-journeys::a.e2e.ts::one", "browser-journeys::b.e2e.ts::two"]})
        rc, _ = self.app(runner, sha, [])
        self.assertEqual(rc, 1)
        rec = self.fx.records()[-1]["post_release"]
        self.assertEqual(rec["regressions"], ["browser-journeys::b.e2e.ts::two"])
        self.assertEqual(rec["preexisting"], ["browser-journeys::a.e2e.ts::one"])
        self.assertIn("app-rollback", runner.names())

    def assert_browser_regression_across_granularity(self, post, retry):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner(smoke={"smoke-post": post, "smoke-retry": retry})
        rc, _ = self.app(runner, sha, [])
        self.assertEqual(rc, 1)
        rec = self.fx.records()[-1]["post_release"]
        self.assertEqual(rec["flaky"], [])
        self.assertEqual(rec["regressions"], ["browser-journeys"])
        self.assertIn("app-rollback", runner.names())

    def test_a_whole_browser_failure_then_a_named_one_is_a_regression_not_a_flake(self):
        self.assert_browser_regression_across_granularity(
            ["browser-journeys"], ["browser-journeys::b.e2e.ts::two"])

    def test_a_named_browser_failure_then_a_whole_one_is_a_regression_not_a_flake(self):
        self.assert_browser_regression_across_granularity(
            ["browser-journeys::b.e2e.ts::two"], ["browser-journeys"])

    def test_each_lane_proves_only_its_own_journeys(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.app(runner, sha, [])[0], 0)
        self.assertEqual(rp.SMOKE_LANE_JOURNEYS["app"], ("release-identity", "sign-in-gate", "browser-journeys"))
        self.assertNotIn("deal-board", rp.SMOKE_LANE_JOURNEYS["app"])
        self.assertNotIn("sign-in-gate", rp.SMOKE_LANE_JOURNEYS["worker"])

    def test_a_failure_pre_existing_in_two_consecutive_releases_files_a_coverage_loop(self):
        failing = {name: ["invoices-list"] for name in ("smoke-baseline", "smoke-post", "smoke-retry")}
        self.fx.commit({"mcp-server/src/a.js": "1"})
        rc, verbs, live = self.worker(FakeRunner(smoke=failing))
        self.assertEqual(rc, 0)
        self.assertEqual([a for v, a in verbs if v == "add-loop"], [])   # once may be a passing outage
        self.fx.commit({"mcp-server/src/b.js": "2"})
        runner = FakeRunner(smoke=failing)
        verbs2: list = []
        live = {"sha": self.fx.state()["worker"]["last_released_sha"]}
        runner.live = live
        self.assertEqual(self.fx.pipeline(runner, live=live, verbs=verbs2).tick(["worker"]), 0)
        loops = [a for v, a in verbs2 if v == "add-loop"]
        self.assertEqual(len(loops), 1)
        self.assertIn("invoices-list", loops[0]["body"])
        self.assertIn("two consecutive", loops[0]["body"])
        self.assertEqual(self.fx.records()[-1]["post_release"]["persistent_preexisting"], ["invoices-list"])

    def test_a_missing_smoke_switch_refuses_the_lane_before_anything_runs(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        cfg = self.fx.config()
        del cfg["worker"]["post_release_smoke"]
        runner = FakeRunner()
        live = {"sha": self.fx.base}
        runner.live = live
        self.fx.pipeline(runner, cfg=cfg, live=live).tick(["worker"])
        self.assertNotIn("promote", runner.names())
        self.assertFalse([n for n in runner.names() if n.startswith("smoke-")])

    def test_app_fail_rolls_back_to_the_previous_app_version(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner(smoke={"smoke-post": ["browser-journeys"], "smoke-retry": ["browser-journeys"]})
        verbs: list = []
        rc, live = self.app(runner, sha, verbs)
        self.assertEqual(rc, 1)
        self.assertEqual(self.argv(runner, "app-rollback"), ["node", "scripts/rollback-production.mjs", PRIOR_APP])
        self.assertEqual(runner.cwds["app-rollback"], runner.cwds["app-release"])
        self.assertIn("CLOUDFLARE_API_TOKEN", runner.envs["app-rollback"])
        self.assertEqual(live["provider_version_id"], PRIOR_APP)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "post-release-smoke"))
        self.assertTrue(rec["post_release"]["rollback"]["ok"])
        self.assertEqual(len([v for v, _ in verbs if v == "add-loop"]), 1)
        self.assertNotEqual(self.fx.state()["app"].get("last_released_sha"), sha)

class StagingGuard(Base):
    def test_failed_staging_never_promotes(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(fail_at="staging")
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertIn("upload", runner.names())
        self.assertNotIn("promote", runner.names())
        self.assertEqual(self.fx.records()[-1]["step"], "staging")


DO_V0 = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
DO_MARKER = f"DO migration applied: tag=v1-workflow-census-anchor from=none version={DO_V0}\n"


class DurableObjectMigration(Base):
    """bin/deploy-worker.sh applies a pending Durable Object migration inside
    the upload step; the pipeline carries its marker into the run record."""

    def test_no_migration_records_none(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), live=live).tick(["worker"]), 0)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "shipped")
        self.assertIsNone(rec["do_migration"])

    def test_applied_migration_is_recorded_and_the_normal_path_continues(self):
        self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        live = {"sha": self.fx.base}
        # The migration deploy's own version is the candidate the wrapper prints.
        runner = FakeRunner(live=live, outputs={
            "upload": DO_MARKER + f"Durable Object migration release\n  provider version: {DO_V0}\n"})
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        names = runner.names()
        self.assertLess(names.index("upload"), names.index("staging"))
        self.assertLess(names.index("staging"), names.index("promote"))
        self.assertIn("health", names)
        promote = next(a for n, a in runner.calls if n == "promote")
        self.assertEqual(promote[promote.index("--promote-version") + 1], DO_V0)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "shipped")
        self.assertEqual(rec["provider_version_id"], DO_V0)
        self.assertEqual(rec["do_migration"], {"applied": True, "possibly_applied": False,
                                               "tag": "v1-workflow-census-anchor",
                                               "from_tag": None, "provider_version_id": DO_V0})

    def test_upload_failure_after_the_migration_dispatches_forward_fix(self):
        sha = self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        runner, verbs = FakeRunner(fail_at="upload", outputs={
            "upload": DO_MARKER + "REFUSED: the migration is applied, but Production /release did not read back\n"}), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertNotIn("staging", runner.names())
        self.assertNotIn("promote", runner.names())
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "upload"))
        self.assertEqual(rec["do_migration"]["tag"], "v1-workflow-census-anchor")
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        body = verbs[0][1]["body"]
        self.assertIn("A DURABLE OBJECT MIGRATION WAS APPLIED", body)
        self.assertIn("forward", body)

    def test_possibly_applied_migration_dispatches_forward_fix(self):
        self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        runner, verbs = FakeRunner(fail_at="upload", outputs={
            "upload": "DO migration possibly applied: tag=v1-workflow-census-anchor from=none version=unknown\n"
                      "REFUSED: the migration deploy exited 1 and the Worker reports tag unknown\n"}), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["do_migration"]["possibly_applied"], True)
        self.assertEqual(rec["do_migration"]["applied"], False)
        body = verbs[0][1]["body"]
        self.assertIn("A DURABLE OBJECT MIGRATION WAS POSSIBLY APPLIED", body)
        self.assertIn("forward", body)
        self.assertIn("blocks rollback", body)

    def test_upload_failure_without_the_marker_claims_no_migration(self):
        self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        runner, verbs = FakeRunner(fail_at="upload", outputs={
            "upload": "REFUSED: the target Worker's applied Durable Object migration tag could not be determined\n"}), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertIsNone(self.fx.records()[-1]["do_migration"])
        self.assertNotIn("DURABLE OBJECT MIGRATION", verbs[0][1]["body"])

    def test_marker_parser(self):
        self.assertIsNone(rp.parse_do_migration("uploaded only\n"))
        got = rp.parse_do_migration("x\nDO migration applied: tag=v2 from=v1 version=unknown\n")
        self.assertEqual(got, {"applied": True, "possibly_applied": False, "tag": "v2", "from_tag": "v1",
                               "provider_version_id": None})
        got = rp.parse_do_migration(f"DO migration possibly applied: tag=v2 from=none version={DO_V0}\n")
        self.assertEqual(got, {"applied": False, "possibly_applied": True, "tag": "v2", "from_tag": None,
                               "provider_version_id": DO_V0})


def _finding(key, detail, *, subject="", count=1, hard_error=False, time_rolling=False):
    return {"key": key, "subject": subject, "detail": detail, "count": count,
            "hard_error": hard_error, "time_rolling": time_rolling}


RULE_GAPS_98 = _finding("rule_enforcement", "98 active rule gaps", count=98)
RULE_GAPS_97 = _finding("rule_enforcement", "97 active rule gaps", count=97)
RULE_GAPS_99 = _finding("rule_enforcement", "99 active rule gaps", count=99)


def scheduled_job_findings(snapshot):
    """Run the report and its shipped health adapter without live machine readers."""
    sys.path.insert(0, str(HERE.parent / "lib"))
    from lib import scheduled_jobs
    tree = ast.parse((HERE.parent / "tools/health-check.py").read_text())
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "_red"
                and node.args and isinstance(node.args[0], ast.BinOp)
                and isinstance(node.args[0].left, ast.Constant)
                and node.args[0].left.value == "scheduled_jobs_")
    adapter = compile(ast.Expression(call), "health scheduled-job adapter", "eval")
    rows = scheduled_jobs.report({"jobs": []}, snapshot, now=0)
    return [eval(adapter, {"_red": _finding, "_job_row": row, "_line": line})
            for row, line in zip(rows, scheduled_jobs.render(rows))]


class HealthCompleteMarkerDoesNotDrift(unittest.TestCase):
    """Point 3 of the third round of an independent review of PR #1237: a
    REAL run of health-preflight showed `complete=False` on every single
    read, baseline and live alike, even though tools/health-check.py had
    printed its completion marker as the true last line of stdout and
    written a well-formed findings.json. The cause: ops/release-pipeline.py's
    HEALTH_COMPLETE_MARKER constant was a truncated PREFIX of the actual
    `print(...)` call in tools/health-check.py — missing "; use --recovery
    --reason <why>." — so the exact-string-equality check in
    read_health_findings() never matched, no matter how clean the read.
    Every FakeRunner-based selftest passed anyway because FakeRunner builds
    its own fake marker text FROM ops/release-pipeline.py's constant, so
    both sides of the (wrong) comparison always agreed with each other, just
    never with the real file. This test reads tools/health-check.py's
    SOURCE TEXT directly (it cannot be imported — see tools/health-check-
    findings-selftest.py's module docstring) and proves the two literal
    strings are identical, so this specific drift can never come back
    silently."""

    def test_pipeline_marker_matches_health_checks_own_print_exactly(self):
        health_check_src = (HERE / "../tools/health-check.py").resolve().read_text(encoding="utf-8")
        m = re.search(r'_HEALTH_COMPLETION_MARKER = \(\s*"([^"]*)"\s*\n\s*"([^"]*)"\s*\)',
                      health_check_src)
        self.assertIsNotNone(m, "could not find _HEALTH_COMPLETION_MARKER in tools/health-check.py "
                                "— has it been renamed or reshaped?")
        health_check_marker = m.group(1) + m.group(2)
        self.assertEqual(rp.HEALTH_COMPLETE_MARKER, health_check_marker)
        # And all print call sites in tools/health-check.py actually use
        # the constant, not a re-typed literal that could drift from it on
        # its own.
        self.assertEqual(health_check_src.count("print(_HEALTH_COMPLETION_MARKER)"), 2,
                         "tools/health-check.py should print the marker constant, by name, "
                         "from the REFUSED return and the normal end")


class HealthGate(Base):
    """The post-release health gate judges the release, not standing debt: it
    diffs by (key, subject) against a baseline taken before any mutation, and
    fails on a new pair, a risen count, or any hard_error — never on a
    standing finding unchanged since the baseline. Findings A-G below track
    the points of an independent review of PR #1237 by letter."""

    def main_advances_during_release(self, *, fetch):
        target = self.fx.commit({"mcp-server/src/a.js": "1"})
        writer = self.fx.tmp / "unrelated-writer"
        git(self.fx.tmp, "clone", "-q", "--branch", "main", str(self.fx.origin), str(writer))
        git(writer, "config", "user.useConfigOnly", "true")
        live = {"sha": self.fx.base}
        fx = self.fx

        class AdvancingMainRunner(FakeRunner):
            def _health_output(self, name, argv):
                if name == "health":
                    (writer / "README.md").write_text("unrelated main advance\n")
                    git(writer, "add", "README.md")
                    git(writer, "commit", "-q", "-m", "unrelated main advance")
                    git(writer, "push", "-q", "origin", "HEAD:main")
                    if fetch:
                        git(fx.repo, "fetch", "-q", "origin")
                ahead, behind = map(int, git(fx.repo, "rev-list", "--left-right", "--count",
                                             "HEAD...origin/main").split())
                snapshot = {"plists": {}, "launchctl_list": "", "launchctl_disabled": "",
                            "cron": "", "machine_role": "primary",
                            "git": {"ahead": ahead, "behind": behind, "branch": "main",
                                    "remote_matches": git(fx.repo, "rev-parse", "origin/main") ==
                                    git(writer, "rev-parse", "HEAD")}}
                findings = scheduled_job_findings(snapshot)
                if name == "health-baseline":
                    self.health_baseline_findings = findings
                else:
                    self.health_findings = findings
                return super()._health_output(name, argv)

        runner = AdvancingMainRunner(live=live)
        pipe = self.fx.pipeline(runner, live=live)
        rc = pipe.tick(["worker"])
        key = "scheduled_jobs_behind_main" if fetch else "scheduled_jobs_remote_ref_stale"
        self.assertIn("health", runner.names(), self.fx.records()[-1])
        self.assertEqual(runner.health_baseline_findings, [])
        self.assertEqual([row["key"] for row in runner.health_findings], [key])
        self.assertEqual(live["sha"], target)
        self.assertNotEqual(git(writer, "rev-parse", "HEAD"), target)
        self.assertEqual(rc, 0, self.fx.records()[-1])
        record = self.fx.records()[-1]
        self.assertEqual(record["status"], "shipped")
        self.assertIn(key + "[canonical]", record["health_time_rolling_not_attributed"][0])
        self.assertFalse(self.fx.state()["worker"].get("failed_sha"))

    def test_mid_release_main_advance_does_not_fail_on_canonical_behind_main(self):
        self.main_advances_during_release(fetch=True)

    def test_mid_release_main_advance_does_not_fail_on_stale_remote_ref(self):
        self.main_advances_during_release(fetch=False)

    def test_main_lag_hard_errors_are_never_excused(self):
        for key in ("scheduled_jobs_behind_main", "scheduled_jobs_remote_ref_stale"):
            with self.subTest(key=key):
                finding = _finding(key, "unreadable", subject="canonical",
                                   time_rolling=True, hard_error=True)
                bad, excused = rp.health_regression([finding], [finding])
                self.assertEqual(excused, [])
                self.assertIn(key + "[canonical]: hard_error", bad[0])

    def test_other_scheduled_job_regressions_still_fail(self):
        findings = scheduled_job_findings({
            "plists": {}, "launchctl_list": "", "launchctl_disabled": "", "cron": "",
            "machine_role": "primary", "errors": ["launchctl"],
            "git": {"ahead": 1, "behind": 0, "branch": "feature", "remote_matches": True}})
        findings.append(_finding("scheduled_jobs_stale_log", "job stopped writing",
                                 subject="job", time_rolling=True))
        bad, excused = rp.health_regression([], findings)
        self.assertEqual(excused, [])
        self.assertEqual({line.split("[", 1)[0] for line in bad}, {
            "scheduled_jobs_ahead_of_main", "scheduled_jobs_wrong_branch",
            "scheduled_jobs_evidence_unavailable", "scheduled_jobs_stale_log"})

    def test_main_lag_exception_requires_time_rolling_metadata(self):
        for key in ("scheduled_jobs_behind_main", "scheduled_jobs_remote_ref_stale"):
            with self.subTest(key=key):
                bad, excused = rp.health_regression([], [_finding(key, "ordinary failure")])
                self.assertEqual(excused, [])
                self.assertIn(key + "[]: new", bad[0])

    def test_missing_feature_verb_after_promote_fails_even_when_baseline_was_pending(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        pending = _finding("feature_switch_pending_deploy", "check-feature-switches pending deploy")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[pending], health_findings=[pending])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("promote", runner.names())
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "health")
        self.assertIn("feature_switch_pending_deploy", rec["detail"])

    def test_pending_feature_verb_allows_baseline_and_passes_when_deployed(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        pending = _finding("feature_switch_pending_deploy", "check-feature-switches pending deploy")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[pending], health_findings=[])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("promote", runner.names())
        self.assertIn("health", runner.names())

    def test_unreadable_feature_switch_baseline_still_blocks_before_promote(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        failure = _finding("feature_switch_unreadable", "HTTP 401", hard_error=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[failure])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 3)
        self.assertNotIn("promote", runner.names())
        self.assertEqual(self.fx.records()[-1]["reason"], "health_baseline_hard_error")



    def test_standing_finding_present_before_and_after_does_not_fail(self):
        # D: "98 becoming 97 rule gaps" must not fail — and neither may an
        # UNCHANGED 98 -> 98, even though health-check.py's own rc stays 1
        # for as long as any rule gap exists (see test_D below for the
        # improving case; this is the unchanged case).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_98])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("health-baseline", runner.names())
        self.assertIn("health", runner.names())

    def test_new_finding_since_baseline_fails_the_release(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_finding = _finding("export_receipt", "LATEST FAILED vendors.xlsx (latest status failed)",
                               subject="vendors.xlsx")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_98, new_finding])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "health")
        self.assertIn("export_receipt", rec.get("detail", ""))
        # promotion already happened; the gate judges what promotion did, it
        # does not (and cannot) un-promote.
        self.assertIn("promote", runner.names())
        # G: the fix session is pointed at a small new-findings-only file,
        # not the full health output.
        findings_log = Path(rec["log"])
        self.assertTrue(findings_log.name.endswith("health-new-findings.log"))
        text = findings_log.read_text()
        self.assertIn("export_receipt", text)
        self.assertNotIn("rule_enforcement", text)  # the unchanged standing finding is NOT dumped in

    def test_an_unavailable_live_read_is_never_a_pass_even_with_no_findings(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_marker=False, health_findings=[])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertEqual(self.fx.records()[-1]["step"], "health")

    def test_A_nonzero_exit_with_no_finding_at_all_always_fails(self):
        # Point A's literal example: health exits nonzero but recorded no
        # finding to explain it (the historical "export receipts UNREADABLE"
        # bug, now fixed at the source in tools/health-check.py, but this is
        # the pipeline-side backstop against a future regression of that).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, fail_at="health",
                            health_baseline_findings=[], health_findings=[])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "health")
        self.assertIn("no finding at all", rec.get("detail", ""))

    def test_B_and_E_an_unavailable_baseline_is_a_pre_promote_block_not_a_failure(self):
        # B: the baseline must be taken before any --apply step; E: an
        # incomplete baseline is a BLOCKED hold, so the SHA is never consumed
        # (no failed_sha, no diagnosis dispatch, retried next tick) — not a
        # StepFailed after promotion already ran. Point 1 of the second round
        # of review: the baseline is read IN the release worktree (a checkout
        # only, no --apply), so "worktree" now runs before "health-baseline"
        # — that checkout is still trivially re-creatable and cleaned up by
        # remove_worktrees() on this Blocked path, so it does not turn an
        # incomplete baseline into a real failure.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_marker=False,
                            health_findings=[RULE_GAPS_98])
        # A clean Blocked hold with no capability to file returns 0 — "nothing
        # failed", per Blocked's own docstring; that is what "the SHA isn't
        # consumed" means in practice, not a nonzero pipeline exit.
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "blocked")
        self.assertEqual(self.fx.records()[-1]["reason"], "health_baseline_unavailable")
        self.assertIsNone(self.fx.state().get("worker", {}).get("failed_sha"))
        # nothing after the baseline read ever ran: not staging-prepare, not
        # migrate-apply, not upload, not promote.
        for later in ("staging-prepare", "staging-app-writer", "migrate-plan",
                      "migrate-apply", "upload", "staging", "promote", "health"):
            self.assertNotIn(later, runner.names())
        self.assertEqual(runner.names(),
                         ["wrangler-auth", "worktree", "venv-link", "npm-ci", "health-baseline"])

    def test_B_baseline_runs_in_the_worktree_before_every_apply_step(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        names = runner.names()
        # "worktree" (a checkout, no --apply) still comes before the
        # baseline — see point 1 of the second round of review — but every
        # REAL --apply step must still come after the baseline read.
        self.assertLess(names.index("worktree"), names.index("health-baseline"))
        baseline_i = names.index("health-baseline")
        for apply_step in ("staging-prepare", "staging-app-writer", "migrate-plan"):
            if apply_step in names:
                self.assertLess(baseline_i, names.index(apply_step),
                                f"health-baseline must run before {apply_step}")

    def test_point1_baseline_and_post_read_run_in_the_same_folder(self):
        # Point 1 of the second round of review: the baseline and the
        # post-promote read must use the SAME cwd (the release worktree),
        # not two different checkouts — otherwise migrate-apply rewriting
        # db/schema.sql in the worktree spuriously "changes" repo_loose_work
        # against a baseline read from the pipeline's own checkout.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("health-baseline", runner.cwds)
        self.assertIn("health", runner.cwds)
        self.assertEqual(runner.cwds["health-baseline"], runner.cwds["health"])
        # and neither one is the pipeline's own repo checkout — it is a
        # dedicated release worktree.
        self.assertNotEqual(runner.cwds["health-baseline"], str(self.fx.repo))

    def test_point1_the_baselines_worktree_has_a_linked_venv(self):
        # Point 1 (BLOCKER) of the third round of review, reproduced with a
        # real run: venv-link must run BEFORE the health baseline, or
        # `./run.sh health` in the worktree falls back to the bare Homebrew
        # python3 (no psycopg, no openpyxl) and every worker release blocks
        # on a source_unreadable hard_error. "worktree" then "venv-link"
        # then "health-baseline", in that order, every time.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        names = runner.names()
        self.assertLess(names.index("worktree"), names.index("venv-link"))
        self.assertLess(names.index("venv-link"), names.index("health-baseline"))
        # FakeRunner does not touch the filesystem, so the real proof of a
        # working link is the venv-link argv itself: `ln -s <repo>/.venv
        # <worktree>/.venv`, with the LINK TARGET sitting directly inside
        # the exact folder the baseline read then runs in.
        venv_link_argv = next(argv for name, argv in runner.calls if name == "venv-link")
        self.assertEqual(venv_link_argv[0], "ln")
        self.assertTrue(venv_link_argv[-1].endswith("/.venv"))
        self.assertEqual(str(Path(venv_link_argv[-1]).parent), runner.cwds["health-baseline"])

    def test_B_baseline_runs_before_migrate_apply_when_a_migration_is_pending(self):
        # The probe's original concern named migrate-apply specifically ("the
        # baseline is taken after migrate-apply and the staging --apply
        # steps, so a release's own migration damage is forgiven"). Checked
        # with --only migrate-apply's precondition (pending > 0) actually
        # true, isolated from the unrelated schema-followup PR path that a
        # full green run with pending migrations also exercises.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, fail_at="migrate-apply", pending=1)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        names = runner.names()
        self.assertIn("health-baseline", names)
        self.assertIn("migrate-apply", names)
        self.assertLess(names.index("health-baseline"), names.index("migrate-apply"))

    def test_C_two_identical_failures_in_one_run_are_not_hidden_as_one_unchanged_line(self):
        # The exact-string scheme's blind spot: baseline has ONE occurrence
        # of a job's terminal-failure finding, live has TWO (same key and
        # subject, so a naive text diff sees "no new line" — this scheme
        # instead compares the accumulated COUNT and must catch the rise).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        one = _finding("job_terminal_failure", "nightly-export failed", subject="nightly-export", count=1)
        two = _finding("job_terminal_failure", "nightly-export failed", subject="nightly-export", count=2)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[one], health_findings=[two])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("count 1 -> 2", self.fx.records()[-1].get("detail", ""))

    def test_D_a_falling_count_is_an_improvement_and_passes(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_97])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_D_time_rolling_findings_are_reported_but_never_diffed(self):
        # A job's MISSING DUE date rolls forward daily; the SUBJECT text
        # differs from yesterday's baseline by construction, which would read
        # as "new" under a naive diff. time_rolling=True excludes it.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("job_missing_due", "cal MISSING DUE execution for 2026-09-23",
                             subject="cal", time_rolling=True)
        today = _finding("job_missing_due", "cal MISSING DUE execution for 2026-09-24",
                         subject="cal", time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_D_any_hard_error_fails_even_when_identical_to_the_baseline(self):
        # Point A/D together: hard_error is unconditional and is never
        # excused by a matching baseline entry — a LIVE structural read
        # failure that is not the SAME hard_error the baseline had (so it
        # does not trip point 2's earlier pre-promote block below) must
        # still fail on its own terms, not be waved through as "unchanged."
        # Modeled here as a baseline entry that reports the same (key,
        # subject) and count WITHOUT hard_error, and a live read that adds
        # hard_error=True to it — count-unchanged would normally read as a
        # pass, but hard_error overrides that.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        baseline_row = _finding("export_unreadable", "export receipts flaky", hard_error=False)
        live_row = _finding("export_unreadable", "export receipts UNREADABLE", hard_error=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[baseline_row],
                            health_findings=[live_row])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("hard_error", self.fx.records()[-1].get("detail", ""))

    def test_point2_a_hard_error_already_in_the_baseline_blocks_before_promote(self):
        # Point 2 of the second round of review: a baseline that ALREADY
        # shows a hard_error finding must block before any --apply step,
        # not ship and then predictably fail its own post-promote
        # comparison. This is a Blocked hold with a capability (files a
        # loop), so it returns 3, not a StepFailed.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        broken = _finding("export_unreadable", "export receipts UNREADABLE", hard_error=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[broken], health_findings=[broken])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 3)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "blocked")
        self.assertEqual(rec["reason"], "health_baseline_hard_error")
        self.assertTrue(rec.get("loop_filed"))
        for later in ("staging-prepare", "migrate-plan", "upload", "staging", "promote", "health"):
            self.assertNotIn(later, runner.names())

    def test_point2_credential_expiring_soon_alone_is_not_hard_error(self):
        # The other half of point 2: expiring_soon (and registry-audit data
        # errors) are counted findings, not hard_error — health-check.py's
        # own logic is the source of truth for that distinction and is
        # proven directly in tools/health-check-findings-selftest.py; this
        # only proves the pipeline's OWN gate does not treat a non-hard_error
        # baseline finding as a pre-promote block.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        expiring = _finding("credential_health", "1 of 4 credential(s) need attention "
                            "(failed=0 expiring_soon=1 unverifiable=0 unconfigured=0 ok=3)",
                            count=1, hard_error=False)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[expiring],
                            health_findings=[expiring])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_F_marker_present_but_not_the_last_line_is_incomplete(self):
        # A crash on the way out after the happy-path print (e.g. a
        # traceback appended to the same stdout) must not read as complete:
        # the marker printed, then something else printed after it, so it is
        # not the LAST non-empty line any more. FakeRunner's own
        # health_marker flag always puts the marker last when True, so this
        # is built directly as extra text with health_marker=False (the
        # marker text is embedded in the "extra" text itself, followed by
        # a traceback — exactly what a real crash-on-the-way-out looks like).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(
            live=live, health_marker=False,
            health_out_extra=f"{rp.HEALTH_COMPLETE_MARKER}\nTraceback (most recent call last):\n  boom")
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertEqual(self.fx.records()[-1]["step"], "health")

    def test_G_new_findings_file_never_contains_the_full_health_log_path(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_finding = _finding("export_receipt", "LATEST FAILED vendors.xlsx", subject="vendors.xlsx")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[], health_findings=[new_finding])
        self.fx.pipeline(runner, live=live).tick(["worker"])
        rec = self.fx.records()[-1]
        # "log" is what queue_turn's dispatched turn tells the fix session to
        # read; it must be the small new-findings file, not the raw
        # `./run.sh health` stdout capture (…-health.log).
        self.assertTrue(rec["log"].endswith("health-new-findings.log"))
        self.assertFalse(rec["log"].endswith("-health.log"))

    def test_point4_a_rolling_finding_still_fails_when_its_count_rises(self):
        # Point 4 of the second round of review, still true today only for a
        # time_rolling key that is NOT on HEALTH_REGRESSION_FIRST_APPEARANCE_
        # ALLOWLIST: doctrine_gate stays fully count-sensitive (round 12
        # left it untouched), so a rising count within an already-present
        # doctrine_gate finding still fails. (doctrine_stale moved to the
        # allowlist-only "subject set, never count" rule in round 12 — see
        # test_round12_allowlisted_existing_subject_count_rise_is_excused
        # below for its new behavior, and test_round7_doctrine_gate_is_
        # still_not_on_the_allowlist for confirmation it's excluded.)
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("doctrine_gate", "3 failures in 24h", count=3, time_rolling=True)
        today = _finding("doctrine_gate", "5 failures in 24h", count=5, time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("time_rolling count 3 -> 5", self.fx.records()[-1].get("detail", ""))

    def test_point4_a_rolling_finding_falling_still_passes(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("doctrine_gate", "5 failures in 24h", count=5, time_rolling=True)
        today = _finding("doctrine_gate", "3 failures in 24h", count=3, time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round12_allowlisted_existing_subject_count_rise_is_excused(self):
        # Round 12, point 2: the reviewer refuted the prior rule for an
        # ALLOWLISTED key. The gate runs once, after promote, and never
        # retries the SHA — an already-known-broken hourly job whose
        # job_missing_due count keeps climbing must not fail every release
        # forever. A subject already present in the baseline is now excused
        # no matter how far its count rises.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 09:00",
                             subject="hourly-sync", time_rolling=True, count=1)
        today = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 14:00",
                         subject="hourly-sync", time_rolling=True, count=6)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round13_allowlisted_new_subject_passes(self):
        # Round 13 reverses round 12's other half: making a brand-new
        # subject fail brought back exactly the clock-driven false failure
        # the allowlist existed to prevent — a job's own due window can
        # pass during the tens of minutes a release takes, turning it into
        # a "new" job_missing_due subject with no baseline entry, through
        # no fault of the release. A new subject on an allowlisted key is
        # now excused, same as an existing one, regardless of count.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        existing = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 09:00",
                            subject="hourly-sync", time_rolling=True, count=1)
        new_job = _finding("job_missing_due", "weekly-report MISSING DUE execution for 09-24",
                           subject="weekly-report", time_rolling=True, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[existing],
                            health_findings=[existing, new_job])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round13_a_new_non_time_rolling_key_still_fails(self):
        # A new (key, subject) pair that is NOT time_rolling at all (so not
        # eligible for the allowlist regardless of the key name) still
        # fails as an ordinary new regression — round 13 only takes
        # time_rolling, allowlisted keys out of the comparison.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_credential_gap = _finding("credential_health", "new-integration token expiring",
                                      subject="new-integration")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[new_credential_gap])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("new-integration", self.fx.records()[-1].get("detail", ""))

    def test_round13_receipt_lists_the_unattributed_time_rolling_findings(self):
        # "Keep them in the findings and the receipt, visibly listed as
        # time-rolling, not attributed, so nothing is hidden": a release
        # that PASSES (no non-allowlisted regression) still carries every
        # allowlisted time_rolling finding forward into the shipped-release
        # receipt (releases.jsonl), explicitly labeled, even though none of
        # them influenced the pass/fail outcome.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        missing_due = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 09:00",
                               subject="hourly-sync", time_rolling=True, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[], health_findings=[missing_due])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        record = self.fx.records()[-1]
        self.assertEqual(record.get("status"), "shipped")
        unattributed = record.get("health_time_rolling_not_attributed", [])
        self.assertEqual(len(unattributed), 1)
        self.assertIn("job_missing_due", unattributed[0])
        self.assertIn("hourly-sync", unattributed[0])
        self.assertIn("time-rolling, not attributed", unattributed[0])

    def test_round14_stale_then_failed_is_not_lost(self):
        # Round 14 (pre-existing since round 3): the baseline lookup keyed
        # only by (key, subject) let a time_rolling baseline row match an
        # ordinary live row for the same (key, subject) as its "prior".
        # export_receipt[vendors.xlsx] STALE (time_rolling=True) at
        # baseline, then LATEST FAILED (time_rolling=False, NOT hard_error)
        # after the release: the live row matched the STALE baseline row,
        # saw count 1 against 1 (unchanged), and vanished — not a
        # regression (count didn't rise) and not excused (not time_rolling)
        # — bad=[] and excused=[]. Keying the baseline lookup by (key,
        # subject, time_rolling) too means the FAILED live row has no
        # baseline entry of its OWN rolling-ness to match, so it is
        # correctly treated as a genuinely new (ordinary) finding and fails
        # the gate.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        stale = _finding("export_receipt", "STALE vendors.xlsx (last ok 2026-09-20)",
                         subject="vendors.xlsx", time_rolling=True, count=1)
        failed = _finding("export_receipt",
                          "LATEST FAILED vendors.xlsx (latest status error)",
                          subject="vendors.xlsx", time_rolling=False, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[stale], health_findings=[failed])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("vendors.xlsx", self.fx.records()[-1].get("detail", ""))

    def test_round14_failed_then_stale_is_excused_not_lost(self):
        # The reverse of the case above: export_receipt[vendors.xlsx]
        # LATEST FAILED (ordinary) at baseline, then STALE (time_rolling)
        # after the release. The live STALE row is on the allowlist, so
        # round 13's unconditional excuse for allowlisted time_rolling rows
        # applies regardless of what the baseline held — it is excused, not
        # silently dropped and not failed, and it shows up in the receipt.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        failed = _finding("export_receipt",
                          "LATEST FAILED vendors.xlsx (latest status error)",
                          subject="vendors.xlsx", time_rolling=False, count=1)
        stale = _finding("export_receipt", "STALE vendors.xlsx (last ok 2026-09-20)",
                         subject="vendors.xlsx", time_rolling=True, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[failed], health_findings=[stale])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        unattributed = self.fx.records()[-1].get("health_time_rolling_not_attributed", [])
        self.assertEqual(len(unattributed), 1)
        self.assertIn("vendors.xlsx", unattributed[0])
        self.assertIn("time-rolling, not attributed", unattributed[0])

    def test_round12_rule_enforcement_count_rise_still_fails(self):
        # "Keep every other key count-sensitive": rule_enforcement is not
        # time_rolling at all, so a plain count rise (98 -> 99) still fails
        # exactly as it always has — round 12 only touches the three
        # allowlisted time_rolling keys.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_99])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("count 98 -> 99", self.fx.records()[-1].get("detail", ""))

    def test_point2r4_a_new_doctrine_gate_finding_with_no_baseline_still_fails(self):
        # Point 2 of round 4 of an independent review of PR #1237:
        # doctrine_gate (and doctrine_stale) are time_rolling=True in
        # tools/health-check.py, but that must not blanket-excuse a
        # genuinely NEW finding just because it has no baseline entry — 40
        # gate failures appearing where the baseline had 0 is a real
        # regression this release introduced, not a clock crossing, and
        # must fail the gate.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_gate_failures = _finding("doctrine_gate", "40 failures in 24h", count=40,
                                     time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[new_gate_failures])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("doctrine_gate", self.fx.records()[-1].get("detail", ""))

    def test_point2r4_allowlisted_keys_still_excuse_a_first_appearance(self):
        # Round 12 briefly reversed this (making a first appearance fail);
        # round 13 reverses it back and goes further — a key on the
        # allowlist is now out of the comparison entirely, so export_
        # receipt (the STALE branch) and job_missing_due are excused on a
        # first appearance with no baseline entry, exactly as through
        # round 11, AND (unlike round 11) an existing subject's count rise
        # is excused too (see test_round12_allowlisted_existing_subject_
        # count_rise_is_excused and test_round13_allowlisted_new_subject_
        # passes above, which cover that half explicitly).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        stale_export = _finding("export_receipt", "STALE vendors.xlsx (last ok 2026-09-20)",
                                subject="vendors.xlsx", time_rolling=True)
        missing_due = _finding("job_missing_due", "cal MISSING DUE execution for 2026-09-24",
                               subject="cal", time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[stale_export, missing_due])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round7_doctrine_stale_first_appearance_is_excused(self):
        # Round 7 excused doctrine_stale's first appearance as clock noise;
        # round 12 briefly reversed it; round 13 restores the excuse (and
        # extends it to a count rise too) — a section crossing review_after
        # for the very first time, with nothing in the baseline to compare
        # against, is clock noise this release did not cause.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_stale = _finding("doctrine_stale", "2 stale sections", count=2,
                             time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[new_stale])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round7_doctrine_gate_is_still_not_on_the_allowlist(self):
        # Confirms the allowlist addition is scoped to doctrine_stale only:
        # doctrine_gate must remain excluded (see test_point2r4_a_new_
        # doctrine_gate_finding_with_no_baseline_still_fails above, which
        # already pins this behavior — this test pins the constant itself
        # so the two cannot silently drift apart).
        self.assertNotIn("doctrine_gate", rp.HEALTH_REGRESSION_FIRST_APPEARANCE_ALLOWLIST)
        self.assertIn("doctrine_stale", rp.HEALTH_REGRESSION_FIRST_APPEARANCE_ALLOWLIST)

    def test_point1_repo_loose_work_is_excluded_from_the_gate(self):
        # Point 1 of the second round of review: even reading baseline and
        # post-promote in the same worktree, migrate-apply legitimately
        # rewrites db/schema.sql as part of a normal migration release, so
        # repo_loose_work must never fail the gate on its own.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        loose_before = _finding("repo_loose_work", "0 actionable path(s)", count=0)
        loose_after = _finding("repo_loose_work", "1 actionable path(s)", count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[loose_before],
                            health_findings=[loose_after])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_point5_an_incomplete_baseline_escalates_after_n_ticks(self):
        # Point 5 of the second round of review: an incomplete baseline must
        # not hold silently forever. The first HEALTH_BASELINE_ESCALATE_AFTER
        # - 1 ticks on the SAME sha are ordinary clean holds (capability=None,
        # tick returns 0, no loop filed); the Nth tick escalates to a filed
        # loop (capability set, tick returns 3).
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        n = rp.Pipeline.HEALTH_BASELINE_ESCALATE_AFTER
        verbs: list = []
        for attempt in range(1, n):
            runner = FakeRunner(live=live, health_baseline_marker=False)
            pipe = self.fx.pipeline(runner, live=live, verbs=verbs)
            pipe.now = lambda: attempt * 10000
            rc = pipe.tick(["worker"])
            self.assertEqual(rc, 0, f"attempt {attempt} should still be a clean, silent hold")
            self.assertEqual(self.fx.records()[-1]["reason"], "health_baseline_unavailable")
            self.assertEqual(verbs, [], f"no loop should be filed before attempt {n}")
        runner = FakeRunner(live=live, health_baseline_marker=False)
        pipe = self.fx.pipeline(runner, live=live, verbs=verbs)
        pipe.now = lambda: n * 10000
        rc = pipe.tick(["worker"])
        self.assertEqual(rc, 3, "the Nth consecutive incomplete baseline must escalate")
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "health_baseline_stalled")
        self.assertTrue(rec.get("loop_filed"))
        self.assertEqual(len(verbs), 1)

    def test_point5_a_completed_baseline_resets_the_incomplete_counter(self):
        # A baseline that eventually DOES complete clears the per-sha
        # counter, so a later transient blip does not inherit an escalation
        # that is already most of the way to firing.
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        n = rp.Pipeline.HEALTH_BASELINE_ESCALATE_AFTER
        for attempt in range(n - 1):
            runner = FakeRunner(live=live, health_baseline_marker=False)
            pipe = self.fx.pipeline(runner, live=live)
            pipe.now = lambda: attempt * 10000
            pipe.tick(["worker"])
        good_runner = FakeRunner(live=live)
        pipe = self.fx.pipeline(good_runner, live=live)
        pipe.now = lambda: n * 10000
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"].get("health_baseline_incomplete", {}).get(sha),
                         None)


class KillSwitch(Base):
    def setUp(self):
        super().setUp()
        self.fx.commit({"mcp-server/src/a.js": "1"})

    def test_config_flag(self):
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, cfg=self.fx.config(enabled=False)).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records(), [])

    def test_local_override_file(self):
        (self.fx.tmp / "release-pipeline.off").write_text("")
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])

    def test_lane_flag(self):
        cfg = self.fx.config()
        cfg["worker"]["enabled"] = False
        runner = FakeRunner()
        self.fx.pipeline(runner, cfg=cfg).tick(["worker"])
        self.assertEqual(runner.calls, [])


class Lock(Base):
    def test_concurrent_tick_is_a_noop(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        root = self.fx.repo / "out/release-pipeline"
        with rp.single_run_lock(root) as held:
            self.assertTrue(held)
            self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        with rp.single_run_lock(root) as held:  # released afterwards
            self.assertTrue(held)


class GitHubBudgetEnvironment(unittest.TestCase):
    def test_configured_cache_path_reaches_github_reader_without_credentials(self):
        budget_path = "/tmp/github-read-budget.json"
        env = rp.child_env({
            "HOME": "/tmp/home",
            "CARR_GITHUB_READ_BUDGET": budget_path,
            "DATABASE_URL": "fixture-dsn",
            "CARR_DB_PASSWORD": "fixture-password",
            "GH_TOKEN": "fixture-token",
        })

        self.assertEqual(rp.GitHub("o/r", env).reader.budget.path, Path(budget_path))
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("CARR_DB_PASSWORD", env)
        self.assertNotIn("GH_TOKEN", env)


class CanaryAndCI(Base):
    """B2: the nearest canary verdict decides; every release-path PR has green CI."""

    def test_a_docs_only_commit_cannot_hide_a_red_canary(self):
        code = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"docs/n.md": "x"})            # canary skipped this one: no run at all
        runner = FakeRunner()
        gh = FakeGitHub(canary={code: ("completed", "failure")})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_red")

    def test_an_uncanaried_code_head_holds(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, github=FakeGitHub(canary={})).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_pending")

    def test_an_in_progress_canary_holds(self):
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={a: ("in_progress", None)})
        self.fx.pipeline(FakeRunner(), github=gh).tick(["worker"])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_pending")

    def test_a_cancelled_run_on_an_ignored_commit_may_be_walked_past(self):
        code = self.fx.commit({"mcp-server/src/a.js": "1"})
        docs = self.fx.commit({"docs/n.md": "x"})
        live = {"sha": self.fx.base}
        gh = FakeGitHub(canary={code: ("completed", "success"), docs: ("completed", "cancelled")})
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), github=gh, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_a_green_canary_below_a_docs_commit_passes(self):
        code = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"docs/n.md": "x"})
        live = {"sha": self.fx.base}
        gh = FakeGitHub(canary={code: ("completed", "success")})
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), github=gh, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_red_ci_on_an_earlier_pr_in_the_batch_holds(self):
        first = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"mcp-server/src/b.js": "2"})
        runner = FakeRunner()
        gh = FakeGitHub(red_ci_prs={FakeGitHub().pr_number(first)})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "ci_not_green")
        self.assertIn(f"PR #{FakeGitHub().pr_number(first)}", rec["detail"])

    def test_a_newer_cancelled_ci_run_does_not_hide_an_older_green_one(self):
        first = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        gh = FakeGitHub(ci_runs={FakeGitHub().pr_number(first): [(38153038213, "success"),
                                                                 (38154018553, "cancelled")]})
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), github=gh, live=live).tick(["worker"]), 0,
                         self.fx.records())
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_a_newer_failed_ci_run_still_holds_over_an_older_green_one(self):
        first = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        gh = FakeGitHub(ci_runs={FakeGitHub().pr_number(first): [(38153038213, "success"),
                                                                 (38154018553, "failure")]})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "ci_not_green")
        self.assertIn("38154018553", rec["detail"])

    def test_only_a_cancelled_ci_run_holds(self):
        first = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        gh = FakeGitHub(ci_runs={FakeGitHub().pr_number(first): [(38154018553, "cancelled")]})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "ci_not_green")
        self.assertIn("has no successful", rec["detail"])

    def test_unicode_named_release_path_cannot_hide_failed_ci(self):
        first = self.fx.commit({"mcp-server/src/unicodé.js": "1"})
        self.fx.commit({"mcp-server/src/ordinary.js": "2"})
        runner = FakeRunner()
        gh = FakeGitHub(red_ci_prs={FakeGitHub().pr_number(first)})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0,
                         self.fx.records())
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "ci_not_green")

    def test_trailing_space_release_path_cannot_hide_failed_ci(self):
        first = self.fx.commit({"mcp-server/src/runtime.md ": "1"})
        self.fx.commit({"mcp-server/src/ordinary.js": "2"})
        runner = FakeRunner()
        gh = FakeGitHub(red_ci_prs={FakeGitHub().pr_number(first)})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "ci_not_green")

    def test_trailing_newline_release_path_cannot_hide_failed_ci(self):
        first = self.fx.commit({"mcp-server/src/runtime.md\n": "1"})
        self.fx.commit({"mcp-server/src/ordinary.js": "2"})
        runner = FakeRunner()
        gh = FakeGitHub(red_ci_prs={FakeGitHub().pr_number(first)})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "ci_not_green")


class ReleaseTarget(Base):
    """The Worker ships up to the NEWEST green-canary commit, not HEAD.

    main-canary runs ~20 minutes while merges can land every 10-20 minutes.
    Running canaries finish, but HEAD may still be pending or in progress and
    older pending runs may be replaced; demanding HEAD itself be green can
    starve the lane."""

    GREEN, RED = ("completed", "success"), ("completed", "failure")

    def setUp(self):
        super().setUp()
        self.live = {"sha": self.fx.base}

    def ship(self, gh):
        runner = FakeRunner(live=self.live)
        rc = self.fx.pipeline(runner, github=gh, live=self.live).tick(["worker"])
        return rc, runner

    def released(self, runner):
        return [a[a.index("--release-sha") + 1] for n, a in runner.calls if n == "upload"]

    def assert_shipped(self, rc, runner, target):
        self.assertEqual(rc, 0, self.fx.records()[-1])
        self.assertEqual(self.released(runner), [target])
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], target)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["sha"]), ("shipped", target))
        return rec

    def test_repaired_credential_retries_failed_canary_below_unverified_head(self):
        target = self.fx.commit({"mcp-server/src/z.js": "1"})
        head = self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={target: self.GREEN, head: ("in_progress", None)})
        (self.fx.cred / "tokens.env").write_text("")
        self.assertEqual(self.fx.pipeline(FakeRunner(), github=gh).tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], target)
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        (self.fx.cred / "tokens.env").write_text(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
        self.assert_shipped(*self.ship(gh), target)

    def test_head_in_progress_ships_the_older_green_commit(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        pr_a = FakeGitHub().pr_number(a)
        # HEAD's CI is red, but the release target is the older green source.
        # the batch now ends at z and every downstream check names z, not HEAD.
        gh = FakeGitHub(canary={z: self.GREEN, a: ("in_progress", None)}, red_ci_prs={pr_a})
        rc, runner = self.ship(gh)
        rec = self.assert_shipped(rc, runner, z)
        self.assertEqual(rec["pr"], FakeGitHub().pr_number(z))
        self.assertEqual(rec["pr_head_sha"], pr_head(FakeGitHub().pr_number(z)))
        self.assertNotIn(pr_a, rec["prs"])
        self.assertNotIn(a, [x for _, argv in runner.calls for x in argv])

    def test_head_cancelled_ships_the_older_green_commit(self):
        for conclusion in ("cancelled", "skipped"):
            with self.subTest(conclusion):
                self.tearDown()
                self.setUp()
                z = self.fx.commit({"mcp-server/src/z.js": "1"})
                a = self.fx.commit({"mcp-server/src/a.js": "1"})
                gh = FakeGitHub(canary={z: self.GREEN, a: ("completed", conclusion)})
                self.assert_shipped(*self.ship(gh), z)

    def test_uncanaried_commits_between_are_walked_past(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        b = self.fx.commit({"mcp-server/src/b.js": "1"})
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={z: self.GREEN, b: ("completed", "cancelled"), a: ("queued", None)})
        self.assert_shipped(*self.ship(gh), z)

    def test_a_red_commit_with_no_green_below_blocks(self):
        r = self.fx.commit({"mcp-server/src/r.js": "1"})
        h = self.fx.commit({"mcp-server/src/h.js": "1"})
        gh = FakeGitHub(canary={self.fx.base: self.GREEN, r: self.RED, h: ("in_progress", None)})
        rc, runner = self.ship(gh)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["reason"]), ("blocked", "canary_red"))
        self.assertIn(r[:12], rec["detail"])
        self.assertNotIn("last_released_sha", self.fx.state().get("worker", {}))

    def test_a_red_commit_ships_only_the_green_below_it_and_nothing_past_it(self):
        g = self.fx.commit({"mcp-server/src/g.js": "1"})
        r = self.fx.commit({"mcp-server/src/r.js": "1"})
        h = self.fx.commit({"mcp-server/src/h.js": "1"})
        gh = FakeGitHub(canary={g: self.GREEN, r: self.RED, h: ("in_progress", None)})
        self.assert_shipped(*self.ship(gh), g)
        # next tick: HEAD still unverified, the red commit is now the whole story
        rc, runner = self.ship(gh)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_red")
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], g)
        # a newer red HEAD still ships nothing past the red commits
        gh.canary[h] = self.RED
        rc, runner = self.ship(gh)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_red")

    def test_a_green_fix_forward_above_a_red_commit_ships(self):
        r = self.fx.commit({"mcp-server/src/r.js": "1"})
        f = self.fx.commit({"mcp-server/src/r.js": "fixed"})
        gh = FakeGitHub(canary={r: self.RED, f: self.GREEN})
        self.assert_shipped(*self.ship(gh), f)

    def test_only_ignored_commits_since_the_release_ship_head_as_before(self):
        # dealroom/*.md is a release path AND canary-ignored: no canary runs,
        # and the verdict is the last released commit's own green run.
        self.fx.commit({"dealroom/a.md": "1"})
        head = self.fx.commit({"dealroom/b.md": "2"})
        gh = FakeGitHub(canary={self.fx.base: self.GREEN})
        self.assert_shipped(*self.ship(gh), head)

    def test_ignored_commits_directly_above_the_green_one_ride_along(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        d = self.fx.commit({"docs/n.md": "x"})
        self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={z: self.GREEN, d: ("completed", "cancelled")})
        self.assert_shipped(*self.ship(gh), d)

    def test_no_green_newer_than_the_last_release_holds(self):
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        b = self.fx.commit({"mcp-server/src/b.js": "1"})
        gh = FakeGitHub(canary={self.fx.base: self.GREEN, a: ("completed", "cancelled"),
                                b: ("in_progress", None)})
        rc, runner = self.ship(gh)
        self.assertEqual((rc, runner.calls), (0, []))
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["reason"], rec["sha"]), ("blocked", "canary_pending", b))
        self.assertNotIn("last_released_sha", self.fx.state().get("worker", {}))

    def test_a_failed_target_is_not_retried_while_head_is_unverified(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={z: self.GREEN, a: ("in_progress", None)})
        verbs: list = []
        self.assertEqual(self.fx.pipeline(FakeRunner(fail_at="upload"), github=gh, verbs=verbs)
                         .tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], z)
        b = self.fx.commit({"mcp-server/src/b.js": "1"})     # a fix-forward, not yet canaried
        gh.canary[b] = ("in_progress", None)
        again = FakeRunner()
        self.assertEqual(self.fx.pipeline(again, github=gh, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(again.calls, [])
        self.assertEqual(len(verbs), 1)
        gh.canary[b] = self.GREEN                             # the fix-forward goes green
        self.assert_shipped(*self.ship(gh), b)


class AppLane(Base):
    """The app lane requires green checks and verifies the live release."""

    def cfg(self):
        cfg = self.fx.config()
        cfg["app"]["enabled"] = True
        return cfg

    def test_empty_app_checks_hold(self):
        self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, cfg=self.cfg(), github=FakeGitHub(checks=[]))
        pipe.http = lambda _u: {"source_commit": self.fx.base, "environment": "production"}
        self.assertEqual(pipe.tick(["app"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "checks_missing")


    def test_green_merged_app_change_releases_without_review(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, cfg=self.cfg())
        live = {"source_commit": self.fx.base, "environment": "production"}
        pipe.http = lambda _u: live
        orig = runner.run

        def run(argv, **kw):
            res = orig(argv, **kw)
            if argv[:2] == ["node", "scripts/release-production.mjs"]:
                live["source_commit"] = sha
            return res
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(pipe.tick(["app"]), 0, self.fx.records()[-1])
        self.assertEqual(runner.names()[:8], ["wrangler-auth", "app-worktree", "app-npm-ci", "app-build",
                                              "smoke-baseline", "app-main-ref-refresh", "app-release",
                                              "smoke-post"])
        build_index = runner.names().index("app-build")
        self.assertEqual(runner.calls[build_index][1], ["npm", "run", "build"])
        self.assertEqual(runner.envs["app-build"]["DOCTORCRE_SOURCE_COMMIT"], sha)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs["app-build"])
        self.assertEqual(runner.cwds["app-build"], runner.cwds["app-release"])
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")
        for row in self.fx.records():
            self.assertEqual(row["live_identity"]["url"], "https://app.doctorcre.com")
            self.assertEqual(row["live_identity"]["source_commit"], sha)
        # The slice marker follows Worker releases only: the app lane records
        # no ops.release row for membership to attach to.
        self.assertEqual(self.fx.slice_marks, [])
        self.assertNotIn("slice_marker", self.fx.records()[-1])

    def test_immutable_main_fetch_refreshes_stale_tracking_ref_before_exact_main_guard(self):
        other = self.fx.tmp / "app-main-writer"
        git(self.fx.tmp, "clone", "-q", str(self.fx.origin), str(other))
        git(other, "config", "user.email", "t@example.invalid")
        git(other, "config", "user.name", "t")
        (other / "src").mkdir()
        (other / "src/app.js").write_text("reviewed release candidate\n")
        git(other, "add", "src/app.js")
        git(other, "commit", "-q", "-m", "reviewed app release candidate")
        git(other, "push", "-q", "origin", "HEAD:main")
        candidate = git(other, "rev-parse", "HEAD")
        stale = git(self.fx.repo, "rev-parse", "refs/remotes/origin/main")
        self.assertNotEqual(candidate, stale)
        fetch_head = self.fx.repo / ".git/FETCH_HEAD"
        fetch_head.write_text("another session's fetch evidence\n")

        live = {"source_commit": self.fx.base, "environment": "production"}
        runner = ExactMainAppGuardRunner(live=live)
        pipe = self.fx.pipeline(runner, cfg=self.cfg(), live=live)
        pipe.http = lambda _url: live

        self.assertEqual(pipe.tick(["app"]), 0, self.fx.records()[-1])
        self.assertEqual(runner.guard_observations, [(candidate, candidate)])
        self.assertEqual(git(self.fx.repo, "rev-parse", "HEAD"), self.fx.base)
        self.assertEqual(git(self.fx.repo, "rev-parse", "refs/remotes/origin/main"), candidate)
        self.assertEqual(fetch_head.read_text(), "another session's fetch evidence\n")
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_app_release_refuses_main_advance_during_build(self):
        other = self.fx.tmp / "app-main-writer"
        git(self.fx.tmp, "clone", "-q", str(self.fx.origin), str(other))
        git(other, "config", "user.email", "t@example.invalid")
        git(other, "config", "user.name", "t")
        (other / "src").mkdir()
        (other / "src/app.js").write_text("reviewed release candidate\n")
        git(other, "add", "src/app.js")
        git(other, "commit", "-q", "-m", "reviewed app release candidate")
        git(other, "push", "-q", "origin", "HEAD:main")
        candidate = git(other, "rev-parse", "HEAD")

        def advance_main():
            (other / "src/next.js").write_text("unexpected later main commit\n")
            git(other, "add", "src/next.js")
            git(other, "commit", "-q", "-m", "advance main during app build")
            git(other, "push", "-q", "origin", "HEAD:main")

        live = {"source_commit": self.fx.base, "environment": "production"}
        runner = ExactMainAppGuardRunner(live=live, on_build=advance_main)
        pipe = self.fx.pipeline(runner, cfg=self.cfg(), live=live)
        pipe.http = lambda _url: live

        self.assertEqual(pipe.tick(["app"]), 1)
        self.assertNotIn("app-release", runner.names())
        self.assertEqual(self.fx.records()[-1]["sha"], candidate)
        self.assertEqual(self.fx.records()[-1]["status"], "failed")
        self.assertNotEqual(git(self.fx.repo, "ls-remote", "origin", "refs/heads/main").split()[0], candidate)

    def test_failed_credential_free_build_never_reaches_publication(self):
        self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner(fail_at="app-build")
        pipe = self.fx.pipeline(runner, cfg=self.cfg())
        pipe.http = lambda _u: {"source_commit": self.fx.base, "environment": "production"}
        self.assertEqual(pipe.tick(["app"]), 1)
        self.assertIn("app-build", runner.names())
        self.assertNotIn("app-release", runner.names())
        self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs["app-build"])
        self.assertEqual(self.fx.records()[-1]["step"], "app-build")

    def test_live_readback_retries_stale_response_and_records_each_payload(self):
        pipe = self.fx.pipeline(FakeRunner(), cfg=self.cfg())
        sha = "a" * 40
        seen = []
        replies = iter([
            {"source_commit": "b" * 40, "environment": "production"},
            {"source_commit": sha, "environment": "production"},
        ])
        pipe.http = lambda url: (seen.append(url) or next(replies))
        pipe.sleep = lambda seconds: seen.append(seconds)
        pipe.verify_app_live(self.cfg()["app"], sha)
        self.assertEqual(seen, [self.cfg()["app"]["live_release_url"], 5,
                                self.cfg()["app"]["live_release_url"]])
        rows = [json.loads(line) for line in (pipe.run_dir / "app-verify-live.jsonl").read_text().splitlines()]
        self.assertEqual([row["response"]["source_commit"] for row in rows], ["b" * 40, sha])

    def test_live_readback_failure_preserves_last_response_and_log(self):
        pipe = self.fx.pipeline(FakeRunner(), cfg=self.cfg())
        pipe.http = lambda _url: {"source_commit": "c" * 40, "environment": "staging"}
        pipe.sleep = lambda _seconds: None
        with self.assertRaises(rp.StepFailed) as caught:
            pipe.verify_app_live(self.cfg()["app"], "a" * 40, attempts=2)
        self.assertEqual(caught.exception.step, "app-verify-live")
        self.assertIn("source_commit=" + "c" * 40, caught.exception.detail)
        self.assertEqual(len((pipe.run_dir / "app-verify-live.jsonl").read_text().splitlines()), 2)

    def test_live_read_error_preserves_prior_observation_in_failure(self):
        pipe = self.fx.pipeline(FakeRunner(), cfg=self.cfg())
        reads = iter([{"source_commit": "d" * 40, "environment": "production"}, TimeoutError("edge timeout")])
        def read(_url):
            item = next(reads)
            if isinstance(item, Exception):
                raise item
            return item
        pipe.http = read
        pipe.sleep = lambda _seconds: None
        with self.assertRaises(rp.StepFailed) as caught:
            pipe.verify_app_live(self.cfg()["app"], "a" * 40, attempts=2)
        self.assertIn("source_commit=" + "d" * 40, caught.exception.detail)
        self.assertIn("last_read_error=TimeoutError", caught.exception.detail)
        rows = [json.loads(line) for line in (pipe.run_dir / "app-verify-live.jsonl").read_text().splitlines()]
        self.assertEqual(rows[1]["error"], "TimeoutError: edge timeout")


class Robustness(Base):
    def test_unexpected_error_before_any_step_is_recorded_not_burned(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        rc = self.fx.pipeline(FakeRunner(), github=FakeGitHub(raise_on="pr_for_commit"), verbs=verbs).tick(["worker"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.fx.records()[-1]["status"], "error")
        self.assertIsNone(self.fx.state().get("worker", {}).get("failed_sha"))
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])

    def test_unexpected_error_after_a_mutation_is_a_failure(self):
        # Crashing at staging-prepare, which only runs AFTER the health
        # baseline has already succeeded and self.mutated is already True
        # (point 1 of the third round of review moved venv-link AND npm-ci
        # to run BEFORE the baseline, and neither of those sets
        # self.mutated on its own — see the next test). staging-prepare is
        # the first step downstream of a successful baseline, so a crash
        # here is unambiguously "after a mutation."
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        orig = runner.run

        def run(argv, **kw):
            if any("staging-project-replacement.py" in a for a in argv) and "prepare" in argv:
                raise OSError("disk full")
            return orig(argv, **kw)
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual(self.fx.records()[-1]["step"], "unexpected")

    def test_unexpected_error_during_npm_ci_is_not_yet_a_mutation(self):
        # Point 1 of the third round of review: venv-link and npm-ci now
        # run BEFORE the health baseline, and neither sets self.mutated —
        # "neither touches production state, so the SHA isn't consumed"
        # (the coordinator's own framing). An npm-ci crash is therefore
        # still a clean, pre-mutation error: retried next tick, no
        # failed_sha, same as a crash before the worktree even existed.
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        orig = runner.run

        def run(argv, **kw):
            if argv[:2] == ["npm", "ci"]:
                raise OSError("disk full")
            return orig(argv, **kw)
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertNotIn("failed_sha", self.fx.state().get("worker", {}))
        self.assertEqual(self.fx.records()[-1]["status"], "error")

    def test_failure_after_migrations_says_db_is_ahead(self):
        self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        verbs: list = []
        runner = FakeRunner(pending=2, fail_at="upload")
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertIn("migrate-apply", runner.names())
        self.assertIs(self.fx.records()[-1]["db_ahead_of_worker"], True)
        self.assertIn("db_ahead_of_worker: true", verbs[0][1]["body"])

    def test_failure_before_migrations_does_not_claim_db_ahead(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.pipeline(FakeRunner(fail_at="staging-prepare")).tick(["worker"])
        self.assertIs(self.fx.records()[-1]["db_ahead_of_worker"], False)

    def test_a_second_failure_of_the_same_sha_is_not_deduplicated_away(self):
        # N2: after clear-failed the same SHA fails differently; its turn must
        # land with its own msg_id and its own queue key.
        sha = self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        verbs: list = []
        self.fx.pipeline(FakeRunner(fail_at="staging-prepare"), verbs=verbs).tick(["worker"])
        rp.clear_failed(rp.Store(self.fx.repo / "out/release-pipeline"), "worker", sha, "retry",
                        repo_dir=self.fx.repo)
        self.fx.pipeline(FakeRunner(pending=1, fail_at="upload"), verbs=verbs).tick(["worker"])
        turns = [a for v, a in verbs if v == "add-room-turn"]
        self.assertEqual(len(turns), 2)
        self.assertNotEqual(turns[0]["msg_id"], turns[1]["msg_id"])
        self.assertIn(f"key=release-fix-{sha[:8]} ", turns[0]["body"])
        self.assertIn(f"key=release-fix-{sha[:8]}-2 ", turns[1]["body"])
        self.assertIn("db_ahead_of_worker: true", turns[1]["body"])

    def test_a_deduplicated_room_answer_is_not_a_dispatch(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        pipe = self.fx.pipeline(FakeRunner(fail_at="upload"))
        pipe.call_verb = lambda verb, args: (True, {"ok": True, "deduplicated": True})
        self.assertEqual(pipe.tick(["worker"]), 1)
        self.assertIs(self.fx.records()[-1]["dispatched"], False)

    def test_a_hold_after_a_mutation_is_a_failure(self):
        sha = self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        verbs: list = []
        runner = FakeRunner(pending=1)
        orig = runner.run

        def run(argv, **kw):
            if "release" in argv and "show" in argv:
                return rp.Result(0, "")          # every key exists: release_key_exhausted
            return orig(argv, **kw)
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "blocked:release_key_exhausted"))
        self.assertIs(rec["db_ahead_of_worker"], True)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])

    def test_clear_failed_lets_the_same_sha_run_again(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.pipeline(FakeRunner(fail_at="upload")).tick(["worker"])
        store = rp.Store(self.fx.repo / "out/release-pipeline")
        with self.assertRaises(SystemExit):
            rp.clear_failed(store, "worker", "0" * 40, "wrong sha", repo_dir=self.fx.repo)
        msg = rp.clear_failed(store, "worker", sha, "credential restored", repo_dir=self.fx.repo)
        self.assertEqual(msg, f"release-pipeline[worker]: cleared failed {sha[:12]} (upload); next tick retries it")
        self.assertEqual(self.fx.records()[-1]["status"], "failure_cleared")
        self.assertNotIn("cleared_worktree", self.fx.records()[-1])
        self.assertFalse((store.root / "worktrees-cleared").exists())
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)

    def test_clear_failed_retires_the_kept_diagnosis_worktree(self):
        # 2026-10-04, twice: the failed run's worktree stayed for diagnosis,
        # so the retry clear-failed allowed burned the SHA again at
        # "<wt> already exists". Clearing moves it aside (never deletes it).
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.pipeline(FakeRunner(fail_at="upload")).tick(["worker"])
        store = rp.Store(self.fx.repo / "out/release-pipeline")
        kept = Path(store.load()["worker"].get("failed_worktree") or store.release_worktree("worker", sha))
        kept.parent.mkdir(parents=True, exist_ok=True)
        git(self.fx.repo, "worktree", "add", "--detach", str(kept), sha)
        (kept / "diagnosis.log").write_text("evidence")
        msg = rp.clear_failed(store, "worker", sha, "credential restored", repo_dir=self.fx.repo)
        self.assertFalse(kept.exists())
        moved = [p for p in (store.root / "worktrees-cleared").iterdir()]
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0].name.startswith(f"worker-{sha[:12]}-"))
        self.assertEqual((moved[0] / "diagnosis.log").read_text(), "evidence")
        self.assertIn(str(moved[0]), msg)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["cleared_worktree"]), ("failure_cleared", str(moved[0])))
        # Pruned: the old path is no longer a registered worktree.
        self.assertNotIn(str(kept), git(self.fx.repo, "worktree", "list", "--porcelain"))
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("worktree", runner.names())
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        # And a real `git worktree add` at the same path now succeeds.
        git(self.fx.repo, "worktree", "add", "--detach", str(kept), sha)


class ClearFailedRetirement(Base):
    def setUp(self):
        super().setUp()
        self.sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.store = rp.Store(self.fx.repo / "out/release-pipeline")
        self.store.save({"worker": {"failed_sha": self.sha, "failed_step": "upload"}})
        self.kept = self.store.release_worktree("worker", self.sha)
        self.kept.parent.mkdir(parents=True, exist_ok=True)
        git(self.fx.repo, "worktree", "add", "--detach", str(self.kept), self.sha)
        (self.kept / "diagnosis.log").write_text("evidence")

    def clear(self):
        return rp.clear_failed(self.store, "worker", self.sha, "repair verified", repo_dir=self.fx.repo)

    def assert_retry_available(self):
        self.assertIsNone(self.store.load()["worker"]["failed_sha"])
        moved = Path(self.store.records()[-1]["cleared_worktree"])
        self.assertEqual((moved / "diagnosis.log").read_text(), "evidence")
        git(self.fx.repo, "worktree", "add", "--detach", str(self.kept), self.sha)

    def test_retirement_does_not_prune_an_unrelated_missing_registration(self):
        unrelated = self.fx.tmp / "unrelated"
        git(self.fx.repo, "worktree", "add", "--detach", str(unrelated), self.sha)
        unrelated.rename(self.fx.tmp / "unrelated-diagnosis")
        self.clear()
        self.assertIsNotNone(rp.worktree_registration(self.fx.repo, unrelated))
        self.assert_retry_available()

    def test_wrong_head_worktree_is_not_retired(self):
        git(self.kept, "checkout", "--detach", self.fx.base)
        with self.assertRaisesRegex(SystemExit, "identity"):
            self.clear()
        self.assertTrue(self.kept.exists())
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)

    def test_unregistered_active_directory_is_untouched(self):
        git(self.fx.repo, "worktree", "remove", "--force", str(self.kept))
        self.kept.mkdir(); (self.kept / "unrelated.txt").write_text("owned by another operation")
        with self.assertRaisesRegex(SystemExit, "identity"):
            self.clear()
        self.assertEqual((self.kept / "unrelated.txt").read_text(), "owned by another operation")

    def test_attempt_worktrees_do_not_collide_with_retained_diagnosis(self):
        self.store.save({})
        runner = FakeRunner(fail_at="upload")
        pipe = self.fx.pipeline(runner)
        pipe.tick(["worker"])
        path1 = Path(pipe.store.load()["worker"]["failed_worktree"])
        self.assertIn(pipe.run_id, path1.name)
        self.assertNotEqual(path1, self.kept)
        self.assertTrue(self.kept.exists())

    def test_first_failure_save_binds_owned_attempt_worktree(self):
        self.store.save({})
        pipe = self.fx.pipeline(FakeRunner(fail_at="staging-prepare"))
        save = pipe.store.save
        def checked(state):
            lane = state.get("worker", {})
            if lane.get("failed_sha"):
                self.assertEqual(lane.get("failed_worktree"),
                    str(pipe.store.release_worktree("worker", self.sha, pipe.run_id)))
            save(state)
        with mock.patch.object(pipe.store, "save", side_effect=checked):
            pipe.tick(["worker"])

    def test_release_fetch_does_not_mutate_shared_remote_refs_or_fetch_head(self):
        git(self.fx.repo, "update-ref", "refs/remotes/origin/main", self.fx.base)
        fetch_head = self.fx.repo / ".git/FETCH_HEAD"
        fetch_head.write_text("another session's fetch evidence\n")
        pipe = self.fx.pipeline(FakeRunner(), github=FakeGitHub())
        pipe.tick(["worker"])
        self.assertEqual(git(self.fx.repo, "rev-parse", "origin/main"), self.fx.base)
        self.assertEqual(fetch_head.read_text(), "another session's fetch evidence\n")

    def test_retirement_resume_does_not_repeat_registration_removal_or_receipt(self):
        record = self.store.record
        def interrupted(row):
            record(row)
            raise OSError("lost acknowledgement after durable receipt")
        with mock.patch.object(self.store, "record", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.clear()
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)
        self.clear()
        self.assertEqual(len(self.store.records()), 1)
        self.assert_retry_available()

    def test_torn_receipt_append_never_clears_without_a_readable_receipt(self):
        path = self.store.records_path
        def torn(row):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"lane": "worker", "status": "failure_cle')
            raise OSError("interrupted mid-append")
        with mock.patch.object(self.store, "record", side_effect=torn):
            with self.assertRaises(OSError):
                self.clear()
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)
        self.clear()
        receipts = [r for r in self.store.records() if r.get("status") == "failure_cleared"]
        self.assertEqual(len(receipts), 1)
        self.assert_retry_available()

    def test_unreadable_receipt_never_clears_failure(self):
        with mock.patch.object(self.store, "record", return_value=None):
            with self.assertRaisesRegex(SystemExit, "receipt"):
                self.clear()
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)

    def test_registration_result_fault_matrix_preserves_failure_and_evidence(self):
        original = rp.subprocess.run
        for rc, output in [(0, ""), (0, "worktree partial\0\0"),
                (0, "worktree /unknown\0HEAD invalid\0\0"), (7, "")]:
            with self.subTest(rc=rc, output=output):
                def run(argv, **kwargs):
                    if argv[-3:] == ["list", "--porcelain", "-z"]:
                        return subprocess.CompletedProcess(argv, rc, output, "secret-error-canary")
                    return original(argv, **kwargs)
                with mock.patch.object(rp.subprocess, "run", side_effect=run):
                    with self.assertRaises(SystemExit) as refusal:
                        self.clear()
                self.assertNotIn("secret-error-canary", str(refusal.exception))
                self.assertTrue(self.kept.exists())
                self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)

    def test_malformed_registration_after_rename_never_clears_failure(self):
        archive = self.store.root / "worktrees-cleared" / (self.kept.name + "-retained")
        archive.parent.mkdir(parents=True)
        os.rename(self.kept, archive)
        state = self.store.load()
        state["worker"]["failed_worktree_retirement"] = {"sha": self.sha, "path": str(archive)}
        self.store.save(state)
        original = rp.subprocess.run
        def run(argv, **kwargs):
            if argv[-3:] == ["list", "--porcelain", "-z"]:
                return subprocess.CompletedProcess(argv, 0, "worktree /unknown\0HEAD invalid\0\0", "")
            return original(argv, **kwargs)
        with mock.patch.object(rp.subprocess, "run", side_effect=run):
            with self.assertRaises(SystemExit):
                self.clear()
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)
        self.assertEqual(self.store.records(), [])
        self.assertTrue(archive.is_dir())

    def test_clear_resumes_after_registration_removal_failure(self):
        original = subprocess.run

        def run(argv, **kwargs):
            if argv[-3:-1] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(argv, 7, "", "injected registration failure")
            return original(argv, **kwargs)

        with mock.patch.object(rp.subprocess, "run", side_effect=run):
            with self.assertRaises(SystemExit):
                self.clear()
        self.assertFalse(self.kept.exists())
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)
        self.clear()
        self.assert_retry_available()

    def test_new_failed_sha_does_not_reuse_an_older_retirement(self):
        original = subprocess.run

        def run(argv, **kwargs):
            if argv[-3:-1] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(argv, 7, "", "injected registration failure")
            return original(argv, **kwargs)

        with mock.patch.object(rp.subprocess, "run", side_effect=run):
            with self.assertRaises(SystemExit):
                self.clear()
        old_archive = next((self.store.root / "worktrees-cleared").iterdir())
        self.sha = self.fx.commit({"mcp-server/src/a.js": "2"})
        state = self.store.load()
        state["worker"]["failed_sha"] = self.sha
        self.store.save(state)
        self.kept = self.store.release_worktree("worker", self.sha)
        git(self.fx.repo, "worktree", "add", "--detach", str(self.kept), self.sha)
        (self.kept / "diagnosis.log").write_text("evidence")
        self.clear()
        self.assert_retry_available()
        self.assertEqual((old_archive / "diagnosis.log").read_text(), "evidence")

    def test_clear_resumes_after_registration_removal_timeout(self):
        original = subprocess.run

        def run(argv, **kwargs):
            if argv[-3:-1] == ["worktree", "remove"]:
                raise subprocess.TimeoutExpired(argv, 300)
            return original(argv, **kwargs)

        with mock.patch.object(rp.subprocess, "run", side_effect=run):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.clear()
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)
        self.clear()
        self.assert_retry_available()

    def test_clear_resumes_after_interruption_between_rename_and_registration_removal(self):
        original = os.rename

        def rename(src, dst):
            original(src, dst)
            raise OSError("injected interruption after rename")

        with mock.patch.object(rp.os, "rename", side_effect=rename):
            with self.assertRaises(OSError):
                self.clear()
        self.assertFalse(self.kept.exists())
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)
        self.clear()
        self.assert_retry_available()

    def test_clear_preserves_a_locked_diagnosis_worktree(self):
        git(self.fx.repo, "worktree", "lock", "--reason", "diagnosis in progress", str(self.kept))
        before = self.store.load()
        with self.assertRaisesRegex(SystemExit, "locked"):
            self.clear()
        self.assertEqual(self.store.load(), before)
        self.assertEqual((self.kept / "diagnosis.log").read_text(), "evidence")
        self.assertFalse((self.store.root / "worktrees-cleared").exists())
        self.assertEqual(self.store.records(), [])
        git(self.fx.repo, "worktree", "unlock", str(self.kept))
        self.clear()
        self.assert_retry_available()

    def test_clear_refuses_successful_removal_that_retains_registration(self):
        original = subprocess.run

        def run(argv, **kwargs):
            if argv[-3:-1] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            return original(argv, **kwargs)

        with mock.patch.object(rp.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(SystemExit, "still registered"):
                self.clear()
        self.assertEqual(self.store.load()["worker"]["failed_sha"], self.sha)
        self.assertEqual(self.store.records(), [])
        self.clear()
        self.assert_retry_available()

    def test_clear_refuses_while_tick_lock_is_held(self):
        before = self.store.load()
        with rp.single_run_lock(self.store.root) as held:
            self.assertTrue(held)
            with self.assertRaisesRegex(SystemExit, "another run holds the lock"):
                self.clear()
        self.assertEqual(self.store.load(), before)
        self.assertEqual((self.kept / "diagnosis.log").read_text(), "evidence")
        self.assertEqual(self.store.records(), [])

    def test_concurrent_clearance_cannot_start_a_retry_during_rename(self):
        original = os.rename
        competing = False

        def rename(src, dst):
            nonlocal competing
            # The competing clearance must fail before it can clear the SHA
            # and allow a tick to create a new worktree at the original path.
            if not competing:
                competing = True
                with self.assertRaisesRegex(SystemExit, "another run holds the lock"):
                    self.clear()
            original(src, dst)

        with mock.patch.object(rp.os, "rename", side_effect=rename):
            self.clear()
        self.assert_retry_available()

    def test_tick_cannot_update_another_lane_during_registration_removal(self):
        state = self.store.load()
        state["app"] = {"last_released_sha": self.fx.base}
        self.store.save(state)
        original = subprocess.run
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner)
        output: list[str] = []
        pipe.out = output.append

        def run(argv, **kwargs):
            if argv[-3:-1] == ["worktree", "remove"]:
                self.assertEqual(pipe.tick(["worker", "app"]), 0)
                self.assertTrue(any("another run holds the lock" in line for line in output))
                self.assertEqual(runner.calls, [])
            return original(argv, **kwargs)

        with mock.patch.object(rp.subprocess, "run", side_effect=run):
            self.clear()
        self.assertEqual(self.store.load()["app"], state["app"])
        self.assert_retry_available()

    def test_clear_keeps_lock_until_receipt_is_written(self):
        original = self.store.record

        def record(row):
            with self.assertRaisesRegex(SystemExit, "another run holds the lock"):
                self.clear()
            original(row)

        with mock.patch.object(self.store, "record", side_effect=record):
            self.clear()
        self.assert_retry_available()


class StaleControllerRefusal(Base):
    def test_stale_controller_bytes_refuse_release_without_burning_target(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "candidate", "ops/release-pipeline.py": "current controller\n"})
        (self.fx.repo / "ops/release-pipeline.py").write_text("obsolete controller\n")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        pipe = self.fx.pipeline(runner, live=live)
        pipe.store.save({"worker": {"last_released_sha": self.fx.base}})
        self.assertEqual(pipe.tick(["worker"]), 3)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "controller_stale")
        self.assertEqual(self.fx.records()[-1]["sha"], sha)
        state = self.fx.state()["worker"]
        self.assertFalse(state.get("failed_sha"))
        self.assertEqual(state["last_released_sha"], self.fx.base)


class Blockers(Base):
    def test_changed_notification_does_not_reuse_an_incompatible_record_key(self):
        held = rp.blocker_loop("CLOUDFLARE_API_TOKEN", "token missing")
        failed = rp.blocker_loop("CLOUDFLARE_API_TOKEN", "token missing", recovery="Clear the failed SHA.")
        # withEnvelope rejects the same key for changed request bytes. Retry
        # stability comes from the stored pending payload, including its key.
        self.assertNotEqual(held["idempotency_key"], failed["idempotency_key"])
        self.assertEqual(failed, rp.blocker_loop("CLOUDFLARE_API_TOKEN", "token missing",
                                               recovery="Clear the failed SHA."))

    def test_nonzero_auth_rejection_files_the_credential_loop(self):
        for response in ("Authentication error [code: 10000]",
                         "A request to the Cloudflare API (/user/tokens/verify) failed.\nInvalid access token [code: 9109]"):
            with self.subTest(response=response):
                self.fx.commit({"mcp-server/src/a.js": response})
                verbs: list = []
                runner = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": response})
                self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
                self.assertEqual(self.fx.records()[-1]["step"], "credential-missing")
                self.assertEqual(self.fx.records()[-1]["rc"], 7)
                self.assertIn("credential rejected", self.fx.records()[-1]["detail"])
                self.assertIn("CLOUDFLARE_API_TOKEN:rejected", self.fx.state()["filed_blockers"])
                self.assertNotIn("worktree-add", runner.names())

    def test_nonzero_auth_network_failure_does_not_name_a_rejected_token(self):
        for response in ("fetch failed: network timeout", "Cloudflare API unavailable [code: 10001]"):
            with self.subTest(response=response):
                self.fx.commit({"mcp-server/src/a.js": response})
                verbs: list = []
                runner = FakeRunner(fail_at="wrangler-auth", outputs={"wrangler-auth": response})
                self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
                self.assertEqual(self.fx.records()[-1]["step"], "wrangler-auth")
                self.assertEqual([v for v, _ in verbs], ["add-room-turn"])

    def test_failed_loop_delivery_retries_without_retrying_the_failed_sha(self):
        (self.fx.cred / "tokens.env").unlink()
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        first_calls: list = []
        pipe = self.fx.pipeline(FakeRunner())
        def unavailable(verb, args):
            first_calls.append((verb, args))
            return (False, "temporary network outage") if verb == "add-loop" else (True, {})
        pipe.call_verb = unavailable
        self.assertEqual(pipe.tick(["worker"]), 1)
        self.assertFalse(self.fx.records()[-1]["loop_filed"])
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        retry_calls: list = []
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, verbs=retry_calls).tick(["worker"]), 0)
        self.assertEqual([v for v, _ in retry_calls], ["add-loop"])
        self.assertEqual(first_calls[0][1], retry_calls[0][1])
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertIn("CLOUDFLARE_API_TOKEN", self.fx.state()["filed_blockers"])
        self.assertFalse(self.fx.state().get("pending_blockers"))
        final_calls: list = []
        self.fx.pipeline(FakeRunner(), verbs=final_calls).tick(["worker"])
        self.assertEqual(final_calls, [])

    def test_cloudflare_recipe_tracks_the_inventory_and_checked_directory(self):
        (self.fx.cred / "tokens.env").unlink()
        inventory = self.fx.tmp / "credential-inventory.json"
        inventory.write_text(json.dumps({"credentials": [{
            "name": "cloudflare-deploy-token", "probe": {"path": "~/canonical/tokens.env"},
            "replacement_plan": "Grant fixture scope only; store in ~/canonical/tokens.env; chmod 600 ~/canonical/tokens.env."
        }]}))
        self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        with mock.patch.object(rp, "CREDENTIAL_INVENTORY_PATH", inventory, create=True):
            self.assertEqual(self.fx.pipeline(FakeRunner(), verbs=verbs).tick(["worker"]), 1)
        body = verbs[0][1]["body"]
        self.assertIn("Grant fixture scope only", body)
        self.assertIn(f"store in {self.fx.cred / 'tokens.env'}", body)
        self.assertIn(f"chmod 600 {self.fx.cred / 'tokens.env'}", body)
        self.assertNotIn("~/canonical/tokens.env", body)
        self.assertNotIn("~/.config/carr/tokens.env", body)
        self.assertNotIn("Workers Scripts:Edit", body)

    def test_hold_loop_keeps_automatic_retry_instructions(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        (self.fx.cred / "db.env").write_text("")
        verbs: list = []
        self.assertEqual(self.fx.pipeline(FakeRunner(), verbs=verbs).tick(["worker"]), 3)
        body = verbs[0][1]["body"]
        self.assertIn("every tick", body)
        self.assertNotIn("clear-failed", body)

    def test_health_blocker_names_the_authorized_repair_lane(self):
        args = rp.blocker_loop("health_baseline_hard_error", "Jev receipt integrity is broken")
        self.assertEqual(args["blocker"], "other_lane")
        self.assertEqual(args["owner"], "Claude")
        self.assertIn("release-repair lane", args["blocker_detail"])

    def test_credential_blocker_names_the_decider_for_the_live_verb_gate(self):
        args = rp.blocker_loop("NEON_API_KEY", "NEON_API_KEY is absent from db.env")
        result = subprocess.run(["node", "--input-type=module", "-e",
            "import {needsDecider} from './mcp-server/src/verb-gate-checks.js'; "
            "console.log(JSON.stringify(needsDecider(JSON.parse(process.argv[1]))));",
            json.dumps(args)], cwd=str(HERE.parent), capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), False)

    def test_call_verb_preserves_multiline_error_and_both_streams(self):
        pipe = self.fx.pipeline(FakeRunner())
        failed = subprocess.CompletedProcess([], 1, stdout="request refused\n",
            stderr='local-verb identity\nTOOL ERROR {\n  "error": "capability_no_decider",\n'
                   '  "dsn": "postgres://user:secret@example.invalid/db"\n}\n')  # ci-secret-scan: allow (redaction fixture)
        with mock.patch.object(rp.subprocess, "run", return_value=failed):
            ok, detail = pipe._call_verb("add-loop", {})
        self.assertFalse(ok)
        self.assertIn("capability_no_decider", detail)
        self.assertIn("request refused", detail)
        self.assertNotIn("user:secret", detail)
        self.assertIn("[REDACTED]", detail)

    def test_call_verb_preserves_plaintext_identifiers_without_exposing_credentials(self):
        pipe = self.fx.pipeline(FakeRunner())
        sha = "cd23702fe8c6f94a6b43f1ae2cebc3f6ed61bd82"
        receipt = "aa870010-f2c7-4a02-9994-9c141eed5200"
        known_secret = "12345678-1234-1234-1234-123456789abc"
        pipe.env["RUNNER_TOKEN"] = known_secret
        stderr = (f"Source SHA mismatch: {sha}\nreceipt_id={receipt}\n"
                  f"receipt_id={known_secret}\n"
                  f"TOKEN={sha}\nPASSWORD={receipt}\n"
                  "receipt_id=ghp_syntheticfixture12345678901234567890\n"  # ci-secret-scan: allow (synthetic redaction fixture)
                  "dsn=postgres://user:secret@example.invalid/db\n")  # ci-secret-scan: allow (synthetic redaction fixture)
        failed = subprocess.CompletedProcess([], 1, stdout="request refused\n", stderr=stderr)
        with mock.patch.object(rp.subprocess, "run", return_value=failed):
            ok, detail = pipe._call_verb("add-loop", {})
        self.assertFalse(ok)
        self.assertIn(f"Source SHA mismatch: {sha}", detail)
        self.assertIn(f"receipt_id={receipt}", detail)
        self.assertIn("request refused", detail)
        self.assertNotIn(known_secret, detail)
        self.assertNotIn(f"TOKEN={sha}", detail)
        self.assertNotIn(f"PASSWORD={receipt}", detail)
        self.assertNotIn("ghp_syntheticfixture", detail)
        self.assertNotIn("user:secret", detail)

    def test_missing_credential_files_one_loop_once(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        (self.fx.cred / "db.env").write_text("CARR_DB_JOBS_URL='v'\n")
        verbs: list = []
        for _ in range(2):
            runner = FakeRunner()
            self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 3)
            self.assertEqual(runner.calls, [])
        self.assertEqual([v for v, _ in verbs], ["add-loop"])
        self.assertEqual(verbs[0][1]["blocker"], "capability")
        self.assertIn("NEON_API_KEY", verbs[0][1]["blocker_detail"])

    def test_rejected_token_fails_and_dispatches_before_any_worktree(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner(wrangler_out="You are not authenticated. Please run `wrangler login`.")
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertEqual(runner.names(), ["wrangler-auth"])
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual(self.fx.records()[-1]["step"], "credential-missing")
        self.assertIn("credential rejected", self.fx.records()[-1]["detail"])
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])
        self.assertIn("credential rejected", verbs[0][1]["blocker_detail"])
        self.assertTrue(self.fx.records()[-1]["loop_filed"])


class CapabilityRecovery(Base):
    """PR 1287: delivery recovery never replays a failed deployment."""

    def missing(self):
        (self.fx.cred / "tokens.env").write_text("")
        return self.fx.commit({"mcp-server/src/a.js": "1"})

    def test_first_diagnosis_retries_after_timeout_without_new_sha(self):
        self.missing()
        calls = []

        def door(verb, args):
            calls.append((verb, args))
            return (verb != "add-room-turn", "timeout")

        pipe = self.fx.pipeline(FakeRunner())
        pipe.call_verb = door
        self.assertEqual(pipe.tick(["worker"]), 1)
        retry = self.fx.pipeline(FakeRunner(), verbs=calls)
        self.assertEqual(retry.tick(["worker"]), 0)
        turns = [a for v, a in calls if v == "add-room-turn"]
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0], turns[1])
        self.assertEqual(retry.runner.calls, [])
        self.fx.commit({"mcp-server/src/a.js": "2"})
        self.assertEqual(self.fx.pipeline(FakeRunner(), verbs=calls).tick(["worker"]), 1)
        self.assertEqual(len([v for v, _ in calls if v == "add-room-turn"]), 2)

    def test_first_diagnosis_survives_crash_after_loop_ack(self):
        self.missing()
        calls = []

        def crash(verb, args):
            calls.append((verb, args))
            if verb == "add-room-turn":
                raise SystemExit("fixture crash")
            return True, {"ok": True}

        pipe = self.fx.pipeline(FakeRunner())
        pipe.call_verb = crash
        with self.assertRaises(SystemExit):
            pipe.tick(["worker"])
        retry = self.fx.pipeline(FakeRunner(), verbs=calls)
        self.assertEqual(retry.tick(["worker"]), 0)
        self.assertEqual([v for v, _ in calls], ["add-loop", "add-room-turn", "add-room-turn"])
        self.assertEqual(calls[1][1], calls[2][1])
        self.assertEqual(retry.runner.calls, [])

    def test_lost_diagnosis_ack_replays_original_turn_and_accepts_dedup(self):
        self.missing()
        calls = []

        def lost_ack(verb, args):
            calls.append((verb, args))
            if verb == "add-room-turn":
                raise TimeoutError("ack lost")
            return True, {"ok": True}

        pipe = self.fx.pipeline(FakeRunner())
        pipe.call_verb = lost_ack
        self.assertEqual(pipe.tick(["worker"]), 1)

        def replay(verb, args):
            calls.append((verb, args))
            return True, {"deduplicated": True}

        retry = self.fx.pipeline(FakeRunner())
        retry.call_verb = replay
        self.assertEqual(retry.tick(["worker"]), 0)
        self.assertEqual(calls[1][1], calls[2][1])
        self.assertEqual(retry.runner.calls, [])
        self.assertEqual(self.fx.state()["pending_diagnoses"], {})
        self.assertIn("CLOUDFLARE_API_TOKEN", self.fx.state()["diagnosed_capabilities"])
        self.fx.commit({"mcp-server/src/a.js": "2"})
        self.assertEqual(self.fx.pipeline(FakeRunner(), verbs=calls).tick(["worker"]), 1)
        self.assertEqual([v for v, _ in calls], ["add-loop", "add-room-turn", "add-room-turn"])

    def test_failed_loop_does_not_duplicate_delivered_diagnosis_across_shas(self):
        self.missing()
        calls = []

        def door(verb, args):
            calls.append((verb, args))
            return verb != "add-loop", "timeout"

        for i in range(2):
            pipe = self.fx.pipeline(FakeRunner())
            pipe.call_verb = door
            self.assertEqual(pipe.tick(["worker"]), 1)
            self.fx.commit({"mcp-server/src/a.js": str(i + 2)})
        self.assertEqual(len([v for v, _ in calls if v == "add-room-turn"]), 1)
        self.assertIn("CLOUDFLARE_API_TOKEN", self.fx.state()["pending_blockers"])

    def test_nonzero_auth_rejection_files_one_loop_and_one_diagnosis(self):
        calls = []
        for i in range(2):
            self.fx.commit({"mcp-server/src/a.js": str(i)})
            runner = FakeRunner(fail_at="wrangler-auth", outputs={
                "wrangler-auth": "Authentication error [code: 10000]"})
            self.assertEqual(self.fx.pipeline(runner, verbs=calls).tick(["worker"]), 1)
            self.assertEqual(runner.names(), ["wrangler-auth"])
        self.assertEqual([v for v, _ in calls], ["add-loop", "add-room-turn"])
        self.assertIn("credential rejected", self.fx.records()[-1]["detail"])

    def test_pending_loop_retries_exact_request_on_burned_sha(self):
        self.missing()
        calls = []

        def door(verb, args):
            calls.append((verb, args))
            return (verb != "add-loop", "timeout")

        pipe = self.fx.pipeline(FakeRunner())
        pipe.call_verb = door
        self.assertEqual(pipe.tick(["worker"]), 1)
        retry = self.fx.pipeline(FakeRunner(), verbs=calls)
        self.assertEqual(retry.tick(["worker"]), 0)
        loops = [a for v, a in calls if v == "add-loop"]
        self.assertEqual(len(loops), 2)
        self.assertEqual(loops[0], loops[1])
        self.assertEqual(retry.runner.calls, [])

    def test_report_tracks_only_lanes_still_failed_after_token_repair(self):
        sha = self.missing()
        calls = []
        self.assertEqual(self.fx.pipeline(FakeRunner(), verbs=calls).tick(["worker"]), 1)
        notification = calls[0][1]["body"]
        self.assertIn(sha, notification)
        self.assertIn("automatically retries", notification)
        live = {"sha": self.fx.base}
        pipe = self.fx.pipeline(FakeRunner(live=live), live=live, verbs=calls)
        state = pipe.store.load()
        state["app"] = {"failed_sha": "b" * 40, "failed_step": "credential-missing"}
        pipe.store.save(state)
        report = rp.report(pipe.store, "2099-01-01")
        for lane, head in (("worker", sha), ("app", "b" * 40)):
            self.assertIn(f"--lane {lane} --sha {head}", report)
        self.assertIn("verif", report.lower())
        (self.fx.cred / "tokens.env").write_text(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
        self.assertEqual(pipe.tick(["worker"]), 0)
        report = rp.report(pipe.store, "2099-01-01")
        self.assertNotIn(f"--lane worker --sha {sha}", report)
        self.assertIn(f"--lane app --sha {'b' * 40}", report)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        self.assertEqual([v for v, _ in calls], ["add-loop", "add-room-turn"])

    def test_loop_operation_key_binds_complete_payload(self):
        first = rp.blocker_loop("CLOUDFLARE_API_TOKEN", "missing from /one")
        second = rp.blocker_loop("CLOUDFLARE_API_TOKEN", "missing from /two")
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(first, rp.blocker_loop("CLOUDFLARE_API_TOKEN", "missing from /one"))

    def test_remedy_uses_inventory_and_checked_custom_path(self):
        self.missing()
        inventory = self.fx.tmp / "inventory.json"
        inventory.write_text(json.dumps({"credentials": [{"name": "cloudflare-deploy-token",
            "probe": {"path": "~/.config/carr/tokens.env"},
            "replacement_plan": "Fixture authority: provision ~/.config/carr/tokens.env"}]}))
        calls = []
        with mock.patch.object(rp, "CREDENTIAL_INVENTORY_PATH", inventory, create=True):
            self.assertEqual(self.fx.pipeline(FakeRunner(), verbs=calls).tick(["worker"]), 1)
        body = calls[0][1]["body"]
        self.assertIn(f"Fixture authority: provision {self.fx.cred / 'tokens.env'}", body)
        self.assertNotIn("~/.config/carr/tokens.env", body)


class DeployCredential(unittest.TestCase):
    """CLOUDFLARE_API_TOKEN comes from $HOME/.config/carr/tokens.env into the
    wrangler-running steps' env only. Every case runs under a temporary HOME,
    so the operator's real credential files are never read."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.home = tmp / "home"
        self.cred = self.home / ".config" / "carr"
        self.cred.mkdir(parents=True)
        self.fx = Fixture(tmp)
        for name in ("db.env", "mcp-tokens.env"):
            (self.cred / name).write_text((self.fx.cred / name).read_text())
        self._home = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._home.start()
        self.lines: list[str] = []

    def tearDown(self):
        self._home.stop()
        self._tmp.cleanup()

    def pipeline(self, runner, *, verbs=None, dry_run=False, app=False):
        cfg = self.fx.config(credential_dir="~/.config/carr")
        if app:
            cfg["worker"]["enabled"] = False
            cfg["app"]["enabled"] = True
        pipe = self.fx.pipeline(runner, cfg=cfg, verbs=verbs, dry_run=dry_run)
        pipe.out = self.lines.append
        return pipe

    def write_tokens(self, text: str) -> None:
        (self.cred / "tokens.env").write_text(text)

    def assert_never_echoed(self):
        self.assertFalse(any(CF_TOKEN in line for line in self.lines), "token printed")
        out = self.fx.repo / "out"
        for f in out.rglob("*") if out.exists() else []:
            if f.is_file():
                self.assertNotIn(CF_TOKEN, f.read_text(errors="replace"), f"token written to {f}")

    def test_token_reaches_only_the_wrangler_steps(self):
        self.write_tokens(f"# deploy\nOTHER=x\nexport CLOUDFLARE_API_TOKEN='{CF_TOKEN}'\n")
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live, cfg=self.fx.config(credential_dir="~/.config/carr"))
                         .tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        for step in ("wrangler-auth", "upload", "staging", "promote"):
            self.assertEqual(runner.envs[step].get("CLOUDFLARE_API_TOKEN"), CF_TOKEN, step)
        for step in ("worktree", "npm-ci", "staging-prepare", "migrate-plan"):
            self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs[step], step)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", rp.child_env({"CLOUDFLARE_API_TOKEN": "x", "HOME": "/h"}))
        self.assert_never_echoed()

    def _assert_credential_failure(self, verbs, runner, sha):
        self.assertEqual(runner.calls, [])                       # before wrangler, before any worktree
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "credential-missing"))
        self.assertIn("credential missing: CLOUDFLARE_API_TOKEN", rec["detail"])
        self.assertIn(str(self.cred / "tokens.env"), rec["detail"])
        self.assertTrue(rec["dispatched"])
        self.assertTrue(rec["loop_filed"])
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])
        self.assertEqual(verbs[0][1]["blocker"], "capability")
        self.assertIn("CLOUDFLARE_API_TOKEN is absent", verbs[0][1]["blocker_detail"])
        self.assertIn("Joe grants it", verbs[0][1]["blocker_detail"])
        self.assertIn("chmod 600", verbs[0][1]["body"])
        self.assertIn("credential-missing", verbs[1][1]["body"])
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertFalse((self.fx.repo / "out/release-pipeline/worktrees").exists()
                         and any((self.fx.repo / "out/release-pipeline/worktrees").iterdir()))
        self.assertTrue(any("credential missing" in line for line in self.lines))

    def test_missing_file_fails_loudly_and_dispatches(self):
        shutil.rmtree(self.fx.repo / "out/release-pipeline/toolchain")
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner()
        self.assertEqual(self.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self._assert_credential_failure(verbs, runner, sha)

    def test_file_without_the_key_fails_loudly_and_dispatches(self):
        self.write_tokens("SOMETHING_ELSE=v\nCLOUDFLARE_API_TOKEN=\n")
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner()
        self.assertEqual(self.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self._assert_credential_failure(verbs, runner, sha)

    def test_app_lane_release_gets_the_token_and_fails_without_it(self):
        self.write_tokens(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        pipe = self.pipeline(runner, app=True)
        live = {"source_commit": self.fx.base, "environment": "production"}
        pipe.http = lambda _u: live
        orig = runner.run

        def run(argv, **kw):
            res = orig(argv, **kw)
            if argv[:2] == ["node", "scripts/release-production.mjs"]:
                live["source_commit"] = sha
            return res
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(pipe.tick(["app"]), 0)
        self.assertEqual(runner.envs["app-release"].get("CLOUDFLARE_API_TOKEN"), CF_TOKEN)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs["app-npm-ci"])
        self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs["app-build"])
        self.assertEqual(runner.calls[runner.names().index("app-build")][1],
                         ["npm", "run", "build"])
        self.assertEqual(runner.calls[runner.names().index("app-release")][1],
                         ["node", "scripts/release-production.mjs"])
        self.assert_never_echoed()

        (self.cred / "tokens.env").write_text("")
        sha2 = self.fx.commit({"src/worker.js": "2"})
        verbs: list = []
        runner2 = FakeRunner()
        pipe2 = self.pipeline(runner2, verbs=verbs, app=True)
        pipe2.http = lambda _u: live
        self.assertEqual(pipe2.tick(["app"]), 1)
        self.assertEqual(runner2.calls, [])
        self.assertEqual(self.fx.records()[-1]["step"], "credential-missing")
        self.assertEqual(self.fx.state()["app"]["failed_sha"], sha2)
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])

    def test_missing_token_files_its_loop_once_across_shas(self):
        verbs: list = []
        for i in range(2):
            self.fx.commit({"mcp-server/src/a.js": str(i)})
            self.assertEqual(self.pipeline(FakeRunner(), verbs=verbs).tick(["worker"]), 1)
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])
        first, last = self.fx.records()[-2:]
        self.assertTrue(first["dispatched"])
        self.assertFalse(last["dispatched"])
        self.assertEqual(last["dispatch_skipped"], "capability_already_diagnosed")
        self.assertNotIn("loop_filed", self.fx.records()[-1])
        self.assertIn("CLOUDFLARE_API_TOKEN", self.fx.state()["filed_blockers"])

    def test_token_restoration_automatically_retries_both_lanes(self):
        for lane in ("worker", "app"):
            with self.subTest(lane=lane):
                self.write_tokens("")
                sha = self.fx.commit({"mcp-server/src/a.js": lane, "src/worker.js": lane})
                store = rp.Store(self.fx.repo / "out/release-pipeline")
                state = store.load()
                state.pop("filed_blockers", None)
                store.save(state)
                verbs: list = []
                failing = self.pipeline(FakeRunner(), verbs=verbs, app=lane == "app")
                if lane == "app":
                    failing.http = lambda _u: {"source_commit": self.fx.base, "environment": "production"}
                self.assertEqual(failing.tick([lane]), 1)
                body = verbs[0][1]["body"]
                self.assertIn(f"{lane} lane", body)
                self.assertIn(sha, body)
                self.assertIn("ops/release-pipeline.py report", body)
                self.assertIn(f"clear-failed --lane {lane} --sha {sha}",
                              rp.report(store, "2099-01-01"))
                self.assertIn("automatically retries", body)
                self.write_tokens(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
                live = {"sha": self.fx.base}
                runner = FakeRunner(live=live)
                pipe = self.pipeline(runner, app=lane == "app")
                if lane == "worker":
                    pipe.http = lambda _u: {"git_sha": {"value": live["sha"]},
                                          "worker_version": {"id": live.get("version", PRIOR)}}
                else:
                    pipe.http = lambda _u: {"source_commit": live["sha"], "environment": "production"}
                    original_run = runner.run
                    def run(argv, **kw):
                        result = original_run(argv, **kw)
                        if argv[:2] == ["node", "scripts/release-production.mjs"]:
                            live["sha"] = sha
                        return result
                    runner.run = run
                self.assertEqual(pipe.tick([lane]), 0)
                self.assertEqual(self.fx.state()[lane]["last_released_sha"], sha)

    def test_combined_lanes_recover_from_one_notification_without_resetting_dedup(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "both", "src/worker.js": "both"})
        cfg = self.fx.config(credential_dir="~/.config/carr")
        cfg["app"].update(enabled=True)
        live = {"worker": self.fx.base, "app": self.fx.base}
        verbs: list = []

        def pipeline(runner):
            pipe = self.fx.pipeline(runner, cfg=cfg, verbs=verbs)
            pipe.http = lambda url: ({"git_sha": {"value": live["worker"]}, "worker_version": {"id": PRIOR}}
                                     if url == cfg["worker"]["live_release_url"] else
                                     {"source_commit": live["app"], "environment": "production"})
            return pipe

        self.assertEqual(pipeline(FakeRunner()).tick(["worker", "app"]), 1)
        for lane in live:
            self.assertEqual(self.fx.state()[lane]["failed_sha"], sha)
        loops = [args for verb, args in verbs if verb == "add-loop"]
        self.assertEqual(len(loops), 1)
        self.assertIn("ops/release-pipeline.py report", loops[0]["body"])
        self.assertIn("every currently failed lane", loops[0]["body"])

        self.write_tokens(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
        store = rp.Store(self.fx.repo / "out/release-pipeline")
        worker_live = {"sha": self.fx.base}
        runner = FakeRunner(live=worker_live)
        original_run = runner.run
        def run(argv, **kw):
            result = original_run(argv, **kw)
            live["worker"] = worker_live["sha"]
            if argv[:2] == ["node", "scripts/release-production.mjs"]:
                live["app"] = sha
            return result
        runner.run = run
        self.assertEqual(pipeline(runner).tick(["worker", "app"]), 0)
        for lane in live:
            self.assertEqual(self.fx.state()[lane]["last_released_sha"], sha)
        self.assertEqual(len([v for v, _ in verbs if v == "add-loop"]), 1)
        self.assertIn("CLOUDFLARE_API_TOKEN", self.fx.state()["filed_blockers"])
        self.assertNotIn("clear-failed", rp.report(store, "2099-01-01"))
        self.assert_never_echoed()

    def test_dry_run_reports_the_missing_token_and_records_nothing(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.pipeline(runner, dry_run=True).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertTrue(any("would FAIL here: credential missing" in line for line in self.lines))
        self.assertEqual(self.fx.records(), [])


class FailClosedVenv(Base):
    """The coordinator's own re-run of health-preflight at a8619391 caught
    this: with no real `.venv` on the machine, `ln -s <repo>/.venv
    <worktree>/.venv` happily created a DANGLING symlink anyway, and the
    health read then reported 5 hard errors for missing psycopg/openpyxl —
    reading exactly like a code bug when it was really this machine's
    environment never having a venv at all. `_release_worktree_ready` (used
    by both release_worker and health_preflight) now checks the SOURCE
    venv's `bin/python` before linking and fails closed with a clear
    message naming the real cause, instead of producing a dangling link
    whose failure mode looks unrelated."""

    def test_release_worker_fails_closed_when_the_source_venv_is_missing(self):
        (self.fx.repo / ".venv" / "bin" / "python").unlink()
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "venv-source-missing")
        self.assertIn(".venv", rec.get("detail", ""))
        self.assertIn("bin/python", rec.get("detail", ""))
        # the real `ln -s` never ran — no dangling symlink was created.
        self.assertNotIn("venv-link", runner.names())
        self.assertNotIn("npm-ci", runner.names())

    def test_release_worker_still_ships_when_the_source_venv_is_present(self):
        # The fixture's stub `.venv/bin/python` (see Fixture.__init__) is
        # exactly what a real checkout has; this is the control case
        # proving the fail-closed check does not false-positive on a
        # perfectly fine venv.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("venv-link", runner.names())

    def test_health_preflight_fails_closed_when_the_source_venv_is_missing(self):
        (self.fx.repo / ".venv" / "bin" / "python").unlink()
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner)
        lines: list[str] = []
        pipe.out = lines.append
        self.assertEqual(pipe.health_preflight(sha), 1)
        text = "\n".join(lines)
        self.assertIn(".venv", text)
        self.assertIn("bin/python", text)
        self.assertNotIn("health-baseline", runner.names())
        # the throwaway worktree was still cleaned up on this failure.
        self.assertFalse(pipe.worktrees)

    def test_dry_run_never_touches_the_real_filesystem_for_the_venv_check(self):
        # A dry run must stay purely descriptive — it must not fail closed
        # (or succeed) based on the real machine's venv state, the same
        # contract every other dry-run step already has.
        (self.fx.repo / ".venv" / "bin" / "python").unlink()
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, dry_run=True).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])


class DryRun(Base):
    def test_dry_run_prints_and_executes_nothing(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        lines: list[str] = []
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, dry_run=True)
        pipe.out = lines.append
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.state(), {})
        self.assertEqual(self.fx.records(), [])
        text = "\n".join(lines)
        for needle in ("staging-project-replacement.py prepare --apply --local-checks-green",
                       "provision-staging-app-writer.py", "bin/migrate-prod.sh",
                       f"bin/deploy-worker.sh --upload-version --release-sha {sha}",
                       "--env staging --recovery-step forward_fix", "--promote-version", "./run.sh health"):
            self.assertIn(needle, text)


class LostStagingCredentialRotation(Base):
    """Joe's 2026-10-12 approval reaches the provisioner through ONE command:
    `tick --lane worker --rotate-lost-staging-credential LABEL` forwards
    --rotate-lost-credential LABEL to the staging-app-writer step and nowhere
    else; without it the step's argv is exactly what it always was."""

    def _app_writer_argv(self, runner):
        return next(argv for name, argv in runner.calls if name == "staging-app-writer")

    def test_the_flag_is_forwarded_to_the_staging_app_writer_step_only(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        pipe = self.fx.pipeline(runner, live=live)
        pipe.rotate_lost_staging_credentials = ("writer", "gate_zero_producer")
        self.assertEqual(pipe.tick(["worker"]), 0)
        argv = self._app_writer_argv(runner)
        self.assertEqual(argv[argv.index("--apply"):], [
            "--apply", "--rotate-lost-credential", "writer",
            "--rotate-lost-credential", "gate_zero_producer"])
        for name, other in runner.calls:
            if name != "staging-app-writer":
                self.assertNotIn("--rotate-lost-credential", other, name)

    def test_without_the_flag_the_step_is_unchanged(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        argv = self._app_writer_argv(runner)
        self.assertEqual(argv[-1], "--apply")
        self.assertNotIn("--rotate-lost-credential", argv)

    def test_dry_run_says_a_rotation_would_occur_and_runs_nothing(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        lines: list[str] = []
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, dry_run=True)
        pipe.out = lines.append
        pipe.rotate_lost_staging_credentials = ("writer",)
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        text = "\n".join(lines)
        self.assertIn("[dry-run] staging-app-writer will DELIBERATELY ROTATE "
                      "the lost staging credential(s) writer", text)
        self.assertIn("provision-staging-app-writer.py", text)
        self.assertIn("--apply --rotate-lost-credential writer", text)

    def test_main_forwards_the_labels_for_a_worker_tick_only(self):
        built: list[dict] = []

        class Recorder:
            def __init__(self, cfg, **kwargs):
                built.append(kwargs)

            def tick(self, lanes):
                built[-1]["lanes"] = lanes
                return 0

        with mock.patch.object(rp, "Pipeline", Recorder), \
                mock.patch.object(rp, "load_config", return_value={}):
            self.assertEqual(rp.main(["tick", "--lane", "worker", "--dry-run",
                                      "--rotate-lost-staging-credential", "writer",
                                      "--rotate-lost-staging-credential", "reader"]), 0)
            self.assertEqual(built[-1]["rotate_lost_staging_credentials"], ("writer", "reader"))
            self.assertEqual(built[-1]["lanes"], ["worker"])
            self.assertTrue(built[-1]["dry_run"])
            self.assertEqual(rp.main(["tick", "--lane", "worker"]), 0)
            self.assertEqual(built[-1]["rotate_lost_staging_credentials"], ())
            for bad in (["tick", "--rotate-lost-staging-credential", "writer"],
                        ["tick", "--lane", "app", "--rotate-lost-staging-credential", "writer"],
                        ["tick", "--lane", "worker", "--lane", "app",
                         "--rotate-lost-staging-credential", "writer"],
                        ["report", "--lane", "worker", "--rotate-lost-staging-credential", "writer"],
                        ["tick", "--lane", "worker", "--rotate-lost-staging-credential", "production"],
                        ["tick", "--lane", "worker", "--rotate-lost-staging-credential", "writer",
                         "--rotate-lost-staging-credential", "writer"]):
                with self.subTest(bad=bad), mock.patch("sys.stderr"), \
                        self.assertRaises(SystemExit) as raised:
                    rp.main(bad)
                self.assertEqual(raised.exception.code, 2)

    def test_the_labels_are_exactly_the_provisioners_credential_profiles(self):
        spec = importlib.util.spec_from_file_location(
            "staging_database_credential_for_release_selftest",
            HERE.parent / "tools/staging_database_credential.py")
        assert spec is not None and spec.loader is not None
        credential = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = credential
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(credential)
        with self.assertRaises(credential.CredentialRefusal) as unknown:
            credential.profile("__no_such_profile__", config_root=Path(self._tmp.name))
        listed = str(unknown.exception).split(": ", 1)[1].split(", ")
        self.assertEqual(sorted(rp.ROTATABLE_STAGING_CREDENTIALS), sorted(listed))


class SchemaSnapshotGhRunner(FakeRunner):
    """FakeRunner with a stubbed `gh pr list` / `gh pr close`: an in-memory
    set of open PRs, and chosen PR numbers whose close exits nonzero or
    raises."""

    def __init__(self, open_prs: dict[int, str], new_pr: int, *, close_rc: dict | None = None,
                 close_raises: set | None = None, list_rc: int = 0, forks: set | None = None,
                 pr_create_out: str | None = None, no_fork_flag: set | None = None):
        super().__init__()
        self.no_fork_flag = no_fork_flag or set()   # rows listed WITHOUT isCrossRepository
        self.forks = forks or set()          # PR numbers whose head lives in a fork
        self.pr_create_out = pr_create_out   # override what `gh pr create` prints
        self.open_prs, self.new_pr = dict(open_prs), new_pr
        self.close_rc, self.close_raises = close_rc or {}, close_raises or set()
        self.list_rc = list_rc
        self.closed: list[tuple[int, str]] = []

    def run(self, argv, *, cwd, log, env, timeout=3600):
        if argv[:3] == ["gh", "pr", "list"]:
            self.calls.append(("gh-pr-list", list(argv)))
            assert "isCrossRepository" in argv[argv.index("--json") + 1], "fork flag not requested"
            rows = [{"number": n, "headRefName": h, "isCrossRepository": n in self.forks}
                    for n, h in self.open_prs.items()]
            for row in rows:
                if row["number"] in self.no_fork_flag:
                    del row["isCrossRepository"]
            return rp.Result(self.list_rc, json.dumps(rows) if self.list_rc == 0 else "boom")
        if argv[:3] == ["gh", "pr", "close"]:
            self.calls.append(("gh-pr-close", list(argv)))
            num = int(argv[3])
            if num in self.close_raises:
                raise OSError("gh vanished")
            rc = self.close_rc.get(num, 0)
            if rc == 0:
                self.closed.append((num, argv[argv.index("--comment") + 1]))
                self.open_prs.pop(num, None)
            return rp.Result(rc, "")
        if argv[:3] == ["git", "status", "--porcelain"] and log.stem.endswith("schema-diff"):
            self.calls.append(("schema-diff", list(argv)))
            return rp.Result(0, " M db/schema.sql\n")      # the shadow rewrote the snapshot
        res = super().run(argv, cwd=cwd, log=log, env=env, timeout=timeout)
        if log.stem.endswith("schema-pr") and res.rc == 0:
            self.open_prs[self.new_pr] = argv[argv.index("--head") + 1]
            if self.pr_create_out is not None:
                return rp.Result(0, self.pr_create_out)
            return rp.Result(0, f"https://example.invalid/o/r/pull/{self.new_pr}\n")
        return res


class RealGitRunner(FakeRunner):
    """Runs git steps for real; migrate-apply regenerates the worktree's
    db/schema.sql the way bin/migrate-prod.sh does."""

    def __init__(self, schema: str, shadow_schema: str | None = None, **kw):
        super().__init__(**kw)
        self.schema = schema            # what bin/migrate-prod.sh dumps from production
        self.shadow_schema = schema if shadow_schema is None else shadow_schema   # what the PG 18 shadow writes

    def run(self, argv, *, cwd, log, env, timeout=3600):
        if argv[0] != "git":
            res = super().run(argv, cwd=cwd, log=log, env=env, timeout=timeout)
            if log.stem.endswith("migrate-apply"):
                (Path(cwd) / "db/schema.sql").write_text(self.schema)
            if log.stem.endswith("schema-shadow"):
                (Path(cwd) / "db/schema.sql").write_text(self.shadow_schema)
            return res
        self.calls.append((log.stem.split("-", 1)[1], list(argv)))
        proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True)
        return rp.Result(proc.returncode, proc.stdout + proc.stderr)


class ExactMainAppGuardRunner(RealGitRunner):
    """Use real local Git state for the app's unchanged exact-main guard."""

    def __init__(self, **kw):
        self.on_build = kw.pop("on_build", None)
        super().__init__("", **kw)
        self.guard_observations = []
        self.live = kw.get("live")

    def run(self, argv, *, cwd, log, env, timeout=3600):
        if argv[:2] == ["node", "scripts/release-production.mjs"]:
            head = git(Path(cwd), "rev-parse", "HEAD")
            tracking = git(Path(cwd), "rev-parse", "refs/remotes/origin/main")
            self.guard_observations.append((head, tracking))
            if head != tracking:
                self.calls.append((log.stem.split("-", 1)[1], list(argv)))
                return rp.Result(1, "release guard: HEAD must equal origin/main")
            if isinstance(self.live, dict):
                self.live["source_commit"] = head
                self.live["environment"] = "production"
            return super().run(argv, cwd=cwd, log=log, env=env, timeout=timeout)
        result = super().run(argv, cwd=cwd, log=log, env=env, timeout=timeout)
        if log.stem.endswith("app-build") and self.on_build is not None:
            self.on_build()
        return result


class SchemaFollowupBase(Base):
    def test_schema_followup_branches_from_observed_main_not_the_stale_shared_ref(self):
        stale = self.fx.commit({"db/schema.sql": "create table a(id int);\n"})
        head = self.fx.commit({"db/schema.sql": "create table a (id int);\n", "mcp-server/src/a.js": "1"})
        git(self.fx.repo, "update-ref", "refs/remotes/origin/main", stale)
        live = {"sha": self.fx.base}
        runner = RealGitRunner("create table a(id int);\n", pending=1, live=live)
        pipe = self.fx.pipeline(runner, live=live)
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertIn("schema-commit", runner.names())
        branch = f"{rp.SCHEMA_SNAPSHOT_PREFIX}{head[:8]}"
        self.assertEqual(git(self.fx.repo, "rev-parse", f"{branch}^"), head)
        self.assertEqual(git(self.fx.repo, "rev-parse", "origin/main"), stale)  # shared ref untouched
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], head)

    def _released_with_prod_dump(self, shadow_schema):
        """A release whose production dump is formatted differently from main's
        snapshot (what bin/migrate-prod.sh wrote), with a chosen shadow result."""
        head = self.fx.commit({"db/schema.sql": "alter table a add check ((x > 0));\n", "mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = RealGitRunner("alter table a add check (x > 0);\n", shadow_schema, pending=1, live=live)
        pipe = self.fx.pipeline(runner, live=live)
        self.assertEqual(pipe.tick(["worker"]), 0)
        return head, runner, pipe

    def test_snapshot_pr_is_regenerated_by_the_ci_gate_not_copied_from_the_production_dump(self):
        head, runner, _ = self._released_with_prod_dump("alter table a add check ((x > 0)) not valid;\n")
        shadow = [argv for name, argv in runner.calls if name == "schema-shadow"]
        self.assertEqual(len(shadow), 1)
        self.assertEqual(shadow[0][1:], ["ops/migration-shadow.py", "--write", "--base", head])
        branch = f"{rp.SCHEMA_SNAPSHOT_PREFIX}{head[:8]}"
        self.assertEqual(git(self.fx.repo, "show", f"{branch}:db/schema.sql").strip(),
                         "alter table a add check ((x > 0)) not valid;")

    def test_no_snapshot_pr_when_the_regenerated_file_equals_main(self):
        # Production dumps `check (x > 0)`, main carries `check ((x > 0))` and the
        # PostgreSQL 18 shadow reproduces main's text: nothing for a PR to say.
        head, runner, pipe = self._released_with_prod_dump("alter table a add check ((x > 0));\n")
        for step in ("schema-shadow", "schema-diff", "schema-worktree-remove", "schema-branch-remove"):
            self.assertIn(step, runner.names())
        for step in ("schema-commit", "schema-push", "schema-pr"):
            self.assertNotIn(step, runner.names())
        branch = f"{rp.SCHEMA_SNAPSHOT_PREFIX}{head[:8]}"
        self.assertEqual(git(self.fx.repo, "branch", "--list", branch), "")
        self.assertFalse((pipe.store.root / "worktrees" / f"schema-{head[:12]}").exists())


class SchemaSnapshotSupersede(Base):
    """Every production release that applied migrations opens a cumulative
    `release/schema-snapshot-*` PR. Nothing merges them automatically, so the
    newest must close every older open one (close only: never merge, never
    label, never delete a branch), and a close failure must never fail the
    already-shipped release."""

    SHA = "abcdef0123456789abcdef0123456789abcdef01"
    NEW = 120
    OLDER = {101: "release/schema-snapshot-11111111", 108: "release/schema-snapshot-22222222"}
    OTHER = {110: "feature/unrelated", 111: "release/other-thing", 112: "schema-snapshot-lookalike"}

    def _followup(self, runner):
        pipe = self.fx.pipeline(runner)
        wt = self.fx.tmp / "release-wt"
        (wt / "db").mkdir(parents=True)
        (wt / "db" / "schema.sql").write_text("-- snapshot\n")
        (pipe.store.root / "worktrees" / f"schema-{self.SHA[:12]}" / "db").mkdir(parents=True)
        return pipe, pipe.schema_followup(wt, self.SHA, self.SHA)

    def test_older_snapshots_close_new_stays_open_others_untouched(self):
        runner = SchemaSnapshotGhRunner({**self.OLDER, **self.OTHER}, self.NEW)
        pipe, url = self._followup(runner)
        self.assertTrue(url.endswith(f"/pull/{self.NEW}"))
        self.assertEqual(sorted(n for n, _ in runner.closed), [101, 108])
        self.assertEqual(pipe.schema_superseded_closed, [101, 108])
        for _, comment in runner.closed:
            self.assertEqual(comment, f"Superseded by #{self.NEW}, which carries the cumulative "
                                      "production schema snapshot.")
        # the new PR stays open and every non-snapshot PR is never touched
        self.assertIn(self.NEW, runner.open_prs)
        closes = [a for n, a in runner.calls if n == "gh-pr-close"]
        touched = {int(a[3]) for a in closes}
        self.assertNotIn(self.NEW, touched)
        self.assertFalse(touched & set(self.OTHER))
        # close only: no merge, no label, no branch deletion, and only after
        # the new PR was created
        flat = [" ".join(a) for _, a in runner.calls]
        for banned in ("pr merge", "--add-label", "carr-automerge-pilot", "--delete-branch",
                       "push origin --delete", "branch -D"):
            self.assertFalse(any(banned in c for c in flat), banned)
        names = runner.names()
        self.assertLess(names.index("schema-pr"), names.index("gh-pr-list"))
        self.assertLess(max(i for i, n in enumerate(names) if n == "gh-pr-close"),
                        names.index("schema-worktree-remove"))

    def test_a_close_failure_is_logged_and_the_step_still_succeeds(self):
        runner = SchemaSnapshotGhRunner({**self.OLDER, 105: "release/schema-snapshot-33333333"}, self.NEW,
                                        close_rc={101: 1}, close_raises={105})
        pipe, url = self._followup(runner)   # must not raise
        self.assertTrue(url.endswith(f"/pull/{self.NEW}"))
        self.assertEqual(pipe.schema_superseded_closed, [108])
        self.assertIn("schema-worktree-remove", runner.names())

    def test_a_failed_listing_closes_nothing_and_does_not_fail(self):
        runner = SchemaSnapshotGhRunner(self.OLDER, self.NEW, list_rc=1)
        pipe, _ = self._followup(runner)
        self.assertEqual(pipe.schema_superseded_closed, [])
        self.assertNotIn("gh-pr-close", runner.names())

    def test_a_fork_pr_with_the_snapshot_branch_name_stays_open(self):
        # The repo is public: a fork may name its head release/schema-snapshot-*.
        runner = SchemaSnapshotGhRunner({**self.OLDER, 130: "release/schema-snapshot-99999999"}, self.NEW,
                                        forks={130})
        pipe, _ = self._followup(runner)
        self.assertEqual(pipe.schema_superseded_closed, [101, 108])
        self.assertIn(130, runner.open_prs)
        self.assertNotIn(130, {int(a[3]) for n, a in runner.calls if n == "gh-pr-close"})

    def test_a_snapshot_pr_without_a_readable_fork_flag_stays_open(self):
        # Fail closed: only an explicit isCrossRepository=false is closeable.
        runner = SchemaSnapshotGhRunner({**self.OLDER, 131: "release/schema-snapshot-88888888"}, self.NEW,
                                        no_fork_flag={131})
        pipe, _ = self._followup(runner)
        self.assertEqual(pipe.schema_superseded_closed, [101, 108])
        self.assertIn(131, runner.open_prs)

    def test_unreadable_pr_create_output_closes_nothing(self):
        for out in ("", "created, but no URL here\n", "https://example.invalid/o/r/pull/abc\n"):
            with self.subTest(out=out):
                runner = SchemaSnapshotGhRunner(self.OLDER, self.NEW, pr_create_out=out)
                pipe = self.fx.pipeline(runner)
                wt = self.fx.tmp / f"release-wt-{abs(hash(out))}"
                (wt / "db").mkdir(parents=True)
                (wt / "db" / "schema.sql").write_text("-- snapshot\n")
                fwt = pipe.store.root / "worktrees" / f"schema-{self.SHA[:12]}"
                (fwt / "db").mkdir(parents=True, exist_ok=True)
                pipe.schema_followup(wt, self.SHA, self.SHA)   # must not raise
                self.assertEqual(pipe.schema_superseded_closed, [])
                self.assertNotIn("gh-pr-close", runner.names())
                self.assertEqual(set(runner.open_prs), set(self.OLDER) | {self.NEW})

    def _direct(self, open_prs):
        """close_superseded_schema_prs on its own, with a fixed PR list, so a
        row can share ONE identity field with the new PR but not the other."""
        runner = SchemaSnapshotGhRunner(open_prs, self.NEW)
        pipe = self.fx.pipeline(runner)
        new_branch = f"{rp.SCHEMA_SNAPSHOT_PREFIX}{self.SHA[:8]}"
        closed = pipe.close_superseded_schema_prs(self.fx.tmp, f"https://example.invalid/o/r/pull/{self.NEW}",
                                                  new_branch)
        return runner, closed, new_branch

    def test_the_branch_exclusion_alone_protects_the_new_pr(self):
        # Kills the mutant that drops only `headRefName != new_branch`: a row
        # on the new branch under a DIFFERENT number must stay open.
        new_branch = f"{rp.SCHEMA_SNAPSHOT_PREFIX}{self.SHA[:8]}"
        runner, closed, _ = self._direct({**self.OLDER, self.NEW + 1: new_branch})
        self.assertEqual(closed, [101, 108])
        self.assertIn(self.NEW + 1, runner.open_prs)

    def test_the_number_exclusion_alone_protects_the_new_pr(self):
        # Kills the mutant that drops only `number != new_num`: the new PR's
        # NUMBER under a different snapshot branch name must stay open.
        runner, closed, _ = self._direct({**self.OLDER, self.NEW: "release/schema-snapshot-deadbeef"})
        self.assertEqual(closed, [101, 108])
        self.assertIn(self.NEW, runner.open_prs)

    def test_a_failed_pr_create_closes_nothing(self):
        runner = SchemaSnapshotGhRunner(self.OLDER, self.NEW)
        runner.fail_at = "schema-pr"
        with self.assertRaises(rp.StepFailed):
            self._followup(runner)
        self.assertNotIn("gh-pr-list", runner.names())
        self.assertEqual(runner.closed, [])


class GitHubDiagnosticPersistence(Base):
    def test_fine_grained_tokens_never_reach_console_or_blocked_records(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        pipe = self.fx.pipeline(FakeRunner())
        pipe.github_factory = lambda repo: rp.GitHub(repo, {})
        output = []
        pipe.out = output.append
        token = "github" + "_pat_" + ("A" * 82)
        real_run = rp.subprocess.run

        def run(argv, **kwargs):
            if argv[0] == "gh":
                return subprocess.CompletedProcess(argv, 1, "", f"Authorization: token {token}\nHTTP 401\n")
            return real_run(argv, **kwargs)

        with mock.patch.object(rp.subprocess, "run", side_effect=run), \
                mock.patch.object(rp.time, "sleep"):
            self.assertEqual(pipe.tick(["worker"]), 0)
        record = self.fx.records()[-1]
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["reason"], "github_unreadable")
        for diagnostic in ("\n".join(output), json.dumps(record)):
            self.assertNotIn(token, diagnostic)
            self.assertIn("[REDACTED]", diagnostic)
            self.assertIn("HTTP 401", diagnostic)


class TokenCheckWrangler(Base):
    """wrangler-auth runs the pipeline's own wrangler install, never the
    pipeline checkout's mcp-server/node_modules (emptied 2026-10-05, which
    made `wrangler whoami` exit 127 on both lanes)."""

    def toolchain(self) -> Path:
        return self.fx.repo / "out/release-pipeline/toolchain"

    def test_whoami_uses_the_installed_toolchain_not_the_checkout(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(runner.names()[0], "wrangler-auth")
        self.assertNotIn("wrangler-install", runner.names())
        whoami = dict(runner.calls)["wrangler-auth"]
        self.assertEqual(whoami, [str(self.fx.wrangler), "whoami"])
        self.assertEqual(runner.cwds["wrangler-auth"], str(self.fx.repo / "mcp-server"))

    def test_missing_toolchain_is_installed_before_whoami_and_kept(self):
        shutil.rmtree(self.toolchain())
        stale = self.toolchain() / "mcp-server-0000000000000000"
        stale.mkdir(parents=True)
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = InstallingRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        self.assertEqual(runner.names()[:3], ["wrangler-install", "wrangler-auth", "worktree"])
        install = dict(runner.calls)["wrangler-install"]
        self.assertEqual(install, ["npm", "ci", "--no-audit", "--no-fund"])
        home = self.fx.wrangler.parents[2]
        self.assertEqual(runner.cwds["wrangler-install"], str(home) + ".partial")
        self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs["wrangler-install"])
        self.assertEqual(dict(runner.calls)["wrangler-auth"][0], str(self.fx.wrangler))
        self.assertTrue(self.fx.wrangler.exists())
        self.assertEqual(sorted(p.name for p in self.toolchain().iterdir()), [home.name])
        for name in rp.TOOLCHAIN_FILES:
            self.assertEqual((home / name).read_bytes(), (self.fx.repo / "mcp-server" / name).read_bytes())

        # The next tick reuses the install.
        self.fx.commit({"mcp-server/src/a.js": "2"})
        again = InstallingRunner(live=live)
        self.assertEqual(self.fx.pipeline(again, live=live).tick(["worker"]), 0)
        self.assertNotIn("wrangler-install", again.names())

    def test_lockfile_change_selects_a_new_install(self):
        old = self.fx.wrangler
        (self.fx.repo / "mcp-server/package-lock.json").write_text('{"lockfileVersion": 3, "x": 1}\n')
        new = rp.wrangler_toolchain(self.fx.repo, self.fx.repo / "out/release-pipeline")
        self.assertNotEqual(new, old.parents[2])

    def test_install_without_a_binary_fails_at_wrangler_install(self):
        shutil.rmtree(self.toolchain())
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertEqual(runner.names(), ["wrangler-install"])
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"], rec["sha"]), ("failed", "wrangler-install", sha))
        self.assertFalse(self.fx.wrangler.parents[2].exists())

    def test_failed_npm_ci_stops_before_whoami(self):
        shutil.rmtree(self.toolchain())
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = InstallingRunner(live=live, fail_at="wrangler-install")
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertEqual(runner.names(), ["wrangler-install"])
        self.assertEqual(self.fx.records()[-1]["step"], "wrangler-install")

    def test_dry_run_installs_nothing(self):
        shutil.rmtree(self.toolchain())
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.fx.pipeline(runner, live=live, dry_run=True).tick(["worker"])
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.toolchain().exists())


class Report(Base):
    def test_report_lists_what_shipped(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        self.fx.pipeline(FakeRunner(live=live), live=live).tick(["worker"])
        store = rp.Store(self.fx.repo / "out/release-pipeline")
        day = self.fx.records()[-1]["ts"][:10]
        self.assertIn("SHIPPED worker", rp.report(store, day))


if __name__ == "__main__":
    unittest.main(verbosity=2)
