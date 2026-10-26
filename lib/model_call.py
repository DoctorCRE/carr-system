"""One isolated headless model call with a schema-checked JSON reply.

Shared by the Outlook intake (next steps) and the email router (proposals). The
call runs `claude -p` on the signed-in subscription in an empty folder with no
tools, settings, MCP servers, slash commands or session file, and without an
API key in its environment. The model name comes from the per-machine settings
(`model` in ~/.config/carr/outlook.json, default haiku).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile


def command(model: str, schema: dict, system_prompt: str) -> list[str]:
    return ['claude', '-p', '--model', model, '--no-session-persistence', '--tools', '',
            '--setting-sources', '', '--strict-mcp-config', '--disable-slash-commands',
            '--exclude-dynamic-system-prompt-sections', '--output-format', 'json',
            '--json-schema', json.dumps(schema), '--system-prompt', system_prompt]


def ask(prompt: str, *, model: str, schema: dict, system_prompt: str, timeout: int = 180) -> dict:
    """Return the reply's structured output; raise on a failed run or a reply without one."""
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'CLAUDECODE')}
    with tempfile.TemporaryDirectory() as empty:
        done = subprocess.run(command(model, schema, system_prompt), input=prompt, capture_output=True,
                              text=True, cwd=empty, env=env, timeout=timeout)
    if done.returncode:
        raise RuntimeError(f'model exit {done.returncode}')
    reply = json.loads(done.stdout)
    out = reply.get('structured_output') if isinstance(reply, dict) else None
    if not isinstance(reply, dict) or reply.get('is_error') or not isinstance(out, dict):
        raise ValueError('model reply carries no result')
    return out
