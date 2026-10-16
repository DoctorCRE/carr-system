#!/usr/bin/env python3
"""Run one authenticated controller job through the dedicated Model Room desk.

The controller authenticates and checks its claim before writing to stdin.
No controller credential is passed to the model process.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import codex_checkout
import dispatch
import desks

ENGINEERING_DESK = "engineering-codex"
DESK_SPEC_PATH = REPO / "ops/config/engineering-codex-desk.v1.json"
DEDICATED_REGISTRY_PATH = Path.home() / ".config/carr/hermes-desks.json"
AUTHORIZED_WRITABLE_ROOTS = ("/Users/booko/carr-system/.git", "/Users/booko/carr-system/out")
AUTHORIZED_CODEX_CONFIG_OVERRIDES = (
    "sandbox_workspace_write.network_access=true",
    'features.network_proxy={enabled=true,domains={"github.com"="allow","api.github.com"="allow"}}',
)
EXECUTOR_TIMEOUT_SECONDS = 900


class DispatchRefusal(ValueError):
    pass


def validate_job(job: dict) -> dict:
    if not isinstance(job, dict) or set(job) != {"repository", "revision", "permitted_work", "checks"}:
        raise DispatchRefusal("job requires repository, revision, permitted_work and checks")
    if job["repository"] != "jbookout/carr-system" or not re.fullmatch(r"[0-9a-f]{40}", str(job["revision"])):
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


def _git_common_dir() -> Path:
    dotgit = REPO / ".git"
    if dotgit.is_dir():
        common = dotgit.resolve()
    elif dotgit.is_file():
        try:
            marker = dotgit.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DispatchRefusal("Engineering repository Git metadata is unavailable") from exc
        prefix = "gitdir: "
        if not marker.startswith(prefix) or "\n" in marker:
            raise DispatchRefusal("Engineering repository Git metadata is malformed")
        git_dir = Path(marker[len(prefix):])
        if not git_dir.is_absolute():
            git_dir = REPO / git_dir
        git_dir = git_dir.resolve()
        commondir = git_dir / "commondir"
        if commondir.is_file():
            try:
                common_ref = commondir.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise DispatchRefusal("Engineering repository common Git metadata is unavailable") from exc
            if not common_ref or "\n" in common_ref:
                raise DispatchRefusal("Engineering repository common Git metadata is malformed")
            common = (git_dir / common_ref).resolve()
        else:
            common = git_dir
    else:
        raise DispatchRefusal("Engineering repository Git metadata is unavailable")
    if not common.is_dir() or not (common / "HEAD").is_file() or common.name != ".git":
        raise DispatchRefusal("Engineering repository common Git metadata is invalid")
    return common


def _dedicated_writable_roots() -> list[str]:
    common = _git_common_dir()
    shared_out = (common.parent / "out").resolve()
    if not shared_out.is_dir():
        raise DispatchRefusal("Engineering shared output root is unavailable")
    roots = [str(common), str(shared_out)]
    if tuple(roots) != AUTHORIZED_WRITABLE_ROOTS:
        raise DispatchRefusal("Engineering desk writable roots do not match the authorized machine boundary")
    return roots


def _desk_spec() -> dict:
    try:
        value = json.loads(DESK_SPEC_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchRefusal("tracked Engineering desk configuration is unavailable") from exc
    expected = {"schema_version", "name", "kind", "family", "effort", "cwd", "sandbox", "room_seat"}
    if not isinstance(value, dict) or set(value) != expected:
        raise DispatchRefusal("tracked Engineering desk configuration has an unsupported shape")
    if (value["schema_version"] != "engineering-codex-desk.v1" or value["name"] != ENGINEERING_DESK
            or value["kind"] != "codex-session" or value["cwd"] != "{{REPO}}"
            or value["room_seat"] is not None):
        raise DispatchRefusal("tracked Engineering desk configuration is not dedicated and unseated")
    for field in ("family", "effort", "sandbox"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise DispatchRefusal("tracked Engineering desk configuration is incomplete")
    return value


def install_dedicated_codex_desk(registry: desks.Registry) -> dict:
    """Bootstrap only the tracked unseated desk, then return its exact readback."""
    spec = _desk_spec()
    entry = registry.register(spec["name"], spec["kind"], family=spec["family"], effort=spec["effort"],
                              cwd=str(REPO), sandbox=spec["sandbox"],
                              add_dirs=_dedicated_writable_roots())
    return _dedicated_codex_desk(registry)


def _dedicated_codex_desk(registry: desks.Registry) -> dict:
    try:
        entry = registry.resolve(ENGINEERING_DESK)
    except desks.DeskError as exc:
        raise DispatchRefusal("dedicated Engineering Codex desk is unavailable") from exc
    spec = _desk_spec()
    allowed_fields = {
        "name", "kind", "family", "effort", "cwd", "sandbox", "add_dirs", "room_seat", "thread_id", "registered_at",
        "last_seen", "last_live", "last_auth", "last_auth_at",
    }
    if set(entry) - allowed_fields:
        raise DispatchRefusal("dedicated Engineering desk has an unapproved execution field")
    expected = {"kind": spec["kind"], "family": spec["family"], "effort": spec["effort"],
                "cwd": str(REPO), "sandbox": spec["sandbox"],
                "add_dirs": _dedicated_writable_roots()}
    if entry.get("name") != ENGINEERING_DESK or any(entry.get(key) != value for key, value in expected.items()) or entry.get("room_seat") is not None:
        raise DispatchRefusal("dedicated Engineering desk is not a modeled Codex session")
    if (entry.get("thread_id") is not None and not isinstance(entry["thread_id"], str)) or (
            "registered_at" in entry and not isinstance(entry["registered_at"], str)) or (
            "last_seen" in entry and not isinstance(entry["last_seen"], str)) or (
            "last_live" in entry and not isinstance(entry["last_live"], bool)) or (
            "last_auth" in entry and entry["last_auth"] is not None and not isinstance(entry["last_auth"], bool)) or (
            "last_auth_at" in entry and not isinstance(entry["last_auth_at"], str)):
        raise DispatchRefusal("dedicated Engineering desk metadata is invalid")
    return entry


def _safe_child_env() -> dict[str, str]:
    return {key: os.environ[key] for key in ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TERM") if os.environ.get(key)}


def run(job: dict, *, dispatch_fn=dispatch.dispatch, registry: desks.Registry | None = None) -> dict:
    validate_job(job)
    _dedicated_codex_desk(registry or desks.Registry(DEDICATED_REGISTRY_PATH))
    prepared = codex_checkout.prepare("new:engineering-" + uuid.uuid4().hex[:12], str(REPO), _safe_child_env())
    if prepared["checkout_revision"] != job["revision"]:
        raise DispatchRefusal("repository advanced before dispatch; job revision is stale")
    prompt = (
        "Read standing-context before work. Execute this job only in " + prepared["checkout_path"] + ". "
        "Return one JSON object with repository, revision, permitted_work, checks and outcome. "
        "Report the work actually performed as a subset of permitted_work. Checks contain check_ref, state "
        "and evidence_refs. Outcome contains status (claimed_complete, failed or blocked), artifact_refs, "
        "evidence_refs, detail and source_evidence (worktree_ref, branch_ref, source_sha, evidence_refs). "
        "Do not merge or deploy. Never claim a check passed without its actual output.\n" + json.dumps(job)
    )
    row = dispatch_fn(ENGINEERING_DESK, prompt, env=_safe_child_env(), fresh=True,
        cwd=prepared["checkout_path"], config_overrides=AUTHORIZED_CODEX_CONFIG_OVERRIDES,
        codex_timeout_s=EXECUTOR_TIMEOUT_SECONDS, writes=job["permitted_work"]["paths"])
    if row.get("status") != "completed":
        raise DispatchRefusal("engineering desk did not complete")
    try:
        result = validate_result(json.loads(row["result"]), job)
    except (KeyError, json.JSONDecodeError) as exc:
        raise DispatchRefusal("engineering desk did not return one job result") from exc
    source = result["outcome"].get("source_evidence", {})
    if (source.get("worktree_ref") != prepared["checkout_path"]
            or source.get("branch_ref") != prepared["checkout_branch"]):
        raise DispatchRefusal("result belongs to a different checkout")
    tree = prepared["checkout_path"]
    head = subprocess.check_output(['git', '-C', tree, 'rev-parse', 'HEAD'], text=True).strip()
    if source.get("source_sha") != head:
        raise DispatchRefusal("result revision does not match its checkout")
    changed = subprocess.check_output(['git', '-C', tree, 'diff', '--no-renames', '--name-only', '-z', job["revision"]], text=True)
    untracked = subprocess.check_output(['git', '-C', tree, 'ls-files', '--others', '--exclude-standard', '-z'], text=True)
    paths = [path for path in (changed + untracked).split('\0') if path]
    if paths and "repository:write-declared-scope" not in job["permitted_work"]["actions"]:
        raise DispatchRefusal("read-only job changed files")
    if any(not any(fnmatchcase(path, allowed) for allowed in job["permitted_work"]["paths"]) for path in paths):
        raise DispatchRefusal("job changed files outside permitted work")
    return result


def main() -> int:
    result: dict
    try:
        if sys.argv[1:] == ["--preflight"]:
            _dedicated_codex_desk(desks.Registry(DEDICATED_REGISTRY_PATH))
            result = {"ok": True}
        elif sys.argv[1:] == ["--install-desk"]:
            result = {"ok": True, "desk": install_dedicated_codex_desk(desks.Registry(DEDICATED_REGISTRY_PATH))}
        elif not sys.argv[1:]:
            result = run(json.load(sys.stdin))
        else:
            raise DispatchRefusal("unsupported arguments")
    except (DispatchRefusal, desks.DeskError, ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
