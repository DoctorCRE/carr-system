#!/usr/bin/env python3
"""Synthetic Outlook corpus, guarded differential locations, and atlas hypotheses."""
import argparse
import base64
import hashlib
import importlib.util
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
SIZES = (0, 1, 7, 31, 127, 255, 1023, 4095, 16384, 65536)


def corpus(account, nonce=None):
    if not re.fullmatch(r'[A-Za-z0-9._+-]+@(?:outlook\.com|example\.test)', account):
        raise ValueError('invalid synthetic account')
    nonce = nonce or uuid.uuid4().hex
    cases = []
    for index, (kind, size) in enumerate((kind, size) for kind in ('plain', 'html') for size in SIZES):
        case_id = f'case-{index:02d}'
        marker = f'HX-{nonce}-{index:02d}-' + 'S' * (index + 1)
        body_marker = f'HB-{nonce}-{index:02d}-'
        body = ((chr(65 + index) + body_marker + 'b' * size)[:size] if size < 64 else
                ((f'<p>{body_marker}' + 'h' * size)[:size - 4] + '</p>' if kind == 'html' else
                 (body_marker + 'p' * size)[:size]))
        instant = datetime(2000, 2, 29, 12, 34, 56, tzinfo=timezone.utc) + timedelta(days=index * 367, seconds=index * 97)
        to = [account] + [f'to-{index}-{n}@example.test' for n in range(index % 4)]
        cc = [f'cc-{index}-{n}@example.test' for n in range(index % 3)]
        importance = ('low', 'normal', 'high')[index % 3]
        headers = {
            'From': account, 'To': ', '.join(to), 'Subject': marker,
            'Date': format_datetime(instant), 'Message-ID': f'<{nonce}.{index}@example.test>',
            'X-Unsent': '1', 'Importance': importance,
            'X-Priority': {'high': '1', 'normal': '3', 'low': '5'}[importance],
            'MIME-Version': '1.0', 'Content-Type': f'text/{kind}; charset="utf-8"',
            'Content-Transfer-Encoding': 'base64',
        }
        if cc:
            headers['Cc'] = ', '.join(cc)
        cases.append({'id': case_id, 'file': f'{case_id}.emltpl', 'headers': headers,
                      'subject': marker, 'body': body, 'body_length': size, 'format': kind,
                      'date': instant.isoformat(), 'to_count': len(to), 'cc_count': len(cc),
                      'importance': importance, 'draft': True,
                      'markers': [marker] + ([body_marker] if size >= 64 else [])})
    return {'schema': 'hxstore-corpus/v1', 'nonce': nonce, 'account': account,
            'unsupported_flags': ['read', 'follow_up'], 'cases': cases}


def write_corpus(account, out):
    manifest = corpus(account)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError('output directory must be empty')
    for case in manifest['cases']:
        headers = '\r\n'.join(f'{k}: {v}' for k, v in case['headers'].items())
        encoded = base64.encodebytes(case['body'].encode()).replace(b'\n', b'\r\n')
        (out / case['file']).write_bytes(headers.encode() + b'\r\n\r\n' + encoded)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def load_manifest(path):
    manifest = json.loads(path.read_bytes())
    if not re.fullmatch(r'[0-9a-f]{32}', manifest['nonce']):
        raise ValueError('invalid nonce')
    if manifest != corpus(manifest['account'], manifest['nonce']):
        raise ValueError('manifest differs from corpus contract')
    return manifest


def encodings(value):
    if isinstance(value, str):
        return [(encoding, value.encode(encoding)) for encoding in ('utf-8', 'utf-16le') if value]
    return [(f'u{width * 8}{suffix}', value.to_bytes(width, order))
            for width in (2, 4, 8) for suffix, order in (('le', 'little'), ('be', 'big'))
            if 0 <= value < 1 << (width * 8)]


