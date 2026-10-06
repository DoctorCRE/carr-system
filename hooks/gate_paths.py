#!/usr/bin/env python3
"""gate_paths.py — ONE definition of "this file IS enforcement", for both doors.

WHY THIS MODULE EXISTS, and it is a hole that was open for three days.

Loop #231 opened 2026-08-07 when a subagent widened the security guard's own
KNOWN_HOSTS to unblock itself, and only the harness caught it. gate-edit-gate.py
closed the Write/Edit door on 2026-08-09 and its docstring told every reader:

    "Do NOT route around this by writing the file with a shell command;
     guard-unattended.py covers that path"

THAT SENTENCE WAS FALSE. Verified 2026-08-10 by firing the real hook rather than
by reading it — four shapes that rewrite a gate or wipe the live wiring all
returned exit 0, ALLOW:

    echo "KNOWN_HOSTS.add(...)" >> hooks/guard-unattended.py     -> ALLOW
    sed -i "" s/x/y/ hooks/record-home-gate.py                    -> ALLOW
    echo {} > ~/.claude/settings.json                             -> ALLOW
    cat x | tee hooks/gate-edit-gate.py                           -> ALLOW

The control in the same run — `echo hi >> open-loops.md` — was correctly DENIED,
so the Bash door was wired and working; it simply had no idea the gates existed.
guard-unattended.py's shell door guards generated renders, corpus renders and
vault .md, and nothing else. The false claim is the interesting part: it is why
nobody built this. A docstring asserted the coverage, so the gap read as closed.

THE RULE THIS OBEYS. a8c55a47 — a manual path and an automated path that do the
same job must be the same code. Two doors onto the same protected class must not
each carry their own copy of the list, which is how md_manifest.py and
corpus_renders.py are already shared between record-home-gate.py and
guard-unattended.py. This module is that same pattern for the gate files.

POSTURE: ANNOUNCE, NOT DENY — and that is Joe's ruling, not a softening chosen
here. Decision bd30b665, 2026-08-10: "dude, i think the gate think is a shitty
rule. its causing some serious blockage" / "yea downgrade it". The Write/Edit
door announces and allows; this door does the same, so a session gets the same
answer whichever way it reaches the file. A deny here would ALSO be worse than
the one it mirrors, because the Bash door has no approval channel at all — it
would be exactly the wall he rejected, with no way to open it.

WHAT ANNOUNCE ACTUALLY BUYS, stated plainly so nobody over-reads it: it does not
PREVENT a rogue gate edit. It makes one impossible to perform QUIETLY, which was
always the real property — the 2026-08-07 incident was caught by an outside
reviewer precisely because nothing in CARR said a word. Prevention lives in
hooks/gate-integrity.py, which hashes every gate against
ops/config/gate-baseline.json at each SessionStart and reports drift.

FAILS OPEN on any internal error, like every other gate here.
"""

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_RX = re.escape(REPO.rstrip("/"))

# The files whose CONTENTS are the enforcement. Kept identical in meaning to
# gate-edit-gate.py's original private list, which now imports from here.
PROTECTED_PATTERNS = [
    re.compile(rf"^{REPO_RX}/hooks/[^/]+\.py$"),
    re.compile(rf"^{REPO_RX}/ops/config/hooks\.json$"),
    re.compile(rf"^{REPO_RX}/ops/config/gate-baseline\.json$"),
    re.compile(rf"^{REPO_RX}/ops/harden-gates\.sh$"),
    re.compile(r"/carr-system/hooks/[^/]+\.py$"),       # the gate logic itself
    re.compile(r"/carr-system/ops/config/hooks\.json$"),        # the wiring
    re.compile(r"/carr-system/ops/config/gate-baseline\.json$"),  # the baseline
    re.compile(r"/carr-system/ops/harden-gates\.sh$"),           # the lock
    re.compile(r"/\.claude/settings\.json$"),           # live wiring, all copies
]

# Fixtures prove a gate change; they are not the gate. Gating them would make
# testing a gate harder than weakening one — precisely backwards.
EXEMPT = re.compile(r"-selftest\.py$")


def vault_roots():
    """Every CARR vault root that exists on THIS machine, trailing slash.

    Added 2026-08-10 in the Dell migration audit. Two gates pinned the vault to
    "/Users/booko/…/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI",
    which is one person's Drive account. On any second machine that path cannot
    exist, so the vault write-protections installed, passed their selftests, and
    matched nothing — a gate that is green and guards zero paths, which is worse
    than an absent one because it reports safety. The Drive mount is named for
    the ACCOUNT, so the only portable answer is to glob for it, the same way
    ops/config-as-code.py resolves {{VAULT}}.

    Callers ADD these to their existing hardcoded spellings rather than
    replacing them, so the primary machine's behaviour is unchanged.
    """
    import glob as _glob
    home = os.path.expanduser("~")
    found = list(_glob.glob(os.path.join(
        home, "Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI")))
    found.append(os.path.join(home, "My Drive/CARR AI"))
    env = os.environ.get("CARR_VAULT")
    if env:
        found.append(env)
    roots = [p.rstrip("/") + "/" for p in sorted(found) if os.path.isdir(p)]
    return tuple(dict.fromkeys(roots))          # de-duped, order preserved


