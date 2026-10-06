"""Deterministic evidence classification and local job/watchdog ledgers."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parents[1]
# Each evidence source and the finding kinds detect derives from it. detect refuses
# a kind its source does not declare, and an unreadable source blinds exactly these.
EVIDENCE = {
    "scheduled_jobs": frozenset({"scheduled_job_drift"}),
    "jobs": frozenset({"job_failed", "job_dead", "job_hang", "job_silent", "job_over_limit"}),
    "prs": frozenset({"pr_blocked_review", "pr_ci_red", "pr_conflict", "pr_draft_idle", "pr_ready"}),
    "merge_queue": frozenset({"pr_ready"}),
    "queue_log": frozenset({"queue_error"}),
    "release_log": frozenset({"pipeline_blocked", "pipeline_stale"}),
    "branches": frozenset({"branch_idle"}),
}
# An unreadable evidence source cannot prove the findings it feeds have cleared.
EVIDENCE_ERROR_KINDS = frozenset({"collection_error", "environment", "rate_limited"})
RATE_LIMIT = re.compile(r"API rate limit|secondary rate limit", re.I)
RELEASE_LANE = re.compile(r"^release-pipeline\[([^\]]+)\]: (.*)$")
# Lines that end a lane's tick (ops/release-pipeline.py run_lane). Anything else the
# lane prints, such as a failed loop filing after BLOCKED, never replaces its outcome.
RELEASE_OUTCOME = re.compile(
    r"SHIPPED |BLOCKED |FAILED at |UNEXPECTED |doc/test-only batch"
    r"|main \w+ is already released|\w+ failed at .*; waiting for a fix-forward merge"
    r"|target \w+ is at or before the failed .*; waiting for a green fix-forward"
    r"|disabled |lane \S+ disabled ")


class MissingTool(RuntimeError):
    def __init__(self, tool):
        super().__init__(f"required tool {tool!r} is not on PATH {os.environ.get('PATH', '')!r}")
        self.tool = tool


def load_config(path):
    config = json.loads(Path(path).read_text())
    if config["schema_version"] != 1:
        raise ValueError("unsupported watchdog config")
    for key, value in config["thresholds"].items():
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"invalid threshold {key}")
    for action in config["actions"].values():
        if isinstance(action, str) and action not in {"restart_once", "fix_once", "enqueue", "report"}:
            raise ValueError("unknown watchdog action")
    return config


def repository_roots(config=None):
    config = config or load_config(SOURCE / "ops/config/job-watchdog.json")
    return {repo: Path(config["repository_roots"][repo]).expanduser() for repo in config["repositories"]}


def protected_branch(config, name):
    return name in config["protected_branches"]


def epoch(value):
    if isinstance(value, (float, int)):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def stamp(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).isoformat()


def finding(kind, subject, reason, config, **fields):
    key = f"{kind}:{subject}"
    needs_joe = next((k for k, patterns in config["needs_joe_patterns"].items()
                      if any(p in reason.lower() for p in patterns)), None)
    return {"key": key, "kind": kind, "subject": subject, "reason": reason,
            "next_action": config["next_actions"][kind], "owner": "orchestrator", "needs_joe": needs_joe, **fields}


def reviewed_head(comment):
    body = comment.get("body", "")
    match = re.search(r"(?mi)^Reviewed-SHA:\s*([0-9a-f]{7,40})\s*$", body)
    return match.group(1) if match else (comment.get("commit") or {}).get("oid")


def current_check_attempts(checks):
    """Resolve attempts per provider/workflow/context; retain ambiguous evidence."""
    groups = {}
    for index, check in enumerate(checks):
        kind = check.get("__typename") or ("StatusContext" if "state" in check else "CheckRun")
        suite = check.get("checkSuite") or {}
        workflow = ((suite.get("workflowRun") or {}).get("workflow") or {})
        url = check.get("detailsUrl") or check.get("targetUrl") or ""
        parsed = urlsplit(url)
        provider = ((suite.get("app") or {}).get("id") or check.get("provider") or
                    (check.get("creator") or {}).get("login") or parsed.netloc)
        context = check.get("name") if kind == "CheckRun" else check.get("context")
        identity = (kind, provider, workflow.get("id") or check.get("workflowName"), context)
        # Missing identity cannot prove that one check supersedes another.
        key = identity if provider and context else ("unidentified", index)
        groups.setdefault(key, []).append(check)
    current = []
    for attempts in groups.values():
        def run_id(check):
            value = check.get("databaseId")
            if isinstance(value, int) and value > 0:
                return value
            match = re.search(r"/actions/runs/\d+/(?:job|jobs)/(\d+)(?:[/?#]|$)", check.get("detailsUrl") or "")
            return int(match.group(1)) if match else None

        ids = [run_id(c) for c in attempts]
        if all(value is not None for value in ids):
            ranks = ids
        else:
            try:
                ranks = [epoch(c.get("startedAt") or c.get("createdAt")) for c in attempts]
            except (AttributeError, TypeError, ValueError):
                # No trustworthy ordering: every attempt must pass.
                current.extend(attempts)
                continue
        latest = max(ranks)
        current.extend(c for c, rank in zip(attempts, ranks) if rank == latest)
    return current


def green(checks):
    return _green_current(current_check_attempts(checks))


def _green_current(checks):
    if not checks:
        return False
    return all((c.get("conclusion") in {"SUCCESS", "SKIPPED", "NEUTRAL"}
                and c.get("status", "COMPLETED") == "COMPLETED") or
               c.get("state") in {"SUCCESS", "SKIPPED", "NEUTRAL"} for c in checks)


def detect(facts, config, now):
    """Classify an immutable snapshot; no I/O, models, or effects."""
    found = []
    def emit(source, kind, subject, reason, **fields):
        if kind not in EVIDENCE[source]:
            raise ValueError(f"{kind} is not declared as derived from {source} in EVIDENCE")
        found.append(finding(kind, subject, reason, config, **fields))
    t = config["thresholds"]
    for row in facts.get("scheduled_jobs", []):
        emit("scheduled_jobs", "scheduled_job_drift", row["label"], row["detail"],
             key=row["key"], next_action=row["fix"] +
             "; verify python3 ops/scheduled-jobs-check.py; auto-clear on next complete scan without this finding")
    jobs = facts.get("jobs", [])
    for job in jobs:
        subject = job["id"]
        fields = {"job": job, "card": job.get("card", subject)}
        if "exit_code" in job:
            if job["exit_code"] != 0 and not job.get("superseded"):
                emit("jobs", "job_failed", subject, f"exit {job['exit_code']}: {job.get('log_tail', '')}", **fields)
            continue
        if not job.get("alive"):
            emit("jobs", "job_dead", subject, "registered PID is dead or changed without an exit record", **fields)
            continue
        tail = job.get("log_tail", "")
        evidence = "\nLog evidence: " + tail if tail else ""
        if any(re.search(p, tail) for p in config["hang_patterns"]):
            emit("jobs", "job_hang", subject, "interactive hang signature in log tail" + evidence, **fields)
        elif config.get("job_silence_policies", {}).get(job.get("card")) != "until_run_limit" and \
                now - epoch(job.get("log_mtime", job["start"])) >= t["silent_seconds"]:
            emit("jobs", "job_silent", subject, "log silent for at least the configured limit" + evidence, **fields)
        if now - epoch(job["start"]) >= job["limit"]:
            emit("jobs", "job_over_limit", subject, "registered run exceeded its time limit" + evidence, **fields)
    queue = {tuple(line.split()[:3]) for line in facts.get("queue", "").splitlines() if len(line.split()) >= 3}
    for pr in facts.get("prs", []):
        repo, number, head = pr["repo"], pr["number"], pr["headRefOid"]
        subject = f"{repo}#{number}@{head}"
        fields = {"repo": repo, "pr": number, "head": head,
                  "card": pr.get("card", f"pr-{repo.split('/')[-1]}-{number}")}
        comments = pr.get("comments", []) + pr.get("reviews", [])
        verdicts = []
        for c in comments:
            first = c.get("body", "").splitlines()[:1]
            first = first[0].strip() if first else ""
            if reviewed_head(c) != head:
                continue
            state = c.get("state")
            if first in {"REVIEW: BLOCKED", "CHANGES REQUESTED", "APPROVE", "REVIEW: APPROVED"} or state in {"APPROVED", "CHANGES_REQUESTED"}:
                verdicts.append((epoch(c.get("submittedAt") or c.get("createdAt") or pr["updatedAt"]), c,
                                 first in {"REVIEW: BLOCKED", "CHANGES REQUESTED"} or state == "CHANGES_REQUESTED"))
        latest = max(verdicts, key=lambda v: v[0]) if verdicts else None
        commits = pr.get("commits", [])
        head_time = max((epoch(c["committedDate"]) for c in commits), default=epoch(pr["updatedAt"]))
        active_fixer = any(j.get("pr") == number and j.get("repo") == repo and j.get("head") == head
                           and "exit_code" not in j and j.get("alive") for j in jobs)
        if latest and latest[2] and now - max(head_time, latest[0]) >= t["review_idle_seconds"] and not active_fixer:
            emit("prs", "pr_blocked_review", subject, latest[1].get("body", "CHANGES REQUESTED"), **fields)
        checks = current_check_attempts(pr.get("statusCheckRollup") or [])
        if any(c.get("conclusion") in {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
               or c.get("state") in {"FAILURE", "ERROR"} for c in checks):
            emit("prs", "pr_ci_red", subject, "hosted CI failed on current head", **fields)
        if pr.get("mergeable") == "CONFLICTING" or pr.get("mergeStateStatus") == "DIRTY":
            emit("prs", "pr_conflict", subject, "current head has merge conflicts", **fields)
        if pr.get("isDraft") and now - epoch(pr["updatedAt"]) >= t["draft_idle_seconds"]:
            emit("prs", "pr_draft_idle", subject, "draft idle for configured limit", **fields)
        if latest and not latest[2] and _green_current(checks) and not pr.get("isDraft") and pr.get("mergeable") == "MERGEABLE" and (repo, str(number), head) not in queue and not pr.get("mergeQueueEntry"):
            emit("prs", "pr_ready", subject, "approved current head with green CI outside merge queue", **fields)
    for log in facts.get("logs", []):
        source = log["type"] + "_log"
        if log["type"] == "queue":
            kind, lines = "queue_error", [line for line in log.get("tail", "").splitlines()
                                          if any(p.lower() in line.lower() for p in config["queue_error_patterns"])]
        else:
            kind, lines = "pipeline_blocked", [line for line in release_outcomes(log.get("tail", "")).values()
                                               if line.split(": ", 1)[1].startswith("BLOCKED")]
        # Line digest identifies a durable failure, rather than rediscovering it each interval.
        for line in lines:
            emit(source, kind, log["path"] + ":" + hashlib.sha256(line.encode()).hexdigest()[:16], line)
        if log["type"] == "release" and now - epoch(log["mtime"]) >= t["pipeline_stale_seconds"]:
            emit(source, "pipeline_stale", log["path"], "release log stopped updating")
    for branch in facts.get("branches", []):
        if not protected_branch(config, branch["name"]) and not branch.get("open_pr") and \
                now - epoch(branch["updated"]) >= t["branch_idle_seconds"]:
            emit("branches", "branch_idle", branch["repo"] + ":" + branch["name"], "branch idle for configured limit")
    for error in facts.get("errors", []):
        found.append(finding(error["kind"], error["source"], error["reason"], config, blinds=error["blinds"]))
    for f in found:
        text = f["reason"].lower()
        f["needs_joe"] = next((k for k, patterns in config["needs_joe_patterns"].items()
                               if any(p in text for p in patterns)), None)
    return found


def release_outcomes(text):
    """Each lane's latest tick-ending line; a later SHIPPED or FAILED supersedes BLOCKED."""
    outcomes = {}
    for line in text.splitlines():
        match = RELEASE_LANE.match(line)
        if match and RELEASE_OUTCOME.match(match.group(2)):
            outcomes[match.group(1)] = line
    return outcomes


