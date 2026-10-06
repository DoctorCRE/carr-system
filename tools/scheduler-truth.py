#!/usr/bin/env python3
"""scheduler-truth.py — one answer to "what is actually scheduled on this Mac,
and does anything know about it". Program 4's "consolidate scheduler truth".

THE PROBLEM, counted 2026-08-14. Five places claim to know what runs on this
machine and no two of them agreed:

    launchctl list                 16 com.carr.* jobs loaded
    ~/Library/LaunchAgents         16 com.carr.* plists installed
    ops/launchd/                   17 plists in the repo
    ops/config/services.json       23 services declared
    ops.service                    23 rows (25 service/environment rows)

Every one of those numbers was reachable in about a minute, and nobody had put
them side by side, so the gaps were invisible: NINE launchd jobs registered
nowhere, and one plist in the repo that is not installed. A job registered
nowhere is worse off than a job that looks unhealthy — it cannot appear in
`ops-record health` at all, so its failure is not `unknown`, it is
unrepresentable, and to a human reading the table that is indistinguishable
from nothing being wrong.

WHAT THIS FILE DOES NOT CLAIM, because an earlier draft of it did and was wrong.
The registry IS applied: services.json's 23 declarations and ops.service's 23
rows agree exactly. The first version of this docstring said eleven services had
never been synced, on the strength of a `health` listing that had been truncated
to its last 60 lines by the command that produced it. Reading a summary and not
the artifact is rule fa217e48, and the number that mattered was hiding in the
part scrolled off the top: 21 of the 25 service/environment rows have never been
observed AT ALL, which is a far worse finding than the one the truncated view
suggested.

WHAT THIS IS NOT. It is not a second registry. ops/config/services.json remains
the declaration and ops.service remains its render; this file reads all five
sources and reports where they disagree. Adding a sixth source of truth to fix
five that disagree is how the disease spreads.

WHY IT RUNS WITHOUT A DATABASE. A cold Mac, a CI container and a session whose
credential has not loaded all still need the first four sources reconciled — and
the fifth being unreachable is itself a finding worth printing rather than a
reason to refuse. The database section is additive.

EXIT CODES: 0 clean, 1 drift found, 2 could not read the machine at all.

    tools/scheduler-truth.py              # the report
    tools/scheduler-truth.py --quiet      # only the drift lines
"""
import glob
import json
import os
import plistlib
import re

import subprocess
import sys
from typing import Any, Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
from lib.launchd_observation import LaunchdObservationError
from lib.launchd_observation import loaded_labels as native_loaded_labels

REPO_PLISTS = os.path.join(REPO, "ops", "launchd")
INSTALLED = os.path.expanduser("~/Library/LaunchAgents")
SERVICES = os.path.join(REPO, "ops", "config", "services.json")

