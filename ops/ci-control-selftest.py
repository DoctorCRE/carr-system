#!/usr/bin/env python3
"""Exercise hosted event isolation and class conclusions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def workflow(filename="ci.yml"):
    return json.loads(subprocess.check_output(['node', '-e',
        "const y=require('js-yaml'),fs=require('fs');console.log(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))",
        str(ROOT / '.github/workflows' / filename)], cwd=ROOT / 'mcp-server', text=True))


def release_download_violations(root):
    import re
    violations = []
    for folder in ('.github', 'ops', 'tools'):
        for path in (root / folder).rglob('*'):
            if not path.is_file() or path.suffix not in ('.py', '.sh', '.yml', '.yaml', '.js', '.mjs', '.ts'):
                continue
            if any(part in ('node_modules', '__pycache__', '.venv') for part in path.parts):
                continue
            name = path.relative_to(root).as_posix()
            if name in ('ops/ci-control-selftest.py', 'ops/doctorcre-artifact-selftest.py'):
                continue
            text = path.read_text()
            if name == 'tools/doctorcre-artifact.py':
                text = text.replace('f"https://github.com/{pin[\'repository\']}/releases/download/{pin[\'release_tag\']}"', "", 1)
            if name == 'ops/gitleaks.py':
                text = text.replace("f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{target}.tar.gz'", "", 1)
            urls = re.findall(r'https://github[.]com/[^\s]+/releases/(?:latest/)?download[^\s"\']*', text)
            violations.extend(name + ': uncached release download' for _ in urls)
    return violations


class Controls(unittest.TestCase):
    def test_python39_uses_digest_verified_distribution(self):
        job = workflow()['jobs']['launchd-python39']
        self.assertRegex(job.get('container', {}).get('image', ''),
                         r'^python:3[.]9[.]25-slim-bookworm@sha256:[0-9a-f]{64}$')
        for step in job['steps']:
            self.assertNotIn('actions/cache@', step.get('uses', ''))
            self.assertNotIn('actions/setup-python@', step.get('uses', ''))
        self.assertEqual(job['steps'][-1]['run'],
                         'python -I tools/dictation-rig/tests/test_python39_imports.py')

    def test_release_policy_rejects_test_named_operational_sources(self):
        for name in ('.github/workflows/new.yml', '.github/workflows/test-download.yml',
                     '.github/workflows/release-selftest.yml', 'ops/test-bootstrap.py',
                     'ops/bootstrap-selftest.py', 'tools/installer.sh', 'ops/gitleaks.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / name
                path.parent.mkdir(parents=True)
                path.write_text('curl -L https://github.com/gitleaks/gitleaks/releases/download/v999/new.tar.gz')
                self.assertEqual(release_download_violations(root),
                                 [name + ': uncached release download'])

    def test_release_policy_allows_only_one_pinned_expression(self):
        for name, expression in (
            ('ops/gitleaks.py', "f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/gitleaks_{VERSION}_{target}.tar.gz'"),
            ('tools/doctorcre-artifact.py', 'f"https://github.com/{pin[\'repository\']}/releases/download/{pin[\'release_tag\']}"'),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / name
                path.parent.mkdir(parents=True)
                path.write_text('url = ' + expression)
                self.assertEqual(release_download_violations(root), [])
                path.write_text('url = ' + expression + '\nextra = ' + expression)
                self.assertEqual(release_download_violations(root),
                                 [name + ': uncached release download'])

    def test_release_download_policy(self):
        self.assertEqual(release_download_violations(ROOT), [])
        action = workflow('../actions/ci-downloads/action.yml')
        archive = next(step for step in action['runs']['steps']
                       if step.get('with', {}).get('path') == '~/.cache/leak-guard/**/release.tar.gz')
        self.assertIn('actions/cache@', archive['uses'])
        self.assertEqual(archive['with']['key'],
                         "gitleaks-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('ops/gitleaks.py') }}")
        self.assertNotIn('restore-keys', archive['with'])
        product = next(step for step in action['runs']['steps']
                       if step.get('with', {}).get('path') == '~/.cache/doctorcre-artifact')
        self.assertIn('actions/cache@', product['uses'])
        self.assertEqual(product['with']['key'],
                         "doctorcre-artifact-${{ hashFiles('ops/config/doctorcre-artifact.v1.json', 'tools/doctorcre-artifact.py') }}")
        self.assertNotIn('restore-keys', product['with'])
        for filename in ('ci.yml', 'main-canary.yml', 'unit-shards.yml'):
            for job in workflow(filename)['jobs'].values():
                steps = job.get('steps', [])
                for i, step in enumerate(steps):
                    if 'ops/gitleaks.py --install' in step.get('run', ''):
                        self.assertTrue(any(s.get('uses') == './.github/actions/ci-downloads' for s in steps[:i]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github/workflows').mkdir(parents=True)
            (root / '.github/workflows/new.yml').write_text(
                'run: curl https://github.com/vendor/tool/releases/download/v1/tool.tar.gz')
            self.assertEqual(release_download_violations(root),
                             ['.github/workflows/new.yml: uncached release download'])
            (root / 'tools').mkdir()
            (root / 'tools/doctorcre-artifact.py').write_text(
                "base = f\"https://github.com/{pin['repository']}/releases/download/{pin['release_tag']}\"\n"
                'extra = "https://github.com/vendor/other/releases/download/v1/other.tar"')
            self.assertIn('tools/doctorcre-artifact.py: uncached release download',
                          release_download_violations(root))


    def test_gitleaks_download_states(self):
        import gitleaks
        import hashlib
        import io
        import tarfile
        from unittest.mock import patch
        from urllib.error import HTTPError
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as bundle:
            member = tarfile.TarInfo('gitleaks')
            member.size = 7
            bundle.addfile(member, io.BytesIO(b'scanner'))
        fixture_archive_bytes = data.getvalue()
        outage = HTTPError('https://github.com', 504, 'outage', {}, None)
        for mode in ('cold', 'cached', 'transient', 'exhausted', 'tampered', 'bad-download'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                archive = home / '.cache/leak-guard' / gitleaks.VERSION / 'linux_x64/release.tar.gz'
                if mode in ('cached', 'tampered'):
                    archive.parent.mkdir(parents=True)
                    archive.write_bytes(fixture_archive_bytes if mode == 'cached' else b'tampered')
                effects = ([outage, io.BytesIO(fixture_archive_bytes)] if mode == 'transient' else
                           [outage] * 3 if mode == 'exhausted' else
                           [io.BytesIO(b'invalid' if mode == 'bad-download' else fixture_archive_bytes)])
                with patch.object(Path, 'home', return_value=home), patch.object(gitleaks.platform, 'machine', return_value='x86_64'), patch.object(gitleaks.platform, 'system', return_value='Linux'), patch.dict(gitleaks.ARCHIVES, {'linux_x64': hashlib.sha256(fixture_archive_bytes).hexdigest()}), patch.object(gitleaks.urllib.request, 'urlopen', side_effect=effects) as network, patch.object(gitleaks.time, 'sleep') as sleep:
                    if mode in ('exhausted', 'tampered', 'bad-download'):
                        with self.assertRaises((OSError, ValueError)):
                            gitleaks.binary(install=True)
                        self.assertFalse((archive.parent / 'gitleaks').exists())
                    else:
                        executable = gitleaks.binary(install=True)
                        self.assertEqual(executable.read_bytes(), b'scanner')
                        self.assertEqual(gitleaks.binary(), executable)
                    self.assertEqual(network.call_count, {'cached': 0, 'tampered': 0, 'transient': 2, 'exhausted': 3}.get(mode, 1))
                    self.assertEqual([c.args[0] for c in sleep.call_args_list],
                                     [2, 4] if mode == 'exhausted' else [2] if mode == 'transient' else [])

    def test_full_gates_install_the_shared_pinned_scanner_before_checks(self):
        jobs = workflow()['jobs']
        install = next(step for step in jobs['secret']['steps']
                       if step.get('run') == 'python3 ops/gitleaks.py --install')
        steps = jobs['classes']['steps']
        checks = next(step for step in steps if step.get('name') == "Run this group's check classes")
        self.assertIn(install, steps[:steps.index(checks)])

    def test_node_install_reuses_downloads_without_duplicating_dependency_audit(self):
        wf = workflow()
        setup = next(step for step in wf['jobs']['classes']['steps']
                     if 'actions/setup-node@' in step.get('uses', ''))
        self.assertFalse(setup['with']['package-manager-cache'])
        cache = workflow('../actions/ci-downloads/action.yml')
        self.assertIn('$ImageOS-$ImageVersion', cache['runs']['steps'][0]['run'])
        npm, pip = cache['runs']['steps'][1:3]
        for step, locks in ((npm, ('.nvmrc', 'mcp-server/package-lock.json', 'practice-plugin/package-lock.json')),
                            (pip, ('.python-version', 'requirements.lock'))):
            key = step['with']['key']
            self.assertIn('${{ runner.os }}-${{ runner.arch }}-${{ steps.image.outputs.identity }}', key)
            for lock in locks:
                self.assertIn(lock, key)
            self.assertNotIn('restore-keys', step['with'])
        for filename in ('ci.yml', 'main-canary.yml', 'unit-shards.yml'):
            for job in workflow(filename)['jobs'].values():
                steps = job.get('steps', [])
                setups = [step for step in steps if 'actions/setup-node@' in step.get('uses', '')
                          or 'actions/setup-python@' in step.get('uses', '')]
                if not setups:
                    continue
                with self.subTest(workflow=filename, job=job.get('name')):
                    self.assertTrue(all('cache' not in step['with'] for step in setups))
                    restore = next(step for step in steps if step.get('uses') == './.github/actions/ci-downloads')
                    self.assertGreater(steps.index(restore), steps.index(setups[-1]))
                    if not job.get('container'):
                        self.assertEqual(restore.get('if'), setups[-1].get('if'))
        install = next(step for step in wf['jobs']['classes']['steps']
                       if step.get('name') == 'Install Node dependencies')
        commands = install['run'].splitlines()
        self.assertEqual(commands, ['npm --prefix mcp-server ci --no-audit',
                                    'npm --prefix practice-plugin ci --no-audit'])
        self.assertIn('dependency', ' '.join(wf['jobs']['classes']['strategy']['matrix']['classes']).split())
        # The strict class, rather than install's informational audit, retains
        # the existing vulnerability policy and fails on unavailable reports.
        self.assertIn('["npm", "audit", "--omit=dev", "--audit-level=high", "--json"]',
                      (ROOT / 'ops/ci-dep-check.py').read_text())

    def test_class_jobs_do_not_wait_for_a_scope_runner(self):
        job = workflow()['jobs']['classes']
        self.assertNotIn('scope', job.get('needs', []))
        migration = next(step for step in job['steps'] if step.get('id') == 'scope')
        self.assertIn('ops/db-acceptance-scope.py', migration['run'])
        self.assertTrue(migration['continue-on-error'])
        self.assertEqual(job['strategy']['matrix']['classes'][0], 'migration')
        for step in job['steps']:
            self.assertNotIn('needs.scope', json.dumps(step))

    def test_hosted_database_lanes_require_image_without_acquisition(self):
        import re
        from ci_unit_shards import POSTGRES_PROFILE, MAX_COUNT
        self.assertEqual(POSTGRES_PROFILE, 'both')
        self.assertEqual(MAX_COUNT, 16)
        lanes = {'ci.yml': ('fast', 'classes', 'migration-fixtures'),
                 'main-canary.yml': ('classes',), 'unit-shards.yml': ('unit',)}
        images = set()
        for filename, names in lanes.items():
            jobs = workflow(filename)['jobs']
            for name in names:
                job = jobs[name]
                image = job['container']['image']
                self.assertRegex(image, r'ghcr.io/jbookout/software-factory/ci-postgres@sha256:[0-9a-f]{64}')
                images.add(re.search(r'ghcr.io/[^\s\']+', image).group())
                self.assertEqual(job['env']['CARR_CI_POSTGRES_IMAGE'], POSTGRES_PROFILE)
                self.assertEqual(job['defaults']['run']['shell'], 'bash')
                if name == 'classes':
                    for selected in ('unit', 'gates', 'migration'):
                        self.assertIn("matrix.classes == '" + selected + "'", image)
                checks = [step for step in job['steps'] if 'ci-apt' in step.get('uses', '')
                          or 'ci-postgres.sh' in step.get('run', '')]
                self.assertTrue(checks)
                for step in checks:
                    if 'run' in step:
                        self.assertEqual(step['timeout-minutes'], 5)
            for job in jobs.values():
                for step in job.get('steps', []):
                    command = step.get('run', '')
                    self.assertNotRegex(command, r'apt(?:-get)?|curl.*postgres|wget.*postgres')
                    if 'ci-zsh.sh' in command:
                        self.assertEqual(step.get('if'), "matrix.classes != 'unit' && matrix.classes != 'gates' && matrix.classes != 'migration' && matrix.classes != 'dependency'")
        self.assertEqual(len(images), 1)
        action = workflow('../actions/ci-apt/action.yml')
        steps = action['runs']['steps']
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['env']['CARR_CI_POSTGRES_IMAGE'], 'both')
        self.assertNotRegex(json.dumps(steps), r'apt-get|actions/cache|--postgres-attempt')
        shards = workflow('unit-shards.yml')['jobs']['unit']
        run = next(step for step in shards['steps'] if step.get('name') == 'Run assigned MCP suites')
        self.assertIn('@postgres:5432/', run['env']['CARR_CI_DATABASE_URL'])
        canary = workflow('main-canary.yml')['jobs']['classes']['steps']
        run = next(step for step in canary if step.get('name') == "Run this group's check classes")
        self.assertIn('python ops/local-pg-ci.py --class migration --migration-part core', run['run'])
        self.assertIn('ops/ci.sh --strict --only migration --migration-part fixtures', run['run'])
        self.assertNotIn('local-pg-ci.py --class migration --migration-part fixtures', run['run'])

    def test_native_candidate_event_contract(self):
        wf = workflow()
        self.assertEqual(wf['on']['merge_group']['types'], ['checks_requested'])
        self.assertEqual(wf['on']['pull_request']['types'],
                         ['opened', 'edited', 'synchronize', 'reopened',
                          'converted_to_draft', 'ready_for_review'])
        self.assertEqual(wf['concurrency'], {
            'group': '${{ github.workflow }}-${{ github.head_ref || github.ref }}',
            'cancel-in-progress': "${{ github.event_name == 'pull_request' || (github.event_name == 'push' && github.ref != 'refs/heads/main') }}"})
        self.assertEqual(wf['jobs']['select']['if'],
                         "${{ !cancelled() && !github.event.pull_request.draft }}")
        self.assertEqual(wf['jobs']['checks']['if'], "${{ always() }}")
        self.assertEqual(wf['jobs']['checks']['name'],
                         "ops/ci.sh --strict")
        for name, job in wf['jobs'].items():
            if name in ('select', 'checks'):
                continue
            with self.subTest(job=name):
                self.assertIn('select', job['needs'])
                self.assertIn('needs.select.outputs', job['if'])
                self.assertIn('!cancelled()', job['if'])
        for name in ('classes', 'migration-fixtures'):
            self.assertFalse(wf['jobs'][name]['strategy']['fail-fast'])

    def test_required_context_reports_even_after_a_failed_class(self):
        wf = workflow()
        self.assertEqual(wf['jobs']['checks']['name'], "ops/ci.sh --strict")
        self.assertIn('always()', wf['jobs']['checks']['if'])
        self.assertIn('!cancelled()', wf['jobs']['classes']['if'])
        self.assertEqual(set(wf['jobs']['checks']['needs']), {'select', 'lint', 'unit', 'fast', 'secret', 'scope', 'classes', 'migration-fixtures', 'launchd-python39'})
        steps = wf['jobs']['checks']['steps']
        verdict = next(step for step in steps if step.get('name') == 'Fail unless every class group succeeded')
        self.assertIn('python3 ops/ci-evidence.py verdict', verdict['run'])
        self.assertEqual(verdict['env']['CI_NEEDS'], '${{ toJSON(needs) }}')
        self.assertNotIn('GH_TOKEN', verdict['env'])

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check-downloads':
        failures = release_download_violations(Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT)
        print('\n'.join(failures) if failures else 'Release download policy passed')
        sys.exit(bool(failures))
    unittest.main()
