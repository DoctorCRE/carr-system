#!/usr/bin/env python3
"""Check native safety hooks and their SHA-256 baseline.

Re-bless intentional changes with the existing scoped procedure:
    python3 hooks/gate-integrity.py --bless <gate> [<gate>...]
An unscoped --bless rebuilds the inventory only from a clean gate tree.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parent.parent
REQUIRED = {
    'claude': {'guard-unattended.py', 'record-home-gate.py', 'bash-write-gate.py',
               'write-effect-check.py', 'draft-export-gate.py',
               'weekend-quiet-gate.py', 'costar-lane-gate.py'},
    'codex': {'guard-unattended.py', 'record-home-gate.py'},
}

HOOKS = REPO / "hooks"
BASELINE = REPO / "ops/config/gate-baseline.json"
CONTRACTS = {
    "hook_runtime.py": REPO / "lib/hook_runtime.py",
    "delegation-gate-hook.json": REPO / "ops/config/delegation-gate-hook.json",
    "hooks.json": REPO / "ops/config/hooks.json",
    "codex-hooks.json": REPO / "ops/config/codex-hooks.json",
    "rule-enforcement-map.json": REPO / "ops/config/rule-enforcement-map.json",
    "model-floors.json": REPO / "ops/config/model-floors.json",
    "session-context-lifecycle.v2.json": REPO / "ops/config/session-context-lifecycle.v2.json",
}

FLOOR = [
    "guard-unattended.py",      # egress deny
    "record-home-gate.py",      # .md write deny
    "lint-gate.py",             # writing lint
    "context-handoff-gate.py",  # Stop context-threshold handoff
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


def check_baseline():
    """Compare tree-local gates and retained contracts to their blessed hashes."""
    if not os.path.exists(BASELINE):
        return ["GATE INTEGRITY: no baseline; gate tampering is UNDETECTED."]
    problems = []
    with open(BASELINE) as fh:
        baseline_data = json.load(fh)
    base = baseline_data.get("hashes", {})
    now = current()
    base_contracts = baseline_data.get("contracts", {})
    now_contracts = current_contracts()

    for name, want in base.items():
        got = now.get(name)
        if got is None:
            problems.append(f"MISSING: hooks/{name} is GONE — that gate is off")
        elif not isinstance(want, str) or not re.fullmatch(r"[0-9a-f]{64}", want):
            problems.append(f"INVALID: hooks/{name} has no valid SHA-256 baseline")
        elif got != want:
            problems.append(f"CHANGED: hooks/{name} no longer matches the blessed baseline")
    for name, got in now.items():
        if name not in base and got:
            problems.append(f"UNBLESSED: hooks/{name} exists but is not in the baseline")
    for name, want in base_contracts.items():
        got = now_contracts.get(name)
        if got is None:
            problems.append(f"MISSING: ops/config/{name} is GONE — its wiring contract is off")
        elif not isinstance(want, str) or not re.fullmatch(r"[0-9a-f]{64}", want):
            problems.append(f"INVALID: ops/config/{name} has no valid SHA-256 baseline")
        elif got != want:
            problems.append(f"CHANGED: ops/config/{name} no longer matches the blessed baseline")
    for name, got in now_contracts.items():
        if name not in base_contracts and got:
            problems.append(f"UNBLESSED: ops/config/{name} exists but is not in the baseline")

    return problems


def check(document, client, repo=REPO):
    hooks = document.get('hooks', document)
    installed = set()
    errors = []
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get('hooks', []):
                for path in re.findall(r'(?:\{\{REPO\}\}|[^\s\"\']+)/(?:hooks|ops)/[^\s\"\']+\.py', hook.get('command', '')):
                    path = Path(path.replace('{{REPO}}', str(repo)))
                    if not path.is_file():
                        errors.append(f'missing hook script: {path}')
                    if event == "PreToolUse":
                        installed.add(path.name)
    errors.extend(f'{client}: required safety hook is not installed: {name}'
                  for name in sorted(REQUIRED[client] - installed))
    return errors


def main():
    if '--bless' in sys.argv:
        rest = sys.argv[sys.argv.index('--bless') + 1:]
        names = [os.path.basename(a) for a in rest if not a.startswith('-')]
        return bless(names or None)
    strict = '--strict' in sys.argv
    home = Path.home()
    sources = {
        'claude': REPO / 'ops/config/hooks.json' if strict else home / '.claude/settings.json',
        'codex': REPO / 'ops/config/codex-hooks.json' if strict else home / '.codex/hooks.json',
    }
    errors = check_baseline()
    for client, path in sources.items():
        if not strict and not path.exists():
            continue
        try:
            errors.extend(check(json.loads(path.read_text()), client))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append(f'{path}: {exc}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'GATE INTEGRITY: {len(current())} gates match baseline; required safety hooks are installed; every configured script exists.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
