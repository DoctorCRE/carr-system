#!/usr/bin/env python3
"""Pure regressions for rule-enforcement-map-check validation.

EXTENDED 2026-08-14 for the rule-enforceability audit (item #1, rule ab814a26:
"a rule ships with its enforcement decided at creation" was itself unenforced).
The old BASE fixture proved a rule with NO rule_controls entry at all still
VALIDATED — that silent fall-through to default_category is exactly the gap the
audit found live on two rules activated the same day. BASE now covers all three
honest shapes an entry can take (a built gate, a judgment call with its reason
stated, and an acknowledged gap with its remedy named) so "exact coverage"
means what it says: every active rule has SOME real entry, never an implicit
one.
"""
from __future__ import annotations

import contextlib
import copy
import io
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "rule_enforcement_map_check", os.path.join(REPO, "ops", "rule-enforcement-map-check.py")
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Three active rules, one per honest shape:
#   aaaaaaaa — hard_pre_action, backed by a real gate (deny_gate, built)
#   bbbbbbbb — judgment_advisory, a genuine judgment call (judgment_ambient)
#   dddddddd — judgment_advisory, classification simply hasn't happened yet (unbuilt)
BASE = {
    "schema_version": 1,
    "default_category": "judgment_advisory",
    "categories": {key: {} for key in mod.CATEGORIES},
    "control_catalog": {"real": {"implementation": ["hooks/gate-integrity.py"], "test": ["ops/rule-enforcement-map-selftest.py"], "failure_mode": "deny"}},
    # rule_controls sits BEFORE active_rule_ids, matching the real map's key
    # order (ops/config/rule-enforcement-map.json) — bin/sync-enforcement-map.py
    # locates the rule_controls block by searching up to the following
    # "active_rule_ids" key, so a fixture with a different order would not be
    # exercising the real file's shape.
    "rule_controls": {
        "aaaaaaaa": {"category": "hard_pre_action", "enforcement_class": "deny_gate", "binding_moment": "before write", "control": "real", "exceptions": "none"},
        "bbbbbbbb": {"category": "judgment_advisory", "enforcement_class": "judgment_ambient", "why_unenforceable": "requires contextual judgment no script can make"},
        "dddddddd": {"category": "judgment_advisory", "enforcement_class": "unbuilt", "planned_control": "build a PreToolUse gate on the named action"},
    },
    "active_rule_ids": {"shared": ["aaaaaaaa", "dddddddd"], "joe": ["bbbbbbbb"]},
    "category_overrides": {"hard_pre_action": ["aaaaaaaa"]},
}
SOURCE = {"shared": ["aaaaaaaa", "dddddddd"], "joe": ["bbbbbbbb"]}


def case(name, data, passes, source_ids=SOURCE):
    got = mod.validate(data, source_ids)
    ok = (not got) == passes
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got or 'valid'}")
    return ok


def rc(**overrides):
    """A deep copy of BASE's rule_controls with one entry patched or removed.

    `overrides[id] = None` deletes that entry; any other value replaces it.
    """
    controls = copy.deepcopy(BASE["rule_controls"])
    for rid, val in overrides.items():
        if val is None:
            controls.pop(rid, None)
        else:
            controls[rid] = val
    return controls


