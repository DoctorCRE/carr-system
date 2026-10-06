"""rule_boot_gate.py — state and classification for the gated rule boot.

WHAT THE GATE IS. Joe asked for 100% recall on relevant rules. Nothing put the
rules in front of a context before it acted, so the design Jev chose (p=1.00)
is a gate: every session and every subagent must fetch every page of
standing-context `detail: "boot"` (mcp-server/src/rule-boot.js: the full text
of the always-on rules plus a one-line index of every active rule) before any
other tool call runs. hooks/rule-boot-gate.py enforces it on PreToolUse and
reads each fetch's answer on PostToolUse; hooks/gate-integrity.py arms (and
re-arms) it at SessionStart. They all import this module so the state layout,
the fetch-call grammar and the notices live in one place.

DATABASE AND CODE ONLY. The state here holds the digest, the page count and
which pages a context has fetched. It never holds rule text.

STATE LAYOUT (under out/rule-boot-gate/, gitignored, per machine):
    <session>/arm.json                    status (armed | unavailable |
                                          not_deployed), digest, pages_total,
                                          epoch
    <session>/fetched/<agent>/<key>/      one directory per context and digest
        p<N>        page N's fetch was ATTEMPTED (PreToolUse)
        c<N>        page N came back as a real boot page (PostToolUse); holds
                    the page text's length, summed against total_chars
        u<N>        page N's answer, through a filter, was not the whole page:
                    not read, and not an outage either
        failed      a fetch came back as an error: the store is unreachable
        unsupported the Worker rejected detail=boot: not deployed yet
        d<H>-<rnd>  one deny, made when H pages were confirmed
Marker files, not a read-modify-write JSON, because a model often fetches the
pages in parallel and parallel hooks would otherwise lose each other's pages.
<key> is the digest for a subagent and digest+epoch for the main context, so
a SessionStart re-arm (startup, resume, clear, compact, fork) makes the main
context fetch again, and a digest change re-gates every context.

WHEN THE DIGEST MOVES MID-SESSION. Every boot page carries the corpus digest
and page count. When a fetch in any context returns a digest or page count
that differs from the arm, PostToolUse re-arms the session with it, so every
context of the session is held again at its next call until it has the new
pages. There is no timer and no network in the gate: a change is seen at
SessionStart or at the next boot fetch by any context in the session (every
new subagent makes one). A session whose contexts are all complete and that
starts no subagent keeps the digest it has until one of those happens.

DELIVERY BEFORE EFFECTS. Ordinary tools remain held until every full page is
confirmed. Outages, repeated attempts, missing adapters and unwritable state
are explicit failures, never evidence that rules were read. Fetches, rule-read
verbs and ToolSearch remain allowed for recovery. The diagnostic marker count
is capped; enforcement is not. Tool-less delegates need a fetch-capable route.
"""
import errno
import json
import os
import re
import secrets
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = "carr-rule-boot-gate/v1"
BOOT_SCHEMA = "carr-rule-boot/v1"

MAX_DIAGNOSTIC_MARKERS = 3
PENDING_GRACE_S = 45

# The CARR connector's MCP prefixes: the local server, the records alias and
# the claude.ai account connector (by name and by this account's connector
# id). Only these count as the fetch or as a pre-boot read; the same verb name
# on any other server is an ordinary tool. A partner whose claude.ai connector
# has a different id fetches through the Bash form, which is always allowed.
CARR_MCP_PREFIXES = ("mcp__carr__", "mcp__carr_records__", "mcp__claude_ai_CARR_Record_Layer__",
                     "mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__")

# Read-only rule verbs a context may call before its boot is complete. They
# read rules and doctrine and write nothing.
READ_ONLY_RULE_VERBS = frozenset({
    "standing-context", "applicable-rules", "resolve-doctrine-rules",
    "read-doctrine", "search-doctrine", "doctrine-index", "doctrine-sections",
})
# Tools that are needed to REACH the fetch: in Claude Code, MCP tool schemas
# are deferred and must be loaded with ToolSearch before they can be called.
REACH_TOOLS = frozenset({"ToolSearch"})
# Built-in subagent types with neither Bash nor an MCP tool. They can never
# fetch, so they are never denied (the deny cap covers custom ones).
TOOLLESS_AGENT_TYPES = frozenset({"statusline-setup"})

