#!/usr/bin/env python3
"""extract-real-replay.py — refresh the REAL replay fixtures that
ops/gate-replay.py runs every hook gate over.

WHY THIS EXISTS. Gates kept shipping tested only on invented input shapes: PR
#1224's Stop gate parsed an invented transcript shape and never fired on 803
real receipts, and PR #1225's shell regexes were tested only on invented
commands. ops/gate-replay.py answers that by running every gate over committed
real records on every CI run. This script is where those records come from.

WHAT IT WRITES, into ops/fixtures/real-replay/ (one JSON object per line, each
with a content-hash `id`, so adding a record never renumbers the others):

  bash-commands.jsonl    real Bash tool calls        {tool_name, tool_input}
  file-edits.jsonl       real Edit/Write/MultiEdit   {tool_name, tool_input}
  read-calls.jsonl       real Read/Grep/Glob         {tool_name, tool_input}
  hook-advisories.jsonl  real hook_additional_context records, including the
                         jev-build-turn-receipt/v1 build receipts and the
                         rule-jev-message-delivery/v2 receipts that nest one
                         under `build_receipt`     {hookEvent, hookName, content}

The synthetic-structured sets (agent-prompts, tool-calls, user-prompts) are
hand-written and NOT produced here: their real counterparts are free prose
about client and deal work, which no pattern scan can be trusted to clear.

SAMPLING. Records are deduplicated after normalisation, then stratified so the
set covers many command and advisory shapes rather than the first N of one
session: Bash by leading command (git and gh by subcommand), edits and reads by
top-level directory, advisories by schema plus the set of required facets (or
by headline for text advisories). Within a stratum the picks are spread evenly
across history. Deterministic for a given transcript set.

NORMALISATION, which is identity plumbing and never redaction of content:
  - the checkout root, and any .claude/worktrees/<name> under it -> {{REPO}}
  - the home directory                                          -> {{HOME}}
  - every real session id                                -> REPLAY_SESSION_ID
ops/gate-replay.py substitutes the replay sandbox's own values back.

LEAK GUARD. After normalisation every candidate record is scanned, keys and
values, with ops/business_data_patterns.py. ANY hit drops the WHOLE record;
nothing is ever partially redacted and kept. ops/gate-replay.py runs the same
scan over every committed fixture on every CI run, so a record that somehow got
past this script still fails CI.

USAGE:
    python3 tools/extract-real-replay.py            # writes the four files
    python3 tools/extract-real-replay.py --dry-run  # counts only

It is a plain script (shebang + entrypoint guard), not a verb, worker route or
job definition, so it owes no SCAC registry successor.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ops import business_data_patterns as bdp  # noqa: E402

OUT_DIR = REPO / "ops" / "fixtures" / "real-replay"
DEFAULT_TRANSCRIPTS = os.path.expanduser("~/.claude/projects")
REAL_REPO = "/Users/booko/carr-system"
REAL_HOME = "/Users/booko"
MAX_FIELD_CHARS = 3000

# Text advisories whose body QUOTES conversation or raw tool output: the ledger
# sweep quotes the partner's own words, chat lint quotes the previous reply, and
# a persisted-output notice previews whatever a tool printed. They are prose in
# the sense that matters here, so they are never extracted, whatever the leak
# scan says about any one of them.
QUOTING_ADVISORY_PREFIXES = ("LEDGER SWEEP", "CHAT LINT", "<persisted-output>")

# Edits that carry client or practice names by construction, whatever the
# leak scan says about them (2026-09-24: the client-name scrub's own edits
# reached the public fixtures, each pairing a real name with its pseudonym).
# exporters/targets.py holds DOSSIER_FILES, the roster of client dossiers.
NAME_BEARING_PATHS = ("exporters/targets.py",)
NAME_BEARING_MARKERS = ("DOSSIER_FILES",)
WORD_RE = re.compile(r"[A-Za-z]+")


def scrub_style_rename(old: str, new: str) -> bool:
    """True when an edit only swaps words: the same text with the words taken
    out, and every changed word alphabetic, at least one of them capitalised
    (a name) on either side. That is the shape of a rename or a scrub, and the
    swapped words are the payload."""
    if not old or not new or old == new:
        return False
    if WORD_RE.sub("", old) != WORD_RE.sub("", new):
        return False
    a, b = WORD_RE.findall(old), WORD_RE.findall(new)
    if len(a) != len(b):
        return False
    changed = [(x, y) for x, y in zip(a, b) if x != y]
    return bool(changed) and any(x[:1].isupper() or y[:1].isupper() for x, y in changed)


def name_bearing_edit(tool_input: Record) -> bool:
    path = str(tool_input.get("file_path", ""))
    if any(path.endswith("/" + p) or path == p for p in NAME_BEARING_PATHS):
        return True
    pieces: List[Record] = [tool_input]
    if isinstance(tool_input.get("edits"), list):
        pieces += [e for e in tool_input["edits"] if isinstance(e, dict)]
    for piece in pieces:
        for key in ("old_string", "new_string", "content"):
            value = piece.get(key)
            if isinstance(value, str) and any(m in value for m in NAME_BEARING_MARKERS):
                return True
        old, new = piece.get("old_string"), piece.get("new_string")
        if isinstance(old, str) and isinstance(new, str) and scrub_style_rename(old, new):
            return True
    return False


WORKTREE_RE = re.compile(re.escape(REAL_REPO) + r"/\.claude/worktrees/[A-Za-z0-9._-]+")
UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")

Record = Dict[str, Any]


def record_id(record: Record) -> str:
    body = {k: v for k, v in record.items() if k != "id"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return digest[:12]


class Normaliser:
    def __init__(self, session_ids: set) -> None:
        self.session_ids = {s.lower() for s in session_ids}

    def text(self, value: str) -> str:
        out = WORKTREE_RE.sub("{{REPO}}", value)
        out = out.replace(REAL_REPO, "{{REPO}}").replace(REAL_HOME, "{{HOME}}")
        return UUID_RE.sub(
            lambda m: bdp.REPLAY_SESSION_ID if m.group(0).lower() in self.session_ids
            else m.group(0), out)

    def deep(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.deep(v) for v in value]
        if isinstance(value, dict):
            return {self.text(str(k)): self.deep(v) for k, v in value.items()}
        return value


def transcripts(root: str) -> List[str]:
    paths = glob.glob(os.path.join(root, "*", "**", "*.jsonl"), recursive=True)
    return sorted(p for p in paths if "carr-system" in p)


def session_ids(paths: List[str]) -> set:
    found = set()
    for path in paths:
        for part in Path(path).parts:
            stem = part[:-6] if part.endswith(".jsonl") else part
            if UUID_RE.fullmatch(stem):
                found.add(stem.lower())
    return found


def iter_records(paths: List[str]) -> Iterator[Record]:
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        if record.get("sessionId"):
                            yield {"__session": record["sessionId"], **record}
                        else:
                            yield record
        except OSError as err:
            print(f"extract-real-replay: skipping {path}: {err}", file=sys.stderr)


def tool_uses(record: Record) -> Iterator[Record]:
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            yield item


def too_big(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) > MAX_FIELD_CHARS
    if isinstance(value, list):
        return any(too_big(v) for v in value)
    if isinstance(value, dict):
        return any(too_big(v) for v in value.values())
    return False


def top_dir(path: str) -> str:
    if path.startswith("{{REPO}}/"):
        rest = path[len("{{REPO}}/"):]
        return "repo:" + (rest.split("/", 1)[0] if "/" in rest else "(root)")
    if path.startswith("{{HOME}}/"):
        return "home"
    return "other"


def bash_stratum(tool_input: Record) -> str:
    command = str(tool_input.get("command", ""))
    stripped = re.sub(r"^\s*cd\s+\S+\s*&&\s*", "", command)
    words = stripped.split()
    if not words:
        return ""
    head = os.path.basename(words[0])
    if head in ("git", "gh", "npm", "run.sh", "bash", "sh", "zsh") and len(words) > 1:
        return f"{head} {words[1]}"
    return head


class Bucket:
    """Deduplicated candidates grouped into strata, then an even spread."""

    def __init__(self, per_stratum: int, total: int) -> None:
        self.per_stratum = per_stratum
        self.total = total
        self.strata: "OrderedDict[str, List[Record]]" = OrderedDict()
        self.seen: set = set()

    def offer(self, stratum: str, record: Record) -> None:
        key = json.dumps(record, sort_keys=True)
        if key in self.seen:
            return
        self.seen.add(key)
        self.strata.setdefault(stratum, []).append(record)

    def pick(self) -> List[Record]:
        chosen: List[Record] = []
        for items in self.strata.values():
            if len(items) <= self.per_stratum:
                chosen.extend(items)
                continue
            step = len(items) / self.per_stratum
            chosen.extend(items[int(i * step)] for i in range(self.per_stratum))
        if len(chosen) > self.total:
            step = len(chosen) / self.total
            chosen = [chosen[int(i * step)] for i in range(self.total)]
        return chosen


def advisory_stratum(event: str, content: List[Any]) -> Tuple[str, bool]:
    first = content[0] if content and isinstance(content[0], str) else ""
    stripped = first.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            receipt = data.get("build_receipt") if isinstance(data.get("build_receipt"), dict) else None
            nested = receipt is not None
            advisory = (receipt or data).get("advisory")
            facets: Tuple[str, ...] = ()
            if isinstance(advisory, dict):
                facets = tuple(sorted(
                    str(a.get("facet")) for a in advisory.get("required_actions") or []
                    if isinstance(a, dict)))
            has_receipt = nested or data.get("schema") == "jev-build-turn-receipt/v1"
            packs = ",".join(sorted(str(p) for p in data.get("packs") or []))
            rules = ",".join(sorted(str(r) for r in data.get("rule_ids") or []))
            return (f"{event}|json|{data.get('schema')}|{','.join(facets)}|{packs}|{rules}",
                    has_receipt)
    headline = re.sub(r"[\d.]+", "#", " ".join(stripped.split()[:3]))
    return f"{event}|text|{headline}", False


def tracked_tree() -> Tuple[set, set]:
    """Files git tracks in this checkout, and every directory above them."""
    import subprocess
    sys.path.insert(0, str(REPO / "ops"))
    import git_env
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, env=git_env.scrubbed_env(),
                         capture_output=True, check=True).stdout.decode("utf-8", "replace")
    files = {name for name in out.split("\0") if name}
    dirs = {""}
    for name in files:
        parts = name.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:depth]))
    return files, dirs


def in_tracked_tree(path: str, files: set, dirs: set) -> bool:
    """True only for a path inside the checkout that git tracks: a public file
    (or a directory holding public files). Untracked scratch, out/ and local
    notes are where private material lands, so they never become fixtures."""
    if path == "{{REPO}}":
        return True
    if not path.startswith("{{REPO}}/"):
        return False
    rel = path[len("{{REPO}}/"):].rstrip("/")
    return rel in files or rel in dirs


def extract(root: str, tracked: Optional[Tuple[set, set]] = None) -> Dict[str, List[Record]]:
    paths = transcripts(root)
    norm = Normaliser(session_ids(paths))
    files, dirs = tracked if tracked is not None else tracked_tree()
    bash = Bucket(per_stratum=12, total=320)
    edits = Bucket(per_stratum=10, total=110)
    reads = Bucket(per_stratum=6, total=60)
    advisories = Bucket(per_stratum=2, total=100)
    stats = {"dropped_leak": 0, "dropped_size": 0, "dropped_name_bearing": 0}

    def admit(bucket: Bucket, stratum: str, record: Record) -> None:
        if too_big(record):
            stats["dropped_size"] += 1
            return
        if bdp.scan_value(record):
            stats["dropped_leak"] += 1
            return
        bucket.offer(stratum, record)

    for raw in iter_records(paths):
        if raw.get("__session"):
            norm.session_ids.add(str(raw["__session"]).lower())
        for use in tool_uses(raw):
            name = use.get("name")
            tool_input = use.get("input")
            if not isinstance(tool_input, dict):
                continue
            if name == "Bash":
                command = tool_input.get("command")
                if not isinstance(command, str) or not command.strip():
                    continue
                kept = {"command": command}
                if isinstance(tool_input.get("description"), str):
                    kept["description"] = tool_input["description"]
                record = {"tool_name": "Bash", "tool_input": norm.deep(kept)}
                admit(bash, bash_stratum(record["tool_input"]), record)
            elif name in ("Edit", "Write", "MultiEdit"):
                record = {"tool_name": name, "tool_input": norm.deep(tool_input)}
                path = str(record["tool_input"].get("file_path", ""))
                # Only edits to files git tracks in this public checkout.
                # Edits under the home directory are memory notes, settings
                # and vault records, and untracked files in the checkout are
                # scratch: prose about the business, the same class as prompts.
                if not in_tracked_tree(path, files, dirs):
                    continue
                if name_bearing_edit(record["tool_input"]):
                    stats["dropped_name_bearing"] += 1
                    continue
                admit(edits, f"{name}|{top_dir(path)}", record)
            elif name in ("Read", "Grep", "Glob"):
                record = {"tool_name": name, "tool_input": norm.deep(tool_input)}
                path = str(record["tool_input"].get("file_path")
                           or record["tool_input"].get("path") or "")
                # Same boundary as edits: a read outside the tracked tree names
                # a file in someone's documents, and the name is the leak.
                if not in_tracked_tree(path, files, dirs):
                    continue
                admit(reads, f"{name}|{top_dir(path)}", record)
        attachment = raw.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "hook_additional_context":
            content = attachment.get("content")
            if not isinstance(content, list) or not content:
                continue
            if any(isinstance(c, str) and c.lstrip().startswith(QUOTING_ADVISORY_PREFIXES)
                   for c in content):
                continue
            content = norm.deep(content)
            event = str(attachment.get("hookEvent") or "")
            stratum, has_receipt = advisory_stratum(event, content)
            record = {"hookEvent": event, "hookName": str(attachment.get("hookName") or ""),
                      "content": content, "has_build_receipt": has_receipt}
            admit(advisories, stratum, record)

    out: Dict[str, List[Record]] = {}
    for name, bucket in (("bash-commands.jsonl", bash), ("file-edits.jsonl", edits),
                         ("read-calls.jsonl", reads), ("hook-advisories.jsonl", advisories)):
        rows = []
        for record in bucket.pick():
            record = {"id": record_id(record), **record}
            rows.append(record)
        rows.sort(key=lambda r: r["id"])
        out[name] = rows
    print(f"extract-real-replay: {len(paths)} transcripts; dropped {stats['dropped_leak']} "
          f"candidate records on a leak-pattern hit and {stats['dropped_size']} for size",
          file=sys.stderr)
    return out


def write(out: Dict[str, List[Record]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in out.items():
        with open(out_dir / name, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--transcripts", default=DEFAULT_TRANSCRIPTS,
                        help="directory holding Claude Code project transcript folders")
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    out = extract(args.transcripts)
    for name, rows in out.items():
        extra = ""
        if name == "hook-advisories.jsonl":
            extra = f" ({sum(1 for r in rows if r['has_build_receipt'])} build receipts)"
        print(f"extract-real-replay: {name}: {len(rows)} records{extra}")
    if not args.dry_run:
        write(out, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