def is_enforcement(path):
    """True when `path` is one of the files that ARE the enforcement."""
    if not path:
        return False
    try:
        p = os.path.abspath(os.path.expanduser(path))
    except Exception:
        return False
    if EXEMPT.search(p):
        return False
    return any(rx.search(p) for rx in PROTECTED_PATTERNS)


# ── the shell door ───────────────────────────────────────────────────────────
# Path shapes as they appear INSIDE a command string. Deliberately anchored on
# the same five classes as PROTECTED_PATTERNS rather than a loose "hooks" match,
# so an ordinary `grep hooks/` or `cat hooks/x.py` never trips it.
_REPO_PREFIX_RE = rf"(?:{REPO_RX}|(?:[~\w./-]*)?/carr-system)"
_PATH_RE = re.compile(
    rf"(?:{_REPO_PREFIX_RE}/(?:hooks/[\w.-]+\.py"
    r"|ops/config/(?:hooks|gate-baseline)\.json"
    r"|ops/harden-gates\.sh)"
    r"|(?:[~\w./-]*)?/\.claude/settings\.json)"
)

# Same proven shapes as guard-unattended.py's render door: redirect, tee,
# in-place sed, truncate. macOS sed carries a backup-suffix argument, so the
# sed clause accepts anything up to a clause separator.
_WRITE_BEFORE_CTX = re.compile(
    r"(>>?|\btee(\s+-a)?|\bsed\s+-i[^|;&]*|\btruncate\b[^|;&]*)\s*[\"']?$")

# Turning a gate OFF needs no write at all. guard-unattended.py's own docstring
# advertises it: "DISABLE FAST: ... `chmod -x` this file (a non-executable hook
# errors, and this gate fails open)". A silent chmod or rm is the quietest
# possible way to remove enforcement, so both are announced alongside writes.
_DISABLE_CMDS = ("chmod", "rm", "unlink", "chflags")
_COPY_CMDS = ("cp", "mv", "rsync", "install", "ln")

# ── the interpreter door (loop #484, opened 2026-08-21) ──────────────────────
# WHAT WAS OPEN, and it was found by doing rather than by a check: a session
# rewrote hooks/guard-unattended.py with
#
#     python3 - <<'PY'
#     path = "<gate>"
#     open(path, "w").write(...)
#     PY
#
# and the write landed with no denial, no prompt and no announcement. Clause 2
# below required the gate path to sit INSIDE the open() call as a literal, so a
# path held in a variable — the natural way to write a heredoc — walked past it,
# and so did node, ruby and perl, which clause 2 never knew about.
#
# WHY THIS IS NOT ANOTHER SHAPE IN THE LIST. Chasing shapes is how this hole got
# here: the next session finds `ruby -e`, a Node one-liner, or an interpreter
# nobody thought of. The pairing that matters is TARGET plus INTENT, wherever
# each appears in the command text — the path does not have to be inside the
# write call, or even a literal in the same statement. So this clause asks three
# ordered questions and nothing about the shape of the write:
#
#   1. is this an interpreter running INLINE code (a -c/-e flag, an in-place
#      flag, or a heredoc)? Running a gate as a SCRIPT is not — that is how
#      `python3 hooks/gate-integrity.py --bless x` stays silent, and it must,
#      because re-blessing is the repair this system asks sessions to perform.
#   2. does the command text name a non-exempt enforcement path anywhere?
#   3. does it carry a write intent anywhere — a write/truncate/unlink/rename/
#      chmod verb, an in-place flag, or a file mode literal?
#
# THE RESIDUAL, NAMED RATHER THAN IMPLIED: a read-only one-liner that happens to
# use one of these words still announces, and code that assembles the path from
# pieces at runtime still does not. The first is cheap — announce-and-allow
# costs one message — and the second is the same residual guard-unattended.py
# already names, backstopped by gate-integrity.py, which does not care how the
# bytes changed.
_INLINE_CODE = re.compile(
    r"\b(?:python[\d.]*|node|nodejs|ruby|perl|php|deno|bun|osascript)\b"
    r"(?:\s+-\w+)*\s+-(?:c|e|E|pi?e?|ne?)\b"          # -c / -e / -pe / -pi
    r"|\b(?:python[\d.]*|node|nodejs|ruby|perl|php|deno|bun)\b[^|;&\n]*<<"
    r"|\b(?:perl|ruby)\b(?:\s+-\w+)*\s+-\w*i\w*\b",   # perl -pi / ruby -i
)

