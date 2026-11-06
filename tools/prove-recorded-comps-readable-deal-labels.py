#!/usr/bin/env python3
"""Observe natural recorded comps and the release-pinned deterministic producer."""
import argparse
import base64
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.release_reader import read as read_http
LIMIT = 1048576
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.I)


class Unavailable(Exception):
    pass


class ProofFailed(Exception):
    pass


def require(predicate, condition):
    if not condition:
        raise ProofFailed(predicate)


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def check_table(table):
    columns = {c['key']: c for c in table['columns']}
    require('natural.deal-column', columns['deal']['label'] == 'Deal')
    require('natural.proposed-on-column', columns['proposed_on'] == {'key': 'proposed_on', 'label': 'Proposed on', 'type': 'date'})
    require('natural.rent-column', columns['rent']['label'] == 'Proposed rent ($/SF/year)')
    require('natural.row-budget', 0 < len(table['rows']) <= 200)
    for row in table['rows']:
        name = row['deal']['value']
        require('natural.deal-readable', isinstance(name, str) and bool(name.strip()) and not UUID.fullmatch(name))
        require('natural.deal-source', isinstance(row['deal']['source']['record_id'], str) and UUID.fullmatch(row['deal']['source']['record_id']))
        require('natural.rent-source', isinstance(row['rent']['source']['record_id'], str) and UUID.fullmatch(row['rent']['source']['record_id']))
        date = row['proposed_on']['value']
        require('natural.proposed-on-value', date is None or isinstance(date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date))


SYNTHETIC = r'''
import {isDeepStrictEqual} from 'node:util';
function require(predicate, condition) {
 if (!condition) throw new Error(predicate);
}
try {
const {recordedCompsTable,validateAnswerBlocks}=await import('./answer-blocks.mjs');
class ToolError extends Error {}
const id='11111111-1111-4111-8111-111111111111';
const negotiation='22222222-2222-4222-8222-222222222222';
let calls=0;
const c={query:async(sql,args)=>{
 calls++;
 require('synthetic.query-arguments',isDeepStrictEqual(args,[[id]]));
 require('synthetic.deal-join',typeof sql==='string' && /join public\.deal/.test(sql));
 require('synthetic.query-order-and-budget',typeof sql==='string' && /order by n.proposed_on desc, n.id limit 200/.test(sql));
 return {rows:[{missing:false,comps:[{id:negotiation,deal_id:id,deal_name:'Synthetic Harbor',proposed_on:'2026-11-01',side:'landlord',rate_norm_sf_yr:'28.5'}]}]};
}};
const table=await recordedCompsTable(c,[id],ToolError);
require('synthetic.query-count',calls===1);
require('synthetic.deal-name',table.rows[0].deal.value==='Synthetic Harbor');
require('synthetic.deal-source',table.rows[0].deal.source.record_id===id);
require('synthetic.proposed-on',table.rows[0].proposed_on.value==='2026-11-01');
require('synthetic.rent-source',table.rows[0].rent.source.record_id===negotiation);
require('synthetic.rent-value',table.rows[0].rent.value===28.5);
try { validateAnswerBlocks([table],ToolError); } catch { throw new Error('synthetic.schema'); }
const blocks=JSON.parse(process.argv[2]);
try { validateAnswerBlocks(blocks,ToolError); } catch { throw new Error('natural.schema'); }
console.log(JSON.stringify({passed:true}));
} catch (error) {
 if (error.code==='ERR_MODULE_NOT_FOUND') {
  console.log(JSON.stringify({predicate:'synthetic.runtime-dependencies',unavailable:true}));
  process.exit(78);
 }
 const predicate=/^(synthetic|natural)\.[a-z-]+$/.test(error.message) ? error.message : 'synthetic.execution';
 console.log(JSON.stringify({predicate}));
 process.exitCode=1;
}
'''


