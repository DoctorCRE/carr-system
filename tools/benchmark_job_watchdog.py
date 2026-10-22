#!/usr/bin/env python3
"""Compare full scans using a private tape of real, read-only provider responses."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import job_watchdog


class ReplayEffects:
    def __init__(self, *args):
        pass

    def report(self, finding):
        return {}

    def recover(self, finding):
        return {}


def configuration():
    config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
    config['repositories'] = ['jbookout/carr-system']
    config['repository_roots'] = {'jbookout/carr-system': str(ROOT)}
    config['vendor_release_watches'] = []
    config['paths']['merge_queue'] = 'queue.txt'
    config['paths']['queue_logs'] = []
    config['paths']['release_log'] = 'release.log'
    config['paths'].pop('salesforce_refresh', None)
    config['actions'] = {key: 'report' for key in config['actions']}
    config['actions']['file_defects'] = False
    return config


def load_baseline(path):
    spec = importlib.util.spec_from_file_location('watchdog_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SOURCE = ROOT
    return module


def capture(module, path):
    tape = {'captured_at': time.time(), 'commands': {}}
    original = module.command
    last_call = 0
    def record(argv, config, cwd=None):
        nonlocal last_call
        if argv[0] == 'gh':
            time.sleep(max(0, 2 - (time.monotonic() - last_call)))
            last_call = time.monotonic()
        elif argv[:2] != ['git', 'show']:
            raise AssertionError('capture refuses non-read transport')
        started = time.perf_counter()
        output = original(argv, config, cwd)
        seconds = time.perf_counter() - started
        if 'pulls?state=open' in argv[-1] or 'branches?' in argv[-1]:
            limit = 6 if 'pulls?state=open' in argv[-1] else 12
            output = json.dumps([[item for page in json.loads(output) for item in page][:limit]])
        tape['commands'][json.dumps(argv)] = {'output': output, 'seconds': seconds}
        print(json.dumps({'captured_commands': len(tape['commands'])}), flush=True)
        return output
    with tempfile.TemporaryDirectory(dir=path.parent) as folder, \
         patch.object(module.sys, 'platform', 'linux'), patch.object(module, 'command', side_effect=record):
        facts = module.collect(Path(folder), configuration(), tape['captured_at'])
        if facts['errors']:
            raise RuntimeError('capture had collection errors')
        tape['counts'] = {key: len(facts[key]) for key in ('jobs', 'prs', 'branches', 'errors')}
    path.write_text(json.dumps(tape))
    print(json.dumps({'capture': str(path), 'counts': tape['counts'], 'commands': len(tape['commands'])}), flush=True)


def replay(module, tape, scans):
    now = tape['captured_at']
    calls = 0
    replayed_seconds = 0
    warming = True
    def transport(argv, config, cwd=None):
        nonlocal calls, replayed_seconds
        entry = tape['commands'][json.dumps(argv)]
        if warming:
            return entry['output']
        calls += 1
        replayed_seconds += entry['seconds']
        time.sleep(entry['seconds'])
        return entry['output']
    durations, findings, errors = [], [], 0
    with tempfile.TemporaryDirectory(dir=ROOT / 'out/watchdog-speed') as folder:
        root = Path(folder)
        config = configuration()
        with patch.object(module.sys, 'platform', 'linux'), \
             patch.object(module.time, 'time', return_value=now), \
             patch.object(module, 'command', side_effect=transport), \
             patch.object(module, 'Effects', ReplayEffects), contextlib.redirect_stdout(io.StringIO()):
            if module.scan(root, config):
                raise AssertionError('warmup scan failed')
        warming = False
        for index in range(scans):
            clock = now + (index + 1) * 120
            with patch.object(module.sys, 'platform', 'linux'), \
                 patch.object(module.time, 'time', return_value=clock), \
                 patch.object(module, 'command', side_effect=transport), \
                 patch.object(module, 'Effects', ReplayEffects), contextlib.redirect_stdout(io.StringIO()):
                started = time.perf_counter()
                result = module.scan(root, config)
                durations.append(time.perf_counter() - started)
                errors += result != 0
                rows = module.read_latest(root / config['paths']['findings'])
                findings.append(sorted((row['key'], row['reason']) for row in rows.values() if not row.get('cleared_at')))
    return {'durations_seconds': durations, 'total_seconds': sum(durations),
            'job_hours_per_day_at_120s': statistics.mean(durations) * 720 / 3600,
            'transport_calls': calls, 'replayed_transport_seconds': replayed_seconds,
            'errors': errors}, findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--tape', type=Path, required=True)
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--scans', type=int, default=5)
    args = parser.parse_args()
    baseline = load_baseline(args.baseline)
    if args.capture:
        capture(baseline, args.tape)
    tape = json.loads(args.tape.read_text())
    report = {'scope': 'steady-state replay after an untimed cache warmup; sample of six real public PRs and twelve branches with measured transport delays; empty isolated local state; no launchd, cleanup inventory, production effects or live stores',
              'counts': tape['counts'], 'runs': [], 'scans_per_run': args.scans}
    for run in range(args.runs):
        sides = [('before', baseline), ('after', job_watchdog)]
        if run % 2:
            sides.reverse()
        results = {}
        for side, module in sides:
            result, findings = replay(module, tape, args.scans)
            results[side] = (result, findings)
            print(json.dumps({'run': run + 1, 'side': side, **result}), flush=True)
        if results['before'][1] != results['after'][1]:
            raise AssertionError('finding or auto-clear parity failed')
        if any(results[side][0]['errors'] for side in results):
            raise AssertionError('replay scan failed')
        report['runs'].append({side: result[0] for side, result in results.items()})
    report['finding_parity'] = True
    report['summary'] = {}
    for side in ('before', 'after'):
        means = [statistics.mean(run[side]['durations_seconds']) for run in report['runs']]
        report['summary'][side] = {'median_mean_scan_seconds': statistics.median(means),
            'range_mean_scan_seconds': [min(means), max(means)],
            'median_job_hours_per_day_at_120s': statistics.median(means) * 720 / 3600}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()
