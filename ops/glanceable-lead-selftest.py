#!/usr/bin/env python3
"""ops/glanceable-lead-selftest.py — acceptance test for ops/glanceable-lead-check.py.

THE RULE (17ffd587, Joe, Jul 15/16 2026): never surface "call this lead / follow
up" on open-loops, the heartbeat, or the Monday brief. Leads live on The Lead
Board and Joe works them there. CARVE-OUT, in the rule's own words: a follow-up
DATE Joe sets on a real DEAL is the reminder system working — surface it.

WHY IT NEEDS A CHECK. The rule governs what reaches Joe's daily surfaces, and
those surfaces are GENERATED. Nothing stops a lead-outreach row being written
into a loop and rendering onto the hot list the next morning; the rule has held
so far because sessions remembered it, which is the failure mode rule ab814a26
names.

WHAT COUNTS AS A VIOLATION, and the narrowness is the design: a lead reference
sitting in an OUTREACH instruction. "Send Dr. X (L-221) the advisory" is the
shape the rule forbids. A lead reference doing bookkeeping — "Registry L-004
stamp owed to the next writer" — is not an outreach reminder and must not be
flagged, or the check starts crying about its own record-keeping.

THE HOT LIST IS GLANCEABLE; THE BACKLOG IS NOT. open-loops.md is always-read.
open-loops-backlog.md says in its own header that it is read on cadence, not
every session, which is where a dormant lead item is allowed to live.

RUN IT:
    python3 ops/glanceable-lead-selftest.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO, "ops", "glanceable-lead-check.py")

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def scan(text: str, name: str = "open-loops.md", assume: str = "lead"):
    """Run the check over one throwaway surface.

    `assume` stands in for the record lookup so the MATCHER is tested without a
    round trip per candidate: "lead" means the ref resolves to a lead-only party,
    "carveout" means it also holds a client record or live deal, "unknown" means
    the record layer could not be reached.
    """
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, name)
        with open(f, "w") as fh:
            fh.write(text + "\n")
        p = subprocess.run([sys.executable, CHECK, "--path", f],
                           capture_output=True, text=True, timeout=120,
                           env={**os.environ, "CARR_GLANCEABLE_ASSUME": assume})
        return p.returncode, p.stdout + p.stderr


print("glanceable-lead-selftest — a lead in an outreach instruction, and nothing else")

check("the check exists", os.path.exists(CHECK), CHECK)
if not os.path.exists(CHECK):
    sys.exit(1)

# ── the shapes the rule forbids ─────────────────────────────────────────────
for label, line in [
    # A DIFFERENT lead ref than the live one on purpose: L-221 is in the known
    # baseline, so using it here would test the baseline rather than the match,
    # and would have gone green for the wrong reason. The honorific stays,
    # because the period in "Dr." is what the first version of the clause window
    # broke on.
    ("send a document to a lead, honorific and all",
     "| 338 | joe | Send Dr. Randall Kessler (L-902) the buyer advisory on The Palisade."),
    ("call a lead", "| 12 | joe | Call L-98 about the Milton startup this week."),
    ("follow up with a lead", "| 40 | joe | Follow up with L-77, no answer last time."),
    ("email a lead", "| 41 | joe | Email L-55 the intro packet."),
    ("nudge a lead", "| 42 | joe | Nudge L-31 if nothing lands by Friday."),
    ("reach out to a lead", "| 43 | joe | Reach out to L-12 before the conference."),
]:
    rc, out = scan(line)
    check(f"caught: {label}", rc != 0, out[:220])

# ── what must stay silent ───────────────────────────────────────────────────
for label, line, fname in [
    ("a lead reference doing bookkeeping",
     "| 83 | joe | Registry L-004 stamp (7/30) owed to the next guarded writer.",
     "open-loops.md"),
    ("THE CARVE-OUT: a dated follow-up on a real DEAL",
     "| 83 | joe | 2026-08-12 Harborline Pelvic Therapy (C-112) — follow up on the draft lease.",
     "open-loops.md"),
    ("a client follow-up with no lead reference at all",
     "| 90 | joe | Call Dr. Stroud about the Oxford markup before the 20th.",
     "open-loops.md"),
    ("the same outreach row on the BACKLOG, which is read on cadence",
     "| 338 | joe | Send Dr. Randall Kessler (L-221) the buyer advisory.",
     "open-loops-backlog.md"),
    ("a lead mentioned as the SOURCE of something, not a task",
     "| 44 | joe | The Palisade advisory came out of the L-221 intake, filed for reuse.",
     "open-loops.md"),
    ("a superseded row naming a lead",
     "| 45 | joe | Renumbered from L-19; the row it replaced is closed, not abandoned.",
     "open-loops.md"),
]:
    rc, out = scan(line, fname)
    check(f"silent on: {label}", rc == 0, out[:260])

# ── THE CARVE-OUT, and the false positive that produced it ─────────────────
# On 2026-08-15 this check flagged loop 338 — "Send Dr. Randall Kessler (L-221) the
# buyer advisory" — and a session baselined it as a known violation. The RECORD
# said Kessler holds a client row (C-199) and a live deal, The Palisade Investment
# Purchase. That makes the row a dated follow-up on a real deal, which rule
# 17ffd587 writes into itself as permitted. The render said lead; the record said
# client with a deal; only one of those is the answer.
LIVE_ROW = ("| 338 | joe | 2026-08-13 Send Dr. Randall Kessler (L-221, Jackson MS "
            "surgeon) the buyer advisory on The Palisade.")

rc, out = scan(LIVE_ROW, assume="carveout")
check("a lead who ALSO holds a client record or live deal is the carve-out",
      rc == 0, out[:300])
check("...and the pass names it as a carve-out rather than staying silent",
      "carve-out" in out.lower(), out[:300])

rc, out = scan(LIVE_ROW, assume="lead")
check("the same row IS a finding when the person is lead-only", rc != 0, out[:300])

rc, out = scan(LIVE_ROW, assume="unknown")
check("an unreachable record is UNVERIFIED, never counted as a violation",
      rc == 0, out[:300])
check("...and says so out loud rather than reporting clean",
      "unverified" in out.lower(), out[:300])

# The lookup asks by NAME because `find` returns nothing for a bare ref. Asking
# it for "L-221" got an empty answer, and reading that emptiness as "no client
# link" is how the false positive survived.
sys.path.insert(0, os.path.join(REPO, "ops"))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("glanceable_lead_check", CHECK)
assert _spec is not None and _spec.loader is not None
_mod = _ilu.module_from_spec(_spec)
sys.modules["glanceable_lead_check"] = _mod
_spec.loader.exec_module(_mod)
check("the person's name is extracted from the row, since a bare ref finds nothing",
      _mod.person_for(LIVE_ROW) == "Dr. Randall Kessler",
      repr(_mod.person_for(LIVE_ROW)))
check("...and the outreach verb is not mistaken for part of the name",
      "Send" not in (_mod.person_for(LIVE_ROW) or ""),
      repr(_mod.person_for(LIVE_ROW)))

# ── the finding has to be usable ────────────────────────────────────────────
rc, out = scan("| 12 | joe | Call L-98 about the Milton startup this week.")
check("the finding names the lead and the surface",
      "L-98" in out and "open-loops.md" in out, out[:260])
check("...and points at the Lead Board as the right home",
      "lead board" in out.lower(), out[:300])

# ── the real surfaces ───────────────────────────────────────────────────────
p = subprocess.run([sys.executable, CHECK], capture_output=True, text=True,
                   cwd=REPO, timeout=180)
# THIS ASSERTION IS THE ENFORCEMENT. Accepting either exit code would make the
# whole file a report nobody reads. The live surfaces are clean apart from the
# one row held in KNOWN, so demanding zero costs nothing today and fails CI the
# moment a new lead-outreach reminder renders onto Joe's daily view.
check("NO LEAD-ONLY OUTREACH REMINDER ON A LIVE SURFACE — a new one fails here",
      p.returncode == 0, (p.stdout + p.stderr)[:500])
check("...and names which surfaces it read, so an empty pass is not a silent one",
      "surface" in (p.stdout + p.stderr).lower(), (p.stdout + p.stderr)[:400])

print()
if FAILED:
    print(f"FAILED {len(FAILED)} check(s):")
    for _label in FAILED:
        print(f"  - {_label}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
