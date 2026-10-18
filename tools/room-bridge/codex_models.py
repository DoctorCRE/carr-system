"""Resolve a Codex family from the CLI catalog on every job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

FAMILIES = ('sol', 'luna')
CODEX_KINDS = ('codex-session', 'codex-exec', 'codex-live')
VERSION = re.compile(r'^gpt-(\d+(?:\.\d+)*)-(sol|luna)$')


def family_default(family: str | None, legacy_model: str | None = None) -> str | None:
    from desks import DeskError
    if family is None and legacy_model:
        family = 'luna' if legacy_model.endswith('-luna') else 'sol'
    if family is not None and family not in FAMILIES:
        raise DeskError('bad_family', 'Codex family must be sol or luna')
    return family


def resolve_model(family: str | None, env: dict | None = None) -> str:
    from desks import DeskError
    family = family_default(family)
    if family is None:
        raise DeskError('missing_family', 'Codex dispatch requires --family or a desk default family')
    source = env or os.environ
    home = Path(source.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    path = home / 'models_cache.json'
    try:
        catalog = json.loads(path.read_text(encoding='utf-8'))
        models = catalog['models']
        if not isinstance(models, list):
            raise ValueError('models must be a list')
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise DeskError('codex_catalog_unavailable', f'Codex model catalog missing or unreadable: {path}') from exc
    candidates: list[tuple[tuple[int, ...], str]] = []
    for entry in models:
        if not isinstance(entry, dict):
            continue
        if (entry.get('visibility', 'list') != 'list' or entry.get('hidden') is True
                or entry.get('retired') is True or entry.get('status') in ('hidden', 'retired')):
            continue
        slug = entry.get('slug')
        if not isinstance(slug, str):
            continue
        match = VERSION.fullmatch(slug)
        if match and match[2] == family:
            candidates.append((tuple(int(n) for n in match[1].split('.')), slug))
    if not candidates:
        raise DeskError('codex_family_unavailable', f'Codex catalog has no versioned {family} model: {path}')
    return max(candidates)[1]
