#!/usr/bin/env python3
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import pg_orphan_reap as reaper


class Selection(unittest.TestCase):
    def test_only_old_orphan_throwaway_postmasters_are_selected(self):
        fixture = '''
101 1 7201 /opt/homebrew/bin/postgres -D /tmp/carr-local-pg-ci.abc/data
102 44 9000 /opt/homebrew/bin/postgres -D /tmp/release-abandon-abc/data
103 1 7200 /opt/homebrew/bin/postgres -D /tmp/successor-postgres-abc/data
104 1 9900 /opt/homebrew/bin/postgres -D /var/lib/postgresql/data
105 1 9900 /opt/homebrew/bin/postgres -D /tmp/production/data
106 1 9900 postgres: checkpointer
107 1 9900 /opt/homebrew/bin/postgres -D /tmp/pr1493-pg
108 1 9900 /opt/homebrew/bin/postgres -D /tmp/shadowx.abc/data
109 1 9900 /opt/homebrew/bin/postgres -D /home/joe/shadowx.abc/data
110 1 9900 /opt/homebrew/bin/postgres -D /tmp/carr-local-pg-ci.abc/data/other
'''
        self.assertEqual([p.pid for p in reaper.select_orphans(fixture, 7200)], [101, 107, 108])

    def test_macos_elapsed_time_and_current_fixture_prefixes(self):
        prefixes = ('carr-activation-repin-', 'carr-jev-aging-', 'dot-independent-restore-',
                    'local-deals-', 'node-pg-life-')
        rows = '\n'.join(f'{100+i} 1 02:00:01 /opt/homebrew/bin/postgres -D /tmp/{prefix}abc/data'
                         for i, prefix in enumerate(prefixes))
        self.assertEqual(len(reaper.select_orphans(rows)), len(prefixes))
        self.assertEqual(reaper.elapsed_seconds('2-03:04:05'), 183845)
        self.assertEqual(reaper.elapsed_seconds('59:59'), 3599)


if __name__ == '__main__':
    unittest.main()
