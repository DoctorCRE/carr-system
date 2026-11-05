#!/usr/bin/env python3
"""Remove exact legacy CARR Claude Code user registrations after merge."""
import argparse
import copy
import json
import os
from pathlib import Path
import tempfile

REPO = Path(__file__).resolve().parents[1]


def cleaned(document, kind, home):
    result = copy.deepcopy(document)
    if kind == 'mcp':
        servers = result.get('mcpServers', {})
        expected = json.loads((REPO / '.mcp.json').read_text())['mcpServers']['carr']
        if servers.get('carr') == expected:
            del servers['carr']
        elif 'carr' in servers:
            raise ValueError('carr user registration differs from the moved server; refusing removal')
    else:
        legacy = json.loads((REPO / 'ops/config/claude-legacy-user-hooks.json').read_text().replace(
            '{{REPO}}', str(home / 'carr-system')))
        for event, groups in result.get('hooks', {}).items():
            retained = []
            for group in groups:
                scope = {k: v for k, v in group.items() if k != 'hooks'}
                moved = [h for g in legacy.get(event, [])
                         if {k: v for k, v in g.items() if k != 'hooks'} == scope
                         for h in g['hooks']]
                hooks = [h for h in group.get('hooks', []) if h not in moved]
                if hooks or not group.get('hooks'):
                    retained.append({**group, 'hooks': hooks})
            result['hooks'][event] = retained
    return result


def migrate(home, apply=False):
    plans = []
    for path, kind in [(home / '.claude.json', 'mcp'),
                       (home / '.claude/settings.json', 'hooks')]:
        if not path.exists():
            continue
        if path.is_symlink():
            raise ValueError(f'refusing symlink: {path}')
        raw = path.read_bytes()
        document = json.loads(raw)
        result = cleaned(document, kind, home)
        if result != document:
            plans.append((path, raw, result))
    for path, raw, result in plans:
        print(f'{"REMOVE" if apply else "WOULD REMOVE"} exact legacy CARR entries from {path}')
        if not apply:
            continue
        if path.read_bytes() != raw:
            raise ValueError(f'configuration changed during migration: {path}')
        fd, backup = tempfile.mkstemp(prefix=path.name + '.before-carr-scope-', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.scope-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(result, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            if path.read_bytes() != raw:
                raise ValueError(f'configuration changed during migration: {path}')
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        print(f'BACKUP {backup}')
    if not plans:
        print('No legacy CARR entries to remove.')
    return len(plans)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='write changes with private backups')
    parser.add_argument('--home', type=Path, default=Path.home(), help='configuration home (defaults to current user)')
    args = parser.parse_args()
    try:
        migrate(args.home.resolve(), args.apply)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f'REFUSED: {exc}\n')


if __name__ == '__main__':
    main()
