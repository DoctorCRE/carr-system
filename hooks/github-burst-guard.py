#!/usr/bin/env python3
"""Refuse direct merge mutations, GitHub bursts, and calls during rate-limit cooldowns."""
import ast
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.environ.get("CARR_HOOK_GUARD_LOG") or os.path.join(REPO, "out", "hook-guard.log")
STATE = os.environ.get("CARR_GITHUB_BURST_STATE") or os.path.join(
    REPO, "out", "github-burst-guard-state.json")
MIN_LOOP_SLEEP = 2
POLL_SLEEP_HINT = 30                    # named in refusals; any sleep >= 2 passes
MAX_LITERAL_CALLS = 10                  # items x gh calls per pass, literal lists only
COOLDOWN = 15 * 60

# A gh word: `gh` on its own followed by a subcommand. `gh-pages`, `ghost`
# and paths ending in /gh-something do not match. Only counted where it is
# RUN: at a command position (GH_CMD_RE), or as the program an xargs /
# parallel fans out to. So `grep "gh pr merge" f` is not a gh call.
GH_RE = re.compile(r"(?<![\w./-])gh\s+(?=[a-z])")
SLEEP_RE = re.compile(r"(?<![\w.-])sleep\s+(\d+(?:\.\d+)?)([smhd]?)\b")
# gh's GraphQL lockout reads "API rate limit already exceeded".
RATE_LIMIT_TEXT = re.compile(r"api rate limit (?:already )?exceeded|secondary rate limit", re.I)
# gh's own error line: `HTTP 403: ...` / `HTTP 429: ...` anywhere on the line,
# or a line that starts with `gh:` or `GraphQL:`.
GH_ERROR_LINE = re.compile(r"\bHTTP (?:403|429)\b|^\s*(?:gh|GraphQL):", re.I)
# Stricter, for output with no visible failure: the prefix at column 0, exact case.
GH_ERROR_LINE_START = re.compile(r"(?:HTTP 403:|HTTP 429:|gh:|GraphQL:)")
EXIT_CODE_LINE = re.compile(r"^\s*Exit code (\d+)\b", re.M)

# Where a command word can start: string or line start, after a shell
# separator (an escaped `\|` inside a grep pattern is not one), inside a
# subshell or substitution, after a shell keyword, or at the start of a quoted
# `sh -c` body.
_CMD_START = (r"(?:^|(?<=[;&(\n`{!])|(?<=(?<!\\)\|)|(?<=-c\s['\"])|(?<=\bthen\s)"
              r"|(?<=\bdo\s)|(?<=\belse\s)|(?<=\bif\s)|(?<=\belif\s)|(?<=\bwhile\s)"
              r"|(?<=\buntil\s))\s*")
_PREFIX = (r"(?:(?:env|nohup|exec|time|command|timeout\s+\S+|caffeinate(?:\s+-\w+)*)\s+)*"
           r"(?:[A-Za-z_]\w*=\S*\s+)*")
GH_CMD_RE = re.compile(_CMD_START + _PREFIX + r"(gh)\s+(?=[a-z])", re.M)
RATE_PROBE_RE = re.compile(r"gh\s+api\s+/?rate_limit\b")
LOOP_KW_RE = re.compile(_CMD_START + r"(for|while|until)\b", re.M)
FANOUT_RE = re.compile(_CMD_START + _PREFIX + r"(xargs|parallel)\b", re.M)
WORD_RE = re.compile(r"(?<![\w$-])(do|done)(?![\w-])")
GITHUB_HOST_RE = re.compile(r"api\.github\.com")

# Shown with every refusal: the cheaper alternatives, named once.
CHEAPER = ("Use `tools/open-prs.sh` to list open PRs across jbookout repos with merge state. "
           "One `gh api --paginate` or `gh api graphql` call returns what a loop "
           "of per-item calls does; or put `sleep 2` (or more) inside the loop.")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} github-burst-guard {msg.rstrip()}\n")
    except OSError:
        pass


def inert_stripped(cmd):
    """Heredoc bodies and quoted -m messages are data, not commands."""
    try:
        from cmd_text import strip_inert_text
        return strip_inert_text(cmd)
    except Exception:
        return cmd


