#!/usr/bin/env python3
"""Install the upstream generated pstack tree for skill-discovering harnesses."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import os


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
        name = temporary.name
    os.replace(name, path)


def sync(source, destination, adapter):
    destination = destination.expanduser().absolute()
    if any(path.is_symlink() for path in [destination, *destination.parents]):
        raise ValueError(f'refusing symlink: {destination}')
    destination = destination.resolve()
    plugins = (Path.home()/'.claude/plugins').resolve()
    if destination.is_relative_to(plugins):
        raise ValueError(f'refusing plugins destination: {destination}')
    if destination.is_relative_to(source.resolve()):
        raise ValueError(f'refusing source destination: {destination}')
    manifest = destination / '.carr-pstack.json'
    previous = json.loads(manifest.read_text())['files'] if manifest.exists() else {}
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    files = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file() and '.git' not in p.relative_to(source).parts}
    source_hashes = {name: digest(data) for name, data in files.items()}
    verification = Path(__file__).with_name('verification')
    for path in verification.rglob('*'):
        if path.is_file():
            files['skills/verify-carr-system/' + str(path.relative_to(verification))] = path.read_bytes()
    files['docs/harness/codex-grok.md'] = adapter.read_bytes()
    for name, data in list(files.items()):
        if name.startswith('skills/') and name.endswith('/SKILL.md'):
            text = data.decode()
            end = text.find('\n# ')
            end = text.index('\n', end + 1) if end >= 0 else text.index('\n---', 4) + 4
            text = text[:end] + '\n\nIn Codex or Grok, read [the harness adapter](../../docs/harness/codex-grok.md) before following this skill.\n' + text[end:]
            files[name] = text.encode()
    desired = {name: digest(data) for name, data in files.items()}
    for name in previous.keys() | files.keys():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f'invalid managed path: {name}')
        path = destination / name
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError(f'refusing symlink: {path}')
        if path.exists():
            current = digest(path.read_bytes())
            if current != previous.get(name) and current != desired.get(name):
                raise ValueError(f'unowned or locally modified file: {path}')
    changed = 0
    for name in previous.keys() - files.keys():
        path = destination / name
        if path.exists():
            path.unlink()
            changed += 1
    for name, data in files.items():
        path = destination / name
        if not path.exists() or path.read_bytes() != data:
            write_atomic(path, data)
            changed += 1
        upstream = source / name
        if upstream.exists():
            path.chmod(upstream.stat().st_mode & 0o777)
    state = json.dumps(dict(source=str(source), revision=revision, source_hashes=source_hashes, files=desired), indent=2, sort_keys=True) + '\n'
    if not manifest.exists() or manifest.read_text() != state:
        write_atomic(manifest, state.encode())
        changed += 1
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home()/'.claude/plugins/marketplaces/cursor-plugins/harness/pstack/claude-code')
    parser.add_argument('--destination', type=Path, default=Path.home()/'.agents/skills/pstack')
    args = parser.parse_args()
    if not (args.source/'skills/poteto-mode/SKILL.md').is_file():
        parser.error('source must be the upstream generated pstack tree')
    changed = sync(args.source.resolve(), args.destination.expanduser(), Path(__file__).with_name('codex-grok.md'))
    print(json.dumps(dict(changed=changed, destination=str(args.destination))))


if __name__ == '__main__':
    main()
