#!/usr/bin/env python3
# doctrine: runbook
"""Can a normal session still FILE A BLOCK, and does the credentialed close stay shut?

WHY THIS EXISTS (WR-000017's sibling, WR-000006; 2026-08-23 gates-audit council).
On 2026-08-23 at 20:08:51Z a session had a receipted SEV-1 close denied by the
Claude Code auto-mode classifier.  At 20:11:01Z the same session called
`report-problem` — the verb that exists so a session can FILE the block it just
hit — and the classifier denied that too.  The rule "a blocked action is filed"
was itself blocked.  The work rerouted through a fresh human-approved session
and cost hours.  Both council chairs ranked that dead end the worst production
roadblock and asked for exactly one thing: a seeded probe proving a normal
session can reach the capture path, with the credentialed close still shut.

WHAT THIS IS NOT, because the distinction was argued and settled.  It is NOT a
reachability check.  Reachability (ops/reachability-check.py) asks whether a
declared control is referenced from a live path AS VISIBLE IN THE TREE:
repository content only, no machine state, no network, no database.  That
property is what makes a bare runner and Joe's Mac return the same answer, and a
live probe would destroy it — this file talks to the deployed Worker over the
network with a credential CI does not have.  This is the consumer-side receipt
that can read false while every builder-side check is green.  Two questions, two
files.

THE POSITIVE CASE IS A REFUSAL, and that is the whole trick.  Calling
report-problem with ONLY an idempotency_key makes the deployed verb reject it on
its own schema — `missing_required`, naming situation/title/desired_outcome/
acceptance_criteria.  That rejection is the proof: it can only be produced by
code that RAN, so the call crossed the permission boundary and reached the verb
registry.  It also writes nothing, so this is safe on every pre-push.  A
permission denial looks nothing like it: the classifier's refusal never reaches
the process at all.

THE NEGATIVE CASE DELIBERATELY DOES NOT ASSERT THE CLASSIFIER.  On 2026-08-23
the identical break-glass command was DENIED at 20:08:51Z, SUCCEEDED at
20:19-20:20Z from another session eleven minutes later, and was DENIED again at
20:29:03Z.  Then report-problem itself, denied at 20:11:01Z, was ALLOWED at
21:43Z with no settings change.  A subject that changed answer three times in
twenty minutes is not a gate; asserting it would flake by construction and burn
the credibility of everything attached to it.  So the classifier's verdict is
RECORDED AS AN OBSERVATION and never asserted, and what IS asserted is the half
that cannot drift: the database grant.  carr_jobs — the role every scheduled job
runs as — holds a column-scoped UPDATE on ops.incident and no grant at all on
resolved_at or root_cause, so a machine can move an incident to monitoring and
can never mark it closed.  That is "closing an incident is a human's call"
enforced in grants rather than in prose, and it is documented at
resolve_authority() in tools/ops-record.py.

EXIT 78 (EX_CONFIG) WHERE IT CANNOT HONESTLY RUN.  A GitHub runner has no local
token and no database credential.  Reporting red there would teach everyone to
scroll past a check that is merely unconfigured, which is how a gate becomes
decorative.  78 is already this repository's convention for that and ci.sh's
loops honour it, printing the reason on every run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

EX_CONFIG = 78

# The four fields report-problem requires. The positive case asserts the deployed
# verb names EVERY one of them: a truncated list would mean we reached something
# other than the contract we think we reached.
REQUIRED_FIELDS = ("situation", "title", "desired_outcome", "acceptance_criteria")

# Any UUID works — the call is refused on schema before idempotency is consulted,
# so this never reserves a key and never writes a row. Fixed rather than random
# so two runs are byte-identical and a diff of this file's behaviour is readable.
PROBE_KEY = "00000000-0000-0000-0000-000000000000"

# The grant boundary, as ops/ops-record.py's resolve_authority() documents it.
CLOSE_ONLY_COLUMNS = ("resolved_at", "root_cause")


def fail(message: str) -> int:
    print(f"capture-verb-reachability: FAIL — {message}", file=sys.stderr)
    return 1


def skip(message: str) -> int:
    # Printed on stdout: ci.sh's loops tail the log and show this as the reason,
    # so an unconfigured machine says WHY rather than going quiet.
    print(f"capture-verb-reachability: not configured here — {message}")
    return EX_CONFIG


def observe(message: str) -> None:
    """Recorded, never asserted. See the classifier note in the docstring."""
    print(f"capture-verb-reachability: observation — {message}")


def probe_capture_path() -> tuple[bool, str]:
    """(reached, detail). Reached means the call got past permissions to the verb."""
    run = subprocess.run(
        [str(REPO / "run.sh"), "call", "report-problem",
         json.dumps({"idempotency_key": PROBE_KEY})],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    blob = run.stdout + run.stderr

    # A classifier denial never reaches the process, so it cannot produce this
    # text. Match the verb's own refusal rather than the exit code alone: the
    # wrapper prints a banner and exits 0 for a TOOL ERROR too, so exit status
    # alone would call a transport failure a success.
    if '"error"' not in blob or "missing_required" not in blob:
        return False, ("the deployed verb's own missing_required refusal never arrived; "
                       f"exit={run.returncode}, output={blob.strip()[:400] or '(empty)'}")

    absent = [field for field in REQUIRED_FIELDS if field not in blob]
    if absent:
        return False, ("reached a verb that refused, but it did not name "
                       f"{', '.join(absent)} — this may not be report-problem's contract")
    return True, "the deployed verb answered with its own schema refusal, naming all four fields"


def grant_boundary(cursor) -> tuple[bool, str]:
    """The deterministic half: carr_jobs can move an incident, never close one."""
    granted = {
        row[0] for row in cursor.execute(
            "select column_name from information_schema.column_privileges "
            "where grantee = 'carr_jobs' and table_schema = 'ops' "
            "and table_name = 'incident' and privilege_type = 'UPDATE'"
        )
    }
    if not granted:
        return False, ("carr_jobs holds no column-scoped UPDATE on ops.incident at all. "
                       "Either the grant was dropped or this database is not production-shaped; "
                       "either way the boundary this asserts is not the one running.")
    leaked = sorted(column for column in CLOSE_ONLY_COLUMNS if column in granted)
    if leaked:
        return False, (f"carr_jobs can now write {', '.join(leaked)} on ops.incident. "
                       "That is the close itself: a scheduled job could mark an incident "
                       "resolved with nobody deciding it. See resolve_authority() in "
                       "tools/ops-record.py.")
    return True, (f"carr_jobs holds UPDATE on {len(granted)} ops.incident column(s) and none of "
                  f"{', '.join(CLOSE_ONLY_COLUMNS)}")


def main() -> int:
    if not (REPO / "run.sh").exists():
        return skip("run.sh is absent, so there is no local door to probe")

    # The local door needs a machine token. Absent it, the probe would report a
    # permission problem that is really a missing credential. The path is read
    # from local-verb.mjs's own default (and its CARR_MCP_ENV override) rather
    # than guessed — the first draft guessed ~/.config/carr/local-token, which
    # does not exist, so this skipped on the very machine where the door works.
    token = Path(os.environ.get("CARR_MCP_ENV")
                 or Path.home() / ".config" / "carr" / "mcp-tokens.env")
    if not token.exists():
        return skip(f"no local machine token at {token}, so the deployed Worker cannot be reached")

    reached, detail = probe_capture_path()
    if not reached:
        return fail("a normal session can no longer reach report-problem. " + detail +
                    "  This is the 2026-08-23 dead end returning: the verb that exists so a "
                    "session can file a block is itself unreachable. The documented door is "
                    "`./run.sh call report-problem '<json>'` (see CLAUDE.md).")
    print(f"  ok    capture path reachable — {detail}")

    # The credential half. Its absence is a skip, not a pass: silence here would
    # let the whole check go green while asserting only half of what it claims.
    try:
        sys.path.insert(0, str(REPO / "ops"))
        from gate_runtime_role import rollback_only_connection  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return skip(f"gate_runtime_role is unavailable ({exc}), so the grant half cannot run")

    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return skip("no DATABASE_URL, so the carr_jobs grant boundary cannot be read")

    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            held, grant_detail = grant_boundary(cur)
    except Exception as exc:  # noqa: BLE001
        return skip(f"the grant boundary could not be read ({exc})")

    if not held:
        return fail("the credentialed close boundary moved. " + grant_detail)
    print(f"  ok    close stays a human's call — {grant_detail}")

    observe("the harness classifier's own verdict is NOT asserted here. On 2026-08-23 the same "
            "break-glass command was denied at 20:08:51Z, succeeded at 20:19Z, and was denied "
            "again at 20:29:03Z; report-problem was denied at 20:11:01Z and allowed at 21:43Z. "
            "Ruling on that flapping is WR-000002, still open.")
    print("capture-verb-reachability: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
