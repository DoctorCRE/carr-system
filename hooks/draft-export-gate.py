#!/usr/bin/env python3
"""draft-export-gate.py — out/exports/ is a DRAFT, and it says so nowhere the
reader will see it.

THE FAILURE, 2026-08-15, defect 280b1b6c. Asked what was left on Program 4, a
session grepped out/exports/decision-history.md, found one unrelated line, and
told Joe an entire bullet had never been scoped. It had been settled four hours
earlier, in a decision sitting in the LIVE render. The draft copy was thirty-two
hours old. Two turns of work were built on that, and Joe's next instruction was
shaped by it.

WHY THIS DIRECTORY AND NOT ANOTHER. Three properties combine into a trap:
  * It is INSIDE THE REPO, so it is the closest thing to hand when a session
    wants to search decisions, loops or the roster. Nothing about reaching for
    it feels like a shortcut.
  * It is GITIGNORED, so it never travels, never appears in a diff, and no
    review ever notices it went stale.
  * `run.sh export` writes it as a DRAFT by default and only CARR_EXPORT_LIVE=1
    reaches the vault, so its age is bounded by nothing at all.

THE EXISTING BANNER GUARDS THE WRONG HAZARD. Every export carries "GENERATED
from the CARR record layer — do not hand-edit." That protects the file from
being WRITTEN. It tells a READER nothing, and a `grep -c` never prints it.

ANNOUNCE, NEVER DENY, and this is forced by evidence rather than by caution.
Real code reads this directory on purpose: tools/calendar-touch-matcher.py and
tools/mail-touch-matcher.py load the roster and registry from it.
Denying would break working code to prevent a reading mistake. Same posture
as gate_paths.py, and for the same reason: the property worth having is that the
mistake cannot be made QUIETLY.

THE AGE IS THE MESSAGE. "This is a draft" is a label a session skims past.
"This file is 32 hours old, the live render is 5 hours old" is a fact that
changes the next command. So this reports both, measured at the moment of
reading, and names the store as the source that is authoritative regardless.

FIRES ONCE PER SESSION PER FILE. A session legitimately working with a draft
should hear it the first time and then be left alone.

FAILS OPEN on everything: no directory, no file, an unreadable mtime, an
internal error. Debug to out/hook-guard.log.

Fixtures: ops/draft-export-gate-selftest.py.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    DEBUG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    DEBUG = os.path.join(REPO, "out", "hook-guard.log")
SEEN = os.path.join(REPO, "out", ".draft-export-gate-seen")

# The vault render for the three files a session most often reaches for. Values
# are vault-relative; the vault root is resolved at call time so a machine
# without it simply reports the store instead.
LIVE_RENDER = {
    "decision-history.md": "00_Context/decision-history.md",
    "open-loops.md": "00_Context/open-loops.md",
    "compiled-rules-joe.md": "00_Context/compiled-rules-joe.md",
    "compiled-rules-shared.md": "DNA/compiled-rules-shared.md",
}

# What to call instead, per file. The store is authoritative for all of them;
# these are the verbs that read it.
STORE_VERB = {
    "decision-history.md": "the decision store (v_decision_entry), or "
                           "`find` / `catch-me-up` for a record's own rulings",
    "open-loops.md": "`loop-board`",
    "compiled-rules-joe.md": "`standing-context`",
    "compiled-rules-shared.md": "`standing-context`",
}

# A READ of the directory, not a mention of it. Requires a path separator after
# the directory name, which an `echo "... out/exports ..."` does not have, and
# which is the same prose-versus-command distinction guard-unattended.py draws.
READS_DRAFT = re.compile(r"(?:^|[\s\'\"=(<|;&])(?:[\w./~$${}-]*/)?out/exports/([\w.-]+)")

# Running the exporter is not misreading its output.
IS_EXPORT_RUN = re.compile(r"\b(?:run\.sh|/)\s*export\b|CARR_EXPORT_LIVE", re.I)


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(DEBUG, "a") as fh:
            fh.write(f"{ts} draft-export-gate {msg.rstrip()}\n")
    except Exception:
        pass


def vault_root():
    home = os.path.expanduser("~")
    for base in (os.path.join(home, "My Drive", "CARR AI"),
                 os.path.join(home, "Library", "CloudStorage")):
        if os.path.isdir(base):
            if base.endswith("CARR AI"):
                return base
            for entry in sorted(os.listdir(base)):
                cand = os.path.join(base, entry, "My Drive", "CARR AI")
                if os.path.isdir(cand):
                    return cand
    return None


def age_phrase(seconds):
    hours = seconds / 3600.0
    if hours < 1:
        mins = max(1, int(seconds / 60))
        return f"{mins} minute{'s' if mins != 1 else ''} old"
    if hours < 48:
        return f"{hours:.0f} hours old"
    return f"{hours / 24:.1f} days old"


def already_said(key, session):
    """Once per session per file. FIXTURES ARE EXEMPT, and that is not a
    convenience: the dedup is state on disk, so without this exemption the
    selftest's second case is silenced by its first and every later assertion
    reads an empty message. Found exactly that way on the first run."""
    if session == "selftest":
        return False
    try:
        if os.path.exists(SEEN):
            with open(SEEN) as fh:
                if key in {l.strip() for l in fh}:
                    return True
        os.makedirs(os.path.dirname(SEEN), exist_ok=True)
        with open(SEEN, "a") as fh:
            fh.write(key + "\n")
    except Exception:
        pass
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        cmd = ti.get("command") if isinstance(ti, dict) else None
        if not isinstance(cmd, str) or not cmd.strip():
            sys.exit(0)
        if IS_EXPORT_RUN.search(cmd):
            sys.exit(0)

        names = READS_DRAFT.findall(cmd)
        if not names:
            sys.exit(0)

        root = os.environ.get("CARR_DRAFT_EXPORT_ROOT") or REPO
        drafts = os.path.join(root, "out", "exports")
        if not os.path.isdir(drafts):
            dlog("ALLOW(no-draft-dir)")
            sys.exit(0)

        now = time.time()
        lines, reported = [], []
        for name in names:
            path = os.path.join(drafts, name)
            if not os.path.exists(path):
                continue
            key = f"{payload.get('session_id')}::{name}"
            if already_said(key, payload.get("session_id")):
                continue
            draft_age = age_phrase(now - os.path.getmtime(path))
            row = f"  · out/exports/{name} — DRAFT, {draft_age}"
            rel = LIVE_RENDER.get(name)
            vroot = vault_root()
            if rel and vroot:
                live = os.path.join(vroot, rel)
                if os.path.exists(live):
                    row += (f"\n      live render: {rel} — "
                            f"{age_phrase(now - os.path.getmtime(live))}")
            verb = STORE_VERB.get(name)
            if verb:
                row += f"\n      authoritative: {verb}"
            lines.append(row)
            reported.append(name)

        if not lines:
            sys.exit(0)

        dlog(f"CONTEXT files={','.join(reported)}")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "additionalContext": (
                    "out/exports/ IS A DRAFT DIRECTORY, NOT THE RECORD. "
                    "`run.sh export` writes there by default and only "
                    "CARR_EXPORT_LIVE=1 reaches the vault, so these files go "
                    "arbitrarily stale and nothing announces it — the directory "
                    "is gitignored, so no diff and no review ever shows the "
                    "drift.\n\n" + "\n".join(lines) +
                    "\n\nOn 2026-08-15 a session grepped a 32-hour-old draft of "
                    "decision-history, missed a ruling made four hours earlier, "
                    "and told Joe a whole Program 4 bullet had never been "
                    "scoped (defect 280b1b6c). Reading a draft is fine when you "
                    "mean to; deciding anything from one is the trap. If the "
                    "answer matters, ask the store."
                ),
            }
        }))
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
