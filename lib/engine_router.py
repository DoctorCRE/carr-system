"""The email router's side of the engine's doors 21 (suggestions) and 22 (reply drafts).

The doors are fixed in the engine repository's docs/new-database.md, "Doors the old
system calls". The router calls them only when outlook.json sets engine_router; otherwise
nothing here runs and the router reads and writes the record layer as before.

verbs() answers the router's three record-layer verbs from the engine, so
lib/email_router.py routes the same way on either side:

- email-router-queue reads GET /jobs/router-queue page by page, skips the emails the
  router already handled, and rebuilds the record layer's queue shape from the answer.
- record-email-proposal stores a suggestion through POST /jobs/proposals and a reply draft
  through POST /jobs/reply-drafts, under the router's own key, which it derives from the
  proposal so a retry converges.
- inherit-email-thread links the email to its thread's deal through
  POST /jobs/activities/{id}/about. lib/email_matching checked the thread; the engine
  keeps the link without checking it again.

As the door doc says, a 5xx or a lost connection is retried with the same key, and a
refusal of the caller itself fails the write the same way. Every other 4xx is final and
counted as a refusal. The engine has no field for a proposal's confidence, sender role,
matching evidence or a filing's note, so those are not sent. A next step goes without an
owner, and the engine gives it to the partner who accepts it (ruling 22g).
"""
from __future__ import annotations

import json
from typing import Callable
from urllib.parse import quote

from lib.engine_mail import Door, Reply, verdict

PAGE = 200


class Refused(Exception):
    """The engine refused for good; the code names the refusal in the router's receipt."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Unanswered(Exception):
    """No final answer (5xx, lost connection or a caller refusal): the email is routed again."""


def refusal_code(error: Exception) -> str | None:
    return error.code if isinstance(error, Refused) else None


def _answer(reply: Reply, refusal: str) -> dict:
    judged = verdict(reply)
    if judged == 'done':
        return reply.body
    if judged in ('retry', 'caller'):
        raise Unanswered(judged)
    raise Refused(f'{refusal}_{reply.error}')


def _person(person: dict) -> dict:
    """An engine person in the record layer's contact shape that lib.email_matching reads."""
    return {'email': person.get('address'), 'name': person.get('name'), 'kind': person.get('kind'),
            'party_id': person.get('party_id'), 'internal': person.get('internal') is True, 'role': person.get('role'),
            'unknown': person.get('party_id') is None,
            'related_clients': [{'client_id': client} for client in person.get('client_party_ids') or []]}


def _thread(prior: dict) -> dict:
    return {'activity_id': prior['activity_id'], 'deal': prior['deal_id'], 'occurred_at': prior['happened_at'],
            'conversation_id': prior.get('conversation_id'), 'subject': prior.get('subject') or '',
            'sender': _person(prior.get('sender') or {}),
            'recipients': [_person(p) for p in prior.get('recipients') or []]}


def _deal(deal: dict) -> dict:
    return {'id': deal['deal_id'], 'name': deal.get('name'), 'phase': deal.get('phase'), 'deal_type': deal.get('kind'),
            'client_party_id': deal.get('client_party_id'), 'client_id': deal.get('client_party_id'),
            'client': deal.get('client_name'), 'property': deal.get('property'), 'city': deal.get('city'),
            'participants': deal.get('participants') or []}


