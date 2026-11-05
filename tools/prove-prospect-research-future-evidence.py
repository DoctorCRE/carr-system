#!/usr/bin/env python3
"""Prove bounded research readback. Fixtures never contact production. Failures exit nonzero."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1048576


class AssertionFailure(ValueError):
    pass


def require(condition, assertion):
    if not condition:
        raise AssertionFailure(assertion)


def timestamp(value):
    require(isinstance(value, str), 'observation_contract')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('observation_contract') from None
    require(result.tzinfo is not None, 'observation_contract')
    return result


class Proof:
    def __init__(self, args):
        self.args = args
        self.deadline = time.monotonic() + args.timeout_seconds
        self.reads = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'total_deadline')
        return remaining

    def process(self, command):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=self.remaining())
        self.remaining()
        require(result.returncode == 0, 'process_or_auth')
        require(len(result.stdout) <= MAX_BYTES, 'response_size')
        return result.stdout.decode()

    def fixture(self, *args):
        return self.process([sys.executable, str(self.args.fixture), '--stub', *args])

    def call(self, verb, args):
        self.reads += 1
        require(self.reads <= 21, 'record_read_cap')
        encoded = json.dumps(args)
        raw = self.fixture('call', verb, encoded) if self.args.fixture else self.process(
            [str(ROOT / 'run.sh'), 'call', verb, encoded])
        result = json.loads(raw)
        require(isinstance(result, dict) and not result.get('error') and result.get('ok') is not False, 'read_contract')
        return result

    def run(self):
        if self.args.fixture:
            release = json.loads(self.fixture('release'))
        else:
            release = json.loads(self.process(['curl', '--fail', '--silent', '--show-error',
                '--max-time', str(self.remaining()), '--max-filesize', str(MAX_BYTES),
                'https://api.doctorcre.com/release']))
        self.remaining()
        require(isinstance(release, dict) and release.get('ok') is True
                and release.get('env', {}).get('value') == 'production', 'release_contract')
        sha = release.get('git_sha', {}).get('value')
        require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha), 'release_revision')
        merge = self.args.merge_sha or self.process(['git', 'rev-parse', 'HEAD']).strip()
        require(re.fullmatch('[0-9a-f]{40}', merge), 'merge_revision')
        self.process(['git', 'merge-base', '--is-ancestor', merge, sha])
        board = self.call('lead-board', {})
        leads = board.get('leads')
        require(isinstance(leads, list) and leads, 'usable_leads')
        ids = []
        for row in leads[:self.args.max_records]:
            require(isinstance(row, dict) and isinstance(row.get('id'), str), 'lead_contract')
            UUID(row['id'])
            require(row['id'] not in ids, 'unique_leads')
            ids.append(row['id'])
        verified = relevant = findings = 0
        for lead_id in ids:
            reply = self.call('prospect-research', {'subject': lead_id})
            require(reply.get('schema_version') == 'prospect-research.v1' and reply.get('sent') is False, 'research_contract')
            research = reply.get('research')
            require(isinstance(research, dict) and research.get('coverage') == 'recorded_findings_only'
                    and research.get('external_search_performed') is False, 'research_coverage')
            evidence = research.get('evidence')
            require(isinstance(evidence, list) and len(evidence) <= 500, 'evidence_contract')
            now = datetime.now(timezone.utc)
            current = []
            for finding in evidence:
                require(isinstance(finding, dict) and all(type(finding.get(k)) is bool
                        for k in ('found', 'expired', 'proposes_correction'))
                        and isinstance(finding.get('kind'), str) and isinstance(finding.get('source'), str), 'finding_contract')
                findings += 1
                if finding['kind'] in ('verified', 'discrepancy') or finding['proposes_correction']:
                    relevant += 1
                try:
                    observed = timestamp(finding.get('observed_at'))
                except ValueError:
                    continue
                if not finding['expired'] and observed <= now:
                    current.append(finding)
            conflict = any(f['found'] and (f['kind'] == 'discrepancy' or f['proposes_correction']) for f in current)
            sourced = any(f['kind'] == 'verified' and f['found'] and f['source'].strip() for f in current)
            expected = 'needs_review' if conflict else 'verified' if sourced else 'needs_research'
            require(research.get('state') == expected, 'current_evidence_state')
            verified += expected == 'verified'
            self.remaining()
        print('PASS release_contains_merge')
        print('PASS current_evidence_state')
        print(f'counts records={len(ids)} findings={findings} relevant={relevant} verified={verified} reads={self.reads}')
        print('COVERAGE limited' if relevant == 0 else 'COVERAGE recorded_findings_only')


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='Live mode requires a checkout at the merged revision or --merge-sha. No retries. All failures exit nonzero.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live', action='store_true', help='Read production through the local actor wrapper.')
    mode.add_argument('--fixture', nargs='?', type=Path, const=ROOT / 'tools/test_prove_prospect_research_future_evidence.py',
                      help='Use a synthetic stub process. Default is the committed test fixture.')
    parser.add_argument('--timeout-seconds', type=float, default=30, help='Total deadline, greater than zero and at most 30.')
    parser.add_argument('--max-records', type=int, default=20, help='Exact lead reads, from 1 through 20.')
    parser.add_argument('--merge-sha', help='Full merged revision which the release must contain. Defaults to checkout HEAD.')
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= 30 or not 1 <= args.max_records <= 20:
        parser.error('budgets must be within documented bounds')
    try:
        Proof(args).run()
    except AssertionFailure as error:
        print(f'FAIL {error}', file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError):
        print('FAIL proof_assertions', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
