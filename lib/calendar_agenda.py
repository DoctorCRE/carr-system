"""Pure Outlook calendar display projection; no transport or record writes.

Intake owns decoding and publication. Cancellation applies across stored copies
of the same GOID because revision ordering is not established by the decoder.
"""
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import re

from lib.outlook_settings import OutlookSettings

AGENDA_SCHEMA = 'carr-calendar-agenda.v1'
AGENDA_DAYS = 14
AGENDA_ATTENDEES = 50
AGENDA_MAX_BYTES = 200_000
CANCELED = re.compile(r'\s*cancell?ed\s*:', re.I)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _iso(unix: int) -> str:
    return datetime.fromtimestamp(unix, timezone.utc).isoformat()


def _instance(goid: str) -> str:
    """The instance date Outlook writes into an occurrence's Global Object ID (zero for a single item or a series)."""
    return goid[32:40] if len(goid) >= 40 else '00000000'


def _series(goid: str) -> str:
    return goid[:32] + '00000000' + goid[40:] if len(goid) >= 40 else goid


def agenda(events: list[dict], meta: dict, settings: OutlookSettings, now: datetime) -> tuple[dict, dict]:
    """The display snapshot of upcoming meetings and recent outcome review proposals.

    Joe is the configured account. A meeting shows only when every stored copy
    names him as organizer or attendee, so TEST-account, personal and other
    partners' items never show; an invite sent only to a group he belongs to is
    left out and the snapshot says so. Copies are collapsed by Global Object ID.
    Outlook writes each recurring occurrence under its own ID (the instance date
    sits in bytes 16-19), so the series master is skipped once occurrences exist.
    Revision order is not decoded: an ID seen at more than one start is one row
    flagged time_changed with every observed time. Attendee responses and folder
    membership are not decoded either, so every snapshot carries that warning;
    the brief turns warnings into plain sentences and never reports them ready.
    No notes or bodies leave the private copy.
    """
    tz = settings.timezone
    day = datetime.combine(now.astimezone(tz).date(), time(), tz)
    lo, hi = int(day.timestamp()), int((day + timedelta(days=AGENDA_DAYS)).timestamp())
    review_hi = int(now.timestamp())
    review_lo = review_hi - 86_400
    account = settings.account
    with_occurrences = {_series(e['goid']) for e in events if _instance(e['goid']) != '00000000'}
    by_goid: dict[str, list[dict]] = {}
    for e in events:
        by_goid.setdefault(e['goid'], []).append(e)
    counts = dict.fromkeys(('calendar_agenda_items', 'calendar_agenda_canceled', 'calendar_agenda_conflicts',
                            'calendar_agenda_other_accounts', 'calendar_agenda_time_changed',
                            'calendar_agenda_recurring', 'calendar_agenda_trimmed',
                            'calendar_follow_up_trimmed'), 0)
    items, reviews = [], []
    for goid, group in by_goid.items():
        if _instance(goid) == '00000000' and goid in with_occurrences:
            continue
        current = [e for e in group if e['end_unix'] > lo and e['start_unix'] < hi]
        recent = [e for e in group if review_lo < e['end_unix'] <= review_hi]
        if not current and not recent:
            continue
        if not all(account in {(e['organizer_email'] or '').lower(), *((m or '').lower() for _, m in e['attendees'])}
                   for e in group):
            counts['calendar_agenda_other_accounts'] += 1
            continue
        if any(e.get('lifecycle_status') == 'canceled' or CANCELED.match(e['subject'] or '') for e in group):
            counts['calendar_agenda_canceled'] += 1
            continue
        shown = max(current or recent, key=lambda e: (len(e['attendees']), len(e['subject'] or ''), e['end_unix']))
        starts = sorted({e['start_unix'] for e in group})
        conflict = len({json.dumps([e['subject'], e['location'], e['end_unix'] - e['start_unix'],
                                    sorted((n or '', m or '') for n, m in e['attendees'])]) for e in group}) > 1
        recurring = _instance(goid) != '00000000'
        start = min(e['start_unix'] for e in (current or recent))
        local = datetime.fromtimestamp(start, tz)
        length = shown['end_unix'] - shown['start_unix']
        local_end = datetime.fromtimestamp(start + length, tz)
        counts['calendar_agenda_conflicts'] += conflict
        counts['calendar_agenda_time_changed'] += len(starts) > 1
        counts['calendar_agenda_recurring'] += recurring
        attendees = [{'name': name or None, 'email': (email or '').lower() or None} for name, email in shown['attendees']]
        item = {'key': _hash(goid)[:16], 'start': _iso(start), 'end': _iso(start + length),
                      'all_day': local.time() == local_end.time() == time() and local_end.date() > local.date(),
                      'title': shown['subject'] or 'Meeting', 'location': shown['location'] or None,
                      'organizer': {'name': shown['organizer_name'] or None,
                                    'email': (shown['organizer_email'] or '').lower() or None},
                      'attendees': attendees[:AGENDA_ATTENDEES], 'attendee_count': len(attendees),
                      'recurring': recurring, 'time_changed': len(starts) > 1,
                      'times': [_iso(t) for t in starts] if len(starts) > 1 else [],
                      'candidates': len(group), 'conflict': conflict}
        if current and start + length > review_hi:
            items.append(item)
        if (review_lo < start + length <= review_hi and length > 0
                and not item['all_day'] and not conflict and len(starts) == 1):
            reviews.append({k: item[k] for k in ('key', 'title', 'start', 'end')} | {
                'state': 'needs_review',
                'prompt': 'Confirm whether this meeting happened. If it did, record the outcome and decide the next step.'})
    reviews.sort(key=lambda i: (i['end'], i['key']), reverse=True)
    items.sort(key=lambda i: (i['start'], i['title'], i['key']))
    counts['calendar_follow_up_trimmed'] = max(0, len(reviews) - 100)
    del reviews[100:]
    undecoded = max(0, (meta.get('event_identities') or 0) - len({(e['goid'], e['start_unix']) for e in events}))
    warnings: list[dict] = [{'code': 'responses_unverified'}, {'code': 'group_invites_not_shown'}]
    if undecoded:
        warnings.append({'code': 'undecoded', 'count': undecoded})
    if counts['calendar_agenda_canceled']:
        warnings.append({'code': 'canceled_hidden', 'count': counts['calendar_agenda_canceled']})
    if counts['calendar_agenda_time_changed']:
        warnings.append({'code': 'time_changed', 'count': counts['calendar_agenda_time_changed']})
    if with_occurrences:
        # Any stored series may have occurrences Outlook has not written yet, in this window or not.
        warnings.append({'code': 'recurring_unverified', 'count': len(with_occurrences)})
    if counts['calendar_follow_up_trimmed']:
        warnings.append({'code': 'follow_up_trimmed', 'count': counts['calendar_follow_up_trimmed']})
    snapshot: dict = {'schema': AGENDA_SCHEMA, 'source': 'outlook_hxstore', 'lifecycle': 'unverified',
                'observed_at': now.astimezone(timezone.utc).isoformat(),
                'window_start': _iso(lo), 'window_end': _iso(hi), 'time_zone': str(tz),
                'warnings': warnings, 'items': items, 'follow_up_reviews': reviews}
    while len(json.dumps(snapshot)) > AGENDA_MAX_BYTES and (reviews or items):
        rows, counter, code = ((reviews, 'calendar_follow_up_trimmed', 'follow_up_trimmed') if reviews
                               else (items, 'calendar_agenda_trimmed', 'trimmed'))
        rows.pop()
        counts[counter] += 1
        warning = next((w for w in warnings if w['code'] == code), None)
        if warning is None:
            warnings.append({'code': code, 'count': counts[counter]})
        else:
            warning['count'] = counts[counter]
    counts['calendar_follow_up_reviews'] = len(reviews)
    counts['calendar_agenda_items'] = len(snapshot['items'])
    counts['calendar_agenda_undecoded'] = undecoded
    return snapshot, counts

