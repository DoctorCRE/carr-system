#!/usr/bin/env python3
# ci: selftest
"""Keep the retired launcher absent and its shared record callers intact."""
from pathlib import Path
import json
r = Path(__file__).resolve().parents[1]
paths = ['pipelines/build-front-door.py', 'pipelines/front-door.html', 'ops/front-door-video-assets-selftest.py']
remaining = [p for p in paths if (r / p).exists()]
assert not remaining, f'obsolete front-door artifacts remain: {remaining}'
registry = json.loads((r / 'ops/config/drive-dependencies.v1.json').read_text())
assert not any(e['id'] == 'normal-front-door-launch-links' for e in registry['entries'])
assert 'DNA/Team/front-door-build-note' not in (r / 'pipelines/doctrine_inventory.py').read_text()
assert 'call lead-hot' in (r / 'tools/doc-convo/bin/refresh-context.sh').read_text()
assert '"claim-card"' in (r / 'ops/scac-mutation-inventory.mjs').read_text()
print('front-door deletion and retained callers passed')