# nightly-record-layer records every one of its STEPS itself, under one
# correlation id per night (bin/nightly.sh's record_run). Wrapping the chain as
# a whole would add a second, coarser row saying only "the chain exited 1" next
# to the precise row naming which step did — worse, not better. This is an
# exemption with a reason, which is the only kind that should exist: a bare
# allow-list is how a gap becomes permanent.
WRAPPER_EXEMPT = {
    "nightly-record-layer": "records each of its own steps under one correlation "
                            "id; a whole-chain row would be coarser than what it "
                            "already writes",
    # KeepAlive servers. The wrapper records when its child EXITS, and a KeepAlive
    # child exiting means launchd restarted it — so wrapping these would file a run
    # row per restart and read as repeated failures of a service doing exactly what
    # it was built to do. Up-or-down is the question they need answered, and no
    # run row from the wrapper can ask it.
    #
    # THE MISSING SIGNAL IS NO LONGER MISSING (2026-08-14). bin/probe-keepalive.py
    # asks the question every 10 minutes and records the answer, so these servers
    # are exempt from the WRAPPER while being fully observed — which is a
    # different state from the one this list described when it was written, and
    # worth spelling out so nobody "fixes" the exemption by wrapping them.
    "call-mode": "KeepAlive server — probed by bin/probe-keepalive.py (TCP "
                 "connect to 127.0.0.1:4682); the wrapper would record one row "
                 "per restart",
    "doc-engine": "KeepAlive server — probed by bin/probe-keepalive.py (TCP "
                  "connect to 127.0.0.1:4680); same wrapper reason as call-mode",
    "canary-ingest-sink": "KeepAlive server — probed by bin/probe-keepalive.py "
                          "(TCP connect to 127.0.0.1:4684) on its installation "
                          "machine; the wrapper would record one row per restart",
    "quill-dictate": "KeepAlive server — probed by bin/probe-keepalive.py, "
                     "process liveness only (no port to knock on); same wrapper "
                     "reason as call-mode",
    # The control-plane tick has its own durable result: it uses the jobs-role
    # credential to make idempotent ledger enqueues.  Sending it through
    # bin/run-scheduled.sh would introduce the broad service-run writer on the
    # execution path and defeat the narrow-credential boundary it exists to
    # enforce.  It is therefore deliberately direct, with this explicit reason
    # rather than an invisible coverage hole.
    "control-plane-tick": "jobs-role ledger adapter — durable work state is the "
                          "idempotent enqueue; run-scheduled.sh would require a "
                          "broader writer credential",
    "calendar-prebrief-joe": "receipt-gated jobs adapter — its exact ops.job "
                              "lease and calendar-prebrief receipts are the "
                              "durable result; wrapping it would add the broad "
                              "ops.run writer without improving that evidence",
}

# A plist that is deliberately NOT installed here. Without this, the reconciliation
# reports a drift line every run for a machine that is configured correctly, and a
# permanent false line is how a real one stops being read.
INSTALL_EXEMPT = {
    "com.carr.fetch-allowlist": "SECONDARY MACHINE ONLY — on Joe's primary Mac the "
                                "allowlist is regenerated as a step of the nightly "
                                "chain, so this agent is correct to be absent here",
    "com.carr.control-plane-tick": "DEFINITION ONLY — the ledger adapter is not "
                                     "installed until its shadow/canary evidence and "
                                     "the required cutover approval exist; legacy "
                                     "schedules remain active in the meantime",
    "com.carr.cutover-watch": "NOT YET INSTALLED — built in worktree cutover-watch "
                              "(2026-08-27); the orchestrating session installs it "
                              "after this branch merges to main. Remove this entry "
                              "once that install has happened, or this line goes "
                              "stale in the direction that hides a real gap.",
}

DRIFT: list[str] = []

# Set only when the database render was genuinely read. The verdict below
# distinguishes 'every source agrees' from 'the four I could read agree',
# because those are different sentences and only one of them is true when
# the ledger is unreachable.
REGISTRY_COMPARED = False


def drift(line: str) -> None:
    DRIFT.append(line)


def label_of(path: str) -> str:
    return os.path.basename(path).replace(".plist", "")


def read_plist(path: str) -> dict[str, Any]:
    """Repo copies store {{REPO}}/{{HOME}}/{{VAULT}} placeholders (see
    ops/config-as-code.py) — plistlib does not care, and neither does anything
    below, which compares structure rather than resolved paths."""
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 — a malformed plist IS the finding
        return {"__error__": str(exc)}


def loaded_labels() -> Optional[set[str]]:
    """What launchd currently holds, from a native exact print read.

    ``launchctl list`` is not an authoritative inventory on this Mac and has
    reported zero while exact ``launchctl print`` reads found loaded jobs.
    ``None`` means launchd was unreadable; it must never be treated as empty.
    """
    try:
        return native_loaded_labels()
    except (LaunchdObservationError, OSError, subprocess.SubprocessError):
        return None


def wrapped(pl: dict[str, Any]) -> bool:
    return any("run-scheduled.sh" in str(a) for a in pl.get("ProgramArguments", []))


# bin/run-scheduled.sh's own optional flags (Program 4 follow-up), each
# consuming exactly one value. Kept in sync with that script's flag-parsing
# loop by hand — there being only two of them and both single-value is what
# makes hand-sync tolerable; a third flag should make this a shared table
# instead of two copies.
WRAPPER_FLAGS_WITH_VALUE = {"--heartbeat-interval", "--also-heartbeat"}


