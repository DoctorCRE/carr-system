#!/usr/bin/env python3
"""sync-enforcement-map.py — keep the enforcement map's rule inventory in step
with the compiled-rules renders, so activating a rule can never again leave the
gates reporting themselves out of force.

WHY THIS EXISTS. Twice in two days (2026-08-12 and 2026-08-13) a rule was
activated, the compiled-rules renders picked it up on the hourly refresh, and
the enforcement map did not. The map's `active_rule_ids` must match the render
order exactly, so each time the parity checker failed, and every session that
booted afterwards was told all enforcing gates were not in force. On 2026-08-13
the covering commit even asserted "re-bless in the same commit" while touching
only one file, so the miss survived review.

`active_rule_ids` is DERIVED DATA: its only correct value is the render order.
Nothing is decided here, which is exactly why no model may be spent on it (Joe's
2026-08-13 council rule on never spending a cognition token on anything already
expressible as a tested predicate). This is that predicate.

WHAT IT DELIBERATELY DOES NOT DO. It never runs a full `gate-integrity.py
--bless`. A full bless re-hashes every gate SCRIPT, so an automated job calling
it would happily bless a tampered gate and destroy the detection the baseline
exists to provide. This script rewrites exactly one baseline entry — the
contract hash of the map file it just derived — and leaves all 20 gate-script
hashes frozen. Tampering with a gate is still detected on the next boot.

SKIP-not-FAIL, per house convention: a missing render or baseline is a SKIP at
exit 0, not a failure that would take the hourly refresh down with it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from types import ModuleType

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# RESOLVED AT CALL TIME, NOT IMPORT TIME, and that is the whole point.
#
# These were `MAP = os.path.join(REPO, ...)` constants until 2026-08-13. The
# selftest redirects this module at a throwaway repo by setting `mod.REPO` after
# importing it, which works for git() below (it reads REPO when it runs) and did
# NOT work for the constants (already frozen to the real checkout). The module
# was split-brained for the whole run: git operations went to the fixture while
# every read and write of the map and the baseline went to the LIVE repo, so a
# test whose docstring promises "never touches the real one" silently modified
# ops/config/rule-enforcement-map.json and ops/config/gate-baseline.json on
# every run. A module whose paths freeze at import cannot be pointed at a
# fixture at all — functions can.
def map_path() -> str:
    return os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")


def baseline_path() -> str:
    return os.path.join(REPO, "ops", "config", "gate-baseline.json")

# The map keys its inventory by scope; each scope is rendered to its own file.
RENDERS = {
    "shared": os.path.join("DNA", "compiled-rules-shared.md"),
    "joe": os.path.join("00_Context", "compiled-rules-joe.md"),
}

ID_RE = re.compile(r"^`#?([0-9a-f]{8})`|^#### .*`#([0-9a-f]{8})`", re.M)

# The two files this script is allowed to touch, and the ONLY two it may ever
# commit. Named explicitly because the house rule is to name every path and
# never `git add -A` in a tree that regularly holds another session's work.
OWNED = ["ops/config/rule-enforcement-map.json", "ops/config/gate-baseline.json"]

# The branch this job publishes on. STABLE rather than timestamped: an hourly job
# that minted a new branch name every run would leave a litter of them and a new
# pull request beside each. One branch, force-updated, one pull request reused.
GATES_BRANCH = "gates/enforcement-sync"

# 2026-08-14, rule-enforceability audit (rule ab814a26: "a rule ships with its
# enforcement decided at creation"). Before this, a newly activated rule that
# this job picked up got an active_rule_ids entry and NOTHING ELSE — no
# rule_controls entry at all, which ops/rule-enforcement-map-check.py now
# refuses outright rather than silently defaulting. This job must never leave a
# rule it just added in that state, so every id it adds also gets a placeholder
# entry, honestly marked `unbuilt` rather than guessed at. Classifying a rule
# (deny_gate vs judgment_ambient vs whatever it turns out to be) is a judgment
# call this mechanical sync must not make on a human's behalf — see rule
# 5e89c211, never spend a cognition token on a decision a predicate can make,
# and the inverse of it: never let a predicate MAKE a decision that needed one.
def pending_planned_control(today: date | None = None) -> str:
    """A placeholder whose grace window starts when this sync actually mints it."""
    minted = (today or date.today()).isoformat()
    return (f"pending classification — minted {minted}; "
            "see rule-enforceability audit 2026-08-14")


sys.path.insert(0, os.path.join(REPO, "ops"))
sys.path.insert(0, REPO)
from git_env import scrubbed_env  # noqa: E402
from lib.loadpy import load_module_from_path  # noqa: E402


def git(*args, check=False):
    """Run git in the repo and return (returncode, combined output).

    THE ENVIRONMENT IS SCRUBBED, and that is load-bearing rather than tidy.
    `cwd=REPO` does not pin git to REPO: GIT_DIR outranks the working directory,
    and every git hook exports it. This script runs on the hourly rules refresh
    and COMMITS AND PUSHES two gate files, so inheriting a stray GIT_DIR means
    committing the gate pair into whatever repository invoked us. Observed
    2026-08-13 through the selftest, which drove this function under a hook-like
    GIT_DIR and watched the commit land in the outer repo, sweeping an unrelated
    file with it — the exact two-writer accident rule 308ef1de exists to stop.
    """
    p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                       env=scrubbed_env())
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    return p.returncode, (p.stdout + p.stderr).strip()


def git_in(worktree: str, *args, extra_env: dict | None = None):
    """git(), but rooted in the throwaway publish worktree.

    A separate function rather than a cwd argument on git(), because the two have
    genuinely different safety stories and blurring them is how one of them loses
    its guard. git() is pinned to REPO and must never drift off it. This one is
    pinned to a directory THIS PROCESS just created, does the committing, and is
    the only place a commit is allowed to happen at all. Both scrub the
    environment for the same reason: an inherited GIT_DIR outranks cwd, so
    without scrubbing this would commit into whatever repository invoked the
    hourly refresh.
    """
    env = scrubbed_env()
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(["git", *args], cwd=worktree, capture_output=True,
                       text=True, env=env)
    return p.returncode, (p.stdout + p.stderr).strip()


def read_owned(path: str) -> str | None:
    """The current bytes of one owned path, or None if it cannot be read.

    None is returned rather than raising so a vanished or unreadable file can
    never take the hourly refresh down; it simply compares unequal to whatever
    this run derives, which lands it in the conservative branch — treated as
    another writer's and left alone.
    """
    try:
        with open(os.path.join(REPO, path), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def dirty_owned() -> list[str]:
    """Which of OWNED already have uncommitted changes, before this run writes.

    Asks git once per path and returns OUR canonical path strings rather than
    slicing them out of porcelain output. The first version sliced `line[3:]`,
    which is correct for a plain " M path" line and wrong for other status
    shapes — the selftest caught it returning "ps/config/gate-baseline.json",
    an off-by-one that would have made the guard compare a mangled name and
    silently decide nobody owned the file. Comparing against a path we already
    hold removes the parsing step that could be wrong at all.
    """
    out = []
    for path in OWNED:
        rc, res = git("status", "--porcelain", "--", path)
        if rc == 0 and res.strip():
            out.append(path)
    return out


def publish_via_branch(summary: str, preexisting: list[str]) -> None:
    """Commit the synced pair, or say loudly why it was not committed.

    THIS IS THE HALF THAT WAS MISSING. The first version of this script derived
    the map correctly, re-stamped the baseline correctly, printed "COMMIT NEEDED"
    and stopped — into a log nobody reads. So on 2026-08-13 the sync ran at
    08:39, did its job perfectly, and left both config files sitting uncommitted;
    a session found them by accident hours later. An unpushed gate change is the
    same outage in slow motion: the local machine looks healthy while the repo
    and Dell's clone never receive it. Committing is not decoration on this job,
    it is the job (Joe, 2026-08-11: "you need to start committing your work").
    """
    if preexisting:
        # Another writer was already mid-edit on these exact files. Committing
        # would sweep their work into our commit, which the two-writer rule
        # forbids outright. Leave it and be loud.
        print("sync-enforcement-map: NOT COMMITTED — these were already modified "
              "before this run, so another writer owns them: "
              + ", ".join(preexisting))
        print("sync-enforcement-map: FAIL a human must commit the pair")
        return

    # ── PUBLISHED THROUGH A BRANCH, NEVER COMMITTED ON main ──────────────────
    #
    # CHANGED 2026-08-14, and the old behaviour is why. This used to
    # `git commit` the pair on whatever branch the canonical tree was on — which
    # is always main — and then `git push origin HEAD`. main is protected, so the
    # push was refused every time and the commit stayed local. Not a theory;
    # out/rules-refresh.log carries it verbatim:
    #
    #   sync-enforcement-map: committed the pair
    #   sync-enforcement-map: FAIL push refused, commit is local only — ... on main
    #
    # One stranded commit per run, hourly, unattended. That is the machine half of
    # how ~/carr-system reached nine unpushed commits by 2026-08-14, and it made
    # this job the largest single producer of the divergence it was meant to end.
    # ops/githooks/pre-commit now refuses a commit on main outright, so the old
    # path would not merely strand — it would fail.
    #
    # The pair is therefore built in a THROWAWAY WORKTREE on a stable branch cut
    # from origin/main, pushed there, and offered as a pull request. The canonical
    # tree is never committed to and is left exactly as clean as it was found,
    # which also keeps the next run's dirty_owned() check honest: leaving the
    # files modified here would make the following hour read them as another
    # writer's work and refuse for ever.
    rc, _ = git("fetch", "origin", "main")
    if rc != 0:
        print("sync-enforcement-map: FAIL could not fetch origin/main — nothing published")
        return

    # NOTHING TO PUBLISH is the common case once a PR has merged and this tree
    # has pulled. Checked against origin/main rather than local HEAD, because
    # local HEAD on a busy machine is not what the repository actually holds.
    rc, _ = git("diff", "--quiet", "origin/main", "--", *OWNED)
    if rc == 0:
        # Already upstream — the pull request from an earlier run has merged. The
        # working copies are LEFT AS THEY ARE and deliberately not restored to
        # HEAD: on a busy machine HEAD is routinely behind origin/main, so
        # restoring would overwrite correct content with a stale copy and put
        # this machine straight back out of parity.
        print("sync-enforcement-map: pair already matches origin/main — nothing to publish")
        return

    tmp = tempfile.mkdtemp(prefix="carr-gates-sync-")
    wt = os.path.join(tmp, "wt")
    try:
        # -B resets the branch to origin/main every run, so a stale attempt from
        # a previous hour is replaced rather than built upon. The branch name is
        # STABLE on purpose: repeated runs update one branch and reuse one pull
        # request instead of littering a new one every hour.
        rc, out = git("worktree", "add", "--quiet", "-B", GATES_BRANCH, wt, "origin/main")
        if rc != 0:
            print(f"sync-enforcement-map: FAIL could not create the publish worktree — {out}")
            return

        for path in OWNED:
            shutil.copyfile(os.path.join(REPO, path), os.path.join(wt, path))

        rc, out = git_in(wt, "add", *OWNED)
        if rc != 0:
            print(f"sync-enforcement-map: FAIL could not stage the pair — {out}")
            return
        rc, out = git_in(wt, "diff", "--cached", "--quiet")
        if rc == 0:
            print("sync-enforcement-map: the branch already carries this pair — nothing new")
            return

        rc, out = git_in(wt, "commit", "-m",
                         f"gates: sync enforcement map inventory ({summary}) and "
                         "re-stamp its baseline hash\n\n"
                         "Automated by bin/sync-enforcement-map.py on the hourly rules "
                         "refresh. Both paths land in ONE commit because a map change "
                         "without its baseline is what took the gates down on "
                         "2026-08-12.\n\n"
                         "Published on a branch rather than committed on main: main is "
                         "protected, so the old direct commit could never be pushed and "
                         "stranded on the canonical checkout once an hour.")
        if rc != 0:
            print(f"sync-enforcement-map: FAIL commit refused — {out}")
            return

        # CARR_SKIP_CI because the remote required check is the real gate and this
        # runs unattended once an hour; a full local CI pass per run would put
        # minutes of work on a schedule to re-prove what the PR proves anyway.
        rc, out = git_in(wt, "push", "--force", "origin",
                         f"HEAD:refs/heads/{GATES_BRANCH}",
                         extra_env={"CARR_SKIP_CI": "1"})
        if rc != 0:
            print(f"sync-enforcement-map: FAIL push refused — {out}")
            return
        print(f"sync-enforcement-map: pushed the pair to {GATES_BRANCH}")
        open_or_note_pr(summary)
    finally:
        # ONLY the throwaway worktree is cleaned up. The derived pair STAYS in the
        # canonical tree — see below.
        git("worktree", "remove", "--force", wt)
        shutil.rmtree(tmp, ignore_errors=True)


# THE PAIR IS LEFT IN PLACE, and restoring it was a real outage in miniature.
#
# The first version of this branch-publishing path restored the working copies to
# HEAD after pushing, so the canonical tree stayed clean. That looked tidy and was
# wrong: the map went straight back to its stale content, the parity checker kept
# failing, and hooks/gate-integrity.py therefore told EVERY session at boot that
# the enforcement layer had changed and the gates must not be treated as in force.
# Observed within minutes of shipping it, 2026-08-14.
#
# The pair is derived data whose whole purpose is to be correct ON THIS MACHINE.
# It does not need to be committed here to be right, it needs to be PRESENT. So
# the derived content stays, this machine is in parity immediately, and the commit
# travels separately through the branch and its pull request.
#
# The cost is that the canonical tree carries two modified files until that pull
# request merges and the tree pulls — minutes, not for ever, and self-clearing:
# once origin/main holds the same content the run above returns early without
# touching anything. dirty_owned() alone could not tell that leftover apart from
# another writer's edit, which is why main() now compares CONTENT rather than
# just asking which paths are dirty.


def open_or_note_pr(summary: str) -> None:
    """Offer the branch as a pull request, or say plainly that it is waiting.

    gh may not be authenticated in a launchd environment, and that is not a
    failure of the sync — the pair is already safe on the branch. Saying so
    beats a traceback in a log nobody reads.
    """
    if shutil.which("gh") is None:
        print(f"sync-enforcement-map: branch pushed, no gh on PATH — land it with "
              f"`gh pr create --base main --head {GATES_BRANCH}`")
        return
    p = subprocess.run(
        ["gh", "pr", "create", "--base", "main", "--head", GATES_BRANCH,
         "--title", f"gates: sync enforcement map inventory ({summary})",
         "--body",
         "Automated by `bin/sync-enforcement-map.py` on the hourly rules refresh.\n\n"
         "The enforcement map and its gate baseline move together — a map change "
         "without its baseline is what took the gates down on 2026-08-12.\n\n"
         "Published on a branch because `main` is protected: the previous version "
         "of this job committed directly on `main`, could never push, and stranded "
         "one commit per run on the canonical checkout."],
        cwd=REPO, capture_output=True, text=True, env=scrubbed_env())
    out = (p.stdout + p.stderr).strip()
    if p.returncode == 0:
        print(f"sync-enforcement-map: opened {out.splitlines()[-1] if out else 'a pull request'}")
    elif "already exists" in out:
        print("sync-enforcement-map: the open pull request for this branch was updated")
    else:
        print(f"sync-enforcement-map: branch pushed, PR not opened — {out}")
        print(f"sync-enforcement-map: land it with "
              f"`gh pr create --base main --head {GATES_BRANCH}`")


def find_vault() -> tuple[str | None, ModuleType]:
    """Locate the Drive vault the renders live in, and hand back the checker
    module that found it (main() needs its `ids` parser too).

    Mirrors ops/rule-enforcement-map-check.py rather than inventing a second
    search order — a manual path and an automated path that do the same job
    must be the same code, and two different vault searches would drift.

    The return annotation said `str | None` while the body returned a pair. Both
    callers already unpacked two values, so nothing was broken at runtime, but it
    is what the type-check tripwire was reporting on 2026-08-14 and it made the
    signature a lie about the only thing a signature is for.
    """
    sys.path.insert(0, os.path.join(REPO, "ops"))
    mod = load_module_from_path(
        "map_check", os.path.join(REPO, "ops", "rule-enforcement-map-check.py"))
    return mod.find_vault(), mod


def pending_entry_line(rule_id: str, default_category: str) -> str:
    """One compact JSON line for a newly-added rule with no classification yet.

    Matches the map file's existing one-object-per-line style for rule_controls
    entries. `ensure_ascii=False` keeps the em dash literal instead of escaping
    it to `\\u2014`, matching the plain readable strings already in this file.
    """
    obj = {
        "category": default_category,
        "enforcement_class": "unbuilt",
        "planned_control": pending_planned_control(),
    }
    return f'    "{rule_id}": {json.dumps(obj, ensure_ascii=False)}'


def add_pending_rule_controls(text: str, new_ids: list[str], default_category: str) -> str | None:
    """Append a placeholder rule_controls entry for every id in `new_ids` that
    the file does not already classify. Returns the rewritten text, or None if
    the rule_controls block could not be located (the caller SKIPs on None,
    same convention as every other block lookup in this file).

    Appends rather than reformatting the whole block for the same reason the
    active_rule_ids rewrite above is a targeted splice and not a json.dump: a
    full re-render would touch every existing hand-authored line and bury the
    one real change in a diff nobody could review.
    """
    if not new_ids:
        return text
    m = re.search(r'("rule_controls": \{\n)(?P<body>.*?)(\n( *)\},\n *"active_rule_ids")',
                 text, re.S)
    if not m:
        return None
    body = m.group("body").rstrip()
    if not body.endswith(","):
        body += ","
    new_lines = [pending_entry_line(rid, default_category) for rid in new_ids]
    new_body = body + "\n" + ",\n".join(new_lines)
    return text[: m.start("body")] + new_body + text[m.end("body") :]


def end_of_object(text: str, i: int) -> int:
    """Index just past the `{...}` that begins at text[i], strings respected.

    Written as a scan rather than a regex because a rule_controls entry is a
    JSON object on one line in the real map and pretty-printed over several in
    the selftest fixture; a line-shaped pattern would quietly handle only one
    of those and pass its own test while leaving the live file broken.
    """
    depth = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unterminated object in the enforcement map")


def drop_retired_entries(text: str, gone: list[str]) -> str | None:
    """Erase every trace of a retired rule from the map's other two homes.

    WHY THIS EXISTS, and why the script was half-finished without it. Activating
    a rule and retiring one are not mirror images. An activation only has to add
    to `active_rule_ids` (the inventory splice above) and mint a placeholder in
    `rule_controls`. A retirement has to REMOVE from three places, because the
    map also holds a `rule_controls` entry per rule and, for anything not
    advisory, a `category_overrides` list naming it. main() computed `dropped`
    and threw it away, so those two stragglers survived every retirement.

    Measured, not theorised: rule a225b744 was retired on 2026-08-14, the render
    dropped it, the sync dropped it from the inventory, and the checker then
    failed with "override references unknown rule a225b744" plus "entries
    reference inactive/unknown rule(s): a225b744". hooks/gate-integrity.py reads
    that failure as the enforcement layer having changed, so every session that
    booted afterwards was told not to treat any gate as in force — the same
    outage this script exists to prevent, arriving through the one direction it
    never handled.

    Returns the rewritten text, or None if any id is not a plain rule id (the
    caller SKIPs on None rather than running a substitution it cannot bound).
    """
    if not gone:
        return text
    for rule_id in gone:
        if not re.fullmatch(r"[0-9a-f]{8}", rule_id):
            return None
        quoted = re.escape(f'"{rule_id}"')

        # 1. category_overrides — a string element in a list. Three shapes, and
        #    the sole-element one has to become `[]` rather than a dangling
        #    comma. Safe to run over the whole file: the inventory splice has
        #    already removed this id from active_rule_ids, and its rule_controls
        #    key is followed by a colon, so neither can match these patterns.
        text = re.sub(quoted + r",\s*", "", text)
        text = re.sub(r",\s*" + quoted + r"(?=\s*\])", "", text)
        text = re.sub(r"\[\s*" + quoted + r"\s*\]", "[]", text)

        # 2. rule_controls — an object member. Cut the whole member plus one
        #    separating comma, taking the PRECEDING comma when it was last so
        #    the block does not end on a dangling one.
        match = re.search(r"\n[ \t]*" + quoted + r":[ \t]*\{", text)
        if not match:
            continue
        end = end_of_object(text, text.index("{", match.end() - 1))
        tail = text[end:]
        if tail.lstrip().startswith(","):
            cut_start, cut_end = match.start(), end + tail.index(",") + 1
        else:
            previous_comma = text.rfind(",", 0, match.start())
            cut_start = previous_comma if previous_comma != -1 else match.start()
            cut_end = end
        text = text[:cut_start] + text[cut_end:]
    return text


def main() -> int:
    if not (os.path.exists(map_path()) and os.path.exists(baseline_path())):
        print("sync-enforcement-map: SKIP map or baseline missing")
        return 0

    try:
        vault, checker = find_vault()
    except Exception as exc:
        print(f"sync-enforcement-map: SKIP cannot locate vault ({exc})")
        return 0

    if not vault:
        print("sync-enforcement-map: SKIP vault not reachable")
        return 0

    # Captured BEFORE anything is written: once this script edits the pair they
    # are dirty by definition, so the only moment we can tell our own change
    # apart from another writer's is right now.
    #
    # CONTENT, NOT JUST THE PATH LIST, since 2026-08-14. The pair is now left in
    # place after publishing rather than restored (see publish_via_branch), so on
    # the next run the previous run's OWN output is sitting there dirty. A guard
    # that reads any dirty owned file as another writer's work would then refuse
    # for ever, and the map would never sync again — which is worse than the
    # divergence it was protecting against, because a stale map makes every
    # session boot believing the gates are not in force.
    #
    # The discriminator is what the file SAYS. Held here and compared against
    # what this run derives; if they match, that dirt is ours.
    before = {p: read_owned(p) for p in dirty_owned()}

    data = json.load(open(map_path()))
    inventory = data.get("active_rule_ids") or {}

    changed = []
    for scope, rel in RENDERS.items():
        path = os.path.join(vault, rel)
        if not os.path.exists(path):
            print(f"sync-enforcement-map: SKIP {scope} render missing")
            return 0
        rendered = checker.ids(path)
        if not rendered:
            # An empty parse means the render format moved, not that every rule
            # was retired. Refuse to write an empty inventory over a good one.
            print(f"sync-enforcement-map: SKIP {scope} render parsed 0 ids")
            return 0
        if inventory.get(scope) != rendered:
            added = [r for r in rendered if r not in (inventory.get(scope) or [])]
            dropped = [r for r in (inventory.get(scope) or []) if r not in rendered]
            changed.append((scope, added, dropped))
            inventory[scope] = rendered

    # STRANDED IDS ARE COMPUTED FROM THE FILE, NOT FROM THIS RUN'S DIFF, and that
    # distinction is not academic — it is the difference between fixing the live
    # map and watching it stay broken. A first version of the prune below keyed
    # off `dropped`, so it only fired on the one run that happened to notice the
    # retirement. By then the unfixed job had ALREADY synced the inventory (it
    # landed on main as commit c666629), so every later run saw the inventory in
    # parity, took the early return here, and left the two stragglers wedged in
    # the file for good. A repair that only works if it runs before the damage is
    # not a repair. The honest question is the state one: which ids does the map
    # still classify that are not active anywhere?
    active_everywhere = {rid for ids in inventory.values() for rid in ids}
    stranded = [rid for rid in (data.get("rule_controls") or {})
                if rid not in active_everywhere]
    for _category, rule_ids in (data.get("category_overrides") or {}).items():
        for rid in rule_ids if isinstance(rule_ids, list) else []:
            if rid not in active_everywhere and rid not in stranded:
                stranded.append(rid)

    if not changed and not stranded:
        print("sync-enforcement-map: OK already in parity")
        return 0

    # Rewrite the inventory IN PLACE, preserving the file's one-line-per-scope
    # array style. A json.dump would reformat every array in the file and bury
    # a one-rule change in hundreds of lines of noise, which makes the diff
    # unreviewable — and an unreviewable gate diff is how this bug shipped.
    text = open(map_path()).read()
    for scope, _added, _dropped in changed:
        rendered = inventory[scope]
        block = re.compile(
            r'(^(?P<indent> *)"' + re.escape(scope) + r'": \[\n)(?P<body>.*?)(\n *\])',
            re.M | re.S,
        )
        m = block.search(text)
        if not m:
            print(f"sync-enforcement-map: SKIP cannot locate {scope} array in map")
            return 0
        line = m.group("indent") + "  " + ", ".join(f'"{r}"' for r in rendered)
        text = text[: m.start("body")] + line + text[m.end("body") :]

    # Every id this run just added needs a rule_controls entry too, or the
    # check now fails it outright as "no enforcement-map entry at all" — the
    # exact silent-default gap the 2026-08-14 audit found. Ids already
    # classified (a human beat this run to it) are left untouched.
    existing_controls = set(data.get("rule_controls") or {})
    newly_added = []
    for _scope, added, _dropped in changed:
        for rid in added:
            if rid not in existing_controls and rid not in newly_added:
                newly_added.append(rid)
    if newly_added:
        patched = add_pending_rule_controls(text, newly_added, data.get("default_category", "judgment_advisory"))
        if patched is None:
            print("sync-enforcement-map: SKIP cannot locate rule_controls block in map")
            return 0
        text = patched

    # Every id the map still classifies but no render still carries needs its
    # rule_controls entry and its category override removed, or the check fails
    # it as "inactive/unknown". `stranded` was derived above from the file's own
    # state, so this covers both the retirement happening right now and any left
    # behind by an earlier run of the version that could not do this.
    #
    # An id that merely MOVED SCOPE (shared to joe, or back) is dropped from one
    # inventory and added to another and survives untouched: `active_everywhere`
    # is the union, so the test is whether it is active ANYWHERE, never whether
    # some particular scope stopped naming it.
    newly_retired = sorted(stranded)
    if newly_retired:
        pruned = drop_retired_entries(text, newly_retired)
        if pruned is None:
            print("sync-enforcement-map: SKIP retired id is not a plain rule id")
            return 0
        text = pruned

    with open(map_path(), "w") as fh:
        fh.write(text)

    # Sanity: the file must still parse and still carry what we intended.
    reread = json.load(open(map_path()))
    for scope, _a, _d in changed:
        if reread["active_rule_ids"][scope] != inventory[scope]:
            print(f"sync-enforcement-map: FAIL {scope} did not land; leaving as-is")
            return 0
    for rid in newly_added:
        entry = reread.get("rule_controls", {}).get(rid)
        if not isinstance(entry, dict) or entry.get("enforcement_class") != "unbuilt":
            print(f"sync-enforcement-map: FAIL pending entry for {rid} did not land; leaving as-is")
            return 0
    # The prune is verified against the re-read file for the same reason every
    # other write here is: an `ok` from a substitution says the pattern ran, not
    # that the value left the file (rule c53beeaa).
    for rid in newly_retired:
        if rid in (reread.get("rule_controls") or {}):
            print(f"sync-enforcement-map: FAIL {rid} still has a rule_controls entry; leaving as-is")
            return 0
        for category, ids in (reread.get("category_overrides") or {}).items():
            if rid in ids:
                print(f"sync-enforcement-map: FAIL {rid} still named by {category}; leaving as-is")
                return 0

    # Re-stamp ONLY this file's contract hash, by targeted replacement so the
    # rest of the baseline is byte-identical. Gate-SCRIPT hashes stay frozen:
    # an automated full bless would launder a tampered gate into the baseline.
    new_hash = hashlib.sha256(open(map_path(), "rb").read()).hexdigest()
    btext = open(baseline_path()).read()
    old_hash = json.load(open(baseline_path())).get("contracts", {}).get(
        "rule-enforcement-map.json"
    )
    if not old_hash or old_hash not in btext:
        print("sync-enforcement-map: SKIP no map contract hash in baseline")
        return 0
    with open(baseline_path(), "w") as fh:
        fh.write(btext.replace(old_hash, new_hash, 1))

    summary_bits = []
    for scope, added, dropped in changed:
        bits = []
        if added:
            bits.append(f"+{','.join(added)}")
        if dropped:
            bits.append(f"-{','.join(dropped)}")
        print(f"sync-enforcement-map: SYNCED {scope} {' '.join(bits)}")
        summary_bits.append(f"{scope} {' '.join(bits)}")
    if newly_added:
        print("sync-enforcement-map: labeled unbuilt/pending: " + ", ".join(newly_added))
    if newly_retired:
        print("sync-enforcement-map: pruned controls + overrides for retired: "
              + ", ".join(newly_retired))
        # A prune-only run has no `changed` scope to describe, and an empty
        # summary would reach the commit message as a blank line — the shape
        # rule 24e10ee8 exists to prevent.
        summary_bits.append("pruned retired " + ",".join(newly_retired))
    print("sync-enforcement-map: contract hash re-stamped; gate hashes untouched")

    if "--no-commit" in sys.argv:
        print("sync-enforcement-map: --no-commit given; leaving the pair modified")
        return 0

    # NOW the two-writer question can be answered honestly: a path that was dirty
    # before this run counts as another writer's only if what it held DIFFERS
    # from what this run just derived. Identical content is this job's own
    # previous output, left in place deliberately.
    preexisting = [p for p, was in before.items() if was != read_owned(p)]
    publish_via_branch("; ".join(summary_bits), preexisting)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
