"""SSH transport for a named Model Room desk. The receiver owns authentication."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import credential_env
from lib.credential_shape import valid_claude_token
from desks import DeskError, NAME_OK, remote_posture

REMOTE_COMMAND = 'cd "$HOME/carr-system" && python3 tools/room-bridge/claude_remote_wire.py --receive'
AUTH_CONFLICTS = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "CLAUDE_CODE_API_KEY_HELPER", "CLAUDE_CONFIG_DIR")


def _conflicts(env: dict) -> list[str]:
    return sorted(k for k, v in env.items() if v and k.startswith(AUTH_CONFLICTS))


def _parse(stdout: str) -> dict:
    try:
        result = json.loads(stdout)
    except (ValueError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}


def run_task(entry: dict, task: str, msg_id: str, *, run=subprocess.run) -> dict:
    remote_posture(entry)
    request = {"desk": entry["name"], "msg_id": msg_id, "host": entry["host"],
               "model": entry["model"], "effort": entry["effort"],
               "timeout_s": entry["timeout_s"], "permission_mode": "dontAsk", "task": task}
    try:
        proc = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                    "--", entry["host"], REMOTE_COMMAND], input=json.dumps(request),
                   text=True, capture_output=True, timeout=entry["timeout_s"] + 15)
    except subprocess.TimeoutExpired:
        return {"status": "timed_out", "detail": "remote deadline exceeded; execution state unknown, do not replay"}
    except OSError:
        return {"status": "failed", "detail": "ssh_unavailable"}
    outcome = _parse(proc.stdout)
    if outcome.get("msg_id") != msg_id or outcome.get("desk") != entry["name"]:
        return {"status": "failed", "detail": "remote_receipt_mismatch", "exit_code": proc.returncode}
    if proc.returncode != 0:
        return {"status": "failed", "detail": outcome.get("detail", "remote_execution_failed"),
                "exit_code": proc.returncode}
    if (outcome.get("status") != "completed" or outcome.get("actual_model") != entry["model"]
            or outcome.get("requested_model") != entry["model"]
            or outcome.get("effort") != entry["effort"] or not outcome.get("result")
            or not outcome.get("session_id")):
        return {"status": "failed", "detail": "remote_result_unverified"}
    return {k: outcome[k] for k in ("status", "result", "actual_model", "requested_model", "effort", "session_id")}


def receive(request: dict, *, run=subprocess.run) -> dict:
    remote_posture(request)
    if not NAME_OK.fullmatch(request.get("desk", "")) or not request.get("task", "").strip():
        raise DeskError("invalid_remote_request", "remote request requires desk and task")
    try:
        uuid.UUID(request["msg_id"])
    except (ValueError, KeyError, TypeError):
        raise DeskError("invalid_remote_request", "remote request requires a dispatch UUID") from None
    binding = {"desk": request["desk"], "msg_id": request["msg_id"]}
    env, warning = credential_env.claude_child_env()
    if env.get("CLAUDE_CODE_EFFORT_LEVEL"):
        return {**binding, "status": "failed", "detail": "remote_effort_override_refused", "exit_code": 3}
    conflicts = _conflicts(env)
    if warning or conflicts or not valid_claude_token(env.get(credential_env.CLAUDE_OAUTH_TOKEN_NAME, "")):
        return {**binding, "status": "failed", "detail": "subscription_auth_refused", "exit_code": 3}
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"), env.get("PATH", "/usr/bin:/bin")])
    claude = str(Path.home() / ".local/bin/claude")
    try:
        auth = run([claude, "auth", "status"], env=env, cwd=ROOT,
                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
        if auth.returncode != 0 or _parse(auth.stdout).get("authMethod") != "oauth_token":
            return {**binding, "status": "failed", "detail": "subscription_auth_refused", "exit_code": 3}
        proc = run([claude, "-p", "--model", request["model"], "--effort", request["effort"],
                    "--permission-mode", "dontAsk", "--output-format", "json"],
                   input=request["task"], env=env, cwd=ROOT, capture_output=True,
                   text=True, timeout=request["timeout_s"])
    except subprocess.TimeoutExpired:
        return {**binding, "status": "timed_out", "detail": "remote_job_timeout; do not replay", "exit_code": 124}
    except OSError:
        return {**binding, "status": "failed", "detail": "claude_cli_unavailable", "exit_code": 127}
    response = _parse(proc.stdout)
    if (proc.returncode != 0 or response.get("is_error") or response.get("type") != "result"
            or response.get("subtype") != "success" or not response.get("result")
            or not response.get("session_id")):
        return {**binding, "status": "failed", "detail": "claude_result_failed", "exit_code": proc.returncode or 1}
    if set(response.get("modelUsage", {})) != {request["model"]}:
        return {**binding, "status": "failed", "detail": "claude_model_mismatch", "exit_code": 1}
    return {**binding, "status": "completed", "result": response["result"],
            "session_id": response["session_id"], "actual_model": request["model"],
            "requested_model": request["model"], "effort": request["effort"]}


if __name__ == "__main__":
    if sys.argv[1:] != ["--receive"]:
        raise SystemExit("transport endpoint; use Model Room dispatch.py send DESK -")
    try:
        outcome = receive(json.load(sys.stdin))
    except (DeskError, ValueError, TypeError, AttributeError) as exc:
        print(json.dumps({"status": "failed", "detail": getattr(exc, "code", "invalid_remote_request")}))
        raise SystemExit(2)
    print(json.dumps(outcome))
    raise SystemExit(outcome.get("exit_code", 0 if outcome["status"] == "completed" else 1))
