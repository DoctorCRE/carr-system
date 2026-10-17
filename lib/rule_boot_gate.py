"""rule_boot_gate.py — state and classification for the gated rule boot.

Every session and subagent must fetch the current sponsor-scoped corpus digest
through standing-context detail="boot" before ordinary tools run. The server
returns one page without rule text. The scoped lookup delivers binding text
at the relevant action. hooks/rule-boot-gate.py enforces the receipt on
PreToolUse and confirms it on PostToolUse. Older paged responses remain valid.

DATABASE AND CODE ONLY. The state here holds the digest, the page count and
which pages a context has fetched. It never holds rule text.

STATE LAYOUT (under out/rule-boot-gate/, gitignored, per machine):
    <session>/arm.json                    status (armed | unavailable |
                                          not_deployed), digest, pages_total,
                                          epoch
                                          [carry_epoch: see WHO COMPACTED]
                                          [by_agent: a flagged child's SessionStart
                                          re-armed it on a new digest]
    <session>/agents/<agent>.epoch        one epoch per child context; a child
                                          SessionStart rotates only this file
    <session>/fetched/<agent>/<key>/      one directory per context and digest
        p<N>        page N's fetch was ATTEMPTED (PreToolUse)
        c<N>        page N came back as a real boot page (PostToolUse); holds
                    the page text's length, summed against total_chars
        u<N>        page N's answer, through a filter, was not the whole page:
                    not read, and not an outage either
        failed      a fetch came back as an error: the store is unreachable
        unsupported the Worker rejected detail=boot: not deployed yet
        d<H>-<rnd>  one deny, made when H pages were confirmed
        a-child     main: the compaction behind this epoch was a subagent's
        a-own       main: it was this context's own (see WHO COMPACTED)
        k<T>        a child: its transcript showed no compaction since its boot
                    when checked against the compaction re-armed at T
Marker files, not a read-modify-write JSON, because a model often fetches the
pages in parallel and parallel hooks would otherwise lose each other's pages.
<key> is digest+the child's own epoch when its epoch file exists, digest alone
for a child that has not emitted SessionStart, and digest+the session epoch for
the main context. A child SessionStart therefore re-gates only that child. A
main SessionStart re-gates the main context, and a digest change re-gates every
context.

WHO COMPACTED (WR-000265, measured 2026-10-12). A subagent's compaction runs
the SessionStart hooks too, and Claude Code (2.1.287, 2.1.293, 2.1.295) builds
that payload without the agent's context: the parent's session_id, the
parent's transcript_path, no agent_id, and agent_type is the MAIN thread's. No
payload field tells a child's compaction from the parent's own. A SessionStart
that does carry agent_id (the flagged form) rotates only that child's epoch.
An unflagged compact with the same corpus still rotates the session epoch
(fail closed), and records as carry_epoch the epoch under which the main
context last held a complete boot. The transcripts then settle it at the main
context's next held call: the main file is <dir>/<session>.jsonl and a
subagent's is <dir>/<session>/subagents/agent-<id>.jsonl. When the main file
has no compact_boundary of its own after the main boot completed, has a line
written after the re-arm, and some subagent file has a compact_boundary within
COMPACT_SLACK_S before the re-arm, the compaction was a child's and the carried
boot counts (a-child). A main boundary after the boot is the main context's own
compaction (a-own): held, as before. Anything less settles nothing: held. This
never carries a boot across the main context's own compaction. A child is
re-gated (new agent epoch) when its own file shows a compact_boundary after its
boot completed. Without a transcript (another host, a replay) nothing changes.

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
import ast
import builtins
import errno
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = "carr-rule-boot-gate/v1"
BOOT_SCHEMA = "carr-rule-boot/v1"

MAX_DIAGNOSTIC_MARKERS = 3
PENDING_GRACE_S = 45
# A child's compact_boundary is stamped before its SessionStart hooks run
# (after PostCompact hooks and the summary), so it precedes the re-arm.
COMPACT_SLACK_S = 600
# The PreToolUse hook has a 5s timeout and a cancelled hook lets the call
# through, so the transcript reads are bounded: a file larger than this, or a
# scan past the budget, settles nothing and the context stays held.
MAX_TRANSCRIPT_BYTES = 128 * 1024 * 1024
SCAN_BUDGET_S = 1.5
_AGENT_FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

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
# computes over what the fetch printed and cannot run, write or read anything else:
#     jq [display options] [filter]        any filter except the ones that read the
#                                          environment, other files or modules
#     python3|python -c '<code>'           code whose syntax tree is limited to pure
#                                          computation over stdin and print (_py_pure)
#     python3|python -m json.tool [opts]   (both Python forms only from a checkout root:
#                                          Python imports from its working directory)
#     head|tail [-n N | -N | -c N]         cat
# Anything else — `;`, `||`, `&`, a second `&&`, `$`, backticks, a file
# argument, a write, an unknown program — is not a fetch and is held as usual.
# A relative cd is refused because PostToolUse may see the post-cd cwd.
# WHAT A PIPED PAGE PROVES. A
# filter may keep the whole JSON, and then the page is confirmed like a direct
# read (digest, page, text and length). Or it may keep less: the page then
# counts when the call SUCCEEDED and its canonical JSON projection retains
# the upstream ok:true and rule_boot.digest fields. A context that fetched
# the page and kept the digest
# has done the read; demanding the whole JSON made sessions fetch every page
# twice. The cost, accepted on purpose: a filtered page has no length evidence,
# and a filter that keeps the digest and drops the text still counts.
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
# jq has no way to run a program or write a file. What it CAN do beyond reshaping
# stdin is read the environment, other files and modules; those builtins are refused.
_JQ_REFUSED = re.compile(
    r"\$(?:ENV|__)|\b(?:env|input|inputs|input_filename|input_line_number|import|include|"
    r"modulemeta|get_search_list|get_prog_origin|get_jq_origin|debug|stderr|halt_error)\b")
_HEAD_TAIL_ARGS = re.compile(r"(?:-n ?\+?\d{1,7}|-c ?\+?\d{1,9}|-\d{1,7}|-n\+?\d{1,7}|-c\+?\d{1,9})?")
_JSON_TOOL_ARGS = re.compile(r"(?:\s*(?:--indent \d{1,2}|--sort-keys|--compact|--no-ensure-ascii|--tab))*")
# A python3 -c filter is admitted when its syntax tree can only compute over
# stdin and print (_py_pure). It is not trusted to preserve the page: what it
# printed is judged by the digest it carries (see WHAT A PIPED PAGE PROVES).
_PY_IMPORTS = frozenset({"json", "sys"})
_PY_BUILTINS = frozenset({"print", "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
                          "sorted", "range", "enumerate", "zip", "min", "max", "sum", "any", "all",
                          "repr", "reversed", "abs", "round", "isinstance", "True", "False", "None"})
_PY_MODULE_ATTRS = {("json",): {"load", "loads", "dumps"}, ("sys",): {"stdin", "stdout"},
                    ("sys", "stdin"): {"read", "readline", "readlines"}, ("sys", "stdout"): {"write"}}
_PY_METHODS = frozenset({"get", "keys", "values", "items", "split", "rsplit", "strip", "lstrip", "rstrip",
                         "join", "replace", "splitlines", "startswith", "endswith", "lower", "upper",
                         "count", "find", "index", "partition", "rpartition"})

_PY_NODES = (ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.For, ast.If, ast.IfExp, ast.Compare,
             ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Store, ast.Constant,
             ast.Attribute, ast.Subscript, ast.Slice, ast.Tuple, ast.List, ast.Dict, ast.Set, ast.ListComp,
             ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.comprehension, ast.keyword, ast.JoinedStr,
             ast.FormattedValue, ast.Import, ast.alias, ast.Pass, ast.Break, ast.Continue,
             ast.operator, ast.boolop, ast.cmpop, ast.unaryop)
# Every other builtin name (open, exec, eval, getattr, __import__, ...) is refused.
_PY_UNSAFE_BUILTINS = frozenset(dir(builtins)) - _PY_BUILTINS
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


def _agent_epoch_path(session_id, agent_id):
    return os.path.join(_session_dir(session_id), "agents",
                        safe_key(agent_id, "agent") + ".epoch")


def _read_agent_epoch(session_id, agent_id):
    try:
        with open(_agent_epoch_path(session_id, agent_id), encoding="utf-8") as fh:
            return safe_key(fh.read().strip(), "invalid")
    except FileNotFoundError:
        return None


def _write_agent_epoch(session_id, agent_id):
    path = _agent_epoch_path(session_id, agent_id)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    epoch = secrets.token_hex(6)
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(epoch)
    os.replace(tmp, path)


def _stand_in(arm):
    """The keying arm for a context whose session has no usable page count."""
    return arm if arm and arm.get("status") == "armed" else {
        "digest": "sha256:" + str((arm or {}).get("status") or "unarmed"),
        "epoch": (arm or {}).get("epoch") or "unarmed"}


def _fetch_dir(session_id, agent_id, arm):
    agent = safe_key(agent_id, "main")
    digest = safe_key(str(arm.get("digest") or "").replace("sha256:", ""), "none")[:24]
    if agent_id:
        epoch = _read_agent_epoch(session_id, agent_id)
        key = f"{digest}-{epoch}" if epoch else digest
    else:
        key = f"{digest}-{safe_key(arm.get('epoch'), 'e0')}"
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


def _jq_filter(args):
    """The optional filter, or False when options or file arguments are invalid."""
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
    return flt


def _jq_ok(args):
    flt = _jq_filter(args)
    if flt is None:
        return True
    return isinstance(flt, str) and bool(flt.strip()) and not _JQ_REFUSED.search(flt)


def _py_pure(code):
    """True when `code` can only compute over stdin and print: imports of json
    and sys only, a fixed set of builtins, json.load/loads/dumps, sys.stdin
    reads and sys.stdout.write, string and mapping methods, no underscore
    names, no definitions. Every object it can reach is data, a pure function
    or stdin/stdout, so no keyword argument can open, run or write anything.
    Anything it cannot parse is refused."""
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError):
        return False
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        if not isinstance(node, _PY_NODES):
            return False
        if isinstance(node, ast.Import):
            if any(a.name not in _PY_IMPORTS or (a.asname and a.asname != a.name) for a in node.names):
                return False
        elif isinstance(node, ast.keyword):
            if node.arg is None:
                return False  # **mapping: keyword names this check cannot see
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or not _py_attr_ok(node):
                return False
        elif isinstance(node, ast.Name):
            if node.id.startswith("_"):
                return False
            if node.id in _PY_IMPORTS:
                # json and sys only as the base of an admitted attribute chain.
                if isinstance(node.ctx, ast.Store) or not isinstance(parents.get(node), ast.Attribute):
                    return False
            elif node.id in _PY_UNSAFE_BUILTINS:
                return False
        elif isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            return False
    return True


def _py_chain(node):
    """("sys", "stdin") for sys.stdin, None when the base is not a bare name."""
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return tuple([node.id] + names[::-1])


def _py_attr_ok(node):
    chain = _py_chain(node)
    if chain and chain[0] in _PY_IMPORTS:
        return node.attr in _PY_MODULE_ATTRS.get(chain[:-1], ())
    return node.attr in _PY_METHODS


def _harmless_filter(stage):
    if not stage or any(t[0] != "word" for t in stage):
        return False
    prog, args = stage[0][1], [t[1] for t in stage[1:]]
    if prog == "jq":
        return _jq_ok(args)
    if prog in ("python3", "python"):
        if len(args) == 2 and args[0] == "-c":
            return _py_pure(args[1])
        return args[:2] == ["-m", "json.tool"] and bool(_JSON_TOOL_ARGS.fullmatch(" ".join(args[2:])))
    if prog in ("head", "tail"):
        return bool(_HEAD_TAIL_ARGS.fullmatch(" ".join(args)))
    if prog == "cat":
        return not args
    return False


def _py_preserves_root(code):
    """Prove printed values retain the complete stdin response without mutation."""
    tree = ast.parse(code)
    variables, roots = set(), set()

    def data(node):
        if isinstance(node, ast.Name):
            return node.id in variables
        if isinstance(node, ast.Subscript):
            return data(node.value) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, (str, int))
        if isinstance(node, ast.Dict):
            return bool(node.keys) and all(isinstance(k, ast.Constant) and isinstance(k.value, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,29}", k.value) and data(v)
                for k, v in zip(node.keys, node.values))
        if not isinstance(node, ast.Call):
            return False
        chain = _py_chain(node.func)
        if chain == ("sys", "stdin", "read"):
            return not node.args and not node.keywords
        if chain == ("json", "load"):
            return len(node.args) == 1 and _py_chain(node.args[0]) == ("sys", "stdin") and not node.keywords
        if chain == ("json", "loads"):
            return len(node.args) == 1 and isinstance(node.args[0], ast.Call) and data(node.args[0]) and not node.keywords
        if chain == ("json", "dumps"):
            return len(node.args) == 1 and data(node.args[0]) and all(
                k.arg in ("indent", "sort_keys", "ensure_ascii") and isinstance(k.value, ast.Constant)
                and isinstance(k.value.value, (int, bool, type(None))) for k in node.keywords)
        return False

    def root(node):
        if isinstance(node, ast.Name):
            return node.id in roots
        if not isinstance(node, ast.Call):
            return False
        chain = _py_chain(node.func)
        if chain in (("sys", "stdin", "read"), ("json", "load")):
            return data(node)
        return chain in (("json", "loads"), ("json", "dumps")) and data(node) and root(node.args[0])

    printed, preserves_root = False, True
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            continue  # capability validation has already limited imports to json/sys
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name) or not data(statement.value):
                return False
            name = statement.targets[0].id
            is_root = root(statement.value)
            variables.add(name)
            roots.discard(name)
            if is_root:
                roots.add(name)
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if not (isinstance(call.func, ast.Name) and call.func.id == "print"
                    or _py_chain(call.func) == ("sys", "stdout", "write")):
                return False
            if not call.args or not all(data(arg) for arg in call.args) or any(
                    k.arg not in ("end", "sep") or not isinstance(k.value, ast.Constant)
                    or not isinstance(k.value.value, str) or k.value.value.strip() for k in call.keywords):
                return False
            printed = True
            preserves_root = preserves_root and len(call.args) == 1 and root(call.args[0])
        else:
            return False
    return printed and preserves_root


def parse_bash_fetch(command, cwd, *, verbs=("standing-context",)):
    """(args, direct) when `command` is a standing-context fetch in the grammar
    above (`direct` False when output passes through a filter), else None.
    The lookup adapter may name the read-only rule verbs; boot uses only standing-context."""
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
    if len(words) not in (3, 4) or words[1][1] != "call" or words[2][1] not in verbs:
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
        f"  Bash, digest only (cheapest, ~200 characters a page; run every page in one parallel batch): "
        f"cd {REPO} && ./run.sh call standing-context "
        f"'{{\"detail\":\"boot\",\"page\":{first}}}' | jq -c '{{ok: .ok, rule_boot: {{digest: .rule_boot.digest}}}}'\n"
        f"  MCP (CARR connector):  standing-context with {{\"detail\":\"boot\",\"page\":{first}}}\n"
        f"  Bash: {os.path.join(REPO, 'run.sh')} call standing-context "
        f"'{{\"detail\":\"boot\",\"page\":{first}}}'\n"
        "Until then only these calls, other standing-context calls, the read-only rule verbs "
        "(applicable-rules, resolve-doctrine-rules, read-doctrine, search-doctrine, doctrine-index, "
        "doctrine-sections) and ToolSearch will run. A leading `cd <absolute repo path> &&` and a pipe "
        "into a filter (jq, python3 -c from the repo root, head) keep it a fetch; a filtered page counts "
        "when it prints the whole JSON or keeps the upstream .ok and .rule_boot.digest fields. "
        "Anything run after the fetch "
        "(&&, ;, ||, &) is not a fetch. "
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


def arm_session(session_id, source, now=None, *, agent_id=None):
    """Arm the gate for a session at SessionStart and return the context text
    (always under 10k characters: SessionStart context is capped there)."""
    stored = read_arm(session_id)
    response, reason = _live_page_one()
    boot = response.get("rule_boot") if isinstance(response, dict) else None
    live_armed = (isinstance(boot, dict) and boot.get("digest")
                  and int(boot.get("pages_total") or 0) >= 1)
    stored_armed = stored and stored.get("status") == "armed"
    same_corpus = (live_armed and stored_armed
                   and stored.get("digest") == boot.get("digest")
                   and int(stored.get("pages_total") or 0) == int(boot.get("pages_total") or 0))
    if agent_id and stored_armed and (same_corpus or not live_armed):
        _write_agent_epoch(session_id, agent_id)
        return fetch_instructions(list(range(1, int(stored["pages_total"]) + 1)),
                                  stored["digest"], int(stored["pages_total"]))

    arm = {"schema": SCHEMA, "source": str(source or ""), "armed_at": int(now or time.time()),
           "epoch": secrets.token_hex(6)}
    if live_armed:
        arm.update(status="armed", digest=boot["digest"], pages_total=int(boot["pages_total"]))
        if int(boot.get("total_chars") or 0) >= 1:
            arm["total_chars"] = int(boot["total_chars"])
        if agent_id:
            arm["by_agent"] = safe_key(agent_id, "agent")
        elif str(source or "") == "compact" and same_corpus:
            carry = _carry_epoch(session_id, stored)
            if carry:
                arm["carry_epoch"] = carry
        write_arm(session_id, arm)
        if agent_id:
            _write_agent_epoch(session_id, agent_id)
        return fetch_instructions(list(range(1, arm["pages_total"] + 1)), arm["digest"], arm["pages_total"])
    if str(reason or "").startswith("not_deployed"):
        arm.update(status="not_deployed", reason=reason)
        write_arm(session_id, arm)
        return NOT_DEPLOYED_NOTICE
    arm.update(status="unavailable", reason=reason or "unreachable")
    write_arm(session_id, arm)
    return f"{UNAVAILABLE_NOTICE} ({arm['reason']})"


def _carry_epoch(session_id, stored):
    """The epoch under which the main context last held a complete boot of the
    stored corpus: the stored epoch when it is complete there, else the one the
    stored arm already carried (a chain of unattributed compactions)."""
    if _boot_time(_fetch_dir(session_id, None, stored), stored) is not None:
        return safe_key(stored.get("epoch"), "e0")
    return stored.get("carry_epoch")


# ---------------------------------------------------------------- who compacted

def _iso_ts(value):
    try:
        text = str(value)
        return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _boot_time(folder, arm):
    """When this context's boot of `arm` completed (the newest page
    confirmation), or None when it is not complete."""
    total = int(arm.get("pages_total") or 0)
    if total < 1 or not set(range(1, total + 1)) <= _pages(_markers(folder), "c") or _short_text(folder, arm):
        return None
    try:
        return max(os.path.getmtime(os.path.join(folder, f"c{p}")) for p in range(1, total + 1))
    except OSError:
        return None


def _transcript_events(path):
    """(own boundaries, newest timestamp) of one Claude Code transcript, or
    None when it cannot be read. Own boundaries are the timestamps of the
    compact_boundary entries that belong to the file's own context: a
    sidechain (subagent) entry mirrored into a main file is not one."""
    try:
        if os.path.getsize(path) > MAX_TRANSCRIPT_BYTES:
            return None
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    sub = os.path.basename(os.path.dirname(path)) == "subagents"
    own, start = [], 0
    while True:
        at = data.find(b'"compact_boundary"', start)
        if at < 0:
            break
        head = data.rfind(b"\n", 0, at) + 1
        end = data.find(b"\n", at)
        end = len(data) if end < 0 else end
        start = end
        try:
            entry = json.loads(data[head:end])
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("subtype") != "compact_boundary":
            continue
        if not sub and (entry.get("isSidechain") or entry.get("agentId")):
            continue
        ts = _iso_ts(entry.get("timestamp"))
        if ts is not None:
            own.append(ts)
    # Bookkeeping lines (custom-title, last-prompt, ...) carry no timestamp and
    # come in bursts, and entries are only roughly in time order: take the
    # newest of the last few stamped lines.
    newest, stamped = None, 0
    for line in reversed(data.rstrip(b"\n").rsplit(b"\n", 512)[-512:]):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        ts = _iso_ts(entry.get("timestamp")) if isinstance(entry, dict) else None
        if ts is None:
            continue
        newest = ts if newest is None else max(newest, ts)
        stamped += 1
        if stamped >= 16:
            break
    return own, newest


def _main_transcript(session_id, transcript_path):
    """The main context's transcript the payload names, when it is one."""
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    path = os.path.realpath(os.path.expanduser(transcript_path))
    return path if os.path.basename(path) == f"{session_id}.jsonl" else None


