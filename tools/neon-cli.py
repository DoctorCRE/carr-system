#!/usr/bin/env python3
"""Run a Neon command without browser authentication; capture output in callers."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.neon_cli import run


def main():
    try:
        result = run(sys.argv[1:])
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit(f'neonctl could not complete ({type(exc).__name__}); provider output suppressed')
    if result.returncode:
        sys.exit(f'neonctl failed (exit {result.returncode}); provider output suppressed')
    sys.stdout.write(result.stdout)


if __name__ == '__main__':
    main()
