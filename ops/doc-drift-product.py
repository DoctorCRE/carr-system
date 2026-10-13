"""CARR dependency projection, public instruction-report artifact and drift summary."""
import argparse
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


def summary(report):
    findings = report['findings']
    files = sorted({f['file'] for f in findings})
    lines = [f"## Instruction drift at `{report['source_sha'][:12]}`", '',
             f"{len(findings)} stale claims in {len(files)} of {len(report['files_checked'])} instruction files. "
             'Owner: orchestrator. Repair the instruction, then rerun `ops/ci.sh --doc-drift`.', '']
    for f in findings:
        candidate = f"; candidate `{f['suggestion']}`" if f.get('suggestion') else ''
        lines.append(f"- `{f['file']}:{f['line']}` missing {f['kind']} `{f['target']}`{candidate}")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report', type=Path)
    parser.add_argument('--output', type=Path, help='write the public artifact')
    parser.add_argument('--summary', type=Path, help='append the drift summary; exit 1 when drift exists')
    args = parser.parse_args()
    if not args.report:
        verify_dependencies(args.root)
        return 0
    report = json.loads(args.report.read_text())
    if args.summary:
        with args.summary.open('a') as handle:
            handle.write(summary(report))
        return 1 if report['findings'] else 0
    if not args.output or args.report.resolve() == args.output.resolve():
        parser.error('artifact publication requires a distinct output path')
    args.output.write_text(json.dumps(public_report(report), indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
