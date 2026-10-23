#!/usr/bin/env python3
"""
db-tap.py — run a production tap (a .sql file or a pipelines/ script) without
shell command substitution.

WHY THIS EXISTS (2026-07-31, Fable seat session 3): Joe opened the psql gate
(three allow rules in the vault project's .claude/settings.json), but the
harness classifier still refuses any Bash command containing $(...) because it
cannot vouch for the nested command. backup-dump.sh already solved this shape:
derive the owner DSN from neonctl INSIDE the process. This tool is that pattern
for taps: it obtains the connection string itself, then either runs psql -f on
a SQL file or execs a pipelines/ script with DATABASE_URL set.

Usage:
  .venv/bin/python tools/db-tap.py sql pipelines/r2-quota-seed.sql
  .venv/bin/python tools/db-tap.py run pipelines/backfill_document_attachments.py [--apply ...]
  .venv/bin/python tools/db-tap.py --branch rehearse-0026 run tools/migrate.py --apply --yes

--project staging (before the mode) targets the ISOLATED STAGING PROJECT, a
separate Neon project that shares no data or credentials with production. That
separation is the point: a Neon BRANCH lives inside production's project and
starts as a copy of its data, so it is not isolation.

--branch <name> (before the mode) targets a Neon branch instead of production —
the rehearse-on-branch pattern through the same no-substitution path (added
2026-07-31, Fable seat session 4, for the 0026 rehearsal).

READ-ONLY BY DEFAULT (Phase 1, 2026-08-13): every ordinary invocation, sql or
run, opens its subprocess with default_transaction_read_only=on. A write
against production fails at the server unless you deliberately break glass:

  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "why" sql pipelines/x.sql

Both CARR_BREAK_GLASS=1 and a non-empty --reason are required together. A
break-glass run is banner-announced, requires a local actor identity
(~/.config/carr/local-actor.json, see bin/set-local-actor.sh), and appends a
receipt to out/break-glass-receipts.log before the target ever runs.

Never prints the DSN. ON_ERROR_STOP is always set for sql mode.
"""
import json
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import tomllib
import urllib.parse
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
NEONCTL = os.path.join(REPO, "mcp-server", "node_modules", ".bin", "neonctl")
# A CARR Mac IS NOT GUARANTEED TO HAVE HOMEBREW. bin/notes-sweep-post.sh already
# states the reason in full: Dell's Mac has no /opt/homebrew at all, because
# installing Homebrew needs sudo his session cannot invoke, so his toolchain
# lives under the user-local prefix instead. A candidate list that knew only the
# two Homebrew prefixes therefore resolved to the bare name on that machine and
# every psql path died as FileNotFoundError deep inside subprocess — see
# psql_bin() below, which now refuses in words instead.
from lib.local_principal import LocalPrincipalError, local_actor_slug as _established_actor_slug
from lib.machine_prerequisites import PSQL_CANDIDATES, find_executable
RECEIPT_LOG = os.path.join(REPO, "out", "break-glass-receipts.log")


def _cloudflare_account_id() -> str:
    try:
        with open(os.path.join(REPO, "mcp-server", "wrangler.toml"), "rb") as handle:
            value = tomllib.load(handle).get("account_id")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.exit(f"db-tap: could not load the pinned Wrangler account: {type(exc).__name__}")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        sys.exit("db-tap: wrangler.toml account_id must be lowercase 32-hex")
    return value


CLOUDFLARE_ACCOUNT_ID = _cloudflare_account_id()