def case_sync_adds_pending_unbuilt():
    """bin/sync-enforcement-map.py must add a brand-new active rule as
    `unbuilt`/pending — never silently omit it — and the map must still PASS
    the check afterward, with the new rule visibly labeled rather than
    defaulted. Drives the real sync module against a throwaway fixture repo and
    a throwaway fake vault, exactly the way ops/sync-enforcement-map-commit-
    selftest.py drives the commit half; this exercises the OTHER half, the
    rule_controls placeholder the audit asked for.
    """
    name = "sync job adds new rule as pending-unbuilt; check still passes"
    repo = tempfile.mkdtemp(prefix="syncmap-pending-selftest-")
    vault = tempfile.mkdtemp(prefix="syncmap-pending-vault-")
    try:
        os.makedirs(os.path.join(repo, "ops", "config"), exist_ok=True)
        # sync-enforcement-map.py's find_vault() dynamically loads
        # ops/rule-enforcement-map-check.py FROM `REPO` (the fixture, once
        # mod.REPO is redirected below) to get its find_vault()/ids() helpers —
        # it must actually be there for that lookup to succeed.
        import shutil as _shutil
        _shutil.copyfile(
            os.path.join(REPO, "ops", "rule-enforcement-map-check.py"),
            os.path.join(repo, "ops", "rule-enforcement-map-check.py"))
        map_path = os.path.join(repo, "ops", "config", "rule-enforcement-map.json")
        baseline_path = os.path.join(repo, "ops", "config", "gate-baseline.json")

        seed = copy.deepcopy(BASE)
        with open(map_path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, indent=2)
            fh.write("\n")
        import hashlib
        seed_hash = hashlib.sha256(open(map_path, "rb").read()).hexdigest()
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump({"contracts": {"rule-enforcement-map.json": seed_hash}}, fh)

        os.makedirs(os.path.join(vault, "DNA"), exist_ok=True)
        os.makedirs(os.path.join(vault, "00_Context"), exist_ok=True)
        # A brand-new id, "eeeeeeee", appears in the shared render that was not
        # in the map's inventory before this run — the moment a rule gets
        # activated and the hourly refresh picks it up.
        with open(os.path.join(vault, "DNA", "compiled-rules-shared.md"), "w") as fh:
            fh.write("- one `#aaaaaaaa`\n- two `#dddddddd`\n- new `#eeeeeeee`\n")
        with open(os.path.join(vault, "00_Context", "compiled-rules-joe.md"), "w") as fh:
            fh.write("- three `#bbbbbbbb`\n")

        sync_spec = importlib.util.spec_from_file_location(
            "sync_enforcement_map_pending", os.path.join(REPO, "bin", "sync-enforcement-map.py"))
        sync_mod = importlib.util.module_from_spec(sync_spec)
        sync_spec.loader.exec_module(sync_mod)
        sync_mod.REPO = repo

        prior_argv = sys.argv[:]
        prior_vault_env = os.environ.get("CARR_VAULT")
        os.environ["CARR_VAULT"] = vault
        sys.argv = [prior_argv[0], "--no-commit"]
        try:
            rc_code = sync_mod.main()
        finally:
            sys.argv = prior_argv
            if prior_vault_env is None:
                os.environ.pop("CARR_VAULT", None)
            else:
                os.environ["CARR_VAULT"] = prior_vault_env

        if rc_code != 0:
            print(f"FAIL  {name}: sync main() returned {rc_code}")
            return False

        with open(map_path, encoding="utf-8") as fh:
            synced = json.load(fh)

        entry = synced.get("rule_controls", {}).get("eeeeeeee")
        if not isinstance(entry, dict) or entry.get("enforcement_class") != "unbuilt" \
                or not (entry.get("planned_control") or "").strip() \
                or date.today().isoformat() not in entry.get("planned_control", ""):
            print(f"FAIL  {name}: new rule not labeled unbuilt/pending: {entry!r}")
            return False

        source_ids = {"shared": ["aaaaaaaa", "dddddddd", "eeeeeeee"], "joe": ["bbbbbbbb"]}
        errors = mod.validate(synced, source_ids)
        ok = not errors
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {errors or 'valid'}")
        return ok
    finally:
        subprocess.run(["rm", "-rf", repo, vault])


