#!/usr/bin/env python3
"""Compare dry-run controller overhead with isolated, offline release fixtures.

No production steps execute. These timings cannot estimate production savings.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))


def module(name, source):
    loaded = types.ModuleType(name)
    loaded.__file__ = str(ROOT / 'ops/release-pipeline.py')
    sys.modules[name] = loaded
    exec(compile(source, loaded.__file__, 'exec'), loaded.__dict__)
    return loaded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-ref', required=True)
    args = parser.parse_args()
    before = subprocess.run(['git', 'show', args.before_ref + ':ops/release-pipeline.py'],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout
    spec = importlib.util.spec_from_file_location('release_fixture', ROOT / 'ops/release-pipeline-selftest.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    variants = {'before': module('release_before', before),
                'after': module('release_after', (ROOT / 'ops/release-pipeline.py').read_text())}
    samples = []
    for _ in range(5):
        for label, variant in variants.items():
            fixture.rp = variant
            with tempfile.TemporaryDirectory() as raw:
                fx = fixture.Fixture(Path(raw))
                fx.commit({'mcp-server/src/timing.js': '1'})
                runner = fixture.FakeRunner()
                pipe = fx.pipeline(runner, dry_run=True)
                timings = {}
                def timed(name, fn):
                    def call(*a, **kw):
                        start = time.perf_counter()
                        try:
                            return fn(*a, **kw)
                        finally:
                            timings[name] = (time.perf_counter() - start) * 1000
                    return call
                for name in ('worker_evidence', 'wrangler_auth', 'health_read',
                             '_release_worktree_ready', 'worker_prerequisites'):
                    if hasattr(pipe, name):
                        setattr(pipe, name, timed(name, getattr(pipe, name)))
                start = time.perf_counter()
                rc = pipe.tick(['worker'])
                elapsed = (time.perf_counter() - start) * 1000
                assert rc == 0 and not runner.calls and not fx.records(), 'dry-run executed a release step'
                samples.append({'variant': label, 'total_ms': elapsed, 'steps_ms': timings,
                                'production_steps_executed': len(runner.calls), 'rc': rc})
    report = {'scope': 'offline dry-run controller overhead; production commands skipped',
              'samples': samples, 'summary': {}}
    for label in variants:
        rows = [r for r in samples if r['variant'] == label]
        names = sorted({k for r in rows for k in r['steps_ms']})
        def summarize(values):
            return {'median_ms': round(statistics.median(values), 3),
                    'range_ms': [round(min(values), 3), round(max(values), 3)]}
        report['summary'][label] = {'total': summarize([r['total_ms'] for r in rows]),
            'steps': {k: summarize([r['steps_ms'][k] for r in rows if k in r['steps_ms']]) for k in names}}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
