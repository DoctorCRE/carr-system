"""jev_precheck.py — gather the facts that decide whether a shell command works.

THE MEASUREMENT THIS FILE EXISTS BECAUSE OF, run 2026-09-18 against 464 real
shell calls taken from one session's own transcript.

Asking a judgment "will this command fail?" over the COMMAND TEXT ALONE has no
discriminating power at all. Twenty-six calls that actually failed and
twenty-six that actually succeeded both scored a median of 0.44. At a 0.8
threshold the question caught fewer real failures than it raised false alarms.
A control set of successful commands is the only reason that is readable — the
failure scores on their own look respectable.

Ask the SAME question with ONE relevant environment fact attached and the same
eight commands separate at a median of 0.94 against 0.17, seven of eight right.
The facts that did it were mundane and all knowable in advance:

  · ops/ holds no __init__.py, so it is not a package and a relative import
    from a sibling module raises.
  · jev_judge.judge() returns the decoded response with answers nested under
    an "answers" key, so reading one level shallower raises.
  · ops/ci.sh has no --class option and exits on an unrecognised argument.
  · hooks/guard-unattended.py refuses any recursive or forced delete outright,
    whatever the target is.

So the judgment was never the hard part or the expensive part — one question is
around half a second and a fraction of a cent. THE WORK IS COLLECTING THE
FACTS, and that is ordinary deterministic code. This module is that code. It
does not call the model and it does not decide anything: it reads a command and
returns what is true about the things the command names.

WHAT IT WILL NOT DO. It never runs the command, never imports a module to
inspect it, and never executes repository code to find out what that code does.
Importing a module to learn its interface runs everything at that module's top
level, which is exactly the side effect a pre-check exists to avoid. Every fact
below is read from text on disk.

IT IS A LIBRARY AND MUST STAY ONE. No shebang and no main guard: either turns a
.py file into a registered script entrypoint in the sealed source inventory,
moves the frontier, and owes a forward-only registry successor. The detector is
a regex over the whole file with no notion of docstrings, so the construct is
described here and never spelled. ops/typesafe_client.py carries the long form.
"""

import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A path-shaped token. Deliberately conservative: a bare word is not a path, so
# something has to look like a path before this module claims a fact about it.
PATH_TOKEN = re.compile(r"(?<![\w/.-])((?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]+)")

# `--flag` or `-f` as written on a command line.
FLAG_TOKEN = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")

# What a script declares it accepts. Covers the spellings used across this
# repository's shell entry points: a case arm and a long-option test. When no
# flags are found, declared_flags() decides between "could not tell" and
# "accepts nothing" by whether the script reads its arguments at all — those
# two must never look the same, and the first version of this file collapsed
# them, which is a defect recorded in full at declared_flags().
CASE_ARM = re.compile(r"^\s*\(?\s*(--?[\w-]+(?:\s*\|\s*--?[\w-]+)*)\s*\)", re.M)
OPTION_TEST = re.compile(r"[\"']?(--[\w-]+)[\"']?\s*\)")

# Does the script read its arguments at all? This separates "declares no
# options" from "has a parser this could not follow".
ARGUMENT_HANDLING = re.compile(r"\$\{?[1-9@*]|\bgetopts\b|\bshift\b|\$\{?#\b")

# Actions the unattended guard refuses outright, read from its own behaviour
# rather than guessed. Each is a refusal a command cannot argue its way out of.
GUARD_REFUSALS = (
    (re.compile(r"\brm\s+(-\w*[rf]\w*\s+)+"), "hooks/guard-unattended.py refuses any "
     "recursive or forced delete outright, whatever the target is"),
    (re.compile(r"\bsudo\b"), "sudo is on the deny list and is refused before it runs"),
    (re.compile(r"\b(diskutil|dd)\b"), "this command is on the deny list and is refused "
     "before it runs"),
)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def referenced_paths(command, repo=REPO):
    """Repository paths the command names, with whether each actually exists.

    Only paths inside the repository are reported. A command naming something
    outside it is not this module's business, and claiming a fact about an
    absolute system path invites a confident wrong answer about a machine this
    module cannot see.
    """
    facts = []
    for match in dict.fromkeys(PATH_TOKEN.findall(command)):
        cleaned = match[2:] if match.startswith("./") else match
        # ONE guard, not two. An earlier version also rejected anything holding
        # ".." before this line, and mutation testing showed that check could be
        # deleted with every test still green — because abspath() normalises the
        # walk-up away and this comparison then rejects it anyway. Redundant
        # defence is not free: it reads as the thing doing the work, so the line
        # that IS doing the work stops being tested by anyone's attention.
        full = os.path.join(repo, cleaned)
        if os.path.commonpath((os.path.abspath(full), os.path.abspath(repo))) != os.path.abspath(repo):
            continue
        looks_like_repo_path = os.path.exists(full) or cleaned.split("/")[0] in (
            "ops", "hooks", "bin", "tools", "pipelines", "evals", "migrations",
            "mcp-server", "audits")
        if not looks_like_repo_path:
            continue
        facts.append({"path": cleaned, "exists": os.path.exists(full),
                      "is_dir": os.path.isdir(full)})
    return facts


# An import statement, in any of the spellings a command can carry: a heredoc,
# a -c string, or a quoted fragment. The note below fires ONLY when one of these
# is present, which is the whole difference between a fact and a nuisance.
IMPORT_STATEMENT = re.compile(r"(?:^|[\s;'\"])(?:from\s+[.\w]+\s+import\b|import\s+[.\w]+)")


