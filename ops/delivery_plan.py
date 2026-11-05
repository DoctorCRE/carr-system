"""Canonical read-only delivery plans from immutable first-parent Git objects."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'contracts/production-proof-plan.v1.json'
INVENTORY = 'contracts/delivery-inventory.v1.json'
SCHEMA = 'contracts/delivery-plan.v1.json'


def classify_paths(paths, policy, inventory=None):
    if not isinstance(paths, list) or not paths or len(paths) > 10000:
        raise ValueError('changed paths missing or exceed 10000')
    if inventory is None:
        inventory = json.loads((ROOT / INVENTORY).read_text())
    product, targets, lanes = [], set(), set()
    for name in paths:
        if (not isinstance(name, str) or not name or name.startswith('/') or '\\' in name
                or any(p in {'.', '..', ''} for p in name.split('/'))
                or any(ord(c) < 32 for c in name)):
            raise ValueError('invalid changed path')
        matches = [lane for lane, rule in inventory['lanes'].items()
                   if any(fnmatch.fnmatchcase(name, pattern) for pattern in rule['paths'])]
        if not matches:
            raise ValueError('unclassified changed path')
        non_product = any(fnmatch.fnmatchcase(name, p) for p in policy['x-non-product-paths'])
        if non_product:
            lanes.update(lane for lane in matches if lane in {'docs', 'control'})
            if not set(matches) & {'docs', 'control'}:
                lanes.add('control')
        else:
            lanes.update(matches)
            product.append(name)
            declared = {target for target, patterns in policy['x-required-targets'].items()
                        if any(fnmatch.fnmatchcase(name, p) for p in patterns)}
            targets.update(declared or {t for lane in matches for t in inventory['lanes'][lane]['targets']})
    return {'product_paths': product, 'required_targets': sorted(targets), 'lanes': sorted(lanes)}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


class Source:
    def __init__(self, root):
        self.root = Path(root)
        self.deadline = time.monotonic() + 60
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_GRAFT_FILE=os.devnull, GIT_NO_LAZY_FETCH='1')

    def git(self, *args):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('resolution deadline exceeded')
        result = subprocess.run(['git', '--no-replace-objects', '-C', str(self.root), *args],
                                env=self.env, capture_output=True, timeout=min(15, remaining))
        if result.returncode:
            raise ValueError('source object unavailable')
        return result.stdout

    def blob(self, sha, path):
        if not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', path) or any(
                p in {'.', '..'} for p in path.split('/')):
            raise ValueError('invalid source path')
        fields = self.git('ls-tree', '-l', sha, '--', path).split()
        if (len(fields) != 5 or fields[0] not in {b'100644', b'100755'} or fields[1] != b'blob'
                or not fields[3].isdigit() or int(fields[3]) > 1048576):
            raise ValueError('source is missing, oversized, or not a regular file')
        return self.git('cat-file', 'blob', fields[2].decode())

    def ref(self, sha, path):
        return {'path': path, 'commit': sha, 'sha256': digest(self.blob(sha, path))}


def validate(value, schema, label='requirement'):
    types = {'object': dict, 'array': list, 'string': str, 'integer': int}
    kind = schema.get('type')
    if kind and (not isinstance(value, types[kind]) or kind == 'integer' and isinstance(value, bool)):
        raise ValueError(f'{label}: expected {kind}')
    if 'enum' in schema and not any(type(value) is type(v) and value == v for v in schema['enum']):
        raise ValueError(f'{label}: unregistered value')
    if kind == 'object':
        if set(schema['required']) - value.keys() or value.keys() - schema['properties'].keys():
            raise ValueError(f'{label}: missing or unknown fields')
        for key, child in value.items():
            validate(child, schema['properties'][key], label + '.' + key)
    if kind == 'array':
        if not schema['minItems'] <= len(value) <= schema['maxItems']:
            raise ValueError(f'{label}: array bounds')
        for item in value:
            validate(item, schema['items'], label)
    if kind == 'string':
        if len(value.strip()) < schema.get('minLength', 0) or value.strip().lower() in {'todo', 'tbd', 'placeholder'}:
            raise ValueError(f'{label}: empty or placeholder')
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            raise ValueError(f'{label}: invalid format')
    if kind == 'integer' and not schema['minimum'] <= value <= schema['maximum']:
        raise ValueError(f'{label}: budget bounds')


def resolve(root, sha, *, main_ref='origin/main'):
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('source SHA must be full lowercase commit SHA')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', main_ref) or main_ref.startswith('-'):
        raise ValueError('invalid main ref')
    source = Source(root)
    if source.git('cat-file', '-t', sha).strip() != b'commit':
        raise ValueError('source must be a commit')
    # Ancestry alone admits side-branch commits that never landed independently.
    chain = source.git('rev-list', '--first-parent', '--max-count=100000', main_ref).decode().splitlines()
    if sha not in chain:
        raise ValueError('source not in first-parent main history')
    parents = source.git('rev-list', '--parents', '-n', '1', sha).decode().split()
    if len(parents) < 2:
        raise ValueError('source has no first parent')
    parent = parents[1]
    policy = json.loads(source.blob(sha, POLICY))
    inventory = json.loads(source.blob(sha, INVENTORY))
    schema = json.loads(source.blob(sha, SCHEMA))
    if (inventory['schema'] != 'carr-delivery-inventory.v1' or schema['$id'] != 'carr-delivery-plan.v1'
            or inventory['repository'] != 'DoctorCRE/carr-system' or inventory['environment'] != 'production'
            or set(inventory['lanes']) != {'database', 'worker', 'studio', 'app', 'control', 'docs'}):
        raise ValueError('unsupported delivery contract')
    paths = sorted(set(source.git('diff', '--name-only', '--no-renames', '-z', parent, sha)
                       .decode().rstrip('\0').split('\0')) - {''})
    classification = classify_paths(paths, policy, inventory) if paths else {
        'required_targets': [], 'lanes': ['docs']}
    requirements, covered, ids = [], set(), set()
    names = [p for p in paths if re.fullmatch(r'proofs/requirements/[A-Za-z0-9][A-Za-z0-9._-]*\.json', p)]
    if len(names) > 100:
        raise ValueError('requirements exceed 100')
    deleted = set(source.git('diff', '--name-only', '--no-renames', '--diff-filter=D', '-z', parent, sha)
                  .decode().rstrip('\0').split('\0'))
    for name in names:
        if name in deleted:
            raise ValueError('deleted requirement cannot supply proof')
        raw = source.blob(sha, name)
        requirement = json.loads(raw)
        validate(requirement, policy)
        if requirement['requirement_id'] in ids:
            raise ValueError('duplicate requirement ID')
        ids.add(requirement['requirement_id'])
        assertions = requirement['assertions']
        if len({a['id'] for a in assertions}) != len(assertions):
            raise ValueError('duplicate assertion IDs')
        observers, runner_targets = [], set()
        for runner_id in sorted({a['runner'] for a in assertions}):
            runner = policy['x-runners'].get(runner_id)
            if runner is None:
                raise ValueError('unregistered runner')
            if 'sources' in runner:
                bindings = runner['sources']
                if ('revision' in runner or not 1 <= len(bindings) <= 16 or runner['path'] not in bindings):
                    raise ValueError('invalid observer source binding')
                refs = [source.ref(sha, p) for p in sorted(bindings)]
                if any(ref['sha256'] != bindings[ref['path']] for ref in refs):
                    raise ValueError('observer source digest mismatch')
            else:
                revision = runner['revision']
                if not re.fullmatch('[0-9a-f]{40}', revision):
                    raise ValueError('invalid observer revision')
                source.git('merge-base', '--is-ancestor', revision, sha)
                refs = [source.ref(revision, runner['path'])]
            runner_targets.update(runner['targets'])
            observers.append({'runner': runner_id, 'sources': refs})
        if set(requirement['targets']) - runner_targets:
            raise ValueError('requirement target has no registered observer')
        covered.update(requirement['targets'])
        requirements.append({'requirement_id': requirement['requirement_id'], 'path': name,
                             'commit': sha, 'sha256': digest(raw), 'targets': sorted(set(requirement['targets'])),
                             'observers': observers})
    if set(classification['required_targets']) - covered:
        raise ValueError('changed runtime target missing from landed requirements')
    lanes = sorted(set(classification['lanes']) | covered)
    plan = {'schema': 'carr-delivery-plan.v1', 'repository': inventory['repository'],
            'environment': inventory['environment'],
            'source': {'commit': sha, 'first_parent': parent,
                       'tree': source.git('rev-parse', sha + '^{tree}').decode().strip()},
            'contracts': [source.ref(sha, p) for p in [SCHEMA, INVENTORY, POLICY]],
            'changed_paths': paths, 'lanes': lanes,
            'targets': sorted(covered | set(classification['required_targets'])),
            'required_proofs': sorted({proof for lane in lanes
                                       for proof in inventory['lanes'][lane]['required_proofs']}),
            'requirements': requirements, 'execution_attested': False}
    if len(canonical(plan)) > 1048576 - 128:
        raise ValueError('plan projection exceeds 1 MiB')
    plan['plan_digest'] = digest(canonical(plan))
    return plan
