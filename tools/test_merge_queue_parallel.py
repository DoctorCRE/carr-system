"""Speculative validation through the daemon and real Git repositories."""
import sys
from pathlib import Path
import unittest
import time
import json
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_merge_queue as fixture
module = fixture.module


class ParallelTests(unittest.TestCase):
    git = fixture.QueueTests.git
    save = fixture.QueueTests.save
    load = fixture.QueueTests.load
    calls = fixture.QueueTests.calls
    bind_work_queue = fixture.QueueTests.bind_work_queue
    restart = fixture.QueueTests.restart

    def setUp(self):
        fixture.QueueTests.setUp(self)
        self.addCleanup(lambda: [child.wait(timeout=10) for child in self.q.compositions.children.values()])
        script = self.root / 'tools/prepare_pr.py'
        script.write_text(
            'import sys,json,os,time,fcntl\nfrom pathlib import Path\n'
            'sys.path.insert(0, '+repr(str(Path(__file__).resolve().parent))+')\n'
            'from prepare_pr import prepare\n'
            'a=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n'
            'time.sleep(float(os.environ.get("COMPOSE_DELAY",0)))\n'
            'head=prepare(a["--remote"],a["--branch"],a["--head"],a["--main"],Path(a["--receipt"]),lambda _:None)\n'
            'state=Path(os.environ["FAKE_GH_STATE"])\n'
            'with state.open("r+") as f:\n'
            ' fcntl.flock(f,fcntl.LOCK_EX)\n'
            ' d=json.load(f)\n'
            ' d["prs"]["jbookout/carr-system#"+a["--pr"]]["head"]["sha"]=head\n'
            ' f.seek(0);json.dump(d,f);f.truncate()\n'
            'print(json.dumps(dict(head=head,changed=head!=a["--head"])))\n')

    def pr(self, *args, **kwargs):
        p = fixture.QueueTests.pr(self, *args, **kwargs)
        self.git('update-ref', 'refs/heads/'+p['head']['ref'], p['head']['sha'])
        return p

    def settle(self, outcome='waiting_ci'):
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            self.q.tick()
            if any(r['outcome']==outcome for r in self.q.status()):
                return
            time.sleep(.02)
        self.fail(str(self.q.status()))

    def test_top_four_heads_are_observed_while_first_ci_is_pending(self):
        self.q.parallel_size = 4
        self.data['required'] = [{'bucket': 'pending'}]
        for n in range(1, 7):
            self.pr(n, head=self.updated)
            self.q.enqueue(module.REPOS[0], n, self.updated)
        self.settle()
        self.q.tick()
        observed = [r['pr'] for r in self.q.status() if r['outcome'] == 'waiting_ci']
        self.assertEqual(observed, [1, 2, 3, 4])
        self.assertEqual(self.calls('merge'), [])

    def test_disjoint_main_advance_reuses_green_composition(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        self.settle()
        (self.remote / 'independent.txt').write_text('other PR\n')
        self.git('add', 'independent.txt')
        self.git('commit', '-qm', 'another PR lands')
        advanced = self.git('rev-parse', 'HEAD').strip()
        self.load()['required'] = [{'bucket': 'pass'}]
        self.data['prs'][module.REPOS[0]+'#1']['mergeable_state'] = 'behind'
        tree = self.git('merge-tree', '--write-tree', advanced, self.updated).strip()
        self.data['merge_sha'] = self.git('commit-tree', tree, '-p', advanced, '-m', 'squash').strip()
        self.save()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_overlapping_main_advance_starts_new_composition(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        self.settle()
        (self.remote / 'file.txt').write_text('conflicting landing\n')
        self.git('add', 'file.txt')
        self.git('commit', '-qm', 'touch same source')
        self.load()['required'] = [{'bucket': 'pass'}]
        self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        self.assertIn(self.q.status()[0]['outcome'], ('composing', 'conflict'))

    def test_composers_start_without_waiting_for_each_other(self):
        self.q.parallel_size = 4
        for n in range(1, 5):
            self.pr(n, head=self.updated)
            self.q.enqueue(module.REPOS[0], n, self.updated)
        with patch.dict('os.environ', COMPOSE_DELAY='1'):
            started = time.monotonic()
            self.q.tick()
            elapsed = time.monotonic()-started
        self.assertLess(elapsed, 3)
        self.assertEqual([r['outcome'] for r in self.q.status()], ['composing']*4)

    def test_changed_head_never_lands_after_green_check(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.settle()
        real_green = self.q.green
        def green(*args):
            result = real_green(*args)
            self.load()['prs'][module.REPOS[0]+'#1']['head']['sha'] = self.changed
            self.save()
            return result
        self.load()['required'] = [{'bucket':'pass'}]
        self.save()
        with patch.object(self.q, 'green', side_effect=green):
            self.q.tick()
        self.assertEqual(self.calls('merge'), [])

    def test_restart_after_lost_merge_response_does_not_land_twice(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.settle()
        self.load().update(required=[{'bucket':'pass'}], merge_landed_error=True)
        self.save()
        self.q.tick()
        self.restart()
        self.q.parallel_size = 4
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.q.status()[0]['merged_commit'], self.merge_sha)

    def test_restart_recovers_completed_composer_without_second_push(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.q.tick()
        for child in self.q.compositions.children.values():
            child.wait(timeout=10)
        self.restart()
        self.q.parallel_size = 4
        self.q.tick()
        self.assertEqual(self.q.status()[0]['outcome'], 'waiting_ci')
        self.assertEqual(self.q.status()[0]['composition_base'], self.main)

    def test_main_touch_then_revert_still_invalidates_composition(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.settle()
        (self.remote / 'file.txt').write_text('temporary\n')
        self.git('commit', '-qam', 'touch')
        self.git('revert', '--no-edit', 'HEAD')
        self.load()['required'] = [{'bucket':'pass'}]
        self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])

    def test_other_repo_progresses_while_first_repo_ci_waits(self):
        self.q.parallel_size = 4
        for repo in module.REPOS[:2]:
            self.pr(head=self.updated, repo=repo)
            self.q.enqueue(repo, 1, self.updated)
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.settle()
        self.q.tick()
        self.assertEqual([r['outcome'] for r in self.q.status()], ['waiting_ci']*2)

    def test_strict_ruleset_refuses_speculative_mode(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        original = self.q.api
        def api(path, *args, **kwargs):
            if '/rules/branches/main' in path:
                return [dict(type='required_status_checks', parameters=dict(
                    strict_required_status_checks_policy=True,
                    required_status_checks=[dict(context='ops/ci.sh --strict')]))]
            return original(path, *args, **kwargs)
        with patch.object(self.q, 'api', side_effect=api):
            self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.status()[0]['outcome'], 'parallel_rules_changed')

    def test_red_canary_opens_normal_revert_pr_and_never_pushes_main(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.settle('merge_requested')
        self.q.tick()
        self.load()['canary'] = 'failure'
        self.save()
        self.q.tick()
        creates = [a for a in self.load()['calls'] if a[:2] == ['pr','create']]
        self.assertEqual(len(creates), 1)
        self.assertIn('Revert', creates[0][creates[0].index('--title')+1])
        self.assertEqual(self.git('rev-parse', 'main').strip(), self.merge_sha)

    def red_canary(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.settle('merge_requested')
        self.q.tick()
        self.load()['canary'] = 'failure'
        self.save()
        self.q.tick()

    def test_revert_requires_ci_and_requeues_culprit_after_verified_merge(self):
        self.red_canary()
        self.load()['required'] = [{'bucket':'pending'}]
        self.save()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        revert = next(p for p in self.load()['prs'].values() if p['head']['ref'].startswith('merge-queue/revert-'))
        head = revert['head']['sha']
        self.git('merge-base', '--is-ancestor', self.merge_sha, head)
        self.assertEqual(self.git('rev-parse', head+'^{tree}').strip(), self.git('rev-parse', self.main+'^{tree}').strip())
        self.data['required'] = [{'bucket':'pass'}]
        self.data['merge_sha'] = self.git('commit-tree', self.git('rev-parse', head+'^{tree}').strip(), '-p', self.merge_sha, '-m', 'revert squash').strip()
        self.save()
        self.q.tick()
        self.q.tick()
        self.q.tick()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 2, self.calls('merge'))
        with module.WorkQueue(Path(__import__('os').environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            cards = [c for c in work.export()['items'] if c['card_id'].startswith('canary-requeue:')]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['stage'], 'queued')
        reapply = cards[0]['prs'][0]['head']
        self.assertEqual(self.git('rev-parse', reapply+'^{tree}').strip(), self.git('rev-parse', self.updated+'^{tree}').strip())

    def test_lost_revert_pr_response_is_reconciled_without_duplicate(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.settle('merge_requested')
        self.q.tick()
        self.load()['canary'] = 'failure'
        self.save()
        command = module.command
        def lost(argv, **kwargs):
            result = command(argv, **kwargs)
            if argv[:3] == ['gh','pr','create']:
                raise module.CommandFailed('lost create response')
            return result
        with patch.object(module, 'command', side_effect=lost):
            self.q.tick()
        self.restart()
        self.q.tick()
        creates = [a for a in self.load()['calls'] if a[:2]==['pr','create']]
        self.assertEqual(len(creates), 1)

    def test_renaming_a_touched_file_invalidates_composition(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.settle()
        self.git('mv', 'file.txt', 'renamed.txt')
        self.git('commit', '-qm', 'rename touched source')
        self.load()['required'] = [{'bucket':'pass'}]
        self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])

    def test_off_flag_keeps_serial_waiting_and_drains_composer_receipts(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.pr(2, head=self.updated)
        for n in (1, 2):
            self.q.enqueue(module.REPOS[0], n, self.updated)
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.q.tick()
        for child in self.q.compositions.children.values():
            child.wait(timeout=10)
        self.restart()
        self.q.tick()
        self.assertEqual([r['outcome'] for r in self.q.status()], ['waiting_ci','composing'])

    def test_preexisting_revert_branch_must_contain_the_actual_inverse(self):
        self.git('branch', 'merge-queue/revert-'+self.merge_sha, self.changed)
        self.red_canary()
        self.assertFalse([a for a in self.load()['calls'] if a[:2]==['pr','create']])

    def simulate(self, size):
        self.q.parallel_size = size
        self.data['dynamic_merge'] = True
        for n in range(1, 7):
            self.git('switch', '-q', '-C', 'synthetic-'+str(n), self.main)
            (self.remote / ('pr-'+str(n)+'.txt')).write_text(str(n)+'\n')
            self.git('add', 'pr-'+str(n)+'.txt')
            self.git('commit', '-qm', 'synthetic PR '+str(n))
            head = self.git('rev-parse', 'HEAD').strip()
            self.pr(n, head=head)
            self.q.enqueue(module.REPOS[0], n, head)
        self.git('switch', '-q', 'main')
        self.save()
        now, starts, landings = 0, {}, []
        transport = self.q._gh_request
        def timed(args, decode, validate):
            if args[:2] == ('pr','checks'):
                p = self.load()['prs'][module.REPOS[0]+'#'+args[2]]
                head = p['head']['sha']
                starts.setdefault(head, now)
                return [dict(bucket='pass' if now-starts[head]>=1800 else 'pending')]
            return transport(args, decode, validate)
        with patch.object(self.q, '_gh_request', side_effect=timed):
            for step in range(1000):
                self.q.tick()
                # Local composition is outside the simulated provider CI clock.
                for child in self.q.compositions.children.values():
                    child.wait(timeout=10)
                self.q.tick()
                merged = sum(p['merged'] for p in self.load()['prs'].values())
                if merged>len(landings):
                    landings.extend([now]*(merged-len(landings)))
                if merged == 6:
                    break
                future = [start+1800 for start in starts.values() if start+1800>now]
                if future:
                    now = min(future)
            else:
                self.fail(str(self.q.status()))
        self.assertEqual(self.git('ls-tree', '--name-only', 'main').count('pr-'), 6)
        self.assertEqual(len(self.calls('merge')), 6)
        cycles = len(set(starts.values()))
        return dict(size=size, ci_runs=len(starts), cycles=cycles, wall_seconds=now,
                    merged=6, failures=0, landing_seconds=landings)

    def test_six_independent_prs_use_two_ci_waves(self):
        result = self.simulate(4)
        self.assertEqual(result['cycles'], 2)
        self.assertEqual(result['ci_runs'], 6)
        self.assertLessEqual(result['wall_seconds'], 3660)
        print('SIMULATION '+json.dumps(result, sort_keys=True))

    def test_six_serial_prs_use_six_ci_waves(self):
        result = self.simulate(1)
        self.assertEqual(result['cycles'], 6)
        self.assertEqual(result['ci_runs'], 6)
        self.assertGreaterEqual(result['wall_seconds'], 10800)
        print('SIMULATION '+json.dumps(result, sort_keys=True))

    def test_unreadable_process_inventory_does_not_stall_other_repositories(self):
        self.q.parallel_size = 4
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        for child in self.q.compositions.children.values():
            child.wait(timeout=10)
        self.restart()
        self.q.parallel_size = 4
        self.pr(repo=module.REPOS[1], head=self.updated)
        self.q.enqueue(module.REPOS[1], 1, self.updated)
        real_run = module.subprocess.run
        def run(argv, **kwargs):
            if argv[:2] == ['ps','-axo']:
                raise OSError('process inventory unreadable')
            return real_run(argv, **kwargs)
        with patch.object(module.subprocess, 'run', side_effect=run):
            self.q.tick()
        self.assertEqual(self.q.status()[-1]['outcome'], 'composing')


if __name__ == '__main__':
    unittest.main()
