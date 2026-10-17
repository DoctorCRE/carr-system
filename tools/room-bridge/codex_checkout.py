"""Provision standalone job repositories before Codex enters its sandbox."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.git_env import scrubbed_env
from desks import DeskError

CANONICAL_REPO = Path.home() / 'carr-system'
CHECKOUT_ROOT = Path('/private/tmp')


def prepare(target: str, source: str, env: dict | None = None) -> dict[str, str]:
    """Return a retained parent workspace and nested clone; clean failed clones.

    A workspace-root .git is protected by Codex. The parent contains no Git
    metadata, so its nested standalone checkout remains sandbox-writable.
    No permissions flags or shared Git metadata are changed.
    """
    child_env = scrubbed_env(env)

    def git(args: list[str], cwd: str, stage: str, timeout: int = 30) -> str:
        try:
            result = subprocess.run(['git', *args], cwd=cwd, env=child_env,
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise DeskError('codex_checkout_failed', f'checkout refused: {stage} unavailable or timed out') from None
        if result.returncode:
            # Git stderr/argv can include an authenticated origin URL.
            raise DeskError('codex_checkout_failed',
                            f'checkout refused: {stage} failed (git exit {result.returncode})')
        return result.stdout.strip()

    new_branch = target.startswith('new:')
    branch = target[4:] if new_branch else target
    if not branch or branch.startswith('-'):
        raise DeskError('bad_checkout', 'checkout requires a branch or new:<branch>')
    git(['check-ref-format', '--branch', branch], source, 'branch validation')
    origin = git(['remote', 'get-url', 'origin'], source, 'origin lookup')
    canonical = str(CANONICAL_REPO)
    author = git(['config', '--get', 'user.name'], canonical, 'canonical author lookup')
    email = git(['config', '--get', 'user.email'], canonical, 'canonical author lookup')
    if not author or not email.endswith('@users.noreply.github.com'):
        raise DeskError('codex_checkout_failed', 'checkout refused: canonical author needs a noreply email')
    try:
        workspace = Path(tempfile.mkdtemp(prefix='room-codex-', dir=CHECKOUT_ROOT))
    except OSError:
        raise DeskError('codex_checkout_failed', 'checkout refused: job workspace unavailable') from None
    repo = workspace / 'checkout'
    try:
        args = ['clone', '--no-local']
        if not new_branch:
            args += ['--branch', branch]
        git([*args, '--', origin, str(repo)], source, 'origin clone', timeout=180)
        if new_branch:
            git(['switch', '-c', branch], str(repo), 'new branch creation')
        git(['config', '--local', 'user.name', author], str(repo), 'author configuration')
        git(['config', '--local', 'user.email', email], str(repo), 'author configuration')
        revision = git(['rev-parse', 'HEAD'], str(repo), 'revision lookup')
    except BaseException:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    return {'checkout_path': str(repo), 'checkout_workspace': str(workspace),
            'checkout_branch': branch, 'checkout_revision': revision}


def instruction(checkout: dict[str, str]) -> str:
    return (f"Job checkout: {checkout['checkout_path']}\n"
            'Change into this standalone repository and read its AGENTS.md before working. '
            'Commit and push from this checkout. Its parent is the fresh Codex workspace; '
            'a resumed desk keeps its existing cwd. The checkout is retained after the turn.\n\n')
