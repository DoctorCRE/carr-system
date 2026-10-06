#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""jev-supervisor.py — the in-session Jev checks from open loop #629, one hook.

WHAT IT IS. Loop #629 listed twenty-five Jev checks for supervising a coding
agent: first the local Qwen workhorse (`flash`), then Claude sessions too,
because every check is model-agnostic. The judgments live in libraries under
ops/ (jev_session_watch, jev_done_checks, jev_intake, jev_best_of, jev_notebook).
This file is only the dispatcher that a Claude Code hook calls: it looks at the
event, runs the cheap deterministic trigger each check owns, and asks Jev only
when one fires.

IT NEVER BLOCKS. Exit status is always 0 and nothing here sets a decision.
Joe's 2026-08-23 Stop-gate rationing leaves exactly three hooks able to reopen
a turn and this is not one of them. Every check is shadow-first per
ops/jev_judge.py: the answer is recorded to out/jev-judge.jsonl beside what the
session actually did, so a threshold can be measured before anything acts.

TWO MODES, chosen by the CARR_JEV_SUPERVISOR environment variable:
  advise (default) — hand the session a short advisory line (the likely buggy
                     line, the real path it probably meant, the tests worth
                     running, "you are looping"). Joe, 2026-09-24 (decision
                     5ec806a4, "every jev check in the system too is not a
                     shadow"): every session gets this, Claude included, not
                     just the flash sessions it was written for.
  shadow           — record only; the session sees nothing. Set
                     CARR_JEV_SUPERVISOR=shadow explicitly to go back to this.

EVENTS
  PostToolUse: one boundary request batches triggered security, failure,
               path, bug, duplicate, and test questions. A quiet tool result
               may run the throttled progress watch.
  Stop: one request batches a new done claim and new diff review.

Budget: each Jev call carries its own short timeout inside the library, and
the whole hook stops starting new checks once BUDGET_SECONDS is spent, so a slow
vendor costs a bounded delay rather than a stalled session.
"""
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_SECONDS = 20.0
# JOE, 2026-09-24 (decision 5ec806a4): "every jev check in the system too is
# not a shadow". advise is now the default for every session, Claude included;
# CARR_JEV_SUPERVISOR still overrides it explicitly (e.g. back to "shadow" or
# "off") when a session wants that.
MODE = os.environ.get("CARR_JEV_SUPERVISOR", "advise").strip().lower()
MAX_OUTPUT_CHARS = 12000

TEST_COMMAND = re.compile(r"\b(pytest|unittest|selftest|test[-_]\S*\.py|npm (run )?test|node --test|"
                          r"go test|cargo test|jest|vitest|mocha|-selftest\.py)\b")
MISSING_FILE = re.compile(r"(No such file or directory|does not exist|File not found|"
                          r"ENOENT|cannot find the path)", re.I)
MISSING_PATH_TOKEN = re.compile(r"['\"`]?((?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-]+)+|[\w\-]+\.\w{1,5})['\"`]?")
TRACEBACK_FILE = re.compile(r'File "([^"]+)", line (\d+)')
NODE_FRAME = re.compile(r"\(?(/[^\s():]+\.(?:m?js|ts)):(\d+):\d+\)?")
NEW_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|function)\s+([A-Za-z_]\w*)\s*\(", re.M)
TEST_PATH = re.compile(r"(^|/)(tests?/|test[-_][^/]*|[^/]*[-_.]test\.|[^/]*selftest)")


def _lib(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = _lib("typesafe_client")


def _text(value):
    """A tool response as plain text, whatever shape the harness gave it."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [value.get(k) for k in ("stdout", "stderr", "output", "content", "error", "result")]
        joined = "\n".join(p if isinstance(p, str) else json.dumps(p) for p in parts if p)
        return joined or json.dumps(value)[:MAX_OUTPUT_CHARS]
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    return str(value)


def _exit_code(response):
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "returncode", "code"):
            if isinstance(response.get(key), int):
                return response[key]
        if response.get("interrupted") or response.get("is_error"):
            return 1
    return None


