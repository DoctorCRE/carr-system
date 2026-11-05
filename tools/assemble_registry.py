#!/usr/bin/env python3
"""Validate committed registry inputs and atomically publish static Worker adapters."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def assemble():
    config = tomllib.loads((ROOT / 'mcp-server/wrangler.toml').read_text())
    expected = {f'#{name}': f'./.generated/current/{name}.js'
                for name in ('verb-families', 'operation-contracts')}
    if config.get('alias') != expected:
        raise ValueError('Worker registry aliases must replace both Node discovery adapters')
    subprocess.run(['node', 'ops/scac-mutation-inventory.mjs'], cwd=ROOT, check=True)
    data = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', '''
import {ids} from './mcp-server/src/verb-families.node.js';
import {SCAC_MUTATION_OPERATIONS as operations} from './mcp-server/src/operation-contracts.node.js';
console.log(JSON.stringify({ids,operations}));
'''], cwd=ROOT, text=True))
    inputs = [ROOT / name for name in (
        'tools/assemble_registry.py', 'mcp-server/src/registry-layout.js',
        'mcp-server/src/verb-families.node.js', 'mcp-server/src/operation-contracts.node.js',
        'mcp-server/src/scac-mutation-registry.current.generated.js',
        'mcp-server/package.json', 'mcp-server/wrangler.toml')]
    inputs += sorted((ROOT / 'mcp-server/src/tool-families').iterdir())
    inputs += sorted((ROOT / 'mcp-server/contracts/operations').iterdir())
    receipt = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    digest = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    parent = ROOT / 'mcp-server/.generated'
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent, prefix='preparing-') as work:
        work = Path(work)
        families = '\n'.join(f'import f{i} from "../../src/tool-families/{name}.js";' for i, name in enumerate(data['ids']))
        families += '\nexport default Object.freeze([' + ','.join(f'f{i}' for i in range(len(data['ids']))) + ']);\n'
        (work / 'verb-families.js').write_text(families)
        (work / 'operation-contracts.js').write_text('export const SCAC_MUTATION_OPERATIONS = Object.freeze(' + json.dumps(data['operations'], separators=(',', ':')) + ');\n')
        (work / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        destination = parent / digest
        if destination.exists():
            for path in work.iterdir():
                if path.read_bytes() != (destination / path.name).read_bytes():
                    raise ValueError('corrupt existing registry assembly')
        else:
            os.rename(work, destination)
        pointer = parent / ('pointer-' + str(os.getpid()))
        try:
            pointer.symlink_to(digest, target_is_directory=True)
            os.replace(pointer, parent / 'current')
        finally:
            pointer.unlink(missing_ok=True)
    print(f'Static registry assembly: {digest}')


if __name__ == '__main__':
    assemble()
