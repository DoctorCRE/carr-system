import json
import base64
import hashlib
import importlib.util
import os
import pathlib
import subprocess
import unittest
import uuid


SCRIPT = pathlib.Path(__file__).with_name('leak_guard.py')
EXPORTER = SCRIPT.parents[1] / 'ops' / 'export-leak-guard-hashes.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('guard', SCRIPT)
exporter = load('exporter', EXPORTER)


def corpus_hash(salt, *words):
    return hashlib.sha256((salt + '\0' + '\0'.join(words)).encode()).hexdigest()


class LeakGuardTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(os.environ.get('LEAK_GUARD_TEST_ROOT', '/tmp/leak-guard-tests')) / str(uuid.uuid4())
        self.root.mkdir(parents=True, mode=0o700)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.env = dict(os.environ)

    def run_guard(self, *args):
        return subprocess.run(['python3', str(SCRIPT), '--root', str(self.root), *args],
                              env=self.env, capture_output=True, text=True)

    def commit(self):
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Synthetic', '-c',
                        'user.email=64207374+jbookout@users.noreply.github.com', 'commit', '-qm', 'test'],
                       check=True)
        return subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).decode().strip()

    def test_secret_blocks_without_echoing_value(self):
        value = 'ghp_' + base64.b64encode(bytes(range(27))).decode()
        (self.root / 'sample.txt').write_text('token = "' + value + '"\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        result = self.run_guard('--staged', '--generic-only')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('sample.txt:1', result.stdout)
        self.assertNotIn(value, result.stdout + result.stderr)

    def test_private_export_and_generic_pii_are_blocked(self):
        name = 'Fictional' + ' Person'
        salt = '12' * 32
        corpus = {'schema': 'leak-guard-corpus.v2', 'salt': salt, 'lengths': [2],
                  'hashes': [corpus_hash(salt, 'fictional', 'person')]}
        corpus_path = self.root / 'corpus.json'
        corpus_path.write_text(json.dumps(corpus))
        values = [name, 'contact@' + 'fictional-mail.com', '(212)' + ' 234-6789', '843' + ' Imaginary Avenue']
        (self.root / 'sample.txt').write_text('\n'.join(values))
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        result = self.run_guard('--staged', '--corpus', str(corpus_path))
        self.assertEqual(result.returncode, 1, result.stderr)
        for line in range(1, 5):
            self.assertIn(f'sample.txt:{line}', result.stdout)
        for value in values:
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_removed_secret_still_blocks_range_and_pre_push(self):
        (self.root / 'sample.txt').write_text('safe\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        base = self.commit()
        value = 'ghp_' + base64.b64encode(bytes(range(27))).decode()
        (self.root / 'sample.txt').write_text('token="' + value + '"\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        self.commit()
        (self.root / 'sample.txt').write_text('safe again\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        head = self.commit()
        result = self.run_guard('--range', base + '..' + head, '--generic-only')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('sample.txt:1', result.stdout)
        refs = f'refs/heads/feature {head} refs/heads/feature {base}\n'
        hook = subprocess.run(['python3', str(SCRIPT), '--root', str(self.root), '--pre-push',
                               '--generic-only'], input=refs, env=self.env, capture_output=True, text=True)
        self.assertEqual(hook.returncode, 1, hook.stderr)
        self.assertNotIn(value, result.stdout + hook.stdout + hook.stderr)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), '-c', 'user.name=Synthetic', '-c',
                                        'user.email=synthetic@example.invalid', *args]).decode().strip()

    def test_merging_main_scans_only_what_the_branch_introduces(self):
        (self.root / 'sample.txt').write_text('safe\n')
        self.git('add', 'sample.txt')
        self.commit()
        self.git('branch', '-M', 'main')
        self.git('checkout', '-qb', 'feature')
        (self.root / 'feature.txt').write_text('feature work\n')
        self.git('add', 'feature.txt')
        before_merge = self.commit()
        self.git('checkout', '-q', 'main')
        value = 'ghp_' + base64.b64encode(bytes(range(27))).decode()
        (self.root / 'main-side.txt').write_text('token="' + value + '"\n')
        self.git('add', 'main-side.txt')
        main = self.commit()
        self.git('checkout', '-q', 'feature')
        self.git('merge', '-q', '--no-edit', 'main')
        head = self.git('rev-parse', 'HEAD')
        result = self.run_guard('--range', main + '..' + head, '--generic-only')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        refs = f'refs/heads/feature {head} refs/heads/feature {before_merge}\n'
        self.git('update-ref', 'refs/remotes/origin/main', main)
        # ops/ci.sh check_secret runs this when the pre-push hook sets CARR_CI_RANGE.
        result = self.run_guard('--range', before_merge + '..' + head, '--generic-only')
        self.assertEqual(result.returncode, 0, 'ci.sh pre-push range: ' + result.stdout + result.stderr)
        hook = subprocess.run(['python3', str(SCRIPT), '--root', str(self.root), '--pre-push', '--generic-only'],
                              input=refs, env=self.env, capture_output=True, text=True)
        self.assertEqual(hook.returncode, 0, hook.stdout + hook.stderr)
        new_ref = subprocess.run(['python3', str(SCRIPT), '--root', str(self.root), '--pre-push', '--generic-only'],
                                 input=refs.replace(before_merge, '0' * 40), env=self.env, capture_output=True,
                                 text=True)
        self.assertEqual(new_ref.returncode, 0, new_ref.stdout + new_ref.stderr)

    def test_merge_resolution_content_is_still_scanned(self):
        (self.root / 'sample.txt').write_text('safe\n')
        self.git('add', 'sample.txt')
        self.commit()
        self.git('branch', '-M', 'main')
        self.git('checkout', '-qb', 'feature')
        (self.root / 'feature.txt').write_text('feature work\n')
        self.git('add', 'feature.txt')
        self.commit()
        self.git('checkout', '-q', 'main')
        (self.root / 'main.txt').write_text('main work\n')
        self.git('add', 'main.txt')
        main = self.commit()
        self.git('checkout', '-q', 'feature')
        self.git('merge', '-q', '--no-commit', 'main')
        value = 'ghp_' + base64.b64encode(bytes(range(27))).decode()
        (self.root / 'sample.txt').write_text('token="' + value + '"\n')
        self.git('add', 'sample.txt')
        head = self.commit()
        result = self.run_guard('--range', main + '..' + head, '--generic-only')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('sample.txt:1', result.stdout)
        self.assertNotIn(value, result.stdout + result.stderr)

    def test_allowlist_is_bound_to_fixture_bytes_and_requires_reason(self):
        raw = ('test@' + 'fictional-mail.com\n').encode()
        (self.root / 'fixture.txt').write_bytes(raw)
        subprocess.run(['git', '-C', str(self.root), 'add', 'fixture.txt'], check=True)
        allow = self.root / 'allow.json'
        entry = {'path': 'fixture.txt', 'sha256': hashlib.sha256(raw).hexdigest(),
                 'rule': 'pii-email', 'reason': 'Synthetic email for detector qualification.'}
        allow.write_text(json.dumps({'schema': 'leak-guard-allowlist.v1', 'entries': [entry]}))
        result = self.run_guard('--staged', '--generic-only', '--allowlist', str(allow))
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.root / 'fixture.txt').write_bytes(raw + b'changed\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'fixture.txt'], check=True)
        result = self.run_guard('--staged', '--generic-only', '--allowlist', str(allow))
        self.assertEqual(result.returncode, 1, result.stderr)
        entry['reason'] = ''
        allow.write_text(json.dumps({'schema': 'leak-guard-allowlist.v1', 'entries': [entry]}))
        result = self.run_guard('--staged', '--generic-only', '--allowlist', str(allow))
        self.assertEqual(result.returncode, 2, result.stderr)

    def export(self, values, excluded=()):
        output = self.root / 'corpus.json'
        counts = exporter.export(values, list(excluded), output)
        return counts, output.read_text()

    def test_hash_export_contains_no_original_values(self):
        values = ['Fictional' + ' Person', 'contact@' + 'fictional-mail.com', '843' + ' Imaginary Avenue']
        counts, text = self.export(values)
        corpus = json.loads(text)
        self.assertEqual(counts, (3, 0, 3))
        self.assertEqual(corpus['schema'], 'leak-guard-corpus.v2')
        self.assertEqual(len(corpus['hashes']), 3)
        for value in values:
            self.assertNotIn(value, text)

    def test_non_latin_name_export_and_detection_preserve_letters(self):
        value = chr(0x6f22) + chr(0x5b57) + ' ' + chr(0x540d) + chr(0x524d)
        self.export([value])
        corpus = self.root / 'corpus.json'
        (self.root / 'sample.txt').write_text(value + '\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        result = self.run_guard('--staged', '--corpus', str(corpus))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('sample.txt:1 client-data', result.stdout)
        self.assertTrue(value not in result.stdout + result.stderr, 'Matched value escaped diagnostics')

    def test_client_data_needs_more_than_one_token(self):
        salt = '12' * 32
        corpus = self.root / 'corpus.json'
        corpus.write_text(json.dumps({'schema': 'leak-guard-corpus.v2', 'salt': salt, 'lengths': [1, 2],
                                      'hashes': [corpus_hash(salt, 'ordinary'),
                                                 corpus_hash(salt, 'fictional', 'person')]}))
        (self.root / 'sample.py').write_text('ordinary = 1\n' + 'Fictional' + ' Person\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.py'], check=True)
        result = self.run_guard('--staged', '--corpus', str(corpus))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn('sample.py:1', result.stdout)
        self.assertIn('sample.py:2 client-data', result.stdout)

    def test_reserved_domains_and_prose_are_not_contact_data(self):
        ignored = ['fixture@' + 'example.invalid', 'user.email=64207374+jbookout@' + 'users.noreply.github.com',
                   '*@' + '127.0.0.1:*', 'probe@' + 'example.com', '2026-08-06 and ran in exactly one ' + 'place']
        flagged = ['contact@' + 'fictional-mail.com', '843' + ' Imaginary Avenue']
        (self.root / 'sample.txt').write_text('\n'.join(ignored + flagged) + '\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        result = self.run_guard('--staged', '--generic-only')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, 'sample.txt:6 pii-email\nsample.txt:7 pii-address\n')

    def test_export_indexes_only_possible_lengths_for_each_hashed_start(self):
        corpus = json.loads(self.export(['Fictional', 'Imaginary' + ' Customer'])[1])
        self.assertEqual(sorted(corpus['start_lengths'].values()), [[1], [2]])
        self.assertTrue(all(len(key) == 64 for key in corpus['start_lengths']))

    def test_utf16_secret_and_sensitive_filename_are_blocked(self):
        value = 'ghp_' + base64.b64encode(bytes(range(27))).decode()
        (self.root / 'sample.txt').write_bytes(('token="' + value + '"\n').encode('utf-16'))
        filename = 'wrapped\n' + value
        (self.root / filename).write_text('safe\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt', filename], check=True)
        result = self.run_guard('--staged', '--generic-only')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(value not in result.stdout + result.stderr, 'Matched filename value escaped diagnostics')
        self.assertIn('sample.txt:1', result.stdout)
        self.assertIn('[redacted-path:', result.stdout)

    def test_missing_corpus_and_binary_refuse_without_values(self):
        (self.root / 'asset.dat').write_bytes(b'opaque\0content')
        subprocess.run(['git', '-C', str(self.root), 'add', 'asset.dat'], check=True)
        result = self.run_guard('--staged', '--generic-only')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('asset.dat:1 opaque-binary', result.stdout)
        result = self.run_guard('--staged', '--corpus', str(self.root / 'missing.json'))
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_private_history_report_contains_counts_and_paths_only(self):
        (self.root / '.gitignore').write_text('out/\n')
        (self.root / 'sample.txt').write_text('contact@' + 'fictional-mail.com\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '.gitignore', 'sample.txt'], check=True)
        self.commit()
        report = self.root / 'out/history.json'
        result = self.run_guard('--history', '--generic-only', '--report', str(report))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        data = json.loads(report.read_text())
        self.assertEqual(set(data), {'schema', 'counts', 'files'})
        self.assertEqual(data['counts'], {'pii-email': 1})
        self.assertEqual(data['files'], {'sample.txt': 1})

    def test_client_exception_only_allows_its_specific_hash(self):
        salt = '12' * 32
        first = 'Fictional' + ' Person'
        second = 'Imaginary' + ' Customer'
        hashes = [corpus_hash(salt, 'fictional', 'person'), corpus_hash(salt, 'imaginary', 'customer')]
        corpus = self.root / 'corpus.json'
        corpus.write_text(json.dumps({'schema': 'leak-guard-corpus.v2', 'salt': salt, 'lengths': [2], 'hashes': hashes}))
        raw = (first + '\n' + second + '\n').encode()
        (self.root / 'fixture.txt').write_bytes(raw)
        subprocess.run(['git', '-C', str(self.root), 'add', 'fixture.txt'], check=True)
        allow = self.root / 'allow.json'
        allow.write_text(json.dumps({'schema': 'leak-guard-allowlist.v1', 'entries': [{
            'path': 'fixture.txt', 'sha256': hashlib.sha256(raw).hexdigest(), 'rule': 'client-data',
            'value_sha256': hashes[0], 'reason': 'Synthetic overlap for qualification.'}]}))
        result = self.run_guard('--staged', '--corpus', str(corpus), '--allowlist', str(allow))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn('fixture.txt:1', result.stdout)
        self.assertIn('fixture.txt:2', result.stdout)

    def test_git_push_hook_refuses_seeded_secret_before_receiver_gets_it(self):
        # A copy of the guard reads a synthetic corpus beside it, not the committed one.
        (self.root / 'security').mkdir()
        (self.root / 'security' / 'leak_guard.py').write_bytes(SCRIPT.read_bytes())
        (self.root / 'security' / 'client-hashes.json').write_text(json.dumps(guard.make_corpus(['Fictional Person'], '12' * 32)))
        (self.root / '.git' / 'info' / 'exclude').write_text('security/\n')
        # The committed hook carries no executable bit; Git runs only executable hooks.
        hooks = self.root / '.git' / 'leak-guard-hooks'
        hooks.mkdir()
        (hooks / 'pre-push').write_bytes(SCRIPT.with_name('pre-push').read_bytes())
        (hooks / 'pre-push').chmod(0o700)
        subprocess.run(['git', '-C', str(self.root), 'config', 'core.hooksPath', str(hooks)], check=True)
        value = 'ghp_' + base64.b64encode(bytes(range(27))).decode()
        (self.root / 'sample.txt').write_text('token="' + value + '"\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        self.commit()
        receiver = self.root / 'out/_to_delete/receiver.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(receiver)], check=True)
        result = subprocess.run(['git', '-C', str(self.root), 'push', str(receiver), 'HEAD:refs/heads/seeded'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('sample.txt:1', result.stdout + result.stderr)
        self.assertNotIn(value, result.stdout + result.stderr)
        refs = subprocess.check_output(['git', '-C', str(receiver), 'for-each-ref'])
        self.assertEqual(refs, b'')

    def test_large_scan_bounds_each_gitleaks_input_batch(self):
        from unittest.mock import patch
        run = subprocess.run
        sizes = []
        def observed_run(*args, **kwargs):
            if 'input' in kwargs:
                sizes.append(len(kwargs['input']))
            return run(*args, **kwargs)
        source = [guard.Change(f'file-{i}.txt', b'x' * 1048576, None, True) for i in range(20)]
        with patch.object(guard.subprocess, 'run', side_effect=observed_run):
            self.assertEqual(guard.scan(self.root, source, None, set()), 0)
        self.assertGreater(len(sizes), 1)
        self.assertLessEqual(max(sizes), 9 * 1048576)

    def test_long_encoded_text_completes_without_quadratic_pii_search(self):
        (self.root / 'sample.txt').write_text('/' * 60000 + '\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'sample.txt'], check=True)
        result = subprocess.run(['python3', str(SCRIPT), '--root', str(self.root),
                                 '--staged', '--generic-only'], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


    def secret(self):
        return 'ghp_' + base64.b64encode(bytes(range(27))).decode()

    def write_commit(self, path, text):
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text)
        self.git('add', path)
        return self.commit()

    def test_only_added_lines_are_judged(self):
        value = self.secret()
        base = self.write_commit('config.txt', 'token="' + value + '"\nmode = old\n')
        edited = self.write_commit('config.txt', 'token="' + value + '"\nmode = new\n')
        result = self.run_guard('--range', base + '..' + edited, '--generic-only')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / 'config.txt').write_text('token="' + value + '"\nmode = staged\n')
        self.git('add', 'config.txt')
        self.assertEqual(self.run_guard('--staged', '--generic-only').returncode, 0)
        head = self.write_commit('config.txt', 'token="' + value + '"\nmode = new\nother="' + value + '"\n')
        result = self.run_guard('--range', base + '..' + head, '--generic-only')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, 'config.txt:3 github-pat\n')

    def test_merge_judges_lines_new_against_every_parent(self):
        value = self.secret()
        self.write_commit('shared.txt', 'first\n\n\n\nlast\n')
        self.git('branch', '-M', 'main')
        self.git('checkout', '-qb', 'feature')
        self.write_commit('shared.txt', 'first edited\n\n\n\nlast\n')
        self.git('checkout', '-q', 'main')
        main = self.write_commit('shared.txt', 'first\n\n\n\nlast\ntoken="' + value + '"\n')
        self.git('checkout', '-q', 'feature')
        self.git('merge', '-q', '--no-edit', 'main')
        head = self.git('rev-parse', 'HEAD')
        result = self.run_guard('--range', main + '..' + head, '--generic-only')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_client_data_in_a_path_counts_only_where_the_path_is_introduced(self):
        corpus = self.root / 'corpus.json'
        corpus.write_text(json.dumps(guard.make_corpus(['Fictional' + ' Person'], '12' * 32)))
        self.git('add', 'corpus.json')
        name = 'notes/fictional-' + 'person.txt'
        base = self.write_commit(name, 'first\n')
        edited = self.write_commit(name, 'second\n')
        result = self.run_guard('--range', base + '..' + edited, '--corpus', str(corpus), '--generic-only')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_guard('--range', base + '..' + edited, '--corpus', str(corpus))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        added = self.write_commit('archive/fictional-' + 'person.txt', 'second\n')
        result = self.run_guard('--range', edited + '..' + added, '--corpus', str(corpus))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertRegex(result.stdout, r'^\[redacted-path:[0-9a-f]{12}\]:1 client-data\n$')

    def matches(self, corpus, text):
        return [rule for _, rule, _ in guard.pii_findings(text, guard.prepare_corpus(corpus))
                if rule == 'client-data']

    def test_tokens_are_hashed_with_a_separator(self):
        salt = '12' * 32
        # Without the start index every window is hashed, as with an older corpus.
        corpus = guard.validate_corpus({'schema': 'leak-guard-corpus.v2', 'salt': salt, 'lengths': [2, 3],
                                        'hashes': [corpus_hash(salt, '0000000'),
                                                   corpus_hash(salt, 'imaginary', 'customer')]})
        self.assertEqual(self.matches(corpus, 'score 000 0 000\n'), [])
        self.assertEqual(self.matches(corpus, 'Imagin Arycustomer\n'), [])
        self.assertEqual(self.matches(corpus, 'Imaginary' + ' Customer\n'), ['client-data'])

    def test_only_phone_shaped_digit_runs_are_client_data(self):
        salt = '12' * 32
        corpus = guard.make_corpus(['(212)' + ' 234-6789', 'Fictional' + ' Person'], salt)
        corpus['hashes'] = sorted(corpus['hashes'] + [corpus_hash(salt, '2', '0', '0'), corpus_hash(salt, '000', '0', '000')])
        corpus['lengths'] = sorted(set(corpus['lengths']) | {3})
        del corpus['starts'], corpus['start_lengths']
        corpus = guard.validate_corpus(corpus)
        self.assertEqual(self.matches(corpus, '"version": "2.0.0"\nscores 000 0 000\n'), [])
        self.assertEqual(self.matches(corpus, 'call 212.234.' + '6789\n'), ['client-data'])

    def test_corpus_before_separator_hashing_is_refused_without_values(self):
        corpus = guard.make_corpus(['Fictional' + ' Person'], '12' * 32)
        corpus['schema'] = 'leak-guard-corpus.v1'
        path = self.root / 'corpus.json'
        path.write_text(json.dumps(corpus))
        (self.root / 'sample.txt').write_text('safe\n')
        self.git('add', 'sample.txt')
        result = self.run_guard('--staged', '--corpus', str(path))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('ops/export-leak-guard-hashes.py', result.stderr)
        self.assertNotIn(corpus['salt'], result.stdout + result.stderr)
        self.assertNotIn(corpus['hashes'][0], result.stdout + result.stderr)

    def test_export_drops_excluded_staff_values(self):
        excluded = ['Fictional' + ' Partner', 'Imaginary']
        values = ['Fictional' + ' Partner', 'Dr. Fictional' + ' Partner DDS', 'partner@' + 'fictional-mail.com',
                  'Imaginary', 'Imaginary' + ' Customer', 'Synthetic' + ' Client']
        counts, text = self.export(values, excluded)
        self.assertEqual(counts, (6, 3, 3))
        for value in values + excluded:
            self.assertNotIn(value, text)
        corpus = guard.validate_corpus(json.loads(text))
        self.assertEqual(self.matches(corpus, 'Fictional' + ' Partner\n'), [])
        self.assertEqual(self.matches(corpus, 'Imaginary' + ' Customer\n'), ['client-data'])
        self.assertEqual(self.matches(corpus, 'Synthetic' + ' Client\n'), ['client-data'])

    def test_export_salt_is_kept_only_within_a_schema(self):
        output = self.root / 'corpus.json'
        output.write_text(json.dumps({'schema': 'leak-guard-corpus.v1', 'salt': '34' * 32}))
        exporter.export(['Fictional' + ' Person'], [], output)
        fresh = json.loads(output.read_text())['salt']
        self.assertNotEqual(fresh, '34' * 32)
        exporter.export(['Fictional' + ' Person'], [], output)
        self.assertEqual(json.loads(output.read_text())['salt'], fresh)

    def test_exclusion_list_must_be_untracked_under_out(self):
        (self.root / '.gitignore').write_text('out/\n')
        listed = self.root / 'out' / 'staff.txt'
        listed.parent.mkdir()
        listed.write_text('Fictional' + ' Partner\n')
        self.assertEqual(exporter.exclusion_file(self.root, listed), ['Fictional' + ' Partner'])
        outside = self.root / 'staff.txt'
        outside.write_text('Fictional' + ' Partner\n')
        with self.assertRaisesRegex(exporter.Refusal, 'under out/'):
            exporter.exclusion_file(self.root, outside)
        self.git('add', '-f', 'out/staff.txt')
        with self.assertRaisesRegex(exporter.Refusal, 'tracked'):
            exporter.exclusion_file(self.root, listed)

if __name__ == '__main__':
    unittest.main()
