#!/usr/bin/env python3
"""Headless Friday social batch: plan, check and submit future review drafts to Blotato.

    ./run.sh social-batch plan
    ./run.sh social-batch check <drafts.json>
    ./run.sh social-batch submit <drafts.json>
    ./run.sh social-batch render <card.html> <card.png> --size 1080x1350

Design: docs/social/headless-batch-design.md (WR-000561). The model writes drafts.json;
this script owns every fixed rule. The only Blotato writes are a media upload and a
post with a future scheduledTime, so nothing publishes before Joe reviews it.
Week boundaries are America/Chicago. Prints one JSON object; never prints the key.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import http.server
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo('America/Chicago')
UTC = timezone.utc
API = 'https://backend.blotato.com/v2'
CADENCE = REPO / 'ops/social/cadence.v1.json'
ACCOUNTS = REPO / 'claude-tree/skills/social-media-manager/config/blotato-accounts.json'
OUT = REPO / 'out/social-batch'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
CARD_HOST = 'card.test'  # also in tools/card-chrome.mjs
CARD_LAUNCHER = REPO / 'tools/card-chrome.mjs'
FONTS = REPO / 'claude-tree/skills/write-content/graphics/fonts'
FONT_TYPES = {'.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2'}
SCHEMA = 'carr-social-batch-drafts/v1'
BANK = 'content-inspiration-bank'

CARR_PLATFORMS = ('linkedin', 'facebook', 'instagram')
SINGLE_CARDS = {'single_card', 'split_stat', 'big_number', 'quote_card'}
FORMATS = SINGLE_CARDS | {'text', 'text_question', 'carousel', 'chart', 'animated_card', 'animated_gif',
                          'diagram', 'annotated_screenshot'}
KINDS = {None, 'take', 'series', 'story_time', 'quote_tweet'}
ROUTES = {'webfetch', 'curl', 'grok_only', 'bank'}
WAIVABLE = {'standalone', 'one_liner', *CARR_PLATFORMS, 'x_visual', 'x_series', 'story_time', 'x_every_day'}
TOP_KEYS = {'schema', 'week_start', 'plan_digest', 'drafts', 'run_notes'}
DRAFT_KEYS = {'id', 'platform', 'lane', 'kind', 'format', 'local_time', 'text', 'media', 'visual', 'sources',
              'lint_review_clearances', 'bank_angles', 'alt_text'}
DRAFT_REQUIRED = {'id', 'platform', 'lane', 'format', 'local_time', 'text'}
NOTE_KEYS = {'grok', 'claims_review', 'improved', 'not_improved', 'skipped_slots', 'bank_corrections'}
MEDIA_TYPES = {'.png': 'image/png', '.gif': 'image/gif', '.mp4': 'video/mp4'}
STORY_FLAG = re.compile(r'\b(made[ -]up|invented|composite|hypothetical|fictional|not a real)\b', re.I)
STATUS_URL = re.compile(r'https?://(?:www\.)?(?:x|twitter)\.com/\w+/status/\d+', re.I)
HASHTAG = re.compile(r'(?<![\w&])#\w+')


class Stop(Exception):
    """A run-level stop: queue_unreadable, spec_changed, spec_unreadable or submit_failed."""

    def __init__(self, code: str, detail: str):
        super().__init__(f'{code}: {detail}')
        self.code, self.detail = code, detail


@dataclasses.dataclass
class Context:
    now: datetime
    transport: Callable[[str, str, dict, bytes | None], tuple[int, bytes]]
    doctrine: Callable[[str], dict]
    verb: Callable[[str, dict], dict]
    out: Path = OUT
    cadence_path: Path = CADENCE
    accounts_path: Path = ACCOUNTS
    receipt: str | None = None

    @property
    def cadence(self) -> dict:
        return json.loads(self.cadence_path.read_text())

    @property
    def accounts(self) -> dict:
        return json.loads(self.accounts_path.read_text())


# ---------------------------------------------------------------- time
def parse_utc(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('timestamp without zone')
    return value.astimezone(UTC)


def stamp(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def local_to_utc(local_time: str) -> str:
    return stamp(_local(local_time))


def _local(local_time: str) -> datetime:
    naive = datetime.fromisoformat(local_time)
    if naive.tzinfo is not None or naive.second or naive.microsecond:
        raise ValueError('local_time must be a wall-clock minute without a zone')
    first, second = naive.replace(tzinfo=ZONE, fold=0), naive.replace(tzinfo=ZONE, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError('local_time falls in the DST change hour')
    if first.astimezone(UTC).astimezone(ZONE).replace(tzinfo=None) != naive:
        raise ValueError('local_time does not exist in America/Chicago')
    return first


def target_week(now: datetime) -> date:
    """The first Monday strictly after the run date, in America/Chicago."""
    today = now.astimezone(ZONE).date()
    return today + timedelta(days=7 - today.weekday())


def bounds(week: date) -> tuple[datetime, datetime]:
    return (datetime.combine(week, time(), ZONE).astimezone(UTC),
            datetime.combine(week + timedelta(days=7), time(), ZONE).astimezone(UTC))


def minute(text: str) -> str:
    return stamp(parse_utc(text))[:16]


# ---------------------------------------------------------------- Blotato
def http_transport(key: Callable[[], str] | None = None, opener=urllib.request.urlopen):
    """Real HTTP. The key header is sent to backend.blotato.com only, never to media hosts."""
    cache: dict[str, str] = {}

    def send(method, url, headers, data):
        headers = dict(headers)
        if url.startswith(API + '/'):
            if 'key' not in cache:
                cache['key'] = (key or _key)()
            headers['blotato-api-key'] = cache['key']
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with opener(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Stop('queue_unreadable', f'{method} {urllib.parse.urlsplit(url).path}: {type(exc).__name__}') from None
    return send


def _key() -> str:
    """The key from the environment, or from ~/.zprofile in a child shell. Never logged."""
    if os.environ.get('BLOTATO_API_KEY'):
        return os.environ['BLOTATO_API_KEY']
    result = subprocess.run(['/bin/zsh', '-c', 'source ~/.zprofile >/dev/null 2>&1; printf %s "${BLOTATO_API_KEY:-}"'],
                            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    if not result.stdout:
        raise Stop('queue_unreadable', 'Blotato key unavailable')
    return result.stdout


class Blotato:
    def __init__(self, transport):
        self.send = transport

    def listing(self, start: datetime, end: datetime) -> list[dict]:
        query = urllib.parse.urlencode({'since': stamp(start), 'until': stamp(end), 'limit': 100})
        status, raw = self.send('GET', f'{API}/posts?{query}', {}, None)
        if status != 200:
            raise Stop('queue_unreadable', f'listing answered HTTP {status}')
        try:
            items = json.loads(raw)['items']
            if not isinstance(items, list):
                raise TypeError('items')
            for value in items:
                parse_utc(value['postTime'])
                if not (isinstance(value['platform'], str) and isinstance(value['state']['type'], str)):
                    raise TypeError('item')
                str(value['account']['id'])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise Stop('queue_unreadable', 'listing answer is not the recorded shape') from None
        if len(items) >= 100:
            raise Stop('queue_unreadable', 'listing may be truncated at 100 items')
        # The API window is trusted only as far as this local-week filter agrees.
        return [i for i in items if start <= parse_utc(i['postTime']) < end]

    def status(self, submission_id: str) -> dict | None:
        status, raw = self.send('GET', f'{API}/posts/{urllib.parse.quote(submission_id)}', {}, None)
        if status == 404:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if status != 200 or not isinstance(value, dict) or not isinstance(value.get('status'), str):
            raise Stop('queue_unreadable', f'readback answered HTTP {status}')
        return value

    def upload(self, path: Path) -> tuple[str, str]:
        """Presigned upload, then download it again and match SHA-256."""
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        status, raw = self.send('POST', API + '/media/uploads', {'Content-Type': 'application/json'},
                                json.dumps({'filename': f'{digest[:16]}{path.suffix}'}).encode())
        answer = json.loads(raw) if status == 200 else {}
        put_url = answer.get('presignedUrl') or answer.get('uploadUrl')
        public = answer.get('publicUrl') or answer.get('url')
        if not (put_url and public):
            raise Stop('submit_failed', f'media upload answered HTTP {status}')
        status, _ = self.send('PUT', put_url, {'Content-Type': MEDIA_TYPES[path.suffix]}, data)
        got_status, got = self.send('GET', public, {}, None)
        if status != 200 or got_status != 200 or hashlib.sha256(got).hexdigest() != digest:
            raise Stop('submit_failed', f'media {path.name} did not read back byte-identical')
        return public, digest

    def create(self, body: dict) -> str:
        if not body.get('scheduledTime'):
            raise Stop('submit_failed', 'refusing a post without scheduledTime')
        status, raw = self.send('POST', API + '/posts', {'Content-Type': 'application/json'}, json.dumps(body).encode())
        try:
            submission = json.loads(raw).get('postSubmissionId')
        except (ValueError, AttributeError):
            submission = None
        if status not in (200, 201) or not isinstance(submission, str) or not submission:
            raise Stop('submit_failed', f'create answered HTTP {status} without a submission id')
        return submission


# ---------------------------------------------------------------- logs
def ledger_rows(out: Path) -> list[dict]:
    rows = []
    for path in sorted(out.glob('*/ledger.jsonl')):
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append({**json.loads(line), 'week': path.parent.name})
    return rows


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        handle.write(json.dumps(row, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def own_keys(ctx: Context, drafts: dict) -> frozenset[tuple[str, str]]:
    """This batch's own earlier writes, so a retry plans against the same queue it drafted on."""
    ids = {d.get('id') for d in drafts.get('drafts', []) if isinstance(d, dict)}
    return frozenset((r['platform'], r['utc'][:16]) for r in ledger_rows(ctx.out)
                     if r.get('week') == drafts.get('week_start') and r.get('event') in ('intent', 'posted')
                     and r.get('draft') in ids)


