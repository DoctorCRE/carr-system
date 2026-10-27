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
picks it up automatically as a *-selftest.py. These are the questions a change
can be checked against before it merges.

THE ASSERTIONS.

  1. ALL FOUR ENVIRONMENTS ARE DECLARED. The Program 0 inventory found local
     "implicit and defined nowhere" and development non-existent; a matrix
     missing an environment is how that state persisted.
  2. EVERY DECLARED WORKER MATCHES wrangler.toml, by name, for the environments
     that deploy one — and the ones that do not deploy declare no name.
  3. PRODUCTION IS PINNED BY ID AND STAGING IS RESOLVED BY NAME, matching
     tools/db-tap.py. Reversing these is the single worst mistake the project
     map could make, so the matrix and the code have to agree out loud.
  4. DECLARED DOMAINS MATCH THE WORKER CONFIG, in both directions, and
     [env.staging] carries an explicit `routes = []`. A domain in
     the matrix that wrangler does not serve is fiction; a route wrangler serves
     that the matrix does not name is an undeclared production surface.
  5. EVERY OBSERVED STAGING-VS-PRODUCTION BINDING DIFFERENCE IS NAMED. Matched
     by keyword against intentional_differences, which is deliberately a loose
     match: the point is that a human wrote a sentence about it, not that the
     sentence has a particular shape.
  6. ALL WORKERS CARRY A CPU CAP AND EXPLICIT LOG SAMPLING. Workers Paid has no spend ceiling, so the
     top-level `[limits] cpu_ms` is what bounds one invocation's CPU; staging
     inherits it, and an environment override may only lower it. Production
     samples at 0.1 and staging at 0.01, including any nested logs override.
  8. ONLY THE DAILY BUDGET WRAPPER MAY ACCESS THE AI BINDING. A parsed source
     check catches raw access, aliases, destructuring, and static computed names.
  9. PRODUCTION AND STAGING AI BUDGETS TOTAL LESS THAN THE SHARED FREE ALLOWANCE.
"""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MATRIX = REPO / "ops" / "config" / "environments.json"
WRANGLER = REPO / "mcp-server" / "wrangler.toml"
DB_TAP = REPO / "tools" / "db-tap.py"
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"

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


def workflow_jobs(text: str) -> dict[str, str]:
    """Each top-level job's body in deploy.yml, keyed by job id (text only)."""
    jobs: dict[str, str] = {}
    current = None
    for line in text.split("jobs:\n", 1)[1].splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            current = line.strip()[:-1]
            jobs[current] = ""
        elif current:
            jobs[current] += line + "\n"
    return jobs


def billing_failures(config: dict) -> list[str]:
    """Validate effective CPU caps and explicit sampling for every Worker."""
    failures = []
    production_cpu = config.get("limits", {}).get("cpu_ms")
    for name, worker in [("production", config), *config.get("env", {}).items()]:
        cpu = worker.get("limits", {}).get("cpu_ms", production_cpu)
        if type(cpu) is not int or not 0 < cpu < 30_000:
            failures.append(f"{name}: missing or unsafe cpu_ms")
        elif type(production_cpu) is not int or cpu > production_cpu:
            failures.append(f"{name}: cpu_ms exceeds production cap")
        rate = worker.get("observability", {}).get("head_sampling_rate")
        expected = {"production": 0.1, "staging": 0.01}.get(name)
        if type(rate) not in (int, float) or not 0 <= rate < 1 or (expected is not None and rate != expected):
            failures.append(f"{name}: missing or unsafe head_sampling_rate")
        logs_rate = worker.get("observability", {}).get("logs", {}).get("head_sampling_rate", rate)
        if type(logs_rate) not in (int, float) or not 0 <= logs_rate < 1 or (expected is not None and logs_rate != expected):
            failures.append(f"{name}: unsafe logs.head_sampling_rate override")
    return failures


def ai_budget_failures(config: dict) -> list[str]:
    """Keep the two isolated counters below the account's shared free allowance."""
    failures = []
    for name, worker, cap in (("production", config, 8000),
                              ("staging", config.get("env", {}).get("staging", {}), 1500)):
        value = worker.get("vars", {}).get("AI_DAILY_NEURON_BUDGET")
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or int(value) > cap:
            failures.append(f"{name}: missing or unsafe AI_DAILY_NEURON_BUDGET")
    return failures


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
          envs["staging"]["worker"].get("deployed")
          and envs["staging"]["worker"].get("name") == stg_cfg.get("name") == "carr-mcp-staging",
          f"matrix {envs['staging']['worker'].get('name')!r} "
          f"vs wrangler {stg_cfg.get('name')!r}")
    check("2c. local and rehearsal declare no Worker",
          not envs["local"]["worker"].get("deployed")
          and not envs["rehearsal"]["worker"].get("deployed"))

    jobs = workflow_jobs(DEPLOY.read_text())
    staging_job, production_job = jobs.get("staging", ""), jobs.get("deploy", "")
    check("2d. deploy.yml deploys staging in its own job, in the staging environment",
          "environment: staging" in staging_job and "--env staging" in staging_job
          and "STAGING_DATABASE_URL" in staging_job
          and "PRODUCTION_DATABASE_URL" not in staging_job)
    check("2e. staging never gates production, and production never gates staging",
          "needs:" not in staging_job and "needs:" not in production_job
          and "--env staging" not in production_job,
          "a `needs:` would let one environment's failure stop the other's deploy")

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
    # An OMITTED `routes` key inherits production's custom domains, which is
    # how a staging deploy took them over on 2026-08-13. Only an explicit empty
    # list is safe, so a missing key fails exactly like a filled one.
    check("4d. staging declares no domain, and wrangler gives it an explicit empty routes list",
          not envs["staging"]["domains"]
          and "routes" in stg_cfg and stg_cfg["routes"] == []
          and "route" not in stg_cfg and stg_cfg.get("workers_dev") is True,
          f"[env.staging] routes={stg_cfg.get('routes', '<missing: inherits production>')!r}")

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

    # ── 6 ────────────────────────────────────────────────────────────────────
    uptime_path = REPO / "mcp-server" / "wrangler.uptime.toml"
    with uptime_path.open("rb") as fh:
        uptime = tomllib.load(fh)
    billing = billing_failures(wrangler) + [f"uptime: {failure}" for failure in billing_failures(uptime)]
    check("6. every Worker has bounded CPU and explicit log sampling", not billing, "; ".join(billing))
    ai_boundary = subprocess.run(
        ["node", str(REPO / "mcp-server/check-ai-boundary.mjs")],
        capture_output=True, text=True, cwd=REPO)
    check("8. only the daily budget wrapper accesses Workers AI", ai_boundary.returncode == 0,
          ai_boundary.stdout + ai_boundary.stderr)
    ai_budget = ai_budget_failures(wrangler)
    check("9. AI budgets stay below the shared free allowance", not ai_budget, "; ".join(ai_budget))

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
