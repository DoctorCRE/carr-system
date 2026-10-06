#!/usr/bin/env python3
"""ops/glanceable-lead-check.py — no lead-outreach reminders on a glanceable surface.

THE RULE (17ffd587, Joe, Jul 15/16 2026): never surface "call this lead / follow
up" on open-loops, the heartbeat, or the Monday brief. Leads live on The Lead
Board and Joe works them there. Its carve-out, in the rule's own words: a
follow-up DATE Joe sets on a real DEAL is the reminder system working — surface
it at the right time.

WHY IT NEEDS A CHECK RATHER THAN A HABIT. The surfaces this rule governs are
GENERATED. Nothing stopped a lead-outreach row being written into a loop and
rendering onto Joe's hot list the next morning; the rule held because sessions
remembered it, which is the exact shape rule ab814a26 was taught about.

WHAT IS A VIOLATION, and the narrowness is the whole design: a lead reference
sitting inside an OUTREACH INSTRUCTION. "Send Dr. X (L-221) the advisory" is the
shape the rule forbids.

WHAT IS NOT, each one a real line from the live surfaces or a near neighbour:
  · bookkeeping — "Registry L-004 stamp owed to the next writer" mentions a lead
    ref while doing filing, and flagging it would make the check cry about the
    system's own record-keeping until somebody muted it.
  · the CARVE-OUT — a dated follow-up on a deal (C-###) is the reminder system
    working. This check never looks at deal refs.
  · provenance — "the advisory came out of the L-221 intake" names a source.

THE HOT LIST IS GLANCEABLE; THE BACKLOG IS NOT. open-loops.md is always-read.
open-loops-backlog.md states in its own header that it is read on cadence rather
than every session, which is where a dormant lead item is allowed to live. The
brief sections under out/brief-pack/ are glanceable and are checked.

IT REPORTS; IT DOES NOT EDIT. The remedy for a real hit is to move the row to
The Lead Board, which is a judgement about that lead's state and belongs to a
session or to Joe, not to a regex.

RUN IT:
    python3 ops/glanceable-lead-check.py
    python3 ops/glanceable-lead-check.py --path FILE
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Glanceable, in the rule's own listing: the hot loop list and the brief
# sections. The backlog is deliberately absent — see the module docstring.
SURFACES = [
    os.path.join(REPO, "out", "exports", "open-loops.md"),
    os.path.join(REPO, "out", "exports", "action-required.md"),
]
SURFACE_DIRS = [os.path.join(REPO, "out", "brief-pack")]

LEAD = r"L-\d+"

# An instruction aimed AT the lead. Ordered loosely by how often each shows up
# in a real loop row.
OUTREACH = (r"send|call|calling|email|e-mail|follow[ -]?up|following up|nudge|"
            r"reach out|reaching out|ping|check in|checking in|touch base|"
            r"outreach|contact|phone|text|invite|schedule a call")

# The lead ref and the instruction have to be in the SAME clause. A row that
# says "follow up on the lease" and separately cites a lead ref for provenance
# is not an instruction to work that lead.
#
# THE WINDOW ALLOWS PERIODS, and that is not laziness. The first version excluded
# them to stop a match running across a sentence boundary, which silently missed
# the live case this check was written for: "Send Dr. Randall Kessler (L-221) the
# buyer advisory" — the period in the honorific ended the clause four characters
# in. Real loop rows are full of "Dr.", and no punctuation rule separates an
# abbreviation from a sentence end. The distance cap does the clause-locality
# work instead, and BENIGN below carries the precision.
_CLAUSE = r"[^|\n]{0,70}"
VIOLATION = re.compile(
    rf"(?:{OUTREACH}){_CLAUSE}\b{LEAD}\b"
    rf"|\b{LEAD}\b{_CLAUSE}(?:{OUTREACH})",
    re.I)

# Bookkeeping and provenance, checked on the same clause and winning over the
# instruction match, because these read as verbs to a regex and are not.
BENIGN = re.compile(
    r"registry|stamp|renumber|superseded|closed, not abandoned|"
    r"came out of|filed for reuse|row it replaced|provenance|"
    r"promoted from|merged from", re.I)


# The person's name as written immediately before the ref, which is how these
# rows read: "Send Dr. Randall Kessler (L-221, Jackson MS surgeon...)". Needed
# because `find` searches by NAME and returns nothing for a bare ref — the first
# version of this lookup asked it for "L-221", got an empty answer, and read that
# emptiness as "no client link", which is how the false positive survived.
NAME_BEFORE_REF = re.compile(
    r"((?:[A-Z][\w.'-]*\s+){1,4})\(\s*L-\d+", re.UNICODE)


def person_for(line: str) -> str | None:
    m = NAME_BEFORE_REF.search(line)
    if not m:
        return None
    words = [w for w in m.group(1).split()
             if w.lower().rstrip(".") not in {"send", "call", "email", "nudge",
                                              "contact", "phone", "text", "the",
                                              "to", "with", "for", "invite"}]
    return " ".join(words).strip() or None


def is_carve_out(ref: str, repo: str, line: str = "") -> bool | None:
    """Does this lead ref belong to somebody who is ALSO a client with a deal?

    THE FALSE POSITIVE THIS EXISTS FOR, 2026-08-15. The first version of this
    check flagged loop 338 — "Send Dr. Randall Kessler (L-221) the buyer advisory" —
    and a session baselined it as a known violation. Opening the RECORD showed
    Kessler holds both a lead row and a client row (C-199) with a live deal, The
    Enclave Investment Purchase, phase research, owner joe. That makes the row a
    dated follow-up on a real deal, which is precisely the carve-out rule
    17ffd587 writes into itself. The render said "lead"; the record said
    "client with a deal"; only one of them is the answer.

    Returns True (carve-out, do not flag), False (lead-only, flag), or None when
    the record layer cannot be reached — and None must never be treated as
    False. No negative finding from a single collection (rule 2b889e80), and a
    false positive about Joe's live pipeline is worse than a miss here.
    """
    # Fixture control, for testing the MATCHER without a record round trip per
    # candidate. Never set outside the selftest; absent in every real run.
    forced = os.environ.get("CARR_GLANCEABLE_ASSUME")
    if forced == "lead":
        return False
    if forced == "carveout":
        return True
    if forced == "unknown":
        return None

    run = os.path.join(repo, "run.sh")
    if not os.path.exists(run):
        return None
    query = person_for(line) or ref
    try:
        p = subprocess.run(["zsh", run, "call", "find", json.dumps({"query": query})],
                           cwd=repo, capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    body = p.stdout or ""
    start = body.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(body[start:])
    except Exception:
        return None
    for link in (data.get("lead_client_links") or []):
        if link.get("lead_ref") == ref and link.get("client_ref"):
            return True
    if data.get("deals"):
        return True
    return False


def offenders(text: str) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if not VIOLATION.search(line):
            continue
        if BENIGN.search(line):
            continue
        out.append((i, line.strip()))
    return out


def surfaces() -> list[str]:
    found = [p for p in SURFACES if os.path.exists(p)]
    for d in SURFACE_DIRS:
        if os.path.isdir(d):
            found += [os.path.join(d, f) for f in sorted(os.listdir(d))
                      if f.endswith(".md")]
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", help="check one file instead of the live surfaces")
    args = ap.parse_args()

    targets = [args.path] if args.path else surfaces()

    # A file named as a backlog is read on cadence, not glanceable. Honoured by
    # NAME so a fixture and the real render behave identically.
    targets = [t for t in targets if "backlog" not in os.path.basename(t).lower()]

    findings: list[tuple[str, int, str]] = []
    carved: list[tuple[str, int, str]] = []
    unknown: list[tuple[str, int, str]] = []
    for path in targets:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for lineno, line in offenders(text):
            m = re.search(LEAD, line)
            ref = m.group(0) if m else "?"
            verdict = is_carve_out(ref, REPO, line) if m else False
            if verdict is True:
                carved.append((path, lineno, ref))
                continue
            if verdict is None:
                unknown.append((path, lineno, ref))
                continue
            findings.append((path, lineno, line))

    if not findings:
        names = ", ".join(os.path.basename(t) for t in targets) or "none found"
        extra = ""
        if carved:
            extra += (f"; {len(carved)} carve-out(s) — "
                      + ", ".join(sorted({r for _, _, r in carved}))
                      + " also hold a client record or live deal, which the rule "
                        "explicitly permits")
        if unknown:
            extra += (f"; {len(unknown)} UNVERIFIED — "
                      + ", ".join(sorted({r for _, _, r in unknown}))
                      + " (record layer unreachable; NOT counted as clean)")
        print(f"glanceable-lead: OK — no lead-only outreach reminder on "
              f"{len(targets)} surface(s): {names}{extra}")
        return 0

    print(f"glanceable-lead: {len(findings)} lead-outreach reminder(s) on a "
          f"glanceable surface — rule 17ffd587 keeps these off Joe's daily view:\n")
    for path, lineno, line in findings:
        rel = os.path.relpath(path, REPO)
        print(f"  {rel}:{lineno}")
        print(f"    {line[:150]}")
    print("\n  Leads live on THE LEAD BOARD and Joe works them there. A "
          "'call/send/follow up'\n  item on a lead does not belong on the hot loop "
          "list or the brief.\n"
          "\n  NOT a violation and never flagged: a dated follow-up on a real DEAL "
          "(C-###),\n  which the rule explicitly carves out as the reminder system "
          "working.\n"
          "\n  FIX: move the row to the Lead Board, or re-file it to "
          "open-loops-backlog.md,\n  which is read on cadence rather than every "
          "session. Deciding which is a\n  judgement about that lead, so this "
          "check reports and never edits.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
