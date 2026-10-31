#!/usr/bin/env python3
"""Fast-forward main without sharing FETCH_HEAD with concurrent fetchers."""
from pathlib import Path
import subprocess
import sys


def update(repo: Path, *, runner=subprocess.run) -> int:
    git = ['/usr/bin/git', '-C', str(repo)]
    branch = runner(git + ['symbolic-ref', '--quiet', '--short', 'HEAD'],
                    capture_output=True, text=True)
    if branch.returncode or branch.stdout.strip() != 'main':
        print('checkout-pull: expected main; checkout unchanged', file=sys.stderr)
        return 1
    for attempt in range(2):
        fetched = runner(git + ['fetch', '--quiet', '--no-write-fetch-head', 'origin',
                                '+refs/heads/main:refs/remotes/origin/main'],
                         capture_output=True, text=True)
        if not fetched.returncode:
            break
        if attempt == 0 and 'cannot lock ref' in fetched.stderr:
            continue
        print(fetched.stderr, file=sys.stderr, end='')
        return fetched.returncode
    return runner(git + ['merge', '--ff-only', '--quiet', 'refs/remotes/origin/main']).returncode


if __name__ == '__main__':
    raise SystemExit(update(Path(sys.argv[1])))
