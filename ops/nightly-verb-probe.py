#!/usr/bin/env python3
"""nightly-verb-probe.py — what the nightly chain CAN verify unattended.

WHY THIS EXISTS (2026-08-04, loop #178, Joe's go). `mcp-server/smoke-reads.sh`
exercised every read verb end-to-end through the Worker, and it was the only
thing proving the verbs actually worked. It authenticated with a static bearer
from ~/.config/carr/mcp-tokens.env — the legacy PARTNER_TOKENS path — which was
deliberately retired on 2026-08-03 (commit 5b13ed7). The Worker no longer has
code that validates it, so `invalid_token` is now correct and permanent, and the
suite has been dead in the chain ever since: 23 probes, 0 passed, every night.

THE FORK JOE RULED. Three options were on the table: mint a machine credential
for /mcp, point the probes at the database, or accept the suite as
interactive-only. Reintroducing a machine bearer would partly undo a retirement
made on purpose. So the chain gets what it CAN check without a partner identity,
and the full Worker suite stays a post-deploy, human-present run.

WHAT THIS CHECKS, and the reasoning for each half.

(1) THE WORKER IS DEPLOYED AND ITS AUTH GATE IS LIVE. /health must answer 200
and /mcp must REFUSE an unauthenticated call with 401. The second is the load-
bearing one: a Worker that stops rejecting anonymous callers is a security
failure that a reachability check alone would score green. Only the primary
host api.doctorcre.com is probed. The retiring alias must not receive requests
from repository callers.

(2) EVERY VIEW THE VERBS READ IS STILL QUERYABLE. This is the half that replaces
real coverage rather than gesturing at it. The read verbs are thin wrappers over
v_* views, so the failure that actually bites is a migration changing or breaking
one — sixteen migrations landed in a single day on 2026-08-02. A view that has
been dropped, renamed, or left referencing a removed column fails here on the
night it breaks instead of the next time a human happens to call the verb.

WHAT IT DELIBERATELY DOES NOT CHECK, stated so nobody reads a green row as more
than it is: transport, MCP tool dispatch, argument validation, and verb-level
answer correctness. Those need the Worker path and a real grant. Run
`./mcp-server/smoke-reads.sh` after every deploy, from an interactive session
that holds a token. This probe is not a replacement for it and does not claim to
be.

ZERO ROWS IS NOT A FAILURE. A view returning nothing is a valid answer — most of
them legitimately empty out. Asserting row counts here would manufacture exactly
the false-alarm noise that trains people to ignore a check.

PERMISSION DENIED IS REPORTED, NOT FAILED. The exporter role genuinely lacks
SELECT on some objects by design. That is a config fact, not schema drift, so it
is listed and does not set the exit code. A view that has VANISHED is drift and
does fail.

  ./.venv/bin/python ops/nightly-verb-probe.py

Exit 0 = Worker gate healthy and every view queryable.
Exit 1 = at least one hard failure; the message carries the real error text.
Exit 0 with SKIP = no credential on this machine (house convention).
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from exporters.common import connect  # noqa: E402

HOSTS = ["api.doctorcre.com"]
TIMEOUT = 15


# Cloudflare 403s urllib's default User-Agent ("Python-urllib/3.x") before the
# request ever reaches the Worker. Caught on this probe's first run, 2026-08-04:
# it reported all four Worker checks failing, including "the auth gate is not
# doing its job", while curl was getting 200 from both hosts in the same second.
# A monitoring script that cannot tell "blocked at the edge" from "the service is
# down" raises a false alarm on its first night and gets ignored by its third.
UA = "carr-nightly-verb-probe/1.0 (+ops/nightly-verb-probe.py)"


def http_status(url, method="GET", body=None):
    """Return the status code, treating an HTTP error as its code rather than an exception."""
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("user-agent", UA)
    if body is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"unreachable ({type(e).__name__})"


def check_worker():
    """Deployed, and still refusing anonymous callers."""
    problems = []
    for host in HOSTS:
        health = http_status(f"https://{host}/health")
        if health != 200:
            problems.append(f"{host}/health returned {health}, want 200")

        # The security half. An unauthenticated /mcp call MUST be refused.
        mcp = http_status(f"https://{host}/mcp", method="POST", body=b"{}")
        if mcp != 401:
            problems.append(
                f"{host}/mcp answered {mcp} to an UNAUTHENTICATED call, want 401 — "
                f"the auth gate is not doing its job")
    return problems


def check_views():
    """Every v_* view still resolves. Drift fails; a missing grant is reported."""
    hard, denied = [], []
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""select table_name from information_schema.views
                        where table_schema = 'public' and table_name like 'v\\_%'
                        order by table_name""")
        views = [r[0] for r in cur.fetchall()]

    if not views:
        return ["information_schema returned NO v_* views — that is itself the finding"], [], 0

    for v in views:
        # Each in its own transaction: one failure must not abort the rest.
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(f'select * from "{v}" limit 1')
                cur.fetchall()
        except Exception as e:
            name = type(e).__name__
            first = str(e).strip().splitlines()[0] if str(e).strip() else name
            if "InsufficientPrivilege" in name or "permission denied" in first:
                denied.append(v)
            else:
                hard.append(f"{v}: {name} — {first}")
    return hard, denied, len(views)


def main():
    try:
        connect().close()
    except SystemExit as e:
        print(f"SKIP nightly-verb-probe: {e}")
        return 0

    worker = check_worker()
    hard, denied, total = check_views()

    if worker:
        for p in worker:
            print(f"  FAIL worker  {p}")
    else:
        print(f"  ok   worker  /health 200 and /mcp 401 on {len(HOSTS)} host(s)")

    if hard:
        for p in hard:
            print(f"  FAIL view    {p}")
    else:
        print(f"  ok   views   {total - len(denied)}/{total} queryable"
              + (f", {len(denied)} refused to the exporter role" if denied else ""))

    if denied:
        print(f"       (no SELECT for exporter: {', '.join(denied)} — config, not drift)")

    if worker or hard:
        print("STALE verb probe — the chain cannot vouch for the record layer tonight")
        print("  note: this probe does NOT cover transport, dispatch or answer "
              "correctness. Run ./mcp-server/smoke-reads.sh interactively after a deploy.")
        return 1

    print("OK verb probe — worker gate healthy, every view queryable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
