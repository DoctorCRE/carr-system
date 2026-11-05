#!/usr/bin/env python3
"""corpus_renders.py — the deny set for CORPUS RENDERS: files git owns and the
Drive/home copies merely display.

WHY THIS EXISTS. Joe, 2026-08-10, after the nightly chain failed on it:
"create a hook that prevents anything on me or dells side from writing to the
drive files ever again."

THE INCIDENT IT CLOSES. On 2026-08-09 the chair-verdict-bars work was written
into the DRIVE copies of eight council files. Git has been canonical for the
doctrine corpus since 2026-08-06, so those edits landed on renders. Nothing
stopped them and nothing noticed until the nightly corpus push refused to
overwrite them the next morning and exited 2. Nothing was lost that time — the
push refuses rather than clobbering, which is the one thing standing between
this failure and silent data loss. Rely on that twice and the second time is a
`--push` running before anyone reads the conflict.

The cost was not the eight files. It was that the chain went red for a reason
nobody could distinguish from the mypy tripwire that had already been red for
three days, so the chain's exit code stopped carrying information.

WHY record-home-gate DIDN'T ALREADY COVER IT, and this is the interesting part.
That gate's docstring names the `~/.claude` and `My Drive/.claude` trees as
explicitly out of scope: "harness-required; repo-canonical from P6." The word
doing the work is repo-canonical — the reasoning was that because git owns
them, something else governs them. Nothing did. An exclusion justified by
another control's existence is only as good as that control, and here the other
control was an assumption.

WHAT IT DENIES — TWO PROJECTIONS, because the Drive tree has two owners and a
file is worth protecting regardless of which tool writes it:
  * every `source` in corpus/manifest.json — `vault:` doctrine under CARR AI and
    `home:` portable meta-skills under ~/.claude, written by corpus-sync.py
  * every file under claude-tree/{skills,agents}, projected onto
    My Drive/.claude by bin/sync-skills.sh

THE SECOND ONE WAS ADDED 2026-08-10 WITH THE TWO-OWNERS RULING (loop #312), and
the reason is worth stating. Those 24 files were originally in the corpus set, so
this module covered them for free. The ruling moved ownership to claude-tree/ and
took them OUT of the set — which would have silently removed the write-block from
exactly the files whose loss started the investigation. Protection follows the
FILE, never the tool that happens to own it this week.

NEITHER LIST IS RETYPED. One is read from the manifest, the other walked from the
directory. Same discipline as record-home-gate parsing exporters/targets.py: a
duplicated list is the two-homes disease one layer down, and it goes stale on the
first file anyone adds.

WHAT IT DOES NOT TOUCH. Anything not in the manifest. The repo's own
corpus/ copies — those are the canonical originals and editing them is the
CORRECT path this gate steers people toward. Scratchpads, Life AI, non-corpus
files anywhere.

DELL'S SIDE. The gate resolves its roots from `CARR_VAULT` when set and falls
back to a glob over `~/Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI`, so
it binds on his machine the moment he pulls, with no per-machine edit. His
settings already carry the two hooks this module plugs into.

The Bash door refuses uninspectable write operands. The file-tool door retains
its existing error policy.
"""

import ast
import re
import shlex
import glob
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "corpus", "manifest.json")


def _vault():
    """CARR AI's absolute path. Env first so Dell needs no edit; then Joe's
    literal path; then a glob, which is what makes this portable to any Drive
    account without the gate knowing whose it is."""
    env = os.environ.get("CARR_VAULT")
    if env and os.path.isdir(env):
        return env
    joe = os.path.join(os.path.expanduser("~"), "Library", "CloudStorage",
                       "GoogleDrive-joe.bookout.carr.us@gmail.com", "My Drive", "CARR AI")
    if os.path.isdir(joe):
        return joe
    for hit in sorted(glob.glob(os.path.expanduser(
            "~/Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI"))):
        if os.path.isdir(hit):
            return hit
    return joe                       # unreachable Drive: paths just won't match


def roots():
    v = _vault()
    return {"vault": v,
            "drive": os.path.dirname(v),          # "My Drive"
            "home": os.path.expanduser("~")}


def _abs(source, rt):
    """One manifest `source` -> absolute path. `drive:x` and `home:x` are
    root-prefixed; a bare path is vault-relative."""
    if ":" in source and source.split(":", 1)[0] in ("drive", "home"):
        kind, rel = source.split(":", 1)
        return os.path.join(rt[kind], rel)
    return os.path.join(rt["vault"], source)


SKILLS_TREE = os.path.join(REPO, "claude-tree")   # bin/sync-skills.sh's source


