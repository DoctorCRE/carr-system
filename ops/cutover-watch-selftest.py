#!/usr/bin/env python3
"""cutover-watch-selftest.py — hermetic proof for the cutover-watch agent
(bin/cutover-watch.sh, tools/cutover-watch.py, ops/launchd/com.carr.cutover-watch.plist).

WHY HERMETIC. The real script needs a live ops.job / ops.job_receipt /
ops.workflow_acceptance ledger under the narrow jobs credential and a
reachable record layer to write loop #532 — neither belongs in a suite that
must run on a cold Mac or in CI. What this suite CAN prove without either is
the part the real script's behaviour actually reduces to: the pure
build_snapshot()/diff_snapshot() pair (imported directly from
tools/cutover-watch.py, not reimplemented — a fix to either function is
proven by the same file it changes), plus static, file-text properties that
do not need a database at all: the credential-boundary shape, the plist's
wiring through bin/run-scheduled.sh, its doctrine declaration, the
services.json entry, and the scheduler-truth.py install exemption that keeps
"not installed here on purpose" from reading as drift.

Same case()/CASES table style as ops/cutover-readiness-selftest.py.

  ./.venv/bin/python ops/cutover-watch-selftest.py [-v]

Exit 0 = every case passed. Exit 1 = at least one did not.
"""
from __future__ import annotations
from typing import Any

import json
import plistlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.loadpy import load_module_from_path  # noqa: E402
from lib import launchd_calendar  # noqa: E402
from lib.launchd_scope import PRIMARY_ONLY  # noqa: E402

cw = load_module_from_path("cutover_watch", str(REPO / "tools" / "cutover-watch.py"))
st = load_module_from_path("scheduler_truth", str(REPO / "tools" / "scheduler-truth.py"))

SH_PATH = REPO / "bin" / "cutover-watch.sh"
PLIST_PATH = REPO / "ops" / "launchd" / "com.carr.cutover-watch.plist"
SERVICES_PATH = REPO / "ops" / "config" / "services.json"

VERBOSE = "-v" in sys.argv[1:]
CASES: list[tuple] = []


def case(name, fn):
    CASES.append((name, fn))


# ---------------------------------------------------------------------------
# build_snapshot() / diff_snapshot() — the real decision logic
# ---------------------------------------------------------------------------

def unaccepted_row(workflow_key, mode, receipt_ref, version=1):
    return {"workflow_key": workflow_key, "mode": mode, "receipt_ref": receipt_ref,
            "definition_version": version, "created_at": "2026-08-27T00:00:00Z"}


def dead_letter_row(job_id):
    return {"id": job_id, "workflow_key": "notes-sweep", "mode": "canary",
            "ended_at": "2026-08-27T00:00:00Z", "last_failure_class": "exit_78",
            "last_failure_detail": "not configured"}


case("build_snapshot: unaccepted rows key by workflow/mode/receipt_ref, sorted and deduped",
     lambda: cw.build_snapshot(
         [unaccepted_row("calendar-fetch-daily", "canary", "r2"),
          unaccepted_row("calendar-fetch-daily", "canary", "r1"),
          unaccepted_row("calendar-fetch-daily", "canary", "r1")],  # exact dup collapses
         [], [])["unaccepted"]
     == ["calendar-fetch-daily/canary#r1", "calendar-fetch-daily/canary#r2"])

case("build_snapshot: dead-letter rows key by job id",
     lambda: cw.build_snapshot([], [dead_letter_row("j-1"), dead_letter_row("j-2")], [])
     ["dead_letters"] == ["j-1", "j-2"])

case("build_snapshot: acceptance_rows is a count, not identities",
     lambda: cw.build_snapshot([], [], [{"a": 1}, {"a": 2}, {"a": 3}])["acceptance_rows"] == 3)

# ---- the property the whole agent exists to have: silent when unchanged ----

case("diff_snapshot: identical old/new state is silent (a quiet run stays quiet)",
     lambda: cw.diff_snapshot(
         cw.build_snapshot([unaccepted_row("x", "shadow", "r1")], [dead_letter_row("j-1")], []),
         cw.build_snapshot([unaccepted_row("x", "shadow", "r1")], [dead_letter_row("j-1")], []))
     == [])

case("diff_snapshot: first run (no sentinel) with a clear ledger is also silent",
     lambda: cw.diff_snapshot(None, cw.build_snapshot([], [], [])) == [])

