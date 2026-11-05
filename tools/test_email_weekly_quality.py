"""Exercise weekly quality through its actual PostgreSQL query."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir, postgres_fixture_group

SPEC = importlib.util.spec_from_file_location('weekly_quality', Path(__file__).with_name('email-weekly-quality.py'))
assert SPEC is not None and SPEC.loader is not None
quality = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = quality
SPEC.loader.exec_module(quality)


class QualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        group = postgres_fixture_group()
        group.__enter__()
        cls.addClassCleanup(group.__exit__, None, None, None)
        binary = postgres_bin_dir()
        pg = DisposablePostgres('weekly-quality-', binary / 'pg_ctl')
        cls.addClassCleanup(pg.close)
        data = pg.root / 'data'
        pg.run([str(binary / 'initdb'), '-D', str(data), '-A', 'trust'], capture_output=True, text=True, check=True)
        pg.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(pg.root / 'postgres.log'),
                '-o', f'-k {pg.root} -c listen_addresses= -p 55473', '-w', 'start'], capture_output=True, text=True, check=True)
        cls.command = [str(binary / 'psql'), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-h', str(pg.root), '-p', '55473', '-d', 'postgres']
        cls.sql('''create table actor(id text primary key, slug text, kind text);
create table deal(id text primary key, owner text);
create table activity(id text primary key,kind text,email_source jsonb,email_identity jsonb);
create table event(id text primary key,subject_id text,verb text,new_value jsonb,occurred_at timestamptz,
 recorded_at timestamptz,mutation_order bigint,actor_id text,cause text,human_quote text,sponsoring_human_slug text);
create view v_email_thread_filing_event as select id,subject_id,verb,new_value,recorded_at,mutation_order from event
where verb in ('inherit-email-thread','confirm-email-filing','undo-email-thread-filing');
insert into actor values ('j','joe','human'),('d','dell','human'),('a','worker','automation');
insert into deal values ('deal','joe');''')

    @classmethod
    def sql(cls, query):
        return subprocess.run(cls.command, input=query, text=True, capture_output=True, check=True, timeout=35).stdout.strip()

    def setUp(self):
        self.sql("delete from event where id is not null; delete from activity where id is not null;")
        self.window = quality.Window('2026-10-01T00:00:00Z', '2026-10-08T00:00:00Z', '2026-10-15T00:00:00Z', 'joe', None)

    def effect(self, ident, verb='inherit-email-thread', actor='a', cause='automation_job', at='2026-10-02', kind='email_in', owner='joe'):
        self.sql(f"""insert into activity values ('{ident}','{kind}','{{"mailbox_owner":"{owner}"}}','{{"mailbox_owner":"{owner}","mailbox_account":"private@example.test"}}');
