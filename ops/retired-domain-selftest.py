#!/usr/bin/env python3
"""Reject retired-domain targets in tracked callers and configuration."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = 'practicecre' + '.com'
HISTORY = re.compile(r'(^corpus/|(^|/)(fixtures|history|archive)(/|$)|runbook.*\d{4}-\d{2}-\d{2})')
RETAINED = {
    'mcp-server/wrangler.toml': '{ pattern = "api.' + DOMAIN + '", custom_domain = true },',
    'ops/doctorcre-production-smoke.py': 'expected = {"api.' + DOMAIN + '", "api.doctorcre.com", "dealroom.doctorcre.com", "reports.doctorcre.com"}',
}


def exempt(path, line):
    if HISTORY.search(path):
        return True
    text = line.strip()
    if (path == 'secrets-inventory.md' and text.startswith('| GOOGLE_CLIENT_ID |')
            and 'verified in the console 2026-08-02' in text):
        return True
    if path == 'exporters/targets.py' and text == (
            '# LAYER decided\", \"' + DOMAIN + ' registered\", the doctorcre ruling) all resolve'):
        return True
    if text == RETAINED.get(path):
        return True
    return (path == 'ops/doctorcre-production-smoke-selftest.py'
            and text.startswith("'routes = ["))


def main():
    assert not exempt('bin/example.sh', '# curl https://api.' + DOMAIN + '/mcp')
    for host, expected in [('api.' + DOMAIN, 2), ('api.doctorcre.com', 0)]:
        for command in [f'curl -X POST -d probe=1 https://{host}/mcp',
                        f'curl -q -H "Authorization: Bearer dummy-test-value" https://{host}/mcp']:
            payload = {'tool_name': 'Bash', 'tool_input': {'command': command}}
            guard = subprocess.run(
                [sys.executable, str(ROOT / 'hooks/guard-unattended.py')],
                input=json.dumps(payload), text=True, capture_output=True,
                env={**os.environ, 'CARR_HOOK_FIXTURE': '1'}, timeout=20)
            assert guard.returncode == expected, f'{host}: guard exit {guard.returncode}'
    result = subprocess.run(
        ['git', 'grep', '-I', '-n', '-i', '-F', DOMAIN, '--', '.'],
        cwd=ROOT, text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    violations = []
    for match in result.stdout.splitlines():
        path, number, line = match.split(':', 2)
        if not exempt(path, line):
            violations.append(f'{path}:{number}')
    assert not violations, 'Retired domain in active targets: ' + ', '.join(violations)
    print('retired-domain-selftest: tracked callers and config exclude the retired domain')


if __name__ == '__main__':
    main()