def case_sync_prunes_a_retired_rule():
    """The mirror of the case above, and the half that was missing.

    RETIRING a rule is not the reverse of activating one, because the map holds
    a retired id in TWO more places than the inventory the sync rewrites: its
    `rule_controls` entry and whatever `category_overrides` list named it. The
    sync computed `dropped` and then did nothing with it, so on 2026-08-14 rule
    a225b744 was retired, the render lost it, the inventory lost it, and the two
    stragglers stayed — which the checker correctly rejects as "override
    references unknown rule" plus "entries reference inactive/unknown rule(s)".
    Every session that booted afterwards was told the enforcement layer had
    changed and the gates must not be treated as in force, which is the exact
    outage this whole script was written to end. Adding a rule was tested;
    removing one was not, so only half the loop was ever closed.

    `cccccccc` retires here holding BOTH stragglers — a rule_controls entry and
    a session_task_rail override — so a fix that cleans one and forgets the
    other still fails this case.
    """
    name = "sync job prunes a retired rule from controls AND overrides"
    repo = tempfile.mkdtemp(prefix="syncmap-retire-selftest-")
    vault = tempfile.mkdtemp(prefix="syncmap-retire-vault-")
    try:
        os.makedirs(os.path.join(repo, "ops", "config"), exist_ok=True)
        import shutil as _shutil
        _shutil.copyfile(
            os.path.join(REPO, "ops", "rule-enforcement-map-check.py"),
            os.path.join(repo, "ops", "rule-enforcement-map-check.py"))
        map_path = os.path.join(repo, "ops", "config", "rule-enforcement-map.json")
        baseline_path = os.path.join(repo, "ops", "config", "gate-baseline.json")

        seed = copy.deepcopy(BASE)
        seed["rule_controls"]["cccccccc"] = {
            "category": "session_task_rail", "enforcement_class": "surfacing",
            "binding_moment": "at session start", "control": "real",
            "exceptions": "none"}
        seed["active_rule_ids"]["shared"] = ["aaaaaaaa", "cccccccc", "dddddddd"]
        seed["category_overrides"]["session_task_rail"] = ["cccccccc"]
        with open(map_path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, indent=2)
            fh.write("\n")
        import hashlib
        seed_hash = hashlib.sha256(open(map_path, "rb").read()).hexdigest()
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump({"contracts": {"rule-enforcement-map.json": seed_hash}}, fh)

        os.makedirs(os.path.join(vault, "DNA"), exist_ok=True)
        os.makedirs(os.path.join(vault, "00_Context"), exist_ok=True)
        # cccccccc is GONE from the render — the moment a rule gets retired and
        # the hourly refresh re-renders without it.
        with open(os.path.join(vault, "DNA", "compiled-rules-shared.md"), "w") as fh:
            fh.write("- one `#aaaaaaaa`\n- two `#dddddddd`\n")
        with open(os.path.join(vault, "00_Context", "compiled-rules-joe.md"), "w") as fh:
            fh.write("- three `#bbbbbbbb`\n")

        sync_spec = importlib.util.spec_from_file_location(
            "sync_enforcement_map_retire", os.path.join(REPO, "bin", "sync-enforcement-map.py"))
        sync_mod = importlib.util.module_from_spec(sync_spec)
        sync_spec.loader.exec_module(sync_mod)
        sync_mod.REPO = repo

        prior_argv = sys.argv[:]
        prior_vault_env = os.environ.get("CARR_VAULT")
        os.environ["CARR_VAULT"] = vault
        sys.argv = [prior_argv[0], "--no-commit"]
        try:
            rc_code = sync_mod.main()
        finally:
            sys.argv = prior_argv
            if prior_vault_env is None:
                os.environ.pop("CARR_VAULT", None)
            else:
                os.environ["CARR_VAULT"] = prior_vault_env

        if rc_code != 0:
            print(f"FAIL  {name}: sync main() returned {rc_code}")
            return False

        with open(map_path, encoding="utf-8") as fh:
            synced = json.load(fh)

        if "cccccccc" in (synced.get("rule_controls") or {}):
            print(f"FAIL  {name}: retired rule still has a rule_controls entry")
            return False
        still_named = [cat for cat, ids in (synced.get("category_overrides") or {}).items()
                       if "cccccccc" in ids]
        if still_named:
            print(f"FAIL  {name}: retired rule still named by override(s) {still_named}")
            return False
        # The rules that did NOT retire must survive untouched — a prune that
        # over-reaches is worse than one that under-reaches.
        if "aaaaaaaa" not in (synced.get("rule_controls") or {}) \
                or synced["category_overrides"].get("hard_pre_action") != ["aaaaaaaa"]:
            print(f"FAIL  {name}: the prune took a surviving rule with it")
            return False

        source_ids = {"shared": ["aaaaaaaa", "dddddddd"], "joe": ["bbbbbbbb"]}
        errors = mod.validate(synced, source_ids)
        ok = not errors
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {errors or 'valid'}")
        return ok
    finally:
        subprocess.run(["rm", "-rf", repo, vault])


