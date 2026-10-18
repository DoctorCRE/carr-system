#!/usr/bin/env python3
"""Run this branch's read-only CLI on the MacBook without installing or activating it."""
import base64
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / 'ops/config/studio-failover.v1.json').read_text())
names = ['ops/studio-failover.py', 'ops/git_env.py', 'ops/studio-job-guard.py',
         'lib/studio_failover.py', 'lib/studio_failover_health.py', 'lib/machine_role.py',
         'ops/config/studio-failover.v1.json', *[j['source'] for j in config['jobs']]]
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
    for name in sorted(set(names)): archive.add(ROOT / name, arcname=name, recursive=False)
# Only the explicit source list enters this packet; no credential or host state.
packet = base64.b64encode(buffer.getvalue()).decode()
remote = '''import base64,io,subprocess,tarfile,tempfile,sys
from pathlib import Path
with tempfile.TemporaryDirectory(prefix="carr-failover-probe-") as raw:
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(PACKET)),mode="r:gz") as archive:
        archive.extractall(raw,filter="data")
    result=subprocess.run([sys.executable,str(Path(raw)/"ops/studio-failover.py"),
        "takeover","--target","macbook","--dry-run"],capture_output=True,text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
'''.replace('PACKET', repr(packet))
r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                    config['hosts']['macbook']['ssh'], '~/carr-system/.venv/bin/python -'], input=remote,
                   capture_output=True, text=True, timeout=60)
if not r.stdout:
    raise SystemExit('MacBook probe unavailable; exit ' + str(r.returncode))
report = json.loads(r.stdout)
out = ROOT / 'out/studio-failover/macbook-dryrun.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'target': report['target'], 'ready': report['ready'], 'steps': len(report['steps']),
                  'missing': report['missing'], 'receipt': str(out)}, indent=2))
raise SystemExit(r.returncode)
