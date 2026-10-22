"""Exercise batch composition against synthetic Git repositories."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from merge_queue.compose import compose


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='merge-queue-compose-test.')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'source'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic Builder')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.base = self.commit({'source.txt': 'base\n'}, 'Synthetic base')

    def git(self, *args, check=True):
        result = subprocess.run(['git', '-C', str(self.repo), *args],
                                capture_output=True, text=True, check=check)
        return result.stdout.strip()

    def commit(self, files, message):
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git('add', '--', *files)
        message_file = Path(self.temp.name) / 'message'
        message_file.write_text(message + '\n')
        self.git('commit', '-q', '-F', str(message_file))
        return self.git('rev-parse', 'HEAD')

    def branch(self, name, start=None):
        self.git('checkout', '-q', '-b', name, start or self.base)

    def request(self, *heads, repo='jbookout/software-factory'):
        return dict(source=str(self.repo), repo=repo, base=self.base, branch='candidate',
                    members=[dict(pr=101 + i, head=head) for i, head in enumerate(heads)])

    def assert_chain(self, result, request):
        parent = self.base
        self.assertEqual(len(result['members']), len(request['members']))
        for actual, expected in zip(result['members'], request['members']):
            self.assertEqual((actual['pr'], actual['head']), (expected['pr'], expected['head']))
            self.assertEqual(self.git('show', '-s', '--format=%P', actual['commit']), parent)
            self.assertEqual(self.git('rev-parse', actual['commit'] + '^{tree}'), actual['tree'])
            self.assertIn(f"#{expected['pr']}", self.git('show', '-s', '--format=%B', actual['commit']))
            parent = actual['commit']
        self.assertEqual(result['candidate'], parent)
        self.assertEqual(result['tree'], result['members'][-1]['tree'])
        self.assertEqual(self.git('rev-parse', 'candidate'), parent)
        self.assertEqual(int(self.git('rev-list', '--count', f'{self.base}..{parent}')),
                         len(request['members']))
        for member in request['members']:
            ancestors = self.git('rev-list', parent).splitlines()
            self.assertNotIn(member['head'], ancestors)

    def test_independent_members_form_ordered_squashes_with_prefix_trees(self):
        self.branch('first')
        self.commit({'first.txt': 'first draft\n'}, 'First draft')
        first = self.commit({'first.txt': 'first final\n'}, 'First final')
        first_tree = self.git('rev-parse', 'HEAD^{tree}')
        self.branch('second')
        second = self.commit({'second.txt': 'second\n'}, 'Second')
        self.branch('expected', first)
        self.commit({'second.txt': 'second\n'}, 'Expected combined tree')
        final_tree = self.git('rev-parse', 'HEAD^{tree}')
        request = self.request(first, second)

        result = compose(request, lambda repo: self.fail('unexpected regeneration'))

        self.assert_chain(result, request)
        self.assertEqual([m['tree'] for m in result['members']], [first_tree, final_tree])

    def test_stacked_member_uses_original_ancestry_without_publishing_it(self):
        self.branch('first')
        first = self.commit({'source.txt': 'first\n'}, 'First')
        first_tree = self.git('rev-parse', 'HEAD^{tree}')
        self.branch('second', first)
        second = self.commit({'source.txt': 'second\n'}, 'Stacked second')
        second_tree = self.git('rev-parse', 'HEAD^{tree}')
        request = self.request(first, second)

        result = compose(request, lambda repo: self.fail('unexpected regeneration'))

        self.assert_chain(result, request)
        self.assertEqual([m['tree'] for m in result['members']], [first_tree, second_tree])
        self.assertEqual(self.git('show', result['candidate'] + ':source.txt'), 'second')

    def test_generated_conflict_regenerates_once_into_final_member_only(self):
        generated = 'db/schema.sql'
        self.base = self.commit({generated: 'base generated\n'}, 'Generated base')
        self.branch('first')
        first = self.commit({'first.txt': 'first\n', generated: 'first generated\n'}, 'First')
        first_tree = self.git('rev-parse', 'HEAD^{tree}')
        self.branch('second')
        second = self.commit({'second.txt': 'second\n', generated: 'second generated\n'}, 'Second')
        self.branch('expected', first)
        self.commit({'second.txt': 'second\n', generated: 'final generated\n',
                     'new-generated.txt': 'new\n'}, 'Expected regenerated tree')
        final_tree = self.git('rev-parse', 'HEAD^{tree}')
        request = self.request(first, second, repo='jbookout/carr-system')
        regenerations = []

        def regenerate(repo):
            regenerations.append(repo)
            self.assertEqual((repo / 'first.txt').read_text(), 'first\n')
            self.assertEqual((repo / 'second.txt').read_text(), 'second\n')
            self.assertEqual((repo / generated).read_text(), 'first generated\n')
            (repo / generated).write_text('final generated\n')
            (repo / 'new-generated.txt').write_text('new\n')

        result = compose(request, regenerate)

        self.assertEqual(len(regenerations), 1)
        self.assert_chain(result, request)
        self.assertEqual([m['tree'] for m in result['members']], [first_tree, final_tree])

    def test_source_conflict_names_member_and_publishes_no_candidate(self):
        self.branch('first')
        first = self.commit({'first.txt': 'first\n'}, 'First')
        self.branch('second')
        second = self.commit({'source.txt': 'second\n'}, 'Second')
        self.branch('third')
        third = self.commit({'source.txt': 'third\n'}, 'Third conflict')
        request = self.request(first, second, third, repo='jbookout/carr-system')

        result = compose(request, lambda repo: self.fail('conflict must stop regeneration'))

        self.assertEqual(result, dict(request=request, conflict=103, detail='source conflict: source.txt'))
        self.assertEqual(self.git('show-ref', '--verify', 'refs/heads/candidate', check=False), '')


if __name__ == '__main__':
    unittest.main()
