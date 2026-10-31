#!/usr/bin/env python3
"""Prepare exact Worker source for counting without writing into its checkout."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare(worker, target):
    runtime = ROOT / 'mcp-server/node_modules'
    (target / 'node_modules').symlink_to(runtime)
    (target / 'lib').symlink_to(worker.parent / 'lib')
    generated = ('scac-mutation-registry.current.generated.js', 'tool-families.generated.js')
    if ((worker / 'src/scac-mutation-registry.inputs.js').exists() and
            any(not (worker / 'src' / name).exists() for name in generated)):
        shutil.copytree(worker / 'src', target / 'mcp-server/src')
        shutil.copy(worker / 'package.json', target / 'mcp-server/package.json')
        for path in ('tools/regen.py', 'ops/git_env.py', 'ops/scac-mutation-inventory.mjs'):
            source = worker.parent / path
            if not source.exists():
                source = ROOT / path
            (target / path).parent.mkdir(exist_ok=True)
            shutil.copy(source, target / path)
        subprocess.run([sys.executable, target / 'tools/regen.py', 'registry'],
                       cwd=target, stdout=sys.stderr, check=True)
    else:
        (target / 'mcp-server').symlink_to(worker)


if __name__ == '__main__':
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
