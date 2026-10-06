#!/usr/bin/env python3
"""frozen-install-check.py — refuse an install command that is not frozen.

WHY THIS IS NOT ops/ci-dep-check.py. That check reads the LOCKFILES: it proves
requirements.lock is pinned, that it matches requirements.txt, and that npm
reports no high/critical advisory. It never reads a single install COMMAND. So a
tree can hold a perfect lockfile and still install around it — `npm install`
re-resolves and rewrites the lock it was handed, and `pip install -r
requirements.txt` installs the loose human-facing declaration (`>=` ranges)
rather than the pinned output beside it. Two runs a week apart then produce two
different dependency graphs from the same green tree, which is the exact
reproducibility hole the lock exists to close.

That is not hypothetical here. bin/worktree.sh's own header records
node_modules being LINKED rather than installed "because a per-worktree npm
install would drift from the canonical lockfile"; PR #834 then had to close the
trust boundary when the shared cache drifted anyway. Both are the same lesson
from the runtime side. This is the static side: an unfrozen install cannot get
into the tree in the first place.

WHAT COUNTS AS FROZEN

  npm     `npm ci` only. `npm install` / `npm i` / `npm add` re-resolve.
          A GLOBAL install (`-g`, `--global`) is a developer's CLI tool, not
          this repo's dependency graph, and is not judged.
  pnpm    `--frozen-lockfile`.
  yarn    `--frozen-lockfile` (v1) or `--immutable` (berry).
  pip     a `-r <file>` that names a `.lock`, or `--require-hashes`, or every
          named package pinned with `==`. `uv pip install` is judged the same
          way, since the store it uses changes nothing about resolution.

  The INSTALLER BOOTSTRAP (`pip install --upgrade pip`, setuptools, wheel) is
  not a project dependency and is deliberately out of subject. Say so out loud
  rather than leaving a silent hole: it is the one unpinned install this check
  allows, and it allows it because pinning the installer is a different
  argument from pinning the dependency graph.

WHAT IS SCANNED. Tracked files under the roots where this repo's automation
actually lives (SCAN_ROOTS below), plus the `scripts` block of each npm
project's package.json. Prose is not scanned: corpus/ and runbooks/ carry
install instructions for humans, and flagging those would train everyone to
ignore this check.

COMMENTS AND STRINGS. A match inside a comment is not a command, and this repo
comments heavily about the very commands it is refusing — bin/worktree.sh
explains why it does not run `npm install`, and .github/workflows/ci.yml has a
timing table with `pip install` in it. So comments are stripped per language
before matching. Python STRING literals are stripped too, because every pip
mention in this repo's Python is a human-facing hint ("psycopg not installed
(pip install 'psycopg[binary]')"), never an executed command.

  KNOWN BLIND SPOT, stated rather than discovered later: a Python file that
  shells out an install through a string literal is therefore invisible to this
  check. It is a real hole. It is accepted because this repo's install surface
  is shell and YAML, and because the alternative — flagging every hint string —
  produces a check people route around. If an install ever moves into Python,
  this needs a subprocess-aware pass.

EXCEPTIONS ARE ANNOUNCED, NEVER SILENT — the same discipline
ops/config/ci-check-scope.json applies to the gates class. Two kinds, and the
difference is who owns the fix:

  handoff   the finding is real and the file belongs to another owner's
            surface. Hosted-CI workflow files are the CI/CD hardening owner's;
            R05 is explicitly forbidden from editing them. These are listed in
            out/repo-hygiene-program/r05-contract-change-draft.md and print as
            HANDOFF on every run.
  exempt    the unfrozen form is deliberate and has a doctrine reason.

Neither fails the check, and both print every single run, so the coverage this
delivers is visible in its own output rather than buried in a config file.

STATUS: SHADOW, ON PURPOSE. ops/ci.sh's dependency class runs this on every
local and hosted run and PRINTS what it finds, but does not fail the class on
it. `ops/ci.sh --strict` is the required status check on main, so blocking here
would make this a required hosted gate — and hosted/required activation belongs
to the master plan's CI/CD hardening owner, not to slice R05 which built it. The
contract change asking for that flip, and the exact one-line diff it needs, is
out/repo-hygiene-program/r05-contract-change-draft.md. Run directly and this
exits nonzero: the enforcement is real, only its wiring is deferred.

Usage:
    ops/frozen-install-check.py [--root DIR] [--json]
Exit 0 clean, 1 on any unexcepted unfrozen install.
"""

