#!/usr/bin/env python3
"""
call-verb.py — fire ONE record verb from a terminal on this Mac.

WHY THIS EXISTS (2026-08-08, monthly resurface session). Joe tried to close four
idea rows from the shell and there was no path: `run.sh` has no verb subcommand,
so a session can call a verb through the MCP connector but a human at a prompt
cannot. The capability already existed — mcp-server/local-verb.mjs — but it was
invisible from run.sh.

Usage:
  ./run.sh call <verb> '<json args>'
      Default path: local-verb.mjs runs the verb as an HTTPS call against the
      DEPLOYED WORKER (production, always — there is no other database behind
      it). This is what every ordinary call should use.

  ./run.sh call --reason "why" <verb> '<json args>'
      BREAK-GLASS (Phase 1, 2026-08-13, decision 97e76a2f): requires
      CARR_BREAK_GLASS=1 in the environment too (both together, same envelope
      as tools/db-tap.py — see its header). Derives a direct DATABASE_URL via
      db-tap and runs local-verb.mjs's direct-database code path instead of
      the HTTPS default. Prints the same banner db-tap.py prints and appends a
      receipt to out/break-glass-receipts.log BEFORE the verb ever runs — one
      shared audit trail for every break-glass act in this repo.

  ./run.sh call --branch rehearse-0031 --reason "why" add-loop '{"kind":"idea", ...}'
      Break-glass against a Neon branch instead of production — for proving a
      verb change before it ships. --branch is ONLY meaningful under
      break-glass: the deployed Worker has no notion of a branch, it always
      talks to production, so --branch without --reason (and CARR_BREAK_GLASS=1)
      is refused rather than silently ignored.

  ./run.sh call list-verbs '{}'

THE DEFECT THIS CLOSES. Until this change, EVERY call through this path opened
a direct connection to the production database and ran the verb's handler
locally — bypassing the deployed Worker's identity derivation, its profile
gate, and its read-call instrumentation (tool_read_count) entirely. The DEFAULT
path is now the deployed Worker, exactly like every other MCP caller; the
direct-database path survives only as explicit, receipted break-glass. See
mcp-server/local-verb.mjs's own header for the full design, and
mcp-server/src/index.js's "local token" comment for the server side.

IDENTITY. There is no actor-slug argument. On the default path, identity is
server-derived from a LOCAL_TOKENS bearer (see mcp-server/src/identity.js);
this script never sees or handles that token, it just runs local-verb.mjs with
no DATABASE_URL set. On the break-glass path, identity comes from
~/.config/carr/local-actor.json (bin/set-local-actor.sh) — see
mcp-server/local-verb.mjs for the resolution and its stated limits.

The unattended room bridge's queue projector is the one bounded internal
exception to the LOCAL_TOKENS credential choice: its subprocess sets
CARR_MCP_CLIENT_PROFILE=hermes-projector, causing local-verb.mjs to read
CARR_HERMES_MCP_TOKEN from the same 600-mode file. The Worker still derives the
actor from HERMES_TOKENS; no slug or token is put in argv. Ordinary bridge calls
clear the selector in verb_io.py and remain joe-local.

Never prints the DSN or any token value.
"""
import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_VERB = os.path.join(REPO, "mcp-server", "local-verb.mjs")
NODE_CANDIDATES = [
    "/opt/homebrew/bin/node",
    "/usr/local/opt/node@22/bin/node",
    # A user-local prefix, listed BEFORE the bare "node" because a launchd agent
    # does not inherit a login shell's PATH — the calendar plist, for one, sets
    # PATH to /opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin and nothing else, so
    # a node reachable only through PATH is invisible to every unattended run.
    # Dell's Mac has no Homebrew and no sudo to make one, so this is where node
    # lives there (installed 2026-08-18, v26.5.1 to match .nvmrc, checksum-verified
    # against nodejs.org's SHASUMS256.txt). The version is taken from .nvmrc rather
    # than from whatever nodejs.org calls current LTS: the first install here was
    # 24.19.0 chosen that way, which is a major version below what CI runs.
    # Absent on a machine that has Homebrew, in which case the first candidate won.
    os.path.expanduser("~/.local/bin/node"),
    "node",
]


