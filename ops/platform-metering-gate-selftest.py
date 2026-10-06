#!/usr/bin/env python3
"""Executable contract for the permanent platform-cost admission gate."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
import re
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# PRIVATE NAMES, for the reason ops/backup-workflow-status.py now uses them: a
# module-level public import re-exports the metering gate through THIS file, and
# a caller who imports this suite gets the privileged `admitted` outcome back
# from its own policy and request objects. This shape pre-dates the standing
# authority rule; it is closed here rather than left as the next instance of it.
from lib.platform_metering import (  # noqa: E402
    MeteringRefusal as _MeteringRefusal,
    authorize_metered_execution as _authorize_metered_execution,
)


POLICY = json.loads((REPO / "ops/config/platform-metering.v1.json").read_text(encoding="utf-8"))
FAILED: list[str] = []


def _repository_scripts() -> set[str]:
    """Every .py and .sh file the REPOSITORY tracks — not every file on disk.

    This used to walk REPO.rglob("*"), which descends into git worktrees and
    scratch directories that live under the repo root. Worktree-per-session is
    the default working style here, so on 2026-08-21 the check discovered 268
    branch-create implementations against an inventory of 4. All 264 extras
    were copies inside .claude/worktrees/, .codex-worktrees/, .worktrees/ and
    .tmp-* dirs; not one was real. The gate passed on a clean CI runner and
    failed on the developer machine at the identical commit, so its only
    reachable remedy was CARR_SKIP_CI=1 — which is how an always-red local gate
    quietly turns the override into the routine and removes the protection from
    every other class it guards.

    `git ls-files` is the honest source: an untracked copy of a tracked file is
    not a second implementation, and a real new one has to be tracked before it
    can ship. Falls back to a filtered walk when git is unavailable, so the
    check still runs rather than vanishing.
    """
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--", "*.py", "*.sh"],
                             cwd=REPO, capture_output=True, text=True,
                             timeout=30, check=True).stdout
        tracked = {name for name in out.split("\0") if name}
        if tracked:
            return {name for name in tracked
                    if Path(name).name != Path(__file__).name
                    and (REPO / name).is_file()}
    except (OSError, subprocess.SubprocessError):
        pass
    skip = (".git", ".claude", ".codex-worktrees", ".worktrees", "node_modules")
    found = set()
    for path in REPO.rglob("*"):
        if path.suffix not in {".py", ".sh"} or path.name == Path(__file__).name:
            continue
        parts = path.relative_to(REPO).parts
        if any(part in skip or part.startswith(".tmp-") for part in parts[:-1]):
            continue
        found.add(str(path.relative_to(REPO)))
    return found


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def refuses(call: Callable[[], Any]) -> bool:
    try:
        call()
    except _MeteringRefusal:
        return True
    return False


def _registered_mark(name: str) -> str:
    """One registered opaque mark. Opaque on purpose: nothing reads it as an outcome."""
    return "m" + hashlib.sha256(
        f"platform-metering-gate-selftest/v1:{name}".encode("utf-8")
    ).hexdigest()[:15]


# The only three values _gate_mark can ever return. They are digests rather than
# words so that no reader -- and no future caller of this file -- can mistake one
# for the gate's own verdict vocabulary, and so that the privileged-word sweep
# has nothing to find here.
_MARK_REFUSED = _registered_mark("refusal")
_MARK_CONTRACT_HELD = _registered_mark("contract-held")
_MARK_UNREGISTERED = _registered_mark("unregistered-shape")


def _gate_mark(call: Callable[[], Any], gate_key: str) -> str:
    """A registered opaque mark for what THE GATE decided. Module-private.

    An earlier revision of this file exported ``admits(call)``, which handed the
    caller's own object straight back. That is the exported-privileged-outcome
    shape the standing rule forbids: whatever a caller's lambda returned became
    this file's answer. Nothing of the caller's escapes now -- the decision
    object is inspected here and collapsed to one of three module-private marks
    minted above, and only a mark crosses the boundary.

    A check that expects the contract to hold must still be able to report FAIL
    when the gate refuses instead, which is why the refusal is caught rather
    than left to abort the suite with a traceback twenty checks early.
    """
    try:
        decision = call()
    except _MeteringRefusal:
        return _MARK_REFUSED
    if (isinstance(decision, dict) and decision.get("admitted") is True
            and decision.get("gate") == gate_key):
        return _MARK_CONTRACT_HELD
    return _MARK_UNREGISTERED


def main() -> int:
    print("platform-metering-gate-selftest — paid dispatch is admitted before execution\n")

    check("unknown metered dispatch fails closed", refuses(lambda: _authorize_metered_execution(
        POLICY, "unregistered-dispatch", {}, today=date(2026, 8, 17))))

    # The 2026-08-17 Actions pause was cleared on 2026-09-12 (decision
    # 9935743c-21de-4490-83db-d11a5e20f6b1) after it outlived its own
    # ends_after_verified_allowance_reset date and began refusing on a fact that
    # was no longer true.  What this check used to assert -- that the LIVE policy
    # refuses -- is therefore gone on purpose.  What replaces it is the pair that
    # is actually load-bearing: the live policy admits a candidate that satisfies
    # its declared contract, and the temporary-control MACHINERY still refuses,
    # proved against a synthetic policy rather than by leaving a retired control
    # switched on.  Deleting the first assertion without adding the second would
    # have removed all coverage of lib/platform_metering.py's two pause clauses.
    # _gate_mark() rather than a bare call: a re-imposed pause must FAIL this
    # check by name, not crash the process before the twenty later checks run.
    actions_mark = _gate_mark(lambda: _authorize_metered_execution(
        POLICY, "github-actions-remote-ci", {
            "candidate_sha": "a" * 40, "local_checks_green": True,
        }, today=date(2026, 9, 12)), "github-actions-remote-ci")
    check("GitHub Actions clears its own contract once the pause is lifted",
          actions_mark == _MARK_CONTRACT_HELD)
    check("GitHub Actions without proven local checks is still refused", refuses(
        lambda: _authorize_metered_execution(POLICY, "github-actions-remote-ci", {
            "candidate_sha": "a" * 40,
        }, today=date(2026, 9, 12))))
    check("GitHub Actions without an exact candidate SHA is still refused", refuses(
        lambda: _authorize_metered_execution(POLICY, "github-actions-remote-ci", {
            "candidate_sha": "a" * 39, "local_checks_green": True,
        }, today=date(2026, 9, 12))))

    paused = json.loads(json.dumps(POLICY))
    paused["temporary_controls"]["github_actions_pause"]["repository_actions_enabled"] = False
    check("a re-imposed repository disable still refuses", refuses(
        lambda: _authorize_metered_execution(paused, "github-actions-remote-ci", {
            "candidate_sha": "a" * 40, "local_checks_green": True,
        }, today=date(2026, 9, 12))))
    unverified = json.loads(json.dumps(POLICY))
    unverified["temporary_controls"]["github_actions_pause"]["verified_allowance_reset"] = False
    check("an unverified allowance reset still refuses", refuses(
        lambda: _authorize_metered_execution(unverified, "github-actions-remote-ci", {
            "candidate_sha": "a" * 40, "local_checks_green": True,
        }, today=date(2026, 9, 12))))
    check("the cleared pause records the authority that cleared it",
          POLICY["temporary_controls"]["github_actions_pause"].get("cleared_decision_ref")
          == "9935743c-21de-4490-83db-d11a5e20f6b1")

    allowed_neon = _authorize_metered_execution(POLICY, "neon-disposable-branch", {
        "requested_lifetime_minutes": 120,
        "cleanup_registered": True,
        "active_nondefault_branches": 1,
    }, today=date(2026, 8, 17))
    check("one bounded disposable Neon branch is admitted", allowed_neon["admitted"] is True)
    check("Neon branch without same-run cleanup is refused", refuses(
        lambda: _authorize_metered_execution(POLICY, "neon-disposable-branch", {
            "requested_lifetime_minutes": 120,
            "cleanup_registered": False,
            "active_nondefault_branches": 1,
        }, today=date(2026, 8, 17))))
    check("Neon branch over the lifetime cap is refused", refuses(
        lambda: _authorize_metered_execution(POLICY, "neon-disposable-branch", {
            "requested_lifetime_minutes": 121,
            "cleanup_registered": True,
            "active_nondefault_branches": 1,
        }, today=date(2026, 8, 17))))
    check("Neon fanout over the branch cap is refused", refuses(
        lambda: _authorize_metered_execution(POLICY, "neon-disposable-branch", {
            "requested_lifetime_minutes": 60,
            "cleanup_registered": True,
            "active_nondefault_branches": 3,
        }, today=date(2026, 8, 17))))

    check("Cloudflare deploy without local verification is refused", refuses(
        lambda: _authorize_metered_execution(POLICY, "cloudflare-worker-release", {
            "release_preflight_green": False,
            "performance_budget_ref": "release:R-1",
            "release_candidate_count": 1,
        }, today=date(2026, 8, 17))))
    allowed_deploy = _authorize_metered_execution(POLICY, "cloudflare-worker-release", {
        "release_preflight_green": True,
        "performance_budget_ref": "release:R-1",
        "release_candidate_count": 1,
    }, today=date(2026, 8, 17))
    check("one verified Cloudflare release candidate is admitted", allowed_deploy["admitted"] is True)

    gates = POLICY.get("execution_gates", {})
    check("paid cognition delegates to the transactional database cost gate",
          gates.get("cognition-provider", {}).get("installed_enforcement_ref") ==
          "tools/control-plane.py:_reserve->ops.admit_job_cost")

    branch_sources = {
        "ops/p1-integration-gate.py",
        "ops/p1-rebuild-gate.py",
        "ops/cc-update-audit-shadow-harness.py",
        "bin/restore-rehearse.sh",
        "tools/pitr-restore-proof.py",
    }
    # A create through the provider's REST API (POST .../branches) is a branch
    # create exactly as much as a neonctl call is, so it is discovered too.
    api_branch_create = re.compile(r'["\x27]POST["\x27]\s*,\s*f?["\x27]/projects/\{[^}]+\}/branches["\x27]')
    branch_patterns = (
        re.compile(r'["\x27]branches["\x27]\s*,\s*["\x27]create["\x27]'),
        re.compile(r'\$NEONCTL[^\n]*\bbranches\s+create\b'),
        api_branch_create,
    )
    discovered_branch_sources = {
        relative
        for relative in _repository_scripts()
        if any(pattern.search((REPO / relative).read_text(encoding="utf-8",
                                                          errors="ignore"))
               for pattern in branch_patterns)
    }
    check("every Neon branch-create implementation is inventoried",
          discovered_branch_sources == branch_sources)
    for relative in sorted(branch_sources):
        source = (REPO / relative).read_text(encoding="utf-8")
        create_at = source.find("branches create")
        if create_at < 0:
            create_at = source.find('"branches","create"')
        if create_at < 0:
            create_at = source.find('"branches", "create"')
        if create_at < 0:
            api_match = api_branch_create.search(source)
            create_at = api_match.start() if api_match else -1
        authorize_at = source.find("neon-disposable-branch")
        check(f"{relative} admits metered branch work before create",
              authorize_at >= 0 and create_at >= 0 and authorize_at < create_at)

    deploy = (REPO / "bin/deploy-worker.sh").read_text(encoding="utf-8")
    check("Worker release passes the metering gate before Wrangler",
          deploy.find("cloudflare-worker-release") < deploy.find('"$WRANGLER" versions upload'))

    control_plane = (REPO / "tools/control-plane.py").read_text(encoding="utf-8")
    check("provider call remains behind transactional cost admission",
          control_plane.find("ops.admit_job_cost") < control_plane.find("CognitionDispatcher(")
          and "budget admission refusal stops before every provider call" in
          (REPO / "ops/control-plane-runner-selftest.py").read_text(encoding="utf-8"))

    pilot = (REPO / "ops/automerge_pilot.py").read_text(encoding="utf-8")
    execute_start = pilot.find("def command_execute")
    check("automerge admits remote CI before it merges or dispatches",
          execute_start >= 0
          and execute_start < pilot.find('"github-actions-remote-ci"', execute_start)
          < pilot.find("execute_conditional_merge(", execute_start)
          < pilot.find("api.dispatch_workflow(", execute_start))

    print(f"\n{len(FAILED)} failure(s)" if FAILED else "\nall platform metering gate checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
