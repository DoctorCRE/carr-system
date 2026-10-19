"""Full Grok jobs through registration and the actual CLI seam."""
import json
import os
from pathlib import Path
import tempfile
import unittest

import desks
import grok_wire


class GrokBuildTests(unittest.TestCase):
    def test_default_desk_uses_newest_cli_model_and_workspace_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'grok'
            binary.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
if sys.argv[1:] == ['models']:
    print('Available models:\\n  - grok-9.1\\n  * grok-9.2 (default)\\n  - grok-9.2-build-fast')
else:
    pathlib.Path('invocation.json').write_text(json.dumps(sys.argv[1:]))
    print(json.dumps({'type': 'text', 'data': 'Tests passed; branch pushed.'}))
    print(json.dumps({'type': 'end', 'stopReason': 'end_turn', 'requestId': 'r',
        'sessionId': 's', 'modelUsage': {'grok-9.2-build': {'modelCalls': 1}}}))
''')
            binary.chmod(0o755)
            reg = desks.Registry(root / 'desks.json')
            entry = reg.register('grok-builder', 'grok-cli', cwd=tmp)
            row = grok_wire.run_task(entry, 'Fix the build, commit and open a PR.',
                env={**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH']})
            self.assertEqual(row['status'], 'completed')
            self.assertEqual(row['provider_metadata']['actual_model'], 'grok-9.2-build')
            argv = json.loads((root / 'invocation.json').read_text())
            self.assertEqual(argv[argv.index('--sandbox') + 1], 'workspace')
            self.assertEqual(argv[argv.index('--model') + 1], 'grok-9.2')
            self.assertNotIn('Read-only retrieval', argv[-1])


if __name__ == '__main__':
    unittest.main()
