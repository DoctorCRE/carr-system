#!/usr/bin/env python3
"""Hermetic tests for exact-source runtime provisioning and cleanup."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env  # noqa: E402


DEPLOY = ROOT / "bin" / "deploy-worker.sh"
WRANGLER = ROOT / "mcp-server" / "node_modules" / ".bin" / "wrangler"
RUNTIME = ROOT / "mcp-server" / "node_modules"
FIXTURE_ENV = fixture_env()


def fixture_git_env() -> dict[str, str]:
    """Keep Git's automatic maintenance inside the fixture command lifetime.

    The fixture `git commit` may start `git maintenance run --auto`, which
    detaches by default and keeps writing `.git/objects` after
    subprocess.run() returns; TemporaryDirectory cleanup then fails with
    ENOTEMPTY. Same fixture-only rule as ops/machine-converge-selftest.py.
    """
    env = fixture_env()
    env.update({
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "maintenance.autoDetach",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "gc.autoDetach",
        "GIT_CONFIG_VALUE_1": "false",
    })
    return env


FIXTURE_GIT_ENV = fixture_git_env()
RECOVERY_ARGS = (
    "--env", "staging",
    "--release-key", "candidate",
    "--recovery-attempt-id", "11111111-2222-4333-8444-555555555555",
    "--recovery-step", "prior",
    "--recovery-prior-release-key", "prior",
    "--staging-receipt-idempotency-key", "22222222-2222-4333-8444-555555555555",
)
LEGACY_RELEASE_SHA = "11161a011f47d415d8fefdfd9cac842849bdbcce"
STAMP_INTRODUCTION_SHA = "ab9678a86f427e8f9e5d1f75597a21b920630995"


def make_source(*, mismatch: bool = False, broken_attachment: bool = False,
                legacy_without_stamp: bool = False
                ) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
    holder = tempfile.TemporaryDirectory(prefix="exact-recovery-source-")
    root = Path(holder.name)
    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", "mcp-server", "dealroom"],
        env=FIXTURE_GIT_ENV,
    ).decode().split("\0")
    for relative in filter(None, tracked):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination, follow_symlinks=False)
    assert not (root / "mcp-server" / "node_modules").exists()
    if mismatch:
        lock = root / "mcp-server" / "package-lock.json"
        lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    if broken_attachment:
        config = root / "mcp-server" / "wrangler.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("routes = []\n", "", 1),
            encoding="utf-8",
        )
    if legacy_without_stamp:
        (root / "mcp-server" / "bin" / "seal-candidate-manifest.mjs").unlink()
        (root / "mcp-server" / "src" / "build-stamp.js").unlink()
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=FIXTURE_GIT_ENV)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "selftest@example.invalid"],
                   check=True, env=FIXTURE_GIT_ENV)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "selftest"],
                   check=True, env=FIXTURE_GIT_ENV)
    subprocess.run(["git", "-C", str(root), "add", "mcp-server", "dealroom"],
                   check=True, env=FIXTURE_GIT_ENV)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"],
                   check=True, env=FIXTURE_GIT_ENV)
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                  text=True, env=FIXTURE_GIT_ENV).strip()
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "--detach", sha],
                   check=True, env=FIXTURE_GIT_ENV)
    if broken_attachment:
        subprocess.run(["git", "-C", str(root), "update-ref",
                        "refs/remotes/origin/main", sha], check=True, env=FIXTURE_GIT_ENV)
    return holder, root, sha


def historical_source(sha: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    holder = tempfile.TemporaryDirectory(prefix="exact-historical-source-")
    root = Path(holder.name) / "repo"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(root)],
                   check=True, env=FIXTURE_GIT_ENV)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "--detach", sha],
                   check=True, env=FIXTURE_GIT_ENV)
    return holder, root


def wrapper(root: Path, sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(DEPLOY), "--release-sha", sha,
         "--internal-exact-source-root", str(root), *RECOVERY_ARGS],
        cwd=ROOT,
        env={**FIXTURE_ENV, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def test_wrangler_dry_run() -> None:
    with tempfile.TemporaryDirectory(prefix="exact-recovery-bundle-") as raw:
        root = Path(raw)
        shutil.copytree(ROOT / "mcp-server", root / "mcp-server",
                        ignore=shutil.ignore_patterns("node_modules"))
        shutil.copytree(ROOT / "dealroom", root / "dealroom")
        # Wrangler validates the generated assets directory before a dry-run.
        # Artifact identity/tamper/rollback behavior is exercised separately by
        # doctorcre-artifact-selftest; this fixture only proves runtime bundling.
        shutil.copytree(ROOT / "dealroom",
                        root / "out" / "doctorcre-artifacts" / "current")
        worker_deps = root / "mcp-server" / "node_modules"
        assert not worker_deps.exists()
        worker_deps.symlink_to(RUNTIME)
        result = subprocess.run(
            [str(WRANGLER), "deploy", "--dry-run", "--env", "staging",
             "--config", str(root / "mcp-server" / "wrangler.toml")],
            cwd=ROOT,
            env={**os.environ, "XDG_CONFIG_HOME": str(root / "config")},
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "--dry-run: exiting now." in result.stdout
        assert "Worker Version ID:" not in result.stdout
        worker_deps.unlink()
        assert not worker_deps.exists()


def test_cleanup_traps(source: str) -> None:
    start = source.index("cleanup_ephemeral() {")
    end = source.index("\ntrap cleanup_ephemeral EXIT", start)
    functions = source[start:end]
    with tempfile.TemporaryDirectory(prefix="exact-recovery-cleanup-") as raw:
        root = Path(raw)
        link = root / "node_modules"
        script = root / "cleanup.sh"
        script.write_text(
            "#!/bin/sh\nset -eu\n"
            f"REPO={ROOT!s}\nEXACT_RUNTIME_LINK={link!s}\nSTAGING_RECEIPT=\n"
            + functions
            + "\ntrap cleanup_ephemeral EXIT\n"
            + f"ln -s {RUNTIME!s} {link!s}\nexit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        success = subprocess.run([str(script)], capture_output=True, text=True, check=False)
        assert success.returncode == 0
        assert not link.exists()

        script.write_text(
            "#!/bin/sh\nset -eu\n"
            f"REPO={ROOT!s}\nEXACT_RUNTIME_LINK={link!s}\nSTAGING_RECEIPT=\n"
            + functions
            + "\ntrap cleanup_ephemeral EXIT\ntrap 'cleanup_on_signal 143' TERM\n"
            + f"ln -s {RUNTIME!s} {link!s}\nkill -TERM $$\n",
            encoding="utf-8",
        )
        signal_exit = subprocess.run([str(script)], capture_output=True, text=True, check=False)
        assert signal_exit.returncode == 143
        assert not link.exists()


def shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + 2
    return source[start:end]


def test_legacy_prior_omits_candidate_stamps(source: str) -> None:
    """An exact pre-stamp rollback source still reaches Wrangler safely."""
    holder, exact_root = historical_source(LEGACY_RELEASE_SHA)
    exact_sha = LEGACY_RELEASE_SHA
    try:
      with tempfile.TemporaryDirectory(prefix="legacy-prior-stamp-") as raw:
        root = Path(raw)
        worker = exact_root / "mcp-server"
        assert not (worker / "bin" / "seal-candidate-manifest.mjs").exists()
        assert not (worker / "src" / "build-stamp.js").exists()
        call_log = root / "wrangler.args"
        wrangler = root / "wrangler"
        wrangler.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CALL_LOG\"\n"
            "printf '%s\\n' '--dry-run: exiting now.'\n",
            encoding="utf-8",
        )
        wrangler.chmod(0o755)
        harness = root / "harness.sh"
        harness.write_text(
            "#!/bin/sh\nset -eu\n"
            f"SOURCE_ROOT={exact_root}\nWORKER_DIR={worker}\nWRANGLER={wrangler}\n"
            "VERSION_MODE=ordinary\nTARGET_ENV=staging\nRECOVERY_STEP=prior\n"
            f"EXACT_SOURCE_ROOT={exact_root}\n"
            f"HEAD_SHA={exact_sha}\n"
            "DEPLOY_TAG=carr-staging-22222222222243338444555555555555\n"
            f"BUILD_STAMP_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
            f"CANDIDATE_SEALER_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
            "fail() { echo \"REFUSED: $1\" >&2; exit 1; }\n"
            "seal_candidate_field() { echo unexpected-sealer-call; exit 91; }\n"
            + shell_function(source, "exact_source_predates_candidate_stamps") + "\n"
            + shell_function(source, "prepare_candidate_stamps") + "\n"
            + shell_function(source, "deploy_staging_worker") + "\n"
            "prepare_candidate_stamps\ndeploy_staging_worker\n",
            encoding="utf-8",
        )
        harness.chmod(0o755)
        result = subprocess.run(
            ["sh", str(harness)],
            env={**os.environ, "CALL_LOG": str(call_log)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "--dry-run: exiting now." in result.stdout
        args = call_log.read_text(encoding="utf-8").splitlines()
        assert args == [
            "deploy", "--env", "staging",
            "--var", f"GIT_SHA:{exact_sha}",
            "--tag", "carr-staging-22222222222243338444555555555555",
        ], args
    finally:
        holder.cleanup()


def test_post_introduction_deletion_cannot_claim_legacy(source: str) -> None:
    holder, exact_root = historical_source(STAMP_INTRODUCTION_SHA)
    try:
        subprocess.run(["git", "-C", str(exact_root), "config", "user.email",
                        "selftest@example.invalid"], check=True, env=FIXTURE_GIT_ENV)
        subprocess.run(["git", "-C", str(exact_root), "config", "user.name", "selftest"],
                       check=True, env=FIXTURE_GIT_ENV)
        subprocess.run(["git", "-C", str(exact_root), "rm", "-q",
                        "mcp-server/bin/seal-candidate-manifest.mjs",
                        "mcp-server/src/build-stamp.js"], check=True, env=FIXTURE_GIT_ENV)
        subprocess.run(["git", "-C", str(exact_root), "commit", "-qm",
                        "delete both stamp files"], check=True, env=FIXTURE_GIT_ENV)
        exact_sha = subprocess.check_output(
            ["git", "-C", str(exact_root), "rev-parse", "HEAD"],
            text=True, env=FIXTURE_GIT_ENV).strip()
        with tempfile.TemporaryDirectory(prefix="post-stamp-delete-") as raw:
            root = Path(raw)
            harness = root / "harness.sh"
            harness.write_text(
                "#!/bin/sh\nset -eu\n"
                f"SOURCE_ROOT={exact_root}\nWORKER_DIR={exact_root / 'mcp-server'}\n"
                "VERSION_MODE=ordinary\nTARGET_ENV=staging\nRECOVERY_STEP=prior\n"
                f"EXACT_SOURCE_ROOT={exact_root}\nHEAD_SHA={exact_sha}\n"
                f"BUILD_STAMP_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
                f"CANDIDATE_SEALER_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
                "fail() { echo \"REFUSED: $1\" >&2; exit 1; }\n"
                "seal_candidate_field() { echo should-not-run; exit 91; }\n"
                + shell_function(source, "exact_source_predates_candidate_stamps") + "\n"
                + shell_function(source, "prepare_candidate_stamps") + "\n"
                "prepare_candidate_stamps\n",
                encoding="utf-8",
            )
            result = subprocess.run(["sh", str(harness)], capture_output=True,
                                    text=True, check=False, env=FIXTURE_GIT_ENV)
            assert result.returncode != 0, (result.stdout, result.stderr)
            assert "candidate sealer is missing" in result.stderr
    finally:
        holder.cleanup()


def test_missing_candidate_sealer_refuses_every_other_route(source: str) -> None:
    for step, mode, target, exact_root in (
            ("current_before", "ordinary", "staging", True),
            ("current_after", "ordinary", "staging", True),
            ("restore_only", "ordinary", "staging", True),
            ("standalone", "ordinary", "staging", False),
            ("prior", "ordinary", "staging", False),
            ("standalone", "upload", "production", False)):
        with tempfile.TemporaryDirectory(prefix="missing-candidate-sealer-") as raw:
            root = Path(raw)
            worker = root / "mcp-server"
            (worker / "src").mkdir(parents=True)
            harness = root / "harness.sh"
            harness.write_text(
                "#!/bin/sh\nset -eu\n"
                f"WORKER_DIR={worker}\nVERSION_MODE={mode}\nTARGET_ENV={target}\n"
                f"RECOVERY_STEP={step}\nEXACT_SOURCE_ROOT={'/exact' if exact_root else ''}\n"
                f"SOURCE_ROOT={root}\n"
                "HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                f"BUILD_STAMP_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
                f"CANDIDATE_SEALER_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
                "fail() { echo \"REFUSED: $1\" >&2; exit 1; }\n"
                "seal_candidate_field() { echo should-not-run; exit 91; }\n"
                + shell_function(source, "exact_source_predates_candidate_stamps") + "\n"
                + shell_function(source, "prepare_candidate_stamps") + "\n"
                "prepare_candidate_stamps\n",
                encoding="utf-8",
            )
            result = subprocess.run(["sh", str(harness)], capture_output=True,
                                    text=True, check=False)
            assert result.returncode != 0, (step, mode, result.stdout, result.stderr)
            assert "candidate sealer is missing" in result.stderr, (
                step, mode, result.stdout, result.stderr
            )


def test_declared_candidate_stamp_contract_requires_sealer(source: str) -> None:
    with tempfile.TemporaryDirectory(prefix="declared-candidate-stamp-") as raw:
        root = Path(raw)
        worker = root / "mcp-server"
        (worker / "src").mkdir(parents=True)
        (worker / "src" / "build-stamp.js").write_text(
            'export const BUILD_STAMP_NAMES = {candidateManifest: "CANDIDATE_MANIFEST"};\n',
            encoding="utf-8",
        )
        harness = root / "harness.sh"
        harness.write_text(
            "#!/bin/sh\nset -eu\n"
            f"WORKER_DIR={worker}\nVERSION_MODE=ordinary\nTARGET_ENV=staging\n"
            f"SOURCE_ROOT={root}\nRECOVERY_STEP=prior\nEXACT_SOURCE_ROOT=/exact\n"
            "HEAD_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            f"BUILD_STAMP_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
            f"CANDIDATE_SEALER_INTRODUCTION_SHA={STAMP_INTRODUCTION_SHA}\n"
            "fail() { echo \"REFUSED: $1\" >&2; exit 1; }\n"
            "seal_candidate_field() { echo should-not-run; exit 91; }\n"
            + shell_function(source, "exact_source_predates_candidate_stamps") + "\n"
            + shell_function(source, "prepare_candidate_stamps") + "\n"
            "prepare_candidate_stamps\n",
            encoding="utf-8",
        )
        result = subprocess.run(["sh", str(harness)], capture_output=True,
                                text=True, check=False)
        assert result.returncode != 0
        assert "candidate sealer is missing" in result.stderr

def test_source_fixture_excludes_ignored_runtime_inputs() -> None:
    runtime = ROOT / "mcp-server" / ".wrangler"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exact-fixture-", dir=runtime) as raw:
        ignored = Path(raw) / "runtime.json"
        ignored.write_text('{}\n', encoding="utf-8")
        holder, root, _sha = make_source()
        try:
            assert not (root / ignored.relative_to(ROOT)).exists(), "runtime state entered the exact source fixture"
        finally:
            holder.cleanup()


def main() -> int:
    test_source_fixture_excludes_ignored_runtime_inputs()
    source = DEPLOY.read_text(encoding="utf-8")
    assert 'cmp -s "$CURRENT_PACKAGE_LOCK" "$EXACT_PACKAGE_LOCK"' in source
    assert source.index("validate-exact-recovery-source.py") < source.index("cmp -s")
    assert source.index("cmp -s") < source.index('ln -s "$REPO/mcp-server/node_modules"')
    assert "trap cleanup_ephemeral EXIT" in source
    assert "cleanup_on_signal 130" in source
    assert "cleanup_on_signal 143" in source

    mismatch_holder, mismatch_root, mismatch_sha = make_source(mismatch=True)
    try:
        refused = wrapper(mismatch_root, mismatch_sha)
        assert refused.returncode != 0
        assert "dependency lockfile differs" in (refused.stdout + refused.stderr), (
            refused.returncode, refused.stdout, refused.stderr
        )
        assert not (mismatch_root / "mcp-server" / "node_modules").exists()
    finally:
        mismatch_holder.cleanup()

    cleanup_holder, cleanup_root, cleanup_sha = make_source(broken_attachment=True)
    try:
        refused = wrapper(cleanup_root, cleanup_sha)
        assert refused.returncode != 0
        assert not (cleanup_root / "mcp-server" / "node_modules").exists(), (
            refused.stdout, refused.stderr
        )
    finally:
        cleanup_holder.cleanup()

    test_cleanup_traps(source)
    test_wrangler_dry_run()
    test_legacy_prior_omits_candidate_stamps(source)
    test_post_introduction_deletion_cannot_claim_legacy(source)
    test_missing_candidate_sealer_refuses_every_other_route(source)
    test_declared_candidate_stamp_contract_requires_sealer(source)
    print("exact-recovery-runtime: lock gate, dry-run bundle, and cleanup passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
