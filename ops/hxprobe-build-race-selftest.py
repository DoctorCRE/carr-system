#!/usr/bin/env python3
"""Hold a competing publisher in Cargo's observed unlink/link window."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import hxprobe_test_binary as decoder
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REAL_RUN = subprocess.run


def publish(path):
    path.write_text('#!/bin/sh\nprintf "%s\\n" "$1"\n')
    path.chmod(0o700)


def load_fixture():
    spec = importlib.util.spec_from_file_location('build_fixture_race', ROOT / 'ops/outlook-build-fixtures-selftest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildRace(unittest.TestCase):
    def setUp(self):
        decoder.test_binary.cache_clear()
        self.addCleanup(decoder.test_binary.cache_clear)

    def test_parent_artifact_is_reused_without_cargo(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'hxprobe'
            publish(binary)
            with patch.dict(os.environ, {'CARR_HXPROBE_TEST_BINARY': str(binary)}), patch('subprocess.run') as build:
                self.assertEqual(decoder.test_binary(), binary)
                build.assert_not_called()
            binary.unlink()
            decoder.test_binary.cache_clear()
            with patch.dict(os.environ, {'CARR_HXPROBE_TEST_BINARY': str(binary)}), self.assertRaises(FileNotFoundError):
                decoder.test_binary()

    def test_standalone_build_is_cached_and_cleaned_by_owner(self):
        with patch.dict(os.environ), patch('atexit.register') as cleanup:
            os.environ.pop('CARR_HXPROBE_TEST_BINARY', None)
            def build(command, **kwargs):
                target = Path(command[command.index('--target-dir') + 1]) / 'debug/hxprobe'
                target.parent.mkdir()
                publish(target)
            with patch('subprocess.run', side_effect=build) as cargo:
                binary = decoder.test_binary()
                self.assertEqual(decoder.test_binary(), binary)
                self.assertTrue(binary.is_file())
                self.assertEqual(cargo.call_count, 1)
            cleanup.call_args.args[0]()
            self.assertFalse(binary.parent.parent.exists())

    def test_failed_build_cleans_target_and_refuses_reader(self):
        targets = []
        def fail(command, **kwargs):
            targets.append(Path(command[command.index('--target-dir') + 1]))
            raise subprocess.CalledProcessError(17, command)
        with patch.dict(os.environ), patch('subprocess.run', side_effect=fail):
            os.environ.pop('CARR_HXPROBE_TEST_BINARY', None)
            with self.assertRaises(subprocess.CalledProcessError):
                decoder.test_binary()
        self.assertFalse(targets[0].exists())

    def test_completed_decoder_survives_competing_build_publication(self):
        fixture = load_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared = root / 'vendor/hxprobe/target/debug/hxprobe'
            shared.parent.mkdir(parents=True)
            publish(shared)
            built, gap, resume = threading.Event(), threading.Event(), threading.Event()

            def build(command, **kwargs):
                self.assertEqual(command[:2], ['cargo', 'build'])
                target = (Path(command[command.index('--target-dir') + 1]) / 'debug/hxprobe'
                          if '--target-dir' in command else shared)
                target.parent.mkdir(parents=True, exist_ok=True)
                publish(target)
                built.set()
                return subprocess.CompletedProcess(command, 0)

            def competing_build():
                if not built.wait(10):
                    raise AssertionError('reader build did not finish')
                shared.unlink()
                gap.set()
                if not resume.wait(10):
                    raise AssertionError('reader did not release competing build')
                publish(shared)

            def read(binary, _fixture, _directory):
                self.assertTrue(gap.wait(10), 'competing build never reached unlink/link gap')
                try:
                    result = REAL_RUN([str(binary), 'decoder-readable'], capture_output=True, text=True, check=True)
                    self.assertEqual(result.stdout.strip(), 'decoder-readable')
                finally:
                    resume.set()
                return None

            with patch.dict(os.environ, {}, clear=False), \
                    patch.object(fixture, 'ROOT', root), \
                    patch.object(fixture, 'validate', return_value=[{'id': 'race', 'expected': None}]), \
                    patch.object(fixture, 'decode', side_effect=read), \
                    patch('subprocess.run', side_effect=build), ThreadPoolExecutor(max_workers=1) as pool:
                os.environ.pop('CARR_HXPROBE_TEST_BINARY', None)
                contender = pool.submit(competing_build)
                try:
                    fixture.BuildFixtures('test_build_fixture_manifest_requires_evidence_and_exact_decoder_output').test_build_fixture_manifest_requires_evidence_and_exact_decoder_output()
                finally:
                    resume.set()
                contender.result(timeout=10)


if __name__ == '__main__':
    unittest.main()
