#!/usr/bin/env python3
"""gate-integrity.py — SessionStart attestation that the gates are still there.

THE PROBLEM THIS ADDRESSES, AND THE HONEST LIMIT OF ITS ANSWER.
Joe, 2026-08-09: "the gates live in a repo i can edit — so what are you
suggesting is the solution to that problem?"

Both council chairs raised this unprompted as the decisive hole. Grok: "If the
system can 'internally decide' to loosen its own collar, you do not have a
boundary... without it every gate is temporary cosplay." Codex: "None of these
hooks is truly unbypassable if an AI session with repo access can edit the
hook, settings, or launcher that enforces them... the agent may be the operator,
but it cannot also be the root authority that decides whether its own actions
were allowed."

THIS FILE IS NOT THE FIX. IT IS THE ALARM.
A session running as Joe's user can edit hooks/*.py, can edit this file, and can
edit the baseline this file compares against. Two edits instead of one is
friction and a git trace — it is not prevention, and claiming otherwise would be
exactly the "hardness cosplay" the council warned about.

THE ACTUAL FIX IS ops/harden-gates.sh, WHICH NEEDS ROOT (Joe's password, one
time). It chowns the hook scripts and ~/.claude/settings.json to root:wheel with
mode 644/755 — readable and executable by the session, writable by nobody but
root. sudo is in the deny list and needs a password only Joe holds, so that
boundary is enforced by the OS rather than by good intentions. Once that is
done, THIS hook's job changes from "detect drift" to "detect that hardening was
undone", which is a much stronger signal because it should never happen.

WHAT IT CHECKS, every SessionStart:
  1. Each gate script's SHA-256 against ops/config/gate-baseline.json.
  2. The live settings.json hooks block against the repo's ops/config/hooks.json
     — a gate whose script is pristine but which settings no longer INVOKES is
     off, and that is the failure that actually happened on 2026-08-08 when a
     plugin install silently deleted the whole hooks block for a day.
  3. Whether hardening is in place (root ownership), reported every session so
     "we never did that" cannot quietly become the status quo.
  4. That every gate named in the baseline still EXISTS. A deleted gate is the
     loudest possible failure and must not read as "no drift".

OUTPUT: stdout on a SessionStart hook is injected into the session's context, so
a failure is stated to the session AND visible to Joe in the same breath. Silent
success prints one short line — a check nobody ever sees the output of is a
check nobody trusts.

Regenerate the baseline deliberately after an intended gate change:
    python3 hooks/gate-integrity.py --bless
That writes the current hashes and stamps who/when. It is deliberately a
separate, named action so that "the baseline moved" is always a decision in the
git log rather than a side effect.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_repo(repo=None):
    """The checkout the INSTALLED adapters point at — the main worktree.

    WHY THIS IS NOT JUST REPO. Worktree-per-session is this repo's default
    (rule 4a53ff82), so most sessions run from .claude/worktrees/<name>, where
    REPO is that worktree. But there is only ever ONE installed wiring:
    ~/.claude/settings.json and ~/.codex/hooks.json name absolute commands in
    the canonical checkout. Rendering {{REPO}} as the worktree therefore made
    every expected command mismatch the live one, and the wiring comparison
    reported all 40 gates as "found 0" — a full GATE INTEGRITY FAILURE banner,
    from a machine whose gates were 40/40 exact.

    That is worse than a cosmetic bug. The banner tells the session to open its
    next message to Joe by saying the gates are not in force, so the default
    working tree produced a false alarm on the one check that guards every
    other gate; and a session that learns to disregard it has been trained to
    ignore exactly the signal that caught the 2026-08-08 outage. Recorded as
    defect class worktree-run-of-gate-integrity-reports-false-total-failure.

    Content checks are unaffected and stay tree-local on purpose: hashing the
    hook files in the tree under test is what catches tampering, and that half
    was always correct. Only the WIRING comparison moves to the install target.

    In the canonical checkout this returns REPO unchanged, so nothing about the
    normal path changes. Falls back to REPO whenever git cannot answer.
    """
    repo = repo or REPO
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return repo
    common = out.stdout.strip()
    if out.returncode != 0 or not common:
        return repo
    # --git-common-dir points at the MAIN worktree's .git directory whichever
    # worktree asked, so its parent is the canonical checkout.
    candidate = os.path.dirname(os.path.abspath(common))
    return candidate if os.path.isdir(os.path.join(candidate, "hooks")) else repo


INSTALL_REPO = install_repo()
HOOKS = os.path.join(REPO, "hooks")
BASELINE = os.path.join(REPO, "ops", "config", "gate-baseline.json")
REPO_HOOKS_JSON = os.path.join(REPO, "ops", "config", "hooks.json")
DELEGATION_HOOK_CONFIG = os.path.join(
    REPO, "ops", "config", "delegation-gate-hook.json"
)
CODEX_HOOKS_REPO = os.path.join(REPO, "ops", "config", "codex-hooks.json")
CODEX_HOOKS_LIVE = os.path.expanduser("~/.codex/hooks.json")
CODEX_CONFIG_LIVE = os.path.expanduser("~/.codex/config.toml")
RULE_ENFORCEMENT_MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
RULE_ENFORCEMENT_MAP_CHECK = os.path.join(REPO, "ops", "rule-enforcement-map-check.py")
# model-floors.json added 2026-08-13 with model-floor-gate.py. A gate whose
# BEHAVIOUR is driven by a data file is only as tamper-evident as that file:
# hashing the script alone would let someone lower or delete a voice run's model
# floor without anything noticing, which is the whole failure the baseline
# exists to make visible.
MODEL_FLOORS = os.path.join(REPO, "ops", "config", "model-floors.json")
SESSION_CONTEXT_LIFECYCLE = os.path.join(
    REPO, "ops", "config", "session-context-lifecycle.v2.json")
CONTRACTS = {
    "delegation-gate-hook.json": DELEGATION_HOOK_CONFIG,
    "hooks.json": REPO_HOOKS_JSON,
    "codex-hooks.json": CODEX_HOOKS_REPO,
    "rule-enforcement-map.json": RULE_ENFORCEMENT_MAP,
    "model-floors.json": MODEL_FLOORS,
    "session-context-lifecycle.v2.json": SESSION_CONTEXT_LIFECYCLE,
}
SETTINGS = os.path.expanduser("~/.claude/settings.json")


def project_settings_path(project_dir=None):
    """Settings for the project that launched this session, not Joe's Drive.

    Claude exposes CLAUDE_PROJECT_DIR to hooks.  The explicit argument keeps the
    resolver testable; REPO is the safe fallback for direct/manual invocation.
    """
    root = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or REPO
    return os.path.join(os.path.abspath(os.path.expanduser(root)),
                        ".claude", "settings.json")


def classify_codex_configuration(config_exists, hooks_exist):
    """Classify one optional Codex adapter; hooks-only is a broken adapter."""
    if config_exists:
        return "configured"
    if hooks_exist:
        return "partial"
    return "absent"


def codex_configuration_state(config_path=CODEX_CONFIG_LIVE,
                              hooks_path=CODEX_HOOKS_LIVE):
    return classify_codex_configuration(
        os.path.exists(config_path), os.path.exists(hooks_path)
    )


def claude_configuration_state(settings_path=SETTINGS):
    """Claude is an optional client adapter, not a CARR system dependency."""
    return "configured" if os.path.isfile(settings_path) else "absent"

# The files whose contents ARE the enforcement. Anything that can refuse, block,
# or classify belongs here.
#
# DERIVED, NOT TYPED — changed 2026-08-10 (loop #231) after this list was found
# four gates short. It was hand-maintained, so every gate built after it was
# written silently escaped the integrity check: gate-edit-gate.py (the Write/Edit
# door, wired in settings.json since 2026-08-09), git-writer-gate.py (also
# wired), session-brief.py (SessionStart), and the two shared classifiers that
# DEFINE what the deny gates deny — md_manifest.py and corpus_renders.py. Gutting
# md_manifest.py would have switched off the vault-wide .md deny-by-default while
# this check printed "10 gates match baseline; wiring OK".
#
# That matters more than an ordinary omission, because the detection layer is the
# whole justification for the 2026-08-10 downgrade from block to announce
# (decision bd30b665): "An unauthorised gate edit is caught either way." It was
# only true for the ten files somebody had remembered to type here.
#
# Reading the directory keeps it true for every gate added later, with nobody
# remembering anything. FLOOR is what the list held before, kept as a floor so a
# directory read that returns nothing degrades toward MORE coverage rather than
# silently hashing an empty set — the failure shape this whole file exists to
# catch.
FLOOR = [
    "guard-unattended.py",      # egress deny
    "record-home-gate.py",      # .md write deny
    "rule-shape-gate.py",       # teach shape
    "lint-gate.py",             # writing lint
    "ledger-sweep.py",          # Stop nudge
    "conduct-stop-gate.py",     # Stop HARD gate
    "context-handoff-gate.py",  # Stop context-threshold handoff
    "escalation-gate.py",       # AskUserQuestion deny
    "conduct_patterns.py",      # the shared classifier both conduct gates use
    "gate-integrity.py",        # this file — it must guard itself
]


def gated():
    """Every .py in hooks/ — gates and the classifiers they import alike.

    hooks/ holds nothing else by construction: fixtures live in ops/*-selftest.py
    and are deliberately excluded from protection everywhere (gating them would
    make testing a gate harder than weakening one).
    """
    try:
        found = sorted(f for f in os.listdir(HOOKS)
                       if f.endswith(".py") and not f.endswith("-selftest.py"))
    except Exception:
        found = []
    return sorted(set(found) | set(FLOOR))


GATED = gated()


def sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return None


def owner_of(path):
    """(uid, mode) or None. Root-owned + not user-writable is the real fix."""
    try:
        st = os.stat(path)
        return st.st_uid, oct(st.st_mode & 0o777)
    except FileNotFoundError:
        return None


def hardened(path):
    o = owner_of(path)
    if not o:
        return False
    uid, mode = o
    # root-owned AND the owner-write bit is the only write bit set
    return uid == 0 and not (int(mode, 8) & 0o022)


def current():
    return {n: sha(os.path.join(HOOKS, n)) for n in GATED}


def current_contracts():
    """Versioned wiring contracts can change enforcement without changing a hook."""
    return {name: sha(path) for name, path in CONTRACTS.items()}


def dirty_gate_files():
    """Gate files and contracts whose working copy differs from HEAD.

    THE WHOLE REASON THIS FUNCTION EXISTS (defect 320f5531, 2026-08-13). bless()
    used to rebuild the entire baseline from the working tree in one shot. With
    two or more sessions on one checkout that means a session blessing ITS OWN
    gate change silently adopts every other session's in-flight, uncommitted gate
    edit as approved. It happened the day two new gates landed: one session's
    bless absorbed another's modified delegation-gate.py, turning "an unreviewed
    edit is present" into "this edit is approved" — the one state this check
    exists to prevent. It was caught only because the session that caused it
    noticed and hand-restored that single baseline entry.
    """
    out = subprocess.run(["git", "-C", REPO, "diff", "--name-only", "HEAD"],
                         capture_output=True, text=True).stdout.split()
    changed = set()
    for rel in out:
        base = os.path.basename(rel)
        if base in GATED and rel.startswith("hooks/"):
            changed.add(base)
        for name, path in CONTRACTS.items():
            if os.path.abspath(path) == os.path.abspath(os.path.join(REPO, rel)):
                changed.add(name)
    return sorted(changed)


def bless(only=None):
    """Re-record the blessed baseline.

    `only` names the gates the caller INTENDS to bless; every other entry is
    carried forward from the existing baseline untouched. That is what makes a
    bless the caller's own act rather than a whole-machine one.

    Unscoped bless is still allowed, because on a clean tree it is harmless — it
    just re-records what is already committed. It REFUSES the moment any gate file
    is dirty, since that is exactly when it would adopt work the caller has not
    read. The refusal names the files and tells the caller to scope the call.
    """
    dirty = dirty_gate_files()
    if only is None and dirty:
        print("REFUSED: gate files are modified and this bless names none of them:",
              file=sys.stderr)
        for name in dirty:
            print(f"  {name}", file=sys.stderr)
        print("An unscoped bless rebuilds the WHOLE baseline from the working tree, "
              "so it would adopt every one of those as approved — including any "
              "written by another session. Name what you mean to bless:\n"
              "  python3 hooks/gate-integrity.py --bless <gate> [<gate>...]\n"
              "Everything you do not name keeps its existing blessed hash.",
              file=sys.stderr)
        return 1

    fresh_hashes, fresh_contracts = current(), current_contracts()
    known = set(fresh_hashes) | set(fresh_contracts)

    if only:
        unknown = [n for n in only if n not in known]
        if unknown:
            print(f"REFUSED: not gate file(s) this baseline tracks: {', '.join(unknown)}",
                  file=sys.stderr)
            return 1
        try:
            with open(BASELINE) as fh:
                prior = json.load(fh)
        except (OSError, ValueError):
            print("REFUSED: no readable existing baseline to carry forward, so a "
                  "scoped bless would silently drop every gate it does not name. "
                  "Re-run unscoped on a clean tree to establish one.", file=sys.stderr)
            return 1
        hashes = dict(prior.get("hashes") or {})
        contracts = dict(prior.get("contracts") or {})
        for name in only:
            if name in fresh_hashes:
                hashes[name] = fresh_hashes[name]
            if name in fresh_contracts:
                contracts[name] = fresh_contracts[name]
    else:
        hashes, contracts = fresh_hashes, fresh_contracts

    who = subprocess.run(["git", "-C", REPO, "config", "user.email"],
                         capture_output=True, text=True).stdout.strip()
    rev = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    data = {
        "_note": "Regenerated by hooks/gate-integrity.py --bless. A change here "
                 "should always accompany a deliberate gate change in the same "
                 "commit. If this moved on its own, that is the finding. A bless "
                 "naming specific gates rewrites only those; everything else is "
                 "carried forward, so one session's bless cannot adopt another's "
                 "in-flight edit.",
        "blessed_by": "git-actor-sha256:" + hashlib.sha256(who.encode()).hexdigest() if who else "unknown",
        "blessed_at_rev": rev or "unknown",
        "hashes": hashes,
        "contracts": contracts,
    }
    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
    with open(BASELINE, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    scope = f"{len(only)} named gate(s)" if only else \
            f"{len([v for v in hashes.values() if v])} gate(s)"
    print(f"blessed {scope} -> {BASELINE}")
    return 0


def render_config(path):
    """Load one portable hooks config with this machine's concrete paths."""
    raw = open(path).read()
    # {{REPO}} renders the command the INSTALLED adapter must be invoking, which
    # is always the main worktree — see install_repo(). Identical to REPO in the
    # canonical checkout.
    raw = raw.replace("{{REPO}}", INSTALL_REPO).replace("{{HOME}}", os.path.expanduser("~"))
    return json.loads(raw.replace("{{VAULT}}", os.path.expanduser("~/My Drive/CARR AI")))


def hook_tuples(hooks):
    """Exact event/matcher/command/timeout tuples, preserving duplicate evidence."""
    out = []
    for event, groups in (hooks or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher", "")
            for hook in group.get("hooks", []) or []:
                if isinstance(hook, dict):
                    out.append((event, matcher, hook.get("type"), hook.get("command"), hook.get("timeout")))
    return out


def validate_expected_wiring(live, expected):
    """Return exact CARR-owned wiring defects while allowing unrelated user hooks."""
    want = hook_tuples(expected)
    have = hook_tuples(live)
    errors = []
    for item, count in Counter(want).items():
        actual = have.count(item)
        if actual != count:
            errors.append(f"expected {count} exact {item[0]}/{item[1] or 'Stop'} hook {os.path.basename(str(item[3]))}; found {actual}")
    wanted_commands = {item[3] for item in want}
    wanted_events = {item[0] for item in want}
    for item in have:
        if item[0] in wanted_events and item[3] in wanted_commands and item not in want:
            errors.append(f"CARR hook tuple drifted for {os.path.basename(str(item[3]))}: matcher/timeout/type is not the tracked contract")
    return errors


def settings_matches_repo():
    """Is every CARR-owned Claude hook exactly wired once in live settings?"""
    try:
        live = json.load(open(SETTINGS)).get("hooks", {})
    except Exception:
        return None, "settings.json unreadable"
    try:
        want = render_config(REPO_HOOKS_JSON)
    except Exception as e:
        return None, f"repo hooks.json unparseable: {e}"
    return validate_expected_wiring(live, want), None


# A gate new in source and not yet installed reports PENDING INSTALL, not a
# failure: its source lands (and is blessed) before `config-as-code install`
# wires it, and a false GATE INTEGRITY FAILURE in every session in between
# would teach sessions to ignore the real one. Only while NONE of the gate's
# tuples is installed, and only until it has been seen installed once on this
# machine (a stamp under out/): after that, a missing tuple is tampering and
# fails like any other.
PENDING_INSTALL_GATES = ("rule-boot-gate.py",)
PENDING_INSTALL_STAMP_DIR = os.path.join(REPO, "out", "gate-install-seen")


def split_pending_install(wiring_errors, live):
    """(real_errors, pending_gate_names) for the Claude adapter wiring."""
    live_commands = [str(t[3]) for t in hook_tuples(live)]
    pending, real = set(), []
    for err in wiring_errors:
        gate = next((g for g in PENDING_INSTALL_GATES
                     if err.endswith(f"hook {g}; found 0")), None)
        installed_any = gate and any(c.endswith("/" + gate) or c.endswith(" " + gate)
                                     for c in live_commands)
        seen = gate and os.path.exists(os.path.join(PENDING_INSTALL_STAMP_DIR, gate))
        if gate and not installed_any and not seen:
            pending.add(gate)
        else:
            real.append(err)
    for gate in PENDING_INSTALL_GATES:
        if any(c.endswith("/" + gate) for c in live_commands):
            try:
                os.makedirs(PENDING_INSTALL_STAMP_DIR, exist_ok=True)
                open(os.path.join(PENDING_INSTALL_STAMP_DIR, gate), "a").close()
            except OSError:
                pass
    return real, sorted(pending)


def delegation_hook_contract():
    """Load the versioned, exact CARR-project interception contract."""
    try:
        contract = json.load(open(DELEGATION_HOOK_CONFIG))
    except Exception as exc:
        return None, f"delegation hook contract unreadable: {exc}"
    required = {"version", "event", "matcher", "command", "timeout",
                "known_read_sweep_tools"}
    if not isinstance(contract, dict) or not required.issubset(contract):
        return None, "delegation hook contract is missing required fields"
    if contract["version"] != 1 or contract["event"] != "PreToolUse":
        return None, "delegation hook contract has an unsupported version/event"
    expected_tools = contract["matcher"].split("|")
    if (not all(isinstance(tool, str) and tool for tool in expected_tools)
            or contract["known_read_sweep_tools"] != expected_tools):
        return None, "delegation hook matcher does not exactly name its classified tools"
    if (not isinstance(contract["command"], str)
            or not isinstance(contract["timeout"], int)):
        return None, "delegation hook contract command/timeout is malformed"
    err = contract_shape_error(contract["command"],
                               contract.get("canonical_only_commands", []))
    if err:
        return None, err
    return contract, None


def contract_shape_error(command, canonical_only=()):
    """The path-resolution contract for the delegation hook command.

    The command must prefer the canonical ~/carr-system checkout (so on a Mac a
    worktree or branch can never swap in its own gate) and must fall back to
    $CLAUDE_PROJECT_DIR when that checkout is absent (a Claude Code cloud
    container clones the repo elsewhere; without the fallback python3 exits 2
    and every Bash/Read/Grep/Glob call is blocked). ops/cloud-hook-paths-
    selftest.py runs the command itself in both layouts; this is the static
    half, so a hand edit to the contract that drops either path fails here.

    canonical_only_commands is an exact, enumerated list of the older
    canonical-only form, accepted for Mac-only projects such as the Drive vault
    whose live settings still carry it. On a Mac both forms run the same file.
    """
    if "/Users/" in command or "${HOME}/carr-system/" not in command:
        return "delegation hook contract is not machine-portable"
    if "${CLAUDE_PROJECT_DIR" not in command:
        return "delegation hook contract has no $CLAUDE_PROJECT_DIR fallback"
    if not isinstance(canonical_only, (list, tuple)):
        return "delegation hook canonical_only_commands must be a list"
    for extra in canonical_only:
        if (not isinstance(extra, str) or "/Users/" in extra
                or "${HOME}/carr-system/" not in extra):
            return "delegation hook canonical_only_commands entry is not machine-portable"
    return None


def validate_delegation_wiring(live, contract):
    """Require one exact matcher with one exact command and timeout.

    A substring check would accept an injected shell suffix, a narrowed matcher,
    or a slow timeout that makes the boundary intermittent.  Keep this pure so
    --selftest can prove its negative cases without touching real settings.
    """
    entries = live.get(contract["event"], []) if isinstance(live, dict) else []
    matching = [entry for entry in entries
                if isinstance(entry, dict)
                and entry.get("matcher") == contract["matcher"]]
    if len(matching) != 1:
        return False, "expected exactly one project PreToolUse matcher"
    accepted = [contract["command"], *contract.get("canonical_only_commands", [])]
    expected_hooks = [[{
        "type": "command",
        "command": command,
        "timeout": contract["timeout"],
    }] for command in accepted]
    if matching[0].get("hooks") not in expected_hooks:
        return False, "project matcher command or timeout is not exact"
    return True, None


def project_delegation_wired(settings_path=None):
    """The delegation gate is CARR-project-only, never a global Claude hook."""
    contract, err = delegation_hook_contract()
    if err:
        return False, err
    settings_path = settings_path or project_settings_path()
    try:
        live = json.load(open(settings_path)).get("hooks", {})
    except Exception as exc:
        return False, f"CARR project settings unreadable at {settings_path}: {exc}"
    return validate_delegation_wiring(live, contract)


def codex_wiring_matches_repo():
    """Codex has one machine hooks document; enforce CARR scope in its scripts.

    Codex supports repository-local hooks, but this installation deliberately
    keeps one tracked global document because both ~/carr-system and the Drive
    CARR AI vault are active roots for the same operating system. Each added
    Codex path self-scopes to CARR before applying a rule, so Life AI and other
    repos receive no new behavior. Compare parsed JSON to avoid whitespace
    drift hiding a matcher, command, or timeout change. This proves only that
    the installed file exactly matches this contract; Codex runtime trust and
    invocation still require a live negative smoke.
    """
    try:
        wanted = json.load(open(CODEX_HOOKS_REPO))
        # INSTALL_REPO, not REPO: same reason as render_config — ~/.codex/hooks.json
        # names commands in the canonical checkout whichever worktree asks.
        rendered = json.loads(json.dumps(wanted).replace("{{REPO}}", INSTALL_REPO))
        live = json.load(open(CODEX_HOOKS_LIVE))
    except Exception as exc:
        return False, f"Codex hooks unreadable: {exc}"
    errors = validate_carr_owned_wiring(live.get("hooks", {}), rendered.get("hooks", {}))
    if errors:
        return False, "; ".join(errors)
    return True, None


def is_carr_hook_command(command):
    """Only paths owned by this CARR checkout are subject to exact comparison."""
    if not isinstance(command, str):
        return False
    candidate = command.replace("\\", "/").lower()
    return "/carr-system/hooks/" in candidate or "/my drive/carr ai/hooks/" in candidate


def validate_carr_owned_wiring(live, expected):
    """Exact CARR tuples once each, while allowing unrelated global Codex hooks."""
    errors = validate_expected_wiring(live, expected)
    wanted = set(hook_tuples(expected))
    for item in hook_tuples(live):
        if is_carr_hook_command(item[3]) and item not in wanted:
            errors.append("untracked/drifted CARR hook tuple: "
                          f"{item[0]}/{item[1] or 'Stop'} {os.path.basename(str(item[3]))}")
    return errors


def rule_enforcement_map_matches_inventory(runner=subprocess.run):
    """Run the semantic map checker, not just its baseline hash.

    A blessed JSON file can still be stale relative to the compiled rule
    inventory. Give that checker a bounded execution path so integrity cannot
    call the contract green on hash equality alone.
    """
    try:
        result = runner([sys.executable, RULE_ENFORCEMENT_MAP_CHECK], cwd=REPO,
                        capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return False, "rule-enforcement map checker timed out after 45s"
    except Exception as exc:
        return False, f"rule-enforcement map checker could not run: {type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, None
    detail = ((result.stderr or result.stdout or "no output").strip().splitlines() or ["no output"])[-1]
    return False, f"rule-enforcement map checker failed: {detail}"


def delegation_wiring_selftest():
    """Regression-test the exact matcher/command/timeout comparison itself."""
    contract = {
        "version": 1,
        "event": "PreToolUse",
        "matcher": "Bash|Read",
        "command": "/exact/delegation-gate.py",
        "timeout": 10,
        "known_read_sweep_tools": ["Bash", "Read"],
    }
    good = {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [{
        "type": "command", "command": "/exact/delegation-gate.py", "timeout": 10,
    }]}]}
    cases = [
        ("exact wiring accepted", good, True),
        ("narrowed matcher rejected", {"PreToolUse": [{"matcher": "Bash", "hooks": good["PreToolUse"][0]["hooks"]}]}, False),
        ("command suffix rejected", {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [{
            "type": "command", "command": "/exact/delegation-gate.py --later", "timeout": 10,
        }]}]}, False),
        ("timeout drift rejected", {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [{
            "type": "command", "command": "/exact/delegation-gate.py", "timeout": 11,
        }]}]}, False),
    ]
    outcomes = []
    for name, live, expected in cases:
        accepted, _ = validate_delegation_wiring(live, contract)
        ok = accepted == expected
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        outcomes.append(ok)
    # A Mac-only project (the Drive vault) may keep the canonical-only form;
    # it is an exact, enumerated alternative, never a substring match.
    legacy = "/exact/canonical-only/delegation-gate.py"
    with_legacy = dict(contract, canonical_only_commands=[legacy])
    legacy_live = {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [{
        "type": "command", "command": legacy, "timeout": 10,
    }]}]}
    suffixed_legacy = {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [{
        "type": "command", "command": legacy + " --later", "timeout": 10,
    }]}]}
    legacy_cases = [
        ("listed canonical-only command accepted", legacy_live, with_legacy, True),
        ("canonical-only command without a listing rejected", legacy_live, contract, False),
        ("suffixed canonical-only command rejected", suffixed_legacy, with_legacy, False),
        ("portable command still accepted beside a listing", good, with_legacy, True),
    ]
    for name, live, which, expected in legacy_cases:
        accepted, _ = validate_delegation_wiring(live, which)
        ok = accepted == expected
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        outcomes.append(ok)
    portable = ('if [ -d "${HOME}/carr-system/hooks" ]; then r="${HOME}/carr-system"; '
                'else r="${CLAUDE_PROJECT_DIR:-}"; fi; exec python3 "$r/hooks/delegation-gate.py"')
    shape_cases = [
        ("contract with a $CLAUDE_PROJECT_DIR fallback accepted", portable, [], True),
        ("contract without a $CLAUDE_PROJECT_DIR fallback rejected",
         '/usr/bin/env python3 "${HOME}/carr-system/hooks/delegation-gate.py"', [], False),
        ("contract with a machine-specific canonical-only command rejected", portable,
         ["/usr/bin/env python3 /Users/x/carr-system/hooks/delegation-gate.py"], False),
    ]
    for name, command, extra, expected in shape_cases:
        ok = (contract_shape_error(command, extra) is None) == expected
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        outcomes.append(ok)
    expected_wiring = {"PreToolUse": [{"matcher": "Bash", "hooks": [{
        "type": "command", "command": "/exact/guard.py", "timeout": 10,
    }]}]}
    wiring_cases = [
        ("global exact tuple accepted", expected_wiring, True),
        ("global narrowed matcher rejected", {"PreToolUse": [{"matcher": "Read", "hooks": expected_wiring["PreToolUse"][0]["hooks"]}]}, False),
        ("global timeout drift rejected", {"PreToolUse": [{"matcher": "Bash", "hooks": [{
            "type": "command", "command": "/exact/guard.py", "timeout": 11,
        }]}]}, False),
        ("global duplicate rejected", {"PreToolUse": expected_wiring["PreToolUse"] * 2}, False),
        ("unrelated hook allowed", {"PreToolUse": expected_wiring["PreToolUse"] + [{"matcher": "Other", "hooks": [{
            "type": "command", "command": "/other/plugin.py", "timeout": 3,
        }]}]}, True),
    ]
    for name, live, expected_ok in wiring_cases:
        ok = (not validate_expected_wiring(live, expected_wiring)) == expected_ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        outcomes.append(ok)
    codex_expected = {"Stop": [{"matcher": "", "hooks": [{
        "type": "command", "command": "/Users/booko/carr-system/hooks/completion-evidence-gate.py", "timeout": 15,
    }]}]}
    codex_live = {"Stop": codex_expected["Stop"] + [{"matcher": "", "hooks": [{
        "type": "command", "command": "/Users/booko/other/hooks/keep.py", "timeout": 5,
    }]}]}
    codex_cases = [
        ("Codex unrelated tuple allowed", codex_live, True),
        ("Codex CARR duplicate rejected", {"Stop": codex_expected["Stop"] * 2}, False),
        ("Codex untracked CARR tuple rejected", {"Stop": codex_expected["Stop"] + [{"matcher": "", "hooks": [{
            "type": "command", "command": "/Users/booko/carr-system/hooks/other.py", "timeout": 15,
        }]}]}, False),
    ]
    for name, live, expected_ok in codex_cases:
        ok = (not validate_carr_owned_wiring(live, codex_expected)) == expected_ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        outcomes.append(ok)
    role_cases = [
        ("machine without Codex adapter accepted", False, False, "absent"),
        ("Codex configured machine detected", True, True, "configured"),
        ("Codex config without hooks still requires repair", True, False, "configured"),
        ("Codex hooks-only half-install rejected", False, True, "partial"),
    ]
    for name, config_exists, hooks_exist, expected in role_cases:
        ok = classify_codex_configuration(config_exists, hooks_exist) == expected
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        outcomes.append(ok)
    resolved = project_settings_path("/Users/dell/carr-system")
    ok = resolved == "/Users/dell/carr-system/.claude/settings.json"
    print(f"{'PASS' if ok else 'FAIL'}  active project settings are machine-neutral")
    outcomes.append(ok)
    claude_cases = [
        ("machine without Claude adapter accepted", "/__carr_missing__/settings.json", "absent"),
        ("Claude adapter detected", __file__, "configured"),
    ]
    for name, path, expected in claude_cases:
        ok = claude_configuration_state(path) == expected
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        outcomes.append(ok)
    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
    def succeeds(*args, **kwargs):
        return Result()
    def fails(*args, **kwargs):
        return Result(1, stderr="coverage missing")
    def times_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("rule-enforcement-map-check.py", 45)
    map_cases = [
        ("rule map check accepted", succeeds, True),
        ("rule map checker failure rejected", fails, False),
        ("rule map checker timeout rejected", times_out, False),
    ]
    for name, runner, expected_ok in map_cases:
        ok, _ = rule_enforcement_map_matches_inventory(runner)
        passes = ok == expected_ok
        print(f"{'PASS' if passes else 'FAIL'}  {name}")
        outcomes.append(passes)
    print(f"{sum(outcomes)}/{len(outcomes)} exact wiring cases passed")
    return 0 if all(outcomes) else 1


def main():
    if "--selftest" in sys.argv:
        return delegation_wiring_selftest()
    if "--bless" in sys.argv:
        # Everything after --bless names the gates to re-bless. No names means
        # the whole baseline, which is refused while any gate file is dirty.
        rest = sys.argv[sys.argv.index("--bless") + 1:]
        names = [os.path.basename(a) for a in rest if not a.startswith("-")]
        return bless(names or None)

    # --strict is for CI, and CONTENT is the half of the report it may fail on.
    #
    # WHY THE SPLIT EXISTS (found 2026-08-14 by watching it fail). ops/ci.sh has
    # always called this script and its own comment claimed a gate edited
    # without a re-bless "fails here". It could not: every path below returns 0,
    # deliberately, because this is a SessionStart hook and a boot check that
    # wedges a session is worse than the drift it reports. So CI announced a
    # baseline check it never performed — and pull request #102, cut before #99
    # merged, overwrote main's baseline and dropped two gates with CI green the
    # whole way. The pre-commit co-change check cannot see that one: the
    # overwrite happens inside GitHub's merge, where no local hook runs, which
    # leaves CI as the only layer that can catch it.
    #
    # CONTENT problems are facts about the repository — a hash that does not
    # match, a blessed gate gone, a gate nothing blessed. True on every machine
    # and on a bare runner. ENVIRONMENT problems (live settings wiring, the
    # Codex adapter, the vault-backed rule map) depend on a configured machine,
    # and a runner legitimately has none of it. Failing CI on those would get
    # --strict deleted within a week, and then it protects nothing.
    problems = []
    content_problems = []
    strict = "--strict" in sys.argv

    if not os.path.exists(BASELINE):
        print("GATE INTEGRITY: no baseline yet — run "
              "`python3 hooks/gate-integrity.py --bless` after reviewing the gates. "
              "Until then, gate tampering is UNDETECTED.")
        # No baseline at all IS a content fact, and a repository that lost it
        # must not merge: that is indistinguishable from deleting the evidence.
        return 1 if strict else 0

    base = json.load(open(BASELINE)).get("hashes", {})
    now = current()
    baseline_data = json.load(open(BASELINE))
    base_contracts = baseline_data.get("contracts", {})
    now_contracts = current_contracts()

    def content(problem):
        """A fact about the repository — the half --strict may fail CI on."""
        content_problems.append(problem)
        problems.append(problem)

    for name, want in base.items():
        got = now.get(name)
        if got is None:
            content(f"MISSING: hooks/{name} is GONE — that gate is off")
        elif not isinstance(want, str) or not re.fullmatch(r"[0-9a-f]{64}", want):
            content(f"INVALID: hooks/{name} has no valid SHA-256 baseline")
        elif got != want:
            content(f"CHANGED: hooks/{name} no longer matches the blessed baseline")
    for name, got in now.items():
        if name not in base and got:
            content(f"UNBLESSED: hooks/{name} exists but is not in the baseline")
    for name, want in base_contracts.items():
        got = now_contracts.get(name)
        if got is None:
            content(f"MISSING: ops/config/{name} is GONE — its wiring contract is off")
        elif not isinstance(want, str) or not re.fullmatch(r"[0-9a-f]{64}", want):
            content(f"INVALID: ops/config/{name} has no valid SHA-256 baseline")
        elif got != want:
            content(f"CHANGED: ops/config/{name} no longer matches the blessed baseline")
    for name, got in now_contracts.items():
        if name not in base_contracts and got:
            content(f"UNBLESSED: ops/config/{name} exists but is not in the baseline")

    map_valid, map_err = rule_enforcement_map_matches_inventory()
    if not map_valid:
        problems.append(f"RULE ENFORCEMENT MAP: {map_err}")

    codex_state = codex_configuration_state()
    if codex_state == "partial":
        problems.append(
            "CODEX WIRING: hooks.json exists without config.toml — this is a "
            "broken optional client adapter, not a CARR core dependency"
        )
    elif codex_state == "configured":
        codex_wired, codex_err = codex_wiring_matches_repo()
        if codex_err:
            problems.append(f"CODEX WIRING: {codex_err}")
        elif not codex_wired:
            problems.append(
                "NOT WIRED UP: Codex does not run the tracked hooks contract — "
                "the CARR-scoped Codex boundary is not active"
            )

    claude_state = claude_configuration_state()
    pending_install = []
    if claude_state == "configured":
        wiring_errors, err = settings_matches_repo()
        if not err and wiring_errors:
            try:
                live_hooks = json.load(open(SETTINGS)).get("hooks", {})
            except Exception:
                live_hooks = {}
            wiring_errors, pending_install = split_pending_install(wiring_errors, live_hooks)
        if err:
            problems.append(f"CLAUDE ADAPTER WIRING: {err}")
        elif wiring_errors:
            problems.append("CLAUDE ADAPTER WIRING: "
                            + "; ".join(sorted(set(wiring_errors))))

    project_adapter = project_settings_path()
    project_adapter_state = "available" if os.path.isfile(project_adapter) else "absent"
    if project_adapter_state == "available":
        delegation_wired, delegation_err = project_delegation_wired(project_adapter)
        if delegation_err:
            problems.append(f"CARR CLAUDE PROJECT ADAPTER: {delegation_err}")
        elif not delegation_wired:
            problems.append(
                "CLAUDE PROJECT ADAPTER DRIFT: CARR delegation matcher/command/timeout "
                "does not match ops/config/delegation-gate-hook.json"
            )

    unhardened = [n for n in GATED
                  if os.path.exists(os.path.join(HOOKS, n))
                  and not hardened(os.path.join(HOOKS, n))]

    if problems:
        print("=" * 70)
        print("GATE INTEGRITY FAILURE — the enforcement layer changed.")
        for p in problems:
            print(f"  - {p}")
        print()
        print("This session must NOT treat the gates as in force. Tell Joe in "
              "your first response, before doing any other work. If the change "
              "was intentional and reviewed, re-bless the baseline in the same "
              "commit as the gate change. If it was not intentional, that is a "
              "finding: something disabled a gate. On 2026-08-08 a plugin "
              "install silently deleted the entire hooks block and all five "
              "gates were off for a day, found by accident.")
        if strict and content_problems:
            print()
            print("--strict: FAILING on the repository-content findings above. "
                  "A gate whose baseline entry is missing, stale, or absent must "
                  "not merge — that is the one drift no local hook can catch, "
                  "because a stale branch's baseline overwrites main inside "
                  "GitHub's own merge.")
        print("=" * 70)
        # Bare invocation ALWAYS returns 0: this is a SessionStart hook and it
        # must never wedge a session. Only --strict, which nothing interactive
        # passes, converts a content finding into a failing exit code.
        return 1 if (strict and content_problems) else 0

    # THE BANNER STATES THE RULING, IT DOES NOT NAG FOR A REJECTED OPTION.
    #
    # This line used to read "run ops/harden-gates.sh with sudo to make
    # tampering impossible rather than merely detectable", printed at every
    # single session start. That was a standing recommendation to redo
    # something Joe had already considered, authorized, tested, and then
    # deliberately REVERSED on 2026-08-10, in his own words: "man honestly i
    # disagree with hardening at all. i think the council went overboard on
    # that. youre not going to rewrite hooks anyway without a good reason. if
    # anything, you can just ask me if i approve writing hooks instead of
    # requiring a password." The cost he named was concrete: a buggy gate
    # wrongly blocking legitimate work stays broken until he is available to
    # authenticate.
    #
    # IT COST A REAL ERROR, 2026-08-13. A session read this banner plus
    # harden-gates.sh's "0 hardened · 18 unprotected" as evidence of DRIFT,
    # filed a defect asserting that a completed loop had been closed without
    # its work landing, and put a hot item in front of Joe asking him to run
    # the very thing he had ruled against. Both were wrong. Prompting for a
    # settled decision does not read as a neutral status line; it reads as an
    # unmet obligation, which is exactly the "dated artifact read as present
    # state" failure this system already logs more than any other.
    #
    # So the unhardened count is still REPORTED, because it is true and it is
    # the actual security posture, but it is stated as the decision it is.
    note = ""
    if unhardened:
        note = (f" · OS hardening OFF by Joe's ruling 2026-08-10 "
                f"({len(unhardened)}/{len(GATED)} gates user-writable): tampering is "
                f"DETECTABLE here, not prevented, and that trade was chosen on purpose "
                f"so a buggy gate never needs a password to fix. In-session gate edits "
                f"are approved through gate-edit-gate.py instead. Do NOT propose "
                f"ops/harden-gates.sh as a fix; it is kept only for a deliberate reversal")
    wiring_word = ("installed adapter wiring exact except PENDING INSTALL (in source, not yet "
                   f"installed by config-as-code install; not a failure): {', '.join(pending_install)}"
                   if pending_install else "installed adapter wiring exact")
    print(f"GATE INTEGRITY: {len(base)} gates match baseline; {wiring_word}{note}")
    print(f"CLIENT ADAPTERS: Claude={claude_state}; Codex={codex_state}; "
          f"Claude-project={project_adapter_state}. CARR core is client-independent; "
          "adapter equality does not prove runtime invocation.")
    return 0


def _session_start_payload():
    """The SessionStart hook payload, or None when this is not a hook run.

    Only a bare invocation (no flags) fed a SessionStart JSON payload counts:
    CI's --strict, --bless, --selftest and every script that runs this file by
    hand never arm the rule boot gate and never touch the network. stdin is
    read without blocking, because hand runs inherit whatever stdin they have;
    under hooks/hook-meter-run.py it is an in-memory stream with no fileno.
    """
    if len(sys.argv) > 1 or sys.stdin is None:
        return None
    try:
        if sys.stdin.isatty():
            return None
        try:
            import select
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.2)
            if not ready:
                return None
        except (AttributeError, OSError, ValueError):
            pass
        payload = json.loads(sys.stdin.read() or "null")
    except Exception:
        return None
    if isinstance(payload, dict) and payload.get("hook_event_name") == "SessionStart":
        return payload
    return None


def arm_rule_boot(payload):
    """Arm (or re-arm) hooks/rule-boot-gate.py for this session.

    Runs on every SessionStart source -- startup, resume, clear and compact --
    because each of them starts a context that has not read the rules: a
    compacted context has lost them. lib/rule_boot_gate.py reads page 1 of
    standing-context detail=boot from the store to learn the digest and the
    page count, writes the arm state, and returns the instruction text
    (under the 10k SessionStart cap). The store unreachable is stated, never
    papered over (CLAUDE.md: no local fallback). Never raises.
    """
    try:
        sys.path.insert(0, REPO)
        from lib.rule_boot_gate import arm_session
        return arm_session(payload.get("session_id") or payload.get("sessionId"),
                           payload.get("source"))
    except Exception as exc:
        return f"RULE BOOT: could not arm the rule gate ({exc}); read the rules with standing-context detail=boot before acting."


if __name__ == "__main__":
    hook_payload = _session_start_payload()
    try:
        rc = main()
    except Exception as exc:
        # Fail OPEN and SILENT-ish: a broken attestation must never block a
        # session, but it must not claim everything is fine either.
        print(f"GATE INTEGRITY: check could not run ({exc}) — treat gates as UNVERIFIED.")
        rc = 0
    if hook_payload is not None:
        print(arm_rule_boot(hook_payload))
    sys.exit(rc)
