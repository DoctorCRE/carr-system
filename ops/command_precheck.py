"""command_precheck.py — warn about a shell command BEFORE it runs. Never deny.

WHY THIS EXISTS, measured 2026-09-18 against 464 real shell calls taken from one
session's own transcript. Twenty-six of them came back an error, a refusal or a
guard block: roughly four thousand characters of error text into a session's
context before any diagnosis, and then a re-read, a fix and a retry for each.
Every one of those was knowable in advance from a fact on disk.

THE MEASUREMENT THAT DECIDES THE DESIGN, and it is the whole reason this file
gathers before it asks. Putting the command text alone in front of a judgment
has NO discriminating power: twenty-six failures and twenty-six successes both
scored a median of 0.44, and at a 0.8 threshold the question raised more false
alarms than it caught real failures. Attach the one environment fact that
decides the command and the same set separates at 0.94 against 0.17. So
ops/jev_precheck.py collects the facts first, deterministically. Since the
2026-10-04 Jev audit the facts that ever produced a warning are decided by
predicate here and no judgment is asked at all (see REASONS below).

IT IS A LIBRARY, AND THAT IS NOT A STYLE CHOICE. A file under hooks/ with a
shebang is a NEW sealed ingress, and admitting one means a mutation-registry
successor: a generated registry file, a production migration, a fixture rebuilt
from origin/main, and a disposable PostgreSQL round trip to learn the catalog
digest the migration reads back on itself. That is the heavy path. Living here
with no shebang and no main guard, invoked by a hook that already runs on every
Bash call, costs a re-digest of that one hook instead — an overlay upsert. The
frontier does not move. Do not add a shebang to this file to "make it runnable".

(The detector is a regex over the whole file and does not know what a docstring
is, so the guard construct is described here and never spelled. And note that
fullInventory enumerates TRACKED files only: an untracked entrypoint passes the
assertion and fails the moment it is staged, which is how this file's first
draft looked safe.)

IT NEVER DENIES, AND THAT IS NOT TIMIDITY. A probabilistic refusal in front of
every shell call converts a model's uncertainty into a blocked session, and the
one thing worse than a wrong command is a session that cannot run the right one.
This prints a warning and returns zero. Rule: exit 2 never appears in this file
and a test asserts it.

IT FAILS OPEN ON EVERYTHING. No credential, service down, slow response, bad
payload, unreadable repository — every one of those returns zero silently. A
pre-check that turns somebody else's outage into this repository's outage has
cost more than it will ever save.

THREE FILTERS BEFORE THE FACT PREDICATES, in order, and each one is free:

  1. No literal shell command in Bash or Codex's exec wrapper: return.
  2. Every statement in the command is a known pure read: return. About two
     thirds of real traffic stops here, and no model is needed to know that a
     grep is a read.
  3. The fact-gatherer found NOTHING about the command: return. With nothing on
     disk contradicting it, there is nothing to ask about, and asking anyway is
     what produced the 0.44 median.

Only what survives all three reaches the predicates, which cost no request.

A separate, narrow preflight for an actual model CLI invocation reads Joe's
Model Room route from AGENTS.md and shows it before that command runs.
It never sees ordinary text searches or authentication readback as model work.

KILL SWITCH: set CARR_PRECHECK=0. Anything in front of every shell call needs
one, and that is engineering rather than caution.

UNATTENDED WORKERS get the same check: it is deterministic and makes no paid
call, so CARR_JEV_WORKER=off no longer changes anything here.
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

# The rule itself is read at the moment of use from the checkout's boot file.
# This narrow detector runs before the ordinary command-failure precheck, so a
# direct model invocation gets the route even when there are no filesystem
# facts to judge. It does not match a read such as `rg claude`.
MODEL_CLI = re.compile(
    r"(?m)(?:^|[;&|]\s*)(?:\s*(?:env\s+)?(?:[A-Za-z_][\w]*=\S+\s+)*)?"
    r"(?:\S*/)?(?:claude|grok|gemini|codex|opencode|ollama|aider)(?=\s|$)", re.I)
MODEL_PACKAGE_CLI = re.compile(
    r"(?m)(?:^|[;&|]\s*)\s*npx\s+(?:-y\s+)?"
    r"(?:@anthropic-ai/claude-code|@google/gemini-cli|@openai/codex)(?=\s|$)", re.I)
MODEL_CLI_AUTH = re.compile(r"^\s*(?:auth|login|logout|status|doctor|version|--version|--help)\b", re.I)
MODEL_ROOM_START = "<!-- carr-model-room-route:start -->"
MODEL_ROOM_END = "<!-- carr-model-room-route:end -->"


def model_room_rule(repo=REPO):
    """Pull the current source-controlled route instead of embedding a copy."""
    # A model call may start while the shell is in doctorcre-app or another
    # checkout. The installed hook still lives in CARR, so use its own boot
    # rule when that checkout has no route section.
    for root in dict.fromkeys((repo, REPO)):
        try:
            with open(os.path.join(root, "AGENTS.md"), encoding="utf-8") as handle:
                source = handle.read()
            return source.split(MODEL_ROOM_START, 1)[1].split(MODEL_ROOM_END, 1)[0].strip()
        except (OSError, IndexError):
            continue
    return None


def model_room_advisory(command, repo=REPO):
    """The route, read before a direct model CLI is attempted. Never deny.

    The executable position is found deterministically, which is all the
    route needs. The paid "direct-work score" this used to append changed
    nothing the rule did not already say (2026-10-04 audit)."""
    match = MODEL_CLI.search(command) or MODEL_PACKAGE_CLI.search(command)
    if not match or MODEL_CLI_AUTH.match(command[match.end():]):
        return None
    rule = model_room_rule(repo)
    if not rule:
        return None
    return "MODEL ROOM ROUTE — read the current rule before this CLI call: " + rule


def _sibling(name):
    """Load an ops/ library by path. ops/ is not a package by design."""
    import importlib.util
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


# DETERMINISTIC SINCE THE 2026-10-04 JEV AUDIT. This precheck used to send the
# gathered facts to Jev on every non-read shell call: 35,357 paid attempts in
# the seven days to 2026-10-04, the largest single site. Its own log showed
# what earned a warning. Of 3,170 judged commands 348 warned; 287 of those were
# a missing path, 57 a module interface (2% of the 2,518 times that fact was
# present), 6 a guard refusal, and 0 of 394 an undeclared option. A missing
# path is a fact, not a judgment (rule 5e89c211), so it is decided here by
# predicate. A guard refusal is left to the guard: jev_precheck's list is a
# regex copy that over-reads it (sudo, scratch-zone rm -rf), and where the
# guard does refuse, its own PreToolUse denial already says so. Replayed over the same log, the missing-path
# predicate below warns on 263 commands against Jev's 287, and the commands it
# alone flags are real failures (an rg or git add of a file that is not there).
# The interface, import and option facts alone no longer produce a warning.
#
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

    A glob such as tools/jev* is not the missing prefix tools/jev, and a path
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
    precheck = _sibling("jev_precheck")
    facts = precheck.environment_facts(command, repo)
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
            route = model_room_advisory(command, repo)
            if route:
                warnings.append(route)
                break
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