# THE BASH FETCH GRAMMAR (fixed 2026-09-27 after the gate held a live session's
# real fetches). A fetch is recognised by what the command DOES, not by one
# spelling of it:
#     [cd <absolute dir> &&] <run.sh> call standing-context ['<json>'] [redirect]... [| <filter>]...
# <run.sh> is any path (absolute, ~/, or relative to the cwd or the cd target)
# that resolves to this checkout's run.sh or a git worktree of the same repo.
# The only redirects are 2>&1, 2>/dev/null and </dev/null. Each <filter> only
# reshapes what the fetch printed and cannot run, write or invent anything:
#     jq [display options] [path filter]   paths only: .a.b, .[], .[0], |, ",", keys, length, type
#     python3|python -c '<code>'           one literal JSON pretty-printer, whose
#                                          parse/serialize path preserves every field
#     python3|python -m json.tool [opts]   (both Python forms only from a checkout root:
#                                          Python imports from its working directory)
#     head|tail [-n N | -N | -c N]         cat
# Anything else — `;`, `||`, `&`, a second `&&`, `$`, backticks, a file
# argument, a write, an unknown program — is not a fetch and is held as usual.
# A relative cd is refused because PostToolUse may see the post-cd cwd.
# Because no filter can invent or rewrite output, a page read through a pipe is confirmed
# the same way as a direct one: by the answer's own digest, page and text.
# KNOWN RESIDUALS (Jev, 2026-09-27, kind rule-boot-gate-fetch-recognition-
# loopholes; top gap edited_worktree_run_sh 0.95), kept on purpose: the gate
# trusts the run.sh it recognises, and a session can edit a worktree's run.sh
# (or what it calls: tools/call-verb.py, tools/db-tap.py, mcp-server/local-verb.mjs)
# or put another jq/python3 first on PATH. Closing that means pinning the whole
# fetch chain, and the gate exists to put the rules in front of a cooperating
# context, not to sandbox one; the permission system still judges every command.
_SAFE_CHAR = re.compile(r"[A-Za-z0-9_./~+:=,@%-]")
_REDIRECT = re.compile(r"(?:2>&1|2>\s*/dev/null|<\s*/dev/null)(?=[\s|]|$)")
_JQ_OPTS = frozenset({"-r", "-c", "-S", "-M", "-C", "-j", "-a", "--raw-output", "--compact-output",
                      "--sort-keys", "--tab", "--monochrome-output", "--color-output",
                      "--ascii-output", "--join-output"})
_JQ_PATH_TOKEN = re.compile(
    r'\s+|\|\s*|,|\.\[\]|\.\["[A-Za-z0-9_]+"\]|\.[A-Za-z_][A-Za-z0-9_]*|\.|\[\d{1,4}\]|\[\]'
    r'|(?:keys|length|type)(?![A-Za-z0-9_])')
_HEAD_TAIL_ARGS = re.compile(r"(?:-n ?\+?\d{1,7}|-c ?\+?\d{1,9}|-\d{1,7}|-n\+?\d{1,7}|-c\+?\d{1,9})?")
_JSON_TOOL_ARGS = re.compile(r"(?:\s*(?:--indent \d{1,2}|--sort-keys|--compact|--no-ensure-ascii|--tab))*")
# An AST node whitelist cannot prove dataflow: even read/print-only code can
# replace a byte by splitting stdin reads and printing a constant in between.
# This exact script reads the complete JSON once and serializes that object
# without assignment to its fields. Other -c programs are not trusted fetches.
_PY_JSON_PRETTY = "import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))"
# Only the Worker's own closed-vocabulary refusal of `detail` means "not
# deployed yet"; any other error is an outage.
_BOOT_UNSUPPORTED = re.compile(
    r"value_not_in_declared_vocabulary[\s\S]{0,300}\"?field\"?\s*[:=]\s*\"?detail\b"
    r"|\"?field\"?\s*[:=]\s*\"?detail\b[\s\S]{0,300}value_not_in_declared_vocabulary")
_OUT_OF_RANGE = "page_out_of_range"

UNAVAILABLE_NOTICE = (
    "RULES UNAVAILABLE: the CARR store did not answer the full boot. Ordinary tools stay held. "
    "Report the outage; retry the read-only standing-context boot door and read every page.")
NOT_DEPLOYED_NOTICE = (
    "RULE BOOT NOT DEPLOYED YET: the Worker answered but does not serve detail=boot. "
    "Ordinary tools stay held until the full boot is served. Rule reads and ToolSearch remain available.")
STATE_UNWRITABLE_NOTICE = (
    "RULE BOOT STATE UNWRITABLE: cannot save verified page state ({why}). Ordinary tools stay held. "
    "Report the state-folder fault and repair it; rule reads and ToolSearch remain available.")
UNARMED_NOTICE = (
    "RULE BOOT NOT ARMED: SessionStart did not arm the rule gate for this session. Read every "
    "page standing-context {\"detail\":\"boot\"} names before acting, before ordinary tools can run.")


def state_root():
    # Test and replay override only; a session cannot set a hook's environment.
    return os.environ.get("CARR_RULE_BOOT_STATE_DIR") or os.path.join(REPO, "out", "rule-boot-gate")


