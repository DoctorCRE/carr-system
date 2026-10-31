"""The one way an unattended script calls a record-layer verb.

Every scheduled job reaches the record layer through the same door a person
does, `./run.sh call <verb> '<json>'` (tools/call-verb.py, then
mcp-server/local-verb.mjs over HTTPS to the deployed Worker). Never a direct
database connection, and never the generic MCP call-verb passthrough.

What this module owns is the MEANING of what comes back, which each script
used to re-derive and got differently: some failed on `ok:false`, others
accepted any parseable stdout, so a refused write could read as success.
call_verb() answers with exactly one of four outcomes:

  ok         the verb answered and did what was asked; `reply` is its result.
  refused    the record layer answered and said no: a ToolError (unknown verb,
             human-only gate, bad arguments) or a reply carrying `ok: false`
             or an `error`. `reply` is the refusal payload. Retrying the same
             call will not help.
  transient  the Worker could not be reached, answered 5xx, or the call timed
             out. The same call may succeed later.
  failed     anything else: the door could not start, printed something that
             is not a reply, or exited non-zero for a reason that is neither
             of the above.

The runner is injectable and has subprocess.run's signature, so a test
replays what `run.sh call` prints instead of patching subprocess. A client
profile (CARR_MCP_CLIENT_PROFILE) is selected only by the `profile` option; an
inherited one is dropped, so ordinary traffic can never silently borrow
another identity's credential.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.secret_redaction import redact_text, sensitive_env_values

REPO = Path(__file__).resolve().parents[1]
RUN_SH = REPO / "run.sh"
PROFILE_ENV = "CARR_MCP_CLIENT_PROFILE"
DEADLINE_ENV = "CARR_RECORD_DEADLINE_MS"

OK, REFUSED, TRANSIENT, FAILED = "ok", "refused", "transient", "failed"
OUTCOMES = (OK, REFUSED, TRANSIENT, FAILED)

DETAIL_LIMIT = 300

# What local-verb.mjs prints on stderr when the Worker itself is the problem.
_TRANSIENT = re.compile(
    r"could not reach the deployed Worker"
    r"|^HTTP 5\d\d from "
    r"|^non-JSON response \(HTTP 5\d\d\)", re.M)
_TOOL_ERROR = "TOOL ERROR "

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]


@dataclass(frozen=True)
class VerbResult:
    verb: str
    kind: str
    reply: Any = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == OK

    @property
    def error(self) -> str | None:
        """The refusal's error code (`unknown_tool`, `version_conflict`, ...), if it named one."""
        if isinstance(self.reply, dict):
            code = self.reply.get("error") or self.reply.get("kind")
            return code if isinstance(code, str) else None
        return None

    def describe(self) -> str:
        """One line for a log or a finding: verb, outcome, redacted detail."""
        return f"{self.verb} {self.kind}: {self.detail}" if self.detail else f"{self.verb} {self.kind}"


def call_verb(verb: str, args: Mapping[str, Any], *, timeout: float | None = 120,
              profile: str | None = None, env: Mapping[str, str] | None = None,
              runner: Runner | None = None) -> VerbResult:
    """Call one verb through `run.sh call`. Never raises for a failed call.
    The default runner owns and reaps the complete subprocess group."""
    runner = runner or _run_process
    child_env = {k: v for k, v in (os.environ if env is None else env).items()
                 if k not in (PROFILE_ENV, DEADLINE_ENV)}
    if profile is not None:
        child_env[PROFILE_ENV] = profile
    child_env[DEADLINE_ENV] = (str(int(time.time() * 1000 + timeout * 1000))
                               if timeout is not None else "0")
    secrets = sensitive_env_values(child_env)

    def line(text: str) -> str:
        lines = redact_text(text or "", known_secrets=secrets).strip().splitlines()
        return " ".join(lines[-1].split())[-DETAIL_LIMIT:] if lines else ""

    try:
        proc = runner([str(RUN_SH), "call", verb, "-"], cwd=str(REPO), env=child_env,
                      input=json.dumps(args), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return VerbResult(verb, TRANSIENT, detail=f"timed out after {timeout:g}s")
    except OSError as exc:
        return VerbResult(verb, FAILED, detail=line(f"could not start run.sh call: {exc}"))
    stdout, stderr = _text(proc.stdout), _text(proc.stderr)

    if proc.returncode != 0:
        if _TOOL_ERROR in stderr:
            try:
                payload, _ = json.JSONDecoder().raw_decode(stderr.split(_TOOL_ERROR, 1)[1].lstrip())
            except ValueError:
                payload = _UNPARSED
            if payload is _UNPARSED:
                return VerbResult(verb, REFUSED, detail=line(stderr))
            return VerbResult(verb, REFUSED, reply=payload, detail=line(json.dumps(payload)))
        kind = TRANSIENT if _TRANSIENT.search(stderr) else FAILED
        cause = next((row for row in stderr.splitlines() if _TRANSIENT.search(row) or row.startswith("no MCP token for client profile ")), stderr or stdout)
        if cause.startswith("no MCP token for client profile "):
            cause = cause.partition(" — ")[0]
        return VerbResult(verb, kind, detail=line(cause) or f"exit {proc.returncode}")

    body = _json(stdout)
    if body is _UNPARSED:
        return VerbResult(verb, FAILED,
                          detail=line(f"no JSON reply: {stdout}") if stdout.strip() else "empty reply")
    if isinstance(body, dict) and (body.get("ok") is False or body.get("error")):
        return VerbResult(verb, REFUSED, reply=body, detail=line(json.dumps(body)))
    return VerbResult(verb, OK, reply=body)


def _run_process(argv, *, cwd, env, input, capture_output, text, timeout):
    with subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=text, start_new_session=True) as proc:
        try:
            stdout, stderr = proc.communicate(input=input, timeout=timeout)
        except BaseException:
            _stop_process_group(proc)
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _stop_process_group(proc):
    def send(sig):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        proc.communicate(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # A child may close its pipes and outlive the terminated group leader.
        send(signal.SIGKILL)
        proc.communicate()


_UNPARSED = object()


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return _UNPARSED


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def call_command(argv: list[str], verb: str, args: Mapping[str, Any], *,
                 timeout: float = 60, runner: Runner | None = None) -> tuple[bool, dict[str, Any]]:
    """Call a line-oriented command, preserving its last JSON dictionary reply.

    Unlike call_verb, this command contract requires an explicit ok:true and
    returns subprocess exception names without interpreting record outcomes.
    """
    runner = runner or subprocess.run
    try:
        done = runner(argv + [verb, json.dumps(args)], capture_output=True, text=True,
                      timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, {"error": type(exc).__name__}
    reply: dict[str, Any] = {}
    for line in reversed(done.stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            reply = parsed
            break
    return done.returncode == 0 and reply.get("ok") is True, reply