def _stamp(ts):
    return time.strftime("%H:%M:%SZ", time.gmtime(ts))


def _main_compaction(session_id, payload, arm, carry_folder):
    """Whose compaction re-armed this main context: ("child", ts) when the
    transcripts show a subagent's and not this context's, ("own", ts) when
    the main transcript shows its own after its boot, (None, None) when they
    do not settle it."""
    deadline = time.monotonic() + SCAN_BUDGET_S
    boot = _boot_time(carry_folder, arm)
    main = _main_transcript(session_id, payload.get("transcript_path") or payload.get("transcriptPath"))
    if boot is None or main is None:
        return None, None
    events = _transcript_events(main)
    if events is None:
        return None, None
    own = [ts for ts in events[0] if ts > boot]
    if own:
        return "own", max(own)
    armed_at = int(arm.get("armed_at") or 0)
    if events[1] is None or events[1] <= armed_at:
        return None, None
    folder = os.path.join(main[:-len(".jsonl")], "subagents")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None, None
    for name in names:
        path = os.path.join(folder, name)
        if not (name.startswith("agent-") and name.endswith(".jsonl")):
            continue
        if time.monotonic() > deadline:
            return None, None
        try:
            if os.path.getmtime(path) < armed_at - COMPACT_SLACK_S:
                continue
        except OSError:
            continue
        child = _transcript_events(path)
        hits = [ts for ts in (child[0] if child else []) if armed_at - COMPACT_SLACK_S <= ts <= armed_at + 5]
        if hits:
            return "child", max(hits)
    return None, None


