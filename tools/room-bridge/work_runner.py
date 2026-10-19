"""Detached work-queue executor. No sender waits for a desk or Codex slot.

The runner holds a singleton flock, claims only after nonblocking desk/slot
locks, and passes those locks to its worker. Workers survive the runner.
Unknown interrupted effects retain a durable reservation until reconciled.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from work_queue import WorkQueue
import write_ownership

CODEX_SLOT_DIR = Path.home() / '.config/carr/codex-slots'


class Resources:
    def __init__(self, locks, slots=None, limit=None):
        self.locks = Path(locks)
        self.slots = Path(slots or os.environ.get('CARR_CODEX_SLOT_DIR') or CODEX_SLOT_DIR)
        self.limit = max(1, int(limit or os.environ.get('CARR_CODEX_MAX_CONCURRENT') or 4))

    @staticmethod
    def _try(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    def acquire(self, desk, *, codex):
        desk_lock = self._try(self.locks / (hashlib.sha256(desk.encode()).hexdigest() + '.lock'))
        if desk_lock is None:
            return None
        if not codex:
            return [desk_lock]
        for index in range(self.limit):
            slot = self._try(self.slots / f'slot-{index}.lock')
            if slot is not None:
                return [desk_lock, slot]
        desk_lock.close()
        return None


class Runner:
    def __init__(self, queue, resources, *, launch, terminated=write_ownership.process_terminated):
        self.queue, self.resources, self.launch, self.terminated = queue, resources, launch, terminated

    def tick(self):
        self.queue.recover(self.terminated, actor='runner')
        for candidate in self.queue.candidates():
            active = self.queue.active()
            if candidate['desk'] in {r['desk'] for r in active}:
                continue
            codex = json.loads(candidate['options']).get('codex', False)
            if codex and sum(json.loads(r['options']).get('codex', False) for r in active) >= self.resources.limit:
                continue
            handles = self.resources.acquire(candidate['desk'], codex=codex)
            if handles is None:
                continue
            try:
                card = self.queue.claim(eligible_desks={candidate['desk']}, card_id=candidate['card_id'],
                                        actor='runner', owner=write_ownership.process_owner())
                if card is not None:
                    try:
                        self.launch(card, handles)
                    except OSError as exc:
                        self.queue.finish(card['card_id'], card['claim'],
                                          {'status': 'failed', 'detail': f'worker launch failed: {exc}',
                                           'termination_confirmed': True}, actor='runner')
            finally:
                # Close only this copy. LOCK_UN would release the worker's inherited lock.
                for handle in handles:
                    handle.close()


def kick(path, registry, results):
    """Return immediately; runner flock makes concurrent kicks harmless."""
    path = Path(path)
    with path.with_suffix('.runner.log').open('a') as log:
        subprocess.Popen([sys.executable, str(HERE / 'work_runner.py'), '--db', str(path),
                          '--registry', str(registry), '--results', str(results)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)


def worker(queue, card_id, token, registry, results):
    import dispatch
    from desks import Registry, DeskError
    queue.attach(card_id, token, write_ownership.process_owner())
    row = queue.execution(card_id, token)
    options = json.loads(row['options'])
    options.pop('codex', None)
    results = Path(options.pop('result_path', results))
    registry = Path(options.pop('registry_path', registry))
    def started(entry):
        queue.update(card_id, actor='worker', reason='executor resolved at start',
                     agent=entry.get('name', row['desk']),
                     **{k: entry[k] for k in ('model', 'effort', 'family') if entry.get(k)})
    try:
        result = dispatch.dispatch(row['desk'], row['task'], registry=Registry(registry),
                                   results_path=results, on_started=started, **options)
    except DeskError as exc:
        result = {'status': 'failed', 'detail': str(exc), 'termination_confirmed': True}
    except Exception as exc:
        result = {'status': 'failed', 'detail': f'{type(exc).__name__}: {exc}',
                  'termination_confirmed': False}
    queue.finish(card_id, token, result, actor='worker')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('--results', required=True, type=Path)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--worker', nargs=2, metavar=('CARD', 'CLAIM'))
    args = parser.parse_args(argv)
    with WorkQueue(args.db) as queue:
        if args.worker:
            worker(queue, *args.worker, args.registry, args.results)
            return 0
        if args.publish:
            lock = Resources._try(args.db.with_suffix('.publish.lock'))
            if lock is None:
                return 0
            with lock:
                os.environ['CARR_WORK_QUEUE_DB'] = str(args.db)
                sys.path.insert(0, str(HERE.parent))
                import progress_board
                def publish():
                    if os.environ.get('PROGRESS_BOARD_LOCAL_ONLY') == '1':
                        raise RuntimeError('board publication disabled by PROGRESS_BOARD_LOCAL_ONLY')
                    progress_board.publish_board('carr-v5', refresh_inventory=False)
                queue.publish(publish)
            return 0
        lock = Resources._try(args.db.with_suffix('.runner.lock'))
        if lock is None:
            return 0
        children = []
        def launch(card, handles):
            key = hashlib.sha256(card['card_id'].encode()).hexdigest()[:20]
            with args.db.with_name(f'work-{key}.log').open('a') as log:
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                    '--db', str(args.db), '--registry', str(args.registry), '--results', str(args.results),
                    '--worker', card['card_id'], card['claim']], stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, start_new_session=True,
                    pass_fds=tuple(h.fileno() for h in handles))
                children.append((child, card['card_id'], card['claim']))
                owner = {**write_ownership.process_owner(), 'pid': child.pid,
                         'start_time': write_ownership.process_start(child.pid)}
                try:
                    queue.attach(card['card_id'], card['claim'], owner)
                except ValueError:
                    pass  # The worker already committed its completion receipt.
        resources = Resources(args.db.with_suffix('.desks'))
        runner = Runner(queue, resources, launch=launch)
        publisher = None
        with lock:
            while True:
                runner.tick()
                publication = queue.publication()
                if (publication['dirty'] > publication['published']
                        and time.time() - publication['last_attempt'] >= 2
                        and (publisher is None or publisher.poll() is not None)):
                    with args.db.with_suffix('.publish.log').open('a') as log:
                        publisher = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                            '--db', str(args.db), '--registry', str(args.registry), '--results', str(args.results),
                            '--publish'], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True)
                waiting = []
                for child, card, token in children:
                    if child.poll() is None:
                        waiting.append((child, card, token))
                        continue
                    row = next((r for r in queue.active() if r['card_id'] == card and r['claim'] == token), None)
                    if (row is not None and row['stage'] != 'blocked'
                            and json.loads(row['data']).get('last_result', {}).get('status')
                            not in ('delivered', 'delivered_live', 'backgrounded')):
                        queue.finish(card, token, {'status': 'failed', 'termination_confirmed': False,
                            'detail': 'worker exited without a completion receipt; reconcile effects'}, actor='runner')
                children[:] = waiting
                time.sleep(1)


if __name__ == '__main__':
    raise SystemExit(main())
