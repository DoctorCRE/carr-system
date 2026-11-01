#!/usr/bin/env python3
"""Exercise managed install reconciliation through its public sync interface."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('pstack_update', Path(__file__).with_name('update.py'))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Install(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source, self.dest = root/'source', root/'dest'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', self.source], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        self.skill = self.source/'skills/poteto-mode/SKILL.md'
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text('---\nname: poteto-mode\n---\n\n# Poteto\n\nOriginal instructions.\n')
        self.adapter = root/'adapter.md'
        self.adapter.write_text('# Adapter\n')

    def sync(self):
        return module.sync(self.source, self.dest, self.adapter)

    def test_idempotent(self):
        self.assertGreater(self.sync(), 0)
        mtimes = {p:p.stat().st_mtime_ns for p in self.dest.rglob('*') if p.is_file()}
        self.assertEqual(self.sync(), 0)
        self.assertEqual(mtimes, {p:p.stat().st_mtime_ns for p in mtimes})

    def test_update_and_retirement(self):
        self.sync()
        self.skill.write_text(self.skill.read_text()+'New instruction.\n')
        self.assertGreater(self.sync(), 0)
        self.assertIn('New instruction.', (self.dest/'skills/poteto-mode/SKILL.md').read_text())
        self.skill.unlink()
        self.sync()
        self.assertFalse((self.dest/'skills/poteto-mode/SKILL.md').exists())

    def test_preserves_user_edit(self):
        self.sync()
        target = self.dest/'skills/poteto-mode/SKILL.md'
        target.write_text('User edit')
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(target.read_text(), 'User edit')

    def test_refuses_collision(self):
        target = self.dest/'skills/poteto-mode/SKILL.md'
        target.parent.mkdir(parents=True)
        target.write_text('Unowned')
        with self.assertRaises(ValueError):
            self.sync()

    def test_refuses_symlink_parent(self):
        self.dest.mkdir()
        outside = self.dest.parent/'outside'
        outside.mkdir()
        (self.dest/'skills').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(list(outside.iterdir()), [])

    def test_no_heading(self):
        self.skill.write_text('---\nname: poteto-mode\n---\n\nInstructions.\n')
        self.sync()
        self.assertIn('harness adapter', (self.dest/'skills/poteto-mode/SKILL.md').read_text())


if __name__ == '__main__':
    unittest.main()
