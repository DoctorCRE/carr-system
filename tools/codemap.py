# Print a fresh map of tracked scripts and their opening descriptions.
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".sh", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".applescript"}


def in_scope(path):
    return path.parts[0] in {"bin", "ops", "tools", "hooks"} or path.parent == Path("mcp-server/src")


def description(path):
    with path.open(encoding="utf-8", errors="replace") as source:
        for index, line in enumerate(source):
            line = line.strip()
            if index == 0 and line.startswith("#!"):
                continue
            if index < 2 and re.match(r"#.*?coding[:=]\s*[-\w.]+", line):
                continue
            if line:
                break
        else:
            return ""
        markers = ("--",) if path.suffix == ".applescript" else ("#", "//")
        for marker in markers:
            if line.startswith(marker):
                return line[len(marker):].strip()
        quote = re.match(r'''[rRuU]?("""|\x27\x27\x27)''', line)
        if quote:
            ending = quote[1]
            line = line[quote.end():]
        elif line.startswith("/*") or (path.suffix == ".applescript" and line.startswith("(*")):
            ending = "*/" if line.startswith("/*") else "*)"
            line = line[2:]
        else:
            return ""
        while True:
            text = line.split(ending, 1)[0].strip().lstrip("*").strip()
            if text or ending in line:
                return text
            line = next(source, "")
            if not line:
                return ""
    return ""


def main():
    parser = argparse.ArgumentParser(description="Map tracked scripts by folder.")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--missing", action="store_true", help="list scripts without opening descriptions")
    output.add_argument("--check", nargs="?", const="origin/main", metavar="BASE",
                        help="require descriptions under 100 characters for scripts changed since BASE")
    args = parser.parse_args()
    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "--stage", "-z"],
        stderr=subprocess.PIPE,
    )
    entries = {}
    for record in tracked.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode = metadata.split()[0]
        name = os.fsdecode(raw_path)
        path = ROOT / name
        if mode not in (b"100644", b"100755") or path.is_symlink() or not path.is_file():
            continue
        if path.suffix not in EXTENSIONS and not (in_scope(Path(name)) and mode == b"100755"):
            continue
        entries[name] = description(path)
    if args.check is not None:
        base = subprocess.check_output(
            ["git", "-C", str(ROOT), "merge-base", args.check, "HEAD"],
            stderr=subprocess.PIPE,
        ).strip().decode("ascii")
        raw_changed = subprocess.check_output(
            ["git", "-C", str(ROOT), "diff", "--name-only", "--no-renames", "-z", base, "--"], stderr=subprocess.PIPE,
        )
        changed = {os.fsdecode(path) for path in raw_changed.split(b"\0") if path}
        invalid = False
        for name, summary in sorted(entries.items()):
            if name in changed and in_scope(Path(name)) and not 0 < len(summary) < 100:
                invalid = True
                print(f"{name}: add an opening description under 100 characters")
        return int(invalid)
    group = None
    missing = False
    for name, summary in sorted(entries.items(), key=lambda entry: (
            entry[0].split("/", 1)[0] if "/" in entry[0] else "", entry[0])):
        if args.missing:
            if not summary:
                print(name)
                missing = True
            continue
        folder = name.split("/", 1)[0] if "/" in name else "root"
        if folder != group:
            if group is not None:
                print()
            print(f"[{folder}]")
            group = folder
        print(f"{name} — {summary or '[missing description]'}")
    return int(missing)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"codemap: {error}", file=sys.stderr)
        raise SystemExit(2)