def path_at(root, configured):
    path = Path(configured).expanduser()
    return path if path.is_absolute() else root / path


@contextlib.contextmanager
def locked(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield handle


def append(path, row):
    with locked(path) as handle:
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_latest(path):
    if not path.exists():
        return {}
    with path.open() as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        rows = {}
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("key", row.get("id"))
            rows[key] = {**rows.get(key, {}), **row}
        return rows


def tail(path, limit):
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - limit))
        return handle.read().decode("utf-8", errors="replace")


def command(argv, config, cwd=None):
    try:
        result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=config["thresholds"]["command_timeout_seconds"])
    except FileNotFoundError as exc:
        if cwd is not None and not Path(cwd).is_dir():
            raise
        raise MissingTool(argv[0]) from exc
    if result.returncode:
        raise RuntimeError(f"{argv[0]} exit {result.returncode}: {result.stderr.strip()} {result.stdout.strip()}")
    return result.stdout


def process_identity(pid, config=None):
    if not isinstance(pid, int) or pid <= 1:
        return None
    config = config or load_config(SOURCE / "ops/config/job-watchdog.json")
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "pgid="],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=config["thresholds"]["process_probe_seconds"])
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def process_group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM does not prove absence. Keep polling through group teardown;
        # an actual TERM/KILL denial still refuses recovery before relaunch.
        return True


