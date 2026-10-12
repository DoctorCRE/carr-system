#!/usr/bin/env python3
"""mechanism-doctrine-gate.py — a new mechanism ships with its doctrine, or it
does not ship.

THE RULING (open loop 504, the flaw-fix package; Joe adopted it through the
2026-08-21 tune-up council): "Knowledge ships with the mechanism: any new
mechanism lands with its doctrine section in the same change, as a compiled
admission check rather than prose."

The last four words are the whole design. There has been a written expectation
that a new gate, hook or scheduled job arrives with an explanation of what it is
for, and it has been honoured about as well as written expectations usually are
— which is why the repository has accumulated mechanisms nobody can account for.
Two of them were cleared the same day this file was written: two Program 1
environment gates that ran nowhere at all, and a launchd job loaded on Joe's Mac
with no plist behind it. In each case the mechanism existed and the knowledge of
why did not, and in each case somebody spent an afternoon reconstructing it.

WHAT COUNTS AS A NEW MECHANISM, deliberately narrow. Only files ADDED by the
change under review, and only these four kinds:

    ops/*-gate.py            a check that can refuse work
    hooks/*.py               a gate that runs inside the session
    ops/launchd/*.plist      a job that runs on its own schedule
    ops/scheduled-tasks/*    a job that asks an AI client to act later

These four share the property that makes the rule worth enforcing: each one
changes what the system does WITHOUT anyone asking it to, so a reader who meets
it later cannot work out its purpose by looking at who called it. A library, a
test fixture or an ordinary script is not covered — those are read by the code
that calls them, and the call site is the explanation.

MODIFYING an existing mechanism is not covered either. The rule is about
knowledge arriving WITH a mechanism, not about re-justifying every edit, and a
gate that demanded a doctrine slug on every touch would be uninstalled within a
week.

WHAT IT REQUIRES: a declaration line naming the doctrine section that explains
the mechanism.

    # doctrine: <slug>

for Python, and `doctrine: <slug>` inside a comment or key for the other two.
The slug is the document's own name in the doctrine store — the thing
`read-doctrine` takes — never a file path, because the generated markdown
renders were retired on 2026-08-19 and a path is a pointer to something that no
longer exists.

WHY A DECLARATION RATHER THAN A DIFF CHECK. The obvious implementation is to
demand that the same commit also touch a doctrine file. It cannot be done: since
the cutoff, doctrine lives in the store and not in this repository, so there is
no file in any diff to look for. The declaration is the part that CAN be
compiled, and it is the part that carries the knowledge forward — a slug in the
file survives every future reader, where a commit message is read once.

WHEN THE STORE IS REACHABLE the slug is also RESOLVED, so a declaration naming
a document that does not exist fails the same as no declaration at all. That is
the half that stops this becoming a box-ticking exercise. When the store is not
reachable — CI runners have no credential by construction — the resolution is
skipped and SAID to be skipped, and the static check still stands.

Exit 0 clean · 1 a new mechanism has no usable doctrine declaration · 2 could
not determine the change under review, which is not a pass.

  ops/mechanism-doctrine-gate.py                  # against origin/main
  ops/mechanism-doctrine-gate.py --base <ref>
  ops/mechanism-doctrine-gate.py --explain        # what it would check, and why
"""
# doctrine: mechanism-doctrine-admission

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A HOOKS FILE IS A MECHANISM ONLY IF IT CAN ACT, not because of where it lives.
# This gate first classified every hooks/*.py as a session gate, which is where
# the gates are but not what makes one. It caught hooks/turn_origin.py on
# 2026-08-23 — a shared detector two gates import, which reads no payload,
# refuses nothing and runs on no schedule. The gate's own --explain text already
# said a library is not covered; the predicate did not agree with it.
#
# The honest discriminator is behavioural: a gate READS THE HOOK PAYLOAD or has
# an entry point to be invoked through. A library has neither — it is imported.
# Checked across all 50 files in hooks/ when this was written: 43 have an entry
# point and are mechanisms, 7 do not and are the shared modules
# (cmd_text, conduct_patterns, corpus_renders, gate_paths, md_manifest,
# refused_content, turn_origin). No file changed side.
#
# NOT keyed on the underscore-versus-hyphen naming convention those 7 happen to
# share. That convention is real and it would work today, but it is a habit
# rather than a contract, and a gate named with an underscore would then be
# waved through silently — the failure this gate exists to prevent.
_ENTRY_POINT = re.compile(r"sys\.stdin|def main\(|__main__", re.M)


def _has_entry_point(path):
    try:
        return bool(_ENTRY_POINT.search(
            open(os.path.join(REPO, path), encoding="utf-8", errors="replace").read()))
    except OSError:
        # Unreadable is not a licence to skip: treat it as a mechanism so the
        # failure is a refusal to classify rather than a silent pass.
        return True


# The four kinds. Each entry is (human name, predicate on a repo-relative path).
MECHANISM_KINDS = (
    ("a check that can refuse work",
     lambda p: p.startswith("ops/") and p.endswith("-gate.py")),
    ("a gate that runs inside the session",
     lambda p: p.startswith("hooks/") and p.endswith(".py") and _has_entry_point(p)),
    ("a job that runs on its own schedule",
     lambda p: p.startswith("ops/launchd/") and p.endswith(".plist")),
    ("a job that asks an AI client to act later",
     lambda p: p.startswith("ops/scheduled-tasks/")),
)

