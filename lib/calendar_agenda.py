"""Pure Outlook calendar display projection; no transport or record writes.

Intake owns decoding and publication. Cancellation applies across stored copies
of the same GOID because revision ordering is not established by the decoder.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import re

from lib.outlook_settings import OutlookSettings

AGENDA_SCHEMA = 'carr-calendar-agenda.v1'
AGENDA_DAYS = 14
AGENDA_ATTENDEES = 50
AGENDA_REVIEWS = 100
AGENDA_MAX_BYTES = 200_000
UNREADABLE = 'Meeting title unreadable'
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


def _occurrence_series(goids: Iterable[str]) -> set[str]:
    return {_series(goid) for goid in goids if _instance(goid) != '00000000'}


def _identities(events: Iterable[dict]) -> set[tuple[str, int]]:
    return {(e['goid'], e['start_unix']) for e in events}


def _subject_evidence(event: dict) -> str | None:
    return event.get('legacy_subject') or event['subject']


def _review_key(row: dict) -> tuple[str, str]:
    return row['end'], row['key']


def _item_key(row: dict) -> tuple[str, str, str]:
    return row['start'], row['title'], row['key']


def _add_warning(warnings: list[dict], code: str, count: int) -> None:
    for warning in warnings:
        if warning['code'] == code:
            warning['count'] += count
            return
    warnings.append({'code': code, 'count': count})


def agenda(events: list[dict], meta: dict, settings: OutlookSettings, now: datetime) -> tuple[dict, dict]:
    """The display snapshot of today's and upcoming meetings, plus recent outcome review proposals.

    A meeting shows only when every copy in its decode group names the
    configured account as organizer or attendee. The snapshot warns that
    group-only invites are left out. Copies collapse by Global Object ID.
    Outlook writes each recurring occurrence under its own ID (the instance date
    sits in bytes 16-19), so the series master is skipped once occurrences exist.
    Revision order is not decoded: an ID seen at more than one start is one row
    flagged time_changed with every observed time. Attendee responses and folder
    membership are not decoded either, so every snapshot carries that warning;
    the brief turns warnings into plain sentences and never reports them ready.
    No notes or bodies leave the private copy.
    """
    legacy: list[dict] = []
    recovered: list[dict] = []
    for event in events:
        (recovered if 'legacy_subject' in event and event['legacy_subject'] is None else legacy).append(event)
    legacy_goids = {e['goid'] for e in legacy}
    legacy_ids = {e['id'] for e in legacy if e.get('id') is not None}
    legacy_series = _occurrence_series(legacy_goids)
    recovered = [e for e in recovered if e['goid'] not in legacy_goids
                 and e.get('id') not in legacy_ids
                 and e['goid'] not in legacy_series]
    legacy_identities = _identities(legacy)
    identity_count = meta.get('event_identities') or 0
    # Main's payload budget includes its original subjects and undecoded warning.
    snapshot, counts, unreadable_keys = _project(
        legacy, max(0, identity_count - len(legacy_identities)), settings, now)
    _trim(snapshot, counts, snapshot)
    warnings = snapshot['warnings']
    if recovered:
        extra, extra_counts, extra_unreadable = _project(recovered, 0, settings, now)
        unreadable_keys.update(extra_unreadable)
        _add_recovered(snapshot, counts, extra, extra_counts,
                       len(legacy_series & _occurrence_series(e['goid'] for e in recovered)))

    identities_seen = legacy_identities | _identities(recovered)
    unreadable = _identities(e for e in (*legacy, *recovered) if e.get('subject_undecoded'))
    undecoded = max(0, identity_count - len(identities_seen)) + len(unreadable)
    warnings[:] = [warning for warning in warnings if warning['code'] != 'undecoded']
    if undecoded:
        warnings.insert(2, {'code': 'undecoded', 'count': undecoded})
    for section in ('items', 'follow_up_reviews'):
        for row in snapshot[section]:
            if row['key'] in unreadable_keys:
                row['title'] = UNREADABLE
    counts['calendar_follow_up_reviews'] = len(snapshot['follow_up_reviews'])
    counts['calendar_agenda_items'] = len(snapshot['items'])
    counts['calendar_agenda_undecoded'] = undecoded
    return snapshot, counts


def _add_recovered(snapshot: dict, counts: dict, extra: dict, extra_counts: dict, shared_series: int) -> None:
    reviews = extra['follow_up_reviews']
    slots = AGENDA_REVIEWS - len(snapshot['follow_up_reviews'])
    excess = max(0, len(reviews) - slots)
    if excess:
        del reviews[slots:]
        extra_counts['calendar_follow_up_trimmed'] += excess
        _add_warning(extra['warnings'], 'follow_up_trimmed', excess)
    for name, count in extra_counts.items():
        counts[name] += count
    for warning in extra['warnings']:
        code, count = warning['code'], warning.get('count', 0)
        if code == 'recurring_unverified':
            count -= shared_series
        if count:
            _add_warning(snapshot['warnings'], code, count)

    snapshot['items'].extend(extra['items'])
    snapshot['follow_up_reviews'].extend(reviews)
    _trim(snapshot, counts, extra)
    snapshot['items'].sort(key=_item_key)
    snapshot['follow_up_reviews'].sort(key=_review_key, reverse=True)


def _project(events: list[dict], undecoded: int, settings: OutlookSettings, now: datetime) -> tuple[dict, dict, set[str]]:
    tz = settings.timezone
    day = datetime.combine(now.astimezone(tz).date(), time(), tz)
    lo, hi = int(day.timestamp()), int((day + timedelta(days=AGENDA_DAYS)).timestamp())
    review_hi = int(now.timestamp())
    review_lo = review_hi - 86_400
    account = settings.account
    with_occurrences = _occurrence_series(e['goid'] for e in events)
    by_goid: dict[str, list[dict]] = {}
    for e in events:
        by_goid.setdefault(e['goid'], []).append(e)
    counts = dict.fromkeys(('calendar_agenda_items', 'calendar_agenda_canceled', 'calendar_agenda_conflicts',
                            'calendar_agenda_other_accounts', 'calendar_agenda_time_changed',
                            'calendar_agenda_recurring', 'calendar_agenda_trimmed',
                            'calendar_follow_up_trimmed'), 0)
    items, reviews = [], []
    unreadable_keys = set()
    for goid, group in by_goid.items():
        if _instance(goid) == '00000000' and goid in with_occurrences:
            continue
        current = [e for e in group if e['start_unix'] < hi
                   and (e['end_unix'] > lo or e['start_unix'] == e['end_unix'] == lo)]
        recent = [e for e in group if review_lo < e['end_unix'] <= review_hi]
        if not current and not recent:
            continue
        if not all(account in {(e['organizer_email'] or '').lower(), *((m or '').lower() for _, m in e['attendees'])}
                   for e in group):
            counts['calendar_agenda_other_accounts'] += 1
            continue
        if any(e.get('lifecycle_status') == 'canceled' or CANCELED.match(_subject_evidence(e) or '') for e in group):
            counts['calendar_agenda_canceled'] += 1
            continue
        shown = max(current or recent, key=lambda e: (len(e['attendees']), len(_subject_evidence(e) or ''), e['end_unix']))
        starts = sorted({e['start_unix'] for e in group})
        subjects = {subject for e in group if (subject := _subject_evidence(e)) is not None
                    or not e.get('subject_undecoded')}
        conflict = len(subjects) > 1 or len({
            json.dumps([e['location'], e['end_unix'] - e['start_unix'],
                        sorted((n or '', m or '') for n, m in e['attendees'])]) for e in group
        }) > 1
        recurring = _instance(goid) != '00000000'
        start, end = shown['start_unix'], shown['end_unix']
        local = datetime.fromtimestamp(start, tz)
        local_end = datetime.fromtimestamp(end, tz)
        counts['calendar_agenda_conflicts'] += conflict
        counts['calendar_agenda_time_changed'] += len(starts) > 1
        counts['calendar_agenda_recurring'] += recurring
        attendees = [{'name': name or None, 'email': (email or '').lower() or None} for name, email in shown['attendees']]
        key = _hash(goid)[:16]
        if shown.get('subject_undecoded'):
            unreadable_keys.add(key)
        item = {'key': key, 'start': _iso(start), 'end': _iso(end),
                      'all_day': local.time() == local_end.time() == time() and local_end.date() > local.date(),
                      'title': _subject_evidence(shown) or 'Meeting', 'location': shown['location'] or None,
                      'organizer': {'name': shown['organizer_name'] or None,
                                    'email': (shown['organizer_email'] or '').lower() or None},
                      'attendees': attendees[:AGENDA_ATTENDEES], 'attendee_count': len(attendees),
                      'recurring': recurring, 'time_changed': len(starts) > 1,
                      'times': [_iso(t) for t in starts] if len(starts) > 1 else [],
                      'candidates': len(group), 'conflict': conflict}
        if current:
            items.append(item)
        if (review_lo < end <= review_hi and end > start
                and not item['all_day'] and not conflict and len(starts) == 1):
            reviews.append({k: item[k] for k in ('key', 'title', 'start', 'end')} | {
                'state': 'needs_review',
                'prompt': 'Confirm whether this meeting happened. If it did, record the outcome and decide the next step.'})
    reviews.sort(key=_review_key, reverse=True)
    items.sort(key=_item_key)
    counts['calendar_follow_up_trimmed'] = max(0, len(reviews) - AGENDA_REVIEWS)
    del reviews[AGENDA_REVIEWS:]
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
    return snapshot, counts, unreadable_keys


def _trim(snapshot: dict, counts: dict, candidates: dict) -> None:
    reviews = list(candidates['follow_up_reviews'])
    cutoff = snapshot['observed_at']
    ended = sorted((i for i in candidates['items'] if i['end'] <= cutoff), key=_review_key, reverse=True)
    upcoming = [i for i in candidates['items'] if i['end'] > cutoff]
    while (ended or reviews or upcoming) and len(json.dumps(snapshot)) > AGENDA_MAX_BYTES:
        if ended:
            snapshot['items'].remove(ended.pop())
            counter, code = 'calendar_agenda_trimmed', 'ended_trimmed'
        elif reviews:
            snapshot['follow_up_reviews'].remove(reviews.pop())
            counter, code = 'calendar_follow_up_trimmed', 'follow_up_trimmed'
        else:
            snapshot['items'].remove(upcoming.pop())
            counter, code = 'calendar_agenda_trimmed', 'trimmed'
        counts[counter] += 1
        _add_warning(snapshot['warnings'], code, 1)
