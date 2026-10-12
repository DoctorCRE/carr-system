#!/usr/bin/env python3
"""Probe builder seats daily; write only allowlisted results for the orchestrator."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sys
import uuid
from seat_health import (assess, SEATS, atomic_json, combined_report, dispatchable, health_rows,
                         probe, probe_jev, publish, read_dashboard, reconcile, verb_call)
import host_liveness

ROOT = Path(__file__).resolve().parents[1]


def hosts():
    """(config, liveness) — liveness is all-unknown when the tailnet cannot be read."""
    config = host_liveness.load_config()
    return config, host_liveness.observe(config=config)


def placement(seat, config, live):
    """('run', here) or ('skip', why). A seat runs where its host is: on the
    MacBook only jev and sol-macbook (locally) can run; on the Studio every
    seat can, sol-macbook over ssh. Unknown placement keeps the old behaviour."""
    host = host_liveness.seat_host(seat, config)
    if host is None:
        return 'run', False
    if live['hosts'].get(host, {}).get('state') == 'offline':
        return 'skip', f'host {host} offline'
    me = live.get('self')
    if me is None:
        return 'run', False
    if host == me:
        return 'run', seat == 'sol-macbook'
    if seat == 'sol-macbook':
        return 'run', False
    return 'skip', f'runs on {host}, not this Mac'


def read_report(path):
    try:
        report = json.loads(path.read_text())
        seats = report.get('seats') if isinstance(report, dict) and report.get('schema') == 'carr-seat-health/v1' else None
        return {seat: row for seat, row in seats.items() if seat in SEATS and isinstance(row, dict)} if isinstance(seats, dict) else {}
    except (OSError, ValueError):
        return {}


def completed_today(ledger, day):
    if not ledger.exists():
        return None
    for line in reversed(ledger.read_text().splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (isinstance(row, dict) and row.get('completed_recording') is True
                and row.get('day') == day and type(row.get('passed')) is bool
                and isinstance(row.get('seats'), list)
                and all(isinstance(seat, str) for seat in row['seats'])
                and len(row['seats']) == len(SEATS) and set(row['seats']) == set(SEATS)):
            return row
    return None


def publish_receipt(ledger, receipt):
    previous = ledger.read_text() if ledger.exists() else ''
    temp = ledger.with_suffix('.tmp')
    temp.write_text(previous.rstrip('\n') + ('\n' if previous else '') + json.dumps(receipt) + '\n')
    temp.replace(ledger)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-repo', type=Path, default=ROOT)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--seat', choices=list(SEATS), action='append')
    parser.add_argument('--timeout-seconds', type=int, default=90)
    parser.add_argument('--e2e-cli', type=Path, default=Path.home() / 'doctorcre-app/node_modules/.bin/e2e')
    parser.add_argument('--no-record', action='store_true', help='Watched probes without record mutations')
    parser.add_argument('--force', action='store_true', help='Run again today for attended verification')
    parser.add_argument('--show', action='store_true', help='Read current health without a model call')
    parser.add_argument('--jev-task', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.jev_task:
        return probe_jev(args.jev_task)
    if args.timeout_seconds < 1 or args.timeout_seconds > 1800:
        parser.error('timeout must be between 1 and 1800 seconds')
    args.runtime_repo = args.runtime_repo.resolve()
    output = args.output_root or args.runtime_repo / 'out/orch/budget'
    report_path = output / 'seat-health.json'
    door = lambda name, payload: verb_call(args.runtime_repo, name, payload)  # noqa: E731
    config, live = hosts()
    if args.show:
        try:
            report = json.loads(report_path.read_text())
        except (OSError, ValueError):
            report = {}
        rows = health_rows(combined_report(read_dashboard(door), report),
                           offline=host_liveness.offline_seats(live, config))
        print('\n'.join(rows))
        return int(any(line.startswith('FAIL') for line in rows))
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'seat-health.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('seat-health: another probe run owns the lock', file=sys.stderr)
            return 2
        now = datetime.now(timezone.utc)
        ledger = output / 'seat-health-runs.jsonl'
        previous = completed_today(ledger, now.date().isoformat())
        if previous and not args.force and not args.seat and not args.no_record:
            print('seat-health: today recording completed; read seat-health.json')
            return int(not previous['passed'])
        run_id = 'seat-health-' + str(uuid.uuid4())
        run_dir = args.runtime_repo / 'out/seat-health-runs' / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        rows = read_report(report_path)
        selected = list(args.seat or SEATS)
        recorded = True
        failed = False
        for seat in selected:
            where, here = placement(seat, config, live)
            if where == 'skip':
                # Not probed, so not observed: no row, no loop, and the day's
                # recording stays incomplete so a later run can still cover it.
                recorded = False
                print(f"SKIP {seat}: {here} · not probed; the host row carries the alarm")
                continue
            try:
                row = probe(seat, args.runtime_repo, run_dir.resolve(), args.timeout_seconds, args.e2e_cli, here)
            except (ValueError, OSError, TypeError, AttributeError):
                row = assess(seat, '', '', 4, 0, reason='invalid_probe_data')
            row['observed_at'] = datetime.now(timezone.utc).isoformat()
            row['record_action'] = 'not_requested'
            if not args.no_record:
                try:
                    row['record_action'] = reconcile(row, output / ('seat-health-' + seat + '-loop.json'),
                        lambda name, payload: verb_call(args.runtime_repo, name, payload))
                except (ValueError, OSError, TypeError, AttributeError):
                    row['record_action'] = 'error'
            recorded &= row['record_action'] in ('none', 'open', 'cleared')
            failed |= not row['passed'] or row['record_action'] == 'error'
            rows[seat] = row
            atomic_json(report_path, {'schema': 'carr-seat-health/v1', 'run_id': run_id,
                'observed_at': now.isoformat(), 'max_age_seconds': 86400,
                'recipient': 'orchestrator', 'seats': rows})
            print(f"{'PASS' if row['passed'] else 'FAIL'} {seat} latency={row['latency_seconds']}s "
                  f"layer={row['failing_layer'] or 'none'} record={row['record_action']} · {row['action']}")
        published = True
        if not args.no_record:
            # Seats and host liveness reach the record layer so the OTHER Mac's
            # health run and dispatch can read them (read-resource-dashboard).
            try:
                result = publish(rows, door, live=live, source_host=live.get('self'))
            except (ValueError, OSError, TypeError, AttributeError):
                result = {'model_route': 'error'}
            published = all(value == 'ok' for value in result.values())
            print('PUBLISH ' + ' '.join(f'{key}={value}' for key, value in sorted(result.items()))
                  + ' · record-resource-observation; on breach: rerun with --force after the '
                  'record layer answers')
        publish_receipt(ledger, {'run_id': run_id, 'day': now.date().isoformat(),
            'ended_at': datetime.now(timezone.utc).isoformat(), 'seats': selected,
            'completed_recording': not args.no_record and set(selected) == set(SEATS) and recorded
                                   and published,
            'passed': not failed})
        return int(failed or not published)


if __name__ == '__main__':
    raise SystemExit(main())