def mask_quoted_data(cmd):
    """Blank the inside of quoted strings the shell will not run.

    A `--body "..."`, a grep pattern or a sed script that merely mentions
    `gh pr view` is data. Kept as code: an
    `sh -c` / `bash -c` body, and a double-quoted string holding `$` or a
    backtick, whose substitutions (`"$(gh pr view $n)"`) do run. Offsets are
    preserved, so every later match still lines up with the original text.
    """
    out = list(cmd)
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c not in "'\"":
            i += 1
            continue
        j = i + 1
        while j < len(cmd) and cmd[j] != c:
            j += 2 if (c == '"' and cmd[j] == "\\") else 1
        inner = cmd[i + 1:j]
        runs = re.search(r"-c\s*$", cmd[max(0, i - 4):i]) is not None
        if not runs and not (c == '"' and re.search(r"[$`]", inner)):
            for k in range(i + 1, min(j, len(cmd))):
                if out[k] != "\n":
                    out[k] = " "
        i = j + 1
    return "".join(out)


# ---- state ----------------------------------------------------------------
class StateUnreadable(Exception):
    pass


def read_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise StateUnreadable(str(exc))
    if not isinstance(data, dict):
        raise StateUnreadable("state is not an object")
    return data


def write_state(**updates):
    try:
        state = read_state()
    except StateUnreadable:
        state = {}
    state.update(updates)
    tmp = f"{STATE}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE)
    except OSError as exc:
        log(f"STATE-WRITE-FAILED {exc}")


def stamp(state, key):
    value = state.get(key)
    return value if isinstance(value, (int, float)) else None


# ---- command analysis -----------------------------------------------------
def sleeps(text):
    """Every `sleep N` in text, in seconds."""
    scale = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return [float(n) * scale[unit] for n, unit in SLEEP_RE.findall(text)]


def loop_regions(cmd):
    """(keyword, header, body) for each for/while/until ... do ... done."""
    out = []
    for m in LOOP_KW_RE.finditer(cmd):
        start = m.end()
        tokens = list(WORD_RE.finditer(cmd, start))
        if not tokens or tokens[0].group(1) != "do":
            continue
        depth = 0
        end = None
        for t in tokens:
            depth += 1 if t.group(1) == "do" else -1
            if depth == 0:
                end = t
                break
        if end is None:
            continue
        out.append((m.group(1), cmd[start:tokens[0].start()], cmd[tokens[0].end():end.start()]))
    return out