def python_package_facts(command, repo=REPO):
    """Whether directories the command imports from are importable packages.

    A relative import from a sibling raises when the directory holds no
    __init__.py, and that single fact decided one of the measured failures.

    GATED ON AN ACTUAL IMPORT, and the gate is the point. The first version
    emitted this note for any command that merely NAMED a path inside such a
    directory, so a plain grep drew a confident irrelevant fact. That is the
    same degeneracy measured in the rule selector the same day: a note that
    fires on topic rather than on its condition. A fact-gatherer that produces
    a fact about everything has produced nothing, and it is worse than silence
    because it fills the state with noise the judgment then has to ignore.
    """
    if not IMPORT_STATEMENT.search(command):
        return []
    facts = []
    for directory in dict.fromkeys(
            part.split("/")[0] for part in PATH_TOKEN.findall(command) if "/" in part):
        full = os.path.join(repo, directory)
        if not os.path.isdir(full):
            continue
        is_package = os.path.exists(os.path.join(full, "__init__.py"))
        facts.append({
            "directory": directory,
            "is_python_package": is_package,
            "note": (f"{directory}/ holds no __init__.py, so it is not a Python package "
                     "and a relative import from a sibling module inside it raises")
            if not is_package and any(
                name.endswith(".py") for name in os.listdir(full)) else None,
        })
    return [fact for fact in facts if fact["note"]]


def module_interface(path, repo=REPO):
    """The public callables a Python file defines, read WITHOUT importing it.

    Parsed from the syntax tree, because importing a module to learn its shape
    runs everything at its top level — the precise side effect a pre-check is
    supposed to prevent. Returns None when the file cannot be parsed, so a
    caller can tell "no interface" from "could not read it".
    """
    source = _read(os.path.join(repo, path))
    if source is None:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    signatures = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                not node.name.startswith("_"):
            args = [a.arg for a in node.args.args]
            kwonly = [a.arg for a in node.args.kwonlyargs]
            signatures.append(f"{node.name}({', '.join(args + ['*'] + kwonly)})"
                              if kwonly else f"{node.name}({', '.join(args)})")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            signatures.append(f"class {node.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    signatures.append(f"{target.id} (constant)")
    return signatures


def declared_flags(path, repo=REPO):
    """Options a shell script declares it accepts, read from its own source.

    Returns None when the options cannot be read, which is NOT the same as an
    empty set: a script whose parser this cannot follow must not be reported as
    accepting nothing, because that turns "unknown" into a confident refusal.
    """
    source = _read(os.path.join(repo, path))
    # An extensionless file is a shell script here by convention, so both are
    # allowed. Spelling that as endswith((".sh", "")) does NOT work and shipped
    # in the first version: the empty string is a suffix of everything, so the
    # guard admitted every file and a Python module was read for shell options.
    basename = os.path.basename(path)
    if source is None or not (basename.endswith(".sh") or "." not in basename):
        return None
    flags = set()
    for arm in CASE_ARM.findall(source):
        flags.update(part.strip() for part in arm.split("|"))
    flags.update(OPTION_TEST.findall(source))
    if flags:
        return sorted(flags)
    # NO FLAGS FOUND MEANS ONE OF TWO DIFFERENT THINGS, and collapsing them is
    # a real defect that shipped in the first version of this function: it
    # returned `sorted(flags) or None`, so a readable script declaring no
    # options became indistinguishable from one whose parser could not be
    # followed — the exact conflation the docstring above forbids, and it made
    # unknown_flags miss a bogus flag passed to a no-option script. The tell is
    # whether the script touches its arguments at all. One that never reads $1,
    # $@ or getopts genuinely accepts nothing; one that does has a parser this
    # could not follow, and unknown is the honest answer.
    return None if ARGUMENT_HANDLING.search(source) else []


def guard_refusals(command):
    """Actions a guard refuses before the command runs. Exact, not judged."""
    return [note for pattern, note in GUARD_REFUSALS if pattern.search(command)]


def unknown_flags(command, repo=REPO):
    """Flags passed to a repository script that the script does not declare.

    Only reported for scripts whose options could actually be read. Silence
    here means "could not tell", never "the flag is fine".
    """
    findings = []
    for fact in referenced_paths(command, repo):
        if not fact["exists"] or fact["is_dir"]:
            continue
        declared = declared_flags(fact["path"], repo)
        if declared is None:
            continue
        after = command.split(fact["path"], 1)[-1].split("&&")[0].split("|")[0]
        passed = [flag for flag in FLAG_TOKEN.findall(after) if flag not in declared]
        if passed:
            findings.append(
                f"{fact['path']} declares only {', '.join(declared)} and exits on an "
                f"unrecognised argument; this passes {', '.join(passed)}")
    return findings


def environment_facts(command, repo=REPO):
    """Everything known about what this command names. The state for a judgment.

    Returns a mapping, not prose, so a caller decides what to send. Empty
    sections are dropped: a fact list padded with "nothing found" entries makes
    a state look informative when it is not, and the measurement behind this
    module is that a thin state produces a confident meaningless answer.
    """
    facts = {}
    missing = [f["path"] for f in referenced_paths(command, repo) if not f["exists"]]
    if missing:
        facts["paths_that_do_not_exist"] = missing
    packages = python_package_facts(command, repo)
    if packages:
        facts["import_notes"] = [fact["note"] for fact in packages]
    refusals = guard_refusals(command)
    if refusals:
        facts["guard_refusals"] = refusals
    flags = unknown_flags(command, repo)
    if flags:
        facts["undeclared_options"] = flags
    interfaces = {}
    for fact in referenced_paths(command, repo):
        if fact["exists"] and fact["path"].endswith(".py") and not fact["is_dir"]:
            signatures = module_interface(fact["path"], repo)
            if signatures:
                interfaces[fact["path"]] = signatures[:25]
    if interfaces:
        facts["module_interfaces"] = interfaces
    return facts
