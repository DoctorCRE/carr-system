#!/usr/bin/env python3
"""Verify one natural intake field census using only its aggregate generation bundle."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_field_census import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--after', required=True, help='Timezone-aware observation floor set before the natural run')
    args = parser.parse_args()
    try:
        floor = datetime.fromisoformat(args.after)
        if floor.tzinfo is None:
            raise ValueError
        bundle = validate(json.loads(args.bundle.read_text()))
        if (datetime.fromisoformat(bundle['binding']['started_at']) <= floor
                or bundle['census']['population']['admitted'] == 0):
            raise ValueError
        print(json.dumps(dict(accounting_differences=0, generation=bundle['binding']['generation'],
                              population=bundle['census']['population'],
                              message_causes=bundle['census']['message_causes'], fields=bundle['census']['fields'])))
        return 0
    except (ValueError, OSError, TypeError):
        print('field_census_proof_failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
