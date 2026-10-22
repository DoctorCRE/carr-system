#!/usr/bin/env python3
"""Measure complete scans against live read-only sources in isolated local ledgers."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'tools')]
import job_watchdog
import machine_cleanup
import scheduled_jobs


class ReadOnlyEffects:
    def __init__(self, *args):
        pass

    def report(self, finding):
        return {}

    def clear(self, finding):
        return {}


def load_baseline(path):
    spec = importlib.util.spec_from_file_location('watchdog_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SOURCE = ROOT
    return module


def summary(rows):
    durations = [row['seconds'] for row in rows]
    return {'ticks': len(rows), 'total_job_seconds': sum(durations),
            'mean_job_seconds_per_tick': statistics.mean(durations),
            'median_job_seconds_per_tick': statistics.median(durations),
            'range_seconds': [min(durations), max(durations)],
            'failed_ticks': sum(row['status'] != 'completed' for row in rows),
            'collection_error_ticks': sum(bool(row.get('collection_errors')) for row in rows)}


def ledger_rows(path):
    rows, started = [], None
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('key') != 'scan':
            continue
        if row['status'] == 'started':
            started = job_watchdog.epoch(row['at'])
        elif row['status'] in {'completed', 'failed'} and started is not None:
            rows.append({**row, 'seconds': job_watchdog.epoch(row['at']) - started})
            started = None
    return rows


def configuration(source, root):
    config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
    for key in ('registry', 'release_log'):
        config['paths'][key] = str(job_watchdog.path_at(source, config['paths'][key]))
    config['repository_roots']['jbookout/carr-system'] = str(root.resolve())
    config['actions'] = {key: 'report' for key in config['actions']}
    config['actions']['file_defects'] = False
    for relative in ('out/watchdog/pr-cache.json', 'out/watchdog/branch-dates.json',
                     'out/watchdog/vendor-releases.jsonl', 'out/machine-cleanup.json'):
        if (source / relative).exists():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=5)
    args = parser.parse_args()
    source = args.source_root.resolve()
    original_command = job_watchdog.command
    original_inventory = machine_cleanup.inventory_findings
    real_roots = job_watchdog.repository_roots()
    last_gh = 0
    metrics = {}
    def transport(argv, config, cwd=None):
        nonlocal last_gh
        if argv[0] == 'gh':
            time.sleep(max(0, 2 - (time.monotonic() - last_gh)))
            last_gh = time.monotonic()
            metrics['github_calls'] += 1
        elif argv[0] not in {'git', 'ps'}:
            raise AssertionError('read-only transport refuses ' + argv[0])
        if cwd and Path(cwd).resolve() in scratch_roots:
            cwd = source
        return original_command(argv, config, cwd)
    def schedule_check(snapshot=None, now=None, **kwargs):
        started = time.perf_counter()
        manifest = scheduled_jobs.native_manifest()
        rows = scheduled_jobs.report(manifest, scheduled_jobs.collect(manifest), now)
        metrics['scheduled_seconds'] += time.perf_counter() - started
        metrics['scheduled_observations'] += 1
        return rows
    def inventory(config, **kwargs):
        started = time.perf_counter()
        kwargs['roots'] = real_roots
        rows = original_inventory(config, **kwargs)
        metrics['cleanup_seconds'] += time.perf_counter() - started
        metrics['cleanup_inventories'] += 1
        return rows
    modules = {'before': load_baseline(args.baseline), 'after': job_watchdog}
    report = {'scope': 'Full live-source scans, all configured PRs/branches, launchd/git, cleanup inventory, registry and logs. Only effects are replaced by successful read-only receipts; all ledgers and caches are isolated. Not deployed-job savings. Sources may change between ticks.',
              'historical_recent_100': summary(ledger_rows(source / 'out/watchdog/runs.jsonl')[-100:]),
              'runs': {'before': [], 'after': []}}
    with tempfile.TemporaryDirectory(prefix='watchdog-measure-') as directory:
        roots = {side: Path(directory, side) for side in modules}
        scratch_roots = {root.resolve() for root in roots.values()}
        configs = {side: configuration(source, root) for side, root in roots.items()}
        for run in range(-1, args.runs):
            order = ('before', 'after') if run % 2 == 0 else ('after', 'before')
            for side in order:
                metrics = dict.fromkeys(('github_calls', 'scheduled_seconds', 'scheduled_observations',
                                         'cleanup_seconds', 'cleanup_inventories'), 0)
                module, root = modules[side], roots[side]
                started = time.perf_counter()
                with patch.object(module, 'command', side_effect=transport), \
                     patch.object(module, 'Effects', ReadOnlyEffects), \
                     patch.object(scheduled_jobs, 'check', side_effect=schedule_check), \
                     patch.object(machine_cleanup, 'inventory_findings', side_effect=inventory), \
                     contextlib.redirect_stdout(io.StringIO()):
                    exit_code = module.scan(root, configs[side])
                if metrics['scheduled_observations'] != 1 or metrics['cleanup_inventories'] != 1:
                    raise AssertionError('full scan must observe launchd/git and cleanup inventory')
                row = ledger_rows(root / 'out/watchdog/runs.jsonl')[-1]
                row.update(seconds=time.perf_counter() - started, exit_code=exit_code, **metrics)
                if run >= 0:
                    report['runs'][side].append(row)
                print(json.dumps({'side': side, 'warmup': run < 0, **row}), flush=True)
                args.output.write_text(json.dumps(report, indent=2) + '\n')
    report['summary'] = {side: summary(rows) for side, rows in report['runs'].items()}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()
