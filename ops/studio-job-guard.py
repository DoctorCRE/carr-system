#!/usr/bin/env python3
"""Hold the off-host owner and job locks for the complete process group lifetime."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.studio_failover import Leader, connection, run_guarded, write_json


def main():
    host, label = sys.argv[1:3]
    marker = Path.home() / '.config/carr/failover-host.json'
    def disarm():
        write_json(marker, {'host': host, 'armed': False})
    try:
        config = json.loads((ROOT / 'ops/config/studio-failover.v1.json').read_text())
        local = json.loads(marker.read_text())
        if local != {'host': host, 'armed': True} or label not in {j['label'] for j in config['jobs']}:
            return 75
        return run_guarded(Leader(connection()), host, label,
                           lambda: subprocess.Popen(sys.argv[4:], start_new_session=True), disarm)
    except Exception as exc:
        disarm()
        # libpq exceptions can include credentials; only the exception type crosses the output seam.
        print('FAIL leader guard: ' + type(exc).__name__, file=sys.stderr)
        return 76


if __name__ == '__main__':
    raise SystemExit(main())
