"""door-reachability-check.py — every executable artifact and repo skill has a
door that runs it, or a written declaration saying why it has none.

WHY THIS EXISTS (Work Request WR-000284, defect 96b57c6c). The pstack and
Pocock skills sat in the tree for a week with nothing that loaded them. Measured
on main 989f4122 the same day: 63 scripts under ops/ tools/ bin/ lib/ pipelines/
evals/ security/ were reachable from no door at all, 80 more only from a test,
4 repo SKILL.md files were named nowhere, and 4 ops/*-gate.py had neither a
`# ci: db-gate` marker nor a caller. Every registry here proves an artifact
EXISTS. Three checks ask whether anything CALLS one, each over a narrow lane:
ops/judgment-wiring-selftest.py (judgment libraries), ops/reachability-check.py
(declared controls) and ops/ci.sh's `# ci: db-gate` self-registration loop.
This generalises them to every script and skill.

THE GRAPH. Nodes are the artifacts under audit: scripts under the audited
trees (including their zsh scripts and extensionless executables such as
bin/dot-relay), hooks/ code, and repo SKILL.md files. A file reaches an artifact when its text
names the artifact's basename, or its stem when the stem is at least
STEM_MIN characters (shorter stems are ordinary words), and a Python file
reaches every .py node whose module name it imports, at any length. A skill's needle is its
directory name; a short one counts only in a form that names a skill
(`skills/x`, `/x`, `` `x` ``, `**x**`). Breadth-first search runs in three
tiers, and the first tier to reach a file decides its status:

  wired       reached from a production door (ops/ci.sh, ops/githooks/,
              .github/workflows/, ops/launchd/, run.sh, the hook configs,
              mcp-server/src/, any ops/*-gate.py marked `# ci: db-gate`,
              the same glob ci.sh runs)
  ci-only     reached only from a test-shaped file
  prose-only  reached only from AGENTS.md, CLAUDE.md, a README.md or docs/**.md
              (a JSON inventory under docs/ is a record, not prose)

A plugin's README, docs/ and plugin.json are a production door for that
plugin's own skills when AGENTS.md or CLAUDE.md names the plugin, and prose
otherwise.

WHAT AN EDGE IS NOT. An edge is a mention, not a proven call: a comment, a
preflight file list or a SQL string in a reached file counts the same as an
invocation, and two files sharing a basename share the edge. So a wired status
is an upper bound and an unreachable one is trustworthy. Verbs, workflows and
launchd services are roots here, never audited; ops/reachability-check.py owns
whether hooks and launchd jobs are registered.

DECLARATIONS live in ops/config/door-inventory.v1.json: one_shot (ran once by
design; evidence is the commit or receipt), hand_tool (a human runs it by name;
evidence is the doc or run.sh line), inert (built, no caller yet; evidence is
the loop or Work Request that will wire it, so an inert row must name a
WR-NNNNNN or a loop number). A declaration means "no production
door, on purpose", so it goes stale when the artifact becomes wired or is
deleted, and the stale entry is itself a finding.

VERDICT. Exit 1 on an unreachable undeclared artifact, a stale declaration, or
an invalid one (unknown class, placeholder reason, no evidence). ci-only and
prose-only are printed with their bound action and do not fail yet (Joe's
2026-10-12 ruling: measure first). Repository content only: no machine state,
no network, no database, so a bare runner and a Mac agree.

    python3 ops/door-reachability-check.py
    python3 ops/door-reachability-check.py --repo PATH
    python3 ops/door-reachability-check.py --why PATH   # the files that reach PATH
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import deque

DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = "ops/config/door-inventory.v1.json"
SCHEMA = "door-inventory/v1"
WORK_REQUEST = "WR-000284"

AUDIT_DIRS = ("ops/", "tools/", "bin/", "scripts/", "lib/", "pipelines/", "generators/",
              "evals/", "security/", "control-room/", "mcp-server/scripts/", "mcp-server/tools/")
CODE_SUFFIXES = (".py", ".sh", ".mjs", ".js", ".ts")
SHELL_SUFFIXES = (".zsh", ".bash")
STEM_MIN = 10

# Records and history: they name artifacts by construction, so they are neither
# callers nor audited. The manifest is here because it names every declared path.
INERT_PREFIXES = ("corpus/", "claude-tree/", "out/", "db/", "migrations/", "audits/",
                  "phase0/", ".claude/worktrees/")
INERT_FILES = (MANIFEST,)
WALK_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}
MAX_BYTES = 2 << 20

PRODUCTION_FILES = ("ops/ci.sh", "run.sh", "ops/config/hooks.json", "ops/config/codex-hooks.json",
                    "ops/config/delegation-gate-hook.json", ".claude/settings.json")
DB_GATE_MARKER = re.compile(r"^# ci: db-gate", re.M)
DB_GATE_GLOB = re.compile(r"ops/[^/]+-gate\.py")
PROSE_NAMES = ("AGENTS.md", "CLAUDE.md", "README.md")
PLUGIN = re.compile(r"^plugins/([^/]+)/")

TIERS = ("wired", "ci-only", "prose-only")
CLASSES = ("one_shot", "hand_tool", "inert")
PLACEHOLDER = re.compile(r"^\W*(n/?a|none|tbd|todo|trivial|minor|unknown|-+)\W*$", re.I)
MIN_REASON_WORDS = 6
LOOP_OR_WR = re.compile(r"\bWR-\d{6}\b|\bloop #?\d+\b", re.I)

BOUND_ACTION = {
    "ci-only": "exercised only by a test; wire it to a door or declare it in "
               f"{MANIFEST} ({WORK_REQUEST})",
    "prose-only": "named only in AGENTS.md, CLAUDE.md, a README or docs/; wire it to a door "
                  f"or declare it in {MANIFEST} ({WORK_REQUEST})",
}


def tree_files(repo: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", repo, "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return sorted(p for p in set(out.stdout.splitlines())
                          if p and os.path.isfile(os.path.join(repo, p)))
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for base, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in WALK_SKIP]
        found += [os.path.relpath(os.path.join(base, n), repo) for n in names]
    return sorted(found)


def read(repo: str, rel: str) -> str:
    path = os.path.join(repo, rel)
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return ""
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def is_test_shaped(rel: str) -> bool:
    base = os.path.basename(rel)
    return (base.startswith(("test-", "test_")) or base.endswith("-selftest.py")
            or ".test." in base or any(s in "/" + rel for s in ("/test/", "/tests/")))


def is_inert(rel: str) -> bool:
    return rel in INERT_FILES or rel.startswith(INERT_PREFIXES) or "/fixtures/" in "/" + rel


def is_script_artifact(repo: str, rel: str) -> bool:
    """A script under the audited trees or hooks/: a code suffix, a zsh or bash
    script, or an extensionless file that starts with a shebang."""
    if is_test_shaped(rel) or rel.startswith("mcp-server/src/"):
        return False
    if rel.startswith("hooks/"):
        return rel.endswith(CODE_SUFFIXES + SHELL_SUFFIXES)
    if not rel.startswith(AUDIT_DIRS):
        return False
    if rel.endswith(CODE_SUFFIXES + SHELL_SUFFIXES):
        return True
    if "." in os.path.basename(rel):
        return False
    try:
        with open(os.path.join(repo, rel), "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def is_skill(rel: str) -> bool:
    return os.path.basename(rel) == "SKILL.md"


def needles(rel: str) -> list[str]:
    if is_skill(rel):
        name = os.path.basename(os.path.dirname(rel))
        if len(name) >= STEM_MIN:
            return [name]
        return [f"skills/{name}", f"/{name}", f"`{name}`", f"**{name}**"]
    base = os.path.basename(rel)
    stem = os.path.splitext(base)[0]
    return [base, stem] if len(stem) >= STEM_MIN else [base]


TOKEN = re.compile(r"[\w.-]+")
PY_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import[ \t]+\(?([\w, \t]+)|import[ \t]+([\w., \t]+))", re.M)
SKILL_FORM = re.compile(r"(?<![\w-])(skills/|/|`|\*\*)([\w-]+)(`|\*\*)?")


def imported_modules(text: str) -> set[str]:
    """Module names a Python file imports, at any length: `from lib.pgrow import
    fetch_one` and `from lib import dot_relay` are unambiguous where a bare
    five-letter word in prose is not."""
    found: set[str] = set()
    for frm, names, plain in PY_IMPORT.findall(text):
        found.update(frm.split("."))
        found.update(n.split(" as ")[0].strip() for n in names.split(","))
        for mod in plain.split(","):
            found.update(mod.split(" as ")[0].strip().split("."))
    found.discard("")
    return found


def mentions(text: str) -> set[str]:
    r"""Every string a needle can equal, read off the text.

    A run of [\w.-] is split on dots so a module path (`pkg.module_name`) and
    a basename ending a sentence (`module_name.py.`) both yield their parts.
    One set lookup per distinct run replaces an alternation of thousands of
    literals, which Python's regex engine tries one by one at every position
    (two minutes over this tree, against seconds here).
    """
    found: set[str] = set()
    for run in set(TOKEN.findall(text)):
        parts = run.split(".")
        found.add(run.strip("."))
        found.update(parts)
        found.update(f"{a}.{b}" for a, b in zip(parts, parts[1:]))
    for prefix, name, suffix in SKILL_FORM.findall(text):
        if prefix in ("/", "skills/"):
            found.update(("/" + name, prefix + name))
        elif prefix == suffix:
            found.add(f"{prefix}{name}{suffix}")
    return found


class Graph:
    def __init__(self, repo: str, nodes: list[str]) -> None:
        self.repo = repo
        self.by_needle: dict[str, set[str]] = {}
        self.by_module: dict[str, set[str]] = {}
        for rel in nodes:
            for n in needles(rel):
                self.by_needle.setdefault(n, set()).add(rel)
            if rel.endswith(".py"):
                self.by_module.setdefault(os.path.basename(rel)[:-3], set()).add(rel)
        self.cache: dict[str, set[str]] = {}

    def edges(self, rel: str) -> set[str]:
        if rel not in self.cache:
            text = read(self.repo, rel)
            hits: set[str] = set()
            for n in mentions(text) & self.by_needle.keys():
                hits |= self.by_needle[n]
            if rel.endswith(".py"):
                for n in imported_modules(text) & self.by_module.keys():
                    hits |= self.by_module[n]
            hits.discard(rel)
            self.cache[rel] = hits
        return self.cache[rel]


def roots(repo: str, files: list[str], skills: list[str]) -> dict[str, list[tuple[str, set[str] | None]]]:
    """Each tier's roots, as (file, the targets it may reach or None for any)."""
    callers = [f for f in files if not is_inert(f)]
    production: list[tuple[str, set[str] | None]] = []
    ci_only: list[tuple[str, set[str] | None]] = []
    prose: list[tuple[str, set[str] | None]] = []
    named_text = read(repo, "AGENTS.md") + read(repo, "CLAUDE.md")
    for f in callers:
        if (f in PRODUCTION_FILES or f.startswith(("ops/githooks/", "mcp-server/src/"))
                or (f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml")))
                or (f.startswith("ops/launchd/") and f.endswith(".plist"))
                or (DB_GATE_GLOB.fullmatch(f) and DB_GATE_MARKER.search(read(repo, f)))):
            production.append((f, None))
        elif is_test_shaped(f):
            ci_only.append((f, None))
        plugin = PLUGIN.match(f)
        if plugin and (os.path.basename(f) in ("README.md", "plugin.json")
                       or f.startswith(plugin.group(0) + "docs/")):
            own = {s for s in skills if s.startswith(plugin.group(0))}
            named = re.search(rf"(?<![\w-])plugins/{re.escape(plugin.group(1))}(?![\w-])", named_text)
            (production if named else prose).append((f, own))
        if os.path.basename(f) in PROSE_NAMES or (f.startswith("docs/") and f.endswith(".md")):
            prose.append((f, None))
    return {"wired": production, "ci-only": ci_only, "prose-only": prose}


def classify(graph: Graph, tier_roots: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Each reached file's tier, and the file that first reached it."""
    status: dict[str, str] = {}
    parent: dict[str, str] = {}
    for tier in TIERS:
        frontier: deque[str] = deque()
        for root, allowed in tier_roots[tier]:
            status.setdefault(root, tier)
            for t in graph.edges(root):
                if (allowed is None or t in allowed) and t not in status:
                    status[t], parent[t] = tier, root
                    frontier.append(t)
        while frontier:
            src = frontier.popleft()
            for t in graph.edges(src):
                if t not in status:
                    status[t], parent[t] = tier, src
                    frontier.append(t)
    return status, parent


def load_declarations(repo: str, findings: list[str]) -> dict[str, dict]:
    path = os.path.join(repo, MANIFEST)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        findings.append(f"invalid manifest  {MANIFEST} — {e}")
        return {}
    if doc.get("schema") != SCHEMA:
        findings.append(f"invalid manifest  {MANIFEST} — schema must be {SCHEMA!r}")
    declared: dict[str, dict] = {}
    for entry in doc.get("declared", []):
        rel = str(entry.get("path", ""))
        reason = str(entry.get("reason", "")).strip()
        evidence = str(entry.get("evidence", "")).strip()
        if rel in declared:
            findings.append(f"invalid declaration  {rel} — declared twice")
        elif entry.get("class") not in CLASSES:
            findings.append(f"invalid declaration  {rel} — class {entry.get('class')!r} "
                            f"is not one of {', '.join(CLASSES)}")
        elif PLACEHOLDER.match(reason) or len(reason.split()) < MIN_REASON_WORDS:
            findings.append(f"invalid declaration  {rel} — placeholder reason {reason!r}; "
                            f"say in at least {MIN_REASON_WORDS} words why it has no door")
        elif not evidence or PLACEHOLDER.match(evidence):
            findings.append(f"invalid declaration  {rel} — no evidence (commit, receipt, doc "
                            "path, run.sh line, loop or Work Request)")
        elif entry["class"] == "inert" and not LOOP_OR_WR.search(evidence):
            findings.append(f"invalid declaration  {rel} — inert needs the loop or Work Request "
                            "that will wire it (WR-NNNNNN or loop #N) as evidence")
        declared[rel] = entry
    return declared


def check(repo: str, why: str | None = None) -> tuple[bool, list[str]]:
    files = tree_files(repo)
    live = [f for f in files if not is_inert(f)]
    skills = [f for f in live if is_skill(f)]
    artifacts = sorted([f for f in live if is_script_artifact(repo, f)] + skills)
    status, parent = classify(Graph(repo, artifacts), roots(repo, files, skills))
    if why is not None:
        chain = [why]
        while chain[-1] in parent:
            chain.append(parent[chain[-1]])
        return True, [f"{why}: {status.get(why, 'unreachable')}", *(f"  <- {c}" for c in chain[1:])]

    findings: list[str] = []
    declared = load_declarations(repo, findings)
    existing = set(files)
    buckets: dict[str, list[str]] = {t: [] for t in (*TIERS, "declared")}
    for rel in sorted(declared):
        if rel not in existing:
            findings.append(f"stale declaration  {rel} — the path no longer exists; delete the entry")
        elif rel not in artifacts:
            findings.append(f"invalid declaration  {rel} — not an artifact under audit")
        elif status.get(rel) == "wired":
            findings.append(f"stale declaration  {rel} — now wired; delete the entry")
    for rel in artifacts:
        tier = status.get(rel)
        if tier == "wired":
            buckets["wired"].append(rel)
        elif rel in declared:
            buckets["declared"].append(rel)
        elif tier:
            buckets[tier].append(rel)
        else:
            findings.append(f"unreachable  {rel}")

    counts = ", ".join(f"{len(buckets[k])} {k}" for k in (*TIERS, "declared"))
    lines = [f"door-reachability: {'FAIL' if findings else 'OK'} — {counts}"]
    for tier in ("ci-only", "prose-only"):
        lines.append(f"{tier}: {BOUND_ACTION[tier]}")
        lines += [f"  {rel}" for rel in buckets[tier]]
    lines.append("ci-only and prose-only do not fail yet (Joe's 2026-10-12 ruling: measure first).")
    # Findings last: ops/ci.sh shows a failing inventory check's final lines.
    if findings:
        lines += [f"  {f}" for f in findings]
        lines.append(f"  Wire each unreachable artifact to a door, or declare it in {MANIFEST} "
                     "with class one_shot|hand_tool|inert, a specific reason and its evidence.")
        lines.append(f"door-reachability: FAIL — {len(findings)} finding(s) above")
    return not findings, lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--why", metavar="PATH", help="print the chain of files that reaches PATH")
    args = ap.parse_args()
    ok, lines = check(os.path.abspath(args.repo), args.why)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
