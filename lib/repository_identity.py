"""Exact repository identities shared with the Work Request registry."""
import json
from pathlib import Path

REGISTRY = json.loads((Path(__file__).resolve().parents[1] / 'mcp-server/src/progress-address.v1.json').read_text())
_ACCEPTED = list(REGISTRY['repositories'])
_NAMES = {name.lower(): name for name in _ACCEPTED}
for _identity in REGISTRY['repository_identities'].values():
    for _alias in _identity['aliases']:
        _NAMES[_alias.lower()] = _identity['canonical']
        _ACCEPTED.append(_alias)


def canonical_repository(value):
    return _NAMES.get(value.lower()) if isinstance(value, str) else None


def repository_names(aliases=False):
    return list(_ACCEPTED) if aliases else list(REGISTRY['repositories'])


def repository_for_name(name):
    return next((repo for repo in repository_names() if repo.split('/')[1] == name), None)


def repository_lookup_name(value):
    """Use the retained GitHub redirect name until its alias is retired."""
    canonical = canonical_repository(value)
    identity = next((row for row in REGISTRY['repository_identities'].values()
                     if row['canonical'] == canonical), {})
    aliases = identity.get('aliases', [])
    return aliases[0] if aliases else canonical
