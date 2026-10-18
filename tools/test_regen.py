"""Regeneration executes owning commands, stops on failures and converges."""
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import regen


class RegenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(regen.ROOT / 'hooks', self.root / 'hooks')
        (self.root / 'ops/config').mkdir(parents=True)
        for path in ['ops/config/gate-baseline.json', 'lib/hook_runtime.py',
                     'ops/config/delegation-gate-hook.json', 'ops/config/hooks.json',
                     'ops/config/codex-hooks.json', 'ops/config/rule-enforcement-map.json', 'ops/config/model-floors.json']:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(regen.ROOT / path, target)
        (self.root / 'requirements.txt').write_text('fixture\n')
        digest = hashlib.sha256(b'fixture').hexdigest()
        (self.root / 'requirements.lock').write_text(f'# requirements.txt sha256: {digest}\n')
        shutil.copyfile(regen.ROOT / 'ops/ci-dep-check.py', self.root / 'ops/ci-dep-check.py')
        (self.root / 'render.py').write_text("from pathlib import Path\nPath('derived.txt').write_text('fresh\\n')\n")
        (self.root / 'ops/migration-shadow.py').write_text("from pathlib import Path\nPath('schema.txt').write_text('disposable\\n')\n")

    def run_regen(self):
        with patch.object(regen, 'ROOT', self.root), patch.object(regen, 'migration_names', return_value=set()), \
                patch.object(regen, 'GENERATORS', [('fixture', ('derived.txt',), (sys.executable, 'render.py'))]):
            regen.regenerate(None)

    def test_generators_replace_stale_bytes_and_second_run_has_no_diff(self):
        (self.root / 'derived.txt').write_text('stale\n')
        self.run_regen()
        self.assertEqual((self.root / 'derived.txt').read_text(), 'fresh\n')
        self.assertEqual((self.root / 'schema.txt').read_text(), 'disposable\n')
        first = (self.root / 'ops/config/gate-baseline.json').read_bytes()
        self.run_regen()
        self.assertEqual((self.root / 'ops/config/gate-baseline.json').read_bytes(), first)
        self.assertEqual((self.root / 'derived.txt').read_text(), 'fresh\n')

    def test_failed_generator_stops_before_schema_and_never_claims_success(self):
        (self.root / 'render.py').write_text("raise SystemExit('seeded generator failure')\n")
        with self.assertRaisesRegex(ValueError, 'seeded generator failure'):
            self.run_regen()
        self.assertFalse((self.root / 'schema.txt').exists())


if __name__ == '__main__':
    unittest.main()
