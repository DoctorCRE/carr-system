#!/usr/bin/env python3
"""Exercise open PR overlap detection through a stubbed gh command."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('pr-overlap.py')

class OverlapTest(unittest.TestCase):
    def run_case(self, pages, paths, fail=''):
        with tempfile.TemporaryDirectory() as tmp:
            gh = Path(tmp, 'gh')
            gh.write_text('''#!/usr/bin/env python3
import json, os, sys
endpoint = sys.argv[2].split('?')[0]
if os.environ.get('FAIL') in endpoint and os.environ.get('FAIL'):
    print('offline', file=sys.stderr); sys.exit(1)
pages = json.loads(os.environ['PAGES'])[endpoint]
if '--paginate' not in sys.argv:
    pages = pages[:1]
if '--slurp' in sys.argv:
    print(json.dumps(pages))
else:
    for page in pages:
        print(json.dumps(page))
''')
            gh.chmod(0o755)
            env = dict(os.environ, PATH=tmp + os.pathsep + os.environ['PATH'], PAGES=json.dumps(pages), FAIL=fail)
            return subprocess.run([sys.executable, str(SCRIPT), 'DoctorCRE/carr-system', *paths], env=env, text=True, capture_output=True)

    def test_overlap_and_directory_boundary(self):
        pages = {'repos/DoctorCRE/carr-system/pulls': [[{'number': 7, 'html_url': 'https://example.test/7'}]], 'repos/DoctorCRE/carr-system/pulls/7/files': [[{'filename': 'ops/a.py'}, {'filename': 'ops-other/b.py'}]]}
        r = self.run_case(pages, ['ops/'])
        self.assertEqual(r.returncode, 1)
        self.assertIn('https://example.test/7', r.stdout)
        self.assertIn('ops/a.py', r.stdout)
        self.assertNotIn('ops-other/b.py', r.stdout)

    def test_no_overlap(self):
        r = self.run_case({'repos/DoctorCRE/carr-system/pulls': [[]]}, ['ops/a.py'])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), 'No overlap.')

    def test_paginated_files(self):
        pages = {'repos/DoctorCRE/carr-system/pulls': [[{'number': 8, 'html_url': 'https://example.test/8'}]], 'repos/DoctorCRE/carr-system/pulls/8/files': [[{'filename': f'other/{i}'} for i in range(100)], [{'filename': 'ops/target.py'}]]}
        r = self.run_case(pages, ['ops/target.py'])
        self.assertEqual(r.returncode, 1)
        self.assertIn('ops/target.py', r.stdout)

    def test_gh_failure_fails_closed(self):
        r = self.run_case({}, ['ops/a.py'], 'pulls')
        self.assertEqual(r.returncode, 3)
        self.assertIn('incomplete', r.stderr.lower())
        self.assertNotIn('No overlap', r.stdout)

    def test_failure_after_overlap_preserves_matches(self):
        pages = {'repos/DoctorCRE/carr-system/pulls': [[{'number': 1, 'html_url': 'https://example.test/1'}, {'number': 2, 'html_url': 'https://example.test/2'}]], 'repos/DoctorCRE/carr-system/pulls/1/files': [[{'filename': 'ops/a.py'}]]}
        r = self.run_case(pages, ['ops/a.py'], '/2/files')
        self.assertEqual(r.returncode, 3)
        self.assertIn('incomplete', r.stderr.lower())

        self.assertIn('https://example.test/1', r.stdout)
        self.assertIn('ops/a.py', r.stdout)
        self.assertNotIn('No overlap', r.stdout)

    def test_paginated_open_prs(self):
        base = 'repos/DoctorCRE/carr-system/pulls'
        prs = [{'number': n, 'html_url': f'https://example.test/{n}'} for n in range(1, 102)]
        pages = {base: [prs[:100], prs[100:]]}
        pages.update({base + f'/{pr["number"]}/files': [[{'filename':
            'ops/target.py' if pr['number'] == 101 else 'other/a.py'}]] for pr in prs})
        r = self.run_case(pages, ['ops/target.py'])
        self.assertEqual(r.returncode, 1)
        self.assertIn('https://example.test/101', r.stdout)

    def test_rename_matches_both_names(self):
        base = 'repos/DoctorCRE/carr-system/pulls'
        pages = {base: [[{'number': 1, 'html_url': 'https://example.test/1'}]],
                 base + '/1/files': [[{'filename': 'ops/new.py',
                                      'previous_filename': 'ops/old.py', 'status': 'renamed'}]]}
        for path in ('ops/old.py', 'ops/new.py'):
            with self.subTest(path=path):
                r = self.run_case(pages, [path])
                self.assertEqual(r.returncode, 1)
                self.assertIn(path, r.stdout)

if __name__ == '__main__':
    unittest.main()