import argparse
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tokenize

REPO = pathlib.Path(__file__).resolve().parent.parent
SCOPE = "ops/config/frozen-install-scope.json"

# Where this repo's automation lives. Prose trees are deliberately absent.
SCAN_ROOTS = ("bin/", "ops/", "tools/", "hooks/", "pipelines/", ".github/workflows/")
SCAN_FILES = ("run.sh",)
CODE_SUFFIXES = (".sh", ".zsh", ".bash", ".py", ".yml", ".yaml", ".js", ".mjs")
NPM_PROJECTS = ("mcp-server", "control-room", "workspace")

BOOTSTRAP = {"pip", "setuptools", "wheel", "distribute"}

# pip flags that consume the following token, so it is not a package name.
PIP_VALUE_FLAGS = {
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "--target", "-t", "--prefix",
    "--root", "--python", "--cache-dir", "--timeout", "--retries", "--proxy",
    "--trusted-host", "--platform", "--python-version", "--implementation",
    "--abi", "--only-binary", "--no-binary", "--report", "--log",
}

NPM_RE = re.compile(r"(?<![\w/.-])npm\s+((?:[^\s;|&]+\s+)*?)(install|i|add)(?![\w-])")
PNPM_RE = re.compile(r"(?<![\w/.-])pnpm\s+((?:[^\s;|&]+\s+)*?)(install|i|add)(?![\w-])")
YARN_RE = re.compile(r"(?<![\w/.-])yarn\s+((?:[^\s;|&]+\s+)*?)(install|add)(?![\w-])")
REDIRECT_RE = re.compile(r"^(?:\d*[<>]|&[<>])")
PIP_RE = re.compile(
    r"(?:(?<![\w.-])uv\s+pip|(?<![\w.-])python[\d.]*\s+-m\s+pip|(?<![\w.-])pip[\d.]*)"
    r"\s+install(?![\w-])(?P<rest>[^;|&\n]*)")


def rel(path):
    try:
        return str(pathlib.Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


# --------------------------------------------------------------- comment strip
def strip_hash_comments(text):
    """Drop `#` comments outside quotes, for shell and YAML.

    Quote-aware because `sed 's/^/#/'` and a YAML string containing a hash are
    both real in this tree, and a naive split at the first `#` would silently
    truncate the command it was meant to read.
    """
    out = []
    for line in text.splitlines():
        quote = None
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if quote:
                if ch == "\\" and quote == '"':
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def strip_slash_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def strip_python(text):
    """Drop COMMENT and STRING tokens; keep line numbers by rebuilding per line."""
    lines = text.splitlines()
    kept = [list(line) for line in lines]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable: fall back to comment-stripping only, rather than
        # silently scanning nothing.
        return strip_hash_comments(text)
    for tok in tokens:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, erow + 1):
            if row - 1 >= len(kept):
                continue
            line = kept[row - 1]
            start = scol if row == srow else 0
            end = ecol if row == erow else len(line)
            for col in range(start, min(end, len(line))):
                line[col] = " "
    return "\n".join("".join(line) for line in kept)


def join_continuations(text):
    """Join backslash-continued shell lines, keeping the FIRST line's number.

    bin/migrate-dell.sh's pip invocation puts `-r "$REPO/requirements.txt"` on
    the continuation line. Read line-by-line it looks like a bare
    `pip install -q --disable-pip-version-check`, which has no requirement file
    at all and would be judged on the wrong evidence.
    """
    out = []
    buf = None
    start = 0
    for n, line in enumerate(text.splitlines(), 1):
        if buf is None:
            buf, start = line, n
        else:
            buf += " " + line.strip()
        if buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1]
            continue
        out.append((start, buf))
        buf = None
    if buf is not None:
        out.append((start, buf))
    return out


def quoted_mask(line):
    """Per-column: is this character inside a shell quote?

    A quoted `npm install` in a shell script is a MESSAGE, not a command, and
    this repo is full of them — bin/deploy-worker.sh and bin/restore-rehearse.sh
    both tell the reader to "run npm install in mcp-server/" inside an error
    string, and bin/worktree.sh prints one when a frozen install fails. Only the
    match's START position is tested against this, so a real command with a
    quoted ARGUMENT — `pip install "psycopg[binary]"` in a workflow — is still
    read with its arguments intact.

    BLIND SPOT, stated: `sh -c "npm install"` and `eval` hide a real command
    inside a quote. Neither appears in this tree's install surface today.
    """
    mask = [False] * len(line)
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            mask[i] = True
            if ch == "\\" and quote == '"' and i + 1 < len(line):
                mask[i + 1] = True
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            mask[i] = True
        i += 1
    return mask


