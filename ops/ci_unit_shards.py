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
COUNT = 5
WATCHDOG_MS = 600000
BUDGET_MS = WATCHDOG_MS * 4 // 5


def suite_files(root):
    return json.loads(subprocess.check_output(['node', str(root / 'mcp-server/suite-discovery.mjs')],
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
    fallback = max((row['milliseconds'] for row in measured.values()), default=WATCHDOG_MS)
    records = {file: measured.get(file, {'milliseconds': fallback, 'serial': True}) for file in suite_files(root)}
    return records, data.get('snapshot_template_ms', 0)


def plan(root=ROOT, count=COUNT):
    records, setup_ms = durations(root)
    shards = [[] for _ in range(count)]
    for file in sorted(records, key=lambda file: (
            -records[file]['milliseconds'] / (1 if records[file]['serial'] else 4), file)):
        index = min(range(count), key=lambda index: (estimate(shards[index] + [file], records, setup_ms), len(shards[index]), index))
        shards[index].append(file)
    if any(not suites for suites in shards):
        raise ValueError('unit shard has zero suites')
    return [sorted(files) for files in shards]


def regenerate(logs, source_run, output, root=ROOT):
    samples = {}
    setup_samples = []
    for log in logs:
        setup_samples.extend(map(int, re.findall(r'suite-timing: snapshot-template (\d+)ms', log.read_text())))
        for file, milliseconds in re.findall(r'suite-timing: (\S+\.test\.(?:mjs|js)) (\d+)ms', log.read_text()):
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
    if sorted(ids) != list(range(1, COUNT + 1)):
        raise ValueError(f'require exactly one receipt for each of {COUNT} shards')
    for receipt in receipts:
        actual = receipt['suites']
        if not actual:
            raise ValueError(f"shard {receipt['shard']} ran zero suites")
        wanted = expected[receipt['shard'] - 1]
        if Counter(actual) != Counter(wanted):
            missing = sorted((Counter(wanted) - Counter(actual)).elements())
            extra = sorted((Counter(actual) - Counter(wanted)).elements())
            raise ValueError(f"shard {receipt['shard']} coverage mismatch: missing={missing}, unexpected={extra}")
    print(f'Unit shard coverage: {sum(map(len, expected))} suites exactly once across {COUNT} nonempty shards')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('action', choices=('plan', 'verify', 'timings'))
    parser.add_argument('receipts', nargs='*', type=Path)
    parser.add_argument('--source-run', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'plan':
            print(json.dumps(plan(args.root)))
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
