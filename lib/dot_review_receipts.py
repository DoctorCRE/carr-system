"""Detect local receipt tampering and bind Dot decisions to relay runs and live authors.

An external SQLite checkpoint detects changes to either ledger head. Authenticated
relay consumption advances it; raw ledger writes do not. Public append APIs are
not an authentication boundary: every consumer verifies the report against Slack.
Coordinated rewriting of both local evidence and Slack remains out of scope.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import sqlite3
import shutil
import time
from decimal import Decimal

from lib import dot_relay
from lib.secret_redaction import redact_text

SCHEMA = 'carr-dot-review-receipt/v2'
RUN_SCHEMA = 'carr-dot-relay-run/v1'
ALARM = 'DOT_REVIEW_RECEIPT_ALARM'
GENESIS = '0' * 64
TIMESTAMP_SKEW_SECONDS = 30
DOT_MARKER = 'Reviewer: ChatGPT Dot'
STAMP = re.compile(r'^APPROVE\r?\nReviewed-SHA: [0-9a-f]{40}\r?\n(?:\r?\n)?'
                   r'(?:Orchestrator merge queue:|Orchestrator: verified exact head)')


def publication_body(meta, report, known_secrets=()):
    lines = report.strip().splitlines()
    if len(lines) < 2 or lines[0] not in ('APPROVE', 'REVIEW: BLOCKED') or lines[1] != 'Reviewed-SHA: ' + meta['sha']:
        raise ValueError('Dot verdict must carry the exact brief SHA in its first two lines')
    if any(line.strip() in ('APPROVE', 'REVIEW: BLOCKED') for line in lines[2:]):
        raise ValueError('multiple verdicts in completed Dot report')
    if any('reviewed-sha:' in line.lower() for line in lines[2:]):
        raise ValueError('duplicate reviewed SHA')
    review_key = hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()
    marker = '<!-- dot-review:' + review_key + ' -->'
    findings = '\n'.join(line for line in lines[2:] if not line.startswith('DOT-REPORT-END')).strip()
    if re.search(r'^Orchestrator(?: merge queue:|: verified exact head)', findings, re.M):
        raise ValueError('Dot report contains an orchestrator authority stamp')
    return '\n'.join(lines[:2]) + '\n\n' + redact_text(findings, known_secrets=known_secrets) + '\n\n' + DOT_MARKER + '\n' + marker


def receipt_directory():
    return Path(os.environ.get('CARR_DOT_REVIEW_RECEIPTS',
                               str(Path.home() / '.local/state/carr/merge-queue/relay')))


def anchor_database():
    directory = receipt_directory().resolve()
    path = Path(os.environ.get('CARR_DOT_REVIEW_ANCHORS', str(directory) + '.anchors.sqlite3')).resolve()
    if path.is_relative_to(directory):
        raise _ChainError('chain anchor must be outside the receipts directory')
    return path


@contextmanager
def _anchors():
    path = anchor_database()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS actor (sender TEXT, channel TEXT, github_actor TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS heads (ledger TEXT PRIMARY KEY, sequence INTEGER, sha256 TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS audit (event TEXT, detail TEXT)')
        db.execute('BEGIN IMMEDIATE')
        yield db
    finally:
        db.close()


def configure_actor(sender, channel, github_actor):
    """Enroll public identities explicitly; never infer GitHub identity from Slack IDs."""
    if any(not isinstance(v, str) or not v.strip() or re.search(r'[\s:]', v)
           for v in (sender, channel, github_actor)):
        raise ValueError('Dot sender, channel and GitHub actor mapping are required')
    with _anchors() as db:
        current = db.execute('SELECT sender, channel, github_actor FROM actor').fetchall()
        values = (sender, channel, github_actor.casefold())
        if current and current != [values]:
            raise ValueError('configured Dot actor differs; reconcile identity explicitly')
        if not current:
            db.execute('INSERT INTO actor VALUES (?,?,?)', values)
            for ledger in ('runs', 'receipts'):
                db.execute('INSERT INTO heads VALUES (?,?,?)', (ledger, 0, GENESIS))
            db.execute('INSERT INTO audit VALUES (?,?)', ('configure_actor', json.dumps(values)))
        db.commit()


def _actor(db):
    rows = db.execute('SELECT sender, channel, github_actor FROM actor').fetchall()
    if len(rows) != 1:
        raise _ChainError('configured Dot actor mapping is unavailable')
    return dict(zip(('sender', 'channel', 'github_actor'), rows[0]))


def _identity(value, actor):
    if value.get('reviewer') != actor['sender']:
        raise _ChainError('relay reviewer is not the configured Dot Slack sender')
    ts = value.get('slack_message_ts', '')
    if (not isinstance(ts, str) or not re.fullmatch(r'[0-9]+\.[0-9]{6}', ts) or
            value.get('relay_run_id') != actor['channel'] + ':' + ts):
        raise _ChainError('relay run does not match configured channel and Slack message timestamp')
    if (not isinstance(value.get('report_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', value['report_sha256'])):
        raise _ChainError('relay report text hash is missing')


def _head(chain):
    return (chain[-1]['sequence'], chain[-1]['sha256']) if chain else (0, GENESIS)


def _verify_heads(db, ledgers):
    actor = _actor(db)
    for ledger, chain in ledgers.items():
        anchored = db.execute('SELECT sequence, sha256 FROM heads WHERE ledger=?', (ledger,)).fetchone()
        audit = db.execute('SELECT detail FROM audit WHERE event=? ORDER BY rowid DESC LIMIT 1',
                           ('anchor_' + ledger,)).fetchone()
        recorded = _head([json.loads(audit[0])]) if audit else (0, GENESIS)
        if anchored != recorded:
            raise _ChainError(f'{ledger} external anchor moved backwards or diverged from its audit history')
        if anchored != _head(chain):
            raise _ChainError(f'{ledger} chain head moved backwards or diverged from external anchor')
        for entry in chain:
            _identity(entry, actor)
    return actor


def archive_legacy(*, operator):
    """Move only v1 binding folders aside and receipt their names and content hashes."""
    if not isinstance(operator, str) or not operator.strip():
        raise ValueError('archive requires a named operator')
    directory = receipt_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with _locked(directory), _anchors() as db:
        folders = []
        for path in sorted(directory.iterdir()):
            if path.name in {'.lock', 'runs', 'receipts'}:
                continue
            if path.is_symlink() or not path.is_dir() or not re.fullmatch(r'[0-9a-f]{64}', path.name):
                raise _ChainError('unrecognized receipt storage cannot be archived as v1')
            files = sorted(path.iterdir())
            if not files or any(f.is_symlink() or not f.is_file() or
                                json.loads(f.read_text()).get('schema') != 'carr-dot-review-receipt/v1'
                                for f in files):
                raise _ChainError('legacy folder contains non-v1 evidence')
            folders.append((path, {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}))
        if not folders:
            return None
        destination = directory.parent / (directory.name + '.v1-archive') / str(time.time_ns())
        destination.mkdir(parents=True)
        receipt = {'schema': 'carr-dot-v1-archive/v1', 'operator': operator,
                   'source': str(directory.resolve()), 'destination': str(destination.resolve()),
                   'folders': {p.name: hashes for p, hashes in folders}}
        # Persist the manifest before moving; a failed partial move remains receipted.
        (destination / 'archive-receipt.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
        db.execute('INSERT INTO audit VALUES (?,?)', ('archive_v1', json.dumps(receipt, sort_keys=True)))
        db.commit()
        for path, _ in folders:
            shutil.move(str(path), str(destination / path.name))
        _sync(destination)
        _sync(directory)
        return receipt


def _binding(meta, body):
    if (not isinstance(meta.get('repo'), str) or not meta['repo'].strip() or
            type(meta.get('pr')) is not int or meta['pr'] < 1 or
            not isinstance(meta.get('sha'), str) or not re.fullmatch(r'[0-9a-f]{40}', meta['sha']) or
            not isinstance(body, str)):
        raise ValueError('invalid Dot receipt binding')
    return {'repo': meta['repo'], 'pr': meta['pr'], 'reviewed_sha': meta['sha'],
            'body_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode('utf-8')).hexdigest()


class _ChainError(ValueError):
    pass


def _alarm(reason):
    print(f'{ALARM}: {reason}', file=sys.stderr)


@contextmanager
def _locked(directory):
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_chain(directory, schema):
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise _ChainError(f'{directory.name} ledger is not a directory')
    entries, previous, seen_runs = [], GENESIS, set()
    for sequence, path in enumerate(sorted(directory.iterdir()), 1):
        if path.name != f'{sequence:020d}.json' or not path.is_file():
            raise _ChainError(f'{directory.name} ledger has an unexpected entry or chain gap')
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise _ChainError(f'{directory.name} ledger entry is unreadable') from error
        if not isinstance(value, dict):
            raise _ChainError(f'{directory.name} ledger entry is not an object')
        unsigned = {k: v for k, v in value.items() if k != 'sha256'}
        if (value.get('schema') != schema or type(value.get('sequence')) is not int or
                value['sequence'] != sequence or value.get('prev_sha256') != previous or
                value.get('sha256') != _digest(unsigned)):
            raise _ChainError(f'{directory.name} ledger hash chain is broken')
        try:
            _binding({'repo': value.get('repo'), 'pr': value.get('pr'),
                      'sha': value.get('reviewed_sha')}, '')
        except ValueError as error:
            raise _ChainError(f'{directory.name} ledger binding is invalid') from error
        if (not isinstance(value.get('body_sha256'), str) or
                not re.fullmatch(r'[0-9a-f]{64}', value['body_sha256']) or
                not isinstance(value.get('reviewer'), str) or not value['reviewer'].strip() or
                not isinstance(value.get('relay_run_id'), str) or not value['relay_run_id'].strip()):
            raise _ChainError(f'{directory.name} ledger relay identity is invalid')
        if value['relay_run_id'] in seen_runs:
            raise _ChainError(f'{directory.name} ledger repeats a relay run')
        seen_runs.add(value['relay_run_id'])
        entries.append(value)
        previous = value['sha256']
    return entries


def _read_ledgers(directory):
    if any(path.name not in {'.lock', 'runs', 'receipts'} for path in directory.iterdir()):
        raise _ChainError('legacy or unrecognized receipt storage requires reconciliation')
    return {'runs': _read_chain(directory / 'runs', RUN_SCHEMA),
            'receipts': _read_chain(directory / 'receipts', SCHEMA)}


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append(ledger, value, *, anchor=False):
    directory = receipt_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with _locked(directory):
        try:
            ledgers = _read_ledgers(directory)
            chain = ledgers[ledger]
            if anchor:
                with _anchors() as db:
                    actor = _verify_heads(db, ledgers)
                    _identity(value, actor)
                    if ledger == 'receipts' and not any(
                            all(run.get(k) == value.get(k) for k in
                                ('repo', 'pr', 'reviewed_sha', 'body_sha256', 'reviewer',
                                 'relay_run_id', 'slack_message_ts', 'slack_thread_ts', 'report_sha256'))
                            for run in ledgers['runs']):
                        raise _ChainError('publication lacks its anchored relay run')
        except (OSError, sqlite3.Error, ValueError) as error:
            _alarm(str(error))
            raise ValueError('append-only Dot ledger failed validation') from error
        for entry in chain:
            if entry['relay_run_id'] == value['relay_run_id']:
                stored = {k: v for k, v in entry.items() if k not in {'sequence', 'prev_sha256', 'sha256'}}
                if stored != value:
                    raise ValueError('append-only Dot relay run conflict')
                return entry
        value = {**value, 'sequence': len(chain) + 1,
                 'prev_sha256': chain[-1]['sha256'] if chain else GENESIS}
        value['sha256'] = _digest(value)
        destination = directory / ledger
        destination.mkdir(exist_ok=True)
        fd = os.open(destination / f'{value["sequence"]:020d}.json',
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        _sync(destination)
        _sync(directory)
        if anchor:
            with _anchors() as db:
                db.execute('UPDATE heads SET sequence=?, sha256=? WHERE ledger=?',
                           (value['sequence'], value['sha256'], ledger))
                db.execute('INSERT INTO audit VALUES (?,?)', ('anchor_' + ledger, json.dumps(value, sort_keys=True)))
                db.commit()
        return value


def _value(schema, meta, body, reviewer, relay_run_id, report=None, thread_ts=None):
    if (not isinstance(reviewer, str) or not reviewer.strip() or
            not isinstance(relay_run_id, str) or not relay_run_id.strip()):
        raise ValueError('Dot ledger requires a reviewer and relay run')
    ts = relay_run_id.rsplit(':', 1)[-1]
    if not re.fullmatch(r'[0-9]+\.[0-9]{6}', ts):
        raise ValueError('invalid Slack message timestamp')
    if Decimal(ts) > Decimal(str(time.time())) + TIMESTAMP_SKEW_SECONDS:
        raise ValueError('Slack message timestamp is in the future')
    if thread_ts is not None and not re.fullmatch(r'[0-9]+\.[0-9]{6}', thread_ts):
        raise ValueError('invalid Slack thread timestamp')
    return {'schema': schema, **_binding(meta, body), 'reviewer': reviewer.strip(),
            'relay_run_id': relay_run_id.strip(), 'slack_message_ts': ts, 'slack_thread_ts': thread_ts or ts,
            'report_sha256': hashlib.sha256((body if report is None else report).encode()).hexdigest()}


def record_run(meta, body, *, reviewer, relay_run_id, report=None, thread_ts=None):
    """Append unanchored run evidence; this alone never authorizes a verdict."""
    return _append('runs', _value(RUN_SCHEMA, meta, body, reviewer, relay_run_id, report, thread_ts))


def record(meta, body, *, reviewer, relay_run_id, builder=None, branch_author=None, report=None, anchor=False, thread_ts=None):
    """Append local provenance; consumers still require independent Slack verification."""
    value = _value(SCHEMA, meta, body, reviewer, relay_run_id, report, thread_ts)
    if builder is not None:
        value['builder'] = builder
    if branch_author is not None:
        value['branch_author'] = branch_author
    return _append('receipts', value, anchor=anchor)


def attest_run(meta, body, *, reviewer, relay_run_id, report, thread_ts=None):
    """Append report evidence; this public API does not authenticate Slack provenance."""
    return _append('runs', _value(RUN_SCHEMA, meta, body, reviewer, relay_run_id, report, thread_ts), anchor=True)


def _live_authors(meta, api):
    if api is None:
        raise ValueError('fresh GitHub author reader is unavailable')
    endpoint = f'repos/{meta["repo"]}/pulls/{meta["pr"]}'
    pr = api(endpoint)
    author = (pr.get('user') or {}).get('login') if isinstance(pr, dict) else None
    if not isinstance(author, str) or not author.strip():
        raise ValueError('live GitHub PR author is unknown')
    commits = api(endpoint + '/commits?per_page=100')
    if not isinstance(commits, list):
        raise ValueError('live GitHub commit authors are unreadable')
    authors = {author.strip().casefold()}
    for commit in commits:
        login = (commit.get('author') or {}).get('login') if isinstance(commit, dict) else None
        if not isinstance(login, str) or not login.strip():
            raise ValueError('live GitHub commit author is unknown')
        authors.add(login.strip().casefold())
        raw_author = ((commit.get('commit') or {}).get('author') or {})
        for field in ('name', 'email'):
            alias = raw_author.get(field)
            if isinstance(alias, str) and alias.strip():
                authors.add(alias.strip().casefold())
    return authors


def _slack_transport(actor):
    """Use the relay's credential loader and read adapter, never a local receipt as proof."""
    config = dot_relay.read_config(Path.home() / '.hermes/.env', Path(__file__).resolve().parents[1])
    if config['sender'] != actor['sender'] or config['channel'] != actor['channel']:
        raise _ChainError('Slack configuration differs from the enrolled Dot actor')
    return dot_relay.SlackTransport(config['token'], config['channel'])


