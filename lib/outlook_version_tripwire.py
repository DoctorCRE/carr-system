"""Private, receipt-backed Outlook version review. Never decodes mailbox values."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid


class IntakeBlocked(RuntimeError):
    """The installed version has no accepted complete observation."""


def _version(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'build'}
            or any(not isinstance(v, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', v)
                   or len(v) > 64 for v in value.values())):
        raise IntakeBlocked('invalid_version')
    return value


def _pin(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise IntakeBlocked('invalid_source_pin')
    return value


def _read(path):
    with path.open() as stream:
        return json.load(stream)


def _write(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@contextmanager
def _lock(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    fd = os.open(root / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _shape(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('invalid_observation_shape')


def _number(value):
    if type(value) is not int or value < 0:
        raise ValueError('invalid_observation_count')
    return value


def _percent(value, decoded, total):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or abs(value - 100 * decoded / total) > 0.000001):
        raise ValueError('invalid_observation_percentage')


def _validate_observation(report):
    _shape(report, 'schema blocks records unknown_fields coverage issues issue_counts')
    for key in ('blocks', 'records', 'unknown_fields', 'issues', 'issue_counts'):
        if not isinstance(report[key], list):
            raise ValueError('invalid_observation_rows')
    coverage = report['coverage']
    _shape(coverage, 'block_signatures verified_blocks unverified_blocks undecodable_blocks '
                     'verified_payload_bytes decoded_bytes decoded_byte_percent')
    for key in set(coverage) - {'decoded_byte_percent'}:
        _number(coverage[key])
    if coverage['unverified_blocks'] or coverage['undecodable_blocks']:
        raise ValueError('invalid_regions')
    for row in report['blocks']:
        _shape(row, 'block_type occurrences payload_bytes')
        for value in row.values():
            _number(value)
    if (sum(b['payload_bytes'] for b in report['blocks']) != coverage['verified_payload_bytes']
            or sum(b['occurrences'] for b in report['blocks']) != coverage['verified_blocks']
            or coverage['block_signatures'] != coverage['verified_blocks']):
        raise ValueError('invalid_block_totals')
    for row in report['records']:
        _shape(row, 'record_type occurrences total_bytes decoded_bytes decoded_byte_percent '
                    'wire_field_ids decoded_field_id_percent fields')
        for key in ('occurrences', 'total_bytes', 'decoded_bytes'):
            _number(row[key])
        if (not row['occurrences'] or not row['total_bytes']
                or row['decoded_bytes'] > row['total_bytes']
                or row['wire_field_ids'] is not None or row['decoded_field_id_percent'] is not None
                or not isinstance(row['fields'], list)):
            raise ValueError('invalid_record_counts')
        _percent(row['decoded_byte_percent'], row['decoded_bytes'], row['total_bytes'])
        for field in row['fields']:
            _shape(field, 'field_id identifier_kind wire_type occurrences')
            if not 0 < _number(field['occurrences']) <= row['occurrences']:
                raise ValueError('invalid_field_count')
    if (sum(r['total_bytes'] for r in report['records']) != coverage['verified_payload_bytes']
            or sum(r['decoded_bytes'] for r in report['records']) != coverage['decoded_bytes']
            or not coverage['verified_payload_bytes']):
        raise ValueError('invalid_record_totals')
    _percent(coverage['decoded_byte_percent'], coverage['decoded_bytes'], coverage['verified_payload_bytes'])
    remainders = {r['record_type']: r for r in report['records'] if r['decoded_bytes'] < r['total_bytes']}
    seen = set()
    for row in report['unknown_fields']:
        _shape(row, 'record_type field_id wire_type occurrences unknown_bytes')
        name = row['record_type']
        if name not in remainders or name in seen or row['field_id'] is not None or row['wire_type'] != 'unknown':
            raise ValueError('invalid_unknown_group')
        seen.add(name)
        record = remainders[name]
        if (not 0 < _number(row['occurrences']) <= record['occurrences']
                or _number(row['unknown_bytes']) != record['total_bytes'] - record['decoded_bytes']):
            raise ValueError('invalid_unknown_count')
    if seen != set(remainders):
        raise ValueError('missing_unknown_group')


def structural_map(report):
    """Project the existing observe contract; occurrence/byte totals are not format changes."""
    _validate_observation(report)
    if report['schema'] != 'hxprobe-observation/v1':
        raise ValueError('invalid_observation')
    if report['issues'] or report['issue_counts']:
        raise ValueError('invalid_regions')
    blocks = sorted({row['block_type'] for row in report['blocks']})
    records = {}
    for row in report['records']:
        name = row['record_type']
        if not re.fullmatch(r'mail|unframed|object\.class\.[0-9a-f]{4}', name) or name in records:
            raise ValueError('invalid_record_type')
        fields = []
        for field in row['fields']:
            if not re.fullmatch(r'[A-Za-z0-9_.]{1,100}', field['field_id']):
                raise ValueError('invalid_field')
            if not re.fullmatch(r'[A-Za-z0-9_\[\]-]{1,40}', field['wire_type']):
                raise ValueError('invalid_wire_type')
            if field['identifier_kind'] not in {'byte_offset', 'string_run_selector'}:
                raise ValueError('invalid_identifier_kind')
            fields.append([field['field_id'], field['identifier_kind'], field['wire_type']])
        records[name] = sorted(fields)
    if not records or report['coverage']['verified_payload_bytes'] <= 0:
        raise ValueError('empty_observation')
    if any(type(kind) is not int or kind not in {8, 16} for kind in blocks):
        raise ValueError('unknown_block_type')
    return {'blocks': blocks, 'records': records}


class Tripwire:
    def __init__(self, root, source_pin, known_types):
        self.root = Path(root)
        self.source = _pin(source_pin)
        self.known_types = set(known_types)

    def _attempt(self, version):
        identity = {'version': _version(version), 'source_pin': self.source}
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'carr-outlook-tripwire/v1/' + _digest(identity)))

    def _dirs(self):
        for name in ('attempts', 'alerts', 'snapshots'):
            (self.root / name).mkdir(mode=0o700, exist_ok=True)

    def _finish(self, path, attempt):
        _write(path, attempt)
        alert = self.root / 'alerts' / (attempt['id'] + '.json')
        if not alert.exists():
            _write(alert, {
                'schema': 'outlook-version-alert/v1', 'recipient': 'orchestrator', 'idempotency_key': attempt['id'],
                'version': attempt['version'], 'source_pin': self.source,
                'status': attempt['status'], 'reason': attempt['reason'],
                'baseline': attempt.get('baseline'),
                'candidate_difference_counts': attempt.get('differences', {}),
                'candidate_record_types': attempt.get('candidate_types', {}),
                'unknown_record_types': attempt.get('unknown_record_types', []),
                'unknown_record_type_count': attempt.get('unknown_record_type_count', 0),
                'action': 'Hold intake. Review the private observation and source-bound atlas. '
                          'Accept only a complete supported observation; reconcile interrupted receipts before retry.',
                'limitations': 'Structural candidates only. Type presence can change with normal activity. '
                               'No ownership, field semantics, or full mapping is established.'})
        return attempt

    def check(self, version, snapshot, observe):
        attempt_id = self._attempt(version)
        with _lock(self.root):
            self._dirs()
            path = self.root / 'attempts' / (attempt_id + '.json')
            if path.exists():
                attempt = _read(path)
                if attempt['status'] == 'started':
                    attempt.update(status='blocked', reason='interrupted_requires_reconciliation')
                self._finish(path, attempt)
                try:
                    _accepted(self.root, version, self.source)
                    return dict(attempt, status='accepted', reason='version_accepted')
                except (IntakeBlocked, OSError, ValueError, KeyError, TypeError):
                    return attempt
            attempt = {'schema': 'outlook-version-attempt/v1', 'id': attempt_id,
                       'version': version, 'source_pin': self.source,
                       'status': 'started', 'reason': 'snapshot_pending'}
            _write(path, attempt)
            work = self.root / 'snapshots' / attempt_id
            work.mkdir(mode=0o700)
            failure = 'snapshot_failed'
            try:
                snapshot(work)
                if not (work / 'store.hxd').is_file():
                    raise ValueError('snapshot_missing')
                attempt['snapshot_receipt'] = {'complete': True}
                attempt['reason'] = 'observation_pending'
                _write(path, attempt)
                failure = 'decoder_failed'
                report = observe(work / 'store.hxd')
                failure = 'invalid_observation'
                mapping = structural_map(report)
                _write(work / 'observation.json', report)
                attempt['observation_receipt'] = _digest(report)
                attempt['map'] = mapping
                failure = 'baseline_invalid'
                baseline = self.root / 'accepted.json'
                accepted = _read(baseline) if baseline.exists() else None
                if accepted:
                    _accepted(self.root, accepted['version'], accepted['source_pin'])
                    attempt['baseline'] = {k: accepted[k] for k in ('id', 'version', 'source_pin')}
                    attempt['baseline']['map_sha256'] = _digest(accepted['map'])
                old = accepted['map'] if accepted else {'blocks': [], 'records': {}}
                before, after = old['records'], mapping['records']
                added = sorted(after.keys() - before.keys())
                absent = sorted(before.keys() - after.keys())
                changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
                attempt['differences'] = {
                    'added_record_types': len(added), 'absent_record_types': len(absent),
                    'changed_record_layouts': len(changed),
                    'block_type_changes': len(set(old['blocks']) ^ set(mapping['blocks']))}
                attempt['candidate_types'] = {'added': added[:32], 'absent': absent[:32], 'changed': changed[:32]}
                unknown = sorted(set(mapping['records']) - self.known_types)
                attempt['unknown_record_types'] = unknown[:32]
                attempt['unknown_record_type_count'] = len(unknown)
                if unknown:
                    attempt.update(status='blocked', reason='unknown_record_type')
                else:
                    attempt.update(status='review_required', reason='version_not_accepted')
            except Exception:
                attempt.update(status='blocked', reason=failure)
            return self._finish(path, attempt)

    def accept(self, version):
        with _lock(self.root):
            attempt = _read(self.root / 'attempts' / (self._attempt(version) + '.json'))
            if attempt['status'] != 'review_required':
                raise IntakeBlocked('observation_not_acceptable')
            report = _read(self.root / 'snapshots' / attempt['id'] / 'observation.json')
            if _digest(report) != attempt['observation_receipt'] or structural_map(report) != attempt['map']:
                raise IntakeBlocked('observation_receipt_mismatch')
            _write(self.root / 'accepted.json', attempt)


def _accepted(root, installed_version, source_pin):
    accepted = _read(root / 'accepted.json')
    if accepted['version'] != installed_version or accepted['source_pin'] != source_pin:
        raise IntakeBlocked('version_or_source_not_accepted')
    attempt = _read(root / 'attempts' / (accepted['id'] + '.json'))
    report = _read(root / 'snapshots' / accepted['id'] / 'observation.json')
    if (attempt != accepted or accepted['status'] != 'review_required'
            or _digest(report) != accepted['observation_receipt']
            or structural_map(report) != accepted['map']):
        raise IntakeBlocked('acceptance_receipt_invalid')
    return accepted


def require_accepted(root, installed_version, source_pin):
    """Call before snapshot, full decoder export, or intake side effects. Fails closed."""
    _version(installed_version)
    _pin(source_pin)
    root = Path(root)
    try:
        with _lock(root):
            _accepted(root, installed_version, source_pin)
    except (OSError, ValueError, KeyError, TypeError):
        raise IntakeBlocked('acceptance_unavailable') from None


def preflight_alert(root):
    root = Path(root)
    with _lock(root):
        (root / 'alerts').mkdir(mode=0o700, exist_ok=True)
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'carr-outlook-tripwire/v1/preflight_failed'))
        path = root / 'alerts' / (identity + '.json')
        if not path.exists():
            _write(path, {'schema': 'outlook-version-alert/v1', 'recipient': 'orchestrator', 'idempotency_key': identity,
                          'status': 'blocked', 'reason': 'preflight_failed',
                          'action': 'Hold intake. Verify app metadata, reviewed observer pin, source files and private state permissions.',
                          'limitations': 'No snapshot or complete observation receipt exists for this preflight.'})