def wrapper_service(pl: dict[str, Any]) -> str:
    """The service key a wrapped plist reports under: the first positional
    argument after the wrapper path and any of ITS OWN flags. Before Program 4's
    heartbeat throttle this was simply "the argument right after the wrapper
    path" — now a plist that inserts --heartbeat-interval 1800 ahead of the
    service key would misread the flag NAME itself as the key unless those
    flags are skipped exactly the way bin/run-scheduled.sh's own parser skips
    them. Reading it back is what catches a copy-paste that wrapped a job
    under a neighbour's key — the failure mode of rewiring seven files."""
    args = [str(a) for a in pl.get("ProgramArguments", [])]
    for i, a in enumerate(args):
        if "run-scheduled.sh" not in a:
            continue
        j = i + 1
        while j < len(args):
            tok = args[j]
            if tok in WRAPPER_FLAGS_WITH_VALUE:
                j += 2
                continue
            if tok == "--":
                j += 1
            return args[j] if j < len(args) else ""
        return ""
    return ""



def disabled_labels():
    """Labels launchd itself reports as disabled, in either vocabulary.

    macOS 26 prints `"label" => disabled`; older releases print `=> true`.
    lib/launchd_scheduler_native.py learned this the same week; the two readers
    stay in step because both accept both spellings and neither guesses.
    """
    try:
        out = subprocess.run(["/bin/launchctl", "print-disabled", f"gui/{os.getuid()}"],
                             capture_output=True, text=True, check=False)
    except OSError:
        return set()
    if out.returncode != 0:
        return set()
    found = set()
    for line in out.stdout.splitlines():
        m = re.match(r'\s*"?([A-Za-z0-9._-]+)"?\s*=>\s*([A-Za-z]+)\b', line)
        if m and m.group(2).lower() in ("true", "disabled"):
            found.add(m.group(1))
    return found

