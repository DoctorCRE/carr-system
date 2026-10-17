"""The local half of the needs-Joe list.

The deployed governance-queue verb cannot read this machine's files, so the
board job re-derives these items from scratch on every run and publishes them
as the `needs-joe-local` board page the verb reads. Nothing is carried from
one run to the next: an item leaves when its PR closes, a later SUPERSEDED line
names its PR, or its line is removed from the file.

Source: out/orch/needs-joe-or-wait.txt, appended by out/orch/rescope-fix.sh
(`<ts> <repo>#<n> NEEDS JOE|WAITING ON|SUPERSEDED ...`) and by the orchestrator
(`<ts> TABLED[ (why)]: <what>`). Classification is the verb's job; this module
only parses lines and attaches live PR state.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

BOARD_ID = "needs-joe-local"
SCHEMA = "needs-joe-local.v1"
SOURCE = Path.home() / "carr-system/out/orch/needs-joe-or-wait.txt"
TEXT_LIMIT = 300
CLOSED = {"CLOSED", "MERGED"}

PR_LINE = re.compile(r"^(\S+)\s+([\w.-]+/[\w.-]+)#(\d+)\s+(NEEDS JOE|WAITING ON|SUPERSEDED)\b:?\s*(.*)$")
TABLED_LINE = re.compile(r"^(\S+)\s+TABLED\b(?:\s*\([^)]*\))?:?\s*(.*)$")
KINDS = {"NEEDS JOE": "needs_joe", "WAITING ON": "waiting", "SUPERSEDED": "superseded"}

PrLookup = Callable[[str, int], dict[str, Any]]


def read_source(path: Path = SOURCE) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def derive_items(text: str, lookup: PrLookup) -> list[dict[str, Any]]:
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    tabled: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if match := PR_LINE.match(line):
            at, repo, number, marker, rest = match.groups()
            key = (repo, int(number))
            # The newest line about a PR is its state; older lines are history.
            latest.pop(key, None)
            latest[key] = {"kind": KINDS[marker], "repo": repo, "number": int(number),
                           "text": (rest if marker == "NEEDS JOE" else f"{marker} {rest}".strip())[:TEXT_LIMIT],
                           "at": at}
        elif match := TABLED_LINE.match(line):
            at, rest = match.groups()
            tabled.append({"kind": "tabled", "repo": None, "number": None, "pr_state": None,
                           "pr_title": None, "text": rest.strip()[:TEXT_LIMIT], "at": at})
    items = []
    for (repo, number), item in latest.items():
        if item["kind"] == "superseded":
            continue
        try:
            pr = lookup(repo, number)
            state, title = str(pr.get("state") or "UNKNOWN").upper(), pr.get("title")
        except Exception:
            # Never drop an item because the PR read failed; say it is unknown.
            state, title = "UNKNOWN", None
        if state in CLOSED:
            continue
        items.append({**item, "pr_state": state, "pr_title": title})
    return items + tabled


def local_page(items: list[dict[str, Any]], observed_at: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "observed_at": observed_at,
            "source": "out/orch/needs-joe-or-wait.txt + live PR state", "items": items}



def publish(call_verb: Callable[[str, dict[str, Any]], dict[str, Any]],
            stable_key: Callable[[str, dict[str, Any]], str],
            lookup: PrLookup, text: str, observed_at: str) -> int:
    """Publish this run's page on the remote version and verify it reads back.
    Every run republishes: the verb judges freshness by publish time, so
    skipping an unchanged page would make a healthy job look dead."""
    page = local_page(derive_items(text, lookup), observed_at)
    remote = call_verb("read-progress-board", {"board_id": BOARD_ID}).get("snapshot")
    args = {"board_id": BOARD_ID, "base_version": int(remote["version"]) if remote else 0, "snapshot": page}
    call_verb("publish-board-snapshot", {**args, "idempotency_key": stable_key("publish-board-snapshot", args)})
    after = call_verb("read-progress-board", {"board_id": BOARD_ID}).get("snapshot") or {}
    if after.get("snapshot_json") != page:
        raise RuntimeError("needs-joe local page did not read back")
    return len(page["items"])
