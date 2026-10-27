#!/usr/bin/env python3
"""Acceptance tests for the headless Friday social batch (WR561.S2); no network, no store writes."""
from __future__ import annotations

import copy
import fnmatch
import importlib.util
import json
import plistlib
import re
import socket
import struct
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tools'))

import social_batch as sb  # noqa: E402
from lib import launchd_hold  # noqa: E402
from lib.headless_tasks import calendar_entries  # noqa: E402

FIXTURES = REPO / 'tools/fixtures/social-batch'
RECORDED = json.loads((FIXTURES / 'blotato.json').read_text())
CADENCE = json.loads((REPO / 'ops/social/cadence.v1.json').read_text())
UTC = timezone.utc
FRIDAY_BEFORE_NOV_2 = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)   # Fri 06:00 CDT
FRIDAY_BEFORE_OCT_26 = datetime(2026, 10, 23, 11, 0, tzinfo=UTC)  # Fri 06:00 CDT


def png(path: Path, width: int, height: int) -> None:
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\x00' + b'\x00\x00\x00' * width for _ in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def item(platform, when, state='scheduled', number=[7000000]):
    number[0] += 1
    value = copy.deepcopy(RECORDED['listing_item'])
    value.update(id=str(number[0]), platform=platform, postTime=when, state={'type': state},
                 account={'id': RECORDED['accounts'][platform]['accountId'], 'name': 'synthetic', 'subaccount': None})
    return value


class FakeBlotato:
    """A stateful stand-in for backend.blotato.com built from the recorded shapes.

    Any request outside the read endpoints and the three write endpoints fails the test.
    """

    def __init__(self, items=(), honour_window=True):
        self.items = [dict(i) for i in items]
        self.honour_window = honour_window
        self.requests: list[tuple[str, str]] = []
        self.posts: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.listing_status = 200
        self.listing_body = None
        self.lose_create_response = False
        self.created = 0

    def __call__(self, method, url, headers, data):
        self.requests.append((method, url))
        parsed = urlparse(url)
        api = urlparse(sb.API)
        if parsed.netloc == api.netloc and parsed.path == api.path + '/posts' and method == 'GET':
            if self.listing_body is not None or self.listing_status != 200:
                return self.listing_status, self.listing_body or b'{}'
            query = parse_qs(parsed.query)
            since, until = (sb.parse_utc(query[k][0]) for k in ('since', 'until'))
            chosen = [i for i in self.items if not self.honour_window or since <= sb.parse_utc(i['postTime']) < until]
            return 200, json.dumps({'items': chosen}).encode()
        if parsed.netloc == api.netloc and parsed.path.startswith(api.path + '/posts/') and method == 'GET':
            post = self.posts.get(parsed.path.rsplit('/', 1)[1])
            return (200, json.dumps(post).encode()) if post else (404, b'{"message":"not found"}')
        if url == sb.API + '/media/uploads' and method == 'POST':
            name = json.loads(data)['filename']
            upload = RECORDED['media_upload']
            return 200, json.dumps({'presignedUrl': upload['presignedUrl'] + name,
                                    'publicUrl': upload['publicUrl'] + name}).encode()
        if url.startswith(RECORDED['media_upload']['presignedUrl']) and method == 'PUT':
            self.blobs[url.rsplit('/', 1)[1]] = data
            return 200, b''
        if url.startswith(RECORDED['media_upload']['publicUrl']) and method == 'GET':
            return 200, self.blobs[url.rsplit('/', 1)[1]]
        if url == sb.API + '/posts' and method == 'POST':
            body = json.loads(data)
            assert body['scheduledTime'], 'immediate publish attempted'
            self.created += 1
            sid = f'00000000-0000-4000-8000-{self.created:012d}'
            content = body['post']['content']
            self.posts[sid] = {'postSubmissionId': sid, 'scheduledTime': body['scheduledTime'], 'status': 'scheduled'}
            self.items.append(item(content['platform'], body['scheduledTime']))
            if self.lose_create_response:
                raise ConnectionResetError('response lost after the post was created')
            return 200, json.dumps({'postSubmissionId': sid, 'scheduledTime': body['scheduledTime']}).encode()
        raise AssertionError(f'unexpected Blotato call {method} {url}')

    def writes(self):
        return [r for r in self.requests if r[0] != 'GET']