def safe_key(value, default):
    text = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or ""))[:128].strip("._")
    return text or default


def _session_dir(session_id):
    return os.path.join(state_root(), safe_key(session_id, "no-session"))


def read_arm(session_id):
    try:
        with open(os.path.join(_session_dir(session_id), "arm.json"), encoding="utf-8") as fh:
            arm = json.load(fh)
        return arm if isinstance(arm, dict) else None
    except (OSError, ValueError):
        return None


def write_arm(session_id, arm):
    folder = _session_dir(session_id)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".arm.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(arm, fh, sort_keys=True)
    os.replace(tmp, os.path.join(folder, "arm.json"))


def _stand_in(arm):
    """The keying arm for a context whose session has no usable page count."""
    return arm if arm and arm.get("status") == "armed" else {
        "digest": "sha256:" + str((arm or {}).get("status") or "unarmed"),
        "epoch": (arm or {}).get("epoch") or "unarmed"}


def _fetch_dir(session_id, agent_id, arm):
    agent = safe_key(agent_id, "main")
    digest = safe_key(str(arm.get("digest") or "").replace("sha256:", ""), "none")[:24]
    key = digest if agent_id else f"{digest}-{safe_key(arm.get('epoch'), 'e0')}"
    return os.path.join(_session_dir(session_id), "fetched", agent, key)


def _touch(folder, name):
    """Create folder/name. Returns None on success, else the OSError text."""
    try:
        os.makedirs(folder, exist_ok=True)
        fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT, 0o644)
        os.close(fd)
        return None
    except OSError as exc:
        return errno.errorcode.get(exc.errno, "OSError") if exc.errno else str(exc)


def _markers(folder):
    try:
        return os.listdir(folder)
    except OSError:
        return []


def record_page(session_id, agent_id, arm, page):
    return _touch(_fetch_dir(session_id, agent_id, arm), f"p{int(page)}")


def _pages(names, prefix):
    return {int(n[1:]) for n in names if re.fullmatch(prefix + r"\d{1,4}", n)}


def fetched_pages(session_id, agent_id, arm):
    """Pages this context has ATTEMPTED under this arm."""
    return _pages(_markers(_fetch_dir(session_id, agent_id, arm)), "p")


def _page_of(args):
    if not isinstance(args, dict) or args.get("detail") != "boot":
        return None
    page = args.get("page", 1)
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None
    return page if 1 <= page <= 9999 else None


def _git_common_dir(base):
    """The shared .git folder of the checkout rooted at `base`, or None.

    A main checkout's .git is that folder. A worktree's .git file names
    <common>/worktrees/<name>, whose `commondir` leads back to <common> and
    whose `gitdir` names this worktree's .git again: git's own back-reference,
    required here so a lookalike folder whose .git merely points into ours is
    not taken for a worktree. Raises OSError on an unreadable pointer."""
    dotgit = os.path.join(base, ".git")
    if os.path.isdir(dotgit):
        return os.path.realpath(dotgit)
    with open(dotgit, encoding="utf-8") as fh:
        pointer = fh.read(4096)
    if not pointer.startswith("gitdir:"):
        return None
    gitdir = os.path.join(base, pointer.split(":", 1)[1].strip())
    with open(os.path.join(gitdir, "gitdir"), encoding="utf-8") as fh:
        back = fh.read(4096).strip()
    if os.path.realpath(os.path.join(gitdir, back)) != os.path.realpath(dotgit):
        return None
    with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as fh:
        common = os.path.realpath(os.path.join(gitdir, fh.read(4096).strip()))
    if os.path.dirname(os.path.dirname(os.path.realpath(gitdir))) != common:
        return None
    return common


def _is_this_repos_run_sh(path):
    """True when `path` is run.sh at the root of this checkout, or of the main
    checkout or any git worktree of the same repository."""
    try:
        if os.path.basename(path) != "run.sh" or not os.path.isfile(path):
            return False
        real = os.path.realpath(path)
        if real == os.path.realpath(os.path.join(REPO, "run.sh")):
            return True
        mine = _git_common_dir(REPO)
        return bool(mine) and _git_common_dir(os.path.dirname(real)) == mine
    except OSError:
        return False


def _is_checkout_root(folder):
    """True when `folder` is the root of this checkout or a worktree of it."""
    return bool(folder) and os.path.isabs(folder) and _is_this_repos_run_sh(os.path.join(folder, "run.sh")) \
        and os.path.realpath(folder) == os.path.dirname(os.path.realpath(os.path.join(folder, "run.sh")))


