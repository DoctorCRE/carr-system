#!/usr/bin/env python3
"""Partition MCP suites and verify execution receipts against current discovery."""
import argparse
from collections import Counter
import heapq
import subprocess
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAX_COUNT = 16
POSTGRES_PROFILE = "both"
WATCHDOG_MS = 600000
BUDGET_MS = WATCHDOG_MS * 4 // 5
DEFAULT_SUITE_MS = 60000


def suite_files(root):
    return json.loads(subprocess.check_output(['node', str(root / 'mcp-server/suite-discovery.ts')],
                                              cwd=root, text=True))


def estimate(files, records, setup_ms=0):
    lanes = [0] * 4
    serial = 0
    for file in sorted(files):
        record = records[file]
        if record['serial']:
            serial += record['milliseconds']
        else:
            heapq.heapreplace(lanes, lanes[0] + record['milliseconds'])
    return setup_ms + serial + max(lanes)


def durations(root=ROOT):
    timing_file = root / 'ops/config/ci-unit-durations.json'
    data = json.loads(timing_file.read_text()) if timing_file.exists() else {}
    measured = data.get('suites', {})
    records = {file: measured.get(file, {'milliseconds': DEFAULT_SUITE_MS, 'serial': True, 'unmeasured': True}) for file in suite_files(root)}
    return records, data.get('snapshot_template_ms', 0)


def _assign(records, setup_ms, count):
    if count < 1:
        raise ValueError("shard count must be positive")
    shards = [[] for _ in range(count)]
    for file in sorted(records, key=lambda file: (
            -records[file]['milliseconds'] / (1 if records[file]['serial'] else 4), file)):
        index = min(range(count), key=lambda index: (estimate(shards[index] + [file], records, setup_ms), len(shards[index]), index))
        shards[index].append(file)
    if any(not suites for suites in shards):
        raise ValueError('unit shard has zero suites')
    return [sorted(files) for files in shards]


def plan(root=ROOT, count=None):
    records, setup = durations(root)
    if count is not None:
        return _assign(records, setup, count)
    for file, row in records.items():
        predicted = setup + row['milliseconds']
        if predicted > BUDGET_MS:
            raise ValueError(f'unit suite {file} predicted {predicted}ms exceeds {BUDGET_MS}ms budget '
                             f'(80% of {WATCHDOG_MS}ms watchdog); remeasure or split the suite')
    unknown = {file: row for file, row in records.items() if row.get('unmeasured')}
    measured = {file: row for file, row in records.items() if not row.get('unmeasured')}
    predicted = 0
    for cardinality in range(bool(measured), min(MAX_COUNT - bool(unknown), len(measured)) + 1):
        shards = _assign(measured, setup, cardinality) if measured else []
        if unknown:
            shards += _assign(unknown, setup, min(MAX_COUNT - cardinality, len(unknown)))
        predicted = max(estimate(files, records, setup) for files in shards)
        if predicted <= BUDGET_MS:
            return shards
    raise ValueError(f'unit workload exceeds {BUDGET_MS}ms budget at the {MAX_COUNT}-shard cap '
                     f'(predicted {predicted}ms); remeasure or split slow suites')


def regenerate(logs, source_run, output, root=ROOT):
    samples = {}
    setup_samples = []
    for log in logs:
        setup_samples.extend(map(int, re.findall(r'suite-timing: snapshot-template (\d+)ms', log.read_text())))
        for file, milliseconds in re.findall(r'suite-timing: (\S+\.test\.(?:mjs|js|ts)) (\d+)ms', log.read_text()):
            if file in samples:
                raise ValueError(f'duplicate suite timing: {file}')
            samples[file] = int(milliseconds)
    expected = suite_files(root)
    if set(samples) != set(expected):
        raise ValueError(f'timings must cover current suites: missing={sorted(set(expected) - samples.keys())}, '
                         f'unexpected={sorted(samples.keys() - set(expected))}')
    script = """import {suiteBatches} from './mcp-server/run-tests.mjs';
import {readFile} from 'node:fs/promises';
const files=JSON.parse(process.argv[1]), browsers=[], postgres=[];
for(const file of files) {
  const source=await readFile('mcp-server/test/'+file,'utf8');
  if(source.includes('chrome-launch.mjs')) browsers.push(file);
  if(/disposable-postgres\\.mjs|postgres-database\\.mjs|initdb/.test(source)) postgres.push(file);
}
console.log(JSON.stringify(suiteBatches(files,browsers,postgres)));"""
    batches = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script, json.dumps(expected)],
                                                cwd=root, text=True))
    serial = {file for batch in batches if len(batch) == 1 for file in batch}
    data = {'source_run': source_run, 'snapshot_template_ms': max(setup_samples, default=0), 'suites': {file: {'milliseconds': samples[file], 'serial': file in serial}
                                              for file in expected}}
    rows = ',\n'.join(f'    {json.dumps(file)}: {json.dumps(record)}' for file, record in data['suites'].items())
    output.write_text('{\n  "source_run": ' + str(source_run) + ',\n  "snapshot_template_ms": ' + str(data['snapshot_template_ms']) + ',\n  "suites": {\n' + rows + '\n  }\n}\n')


def verify(receipts, root=ROOT):
    expected = plan(root)
    ids = [receipt['shard'] for receipt in receipts]
    if sorted(ids) != list(range(1, len(expected) + 1)):
        raise ValueError(f'require exactly one receipt for each of {len(expected)} shards')
    for receipt in receipts:
        actual = receipt['suites']
        if not actual:
            raise ValueError(f"shard {receipt['shard']} ran zero suites")
        wanted = expected[receipt['shard'] - 1]
        if Counter(actual) != Counter(wanted):
            missing = sorted((Counter(wanted) - Counter(actual)).elements())
            extra = sorted((Counter(actual) - Counter(wanted)).elements())
            raise ValueError(f"shard {receipt['shard']} coverage mismatch: missing={missing}, unexpected={extra}")
    print(f'Unit shard coverage: {sum(map(len, expected))} suites exactly once across {len(expected)} nonempty shards')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('action', choices=('plan', 'capabilities', 'verify', 'timings', 'budget', 'matrix'))
    parser.add_argument('receipts', nargs='*', type=Path)
    parser.add_argument('--source-run', type=int)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--count', type=int, help='fixed-count diagnostic plan; does not admit execution')
    args = parser.parse_args()
    if args.count is not None and args.action != 'plan':
        parser.error('--count is only for diagnostic plan')
    try:
        if args.action == 'capabilities':
            print(json.dumps([{'shard': index, 'postgres_profile': POSTGRES_PROFILE,
                               'majors': [17, 18]}
                              for index, _ in enumerate(plan(args.root), 1)]))
        elif args.action == 'plan':
            print(json.dumps(plan(args.root, args.count)))
        elif args.action == 'budget':
            records, setup = durations(args.root)
            print(json.dumps([{'shard': index, 'predicted_ms': estimate(files, records, setup),
                               'budget_ms': BUDGET_MS}
                              for index, files in enumerate(plan(args.root), 1)]))
        elif args.action == 'matrix':
            shards = plan(args.root)
            print(json.dumps({'include': [{'shard': index, 'count': len(shards)}
                                          for index, _ in enumerate(shards, 1)]}))
        elif args.action == 'timings':
            if not (args.source_run and args.output):
                parser.error('timings requires --source-run and --output')
            regenerate(args.receipts, args.source_run, args.output, args.root)
        else:
            verify([json.loads(file.read_text()) for file in args.receipts], args.root)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
