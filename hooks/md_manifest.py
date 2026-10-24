"""md_manifest.py — the exact-path manifest behind the vault-wide .md write block.

PHASE 0 of the doctrine-store build (design/doctrine-store-build-2026-08-07.md,
council decision 82a2fb62; Joe's rulings 14181e60 database-first and 20dfdfcc
no-Drive-markdown). The council's enforcement verdict, unanimous after round 2:
IMMEDIATE hard block on hand-authored .md across every write surface, allowlist
by EXACT PATH plus renderer-owned output directories — no warn mode, no broad
globs, no "existing files are allowed."

This module is the ONE home of that allowlist. record-home-gate.py (Write/Edit
hook) and guard-unattended.py (Bash redirect door) both import it, because a
manual path and an automated path that do the same job must be the same code
(rule a8c55a47). The generated-render deny set is NOT here — it stays parsed
from exporters/targets.py by record-home-gate, single-sourced from the exporter.

SHAPE. Two row kinds, mirroring the md_write_manifest table the P1 schema adds
(this module is the file-side twin until that table renders it):

  ALLOW_EXACT   — vault-relative paths a session may Write/Edit. Machine-required
                  bootstrap surfaces only.
  ALLOW_PREFIX  — renderer/job-owned output directories, each with a mandatory
                  retires_at. These exist so P0 does not break running business
                  jobs (weekly briefs, client-intake dossier narratives) before
                  their outputs are re-pointed at the store. Every row DIES at
                  the migration cutoff; a prefix without retires_at is a config
                  error and the loader refuses it.

Everything else that is a .md under the vault: DENIED, with the message naming
the verb to use instead. Paths OUTSIDE the vault (the repo — git-owned; the
scratchpad — the ungoverned annex; ~/.claude and My Drive/.claude skill trees —
harness-required, repo-canonical from P6) are out of this gate's jurisdiction
on purpose; the vault is where all four incidents happened.
"""

from datetime import date

# The cutoff bound to Joe's calibration bet (decision 71cc1099): P6 acceptance
# target 2026-08-21. If the cutoff moves, move it HERE and the temporary rows
# move with it — never edit a row's date individually.
CUTOFF = date(2026, 8, 21)

# CLOSED EARLY, PER PARTNER — Joe, 2026-08-14: "I'm ALREADY migrated over fully.
# there is no need for anything running on my side of the system to wait until
# august 21 to retire ... that way i can fix any issues that pop up in the
# meantime before dell's system fully retires its aug 21 items."
#
# What prompted it: the nightly drift watch reported two new hand-authored .md
# files in the vault, and they were LEGITIMATE under the temporary rows below —
# the weekly social batch writing its own report to disk. The gate was working;
# the allowance was the hole.
#
# WHY THIS IS PER PARTNER AND NOT ONE DATE. The first cut of this change closed
# the rows for everyone, which would have retired Dell's jobs a week before his
# system migrates — the opposite of the intent. The date a temporary row dies is
# a property of WHOSE MACHINE IS WRITING, because migration happened per partner:
# Joe is done, Dell is not until 2026-08-21. Retiring Joe's early is deliberately
# a staggered rollout — he absorbs the breakage first and fixes it while Dell's
# side still works.
#
# WHY THE ROWS STAY IN THE FILE INSTEAD OF BEING DELETED. A deleted row falls
# through to the generic deny message, which tells a job's author only that
# markdown is closed. A retired row produces the SPECIFIC message below — "its
# writer was supposed to be re-pointed" — which names what has to happen and to
# which job. The diagnosis is the point.
#
# WHAT THIS BREAKS ON JOE'S MACHINE, ON PURPOSE AND LOUDLY: the weekly social
# batch's report file, the weekly network brief's output, and client-intake's
# dossier narrative. Each is filed as a loop naming the verb its content should
# go through instead. The system's own design intends this — a retired row's
# denial IS the alarm that a writer was never re-pointed.
CLOSED_EARLY = date(2026, 8, 13)

# Partners whose migration is COMPLETE, so every temporary row above dies for
# them now instead of at CUTOFF. Add "dell" here when his side is migrated —
# and when both are in, the rows and this set can go entirely.
MIGRATED_PARTNERS = {"joe"}

IDENTITY_FILE = "~/.config/carr/local-actor.json"


