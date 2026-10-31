"""The poller respects the isolated capture log used by tests."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_poller_writes_only_to_configured_log(tmp_path):
    home = tmp_path / 'home'
    recordings = tmp_path / 'recordings'
    recordings.mkdir()
    script = tmp_path / 'bin' / 'capture-poll.sh'
    script.parent.mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / 'bin' / 'capture-poll.sh', script)
    log = tmp_path / 'isolated.log'
    result = subprocess.run(['/bin/bash', str(script)], env={**os.environ, 'HOME': str(home),
                            'CARR_RECORDINGS_DIR': str(recordings), 'CARR_CAPTURE_LOG': str(log)}, check=False)
    assert result.returncode == 1
    assert 'capture-bridge.py missing' in log.read_text()
    assert not list((home / 'Library/Logs').rglob('*'))


def test_phone_suite_does_not_write_default_logs(tmp_path):
    home = tmp_path / 'home'
    child_env = {**os.environ, 'HOME': str(home)}
    child_env.pop('CARR_CAPTURE_LOG', None)
    child_env.pop('CARR_CALL_MODE_LOG', None)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name('test_phone_intake.py'))],
        env=child_env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list((home / 'Library/Logs').rglob('*'))