def _child_compacted(session_id, agent_id, payload, arm, folder):
    """The time a child's own transcript shows it compacted after its boot,
    when a compaction re-armed the session after that boot; else None."""
    if str(arm.get("source") or "") != "compact" or not _AGENT_FILE_ID.fullmatch(str(agent_id)):
        return None
    armed_at = int(arm.get("armed_at") or 0)
    boot = _boot_time(folder, arm)
    # armed_at is whole seconds: a boot inside that second is still checked.
    if boot is None or armed_at + 1 <= boot or f"k{armed_at}" in _markers(folder):
        return None
    main = _main_transcript(session_id, payload.get("transcript_path") or payload.get("transcriptPath"))
    if main is None:
        return None
    events = _transcript_events(os.path.join(main[:-len(".jsonl")], "subagents", f"agent-{agent_id}.jsonl"))
    if events is None:
        return None
    own = [ts for ts in events[0] if ts > boot]
    if own:
        return max(own)
    _touch(folder, f"k{armed_at}")
    return None


def _rearm_reason(arm, owner, when):
    """Why a main context that had read the rules is held again, naming the
    real source of the re-arm."""
    source = str(arm.get("source") or "")
    if arm.get("by_agent"):
        return (f"A subagent's SessionStart ({source}) found a new rule digest, so every context of "
                "this session reads the new boot. Read every page.\n")
    if source != "compact":
        return (f"This session was re-armed at SessionStart ({source}): pages read before it do "
                "not count, because this context no longer holds them. Read every page again.\n")
    if owner == "own":
        return (f"This context compacted at {_stamp(when)} (its transcript shows the compaction): "
                "pages read before it do not count, because this context no longer holds them. "
                "Read every page again.\n")
    return (f"This session was re-armed at SessionStart (compact) at {_stamp(int(arm.get('armed_at') or 0))}. "
            "Claude Code's SessionStart payload does not say whether this context or one of its "
            "subagents compacted, and the transcripts did not show it was a subagent, so pages read "
            "before it do not count. Read every page again.\n")


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
    owner, when = None, None
    if missing and not agent_id and arm.get("carry_epoch") and "a-own" not in names:
        # An unflagged SessionStart(compact) re-armed this context: carry its
        # earlier boot only when the transcripts show a child compacted.
        carried = _fetch_dir(session_id, None, {**arm, "epoch": arm["carry_epoch"]})
        if "a-child" in names:
            owner = "child"
        else:
            owner, when = _main_compaction(session_id, payload, arm, carried)
            if owner:
                _touch(folder, f"a-{owner}")
        if owner == "child" and _boot_time(carried, arm) is not None:
            return "allow", None
    elif "a-own" in names:
        owner = "own"
    if not missing and agent_id:
        compacted = _child_compacted(session_id, agent_id, payload, arm, folder)
        if compacted is not None:
            try:
                _write_agent_epoch(session_id, agent_id)
            except OSError as exc:
                return "deny", STATE_UNWRITABLE_NOTICE.format(why=str(exc))
            return _hold(_fetch_dir(session_id, agent_id, key), 0, (
                f"RULE BOOT: this subagent compacted at {_stamp(compacted)} after it read the rules "
                "(its transcript shows the compaction), so its earlier reads do not count. "
                "Read every page again.\n") + fetch_instructions(list(range(1, total + 1)), arm.get("digest"), total))
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
    lead = (f"Page(s) {', '.join(map(str, unreadable))} came back without the whole page or the boot's "
            "digest (a filter kept neither), so they do not count as read.\n") if unreadable else ""
    source = str(arm.get("source") or "")
    if not agent_id and not confirmed and source in ("compact", "resume", "clear"):
        # The main context's pages are keyed by the arm's epoch, so reads made
        # before this SessionStart do not count: say so, naming whose
        # compaction it was, or a context that remembers reading them (in its
        # summary) thinks the gate is broken.
        if owner == "own" and when is None:
            when = int(arm.get("armed_at") or 0)
        lead += _rearm_reason(arm, owner, when)
    return _hold(folder, len(confirmed), lead + fetch_instructions(missing, arm.get("digest"), total))


