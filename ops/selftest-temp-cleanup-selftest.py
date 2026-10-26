#!/usr/bin/env python3
"""Prove the fixture-heavy selftests leave a repo-local TMPDIR clean."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

for name in ("session-brief-selftest.py",
             "backup-workflow-selftest.py",
             "hook-env-isolation-selftest.py",
             "../test/doc_drift/test_checker.py",
             "../test/doc_drift/test_ci.py"):
    with tempfile.TemporaryDirectory(prefix="fixture-tmp-") as root:
        env = os.environ.copy()
        env["TMPDIR"] = root
        env["TMP"] = root
        env["TEMP"] = root
        env["CARR_FLASH_STATE_DIR"] = os.path.join(root, "flash-state")
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(HERE, name)],
                cwd=os.path.dirname(HERE), env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(f"{name} exceeded 60s timeout") from exc
        assert result.returncode == 0, f"{name} failed:\n{result.stdout}"
        children = os.listdir(root)
        assert not children, f"{name} leaked {children!r}\n{result.stdout}"

# Exercise the standard positional APIs after each suite installs its wrapper;
# TemporaryDirectory itself must remain compatible with that wrapper too.
with tempfile.TemporaryDirectory(prefix="compat-tmp-") as compat_root:
    compat_env = {**os.environ, "SELFTEST_OPS": HERE,
                  "TMPDIR": compat_root, "TMP": compat_root,
                  "TEMP": compat_root, "PARENT_TMP": compat_root}
    compat = subprocess.run(
        [sys.executable, "-c", """
import os, runpy, tempfile
here = os.environ['SELFTEST_OPS']
for name in ('session-brief-selftest.py',):
    runpy.run_path(os.path.join(here, name))
    d = tempfile.mkdtemp('.suffix', 'positional-')
    fd, path = tempfile.mkstemp('.log', 'positional-', None, False)
    os.close(fd)
    assert d.startswith(os.environ['PARENT_TMP'])
    assert path.startswith(os.environ['PARENT_TMP'])
    with tempfile.TemporaryDirectory(prefix='temporary-directory-') as held:
        assert held.startswith(os.environ['PARENT_TMP'])
    os.unlink(path)
    os.rmdir(d)
"""],
        cwd=os.path.dirname(HERE), env=compat_env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
    )
    assert compat.returncode == 0, f"tempfile API compatibility failed:\n{compat.stdout}"
    assert not os.listdir(compat_root), f"compatibility leaked {os.listdir(compat_root)!r}"
print("ok: selftests clean all temporary fixtures")
