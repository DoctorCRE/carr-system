#!/usr/bin/env python3
"""Execute an authenticated Engineering Passport job with native Codex.

The controller authenticates before writing stdin. Its credentials never enter
Codex. Source and scope checks run before the temporary clone is deleted.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

REPO = Path(__file__).resolve().parents[2]
REPOSITORY_URL = 'https://github.com/DoctorCRE/carr-system.git'


class DispatchRefusal(ValueError):
    pass


def validate_job(job: dict) -> dict:
    if not isinstance(job, dict) or set(job) != {"repository", "revision", "permitted_work", "checks"}:
        raise DispatchRefusal("job requires repository, revision, permitted_work and checks")
    if job["repository"] != "DoctorCRE/carr-system" or not re.fullmatch(r"[0-9a-f]{40}", str(job["revision"])):
        raise DispatchRefusal("job repository or revision is invalid")
    scope = job["permitted_work"]
    if (not isinstance(scope, dict) or not isinstance(scope.get("objective"), str)
            or not scope["objective"].strip() or not isinstance(scope.get("scope_boundary"), str)):
        raise DispatchRefusal("job permitted work is missing")
    for key in ("actions", "resource_refs", "component_refs", "paths"):
        if not isinstance(scope.get(key), list) or any(not isinstance(x, str) or not x for x in scope[key]):
            raise DispatchRefusal("job permitted work lists are invalid")
    for path in scope["paths"]:
        if path.startswith("/") or ".." in path.split("/"):
            raise DispatchRefusal("job paths must be repository relative")
    if not isinstance(job["checks"], list) or not job["checks"]:
        raise DispatchRefusal("job checks are missing")
    if any(not isinstance(c, dict) or not isinstance(c.get("check_ref"), str) or not c["check_ref"] for c in job["checks"]):
        raise DispatchRefusal("job checks must name their check_ref")
    if len({c["check_ref"] for c in job["checks"]}) != len(job["checks"]):
        raise DispatchRefusal("job checks must be unique")
    return job


def validate_result(result: dict, job: dict) -> dict:
    if not isinstance(result, dict) or set(result) != {*job, "outcome"}:
        raise DispatchRefusal("job result has an unsupported shape")
    if any(result[key] != job[key] for key in ("repository", "revision")):
        raise DispatchRefusal("job result changed repository or revision")
    permitted, actual = job["permitted_work"], result["permitted_work"]
    if not isinstance(actual, dict) or set(actual) != set(permitted):
        raise DispatchRefusal("job result changed its scope fields")
    for key, value in actual.items():
        if key in {"actions", "resource_refs", "component_refs", "paths"}:
            if not isinstance(value, list) or any(item not in permitted[key] for item in value):
                raise DispatchRefusal("job result exceeds permitted work")
        elif value != permitted[key]:
            raise DispatchRefusal("job result changed permitted work")
    checks = result["checks"]
    if not isinstance(checks, list) or sorted(c.get("check_ref", "") for c in checks if isinstance(c, dict)) != sorted(c["check_ref"] for c in job["checks"]):
        raise DispatchRefusal("job result omitted or added checks")
    if any(c.get("state") not in {"passed", "failed", "blocked", "not_run"}
           or not isinstance(c.get("evidence_refs"), list)
           or c["state"] == "passed" and not c["evidence_refs"] for c in checks):
        raise DispatchRefusal("job check result is invalid")
    outcome = result["outcome"]
    if not isinstance(outcome, dict) or outcome.get("status") not in {"claimed_complete", "failed", "blocked"}:
        raise DispatchRefusal("job outcome is invalid")
    if outcome["status"] == "claimed_complete" and (any(c["state"] != "passed" for c in checks)
            or not outcome.get("artifact_refs") or not outcome.get("evidence_refs")):
        raise DispatchRefusal("completion requires passing checks and their output")
    return result


def _safe_child_env() -> dict[str, str]:
    return {key: os.environ[key] for key in ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TERM") if os.environ.get(key)}


def preflight() -> dict:
    process = subprocess.run(['codex', '--version'], env=_safe_child_env(),
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True)
    return {"ok": True, "executor": "codex exec", "version": process.stdout.strip()}


def run(job: dict) -> dict:
    validate_job(job)
    env = _safe_child_env()
    with tempfile.TemporaryDirectory(prefix='engineering-codex-') as temporary:
        tree = Path(temporary) / 'checkout'
        subprocess.run(['git', 'clone', '--quiet', '--', REPOSITORY_URL, str(tree)],
                       env=env, check=True, capture_output=True, text=True)
        revision = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'],
                                           env=env, text=True).strip()
        if revision != job['revision']:
            raise DispatchRefusal('repository advanced before execution; job revision is stale')
        branch = 'engineering-' + uuid.uuid4().hex[:12]
        subprocess.run(['git', '-C', str(tree), 'switch', '--quiet', '-c', branch],
                       env=env, check=True, capture_output=True, text=True)
        prompt = (
            "Read standing-context before work. Execute this job only in " + str(tree) + ". "
            "Return one JSON object with repository, revision, permitted_work, checks and outcome. "
            "Report the work actually performed as a subset of permitted_work. Checks contain check_ref, state "
            "and evidence_refs. Outcome contains status (claimed_complete, failed or blocked), artifact_refs, "
            "evidence_refs, detail and source_evidence (worktree_ref, branch_ref, source_sha, evidence_refs). "
            "Use the exact checkout path for worktree_ref and its Git branch name for branch_ref; "
            "the adapter converts them to opaque receipt identifiers after validation. "
            "Do not merge or deploy. Never claim a check passed without its actual output.\n" + json.dumps(job)
        )
        result_path = tree.parent / 'result.json'
        command = ['codex', 'exec', '--dangerously-bypass-hook-trust', '--sandbox', 'workspace-write',
                   '-c', 'sandbox_workspace_write.network_access=true',
                   '-c', 'features.network_proxy={enabled=true,domains={"github.com"="allow","api.github.com"="allow"}}',
                   '-C', str(tree), '-o', str(result_path), prompt]
        completed = subprocess.run(command, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if completed.returncode or not result_path.is_file():
            raise DispatchRefusal('Codex execution did not complete')
        try:
            result = validate_result(json.loads(result_path.read_text()), job)
        except (OSError, json.JSONDecodeError) as exc:
            raise DispatchRefusal("Codex did not return one job result") from exc
        source = result["outcome"].get("source_evidence", {})
        if (source.get("worktree_ref") != str(tree)
                or source.get("branch_ref") != branch):
            raise DispatchRefusal("result belongs to a different checkout")
        head = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'], text=True, env=env).strip()
        if source.get("source_sha") != head:
            raise DispatchRefusal("result revision does not match its checkout")
        changed = subprocess.check_output(['git', '-C', str(tree), 'diff', '--no-renames', '--name-only', '-z', job["revision"]], text=True, env=env)
        untracked = subprocess.check_output(['git', '-C', str(tree), 'ls-files', '--others', '--exclude-standard', '-z'], text=True, env=env)
        paths = [path for path in (changed + untracked).split('\0') if path]
        if paths and "repository:write-declared-scope" not in job["permitted_work"]["actions"]:
            raise DispatchRefusal("read-only job changed files")
        if any(not any(fnmatchcase(path, allowed) for allowed in job["permitted_work"]["paths"]) for path in paths):
            raise DispatchRefusal("job changed files outside permitted work")
        source["worktree_ref"] = "worktree:" + sha256(str(tree).encode("utf-8")).hexdigest()
        source["branch_ref"] = "branch:" + sha256(branch.encode("utf-8")).hexdigest()
        return result


def main() -> int:
    try:
        if sys.argv[1:] == ["--preflight"]:
            result = preflight()
        elif not sys.argv[1:]:
            result = run(json.load(sys.stdin))
        else:
            raise DispatchRefusal("unsupported arguments")
    except (DispatchRefusal, ValueError, OSError, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, DispatchRefusal) else type(exc).__name__
        print(json.dumps({"ok": False, "error": detail}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
