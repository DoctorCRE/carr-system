#!/usr/bin/env python3
"""Run source studies directly through confined native provider CLIs.

The study uses Codex's configured model and effort. Provider authentication
alone reaches the confined process; CARR credentials and tools never do.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib

def preflight(phase):
    if phase == 'retrieval':
        from grok_wire import MODEL, EFFORT
        model, effort, name, kind = MODEL, EFFORT, 'grok-build', 'grok-cli'
    else:
        home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        config = tomllib.loads((home / 'config.toml').read_text())
        model, effort = config.get('model'), config.get('model_reasoning_effort')
        if not isinstance(model, str) or not model.strip() or effort not in ('minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('Codex config must name a model and reasoning effort for a pinned source study')
        name, kind = 'source-study', 'codex-exec'
    bound = {'name': name, 'kind': kind, 'model': model, 'effort': effort, 'sandbox': 'read-only'}
    digest = hashlib.sha256(json.dumps(bound, sort_keys=True).encode()).hexdigest()
    return {**bound, 'digest': digest}


def runtime_env(folder, auth_home, ambient, *, provider='codex'):
    folder.mkdir(parents=True, exist_ok=True)
    home = folder / 'home'
    provider_home = home / ('.grok' if provider == 'grok' else '.codex')
    provider_home.mkdir(parents=True, exist_ok=True)
    tmp = folder / 'tmp'
    tmp.mkdir(exist_ok=True)
    # Only provider authentication is copied. Config, MCP, hooks, keychains,
    # environment tokens and the caller's session history are excluded.
    shutil.copyfile(auth_home / 'auth.json', provider_home / 'auth.json')
    (provider_home / 'auth.json').chmod(0o600)
    (provider_home / 'config.toml').write_text('' if provider == 'grok' else
        'approval_policy="never"\nsandbox_mode="read-only"\nweb_search="live"\n'
        '[features]\nshell_tool=false\nmulti_agent=false\n')
    return {'HOME': str(home), **({'CODEX_HOME': str(provider_home)} if provider == 'codex' else {}), 'TMPDIR': str(tmp),
            'PATH': ambient.get('PATH', '/opt/homebrew/bin:/usr/bin:/bin'),
            'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}


def provider_executable(binary):
    executable = Path(binary).resolve()
    with executable.open('rb') as handle:
        header = handle.read(128).split(b'\n', 1)[0]
    if not header.startswith(b'#!') or b'node' not in header:
        return executable
    # The npm Codex launcher spawns its packaged Rust executable. Resolve the
    # same platform package before confinement so the provider needs no fork.
    package = executable.parent.parent
    if json.loads((package / 'package.json').read_text()).get('name') != '@openai/codex':
        raise ValueError('research Node launcher is not the Codex package')
    architecture = {'arm64': ('arm64', 'aarch64-apple-darwin'),
                    'x86_64': ('x64', 'x86_64-apple-darwin')}.get(platform.machine())
    if sys.platform != 'darwin' or architecture is None:
        raise ValueError('research Codex native platform is unavailable')
    suffix, target = architecture
    for vendor in (package / 'node_modules/@openai' / f'codex-darwin-{suffix}' / 'vendor',
                   package / 'vendor'):
        candidate = vendor / target / 'bin/codex'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise ValueError('research Codex native executable is unavailable')


def confine(argv, folder):
    if not Path('/usr/bin/sandbox-exec').exists():
        raise ValueError('source studies require macOS sandbox-exec; no unconfined fallback')
    # Public/provider HTTPS is allowed; local authenticated IPC, private files,
    # keychains, Apple Events and all writes outside this job remain closed.
    paths = [str(folder.resolve()), str(Path.home().resolve()),
             str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())]
    if any('"' in path or '\\' in path for path in paths):
        raise ValueError('research path cannot be expressed in sandbox profile')
    job, home, prefix, base = paths
    executable = str(provider_executable(argv[0]))
    argv = [executable, *argv[1:]]
    if '"' in executable or '\\' in executable:
        raise ValueError('provider executable cannot be expressed in sandbox profile')
    profile = (
        '(version 1)(allow default)(deny network*)'
        '(allow network-outbound (remote tcp "*:443"))'
        '(deny file-read* (subpath "' + home + '") (subpath "/Users") '
        '(subpath "/private/tmp") (subpath "/private/var/folders") '
        '(subpath "/opt/homebrew/etc") (subpath "/opt/homebrew/var") '
        '(subpath "/Library/Keychains") (subpath "/etc/ssh"))'
        f'(allow file-read* (subpath "{job}") (subpath "{prefix}") (subpath "{base}"))'
        f'(allow file-read* (literal "{executable}"))'
        '(allow file-read-metadata)(deny file-write*)'
        f'(allow file-write* (subpath "{job}") (literal "/dev/null"))'
        '(deny mach-lookup)(deny appleevent-send)(deny signal (target others))'
        # A research provider is one owned process. Fork/spawn is denied by
        # the OS before an intermediary can detach and disappear between
        # ancestry samples. Threads and replacement exec remain available.
        '(deny process-fork)'
    )
    return ['/usr/bin/sandbox-exec', '-p', profile, *argv]


def observed_model(codex_home, thread_id, entry):
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError('Codex result lacks a thread identity')
    matches = []
    for path in (codex_home / 'sessions').rglob('*.jsonl'):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        if not any(row.get('type') == 'session_meta' and row.get('payload', {}).get('id') == thread_id
                   for row in rows):
            continue
        contexts = [row['payload'] for row in rows if row.get('type') == 'turn_context']
        if not contexts or any(c.get('model') != entry['model'] or c.get('effort') != entry['effort']
                               for c in contexts):
            raise ValueError('Codex actual model/effort mismatch')
        matches.append({'model': entry['model'], 'effort': entry['effort'], 'thread_id': thread_id})
    if len(matches) != 1:
        raise ValueError('Codex actual model evidence missing or ambiguous')
    return matches[0]


def run_job(folder, phase):
    with tempfile.TemporaryDirectory(prefix='.runtime-', dir=folder) as temporary:
        return _run_job(folder, phase, Path(temporary))


def _run_job(folder, phase, runtime):
    request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
    route = preflight(phase)
    if route != request['route']:
        raise ValueError('provider model or effort changed after preflight')
    provider = 'grok' if phase == 'retrieval' else 'codex'
    auth_home = Path.home() / '.grok' if provider == 'grok' else Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    env = runtime_env(runtime, auth_home, os.environ, provider=provider)

    def confined_command(command):
        if command[0] not in ('codex', 'grok'):
            raise ValueError('research attempted an unexpected executable')
        binary = shutil.which(command[0], path=env['PATH'])
        if not binary:
            raise ValueError('research provider executable unavailable')
        return confine([binary, *command[1:]], folder)

    if phase == 'retrieval':
        from grok_wire import PROVIDER_MODEL, invoke_cli, run_request
        result = run_request(request['prompt'], cwd=str(folder), model=route['model'],
            effort=route['effort'], idle_seconds=request['timeout'],
            invoke=lambda prompt, **kw: invoke_cli(
                prompt, **kw, model=route['model'], env=env,
                popen=lambda argv, **options: subprocess.Popen(
                    confined_command(argv), **options)))
        metadata = result.get('provider_metadata', {})
        if (result.get('status') != 'completed' or not result.get('result')
                or metadata.get('actual_model') != PROVIDER_MODEL or metadata.get('effort') != route['effort']
                or not metadata.get('request_id') or not metadata.get('session_id')
                or metadata.get('model_calls', 0) < 1):
            raise ValueError('Grok actual provider metadata missing or mismatched')
        observed = {'model': PROVIDER_MODEL, 'effort': metadata['effort'],
                    'thread_id': metadata['session_id']}
        answer = result['result']
    else:
        output = runtime / 'result.md'
        process = subprocess.run(confined_command(['codex', 'exec', '--dangerously-bypass-hook-trust', '--json',
            '--sandbox', 'read-only', '--skip-git-repo-check', '-C', str(folder), '-o', str(output),
            '-m', route['model'], '-c', 'model_reasoning_effort=' + json.dumps(route['effort']),
            request['prompt']]), cwd=str(folder), env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=request['timeout'])
        events = []
        for line in process.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        threads = [event.get('thread_id') for event in events if event.get('type') == 'thread.started']
        if (process.returncode or len(threads) != 1 or not output.is_file()
                or not any(event.get('type') == 'turn.completed' for event in events)
                or any(event.get('type') == 'turn.failed' for event in events)):
            raise ValueError('Codex source study did not complete')
        answer = output.read_text()
        if not answer.strip():
            raise ValueError('Codex source study returned no report')
        observed = observed_model(Path(env['CODEX_HOME']), threads[0], route)
    print(json.dumps({'route': route, 'observed': observed, 'status': 'completed', 'result': answer}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('retrieval', 'study'), default='study')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--job', type=Path)
    args = parser.parse_args()
    try:
        if args.preflight:
            print(json.dumps(preflight(args.phase)))
        elif args.job:
            run_job(args.job.resolve(), args.phase)
        else:
            parser.error('--job or --preflight is required')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Source study refused: {type(error).__name__}; verify provider config, authentication and research confinement', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
