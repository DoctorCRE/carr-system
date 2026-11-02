#!/usr/bin/env python3
"""Arm a one-shot totals census through the configured intake snapshot lifecycle."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_intake import OUT
from lib.outlook_settings import SettingsError, load_settings


def main() -> int:
    try:
        if len(sys.argv) != 1:
            print('coverage_census_request_failed=usage', file=sys.stderr)
            return 2
        out = load_settings().repo / OUT
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(out, 0o700)
        fd = os.open(out / 'coverage-census.request', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    except FileExistsError:
        print('coverage_census_request_failed=exists', file=sys.stderr)
        return 2
    except (OSError, SettingsError):
        print('coverage_census_request_failed=setup', file=sys.stderr)
        return 2
    print('coverage_census_requested=1')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
