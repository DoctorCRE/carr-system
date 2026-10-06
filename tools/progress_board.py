#!/usr/bin/env python3
"""CARR's progress board: task, question and answer commands plus the JSON
data contract that the one interactive board renders.

There is exactly one board UI: app.doctorcre.com/progress-board (Joe's
ruling 2026-09-29). This tool never renders a page. It keeps the local JSON
state, derives PR, release and health facts from GitHub and the release
pipeline, and publishes the snapshot the app renders. The system-wide
all-repos board is built here from gh data on every publish.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator
from urllib.request import Request, urlopen
from urllib.parse import urlencode


STATUSES = ("queued", "running", "review", "blocked", "done", "failed", "superseded")
# Failed and superseded cards leave the pipeline: they show only in History,
# each with its reason.
RETIRED_STATUSES = ("failed", "superseded")
DONE_WITHOUT_PR_EVIDENCE = "Complete; no PR (marked done by the orchestrator)"
PIPELINE_STAGES = ("queued", "build", "review", "ci", "merged", "live")
PR_STAGES = PIPELINE_STAGES[1:] + ("measured",)
STAGE_LABELS = {
    "queued": "Queued",
    "build": "Building",
    "review": "Review",
    "ci": "CI",
    "merged": "Merged",
    "live": "Live",
}
STATUS_TO_STAGE = {
    "queued": "queued",
    "running": "build",
    "review": "review",
    "blocked": "review",
    "failed": "ci",
    "superseded": "ci",
}
# In flight: a card that should keep moving. Stale applies only to these.
IN_FLIGHT = frozenset({"running", "review", "blocked"})
STUCK_AFTER = timedelta(hours=2)
STALE_AFTER = timedelta(hours=6)
LAUNCHD_BOARD = "carr-v5"
DEFAULT_PR_REPO = "jbookout/carr-system"
SNAPSHOT_SCHEMA = "carr-progress-board.v2"
# publish-board-snapshot refuses JSON.stringify(snapshot).length > 262144
# (mcp-server/src/board-answers.js): compact JSON, counted in UTF-16 units.
SNAPSHOT_LIMIT = 262144
SNAPSHOT_BUDGET = SNAPSHOT_LIMIT * 9 // 10
# The app card contract, not the local job/PR diagnostic record. In particular,
# note duplicates watchdog stderr already carried in blocked_reason.
SNAPSHOT_TASK_FIELDS = frozenset("""
    title status stage executor provider model effort health repo pr pr_url
    pr_phase pr_head pr_checks pr_links question review_verdict summary blocked_reason next_action evidence
    release_wait created_at updated_at completed_at merged_at manual_stage
    stage_entered_at stage_history question_ids human_ref kind related
    work_request work_request_ref
""".split())
BLOCKER_EXCERPT_LIMIT = 192

ALL_REPOS_BOARD = "all-repos"
GITHUB_OWNER = "jbookout"
CORE_REPOS = ("jbookout/carr-system", "jbookout/doctorcre-app", "jbookout/software-factory")
RECENT_MERGED = timedelta(days=7)
# The repository this tool reads its release config, review rules and verbs
# from. The launchd wrapper binds it explicitly, so a copy of the tool run from
# anywhere else still reads the canonical checkout.
REPO_ROOT = Path(os.environ.get("CARR_REPO_ROOT") or Path(__file__).resolve().parents[1]).expanduser().resolve()
RELEASE_CONFIG = REPO_ROOT / "ops" / "config" / "release-pipeline.v1.json"
SHA_RE = re.compile(r"[0-9a-f]{40}")

# Executor pools in ledger order: key, label, glyph. The app legend carries
# the same letters; its test pins them.
POOLS = (
    ("codex", "Codex", "C"),
    ("grok", "Grok", "G"),
    ("flash-next", "Flash Next", "F"),
    ("claude-cloud", "Claude cloud credits", "✦"),
    ("orchestrator", "Orchestrator seat", "O"),
)

# A blocked PR phase names its own reason and the next action.
PHASE_BLOCKS = {
    "Checks failing": ("CI checks are failing on the PR head", "Read the failing check log, fix, and push"),
    "Review blocked": ("An independent reviewer posted BLOCK", "Address the review findings and push a new head"),
    "Merge conflict": ("Merge conflict with the base branch", "Merge the base branch and resolve the conflict"),
    "Changes requested": ("A reviewer requested changes", "Address the requested changes and re-request review"),
    "Closed unmerged": ("PR closed without merging", "Decide: reopen, replace, or retire the task"),
}

RELEASE_TARGETS = {
    DEFAULT_PR_REPO: (Path.home() / "carr-system", "https://api.doctorcre.com/release"),
    "jbookout/doctorcre-app": (Path.home() / "doctorcre-app", "https://app.doctorcre.com/app-release"),
}
AUTOMATIC_DELIVERY_TARGETS = {
    DEFAULT_PR_REPO: "worker",
    "jbookout/doctorcre-app": "app",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now_utc().isoformat(timespec="seconds")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def age_text(timestamp: Any, at: datetime | None = None) -> str:
    """Compact age: 35m, 2h 14m, 1d 2h."""
    then = parse_time(timestamp)
    if then is None:
        return "unknown"
    minutes = max(0, int(((at or now_utc()) - then).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes // 1440}d {(minutes % 1440) // 60}h"


def board_dir() -> Path:
    configured = os.environ.get("PROGRESS_BOARD_ROOT")
    if configured:
        return Path(configured).expanduser().resolve() / "boards"
    return Path.cwd() / "out" / "boards"


def safe_project(project: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project):
        raise SystemExit("project must contain only letters, numbers, dot, underscore, or hyphen")
    return project


def safe_asker_ref(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", value):
        raise SystemExit("asker_ref must be a short named session or orchestrator reference")
    return value


def safe_repo(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise SystemExit("repo must be a GitHub owner/repository name")
    return value


def task_repo(task: dict[str, Any]) -> str:
    return safe_repo(task.get("repo") or DEFAULT_PR_REPO)


def pr_key(task: dict[str, Any]) -> tuple[str, int]:
    return task_repo(task), int(task["pr"])


def card_key(repo: str, number: int) -> str:
    """PR numbers are per repository, so a card key always carries the repo."""
    return f"{safe_repo(repo).split('/', 1)[1]}-{int(number)}"


def state_path(project: str) -> Path:
    return board_dir() / f"{safe_project(project)}.json"


def read_state(project: str) -> dict[str, Any]:
    path = state_path(project)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"board does not exist: {project}; run init first")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid board JSON {path}: {exc}")


def temp_file(path: Path, text: str) -> Path:
    """A private, fully written sibling of path: every writer has its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def atomic_write(path: Path, text: str) -> None:
    tmp = temp_file(path, text)
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(state: dict[str, Any]) -> None:
    atomic_write(state_path(state["project"]), json.dumps(state, indent=2, sort_keys=False) + "\n")


def create_json(state: dict[str, Any]) -> None:
    """Write a new board, refusing if one appeared first (link is exclusive)."""
    path = state_path(state["project"])
    tmp = temp_file(path, json.dumps(state, indent=2, sort_keys=False) + "\n")
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise SystemExit(f"board already exists: {state['project']}")
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def board_lock(project: str) -> Iterator[int]:
    """One transaction at a time per board: read, change and write under an
    exclusive lock. Never held across a GitHub read, and never nested."""
    path = board_dir() / f"{safe_project(project)}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def is_stuck(task: dict[str, Any], at: datetime | None = None) -> bool:
    if task.get("status") == "blocked":
        return True
    if task.get("status") != "running":
        return False
    updated = parse_time(task.get("updated_at"))
    if updated is None:
        return True
    return (at or now_utc()) - updated > STUCK_AFTER


def is_stale(task: dict[str, Any], at: datetime | None = None) -> bool:
    """An in-flight card with no update for STALE_AFTER or longer."""
    if task.get("status") not in IN_FLIGHT or task_stage(task) == "live":
        return False
    updated = parse_time(task.get("updated_at"))
    return updated is not None and (at or now_utc()) - updated >= STALE_AFTER


def violation(executor: str) -> bool:
    value = executor.lower()
    if "claude-plan" in value:
        return True
    if "orchestrator" in value and "claude" not in value:
        return False
    return bool(re.search(r"\b(?:opus|sonnet)\s+subagent\b", value)) or (
        "claude" in value and "subagent" in value
    )


def executor_pool(executor: str) -> str:
    value = executor.lower().strip()
    if "orchestrator" in value:
        return "orchestrator"
    if "claude" in value or "opus" in value or "sonnet" in value:
        return "claude-cloud"
    if "flash next" in value or "flash-next" in value:
        return "flash-next"
    if "grok" in value:
        return "grok"
    if "codex" in value or "gpt-" in value:
        return "codex"
    return "unassigned"


def executor_glyph(executor: str) -> str:
    return {key: glyph for key, _, glyph in POOLS}.get(executor_pool(executor), "?")


def executor_metadata(executor: str | None) -> tuple[str, str, str]:
    """Recover provider, model and effort from legacy executor labels."""
    value = (executor or "").strip()
    lower = value.lower()
    effort_match = re.search(r"\b(low|medium|high|xhigh|max|ultra)\b", lower)
    effort = effort_match.group(1) if effort_match else "unknown"
    # Explicit model evidence first; a seat name ("orchestrator") says who
    # dispatched the work, not which model did it.
    gpt = re.search(r"\bgpt-[\w.-]+", value, re.IGNORECASE)
    if gpt:
        return "Codex", gpt.group(0).lower(), effort
    claude = re.search(r"\bclaude\s+(?:opus|sonnet|haiku)\s+[\d.]+", value, re.IGNORECASE)
    if claude:
        return "Anthropic", " ".join(part.capitalize() if not part[0].isdigit() else part
                                     for part in claude.group(0).split()), effort
    if "orchestrator" in lower:
        return "Unknown", "unknown", effort
    provider = {"codex": "Codex", "claude-cloud": "Anthropic", "grok": "xAI",
                "flash-next": "Google"}.get(executor_pool(value), "Unknown")
    return provider, "unknown", effort


def task_identity(task: dict[str, Any]) -> tuple[str, str, str]:
    derived = executor_metadata(task.get("executor"))
    return (str(task.get("provider") or derived[0]),
            str(task.get("model") or derived[1]),
            str(task.get("effort") or derived[2]))


