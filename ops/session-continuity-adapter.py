"""Pinned development bridge. No native hook installation or local authority store."""
import argparse
import hashlib
import importlib.util
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import uuid

MANIFEST = Path(__file__).parent / 'config/session-continuity-adapter.v1.json'
MAX_BYTES = 70000


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def verified_artifact(root, manifest, destination):
    root = Path(root).resolve(strict=True)
    required = {'contracts/session-continuity.v1.json', 'tools/session-continuity.py',
                'lib/session_continuity.py', 'lib/session_adapters/claude.py',
                'lib/session_adapters/codex.py', 'lib/session_adapters/grok.py'}
    if set(manifest['artifacts']) != required or manifest['authority_contract'] != 'session-checkpoint.v1':
        raise ValueError('artifact_contract_mismatch')
    revision = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], check=True,
                              capture_output=True, timeout=3).stdout.decode().strip()
    if revision != manifest['revision'] or manifest['contract'] != 'session-continuity.v1':
        raise ValueError('artifact_revision_mismatch')
    for name, expected in manifest['artifacts'].items():
        path = root / name
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('unsafe_artifact')
        if path.stat().st_size > 1048576:
            raise ValueError('artifact_too_large')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('artifact_hash_mismatch')
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class AuthorityStore:
    def __init__(self, policy, call):
        self.policy, self.call = policy, call

    def read(self, binding):
        result = self.call('read-session-checkpoint-v1', {'binding': binding})
        if result.get('schema') != 'session-checkpoint.v1' or result.get('ok') is not True:
            raise ValueError('invalid_authority_readback')
        record = result['record']
        if record is not None and record['binding'] != binding:
            raise ValueError('authority_binding_mismatch')
        return record

    def save(self, envelope, event, decision, fault=None):
        if fault is not None:
            raise ValueError('unsupported_fault')
        binding = envelope['binding']
        previous = self.read(binding)
        if previous:
            old = previous['envelope']
            if previous['archived']:
                return {'status': 'degraded', 'reason': 'checkpoint_archived'}
            if old and (envelope['epoch'] < old['epoch'] or envelope['cursor'] < old['cursor']):
                return {'status': 'degraded', 'reason': 'old_replay'}
            if old and envelope['cursor'] == old['cursor']:
                def event_fields(value):
                    return {k: v for k, v in value.items() if k not in ('now', 'expected_generation')}
                if self.policy.digest(event_fields(envelope)) != self.policy.digest(event_fields(old)):
                    return {'status': 'degraded', 'reason': 'changed_replay'}
                if decision['status'] != 'persist':
                    return decision
            if old and envelope['epoch'] == old['epoch'] and envelope['cursor'] > old['cursor']:
                old_event = self.policy.normalize(old)
                if envelope['model_fingerprint'] == old['model_fingerprint'] and event.window == old_event.window and event.telemetry == 'fresh' and old_event.used is not None and event.used is not None and event.used < old_event.used:
                    return {'status': 'unknown', 'reason': 'counter_reset_without_new_epoch'}
        if decision['status'] != 'persist':
            if envelope['expected_generation'] != (previous['generation'] if previous else 0):
                return {'status': 'degraded', 'reason': 'cas_conflict', 'generation': previous['generation'] if previous else 0}
            return decision
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, self.policy.digest(binding) + ':' + str(envelope['epoch']) + ':' + str(envelope['cursor'])))
        args = {'binding': binding, 'envelope': envelope, 'idempotency_key': key}
        ambiguous = False
        try:
            result = self.call('write-session-checkpoint-v1', args)
            if result.get('ok') is not True or result.get('schema') != 'session-checkpoint.v1':
                raise ValueError('invalid_authority_write')
            saved = result['record']
        except (TimeoutError, OSError):
            ambiguous = True
            saved = None
        readback = self.read(binding)
        if not readback or readback['archived'] or readback['last_write_key'] != key or readback['envelope'] != envelope or readback['generation'] != envelope['expected_generation'] + 1 or (not ambiguous and saved != readback):
            raise ValueError('authority_readback_mismatch')
        return self.saved(readback, decision)

    def saved(self, record, decision):
        envelope = record['envelope']
        return {'status': 'saved', 'decision': decision, 'checkpoint': envelope['checkpoint'],
                'receipt': {'schema': 'session-checkpoint.v1', 'sponsor': record['sponsor'],
                            'authority_session': record['authority_session'],
                            'checkpoint_digest': self.policy.digest(envelope['checkpoint']),
                            'binding_digest': self.policy.digest(record['binding']),
                            'source_event_ref': self.policy.digest({k: v for k, v in envelope.items() if k not in ('now', 'expected_generation')}),
                            'generation': record['generation'], 'durable_readback': True,
                            'native_delivery': 'pending', 'archived': record['archived']}}

    def recover(self, binding):
        record = self.read(binding)
        if not record or record['envelope'] is None:
            return {'status': 'unknown', 'reason': 'no_checkpoint'}
        result = self.saved(record, {'status': 'restore'})
        result['recovery_output'] = self.policy.ADAPTERS[binding['provider']].render(result['checkpoint'], result['receipt'])
        result['recovery_support'] = 'artifact_only' if result['recovery_output'] is not None else 'unsupported'
        return result


