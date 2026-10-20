#!/usr/bin/env python3
"""Hermetic regression tests for the room-bridge selftest harness."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "room_bridge_selftest", os.path.join(HERE, "room-bridge-selftest.py"))
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_timeout_is_visible_and_later_suites_run():
    calls = []

    def runner(argv, **kwargs):
        rel = argv[1].split("/tools/", 1)[-1]
        calls.append(rel)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"],
                                             output="partial stdout",
                                             stderr="partial stderr")
        return SimpleNamespace(returncode=0, stdout="later passed\n", stderr="")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = mod.main(suites=("tools/room-bridge/test_queue_unit.py",
                                  "tools/room-bridge/test_queue_projection_unit.py"),
                          runner=runner, timeout=7)
    text = out.getvalue()
    assert result == 1
    assert calls == ["room-bridge/test_queue_unit.py",
                     "room-bridge/test_queue_projection_unit.py"]
    assert "timed out after 7s" in text
    assert "partial stdout" in text and "partial stderr" in text
    assert "test_queue_projection_unit.py" in text and "1/2 suites passed" in text


def test_clean_set_stays_green():
    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="passed\n", stderr="")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = mod.main(suites=("tools/room-bridge/test_queue_unit.py",), runner=runner)
    assert result == 0
    assert "1/1 suites passed" in out.getvalue()


def test_each_suite_gets_a_disposable_store_even_with_an_inherited_override():
    from pathlib import Path
    from unittest.mock import patch
    stores = []

    def runner(argv, **kwargs):
        path = Path(kwargs['env']['CARR_WORK_QUEUE_DB'])
        assert path.parent.is_dir()
        assert not path.exists()
        path.touch()
        stores.append(path)
        return SimpleNamespace(returncode=0, stdout='passed\n', stderr='')

    with patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': '/non-temporary/forbidden.sqlite3'}):
        assert mod.main(suites=('tools/room-bridge/test_queue_unit.py',) * 2, runner=runner) == 0
    assert len(set(stores)) == 2
    assert all(not path.parent.exists() for path in stores)


test_timeout_is_visible_and_later_suites_run()
test_clean_set_stays_green()
test_each_suite_gets_a_disposable_store_even_with_an_inherited_override()
print("room-bridge-selftest harness: 3/3 passed")