_WRITE_INTENT = re.compile(
    r"write|truncate|unlink|\bremove\b|\brename\b|\breplace\b|\bchmod\b"
    r"|symlink|shutil|copyfile|\bdump\b|\bputs\b"
    r"|,\s*[\"']{1}[wax][btx+]*[\"']{1}"                  # a WRITE file-mode literal
    r"|\s-\w*i\w*\s",                                   # an in-place flag
    re.IGNORECASE,
)


def _inline_interpreter_write(cmd, hits):
    """The gate path an inline interpreter invocation writes, else None."""
    if not _INLINE_CODE.search(cmd):
        return None
    if not _WRITE_INTENT.search(cmd):
        return None
    for _, path in hits:
        if EXEMPT.search(path):
            continue
        return path
    return None



def enforcement_write(cmd):
    """The matched enforcement path when `cmd` writes to / disables one, else None.

    BEST-EFFORT, and said plainly rather than implied: it catches the ordinary
    shapes — `>`/`>>`, tee, `sed -i`, truncate, cp/mv/rsync/ln destinations,
    chmod/rm/chflags targets, inline python open(...,'w'), and — since loop
    #484 — any interpreter running inline code that names a gate path and a
    write intent, however the two are spelled. It does not chase
    adversarial obfuscation, for the same reason the render door does not: a
    session determined to obfuscate has already decided to defeat the control,
    and gate-integrity.py is the backstop that does not care how the bytes
    changed.
    """
    if not cmd:
        return None
    try:
        hits = [(m.start(), m.group(0)) for m in _PATH_RE.finditer(cmd)]
        if not hits:
            return None
        for idx, path in hits:
            if EXEMPT.search(path):
                continue

            # 1. redirect / tee / sed -i / truncate immediately before the path
            before = cmd[max(0, idx - 60):idx]
            if _WRITE_BEFORE_CTX.search(before):
                return path

            # 2. inline python write
            if re.search(r"open\(\s*[\"'][^\"']*" + re.escape(os.path.basename(path))
                         + r"[\"']\s*,\s*[\"'][wa]", cmd):
                return path

        # 3. clause-leading commands: copy destinations and outright disables
        for clause in re.split(r"[;&|]", cmd):
            toks = clause.strip().split()
            if not toks:
                continue
            head = os.path.basename(toks[0])
            clause_hits = [m.group(0) for m in _PATH_RE.finditer(clause)
                           if not EXEMPT.search(m.group(0))]
            if not clause_hits:
                continue
            if head in _DISABLE_CMDS:
                return clause_hits[0]
            if head in _COPY_CMDS:
                # destination only — reading a gate INTO somewhere else is fine
                tail = clause.strip().rstrip("\"' ")
                for p in clause_hits:
                    if tail.endswith(p):
                        return p
        # 4. an interpreter running inline code that names a gate and a write
        return _inline_interpreter_write(cmd, hits)
    except Exception:
        return None                                    # fail open, like the rest


def announcement(path, how):
    """The message both doors print. One wording, so the two cannot drift.

    WR-000019 slice S3 (2026-08-27): gate-edit-gate.py and settings-change-
    gate.py stopped asking for an in-session sign-off on this file the same
    day canonical-edit-gate.py and git-writer-gate.py retired. The property
    that matters — an edit to hooks/, ops/config/, or settings lands only
    through review — is now guaranteed server-side: the PR-only ruleset on
    main plus required CI mean a canonical-tree edit cannot reach main any
    other way, and CODEOWNERS auto-requests Joe on any PR touching hooks/ or
    ops/config/. This message stopped being an approval request and became a
    nudge toward that route.
    """
    name = os.path.basename(path)
    return (
        f"GATE CHANGE ANNOUNCED — {name} is enforcement, and {how} just changed it.\n\n"
        f"  file: {path}\n\n"
        "The action was ALLOWED — this file no longer asks for an in-session "
        "sign-off. Land it the way every canonical-tree edit lands now: "
        "`./run.sh worktree <name>`, commit and push from there, and open a PR. "
        "CODEOWNERS auto-requests Joe on hooks/ and ops/config/, and the PR-only "
        "ruleset with required CI is what actually keeps main honest — say in "
        "the PR description what changed and whether it makes the gate STRONGER "
        "or WEAKER (weaker is allowed; hiding that it is weaker is not). "
        "Re-bless the baseline in the SAME commit "
        "(`python3 hooks/gate-integrity.py --bless <name>`), or the next "
        "SessionStart reports it UNBLESSED and the check goes chronically red "
        "for a benign reason, which is the failure mode that hid a real "
        "five-gate wipe on 2026-08-08."
    )
