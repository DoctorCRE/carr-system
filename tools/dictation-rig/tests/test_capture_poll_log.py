"""The poller respects the isolated capture log used by tests."""

import os
from pathlib import Path
import shutil
import subprocess


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
    assert not (home / 'Library/Logs/capture-bridge.log').exists()
