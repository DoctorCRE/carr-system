#!/usr/bin/env python3
"""Five alternating warmed lookups per side, with equality and transport counts.

Usage: python3 ops/rule-scope-cache-benchmark.py BASE_REF [--live]
Local uses the registered handler and a synthetic query adapter. Live uses only
standing-context reads through run.sh. Profiling runs are separate from timings.
"""
import json
import inspect
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
import lib.rule_scope_lookup as current

DRIVER = """
import { readFileSync } from 'node:fs';
const start = performance.now();
const { executeRegisteredTool } = await import('./mcp-server/src/tools.js');
const imported = performance.now();
const rules = JSON.parse(readFileSync(process.argv[1], 'utf8'));
const args = JSON.parse(readFileSync(0, 'utf8'));
let queryMs = 0, digestMs = 0, queries = 0;
const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
crypto.subtle.digest = async (...args) => {
  const start = performance.now();
  try { return await originalDigest(...args); }
  finally { digestMs += performance.now() - start; }
};
const client = { query: async () => {
  const start = performance.now(); queries++;
  const rows = structuredClone(rules);
  queryMs += performance.now() - start;
  return { rows };
} };
const actor = { slug: 'joe-local', human: false, via: 'local-token', sponsoring_human_id: 'joe' };
const handlerStart = performance.now();
const result = await executeRegisteredTool(client, actor, 'standing-context', args);
process.stdout.write(JSON.stringify(result));
if (process.argv[2] === 'profile') console.error(JSON.stringify({
  registry_import_ms: imported-start, synthetic_query_ms: queryMs, queries,
  digest_ms: digestMs, handler_ms: performance.now()-handlerStart
}));
"""

LIVE_PROFILE = """
const originalFetch = globalThis.fetch;
globalThis.fetch = async (...args) => {
  const start = performance.now();
  const response = await originalFetch(...args);
  const headersMs = performance.now()-start;
  const originalText = response.text.bind(response);
  response.text = async () => {
    const text = await originalText();
    console.error(JSON.stringify({http_headers_ms: headersMs,
      http_body_ms: performance.now()-start,
      server_timing: response.headers.get('server-timing'), status: response.status}));
    return text;
  };
  return response;
};
"""


def main():
    revision = sys.argv[1]
    live = '--live' in sys.argv[2:]
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
            result = (subprocess.run(command, **kwargs) if live else
                      subprocess.run(['node', '--input-type=module', '-e', DRIVER, str(fixture)],
                                     input=command[-1], **kwargs))
            if result.returncode != 0:
                print(result.stderr, file=sys.stderr)
                raise RuntimeError('benchmark transport failed')
            sizes[phase].append(len(result.stdout.encode()))
            return result
        before.CACHE_DIR = root/'before'
        before_options = {'session_id': 'benchmark'} if 'session_id' in inspect.signature(before.lookup).parameters else {}
        with patch.object(current, 'CACHE_DIR', root/'after'), \
                patch('lib.rule_routes._context_epoch', return_value='benchmark-epoch'), \
                patch('lib.rule_boot_gate.read_arm', return_value={'digest': 'benchmark-boot'}):
            expected = current.lookup(event, runner=runner, session_id='benchmark')
            assert before.lookup(event, runner=runner, **before_options) == expected
            sizes = {'before': [], 'after': []}
            for _ in range(5):
                for phase, module in (('before', before), ('after', current)):
                    start = time.perf_counter()
                    result = module.lookup(event, runner=runner, **(before_options if phase == 'before' else {'session_id': 'benchmark'}))
                    samples[phase].append(round((time.perf_counter()-start)*1000, 3))
                    assert result == expected, 'selection changed'
        if live:
            loader = root/'profile.mjs'
            loader.write_text(LIVE_PROFILE)
            env = dict(__import__('os').environ, NODE_OPTIONS='--import=' + str(loader))
            for key in ('DATABASE_URL', 'CARR_BREAK_GLASS', 'CARR_BREAK_GLASS_REASON', 'CARR_MCP_CLIENT_PROFILE'):
                env.pop(key, None)
            command = [str(REPO/'run.sh'), 'call', 'standing-context', '{"detail":"scopes"}']
            start = time.perf_counter()
            profile = subprocess.run(command, cwd=REPO, env=env, text=True, capture_output=True, check=True)
        else:
            start = time.perf_counter()
            profile = subprocess.run(['node', '--input-type=module', '-e', DRIVER, str(fixture), 'profile'],
                                     input='{"detail":"scopes"}', cwd=REPO, text=True,
                                     capture_output=True, check=True)
        elapsed = (time.perf_counter()-start)*1000
        detail = json.loads(next(line for line in profile.stderr.splitlines() if line.startswith('{')))
        assert json.loads(profile.stdout)['ok'] is True
        detail['process_total_ms'] = elapsed
        detail['outside_' + ('http_ms' if live else 'registry_and_handler_ms')] = elapsed - (
            detail['http_body_ms'] if live else detail['registry_import_ms'] + detail['handler_ms'])
    print(json.dumps({'surface': 'live run.sh and deployed Worker' if live else
                      'local Node and registered handler; synthetic query adapter; no network',
                      'baseline_ref': revision, 'errors': 0, 'equal_selections': 10,
                      'runs_per_side': 5, 'samples_ms': samples,
                      'medians_ms': {k: statistics.median(v) for k, v in samples.items()},
                      'ranges_ms': {k: [min(v), max(v)] for k, v in samples.items()},
                      'transport_calls': {k: len(v) for k, v in sizes.items()},
                      'response_bytes': sizes, 'separate_profile': detail,
                      'limitation': 'No live server-timing header means network and server query/digest cannot be separated without deployment.'
                                    if live else 'Query adapter timings exclude a real database.'}, indent=2))


if __name__ == '__main__':
    main()
