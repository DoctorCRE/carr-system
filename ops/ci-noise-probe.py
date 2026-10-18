#!/usr/bin/env python3
"""Emit a completed verdict only after the edge probe returns normally."""
# doctrine: engineering-workflow-sop
from contextlib import redirect_stdout
import json
from pathlib import Path
import runpy
import sys


def main():
    probe = runpy.run_path(str(Path(__file__).with_name('edge-liveness.py')))
    affected_services = []
    assess = probe['assess']

    def retain_observations(rows):
        affected_services.extend(
            {'service_key': row[0], 'hours_since_observed': float(row[7])}
            for row in rows if row[6] is not None and row[4] == 'stale')
        return assess(rows)

    probe['main'].__globals__['assess'] = retain_observations
    with redirect_stdout(sys.stderr):
        code = probe['main']()
    status = {0: 'HEALTHY', 1: 'ALARM', 78: 'BLIND'}[code]
    print(json.dumps({'schema': 'edge-liveness-verdict/v1', 'probe_exit': code, 'status': status,
                      'affected_services': affected_services}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