# `# doctrine: some-slug` in Python; `doctrine: some-slug` anywhere in the
# others, so a plist can carry it in a comment and a task file in frontmatter.
# The trailing group closes an XML comment, because a plist can only carry this
# as `<!-- doctrine: slug -->`. Without it the documented plist form silently did
# not work while the docstring said it did — caught by the fixtures, not by
# reading. Nothing else may follow the slug: trailing prose would let a sentence
# mentioning doctrine pass as a declaration.
DECLARATION = re.compile(
    r"^[#<!/*\-\s]*doctrine:\s*([A-Za-z0-9][A-Za-z0-9._-]{3,})\s*(?:-->|\*/)?\s*$",
    re.M)

# A selftest's own fixtures are not mechanisms, and neither is this file's test.
EXEMPT = re.compile(r"-selftest\.py$|/fixtures/|/test/")


def say(msg=""):
    print(msg)


def classify(path):
    """The human name of the kind this path is, or None."""
    if EXEMPT.search(path):
        return None
    for name, pred in MECHANISM_KINDS:
        if pred(path):
            return name
    return None


def default_base():
    """The merge base of HEAD and origin/main, or origin/main when none resolves.

    Measuring against origin/main directly is wrong for any branch older than
    main's latest commit: a mechanism main DELETED after the branch forked is
    still in the branch's tree, and a plain diff reads it as a file the branch
    ADDED.  On 2026-10-12 one hook retired on main turned six unrelated pull
    requests red this way, each labelled inherited-from-main.  The merge base
    is what the change actually started from.
    """
    try:
        out = subprocess.run(["git", "merge-base", "HEAD", "origin/main"],
                             cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        return "origin/main"
    return out or "origin/main"


def added_files(base):
    """Repo-relative paths ADDED (not modified) between base and the worktree."""
    try:
        out = subprocess.run(
            ["git", "diff", "--diff-filter=A", "--name-only", base, "--"],
            cwd=REPO, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or "").strip() or "git diff failed")
    return [p for p in out.splitlines() if p.strip()]


def declared_slug(path):
    full = os.path.join(REPO, path)
    try:
        with open(full, errors="replace") as fh:
            head = fh.read(8192)      # a declaration belongs at the top or nowhere
    except OSError:
        return None
    m = DECLARATION.search(head)
    return m.group(1) if m else None


def known_slugs():
    """Every doctrine slug the store knows, or None when it cannot be reached.

    None and empty are different answers and must not be conflated: an empty
    index would fail every declaration, which is exactly the wrong behaviour on
    a runner with no credential.
    """
    run = os.path.join(REPO, "run.sh")
    if not os.path.exists(run):
        return None
    try:
        p = subprocess.run([run, "call", "doctrine-index", "{}"],
                           cwd=REPO, capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    m = re.search(r"\{.*\}", p.stdout, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    docs = d.get("documents") or d.get("docs") or []
    slugs = {x.get("slug") for x in docs if isinstance(x, dict) and x.get("slug")}
    return slugs or None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default=None,
                    help="what the change is measured against (default: the merge base of "
                         "HEAD and origin/main, so a file main has since deleted is not read "
                         "as one this change added; origin/main itself when no merge base resolves)")
    ap.add_argument("--explain", action="store_true",
                    help="print what this gate covers and exit")
    args = ap.parse_args()

    if args.explain:
        say("mechanism-doctrine-gate covers files ADDED by a change, of four kinds:")
        for name, _ in MECHANISM_KINDS:
            say(f"    {name}")
        say()
        say("Each must carry a line naming the doctrine section that explains it:")
        say("    # doctrine: <slug>")
        say("The slug is the document's own name in the doctrine store, never a path.")
        say("Modifying an existing mechanism is not covered; only adding a new one.")
        return 0

    try:
        base = args.base or default_base()
        added = added_files(base)
    except RuntimeError as exc:
        say(f"FAIL  mechanism-doctrine — cannot read the change against {args.base or default_base()}: {exc}")
        say("      A gate that cannot see the diff has not passed; it has not run.")
        return 2

    new_mechanisms = [(p, kind) for p in added if (kind := classify(p))]
    if not new_mechanisms:
        say("ok  mechanism-doctrine: this change adds no new gate, hook or scheduled job")
        return 0

    slugs = known_slugs()
    missing, unresolved, good = [], [], []
    for path, kind in new_mechanisms:
        slug = declared_slug(path)
        if not slug:
            missing.append((path, kind))
        elif slugs is not None and slug not in slugs:
            unresolved.append((path, slug))
        else:
            good.append((path, slug))

    for path, slug in good:
        say(f"ok  {path} -> doctrine '{slug}'")

    if not missing and not unresolved:
        note = ("" if slugs is not None else
                "  (store unreachable — slugs were NOT resolved, only required)")
        say(f"ok  mechanism-doctrine: {len(good)} new mechanism(s) carry their doctrine{note}")
        return 0

    say()
    if missing:
        say("FAIL  a new mechanism arrived with no doctrine section naming what it is for:")
        for path, kind in missing:
            say(f"        {path}")
            say(f"          {kind} — add a line near the top:  # doctrine: <slug>")
    if unresolved:
        say("FAIL  a doctrine slug was named that the store does not have:")
        for path, slug in unresolved:
            say(f"        {path} names '{slug}'")
        say("        Write the section first, then name it. A slug pointing at nothing")
        say("        is the prose problem wearing a declaration.")
    say()
    say("  The rule: knowledge ships with the mechanism. Something that changes what")
    say("  the system does without being asked must arrive with the explanation of")
    say("  why, in the same change — or the next reader reconstructs it by hand, the")
    say("  way two gates that ran nowhere and a job with no plist were reconstructed")
    say("  on 2026-08-22.")
    say("  Not a mechanism? ops/mechanism-doctrine-gate.py --explain names what is")
    say("  covered; a library, fixture or ordinary script is not.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