case("diff_snapshot: first run (no sentinel) with existing pending state speaks",
     lambda: len(cw.diff_snapshot(
         None, cw.build_snapshot([unaccepted_row("x", "shadow", "r1")], [], []))) == 1)

# ---- it speaks when a new dead-letter or unaccepted receipt appears ----

case("diff_snapshot: a newly dead-lettered job speaks",
     lambda: any("newly dead-lettered" in c for c in cw.diff_snapshot(
         cw.build_snapshot([], [dead_letter_row("j-1")], []),
         cw.build_snapshot([], [dead_letter_row("j-1"), dead_letter_row("j-2")], []))))

case("diff_snapshot: a newly unaccepted receipt speaks",
     lambda: any("not yet accepted" in c for c in cw.diff_snapshot(
         cw.build_snapshot([unaccepted_row("x", "shadow", "r1")], [], []),
         cw.build_snapshot([unaccepted_row("x", "shadow", "r1"),
                             unaccepted_row("y", "canary", "r9")], [], []))))

case("diff_snapshot: a receipt clearing (accepted or left succeeded) is also reported",
     lambda: any("no longer pending" in c for c in cw.diff_snapshot(
         cw.build_snapshot([unaccepted_row("x", "shadow", "r1")], [], []),
         cw.build_snapshot([], [], []))))

case("diff_snapshot: acceptance_rows count alone (no set change) does not speak",
     lambda: cw.diff_snapshot(
         cw.build_snapshot([], [], [{"a": 1}]),
         cw.build_snapshot([], [], [{"a": 1}, {"a": 2}])) == [])

# ---------------------------------------------------------------------------
# credential boundary — static text, no live database or credential needed
# ---------------------------------------------------------------------------

# Every DSN name this script must NEVER reach for. Deliberately the same
# forbidden shape rule-delivery-shadow-ledger.py and
# ops/routine-credential-boundary-selftest.py already police — one list, not
# a second guess at what "authority" means here.
FORBIDDEN_CREDENTIAL_NAMES = (
    "CARR_DB_WRITER_URL", "CARR_DB_OWNER_URL", "CARR_DB_CADENCE_URL",
    "CARR_DB_MATCHER_URL", "CARR_DB_EXPORTER_URL", "CARR_DB_BACKUP_URL",
    "BACKUP_DATABASE_URL", "CARR_IMPORT_DB_URL", "CARR_DB_AUTHORITY_JOE_URL",
    "DATABASE_URL", "PGPASSWORD",
)

sh_text = SH_PATH.read_text(encoding="utf-8") if SH_PATH.exists() else ""
py_text = (REPO / "tools" / "cutover-watch.py").read_text(encoding="utf-8")

case("bin/cutover-watch.sh exists", lambda: SH_PATH.exists())

for _name in FORBIDDEN_CREDENTIAL_NAMES:
    case(f"bin/cutover-watch.sh never names the authority credential {_name}",
         (lambda n=_name: n not in sh_text))
    case(f"tools/cutover-watch.py never names the authority credential {_name}",
         (lambda n=_name: n not in py_text))

case("bin/cutover-watch.sh never sources db.env directly (the comment on line "
     "51 only explains that it does not, which is not the same as doing it)",
     lambda: not re.search(r'^\s*(?:\.|source)\s+.*db\.env', sh_text, re.M))

case("bin/cutover-watch.sh sources the narrow routine credential loader",
     lambda: 'source "$REPO/bin/routine-credential-env.sh"' in sh_text)

case("bin/cutover-watch.sh clears the routine env before loading",
     lambda: "carr_clear_routine_db_env" in sh_text)

case("bin/cutover-watch.sh loads ONLY CARR_DB_JOBS_URL, no other key",
     lambda: bool(re.search(r"carr_load_routine_db_env\s+CARR_DB_JOBS_URL\s*(?:[;\n]|$)",
                             sh_text)))

case("bin/cutover-watch.sh declares its risk color",
     lambda: "RISK COLOR:" in sh_text)

case("bin/cutover-watch.sh never accepts, promotes, disables, or dispatches anything",
     lambda: not re.search(r'"call",\s*"(accept-workflow|disable-legacy-schedule|'
                            r'promote-\w+|complete-action|issue-execution-envelope)"',
                            sh_text))

case("tools/cutover-watch.py's only verb calls are read-loop and update-loop",
     lambda: set(re.findall(r'call_verb\("([a-z-]+)"', py_text)) == {"read-loop", "update-loop"})