def _lex(command):
    """The command as ("word", text, quoted) / ("op", "&&"|"|") / ("redir", text)
    tokens, or None when it uses anything outside the fetch grammar."""
    tokens, buf, state = [], [], {"quoted": False, "active": False}

    def end_word():
        if state["active"]:
            tokens.append(("word", "".join(buf), state["quoted"]))
        buf.clear()
        state.update(quoted=False, active=False)

    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch in " \t":
            end_word()
            i += 1
            continue
        if not state["active"]:
            m = _REDIRECT.match(command, i)
            if m:
                tokens.append(("redir", re.sub(r"\s+", "", m.group(0)), False))
                i = m.end()
                continue
        if ch in "'\"":
            j = command.find(ch, i + 1)
            if j < 0:
                return None
            inner = command[i + 1:j]
            if ch == '"' and any(c in inner for c in "$`\\!"):
                return None
            buf.append(inner)
            state.update(quoted=True, active=True)
            i = j + 1
            continue
        if command.startswith("&&", i):
            end_word()
            tokens.append(("op", "&&", False))
            i += 2
            continue
        if ch == "|" and not command.startswith("||", i) and not command.startswith("|&", i):
            end_word()
            tokens.append(("op", "|", False))
            i += 1
            continue
        if not _SAFE_CHAR.fullmatch(ch):
            return None
        buf.append(ch)
        state["active"] = True
        i += 1
    end_word()
    return tokens


def _expand(word, quoted, base):
    """An absolute path for a path word, or None. Only an unquoted ~ expands."""
    if not quoted and (word == "~" or word.startswith("~/")):
        word = os.path.expanduser(word)
    if os.path.isabs(word):
        return word
    return os.path.join(base, word) if base and os.path.isabs(base) else None


def _jq_ok(args):
    """jq with display options and at most one path-only filter, no files."""
    flt, i = None, 0
    while i < len(args):
        a = args[i]
        if a in _JQ_OPTS:
            i += 1
        elif a == "--indent" and i + 1 < len(args) and args[i + 1].isdigit():
            i += 2
        elif flt is None and not a.startswith("-"):
            flt, i = a, i + 1
        else:
            return False
    if flt is None:
        return True
    pos = 0
    while pos < len(flt):
        m = _JQ_PATH_TOKEN.match(flt, pos)
        if not m or m.end() == pos:
            return False
        pos = m.end()
    return bool(flt.strip())


def _harmless_filter(stage):
    if not stage or any(t[0] != "word" for t in stage):
        return False
    prog, args = stage[0][1], [t[1] for t in stage[1:]]
    if prog == "jq":
        return _jq_ok(args)
    if prog in ("python3", "python"):
        if len(args) == 2 and args[0] == "-c":
            return args[1] == _PY_JSON_PRETTY
        return args[:2] == ["-m", "json.tool"] and bool(_JSON_TOOL_ARGS.fullmatch(" ".join(args[2:])))
    if prog in ("head", "tail"):
        return bool(_HEAD_TAIL_ARGS.fullmatch(" ".join(args)))
    if prog == "cat":
        return not args
    return False


def parse_bash_fetch(command, cwd):
    """(args, direct) when `command` is a standing-context fetch in the grammar
    above (`direct` False when output passes through a filter), else None."""
    tokens = _lex(str(command or "").strip())
    if not tokens:
        return None
    base = cwd
    if tokens[0][:2] == ("word", "cd") and not tokens[0][2]:
        if len(tokens) < 4 or tokens[1][0] != "word" or tokens[2][:2] != ("op", "&&"):
            return None
        target = _expand(tokens[1][1], tokens[1][2], None)
        if not target:
            return None  # a relative cd: PostToolUse may see the post-cd cwd
        base, tokens = target, tokens[3:]
    # Any further && lands inside a stage below, where only words and
    # redirects are allowed, so it is refused there.
    stages, current = [], []
    for t in tokens:
        if t[:2] == ("op", "|"):
            stages.append(current)
            current = []
        else:
            current.append(t)
    stages.append(current)
    head = stages[0]
    if not head or head[0][0] != "word":
        return None
    words = [t for t in head if t[0] == "word"]
    if not all(t[0] in ("word", "redir") for t in head) or [t[0] for t in head[:len(words)]] != ["word"] * len(words):
        return None  # a redirect only after the arguments
    if len(words) not in (3, 4) or [w[1] for w in words[1:3]] != ["call", "standing-context"]:
        return None
    prog = words[0]
    if "/" not in prog[1]:
        return None  # a bare `run.sh` is looked up on PATH, not here
    path = _expand(prog[1], prog[2], base)
    if not path or not _is_this_repos_run_sh(path):
        return None
    if not all(_harmless_filter(stage) for stage in stages[1:]):
        return None
    # Python puts its working directory first on sys.path, so `import json`
    # would run a json.py planted there. A Python filter runs only from a
    # checkout root of this repo (review of #1343).
    uses_python = any(stage[0][1] in ("python3", "python") for stage in stages[1:])
    if uses_python and not _is_checkout_root(base):
        return None
    args = {}
    if len(words) == 4:
        try:
            args = json.loads(words[3][1])
        except ValueError:
            return None
        if not isinstance(args, dict):
            return None
    return args, len(stages) == 1


