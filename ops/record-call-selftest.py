#!/usr/bin/env python3
"""ops/record-call-selftest.py — the interface test for lib/record_call.py, the
one way an unattended script calls a record-layer verb.

Every case drives call_verb() through an injected runner that stands where
`./run.sh call` would, replaying what tools/call-verb.py and local-verb.mjs
actually print and exit with. Node renders the CLI's guidance suffix; no
network or credential is used.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib import record_call  # noqa: E402
from lib.record_call import FAILED, OK, REFUSED, TRANSIENT, call_verb  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, got: object = None) -> None:
    print(("ok    " if cond else "FAIL  ") + name + ("" if cond else f"  (got {got!r})"))
    if not cond:
        FAILS.append(name)


class Runner:
    """Replays one `./run.sh call` result and remembers how it was asked."""

    def __init__(self, rc: int = 0, stdout: str | bytes = "", stderr: str | bytes = "",
                 raises: BaseException | None = None):
        self.rc, self.stdout, self.stderr, self.raises = rc, stdout, stderr, raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.rc, self.stdout, self.stderr)


def reply(obj: object) -> str:
    return json.dumps(obj, indent=2) + "\n"


# ── outcomes ──────────────────────────────────────────────────────────────

r = call_verb("read-loop", {"id": 1}, runner=Runner(0, reply({"id": 1, "status": "open"})))
check("a JSON reply on exit 0 is ok and carries the parsed reply",
      r.kind == OK and r.ok and r.reply == {"id": 1, "status": "open"}, r)

r = call_verb("update-loop", {}, runner=Runner(0, reply({"ok": False, "kind": "conflict"})))
check("ok:false on exit 0 is a refusal, never success",
      r.kind == REFUSED and not r.ok and r.reply == {"ok": False, "kind": "conflict"}, r)

r = call_verb("update-loop", {}, runner=Runner(0, reply({"error": "version_conflict"})))
check("an error key on exit 0 is a refusal", r.kind == REFUSED and not r.ok, r)

r = call_verb("update-loop", {}, runner=Runner(0, reply({"ok": True, "error": None})))
check("a null error key is not a refusal", r.kind == OK, r)

tool_error = 'TOOL ERROR {\n  "error": "unknown_tool",\n  "name": "read-x"\n}\n'
r = call_verb("read-x", {}, runner=Runner(1, "", tool_error))
check("a Worker ToolError is a refusal with its payload as the reply",
      r.kind == REFUSED and r.reply == {"error": "unknown_tool", "name": "read-x"}, r)
check("the refusal names its error code", r.error == "unknown_tool", r.error)

guidance = subprocess.run(
    ["node", "--input-type=module", "-e",
     'import {humanOnlyGuidance} from "./mcp-server/human-only-hint.mjs"; '
     'process.stdout.write(humanOnlyGuidance("synthetic-verb"));'],
    cwd=REPO, capture_output=True, text=True, check=True).stdout
human_only = {"error": "human_only", "name": "synthetic-verb"}
r = call_verb("synthetic-verb", {}, runner=Runner(1, "", "TOOL ERROR " + reply(human_only) + guidance))
check("the complete CLI refusal preserves the payload before its guidance",
      r.kind == REFUSED and r.reply == human_only and r.error == "human_only", r)
check("the complete CLI refusal describes its structured error",
      "human_only" in r.describe() and "synthetic-verb" in r.detail, r.describe())

r = call_verb("synthetic-verb", {}, runner=Runner(1, "", "TOOL ERROR {broken\n" + guidance))
check("a malformed CLI ToolError stays refused without an invented payload",
      r.kind == REFUSED and r.reply is None and r.error is None, r)

unreachable = ("could not reach the deployed Worker at https://api.doctorcre.com/mcp: fetch failed\n"
               "The default path fails here rather than silently opening a direct database connection.\n")
r = call_verb("add-loop", {}, runner=Runner(1, "", unreachable))
check("an unreachable Worker is transient", r.kind == TRANSIENT and r.reply is None, r)

r = call_verb("add-loop", {}, runner=Runner(1, "", "HTTP 503 from https://api.doctorcre.com/mcp: {}\n"))
check("an HTTP 5xx from the Worker is transient", r.kind == TRANSIENT, r)

r = call_verb("add-loop", {}, runner=Runner(1, "", "non-JSON response (HTTP 502) from https://x: <html>\n"))
check("a non-JSON 5xx gateway page is transient", r.kind == TRANSIENT, r)

r = call_verb("add-loop", {}, timeout=7, runner=Runner(raises=subprocess.TimeoutExpired("run.sh", 7)))
check("a timeout is transient and says how long it waited",
      r.kind == TRANSIENT and "7" in r.detail, r)

r = call_verb("add-loop", {}, runner=Runner(1, "", 'RPC ERROR {"code": -32600}\n'))
check("an RPC error is a failure", r.kind == FAILED, r)

r = call_verb("add-loop", {}, runner=Runner(1, "", "HTTP 401 from https://x: {}\n"))
check("an HTTP 4xx from the Worker is a failure, not transient", r.kind == FAILED, r)

r = call_verb("add-loop", {}, runner=Runner(0, "Traceback: something\n"))
check("non-JSON stdout on exit 0 is a failure", r.kind == FAILED, r)

r = call_verb("add-loop", {}, runner=Runner(0, ""))
check("empty stdout on exit 0 is a failure: no reply is not a success", r.kind == FAILED, r)

r = call_verb("add-loop", {}, runner=Runner(raises=FileNotFoundError(2, "No such file")))
check("a runner that cannot start is a failure", r.kind == FAILED, r)

r = call_verb("read-loop", {}, runner=Runner(0, reply({"id": 1}).encode(), b""))
check("a runner returning bytes is decoded", r.kind == OK and r.reply == {"id": 1}, r)

r = call_verb("read-loop", {}, runner=Runner(0, reply("plain text")))
check("a JSON string reply is ok", r.kind == OK and r.reply == "plain text", r)

# ── the route ─────────────────────────────────────────────────────────────

runner = Runner(0, reply({}))
call_verb("add-loop", {"body": "x"}, timeout=45, runner=runner)
argv, kwargs = runner.calls[0]
check("the only route is `run.sh call <verb> '<json>'`",
      argv == [str(REPO / "run.sh"), "call", "add-loop", json.dumps({"body": "x"})], argv)
check("it runs from the repo with stdin closed and the given timeout",
      kwargs.get("cwd") == str(REPO) and kwargs.get("stdin") == subprocess.DEVNULL
      and kwargs.get("timeout") == 45, kwargs)

runner = Runner(0, reply({}))
call_verb("add-loop", {}, env={"HOME": "/h", "PATH": "/p", "CARR_MCP_CLIENT_PROFILE": "x"},
          runner=runner)
check("an inherited client profile never leaks into the call",
      runner.calls[0][1]["env"] == {"HOME": "/h", "PATH": "/p"}, runner.calls[0][1]["env"])

runner = Runner(0, reply({}))
call_verb("project-room-queue", {}, env={"HOME": "/h"}, profile="hermes-projector", runner=runner)
check("a profile is selected only by the profile option",
      runner.calls[0][1]["env"] == {"HOME": "/h", "CARR_MCP_CLIENT_PROFILE": "hermes-projector"},
      runner.calls[0][1]["env"])

# ── what a failure line may carry ─────────────────────────────────────────

token = "ghp_" + "A" * 36
r = call_verb("add-loop", {}, runner=Runner(1, "", f"boom: authorization failed for {token}\n"))
check("a failure detail is redacted", token not in r.detail and "boom" in r.detail, r.detail)

r = call_verb("add-loop", {}, runner=Runner(1, "", "x" * 5000 + "\nlast line\n"))
check("a failure detail is one bounded line", "\n" not in r.detail and len(r.detail) <= 300, len(r.detail))

check("describe() names the verb and the outcome",
      call_verb("add-loop", {}, runner=Runner(1, "", unreachable)).describe().startswith(
          "add-loop transient:"), None)

check("the module exposes exactly four outcomes",
      set(record_call.OUTCOMES) == {OK, REFUSED, FAILED, TRANSIENT}, record_call.OUTCOMES)

if FAILS:
    print(f"record-call-selftest: {len(FAILS)} FAILED")
    sys.exit(1)
print("record-call-selftest: all checks passed")