def executor_ledger(tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-pool counts with each provider/model/effort seen, and policy flags."""
    rows = []
    pools = [*POOLS, ("unassigned", "Unassigned", "?")]
    for key, label, glyph in pools:
        members = [t for t in tasks.values() if executor_pool(str(t.get("executor") or "")) == key]
        if key == "unassigned" and not members:
            continue
        models: dict[tuple[str, str, str], int] = {}
        for task in members:
            identity = task_identity(task)
            models[identity] = models.get(identity, 0) + 1
        rows.append({
            "pool": key, "label": label, "glyph": glyph, "count": len(members),
            "violation": any(violation(str(t.get("executor") or "")) for t in members),
            "models": [{"provider": p, "model": m, "effort": e, "count": n}
                       for (p, m, e), n in sorted(models.items(), key=lambda item: (-item[1], item[0]))],
        })
    return rows


def is_retired(task: dict[str, Any]) -> bool:
    return task.get("status") in RETIRED_STATUSES


def retired_reason(task: dict[str, Any]) -> str:
    reason = task.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    if task.get("pr_phase") == "Closed unmerged":
        return "PR closed without merging"
    note = task.get("note")
    return note.strip() if isinstance(note, str) and note.strip() else "No reason recorded"


def task_stage(task: dict[str, Any]) -> str:
    # Live means complete (Joe). A done card with no PR has nothing left to
    # merge or release. A note that names a PR is never read as one.
    if task.get("status") == "done" and task.get("pr") is None:
        return "live"
    requested = task.get("stage")
    if requested == "measured":
        requested = "live"
    evidence = task.get("evidence")
    if requested == "live" and not (isinstance(evidence, str) and evidence.strip()):
        requested = None
    if requested in PIPELINE_STAGES:
        return requested
    if task.get("status") == "done":
        # Merged and waiting on a verified release stays Merged; a PR not yet
        # merged is still in review. Never back in Building.
        return "merged" if task.get("pr_phase") == "Merged" else "review"
    if task.get("status") == "measured":
        return "live" if isinstance(evidence, str) and evidence.strip() else "build"
    return STATUS_TO_STAGE.get(task.get("status", "queued"), "queued")


def completed_at(task: dict[str, Any]) -> datetime | None:
    if task_stage(task) != "live":
        return None
    return parse_time(task.get("completed_at") or task.get("updated_at"))


def task_health(task: dict[str, Any], at: datetime | None = None) -> str:
    # A finished card cannot be blocked: a leftover flag from an earlier
    # review round is ignored here and dropped from state by normalize_task.
    if task.get("status") == "done" or task_stage(task) == "live":
        return "healthy"
    if task.get("status") in {"blocked", "failed"} or task.get("health") == "blocked" or is_stuck(task, at):
        return "blocked"
    if task.get("status") == "review" or task.get("health") == "question" or task.get("question"):
        return "question"
    return "healthy"


def pulse_state(task: dict[str, Any], at: datetime | None = None) -> str:
    health = task_health(task, at)
    if health == "blocked":
        return "critical"
    if health == "question":
        return "attention"
    if task.get("status") in {"done", "queued"}:
        return "still"
    return "healthy"


def blocked_detail(task: dict[str, Any], at: datetime | None = None) -> tuple[str, str] | None:
    """Every blocked card names why and what happens next."""
    if task_health(task, at) != "blocked":
        return None
    phase = PHASE_BLOCKS.get(str(task.get("pr_phase") or ""))
    reason = str(task.get("blocked_reason") or "").strip()
    action = str(task.get("next_action") or "").strip()
    if reason:
        return reason, action or (phase[1] if phase else "Orchestrator: record the next action")
    if phase:
        return phase
    if task.get("status") == "failed":
        return "Task failed", "Decide: retry, replace, or retire the task"
    if task.get("status") == "running" and is_stuck(task, at):
        age = age_text(task.get("updated_at"), at)
        return (f"No update for {age}" if age != "unknown" else "No update recorded",
                "Check the executor session; post an update or re-dispatch")
    return "Marked blocked without a recorded reason", "Orchestrator: record the reason and next action"


def stage_entered_at(task: dict[str, Any]) -> str | None:
    history = task.get("stage_history") or []
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    value = (task.get("stage_entered_at") or last.get("entered_at") or last.get("at")
             or task.get("updated_at") or task.get("created_at"))
    return value if isinstance(value, str) else None


def stage_timer(task: dict[str, Any], at: datetime | None = None) -> str:
    """'build 2h 14m': the stage and how long the card has been in it."""
    return f"{task_stage(task)} {age_text(stage_entered_at(task), at)}"


def stage_durations(task: dict[str, Any], at: datetime | None = None) -> list[tuple[str, str, str]]:
    history = [h for h in task.get("stage_history") or [] if isinstance(h, dict) and h.get("entered_at")]
    rows = []
    for index, entry in enumerate(history):
        end = parse_time(history[index + 1]["entered_at"]) if index + 1 < len(history) else (at or now_utc())
        rows.append((str(entry.get("stage")), str(entry["entered_at"]), age_text(entry["entered_at"], end)))
    return rows


def record_stage(task: dict[str, Any], entered_at: str, prior_stage: str | None) -> None:
    stage = task_stage(task)
    history = list(task.get("stage_history") or [])
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    if (prior_stage == stage and history) or last.get("stage") == stage:
        return
    task["stage_history"] = [*history, {"stage": stage, "entered_at": entered_at}]
    task["stage_entered_at"] = entered_at


def normalize_task(task: dict[str, Any]) -> bool:
    """Idempotent repair of stored state; never moves updated_at."""
    before = json.dumps(task, sort_keys=True)
    if task.get("status") == "done" or task_stage(task) == "live":
        for field in ("health", "blocked_reason", "next_action", "blocked_source", "blocked_head"):
            task.pop(field, None)
    if task_stage(task) == "live":
        task.pop("release_wait", None)
    history = []
    for entry in task.get("stage_history") or []:
        if not isinstance(entry, dict):
            continue
        entered = entry.get("entered_at") or entry.get("at")
        if isinstance(entered, str) and entry.get("stage"):
            history.append({"stage": entry["stage"], "entered_at": entered})
    fallback = task.get("updated_at") or task.get("created_at")
    stage = task_stage(task)
    if isinstance(fallback, str) and (not history or history[-1]["stage"] != stage):
        history.append({"stage": stage, "entered_at": fallback})
    if history:
        task["stage_history"] = history
        if task.get("stage_entered_at") != history[-1]["entered_at"]:
            task["stage_entered_at"] = history[-1]["entered_at"]
    return json.dumps(task, sort_keys=True) != before


def task_summary(task: dict[str, Any]) -> str:
    summary = str(task.get("summary") or "").strip()
    if summary:
        return summary
    return str(task.get("title") or "This task").strip().rstrip(".") + "."



def one_sentence(value: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*(?:[-*]\s+|#+\s+)", "", value or "")).strip()
    text = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].rstrip(".")
    return text + "." if text else ""


def backfill_state(state: dict[str, Any], lookup: Any) -> int:
    """Fill legacy task metadata without changing recorded status or timestamps."""
    changed = 0
    for task in state.get("tasks", {}).values():
        before = dict(task)
        provider, model, effort = task_identity(task)
        task.setdefault("provider", provider)
        task.setdefault("model", model)
        task.setdefault("effort", effort)
        if not task.get("summary"):
            if task.get("pr") is not None:
                info = lookup(int(task["pr"]), task_repo(task))
                if not info or not info.get("title") or not info.get("body"):
                    raise RuntimeError(f"PR {task['pr']} title/body unavailable for summary backfill")
                task["summary"] = one_sentence(info["title"])
            else:
                task["summary"] = one_sentence(task.get("note") or task.get("title") or "")
        if not task.get("stage_history"):
            task["stage_history"] = [{"stage": task_stage(task), "status": task.get("status"),
                                      "at": task.get("updated_at") or task.get("created_at"),
                                      "source": "observed snapshot"}]
        if task != before:
            changed += 1
    return changed

def pr_summary(body: str | None, limit: int = 160) -> str:
    """First plain line of a PR body: no headings, tables, code or markup."""
    fenced = False
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith(("#", "|", "<!--", ">", "---")):
            continue
        line = re.sub(r"^(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)", "", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"(\*\*|__|`|\*)", "", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


def check_counts(rollup: list[dict[str, Any]]) -> tuple[int, int, int]:
    passed = pending = failed = 0
    for check in rollup:
        conclusion = str(check.get("conclusion") or check.get("state") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in {"SUCCESS", "SKIPPED", "NEUTRAL"}:
            passed += 1
        elif conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}:
            failed += 1
        elif status or conclusion:
            pending += 1
    return passed, pending, failed


def checks_summary(payload: dict[str, Any]) -> str:
    rollup = payload.get("statusCheckRollup") or []
    if not isinstance(rollup, list) or any(not isinstance(check, dict) for check in rollup):
        return "checks unavailable"
    passed, pending, failed = check_counts(rollup)
    if passed + pending + failed == 0:
        return "no checks"
    return f"{passed} pass · {pending} pending · {failed} fail"


def repo_lib(name: str) -> ModuleType:
    """A lib/ module from the bound repository, imported when first used, so
    an extracted copy still loads and reports what it cannot reach."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module(f"lib.{name}")


def gh_binary() -> str | None:
    if os.environ.get("PROGRESS_BOARD_SKIP_GH"):
        return None
    return repo_lib("github_reader").resolve_gh()


def log(message: str) -> None:
    print(f"progress-board: {message}", file=sys.stderr)


# The board re-renders on a short interval and keeps the last known state when
# a read fails, so one quick retry is worth having and a long wait is not.
GH_RETRY_DELAYS = (2,)


@functools.lru_cache(maxsize=None)
def gh_reader(binary: str, timeout: int) -> Any:
    """One lib/github_reader reader per binary and timeout for the whole run,
    so a GitHub outage costs one retry cycle per render rather than one per read."""
    return repo_lib("github_reader").GitHubReader(gh=binary, timeout=timeout,
                                                  retry_delays=GH_RETRY_DELAYS)


def gh_text(args: list[str], timeout: int = 30) -> str:
    binary = gh_binary()
    if binary is None:
        raise RuntimeError("gh CLI unavailable")
    return gh_reader(binary, timeout).text(args)


def gh_json(args: list[str], timeout: int = 30) -> Any:
    binary = gh_binary()
    if binary is None:
        raise RuntimeError("gh CLI unavailable")
    return gh_reader(binary, timeout).json(args)


# All GitHub reads for a render share this pass, including the all-repos
# refresh that follows the project board. Persisted snapshots live outside
# board JSON so PR identity, not board identity, owns the cache.
GITHUB_PASS: GitHubReadPass | None = None
REST_PAGE_SIZE = 100
REST_MAX_ROWS = 16000


def rest_rows(path: str, key: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for page in range(1, REST_MAX_ROWS // REST_PAGE_SIZE + 1):
        join = "&" if "?" in path else "?"
        payload = gh_json(["api", f"{path}{join}per_page={REST_PAGE_SIZE}&page={page}"], timeout=30)
        batch = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise RuntimeError("gh returned a malformed REST list")
        rows.extend(batch)
        if len(batch) < REST_PAGE_SIZE:
            return rows
    raise RuntimeError("REST list would be incomplete at the pagination cap")


def rest_pr(raw: Any) -> dict[str, Any]:
    if (not isinstance(raw, dict) or raw.get("state") not in {"open", "closed"}
            or not isinstance(raw.get("draft"), bool)
            or not isinstance(raw.get("head"), dict) or not isinstance(raw["head"].get("sha"), str)
            or not isinstance(raw.get("user"), dict) or not isinstance(raw["user"].get("login"), str)
            or (raw.get("merge_commit_sha") is not None and not isinstance(raw["merge_commit_sha"], str))):
        raise RuntimeError("gh returned a malformed PR payload")
    merged = bool(raw.get("merged_at"))
    if merged and not SHA_RE.fullmatch(str(raw.get("merge_commit_sha") or "")):
        raise RuntimeError("merged PR has no valid merge commit SHA")
    return {
        "number": raw.get("number"), "title": raw.get("title"), "body": raw.get("body"),
        "url": raw.get("html_url"), "createdAt": raw.get("created_at"), "updatedAt": raw.get("updated_at"),
        "state": "MERGED" if merged else raw["state"].upper(), "isDraft": raw["draft"],
        "headRefOid": raw["head"]["sha"], "headRefName": raw["head"].get("ref", ""),
        "author": {"login": raw["user"]["login"]}, "mergedAt": raw.get("merged_at"),
        # An open PR's merge_commit_sha is a test merge, never a delivered commit.
        "mergeCommit": {"oid": raw["merge_commit_sha"]} if merged and raw.get("merge_commit_sha") else None,
        "mergeable": ("MERGEABLE" if raw.get("mergeable") is True else
                      "CONFLICTING" if raw.get("mergeable") is False else "UNKNOWN"),
        "statusCheckRollup": [], "comments": [], "reviewDecision": "",
    }


def rest_review_decision(reviews: list[dict[str, Any]], raw: dict[str, Any], rules: dict[str, Any]) -> str:
    # A comment-only review does not withdraw an earlier approval/request.
    latest = {}
    for review in sorted(reviews, key=lambda r: (str(r.get("submitted_at") or ""), int(r.get("id") or 0))):
        if not release_pipeline().trusted_commenter(review, rules):
            continue
        state = str(review.get("state") or "").upper()
        if state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            login = (review.get("user") or {}).get("login")
            if not isinstance(login, str):
                raise RuntimeError("gh returned a malformed review")
            latest[login] = state
    if "CHANGES_REQUESTED" in latest.values():
        return "CHANGES_REQUESTED"
    if "APPROVED" in latest.values():
        return "APPROVED"
    # This equivalent keeps the board waiting for review. REST's blocked state
    # can also name an unmet check, which the rollup handles before review.
    return "REVIEW_REQUIRED" if raw.get("mergeable_state") == "blocked" else ""


def rest_checks(repo: str, head: str) -> list[dict[str, Any]]:
    checks = rest_rows(f"repos/{repo}/commits/{head}/check-runs", "check_runs")
    if not all(valid_check(check) for check in checks):
        raise RuntimeError("gh returned a malformed PR payload")
    statuses = rest_rows(f"repos/{repo}/commits/{head}/statuses")
    # REST returns status history; the board renders the latest per context.
    contexts = {}
    for status in statuses:
        context = status.get("context")
        if not isinstance(context, str) or not isinstance(status.get("state"), str):
            raise RuntimeError("gh returned a malformed commit status")
        if context not in contexts:
            contexts[context] = {"__typename": "StatusContext", "context": context, "state": status["state"].upper()}
    return [{"__typename": "CheckRun", "name": c.get("name"), "status": c["status"].upper(),
             "conclusion": c["conclusion"].upper() if isinstance(c["conclusion"], str) else None}
            for c in checks] + list(contexts.values())


def pr_fresh_seconds() -> float:
    """Seconds an open-PR observation is reused (PROGRESS_BOARD_PR_FRESH_SECONDS, default 120; 0 disables)."""
    try:
        return max(0.0, float(os.environ.get("PROGRESS_BOARD_PR_FRESH_SECONDS", "120")))
    except ValueError:
        return 120.0


class GitHubReadPass:
    def __init__(self) -> None:
        self.path = board_dir() / ".github-pr-cache.json"
        self.saved: dict[str, dict[str, Any]] = {}
        self.reconcile()
        self.results: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
        # Legacy boards share identities too. Seed all their terminal facts
        # before the first open card in any one board can rediscover that PR.
        for path in board_dir().glob("*.json"):
            if path.name.startswith("."):
                continue
            self.seed((read_json_file(path).get("tasks") or {}).values())

    def reconcile(self) -> None:
        self.saved = {identity: info for identity, info in read_json_file(self.path).items()
                      if validated_pr(info) is not None}

    def observe(self) -> int:
        # Reserve ordering before network I/O. PR timestamps cannot order CI
        # or mergeability observations; completion time cannot order readers.
        with board_lock("github-pr-cache"):
            saved = read_json_file(self.path)
            generation = int(saved.get("_generation") or 0) + 1
            saved["_generation"] = generation
            atomic_write(self.path, json.dumps(saved, sort_keys=True) + "\n")
        return generation

    def save(self, identity: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Retain the winning fact in memory as well as on disk. MERGED facts
        # are immutable except for one authenticated legacy enrichment.
        with board_lock("github-pr-cache"):
            saved = read_json_file(self.path)
            old = saved.get(identity) or {}
            terminal_conflict = (old.get("state") == "MERGED" and
                                 (not old.get("_legacy_terminal") or payload.get("_legacy_terminal")
                                  or payload.get("state") != "MERGED" or merge_sha(old) != merge_sha(payload)))
            superseded = (str(old.get("updatedAt") or "") > str(payload.get("updatedAt") or "")
                          or int(old.get("_observation") or 0) > int(payload.get("_observation") or 0))
            if terminal_conflict or superseded:
                winner = old
            else:
                winner = payload
                saved[identity] = payload
                atomic_write(self.path, json.dumps(saved, sort_keys=True) + "\n")
            self.saved[identity] = winner
            return winner

    def seed(self, tasks: Any) -> None:
        for task in tasks:
            if task.get("pr") is None:
                continue
            identity = f"{task_repo(task)}#{int(task['pr'])}"
            if identity in self.saved:
                continue
            if task.get("pr_phase") == "Merged" and SHA_RE.fullmatch(str(task.get("merge_sha") or "")):
                # Already verified legacy terminal cards need no rediscovery.
                self.save(identity, {"state": "MERGED", "isDraft": False, "_legacy_terminal": True,
                          "headRefOid": task.get("pr_head") or "", "author": {"login": task.get("author") or ""},
                          "mergeCommit": {"oid": task["merge_sha"]}, "statusCheckRollup": [], "comments": [],
                          "number": int(task['pr']), "title": task.get("title") or "PR",
                          "body": task.get("summary") or "", "headRefName": task.get("branch") or "",
                          "url": task.get("url") or f"https://github.com/{task_repo(task)}/pull/{task['pr']}",
                          "createdAt": task.get("created_at") or task.get("updated_at") or stamp(),
                          "updatedAt": task.get("github_updated_at"), "mergedAt": task.get("merged_at"),
                          "reviewDecision": "", "mergeable": "UNKNOWN"})
            elif task.get("pr_phase") == "Closed unmerged":
                self.save(identity, {"state": "CLOSED", "isDraft": False, "_legacy_terminal": True, "headRefOid": task.get("pr_head") or "",
                          "author": {"login": task.get("author") or ""}, "mergeCommit": None,
                          "statusCheckRollup": [], "comments": [], "reviewDecision": "", "mergeable": "UNKNOWN"})

    def result(self, info: dict[str, Any] | None, repo: str,
               error: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        # Raw observations are reusable; review trust belongs to the current policy.
        if info and "_reviews" in info:
            info = {**info, "reviewDecision": rest_review_decision(
                info["_reviews"], {"mergeable_state": info.get("_mergeable_state")}, review_rules(repo))}
        return info, error or (info.get("_refresh_error") if info else None)

    @staticmethod
    def matches_discovery(info: dict[str, Any] | None, raw: dict[str, Any] | None) -> bool:
        return raw is None or (info is not None
            and raw.get("state") == ("open" if info["state"] == "OPEN" else "closed")
            and raw.get("updated_at") == info.get("updatedAt")
            and (not isinstance(raw.get("head"), dict)
                 or raw["head"].get("sha") == info.get("headRefOid")))

    def read(self, number: int, repo: str, raw: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
        repo = safe_repo(repo)
        identity = f"{repo}#{number}"
        self.reconcile()
        old = self.saved.get(identity)
        if old and old.get("state") == "MERGED" and not old.get("_legacy_terminal"):
            return self.result(old, repo)
        # An open PR read moments ago is reused instead of re-fetched. Every board
        # mutation renders every open PR, so one job-watchdog scan (100+ mutations)
        # spent the whole 5,000/hr REST pool in minutes on 2026-10-04. Discovery
        # rows (raw) still carry their own version evidence and are checked below.
        cached = self.results.get(identity)
        if cached:
            info, error = cached
            if info and old and int(old.get("_observation") or 0) > int(info.get("_observation") or 0):
                info, error = old, None
            # Discovery rows are evidence: a different state, version or head
            # invalidates even an earlier result from this same render pass.
            if self.matches_discovery(info, raw):
                return self.result(info, repo, error)
        window = pr_fresh_seconds()
        # Payload and observation time come from the same atomic cache snapshot.
        # A losing writer never changes the winner's time; failed refreshes miss.
        if (window and raw is None and old and not old.get("_legacy_terminal")
                and not old.get("_refresh_error")
                and isinstance(old.get("_observed_at"), (int, float))
                and 0 <= time.time() - old["_observed_at"] < window):
            return self.result(old, repo)
        observation = self.observe()
        observed_at = time.time()
        discovery = raw if raw is not None else (old.get("_discovery") if old else None)
        if old and discovery is not None and not self.matches_discovery(old, discovery):
            hint = {key: discovery[key] for key in ("state", "updated_at", "head") if key in discovery}
            old = self.save(identity, {**old, "_observation": observation, "_observed_at": None,
                                      "_discovery": hint,
                                      "_refresh_error": "PR discovery invalidated cached observation"})
        result: tuple[dict[str, Any] | None, str | None]
        try:
            # Mergeability changes with the base and CI changes independently
            # of updated_at. Neither can use PR-version invalidation.
            raw = gh_json(["api", f"repos/{repo}/pulls/{number}"], timeout=30)
            info = rest_pr(raw)
            assert isinstance(raw, dict)  # rest_pr has validated the response
            if (discovery is not None and not self.matches_discovery(info, discovery)
                    and str(info.get("updatedAt") or "") <= str(discovery.get("updated_at") or "")):
                kind = "closed discovery" if discovery.get("state") == "closed" else "discovery"
                raise RuntimeError(f"PR detail disagrees with {kind}")
            head = info["headRefOid"]
            base = f"repos/{repo}"
            if old and old.get("_legacy_terminal") and old["state"] == "MERGED":
                if info["state"] != "MERGED" or merge_sha(info) != merge_sha(old):
                    raise RuntimeError("legacy merge evidence disagrees with GitHub")
                info["comments"] = old["comments"]
                info["reviewDecision"] = old["reviewDecision"]
                info["statusCheckRollup"] = old["statusCheckRollup"]
            else:
                info["statusCheckRollup"] = rest_checks(repo, head)
                if (old and not old.get("_legacy_terminal") and info.get("updatedAt")
                        and info["updatedAt"] == old.get("updatedAt")
                        and head == old.get("headRefOid") and info["state"] == old["state"]):
                    info["comments"] = old["comments"]
                    reviews = old.get("_reviews")
                else:
                    comments = rest_rows(f"{base}/issues/{number}/comments")
                    info["comments"] = [{"author": c.get("user"), "authorAssociation": c.get("author_association"),
                                         "body": c.get("body"), "createdAt": c.get("created_at")} for c in comments]
                    reviews = None
                # Cache review observations, never the trust-policy result.
                # Existing snapshots without raw reviews migrate on this read.
                if reviews is None:
                    reviews = rest_rows(f"{base}/pulls/{number}/reviews")
                info["_reviews"] = reviews
                info["_mergeable_state"] = raw.get("mergeable_state")
                info["reviewDecision"] = rest_review_decision(reviews, raw, review_rules(repo))
            if info["state"] == "MERGED":
                files = rest_rows(f"{base}/pulls/{number}/files")
                info["files"] = [{"path": f.get("filename")} for f in files]
                info["changedFiles"] = raw.get("changed_files")
                if changed_paths(info) is None:
                    raise RuntimeError("merged PR file manifest is incomplete")
            if validated_pr(info) is None:
                raise RuntimeError("gh returned a malformed PR payload")
            info["_observation"] = observation
            info["_observed_at"] = observed_at
            result = self.result(self.save(identity, info), repo)
        except (RuntimeError, TypeError, ValueError, AttributeError, KeyError) as exc:
            self.reconcile()
            old = self.saved.get(identity) or old
            if old:
                old = self.save(identity, {**old, "_observation": observation,
                                          "_observed_at": None, "_refresh_error": str(exc)})
                result = self.result(old, repo)
            else:
                result = (None, str(exc))
        self.results[identity] = result
        return result


@contextlib.contextmanager
def github_read_pass() -> Iterator[GitHubReadPass]:
    global GITHUB_PASS
    if GITHUB_PASS is not None:
        yield GITHUB_PASS
        return
    GITHUB_PASS = GitHubReadPass()
    try:
        yield GITHUB_PASS
    finally:
        GITHUB_PASS = None


def with_github_read_pass(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with github_read_pass():
            return fn(*args, **kwargs)
    return wrapped


def valid_check(check: Any) -> bool:
    """A CheckRun (status + conclusion) or a legacy commit StatusContext (state)."""
    if not isinstance(check, dict):
        return False
    if "status" in check:
        return (isinstance(check["status"], str) and "conclusion" in check
                and (check["conclusion"] is None or isinstance(check["conclusion"], str)))
    return "conclusion" not in check and isinstance(check.get("state"), str)


def fetch_pr(number: int, repo: str) -> tuple[dict[str, Any] | None, str | None]:
    """REST-only cached read; errors carry the last successful observation."""
    with github_read_pass() as reads:
        return reads.read(number, repo)


def validated_pr(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if not isinstance(state, str) or state not in {"OPEN", "CLOSED", "MERGED"}:
        return None
    if not isinstance(payload.get("isDraft"), bool) or not isinstance(payload.get("headRefOid"), str):
        return None
    author = payload.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("login"), str):
        return None
    checks = payload.get("statusCheckRollup")
    if not isinstance(checks, list):
        return None
    if not all(valid_check(check) for check in checks):
        return None
    if not valid_comments(payload.get("comments")):
        return None
    merge = payload.get("mergeCommit")
    if merge is not None and not (isinstance(merge, dict) and isinstance(merge.get("oid"), str)):
        return None
    for field in ("reviewDecision", "mergeable"):
        if payload.get(field) is not None and not isinstance(payload[field], str):
            return None
    if not valid_files(payload, required=False):
        return None
    return payload


def valid_comments(comments: Any) -> bool:
    if not isinstance(comments, list):
        return False
    for comment in comments:
        if not isinstance(comment, dict):
            return False
        commenter = comment.get("author")
        if (not isinstance(commenter, dict) or not isinstance(commenter.get("login"), str)
                or not all(isinstance(comment.get(field), str)
                           for field in ("authorAssociation", "body", "createdAt"))):
            return False
    return True


def valid_files(payload: dict[str, Any], required: bool) -> bool:
    files, count = payload.get("files"), payload.get("changedFiles")
    if files is None and count is None:
        return not required
    return (isinstance(files, list) and isinstance(count, int) and not isinstance(count, bool)
            and all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in files))


def valid_open_pr(pr: Any) -> bool:
    """Every field a card is built from, with its type. One bad row fails the
    whole repository read, so a partial answer never replaces good cards."""
    if not isinstance(pr, dict) or not isinstance(pr.get("number"), int) or isinstance(pr.get("number"), bool):
        return False
    if not all(isinstance(pr.get(field), str) for field in ("title", "headRefName", "headRefOid", "url", "createdAt")):
        return False
    if pr.get("updatedAt") is not None and not isinstance(pr["updatedAt"], str):
        return False
    if pr.get("body") is not None and not isinstance(pr["body"], str):
        return False
    author = pr.get("author")
    if author is not None and not (isinstance(author, dict) and isinstance(author.get("login"), str)):
        return False
    checks = pr.get("statusCheckRollup")
    return (isinstance(pr.get("isDraft"), bool) and isinstance(checks, list) and all(valid_check(c) for c in checks)
            and valid_comments(pr.get("comments"))
            and all(pr.get(field) is None or isinstance(pr[field], str) for field in ("mergeable", "reviewDecision")))


def merge_sha(payload: dict[str, Any]) -> str | None:
    merge = payload.get("mergeCommit")
    oid = str(merge.get("oid") or "").lower() if isinstance(merge, dict) else ""
    return oid if SHA_RE.fullmatch(oid) else None


# ── review evidence ──────────────────────────────────────────────────────────
# One interpretation, the release pipeline's own (ops/release-pipeline.py):
# the LATEST verdict-carrying comment from a trusted author decides; BLOCK
# markers block; an approval counts only as a literal APPROVE whose
# Reviewed-SHA is the exact PR head. Every session posts through the owner
# account, so authorship by the PR's own account is not disqualifying.

_PIPELINE: ModuleType | None = None


def release_pipeline() -> ModuleType:
    global _PIPELINE
    if _PIPELINE is None:
        path = REPO_ROOT / "ops" / "release-pipeline.py"
        spec = importlib.util.spec_from_file_location("carr_release_pipeline", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"release pipeline unavailable at {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _PIPELINE = module
    return _PIPELINE


_CONFIG: dict[str, Any] | None = None


def release_config() -> dict[str, Any]:
    global _CONFIG
    if _CONFIG is None:
        try:
            loaded = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log(f"release pipeline config unreadable at {RELEASE_CONFIG} ({exc}); "
                "review rules and auto-live are unavailable")
            loaded = {}
        _CONFIG = loaded if isinstance(loaded, dict) else {}
    return _CONFIG


def lane_config(repo: str) -> tuple[str, dict[str, Any]] | None:
    for lane in ("worker", "app"):
        entry = release_config().get(lane)
        if isinstance(entry, dict) and entry.get("github_repo") == repo:
            return lane, entry
    return None


def review_rules(repo: str) -> dict[str, Any]:
    """The repository's lane review rules; a repo with no lane uses the
    worker's, the pipeline's reference contract."""
    found = lane_config(repo)
    if found:
        return found[1]
    worker = release_config().get("worker")
    if not isinstance(worker, dict):
        raise RuntimeError("release pipeline review rules unavailable")
    return worker


def review_verdict(payload: dict[str, Any], repo: str = DEFAULT_PR_REPO) -> str:
    """BLOCK, APPROVE (exact head), or Not recorded."""
    pipeline = release_pipeline()
    rules = review_rules(repo)
    comments = [{"body": str(c.get("body") or ""), "author_association": c.get("authorAssociation"),
                 "user": {"login": (c.get("author") or {}).get("login")}, "created_at": c.get("createdAt"),
                 "id": index}
                for index, c in enumerate(payload.get("comments") or []) if isinstance(c, dict)]
    last = pipeline.deciding_verdict(comments, rules)
    if last is None:
        return "Not recorded"
    if pipeline.verdict(last["body"], rules) == "block":
        return "BLOCK"
    head = str(payload.get("headRefOid") or "").lower()
    return "APPROVE" if SHA_RE.fullmatch(head) and pipeline.reviewed_header_sha(last["body"]) == head \
        else "Not recorded"


FAILING_CHECKS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
PASSING_CHECKS = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def check_outcome(check: dict[str, Any]) -> str:
    return str(check.get("conclusion") or check.get("state") or "").upper()


def failing_check_names(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("statusCheckRollup")
    checks = [check for check in raw if isinstance(check, dict)] if isinstance(raw, list) else []
    return [str(check.get("name") or check.get("context") or "unnamed check")
            for check in checks if check_outcome(check) in FAILING_CHECKS]


def derived_pr_state(payload: dict[str, Any], repo: str = DEFAULT_PR_REPO) -> tuple[str, str, str]:
    """GitHub's view of a PR as (status, stage, phase). Draft is build; open
    with checks running is CI; failing checks, a conflict, changes requested
    or a BLOCK verdict are blocked; everything else waits on review."""
    state = str(payload.get("state") or "").upper()
    if state == "MERGED":
        return "done", "merged", "Merged"
    if state == "CLOSED":
        return "failed", "ci", "Closed unmerged"
    if payload.get("isDraft"):
        return "running", "build", "Draft"
    raw = payload.get("statusCheckRollup")
    checks = [check for check in raw if isinstance(check, dict)] if isinstance(raw, list) else []
    if failing_check_names(payload):
        return "blocked", "ci", "Checks failing"
    if str(payload.get("mergeable") or "").upper() == "CONFLICTING":
        return "blocked", "review", "Merge conflict"
    if str(payload.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        return "blocked", "review", "Changes requested"
    if any(check_outcome(check) not in PASSING_CHECKS for check in checks):
        return "running", "ci", "CI"
    verdict = review_verdict(payload, repo)
    if verdict == "BLOCK":
        return "blocked", "review", "Review blocked"
    if verdict == "APPROVE":
        return "review", "review", "Ready to merge"
    if str(payload.get("reviewDecision") or "").upper() == "APPROVED":
        return "review", "review", "Approved"
    return "review", "review", "Awaiting review"


def derived_block(payload: dict[str, Any], phase: str) -> tuple[str, str] | None:
    if phase == "Checks failing":
        names = failing_check_names(payload)
        return f"Failing checks: {', '.join(names)}", PHASE_BLOCKS[phase][1]
    return PHASE_BLOCKS.get(phase)


# ── verified releases ────────────────────────────────────────────────────────
# A merged card is Live only when its merge commit is an ancestor of the latest
# verified release for its repository: the newest `shipped` row the release
# pipeline wrote for that repo's lane, or, when the pipeline has none yet, the
# SHA the live release endpoint serves.

RELEASE_CACHE: dict[str, Any] = {}
# Why the live release probe could not verify a repository's release.
RELEASE_ERRORS: dict[str, str] = {}


def release_lanes() -> dict[str, dict[str, str]]:
    config = release_config()
    lanes = {}
    for lane in ("worker", "app"):
        entry = config.get(lane)
        if isinstance(entry, dict) and isinstance(entry.get("github_repo"), str):
            lanes[lane] = {"repo": entry["github_repo"], "url": str(entry.get("live_release_url") or "")}
    return lanes


def releases_path() -> Path:
    configured = os.environ.get("PROGRESS_BOARD_RELEASES")
    return Path(configured) if configured else REPO_ROOT / "out" / "release-pipeline" / "releases.jsonl"


def release_rows(repo: str) -> list[dict[str, Any]]:
    """The pipeline's rows for this repo's lanes, oldest first."""
    lanes = {lane for lane, entry in release_lanes().items() if entry["repo"] == repo}
    try:
        lines = releases_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("lane") in lanes:
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("ts") or ""))


def probe_sha(lane: str, payload: dict[str, Any]) -> str | None:
    """The released SHA a probe reports; None for a non-production app;
    ValueError for any other shape."""
    if lane == "worker":
        field = payload.get("git_sha")
        value = field.get("value") if isinstance(field, dict) else None
    else:
        if payload.get("environment") != "production":
            return None
        value = payload.get("source_commit")
    if not isinstance(value, str) or not SHA_RE.fullmatch(value.lower()):
        raise ValueError(f"the {lane} release probe returned an invalid release shape")
    return value.lower()


def probe_json(url: str) -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "carr-progress-board"})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 — fixed https config URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def latest_release(repo: str) -> dict[str, Any] | None:
    if repo in RELEASE_CACHE:
        return RELEASE_CACHE[repo]
    release = None
    shipped = [row for row in release_rows(repo)
               if row.get("status") == "shipped" and SHA_RE.fullmatch(str(row.get("sha") or "").lower())]
    if shipped:
        row = shipped[-1]
        release = {"sha": str(row["sha"]).lower(), "lane": row.get("lane"), "ts": row.get("ts"),
                   "source": "releases.jsonl"}
    elif not os.environ.get("PROGRESS_BOARD_SKIP_PROBE"):
        for lane, entry in release_lanes().items():
            if entry["repo"] != repo or not entry["url"].startswith("https://"):
                continue
            payload = probe_json(entry["url"])
            if payload is None:
                RELEASE_ERRORS[repo] = f"the {lane} release probe was unreachable"
                continue
            try:
                sha = probe_sha(lane, payload)
            except ValueError as exc:
                RELEASE_ERRORS[repo] = str(exc)
                log(f"{repo}: {exc}; release left unverified")
                continue
            if sha:
                release = {"sha": sha, "lane": lane, "ts": None, "source": "live probe"}
                break
    RELEASE_CACHE[repo] = release
    return release


def release_wait_reason(repo: str) -> str:
    """Why a merged commit is not live yet, in the pipeline's own words."""
    rows = release_rows(repo)
    shipped_ts = max((str(r.get("ts") or "") for r in rows if r.get("status") == "shipped"), default="")
    later = [r for r in rows if str(r.get("ts") or "") > shipped_ts and r.get("status") != "shipped"]
    if later:
        row = later[-1]
        status = str(row.get("status") or "unknown")
        detail = str(row.get("detail") or "").strip()
        tail = f": {detail[:200]}" if detail else ""
        if status == "blocked":
            return f"release pipeline blocked ({row.get('reason') or 'no reason'}){tail}"
        if status == "failed":
            return f"release pipeline failed at {row.get('step') or row.get('failed_step') or 'unknown step'}{tail}"
        if status == "no_release_needed":
            return "latest batch was doc/test-only; waiting for the next release"
        return f"release pipeline {status}{tail}"
    release = latest_release(repo)
    if release:
        return f"waiting for the next release after {release['sha'][:12]}"
    if repo in RELEASE_ERRORS:
        return f"release unverified: {RELEASE_ERRORS[repo]} (release probe)"
    return "no verified release recorded yet"


def ancestry_cache_path() -> Path:
    return board_dir() / "release-ancestry.json"


def compare_status(repo: str, base: str, head: str) -> str | None:
    """GitHub's compare status of head against base; `behind` or `identical`
    means head is an ancestor of base. Commit ancestry never changes, so every
    answer is cached."""
    key = f"{repo}:{base}...{head}"
    path = ancestry_cache_path()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if isinstance(cache, dict) and isinstance(cache.get(key), str):
        return cache[key]
    try:
        status = gh_text(["api", f"repos/{repo}/compare/{base}...{head}", "--jq", ".status"], timeout=15).strip()
    except RuntimeError:
        return None
    if status not in {"ahead", "behind", "identical", "diverged"}:
        return None
    cache = cache if isinstance(cache, dict) else {}
    cache[key] = status
    atomic_write(path, json.dumps(cache, indent=1, sort_keys=True) + "\n")
    return status


def changed_paths(payload: dict[str, Any]) -> list[str] | None:
    """The PR's complete changed-file list, or None if gh gave less."""
    if not valid_files(payload, required=True):
        return None
    paths = [str(entry["path"]) for entry in payload["files"]]
    return paths if len(paths) == payload["changedFiles"] and all(paths) else None


def release_scope_gap(repo: str, paths: list[str] | None) -> str | None:
    """None when the repository's release lane deploys every runtime path the
    PR changed; otherwise why a release cannot certify it. Doc and test paths
    the lane declares non-release carry no runtime."""
    if paths is None:
        return "the PR's changed-file list is unavailable or incomplete"
    found = lane_config(repo)
    if found is None:
        return f"{repo} has no release lane"
    lane, entry = found
    prefixes = entry.get("release_paths")
    ignore = entry.get("non_release_globs") or []
    hit = release_pipeline()._glob_hit
    outside = [path for path in paths
               if prefixes is not None and not any(path == x.rstrip("/") or path.startswith(x) for x in prefixes)
               and not any(hit(path, glob) for glob in ignore)]
    if outside:
        shown = ", ".join(outside[:3]) + (f" and {len(outside) - 3} more" if len(outside) > 3 else "")
        return f"changes outside the {lane} release paths ({shown})"
    return None


def auto_live(task: dict[str, Any], repo: str, at: str, paths: list[str] | None) -> bool:
    """Move a merged card to Live once its merge commit is in a verified
    release of a lane that deploys everything the PR changed. Anything else
    (a local tool, a LaunchAgent) waits for an operational receipt."""
    merge = str(task.get("merge_sha") or "")
    if task_stage(task) != "merged" or not SHA_RE.fullmatch(merge):
        return False
    gap = release_scope_gap(repo, paths)
    if gap:
        wait = (f"{gap}; a release cannot make it Live, it needs an operational receipt "
                "(task --stage live --evidence)")
        if task.get("release_wait") != wait:
            task["release_wait"] = wait
            return True
        return False
    release = latest_release(repo)
    if release and (release["sha"] == merge or compare_status(repo, release["sha"], merge) in {"behind", "identical"}):
        prior = task_stage(task)
        where = (f"the verified {release['lane']} release {release['sha'][:12]}"
                 + (f" shipped {release['ts']}" if release.get("ts") else f" ({release['source']})"))
        task.update({"status": "done", "stage": "live", "completed_at": at, "updated_at": at,
                     "evidence": f"Merged commit {merge[:12]} is in {where}"})
        task.pop("release_wait", None)
        record_stage(task, at, prior)
        normalize_task(task)
        return True
    wait = release_wait_reason(repo)
    if task.get("release_wait") != wait:
        task["release_wait"] = wait
        return True
    return False


def delivered_live(task: dict[str, Any], repo: str, at: str, evidence: str | None) -> bool:
    """Move a project card from Merged to Live only when its declared delivery
    target is the one this repository's release deploys (worker or app) and
    the production source readback shows the change. Any other target, or
    none, waits for measured evidence (task --stage live --evidence)."""
    if task_stage(task) != "merged":
        return False
    target = task.get("delivery_target")
    automatic = AUTOMATIC_DELIVERY_TARGETS.get(repo)
    if automatic and target == automatic and evidence:
        prior = task_stage(task)
        task.update({"status": "done", "stage": "live", "completed_at": at, "updated_at": at,
                     "evidence": evidence})
        task.pop("release_wait", None)
        record_stage(task, at, prior)
        normalize_task(task)
        return True
    if automatic and target == automatic:
        wait = f"production does not show this change yet; {release_wait_reason(repo)}"
    elif target:
        wait = (f"a release does not complete the {target} delivery target; "
                "it needs measured evidence (task --stage live --evidence)")
    else:
        wait = ("no delivery target recorded; set --delivery-target (worker or app complete from the "
                "release readback) or record measured evidence (task --stage live --evidence)")
    if task.get("release_wait") != wait:
        task["release_wait"] = wait
        return True
    return False


# ── the system-wide board ────────────────────────────────────────────────────

def branch_executor(branch: str, author: str) -> str:
    value = branch.lower()
    if value.startswith("claude/"):
        return "Claude cloud"
    if "codex" in value:
        return "Codex"
    if "grok" in value:
        return "Grok"
    if "flash" in value:
        return "Flash Next"
    return author or "unassigned"


def open_pr_state(repo: str, pr: dict[str, Any]) -> tuple[str, str, str]:
    return derived_pr_state({**pr, "state": "OPEN"}, repo)


def pr_card(repo: str, pr: dict[str, Any], merged: bool) -> dict[str, Any]:
    author = str((pr.get("author") or {}).get("login") or "") if isinstance(pr.get("author"), dict) else ""
    branch = str(pr.get("headRefName") or "")
    if merged:
        status, stage, phase = "done", "merged", "Merged"
    else:
        status, stage, phase = open_pr_state(repo, pr)
    card: dict[str, Any] = {
        "title": str(pr.get("title") or f"PR {pr.get('number')}")[:200],
        "summary": pr_summary(pr.get("body")),
        "repo": repo, "pr": int(pr["number"]),
        "url": str(pr.get("url") or f"https://github.com/{repo}/pull/{int(pr['number'])}"),
        "author": author, "executor": branch_executor(branch, author), "branch": branch,
        "pr_head": str(pr.get("headRefOid") or ""),
        "created_at": pr.get("createdAt"), "updated_at": pr.get("updatedAt") or pr.get("createdAt"),
        "status": status, "stage": stage, "pr_phase": phase,
    }
    if not merged and isinstance(pr.get("statusCheckRollup"), list):
        card["pr_checks"] = checks_summary(pr)
        card["review_verdict"] = review_verdict(pr, repo)
    block = derived_block(pr, phase) if status == "blocked" else None
    if block:
        card.update({"blocked_reason": block[0], "next_action": block[1], "blocked_source": "github"})
    if merged:
        card["merged_at"] = pr.get("mergedAt")
        sha = merge_sha(pr)
        if sha:
            card["merge_sha"] = sha
    return {key: value for key, value in card.items() if value is not None}


def list_repositories(prior: list[str]) -> list[str]:
    try:
        # The authenticated endpoint includes private repositories too.
        rows = rest_rows("user/repos?affiliation=owner")
        names = [str(row["full_name"]) for row in rows if not row.get("archived")
                 and str(row.get("full_name") or "").startswith(GITHUB_OWNER + "/")]
    except (RuntimeError, TypeError, KeyError):
        names = prior
    extra = sorted({name for name in names if name not in CORE_REPOS and re.fullmatch(r"[\w.-]+/[\w.-]+", name)})
    return [*CORE_REPOS, *extra]


def read_repository(repo: str, since: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """REST discovery plus shared hydration. Only authenticated merged facts
    are immutable; closed-unmerged identities can reopen."""
    assert GITHUB_PASS is not None
    cursor_path = board_dir() / ".github-discovery.json"
    cursor = read_json_file(cursor_path).get(repo) or since + "T00:00:00Z"
    # GitHub timestamps have second resolution. Overlap the cursor by one
    # second so a closure during this read cannot fall between two polls.
    started = (now_utc() - timedelta(seconds=1)).isoformat(timespec="seconds")
    open_prs = []
    for raw in rest_rows(f"repos/{repo}/pulls?state=open&sort=updated&direction=desc"):
        if not isinstance(raw.get("number"), int):
            raise RuntimeError("gh returned a malformed open PR")
        info, error = GITHUB_PASS.read(raw["number"], repo, raw)
        if error or info is None or not valid_open_pr(info):
            raise RuntimeError(error or "gh returned a malformed open PR")
        if info["state"] == "OPEN":
            open_prs.append(info)
    # Issues support updated-since; pulls do not. They supply identities only.
    # Each new closed identity is hydrated once to distinguish merged/closed.
    query = urlencode({"state": "closed", "since": cursor, "sort": "updated", "direction": "asc"})
    for issue in rest_rows(f"repos/{repo}/issues?{query}"):
        if not issue.get("pull_request"):
            continue
        if not isinstance(issue.get("number"), int):
            raise RuntimeError("gh returned a malformed closed PR identity")
        info, error = GITHUB_PASS.read(issue["number"], repo, issue)
        if error or info is None:
            raise RuntimeError(error or "gh returned a malformed closed PR")
        if info["state"] == "OPEN":
            # Do not advance past an unaccounted closure (including GitHub
            # replication lag). A later sweep must rediscover this identity.
            raise RuntimeError("closed discovery disagrees with PR detail")
    GITHUB_PASS.reconcile()
    # Migrate legacy facts once, including unknown merge dates, before applying
    # the recent-merge filter or verifying delivery from the changed paths.
    for identity, info in list(GITHUB_PASS.saved.items()):
        if identity.startswith(repo + "#") and info.get("state") == "MERGED" and info.get("_legacy_terminal"):
            _, error = GITHUB_PASS.read(info["number"], repo)
            if error:
                raise RuntimeError(error)
    GITHUB_PASS.reconcile()
    open_prs = [GITHUB_PASS.saved.get(f"{repo}#{info['number']}", info) for info in open_prs]
    open_prs = [info for info in open_prs if info["state"] == "OPEN"]
    merged_prs = [info for identity, info in GITHUB_PASS.saved.items()
                  if identity.startswith(repo + "#") and info.get("state") == "MERGED"
                  and str(info.get("mergedAt") or "") >= since]
    for info in merged_prs:
        if not isinstance(info.get("number"), int):
            raise RuntimeError("cached merged PR identity is malformed")
    with board_lock("github-discovery"):
        cursors = read_json_file(cursor_path)
        cursors[repo] = max(str(cursors.get(repo) or ""), started)
        atomic_write(cursor_path, json.dumps(cursors, sort_keys=True) + "\n")
    return open_prs, merged_prs


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@with_github_read_pass
def build_all_repos() -> dict[str, Any]:
    """Rebuild the all-repos board from gh. A repo that cannot be read, or
    returns a malformed or incomplete list, keeps its previous cards and
    names the error, including a complete outage. GitHub is read before the
    board lock is taken; the merge into the board happens under it."""
    path = state_path(ALL_REPOS_BOARD)
    generation, unlocked = begin_refresh(ALL_REPOS_BOARD)
    assert GITHUB_PASS is not None
    GITHUB_PASS.seed((unlocked.get("tasks") or {}).values())
    prior_repos = [str(row.get("repo")) for row in unlocked.get("repos") or [] if isinstance(row, dict)]
    since = (now_utc() - RECENT_MERGED).date().isoformat()
    reads: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]] | str] = {}
    for repo in list_repositories(prior_repos):
        try:
            reads[repo] = read_repository(repo, since)
        except RuntimeError as exc:
            reads[repo] = str(exc)[:200]
    for repo, result in reads.items():
        if not isinstance(result, str):
            latest_release(repo)  # any live probe happens before the lock
    with board_lock(ALL_REPOS_BOARD):
        prior = read_json_file(path)
        if refresh_generation(ALL_REPOS_BOARD) != generation:
            return prior
        state = assemble_all_repos(prior, reads)
        write_json(state)
    return state


def assemble_all_repos(prior: dict[str, Any],
                       reads: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]] | str]) -> dict[str, Any]:
    raw_tasks = prior.get("tasks")
    prior_tasks: dict[str, Any] = raw_tasks if isinstance(raw_tasks, dict) else {}
    at = stamp()
    tasks: dict[str, dict[str, Any]] = {}
    rows = []
    failed = []
    for repo, result in reads.items():
        if isinstance(result, str):
            kept = {key: task for key, task in prior_tasks.items() if task.get("repo") == repo}
            tasks.update(kept)
            rows.append({"repo": repo, "open": sum(task_stage(t) not in {"merged", "live"} for t in kept.values()),
                         "merged": sum(task_stage(t) in {"merged", "live"} for t in kept.values()),
                         "error": result})
            failed.append({"repo": repo, "error": result})
            continue
        open_prs, merged_prs = result
        for merged, prs in ((False, open_prs), (True, merged_prs)):
            for pr in prs:
                key = card_key(repo, pr["number"])
                card = pr_card(repo, pr, merged)
                previous = prior_tasks.get(key) or {}
                if merged and pr.get("_legacy_terminal") and previous:
                    card = dict(previous)
                if task_stage(previous) == "live" and merged:
                    for field in ("status", "stage", "evidence", "completed_at"):
                        card[field] = previous[field]
                history = previous.get("stage_history")
                if history:
                    card["stage_history"] = history
                    card["stage_entered_at"] = previous.get("stage_entered_at")
                    record_stage(card, at, task_stage(previous))
                else:
                    entered = card.get("merged_at") if merged else card.get("updated_at")
                    record_stage(card, str(entered or at), None)
                normalize_task(card)
                auto_live(card, repo, at, changed_paths(pr) if merged else None)
                tasks[key] = card
        rows.append({"repo": repo, "open": len(open_prs), "merged": len(merged_prs)})
    raw_sync = prior.get("github_sync")
    previous_sync: dict[str, Any] = raw_sync if isinstance(raw_sync, dict) else {}
    return {
        "version": 2, "project": ALL_REPOS_BOARD, "title": "All repositories", "kind": "all-repos",
        "created_at": prior.get("created_at") or at,
        "updated_at": max((str(t.get("updated_at") or "") for t in tasks.values()), default=at) or at,
        "tasks": tasks, "questions": {}, "deliverables": [], "notes": [], "repos": rows,
        "github_sync": {"checked_at": at, "failed": failed, "stale": bool(failed),
                        "last_verified_at": at if not failed else previous_sync.get("last_verified_at")},
    }


