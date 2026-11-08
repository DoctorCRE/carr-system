"""Reject unscoped recording provenance, including whole-registry pins."""
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEME = 'registered-handler-contract/v1'


def check(root):
    errors = []
    sources = root / 'mcp-server/src'
    registries = [path for path in sources.rglob('*')
                  if path.suffix in ('.js', '.ts') and
                  ('registry' in path.name or path.name == 'tools.js'
                   or len(re.findall(r'\bhandler\s*:', path.read_text())) > 1)]
    hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in registries if p.is_file()}
    for directory in ('mcp-server/test', 'tools/fixtures'):
        for path in sorted((root / directory).rglob('*.json')):
            value = json.loads(path.read_text())
            if not isinstance(value, dict):
                continue
            provenance = value.get('provenance', {})
            if 'producer_sha256' not in provenance:
                continue
            scope = provenance.get('producer_scope', {})
            if (set(scope) != {'scheme', 'verbs'} or scope.get('scheme') != SCHEME or not scope.get('verbs')
                    or not all(isinstance(v, str) and v for v in scope['verbs'])
                    or scope['verbs'] != sorted(set(scope['verbs']))
                    or provenance['producer_sha256'] in hashes):
                errors.append(f'{path.relative_to(root)}: producer hash must bind named registered handlers and contracts, never a whole registry')
    return errors


if __name__ == '__main__':
    failures = check(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    print('\n'.join(failures) if failures else 'Recording producer scopes verified')
    sys.exit(bool(failures))