case("tools/cutover-watch.py's record write is scoped to loop #532",
     lambda: 'LOOP_NUMBER = "532"' in py_text)

case("tools/cutover-watch.py refuses a connection that is not the routine role",
     lambda: "ALLOWED_ROLE" in py_text and 'ALLOWED_ROLE = "carr_jobs"' in py_text)

# ---------------------------------------------------------------------------
# the plist
# ---------------------------------------------------------------------------

case("ops/launchd/com.carr.cutover-watch.plist exists and parses", lambda: PLIST_PATH.exists())

_pl = plistlib.loads(PLIST_PATH.read_bytes()) if PLIST_PATH.exists() else {}
_plist_text = PLIST_PATH.read_text(encoding="utf-8") if PLIST_PATH.exists() else ""

case("plist Label matches the file name convention",
     lambda: _pl.get("Label") == "com.carr.cutover-watch")

case("plist is invoked through bin/run-scheduled.sh (a durable run result, not a bare script)",
     lambda: any("run-scheduled.sh" in str(a) for a in _pl.get("ProgramArguments", [])))

def _wrapper_service_key(pl):
    args = [str(a) for a in pl.get("ProgramArguments", [])]
    for i, a in enumerate(args):
        if "run-scheduled.sh" in a:
            return args[i + 1] if i + 1 < len(args) else None
    return None


case("plist's run-scheduled.sh service key matches its services.json key",
     lambda: _wrapper_service_key(_pl) == "cutover-watch")

case("plist uses the repo's {{REPO}} token, not a resolved absolute path",
     lambda: all("{{REPO}}" in str(a) for a in _pl.get("ProgramArguments", [])
                 if a.endswith(".sh")))

case("plist StandardOut/ErrorPath land under {{REPO}}/out/",
     lambda: str(_pl.get("StandardOutPath", "")).startswith("{{REPO}}/out/")
     and str(_pl.get("StandardErrorPath", "")).startswith("{{REPO}}/out/"))

case("plist wakes every 30 minutes as specified",
     lambda: "StartInterval" not in _pl and launchd_calendar.cadence_seconds(_pl) == 1800)

case("plist carries a mechanism-doctrine-gate declaration",
     lambda: bool(re.search(r"doctrine:\s*[A-Za-z0-9][A-Za-z0-9._-]{3,}\s*-->", _plist_text)))

# ---------------------------------------------------------------------------
# services.json registration
# ---------------------------------------------------------------------------

_services = json.loads(SERVICES_PATH.read_text(encoding="utf-8"))["services"]
_svc: dict[str, Any] = next((s for s in _services if s.get("key") == "cutover-watch"), {})

case("services.json declares the cutover-watch service", lambda: bool(_svc))

case("services.json entry names the right script",
     lambda: _svc.get("repo_path") == "bin/cutover-watch.sh")

case("services.json entry names the right plist",
     lambda: any(e.get("deploy_mechanism") == "ops/launchd/com.carr.cutover-watch.plist"
                 for e in _svc.get("environments", [])))

case("services.json entry is launchd runtime with an owner_actor",
     lambda: _svc.get("runtime") == "launchd" and bool(_svc.get("owner_actor")))

case("services.json entry declares an expected cadence matching the plist's calendar cadence",
     lambda: any(e.get("expected_cadence_seconds") == 1800 for e in _svc.get("environments", [])))

# ---------------------------------------------------------------------------
# scheduler-truth.py knows it is not installed yet, on purpose
# ---------------------------------------------------------------------------

case("tools/scheduler-truth.py exempts the not-yet-installed cutover-watch plist",
     lambda: "com.carr.cutover-watch" in st.INSTALL_EXEMPT)

# ---------------------------------------------------------------------------
# ops/config-as-code.py keeps this primary-only (a shared-record writer)
# ---------------------------------------------------------------------------

case("ops/config-as-code.py keeps cutover-watch PRIMARY_ONLY (writes the shared record)",
     lambda: "com.carr.cutover-watch.plist" in PRIMARY_ONLY)


def main() -> int:
    failed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
        except Exception as e:  # noqa: BLE001 — a case that raises is a failed case
            ok = False
            name = f"{name}  [EXCEPTION: {type(e).__name__}: {e}]"
        if VERBOSE or not ok:
            print(f"  {'OK' if ok else 'FAIL'}  {name}")
        if not ok:
            failed += 1
    print(f"cutover-watch-selftest: {len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
