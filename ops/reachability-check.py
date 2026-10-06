#!/usr/bin/env python3
"""reachability-check.py — a declared control that no live execution path
references is not built, it is a file.

THE 2026-08-23 COMPLETION-INTEGRITY COUNCIL, fix D. Every gate this repository
owns asks whether a thing EXISTS. gate-integrity.py hashes all fifty hooks/*.py
whether or not any settings file names one. config-as-code compares plist BODIES
between the repo and the machine. The enforcement map records which control
implements which rule. Not one of them asks the only question that separates a
control from a text file: does anything CALL it?

So these sat in the tree, green, for weeks:

  hooks/ledger-boundary-sweep.py   Its own documentation has read "WRITTEN BUT
                                   REGISTERED NOWHERE" since the 2026-08-06 #214
                                   audit. Its docstring says it blocks; it blocks
                                   nothing. It is hashed in gate-baseline.json
                                   and named by the enforcement map, both of
                                   which made it look covered.
  ops/launchd/com.carr.fleet-sync  At discovery, a plist no service in
                                   services.json declared, so no health row could
                                   mention it. The recovery registration closed
                                   this seed; the generic fixture keeps the class
                                   under test.
  tools/sync-rule-admission.py     Until PR #529 its only caller was CI, against
                                   the disposable database CI stands up. CI proved
                                   the manifest and the schema agreed; nothing
                                   ever installed it in production, so there was
                                   no production door and therefore no drift for
                                   anyone to notice. bin/sync-rule-admission-prod.sh
                                   exists precisely to be that non-CI caller.

WHAT THIS IS NOT, and the constraint is the council's own: NOT a daemon, NOT a
nightly step, and NOT a reference graph over the whole repository. A graph that
counts a comment, a history file or a dead test helper as a caller never fails,
and a check that never fails is the disease it was built for. The inventory
below is the SPEC — four narrow lanes, each with a named source of declaration
and a named source of reference. Widening it is a decision, not a refactor.

  lane       declared in                     reachable when it is named by
  ---------  ------------------------------  --------------------------------
  hook       hooks/*-*.py                    ops/config/hooks.json (the global
                                             block the repo manages) or the
                                             project .claude/settings.json
  hook       hooks/*_*.py (helper modules)   imported by another hooks/*.py
  launchd    ops/launchd/*.plist             a service in ops/config/services.json
  installer  bin/install-*.sh, bin/sync-*.sh,  any tracked file that is not CI,
             tools/sync-*.py, hooks/install-*.py  not a selftest, and not an
                                             inert record
  registry   rule-enforcement-map.json's     its implementation files exist, and
             control_catalog                 at least one named hook is registered

WHAT IT DELIBERATELY DOES NOT READ: the machine. No launchctl, no ~/.claude, no
network, no database — the same standard the other inventory checks in ci.sh's
gates class meet, so it gives the same answer on a bare runner and on Joe's Mac.
The machine-state half of the launchd question already has two owners,
tools/scheduler-truth.py and ops/launchd-plist-parity.py, and this check does not
restate what they observe. It asks the repository-visible question those two
cannot ask on a runner: is this job declared to the fleet at all?

TOMBSTONES, and why one is not a mute button. An entry may be marked
intentionally-not-wired in ops/config/reachability-tombstones.json. The mark
must carry a REASON and a REOPEN CONDITION, it must name something that still
exists, and it stops applying the moment the entry becomes reachable — at which
point the stale mark is itself the finding. A completed lifecycle should leave
nothing behind pointing at it, and that has to hold for the exemption list too
or the exemption list becomes the next graveyard.

    ops/reachability-check.py                 # the report
    ops/reachability-check.py --json          # machine-readable findings
    ops/reachability-check.py --repo PATH     # measure some other tree

EXIT CODES: 0 clean, 1 findings, 2 the tree could not be read at all.
"""
import argparse
import json
import os
import re
import subprocess
import sys

DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REGISTRATION_SURFACES = ("ops/config/hooks.json", ".claude/settings.json")
SERVICES = "ops/config/services.json"
ENFORCEMENT_MAP = "ops/config/rule-enforcement-map.json"
TOMBSTONES = "ops/config/reachability-tombstones.json"

LANES = ("hook", "launchd", "installer", "registry")

# CI AND SELFTESTS ARE NOT DOORS. This is the entire content of the #529 lesson:
# a tool exercised only by the suite that proves it correct has been proven
# correct about a world nobody lives in.
CI_PREFIXES = (".github/",)
CI_FILES = ("ops/ci.sh",)

# RECORDS, NOT CALLERS. Each of these names implementation files by construction
# — gate-baseline.json hashes every hook that exists, the enforcement map lists
# every control, db/schema.sql is a rendered dump, audits/ and phase0/ are dated
# history. If they counted as references nothing in this check could ever fail,
# which is exactly the over-match grok named as the likely first-draft defect.
INERT_PREFIXES = (
    "db/", "migrations/", "audits/", "phase0/", "out/",
    "claude-tree/",      # the retired noncanonical settings mirror; bin/sync-settings.sh
                         # says so in its own header. A retired mirror is not a registration.
    ".claude/worktrees/",
)
INERT_FILES = (
    "ops/config/gate-baseline.json",
    ENFORCEMENT_MAP,
    TOMBSTONES,          # a tombstone excuses a reference; it is never one
    "package-lock.json",
)

INSTALLER_GLOBS = (
    ("bin", "install-", ".sh"),
    ("bin", "sync-", ".sh"),
    ("tools", "sync-", ".py"),
    ("hooks", "install-", ".py"),
)

# Prefixes the enforcement map uses for an implementation that is deliberately
# not a file in this repository. `path:` is a repo path with a redundant label.
EXTERNAL_PREFIXES = ("external:", "command:", "service:")
PATH_PREFIX = "path:"

WALK_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "out",
             ".mypy_cache", ".pytest_cache", ".claude"}

# A CALLER IS A SCRIPT, A CONFIG OR A RUNBOOK. Bounding the scan by extension
# and size is not tidiness: this check runs on every push inside ci.sh's gates
# class, which the 2026-08-23 actions-minutes council put on a wall-clock budget,
# and reading the whole 32 MB working tree to look for nine path strings spent
# two seconds of it on files that cannot contain an invocation.
CALLER_SUFFIXES = (
    ".py", ".sh", ".zsh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".json",
    ".md", ".toml", ".yml", ".yaml", ".plist", ".sql", ".txt", ".tsv", ".cfg",
)
CALLER_MAX_BYTES = 1 << 20


