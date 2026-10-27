#!/usr/bin/env python3
"""Fast-forward a checkout, reporting dirty files and retrying transient ref locks."""
import subprocess
import sys
import time


def main(repo, git='/usr/bin/git'):
    status = subprocess.run([git, '-C', repo, 'status', '--porcelain', '--untracked-files=no'], capture_output=True, text=True)
    if status.returncode:
        sys.stderr.write(status.stderr)
        return status.returncode
    if status.stdout:
        sys.stderr.write('checkout-pull: tracked files are dirty:\n' + status.stdout)
        return 1
    for attempt in range(3):
        result = subprocess.run([git, '-C', repo, 'pull', '--ff-only', '--quiet'], capture_output=True, text=True)
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.returncode == 0:
            return 0
        sys.stderr.write(result.stderr)
        if 'cannot lock ref' not in result.stderr or attempt == 2:
            return result.returncode
        sys.stderr.write('checkout-pull: retrying after ref lock failure\n')
        time.sleep(1)
    return 1


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
