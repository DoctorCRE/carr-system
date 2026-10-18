#!/usr/bin/env python3
"""Confined source-study adapter for named Model Room desks.

Model policy lives in the desk registry. This adapter narrows its authority,
uses the existing dispatch wire and verifies runtime model evidence. No live
record credential or caller configuration is made available to the worker.
Providers may use threads and replacement exec, but cannot fork/spawn helper
processes. The OS rejects detachment before it can escape job cancellation.
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def validate_desk(entry):
    if (entry.get('name') != 'source-study' or entry.get('kind') != 'codex-session'
            or not entry.get('family') or not entry.get('effort')
            or entry.get('sandbox') != 'read-only' or entry.get('add_dirs')):
        raise ValueError('source-study desk must name a model family/effort and read-only posture without extra directories')
    return entry


def preflight(phase):
    from desks import Registry
    from grok_wire import validate_entry
    name = 'grok-build' if phase == 'retrieval' else 'source-study'
    entry = Registry().resolve(name)
    if phase == 'retrieval':
        if entry.get('kind') != 'grok-cli':
            raise ValueError('grok-build must be a Grok CLI desk')
        validate_entry(entry)
    else:
        validate_desk(entry)
        # Desks name a family; dispatch resolves the model from the Codex catalog
        # on every job. Pin the resolved model so a catalog change between
        # preflight and the job is refused rather than silently studied.
        import codex_models
        entry = {**entry, 'model': codex_models.resolve_model(entry['family'], os.environ)}
    # Never resume a thread with another job's inputs, cwd or authority.
    bound = {key: entry.get(key) for key in ('name', 'kind', 'family', 'model', 'effort', 'sandbox')}
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
    catalog = auth_home / 'models_cache.json'
    if provider == 'codex' and catalog.is_file():
        # Non-secret model catalog; dispatch resolves the desk family from it.
        shutil.copyfile(catalog, provider_home / 'models_cache.json')
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
        raise ValueError('Model Room result lacks a thread identity')
    matches = []
    for path in (codex_home / 'sessions').rglob('*.jsonl'):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        if not any(row.get('type') == 'session_meta' and row.get('payload', {}).get('id') == thread_id
                   for row in rows):
            continue
        contexts = [row['payload'] for row in rows if row.get('type') == 'turn_context']
        if not contexts or any(c.get('model') != entry['model'] or c.get('effort') != entry['effort']
                               for c in contexts):
            raise ValueError('Model Room actual model/effort mismatch')
        matches.append({'model': entry['model'], 'effort': entry['effort'], 'thread_id': thread_id})
    if len(matches) != 1:
        raise ValueError('Model Room actual model evidence missing or ambiguous')
    return matches[0]


def run_job(folder, phase):
    with tempfile.TemporaryDirectory(prefix='.runtime-', dir=folder) as temporary:
        return _run_job(folder, phase, Path(temporary))


def _run_job(folder, phase, runtime):
    from desks import Registry
    import dispatch
    request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
    route = preflight(phase)
    if route != request['route']:
        raise ValueError('Model Room desk changed after preflight')
    entry = {**route, 'cwd': str(folder), 'thread_id': None}
    private_registry = folder / 'desks.json'
    private_registry.write_text(json.dumps({'desks': {route['name']: entry}}))
    original_home = Path.home()
    provider = 'grok' if phase == 'retrieval' else 'codex'
    auth_home = original_home / '.grok' if provider == 'grok' else Path(os.environ.get('CODEX_HOME', original_home / '.codex'))
    env = runtime_env(runtime, auth_home, os.environ, provider=provider)
    # The trusted adapter stays outside confinement. Only the existing wire's
    # provider child is confined; it has no access to transport code or host
    # credentials. The wire's last-message temp file must also live in the job.
    original_run = subprocess.run
    original_env = dict(os.environ)
    original_tmp = tempfile.tempdir
    original_timeout = dispatch.CODEX_TIMEOUT_S

    def confined_run(command, **kwargs):
        if command[0] not in ('codex', 'grok'):
            raise ValueError('research wire attempted an unexpected executable')
        binary = shutil.which(command[0], path=env['PATH'])
        if not binary:
            raise ValueError('research provider executable unavailable')
        kwargs['env'] = env
        return original_run(confine([binary, *command[1:]], folder), **kwargs)

    try:
        os.environ.clear()
        os.environ.update(env)
        tempfile.tempdir = env['TMPDIR']
        dispatch.CODEX_TIMEOUT_S = request['timeout']
        result = dispatch.dispatch(route['name'], request['prompt'], registry=Registry(private_registry),
                                   results_path=folder / 'dispatch.jsonl', env=env, fresh=True,
                                   provider_run=confined_run)
    finally:
        tempfile.tempdir = original_tmp
        dispatch.CODEX_TIMEOUT_S = original_timeout
        os.environ.clear()
        os.environ.update(original_env)
    if result.get('desk') != route['name'] or result.get('status') != 'completed' or not result.get('result'):
        raise ValueError('Model Room result is incomplete or from another desk')
    if phase == 'retrieval':
        from grok_wire import PROVIDER_MODEL
        metadata = result.get('provider_metadata', {})
        if (metadata.get('actual_model') != PROVIDER_MODEL or metadata.get('effort') != route['effort']
                or not metadata.get('request_id') or not metadata.get('session_id')
                or metadata.get('model_calls', 0) < 1):
            raise ValueError('Grok actual provider metadata missing or mismatched')
        observed = {'model': PROVIDER_MODEL, 'effort': metadata['effort'],
                    'thread_id': metadata['session_id']}
    else:
        observed = observed_model(Path(env['CODEX_HOME']), result.get('thread_id'), entry)
    print(json.dumps({'route': route, 'observed': observed, 'status': 'completed', 'result': result['result']}))


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
        print(f'Model Room refused: {type(error).__name__}; verify named desk, authentication and research confinement', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
