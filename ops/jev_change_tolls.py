"""Verification obligations derived from changed paths and contract dependencies.

No model or result cache: a selected verifier's result is the only reason to
hold a push. A path match never asserts that a seal or a contract has drifted.
"""
import hashlib
import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFY_AT = 0.60
WARN_AT = 0.55
TOLLS = {
    "inventory_reseal": ("sealed contract dependency changed", "run the sealed source inventory verifier; rederive if its contract changed"),
    "new_ingress_admitted": ("new sealed contract source", "verify new sealed rows and derive a registry successor when needed"),
    "gate_rebless": ("baseline-tracked gate changed", "re-bless the changed gate and commit ops/config/gate-baseline.json with it"),
    "paired_selftest": ("gate changed without paired suite", "add regression cases to its paired selftest"),
    "ci_sh_reseal": ("CI runner changed", "run ops/ci-selftest.py"),
    "judgment_without_caller": ("judgment wiring changed", "run ops/judgment-wiring-selftest.py"),
    "derived_file_hand_merged": ("derived artifact merged", "union both intents and rederive against the merged tree"),
    "settings_matcher_change": ("hook registration changed", "verify tool matchers and installed hook paths")}
CONTRACT_PREFIXES = ("mcp-server/src/", "ops/scac-", "ops/config/scheduled-jobs")
SETTINGS_PATHS = {".claude/settings.json", "ops/config/hooks.json", "ops/config/codex-hooks.json",
                  "ops/config/claude-continuity-hooks.json", "ops/config/delegation-gate-hook.json"}
DERIVED_PATHS = {"ops/config/gate-baseline.json", "ops/config/scac-registry-source-inventory-fixtures.v1.json",
                 "ops/config/rule-enforcement-map.json"}


def _contract_sources():
    """Conservative dependency edges from the reviewed inventory's locators.

    Historical locators remain edges: an extra verifier is safe, whereas a
    missing edge can hide a drifted seal. The verifier decides current drift.
    """
    with open(os.path.join(REPO, "ops/config/scac-registry-source-inventory-fixtures.v1.json")) as handle:
        fixture = json.load(handle)
    sources = set()
    def visit(value):
        if isinstance(value, dict):
            locator = value.get("source_locator")
            if isinstance(locator, str):
                sources.add(locator.split("#", 1)[0].split(":", 1)[0])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(fixture)
    return sources

