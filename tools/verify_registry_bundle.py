#!/usr/bin/env python3
"""Compile the Worker with Wrangler dry-run and reject Node registry discovery in its graph."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def verify():
    # No caller-supplied command, deployment mode, credentials, or output path.
    scratch = ROOT / 'scratchpad'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='registry-bundle-', dir=scratch) as temporary:
        work = Path(temporary)
        (work / 'assets').mkdir()
        (work / 'home').mkdir()
        source = (ROOT / 'mcp-server/wrangler.toml').read_text()
        source = source.replace('main = "src/index.js"', f'main = "{ROOT}/mcp-server/src/index.js"')
        source = source.replace('directory = "../out/doctorcre-artifacts/current"', f'directory = "{work}/assets"')
        source = source.replace('command = "python3 ../tools/assemble_registry.py"', f'command = "python3 {ROOT}/tools/assemble_registry.py"')
        source = source.replace('"./.generated/current/', f'"{ROOT}/mcp-server/.generated/current/')
        config = work / 'wrangler.toml'
        config.write_text(source)
        child = {k: v for k, v in os.environ.items()
                 if not k.startswith(('CLOUDFLARE_', 'WRANGLER_', 'CF_'))}
        child.update(HOME=str(work / 'home'), WRANGLER_SEND_METRICS='false',
                     CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV='false')
        subprocess.run([str(ROOT / 'mcp-server/node_modules/.bin/wrangler'), 'deploy',
                        '--dry-run', '--config', str(config), '--outdir', str(work / 'bundle'),
                        '--metafile', str(work / 'graph.json')], cwd=ROOT, env=child, check=True)
        graph = work / 'graph.json'
        if not graph.is_file():
            raise ValueError('Wrangler did not emit a bundle graph')
        inputs = set(json.loads(graph.read_text())['inputs'])
        if any(name.endswith(('verb-families.node.js', 'operation-contracts.node.js')) for name in inputs):
            raise ValueError('Node registry discovery remains reachable in Worker')
        for name in ('verb-families.js', 'operation-contracts.js'):
            if not any('/.generated/' in path and path.endswith('/' + name) for path in inputs):
                raise ValueError(f'Worker is missing static adapter: {name}')
        print(f'Wrangler registry graph: {len(inputs)} inputs; both static adapters, no Node discovery')


if __name__ == '__main__':
    verify()
