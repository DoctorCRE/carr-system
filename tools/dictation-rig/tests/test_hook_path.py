"""Run the Quill hook with launchd's sparse PATH and an offline Claude CLI."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


BIN = Path(__file__).resolve().parents[1] / 'bin'


class HookPathTests(unittest.TestCase):
    def test_hook_summarizes_with_launchd_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'home'
            local_bin = home / '.local/bin'
            local_bin.mkdir(parents=True)
            (local_bin / 'python3').symlink_to(sys.executable)
            claude = local_bin / 'claude'
            claude.write_text(
                f'#!{sys.executable}\n'
                'import json, sys\n'
                'request = json.load(sys.stdin)\n'
                'assert request["TRANSCRIPT"] == {"segments": []}\n'
                'print(json.dumps({"structured_output": {"summary": "offline pack"}}))\n'
            )
            claude.chmod(0o700)
            hook = root / 'tools/dictation-rig/bin/transcribe-session.sh'
            hook.parent.mkdir(parents=True)
            shutil.copy(BIN / hook.name, hook)
            settings = root / 'lib/call_settings.py'
            settings.parent.mkdir()
            settings.write_text('import subprocess, sys\nsys.exit(subprocess.call(sys.argv[1:]))\n')
            hook.with_name('transcribe_session.py').write_text(
                'import json, os, sys, types\n'
                'from pathlib import Path\n'
                f'sys.path.insert(0, {str(BIN)!r})\n'
                'sys.modules["lib.headless_tasks"] = types.SimpleNamespace(API_ENV=())\n'
                'import post_call\n'
                'result = post_call.default_distiller({"context": {}, "transcript": {"segments": []}})\n'
                'assert "/opt/homebrew/bin" in os.get_exec_path()\n'
                'assert "/usr/local/bin" in os.get_exec_path()\n'
                'Path(sys.argv[1], "result.json").write_text(json.dumps(result))\n'
            )
            session = root / 'session'
            session.mkdir()
            result = subprocess.run(
                ['/bin/sh', str(hook), str(session)],
                env={'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads((session / 'result.json').read_text()), {'summary': 'offline pack'})
            self.assertIn('FINISH transcribe-session.sh', (session / 'transcribe.log').read_text())


if __name__ == '__main__':
    unittest.main()
