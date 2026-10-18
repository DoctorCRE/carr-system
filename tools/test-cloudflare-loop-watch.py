"""Loop watch: the local, once-a-minute E2E dead-loop killer.

Every test runs against a fake process table, a fake RunBudget ledger and a
fake signal sender. Nothing here signals a real process, reads the real
process table, or reaches the record layer.
"""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    'cloudflare_loop_watch', Path(__file__).with_name('cloudflare_loop_watch.py'))
assert SPEC is not None and SPEC.loader is not None
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
SMOKE = 'node /Users/booko/doctorcre-app/scripts/e2e-staging/home-smoke.mjs'
SELF_PID, PARENT_PID = 4000, 3999


def settings(**overrides):
    base = {
        'run_budget_globs': [],
        'ledger_deadline_grace_seconds': 10,
        'respawn_max_starts': 5,
        'respawn_window_seconds': 600,
        'time_bound_grace_seconds': 60,
        'sigkill_after_seconds': 10,
        'self_alarm_seconds': 10,
        'max_kills_per_run': 3,
        'max_kills_per_hour': 10,
        'breaker_consecutive_errors': 5,
        'receipt_rotate_bytes': 1_048_576,
        'lock_stale_seconds': 30,
        'report_timeout_seconds': 10,
        'report_max_per_run': 5,
        'outbox_stale_seconds': 900,
        'defect_episode_hours': 24,
        'watched': [
            {'name': 'e2e-home-smoke', 'max_seconds': 120,
             'pattern': r'^\S*node\s+(\S+\s+)*\S*scripts/e2e-staging/home-smoke\.mjs(\s|$)'},
        ],
    }
    base.update(overrides)
    return watch.validate_settings(base)