def process(envelope, artifact_root, call, manifest=None, recovery=False):
    manifest = manifest or json.loads(MANIFEST.read_text())
    if len(encoded(envelope)) > 65536:
        raise ValueError('input_too_large')
    with tempfile.TemporaryDirectory(prefix='carr-continuity-artifact-') as directory:
        snapshot = Path(directory)
        verified_artifact(artifact_root, manifest, snapshot)
        # All imported local modules are loaded from verified snapshot bytes.
        names = [n for n in sys.modules if n == 'lib' or n.startswith('lib.')]
        old = {n: sys.modules.pop(n) for n in names}
        sys.path.insert(0, str(snapshot))
        try:
            for name, relative in [('lib', 'lib'), ('lib.session_adapters', 'lib/session_adapters')]:
                package = types.ModuleType(name)
                package.__path__ = [str(snapshot / relative)]
                sys.modules[name] = package
            spec = importlib.util.spec_from_file_location('_verified_continuity', snapshot / 'lib/session_continuity.py')
            policy = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = policy
            spec.loader.exec_module(policy)
            store = AuthorityStore(policy, call)
            if recovery:
                policy.binding_key(envelope['binding'])
                return store.recover(envelope['binding'])
            return policy.process(envelope, store)
        finally:
            sys.path.remove(str(snapshot))
            for name in list(sys.modules):
                if name == 'lib' or name.startswith('lib.') or name == '_verified_continuity':
                    sys.modules.pop(name, None)
            sys.modules.update(old)


class McpTransport:
    """Supported stateless tools/call, bounded requests and no retries."""
    def __init__(self, endpoint, token):
        if endpoint != 'https://api.doctorcre.com/mcp' or not token:
            raise ValueError('authenticated_endpoint_required')
        self.endpoint, self.token = endpoint, token
        self.requests = 0

    def __call__(self, verb, args):
        self.requests += 1
        if self.requests > 3 or len(encoded(args)) > MAX_BYTES:
            raise ValueError('request_budget_exceeded')
        body = encoded({'jsonrpc':'2.0','id':self.requests,'method':'tools/call',
                        'params':{'name':verb,'arguments':args}})
        request = urllib.request.Request(self.endpoint, data=body, headers={
            'Authorization':'Bearer '+self.token, 'Content-Type':'application/json',
            'Accept':'application/json', 'MCP-Protocol-Version':'2025-03-26'})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        started = time.monotonic()
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
                data = bytearray()
                while len(data) <= MAX_BYTES:
                    if time.monotonic() - started > 8:
                        raise TimeoutError('authority_deadline')
                    chunk = response.read1(min(8192, MAX_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code >= 500 and verb == 'write-session-checkpoint-v1':
                raise OSError('authority_write_ambiguous') from error
            raise ValueError('authority_http_refusal') from error
        if len(data) > MAX_BYTES:
            raise ValueError('authority_response_too_large')
        message = json.loads(data)
        result = message.get('result')
        if not result or result.get('isError') or message.get('error'):
            raise ValueError('authority_contract_refusal')
        content = result.get('structuredContent')
        if content is None:
            content = json.loads(next(item['text'] for item in result['content'] if item['type']=='text'))
        return content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact-root', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--recover', action='store_true')
    args = parser.parse_args()
    data = sys.stdin.buffer.read(65537)
    if len(data) > 65536:
        raise ValueError('input_too_large')
    print(json.dumps(process(json.loads(data), args.artifact_root,
                             McpTransport(args.endpoint, os.environ.get('CARR_SESSION_CHECKPOINT_TOKEN')), recovery=args.recover)))


if __name__ == '__main__':
    main()