insert into event values ('{ident}','deal','{verb}','{{"activity_id":"{ident}"}}','{at}','{at}',1,'{actor}','{cause}',null,'joe');""")

    def undo(self, ident, target, at='2026-10-09', actor='j', activity=None):
        self.sql(f"""insert into event values ('{ident}','deal','undo-email-thread-filing',
'{{"event_id":"{target}","activity_id":"{activity or target}"}}','{at}','{at}',2,'{actor}','human_correction',null,null);""")

    def result(self):
        return quality.report(self.window, lambda sql: json.loads(self.sql(quality.json_query(sql))),
                              lambda: quality.timestamp('2026-10-20T00:00:00Z'))

    def test_empty(self):
        result = self.result()
        self.assertIsNone(result['actions'][0]['observed_rate'])
        self.assertIsNone(result['actions'][0]['threshold_comparison'])
        self.assertEqual(result['actions'][0]['status'], 'no_evidenced_population')
        self.assertIsNone(result['corrected_misses']['count'])
        self.assertTrue(all(row['denominator'] is None for row in result['unsupported_types']))

    def test_threshold_duplicate_and_later_undo(self):
        for i in range(20):
            self.effect(f'e{i}')
        self.undo('u1', 'e0')
        self.undo('duplicate', 'e0')
        action = self.result()['actions'][0]
        self.assertEqual((action['numerator'], action['denominator'], action['observed_rate']), (1, 20, .05))
        self.assertEqual(action['threshold_comparison'], 'at_threshold')
        self.undo('u2', 'e1')
        self.assertEqual(self.result()['actions'][0]['threshold_comparison'], 'above_threshold')

    def test_population_inverse(self):
        self.effect('automatic')
        self.undo('later', 'automatic')
        self.effect('old', at='2026-09-30')
        self.undo('old-undo', 'old')
        self.effect('end', at='2026-10-08')
        self.effect('call', kind='call')
        self.effect('calendar', kind='meeting')
        self.effect('other-owner', owner='dell')
        self.effect('other-actor', actor='d')
        self.effect('confirmed', verb='confirm-email-filing', actor='j', cause='human_stated')
        self.effect('import', cause='import_migration')
        self.effect('unknown', cause='system')
        result = self.result()
        self.assertEqual((result['actions'][0]['numerator'], result['actions'][0]['denominator']), (1, 1))
        self.assertEqual(result['separate_populations'], dict(partner_confirmed=1, bulk_import=1, unclassified=1, unrelated_actor=1))

    def test_nonhuman_sponsorship(self):
        self.sql("insert into actor values ('s','scheduler','system');")
        for actor in ('a', 's'):
            for sponsor, value in (('null', 'null'), ('wrong', "'dell'"), ('partner', "'joe'")):
                ident = f'{actor}-{sponsor}'
                self.effect(ident, actor=actor)
                self.sql(f"update event set sponsoring_human_slug={value} where id='{ident}';")
        self.effect('direct-partner', actor='j')
        self.sql("update event set sponsoring_human_slug=null where id='direct-partner';")
        result = self.result()
        self.assertEqual(result['actions'][0]['denominator'], 3)
        self.assertEqual(result['separate_populations']['unrelated_actor'], 4)

    def test_undo_filters(self):
        for name in ('cutoff', 'wrong-actor', 'wrong-link', 'before', 'recorded-late'):
            self.effect(name)
        self.undo('at-cutoff', 'cutoff', at='2026-10-15')
        self.undo('dell', 'wrong-actor', actor='d')
        self.undo('bad-link', 'wrong-link', activity='other')
        self.undo('before-effect', 'before', at='2026-10-01')
        self.undo('late-record', 'recorded-late')
        self.sql("update event set recorded_at='2026-10-16' where id='late-record';")
        self.assertEqual(self.result()['actions'][0]['numerator'], 0)

    def test_legacy_owner_and_account(self):
        self.effect('legacy')
        self.sql("update activity set email_identity=null,email_source=null where id='legacy';")
        self.assertEqual(self.result()['actions'][0]['denominator'], 1)
        self.window = quality.Window(self.window.start, self.window.end, self.window.undo_before, 'joe', 'private@example.test')
        self.assertEqual(self.result()['actions'][0]['denominator'], 0)

    def test_reproducibility_and_private_output(self):
        self.effect('secret-event')
        self.assertEqual(self.result(), self.result())
        result = self.result()
        self.assertNotIn('private@example.test', quality.summary(result))
        self.assertNotIn('secret-event', json.dumps(result))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.json'
            quality.write_private(path, result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                quality.write_private(path, result)

    def test_cli_fixture_and_error_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'fixture.json'
            output = Path(directory) / 'private.json'
            rows = [dict(population=name, denominator=0, numerator=0) for name in quality.POPULATIONS]
            fixture.write_text(json.dumps(rows))
            command = [sys.executable, str(ROOT / 'tools/email-weekly-quality.py'),
                       '--start', self.window.start, '--end', self.window.end,
                       '--undo-before', self.window.undo_before, '--partner', 'joe',
                       '--fixture', str(fixture), '--output', str(output)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('Synthetic fixture', run.stdout)
            self.assertEqual(json.loads(output.read_text())['source'], 'synthetic')
            fixture.write_text('private@example.test')
            failed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 1)
            self.assertNotIn('private@example.test', failed.stderr)
            self.assertNotIn('Traceback', failed.stderr)

    def test_cli_actual_read_transaction(self):
        self.effect('cli-effect')
        with tempfile.TemporaryDirectory() as directory:
            service = Path(directory) / 'services.conf'
            service.write_text('[quality_fixture]\nhost=' + self.command[self.command.index('-h')+1] +
                               '\nport=55473\ndbname=postgres\n')
            output = Path(directory) / 'private.json'
            run = subprocess.run([sys.executable, str(ROOT / 'tools/email-weekly-quality.py'),
                                  '--start', self.window.start, '--end', self.window.end,
                                  '--undo-before', self.window.undo_before, '--partner', 'joe',
                                  '--service', 'quality_fixture', '--output', str(output)],
                                 capture_output=True, text=True, env={**os.environ, 'PGSERVICEFILE': str(service)})
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(output.read_text())['actions'][0]['denominator'], 1)

    def test_invalid_input(self):
        for start, end, cutoff in [('2026-10-08Z','2026-10-01Z','2026-10-15Z'),
                                   ('2026-10-01','2026-10-08','2026-10-15'),
                                   ('2026-10-01T00:00:00Z','2026-11-08T00:00:00Z','2026-11-15T00:00:00Z')]:
            with self.assertRaises(ValueError):
                quality.report(quality.Window(start,end,cutoff,'joe',None), lambda sql: [], lambda: quality.timestamp('2026-12-01T00:00:00Z'))
        with self.assertRaises(ValueError):
            quality.report(self.window, lambda sql: [], lambda: quality.timestamp('2026-10-20T00:00:00Z'))
        with self.assertRaises(ValueError):
            quality.report(self.window, lambda sql: [], lambda: quality.timestamp('2026-10-10T00:00:00Z'))


if __name__ == '__main__':
    unittest.main()
