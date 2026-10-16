#!/usr/bin/env python3
"""last-deployed-verb-count.py — what the last deploy of this service to this
environment actually shipped, read from the ledger.

WHY THIS EXISTS. bin/deploy-worker.sh refuses unlisted verb loss,
and it needs a previous number to compare against. That number used to live in
mcp-server/.last-deployed-verb-count.<env>, a file the deploy wrote and the
postflight told you to commit. Committing it fails ops/release-manifest-selftest.py
(defect d737c09c): manifest artifact_paths are ['mcp-server','dealroom'], so the
file sits inside the digested artifact while the digest skips dotfiles — moving
the deployed tree without moving its digest, which is the exact condition that
test guards.

THE FILE WAS ALWAYS A DUPLICATE. Every deploy already records `--verb-count`
into ops.deployment, and since the 2026-08-16 ledger fix it does so for staging
as well as production. Write law 14181e60 settles which copy survives: the
database is the source, a file exists only where a machine requires one, and
nothing requires this one.

ONE CODE PATH, TWO CALLERS (rule a8c55a47): the deploy asks this, and so can CI
or a human. A second copy of the query in shell would be free to drift.
The ledger stores counts, so verb-count.sh --check-retirements applies the
committed retirement names with exact count arithmetic for an intended shrink.

EXIT CODES, and the shell caller depends on telling them apart:
  0   a previous deployment exists; its verb count is printed on stdout
  3   no prior deployment for this service+environment — a genuine first deploy,
      which must be allowed to establish the baseline rather than refused forever
  78  no credential in this environment (EX_CONFIG, the convention bin/nightly.sh
      and bin/run-scheduled.sh already use for "not configured here")
  1   the ledger exists but could not be read — the caller must FAIL CLOSED

WHY FAIL CLOSED IS CHEAP HERE, stated so nobody softens it later: the Worker
being deployed needs Postgres for every verb it serves. A deploy attempted while
the ledger is unreachable would ship something that cannot work anyway. Refusing
costs nothing real, and a loss guard that cannot check must never wave a deploy
through — that is how the 2026-08-09 verb loss happened, when production went
from 75 verbs to 66 and nothing objected.

Usage:  last-deployed-verb-count.py <service> <environment>
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Only states that represent something that actually SHIPPED. A planned or
# aborted row never reached the edge, so its count is not a baseline. `verifying`
# counts: the code is live, only the golden suite is unproven.
SHIPPED_STATES = ("complete", "verifying", "rolled_back", "superseded")


def load_ops_record():
    """tools/ops-record.py as a module — THE ONE reader of the ops ledger."""
    spec = importlib.util.spec_from_file_location(
        "ops_record", REPO / "tools" / "ops-record.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load tools/ops-record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline_row(cur, service, environment):
    """The row bin/deploy-worker.sh's verb-loss guard measures against, with the
    identity fields alongside the count.

    EXPORTED SO THERE IS ONE DEFINITION OF "BASELINE" (rule a8c55a47). The check
    in ops/deploy-ledger-vs-live.py has to reason about the very same row this
    script prints a number from; a second copy of this WHERE clause would be free
    to drift, and the whole point of that check is that two records disagreeing
    is the bug. Note `verb_count is not null` is part of the definition, not an
    incidental filter: a complete row carrying no count is invisible here, which
    on 2026-08-20 silently held the baseline at 141 while production served 143.

    Returns (verb_count, git_sha, provider_version_id, state, observed_at) or None.
    """
    cur.execute(
        """select d.verb_count, d.git_sha, d.provider_version_id,
                  d.state, d.observed_at
             from ops.deployment d
             join ops.service s on s.id = d.service_id
            where s.key = %s
              and d.environment = %s
              and d.verb_count is not null
              and d.state = any(%s)
            order by d.observed_at desc
            limit 1""",
        (service, environment, list(SHIPPED_STATES)))
    return cur.fetchone()


def countless_rows_newer_than_baseline(cur, service, environment, observed_at):
    """Shipped rows newer than the baseline that carry NO verb count.

    WHY THIS EXISTS (defect 4077b653, 2026-08-22). `verb_count is not null` in
    baseline_row above is part of the definition of a baseline, and it has to
    be — a row with no count cannot be compared against. But skipping such a row
    SILENTLY means the guard keeps answering with an older number and printing
    OK, which is a loss guard reporting on a state of the world that has moved.

    That is not hypothetical twice over. baseline_row's own docstring records it
    holding at 141 while production served 143 on 2026-08-20. It then happened
    again structurally: `--promote-version` skips the preflight that sets the
    shipping count, Production source deploys are disabled outright, so every
    Production release records no count and the baseline froze at 143 while
    production served 146. The producer half is fixed in bin/deploy-worker.sh;
    this half makes the freeze VISIBLE rather than trusting that it never
    recurs, because the first failure was already written down in a docstring
    and that did not stop the second.

    Reported, never fatal here. The caller decides — see the STALE BASELINE
    block in bin/deploy-worker.sh for why it warns loudly rather than refusing:
    refusing would block a legitimate release on a bookkeeping gap in rows that
    already exist, which is a control lowering the floor it was meant to raise
    (decision 91020f79).
    """
    cur.execute(
        """select count(*)
             from ops.deployment d
             join ops.service s on s.id = d.service_id
            where s.key = %s
              and d.environment = %s
              and d.verb_count is null
              and d.state = any(%s)
              and d.observed_at > %s""",
        (service, environment, list(SHIPPED_STATES), observed_at))
    return int(cur.fetchone()[0])


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: last-deployed-verb-count.py <service> <environment>",
              file=sys.stderr)
        return 64
    service, environment = sys.argv[1], sys.argv[2]

    try:
        ops = load_ops_record()
    except Exception as exc:
        print(f"last-deployed-verb-count: {exc}", file=sys.stderr)
        return 1

    try:
        conn = ops.connect("read")
    except SystemExit as exc:
        # ops-record raises SystemExit with a credential message when no DSN is
        # set. That is "not configured here", never "the ledger is broken".
        msg = str(exc)
        if "no credential" in msg or "psycopg not installed" in msg:
            print(f"last-deployed-verb-count: {msg}", file=sys.stderr)
            return 78
        print(f"last-deployed-verb-count: {msg}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"last-deployed-verb-count: could not reach the ledger: {exc}",
              file=sys.stderr)
        return 1

    try:
        with conn, conn.cursor() as cur:
            row = baseline_row(cur, service, environment)
            stale_newer = (
                countless_rows_newer_than_baseline(cur, service, environment, row[4])
                if row else 0)
    except Exception as exc:
        print(f"last-deployed-verb-count: could not read the ledger: {exc}",
              file=sys.stderr)
        return 1

    if not row or row[0] is None:
        print(f"last-deployed-verb-count: no prior deployment of {service} to "
              f"{environment} carries a verb count", file=sys.stderr)
        return 3

    # STDERR, never stdout: the caller reads stdout as the number itself.
    if stale_newer:
        print(f"last-deployed-verb-count: STALE BASELINE — {stale_newer} newer "
              f"{environment} deployment(s) of {service} shipped with NO verb count "
              f"recorded, so this baseline of {int(row[0])} is from "
              f"{row[4]:%Y-%m-%d} and does not describe what is serving now. "
              "A loss guard comparing against it can pass while verbs disappear "
              "(defect 4077b653).", file=sys.stderr)
    print(int(row[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