def ps_line(pid, command, elapsed_seconds, ppid=1):
    minutes, seconds = divmod(int(elapsed_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    etime = f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'
    return f'{pid:>6} {ppid:>6} {etime:>11} {command}'


class Signals:
    """Records signals instead of sending them; `alive` is the fake kernel's view."""

    def __init__(self, alive=()):
        self.sent = []
        self.alive = set(alive)

    def kill(self, pid, sig):
        self.sent.append((pid, sig))

    def is_alive(self, pid):
        return pid in self.alive


class WatchCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / 'state'
        self.spawned = []

    def tearDown(self):
        self._tmp.cleanup()

    def ledger(self, name='app', *, pid=5000, spent_http=10, limit_http=200, created_ago=30, deadline_s=120,
               stopped=None, schema='e2e-run-budget.v1', lock=True):
        directory = self.root / name / '.e2e' / 'run-budget'
        directory.mkdir(parents=True)
        created = int((NOW.timestamp() - created_ago) * 1000)
        state = {'schema': schema, 'run_id': '1b4e28ba-2fa1-41d2-883f-0016d3cca427',
                 'profile': {'name': 'home-smoke.v1', 'deadlineMs': deadline_s * 1000, 'http': limit_http},
                 'profile_sha256': 'x', 'created_at': created, 'deadline_at': created + deadline_s * 1000,
                 'spent': {'http': spent_http}, 'stopped': stopped}
        (directory / 'state.json').write_text(json.dumps(state))
        if lock:
            (directory / 'lock').write_text(str(pid))
        return directory

    def scan(self, ps, signals=None, cfg=None, now=NOW, globs=None):
        cfg = cfg or settings(run_budget_globs=globs if globs is not None else [str(self.root / '*/.e2e/run-budget')])
        signals = signals or Signals()
        result = watch.scan(cfg, state_dir=self.state, now=now, ps_text='\n'.join(ps), kill=signals.kill,
                            is_alive=signals.is_alive, self_pid=SELF_PID, parent_pid=PARENT_PID,
                            spawn_reporter=lambda: self.spawned.append(1))
        return result, signals

    def outbox(self):
        folder = self.state / 'outbox'
        return sorted(folder.glob('*.json')) if folder.exists() else []


class KillRules(WatchCase):
    def test_over_budget_run_with_a_live_process_is_killed(self):
        self.ledger(spent_http=200, stopped={'reason': 'http-exhausted', 'at': int(NOW.timestamp() * 1000)})
        result, signals = self.scan([ps_line(5000, SMOKE, 30)])
        self.assertEqual(signals.sent, [(5000, signal.SIGTERM)])
        self.assertEqual(len(result['lines']), 1)
        self.assertIn('over-budget', result['lines'][0])

    def test_inside_budget_does_nothing_and_writes_nothing_but_the_heartbeat(self):
        self.ledger(spent_http=50)
        result, signals = self.scan([ps_line(5000, SMOKE, 30)])
        self.assertEqual(signals.sent, [])
        self.assertEqual(result['lines'], [])
        self.assertFalse((self.state / 'loop-watch-receipts.jsonl').exists())
        self.assertEqual(self.outbox(), [])

    def test_deadline_passed_while_alive_is_killed(self):
        self.ledger(created_ago=200, deadline_s=120)
        _, signals = self.scan([ps_line(5000, SMOKE, 100)])
        self.assertEqual(signals.sent, [(5000, signal.SIGTERM)])

    def test_absent_ledger_does_nothing(self):
        result, signals = self.scan([ps_line(5000, SMOKE, 30)], globs=[str(self.root / 'missing/*')])
        self.assertEqual((signals.sent, result['lines']), ([], []))

    def test_unwatched_process_is_never_touched_even_over_every_threshold(self):
        self.ledger(spent_http=200, created_ago=10_000, stopped={'reason': 'http-exhausted', 'at': 1})
        ps = [ps_line(5000, 'python3 some_other_tool.py', 99_999)]
        for minute in range(8):  # respawning, over budget, past deadline and over the time bound
            ps.append(ps_line(5001 + minute, 'python3 some_other_tool.py', 5))
        signals = Signals()
        for minute in range(8):
            self.scan([ps_line(6000 + minute, 'python3 some_other_tool.py', 5), ps[0]], signals=signals,
                      now=NOW + timedelta(minutes=minute))
        self.assertEqual(signals.sent, [])

    def test_agent_sessions_are_never_touched_even_if_a_pattern_would_match(self):
        cfg = settings(run_budget_globs=[], watched=[{'name': 'broad', 'max_seconds': 1, 'pattern': r'home-smoke'}])
        ps = [ps_line(5000, 'claude --resume home-smoke', 9_999),
              ps_line(5001, '/opt/homebrew/bin/codex ' + 'exec home-smoke', 9_999),
              ps_line(SELF_PID, 'python3 x home-smoke', 9_999),
              ps_line(PARENT_PID, '/bin/zsh home-smoke', 9_999),
              ps_line(1, '/sbin/launchd home-smoke', 9_999),
              ps_line(5002, 'python3 tools/cloudflare_spend_guard.py fast home-smoke', 9_999)]
        _, signals = self.scan(ps, cfg=cfg)
        self.assertEqual(signals.sent, [])

    def test_config_refuses_a_pattern_that_matches_an_agent_session(self):
        with self.assertRaises(ValueError):
            settings(watched=[{'name': 'bad', 'max_seconds': 10, 'pattern': r'.*'}])

    def test_respawn_kills_at_six_starts_in_ten_minutes_and_not_at_five(self):
        signals = Signals()
        for start in range(5):
            self.scan([ps_line(7000 + start, SMOKE, 5)], signals=signals, now=NOW + timedelta(minutes=start))
        self.assertEqual(signals.sent, [])
        self.scan([ps_line(7005, SMOKE, 5)], signals=signals, now=NOW + timedelta(minutes=5))
        self.assertEqual(signals.sent, [(7005, signal.SIGTERM)])

    def test_time_bound_plus_grace(self):
        _, signals = self.scan([ps_line(5000, SMOKE, 180)], globs=[])
        self.assertEqual(signals.sent, [])
        _, signals = self.scan([ps_line(5001, SMOKE, 181)], globs=[])
        self.assertEqual(signals.sent, [(5001, signal.SIGTERM)])

    def test_sigkill_follows_on_a_later_run_only_for_the_same_still_watched_process(self):
        signals = Signals(alive={5001})
        self.scan([ps_line(5001, SMOKE, 181)], signals=signals, globs=[])
        self.scan([ps_line(5001, SMOKE, 241)], signals=signals, globs=[], now=NOW + timedelta(seconds=60))
        self.assertEqual(signals.sent, [(5001, signal.SIGTERM), (5001, signal.SIGKILL)])
        reused = Signals(alive={5002})
        self.scan([ps_line(5002, SMOKE, 181)], signals=reused, globs=[])
        # the pid now belongs to a different, younger process: never SIGKILL it
        self.scan([ps_line(5002, 'node other.mjs', 3)], signals=reused, globs=[], now=NOW + timedelta(seconds=60))
        self.assertEqual(reused.sent, [(5002, signal.SIGTERM)])

    def test_scan_has_no_sleep_and_no_network(self):
        source = Path(watch.__file__).read_text()
        self.assertNotIn('time.sleep', source)
        self.assertNotIn('urllib', source)
        self.assertNotIn('socket', source)


class LedgerIsMarkedKilled(WatchCase):
    def test_killed_run_is_marked_in_the_ledger_and_blocked_from_restart(self):
        directory = self.ledger(created_ago=200)
        self.scan([ps_line(5000, SMOKE, 100)])
        state = json.loads((directory / 'state.json').read_text())
        self.assertEqual(state['stopped']['reason'], 'killed-by-loop-watch')
        self.assertEqual(state['killed']['rule'], 'deadline')
        stop = json.loads((directory / 'STOP').read_text())
        self.assertIn('KILLED by loop-watch', stop['reason'])

    def test_a_run_that_already_stopped_keeps_its_own_reason(self):
        directory = self.ledger(spent_http=200, stopped={'reason': 'http-exhausted', 'at': 5})
        self.scan([ps_line(5000, SMOKE, 30)])
        state = json.loads((directory / 'state.json').read_text())
        self.assertEqual(state['stopped'], {'reason': 'http-exhausted', 'at': 5})
        self.assertEqual(state['killed']['rule'], 'over-budget')


class Reporting(WatchCase):
    def test_a_kill_files_exactly_one_defect_with_the_evidence(self):
        self.ledger(created_ago=200)
        self.scan([ps_line(5000, SMOKE, 100)])
        items = self.outbox()
        self.assertEqual(len(items), 1)
        self.assertEqual(self.spawned, [1])
        item = json.loads(items[0].read_text())
        self.assertEqual(item['verb'], 'record-defect')
        args = item['args']
        for field in ('idempotency_key', 'defect_class', 'claimed', 'actual', 'detected_by'):
            self.assertTrue(args[field], field)
        self.assertNotEqual(args['claimed'], args['actual'])
        evidence = args['cost_note']
        for part in (SMOKE, 'pid 5000', 'deadline', 'run_id', 'SIGTERM'):
            self.assertIn(part, evidence + args['actual'])

    def test_a_repeated_kill_of_the_same_runner_updates_the_count_not_the_defect_list(self):
        for minute in range(3):
            self.scan([ps_line(5000 + minute, SMOKE, 181)], globs=[], now=NOW + timedelta(minutes=minute))
        self.assertEqual(len(self.outbox()), 1)
        defects = watch.read_state(self.state)['defects']
        self.assertEqual([d['count'] for d in defects.values()], [3])

    def test_record_layer_down_keeps_the_report_in_the_outbox_and_the_killer_finishes_fast(self):
        self.ledger(created_ago=200)
        started = time.process_time(), time.monotonic()
        cfg = settings(run_budget_globs=[str(self.root / '*/.e2e/run-budget')])
        slow = [sys.executable, '-c', 'import time; time.sleep(30)']
        watch.scan(cfg, state_dir=self.state, now=NOW, ps_text=ps_line(5000, SMOKE, 100), kill=Signals().kill,
                   is_alive=lambda pid: False, self_pid=SELF_PID, parent_pid=PARENT_PID,
                   spawn_reporter=lambda: watch.spawn_reporter(command=slow))
        self.assertLess(time.monotonic() - started[1], 1.0)
        self.assertLess(time.process_time() - started[0], 1.0)
        self.assertEqual(len(self.outbox()), 1)
        delivered = watch.deliver_outbox(cfg, state_dir=self.state, now=NOW, call_verb=lambda verb, args, timeout: False)
        self.assertEqual(delivered, 0)
        self.assertEqual(len(self.outbox()), 1)

    def test_outbox_drains_oldest_first_at_most_five_per_run_once_the_record_layer_is_back(self):
        cfg = settings()
        for n in range(7):
            watch.queue_report(self.state, f'key-{n}', {'claimed': 'a', 'actual': 'b'},
                               now=NOW + timedelta(seconds=n))
        sent = []
        delivered = watch.deliver_outbox(cfg, state_dir=self.state, now=NOW + timedelta(minutes=1),
                                         call_verb=lambda verb, args, timeout: sent.append(args) or True)
        self.assertEqual(delivered, 5)
        self.assertEqual([a['session_key'] for a in sent], [f'loop-watch:key-{n}' for n in range(5)])
        self.assertEqual(len(self.outbox()), 2)

    def test_a_stale_outbox_item_is_reported_once_delivery_works_and_printed_until_then(self):
        cfg = settings()
        watch.queue_report(self.state, 'old', {'claimed': 'a', 'actual': 'b'}, now=NOW - timedelta(minutes=20))
        result, _ = self.scan([], globs=[])
        self.assertTrue(any('undelivered' in line for line in result['errors']))
        watch.deliver_outbox(cfg, state_dir=self.state, now=NOW, call_verb=lambda verb, args, timeout: True)
        remaining = [json.loads(p.read_text()) for p in self.outbox()]
        self.assertEqual([r['args']['defect_class'] for r in remaining], ['loop-watch-report-delayed'])

    def test_kill_cap_stops_killing_alerts_once_and_files_a_defect(self):
        ps = [ps_line(5000 + n, SMOKE, 181) for n in range(5)]
        result, signals = self.scan(ps, globs=[])
        self.assertEqual(len(signals.sent), 3)
        self.assertEqual(sum('kill cap' in line for line in result['lines']), 1)
        classes = [json.loads(p.read_text())['args']['defect_class'] for p in self.outbox()]
        self.assertIn('loop-watch-kill-cap-reached', classes)

    def test_hourly_kill_cap(self):
        signals = Signals()
        for minute in range(5):
            ps = [ps_line(6000 + minute * 10 + n, SMOKE, 181) for n in range(3)]
            self.scan(ps, signals=signals, globs=[], now=NOW + timedelta(minutes=minute))
        self.assertEqual(len([s for s in signals.sent if s[1] == signal.SIGTERM]), 10)

    def test_breaker_trips_after_five_errors_writes_the_off_file_and_files_one_defect(self):
        cfg = settings(run_budget_globs=[])
        for n in range(6):
            code = watch.run_once(cfg, state_dir=self.state, now=NOW + timedelta(minutes=n),
                                  ps_reader=lambda: (_ for _ in ()).throw(RuntimeError('ps broke')),
                                  kill=Signals().kill, is_alive=lambda pid: False,
                                  spawn_reporter=lambda: None, alarm=False)
            self.assertEqual(code, 0 if n == 5 else 3)
        self.assertTrue((self.state / 'loop-watch.off').exists())
        classes = [json.loads(p.read_text())['args']['defect_class'] for p in self.outbox()]
        self.assertEqual(classes.count('loop-watch-breaker-tripped'), 1)

    def test_identical_errors_collapse_to_one_line_with_a_count(self):
        cfg = settings(run_budget_globs=[])
        printed = []
        for n in range(3):
            watch.run_once(cfg, state_dir=self.state, now=NOW + timedelta(minutes=n),
                           ps_reader=lambda: (_ for _ in ()).throw(RuntimeError('ps broke')),
                           kill=Signals().kill, is_alive=lambda pid: False, spawn_reporter=lambda: None,
                           alarm=False, err=printed.append)
        self.assertEqual(len([p for p in printed if 'RuntimeError' in p]), 1)
        self.assertEqual(watch.read_state(self.state)['last_error_repeats'], 2)

    def test_receipt_file_rotates_at_the_configured_size(self):
        self.state.mkdir(parents=True)
        (self.state / 'loop-watch-receipts.jsonl').write_text('x' * 2000)
        cfg = settings(receipt_rotate_bytes=1000, run_budget_globs=[])
        watch.scan(cfg, state_dir=self.state, now=NOW, ps_text=ps_line(5001, SMOKE, 181), kill=Signals().kill,
                   is_alive=lambda pid: False, self_pid=SELF_PID, parent_pid=PARENT_PID, spawn_reporter=lambda: None)
        self.assertEqual((self.state / 'loop-watch-receipts.jsonl.1').read_text(), 'x' * 2000)
        self.assertLess((self.state / 'loop-watch-receipts.jsonl').stat().st_size, 1000)

    def test_heartbeat_is_written_every_run(self):
        self.scan([], globs=[])
        beat = watch.read_heartbeat(self.state)
        self.assertEqual(beat, NOW)


class NeverOverlaps(WatchCase):
    def test_a_second_run_exits_silently_while_the_first_holds_the_lock(self):
        cfg = settings(run_budget_globs=[])
        self.state.mkdir(parents=True)
        holder = subprocess.Popen([sys.executable, '-c',
                                   'import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT);'
                                   'fcntl.flock(fd,fcntl.LOCK_EX);os.write(fd,b"%d %d"%(os.getpid(),int(sys.argv[2])));'
                                   'print("held",flush=True);time.sleep(20)',
                                   str(self.state / 'loop-watch.lock'), str(int(NOW.timestamp()))],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'held')
            printed = []
            code = watch.run_once(cfg, state_dir=self.state, now=NOW + timedelta(seconds=5),
                                  ps_reader=lambda: '', kill=Signals().kill, is_alive=lambda pid: False,
                                  spawn_reporter=lambda: None, alarm=False, err=printed.append, out=printed.append)
            self.assertEqual((code, printed), (0, []))
            self.assertEqual(self.outbox(), [])
            # held far longer than any run can live: that is a loud self-failure
            code = watch.run_once(cfg, state_dir=self.state, now=NOW + timedelta(seconds=60),
                                  ps_reader=lambda: '', kill=Signals().kill, is_alive=lambda pid: False,
                                  spawn_reporter=lambda: None, alarm=False, err=printed.append, out=printed.append)
            classes = [json.loads(p.read_text())['args']['defect_class'] for p in self.outbox()]
            self.assertEqual(classes, ['loop-watch-lock-held'])
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

    def test_off_file_stops_the_run_before_anything_else(self):
        cfg = settings(run_budget_globs=[])
        self.state.mkdir(parents=True)
        (self.state / 'loop-watch.off').write_text('tripped')
        called = []
        code = watch.run_once(cfg, state_dir=self.state, now=NOW, ps_reader=lambda: called.append(1) or '',
                              kill=Signals().kill, is_alive=lambda pid: False, spawn_reporter=lambda: None, alarm=False)
        self.assertEqual((code, called), (0, []))

    def test_self_alarm_ends_a_stuck_run(self):
        script = ('import importlib.util,sys,time;s=importlib.util.spec_from_file_location("w",sys.argv[1]);'
                  'w=importlib.util.module_from_spec(s);s.loader.exec_module(w);w.arm_self_alarm(1);time.sleep(30)')
        started = time.monotonic()
        proc = subprocess.run([sys.executable, '-c', script, watch.__file__], capture_output=True, timeout=20)
        self.assertLess(time.monotonic() - started, 10)
        self.assertNotEqual(proc.returncode, 0)


class Plist(unittest.TestCase):
    def test_plist_runs_once_per_minute_never_respawns_and_is_not_installed(self):
        import plistlib
        path = Path(__file__).resolve().parents[1] / 'tools' / 'cloudflare-spend-guard' / 'launchd' / \
            'com.carr.cloudflare-loop-watch.plist'
        text = path.read_text().replace('{{REPO}}', '/repo')
        plist = plistlib.loads(text.encode())
        self.assertNotIn('KeepAlive', plist)
        self.assertIs(plist.get('RunAtLoad', False), False)
        self.assertEqual(len(plist['StartCalendarInterval']), 60)
        self.assertEqual(plist['ProgramArguments'][1:], ['/repo/tools/cloudflare_loop_watch.py', 'scan'])
        installed = Path.home() / 'Library' / 'LaunchAgents' / 'com.carr.cloudflare-loop-watch.plist'
        self.assertFalse(installed.exists())


if __name__ == '__main__':
    unittest.main()
