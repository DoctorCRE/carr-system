"""Find missing build skills from the native Codex rollout for a PR's Card."""
import json
from pathlib import Path
import re

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / 'ops/config/job-workflows.v1.yaml'


def findings(body, sessions=None):
    card_match = re.search(r'^Card:\s*(WR\d+\.S\d+\.J\d+)\s*$', body, re.M)
    if not card_match:
        return ['PR has no Card; build session log cannot be found']
    card = card_match.group(1)
    sessions = sessions or Path.home() / '.codex/sessions'
    matches = []
    for path in sessions.rglob('rollout-*.jsonl'):
        try:
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            messages = [part.get('text', '') for row in rows
                        if row.get('type') == 'response_item'
                        for payload in [row.get('payload', {})]
                        if payload.get('type') == 'message' and payload.get('role') == 'user'
                        for part in payload.get('content', []) if isinstance(part, dict)]
            briefs = [message for message in messages if re.search(r'^Card:\s*' + re.escape(card) + r'\s*$', message, re.M)
                      and re.search(r'^Type:\s*\S+', message, re.M)]
            if briefs:
                matches.append((briefs[0], rows))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    if not matches:
        return [f'Build session log missing for Card {card}']
    if len(matches) != 1:
        return [f'Multiple build session logs found for Card {card}; skill use cannot be determined']
    brief, rows = matches[0]
    job_type = re.search(r'^Type:\s*(\S+)', brief, re.M).group(1)
    workflows = yaml.safe_load(WORKFLOWS.read_text())['job_types']
    if job_type not in workflows:
        return [f'Unknown job type: {job_type}']
    required = workflows[job_type]['stages']['build']['skills']
    calls = [row.get('payload', {}) for row in rows if row.get('type') == 'response_item'
             and row.get('payload', {}).get('type') in ('function_call', 'custom_tool_call')]
    outputs = {row.get('payload', {}).get('call_id'): str(row.get('payload', {}).get('output', ''))
               for row in rows if row.get('type') == 'response_item'
               and row.get('payload', {}).get('type') in ('function_call_output', 'custom_tool_call_output')}
    used = set()
    for skill in required:
        name = skill.split(':')[-1]
        pattern = re.compile(r'(?:^|/)'+re.escape(name)+r'/SKILL\.md(?:[\s\'"\\]|$)')
        for call in calls:
            text = str(call.get('arguments', call.get('input', '')))
            output = outputs.get(call.get('call_id'), '')
            success = (re.search(r'(?:exit code|exit_code)["\s:=]+0\b', output, re.I)
                       or re.search(r'\bname:\s*' + re.escape(name) + r'\b', output))
            if success and pattern.search(text) and re.search(r'\b(cat|head|sed|read|open)\b', text):
                used.add(skill)
                break
    return [f'Required skill did not run: {skill}' for skill in required if skill not in used]
