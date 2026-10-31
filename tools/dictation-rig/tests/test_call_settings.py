"""Budget delivery through the installed checker entry points, without providers."""
import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tools/dictation-rig/bin'))


def test_launchagents_render_the_standing_budget():
    spec = importlib.util.spec_from_file_location('budget_config_renderer', REPO / 'ops/config-as-code.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    for name in ('call-mode', 'capture-poll'):
        source = (REPO / f'ops/launchd/com.carr.{name}.plist').read_text()
        rendered = plistlib.loads(renderer.concrete(source).encode())
        assert rendered['EnvironmentVariables']['CALL_JEV_DAILY_DOLLARS'] == '0.50'


def test_quill_hook_delivers_budget_to_transcription(tmp_path):
    python = tmp_path / 'python3'
    python.write_text(f'''#!{sys.executable}
import os, sys
from pathlib import Path
if sys.argv[1].endswith('call_settings.py'):
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
if sys.argv[1].endswith('transcribe_session.py'):
    Path(sys.argv[2], 'observed-budget').write_text(os.environ.get('CALL_JEV_DAILY_DOLLARS', 'absent'))
''')
    python.chmod(0o700)
    with patch.dict(os.environ, {'PATH': str(tmp_path) + ':' + os.defpath}):
        os.environ.pop('CALL_JEV_DAILY_DOLLARS', None)
        result = subprocess.run(['/bin/sh', str(REPO / 'tools/dictation-rig/bin/transcribe-session.sh'), str(tmp_path)],
                                capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'observed-budget').read_text() == '0.50'


def test_python_entry_points_load_one_source_and_preserve_explicit_empty(tmp_path):
    from lib import call_settings
    import phone_intake
    import transcribe_session
    from test_call_mode import call_mode, capture_bridge

    source = tmp_path / 'budget.json'
    source.write_text('{"CALL_JEV_DAILY_DOLLARS":"0.01"}')
    with patch.object(call_settings, 'SOURCE', source):
        for value, expected in ((None, '0.01'), ('', '')):
            with patch.dict(os.environ, {'CALL_STUDIO_TOKEN': 'synthetic',
                                          'CARR_CAPTURE_BASE_URL': 'https://synthetic.test'}):
                if value is None:
                    os.environ.pop('CALL_JEV_DAILY_DOLLARS', None)
                else:
                    os.environ['CALL_JEV_DAILY_DOLLARS'] = value
                with patch.object(sys, 'argv', ['phone_intake.py', '--root', str(tmp_path), '--process']), \
                     patch.object(phone_intake, 'load_machine_settings', side_effect=lambda: {'CALL_STUDIO_TOKEN': 'synthetic', 'CARR_CAPTURE_BASE_URL': 'https://synthetic.test'}), \
                     patch.object(phone_intake, 'pull_pending', return_value={}), \
                     patch.object(phone_intake, 'process_pending', return_value={}):
                    phone_intake.main()
                assert os.environ['CALL_JEV_DAILY_DOLLARS'] == expected
                os.environ.pop('CALL_JEV_DAILY_DOLLARS', None)
                with patch.object(sys, 'argv', ['call-mode.py', 'state']), \
                     patch.object(call_mode, 'current_state', return_value={}):
                    assert call_mode.main() == 0
                assert os.environ['CALL_JEV_DAILY_DOLLARS'] == '0.01'
                os.environ.pop('CALL_JEV_DAILY_DOLLARS', None)
                assert transcribe_session.main(['transcribe_session.py']) == 2
                assert os.environ['CALL_JEV_DAILY_DOLLARS'] == '0.01'
                os.environ.pop('CALL_JEV_DAILY_DOLLARS', None)
                with patch.object(capture_bridge, 'load_config', return_value=None):
                    capture_bridge.main(['capture-bridge.py', 'check'])
                assert os.environ['CALL_JEV_DAILY_DOLLARS'] == '0.01'
