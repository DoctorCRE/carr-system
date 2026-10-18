#!/usr/bin/env python3
import json
import re
import sys

BUILD = re.compile(r'\b(?:writ(?:e|ing)\s+(?:the\s+)?code|PR|pull request|commits?|branch(?:es)?)\b', re.I)
READ_ONLY = re.compile(r'^\s*(?:review|search|explore|plan|inspect|find|explain)\b', re.I)
BUILD_ACTION = re.compile(
    r'\b(?:writ(?:e|ing)\s+(?:the\s+)?code|implement|commit\s+(?:the\s+)?changes|'
    r'(?:open|create|make|push)\s+(?:(?:a|the)\s+)?(?:PR|pull request|commit|branch))\b', re.I)


def main():
    payload = json.load(sys.stdin)
    if payload.get('tool_name') != 'Agent':
        return
    launch = payload.get('tool_input') or {}
    if launch.get('subagent_type', '').lower() not in ('general-purpose', 'claude'):
        return
    prompt = launch.get('prompt') or ''
    if not BUILD.search(prompt):
        return
    if READ_ONLY.search(prompt) and not BUILD_ACTION.search(prompt):
        return
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'Use pstack:poteto-agent for build work instead of general-purpose or claude.',
    }}))


if __name__ == '__main__':
    main()
