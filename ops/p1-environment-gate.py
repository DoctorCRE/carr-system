#!/usr/bin/env python3
"""
p1-environment-gate.py — THE ACCEPTANCE TEST FOR PROGRAM 1, SAFE ENVIRONMENTS,
written before the thing it tests (rule e65efc68).

THE GATE, verbatim from the ordered implementation program: "staging cannot
access Production data or credentials."

That sentence has never been executable. The isolation itself was built on
2026-08-13 — a separate Neon project for the database half, a separate Worker
with its own bindings for the compute half — and wrangler.toml argues the case
at length in prose. Prose is not proof. Nothing has ever ASKED the two
environments whether they are still separate, and nothing would notice the day
they stopped being.

WHY THIS IS NOT A CI GATE. ops/ci.sh's migration class runs against a throwaway
Postgres, which is neither of the two environments this asks about. The
question here is about the REAL projects, so it runs where their credentials
live: this machine, from the nightly chain. A gate pointed at a stand-in would
answer about the stand-in.

WHAT PROVOKED IT. On 2026-08-15 the staging database was found FOUR migrations
behind production, including the one carrying P0-1's release object. Nothing
detected that. It surfaced only because a person went to run an acceptance test
there and got "relation ops.release does not exist". An environment nobody
checks is an environment nobody can trust, and G1 rests on trusting this one.

THE SIX ASSERTIONS.

  1. TWO PROJECTS, NOT TWO BRANCHES. Staging resolves to a different Neon
     project than production, on a different host. A Neon BRANCH lives inside
     its parent project and starts as a copy of its data, so a branch would
     hand staging exactly the production rows this gate exists to keep away
     from it. Only a separate project delivers the isolation.

  2. THE CREDENTIALS ARE NOT THE SAME CREDENTIAL. The two connection strings
     differ. Neither is ever printed, compared only by digest, because a live
     credential reached a session transcript on 2026-08-13 and the project had
     to be destroyed and rebuilt over it.

  3. NO ROW IN STAGING SHARES AN ID WITH A ROW IN PRODUCTION. Identity is the
     test, because identity is what "production data" means. Ids are compared,
     never names, emails or phones, and an overlap is reported as a COUNT — so
     a leak is detectable without this gate reading or printing one field of
     client data. The first version demanded ZERO rows in staging and failed on
     its first run against a synthetic fixture; the environment contract
     defines staging's data as "sanitized representative fixtures or dedicated
     test data", so an empty-table test asks for something never required and
     would fail every time anyone rehearsed anything.

  4. THE STAGING WORKER SHARES NO BINDING. Parsed from wrangler.toml: its own
     Worker name, its own KV namespace, and no routes or custom domains at all.
     A staging environment that shares any one of those is production wearing a
     different label.

  5. STAGING IS CURRENT WITH THE REPOSITORY. Its applied migration set equals
     the committed set. This is the assertion that would have caught the
     2026-08-15 drift on the day it appeared instead of six days later.

  6. PRODUCTION IS CURRENT WITH THE REPOSITORY. The same question pointed the
     other way, so a migration sitting unapplied in production is visible daily
     rather than at the moment somebody needs it.

WHERE IT RUNS. Locally, from bin/nightly.sh, and by hand:

    .venv/bin/python ops/p1-environment-gate.py

IT WRITES NOTHING TO EITHER DATABASE. Production is opened read-only at the
session level, and staging is only counted and queried. The nightly wrapper
records the OUTCOME to the operational ledger; this file reports.
"""

import hashlib
import os
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from lib.pgrow import fetch_one  # noqa: E402

try:
    import psycopg
except ImportError:
    sys.exit("p1-environment-gate: psycopg not installed")

# db-tap owns the project map and the credential derivation, and it never echoes
# a connection string. Importing it keeps ONE definition of where each
# environment lives (rule a8c55a47) rather than a second copy that can drift.
sys.path.insert(0, str(REPO / "tools"))
import importlib.util

