"""Local-only runtime identity and installer-derived Outlook source freshness."""
import ast
import json
from pathlib import Path
import re
import subprocess
from subprocess import run as git_run


def _git(repo: Path, *args: str) -> str:
    return git_run(['git', '-C', str(repo), *args], text=True, capture_output=True,
                   check=True, timeout=10).stdout.strip()


def _sources(repo: Path, ref: str) -> set[str]:
    tracked = set(_git(repo, 'ls-tree', '-r', '--name-only', ref).splitlines())
    pending = {p for p in tracked if re.fullmatch(r'bin/install-outlook-.*\.sh', p)}
    found = set()
    while pending:
        path = pending.pop()
        if path in found:
            continue
        found.add(path)
        text = _git(repo, 'show', f'{ref}:{path}')
        if path.endswith(('.c', '.plist')):
            text = re.sub(r'/\*.*?\*/|<!--.*?-->', '', text, flags=re.S)
        if path.endswith('.sh'):
            text = '\n'.join(line.split('#', 1)[0] for line in text.splitlines())
        directories = set()
        if path.startswith('bin/install-outlook-'):
            directories = {item for array in re.findall(r'SOURCES=\(([^)]*)\)', text)
                           for item in array.split()}
        text = text.replace('$REPO/', '')
        candidates: set[str] = set()
        if path.endswith('.py'):
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ImportFrom) and node.module == 'lib':
                    candidates.update(f'lib/{alias.name}.py' for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith('lib.'):
                    candidates.add(node.module.replace('.', '/') + '.py')
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if re.fullmatch(r'[\w-]+(?:/[\w.-]+)+', node.value):
                        candidates.add(node.value)
                    elif re.fullmatch(r'[\w.-]+\.py', node.value):
                        candidates.add(str(Path(path).parent / node.value))
        elif path.endswith(('.sh', '.c', '.plist')):
            candidates.update(re.findall(r'[\w-]+(?:/[\w.-]+)+', text))
            candidates.update(f'lib/{name}.py' for name in re.findall(r'\blib\.([a-z_]+)', text))
            candidates.update(p for label in re.findall(r'com\.carr\.outlook-[a-z-]+', text)
                              for p in tracked if p.endswith('/' + label + '.plist'))
        for candidate in candidates:
            matches = {p for p in tracked if p == candidate or (candidate in directories and p.startswith(candidate + '/'))}
            for match in matches - found:
                if match.endswith(('.py', '.sh', '.c', '.plist')):
                    pending.add(match)
                else:
                    found.add(match)
    return found


def stamp(repo: Path) -> dict:
    result: dict[str, str | int | None] = {'runtime_sha': None, 'runtime_behind': None}
    try:
        result['runtime_sha'] = _git(repo, 'rev-parse', '--short', 'HEAD')
        main = None
        for ref in ('refs/remotes/origin/main', 'refs/heads/main'):
            if git_run(['git', '-C', str(repo), 'show-ref', '--verify', '--quiet', ref], timeout=10).returncode:
                continue
            sha = _git(repo, 'rev-parse', ref)
            if main is not None:
                common = _git(repo, 'merge-base', main, sha)
                if common == sha:
                    continue
                if common != main:
                    raise ValueError('Divergent main refs')
            main = sha
        if main is None:
            result['runtime_note'] = 'main_unknown_locally'
            return result
        if _git(repo, 'rev-parse', '--is-shallow-repository') == 'true':
            raise ValueError('Incomplete history')
        paths = _sources(repo, 'HEAD') | _sources(repo, main)
        latest = _git(repo, 'log', '-1', '--format=%H', main, '--', *sorted(paths))
        if not latest:
            raise ValueError('No installer history')
        _git(repo, 'merge-base', 'HEAD', latest)
        result['runtime_behind'] = int(_git(repo, 'rev-list', '--count', f'HEAD..{latest}'))
    except (OSError, subprocess.SubprocessError, ValueError, SyntaxError):
        result['runtime_note'] = 'history_incomplete'
    return result


def receipt_line(service: str, state: str, reason: str, counts: dict, repo: Path) -> str:
    return f'{service}-receipt ' + json.dumps(dict(state=state, reason=reason, counts=counts, **stamp(repo)))
