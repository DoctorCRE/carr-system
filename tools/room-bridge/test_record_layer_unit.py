"""Record-layer runtime contracts, without paid inference."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import desks
import dispatch
import flash_wire
import grok_wire
import hermes_wire


class WireTests(unittest.TestCase):
    def test_nous_refuses_before_config_import_budget_or_process_launch(self):
        run = Mock()
        with patch.object(hermes_wire.nous_budget, 'Ledger') as ledger:
            with self.assertRaisesRegex(desks.DeskError, 'Hermes.*isolation'):
                hermes_wire.run_task({'kind': 'hermes-nous', 'posture': 'record-layer', 'provider': 'nous',
                    'family': 'glm', 'effort': 'medium', 'cwd': '.', 'timeout_s': 30},
                    'Read the record.', env={}, run=run, results_path='unused', run_id='unused')
        self.assertEqual((run.call_args_list, ledger.call_args_list), ([], []))

    def test_codex_executes_a_fresh_confined_business_run(self):
        calls = []
        def run(argv, **options):
            calls.append((argv, options))
            home = Path(options['env']['HOME'])
            codex_home = Path(options['env']['CODEX_HOME'])
            self.assertTrue(home.is_dir() and codex_home.is_dir())
            self.assertNotEqual(home, codex_home)
            self.assertEqual(list(codex_home.iterdir()), [])
            self.assertNotIn('UNRELATED_SECRET', options['env'])
            if argv[1] == 'sandbox':
                self.assertNotIn('someone@example.test', ' '.join(argv))
                return subprocess.CompletedProcess(argv, 0,
                    '{"file_write":"denied","network":"denied"}', '')
            self.assertEqual(argv[:2], ['codex', 'exec'])
            for flag in ('--ignore-user-config', '--ignore-rules', '--ephemeral', '--skip-git-repo-check'):
                self.assertIn(flag, argv)
            self.assertEqual(argv[argv.index('--sandbox') + 1], 'read-only')
            self.assertNotIn('resume', argv)
            self.assertNotIn('--add-dir', argv)
            self.assertEqual(list(Path(options['cwd']).iterdir()), [])
            self.assertEqual(options['cwd'], argv[argv.index('-C') + 1])
            config = dict(arg.split('=', 1) for arg in argv if '=' in arg)
            self.assertEqual(config['sandbox_workspace_write.network_access'], 'false')
            self.assertEqual(json.loads(config['approval_policy']), 'never')
            mcp = json.loads(config['mcp_servers.carr.url'])
            self.assertEqual(mcp, dispatch.claude_desktop_wire.CARR_MCP_URL)
            self.assertIn('mcp_servers.carr.required=true', argv)
            self.assertIn('Draft for someone@example.test.', argv[-1])
            events = [{'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': 'Synthetic business reply.'}}, {'type': 'turn.completed'}]
            return subprocess.CompletedProcess(argv, 0, '\n'.join(map(json.dumps, events)), '')
        with patch.object(dispatch.codex_ipc, 'thread_owner') as owner:
            result = dispatch._to_codex({'posture': 'record-layer', 'thread_id': 'old',
                'model': 'gpt-6.1-sol', 'effort': 'medium', 'cwd': '/ignored',
                'sandbox': 'danger-full-access', 'add_dirs': ['/ignored']},
                'Draft for someone@example.test.', env={'PATH': '/usr/bin',
                    'CODEX_API_KEY': 'synthetic', 'CARR_MCP_TOKEN': 'synthetic',
                    'UNRELATED_SECRET': 'must-not-inherit'}, live_desktop=True, provider_run=run)
        owner.assert_not_called()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result'], 'Synthetic business reply.')
        self.assertEqual(result['canaries'], {'file_write': 'denied', 'network': 'denied'})
        self.assertEqual(len(calls), 2)
        self.assertFalse(Path(calls[0][1]['env']['HOME']).exists())

    def test_codex_failed_or_unproven_canaries_refuse_before_the_brief(self):
        for output, failed in [
                ('{"file_write":"allowed","network":"denied"}', 'file_write'),
                ('{"file_write":"denied","network":"allowed"}', 'network'),
                ('{"file_write":"denied","network":"inconclusive"}', 'network'),
                ('{}', 'file_write'), ('[]', 'file_write'), ('not json', 'file_write'),
                (subprocess.TimeoutExpired('codex sandbox', 2), 'file_write/network')]:
            with self.subTest(output=output):
                run = Mock(side_effect=output if isinstance(output, Exception) else None,
                           return_value=subprocess.CompletedProcess([], 0, output, ''))
                with self.assertRaises(desks.DeskError) as refused:
                    dispatch._to_codex({'posture': 'record-layer'},
                        'Draft for someone@example.test.', env={
                            'CODEX_API_KEY': 'synthetic', 'CARR_MCP_TOKEN': 'synthetic'}, provider_run=run)
                self.assertEqual(refused.exception.code, 'record_layer_canary_failed')
                self.assertIn(failed, str(refused.exception))
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][1], 'sandbox')

    def test_codex_requires_explicit_auth_and_exact_endpoint_without_retry(self):
        for env, code in [({}, 'record_layer_auth_missing'),
                ({'CODEX_API_KEY': 'synthetic'}, 'record_layer_auth_missing'),
                ({'CARR_MCP_TOKEN': 'synthetic'}, 'record_layer_auth_missing'),
                *[({'CARR_MCP_URL': url}, 'untrusted_carr_endpoint') for url in (
                    'https://other.example.test/mcp', 'https://api.doctorcre.com.example.test/mcp',
                    'http://api.doctorcre.com/mcp', 'https://api.doctorcre.com:8443/mcp',
                    'https://api.doctorcre.com/other')]]:
            with self.subTest(env=env):
                run = Mock()
                with self.assertRaises(desks.DeskError) as refused:
                    dispatch._to_codex({'posture': 'record-layer'}, 'Synthetic.', env=env, provider_run=run)
                self.assertEqual(refused.exception.code, code)
                run.assert_not_called()

    def test_codex_business_profile_rejects_caller_config_overrides(self):
        run = Mock()
        with self.assertRaises(desks.DeskError) as refused:
            dispatch._to_codex({'posture': 'record-layer'}, 'Synthetic.', env={}, provider_run=run,
                              config_overrides=('sandbox_mode="danger-full-access"',))
        self.assertEqual(refused.exception.code, 'bad_codex_config')
        run.assert_not_called()

    def test_grok_refuses_before_catalog_or_model_launch(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 1, '', ''))
        with self.assertRaisesRegex(desks.DeskError, 'Grok.*MCP'):
            grok_wire.run_task({'kind': 'grok-cli', 'posture': 'record-layer', 'model': 'grok', 'effort': 'high'},
                               'Read the record.', run=run)
        run.assert_not_called()

    def test_flash_refuses_before_script_code_or_model_route(self):
        with patch.object(flash_wire, 'run_turn') as run, patch.object(flash_wire, 'code_inputs') as code:
            with self.assertRaisesRegex(desks.DeskError, 'Flash.*MCP'):
                flash_wire.run_task('Read the record.', posture='record-layer')
        run.assert_not_called()
        code.assert_not_called()

    def test_nous_runtime_uses_the_observed_python_launcher_when_discovery_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / 'python'
            python.touch()
            (root / 'run_agent.py').touch()
            launcher = root / 'hermes'
            launcher.write_text('#!/bin/bash\nexec "' + str(python) + '" "' + str(root / 'hermes.py') + '" "$@"\n')
            with patch.object(hermes_wire.shutil, 'which', return_value=str(launcher)):
                command = hermes_wire.launcher_command()
            self.assertEqual(command[:3], [str(python), '-I', '-c'])
            self.assertIn(repr(str(root)), command[3])
            launcher.write_text('#!/bin/bash\nexec unverified-wrapper "$@"\n')
            with patch.object(hermes_wire.shutil, 'which', return_value=str(launcher)):
                with self.assertRaisesRegex(ValueError, 'native runtime'):
                    hermes_wire.launcher_command()

    def test_nous_passes_posture_to_the_owned_native_process(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[:3] == ['hermes', 'portal', 'status']:
                out = 'Auth: logged in'
            elif argv[:3] == ['hermes', 'fallback', 'list']:
                out = 'No fallback providers configured.'
            elif argv[0] == 'curl':
                out = json.dumps({'paidRecommendedModels': [{'modelName': 'glm-5'}]})
            elif '--print-runtime-command' in argv:
                out = json.dumps(['python', '-I', '-c', 'pass'])
            elif '--quote-model' in argv:
                out = json.dumps({'input_usd_per_token': '0.000001', 'output_usd_per_token': '0.000001'})
            else:
                Path(argv[argv.index('--usage-file') + 1]).write_text(json.dumps(
                    {'model': 'glm-5', 'provider': 'nous', 'completed': True}))
                out = 'Read complete.'
            return subprocess.CompletedProcess(argv, 0, out, '')
        accounting = {'allowance': '1', 'before_launch': lambda: None}
        with tempfile.TemporaryDirectory() as tmp:
            result = hermes_wire._run_task({'posture': 'record-layer', 'provider': 'nous', 'family': 'glm',
                'effort': 'medium', 'cwd': tmp, 'timeout_s': 30}, 'Read the record.', env={}, run=run,
                accounting=accounting)
        self.assertEqual(result['status'], 'completed')
        argv, options = calls[-1]
        normalized = list(argv)
        normalized[normalized.index('--usage-file') + 1] = '<usage-file>'
        native = str(Path(hermes_wire.__file__).with_name('hermes_native.py'))
        self.assertEqual(normalized, ['python', '-I', '-c',
            'pass; import runpy; runpy.run_path(' + repr(native) + ", run_name='__main__')",
            '-z', 'Read the record.', '-m', 'glm-5', '--reasoning', 'medium',
            '--usage-file', '<usage-file>', '--allowance-usd', '1', '--price-json',
            '{"input_usd_per_token": "0.000001", "output_usd_per_token": "0.000001"}',
            '--posture', 'record-layer'])
        self.assertEqual({k: options['env'][k] for k in ('HERMES_IGNORE_USER_CONFIG', 'HERMES_YOLO_MODE',
            'HERMES_SINGLE_QUERY_SESSION')}, {'HERMES_IGNORE_USER_CONFIG': '1', 'HERMES_YOLO_MODE': '1',
                                            'HERMES_SINGLE_QUERY_SESSION': '1'})


if __name__ == '__main__':
    unittest.main()
