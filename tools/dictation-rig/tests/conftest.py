"""Keep collection-time imports and each test away from the real home."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def isolated_paths(root):
    return {
        'HOME': str(root),
        'CARR_CAPTURE_LOG': str(root / 'capture-bridge.log'),
        'CARR_CAPTURE_CONFIG': str(root / 'capture-config.json'),
        'CARR_RECORDINGS_DIR': str(root / 'recordings'),
        'CARR_CALL_MODE_LOG': str(root / 'call-mode.log'),
    }


_collection_dir = tempfile.TemporaryDirectory()
_collection_env = patch.dict(os.environ, isolated_paths(Path(_collection_dir.name)))
_collection_env.start()


def pytest_unconfigure():
    _collection_env.stop()
    _collection_dir.cleanup()


@pytest.fixture(autouse=True)
def isolate_home(monkeypatch, tmp_path):
    for name, value in isolated_paths(tmp_path).items():
        monkeypatch.setenv(name, value)
