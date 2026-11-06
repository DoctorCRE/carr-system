// Test recipient suppression and contact policy decisions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS } from '../src/tools.js';

test('suppression read returns only exact normalized recipient decisions', async () => {
  const tool = TOOLS['mail-contact-policy'];
  assert.equal(tool.write, false);
  const db = {query: async (sql, values) => {
    return {rows: [{email: 'person@example.test'}]};
  }};
  assert.deepEqual(await tool.handler(db, {}, {emails: [' Person@Example.Test ']}),
    {recipients: [{email: 'person@example.test', blocked: true}]});
});

test('invalid address sets refuse before reading records', async () => {
  for (const emails of [[], ['bad'], ['a@example.test,b@example.test'], Array(51).fill('a@example.test'), ['Name <a@example.test>'], ['a@b.éé'], [null], ['a@example.test;b@example.test']]) {
    await assert.rejects(TOOLS['mail-contact-policy'].handler({query: () => assert.fail('query')}, {}, {emails}),
      error => error.payload?.error === 'invalid_mail_recipients' || error.error === 'invalid_mail_recipients'
        || String(error).includes('invalid_mail_recipients'));
  }
});

test('existing party and lead instructions block exact email matches in PostgreSQL', async () => {
  const { execFileSync } = await import('node:child_process');
  const { existsSync } = await import('node:fs');
  const { createServer } = await import('node:net');
  const path = await import('node:path');
  const { default: pg } = await import('pg');
  const { acquireDisposablePostgres, acquirePostgresFixtureGroup } = await import('./helpers/disposable-postgres.mjs');
  let bin;
  for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
    try {
      const candidate = execFileSync(config, ['--bindir'], {encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']}).trim();
      if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
    } catch { /* Try the next supported PostgreSQL installation. */ }
  }
  assert.ok(bin, 'disposable PostgreSQL required for suppression proof');
  const release = await acquirePostgresFixtureGroup();
  let fixture, db;
  try {
    const socket = createServer();
    await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
    const port = socket.address().port;
    await new Promise(resolve => socket.close(resolve));
    fixture = await acquireDisposablePostgres({prefix: 'mail-policy-', pgCtl: path.join(bin, 'pg_ctl')});
    const data = path.join(fixture.root, 'data');
    await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
      '-o', `-k ${fixture.root} -h 127.0.0.1 -p ${port}`, '-w', 'start']);
    db = new pg.Client({connectionString: `postgresql://fixture@127.0.0.1:${port}/postgres`});
    await db.connect();
    await db.query(`create table party(id integer primary key, email text, contact_state text, merged_into integer, deleted_at timestamptz);
      create table lead(party_id integer, suppressed boolean, stage text);
      insert into party values
        (1, ' Blocked@Example.Test ', 'do_not_contact', 2, now()),
        (2, 'blocked@example.test', 'active', null, null),
        (3, 'suppressed@example.test', 'active', null, null),
        (4, 'stage@example.test', 'active', null, null),
        (5, 'allowed@example.test', 'active', null, null),
        (6, 'deleted@example.test; retired@example.test', 'do_not_contact', null, now()),
        (7, 'merged@example.test; old@example.test', 'do_not_contact', 2, null),
        (8, null, 'do_not_contact', null, null),
        (9, 'First10@Example.Test; Élodie10@médical.test', 'active', null, null);
      insert into lead values(3, true, 'new'), (4, false, 'do_not_contact');`);
    for (const [index, separator] of ['; ', ', ', ' ', '> Other <'].entries()) {
      for (const [mode, contactState, suppressed, stage] of [
        ['party', 'do_not_contact', false, 'new'],
        ['suppressed', 'active', true, 'new'],
        ['stage', 'active', false, 'do_not_contact'],
      ]) {
        const id = 10 + index * 3 + ['party', 'suppressed', 'stage'].indexOf(mode);
        await db.query('insert into party values($1,$2,$3,2,now())',
          [id, `Name <First${id}@Example.Test${separator}Élodie${id}@médical.test>`, contactState]);
        await db.query('insert into lead values($1,$2,$3)', [id, suppressed, stage]);
      }
    }
    const punctuationCases = [];
    for (const punctuation of [...`!"#$%&'()*+./:=?[\\]^_\`{|}~-`, '😀']) {
      for (const [contactState, suppressed, stage] of [
        ['do_not_contact', false, 'new'],
        ['active', true, 'new'],
        ['active', false, 'do_not_contact'],
      ]) {
        for (const multiple of [false, true]) {
          const id = 100 + punctuationCases.length;
          const address = `a${punctuation}b${id}@example.test`;
          const suffix = `b${id}@example.test`;
          const second = `second${id}@example.test`;
          await db.query('insert into party values($1,$2,$3,null,null)',
            [id, multiple ? `Name <${address}>; ${second}` : address, contactState]);
          await db.query('insert into lead values($1,$2,$3)', [id, suppressed, stage]);
          punctuationCases.push({address, suffix, second, multiple});
        }
      }
    }
    // The deployed reader has these column grants (0500 and 0839), not table ownership.
    await db.query(`create role carr_reader;
      grant select(id,email,contact_state) on party to carr_reader;
      grant select(party_id,suppressed,stage) on lead to carr_reader;
      set role carr_reader;`);
    const read = emails => TOOLS['mail-contact-policy'].handler(db, {}, {emails});
    for (const {address, suffix, second, multiple} of punctuationCases) {
      assert.deepEqual(await read([address, suffix, second]), {recipients: [
        {email: address, blocked: true}, {email: suffix, blocked: false},
        {email: second, blocked: multiple},
      ].sort((a, b) => a.email < b.email ? -1 : a.email > b.email ? 1 : 0)}, `complete punctuation address ${address}`);
    }
    assert.deepEqual(await read(['unknown@example.test', 'allowed@example.test', 'blocked@example.test',
      'suppressed@example.test', 'stage@example.test']), {recipients: [
      {email: 'allowed@example.test', blocked: false},
      {email: 'blocked@example.test', blocked: true},
      {email: 'stage@example.test', blocked: true},
      {email: 'suppressed@example.test', blocked: true},
      {email: 'unknown@example.test', blocked: false},
    ]});
    for (let id = 10; id < 22; id++) {
      const first = `first${id}@example.test`, second = `élodie${id}@médical.test`;
      assert.deepEqual(await read([second.toUpperCase(), first, second, `x${first}`, `first${id}@other.test`]),
        {recipients: [first, `first${id}@other.test`, `x${first}`, second].sort().map(email =>
          ({email, blocked: email === first || email === second}))}, `multi-address restriction ${id}`);
    }
    assert.equal((await read(Array.from({length: 50}, (_, i) => `unknown${i}@example.test`))).recipients.length, 50);
    assert.ok((await read(['deleted@example.test', 'retired@example.test', 'merged@example.test', 'old@example.test'])).recipients.every(row => row.blocked));
    const policy = await read(['first10@example.test', 'élodie10@médical.test']);
    const governor = JSON.parse(execFileSync('python3', ['-c', `
import importlib.util, json, pathlib, sys
root = pathlib.Path.cwd()
sys.path.insert(0, str(root / 'ops'))
spec = importlib.util.spec_from_file_location('send_fixture', root / 'ops/outlook-send-selftest.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
policy = json.load(sys.stdin)
case = fixture.SendTests()
case.setUp()
try:
    for row in policy['recipients']:
        h = fixture.Harness(case.out / str(len(row['email'])))
        h.ports.carr = lambda verb, args: {'recipients': [r for r in policy['recipients'] if r['email'] in args['emails']]}
        result = h.send(to=[row['email']], cc=[], bcc=[])
        assert result.reason == 'do_not_contact', result
        assert h.fake.press_count == 0
        assert h.mailbox.reads == 0
    print(json.dumps({'blocked_addresses': len(policy['recipients']), 'sends': 0}))
finally:
    case.doCleanups()
    case.tearDown()
`], {cwd: path.resolve(import.meta.dirname, '../..'), input: JSON.stringify(policy), encoding: 'utf8'}));
    assert.deepEqual(governor, {blocked_addresses: 2, sends: 0});
    await db.query("reset role");
    await db.query("update party set contact_state='do_not_contact' where id=5");
    await db.query('set role carr_reader');
    assert.deepEqual(await read(['allowed@example.test']),
      {recipients: [{email: 'allowed@example.test', blocked: true}]}, 'each read sees a new instruction');
  } finally {
    try { if (db) await db.end(); }
    finally { try { await fixture?.close(); } finally { await release(); } }
  }
});

