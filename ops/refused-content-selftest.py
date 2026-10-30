#!/usr/bin/env python3
"""
refused-content-selftest.py — fixtures for hooks/refused_content.py, the shared
memory that closes the vault-write hole (rule 76a53dfe), written before it
(rule e65efc68).

THE RULE: a blocked markdown write is RE-ROUTED THROUGH THE VERBS, never hidden
somewhere the gate does not look.

THE HOLE, recorded in the rule's own text and still open when probed on
2026-08-14: the record-home gate refuses record content aimed at the vault —
now through both the file tool and the shell — and the identical content
written to a scratchpad or the temp directory is not refused by anything. The
content lands somewhere the record layer never sees, which is the exact outcome
the rule exists to prevent. Only the destination changed.

WHY THE OBVIOUS FIX IS THE WRONG ONE, and this is the whole design. Blocking
markdown writes to scratch paths would refuse the pull-request bodies, probe
scripts, and backups every session writes constantly — this suite's own author
wrote nine such files in the session that built it. That gate would be removed
within a day, and then the hole is open again with a note saying it was tried.

So the memory is SEQUENTIAL, not positional. The gate remembers the content it
just refused; a later write of substantially the same content ANYWHERE is what
gets stopped. A scratch write that has nothing to do with a refusal stays free,
which is almost all of them. This is the same shape as the resend check: keep
what was blocked, compare the next attempt.

WHAT MUST STAY TRUE:
  1. Content refused at the vault is recognised when it reappears elsewhere.
  2. Unrelated scratch content is never recognised — the common case stays free.
  3. Recognition survives reformatting: whitespace, case, and a changed heading
     do not launder the same body past it.
  4. A small edit does not launder it either, but a genuinely different
     document is not a match.
  5. Sessions are isolated; one session's refusal cannot block another's write.
  6. It fails OPEN on every error, and bounds what it stores.
  7. Remembering never raises into the caller.

RUNNING IT. No database, no network, no vault:

    .venv/bin/python ops/refused-content-selftest.py
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "hooks" / "refused_content.py"

passed = 0
failures: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=failures, counters={"passed": "pass"}, ns=globals())


if not MODULE.exists():
    print(f"  FAIL  the shared memory does not exist at {MODULE}")
    print("\n1 check(s) failed: not implemented")
    sys.exit(1)

spec = importlib.util.spec_from_file_location("refused_content", MODULE)
assert spec and spec.loader, f"could not load {MODULE}"
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

RECORD = (
    "# Vendor findings — Bryant Consultants\n\n"
    "- Strong intro from Dell, pursue this quarter\n"
    "- Territory overlaps the Okaloosa lane\n"
    "- Next step: confirm the referral is still active\n"
)

REFORMATTED = (
    "# VENDOR FINDINGS - BRYANT CONSULTANTS\n\n\n"
    "-   Strong intro from Dell, pursue this quarter\n"
    "-   Territory overlaps the Okaloosa lane\n"
    "-   Next step: confirm the referral is still active\n"
)

RETITLED = (
    "# Scratch notes\n\n"
    "- Strong intro from Dell, pursue this quarter\n"
    "- Territory overlaps the Okaloosa lane\n"
    "- Next step: confirm the referral is still active\n"
)

UNRELATED = (
    "# PR body\n\nFixes the guard's raw-device rule, which never fired on the\n"
    "command it is named for. Fourteen cases pin both directions.\n"
)

print("\nhooks/refused_content.py — a refused write cannot be hidden elsewhere")

with tempfile.TemporaryDirectory() as tmp:
    def fresh(name):
        d = Path(tmp) / name
        d.mkdir(exist_ok=True)
        return str(d)

    # ── 1. the hole itself ──────────────────────────────────────────────────
    home = fresh("hole")
    mod.remember_refusal(RECORD, "s1", home)
    check("content refused at the vault is recognised elsewhere",
          mod.was_refused(RECORD, "s1", home)[0])

    # ── 2. the common case stays free ───────────────────────────────────────
    check("unrelated scratch content is not recognised",
          not mod.was_refused(UNRELATED, "s1", home)[0])
    home2 = fresh("nomem")
    check("with nothing remembered, nothing is recognised",
          not mod.was_refused(RECORD, "s-none", home2)[0])

    # ── 3 & 4. reformatting and small edits do not launder it ───────────────
    check("whitespace and case changes do not launder it",
          mod.was_refused(REFORMATTED, "s1", home)[0])
    check("swapping the heading does not launder the body",
          mod.was_refused(RETITLED, "s1", home)[0])
    much_shorter = "- Strong intro from Dell, pursue this quarter\n"
    check("a single quoted line is NOT treated as the whole document",
          not mod.was_refused(much_shorter, "s1", home)[0],
          "quoting one line into a note is not hiding the record")

    # ── 5. sessions are isolated ────────────────────────────────────────────
    check("one session's refusal cannot block another's write",
          not mod.was_refused(RECORD, "s2", home)[0])

    # ── 6 & 7. failure behaviour ────────────────────────────────────────────
    broken = fresh("broken")
    Path(broken, "s3.json").write_text("{ not json")
    check("a corrupt memory fails open", not mod.was_refused(RECORD, "s3", broken)[0])
    check("a missing directory fails open",
          not mod.was_refused(RECORD, "s4", "/nonexistent-dir")[0])
    check("empty content is never a match",
          not mod.was_refused("", "s1", home)[0])
    try:
        mod.remember_refusal(RECORD, "s5", "/nonexistent-dir/deeper")
        check("remembering into an unwritable path never raises", True)
    except Exception as exc:
        check("remembering into an unwritable path never raises", False, str(exc))

    # Bounded: many refusals must not grow without limit.
    home3 = fresh("bounded")
    for i in range(60):
        mod.remember_refusal(f"# Doc {i}\n\n" + RECORD, "s6", home3)
    import json
    stored = json.loads(Path(home3, "s6.json").read_text())
    check("what it stores is bounded", len(stored.get("seen", [])) <= mod.MAX_REMEMBERED,
          f"{len(stored.get('seen', []))} entries")
    check("and the most recent refusal is still recognised",
          mod.was_refused("# Doc 59\n\n" + RECORD, "s6", home3)[0])

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("REFUSED CONTENT SELFTEST PASSED: a refused record is recognised wherever "
      "it reappears, and ordinary scratch writes stay free.")
