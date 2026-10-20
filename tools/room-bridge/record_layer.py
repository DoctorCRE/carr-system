"""The record-layer posture admits CARR tools or refuses before launch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from desks import DeskError, tool_posture

REFUSALS = {
    'hermes-nous': 'Hermes configuration still merges inherited settings; hooks and plugins have no verified isolation profile',
    'grok-cli': 'Grok has no verified strict MCP config override; --tools alone cannot exclude inherited MCP servers',
    'flash-local': 'Flash has no CARR MCP tool loop; its script and code routes cannot take record-layer work',
}


def enforce(entry, *, kind=None):
    kind = kind or entry['kind']
    tool_posture(kind, entry.get('posture'))
    if entry.get('posture') == 'record-layer' and kind in REFUSALS:
        raise DeskError('record_layer_unsupported', REFUSALS[kind])


CODEX_SANDBOX = ('-c', 'sandbox_mode="read-only"',
                 '-c', 'sandbox_workspace_write.network_access=false',
                 '-c', 'approval_policy="never"')
CANARY = r"""
import errno, json, socket, sys
from pathlib import Path
results = {}
try:
    Path(sys.argv[1]).write_text('example.test confinement canary')
    results['file_write'] = 'allowed'
except OSError as exc:
    results['file_write'] = 'denied' if exc.errno in (errno.EPERM, errno.EACCES) else 'inconclusive'
try:
    with socket.create_connection(('1.1.1.1', 443), timeout=2):
        results['network'] = 'allowed'
except OSError as exc:
    results['network'] = 'denied' if exc.errno in (errno.EPERM, errno.EACCES) else 'inconclusive'
print(json.dumps(results))
"""


def run_codex(entry, task, *, env, run, timeout_s):
    """Prove the native read-only policy before one isolated business turn."""
    from claude_desktop_wire import CARR_MCP_URL
    source = os.environ if env is None else env
    if source.get('CARR_MCP_URL', CARR_MCP_URL) != CARR_MCP_URL:
        raise DeskError('untrusted_carr_endpoint',
                        'record-layer launch requires the configured CARR endpoint ' + CARR_MCP_URL)
    for name in ('CODEX_API_KEY', 'CARR_MCP_TOKEN'):
        if not source.get(name, '').strip():
            raise DeskError('record_layer_auth_missing', 'Codex record-layer requires explicit ' + name)
    with tempfile.TemporaryDirectory(prefix='room-codex-business-') as tmp:
        root = Path(tmp)
        home, codex_home, work = (root / name for name in ('home', 'codex', 'work'))
        for directory in (home, codex_home, work):
            directory.mkdir()
        isolated = {name: source[name] for name in ('PATH', 'CODEX_API_KEY', 'CARR_MCP_TOKEN') if name in source}
        isolated.update(HOME=str(home), CODEX_HOME=str(codex_home), TMPDIR=tmp)
        options = dict(env=isolated, cwd=str(work), capture_output=True, text=True,
                       timeout=timeout_s, stdin=subprocess.DEVNULL)
        try:
            probe = run(['codex', 'sandbox', *CODEX_SANDBOX, '--',
                         sys.executable, '-I', '-c', CANARY, str(work / 'canary')], **options)
            try:
                canaries = json.loads(probe.stdout)
            except (TypeError, ValueError):
                canaries = {}
            for name in ('file_write', 'network'):
                if probe.returncode != 0 or not isinstance(canaries, dict) or canaries.get(name) != 'denied':
                    raise DeskError('record_layer_canary_failed', name + ' canary did not prove denial')
            argv = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                    '--skip-git-repo-check', '--json', '--sandbox', 'read-only', *CODEX_SANDBOX,
                    '-C', str(work), '-m', entry['model'],
                    '-c', 'model_reasoning_effort=' + json.dumps(entry['effort'])]
            config = {
                'mcp_servers.carr.url': CARR_MCP_URL,
                'mcp_servers.carr.required': True,
                'mcp_servers.carr.bearer_token_env_var': 'CARR_MCP_TOKEN',
                'features.shell_tool': False, 'features.view_image': False,
                'features.multi_agent': False, 'features.apps': False,
                'features.plugins': False, 'features.hooks': False,
                'web_search': 'disabled',
            }
            for key, value in config.items():
                argv += ['-c', key + '=' + json.dumps(value)]
            proc = run([*argv, task], **options)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeskError('record_layer_launch_failed',
                            'Codex confined launch failed: ' + type(exc).__name__) from exc
        events = []
        for line in (proc.stdout or '').splitlines():
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except ValueError:
                pass
        messages = [event['item'].get('text', '') for event in events
                    if event.get('type') == 'item.completed'
                    and isinstance(event.get('item'), dict)
                    and event['item'].get('type') == 'agent_message']
        completed = proc.returncode == 0 and messages and any(
            event.get('type') == 'turn.completed' for event in events)
        return {'status': 'completed' if completed else 'failed',
                'result': '\n'.join(messages), 'canaries': canaries,
                'detail': '' if completed else 'Codex confined turn failed; no retry',
                'resumed': False, 'termination_confirmed': True}
