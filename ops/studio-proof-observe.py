#!/usr/bin/env python3
"""Observe one supplied Studio run receipt without starting or refreshing a job."""
import argparse
import json
from pathlib import Path
import re
import sys


def diagnostic_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt-file', type=Path, required=True)
    parser.add_argument('--service', required=True)
    parser.add_argument('--runtime-sha', required=True)
    parser.add_argument('--count', required=True, help='Dot-separated path within receipt counts')
    parser.add_argument('--minimum', type=int, required=True)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[0-9a-f]{40}', args.runtime_sha) or args.minimum < 0:
            raise ValueError('full runtime SHA and nonnegative minimum required')
        with args.receipt_file.open('rb') as stream:
            data = stream.read(1048577)
        if len(data) > 1048576:
            raise ValueError('receipt exceeds 1 MiB')
        prefix = args.service + '-receipt '
        text = data.decode().strip()
        if not text.startswith(prefix):
            raise ValueError('expected one completed service receipt')
        receipt = json.loads(text[len(prefix):])
        if receipt['state'] != 'succeeded' or receipt['runtime_sha'] != args.runtime_sha:
            raise ValueError('successful receipt for expected full runtime SHA required')
        count = receipt['counts']
        for key in args.count.split('.'):
            count = count[key]
        if type(count) is not int or count < args.minimum:
            raise ValueError('receipt count does not meet declared minimum')
        print('Studio proof OBSERVED: expected runtime and count predicate matched; no trusted receipt ingested')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Studio proof BLOCKED: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        return 1


def canonical_main(argv):
    from dataclasses import asdict
    from datetime import datetime, timezone
    from lib.studio_proof import Requirement, Source, acquire_evidence, observe, timestamp
    parser = argparse.ArgumentParser(description='Acquire canonical runs; hold unavailable installation/target facts')
    parser.add_argument('--service', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--environment', choices=['local', 'production', 'staging', 'rehearsal'], required=True)
    parser.add_argument('--runtime-sha', required=True)
    parser.add_argument('--merge-sha', required=True)
    parser.add_argument('--requirement-id', required=True)
    parser.add_argument('--since', required=True)
    parser.add_argument('--count', required=True)
    parser.add_argument('--minimum', type=int, required=True)
    parser.add_argument('--max-age-seconds', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        since = timestamp(args.since)
        now = datetime.now(timezone.utc)
        requirement = Requirement(args.requirement_id, args.service, args.key, args.environment,
                                  'studio', args.count, args.minimum, args.max_age_seconds)
        if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (args.runtime_sha, args.merge_sha)):
            raise ValueError('full source SHAs required')
        source = Source('DoctorCRE/carr-system', args.merge_sha, args.runtime_sha, 'studio')
        evidence = acquire_evidence(requirement, since, now)
        result = observe(requirement, source, evidence, now)
        print(json.dumps({'schema': 'carr-studio-observation.v1', **asdict(result)},
                         default=lambda value: value.isoformat()))
        return 0 if result.state == 'live-proven' else 1
    except (TypeError, ValueError):
        print(json.dumps({'schema': 'carr-studio-observation.v1', 'state': 'held',
                          'reasons': ['invalid bounded source/requirement/interval']}))
        return 1


def main():
    if sys.argv[1:2] == ['canonical']:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        return canonical_main(sys.argv[2:])
    return diagnostic_main()


if __name__ == '__main__':
    sys.exit(main())