def _git_root(cwd):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd or ".",
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _task(transcript):
    if not transcript:
        return ""
    try:
        return _lib("jev_code_review").latest_task(transcript) or ""
    except Exception:
        return ""


def _notable(result):
    """Whether a check's result is worth an advisory line in advise mode."""
    if not isinstance(result, dict):
        return False
    verdict = str(result.get("verdict", ""))
    if verdict == "unavailable" and result.get("check") in {
            "boundary_judgment", "stop_boundary", "inspect_tool_event",
            "inspect_stop_boundary"}:
        return True
    quiet = {"ok", "clear", "clean", "not_triggered", "skipped", "unavailable", "none",
             "no_claim", "supported", "local", "progressing", "no_match", "low", "solid",
             "picked", "routed", "matched", "none_fits", "not_a_failure", "no_source",
             "not_a_new_function", "no_candidates", "no_tests_found", "none_relevant"}
    return verdict not in quiet


def _line(result):
    check = result.get("check", "?")
    detail = result.get("detail") or {}
    hint = detail.get("advice") or detail.get("hint") or detail.get("summary") or ""
    if not hint:
        compact = {k: v for k, v in detail.items() if isinstance(v, (str, int, float, list)) and v}
        hint = json.dumps(compact)[:400]
    return f"[jev {check}] {result.get('verdict')}: {hint}"[:600]


class Run:
    def __init__(self, receipt_path=None):
        self.started = time.monotonic()
        self.results = []
        self.receipt_path = receipt_path or os.path.join(REPO, "out", "jev-boundary-decisions.jsonl")

    def left(self):
        return BUDGET_SECONDS - (time.monotonic() - self.started)

    def do(self, fn, *args, **kwargs):
        name = getattr(fn, "__name__", "?")
        if self.left() <= 1.0:
            if name in {"inspect_tool_event", "inspect_stop_boundary"}:
                self._unavailable(name, "time_budget_exhausted")
            return None
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # a library bug must never reach the session
            if name in {"inspect_tool_event", "inspect_stop_boundary"}:
                self._unavailable(name, "inspection_error")
                return None
            result = {"check": name, "verdict": "unavailable",
                      "confidence": None, "escalate": False, "detail": {"error": repr(exc)[:300]}}
        if isinstance(result, dict):
            self.results.append(result)
        return result

    def _unavailable(self, name, reason):
        self.results.append({"check": "boundary_judgment", "verdict": "unavailable",
                             "confidence": None, "escalate": True,
                             "detail": {"reason": reason,
                                        "advice": f"{name} unavailable ({reason}); inspect boundary manually"}})
        receipt = {"schema": "jev-boundary-decision/v1", "family": name,
                   "status": "unavailable", "reason": reason, "questions": [],
                   "triggers": [], "outcomes": [{"check": "boundary_judgment",
                                              "verdict": "unavailable"}]}
        try:
            os.makedirs(os.path.dirname(self.receipt_path), exist_ok=True)
            with open(self.receipt_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(receipt, sort_keys=True) + "\n")
        except OSError:
            pass


def post_tool_use(payload, run):
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    response = payload.get("tool_response")
    out = _text(response)[-MAX_OUTPUT_CHARS:]
    transcript = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()
    root = _git_root(cwd) or cwd
    task = _task(transcript)
    watch = _lib("jev_session_watch")

    code = _exit_code(response)
    findings = run.do(watch.inspect_tool_event, tool, ti, out, code, task,
                      root, transcript)
    if isinstance(findings, list):
        run.results.extend(row for row in findings if isinstance(row, dict))


