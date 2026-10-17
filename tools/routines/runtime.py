import argparse
from datetime import datetime, timedelta
import fcntl
import importlib
import json
import os
from pathlib import Path
import sys
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lib.record_call import call_verb
from lib.secret_redaction import redact_text, sensitive_env_values

MANIFEST = json.loads((ROOT / 'ops/routines/jobs.v1.json').read_text())
JOBS = {job['id']: job for job in MANIFEST['jobs']}
NAMESPACE = uuid.UUID('17664930-4813-46ec-b5ed-f0e5ffb6f1b8')


def period(now):
    local = now.astimezone(ZoneInfo(MANIFEST['timezone']))
    return (local.date() - timedelta(days=local.weekday())).isoformat()


def scheduled_slot(ident, monday):
    job = JOBS[ident]
    return (datetime.fromisoformat(monday).replace(tzinfo=ZoneInfo(MANIFEST['timezone']),
            hour=job['hour'], minute=job['minute']) + timedelta(days=job['weekday'] - 1))


def due(ident, now, completed):
    return not completed and now >= scheduled_slot(ident, period(now))


class Context:
    def __init__(self, ident, *, dry_run=False, fixture=None, now=None, journal=None):
        self.ident = ident
        self.dry_run = dry_run
        self.fixture = fixture
        self.now = now or datetime.now(ZoneInfo(MANIFEST['timezone']))
        self.effects = []
        self.journal = journal
        self.state = json.loads(journal.read_text()) if journal and journal.exists() else {}

    def save(self):
        if self.journal and not self.dry_run:
            temporary = self.journal.with_suffix('.tmp')
            with temporary.open('w') as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(self.state, handle, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.journal)
            directory = os.open(self.journal.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def read(self, verb, args):
        if self.fixture is not None:
            return self.fixture.get('reads', {}).get(verb, {})
        result = call_verb(verb, args)
        if not result.ok:
            raise RuntimeError(result.describe())
        return result.reply

    def write(self, verb, args, key):
        payload = dict(args, idempotency_key=str(uuid.uuid5(NAMESPACE, f'{self.ident}:{key}')))
        if self.dry_run:
            self.effects.append({'verb': verb, 'key': key})
            return {'ok': True, 'dry_run': True, 'party_id': '00000000-0000-4000-8000-000000000001'}
        cached = self.state.setdefault('effects', {}).get(key)
        if cached:
            if cached['args'] != payload or cached['verb'] != verb:
                raise RuntimeError('effect retry changed the prepared payload')
            if 'reply' in cached:
                return cached['reply']
            raise RuntimeError(f'effect {key} requires record-layer readback before retry; journal retained')
        self.state['effects'][key] = {'verb': verb, 'args': payload}
        self.save()
        result = call_verb(verb, payload)
        if not result.ok:
            raise RuntimeError(result.describe())
        self.state['effects'][key]['reply'] = result.reply
        self.save()
        return result.reply

    def review_item(self, title, body, key):
        return self.write('report-problem', {
            'situation': 'scheduled NPPES lead intake identity verification primary source evidence',
            'title': title[:200], 'desired_outcome': body[:2000],
            'acceptance_criteria': [{'id': 'REVIEW', 'text': 'Resolve the cited lead evidence and identity on the app.'}]
        }, key)

    def failure(self, detail):
        title = f'{self.ident}: repair failed code routine'
        board = self.read('loop-board', {'owner': 'claude', 'status': 'open', 'search': title, 'limit': 300})
        if any(row.get('title') == title for row in board.get('loops', [])):
            return
        self.write('add-loop', {'kind': 'open_loop', 'owner': 'claude', 'domain': 'system',
            'title': title, 'body': f'THE FIX: inspect {self.ident} inputs and its private journal. Read back any unresolved effect before retrying; preserve its idempotency key. Repair code or integration, run its selftests and --dry-run. Failure: {detail}',
            'blocker': 'other_lane', 'blocker_detail': 'The orchestrator builder queue owns source repairs and effect readback.',
            'marker': 'none'}, 'failure-loop:' + period(self.now))


def run_module(ctx, module):
    plan = ctx.state.get('plan') or module.prepare(ctx)
    if plan.get('work') is not True:
        ctx.state.update(completed=True, no_work=True)
        ctx.save()
        return 0
    if not ctx.dry_run:
        ctx.state.update(plan=plan, started_at=ctx.state.get('started_at', ctx.now.isoformat()))
        ctx.save()
    result = module.execute(ctx, plan)
    ctx.state['completed'] = True
    ctx.save()
    print(json.dumps(result, default=str, sort_keys=True))
    return 0


def execute(ctx):
    if not ctx.dry_run and not due(ctx.ident, ctx.now, ctx.state.get('completed', False)):
        return 0
    module = importlib.import_module('tools.routines.' + JOBS[ctx.ident]['module'])
    return run_module(ctx, module)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('id', choices=JOBS)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--fixture', type=Path)
    ap.add_argument('--now', help='ISO timestamp for a dry-run fixture')
    args = ap.parse_args()
    if (args.fixture or args.now) and not args.dry_run:
        ap.error('--fixture and --now require --dry-run')
    now = datetime.fromisoformat(args.now) if args.now else None
    if now and now.tzinfo is None:
        ap.error('--now requires an explicit timezone offset')
    fixture = json.loads(args.fixture.read_text()) if args.fixture else None
    ctx = Context(args.id, dry_run=args.dry_run, fixture=fixture, now=now)
    def failed(exc):
        detail = redact_text(f'{type(exc).__name__}: {exc}', known_secrets=sensitive_env_values(os.environ))[:500]
        print(f'routine {args.id}: FAIL {detail}', file=sys.stderr)
        if not args.dry_run:
            try:
                ctx.failure(detail)
            except Exception:
                print('routine failure-loop write unavailable; inspect private journal and recorded job failure', file=sys.stderr)
        return 1

    if args.dry_run:
        try:
            return execute(ctx)
        except Exception as exc:
            return failed(exc)
    directory = ROOT / 'out/routines' / args.id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            wall_now = ctx.now
            current = directory / (period(wall_now) + '.json')
            for journal in sorted(set(directory.glob('????-??-??.json')) | {current}):
                if journal > current:
                    continue
                state = json.loads(journal.read_text()) if journal.exists() else {}
                if state.get('completed'):
                    continue
                ctx.journal, ctx.state = journal, state
                observed = state.get('started_at') or state.get('scheduled_at')
                ctx.now = datetime.fromisoformat(observed) if observed else (
                    wall_now if journal == current else scheduled_slot(args.id, journal.stem))
                if due(args.id, ctx.now, False):
                    ctx.state.setdefault('scheduled_at', scheduled_slot(args.id, journal.stem).isoformat())
                    ctx.save()
                result = execute(ctx)
                if result or not ctx.state.get('completed'):
                    return result
            return 0
        except Exception as exc:
            return failed(exc)


if __name__ == '__main__':
    raise SystemExit(main())
