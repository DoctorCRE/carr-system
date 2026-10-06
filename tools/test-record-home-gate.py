#!/usr/bin/env python3
"""test-record-home-gate.py — proves the record-home gate denies what it claims
and, just as importantly, ALLOWS ordinary narrative editing.

The allow cases are the point. A deny gate that also blocks doctrine edits gets
disabled within a day, and then it protects nothing.

    .venv/bin/python tools/test-record-home-gate.py     # exit 0 = all pass
"""
import json
import os
import subprocess
import tempfile
import sys

# Script-relative, NOT expanduser("~/carr-system") — see the same fix in
# test-ledger-sweep.py. A checkout outside $HOME made this file crash on import
# rather than report a failure.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "record-home-gate.py")
VAULT = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

DENY, ALLOW = "deny", "allow"

# THIS MACHINE'S VERDICT FOR A JOB-OUTPUT PATH, and why it is computed instead
# of written down. Those rows retire PER PARTNER (Joe migrated 2026-08-14, Dell
# at the 8/21 cutoff), so the honest expectation for a real hook invocation
# depends on whose machine is running the test. Hardcoding Joe's answer passed
# on his Mac and failed on the GitHub runner, which has no identity file and so
# resolves as unmigrated — caught by CI on the first push of that change.
#
# The per-partner LOGIC is pinned exactly by the unit cases below, which inject
# the actor and need no machine. What these subprocess cases prove is the layer
# above it: that the hook actually consults the manifest and applies its verdict
# end to end, on whatever machine is running.
sys.path.insert(0, os.path.join(REPO, "hooks"))
from md_manifest import md_write_verdict  # noqa: E402


def job_output(rel):
    """The manifest's own verdict for a vault-relative job-output path.

    ASK THE MANIFEST, DO NOT RESTATE ITS RULE. The previous version computed
    `DENY if local_actor() in MIGRATED_PARTNERS else ALLOW`, which is only half
    the rule: an unmigrated machine is allowed until the row's retirement date,
    not forever. Once Dell's 2026-08-21 cutoff passed, that unmigrated branch
    began claiming ALLOW where the manifest denies. Joe's Mac kept passing,
    because the migrated branch of the expression is right.

    That is the same shape as the bug the expression was introduced to fix: an
    answer that happened to be true on one machine. Deriving it removes the
    duplicated rule outright, and these subprocess cases still prove what they
    were written to prove — that the hook consults the manifest and applies its
    verdict end to end, on whatever machine runs. The per-partner LOGIC stays
    pinned by the unit cases below, which inject both actor and date and so
    cannot drift with either.
    """
    return DENY if md_write_verdict(rel) else ALLOW

