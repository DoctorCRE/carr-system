"""One API-only SDK job. The parent owns its deadline and process group."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
import subprocess
import sys

import claude_api_config as config

BILLING_ERROR = re.compile(r'credit|billing|payment|balance|\b402\b', re.I)


def billing_error(value):
    return bool(BILLING_ERROR.search(str(value)))


TOOLS = ['Read', 'Write', 'Edit', 'MultiEdit', 'Bash', 'Glob', 'Grep', 'WebFetch', 'WebSearch']


async def execute(request):
    import claude_agent_sdk as sdk
    if getattr(sdk, '__version__', None) != config.SDK_VERSION:
        return {'status': 'failed', 'detail': 'sdk_version_mismatch', 'cost_usd': 0}
    cli = Path(sdk.__file__).parent / '_bundled/claude'
    if not cli.is_file():
        return {'status': 'failed', 'detail': 'bundled_sdk_cli_missing', 'cost_usd': 0}
    auth = subprocess.run([str(cli), 'auth', 'status'], env=os.environ,
                          capture_output=True, text=True, timeout=10)
    try:
        auth_info = json.loads(auth.stdout)
    except ValueError:
        auth_info = {}
    if (auth.returncode or auth_info.get('authMethod') != 'api_key'
            or auth_info.get('apiKeySource') != 'ANTHROPIC_API_KEY'
            or auth_info.get('apiProvider') != 'firstParty'):
        return {'status': 'failed', 'detail': 'api_only_auth_refused', 'cost_usd': 0}
    options = sdk.ClaudeAgentOptions(
        model=request['model'], effort=request['effort'], cwd=request['cwd'],
        cli_path=str(cli), system_prompt={'type': 'preset', 'preset': 'claude_code'},
        tools=TOOLS, allowed_tools=TOOLS, permission_mode='dontAsk',
        setting_sources=[], strict_mcp_config=True,
        max_budget_usd=request['estimate_usd'], env=dict(os.environ),
    )
    actual_model = None
    billing = False
    async for message in sdk.query(prompt=request['task'], options=options):
        if billing_error(getattr(message, 'error', '')):
            billing = True
        if getattr(message, 'model', None):
            actual_model = message.model
        if isinstance(message, sdk.ResultMessage):
            mismatch = actual_model != request['model']
            return {'status': 'failed' if message.is_error or mismatch else 'completed',
                    'detail': 'sdk_model_mismatch' if mismatch else message.subtype, 'result': message.result or '',
                    'actual_model': actual_model, 'session_id': message.session_id,
                    'usage': message.usage, 'cost_usd': message.total_cost_usd,
                    'billing_error': billing or getattr(message, 'api_error_status', None) == 402
                    or (message.is_error and billing_error([message.result, getattr(message, 'errors', [])]))}
    return {'status': 'failed', 'detail': 'sdk_result_missing'}


def main():
    request = json.load(sys.stdin)
    try:
        result = asyncio.run(execute(request))
    except Exception as exc:
        result = {'status': 'failed', 'detail': 'sdk_execution_failed',
                  'billing_error': billing_error(exc) or getattr(exc, 'api_error_status', None) == 402}
    secret = os.environ.get('ANTHROPIC_API_KEY', '')
    output = json.dumps(result)
    print(output.replace(secret, '[redacted]') if secret else output)


if __name__ == '__main__':
    main()