def _verify_slack(entry, body, actor):
    ts = entry['slack_message_ts']
    if Decimal(ts) > Decimal(str(time.time())):
        raise _ChainError('Slack report timestamp is in the future')
    transport = _slack_transport(actor)
    if transport.channel != actor['channel']:
        raise _ChainError('Slack reader channel differs from configured Dot channel')
    messages = transport.replies(entry.get('slack_thread_ts', ts))
    dot_relay._validate_messages(messages)
    messages = sorted(messages, key=lambda m: dot_relay._timestamp_key(m['ts']))
    start = next((i for i, message in enumerate(messages) if message['ts'] == ts), None)
    if start is None:
        raise _ChainError('recorded Dot Slack message does not exist')
    if messages[start]['text'].strip().splitlines()[:2] != body.strip().splitlines()[:2]:
        raise _ChainError('Dot Slack verdict and reviewed head differ from the publication')
    report = []
    for message in messages[start:]:
        if actor['sender'] not in (message.get('user'), message.get('bot_id')):
            if message['ts'] == ts:
                raise _ChainError('recorded Slack message was not posted by Dot')
            continue
        if message.get('edited') or message.get('subtype') not in (None, 'bot_message'):
            raise _ChainError('Dot Slack report has been edited or has an unexpected subtype')
        if Decimal(message['ts']) > Decimal(str(time.time())):
            raise _ChainError('Dot Slack report contains a future message')
        if dot_relay._protocol(message['text'])[0]:
            if message['ts'] == ts:
                raise _ChainError('recorded Slack message is a command, not a report')
            continue
        report.append(message['text'])
        if dot_relay._protocol(message['text'])[1] is not None:
            break
    text = '\n'.join(report)
    if hashlib.sha256(text.encode()).hexdigest() != entry['report_sha256']:
        raise _ChainError('Dot Slack report text differs from the recorded hash')
    if dot_relay._protocol(text)[1] is None:
        raise _ChainError('Dot Slack report is incomplete')
    meta = {'repo': entry['repo'], 'pr': entry['pr'], 'sha': entry['reviewed_sha']}
    try:
        canonical = publication_body(meta, text, (getattr(transport, 'token', ''),))
    except ValueError as error:
        raise _ChainError(str(error)) from error
    if canonical != body:
        raise _ChainError('publication differs from the canonical authenticated Slack report')


