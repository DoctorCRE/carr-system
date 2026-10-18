#!/usr/bin/env python3
"""Read PR state without treating an unreadable request as a closed PR.

Usage: merge-queue-github-read.py OWNER/REPO PR APPROVED_HEAD
Exit 0 returns a validated snapshot; exit 1 returns a blocker; exit 2 is an
invalid invocation. The pending item is preserved in every read result.
This helper never advances a queue or authorizes or runs repair/merge actions.
Use the orchestration PATH so GitHubReader resolves the shared gh wrapper.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.github_reader import GitHubReader, GitHubUnreadable  # noqa: E402


def read_result(repository: str, number: int, approved_head: str, *,
                reader: GitHubReader | None = None) -> dict[str, Any]:
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository)
            or repository.split("/")[-1] in {".", ".."}):
        raise ValueError("repository must be OWNER/REPO")
    if type(number) is not int or number <= 0:
        raise ValueError("PR number must be a positive integer")
    if not re.fullmatch(r"[0-9a-f]{40}", approved_head):
        raise ValueError("approved head must be a full lowercase commit SHA")

    result: dict[str, Any] = {
        "schema": "carr.merge-queue-github-read.v1",
        "pending": {"repository": repository, "number": number, "approved_head": approved_head},
        "ok": False,
        "pull_request": None,
        "blocker": None,
    }
    try:
        data = (reader or GitHubReader()).api(f"repos/{repository}/pulls/{number}")
    except GitHubUnreadable as exc:
        diagnostic = str(exc)
        result["blocker"] = {
            "kind": exc.kind,
            "retryable": exc.transient,
            "attempts": exc.attempts,
            "detail": diagnostic,
        }
        return result

    head = data.get("head") if isinstance(data, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    if (not isinstance(data, dict)
            or data.get("state") not in ("open", "closed")
            or type(data.get("merged")) is not bool
            or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", sha)
            or (data.get("mergeable_state") is not None
                and not isinstance(data["mergeable_state"], str))):
        result["blocker"] = {
            "kind": "invalid_response", "retryable": False, "attempts": None,
            "detail": "GitHub returned no valid PR state, merged flag, and head SHA",
        }
        return result

    result["ok"] = True
    result["pull_request"] = {
        "state": data["state"], "merged": data["merged"], "head": sha,
        "mergeable_state": data.get("mergeable_state"),
    }
    return result


def main(argv: list[str] | None = None, *, reader: GitHubReader | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("number", type=int)
    parser.add_argument("approved_head")
    args = parser.parse_args(argv)
    try:
        result = read_result(args.repository, args.number, args.approved_head, reader=reader)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
