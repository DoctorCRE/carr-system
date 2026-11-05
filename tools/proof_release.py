"""Public release reads for proof runners, within their existing process budgets."""
import json
import math
import re

MAX_BYTES = 1048576


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


def read_release(execute, remaining, origin='https://api.doctorcre.com'):
    budget = remaining()
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError('release deadline exceeded')
    raw = execute(['curl', '-q', '--fail', '--silent', '--show-error',
                   '--max-time', str(budget), '--max-filesize', str(MAX_BYTES),
                   '--write-out', '\n%{http_code}', origin.rstrip('/') + '/release'])
    remaining()
    if len(raw.encode()) > MAX_BYTES + 4:
        raise ValueError('release response exceeds one MiB')
    body, _, status = raw.rpartition('\n')
    if status != '200':
        raise ValueError('release HTTP status refused')
    release = json.loads(body)
    release_revision(release)
    return release
