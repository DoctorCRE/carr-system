"""CARR dependency projection, public instruction-report artifact and drift summary."""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sys


def verify_dependencies(root):
    authority = {line.split('#', 1)[0].strip() for line in (root / 'requirements.txt').read_text().splitlines()}
    projection = [line.split('#', 1)[0].strip() for line in
                  (root / 'scripts/doc-drift/requirements.txt').read_text().splitlines()]
    if any(line and line not in authority for line in projection):
        raise ValueError('instruction checker dependency projection differs from requirements.txt')


def public_report(report):
    public = copy.deepcopy(report)
    for collection in ('claims', 'unchecked'):
        public[collection] = [
            {key: claim[key] for key in ('file', 'line', 'kind', 'unchecked')}
            if claim.get('unchecked') else claim
            for claim in public.get(collection, [])
        ]
    return public


def claim_key(finding):
    return finding['file'], finding['kind'], finding['target']


def baseline(report):
    findings = sorted(claim_key(f) for f in report['findings'])
    return dict(schema='doc-drift-baseline/v1', source_sha=report['source_sha'],
                findings=[dict(file=file, kind=kind, target=target) for file, kind, target in findings])


def new_findings(report, known):
    allowed = Counter(claim_key(f) for f in known['findings'])
    fresh = []
    for finding in report['findings']:
        if allowed[claim_key(finding)]:
            allowed[claim_key(finding)] -= 1
        else:
            fresh.append(finding)
    return fresh


def summary(report, known):
    fresh = new_findings(report, known)
    lines = [f"## Instruction drift at `{report['source_sha'][:12]}`", '',
             f"{len(fresh)} new stale claims beyond the baseline across {len(report['files_checked'])} instruction files. "
             f"{len(report['findings']) - len(fresh)} known stale claims are in `scripts/doc-drift/baseline.json`. "
             'Owner: orchestrator. Repair the instruction, then rerun `ops/ci.sh --doc-drift`.', '']
    for f in fresh:
        candidate = f"; candidate `{f['suggestion']}`" if f.get('suggestion') else ''
        lines.append(f"- `{f['file']}:{f['line']}` missing {f['kind']} `{f['target']}`{candidate}")
    return '\n'.join(lines) + '\n', bool(fresh)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report', type=Path)
    parser.add_argument('--output', type=Path, help='write the public artifact')
    parser.add_argument('--baseline', type=Path, help='known stale claims the summary does not fail on')
    parser.add_argument('--summary', type=Path, help='append the drift summary; exit 1 on drift beyond --baseline')
    parser.add_argument('--write-baseline', type=Path, help='record every stale claim in the report as known')
    args = parser.parse_args()
    if not args.report:
        verify_dependencies(args.root)
        return 0
    report = json.loads(args.report.read_text())
    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(baseline(report), indent=2) + '\n')
        return 0
    if args.summary:
        if not args.baseline:
            parser.error('the drift summary requires --baseline')
        text, drifted = summary(report, json.loads(args.baseline.read_text()))
        with args.summary.open('a') as handle:
            handle.write(text)
        return 1 if drifted else 0
    if not args.output or args.report.resolve() == args.output.resolve():
        parser.error('artifact publication requires a distinct output path')
    args.output.write_text(json.dumps(public_report(report), indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
