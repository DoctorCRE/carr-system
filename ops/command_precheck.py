"""Deterministic shell precheck: report missing repository paths without denying.

Failures are advisory and never prevent the command from running.
"""

import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "command-precheck.jsonl")
LOG_MAX_BYTES = 4 * 1024 * 1024  # a count cap is not a size cap; this is a size cap

# Above this, the warning is printed. Deliberately high: a warning in front of
# every command trains a session to ignore warnings, which is worse than none.
# Re-derive it from LOG once real traffic has accumulated.
WARN_AT = 0.80

# Every statement is one of these and none of the writers below: pure read.
READS = re.compile(
    r"^(?:grep|rg|cat|head|tail|ls|wc|awk|sed -n|find|echo|printf|jq|pwd|which|"
    r"git (?:show|log|diff|status|branch|rev-parse|ls-files|ls-tree|describe)|"
    r"gh (?:pr view|run view|issue view))\b")
WRITES = re.compile(
    r"(?:>{1,2}\s|\brm\b|\bmv\b|\bcp\b|\bmkdir\b|\btee\b|\bchmod\b|sed -i|"
    r"git (?:add|commit|push|merge|checkout|reset|rebase|stash)|"
    r"gh (?:pr (?:create|merge|edit|close)|release)|npm|psql|curl|ssh)")


def is_pure_read(command):
    """True when every statement is a known read and none is a known write."""
    if WRITES.search(command):
        return False
    statements = [s.strip() for s in re.split(r"&&|\|\||;|\n|\|", command) if s.strip()]
    return bool(statements) and all(READS.match(s) for s in statements)


# A command carrying a credential is never inspected or logged.
SENSITIVE_COMMAND = re.compile(
    r"(?i)(?:\b(?:api[_-]?key|access[_-]?token|password|passwd|secret|"
    r"authorization|bearer|pgpassword|database_url)\b\s*(?:=|:|\s+)"
    r"|postgres(?:ql)?://|\b[a-z]+://[^/\s]+@)"
)

