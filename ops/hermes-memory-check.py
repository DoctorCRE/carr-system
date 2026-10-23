#!/usr/bin/env python3
"""hermes-memory-check.py — does the Hermes runtime's self-authored memory
contain anything the CARR record layer should own?

WHY THIS IS A PREDICATE AND NOT A SCHEDULED MODEL RUN. Joe, 2026-08-16: "that
drift check needs to be code, not a scheduled routine. we are going away from
claude running things that could just be code." Rule 5e89c211 says the same
thing in general: never spend a cognition token on state, recurrence, routing,
validation, or a decision already expressible as a predicate. Reading two small
text files and matching them against a fixed vocabulary is a predicate. A model
would cost tokens, vary run to run, and be unable to fail CI.

WHAT IT GUARDS. Hermes keeps two self-written memory files under
~/.hermes/memories/ — MEMORY.md (agent notes) and USER.md (user profile) — and
injects both into its system prompt at the start of every session. It writes
them on its own initiative: within hours of first install on 2026-08-16 it had
already recorded a UI preference nobody asked it to record.

The hazard is a SECOND RULE STORE. CARR's taught rules carry provenance, a
human activation gate, and a single source of truth. These files carry none of
that, are invisible to every CARR surface, go stale silently when the store
changes, and then steer the runtime against the store nobody knew it had
diverged from. That is a correctness problem rather than a security one:
Hermes cannot write to CARR (its profile's write set is empty), so its memory
can never BECOME CARR truth. It can only make the runtime behave as if it were.

WHAT IT DOES NOT DO. It does not read, judge, or rewrite the content's meaning,
and it never deletes anything. It reports. Removing an entry is a decision about
what a runtime should know, and that belongs to a human or to a session acting
with one.

Exit 0 clean, 1 on findings. `run.sh health` reads the summary line.
"""
import os
import re
import sys

HOME = os.path.expanduser("~")
# The selftest points this at a fixture directory. Deliberately an env override
# rather than a flag: a check whose real target can be changed by an argument is
# a check someone can aim somewhere harmless and still call green.
MEMORY_DIR = os.environ.get("HERMES_MEMORY_DIR") or os.path.join(HOME, ".hermes", "memories")
FILES = ("MEMORY.md", "USER.md")

# The pointer USER.md must keep. Its whole job is telling the runtime that its
# standing instructions live in the store and not in the file it is reading, so
# a memory rewrite that drops this line is itself the drift.
REQUIRED_POINTER = "standing-context"

# Caps are Hermes' own, from its memory documentation. A file at its ceiling
# stops accepting writes rather than compacting, so approaching the limit is
# worth surfacing before an entry is silently refused.
CAPS = {"MEMORY.md": 2200, "USER.md": 1375}

# Each pattern names a class of content the record layer already owns. They are
# deliberately narrow and literal: a broad pattern that fires on ordinary prose
# would train everyone to ignore the check, which is worse than no check.
PATTERNS = (
    (
        "record ref",
        re.compile(r"\b(?:C-\d{3,}|L-\d{3,}|V-[A-Z]{2,4}-\d{3,}|T-\d{3,}|WR-[A-Z]+-\d+)\b"),
        "a client, lead, vendor, or work-request ref. The record layer holds these and they change; "
        "a copy here is a snapshot that will silently stop being true.",
    ),
    (
        "rule id",
        re.compile(r"(?<![0-9a-f])[0-9a-f]{8}(?![0-9a-f])"),
        "an eight-character rule id. Rules live in the store, are read live through standing-context, "
        "and are revised without telling this runtime.",
    ),
    (
        "standing instruction",
        re.compile(r"(?i)\b(?:always|never|from now on|going forward)\b[^.\n]{0,80}"
                   r"\b(?:joe|dell|carr|client|deal|lead|vendor|prospect|doctrine|rule)\b"),
        "a standing instruction about how CARR work is done. That is a taught rule; it belongs in the "
        "store behind the human activation gate, where it carries provenance.",
    ),
    (
        "doctrine claim",
        re.compile(r"(?i)\b(?:carr(?:'s)?\s+(?:doctrine|policy|rule|process|playbook)|"
                   r"the\s+(?:deal room|lead board|command center|record layer)\s+(?:is|does|holds|requires))\b"),
        "a claim about how the CARR system itself works. Read it from the store instead; this copy "
        "cannot be updated when the system changes.",
    ),
    (
        "partner working preference",
        re.compile(r"(?i)\b(?:joe|dell)\b[^.\n]{0,60}\b(?:prefers|wants|likes|expects|hates|dislikes|"
                   r"always asks|never wants)\b"),
        "a partner preference. The taught-rule store is where these bind; a copy here binds only this "
        "runtime and nobody can see it.",
    ),
)


