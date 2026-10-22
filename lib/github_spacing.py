"""Stateless spacing checks shared by the single shell hook and its fixtures."""

import re
import shlex

MIN_LOOP_SLEEP = 2

MAX_LITERAL_CALLS = 10

GH_RE = re.compile(r"(?<![\w./-])gh\s+(?=[a-z])")

SLEEP_RE = re.compile(r"(?<![\w.-])sleep\s+(\d+(?:\.\d+)?)([smhd]?)\b")

_CMD_START = (r"(?:^|(?<=[;&(\n`{!])|(?<=(?<!\\)\|)|(?<=-c\s['\"])|(?<=\bthen\s)"
              r"|(?<=\bdo\s)|(?<=\belse\s)|(?<=\bif\s)|(?<=\belif\s)|(?<=\bwhile\s)"
              r"|(?<=\buntil\s))\s*")

_PREFIX = (r"(?:(?:env|nohup|exec|time|command|timeout\s+\S+|caffeinate(?:\s+-\w+)*)\s+)*"
           r"(?:[A-Za-z_]\w*=\S*\s+)*")

GH_CMD_RE = re.compile(_CMD_START + _PREFIX + r"(gh)\s+(?=[a-z])", re.M)

LOOP_KW_RE = re.compile(_CMD_START + r"(for|while|until)\b", re.M)

FANOUT_RE = re.compile(_CMD_START + _PREFIX + r"(xargs|parallel)\b", re.M)

WORD_RE = re.compile(r"(?<![\w$-])(do|done)(?![\w-])")

def mask_quoted_data(cmd):
    """Blank the inside of quoted strings the shell will not run.

    A `--body "..."`, a grep pattern or a sed script that merely mentions
    `gh pr view` or `release-pipeline.py tick` is data. Kept as code: an
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

def reason(command):
    scan = mask_quoted_data(command)
    why = background_reason(scan) or loop_reason(scan)
    return ('GitHub calls need sleep 2 between items: ' + why) if why else None

