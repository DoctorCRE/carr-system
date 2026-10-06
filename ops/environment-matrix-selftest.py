#!/usr/bin/env python3
"""
environment-matrix-selftest.py — the environment matrix has to match the repo.

WHAT P0-2 ASKS FOR: "Local/Rehearsal/Staging/Production matrix with isolated
identities, Neon, KV/R2, domains, secrets, and telemetry", and a "drift report
[that] names every intentional difference." The matrix is now written down in
ops/config/environments.json. A written matrix that nothing checks is a diagram,
so this is the half that makes it a control.

THE RULE THE FILE STATES AND THIS ENFORCES: every difference between two
environments is either INTENTIONAL and named, or it is drift. There is no third
category. An unlisted difference fails here rather than being noticed later by
somebody wondering why staging behaves oddly.

WHY THIS RUNS IN CI RATHER THAN NIGHTLY. It compares two things that both live
in the repository — the matrix and mcp-server/wrangler.toml plus the project map
in tools/db-tap.py — so it needs no credential and no network, and ops/ci.sh
picks it up automatically as a *-selftest.py. The nightly gates
(p1-environment-gate, p1-rebuild-gate) ask the live environments the questions
that need live answers. These are the questions a change can be checked against
before it merges.

THE ASSERTIONS.

  1. ALL FOUR ENVIRONMENTS ARE DECLARED. The Program 0 inventory found local
     "implicit and defined nowhere" and development non-existent; a matrix
     missing an environment is how that state persisted.
  2. EVERY DECLARED WORKER MATCHES wrangler.toml, by name, for the environments
     that deploy one — and the ones that do not deploy declare no name.
  3. PRODUCTION IS PINNED BY ID AND STAGING IS RESOLVED BY NAME, matching
     tools/db-tap.py. Reversing these is the single worst mistake the project
     map could make, so the matrix and the code have to agree out loud.
  4. DECLARED DOMAINS MATCH THE WORKER CONFIG, in both directions. A domain in
     the matrix that wrangler does not serve is fiction; a route wrangler serves
     that the matrix does not name is an undeclared production surface.
  5. EVERY OBSERVED STAGING-VS-PRODUCTION BINDING DIFFERENCE IS NAMED. Matched
     by keyword against intentional_differences, which is deliberately a loose
     match: the point is that a human wrote a sentence about it, not that the
     sentence has a particular shape.
"""

import json
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MATRIX = REPO / "ops" / "config" / "environments.json"
WRANGLER = REPO / "mcp-server" / "wrangler.toml"
DB_TAP = REPO / "tools" / "db-tap.py"

REQUIRED = {"local", "rehearsal", "staging", "production"}

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def named(differences: list[dict], *keywords: str) -> bool:
    """Is this difference described by SOME entry? Loose by design."""
    blob = " ".join((d.get("difference", "") + " " + d.get("why", "")).lower()
                    for d in differences)
    return all(k.lower() in blob for k in keywords)


def main() -> int:
    print("environment-matrix-selftest: the matrix matches the repository")

    matrix = json.loads(MATRIX.read_text())
    envs = {e["name"]: e for e in matrix["environments"]}
    differences = matrix.get("intentional_differences", [])

    with open(WRANGLER, "rb") as fh:
        wrangler = tomllib.load(fh)
    stg_cfg = wrangler.get("env", {}).get("staging", {})

    # ── 1 ────────────────────────────────────────────────────────────────────
    check("1. all four environments are declared",
          REQUIRED <= set(envs),
          "missing: " + ", ".join(sorted(REQUIRED - set(envs))))

    # ── 2 ────────────────────────────────────────────────────────────────────
    check("2a. production's Worker name matches wrangler.toml",
          envs["production"]["worker"].get("name") == wrangler.get("name"),
          f"matrix {envs['production']['worker'].get('name')!r} "
          f"vs wrangler {wrangler.get('name')!r}")
    check("2b. staging's Worker name matches [env.staging]",
          envs["staging"]["worker"].get("name") == stg_cfg.get("name"),
          f"matrix {envs['staging']['worker'].get('name')!r} "
          f"vs wrangler {stg_cfg.get('name')!r}")
    check("2c. local and rehearsal declare no Worker",
          not envs["local"]["worker"].get("deployed")
          and not envs["rehearsal"]["worker"].get("deployed"))

    # ── 3 ────────────────────────────────────────────────────────────────────
    db_tap_src = DB_TAP.read_text()
    check("3a. the matrix says production is pinned by id, and db-tap pins it",
          "PINNED BY ID" in envs["production"]["database"]["resolution"].upper()
          and '"production": {"id"' in db_tap_src.replace("'", '"'),
          "the matrix and tools/db-tap.py disagree about how production resolves")
    check("3b. the matrix says staging resolves by name, and db-tap resolves it by name",
          "by name" in envs["staging"]["database"]["resolution"].lower()
          and '"staging":' in db_tap_src.replace("'", '"')
          and "_project_id_by_name" in db_tap_src)

    # ── 4 ────────────────────────────────────────────────────────────────────
    declared_prod = set(envs["production"]["domains"])
    served_prod = {r["pattern"] for r in wrangler.get("routes", [])
                   if isinstance(r, dict) and r.get("pattern")}
    check("4a. every domain the matrix claims for production is served by the Worker",
          declared_prod <= served_prod,
          "claimed but not served: " + ", ".join(sorted(declared_prod - served_prod)))
    check("4b. every route the Worker serves is named in the matrix",
          served_prod <= declared_prod,
          "served but undeclared: " + ", ".join(sorted(served_prod - declared_prod)))
    product_domains = envs["production"].get("product_domains", [])
    product_hosts = {item.get("hostname") for item in product_domains if isinstance(item, dict)}
    check("4c. the DoctorCRE app host belongs to the independent product Worker",
          product_domains == [{
              "hostname": "app.doctorcre.com",
              "worker": "doctorcre-app",
              "repository": "jbookout/doctorcre-app",
          }] and not (product_hosts & served_prod),
          "app.doctorcre.com must be declared for doctorcre-app and absent from carr-mcp routes")
    check("4d. staging declares no domain, and wrangler gives it none",
          not envs["staging"]["domains"] and not stg_cfg.get("routes"))

    # ── 5 ────────────────────────────────────────────────────────────────────
    prod_kv = {k.get("id") for k in wrangler.get("kv_namespaces", [])}
    stg_kv = {k.get("id") for k in stg_cfg.get("kv_namespaces", [])}
    if prod_kv != stg_kv:
        check("5a. the KV difference between staging and production is named",
              named(differences, "kv"),
              "the namespaces differ and nothing in intentional_differences says why")

    prod_r2 = {b.get("bucket_name") for b in wrangler.get("r2_buckets", [])}
    stg_r2 = {b.get("bucket_name") for b in stg_cfg.get("r2_buckets", [])}
    if prod_r2 != stg_r2:
        check("5b. the R2 difference between staging and production is named",
              named(differences, "r2"),
              "production and staging differ on buckets with no stated reason")

    if declared_prod and not envs["staging"]["domains"]:
        check("5c. the domain difference is named",
              named(differences, "domain"),
              "production has domains and staging has none, unexplained")

    check("5d. every intentional difference states a reason",
          all(d.get("why") for d in differences),
          "an entry names a difference with no why")

    print()
    if FAILURES:
        print(f"environment-matrix-selftest: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("environment-matrix-selftest: the matrix and the repository agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
