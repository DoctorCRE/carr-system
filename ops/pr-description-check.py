#!/usr/bin/env python3
"""Require a written pull request description beyond the repository template."""

import json
import os
import re
from pathlib import Path


def has_description(body: str | None) -> bool:
    text = re.sub(r"<!--[\s\S]*?-->", "", body or "")
    lines = [line.strip() for line in text.splitlines()]
    content = [line for line in lines if line and not line.startswith("#")]
    return bool(re.search(r"[A-Za-z0-9]", "\n".join(content)))


def main() -> int:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        print("GITHUB_EVENT_PATH is required")
        return 1
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
        body = event["pull_request"]["body"]
    except (OSError, ValueError, KeyError, TypeError):
        print("Cannot read pull request body from the GitHub event")
        return 1
    if not has_description(body):
        print("Pull request needs a description under 'What changed' or 'How verified'.")
        return 1
    print("Pull request description present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
