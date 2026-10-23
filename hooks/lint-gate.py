#!/usr/bin/env python3
"""lint-gate.py — the PostToolUse writing-lint gate (idea-bank #32, job b).

WHY. `run.sh lint` is a gate in doctrine and a habit in practice: it only fires
when someone remembers it. `DNA/writing-rules.md` binds EVERY surface a prospect
could ever see, and the expensive failures are the ones that ship. This makes the
gate mechanical for the one moment that matters, the write itself.

WHAT IT DOES. After a Write or Edit lands on a plausibly client-facing vault file,
it runs the linter and puts the result back in front of the session. It NEVER
blocks: the linter's own doctrine is that HARD is blocked and REVIEW must be
cleared consciously, and "consciously" means a human or the session deciding, not
a regex. A clean lint run is also explicitly NOT the writing-audit; that judgment
pass still belongs to the audit skill, and the output says so.

SCOPE, deliberately narrow. Only the CARR vault, only surfaces a prospect could
see. Repo code, scratchpad files, generated renders and internal ledgers are
skipped, because a linter that fires on everything is a linter people learn to
ignore. Generated files are skipped for a second reason: they are never
hand-edited, so linting them would only ever report the exporter's output.

FAILS OPEN AND SILENT. Any error, missing linter, or timeout exits 0 with no
output. This is an advisory gate on top of an existing doctrine, and it must
never be the reason a write appears to fail.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
# Derived from HOME, not typed in (2026-09-23 audit): the literal /Users/booko
# spelling was right on one machine and silently matched nothing on any other.
VAULT = os.path.join(os.path.expanduser("~"), "Library", "CloudStorage",
                     "GoogleDrive-joe.bookout.carr.us@gmail.com", "My Drive", "CARR AI")
CANONICAL = os.environ.get("CARR_ROOT") or os.path.join(os.path.expanduser("~"), "carr-system")
RUN_SH = os.path.join(CANONICAL, "run.sh")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(os.path.expanduser("~/carr-system"))
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")
TIMEOUT = 25


def log(msg):
    """Added 2026-08-03 by the IT hook-coverage sweep, which found this was the
    ONLY hook of the five that wrote nothing, ever. That made the one gate
    enforcing writing-rules.md on client-facing surfaces unauditable: nobody
    could answer "has it ever fired", or "did it skip that draft or pass it".
    A check that cannot be seen is a defect even while the thing it watches is
    fine, and it is the same silent-success shape as the markdown-write defect
    this whole night was about."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} lint-gate {msg.rstrip()}\n")
    except Exception:
        pass

# Generated renders — never hand-edited, so never linted here.
GENERATED = (
    "open-loops.md", "open-loops-backlog.md", "action-required.md", "team-loops.md",
    "compiled-rules-shared.md", "compiled-rules-joe.md", "compiled-rules-dell.md",
    "introduction-rules.md", "clients-active.md", "hunt-ledger.md",
    "deals-reciprocity.generated.md", "record-layer-dictionary.md",
)

# INTERNAL BY CONSTRUCTION — never linted, checked BEFORE the surface map.
#
# Added 2026-08-03. `("Deal Management", "proposal")` below is a broad fragment
# and it swallowed `DNA/Deal Management/record-layer/`, which is the ENGINEERING
# folder: work orders, design memos, onboarding runbooks. Those got the full
# prospect-facing ruleset, so a MARKETING rule enforcing solo-Joe framing fired
# on `dell-onboarding-runbook`, a document whose entire subject is Dell, and a
# style rule about colons fired on a spec. Six "hard ban" hits on a file no
# prospect will ever see.
#
# Rule ede4c735 is explicit that writing-rules binds PROSPECT-VISIBLE SURFACES
# ONLY, so this is not a relaxation — the gate was overreaching its own charter.
# The cost of overreach is not noise, it is that a human learns the alarm is
# usually wrong and starts clicking past it, and then it catches nothing on the
# day it is right. That is the same failure the façade check (rule 28) names for
# health checks reporting everything at one severity.
#
# Deliberately narrow: only folders that are internal by their nature. Anything
# that could plausibly reach a prospect keeps its surface, because a false
# NEGATIVE here is far worse than a false positive.
INTERNAL = (
    "/record-layer/",      # work orders, design memos, specs, runbooks
    "/dna/team/",          # protocol, twin-system playbook, the starter kit
    "/00_context/",        # operating notes, decision history, loops, handoffs
    "/automation/",        # scripts, job docs
    "/archive/",           # snapshots and retired material
    "/idea-inbox/",        # raw capture, never client-facing
    "/_to_delete/",        # staging for deletion
    "/_asset_staging/",    # raw intake
)

