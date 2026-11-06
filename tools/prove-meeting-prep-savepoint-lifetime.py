#!/usr/bin/env python3
"""Read calendar preparation and fault-inject the exact deployed source, separately."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.release_reader import read

LIMIT = 1048576
MODULE = 'mcp-server/src/calendar-meeting-prep.js'


class Unavailable(Exception):
    pass


class Contradiction(Exception):
    pass


SYNTHETIC = r'''
import vm from 'node:vm';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const context=vm.createContext({});
const source=fs.readFileSync(process.argv[1],'utf8');
const module=new vm.SourceTextModule(source,{context});
const imports={
 './identity.js':['personalScopeForActor','organizationTenantForActor'],
 './brief-calendar.js':['CALENDAR_AGENDA_BOARD','calendarSection','addCalendarDealSuggestions']
};
await module.link(async name=>{
 assert.ok(Object.hasOwn(imports,name),'unexpected source dependency');
 return new vm.SyntheticModule(imports[name],function(){
  for(const key of imports[name]) this.setExport(key,()=>{throw Error('unused port invoked');});
 },{context});
});
await module.evaluate({timeout:1000});
let active=false;
const sequence=[];
const c={inTransaction:true,query:async sql=>{
 if(sql==='savepoint calendar_meeting_prep'){assert.equal(active,false);active=true;sequence.push('savepoint');}
 else if(sql==='rollback to savepoint calendar_meeting_prep'){assert.equal(active,true);sequence.push('rollback');}
 else if(sql==='release savepoint calendar_meeting_prep'){assert.equal(active,true);active=false;sequence.push('release');}
 else if(sql==='select 1'){assert.equal(active,false);sequence.push('subsequent');return {rows:[{ok:1}]};}
 else {assert.match(sql,/from v_open_deal_room_board/);sequence.push('select');return {rows:null};}
 return {rows:[]};
}};
const section={state:'ready',sources:{},notes:[],items:[{title:'Synthetic meeting',possible_deals:[{id:'one',name:'Possible lease'}]}]};
await module.namespace.addCalendarMeetingPrep(c,section,'joe');
assert.equal(section.state,'partial');
assert.equal(section.sources.meeting_prep.state,'unavailable');
assert.equal(section.items[0].title,'Synthetic meeting');
assert.equal(section.items[0].possible_deals[0].name,'Possible lease');
assert.equal(section.items[0].possible_deals[0].prep.state,'unavailable');
assert.equal(section.notes.length,1);
assert.equal(section.notes[0],'Meeting deal preparation could not be checked.');
await c.query('select 1');
assert.equal(JSON.stringify(sequence),JSON.stringify(['savepoint','select','rollback','release','subsequent']));
console.log(JSON.stringify({projection_failure_partial:true,meeting_retained:true,subsequent_query:true,sequence}));
'''


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise Unavailable('total deadline exceeded')
    return seconds


def run(command, deadline):
    def bound_output():
        resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))

    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(command, stdout=output, stderr=errors, timeout=remaining(deadline),
                                preexec_fn=bound_output)
        if output.tell() >= LIMIT or errors.tell() >= LIMIT:
            raise Unavailable('process response exceeds limit')
        output.seek(0)
        return result.returncode, output.read()


def get(url, deadline):
    response = read(url, timeout=remaining(deadline), max_bytes=LIMIT)
    if response.status != 200:
        raise Unavailable('HTTP response unavailable')
    if len(response.body) > LIMIT:
        raise Unavailable('HTTP response exceeds limit')
    return response.body


def natural_shape(value):
    if not isinstance(value, dict) or value.get('state') == 'unavailable':
        raise Unavailable('natural calendar evidence unavailable')
    if value.get('state') not in {'ready', 'partial', 'empty'} or not isinstance(value.get('items'), list):
        raise Contradiction('invalid natural calendar shape')
    if len(value['items']) > 100 or not isinstance(value.get('sources'), dict) or not isinstance(value.get('notes'), list):
        raise Contradiction('invalid natural calendar bounds or metadata')
    if not isinstance(value['sources'].get('outlook_calendar'), dict):
        raise Contradiction('natural calendar source metadata missing')
    for item in value['items']:
        if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not isinstance(item.get('possible_deals'), list):
            raise Contradiction('invalid natural meeting shape')
        if len(item['possible_deals']) > 1000:
            raise Contradiction('natural possible deals exceed bounds')
        for deal in item['possible_deals']:
            if (not isinstance(deal, dict) or not isinstance(deal.get('name'), str)
                    or not isinstance(deal.get('id'), str) or not isinstance(deal.get('prep'), dict)
                    or deal['prep'].get('state') not in {'ready', 'unavailable'}):
                raise Contradiction('invalid natural preparation shape')
    return {'structurally_valid': True, 'state': value['state'], 'meeting_count': len(value['items'])}


def observe(args, evidence):
    origin = args.origin.rstrip('/')
    parsed = urlsplit(origin)
    fixture = parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
    if origin != 'https://api.doctorcre.com' and not (fixture and parsed.path == '' and not parsed.query and not parsed.fragment and not parsed.username):
        raise Unavailable('origin is not authorized')
    deadline = time.monotonic() + args.timeout_seconds
    release = json.loads(get(origin + '/release', deadline))
    sha = release.get('git_sha', {}).get('value')
    if release.get('env', {}).get('value') != 'production':
        raise Unavailable('production release identity unavailable')
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
        raise Unavailable('full release SHA unavailable')
    evidence['release_sha'] = sha
    code, raw = run([str(Path(args.call_wrapper).resolve()), 'call', 'calendar-meeting-prep', '{}'], deadline)
    if code:
        raise Unavailable('calendar wrapper refused or failed')
    natural = natural_shape(json.loads(raw))
    evidence['natural_read'] = natural
    base = origin if fixture else 'https://raw.githubusercontent.com'
    source_url = base + '/DoctorCRE/carr-system/' + sha + '/' + MODULE
    source = get(source_url, deadline)
    evidence.update(source_url=source_url, source_sha256=hashlib.sha256(source).hexdigest())
    with tempfile.TemporaryDirectory(prefix='meeting-prep-proof-') as directory:
        path = Path(directory) / 'calendar-meeting-prep.js'
        path.write_bytes(source)
        code, raw = run(['node', '--experimental-vm-modules', '--input-type=module', '-e', SYNTHETIC, str(path)], deadline)
    if code:
        raise Contradiction('release-bound synthetic projection assertion failed')
    synthetic = json.loads(raw)
    return {'release_sha': sha, 'source_url': source_url, 'source_sha256': hashlib.sha256(source).hexdigest(),
            'natural_read': natural, 'release_bound_synthetic': synthetic,
            'requests': {'wrapper': 1, 'release_get': 1, 'source_get': 1},
            'live_proven': not fixture, 'fixture': fixture}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--call-wrapper', required=True)
    parser.add_argument('--timeout-seconds', type=float, default=30)
    args = parser.parse_args()
    if not 0 < args.timeout_seconds <= 30:
        parser.error('timeout must be greater than zero and at most 30 seconds')

    def expired(signum, frame):
        raise Unavailable('total deadline exceeded')

    signal.signal(signal.SIGALRM, expired)
    evidence = {}
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, args.timeout_seconds)
            result = observe(args, evidence)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        print(json.dumps(result))
        return 0
    except Contradiction as error:
        print(json.dumps({**evidence, 'status': 'contradiction', 'reason': str(error), 'live_proven': False}))
        return 1
    except (Unavailable, OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        print(json.dumps({**evidence, 'status': 'unavailable', 'live_proven': False}))
        return 78


if __name__ == '__main__':
    sys.exit(main())
