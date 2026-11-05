#!/usr/bin/env python3
"""Test durable Studio claim delivery without production or installed jobs."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.studio_evidence_outbox import Capture, Outbox, Acknowledgement, Refused


class OutboxTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'claims.sqlite3'
        self.now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        self.capture = Capture(str(uuid4()), str(uuid4()), 'studio-host', 'studio', 'a'*40,
                               'b'*64, 'c'*64, 'd'*64, 'e'*64,
                               self.now-timedelta(minutes=10), self.now-timedelta(minutes=9),
                               self.now-timedelta(minutes=1), self.now)

    def ack(self, capture, digest, *, timeout):
        self.assertEqual(timeout, 5)
        return Acknowledgement(capture.run_id, digest, str(uuid4()))

    def test_enqueue_reopen_acknowledge_and_duplicate(self):
        box = Outbox(self.path)
        self.assertEqual(box.enqueue(self.capture), 'pending')
        self.assertEqual(Outbox(self.path).enqueue(self.capture), 'pending')
        self.assertEqual(box.flush(self.ack), {'acknowledged': 1, 'refused': 0, 'pending': 0, 'paused': False})
        self.assertEqual(Outbox(self.path).enqueue(self.capture), 'acknowledged')
        box.flush(lambda *a, **k: self.fail('acknowledged row replayed'))

    def test_changed_execution_claim_cannot_replace_original(self):
        box = Outbox(self.path)
        box.enqueue(self.capture)
        with self.assertRaises(ValueError):
            box.enqueue(replace(self.capture, runtime_sha='f'*40))
        box.flush(lambda claim, digest, **kw: self.ack(self.capture, digest, **kw))

    def test_refusal_is_retained_and_does_not_starve_next_claim(self):
        box = Outbox(self.path)
        box.enqueue(self.capture)
        next_claim = replace(self.capture, run_id=str(uuid4()))
        box.enqueue(next_claim)
        def send(claim, digest, **kwargs):
            if claim.run_id == self.capture.run_id:
                raise Refused('host_mismatch')
            return self.ack(claim, digest, **kwargs)
        self.assertEqual(box.flush(send)['refused'], 1)
        self.assertEqual(box.enqueue(self.capture), 'refused')
        self.assertEqual(box.enqueue(next_claim), 'acknowledged')

    def test_offline_recovery_persistent_breaker_and_explicit_reset(self):
        Outbox(self.path).enqueue(self.capture)
        def offline(*args, **kwargs):
            raise TimeoutError('offline')
        self.assertFalse(Outbox(self.path).flush(offline)['paused'])
        self.assertTrue(Outbox(self.path).flush(offline)['paused'])
        self.assertTrue(Outbox(self.path).flush(lambda *a, **kw: self.fail('breaker ignored'))['paused'])
        Outbox(self.path).resume()
        self.assertEqual(Outbox(self.path).flush(self.ack)['acknowledged'], 1)

    def test_ambiguous_remote_acceptance_replays_identical_identity(self):
        box = Outbox(self.path)
        box.enqueue(self.capture)
        seen = []
        def send(claim, digest, **kwargs):
            seen.append((claim.run_id, digest))
            if len(seen) == 1:
                raise ConnectionError('lost acknowledgement')
            return self.ack(claim, digest, **kwargs)
        box.flush(send)
        box.flush(send)
        self.assertEqual(seen[0], seen[1])

    def test_crash_before_commit_replays_same_key(self):
        box = Outbox(self.path)
        box.enqueue(self.capture)
        seen = []
        def crash(claim, digest, **kwargs):
            seen.append((claim.run_id, digest))
            raise RuntimeError('process stopped after remote acceptance')
        with self.assertRaises(RuntimeError):
            box.flush(crash)
        def recovered(claim, digest, **kwargs):
            self.assertEqual(seen[0], (claim.run_id, digest))
            return self.ack(claim, digest, **kwargs)
        self.assertEqual(Outbox(self.path).flush(recovered)['acknowledged'], 1)

    def test_mismatched_or_forged_acknowledgement_never_delivers(self):
        for change in ['run_id', 'digest', 'receipt_id', 'json']:
            with self.subTest(change=change):
                path = self.path.with_name(change+'.db')
                box = Outbox(path)
                box.enqueue(self.capture)
                def bad(claim, digest, **kwargs):
                    ack = self.ack(claim, digest, **kwargs)
                    return {'verified': True} if change == 'json' else replace(ack, **{change: 'forged'})
                result = box.flush(bad)
                self.assertTrue(result['paused'])
                self.assertEqual(result['pending'], 1)

    def test_invalid_claims_refuse_staging(self):
        for change in [dict(target='other'), dict(host_id=''), dict(runtime_sha='a'*9),
                       dict(run_id='fake'), dict(command_sha256='z'*64), dict(helper_sha256=None),
                       dict(install_ended_at=self.now+timedelta(seconds=1)),
                       dict(install_started_at=self.now), dict(ended_at=self.now-timedelta(hours=1)),
                       dict(started_at=self.now.replace(tzinfo=None))]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Outbox(self.path).enqueue(replace(self.capture, **change))

    def test_bounded_capacity_and_batch(self):
        box = Outbox(self.path)
        for _ in range(1000):
            box.enqueue(replace(self.capture, run_id=str(uuid4())))
        with self.assertRaises(OverflowError):
            box.enqueue(self.capture)
        result = box.flush(self.ack)
        self.assertEqual(result['acknowledged'], 20)
        self.assertEqual(result['pending'], 980)

    def test_enqueue_during_slow_flush(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        box = Outbox(self.path)
        box.enqueue(self.capture)
        started, release = Event(), Event()
        def slow(claim, digest, **kwargs):
            started.set()
            self.assertTrue(release.wait(15))
            return self.ack(claim, digest, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            flushing = pool.submit(box.flush, slow)
            self.assertTrue(started.wait(5))
            producer = pool.submit(box.enqueue, replace(self.capture, run_id=str(uuid4())))
            try:
                self.assertEqual(producer.result(timeout=12), 'pending')
            finally:
                release.set()
            flushing.result()
        self.assertEqual(box.flush(self.ack)['acknowledged'], 2)

    def test_concurrent_flushers_do_not_duplicate_delivery(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        box = Outbox(self.path)
        box.enqueue(self.capture)
        started, release = Event(), Event()
        sent = []
        def send(claim, digest, **kwargs):
            sent.append(claim.run_id)
            started.set()
            self.assertTrue(release.wait(5))
            return self.ack(claim, digest, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(box.flush, send)
            self.assertTrue(started.wait(5))
            second = pool.submit(box.flush, send)
            release.set()
            first.result()
            second.result()
        self.assertEqual(sent, [self.capture.run_id])

    def test_capacity_includes_terminal_rows(self):
        box = Outbox(self.path)
        for _ in range(1000):
            box.enqueue(replace(self.capture, run_id=str(uuid4())))
        box.flush(self.ack)
        def refuse(*args, **kwargs):
            raise Refused()
        box.flush(refuse)
        with self.assertRaises(OverflowError):
            box.enqueue(self.capture)

    def test_success_resets_transport_failures(self):
        box = Outbox(self.path)
        box.enqueue(self.capture)
        def offline(*args, **kwargs):
            raise TimeoutError()
        box.flush(offline)
        box.flush(self.ack)
        box.enqueue(replace(self.capture, run_id=str(uuid4())))
        self.assertFalse(box.flush(offline)['paused'])
        self.assertEqual(box.flush(self.ack)['acknowledged'], 2)

    def test_concurrent_identical_producers_converge(self):
        from concurrent.futures import ThreadPoolExecutor
        Outbox(self.path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            states = list(pool.map(lambda _: Outbox(self.path).enqueue(self.capture), range(8)))
        self.assertEqual(set(states), {'pending'})
        self.assertEqual(Outbox(self.path).flush(self.ack)['acknowledged'], 1)


if __name__ == '__main__':
    unittest.main()