# ── project boards ───────────────────────────────────────────────────────────

# Fields whose change is a real state change: only these stamp updated_at, so
# the stage timer and the stale flag read true. Check counts, head and verdict
# refresh silently.
DERIVED_STATE = ("status", "stage", "pr_phase", "blocked_reason", "next_action")


def manual_block_holds(task: dict[str, Any], info: dict[str, Any], derived_status: str) -> bool:
    """A blocked note the orchestrator wrote stays until GitHub shows it
    cleared: the PR merged or closed, or a new head was pushed that is not
    itself blocked. (Legacy blocks without a source count as manual.)"""
    if task.get("status") != "blocked" or task.get("blocked_source", "manual") != "manual":
        return False
    if not task.get("blocked_reason"):
        return False
    if str(info.get("state") or "").upper() in {"MERGED", "CLOSED"}:
        return False
    pinned = str(task.get("blocked_head") or task.get("pr_head") or "")
    moved = bool(pinned) and str(info.get("headRefOid") or "") != pinned
    return not (moved and derived_status != "blocked")


def sync_pr_task(task: dict[str, Any], info: dict[str, Any], at: str) -> bool:
    """Bring one PR card to GitHub's state. Returns True if anything changed."""
    status, stage, phase = derived_pr_state(info, task_repo(task))
    floor = task.get("manual_stage")
    current = task_stage(task)
    if current == "live":
        status, stage = "done", "live"
    elif floor in PIPELINE_STAGES and PIPELINE_STAGES.index(stage) < PIPELINE_STAGES.index(floor):
        stage = floor  # never move a card back past a later stage set by hand
    target: dict[str, Any] = {"status": status, "stage": stage, "pr_phase": phase,
                              "blocked_reason": None, "next_action": None, "blocked_source": None}
    if manual_block_holds(task, info, status):
        target.update({"status": "blocked", "blocked_reason": task["blocked_reason"],
                       "next_action": task.get("next_action"), "blocked_source": "manual",
                       "blocked_head": task.get("blocked_head") or task.get("pr_head") or info.get("headRefOid")})
    elif status == "blocked" and current != "live":
        block = derived_block(info, phase)
        if block:
            target.update({"blocked_reason": block[0], "next_action": block[1], "blocked_source": "github"})
    if target["blocked_source"] != "manual":
        target["blocked_head"] = None
    facts: dict[str, Any] = {"pr_checks": checks_summary(info), "pr_head": info.get("headRefOid") or "",
                             "review_verdict": review_verdict(info, task_repo(task))}
    if info.get("_legacy_terminal"):
        facts = {"pr_head": task.get("pr_head") or info.get("headRefOid") or ""}
    if merge_sha(info):
        facts["merge_sha"] = merge_sha(info)
    stamped = any(task.get(field) != target.get(field) for field in DERIVED_STATE)
    before = json.dumps(task, sort_keys=True)
    for field, value in {**target, **facts}.items():
        if value is None:
            task.pop(field, None)
        else:
            task[field] = value
    if stamped:
        task["updated_at"] = at
        record_stage(task, at, current)
    normalize_task(task)
    return json.dumps(task, sort_keys=True) != before