test('contact discovery returns complete policy addresses without inventing suffixes', async () => {
  for (const punctuation of [...`!"#$%&'()*+./:=?[\\]^_\`{|}~-`, '😀']) {
    const address = `a${punctuation}b@example.test`;
    const email = `Name <${address.toUpperCase()}>; Second@Example.Test; ${address}`;
    const result = await TOOLS['email-match-contacts'].handler({query: async () => ({rows: [
      {ref: 'P-0001', name: 'Synthetic', email},
    ]})}, {}, {});
    assert.deepEqual(result.contacts.map(c => c.address), [address, 'second@example.test'], address);
    const policy = await TOOLS['mail-contact-policy'].handler({query: async () => ({rows: [{email}]})}, {},
      {emails: [address, 'b@example.test', 'second@example.test']});
    assert.equal(policy.recipients.find(c => c.email === address).blocked, true);
    assert.equal(policy.recipients.find(c => c.email === 'b@example.test').blocked, false);
  }
});

test('proof CLI consumes registered read replies and refuses contradictions and unavailable evidence', async () => {
  const { mkdtemp, readFile, writeFile, chmod, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const path = await import('node:path');
  const { createServer } = await import('node:http');
  const { spawn } = await import('node:child_process');
  const { Validator } = await import('@cfworker/json-schema');
  const root = path.resolve(import.meta.dirname, '../..');
  const declaration = JSON.parse(await readFile(path.join(root, 'proofs/requirements/mail-policy-multiple-recorded-addresses.json'), 'utf8'));
  assert.ok(declaration.assertions[0].predicate.includes('python3 tools/prove-mail-policy-multiple-recorded-addresses.py --origin https://api.doctorcre.com --call-wrapper ./run.sh --timeout-seconds 30'));
  assert.equal(declaration.safety.max_requests, 22);
  const directory = await mkdtemp(path.join(tmpdir(), 'mail-policy-proof-'));
  const wrapper = path.join(directory, 'wrapper');
  const party = {id: 'aaaaaaaa-0000-4000-8000-000000000001', ref: 'P-0001',
    name: 'Synthetic', email: 'a!b@Example.Test; Second@Example.Test', contact_state: 'do_not_contact'};
  const rows = [{ref: party.ref, name: party.name, email: party.email, party_id: party.id,
    party_version: 1, party_name: party.name, phone: null, cell: null, title: null,
    org_id: null, company_id: null, company_version: null, company_name: null}];
  const invoke = async (verb, args, queryRows) => {
    assert.equal(new Validator(structuredClone(TOOLS[verb].inputSchema)).validate(args).valid, true);
    return TOOLS[verb].handler({query: async () => ({rows: queryRows})}, {}, args);
  };
  const contacts = await invoke('email-match-contacts', {}, rows);
  const research = await invoke('prospect-research', {subject: party.ref}, [{...party, evidence: []}]);
  const emails = contacts.contacts.map(c => c.address).sort();
  const blocked = await invoke('mail-contact-policy', {emails}, [{email: party.email}]);
  const unblocked = await invoke('mail-contact-policy', {emails}, []);
  const fixtures = {'email-match-contacts': contacts, 'prospect-research': research, 'mail-contact-policy': blocked};
  let releaseReads = 0, drift = false;
  const server = createServer((request, response) => {
    assert.equal(request.url, '/release');
    releaseReads++;
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({git_sha: {value: (drift && releaseReads > 1 ? 'b' : 'a').repeat(40)}}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    await writeFile(wrapper, `#!/usr/bin/env python3
import json, pathlib, sys, time
assert sys.argv[1] == 'call'
data = json.loads(pathlib.Path(__file__).with_name('replies.json').read_text())
verb, args = sys.argv[2], json.loads(sys.argv[3])
assert verb in ('email-match-contacts', 'prospect-research', 'mail-contact-policy')
if data.get('timeout'): time.sleep(2)
print(json.dumps(data[verb]))
`);
    await chmod(wrapper, 0o700);
    for (const [mode, code] of [['pass', 0], ['contradiction', 1], ['unavailable', 78],
      ['timeout', 78], ['row-limit', 78], ['release-drift', 78], ['single-address', 78], ['wrong-party', 78], ['discovery-suffix', 78]]) {
      releaseReads = 0;
      drift = mode === 'release-drift';
      const replies = structuredClone(fixtures);
      if (mode === 'contradiction') replies['mail-contact-policy'] = unblocked;
      if (mode === 'unavailable') replies['prospect-research'].contact_state = 'active';
      if (mode === 'wrong-party') replies['prospect-research'].id = 'bbbbbbbb-0000-4000-8000-000000000001';
      if (mode === 'discovery-suffix') {
        replies['email-match-contacts'].contacts[0].address = 'b@example.test';
        replies['mail-contact-policy'] = await invoke('mail-contact-policy',
          {emails: ['b@example.test', 'second@example.test']}, [{email: party.email}]);
      }
      if (mode === 'timeout') replies.timeout = true;
      if (mode === 'row-limit') replies['email-match-contacts'].contacts = Array(1001).fill(contacts.contacts[0]);
      if (mode === 'single-address') replies['email-match-contacts'].contacts = [contacts.contacts[0]];
      await writeFile(path.join(directory, 'replies.json'), JSON.stringify(replies));
      const result = await new Promise((resolve, reject) => {
        const child = spawn('python3', ['tools/prove-mail-policy-multiple-recorded-addresses.py',
          '--origin', origin, '--call-wrapper', wrapper, '--timeout-seconds', mode === 'timeout' ? '0.1' : '30'], {cwd: root});
        let stdout = '', stderr = '';
        child.stdout.on('data', data => stdout += data);
        child.stderr.on('data', data => stderr += data);
        child.on('error', reject);
        child.on('close', status => resolve({status, stdout, stderr}));
      });
      assert.equal(result.status, code, `${mode}: ${result.stderr} ${result.stdout}`);
      const receipt = JSON.parse(result.stdout);
      if (mode === 'discovery-suffix') {
        assert.equal(receipt.reason, 'snapshot_address_evidence_unavailable');
        assert.equal(receipt.wrapper_calls, 1);
      }
      assert.ok(receipt.wrapper_calls <= 20);
      assert.ok(receipt.examined_rows <= 1000);
      assert.doesNotMatch(result.stdout, /example\.test|Synthetic|P-0001/i);
      if (code === 0) {
        assert.equal(receipt.witnesses, 1);
        assert.equal(receipt.addresses, 2);
        assert.equal(releaseReads, 2);
      }
    }
  } finally {
    await new Promise(resolve => server.close(resolve));
    await rm(directory, {recursive: true, force: true});
  }
});
