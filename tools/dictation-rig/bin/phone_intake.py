#!/usr/bin/env python3
"""Pull phone recordings privately onto the Studio, then reuse post-call processing."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import wave
from typing import BinaryIO
from datetime import datetime, timedelta
import urllib.error
import urllib.request

import post_call

MIME_TYPES = {'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/m4a', 'audio/x-m4a', 'audio/mpeg', 'audio/webm'}
ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


INTEGER_SETTINGS = ('CALL_MAX_BYTES', 'CALL_MAX_SECONDS', 'CALL_DAILY_ADMISSIONS',
                    'CALL_PROCESSORS', 'CALL_EXTRACTION_ATTEMPTS')
REQUIRED_SETTINGS = ('CALL_STUDIO_TOKEN', 'CARR_CAPTURE_BASE_URL', *INTEGER_SETTINGS,
                     'TYPESAFE_API_KEY')


class MissingSettings(ValueError):
    pass


def load_machine_settings() -> dict[str, str]:
    """Require the shared private settings before any intake or paid work."""
    from credential_env import load_carr_tokens, TokensFilePermissionError

    try:
        values = load_carr_tokens(REQUIRED_SETTINGS,
                                  path=Path.home() / '.config/carr/typesafe.env')
    except TokensFilePermissionError:
        raise ValueError('machine settings file must be private (mode 600)') from None
    missing = [name for name in REQUIRED_SETTINGS if not values.get(name, '').strip()]
    if missing:
        raise MissingSettings('missing required machine settings: ' + ', '.join(missing))
    for name in INTEGER_SETTINGS:
        try:
            valid = int(values[name]) > 0
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(name + ' must be a positive integer')
    if not values['CARR_CAPTURE_BASE_URL'].startswith('https://'):
        raise ValueError('CARR_CAPTURE_BASE_URL must use HTTPS')
    return values


def config_limit(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError('invalid phone intake limit')
    return min(value, default)


def required_json(path: Path) -> dict:
    value = post_call.read_json(path)
    if value is None:
        raise ValueError('intake artifact is unavailable')
    return value


@contextlib.contextmanager
def exclusive(path: Path, *, blocking: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def validate_metadata(value: dict) -> None:
    fields = {'schema_version', 'session_id', 'sha256', 'byte_count', 'duration_seconds',
              'mime_type', 'owner', 'started_at', 'consent_announced_at', 'session_token'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('invalid phone metadata')
    if (value['schema_version'] != 1 or not isinstance(value['session_id'], str)
            or not ID.fullmatch(value['session_id']) or value['owner'] not in {'joe', 'dell'}
            or not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256'])
            or value['mime_type'] not in MIME_TYPES or type(value['byte_count']) is not int
            or not 0 < value['byte_count'] <= config_limit('CALL_MAX_BYTES', 50 * 1024 * 1024)
            or type(value['duration_seconds']) not in (float, int)
            or not 0 < value['duration_seconds'] <= config_limit('CALL_MAX_SECONDS', 3600)
            or not isinstance(value['session_token'], str) or not value['session_token']):
        raise ValueError('invalid phone metadata or limits')
    for field in ('started_at', 'consent_announced_at'):
        if not isinstance(value[field], str) or datetime.fromisoformat(value[field].replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('timestamp requires timezone')


def receive(root: Path, metadata: dict, source: BinaryIO) -> dict:
    validate_metadata(metadata)
    session = root / f"phone-{metadata['session_id']}"
    with exclusive(root / '.phone-transfer.lock'):
        session.mkdir(mode=0o700, parents=True, exist_ok=True)
        receipt = post_call.read_json(session / 'transfer.json')
        if receipt is not None:
            if receipt['sha256'] != metadata['sha256']:
                raise ValueError('session content conflict')
            post_call.write_private(session / '.capture.json', {'session_token': metadata['session_token']})
            return receipt
        incoming = session / '.incoming'
        digest = hashlib.sha256()
        remaining = metadata['byte_count']
        try:
            fd = os.open(incoming, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError('incomplete recording')
                    handle.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if digest.hexdigest() != metadata['sha256']:
                raise ValueError('recording hash mismatch')
            os.replace(incoming, session / 'imported.audio')
            post_call.write_private(session / 'phone.json', metadata)
            post_call.write_private(session / '.capture.json', {'session_token': metadata['session_token']})
            post_call.write_private(session / 'meta.json', {'started': metadata['started_at'],
                                    'duration_seconds': metadata['duration_seconds']})
            post_call.write_private(session / 'announcement.json', {'announcement_fired_at': metadata['consent_announced_at']})
            post_call.write_private(session / post_call.CONTEXT_FILE, {
                'mode': 'weekly_deal_call', 'started_at': metadata['started_at'],
                'speaker_labels': {'mic': 'Imported speaker', 'system': 'Unknown speaker'},
                'speaker_method': 'single imported track; partner attribution unverified',
            })
            receipt = {'schema_version': 1, 'session_id': metadata['session_id'],
                       'sha256': metadata['sha256'], 'transferred': True}
            post_call.write_private(session / 'transfer.json', receipt)
            for path in session.glob('*.json'):
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
            with (session / '.capture.json').open('rb') as handle:
                os.fsync(handle.fileno())
            directory = os.open(session, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return receipt
        finally:
            incoming.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def pull_pending(root: Path, cfg: dict, token: str) -> dict:
    """Download and fsync each recording before acknowledging cloud removal."""
    counts = {'received': 0, 'pending': 0}
    endpoint = cfg['base_url'].rstrip('/') + '/capture/phone/v1/call-sessions'
    opener = urllib.request.build_opener(NoRedirect())
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': 'carr-quill-dictate/1.0 (phone-intake)'}
    try:
        with exclusive(root / '.phone-pull.lock', blocking=False):
            with opener.open(urllib.request.Request(endpoint, headers=headers), timeout=120) as response:
                listing = json.load(response)
            for metadata in listing['sessions']:
                try:
                    validate_metadata(metadata)
                    url = endpoint + '/' + metadata['session_id']
                    with opener.open(urllib.request.Request(url + '/audio', headers=headers), timeout=120) as response:
                        receive(root, metadata, response)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    if isinstance(exc, urllib.error.HTTPError):
                        exc.close()
                    counts['pending'] += 1
            for session in sorted(root.glob('phone-*')):
                if not (session / 'transfer.json').exists() or (session / 'handoff-ack.json').exists():
                    continue
                try:
                    metadata = required_json(session / 'phone.json')
                    validate_metadata(metadata)
                    receipt = required_json(session / 'transfer.json')
                    url = endpoint + '/' + metadata['session_id']
                    request = urllib.request.Request(url + '/ack',
                        data=json.dumps({'sha256': receipt['sha256']}).encode(),
                        headers={**headers, 'Content-Type': 'application/json'}, method='POST')
                    with opener.open(request, timeout=120) as response:
                        if json.load(response).get('ok') is not True:
                            raise ValueError('invalid handoff acknowledgement')
                    post_call.write_private(session / 'handoff-ack.json', {'ok': True})
                    counts['received'] += 1
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    if isinstance(exc, urllib.error.HTTPError):
                        exc.close()
                    counts['pending'] += 1
    except BlockingIOError:
        return {'busy': True}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        counts['pending'] += 1
    return counts


def extract(session: Path, *, decoder: str = '/opt/homebrew/bin/ffmpeg',
            distiller=post_call.default_distiller) -> dict:
    with exclusive(session / '.phone-extraction.lock', blocking=False):
        return _extract(session, decoder=decoder, distiller=distiller)


def _extract(session: Path, *, decoder: str, distiller) -> dict:
    import transcribe_session

    metadata = required_json(session / 'phone.json')
    validate_metadata(metadata)
    if (session / 'imported.audio').stat().st_size > config_limit('CALL_MAX_BYTES', 50 * 1024 * 1024):
        raise ValueError('recording exceeds call limit')
    maximum_attempts = config_limit('CALL_EXTRACTION_ATTEMPTS', 3)
    status = post_call.read_json(session / post_call.STATUS_FILE) or {}
    decoding = post_call.read_json(session / 'phone-extraction.json') or {}
    attempts = max(status.get('extraction_attempts', 0), decoding.get('attempts', 0))
    if not (session / post_call.REPORT_FILE).exists() and attempts >= maximum_attempts:
        status.update(state='needs_attention', next_attempt_at=None, reason='Call extraction attempt limit reached.')
        post_call.write_private(session / post_call.STATUS_FILE, status)
        return status
    if status.get('next_attempt_at') and post_call.now() < status['next_attempt_at']:
        return status
    if not (session / post_call.REPORT_FILE).exists():
        post_call.write_private(session / 'phone-extraction.json', {'attempts': attempts + 1})
    if not (session / 'transcript.json').exists():
        log = transcribe_session.Logger(session / 'transcribe.log')
        model = transcribe_session.resolve_model(log)
        with model.open('rb') as model_file:
            model_hash = hashlib.file_digest(model_file, 'sha256').hexdigest()
        transcribe_session.verify_model_pin(model_hash, log)
        with tempfile.TemporaryDirectory(prefix='carr-phone-') as tmp:
            wav = Path(tmp) / 'imported.wav'
            maximum = config_limit('CALL_MAX_SECONDS', 3600)
            result = subprocess.run([decoder, '-v', 'error', '-nostdin', '-y',
                                     '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'wav,mov,mp3,matroska,webm',
                                     '-i', str(session / 'imported.audio'),
                                     '-t', str(maximum + 1), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)],
                                    capture_output=True, timeout=180, check=False, env={'PATH': os.defpath})
            if result.returncode != 0:
                raise ValueError('imported audio could not be decoded')
            with wave.open(str(wav), 'rb') as handle:
                duration = handle.getnframes() / handle.getframerate()
            if duration <= 0 or duration > maximum:
                raise ValueError('decoded duration exceeds call limit')
            segments = transcribe_session.run_whisper(wav, model, transcribe_session.load_prompt(), Path(tmp), log, timeout=900, env={'PATH': os.defpath})
            if not segments:
                raise ValueError('no speech extracted')
        post_call.write_private(session / 'transcript.json', {
            'engine': transcribe_session.ENGINE_LABEL, 'model_sha256': model_hash,
            'speaker_method': 'single imported track; partner attribution unverified', 'speaker_tracks': {},
            'segments': transcribe_session.shift_and_tag(segments, 0, ['Imported speaker'] * len(segments)),
        })
    return post_call.process_session(session, distiller,
                                     max_attempts=maximum_attempts)


def bridge_module():
    spec = importlib.util.spec_from_file_location('phone_capture_bridge', Path(__file__).with_name('capture-bridge.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish_status(session: Path, cfg: dict, token: str, bridge) -> bool:
    """Exchange review questions and answers through the authenticated status report."""
    status = post_call.read_json(session / post_call.STATUS_FILE) or {}
    report = post_call.read_json(session / post_call.REPORT_FILE) or {}
    questions = [{'id': question['id'], 'question': question['question']}
                 for question in report.get('review_questions', []) if not question.get('resolved', False)]
    count = len(questions)
    state, reason = status.get('state'), status.get('reason_code', status.get('reason'))
    if report.get('checks_hold'):
        state, reason = 'held_review', 'budget'
    elif state == 'needs_attention':
        if reason not in {'extraction_failed', 'transcript_unavailable', 'transient_ceiling'}:
            reason = 'extraction_failed'
    elif state == 'held_review':
        if reason not in {'summary_repeats_transcript', 'transcript_unavailable', 'budget'}:
            reason = 'summary_repeats_transcript'
    elif count:
        state, reason = 'held_review', 'open_review_questions'
    else:
        state, reason = None, None
    payload = {'state': state, 'reason': reason, 'open_questions': count}
    if 'review_questions' in report:
        payload['review_questions'] = questions
    receipt = session / 'phone-status.json'
    previous = post_call.read_json(receipt) or {}
    pending = previous.get('pending_answer_outcomes', [])
    if pending:
        payload['answer_outcomes'] = pending
    if not count and previous == payload:
        return True
    metadata = required_json(session / 'phone.json')
    code, reply = bridge.http_request('POST',
        f"{cfg['base_url']}/capture/phone/v1/call-sessions/{metadata['session_id']}/status",
        payload, {'Authorization': f'Bearer {token}'})
    if code != 200 or reply.get('ok') is not True:
        return False
    acknowledged = {item['question_id'] for item in pending}
    outcomes = []
    for answer in reply.get('question_answers', []):
        if answer['question_id'] in acknowledged:
            continue
        current = post_call.read_json(session / post_call.REPORT_FILE) or {}
        question = next((q for q in current.get('review_questions', [])
                         if q['id'] == answer['question_id']), None)
        outcome = 'already_resolved'
        if question and not question.get('resolved', False):
            post_call.resolve_question(session, answer['question_id'], answer['answer'])
            outcome = 'applied'
        outcomes.append({'question_id': answer['question_id'], 'outcome': outcome})
    post_call.write_private(receipt, {**payload, **(
        {'pending_answer_outcomes': outcomes} if outcomes else {})})
    return True


def process_pending(root: Path, cfg: dict, token: str, *, slot: int = 0) -> dict:
    """One bounded sweep. The processor lock also excludes another invocation."""
    counts = {'ready': 0, 'pending': 0}
    config_limit('CALL_PROCESSORS', 1)
    if slot != 0:
        raise ValueError('only one phone processor is allowed')
    bridge = bridge_module()
    request = bridge.http_request

    class TransientNetworkError(Exception):
        pass

    def checked_request(*args, **kwargs):
        try:
            code, body = request(*args, **kwargs)
        except bridge.BridgeNetworkError as exc:
            raise TransientNetworkError('connection unavailable') from exc
        if code == 429 or 500 <= code < 600:
            raise TransientNetworkError(f'HTTP {code}')
        return code, body

    bridge.http_request = checked_request

    try:
        with exclusive(root / '.phone-processor-0.lock', blocking=False):
            for session in sorted(root.glob('phone-*')):
                if not (session / 'transfer.json').exists():
                    continue
                try:
                    if int(session.name.removeprefix('phone-').replace('-', ''), 16) % config_limit('CALL_PROCESSORS', 1) != slot:
                        continue
                    previous = post_call.read_json(session / post_call.STATUS_FILE) or {}
                    if previous.get('transient_since') and previous.get('next_attempt_at') and post_call.now() < previous['next_attempt_at']:
                        counts['pending'] += 1
                        continue
                    if not publish_status(session, cfg, token, bridge):
                        counts['pending'] += 1
                        continue
                    report = post_call.read_json(session / post_call.REPORT_FILE) or {}
                    if report.get('checks_hold'):
                        post_call.process_session(session)
                        if not publish_status(session, cfg, token, bridge):
                            counts['pending'] += 1
                            continue
                        previous = post_call.read_json(session / post_call.STATUS_FILE) or {}
                    if (session / 'ingested.json').exists() or previous.get('state') in {'needs_attention', 'held_review', 'filed'}:
                        if previous.get('transient_since') and previous.get('state') != 'needs_attention':
                            previous.pop('transient_since', None)
                            previous.pop('transient_count', None)
                            previous.pop('transient_expired', None)
                            previous['next_attempt_at'] = None
                            post_call.write_private(session / post_call.STATUS_FILE, previous)
                        if (session / 'ingested.json').exists() or previous.get('state') == 'filed':
                            continue
                    if previous.get('state') in {'needs_attention', 'held_review'}:
                        counts['pending'] += 1
                        continue
                    metadata = required_json(session / 'phone.json')
                    code, lease = bridge.http_request('POST',
                        f"{cfg['base_url']}/capture/phone/v1/call-sessions/{metadata['session_id']}/lease",
                        {}, {'Authorization': f'Bearer {token}'})
                    if code != 200 or not lease.get('session_token'):
                        counts['pending'] += 1
                        continue
                    post_call.write_private(session / '.capture.json', {'session_token': lease['session_token']})
                    status = extract(session, decoder=os.environ.get('CALL_AUDIO_DECODER', '/opt/homebrew/bin/ffmpeg'))
                    if not publish_status(session, cfg, token, bridge):
                        counts['pending'] += 1
                        continue
                    if status.get('state') not in {'ready_review', 'filed'}:
                        counts['pending'] += 1
                        continue
                    if bridge.publish_cmd(cfg, session) != 0:
                        publish_status(session, cfg, token, bridge)
                        counts['pending'] += 1
                        continue
                    report = required_json(session / post_call.REPORT_FILE)
                    code, receipt = bridge.http_request('POST',
                        f"{cfg['base_url']}/capture/phone/v1/call-sessions/{metadata['session_id']}/report",
                        report['report'], {'Authorization': f'Bearer {token}'})
                    if code != 200 or receipt.get('ok') is not True:
                        counts['pending'] += 1
                        continue
                    bridge.poll_cmd(cfg, session)
                    if not publish_status(session, cfg, token, bridge):
                        counts['pending'] += 1
                        continue
                    previous = post_call.read_json(session / post_call.STATUS_FILE) or {}
                    if previous.get('transient_since'):
                        previous.pop('transient_since', None)
                        previous.pop('transient_count', None)
                        previous.pop('transient_expired', None)
                        previous['next_attempt_at'] = None
                        post_call.write_private(session / post_call.STATUS_FILE, previous)

                    counts['ready'] += 1
                except (TransientNetworkError, bridge.BridgeNetworkError, ConnectionError, TimeoutError) as exc:
                    previous = post_call.read_json(session / post_call.STATUS_FILE) or {}
                    timestamp = post_call.now()
                    current = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    since = previous.get('transient_since', timestamp)
                    elapsed = current - datetime.fromisoformat(since.replace('Z', '+00:00'))
                    count = previous.get('transient_count', 0) + 1
                    expired = elapsed >= timedelta(hours=24)
                    preserve_state = previous.get('state') in {'needs_attention', 'held_review', 'filed'}
                    delay = min(30 * 2 ** min(count - 1, 5), 900)
                    previous.update({'schema_version': post_call.SCHEMA_VERSION, 'session': session.name,
                                     'state': previous.get('state') if preserve_state else ('needs_attention' if expired else 'blocked'), 'updated_at': timestamp,
                                     'transient_since': since, 'transient_count': count,
                                     'transient_expired': expired,
                                     'next_attempt_at': None if expired else (current + timedelta(seconds=delay)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                     'reason': (previous.get('reason') if preserve_state
                                                else f'Transient network error{" for 24 hours" if expired else ""}: {exc}')})
                    if expired and not preserve_state:
                        previous['reason_code'] = 'transient_ceiling'
                    post_call.write_private(session / post_call.STATUS_FILE, previous)
                    counts['pending'] += 1
                except Exception as exc:
                    previous = post_call.read_json(session / post_call.STATUS_FILE) or {}
                    previous.pop('transient_since', None)
                    previous.pop('transient_count', None)
                    previous.pop('transient_expired', None)
                    attempts = previous.get('extraction_attempts', 0) + 1
                    previous.update({'schema_version': post_call.SCHEMA_VERSION, 'session': session.name,
                                     'state': 'needs_attention' if attempts >= config_limit('CALL_EXTRACTION_ATTEMPTS', 3) else 'blocked',
                                     'updated_at': post_call.now(), 'extraction_attempts': attempts,
                                     'next_attempt_at': None, 'reason': f'Phone processing failed: {type(exc).__name__}'})
                    post_call.write_private(session / post_call.STATUS_FILE, previous)
                    counts['pending'] += 1
    except BlockingIOError:
        return {'busy': True}
    finally:
        bridge.http_request = request
    return counts


def serve(root: Path, cfg: dict, token: str) -> None:
    stopped = threading.Event()

    def processor(slot):
        try:
            while not stopped.is_set():
                process_pending(root, cfg, token, slot=slot)
                stopped.wait(30)
        except BaseException:
            os._exit(1)

    threads = [threading.Thread(target=processor, args=(slot,), daemon=True)
               for slot in range(config_limit('CALL_PROCESSORS', 1))]
    for thread in threads:
        thread.start()
    try:
        while not stopped.is_set():
            pull_pending(root, cfg, token)
            stopped.wait(30)
    finally:
        stopped.set()


def main() -> None:
    from lib.call_settings import configure_environment
    configure_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--process', action='store_true', help='Pull recordings and run one processing sweep')
    args = parser.parse_args()
    try:
        settings = load_machine_settings()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(0)
    token = settings.pop('CALL_STUDIO_TOKEN')
    os.environ.pop('CALL_STUDIO_TOKEN', None)
    os.environ.update(settings)
    base_url = settings['CARR_CAPTURE_BASE_URL']
    if args.process:
        intake = pull_pending(args.root, {'base_url': base_url}, token)
        print(json.dumps({'intake': intake, 'processors': [
            process_pending(args.root, {'base_url': base_url}, token, slot=slot)
            for slot in range(config_limit('CALL_PROCESSORS', 1))]}))
    else:
        serve(args.root, {'base_url': base_url}, token)


if __name__ == "__main__":
    main()
