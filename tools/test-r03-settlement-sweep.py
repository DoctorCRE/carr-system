#!/usr/bin/env python3
"""Disposable-fixture acceptance tests for r03-settlement-sweep-runner.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
OPS = ROOT / "ops"
sys.path.insert(0, str(OPS))
from git_env import fixture_env


ENV = fixture_env()
RUNNER_PATH = ROOT / "tools" / "r03-settlement-sweep-runner.py"
SPEC = importlib.util.spec_from_file_location("r03_settlement_sweep_runner", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load R03 settlement runner")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)

RESTORE = sys.modules["r03_settlement_restore_set"]


def checked(argv: list[str], cwd: Path) -> str:
    result = subprocess.run(argv, cwd=str(cwd), env=ENV, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise AssertionError(f"command failed: {' '.join(argv)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def git(repository: Path, *args: str) -> str:
    return checked(["git", *args], repository)


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "origin.git"
        self.repository = root / "work"
        self.root.mkdir(parents=True)
        checked(["git", "init", "--bare", str(self.remote)], root)
        self.repository.mkdir()
        git(self.repository, "init", "-b", "main")
        git(self.repository, "config", "user.email", "fixture@example.test")
        git(self.repository, "config", "user.name", "R03 fixture")
        (self.repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        git(self.repository, "add", "tracked.txt")
        git(self.repository, "commit", "-m", "fixture base")
        git(self.repository, "remote", "add", "origin", str(self.remote))
        git(self.repository, "push", "-u", "origin", "main")
        git(self.repository, "fetch", "origin", "main")

    @property
    def pin(self) -> str:
        return git(self.repository, "rev-parse", "refs/remotes/origin/main").strip()

    def manifest(self, *, clean_pathspecs: list[str], clean_expected: list[str], branches: list[dict] | None = None,
                 branch_count: int | None = None) -> dict:
        return {
            "schema_version": RUNNER.MANIFEST_SCHEMA,
            "run_id": "fixture-r03-stage5",
            "approved": True,
            "pinned_origin_main": self.pin,
            "preconditions": {
                "capability_denial_tests_passed": True,
                "fresh_verified_production_backup": True,
            },
            "never_cleanable": ["never-cleanable"],
            "clean": {"pathspecs": clean_pathspecs, "expected": clean_expected},
            "restore": [],
            "park": {"paths": [], "archive": None},
            "branches": branches or [],
            "closing": {
                "expected_head": self.pin,
                "expected_branch_count": branch_count if branch_count is not None else 1 + len(branches or []),
            },
        }

    def allowlist(self, manifest: dict, *, execute: bool) -> dict:
        paths = manifest["clean"]["pathspecs"]
        commands = [{
            "id": "stage5.clean.dry",
            "argv": ["git", "clean", "-nd", "--", *paths],
            "pathspecs": paths,
        }]
        if execute:
            commands.append({
                "id": "stage5.clean.execute",
                "argv": ["git", "clean", "-fd", "--", *paths],
                "pathspecs": paths,
            })
        # Stage 3 pushes every backed-up branch tip to the remote in one atomic
        # command, so the allowlist has to carry it whenever any branch declares
        # a backup ref. Mirrors what the real manifest authoring emits.
        refspecs = [f"refs/heads/{b['name']}:{b['tip_backup_ref']}"
                    for b in manifest["branches"] if b["tip_backup_ref"]]
        if refspecs:
            commands.append({
                "id": "stage3.branch-backup.push",
                "argv": ["git", "push", "--atomic", "origin", *refspecs], "pathspecs": [],
            })
        for branch in manifest["branches"]:
            if branch["classification"] == "ancestry_merged" and branch["tip_backup_ref"] is not None:
                commands.append({
                    "id": f"stage5.branch.safe.{branch['name']}",
                    "argv": ["git", "branch", "-d", branch["name"]], "pathspecs": [],
                })
            if branch["classification"] == "squash_merged" and branch["tip_backup_ref"] is not None:
                commands.append({
                    "id": f"stage5.branch.squash.{branch['name']}",
                    "argv": ["git", "branch", "-D", branch["name"]], "pathspecs": [],
                })
        return {
            "schema_version": RUNNER.ALLOWLIST_SCHEMA,
            "runner_argv": [str(RUNNER_PATH.resolve()), "--manifest-fd={manifest_fd}",
                            "--allowlist-fd={allowlist_fd}", "--capability-receipt-fd={capability_receipt_fd}"],
            "commands": commands,
        }


def fd_for(value: dict) -> int:
    read_fd, write_fd = os.pipe()
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    os.write(write_fd, body)
    os.close(write_fd)
    return read_fd


def invoke(fixture: Fixture, manifest: dict, *, execute: bool, before_disposal=None) -> str:
    allowlist = fixture.allowlist(manifest, execute=execute)
    manifest_body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt = {
        "schema_version": RUNNER.RECEIPT_SCHEMA,
        "capability_key": RUNNER.CAPABILITY_KEY,
        "token_digest": "fixture-token",
        "operator_binding_digest": "fixture-operator",
        "approved_manifest_digest": RUNNER._sha256(manifest_body),
        "repository_identity_digest": "fixture-repository",
        "starting_object_id": fixture.pin,
        "allowlist_digest": "fixture-allowlist",
        "consumed_at_ns": 1,
    }
    fds = [fd_for(manifest), fd_for(allowlist), fd_for(receipt)]
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            RUNNER.run_settlement(
                repository=fixture.repository, manifest_fd=fds[0], allowlist_fd=fds[1],
                capability_receipt_fd=fds[2], execute=execute, before_disposal=before_disposal,
            )
    finally:
        for descriptor in fds:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return stdout.getvalue()


def test_dry_run_touches_nothing(root: Path) -> None:
    fixture = Fixture(root / "dry-run")
    candidate = fixture.repository / "scratch" / "remove-me.txt"
    candidate.parent.mkdir()
    candidate.write_text("fixture debris\n", encoding="utf-8")
    before_status = git(fixture.repository, "status", "--porcelain=v1")
    before_head = git(fixture.repository, "rev-parse", "HEAD")
    output = invoke(fixture, fixture.manifest(clean_pathspecs=["scratch"], clean_expected=["scratch"]), execute=False)
    assert candidate.exists(), "dry-run removed a fixture file"
    assert git(fixture.repository, "status", "--porcelain=v1") == before_status, "dry-run changed fixture status"
    assert git(fixture.repository, "rev-parse", "HEAD") == before_head, "dry-run changed fixture HEAD"
    assert "DRY-RUN:" in output and "stage 5 clean diff: ['scratch']" in output
    print("PASS dry_run_touches_nothing")


def test_never_cleanable_candidate_aborts(root: Path) -> None:
    fixture = Fixture(root / "never-cleanable")
    candidate = fixture.repository / "never-cleanable" / "do-not-remove.txt"
    candidate.parent.mkdir()
    candidate.write_text("protected\n", encoding="utf-8")
    try:
        invoke(fixture, fixture.manifest(clean_pathspecs=["never-cleanable"], clean_expected=["never-cleanable"]), execute=False)
    except RUNNER.SweepError as exc:
        assert "never-cleanable" in str(exc)
    else:
        raise AssertionError("never-cleanable clean candidate did not abort")
    assert candidate.exists(), "abort path changed protected fixture content"
    print("PASS never_cleanable_candidate_aborts")


def test_midrun_tree_change_aborts(root: Path) -> None:
    fixture = Fixture(root / "midrun-change")
    candidate = fixture.repository / "scratch" / "remove-me.txt"
    candidate.parent.mkdir()
    candidate.write_text("fixture debris\n", encoding="utf-8")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=["scratch"])
    def seed_write() -> None:
        (fixture.repository / "seeded-midrun-change.txt").write_text("changed after fingerprint\n", encoding="utf-8")
    try:
        invoke(fixture, manifest, execute=True, before_disposal=seed_write)
    except RUNNER.SweepError as exc:
        assert "fingerprint changed" in str(exc)
    else:
        raise AssertionError("mid-run tree change did not abort")
    assert candidate.exists(), "fingerprint abort reached git clean"
    assert (fixture.repository / "seeded-midrun-change.txt").exists()
    print("PASS midrun_tree_change_aborts")


def test_branch_law_retains_unmerged_and_unbacked_squash(root: Path) -> None:
    fixture = Fixture(root / "branch-law")
    git(fixture.repository, "switch", "-c", "unmerged-without-pr")
    (fixture.repository / "unmerged.txt").write_text("unmerged\n", encoding="utf-8")
    git(fixture.repository, "add", "unmerged.txt")
    git(fixture.repository, "commit", "-m", "unmerged fixture")
    unmerged_tip = git(fixture.repository, "rev-parse", "HEAD").strip()
    git(fixture.repository, "switch", "main")
    git(fixture.repository, "switch", "-c", "squash-without-backup")
    (fixture.repository / "squash.txt").write_text("squash\n", encoding="utf-8")
    git(fixture.repository, "add", "squash.txt")
    git(fixture.repository, "commit", "-m", "squash fixture")
    squash_tip = git(fixture.repository, "rev-parse", "HEAD").strip()
    git(fixture.repository, "switch", "main")
    branches: list[dict] = [
        {"name": "unmerged-without-pr", "tip": unmerged_tip, "classification": "unmerged_without_pr",
         "tip_backup_ref": None, "host_confirmation": None},
        {"name": "squash-without-backup", "tip": squash_tip, "classification": "squash_merged",
         "tip_backup_ref": None,
         "host_confirmation": {"provider": "github", "state": "MERGED", "base_ref": "main", "head_oid": squash_tip,
                               "evidence_id": "fixture-pr-1"}},
    ]
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[], branches=branches, branch_count=3)
    output = invoke(fixture, manifest, execute=True)
    assert git(fixture.repository, "show-ref", "--verify", "--quiet", "refs/heads/unmerged-without-pr") == ""
    assert git(fixture.repository, "show-ref", "--verify", "--quiet", "refs/heads/squash-without-backup") == ""
    assert "retained unmerged-without-PR branch" in output
    assert "retained branch lacking tip backup ref" in output
    print("PASS branch_law_retains_unmerged_and_unbacked_squash")



# ── RESTORE-SET DERIVATION ───────────────────────────────────────────────────
# These cases exercise the authoring module and the versioned runner together.


def restore_repo(root: Path, name: str) -> Path:
    """A throwaway repository with one nested tracked file and one top-level."""
    repository = root / name
    repository.mkdir(parents=True)
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "fixture@example.test")
    git(repository, "config", "user.name", "R03 fixture")
    (repository / "hooks").mkdir()
    (repository / "hooks" / "worktree-self-plumb.py").write_text("original\n", encoding="utf-8")
    (repository / "keep.txt").write_text("keep\n", encoding="utf-8")
    git(repository, "add", "hooks", "keep.txt")
    git(repository, "commit", "-m", "restore fixture base")
    return repository


def pin_of(repository: Path) -> str:
    return git(repository, "rev-parse", "HEAD").strip()


def test_restore_clean_tree_yields_empty_set(root: Path) -> None:
    repository = restore_repo(root, "restore-clean")
    assert RESTORE.build_restore_set(repository, pin_of(repository), []) == []


def test_restore_unapproved_dirty_path_refuses_and_names_it(root: Path) -> None:
    repository = restore_repo(root, "restore-refuse")
    (repository / "hooks" / "worktree-self-plumb.py").write_text("someone else's work\n", encoding="utf-8")
    try:
        result = RESTORE.build_restore_set(repository, pin_of(repository), [])
    except RESTORE.RestoreSetRefusal as refusal:
        assert "hooks/worktree-self-plumb.py" in str(refusal), str(refusal)
        return
    raise AssertionError(f"unapproved dirty path was enrolled instead of refused: {result}")


def test_restore_approved_dirty_path_carries_its_pinned_blob(root: Path) -> None:
    repository = restore_repo(root, "restore-approved")
    path = "hooks/worktree-self-plumb.py"
    pin = pin_of(repository)
    expected = git(repository, "rev-parse", f"{pin}:{path}").strip()
    (repository / path).write_text("changed\n", encoding="utf-8")
    entry = RESTORE.build_restore_set(repository, pin, [path])[0]
    assert entry["path"] == path and entry["blob_oid"] == expected
    assert entry["observed_state"].startswith("sha256:")


def test_restore_first_dirty_path_is_not_truncated(root: Path) -> None:
    """Regression for the shipped "ooks/worktree-self-plumb.py".

    Exactly ONE dirty file, so it is the FIRST porcelain line -- the only line
    a whole-output .strip() corrupts, by eating its leading status space.
    """
    repository = restore_repo(root, "restore-truncation")
    (repository / "hooks" / "worktree-self-plumb.py").write_text("changed\n", encoding="utf-8")
    found = RESTORE.dirty_paths(repository)
    assert found == ["hooks/worktree-self-plumb.py"], found
    for path in found:
        assert (repository / path).exists(), f"derived path {path!r} does not exist -- it was truncated"


def test_restore_path_containing_a_space_survives(root: Path) -> None:
    repository = restore_repo(root, "restore-spaced")
    noisy = "hooks/two words.py"
    (repository / noisy).write_text("x\n", encoding="utf-8")
    git(repository, "add", noisy)
    git(repository, "commit", "-m", "add spaced path")
    pin = pin_of(repository)
    (repository / noisy).write_text("y\n", encoding="utf-8")
    assert noisy in RESTORE.dirty_paths(repository)
    assert RESTORE.build_restore_set(repository, pin, [noisy])[0]["path"] == noisy


def test_restore_untracked_file_neither_refuses_nor_enrols(root: Path) -> None:
    repository = restore_repo(root, "restore-untracked")
    (repository / "scratch.tmp").write_text("junk\n", encoding="utf-8")
    assert RESTORE.build_restore_set(repository, pin_of(repository), []) == []


def test_restore_staged_deletion_refuses(root: Path) -> None:
    repository = restore_repo(root, "restore-deletion")
    pin = pin_of(repository)
    git(repository, "rm", "-q", "keep.txt")
    try:
        RESTORE.build_restore_set(repository, pin, [])
    except RESTORE.RestoreSetRefusal as refusal:
        assert "keep.txt" in str(refusal), str(refusal)
        return
    raise AssertionError("staged deletion did not refuse")


def test_restore_stale_allowlist_invents_nothing(root: Path) -> None:
    repository = restore_repo(root, "restore-stale-allow")
    assert RESTORE.build_restore_set(
        repository, pin_of(repository), ["hooks/worktree-self-plumb.py"]) == []

def _advance_origin_main(fixture: Fixture) -> str:
    """Land a new commit on origin/main while the checkout stays where it was.

    This is the ordinary state of a repository other sessions merge into, and the
    case a manifest must survive rather than expire on.
    """
    stay = git(fixture.repository, "rev-parse", "HEAD").strip()
    (fixture.repository / "landed-elsewhere.txt").write_text("another session's merge\n", encoding="utf-8")
    git(fixture.repository, "add", "landed-elsewhere.txt")
    git(fixture.repository, "commit", "-m", "unrelated PR landing on main")
    git(fixture.repository, "push", "origin", "main")
    git(fixture.repository, "reset", "--hard", stay)
    git(fixture.repository, "fetch", "origin", "main")
    return stay


def test_freshness_accepts_advanced_origin_main(root: Path) -> None:
    """origin/main moving forward must NOT expire an otherwise-valid manifest."""
    fixture = Fixture(root / "freshness-advance")
    pin = fixture.pin
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[])
    stayed = _advance_origin_main(fixture)
    assert stayed == pin and fixture.pin != pin, "fixture did not advance origin/main past the pin"
    output = invoke(fixture, manifest, execute=True)
    assert "origin/main advanced" in output, output
    assert "STAGE 6 closing readback passed" in output, output
    print("PASS freshness_accepts_advanced_origin_main")


def test_freshness_refuses_rewound_origin_main(root: Path) -> None:
    """A pin that origin/main can no longer reach invalidates every ancestry claim."""
    fixture = Fixture(root / "freshness-rewind")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[])
    git(fixture.repository, "checkout", "--orphan", "rewritten")
    (fixture.repository / "rewritten.txt").write_text("rewritten history\n", encoding="utf-8")
    git(fixture.repository, "add", "rewritten.txt")
    git(fixture.repository, "commit", "-m", "rewritten history")
    git(fixture.repository, "push", "--force", "origin", "rewritten:main")
    git(fixture.repository, "checkout", "main")
    git(fixture.repository, "fetch", "origin", "main")
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepError as exc:
        assert "does not descend from manifest pin" in str(exc), str(exc)
    else:
        raise AssertionError("a rewound origin/main did not abort the settlement")
    print("PASS freshness_refuses_rewound_origin_main")


def test_precondition_refuses_stale_head(root: Path) -> None:
    """A checkout behind the pin can never satisfy stage 6, so it is refused up front."""
    fixture = Fixture(root / "stale-head")
    debris = fixture.repository / "scratch" / "remove-me.txt"
    debris.parent.mkdir()
    debris.write_text("fixture debris\n", encoding="utf-8")
    _advance_origin_main(fixture)
    # manifest pins the NEW origin/main while the checkout still sits on the old commit
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=["scratch"])
    assert manifest["pinned_origin_main"] != git(fixture.repository, "rev-parse", "HEAD").strip()
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepHeld as exc:
        assert "is not the settled pin" in str(exc), str(exc)
    else:
        raise AssertionError("a stale checkout was not refused before destructive work")
    assert debris.exists(), "refused run still reached git clean"
    # and the dry-run must SAY so rather than implying the run would succeed
    output = invoke(fixture, manifest, execute=False)
    assert "PRECONDITION NOT MET" in output, output
    print("PASS precondition_refuses_stale_head")


def test_closing_detects_collateral_branch_loss(root: Path) -> None:
    """A branch this settlement never declared must not disappear during it."""
    fixture = Fixture(root / "collateral-loss")
    git(fixture.repository, "branch", "bystander")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[], branch_count=2)
    def drop_bystander() -> None:
        git(fixture.repository, "branch", "-D", "bystander")
    try:
        invoke(fixture, manifest, execute=True, before_disposal=drop_bystander)
    except RUNNER.SweepError as exc:
        assert "vanished that this settlement never deleted" in str(exc), str(exc)
        assert "bystander" in str(exc), str(exc)
    else:
        raise AssertionError("collateral branch loss was not detected by the closing readback")
    print("PASS closing_detects_collateral_branch_loss")


def test_closing_detects_undeleted_branch(root: Path) -> None:
    """A branch the runner believes it deleted must not still exist.

    End-to-end this cannot be staged -- if the delete ran, the branch is gone --
    so the closing assertion is exercised directly rather than shipped unproven.
    """
    fixture = Fixture(root / "undeleted-branch")
    git(fixture.repository, "branch", "still-here")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[], branch_count=2)
    parsed = RUNNER.validate_manifest(manifest)
    try:
        RUNNER._stage6_readback(
            fixture.repository, manifest, parsed,
            starting_branches={"main", "still-here"}, deleted={"still-here"},
        )
    except RUNNER.SweepError as exc:
        assert "deleted branches still present" in str(exc), str(exc)
        assert "still-here" in str(exc), str(exc)
    else:
        raise AssertionError("closing readback accepted a branch that was never actually deleted")
    print("PASS closing_detects_undeleted_branch")


def test_canonical_execution_has_no_opt_in(root: Path) -> None:
    fixture = Fixture(root / "canonical-refusal")
    assert "authorized_production_canonical" not in inspect.signature(RUNNER.run_settlement).parameters
    help_text = checked([sys.executable, str(RUNNER_PATH), "--help"], ROOT)
    assert "--authorized-production-canonical-sweep" not in help_text
    previous = RUNNER.CANONICAL_CHECKOUT
    try:
        for canonical in (fixture.repository, fixture.root):
            setattr(RUNNER, "CANONICAL_CHECKOUT", canonical)
            try:
                invoke(fixture, fixture.manifest(clean_pathspecs=[], clean_expected=[]), execute=True)
            except RUNNER.SweepError as exc:
                assert "disposable fixtures only" in str(exc), str(exc)
            else:
                raise AssertionError("canonical execution did not refuse")
    finally:
        setattr(RUNNER, "CANONICAL_CHECKOUT", previous)
    assert not git(fixture.repository, "for-each-ref", "refs/backup")
    print("PASS canonical_execution_has_no_opt_in")


def test_empty_operations_preserve_closing_contract(root: Path) -> None:
    fixture = Fixture(root / "empty-operations-contract")
    keep = fixture.repository / "keep.txt"
    keep.write_text("untracked fixture content\n", encoding="utf-8")
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepError as exc:
        assert "remaining tracked/untracked dirt" in str(exc), str(exc)
    else:
        raise AssertionError("empty operations waived the manifest clean-tree contract")
    assert keep.exists()
    _advance_origin_main(fixture)
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    dry = invoke(fixture, manifest, execute=False)
    assert "PRECONDITION NOT MET" in dry, dry
    assert "PRECONDITION OK" not in dry, dry
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepHeld as exc:
        assert "is not the settled pin" in str(exc), str(exc)
    else:
        raise AssertionError("empty operations admitted a stale HEAD")
    print("PASS empty_operations_preserve_closing_contract")


def test_empty_operations_racing_write_aborts(root: Path) -> None:
    fixture = Fixture(root / "empty-operations-race")
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    def write_unrelated() -> None:
        (fixture.repository / "concurrent.txt").write_text("concurrent fixture write\n", encoding="utf-8")
    try:
        invoke(fixture, manifest, execute=True, before_disposal=write_unrelated)
    except RUNNER.SweepError as exc:
        assert "fingerprint changed" in str(exc), str(exc)
    else:
        raise AssertionError("concurrent write escaped the fixture fingerprint contract")
    # The same dirt present at closing must also fail, independent of file operations.
    parsed = RUNNER.validate_manifest(manifest)
    try:
        RUNNER._stage6_readback(fixture.repository, manifest, parsed, {"main"}, set())
    except RUNNER.SweepError as exc:
        assert "remaining tracked/untracked dirt" in str(exc), str(exc)
    else:
        raise AssertionError("closing waived concurrent dirt for empty operations")
    assert (fixture.repository / "concurrent.txt").exists()
    print("PASS empty_operations_racing_write_aborts")


def test_stale_head_refuses_new_pin_branch_before_backup(root: Path) -> None:
    fixture = Fixture(root / "stale-head-new-branch")
    _advance_origin_main(fixture)
    git(fixture.repository, "branch", "merged-at-new-pin", fixture.pin)
    backup = "refs/backup/fixture-r03-stage5/branch/merged-at-new-pin"
    branch = {"name": "merged-at-new-pin", "tip": fixture.pin, "classification": "ancestry_merged",
              "tip_backup_ref": backup, "host_confirmation": None}
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[], branches=[branch])
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepHeld as exc:
        assert "is not the settled pin" in str(exc), str(exc)
    else:
        raise AssertionError("stale HEAD reached branch deletion instead of admission refusal")
    assert git(fixture.repository, "rev-parse", "refs/heads/merged-at-new-pin").strip() == fixture.pin
    assert not git(fixture.repository, "for-each-ref", "refs/backup")
    assert not git(fixture.repository, "ls-remote", "--refs", "origin", backup)
    print("PASS stale_head_refuses_new_pin_branch_before_backup")


def test_branch_identity_survives_concurrent_tag_collision(root: Path) -> None:
    fixture = Fixture(root / "retained-tag-collision")
    git(fixture.repository, "branch", "bystander")
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[], branch_count=2)
    def add_tag() -> None:
        git(fixture.repository, "tag", "bystander")
    output = invoke(fixture, manifest, execute=True, before_disposal=add_tag)
    assert "closing readback passed" in output, output
    assert RUNNER._branch_set(fixture.repository) == {"main", "bystander"}
    print("PASS branch_identity_survives_concurrent_tag_collision")


def test_declared_branch_identity_survives_existing_tag_collision(root: Path) -> None:
    fixture = Fixture(root / "deleted-tag-collision")
    git(fixture.repository, "branch", "merged")
    git(fixture.repository, "tag", "merged")
    backup = "refs/backup/fixture-r03-stage5/branch/merged"
    branch = {"name": "merged", "tip": fixture.pin, "classification": "ancestry_merged",
              "tip_backup_ref": backup, "host_confirmation": None}
    output = invoke(fixture, fixture.manifest(clean_pathspecs=[], clean_expected=[],
                                            branches=[branch], branch_count=1), execute=True)
    assert "closing readback passed" in output, output
    assert RUNNER._branch_set(fixture.repository) == {"main"}
    assert git(fixture.repository, "rev-parse", "refs/tags/merged").strip() == fixture.pin
    print("PASS declared_branch_identity_survives_existing_tag_collision")


def test_empty_clean_set_never_cleans_whole_tree(root: Path) -> None:
    """An empty pathspec list means clean nothing; `git clean -fd --` means clean everything."""
    fixture = Fixture(root / "empty-clean")
    keep = fixture.repository / "keep-me.txt"
    keep.write_text("untracked but not condemned\n", encoding="utf-8")
    nested = fixture.repository / "nested" / "deep.txt"
    nested.parent.mkdir()
    nested.write_text("also not condemned\n", encoding="utf-8")
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[], branch_count=1)
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepError as exc:
        assert "remaining tracked/untracked dirt" in str(exc), str(exc)
    else:
        raise AssertionError("untracked dirt was accepted by the closing readback")
    assert keep.exists(), "empty clean set widened into deleting untracked files"
    assert nested.exists(), "empty clean set widened into a recursive tree clean"
    print("PASS empty_clean_set_never_cleans_whole_tree")



def test_review_poisoned_environment(root: Path) -> None:
    repository = restore_repo(root, "poison-target")
    other = restore_repo(root, "poison-decoy")
    (repository / "keep.txt").write_text("unapproved edit\n")
    with patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
                                 "GIT_INDEX_FILE": str(other / ".git/index")}):
        try:
            RESTORE.build_restore_set(repository, pin_of(repository), [])
        except RESTORE.RestoreSetRefusal:
            return
    raise AssertionError("poisoned environment hid the target's dirty path")


def test_review_authoring_to_execution_drift(root: Path) -> None:
    for index_only in (False, True):
        fixture = Fixture(root / f"approved-state-{index_only}")
        tracked = fixture.repository / "tracked.txt"
        tracked.write_text("approved edit\n")
        manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[])
        manifest["restore"] = RESTORE.build_restore_set(fixture.repository, fixture.pin, ["tracked.txt"])
        tracked.write_text("later edit\n")
        if index_only:
            git(fixture.repository, "add", "tracked.txt")
            tracked.write_text("approved edit\n")
        before = tracked.read_bytes()
        try:
            invoke(fixture, manifest, execute=True)
        except RUNNER.SweepError as exc:
            assert "observed state" in str(exc), str(exc)
        else:
            raise AssertionError("authoring authorization survived a later worktree/index edit")
        assert tracked.read_bytes() == before


def test_review_literal_restore_scope(root: Path) -> None:
    repository = restore_repo(root, "literal-pathspec")
    for name in ("*.txt", ":(glob)*.txt"):
        (repository / name).write_text("pinned\n")
    git(repository, "add", "--", ":(literal)*.txt", ":(literal):(glob)*.txt")
    git(repository, "commit", "-m", "literal names")
    pin = pin_of(repository)
    (repository / "*.txt").write_text("approved\n")
    entries = RESTORE.build_restore_set(repository, pin, ["*.txt"])
    assert [entry["path"] for entry in entries] == ["*.txt"]
    (repository / ":(glob)*.txt").write_text("approved magic\n")
    entries = RESTORE.build_restore_set(repository, pin, ["*.txt", ":(glob)*.txt"])
    assert {entry["path"] for entry in entries} == {"*.txt", ":(glob)*.txt"}


def test_review_filename_bytes(root: Path) -> None:
    repository = restore_repo(root, "filename-bytes")
    names = ["hooks/x\ry.py", "hooks/x\r\ny.py", "hooks/x\ny.py"]
    for name in names:
        (repository / name).write_bytes(b"pinned\n")
        git(repository, "add", "--", name)
    git(repository, "commit", "-m", "filename bytes")
    pin = pin_of(repository)
    for name in names:
        (repository / name).write_bytes(b"edit\n")
        assert RESTORE.dirty_paths(repository) == [name], repr(RESTORE.dirty_paths(repository))
        assert RESTORE.build_restore_set(repository, pin, [name])[0]["path"] == name
        try:
            RESTORE.build_restore_set(repository, pin, [names[(names.index(name) + 1) % len(names)]])
        except RESTORE.RestoreSetRefusal:
            pass
        else:
            raise AssertionError("filename alias bypassed approval")
        (repository / name).write_bytes(b"pinned\n")

    # APFS rejects invalid UTF-8 filenames. Exercise the raw Git output seam
    # here as well, so the same lossless parser is covered on every platform.
    raw = b" M hooks/non-utf8-\xff.py\0"
    with patch.object(RESTORE.subprocess, "run", return_value=subprocess.CompletedProcess(["git"], 0, raw, b"")):
        assert RESTORE.dirty_paths(repository) == [os.fsdecode(b"hooks/non-utf8-\xff.py")]


def test_review_rename_refusal(root: Path) -> None:
    for existing_destination in (True, False):
        repository = restore_repo(root, f"rename-{existing_destination}")
        if existing_destination:
            (repository / "renamed.txt").write_text("keep\n")
            git(repository, "add", "renamed.txt")
            git(repository, "commit", "-m", "pinned destination")
        pin = pin_of(repository)
        if existing_destination:
            git(repository, "rm", "renamed.txt")
            git(repository, "commit", "-m", "remove destination")
        git(repository, "mv", "keep.txt", "renamed.txt")
        for allowed in (["renamed.txt"], ["keep.txt", "renamed.txt"]):
            try:
                RESTORE.build_restore_set(repository, pin, allowed)
            except RESTORE.RestoreSetRefusal as exc:
                assert "rename" in str(exc) or "copy" in str(exc), str(exc)
            else:
                raise AssertionError("rename silently dropped its source deletion")


def test_review_versioned_authoring_route(root: Path) -> None:
    fixture = Fixture(root / "author-route")
    (fixture.repository / "tracked.txt").write_text("approved edit\n")
    template = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    template["approved"] = False
    template_path = root / "template.json"
    template_path.write_text(json.dumps(template))
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        result = RUNNER.main(["--repository", str(fixture.repository), "--author-template", str(template_path),
                              "--approve-restore-path", "tracked.txt"])
    assert result == 0
    manifest = json.loads(stdout.getvalue())
    assert manifest["approved"] is False, "authoring may not grant admission"
    assert "observed_state" in manifest["restore"][0]
    manifest["approved"] = True  # simulated operator admission of these exact bytes
    output = invoke(fixture, manifest, execute=False)
    assert "writer exclusion" in output and "DRY-RUN:" in output
    assert (fixture.repository / "tracked.txt").read_text() == "approved edit\n"
    allowlist = fixture.allowlist(manifest, execute=True)
    receipt = {
        "schema_version": RUNNER.RECEIPT_SCHEMA, "capability_key": RUNNER.CAPABILITY_KEY,
        "token_digest": "fixture-token", "operator_binding_digest": "fixture-operator",
        "approved_manifest_digest": RUNNER._sha256(json.dumps(
            manifest, sort_keys=True, separators=(",", ":")).encode()),
        "repository_identity_digest": "fixture-repository", "starting_object_id": fixture.pin,
        "allowlist_digest": "fixture-allowlist", "consumed_at_ns": 1,
    }
    fds = [fd_for(value) for value in (manifest, allowlist, receipt)]
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            assert RUNNER.main(["--repository", str(fixture.repository), "--execute",
                                "--manifest-fd", str(fds[0]), "--allowlist-fd", str(fds[1]),
                                "--capability-receipt-fd", str(fds[2])]) == 75
    finally:
        for fd in fds:
            os.close(fd)
    assert "HELD:" in stdout.getvalue() and "writer exclusion" in stdout.getvalue()
    assert "STAGE 3" not in stdout.getvalue() and "closing readback passed" not in stdout.getvalue()
    assert (fixture.repository / "tracked.txt").read_text() == "approved edit\n"
    # A manually prebuilt old entry cannot bypass the same admission predicate.
    (fixture.repository / "tracked.txt").write_text("unapproved edit\n")
    manifest["restore"] = [{"path": "tracked.txt", "blob_oid": git(fixture.repository, "rev-parse", f"{fixture.pin}:tracked.txt").strip()}]
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepError:
        return
    raise AssertionError("prebuilt manifest bypassed authoring state binding")


def test_review_git_timeout(root: Path) -> None:
    # The bounded executable blocks only one selected Git operation, while
    # all fixture setup and the other calls remain real Git.
    import shutil
    real_git = shutil.which("git")
    assert real_git
    repository = restore_repo(root, "git-timeout")
    (repository / "keep.txt").write_text("edit\n")
    wrapper_dir = root / "git-wrapper"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "git"
    wrapper.write_text("#!/usr/bin/env python3\nimport os,sys,time\n"
                       "if os.environ['STALL_GIT'] in sys.argv: time.sleep(10)\n"
                       f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n")
    wrapper.chmod(0o755)
    pin = pin_of(repository)
    for operation in ("status", "ls-tree"):
        with patch.dict(os.environ, {"PATH": str(wrapper_dir) + os.pathsep + os.environ["PATH"], "STALL_GIT": operation}), \
             patch.object(RESTORE, "GIT_TIMEOUT_SECONDS", 0.1, create=True):
            try:
                RESTORE.build_restore_set(repository, pin, ["keep.txt"])
            except RESTORE.RestoreSetRefusal as exc:
                assert "timed out" in str(exc), str(exc)
            else:
                raise AssertionError(f"{operation} wait had no fail-closed bound")



def test_restore_authoring_failures_publish_nothing(root: Path) -> None:
    fixture = Fixture(root / "author-refusal")
    (fixture.repository / "tracked.txt").write_text("unapproved\n")
    template = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    template["approved"] = False
    template_path = root / "refused-template.json"
    template_path.write_text(json.dumps(template))
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert RUNNER.main(["--repository", str(fixture.repository), "--author-template", str(template_path)]) == 78
    assert stdout.getvalue() == "", "refusal published a partial manifest"
    assert "approved restore allow-list" in stderr.getvalue()
    with patch.object(RESTORE, "GIT_TIMEOUT_SECONDS", 0.01), \
         patch.object(RESTORE.subprocess, "run", side_effect=subprocess.TimeoutExpired(["git"], 0.01)), \
         contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert RUNNER.main(["--repository", str(fixture.repository), "--author-template", str(template_path),
                            "--approve-restore-path", "tracked.txt"]) == 78
    assert stdout.getvalue() == "", "timeout published a partial manifest"


def test_restore_special_names_refuse_execution(root: Path) -> None:
    # Keep ignored-file cleaning denied while names after -- remain literal.
    fixture_check = Fixture(root / "ignored-clean-denial")
    allowlist = fixture_check.allowlist(fixture_check.manifest(clean_pathspecs=["scratch"], clean_expected=[]), execute=False)
    allowlist["commands"][0]["argv"] = ["git", "clean", "-fx", "--", "scratch"]
    try:
        RUNNER.validate_allowlist(allowlist)
    except RUNNER.SweepError:
        pass
    else:
        raise AssertionError("ignored-file cleaning was admitted")
    fixture = Fixture(root / "special-execute")
    names = ["*.txt", ":(glob)*.txt", "-option.txt", " leading.txt", "cr\rname.txt", "crlf\r\nname.txt", "lf\nname.txt"]
    for name in names:
        (fixture.repository / name).write_bytes(b"pinned\n")
        git(fixture.repository, "--literal-pathspecs", "add", "--", name)
    git(fixture.repository, "commit", "-m", "special tracked names")
    git(fixture.repository, "push", "origin", "main")
    git(fixture.repository, "fetch", "origin", "main")
    for name in names:
        (fixture.repository / name).write_bytes(b"approved edit\n")
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    manifest["restore"] = RESTORE.build_restore_set(fixture.repository, fixture.pin, names)
    assert "writer exclusion" in invoke(fixture, manifest, execute=False)
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepHeld as exc:
        assert "writer exclusion" in str(exc)
    else:
        raise AssertionError("special-name restore execution bypassed writer exclusion")
    assert all((fixture.repository / name).read_bytes() == b"approved edit\n" for name in names)
    assert (fixture.repository / "tracked.txt").read_text() == "tracked\n"


def test_restore_refusal_precedes_disposal(root: Path) -> None:
    fixture = Fixture(root / "restore-index-race")
    tracked = fixture.repository / "tracked.txt"
    tracked.write_text("approved edit\n")
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    manifest["restore"] = RESTORE.build_restore_set(fixture.repository, fixture.pin, ["tracked.txt"])
    def disposal_reached() -> None:
        raise AssertionError("unsafe restore reached disposal")
    try:
        invoke(fixture, manifest, execute=True, before_disposal=disposal_reached)
    except RUNNER.SweepHeld as exc:
        assert "writer exclusion" in str(exc), str(exc)
    else:
        raise AssertionError("unsafe restore was not held")
    assert tracked.read_text() == "approved edit\n"
    assert git(fixture.repository, "show", ":tracked.txt") == "tracked\n"



def test_restore_late_editor_cannot_be_overwritten(root: Path) -> None:
    fixture = Fixture(root / "late-restore-editor")
    tracked = fixture.repository / "tracked.txt"
    tracked.write_text("approved edit\n")
    manifest = fixture.manifest(clean_pathspecs=[], clean_expected=[])
    manifest["restore"] = RESTORE.build_restore_set(fixture.repository, fixture.pin, ["tracked.txt"])
    before_refs = git(fixture.repository, "show-ref")
    git(fixture.repository, "status", "--porcelain=v1")
    before_index = (fixture.repository / ".git/index").read_bytes()
    before_head = git(fixture.repository, "rev-parse", "HEAD")
    real_git = shutil.which("git")
    assert real_git
    wrapper_dir = root / "late-editor-bin"
    wrapper_dir.mkdir()
    marker = root / "checkout-reached"
    wrapper = wrapper_dir / "git"
    editor_source = f"from pathlib import Path; Path({str(tracked)!r}).write_text('later editor change\\n')"
    # Real subprocess edits after the last verification, then delegates the
    # unchanged checkout argv. No Git output is forged by the wrapper.
    wrapper.write_text(
        f"#!{sys.executable}\nimport os, subprocess, sys\nfrom pathlib import Path\n"
        f"if 'checkout' in sys.argv[1:]:\n"
        f"    Path({str(marker)!r}).touch()\n"
        f"    subprocess.run([sys.executable, '-c', {editor_source!r}], check=True)\n"
        f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n")
    wrapper.chmod(0o755)
    with patch.dict(os.environ, {"PATH": str(wrapper_dir) + os.pathsep + os.environ["PATH"]}):
        try:
            output = invoke(fixture, manifest, execute=True)
        except RUNNER.SweepHeld as exc:
            assert "writer exclusion" in str(exc), str(exc)
        else:
            assert marker.exists() and tracked.read_text() == "tracked\n"
            assert "STAGE 6 closing readback passed" in output
            raise AssertionError("settlement can overwrite an edit after its final verification")
    assert not marker.exists(), "unsafe checkout was reached"
    assert tracked.read_text() == "approved edit\n"
    assert (fixture.repository / ".git/index").read_bytes() == before_index
    assert git(fixture.repository, "show-ref") == before_refs, "refusal mutated backup refs"
    assert git(fixture.repository, "rev-parse", "HEAD") == before_head


def test_restore_unsupported_pinned_objects_refuse(root: Path) -> None:
    repository = restore_repo(root, "unsupported-pinned")
    (repository / "pinned-link").symlink_to("keep.txt")
    git(repository, "add", "pinned-link")
    git(repository, "commit", "-m", "pinned symlink")
    pin = pin_of(repository)
    (repository / "pinned-link").unlink()
    (repository / "pinned-link").symlink_to("other.txt")
    try:
        RESTORE.build_restore_set(repository, pin, ["pinned-link"])
    except RESTORE.RestoreSetRefusal:
        return
    raise AssertionError("authoring enrolled a pinned object its readback cannot verify")


REVIEW_TESTS = [test_review_poisoned_environment, test_review_authoring_to_execution_drift,
               test_review_literal_restore_scope, test_review_filename_bytes, test_review_rename_refusal,
               test_review_versioned_authoring_route, test_review_git_timeout,
               test_restore_authoring_failures_publish_nothing, test_restore_special_names_refuse_execution,
               test_restore_refusal_precedes_disposal, test_restore_unsupported_pinned_objects_refuse,
               test_restore_late_editor_cannot_be_overwritten]


def review_regressions(root: Path) -> None:
    failures = []
    for test in REVIEW_TESTS:
        try:
            test(root)
            print(f"PASS {test.__name__}")
        except (Exception, SystemExit) as exc:
            failures.append(test.__name__)
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    assert not failures, failures


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r03-settlement-sweep-") as temporary:
        root = Path(temporary)
        test_dry_run_touches_nothing(root)
        test_never_cleanable_candidate_aborts(root)
        test_midrun_tree_change_aborts(root)
        test_branch_law_retains_unmerged_and_unbacked_squash(root)
        test_restore_clean_tree_yields_empty_set(root)
        test_restore_unapproved_dirty_path_refuses_and_names_it(root)
        test_restore_approved_dirty_path_carries_its_pinned_blob(root)
        test_restore_first_dirty_path_is_not_truncated(root)
        test_restore_path_containing_a_space_survives(root)
        test_restore_untracked_file_neither_refuses_nor_enrols(root)
        test_restore_staged_deletion_refuses(root)
        test_restore_stale_allowlist_invents_nothing(root)

        test_freshness_accepts_advanced_origin_main(root)
        test_freshness_refuses_rewound_origin_main(root)
        test_precondition_refuses_stale_head(root)
        test_closing_detects_collateral_branch_loss(root)
        test_closing_detects_undeleted_branch(root)
        test_canonical_execution_has_no_opt_in(root)
        test_empty_operations_preserve_closing_contract(root)
        test_empty_operations_racing_write_aborts(root)
        test_stale_head_refuses_new_pin_branch_before_backup(root)
        test_branch_identity_survives_concurrent_tag_collision(root)
        test_declared_branch_identity_survives_existing_tag_collision(root)
        test_empty_clean_set_never_cleans_whole_tree(root)
        review_regressions(root)
    print("r03-settlement-sweep-selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