def prepared_lines(path, text):
    suffix = pathlib.Path(path).suffix
    if suffix == ".py":
        # Strings are already blanked out, so nothing is left to be "quoted".
        return [(n, line, [False] * len(line))
                for n, line in join_continuations(strip_python(text))]
    if suffix in (".js", ".mjs"):
        text = strip_slash_comments(text)
    else:
        text = strip_hash_comments(text)
    return [(n, line, quoted_mask(line)) for n, line in join_continuations(text)]


# ------------------------------------------------------------------- verdicts
def _unquote(tok):
    return tok.strip().strip("'\"")


def pip_verdict(rest):
    """Return None when frozen, else the reason it is not."""
    # Stop at the first redirection. `pip install --user uv </dev/null
    # >>/tmp/x.log 2>&1` otherwise reads `2` as a package name and the log path
    # as another, which reports a real finding with two invented details.
    tokens = []
    for raw in rest.split():
        if REDIRECT_RE.match(raw):
            break
        tokens.append(raw)
    if any(t.startswith("--require-hashes") for t in tokens):
        return None
    reqs, packages = [], []
    skip = False
    for i, raw in enumerate(tokens):
        if skip:
            skip = False
            continue
        tok = _unquote(raw)
        if raw.startswith("-"):
            flag, _, inline = raw.partition("=")
            if flag in ("-r", "--requirement"):
                if inline:
                    reqs.append(_unquote(inline))
                elif i + 1 < len(tokens):
                    reqs.append(_unquote(tokens[i + 1]))
                    skip = True
            elif flag in PIP_VALUE_FLAGS and not inline:
                skip = True
            continue
        if tok.startswith(("<", ">", "|", "&", "$(", "`")) or not tok:
            continue
        packages.append(tok)

    loose = [r for r in reqs if not r.endswith(".lock")]
    if loose:
        return ("installs " + ", ".join(loose)
                + " — the loose declaration, not requirements.lock")

    # A LOCAL PROJECT INSTALL IS NOT FROZEN BY DEFAULT. `pip install .` (or
    # `-e .`, or a path) reads the project's own pyproject/setup metadata and
    # RESOLVES its dependencies from the index, so it can pull a different
    # dependency graph on any two days — the same reproducibility hole a `>=`
    # range leaves. The first cut listed `.` and `-e` as always-frozen, which
    # forgave exactly that. Cross-family review, advisory 1. Resolution has to be
    # switched off (`--no-deps`) or pinned by hashes for a local install to
    # count as frozen.
    resolution_disabled = any(t.startswith("--no-deps") for t in tokens)
    local_targets = [p for p in packages
                     if p in (".", "./", "..")
                     or p.split("[")[0] in (".", "./", "..")
                     or p.startswith(("./", "../", "/"))
                     or "-e" in tokens and p == "."]
    if local_targets and not resolution_disabled:
        return ("local project install " + ", ".join(local_targets)
                + " resolves its own dependencies — add --no-deps or install a lock")

    unpinned = [p for p in packages
                if "==" not in p
                and re.sub(r"[-_.]+", "-", p.split("[")[0]).lower() not in BOOTSTRAP
                and p not in local_targets]
    if unpinned:
        return "unpinned package(s): " + ", ".join(unpinned)
    return None