def change(base="origin/main", repo=REPO):
    """The changed-file list, plus the two facts the questions cannot see.

    Measured against origin/<base> rather than HEAD: on a shared checkout a
    local base drifts behind constantly, and diffing against a stale one
    reports every line those commits added as though it were this change.
    """
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, timeout=120)
    # COMMITTED AND UNCOMMITTED BOTH. The first version diffed only
    # origin/main...HEAD, which is right at push time and useless while
    # working: on 2026-09-18 a session edited mcp-server/src/tools.js, asked
    # what the change owed, and was told about two other files -- because the
    # edit it was asking about had not been committed yet. The toll it needed
    # was the expensive one: 100 of the 864 inventory rows name that file as
    # their source, so its bytes moving re-digests every one of them.
    # NOT .strip() BEFORE .splitlines(). Porcelain's status is two COLUMNS,
    # so an unstaged edit is " M path" with a leading space -- and stripping
    # the whole blob eats that space on the FIRST line only, shifting its
    # path by one character. Caught 2026-09-18 when the advisory reported an
    # edit to "cp-server/src/tools.js", a file that does not exist: one row
    # silently mis-parsed while every row after it was fine.
    names = subprocess.run(["git", "diff", "--name-status", f"{base}...HEAD"],
                           capture_output=True, text=True, cwd=repo,
                           timeout=60).stdout.splitlines()
    names += subprocess.run(["git", "status", "--porcelain=v1"],
                            capture_output=True, text=True, cwd=repo,
                            timeout=60).stdout.splitlines()
    # DELETED IS ITS OWN LIST. A removed path used to fall through to
    # "edited", so the advisory described a deletion as an edit to a file
    # that no longer exists, and the collector selftest's "every named path
    # exists" property failed on any branch that deleted a tracked file.
    added, edited, deleted = [], [], []
    for row in names:
        # Two shapes reach here. `git diff --name-status` gives "M\tpath";
        # `git status --porcelain` gives "?? path" or " M path". Untracked and
        # added both count as ADDED, because what decides the toll is whether
        # the file is new to the repository, not how it got there.
        if "\t" in row:
            parts = row.split("\t")
            status, path = parts[0], parts[-1]
        else:
            status, path = row[:2].strip() or "M", row[3:].strip()
        # A RENAME IS A DELETE PLUS AN ADD. Porcelain prints "R  old -> new"
        # (and --name-status "R100\told\tnew", whose last column is already
        # the new path). Taken whole, "old -> new" was named as one path that
        # does not exist, which the collector selftest's every-named-path-
        # exists property caught on the first branch that renamed a migration.
        if status.startswith(("R", "C")):
            if " -> " in path:
                old_path, path = path.split(" -> ", 1)
            elif "\t" in row:
                old_path = row.split("\t")[1]
            else:
                old_path = None
            if status.startswith("R") and old_path and old_path not in deleted:
                deleted.append(old_path)
            status = "A"
        if not path:
            continue
        if status.startswith("D"):
            target = deleted
        else:
            target = added if status.startswith(("A", "??")) else edited
        if path not in target:
            target.append(path)
    # Committed and uncommitted rows are read together, so a file the branch
    # added in an earlier commit and the working tree has since removed (a
    # renumbered migration) arrives as both added and deleted. It is gone:
    # only the deletion is true. The disk settles which way it ended, so a
    # file deleted in a commit and re-created since stays added.
    on_disk = {path for path in added + edited + deleted if os.path.exists(os.path.join(repo, path))}
    added = [path for path in added if path not in deleted or path in on_disk]
    edited = [path for path in edited if path not in deleted or path in on_disk]
    deleted = [path for path in deleted if path not in on_disk]
    # Whether a file is an entrypoint is a fact the questions CANNOT see, so
    # it is gathered here for every touched file rather than inferred from a
    # path. Two mistakes in the first version of this function, both of which
    # made the model answer a question about facts it had not been given:
    # only ADDED files were classified, so an edited entrypoint looked like an
    # ordinary edit and the reseal toll went unflagged on a branch that owed
    # it; and only the first 4096 bytes were read, so a main guard -- which
    # sits at the BOTTOM of every file that has one -- was invisible in any
    # file longer than that.
    def entrypoint(rel):
        try:
            with open(os.path.join(repo, rel), encoding="utf-8",
                      errors="ignore") as fh:
                body = fh.read()
        except OSError:
            return False
        return body.startswith("#!") or '__name__ == "__main__"' in body \
            or "__name__ == '__main__'" in body

    shebangs = [rel for rel in added if entrypoint(rel)]
    edited_entrypoints = [rel for rel in edited if entrypoint(rel)]
    merged = subprocess.run(
        ["git", "log", "--merges", "--oneline", f"{base}..HEAD"],
        capture_output=True, text=True, cwd=repo, timeout=60).stdout.strip()
    contents = {}
    for rel in sorted(set(added + edited)):
        try:
            with open(os.path.join(repo, rel), "rb") as fh:
                contents[rel] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            contents[rel] = None
    return {"files": {"added": added, "edited": edited, "deleted": deleted},
            "file_content_sha256": contents,
            "added_files_with_a_shebang_or_main_guard": shebangs,
            "edited_files_that_are_script_entrypoints": edited_entrypoints,
            "this_branch_merged_another_branch": bool(merged)}


