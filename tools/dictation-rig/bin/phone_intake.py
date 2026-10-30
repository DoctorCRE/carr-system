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
import tempfile
import threading
import wave
from typing import BinaryIO
from datetime import datetime
import urllib.error
import urllib.request

import post_call

MIME_TYPES = {'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/m4a', 'audio/x-m4a', 'audio/mpeg', 'audio/webm'}
ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def config_limit(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError('invalid phone intake limit')
    return value


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
    import transcribe_session

    if not (session / 'transcript.json').exists():
        metadata = required_json(session / 'phone.json')
        validate_metadata(metadata)
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
                                    capture_output=True, timeout=180, check=False)
            if result.returncode != 0:
                raise ValueError('imported audio could not be decoded')
            with wave.open(str(wav), 'rb') as handle:
                duration = handle.getnframes() / handle.getframerate()
            if duration <= 0 or duration > maximum:
                raise ValueError('decoded duration exceeds call limit')
            segments = transcribe_session.run_whisper(wav, model, transcribe_session.load_prompt(), Path(tmp), log, timeout=900)
            if not segments:
                raise ValueError('no speech extracted')
        post_call.write_private(session / 'transcript.json', {
            'engine': transcribe_session.ENGINE_LABEL, 'model_sha256': model_hash,
            'speaker_method': 'single imported track; partner attribution unverified', 'speaker_tracks': {},
            'segments': transcribe_session.shift_and_tag(segments, 0, ['Imported speaker'] * len(segments)),
        })
    return post_call.process_session(session, distiller, run_checks=False,
                                     max_attempts=config_limit('CALL_EXTRACTION_ATTEMPTS', 3))


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
    if state == 'needs_attention':
        if reason not in {'extraction_failed', 'transcript_unavailable', 'transient_ceiling'}:
            reason = 'extraction_failed'
    elif state == 'held_review':
        if reason not in {'summary_repeats_transcript', 'transcript_unavailable'}:
            reason = 'summary_repeats_transcript'
    elif count:
        state, reason = 'held_review', 'open_review_questions'
    else:
        state, reason = None, None
    payload = {'state': state, 'reason': reason, 'open_questions': count}
    if 'review_questions' in report:
        payload['review_questions'] = questions
    receipt = session / 'phone-status.json'
    if not count and post_call.read_json(receipt) == payload:
        return True
    metadata = required_json(session / 'phone.json')
    code, reply = bridge.http_request('POST',
        f"{cfg['base_url']}/capture/phone/v1/call-sessions/{metadata['session_id']}/status",
        payload, {'Authorization': f'Bearer {token}'})
    if code != 200 or reply.get('ok') is not True:
        return False
    for answer in reply.get('question_answers', []):
        if any(question['id'] == answer['question_id'] for question in questions):
            post_call.resolve_question(session, answer['question_id'], answer['answer'])
    post_call.write_private(receipt, payload)
    return True


def process_pending(root: Path, cfg: dict, token: str, *, slot: int = 0) -> dict:
    """One bounded sweep. The processor lock also excludes another invocation."""
    from transcribe_session import TranscribeError
    counts = {'ready': 0, 'pending': 0}
    bridge = bridge_module()
    try:
        with exclusive(root / f'.phone-processor-{slot}.lock', blocking=False):
            for session in sorted(root.glob('phone-*')):
                if not (session / 'transfer.json').exists():
                    continue
                try:
                    if int(session.name.removeprefix('phone-').replace('-', ''), 16) % config_limit('CALL_PROCESSORS', 1) != slot:
                        continue
                    if not publish_status(session, cfg, token, bridge):
                        counts['pending'] += 1
                        continue
                    if (session / 'ingested.json').exists():
                        continue
                    local = post_call.read_json(session / post_call.STATUS_FILE) or {}
                    if local.get('state') in {'needs_attention', 'held_review'}:
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
                    counts['ready'] += 1
                except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, bridge.BridgeNetworkError, TranscribeError):
                    counts['pending'] += 1
    except BlockingIOError:
        return {'busy': True}
    return counts


def serve(root: Path, cfg: dict, token: str) -> None:
    stopped = threading.Event()

    def processor(slot):
        while not stopped.is_set():
            process_pending(root, cfg, token, slot=slot)
            stopped.wait(30)

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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--process', action='store_true', help='Pull recordings and run one processing sweep')
    args = parser.parse_args()
    token = os.environ.get('CALL_STUDIO_TOKEN', '')
    if not token:
        parser.error('CALL_STUDIO_TOKEN is required')
    base_url = os.environ.get('CARR_CAPTURE_BASE_URL', '')
    if not base_url.startswith('https://'):
        parser.error('CARR_CAPTURE_BASE_URL must use HTTPS')
    if args.process:
        intake = pull_pending(args.root, {'base_url': base_url}, token)
        print(json.dumps({'intake': intake, 'processors': [
            process_pending(args.root, {'base_url': base_url}, token, slot=slot)
            for slot in range(config_limit('CALL_PROCESSORS', 1))]}))
    else:
        serve(args.root, {'base_url': base_url}, token)
