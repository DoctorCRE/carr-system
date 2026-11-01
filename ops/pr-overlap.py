#!/usr/bin/env python3
"""Find open pull requests touching the supplied repository paths."""
import argparse
import json
import subprocess
import sys


def fetch_pages(endpoint):
    result = subprocess.run(
        ['gh', 'api', endpoint + '?per_page=100', '--paginate', '--slurp'],
        capture_output=True, text=True, check=True, timeout=60,
    )
    pages = json.loads(result.stdout)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError('unexpected GitHub response')
    return [item for page in pages for item in page]


def overlaps(a, b):
    a, b = a.rstrip('/'), b.rstrip('/')
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', choices=['DoctorCRE/carr-system', 'jbookout/doctorcre-app'])
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    try:
        prs = fetch_pages(f'repos/{args.repo}/pulls')
        matches = []
        for pr in prs:
            files = fetch_pages(f"repos/{args.repo}/pulls/{pr['number']}/files")
            paths = [name for f in files for name in (f['filename'], f.get('previous_filename'))
                     if name and any(overlaps(name, p) for p in args.paths)]
            if paths:
                matches.append((pr['html_url'], paths))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        print(f'Warning: could not check open PR overlap ({exc}); allowing dispatch.', file=sys.stderr)
        return 0
    if not matches:
        print('No overlap.')
        return 0
    for url, paths in matches:
        print(url)
        for path in paths:
            print(f'  {path}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
