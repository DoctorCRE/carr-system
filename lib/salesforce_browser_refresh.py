"""Read-only browser capture, private snapshots, and journaled CARR reconciliation."""
from __future__ import annotations

import csv
import datetime as dt
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
from collections.abc import Callable
from typing import Any
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CHICAGO = ZoneInfo('America/Chicago')
Json = dict[str, Any]
Verb = Callable[[str, Json], Json]


def private_write(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def snapshot_rows(capture: Json) -> list[dict[str, str]]:
    import re
    from lib.salesforce_active_snapshot import HEADERS

    if capture.get('reason') == 'Salesforce sign-in needed':
        raise ValueError('Salesforce sign-in needed')
    rows = capture.get('rows')
    count = capture.get('expected_count')
    if (capture.get('state') != 'complete' or type(count) is not int or count <= 0
            or not isinstance(rows, list) or len(rows) != count):
        raise ValueError('Salesforce page shape changed')
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'id', *HEADERS[1:]}
                or any(not isinstance(v, str) for v in row.values())
                or not re.fullmatch(r'006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?', row['id'])
                or not all(row[key].strip() for key in ('name', 'phase', 'owner', 'company'))
                or row['id'][:15] in seen):
            raise ValueError('Salesforce page shape changed')
        if row['close']:
            try:
                dt.datetime.strptime(row['close'], '%m/%d/%Y')
            except ValueError:
                raise ValueError('Salesforce page shape changed') from None
        seen.add(row['id'][:15])
        result.append(row)
    if len({row['id'][-8:] for row in result}) != count:
        raise ValueError('Salesforce page shape changed')
    return result


