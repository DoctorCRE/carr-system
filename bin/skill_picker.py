"""Stamp typed job workflows and their local skill instructions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path


def catalog(repo: Path, *, home: Path | None = None) -> list[Skill]:
    home = home or Path.home()
    roots: list[tuple[str, Path]] = []
    for plugin in ('pstack', 'mattpocock-skills'):
        versions = list((home / '.claude/plugins/cache').glob(f'*/{plugin}/*/skills'))
        if versions:
            latest = max(versions, key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.parent.name)))
            roots.append((plugin + ':', latest))
    roots.extend(('', p) for p in sorted((home / '.agents/skills').glob('*')) if p.is_dir())
    roots.append(('', repo / '.claude/skills'))
    fallback_roots = [p for root in (home / '.claude/skills', home / '.codex/skills')
                      for p in sorted(root.glob('*')) if p.is_dir() and not p.name.startswith('.')]
    existing_root_count = len(roots)
    roots.extend(('', p) for p in fallback_roots)
    skills: dict[Path, Skill] = {}
    unprefixed_names: set[str] = set()
    for index, (prefix, root) in enumerate(roots):
        fallback = index >= existing_root_count
        for path in sorted(root.rglob('SKILL.md')):
            if fallback and any(part == 'synced' or part.startswith('.')
                                for part in (root.name, *path.relative_to(root).parts[:-1])):
                continue
            path = path.resolve()
            if path in skills:
                continue
            try:
                text = path.read_text(encoding='utf-8')
                if not text.startswith('---\n'):
                    continue
                header, separator, _ = text[4:].partition('\n---')
                fields = yaml.safe_load(header) if separator else None
                if not isinstance(fields, dict):
                    continue
                name, description = fields.get('name'), fields.get('description')
                if isinstance(name, str) and name.strip() and isinstance(description, str) and description.strip():
                    name = name.strip()
                    if not prefix:
                        if fallback and name in unprefixed_names:
                            continue
                        unprefixed_names.add(name)
                    skills[path] = Skill(prefix + name, description.strip(), path)
            except (OSError, ValueError, yaml.YAMLError):
                continue
    return sorted(skills.values(), key=lambda s: (s.name, str(s.path)))


def header_type(brief: str) -> str | None:
    """Read a single workflow declaration before task text begins."""
    header = []
    for line in brief.splitlines():
        if not line.strip() or line.lstrip().startswith(('```', '~~~', '<work-request-text>', 'Work Request:')):
            break
        header.append(line)
    declared = re.findall(r'^Type:([^\n]*)$', '\n'.join(header), re.MULTILINE)
    return declared[0].strip() if len(declared) == 1 else None


def prepare(brief: str, repo: Path, *, home: Path | None = None):
    """Append every stage and skill required by the brief's declared job type."""
    jobs = yaml.safe_load((repo / 'ops/config/job-workflows.v1.yaml').read_text())['job_types']
    declared = header_type(brief)
    if declared not in jobs:
        raise ValueError('brief requires one Type: line; valid types: ' + ', '.join(jobs))
    job = jobs[declared]
    names = dict.fromkeys(name for stage in job['stages'].values()
                         for name in stage['skills'] + [item['skill'] for item in
                                                        stage.get('conditional_skills', [])])
    available: dict[str, Skill] = {}
    for skill in catalog(repo, home=home):
        available.setdefault(skill.name, skill)
    blocks = ['## Required skills', '### Workflow stages\n\n```yaml\n'
              + yaml.safe_dump(job, sort_keys=False).rstrip() + '\n```']
    for name in names:
        if name not in available:
            raise ValueError(f'required skill unavailable: {name}')
        skill = available[name]
        text = skill.path.read_text(encoding='utf-8')
        blocks.append(f'### {name}\n\nSKILL.md: {skill.path}\n\n{text}')
    return brief + '\n\n' + '\n\n'.join(blocks), [{'name': name} for name in names]
