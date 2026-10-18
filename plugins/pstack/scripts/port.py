#!/usr/bin/env python3
"""Apply or verify the finite platform port against the pinned upstream source."""

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POINTER = (
    "## Platform port\n\n"
    "Read [PORT.md](../../PORT.md) and [pstack-models.md](../../pstack-models.md) "
    "before this skill. Apply their platform mappings to the upstream instructions below.\n\n"
)
REPLACEMENTS = (
    ("~/.cursor/rules/pstack-models.mdc", "plugins/pstack/pstack-models.md"),
    ("pstack-models.mdc", "pstack-models.md"),
    ("AskQuestion", "AskUserQuestion"),
    ("generalPurpose", "general-purpose"),
    ('subagent_type: "Comment Sicko"', 'subagent_type: "comment-sicko"'),
    ("Cursor cloud agent", "headless worktree agent"),
    (".cursor/skills/", ".claude/skills/"),
)


def port(path, data):
    if not path.endswith(".md") or "/principle-" in path:
        return data
    text = data.decode()
    for before, after in REPLACEMENTS:
        text = text.replace(before, after)
    text = re.sub(r"\bTask\b(?! as a verb phrase)", "Agent", text)
    text = re.sub(r"(?<!plugins/)pstack/skills/", "plugins/pstack/skills/", text)
    if path in ("skills/poteto-mode/SKILL.md", "skills/make-bot-ui/SKILL.md"):
        name = path.split("/")[1]
        text = re.sub(r"(?m)^name: .+$", f"name: {name}", text, count=1)
    if path == "agents/comment-sicko.md":
        text = text.replace("name: Comment Sicko", "name: comment-sicko", 1)
    if path.startswith("skills/") and path.endswith("/SKILL.md"):
        end = text.index("\n---", 4) + len("\n---\n")
        text = text[:end] + "\n" + POINTER + text[end:].lstrip("\n")
    return text.encode()


def resolution(path, line):
    if "<Task as a verb phrase>" in line:
        return "M12"
    codes = []
    if re.search(r"\bTask\b|\bAgent\b|generalPurpose|general-purpose", line):
        codes.append("M1")
    if "AskQuestion" in line or "AskUserQuestion" in line:
        codes.append("M2")
    if "pstack-models" in line:
        codes.append("M3")
    if "cursor-team-kit" in line or "create-skill" in line:
        codes.append("M4")
    if "cloud" in line.lower():
        codes.append("M5")
    if "transcript" in line.lower() or ".cursor/projects" in line:
        codes.append("M7")
    if ".cursor/skills" in line or ".cursor/worktrees" in line:
        codes.append("M8")
    if "make-bot-ui" in path:
        codes.append("M9")
    if "babysit" in line.lower() or "/loop" in line:
        codes.append("M10")
    if "scripts/" in path and "cursor" in line.lower():
        codes.append("M11")
    if not codes:
        codes.append("M12")
    return ", ".join(dict.fromkeys(codes))


def inventory(root, title):
    rows = [f"## {title}\n", "| Reference | Resolution |", "| --- | --- |"]
    for path in sorted((root / "skills").rglob("*")):
        if not path.is_file() or "node_modules" in path.parts:
            continue
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, 1):
            if re.search(r"\bTask\b|AskQuestion|cursor", line, re.I):
                relative = path.relative_to(root).as_posix()
                rows.append(f"| `{relative}:{number}` | {resolution(relative, line)} |")
    return "\n".join(rows) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "UPSTREAM.json").read_text())
    failures, changed = [], []
    actual = {p.relative_to(args.source).as_posix() for p in args.source.rglob("*") if p.is_file()}
    expected = {row["path"] for row in manifest["files"]}
    if actual != expected:
        failures.append("upstream file set differs from UPSTREAM.json")
    for row in manifest["files"]:
        relative = row["path"]
        source = args.source / relative
        target = ROOT / relative
        if not source.is_file():
            failures.append(f"upstream missing: {relative}")
            continue
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            failures.append(f"upstream hash differs: {relative}")
            continue
        transformed = port(relative, data)
        if transformed != data:
            changed.append(relative)
        if args.apply:
            target.write_bytes(transformed)
        elif not target.is_file() or target.read_bytes() != transformed:
            failures.append(f"unexpected vendor change: {relative}")
    if args.inventory:
        print(inventory(args.source, "Upstream reference inventory"))
        print(inventory(ROOT, "Installed skills reference inventory"))
    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print(f"PASS: {len(expected)} upstream files authenticated; {len(changed)} platform-only ports; all other bytes unchanged.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
