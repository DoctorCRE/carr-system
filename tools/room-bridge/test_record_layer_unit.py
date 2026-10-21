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

    def test_codex_refuses_before_desktop_resume_or_exec(self):
        with patch.object(dispatch.subprocess, 'run') as run, patch.object(dispatch.codex_ipc, 'thread_owner') as owner:
            with self.assertRaisesRegex(desks.DeskError, 'Codex.*tool'):
                dispatch._to_codex({'posture': 'record-layer', 'thread_id': 'old'}, 'Read the record.',
                                   env={}, live_desktop=True)
        run.assert_not_called()
        owner.assert_not_called()

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