def local_actor():
    """Which partner's machine this is, from the same per-machine identity file
    ops/cutover-readiness.py reads, or None if it cannot be established.

    UNKNOWN FALLS BACK TO THE LATER DATE, deliberately. An unresolvable identity
    on Dell's machine must not retire his rows a week early; on Joe's it resolves
    fine, and if it ever stops resolving the health check's cutover-readiness row
    says so out loud. Erring toward the old behaviour is the direction that
    cannot break a partner who has not migrated.
    """
    import json as _json
    import os as _os
    try:
        with open(_os.path.expanduser(IDENTITY_FILE)) as fh:
            return (_json.load(fh).get("actor_slug") or "").strip() or None
    except Exception:
        return None

# Machine-required bootstrap surfaces a session may legitimately hand-edit
# before their content migrates. CLAUDE.md is a PARTIAL render (gist block is
# exporter-owned; the prose around it is doctrine Joe edits). AGENTS.md is the
# Cowork-facing standing context, same status.
ALLOW_EXACT = {
    "CLAUDE.md",
    "AGENTS.md",
}

# PERMANENT machine-required prefixes (no retirement): launchd-scheduled runs
# read these thin task prompts FROM DISK at fire time (the thin-prompt law,
# rule f04a05aa) — they are consumers' files the way SKILL.md is the
# harness's, and they migrate only WITH a re-pointed consumer (P6+, same
# class as skills). Ruled during the day-6/7 triage.
ALLOW_MACHINE_PREFIX = (
    "Automation/local-tasks/",
)

# Renderer/job-owned output directories, all retiring at cutoff. After CUTOFF
# these return DENY like everything else — the jobs writing here must be
# re-pointed at the store by P5 (loop #242 acceptance).
ALLOW_PREFIX = {
    # Monday/weekly brief outputs — written by scheduled brief sessions today;
    # P5 re-points brief delivery at records + email handover.
    "DNA/Network/briefs/": CUTOFF,
    # Hand-authored dossier narrative + client-intake's <name>-intake.md files.
    # The GENERATED dossiers in this same directory stay denied via the render
    # set (exact paths win over prefix allowance — deny is checked first).
    "DNA/Clients/prospects/": CUTOFF,
    # ── THE FIVE WRITERS THE EARLY CLOSURE MISSED, added 2026-08-14 ──────────
    # Joe's ruling keeps retired rows instead of deleting them for one reason,
    # in its own words: "A deleted row falls through to the generic deny
    # message, which tells a job's author only that markdown is closed. A
    # retired row produces the SPECIFIC message — its writer was supposed to be
    # re-pointed — which names what has to happen and to which job. The
    # diagnosis is the point."
    #
    # These paths never had rows AT ALL, so they fell through to exactly the
    # generic message the mechanism exists to avoid, and the jobs writing them
    # kept running with no diagnosis attached to the denial. Found by the
    # write-effect check on the day it shipped: it reported them as files
    # nothing may write, and none of them carried a writer's name.
    #
    # THEY RETIRE AT CLOSED_EARLY FOR EVERYONE, NOT AT CUTOFF, and that is a
    # deliberate narrowing. The first cut of this change gave them CUTOFF like
    # the rows above, which would have been faithful to the staggering rule but
    # wrong here: these paths never had an allowance to stagger, so a CUTOFF row
    # would GRANT Dell a write window that does not exist today. Improving a
    # denial's wording must not quietly widen what may be written. All three are
    # Joe-personal trees anyway (00_Context and Automation are outside the shared
    # DNA tier), so there is no partner whose jobs the earlier date could break.
    # Net effect of this row: the verdict is unchanged for both partners, and
    # only the MESSAGE improves — from "markdown is closed" to the specific
    # alarm naming the job.
    "00_Context/today.md": CLOSED_EARLY,
    "Automation/Learning/": CLOSED_EARLY,
    "Automation/radar/": CLOSED_EARLY,
}