def _db_tap():
    # db-tap.py's hyphen makes it un-importable by name; load it by path so the
    # DSN logic AND the break-glass receipt/actor helpers have exactly one home
    # (rule a8c55a47 — the manual path and the automated path that do the same
    # job must BE the same code). local_actor_slug()/append_receipt() were
    # promoted from private helpers to a public API for exactly this reuse
    # (Phase 1, 2026-08-13).
    path = os.path.join(REPO, "tools", "db-tap.py")
    spec = importlib.util.spec_from_file_location("db_tap", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def node_bin() -> str:
    for p in NODE_CANDIDATES:
        if os.path.sep not in p or os.path.exists(p):
            return p
    return "node"


def break_glass_dsn(db_tap, branch):
    """Choose the narrow Joe authority credential for an authority-only verb.

    A named rehearsal branch has no corresponding authority credential, so it
    retains the existing owner-derived branch path.  On production, though,
    the documented authority credential must win when it is available: deriving
    neondb_owner first makes an authority-only verb unreachable through this
    otherwise receipted wrapper and widens its database privilege needlessly.
    The value is returned only to subprocess environment construction and is
    never printed.
    """
    authority_url = os.environ.get("CARR_DB_AUTHORITY_JOE_URL")
    if branch is None and authority_url:
        return authority_url
    return db_tap.dsn(branch or "production")


def main() -> None:
    argv = sys.argv[1:]
    branch = None
    reason = ""
    while argv and argv[0] in ("--branch", "--reason"):
        flag = argv[0]
        if len(argv) < 2:
            sys.exit(f"{flag} needs a value")
        if flag == "--branch":
            branch = argv[1]
        else:
            reason = argv[1]
        argv = argv[2:]
    if not argv:
        sys.exit(__doc__)
    verb, rest = argv[0], argv[1:]
    args_json = rest[0] if rest else "{}"
    # Anything past args_json used to be an actor-slug argv (default "joe").
    # That default is gone. Pass any leftover positional args straight through
    # so local-verb.mjs's own guard rejects them loudly and names the exact
    # setup command, rather than this wrapper silently swallowing a stale
    # slug argument.
    extra = rest[1:]

    if not os.path.exists(LOCAL_VERB):
        sys.exit(f"no such file: {LOCAL_VERB}")

    engaged = os.environ.get("CARR_BREAK_GLASS") == "1" and bool(reason and reason.strip())

    if not engaged:
        if branch is not None:
            sys.exit(
                "--branch requires break-glass: set CARR_BREAK_GLASS=1 and pass --reason \"...\".\n"
                "  The default path (no --branch, no break-glass) talks to the deployed Worker, "
                "which always runs against production — there is no branch behind it. See "
                "mcp-server/local-verb.mjs and tools/db-tap.py for the same envelope."
            )
        if os.environ.get("CARR_BREAK_GLASS") == "1" and not (reason and reason.strip()):
            sys.exit('CARR_BREAK_GLASS=1 is set but --reason "..." is missing — both are required together.')
        # ---- default path: HTTPS to the deployed Worker, no DSN derived at all ----
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)  # never let a caller's stray export force break-glass mode
        os.chdir(os.path.join(REPO, "mcp-server"))
        rc = subprocess.run([node_bin(), LOCAL_VERB, verb, args_json, *extra], env=env).returncode
        sys.exit(rc)

    # ---- break-glass path: direct database connection, receipted ----
    url = break_glass_dsn(_db_tap(), branch)
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "CARR_BREAK_GLASS": "1",
        "CARR_BREAK_GLASS_REASON": reason,
    }
    os.chdir(os.path.join(REPO, "mcp-server"))
    rc = subprocess.run(
        [node_bin(), LOCAL_VERB, verb, args_json, *extra], env=env
    ).returncode
    sys.exit(rc)


if __name__ == "__main__":
    main()