def progress(payload, run):
    """Local predicates run independently of paid-call admission."""
    transcript = payload.get("transcript_path") or ""
    if transcript:
        watch = _lib("jev_session_watch")
        run.do(watch.watch_progress, transcript, _task(transcript),
               elapsed_seconds=payload.get("elapsed_seconds"),
               budget_seconds=payload.get("budget_seconds"),
               artifact_before=payload.get("artifact_before"),
               artifact_after=payload.get("artifact_after"))


NOTIFICATION_WRAPPER = re.compile(
    r"(?:<task-notification(?:\s[^>]*)?>[\s\S]*</task-notification>|"
    r"<ci-monitor-event(?:\s[^>]*)?>[\s\S]*</ci-monitor-event>|"
    r"\[SYSTEM NOTIFICATION(?:\s[^\]]*)?\][\s\S]*)")


def _request_provenance(rec):
    """Classify a user request; tool results are not requests.

    Provenance flags and origins take precedence over text. Only a leading
    notification wrapper counts; mentioning its name in human prose does not.
    """
    if rec.get("type") != "user":
        return None
    message = rec.get("message")
    if not isinstance(message, dict):
        return "unknown"
    content = message.get("content")
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return None
        content = "\n".join(b["text"] for b in blocks
                            if b.get("type") == "text" and isinstance(b.get("text"), str))
    origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    if (rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary")
            or origin.get("kind") not in (None, "", "human", "user", "keyboard")):
        return "notification"
    if not isinstance(content, str) or not content.strip():
        return "unknown"
    if NOTIFICATION_WRAPPER.fullmatch(content.strip()):
        return "notification"
    return "human"


def _transcript_records(transcript):
    """Read a bounded tail once per consumer; unavailable provenance stays unknown."""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 2_000_000))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except (OSError, TypeError, ValueError):
        return []
    records = []
    for raw in lines:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _last_test_evidence(transcript):
    """Completed tests after the latest human request in the tail.

    If that request is outside the bounded tail, omit test evidence rather than
    risk attributing an earlier task's test to this one.
    """
    evidence = {}
    commands = {}
    tests = []
    saw_request = False
    for rec in _transcript_records(transcript):
        message = rec.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if _request_provenance(rec) == "human":
            saw_request = True
            commands.clear()
            tests.clear()
            continue
        if not saw_request:
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmd = str((block.get("input") or {}).get("command", ""))
                if TEST_COMMAND.search(cmd):
                    commands[block.get("id")] = cmd
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in commands:
                code = int(block["is_error"]) if isinstance(block.get("is_error"), bool) else None
                tests.append((commands.pop(block["tool_use_id"]),
                              _text(block.get("content"))[-6000:], code))
    if not tests:
        return evidence
    command, output, code = tests[-1]
    evidence = {"test_command": command, "test_output": output,
                "test_run_count": len(tests),
                "test_runs": [{"command": cmd, "output": out, "exit_code": result}
                              for cmd, out, result in tests],
                "test_failure_count": sum(result != 0 for _, _, result in tests if result is not None)}
    if code is not None:
        evidence["test_exit_code"] = code
    # Keep the failure signal even when many later successful runs crowd the
    # evidence budget. The count covers failures whose excerpts do not fit.
    limit = 3900
    header = (f"{len(tests)} completed test runs; "
              f"{evidence['test_failure_count']} failed. Chronological excerpts:\n")
    selected = set(range(len(tests)))
    def excerpt(i):
        test_command, test_output, result = tests[i]
        status = "FAIL" if result else "PASS" if result == 0 else "UNKNOWN"
        return f"{i + 1}. {status} {test_command[:250]}\n{test_output[-900:]}\n"
    lines = {i: excerpt(i) for i in selected}
    if len(header) + sum(map(len, lines.values())) > limit:
        # Reserve the latest result, then retain as many recent failures and
        # their later same-command passes as fit. Counts cover omitted runs.
        important = {len(tests) - 1}
        for i, (test_command, _, result) in enumerate(tests):
            if result not in (None, 0):
                important.add(i)
                for j in range(len(tests) - 1, i, -1):
                    if tests[j][0] == test_command and tests[j][2] == 0:
                        important.add(j)
                        break
        kept = set()
        for i in sorted(important, reverse=True):
            if len(header) + sum(len(lines[j]) for j in kept) + len(lines[i]) <= limit:
                kept.add(i)
        selected = kept
    omitted = len(tests) - len(selected)
    if omitted:
        header += f"{omitted} selected excerpts omitted by size limit.\n"
        evidence["test_history_truncated"] = True
    evidence["test_history"] = (header + "".join(lines[i] for i in sorted(selected)))[:limit]
    return evidence


