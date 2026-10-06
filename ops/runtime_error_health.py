import json
from datetime import datetime, timezone
from pathlib import Path

CONTRACT = json.loads((Path(__file__).resolve().parent.parent / 'mcp-server/src/runtime-errors.v1.json').read_text())
ACTION = CONTRACT['response_action'].replace('{quiet_hours}', str(CONTRACT['auto_clear']['quiet_ms'] // 3600000))


def release_gate_failure(repo, row):
    try:
        installation = json.loads((Path(repo) / 'out/runtime-error-consumer-installed.json').read_text())
        return (installation.get('workflow') == 'runtime-error-watch'
                and installation.get('installed') is True and row.startswith('UNAVAILABLE'))
    except (OSError, ValueError, TypeError):
        return False


def health_row(repo, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        receipt = json.loads((Path(repo) / 'out/runtime-error-health.json').read_text())
        age = (now - datetime.fromisoformat(receipt['checked_at'])).total_seconds()
        if not 0 <= age <= 600:
            return f'UNAVAILABLE runtime errors — consumer receipt older than 10m · {ACTION}'
        row = receipt['health']
        if not isinstance(row, str) or 'on breach:' not in row:
            raise ValueError('unbound runtime error row')
        return row
    except (OSError, ValueError, KeyError, TypeError):
        return f'UNAVAILABLE runtime errors — capture/consumer receipt missing · {ACTION}'