def _email(mail: dict, owner: str) -> dict:
    """One queued email as the record layer's queue gave it. An email on one open deal, or with no open
    deal on one closed deal, names it; on several it names none and the router leaves it where it is."""
    linked = mail.get('deals') or [{'deal_id': deal} for deal in dict.fromkeys(
        link['deal_id'] for link in mail.get('about') or [] if link.get('deal_id'))]
    filed = linked[0] if len(linked) == 1 else {}
    people = [_person(mail.get('sender') or {}), *(_person(p) for p in mail.get('recipients') or [])]
    contact = next((p for p in people if p['party_id'] and not p['internal']), None)
    party = contact['party_id'] if contact else next(
        (link['party_id'] for link in mail.get('about') or [] if link.get('party_id')), None)
    return {'activity_id': mail['activity_id'], 'occurred_at': mail['happened_at'],
            'kind': 'email_out' if mail.get('outgoing') else 'email_in',
            'summary': mail.get('subject') or '', 'subject': mail.get('subject') or '', 'body': mail.get('body') or '',
            'conversation_id': mail.get('conversation_id'), 'sender': people[0], 'recipients': people[1:],
            'mailbox_owner': mail.get('mailbox_user') or owner, 'lead_source': mail.get('lead_source') is True,
            'subject_id': party, 'subject_name': contact['name'] if contact else None,
            'deal': filed.get('deal_id'), 'deal_name': filed.get('name'), 'phase': filed.get('phase'),
            'deal_type': filed.get('kind'), 'several_deals': len(linked) > 1, 'suppressed_deals': mail.get('suppressed_deal_ids') or [],
            'thread_history': [_thread(prior) for prior in mail.get('thread') or []]}


def verbs(door: Door, owner: str) -> Callable[[str, dict], dict]:
    """The router's record-layer verbs, answered from the engine for owner's mailbox."""
    served: dict[str, dict] = {}

    def post(path: str, request: dict, refusal: str) -> dict:
        return _answer(door('POST', path, json.dumps(request).encode()), refusal)

    def queue(args: dict) -> dict:
        handled, after = set(args['exclude']), None
        pending: list[dict] = []
        deals: list[dict] = []
        while True:
            page = _answer(door('GET', f"/jobs/router-queue?days={args['days']}&limit={PAGE}"
                                + (f'&after={quote(after)}' if after else ''), None), 'email_router_queue')
            deals = page.get('open_deals') or []
            pending += [mail for mail in page.get('emails') or [] if mail['activity_id'] not in handled]
            after = page.get('next_cursor')
            if not after or len(pending) > args['limit']:
                break
        emails = [_email(mail, owner) for mail in pending[:args['limit']]]
        served.update((email['activity_id'], email) for email in emails)
        return {'emails': emails, 'open_deals': [_deal(deal) for deal in deals],
                'more': bool(after) or len(pending) > args['limit']}

    def store(args: dict) -> dict:
        common = {'idempotency_key': args['idempotency_key'], 'activity_id': args['activity_id'],
                  'evidence': args['evidence_quote']}
        if args['kind'] == 'reply_draft':
            return post('/jobs/reply-drafts', {**common, 'subject': args['subject'], 'body': args['body']},
                        'email_proposal')
        party = served.get(args['activity_id'], {}).get('subject_id')
        record = ({'deal_id': args['deal']} if args.get('deal') else
                  {'party_id': party} if args['kind'] == 'next_step' and party else {})
        if not record and args['kind'] != 'possible_new_deal':
            raise Refused('email_proposal_no_record')
        step = {'what': args['text'], 'due_on': args['next_date']} if args['kind'] == 'next_step' else {}
        return post('/jobs/proposals', {**common, 'kind': args['kind'], **record, **step}, 'email_proposal')

    def inherit(args: dict) -> dict:
        deal = next(prior['deal'] for prior in served[args['activity_id']]['thread_history']
                    if prior['activity_id'] == args['thread_activity_id'])
        return post(f"/jobs/activities/{quote(args['activity_id'])}/about",
                    {'idempotency_key': args['idempotency_key'], 'about': [{'deal_id': deal}]}, 'email_thread')

    doors = {'email-router-queue': queue, 'record-email-proposal': store, 'inherit-email-thread': inherit}

    def call(verb: str, args: dict) -> dict:
        if verb not in doors:
            raise PermissionError(f'the engine answers no {verb} for the router')
        return doors[verb](args)
    return call