# Path fragment -> linter surface. First match wins, most specific first.
SURFACES = (
    ("Marketing/Social Media", "social"),
    ("DNA/Marketing", "social"),
    ("/Marketing/", "social"),
    ("Outreach/", "email"),
    ("templates.md", "email"),
    ("intake/", "proposal"),
    ("Output/", "proposal"),
    ("benefit-summary", "proposal"),
    ("proposals", "proposal"),
    ("Deal Management", "proposal"),
    ("GBP", "web"),
    ("SEO", "web"),
    ("landing", "web"),
)


def surface_for(path):
    low = path.lower()
    if "scratchpad" in low or "/out/" in low or ".generations" in low:
        return None
    if os.path.basename(path) in GENERATED:
        return None
    if not path.startswith(VAULT):
        return None
    if not path.endswith((".md", ".txt", ".html")):
        return None
    if any(frag in low for frag in INTERNAL):
        return None
    for frag, surf in SURFACES:
        if frag.lower() in low:
            return surf
    return None


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run


@decision(failure="raise")
def decide(payload):
    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        path = ti.get("file_path") or ti.get("filePath") or ""
        if not path:
            sys.exit(0)

        surface = surface_for(path)
        if not surface:
            sys.exit(0)
        if not (os.path.exists(RUN_SH) and os.path.exists(path)):
            sys.exit(0)

        res = subprocess.run(
            [RUN_SH, "lint", path, "--surface", surface],
            capture_output=True, text=True, timeout=TIMEOUT,
            cwd=CANONICAL,
        )
        out = (res.stdout or "") + (res.stderr or "")
        if not out.strip():
            sys.exit(0)

        tail = "\n".join(out.strip().splitlines()[-25:])
        rel = path[len(VAULT):].lstrip("/")
        if "FAIL" in out or "hard-ban" in out:
            msg = (f"WRITING-LINT: HARD BAN HIT on {rel} (surface: {surface}). "
                   f"writing-rules.md says do not ship until these are zero. Fix before this "
                   f"reaches Joe or a prospect.\n\n{tail}")
        elif "REVIEW" in out:
            msg = (f"WRITING-LINT: REVIEW items on {rel} (surface: {surface}). No hard bans. "
                   f"Clear each one consciously or fix it; do not ignore silently. A clean lint "
                   f"run is not the writing-audit.\n\n{tail}")
        else:
            sys.exit(0)

        # PostToolUse reaches the session ONLY through structured JSON. Plain text
        # on stdout is not injected into context, so the earlier draft of this hook
        # would have run the linter and thrown the result away. additionalContext
        # arrives as a system reminder the session reads.
        log(f"REPORT {msg.splitlines()[0][:180] if msg else '(empty)'}")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": msg,
            }
        }))
        sys.exit(0)
    except Exception as exc:
        # fails open and silent to the session, per the docstring — but NOT
        # silent to the log, which is the whole point of adding one.
        log(f"ALLOW(internal-error) {type(exc).__name__}: {exc}")
        sys.exit(0)


def main():
    sys.exit(run(decide))


if __name__ == "__main__":
    sys.exit(main())
