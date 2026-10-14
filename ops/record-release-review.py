#!/usr/bin/env python3
"""Record exact-head review provenance for the local release pipeline.

The public GitHub comment proves the verdict and reviewed SHA, not the reviewer.
This command requires that exact PR to be merged, then records a native Codex
continuity event through the deployed Worker before appending the local event
consumed by release-pipeline.py. Reviewer identity is fixed by the dedicated
Codex continuity authentication profile; task references remain caller-reported
audit context and are deliberately marked unverified.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.github_reader import GitHubReader

ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

spec = importlib.util.spec_from_file_location("release_pipeline", ROOT / "ops/release-pipeline.py")
assert spec is not None and spec.loader is not None
rp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rp
spec.loader.exec_module(rp)


def load_lane(repo: str) -> dict:
    cfg = json.loads((ROOT / "ops/config/release-pipeline.v1.json").read_text())
    for lane in ("worker", "app"):
        if cfg[lane].get("github_repo") == repo:
            return cfg[lane]
    raise ValueError("repository is not a configured release lane")


def verify_source(gh: Any, repo: str, number: int, expected_head: str,
                  cfg: dict) -> tuple[dict, dict, str]:
    pr = gh.api(f"repos/{repo}/pulls/{number}")
    if not isinstance(pr, dict):
        raise ValueError("GitHub returned no pull request object")
    head = str((pr.get("head") or {}).get("sha") or "").lower()
    merge_sha = str(pr.get("merge_commit_sha") or "").lower()
    if (pr.get("state") != "closed" or not pr.get("merged_at") or
            (pr.get("base") or {}).get("ref") != "main" or head != expected_head or
            not SHA_RE.fullmatch(merge_sha)):
        raise ValueError("PR is not merged to main at the requested exact head")
    comments = gh.api(f"repos/{repo}/issues/{number}/comments", paginate=True)
    approval, rule, reviewed_sha = rp.approval_of(comments, cfg, head)
    if rule != "exact" or reviewed_sha != head:
        raise ValueError("approval does not cover the exact merged PR head")
    comment_id = approval.get("id")
    if type(comment_id) is not int or comment_id < 1:
        raise ValueError("deciding approval has no stable GitHub comment id")
    if approval.get("html_url") != f"https://github.com/{repo}/pull/{number}#issuecomment-{comment_id}":
        raise ValueError("deciding approval URL does not bind the configured repository and comment")
    return pr, approval, merge_sha


def _run_record(args: dict, *, runner: Callable[..., Any] = subprocess.run) -> str:
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("CARR_BREAK_GLASS", None)
    env.pop("CARR_BREAK_GLASS_REASON", None)
    env["CARR_MCP_CLIENT_PROFILE"] = "codex-continuity"
    result = runner([str(ROOT / "run.sh"), "call", "codex-record-event", json.dumps(args)],
                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError("authenticated Codex continuity record failed")
    try:
        payload = json.loads(result.stdout)
        event_id = payload["event"]["id"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("continuity record returned no event id") from None
    if not isinstance(event_id, str) or not event_id.strip():
        raise RuntimeError("continuity record returned no event id")
    return event_id


def _append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing: list[dict] = []
        if path.exists():
            for raw in path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    existing.append(value)
        same_merge = [x for x in existing if x.get("merge_sha") == event["merge_sha"] and
                      x.get("repository") == event["repository"]]
        if same_merge:
            if any(x == event for x in same_merge):
                return
            raise RuntimeError("a different merge event already exists for this merge SHA")
        with path.open("a", encoding="utf-8") as output:
            output.write(line)
            output.flush()
            os.fsync(output.fileno())


def record(args: argparse.Namespace, *, gh: Any | None = None,
           record_event: Callable[[dict], str] = _run_record) -> dict:
    if not SHA_RE.fullmatch(args.head_sha):
        raise ValueError("head SHA must be a full lowercase commit SHA")
    if not SLUG_RE.fullmatch(args.reviewer_task_ref) or not SLUG_RE.fullmatch(args.maker_task_ref):
        raise ValueError("task references must be bounded lowercase identifiers")
    if args.reviewer_task_ref != args.native_task_id:
        raise ValueError("reviewer task reference must match the native task bound by Codex continuity")
    if args.reviewer_task_ref == args.maker_task_ref:
        raise ValueError("reviewer and maker task references must differ")
    if (not args.builder_evidence or len(args.builder_evidence) > 8 or
            any(not ref.strip() or len(ref) > 500 for ref in args.builder_evidence)):
        raise ValueError("one to eight non-empty builder evidence references of at most 500 characters are required")
    repo = args.repository
    lane = load_lane(repo)
    gh = gh or GitHubReader(cwd=str(ROOT))
    pr, approval, merge_sha = verify_source(gh, repo, args.pull_number, args.head_sha, lane)
    comment_id = int(approval["id"])
    cursor = {
        "schema": "carr-release-review-event.v1",
        "repository": repo, "pull_number": args.pull_number,
        "head_sha": args.head_sha, "reviewed_sha": args.head_sha,
        "review_comment_id": comment_id, "merge_sha": merge_sha,
        "reviewer_task_ref": args.reviewer_task_ref,
        "maker_task_ref": args.maker_task_ref,
        "task_refs_verified": False,
        "builder_evidence_refs": args.builder_evidence,
    }
    if len(json.dumps(cursor, separators=(",", ":")).encode("utf-8")) > 1800:
        raise ValueError("review context exceeds the continuity cursor limit")
    key_material = f"release-review\0{repo}\0{merge_sha}\0{comment_id}".encode()
    continuity_args = {
        "idempotency_key": hashlib.sha256(key_material).hexdigest(),
        "runtime": "codex", "native_task_id": args.native_task_id,
        "project_id": args.project_id, "cwd": args.cwd,
        "event_type": "release_review", "cursor": cursor,
    }
    continuity_id = record_event(continuity_args)
    event = {
        **cursor,
        "schema": "carr-release-merge-event.v1",
        "provenance_source": "codex-record-event",
        "continuity_event_id": continuity_id,
        "reviewer": "codex",
        "review_comment_url": approval.get("html_url"),
        "merge_sha": merge_sha,
        "author": (pr.get("user") or {}).get("login"),
    }
    _append_event(ROOT / "out/merge-events.jsonl", event)
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-number", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--native-task-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--reviewer-task-ref", required=True)
    parser.add_argument("--maker-task-ref", required=True)
    parser.add_argument("--builder-evidence", action="append", default=[])
    args = parser.parse_args()
    try:
        event = record(args)
    except Exception as exc:
        print(f"release review event refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "merge_sha": event["merge_sha"],
                      "continuity_event_id": event["continuity_event_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
