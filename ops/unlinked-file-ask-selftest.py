#!/usr/bin/env python3
"""
unlinked-file-ask-selftest.py — fixtures for the unlinked-file-ask half of
hooks/chat-lint-gate.py, written before it (rule e65efc68).

THE RULE, 8c1e6057, in Joe's own words: "in the future if you need me to edit a
file - always include the link to the file like you have above. that was way
easier". Its statement: whenever a partner is asked to edit, open, or review a
file, the message must carry a clickable markdown link to that file, never just
the path in prose.

THE HOLE, bucket U in the 2026-08-14 enforceability audit and probed still open
the hour this was written. Four shapes were fed to chat-lint-gate's own scan()
and every one passed, including "Take a look at deliverables/loi.docx and tell
me if the term is right." The harness prompt asks for markdown links in prose;
prose does not bind, which is the standing reason gates exist here.

WHY THIS IS A PREDICATE AND NOT A JUDGMENT. The binding condition has two
halves and both are mechanical: an ASK VERB aimed at the reader (open, edit,
review, look at, check, sign, approve) and a FILE PATH that is not already
wrapped as a markdown link. Neither needs the meaning of the sentence.

THE FALSE POSITIVE THAT WOULD KILL IT is the ordinary report. Session messages
name paths constantly — this very suite's own commit message names six — and a
check that fired on every mention would be muted within a day. A MENTION IS NOT
AN ASK: the ask verb must be present, and it must be pointed at the reader
rather than describing what the session itself did.

WHAT MUST STAY TRUE:
  1. An ask carrying a bare path is flagged.
  2. The same ask carrying a proper markdown link is NOT. This is the direction
     a carve-out tested only on the refusing side loses first.
  3. A path merely MENTIONED, with no ask, is never flagged.
  4. A session narrating its OWN reads and writes is never flagged — "I opened
     x.py" is not asking anyone to do anything.
  5. Code fences are exempt, exactly as the rest of this gate treats them.
  6. It never fires on prose that has no path in it at all.

RUNNING IT. No database, no network, no vault:

    .venv/bin/python ops/unlinked-file-ask-selftest.py
"""

import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "hooks" / "chat-lint-gate.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


print("\nhooks/chat-lint-gate.py — an ask to open a file carries a clickable "
      "link (8c1e6057)")

if not GATE.exists():
    print(f"  FAIL  the gate does not exist at {GATE}")
    sys.exit(1)

spec = importlib.util.spec_from_file_location("chat_lint_gate", GATE)
assert spec and spec.loader          # same narrowing idiom as ops/automerge-pilot-selftest.py
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if not hasattr(mod, "unlinked_file_ask_findings"):
    print("  FAIL  hooks/chat-lint-gate.py has no unlinked_file_ask_findings()")
    print("\n1 check(s) failed: not implemented")
    sys.exit(1)


def flagged(text):
    return bool(mod.unlinked_file_ask_findings(mod.strip_fences(text)))


# ── 1. the hole this closes ────────────────────────────────────────────────
ASKS = [
    "Take a look at deliverables/loi-vantora-dental.docx and tell me if the term is right.",
    "Can you open ops/config/services.json and confirm the cadence?",
    "Please review the draft at deliverables/proposal-gcph.docx before I send it.",
    "Edit corpus/templates/loi-template.docx and change the TI allowance.",
    "Sign off on deliverables/psa-dune-lakes.pdf when you get a minute.",
    "Check workspace/renewal-shortlist.xlsx — the Miramar row looks wrong.",
]
for text in ASKS:
    check(f"ask with a bare path is flagged: {text[:44]}…", flagged(text))

# ── 2. the direction a one-sided carve-out loses first ─────────────────────
LINKED = [
    "Take a look at [the Vantora LOI](deliverables/loi-vantora-dental.docx) and tell me if the term is right.",
    "Can you open [services.json](ops/config/services.json) and confirm the cadence?",
    "Please review [the GCPH proposal](deliverables/proposal-gcph.docx) before I send it.",
    "Check [the renewal shortlist](workspace/renewal-shortlist.xlsx) — the Miramar row looks wrong.",
]
for text in LINKED:
    check(f"the same ask WITH a link is allowed: {text[:44]}…", not flagged(text),
          "this is the direction that keeps the rule satisfiable")

# ── 3. a mention is not an ask ─────────────────────────────────────────────
MENTIONS = [
    "The gate lives in hooks/costar-lane-gate.py and refuses Chrome.",
    "ops/ci.sh globs ops/*-selftest.py, so the check never ran on the real tree.",
    "Counts come from audits/rule-enforceability-audit-2026-08-14.tsv.",
    "I added ops/map-row-evidence-check.py and blessed ops/config/gate-baseline.json.",
    "The freshness class refused the push because ops/config/gate-baseline.json moved.",
    # Observed in the 201-message sweep: a CLI flag is not an imperative.
    "bin/schema-snapshot.sh --check is red on main right now (exit 1).",
    "hooks/costar-lane-gate.py — the gate; PreToolUse deny, fails open on bad input.",
    "ops/githooks/commit-claims-check.py — new commit-msg check.",
]
for text in MENTIONS:
    check(f"mention with no ask is not flagged: {text[:44]}…", not flagged(text),
          "session reports name paths constantly; firing on those gets it muted")

# ── 4. the session narrating its OWN actions is not an ask ─────────────────
OWN = [
    "I opened ops/config/hooks.json and added the matcher.",
    "I will review tools/writing-lint.py next.",
    "Let me check hooks/record-home-gate.py before deciding.",
    "I looked at ops/githooks/pre-commit and it already refuses this.",
]
for text in OWN:
    check(f"session narrating its own work is not flagged: {text[:40]}…",
          not flagged(text), "'I opened x' asks nobody to do anything")

# ── 5. code fences are exempt, as everywhere else in this gate ─────────────
fenced = ("Here is the command:\n\n```bash\ncat deliverables/loi.docx\n```\n\n"
          "Run it when you can.")
check("a path inside a code fence is exempt",
      not flagged(mod.strip_fences(fenced)))

# ── 6. no path, no finding ─────────────────────────────────────────────────
for text in ["Take a look and tell me what you think.",
             "Can you review this when you get a chance?",
             "Please approve the change."]:
    check(f"an ask with no path at all is not flagged: {text[:40]}…",
          not flagged(text))

# ── the finding is usable: it names the path and says what to do ───────────
found = mod.unlinked_file_ask_findings(
    "Can you open ops/config/services.json and confirm the cadence?")
check("the finding names the offending path",
      any("services.json" in str(f) for f in found), str(found)[:120])

# ── the whole gate still runs, writing half included ───────────────────────
check("scan() still returns findings for the writing half",
      isinstance(mod.scan("Take a look at deliverables/loi.docx please."), list))

print(f"\n{passed} check(s) passed"
      + (f", {len(failures)} FAILED: {failures}" if failures else ""))
sys.exit(1 if failures else 0)
