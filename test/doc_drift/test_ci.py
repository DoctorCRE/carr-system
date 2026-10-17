import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CiTest(unittest.TestCase):
    def test_missing_product_manifest_refuses(self):
        spec = importlib.util.spec_from_file_location('doc_drift_ci', ROOT / 'scripts/doc-drift/ci.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scratch = Path(tempfile.mkdtemp(prefix='doc-drift-missing-pin-'))
        with self.assertRaises(ValueError):
            module.verify_snapshot(scratch)

    def test_pinned_product_copy_refuses_modified_bytes(self):
        spec = importlib.util.spec_from_file_location('doc_drift_ci', ROOT / 'scripts/doc-drift/ci.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scratch = Path(tempfile.mkdtemp(prefix='doc-drift-pin-fixture-'))
        (scratch / 'scripts/doc-drift').mkdir(parents=True)
        (scratch / 'tool.py').write_text('changed')
        (scratch / 'scripts/doc-drift/source.json').write_text(json.dumps(dict(
            repository='jbookout/software-factory', revision='a' * 40,
            files={'tool.py': '0' * 64})))
        with self.assertRaises(ValueError):
            module.verify_snapshot(scratch)

    @unittest.skipUnless((ROOT / 'ops/ci.sh').exists(), 'CARR owns the single CI script')
    def test_carr_ci_entrypoint_forwards_arguments_to_local_checker(self):
        scratch = Path(tempfile.mkdtemp(prefix='doc-drift-ci-fixture-'))
        (scratch / 'ops').mkdir()
        (scratch / 'ops/ci.sh').write_bytes((ROOT / 'ops/ci.sh').read_bytes())
        (scratch / '.venv/bin').mkdir(parents=True)
        executable = scratch / '.venv/bin/python'
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        executable.chmod(0o755)
        env = dict(os.environ)
        env.pop('CARR_DOC_DRIFT_PYTHON', None)
        result = subprocess.run(['bash', str(scratch / 'ops/ci.sh'), '--doc-drift', '--base', 'abc123'],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(scratch / 'scripts/doc-drift/ci.py'), '--base', 'abc123'])


if __name__ == '__main__':
    unittest.main()
