// P1 S4 proof (docs/memory/team-memory-design.md §9): the staged convention
// proposals, governance-queue's proposal lane, adopt-rule's refusals and one
// in-person adoption, against a disposable PostgreSQL built as deploy builds
// it: db/schema.sql, then every migration missing from its ledger.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { migrationNames, readMigration } from './migration-source.testhelper.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';

const REPO = new URL('../../', import.meta.url).pathname;
const MIGRATION = migrationNames().find(name => /^\d{4}_rule_card_adoption\.sql$/.test(name));
const PLAN = JSON.parse(execFileSync('python3', [`${REPO}ops/stage-convention-proposals.py`, '--print'],
  { encoding: 'utf8' }));

// The connector Claude session and the local machine door, both sponsored by
// Joe and both holding partner authority for ordinary humanOnly verbs.
const claudeForJoe = { id: '10000000-0000-0000-0000-000000000011', slug: 'claude', human: false,
  sponsoring_human_slug: 'joe', native_agent_verified: true, via: 'oauth-agent', client_id: 'claude-client' };
const joeLocal = { id: '10000000-0000-0000-0000-000000000010', slug: 'joe-local', human: false,
  sponsoring_human_slug: 'joe', native_agent_verified: true, via: 'local-token', client_id: 'local-verb' };

let fixture, c, joe, dell;
before(async () => {
  assert.ok(MIGRATION, 'the S4 migration exists');
  const bin = [process.env.CARR_LOCAL_PG_BIN_DIR, '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin',
    '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
  assert.ok(bin, 'PostgreSQL is required for the isolated org-conventions proof');
  fixture = await acquireDisposablePostgres({ prefix: 'org-conventions-', pgCtl: `${bin}/pg_ctl` });
  await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
  await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
  c = new pg.Client({ host: fixture.root, user: 'carr_ci', database: 'carr_ci' });
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', '-X', '-v', 'ON_ERROR_STOP=1', '-q',
    '-f', new URL('../../db/schema.sql', import.meta.url).pathname]);
  const applied = new Set((await c.query('select filename from schema_migrations')).rows.map(r => r.filename));
  const pending = migrationNames().filter(name => !applied.has(name)).sort();
  assert.ok(pending.includes(MIGRATION), 'the S4 migration is pending over the snapshot');
  for (const name of pending) await c.query(readMigration(name));
  await c.query(`do $roles$ begin
    if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
    if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then create role carr_authority_dell login; end if;
    grant carr_authority to carr_authority_joe; grant carr_authority to carr_authority_dell;
  end $roles$`);
  const partner = async slug => ({ ...(await c.query(`insert into actor(slug,kind,display_name) values($1,'human','Synthetic partner')
    on conflict(slug) do update set active=true returning id,slug`, [slug])).rows[0], human: true,
    via: 'dealroom-cookie', client_id: 'dealroom-pwa' });
  joe = await partner('joe');
  dell = await partner('dell');
  await seedDeliveredRule();
});

// The empty fixture has no rule packs and no active rules, which the
// production policy-integrity check refuses at commit. Seed one active,
// pack-delivered rule through the real approval chain, as production holds.
async function seedDeliveredRule() {
  await c.query('begin');
  await c.query(`insert into ops.rule_pack(pack,title,description,triggers,source)
    values('fixture-pack','Fixture pack','Synthetic pack for the S4 proof','{fixture-trigger}','org-conventions-postgres')`);
  const id = (await c.query(`insert into rule(statement,taught_by,status) values('Synthetic delivered rule.',$1,'proposed')
    returning id`, [joe.id])).rows[0].id;
  await c.query(`insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
    values($1::uuid,left($1::text,8),'pack','{fixture-pack}','shared',null,'org-conventions-postgres',repeat('0',64))`, [id]);
  await c.query('set local session authorization carr_authority_joe');
  await c.query(`select ops.approve_rule($1,'human_only',array['human_authority_runtime'],$2,'Synthetic fixture rule')`,
    [id, randomUUID()]);
  await c.query('commit');
}
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

// `as` is the database principal the deployed dispatcher would connect as:
// carr_writer for ordinary writes, the partner's authority login for adopt-rule.
async function call(actor, name, args, as = 'carr_writer') {
  await c.query('begin');
  try {
    await c.query(as === 'carr_writer' ? 'set local role carr_writer' : `set local session authorization ${as}`);
    const keyed = name === 'governance-queue' || name === 'rule-cards' ? args : { idempotency_key: randomUUID(), ...args };
    const result = await executeRegisteredTool(c, actor, name, keyed);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}

async function refused(promise, code) {
  await assert.rejects(promise, error => {
    assert.equal(error.payload?.error, code, error.message);
    assert.equal(publicError(error).error, code);
    return true;
  });
}

const ruleCount = async () => Number((await c.query('select count(*) from rule')).rows[0].count);
const adopt = (actor, proposal, as, extra = {}) => call(actor, 'adopt-rule', { section_id: proposal.section_id,
  base_version: proposal.base_version, reason: 'Synthetic in-person adoption', ...extra }, as);

let queue;
test('the staging plan writes every proposal through the team-memory gates, and no proposal touches rule', async () => {
  const before = await ruleCount();
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'claude_code' });
  for (const p of PLAN)
    await call(joe, 'write-doctrine-section', { document: 'team-memory', section_key: p.section_key,
      title: p.title, body_text: p.body_text, base_version: 0, attach_id });
  assert.equal(await ruleCount(), before, 'a proposal section cannot change rule');
  queue = (await call(joe, 'governance-queue', {})).pending_convention_proposals;
  assert.equal(queue.length, PLAN.length);
  assert.ok(queue.every(p => p.adoptable), 'every staged proposal is adoptable');
});

