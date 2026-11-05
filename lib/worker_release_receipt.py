"""Immutable Worker lane authorization, never aggregate delivery proof."""
from __future__ import annotations

import io
import json
import re
import subprocess
import zipfile

from ops.git_env import scrubbed_env

REPOSITORY = 'DoctorCRE/carr-system'
SCHEMA = 'carr-worker-release/v1'
MAX_ARCHIVE = 65536
MAX_RECEIPT = 8192
SHA = re.compile(r'[0-9a-f]{40}')
VERSION = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')


def identity(live):
    try:
        sha, version = live['git_sha']['value'], live['worker_version']['id']
        if (live['env']['value'] != 'production' or not isinstance(sha, str)
                or not SHA.fullmatch(sha) or not isinstance(version, str)
                or not VERSION.fullmatch(version)):
            raise ValueError('production identity unavailable')
        return sha, version
    except (KeyError, TypeError) as error:
        raise ValueError('production identity unavailable') from error


def seal(live, sha, version, verbs, run_id, attempt):
    if (identity(live) != (sha, version) or type(verbs) is not int or verbs < 1
            or type(live.get('verb_count')) is not int or live['verb_count'] != verbs
            or any(type(v) is not int or v < 1 for v in (run_id, attempt))):
        raise ValueError('verified Worker identity mismatch')
    return {'schema': SCHEMA, 'repository': REPOSITORY, 'environment': 'production',
            'source_sha': sha, 'worker_version': version, 'verb_count': verbs,
            'run_id': run_id, 'run_attempt': attempt}


def github(path, *, binary=False):
    result = subprocess.run(['gh', 'api', path], check=True, capture_output=True,
                            timeout=20, env=scrubbed_env())
    limit = MAX_ARCHIVE if binary else 262144
    if len(result.stdout) > limit:
        raise ValueError('GitHub receipt response too large')
    return result.stdout if binary else json.loads(result.stdout)


def unpack(data):
    if len(data) > MAX_ARCHIVE:
        raise ValueError('Worker receipt archive too large')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            if (len(files) != 1 or files[0].filename != 'worker-release.json'
                    or files[0].file_size > MAX_RECEIPT):
                raise ValueError('Worker receipt archive layout invalid')
            return json.loads(archive.read(files[0]))
    except (zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError('Worker receipt archive invalid') from error


def discover(live, *, api=github):
    sha, version = identity(live)
    name = f'worker-release-{sha}'
    base = f'repos/{REPOSITORY}/actions'
    runs = api(f'{base}/workflows/deploy.yml/runs?branch=main&per_page=2')['workflow_runs']
    for producer in runs[:2]:
        run_id = producer.get('id')
        if type(run_id) is not int or run_id < 1 or not trusted_run(producer, run_id):
            continue
        artifacts = api(f'{base}/runs/{run_id}/artifacts?name={name}&per_page=2')['artifacts']
        for artifact in artifacts[:2]:
            if (artifact.get('name') != name or artifact.get('expired') is not False
                    or type(artifact.get('id')) is not int or artifact['id'] < 1
                    or type(artifact.get('size_in_bytes')) is not int
                    or not 0 < artifact['size_in_bytes'] <= MAX_ARCHIVE
                    or artifact.get('workflow_run', {}).get('id') != run_id):
                continue
            try:
                receipt = unpack(api(f'{base}/artifacts/{artifact["id"]}/zip', binary=True))
            except (ValueError, KeyError, TypeError):
                continue
            expected = seal(live, sha, version, live.get('verb_count'), run_id, producer.get('run_attempt'))
            integers = ('verb_count', 'run_id', 'run_attempt')
            if (isinstance(receipt, dict) and all(type(receipt.get(k)) is int for k in integers)
                    and receipt == expected):
                run = api(f'{base}/runs/{run_id}')
                if trusted_run(run, run_id) and run['run_attempt'] == receipt['run_attempt']:
                    return {**receipt, 'artifact_id': artifact['id']}
                raise ValueError('Worker receipt producer changed during discovery')
    raise ValueError('matching verified Worker lane receipt unavailable')


def trusted_run(run, run_id):
    return (type(run.get('id')) is int and run['id'] == run_id
            and type(run.get('run_attempt')) is int and run['run_attempt'] > 0
            and run.get('path') == '.github/workflows/deploy.yml'
            and run.get('event') in {'workflow_run', 'workflow_dispatch'}
            and run.get('head_branch') == 'main'
            and run.get('repository', {}).get('full_name') == REPOSITORY
            and run.get('head_repository', {}).get('full_name') == REPOSITORY)