def board_task(root, config, card, executor, status, note, project=None, pr=None, repo=None, needs_joe=False, health=None,
               expected_task=None, reason=None, next_action=None):
    project = project or config["board"]
    board = root / "out/boards" / (project + ".json")
    env = dict(os.environ, PROGRESS_BOARD_ROOT=str(root / "out"))
    with locked(root / "out/watchdog/board.lock"):
        if not board.exists():
            result = subprocess.run([sys.executable, str(SOURCE / "tools/progress_board.py"), "init",
                                     project, "--title", "Agent jobs"],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=config["thresholds"]["command_timeout_seconds"])
            if result.returncode:
                raise RuntimeError(result.stderr)
        prior = json.loads(board.read_text()).get("tasks", {}).get(card, {})
        argv = [sys.executable, str(SOURCE / "tools/progress_board.py"), "task", project, card,
                "--title", prior.get("title", card), "--executor", prior.get("executor", executor) if executor == "orchestrator" else executor, "--status", status,
                "--health", health or ("blocked" if status == "blocked" else "healthy"), "--note", note]
        argv.extend(["--lane", config["needs_joe_lane"] if needs_joe else "status"])
        if expected_task is not None:
            argv.extend(["--expected-task", json.dumps(expected_task)])
        if status == "blocked":
            argv.extend(["--reason", reason or note,
                         "--next-action", next_action or config["next_actions"]["job_failed"]])
        if pr is not None:
            argv.extend(["--pr", str(pr), "--repo", repo])
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=config["thresholds"]["command_timeout_seconds"])
        if result.returncode:
            raise RuntimeError(result.stderr)
        return prior