def settle_done_without_pr(task: dict[str, Any]) -> bool:
    """Store a finished no-PR card as Live, keeping its own times."""
    if task.get("status") != "done" or task.get("pr") is not None:
        return False
    evidence = task.get("evidence")
    settled = {"stage": "live",
               "evidence": evidence if isinstance(evidence, str) and evidence.strip() else DONE_WITHOUT_PR_EVIDENCE,
               "completed_at": task.get("completed_at") or task.get("updated_at") or stamp()}
    if all(task.get(field) == value for field, value in settled.items()):
        return False
    task.update(settled)
    return True


def deployed_release(repo: str) -> tuple[Path, str, str] | None:
    """Read where this repository runs. Pipeline cursors and main are not deployment proof."""
    target = RELEASE_TARGETS.get(repo)
    if target is None:
        return None
    root, url = target
    try:
        with urlopen(Request(url, headers={"User-Agent": "carr-progress-board"}), timeout=5) as response:
            live = json.load(response)
        if not isinstance(live, dict):
            return None
        if repo == DEFAULT_PR_REPO:
            if live.get("ok") is not True or (live.get("env") or {}).get("value") != "production":
                return None
            sha = (live.get("git_sha") or {}).get("value")
        else:
            if live.get("service") != "doctorcre-app" or live.get("environment") != "production":
                return None
            sha = live.get("source_commit")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            return None
        return root, sha, url
    except (OSError, ValueError, AttributeError, HTTPException):
        return None