CASES = [
    # (label, expected, tool, tool_input)
    ("A · generated render (shared rules)", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/compiled-rules-shared.md", "content": "x"}),
    ("A · generated render (open-loops)", DENY, "Edit",
     {"file_path": f"{VAULT}/00_Context/open-loops.md", "new_string": "x"}),
    ("A · generated render (non-md: deals json)", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/Deal Management/panhandle-team-deals.json", "content": "{}"}),

    # --- the 2026-08-03 IT sweep found 27 of 41 targets unguarded. These four
    # were all in that gap; they pass only because the set is now parsed from
    # exporters/targets.py instead of retyped.
    ("A · client dossier (the worst case, list-guarded)", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/LumoraDentalGroup.md", "new_string": "deal update"}),
    # NOT a blanket directory rule, and that distinction is load-bearing:
    # prospects/ holds 23 GENERATED dossiers alongside hand-authored files the
    # client-intake agent writes on purpose. Guarding the directory blocked those
    # too, and over-blocking a partner's own writing surface is how a gate ends up
    # switched off. The set is the exporter's own DOSSIER_FILES list.
    ("P0+ · intake file in prospects/ follows this machine's partner",
     job_output("DNA/Clients/prospects/Castillo-intake.md"), "Write",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/Castillo-intake.md", "new_string": "x"}),
    ("P0+ · enterprise file in prospects/ follows this machine's partner",
     job_output("DNA/Clients/prospects/NationalAccount-enterprise.md"), "Edit",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/NationalAccount-enterprise.md", "new_string": "x"}),
    ("P0+ · a name not in DOSSIER_FILES is DENIED too (closed 2026-08-14)",
     job_output("DNA/Clients/prospects/BrandNewClient.md"), "Write",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/BrandNewClient.md", "content": "x"}),
    ("A · hunt-ledger (was unguarded)", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/Network/hunt-ledger.md", "new_string": "x"}),
    ("A · record-layer-dictionary (was unguarded)", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/Team/record-layer-dictionary.md", "new_string": "x"}),

    ("B · NEW md in 00_Context/", DENY, "Write",
     {"file_path": f"{VAULT}/00_Context/audit-findings-2026-08-03.md", "content": "findings"}),
    ("B · NEW md at the vault root", DENY, "Write",
     {"file_path": f"{VAULT}/DECISIONS.md", "content": "we decided"}),

    ("C · handoff, new file", DENY, "Write",
     {"file_path": f"{VAULT}/00_Context/handoffs/handoff-2026-08-04-thing.md", "content": "x"}),
    ("C · handoff, EDIT to an existing one", DENY, "Edit",
     {"file_path": f"{VAULT}/00_Context/handoffs/handoff-2026-08-02-rule-curation.md",
      "new_string": "session 2 delta"}),

    ("D · rule uuid + rule verb in a narrative file", DENY, "Edit",
     {"file_path": f"{VAULT}/CLAUDE.md",
      "new_string": "activate-rule on 3bc574d9-f672-4619-9624-13ffef436a3f"}),

    # ---- PHASE 0 (2026-08-07, decision 82a2fb62): deny-by-default flips the
    # old "doctrine editing stays open" posture. Doctrine edits are now DENIED —
    # content routes through verbs until the store's write verbs land (P2) and
    # the corpus migrates (P4/P5). The allow set is the exact-path manifest
    # (hooks/md_manifest.py) plus the retiring job-output prefixes.
    ("P0 · doctrine edit under DNA/ now DENIED", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/writing-rules.md", "new_string": "no em-dashes"}),
    ("P0 · NEW doctrine file under DNA/ now DENIED", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/Marketing/new-playbook.md", "content": "playbook"}),
    ("P0 · idea-inbox distillation now DENIED", DENY, "Write",
     {"file_path": f"{VAULT}/00_Context/idea-inbox/2026-08-08-some-study.md", "content": "x"}),
    ("P0 · INDEX.md edit now DENIED (migrates in P4)", DENY, "Edit",
     {"file_path": f"{VAULT}/INDEX.md", "new_string": "x"}),
    ("P0 · uppercase extension does not slip through", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/Team/notes.MD", "content": "x"}),
    ("allow · CLAUDE.md ordinary edit (manifest exact)", ALLOW, "Edit",
     {"file_path": f"{VAULT}/CLAUDE.md", "new_string": "rev 11: weekends are off"}),
    ("allow · AGENTS.md edit (manifest exact)", ALLOW, "Edit",
     {"file_path": f"{VAULT}/AGENTS.md", "new_string": "x"}),
    ("P0+ · weekly brief output follows this machine's partner",
     job_output("DNA/Network/briefs/2026-08-10-network-brief.md"), "Write",
     {"file_path": f"{VAULT}/DNA/Network/briefs/2026-08-10-network-brief.md", "content": "x"}),
    ("allow · repo file, outside the vault", ALLOW, "Write",
     {"file_path": os.path.join(REPO, "specs", "some-spec.md"), "content": "spec"}),
    ("allow · scratchpad", ALLOW, "Write",
     {"file_path": "/private/tmp/claude-501/scratch/notes.md", "content": "scratch"}),
    ("allow · non-markdown in the vault", ALLOW, "Write",
     {"file_path": f"{VAULT}/Automation/lead-board.html", "content": "<html>"}),
    ("allow · bare uuid with no rule verb", ALLOW, "Edit",
     {"file_path": f"{VAULT}/CLAUDE.md",
      "new_string": "session id 3bc574d9-f672-4619-9624-13ffef436a3f"}),
    ("allow · a tool the gate does not cover", ALLOW, "Bash",
     {"command": f"rm {VAULT}/x.md"}),
]


# THE DOSSIER ROSTER IS SYNTHETIC HERE (WR-000049). The real roster is a
# gitignored per-machine file whose keys are client names; the gate reads it
# through exporters/dossier_roster.py, which honours CARR_DOSSIER_ROSTER. Every
# subprocess below points that variable at this temp file, so the list-guarded
# case is hermetic: the same verdict on Joe's Mac, on Dell's, and on the runner.
SYNTHETIC_DOSSIER = "LumoraDentalGroup.md"
_ROSTER_DIR = tempfile.mkdtemp(prefix="record-home-roster-")
SYNTHETIC_ROSTER = os.path.join(_ROSTER_DIR, "dossier-roster.local.json")
with open(SYNTHETIC_ROSTER, "w", encoding="utf-8") as _fh:
    json.dump({"dossiers": {SYNTHETIC_DOSSIER: "flat", "SampleClinic-Harbor.md": "chronological"}}, _fh)
NO_ROSTER = os.path.join(_ROSTER_DIR, "absent.json")


def run(tool, ti, roster=None):
    env = dict(os.environ, CARR_DOSSIER_ROSTER=roster or SYNTHETIC_ROSTER,
               CARR_HOOK_GUARD_LOG=os.path.join(_ROSTER_DIR, "guard.log"))
    p = subprocess.run([sys.executable, GATE],
                       input=json.dumps({"tool_name": tool, "tool_input": ti}),
                       capture_output=True, text=True, timeout=20, env=env)
    return p.returncode, (p.stderr or "").strip()


def roster_layer_cases():
    """The dossier layer (A) is list-guarded by the roster, not merely covered by
    the .md deny-by-default (B). Proven by the MESSAGE: A names a GENERATED
    render; with the roster absent the same path falls through to B."""
    ti = {"file_path": f"{VAULT}/DNA/Clients/prospects/{SYNTHETIC_DOSSIER}", "new_string": "x"}
    rc_in, err_in = run("Edit", ti)
    rc_out, err_out = run("Edit", ti, roster=NO_ROSTER)
    return [
        ("roster · a rostered dossier is denied as a GENERATED render (layer A)",
         rc_in == 2 and "is a GENERATED render" in err_in),
        ("roster · with no roster file the dossier layer names nothing",
         "is a GENERATED render" not in err_out),
    ]


def manifest_unit_cases():
    """Direct md_manifest checks the subprocess harness cannot reach: the
    retirement clock is injectable only in-process."""
    sys.path.insert(0, os.path.join(REPO, "hooks"))
    from datetime import date, timedelta
    from md_manifest import md_write_verdict, CUTOFF
    cases = [
        # CLOSED EARLY, PER PARTNER — Joe, 2026-08-14: he is fully migrated, so
        # his side retires now and absorbs the breakage while Dell's still
        # works. The staggering IS the requirement, so both halves are asserted:
        # closing Dell early would be as wrong as leaving Joe open.
        ("unit · a migrated partner's brief row is CLOSED before the old cutoff",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF,
                          actor="joe") is not None),
        ("unit · an UNMIGRATED partner keeps the row until the cutoff",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF,
                          actor="dell") is None),
        ("unit · and the unmigrated partner still closes AFTER the cutoff",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF + timedelta(days=1),
                          actor="dell") is not None),
        ("unit · an UNRESOLVABLE identity falls back to the later date, so a "
         "broken identity file cannot retire an unmigrated partner early",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF,
                          actor="unresolved-machine") is None),
        ("unit · the social batch path is closed for a migrated partner",
         md_write_verdict("Marketing/Social Media/x-batch-2026-08-24-week.md",
                          today=CUTOFF, actor="joe") is not None),
        ("unit · closed-row message names the ruling, not just the cutoff",
         "eliminate the ability" in (md_write_verdict(
             "DNA/Network/briefs/x.md", today=CUTOFF, actor="joe") or "")),

        # THE FIVE WRITERS THE EARLY CLOSURE MISSED. Joe's ruling keeps retired
        # rows in the file instead of deleting them for one stated reason: "A
        # deleted row falls through to the generic deny message, which tells a
        # job's author only that markdown is closed. A retired row produces the
        # SPECIFIC message — its writer was supposed to be re-pointed — which
        # names what has to happen and to which job. The diagnosis is the point."
        #
        # These five never had rows at all, so they fell through to exactly the
        # generic message the mechanism exists to avoid, and the jobs writing
        # them kept running with no diagnosis attached. Found 2026-08-14 by the
        # write-effect check, which reported them as changed files nothing may
        # write.
        ("unit · the weekday brief's output is a RETIRED row for a migrated "
         "partner, not a generic deny",
         "supposed to be re-pointed" in (md_write_verdict(
             "00_Context/today.md", today=CUTOFF, actor="joe") or "")),
        ("unit · and it names the job that has to change",
         "local-briefs" in (md_write_verdict(
             "00_Context/today.md", today=CUTOFF, actor="joe") or "")),
        # NO NEW WRITE WINDOW. These three retire at CLOSED_EARLY for EVERYONE
        # rather than at CUTOFF, because they never had an allowance to stagger:
        # a CUTOFF row would GRANT an unmigrated partner a window that does not
        # exist today. Improving a denial's wording must not widen what may be
        # written, so the verdict is unchanged for both partners and only the
        # message improves.
        ("unit · the weekday brief is closed for an UNMIGRATED partner too — "
         "the new row must not grant a window that did not exist",
         md_write_verdict("00_Context/today.md", today=CUTOFF,
                          actor="dell") is not None),
        ("unit · and closed for an unmigrated partner even BEFORE the cutoff",
         md_write_verdict("00_Context/today.md", today=CUTOFF - timedelta(days=5),
                          actor="dell") is not None),
        ("unit · the learning job outputs are retired rows for a migrated partner",
         "supposed to be re-pointed" in (md_write_verdict(
             "Automation/Learning/weekly-learning-latest.md", today=CUTOFF,
             actor="joe") or "")),
        # NAMED BY WRITE SITE, not by the task that invokes them. The first cut
        # named the orchestrating task prompt and missed learning_jobs.py, which
        # writes two of the three outputs. A job author sent to a task prompt
        # finds a runbook, not the code that emits the file.
        ("unit · the learning row names the PIPELINE that writes the file",
         "learning_jobs.py" in (md_write_verdict(
             "Automation/Learning/weekly-learning-latest.md", today=CUTOFF,
             actor="joe") or "")),
        ("unit · the radar digest is a retired row for a migrated partner",
         "supposed to be re-pointed" in (md_write_verdict(
             "Automation/radar/radar-digest-latest.md", today=CUTOFF,
             actor="joe") or "")),
        # THE ATTRIBUTION MUST BE A WRITE SITE, NOT A GREP HIT. The first version
        # of this row named tools/health-check.py, because that file mentions the
        # path — in its WATCH table, a staleness list of (name, output glob, max
        # age, inputs, note). It only READS it. Naming the wrong job inside a
        # message whose whole purpose is naming the right one would send its
        # reader to a file that never touches the output. Both halves are pinned
        # here so the mistake cannot come back: the real writer is named, and the
        # watcher is asserted absent.
        ("unit · the radar row names the Monday run, the job that WRITES it",
         "Monday radar run" in (md_write_verdict(
             "Automation/radar/radar-digest-latest.md", today=CUTOFF,
             actor="joe") or "")),
        ("unit · and it does NOT name health-check, which only watches the path",
         "health-check" not in (md_write_verdict(
             "Automation/radar/radar-digest-latest.md", today=CUTOFF,
             actor="joe") or "")),
        # The rows that already existed gain the same naming, so the diagnosis
        # is uniform rather than only on the paths added last.
        ("unit · an EXISTING retired row now names its writer too",
         "batch" in (md_write_verdict(
             "Marketing/Social Media/x-batch-2026-08-24-week.md",
             today=CUTOFF, actor="joe") or "").lower()),
        # And a path nobody declared still gets the generic message — the change
        # must not turn every unknown file into a false "your job needs
        # re-pointing" claim.
        ("unit · an UNDECLARED path keeps the generic closed message",
         "supposed to be re-pointed" not in (md_write_verdict(
             "DNA/Some/hand-authored-note.md", today=CUTOFF, actor="joe") or "")),
        ("unit · brief prefix DENIED after cutoff",
         md_write_verdict("DNA/Network/briefs/x.md",
                          today=CUTOFF + timedelta(days=1)) is not None),
        ("unit · prospects prefix DENIED after cutoff",
         md_write_verdict("DNA/Clients/prospects/Castillo-intake.md",
                          today=CUTOFF + timedelta(days=1)) is not None),
        ("unit · deny message names the verbs",
         "log-decision" in (md_write_verdict("DNA/writing-rules.md") or "")),
        ("unit · exact allow is not a prefix (CLAUDE.md.bak-style)",
         md_write_verdict("CLAUDE.md.old.md") is not None),
    ]
    return cases


def main():
    failed = 0
    for label, ok in manifest_unit_cases() + roster_layer_cases():
        failed += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    for label, expected, tool, ti in CASES:
        rc, err = run(tool, ti)
        got = DENY if rc == 2 else ALLOW
        ok = got == expected
        failed += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}  (expected {expected}, got {got})")
        if not ok and err:
            print(f"          stderr: {err[:160]}")
        if ok and expected == DENY and not err:
            print("          FAIL: denied with no reason on stderr")
            failed += 1
    print(f"\npassed {len(CASES) - failed} · failed {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