def read(repo, rel):
    try:
        with open(os.path.join(repo, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def tree_files(repo):
    """Every file this check may treat as a caller.

    git ls-files with --others is deliberate: a caller added in the working tree
    but not yet committed still counts. The alternative is the tracked-only
    shape that makes a check pass locally and fail at pre-push for a reason the
    diff does not show. A tree without git falls back to a pruned walk, which is
    what the selftest's fixtures use.
    """
    try:
        out = subprocess.run(
            ["git", "-C", repo, "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return sorted(set(p for p in out.stdout.splitlines() if p))
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for base, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in WALK_SKIP]
        for n in names:
            rel = os.path.relpath(os.path.join(base, n), repo)
            found.append(rel)
    return sorted(found)


def is_inert(rel):
    return rel in INERT_FILES or rel.startswith(INERT_PREFIXES)


def is_ci(rel):
    return rel in CI_FILES or rel.startswith(CI_PREFIXES)


def is_selftest(rel):
    base = os.path.basename(rel)
    return base.endswith("-selftest.py") or base.startswith("test-") or base.startswith("test_")


def eligible_callers(repo, files):
    """Tracked files that could plausibly INVOKE something, with their text."""
    out = {}
    for rel in files:
        if is_inert(rel) or is_ci(rel) or is_selftest(rel):
            continue
        if not rel.endswith(CALLER_SUFFIXES) and "." in os.path.basename(rel):
            continue
        try:
            if os.path.getsize(os.path.join(repo, rel)) > CALLER_MAX_BYTES:
                continue
        except OSError:
            continue
        body = read(repo, rel)
        if body is not None:
            out[rel] = body
    return out


def registered_hooks(repo):
    """Every hook name a settings surface can cause to run.

    ANY .py TOKEN, NOT JUST A hooks/ PATH, and the first draft of this check got
    it wrong. `hooks/run-record-gate.py drift-claim-gate.py` is a dispatcher
    line: run-record-gate re-executes the named gate under the repository
    interpreter because Claude invokes hooks through a system Python that has no
    psycopg. Matching only `hooks/<name>.py` therefore reported drift-claim-gate
    and drift-assertion-gate as registered nowhere while they fire on every
    matching tool call — two false positives in the very first real run, which
    is how a new gate earns a reputation for crying wolf and gets skipped.
    """
    names = set()
    for surface in REGISTRATION_SURFACES:
        body = read(repo, surface)
        if body:
            names.update(re.findall(r"([A-Za-z0-9_.-]+\.py)", body))
    return {os.path.basename(n) for n in names}


def listdir(repo, rel):
    try:
        return sorted(os.listdir(os.path.join(repo, rel)))
    except OSError:
        return []


def finding(lane, entry, why, remedy):
    return {"lane": lane, "entry": entry, "why": why, "remedy": remedy}


# ------------------------------------------------------------------ hook lane
def hook_findings(repo):
    out = []
    registered = registered_hooks(repo)
    names = [n for n in listdir(repo, "hooks") if n.endswith(".py")]
    # A hyphen cannot appear in a Python module name, so a hyphenated file in
    # hooks/ can only ever be run as a command: it is an ENTRYPOINT, and the
    # only thing that runs it is a settings registration. This is a structural
    # fact about the filenames, not a guess about intent.
    entrypoints = [n for n in names if "-" in n and not n.startswith("install-")]
    modules = [n for n in names if "-" not in n]

    for name in sorted(entrypoints):
        if name not in registered:
            out.append(finding(
                "hook", f"hooks/{name}",
                "no settings file registers it, so it never runs; existing in the "
                "tree and being hashed in the gate baseline is not the same as "
                "being installed",
                f"register it in {REGISTRATION_SURFACES[0]} (then "
                f"`ops/config-as-code.py install --apply`), or tombstone it in "
                f"{TOMBSTONES} with a reason and a reopen condition"))

    bodies = {n: (read(repo, f"hooks/{n}") or "") for n in names}
    for name in sorted(modules):
        stem = name[: -len(".py")]
        pattern = re.compile(rf"^\s*(?:from\s+(?:hooks\.)?{re.escape(stem)}\s+import|import\s+(?:hooks\.)?{re.escape(stem)})\b", re.M)
        if not any(pattern.search(body) for other, body in bodies.items() if other != name):
            out.append(finding(
                "hook", f"hooks/{name}",
                "a helper module in hooks/ that no other hook imports; nothing "
                "can reach it and no registration ever will, because a module is "
                "not a command",
                f"import it from the hook it belongs to, delete it, or tombstone "
                f"it in {TOMBSTONES}"))
    return out


# --------------------------------------------------------------- launchd lane
def launchd_findings(repo):
    out = []
    declared = set(re.findall(r"ops/launchd/([A-Za-z0-9_.-]+\.plist)",
                              read(repo, SERVICES) or ""))
    for name in listdir(repo, "ops/launchd"):
        if not name.endswith(".plist"):
            continue
        if name not in declared:
            out.append(finding(
                "launchd", f"ops/launchd/{name}",
                f"no service in {SERVICES} names it, so it is outside the fleet's "
                "intended-loaded manifest and no health row can ever mention it — "
                "its failure is not `unknown`, it is unrepresentable",
                f"declare it as a service `deploy_mechanism` in {SERVICES}, or "
                f"tombstone it in {TOMBSTONES} with a reason and a reopen condition"))
    return out


# ------------------------------------------------------------- installer lane
def installer_entries(repo):
    out = []
    for directory, prefix, suffix in INSTALLER_GLOBS:
        for name in listdir(repo, directory):
            if name.startswith(prefix) and name.endswith(suffix):
                out.append(f"{directory}/{name}")
    return sorted(set(out))


def installer_findings(repo, callers):
    out = []
    for rel in installer_entries(repo):
        doors = [c for c, body in callers.items() if c != rel and rel in body]
        if not doors:
            out.append(finding(
                "installer", rel,
                "nothing outside CI and the selftests names it, so whatever it "
                "installs has no door into the world it was written for — the "
                "PR #529 shape, where a tool proven green against CI's throwaway "
                "database had never once run against production",
                f"give it a caller a human or a schedule actually reaches (a bin/ "
                f"door, a launchd job, a runbook), or tombstone it in {TOMBSTONES}"))
    return out


# -------------------------------------------------------------- registry lane
def catalog(repo):
    try:
        return json.loads(read(repo, ENFORCEMENT_MAP) or "{}").get("control_catalog", {})
    except ValueError:
        return {}


def looks_like_repo_path(ref):
    return "/" in ref and " " not in ref


def registry_findings(repo, registered):
    out = []
    for key, entry in sorted(catalog(repo).items()):
        impls = entry.get("implementation", []) or []
        missing, hooks = [], []
        for raw in impls:
            ref = raw[len(PATH_PREFIX):] if raw.startswith(PATH_PREFIX) else raw
            if ref.startswith(EXTERNAL_PREFIXES):
                continue
            if not looks_like_repo_path(ref):
                continue
            if ref.startswith("hooks/") and ref.endswith(".py"):
                hooks.append(ref)
            if not os.path.exists(os.path.join(repo, ref)):
                missing.append(ref)
        if missing:
            out.append(finding(
                "registry", key,
                "the enforcement map records this control as implemented by "
                + ", ".join(missing) + ", which is not in the tree; a registry "
                "row outliving its implementation reports coverage that cannot fire",
                f"repoint or remove the entry in {ENFORCEMENT_MAP} (rule c0b38d80: "
                f"re-bless ops/config/gate-baseline.json in the same commit)"))
        elif hooks and not any(os.path.basename(h) in registered for h in hooks):
            out.append(finding(
                "registry", key,
                "every hook this control names (" + ", ".join(hooks) + ") is "
                "registered in no settings file, so the map reports a control "
                "that no session can ever trip",
                f"register the hook, or tombstone this entry in {TOMBSTONES} so "
                f"the map's claim and the machine's reality are the same sentence"))
    return out


# ---------------------------------------------------------------- tombstones
def load_tombstones(repo):
    body = read(repo, TOMBSTONES)
    if body is None:
        return [], None
    try:
        return json.loads(body).get("tombstones", []), None
    except ValueError as exc:
        return [], f"{TOMBSTONES} is not valid JSON ({exc})"


def entry_exists(repo, lane, entry):
    if lane == "registry":
        return entry in catalog(repo)
    return os.path.exists(os.path.join(repo, entry))


def apply_tombstones(repo, raw, marks):
    """Returns (surviving findings, tombstone findings).

    A mark is checked in three ways because each failure is a different lie: an
    incomplete mark hides a decision nobody made, a mark on something that is
    gone is a pointer left behind by a completed lifecycle, and a mark on
    something now reachable is an exemption that outlived its cause and would
    silently absorb the NEXT regression of the same entry.
    """
    problems = []
    excused = set()
    keyed = {(f["lane"], f["entry"]) for f in raw}
    for i, mark in enumerate(marks):
        where = f"{TOMBSTONES}[{i}]"
        if not isinstance(mark, dict):
            problems.append(finding("tombstone", where, "not an object", "fix the file"))
            continue
        lane, entry = mark.get("lane"), mark.get("entry")
        label = f"{where} {lane or '?'}:{entry or '?'}"
        if lane not in LANES or not entry:
            problems.append(finding(
                "tombstone", label,
                f"lane must be one of {', '.join(LANES)} and entry must be named",
                "fix the mark"))
            continue
        if not str(mark.get("reason", "")).strip() or not str(mark.get("reopen_when", "")).strip():
            problems.append(finding(
                "tombstone", label,
                "a tombstone without both a reason and a reopen condition is a "
                "mute button; the point of the mark is that someone can tell "
                "later whether it still holds",
                "add `reason` and `reopen_when`"))
            continue
        if not entry_exists(repo, lane, entry):
            problems.append(finding(
                "tombstone", label,
                "the tombstone names something that is not in the tree; a "
                "completed lifecycle should leave nothing behind pointing at it",
                "delete the mark"))
            continue
        if (lane, entry) not in keyed:
            problems.append(finding(
                "tombstone", label,
                "this entry is reachable now, so the exemption has outlived its "
                "cause and would silently absorb the next time it comes unwired",
                "delete the mark — its reopen condition has been met"))
            continue
        excused.add((lane, entry))
    return [f for f in raw if (f["lane"], f["entry"]) not in excused], problems


# --------------------------------------------------------------------- driver
def collect(repo):
    files = tree_files(repo)
    callers = eligible_callers(repo, files)
    registered = registered_hooks(repo)
    raw = (hook_findings(repo) + launchd_findings(repo)
           + installer_findings(repo, callers) + registry_findings(repo, registered))
    marks, broken = load_tombstones(repo)
    surviving, problems = apply_tombstones(repo, raw, marks)
    if broken:
        problems.append(finding("tombstone", TOMBSTONES, broken, "fix the JSON"))
    return surviving + problems, len(marks)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(os.path.join(repo, "ops")):
        print(f"reachability-check: {repo} is not a CARR checkout (no ops/)", file=sys.stderr)
        return 2

    found, marks = collect(repo)

    if args.json:
        print(json.dumps({"findings": found, "tombstones": marks}, indent=2))
        return 1 if found else 0

    if not found:
        # THE EXEMPTIONS ARE PRINTED ON EVERY GREEN RUN, deliberately. A skipped
        # check that announces its own reduced coverage is the convention ci.sh's
        # gates class already follows, and it is the difference between a mark
        # and a mute button: an exemption nobody ever sees again is how the next
        # graveyard starts.
        print(f"reachability-check: every declared control is reachable or marked")
        marks_list, _ = load_tombstones(repo)
        if marks_list:
            print(f"\n  {len(marks_list)} tombstone(s) in force — declared "
                  f"controls deliberately not wired. Reasons in {TOMBSTONES}:")
            for m in marks_list:
                print(f"    {m.get('lane'):<9} {m.get('entry')}")
                print(f"              reopens when: {m.get('reopen_when')}")
        return 0

    print(f"reachability-check: {len(found)} declared control(s) reached from no "
          f"live execution path\n")
    for lane in ("hook", "launchd", "installer", "registry", "tombstone"):
        rows = [f for f in found if f["lane"] == lane]
        if not rows:
            continue
        print(f"  {lane}")
        for f in rows:
            print(f"    ⚠︎ {f['entry']}")
            print(f"        {f['why']}")
            print(f"        remedy: {f['remedy']}")
        print()
    print(f"  {marks} tombstone(s) in force. A tombstone needs a reason and a "
          f"reopen condition,\n  and expires by itself the moment its entry "
          f"becomes reachable.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
