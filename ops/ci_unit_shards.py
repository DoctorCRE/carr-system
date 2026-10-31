#!/usr/bin/env python3
"""Partition MCP suites and verify execution receipts against current discovery."""
import argparse
from collections import Counter
import heapq
import statistics
import subprocess
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COUNT = 4


def suite_files(root):
    return sorted(file.name for file in (root / 'mcp-server/test').iterdir()
                  if file.is_file() and re.search(r'\.test\.(?:js|mjs)$', file.name))


def estimate(files, records):
    lanes = [0] * 4
    serial = 0
    for file in sorted(files):
        record = records[file]
        if record['serial']:
            serial += record['milliseconds']
        else:
            heapq.heapreplace(lanes, lanes[0] + record['milliseconds'])
    return serial + max(lanes)


def plan(root=ROOT):
    timing_file = root / 'ops/config/ci-unit-durations.json'
    measured = json.loads(timing_file.read_text())['suites'] if timing_file.exists() else {}
    fallback = statistics.median(row['milliseconds'] for row in measured.values()) if measured else 1000
    records = {file: measured.get(file, {'milliseconds': fallback, 'serial': True}) for file in suite_files(root)}
    shards = [[] for _ in range(COUNT)]
    for file in sorted(records, key=lambda file: (
            -records[file]['milliseconds'] / (1 if records[file]['serial'] else 4), file)):
        index = min(range(COUNT), key=lambda index: (estimate(shards[index] + [file], records), len(shards[index]), index))
        shards[index].append(file)
    if any(not suites for suites in shards):
        raise ValueError('unit shard has zero suites')
    return [sorted(files) for files in shards]


def regenerate(logs, source_run, output, root=ROOT):
    samples = {}
    for log in logs:
        for file, milliseconds in re.findall(r'suite-timing: (\S+\.test\.(?:mjs|js)) (\d+)ms', log.read_text()):
            if file in samples:
                raise ValueError(f'duplicate suite timing: {file}')
            samples[file] = int(milliseconds)
    expected = suite_files(root)
    if set(samples) != set(expected):
        raise ValueError(f'timings must cover current suites: missing={sorted(set(expected) - samples.keys())}, '
                         f'unexpected={sorted(samples.keys() - set(expected))}')
    script = """import {suiteBatches} from './mcp-server/run-tests.mjs';
import {readdir,readFile} from 'node:fs/promises';
const files=await readdir('mcp-server/test'), browsers=[];
for(const file of files.filter(file=>/\\.test\\.(?:mjs|js)$/.test(file)))
  if((await readFile('mcp-server/test/'+file,'utf8')).includes('chrome-launch.mjs')) browsers.push(file);
console.log(JSON.stringify(suiteBatches(files,browsers)));"""
    batches = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=root, text=True))
    serial = {file for batch in batches if len(batch) == 1 for file in batch}
    data = {'source_run': source_run, 'suites': {file: {'milliseconds': samples[file], 'serial': file in serial}
                                              for file in expected}}
    rows = ',\n'.join(f'    {json.dumps(file)}: {json.dumps(record)}' for file, record in data['suites'].items())
    output.write_text('{\n  "source_run": ' + str(source_run) + ',\n  "suites": {\n' + rows + '\n  }\n}\n')


def verify(receipts, root=ROOT):
    expected = plan(root)
    ids = [receipt['shard'] for receipt in receipts]
    if sorted(ids) != list(range(1, COUNT + 1)):
        raise ValueError('require exactly one receipt for each of four shards')
    for receipt in receipts:
        actual = receipt['suites']
        if not actual:
            raise ValueError(f"shard {receipt['shard']} ran zero suites")
        wanted = expected[receipt['shard'] - 1]
        if Counter(actual) != Counter(wanted):
            missing = sorted((Counter(wanted) - Counter(actual)).elements())
            extra = sorted((Counter(actual) - Counter(wanted)).elements())
            raise ValueError(f"shard {receipt['shard']} coverage mismatch: missing={missing}, unexpected={extra}")
    print(f'Unit shard coverage: {sum(map(len, expected))} suites exactly once across four nonempty shards')


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
