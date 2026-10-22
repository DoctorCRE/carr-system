"""Read-only rule-fetch classification used by the per-call lookup.

The boot gate is retired. These helpers retain the existing lookup import
contract and legacy epoch reads until the per-call lookup is replaced.
"""
import ast


import builtins


import json


import os


import re


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


CARR_MCP_PREFIXES: tuple[str, ...] = ("mcp__carr__", "mcp__carr_records__", "mcp__claude_ai_CARR_Record_Layer__",
                     "mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__")

CARR_MCP_PREFIXES += tuple(prefix.removeprefix("mcp__") for prefix in CARR_MCP_PREFIXES)

READ_ONLY_RULE_VERBS = frozenset({
    "standing-context", "applicable-rules", "resolve-doctrine-rules",
    "read-doctrine", "search-doctrine", "doctrine-index", "doctrine-sections",
})


REACH_TOOLS = frozenset({"ToolSearch"})


_SAFE_CHAR = re.compile(r"[A-Za-z0-9_./~+:=,@%-]")


_REDIRECT = re.compile(r"(?:2>&1|2>\s*/dev/null|<\s*/dev/null)(?=[\s|]|$)")


_JQ_OPTS = frozenset({"-r", "-c", "-S", "-M", "-C", "-j", "-a", "--raw-output", "--compact-output",
                      "--sort-keys", "--tab", "--monochrome-output", "--color-output",
                      "--ascii-output", "--join-output"})


_JQ_REFUSED = re.compile(
    r"\$(?:ENV|__)|\b(?:env|input|inputs|input_filename|input_line_number|import|include|"
    r"modulemeta|get_search_list|get_prog_origin|get_jq_origin|debug|stderr|halt_error)\b")


_HEAD_TAIL_ARGS = re.compile(r"(?:-n ?\+?\d{1,7}|-c ?\+?\d{1,9}|-\d{1,7}|-n\+?\d{1,7}|-c\+?\d{1,9})?")


_JSON_TOOL_ARGS = re.compile(r"(?:\s*(?:--indent \d{1,2}|--sort-keys|--compact|--no-ensure-ascii|--tab))*")


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


_PY_UNSAFE_BUILTINS = frozenset(dir(builtins)) - _PY_BUILTINS


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


def _agent_epoch_path(session_id, agent_id):
    return os.path.join(_session_dir(session_id), "agents",
                        safe_key(agent_id, "agent") + ".epoch")


def _read_agent_epoch(session_id, agent_id):
    try:
        with open(_agent_epoch_path(session_id, agent_id), encoding="utf-8") as fh:
            return safe_key(fh.read().strip(), "invalid")
    except FileNotFoundError:
        return None


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
    if name in ("Bash", "run_terminal_command") and isinstance(tool_input, dict):
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
