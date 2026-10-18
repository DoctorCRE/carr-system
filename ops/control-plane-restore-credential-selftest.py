#!/usr/bin/env python3
"""restore.non_interactive_credential asks the credential loader, never the environment.

bin/restore-rehearse.sh loads NEON_API_KEY itself (bin/routine-credential-env.sh),
so the dispatcher's own environment says nothing about whether the job can run.
The preflight fact must ask the loader whether the key CAN be loaded, get back a
boolean only, and never carry the key value anywhere: not into os.environ, not
into the fact envelope, not into captured output.

Every credential here is a stub in a temp directory.  No real key is read.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'ops')]
from lib.loadpy import load_module_from_path  # noqa: E402

cp = load_module_from_path('control_plane_restore_credential_under_test',
                           str(REPO / 'tools' / 'control-plane.py'))
manifest = json.loads((REPO / 'ops/config/control-plane-workflows.v1.json').read_text())
workflow = next(w for w in manifest['workflows'] if w['key'] == 'restore-rehearse-weekly')
PAYLOAD = {'scheduled_for': '2026-08-17T13:00:00+00:00'}
FACT = 'restore.non_interactive_credential'
SENTINEL = 'stub-secret-SENTINEL-4f9a1c7e-not-a-real-key'
FAILED: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        FAILED.append(label)


def stub_loader(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text('#!/bin/zsh\n' + body + '\n', encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fact_envelope() -> dict:
    collector = cp._workflow_fact_collector(workflow, PAYLOAD, mode='shadow')
    built = tuple(collector.collect(fact=FACT, workflow_key='restore-rehearse-weekly',
                                    stage='routing'))
    assert len(built) == 1
    return built[0]


@contextlib.contextmanager
def empty_dispatcher_env(loader: Path):
    saved = {k: os.environ.pop(k, None) for k in ('NEON_API_KEY', 'CARR_AGE_IDENTITY')}
    setattr(cp, 'ROUTINE_CREDENTIAL_LOADER', loader)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
        if hasattr(cp, 'ROUTINE_CREDENTIAL_LOADER'):
            delattr(cp, 'ROUTINE_CREDENTIAL_LOADER')


with tempfile.TemporaryDirectory() as tmp:
    tmpdir = Path(tmp)

    # (a) loader says loadable, dispatcher environment is empty -> true.
    ok_loader = stub_loader(tmpdir, 'loads-ok.sh', 'exit 0')
    with empty_dispatcher_env(ok_loader):
        env = fact_envelope()
        check('(a) true when the loader reports the key loadable, dispatcher env empty',
              env['value'] is True and 'NEON_API_KEY' not in os.environ
              and 'CARR_AGE_IDENTITY' not in os.environ)

    # (b) loader fails, is missing, or hangs -> false (fail closed).
    fail_loader = stub_loader(tmpdir, 'loads-fail.sh', 'exit 1')
    with empty_dispatcher_env(fail_loader):
        check('(b) false when the loader exits non-zero', fact_envelope()['value'] is False)
    with empty_dispatcher_env(tmpdir / 'no-such-loader.sh'):
        check('(b) false when the loader script is missing', fact_envelope()['value'] is False)
    hang_loader = stub_loader(tmpdir, 'loads-hang.sh', 'sleep 30')
    with empty_dispatcher_env(hang_loader):
        saved_timeout = getattr(cp, 'ROUTINE_CREDENTIAL_CHECK_TIMEOUT', None)
        setattr(cp, 'ROUTINE_CREDENTIAL_CHECK_TIMEOUT', 1)
        try:
            check('(b) false when the loader times out', fact_envelope()['value'] is False)
        finally:
            if saved_timeout is None:
                delattr(cp, 'ROUTINE_CREDENTIAL_CHECK_TIMEOUT')
            else:
                setattr(cp, 'ROUTINE_CREDENTIAL_CHECK_TIMEOUT', saved_timeout)

    # (b) the dispatcher's own env no longer decides the answer.
    with empty_dispatcher_env(fail_loader):
        os.environ['NEON_API_KEY'] = 'irrelevant-env-value'
        try:
            check('(b) a key in the dispatcher env does not rescue a failing loader',
                  fact_envelope()['value'] is False)
        finally:
            os.environ.pop('NEON_API_KEY', None)

    # (c) the key value never escapes.  The stub is hostile: it reads a secret,
    # exports it, and prints it to stdout and stderr, then reports success.
    leaky = stub_loader(tmpdir, 'loads-and-leaks.sh',
                        f'export NEON_API_KEY={SENTINEL}\n'
                        f'print -r -- "$NEON_API_KEY"\n'
                        f'print -ru2 -- "$NEON_API_KEY"\n'
                        'exit 0')
    out, err = io.StringIO(), io.StringIO()
    with empty_dispatcher_env(leaky), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        env = fact_envelope()
    # Capture at the file-descriptor level too: a child that inherits stdout/stderr
    # would bypass redirect_stdout.
    probe = subprocess.run(
        [sys.executable, '-c',
         'import sys,os,json;sys.argv=["x"];'
         'sys.path[:0]=[%r,%r];' % (str(REPO), str(REPO / 'ops')) +
         'from lib.loadpy import load_module_from_path as L;'
         'cp=L("cpx",%r);' % str(REPO / 'tools' / 'control-plane.py') +
         'cp.ROUTINE_CREDENTIAL_LOADER=__import__("pathlib").Path(%r);' % str(leaky) +
         'm=json.load(open(%r));' % str(REPO / 'ops/config/control-plane-workflows.v1.json') +
         'w=[x for x in m["workflows"] if x["key"]=="restore-rehearse-weekly"][0];'
         'c=cp._workflow_fact_collector(w,{"scheduled_for":"2026-08-17T13:00:00+00:00"},mode="shadow");'
         'r=tuple(c.collect(fact="restore.non_interactive_credential",workflow_key="restore-rehearse-weekly",stage="routing"));'
         'print(json.dumps(r[0]["value"]));'
         'print(json.dumps(dict(os.environ)))'],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k not in ('NEON_API_KEY', 'CARR_AGE_IDENTITY')})
    check('(c) a loadable key still yields exactly a boolean true',
          env['value'] is True and type(env['value']) is bool)
    check('(c) key value absent from os.environ', SENTINEL not in json.dumps(dict(os.environ)))
    check('(c) key value absent from the returned fact envelope', SENTINEL not in json.dumps(env))
    check('(c) key value absent from captured stdout/stderr (in-process)',
          SENTINEL not in out.getvalue() and SENTINEL not in err.getvalue())
    check('(c) key value absent from child-inherited fds and the child environment',
          probe.returncode == 0 and SENTINEL not in probe.stdout + probe.stderr
          and probe.stdout.splitlines()[:1] == ['true'])

    # The real loader's --check mode, against stub credential files only.
    helper = REPO / 'bin' / 'routine-credential-env.sh'

    def run_check(env_file: Path | None, *keys: str) -> subprocess.CompletedProcess:
        env = {'HOME': str(tmpdir), 'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
        if env_file is not None:
            env['CARR_ROUTINE_DB_ENV_FILE'] = str(env_file)
        return subprocess.run(['zsh', str(helper), '--check', *keys], capture_output=True,
                              text=True, timeout=30, env=env, stdin=subprocess.DEVNULL)

    good = tmpdir / 'good.env'
    good.write_text(f"NEON_API_KEY='{SENTINEL}'\nCARR_DB_JOBS_URL='postgres://stub'\n")
    good.chmod(0o600)
    r = run_check(good, 'NEON_API_KEY')
    check('loader --check exits 0 for a loadable key and prints nothing',
          r.returncode == 0 and r.stdout == '' and r.stderr == '' and SENTINEL not in r.stdout + r.stderr)
    r = run_check(good, 'NOT_IN_FILE')
    check('loader --check exits non-zero when the named key is absent from the file',
          r.returncode != 0 and r.stdout == '')
    r = run_check(tmpdir / 'missing.env', 'NEON_API_KEY')
    check('loader --check exits non-zero when the credential file is missing',
          r.returncode != 0 and r.stdout == '')
    loose = tmpdir / 'loose.env'
    loose.write_text(f"NEON_API_KEY='{SENTINEL}'\n")
    loose.chmod(0o644)
    r = run_check(loose, 'NEON_API_KEY')
    check('loader --check exits non-zero for a group/world-readable file, and prints no secret',
          r.returncode != 0 and SENTINEL not in r.stdout + r.stderr)
    empty = tmpdir / 'empty.env'
    empty.write_text("NEON_API_KEY=''\n")
    empty.chmod(0o600)
    r = run_check(empty, 'NEON_API_KEY')
    check('loader --check exits non-zero for an empty value', r.returncode != 0)
    r = run_check(good)
    check('loader --check with no key names exits non-zero', r.returncode != 0)

    # Sourcing stays side-effect free: no --check handling, no exit, no output.
    sourced = subprocess.run(
        ['zsh', '-c', f'source {helper}; print -r -- sourced-ok'],
        capture_output=True, text=True, timeout=30, env={'HOME': str(tmpdir), 'PATH': os.environ['PATH']})
    check('sourcing the helper still defines functions silently and does not exit',
          sourced.stdout == 'sourced-ok\n' and sourced.returncode == 0)

if FAILED:
    print(f"\nFAILED {len(FAILED)}:")
    for label in FAILED:
        print('  -', label)
    sys.exit(1)
print('\ncontrol-plane restore credential selftest: ok')
