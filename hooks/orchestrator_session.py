"""Read the orchestrator role and latest usage from a Claude transcript."""
import json
from pathlib import Path

MARKER = 'CARR-Session-Role: orchestrator'


def transcript_snapshot(path, session):
    first_prompt = None
    latest = None
    with Path(path).open() as transcript:
        for line in transcript:
            record = json.loads(line)
            if record.get('sessionId', session) != session or record.get('isSidechain'):
                continue
            if record.get('type') == 'user' and first_prompt is None and not record.get('isMeta'):
                content = record['message']['content']
                if isinstance(content, list):
                    content = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')
                first_prompt = content
            if record.get('type') == 'assistant':
                latest = record['message'].get('usage')
    return (isinstance(first_prompt, str) and MARKER in first_prompt.splitlines()), latest