def scan_text(path, text):
    findings = []
    for lineno, line, mask in prepared_lines(path, text):
        def is_message(m):
            return bool(mask) and m.start() < len(mask) and mask[m.start()]

        for m in NPM_RE.finditer(line):
            if is_message(m):
                continue
            flags, verb = m.group(1) or "", m.group(2)
            if re.search(r"(?<![\w-])(-g|--global)(?![\w-])", flags + " " + line[m.end():]):
                continue
            findings.append((lineno, line.strip(),
                             f"`npm {verb}` re-resolves and rewrites the lockfile — use `npm ci`"))
        for m in PNPM_RE.finditer(line):
            if is_message(m) or "--frozen-lockfile" in line:
                continue
            if re.search(r"(?<![\w-])(-g|--global)(?![\w-])", line):
                continue
            findings.append((lineno, line.strip(),
                             "`pnpm install` without --frozen-lockfile re-resolves"))
        for m in YARN_RE.finditer(line):
            if is_message(m) or "--frozen-lockfile" in line or "--immutable" in line:
                continue
            if re.search(r"(?<![\w-])(-g|--global)(?![\w-])", line):
                continue
            findings.append((lineno, line.strip(),
                             "`yarn install` without --frozen-lockfile/--immutable re-resolves"))
        for m in PIP_RE.finditer(line):
            if is_message(m):
                continue
            why = pip_verdict(m.group("rest"))
            if why:
                findings.append((lineno, line.strip(), why))
    return findings


# --------------------------------------------------------------------- scope
def load_scope(root):
    path = root / SCOPE
    if not path.exists():
        return {"handoff": [], "exempt": []}
    data = json.loads(path.read_text())
    return {"handoff": data.get("handoff", []), "exempt": data.get("exempt", [])}


def excepted(scope, kind, relpath, line):
    for entry in scope.get(kind, []):
        if entry.get("path") != relpath:
            continue
        match = entry.get("match")
        if match and match not in line:
            continue
        return entry
    return None


# ---------------------------------------------------------------------- files
def tracked_files(root):
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, text=True, check=True).stdout
        names = [n for n in out.split("\0") if n]
    except (subprocess.CalledProcessError, FileNotFoundError):
        names = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    keep = []
    for name in names:
        if name in SCAN_FILES or name.startswith(SCAN_ROOTS):
            if name.endswith(CODE_SUFFIXES):
                keep.append(name)
        if name.endswith("package.json") and name.split("/")[0] in NPM_PROJECTS:
            keep.append(name)
    return sorted(set(keep))


def scan_package_json(path, text):
    try:
        scripts = (json.loads(text).get("scripts") or {})
    except json.JSONDecodeError:
        return []
    findings = []
    for name, body in scripts.items():
        for _, _, why in scan_text("script.sh", str(body)):
            findings.append((0, f'scripts.{name}: {body}', why))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root).resolve()

    scope = load_scope(root)
    problems, handoffs, exemptions = [], [], []
    scanned = 0

    for name in tracked_files(root):
        path = root / name
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        scanned += 1
        found = (scan_package_json(name, text) if name.endswith("package.json")
                 else scan_text(name, text))
        for lineno, line, why in found:
            hit = excepted(scope, "handoff", name, line)
            if hit:
                handoffs.append((name, lineno, why, hit.get("reason", "")))
                continue
            hit = excepted(scope, "exempt", name, line)
            if hit:
                exemptions.append((name, lineno, why, hit.get("reason", "")))
                continue
            problems.append((name, lineno, line, why))

    if args.json:
        print(json.dumps({
            "scanned": scanned,
            "problems": [{"path": p, "line": n, "text": t, "why": w}
                         for p, n, t, w in problems],
            "handoff": [{"path": p, "line": n, "why": w, "reason": r}
                        for p, n, w, r in handoffs],
            "exempt": [{"path": p, "line": n, "why": w, "reason": r}
                       for p, n, w, r in exemptions],
        }, indent=2))
        return 1 if problems else 0

    for name, lineno, why, reason in handoffs:
        print(f"  HANDOFF: {name}:{lineno} — {why}\n           {reason}")
    for name, lineno, why, reason in exemptions:
        print(f"  EXEMPT:  {name}:{lineno} — {why}\n           {reason}")

    if problems:
        print("\nfrozen-install check failed:", file=sys.stderr)
        for name, lineno, line, why in problems:
            print(f"  - {name}:{lineno}: {why}", file=sys.stderr)
            print(f"      {line}", file=sys.stderr)
        print("\nFrozen spellings: `npm ci`; `pip install -r requirements.lock`;\n"
              "pnpm/yarn with --frozen-lockfile. To build a worktree its own frozen\n"
              "runtime: ./run.sh worktree --install", file=sys.stderr)
        return 1

    print(f"frozen installs: {scanned} automation file(s) scanned, 0 unfrozen"
          + (f" ({len(handoffs)} handed off, {len(exemptions)} excepted)"
             if handoffs or exemptions else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
