"""Give a verified synthetic staging journey one overdue stored checklist item."""
import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

PRODUCER_COMMIT = '517ecc12c0480d859dd1f4cb270f2a283a853065'


def bind_home_rows(journey):
    original = journey.owned_rows

    def owned_rows(run):
        ids = journey.identities(run)
        return [('public.next_action', "subject_type='deal' and subject_id=%s and source_ref=%s",
                 (ids['deal'], f'fixtures/staging-home/{run}'))] + original(run)

    journey.owned_rows = owned_rows


def prepare(journey, conn, run):
    result = journey.prepare(conn, run)
    journey.verify(conn, run)
    ids = journey.identities(run)
    try:
        with conn.cursor() as cur:
            journey.owner(cur)
            cur.execute("update public.deal set next_date=date '2000-01-01' where id=%s", (ids['deal'],))
            cur.execute("update public.deal_note set kind='next_step' where id=%s and deal_id=%s",
                        (ids['note_b'], ids['deal']))
            cur.execute("""insert into public.next_action(subject_type,subject_id,owner_id,due_on,description,
                          created_by,updated_by,item_title,item_bullets,source_ref)
                select 'deal',%s,id,date '2000-01-01','Review synthetic update',id,id,
                       'Review synthetic update','[{"kind":"promised","text":"Review the synthetic update."}]'::jsonb,%s
                  from public.actor where slug='joe' and active
                on conflict (subject_id,source_ref,item_title)
                  where subject_type='deal' and item_title is not null do nothing""",
                        (ids['deal'], f'fixtures/staging-home/{run}'))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'read', 'cleanup', 'remaining'))
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    root = pathlib.Path(os.environ['CARR_JOURNEY_FIXTURE_REPO']).resolve()
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    changed = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip()
    if head != PRODUCER_COMMIT or changed:
        raise ValueError('Home fixture requires the clean pinned journey producer')
    spec = importlib.util.spec_from_file_location('journey', root / 'tools/staging-journey-fixtures.py')
    journey = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(journey)
    bind_home_rows(journey)
    journey.identities(args.run)
    owner = journey.staging_owner()
    with journey.psycopg.connect(owner.value, connect_timeout=10) as conn:
        if args.action == 'prepare':
            result = prepare(journey, conn, args.run)
        elif args.action == 'read':
            result = journey.verify(conn, args.run)
        else:
            if args.action == 'cleanup':
                journey.cleanup(conn, args.run)
            result = {'schema': journey.SCHEMA, 'run': args.run, 'remaining': journey.remaining(conn, args.run)}
        print(json.dumps(result, default=str))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': 'staging_home_fixture_failed', 'type': type(error).__name__}), file=sys.stderr)
        sys.exit(1)