def main() -> int:
    quiet = "--quiet" in sys.argv

    if not os.path.isdir(REPO_PLISTS):
        print(f"scheduler-truth: {REPO_PLISTS} is missing", file=sys.stderr)
        return 2

    repo = {label_of(p): read_plist(p) for p in glob.glob(f"{REPO_PLISTS}/*.plist")}
    inst = {label_of(p): read_plist(p)
            for p in glob.glob(f"{INSTALLED}/com.carr.*.plist")}
    live = loaded_labels()

    declared: dict[str, Any] = {}
    try:
        for svc in json.load(open(SERVICES))["services"]:
            declared[svc["key"]] = svc
    except Exception as exc:  # noqa: BLE001
        print(f"scheduler-truth: cannot read {SERVICES}: {exc}", file=sys.stderr)
        return 2

    launchd_declared = {k: v for k, v in declared.items()
                        if v.get("runtime") == "launchd"}

    if not quiet:
        print("Scheduler truth — five sources, one table\n")
        print(f"  ops/launchd/            {len(repo):>3} plist(s) in the repo")
        print(f"  ~/Library/LaunchAgents  {len(inst):>3} plist(s) installed")
        print(f"  launchctl print         {'  ?' if live is None else f'{len(live):>3}'}"
              f" com.carr.* job(s) loaded")
        print(f"  services.json           {len(declared):>3} service(s) declared"
              f" ({len(launchd_declared)} of them launchd)")

    # ── repo vs machine ──────────────────────────────────────────────────────
    for lbl in sorted(set(repo) - set(inst)):
        if lbl in INSTALL_EXEMPT:
            if not quiet:
                print(f"\n  n/a  {lbl}: not installed here on purpose — "
                      f"{INSTALL_EXEMPT[lbl]}")
            continue
        drift(f"IN REPO, NOT INSTALLED   {lbl} — ops/config-as-code.py install --apply")
    for lbl in sorted(set(inst) - set(repo)):
        drift(f"INSTALLED, NOT IN REPO   {lbl} — ops/config-as-code.py pull "
              f"(config that exists only on one Mac is config nobody can restore)")

    # ── installed vs loaded ──────────────────────────────────────────────────
    if live is not None:
        disabled = disabled_labels()
        for lbl in sorted(set(inst) - live):
            # A RETIRED SCHEDULE IS NOT DRIFT. Once a legacy agent is switched
            # off — disabled in launchd's registry and unloaded — its plist
            # stays on disk on purpose, so the retirement is auditable and
            # reversible. Read as drift it would tell every future run to
            # bootstrap the very job the cutover just retired, and each
            # retirement would leave a permanent false alarm behind it.
            # Measured 2026-08-28 on the first real retirement,
            # com.carr.calendar-eventkit.
            if lbl in disabled:
                if not quiet:
                    print(f"\n  n/a  {lbl}: retired on purpose — disabled in launchd's "
                          f"registry and unloaded; the plist is kept so the switch-off "
                          f"stays auditable and reversible")
                continue
            drift(f"INSTALLED, NOT LOADED    {lbl} — launchctl bootstrap "
                  f"gui/$UID {INSTALLED}/{lbl}.plist")
        for lbl in sorted(live - set(inst)):
            drift(f"LOADED, NO PLIST ON DISK {lbl} — loaded from something that is "
                  f"no longer there; it will not survive a reboot")

    # ── malformed ────────────────────────────────────────────────────────────
    for src, where in ((repo, "ops/launchd"), (inst, "~/Library/LaunchAgents")):
        for lbl, pl in sorted(src.items()):
            if "__error__" in pl:
                drift(f"UNREADABLE PLIST         {where}/{lbl}.plist — {pl['__error__']}")

    # ── scheduled but registered nowhere ─────────────────────────────────────
    # This is the one that matters most. A launchd job with no service row
    # cannot appear in `ops-record health` at all, so its failure is not
    # "unknown" — it is unrepresentable, which reads to a human exactly like
    # nothing being wrong.
    for lbl in sorted(repo):
        short = lbl.replace("com.carr.", "")
        if short not in declared:
            drift(f"SCHEDULED, UNREGISTERED  {short} — runs on this Mac and no "
                  f"service declares it, so no health row can ever mention it. "
                  f"Add it to ops/config/services.json")

    for key, svc in sorted(launchd_declared.items()):
        if f"com.carr.{key}" in repo:
            continue
        # A service can RIDE another job's plist instead of owning one under
        # its own name (Program 4's carr-local-edge-node: no LaunchAgent of
        # its own, recorded via --also-heartbeat on partner-ping's wake). Its
        # own deploy_mechanism says so explicitly, so honor that declaration
        # rather than assuming plist filename always equals service key — only
        # a service with NO plist anywhere the registry points to is drift.
        rides_another_plist = any(
            env.get("deploy_mechanism", "").startswith("ops/launchd/")
            and os.path.basename(env["deploy_mechanism"]).removesuffix(".plist") in repo
            for env in svc.get("environments", []))
        if not rides_another_plist:
            drift(f"REGISTERED, NO PLIST     {key} — services.json declares a "
                  f"launchd service with no plist in ops/launchd/")

    # ── recording coverage, the Program 4 gate ───────────────────────────────
    if not quiet:
        print("\nDurable run results — does a failure in this job survive it?\n")
    covered = uncovered = 0
    for key in sorted(launchd_declared):
        lbl = f"com.carr.{key}"
        if lbl not in repo:
            continue
        pl = repo[lbl]
        if key in WRAPPER_EXEMPT:
            if not quiet:
                print(f"  n/a  {key:<24} {WRAPPER_EXEMPT[key]}")
            covered += 1
            continue
        if "__error__" in pl:
            # Already reported as UNREADABLE above. Saying "no durable run
            # result" here too would be a second finding for one cause, and the
            # remedy it prints (wrap the job) would be wrong — the job may
            # already be wrapped, as this one was.
            if not quiet:
                print(f"  ?    {key:<24} cannot tell — its plist does not parse")
            continue
        if wrapped(pl):
            got = wrapper_service(pl)
            if got != key:
                drift(f"WRAPPED UNDER THE WRONG KEY {key} — its plist records as "
                      f"'{got}', so its runs land on another service's history")
            elif not quiet:
                print(f"  ok   {key:<24} records through bin/run-scheduled.sh")
            covered += 1
        else:
            uncovered += 1
            drift(f"NO DURABLE RUN RESULT    {key} — invokes its script directly, "
                  f"so a failure lives only in a local log. Wrap it: "
                  f"/bin/zsh {{{{REPO}}}}/bin/run-scheduled.sh {key} launchd.run <its "
                  f"current ProgramArguments>")

    if not quiet:
        total = covered + uncovered
        print(f"\n  {covered}/{total} registered launchd service(s) write a durable "
              f"run result.")

    # ── the registry vs its render ───────────────────────────────────────────
    # Additive: absent credentials are a finding, not a refusal.
    # The credential is loaded by ops-record.py's OWN loader, imported rather
    # than reimplemented. The first cut of this file re-parsed
    # ~/.config/carr/db.env with a regex, stripped double quotes and not single
    # ones, and handed psycopg a DSN still wrapped in apostrophes — which failed
    # with "invalid connection option" and would have reported the registry
    # unreachable on a Mac where it was perfectly reachable. Values in that file
    # are shell-quoted so `set -a; . db.env` survives an & in a DSN, and
    # ops-record.py already knows that. Rule a8c55a47: one job, one code path.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ops_record", os.path.join(REPO, "tools", "ops-record.py"))
        # spec and spec.loader are Optional in the stubs, and both really can be
        # None — a path that is not an importable source file gives back a spec
        # of None rather than raising. Handled rather than asserted away: this
        # whole section is additive, so "the loader is not there" belongs on the
        # same branch as "the credential is not there" and prints, not raises.
        if spec is None or spec.loader is None:
            raise ImportError(f"{REPO}/tools/ops-record.py is not importable")
        ops_record = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ops_record)
        ops_record._load_db_env()
        dsn = next((os.environ[v] for v in ("DATABASE_URL", "CARR_DB_JOBS_URL")
                    if os.environ.get(v)), "")
        if dsn:
            import psycopg as pg
            with pg.connect(dsn, connect_timeout=10) as conn:
                rows = conn.execute("select key from ops.service").fetchall()
            in_db = {r[0] for r in rows}
            global REGISTRY_COMPARED
            REGISTRY_COMPARED = True
            if not quiet:
                print(f"\n  ops.service             {len(in_db):>3} row(s) in the "
                      f"database")
            missing = set(declared) - in_db
            if missing:
                drift(f"DECLARED, NOT APPLIED    {len(missing)} service(s) in "
                      f"services.json have no ops.service row "
                      f"({', '.join(sorted(missing)[:4])}"
                      f"{', …' if len(missing) > 4 else ''}) — "
                      f"tools/ops-record.py sync-registry")
            for k in sorted(in_db - set(declared)):
                drift(f"IN DATABASE, UNDECLARED  {k} — an ops.service row no "
                      f"declaration produces; sync-registry will never remove it")
        elif not quiet:
            print("\n  ops.service               ? no database credential in this "
                  "environment — the registry's render could not be compared")
    except ImportError:
        if not quiet:
            print("\n  ops.service               ? psycopg unavailable — the "
                  "registry's render could not be compared")
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"\n  ops.service               ? unreachable ({exc}) — the "
                  f"registry's render could not be compared")

    # ── verdict ──────────────────────────────────────────────────────────────
    print()
    if not DRIFT and REGISTRY_COMPARED:
        print("scheduler-truth: no drift — every source agrees.")
        return 0
    if not DRIFT:
        # Four sources agreed and the fifth was never read. Saying "every source
        # agrees" here would be a false all-clear, which is the failure this
        # whole tool was built to detect — and it printed exactly that until
        # ops/degraded-mode-exercise.py cut the database off and caught it.
        print("scheduler-truth: the four local sources agree. THE DATABASE "
              "RENDER WAS NOT READ, so this is not a clean bill — a service "
              "declared here and missing from ops.service would look identical "
              "to this.")
        return 0
    print(f"scheduler-truth: {len(DRIFT)} drift finding(s)\n")
    for d in DRIFT:
        print(f"  ⚠︎ {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