def _matching(meta, body, api):
    binding = _binding(meta, body)
    directory = receipt_directory()
    if not directory.exists():
        if not anchor_database().exists():
            return None, False
        try:
            with _anchors() as db:
                _verify_heads(db, {'runs': [], 'receipts': []})
        except (OSError, sqlite3.Error, ValueError) as error:
            _alarm(str(error))
            return None, _claims_relay(body)
        return None, False
    try:
        with _locked(directory):
            ledgers = _read_ledgers(directory)
            with _anchors() as db:
                actor = _verify_heads(db, ledgers)
    except (OSError, sqlite3.Error, ValueError) as error:
        _alarm(str(error))
        return None, _claims_relay(body)
    candidates = [entry for entry in ledgers['receipts']
                  if all(entry.get(k) == v for k, v in binding.items())]
    if not candidates:
        if any(all(run.get(k) == v for k, v in binding.items()) for run in ledgers['runs']):
            _alarm('relay run lacks its publication receipt')
            return None, True
        return None, False
    paired = []
    for entry in candidates:
        run = next((run for run in ledgers['runs']
                    if run['relay_run_id'] == entry['relay_run_id'] and
                    run['reviewer'] == entry['reviewer'] and
                    all(run.get(k) == entry.get(k) for k in
                        ('slack_message_ts', 'slack_thread_ts', 'report_sha256')) and
                    all(run.get(k) == v for k, v in binding.items())), None)
        if run is None:
            _alarm('receipt lacks a matching relay run')
        else:
            paired.append(entry)
    if not paired:
        return None, True
    try:
        authors = _live_authors(meta, api)
    except Exception as error:
        _alarm(f'live GitHub author verification failed ({type(error).__name__})')
        return None, True
    for entry in paired:
        if actor['github_actor'].casefold() in authors:
            _alarm('mapped Dot GitHub actor is a live PR or commit author')
        else:
            try:
                _verify_slack(entry, body, actor)
            except Exception as error:
                # Credential/API exceptions may contain secrets. Log only their type.
                _alarm('Slack report verification failed: ' + (str(error) if isinstance(error, _ChainError)
                                                             else type(error).__name__))
                continue
            return entry, True
    return None, True