def case_sync_prunes_an_already_stranded_rule():
    """The case the first version of the prune got wrong, found on live data.

    A prune keyed off THIS RUN'S `dropped` only fires on the run that notices
    the retirement. By the time the fix existed, the unfixed job had already
    synced the inventory (commit c666629 on main), so every later run saw the
    inventory in parity, took the early "OK already in parity" return, and left
    the stragglers wedged in the file permanently — the gate staying broken with
    the repair sitting right there. A repair that only works if it runs before
    the damage is not a repair.

    So the fixture here has an inventory ALREADY in parity with the renders and a
    rule_controls entry plus an override for an id no render carries. Nothing is
    `dropped`; the run must still notice and clean it.
    """
    name = "sync job prunes a rule stranded by an earlier run, inventory already in parity"
    repo = tempfile.mkdtemp(prefix="syncmap-stranded-selftest-")
    vault = tempfile.mkdtemp(prefix="syncmap-stranded-vault-")
    try:
        os.makedirs(os.path.join(repo, "ops", "config"), exist_ok=True)
        import shutil as _shutil
        _shutil.copyfile(
            os.path.join(REPO, "ops", "rule-enforcement-map-check.py"),
            os.path.join(repo, "ops", "rule-enforcement-map-check.py"))
        map_path = os.path.join(repo, "ops", "config", "rule-enforcement-map.json")
        baseline_path = os.path.join(repo, "ops", "config", "gate-baseline.json")

        seed = copy.deepcopy(BASE)
        # cccccccc is NOT in any inventory — an earlier run already removed it
        # from there and could not remove it from these two places.
        seed["rule_controls"]["cccccccc"] = {
            "category": "session_task_rail", "enforcement_class": "surfacing",
            "binding_moment": "at session start", "control": "real",
            "exceptions": "none"}
        seed["category_overrides"]["session_task_rail"] = ["cccccccc"]
        with open(map_path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh, indent=2)
            fh.write("\n")
        import hashlib
        seed_hash = hashlib.sha256(open(map_path, "rb").read()).hexdigest()
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump({"contracts": {"rule-enforcement-map.json": seed_hash}}, fh)

        os.makedirs(os.path.join(vault, "DNA"), exist_ok=True)
        os.makedirs(os.path.join(vault, "00_Context"), exist_ok=True)
        # Renders match the inventory exactly — this run has nothing to sync.
        with open(os.path.join(vault, "DNA", "compiled-rules-shared.md"), "w") as fh:
            fh.write("- one `#aaaaaaaa`\n- two `#dddddddd`\n")
        with open(os.path.join(vault, "00_Context", "compiled-rules-joe.md"), "w") as fh:
            fh.write("- three `#bbbbbbbb`\n")

        sync_spec = importlib.util.spec_from_file_location(
            "sync_enforcement_map_stranded", os.path.join(REPO, "bin", "sync-enforcement-map.py"))
        sync_mod = importlib.util.module_from_spec(sync_spec)
        sync_spec.loader.exec_module(sync_mod)
        sync_mod.REPO = repo

        prior_argv = sys.argv[:]
        prior_vault_env = os.environ.get("CARR_VAULT")
        os.environ["CARR_VAULT"] = vault
        sys.argv = [prior_argv[0], "--no-commit"]
        try:
            rc_code = sync_mod.main()
        finally:
            sys.argv = prior_argv
            if prior_vault_env is None:
                os.environ.pop("CARR_VAULT", None)
            else:
                os.environ["CARR_VAULT"] = prior_vault_env

        if rc_code != 0:
            print(f"FAIL  {name}: sync main() returned {rc_code}")
            return False

        with open(map_path, encoding="utf-8") as fh:
            synced = json.load(fh)
        if "cccccccc" in (synced.get("rule_controls") or {}):
            print(f"FAIL  {name}: stranded rule still has a rule_controls entry")
            return False
        if any("cccccccc" in ids for ids in (synced.get("category_overrides") or {}).values()):
            print(f"FAIL  {name}: stranded rule is still named by an override")
            return False

        source_ids = {"shared": ["aaaaaaaa", "dddddddd"], "joe": ["bbbbbbbb"]}
        errors = mod.validate(synced, source_ids)
        ok = not errors
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {errors or 'valid'}")
        return ok
    finally:
        subprocess.run(["rm", "-rf", repo, vault])


