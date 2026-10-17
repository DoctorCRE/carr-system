#!/usr/bin/env python3
"""Provision and deploy an exact E2E candidate to the isolated staging Worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from credential_env import TokensFilePermissionError, load_carr_tokens

CARR_HOST = "carr-mcp-staging.joe-bookout-carr-us.workers.dev"
APP_HOST = "doctorcre-app-staging.joe-bookout-carr-us.workers.dev"
SECRET_FILE = Path.home() / ".config/carr/e2e-session-secret"


class StagingRefusal(RuntimeError):
    pass


def validate_config(source: str) -> dict:
    config = tomllib.loads(source)
    staging = config.get("env", {}).get("staging", {})
    variables = staging.get("vars", {})
    production_kv = {row.get("id") for row in config.get("kv_namespaces", [])}
    staging_kv = staging.get("kv_namespaces", [])
    if staging.get("name") != "carr-mcp-staging" or staging.get("routes") != [] \
            or staging.get("workers_dev") is not True \
            or variables.get("CARR_ENV") != "staging" \
            or variables.get("APP_HOST") != CARR_HOST \
            or variables.get("DOCTORCRE_APP_HOST") != APP_HOST \
            or not staging_kv or any(not row.get("id") or row["id"] in production_kv for row in staging_kv):
        raise StagingRefusal("Worker config does not name the isolated staging target")
    return config


def checked_run(args, *, run=subprocess.run, **kwargs):
    try:
        result = run([sys.executable, str(REPO / "bin/with-timeout.py"), "120", *map(str, args)],
                     capture_output=True, text=True, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        raise StagingRefusal("command unavailable or timed out; output suppressed") from None
    if result.returncode:
        raise StagingRefusal("command failed; output suppressed")
    return result.stdout


def source_identity(repo: Path, *, run=subprocess.run) -> str:
    sha = checked_run(["git", "rev-parse", "HEAD"], cwd=repo, run=run).strip()
    dirty = checked_run(["git", "status", "--porcelain", "--untracked-files=normal", "--", ".",
                         ":(exclude)mcp-server/node_modules", ":(exclude)control-room/node_modules",
                         ":(exclude)workspace/node_modules"], cwd=repo, run=run).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or dirty:
        raise StagingRefusal("E2E staging deploy requires an exact clean committed source")
    return sha


def provider_environment(environ: dict, token: str | None) -> dict:
    if not token:
        raise StagingRefusal("long-lived Cloudflare token is unavailable; no interactive login")
    allowed = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SSL_CERT_FILE", "SSL_CERT_DIR")
    child = {key: environ[key] for key in allowed if environ.get(key)}
    child.update(CLOUDFLARE_API_TOKEN=token, CI="true", WRANGLER_SEND_METRICS="false")
    return child


def load_secret() -> str:
    try:
        metadata = SECRET_FILE.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise StagingRefusal("E2E secret must be an owned mode-600 regular file")
        secret = SECRET_FILE.read_text().strip()
    except OSError:
        raise StagingRefusal("E2E secret file is unavailable") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
        raise StagingRefusal("E2E secret has an invalid shape")
    return secret


def provision_and_deploy(repo: Path, sha: str, secret: str, child_env: dict, *, run=subprocess.run):
    config = validate_config((repo / "mcp-server/wrangler.toml").read_text())
    child_env = {**child_env, "CLOUDFLARE_ACCOUNT_ID": config["account_id"]}
    checked_run([sys.executable, repo / "ops/deploy-attachment-check.py", repo / "mcp-server/wrangler.toml", "staging"],
                cwd=repo, env=child_env, run=run)
    checked_run([sys.executable, repo / "tools/release-manifest.py", "doctorcre-artifact", "materialize",
                 "--pin", repo / "ops/config/doctorcre-artifact.v1.json", "--root", repo / "out/doctorcre-artifacts"],
                cwd=repo, env=child_env, run=run)
    wrangler = repo / "mcp-server/node_modules/.bin/wrangler"
    checked_run([wrangler, "secret", "put", "E2E_SESSION_SECRET", "--env", "staging"],
                cwd=repo / "mcp-server", env=child_env, input=secret + "\n", run=run)
    print("E2E secret provisioned to carr-mcp-staging; value suppressed", flush=True)
    checked_run([wrangler, "deploy", "--env", "staging", "--var", "GIT_SHA:" + sha],
                cwd=repo / "mcp-server", env=child_env, run=run)
    print("CARR staging candidate deployed at " + sha, flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def staging_fetch(path: str, *, method="GET", headers=None):
    if path not in {"/release", "/auth/e2e-session", "/auth/session"}:
        raise StagingRefusal("smoke request is outside the fixed staging routes")
    request = urllib.request.Request("https://" + CARR_HOST + path, method=method,
        headers={"user-agent": "Mozilla/5.0 (compatible; DoctorCRE-Staging-E2E/1.0)", **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            return response.status, dict(response.headers.items()), json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError):
        raise StagingRefusal("staging HTTP readback failed; response suppressed") from None


def smoke(sha: str, secret: str, *, fetch=staging_fetch):
    status, _, release = fetch("/release")
    if status != 200 or release.get("env", {}).get("value") != "staging" \
            or release.get("git_sha", {}).get("value") != sha:
        raise StagingRefusal("staging /release does not serve the exact candidate")
    status, headers, body = fetch("/auth/e2e-session", method="POST", headers={"authorization": "Bearer " + secret})
    cookie = next((value for key, value in headers.items() if key.lower() == "set-cookie"), "")
    if status != 200 or body != {"ok": True} or not cookie.startswith("__Host-dealroom_session=") \
            or "Domain=" in cookie or not all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=Lax")):
        raise StagingRefusal("staging E2E exchange did not issue the normal secure session")
    status, _, session = fetch("/auth/session", headers={"cookie": cookie.split(";", 1)[0]})
    if status != 200 or session.get("actor") != {"slug": "joe", "display": "E2E Joe"} \
            or session.get("e2e_principal") != "e2e-joe" or not session.get("csrf_token"):
        raise StagingRefusal("staging E2E session readback differs from the fixed synthetic principal")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="provision and deploy to fixed staging, then smoke")
    parser.add_argument("--source-sha", help="require this exact CARR source commit before any provider write")
    parser.add_argument("--app-repo", type=Path, help="also deploy the committed DoctorCRE staging candidate")
    parser.add_argument("--app-sha", help="exact DoctorCRE candidate source commit")
    args = parser.parse_args(argv)
    validate_config((REPO / "mcp-server/wrangler.toml").read_text())
    sha = source_identity(REPO)
    if args.source_sha and args.source_sha != sha:
        raise StagingRefusal("CARR source differs from the pinned E2E candidate")
    if args.apply and not args.source_sha:
        raise StagingRefusal("--apply requires the exact CARR --source-sha")
    if bool(args.app_repo) != bool(args.app_sha) or (args.app_sha and not re.fullmatch(r"[a-f0-9]{40}", args.app_sha)):
        raise StagingRefusal("app deployment requires both its repository and exact source SHA")
    if args.app_repo:
        remote = checked_run(["git", "remote", "get-url", "origin"], cwd=args.app_repo).strip()
        if not re.fullmatch(r"(?:https://github.com/|git@github.com:)jbookout/doctorcre-app(?:\.git)?", remote):
            raise StagingRefusal("app repository is outside the authorized DoctorCRE home")
        if source_identity(args.app_repo) != args.app_sha:
            raise StagingRefusal("DoctorCRE source differs from the exact clean candidate")
    if not args.apply:
        print("Verified staging-only configuration and clean CARR candidate " + sha)
        return 0
    secret = load_secret()
    token = load_carr_tokens(["CLOUDFLARE_API_TOKEN"]).get("CLOUDFLARE_API_TOKEN")
    child_env = provider_environment(dict(os.environ), token)
    provision_and_deploy(REPO, sha, secret, child_env)
    for attempt in range(12):
        try:
            smoke(sha, secret)
            break
        except StagingRefusal:
            if attempt == 11:
                raise
            time.sleep(5)
    print("Verified exact CARR staging source and synthetic normal session", flush=True)
    if args.app_repo:
        checked_run(["node", "scripts/e2e-staging/deploy-app.mjs", "--source-sha", args.app_sha],
                    cwd=args.app_repo, env=child_env)
        print("DoctorCRE staging candidate deployed at " + args.app_sha, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (StagingRefusal, TokensFilePermissionError) as error:
        raise SystemExit("E2E staging provision/deploy refused: " + str(error)) from None
    except (OSError, ValueError):
        raise SystemExit("E2E staging provision/deploy refused: local input is unreadable; output suppressed") from None
