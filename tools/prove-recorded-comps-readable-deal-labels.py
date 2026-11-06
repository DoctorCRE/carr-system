#!/usr/bin/env python3
"""Observe natural recorded comps and the release-pinned deterministic producer."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
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


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def check_table(table):
    columns = {c['key']: c for c in table['columns']}
    assert columns['deal']['label'] == 'Deal'
    assert columns['proposed_on'] == {'key': 'proposed_on', 'label': 'Proposed on', 'type': 'date'}
    assert columns['rent']['label'] == 'Proposed rent ($/SF/year)'
    assert 0 < len(table['rows']) <= 200
    for row in table['rows']:
        assert isinstance(row['deal']['value'], str) and row['deal']['value'].strip()
        assert not UUID.fullmatch(row['deal']['value'])
        assert UUID.fullmatch(row['deal']['source']['record_id'])
        assert UUID.fullmatch(row['rent']['source']['record_id'])
        assert row['proposed_on']['value'] is None or re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['proposed_on']['value'])


SYNTHETIC = r'''
import assert from 'node:assert/strict';
import {recordedCompsTable,validateAnswerBlocks} from './answer-blocks.mjs';
class ToolError extends Error {}
const id='11111111-1111-4111-8111-111111111111';
const negotiation='22222222-2222-4222-8222-222222222222';
let calls=0;
const c={query:async(sql,args)=>{
 calls++;
 assert.deepEqual(args,[[id]]);
 assert.match(sql,/join public\.deal/);
 assert.match(sql,/order by n.proposed_on desc, n.id limit 200/);
 return {rows:[{missing:false,comps:[{id:negotiation,deal_id:id,deal_name:'Synthetic Harbor',proposed_on:'2026-11-01',side:'landlord',rate_norm_sf_yr:'28.5'}]}]};
}};
const table=await recordedCompsTable(c,[id],ToolError);
assert.equal(calls,1);
assert.equal(table.rows[0].deal.value,'Synthetic Harbor');
assert.equal(table.rows[0].deal.source.record_id,id);
assert.equal(table.rows[0].proposed_on.value,'2026-11-01');
assert.equal(table.rows[0].rent.source.record_id,negotiation);
assert.equal(table.rows[0].rent.value,28.5);
validateAnswerBlocks([table],ToolError);
const blocks=JSON.parse(process.argv[2]);
validateAnswerBlocks(blocks,ToolError);
console.log('synthetic and natural schema passed');
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
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.DEVNULL, start_new_session=True,
                                       preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT + 1, LIMIT + 1)))
            try:
                code = process.wait(timeout=remaining())
            finally:
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
                raise AssertionError('producer contradiction')
            raise Unavailable('command unavailable')
        return data

    def get(url):
        response = read_http(url, timeout=remaining(), max_bytes=LIMIT)
        if response.status != 200:
            raise Unavailable('HTTP status unavailable')
        data = response.body
        if len(data) > LIMIT:
            raise Unavailable('HTTP response exceeds budget')
        return data

    calls = 0
    def call(verb, value):
        nonlocal calls
        calls += 1
        assert calls <= 8
        data = json.loads(run([args.call_wrapper, 'call', verb, json.dumps(value)]))
        if 'content' in data:
            data = json.loads(data['content'][0]['text'])
        if data.get('error') or data.get('ok') is False:
            raise Unavailable('read authorization unavailable')
        return data

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
    sources = {}
    for path in ('src/answer-blocks.js', 'src/answer-blocks.v1.schema.json', 'package-lock.json'):
        data = get(f'https://raw.githubusercontent.com/DoctorCRE/carr-system/{sha}/mcp-server/{path}')
        sources[path] = data
        receipt['sources'][path] = hashlib.sha256(data).hexdigest()
    local_lock = json.loads((ROOT / 'mcp-server/package-lock.json').read_text())
    released_lock = json.loads(sources['package-lock.json'])
    key = 'node_modules/@cfworker/json-schema'
    if local_lock['packages'][key] != released_lock['packages'][key]:
        raise Unavailable('validator dependency differs from release')
    blocks = []
    conversations = call('list-doc-conversations', {'limit': 10})['conversations']
    for conversation in conversations[:3]:
        turns = call('read-doc-conversation', {'conversation_id': conversation['id'], 'limit': 200})['turns']
        for turn in turns[:200]:
            if stamp(turn['at']) < deployed:
                continue
            for block in turn.get('answer_blocks', [])[:8]:
                if block.get('type') == 'table.v1' and block.get('id') == 'comps' and block.get('rows'):
                    check_table(block)
                    blocks = [block]
                    break
            if blocks:
                break
        if blocks:
            break
    if blocks:
        row = blocks[0]['rows'][0]
        room = call('get-deal-room', {'deal': row['deal']['source']['record_id']})
        assert room['name'] == row['deal']['value'], 'canonical deal name differs'
        receipt['natural_read'] = 'passed'
    with tempfile.TemporaryDirectory(prefix='.comps-proof-', dir=ROOT / 'mcp-server') as temp:
        folder = Path(temp)
        (folder / 'answer-blocks.mjs').write_bytes(sources['src/answer-blocks.js'])
        (folder / 'answer-blocks.v1.schema.json').write_bytes(sources['src/answer-blocks.v1.schema.json'])
        (folder / 'check.mjs').write_text(SYNTHETIC)
        result = run(['node', str(folder / 'check.mjs'), json.dumps(blocks)], contradiction=True)
        assert b'synthetic and natural schema passed' in result
    receipt['synthetic'] = 'passed'
    receipt['wrapper_calls'] = calls
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
    receipt = {'natural_read': 'unavailable', 'synthetic': 'unavailable'}
    try:
        return execute(args, receipt)
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        receipt['contradiction'] = type(error).__name__
        print(json.dumps(receipt, sort_keys=True))
        return 1
    except (Unavailable, OSError, subprocess.TimeoutExpired) as error:
        receipt['reason'] = type(error).__name__
        print(json.dumps(receipt, sort_keys=True))
        return 78
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    sys.exit(main())
