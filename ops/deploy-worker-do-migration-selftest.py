#!/usr/bin/env python3
"""deploy-worker-do-migration-selftest.py — a pending Durable Object migration
ships unattended, and every doubt about it ships nothing.

WHAT THIS GUARDS. bin/deploy-worker.sh --upload-version runs `wrangler versions
upload`, which wrangler 4.137 refuses while the Worker has a pending Durable
Object migration. The wrapper's do-migration block reads the applied tag,
checks Production's attachments, prechecks staging, applies a pending tag with
the documented `wrangler deploy`, records the move, verifies it, and makes the
migration deploy's own version the release candidate.

HOW. The house pattern for this wrapper (ops/deploy-release-wiring-selftest.py):
the whole script cannot run hermetically (exact origin/main, a database, real
credentials), so the exact block between its BEGIN/END do-migration markers AND
the upload branch's own candidate fragment are extracted VERBATIM and executed
against a fake `wrangler`, a fake `curl` and a fake `tools/ops-record.py` that
model the real ones: the fake `versions upload` refuses exactly as wrangler
4.137 does while a migration is pending, so reaching it proves the ordering.
The real ops/worker-do-migration.py, ops/verify-worker-release.py,
ops/deploy-attachment-check.py and `ops-record.py staging-target` judge.

Scenarios:
  A  no migration declared / A2 declared and applied -> the ordinary upload
  B  pending -> Production attachments, staging precheck, deploy of the exact
     SHA, ops.settings_change record, read-back, and the deploy's version is
     the candidate (no second upload); B2 a later tag; B3 the probe secret
  S  the staging precheck refuses before Production is touched, in every way
     it can fail, with correct receipt fields
  K  staging already carries the tag: only a durable receipt with the same
     steps digest admits it
  T  Production attachments a plain deploy would change: refused
  C  Production's applied tag unknown: refused before anything
  D  the Production deploy fails, half-succeeds or cannot be read back, and the
     database record fails
  E  source wiring
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

GIT_ENV = fixture_env()  # every git call below targets a throwaway repo

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "deploy-worker.sh"
HEAD_SHA = "a" * 40
V0 = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
V1 = "0f1e2d3c-4b5a-4968-8776-655443322110"
VS = "5a5a5a5a-1b1b-4c2c-8d3d-4e4e4e4e4e4e"
TOKEN = "cf-selftest-token-9f8e7d6c5b4a"
TAG1 = "v1-workflow-census-anchor"
TAG2 = "v2-selftest-second-class"
PROD_RELEASE = "https://api.doctorcre.com/release"

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


FAKE_WRANGLER = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_STATE"]
state = json.load(open(state_path))
argv = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(["wrangler", *argv]) + "\n")
def save():
    json.dump(state, open(state_path, "w"))
if argv[:2] == ["auth", "token"]:
    if state.get("auth_fail"):
        print("Not logged in.", file=sys.stderr); sys.exit(1)
    print(json.dumps({"type": "api_token", "token": os.environ["FAKE_TOKEN"]}, indent=2)); sys.exit(0)
if argv[:1] == ["deploy"] and argv[1:3] == ["--env", "staging"]:
    mode = state.get("staging_deploy_mode", "ok")
    sha = next(a.split(":", 1)[1] for a in argv if a.startswith("GIT_SHA:"))
    state["staging_deployed"] = True
    if mode == "fail":
        save(); print("X [ERROR] staging deploy failed"); sys.exit(1)
    if mode != "no_tag_move":
        state["staging_applied_tag"] = state["latest_tag"]
    state["staging_served_sha"] = sha
    state["staging_served_version"] = os.environ["FAKE_VS"]
    save()
    print("Uploaded carr-mcp-staging")
    if mode != "no_version":
        print("Current Version ID: " + os.environ["FAKE_VS"])
    if mode == "fail_after_apply":
        print("X [ERROR] staging route sync failed"); sys.exit(1)
    sys.exit(0)
if argv[:1] == ["deploy"]:
    mode = state.get("deploy_mode", "ok")
    sha = next(a.split(":", 1)[1] for a in argv if a.startswith("GIT_SHA:"))
    state["prod_deployed"] = True
    if mode == "fail_before_apply":
        save(); print("X [ERROR] A request to the Cloudflare API failed."); sys.exit(1)
    if mode != "ok_no_tag_move":
        state["applied_tag"] = state["latest_tag"]
    state["served_sha"] = sha
    state["served_version"] = os.environ["FAKE_V0"]
    save()
    print("Uploaded carr-mcp")
    if mode != "no_version":
        print("Current Version ID: " + os.environ["FAKE_V0"])
    if mode == "fail_after_apply":
        print("X [ERROR] custom domain sync failed"); sys.exit(1)
    sys.exit(0)
if argv[:2] == ["versions", "upload"]:
    if state.get("latest_tag") and state.get("applied_tag") != state["latest_tag"]:
        print("X [ERROR] This Worker has a pending Durable Object migration, which cannot be "
              "applied by `wrangler versions upload`."); sys.exit(1)
    print("Worker Version ID: " + os.environ["FAKE_V1"]); sys.exit(0)
print("fake wrangler: unexpected " + " ".join(argv), file=sys.stderr); sys.exit(2)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
state = json.load(open(os.environ["FAKE_STATE"]))
argv = sys.argv[1:]
stdin = sys.stdin.read() if "--config" in argv else ""
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(["curl", *argv]) + "\n")
url = next(a for a in argv if a.startswith("https://"))
out = argv[argv.index("-o") + 1] if "-o" in argv else None
authed = f"Authorization: Bearer {os.environ['FAKE_TOKEN']}" in stdin
if "/workers/services/" in url:
    if not authed:
        sys.exit(22)
    staging = url.endswith("/services/carr-mcp-staging")
    mode = state.get("staging_services_mode" if staging else "services_mode", "ok")
    deployed = state.get("staging_deployed" if staging else "prod_deployed")
    if mode == "http_error" or (mode == "fail_after_deploy" and deployed):
        sys.exit(22)
    if mode == "malformed":
        body = "<html>not json</html>"
    else:
        name = "carr-mcp-staging" if staging else state.get("script_id", "carr-mcp")
        script = {"id": name, "etag": "e"}
        applied = state.get("staging_applied_tag") if staging else state.get("applied_tag")
        if applied is not None:
            script["migration_tag"] = applied
        body = json.dumps({"success": True, "errors": [], "result": {
            "id": name, "default_environment": {"environment": "production", "script": script}}})
    open(out, "w").write(body)
    sys.exit(0)
if "/workers/domains?service=" in url:
    if not authed or state.get("domains_mode") == "http_error":
        sys.exit(22)
    rows = [{"id": f"d{i}", "hostname": h, "service": state.get("domain_service", "carr-mcp"),
             "environment": "production", "zone_id": "z", "zone_name": "example.test"}
            for i, h in enumerate(state["live_domains"])]
    total = len(rows) + (1 if state.get("domains_mode") == "paginated" else 0)
    open(out, "w").write(json.dumps({"success": True, "errors": [], "result": rows,
        "result_info": {"page": 1, "per_page": len(rows), "count": len(rows), "total_count": total}}))
    sys.exit(0)
if url.endswith("/workers/scripts/carr-mcp/subdomain"):
    if not authed:
        sys.exit(22)
    open(out, "w").write(json.dumps({"success": True, "errors": [], "result": {
        "enabled": state.get("live_workers_dev", False), "previews_enabled": False}}))
    sys.exit(0)
if url.endswith("/release") and "carr-mcp-staging" in url:
    if state.get("staging_served_sha") is None:
        sys.exit(22)
    print(json.dumps({"ok": True, "env": {"value": "staging"},
                      "git_sha": {"value": "c" * 40 if state.get("staging_stale_readback") else state["staging_served_sha"]},
                      "provider": "cloudflare-workers",
                      "worker_version": {"id": state["staging_served_version"]},
                      "program6_actions": {"enabled": True, "posture": "enabled", "reason": None},
                      "schema": {"highest_applied_migration": "0590_selftest.sql", "applied_count": 5}}))
    sys.exit(0)
if url.endswith("/release"):
    if state.get("served_sha") is None:
        sys.exit(22)
    print(json.dumps({"ok": True, "env": {"value": "production"},
                      "git_sha": {"value": "b" * 40 if state.get("stale_readback") else state["served_sha"]},
                      "provider": "cloudflare-workers",
                      "worker_version": {"id": state["served_version"]},
                      "program6_actions": {"enabled": True, "posture": "enabled", "reason": None},
                      "schema": {"highest_applied_migration": "0590_selftest.sql", "applied_count": 5}}))
    sys.exit(0)
sys.exit(6)
'''

FAKE_OPS_RECORD = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(["ops-record", *argv]) + "\n")
if argv[:1] == ["settings-change"]:
    state = json.load(open(os.environ["FAKE_STATE"]))
    if state.get("settings_change_fail"):
        print("ops-record: could not record the settings change", file=sys.stderr); sys.exit(1)
    print("00000000-0000-4000-8000-000000000001"); sys.exit(0)
if argv[:1] == ["staging-target"]:
    os.execv(sys.executable, [sys.executable, os.environ["REAL_OPS_RECORD"], *argv])
print("fake ops-record: unexpected " + " ".join(argv), file=sys.stderr); sys.exit(2)
'''


FAKE_RELEASE_MANIFEST = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(["release-manifest", *argv]) + "\n")
if argv[:1] == ["bind-provider"]:
    state = json.load(open(os.environ["FAKE_STATE"]))
    if state.get("bind_fail"):
        print("release-manifest: bind-provider refused", file=sys.stderr); sys.exit(1)
    print("{}"); sys.exit(0)
print("fake release-manifest: unexpected " + " ".join(argv), file=sys.stderr); sys.exit(2)
'''


def extract_block(source: str) -> str:
    start = source.index("# ---------- pending Durable Object migration (BEGIN do-migration block)")
    end = source.index("# ---------- (END do-migration block) ----------", start)
    return source[start:end]


def extract_upload_fragment(source: str) -> str:
    start = source.index("  # A pending Durable Object migration would make `versions upload` refuse;")
    marker = ("fail \"the uploaded version could not be bound into its release manifest; "
              "$DO_TRAFFIC_CLAUSE.\"\n  fi\n")
    return source[start:source.index(marker, start) + len(marker)]


def wrangler_toml(tags: list[str], *, classes: str = "C", staging_routes: bool = True,
                  staging_migrations: list[tuple[str, str]] | None = None) -> str:
    base = (REPO / "mcp-server" / "wrangler.toml").read_text(encoding="utf-8")
    # The fixture starts from the real file, minus any migrations it may carry
    # by the time this runs, and declares exactly the tags each scenario needs.
    base = re.sub(r"(?m)^\[\[migrations\]\]\n(?:[^\[\n].*\n|\n)*", "", base)
    if not staging_routes:
        assert base.count("\nroutes = []\n") == 1
        base = base.replace("\nroutes = []\n", "\n")
    extra = "".join(f'\n[[migrations]]\ntag = "{t}"\nnew_sqlite_classes = ["{classes}{i}"]\n'
                    for i, t in enumerate(tags))
    for tag, cls in staging_migrations or []:
        extra += f'\n[[env.staging.migrations]]\ntag = "{tag}"\nnew_sqlite_classes = ["{cls}"]\n'
    return base + extra


def declared_domains(toml_text: str) -> list[str]:
    return [r["pattern"] for r in tomllib.loads(toml_text).get("routes", [])]


def expected_digest(toml_text: str) -> str:
    migrations = tomllib.loads(toml_text).get("migrations", [])
    canonical = json.dumps(migrations, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def run(source: str, *, tags: list[str], state: dict, toml: str | None = None,
        state_dir: Path | None = None, locate: str = "override", env_extra: dict | None = None,
        block_receipt: str | None = None) -> dict:
    block, fragment = extract_block(source), extract_upload_fragment(source)
    toml = toml if toml is not None else wrangler_toml(tags)
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "bin").mkdir()
        (tmp / "worker").mkdir()
        if locate == "worktree":
            # The pipeline's REPO is a linked release worktree it deletes after
            # the run; the durable receipts must land beside the MAIN checkout.
            git = ["git", "-c", "user.name=selftest", "-c", "user.email=selftest@example.test"]
            subprocess.run([*git, "init", "-q", str(tmp / "main")], check=True, env=GIT_ENV)
            subprocess.run([*git, "-C", str(tmp / "main"), "commit", "-q", "--allow-empty", "-m", "init"],
                           check=True, env=GIT_ENV)
            subprocess.run([*git, "-C", str(tmp / "main"), "worktree", "add", "-q", "--detach",
                            str(tmp / "repo")], check=True, env=GIT_ENV)
        (tmp / "repo" / "tools").mkdir(parents=True)
        (tmp / "repo" / "ops").symlink_to(REPO / "ops")
        (tmp / "repo" / "lib").symlink_to(REPO / "lib")
        (tmp / "worker" / "wrangler.toml").write_text(toml, encoding="utf-8")
        for path, body in ((tmp / "bin" / "wrangler", FAKE_WRANGLER), (tmp / "bin" / "curl", FAKE_CURL),
                           (tmp / "repo" / "tools" / "ops-record.py", FAKE_OPS_RECORD),
                           (tmp / "repo" / "tools" / "release-manifest.py", FAKE_RELEASE_MANIFEST)):
            path.write_text(body.replace("/usr/bin/env python3", sys.executable, 1), encoding="utf-8")
            path.chmod(0o755)
        tag_dir = state_dir if state_dir is not None else tmp / "tag-receipts"
        if block_receipt is not None:
            # a directory where the receipt file must go: the write cannot land
            (tag_dir / block_receipt).mkdir(parents=True)
        state = {"latest_tag": tags[-1] if tags else None, "live_domains": declared_domains(toml), **state}
        (tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")
        calls = tmp / "calls.jsonl"
        calls.write_text("", encoding="utf-8")
        harness = tmp / "harness.sh"
        harness.write_text(
            "#!/bin/sh\nset -eu\n"
            'fail() { echo ""; echo "REFUSED: $1" >&2; echo "" >&2; exit 1; }\n'
            'PROBE_TOKENS_FILE="${PROBE_TOKENS_FILE:-}"\n'
            + block + fragment +
            'echo "  provider version: $PROVIDER_VERSION_ID"\n'
            'echo "UPLOAD-REACHED evidence=${DO_MIGRATION_EVIDENCE:-}"\n',
            encoding="utf-8")
        env = {
            "PATH": f"{tmp / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(tmp), "TMPDIR": str(tmp),
            "REPO": str(tmp / "repo"), "WORKER_DIR": str(tmp / "worker"),
            "WRANGLER": str(tmp / "bin" / "wrangler"), "PY": sys.executable,
            "TARGET_ENV": "production", "HEAD_SHA": HEAD_SHA, "PROVIDER": "cloudflare-workers",
            "CANDIDATE_MANIFEST": "{}", "CANDIDATE_MANIFEST_DIGEST": "sha256:" + "0" * 64,
            "EXPECTED_PROGRAM6_ACTIONS": "enabled",
            "EXPECTED_SCHEMA_HIGHEST_MIGRATION": "0590_selftest.sql",
            "EXPECTED_SCHEMA_APPLIED_COUNT": "5",
            "CARR_READBACK_ATTEMPTS": "2", "CARR_READBACK_SLEEP": "0",
            "FAKE_STATE": str(tmp / "state.json"), "FAKE_CALLS": str(calls),
            "FAKE_TOKEN": TOKEN, "FAKE_V0": V0, "FAKE_V1": V1, "FAKE_VS": VS,
            "REAL_OPS_RECORD": str(REPO / "tools" / "ops-record.py"),
            "RELEASE_MANIFEST": str(tmp / "manifest.json"),
            "FOUNDATION_ASSURANCE_STAGING_PROVIDER": "",
            "GIT_CEILING_DIRECTORIES": str(tmp),
            **(env_extra or {}),
        }
        if locate == "override":
            env["CARR_DO_MIGRATION_STATE_DIR"] = str(tag_dir)
        done = subprocess.run(["sh", str(harness)], env=env, capture_output=True, text=True,
                              timeout=180)
        receipt_path = tmp / "repo" / "out" / "deploy-worker" / f"do-migration-{HEAD_SHA}.json"
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        receipt_check = None
        if receipt is not None:
            receipt_check = subprocess.run(
                [sys.executable, str(REPO / "ops" / "worker-do-migration.py"), "receipt",
                 "--file", str(receipt_path), "--sha", HEAD_SHA],
                capture_output=True, text=True).returncode
        tag_receipts = {}
        for where in (tag_dir, tmp / "repo" / "out" / "deploy-worker" / "do-migration-tags",
                      tmp / "main" / "out" / "deploy-worker" / "do-migration-tags"):
            if where.is_dir():
                for f in (f for f in where.glob("*.json") if f.is_file()):
                    tag_receipts[str(f.relative_to(tmp)) if f.is_relative_to(tmp) else f.name] = \
                        json.loads(f.read_text())
        rows = [json.loads(line) for line in calls.read_text().splitlines() if line.strip()]
        return {"rc": done.returncode, "out": done.stdout, "err": done.stderr,
                "all": done.stdout + done.stderr, "calls": rows, "receipt": receipt,
                "receipt_valid": receipt_check == 0, "tag_receipts": tag_receipts}


def wrangler_calls(res: dict, *prefix: str) -> list[list[str]]:
    return [c[1:] for c in res["calls"] if c[0] == "wrangler" and c[1:1 + len(prefix)] == list(prefix)]


def prod_deploys(res: dict) -> list[list[str]]:
    return [c for c in wrangler_calls(res, "deploy") if c[1:3] != ["--env", "staging"]]


def staging_deploys(res: dict) -> list[list[str]]:
    return wrangler_calls(res, "deploy", "--env", "staging")


def services_reads(res: dict) -> int:
    return sum(1 for c in res["calls"] if c[0] == "curl" and any("/workers/services/" in a for a in c))


def attachment_reads(res: dict) -> int:
    return sum(1 for c in res["calls"] if c[0] == "curl"
               and any("/workers/domains?" in a or a.endswith("/subdomain") for a in c))


def settings_changes(res: dict) -> list[list[str]]:
    return [c[2:] for c in res["calls"] if c[0] == "ops-record" and c[1:2] == ["settings-change"]]


def arg(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


def index_of(res: dict, pred) -> int | None:
    return next((i for i, c in enumerate(res["calls"]) if pred(c)), None)


def ordered(positions: list[int | None]) -> bool:
    present = [p for p in positions if p is not None]
    return len(present) == len(positions) and present == sorted(present) and len(set(present)) == len(present)


def token_in_argv(res: dict) -> bool:
    return any(TOKEN in a for c in res["calls"] for a in c)


def production_untouched(res: dict) -> bool:
    return (not prod_deploys(res) and not wrangler_calls(res, "versions")
            and not settings_changes(res)
            and not any(c[0] == "curl" and PROD_RELEASE in c for c in res["calls"])
            and "provider version:" not in res["out"])


def main() -> int:
    print("deploy-worker-do-migration-selftest: a pending DO migration ships; every doubt ships nothing")
    source = SCRIPT.read_text(encoding="utf-8")
    baseline = tomllib.loads((REPO / "mcp-server" / "wrangler.toml").read_text(encoding="utf-8"))
    fixture = tomllib.loads(wrangler_toml([]))
    check("fixture removes production migrations without discarding subsequent environment tables",
          fixture.get("migrations", []) == [] and fixture.get("env") == baseline.get("env"))

    # A. current main: no [[migrations]] at all
    res = run(source, tags=[], state={})
    check("A. no declared migration: the ordinary upload is the candidate", res["rc"] == 0
          and f"Worker Version ID: {V1}" in res["out"] and f"provider version: {V1}" in res["out"],
          res["all"][-600:])
    check("A. no declared migration: no metadata or attachment read, no deploy, no record",
          services_reads(res) == 0 and attachment_reads(res) == 0 and not wrangler_calls(res, "deploy")
          and not settings_changes(res) and res["receipt"] is None)

    # A2. declared and already applied
    res = run(source, tags=[TAG1], state={"applied_tag": TAG1})
    check("A2. applied == declared latest: the ordinary upload is the candidate",
          res["rc"] == 0 and f"provider version: {V1}" in res["out"]
          and "UPLOAD-REACHED evidence=\n" in res["out"] + "\n", res["all"][-600:])
    check("A2. the applied tag was READ, and nothing else happened",
          services_reads(res) == 1 and attachment_reads(res) == 0 and not wrangler_calls(res, "deploy")
          and res["receipt"] is None and "DO migration" not in res["out"])

    # B. pending (the first release after #1244: nothing applied yet)
    toml = wrangler_toml([TAG1])
    res = run(source, tags=[TAG1], state={"applied_tag": None}, toml=toml)
    deploys = prod_deploys(res)
    check("B. pending: exactly one Production deploy and NO second upload",
          res["rc"] == 0 and len(deploys) == 1 and not wrangler_calls(res, "versions"),
          res["all"][-900:])
    check("B. the migration deploy's own version is the release candidate",
          f"provider version: {V0}" in res["out"] and V1 not in res["out"])
    check("B. the deploy carries this SHA's stamps and names the tag",
          bool(deploys) and f"GIT_SHA:{HEAD_SHA}" in deploys[0]
          and any(a.startswith("CANDIDATE_MANIFEST_DIGEST:") for a in deploys[0])
          and f"carr do-migration {TAG1} {HEAD_SHA}" in deploys[0]
          and "--env" not in deploys[0] and "--secrets-file" not in deploys[0])
    att_at = index_of(res, lambda c: c[0] == "curl" and any("/workers/domains?" in a for a in c))
    stg_at = index_of(res, lambda c: c[:4] == ["wrangler", "deploy", "--env", "staging"])
    stg_rel = index_of(res, lambda c: c[0] == "curl" and any("carr-mcp-staging" in a and a.endswith("/release") for a in c))
    prod_at = index_of(res, lambda c: c[:2] == ["wrangler", "deploy"] and c[2:4] != ["--env", "staging"])
    rec_at = index_of(res, lambda c: c[:2] == ["ops-record", "settings-change"])
    prod_rel = index_of(res, lambda c: c[0] == "curl" and PROD_RELEASE in c)
    check("B. order: attachments, staging deploy, staging read-back, Production deploy, DB record, read-back",
          ordered([att_at, stg_at, stg_rel, prod_at, rec_at, prod_rel]) and services_reads(res) == 4,
          str([att_at, stg_at, stg_rel, prod_at, rec_at, prod_rel, services_reads(res)]))
    check("B. staging gets the same SHA", stg_at is not None and f"GIT_SHA:{HEAD_SHA}" in res["calls"][stg_at])
    check("B. the marker line names tag, prior tag and version",
          f"DO migration applied: tag={TAG1} from=none version={V0}" in res["out"])
    changes = settings_changes(res)
    check("B. the Production move is written to ops.settings_change the moment the deploy returns",
          len(changes) == 1 and arg(changes[0], "--kind") == "worker-do-migration"
          and arg(changes[0], "--outcome") == "applied"
          and arg(changes[0], "--target") == "cloudflare-workers:carr-mcp:production"
          and arg(changes[0], "--environment") == "production"
          and TAG1 in (arg(changes[0], "--reason") or "") and V0 in (arg(changes[0], "--reason") or ""),
          json.dumps(changes))
    rc = res["receipt"] or {}
    sp = rc.get("staging_precheck", {})
    check("B. receipt records the applied, verified migration and its steps",
          rc.get("state") == "applied_verified" and rc.get("new_tag") == TAG1
          and rc.get("old_tag") is None and rc.get("migration_version_id") == V0
          and rc.get("deploy_exit") == 0 and rc.get("readback") == "identity-ok"
          and rc.get("steps_digest") == expected_digest(toml) and rc.get("db_record") == "recorded"
          and rc.get("refusal") is None and res["receipt_valid"], json.dumps(rc))
    check("B. receipt records the staging precheck",
          sp.get("state") == "passed" and sp.get("tag_moved") is True and sp.get("deploy_exit") == 0
          and sp.get("version_id") == VS and sp.get("old_tag") is None, json.dumps(sp))
    tr: dict = next(iter(res["tag_receipts"].values()), {})
    check("B. a durable staging tag receipt holds the applied steps digest",
          len(res["tag_receipts"]) == 1 and tr.get("script") == "carr-mcp-staging"
          and tr.get("tag") == TAG1 and tr.get("steps_digest") == expected_digest(toml)
          and tr.get("environment") == "staging" and tr.get("version_id") == VS
          and tr.get("git_sha") == HEAD_SHA, json.dumps(res["tag_receipts"]))
    check("B. deployment evidence carries do-migration=<tag>@<version>",
          f"UPLOAD-REACHED evidence=do-migration={TAG1}@{V0}" in res["out"])
    check("B. the bearer token never reached any argv", not token_in_argv(res))

    # B2. two tags, the first already applied
    res = run(source, tags=[TAG1, TAG2], state={"applied_tag": TAG1})
    check("B2. only the later tag is pending and applied",
          res["rc"] == 0 and f"pending: {TAG2} (applied: {TAG1};" in res["out"]
          and (res["receipt"] or {}).get("pending_tags") == [TAG2]
          and (res["receipt"] or {}).get("old_tag") == TAG1, res["all"][-600:])

    # B3. the probe-token secret the upload would carry rides on the migration deploy
    res = run(source, tags=[TAG1], state={"applied_tag": None},
              env_extra={"PROBE_TOKENS_FILE": "/private/probe-tokens.json"})
    deploys = prod_deploys(res)
    check("B3. --probe-tokens-file reaches the Production migration deploy, not staging",
          res["rc"] == 0 and len(deploys) == 1
          and arg(deploys[0], "--secrets-file") == "/private/probe-tokens.json"
          and all("--secrets-file" not in c for c in staging_deploys(res)), res["all"][-600:])

    # S. the staging precheck refuses before Production is touched
    staging_cases: list[tuple[str, dict, dict[str, Any], str, bool | None, int | None, bool]] = [
            ("staging deploy fails", {"staging_deploy_mode": "fail"}, {},
             "the staging deploy exited 1", None, 1, True),
            ("staging deploy exits 1 after applying", {"staging_deploy_mode": "fail_after_apply"}, {},
             "the staging deploy exited 1", None, 1, True),
            ("staging deploy prints no version", {"staging_deploy_mode": "no_version"}, {},
             "printed no version id", None, 0, True),
            ("staging tag does not move", {"staging_deploy_mode": "no_tag_move"}, {},
             f"did not move to {TAG1} (it reports none)", False, 0, True),
            ("staging re-read fails after its deploy", {"staging_services_mode": "fail_after_deploy"}, {},
             "could not be re-read after the deploy", None, 0, True),
            ("staging tag unknown before its deploy", {"staging_services_mode": "http_error"}, {},
             "could not be determined", None, None, False),
            ("staging read-back mismatch", {"staging_stale_readback": True}, {},
             "did not read back", True, 0, True),
            ("staging attachment check refuses", {}, {"toml": wrangler_toml([TAG1], staging_routes=False)},
             "the staging attachment check refused", None, None, False),
            ("staging declares another latest tag", {},
             {"toml": wrangler_toml([TAG1], staging_migrations=[(TAG2, "S0")])},
             "different latest migration tags", None, None, False),
            ("staging declares other steps for the same tag", {},
             {"toml": wrangler_toml([TAG1], staging_migrations=[(TAG1, "Other0")])},
             "different migration steps", None, None, False),
            ("durable receipt directory unlocatable", {}, {"locate": "none"},
             "could not be located", None, None, False),
            ("durable receipt cannot be written after staging applied the tag", {},
             {"block_receipt": f"carr-mcp-staging--{TAG1}.json"},
             "its durable receipt could not be written", True, 0, True)]
    for label, s_state, kwargs, why, moved, s_exit, s_deployed in staging_cases:
        res = run(source, tags=[TAG1], state={"applied_tag": None, **s_state}, **kwargs)
        check(f"S. {label}: refused, traffic not changed, Production untouched",
              res["rc"] == 1 and "staging precheck" in res["err"] and why in res["err"]
              and "Production traffic was not changed" in res["err"] and production_untouched(res)
              and bool(staging_deploys(res)) == s_deployed, res["all"][-700:])
        rc = res["receipt"] or {}
        sp = rc.get("staging_precheck", {})
        check(f"S. {label}: receipt not_applied, no Production exit, exact staging fields",
              rc.get("state") == "not_applied" and rc.get("migration_version_id") is None
              and rc.get("deploy_exit") is None and sp.get("state") == "failed"
              and why in (sp.get("reason") or "") and (rc.get("refusal") or "").startswith("staging precheck:")
              and sp.get("tag_moved") is moved and sp.get("deploy_exit") == s_exit
              and res["receipt_valid"] and "DO migration" not in res["out"], json.dumps(rc))

    # F. a failure AFTER the upload branch reports what traffic actually did
    res = run(source, tags=[TAG1], state={"applied_tag": None, "bind_fail": True})
    low = res["err"].lower()
    check("F1. a refusal after Production moved says Production ALREADY serves the migration version",
          res["rc"] == 1 and "could not be bound into its release manifest" in res["err"]
          and f"Production ALREADY serves {V0}" in res["err"] and "forward fix only" in res["err"]
          and len(prod_deploys(res)) == 1, res["all"][-700:])
    check("F1. ...and never tells the operator traffic was unchanged or Production untouched",
          "not changed" not in low and "untouched" not in low and "not touched" not in low,
          res["err"][-700:])
    res = run(source, tags=[], state={"bind_fail": True})
    check("F2. the same refusal with no migration says traffic was not changed (nothing moved)",
          res["rc"] == 1 and "could not be bound into its release manifest; traffic was not changed" in res["err"]
          and not wrangler_calls(res, "deploy") and "ALREADY" not in res["err"], res["all"][-700:])

    # H. a HAND-WRITTEN receipt must prove, from git history, the steps the tag was
    # applied with. A tag is applied only by the first deploy that carries it, so
    # the SHA serving staging now proves nothing if the tag's steps were edited.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        git = ["git", "-c", "user.name=selftest", "-c", "user.email=selftest@example.test", "-C", str(tmp / "r")]
        (tmp / "r" / "mcp-server").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(tmp / "r")], check=True, env=GIT_ENV)
        cfg = tmp / "r" / "mcp-server" / "wrangler.toml"
        head = '[env.staging]\nname = "carr-mcp-staging"\n'

        def commit(text: str, msg: str) -> str:
            cfg.write_text(text, encoding="utf-8")
            subprocess.run([*git, "add", "-A"], check=True, env=GIT_ENV)
            subprocess.run([*git, "commit", "-q", "-m", msg], check=True, env=GIT_ENV)
            return subprocess.run([*git, "rev-parse", "HEAD"], check=True, env=GIT_ENV,
                                  capture_output=True, text=True).stdout.strip()

        d1_text = head + f'\n[[migrations]]\ntag = "{TAG1}"\nnew_sqlite_classes = ["A"]\n'
        d2_text = head + f'\n[[migrations]]\ntag = "{TAG1}"\nnew_sqlite_classes = ["B"]\n'
        commit(head, "no migrations")
        c_intro = commit(d1_text, "introduce the tag with steps D1")
        c_same = commit(d1_text + "# an unrelated edit\n", "unrelated edit")
        d1, d2 = expected_digest(d1_text), expected_digest(d2_text)

        def hand_write(ref: str, digest: str, out: Path) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(REPO / "ops" / "worker-do-migration.py"), "tag-receipt", "write",
                 "--dir", str(out), "--script", "carr-mcp-staging", "--tag", TAG1, "--digest", digest,
                 "--sha", c_same, "--version-id", VS, "--environment", "staging",
                 "--history-repo", str(tmp / "r"), "--history-ref", ref],
                capture_output=True, text=True, env=GIT_ENV)

        done = hand_write(c_same, d1, tmp / "h1")
        row = json.loads(done.stdout) if done.returncode == 0 else {}
        proof = row.get("history_proof") or {}
        check("H1. unchanged since introduced: the hand-written receipt is written with its proof",
              done.returncode == 0 and proof.get("introduced_in") == c_intro
              and proof.get("commits_checked") == 2 and proof.get("ref_sha") == c_same
              and row.get("steps_digest") == d1, done.stdout + done.stderr)
        chk = subprocess.run([sys.executable, str(REPO / "ops" / "worker-do-migration.py"), "tag-receipt",
                              "check", "--dir", str(tmp / "h1"), "--script", "carr-mcp-staging",
                              "--tag", TAG1, "--digest", d1], capture_output=True, text=True)
        check("H1. ...and the wrapper's receipt check accepts it", chk.returncode == 0
              and json.loads(chk.stdout).get("match") is True, chk.stdout + chk.stderr)

        done = hand_write(c_same, d2, tmp / "h3")
        check("H3. a digest the history does not prove is refused and nothing is written",
              done.returncode == 4 and "not the digest the history proves" in done.stderr
              and not (tmp / "h3").exists(), done.stdout + done.stderr)

        # the reviewer's sequence: T applied with D1, then T's steps edited to D2
        c_edit = commit(d2_text, "fix forward edits the tag's steps in place")
        done = hand_write(c_edit, d2, tmp / "h2")
        check("H2. the tag's steps edited after introduction: refused (exit 4), no receipt, even though "
              "the current commit's digest matches",
              done.returncode == 4 and "changed at " + c_edit[:12] in done.stderr
              and not (tmp / "h2").exists(), done.stdout + done.stderr)

        done = subprocess.run(
            [sys.executable, str(REPO / "ops" / "worker-do-migration.py"), "tag-receipt", "write",
             "--dir", str(tmp / "h4"), "--script", "carr-mcp-staging", "--tag", TAG2, "--digest", d2,
             "--sha", c_edit, "--version-id", VS, "--environment", "staging",
             "--history-repo", str(tmp / "r"), "--history-ref", c_edit],
            capture_output=True, text=True, env=GIT_ENV)
        check("H4. a tag the history never declared is refused",
              done.returncode == 4 and "is not declared" in done.stderr and not (tmp / "h4").exists(),
              done.stdout + done.stderr)

    # H5. the proof covers every entry BEFORE the tag too: the digest is over the
    # whole list, so an earlier entry edited after the tag was introduced means
    # the deploy that applied the tag ran a different list.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        git = ["git", "-c", "user.name=selftest", "-c", "user.email=selftest@example.test", "-C", str(tmp / "r")]
        (tmp / "r" / "mcp-server").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(tmp / "r")], check=True, env=GIT_ENV)
        cfg = tmp / "r" / "mcp-server" / "wrangler.toml"
        head = '[env.staging]\nname = "carr-mcp-staging"\n'
        earlier = "v0-selftest-earlier"

        def commit5(text: str, msg: str) -> str:
            cfg.write_text(text, encoding="utf-8")
            subprocess.run([*git, "add", "-A"], check=True, env=GIT_ENV)
            subprocess.run([*git, "commit", "-q", "-m", msg], check=True, env=GIT_ENV)
            return subprocess.run([*git, "rev-parse", "HEAD"], check=True, env=GIT_ENV,
                                  capture_output=True, text=True).stdout.strip()

        def entries(first_classes: str) -> str:
            return (head + f'\n[[migrations]]\ntag = "{earlier}"\nnew_sqlite_classes = ["{first_classes}"]\n'
                    + f'\n[[migrations]]\ntag = "{TAG1}"\nnew_sqlite_classes = ["T"]\n')

        commit5(head + f'\n[[migrations]]\ntag = "{earlier}"\nnew_sqlite_classes = ["E"]\n', "earlier tag")
        commit5(entries("E"), "introduce the tag after the earlier entry")
        c_edit = commit5(entries("E2"), "edit the EARLIER entry after the tag was introduced")
        done = subprocess.run(
            [sys.executable, str(REPO / "ops" / "worker-do-migration.py"), "tag-receipt", "write",
             "--dir", str(tmp / "h5"), "--script", "carr-mcp-staging", "--tag", TAG1,
             "--digest", expected_digest(entries("E2")), "--sha", c_edit, "--version-id", VS,
             "--environment", "staging", "--history-repo", str(tmp / "r"), "--history-ref", c_edit],
            capture_output=True, text=True, env=GIT_ENV)
        check("H5. an EARLIER [[migrations]] entry edited after the tag was introduced: refused (exit 4), "
              "no receipt, although the tag's own entry never changed",
              done.returncode == 4 and "changed at " + c_edit[:12] in done.stderr
              and not (tmp / "h5").exists(), done.stdout + done.stderr)

    # K. staging already carries the tag
    res = run(source, tags=[TAG1], state={"applied_tag": None, "staging_applied_tag": TAG1})
    check("K1. staging already carries the tag with no durable receipt: refused before staging moves",
          res["rc"] == 1 and "no durable receipt" in res["err"] and "applied only once" in res["err"]
          and not staging_deploys(res) and production_untouched(res), res["all"][-700:])
    with tempfile.TemporaryDirectory() as shared_raw:
        shared = Path(shared_raw)
        first = run(source, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "fail_before_apply"},
                    state_dir=shared)
        res = run(source, tags=[TAG1], state={"applied_tag": None, "staging_applied_tag": TAG1},
                  state_dir=shared)
        check("K2. an earlier attempt's durable receipt with the same steps admits the applied tag",
              first["rc"] == 1 and res["rc"] == 0
              and (res["receipt"] or {}).get("staging_precheck", {}).get("tag_moved") is False
              and (res["receipt"] or {}).get("state") == "applied_verified", res["all"][-700:])
    with tempfile.TemporaryDirectory() as shared_raw:
        shared = Path(shared_raw)
        run(source, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "fail_before_apply"},
            state_dir=shared, toml=wrangler_toml([TAG1], classes="Old"))
        res = run(source, tags=[TAG1], state={"applied_tag": None, "staging_applied_tag": TAG1},
                  state_dir=shared, toml=wrangler_toml([TAG1], classes="New"))
        check("K3. steps edited after the tag was applied: refused before staging moves",
              res["rc"] == 1 and "now declares" in res["err"] and not staging_deploys(res)
              and production_untouched(res)
              and (res["receipt"] or {}).get("staging_precheck", {}).get("tag_moved") is None,
              res["all"][-700:])
    res = run(source, tags=[TAG1], state={"applied_tag": None}, locate="worktree")
    check("K4. from a linked release worktree the durable receipts land beside the MAIN checkout",
          res["rc"] == 0 and list(res["tag_receipts"]) ==
          [f"main/out/deploy-worker/do-migration-tags/carr-mcp-staging--{TAG1}.json"],
          str(list(res["tag_receipts"])) + res["all"][-400:])

    # T. Production attachments a plain deploy would rewrite
    declared = declared_domains(wrangler_toml([TAG1]))
    for label, t_state, why in (
            ("a declared custom domain Production lacks (today's real state)",
             {"live_domains": [d for d in declared if d != "reports.doctorcre.com"]},
             "would ATTACH custom domain(s) Production does not have: reports.doctorcre.com"),
            ("a custom domain Production has and wrangler.toml lacks",
             {"live_domains": [*declared, "old.example.test"]}, "would DETACH"),
            ("workers.dev enabled on Production", {"live_workers_dev": True}, "workers.dev enabled=false"),
            ("custom domains unreadable", {"domains_mode": "http_error"}, "could not be read"),
            ("custom domains paginated", {"domains_mode": "paginated"}, "could not be compared"),
            ("a custom domain bound to another Worker", {"domain_service": "someone-else"},
             "could not be compared")):
        res = run(source, tags=[TAG1], state={"applied_tag": None, **t_state})
        rc = res["receipt"] or {}
        check(f"T. {label}: refused before staging or Production moves",
              res["rc"] == 1 and why in res["all"] and "Production traffic was not changed" in res["err"]
              and not staging_deploys(res) and production_untouched(res)
              and rc.get("state") == "not_applied" and (rc.get("refusal") or "").startswith("production attachments")
              and rc.get("staging_precheck", {}).get("state") == "not-run" and res["receipt_valid"],
              res["all"][-700:])

    # C. unknown applied tag: every variant refuses before any mutation
    for label, state in (
            ("metadata read fails", {"services_mode": "http_error"}),
            ("metadata is not JSON", {"services_mode": "malformed"}),
            ("metadata names another script", {"script_id": "carr-mcp-staging"}),
            ("applied tag is not declared", {"applied_tag": "v0-someone-else"}),
            ("no credential", {"auth_fail": True})):
        res = run(source, tags=[TAG1], state=state)
        check(f"C. {label}: refused, fail-closed and loud",
              res["rc"] == 1 and "REFUSED:" in res["err"] and "UNKNOWN" in res["err"]
              and "traffic was not changed" in res["err"], res["all"][-600:])
        check(f"C. {label}: no attachment read, no deploy, no upload, no receipt",
              attachment_reads(res) == 0 and not wrangler_calls(res, "deploy")
              and not wrangler_calls(res, "versions") and res["receipt"] is None)

    # D. the Production migration deploy
    res = run(source, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "fail_before_apply"})
    rc = res["receipt"] or {}
    check("D1. failed deploy, tag unchanged: refused, nothing to roll back, no candidate",
          res["rc"] == 1 and "NOT applied" in res["err"] and "Nothing needs rolling back" in res["err"]
          and not wrangler_calls(res, "versions") and "provider version:" not in res["out"],
          res["all"][-600:])
    check("D1. receipt not_applied with the deploy's exit; recorded as a failed change; no marker",
          rc.get("state") == "not_applied" and rc.get("deploy_exit") == 1
          and [arg(c, "--outcome") for c in settings_changes(res)] == ["failed"]
          and "DO migration" not in res["out"], json.dumps(rc))

    res = run(source, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "fail_after_apply"})
    check("D2. failed deploy, tag applied: refused, rollback blocked, forward fix only",
          res["rc"] == 1 and "WAS applied" in res["err"] and "FORWARD FIX ONLY" in res["err"]
          and "do not promote a" in res["err"] and "provider version:" not in res["out"],
          res["all"][-600:])
    check("D2. receipt applied_unverified, applied marker, recorded as applied",
          (res["receipt"] or {}).get("state") == "applied_unverified"
          and (res["receipt"] or {}).get("deploy_exit") == 1
          and f"DO migration applied: tag={TAG1}" in res["out"]
          and [arg(c, "--outcome") for c in settings_changes(res)] == ["applied"] and res["receipt_valid"])

    res = run(source, tags=[TAG1], state={"applied_tag": None, "stale_readback": True})
    check("D3. applied but read-back mismatch: refused, forward fix, no candidate",
          res["rc"] == 1 and "did not read back" in res["err"] and "FORWARD FIX ONLY" in res["err"]
          and not wrangler_calls(res, "versions") and "provider version:" not in res["out"]
          and (res["receipt"] or {}).get("state") == "applied_unverified"
          and (res["receipt"] or {}).get("readback") == "mismatch", res["all"][-600:])

    res = run(source, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "ok_no_tag_move"})
    check("D4. deploy returned 0 but the tag did not move: possibly applied, forward fix",
          res["rc"] == 1 and "possibly applied" in res["err"] and "FORWARD FIX ONLY" in res["err"]
          and f"DO migration possibly applied: tag={TAG1} from=none version={V0}" in res["out"]
          and "DO migration applied:" not in res["out"] and "provider version:" not in res["out"]
          and (res["receipt"] or {}).get("state") == "unknown"
          and [arg(c, "--outcome") for c in settings_changes(res)] == ["failed"]
          and "POSSIBLY APPLIED" in (arg(settings_changes(res)[0], "--reason") or ""), res["all"][-700:])

    res = run(source, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "no_version"})
    check("D5. deploy returned 0 and moved the tag but printed no version: refused, forward fix",
          res["rc"] == 1 and "none printed" in res["err"] and "FORWARD FIX ONLY" in res["err"]
          and f"DO migration applied: tag={TAG1} from=none version=unknown" in res["out"]
          and (res["receipt"] or {}).get("state") == "applied_unverified"
          and (res["receipt"] or {}).get("migration_version_id") is None
          and "provider version:" not in res["out"], res["all"][-700:])

    res = run(source, tags=[TAG1], state={"applied_tag": None, "services_mode": "fail_after_deploy"})
    check("D6. the tag cannot be re-read after the deploy: possibly applied marker, forward fix",
          res["rc"] == 1 and "possibly applied" in res["err"]
          and f"DO migration possibly applied: tag={TAG1}" in res["out"]
          and (res["receipt"] or {}).get("state") == "unknown"
          and "provider version:" not in res["out"], res["all"][-700:])

    res = run(source, tags=[TAG1], state={"applied_tag": None, "settings_change_fail": True})
    check("D7. the database record of the move fails: the release stops before any candidate",
          res["rc"] == 1 and "could not be recorded in ops.settings_change" in res["err"]
          and "provider version:" not in res["out"]
          and (res["receipt"] or {}).get("db_record") == "failed"
          and (res["receipt"] or {}).get("state") == "applied_verified", res["all"][-700:])

    res = run(source, tags=[TAG1], state={"applied_tag": None},
              env_extra={"FOUNDATION_ASSURANCE_STAGING_PROVIDER": VS})
    check("D8. WR95 live acquisition with a pending migration: refused before anything moves",
          res["rc"] == 1 and "WR95" in res["err"] and "Production traffic was not changed" in res["err"]
          and attachment_reads(res) == 0 and not wrangler_calls(res, "deploy"), res["all"][-600:])

    # E. source wiring
    upload_at = source.index('VERSION_UPLOAD_OUTPUT="$("$WRANGLER" versions upload')
    call_at = source.rfind("\n  apply_pending_do_migration\n", 0, upload_at)
    branch_at = source.index('if [ "$VERSION_MODE" = "upload" ]; then\n  if [ -n "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" ]')
    adopt_at = source.index('  if [ -n "$DO_MIGRATION_VERSION_ID" ]; then\n    PROVIDER_VERSION_ID="$DO_MIGRATION_VERSION_ID"\n  else\n')
    check("E1. the upload branch applies/refuses the migration, then uploads only when none was applied",
          branch_at < call_at < adopt_at < upload_at)
    promote_at = source.index('"$WRANGLER" versions deploy "${PROVIDER_VERSION_ID}@100"')
    check("E2. promotion loads the receipt before it moves traffic",
          source.rfind("load_do_migration_receipt\n", 0, promote_at) > source.index("(END do-migration block)"))
    check("E3. every deployment row of that promotion names the migration",
          'rd_evidence_ref="$rd_evidence_ref;$DO_MIGRATION_EVIDENCE"' in source)
    check("E4. the smoke-failure rollback advice states the migration limit",
          'echo "$DO_MIGRATION_ROLLBACK_NOTE"' in source)
    check("E5. staging's deploy path is untouched by the block",
          "apply_pending_do_migration" not in source[source.index("deploy_staging_worker() {"):
                                                    source.index("# ---------- pending Durable Object")])
    tail = source[upload_at:source.index('echo "The wrapper writes the typed rehearsal evidence required for readiness."')]
    check("E6. after the candidate exists, no refusal claims traffic was unchanged unconditionally",
          tail.count("raffic was not changed") == 2 and tail.count("$DO_TRAFFIC_CLAUSE") >= 4,
          f"{tail.count('raffic was not changed')} / {tail.count('$DO_TRAFFIC_CLAUSE')}")

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
