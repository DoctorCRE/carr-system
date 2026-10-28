"""Select send-time skills by the catalog names the brief mentions; nothing leaves the machine."""
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
    skills: dict[Path, Skill] = {}
    for prefix, root in roots:
        for path in sorted(root.rglob('SKILL.md')):
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
                    skills[path] = Skill(prefix + name.strip(), description.strip(), path)
            except (OSError, ValueError, yaml.YAMLError):
                continue
    return sorted(skills.values(), key=lambda s: (s.name, str(s.path)))


BASELINE = ('poteto-mode', 'principle-prove-it-works')
# What the old Jev picker chose for nearly every implementation brief; used when a brief names nothing else.
DEFAULT = ('tdd', 'implement')
MAX_NAMED = 6


def short(skill: Skill) -> str:
    return skill.name.rsplit(':', 1)[-1]


def mentioned(skill: Skill, brief: str) -> bool:
    """The skill's name appears as a whole token, its words joined by hyphens or spaces, in any case."""
    words = r'[\s-]+'.join(map(re.escape, re.split(r'[\s-]+', short(skill))))
    return re.search(rf'(?<![\w-]){words}(?![\w-])', brief, re.I) is not None


def prepare(brief: str, repo: Path, *, home: Path | None = None):
    """Prepend the Skills line and append the selected local instructions."""
    skills = catalog(repo, home=home)
    baseline = [next((s for s in skills if s.name == 'pstack:' + name),
                     next((s for s in skills if s.name == name), None)) for name in BASELINE]
    selected = [s for s in baseline if s is not None]
    others = [s for s in skills if s not in selected and short(s) not in BASELINE]
    named = [s for s in others if mentioned(s, brief)]
    if all(short(s) in DEFAULT for s in named):
        named = [s for s in others if short(s) in DEFAULT]
    selected.extend(named[:MAX_NAMED])
    blocks, picks = ['## Required skills'], []
    for skill in selected:
        try:
            text = skill.path.read_text(encoding='utf-8')
        except (OSError, ValueError):
            blocks.append(f'Skill picker skipped: cannot read {skill.path}.')
            continue
        blocks.append(f'### {skill.name}\n\nSKILL.md: {skill.path}\n\n{text}')
        picks.append({'name': skill.name})
    for name, baseline_skill in zip(BASELINE, baseline):
        if baseline_skill is None:
            blocks.append(f'Baseline skill unavailable: {name}.')
    return ('Skills: ' + ', '.join(p['name'] for p in picks) + '\n\n'
            + brief + '\n\n' + '\n\n'.join(blocks)), picks