def _carr_verb(name):
    for prefix in CARR_MCP_PREFIXES:
        if name.startswith(prefix):
            # Codex exposes the MCP verb with underscores; the record-layer
            # vocabulary and Claude adapter use hyphens for the same verb.
            return name[len(prefix):].replace("_", "-")
    return None


def _classify(tool_name, tool_input, cwd=None):
    """(kind, page, direct): classify() plus whether a Bash fetch's output
    reached the tool result unfiltered (always True for MCP)."""
    name = str(tool_name or "")
    if name in REACH_TOOLS:
        return ("readonly", None, True)
    verb = _carr_verb(name)
    if verb in READ_ONLY_RULE_VERBS:
        if verb == "standing-context":
            return ("fetch", _page_of(tool_input), True)
        return ("readonly", None, True)
    if name == "Bash" and isinstance(tool_input, dict):
        parsed = parse_bash_fetch(tool_input.get("command"), cwd)
        if parsed:
            return ("fetch", _page_of(parsed[0]), parsed[1])
    return ("other", None, True)


def classify(tool_name, tool_input, cwd=None):
    """("fetch", page), ("readonly", None) or ("other", None).

    "fetch" with a page is a boot page fetch, to be recorded; "fetch" with
    None is any other standing-context call (allowed, not recorded).
    """
    kind, page, _direct = _classify(tool_name, tool_input, cwd)
    return kind, page


def fetch_instructions(pages, digest=None, pages_total=None):
    shown = ", ".join(str(p) for p in pages[:40]) + (" …" if len(pages) > 40 else "")
    first = pages[0] if pages else 1
    head = (f"digest {str(digest)[7:19]}, " if digest else "") + (
        f"{pages_total} page(s)" if pages_total else "")
    return (
        f"RULE BOOT: this context must read the CARR rules ({head}) before any other tool.\n"
        f"Fetch each missing page ({shown}), one call per page, then continue:\n"
        f"  MCP (CARR connector):  standing-context with {{\"detail\":\"boot\",\"page\":{first}}}\n"
        f"  Bash: {os.path.join(REPO, 'run.sh')} call standing-context "
        f"'{{\"detail\":\"boot\",\"page\":{first}}}'\n"
        "Until then only these calls, other standing-context calls, the read-only rule verbs "
        "(applicable-rules, resolve-doctrine-rules, read-doctrine, search-doctrine, doctrine-index, "
        "doctrine-sections) and ToolSearch will run. A leading `cd <absolute repo path> &&` and a pipe "
        "into a formatter (jq, python3 -c from the repo root, head) keep it a fetch if the whole JSON "
        "prints; anything run after the fetch (&&, ;, ||, &) does not. "
        "Ordinary tools stay held until the complete boot is verified; recovery rule reads remain available.")


# ---------------------------------------------------------------- answers

def _strings(value, out, depth=0):
    if depth > 6 or len(out) > 64:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out, depth + 1)


