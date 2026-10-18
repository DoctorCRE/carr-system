"""Failure histories exercised against an owned PostgreSQL 18 cluster."""
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from cluster import Cluster
import linkfork_migration
from model import FAMILIES, KINDS, domain_ddl, edge_ddl, insert_edge


class SQLRegressions(unittest.TestCase):
    pg_bin = '/opt/homebrew/opt/postgresql@18/bin'

    def setUp(self):
        self.cluster = self.enterContext(Cluster(self.pg_bin))

    def seed(self, c):
        for stmt in domain_ddl('c', 'C') + edge_ddl('c', 'C'):
            c.execute(stmt)
        self.p, self.t, self.u, self.src, self.e = [UUID(int=i) for i in range(1, 6)]
        for id_ in (self.p, self.t, self.u):
            c.execute('INSERT INTO c.d_party VALUES (%s,%s)', (id_, 'party'))
        c.execute('INSERT INTO c.d_doctrine_section VALUES (%s,%s)', (self.src, 'source'))
        insert_edge(c, 'c', 'C', FAMILIES[0], (self.e, 'doctrine_link', 'doctrine_section', self.src, 'party', self.p, 'citation'))

    def prepare(self, c, design):
        module = linkfork_migration
        stmts, _ = module.plan('shadow', design)
        for stmt in stmts + [module.replay_sql('shadow', design)]:
            c.execute(stmt)

    def copy(self, c, design):
        for stmt in linkfork_migration.backfill('shadow', design):
            c.execute(stmt)

    def assert_equal(self, c):
        self.assertEqual(c.execute('SELECT * FROM c.relationships ORDER BY id').fetchall(),
                         c.execute('SELECT * FROM shadow.relationships ORDER BY id').fetchall())
        for kind in KINDS:
            self.assertEqual(c.execute(f'SELECT id,payload FROM c.d_{kind} ORDER BY id').fetchall(),
                             c.execute(f'SELECT id,payload FROM shadow.d_{kind} ORDER BY id').fetchall())

    def history(self, design, before_copy):
        with self.cluster.connect() as c:
            c.autocommit = True
            self.seed(c)
            self.prepare(c, design)
            if not before_copy:
                self.copy(c, design)
            c.execute('UPDATE c.l_doctrine_link SET dst_id=%s WHERE id=%s', (self.t, self.e))
            with c.transaction():
                c.execute('UPDATE c.l_doctrine_link SET dst_id=%s WHERE id=%s', (self.u, self.e))
                c.execute('DELETE FROM c.d_party WHERE id=%s', (self.t,))
            if before_copy:
                self.copy(c, design)
            c.execute('SELECT shadow_cdc.replay()')
            self.assert_equal(c)

    def test_presnapshot_history_a(self):
        self.history('A', True)

    def test_presnapshot_history_b(self):
        self.history('B', True)

    def test_postsnapshot_history_a(self):
        self.history('A', False)

    def test_postsnapshot_history_b(self):
        self.history('B', False)

    def identities(self, design, synchronous):
        with self.cluster.connect() as c:
            c.autocommit = True
            self.seed(c)
            self.prepare(c, design)
            self.copy(c, design)
            if synchronous:
                c.execute(linkfork_migration.sync_sql('shadow', design))
            c.execute('UPDATE c.d_party SET id=%s WHERE id=%s', (UUID(int=99), self.t))
            if not synchronous:
                c.execute('SELECT shadow_cdc.replay()')
            self.assert_equal(c)
            c.execute('UPDATE c.l_doctrine_link SET id=%s WHERE id=%s', (UUID(int=98), self.e))
            if not synchronous:
                c.execute('SELECT shadow_cdc.replay()')
            self.assert_equal(c)
            if design == 'B':
                self.assertEqual(c.execute('SELECT count(*) FROM shadow.entity WHERE id=%s', (self.t,)).fetchone()[0], 0)

    def test_identity_catchup_a(self):
        self.identities('A', False)

    def test_identity_catchup_b(self):
        self.identities('B', False)

    def test_identity_sync_a(self):
        self.identities('A', True)

    def test_identity_sync_b(self):
        self.identities('B', True)

    def test_socket_connection_ignores_environment(self):
        with socket.socket() as listener, tempfile.TemporaryDirectory() as directory:
            listener.bind(('127.0.0.1', self.cluster.port))
            listener.listen()
            listener.settimeout(.1)
            service = Path(directory) / 'services.conf'
            service.write_text('[poison]\nhostaddr=127.0.0.1\n')
            poisoned = {'PGHOSTADDR':'127.0.0.1', 'PGSERVICE':'poison', 'PGSERVICEFILE':str(service),
                        'PGHOST':'127.0.0.1', 'PGPORT':str(self.cluster.port), 'PGDATABASE':'wrong',
                        'PGUSER':'wrong', 'PGOPTIONS':'-c statement_timeout=1'}
            with patch.dict(os.environ, poisoned):
                with self.cluster.connect() as c:
                    self.assertIsNone(c.execute('SELECT inet_server_addr()').fetchone()[0])
                self.assertEqual({k:os.environ[k] for k in poisoned}, poisoned)
                with self.assertRaises(TimeoutError):
                    listener.accept()

    def test_incident_deletion_cascades_in_c(self):
        with self.cluster.connect() as c:
            self.seed(c)
            c.execute('INSERT INTO c.d_incident VALUES (%s,%s)', (UUID(int=10), 'incident'))
            insert_edge(c, 'c', 'C', FAMILIES[1], (UUID(int=11), 'incident_link', 'incident', UUID(int=10), 'decision', self.p, 'reference'))
            c.execute('DELETE FROM c.d_incident WHERE id=%s', (UUID(int=10),))
            self.assertEqual(c.execute('SELECT count(*) FROM c.l_incident_link').fetchone()[0], 0)

    def incident_cascade(self, design, synchronous):
        with self.cluster.connect() as c:
            c.autocommit = True
            self.seed(c)
            incident, retained, decision = (UUID(int=i) for i in (10, 12, 14))
            c.execute('INSERT INTO c.d_decision VALUES (%s,%s)', (decision, 'decision'))
            for source, edge in ((incident, UUID(int=11)), (retained, UUID(int=13))):
                c.execute('INSERT INTO c.d_incident VALUES (%s,%s)', (source, 'incident'))
                insert_edge(c, 'c', 'C', FAMILIES[1], (edge, 'incident_link', 'incident', source, 'decision', decision, 'reference'))
            self.prepare(c, design)
            self.copy(c, design)
            if synchronous:
                c.execute(linkfork_migration.sync_sql('shadow', design))
            c.execute('DELETE FROM c.d_incident WHERE id=%s', (incident,))
            if not synchronous:
                c.execute('SELECT shadow_cdc.replay()')
            self.assert_equal(c)
            self.assertEqual(c.execute('SELECT count(*) FROM shadow.relationships WHERE family=%s', ('incident_link',)).fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT id FROM shadow.d_decision').fetchall(), [(decision,)])
            if design == 'B':
                self.assertEqual(c.execute('SELECT count(*) FROM shadow.entity WHERE id=%s', (incident,)).fetchone()[0], 0)

    def test_incident_cascade_sync_a(self):
        self.incident_cascade('A', True)

    def test_incident_cascade_sync_b(self):
        self.incident_cascade('B', True)

    def test_incident_cascade_catchup_a(self):
        self.incident_cascade('A', False)

    def test_incident_cascade_catchup_b(self):
        self.incident_cascade('B', False)


def verify(pg_bin):
    SQLRegressions.pg_bin = pg_bin
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SQLRegressions))
    if not result.wasSuccessful():
        raise AssertionError('linkfork SQL regressions failed')


if __name__ == '__main__':
    verify(SQLRegressions.pg_bin)