def _load_credential_module():
    path = pathlib.Path(REPO) / "tools/staging_database_credential.py"
    spec = importlib.util.spec_from_file_location("db_tap_staging_credential", path)
    if spec is None or spec.loader is None:
        sys.exit("db-tap: staging credential helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


staging_credential = _load_credential_module()


def _neon_api_key() -> str:
    """NEON_API_KEY from the environment, or from db.env beside the other
    credentials. Empty string when there is none.

    WHY THIS EXISTS, 2026-08-10. neonctl's saved browser login expires on its own
    schedule, and when it does it does not fail cleanly — it PROMPTS, waits 60
    seconds for a browser nobody is sitting at, and times out. Because this
    function is the one place four different jobs derive a credential, that one
    expiry silently took down all four: the GitHub production deployment workflow, record imports,
    bin/restore-rehearse.sh (the only proof the encrypted backups can be restored)
    and pipelines/partner_ping.py, the Joe/Dell interrupt channel.

    The ping is the one that shows how bad the failure mode is. It kept running
    every 120 seconds and kept logging "nothing new since 2026-08-03T20:02:16",
    382 consecutive identical lines over six days, because a channel whose query
    is broken and a channel with genuinely nothing to say produce byte-identical
    output. Its watermark had not moved since the day the login lapsed. Nothing
    alarmed, because nothing watches it.

    A Neon API key does not expire on a timer and needs no browser, and neonctl
    reads it from NEON_API_KEY. This keeps the property the pattern was built for
    — the DSN is still derived per invocation, still never on a command line and
    never in a transcript — and removes only the human from the refresh."""
    key = (os.environ.get("NEON_API_KEY") or "").strip()
    if key:
        return key
    env_file = os.path.join(os.path.expanduser("~"), ".config", "carr", "db.env")
    try:
        with open(env_file, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("NEON_API_KEY="):
                    # db.env values are shell-quoted so `set -a; . db.env` survives
                    # an & in a DSN; strip the quotes the same way db_url() does.
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


# The two Neon PROJECTS, which is the isolation boundary that matters. A Neon
# BRANCH lives inside its parent project and starts as a copy of its data, so a
# branch of production is NOT isolated from production — it IS production's data
# under another name. Gate G1 requires an environment that cannot reach
# production's data or credentials, and only a separate project delivers that.
# Created 2026-08-13 alongside this change.
# Each project carries its own DEFAULT BRANCH NAME, because they differ and
# assuming otherwise fails at the neonctl call: the production project's primary
# branch is named "production", while a freshly created Neon project's is "main".
# Production is PINNED BY ID and staging is RESOLVED BY NAME, and the asymmetry
# is deliberate. Production's id must never drift to whatever a lookup happens to
# return — a name collision or a typo that silently repointed it at another
# project is the worst failure this file could have. Staging, by contrast, is
# rebuilt whenever it needs to be (it holds nothing that cannot be regenerated),
# and pinning its id meant editing this file on every rebuild — a step someone
# forgets, after which the tool writes to a project that no longer exists or,
# worse, to whatever inherited the id.
PROJECTS = {
    "production": {"id": "steep-field-48688294", "default_branch": "production"},
    "staging":    {"name": "carr-staging", "default_branch": "main"},
}
NEON_ORG = "org-dry-dew-75906281"


def _project_id_by_name(name: str, env: dict) -> str:
    """Resolve a Neon project id from its name. Never prints a connection string;
    the projects list carries ids and names only."""
    payload = _json_command(
        [NEONCTL, "projects", "list", "--org-id", NEON_ORG, "--output", "json"],
        env, "staging project lookup",
    )
    rows = payload if isinstance(payload, list) else payload.get("projects", [])
    matches = [r["id"] for r in rows if r.get("name") == name]
    if not matches:
        sys.exit(f"db-tap: no Neon project named '{name}'. Create it, then retry.")
    if len(matches) > 1:
        sys.exit(f"db-tap: {len(matches)} Neon projects named '{name}' — refusing to guess which.")
    return matches[0]


def _json_command(args: list[str], env: dict, label: str):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired):
        sys.exit(f"db-tap: {label} did not complete; provider output suppressed")
    if out.returncode != 0:
        sys.exit(f"db-tap: {label} failed (rc={out.returncode}); provider output suppressed")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.exit(f"db-tap: {label} returned invalid JSON; provider output suppressed")


def _staging_runtime_target(env: dict) -> tuple[str, str, str, str]:
    """Resolve exact staging project/main/read-write endpoint without a DSN reveal."""
    project_id = _project_id_by_name(PROJECTS["staging"]["name"], env)
    if project_id == PROJECTS["production"]["id"]:
        sys.exit("db-tap: staging resolved to the canonical Production project id")
    branch_payload = _json_command(
        [NEONCTL, "branches", "list", "--project-id", project_id, "--output", "json"],
        env, "staging branch lookup",
    )
    branches = branch_payload if isinstance(branch_payload, list) else branch_payload.get("branches", [])
    matches = [row for row in branches if row.get("name") == "main" and row.get("default") is True]
    if len(matches) != 1 or str(matches[0].get("project_id") or project_id) != project_id:
        sys.exit("db-tap: staging must have exactly one default main branch")
    branch_id = str(matches[0].get("id") or "")
    if not branch_id:
        sys.exit("db-tap: staging main branch has no immutable id")
    endpoint_payload = _json_command(
        [NEONCTL, "api", f"/projects/{project_id}/branches/{branch_id}/endpoints", "--output", "json"],
        env, "staging endpoint lookup",
    )
    endpoints = endpoint_payload if isinstance(endpoint_payload, list) else endpoint_payload.get("endpoints", [])
    endpoints = [row for row in endpoints if isinstance(row, dict)
                 and str(row.get("branch_id") or branch_id) == branch_id
                 and row.get("type") in {"read_write", "read-write", "rw"}]
    endpoint_id = str(endpoints[0].get("id") or "") if len(endpoints) == 1 else ""
    endpoint_host = str(endpoints[0].get("host") or "").lower().rstrip(".") \
        if len(endpoints) == 1 else ""
    if (
        len(endpoints) != 1 or not endpoint_id.startswith("ep-")
        or not endpoint_host.startswith(endpoint_id + ".")
        or not endpoint_host.endswith(".neon.tech")
    ):
        sys.exit("db-tap: staging main must have exactly one read-write endpoint")
    return project_id, branch_id, endpoint_id, endpoint_host


def _staging_file_dsn(role_name: str, branch: str | None, env: dict) -> str:
    if branch not in (None, "main"):
        sys.exit("db-tap: staging runtime credentials are pinned to default main")
    _project_id, _branch_id, _endpoint_id, endpoint_host = _staging_runtime_target(env)
    profile_label = {"app_writer": "writer", "app_reader": "reader"}.get(role_name)
    if profile_label is None:
        sys.exit("db-tap: staging file credential role must be app_writer or app_reader")
    profile = staging_credential.profile(profile_label)
    stored = staging_credential.load_existing(
        profile.paths, key=profile.key, role_name=profile.role_name,
        expected_endpoint=endpoint_host, expected_port=5432, expected_database="neondb",
    )
    if stored.state != "final":
        sys.exit(f"db-tap: staging {role_name} credential is not verified/final")
    return stored.value


# ONE DERIVATION PER PROCESS, NOT ONE PER CALL. Every neonctl spawn costs about
# 1.5-3 seconds on a healthy machine, and a single `run.sh health` asks for the
# production string several times, so the repeated spawns were a real share of
# "everything takes forever" even with nothing broken. This cache is IN-PROCESS
# ONLY and is deliberately never written to disk: the property this file exists
# to hold is that the credential is derived, used, and never echoed or stored,
# and an in-memory dict keeps that exactly. The former outage drill cached
# the staging string the same way for the same reason.
_DSN_CACHE: dict[tuple, str] = {}


def _no_dsn_message(detail: str, key: str) -> str:
    """The ONE explanation of a failed derivation, shared by both routes out.

    NAME THE ACTUAL CAUSE. Printing neonctl's stderr alone is true and useless:
    on an expired login it is a browser URL and an "authentication timed out"
    line, which points the reader at another browser trip when the fix is a
    stored key. Rule a8c55a47 — a manual path and an automated path that do the
    same job must be the same code — binds across the timeout route and the
    nonzero-exit route, so they answer in the same words.
    """
    if not key:
        return (
            "neonctl could not derive a connection string, and NEON_API_KEY is NOT SET.\n"
            "  This is almost certainly the expired-browser-login failure: neonctl\n"
            "  prompts for a browser, waits 60 seconds, and gives up.\n"
            "  Fix it once: create a Neon API key in the console and add it to\n"
            "  ~/.config/carr/db.env as NEON_API_KEY=... (chmod 600, already gitignored).\n"
            f"  neonctl said: {detail}")
    return f"neonctl failed with NEON_API_KEY set: {detail}"


def dsn(branch=None, project: str = "production", role_name: str = "neondb_owner") -> str:
    """Connection string for a branch of a named PROJECT.

    The value is returned to the caller and never printed. neonctl prints the
    URI with its password embedded when run bare, which is how a live credential
    ended up in a session transcript on 2026-08-13 while creating the staging
    project — that project was destroyed and rebuilt with output suppressed
    rather than left with a credential that had been written down. Every path
    through this file keeps that property: derive, use, never echo.
    """
    spec = PROJECTS.get(project)
    if spec is None:
        sys.exit(f"db-tap: unknown project '{project}' (known: {', '.join(PROJECTS)})")
    if role_name not in ("neondb_owner", "app_writer", "app_reader"):
        sys.exit("db-tap: role_name must be neondb_owner, app_writer, or app_reader")
    key = _neon_api_key()
    env = {**os.environ,
           "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    if key:
        env["NEON_API_KEY"] = key
    if project == "staging" and role_name in {"app_writer", "app_reader"}:
        # DELIBERATELY NOT CACHED. This path re-reads the stored staging
        # credential and REFUSES it unless its recorded state is still
        # verified/final, so the check is the point of the call rather than
        # overhead on the way to a value. Caching it made a credential that had
        # since been demoted to pending keep answering with the value from
        # before the demotion — the credential isolation regressions caught
        # exactly that. It is also a file read with no neonctl spawn behind it,
        # so there is nothing here worth the risk of a stale answer.
        return _staging_file_dsn(role_name, branch, env)
    project_id = spec.get("id") or _project_id_by_name(spec["name"], env)
    if branch is None:
        branch = spec["default_branch"]
    # Keyed on the RESOLVED branch, so dsn() and dsn(branch=<the default>) are one
    # entry rather than two spellings of the same connection.
    cache_key = (project, branch, role_name)
    if cache_key in _DSN_CACHE:
        return _DSN_CACHE[cache_key]
    try:
        out = subprocess.run(
            [NEONCTL, "connection-string", branch,
             "--project-id", project_id,
             "--role-name", role_name,
             # A project can hold more than one database.  All sanctioned CARR
             # owner paths target this one explicitly rather than inheriting the
             # provider's mutable default database selection.
             "--database-name", "neondb",
             "--endpoint-type", "read_write"],
            capture_output=True, text=True, timeout=60, env=env,
        )
    except subprocess.TimeoutExpired:
        # THE TIMEOUT IS THE EXPIRED-LOGIN SIGNATURE, AND IT USED TO ESCAPE THIS
        # FUNCTION UNCAUGHT. `timeout=60` raises rather than returning a nonzero
        # result, so the carefully-worded diagnosis below — the one that names a
        # stored API key as the fix — was unreachable on the single failure it
        # was written for. What the reader got instead was a raw TimeoutExpired
        # traceback, which is why on 2026-08-21 this presented as "the whole
        # system is slow" for two minutes per command rather than as one expired
        # credential. Same finding, same words, on both routes out.
        raise SystemExit(_no_dsn_message("timed out after 60s with no answer", key))
    if out.returncode != 0 or not out.stdout.strip():
        detail = out.stderr.strip()[:200] or f"exit status {out.returncode}"
        raise SystemExit(_no_dsn_message(detail, key))
    return _DSN_CACHE.setdefault(cache_key, out.stdout.strip())


def psql_bin() -> str:
    """The postgres client, or a refusal that NAMES THE MISSING DEPENDENCY.

    The old body returned the bare name "psql" for two different reasons — the
    loop's `os.path.sep not in p` test matched it unconditionally, and so did the
    final fallback — so a machine with no postgres client at all got a plausible
    string back and failed later as `FileNotFoundError: 'psql'` from inside
    subprocess, with a traceback that named neither the dependency nor the fix.
    On 2026-08-21 that read as four UNREADABLE sections in `run.sh health` and
    looked like a database outage; it was a missing client binary.

    A bare name is only a real answer if it actually resolves on PATH, so it is
    resolved here rather than assumed.
    """
    found = find_executable(PSQL_CANDIDATES)
    if found:
        return found
    sys.exit(
        "db-tap: NO POSTGRES CLIENT ON THIS MACHINE — `psql` is not installed.\n"
        "  This is a missing dependency, NOT a database or credential failure;\n"
        "  the connection string may well be fine. Looked in:\n"
        + "".join(f"    {c}\n" for c in PSQL_CANDIDATES) +
        "  On a Homebrew machine: brew install libpq\n"
        "  On a machine without Homebrew (Dell's has none — installing it needs\n"
        "  sudo his session cannot invoke, see bin/notes-sweep-post.sh), install\n"
        "  the client under ~/.local, which needs no sudo and is already on this\n"
        "  candidate list."
    )


def _engaged(reason: str) -> bool:
    # Break-glass is an ACCIDENT gate and an audit trail, not an attacker gate:
    # any process running as this Mac user can set CARR_BREAK_GLASS=1 and pass
    # any --reason string, exactly as this script itself can. The real security
    # boundary is server-side (a scoped role/credential), not this file — same
    # honest limit bin/set-local-actor.sh states for identity.
    return os.environ.get("CARR_BREAK_GLASS") == "1" and bool(reason and reason.strip())


def local_actor_slug() -> str:
    """Public (Phase 1, 2026-08-13): tools/call-verb.py's own break-glass path
    (mcp-server/local-verb.mjs's direct-database mode) reuses this rather than
    re-implementing the same ~/.config/carr/local-actor.json read a second
    time — rule a8c55a47, a manual path and an automated path that do the same
    job must be the same code. Was `_local_actor_slug`; renamed, no other
    caller referenced the old name."""
    try:
        return _established_actor_slug()
    except LocalPrincipalError:
        return "identity-not-set"


def append_receipt(actor: str, mode: str, target: str, host: str, reason: str,
                    log_path: str = RECEIPT_LOG) -> None:
    """Public (Phase 1, 2026-08-13): same reuse rationale as local_actor_slug
    above. tools/call-verb.py's break-glass path appends to this exact log,
    in this exact line shape, so out/break-glass-receipts.log stays ONE audit
    trail for every break-glass act in the repo rather than two files that
    could drift in format. Was `_append_receipt`; renamed, no other caller
    referenced the old name."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    line = (f"{datetime.now(timezone.utc).isoformat()} actor={actor} mode={mode} "
            f'target={target} host={host} reason="{reason.strip()}"\n')
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _run_sql_in_process(url: str, path: str) -> int:
    """Run a .sql file through psycopg instead of shelling out to psql.

    WHY THERE IS NO psql SUBPROCESS HERE ANY MORE, 2026-08-21. `sql` mode used to
    exec the psql client, which made a postgres CLIENT BINARY a hard dependency of
    the everyday health path. Dell's Mac has no Homebrew and no sudo to install
    one (bin/notes-sweep-post.sh states why), so there was no psql anywhere on the
    machine and four `run.sh health` sections reported the canonical store
    UNREADABLE — which reads as a database outage and was a missing binary. The
    only npm-published client is a 147MB beta, far too much to put in the lockfile
    that every session's record verbs load, and psycopg was already installed and
    working. So the dependency is removed rather than satisfied.

    Two properties improve on the way past. The DSN no longer appears in the
    process argument list, where any other user on the machine could read it —
    psql took the URI as argv[1], which this file's own docstring calls the thing
    it exists to prevent. And ON_ERROR_STOP stops being a flag that has to be
    remembered: one transaction wraps the whole script, so a failure halfway
    through rolls back instead of leaving a half-applied file.

    Output is unaligned pipe-separated rows, which is what tools/health-check.py
    already parses — it splits on "|", strips, and selects rows by column count.
    """
    try:
        import psycopg
    except ImportError:
        sys.exit("db-tap: psycopg is not installed in this interpreter — "
                 "run through .venv/bin/python, or pip install -r requirements.txt")
    try:
        with open(path, encoding="utf-8") as handle:
            script = handle.read()
    except OSError as exc:
        sys.exit(f"db-tap: cannot read {path}: {exc}")
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(script)
                while True:
                    if cur.description is not None:
                        for row in cur.fetchall():
                            print("|".join("" if v is None else str(v) for v in row))
                    if not cur.nextset():
                        break
    except psycopg.Error as exc:
        # str(exc) carries the server's message and position, never the DSN.
        print(f"db-tap: {exc}".strip(), file=sys.stderr)
        return 1
    return 0


def main() -> None:
    argv = sys.argv[1:]
    branch = None            # None = use the project's own default branch
    project = "production"
    reason = ""
    while argv and argv[0] in ("--branch", "--reason", "--project"):
        flag = argv[0]
        if len(argv) < 2:
            sys.exit(f"{flag} needs a value")
        if flag == "--branch":
            branch = argv[1]
        elif flag == "--project":
            project = argv[1]
            # A named project's default branch is its own "production" branch in
            # Neon's vocabulary; only override when the caller also asked for one.
        else:
            reason = argv[1]
        argv = argv[2:]
    if len(argv) < 2 or argv[0] not in ("sql", "run"):
        sys.exit(__doc__)
    mode, target, extra = argv[0], argv[1], argv[2:]
    target_abs = target if os.path.isabs(target) else os.path.join(REPO, target)
    if not os.path.exists(target_abs):
        sys.exit(f"no such file: {target_abs}")
    url = dsn(branch, project)
    os.chdir(REPO)

    # THE READ-ONLY DEFAULT IS A PRODUCTION PROTECTION, NOT A UNIVERSAL ONE.
    # Break-glass exists because an unintended write against the live record
    # layer is unrecoverable. Staging is a separate Neon project holding no
    # production data, and writing to it is the entire reason it exists —
    # requiring break-glass there would train everyone to type CARR_BREAK_GLASS=1
    # routinely, which is precisely how a protection stops protecting the thing
    # it was built for. So staging is writable and production is not.
    engaged = _engaged(reason)
    # THE READ-ONLY DEFAULT IS A PRODUCTION PROTECTION, NOT A UNIVERSAL ONE, and
    # writability is a SEPARATE question from break-glass. Break-glass exists
    # because an unintended write against the live record layer is unrecoverable;
    # it logs a receipt and announces itself. Staging is a separate Neon project
    # holding no production data, and writing to it is the entire reason it
    # exists. Requiring break-glass there would mean a receipt for every routine
    # staging load and would train everyone to type CARR_BREAK_GLASS=1 by habit —
    # exactly how a protection stops protecting the thing it was built for.
    # So: staging is writable WITHOUT being break-glass.
    writable = engaged or project != "production"
    env = {**os.environ}

    if engaged:
        actor = local_actor_slug()
        host = urllib.parse.urlsplit(url).hostname or "unknown-host"
        if actor == "identity-not-set":
            append_receipt(actor, mode, target, host, reason)
            sys.exit(
                "BREAK-GLASS REFUSED: no local actor identity found at\n"
                "  ~/.config/carr/local-actor.json (written once per machine by\n"
                "  bin/set-local-actor.sh). Run that script, then retry.\n"
                "  This refused attempt was still logged to\n"
                "  out/break-glass-receipts.log as identity-not-set."
            )
        print("=" * 72, file=sys.stderr)
        print("BREAK-GLASS ENGAGED — running WITHOUT the read-only guard.", file=sys.stderr)
        print(f"  actor:  {actor}", file=sys.stderr)
        print(f"  reason: {reason.strip()}", file=sys.stderr)
        print(f"  mode:   {mode} {target}", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        # Log the ATTEMPT before running the target below — the attempt is the
        # event this receipt records, not success. Do not roll this line back
        # if the run then fails.
        append_receipt(actor, mode, target, host, reason)
    elif not writable:
        # Default posture: PGOPTIONS is a standard libpq env var honored the
        # same way whether the subprocess is psql (sql mode) or a Python
        # script connecting via psycopg/psycopg2 off DATABASE_URL (run mode,
        # also libpq-based) — one mechanism, no per-mode special-casing.
        # Merge onto any PGOPTIONS a caller already set rather than overwrite
        # it (none currently do).
        existing = env.get("PGOPTIONS", "")
        guard = "-c default_transaction_read_only=on"
        env["PGOPTIONS"] = f"{existing} {guard}".strip()

    if mode == "sql":
        # PGOPTIONS is read by libpq from the PROCESS environment, and psycopg is
        # libpq, so the read-only guard has to land on os.environ here rather than
        # on the dict that was built for a subprocess that no longer exists.
        if "PGOPTIONS" in env:
            os.environ["PGOPTIONS"] = env["PGOPTIONS"]
        rc = _run_sql_in_process(url, target_abs)
    else:
        env["DATABASE_URL"] = url
        # Cloudflare ACCOUNT ID (an identifier, not a credential) — needed by
        # R2 wrangler calls in pipeline scripts; verbatim from ORDER 20's taps.
        if env.get("CLOUDFLARE_ACCOUNT_ID") not in (None, "", CLOUDFLARE_ACCOUNT_ID):
            sys.exit("db-tap: ambient Cloudflare account differs from wrangler.toml")
        env["CLOUDFLARE_ACCOUNT_ID"] = CLOUDFLARE_ACCOUNT_ID
        rc = subprocess.run([os.path.join(REPO, ".venv", "bin", "python"), target_abs, *extra], env=env).returncode

    if not writable and rc != 0:
        print(
            "refused (or failed) while running read-only. To write, set "
            'CARR_BREAK_GLASS=1 and pass --reason "..." — see tools/db-tap.py.',
            file=sys.stderr,
        )
    sys.exit(rc)


if __name__ == "__main__":
    main()