def run_refresh(directory: Path, capture: Callable[[], Json], call: Verb, *, now: dt.datetime, scheduled: bool = False) -> Json:
    from lib import salesforce_deal_sync as sync
    from lib.salesforce_active_snapshot import HEADERS

    if directory.resolve().is_relative_to(ROOT):
        raise ValueError("Salesforce runtime files must stay outside the repository")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    run_id = now.astimezone(CHICAGO).strftime('%Y-%m-%dT%H%M%S') + '-' + uuid.uuid4().hex
    receipt: Json = {'schema': 'salesforce-browser-refresh.v1', 'at': now.isoformat(), 'run_id': run_id,
                     'status': 'failed', 'counts': {'added': 0, 'updated': 0, 'unchanged': 0, 'failed': 0}}
    with os.fdopen(os.open(directory / 'refresh.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = directory / 'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if scheduled and not schedule_due(state, now=now):
            return {'status': 'skipped'}
        if scheduled:
            state['attempted_on'] = now.astimezone(CHICAGO).date().isoformat()
        state['latest'] = {'status': 'running', 'at': now.isoformat()}
        private_write(state_path, json.dumps(state))
        plan: Json | None = None
        journal_path = directory / (run_id + '.receipts.jsonl')
        try:
            for journal in directory.glob("*.receipts.jsonl"):
                entries = [json.loads(line) for line in journal.read_text().splitlines()]
                if not entries or entries[-1].get("status") != "complete":
                    raise ValueError("Salesforce prior run incomplete; reconcile private journal before retry")
            rows = snapshot_rows(capture())
            output = io.StringIO()
            writer = csv.writer(output, delimiter='|', lineterminator='\n')
            writer.writerow(HEADERS)
            writer.writerows([row['id'][-8:], *(row[key] for key in HEADERS[1:])] for row in rows)
            private_write(directory / (run_id + '.psv'), output.getvalue())
            report = io.StringIO()
            writer = csv.writer(report, lineterminator='\n')
            writer.writerow(['Opportunity ID', 'Opportunity Name', 'Account Name', 'Stage', 'Opportunity Owner', 'Close Date'])
            writer.writerows([row[key] for key in ('id', 'name', 'company', 'phase', 'owner', 'close')] for row in rows)
            deals = sync.read_snapshot(call)
            clients = [{'ref': d['client_ref'], 'name': d['client_name']} for d in deals if d.get('client_ref')]
            for name in sorted({row['company'] for row in rows}):
                found = call('find', {'query': name})
                clients.extend({'ref': c['ref'], 'name': c['name']} for c in found.get('parties', [])
                               if c.get('kind') == 'client' and c.get('merged') is False)
            plan = sync.build_plan(report.getvalue(), deals=deals, clients=clients,
                                   as_of=now.astimezone(CHICAGO).date(), expected_count=len(rows))
            private_write(directory / (run_id + '.plan.json'), json.dumps(plan))
            receipt['counts']['unchanged'] = plan['counts']['unchanged']
            receipt['excluded'] = plan['counts']['out_of_scope']
            if plan['counts']['held']:
                receipt['counts']['failed'] = sum(plan['counts'][kind] for kind in ('held', 'create', 'update'))
                raise ValueError('Salesforce reconciliation held; no rows imported')
            sync.apply_plan(plan, call, journal_path)
            receipt['counts'].update(added=plan['counts']['create'], updated=plan['counts']['update'],
                                     unchanged=plan['counts']['unchanged'])
            receipt['status'] = 'success'
            state['last_success'] = receipt['at']
        except Exception as error:
            if plan and journal_path.exists():
                entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
                verified = {e.get('opportunity_digest') for e in entries if e.get('status') == 'verified'}
                for kind, label in (('create', 'added'), ('update', 'updated')):
                    receipt['counts'][label] = sum(item['kind'] == kind and sync.fingerprint(item['opportunity_id']) in verified
                                                   for item in plan['items'])
                receipt['counts']['failed'] = sum(plan['counts'][kind] for kind in ('create', 'update')) - len(verified)
            receipt['reason'] = str(error) if isinstance(error, ValueError) else 'Salesforce refresh failed; inspect private journal before retry'
            receipt['counts']['failed'] = max(1, receipt['counts']['failed'])
        private_write(directory / (run_id + '.run.json'), json.dumps(receipt))
        state['latest'] = receipt
        private_write(state_path, json.dumps(state))
    return receipt


def schedule_due(state: Json, *, now: dt.datetime) -> bool:
    local = now.astimezone(CHICAGO)
    return (local.weekday() < 5 and (local.hour, local.minute) >= (6, 30)
            and state.get('attempted_on') != local.date().isoformat())


def health_findings(state: Json, *, now: dt.datetime) -> list[Json]:
    reasons = []
    latest = state.get('latest', {})
    if latest.get('status') == 'failed':
        reasons.append(latest.get('reason', 'Salesforce refresh failed'))
    elif latest.get('status') == 'running':
        try:
            started = dt.datetime.fromisoformat(latest['at'])
            if now - started > dt.timedelta(minutes=10):
                reasons.append('Salesforce refresh interrupted; reconcile private journal before retry')
        except (KeyError, TypeError, ValueError):
            reasons.append('Salesforce refresh evidence invalid')
    try:
        success = dt.datetime.fromisoformat(state['last_success'])
        if now - success > dt.timedelta(hours=26):
            reasons.append('Salesforce last success older than 26h')
        elif success > now:
            reasons.append('Salesforce refresh evidence invalid')
    except (KeyError, TypeError, ValueError):
        reasons.append('Salesforce has no successful refresh')
    return [{'subject': 'salesforce-browser-refresh', 'reason': reason} for reason in reasons]


def validate_url(url: str) -> str:
    import re
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if (parts.scheme != 'https' or not re.fullmatch(r'[a-z0-9-]+\.lightning\.force\.com', parts.netloc)
            or parts.path != '/lightning/o/Opportunity/list' or parts.fragment
            or set(query) != {'filterName'} or len(query['filterName']) != 1
            or not re.fullmatch(r'[A-Za-z0-9_]+', query['filterName'][0])):
        raise ValueError('Use an HTTPS Salesforce Opportunity list URL with one filterName')
    return url


def capture_chrome(url: str, *, run: Callable[..., Any] | None = None,
                   wait: Callable[[float], None] | None = None) -> Json:
    import subprocess
    import time

    validate_url(url)
    run = run or subprocess.run
    wait = wait or time.sleep
    bridge = ['/usr/bin/osascript', str(ROOT / 'pipelines/read-salesforce-list.applescript')]
    script = (ROOT / 'pipelines/capture-salesforce-list.js').read_text()
    start = ('(() => { if (document.readyState !== "complete" || '
             '(!document.querySelector("table[role=grid], table[role=treegrid], input[type=password], #username"))) '
             'return JSON.stringify({state:"loading"}); '
             'if (!window.__carrSalesforceRefresh) { ' + script + ' } '
             'return JSON.stringify(window.__carrSalesforceRefresh); })()')
    tab = ''
    try:
        opened = run([*bridge, 'open', url], capture_output=True, text=True, timeout=15, check=True)
        tab = opened.stdout.strip()
        if not tab.isdecimal():
            raise ValueError('Salesforce Chrome bridge unavailable')
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            response = run([*bridge, 'evaluate', tab, url, start], capture_output=True, text=True, timeout=15, check=True)
            result: Json = json.loads(response.stdout)
            if result.get('state') in ('complete', 'failed'):
                return result
            wait(1)
        raise ValueError('Salesforce page shape changed')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        raise ValueError('Salesforce Chrome bridge unavailable; verify Apple Events permission') from None
    finally:
        if tab.isdecimal():
            try:
                run([*bridge, 'close', tab], capture_output=True, text=True, timeout=15, check=True)
            except (subprocess.SubprocessError, OSError):
                pass