def deployment_evidence(info: dict[str, Any], release: tuple[Path, str, str] | None) -> str | None:
    merge = info.get("mergeCommit")
    commit = merge.get("oid") if isinstance(merge, dict) else None
    if release is None or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return None
    root, sha, url = release
    try:
        result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, sha],
                                capture_output=True, timeout=5, check=False)
        if result.returncode == 0:
            paths = subprocess.run(["git", "-C", str(root), "show", "--format=", "--name-only", "-z",
                                    "--diff-merges=first-parent", commit],
                                   capture_output=True, timeout=5, check=True).stdout.split(b"\0")
            # Initial completion needs the delivered change still present. If
            # later edits affect these files, leave completion to measured proof.
            changed_paths = [os.fsdecode(path) for path in paths if path]
            if not changed_paths:
                return None
            delivered = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "--no-ext-diff",
                                        "--no-textconv", commit, sha, "--",
                                        *[f":(literal){path}" for path in changed_paths]],
                                       capture_output=True, timeout=5, check=False)
            if delivered.returncode == 0:
                return f"GET {url} observed production source {sha}; merged commit {commit} is an ancestor and its changed files match the deployed tree; verified {stamp()}"
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def refresh_generation(project: str) -> int:
    """Read the generation from the lock file while holding the board lock.
    An empty pre-existing lock file represents a board not yet refreshed."""
    return int((board_dir() / f"{safe_project(project)}.lock").read_text() or "0")