def _skills_renders(rt):
    """{realpath: label} for the OTHER projection onto My Drive/.claude.

    Added 2026-08-10 with the two-owners ruling (loop #312). Those 24 files used
    to be in the corpus set, so this module protected them for free. The ruling
    moved ownership to claude-tree/ and took them OUT of the set — which would
    have silently removed the write-block from exactly the files whose loss
    started the whole investigation. Protection follows the file, not the tool
    that happens to own it this week.

    Enumerated from the claude-tree directory rather than listed, same discipline
    as reading the corpus manifest: a list goes stale on the first skill anyone
    adds."""
    out = {}
    dst = os.path.join(rt["drive"], ".claude")
    for sub in ("skills", "agents"):
        src_root = os.path.join(SKILLS_TREE, sub)
        for dirpath, _dirs, files in os.walk(src_root):
            for fn in files:
                if fn.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), src_root)
                try:
                    real = os.path.realpath(os.path.join(dst, sub, rel))
                except Exception:
                    continue
                out[real] = f"claude-tree:{sub}/{rel}"
    return out


def render_map():
    """{realpath: source-label} for every projected render — the doctrine corpus
    AND the skills/agents tree. Empty on any failure, which is the fail-open
    path."""
    out = {}
    rt = roots()
    try:
        man = json.load(open(MANIFEST))
        for f in man.get("files", []):
            src = f.get("source")
            if not src:
                continue
            try:
                out[os.path.realpath(_abs(src, rt))] = src
            except Exception:
                continue
    except Exception:
        pass
    try:
        out.update(_skills_renders(rt))
    except Exception:
        pass
    return out


def _fix(source):
    """The exact repo path to edit instead, so the denial names the next move
    rather than only the refusal. A refusal without the alternative is how a
    gate gets switched off."""
    if source.startswith("claude-tree:"):
        return "claude-tree/" + source.split(":", 1)[1]
    if ":" in source and source.split(":", 1)[0] in ("drive", "home"):
        kind, rel = source.split(":", 1)
        return f"corpus/_{kind}/{rel}"
    return f"corpus/{source}"


def _publish(source):
    """The command that publishes the repo original — different per projection."""
    if source.startswith("claude-tree:"):
        return "./bin/sync-skills.sh --apply"
    return "python3 tools/corpus-sync.py --push"


def verdict(path, tool="this write"):
    """Deny reason for an absolute path, or None. `path` may be user-relative."""
    try:
        real = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return None
    src = render_map().get(real)
    if not src:
        return None
    owner = ("the skills-and-agents tree" if src.startswith("claude-tree:")
             else "the doctrine corpus")
    return (
        f"'{src}' is a PROJECTED RENDER, not a source file. Git owns it through "
        f"{owner}, and this copy exists only to be read — the next projection either "
        f"overwrites {tool} or refuses and turns the nightly chain red. Both happened "
        f"on 2026-08-10: the corpus push refused eight council files in the morning, "
        f"and the skills projection silently deleted the panel-verdict rule from the "
        f"roster file that afternoon. "
        f"EDIT THIS INSTEAD: ~/carr-system/{_fix(src)} — then run `{_publish(src)}` "
        f"to publish it. That is the only path that reaches BOTH partners; a "
        f"Drive-side edit reaches neither, because Dell's copy is projected from git "
        f"too."
    )


def _shell_word(raw):
    """Decode quoting and HOME without executing shell code."""
    parts = re.findall(r"'[^']*'|\"(?:\\[\s\S]|[^\"\\])*\"|\\[\s\S]|[^'\"\\]+", raw)
    expanded = []
    for part in parts:
        if part.startswith("'") or part.startswith("\\"):
            expanded.append(part)
            continue
        part = re.sub(r"\$\{HOME\}|\$HOME(?![A-Za-z0-9_])",
                      lambda _: shlex.quote(os.path.expanduser('~')) if not part.startswith('"')
                      else os.path.expanduser('~').replace('\\', '\\\\').replace('"', '\\\"'), part)
        if part.startswith('~/') or part == '~':
            part = shlex.quote(os.path.expanduser('~')) + part[1:]
        expanded.append(part)
    words = shlex.split(''.join(expanded))
    if len(words) != 1:
        raise ValueError('write operand does not resolve to one word')
    return words[0]