_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("p1-environment-gate: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

# Client-identifying tables. Counted, never read. If staging ever needs
# representative data it gets SANITIZED fixtures under a different name, and
# this list is what stops real rows arriving instead.
CLIENT_TABLES = ("party", "client", "deal", "lead", "vendor")

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def digest(secret: str) -> str:
    """A comparable fingerprint that is not the secret. Never print the input."""
    return hashlib.sha256(secret.encode()).hexdigest()[:16]


def host_of(conn_string: str) -> str:
    """The host portion only. Safe to print; carries no credential."""
    tail = conn_string.split("@", 1)[-1]
    return tail.split("/", 1)[0].split("?", 1)[0]


def committed_migrations() -> list[str]:
    from migration_source import migration_names
    return sorted(migration_names(REPO))


def applied_migrations(conn_string: str, read_only: bool) -> list[str]:
    with psycopg.connect(conn_string, autocommit=True) as conn:
        if read_only:
            # PRODUCTION IS OPENED READ-ONLY AT THE SESSION LEVEL. This gate has
            # no business writing there, and saying so to the server is stronger
            # than intending it.
            conn.execute("set default_transaction_read_only = on")
        with conn.cursor() as cur:
            cur.execute("select filename from schema_migrations order by filename")
            return [r[0] for r in cur.fetchall()]


def table_counts(conn_string: str, tables, read_only: bool) -> dict[str, int]:
    counts = {}
    with psycopg.connect(conn_string, autocommit=True) as conn:
        if read_only:
            conn.execute("set default_transaction_read_only = on")
        with conn.cursor() as cur:
            for t in tables:
                cur.execute("select to_regclass(%s)", (f"public.{t}",))
                if fetch_one(cur)[0] is None:
                    counts[t] = -1          # absent is a different answer from empty
                    continue
                cur.execute(f"select count(*) from public.{t}")   # noqa: S608
                counts[t] = fetch_one(cur)[0]
    return counts


def shared_ids(stg_conn: str, prod_conn: str, tables) -> dict[str, int]:
    """How many ids exist in BOTH environments, per table.

    Identity is the whole test. A staging row that carries a production id is a
    copy of a production record however it arrived; a staging row with its own
    id is a fixture, which the environment contract permits. Only ids cross this
    function, and only a count leaves it.
    """
    result: dict[str, int] = {}
    with psycopg.connect(stg_conn, autocommit=True) as sconn, \
         psycopg.connect(prod_conn, autocommit=True) as pconn:
        pconn.execute("set default_transaction_read_only = on")
        for t in tables:
            with sconn.cursor() as scur:
                scur.execute("select to_regclass(%s)", (f"public.{t}",))
                if fetch_one(scur)[0] is None:
                    continue
                scur.execute(f"select id from public.{t}")        # noqa: S608
                ids = [r[0] for r in scur.fetchall()]
            if not ids:
                result[t] = 0
                continue
            with pconn.cursor() as pcur:
                pcur.execute("select to_regclass(%s)", (f"public.{t}",))
                if fetch_one(pcur)[0] is None:
                    continue
                pcur.execute(
                    f"select count(*) from public.{t} where id = any(%s)",  # noqa: S608
                    (ids,))
                result[t] = fetch_one(pcur)[0]
    return result


def staging_worker_config() -> dict:
    with open(REPO / "mcp-server" / "wrangler.toml", "rb") as fh:
        return tomllib.load(fh)


def main() -> int:
    print("p1-environment-gate: staging cannot reach production data or credentials")

    prod_dsn = db_tap.dsn(project="production")
    stg_dsn = db_tap.dsn(project="staging")

    # ── 1. two projects, not two branches ────────────────────────────────────
    prod_host, stg_host = host_of(prod_dsn), host_of(stg_dsn)
    check("1. staging and production are different database hosts",
          prod_host != stg_host,
          f"both answer on {prod_host}")

    prod_project = db_tap.PROJECTS["production"]
    stg_project = db_tap.PROJECTS["staging"]
    check("1b. they are different Neon PROJECTS, not branches of one",
          prod_project.get("id") != stg_project.get("id")
          and prod_project.get("name") != stg_project.get("name"),
          "the project map points both environments at one project")

    # ── 2. the credentials are not the same credential ───────────────────────
    check("2. the two connection strings are different credentials",
          digest(prod_dsn) != digest(stg_dsn),
          "identical credential digests")

    # ── 3. staging holds no PRODUCTION record ────────────────────────────────
    # THE FIRST VERSION OF THIS ASSERTION DEMANDED ZERO ROWS, and failed on its
    # first run against a row named "Throwaway Repro Org" — a synthetic fixture
    # from a 2026-08-14 reproduction. The assertion was wrong, not the state:
    # the environment contract defines staging's data as "sanitized
    # representative fixtures or dedicated test data", so an empty-table test
    # asks for something the contract never required and would fail every time
    # anyone rehearsed anything.
    #
    # The precise question is whether any row in staging IS a production row,
    # and identity answers it exactly. Ids are compared, never names, emails or
    # phones — an overlap is reported as a COUNT, so a leak is detectable
    # without this gate reading, printing or copying one field of client data.
    stg_counts = table_counts(stg_dsn, CLIENT_TABLES, read_only=False)
    prod_counts = table_counts(prod_dsn, CLIENT_TABLES, read_only=True)

    overlaps = shared_ids(stg_dsn, prod_dsn, CLIENT_TABLES)
    leaked = {t: n for t, n in overlaps.items() if n > 0}
    check("3. no row in staging shares an id with a row in production",
          not leaked,
          "PRODUCTION ROWS PRESENT IN STAGING: "
          + ", ".join(f"{t}={n}" for t, n in leaked.items()) if leaked else "")

    # The control: if production is ALSO empty, assertion 3 proved nothing.
    check("3b. production is not empty, so the comparison means something",
          any(n > 0 for n in prod_counts.values()),
          "no client table in production has rows — is this really production?")

    # Not an assertion: staging's own fixture volume, printed so growth is
    # visible. A staging database quietly filling up is worth seeing before it
    # becomes the thing somebody mistakes for representative data.
    fixtures = {t: n for t, n in stg_counts.items() if n > 0}
    print(f"        staging fixtures: "
          + (", ".join(f"{t}={n}" for t, n in fixtures.items()) if fixtures else "none"))

    # ── 4. the staging Worker shares no binding ──────────────────────────────
    cfg = staging_worker_config()
    stg_env = cfg.get("env", {}).get("staging", {})
    check("4a. staging deploys a different Worker name",
          stg_env.get("name") and stg_env["name"] != cfg.get("name"),
          f"staging name {stg_env.get('name')!r} vs production {cfg.get('name')!r}")

    prod_kv = {k.get("id") for k in cfg.get("kv_namespaces", [])}
    stg_kv = {k.get("id") for k in stg_env.get("kv_namespaces", [])}
    check("4b. staging has its own KV namespace",
          bool(stg_kv) and not (stg_kv & prod_kv),
          f"shared namespace id(s): {stg_kv & prod_kv}" if (stg_kv & prod_kv) else
          "staging declares no KV namespace of its own")

    stg_routes = stg_env.get("routes", []) or []
    check("4c. staging claims no route and no custom domain",
          not stg_routes,
          f"staging declares {len(stg_routes)} route(s) — one typo from production DNS")

    # ── 5 and 6. both environments are current with the repository ───────────
    committed = committed_migrations()
    for label, conn_string, read_only, number in (
        ("staging", stg_dsn, False, "5"),
        ("production", prod_dsn, True, "6"),
    ):
        applied = applied_migrations(conn_string, read_only)
        missing = [m for m in committed if m not in applied]
        check(f"{number}. {label} has every committed migration applied",
              not missing,
              f"{len(missing)} unapplied: " + ", ".join(missing[:4])
              + (" …" if len(missing) > 4 else ""))

    print()
    if FAILURES:
        print(f"p1-environment-gate: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("p1-environment-gate: G1 isolation holds, and both environments are current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
