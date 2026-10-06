#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from datetime import datetime
import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import zipfile


def api(path):
    return subprocess.check_output(['gh', 'api', 'repos/jbookout/carr-system/' + path], timeout=60)


def quantile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def measure(root, cached=False):
    runs_by_lane = {}
    for lane, workflow, query in [('main', 'main-canary.yml', ''), ('pr', 'ci.yml', '&event=pull_request')]:
        path = root / f'{lane}-runs.json'
        if not cached:
            path.write_bytes(api(f'actions/workflows/{workflow}/runs?per_page=30{query}'))
        runs_by_lane[lane] = json.loads(path.read_bytes())['workflow_runs']
    def fetch(run):
        folder = root / str(run['id'])
        folder.mkdir(exist_ok=True)
        (folder / 'jobs.json').write_bytes(api(f'actions/runs/{run["id"]}/jobs?per_page=100'))
        if run['status'] == 'completed':
            (folder / 'logs.zip').write_bytes(api(f'actions/runs/{run["id"]}/logs'))
    if not cached:
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(fetch, [r for runs in runs_by_lane.values() for r in runs]))
    report = {'method': 'Nearest-rank p50/p90 in seconds; finished class timings only, including finished work in failed or cancelled runs. Unfinished classes are excluded.', 'lanes': {}}
    for lane, runs in runs_by_lane.items():
        values, gates, samples = defaultdict(list), defaultdict(list), []
        missing = []
        total = []
        for run in runs:
            folder = root / str(run['id'])
            if run['conclusion'] == 'success':
                jobs = json.loads((folder / 'jobs.json').read_bytes())['jobs']
                end = max(datetime.fromisoformat(j['completed_at']) for j in jobs if j['completed_at'])
                total.append((end - datetime.fromisoformat(run['run_started_at'])).total_seconds())
            if not zipfile.is_zipfile(folder / 'logs.zip'):
                missing.append(run['id'])
                continue
            with zipfile.ZipFile(folder / 'logs.zip') as logs:
                for name in logs.namelist():
                    if '/' in name:
                        continue
                    for line in logs.read(name).decode(errors='replace').splitlines():
                        if 'ci-timing:' in line:
                            for cls, seconds in re.findall(r'(\w+)=(\d+)s', line):
                                values[cls].append(int(seconds))
                                samples.append({'run_id':run['id'],'class':cls,'seconds':int(seconds)})
                        if 'db-gate-timing:' in line:
                            for gate, seconds in re.findall(r'([\w-]+)=(\d+)s', line):
                                gates[gate].append(int(seconds))
        def summary(items):
            return {name: {'n':len(v),'p50':quantile(v,.5),'p90':quantile(v,.9)} for name,v in items.items()}
        report['lanes'][lane] = {'run_ids':[r['id'] for r in runs],
            'window':[min(r['created_at'] for r in runs),max(r['created_at'] for r in runs)],
            'outcomes':dict(Counter(r['conclusion'] or r['status'] for r in runs)),
            'missing_logs':missing,'classes':summary(values),'db_gates':summary(gates),
            'successful_run_seconds':{'n':len(total),'p50':quantile(total,.5),'p90':quantile(total,.9)},
            'samples':samples}
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--cached',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.cache.mkdir(parents=True,exist_ok=True)
    report=measure(args.cache,args.cached)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    for lane,data in report['lanes'].items():
        print(lane,data['outcomes'],data['successful_run_seconds'])
        for cls,row in data['classes'].items():print(cls,row)

if __name__=='__main__':main()