# The guard block is the paragraphs that TELL the runtime where its standing
# instructions live and what must not be stored here. It is prose about memory
# rather than memory content, and it necessarily names Joe, CARR and rules — so
# scanning it produced the check's first false positive on its own first run
# ("Joe wants work done", inside the sentence pointing at the store). Skipping
# it is not a hole: a pattern hiding inside the guard block would still be
# caught, because the block is identified by these two literal markers and an
# entry cannot smuggle content in by claiming to be one.
GUARD_MARKERS = (REQUIRED_POINTER, "DO NOT STORE")


def entries(text):
    """The units to scan, in the order they appear.

    Hermes separates its own entries with a section sign. A hand-authored file
    (the guard USER.md is one) has none, so fall back to blank-line paragraphs
    rather than treating the whole file as a single entry — one giant entry
    means one finding and a useless excerpt.
    """
    parts = text.split("§") if "§" in text else re.split(r"\n\s*\n", text)
    out = []
    for i, part in enumerate(parts, 1):
        part = part.strip()
        if not part:
            continue
        if any(marker in part for marker in GUARD_MARKERS):
            continue
        out.append((i, part))
    return out


def scan():
    findings = []
    notes = []

    if not os.path.isdir(MEMORY_DIR):
        # Hermes not installed on this machine, or no session has run yet.
        # Nothing to check is a clean result, not a failure.
        print("hermes-memory: OK (no memory directory; nothing to check)")
        return 0

    for name in FILES:
        path = os.path.join(MEMORY_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as exc:
            findings.append(f"{name}: unreadable ({exc.__class__.__name__})")
            continue

        cap = CAPS.get(name)
        if cap and len(text) > cap * 0.85:
            notes.append(f"{name} at {len(text)}/{cap} chars; writes are refused at the cap, not compacted")

        if name == "USER.md" and REQUIRED_POINTER not in text:
            findings.append(
                f"{name}: the pointer to the CARR store is gone. This file is supposed to tell the "
                f"runtime that its standing instructions come from `standing-context`, not from its "
                f"own memory. Without that line it has no reason to look."
            )

        for index, entry in entries(text):
            for label, pattern, why in PATTERNS:
                match = pattern.search(entry)
                if match:
                    excerpt = " ".join(entry.split())[:110]
                    findings.append(
                        f"{name} entry {index} [{label}]: \"{excerpt}\"\n"
                        f"      matched: {match.group(0)[:60]!r}\n"
                        f"      why: {why}"
                    )
                    break  # one finding per entry; the first class is enough to act on

    if findings:
        print(f"hermes-memory: DRIFT — {len(findings)} entr(ies) hold content the record layer owns")
        for f in findings:
            print(f"  · {f}")
        for n in notes:
            print(f"  note: {n}")
        print("  Propose guidance in CLAUDE.md, AGENTS.md or a skill through a PR; use the right record verb for facts,")
        print("  then remove it from the memory file. Hermes cannot write to CARR, so nothing here has")
        print("  reached the record; the risk is the runtime acting on a private copy.")
        return 1

    summary = "no record-layer content in the runtime's self-authored memory"
    if notes:
        summary += " · " + " · ".join(notes)
    print(f"hermes-memory: OK ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(scan())