def _run_cases():
    cases = [
        case("exact coverage: one entry per honest shape", copy.deepcopy(BASE), True),
        case("complete entry per class passes (deny_gate + judgment_ambient + unbuilt)",
             copy.deepcopy(BASE), True),
        case("zero-render machine validates versioned local contract",
             copy.deepcopy(BASE), True, None),
        case("missing active id", {**copy.deepcopy(BASE), "active_rule_ids": {"shared": ["dddddddd"], "joe": ["bbbbbbbb"]}}, False),
        case("malformed local inventory id", {**copy.deepcopy(BASE), "active_rule_ids": {"shared": ["not-an-id", "dddddddd"], "joe": ["bbbbbbbb"]}}, False, None),
        case("extra scope fails render parity", {**copy.deepcopy(BASE), "active_rule_ids": {**BASE["active_rule_ids"], "dell": []}}, False),
        case("duplicate scope id", {**copy.deepcopy(BASE), "active_rule_ids": {"shared": ["aaaaaaaa", "bbbbbbbb"], "joe": ["bbbbbbbb"]}}, False),
        case("unknown override", {**copy.deepcopy(BASE), "category_overrides": {"hard_pre_action": ["cccccccc"]}}, False),
        case("double classification", {**copy.deepcopy(BASE), "category_overrides": {"hard_pre_action": ["aaaaaaaa"], "session_task_rail": ["aaaaaaaa"]}}, False),
        case("no entries at all fails every active rule", {**copy.deepcopy(BASE), "rule_controls": {}}, False),
        case("missing local control path fails", {**copy.deepcopy(BASE), "control_catalog": {"real": {"implementation": ["hooks/missing.py"], "test": ["ops/rule-enforcement-map-selftest.py"], "failure_mode": "deny"}}}, False),

        # The five cases the 2026-08-14 rule-enforceability audit named explicitly.
        case("unmapped active rule fails the check",
             {**copy.deepcopy(BASE),
              "active_rule_ids": {"shared": ["aaaaaaaa", "dddddddd", "eeeeeeee"], "joe": ["bbbbbbbb"]}},
             False,
             {"shared": ["aaaaaaaa", "dddddddd", "eeeeeeee"], "joe": ["bbbbbbbb"]}),
        case("judgment_ambient without why_unenforceable fails",
             {**copy.deepcopy(BASE), "rule_controls": rc(bbbbbbbb={"category": "judgment_advisory", "enforcement_class": "judgment_ambient"})},
             False),
        case("unbuilt without planned_control fails",
             {**copy.deepcopy(BASE), "rule_controls": rc(dddddddd={"category": "judgment_advisory", "enforcement_class": "unbuilt"})},
             False),
        case("complete entry per class: built deny_gate alone passes",
             {**copy.deepcopy(BASE),
              "active_rule_ids": {"shared": ["aaaaaaaa"], "joe": []},
              "rule_controls": rc(bbbbbbbb=None, dddddddd=None)},
             True,
             {"shared": ["aaaaaaaa"], "joe": []}),
        case_sync_adds_pending_unbuilt(),
        case_sync_prunes_a_retired_rule(),
        case_sync_prunes_an_already_stranded_rule(),
    ]
    print(f"rule-enforcement-map-selftest: {sum(cases)}/{len(cases)} passed")
    return 0 if all(cases) else 1


def main():
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        result = _run_cases()
    output = capture.getvalue()
    print(output, end="")
    if result:
        for line in output.splitlines():
            if line.startswith("FAIL  "):
                print(f"failure-summary: {line}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