def begin_refresh(project: str) -> tuple[int, dict[str, Any]]:
    """Reserve a board generation before reading external evidence. Only the
    latest started refresh may commit; local mutations keep this generation.
    The reservation and the input snapshot are one locked transaction."""
    with board_lock(project) as lock_fd:
        state = read_json_file(state_path(project)) if project == ALL_REPOS_BOARD else read_state(project)
        generation = refresh_generation(project) + 1
        # Reservations belong to the transaction metadata, not the published
        # board: a failed fetch must leave the last known board untouched.
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, str(generation).encode("ascii"))
        os.fsync(lock_fd)
        return generation, state


@with_github_read_pass
def render(project: str) -> None:
    """Sync every PR card from GitHub, refresh release and health facts, and
    write the JSON. GitHub is read first, without the board lock; the result
    is applied to a fresh read under the lock, so a note or task written
    meanwhile is kept. A superseded refresh cannot replace newer evidence.
    A gh failure never stops the run: the card keeps its
    last known state and github_sync names the failure, when it was checked
    and when every card was last verified. The name is kept for the launchd
    job; nothing here renders a page."""
    if project == ALL_REPOS_BOARD:
        build_all_repos()
        return
    generation, initial = begin_refresh(project)
    tasks = initial.get("tasks", {}).values()
    assert GITHUB_PASS is not None
    GITHUB_PASS.seed(tasks)
    keys = {pr_key(task) for task in tasks if task.get("pr") is not None}
    fetched = {key: fetch_pr(key[1], key[0]) for key in sorted(keys)}
    # Every network and git read happens here, before the lock: the release
    # readback for cards whose delivery target a release completes, and the
    # pipeline's reason for the rest.
    targeted = {pr_key(task) for task in tasks if task.get("pr") is not None
                and task.get("delivery_target") == AUTOMATIC_DELIVERY_TARGETS.get(pr_key(task)[0])}
    releases: dict[str, tuple[Path, str, str] | None] = {}
    evidence: dict[tuple[str, int], str | None] = {}
    for key, (info, _) in fetched.items():
        if info is None or info.get("state") != "MERGED":
            continue
        latest_release(key[0])
        if key in targeted:
            if key[0] not in releases:
                releases[key[0]] = deployed_release(key[0])
            evidence[key] = deployment_evidence(info, releases[key[0]])
    with board_lock(project):
        state = read_state(project)
        if refresh_generation(project) != generation:
            return
        if apply_sync(state, fetched, evidence):
            state["updated_at"] = max((str(task.get("updated_at") or "") for task in state["tasks"].values()),
                                      default=state.get("updated_at"))
            write_json(state)
    # The retired static page: never leave a stale copy to be mistaken for a board.
    (board_dir() / f"{safe_project(project)}.html").unlink(missing_ok=True)


def apply_sync(state: dict[str, Any],
               fetched: dict[tuple[str, int], tuple[dict[str, Any] | None, str | None]],
               evidence: dict[tuple[str, int], str | None] | None = None) -> bool:
    changed = False
    failed: list[dict[str, str]] = []
    synced = 0
    at = now_utc().isoformat(timespec="microseconds")
    for task_id, task in state.get("tasks", {}).items():
        if settle_done_without_pr(task):
            changed = True
        if normalize_task(task):
            changed = True
        if task.get("pr") is None:
            continue
        key = pr_key(task)
        if key not in fetched:
            continue  # added after GitHub was read; the next run syncs it
        info, error = fetched[key]
        if info is None or error:
            label = f"{key[0].split('/', 1)[1]}#{key[1]}"
            log(f"sync {label} ({task_id}) failed, keeping last known state: {error}")
            failed.append({"card": task_id, "pr": label, "error": str(error)[:200]})
            continue
        synced += 1
        if sync_pr_task(task, info, at):
            changed = True
        if delivered_live(task, task_repo(task), at, (evidence or {}).get(key)):
            changed = True
    if fetched and not os.environ.get("PROGRESS_BOARD_SKIP_GH"):
        raw_sync = state.get("github_sync")
        previous: dict[str, Any] = raw_sync if isinstance(raw_sync, dict) else {}
        state["github_sync"] = {"checked_at": at, "synced": synced, "failed": failed, "stale": bool(failed),
                                "last_verified_at": at if not failed else previous.get("last_verified_at")}
        changed = True
    return changed


def local_only() -> bool:
    return bool(os.environ.get("PROGRESS_BOARD_LOCAL_ONLY"))


def refresh_and_publish(project: str) -> None:
    """Every mutation reaches the app board, the only board UI. A failed
    publication is loud: the local state is kept and the retry is named."""
    render(project)
    if local_only():
        log(f"{project}: saved locally; not published to the app board (PROGRESS_BOARD_LOCAL_ONLY is set)")
        return
    try:
        publish_board(project)
    except RuntimeError as exc:
        raise SystemExit(f"progress-board: {project} saved locally but not published to the app board: {exc}. "
                         f"Retry: tools/progress_board.py render {project} --publish")


def mutate(project: str, change: Callable[[dict[str, Any]], bool | None]) -> None:
    """Read, change and write one board as a single locked transaction, then
    refresh and publish it."""
    with board_lock(project):
        state = read_state(project)
        if change(state) is False:
            return
        state["updated_at"] = stamp()
        write_json(state)
    refresh_and_publish(project)


def call_verb(verb: str, args: dict[str, Any]) -> dict[str, Any]:
    """Use the existing noninteractive local-token route; no model is involved.
    Anything short of an explicit ok:true reply raises."""
    record_call = repo_lib("record_call")
    result = record_call.call_verb(verb, args, timeout=30)
    payload = result.reply
    if not result.ok and result.kind != record_call.REFUSED:
        raise RuntimeError(result.describe())
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"{verb} refused: {payload.get('error', 'unknown result') if isinstance(payload, dict) else 'invalid result'}")
    return payload


def stable_key(verb: str, args: dict[str, Any]) -> str:
    body = json.dumps([verb, args], sort_keys=True, separators=(",", ":"))
    return "board-" + hashlib.sha256(body.encode()).hexdigest()




class SnapshotTooLarge(RuntimeError):
    """The board's untrimmable content alone exceeds the server limit."""


def snapshot_size(snapshot: dict[str, Any]) -> int:
    """JSON.stringify(snapshot).length, as publish-board-snapshot measures it."""
    text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-16-le")) // 2