VERIFIERS = {
    "inventory_reseal": (
        ["node", "--input-type=module", "-e",
         "import {assertCurrentSourceInventoryMatchesFixture} from "
         "'./ops/scac-mutation-inventory.mjs'; import {TOOLS} from "
         "'./mcp-server/src/tools.js'; "
         "assertCurrentSourceInventoryMatchesFixture(TOOLS);"],
        "the sealed source inventory does not match this tree"),
    "gate_rebless": (
        [".venv/bin/python", "hooks/gate-integrity.py"],
        "a gate's bytes no longer match ops/config/gate-baseline.json"),
    "judgment_without_caller": (
        [".venv/bin/python", "ops/judgment-wiring-selftest.py"],
        "a module that reaches the model has no caller and is not declared inert"),
    "ci_sh_reseal": (
        [".venv/bin/python", "ops/ci-selftest.py"],
        "ops/ci.sh changed and something that reads it back out disagrees"),
}



def verify(state=None, *, floor=None, client=None, api_key=None, repo=REPO):
    """Run every verifier selected by the path/contract dependency graph.

    Returns [(name, probability, reason, output)] for checks that FAILED. An
    empty list means either nothing was flagged or everything flagged is
    already paid.

    `floor` defaults to VERIFY_AT rather than the advisory's own warning floor:
    a check worth running is a lower bar than a remedy worth printing, so this
    deliberately runs more checks than the advisory prints warnings.
    """
    floor = VERIFY_AT if floor is None else floor
    state = change(repo=repo) if state is None else state
    failures = []
    for probability, name, _fix in owed(state, client=client, api_key=api_key,
                                        floor=floor):
        entry = VERIFIERS.get(name)
        if not entry:
            continue
        argv, reason = entry
        try:
            done = subprocess.run(argv, cwd=repo, capture_output=True,
                                  text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            # A verifier that cannot RUN is not a verifier that failed. Saying
            # otherwise would block a push on a missing interpreter, which is
            # the fail-closed trap this advisory is not allowed to become.
            failures.append((name, probability, f"{reason} (check could not run: {exc})", ""))
            continue
        if done.returncode != 0:
            failures.append((name, probability, reason,
                             ((done.stdout or "") + (done.stderr or ""))[-1200:]))
    return failures



def owed(state=None, *, client=None, api_key=None, floor=WARN_AT):
    state = change() if state is None else state
    files = state.get("files",{})
    if isinstance(files,list):
        files = {"edited":files}
    touched = set(files.get("added",[]) + files.get("edited",[]) + files.get("deleted",[]))
    added = set(files.get("added",[]))
    try:
        with open(os.path.join(REPO,"ops/config/gate-baseline.json")) as fh:
            baseline = json.load(fh)
        tracked = {"hooks/" + name for name in baseline.get("hashes",{})}
    except (OSError,ValueError):
        tracked = set()
    selected = set()
    try:
        sealed_sources = _contract_sources()
    except (OSError, ValueError):
        # A missing dependency graph needs verification; never infer no toll.
        sealed_sources = touched
    if touched & sealed_sources or any(path.startswith(CONTRACT_PREFIXES) for path in touched):
        selected.add("inventory_reseal")
    if any(path.startswith(CONTRACT_PREFIXES) for path in added):
        selected.add("new_ingress_admitted")
    gates = {path for path in touched if path in tracked}
    if gates:
        selected.add("gate_rebless")
    if any("ops/"+os.path.basename(p).removesuffix(".py")+"-selftest.py" not in touched for p in gates):
        selected.add("paired_selftest")
    if "ops/ci.sh" in touched:
        selected.add("ci_sh_reseal")
    if any(path.startswith("ops/jev_") or path in {"ops/typesafe_client.py","ops/judgment-wiring-selftest.py"} for path in touched):
        selected.add("judgment_without_caller")
    if state.get("this_branch_merged_another_branch") and touched & DERIVED_PATHS:
        selected.add("derived_file_hand_merged")
    if touched & SETTINGS_PATHS:
        selected.add("settings_matcher_change")
    return [(1.0,name,TOLLS[name][1]) for name in sorted(selected) if 1.0 >= floor]
