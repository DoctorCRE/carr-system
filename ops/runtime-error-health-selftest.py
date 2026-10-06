import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import runtime_error_health as runtime_health
from runtime_error_health import health_row, ACTION

root = Path(__file__).resolve().parent.parent / 'out/_to_delete/runtime-error-health' / str(uuid4())
(root / 'out').mkdir(parents=True)
now = datetime.now(timezone.utc)
assert health_row(root, now).startswith('UNAVAILABLE')
receipt = root / 'out/runtime-error-health.json'
receipt.write_text(json.dumps({'checked_at': now.isoformat(), 'health': f'WARN runtime errors — 1 active fingerprint · {ACTION}'}))
assert health_row(root, now).startswith('WARN')
assert 'owner orchestrator' in health_row(root, now)
assert 'verify' in health_row(root, now)
assert 'auto-clear after 24h quiet on a newer release' in health_row(root, now)
assert health_row(root, now + timedelta(minutes=11)).startswith('UNAVAILABLE')
receipt.write_text(json.dumps({'checked_at': now.isoformat(), 'health': 'OK naked metric'}))
assert health_row(root, now).startswith('UNAVAILABLE')
print('runtime-error-health-selftest: bound, stale and missing rows passed')

assert runtime_health.release_gate_failure(root, 'UNAVAILABLE missing') is False
assert runtime_health.release_gate_failure(root, 'WARN active errors') is False
(root / 'out/runtime-error-consumer-installed.json').write_text(json.dumps({'workflow': 'runtime-error-watch', 'installed': True}))
assert runtime_health.release_gate_failure(root, 'UNAVAILABLE missing') is True
assert runtime_health.release_gate_failure(root, 'WARN active errors') is False
contract = json.loads((Path(__file__).resolve().parent.parent / 'mcp-server/src/runtime-errors.v1.json').read_text())
assert ACTION == contract['response_action'].replace('{quiet_hours}', str(contract['auto_clear']['quiet_ms'] // 3600000))