def needles_for(manifest):
    needles = []
    for case in manifest['cases']:
        fields = {key.lower(): value for key, value in case['headers'].items()}
        for header in ('To', 'Cc'):
            for index, address in enumerate(case['headers'].get(header, '').split(', ') if case['headers'].get(header) else []):
                fields[f'{header.lower()}.{index}'] = address
        fields.update(body=case['body'], body_length=case['body_length'],
                      to_count=case['to_count'], cc_count=case['cc_count'], draft=1)
        for field, value in fields.items():
            for encoding, data in encodings(value):
                needles.append((case['id'], field, encoding, data))
        instant = datetime.fromisoformat(case['date'])
        seconds = int((instant - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds())
        for encoding, value in [('unix-s', seconds), ('unix-ms', seconds * 1000),
                                ('filetime', (seconds + 11644473600) * 10000000)]:
            for suffix, order in [('le', 'little'), ('be', 'big')]:
                needles.append((case['id'], 'date', f'{encoding}-{suffix}', value.to_bytes(8, order)))
                if encoding == 'unix-s' and seconds <= 0xffffffff:
                    needles.append((case['id'], 'date', f'{encoding}-u32{suffix}', value.to_bytes(4, order)))
        for index, marker in enumerate(case['markers']):
            for encoding, data in encodings(marker):
                needles.append((case['id'], f'marker.{index}', encoding, data))
    return needles


def differential(args):
    if not args.synthetic_store:
        raise ValueError('synthetic opt-in required')
    manifest = load_manifest(args.manifest)
    needles = needles_for(manifest)
    labels = {}
    for case_id, field, encoding, data in needles:
        labels.setdefault(data, []).append((case_id, field, encoding))
    unique = list(labels)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'needles'
        path.write_text(''.join(data.hex() + '\n' for data in unique))
        result = subprocess.run([str(ROOT / 'vendor/hxprobe/target/debug/hxprobe'), 'locate', str(args.before), str(args.after), str(path)],
                                capture_output=True, timeout=300)
    if result.returncode:
        raise ValueError('store rejected')
    report = json.loads(result.stdout)
    counts = dict(zip(unique, report.pop('match_counts')))
    found = {(case_id, field) for data, definitions in labels.items() if counts[data]['after'] > 0
             for case_id, field, _ in definitions}
    required = {(c['id'], f'marker.{i}') for c in manifest['cases'] for i in range(len(c['markers']))}
    if not required <= found:
        raise ValueError('markers absent')
    hits = []
    for hit in report['hits']:
        data = unique[hit.pop('needle')]
        hits.extend(dict(hit, case_id=case_id, field=field, encoding=encoding)
                    for case_id, field, encoding in labels[data])
    report['hits'] = hits
    report['matches'] = [dict(case_id=case_id, field=field, encoding=encoding, **counts[data])
                         for case_id, field, encoding, data in needles]
    report.update(schema='hxstore-differential/v1', marker_guard='all_after_markers_found',
                  offset_policy='file offsets are physical; payload and record offsets are decompressed; ranges are half-open and position-aligned')
    return report


def import_hypotheses(args):
    manifest = load_manifest(args.manifest)
    document = json.loads(args.hypotheses.read_bytes())
    if set(document) != {'schema', 'corpus_sha256', 'hypotheses'} or document['schema'] != 'hxstore-decode-hypotheses/v1':
        raise ValueError('invalid hypothesis document')
    digest = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    if document['corpus_sha256'] != digest or not document['hypotheses']:
        raise ValueError('wrong hypothesis corpus')
    case_ids = {case['id'] for case in manifest['cases']}
    hypotheses = []
    for h in document['hypotheses']:
        if 'corpus_sha256' in h or not set(h['evidence']) <= case_ids:
            raise ValueError('unknown evidence case')
        for case in manifest['cases']:
            if case['id'] in h['evidence']:
                value = case
                for segment in h['manifest_field'].split('.'):
                    value = value[segment]
                if not isinstance(value, (str, int, bool)):
                    raise ValueError('hypothesis must identify a scalar manifest field')
        hypotheses.append(dict(h, corpus_sha256=digest))
    spec = importlib.util.spec_from_file_location('hxstore_atlas', ROOT / 'ops/hxstore-atlas.py')
    atlas_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(atlas_module)
    atlas = json.loads(args.atlas.read_bytes())
    atlas_module.validate_hypotheses(hypotheses)
    merged = {h['field_id']: h for h in atlas.get('decode_hypotheses', [])}
    merged.update((h['field_id'], h) for h in hypotheses)
    atlas['decode_hypotheses'] = list(merged.values())
    return atlas_module.validate(atlas)


class InputParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'synthetic harness input rejected\n')


def main():
    parser = InputParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generate = commands.add_parser('corpus', help='write templates and manifest into an empty directory')
    generate.add_argument('--account', required=True, help='dedicated synthetic Outlook.com account')
    generate.add_argument('--out', type=Path, required=True)
    diff = commands.add_parser('diff', help='report metadata only after synthetic marker validation')
    diff.add_argument('before', type=Path)
    diff.add_argument('after', type=Path)
    diff.add_argument('--manifest', type=Path, required=True)
    diff.add_argument('--synthetic-store', action='store_true')
    hypotheses = commands.add_parser('hypotheses', help='validate evidence and emit an augmented atlas to stdout')
    hypotheses.add_argument('--manifest', type=Path, required=True)
    hypotheses.add_argument('--hypotheses', type=Path, required=True)
    hypotheses.add_argument('--atlas', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'corpus':
            write_corpus(args.account, args.out)
        elif args.command == 'diff':
            print(json.dumps(differential(args), separators=(',', ':')))
        elif args.command == 'hypotheses':
            print(json.dumps(import_hypotheses(args), indent=2))
    except (ValueError, OSError, KeyError, TypeError, AttributeError, OverflowError, subprocess.SubprocessError):
        print('synthetic harness input rejected', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
