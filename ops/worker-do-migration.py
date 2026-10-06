#!/usr/bin/env python3
"""worker-do-migration.py — is a Durable Object migration pending on the Worker?

WHY THIS EXISTS. Production ships through `wrangler versions upload` followed by
an exact `wrangler versions deploy <id>@100` (bin/deploy-worker.sh). Wrangler
4.137 refuses `versions upload` outright while the Worker has a Durable Object
migration it has not applied, and Cloudflare's own documentation says a Durable
Object lifecycle change "can only be applied via `wrangler deploy`". So the first
release whose wrangler.toml adds a `[[migrations]]` tag would stop the
unattended release pipeline at its upload step, with nothing a retry could fix.

bin/deploy-worker.sh --upload-version asks this file ONE question before it
uploads: which migration tag has the deployed Worker applied, and is the newest
tag declared in wrangler.toml beyond it. When it is, the wrapper applies it the
documented way and then continues with the ordinary upload.

THE APPLIED TAG IS READ, NEVER INFERRED. It comes from the same Cloudflare
metadata wrangler itself reads to decide what to send
(`GET /accounts/<account>/workers/services/<script>`, field
`default_environment.script.migration_tag`; see getMigrationsToUpload in
wrangler-dist/cli.js). This file does not fetch it — the wrapper does, with the
token `wrangler auth token` resolves — it only judges the response. The pending
decision then mirrors wrangler's own: no applied tag means every declared
migration is pending; an applied tag that is the newest declared one means none
is; an applied tag earlier in the list means the ones after it are.

FAIL CLOSED, exit 3, whenever the applied tag cannot be determined: a response
that is not JSON, not `success: true`, has no script object, names a different
script, or carries a migration_tag that is not a non-empty string. An applied tag
that wrangler.toml does NOT declare is also exit 3: wrangler would warn and
re-apply the whole list, and a release pipeline must not guess which of those
two histories is true. `migration_tag` absent or null on an otherwise exact
script object is the documented "none applied" state (the Cloudflare API marks
the field optional), which is also exactly how wrangler reads it.

THE STEPS DIGEST. A tag is applied once and never again, so a Worker can carry
tag T whose declared steps have since been edited (say `new_classes` became
`new_sqlite_classes`). Every target and plan therefore carries `steps_digest`,
the sha256 of the declared [[migrations]] list in canonical JSON, and the
wrapper keeps a durable per-tag receipt of the digest it applied, so "already
applied" is accepted only when it was applied with these exact steps.

THE ATTACHMENTS A DEPLOY WOULD REWRITE. Only the migration path runs a plain
`wrangler deploy` against Production, and that command re-publishes the
top-level routes: wrangler 4.137 replaces the Worker's custom-domain set
(`domains/changeset?replace_state=true`, and outside a TTY it forces
override_existing_origin and override_existing_dns_record) and sets the
workers.dev subdomain to `workers_dev`, defaulting to false whenever routes are
declared. `attachments` compares what the deploy would publish with what
Production has, and refuses on any difference rather than letting a migration
release quietly change hostnames.

Subcommands (all output is JSON on stdout; nothing here reads a credential):
  target      --config <wrangler.toml> --env <production|name>
              account id, script name, declared tags and steps digest
  plan        --config <wrangler.toml> --env <...> --services-json <file>
              the pending decision against a fetched services response
  receipt     --file <receipt.json> --sha <40-hex>
              validate a migration receipt this wrapper wrote and print it
  attachments --config <wrangler.toml> --domains-json <file> --subdomain-json <file>
              exit 0 = Production's attachments already equal what a deploy
              publishes, 4 = they differ (the difference is printed), 3 = unknown
  tag-receipt write|check --dir <dir> --script <name> --tag <tag> --digest <sha256:...>
              [--sha <40-hex> --version-id <uuid> --environment <env>]
              [--history-repo <checkout> [--history-ref origin/main]]
              the durable per-tag receipt; check exits 4 when it is missing or
              names other steps. A HAND-WRITTEN receipt must pass --history-repo:
              write then refuses (exit 4) unless the tag's [[migrations]] entry
              and every entry before it are identical in every first-parent
              commit of the ref since the commit that introduced the tag, and
              --digest is the digest that history proves
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

UNKNOWN = 3
USAGE = 2
DIFFERS = 4
RECEIPT_SCHEMA = "carr-worker-do-migration-receipt.v1"
TAG_RECEIPT_SCHEMA = "carr-worker-do-migration-tag-receipt.v1"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RECEIPT_STATES = ("applied_verified", "applied_unverified", "not_applied", "unknown")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Undetermined(Exception):
    """The applied tag cannot be established; the caller must fail closed."""


class ConfigError(Exception):
    """wrangler.toml does not describe a usable target."""


class HistoryChanged(Exception):
    """The tag's steps changed after it was introduced; no receipt can prove them."""