def stop(payload, run):
    transcript = payload.get("transcript_path") or ""
    final = str(payload.get("last_assistant_message") or "")
    cwd = payload.get("cwd") or os.getcwd()
    root = _git_root(cwd)
    task = _task(transcript)
    if not final and transcript:
        try:
            watch = _lib("jev_session_watch")
            final = watch.last_assistant_text(transcript) or ""
        except Exception:
            final = ""
    done = _lib("jev_done_checks")
    evidence = _last_test_evidence(transcript) if transcript else {}
    # The pre-launch contract's artifacts are read from disk by the predicate
    # itself; the payload carries no evidence a session could author.
    criteria = _lib_acceptance_contract(task).get("criteria")
    if criteria:
        evidence.update({"criteria": criteria, "claim_scope": "current_completion", "root": root or cwd})
    diff = ""
    if root:
        try:
            diff = subprocess.run(["git", "diff", "HEAD", "-U3"], cwd=root, capture_output=True,
                                  text=True, timeout=10).stdout[:40000]
            evidence["diff_stat"] = subprocess.run(["git", "diff", "HEAD", "--stat"], cwd=root,
                                                   capture_output=True, text=True, timeout=10).stdout[-3000:]
        except Exception:
            pass
    findings = run.do(done.inspect_stop_boundary, final, evidence, diff, task,
                      payload.get("session_id") or "")
    if isinstance(findings, list):
        run.results.extend(row for row in findings if isinstance(row, dict))