# WHICH JOB WRITES A RETIRED PATH, so the denial can name it. The retired
# message said "its writer was supposed to be re-pointed" without ever saying
# WHICH writer — half of the diagnosis the ruling asks for. Longest prefix wins,
# so a specific file beats the directory it sits in.
WRITER_BY_PREFIX = {
    "00_Context/today.md": "bin/local-briefs.sh (launchd com.carr.local-briefs, 06:45 weekdays)",
    # NAMED BY WRITE SITE, not by the task that invokes them. The first version
    # said "the weekly metrics jobs — social-metrics-pull-weekly and
    # pull_placement_metrics.py", which names the ORCHESTRATING task prompt and
    # misses pipelines/learning_jobs.py entirely — the file that writes TWO of
    # the three outputs here, at its write() method. A job author sent to the
    # task prompt would find a runbook, not the code that emits the file.
    "Automation/Learning/": "pipelines/learning_jobs.py (weekly-learning, correction-miner) "
                            "and pipelines/pull_placement_metrics.py (placement-pull) — "
                            "both invoked by ops/scheduled-tasks/social-metrics-pull-weekly",
    # NOT health-check.py, and the correction is worth the line. The first
    # version of this map named it, on the strength of a grep hit — but
    # health-check only WATCHES this path: it appears in that file's WATCH
    # table, a staleness list of (name, output glob, max age, inputs, note).
    # A grep match is not a write site, and naming the wrong job in a message
    # whose entire purpose is naming the right one would have sent its reader to
    # a file that never touches it. The writer is named in health-check's own
    # note field for that row, which is where this value comes from.
    "Automation/radar/": "the Monday radar run (radar-digest-sop.md)",
    "DNA/Network/briefs/": "the weekly network brief run",
    "DNA/Clients/prospects/": "client-intake's dossier narrative",
    "Marketing/Social Media/": "the weekly social batch run",
    "DNA/Marketing/Social Media/": "the weekly social batch run",
}


def writer_for(rel):
    """The job that writes `rel`, or None. Longest declared prefix wins."""
    best = None
    for prefix, name in WRITER_BY_PREFIX.items():
        if rel.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, name)
    return best[1] if best else None

# Job-output FILENAME patterns that live in folders shared with migrated
# doctrine (a prefix row would un-block the migrated files too). The weekly
# social batch writes flat into Marketing/Social Media/ — caught during the
# day-6 DM triage before Monday's batch job hit the gate. Same retirement
# clock as the prefixes.
import re as _re
ALLOW_PATTERNS = {
    _re.compile(r"^(DNA/)?Marketing/Social Media/(week-20\d\d[^/]*/)?"
                r"(x-)?batch-20\d\d-\d\d-\d\d[^/]*\.md$"): CUTOFF,
}


def md_write_verdict(rel, today=None, actor=None):
    """None = allowed; else the deny reason for a vault-relative .md path.

    Caller has already established: path is inside the vault, ends .md, and is
    NOT a generated render (renders carry their own, earlier deny with a more
    specific message). `today` and `actor` are injectable for tests.
    """
    today = today or date.today()
    if actor is None:
        actor = local_actor()
    # A migrated partner's temporary rows are already dead; everyone else's run
    # to CUTOFF. See MIGRATED_PARTNERS above for why this is per machine.
    def _effective(retires):
        return CLOSED_EARLY if actor in MIGRATED_PARTNERS else retires
    if rel in ALLOW_EXACT:
        return None
    for prefix in ALLOW_MACHINE_PREFIX:
        if rel.startswith(prefix):
            return None
    writer = writer_for(rel)
    who = f"THE JOB TO FIX IS {writer}. " if writer else ""
    retired_msg = (f"'{rel}' sat on a TEMPORARY manifest row, and that row is now "
                   f"CLOSED (Joe, 2026-08-14: \"we need to eliminate the ability to "
                   f"write markdown at all. me or dell\" — brought forward from the "
                   f"2026-08-21 cutoff). Its writer was supposed to be re-pointed at "
                   f"the store, and this write is the alarm that it was not. "
                   f"{who}Route the "
                   f"content through a verb and fix the job: the posts, findings, "
                   f"decisions and open items in a report each have one. A markdown "
                   f"file reaches nobody and no query finds it.")
    for prefix, retires in ALLOW_PREFIX.items():
        if rel.startswith(prefix):
            return None if today <= _effective(retires) else retired_msg
    for pat, retires in ALLOW_PATTERNS.items():
        if pat.match(rel):
            return None if today <= _effective(retires) else retired_msg
    return (f"'{rel}': hand-authored markdown in the vault is closed (Joe, 2026-08-07: "
            f"\"i don't want anything written into .md files... database first\" — rule "
            f"14181e60, council decision 82a2fb62). The doctrine store is being built "
            f"(loop #242); until its write verbs land, route content through the verbs "
            f"that exist: log-decision (a settled call), add-loop (an open item or idea), "
            f"record-finding (research), log-capture (a studied source). Propose standing guidance as an edit to "
            f"CLAUDE.md, AGENTS.md or a skill in a PR; proposals bind nobody until merged.")
