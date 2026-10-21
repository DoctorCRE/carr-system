#!/usr/bin/env python3
"""Source-only Outlook version monitor. Installation and alert delivery belong to the orchestrator."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_version_ports import OperationalPorts, known_record_types
from lib.outlook_version_tripwire import Tripwire, require_accepted, preflight_alert, _write, _lock


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def main():
    args = None
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('command', choices=('check', 'accept', 'gate'))
        parser.add_argument('--app', type=Path, required=True)
        parser.add_argument('--home', type=Path, required=True)
        parser.add_argument('--state', type=Path, required=True)
        parser.add_argument('--observer', type=Path, required=True)
        parser.add_argument('--observer-sha256', required=True)
        args = parser.parse_args()
        ports = OperationalPorts(args.home, args.app, args.observer, args.observer_sha256)
        monitor = Tripwire(args.state, ports.source_pin, known_record_types())
        if args.command == 'check':
            with _lock(args.state):
                sources = args.state / 'sources'
                sources.mkdir(mode=0o700, exist_ok=True)
                _write(sources / (ports.source_pin + '.json'), ports.manifest)
            result = monitor.check(ports.version, ports.snapshot, ports.observe)
            print(json.dumps({'schema': 'outlook-version-status/v1', 'status': result['status'],
                              'reason': result['reason'], 'alert_id': result['id']}))
            return 0 if result['status'] == 'accepted' else 3
        if args.command == 'accept':
            monitor.accept(ports.version)
        ports._unchanged()
        require_accepted(args.state, ports.version, ports.source_pin)
        print('{"schema":"outlook-version-status/v1","status":"accepted"}')
        return 0
    except Exception:
        if args is not None and args.command == 'check':
            try:
                preflight_alert(args.state)
            except OSError:
                pass
        print('Outlook version tripwire refused input or operation', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