def _lib_acceptance_contract(task):
    spec = importlib.util.spec_from_file_location("acceptance_checks", os.path.join(REPO, "lib", "acceptance_checks.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.contract(task)


def fact_boundary(payload, run):
    """Source-grounded claim check at the completion-report and record-write
    acknowledgement boundaries (ops/jev_fact_boundary.py). Kept apart from
    stop() and post_tool_use(): it owns its trigger, retrieval and decision,
    runs on what budget they leave, and a failure here returns quietly.
    OFF unless CARR_JEV_FACT_BOUNDARY=on: every search-doctrine call it makes
    side-writes a log_retrieval_query row that retrieval curation mines, so
    it stays opt-in until a non-logging read path exists."""
    if os.environ.get("CARR_JEV_FACT_BOUNDARY", "off").strip().lower() != "on":
        return None
    try:
        lib = _lib("jev_fact_boundary")
        boundary = lib.boundary_from_hook(payload)
    except Exception:
        return None
    if not boundary:
        return None
    return run.do(lib.check_boundary, boundary, budget_seconds=run.left() - 1.0)


# JUDGMENT POINTS ONLY (Joe, 2026-09-25: Jev at judgment calls, not every turn;
# 2026-10-03: "fix whatever is causing the heavy usage now"). Measured that
# morning: one orchestrator session paid for ~300 Jev requests in an hour, most
# of them the injection screen re-reading its own local command output (rule
# text is full of "never"/"always") and turn-end claim checks on background
# notification turns. A tool result is a judgment point only when it brings in
# outside content or failed. Unknown tool provenance retains the library's
# deterministic security floor; local Read/Grep and simple shell readers
# bypass inspection when they carry no failure evidence.
# A turn end is checked unless the latest request is a known notification.
# The shared daily paid-call cap owns the spending bound.
LOCAL_READ_COMMANDS = {"cat", "ls", "pwd", "head", "tail", "wc"}


def _local_shell_read(tool_input):
    """Trust only a single known reader without shell evaluation or chaining."""
    if not isinstance(tool_input, dict):
        return False
    command = tool_input.get("command")
    if not isinstance(command, str) or any(c in command for c in "$`;|&><\n()"):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return bool(words) and words[0] in LOCAL_READ_COMMANDS


def judgment_point(event, payload):
    """Whether this hook event is worth a paid Jev request at all."""
    if event == "PostToolUse":
        name = (payload.get("tool_name") or "").lower()
        if name not in {"bash", "read", "grep"}:
            return True
        if name == "bash" and not _local_shell_read(payload.get("tool_input")):
            return True
        response = payload.get("tool_response")
        code = _exit_code(response)
        if code not in (None, 0):
            return True
        output = _text(response)[-MAX_OUTPUT_CHARS:]
        watch = _lib("jev_session_watch")
        return bool(MISSING_FILE.search(output) or watch.FAILURE_MARKERS.search(output))
    if event == "Stop":
        for rec in reversed(_transcript_records(payload.get("transcript_path") or "")):
            provenance = _request_provenance(rec)
            if provenance is not None:
                return provenance != "notification"
        return True  # unknown provenance retains completion checking
    return False


# The paid sites this hook dispatches, for a site-budget pause.
SUPERVISOR_SITES = ("jev_session_watch", "jev_fact_boundary")


def _quiet_unavailable(results, session):
    """Collapse typed vendor/budget outages; retain local and policy failures.

    While a cap or site budget holds, the client's pause_notice says so once
    per session per window with the reset time. Typed vendor outages are said
    once per session per hour. Local/policy failures and verdicts always print. When the notice store
    cannot be read the old per-call lines are kept, so a broken store never
    hides an outage.
    """
    def collapsible(result):
        return (result.get("verdict") == "unavailable" and
                (result.get("detail") or {}).get("reason") in
                {"vendor_unavailable", "daily_paid_call_cap", "hourly_paid_call_cap",
                 "site_daily_budget", "site_hourly_budget"})
    unavailable = [r for r in results if collapsible(r)]
    if not unavailable:
        return [_line(r) for r in results]
    try:
        client = _lib("typesafe_client")
        notice = (client.pause_notice(session, sites=SUPERVISOR_SITES)
                  if client.active_pause(sites=SUPERVISOR_SITES) else client.outage_notice(session))
    except Exception:
        return [_line(r) for r in results]
    lines = [_line(r) for r in results if not collapsible(r)]
    return ([notice] if notice else []) + lines


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict) or MODE == "off" or payload.get("session_id") == "selftest":
        return 0
    # The registry/client owns each site's policy, including mixed policies.
    enabled = {site for site in SUPERVISOR_SITES if POLICY.call_site_enabled(site)}
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    if event == "Stop" and payload.get("stop_hook_active"):
        return 0
    run = Run()
    try:
        if event == "PostToolUse":
            progress(payload, run)
            if "jev_session_watch" in enabled and judgment_point(event, payload):
                post_tool_use(payload, run)
            # The optional fact library owns its record-write trigger, including
            # successful acknowledgements through Bash and MCP.
            if "jev_fact_boundary" in enabled:
                fact_boundary(payload, run)
        elif event == "Stop" and judgment_point(event, payload):
            stop(payload, run)
            if "jev_fact_boundary" in enabled:
                fact_boundary(payload, run)
    except Exception:
        return 0
    if MODE != "advise":
        return 0
    lines = _quiet_unavailable([r for r in run.results if _notable(r)], payload.get("session_id"))
    if not lines:
        return 0
    text = "\n".join(lines[:4])
    if event == "PostToolUse":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": text}}))
    else:
        print(json.dumps({"systemMessage": text}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