def shell_write_verdict(command, cwd=None, _depth=0):
    """Inspect supported write operands; refuse unresolved write paths."""
    from cmd_text import SHELL_BOUNDARIES, SHELL_REDIRECTS, strip_inert_text
    if _depth > 4:
        raise ValueError("nested shell inspection limit exceeded")
    text = strip_inert_text(command).replace("-m <message>", "-m message")
    word = r"(?:'[^']*'|\"(?:\\[\s\S]|[^\"\\])*\"|\\[\s\S]|[^\s'\"\\;&|<>])+"
    tokens = re.findall(r'[;&|<>]+|\n|' + word, text)
    cwd = os.path.realpath(cwd or os.getcwd())
    possible_cwds = {cwd}
    protected = render_map()
    if not protected:
        raise ValueError('corpus render deny set is unavailable')

    def inspect(path):
        if any(ch in path for ch in '$`*?[]{}') or path.startswith('~'):
            raise ValueError('dynamic write operand; use a literal path or HOME')
        for base in possible_cwds:
            real = os.path.realpath(os.path.join(base, path))
            if real in protected:
                return verdict(real, 'this command')
        return None

    segment = []
    for token in tokens + [';']:
        if token not in SHELL_BOUNDARIES and token != '\n':
            segment.append(token)
            continue
        words, outputs, stdin_scripts, i = [], [], [], 0
        while i < len(segment):
            raw = segment[i]
            if raw in SHELL_REDIRECTS:
                if i + 1 >= len(segment):
                    raise ValueError('missing redirection target')
                target = _shell_word(segment[i + 1])
                if raw == "<<<":
                    stdin_scripts.append(target)
                if raw in {'>', '>>', '>|', '<>', '&>', '&>>'} or (
                        raw == '>&' and not target.isdigit() and target != '-'):
                    outputs.append(target)
                i += 2
            else:
                words.append(_shell_word(raw))
                i += 1
        segment = []
        if not words and not outputs:
            continue
        program = os.path.basename(words[0]) if words else ''
        args = words[1:]
        if program.startswith('python') or program in {'sh', 'bash', 'zsh', 'dash'}:
            for script in stdin_scripts:
                script = shlex.join([program, '-c', script]) if program.startswith('python') else script
                for base in possible_cwds:
                    reason = shell_write_verdict(script, base, _depth + 1)
                    if reason:
                        return reason
        if program == 'cd':
            if len(args) != 1 or args[0] == '-':
                raise ValueError('cannot resolve working directory change')
            inspect(args[0])
            possible_cwds.update(os.path.realpath(os.path.join(base, args[0]))
                                 for base in tuple(possible_cwds))
            if len(possible_cwds) > 32:
                raise ValueError('working directory inspection limit exceeded')
        if program in {'sh', 'bash', 'zsh', 'dash'}:
            for j, arg in enumerate(args[:-1]):
                if arg.startswith('-') and not arg.startswith('--') and 'c' in arg:
                    for base in possible_cwds:
                        reason = shell_write_verdict(args[j + 1], base, _depth + 1)
                        if reason:
                            return reason
        writers = {'tee', 'cp', 'mv', 'rsync', 'sed'}
        if program not in writers and program not in {'echo', 'printf', 'cat', 'grep', 'rg'}:
            if any(os.path.basename(a.strip('()')) in writers for a in words):
                raise ValueError('wrapped or compound writer; use a direct literal command')
        if program in {'cp', 'mv', 'rsync'}:
            operands, options = [], True
            for arg in args:
                if options and arg == '--':
                    options = False
                elif options and arg.startswith('-') and arg != '-':
                    if not re.fullmatch(r'-[abdfinprRuvzHLPhAX]+', arg):
                        raise ValueError('unsupported destination option; use a final literal destination')
                else:
                    operands.append(arg)
            args = operands
        if program in writers:
            if program == 'tee' or (program == 'sed' and any(a.startswith(('-i', '--in-place')) for a in args)):
                outputs.extend(a for a in args if a and not a.startswith('-'))
            elif program != 'sed' and args:
                outputs.append(args[-1])
                # A directory destination writes each source's basename too.
                if program in {'cp', 'mv', 'rsync'}:
                    outputs.extend(os.path.join(args[-1], os.path.basename(a))
                                   for a in args[:-1] if a and not a.startswith('-'))
        if program.startswith('python') and '-c' in args:
            index = args.index('-c') + 1
            if index >= len(args):
                raise ValueError('missing Python command')
            tree = ast.parse(args[index])
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute) and node.func.attr in {
                        'open', 'write_text', 'write_bytes'}:
                    raise ValueError('indirect Python write; use a literal open path')
                if isinstance(node.func, ast.Name) and node.func.id == 'open':
                    mode = node.args[1] if len(node.args) > 1 else next(
                        (k.value for k in node.keywords if k.arg == 'mode'), ast.Constant('r'))
                    if not isinstance(mode, ast.Constant) or not isinstance(mode.value, str):
                        raise ValueError('dynamic Python open mode')
                    if any(c in mode.value for c in 'wax+'):
                        path = node.args[0] if node.args else next(
                            (k.value for k in node.keywords if k.arg == 'file'), None)
                        if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
                            raise ValueError('dynamic Python write path')
                        outputs.append(path.value)
        if outputs and (re.search(r'\b(?:HOME|PWD)=', text)
                        or any(re.match(r'[A-Za-z_][A-Za-z0-9_]*=', a) for a in words)
                        or program in {'env', 'sudo', 'eval', 'exec', 'xargs'}):
            raise ValueError('wrapped or assigned write context; use a direct literal command')
        for path in outputs:
            reason = inspect(path)
            if reason:
                return reason
    return None