const proposal = key => queue.find(p => p.section_key === `conventions/proposed/${key}`);

test('adopt-rule refuses a claude-sponsored connector session and the local machine door', async () => {
  for (const actor of [claudeForJoe, joeLocal])
    await refused(adopt(actor, proposal('card-k01'), 'carr_authority_joe'), 'human_only_verb_requires_verified_partner');
  await refused(adopt({ ...joe, unattended: true }, proposal('card-k01'), 'carr_authority_joe'),
    'unattended_runner_authority_not_admitted');
});

test('adopt-rule refuses Joe\'s own claude.ai connector session: adoption is app-only', async () => {
  const connector = { ...joe, via: 'oauth-google', client_id: 'claude-ai-connector' };
  await refused(adopt(connector, proposal('card-k01'), 'carr_authority_joe'), 'rule_adoption_requires_app_approval');
  assert.equal((await c.query('select count(*) from rule where card is not null')).rows[0].count, '0');
});

test('the writer connection cannot reach ops.adopt_rule', async () => {
  await refused(adopt(joe, proposal('card-k01'), 'carr_writer'), 'database_refused_the_statement');
});

test('Joe adopts a core card in person: active rule, card on the rule, proposal retired', async () => {
  const k01 = proposal('card-k01');
  const out = await adopt(joe, k01, 'carr_authority_joe');
  assert.equal(out.card_id, 'K01');
  assert.equal(out.load_layer, 'control', 'adoption never grows the capped layer0 boot set');
  const rule = (await c.query('select * from rule where id=$1', [out.rule_id])).rows[0];
  assert.equal(rule.status, 'active');
  assert.equal(rule.activated_by, joe.id);
  assert.equal(rule.taught_by, joe.id);
  assert.equal(rule.card.id, 'K01');
  assert.equal(rule.card.placement, 'CORE');
  assert.equal(rule.card.wording, undefined);
  assert.equal(rule.statement, k01.statement);
  const section = (await c.query('select status, current_version from doctrine_section where id=$1', [k01.section_id])).rows[0];
  assert.deepEqual(section, { status: 'retired', current_version: String(k01.base_version + 1) });
  const session = (await c.query(`select m.surface from doctrine_change_set cs
    join memory_session m on m.attach_id::text = cs.session_key where cs.idempotency_key like 'adopt-rule:%'`)).rows;
  assert.deepEqual(session, [{ surface: 'app' }], 'the retire is attributed to the approval surface attach');
  const snap = await executeRegisteredTool(c, joe, 'read-doctrine', { document: 'team-memory' });
  assert.equal(snap.generation, out.generation);
  assert.ok(!snap.sections.some(s => s.section_id === k01.section_id), 'the snapshot drops the retired proposal');
  const after = (await call(joe, 'governance-queue', {})).pending_convention_proposals;
  assert.equal(after.length, queue.length - 1);
  const cards = await call(joe, 'rule-cards', {});
  assert.equal(cards.count, 1);
  assert.equal(cards.cards[0].id, 'K01');
  assert.equal(cards.cards[0].wording, k01.statement);
});

test('an adopted card cannot be adopted twice, and a stale base_version is refused', async () => {
  const k01 = proposal('card-k01');
  await refused(adopt(joe, k01, 'carr_authority_joe'), 'convention_proposal_not_found');
  const k02 = proposal('card-k02');
  await refused(adopt(joe, { ...k02, base_version: k02.base_version + 1 }, 'carr_authority_joe'), 'version_conflict');
});

test('an uncovered ORG convention adopts without a card on the control layer', async () => {
  const org = queue.find(p => !p.section_key.startsWith('conventions/proposed/card-'));
  const out = await adopt(joe, org, 'carr_authority_joe');
  assert.equal(out.card_id, null);
  assert.equal(out.load_layer, 'control');
  const rule = (await c.query('select status, card from rule where id=$1', [out.rule_id])).rows[0];
  assert.deepEqual(rule, { status: 'active', card: null });
});

test('a malformed proposal is refused and changes nothing', async () => {
  const { attach_id } = await call(joe, 'attach-memory', { surface: 'claude_code' });
  const written = await call(joe, 'write-doctrine-section', { document: 'team-memory',
    section_key: 'conventions/proposed/free-prose', body_text: 'Proposed convention: free prose', base_version: 0, attach_id });
  const before = await ruleCount();
  await refused(adopt(joe, { section_id: written.section_id, base_version: 1 }, 'carr_authority_joe'),
    'convention_proposal_malformed');
  assert.equal(await ruleCount(), before);
});

test('Dell\'s authority login is refused by the Joe-only approval chain (named gap)', async () => {
  await refused(adopt(dell, proposal('card-k02'), 'carr_authority_dell'), 'rule_adoption_requires_joe');
});
