#!/usr/bin/env python3
"""Refresh Salesforce from the signed-in Chrome profile; never write to Salesforce."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.salesforce_browser_refresh import capture_chrome, run_refresh, validate_url
from lib.salesforce_deal_sync import deployed_verb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scheduled', action='store_true', help='Run once per Chicago weekday, after 6:30')
    args = parser.parse_args()
    directory = Path.home() / '.config/carr/salesforce-refresh'
    try:
        config = json.loads((directory / 'config.json').read_text())
        url = validate_url(config['url'])
        receipt = run_refresh(directory, lambda: capture_chrome(url), deployed_verb,
                              now=dt.datetime.now(dt.timezone.utc), scheduled=args.scheduled)
        if receipt['status'] != 'skipped':
            print(json.dumps(receipt, sort_keys=True))
        return 1 if receipt['status'] == 'failed' else 0
    except BlockingIOError:
        return 0
    except (OSError, KeyError, TypeError, ValueError):
        print('Salesforce refresh configuration or evidence invalid', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