# ---------------------------------------------------------------- plan
def spec_check(ctx: Context) -> None:
    documents: dict[str, dict] = {}
    for pin in ctx.cadence['pins']:
        if pin['document'] not in documents:
            try:
                documents[pin['document']] = ctx.doctrine(pin['document'])
            except Exception as exc:  # the store is unreachable or the verb failed
                raise Stop('spec_unreadable', f"read-doctrine {pin['document']}: {type(exc).__name__}") from None
        found = {s.get('section_id'): s for s in documents[pin['document']].get('sections', [])}
        if found.get(pin['section_id'], {}).get('content_hash') != pin['content_hash']:
            raise Stop('spec_changed', f"{pin['document']}#{pin['section_key']} changed; read it, then re-pin "
                                       'ops/social/cadence.v1.json')


def plan(ctx: Context, own: frozenset[tuple[str, str]] = frozenset()) -> dict:
    spec_check(ctx)
    cadence, accounts = ctx.cadence, ctx.accounts
    week = target_week(ctx.now)
    start, end = bounds(week)
    api = Blotato(ctx.transport)
    logged = [r for r in ledger_rows(ctx.out) if r.get('event') == 'posted']
    by_minute = {(r['platform'], r['utc'][:16]): r for r in logged}
    held: dict[tuple[str, str], dict] = {}
    for value in api.listing(start, end):
        platform = value['platform']
        key = (platform, minute(value['postTime']))
        if (value['state']['type'] == 'failed' or key in own or platform not in accounts
                or str(value['account']['id']) != str(accounts[platform]['accountId'])):
            continue
        held[key] = {'platform': platform, 'utc': key[1], 'source': 'listing', 'lane': _lane(platform, by_minute.get(key))}
    # The default listing reaches about 7 days back; every post this script logged for
    # the week is also read back by submission id, so no listing horizon can hide it.
    # Keyed by Blotato's current time, so a post Joe moved is not counted twice.
    for row in logged:
        if (row['platform'], row['utc'][:16]) in own or not start <= parse_utc(row['utc']) < end:
            continue
        status = api.status(row['submission_id']) if row.get('submission_id') else {'status': 'scheduled'}
        if not status or status['status'] == 'failed':
            continue
        when = status.get('scheduledTime') or row['utc']
        key = (row['platform'], minute(when))
        if key not in held and key not in own and start <= parse_utc(when) < end:
            held[key] = {'platform': row['platform'], 'utc': key[1], 'source': 'log', 'lane': _lane(row['platform'], row)}
    warnings = []
    if not api.listing(*bounds(week - timedelta(days=7))):
        warnings.append('control_week_empty: the previous week lists no posts; planning from the spec cadence')
    unknown_x = sum(1 for h in held.values() if h['lane'] == 'unknown')
    lanes = {}
    for lane, rule in cadence['lanes'].items():
        count = sum(1 for h in held.values() if h['lane'] == lane)
        blind = unknown_x if rule['platform'] == 'twitter' else 0
        lanes[lane] = {'min': rule['min'], 'max': rule['max'], 'held': count,
                       'open': max(0, rule['max'] - count - blind)}
    x_max = sum(r['max'] for r in cadence['lanes'].values() if r['platform'] == 'twitter')
    x_held = sum(1 for h in held.values() if h['platform'] == 'twitter')
    x_total = {'max': x_max, 'held': x_held, 'unknown_lane': unknown_x, 'open': max(0, x_max - x_held)}
    covered = (x_total['open'] == 0 or all(lanes[l]['open'] == 0 for l in ('standalone', 'one_liner'))) and all(
        lanes[p]['open'] == 0 for p in CARR_PLATFORMS)
    earlier = sorted((r for r in logged if parse_utc(r['utc']) < start), key=lambda r: r['utc'], reverse=True)
    previous_start = bounds(week - timedelta(days=7))[0]
    result = {
        'ok': True, 'status': 'week_covered' if covered else 'ready', 'timezone': 'America/Chicago',
        'week_start': week.isoformat(), 'window_utc': [stamp(start), stamp(end)],
        'days': [{'date': day.isoformat(), 'utc_offset': datetime.combine(day, time(12), ZONE).isoformat()[-6:]}
                 for day in (week + timedelta(days=i) for i in range(7))],
        'lanes': lanes, 'x_total': x_total,
        'held_posts': sorted(held.values(), key=lambda h: h['utc']),
        'recent_x_visual_formats': [r['format'] for r in earlier if r['platform'] == 'twitter' and r.get('visual')]
        [:cadence['x_visual']['no_repeat_last']],
        'animated_last_week': any(r.get('format') == 'animated_card' and parse_utc(r['utc']) >= previous_start
                                  for r in earlier),
        'warnings': warnings,
    }
    result['plan_digest'] = hashlib.sha256(json.dumps(
        {k: result[k] for k in ('week_start', 'lanes', 'x_total', 'held_posts', 'recent_x_visual_formats',
                                'animated_last_week')} | {'pins': cadence['pins']}, sort_keys=True).encode()).hexdigest()
    if covered:
        _artifact(ctx, {'summary': f'SOCIAL_BATCH_WEEK_COVERED week={week}', 'week_start': week.isoformat()})
    return result