def _log(record):
    """Append one observation. Never raises, and never fills the disk."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX_BYTES:
            # TRIM BY BYTES, NOT BY LINES. Keeping the last N lines does not
            # bound a file: one enormous line is its own last N lines, so the
            # file never shrinks and the cap silently does nothing. That is the
            # exact shape of the hook-state files that once filled this disk.
            # Read the tail, then drop the leading partial record.
            with open(LOG, "rb") as handle:
                handle.seek(-LOG_MAX_BYTES // 2, os.SEEK_END)
                tail = handle.read()
            _, _, tail = tail.partition(b"\n")
            with open(LOG, "wb") as handle:
                handle.write(tail)
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except Exception:
        pass


def repo_root(cwd):
    """The checkout a command's relative paths are written against.

    THE HOOK DOES NOT RUN WHERE THE SESSION RUNS. Hooks execute from the
    canonical checkout, so resolving `ops/foo.py` against this file's own
    location asks whether the file exists in CANONICAL — and most work here
    happens in a worktree. The first live run of this pre-check flagged a file
    the session had just written, at 0.94, because canonical had never seen it.
    A gate that cries wolf on every new file in every worktree is a gate a
    session learns to scroll past, which is worse than not having one.

    Walks up from the session's own directory to the enclosing checkout rather
    than assuming cwd IS the root, because a command can be run from a
    subdirectory while naming paths from the root. Falls back to this file's
    checkout when there is no usable cwd, which is the single-checkout case.
    """
    path = os.path.abspath(os.path.expanduser(cwd or ""))
    for _ in range(12):
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return REPO


# (facts key, reason id, label)
REASONS = (
    ("paths_that_do_not_exist", "missing_path", "the command reads a path that does not exist"),
)

# A command that makes a path is not stopped by its absence. Any creating
# operation anywhere in the command silences the missing-path warning: silence
# is safer than a confident warning about a path the command is about to make.
CREATES = re.compile(
    r"(?:>{1,2}|\bmkdir\b|\btouch\b|\btee\b|\bcp\b|\bmv\b|\bln\b|--output\b|\s-o\s|"
    r"\bgit\s+(?:checkout|clone|worktree|switch|restore|mv|apply|am|stash)\b)")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\b", re.S)


def missing_operands(command, missing):
    """The missing paths that appear as whole shell words outside heredoc bodies.

    A glob such as tools/test* is not the missing prefix tools/test, and a path
    inside a heredoc is text the shell never opens. Unparseable shell is silent.
    """
    import shlex
    try:
        words = shlex.split(HEREDOC.sub(" ", command), comments=True)
    except ValueError:
        return []
    words = {w[2:] if w.startswith("./") else w for w in words}
    return [path for path in missing if path in words]


def check(command, repo=REPO):
    """(probability, facts, reasons) — or (None, facts, {}) when nothing warns.

    No model is asked. A reason is present only when its predicate holds, with
    probability 1.0, so the caller's WARN_AT floor and output format still apply.
    """
    missing = []
    for match in dict.fromkeys(re.findall(r"(?<![\w/.-])((?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]+)", command)):
        path = match[2:] if match.startswith("./") else match
        full = os.path.abspath(os.path.join(repo, path))
        if os.path.commonpath((full, os.path.abspath(repo))) != os.path.abspath(repo):
            continue
        if path.split("/")[0] in ("ops", "hooks", "bin", "tools", "pipelines", "evals",
                                   "migrations", "mcp-server", "audits") and not os.path.exists(full):
            missing.append(path)
    facts = {"paths_that_do_not_exist": missing} if missing else {}
    if not facts:
        return None, {}, {}
    reasons = {}
    missing = facts.get("paths_that_do_not_exist") or []
    operands = missing_operands(command, missing) if missing and not CREATES.search(command) else []
    if operands:
        facts = dict(facts, paths_that_do_not_exist=operands)
        reasons["missing_path"] = 1.0
    if not reasons:
        return None, facts, {}
    return 1.0, facts, reasons


def _commands(payload):
    """Read literal commands only; dynamic JS expressions have no safe precheck."""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if tool == "Bash" and isinstance(tool_input, dict):
        command = tool_input.get("command")
        return [command] if isinstance(command, str) else []
    if tool == "exec_command" and isinstance(tool_input, dict):
        command = tool_input.get("cmd")
        return [command] if isinstance(command, str) else []
    if tool != "functions.exec":
        return []
    source = tool_input if isinstance(tool_input, str) else tool_input.get("code", "")
    if not isinstance(source, str):
        return []
    commands = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\b['\"]?cmd['\"]?\s*:\s*", source):
        try:
            command, _ = decoder.raw_decode(source[match.end():])
        except (TypeError, ValueError):
            continue
        if isinstance(command, str) and command not in commands:
            commands.append(command)
        if len(commands) == 3:
            break
    return commands


def _command_cwd(payload):
    cwd = payload.get("cwd") or payload.get("workingDirectory") or ""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if tool == "exec_command" and isinstance(tool_input, dict):
        workdir = tool_input.get("workdir")
        return workdir if isinstance(workdir, str) else cwd
    if tool != "functions.exec":
        return cwd
    source = tool_input if isinstance(tool_input, str) else tool_input.get("code", "")
    if not isinstance(source, str):
        return None
    matches = list(re.finditer(r"\b['\"]?workdir['\"]?\s*:\s*", source))
    workdirs = []
    decoder = json.JSONDecoder()
    for match in matches:
        try:
            value, _ = decoder.raw_decode(source[match.end():])
        except (TypeError, ValueError):
            return None
        if not isinstance(value, str):
            return None
        workdirs.append(value)
    # Different or dynamic nested working directories make relative facts
    # ambiguous. Silence is safer than a confident warning about another tree.
    return workdirs[0] if len(set(workdirs)) == 1 else (cwd if not matches else None)


def advisory(payload):
    """The warning text for this tool call, or None. NEVER raises, never denies.

    Returns a string the caller prints as additionalContext, so the hosting hook
    stays a thin dispatcher and every decision lives here where it is tested.
    """
    if os.environ.get("CARR_PRECHECK") == "0":
        return None
    try:
        command_cwd = _command_cwd(payload)
        if command_cwd is None:
            return None
        repo = repo_root(command_cwd)
        warnings = []
        for command in _commands(payload):
            if not command.strip() or is_pure_read(command) or SENSITIVE_COMMAND.search(command):
                continue
            probability, facts, reasons = check(command, repo)
            if probability is None:
                continue
            _log({"command": command[:400], "p": probability, "facts": facts,
                  "reasons": reasons, "repo": repo, "warned": probability >= WARN_AT})
            if probability < WARN_AT:
                break
            lines = [f"PRE-CHECK {probability:.2f} — this command looks likely to fail. "
                     "It has NOT been blocked; run it anyway if you disagree."]
            needs = {key: fact for fact, key, _ in REASONS}
            for key, reason in sorted(reasons.items(), key=lambda item: -item[1]):
                if reason < WARN_AT:
                    continue
                value = facts.get(needs.get(key), [])
                shown = value if isinstance(value, list) else [str(value)]
                for item in shown[:3]:
                    lines.append(f"  · {reason:.2f}  {item}")
            warnings.append("\n".join(lines))
            break
        return "\n".join(warnings)[:1800] if warnings else None
    except Exception:
        return None  # fails open, always
