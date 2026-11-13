"""Give decoder tests an executable no competing Cargo build can republish."""
import atexit
from functools import cache
import os
from pathlib import Path
import subprocess
import tempfile

CRATE = Path(__file__).resolve().parents[1] / 'vendor/hxprobe'


@cache
def test_binary() -> Path:
    prepared = os.environ.get('CARR_HXPROBE_TEST_BINARY')
    if prepared:
        binary = Path(prepared)
        if not binary.is_file():
            raise FileNotFoundError(binary)
        return binary
    directory = tempfile.TemporaryDirectory(prefix='hxprobe-test-')
    try:
        subprocess.run(['cargo', 'build', '--locked', '--target-dir', directory.name],
                       cwd=CRATE, check=True, timeout=180)
    except BaseException:
        directory.cleanup()
        raise
    atexit.register(directory.cleanup)
    return Path(directory.name) / 'debug/hxprobe'