def statement_end(cmd, pos):
    """End of the shell statement starting at pos: the next unquoted ; | & or newline."""
    quote = None
    i = pos
    while i < len(cmd):
        c = cmd[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "\\":
            i += 2
            continue
        elif c in ";|&\n":
            return i
        i += 1
    return len(cmd)


def literal_item_count(header):
    """Item count of `for x in a b c`, or None when the list is not literal."""
    m = re.match(r"\s*\w+\s+in\s+(.*?)\s*;?\s*$", header, re.S)
    if not m:
        return None
    items = m.group(1)
    if re.search(r"[$`*?\[{]", items):
        return None
    try:
        return len(shlex.split(items))
    except ValueError:
        return None


def fanout_segments(cmd):
    """(program, text) for each xargs / parallel and the statement it runs."""
    for m in FANOUT_RE.finditer(cmd):
        yield m.group(1), cmd[m.end():statement_end(cmd, m.end())], m.end()


def gh_calls(text):
    """Offsets where each gh the shell would RUN in text begins."""
    found = {m.start(1) for m in GH_CMD_RE.finditer(text)}
    for _, segment, offset in fanout_segments(text):
        found.update(offset + m.start() for m in GH_RE.finditer(segment))
    return found


def backgrounded(cmd, pos):
    """True when the statement starting at pos is sent to the background by a
    single `&`. A pipeline is followed to its end (`gh x | jq . &` runs gh in
    the background too); `&&`, `||`, `;` and a newline end it in the
    foreground; `2>&1`, `>&2`, `&>file` and `|&` are redirections. A `)` or
    backtick closing the substitution the call sits in also ends it, so the
    `&` in `"...?head=$(gh ...)&per_page=50"` is not read as backgrounding."""
    quote = None
    depth = 0
    i = pos
    while i < len(cmd):
        c = cmd[i]
        nxt = cmd[i + 1] if i + 1 < len(cmd) else ""
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "\\":
            i += 2
            continue
        elif c == "(":
            depth += 1
        elif c in ")`":
            if depth == 0:              # end of the `$( )` / subshell holding it
                return False
            depth -= 1
        elif c in ";\n" or (c == "|" and nxt == "|"):
            return False
        elif c == "&":
            prev = cmd[i - 1] if i else ""
            if nxt == "&":
                return False
            if prev in ">|" or nxt == ">":
                i += 1
                continue
            return True
        i += 1
    return False


def background_reason(cmd):
    calls = sorted(gh_calls(cmd))
    if len(calls) < 2 or not any(backgrounded(cmd, pos) for pos in calls):
        return None
    return (f"{len(calls)} gh calls with at least one sent to the background by `&`, "
            f"so they run at the same time")


def loop_reason(cmd):
    for keyword, header, body in loop_regions(cmd):
        gh_header = len(gh_calls(header))
        gh_body = len(gh_calls(body))
        if gh_header + gh_body == 0:
            continue
        # The `until gh ...; do sleep 30; done` poll needs no branch of its
        # own: its sleep already clears MIN_LOOP_SLEEP. The selftest pins it.
        if any(s >= MIN_LOOP_SLEEP for s in sleeps(header + body)):
            continue
        if keyword == "for" and gh_body and not gh_header:
            n = literal_item_count(header)
            if n is not None and n * gh_body <= MAX_LITERAL_CALLS:
                continue
        return f"a `{keyword}` loop that runs gh on every pass with no `sleep {MIN_LOOP_SLEEP}`+ inside it"
    for program, segment, _ in fanout_segments(cmd):
        if GH_RE.search(segment) and not any(s >= MIN_LOOP_SLEEP for s in sleeps(segment)):
            return f"`{program}` fanning gh out over a list with no `sleep {MIN_LOOP_SLEEP}`+ between calls"
    return None








def gh_calls_only_rate_probe(cmd):
    """True when every gh the command runs is `gh api rate_limit`."""
    return all(RATE_PROBE_RE.match(cmd, pos) for pos in gh_calls(cmd))


def touches_github(cmd):
    return bool(gh_calls(cmd) or GITHUB_HOST_RE.search(cmd))


# ---- the two halves -------------------------------------------------------




def merge_approval_reason(cmd):
    source = cmd.replace('\\\n', '')
    scan = re.sub(r"['\"`]", '', source)
    repo_options = r"(?:(?:-R|--repo|--hostname)(?:=|\s*)\S+\s+)*"
    pinned = (r"\bgh\s+pr\s+merge\s+[1-9][0-9]*\s+--repo\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
              r"\s+--auto\s+--squash\s+--match-head-commit\s+[0-9a-f]{40}(?=\s*(?:$|[;&|\n]))")
    direct = re.search(r"\bgh\s+" + repo_options + r"pr\s+" + repo_options + r"merge\b",
                       re.sub(pinned, '', scan))
    graphql = re.search(r"\bgh\s+" + repo_options + r"api\s+graphql\b", scan) and re.search(
        r"\b(?:mergePullRequest|enablePullRequestAutoMerge)\b", scan)
    rest = False
    for statement in re.split(r"[;|&\n()]", scan):
        if not re.search(r"\bpulls/[^/\s]+/merge\b", statement):
            continue
        methods = re.findall(r"(?:-X\s*|--(?:method|request)(?:=|\s+))(\S+)", statement)
        if any(method.upper() != 'GET' for method in methods) or (
                not methods and re.search(r"\bgh\s+" + repo_options + r"api\b", statement)):
            rest = True
            break
    if direct or rest or graphql:
        return ('MERGE APPROVAL GUARD: direct merges require squash auto-merge bound to an approved head. '
                'Use tools/approve-merge.sh <owner/repo> <pr> <approved_sha>.')
    return None


def pre_verdict(cmd, now):
    """Return a refusal reason, or None to allow."""
    reason = merge_approval_reason(cmd)
    if reason:
        return reason
    scan = mask_quoted_data(inert_stripped(cmd))
    has_gh = bool(gh_calls(scan))
    if not has_gh:
        return None
    try:
        state = read_state()
    except StateUnreadable as exc:
        log(f"ALLOW(state-unreadable) {exc}")
        return None

    limited = stamp(state, "rate_limited_at")
    if limited is not None and now - limited < COOLDOWN and not (gh_calls_only_rate_probe(scan)):
        left = int((COOLDOWN - (now - limited)) // 60) + 1
        return (f"GITHUB BURST GUARD: GitHub answered with a rate-limit error "
                f"{int((now - limited) // 60)} minute(s) ago, and every session, CI "
                f"watcher share that account. Any more calls "
                f"now extend the lockout. Wait about {left} more minute(s). "
                f"`gh api rate_limit` is still allowed and shows when the limit resets "
                f"(rule 74ddb23c, never burst GitHub).")

    if has_gh:
        why = background_reason(scan)
        if why:
            return (f"GITHUB BURST GUARD: this command is {why}. Parallel calls "
                    f"trip GitHub's short-window rate limit for the whole shared "
                    f"account, which every session and CI watcher use. Run them one at a time with `;` or `&&` (a "
                    f"`sleep {MIN_LOOP_SLEEP}` between them for more than a few), "
                    f"or use one `gh api --paginate` / `gh api graphql` call "
                    f"(rule 74ddb23c, never burst GitHub).")
        why = loop_reason(scan)
        if why:
            return (f"GITHUB BURST GUARD: this command is {why}. A burst like that "
                    f"trips GitHub's short-window rate limit for the whole shared "
                    f"account; on 2026-10-06 one such loop (~55 calls in seconds) "
                    f"locked out an urgent production release. {CHEAPER} A poll "
                    f"of the form `until gh ...; do sleep {POLL_SLEEP_HINT}; done` "
                    f"is also allowed (rule 74ddb23c, never burst GitHub).")

    return None


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def call_failed(payload, event):
    """The call failed: a PostToolUseFailure event, or a non-zero exit code
    in the payload (top level or inside the tool response), or an
    `Exit code N` line with N != 0 in the error text."""
    if event == "PostToolUseFailure":
        return True
    places = [payload]
    for key in ("tool_response", "tool_output", "toolResponse"):
        if isinstance(payload.get(key), dict):
            places.append(payload[key])
    for place in places:
        for key in ("exit_code", "exitCode", "returncode", "returnCode", "status_code"):
            code = _int(place.get(key))
            if code is not None:
                return code != 0
    error = payload.get("error")
    if isinstance(error, str):
        m = EXIT_CODE_LINE.search(error)
        if m:
            return int(m.group(1)) != 0
    return False


def rate_limit_error_line(text):
    """True when a rate-limit phrase sits on gh's own error line."""
    for line in text.splitlines():
        if RATE_LIMIT_TEXT.search(line) and GH_ERROR_LINE.search(line):
            return True
    return False


def response_lines(payload):
    """The response as plain text lines: JSON-escaped newlines are undone, so
    a stderr field's lines are lines here too."""
    parts = []
    for key in ("tool_response", "tool_output", "toolResponse", "error"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            parts.extend(v for v in value.values() if isinstance(v, str))
        elif isinstance(value, str):
            parts.append(value)
        else:
            parts.append(json.dumps(value))
    return "\n".join(parts)


def line_start_rate_limit(text):
    """True when a line STARTS (column 0, no indent, no diff +/-) with gh's
    error prefix and that same line carries a rate-limit phrase."""
    for line in text.splitlines():
        if GH_ERROR_LINE_START.match(line) and RATE_LIMIT_TEXT.search(line):
            return True
    return False






def observe(payload, cmd, now, event):
    scan = mask_quoted_data(inert_stripped(cmd))
    if not touches_github(scan):
        return
    text = response_lines(payload)
    # Path 1: the call failed and the phrase is on gh's error line.
    # Path 2: no failure is visible, as when gh is piped (`gh pr list | head`
    # exits with head's 0), but a line starts exactly with gh's own error
    # prefix. Recording on that weaker evidence is deliberate: a false
    # 15-minute pause costs little, while a missed lockout lets sessions keep
    # hammering GitHub and extend its block. Indented PR-body lines and diff
    # lines (+/-) do not start at column 0 with the prefix, so they stay
    # rejected.
    if (call_failed(payload, event) and rate_limit_error_line(text)) or line_start_rate_limit(text):
        write_state(rate_limited_at=now)
        log(f"RECORDED rate-limit answer :: {cmd[:200]}")


class UninspectableCommand(ValueError):
    pass


def native_commands(code):
    tokens = re.findall(r'''//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|`(?:\\[\s\S]|[^`\\])*`|[A-Za-z_$][\w$]*|[^\s]''', code)
    tokens = [token for token in tokens if not token.startswith(('//', '/*'))]
    commands = []
    for i, token in enumerate(tokens):
        if token == 'exec_command':
            argument = i + 1
        elif token in ('"exec_command"', "'exec_command'", '`exec_command`') and i and tokens[i - 1] == '[':
            if tokens[i + 1:i + 2] != [']']:
                raise UninspectableCommand('Dynamic native tool names cannot be inspected.')
            argument = i + 2
        else:
            continue
        if tokens[argument:argument + 2] == ['?', '.']:
            argument += 2
        if tokens[argument:argument + 2] != ['(', '{']:
            raise UninspectableCommand('Native exec_command requires an inspectable object argument.')
        depth, values = 1, []
        for j in range(argument + 2, len(tokens)):
            current = tokens[j]
            if depth == 1 and current in ('.', '['):
                raise UninspectableCommand('Spread or computed native command arguments cannot be inspected.')
            if depth == 1 and current in ('cmd', '"cmd"', "'cmd'") and tokens[j + 1:j + 2] == [':']:
                literal = tokens[j + 2] if j + 2 < len(tokens) else ''
                if not literal.startswith(('"', "'", '`')) or tokens[j + 3:j + 4] not in ([','], ['}']):
                    raise UninspectableCommand('Dynamic native commands cannot be inspected; use a literal cmd.')
                if literal.startswith('`'):
                    if '${' in literal or '\\' in literal:
                        raise UninspectableCommand('Interpolated or escaped native templates cannot be inspected; use a plain literal cmd.')
                    values.append(literal[1:-1])
                else:
                    if re.search(r'''\\(?![\\'"abfnrtv0-7xuU\n])''', literal):
                        raise UninspectableCommand('Unsupported JavaScript string escape in native command.')
                    try:
                        values.append(ast.literal_eval(literal))
                    except (ValueError, SyntaxError):
                        raise UninspectableCommand('Unsupported native command literal.') from None
            if current in ('{', '[', '('):
                depth += 1
            elif current in ('}', ']', ')'):
                depth -= 1
            if depth == 0:
                break
        if depth != 0 or len(values) != 1:
            raise UninspectableCommand('Native exec_command requires exactly one literal cmd.')
        commands.extend(values)
    return '\n'.join(commands)


def command_of(payload):
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool not in ("Bash", "exec_command", "functions.exec"):
        return None
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    cmd = (ti.get("command") or ti.get("cmd") or ti.get("code") or "") if isinstance(ti, dict) else ti
    if tool == 'functions.exec' and isinstance(cmd, str) and 'exec_command' in cmd:
        cmd = native_commands(cmd)
    return cmd if isinstance(cmd, str) and cmd else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {type(exc).__name__}")
        return 0
    try:
        if not isinstance(payload, dict):
            return 0
        cmd = command_of(payload)
        if cmd is None:
            return 0
        event = payload.get("hook_event_name") or "PreToolUse"
        now = time.time()
        if event in ("PostToolUse", "PostToolUseFailure"):
            observe(payload, cmd, now, event)
            return 0
        reason = pre_verdict(cmd, now)
        if reason:
            log(f"DENY {reason[:120]} :: {cmd[:300]}")
            # Exit 2 + stderr, as guard-unattended.py: it blocks on every build.
            print(reason, file=sys.stderr)
            return 2
        return 0
    except UninspectableCommand as exc:
        print(f'MERGE APPROVAL GUARD: {exc} Use tools/approve-merge.sh.', file=sys.stderr)
        return 2
    except Exception as exc:                       # fail OPEN
        log(f"ALLOW(internal-error) {type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