def matching(meta, body, *, api=None):
    """Return provenance verified against Slack and live authors; unverifiable evidence refuses."""
    return _matching(meta, body, api)[0]


def deciding(comments, repo, pr, *, policy, config, api=None):
    """Return (comment, receipt); comment metadata cannot authenticate a Dot verdict."""
    carrying = []
    for comment in comments:
        body = comment.get('body', '')
        if STAMP.match(body) or not policy['verdict'](body, config):
            continue
        sha = policy['reviewed_header_sha'](body.replace('REVIEW: BLOCKED', 'APPROVE', 1))
        receipt, attempted = _matching({'repo': repo, 'pr': pr, 'sha': sha}, body, api) if sha else (None, False)
        if receipt:
            carrying.append((comment, receipt))
        elif attempted or _claims_relay(body):
            # A claimed relay publication without provenance cannot become an ordinary owner approval.
            continue
        elif policy['trusted_commenter'](comment, config):
            carrying.append((comment, None))
    return max(carrying, key=lambda item: (str(item[0].get('created_at') or ''),
                                         int(item[0].get('id') or 0))) if carrying else (None, None)


def _claims_relay(body):
    fence = None
    for line in body.splitlines()[2:]:
        text = line.strip()
        marker = re.match(r'(`{3,}|~{3,})', text)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
        elif fence is None and (text == DOT_MARKER or re.fullmatch(r'<!-- dot-review:[^\n]* -->', text)):
            return True
    return False