def doctrine_sections(changed=None):
    """read-doctrine answers whose pinned sections carry the pinned hashes."""
    docs: dict[str, dict] = {}
    for pin in CADENCE['pins']:
        content_hash = 'f' * 64 if pin['section_key'] == changed else pin['content_hash']
        docs.setdefault(pin['document'], {'ok': True, 'sections': []})['sections'].append(
            {'section_id': pin['section_id'], 'section_key': pin['section_key'], 'content_hash': content_hash})
    docs['content-inspiration-bank'] = {'ok': True, 'sections': [{'section_key': '4g-feedback-log', 'ordinal': 90}]}
    return docs


class SocialBatchTest(unittest.TestCase):
    def setUp(self):
        # Acceptance 7: any real socket connection fails the suite.
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network call in test'))
        guard.start()
        self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.media = root / 'media'
        self.media.mkdir()
        for name, size in {'x1': (1200, 675), 'li1a': (1080, 1350), 'li1b': (1080, 1350), 'fb2': (1080, 1080),
                           'ig1a': (1080, 1350), 'ig1b': (1080, 1350), 'ig2': (1080, 1350)}.items():
            png(self.media / f'{name}.png', *size)
        accounts = root / 'accounts.json'
        accounts.write_text(json.dumps(RECORDED['accounts']))
        self.verbs: list[tuple[str, dict]] = []
        self.docs = doctrine_sections()
        self.fake = FakeBlotato()
        self.receipt = root / 'receipt.json'
        self.ctx = sb.Context(now=FRIDAY_BEFORE_NOV_2, transport=self.fake,
                              doctrine=lambda document: self.docs[document],
                              verb=lambda verb, args: self.verbs.append((verb, args)) or {'ok': True},
                              out=root / 'out', accounts_path=accounts, receipt=str(self.receipt))

    def drafts(self, plan=None):
        text = (FIXTURES / 'drafts.json').read_text().replace('{media}', str(self.media))
        value = json.loads(text)
        value['plan_digest'] = (plan or sb.plan(self.ctx))['plan_digest']
        return value

    def draft(self, batch, draft_id):
        return next(d for d in batch['drafts'] if d['id'] == draft_id)

    def rules(self, batch):
        return {(f['draft'], f['rule']) for f in sb.check(batch, sb.plan(self.ctx), self.ctx)}

    # -- the valid batch passes -------------------------------------------------
    def test_valid_batch_has_no_findings(self):
        plan = sb.plan(self.ctx)
        self.assertEqual(plan['status'], 'ready')
        self.assertEqual(sb.check(self.drafts(plan), plan, self.ctx), [])

    def test_submit_schedules_reads_back_and_logs(self):
        result = sb.submit(self.drafts(), self.ctx)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['summary'], 'SOCIAL_BATCH_SCHEDULED n=13 verified=13')
        self.assertEqual(self.fake.created, 13)
        self.assertEqual([v for v, _ in self.verbs], ['write-doctrine-section', 'add-loop'])
        section = self.verbs[0][1]
        self.assertEqual((section['document'], section['base_version'], section['ordinal']),
                         ('content-inspiration-bank', 0, 100))
        self.assertIn('Mon 11/02 10:23 CST', self.verbs[1][1]['body'])
        artifact = json.loads(Path(str(self.receipt) + '.artifact').read_text())
        self.assertEqual((artifact['summary'], artifact['all_verified']), (result['summary'], True))
        batch = json.loads((self.ctx.out / '2026-11-02/batch.json').read_text())
        self.assertEqual(len(batch['posts']), 13)
        self.assertTrue(all(p['readback'] == 'scheduled' and p['media_verified'] for p in batch['posts']))

    # -- acceptance 1: a covered week -------------------------------------------
    def test_covered_week_has_zero_open_slots_and_submit_refuses(self):
        self.fake.items = [item(p, t) for p, t in RECORDED['covered_week_2026_11_02']]
        plan = sb.plan(self.ctx)
        self.assertEqual(plan['status'], 'week_covered')
        self.assertEqual({lane: v['open'] for lane, v in plan['lanes'].items()},
                         dict.fromkeys(CADENCE['lanes'], 0))
        self.assertIn('SOCIAL_BATCH_WEEK_COVERED', Path(str(self.receipt) + '.artifact').read_text())
        result = sb.submit(self.drafts(plan), self.ctx)
        self.assertFalse(result['ok'])
        self.assertEqual(self.fake.writes(), [])

    # -- acceptance 2: DST and the UTC edge -------------------------------------
    def test_dst_week_places_each_post_at_the_right_utc_minute(self):
        self.ctx.now = FRIDAY_BEFORE_OCT_26
        plan = sb.plan(self.ctx)
        self.assertEqual(plan['week_start'], '2026-10-26')
        self.assertEqual([d['utc_offset'] for d in plan['days']], ['-05:00'] * 6 + ['-06:00'])
        self.assertEqual(sb.local_to_utc('2026-10-31T10:53'), '2026-10-31T15:53:00Z')
        self.assertEqual(sb.local_to_utc('2026-11-01T18:39'), '2026-11-02T00:39:00Z')

    def test_utc_edge_post_counts_in_its_local_week(self):
        # Sun 11/1 18:39 CST is 00:39 UTC on Mon 11/2. Even a listing that ignores
        # the window must not count it in the week starting 11/2.
        self.fake = FakeBlotato([item(p, t) for p, t in RECORDED['previous_week_2026_10_26']], honour_window=False)
        self.ctx.transport = self.fake
        self.assertEqual(sb.plan(self.ctx)['x_total']['held'], 0)
        since = [parse_qs(urlparse(u).query)['since'][0] for m, u in self.fake.requests if '/posts?' in u]
        self.assertEqual(since[0], '2026-11-02T06:00:00Z')
        self.ctx.now = FRIDAY_BEFORE_OCT_26
        self.assertEqual(sb.plan(self.ctx)['x_total']['held'], 2)

    # -- acceptance 3 (amended by the orchestrator, #2533 review) ----------------
    def test_unreadable_listing_fails_closed(self):
        for status, body in ((500, b'{}'), (200, b'not json'), (200, b'{"posts": []}'),
                             (200, b'{"items": [{"postTime": "x"}]}')):
            with self.subTest(status=status, body=body):
                self.fake.listing_status, self.fake.listing_body = status, body
                with self.assertRaises(sb.Stop) as stop:
                    sb.plan(self.ctx)
                self.assertEqual(stop.exception.code, 'queue_unreadable')
                self.assertEqual(self.fake.writes(), [])

    def test_empty_previous_week_warns_and_uses_spec_cadence(self):
        plan = sb.plan(self.ctx)
        self.assertEqual(plan['status'], 'ready')
        self.assertTrue(any(w.startswith('control_week_empty') for w in plan['warnings']))
        self.assertEqual({lane: plan['lanes'][lane]['open'] for lane in ('linkedin', 'facebook', 'instagram')},
                         {'linkedin': 2, 'facebook': 2, 'instagram': 2})

    def test_carr_caps_come_from_spec_not_last_week(self):
        # Last week ran one LinkedIn post and no Facebook posts; the caps stay 2/2/2.
        self.fake.items = [item('linkedin', '2026-10-27T14:06:00.000Z')]
        plan = sb.plan(self.ctx)
        self.assertEqual(plan['warnings'], [])
        self.assertEqual([plan['lanes'][p]['max'] for p in ('linkedin', 'facebook', 'instagram')], [2, 2, 2])

    def test_logged_post_beyond_the_listing_is_counted(self):
        # A post this script logged for Sun 11/8 that the listing does not return.
        folder = self.ctx.out / '2026-11-02'
        folder.mkdir(parents=True)
        (folder / 'ledger.jsonl').write_text(json.dumps({
            'event': 'posted', 'draft': 'old', 'platform': 'linkedin', 'lane': 'carr', 'format': 'text',
            'visual': False, 'utc': '2026-11-09T00:39:00Z', 'submission_id': 'sid-logged'}) + '\n')
        self.fake.posts['sid-logged'] = {'postSubmissionId': 'sid-logged', 'status': 'scheduled',
                                         'scheduledTime': '2026-11-09T00:39:00.000Z'}
        plan = sb.plan(self.ctx)
        self.assertEqual(plan['lanes']['linkedin']['held'], 1)
        self.assertIn(('GET', sb.API + '/posts/sid-logged'), self.fake.requests)

    def test_unknown_x_posts_count_against_both_x_lanes(self):
        self.fake.items = [item('twitter', '2026-11-03T19:41:00.000Z')]
        lanes = sb.plan(self.ctx)['lanes']
        self.assertEqual((lanes['standalone']['open'], lanes['one_liner']['open']), (3, 6))

    # -- acceptance 4: refused drafts ---------------------------------------------
    def test_check_refuses_each_bad_draft(self):
        cases = {
            'lint_hard': ('x3', {'text': 'Compare the total cost — not the rent.'}),
            'lint_review_uncleared': ('x3', {'text': 'The leasing landscape keeps moving this year.'}),
            'ig_hashtags': ('ig2', {'text': 'Six tags. #a1 #b2 #c3 #d4 #e5 #f6'}),
            'quote_tweet': ('x3', {'text': 'Worth a read https://x.com/someone/status/1234567890'}),
            'time_outside_week': ('o1', {'local_time': '2026-10-29T07:13'}),
            'time_round_minute': ('o1', {'local_time': '2026-11-03T07:15'}),
        }
        for rule, (draft_id, change) in cases.items():
            with self.subTest(rule=rule):
                batch = self.drafts()
                self.draft(batch, draft_id).update(change)
                self.assertIn((draft_id, rule), self.rules(batch))
        # A run late on Sunday 11/1 (22:00 CST) may not schedule 07:13 Monday: under 12 hours out.
        batch = self.drafts()
        self.ctx.now = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
        self.draft(batch, 'x1')['local_time'] = '2026-11-02T07:13'
        self.assertIn(('x1', 'time_too_soon'), self.rules(batch))

    def test_check_refuses_batch_rule_breaks(self):
        batch = self.drafts()
        self.draft(batch, 'o4')['local_time'] = '2026-11-08T07:13'   # repeats o1's 07:13
        self.draft(batch, 'x3')['kind'] = 'series'
        found = {rule for _, rule in self.rules(batch)}
        self.assertTrue({'x_time_repeat', 'x_series_count'} <= found, found)
        batch = self.drafts()
        batch['drafts'] = [d for d in batch['drafts'] if d['id'] != 'o4']
        self.assertIn((None, 'x_every_day'), self.rules(batch))
        batch['run_notes']['skipped_slots'] = [{'lane': 'x_every_day', 'reason': 'no usable fuel for Sunday'},
                                               {'lane': 'one_liner', 'reason': 'thin week'}]
        self.assertNotIn((None, 'x_every_day'), self.rules(batch))

    def test_check_refuses_unknown_fields_and_stale_plan(self):
        batch = self.drafts()
        self.draft(batch, 'x3')['publish_now'] = True
        batch['plan_digest'] = 'stale'
        found = self.rules(batch)
        self.assertIn(('x3', 'schema'), found)
        self.assertIn((None, 'plan_stale'), found)

    # -- acceptance 5: spec drift ---------------------------------------------------
    def test_changed_spec_hash_stops_plan(self):
        self.docs = doctrine_sections(changed=CADENCE['pins'][0]['section_key'])
        with self.assertRaises(sb.Stop) as stop:
            sb.plan(self.ctx)
        self.assertEqual(stop.exception.code, 'spec_changed')
        self.assertEqual(self.fake.requests, [])

    # -- acceptance 6: a retry after a crash never posts twice ----------------------
    def test_retry_after_lost_create_response_adopts_the_post(self):
        batch = self.drafts()
        self.fake.lose_create_response = True
        with self.assertRaises(ConnectionResetError):
            sb.submit(batch, self.ctx)
        self.assertEqual(self.fake.created, 1)
        self.fake.lose_create_response = False
        result = sb.submit(batch, self.ctx)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.fake.created, 13)
        self.assertEqual(len(self.fake.items), 13)

    def test_retry_after_partial_submit_skips_posted_drafts(self):
        batch = self.drafts()
        real = self.fake.__call__
        calls = {'n': 0}

        def crash_on_fifth_create(method, url, headers, data):
            if method == 'POST' and url == sb.API + '/posts':
                calls['n'] += 1
                if calls['n'] == 5:
                    raise KeyboardInterrupt
            return real(method, url, headers, data)
        self.ctx.transport = crash_on_fifth_create
        with self.assertRaises(KeyboardInterrupt):
            sb.submit(batch, self.ctx)
        self.ctx.transport = self.fake
        self.assertTrue(sb.submit(batch, self.ctx)['ok'])
        self.assertEqual(self.fake.created, 13)
        self.assertEqual(sb.submit(batch, self.ctx)['ok'], True)
        self.assertEqual(self.fake.created, 13)
        self.assertEqual([v for v, _ in self.verbs], ['write-doctrine-section', 'add-loop'])

    # -- the publishing gate ------------------------------------------------------------
    def test_writes_are_only_upload_and_future_scheduled_posts(self):
        sb.submit(self.drafts(), self.ctx)
        allowed = {('POST', sb.API + '/media/uploads'), ('POST', sb.API + '/posts')}
        for method, url in self.fake.writes():
            self.assertTrue((method, url) in allowed or
                            (method == 'PUT' and url.startswith(RECORDED['media_upload']['presignedUrl'])), url)
        source = (REPO / 'tools/social_batch.py').read_text()
        for verb in ("'DELETE'", "'PATCH'", '/likes', '/follow', '/replies'):
            self.assertNotIn(verb, source)

    def test_key_header_goes_only_to_blotato(self):
        seen = []

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{}'

        def opener(request, timeout):
            seen.append((request.full_url, request.get_header('Blotato-api-key')))
            return Response()
        send = sb.http_transport(key=lambda: 'synthetic-key', opener=opener)
        send('GET', sb.API + '/posts/x', {}, None)
        send('PUT', RECORDED['media_upload']['presignedUrl'] + 'a.png', {}, b'1')
        self.assertEqual(seen, [(sb.API + '/posts/x', 'synthetic-key'),
                                (RECORDED['media_upload']['presignedUrl'] + 'a.png', None)])

    # -- acceptance 8-10: schedule, prompt and activation gate -----------------------
    def test_schedule_is_friday_six_central_and_definition_only(self):
        manifest = json.loads((REPO / 'ops/headless-tasks/tasks.json').read_text())
        task = manifest['tasks']['social-batch-headless']
        self.assertEqual((manifest['timezone'], task['cron'], task['model']), ('America/Chicago', '0 6 * * 5', 'opus'))
        self.assertTrue({'WebFetch(domain:backend.blotato.com)', 'Read(~/.zprofile)'} <= set(task['disallowed_tools']))
        label = 'com.carr.headless.social-batch-headless'
        template = plistlib.loads((REPO / f'ops/headless-tasks/{label}.plist').read_bytes())
        self.assertEqual(template['Label'], label)
        self.assertEqual(template['ProgramArguments'], ['{{REPO}}/bin/headless-task', 'social-batch-headless'])
        self.assertEqual(template['StartCalendarInterval'], calendar_entries(task['cron']))
        self.assertIn(f'{label}.plist', launchd_hold.DEFINITION_ONLY)

    def test_task_scope_cannot_reach_the_guard_or_other_writers(self):
        """The unattended model may write only its drafts and cards, and run only submit's front door."""
        task = json.loads((REPO / 'ops/headless-tasks/tasks.json').read_text())['tasks']['social-batch-headless']
        allowed, denied = task['allowed_tools'], task['disallowed_tools']
        rule = re.compile(r'^(\w+)(?:\((.*)\))?$')
        bash = [rule.match(t).group(2) for t in allowed if rule.match(t).group(1) == 'Bash']
        files = [rule.match(t) for t in allowed if rule.match(t).group(1) in ('Write', 'Edit', 'NotebookEdit', 'MultiEdit')]
        self.assertTrue(all(m.group(2) for m in files), 'an unscoped Write or Edit reaches every path')
        self.assertTrue(all(rule.match(t).group(2) for t in allowed if rule.match(t).group(1) == 'Bash'))

        def reaches(patterns, value):
            return any(fnmatch.fnmatchcase(value, p) for p in patterns)
        for command in ('./run.sh outlook-send --to x', './run.sh migrate --apply', './run.sh call add-loop {}',
                        './run.sh call write-doctrine-section {}', './run.sh config install --apply',
                        'curl -q https://backend.blotato.com/v2/posts', 'grok -p hi',
                        'ffmpeg -y -i a.gif tools/social_batch.py', 'python3 tools/social_batch.py submit d.json'):
            self.assertFalse(reaches(bash, command), command)
        for command in ('./run.sh social-batch plan', './run.sh call read-doctrine {"document":"x-posting-spec"}'):
            self.assertTrue(reaches(bash, command), command)
        file_patterns = [m.group(2) for m in files]
        guarded = ('tools/social_batch.py', 'ops/social/cadence.v1.json', 'ops/headless-tasks/tasks.json',
                   'lib/headless_tasks.py', 'bin/headless-task', 'run.sh', 'tools/writing-lint.py',
                   'out/social-batch/2026-11-02/ledger.jsonl', 'out/social-batch/2026-11-02/batch.json',
                   'claude-tree/skills/social-media-manager/config/blotato-accounts.json')
        for path in guarded:
            self.assertFalse(reaches(file_patterns, path), path)
        for path in ('out/social-batch/2026-11-02/work/drafts.json', 'out/social-batch/2026-11-02/work/cards/x1.png'):
            self.assertTrue(reaches(file_patterns, path), path)
        for entry in ('Edit(tools/**)', 'Edit(lib/**)', 'Edit(ops/**)', 'Edit(bin/**)', 'Edit(run.sh)',
                      'Edit(out/social-batch/*/ledger.jsonl)', 'Edit(out/social-batch/*/batch.json)'):
            self.assertIn(entry, denied)

    def test_render_writes_only_inside_a_work_folder(self):
        html = self.media / 'card.html'
        html.write_text('<p>x</p>')
        for target in (REPO / 'tools/social_batch.png', self.ctx.out / '2026-11-02/ledger.png',
                       self.ctx.out / '2026-11-02/work/../batch.png'):
            with self.subTest(target=target), self.assertRaises(sb.Stop):
                sb.render(html, target, 10, 10, chrome='/nonexistent/chrome', out=self.ctx.out)

    def test_render_creates_the_work_folder_and_checks_the_size(self):
        html = self.media / 'card.html'
        html.write_text('<p>x</p>')
        chrome = self.media / 'chrome'
        # Like Chrome, the fake writes the screenshot and fails when the folder is missing.
        chrome.write_text('#!/usr/bin/env python3\nimport struct, sys, zlib\n'
                          'path = next(a for a in sys.argv if a.startswith("--screenshot=")).split("=", 1)[1]\n'
                          f'sys.path.insert(0, {str(REPO / "tools")!r})\n'
                          'exec(open(sys.argv[0] + ".png.py").read())\npng(path, 12, 8)\n')
        (self.media / 'chrome.png.py').write_text(
            'from pathlib import Path\nimport struct, zlib\n'
            'def png(path, w, h):\n'
            '    def chunk(k, d): return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d))\n'
            '    raw = b"".join(b"\\x00" + b"\\x00\\x00\\x00" * w for _ in range(h))\n'
            '    Path(path).open("xb").write(b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))'
            ' + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))\n')
        chrome.chmod(0o755)
        target = self.ctx.out / '2026-11-02/work/cards/x1.png'
        result = sb.render(html, target, 12, 8, chrome=str(chrome), out=self.ctx.out)
        self.assertEqual((result['width'], result['height']), (12, 8))
        with self.assertRaises(sb.Stop):
            sb.render(html, self.ctx.out / '2026-11-02/work/x2.png', 99, 8, chrome=str(chrome), out=self.ctx.out)

    def test_submit_rechecks_lead_time_after_slow_uploads(self):
        # Uploads ran so long that x1 (Mon 10:23 CST) is now under 12 hours away.
        batch, real = self.drafts(), self.fake

        def slow(method, url, headers, data):
            if method == 'PUT':
                self.ctx.now = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
            return real(method, url, headers, data)
        self.ctx.transport = slow
        with self.assertRaises(sb.Stop) as stop:
            sb.submit(batch, self.ctx)
        self.assertEqual(stop.exception.code, 'submit_failed')
        self.assertNotIn(('POST', sb.API + '/posts'), real.requests)

    def test_a_logged_post_joe_moved_is_counted_once(self):
        folder = self.ctx.out / '2026-11-02'
        folder.mkdir(parents=True)
        (folder / 'ledger.jsonl').write_text(json.dumps({
            'event': 'posted', 'draft': 'old', 'platform': 'linkedin', 'lane': 'carr', 'format': 'text',
            'visual': False, 'utc': '2026-11-03T15:06:00Z', 'submission_id': 'sid-moved'}) + '\n')
        self.fake.posts['sid-moved'] = {'postSubmissionId': 'sid-moved', 'status': 'scheduled',
                                        'scheduledTime': '2026-11-04T15:11:00.000Z'}
        self.fake.items = [item('linkedin', '2026-11-04T15:11:00.000Z')]
        self.assertEqual(sb.plan(self.ctx)['lanes']['linkedin']['held'], 1)

    def test_prompt_is_store_first_and_carries_a_typed_contract(self):
        from lib.acceptance_checks import contract
        text = (REPO / 'ops/scheduled-tasks/social-batch-headless.SKILL.md').read_text()
        self.assertIn('This definition is disabled', text)
        acceptance = contract(text)
        self.assertEqual({c['kind'] for c in acceptance['criteria']}, {'contains', 'json_equals'})
        self.assertEqual(acceptance['noop_criteria'][0]['text'], 'SOCIAL_BATCH_WEEK_COVERED')
        # STORE-FIRST: every Drive path the prompt names must be one the tracked doctrine
        # inventory keeps off the store by design (ARCHIVE_MARKERS); anything else has a store copy.
        spec = importlib.util.spec_from_file_location('doctrine_inventory', REPO / 'pipelines/doctrine_inventory.py')
        inventory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inventory)
        vault = re.findall(r'\{\{VAULT\}\}/([^"\n]+?\.md)', text)
        self.assertTrue(vault)
        for path in vault:
            self.assertTrue(any(marker in path for marker in inventory.ARCHIVE_MARKERS), f'{path} has a store copy')
        for drive in ('GoogleDrive', 'CloudStorage', 'My Drive', 'CARR AI/'):
            self.assertNotIn(drive, text)
        self.assertEqual(text.count('{{VAULT}}'), len(vault), 'every vault reference must be a .md path checked above')
        for retired in ('x-batch-', 'grok.com', 'INDEX.md', 'Chrome Grok'):
            self.assertNotIn(retired, text)
        self.assertLessEqual(len(json.loads(text.strip()[8:-3])['instructions']), 40)


if __name__ == '__main__':
    unittest.main()