def steps_digest(migrations: list) -> str:
    canonical = json.dumps(migrations, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def load_target(config: Path, env: str) -> dict:
    try:
        doc = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {config}: {exc}") from exc
    account = doc.get("account_id")
    section: dict[str, Any]
    if env == "production":
        section = doc
    else:
        envs = doc.get("env")
        found = envs.get(env) if isinstance(envs, dict) else None
        if not isinstance(found, dict):
            raise ConfigError(f"{config} has no [env.{env}] section")
        section = found
        account = section.get("account_id", account)
    script = section.get("name")
    # migrations are inheritable in wrangler: an env without its own list uses
    # the top-level one, which is what PR #1244's staging comment relies on.
    migrations = section.get("migrations", doc.get("migrations", []))
    if not isinstance(account, str) or not re.fullmatch(r"[0-9a-f]{32}", account):
        raise ConfigError("account_id is not an exact 32-hex account id")
    if not isinstance(script, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", script):
        raise ConfigError("the target script name is not an exact Worker name")
    if not isinstance(migrations, list):
        raise ConfigError("[[migrations]] is not a list")
    tags: list[str] = []
    for entry in migrations:
        tag = entry.get("tag") if isinstance(entry, dict) else None
        if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
            raise ConfigError(f"a [[migrations]] entry has no usable tag: {entry!r}")
        if tag in tags:
            raise ConfigError(f"migration tag {tag!r} is declared twice")
        tags.append(tag)
    exports = section.get("exports", doc.get("exports"))
    if exports:
        # wrangler sends declarative exports INSTEAD of migrations when present;
        # this file only understands the migrations list, so it refuses rather
        # than answering a question wrangler is not asking.
        raise ConfigError("declarative `exports` is configured; this check covers [[migrations]] only")
    return {"account_id": account, "script": script, "declared_tags": tags,
            "steps_digest": steps_digest(migrations)}


def applied_tag(services: object, script: str) -> str | None:
    if not isinstance(services, dict) or services.get("success") is not True:
        raise Undetermined("the services response is not a successful Cloudflare API envelope")
    result = services.get("result")
    if not isinstance(result, dict):
        raise Undetermined("the services response has no result object")
    default_env = result.get("default_environment")
    if not isinstance(default_env, dict):
        raise Undetermined("the services response has no default_environment")
    script_obj = default_env.get("script")
    if not isinstance(script_obj, dict):
        raise Undetermined("the services response has no default_environment.script object")
    if script_obj.get("id") != script:
        raise Undetermined(
            f"the services response describes script {script_obj.get('id')!r}, not {script!r}")
    if "migration_tag" not in script_obj or script_obj["migration_tag"] is None:
        return None
    tag = script_obj["migration_tag"]
    if not isinstance(tag, str) or not tag:
        raise Undetermined(f"migration_tag is present but not a non-empty string: {tag!r}")
    return tag


def plan(target: dict, services: object) -> dict:
    declared = target["declared_tags"]
    if not declared:
        return {"script": target["script"], "declared_tags": [], "latest_tag": None,
                "applied_tag": None, "applied_known": False, "pending": False,
                "pending_tags": [], "steps_digest": target["steps_digest"]}
    applied = applied_tag(services, target["script"])
    if applied is None:
        pending_tags = list(declared)
    elif applied in declared:
        pending_tags = declared[declared.index(applied) + 1:]
    else:
        raise Undetermined(
            f"the Worker reports applied tag {applied!r}, which wrangler.toml does not declare; "
            "wrangler would re-apply every migration, and which history is true is not guessable")
    return {"script": target["script"], "declared_tags": declared, "latest_tag": declared[-1],
            "applied_tag": applied, "applied_known": True, "pending": bool(pending_tags),
            "pending_tags": pending_tags, "steps_digest": target["steps_digest"]}


def read_receipt(path: Path, sha: str) -> dict:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read migration receipt {path}: {exc}") from exc
    required = {"schema", "git_sha", "script", "old_tag", "new_tag", "migration_version_id",
                "state", "applied_at"}
    if not isinstance(receipt, dict) or not required <= set(receipt):
        raise ConfigError("migration receipt is missing required fields")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["git_sha"] != sha:
        raise ConfigError("migration receipt is for another schema or SHA")
    if not isinstance(receipt["new_tag"], str) or not TAG_RE.fullmatch(receipt["new_tag"]):
        raise ConfigError("migration receipt has no exact new tag")
    version = receipt["migration_version_id"]
    if version is not None and not (isinstance(version, str) and UUID_RE.fullmatch(version)):
        raise ConfigError("migration receipt has a malformed provider version id")
    if receipt["state"] not in RECEIPT_STATES:
        raise ConfigError("migration receipt has an unknown state")
    exit_code = receipt.get("deploy_exit")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise ConfigError("migration receipt has a malformed deploy exit")
    return receipt


def _envelope_result(value: object, what: str) -> object:
    if not isinstance(value, dict) or value.get("success") is not True or "result" not in value:
        raise Undetermined(f"the {what} response is not a successful Cloudflare API envelope")
    return value["result"]


def attachments(config: Path, domains: object, subdomain: object) -> dict:
    """What a Production `wrangler deploy` would publish, against what is live."""
    try:
        doc = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {config}: {exc}") from exc
    script = doc.get("name")
    routes = doc.get("routes", [])
    if doc.get("route") is not None or not isinstance(routes, list):
        raise ConfigError("only a top-level `routes` list is understood")
    declared: list[str] = []
    for route in routes:
        if not (isinstance(route, dict) and route.get("custom_domain") is True
                and isinstance(route.get("pattern"), str)):
            raise ConfigError(f"route {route!r} is not a custom domain; zone routes are not compared")
        declared.append(route["pattern"])
    if (doc.get("triggers") or {}).get("crons"):
        raise ConfigError("cron triggers are declared; a deploy would re-publish them and they are not compared")
    want_workers_dev = doc.get("workers_dev", len(routes) == 0)
    want_previews = doc.get("preview_urls")

    result = _envelope_result(domains, "custom domains")
    info = domains.get("result_info") if isinstance(domains, dict) else None
    if not isinstance(result, list):
        raise Undetermined("the custom domains response has no result list")
    if isinstance(info, dict) and info.get("total_count") not in (None, len(result)):
        raise Undetermined("the custom domains response is paginated; not every domain was read")
    live: list[str] = []
    for row in result:
        if not isinstance(row, dict) or not isinstance(row.get("hostname"), str):
            raise Undetermined("a custom domain row has no hostname")
        if row.get("service") != script:
            raise Undetermined(f"a custom domain row names service {row.get('service')!r}, not {script!r}")
        live.append(row["hostname"])
    sub = _envelope_result(subdomain, "workers.dev subdomain")
    if not isinstance(sub, dict) or not isinstance(sub.get("enabled"), bool):
        raise Undetermined("the workers.dev subdomain response has no boolean `enabled`")

    differences = []
    added = sorted(set(declared) - set(live))
    removed = sorted(set(live) - set(declared))
    if added:
        differences.append("the deploy would ATTACH custom domain(s) Production does not have: " + ", ".join(added))
    if removed:
        differences.append("the deploy would DETACH custom domain(s) Production has: " + ", ".join(removed))
    if sub["enabled"] != want_workers_dev:
        differences.append(f"the deploy would set workers.dev enabled={str(want_workers_dev).lower()} "
                           f"(Production has {str(sub['enabled']).lower()})")
    if want_previews is not None and sub.get("previews_enabled") != want_previews:
        differences.append(f"the deploy would set preview URLs enabled={str(want_previews).lower()} "
                           f"(Production has {sub.get('previews_enabled')!r})")
    return {"script": script, "declared_custom_domains": sorted(declared),
            "live_custom_domains": sorted(live),
            "workers_dev": {"deploy": want_workers_dev, "live": sub["enabled"]},
            "same": not differences, "differences": differences}


def tag_receipt_path(directory: Path, script: str, tag: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", script) or not TAG_RE.fullmatch(tag):
        raise ConfigError("tag receipt needs an exact script name and tag")
    return directory / f"{script}--{tag}.json"


def _env_migrations(text: str, env: str) -> list:
    doc = tomllib.loads(text)
    section = doc if env == "production" else (doc.get("env") or {}).get(env)
    if not isinstance(section, dict):
        raise ConfigError(f"no [env.{env}] section")
    migrations = section.get("migrations", doc.get("migrations", []))
    if not isinstance(migrations, list):
        raise ConfigError("[[migrations]] is not a list")
    return migrations


def tag_history_proof(repo: Path, ref: str, config: str, env: str, tag: str, digest: str) -> dict:
    """Proof (a) for a hand-written receipt. A tag is applied only by the first
    deploy that carries it, so the steps it was applied with are whatever the
    [[migrations]] list said when that deploy ran. That is known without
    deployment history only if the list up to and including the tag never
    changed on the release line after the tag was introduced."""
    def git(*argv: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True)
        if done.returncode != 0:
            raise Undetermined(f"git {' '.join(argv)} failed: {done.stderr.strip()[:200]}")
        return done.stdout
    ref_sha = git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    commits = git("rev-list", "--first-parent", "--reverse", ref_sha, "--", config).split()
    introduced = None
    prefix_at: dict[str, list] = {}
    for commit in commits:
        done = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{config}"],
                              capture_output=True, text=True)
        if done.returncode != 0:
            if introduced is None:
                continue
            raise HistoryChanged(f"{config} is missing at {commit[:12]}, after {tag} was introduced")
        try:
            migrations = _env_migrations(done.stdout, env)
        except (tomllib.TOMLDecodeError, ConfigError) as exc:
            if introduced is None:
                continue
            raise HistoryChanged(f"{config} at {commit[:12]} is unreadable after {tag} was introduced: {exc}")
        tags = [m.get("tag") if isinstance(m, dict) else None for m in migrations]
        if tag not in tags:
            if introduced is not None:
                raise HistoryChanged(f"{tag} disappears from {config} at {commit[:12]}")
            continue
        if introduced is None:
            introduced = commit
        prefix_at[commit] = migrations[:tags.index(tag) + 1]
    if introduced is None:
        raise HistoryChanged(f"{tag} is not declared in {config} anywhere on {ref}")
    first = prefix_at[introduced]
    for commit, prefix in prefix_at.items():
        if steps_digest(prefix) != steps_digest(first):
            raise HistoryChanged(
                f"{tag}'s steps (or an earlier entry) changed at {commit[:12]} after the tag was "
                f"introduced at {introduced[:12]}; a deploy may have applied the older steps. "
                "Use a new tag, or prove the applying deploy from staging's deployment history")
    final = git("show", f"{ref_sha}:{config}")
    now = _env_migrations(final, env)
    if not now or not isinstance(now[-1], dict) or now[-1].get("tag") != tag:
        raise HistoryChanged(f"{tag} is not the newest [[migrations]] entry at {ref}")
    proven = steps_digest(now)
    if proven != digest:
        raise HistoryChanged(f"--digest {digest} is not the digest the history proves ({proven})")
    return {"ref": ref, "ref_sha": ref_sha, "introduced_in": introduced,
            "commits_checked": len(prefix_at), "steps_digest": proven}


def write_tag_receipt(args: argparse.Namespace) -> dict:
    if not DIGEST_RE.fullmatch(args.digest or "") or not re.fullmatch(r"[0-9a-f]{40}", args.sha or "")             or not UUID_RE.fullmatch(args.version_id or "") or not args.environment:
        raise ConfigError("tag receipt write needs --digest, --sha, --version-id and --environment")
    path = tag_receipt_path(args.dir, args.script, args.tag)
    proof = None
    if args.history_repo is not None:
        proof = tag_history_proof(args.history_repo, args.history_ref, args.history_config,
                                  args.environment, args.tag, args.digest)
    row = {"schema": TAG_RECEIPT_SCHEMA, "script": args.script, "environment": args.environment,
           "tag": args.tag, "steps_digest": args.digest, "git_sha": args.sha,
           "version_id": args.version_id, "source_ref": "bin/deploy-worker.sh",
           "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    if proof is not None:
        row["source_ref"] = "hand-written: tag-receipt write --history-repo"
        row["history_proof"] = proof
    args.dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return row


def check_tag_receipt(args: argparse.Namespace) -> dict:
    if not DIGEST_RE.fullmatch(args.digest or ""):
        raise ConfigError("tag receipt check needs --digest sha256:<64 hex>")
    path = tag_receipt_path(args.dir, args.script, args.tag)
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"match": False, "reason": f"no durable receipt for {args.script} tag {args.tag} at {path}"}
    except (OSError, ValueError) as exc:
        return {"match": False, "reason": f"the durable receipt {path} is unreadable: {exc}"}
    if not isinstance(row, dict) or row.get("schema") != TAG_RECEIPT_SCHEMA             or row.get("script") != args.script or row.get("tag") != args.tag:
        return {"match": False, "reason": f"the durable receipt {path} is not a receipt for {args.script} tag {args.tag}"}
    if row.get("steps_digest") != args.digest:
        return {"match": False, "reason": f"{args.script} applied tag {args.tag} with steps "
                f"{row.get('steps_digest')}, but wrangler.toml now declares {args.digest}"}
    return {"match": True, "receipt": row}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("target", "plan"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True, type=Path)
        p.add_argument("--env", required=True)
        if name == "plan":
            p.add_argument("--services-json", required=True, type=Path)
    r = sub.add_parser("receipt")
    r.add_argument("--file", required=True, type=Path)
    r.add_argument("--sha", required=True)
    a = sub.add_parser("attachments")
    a.add_argument("--config", required=True, type=Path)
    a.add_argument("--domains-json", required=True, type=Path)
    a.add_argument("--subdomain-json", required=True, type=Path)
    t = sub.add_parser("tag-receipt")
    t.add_argument("action", choices=["write", "check"])
    t.add_argument("--dir", required=True, type=Path)
    t.add_argument("--script", required=True)
    t.add_argument("--tag", required=True)
    t.add_argument("--digest", required=True)
    t.add_argument("--sha")
    t.add_argument("--version-id")
    t.add_argument("--environment")
    t.add_argument("--history-repo", type=Path)
    t.add_argument("--history-ref", default="origin/main")
    t.add_argument("--history-config", default="mcp-server/wrangler.toml")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "receipt":
            print(json.dumps(read_receipt(args.file, args.sha), sort_keys=True))
            return 0
        if args.cmd == "tag-receipt":
            if args.action == "write":
                print(json.dumps(write_tag_receipt(args), sort_keys=True))
                return 0
            verdict = check_tag_receipt(args)
            print(json.dumps(verdict, sort_keys=True))
            return 0 if verdict["match"] else DIFFERS
        if args.cmd == "attachments":
            try:
                domains = json.loads(args.domains_json.read_text(encoding="utf-8"))
                subdomain = json.loads(args.subdomain_json.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Undetermined(f"an attachment response is not readable JSON: {exc}") from exc
            verdict = attachments(args.config, domains, subdomain)
            print(json.dumps(verdict, sort_keys=True))
            if not verdict["same"]:
                for line in verdict["differences"]:
                    print(f"worker-do-migration: {line}", file=sys.stderr)
                return DIFFERS
            return 0
        target = load_target(args.config, args.env)
        if args.cmd == "target":
            print(json.dumps(target, sort_keys=True))
            return 0
        services: object = None
        if target["declared_tags"]:
            try:
                services = json.loads(args.services_json.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Undetermined(f"the services response is not readable JSON: {exc}") from exc
        print(json.dumps(plan(target, services), sort_keys=True))
        return 0
    except HistoryChanged as exc:
        print(f"worker-do-migration: REFUSED: {exc}", file=sys.stderr)
        return DIFFERS
    except Undetermined as exc:
        print(f"worker-do-migration: APPLIED TAG UNKNOWN: {exc}", file=sys.stderr)
        return UNKNOWN
    except ConfigError as exc:
        print(f"worker-do-migration: {exc}", file=sys.stderr)
        return USAGE


if __name__ == "__main__":
    raise SystemExit(main())
