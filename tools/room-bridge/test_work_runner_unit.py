"""Runner eligibility uses durable order and real nonblocking resource locks."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_queue import WorkQueue
from work_runner import Runner, Resources


class RunnerTests(unittest.TestCase):
    def test_publication_acknowledges_reader_linkage_and_still_detects_changed_data(self):
        import copy
        import subprocess
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import progress_board
        repo = Path(__file__).resolve().parents[2]
        remote = {}
        writes = []
        corrupt = False

        def call(verb, args):
            if verb == 'publish-board-snapshot':
                writes.append(args)
                remote[args['board_id']] = {'board_id': args['board_id'], 'version': len(writes),
                          'snapshot_json': copy.deepcopy(args['snapshot'])}
                return {'ok': True, 'snapshot': remote[args['board_id']]}
            self.assertEqual(verb, 'read-progress-board')
            saved = remote.get(args['board_id'])
            if saved is None:
                return {'ok': True, 'snapshot': None, 'questions': []}
            script = """
import {projectBoardLinkage} from './mcp-server/src/work-linkage.js';
let input = ''; for await (const part of process.stdin) input += part;
console.log(JSON.stringify(projectBoardLinkage(JSON.parse(input))));
"""
            row = json.loads(subprocess.run(['node', '--input-type=module', '-e', script],
                input=json.dumps(saved), text=True, capture_output=True, check=True, cwd=repo).stdout)
            if corrupt and args['board_id'] == 'carr-v5':
                row['snapshot_json']['tasks']['job']['title'] = 'Wrong title'
            return {'ok': True, 'snapshot': row, 'questions': []}

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'queue.sqlite3'
            state = {'project': 'carr-v5', 'tasks': {'job': {'title': 'Synthetic job', 'status': 'queued'}}}
            with WorkQueue(db) as queue, \
                 patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(db), 'PROGRESS_BOARD_ROOT': directory}), \
                 patch.object(progress_board, 'read_state', return_value=state), \
                 patch.object(progress_board, 'require_primary_publication'), \
                 patch.object(progress_board, 'call_verb', side_effect=call):
                queue.enqueue('A', desk='sol', task='synthetic', actor='test')
                publish = lambda: progress_board.publish_board('carr-v5', costs={}, refresh_inventory=False)
                self.assertTrue(queue.publish(publish, now=10), queue.publication()['error'])
                self.assertEqual(queue.publication()['published'], queue.export()['revision'])
                publish()
                project_writes = lambda: sum(row['board_id'] == 'carr-v5' for row in writes)
                write_count = project_writes()
                publish()
                self.assertEqual(project_writes(), write_count, 'reader enrichment must not trigger another write')
                queue.enqueue('B', desk='luna', task='synthetic', actor='test')
                corrupt = True
                self.assertFalse(queue.publish(publish, now=12))
                self.assertIn('did not read back', queue.publication()['error'])
                self.assertLess(queue.publication()['published'], queue.publication()['dirty'])

    def test_busy_slots_do_not_block_sender_or_reorder_other_desks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with WorkQueue(root / 'queue.sqlite3') as q:
                q.enqueue('A', desk='sol', task='a', actor='test', options={'codex': True})
                q.enqueue('B', desk='other', task='b', actor='test')
                q.enqueue('C', desk='sol', task='c', actor='test', options={'codex': True})
                resources = Resources(root / 'locks', root / 'slots', limit=1)
                held = resources.acquire('external', codex=True)
                launched, worker_handles = [], []
                def launch(card, handles):
                    launched.append(card['card_id'])
                    worker_handles.extend(os.dup(h.fileno()) for h in handles)
                runner = Runner(q, resources, launch=launch, terminated=lambda owner: False)
                try:
                    runner.tick()
                    self.assertEqual(launched, ['B'])
                    for h in held:
                        h.close()
                    runner.tick()
                    self.assertEqual(launched, ['B', 'A'])
                    runner.tick()
                    self.assertEqual(launched, ['B', 'A'])
                    self.assertEqual([i['card_id'] for i in q.export()['items']], ['A', 'B', 'C'])
                finally:
                    for fd in worker_handles:
                        os.close(fd)
                    for h in held:
                        h.close()

    def test_worker_records_actual_model_and_completion(self):
        from unittest.mock import patch
        import dispatch
        from work_runner import worker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with WorkQueue(root / 'queue.sqlite3') as q:
                q.enqueue('A', desk='sol', task='build', actor='test')
                claim = q.claim(eligible_desks={'sol'}, actor='runner', owner={})
                def execute(*args, **kwargs):
                    kwargs['on_started']({'model': 'actual-sol', 'effort': 'high', 'family': 'sol'})
                    self.assertEqual(q.get('A')['model'], 'actual-sol')
                    return {'status': 'completed', 'model': 'actual-sol', 'msg_id': 'receipt-1'}
                with patch.object(dispatch, 'dispatch', side_effect=execute):
                    worker(q, 'A', claim['claim'], root / 'registry.json', root / 'results.jsonl')
                card = q.get('A')
                self.assertEqual((card['stage'], card['dispatch_ref']), ('review', 'receipt-1'))
                self.assertFalse(card['active'])

    def test_detached_worker_survives_runner_exit_and_restart_obeys_priority(self):
        import subprocess
        import time
        import desks
        root_script = str(Path(__file__).with_name('work_runner.py'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bins = root / 'bin'
            bins.mkdir()
            fake = bins / 'codex'
            fake.write_text('#!' + sys.executable + "\n" + '''
import json, os, sys, time
from pathlib import Path
root = Path(os.environ['SYNTHETIC_QUEUE_ROOT'])
name = 'B' if 'Synthetic B' in sys.argv[-1] else 'A'
(root / (name + '.started')).touch()
deadline = time.monotonic() + 8
while not (root / (name + '.release')).exists():
    if time.monotonic() > deadline:
        sys.exit(1)
    time.sleep(0.03)
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('synthetic completed')
print(json.dumps({'type': 'turn.completed'}))
''')
            fake.chmod(0o755)
            (root / 'models_cache.json').write_text(json.dumps({'models': [{'slug': 'gpt-6.1-sol'}]}))
            registry = desks.Registry(root / 'registry.json')
            registry.register('sol', 'codex-session', family='sol', effort='high', cwd=tmp)
            db, results = root / 'queue.sqlite3', root / 'results.jsonl'
            env = {**os.environ, 'PATH': str(bins) + os.pathsep + os.environ['PATH'],
                   'CODEX_HOME': tmp, 'SYNTHETIC_QUEUE_ROOT': tmp,
                   'CARR_CODEX_SLOT_DIR': str(root / 'slots'), 'CARR_CODEX_MAX_CONCURRENT': '1',
                   'PROGRESS_BOARD_LOCAL_ONLY': '1'}
            args = [sys.executable, root_script, '--db', str(db), '--registry', str(registry.path), '--results', str(results)]
            children = []
            def wait_for(predicate):
                deadline = time.monotonic() + 10
                while not predicate():
                    if time.monotonic() > deadline:
                        self.fail('synthetic queue executor did not reach expected state')
                    time.sleep(0.05)
            try:
                with WorkQueue(db) as q:
                    for card in ('A', 'B'):
                        q.enqueue(card, desk='sol', task='Synthetic ' + card, actor='test', options={'codex': True, 'fresh': True})
                    q.prioritize('B', actor='test', reason='priority before runner starts')
                    with open(os.devnull, 'w') as log:
                        first = subprocess.Popen(args, env=env, stdout=log, stderr=log, start_new_session=True)
                        children.append(first)
                        wait_for(lambda: (root / 'B.started').exists())
                        first.terminate()
                        first.wait(timeout=3)
                        (root / 'B.release').touch()
                        wait_for(lambda: q.get('B')['stage'] == 'review')
                        self.assertFalse((root / 'A.started').exists())
                        second = subprocess.Popen(args, env=env, stdout=log, stderr=log, start_new_session=True)
                        children.append(second)
                        wait_for(lambda: (root / 'A.started').exists())
                        (root / 'A.release').touch()
                        wait_for(lambda: q.get('A')['stage'] == 'review')
                    self.assertEqual(q.get('B')['model'], 'gpt-6.1-sol')
                    self.assertEqual(len(results.read_text().splitlines()), 2)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=3)

    def test_publisher_uses_existing_board_path(self):
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import progress_board
        import work_runner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'queue.sqlite3'
            with WorkQueue(db) as q:
                q.enqueue('A', desk='sol', task='synthetic', actor='test')
                with patch.dict(os.environ, {'PROGRESS_BOARD_LOCAL_ONLY': '0'}), \
                     patch.object(progress_board, 'publish_board', side_effect=RuntimeError('legacy oversized')), \
                     patch.object(progress_board, 'read_state', return_value={'project': 'carr-v5'}), \
                     patch.object(progress_board, 'publish_orchestrator') as publish:
                    result = work_runner.main(['--db', str(db), '--registry', str(root / 'registry.json'),
                                               '--results', str(root / 'results.jsonl'), '--publish'])
                self.assertEqual(result, 0)
                publish.assert_called_once_with({'project': 'carr-v5'})
                self.assertEqual(q.publication()['published'], q.export()['revision'])


if __name__ == '__main__':
    unittest.main()
