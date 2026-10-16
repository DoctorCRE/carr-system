#!/usr/bin/env python3
"""Check that native hook settings install the required safety hooks."""
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parent.parent
REQUIRED = {
    'claude': {'guard-unattended.py', 'record-home-gate.py', 'bash-write-gate.py',
               'write-effect-check.py', 'draft-export-gate.py',
               'weekend-quiet-gate.py', 'costar-lane-gate.py'},
    'codex': {'guard-unattended.py', 'record-home-gate.py'},
}


def check(document, client, repo=REPO):
    hooks = document.get('hooks', document)
    installed = set()
    errors = []
    for event, groups in hooks.items():
        for group in groups:
            for hook in group.get('hooks', []):
                for path in re.findall(r'(?:\{\{REPO\}\}|[^\s\"\']+)/(?:hooks|ops)/[^\s\"\']+\.py', hook.get('command', '')):
                    path = Path(path.replace('{{REPO}}', str(repo)))
                    if not path.is_file():
                        errors.append(f'missing hook script: {path}')
                    if event == "PreToolUse":
                        installed.add(path.name)
    errors.extend(f'{client}: required safety hook is not installed: {name}'
                  for name in sorted(REQUIRED[client] - installed))
    return errors


def main():
    strict = '--strict' in sys.argv
    home = Path.home()
    sources = {
        'claude': REPO / 'ops/config/hooks.json' if strict else home / '.claude/settings.json',
        'codex': REPO / 'ops/config/codex-hooks.json' if strict else home / '.codex/hooks.json',
    }
    errors = []
    for client, path in sources.items():
        if not strict and not path.exists():
            continue
        try:
            errors.extend(check(json.loads(path.read_text()), client))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append(f'{path}: {exc}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Required safety hooks are installed; every configured script exists.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