def execute(args, receipt):
    deadline = time.monotonic() + args.timeout_seconds
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise Unavailable('deadline exceeded')
        return left

    def run(command, contradiction=False):
        with tempfile.TemporaryFile() as output:
            process = None
            try:
                # Deliver the deadline only after ownership of a late launch is recorded.
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
                try:
                    process = subprocess.Popen(command, stdout=output, stderr=subprocess.DEVNULL, start_new_session=True,
                                               preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT + 1, LIMIT + 1)))
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                code = process.wait(timeout=remaining())
            finally:
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            output.seek(0)
            data = output.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise Unavailable('response exceeds budget')
        if code:
            if contradiction:
                try:
                    result = json.loads(data)
                    predicate = result['predicate']
                except (ValueError, KeyError, TypeError):
                    raise ProofFailed('synthetic.execution') from None
                if not isinstance(predicate, str) or not re.fullmatch(r'(synthetic|natural)\.[a-z-]+', predicate):
                    raise ProofFailed('synthetic.execution')
                if code == 78 and result.get('unavailable') is True:
                    receipt['predicate'] = predicate
                    raise Unavailable('producer runtime dependency unavailable')
                raise ProofFailed(predicate)
            raise Unavailable(f'command exited {code}')
        return data

    def get(url):
        response = read_http(url, timeout=remaining(), max_bytes=LIMIT)
        if response.status != 200:
            raise Unavailable(f'HTTP status {response.status}')
        data = response.body
        if len(data) > LIMIT:
            raise Unavailable('HTTP response exceeds budget')
        return data

    calls = 0
    def call(verb, value):
        nonlocal calls
        calls += 1
        receipt['wrapper_calls'] = calls
        require('wrapper.request-budget', calls <= 8)
        data = json.loads(run([args.call_wrapper, 'call', verb, json.dumps(value)]))
        if 'content' in data:
            data = json.loads(data['content'][0]['text'])
        if data.get('error') or data.get('ok') is False:
            raise Unavailable('read authorization unavailable')
        return data

    receipt['predicate'] = 'release.identity'
    release = json.loads(get(args.origin.rstrip('/') + '/release'))
    sha = release.get('git_sha', {}).get('value')
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise Unavailable('release SHA unavailable')
    if release.get('env', {}).get('value') != 'production':
        raise Unavailable('production identity unavailable')
    timestamp = release.get('worker_version', {}).get('timestamp')
    if not timestamp:
        raise Unavailable('release timestamp unavailable')
    try:
        deployed = stamp(timestamp)
    except (ValueError, TypeError):
        raise Unavailable('release timestamp invalid') from None
    receipt.update({'release_sha': sha, 'natural_read': 'unavailable', 'synthetic': 'unavailable', 'sources': {}})
    receipt['predicate'] = 'release.sources'
    sources = {}
    for path in ('src/answer-blocks.js', 'src/answer-blocks.v1.schema.json', 'package-lock.json'):
        data = get(f'https://raw.githubusercontent.com/DoctorCRE/carr-system/{sha}/mcp-server/{path}')
        sources[path] = data
        receipt['sources'][path] = hashlib.sha256(data).hexdigest()
    receipt['predicate'] = 'natural.conversation-response'
    blocks = []
    conversations = call('list-doc-conversations', {'limit': 10})['conversations']
    for conversation in conversations[:3]:
        turns = call('read-doc-conversation', {'conversation_id': conversation['id'], 'limit': 200})['turns']
        for turn in turns[:200]:
            if stamp(turn['at']) < deployed:
                continue
            for block in turn.get('answer_blocks', [])[:8]:
                if block.get('type') == 'table.v1' and block.get('id') == 'comps' and block.get('rows'):
                    receipt['predicate'] = 'natural.table-shape'
                    check_table(block)
                    blocks = [block]
                    break
            if blocks:
                break
        if blocks:
            break
    if blocks:
        row = blocks[0]['rows'][0]
        receipt['predicate'] = 'natural.canonical-response'
        room = call('get-deal-room', {'deal': row['deal']['source']['record_id']})
        require('natural.canonical-deal-name', room['name'] == row['deal']['value'])
        receipt['natural_read'] = 'passed'
    receipt['predicate'] = 'synthetic.execution'
    with tempfile.TemporaryDirectory(prefix='comps-proof-') as temp:
        folder = Path(temp)
        receipt['predicate'] = 'synthetic.runtime-dependencies'
        dependency = json.loads(sources['package-lock.json'])['packages']['node_modules/@cfworker/json-schema']
        if dependency.get('dependencies'):
            raise Unavailable('validator transitive dependencies require audit')
        url = dependency.get('resolved', '')
        if not re.fullmatch(r'https://registry\.npmjs\.org/@cfworker/json-schema/-/json-schema-[0-9.]+\.tgz', url):
            raise Unavailable('validator archive URL outside proof contract')
        archive = get(url)
        integrity = 'sha512-' + base64.b64encode(hashlib.sha512(archive).digest()).decode()
        if integrity != dependency.get('integrity'):
            raise Unavailable('validator archive integrity differs from release')
        package = folder / 'node_modules/@cfworker/json-schema'
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
                total = 0
                for member in bundle:
                    remaining()
                    path = Path(member.name)
                    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'package':
                        raise Unavailable('validator archive path outside package')
                    target = package.joinpath(*path.parts[1:])
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        total += member.size
                        if total > LIMIT:
                            raise Unavailable('validator archive exceeds expanded budget')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.extractfile(member) as source:
                            target.write_bytes(source.read())
                    else:
                        raise Unavailable('validator archive contains unsupported entry')
        except tarfile.TarError:
            raise Unavailable('validator archive invalid') from None
        receipt['predicate'] = 'synthetic.execution'
        (folder / 'answer-blocks.mjs').write_bytes(sources['src/answer-blocks.js'])
        (folder / 'answer-blocks.v1.schema.json').write_bytes(sources['src/answer-blocks.v1.schema.json'])
        (folder / 'check.mjs').write_text(SYNTHETIC)
        result = run(['node', str(folder / 'check.mjs'), json.dumps(blocks)], contradiction=True)
        require('synthetic.completion', json.loads(result).get('passed') is True)
    receipt['synthetic'] = 'passed'
    receipt['predicate'] = 'natural-comps-and-release-producer' if blocks else 'natural.post-deployment-table'
    if not blocks:
        receipt['reason'] = 'no qualifying post-deployment table'
    print(json.dumps(receipt, sort_keys=True))
    return 0 if blocks else 78


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--call-wrapper', required=True)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    origin = urlparse(args.origin)
    if not 0 < args.timeout_seconds <= 30 or origin.scheme not in ('http', 'https') or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('', '/'):
        parser.error('origin or deadline outside proof contract')
    if origin.scheme == 'http' and origin.hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('HTTP is allowed only for loopback fixtures')
    def timeout(_signum, _frame):
        raise Unavailable('total deadline exceeded')
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, args.timeout_seconds)
    receipt = {'natural_read': 'unavailable', 'synthetic': 'unavailable', 'predicate': 'release.identity'}
    try:
        return execute(args, receipt)
    except ProofFailed as error:
        receipt['predicate'] = str(error)
        receipt['contradiction'] = 'predicate failed'
        if str(error).startswith('natural.'):
            receipt['natural_read'] = 'failed'
        elif str(error).startswith('synthetic.'):
            receipt['synthetic'] = 'failed'
        print(json.dumps(receipt, sort_keys=True))
        return 1
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        receipt['contradiction'] = type(error).__name__
        print(json.dumps(receipt, sort_keys=True))
        return 1
    except (Unavailable, OSError, subprocess.TimeoutExpired) as error:
        receipt['reason'] = str(error) if isinstance(error, Unavailable) else type(error).__name__
        print(json.dumps(receipt, sort_keys=True))
        return 78
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    sys.exit(main())
