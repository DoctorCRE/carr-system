"""Selftest for lib/engine_router.py: the email router on engine doors 21 and 22."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import email_matching, email_router, engine_mail, engine_router  # noqa: E402

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
DEAL = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
CLIENT = '33333333-3333-4333-8333-333333333333'
LOI = 'We received the LOI from the landlord today.'
ASK = 'Can you send the floor plan by Friday?'
BROKER = 'broker@brokerage.example'
SENDER = {'address': 'tenant@clinic.example', 'name': 'Synthetic Tenant', 'party_id': CLIENT, 'internal': False,
          'client_party_ids': [], 'role': 'client'}
TO_BROKER = {'address': BROKER, 'name': 'Synthetic Broker', 'kind': 'to', 'party_id': None, 'internal': True,
             'client_party_ids': [], 'role': None}
OPEN: dict = {'deal_id': DEAL, 'name': 'Synthetic clinic lease', 'phase': 'site_selection', 'kind': 'lease',
        'client_party_id': CLIENT, 'client_name': 'Synthetic Clinic', 'property': 'Synthetic building', 'city': None,
        'participants': []}
STEP = {'kind': 'next_step', 'evidence_quote': ASK, 'confidence': 0.8, 'text': 'Send the floor plan',
        'next_date': '2026-11-21', 'owner': None}
BOTH = {'proposals': [{'kind': 'phase_move', 'evidence_quote': LOI, 'confidence': 0.9}, STEP]}


def mail(activity: str, body: str = f'{LOI} {ASK}', **fields) -> dict:
    return {'activity_id': activity, 'message_id': f'{activity}@mail.example', 'happened_at': '2026-11-20T14:00:00+00:00',
            'outgoing': False, 'mailbox_user': 'joe', 'subject': 'Clinic space', 'body': body,
            'conversation_id': f'conversation-{activity}', 'sender': SENDER, 'recipients': [TO_BROKER],
            'about': [{'party_id': CLIENT}],
            'deals': [{'deal_id': DEAL, 'name': OPEN['name'], 'phase': 'site_selection', 'kind': 'lease'}],
            'suppressed_deal_ids': [], 'thread': [], **fields}


def thread_only(seen: list[dict]):
    """Matching that files by thread alone and records the email it was handed; no model is reached."""
    def matching(email, deals, history):
        seen.append(email)
        prior = email_matching.thread_match(email, deals, history)
        return (email_matching.MatchDecision('inherit_thread', prior['deal'], thread_activity_id=prior['activity_id'])
                if prior else email_matching.MatchDecision('contact_only'))
    return matching


class Engine:
    """Fake engine: a paged router queue, and saves that replay a key and converge on the payload."""

    def __init__(self, emails: list[dict], page: int = 200, open_deals: list[dict] | None = None):
        self.emails, self.page, self.open_deals = emails, page, open_deals or [OPEN]
        self.requests: list[tuple[str, str, dict]] = []
        self.keys: dict[str, tuple[str, dict, engine_mail.Reply]] = {}
        self.stored: dict[str, dict] = {}
        self.fail: dict[str, list[engine_mail.Reply]] = {}

    def __call__(self, method: str, path: str, body: bytes | None) -> engine_mail.Reply:
        url = urlsplit(path)
        request = json.loads(body) if body else {}
        self.requests.append((method, url.path, dict(request)))
        if self.fail.get(url.path):
            return self.fail[url.path].pop(0)
        if url.path == '/jobs/router-queue':
            query = parse_qs(url.query)
            assert int(query['limit'][0]) <= 200 and 1 <= int(query['days'][0]) <= 7
            ids = [m['activity_id'] for m in self.emails]
            start = ids.index(query['after'][0]) + 1 if 'after' in query else 0
            rows = self.emails[start:start + self.page]
            more = start + self.page < len(self.emails)
            return engine_mail.Reply(200, {'emails': rows, 'open_deals': self.open_deals,
                                           'next_cursor': rows[-1]['activity_id'] if more else None})
        key = request.pop('idempotency_key')
        if key in self.keys:
            first_path, first, reply = self.keys[key]
            if (first_path, first) != (url.path, request):
                return engine_mail.Reply(409, {'error': 'key_reuse'})
            return engine_mail.Reply(reply.status, {**reply.body, 'replayed': True})
        payload = json.dumps([url.path, request], sort_keys=True)
        if url.path.endswith('/about'):
            reply = engine_mail.Reply(200, {'activity_id': url.path.split('/')[3], 'about': request['about']})
        elif payload in self.stored:
            reply = engine_mail.Reply(200, {**self.stored[payload], 'existing': True})
        else:
            self.stored[payload] = {'proposal_id': f'p-{len(self.stored)}', 'status': 'pending'}
            reply = engine_mail.Reply(201, self.stored[payload])
        self.keys[key] = (url.path, request, reply)
        return reply

    def saved(self, path: str) -> list[dict]:
        return [request for method, at, request in self.requests if method == 'POST' and at == path]


def run(engine: Engine, classify=lambda email, today: BOTH, state: dict | None = None, out: Path | None = None):
    """The router's run with the switch on: nothing but the engine's doors is reachable."""
    with patch.object(email_router.email_summary, 'backfill', side_effect=AssertionError('summaries stay off')):
        if out is not None:
            return email_router.run(out, engine_router.verbs(engine, 'joe'), classify, engine_router.refusal_code,
                                    NOW, clock=lambda: NOW, account=BROKER, expire_blocked=False)
        return 'route', email_router.route(engine_router.verbs(engine, 'joe'), classify, engine_router.refusal_code,
                                           {} if state is None else state, NOW, lambda: NOW, account=BROKER)


class SwitchOn(unittest.TestCase):
    def test_a_filed_email_stores_its_suggestions_and_draft_through_the_engine(self):
        engine = Engine([mail('e1')])
        reply = {'kind': 'reply_draft', 'evidence_quote': ASK, 'confidence': 0.9, 'subject': 'Re: Clinic space',
                 'body': 'Sending it today.'}
        with tempfile.TemporaryDirectory() as out:
            outcome, counts = run(engine, lambda email, today: {'proposals': [*BOTH['proposals'], reply]}, out=Path(out))
        self.assertEqual((outcome, counts['recorded'], counts['routed']), ('succeeded', 3, 1))
        self.assertNotIn('expired_blocked', counts)
        proposals = engine.saved('/jobs/proposals')
        self.assertEqual([{k: v for k, v in p.items() if k != 'idempotency_key'} for p in proposals], [
            {'kind': 'phase_move', 'activity_id': 'e1', 'evidence': LOI, 'deal_id': DEAL},
            {'kind': 'next_step', 'activity_id': 'e1', 'evidence': ASK, 'deal_id': DEAL,
             'what': 'Send the floor plan', 'due_on': '2026-11-21'}])
        self.assertEqual([{k: v for k, v in d.items() if k != 'idempotency_key'} for d in engine.saved('/jobs/reply-drafts')],
                         [{'activity_id': 'e1', 'evidence': ASK, 'subject': 'Re: Clinic space', 'body': 'Sending it today.'}])
        self.assertEqual({path for _, path, _ in engine.requests},
                         {'/jobs/router-queue', '/jobs/proposals', '/jobs/reply-drafts'})

    def test_a_next_step_without_a_deal_names_the_party_the_email_is_about(self):
        engine = Engine([mail('e1', ASK, deals=[])])
        counts = email_router.route(engine_router.verbs(engine, 'joe'), lambda email, today: {'proposals': [STEP]},
                                    engine_router.refusal_code, {}, NOW, lambda: NOW,
                                    matching=lambda *_: email_matching.MatchDecision('contact_only'), account=BROKER)
        self.assertEqual((counts['recorded'], counts['contact_only']), (1, 1))
        self.assertEqual(engine.saved('/jobs/proposals')[0]['party_id'], CLIENT)
        self.assertNotIn('deal_id', engine.saved('/jobs/proposals')[0])

    def test_a_reply_with_no_conversation_id_inherits_by_subject_and_then_gets_its_next_step(self):
        prior = {'activity_id': 'e0', 'deal_id': DEAL, 'happened_at': '2026-11-19T14:00:00+00:00',
                 'subject': 'Clinic space', 'sender': SENDER}
        engine = Engine([mail('e1', ASK, subject='RE: Clinic space', conversation_id=None, deals=[], thread=[prior])])
        _, counts = run(engine, lambda email, today: {'proposals': [STEP]})
        self.assertEqual((counts['inherited'], counts['recorded'], counts['routed']), (1, 1, 1))
        link = engine.saved('/jobs/activities/e1/about')
        self.assertEqual(link[0]['about'], [{'deal_id': DEAL}])
        self.assertEqual(engine.saved('/jobs/proposals')[0]['deal_id'], DEAL)

    def test_a_deal_a_partner_took_the_email_off_is_never_inherited_again(self):
        prior = {'activity_id': 'e0', 'deal_id': DEAL, 'happened_at': '2026-11-19T14:00:00+00:00',
                 'subject': 'Clinic space', 'sender': SENDER}
        engine = Engine([mail('e1', ASK, deals=[], suppressed_deal_ids=[DEAL], thread=[prior])])
        seen = []

        def matching(email, deals, history):
            seen.append(email_matching.thread_match(email, [d for d in deals if d['id'] not in email['suppressed_deals']],
                                                    history))
            return email_matching.MatchDecision('file_to_deal', DEAL, confidence=0.8)
        counts = email_router.route(engine_router.verbs(engine, 'joe'), lambda *_: {'proposals': []},
                                    engine_router.refusal_code, {}, NOW, lambda: NOW, matching=matching, account=BROKER)
        self.assertEqual(seen, [None])
        self.assertEqual((counts['inherited'], counts['recorded']), (0, 0))
        self.assertEqual(engine.saved('/jobs/activities/e1/about'), [])

    def test_a_crashed_write_retries_with_the_same_key_and_converges(self):
        engine = Engine([mail('e1')])
        engine.fail['/jobs/proposals'] = [engine_mail.Reply(503, {'error': 'database_unavailable'})]
        state: dict = {}
        _, first = run(engine, state=state)
        self.assertEqual((first['write_failed'], first['routed']), (1, 0))
        _, second = run(engine, state=state)
        self.assertEqual((second['recorded'], second['routed']), (2, 1))
        keys = [p['idempotency_key'] for p in engine.saved('/jobs/proposals') if p['kind'] == 'phase_move']
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        again = Engine([mail('e1')])
        again.keys, again.stored = engine.keys, engine.stored
        _, third = run(again)
        self.assertEqual(third['recorded'], 2)
        self.assertEqual(len(engine.stored), 2)

    def test_a_refused_suggestion_is_final_and_counted(self):
        engine = Engine([mail('e1')])
        engine.fail['/jobs/proposals'] = [engine_mail.Reply(422, {'error': 'phase_not_eligible'})]
        state: dict = {}
        _, counts = run(engine, state=state)
        self.assertEqual((counts['refused'], counts['recorded'], counts['routed']), (1, 1, 1))
        self.assertEqual(counts['refusal_codes'], {'email_proposal_phase_not_eligible': 1})
        _, again = run(engine, state=state)
        self.assertEqual(again['emails'], 0)

    def test_a_refusal_of_the_caller_is_not_final(self):
        engine = Engine([mail('e1')])
        engine.fail['/jobs/proposals'] = [engine_mail.Reply(403, {'error': 'unknown_job'})]
        _, counts = run(engine)
        self.assertEqual((counts['write_failed'], counts['refused'], counts['routed']), (1, 0, 0))

    def test_the_queue_pages_past_handled_mail_until_the_cursor_ends(self):
        engine = Engine([mail(f'e{n:02}', 'Nothing to act on here today.') for n in range(32)], page=2)
        handled = {f'e{n:02}': NOW.timestamp() for n in range(30)}
        state = {'version': email_router.STATE_VERSION, 'routed': dict(handled), 'reply_recheck': {}}
        _, counts = run(engine, lambda *_: {'proposals': []}, state=state)
        self.assertEqual((counts['emails'], counts['more']), (2, 0))
        self.assertEqual(sorted(state['routed']), sorted([*handled, 'e30', 'e31']))

    def test_an_unfiled_email_inherits_by_conversation_id_whatever_its_subject(self):
        prior = {'activity_id': 'e0', 'deal_id': DEAL, 'happened_at': '2026-11-19T14:00:00+00:00',
                 'conversation_id': 'conversation-e1', 'subject': 'Clinic space', 'sender': SENDER}
        engine = Engine([mail('e1', ASK, subject='Floor plan question', deals=[], thread=[prior])])
        counts = email_router.route(engine_router.verbs(engine, 'joe'), lambda *_: {'proposals': []},
                                    engine_router.refusal_code, {}, NOW, lambda: NOW, matching=thread_only([]),
                                    account=BROKER)
        self.assertEqual(counts['inherited'], 1)
        self.assertEqual(engine.saved('/jobs/activities/e1/about')[0]['about'], [{'deal_id': DEAL}])

    def test_matching_sees_the_recipients_and_the_mailbox_partner_the_queue_names(self):
        cc = {'address': 'landlord@property.example', 'name': 'Synthetic Landlord', 'kind': 'cc', 'party_id': None,
              'internal': False, 'client_party_ids': [], 'role': None}
        engine = Engine([mail('e1', ASK, deals=[], mailbox_user='dell', recipients=[TO_BROKER, cc])])
        seen: list[dict] = []
        email_router.route(engine_router.verbs(engine, 'joe'), lambda *_: {'proposals': []}, engine_router.refusal_code,
                           {}, NOW, lambda: NOW, matching=thread_only(seen), account=BROKER)
        self.assertEqual(seen[0]['mailbox_owner'], 'dell')
        self.assertEqual([(r['email'], r['kind'], r['unknown']) for r in seen[0]['recipients']],
                         [(BROKER, 'to', True), ('landlord@property.example', 'cc', True)])

    def test_an_email_on_several_deals_gets_no_new_link_and_nothing_on_one_of_them(self):
        second = {'deal_id': OTHER, 'name': 'Synthetic second lease', 'phase': 'negotiation', 'kind': 'lease'}
        prior = {'activity_id': 'e0', 'deal_id': OTHER, 'happened_at': '2026-11-19T14:00:00+00:00',
                 'conversation_id': 'conversation-e1', 'subject': 'Clinic space', 'sender': SENDER}
        cases = {'open': {'deals': [mail('e1')['deals'][0], second]},
                 'closed': {'deals': [], 'about': [{'party_id': CLIENT}, {'deal_id': DEAL}, {'deal_id': OTHER}]}}
        for name, fields in cases.items():
            with self.subTest(name):
                engine = Engine([mail('e1', thread=[prior], **fields)], open_deals=[OPEN, {**OPEN, **second}])
                seen: list[dict] = []
                counts = email_router.route(engine_router.verbs(engine, 'joe'), lambda *_: BOTH,
                                            engine_router.refusal_code, {}, NOW, lambda: NOW,
                                            matching=thread_only(seen), account=BROKER)
                self.assertEqual((seen, counts['inherited'], counts['routed']), ([], 0, 1))
                self.assertEqual(engine.saved('/jobs/activities/e1/about'), [])
                self.assertEqual([(p['kind'], p.get('deal_id'), p.get('party_id')) for p in engine.saved('/jobs/proposals')],
                                 [('next_step', None, CLIENT)])

    def test_one_closed_deal_still_means_filed(self):
        engine = Engine([mail('e1', deals=[], about=[{'party_id': CLIENT}, {'deal_id': OTHER}, {'deal_id': OTHER}])])
        seen: list[dict] = []
        email_router.route(engine_router.verbs(engine, 'joe'), lambda *_: BOTH, engine_router.refusal_code, {}, NOW,
                           lambda: NOW, matching=thread_only(seen), account=BROKER)
        self.assertEqual(seen, [])
        self.assertEqual({p.get('deal_id') for p in engine.saved('/jobs/proposals')}, {OTHER})

    def test_a_failed_queue_read_fails_the_run(self):
        engine = Engine([mail('e1')])
        engine.fail['/jobs/router-queue'] = [engine_mail.Reply(0, {})]
        _, counts = run(engine)
        self.assertEqual((counts['queue_failed'], counts['emails']), (1, 0))

    def test_outbound_mail_from_another_mailbox_is_skipped(self):
        engine = Engine([mail('e1', outgoing=True, sender={**SENDER, 'address': 'other@brokerage.example',
                                                            'internal': True})])
        _, counts = run(engine)
        self.assertEqual((counts['emails'], counts['recorded']), (1, 0))
        self.assertEqual(engine.saved('/jobs/proposals'), [])

    def test_step_reads_the_engine_and_never_the_record_layer_when_switched_on(self):
        engine = Engine([mail('e1')])
        settings = SimpleNamespace(engine_router=True, engine_credentials=Path('/synthetic/engine.env'), owner='joe',
                                   model='haiku', account=BROKER)
        with tempfile.TemporaryDirectory() as repo, \
                patch.object(engine_mail, 'connect', return_value=engine) as connect, \
                patch.object(email_router, 'ask_model', return_value=BOTH), \
                patch('lib.outlook_intake._carr', side_effect=AssertionError('record layer reached')), \
                patch.object(email_router.email_summary, 'backfill', side_effect=AssertionError('summaries stay off')), \
                patch('sys.stdout'):
            self.assertEqual(email_router.step(Path(repo), settings), 0)
        connect.assert_called_once_with(Path('/synthetic/engine.env'), 'joe')
        self.assertEqual(len(engine.saved('/jobs/proposals')), 2)


if __name__ == '__main__':
    unittest.main()
