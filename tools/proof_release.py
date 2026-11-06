"""Validate the production release identity consumed by proof runners."""
import re


def release_revision(release):
    try:
        revision = release['git_sha']['value']
        valid = (release['ok'] is True and release['env']['value'] == 'production'
                 and isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision))
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError('release identity unavailable')
    return revision