def run_job(root, config, card, executor, minutes, argv):
    if minutes <= 0 or not argv or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", card):
        raise ValueError("positive minutes, safe card id, and command required")
    job_id = os.environ.get("CARR_JOB_ID") or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job_id):
        raise ValueError("unsafe job id")
    registry = path_at(root, config["paths"]["registry"])
    if job_id in read_latest(registry):
        raise ValueError("job id already registered; refusing duplicate execution")
    log = path_at(root, config["paths"]["job_logs"]) / (job_id + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    board_task(root, config, card, executor, "running", "Job starting; log " + str(log))
    started = time.time()
    proc = None
    row = {"id": job_id, "card": card, "executor": executor, "start": started,
           "limit": minutes * 60, "log_path": str(log), "cwd": str(Path.cwd()),
           "command": argv, "root_id": os.environ.get("CARR_JOB_ROOT_ID", job_id),
           "restart_count": int(os.environ.get("CARR_JOB_RESTART_COUNT", "0")),
           "wrapper_pid": os.getpid(), "board": config["board"]}
    for key in ("repo", "pr", "head"):
        value = os.environ.get("CARR_JOB_" + key.upper())
        if value:
            row[key] = int(value) if key == "pr" else value
    try:
        with log.open("wb") as output:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            row.update(pid=proc.pid, pgid=proc.pid, process_identity=process_identity(proc.pid, config))
            append(registry, row)
            def stop(signum, frame):
                if proc.poll() is None:
                    os.killpg(proc.pid, signum)
            previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                code = proc.wait()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    except OSError as exc:
        code = 127
        row.update(pid=None, process_identity=None)
        append(registry, row)
        with log.open("ab") as output:
            output.write(str(exc).encode())
    except BaseException:
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        raise
    code = 128 - code if code < 0 else code
    end = {"id": job_id, "exit_code": code, "ended_at": stamp(),
           "log_tail": tail(log, config["thresholds"]["log_tail_bytes"])}
    append(registry, end)
    board_task(root, config, card, executor, "done" if code == 0 else "blocked",
               f"Exit {code}; log {log}; next: " + ("orchestrator verify result" if code == 0 else config["next_actions"]["job_failed"]))
    print(json.dumps({**row, **end}))
    return code


def reconcile(root, config, found, effects, now, complete=True):
    """Called under the scan lock. Persist intent before each non-repeatable effect."""
    findings_path = path_at(root, config["paths"]["findings"])
    actions_path = path_at(root, config["paths"]["actions"])
    previous = read_latest(findings_path)
    actions = read_latest(actions_path)
    current = {f["key"]: f for f in found}
    extras = []
    for key, f in current.items():
        prior = previous.get(key, {})
        first_seen = prior.get("first_seen", stamp(now))
        if f["kind"] == "scheduled_job_drift" and prior.get("cleared_at"):
            first_seen = stamp(now)
        row = {**f, "first_seen": first_seen, "cleared_at": None}
        if prior.get("cleared_at"):
            row["board_recovery"] = None
        if not prior or prior.get("cleared_at") or prior.get("reason") != f["reason"]:
            append(findings_path, row)
        # Failed reporting is retried with the SAME record-layer idempotency key.
        if not prior.get("reported") or prior.get("cleared_at"):
            try:
                row.update(effects.report(row) or {})
                row["reported"] = True
                append(findings_path, row)
            except Exception as exc:
                extras.append(finding("record_error", key, str(exc), config))
        action = config["actions"].get(f["kind"], config["actions"]["default"])
        if f.get("needs_joe") or action == "report":
            continue
        action_key = key
        if action == "restart_once":
            job = f["job"]
            action_key = "restart:" + job.get("root_id", job["id"])
            if job.get("restart_count", 0) >= config["thresholds"]["restart_limit"]:
                continue
        existing = actions.get(action_key)
        if existing:
            if existing["status"] != "done":
                extras.append(finding("action_error", action_key, "prior action intent is unresolved; inspect before retry", config))
            continue
        # A cached finding is only a candidate. A refused fresh predicate has
        # executed no effect and must not consume the once-only action slot.
        try:
            if not effects.prepare(action, f):
                continue
        except Exception as exc:
            extras.append(finding("action_error", action_key, str(exc), config))
            continue
        intent = {"key": action_key, "action": action, "status": "intent", "at": stamp(now)}
        append(actions_path, intent)
        actions[action_key] = intent
        try:
            result = effects.act(action, f)
            append(actions_path, {**intent, "status": "done", "result": result})
        except Exception as exc:
            append(actions_path, {**intent, "status": "failed", "error": str(exc)})
            extras.append(finding("action_error", action_key, str(exc), config))
    for f in extras:
        current[f["key"]] = f
        prior = previous.get(f["key"], {})
        row = {**f, "first_seen": prior.get("first_seen", stamp(now)), "cleared_at": None}
        if not prior or prior.get("cleared_at") or prior.get("reason") != f["reason"]:
            append(findings_path, row)
        if not prior.get("reported") or prior.get("cleared_at"):
            try:
                row.update(effects.report(row) or {})
                append(findings_path, {**row, "reported": True})
            except Exception:
                pass  # Original failure remains durable and visible; no recursive record writes.
    # Evidence-source failures cannot prove an old condition has cleared.
    blinded = set()
    for f in found:
        if f["kind"] in EVIDENCE_ERROR_KINDS:
            blinded |= set(f["blinds"])
    if complete:
        for key, prior in previous.items():
            if key not in current and not prior.get("cleared_at") and prior.get("kind") not in blinded:
                if prior.get("board_recovery"):
                    try:
                        effects.clear(prior, list(current.values()))
                    except Exception as exc:
                        error = finding("board_error", key, str(exc), config)
                        current[error["key"]] = error
                        append(findings_path, {**error, "first_seen": stamp(now), "cleared_at": None})
                        continue  # Keep the original open so board recovery is retried.
                append(findings_path, {**prior, "cleared_at": stamp(now)})
    return list(current.values())


PR_FIELDS = "number,headRefOid,headRefName,updatedAt,isDraft,mergeable,comments,reviews,commits,mergeStateStatus"


def collect_pr(repo, number, config):
    pr = json.loads(command(["gh", "pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS], config))
    pr["repo"] = repo
    # gh's exporter omits check providers and workflow IDs. Read those bound to
    # the same head, so equal names cannot collide. The PR snapshot cache keeps
    # this second query to changed or stale PRs. Only a repository that uses
    # GitHub's merge queue can have a queue entry, so others skip that field.
    queue_field = "mergeQueueEntry{id}" if repo in config.get("github_merge_queue_repositories", []) else ""
    owner, name = repo.split("/")
    query = """query($owner:String!,$name:String!,$number:Int!){
      repository(owner:$owner,name:$name){pullRequest(number:$number){
        """ + queue_field + """
        commits(last:1){nodes{commit{oid statusCheckRollup{contexts(first:100){
          pageInfo{hasNextPage}
          nodes{__typename
            ... on CheckRun{databaseId name status conclusion startedAt completedAt detailsUrl
              checkSuite{app{id} workflowRun{workflow{id}}}}
            ... on StatusContext{context state createdAt targetUrl creator{login}}
          }
        }}}}}
      }}
    }"""
    queued = json.loads(command(["gh", "api", "graphql", "-f", "query=" + query,
                                "-f", "owner=" + owner, "-f", "name=" + name,
                                "-F", "number=" + str(number)], config))
    if queued.get("errors"):
        raise RuntimeError(json.dumps(queued["errors"]))
    observed = queued["data"]["repository"]["pullRequest"]
    commit = observed["commits"]["nodes"][0]["commit"]
    if commit["oid"] != pr["headRefOid"]:
        raise RuntimeError("PR head changed while collecting CI evidence")
    contexts = (commit.get("statusCheckRollup") or {}).get("contexts") or {"nodes": []}
    if (contexts.get("pageInfo") or {}).get("hasNextPage"):
        raise RuntimeError("CI evidence exceeds the bounded check collection")
    pr["statusCheckRollup"] = contexts["nodes"]
    pr["mergeQueueEntry"] = observed.get("mergeQueueEntry")
    return pr


def settled(pr):
    """Known mergeability and completed checks permit the longer cache interval."""
    checks = pr.get("statusCheckRollup") or []
    return bool(checks) and pr.get("mergeable") in {"MERGEABLE", "CONFLICTING"} and all(
        (c.get("status", "COMPLETED") == "COMPLETED" and c.get("conclusion") in
         {"SUCCESS", "SKIPPED", "NEUTRAL", "FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"})
        or c.get("state") in {"SUCCESS", "FAILURE", "ERROR"}
        for c in checks)


def read_pr_cache(path):
    """Optimization data is disposable; malformed entries never blind evidence."""
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(cache, dict):
        return {}
    valid = {}
    for key, entry in cache.items():
        try:
            pr = entry["pr"]
            version = entry["version"]
            collected = entry["collected_at"]
            if (not isinstance(pr, dict) or not isinstance(version, list) or len(version) != 2
                    or not isinstance(collected, (int, float)) or isinstance(collected, bool)
                    or not math.isfinite(collected) or collected < 0
                    or not isinstance(pr["number"], int) or isinstance(pr["number"], bool) or pr["number"] <= 0
                    or not isinstance(pr["repo"], str) or key != f"{pr['repo']}#{pr['number']}"
                    or not isinstance(pr["headRefOid"], str) or not re.fullmatch(r"[0-9a-f]{40}", pr["headRefOid"])
                    or not isinstance(pr["updatedAt"], str)
                    or version != [pr["headRefOid"], pr["updatedAt"]]
                    or not isinstance(pr["isDraft"], bool)
                    or pr["mergeable"] not in {"UNKNOWN", "MERGEABLE", "CONFLICTING"}):
                continue
            epoch(pr["updatedAt"])
            for field in ("comments", "reviews", "commits", "statusCheckRollup"):
                if not isinstance(pr[field], list) or not all(isinstance(row, dict) for row in pr[field]):
                    raise ValueError("invalid PR snapshot rows")
            for row in pr["comments"] + pr["reviews"]:
                if (not isinstance(row.get("body", ""), str)
                        or not isinstance(row.get("state", ""), str)):
                    raise ValueError("invalid review body or state")
                epoch(row.get("submittedAt") or row.get("createdAt") or pr["updatedAt"])
                reviewed_head(row)
            for row in pr["commits"]:
                epoch(row["committedDate"])
            for row in pr["statusCheckRollup"]:
                if any(not isinstance(row[field], str) for field in ("status", "conclusion", "state")
                       if field in row and row[field] is not None):
                    raise ValueError("invalid check state")
            valid[key] = entry
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
    return valid


def rate_limit_reason(config, original):
    try:
        resources = json.loads(command(["gh", "api", "rate_limit"], config))["resources"]
        if not isinstance(resources, dict):
            raise ValueError("resources must be a mapping")
        relevant = {k: r for k, r in resources.items() if k in {"core", "graphql"}}
        if not relevant:
            raise ValueError("no core or graphql diagnostic")
        for resource in relevant.values():
            if not isinstance(resource, dict) or any(
                    not isinstance(resource[field], int) or isinstance(resource[field], bool) or resource[field] < 0
                    for field in ("remaining", "used", "limit", "reset")):
                raise ValueError("invalid resource fields")
        spent = {k: r for k, r in relevant.items() if r["remaining"] == 0}
        resets = "; ".join(f"{k} {r['used']}/{r['limit']} resets {stamp(r['reset'])}"
                           for k, r in sorted(spent.items()))
        return str(original) + ("; exhausted allowance: " + resets if resets else "; primary allowances not exhausted")
    except Exception as exc:
        return f"{original}; rate-limit diagnostic unreadable: {exc}"


def collect(root, config, now=None):
    now = time.time() if now is None else now
    facts = {"jobs": [], "prs": [], "logs": [], "branches": [], "errors": [], "queue": ""}
    missing, limited = {}, {}
    def error(subject, exc, evidence):
        blinds = EVIDENCE[evidence]
        if RATE_LIMIT.search(str(exc)):
            # One exhausted allowance is one scan-level finding, not one per PR.
            if not limited:
                limited["github"] = {"kind": "rate_limited", "source": "github",
                                     "reason": rate_limit_reason(config, exc),
                                     "blinds": EVIDENCE["prs"] | EVIDENCE["branches"]}
        elif isinstance(exc, MissingTool):
            # One absent tool is one environment defect, however many sources needed it.
            entry = missing.setdefault(exc.tool, {"kind": "environment", "source": exc.tool,
                                                  "reason": str(exc), "blinds": set()})
            entry["blinds"] |= blinds
        else:
            facts["errors"].append({"kind": "collection_error", "source": subject, "reason": str(exc),
                                    "blinds": sorted(blinds)})
    try:
        jobs = read_latest(path_at(root, config["paths"]["registry"]))
        for job in jobs.values():
            job = dict(job)
            identity = process_identity(job.get("pid"), config)
            job["alive"] = bool(identity and identity == job.get("process_identity"))
            if "exit_code" not in job:
                log = Path(job["log_path"])
                if log.exists():
                    job["log_mtime"] = log.stat().st_mtime
                    job["log_tail"] = tail(log, config["thresholds"]["log_tail_bytes"])
                else:
                    job["log_mtime"] = job["start"]
            facts["jobs"].append(job)
    except Exception as exc:
        error("job registry", exc, "jobs")
    if sys.platform == "darwin":
        try:
            import scheduled_jobs
            facts["scheduled_jobs"] = scheduled_jobs.check(now=now)
            if any(row["code"] == "evidence_unavailable" for row in facts["scheduled_jobs"]):
                error("scheduled job evidence", RuntimeError("incomplete scheduled-job observation"), "scheduled_jobs")
        except Exception as exc:
            error("scheduled jobs", exc, "scheduled_jobs")
    queue = path_at(root, config["paths"]["merge_queue"])
    if queue.exists():
        try:
            facts["queue"] = queue.read_text()
        except OSError as exc:
            error("merge queue", exc, "merge_queue")
    # Each PR's GraphQL snapshot is reused while the REST listing shows the same
    # head and updated_at, bounded by an age limit for changes that bump neither.
    cache_path = path_at(root, config["paths"]["pr_cache"])
    cache = read_pr_cache(cache_path)
    dates_path = root / "out/watchdog/branch-dates.json"
    try:
        dates = json.loads(dates_path.read_text())
        if not isinstance(dates, dict):
            dates = {}
    except (OSError, ValueError):
        dates = {}
    t = config["thresholds"]
    for repo in config["repositories"]:
        if limited:
            facts["prs"].extend(v["pr"] for k, v in cache.items() if k.startswith(repo + "#"))
            continue  # Stop provider reads; all skipped evidence is blinded.
        try:
            pages = json.loads(command(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls?state=open&per_page=100"], config))
            # Closed PRs leave the cache; an unlisted repository keeps its entries.
            listed = {f"{repo}#{pr['number']}" for page in pages for pr in page}
            cache = {k: v for k, v in cache.items() if k in listed or not k.startswith(repo + "#")}
            for page in pages:
                for pr in page:
                    key, version = f"{repo}#{pr['number']}", [pr["head"]["sha"], pr["updated_at"]]
                    entry = cache.get(key)
                    if entry and (limited or (entry["version"] == version and now - entry["collected_at"] <
                                              t["pr_cache_seconds" if settled(entry["pr"]) else "pr_cache_pending_seconds"])):
                        facts["prs"].append(entry["pr"])  # Rate-limited: keep the previous state.
                        continue
                    if limited:
                        continue  # Never collected; the rate_limited finding blinds its kinds.
                    try:
                        cache[key] = {"version": version, "collected_at": now,
                                      "pr": collect_pr(repo, pr["number"], config)}
                        facts["prs"].append(cache[key]["pr"])
                    except Exception as exc:
                        error(f"{repo}#{pr['number']}", exc, "prs")
                        if limited and entry:
                            facts["prs"].append(entry["pr"])
        except Exception as exc:
            error(repo + " PRs", exc, "prs")
        if limited:
            continue
        try:
            pages = json.loads(command(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/branches?per_page=100"], config))
            for page in pages:
                for branch in page:
                    if not protected_branch(config, branch["name"]):
                        sha = branch["commit"]["sha"]
                        key = repo + ":" + sha
                        date = dates.get(key)
                        try:
                            if not isinstance(date, str) or not math.isfinite(epoch(date)):
                                raise ValueError("invalid cached commit date")
                        except (AttributeError, TypeError, ValueError):
                            date = None
                        if date is None:
                            # Commit dates are immutable. Prefer already fetched objects;
                            # still list remote refs every scan so retirement clears facts.
                            try:
                                checkout = Path(config["repository_roots"][repo]).expanduser()
                                date = command(["git", "show", "-s", "--format=%cI", sha], config, checkout).strip()
                                epoch(date)
                            except Exception:
                                commit = json.loads(command(["gh", "api", f"repos/{repo}/commits/{sha}"], config))
                                date = commit["commit"]["committer"]["date"]
                                epoch(date)
                            dates[key] = date
                        facts["branches"].append({"repo": repo, "name": branch["name"],
                                                  "updated": date,
                            "open_pr": any(p["repo"] == repo and p.get("headRefName") == branch["name"]
                                           for p in facts["prs"])})
        except Exception as exc:
            error(repo + " branches", exc, "branches")
    logs = [(p, "queue") for p in config["paths"]["queue_logs"]] + [(config["paths"]["release_log"], "release")]
    for configured, kind in logs:
        log = path_at(root, configured)
        if not log.exists():
            continue  # Optional pipelines that have never run have no stale log.
        try:
            facts["logs"].append({"path": str(log), "type": kind, "mtime": log.stat().st_mtime,
                                  "tail": tail(log, config["thresholds"]["log_tail_bytes"])})
        except OSError as exc:
            error(str(log), exc, kind + "_log")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        staged = cache_path.with_suffix(".tmp")
        staged.write_text(json.dumps(cache, separators=(",", ":")))
        staged.replace(cache_path)
        dates_path.parent.mkdir(parents=True, exist_ok=True)
        staged = dates_path.with_suffix(".tmp")
        staged.write_text(json.dumps(dates, separators=(",", ":")))
        staged.replace(dates_path)
    except OSError:
        pass  # A lost cache only costs the next scan a full collection.
    facts["errors"].extend({**e, "blinds": sorted(e["blinds"])} for e in [*missing.values(), *limited.values()])
    # A successful restarted job supersedes the killed attempt's expected exit.
    recovered = {j.get("root_id") for j in facts["jobs"] if j.get("restart_count", 0) and j.get("exit_code") == 0}
    for job in facts["jobs"]:
        if job["id"] in recovered:
            job["superseded"] = True
    return facts


class Effects:
    def __init__(self, root, config):
        self.root, self.config = root, config
        self.children = []

    def card(self, f):
        return f.get("card") or "wd-" + hashlib.sha256(f["subject"].encode()).hexdigest()[:16]

    def show_finding(self, f, expected_task=None):
        return board_task(self.root, self.config, self.card(f), "orchestrator", "blocked",
                          f["reason"] + "\nNext action: " + f["next_action"],
                          pr=f.get("pr"), repo=f.get("repo"), needs_joe=bool(f.get("needs_joe")),
                          expected_task=expected_task, reason=f["reason"], next_action=f["next_action"])

    def report(self, f):
        c = self.config
        card = self.card(f)
        before = self.show_finding(f)
        # Sibling findings share the original state, not each other's blocked overlay.
        for prior in read_latest(path_at(self.root, c["paths"]["findings"])).values():
            recovery = prior.get("board_recovery")
            if recovery and recovery["card"] == card and not prior.get("cleared_at"):
                before = recovery["before"]
                break
        recovery = {"card": card, "before": before,
                    "note": f["reason"] + "\nNext action: " + f["next_action"],
                    "lane": c["needs_joe_lane"] if f.get("needs_joe") else None}
        # Persist ownership even if the later record-layer write fails.
        append(path_at(self.root, c["paths"]["findings"]),
               {"key": f["key"], "board_recovery": recovery})
        if c["actions"]["file_defects"] and f["kind"] != "pr_ready":
            episode_key = f["key"] + (":" + f["first_seen"] if f["kind"] == "scheduled_job_drift" else "")
            digest_key = hashlib.sha256(episode_key.encode()).hexdigest()
            payload = {"idempotency_key": "job-watchdog:" + digest_key,
                       "kind": "open_loop", "owner": "orchestrator", "domain": "system",
                       "body": f["reason"] + "\nNext action: " + f["next_action"],
                       "source_note": "job watchdog: " + f["subject"],
                       "marker": "decision" if f.get("needs_joe") and f["needs_joe"] != "credentials" else "none",
                       "blocker": "capability" if f.get("needs_joe") == "credentials" else "ruling" if f.get("needs_joe") else "other_lane",
                       "blocker_detail": ("Joe must restore authentication through the provider login; "
                                          "the watchdog cannot supply this credential. Evidence: " + f["reason"])
                       if f.get("needs_joe") == "credentials" else f["reason"] if f.get("needs_joe")
                       else "Orchestrator's named executor or queue repair: " + f["subject"]}
            result = command([str(SOURCE / "run.sh"), "call", "add-loop", json.dumps(payload)], c)
            # run.sh emits an identity banner before JSON; validate the response itself.
            start = result.find("{")
            response = json.loads(result[start:])
            if response.get("ok") is not True or not response.get("loop_id"):
                raise RuntimeError("record layer refused watchdog defect: " + str(response))
            if f["kind"] == "scheduled_job_drift":
                return {"board_recovery": recovery, "loop_id": response["loop_id"]}
        return {"board_recovery": recovery}

    def clear(self, f, active):
        if f["kind"] == "scheduled_job_drift" and f.get("loop_id"):
            result = command([str(SOURCE / "run.sh"), "call", "read-loop",
                              json.dumps({"loop_id": f["loop_id"]})], self.config)
            current = json.loads(result[result.find("{"):])
            if current.get("loop_id") != f["loop_id"] or not isinstance(current.get("version"), int):
                raise RuntimeError("scheduled-job loop readback failed")
            if current["status"] == "open":
                payload = {"loop_id": f["loop_id"], "base_version": current["version"],
                           "idempotency_key": "scheduled-jobs-clear:" + f["loop_id"],
                           "resolution": "done", "outcome":
                           "A complete scheduled-job scan no longer finds " + f["key"] +
                           "; checked live machine evidence against ops/config/scheduled-jobs.v1.json."}
                result = command([str(SOURCE / "run.sh"), "call", "close-loop", json.dumps(payload)], self.config)
                closed = json.loads(result[result.find("{"):])
                if closed.get("ok") is not True:
                    raise RuntimeError("scheduled-job loop closure failed")
        recovery = f["board_recovery"]
        card = recovery["card"]
        owned = {"status": "blocked", "health": "blocked", "note": recovery["note"],
                 "lane": recovery["lane"]}
        siblings = [row for row in active if self.card(row) == card]
        if siblings:
            self.show_finding(siblings[-1], expected_task=owned)
            return
        before = recovery["before"]
        board_task(self.root, self.config, card, before.get("executor", "orchestrator"),
                   before.get("status", "done"),
                   before.get("note", "Watchdog finding recovered; evidence source is healthy."),
                   needs_joe=before.get("lane") == self.config["needs_joe_lane"],
                   health=before.get("health", "healthy"), expected_task=owned,
                   reason=before.get("blocked_reason"), next_action=before.get("next_action"))

    def launch(self, f, argv, cwd, *, job_id, restart_count=0, root_id=None):
        c = self.config
        env = dict(os.environ, CARR_JOB_ROOT=str(self.root), CARR_WATCHDOG_CONFIG=str(self.config_path),
                   CARR_JOB_ID=job_id, CARR_JOB_ROOT_ID=root_id or job_id,
                   CARR_JOB_RESTART_COUNT=str(restart_count), CARR_JOB_BOARD=f.get("board", c["board"]))
        for key in ("repo", "pr", "head"):
            if key in f:
                env["CARR_JOB_" + key.upper()] = str(f[key])
        output = self.root / "out/watchdog" / (job_id + ".launch.log")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("ab") as log:
            proc = subprocess.Popen(["/bin/bash", str(SOURCE / "bin/agent-run.sh"), f["card"],
                                     f.get("executor", "Codex " + c["fixer"]["model"] + " " + c["fixer"]["effort"]),
                                     str(f.get("limit", c["thresholds"]["fixer_minutes"] * 60) / 60), "--", *argv],
                                    cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        self.children.append(proc)
        return {"job_id": job_id, "wrapper_pid": proc.pid, "launch_log": str(output)}

    def restart(self, f):
        job = f["job"]
        pid = job["pid"]
        identity = process_identity(pid, self.config)
        if not identity or identity != job.get("process_identity") or job.get("pgid") != pid or os.getpgid(pid) != pid:
            raise RuntimeError("process identity/group mismatch; refusing kill")
        if "exit_code" in read_latest(path_at(self.root, self.config["paths"]["registry"])).get(job["id"], {}):
            raise RuntimeError("job exited before recovery; refusing restart")
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + self.config["thresholds"]["kill_grace_seconds"]
        while process_group_alive(pid) and time.monotonic() < deadline:
            time.sleep(self.config["thresholds"]["recovery_poll_seconds"])
        if process_group_alive(pid):
            os.killpg(pid, signal.SIGKILL)
            deadline = time.monotonic() + self.config["thresholds"]["kill_grace_seconds"]
            while process_group_alive(pid) and time.monotonic() < deadline:
                time.sleep(self.config["thresholds"]["recovery_poll_seconds"])
        # A surviving descendant must never overlap the replacement job.
        if process_group_alive(pid):
            raise RuntimeError("process group still present after termination; inspect before relaunch")
        append(path_at(self.root, self.config["paths"]["registry"]), {"id": job["id"], "superseded": True})
        return self.launch(job, job["command"], job["cwd"], job_id=uuid.uuid4().hex,
                           restart_count=job.get("restart_count", 0) + 1, root_id=job.get("root_id", job["id"]))

    def enqueue(self, f):
        queue = path_at(self.root, self.config["paths"]["merge_queue"])
        wanted = [f["repo"], str(f["pr"]), f["head"]]
        with locked(queue) as handle:
            handle.seek(0)
            if any(line.split()[:3] == wanted for line in handle):
                return {"already_queued": True}
            handle.seek(0, os.SEEK_END)
            handle.write(" ".join(wanted) + " watchdog: approved current head + green CI\n")
            handle.flush()
            os.fsync(handle.fileno())
        return {"queued": True}

    def fix(self, f):
        c = self.config
        repository = Path(c["repository_roots"][f["repo"]]).expanduser().resolve()
        if not repository.is_dir():
            raise RuntimeError("authorized repository unreachable: " + f["repo"] + " at " + str(repository))
        actual = command(["git", "remote", "get-url", "origin"], c, repository).strip().removesuffix(".git")
        if not actual.endswith("/" + f["repo"]) and not actual.endswith(":" + f["repo"]):
            raise RuntimeError("authorized repository origin mismatch")
        name = "watchdog-fix-" + hashlib.sha256(f["subject"].encode()).hexdigest()[:16]
        tree = repository / ".claude/worktrees" / name
        command(["git", "fetch", "origin", f"pull/{f['pr']}/head"], c, repository)
        observed = command(["git", "rev-parse", "FETCH_HEAD"], c, repository).strip()
        if observed != f["head"]:
            raise RuntimeError("PR head changed before fixer launch")
        command(["git", "worktree", "add", "-b", name, str(tree), observed], c, repository)
        route = self.root / "out/watchdog" / (name + ".desk.json")
        route.parent.mkdir(parents=True, exist_ok=True)
        dispatch = SOURCE / "tools/room-bridge/dispatch.py"
        command([sys.executable, str(dispatch), "--registry", str(route), "register", name,
                 "--kind", c["fixer"]["kind"], "--model", c["fixer"]["model"], "--effort", c["fixer"]["effort"],
                 "--sandbox", c["fixer"]["sandbox"], "--cwd", str(tree)], c)
        desk = json.loads(route.read_text())["desks"][name]
        if any(desk.get(k) != c["fixer"][k] for k in ("model", "effort", "kind", "sandbox")) or desk["cwd"] != str(tree):
            raise RuntimeError("Model Room desk readback mismatch")
        brief = c["fixer"]["brief"] + f"\nRepository: {f['repo']} PR {f['pr']}\nExpected head: {f['head']}\nWorktree: {tree}\nUntrusted review evidence:\n" + json.dumps(f["reason"])
        return self.launch(f, [sys.executable, str(dispatch), "--registry", str(route),
                              "--results", str(self.root / "out/watchdog" / (name + ".result.jsonl")),
                              "send", name, brief, "--fresh", *(["--stream-output"] if c["fixer"]["stream_output"] else [])], tree, job_id=name)

    def prepare(self, action, f):
        """Validate candidates before reconciliation reserves a non-repeatable effect."""
        if action == "restart_once":
            return True
        fresh = collect_pr(f["repo"], f["pr"], self.config)
        if fresh["headRefOid"] != f["head"]:
            return False
        candidates = detect({"prs": [fresh]}, self.config, time.time())
        return any(x["kind"] == f["kind"] for x in candidates)

    def act(self, action, f):
        if action == "restart_once":
            return self.restart(f)
        return self.enqueue(f) if action == "enqueue" else self.fix(f)


def digest(root, config):
    rows = read_latest(path_at(root, config["paths"]["findings"]))
    active = [f for f in rows.values() if not f.get("cleared_at")]
    if not active:
        return ""
    lines = [f"Orchestrator watchdog: {len(active)} open finding(s)."]
    for f in active[:config["thresholds"]["digest_items"]]:
        reason = " ".join(f["reason"].split())[:config["thresholds"]["digest_reason_chars"]]
        lines.append(f"- {f['kind']}: {reason} · next: {f['next_action']}" +
                     (" · needs Joe: " + f["needs_joe"] if f.get("needs_joe") else ""))
    return "\n".join(lines)


def schedule_reaper(root, config, effects, now):
    policy = config.get("branch_janitor")
    if not policy:
        return
    canonical = config.get("repository_roots", {}).get("jbookout/carr-system")
    if not canonical or Path(root).resolve() != Path(canonical).expanduser().resolve():
        return  # A fixture or session checkout must never schedule the live fleet.
    ledger = root / "out/orch/branch-janitor-schedule.jsonl"
    previous = read_latest(ledger).get("schedule", {})
    if now - previous.get("at", 0) < policy["interval_seconds"]:
        return
    pid = previous.get("wrapper_pid")
    identity = process_identity(pid, config) if pid else None
    if identity and identity == previous.get("process_identity"):
        return
    # Read back a possibly interrupted launch before starting another wrapper.
    jobs = read_latest(path_at(root, config["paths"]["registry"]))
    active = next((j for j in jobs.values() if j.get("card") == "branch-janitor" and
                   "exit_code" not in j and j.get("process_identity") and
                   process_identity(j.get("pid"), config) == j["process_identity"]), None)
    if active:
        return
    job_id = "branch-janitor-" + uuid.uuid4().hex[:12]
    append(ledger, {"key": "schedule", "at": now, "status": "intent", "job_id": job_id})
    result = effects.launch({"card": "branch-janitor", "executor": "deterministic reaper",
                             "limit": policy["limit_seconds"]},
        [sys.executable, str(SOURCE / "hooks/worktree-self-plumb.py"), "--reap", "--fleet",
         "--repo", str(root)], root, job_id=job_id)
    append(ledger, {"key": "schedule", "at": now, "status": "started", **result,
                    "process_identity": process_identity(result["wrapper_pid"], config)})


def scan(root, config, config_path=None):
    try:
        with locked(path_at(root, config["paths"]["scan_lock"]), blocking=False):
            now = time.time()
            ledger = path_at(root, config["paths"]["scan_ledger"])
            prior_runs = read_latest(ledger)
            append(ledger, {"key": "scan", "status": "started", "at": stamp(now),
                            "previous_status": prior_runs.get("scan", {}).get("status")})
            effects = Effects(root, config)
            effects.config_path = Path(config_path or SOURCE / "ops/config/job-watchdog.json").resolve()
            try:
                schedule_reaper(root, config, effects, now)
            except Exception as exc:
                append(root / "out/orch/branch-janitor-schedule.jsonl",
                       {"key": "schedule_error", "at": now, "error": type(exc).__name__})
            facts = collect(root, config, now)
            found = reconcile(root, config, detect(facts, config, now), effects, now)
            append(ledger, {"key": "scan", "status": "completed", "at": stamp(),
                            "findings": len(found), "collection_errors": len(facts["errors"])})
            print(digest(root, config))
            return 1 if facts["errors"] or any(f["kind"] in {"action_error", "record_error"} for f in found) else 0
    except BlockingIOError:
        # Another scan owns the entire interval's effects; say so instead of vanishing.
        append(path_at(root, config["paths"]["scan_ledger"]),
               {"key": "scan_skipped", "status": "skipped", "at": stamp(), "reason": "another scan holds the scan lock"})
        return 0