def _utf16_len(text):
    """A string's length as JavaScript counts it (rule-boot.js total_chars)."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


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
    got, by_digest = 0, False
    for p in range(1, int(arm.get("pages_total") or 0) + 1):
        try:
            with open(os.path.join(folder, f"c{p}"), encoding="utf-8") as fh:
                raw = fh.read(32).strip()
        except OSError:
            return True
        if raw == DIGEST_ONLY:
            by_digest = True
            continue
        if not raw.isdigit():
            return True
        got += int(raw)
    # A page confirmed through a filter by its digest carries no length; the
    # length test then cannot apply (WHAT A PIPED PAGE PROVES).
    return False if by_digest else got != want


INCONCLUSIVE_NOTICE = (
    "RULE BOOT: page {page}'s answer did not preserve a successful boot response, so it "
    "does not count as read. Fetch page {page} again without the pipe, or keep the upstream "
    "ok and digest fields with jq -c '{{ok: .ok, rule_boot: {{digest: .rule_boot.digest}}}}'.")

# The c<N> marker of a page confirmed by its digest through a filter: no length.
DIGEST_ONLY = "digest"


def _clear_stale(folder, page):
    """A confirmed page clears the outage, not-deployed and unreadable markers."""
    for stale in ("failed", "unsupported", f"u{page}"):
        try:
            os.unlink(os.path.join(folder, stale))
        except OSError:
            pass


def _bash_preserves_success(command):
    """A compact receipt must retain the source's ok and rule_boot fields.

    Arbitrary projections prove input origin but can rename error fields into
    success fields. Only identity formatting and these canonical jq projections
    retain the success contract when page text is omitted.
    """
    tokens = _lex(str(command or "").strip())
    stages = []
    for token in tokens or []:
        if token[:2] == ("op", "|"):
            stages.append([])
        elif stages:
            stages[-1].append(token[1])
    for stage in stages:
        prog, args = stage[0], stage[1:]
        if prog == "jq":
            flt = _jq_filter(args)
            if flt is not None and re.sub(r"\s+", "", flt) not in (
                    ".", "{ok:.ok,rule_boot:.rule_boot}",
                    "{ok:.ok,rule_boot:{digest:.rule_boot.digest}}"):
                return False
        elif prog in ("python", "python3") and args[:1] == ["-c"]:
            if not _py_preserves_root(args[1]):
                return False
    return bool(stages)


def _filtered_success(response, digest):
    """Read the canonical success envelope from stdout, never error substrings."""
    stdout = response.get("stdout", "") if isinstance(response, dict) else response
    if not isinstance(stdout, str):
        return False
    try:
        lines = stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if line.lstrip().startswith("{"))
        receipt = json.loads("\n".join(lines[start:]))
    except (ValueError, StopIteration):
        return False
    return (isinstance(receipt, dict) and receipt.get("ok") is True
            and isinstance(receipt.get("rule_boot"), dict)
            and receipt["rule_boot"].get("digest") == digest)


def observe(payload):
    """PostToolUse on a boot fetch: record what came back, re-arm the session
    on a new digest or page count. Returns a notice for the context, or None.

    A page is confirmed only by an answer that is that page (_is_page), and
    its text length is recorded so completion can be checked against the
    boot's total_chars. A filtered fetch can also be confirmed by a canonical
    upstream success envelope and armed digest, without length evidence. Other
    filtered answers are INCONCLUSIVE because the filter may be what failed; they
    neither confirm a page nor unlock the context as an outage."""
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
    interrupted = isinstance(response, dict) and response.get("interrupted")
    succeeded = (payload.get("hook_event_name") or "PostToolUse") == "PostToolUse" and not interrupted
    preserves_success = not direct and _bash_preserves_success((payload.get("tool_input") or payload.get("toolInput") or {}).get("command"))
    answer, boot = read_answer(response)
    if not direct and answer == "boot" and (not preserves_success or not _filtered_success(response, boot.get("digest"))):
        answer = "inconclusive"
    if interrupted or (not succeeded and answer == "boot"):
        answer = "inconclusive"
    if answer == "boot" and not _is_page(boot, page):
        answer = "inconclusive"
    if not direct and answer != "boot":
        answer = "inconclusive"
    arm = read_arm(session_id)
    # Only a FILTERED fetch can be confirmed by its digest alone: an unfiltered
    # answer printed everything, so a wrong page or a missing text is just that.
    if (not direct and answer == "inconclusive" and succeeded and arm and arm.get("status") == "armed"
            and preserves_success
            and _filtered_success(response, arm.get("digest"))):
        # The projected envelope retains upstream success and the armed digest.
        folder = _fetch_dir(session_id, agent_id, arm)
        _touch(folder, f"p{page}")
        _put(folder, f"c{page}", DIGEST_ONLY)
        _clear_stale(folder, page)
        return None
    if answer in ("boot", "out_of_range"):
        total = int((boot or {}).get("pages_total") or 0)
        digest = str((boot or {}).get("digest") or "")
        if total >= 1 and digest and (not arm or arm.get("status") != "armed"
                                      or arm.get("digest") != digest
                                      or int(arm.get("pages_total") or 0) != total):
            arm = {**(arm or {}), "schema": SCHEMA, "status": "armed", "digest": digest,
                   "pages_total": total, "rearmed_by": "fetch", "armed_at": int(time.time())}
            # No arm yet (SessionStart did not arm, or parallel fetches each see
            # none): derive the epoch from the digest, so racing writes agree and
            # no fetch's confirmed pages are stranded under a losing epoch.
            arm.setdefault("epoch", "fetch-" + safe_key(digest.replace("sha256:", ""), "none")[:12])
            arm.pop("reason", None)
            arm.pop("total_chars", None)
            arm.pop("carry_epoch", None)
            arm.pop("by_agent", None)
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
        _clear_stale(folder, page)
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