def _find_boot(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, dict):
        boot = value.get("rule_boot")
        if isinstance(boot, dict) and boot.get("digest"):
            return boot
        if value.get("schema") == BOOT_SCHEMA and value.get("digest"):
            return value
        for v in value.values():
            found = _find_boot(v, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _find_boot(v, depth + 1)
            if found:
                return found
    return None


def read_answer(response):
    """What a boot fetch came back as: ("boot", rule_boot) | ("out_of_range",
    {digest, pages_total}) | ("unsupported", None) | ("failed", None). Parses a
    Bash result or an MCP result; never raises."""
    boot = _find_boot(response)
    if boot:
        return "boot", boot
    texts = []
    _strings(response, texts)
    decoder = json.JSONDecoder()
    for text in texts:
        if "rule_boot" not in text and BOOT_SCHEMA not in text:
            continue
        for start in [m.start() for m in re.finditer(r"\{", text)][:8]:
            try:
                parsed, _ = decoder.raw_decode(text, start)
            except ValueError:
                continue
            boot = _find_boot(parsed)
            if boot:
                return "boot", boot
    for text in texts:
        if _OUT_OF_RANGE in text:
            return "out_of_range", _out_of_range_facts(text)
    if isinstance(response, dict) and response.get("error") == _OUT_OF_RANGE:
        return "out_of_range", {"digest": response.get("digest"), "pages_total": response.get("pages_total")}
    if any(_BOOT_UNSUPPORTED.search(t[:20000]) for t in texts):
        return "unsupported", None
    return "failed", None


def _out_of_range_facts(text):
    """digest and pages_total from the Worker's page_out_of_range refusal."""
    total = re.search(r"\"?pages_total\"?\s*[:=]\s*(\d{1,4})", text)
    digest = re.search(r"\"?digest\"?\s*[:=]\s*\"?(sha256:[0-9a-f]{8,64})", text)
    return {"digest": digest.group(1) if digest else None,
            "pages_total": int(total.group(1)) if total else None}


# ---------------------------------------------------------------- arming

def _live_page_one(timeout=10):
    """(response, None) or (None, reason); reason starts with "not_deployed"
    when the store answered and rejected detail=boot."""
    stub = os.environ.get("CARR_RULE_BOOT_FETCH_STUB")
    if stub is not None:  # TEST HOOK ONLY (ops/rule-boot-gate-selftest.py)
        if stub in ("unreachable", "not_deployed"):
            return None, stub
        with open(stub, encoding="utf-8") as fh:
            return json.load(fh), None
    try:
        proc = subprocess.run(
            ["python3", os.path.join(REPO, "tools", "call-verb.py"), "standing-context",
             json.dumps({"detail": "boot", "page": 1})],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=REPO)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"unreachable ({type(exc).__name__})"
    kind, boot = read_answer({"stdout": proc.stdout or "", "stderr": proc.stderr or ""})
    if kind == "boot":
        return {"rule_boot": boot}, None
    if kind == "unsupported":
        return None, "not_deployed (the deployed Worker rejects standing-context detail=boot)"
    return None, f"unreachable (exit {proc.returncode})"


def arm_session(session_id, source, now=None):
    """Arm the gate for a session at SessionStart and return the context text
    (always under 10k characters: SessionStart context is capped there)."""
    response, reason = _live_page_one()
    boot = response.get("rule_boot") if isinstance(response, dict) else None
    arm = {"schema": SCHEMA, "source": str(source or ""), "armed_at": int(now or time.time()),
           "epoch": secrets.token_hex(6)}
    if isinstance(boot, dict) and boot.get("digest") and int(boot.get("pages_total") or 0) >= 1:
        arm.update(status="armed", digest=boot["digest"], pages_total=int(boot["pages_total"]))
        if int(boot.get("total_chars") or 0) >= 1:
            arm["total_chars"] = int(boot["total_chars"])
        write_arm(session_id, arm)
        return fetch_instructions(list(range(1, arm["pages_total"] + 1)), arm["digest"], arm["pages_total"])
    if str(reason or "").startswith("not_deployed"):
        arm.update(status="not_deployed", reason=reason)
        write_arm(session_id, arm)
        return NOT_DEPLOYED_NOTICE
    arm.update(status="unavailable", reason=reason or "unreachable")
    write_arm(session_id, arm)
    return f"{UNAVAILABLE_NOTICE} ({arm['reason']})"


# ---------------------------------------------------------------- verdicts

def _toolless(payload):
    kind = str(payload.get("agent_type") or payload.get("agentType") or "")
    return bool(payload.get("agent_id") or payload.get("agentId")) and kind in TOOLLESS_AGENT_TYPES


def _hold(folder, confirmed, reason):
    """Hold until verified; bound diagnostic writes, never bound enforcement."""
    held = sum(1 for n in _markers(folder) if n.startswith(f"d{confirmed}-"))
    if held >= MAX_DIAGNOSTIC_MARKERS:
        return "deny", reason
    why = _touch(folder, f"d{confirmed}-{secrets.token_hex(4)}")
    if why:
        return "deny", reason + "\n" + STATE_UNWRITABLE_NOTICE.format(why=why)
    return "deny", reason


def verdict(payload, now=None):
    """The PreToolUse decision for one tool call: ("allow"|"deny", context_or_reason)."""
    session_id = payload.get("session_id") or payload.get("sessionId")
    agent_id = payload.get("agent_id") or payload.get("agentId")
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    arm = read_arm(session_id)
    key = _stand_in(arm)
    folder = _fetch_dir(session_id, agent_id, key)
    kind, page = classify(tool, tool_input, payload.get("cwd"))
    if kind == "fetch":
        if page is not None:
            why = record_page(session_id, agent_id, key, page)
            if why:
                return "allow", STATE_UNWRITABLE_NOTICE.format(why=why)
        return "allow", None
    if kind == "readonly":
        return "allow", None
    status = (arm or {}).get("status")
    if status == "not_deployed":
        return _hold(folder, 0, NOT_DEPLOYED_NOTICE + "\nFull boot is required before ordinary tools.")
    names = _markers(folder)
    if "unsupported" in names:
        return _hold(folder, 0, NOT_DEPLOYED_NOTICE + "\nFull boot is required before ordinary tools.")
    if _toolless(payload):
        return _hold(folder, 0, "RULES UNREAD: this subagent needs a rule-fetch tool before ordinary tools.")
    attempted = _pages(names, "p")
    if status != "armed":
        notice = UNAVAILABLE_NOTICE if arm else UNARMED_NOTICE
        if attempted or "failed" in names:
            return _hold(folder, 0, notice + "\nFull boot is required; retry the read-only rule door.")
        return _hold(folder, 0, notice + "\nBefore any other tool, ATTEMPT the rule boot fetch once in "
                     "this context (the fetch and rule-read tools always remain allowed):\n"
                     + fetch_instructions([1]))
    confirmed = _pages(names, "c")
    total = int(arm.get("pages_total") or 0)
    missing = [p for p in range(1, total + 1) if p not in confirmed]
    if not missing:
        if not _short_text(folder, arm):
            return "allow", None
        return _hold(folder, len(confirmed), (
            "RULE BOOT: every page came back, but their text does not add up to the boot's length "
            f"({arm.get('total_chars')} characters), so part of it was cut on the way to you. "
            "Fetch every page again and keep the whole JSON.\n")
            + fetch_instructions(list(range(1, total + 1)), arm.get("digest"), total))
    if "failed" in names:
        return _hold(folder, len(confirmed), UNAVAILABLE_NOTICE + "\nFull boot is required; retry the missing pages.")
    # A page attempted but never answered: PostToolUse writes c<N> or failed,
    # so silence past the grace means the fetch failed without a result.
    now = now or time.time()
    for p in missing:
        if p in attempted and f"u{p}" not in names:
            try:
                age = now - os.path.getmtime(os.path.join(folder, f"p{p}"))
            except OSError:
                continue
            if age > PENDING_GRACE_S:
                return _hold(folder, len(confirmed), UNAVAILABLE_NOTICE + "\nNo confirmed answer; retry the missing pages.")
    unreadable = [p for p in missing if f"u{p}" in names]
    lead = (f"Page(s) {', '.join(map(str, unreadable))} came back without the whole page (a pipe or "
            "formatter kept only part of it), so they do not count as read.\n") if unreadable else ""
    source = str(arm.get("source") or "")
    if not agent_id and not confirmed and source in ("compact", "resume", "clear"):
        # The main context's pages are keyed by the arm's epoch, so reads made
        # before this SessionStart do not count: say so, or a context that
        # remembers reading them (in its summary) thinks the gate is broken.
        lead += (f"This session was re-armed at SessionStart ({source}): pages read before it do "
                 "not count, because this context no longer holds them. Read every page again.\n")
    return _hold(folder, len(confirmed), lead + fetch_instructions(missing, arm.get("digest"), total))


def _utf16_len(text):
    """A string's length as JavaScript counts it (rule-boot.js total_chars)."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def boot_rule_ids(text):
    """The rule ids whose full text a boot page carries (Part 1 headings)."""
    return re.findall(r"^### ([0-9a-f]{8})(?: \(personal\))?$", text, re.M)


def boot_delivery(pages):
    """The rule ids one context received in full, or None.

    `pages` maps page number to the boot answers ONE context read (one
    session, one agent, one compaction epoch). Delivery needs every page of
    one digest, the same page count and total_chars on each, and page texts
    whose JavaScript lengths add up to total_chars: the gate's own test."""
    if not pages:
        return None
    first = next(iter(pages.values()))
    meta = (first.get("digest"), first.get("pages_total"), first.get("total_chars"))
    if any((p.get("digest"), p.get("pages_total"), p.get("total_chars")) != meta
           or not _is_page(p, n) for n, p in pages.items()):
        return None
    if set(pages) != set(range(1, int(meta[1] or 0) + 1)):
        return None
    if sum(_utf16_len(p["text"]) for p in pages.values()) != int(meta[2]):
        return None
    return [rid for n in sorted(pages) for rid in boot_rule_ids(pages[n]["text"])]


def _is_page(boot, page):
    """A boot answer is page `page` read in full: it names that page and a
    digest, and carries the page's text."""
    try:
        same_page = int(boot.get("page")) == int(page) and int(boot.get("total_chars") or 0) > 0
    except (TypeError, ValueError):
        return False
    text = boot.get("text")
    return same_page and str(boot.get("digest") or "").startswith("sha256:") and \
        isinstance(text, str) and text != ""


def _put(folder, name, content):
    """Create or overwrite folder/name with `content`. None, or the OSError text."""
    try:
        os.makedirs(folder, exist_ok=True)
        fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        return None
    except OSError as exc:
        return errno.errorcode.get(exc.errno, "OSError") if exc.errno else str(exc)


def _short_text(folder, arm):
    """True when every page is confirmed but their texts' lengths do not add up
    to the boot's total_chars: some page was cut on its way to the context.
    Missing length metadata never establishes complete delivery."""
    want = int(arm.get("total_chars") or 0)
    if want < 1:
        return True
    got = 0
    for p in range(1, int(arm.get("pages_total") or 0) + 1):
        try:
            with open(os.path.join(folder, f"c{p}"), encoding="utf-8") as fh:
                raw = fh.read(32).strip()
        except OSError:
            return True
        if not raw.isdigit():
            return True
        got += int(raw)
    return got != want


INCONCLUSIVE_NOTICE = (
    "RULE BOOT: page {page}'s answer did not carry that whole page (its rule_boot JSON with the "
    "digest, page {page} and the page text), so it does not count as read. A pipe or formatter "
    "that keeps only part of the output, or cuts it, does this. Fetch page {page} again without "
    "the pipe, or with one that prints the whole JSON.")


def observe(payload):
    """PostToolUse on a boot fetch: record what came back, re-arm the session
    on a new digest or page count. Returns a notice for the context, or None.

    A page is confirmed only by an answer that is that page (_is_page), and
    its text length is recorded so completion can be checked against the
    boot's total_chars. When the fetch's output went through a filter, only a
    confirmed page counts: anything else is INCONCLUSIVE (the filter, not the
    store, may be what failed), which neither confirms a page nor unlocks
    the context as an outage."""
    session_id = payload.get("session_id") or payload.get("sessionId")
    agent_id = payload.get("agent_id") or payload.get("agentId")
    kind, page, direct = _classify(payload.get("tool_name") or payload.get("toolName") or "",
                                   payload.get("tool_input") or payload.get("toolInput") or {},
                                   payload.get("cwd"))
    if kind != "fetch" or page is None:
        return None
    response = payload.get("tool_response")
    if response is None:
        response = payload.get("toolResponse")
    if response is None:
        response = payload.get("error")
    answer, boot = read_answer(response)
    if answer == "boot" and not _is_page(boot, page):
        answer = "inconclusive"
    if not direct and answer != "boot":
        answer = "inconclusive"
    arm = read_arm(session_id)
    if answer in ("boot", "out_of_range"):
        total = int((boot or {}).get("pages_total") or 0)
        digest = str((boot or {}).get("digest") or "")
        if total >= 1 and digest and (not arm or arm.get("status") != "armed"
                                      or arm.get("digest") != digest
                                      or int(arm.get("pages_total") or 0) != total):
            arm = {**(arm or {}), "schema": SCHEMA, "status": "armed", "digest": digest,
                   "pages_total": total, "rearmed_by": "fetch", "armed_at": int(time.time())}
            arm.setdefault("epoch", secrets.token_hex(6))
            arm.pop("reason", None)
            arm.pop("total_chars", None)
            if int((boot or {}).get("total_chars") or 0) >= 1:
                arm["total_chars"] = int(boot["total_chars"])
            try:
                write_arm(session_id, arm)
            except OSError:
                return None
        if answer == "boot" and not arm.get("total_chars"):
            arm["total_chars"] = int(boot["total_chars"])
            try:
                write_arm(session_id, arm)
            except OSError:
                return None
        folder = _fetch_dir(session_id, agent_id, _stand_in(arm))
        if answer == "out_of_range":
            return (f"RULE BOOT: page {page} does not exist; this boot has {total or 'fewer'} "
                    "page(s). Fetch the pages the gate names.")
        _touch(folder, f"p{page}")
        _put(folder, f"c{page}", str(_utf16_len(boot["text"])))
        _put(folder, f"r{page}", json.dumps(boot_rule_ids(boot["text"])))
        confirmed = _pages(_markers(folder), "c")
        if total > 0 and confirmed == set(range(1, total + 1)) and not _short_text(folder, arm):
            try:
                from lib.rule_recall import log_delivery
                ids = []
                for n in range(1, total + 1):
                    with open(os.path.join(folder, f"r{n}"), encoding="utf-8") as handle:
                        ids.extend(json.load(handle))
                if ids:
                    log_delivery(os.path.join(REPO, "out", "rule-boot-delivery.jsonl"),
                                 f"boot:{session_id}:{agent_id or 'main'}:{_stand_in(arm)}", ids)
            except (OSError, ValueError):
                pass
        for stale in ("failed", "unsupported", f"u{page}"):
            try:
                os.unlink(os.path.join(folder, stale))
            except OSError:
                pass
        return None
    folder = _fetch_dir(session_id, agent_id, _stand_in(arm))
    if answer == "inconclusive":
        _touch(folder, f"u{page}")
        return INCONCLUSIVE_NOTICE.format(page=page)
    if answer == "unsupported":
        _touch(folder, "unsupported")
        return NOT_DEPLOYED_NOTICE
    _touch(folder, "failed")
    return UNAVAILABLE_NOTICE