def fit_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Trim until the whole payload fits: oldest Live cards first, then oldest
    Merged cards, then the oldest History rows. Everything in flight, notes,
    decisions and the ledger are never dropped; if they alone are too large
    the snapshot is refused rather than published short."""
    tasks, history = snapshot["tasks"], snapshot["history"]

    def age(task: dict[str, Any]) -> str:
        return str(task.get("completed_at") or task.get("merged_at") or task.get("updated_at") or "")
    order = [("live", "tasks", key) for key in sorted(
                 (k for k, t in tasks.items() if task_stage(t) == "live"), key=lambda k: (age(tasks[k]), k))]
    order += [("merged", "tasks", key) for key in sorted(
                 (k for k, t in tasks.items() if task_stage(t) == "merged"), key=lambda k: (age(tasks[k]), k))]
    order += [("history", "history", key) for key in sorted(
                 history, key=lambda k: (str(history[k].get("updated_at") or ""), k))]
    size = snapshot_size(snapshot)
    index = 0
    while size > SNAPSHOT_BUDGET and index < len(order):
        freed, excess = 0, size - SNAPSHOT_BUDGET
        while freed < excess and index < len(order):
            kind, section, key = order[index]
            index += 1
            entry = snapshot[section].pop(key)
            freed += snapshot_size({key: entry}) - 1
            snapshot["omitted"][kind] += 1
        size = snapshot_size(snapshot)
    if size > SNAPSHOT_BUDGET:
        raise SnapshotTooLarge(f"board {snapshot.get('project')} snapshot is {size} characters after trimming "
                               f"every Live, Merged and History entry; the publication budget is {SNAPSHOT_BUDGET} "
                               f"and the server limit is {SNAPSHOT_LIMIT}")
    return snapshot


@functools.cache
def cost_snapshot_reader() -> Callable:
    """Use the collector's validation contract without importing a provider client."""
    path = Path(__file__).resolve().with_name("system_costs.py")
    spec = importlib.util.spec_from_file_location("carr_system_costs", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("system cost snapshot reader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.load_snapshot


def board_snapshot(state: dict[str, Any], *, costs=None) -> dict[str, Any]:
    """The versioned data contract the app page renders, including fresh local
    cost evidence. Full diagnostics stay local; the app receives bounded cards."""
    tasks = {}
    all_tasks = state.get("tasks") or {}
    for task_id, task in all_tasks.items():
        if is_retired(task):
            continue
        provider, model, effort = task_identity(task)
        card = {key: value for key, value in task.items()
                if key in SNAPSHOT_TASK_FIELDS and value is not None}
        reason = card.get("blocked_reason")
        if isinstance(reason, str) and len(reason) > BLOCKER_EXCERPT_LIMIT:
            head = BLOCKER_EXCERPT_LIMIT // 2
            card["blocked_reason"] = reason[:head] + "…" + reason[-(BLOCKER_EXCERPT_LIMIT - head - 1):]
        tasks[task_id] = {**card, "provider": provider, "model": model, "effort": effort}
    decisions = [
        {"id": qid, "question": q.get("question"), "answer": q.get("answer"), "default": q.get("default"),
         "answered_at": q.get("answered_at") or q.get("updated_at")}
        for qid, q in (state.get("questions") or {}).items() if q.get("answer")
    ]
    return fit_snapshot({
        "schema": SNAPSHOT_SCHEMA,
        "kind": state.get("kind") or "project",
        "project": state["project"],
        "title": state.get("title") or state["project"],
        "tasks": tasks,
        "history": {
            task_id: {"title": task.get("title", task_id), "status": task.get("status"),
                      "reason": retired_reason(task), "executor": task.get("executor", "unassigned"),
                      "pr": task.get("pr"), "repo": task.get("repo"), "updated_at": task.get("updated_at")}
            for task_id, task in all_tasks.items() if is_retired(task)},
        "deliverables": list(state.get("deliverables") or [])[:24],
        "notes": list(state.get("notes") or [])[:50],
        "decisions": decisions,
        "ledger": executor_ledger(all_tasks),
        "repos": list(state.get("repos") or []),
        # When GitHub facts were last checked and verified, and what failed:
        # a card kept from before an outage is never shown as fresh.
        "github_sync": state.get("github_sync"),
        "costs": costs if costs is not None else cost_snapshot_reader()(REPO_ROOT / "out" / "system-costs.json", now=now_utc()),
        "omitted": {"live": 0, "merged": 0, "history": 0},
        "updated_at": state.get("updated_at"),
    })


def question_revision(question: dict[str, Any], project: str) -> dict[str, Any]:
    choices = question.get("choices") or []
    free_text = question.get("free_text")
    return {
        "prompt": question["question"].strip(), "choices": choices,
        "allow_free_text": free_text if isinstance(free_text, bool) else not choices,
        "default_answer": question["default"].strip() if question.get("default") is not None else None,
        "asker_ref": safe_asker_ref(question.get("asker_ref") or f"orchestrator:{project}"),
    }


def publish_external_inventory(cache: dict[str, Any]) -> dict[str, Any]:
    """Publish immutable bounded pages before switching the manifest pointer."""
    pages: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    def emit(rows):
        payload = {'items': rows}
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        board_id = 'carr-v5-external-' + digest
        before = call_verb('read-progress-board', {'board_id': board_id}).get('snapshot')
        if before is None:
            args = {'board_id': board_id, 'base_version': 0, 'snapshot': payload}
            call_verb('publish-board-snapshot', {**args, 'idempotency_key': stable_key('publish-board-snapshot', args)})
        after = call_verb('read-progress-board', {'board_id': board_id}).get('snapshot')
        if not after or after.get('snapshot_json') != payload:
            raise RuntimeError('external inventory page did not read back')
        pages.append({'board_id': board_id, 'version': int(after['version']), 'count': len(rows), 'digest': digest})
    for row in cache['items']:
        candidate = [*batch, row]
        if len(json.dumps({'items': candidate}, ensure_ascii=False)) > 120000:
            if not batch: raise RuntimeError('external inventory row exceeds page contract')
            emit(batch)
            batch = [row]
            if len(json.dumps({'items': batch}, ensure_ascii=False)) > 120000:
                raise RuntimeError('external inventory row exceeds page contract')
        else: batch = candidate
    if batch: emit(batch)
    return {**{key: value for key, value in cache.items() if key not in ('items', 'pr_heads')},
            'schema': 'system-work-external.v2', 'pages': pages, 'item_count': len(cache['items'])}


def publish_board(project: str, *, costs=None) -> dict[str, int]:
    state = read_state(project)
    board = safe_project(project)
    before = call_verb("read-progress-board", {"board_id": board})
    remote_snapshot = before.get("snapshot")
    snapshot = board_snapshot(state, costs=costs)
    if board == "carr-v5":
        from system_work_cache import cached_github
        snapshot["external_inventory"] = publish_external_inventory(cached_github(board_dir() / "system-work-github-cache.json",
            Path.home() / "carr-system/out/orch/dot/job13/report-G.md"))
    if remote_snapshot is None or remote_snapshot.get("snapshot_json") != snapshot:
        args = {"board_id": board, "base_version": int(remote_snapshot["version"]) if remote_snapshot else 0,
                "snapshot": snapshot}
        call_verb("publish-board-snapshot", {**args, "idempotency_key": stable_key("publish-board-snapshot", args)})

    remote_questions = {q["question_id"]: q for q in before.get("questions", [])}
    changed = 0
    for qid, current in state.get("questions", {}).items():
        revisions = [*current.get("history", []), current]
        expected_revision = int(current.get("revision") or 1)
        remote = remote_questions.get(qid)
        remote_revision = int(remote["revision"]) if remote else 0
        if remote_revision > expected_revision:
            raise RuntimeError(f"board question {qid} is newer on the server")
        for number, revision in enumerate(revisions, start=1):
            if number <= remote_revision:
                continue
            fields = question_revision(revision, board)
            verb = "ask-board-question" if number == 1 else "revise-board-question"
            args = {"board_id": board, "question_id": qid, "base_version": number - 1, **fields}
            call_verb(verb, {**args, "idempotency_key": stable_key(verb, args)})
            changed += 1
        if remote_revision == expected_revision and remote:
            fields = question_revision(current, board)
            if any(remote.get(key) != value for key, value in fields.items()):
                raise RuntimeError(f"board question {qid} differs on the server; revise it through ask")

    after = call_verb("read-progress-board", {"board_id": board})
    sealed = after.get("snapshot") or {}
    if sealed.get("snapshot_json") != snapshot:
        raise RuntimeError("published board snapshot did not read back")
    after_questions = {q["question_id"]: q for q in after.get("questions", [])}
    for qid, q in state.get("questions", {}).items():
        remote = after_questions.get(qid)
        if not remote or int(remote["revision"]) != int(q.get("revision") or 1):
            raise RuntimeError(f"published board question {qid} did not read back")
    return {"snapshot_version": int(sealed["version"]), "questions_changed": changed}


def answers_path(project: str) -> Path:
    return board_dir() / f"{safe_project(project)}-answers.jsonl"


def read_answer_events(project: str) -> list[dict[str, Any]]:
    path = answers_path(project)
    if not path.exists():
        return []
    contents = path.read_text(encoding="utf-8")
    if contents and not contents.endswith("\n"):
        raise RuntimeError(f"answer inbox has an incomplete trailing line: {path}")
    return [json.loads(line) for line in contents.splitlines()]


def append_answer_event(project: str, event: dict[str, Any]) -> None:
    path = answers_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short answer inbox write")
        os.fsync(fd)
    finally:
        os.close(fd)


def poll_board_answers(project: str) -> dict[str, int]:
    state = read_state(project)
    expected = {(project, qid, int(q.get("revision") or number)):
                safe_asker_ref(q.get("asker_ref") or f"orchestrator:{project}")
                for qid, current in state.get("questions", {}).items()
                for number, q in enumerate([*current.get("history", []), current], start=1)}
    refs = set(expected.values())

    def require_own_answer(answer: dict[str, Any], asker: str | None = None) -> None:
        try:
            key = (answer["board_id"], answer["question_id"], int(answer["question_revision"]))
            owner = answer["asker_ref"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("board answer does not match a local question") from exc
        if expected.get(key) != owner or (asker is not None and owner != asker):
            raise RuntimeError("board answer does not match a local question and asker")

    events = read_answer_events(project)
    seen = {event["answer"]["id"]: event["answer"] for event in events if event.get("kind") == "answer"}
    for answer in seen.values():
        require_own_answer(answer)
    acked = {event["answer_id"] for event in events if event.get("kind") == "ack"}
    new_count = ack_count = 0
    for asker in sorted(refs):
        cursor = max((int(answer["cursor"]) for answer in seen.values() if answer["asker_ref"] == asker), default=0)
        while True:
            page = call_verb("read-board-answers", {"after_cursor": cursor, "asker_ref": asker, "limit": 500})
            rows = page.get("answers", [])
            for answer in rows:
                require_own_answer(answer, asker)
                answer_cursor = int(answer["cursor"])
                if answer_cursor <= cursor:
                    raise RuntimeError("board answer cursor did not advance")
                cursor = answer_cursor
                if answer["id"] not in seen:
                    append_answer_event(project, {"kind": "answer", "answer": answer})
                    seen[answer["id"]] = answer
                    new_count += 1
            if len(rows) < 500:
                break

    for answer in sorted(seen.values(), key=lambda item: int(item["cursor"])):
        asker = answer["asker_ref"]
        if answer["id"] in acked or asker not in refs:
            continue
        # Live sessions acknowledge for themselves. The orchestrator's durable
        # inbox is the receiver for finished one-shot sessions and its own asks.
        if not asker.startswith(("one-shot:", "orchestrator:")):
            continue
        if answer.get("status") not in {"Received", "Applied"}:
            args = {"answer_id": answer["id"], "asker_ref": asker, "base_version": int(answer["version"])}
            try:
                receipt = call_verb("acknowledge-board-answer",
                                    {**args, "idempotency_key": stable_key("acknowledge-board-answer", args)})
                if receipt.get("answer", {}).get("status") != "Received":
                    raise RuntimeError("board answer Received did not read back from the write")
            except RuntimeError:
                # The server may have committed Received before the local ack
                # event was fsynced. Re-read that exact cursor before retrying.
                reread = call_verb("read-board-answers", {
                    "after_cursor": int(answer["cursor"]) - 1, "asker_ref": asker, "limit": 1,
                }).get("answers", [])
                if reread:
                    require_own_answer(reread[0], asker)
                if len(reread) != 1 or reread[0].get("id") != answer["id"] or \
                        reread[0].get("status") not in {"Received", "Applied"}:
                    raise
        append_answer_event(project, {"kind": "ack", "answer_id": answer["id"], "asker_ref": asker})
        ack_count += 1
    return {"new_answers": new_count, "acknowledged": ack_count}




@with_github_read_pass
def command_render(args: argparse.Namespace) -> None:
    if args.project == ALL_REPOS_BOARD:
        build_all_repos()
        if args.publish:
            publish_board(ALL_REPOS_BOARD)
        return
    render(args.project)
    if args.publish or args.project == LAUNCHD_BOARD:
        publish_board(args.project)
    if args.project == LAUNCHD_BOARD:
        poll_board_answers(args.project)
        publish_needs_joe_local()
        # The system-wide board rides the same scheduled job, after the
        # project board so a gh outage never holds that one back. A failed
        # rebuild is logged and the last known board is published again.
        try:
            build_all_repos()
        except RuntimeError as exc:
            log(f"all-repos rebuild failed, publishing last known state: {exc}")
            if not state_path(ALL_REPOS_BOARD).exists():
                return
        publish_board(ALL_REPOS_BOARD)


def publish_needs_joe_local() -> None:
    """Publish the machine-local half of governance-queue's needs_joe list.
    A failure is logged and left for the verb to report: it marks the page
    stale after two hours, so a dead publisher shows on Joe's list itself."""
    def pr_state(repo: str, number: int) -> dict[str, Any]:
        return gh_json(["pr", "view", str(number), "--repo", repo, "--json", "state,title"])
    try:
        import needs_joe_local

        count = needs_joe_local.publish(call_verb, stable_key, pr_state,
                                        needs_joe_local.read_source(), stamp())
        log(f"needs-joe local page published with {count} item(s)")
    except Exception as exc:
        log(f"needs-joe local page not published: {exc}")


def command_poll(args: argparse.Namespace) -> None:
    poll_board_answers(args.project)


def command_init(args: argparse.Namespace) -> None:
    if args.project == ALL_REPOS_BOARD:
        raise SystemExit("all-repos is built from gh by render; it has no init")
    created = stamp()
    state = {
        "version": 2,
        "project": safe_project(args.project),
        "title": args.title,
        "kind": "project",
        "created_at": created,
        "updated_at": created,
        "tasks": {},
        "questions": {},
        "deliverables": [],
        "notes": [],
    }
    with board_lock(args.project):
        create_json(state)
    if getattr(args, "costs", None) is not None:
        publish_board(args.project, costs=args.costs)
    else:
        refresh_and_publish(args.project)


def command_task(args: argparse.Namespace) -> None:
    expected = json.loads(args.expected_task) if args.expected_task is not None else None
    if args.expected_task is not None and not isinstance(expected, dict):
        raise SystemExit("--expected-task must be a task object")
    def change(state):
        if expected is not None and any(
                state.get("tasks", {}).get(args.task_id, {}).get(key) != value
                for key, value in expected.items()):
            return False
        update_task(state, args)
    mutate(args.project, change)


def update_task(state: dict[str, Any], args: argparse.Namespace) -> None:
    task_time = stamp()
    prior = state.setdefault("tasks", {}).get(args.task_id, {})
    if not prior and not all((args.title, args.status, args.executor)):
        raise SystemExit("new tasks require --title, --status, and --executor")
    stage = "live" if args.stage == "measured" else args.stage
    if stage and stage != "live" and args.pr is None and prior.get("pr") is None:
        raise SystemExit("--stage requires --pr")
    if stage == "live" and not (args.evidence or "").strip():
        raise SystemExit("Live requires --evidence describing the measured operational outcome")
    if args.evidence and stage != "live":
        raise SystemExit("--evidence requires --stage live")
    task = dict(prior)
    if args.delivery_target is not None:
        task["delivery_target"] = args.delivery_target
    executor = args.executor or prior.get("executor")
    derived = executor_metadata(executor)
    new_executor = args.executor is not None
    task.update({
        "domain": args.domain or prior.get("domain") or "system",
        "title": args.title or prior.get("title"),
        "status": args.status or prior.get("status"),
        "executor": executor,
        "provider": args.provider or (None if new_executor else prior.get("provider")) or derived[0],
        "model": args.model or (None if new_executor else prior.get("model")) or derived[1],
        "effort": args.effort or (None if new_executor else prior.get("effort")) or derived[2],
        "pr": args.pr if args.pr is not None else prior.get("pr"),
        "repo": safe_repo(args.repo or prior.get("repo") or DEFAULT_PR_REPO),
        "note": args.note if args.note is not None else prior.get("note"),
        "created_at": prior.get("created_at", task_time),
        "updated_at": task_time,
    })
    if args.summary is not None:
        task["summary"] = args.summary.strip()
    if prior.get("pr") is not None and pr_key(prior) != pr_key(task):
        for field in ("pr_phase", "pr_checks", "pr_head", "evidence", "completed_at", "merge_sha",
                      "review_verdict", "release_wait"):
            task.pop(field, None)
        task["status"] = args.status or "running"
        task["stage"] = stage or "build"
    if args.stage:
        task["stage"] = stage
        task["manual_stage"] = stage
    if stage == "live":
        task["status"] = "done"
        task["evidence"] = args.evidence.strip()
        task["completed_at"] = prior.get("completed_at") if task_stage(prior) == "live" else task_time
    elif task_stage(prior) == "live" and (stage or (args.status and args.status != "done")):
        if not stage:
            task.pop("stage", None)
        task.pop("evidence", None)
        task.pop("completed_at", None)
    if args.health is not None:
        task["health"] = args.health
    if args.lane is not None:
        task["lane"] = None if args.lane == "status" else args.lane
    if args.reason is not None:
        if args.status in RETIRED_STATUSES or (args.status is None and is_retired(task)):
            task["reason"] = args.reason.strip()
        else:
            task["blocked_reason"] = args.reason.strip()
    if args.next_action is not None:
        task["next_action"] = args.next_action.strip()
    normalize_task(task)
    finished = task.get("status") == "done" or task_stage(task) == "live"
    if not finished and (task.get("status") == "blocked" or task.get("health") == "blocked"):
        if not (task.get("blocked_reason") and task.get("next_action")):
            raise SystemExit("a blocked task needs --reason and --next-action")
        if args.reason is not None or args.status == "blocked":
            # Written by hand: the GitHub sync keeps it until GitHub shows it cleared.
            task["blocked_source"] = "manual"
            if task.get("pr_head"):
                task["blocked_head"] = task["pr_head"]
    else:
        for field in ("blocked_reason", "next_action", "blocked_source", "blocked_head"):
            task.pop(field, None)
    record_stage(task, task_time, task_stage(prior) if prior else None)
    if task.get("status") in RETIRED_STATUSES and not str(task.get("reason") or "").strip():
        raise SystemExit(f"a {task.get('status')} card needs --reason")
    state["tasks"][args.task_id] = task


def command_ask(args: argparse.Namespace) -> None:
    mutate(args.project, lambda state: ask_question(state, args))


def ask_question(state: dict[str, Any], args: argparse.Namespace) -> None:
    question_time = stamp()
    prior = state.setdefault("questions", {}).get(args.q_id, {})
    question_text = args.question.strip()
    default = args.default.strip() if args.default is not None else None
    if not question_text or not default:
        raise SystemExit("question and default must be nonempty")
    choices = args.choice or []
    if len(choices) > 8 or any(not choice.strip() or len(choice) > 500 for choice in choices) or len(set(choices)) != len(choices):
        raise SystemExit("provide at most eight distinct, nonempty choices")
    free_text = args.free_text or not choices
    if choices and not free_text and default not in choices:
        raise SystemExit("a choice-only question needs a default among its choices")
    asker_ref = safe_asker_ref(args.asker_ref or prior.get("asker_ref") or f"orchestrator:{args.project}")
    revision = int(prior.get("revision") or 1) + 1 if prior else 1
    history = list(prior.get("history", []))
    if prior:
        history.append({key: prior.get(key) for key in
                        ("question", "default", "choices", "free_text", "asker_ref", "revision")})
    state["questions"][args.q_id] = {
        "question": question_text,
        "default": default,
        "choices": choices,
        "free_text": free_text,
        "asker_ref": asker_ref,
        "revision": revision,
        "history": history,
        "answer": None,
        "created_at": prior.get("created_at", question_time),
        "updated_at": question_time,
    }


def command_answer(args: argparse.Namespace) -> None:
    answer = args.answer
    if answer is None:
        answer = sys.stdin.readline().strip()
    if not answer:
        raise SystemExit("answer must be provided on stdin or with --answer")

    def record(state: dict[str, Any]) -> None:
        question = state.setdefault("questions", {}).get(args.q_id)
        if question is None:
            raise SystemExit(f"question does not exist: {args.q_id}")
        question["answer"] = answer
        question["answered_at"] = question["updated_at"] = stamp()
    mutate(args.project, record)


def command_deliver(args: argparse.Namespace) -> None:
    mutate(args.project, lambda state: state.setdefault("deliverables", []).insert(
        0, {"title": args.title, "link": args.link, "created_at": stamp()}))


def command_note(args: argparse.Namespace) -> None:
    text = args.text.strip()
    if not text:
        raise SystemExit("a note needs text")

    def add(state: dict[str, Any]) -> None:
        notes = state.setdefault("notes", [])
        notes.insert(0, {"text": text[:2000], "created_at": stamp()})
        del notes[50:]
    mutate(args.project, add)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("project")
    init.add_argument("--title", required=True)
    init.set_defaults(func=command_init)
    task = commands.add_parser("task")
    task.add_argument("project")
    task.add_argument("task_id")
    task.add_argument("--title")
    task.add_argument("--status", choices=STATUSES)
    task.add_argument("--executor")
    task.add_argument("--provider")
    task.add_argument("--model")
    task.add_argument("--effort")
    task.add_argument("--summary")
    task.add_argument("--pr", type=int)
    task.add_argument("--repo")
    task.add_argument("--domain", choices=("system", "deals", "unclassified"))
    task.add_argument("--stage", choices=PR_STAGES)
    task.add_argument("--health", choices=("healthy", "question", "blocked"))
    task.add_argument("--reason", help="why the task is blocked (required with blocked), or why it failed or was superseded (required for those)")
    task.add_argument("--next-action", dest="next_action", help="what unblocks it (required with blocked)")
    task.add_argument("--lane", choices=("status", "needs-joe"))
    task.add_argument("--expected-task", help="update only if these task fields still match this JSON object")
    task.add_argument("--note")
    task.add_argument("--evidence")
    task.add_argument("--delivery-target", choices=("worker", "app", "workstation", "database", "manual"),
                      help="Matching worker/app targets may complete from release readback; other targets require measured --evidence")
    task.set_defaults(func=command_task)
    ask = commands.add_parser("ask")
    ask.add_argument("project")
    ask.add_argument("q_id")
    ask.add_argument("--question", required=True)
    ask.add_argument("--default", required=True)
    ask.add_argument("--asker-ref")
    ask.add_argument("--choice", action="append")
    ask.add_argument("--free-text", action="store_true")
    ask.set_defaults(func=command_ask)
    answer = commands.add_parser("answer")
    answer.add_argument("project")
    answer.add_argument("q_id")
    answer.add_argument("--answer")
    answer.set_defaults(func=command_answer)
    deliver = commands.add_parser("deliver")
    deliver.add_argument("project")
    deliver.add_argument("--title", required=True)
    deliver.add_argument("--link", required=True)
    deliver.set_defaults(func=command_deliver)
    note = commands.add_parser("note")
    note.add_argument("project")
    note.add_argument("--text", required=True)
    note.set_defaults(func=command_note)
    render_cmd = commands.add_parser("render", help="refresh derived facts and write the board JSON")
    render_cmd.add_argument("project")
    render_cmd.add_argument("--publish", action="store_true")
    render_cmd.set_defaults(func=command_render)
    poll = commands.add_parser("poll-answers")
    poll.add_argument("project")
    poll.set_defaults(func=command_poll)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if hasattr(args, "task_id") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.task_id):
        raise SystemExit("task_id must contain only letters, numbers, dot, underscore, or hyphen")
    if hasattr(args, "q_id") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.q_id):
        raise SystemExit("q_id must contain only letters, numbers, dot, underscore, or hyphen")
    if getattr(args, "project", None) == ALL_REPOS_BOARD and args.command not in {"render", "poll-answers"}:
        raise SystemExit("all-repos is built from gh; only render and poll-answers apply")
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
