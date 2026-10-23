# Print a fresh map of tracked scripts and their opening descriptions.
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".sh", ".zsh", ".js", ".mjs", ".ts"}


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
        for marker in ("#", "//"):
            if line.startswith(marker):
                return line[len(marker):].strip()
        quote = re.match(r'''[rRuU]?("""|\x27\x27\x27)''', line)
        if quote:
            ending = quote[1]
            line = line[quote.end():]
        elif line.startswith("/*"):
            ending = "*/"
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
    parser.add_argument("--missing", action="store_true", help="list scripts without opening descriptions")
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
        if path.suffix not in EXTENSIONS and not (name.startswith("bin/") and mode == b"100755"):
            continue
        entries[name] = description(path)
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