def _lane(platform: str, logged: dict | None) -> str:
    if platform != 'twitter':
        return platform
    return logged['lane'] if logged and logged.get('lane') in ('standalone', 'one_liner') else 'unknown'


def _artifact(ctx: Context, value: dict) -> None:
    if ctx.receipt:
        path = Path(ctx.receipt + '.artifact')
        path.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')


# ---------------------------------------------------------------- check
def _lint():
    spec = importlib.util.spec_from_file_location('writing_lint', REPO / 'tools/writing-lint.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.lint


def media_kind(path: Path) -> tuple[str, int | None, int | None] | None:
    head = path.read_bytes()[:32]
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return ('png', *struct.unpack('>II', head[16:24]))
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return ('gif', *struct.unpack('<HH', head[6:10]))
    if path.suffix == '.mp4' and head[4:8] == b'ftyp':
        return 'mp4', None, None
    return None


def check(drafts: dict, current: dict, ctx: Context) -> list[dict]:
    """Every fixed rule. An empty list is the only passing answer."""
    cadence, accounts = ctx.cadence, ctx.accounts
    findings: list[dict] = []

    def find(draft, rule, detail):
        findings.append({'draft': draft, 'rule': rule, 'detail': detail})

    if not isinstance(drafts, dict) or set(drafts) != TOP_KEYS or drafts.get('schema') != SCHEMA:
        find(None, 'schema', f'top level must be exactly {sorted(TOP_KEYS)} with schema {SCHEMA}')
        return findings
    if drafts['week_start'] != current['week_start']:
        find(None, 'week_mismatch', f"drafts target {drafts['week_start']}, plan targets {current['week_start']}")
    if drafts['plan_digest'] != current['plan_digest']:
        find(None, 'plan_stale', 'the queue or the spec changed since this batch was drafted; run plan again')
    notes = drafts['run_notes']
    skipped = {}
    if not isinstance(notes, dict) or set(notes) - NOTE_KEYS:
        find(None, 'schema', f'run_notes keys must be within {sorted(NOTE_KEYS)}')
    else:
        grok = notes.get('grok') or {}
        if grok.get('status') not in ('done', 'skipped') or (grok.get('status') == 'skipped' and not grok.get('reason')):
            find(None, 'grok_status', 'run_notes.grok.status must be done, or skipped with a reason')
        if (notes.get('claims_review') or {}).get('done') is not True:
            find(None, 'claims_review_missing', 'run the independent claims reviewer before check')
        for slot in notes.get('skipped_slots') or []:
            if isinstance(slot, dict) and slot.get('lane') in WAIVABLE and str(slot.get('reason', '')).strip():
                skipped[slot['lane']] = slot['reason']
            else:
                find(None, 'schema', f'skipped_slots entries need a lane in {sorted(WAIVABLE)} and a reason')
    items = drafts['drafts'] if isinstance(drafts['drafts'], list) else []
    start, end = bounds(date.fromisoformat(current['week_start']))
    lint = _lint()
    seen, valid = set(), []
    for draft in items:
        draft_id = draft.get('id') if isinstance(draft, dict) else None
        if not isinstance(draft, dict) or set(draft) - DRAFT_KEYS or DRAFT_REQUIRED - set(draft):
            find(draft_id, 'schema', f'draft keys must include {sorted(DRAFT_REQUIRED)} and stay within {sorted(DRAFT_KEYS)}')
            continue
        if draft_id in seen:
            find(draft_id, 'duplicate_id', 'draft ids must be unique')
        seen.add(draft_id)
        platform, lane, text = draft['platform'], draft['lane'], draft['text']
        if platform not in accounts:
            find(draft_id, 'platform_unknown', f'{platform} is not in blotato-accounts.json')
            continue
        if (lane in ('standalone', 'one_liner')) != (platform == 'twitter') or lane not in ('standalone', 'one_liner', 'carr'):
            find(draft_id, 'lane_platform', 'standalone and one_liner are X lanes; carr is LinkedIn, Facebook or Instagram')
            continue
        if draft['format'] not in FORMATS or draft.get('kind') not in KINDS or not isinstance(text, str) or not text.strip():
            find(draft_id, 'schema', f'format must be one of {sorted(FORMATS)}; kind one of take, series, story_time')
            continue
        try:
            when = _local(draft['local_time'])
        except (TypeError, ValueError) as exc:
            find(draft_id, 'time_format', str(exc))
            continue
        if not start <= when < end:
            find(draft_id, 'time_outside_week', f"{draft['local_time']} is outside the week of {current['week_start']}")
        if when < ctx.now + timedelta(hours=cadence['min_lead_hours']):
            find(draft_id, 'time_too_soon', f"must be at least {cadence['min_lead_hours']} hours after the run")
        if when.minute % 5 == 0:
            find(draft_id, 'time_round_minute', 'pick a minute not divisible by 5')
        hours = cadence['standalone_hours']
        if lane == 'standalone' and not hours[0] <= when.hour < hours[1]:
            find(draft_id, 'standalone_hours', f'standalone posts run {hours[0]}:00 to {hours[1]}:00 Central')
        if draft.get('kind') == 'quote_tweet' or STATUS_URL.search(text):
            find(draft_id, 'quote_tweet', 'quote-tweets belong to x-reply-run-daily')
        media = draft.get('media') or []
        if lane == 'one_liner' and (HASHTAG.search(text) or 'http' in text or media):
            find(draft_id, 'one_liner_shape', 'one-liners are text only, with no hashtags, links or media')
        if draft.get('kind') == 'story_time' and not STORY_FLAG.search(text):
            find(draft_id, 'story_time_unflagged', 'a story-time post must say it is made up')
        if platform == 'instagram':
            if len(HASHTAG.findall(text)) > cadence['instagram_hashtags_max']:
                find(draft_id, 'ig_hashtags', f"at most {cadence['instagram_hashtags_max']} hashtags on Instagram")
            if media and not str(draft.get('alt_text', '')).strip():
                find(draft_id, 'alt_text_missing', 'Instagram media needs alt_text')
        if draft['format'] == 'animated_card' and platform in cadence['animated_card']['never_on']:
            find(draft_id, 'animated_on_x', 'animated cards never go on X')
        if draft['format'] == 'carousel' and len(media) < 2:
            find(draft_id, 'carousel_media', 'a carousel needs at least two slides')
        if draft.get('visual') and not media:
            find(draft_id, 'visual_media', 'a visual post needs media')
        texts = [text]
        for entry in media:
            if not isinstance(entry, dict) or set(entry) != {'path', 'card_text'}:
                find(draft_id, 'schema', 'media entries are {path, card_text}')
                continue
            texts.append(entry['card_text'])
            path = Path(entry['path'])
            kind = media_kind(path) if path.is_absolute() and path.is_file() else None
            if path.suffix not in MEDIA_TYPES or kind is None:
                find(draft_id, 'media_type', f'{path.name}: need an existing absolute .png, .gif or .mp4')
            elif platform == 'twitter' and kind[0] == 'mp4':
                find(draft_id, 'media_type', 'no video on X')
            elif platform == 'instagram' and kind[1] and kind[2] and not 0.8 <= kind[1] / kind[2] <= 1.91:
                find(draft_id, 'media_dims', f'{path.name}: Instagram images run 4:5 to 1.91:1')
            if platform == 'twitter' and 'CARR' in entry['card_text'].upper().split():
                find(draft_id, 'x_carr_branding', 'X visuals carry no CARR branding')
        for source in draft.get('sources') or []:
            if not isinstance(source, dict) or source.get('route') not in ROUTES:
                find(draft_id, 'schema', f'sources need a route in {sorted(ROUTES)}')
            elif source['route'] == 'grok_only':
                find(draft_id, 'source_grok_only', 'Grok spots trends; verify the figure at its primary source')
        cleared = {c.get('rule') for c in draft.get('lint_review_clearances') or []
                   if isinstance(c, dict) and str(c.get('reason', '')).strip()}
        for body in texts:
            for severity, rule, _, quote, *_ in lint(body, 'social'):
                if severity == 'HARD':
                    find(draft_id, 'lint_hard', f'{rule}: {quote}')
                elif severity == 'REVIEW' and rule not in cleared:
                    find(draft_id, 'lint_review_uncleared', f'{rule}: {quote}')
        valid.append((draft, when))
    _batch_rules(valid, current, cadence, skipped, find)
    return findings


def _batch_rules(valid, current, cadence, skipped, find) -> None:
    x = [(d, w) for d, w in valid if d['platform'] == 'twitter']
    counts = {lane: sum(1 for d, _ in valid if (d['lane'] if d['platform'] == 'twitter' else d['platform']) == lane)
              for lane in cadence['lanes']}
    for lane, rule in current['lanes'].items():
        if counts[lane] > rule['open']:
            find(None, 'lane_over', f"{lane}: {counts[lane]} drafts for {rule['open']} open slots")
        blind = current['x_total']['unknown_lane'] if cadence['lanes'][lane]['platform'] == 'twitter' else 0
        if counts[lane] + rule['held'] + blind < rule['min'] and lane not in skipped:
            find(None, 'lane_under', f"{lane}: below the minimum of {rule['min']}; add a skipped_slots reason")
    if len(x) > current['x_total']['open']:
        find(None, 'x_total_over', f"{len(x)} X drafts for {current['x_total']['open']} open X slots")
    for name, kind in (('x_series', 'series'), ('story_time', 'story_time')):
        n = sum(1 for d, _ in x if d.get('kind') == kind)
        if n > cadence[name]['max']:
            find(None, f'{name}_count', f"at most {cadence[name]['max']} {kind} posts")
        elif n < cadence[name]['min'] and name not in skipped:
            find(None, f'{name}_count', f"at least {cadence[name]['min']} {kind} post; or add a skipped_slots reason")
    visuals = [d for d, _ in x if d.get('visual')]
    if len(visuals) > cadence['x_visual']['max']:
        find(None, 'x_visual_count', 'one X visual a week')
    elif not visuals and 'x_visual' not in skipped:
        find(None, 'x_visual_count', 'one X visual a week; or add a skipped_slots reason')
    for d in visuals:
        if d['format'] in current['recent_x_visual_formats']:
            find(d['id'], 'x_visual_repeat', f"{d['format']} ran in the last {cadence['x_visual']['no_repeat_last']} X visuals")
    held_days = {parse_utc(h['utc']).astimezone(ZONE).date() for h in current['held_posts'] if h['platform'] == 'twitter'}
    days = held_days | {w.date() for _, w in x}
    week = date.fromisoformat(current['week_start'])
    missing = [week + timedelta(days=i) for i in range(7) if week + timedelta(days=i) not in days]
    if missing and 'x_every_day' not in skipped:
        find(None, 'x_every_day', 'no X post on ' + ', '.join(d.strftime('%a %m/%d') for d in missing))
    clock: dict[str, str] = {}
    for d, w in x:
        if w.strftime('%H:%M') in clock:
            find(d['id'], 'x_time_repeat', f"{w:%H:%M} also used by {clock[w.strftime('%H:%M')]}")
        clock.setdefault(w.strftime('%H:%M'), d['id'])
    carr = [d for d, _ in valid if d['platform'] in CARR_PLATFORMS]
    formats = {d['format'] for d in carr}
    if len(formats) < min(cadence['carr_formats']['min_distinct'], len(carr)):
        find(None, 'carr_formats', f"at least {cadence['carr_formats']['min_distinct']} distinct CARR formats")
    if len(carr) >= cadence['carr_formats']['min_distinct'] and 'carousel' not in formats:
        find(None, 'carr_carousel', 'a carousel on at least one CARR platform')
    for platform in CARR_PLATFORMS:
        mine = [d['format'] for d in carr if d['platform'] == platform]
        if len(mine) > 1 and set(mine) <= SINGLE_CARDS:
            find(None, 'carr_all_single', f'{platform} runs only single cards')
    animated = [d for d, _ in valid if d['format'] == 'animated_card']
    if len(animated) > cadence['animated_card']['max_per_batch']:
        find(None, 'animated_max', 'at most one animated card a batch')
    if animated and current['animated_last_week']:
        find(animated[0]['id'], 'animated_consecutive', 'an animated card ran last week')


# ---------------------------------------------------------------- submit
def submit(drafts: dict, ctx: Context) -> dict:
    own = own_keys(ctx, drafts)
    current = plan(ctx, own)
    if current['status'] == 'week_covered':
        return {'ok': False, 'status': 'week_covered', 'findings': [
            {'draft': None, 'rule': 'week_covered', 'detail': 'the week already holds every lane maximum'}]}
    findings = check(drafts, current, ctx)
    if findings:
        return {'ok': False, 'status': 'refused', 'findings': findings}
    accounts, api = ctx.accounts, Blotato(ctx.transport)
    folder = ctx.out / current['week_start']
    ledger = folder / 'ledger.jsonl'
    rows = [r for r in ledger_rows(ctx.out) if r['week'] == current['week_start']]
    media_urls = {r['sha256']: r['url'] for r in rows if r['event'] == 'media'}
    posted = {r['draft']: r for r in rows if r['event'] == 'posted'}
    intents = {r['draft'] for r in rows if r['event'] == 'intent'}
    window = bounds(date.fromisoformat(current['week_start']))
    for draft in drafts['drafts']:
        if draft['id'] in posted:
            continue
        utc = local_to_utc(draft['local_time'])
        base = {'draft': draft['id'], 'platform': draft['platform'], 'lane': draft['lane'], 'kind': draft.get('kind'),
                'format': draft['format'], 'visual': bool(draft.get('visual')), 'utc': utc}
        if draft['id'] in intents:
            # A crash between POST and the posted row: adopt the post if the queue holds it.
            match = [i for i in api.listing(*window) if i['platform'] == draft['platform'] and minute(i['postTime']) == utc[:16]]
            if match:
                posted[draft['id']] = row = {**base, 'event': 'posted', 'submission_id': None,
                                             'listing_id': match[0]['id'], 'adopted': True}
                append(ledger, row)
                continue
        urls = []
        for entry in draft.get('media') or []:
            path = Path(entry['path'])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest not in media_urls:
                media_urls[digest], _ = api.upload(path)
                append(ledger, {'event': 'media', 'sha256': digest, 'url': media_urls[digest], 'name': path.name})
            urls.append(media_urls[digest])
        if parse_utc(utc) < ctx.now + timedelta(hours=ctx.cadence['min_lead_hours']):
            raise Stop('submit_failed', f"{draft['id']} is no longer a future review draft")
        target = {'targetType': draft['platform']}
        if draft['platform'] == 'facebook':
            target['pageId'] = accounts['facebook']['pageId']
        if draft['platform'] == 'instagram' and draft.get('alt_text'):
            target['altText'] = draft['alt_text']
        body = {'post': {'accountId': accounts[draft['platform']]['accountId'],
                         'content': {'platform': draft['platform'], 'text': draft['text'], 'mediaUrls': urls},
                         'target': target},
                'scheduledTime': utc}
        append(ledger, {**base, 'event': 'intent'})
        submission = api.create(body)
        posted[draft['id']] = row = {**base, 'event': 'posted', 'submission_id': submission}
        append(ledger, row)
    return _finish(drafts, current, ctx, api, ledger, posted, media_urls, window)


def _finish(drafts, current, ctx, api, ledger, posted, media_urls, window) -> dict:
    listing = None
    posts = []
    for draft in drafts['drafts']:
        row = posted[draft['id']]
        if row.get('submission_id'):
            status = api.status(row['submission_id']) or {}
            ok = status.get('status') == 'scheduled' and minute(status.get('scheduledTime', '1970-01-01T00:00:00Z')) == row['utc'][:16]
        else:
            listing = listing if listing is not None else api.listing(*window)
            ok = any(i['platform'] == row['platform'] and minute(i['postTime']) == row['utc'][:16]
                     and i['state']['type'] == 'scheduled' for i in listing)
        digests = [hashlib.sha256(Path(m['path']).read_bytes()).hexdigest() for m in draft.get('media') or []]
        posts.append({'draft': draft['id'], 'platform': row['platform'], 'lane': row['lane'], 'kind': row.get('kind'),
                      'format': row['format'], 'utc': row['utc'], 'local_time': draft['local_time'],
                      'submission_id': row.get('submission_id'), 'readback': 'scheduled' if ok else 'unverified',
                      'media_sha256': digests, 'media_verified': all(d in media_urls for d in digests),
                      'text': draft['text']})
    verified = sum(1 for p in posts if p['readback'] == 'scheduled' and p['media_verified'])
    summary = f'SOCIAL_BATCH_SCHEDULED n={len(posts)} verified={verified}'
    folder = ledger.parent
    (folder / 'batch.json').write_text(json.dumps({
        'schema': 'carr-social-batch-log/v1', 'week_start': current['week_start'], 'summary': summary,
        'posts': posts, 'run_notes': drafts['run_notes'], 'plan_warnings': current['warnings']},
        indent=1, sort_keys=True) + '\n')
    if verified != len(posts):
        return {'ok': False, 'status': 'unverified', 'summary': summary,
                'findings': [{'draft': p['draft'], 'rule': 'readback', 'detail': 'not read back as scheduled'}
                             for p in posts if p['readback'] != 'scheduled' or not p['media_verified']]}
    done = {r.get('event') for r in ledger_rows(ctx.out) if r['week'] == current['week_start']}
    week = current['week_start']
    if 'section' not in done:
        bank = ctx.doctrine(BANK)
        key = f'4-headless-batch-{week}'
        if key not in {s.get('section_key') for s in bank.get('sections', [])}:
            ctx.verb('write-doctrine-section', {
                'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, f'carr-social-batch:{week}:section')),
                'document': BANK, 'section_key': key, 'base_version': 0,
                'ordinal': max([s.get('ordinal') or 0 for s in bank.get('sections', [])] or [0]) + 10,
                'title': f'4. Feedback log, headless batch for the week of {week}',
                'body_text': _section(posts, drafts['run_notes'], current),
                'commit_message': f'social-batch-headless {week}: {summary}'})
        append(ledger, {'event': 'section', 'key': key})
    if 'loop' not in done:
        ctx.verb('add-loop', {
            'idempotency_key': str(uuid.uuid5(uuid.NAMESPACE_URL, f'carr-social-batch:{week}:loop')),
            'kind': 'open_loop', 'owner': 'Joe', 'domain': 'marketing', 'marker': 'dated', 'due_on': week,
            'blocker': 'human_only', 'residue_kind': 'external_send', 'residue_host': 'Blotato',
            'blocker_detail': f'Joe reviews the {len(posts)} scheduled review drafts in Blotato before the first one fires',
            'body': f'Review before it fires: {len(posts)} social review drafts for the week of {week} are scheduled '
                    'in Blotato (social-batch-headless). Times are Central. ' + '; '.join(
                        f"{_local(p['local_time']):%a %m/%d %H:%M %Z} {p['platform']} {p['draft']}"
                        f" ({(p['submission_id'] or 'adopted')[:8]})" for p in sorted(posts, key=lambda p: p['utc']))
                    + '. Cancel any post in Blotato before its time to stop it.',
            'source_note': f'out/social-batch/{week}/batch.json'})
        append(ledger, {'event': 'loop'})
    _artifact(ctx, {'summary': summary, 'week_start': week, 'all_verified': True, 'batch': str(folder / 'batch.json')})
    return {'ok': True, 'status': 'scheduled', 'summary': summary, 'week_start': week}


def _section(posts, notes, current) -> str:
    lines = [f"Headless batch for the week of {current['week_start']}. Every post below was read back by "
             'submission id as scheduled at its intended minute; media matched byte for byte.', '']
    for p in sorted(posts, key=lambda p: p['utc']):
        lines.append(f"- {_local(p['local_time']):%a %m/%d %H:%M %Z} · {p['platform']} · {p['lane']}"
                     f"{' · ' + p['kind'] if p['kind'] else ''} · {p['format']} · {p['draft']}")
    grok = notes.get('grok') or {}
    lines += ['', f"Grok pass: {grok.get('status')}{' (' + grok['reason'] + ')' if grok.get('reason') else ''}."]
    for label, key in (('Grok findings', None), ('Improved', 'improved'), ('Not improved', 'not_improved'),
                       ('Bank corrections', 'bank_corrections')):
        values = grok.get('findings') if key is None else notes.get(key)
        if values:
            lines += [f'{label}:'] + [f'- {v}' for v in values]
    for slot in notes.get('skipped_slots') or []:
        lines.append(f"Skipped {slot['lane']}: {slot['reason']}")
    for warning in current['warnings']:
        lines.append(f'Plan warning: {warning}')
    return '\n'.join(lines)


# ---------------------------------------------------------------- render
def _work_folder(path: Path, out: Path) -> Path:
    """The out/social-batch/<week>/work folder holding path, or Stop."""
    try:
        place = path.resolve().relative_to(out.resolve()).parts
    except ValueError:
        place = ()
    if len(place) < 3 or place[1] != 'work':
        raise Stop('render_failed', 'cards are read from and written to out/social-batch/<week>/work/ only')
    return out.resolve() / place[0] / 'work'


@contextlib.contextmanager
def card_server(work: Path, fonts: Path = FONTS):
    """A throwaway 127.0.0.1 server that is also Chrome's only proxy.

    It answers GET for exactly http://card.test/: files under the work folder, and
    the tracked brand fonts under /fonts/. Every other host, path or method gets an
    error, so a card can reach no file:// URL (an http page may not), no other local
    file, no other local service and no outside host.
    """
    roots = (work.resolve(), fonts.resolve())

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(url.path)
            root, name = (roots[1], path[len('/fonts/'):]) if path.startswith('/fonts/') else (roots[0], path.lstrip('/'))
            target = (root / name).resolve()
            if (url.scheme, url.netloc) != ('http', CARD_HOST) or not target.is_relative_to(root) \
                    or not target.is_file() or (root == roots[1] and target.suffix not in FONT_TYPES):
                self.send_error(403)
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', FONT_TYPES.get(target.suffix) or
                             mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _launcher(mode: str, chrome: str | None, port: int, width: int, height: int, output: str, page: str):
    """tools/card-chrome.mjs owns the Chrome flags and launch (the shared-launcher policy)."""
    binary = chrome or CHROME
    return subprocess.run(['node', str(CARD_LAUNCHER), mode, binary, str(port), str(width), str(height), output, page],
                          check=True, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


def chrome_args(chrome: str, port: int, width: int, height: int, output: str, page: str = '') -> list[str]:
    """The exact Chrome argv the launcher would use, for pinning in tests."""
    return json.loads(_launcher('print', chrome, port, width, height, output, page).stdout)


def browse(html: Path, output: str, width: int, height: int, chrome: str | None = None, out: Path = OUT):
    """Load one work-folder card in sealed headless Chrome; output is --screenshot=... or --dump-dom."""
    work = _work_folder(html, out)
    with card_server(work) as port:
        return _launcher('run', chrome, port, width, height, output, str(html.resolve().relative_to(work)))


def render(html: Path, png: Path, width: int, height: int, chrome: str | None = None, out: Path = OUT) -> dict:
    """Screenshot a card. The HTML and the PNG both live in out/social-batch/<week>/work/,
    the one folder the unattended model may write, so render can neither overwrite the
    guard nor show the model a file outside that folder."""
    _work_folder(html, out)
    _work_folder(png, out)
    if png.suffix != '.png':
        raise Stop('render_failed', 'cards render to .png')
    png.parent.mkdir(parents=True, exist_ok=True)  # Chrome will not create it
    browse(html, f'--screenshot={png.resolve()}', width, height, chrome, out)
    kind = media_kind(png) if png.is_file() else None
    if not kind or kind[1:] != (width, height):
        raise Stop('render_failed', f'{png.name} is not a {width}x{height} PNG')
    return {'ok': True, 'path': str(png), 'width': width, 'height': height,
            'sha256': hashlib.sha256(png.read_bytes()).hexdigest()}


# ---------------------------------------------------------------- CLI
def _verb_runner(verb: str, args: dict) -> dict:
    result = subprocess.run([str(REPO / 'run.sh'), 'call', verb, json.dumps(args)], cwd=REPO,
                            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    text = result.stdout
    value = json.loads(text[text.find('{'):]) if '{' in text else {}
    if result.returncode or value.get('ok') is False or not value:
        raise RuntimeError(f'{verb} failed (exit {result.returncode})')
    return value


def real_context() -> Context:
    return Context(now=datetime.now(UTC), transport=http_transport(),
                   doctrine=lambda document: _verb_runner('read-doctrine', {'document': document}),
                   verb=_verb_runner, receipt=os.environ.get('CARR_HEADLESS_RECEIPT'))


def main(argv=None, ctx: Context | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('plan')
    for name in ('check', 'submit'):
        sub.add_parser(name).add_argument('drafts', type=Path)
    drawing = sub.add_parser('render')
    drawing.add_argument('html', type=Path)
    drawing.add_argument('png', type=Path)
    drawing.add_argument('--size', required=True, help='WIDTHxHEIGHT, e.g. 1080x1350')
    args = parser.parse_args(argv)
    try:
        if args.command == 'render':
            width, height = (int(n) for n in args.size.lower().split('x'))
            return _print(render(args.html, args.png, width, height))
        ctx = ctx or real_context()
        if args.command == 'plan':
            return _print(plan(ctx))
        drafts = json.loads(args.drafts.read_text())
        if args.command == 'check':
            findings = check(drafts, plan(ctx, own_keys(ctx, drafts)), ctx)
            return _print({'ok': not findings, 'findings': findings}, 0 if not findings else 1)
        result = submit(drafts, ctx)
        return _print(result, 0 if result['ok'] else 1)
    except Stop as stop:
        return _print({'ok': False, 'status': stop.code, 'detail': stop.detail}, 2)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return _print({'ok': False, 'status': 'error', 'detail': type(exc).__name__}, 2)


def _print(value: dict, code: int = 0) -> int:
    print(json.dumps(value, indent=1, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
