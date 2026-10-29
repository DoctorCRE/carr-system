"""Select send-time skills; instruction bodies stay local until selection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from credential_env import load_carr_tokens, TokensFilePermissionError


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


BASELINE = ('poteto-mode', 'principle-prove-it-works')
TIMEOUT_S = 5.0
# UTF-8 bytes conservatively bound tokens, leaving room for request framing.
REQUEST_BYTES = 62_000
QUESTION_BYTES = 30_000


def _batches(brief, skills):
    import json
    from typesafe_sdk import Noul

    state_bytes = len(json.dumps(brief, ensure_ascii=False).encode('utf-8'))
    batches, current, size = [], {}, state_bytes
    for index, skill in enumerate(skills):
        question = Noul(instructions=f'Does this task need the {skill.name} skill: {skill.description}?')
        cost = len(question.model_dump_json().encode('utf-8')) + 32
        if state_bytes + cost > QUESTION_BYTES:
            raise ValueError('brief and skill exceed per-question context')
        if current and size + cost > REQUEST_BYTES:
            batches.append(current)
            current, size = {}, state_bytes
        current[str(index)] = question
        size += cost
    if current:
        batches.append(current)
    return batches


async def _score(brief, skills, client_factory, api_key):
    import asyncio
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

    batches = _batches(brief, skills)
    if client_factory is None:
        client_factory = lambda: AsyncTypeSafeClient(
            api_key=api_key, model='jev-latest', timeout=TIMEOUT_S, retry=RetryPolicy(max_retries=0))
    async with client_factory() as client:
        if len(batches) == 1:
            responses = [await client.system_one(state=brief, questions=batches[0])]
        else:
            responses = await asyncio.gather(*(
                client.system_one(state=brief, questions=batch) for batch in batches))
    probabilities = {}
    for batch, response in zip(batches, responses):
        for index in batch:
            value = response.nouls[index].noul
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError('invalid probability')
            probabilities[skills[int(index)]] = value
    return probabilities


def prepare(brief: str, repo: Path, *, home: Path | None = None, client_factory=None):
    """Prepend the Skills line and append the selected local instructions."""
    import asyncio
    import os

    skills = catalog(repo, home=home)
    baseline = [next((s for s in skills if s.name == 'pstack:' + name),
                     next((s for s in skills if s.name == name), None)) for name in BASELINE]
    selected = [s for s in baseline if s is not None]
    probabilities, skipped = {}, ''
    api_key = os.environ.get('TYPESAFE_API_KEY', '').strip()
    if not api_key:
        try:
            api_key = load_carr_tokens(['TYPESAFE_API_KEY'],
                path=(home or Path.home()) / '.config/carr/typesafe.env').get('TYPESAFE_API_KEY', '').strip()
        except TokensFilePermissionError:
            pass
    if not api_key:
        skipped = 'TYPESAFE_API_KEY is missing'
    elif skills:
        async def bounded():
            return await asyncio.wait_for(_score(brief, skills, client_factory, api_key), timeout=TIMEOUT_S)
        try:
            probabilities = asyncio.run(bounded())
        except TimeoutError:
            skipped = 'timeout (5 s)'
        except Exception as error:
            skipped = type(error).__name__
    else:
        skipped = 'skill catalog is empty'
    if probabilities:
        selected.extend(sorted((s for s in skills if s not in selected
                                and s.name.rsplit(':', 1)[-1] not in BASELINE
                                and probabilities[s] >= .80),
                               key=lambda s: (-probabilities[s], s.name, str(s.path)))[:6])
    blocks, picks = ['## Required skills'], []
    if skipped:
        blocks.append(f'Skill picker skipped: {skipped}. Using baseline skills.')
    for skill in selected:
        try:
            text = skill.path.read_text(encoding='utf-8')
        except (OSError, ValueError):
            blocks.append(f'Skill picker skipped: cannot read {skill.path}.')
            continue
        blocks.append(f'### {skill.name}\n\nSKILL.md: {skill.path}\n\n{text}')
        picks.append({'name': skill.name, 'probability': probabilities.get(skill)})
    for name, baseline_skill in zip(BASELINE, baseline):
        if baseline_skill is None:
            blocks.append(f'Baseline skill unavailable: {name}.')
    return ('Skills: ' + ', '.join(str(p['name']) for p in picks) + '\n\n'
            + brief + '\n\n' + '\n\n'.join(blocks)), picks
