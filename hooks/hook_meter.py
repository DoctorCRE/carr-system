"""Log paths for safety hooks and their isolated fixtures."""
import os


def guard_log_path(repo, src=None):
    override = os.environ.get('CARR_HOOK_GUARD_LOG')
    if override:
        return override
    fixture = os.environ.get('CARR_HOOK_FIXTURE') == '1' or os.environ.get('PYTEST_CURRENT_TEST')
    return os.path.join(repo, 'out', 'fixtures', 'hook-guard-fixture.log') if fixture else os.path.join(repo, 'out', 'hook-guard.log')
