#!/usr/bin/env python3
"""Compare empty selections through the real local handler, without network access.

Pass the pre-change Git revision as the sole argument. Five alternating runs
include lookup, subprocess startup, handler execution, transfer, and selection.
The synthetic catalog approximates the audited 340 KB payload. This measures
local transport, not deployed Worker latency or database query time.
"""
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_scope_lookup import lookup

DRIVER = """
import { readFileSync } from 'node:fs';
import { executeRegisteredTool } from './mcp-server/src/tools.js';
const rules = JSON.parse(readFileSync(process.argv[1], 'utf8'));
const args = JSON.parse(readFileSync(0, 'utf8'));
const client = { query: async () => ({ rows: rules }) };
const actor = { slug: 'joe', human: true, via: 'oauth-google' };
const result = await executeRegisteredTool(client, actor, 'standing-context', args);
process.stdout.write(JSON.stringify(result));
"""


def main():
    revision = sys.argv[1]
    source = subprocess.run(['git', 'show', revision + ':lib/rule_scope_lookup.py'],
                            cwd=REPO, text=True, capture_output=True, check=True).stdout
    before = types.ModuleType('before_scope_lookup')
    before.__file__ = str(REPO/'lib/rule_scope_lookup.py')
    exec(compile(source, before.__file__, 'exec'), before.__dict__)
    rows = [dict(id=f'{i:08x}', statement='Binding text. ' * 115,
                 scope={'tools': ['Write']}, personal_to=None) for i in range(1, 201)]
    sizes = {'before': [], 'after': []}
    samples = {'before': [], 'after': []}
    event = dict(hook_event_name='PreToolUse', tool_name='CacheBenchmarkEmpty', tool_input={})
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        fixture = root/'rules.json'
        fixture.write_text(json.dumps(rows))
        phase = 'after'
        def runner(command, **kwargs):
            result = subprocess.run(['node', '--input-type=module', '-e', DRIVER, str(fixture)],
                                    input=command[-1], **kwargs)
            if result.returncode == 0:
                sizes[phase].append(len(result.stdout.encode()))
            return result
        with patch('lib.rule_scope_lookup.CACHE_DIR', root/'cache'), \
                patch('lib.rule_routes._context_epoch', return_value='benchmark-epoch'):
            warm = lookup(event, runner=runner, session_id='benchmark')
            assert warm['complete'] and not warm['rules']
            sizes['after'].clear()
            for _ in range(5):
                for phase in ('before', 'after'):
                    start = time.perf_counter()
                    result = (before.lookup(event, runner=runner) if phase == 'before' else
                              lookup(event, runner=runner, session_id='benchmark'))
                    samples[phase].append(round((time.perf_counter()-start)*1000, 3))
                    assert result == warm
    print(json.dumps({'surface': 'local subprocess and registered handler; synthetic store; no network',
                      'baseline_ref': revision, 'errors': 0, 'runs_per_side': 5,
                      'samples_ms': samples,
                      'medians_ms': {k: statistics.median(v) for k, v in samples.items()},
                      'response_bytes': sizes}, indent=2))


if __name__ == '__main__':
    main()
